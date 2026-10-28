"""UnseenetApp's pinned posts, and the class's last tools (UN-RFC 0033 7).

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


class PinsMixin:

    # --- pinned posts (UN-RFC 0033 section 7) ---
    def _pins_here(self, gid: str | None) -> list[dict]:
        """The pins in force in a real group, as every client works them out."""
        s = self.session
        if not gid or gid in ACROSS or gid.startswith(CONTACT) or gid == s.my_inbox:
            return []
        try:
            return s.pins(gid)
        except (SessionError, ValueError, KeyError):
            return []

    def _pins_hidden(self, gid: str | None) -> bool:
        return bool(gid) and gid in self.session.data.get("pins_hidden", [])

    @staticmethod
    def _first_line(m: Message) -> str:
        if m.kind != KIND_CHAT and m.subject:
            return m.subject
        text = split_signature(m.body)[0] if m.readable else ""
        return next((ln.strip() for ln in text.split("\n") if ln.strip()), "") or "(no text)"

    def _pinned_section(self, table: DataTable, gid: str, pins: list[dict], by_id: dict) -> None:
        """A Pinned section above the threads, newest pin first (section 7.2): Enter on
        its head folds it, and a pin opens the post."""
        hidden = self._pins_hidden(gid)
        table.add_row(" ", Text(("► " if hidden else "▼ ") + f"Pinned ({len(pins)})", style="bold"),
                      Text(""), "", key="pins:head")
        if hidden:
            return
        for p in pins:
            m = by_id.get(p["id"])
            title = f"{PIN_MARK} " + (self._first_line(m) if m else "(a post not fetched yet)")
            if p["note"]:
                title += f"  - {p['note']}"
            if m is not None:
                self.messages["pin:" + p["id"]] = m
            date = p["pinned"].astimezone().strftime("%d %b %H:%M")
            table.add_row(" ", Text(title), Text(self._who(m) if m else ""), date,
                          key="pin:" + p["id"])

    def _render_chat_pins(self, gid: str | None, pins: list[dict], by_id: dict) -> None:
        """The pinned line above a chat: the newest pin first, the next with "Next pin"
        (section 7.3)."""
        for w in self.query("#chat-pins"):
            if not gid or not pins or self._pins_hidden(gid):
                w.remove_class("shown")
                continue
            i = self.pin_at.get(gid, 0) % len(pins)
            p, m = pins[i], by_id.get(pins[i]["id"])
            line = Text(f"{PIN_MARK} ", style="bold yellow")
            if m is not None:
                line.append(self.session.identity_label(m.author_key, m.from_name) + ": ", style="bold")
                line.append(self._first_line(m)[:120])
            else:
                line.append("a message not fetched yet")
            if p["note"]:
                line.append(f"  - {p['note']}", style="dim")
            if len(pins) > 1:
                line.append(f"  ({i + 1}/{len(pins)})", style="dim")
            line.append("   Ctrl+P: Next pin, Pinned posts", style="dim")
            w.update(line)
            w.add_class("shown")

    def _beyond_window(self, gid: str, m: Message | None) -> bool:
        """Older than the group keeps posts: unpinned, the nodes delete it at once."""
        days = int(self.session.manifest_data(gid).get("retention_days", 0) or 0)
        return bool(days and m is not None and m.date
                    and datetime.now(timezone.utc) - m.date > timedelta(days=days))

    def action_toggle_pins(self) -> None:
        """Show or fold the pinned posts of the group shown."""
        gid, s = self.current_group, self.session
        if not self._pins_here(gid):
            self.notify("Nothing is pinned in this group", severity="warning")
            return
        hidden = s.data.setdefault("pins_hidden", [])
        if gid in hidden:
            hidden.remove(gid)
        else:
            hidden.append(gid)
        self._save_soon()
        self._render_threads()

    def action_next_pin(self) -> None:
        """To the next pinned post."""
        gid = self.current_group
        pins = self._pins_here(gid)
        if not pins:
            self.notify("Nothing is pinned in this group", severity="warning")
            return
        if self.view_of(gid) != "chat":
            self.action_pinned_posts()
            return
        self.pin_at[gid] = self.pin_at.get(gid, 0) + 1
        if self._pins_hidden(gid):
            self.session.data["pins_hidden"].remove(gid)
            self._save_soon()
        self._render_threads()

    @work(group="dialog", exit_on_error=False)
    async def action_pinned_posts(self) -> None:
        """All pins of the group: choose one to read it."""
        gid, s = self.current_group, self.session
        pins = self._pins_here(gid)
        if not pins:
            self.notify("Nothing is pinned in this group", severity="warning")
            return
        by_id = {m.message_id: m for m in s.cache.get(gid, {}).values()}
        items = []
        for p in pins:
            m = by_id.get(p["id"])
            label = (f"{self._first_line(m)} - {self._who(m)}" if m else "(not fetched yet)") \
                + f", pinned by {s.label_of(p['by'])}" + (f": {p['note']}" if p["note"] else "")
            items.append((p["id"], label))
        chosen = await self.push_screen_wait(KeyListDialog(
            "Pinned posts", "Newest pin first. Choose one to read it.", items, "Read"))
        m = by_id.get(chosen) if chosen else None
        if m is not None:
            self.current_message = m
            self.push_screen(ReadScreen(m))

    def action_pin(self) -> None:
        """Owner or moderator: pin the selected post, or unpin it (section 7.1). The post
        is the one selected now: a refresh while the dialog opens must not change it."""
        self._pin(self.current_group, self.current_message)

    @work(group="dialog", exit_on_error=False)
    async def _pin(self, gid: str | None, selected: Message | None) -> None:
        from unseenet.core import pins as pn
        s = self.session
        if not self._pins_possible(gid):
            self.notify("Only the owner and moderators of a group pin posts, in groups "
                        "that take pins.", severity="warning")
            return
        if self.view_of(gid) == "chat":            # no selection in a chat: choose a line
            lines = [m for m in sorted(s.cache.get(gid, {}).values(), key=lambda m: m.number,
                                       reverse=True) if m.readable][:40]
            pinned = {p["id"] for p in self._pins_here(gid)}
            chosen = await self.push_screen_wait(KeyListDialog(
                "Pin or unpin", "Newest first. [P]: pinned now - choosing it unpins it.",
                [(m.message_id, (f"{PIN_MARK} " if m.message_id in pinned else "")
                  + f"{self._who(m)}: {self._first_line(m)[:60]}") for m in lines], "Choose"))
            m = next((x for x in lines if x.message_id == chosen), None)
        else:
            m = selected
        if m is None or m.group != gid:
            self.notify("Select a post first", severity="warning")
            return
        pins = self._pins_here(gid)
        now = next((p for p in pins if p["id"] == m.message_id), None)
        what = self._first_line(m)
        if now is not None:
            if not await self.push_screen_wait(ConfirmDialog(
                    f"Unpin \"{what}\"?",
                    "It is older than this group keeps posts, so the nodes delete it as soon "
                    "as it is unpinned." if self._beyond_window(gid, m) else
                    "It stays where it is, and goes when it is older than this group keeps posts.",
                    "Unpin", danger=self._beyond_window(gid, m))):
                return
            note, unpin = "", True
        else:
            try:
                limit = pn.max_pins(s.manifest_data(gid))
            except ValueError:
                limit = pn.MAX_PINS_DEFAULT
            warning = ""
            if len(pins) >= limit:                     # the oldest pin stops counting
                last = next((x for x in s.cache.get(gid, {}).values()
                             if x.message_id == pins[-1]["id"]), None)
                warning = (f"This group shows at most {limit} pins, so "
                           f"\"{self._first_line(last) if last else 'the oldest pin'}\" stops "
                           "counting. Unpin another one first if you would rather keep it "
                           "(Ctrl+P: Pinned posts).")
            note = await self.push_screen_wait(PinDialog(
                f"Pin \"{what}\"", "Everyone sees it at the top of the group, and the nodes "
                "keep it until it is unpinned.", warning))
            if note is None:
                return
            unpin = False
        self.set_status(("Unpinning" if unpin else "Pinning")
                        + " ... the marker needs its proof of work first")
        try:
            await s.pin(gid, m.message_id, note, unpin=unpin)
        except (SessionError, ValueError, OSError, ConnectionError) as exc:
            self.set_status("")
            self.notify(f"Not {'unpinned' if unpin else 'pinned'}: {exc}", severity="error",
                        timeout=8)
            return
        self.set_status("")
        self.pin_at[gid] = 0                           # the newest pin first again
        self._render_threads()
        self.notify("Unpinned" if unpin else "Pinned")

    def action_react(self, msg: Message | None = None) -> None:
        """l: react to the selected post, comment or chat line with one of the group's
        emoji (UN-RFC 0048 section 7.3); in a chat, choose the line first. The emoji you
        gave already takes it back, another one changes it. The post is the one selected
        now: a refresh while the dialog opens must not change it."""
        m = msg or self.current_message
        gid = m.group if msg is not None and m is not None else (self.current_group or "")
        if gid in ACROSS and m is not None:
            gid = m.group
        self._react(gid, m, chat=msg is None and self.view_of(gid) == "chat")

    @work(group="dialog", exit_on_error=False)
    async def _react(self, gid: str, m: Message | None, chat: bool) -> None:
        s = self.session
        if chat:                                       # no selection in a chat
            if gid.startswith("contact:"):            # a private chat: their lines (#9)
                lines = [x for x in reversed(s.conversation(gid.split(":", 1)[1]))
                         if x.readable and s.react_permission(x.group, x).allowed][:40]
            else:
                lines = [x for x in sorted(s.cache.get(gid, {}).values(), key=lambda x: x.number,
                                           reverse=True) if x.readable][:40]
            chosen = await self.push_screen_wait(KeyListDialog(
                "React to", "Newest first.",
                [(x.message_id, f"{self._who(x)}: {self._first_line(x)[:60]}") for x in lines],
                "Choose"))
            m = next((x for x in lines if x.message_id == chosen), None)
        if m is None:
            self.notify("Select a post first", severity="warning")
            return
        allowed = s.react_permission(m.group, m)
        if not allowed:
            self.notify(allowed.reason, severity="warning")
            return
        rs = s.reaction_set(m.group)
        mine = s.reactions_on(m).of(s.identity().key_text)
        first = not s.client_setting("tui", "reacted", False)
        emoji = await self.push_screen_wait(ReactDialog(
            f"\"{self._first_line(m)[:50]}\"", rs.offered, mine, rs.any, first_time=first))
        if not emoji:
            return
        if first:
            s.set_client_setting("tui", "reacted", True)
        self._send_reaction(m, emoji, taking_back=rx.same_emoji(emoji, mine or ""))

    @work(group="react", exit_on_error=False)
    async def _send_reaction(self, m: Message, emoji: str, taking_back: bool) -> None:
        """Post the reaction in the background (its postage takes a moment) and show
        the counts again when it is out (UN-RFC 0048 section 7.3)."""
        self.set_status(("Taking back " if taking_back else "Reacting ") + emoji
                        + " ... the reaction needs its proof of work first")
        try:
            await self.session.react(m, emoji)
        except (SessionError, ValueError, OSError, ConnectionError) as exc:
            self.set_status("")
            self.notify(f"Not sent: {exc}", severity="error", timeout=8)
            return
        self.set_status("")
        if m.group == self.current_group or self.current_group in ACROSS:
            self._render_threads()
        for screen in self.screen_stack:
            if isinstance(screen, ReadScreen) and screen.msg.message_id == m.message_id:
                screen.show(screen.msg, keep_place=True)

    def _pins_possible(self, gid: str | None) -> bool:
        from unseenet.core import pins as pn
        s = self.session
        if not gid or gid in ACROSS or gid.startswith(CONTACT) or gid == s.my_inbox:
            return False
        try:
            return s.moderates_or_owns(gid) and pn.max_pins(s.manifest_data(gid)) > 0
        except ValueError:
            return s.moderates_or_owns(gid)

    @work(group="dialog", exit_on_error=False)
    async def action_withdraw(self) -> None:
        """Withdraw (delete) the selected post or comment, or in a chat your last line
        (UN-RFC 0004, section 7)."""
        s = self.session
        m = self._withdrawable(self.current_group or "")
        if m is None:
            self.notify("You can withdraw your own posts, comments and chat lines, and any "
                        "post in a group you own or moderate.", severity="warning")
            return
        mine = m.author_key == s.identity().key_text
        what = ("chat line" if self.view_of(m.group) == "chat" else "comment") \
            if m.kind == KIND_CHAT else "post"
        shown = m.subject or split_signature(m.body)[0].strip().replace("\n", " ")[:60] \
            or "(no subject)"
        if not await self.push_screen_wait(ConfirmDialog(
                f"Withdraw your {what} \"{shown}\"?" if mine else f"Withdraw this {what} \"{shown}\"?",
                ("Your " + what if mine else "This " + what) + " is taken back from every node "
                "that follows the protocol, and other clients stop showing it.\n\n"
                "It is withdrawn, not deleted: people who have read it may have kept a "
                "copy, and nodes or programs that ignore withdrawals can keep it.",
                "Withdraw", danger=True)):
            return
        self.set_status("Withdrawing ...")
        try:
            await s.cancel(m)
        except (SessionError, OSError, ConnectionError) as exc:
            self.set_status("")
            self.notify(f"Not withdrawn: {exc}", severity="error", timeout=8)
            return
        self.set_status("")
        self.current_message = None
        self._render_threads()
        self.notify("Withdrawn")

    def action_open_inbox(self) -> None:
        """Open your inbox: your private posts."""
        gid = self.session.my_inbox
        if gid in self.groups:
            self.select_group(gid)
        else:
            self.notify("Your inbox is not ready yet - it is created when the node is reachable",
                        severity="warning")

    def action_mark_all_read(self) -> None:
        """Mark every post of the group shown as read."""
        g = self._need_group()
        if not g:
            return
        for m in self.session.cache.get(g.group_id, {}).values():
            self.session.mark_read(m, save=False)
        self.session.save()
        self._render_threads()
        self._save_soon()

    def action_refresh(self) -> None:
        """F5: read the groups from the nodes now."""
        if self.session:
            self.refresh_groups()

    async def action_quit(self) -> None:
        """q: stop the runtime and Tor, save, and quit."""
        if getattr(self, "runtime", None) is not None:
            self.runtime.stop_ripening()           # a segment must not keep us open
        if self.session:
            self.session.save()
            await self.session.close()
        self.exit()
