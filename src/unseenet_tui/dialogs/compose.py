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


class ComposeDialog(Dialog):
    BINDINGS = [Binding("escape", "cancel", "Cancel"), Binding("ctrl+s", "send", "Send")]

    def __init__(self, group: GroupInfo, subject: str = "", quote: str = "", is_reply: bool = False,
                 heading: str | None = None, signature: str = "", has_rules: bool = False,
                 files_allowed: bool = False):
        super().__init__()
        self.group, self.subject, self.quote, self.is_reply = group, subject, quote, is_reply
        self.heading, self.signature, self.has_rules = heading, signature, has_rules
        self.files_allowed = files_allowed       # the group takes files (UN-RFC 0027)
        self.files: list[str] = []               # what to send with it, read after dismiss
        self.tags: list[str] = []                # the author's tags (UN-RFC 0052), after dismiss
        self.languages: list[str] | None = None  # what it is written in (0052 5a), after dismiss
        self._suggested: list[str] = []

    def compose(self) -> ComposeResult:
        """The compose window: where it goes, the subject, the text with the quote and
        signature, files."""
        with Vertical(classes="dialog compose"):
            kind = "Reply in" if self.is_reply else "New post in"
            lock = "  ∩ encrypted" if self.group.type == "closed" else ""
            yield Label(self.heading or f"{kind} {self.app.session.group_label(self.group.group_id)}{lock}", classes="title",
                        markup=False)
            gid = self.group.group_id or ""
            if self.group.type != "inbox" and gid and not gid.startswith(("contact:", "inbox@")):
                codes, why = self.app.session.suggest_languages(gid)        # UN-RFC 0052 5a
                self._suggested = codes
                yield Input(value=", ".join(codes), id="lang",
                            placeholder="Language: none - e.g. da or en (remembered for this group)")
                yield Label("Language: " + {"you": "what you wrote in here last", "group": "the group's",
                                            "profile": "from your profile"}.get(why, "none is sent"),
                            classes="hint", markup=False)
            yield Input(value=self.subject, placeholder="Subject", id="subject")
            text = self.quote + (("\n" if self.quote else "\n\n") + SIG_SEPARATOR + self.signature
                                 if self.signature else "")
            yield EmojiTextArea(text, id="body", soft_wrap=True)     # :ta completion (#6)
            yield Static("", id="body-emoji", markup=False)
            if self.files_allowed:
                yield Input(placeholder="Files to send with it - paths, comma between: "
                            "~/garden.jpg, ~/map.pdf", id="files")
                yield Label("Pictures are cleaned of their metadata - place, camera, time - "
                            "before they are sent.", classes="hint")
            if self.group.type != "inbox":                  # tags, warnings first (UN-RFC 0052)
                yield Input(placeholder="Tags - spoiler, profanity, sexual, nudity, violence, gore, or "
                            "your own words; #words in the text count too", id="tags")
            if self._notices():
                yield Checkbox("Tell the people you mention (a notice in their inbox)",
                               value=self.app.session.notice_settings()["send"], id="notify")
                yield Label("", id="notify-who", classes="hint", markup=False)
            yield Label(("This group has rules - press Esc and ? to read them.  " if self.has_rules
                         else "") + "Ctrl+S to send. Sending takes a moment: the post carries a "
                        "small proof-of-work.", classes="hint")
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
            from unseenet.core.tags import normalize_languages
            chosen = normalize_languages(self.query_one("#lang", Input).value.replace(",", " ").split())
            self.languages = chosen if (chosen or self._suggested) else None
        if self.query("#tags"):                           # the core normalises them, adds #words
            self.tags = [t for t in self.query_one("#tags", Input).value.replace(",", " ").split() if t]
        self.dismiss((subject, body, keys))


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
