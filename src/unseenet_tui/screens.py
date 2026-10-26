"""The screens and widgets: the chat box, reading a post, files on it, About.

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

from .helpers import CSS, DOCS_URL, REPO_URL
from .dialogs.base import Dialog


# ======================================================================
# Dialogs
# ======================================================================

from .emoji import EmojiCompletion  # noqa: E402 - #6


class ChatBox(EmojiCompletion, TextArea):
    """The chat line (#530): Enter sends; Alt+Enter (or Shift+Enter where the terminal
    tells them apart, or Ctrl+J) starts a new line, since a chat line may hold several
    (UN-RFC 0031 section 3.3). Grows with its text up to a few lines. ``value`` and
    ``Submitted`` keep the shape of the Input it replaces."""

    NEWLINE_KEYS = ("alt+enter", "shift+enter", "ctrl+j", "ctrl+enter")

    class Submitted(Message_):
        def __init__(self, box: "ChatBox", value: str):
            super().__init__()
            self.input, self.value = box, value

        @property
        def control(self) -> "ChatBox":
            """The chat box the line was written in."""
            return self.input

    def __init__(self, placeholder: str = "", **kw):
        super().__init__("", soft_wrap=True, show_line_numbers=False, tab_behavior="focus", **kw)
        self.placeholder = placeholder

    @property
    def value(self) -> str:
        """The text in the chat box (set: replace it, the cursor at the end)."""
        return self.text

    @value.setter
    def value(self, text: str) -> None:
        """The text in the chat box (set: replace it, the cursor at the end)."""
        self.load_text(text)
        self.move_cursor(self.document.end)

    async def _on_key(self, event) -> None:
        if await self.emoji_key(event):                 # :ta while typing (#6)
            return
        if event.key == "enter":
            event.stop(); event.prevent_default()
            if not self.disabled:
                self.post_message(self.Submitted(self, self.text))
            return
        if event.key in self.NEWLINE_KEYS:
            event.stop(); event.prevent_default()
            self.insert("\n")
            return
        await super()._on_key(event)
        self.refresh_emoji()


def attachments_text(attachments) -> Text:
    """The files of a post as a terminal shows them (UN-RFC 0027 section 9.2): what the
    author says each is, its type and size - a terminal does not draw pictures here,
    and f saves them."""
    out = Text("Files - f saves them:\n", style="bold")
    for i, a in enumerate(attachments, 1):
        kind = "picture" if a["type"].startswith("image/") else a["type"]
        size = f"{a['size'] / 1024:.0f} KiB" if a["size"] >= 1024 else f"{a['size']} octets"
        dim = f", {a['dim'][0]}×{a['dim'][1]}" if a.get("dim") else ""
        line = f"  {i}. {a.get('name') or 'file'} - {kind}, {size}{dim}"
        out.append(line, style="dim")
        if a.get("alt"):
            out.append(f"\n     {a['alt']}")
        out.append("\n")
    return out


def save_name(folder: Path, name: str) -> Path:
    """A file name in ``folder`` that is not taken, from the name a post gives (never
    a path: only its last part, and nothing hidden)."""
    base = (name or "file").replace("\\", "/").rsplit("/", 1)[-1].lstrip(".") or "file"
    stem, dot, ext = base.rpartition(".")
    stem, ext = (stem, "." + ext) if dot and stem else (base, "")
    path, n = folder / base, 1
    while path.exists():
        n += 1
        path = folder / f"{stem} ({n}){ext}"
    return path


class ReadScreen(Screen):
    """One message on the whole screen. r replies, m writes privately, n and p
    go to the next and previous message, f saves its files, u shows the author's
    profile, Esc goes back."""
    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("q", "back", "Back", show=False),
        Binding("r", "reply", "Reply"),
        Binding("m", "message", "Message"),
        Binding("n", "next", "Next"),
        Binding("p", "previous", "Previous"),
        Binding("space", "page", "Page down", show=False),
        Binding("number_sign", "group_ref", "#group", show=False),
        Binding("f", "save_files", "Save files", show=False),
        Binding("u", "profile", "Profile", show=False),   # the author's: feed, site, follow
        Binding("l", "react", "React"),                   # UN-RFC 0048
    ]
    CSS = """
    ReadScreen #read-head { padding: 0 1; background: $boost; color: $text; }
    ReadScreen #read-scroll { height: 1fr; padding: 0 2; }
    """

    def __init__(self, msg: Message):
        super().__init__()
        self.msg = msg

    def compose(self) -> ComposeResult:
        """The read screen: the header, the post's head and its body in a scroll."""
        yield Header()
        yield Static("", id="read-head")
        with VerticalScroll(id="read-scroll"):
            yield Static("", id="read-body")
        yield Footer()

    def on_mount(self) -> None:
        """Show the post and put the focus in it."""
        self.show(self.msg)
        self.query_one("#read-scroll").focus()

    def show(self, msg: Message, *, keep_place: bool = False) -> None:
        """Show msg; keep_place when the same post was only refreshed (#137),
        so a reader in the middle of a long post isn't sent back to the top."""
        self.msg = msg
        head, body = self.app.article(msg)
        self.query_one("#read-head", Static).update(head)
        self.query_one("#read-body", Static).update(body)
        if not keep_place:
            self.query_one("#read-scroll").scroll_home(animate=False)

    def action_back(self) -> None:
        """Esc: back to the list."""
        self.app.pop_screen()

    async def action_save_files(self) -> None:
        """Save the files of this post in your Downloads folder (else your home), as
        the node sent them and checked against their blob IDs. They are not opened:
        a program outside Unseenet may go to the internet without Tor."""
        if not self.msg.attachments:
            self.app.notify("This post has no files", severity="warning")
            return
        folder = Path.home() / "Downloads"
        folder = folder if folder.is_dir() else Path.home()
        saved = []
        for a in self.msg.attachments:
            try:
                octets = await self.app.session.fetch_blob(self.msg.group, a["blob"])
            except (SessionError, OSError, ConnectionError, NNTPError) as exc:
                self.app.notify(f"{a.get('name') or 'A file'}: {exc}", severity="error")
                continue
            path = save_name(folder, a.get("name") or "")
            path.write_bytes(octets)
            saved.append(path.name)
        if saved:
            self.app.notify(f"Saved in {folder}: {', '.join(saved)}. Opening them in another "
                            "program happens outside Unseenet and Tor.", timeout=10)

    def action_reply(self) -> None:
        """r: reply to the post read."""
        self.app.action_reply()

    def action_react(self) -> None:
        """l: react to the post read (UN-RFC 0048)."""
        self.app.action_react(self.msg)

    def action_message(self) -> None:
        """m: a private message to its author."""
        self.app.action_message()

    def action_group_ref(self) -> None:
        """g: open a #group the post names."""
        self.app.action_group_ref()

    def action_next(self) -> None:
        """n: the next post, or say it was the last."""
        if self.app.neighbour(+1) is None:
            self.app.notify("That was the last one")

    def action_profile(self) -> None:
        """p: the author's profile."""
        if self.msg.readable and self.msg.author_key:
            self.app.action_show_profile(self.msg.author_key)
        else:
            self.app.notify("This post has nobody to show", severity="warning")

    def action_previous(self) -> None:
        """Shift+N: the previous post, or say it was the first."""
        if self.app.neighbour(-1) is None:
            self.app.notify("That was the first one")

    def action_page(self) -> None:
        """Space: a page down, and the next post at the end."""
        scroll = self.query_one("#read-scroll")
        if scroll.scroll_y >= scroll.max_scroll_y:
            self.action_next()                 # like the old newsreaders: space reads on
        else:
            scroll.scroll_page_down(animate=False)


