"""Writing: posts, pages, the preview, the signature, pins and reactions.

Part of the terminal client, split from app.py by area (#20) - a pure move; app.py
re-exports every name, so `from unseenet_tui.app import ...` keeps working.
"""

from __future__ import annotations

# ruff: noqa: F401, E402 - the original module's imports, kept as they were
import argparse
import asyncio
import contextlib
import random
import inspect
import os
import re
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from textual import on, work
from textual.message import Message as Message_
from textual.worker import Worker, WorkerState
from textual.app import App, ComposeResult, ScreenStackError, SystemCommand
from textual.binding import Binding
from rich.text import Text
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.markup import escape
from textual.screen import ModalScreen, Screen
from textual.widgets import (Button, Checkbox, DataTable, Footer, Header, Input, Label,
                             OptionList, RichLog, Select, Static, TextArea, Tree)
from textual.widgets.option_list import Option
from unseenet import __version__
from unseenet.portable import portable_root
from unseenet.console import console_css
from unseenet.core.vault import Vault, VaultError
from unseenet.nntp import NNTPError
from unseenet.core import crypto
from unseenet.core import addresses as addr
from unseenet.core.groups import (INBOX_NAME, make_group_id, KIND_CHAT, KIND_POST, group_refs,
                           personal_inbox_id, short_group_id, split_group_id)
from unseenet.client import session as _session_module
from unseenet.client import naming
from unseenet.client import richtext
from unseenet.client import groupfilter
from unseenet.client.grouptree import fingerprint_suffix, group_tree
from unseenet.core import markup
from unseenet.core import reactions as rx
from unseenet.core import about
from unseenet.core import profiles as prof
from unseenet.core.blobs import FILE_CHOICES, file_choice
from unseenet import names as nm
from unseenet.client.session import MAX_MENTION_NOTICES
from unseenet.client.session import (MAX_SIG_LINES, SIG_SEPARATOR, GroupInfo, Message, Session, SessionError,
                      default_vault_path, split_signature)
from unseenet.client.threads import CONTACT, FOR_YOU, TIMELINE   # the session's pseudo groups (#139)
from unseenet.client.runtime import ClientRuntime
from unseenet.client import events as ev

from .base import Dialog


from ..emoji import EmojiTextArea  # noqa: E402 - #6


def language_options(first, group) -> list[tuple[str, str]]:
    """The choices of the language list: what is suggested and the group's first, then
    the languages a text can be told in, then the rest - each by its code and own name
    (the core's list, no flags) - and None."""
    from unseenet.core import languages as langs
    listed = langs.as_list()
    order = list(dict.fromkeys([*first, *group, *[x["code"] for x in listed if x["recognised"]],
                                *[x["code"] for x in listed]]))
    return [(f"{c.upper():<3} {langs.name(c)}", c) for c in order] + [("None - no language is sent", "")]


