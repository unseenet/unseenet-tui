"""UnseenetApp's the keys' actions: new post, reply, read, mark, refresh.

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
from .helpers import REFRESH_SECONDS, REPO_URL, DOCS_URL, ACROSS, FEED_MARK, START_FEED, START_SITE, CHATS, CHATS_SHOWN, FOLDED, FEED_SHOWN, COMMENT_PAGE, CLIP_LINES, SITE_MARK, REPLY_MARK, MENTION_MARK, for_you_marks, for_you_news, sess_default_pow, group_name_problem, GROUP_KINDS, known_node_details, node_details, ICONS, _ONION, PIN_MARK, ICON_LEGEND, ORIGIN_MARK, NETWORK_NAMES, network_name, origin_text, edited_when, group_icons, is_personal_inbox, by_hierarchy, CSS  # noqa: F401
from .dialogs.base import Dialog, InputDialog, InfoDialog, ConfirmDialog, KeyListDialog  # noqa: F401
from .dialogs.setup import UnlockDialog, ChangePassphraseDialog, OTHER_NODE, SetupDialog, BridgesDialog  # noqa: F401
from .dialogs.compose import ComposeDialog, PageDialog, PreviewDialog, SignatureDialog, PinDialog, ReactDialog, reactions_text  # noqa: F401
from .dialogs.people import ContactsDialog, ChatsDialog, NamesDialog, PROFILE_WARNING, identicon_text, MARKDOWN_HINT, manifest_text, profiles_clean, writes_markdown, ProfileDialog, EditProfileDialog  # noqa: F401
from .dialogs.groups import DELIVERY_CHOICES, DELIVERY, SITE_CHOICE, KINDS_CHOICES, KINDS, REACTIONS_CHOICES, reactions_choice, COMMENTS_CHOICES, FILES_CHOICES, FILES_GROUP_TYPES, reactions_label, comments_label, GROUP_NAME_HELP, CreateGroupDialog, GroupInfoDialog, EditGroupDialog, BrowseTree, NO_SELECT_ON_FOCUS, BrowseDialog  # noqa: F401
from .dialogs.moderation import ReportDialog, ModerationDialog  # noqa: F401
from .dialogs.nodes import NodesDialog, AddNodeDialog, NodeInfoDialog  # noqa: F401
from .screens import ChatBox, attachments_text, save_name, ReadScreen, AboutDialog  # noqa: F401


class ActionsMixin:

    # --- actions ---
    def _need_group(self) -> GroupInfo | None:
        if self.current_group in ACROSS and self.current_message \
                and self.current_message.group in self.groups:
            return self.groups[self.current_message.group]
        if not self.current_group or self.current_group not in self.groups:
            self.notify("Select a group first", severity="warning")
            return None
        return self.groups[self.current_group]

    def _current_contact(self) -> str | None:
        gid = self.current_group or ""
        return gid[len(CONTACT):] if gid.startswith(CONTACT) else None

    def _write_to_contact(self, key: str, subject: str = "", quote: str = "",
                          references: list[str] | None = None) -> None:
        name = self.session.contact_name(key) or key[:20]
        owner = crypto.key_from_text(key)
        self.compose_private(personal_inbox_id(owner), f"Message to {name}", subject, quote,
                             owner_key=owner, references=references)

    def action_new_post(self) -> None:
        """n: a new post here - a thread, or in a chat a private post to that person."""
        contact = self._current_contact()
        if contact:
            self._write_to_contact(contact)
            return
        g = self._need_group()
        if not g:
            return
        if self._is_site(g.group_id):              # a site: n writes a new page (0035, 9.2)
            if not self._writes_pages(g.group_id):
                self.notify("Only the site's writers write its pages", severity="warning")
                return
            self.compose_page(g)
            return
        p = self.session.write_permission(g.group_id, kind=KIND_POST)
        if not p:
            self.notify(p.reason, severity="warning")
            return
        self.compose_post(g)

    def action_reply(self) -> None:
        """r: reply to the selected post, or in a chat to the person privately."""
        contact = self._current_contact()
        m = self.current_message
        if contact:
            if not m:
                self._write_to_contact(contact)
                return
            # a post in answer to a chat line starts without a subject; you give it one
            subject = ("" if not m.subject else m.subject if m.subject.lower().startswith("re:")
                       else f"Re: {m.subject}")
            self._write_to_contact(contact, subject, references=[*m.references, m.message_id])
            return
        g = self._need_group()
        if not g or not m or not m.readable:
            self.notify("Select a post to reply to", severity="warning")
            return
        subject = m.subject if m.subject.lower().startswith("re:") else f"Re: {m.subject}"
        text = split_signature(m.body)[0]          # never quote the signature
        quote = f"{m.from_name} wrote:\n" + "".join(f"> {ln}\n" for ln in text.rstrip().split("\n")) + "\n"
        if m.private and m.kind == KIND_CHAT:    # a chat among the posts: answer in the chat
            key = m.to_key or m.sender_key
            if key:
                self.select_group(CONTACT + key)
                return
        if m.private and m.to_key:               # one you sent: write to them again
            self.compose_private(m.reply_inbox or personal_inbox_id(crypto.key_from_text(m.to_key)),
                                 f"Private message to {self._key_label(m.to_key)}", subject, quote,
                                 owner_key=crypto.key_from_text(m.to_key),
                                 references=[*m.references, m.message_id])
            return
        if not m.private:
            p = self.session.write_permission(m.group, reply_to=m, kind=KIND_POST)
            if not p:
                self.notify(p.reason, severity="warning")
                return
        if m.private:
            target = self.session.reply_target(m)
            if not target:
                self.notify("Anonymous message - there is nobody to reply to", severity="warning")
                return
            key = crypto.key_from_text(m.sender_key) if m.sender_key and not m.reply_inbox else None
            team = self.team_of(self.current_group)       # a team inbox: answer as the team (0047)
            if team:
                self.compose_private(target, f"Reply as {team['name']}", subject, quote,
                                     owner_key=key, references=[*m.references, m.message_id],
                                     as_team=team["group"])
                return
            self.compose_private(target, f"Private reply to {m.from_name}", subject, quote,
                                 owner_key=key, references=[*m.references, m.message_id])
            return
        self.compose_post(g, subject, quote, references=[*m.references, m.message_id])

    @work(group="post", exit_on_error=False)
    async def compose_private(self, target: str, heading: str, subject: str = "", quote: str = "",
                              owner_key: bytes | None = None,
                              references: list[str] | None = None, as_team: str | None = None) -> None:
        """The compose window for a private post or reply to an inbox, then send it sealed
        (UN-RFC 0009). ``as_team``: on a team inbox's behalf, without your signature,
        which would say who wrote it (UN-RFC 0047 section 8)."""
        g = GroupInfo(target, type="inbox")
        dlg = ComposeDialog(
            g, subject, quote, bool(references), heading=heading + "  @ only they can read it",
            signature="" if as_team else self.session.signature(),
            files_allowed=await self._inbox_takes_files(target))
        result = await self.push_screen_wait(dlg)
        if not result:
            return
        subject, body, _ = result
        self.set_status("Sending (computing postage) ...")
        try:
            files = (self.session.prepare_files([Path(p).expanduser() for p in dlg.files])
                     if dlg.files else None)          # encrypted with the post (#497)
            await self.session.send_private(target, subject, body, owner_key=owner_key,
                                            references=references, files=files, as_team=as_team)
        except (SessionError, OSError, ConnectionError, ValueError) as exc:
            self.notify(f"Not sent: {exc}", severity="error", timeout=8)
            return
        self.notify("Private message sent")
        self.set_status("")
        if self._current_contact():
            self._render_threads()

    @work(group="dialog", exit_on_error=False)
    async def action_message(self) -> None:
        """m: a private message to the author of the selected post."""
        m = self.current_message
        known = (m.sender_key or m.author_key) if m and m.readable else ""
        if m and m.private and not m.sender_key:
            known = ""
        contacts = self.session.contacts()
        names = ", ".join(n for _, n in contacts[:6]) + (", ..." if len(contacts) > 6 else "")
        typed = await self.push_screen_wait(InputDialog(
            "Write a private post",
            ("Type a contact's name" + (f" ({names})" if names else "") + ", or paste their key "
             "(they find it with k). Only they can read it. c: your contacts. "
             "To chat instead: Ctrl+P, Chat with someone."),
            "a name or ed25519:...", value=self.session.contact_name(known) or known))
        key = self._recipient(typed)
        if key:
            self._write_to_contact(key)

    def _recipient(self, typed: str | None) -> str | None:
        """A key from what was typed: a key, or (the start of) a contact's name."""
        typed = (typed or "").strip()
        if not typed:
            return None
        if typed.startswith("ed25519:"):
            try:
                crypto.key_from_text(typed)
                return typed
            except crypto.CryptoError:
                self.notify("That is not a valid key", severity="error")
                return None
        low = typed.lower()
        contacts = self.session.contacts()
        exact = [k for k, n in contacts if n.lower() == low]
        found = exact or [k for k, n in contacts if n.lower().startswith(low)]
        if len(found) == 1:
            return found[0]
        self.notify("No contact by that name" if not found else
                    "Several contacts start like that: " +
                    ", ".join(n for k, n in contacts if k in found), severity="warning")
        return None

    @work(group="dialog", exit_on_error=False)
    async def action_contacts(self) -> None:
        """c: your address book - chat, write a post, rename or remove."""
        result = await self.push_screen_wait(ContactsDialog(self.session.contacts(),
                                                            find=self.session.contacts))
        if not result:
            return
        what, key = result
        if what == "chat":
            self.select_group(CONTACT + key)
        elif what == "post":
            self._write_to_contact(key)
        elif what == "rename":
            name = await self.push_screen_wait(InputDialog(
                "Rename contact", "", "Name", value=self.session.contact_name(key) or ""))
            if name:
                self.session.add_contact(key, name)
                await self._render_groups()
                self._render_title()
        elif what == "remove":
            name = self.session.contact_name(key) or key[:20]
            if await self.push_screen_wait(ConfirmDialog(
                    f"Remove {name}?", "The contact is forgotten. Your chat and posts stay.",
                    "Remove", danger=True)):
                self.session.remove_contact(key)
                await self._render_groups()

    @work(group="dialog", exit_on_error=False)
    async def action_chats(self) -> None:
        """Private chats (#39): the list of every chat, unread first, closed last."""
        s = self.session
        partners = s.chat_partners()
        chats = [(k, self._chat_label(k), s.unread_from(k, KIND_CHAT), False) for k in partners]
        chats.sort(key=lambda c: c[2] == 0)                  # unread first, else as they were
        chats += [(k, "• " + name, 0, True) for k, name in s.contacts()
                  if k not in partners and s.chat_closed(k)]
        result = await self.push_screen_wait(ChatsDialog(chats))
        if not result:
            return
        what, key = result
        if what == "chat":
            self.select_group(CONTACT + key)
        elif what == "contacts":
            self.action_contacts()
        elif what == "new":
            self.action_chat_with()

    @work(group="dialog", exit_on_error=False)
    async def action_chat_with(self) -> None:
        """Open a chat with a key (UN-RFC 0028): a contact's, or anyone's."""
        m = self.current_message
        known = (m.sender_key or m.author_key) if m and m.readable else ""
        if m and m.private and not m.sender_key:
            known = m.to_key or ""
        key = await self.push_screen_wait(InputDialog(
            "Chat with someone", "Paste their key (they find it with k). Only the two of you "
            "can read the chat.", "ed25519:...", value=known))
        if not key:
            return
        try:
            crypto.key_from_text(key)
        except crypto.CryptoError:
            self.notify("That is not a valid key", severity="error")
            return
        if key == self.session.identity().key_text:
            self.notify("That is your own key", severity="warning")
            return
        self.select_group(CONTACT + key)

    @work(group="dialog", exit_on_error=False)
    async def action_bridges(self) -> None:
        """Tor bridges, for where Tor is blocked: edit them and restart Tor with them."""
        existing = self.session.tor_in_use()
        if existing is not None:
            self.notify(f"Unseenet uses the Tor at {existing} ({existing.source}). Bridges are "
                        "set up in that Tor, not here.", severity="warning", timeout=8)
            return
        result = await self.push_screen_wait(BridgesDialog(self.session.bridge_setting()))
        if not result:
            return
        await self.session.set_bridges(result["mode"], result["custom"])
        self.notify("Saved - Tor reconnects " +
                    ("directly" if result["mode"] == "off" else f"with {result['mode']} bridges"))
        self._watching.clear()
        self.refresh_groups()
        self._start_watches()
