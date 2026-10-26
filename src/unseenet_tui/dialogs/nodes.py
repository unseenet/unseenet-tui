"""Your nodes: the list, adding one, and a node's info.

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

from ..helpers import known_node_details, node_details
from .base import Dialog
from .setup import OTHER_NODE


class NodesDialog(Dialog):
    """Your nodes. Your key is in the vault, so you are the same person on all of them."""

    def __init__(self, nodes: list[str], infos: dict, counts: dict[str, int], known: list | None = None,
                 labels: dict[str, str] | None = None):
        super().__init__()
        self.nodes, self.infos, self.counts = nodes, infos, counts
        self.known = {n.address: n for n in (known or [])}
        self.labels = labels or {}    # the session's names: listed, else marked unlisted (#518)

    def compose(self) -> ComposeResult:
        """Your nodes: the table of them with how each is doing, and add, remove, first,
        info."""
        with Vertical(classes="dialog list"):
            yield Label("Your nodes", classes="title")
            yield Label("You are the same on every node - your key lives in your vault. "
                        "Each group is read from one node; new groups and your inbox go to "
                        "the default node (♦).", classes="hint")
            yield DataTable(id="nodes-table", cursor_type="row", zebra_stripes=True)
            yield Static("", id="node-details", classes="details", markup=False)
            with Horizontal(classes="buttons"):
                yield Button("OK", id="cancel")
                yield Button("Add", id="add")
                yield Button("Remove", id="remove")
                yield Button("Make default", id="default")
                yield Button("Details", variant="primary", id="info")

    def on_mount(self) -> None:
        """Fill the table and the focus on the node in use."""
        table = self.query_one("#nodes-table", DataTable)
        table.add_columns("", "Name", "Address", "Your groups", "")
        for i, address in enumerate(self.nodes):
            info = self.infos.get(address)
            known = self.known.get(address)
            name = self.labels.get(address) or (known.name if known else "") or (info or {}).get("name", "")
            state = "unreachable" if info is None else "ok"
            short = address if len(address) <= 34 else address[:16] + "…" + address[-14:]
            table.add_row("♦" if i == 0 else "", Text(name), Text(short),
                          str(self.counts.get(address, 0)), state, key=address)
        table.focus()

    def _selected(self) -> str | None:
        table = self.query_one("#nodes-table", DataTable)
        if not table.row_count:
            return None
        return table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value

    @on(DataTable.RowHighlighted, "#nodes-table")
    def _highlighted(self, event: DataTable.RowHighlighted) -> None:
        address = event.row_key.value
        self.query_one("#node-details", Static).update(node_details(
            address, self.infos.get(address), self.known.get(address),
            self.counts.get(address, 0), default=address == self.nodes[0]))

    @on(DataTable.RowSelected, "#nodes-table")
    def _row(self, event: DataTable.RowSelected) -> None:
        self.dismiss(("info", event.row_key.value))

    @on(Button.Pressed, "#add")
    def _add(self) -> None:
        self.dismiss(("add", None))

    @on(Button.Pressed, "#remove")
    @on(Button.Pressed, "#default")
    @on(Button.Pressed, "#info")
    def _act(self, event: Button.Pressed) -> None:
        address = self._selected()
        if address:
            self.dismiss((event.button.id, address))


class AddNodeDialog(Dialog):
    """Add a node: one of the known nodes you do not use yet, or any address."""

    def __init__(self, known: list, used: list[str]):
        super().__init__()
        self.known = [n for n in known if n.address not in used]
        self.choices = [(f"{n.name} - {n.description}", n.address) for n in self.known]
        self.choices.append(("Another node - enter its address", OTHER_NODE))

    def compose(self) -> ComposeResult:
        """Adding a node: its address, or one from the known list."""
        with Vertical(classes="dialog"):
            yield Label("Add a node", classes="title")
            yield Label("You keep your key and your groups; the node's groups are added to "
                        "what you can browse (g).", classes="hint")
            yield Select(self.choices, value=self.choices[0][1], allow_blank=False, id="known")
            other = Input(placeholder="xyz...abc.onion  (add :port if it is not 119)", id="node")
            other.display = len(self.choices) == 1
            yield other
            details = Static(known_node_details(self.known[0]) if self.known else "",
                             id="known-details", classes="details", markup=False)
            details.display = bool(self.known)
            yield details
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Add", variant="primary", id="ok")

    @on(Select.Changed, "#known")
    def _changed(self, event: Select.Changed) -> None:
        self.query_one("#node").display = event.value == OTHER_NODE
        n = next((k for k in self.known if k.address == event.value), None)
        details = self.query_one("#known-details", Static)
        details.display = n is not None
        if n:
            details.update(known_node_details(n))

    @on(Input.Submitted)
    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        choice = self.query_one("#known", Select).value
        address = self.query_one("#node", Input).value.strip() if choice == OTHER_NODE else choice
        if not address:
            self.app.notify("Enter the node's address", severity="error")
            return
        self.dismiss(address)


class NodeInfoDialog(Dialog):
    """UN-RFC 0011 node information; can start a message to the operator."""

    def __init__(self, info: dict, address: str):
        super().__init__()
        self.info, self.address = info, address

    def compose(self) -> ComposeResult:
        """A node's info: who runs it, its policy, what it carries and its limits."""
        i, p = self.info, self.info.get("policy", {})
        can_write = bool(i.get("contact") and i.get("operator"))
        lines = [f"Address:   {self.address}"]
        if not i:
            lines.append("This node publishes no information about itself.")
        else:
            lines += [f"Name:      {i.get('name', '')}",
                      f"Software:  {i.get('software', '')}"]
            if i.get("description"):
                lines.append(f"About:     {i['description']}")
            if i.get("node_key"):
                lines.append(f"Node key:  {i['node_key']}")
            lines += ["",
                      f"New groups:       {p.get('new_groups', '?')}",
                      f"Kept for:         {p.get('retention_days', '?')} days",
                      f"Minimum postage:  {p.get('min_pow', '?')} bits",
                      f"Logs:             {p.get('logging', '?')}",
                      f"Leaves out:       {p.get('excludes') or 'nothing'}",
                      "",
                      ("Operator:  " + i["operator"]) if i.get("operator") else "No operator contact given."]
        with Vertical(classes="dialog"):
            yield Label("About this node", classes="title")
            yield Static("\n".join(lines), markup=False)
            yield Label("The node states this itself; the protocol does not enforce it.", classes="hint")
            with Horizontal(classes="buttons"):
                yield Button("Close", id="cancel")
                if can_write:
                    yield Button("Write to operator", variant="primary", id="ok")

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        self.dismiss(True)