class ComposeDialog(Dialog):
    BINDINGS = [Binding("escape", "cancel", "Cancel"), Binding("ctrl+s", "send", "Send"),
                Binding("ctrl+e", "bigger", "Bigger"),        # room to write (#74, as gui#203)
                Binding("ctrl+b", "bold", "Bold"), Binding("ctrl+q", "quote", "Quote"),
                Binding("ctrl+r", "read_more", "Read more"),
                Binding("ctrl+w", "warnings", "Warning")]

    def __init__(self, group: GroupInfo, subject: str = "", quote: str = "", is_reply: bool = False,
                 heading: str | None = None, signature: str = "", has_charter: bool = False,
                 files_allowed: bool = False, reply_to=None):
        super().__init__()
        self.group, self.subject, self.quote, self.is_reply = group, subject, quote, is_reply
        self.reply_to = reply_to                 # the Message answered: its language first (#686)
        self._lang_touched = False               # the language field is yours once you type in it
        self._lang_setting = False
        self._lang_timer = None
        self.heading, self.signature, self.has_charter = heading, signature, has_charter
        self.files_allowed = files_allowed       # the group takes files (UN-RFC 0027)
        self.files: list[str] = []               # what to send with it, read after dismiss
        self.tags: list[str] = []                # the author's tags (UN-RFC 0052), after dismiss
        self.languages: list[str] | None = None  # what it is written in (0052 5a), after dismiss
        self._suggested: list[str] = []
        self.warnings: list[str] = []            # content warnings chosen (UN-RFC 0052/0053)

    def compose(self) -> ComposeResult:
        """The compose window: where it goes, the subject, the text with the quote and
        signature, files."""
        s = getattr(self.app, "session", None)
        big = bool(s and s.client_setting("tui", "compose_big", False))     # remembered (#74)
        with Vertical(classes="dialog compose" + (" big" if big else "")):
            kind = "Reply in" if self.is_reply else "New post in"
            lock = "  ∩ encrypted" if self.group.type == "closed" else ""
            yield Label(self.heading or f"{kind} {self.app.session.group_label(self.group.group_id)}{lock}", classes="title",
                        markup=False)
            yield Input(value=self.subject, placeholder="Subject", id="subject")
            text = self.quote + (("\n" if self.quote else "\n\n") + SIG_SEPARATOR + self.signature
                                 if self.signature else "")
            yield EmojiTextArea(text, id="body", soft_wrap=True)     # :ta completion (#6)
            yield Static("", id="body-emoji", markup=False)
            gid = self.group.group_id or ""
            # language, tags and warnings on one row (#74, as gui#203), each with its why
            with Horizontal(classes="metarow"):
                if self.group.type != "inbox" and gid and not gid.startswith(("contact:", "inbox@")):
                    codes, why = self.app.session.suggest_languages(gid, reply_to=self.reply_to)  # 0052 5a
                    self._suggested = codes
                    with Vertical(classes="langcol"):
                        yield Select(language_options(codes, self.app.session.manifest_data(gid).get("languages") or []),
                                     value=codes[0] if codes else "", allow_blank=False, id="lang",
                                     prompt="Language")
                        yield Label("Language: " + {"you": "what you wrote in here last", "group": "the group's",
                                                    "profile": "from your profile"}.get(why, "none is sent"),
                                    classes="hint", markup=False, id="lang-why")
                if self.group.type != "inbox":              # tags; warnings behind one choice
                    yield Input(placeholder="Tags - add a tag, or write #word in the text", id="tags")
                    yield Button("⚠ Content warning", id="warn-btn")
            if self.group.type != "inbox":                  # the warnings, when asked for (Ctrl+W)
                with Horizontal(id="warn-list", classes="warnlist"):
                    for w in WARNING_ORDER:
                        yield Checkbox(WARNING_TEXT[w], id="w-" + w)
            if self.files_allowed:
                yield Input(placeholder="Files to send with it - paths, comma between: "
                            "~/garden.jpg, ~/map.pdf", id="files")
                yield Label("Pictures are cleaned of their metadata - place, camera, time - "
                            "before they are sent.", classes="hint")
            if self._notices():
                yield Checkbox("Tell the people you mention (a notice in their inbox)",
                               value=self.app.session.notice_settings()["send"], id="notify")
                yield Label("", id="notify-who", classes="hint", markup=False)
            yield Label((("Cut the quote down to what you answer.  " if self.quote.lstrip().startswith(">")
                          or "\n>" in self.quote else "")
                         + ("This group has a charter: Esc, then ?.  " if self.has_charter else "")
                         + "Signed with your key · proof-of-work added.  Ctrl+B bold · Ctrl+Q quote · "
                         "Ctrl+R read more · Ctrl+W warning · Ctrl+E bigger"), classes="hint", id="foot")
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Send", variant="primary", id="ok")

    def _notices(self) -> bool:
        """Mention notices go with posts in groups, not with private posts or closed groups."""
        s = getattr(self.app, "session", None)
        return bool(s) and self.group.type != "inbox" and s.may_notify(self.group.group_id)

    def _mentioned(self) -> list[tuple[str, str]]:
        return self.app.session.mentioned(self.query_one("#subject", Input).value,
                                          self.query_one("#body", TextArea).text)

    @on(Select.Changed, "#lang")
    def _lang_by_hand(self, event: Select.Changed) -> None:
        # yours only when it differs from what was put there (its first value included)
        auto = getattr(self, "_lang_auto", self._suggested[0] if self._suggested else "")
        if not self._lang_setting and event.value != auto:
            self._lang_touched = True

    @on(TextArea.Changed)
    @on(Input.Changed, "#subject")
    def _ask_language(self) -> None:
        """At a pause in writing, what the text is in (unseenet#686) - into the field,
        unless you typed in it yourself."""
        if not self.query("#lang") or self._lang_touched:
            return
        if self._lang_timer is not None:
            self._lang_timer.stop()
        self._lang_timer = self.set_timer(0.6, self._suggest_language)

    def _suggest_language(self) -> None:
        if self._lang_touched or not self.query("#lang"):
            return
        text = self.query_one("#subject", Input).value + "\n" + self.query_one("#body", TextArea).text
        codes, why = self.app.session.suggest_languages(self.group.group_id, text=text,
                                                        reply_to=self.reply_to)
        sel = self.query_one("#lang", Select)
        want = codes[0] if codes else ""
        if want and want not in {v for _, v in sel._options}:     # a language not listed yet
            sel.set_options(language_options([want] + list(self._suggested), []))
        self._lang_setting = True
        self._lang_auto = want
        sel.value = want
        self._lang_setting = False
        hint = self.query("#lang-why")
        if hint:
            hint.first().update("Language: " + {"text": "from your text", "answered": "the post you answer",
                                                "you": "what you wrote in here last", "group": "the group's",
                                                "profile": "from your profile"}.get(why, "none is sent"))

    @on(TextArea.Changed)
    @on(Input.Changed, "#subject")
    @on(Checkbox.Changed, "#notify")
    def _show_notified(self) -> None:
        if not self._notices():
            return
        found = self._mentioned()
        who = self.query_one("#notify-who", Label)
        on = self.query_one("#notify", Checkbox).value
        if not found:
            who.update("")
        elif not on:
            who.update("Nobody is told: " + ", ".join(n for n, _ in found) + " see it only if "
                       "they follow this group.")
        else:
            told = found[:MAX_MENTION_NOTICES]
            who.update("Told: " + ", ".join(n for n, _ in told)
                       + (f"  (at most {MAX_MENTION_NOTICES}; not told: "
                          + ", ".join(n for n, _ in found[MAX_MENTION_NOTICES:]) + ")"
                          if len(found) > MAX_MENTION_NOTICES else ""))

    def on_mount(self) -> None:
        """Put the cursor below the quote and above the signature."""
        body = self.query_one("#body", TextArea)
        body.emoji_bar = self.query_one("#body-emoji", Static)
        body.emoji_bar.display = False
        # the cursor goes below the quote, above the signature
        body.move_cursor((self.quote.count("\n"), 0) if self.signature else body.document.end)
        if self.is_reply:
            body.focus()
        else:
            self.query_one("#subject").focus()

    def action_bigger(self) -> None:
        """Ctrl+E: the text gets nearly all the room - or back (#74); remembered."""
        win = self.query_one(".dialog.compose")
        big = not win.has_class("big")
        win.set_class(big, "big")
        s = getattr(self.app, "session", None)
        if s is not None:
            s.set_client_setting("tui", "compose_big", big)
        self.query_one("#body", TextArea).focus()

    def _selected_lines(self, body: TextArea) -> tuple[int, int]:
        (r1, _), (r2, _) = sorted((body.selection.start, body.selection.end))
        return r1, r2

    def action_bold(self) -> None:
        """Ctrl+B: ** around the selection (or around "text" to write over)."""
        body = self.query_one("#body", TextArea)
        start, end = sorted((body.selection.start, body.selection.end))
        chosen = body.selected_text or "text"
        body.replace("**" + chosen + "**", start, end)
        if not body.selected_text and start == end:
            body.selection = type(body.selection)((start[0], start[1] + 2), (start[0], start[1] + 6))
        body.focus()

    def action_quote(self) -> None:
        """Ctrl+Q: "> " at the start of the selected lines (or the cursor's line)."""
        body = self.query_one("#body", TextArea)
        r1, r2 = self._selected_lines(body)
        lines = body.text.split("\n")
        for r in range(r1, min(r2, len(lines) - 1) + 1):
            body.insert("> ", (r, 0))
        body.focus()

    def action_warnings(self) -> None:
        """Ctrl+W: show or hide the content warnings to choose from."""
        if not self.query("#warn-list"):
            return
        lst = self.query_one("#warn-list")
        lst.set_class(not lst.has_class("open"), "open")
        if lst.has_class("open"):
            self.query_one("#w-spoiler", Checkbox).focus()

    @on(Button.Pressed, "#warn-btn")
    def _warn_btn(self) -> None:
        self.action_warnings()

    @on(Checkbox.Changed, ".warnlist Checkbox")
    def _warn_changed(self) -> None:
        """Only the chosen warnings show, on the button (as the GUI's)."""
        self.warnings = [w for w in WARNING_ORDER if self.query_one("#w-" + w, Checkbox).value]
        self.query_one("#warn-btn", Button).label = (
            "⚠ " + ", ".join(WARNING_TEXT[w] for w in self.warnings) if self.warnings else "⚠ Content warning")

    def action_read_more(self) -> None:
        """Ctrl+R: a read-more line at the cursor (UN-RFC 0031 3.2.1) - a feed cuts the
        post there for "... more"; nobody needs to know the syntax."""
        body = self.query_one("#body", TextArea)
        row, col = body.cursor_location
        body.text, cursor = with_read_more(body.text, row, col)
        body.move_cursor(cursor)
        body.focus()

    @on(Button.Pressed, "#ok")
    def action_send(self) -> None:
        """Ctrl+S: check and send it, or say what is missing."""
        subject = self.query_one("#subject", Input).value.strip()
        body = self.query_one("#body", TextArea).text
        if not subject or not split_signature(body)[0].strip():
            self.app.notify("Subject and text are required", severity="error")
            return
        keys: list[str] = []
        if self._notices() and self.query_one("#notify", Checkbox).value:
            keys = [k for _, k in self._mentioned()][:MAX_MENTION_NOTICES]
        if self.files_allowed:
            self.files = [p.strip() for p in self.query_one("#files", Input).value.split(",")
                          if p.strip()]
        if self.query("#lang"):                           # chosen; cleared: [] forgets it here
            value = self.query_one("#lang", Select).value
            chosen = [value] if isinstance(value, str) and value else []
            self.languages = chosen if (chosen or self._suggested) else None
        if self.query("#tags"):                           # the core normalises them, adds #words
            typed = [t for t in self.query_one("#tags", Input).value.replace(",", " ").split() if t]
            self.tags = list(self.warnings) + [t for t in typed if t not in self.warnings]
        self.dismiss((subject, body, keys))


