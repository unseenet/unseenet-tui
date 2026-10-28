"""People: contacts, profiles (UN-RFC 0022) and names (UN-RFC 0019).

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


class ContactsDialog(Dialog):
    """Your address book (c): everyone you have named, and what to do with them -
    chat, write a post, rename, remove (UN-RFC 0028: both kinds, one place).

    Type to find someone (#523): the filter looks at the name you gave them, their
    key and the names they hold. Up and Down move in the list, Enter chats, Esc clears
    the filter first and closes on the second press."""

    BINDINGS = [Binding("escape", "cancel", "Close"),
                Binding("down", "move(1)", show=False, priority=True),
                Binding("up", "move(-1)", show=False, priority=True)]

    def __init__(self, contacts: list[tuple[str, str]], find=None):
        super().__init__()
        self.contacts = contacts
        self.find = find or (lambda q: [kv for kv in contacts if q.lower() in kv[1].lower()])
        self.shown = list(contacts)

    def compose(self) -> ComposeResult:
        """Your contacts: the list, and chat, rename, remove, add."""
        with Vertical(classes="dialog"):
            yield Label("Contacts", classes="title")
            if not self.contacts:
                yield Static("No contacts yet. Press a on a post or a message to add its author "
                             "under a name of your own.", markup=False)
            else:
                yield Input(placeholder="Find: a name, a key or sonia@lorensen", id="find")
                yield Label(self._count(), classes="hint", id="count", markup=False)
                yield OptionList(*self._options(), id="who")
            with Horizontal(classes="buttons"):
                yield Button("Close", id="cancel")
                if self.contacts:
                    yield Button("Remove", variant="error", id="remove")
                    yield Button("Rename", id="rename")
                    yield Button("Write a post", id="post")
                    yield Button("Chat", variant="primary", id="chat")

    def on_mount(self) -> None:
        """The focus on the first contact, or on Add."""
        if self.contacts:
            self.query_one("#find", Input).focus()
            self.query_one("#who", OptionList).highlighted = 0

    def _count(self) -> str:
        n, total = len(self.shown), len(self.contacts)
        if n == total:
            return (f"{total} people, under the names you gave them. "
                    "Type to find someone, Enter to chat.")
        return f"{n} of {total} contacts" if n else "No contacts match"

    def _options(self) -> list:
        return [Option(name, id=key) for key, name in self.shown]

    @on(Input.Changed, "#find")
    def _filter(self, event: Input.Changed) -> None:
        self.shown = self.find(event.value) if event.value.strip() else list(self.contacts)
        who = self.query_one("#who", OptionList)
        who.clear_options()
        who.add_options(self._options())
        who.highlighted = 0 if self.shown else None
        self.query_one("#count", Label).update(self._count())

    def action_move(self, step: int) -> None:
        """Up and down the list of contacts."""
        if not self.contacts:
            return
        who = self.query_one("#who", OptionList)
        if not self.shown:
            return
        at = who.highlighted if who.highlighted is not None else -step
        who.highlighted = max(0, min(len(self.shown) - 1, at + step))

    def action_cancel(self) -> None:
        """Esc: close, or cancel a removal being confirmed."""
        if self.contacts:
            find = self.query_one("#find", Input)
            if find.value:
                find.value = ""
                return
        self.dismiss(self.CANCEL_RESULT)

    def _chosen(self) -> str | None:
        who = self.query_one("#who", OptionList)
        if who.highlighted is None or not self.shown:
            return None
        return self.shown[who.highlighted][0]

    @on(Input.Submitted, "#find")
    def _enter(self) -> None:
        if (key := self._chosen()):
            self.dismiss(("chat", key))

    @on(OptionList.OptionSelected, "#who")
    def _picked(self, event: OptionList.OptionSelected) -> None:
        if event.option.id:
            self.dismiss(("chat", event.option.id))

    @on(Button.Pressed, "#chat, #post, #rename, #remove")
    def _act(self, event: Button.Pressed) -> None:
        if (key := self._chosen()):
            self.dismiss((event.button.id, key))


class ChatsDialog(Dialog):
    """Private chats (#39): every chat, one per person - only you and the other person
    can read them - unread first, then the closed ones, which come back when either of
    you writes (UN-RFC 0044 ``closed_chats``). Type to find someone; Enter opens the
    chat; + starts a new one; Contacts opens the address book."""

    ABOUT = ("Private chats, one per person - only you and the other person can read them. "
             "Add people as contacts, or start a chat with +.")
    BINDINGS = [Binding("escape", "cancel", "Close"),
                Binding("plus", "new", "New chat", show=False),
                Binding("down", "move(1)", show=False, priority=True),
                Binding("up", "move(-1)", show=False, priority=True)]

    def __init__(self, chats: list[tuple[str, str, int, bool]]):
        """``chats``: (key, label, unread, closed) for every person."""
        super().__init__()
        self.chats = chats
        self.shown = list(chats)

    def compose(self) -> ComposeResult:
        """The list, a filter, and Contacts, New chat and Chat."""
        with Vertical(classes="dialog"):
            yield Label("Private chats", classes="title")
            yield Static(self.ABOUT, classes="hint", markup=False)
            if self.chats:
                yield Input(placeholder="Find: a name or a key", id="find")
                yield Label(self._count(), classes="hint", id="count", markup=False)
                yield OptionList(*self._options(), id="who")
            with Horizontal(classes="buttons"):
                yield Button("Close", id="cancel")
                yield Button("Contacts", id="contacts")
                yield Button("+ New chat", id="new")
                if self.chats:
                    yield Button("Chat", variant="primary", id="chat")

    def on_mount(self) -> None:
        """The focus in the filter, on the first chat."""
        if self.chats:
            self.query_one("#find", Input).focus()
            self.query_one("#who", OptionList).highlighted = 0

    def _count(self) -> str:
        n, total = len(self.shown), len(self.chats)
        if n == total:
            return f"{total} chat{'s' if total != 1 else ''}. Type to find someone, Enter to chat."
        return f"{n} of {total} chats" if n else "No chat matches"

    def _options(self) -> list:
        return [Option(f"{label}" + (f"  ({unread})" if unread else "")
                       + ("  - closed, back when one of you writes" if closed else ""), id=key)
                for key, label, unread, closed in self.shown]

    @on(Input.Changed, "#find")
    def _filter(self, event: Input.Changed) -> None:
        q = event.value.strip().lower()
        self.shown = [c for c in self.chats if not q or q in c[1].lower() or q in c[0].lower()]
        who = self.query_one("#who", OptionList)
        who.clear_options()
        who.add_options(self._options())
        who.highlighted = 0 if self.shown else None
        self.query_one("#count", Label).update(self._count())

    def action_move(self, step: int) -> None:
        """Up and down the list."""
        if not self.shown:
            return
        who = self.query_one("#who", OptionList)
        at = who.highlighted if who.highlighted is not None else -step
        who.highlighted = max(0, min(len(self.shown) - 1, at + step))

    def action_new(self) -> None:
        """+: a chat with someone new."""
        self.dismiss(("new", None))

    def action_cancel(self) -> None:
        """Esc: clear the filter, then close."""
        if self.chats:
            find = self.query_one("#find", Input)
            if find.value:
                find.value = ""
                return
        self.dismiss(self.CANCEL_RESULT)

    def _chosen(self) -> str | None:
        who = self.query_one("#who", OptionList)
        if who.highlighted is None or not self.shown:
            return None
        return self.shown[who.highlighted][0]

    @on(Input.Submitted, "#find")
    def _enter(self) -> None:
        if (key := self._chosen()):
            self.dismiss(("chat", key))

    @on(OptionList.OptionSelected, "#who")
    def _picked(self, event: OptionList.OptionSelected) -> None:
        if event.option.id:
            self.dismiss(("chat", event.option.id))

    @on(Button.Pressed, "#chat")
    def _chat(self) -> None:
        if (key := self._chosen()):
            self.dismiss(("chat", key))

    @on(Button.Pressed, "#contacts, #new")
    def _other(self, event: Button.Pressed) -> None:
        self.dismiss((event.button.id, None))


class NamesDialog(Dialog):
    """Your names (UN-RFC 0019): those you hold, those you asked for, and a field to claim one."""

    def __init__(self, text: str):
        super().__init__()
        self.text = text

    def compose(self) -> ComposeResult:
        """Names (UN-RFC 0019): the name to look up, give or take back."""
        with Vertical(classes="dialog"):
            yield Label("Your names", classes="title")
            yield Label("A name lets others see @yourname instead of Name~abcd. A name "
                        "without a dot (lorensen) is first come, first served; a domain "
                        "(lorensen.dk) needs a DNS record that shows it is yours.", classes="hint")
            yield Static(self.text, id="names-details", classes="details", markup=False)
            yield Input(placeholder="lorensen  or  lorensen.dk", id="value")
            with Horizontal(classes="buttons"):
                yield Button("Close", id="cancel")
                yield Button("Release", id="release")
                yield Button("Transfer", id="transfer")
                yield Button("Renew", id="renew")
                yield Button("Claim", variant="primary", id="claim")

    def on_mount(self) -> None:
        """The focus in the name."""
        self.query_one("#value", Input).focus()

    @on(Input.Submitted)
    @on(Button.Pressed, "#claim")
    @on(Button.Pressed, "#renew")
    @on(Button.Pressed, "#transfer")
    @on(Button.Pressed, "#release")
    def _act(self, event) -> None:
        name = self.query_one("#value", Input).value.strip().lower().lstrip("@")
        button = getattr(event, "button", None)
        action = button.id if button is not None else "claim"
        if name:
            self.dismiss((action, name))
        else:
            self.notify("Type the name first", severity="warning")


PROFILE_WARNING = prof.PROFILE_WARNING        # the same in every client (unseenet#320)


def identicon_text(key_text: str):
    """The key's identicon (UN-RFC 0022 section 8.4) as coloured block characters."""
    from rich.text import Text
    icon = prof.identicon(crypto.key_from_text(key_text))
    text = Text()
    for row in icon.cells:
        for cell in row:
            text.append("██" if cell else "  ", style=icon.hex() if cell else "")
        text.append("\n")
    return text


MARKDOWN_HINT = "Markdown - # headings, - lists, **strong**, [links](https://...), :tada: (no pictures)"


def manifest_text(text: str, text_type: str | None) -> Text:
    """A profile's about or a group's rules, plain or Markdown (UN-RFC 0022 4.1, 0014 2.1)."""
    return richtext.blocks(markup.manifest_text(profiles_clean(text), text_type))


def profiles_clean(text: str) -> str:
    """A profile's text cleaned as the core does (UN-RFC 0022): no control characters."""
    return prof.clean_text(text or "")


def writes_markdown(app, text_type: str | None, text: str) -> bool:
    """Start an editor in Markdown if the text already is, or, for new text, if posts are."""
    if (text or "").strip():
        return markup.is_markdown(text_type)
    s = getattr(app, "session", None)
    return s is None or s.text_type() == markup.MARKDOWN_TYPE


class ProfileDialog(Dialog):
    """Someone's profile (UN-RFC 0022 section 9.2): names, short form, about, lang, links."""

    def __init__(self, key_text: str, label: str, profile, mine: bool = False,
                 hidden: bool = False, shared: str | None = None,
                 feed_started: bool = False, following: bool = False,
                 site_started: bool = False, theirs: dict | None = None,
                 contact: str | None = None):
        super().__init__()
        self.key_text, self.label, self.profile = key_text, label, profile
        self.mine, self.hidden = mine, hidden
        # the feed and follow buttons (#495): your own feed, or following them
        self.feed_started, self.following = feed_started, following
        self.site_started = site_started
        # someone else's feed and site, when they have them: looked at without following
        self.theirs = theirs or {}
        self.contact = contact           # the name you gave them, if a contact
        self.shared = shared             # "with you" / "with this group": private (0022, 6)

    def compose(self) -> ComposeResult:
        """Someone's profile: name, picture or identicon, about, links, their feed and site,
        and what you may do."""
        p = self.profile
        with Vertical(classes="dialog"):
            title = self.label + ("   [bot]" if p is not None and p.bot else "")
            yield Label(title, classes="title", markup=False)
            yield Static(identicon_text(self.key_text))
            yield Label(self.key_text, classes="hint", markup=False)
            if self.shared:
                yield Label(f"Shared {self.shared} - not public, and shown only here",
                            classes="hint", markup=False)
            if p is None or prof.is_empty(p.raw or {}):
                yield Static("No public profile." if not self.hidden else
                             "You have hidden this profile.", markup=False)
            else:
                out = Text()
                if p.about:
                    out.append_text(manifest_text(p.about, p.about_type))
                    out.append("\n\n")
                lines = []
                if p.lang:
                    lines.append("Writes: " + ", ".join(p.lang))
                if p.links:
                    lines.append("Links, as stated by this identity (not checked):")
                    for link in p.links:
                        label = f"  ({link['label']})" if link.get("label") else ""
                        lines.append(f"  {link['kind']}: {link['value']}{label}")
                out.append("\n".join(lines))
                out.rstrip()
                # a scroll of its own: with Markdown, about can be a small home page
                with VerticalScroll(id="profile-view"):
                    yield Static(out, id="profile-text", markup=False)
            with Horizontal(classes="buttons"):
                yield Button("Close", variant="primary", id="cancel")
                if self.mine:
                    yield Button("Your feed" if self.feed_started else "Start your feed",
                                 id="my-feed")
                    yield Button("Your site" if self.site_started else "Start your site",
                                 id="my-site")
                else:
                    yield Button("Following" if self.following else "Follow", id="follow")
                    yield Button("Remove contact" if self.contact else "Add contact", id="contact")
                    if self.theirs.get("feed"):
                        yield Button("Their feed", id="their-feed")
                    if self.theirs.get("site"):
                        yield Button("Their site", id="their-site")
                    yield Button("Show" if self.hidden else "Hide", id="hide")

    @on(Button.Pressed, "#hide")
    def _hide(self) -> None:
        self.dismiss("show" if self.hidden else "hide")

    @on(Button.Pressed, "#my-site")
    def _my_site(self) -> None:
        self.dismiss("my-site")

    @on(Button.Pressed, "#my-feed")
    def _my_feed(self) -> None:
        self.dismiss("my-feed")

    @on(Button.Pressed, "#follow")
    def _follow(self) -> None:
        self.dismiss("unfollow" if self.following else "follow")

    @on(Button.Pressed, "#their-feed")
    def _their_feed(self) -> None:
        self.dismiss("their-feed")

    @on(Button.Pressed, "#contact")
    def _contact(self) -> None:
        self.dismiss("uncontact" if self.contact else "contact")

    @on(Button.Pressed, "#their-site")
    def _their_site(self) -> None:
        self.dismiss("their-site")


class EditProfileDialog(Dialog):
    """Your public profile (UN-RFC 0022): about, languages and links, one per line."""

    def __init__(self, current: dict | None):
        super().__init__()
        self.current = current or {"v": 1}

    def compose(self) -> ComposeResult:
        """Editing your profile: about (Markdown or plain), links, and the bot mark."""
        c = self.current
        links = "\n".join(f"{link['kind']} {link['value']}"
                           + (f"  # {link['label']}" if link.get("label") else "")
                           for link in c.get("links", []))
        with Vertical(classes="dialog compose"):
            yield Label("Your public profile - anybody can read it", classes="title")
            yield Label("About you - a few lines, or a small home page:", classes="hint")
            yield TextArea(c.get("about", ""), id="about", soft_wrap=True)
            yield Checkbox(MARKDOWN_HINT, id="markdown",
                           value=writes_markdown(self.app, c.get("about_type"), c.get("about", "")))
            yield Input(value=", ".join(c.get("lang", [])), id="lang",
                        placeholder="Languages you write, e.g. da, en")
            yield Label("Links, one per line: kind value  (kinds: " + ", ".join(prof.LINK_KINDS)
                        + ", or x-something)", classes="hint")
            yield TextArea(links, id="links", soft_wrap=False)
            yield Input(placeholder="A picture: the path of a file, e.g. ~/me.jpg (empty: as it is)",
                        id="picture")
            yield Label("The middle square is kept, at most 128 pixels, without its metadata "
                        "(UN-RFC 0022). Everyone sees it; a terminal shows your identicon.",
                        classes="hint")
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Remove profile", variant="error", id="remove")
                yield Button("Publish", variant="primary", id="ok")

    @on(Button.Pressed, "#remove")
    def _remove(self) -> None:
        self.dismiss("remove")

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        links = []
        for n, line in enumerate(self.query_one("#links", TextArea).text.splitlines(), 1):
            line, _, label = line.partition("#")
            parts = line.split(None, 1)
            if not parts:
                continue
            if len(parts) < 2:
                self.app.notify(f"Line {n}: kind, a space, then the address", severity="error")
                return
            link = {"kind": parts[0].lower(), "value": parts[1].strip()}
            if label.strip():
                link["label"] = label.strip()
            links.append(link)
        lang = [t.strip() for t in self.query_one("#lang", Input).value.split(",") if t.strip()]
        try:
            obj = prof.make_profile(self.query_one("#about", TextArea).text, links, lang,
                                    bot=self.current.get("bot") is True,
                                    markdown=self.query_one("#markdown", Checkbox).value)
        except prof.ProfileError as exc:
            self.app.notify(str(exc), severity="error")
            return
        self.picture = self.query_one("#picture", Input).value.strip()   # read after dismiss
        self.dismiss(obj)
