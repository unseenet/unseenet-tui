"""UnseenetApp's names, profiles, moderation and more (UN-RFC 0019 5.4).

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
from .helpers import REFRESH_SECONDS, REPO_URL, DOCS_URL, ACROSS, FEED_MARK, START_FEED, START_SITE, SITE_MARK, REPLY_MARK, MENTION_MARK, for_you_marks, for_you_news, sess_default_pow, group_name_problem, GROUP_KINDS, known_node_details, node_details, ICONS, _ONION, PIN_MARK, ICON_LEGEND, ORIGIN_MARK, NETWORK_NAMES, network_name, origin_text, edited_when, group_icons, is_personal_inbox, by_hierarchy, CSS  # noqa: F401
from .dialogs.base import Dialog, InputDialog, InfoDialog, ConfirmDialog, KeyListDialog  # noqa: F401
from .dialogs.setup import UnlockDialog, ChangePassphraseDialog, OTHER_NODE, SetupDialog, BridgesDialog  # noqa: F401
from .dialogs.compose import ComposeDialog, PageDialog, PreviewDialog, SignatureDialog, PinDialog, ReactDialog, reactions_text  # noqa: F401
from .dialogs.people import ContactsDialog, NamesDialog, PROFILE_WARNING, identicon_text, MARKDOWN_HINT, manifest_text, profiles_clean, writes_markdown, ProfileDialog, EditProfileDialog  # noqa: F401
from .dialogs.groups import DELIVERY_CHOICES, DELIVERY, SITE_CHOICE, KINDS_CHOICES, KINDS, REACTIONS_CHOICES, reactions_choice, COMMENTS_CHOICES, FILES_CHOICES, FILES_GROUP_TYPES, reactions_label, comments_label, GROUP_NAME_HELP, CreateGroupDialog, GroupInfoDialog, EditGroupDialog, BrowseTree, NO_SELECT_ON_FOCUS, BrowseDialog  # noqa: F401
from .dialogs.moderation import ReportDialog, ModerationDialog  # noqa: F401
from .dialogs.nodes import NodesDialog, AddNodeDialog, NodeInfoDialog  # noqa: F401
from .screens import ChatBox, attachments_text, save_name, ReadScreen, AboutDialog  # noqa: F401


class NamesMixin:

    # --- names under a name (UN-RFC 0019 section 5.4) ---
    @work(group="dialog", exit_on_error=False)
    async def action_ask_subname(self) -> None:
        """Ask the holder of a name for a name under it (UN-RFC 0019 5.4)."""
        s = self.session
        name = await self.push_screen_wait(InputDialog(
            "Ask for a name under someone's name",
            "Type the name you would like, e.g. sonia@lorensen. Its holder decides; the name "
            "then shows on your posts. Every name on one identity links it to the others - "
            "use another identity for a part of your life you keep apart.",
            "sonia@lorensen"))
        if not name:
            return
        try:
            await naming.request_subname(s, name.strip().lower())
        except (SessionError, OSError, ConnectionError, nm.NameProblem) as exc:
            self.notify(f"Not sent: {exc}", severity="error", timeout=8)
            return
        self.notify(f"Asked for {name}. The answer comes to your private messages.", timeout=8)

    @work(group="dialog", exit_on_error=False)
    async def action_give_subname(self) -> None:
        """Give a name under one of yours to a key."""
        s = self.session
        parents = naming.parents_i_hold(s)
        if not parents:
            self.notify("You need a name of your own first, e.g. @lorensen (press @)",
                        severity="warning", timeout=8)
            return
        m = self.current_message
        key = m.author_key if m and not m.private and m.author_key else s.identity().key_text
        name = await self.push_screen_wait(InputDialog(
            "Give a name under yours", f"Under {', '.join('@' + p for p in parents)}. You can "
            "give yourself one too, e.g. andreas@" + parents[0] + ".", f"sonia@{parents[0]}",
            value=f"@{parents[0]}"))
        if not name:
            return
        key = await self.push_screen_wait(InputDialog(
            f"Whose is {name}?", "Their key - filled in from the selected post, or yours.",
            "ed25519:...", value=key))
        if not key:
            return
        years = await self.push_screen_wait(InputDialog(
            "For how many years?", "1 to 5. Your client renews it when it is about to run out; "
            "you can take it back at any time.", "1", value="1"))
        if not years:
            return
        try:
            n = max(1, min(5, int(years)))
            await naming.give_subname(s, name.strip().lower(), key.strip(), timedelta(days=365 * n))
        except (SessionError, OSError, ConnectionError, nm.NameProblem, ValueError,
                crypto.CryptoError) as exc:
            self.notify(f"Not given: {exc}", severity="error", timeout=8)
            return
        self.notify(f"{name} given to {s.identity_label(key.strip())}")

    @work(group="dialog", exit_on_error=False)
    async def action_name_requests(self) -> None:
        """The requests for names under yours, to give or refuse."""
        s = self.session
        try:
            await s.fetch_group(s.my_inbox)
        except (SessionError, OSError, ConnectionError):
            pass
        reqs = naming.name_requests(s)
        if not reqs:
            self.notify("Nobody is asking for a name under yours")
            return
        items = [(m.message_id, f"{d['name']} - {s.identity_label(d['key'], m.from_name)}",
                  f"{d['name']}\nfor {s.identity_label(d['key'], m.from_name)}\n{d['key']}")
                 for m, d in reqs]
        result = await self.push_screen_wait(ModerationDialog(
            items, "Name requests", "People asking for a name under yours. Giving one makes it "
            "show on their posts for a year; you can take it back.", "Choose a request",
            "Refuse", "Give"))
        if not result:
            return
        action, which = result
        msg, data = next((m, d) for m, d in reqs if m.message_id == which)
        try:
            await naming.answer_request(s, msg, data, approve=action == "approve")
        except (SessionError, OSError, ConnectionError, nm.NameProblem) as exc:
            self.notify(f"Not done: {exc}", severity="error", timeout=8)
            return
        self.notify(f"{data['name']} given" if action == "approve" else "Refused")

    @work(group="dialog", exit_on_error=False)
    async def action_names_given(self) -> None:
        """The names you gave under yours, to take back."""
        s = self.session
        given = naming.names_given(s)
        if not given:
            self.notify("You have not given any names")
            return
        people = [(c.name, f"{c.name} - {s.identity_label(c.key)}, until "
                   f"{c.expires.astimezone():%d %B %Y}") for c in given]
        name = await self.push_screen_wait(KeyListDialog(
            "Names you gave", "Taking a name back is public: it goes in your list of revoked "
            "names, and clients stop showing it.", people, "Take back"))
        if not name:
            return
        if not await self.push_screen_wait(ConfirmDialog(
                f"Take back {name}?", "It stops showing on their posts.", "Take back",
                danger=True)):
            return
        try:
            await naming.revoke_subname(s, name)
        except (SessionError, OSError, ConnectionError) as exc:
            self.notify(f"Not done: {exc}", severity="error", timeout=8)
            return
        self.notify(f"{name} taken back")

    @work(group="dialog", exit_on_error=False)
    async def action_primary_name(self) -> None:
        """Choose which of your names is shown first."""
        s = self.session
        mine = naming.my_names(s)
        if len(mine) < 2:
            self.notify("You have " + ("one name" if mine else "no names") + "; nothing to choose")
            return
        name = await self.push_screen_wait(KeyListDialog(
            "Name your posts show", "Readers see this one on your posts.",
            [(c.name, naming.name_text(c.name)) for c in mine], "Use"))
        if name:
            naming.set_primary(s, name)
            self.notify(f"Your posts show {naming.name_text(name)}")

    @work(group="dialog", exit_on_error=False)
    async def action_report(self) -> None:
        """Report the selected post to the group's moderators."""
        m, s = self.current_message, self.session
        gid = self.current_group
        if gid and self.view_of(gid) == "chat" and not gid.startswith(CONTACT):   # no line selected
            from .dialogs.reports import ChooseLineDialog
            me = s.identity().key_text
            lines = [(x.message_id, f"{self._who(x)}: {(x.body or '').strip().splitlines()[0][:60] if x.body.strip() else '…'}",
                      x.body.strip()) for x, _ in sorted(s.threads(gid), key=lambda r: (r[0].date is None, r[0].date),
                                                        reverse=True)
                     if x.readable and not x.private and x.author_key != me][:40]
            if not lines:
                self.notify("Nothing here by others to report", severity="warning")
                return
            mid = await self.push_screen_wait(ChooseLineDialog(lines))
            if not mid:
                return
            m = next(x for x, _ in s.threads(gid) if x.message_id == mid)
        if m is None or m.private or not m.readable:
            self.notify("Select the post you want to report", severity="warning")
            return
        self.set_status("Finding who can act on it ...")
        targets = await s.report_targets(m)
        self.set_status("")
        if not targets:
            self.notify("Nobody to report this to: the group has no owner, moderator, "
                        "maintainer or node operator you can reach", severity="warning", timeout=8)
            return
        from unseenet.client.session import REPORT_REASONS
        who = s.identity_label(m.author_key, m.from_name) if m.author_key else "anonymous"
        first = (m.body or "").strip().split("\n", 1)[0][:100] if m.readable else ""
        post = f"{who}: {m.subject or '(no subject)'}" + (f"\n{first}" if first else "")
        result = await self.push_screen_wait(ReportDialog(post, targets, REPORT_REASONS))
        if not result:
            return
        chosen, note, anonymous, reason, paths = result
        self.set_status("Sending the report ...")
        try:
            from pathlib import Path
            files = s.prepare_files([Path(p).expanduser() for p in paths]) if paths else None
            n = await s.report(m, chosen, note, anonymous, files=files, reason=reason)
        except (SessionError, OSError, ConnectionError, ValueError) as exc:
            self.set_status("")
            self.notify(f"Not sent: {exc}", severity="error", timeout=8)
            return
        self.set_status("")
        self.notify(f"Reported to {n} " + ("person" if n == 1 else "people"))

    async def action_open_reported(self) -> None:
        """Open the post a report names, for moderators."""
        m, s = self.current_message, self.session
        found = s.reported_post(m) if m else None
        if not found:
            self.notify("Select a report in your private messages first", severity="warning")
            return
        gid, msgid = found
        if gid not in self.groups:
            self.notify(f"You do not follow {s.group_label(gid)}; subscribe to it with g, "
                        f"then look for {msgid}", severity="warning", timeout=10)
            return
        self.select_group(gid)
        for _ in range(100):
            await asyncio.sleep(0.05)
            if msgid in self.messages:
                break
        if msgid in self.messages:
            self.current_message = self.messages[msgid]
            self.notify("This is the reported post: withdraw it, ban or block its author "
                        "from the command palette", timeout=10)
        else:
            self.notify("The post is not there any more - perhaps it was withdrawn", timeout=8)

    @work(group="dialog", exit_on_error=False)
    async def action_moderation(self) -> None:
        """Approve or reject submissions (UN-RFC 0009 section 3)."""
        s = self.session
        try:
            await s.fetch_group(s.my_inbox)
        except (SessionError, OSError, ConnectionError):
            pass                                  # show what we have
        queue = s.submissions()
        if not queue:
            self.notify("Nothing waiting for approval")
            return
        items = []
        for m, art in queue:
            who = s.identity_label(art["Author-Key"], art.get("From", ""))
            items.append((m.message_id, f"{s.group_label(art.group)}: {art.get('Subject', '')} - {who}",
                          f"From: {who}\nGroup: {s.group_label(art.group)}\n"
                          f"Subject: {art.get('Subject', '')}\n\n{art.body}"))
        result = await self.push_screen_wait(ModerationDialog(items))
        if not result:
            return
        action, which = result
        msg, art = next((m, a) for m, a in queue if m.message_id == which)
        try:
            if action == "approve":
                await s.approve_submission(msg, art)
                self.notify("Approved and posted")
            else:
                if not await self.push_screen_wait(ConfirmDialog(
                        "Reject this post?", "It is not posted, and the queue forgets it. "
                        "The author is not told unless you write to them.", "Reject")):
                    return
                await s.reject_submission(msg)
                self.notify("Rejected")
        except (SessionError, OSError, ConnectionError) as exc:
            self.notify(f"Not done: {exc}", severity="error", timeout=8)
            return
        if self.current_group == art.group:
            self.select_group(art.group)

    def _owned_group(self, kind: str | None = None) -> str | None:
        s, gid = self.session, self.current_group
        if not gid or not s.group_entry(gid).get("owner"):
            self.notify("Only the group's owner can do this", severity="warning")
            return None
        if kind and s.group_entry(gid).get("type") != kind:
            self.notify(f"This is only for {kind} groups", severity="warning")
            return None
        return gid

    @work(group="dialog", exit_on_error=False)
    async def action_add_role(self, role: str) -> None:
        """Owner: add a moderator or (in a channel) a poster (UN-RFC 0004)."""
        gid = self._owned_group("channel" if role == "posters" else None)
        if not gid:
            return
        s, m = self.session, self.current_message
        mine = s.identity().key_text
        key = m.author_key if m and not m.private and m.author_key and m.author_key != mine else ""
        what = "moderator" if role == "moderators" else "poster"
        key = await self.push_screen_wait(InputDialog(
            f"Make a {what} of {s.group_label(gid)}",
            ("Moderators can post directly, approve others' posts and withdraw any post. "
             if role == "moderators" else "Posters can post in this channel. ")
            + "Their key - filled in from the selected post:", "ed25519:...", value=key))
        if not key:
            return
        try:
            await s.set_role(gid, role, key.strip(), True)
        except (SessionError, OSError, ConnectionError, crypto.CryptoError) as exc:
            self.notify(f"Not changed: {exc}", severity="error", timeout=8)
            return
        self.notify(f"{s.identity_label(key.strip())} is now a {what}")

    @work(group="dialog", exit_on_error=False)
    async def action_roles(self) -> None:
        """The moderators and posters of a group you own."""
        gid = self._owned_group()
        if not gid:
            return
        s = self.session
        data = s.manifest_data(gid)
        people = [(k, f"{s.identity_label(k)} (moderator)") for k in data.get("moderators", [])]
        people += [(k, f"{s.identity_label(k)} (poster)") for k in data.get("posters", [])]
        if not people:
            self.notify("This group has no moderators or posters. Ctrl+P: Make moderator.")
            return
        key = await self.push_screen_wait(KeyListDialog(
            f"Moderators and posters of {s.group_label(gid)}",
            "Removing someone takes their role away from now on.", people, "Remove"))
        if not key:
            return
        role = "moderators" if key in data.get("moderators", []) else "posters"
        try:
            await s.set_role(gid, role, key, False)
        except (SessionError, OSError, ConnectionError) as exc:
            self.notify(f"Not changed: {exc}", severity="error", timeout=8)
            return
        self.notify("Removed")

    @work(group="dialog", exit_on_error=False)
    async def action_members(self) -> None:
        """The members of a closed group you own: add, remove (a new key for the others)."""
        gid = self._owned_group("closed")
        if not gid:
            return
        s = self.session
        people = [(k, s.identity_label(k)) for k in s.members(gid)]
        if not people:
            self.notify("No members recorded here. Members you invited from another device, "
                        "or before version 0.3.0, are not listed.", timeout=10)
            return
        key = await self.push_screen_wait(KeyListDialog(
            f"Members of {s.group_label(gid)}",
            "The people you invited from this client. Removing one gives everyone else a new "
            "key; members you invited elsewhere are not listed and lose access too, so invite "
            "them again.", people, "Remove member"))
        if not key:
            return
        if not await self.push_screen_wait(ConfirmDialog(
                f"Remove {s.identity_label(key)}?",
                "The group gets a new key that the others receive. They cannot read anything "
                "new - but they keep everything they have already read.", "Remove",
                danger=True)):
            return
        self.set_status("Making a new key for the group ...")
        try:
            await s.remove_member(gid, key)
        except (SessionError, OSError, ConnectionError) as exc:
            self.set_status("")
            self.notify(f"Not removed: {exc}", severity="error", timeout=8)
            return
        self.set_status("")
        self.notify("Removed. The others have the new key.")

    @work(group="dialog", exit_on_error=False)
    async def action_block(self) -> None:
        """Maintainer: block the selected author across the hierarchy (UN-RFC 0018, 5.4)."""
        m, s, gid = self.current_message, self.session, self.current_group
        me = s.identity().key_text
        mine = [h for h in naming.governing(s, gid or "") if h.maintainer == me] if gid else []
        if not mine:
            self.notify("Only the maintainer of a hierarchy above this group can block "
                        "people in it", severity="warning")
            return
        if m is None or m.private or not m.author_key or m.author_key == me:
            self.notify("Select a post by the person you want to block", severity="warning")
            return
        who = s.identity_label(m.author_key, m.from_name)
        reason = await self.push_screen_wait(InputDialog(
            f"Block {who} in {mine[0].name}.*?",
            "Nodes that honour blocks refuse their posts in every group under "
            f"{mine[0].name}, and readers hide them unless they choose to see them. The "
            "block and its reason are public. Reason (required):",
            "e.g. threats against members in several groups"))
        if not reason:
            return
        self.set_status("Posting your hierarchy list ...")
        try:
            where = await naming.block(s, gid, m.author_key, reason)
        except (SessionError, OSError, ConnectionError, ValueError, crypto.CryptoError) as exc:
            self.set_status("")
            self.notify(f"Not blocked: {exc}", severity="error", timeout=8)
            return
        self.set_status("")
        self.current_message = None
        self.notify(f"{who} is blocked in {where}.*")
        self._render_threads()

    def action_toggle_bots(self) -> None:
        """Hide or show posts by bots (UN-RFC 0030 6)."""
        on = not self.session.hide_bots()
        self.session.set_hide_bots(on)
        self.notify("Hiding posts by bots" if on else "Showing posts by bots, marked (bot)")
        self._render_threads()

    def action_toggle_blocked(self) -> None:
        """Show or hide what a hierarchy's maintainer blocked (UN-RFC 0018 5.4)."""
        on = not self.session.data.get("show_blocked")
        self.session.set_show_blocked(on)
        self.notify("Showing posts blocked by maintainers" if on
                    else "Hiding posts blocked by maintainers")
        self._render_threads()

    @work(group="dialog", exit_on_error=False)
    async def action_banned(self) -> None:
        """The keys banned from the group shown, to add or lift."""
        s, gid = self.session, self.current_group
        if not gid or not s.group_entry(gid).get("owner"):
            self.notify("Only a group's owner can see and lift its bans here; "
                        "? shows the group's settings", severity="warning")
            return
        people = [(k, (s.identity_label(k) + (f" - {r}" if r else ""))) for k, r in s.banned(gid)]
        if not people:
            self.notify("Nobody is banned from this group")
            return
        key = await self.push_screen_wait(KeyListDialog(
            f"Banned from {s.group_label(gid)}", "Lifting a ban lets them post here again.",
            people, "Lift ban"))
        if not key:
            return
        try:
            await s.unban(gid, key)
        except (SessionError, OSError, ConnectionError) as exc:
            self.notify(f"Not lifted: {exc}", severity="error")
            return
        self.notify("Ban lifted")
        self._render_threads()

    @work(group="dialog", exit_on_error=False)
    async def action_ignored(self) -> None:
        """Your ignored people (your own killfile, never sent anywhere)."""
        people = self.session.ignored()
        if not people:
            self.notify("You do not ignore anyone")
            return
        key = await self.push_screen_wait(KeyListDialog(
            "People you ignore", "Their posts and messages are hidden from you, everywhere. "
            "Nobody else knows: the list stays in your vault.", people, "Show again"))
        if not key:
            return
        self.session.unignore(key)
        self.notify("Shown again")
        await self._render_groups()
        self._render_threads()

    @work(group="dialog", exit_on_error=False)
    async def action_add_contact(self) -> None:
        """Add the author of the selected post (or a key) as a contact, with your own name
        for them."""
        m = self.current_message
        mine = self.session.identity().key_text
        key = m.author_key if m and m.readable and m.author_key and m.author_key != mine else ""
        key = await self.push_screen_wait(InputDialog(
            "Add contact", "The person's key. If you had a post by them selected, it is filled in.",
            "ed25519:...", value=key))
        if not key:
            return
        suggestion = self.session.contact_name(key) or (m.from_name if m and m.author_key == key else "")
        name = await self.push_screen_wait(InputDialog(
            "Name this contact", "A name of your own choosing. Only you see it, and nobody can "
            "pretend to be this contact under it: it is tied to the key.", "e.g. Sonia",
            value=suggestion))
        if not name:
            return
        try:
            self.session.add_contact(key.strip(), name)
        except (SessionError, crypto.CryptoError) as exc:
            self.notify(f"Not added: {exc}", severity="error")
            return
        self.notify(f"Added {name}")
        await self._render_groups()
        self._render_threads()

    @work(group="dialog", exit_on_error=False)
    async def action_rename_contact(self) -> None:
        """Give the contact of the chat shown another name."""
        key = self._current_contact()
        if not key:
            self.notify("Select a contact first", severity="warning")
            return
        name = await self.push_screen_wait(InputDialog(
            "Rename contact", "", "Name", value=self.session.contact_name(key) or ""))
        if name:
            self.session.add_contact(key, name)
            await self._render_groups()
            self._render_title()

    @work(group="dialog", exit_on_error=False)
    async def action_remove_contact(self) -> None:
        """Remove the contact of the chat shown; the chat stays."""
        key = self._current_contact()
        if not key:
            self.notify("Select a contact first", severity="warning")
            return
        name = self.session.contact_name(key) or key[:20]
        if not await self.push_screen_wait(ConfirmDialog(
                f"Remove {name}?", "The contact is forgotten. Messages stay in Private messages.",
                "Remove")):
            return
        self.session.remove_contact(key)
        self.current_group = self.session.my_inbox
        await self._render_groups()
        self.select_group(self.session.my_inbox)

    @work(group="dialog", exit_on_error=False)
    async def action_close_chat(self) -> None:
        """Take a chat off the list of chats (UN-RFC 0044 4.2): this one, or one you
        choose. The messages stay, and it comes back with their next line."""
        s = self.session
        key = self._current_contact()
        if not key:
            people = [(k, s.contact_name(k) or s.label_of(k)) for k in s.chat_partners()]
            if not people:
                self.notify("You have no chats to close", timeout=4)
                return
            key = await self.push_screen_wait(KeyListDialog(
                "Close a chat", "It leaves the list on all your devices. The messages stay, "
                "and it comes back by itself when they write to you, or when you write.",
                people, "Close chat"))
            if not key:
                return
        s.close_chat(key)
        name = s.contact_name(key) or s.label_of(key)
        self.notify(f"Closed the chat with {name}. It comes back when one of you writes.",
                    timeout=6)
        if self.current_group == CONTACT + key:
            self.current_group = s.my_inbox
            self.select_group(s.my_inbox)
        await self._render_groups()

    @work(group="dialog", exit_on_error=False)
    async def action_signature(self) -> None:
        """Your signature: edit the template."""
        text = await self.push_screen_wait(SignatureDialog(self.session.signature_template()))
        if text is None:
            return
        try:
            self.session.set_signature(text)
        except SessionError as exc:
            self.notify(str(exc), severity="error")
            return
        self.notify("Signature saved" if text.strip() else "Signature removed")

    @work(group="dialog", exit_on_error=False)
    async def action_unsubscribe(self) -> None:
        """Stop following the group shown."""
        g = self._need_group()
        if not g:
            return
        if g.group_id == self.session.my_inbox:
            self.notify("Your private messages cannot be unsubscribed", severity="warning")
            return
        if not await self.push_screen_wait(ConfirmDialog(
                f"Unsubscribe from {self.session.group_label(g.group_id)}?",
                "You stop following the group, and its saved posts are deleted from this "
                "computer. The group stays on the network; you can subscribe again with g.",
                "Unsubscribe")):
            return
        self.session.forget_group(g.group_id)
        self.current_group = None
        self.notify(f"Unsubscribed from {self.session.group_label(g.group_id)}")
        self.refresh_groups()

    @work(group="dialog", exit_on_error=False)
    async def action_remove_group(self) -> None:
        """Remove a group you own (UN-RFC 0013), with the reason readers will see."""
        g = self._need_group()
        if not g:
            return
        if not self.session.group_entry(g.group_id).get("owner"):
            self.notify("Only the group's owner can remove it. You can unsubscribe instead.",
                        severity="warning")
            return
        if not await self.push_screen_wait(ConfirmDialog(
                f"Remove {self.session.group_label(g.group_id)} from the network?",
                "Every node deletes the group and all its posts, and the name can never be "
                "used again - not even by you. This cannot be undone.\n\n"
                "Copies people have already saved cannot be deleted.",
                "Remove for good", danger=True)):
            return
        self.set_status(f"Removing {self.session.group_label(g.group_id)} ...")
        try:
            await self.session.remove_group(g.group_id)
        except (SessionError, OSError, ConnectionError) as exc:
            self.notify(f"Not removed: {exc}", severity="error", timeout=8)
            return
        self.current_group = None
        self.notify(f"{self.session.group_label(g.group_id)} removed")
        self.refresh_groups()