# content warnings (UN-RFC 0052/0053), spoiler first - the one people use most (#74)
WARNING_ORDER = ("spoiler", "profanity", "sexual", "nudity", "violence", "gore")
WARNING_TEXT = {"spoiler": "Spoiler", "profanity": "Profanity", "sexual": "Sexual content",
                "nudity": "Nudity", "violence": "Violence", "gore": "Gore"}


def with_read_more(text: str, row: int, col: int) -> tuple[str, tuple[int, int]]:
    """``text`` with a read-more line (UN-RFC 0031 3.2.1) put in at (row, col), on a line
    of its own with blank lines around it, and where the cursor goes after it."""
    lines = text.split("\n")
    line = lines[row] if row < len(lines) else ""
    head = "\n".join(lines[:row] + [line[:col].rstrip(" ")]).rstrip("\n")
    tail = "\n".join([line[col:].lstrip(" ")] + lines[row + 1:]).lstrip("\n")
    out = (head + "\n\n" if head else "") + "...\n\n"
    cursor = (out.count("\n"), 0)
    return out + tail, cursor


class PageDialog(Dialog):
    """Write a page of a site (UN-RFC 0035 section 9.2): its path (a new page), title and
    Markdown, pictures as on posts, and a preview as readers will see it. Dismissed with
    ``(path, title, text)``; ``files`` is read after."""
    BINDINGS = [Binding("escape", "cancel", "Cancel"), Binding("ctrl+s", "send", "Publish"),
                Binding("ctrl+o", "preview", "Preview")]

    def __init__(self, heading: str, gid: str, path: str = "", title: str = "", text: str = "",
                 files_allowed: bool = False):
        super().__init__()
        self.heading, self.gid, self.path, self.title_, self.text = heading, gid, path, title, text
        self.new = not path                      # a new page chooses its path
        self.files_allowed = files_allowed
        self.files: list[str] = []

    def compose(self) -> ComposeResult:
        """Writing a page of a site: its path, title and Markdown (UN-RFC 0035)."""
        with Vertical(classes="dialog compose"):
            yield Label(self.heading, classes="title", markup=False)
            yield Input(value=self.path, placeholder="Path - e.g. index, about or projects/ovos",
                        id="path", disabled=not self.new)
            yield Input(value=self.title_, placeholder="Title (the path if left empty)", id="title")
            yield TextArea(self.text, id="body", soft_wrap=True)
            if self.files_allowed:
                yield Input(placeholder="Pictures for it - paths, comma between: ~/garden.jpg",
                            id="files")
            yield Label("Markdown. A link without a scheme is a page of this site: [About](about). "
                        "Ctrl+O previews, Ctrl+S publishes - with a small proof-of-work.",
                        classes="hint")
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Preview", id="preview")
                yield Button("Publish", variant="primary", id="ok")

    def on_mount(self) -> None:
        """The focus in the path for a new page, else in the text."""
        self.query_one("#path" if self.new else "#body").focus()

    def _path(self) -> str:
        return self.query_one("#path", Input).value.strip().strip("/")

    @on(Button.Pressed, "#preview")
    def action_preview(self) -> None:
        """Ctrl+P: the page as readers will see it (UN-RFC 0035 9.2)."""
        from unseenet.core import pages as pg
        path = self._path() or "index"

        def resolve(href: str) -> str | None:
            target = pg.resolve_link(path, href) if pg.valid_path(path) else None
            return addr.page_address(self.gid, target) if target else None
        text = self.query_one("#body", TextArea).text[:pg.MAX_BODY]
        title = self.query_one("#title", Input).value.strip() or path
        self.app.push_screen(PreviewDialog(
            f"Preview - {title}   /{path}",
            richtext.blocks(markup.page_body(text, "text/markdown", resolve))))

    @on(Button.Pressed, "#ok")
    def action_send(self) -> None:
        """Ctrl+S: check the path and size, then publish the page."""
        from unseenet.core import pages as pg
        path, text = self._path(), self.query_one("#body", TextArea).text
        if not pg.valid_path(path):
            self.app.notify("A path is 1-8 parts of a-z 0-9 - _ . separated by /",
                            severity="error")
            return
        if not text.strip():
            self.app.notify("A page needs some text", severity="error")
            return
        if len(text.encode()) > pg.MAX_BODY:
            self.app.notify(f"A page is at most {pg.MAX_BODY // 1024} KiB", severity="error")
            return
        if self.files_allowed:
            self.files = [p.strip() for p in self.query_one("#files", Input).value.split(",")
                          if p.strip()]
        self.dismiss((path, self.query_one("#title", Input).value.strip()[:pg.MAX_TITLE], text))


