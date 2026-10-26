"""UnseenetApp's the group list: filling it, choosing, reading and counting groups.

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


class GroupsMixin:

    # --- groups ---
    def _groups_from_vault(self, available: dict[str, GroupInfo] | None = None) -> dict[str, GroupInfo]:
        groups = {}
        for g in self.session.subscribed_groups(available, counts=False):   # (#139)
            if g.listed is False:
                g.description = "(not on your nodes)"
            groups[g.group_id] = g
        return groups

    async def show_offline(self) -> None:
        """Show what is in the encrypted local cache before any network access."""
        loaded = self.runtime.open()            # starter groups, then the offline cache
        self.groups = self._groups_from_vault()
        await self._render_groups()
        if self.groups:
            self.current_group = next(iter(self.groups))
            self._render_threads()
        self.set_status(f"{loaded} saved posts available offline - connecting ...")

    @work(exclusive=True, group="groups", exit_on_error=False)
    async def refresh_groups(self) -> None:
        """Read the group list from the nodes again and draw it, with the counts."""
        s = self.session
        if set(s.data["subscribed"]) != set(self.groups):    # just followed or left one:
            self.groups = self._groups_from_vault()          # show it now, not after the round
            await self._render_groups()
        result = await self.runtime.refresh()   # inbox once, then every group (#139)
        if result.error:
            self.set_status(f"Offline ({result.error}) - showing saved posts")
            return
        groups = self._groups_from_vault(self.runtime.available)
        self.groups = groups
        before = getattr(self, "_for_you_seen", None)
        now = s.for_you_counts()
        await self._render_groups()
        news = for_you_news(before, now) if before is not None else None
        if news:
            self.notify(news, timeout=8)
        self._for_you_seen = now
        if self.current_group in groups or self._current_contact() or self.current_group in ACROSS:
            self._render_threads()
        elif groups:
            self.select_group(next(iter(groups)))
        if not groups:
            self.query_one("#threads-title", Static).update(
                "No groups yet - press g to browse the node's groups, or Ctrl+P to create one")
        where = f"{len(s.nodes)} nodes" if len(s.nodes) > 1 else "node"
        down = f" - not reachable: {', '.join(s.node_label(n) for n in s.unreachable)}" if s.unreachable else ""
        self.set_status(f"{result.available} groups on {where}, {len(groups)} subscribed{down}")

    async def _render_groups(self) -> None:
        tree = self.query_one("#groups", Tree)
        tree.show_root = False
        # folders are known by ("folder", key): "Chats", a hierarchy ("alt") or a part
        # of a name within one ("alt.history")
        def folder_key(node) -> str:
            return node.data[1] if isinstance(node.data, tuple) else str(node.label).split(" (")[0]

        def all_folders(node):
            for n in node.children:
                if n.children:
                    yield n
                    yield from all_folders(n)
        cursor = tree.cursor_node
        keep = (cursor.data if cursor is not None and cursor.data is not None
                else ("folder", folder_key(cursor)) if cursor is not None else None)
        scroll_y = tree.scroll_y
        was_open = {folder_key(n) for n in all_folders(tree.root) if n.is_expanded}
        known = {folder_key(n) for n in all_folders(tree.root)}
        tree.clear()

        def label(g: GroupInfo, shown: str | None = None) -> Text:
            """The group's line; ``shown`` is its name under its folder (``.history.egypt``)."""
            unread = (self.session.unread_private_posts() if g.group_id == self.session.my_inbox
                      else self.session.unread_count(g.group_id))
            marks = (for_you_marks(self.session.for_you_counts(g.group_id))
                     if g.group_id != self.session.my_inbox else "")
            if shown is None:
                text = Text(self._group_label(g))
            else:
                full = self.session.group_label(g.group_id)
                text = Text(f"{group_icons(g.type, self.session.manifest_data(g.group_id))} ")
                if shown.startswith("."):
                    text.append(".", style="dim")
                    shown = shown[1:]
                text.append(shown + fingerprint_suffix(full))
            text.append((f" ({unread})" if unread else "") + marks)
            if unread:
                text.stylize("bold")
            return text

        def folder_label(name: str, items: list[GroupInfo]) -> Text:
            unread = sum(self.session.unread_count(g.group_id) for g in items)
            text = Text()
            if name.startswith("."):
                text.append(".", style="dim")
                name = name[1:]
            text.append(name + (f" ({unread})" if unread else ""))
            if unread:
                text.stylize("bold")
            return text

        def add_branch(parent, node) -> None:
            """A hierarchy's or folder's contents: its own group first, then the rest."""
            for g in node.items:                # alt.history in .history, unseenet in unseenet.*
                nodes[g.group_id] = parent.add_leaf(label(g, node.own_label), data=g.group_id)
            for child in node.children:
                if child.is_folder:
                    key = child.path
                    inside = {g.group_id for g in child.all_items()}
                    folder = parent.add(folder_label("." + child.label, child.all_items()),
                                        data=("folder", key),
                                        expand=searching or key in was_open
                                        or (key not in known and self.current_group in inside))
                    nodes[("folder", key)] = folder
                    add_branch(folder, child)
                else:
                    for g in child.items:
                        nodes[g.group_id] = parent.add_leaf(label(g, "." + child.label),
                                                            data=g.group_id)

        select = None
        nodes: dict = {}
        groups = list(self.groups.values())
        searching = bool(groupfilter.parse(self.group_query))
        if searching:                            # #13: only what matches, every folder open
            groups = self.session.filter_groups(groups, self.group_query)
            was_open, known = set(), set()
        mine = [g for g in groups if g.group_id == self.session.my_inbox]
        my_feed = self.session.personal_group("feed")
        my_site = self.session.personal_group("site")
        on_top = list(mine)                              # not listed again further down
        for kind, gid_ in (("feed", my_feed), ("site", my_site)):   # "Your feed", "Your site" (#495)
            if not searching and self.session.has_personal_group(kind):
                on_top += [g for g in groups if g.group_id == gid_]
        for g in mine:                                   # private messages always first
            node = tree.root.add_leaf(label(g), data=g.group_id)
            nodes[g.group_id] = node
        for_you = self.session.for_you_counts()
        text = Text(f"{REPLY_MARK} For you" + for_you_marks(for_you))
        if for_you.total:
            text.stylize("bold")
        if not searching:
            nodes[FOR_YOU] = tree.root.add_leaf(text, data=FOR_YOU)
            if self.session.feeds_followed():            # UN-RFC 0034 section 7
                nodes[TIMELINE] = tree.root.add_leaf(Text(f"{FEED_MARK} Timeline"), data=TIMELINE)
            # your own feed and site, or a way to start them, where people look (#495)
            for kind, gid_, mark, start in (("feed", my_feed, FEED_MARK, START_FEED),
                                            ("site", my_site, SITE_MARK, START_SITE)):
                if self.session.has_personal_group(kind):
                    unread = self.session.unread_count(gid_)
                    mine_text = Text(f"{mark} Your {kind}" + (f" ({unread})" if unread else ""))
                    if unread:
                        mine_text.stylize("bold")
                    nodes[gid_] = tree.root.add_leaf(mine_text, data=gid_)
                else:
                    nodes[start] = tree.root.add_leaf(
                        Text(f"{mark} Start your {kind}", style="dim"), data=start)
        self._for_you_seen = getattr(self, "_for_you_seen", None) or for_you
        # one chat per person: your contacts, and anyone else you have chatted with
        # (UN-RFC 0028, 6.2); posts are in Private posts
        partners = [] if searching else self.session.chat_partners()
        if partners:
            total = sum(self.session.unread_from(k, KIND_CHAT) for k in partners)
            folder = Text("Chats" + (f" ({total})" if total else ""))
            if total:
                folder.stylize("bold")
            cnode = tree.root.add(folder, data=("folder", "Chats"),
                                  expand="Chats" in was_open or "Chats" not in known)
            nodes[("folder", "Chats")] = cnode
            for key in partners:
                name = self.session.contact_name(key) or ""
                unread = self.session.unread_from(key, KIND_CHAT)
                if not name:                 # what they call themselves, with their fingerprint
                    said = next((m.from_name for m in reversed(self.session.conversation(key, KIND_CHAT))
                                 if m.sender_key == key and m.from_name), "")
                    name = self.session.identity_label(key, said) if said else self._key_label(key)
                    name = "◦ " + name
                else:
                    name = "• " + name
                text = Text(name
                            + (f" ({unread})" if unread else ""))
                if unread:
                    text.stylize("bold")
                nodes[CONTACT + key] = cnode.add_leaf(text, data=CONTACT + key)
        for top, items in by_hierarchy([g for g in groups if g not in on_top]):
            if not top:                          # a hierarchy of one: no folder, full name
                for g in items:
                    nodes[g.group_id] = tree.root.add_leaf(label(g), data=g.group_id)
                continue
            parent = tree.root.add(folder_label(f"{top}.*", items), data=("folder", top),
                                   expand=searching or top in was_open or top not in known)
            nodes[("folder", top)] = parent
            add_branch(parent, group_tree(top, [(g.group_id.rpartition("@")[0], g) for g in items]))
        # Put the cursor back where it was (or on the current group); the tree computes
        # line numbers on its next refresh, so do it after that.
        select = nodes.get(keep) or nodes.get(self.current_group)
        up = select.parent if select is not None else None
        while up is not None and up is not tree.root:     # a group opened from a folded folder
            up.expand()
            up = up.parent

        def restore() -> None:
            if select is not None and select.line >= 0:
                tree.move_cursor(select, animate=False)
            tree.scroll_to(y=scroll_y, animate=False)
        self.call_after_refresh(restore)

    def _group_label(self, g: GroupInfo) -> str:
        if g.group_id == self.session.my_inbox:
            return f"{group_icons('inbox')} Private posts"
        return f"{group_icons(g.type, self.session.manifest_data(g.group_id))} {self.session.group_label(g.group_id)}"

    def action_search_groups(self) -> None:
        """/: to the group filter."""
        box = self.query_one("#groups-filter", Input)
        box.disabled = False
        box.add_class("shown")
        box.focus()

    def _close_group_search(self) -> None:
        box = self.query_one("#groups-filter", Input)
        box.value = ""
        box.remove_class("shown")
        box.disabled = True
        self.query_one("#groups", Tree).focus()

    @work(group="dialog", exit_on_error=False)
    async def action_toggle_inbox_files(self) -> None:
        """Pictures in private posts and chats to you, on or off (#497)."""
        s = self.session
        on = not s.inbox_files()
        try:
            await s.set_inbox_files(on)
        except (SessionError, OSError, ConnectionError, NNTPError) as exc:
            self.notify(f"Not changed: {exc}", severity="error", timeout=8)
            return
        self.notify("People can send you pictures in private posts and chats - encrypted, "
                    "only you see them" if on else
                    "Private posts and chats to you take no pictures now", timeout=6)

    @work(group="dialog", exit_on_error=False)
    async def _offer_pictures(self, gid: str) -> None:
        """Your feed or site made before they took pictures by default: offer it, once (#496)."""
        s = self.session
        kind = next((k for k in ("feed", "site") if gid == s.personal_group(k)), None)
        if kind is None or not s.personal_group_without_files(kind):
            return
        s.group_entry(gid)["files_offered"] = True
        s.save()
        if not await self.push_screen_wait(ConfirmDialog(
                f"Pictures in your {kind}?",
                f"Your {kind} takes no pictures yet - it was started before they were on by "
                "default. Let it carry pictures, up to 4 MiB each? You can change it later in "
                "Edit group.", "Allow pictures")):
            return
        try:
            await s.allow_pictures(gid)
        except (SessionError, OSError, ConnectionError, NNTPError) as exc:
            self.notify(f"Not changed: {exc}", severity="error", timeout=8)
            return
        self.notify(f"Your {kind} takes pictures now", timeout=5)

    @work(exclusive=True, group="fetch", exit_on_error=False)
    async def select_group(self, gid: str) -> None:
        """Show a group: its threads, chat, feed or site, read from the cache then the node."""
        self.current_group = gid
        self.current_message = None
        self.refresh_bindings()
        if gid == FOR_YOU:
            self._render_threads()
            self.query_one("#threads").focus()
            return
        if gid == TIMELINE:
            self._render_threads()                          # what is saved, at once
            self.query_one("#threads").focus()
            try:
                await self.session.refresh_following()      # people's feeds, as they are now
                for feed in self.session.feeds_followed():
                    await self.session.fetch_group(feed)
            except (SessionError, OSError, ConnectionError) as exc:
                self.set_status(f"Offline ({exc}) - showing saved posts")
            if self.current_group == TIMELINE:
                self._render_threads()
            return
        if gid.startswith(CONTACT):
            try:
                await self.session.fetch_group(self.session.my_inbox)
            except (SessionError, OSError, ConnectionError) as exc:
                self.set_status(f"Offline ({exc}) - showing saved messages")
            self._render_threads()
            self._focus_view(gid)
            return
        g = self.groups.get(gid) or GroupInfo(gid)
        self.query_one("#threads-title", Static).update(f"{self.session.group_label(g.group_id)} - loading ...")
        try:
            await self.session.fetch_group(gid)
        except SessionError as exc:
            if "removed by its owner" in str(exc):
                self.notify(f"{self.session.group_label(g.group_id)} was removed by its owner. "
                            "Use 'Unsubscribe from group' (Ctrl+P) to forget it.",
                            severity="warning", timeout=10)
            else:
                self.set_status(f"{exc}")
        except (OSError, ConnectionError) as exc:
            self.set_status(f"Offline ({exc}) - showing saved posts")
        if self.current_group != gid:            # the user moved on, or the group was removed
            return
        if gid in self.groups:
            self.groups[gid].type = self.session.group_entry(gid).get("type")
        self._render_threads()
        self._focus_view(gid)
        if self.session.rules_unseen(gid) and isinstance(self.screen, Screen) \
                and not isinstance(self.screen, ModalScreen):
            self.action_group_info(first_time=True)

    def _render_threads(self) -> None:
        gid = self.current_group
        s = self.session
        mine, patterns = s.my_message_ids(), s._mention_patterns()
        shown = self.view_of(gid) if gid else "threads"
        feed = shown == "feed"
        page_of: dict[str, str] = {}
        if shown == "pages":                     # a site: one row a page, home first (0035, 5.2)
            site = s.site(gid)
            rows = [(p["current"], 0) for p in site["pages"] if p["current"] is not None]
            page_of = {p["current"].message_id: p["path"] for p in site["pages"] if p["current"]}
            home = site["home"]
        else:                                    # the same rows in every client (#139)
            rows = (s.feed_rows(gid) if feed else s.threads(gid)) if gid else []
        self._scores = {}
        if (not feed or gid == TIMELINE) and shown not in ("chat", "pages"):   # your rules (UN-RFC 0051)
            rows, self._scores = self.scored_rows(gid, rows)
        table = self.query_one("#threads", DataTable)
        keep = self.current_message.message_id if self.current_message else None
        table.clear()
        self.messages = {}
        pins = self._pins_here(gid)
        pinned = {p["id"] for p in pins}
        by_id = {m.message_id: m for m, _ in rows}
        if pins and self.view_of(gid) != "chat":
            self._pinned_section(table, gid, pins, by_id)
        counted: dict = {}                              # reactions, once per group (0048)
        for m, depth in rows:
            self.messages[m.message_id] = m
            flag = "●" if not s.is_read(m) else " "
            title = m.subject or "(no subject)"
            if m.kind == KIND_CHAT and m.readable:     # a chat line among posts: its first line
                first = (m.body.strip().split("\n", 1)[0] if m.readable else "") or "…"
                title = "» " + first[:60] + ("…" if len(first) > 60 else "")
            if feed and depth:                          # a comment: who and what, under its entry
                first = (m.body.strip().split("\n", 1)[0] if m.readable else "") or "…"
                title = first[:70] + ("…" if len(first) > 70 else "")
                subject = "  " * depth + "↳ " + title
            else:
                subject = ("  " * (depth - 1) + "└ " if depth else "") + title
            if m.message_id in page_of:                 # a page: its title and its path
                path = page_of[m.message_id]
                subject = ("⌂ " if path == home else "  ") + (m.subject or path) + f"   /{path}"
            if m.edited:
                subject += "  (edited)"
            notice = s.is_notice(m)                      # UN-RFC 0032: names you elsewhere
            if m.private and not notice:
                subject = f"{ICONS['inbox']} " + subject
            elif m.encrypted and m.readable:
                subject = f"{ICONS['closed']} " + subject
            if m.message_id in pinned:
                subject = f"{PIN_MARK} " + subject           # in its place too (UN-RFC 0033, 7.2)
            why = s.for_me(m, mine, patterns)
            if why:
                subject = (REPLY_MARK if why == "reply" else MENTION_MARK) + " " + subject
            if gid == FOR_YOU:
                subject += (f"  ({s.group_label(m.mention_group)}, not followed)" if notice
                            else f"  ({s.group_label(m.group)})")
            elif gid == TIMELINE:
                subject += f"  ({s.group_label(m.group)})"
            date = m.date.astimezone().strftime("%d %b %H:%M") if m.date else ""
            line = Text(subject)
            if getattr(m, "origin_network", ""):                 # UN-RFC 0049 section 5.1
                line.append(f"  {ORIGIN_MARK} {network_name(m.origin_network)}", style="dim italic")
            liked = self._reactions_line(m, counted)
            if liked:
                line.append("   " + liked, style="dim")
            res = self._scores.get(m.message_id)
            if res is not None:                              # your rules: how it is shown
                if "highlight" in res.actions:
                    line.stylize("bold reverse")
                elif "dim" in res.actions:
                    line.stylize("dim")
                folded_n = getattr(self, "_folded", {}).get(m.message_id)
                if folded_n:
                    line.append(f"   ▸ {folded_n} folded", style="dim italic")
                line.append(f"   {res.score:+d}", style="dim")
            table.add_row(flag, line, Text(self._who(m)), date, key=m.message_id)
        self._render_title()
        # the rows are fresh objects; an open read screen follows them, so it
        # shows an edited post and steps n/p from the right place (#137)
        for screen in self.screen_stack:
            if isinstance(screen, ReadScreen):
                fresh = self.messages.get(screen.msg.message_id)
                if fresh is not None and fresh is not screen.msg:
                    screen.show(fresh, keep_place=True)
        if self._scores and gid not in (FOR_YOU, TIMELINE) and not gid.startswith(CONTACT):
            # seen only while your rules are on: only they use it (UN-RFC 0044 4.5)
            newest = max((m.date for m, _ in rows if m.date), default=None)
            if newest is not None:                   # shown on the screen: seen (UN-RFC 0044 4.5)
                s.mark_seen(gid, newest)
        mode = self.view_of(gid)
        self._apply_view(mode)
        self._render_chat_pins(gid if mode == "chat" else None, pins, by_id)
        self._update_chat_input(gid)
        if mode == "chat":
            chron = [m for m, _ in rows] if gid and gid.startswith(CONTACT) \
                else sorted((m for m, _ in rows), key=lambda m: m.number)
            self._render_chat(chron)
            return
        target = getattr(self, "_open_at", None)          # an address that was opened (#357)
        if target is not None and gid:
            if isinstance(target, tuple):                # a page, by its path
                keep = next((k for k, m in self.messages.items() if m.page == target[1]), keep)
            elif target in self.messages:
                keep = target
            if keep in self.messages:
                self._open_at = None
        if keep in self.messages:
            table.move_cursor(row=table.get_row_index(keep))
        elif gid and gid.startswith(CONTACT) and rows:
            table.move_cursor(row=len(rows) - 1)                  # newest at the bottom
