"""UnseenetApp's addresses: opening and copying them (UN-RFC 0031 3.4.1).

A mixin of the terminal client's app, split from app.py by its sections (#20) - a
pure move. Its methods use the app's widgets and session as self; @on handlers stay
in UnseenetApp, where Textual collects them.
"""

from __future__ import annotations

# ruff: noqa: F401, E402 - app.py's imports, kept as they were
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
from .helpers import REFRESH_SECONDS, REPO_URL, DOCS_URL, ACROSS, FEED_MARK, START_FEED, START_SITE, CHATS, CHATS_SHOWN, CONTACTS, FOLDED, FEED_SHOWN, COMMENT_PAGE, CLIP_LINES, SITE_MARK, REPLY_MARK, MENTION_MARK, for_you_marks, for_you_news, sess_default_pow, group_name_problem, GROUP_KINDS, known_node_details, node_details, ICONS, _ONION, PIN_MARK, ICON_LEGEND, ORIGIN_MARK, NETWORK_NAMES, network_name, origin_text, edited_when, group_icons, is_personal_inbox, by_hierarchy, CSS  # noqa: F401
from .dialogs.base import Dialog, InputDialog, InfoDialog, ConfirmDialog, KeyListDialog  # noqa: F401
from .dialogs.setup import UnlockDialog, ChangePassphraseDialog, OTHER_NODE, SetupDialog, BridgesDialog  # noqa: F401
from .dialogs.compose import ComposeDialog, PageDialog, PreviewDialog, SignatureDialog, PinDialog, ReactDialog, reactions_text  # noqa: F401
from .dialogs.people import ContactsDialog, ChatsDialog, PersonPicker, NamesDialog, PROFILE_WARNING, identicon_text, MARKDOWN_HINT, manifest_text, profiles_clean, writes_markdown, ProfileDialog, EditProfileDialog  # noqa: F401
from .dialogs.groups import DELIVERY_CHOICES, DELIVERY, SITE_CHOICE, KINDS_CHOICES, KINDS, REACTIONS_CHOICES, reactions_choice, COMMENTS_CHOICES, FILES_CHOICES, FILES_GROUP_TYPES, reactions_label, comments_label, GROUP_NAME_HELP, CreateGroupDialog, GroupInfoDialog, EditGroupDialog, BrowseTree, NO_SELECT_ON_FOCUS, BrowseDialog  # noqa: F401
from .dialogs.moderation import ReportDialog, ModerationDialog  # noqa: F401
from .dialogs.nodes import NodesDialog, AddNodeDialog, NodeInfoDialog  # noqa: F401
from .screens import ChatBox, attachments_text, save_name, ReadScreen, AboutDialog  # noqa: F401