class AboutDialog(Dialog):
    """F1: what this program is, and where the project lives."""

    def compose(self) -> ComposeResult:
        # the text is the core's (core/about.py), so every client says the same
        """About: the core's text (core/about.py, the same in every client), the versions
        and links."""
        text = Text()

        def para(p: str) -> None:
            text.append(p + "\n\n")

        for s in about.SECTIONS:
            text.append(s.title + "\n\n", style="bold")
            for p in s.paragraphs:
                para(p)
        text.append(about.COMPARISONS_TITLE + "\n\n", style="bold")
        para(about.COMPARISONS_INTRO)
        for c in about.COMPARISONS:
            text.append(c.name + ": ", style="bold")
            para(c.text)
        text.append(about.STATUS.title + "\n\n", style="bold")
        for p in about.STATUS.paragraphs:
            para(p)
        text.append(f"Source:         {REPO_URL}\nDocumentation:  {DOCS_URL}")
        with Vertical(classes="dialog about"):
            yield Label(f"About Unseenet {__version__} - {about.TAGLINE}", classes="title",
                        markup=False)
            with VerticalScroll(id="about-text"):
                yield Static(text)
            with Horizontal(classes="buttons"):
                yield Button("Close", id="cancel")
                yield Button("GitHub", id="repo")
                yield Button("Documentation", variant="primary", id="docs")

    @on(Button.Pressed, "#repo")
    def _repo(self) -> None:
        self.app.open_url(REPO_URL)

    @on(Button.Pressed, "#docs")
    def _docs(self) -> None:
        self.app.open_url(DOCS_URL)