class PreviewDialog(Dialog):
    """A page as readers will see it (UN-RFC 0035 section 9.2)."""
    BINDINGS = [Binding("escape", "cancel", "Back")]

    def __init__(self, title: str, rendered: Text):
        super().__init__()
        self.title_, self.rendered = title, rendered

    def compose(self) -> ComposeResult:
        """The preview of a page: its rendered text in a scroll, and Close."""
        with Vertical(classes="dialog compose"):
            yield Label(self.title_, classes="title", markup=False)
            with VerticalScroll(id="preview-view"):
                yield Static(self.rendered, id="preview-text")
            with Horizontal(classes="buttons"):
                yield Button("Back", variant="primary", id="cancel")


class SignatureDialog(Dialog):
    BINDINGS = [Binding("escape", "cancel", "Cancel"), Binding("ctrl+s", "save", "Save")]

    def __init__(self, template: str):
        super().__init__()
        self.template = template or "{name}  ·  {key}"

    def compose(self) -> ComposeResult:
        """Your signature: the template, what it will be, and Save."""
        with Vertical(classes="dialog"):
            yield Label("Your signature", classes="title")
            yield Label(f"Added below '-- ' at the end of your posts. At most {MAX_SIG_LINES} lines. "
                        "{name}, {key} and {fp} are filled in. Leave empty for none.",
                        classes="hint", markup=False)
            yield TextArea(self.template, id="sig", soft_wrap=True)
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Save", variant="primary", id="ok")

    def on_mount(self) -> None:
        """Size the field to the most lines a signature may have."""
        self.query_one("#sig", TextArea).styles.height = MAX_SIG_LINES + 2
        self.query_one("#sig").focus()

    @on(Button.Pressed, "#ok")
    def action_save(self) -> None:
        """Ctrl+S: save the signature, if it is not too long."""
        text = self.query_one("#sig", TextArea).text
        if len([ln for ln in text.strip("\n").split("\n") if text.strip()]) > MAX_SIG_LINES:
            self.app.notify(f"At most {MAX_SIG_LINES} lines, please - that was the rule on Usenet too",
                            severity="warning")
            return
        self.dismiss(text)


