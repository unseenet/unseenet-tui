"""Unlocking, setting up, the passphrase and Tor bridges.

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

from ..helpers import known_node_details
from .base import Dialog


class UnlockDialog(Dialog):
    def __init__(self, path: Path, error: str = ""):
        super().__init__()
        self.path, self.error = path, error
        self.new = not path.exists()

    def compose(self) -> ComposeResult:
        """Unlocking the vault: its path and the passphrase."""
        with Vertical(classes="dialog"):
            yield Label("Create a new vault" if self.new else "Unlock your vault", classes="title")
            yield Label(str(self.path.resolve()), classes="hint")
            yield Input(placeholder="Passphrase", password=True, id="pw")
            if self.new:
                yield Input(placeholder="Repeat passphrase", password=True, id="pw2")
                yield Label("Everything you store is encrypted with this passphrase. "
                            "It cannot be recovered if you forget it.", classes="hint")
            if self.error:
                yield Label(self.error, classes="error")
            with Horizontal(classes="buttons"):
                yield Button("Quit", id="cancel")
                yield Button("Create" if self.new else "Unlock", variant="primary", id="ok")

    @on(Input.Submitted)
    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        pw = self.query_one("#pw", Input).value
        if self.new and pw != self.query_one("#pw2", Input).value:
            self.app.notify("The passphrases do not match", severity="error")
            return
        if len(pw) < 4:
            self.app.notify("Passphrase too short", severity="error")
            return
        self.dismiss(pw)


class ChangePassphraseDialog(Dialog):
    """The current passphrase and a new one, twice. Returns (old, new)."""

    def __init__(self, path: Path, error: str = ""):
        super().__init__()
        self.path, self.error = path, error

    def compose(self) -> ComposeResult:
        """Changing the passphrase: the old one, the new one twice, and where copies are."""
        with Vertical(classes="dialog"):
            yield Label("Change passphrase", classes="title")
            yield Label(str(self.path.resolve()), classes="hint")
            yield Input(placeholder="Current passphrase", password=True, id="old")
            yield Input(placeholder="New passphrase", password=True, id="pw")
            yield Input(placeholder="Repeat new passphrase", password=True, id="pw2")
            yield Label("Only this vault file changes. Copies of it on other devices, sticks "
                        "or backups still open with the old passphrase: replace or delete them.",
                        classes="hint")
            if self.error:
                yield Label(self.error, classes="error")
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Change", variant="primary", id="ok")

    @on(Input.Submitted)
    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        old = self.query_one("#old", Input).value
        pw = self.query_one("#pw", Input).value
        if pw != self.query_one("#pw2", Input).value:
            self.app.notify("The new passphrases do not match", severity="error")
            return
        if len(pw) < 4:
            self.app.notify("Passphrase too short", severity="error")
            return
        if pw == old:
            self.app.notify("The new passphrase is the same as the current one", severity="error")
            return
        self.dismiss((old, pw))


OTHER_NODE = "__other__"


class SetupDialog(Dialog):
    """First run: identity and node."""

    def __init__(self, need_identity: bool, need_node: bool, known: list | None = None):
        super().__init__()
        self.need_identity, self.need_node = need_identity, need_node
        from unseenet.known_nodes import KNOWN_NODES
        self.known = list(known or KNOWN_NODES)

    def compose(self) -> ComposeResult:
        """First start: your name and a node to begin with."""
        with Vertical(classes="dialog"):
            yield Label("Welcome to Unseenet", classes="title")
            if self.need_identity:
                yield Label("Your identity is a key pair. The name is just a label others see.",
                            classes="hint")
                # UN-RFC 0050: a new identity, or one you have - from its words or a device
                yield Select([("Create a new identity", "new"),
                              ("Restore from recovery words", "restore"),
                              ("Get identity from another device", "copy")],
                             value="new", allow_blank=False, id="how")
                yield Input(placeholder="Display name, e.g. Andreas", id="display")
            if self.need_node:
                yield Label("Which node do you want to connect to? Pick one of the known nodes, "
                            "or enter the address of another you were given.", classes="hint")
                choices = [(f"{n.name} - {n.description}", n.address) for n in self.known]
                # a known node at random, so new users do not all start on the first
                # (UN-RFC 0043 section 5.1); the list keeps its order
                first = random.choice(self.known) if self.known else None
                choices.append(("Another node - enter its address", OTHER_NODE))
                yield Select(choices, value=first.address if first else OTHER_NODE,
                             allow_blank=False, id="known")
                other = Input(placeholder="xyz...abc.onion  (or host:port for testing)", id="node")
                other.display = False
                yield other
                yield Static(known_node_details(first) if first else "",
                             id="known-details", classes="details", markup=False)
                yield Checkbox("Also use the other known nodes as backups", value=True, id="backups")
            with Horizontal(classes="buttons"):
                yield Button("Quit", id="cancel")
                yield Button("Continue", variant="primary", id="ok")

    @on(Select.Changed, "#how")
    def _how_changed(self, event: Select.Changed) -> None:
        self.query_one("#display").display = event.value == "new"

    @on(Select.Changed, "#known")
    def _known_changed(self, event: Select.Changed) -> None:
        self.query_one("#node").display = event.value == OTHER_NODE
        if event.value == OTHER_NODE:
            self.query_one("#node").focus()
        n = next((k for k in self.known if k.address == event.value), None)
        details = self.query_one("#known-details", Static)
        details.display = n is not None
        if n:
            details.update(known_node_details(n))

    @on(Input.Submitted)
    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        result = {}
        if self.need_identity:
            result["how"] = self.query_one("#how", Select).value
            result["display"] = self.query_one("#display", Input).value.strip()
            if result["how"] == "new" and not result["display"]:
                self.app.notify("Please choose a display name", severity="error")
                return
        if self.need_node:
            known = self.query_one("#known", Select).value
            node = self.query_one("#node", Input).value.strip() if known == OTHER_NODE else known
            if not node:
                self.app.notify("Please enter a node address", severity="error")
                return
            result["node"] = node
            if self.query_one("#backups", Checkbox).value:
                result["backups"] = [n.address for n in self.known if n.address != node]
        self.dismiss(result)


class BridgesDialog(Dialog):
    """How Tor connects: directly, or through bridges where Tor is blocked."""

    BINDINGS = [Binding("escape", "cancel", "Cancel"), Binding("ctrl+s", "save", "Save")]
    CHOICES = [("Off - connect to Tor directly (best when Tor is not blocked)", "off"),
               ("Built-in obfs4 bridges - Tor traffic disguised as random data", "obfs4"),
               ("Built-in Snowflake - through volunteers' browsers, hard to block", "snowflake"),
               ("My own bridges - lines from bridges.torproject.org", "custom")]

    def __init__(self, setting: dict):
        super().__init__()
        self.setting = setting

    def compose(self) -> ComposeResult:
        """Tor bridges, where Tor is blocked: the kind and the bridge lines."""
        with Vertical(classes="dialog"):
            yield Label("Tor bridges", classes="title")
            yield Label("If Tor cannot connect - at work, at school, or in a country that blocks "
                        "Tor - bridges are hidden entrances to the Tor network. The built-in ones "
                        "come with the portable builds. For your own, ask bridges.torproject.org "
                        "or email bridges@torproject.org, and paste the lines below.",
                        classes="hint", markup=False)
            yield Select(self.CHOICES, value=self.setting.get("mode", "off"), allow_blank=False,
                         id="mode")
            lines = TextArea("\n".join(self.setting.get("custom", [])), id="lines", soft_wrap=True)
            lines.display = self.setting.get("mode") == "custom"     # before the first paint
            yield lines
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Save", variant="primary", id="ok")

    def on_mount(self) -> None:
        """Size the bridge lines' field."""
        self.query_one("#lines", TextArea).styles.height = 6
        self._show_lines()

    @on(Select.Changed, "#mode")
    def _show_lines(self, event=None) -> None:
        self.query_one("#lines").display = self.query_one("#mode", Select).value == "custom"

    @on(Button.Pressed, "#ok")
    def action_save(self) -> None:
        """Ctrl+S: check and keep the bridges."""
        mode = self.query_one("#mode", Select).value
        lines = [ln.strip() for ln in self.query_one("#lines", TextArea).text.splitlines()
                 if ln.strip()]
        if mode == "custom" and not lines:
            self.app.notify("Paste at least one bridge line", severity="warning")
            return
        self.dismiss({"mode": mode, "custom": lines})
