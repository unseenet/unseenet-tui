"""The dialogs everything builds on: Dialog, and the input, info, confirm and key-list dialogs.

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



class Dialog(ModalScreen):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]
    CANCEL_RESULT = None          # what closing with Esc or the cancel button returns

    def dismiss(self, result=None):
        # Only the dialog on top can close, and only once: two handlers for one
        # button press must not pop the screen below it (ScreenStackError).
        """Close the dialog with a result - only the one on top, and only once."""
        if self.app.screen is not self:
            return None
        return super().dismiss(result)

    def action_cancel(self) -> None:
        """Esc: close with CANCEL_RESULT."""
        self.dismiss(self.CANCEL_RESULT)

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(self.CANCEL_RESULT)


class InputDialog(Dialog):
    def __init__(self, title: str, hint: str, placeholder: str, value: str = ""):
        super().__init__()
        self.title_, self.hint, self.placeholder, self.value = title, hint, placeholder, value

    def compose(self) -> ComposeResult:
        """A title, a hint, one field, Cancel and OK."""
        with Vertical(classes="dialog"):
            yield Label(self.title_, classes="title")
            yield Label(self.hint, classes="hint")
            yield Input(value=self.value, placeholder=self.placeholder, id="value")
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("OK", variant="primary", id="ok")

    @on(Input.Submitted)
    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        value = self.query_one("#value", Input).value.strip()
        self.dismiss(value or None)


class InfoDialog(Dialog):
    """A value to select and copy; ``web``, when given, is a second one under its own
    label - an address's web form (UN-RFC 0031 section 3.4.1)."""
    def __init__(self, title: str, text: str, web: str | None = None):
        super().__init__()
        self.title_, self.text, self.web = title, text, web

    def compose(self) -> ComposeResult:
        """A title, a text to read and copy, and Close."""
        with Vertical(classes="dialog"):
            yield Label(self.title_, classes="title")
            yield Input(value=self.text, id="value")   # an Input so it can be selected and copied
            if self.web:
                yield Label("On the web - opens in the network for anyone with a client, "
                            "on any entrance:", markup=False)
                yield Input(value=self.web, id="web")
            with Horizontal(classes="buttons"):
                yield Button("Close", variant="primary", id="cancel")


class ConfirmDialog(Dialog):
    # Cancel has the focus, so Enter never confirms by accident. AUTO_FOCUS instead of
    # querying in on_mount: closing the app while the dialog mounted raised NoMatches.
    AUTO_FOCUS = "#cancel"

    def __init__(self, title: str, text: str, confirm: str = "OK", danger: bool = False):
        super().__init__()
        self.title_, self.text, self.confirm, self.danger = title, text, confirm, danger

    def compose(self) -> ComposeResult:
        """A question, its explanation, and the two answers."""
        with Vertical(classes="dialog"):
            yield Label(self.title_, classes="title", markup=False)
            yield Static(self.text, markup=False)
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button(self.confirm, variant="error" if self.danger else "primary", id="ok")

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        self.dismiss(True)


class KeyListDialog(Dialog):
    """A list of people (key, label); pick one for an action."""

    def __init__(self, title: str, hint: str, people: list[tuple[str, str]], action: str):
        super().__init__()
        self.title_, self.hint, self.people, self.action = title, hint, people, action

    def compose(self) -> ComposeResult:
        """A title, a hint and a list to choose one from."""
        with Vertical(classes="dialog"):
            yield Label(self.title_, classes="title", markup=False)
            yield Label(self.hint, classes="hint", markup=False)
            yield Select([(f"{label}  ({key[:20]}...)" if key.startswith("ed25519:") else label, key)
                          for key, label in self.people], prompt="Choose", id="who")
            with Horizontal(classes="buttons"):
                yield Button("Close", id="cancel")
                yield Button(self.action, variant="primary", id="ok")

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        who = self.query_one("#who", Select).value
        if isinstance(who, str):
            self.dismiss(who)
