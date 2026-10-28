"""UnseenetApp's feeds and following (UN-RFC 0034).

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
from .helpers import REFRESH_SECONDS, REPO_URL, DOCS_URL, ACROSS, FEED_MARK, START_FEED, START_SITE, CHATS, CHATS_SHOWN, SITE_MARK, REPLY_MARK, MENTION_MARK, for_you_marks, for_you_news, sess_default_pow, group_name_problem, GROUP_KINDS, known_node_details, node_details, ICONS, _ONION, PIN_MARK, ICON_LEGEND, ORIGIN_MARK, NETWORK_NAMES, network_name, origin_text, edited_when, group_icons, is_personal_inbox, by_hierarchy, CSS  # noqa: F401
from .dialogs.base import Dialog, InputDialog, InfoDialog, ConfirmDialog, KeyListDialog  # noqa: F401
from .dialogs.setup import UnlockDialog, ChangePassphraseDialog, OTHER_NODE, SetupDialog, BridgesDialog  # noqa: F401
from .dialogs.compose import ComposeDialog, PageDialog, PreviewDialog, SignatureDialog, PinDialog, ReactDialog, reactions_text  # noqa: F401
from .dialogs.people import ContactsDialog, ChatsDialog, NamesDialog, PROFILE_WARNING, identicon_text, MARKDOWN_HINT, manifest_text, profiles_clean, writes_markdown, ProfileDialog, EditProfileDialog  # noqa: F401
from .dialogs.groups import DELIVERY_CHOICES, DELIVERY, SITE_CHOICE, KINDS_CHOICES, KINDS, REACTIONS_CHOICES, reactions_choice, COMMENTS_CHOICES, FILES_CHOICES, FILES_GROUP_TYPES, reactions_label, comments_label, GROUP_NAME_HELP, CreateGroupDialog, GroupInfoDialog, EditGroupDialog, BrowseTree, NO_SELECT_ON_FOCUS, BrowseDialog  # noqa: F401
from .dialogs.moderation import ReportDialog, ModerationDialog  # noqa: F401
from .dialogs.nodes import NodesDialog, AddNodeDialog, NodeInfoDialog  # noqa: F401
from .screens import ChatBox, attachments_text, save_name, ReadScreen, AboutDialog  # noqa: F401


class FeedsMixin:

    # --- feeds and following (UN-RFC 0034) ---
    @work(group="dialog", exit_on_error=False)
    async def action_follow(self) -> None:
        """Follow a person: their feeds come into your timeline. Nobody is told."""
        s = self.session
        m = self.current_message
        mine = s.identity().key_text
        default = m.author_key if m and m.author_key and m.author_key != mine and not m.private else ""
        target = await self.push_screen_wait(InputDialog(
            "Follow", "A key, a fingerprint, an unseenet: address or a name such as "
            "andreas@lorensen. Their feeds come into your Timeline; nobody is told, and "
            "nobody can see whom you follow.", "ed25519:... or a name", default))
        if not target:
            return
        await self._follow(target)

    async def _follow(self, target: str) -> None:
        """Follow a person given by key, name or address, and say what came of it."""
        s = self.session
        self.set_status("Following ...")
        try:
            got = await s.follow_person(target)
            for feed in got.get("feeds", []):
                await s.fetch_group(feed)
        except (SessionError, OSError, ConnectionError, ValueError) as exc:
            self.notify(f"Not followed: {exc}", severity="error", timeout=8)
            self.set_status("")
            return
        who = s.label_of(got["key"]) if got.get("key") else target
        feeds = got.get("feeds", [])
        self.notify(f"Following {who}" + ("" if feeds else " - no feed yet; it comes in when they "
                                           "start one"), timeout=6)
        self.set_status("")
        self.refresh_groups()

    @work(group="dialog", exit_on_error=False)
    async def action_following(self) -> None:
        """The people you follow; stop following one."""
        s = self.session
        people = [(p.get("key") or p["fingerprint"], p["label"]) for p in s.following()]
        if not people:
            self.notify("You follow nobody yet - Follow in Ctrl+P, on a post's author", timeout=6)
            return
        target = await self.push_screen_wait(KeyListDialog(
            "Following", "Their feeds are in your Timeline. Only you know whom you follow.",
            people, "Stop following"))
        if not target:
            return
        try:
            await s.unfollow_person(target)
        except (SessionError, ValueError) as exc:
            self.notify(f"Not changed: {exc}", severity="error", timeout=8)
            return
        self.notify("Not following any more", timeout=4)
        self.refresh_groups()

    @work(group="dialog", exit_on_error=False)
    async def action_start_feed(self) -> None:
        """Start your own feed (UN-RFC 0034 section 5), after saying it is public."""
        s = self.session
        gid = s.personal_group("feed")
        if s.has_personal_group("feed"):
            self.select_group(gid)
            return
        if not await self.push_screen_wait(ConfirmDialog(
                "Start your feed", s.personal_group_warning("feed"), "Start your feed")):
            return
        self.set_status("Starting your feed ...")
        try:
            gid = await s.start_personal_group("feed")
        except (SessionError, OSError, ConnectionError) as exc:
            self.notify(f"Not started: {exc}", severity="error", timeout=8)
            self.set_status("")
            return
        self.set_status("")
        self.notify("Your feed is started: n writes in it, and anyone can comment", timeout=8)
        self.refresh_groups()
        self.select_group(gid)

    @work(group="dialog", exit_on_error=False)
    async def action_start_site(self) -> None:
        """Start your own site (UN-RFC 0035 section 7), after saying it is public - or go to it."""
        s = self.session
        gid = s.personal_group("site")
        if s.has_personal_group("site"):
            self.select_group(gid)
            return
        if not await self.push_screen_wait(ConfirmDialog(
                "Start your site", s.personal_group_warning("site"), "Start your site")):
            return
        self.set_status("Starting your site ...")
        try:
            gid = await s.start_personal_group("site")
        except (SessionError, OSError, ConnectionError) as exc:
            self.notify(f"Not started: {exc}", severity="error", timeout=8)
            self.set_status("")
            return
        self.set_status("")
        self.notify("Your site is started: n writes its first page - index is the home page",
                    timeout=8)
        self.refresh_groups()
        self.select_group(gid)

    @work(group="dialog", exit_on_error=False)
    async def action_state_where(self) -> None:
        """Keep your state in the network (the default) or on this device only
        (UN-RFC 0044 section 7.4)."""
        s = self.session
        if s is None:
            return
        local = s.state_kept_local()
        text = ("Your groups, the people you follow, contacts, settings and what you have read "
                "are kept in the network, encrypted so only your key can read them - so a new "
                "stick or phone with your key looks the same after one fetch. Nodes see only a "
                "block of fixed size.\n\n")
        text += ("Now they are kept on this device only. Keep them in the network again?" if local else
                 "Keep them on this device only instead, for an identity that should leave no "
                 "trace of activity at all - not even an encrypted one? What is in the network "
                 "now is removed.")
        if not await self.push_screen_wait(ConfirmDialog(
                "Your state", text, "Keep in the network" if local else "Keep on this device only",
                danger=not local)):
            return
        try:
            await s.set_state_kept_local(not local)
        except (SessionError, OSError, ConnectionError) as exc:
            self.notify(f"Not changed: {exc}", severity="error", timeout=8)
            return
        self.set_status("Your state is kept on this device only." if not local else
                        "Your state is kept in the network again, from the next sync.")

    @work(group="dialog", exit_on_error=False)
    async def action_ripening(self) -> None:
        """Pause or resume ripening your key, with what it is (#358) - as the GUI has it."""
        s, rt = self.session, self.runtime
        if s is None or rt is None:
            return
        lv = s.ripening().level
        paused = rt.ripening_paused
        text = (f"Your key is at level {'-' if lv is None else lv}. Ripening is a calculation "
                "your client works through step by step while Unseenet is open, on one core. "
                "It shows that a key is not brand new, so it cannot be thrown away after every "
                "post - and the riper your key, the less postage each post costs. Level 3 ends "
                "the extra postage.\n\n")
        if s.ripening_limit is not None:
            text += (f"This machine can ripen this key to level {s.ripening_limit}: more needs "
                     "more memory than it has free.\n\n")
        text += ("Ripening is paused until you resume it, also after a restart; it goes on "
                 "where it was." if paused else
                 "Pause it on battery or on a warm laptop - it stays paused, also after a "
                 "restart, until you resume it here, and goes on where it was.")
        if not await self.push_screen_wait(ConfirmDialog(
                "Key ripening", text, "Resume ripening" if paused else "Pause ripening")):
            return
        if paused:
            await rt.resume_ripening()
            self.ripen_on()
            self.set_status("Ripening your key again.")
        else:
            self.workers.cancel_group(self, "ripening")       # our own worker, as well
            await rt.pause_ripening()
            self.set_status("Ripening paused until you resume it.")
        self._update_subtitle()