class PinDialog(Dialog):
    """Pin a post: an optional note, and what happens to the oldest pin when the
    group is full (UN-RFC 0033 section 7.1). Dismisses with the note, "" for none."""

    def __init__(self, title: str, hint: str, warning: str = ""):
        super().__init__()
        self.title_, self.hint, self.warning = title, hint, warning

    def compose(self) -> ComposeResult:
        """Pinning a post: a note to show with it, and Pin (UN-RFC 0033)."""
        with Vertical(classes="dialog"):
            yield Label(self.title_, classes="title", markup=False)
            yield Label(self.hint, classes="hint", markup=False)
            if self.warning:
                yield Static(self.warning, markup=False, classes="hint")
            yield Input(placeholder="A note shown with it (optional)", id="value", max_length=256)
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Pin", variant="primary", id="ok")

    @on(Input.Submitted)
    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        self.dismiss(self.query_one("#value", Input).value.strip())


class ReactDialog(Dialog):
    """React to a post, comment or chat line (UN-RFC 0048 section 7.3): the group's
    set as a short numbered list - a number key chooses -, and where the group takes
    any emoji, a field for another. Your own reaction is marked; choosing it again
    takes it back. Dismisses with the emoji, or None."""

    def __init__(self, what: str, offered: tuple, mine: str | None, any_: bool,
                 first_time: bool = False):
        super().__init__()
        self.what, self.offered, self.mine, self.any_ = what, offered, mine, any_
        self.first_time = first_time

    def compose(self) -> ComposeResult:
        """Reacting: the group's emoji with a number each, or any emoji typed; yours marked."""
        with Vertical(classes="dialog"):
            yield Label("React to " + self.what, classes="title", markup=False)
            lines = []
            for i, e in enumerate(self.offered[:9], 1):
                own = rx.same_emoji(e, self.mine or "")
                lines.append(f"  {i}  {e}" + ("   (yours - again takes it back)" if own else ""))
            yield Static("\n".join(lines) or "  (none)", markup=False)
            if self.any_:
                yield Input(placeholder="Or another emoji (or :shortcode:)", id="value")
            hint = "A number chooses. Esc closes."
            if self.first_time:
                hint += ("\nReactions are public and signed: anybody can see which key "
                         "reacted to what.")
            yield Label(hint, classes="hint", markup=False)
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")

    def on_key(self, event) -> None:
        """A digit picks that emoji at once."""
        if event.character and event.character.isdigit() and event.character != "0":
            i = int(event.character)
            if i <= len(self.offered):
                event.stop()
                self.dismiss(self.offered[i - 1])

    @on(Input.Submitted)
    def _typed(self) -> None:
        value = self.query_one("#value", Input).value.strip()
        if value.isdigit() and 0 < int(value) <= len(self.offered):   # a number in the field
            self.dismiss(self.offered[int(value) - 1])
            return
        if value.startswith(":") and value.endswith(":"):        # UN-RFC 0031 section 5.2
            value = markup.emoji(value.strip(":")) or value
        if not rx.is_emoji(value):
            self.app.notify("One emoji, please", severity="warning")
            return
        self.dismiss(value)


def reactions_text(counts, mine: str | None) -> str:
    """The reactions under a post as a terminal shows them (UN-RFC 0048 section 7.2):
    each emoji and its count, your own in brackets - "👍 3  [❤️ 1]"."""
    if counts is None or not counts.by_emoji:
        return ""
    return "  ".join((f"[{e} {len(k)}]" if rx.same_emoji(e, mine or "") else f"{e} {len(k)}")
                     for e, k in counts.by_emoji.items())