class AddressesMixin:

    # --- addresses (UN-RFC 0031 section 3.4.1, #357) ---
    def current_address(self) -> str | None:
        """The unseenet: address of what you are on: the post or page, else the group,
        else - in a chat with someone - the person."""
        from unseenet.core import addresses as addr
        s, gid, m = self.session, self.current_group or "", self.current_message
        if gid.startswith(CONTACT):
            return addr.identity_address(crypto.fingerprint(crypto.key_from_text(gid[len(CONTACT):])))
        if m is not None and not m.private and m.group and not m.group.startswith(CONTACT):
            if m.page is not None:
                return addr.page_address(m.group, m.page)
            return addr.post_address(m.group, m.message_id)
        if gid and gid not in ACROSS and gid != s.my_inbox:
            return addr.group_address(gid)
        return None

    @work(group="dialog", exit_on_error=False)
    async def action_copy_address(self) -> None:
        """Show the address of what you are on, to copy - and put it on the clipboard
        where the terminal lets us."""
        a = self.current_address()
        if a is None:
            self.notify("Choose a group, a post or a chat first", severity="warning")
            return
        try:
            self.copy_to_clipboard(a)              # OSC 52: many terminals take it
        except Exception:  # noqa: BLE001 - a terminal without it: the dialog is enough
            pass
        from unseenet.core import addresses as addr
        try:
            web = addr.web_address(a)
        except addr.AddressError:
            web = None                               # no listed entrance: the one form only
        await self.push_screen_wait(InfoDialog("Address - select it to copy", a, web))

    @work(group="dialog", exit_on_error=False)
    async def action_open_address(self) -> None:
        """Open an unseenet: address - a group, a post, a page or a person."""
        from unseenet.core import addresses as addr
        s = self.session
        text = await self.push_screen_wait(InputDialog(
            "Open an address", "An unseenet: address - of a group, a post, a page or a person - "
            "or its web form, https://<entrance>/a/..., which opens here over Tor.",
            "unseenet:..."))
        if not text:
            return
        try:
            a = addr.parse(text)
        except addr.AddressError as exc:
            self.notify(f"Not an address: {exc}", severity="error", timeout=6)
            return
        if a.kind == "identity":
            if await self.push_screen_wait(ConfirmDialog(
                    "A person", f"An identity: {a.fingerprint}. Follow them? Their feeds come "
                    "into your Timeline; nobody is told.", "Follow")):
                try:
                    await s.follow_person(a.fingerprint)
                except (SessionError, OSError, ConnectionError, ValueError) as exc:
                    self.notify(f"Not followed: {exc}", severity="error", timeout=8)
                    return
                self.notify("Following", timeout=4)
                self.refresh_groups()
            return
        try:
            m = await s.fetch_manifest(a.group_id)
        except (SessionError, OSError, ConnectionError, NNTPError) as exc:
            self.notify(f"Could not look: {exc}", severity="error", timeout=8)
            return
        if m is None:
            self.notify(f"{a.group_id} is not on your nodes", severity="warning", timeout=8)
            return
        if a.group_id not in s.data["subscribed"]:
            title = m.raw.get("title") or m.name
            if not await self.push_screen_wait(ConfirmDialog(
                    "Follow this group?", f"{s.group_label(a.group_id)} - {title}. Follow it to "
                    "read it here?", "Follow")):
                return
            s.subscribe(a.group_id)
            self.refresh_groups()
        self._open_at = a.message_id if a.kind == "post" else (("page", a.path) if a.kind == "page"
                                                             else None)
        self.select_group(a.group_id)

    @work(group="dialog", exit_on_error=False)
    async def action_tor(self) -> None:
        """Which Tor: our own, or one already running (Tails, Whonix, a system tor)."""
        s = self.session
        existing = s.tor_in_use()
        now = (f"Now: the Tor at {existing} ({existing.source})." if existing
               else "Now: Unseenet starts its own Tor.")
        value = await self.push_screen_wait(InputDialog(
            "Which Tor",
            "auto - use Tails' Tor on Tails, start our own everywhere else (the default)\n"
            "own - always start our own Tor\n"
            "127.0.0.1:9050 - the SOCKS port of a Tor that is already running, "
            "e.g. a system tor or Whonix\n\n" + now,
            "auto, own or 127.0.0.1:9050", s.tor_setting()))
        if value is None:
            return
        try:
            await s.set_tor(value)
        except SessionError as exc:
            self.notify(str(exc), severity="error")
            return
        existing = s.tor_in_use()
        self.notify("Saved - " + (f"using the Tor at {existing}" if existing
                                  else "Unseenet starts its own Tor"))
        self._watching.clear()
        self.refresh_groups()
        self._start_watches()

    @work(group="dialog", exit_on_error=False)
    async def action_change_passphrase(self) -> None:
        """A new passphrase for this vault file; copies elsewhere keep the old one."""
        error = ""
        while True:
            result = await self.push_screen_wait(ChangePassphraseDialog(self.vault_path, error))
            if result is None:
                return
            old, new = result
            self.notify("Encrypting the vault with the new passphrase ...")
            try:
                await asyncio.to_thread(self.session.change_passphrase, old, new)
            except VaultError as exc:
                error = ("The current passphrase is wrong" if "wrong passphrase" in str(exc)
                         else str(exc))
                continue
            self.notify("Passphrase changed. Copies of the vault elsewhere still open with "
                        "the old one - replace or delete them.", timeout=12)
            return
