"""Reporting and the moderators' tools.

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


class ReportDialog(Dialog):
    """Report a post to the people who can act on it (#74)."""

    def __init__(self, subject: str, targets: list[tuple[str, str, str]]):
        super().__init__()
        self.subject, self.targets = subject, targets

    def compose(self) -> ComposeResult:
        """Reporting a post to the group's moderators: why, and Send."""
        with Vertical(classes="dialog"):
            yield Label(f"Report \"{self.subject}\"", classes="title", markup=False)
            yield Label("For posts that break the group's rules or the law. The people you "
                        "choose get the post and your note as a private message. In an "
                        "emergency, contact the police: nobody on Unseenet can see who anyone is.",
                        classes="hint")
            for i, (who, _, _) in enumerate(self.targets):
                yield Checkbox(who, value=True, id=f"t{i}")
            yield Input(placeholder="What is wrong with it? (optional)", id="note")
            yield Checkbox("Anonymous - they cannot see who reported it or answer you",
                           value=False, id="anon")
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Report", variant="error", id="ok")

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        chosen = [t for i, t in enumerate(self.targets) if self.query_one(f"#t{i}", Checkbox).value]
        if not chosen:
            self.app.notify("Choose at least one", severity="warning")
            return
        self.dismiss((chosen, self.query_one("#note", Input).value,
                      self.query_one("#anon", Checkbox).value))


class ModerationDialog(Dialog):
    """Submissions to groups you moderate (UN-RFC 0009 section 3)."""

    def __init__(self, items: list[tuple[str, str, str]], title: str = "Moderation queue",
                 hint: str = "Posts waiting for a moderator. Approving posts them unchanged; "
                              "the first moderator to act decides.",
                 prompt: str = "Choose a post", reject: str = "Reject", approve: str = "Approve"):
        super().__init__()
        self.items = items                  # (id, label, preview)
        self.texts = (title, hint, prompt, reject, approve)

    def compose(self) -> ComposeResult:
        """The moderators' queue: a submission with Approve and Reject."""
        title, hint, prompt, reject, approve = self.texts
        with Vertical(classes="dialog list"):
            yield Label(title, classes="title", markup=False)
            yield Label(hint, classes="hint", markup=False)
            yield Select([(label, i) for i, label, _ in self.items], prompt=prompt, id="which")
            yield Static("", id="preview", classes="details", markup=False)
            with Horizontal(classes="buttons"):
                yield Button("Close", id="cancel")
                yield Button(reject, variant="error", id="reject")
                yield Button(approve, variant="primary", id="approve")

    @on(Select.Changed, "#which")
    def _show(self, event: Select.Changed) -> None:
        text = next((p for i, _, p in self.items if i == event.value), "")
        self.query_one("#preview", Static).update(text)

    @on(Button.Pressed, "#approve")
    @on(Button.Pressed, "#reject")
    def _act(self, event: Button.Pressed) -> None:
        which = self.query_one("#which", Select).value
        if isinstance(which, str):
            self.dismiss((event.button.id, which))
