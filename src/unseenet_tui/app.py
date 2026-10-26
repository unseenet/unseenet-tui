"""Unseenet TUI client (Textual).

    unseenet-tui [--vault unseenet.vault]

Left: your groups with unread counts. Right: threads in the selected group
and the selected article. Everything is stored in the encrypted vault.
"""

from __future__ import annotations

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

# moved out by area (#20); re-exported here, so imports from app keep working
from .helpers import REFRESH_SECONDS, REPO_URL, DOCS_URL, ACROSS, FEED_MARK, START_FEED, START_SITE, SITE_MARK, REPLY_MARK, MENTION_MARK, for_you_marks, for_you_news, sess_default_pow, group_name_problem, GROUP_KINDS, known_node_details, node_details, ICONS, _ONION, PIN_MARK, ICON_LEGEND, ORIGIN_MARK, NETWORK_NAMES, network_name, origin_text, edited_when, group_icons, is_personal_inbox, by_hierarchy, CSS  # noqa: F401
from .dialogs.base import Dialog, InputDialog, InfoDialog, ConfirmDialog, KeyListDialog  # noqa: F401
from .dialogs.setup import UnlockDialog, ChangePassphraseDialog, OTHER_NODE, SetupDialog, BridgesDialog  # noqa: F401
from .dialogs.compose import ComposeDialog, PageDialog, PreviewDialog, SignatureDialog, PinDialog, ReactDialog, reactions_text  # noqa: F401
from .dialogs.people import ContactsDialog, NamesDialog, PROFILE_WARNING, identicon_text, MARKDOWN_HINT, manifest_text, profiles_clean, writes_markdown, ProfileDialog, EditProfileDialog  # noqa: F401
from .dialogs.groups import DELIVERY_CHOICES, DELIVERY, SITE_CHOICE, KINDS_CHOICES, KINDS, REACTIONS_CHOICES, reactions_choice, COMMENTS_CHOICES, FILES_CHOICES, FILES_GROUP_TYPES, reactions_label, comments_label, GROUP_NAME_HELP, CreateGroupDialog, GroupInfoDialog, EditGroupDialog, BrowseTree, NO_SELECT_ON_FOCUS, BrowseDialog  # noqa: F401
from .dialogs.moderation import ReportDialog, ModerationDialog  # noqa: F401
from .dialogs.nodes import NodesDialog, AddNodeDialog, NodeInfoDialog  # noqa: F401
from .screens import ChatBox, attachments_text, save_name, ReadScreen, AboutDialog  # noqa: F401



# ======================================================================
# Main app
# ======================================================================

class UnseenetApp(App):
    TITLE = "unseenet"
    CSS = CSS + console_css()

    def refresh_bindings(self) -> None:
        """An event handled while the app closes may find no screen left (#137)."""
        try:
            super().refresh_bindings()
        except ScreenStackError:
            pass
    BINDINGS = [
        # chat: the input line takes the letters; Esc leaves it, Enter goes back
        # (first, so the footer always has room for them)
        Binding("escape", "leave_input", "Shortcuts"),
        Binding("enter", "write", "Write"),
        Binding("ctrl+f", "attach_to_chat", "Attach"),   # files on the next chat line (#496)
        Binding("n", "new_post", "New post"),
        Binding("r", "reply", "Reply"),
        Binding("l", "react", "React"),               # a like, or another emoji (UN-RFC 0048)
        Binding("g", "browse", "Groups"),
        Binding("slash", "search_groups", "Search groups", show=False),
        Binding("j", "join", "Join", show=False),
        Binding("m", "message", "Message"),
        Binding("c", "contacts", "Contacts", show=False),
        Binding("o", "node_info", "Nodes", show=False),
        Binding("k", "show_key", "My key", show=False),
        Binding("at", "names", "Names", show=False),
        Binding("s", "signature", "Signature", show=False),
        Binding("a", "add_contact", "Add contact", show=False),
        Binding("D", "withdraw", "Withdraw", show=False),
        Binding("e", "edit", "Edit", show=False),
        Binding("u", "show_profile", "Profile", show=False),   # the author's (#506)
        Binding("question_mark", "group_info", "About group"),
        Binding("number_sign", "group_ref", "Go to #group", show=False),
        Binding("space", "next_unread", "Next unread"),
        Binding("f", "read", "Read"),
        Binding("v", "toggle_view", "View"),          # threads, feed, chat; or pages (#490)
        Binding("f5", "refresh", "Refresh"),
        Binding("ctrl+r", "refresh", "Refresh", show=False),
        Binding("f1", "about", "About", show=False),
        Binding("q", "quit", "Quit"),
    ]

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        """Show only the shortcuts that do something where you are (False hides one)."""
        s = self.session
        if isinstance(self.screen, ReadScreen):
            return action in ("about", "refresh")    # it has its own r, m, n, p, l
        if s is None or not s.has_identity():
            return True
        gid = self.current_group or ""
        m = self.current_message
        if gid in ACROSS and m is not None:
            gid = m.group                         # act as in the post's own group
        contact = gid.startswith(CONTACT)
        group = gid in self.groups
        chat = bool(gid) and self.view_of(gid) == "chat"
        typing = getattr(self.focused, "id", None) == "chat-input"
        if action == "leave_input":
            return typing or bool(self.group_query) \
                or getattr(self.focused, "id", None) == "groups-filter"
        if action == "write":
            return chat and not typing
        if action == "attach_to_chat":
            return chat and (group or contact) and self._chat_takes_files(gid)
        shown = self.view_of(gid) if group else "threads"
        if action in ("next_unread", "read"):     # the keys of a list (#490)
            return bool(gid) and not chat and not typing and (action == "next_unread" or m is not None)
        if action == "reply" and shown == "pages":
            return False                          # a page is not answered (UN-RFC 0035)
        if action == "new_post" and shown == "pages":
            return self._writes_pages(gid)        # n writes a page, for the site's writers (#212)
        if action == "new_post":
            return contact or (group and s.write_permission(gid, kind=KIND_POST).allowed)
        if action == "reply":                     # #181: hidden when it would be refused
            return contact or (m is not None and m.readable and (
                m.private or s.write_permission(gid, reply_to=m, kind=KIND_POST).allowed))
        if action == "react":                     # hidden where reacting is impossible (0048)
            if contact:                           # a private chat (UN-RFC 0048 4.3, #9)
                return chat or (m is not None and s.react_permission(m.group, m).allowed)
            if not group or shown == "pages":
                return False
            if chat:
                return bool(s.reaction_set(gid))
            return m is not None and s.react_permission(gid, m).allowed
        if action == "invite":
            g = self.groups.get(gid)
            return bool(g and g.type == "closed" and s.group_entry(gid).get("owner"))
        if action == "withdraw":                  # the selected post, or your last chat line
            return bool(group and self._withdrawable(gid) is not None)
        if action == "edit":                      # your own post, or your last chat line
            return bool(group and self._editable(gid) is not None)
        if action == "add_contact":
            return bool(m and m.readable and m.author_key
                        and m.author_key != s.identity().key_text)
        if action in ("group_info", "toggle_view"):
            return group and gid != s.my_inbox
        return True

    def action_leave_input(self) -> None:
        if getattr(self.focused, "id", None) != "chat-input" and (
                self.group_query or getattr(self.focused, "id", None) == "groups-filter"):
            self._close_group_search()               # Esc ends a search of your groups
            return
        self.query_one("#chat-log").focus()          # now the letters are shortcuts again

    def action_write(self) -> None:
        gid = self.current_group
        if gid and not gid.startswith(CONTACT) and gid in self.groups:
            p = self.session.write_permission(gid, kind=KIND_CHAT)
            if not p:
                self.notify(p.reason, severity="warning")
                return
        self.query_one("#chat-input").focus()

    def _update_chat_input(self, gid: str | None) -> None:
        """The chat line is unavailable, with the reason as its placeholder, where a
        chat line would be refused - not after you typed one (#181)."""
        box = self.query_one("#chat-input", ChatBox)
        p = (self.session.write_permission(gid, kind=KIND_CHAT)
             if gid and not gid.startswith(CONTACT) and gid not in ACROSS else None)
        if p is not None and not p:
            box.disabled, box.placeholder = True, p.reason
        else:
            box.disabled = False
            box.placeholder = "Write a message - Enter sends, Alt+Enter new line, Esc for the shortcuts" + (
                ", Ctrl+F attaches files" if gid and self._chat_takes_files(gid) else "")
        self._show_chat_files(gid)

    def _focus_view(self, gid: str) -> None:
        if self.view_of(gid) != "chat":
            self.query_one("#threads").focus()
        elif self.query_one("#chat-input", ChatBox).disabled:
            self.query_one("#chat-log").focus()
        else:
            self.query_one("#chat-input").focus()

    def __init__(self, vault_path: Path):
        super().__init__()
        self.vault_path = vault_path
        self.session: Session | None = None
        self.runtime: ClientRuntime | None = None
        self.pending: dict[str, list[dict]] = {}   # your chat lines on their way, per group (#325)
        self.chat_files: dict[str, list[str]] = {}  # files for the next chat line, per group (#496)
        self.groups: dict[str, GroupInfo] = {}
        self.current_group: str | None = None
        self.messages: dict[str, Message] = {}
        self.current_message: Message | None = None
        self._inbox_ready = False
        self._watching: set[str] = set()
        self.group_query = ""                    # the search in your group list (#13)
        self.pin_at: dict[str, int] = {}         # the pin a chat's pinned line shows

    # --- layout ---
    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with Vertical(id="groups-pane"):
                yield Static("Groups", id="groups-title")
                # disabled while hidden, or it would take the focus at start
                yield Input(placeholder="Search your groups", id="groups-filter", disabled=True,
                            **NO_SELECT_ON_FOCUS)
                yield Tree("groups", id="groups")
            with Vertical():
                yield Static("Select a group", id="threads-title", markup=False)
                yield DataTable(id="threads", cursor_type="row", zebra_stripes=True)
                with VerticalScroll(id="article"):
                    yield Static("", id="article-head")
                    yield Static("", id="article-body", markup=False)
                with Vertical(id="chat"):
                    yield Static("", id="chat-pins", markup=False)
                    yield RichLog(id="chat-log", wrap=True, markup=False, highlight=False)
                    yield ChatBox(placeholder="Write a message - Enter sends, Alt+Enter new "
                                  "line, Esc for the shortcuts", id="chat-input")
        yield Static("", id="status", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#threads", DataTable)
        table.add_column(" ", key="flag", width=2)
        table.add_column("Subject", key="subject", width=44)
        table.add_column("From", key="from", width=20)
        table.add_column("Date", key="date", width=16)
        self.start()

    def set_status(self, text: str) -> None:
        try:
            self.query_one("#status", Static).update(text)
        except Exception:  # noqa: BLE001  (before mount)
            pass

    # --- startup ---
    def _use_saved_theme(self) -> None:
        """The theme you chose, kept with your settings - on every device (gui#84)."""
        theme = self.session.client_setting("tui", "theme") if self.session else None
        if theme and theme in self.available_themes and theme != self.theme:
            self.theme = theme

    def watch_theme(self, theme: str) -> None:
        """Remember a theme chosen in the command palette (ctrl+p)."""
        s = getattr(self, "session", None)           # Textual may call this before __init__ ends
        if s is not None and s.client_setting("tui", "theme") != theme:
            s.set_client_setting("tui", "theme", theme)

    @work(exclusive=True, group="startup", exit_on_error=False)
    async def start(self) -> None:
        passphrase = os.environ.get("UNSEENET_PASSPHRASE")
        error = ""
        kdf = {"ops": 1, "mem": 8192 * 1024} if os.environ.get("UNSEENET_INSECURE_FAST_KDF") == "1" else {}
        while self.session is None:
            if not passphrase:
                passphrase = await self.push_screen_wait(UnlockDialog(self.vault_path, error))
                if passphrase is None:
                    self.exit()
                    return
            self.set_status("Unlocking vault ...")
            try:
                vault = await asyncio.to_thread(Vault.open, self.vault_path, passphrase, **kdf)
                self.session = Session(vault, status=self.set_status)
            except VaultError as exc:
                error, passphrase = str(exc), None
        s = self.session
        if not s.has_identity() or not s.node:
            result = await self.push_screen_wait(SetupDialog(not s.has_identity(), not s.node,
                                                             s.known_nodes()))
            if result is None:
                self.exit()
                return
            how = result.get("how")
            if "node" in result:
                s.add_node(result["node"])
                for backup in result.get("backups", []):    # read from if the first is down
                    s.add_node(backup)
            if how == "new":
                name = result["display"].lower().replace(" ", "-")
                s.new_identity(name, result["display"])
                await self._first_words(passphrase)
            elif how in ("restore", "copy") and not await self._identity_from_elsewhere(how):
                self.exit()
                return
        self._use_saved_theme()
        self.runtime = ClientRuntime(s)          # startup, refresh and push (#139)
        s.on(self._on_event)
        self._update_subtitle()
        await self.show_offline()
        self.refresh_groups()
        self.set_interval(REFRESH_SECONDS, self.action_refresh)
        if getattr(self, "_restored", False):
            self._after_restore()
        elif s.words_reminder_due():           # UN-RFC 0050 4.2: gently, at most weekly
            s.note_words_reminded()
            self.notify("Your recovery words are not written down yet - they bring your "
                        "identity back if this stick is lost. Ctrl+P: Show recovery words.",
                        timeout=15)

    @work(group="known-nodes", exclusive=True, exit_on_error=False)
    async def update_known_nodes(self) -> None:
        """Node list, releases, renewals and names in the background (#139); quiet on failure."""
        done = await self.runtime.maintain()
        if done.release:                        # a newer release (#264)
            root = portable_root()
            how = ("Update Unseenet in the menu (Ctrl+P)"
                   if root is not None and not (root / "README-graphical.txt").exists()
                   else "see unseenet.org")
            self.notify(f"Unseenet {done.release} is ready: {how}.", timeout=20)
        if done.names:                          # names of groups and people (UN-RFC 0018, 0019)
            await self._render_groups()
            if self.current_group:
                self._render_threads()

    def _on_event(self, event: ev.Event) -> None:
        """The session's events this client acts on at once; the rest it reads itself."""
        if isinstance(event, ev.InboxReady):
            self._inbox_ready = True
            self._start_watches()
            self.update_known_nodes()
            self.ripen_on()
        elif isinstance(event, ev.Ripening) and event.level is not None:
            self._update_subtitle()
        elif isinstance(event, ev.Refreshed) or (isinstance(event, ev.GroupUpdated) and event.new):
            self._notify_soon()                                  # #4: anything for you?

    # --- notifications (#4): what is new comes from the core, telling is ours ---
    def _notify_soon(self) -> None:
        timer = getattr(self, "_notify_timer", None)
        if timer is not None:
            timer.stop()
        self._notify_timer = self.set_timer(1.0, self.check_notifications)

    @work(group="notify", exclusive=True, exit_on_error=False)
    async def check_notifications(self) -> None:
        from .identity_dialogs import on_bootable
        from .notify import AskDialog
        s = self.session
        if s is None or on_bootable():
            return
        n = s.notifications()
        if not n["count"] or n["settings"]["on"] is False:
            return
        if n["settings"]["on"] is None:
            if getattr(self, "_notify_asked", False):
                return
            self._notify_asked = True
            answer = await self.push_screen_wait(AskDialog())
            if answer == "never":
                s.set_notification_settings(on=False)
                return
            if answer != "yes":
                return
            n["settings"] = s.set_notification_settings(on=True)
        self._tell(n)

    def _tell(self, n: dict) -> None:
        from .notify import desktop_terminal, on_screen, sequences, text_of
        focused = bool(getattr(self, "app_focus", True))
        items = [i for i in n["items"] if not on_screen(i, self.current_group, focused)]
        if not items:
            return
        text = text_of({**n, "items": items})
        driver = getattr(self, "_driver", None)
        if desktop_terminal() and driver is not None:
            driver.write(sequences(f"Unseenet: {text}"))
        else:
            self.bell()
        self.notify(f"{text} - see For you", timeout=8)

    @work(group="export", exclusive=True, exit_on_error=False)
    async def action_export(self) -> None:
        """Export my data (#14, UN-RFC 0049 section 7): never keys; private messages only
        when chosen, and then the file encrypted with a passphrase."""
        from .export_dialog import ExportDialog, default_path
        s = self.session
        path = default_path(s.identity().fingerprint)
        choice = await self.push_screen_wait(ExportDialog(path))
        if not choice:
            return
        self.set_status("Exporting your data...")
        try:
            out = await s.export(path, private=choice["private"], passphrase=choice["passphrase"])
        except (SessionError, OSError) as exc:
            self.set_status(f"Not exported: {exc}")
            return
        what = (f"{out['articles']} articles, {out['files']} files, {out['sites']} sites"
                + (f", {out['private']} private messages - encrypted" if out["encrypted"] else ""))
        self.set_status(f"Saved {out['path']}")
        self.notify(f"Saved {out['path']}: {what}. {out['note']}", timeout=15)

    def action_toggle_notifications(self) -> None:
        s = self.session
        on = s.notification_settings()["on"] is True
        s.set_notification_settings(on=not on)
        self.notify("No more notifications." if on else
                    "Notifications on - they say only \"new in For you\" unless you choose otherwise.")

    def action_toggle_notification_content(self) -> None:
        s = self.session
        content = s.notification_settings()["content"]
        s.set_notification_settings(content=not content)
        self.notify("Notifications say only \"new in For you\"." if content else
                    "Notifications show who wrote and the start of the text - also on the lock "
                    "screen and in the system's notification history.", timeout=10)

    @work(group="ripening", exclusive=True, exit_on_error=False)
    async def ripen_on(self) -> None:
        """Ripen the key further while the client is open (UN-RFC 0025 section 7)."""
        if self.runtime is None or self.runtime.ripening_paused:
            return
        try:
            await self.runtime.ripen()
        except (SessionError, OSError, ValueError) as exc:
            self.set_status(f"Ripening stopped: {exc}")

    def _update_subtitle(self) -> None:
        s = self.session
        more = f"  (+{len(s.nodes) - 1} more)" if len(s.nodes) > 1 else ""
        lv = s.ripening().level                   # UN-RFC 0025 section 7: show the level
        ripe = f"  -  key level {lv}" if lv is not None else ""
        if ripe and self.runtime is not None and self.runtime.ripening_paused:
            ripe += " (ripening paused)"                                      # #358
        elif ripe and s.ripening_limit is not None:
            ripe += " (as ripe as this machine allows)"                       # #440
        self.sub_title = (f"{s.display_name()} ({s.identity().fingerprint[:4]})  -  {s.node_label(s.node)}{more}{ripe}")

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
        for kind, gid_ in (("feed", my_feed), ("site", my_site)):   # "My feed", "My site" (#495)
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
                    mine_text = Text(f"{mark} My {kind}" + (f" ({unread})" if unread else ""))
                    if unread:
                        mine_text.stylize("bold")
                    nodes[gid_] = tree.root.add_leaf(mine_text, data=gid_)
                else:
                    nodes[start] = tree.root.add_leaf(
                        Text(f"{mark} Start my {kind}", style="dim"), data=start)
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
        box = self.query_one("#groups-filter", Input)
        box.disabled = False
        box.add_class("shown")
        box.focus()

    @on(Input.Changed, "#groups-filter")
    def _groups_search(self, event: Input.Changed) -> None:
        self.group_query = event.value
        self.run_worker(self._render_groups(), exclusive=True, group="render-groups")

    @on(Input.Submitted, "#groups-filter")
    def _groups_search_done(self, event: Input.Submitted) -> None:
        self.query_one("#groups", Tree).focus()

    def _close_group_search(self) -> None:
        box = self.query_one("#groups-filter", Input)
        box.value = ""
        box.remove_class("shown")
        box.disabled = True
        self.query_one("#groups", Tree).focus()

    @on(Tree.NodeSelected, "#groups")
    def _group_selected(self, event: Tree.NodeSelected) -> None:
        if event.node.data == START_FEED:
            self.action_start_feed()
        elif event.node.data == START_SITE:
            self.action_start_site()
        elif isinstance(event.node.data, str):
            self.select_group(event.node.data)
            self._offer_pictures(event.node.data)

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
            table.add_row(flag, line, Text(self._who(m)), date, key=m.message_id)
        self._render_title()
        # the rows are fresh objects; an open read screen follows them, so it
        # shows an edited post and steps n/p from the right place (#137)
        for screen in self.screen_stack:
            if isinstance(screen, ReadScreen):
                fresh = self.messages.get(screen.msg.message_id)
                if fresh is not None and fresh is not screen.msg:
                    screen.show(fresh, keep_place=True)
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

    # --- chat view (UN-RFC 0008 section 5) ---
    def view_of(self, gid: str | None) -> str:
        return self.session.view_of(gid)

    def _apply_view(self, mode: str) -> None:
        chat = mode == "chat"
        self.query_one("#threads").display = not chat
        self.query_one("#article").display = not chat
        self.query_one("#chat").display = chat

    def _indented(self, m: Message, text: str, indent: int, first: bool = True) -> Text:
        """A message's text in the chat log: Markdown or plain (UN-RFC 0031), each
        line indented under the name."""
        rendered = (richtext.blocks(markup.body(text, m.content_type, m.kind))
                    if m.readable else Text(text))
        out = Text()
        for i, ln in enumerate(rendered.split("\n")):
            if i:
                out.append("\n")
            if i or first:
                out.append(" " * indent)
            out.append(ln)
        return out

    def _render_chat(self, msgs: list[Message]) -> None:
        log = self.query_one("#chat-log", RichLog)
        log.clear()
        mine = self.session.identity().key_text
        pinned = {p["id"] for p in self._pins_here(self.current_group)}
        changed = False
        counted: dict = {}
        for m in msgs:
            own = m.author_key == mine
            who = "You" if own else (self.session.identity_label(m.author_key, m.from_name)
                                     if m.author_key else "anonymous")
            when = m.date.astimezone().strftime("%H:%M") if m.date else "     "
            text = split_signature(m.body)[0].rstrip() if m.readable else f"({m.subject})"
            line = Text(f"{when} ", style="dim")
            line.append(who, style="bold green" if own else "bold")
            if not own and self.session.contact_name(m.author_key):
                line.append(" √", style="green")
            if not own and not m.private and self.session.is_bot(m.author_key):
                line.append(" bot", style="dim")             # UN-RFC 0030 section 6
            if getattr(m, "origin_network", ""):                 # UN-RFC 0049 section 5.1
                line.append(f" {ORIGIN_MARK} {origin_text(m)}", style="dim italic")
            if m.message_id in pinned:
                line.append(f" {PIN_MARK}", style="bold yellow")   # UN-RFC 0033 section 7.3
            if m.readable and m.kind != KIND_CHAT:      # a post in a chat stands out (UN-RFC 0028)
                line.append(": ")
                line.append(f"≡ {m.subject or '(no subject)'}", style="bold underline")
                line.append("  (a post - r answers it with a quote)", style="dim")
                line.append("\n")
                line.append(self._indented(m, text, len(when) + len(who) + 3))
            else:
                line.append(": ")
                line.append(self._indented(m, text, len(when) + len(who) + 3, first=False))
            if m.edited:
                line.append(f"  (edited {edited_when(m)})", style="dim")
            liked = self._reactions_line(m, counted)     # UN-RFC 0048 section 7.2
            if liked:
                line.append(f"\n{' ' * (len(when) + 1)}{liked}", style="dim")
            if m.readable and m.attachments:             # files on a chat line (#496)
                names = ", ".join(a.get("name") or ("picture" if a["type"].startswith("image/")
                                                    else a["type"]) for a in m.attachments)
                line.append(f"\n{' ' * (len(when) + 1)}+ {names}", style="dim")
                line.append("  (v to threads, f to read and save)", style="dim italic")
            log.write(line)
            if not self.session.is_read(m):
                self.session.mark_read(m, save=False)
                changed = True
        for p in self.pending.get(self.current_group, []):     # yours, on their way (#325)
            failed = p["state"] == "failed"
            line = Text("      ", style="dim")
            line.append("You", style="bold red" if failed else "dim")
            line.append(": " + p["text"], style="red" if failed else "dim")
            line.append("  (not sent)" if failed else "  (sending ...)", style="red" if failed else "dim italic")
            log.write(line)
        if changed:
            self._save_soon()

    @work(group="notices", exit_on_error=False)
    async def _send_notices(self, gid: str, msgid: str, subject: str, kind: str,
                            keys: list[str]) -> None:
        """Mention notices, after a random wait so they do not arrive with the post
        (UN-RFC 0032, section 4.1). Quitting before then skips them."""
        try:
            await self.session.send_mention_notices(gid, msgid, subject, kind, keys)
        except (SessionError, OSError, ConnectionError) as exc:
            self.notify(f"Mention notices not sent: {exc}", severity="warning")

    def action_toggle_notices_send(self) -> None:
        on = not self.session.notice_settings()["send"]
        self.session.set_notice_settings(send=on)
        self.notify("People you mention by name are told in their inbox (you can say no per "
                    "post)" if on else "People you mention are no longer told; they see it if "
                    "they follow the group")

    def action_toggle_notices_receive(self) -> None:
        on = not self.session.notice_settings()["receive"]
        self.session.set_notice_settings(receive=on)
        self.notify("Mentions of you in groups you do not follow show in For you" if on
                    else "Mention notices from others are no longer shown")
        self.refresh_groups()

    @work(group="dialog", exit_on_error=False)
    async def action_open_mentioned(self) -> None:
        """From a mention notice in For you: go to the post, or to its group first."""
        s, m = self.session, self.current_message
        if m is None or not s.is_notice(m):
            self.notify("Select a mention notice in For you first", severity="warning")
            return
        gid, msgid = m.mention_group, m.mention_of
        s.mark_read(m)
        if gid not in self.groups:
            try:
                name, fp = split_group_id(gid)
            except ValueError:
                return
            self.action_group_ref(f"#{name}@{fp}")
            return
        self.select_group(gid)
        for _ in range(100):
            await asyncio.sleep(0.05)
            if msgid in self.messages:
                break
        if msgid in self.messages:
            self.current_message = self.messages[msgid]
        else:
            self.notify("The post is not there any more - perhaps it was withdrawn", timeout=8)

    def action_toggle_private_ripe(self) -> None:
        """Hide private posts from strangers whose key is younger than level 1 (about
        an hour of ripening). Local only: nodes know nothing of it (UN-RFC 0025, 3.5)."""
        s = self.session
        hide = s.private_min_ripe() is None
        s.set_private_min_ripe(1 if hide else None)
        self.notify("Private posts from brand-new keys are hidden; contacts always come "
                    "through" if hide else "All private posts are shown again", timeout=6)
        self.run_worker(self._recheck_private(), group="private-ripe", exclusive=True,
                        exit_on_error=False)

    async def _recheck_private(self) -> None:
        await self.session.check_private_senders()
        if self.current_group == self.session.my_inbox or (self.current_group or "").startswith(CONTACT):
            self._render_threads()
        await self._render_groups()

    def action_toggle_markdown(self) -> None:
        """Send what you write as Markdown (the default) or as plain text (UN-RFC 0031)."""
        on = self.session.text_type() != markup.MARKDOWN_TYPE
        self.session.set_markdown(on)
        self.notify("You write Markdown: *emphasis*, **strong**, `code`, lists, :tada:" if on
                    else "You write plain text: it is sent and shown exactly as you type it")

    def action_toggle_view(self) -> None:
        gid = self.current_group
        if not gid or gid == self.session.my_inbox:
            self.notify("Private posts are always shown as a list; open a contact to chat",
                        severity="warning")
            return
        if gid in ACROSS:
            self.notify("For you and the timeline are always shown as they are", severity="warning")
            return
        order = ["threads", "feed", "chat"]                 # UN-RFC 0008 and 0034: chat goes back to threads
        if self.session.manifest_data(gid).get("view") == "pages":
            order = ["pages", "threads"]                    # a site (UN-RFC 0035)
        now = self.view_of(gid)
        new = order[(order.index(now) + 1) % len(order)] if now in order else order[0]
        self.session.set_view(gid, new)
        self.set_status(f"Shown as {new}  (v for the next)")
        self._render_threads()
        self._focus_view(gid)

    @on(ChatBox.Submitted, "#chat-input")
    def _chat_submit(self, event: ChatBox.Submitted) -> None:
        text = event.value.strip()
        if not text or not self.current_group or event.input.disabled:
            return
        event.input.value = ""
        files = self.chat_files.pop(self.current_group, [])
        self._show_chat_files(self.current_group)
        self.send_chat(self.current_group, text, files)

    async def _inbox_takes_files(self, inbox: str) -> bool:
        """Whether someone's inbox takes files (#497), from its manifest - read once."""
        s = self.session
        if not s.manifest_data(inbox):
            try:
                m = await s.fetch_manifest(inbox)
            except (SessionError, OSError, ConnectionError, NNTPError):
                m = None
            if m is not None:
                s.group_entry(inbox)["manifest"] = dict(m.raw)
        return isinstance(s.manifest_data(inbox).get("attachments"), dict)

    def _chat_takes_files(self, gid: str) -> bool:
        """Whether a chat line here may carry files (UN-RFC 0028 section 6.1, #496/#497):
        a group's manifest says so; a person's inbox unless its manifest says no."""
        if gid.startswith(CONTACT):
            try:
                data = self.session.manifest_data(personal_inbox_id(
                    crypto.key_from_text(gid[len(CONTACT):])))
            except crypto.CryptoError:
                return False
            return not data or isinstance(data.get("attachments"), dict)
        if not gid or gid in ACROSS:
            return False
        data = self.session.manifest_data(gid)
        g = self.groups.get(gid)
        gtype = data.get("type") or (g.type if g else None)
        return isinstance(data.get("attachments"), dict) and gtype in FILES_GROUP_TYPES

    def _show_chat_files(self, gid: str | None) -> None:
        """What goes with the next chat line, under the chat input."""
        box = self.query_one("#chat-input", ChatBox)
        files = self.chat_files.get(gid or "", [])
        box.border_subtitle = (f"+ {len(files)} file{'s' if len(files) > 1 else ''}: "
                               + ", ".join(Path(f).name for f in files)
                               + "  (Ctrl+F changes)") if files else ""

    @work(group="dialog", exit_on_error=False)
    async def action_attach_to_chat(self) -> None:
        """Files to send with the next chat line in this group (#496)."""
        gid = self.current_group or ""
        if gid.startswith(CONTACT) and not await self._inbox_takes_files(personal_inbox_id(
                crypto.key_from_text(gid[len(CONTACT):]))):
            self.notify("They take no files in private chats", severity="warning")
            return
        if not self._chat_takes_files(gid):
            self.notify("This group takes no files - its owner can allow them",
                        severity="warning")
            return
        files = await self.push_screen_wait(InputDialog(
            "Files with your next chat line",
            "Paths, comma between. Pictures are cleaned of their metadata - place, camera, "
            "time - before they are sent. Empty: none.",
            "~/garden.jpg, ~/map.pdf", ", ".join(self.chat_files.get(gid, []))))
        if files is None:
            return
        self.chat_files[gid] = [p.strip() for p in files.split(",") if p.strip()]
        self._show_chat_files(gid)
        self.query_one("#chat-input").focus()

    @work(group="post", exit_on_error=False)
    async def send_chat(self, gid: str, text: str, files: list[str] | None = None) -> None:
        self.set_status("Sending (computing postage) ...")
        waiting = [p for p in self.pending.get(gid, []) if p["state"] != "failed"]
        shown = text + (f"  (+ {len(files)} file{'s' if len(files) > 1 else ''})" if files else "")
        entry = {"text": shown, "state": "sending"}          # shown at once, dimmed (#325)
        self.pending[gid] = waiting + [entry]
        if self.current_group == gid:
            self._render_threads()
        try:
            if gid.startswith(CONTACT):
                prepared = (self.session.prepare_files([Path(p).expanduser() for p in files])
                            if files else None)       # encrypted with the line (#497)
                await self.session.send_chat(gid, text, files=prepared)
            else:
                if not await self._accept_portals(gid):
                    self.query_one("#chat-input", ChatBox).value = text
                    self.set_status("")
                    self.pending[gid].remove(entry)
                    if self.current_group == gid:
                        self._render_threads()
                    return
                prepared = (self.session.prepare_files([Path(p).expanduser() for p in files])
                            if files else None)       # cleaned and checked here (#149, #496)
                msgid = await self.session.send_chat(gid, text, files=prepared)   # UN-RFC 0028
                if self.session.was_submitted(gid, msgid):
                    self.notify("Sent to the moderators for approval", timeout=8)
                if self.session.notice_settings()["send"] and self.session.may_notify(gid):
                    found = self.session.mentioned("", text)[:MAX_MENTION_NOTICES]
                    if found:
                        self._send_notices(gid, msgid, "", KIND_CHAT, [k for _, k in found])
                        self.notify("Told: " + ", ".join(n for n, _ in found)
                                    + "  (turn off in Ctrl+P)", timeout=6)
                await self.session.fetch_group(gid)
        except (SessionError, OSError, ConnectionError, ValueError) as exc:
            entry["state"] = "failed"
            self.notify(f"Not sent: {exc}", severity="error", timeout=8)
            self.query_one("#chat-input", ChatBox).value = text       # give the text back
            if files:
                self.chat_files[gid] = files                        # and the files
                self._show_chat_files(gid)
            if self.current_group == gid:
                self._render_threads()
            return
        finally:
            if entry["state"] != "failed" and entry in self.pending.get(gid, []):
                self.pending[gid].remove(entry)                     # the real line is back
        self.set_status("")
        if self.current_group == gid:
            self._render_threads()

    def _reactions_line(self, m: Message, cache: dict | None = None) -> str:
        """The reactions under ``m`` as text (UN-RFC 0048 section 7.2), "" for none.
        ``cache``: {group: all its counts}, so a list counts each group once."""
        s = self.session
        if not m.readable:                  # private ones count too (UN-RFC 0048 4.3, #9)
            return ""
        if cache is None:
            counts = s.reactions_on(m)
        else:
            if m.group not in cache:
                cache[m.group] = s.all_reactions(m.group)
            counts = cache[m.group].get(m.message_id)
        return reactions_text(counts, counts.of(s.identity().key_text) if counts else None)

    def _who(self, m: Message) -> str:
        if m.to_key and self.current_group == self.session.my_inbox:
            return f"You → {self._key_label(m.to_key)}"
        if m.from_name == "You" and m.author_key == self.session.identity().key_text:
            return "You"
        if not m.author_key:
            return m.from_name or "anonymous"
        # your petname, else a registered name, else what they call themselves (UN-RFC 0019)
        who = self.session.identity_label(m.author_key, m.from_name)
        # a key whose profile says it posts automatically (UN-RFC 0030 section 6)
        return who + " (bot)" if not m.private and self.session.is_bot(m.author_key) else who

    def _key_label(self, key_text: str) -> str:
        """A contact's name, else the start of the key and its fingerprint."""
        if self.session.contact_name(key_text) or naming.names_of(self.session, key_text):
            return self.session.identity_label(key_text)
        try:
            fp = crypto.fingerprint(crypto.key_from_text(key_text))[:4]
        except crypto.CryptoError:
            fp = "????"
        return f"{key_text[8:20]}… ({fp})"

    def _render_title(self) -> None:
        gid = self.current_group
        if not gid:
            return
        if gid == FOR_YOU:
            c = self.session.for_you_counts()
            self.query_one("#threads-title", Static).update(
                f"{REPLY_MARK} For you  ·  {REPLY_MARK} {c.replies} unread replies to your posts, "
                f"{MENTION_MARK} {c.mentions} unread posts that name you  ·  from all your groups")
            return
        if gid == TIMELINE:
            n = len(self.session.feeds_followed())
            self.query_one("#threads-title", Static).update(
                f"{FEED_MARK} Timeline  ·  {n} feed{'s' if n != 1 else ''} you follow, newest first, "
                "nothing reordered  ·  r comments in the post's own feed")
            return
        if gid.startswith(CONTACT):
            key = gid[len(CONTACT):]
            msgs = self.session.conversation(key, KIND_CHAT)
            self.query_one("#threads-title", Static).update(
                f"» chat with {self.session.contact_name(key) or key[:20]}  ·  {len(msgs)} messages, "
                f"{self.session.unread_from(key, KIND_CHAT)} unread  ·  only the two of you can read "
                "them  ·  n: write a post instead (posts are in Private posts)")
            return
        g = self.groups.get(gid)
        total = len(self.session.cache.get(gid, {}))
        unread = self.session.unread_count(gid)
        kind = {"closed": "closed, encrypted", "moderated": "moderated", "channel": "channel",
                "open": "open", "inbox": "inbox - only the owner can read"}.get(g.type if g else None, "")
        name = self._group_label(g).strip() if g else gid
        fast = "  ·  fast delivery" if self.session.manifest_data(gid).get("low_latency") else ""
        rules = "  ·  ? rules" if self.session.manifest_data(gid).get("rules") else ""
        hidden = self.session.hidden_by_blocks(gid)
        blocked = ("  ·  " + "; ".join(f"{n} hidden: blocked in {h}.*: {r}" for h, r, n in hidden[:2])
                   if hidden else "")
        self.query_one("#threads-title", Static).update(
            f"{name}  ·  {kind}{fast}  ·  {total} posts, {unread} unread{rules}{blocked}")

    def article(self, msg: Message) -> tuple[str, Text]:
        """The header (markup) and the body of a message, as the reader shows them."""
        date = msg.date.astimezone().strftime("%A %d %B %Y %H:%M") if msg.date else ""
        if getattr(msg, "origin_network", ""):               # UN-RFC 0049 section 5.1
            date = (f"posted {date}\n{ORIGIN_MARK} {escape(origin_text(msg))} "
                    "[dim](the date it was first published is the poster's claim)[/dim]")
        if msg.edited:                                       # UN-RFC 0042, 4.2
            date += f"  (edited {edited_when(msg)})"
        if msg.to_key:
            who = f"you  ·  to [b]{escape(self._key_label(msg.to_key))}[/b]"
        elif msg.private and msg.readable and not msg.sender_key:
            who = "anonymous (the sender did not identify themselves)"
        else:
            petname = self.session.contact_name(msg.author_key)
            registered = " ".join("@" + c.name for c in naming.names_of(self.session, msg.author_key))
            who = (f"{escape(msg.from_name)}  ·  key {escape(msg.author_key[:24])}…  ({msg.author_fp})"
                   + ("  ·  verified" if msg.sender_key else "")
                   + (f"  ·  registered [b]{escape(registered)}[/b]" if registered else "")
                   + (f"  ·  your contact [b]{escape(petname)}[/b]" if petname else ""))
        note = ("\n@ private message you sent - only they can read the copy on the node"
                if msg.to_key else
                "\n@ private message - only you can read it" if msg.private
                else "\n∩ end-to-end encrypted" if msg.encrypted else "")
        if not msg.private and self.session.write_permission(
                msg.group, reply_to=msg, kind=None).code == "comments_closed":
            note += "\n✕ Comments closed"                  # where Reply would be (#181)
        head = f"[b]{escape(msg.subject)}[/b]\nFrom: {who}\nDate: {date}{note}"
        if msg.readable:
            text, sig = split_signature(msg.body)
            # Markdown or plain, as the message says (UN-RFC 0031); the signature never is
            body = Text("\n")
            body.append(richtext.blocks(markup.body(text, msg.content_type, msg.kind)))
            if sig is not None:
                body.append("\n\n")
                body.append(SIG_SEPARATOR + sig, style="dim")
            if msg.attachments:                              # UN-RFC 0027 section 9.2
                body.append("\n\n")
                body.append(attachments_text(msg.attachments))
            liked = self._reactions_line(msg)                # UN-RFC 0048 section 7.2
            may = self.session.react_permission(msg.group, msg)    # private too (#9)
            if liked or may:
                body.append("\n\n")
                if liked:
                    body.append(liked)
                    body.append("   ")
                if may:
                    body.append("l reacts", style="dim italic")
        else:
            body = Text("\n" + (msg.error or msg.subject))
        return head, body

    def neighbour(self, step: int) -> Message | None:
        """Move to the next (+1) or previous (-1) message in the list."""
        table = self.query_one("#threads", DataTable)
        row = table.cursor_row + step
        if not table.row_count or not 0 <= row < table.row_count:
            return None
        table.move_cursor(row=row)
        return self.messages.get(table.coordinate_to_cell_key((row, 0)).row_key.value)

    @on(DataTable.RowSelected, "#threads")
    def _open(self, event: DataTable.RowSelected) -> None:
        """Enter on a message: read it on the whole screen. On the Pinned header: fold
        the section away, or open it again."""
        if event.row_key.value == "pins:head":
            self.action_toggle_pins()
            return
        msg = self.messages.get(event.row_key.value)
        if msg is not None:
            self.current_message = msg
            self.push_screen(ReadScreen(msg))

    def action_next_unread(self) -> None:
        """Space: the next unread post, entry or comment in the list (#490), as in the
        reader and in the graphical client."""
        table = self.query_one("#threads", DataTable)
        keys = [k.value for k in table.rows]
        at = table.cursor_row
        order = keys[at + 1:] + keys[:max(0, at)]
        for key in order:
            m = self.messages.get(key)
            if m is not None and not self.session.is_read(m):
                table.move_cursor(row=table.get_row_index(key))
                return
        self.notify("Nothing unread here", timeout=3)

    def action_read(self) -> None:
        if self.current_message is None:
            self.notify("Select a post first", severity="warning")
            return
        if self.session.is_notice(self.current_message):     # go to the post it is about
            self.action_open_mentioned()
            return
        self.push_screen(ReadScreen(self.current_message))

    @on(DataTable.RowHighlighted, "#threads")
    def _row(self, event: DataTable.RowHighlighted) -> None:
        msg = self.messages.get(event.row_key.value)
        if msg is None or not self.screen_stack:      # the app is closing (#137)
            return
        self.current_message = msg
        self.refresh_bindings()
        head, body = self.article(msg)
        # not there in the chat view, or while the app closes (#137)
        for widget in self.query("#article-head"):
            widget.update(head)
        for widget in self.query("#article-body"):
            widget.update(body)
        if isinstance(self.screen, ReadScreen):
            self.screen.show(msg)
        if not self.session.is_read(msg):
            self.session.mark_read(msg, save=False)
            self._save_soon()
            for table in self.query("#threads"):
                table.update_cell(msg.message_id, "flag", " ")
            self._render_title()

    @work(exclusive=True, group="save", exit_on_error=False)
    async def _save_soon(self) -> None:
        await asyncio.sleep(2)
        self.session.save()
        await self._render_groups()

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
            self.compose_private(target, f"Private reply to {m.from_name}", subject, quote,
                                 owner_key=key, references=[*m.references, m.message_id])
            return
        self.compose_post(g, subject, quote, references=[*m.references, m.message_id])

    @work(group="post", exit_on_error=False)
    async def compose_private(self, target: str, heading: str, subject: str = "", quote: str = "",
                              owner_key: bytes | None = None,
                              references: list[str] | None = None) -> None:
        g = GroupInfo(target, type="inbox")
        dlg = ComposeDialog(
            g, subject, quote, bool(references), heading=heading + "  @ only they can read it",
            signature=self.session.signature(), files_allowed=await self._inbox_takes_files(target))
        result = await self.push_screen_wait(dlg)
        if not result:
            return
        subject, body, _ = result
        self.set_status("Sending (computing postage) ...")
        try:
            files = (self.session.prepare_files([Path(p).expanduser() for p in dlg.files])
                     if dlg.files else None)          # encrypted with the post (#497)
            await self.session.send_private(target, subject, body, owner_key=owner_key,
                                            references=references, files=files)
        except (SessionError, OSError, ConnectionError, ValueError) as exc:
            self.notify(f"Not sent: {exc}", severity="error", timeout=8)
            return
        self.notify("Private message sent")
        self.set_status("")
        if self._current_contact():
            self._render_threads()

    @work(group="dialog", exit_on_error=False)
    async def action_message(self) -> None:
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
                "Start my feed", s.personal_group_warning("feed"), "Start my feed")):
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
                "Start my site", s.personal_group_warning("site"), "Start my site")):
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

    # --- recovery words and copying (UN-RFC 0050, #15) ---
    async def _first_words(self, passphrase: str | None) -> None:
        """Right after a new identity: its words, to write down now or later (4.2)."""
        from .identity_dialogs import ShowWordsDialog
        s = self.session
        if not passphrase:
            return
        try:
            words = await asyncio.to_thread(s.recovery_words, passphrase)
        except SessionError:
            return
        result = await self.push_screen_wait(ShowWordsDialog(
            s.default_identity_name(), s.identity().fingerprint, words, first_time=True,
            vault_path=self.vault_path))
        if result == "written":
            s.note_words_written()
        else:
            s.note_words_reminded()

    async def _identity_from_elsewhere(self, how: str) -> bool:
        """At first start: restore from words, or get the identity from another device.
        False if the user gave up."""
        from .identity_dialogs import RestoreDialog
        s = self.session
        while True:
            if how == "restore":
                got = await self.push_screen_wait(RestoreDialog())
                if got is None:
                    return False
                try:
                    s.restore_identity(got["words"], display=got["display"] or None)
                except SessionError as exc:
                    self.notify(str(exc), severity="error", timeout=10)
                    continue
                self._restored = True
                return True
            if await self._receive_copy():
                return True
            return False

    @work(group="dialog", exit_on_error=False)
    async def action_show_words(self) -> None:
        """Show recovery words: behind the vault passphrase, every time (4.2)."""
        from .identity_dialogs import PassphraseDialog, ShowWordsDialog
        s, error = self.session, ""
        while True:
            pw = await self.push_screen_wait(PassphraseDialog(error))
            if pw is None:
                return
            try:
                words = await asyncio.to_thread(s.recovery_words, pw)
                break
            except SessionError as exc:
                error = str(exc)
        result = await self.push_screen_wait(ShowWordsDialog(
            s.default_identity_name(), s.identity().fingerprint, words, vault_path=self.vault_path))
        if result == "written":
            s.note_words_written()
            self.notify("Noted: your recovery words are written down.")

    @work(group="dialog", exit_on_error=False)
    async def action_restore_words(self) -> None:
        """Restore an identity from its words into this vault; it becomes the default."""
        from .identity_dialogs import RestoreDialog
        got = await self.push_screen_wait(RestoreDialog())
        if got is None:
            return
        try:
            name, ident, new = self.session.restore_identity(got["words"], display=got["display"] or None)
        except SessionError as exc:
            self.notify(str(exc), severity="error", timeout=10)
            return
        self.notify(f"{name} is back{'' if new else ' (it was already here)'} - reading it from "
                    "the network ...")
        self._update_subtitle()
        self._after_restore()

    @work(group="restore", exit_on_error=False)
    async def _after_restore(self) -> None:
        """Read the restored identity's state, name and profile from the network, and say
        which closed groups did not come back - offering to ask their owners (4.4, 4.5)."""
        from .identity_dialogs import ClosedGroupsDialog
        s = self.session
        self._restored = False
        found = await s.restore_from_network()
        self.refresh_groups()
        ask = await self.push_screen_wait(ClosedGroupsDialog(found["closed_groups"], found["state"]))
        for gid in ask or []:
            try:
                await s.ask_to_be_invited(gid)
            except (SessionError, OSError, ConnectionError, NNTPError) as exc:
                self.notify(f"{s.group_label(gid)}: {exc}", severity="error")
        if ask:
            self.notify(f"Asked {len(ask)} owner(s) to invite you again.")

    @work(group="dialog", exit_on_error=False)
    async def action_copy_to(self) -> None:
        """Copy to another device (section 3): choose, show the code, check the words."""
        from .identity_dialogs import CopyChoiceDialog, CopyOfferDialog
        s = self.session
        ids = [(n, e.get("display", n)) for n, e in s.data["identities"].items()]
        choice = await self.push_screen_wait(CopyChoiceDialog(ids))
        if not choice:
            return
        self.notify("Opening a one-time onion service ...")
        try:
            offer = await s.start_copy(choice["names"], choice["group_secrets"])
        except SessionError as exc:
            self.notify(str(exc), severity="error", timeout=12)
            return
        dialog = CopyOfferDialog(offer.code, offer.qr, offer.expires)
        answer: asyncio.Future = asyncio.get_running_loop().create_future()
        self.push_screen(dialog, lambda r: answer.done() or answer.set_result(r))
        check = asyncio.ensure_future(offer.wait_for_check())
        await asyncio.wait({check, answer}, return_when=asyncio.FIRST_COMPLETED)
        if answer.done():                        # closed before the other device came
            check.cancel()
            await offer.cancel()
            return
        words = check.result()
        if words is None:
            dialog.dismiss(None)
            self.notify(offer.error or f"The copy {offer.state}.", severity="error", timeout=12)
            return
        dialog.show_check(words)
        match = await answer
        if match is None:
            await offer.cancel()
            self.notify("Copy cancelled: nothing was sent.")
            return
        result = await offer.confirm(bool(match))
        if result["state"] == "done":
            self.notify("Copied" + (f": {', '.join(result['added'])}" if result["added"]
                                    else " - the other device had it already."), timeout=10)
        else:
            self.notify(result["error"] or f"The copy {result['state']}.",
                        severity="warning" if result["state"] == "cancelled" else "error", timeout=12)

    @work(group="dialog", exit_on_error=False)
    async def action_copy_from(self) -> None:
        if await self._receive_copy():
            self._update_subtitle()
            self.refresh_groups()

    async def _receive_copy(self) -> bool:
        """Get identities from another device by its code (section 3; a terminal has no
        camera, so the code is typed). True when something came."""
        from .identity_dialogs import CopyCodeDialog, CopyWaitDialog
        s = self.session
        while True:
            code = await self.push_screen_wait(CopyCodeDialog())
            if code is None:
                return False
            self.notify("Connecting to the other device over Tor ...")
            try:
                got = await s.receive_copy(code)
            except SessionError as exc:
                self.notify(str(exc), severity="error", timeout=12)
                continue
            waiting = CopyWaitDialog(got.check_words)
            cancelled = asyncio.Event()
            self.push_screen(waiting, lambda r: cancelled.set())
            finish = asyncio.ensure_future(got.finish())
            stop = asyncio.ensure_future(cancelled.wait())
            await asyncio.wait({finish, stop}, return_when=asyncio.FIRST_COMPLETED)
            if not finish.done():
                finish.cancel()
                await got.cancel()
                return False
            stop.cancel()
            waiting.dismiss(None)
            try:
                added = finish.result()
            except SessionError as exc:
                self.notify(str(exc), severity="error", timeout=12)
                continue
            self.notify("Got " + (", ".join(added) or "nothing new") + " from the other device.",
                        timeout=10)
            self._restored = bool(added)
            return True

    def action_about(self) -> None:
        self.push_screen(AboutDialog())

    async def action_update(self) -> None:
        """Run the updater (unseenet.update) on the console; quit if it updated us."""
        import subprocess
        import sys
        root = portable_root()
        if root is None:
            return
        cmd = ([sys.executable, "update"] if getattr(sys, "frozen", False)
               else [sys.executable, "-m", "unseenet.update"]) + ["--media", str(root), "--pause"]
        from unseenet import __version__
        if ".dev" in __version__:          # a development build follows development builds
            cmd += ["--channel", "dev"]
        with self.suspend():
            code = subprocess.run(cmd).returncode
        if code == 0:                    # the files under this program are new: start again
            if self.session:
                self.session.save()
                await self.session.close()
            self.exit(message="Unseenet was updated. Start it again to use the new version.")

    @work(group="dialog", exit_on_error=False)
    async def action_node_info(self) -> None:
        """Your nodes: add, remove, choose the default, see who runs them."""
        s = self.session
        while True:
            self.set_status("Asking your nodes ...")
            infos: dict = {}

            async def ask(address: str) -> None:
                try:
                    infos[address] = await asyncio.wait_for(s.node_info(address), 60)
                except (SessionError, OSError, ConnectionError, NNTPError, ValueError,
                        asyncio.TimeoutError, asyncio.IncompleteReadError):
                    infos[address] = None
            await asyncio.gather(*(ask(n) for n in s.nodes))
            self.set_status("")
            counts = {n: sum(1 for g in s.data["subscribed"] if s.node_for(g) == n) for n in s.nodes}
            result = await self.push_screen_wait(NodesDialog(
                s.nodes, infos, counts, s.known_nodes(),
                labels={n: s.node_label(n) for n in s.nodes}))
            if not result:
                return
            action, address = result
            if action == "add":
                value = await self.push_screen_wait(AddNodeDialog(s.known_nodes(), s.nodes))
                if value:
                    s.add_node(value)
                    self.notify(f"Added {s.node_label(value)} - its groups are now in the group list (g)")
                    self._start_watches()
                    self._update_subtitle()
                    self.refresh_groups()
            elif action == "default":
                s.add_node(address, default=True)
                self._update_subtitle()
                self.notify(f"New groups and your inbox now go to {s.node_label(address)}")
            elif action == "remove":
                if not await self.push_screen_wait(ConfirmDialog(
                        "Stop using this node?",
                        f"{address}\n\nGroups you read from it move to your default node "
                        "(if it carries them). Nothing is deleted on the node itself.",
                        "Remove", danger=True)):
                    continue
                try:
                    s.remove_node(address)
                except SessionError as exc:
                    self.notify(str(exc), severity="error")
                    continue
                self._watching.discard(address)
                self._update_subtitle()
                self.refresh_groups()
            elif action == "info":
                if infos.get(address) is None:
                    self.notify(f"{s.node_label(address)} does not answer right now", severity="warning")
                    continue
                if await self.push_screen_wait(NodeInfoDialog(infos[address], address)):
                    break
        info = infos[address]
        try:
            operator = crypto.key_from_text(info["operator"])
        except (KeyError, crypto.CryptoError):
            self.notify("The node gives no valid operator key", severity="error")
            return
        self.compose_private(info["contact"], f"Message to the operator of {info.get('name', 'this node')}",
                             owner_key=operator)

    async def _accept_portals(self, gid: str) -> bool:
        """Before the first post in a group with portals: say where it will also appear
        (UN-RFC 0023 section 3.5). True if there is nothing to accept or the user agreed."""
        text = self.session.portal_warning(gid)
        if text is None:
            return True
        if not await self.push_screen_wait(ConfirmDialog(
                "This group is also published outside Tor", text, "Post anyway")):
            return False
        self.session.accept_portals(gid)
        return True

    @work(group="post", exit_on_error=False)
    async def compose_post(self, g: GroupInfo, subject: str = "", quote: str = "",
                           references: list[str] | None = None) -> None:
        data = self.session.manifest_data(g.group_id)
        dlg = ComposeDialog(
            g, subject, quote, bool(references), signature=self.session.signature(),
            has_rules=bool(data.get("rules")),
            files_allowed=bool(data.get("attachments")) and g.type != "inbox"
            and (g.type != "closed" or bool(self.session.group_entry(g.group_id).get("keys"))))
        result = await self.push_screen_wait(dlg)
        if not result:
            return
        subject, body, notify = result
        if not await self._accept_portals(g.group_id):
            self.notify("Not sent", timeout=4)
            return
        files = None
        if dlg.files:                                    # cleaned and checked here (#149)
            try:
                files = self.session.prepare_files([Path(p).expanduser() for p in dlg.files])
            except (SessionError, OSError, ValueError) as exc:
                self.notify(f"Not sent: {exc}", severity="error", timeout=8)
                return
            for f in files:
                if getattr(f, "note", None):
                    self.notify(f.note, timeout=6)
        self.set_status("Sending (computing postage) ...")
        try:
            msgid = await self.session.post(g.group_id, subject, body, references, files=files)
        except (SessionError, OSError, ConnectionError) as exc:
            self.notify(f"Not sent: {exc}", severity="error", timeout=8)
            return
        who = (f"{len(notify)} " + ("person is" if len(notify) == 1 else "people are")
               if notify else "")
        if self.session.was_submitted(g.group_id, msgid):
            self.notify("Sent to the moderators. It appears in the group when one of them "
                        "approves it" + (f"; then {who} told." if notify else "."), timeout=10)
        else:
            self.notify("Sent" + (f" - {who} told in a few minutes" if notify else ""))
        if notify:                    # after approval, for a post the moderators have
            self._send_notices(g.group_id, msgid, subject, KIND_POST, notify)
        self.select_group(g.group_id)

    # --- writing pages of a site (UN-RFC 0035 section 9.2, #212) ---
    def _is_site(self, gid: str) -> bool:
        """A site shown as its pages (v shows it as threads, where n writes posts)."""
        return bool(gid) and gid in self.groups and self.view_of(gid) == "pages" \
            and self.session.manifest_data(gid).get("view") == "pages"

    def _writes_pages(self, gid: str) -> bool:
        data, me = self.session.manifest_data(gid), self.session.identity().key_text
        return me in (data.get("owner"), *data.get("moderators", []), *data.get("posters", []))

    def _page_now(self, gid: str, path: str) -> dict | None:
        return next((p for p in self.session.site(gid)["pages"] if p["path"] == path), None)

    @work(group="post", exit_on_error=False)
    async def compose_page(self, g: GroupInfo, m: Message | None = None) -> None:
        """A new page (``m`` None) or a new version of page ``m``; the site is read first,
        so the new version names the current one."""
        from unseenet.core import pages as pg
        s, gid = self.session, g.group_id
        data = s.manifest_data(gid)
        label = s.group_label(gid)
        dlg = PageDialog(
            f"Edit /{m.page} in {label} - readers see the new version" if m else
            f"New page in {label}", gid, path=m.page if m else "",
            title=m.subject if m else "", text=m.body if m and m.readable else "",
            files_allowed=bool(data.get("attachments")))
        result = await self.push_screen_wait(dlg)
        if not result:
            return
        path, title, text = result
        with contextlib.suppress(SessionError, OSError, ConnectionError, NNTPError):
            await s.fetch_group(gid)                 # the site as it is now (section 9.2)
        site = s.site(gid)
        paths = [p["path"] for p in site["pages"]]
        most = data.get("max_pages", pg.MAX_PAGES_DEFAULT)
        if m is None and path in paths:
            if not await self.push_screen_wait(ConfirmDialog(
                    "The page is there", f"/{path} is a page of this site already. Write a new "
                    "version of it?", "Write over it")):
                return
        elif path not in paths and len(paths) >= most:
            if not await self.push_screen_wait(ConfirmDialog(
                    "The site is full", f"The site keeps at most {most} pages; a new one pushes "
                    "the last out (UN-RFC 0035 section 9.2). Write it anyway?", "Write it")):
                return
        files = None
        if dlg.files:
            try:
                files = s.prepare_files([Path(p).expanduser() for p in dlg.files])
            except (SessionError, OSError, ValueError) as exc:
                self.notify(f"Not published: {exc}", severity="error", timeout=8)
                return
        self.set_status("Publishing the page (computing postage) ...")
        try:
            msgid = await s.put_page(gid, path, text, title, files=files)
        except (SessionError, OSError, ConnectionError) as exc:
            self.set_status("")
            self.notify(f"Not published: {exc}", severity="error", timeout=8)
            return
        self.set_status("")
        self.notify(f"Published /{path}", timeout=4)
        self._open_at = msgid
        with contextlib.suppress(SessionError, OSError, ConnectionError, NNTPError):
            await s.fetch_group(gid)
        self.select_group(gid)

    def _selected_page(self) -> tuple[str, Message] | None:
        gid, m = self.current_group or "", self.current_message
        if not self._is_site(gid) or m is None or not m.page:
            self.notify("Select a page of a site first", severity="warning")
            return None
        if not self._writes_pages(gid):
            self.notify("Only the site's writers write its pages", severity="warning")
            return None
        return gid, m

    @work(group="post", exit_on_error=False)
    async def action_page_previous(self) -> None:
        """Publish the selected page's previous version again (UN-RFC 0035 section 10.4)."""
        sel = self._selected_page()
        if sel is None:
            return
        gid, m = sel
        s = self.session
        p = self._page_now(gid, m.page)
        prev = p and p["previous"]
        if not prev or not prev.readable:
            self.notify("This page has no earlier version to go back to", severity="warning")
            return
        if not await self.push_screen_wait(ConfirmDialog(
                "Previous version", f"Publish the earlier version of /{m.page} again "
                f"(\"{prev.subject}\")? It becomes the page in force.", "Go back")):
            return
        self.set_status("Publishing the earlier version (computing postage) ...")
        try:
            await s.put_page(gid, m.page, prev.body, prev.subject, content_type=prev.content_type)
        except (SessionError, OSError, ConnectionError) as exc:
            self.set_status("")
            self.notify(f"Not changed: {exc}", severity="error", timeout=8)
            return
        self.set_status("")
        self.notify(f"/{m.page} is back at its earlier version", timeout=4)
        with contextlib.suppress(SessionError, OSError, ConnectionError, NNTPError):
            await s.fetch_group(gid)
        self.select_group(gid)

    @work(group="post", exit_on_error=False)
    async def action_page_remove(self) -> None:
        """Remove the selected page from its site (``Page-Removed``, UN-RFC 0035)."""
        sel = self._selected_page()
        if sel is None:
            return
        gid, m = sel
        if not await self.push_screen_wait(ConfirmDialog(
                "Remove the page", f"Remove /{m.page} from the site? Readers no longer see it; "
                "people may have kept copies.", "Remove", danger=True)):
            return
        self.set_status("Removing the page (computing postage) ...")
        try:
            await self.session.remove_page(gid, m.page)
        except (SessionError, OSError, ConnectionError) as exc:
            self.set_status("")
            self.notify(f"Not removed: {exc}", severity="error", timeout=8)
            return
        self.set_status("")
        self.notify(f"/{m.page} removed", timeout=4)
        self.current_message = None
        with contextlib.suppress(SessionError, OSError, ConnectionError, NNTPError):
            await self.session.fetch_group(gid)
        self.select_group(gid)

    def _editable(self, gid: str) -> Message | None:
        """What e edits: the selected post if it is yours; in a chat, your last line."""
        s = self.session
        if self.view_of(gid) == "chat":
            mine = [m for m in s.visible(sorted(s.cache.get(gid, {}).values(),
                                                key=lambda m: m.number)) if s.may_edit(m)]
            return mine[-1] if mine else None
        m = self.current_message
        return m if m is not None and m.group == gid and s.may_edit(m) else None

    def _withdrawable(self, gid: str) -> Message | None:
        """What D withdraws: the selected post or comment if you may; in a chat, where
        no line is selected, your last line - as e edits it."""
        s = self.session
        if self.view_of(gid) == "chat":
            me = s.identity().key_text
            mine = [m for m in s.visible(sorted(s.cache.get(gid, {}).values(),
                                                key=lambda m: m.number))
                    if m.author_key == me and s.may_cancel(m)]
            return mine[-1] if mine else None
        m = self.current_message
        return m if m is not None and s.may_cancel(m) else None

    @work(group="dialog", exit_on_error=False)
    async def action_edit(self) -> None:
        """Edit your own post or chat line (UN-RFC 0042): a new version in its place."""
        gid = self.current_group or ""
        cur = self.current_message
        if self._is_site(gid) and cur is not None and cur.page and gid in self.groups:
            if not self._writes_pages(gid):      # any writer edits a page (0035, 9.2)
                self.notify("Only the site's writers write its pages", severity="warning")
                return
            self.compose_page(self.groups[gid], cur)
            return
        m = self._editable(gid)
        g = self.groups.get(gid)
        if m is None or g is None:
            self.notify("Select one of your own posts to edit", severity="warning")
            return
        s = self.session
        p = s.write_permission(gid, reply_to=m if m.references else None, kind=m.kind)
        if not p and p.code != "channel_thread":
            self.notify(p.reason, severity="warning")
            return
        warn = "People may already have read the earlier version, and may keep it."
        if m.kind == KIND_CHAT:
            text = await self.push_screen_wait(InputDialog(
                "Edit your chat line", warn, "Your chat line", split_signature(m.body)[0].rstrip()))
            if not text or text.strip() == split_signature(m.body)[0].strip():
                return
            subject, body, notify = "", text.strip(), []
        else:
            result = await self.push_screen_wait(ComposeDialog(
                g, m.subject, m.body, is_reply=True,
                heading=f"Edit your post in {s.group_label(gid)} - {warn}"))
            if not result:
                return
            subject, body, notify = result
        before = {k for _, k in s.mentioned(m.subject, m.body)}
        notify = [k for k in notify if k not in before]   # only newly mentioned (0042, 4.3)
        self.set_status("Sending the new version (computing postage) ...")
        try:
            msgid = await s.edit(m, subject, body)
        except (SessionError, OSError, ConnectionError) as exc:
            self.notify(f"Not changed: {exc}", severity="error", timeout=8)
            return
        self.set_status("")
        if s.was_submitted(gid, msgid):
            self.notify("Sent to the moderators: the new version appears when one of them "
                        "approves it", timeout=10)
        else:
            self.notify("Edited")
        if notify:
            self._send_notices(gid, msgid, subject, m.kind, notify)
        self.select_group(gid)

    @work(group="dialog", exit_on_error=False)
    async def action_browse(self) -> None:
        self.set_status("Fetching group list ...")
        try:
            groups = await self.session.list_groups()
        except (SessionError, OSError, ConnectionError) as exc:
            self.notify(str(exc), severity="error")
            return
        self.set_status("")
        # other people's inboxes are not groups to read (only the owner can);
        # you write to someone from Contacts
        groups = [g for g in groups if not is_personal_inbox(g.group_id)
                  or g.group_id == self.session.my_inbox]
        # groups for programs (node list, root list, names) are not for reading,
        # unless you already follow one (UN-RFC 0008 section 3.1.1)
        groups = [g for g in groups if g.subscribed or not self.session.is_data_group(g.group_id)]
        await self.push_screen_wait(BrowseDialog(groups))
        self.refresh_groups()

    @work(group="dialog", exit_on_error=False)
    async def action_create_group(self) -> None:
        result = await self.push_screen_wait(CreateGroupDialog())
        if not result:
            return
        self.set_status(f"Creating {result['name']} ...")
        try:
            gid = await self.session.create_group(
                result["name"], result["gtype"], result["title"], local=result["local"],
                rules=result["rules"], rules_type=result.get("rules_type"),
                view=result["view"], low_latency=result["low_latency"],
                kinds=result.get("kinds"), comments=result.get("comments"),
                extra=({"attachments": result["attachments"]} if result.get("attachments")
                       else None),
                min_pow=16 if result["low_latency"] else sess_default_pow())
        except (SessionError, ValueError, OSError, ConnectionError) as exc:
            self.notify(f"Could not create group: {exc}", severity="error", timeout=8)
            return
        self.notify(f"Created {gid}")
        self.current_group = gid
        self.refresh_groups()

    @work(group="dialog", exit_on_error=False)
    async def action_invite(self) -> None:
        g = self._need_group()
        if not g:
            return
        entry = self.session.group_entry(g.group_id)
        if g.type != "closed" or not entry.get("owner"):
            self.notify("You can only invite to closed groups you own", severity="warning")
            return
        key = await self.push_screen_wait(InputDialog(
            f"Invite to {self.session.group_label(g.group_id)}", "Paste the new member's key (they find it with k).",
            "ed25519:..."))
        if not key:
            return
        try:
            await self.session.invite(g.group_id, key)
        except Exception as exc:  # noqa: BLE001
            self.notify(f"Invite failed: {exc}", severity="error", timeout=8)
            return
        await self.push_screen_wait(InfoDialog(
            "Invitation sent - share this with them", self._group_address(g.group_id)))

    def _hints(self, gid: str) -> list[str]:
        """The node this client reads a group from, as an n= hint: only an onion
        address, which is what others can reach (UN-RFC 0031 section 3.4.1)."""
        node = self.session.node_for(gid)
        return [node] if node and _ONION.match(node.lower()) else []

    def _group_address(self, gid: str) -> str:
        return addr.group_address(gid, self._hints(gid))

    def action_post_address(self) -> None:
        """The selected post's unseenet: address, to give to others (UN-RFC 0031)."""
        m = self.current_message
        if m is None or not m.message_id or not self.current_group or m.private \
                or self.current_group not in self.groups:
            self.notify("Select a post in a group first", severity="warning")
            return
        self._show_address("The post's address - others open it with j",
                           addr.post_address(self.current_group, m.message_id,
                                             self._hints(self.current_group)))

    def action_group_address(self) -> None:
        gid = self.current_group
        if not gid or gid not in self.groups or gid in ACROSS or gid.startswith(CONTACT):
            self.notify("Select a group first", severity="warning")
            return
        self._show_address("The group's address - others open it with j", self._group_address(gid))

    def _show_address(self, title: str, text: str) -> None:
        self.copy_to_clipboard(text)          # where the terminal lets it (OSC 52)
        try:
            web = addr.web_address(text)      # the same address on the web (UN-RFC 0031 3.4.1)
        except addr.AddressError:
            web = None
        self.push_screen(InfoDialog(title, text, web))

    def _key_for_fingerprint(self, fp: str) -> str | None:
        """A key this client has met with that fingerprint: yours, a contact's, or an
        author of what you have read."""
        s = self.session
        known = [s.identity(n).key_text for n in s.data["identities"]]
        known += list(s.data.get("contacts", {}))
        known += [m.author_key for g in s.cache.values() for m in g.values()]
        for key in dict.fromkeys(k for k in known if k):
            try:
                if crypto.fingerprint(crypto.key_from_text(key)) == fp:
                    return key
            except crypto.CryptoError:
                continue
        return None

    @work(group="dialog", exit_on_error=False)
    async def action_join(self) -> None:
        """Join a group or open what an unseenet: address names - a group, a post or a
        person; the older forms too (UN-RFC 0031 section 3.4.1)."""
        value = await self.push_screen_wait(InputDialog(
            "Join a group or open an address", "Paste a group ID or an unseenet: address - its web form "
            "(https://<entrance>/a/...) opens here too, over Tor.",
            "dk.snak@... or unseenet:..."))
        if not value:
            return
        value = value.strip()
        looks = (value.lower().startswith((addr.SCHEME, addr.WEB_SCHEME))
                 or addr.is_web_form(value))      # web+unseenet: and https://<host>/a/... too
        try:
            a = addr.parse(value if looks else addr.SCHEME + value)
        except addr.AddressError as exc:
            self.notify(f"That is not an address: {exc}", severity="error")
            return
        if a.kind == "identity":
            key = self._key_for_fingerprint(a.fingerprint)
            if key:
                self.action_show_profile(key)
            else:
                self.notify(f"Someone this client has not met yet: fingerprint {a.fingerprint}",
                            timeout=8)
            return
        gid, node = a.group_id, (a.nodes[0] if a.nodes else None)
        if a.kind == "page":
            self._open_at = ("page", a.path)        # a site opens at that page (#473)
        if gid in self.groups:
            self.current_group = gid
            self.select_group(gid)
            if a.kind == "post":
                await self._select_post(a.message_id)
            return
        if node and node not in self.session.nodes:
            if await self.push_screen_wait(ConfirmDialog(
                    "The group is on another node",
                    f"{node}\n\nYou do not use this node yet. Add it? You keep your key; "
                    "the group is read from there.", "Add node")):
                self.session.add_node(node)
                self._start_watches()
                self._update_subtitle()
            else:
                node = None
        try:
            self.session.subscribe(gid, node=node if node in self.session.nodes else None)
        except ValueError:
            self.notify("That is not a valid group ID", severity="error")
            return
        self.current_group = gid
        self.refresh_groups()
        if a.kind == "post":
            self.select_group(gid)
            await self._select_post(a.message_id)
        elif a.kind == "page":
            self.select_group(gid)

    async def _select_post(self, msgid: str) -> None:
        """Select a post of the open group once it is read, or say it is not there."""
        for _ in range(200):
            if msgid in self.messages:
                break
            await asyncio.sleep(0.05)
        if msgid in self.messages:
            self.current_message = self.messages[msgid]
        else:
            self.notify("The post is not there - it may have been withdrawn or have expired",
                        timeout=8)

    @work(group="dialog", exit_on_error=False)
    async def action_group_ref(self, text: str = "") -> None:
        """Open a group the current post names with # - or the reference that was
        clicked (text) - or offer to subscribe (#172). Never subscribes without asking."""
        s, m = self.session, self.current_message
        refs = (group_refs(text) if text
                else group_refs(f"{m.subject}\n{m.body}") if m is not None and m.readable else [])
        if not refs:
            self.notify("This post names no group. Write e.g. #dk.snak to name one.",
                        severity="warning")
            return
        ref = refs[0]
        if len(refs) > 1:
            text = await self.push_screen_wait(KeyListDialog(
                "Groups in this post", "Which one?", [(r.text, r.text) for r in refs], "Go"))
            if not text:
                return
            ref = next(r for r in refs if r.text == text)
        self.set_status(f"Looking for {ref.text} ...")
        groups = await s.find_group_ref(ref)
        self.set_status("")
        if not groups:
            where = (" (some of your nodes did not answer)" if getattr(s, "unreachable", None)
                     else "")
            self.notify(f"No group called {ref.name} on your nodes{where}.", severity="warning",
                        timeout=8)
            return
        also = {g.group_id for g in groups}
        g = groups[0]
        if not g.subscribed and len(groups) > 1 and s.group_label(g.group_id, also) != ref.name:
            gid = await self.push_screen_wait(KeyListDialog(
                f"More than one {ref.name}", "Groups with the same name have different owners; "
                "check the fingerprint.",
                [(x.group_id, short_group_id(x.group_id) + (f"  {x.description}" if x.description
                                                             else "")) for x in groups], "Choose"))
            if not gid:
                return
            g = next(x for x in groups if x.group_id == gid)
        if isinstance(self.screen, ReadScreen):
            self.pop_screen()
        if g.subscribed:
            self.current_group = g.group_id
            self.select_group(g.group_id)
            return
        label = s.group_label(g.group_id, also)
        lines = [f"{g.group_id}"]
        if g.description:
            lines += ["", g.description]
        warning = naming.group_warning(s, g.group_id, also)
        if warning:
            lines += ["", warning]
        if g.type == "closed":
            lines += ["", "A closed group: you can read it only if its owner invites you."]
        if not await self.push_screen_wait(ConfirmDialog(
                f"Subscribe to {label}?", "\n".join(lines), "Subscribe")):
            return
        node = next((n for n in g.nodes if n in s.nodes), None)
        try:
            s.subscribe(g.group_id, node=node)
        except ValueError:
            self.notify("That is not a valid group ID", severity="error")
            return
        self.current_group = g.group_id
        self.refresh_groups()

    def _names_text(self) -> str:
        s = self.session
        mine = naming.my_names(s)
        lines = []
        primary = naming.preferred(s, s.identity().key_text, mine).name if mine else None
        kinds = {"registered": "registered", "domain": "domain", "sub": "given by its holder"}
        for c in mine:
            lines.append(f"{naming.name_text(c.name)}  ({kinds.get(c.kind, c.kind)}, until "
                         f"{c.expires.astimezone():%d %B %Y})"
                         + ("  - your posts show this one" if c.name == primary and len(mine) > 1 else ""))
        held = {c.name for c in mine}
        for name, action, when in naming.pending(s):
            if name not in held:
                who = ("its holder" if action == "ask"
                       else "the registrar (it decides after about an hour)")
                lines.append(f"{action} {name}: sent {nm.parse_rfc3339(when).astimezone():%d %B %H:%M}, "
                             f"waiting for {who}")
        if not lines:
            lines.append("You have no names yet.")
        lines.append("")
        lines.append(f"Your key: {s.identity().key_text}")
        return "\n".join(lines)

    @work(group="dialog", exit_on_error=False)
    async def action_names(self) -> None:
        """Claim a name for your key, and see the ones you have (UN-RFC 0019)."""
        s = self.session
        self.set_status("Reading the registrar ...")
        try:
            await asyncio.wait_for(s.refresh_naming(), 120)
        except (SessionError, OSError, ConnectionError, NNTPError, ValueError, asyncio.TimeoutError):
            pass                                  # show what we have
        self.set_status("")
        result = await self.push_screen_wait(NamesDialog(self._names_text()))
        if not result:
            return
        action, name = result
        try:
            kind = nm.check_name(name)
        except nm.NameProblem as exc:
            self.notify(str(exc), severity="error")
            return
        to = None
        if action == "release":
            if not await self.push_screen_wait(ConfirmDialog(
                    f"Release @{name}?",
                    "You give the name up at once. Others can then claim it, and your "
                    "posts no longer show it.", "Release", danger=True)):
                return
        elif action == "transfer":
            to = await self.push_screen_wait(InputDialog(
                f"Transfer @{name} to another key",
                "Paste the key that should hold the name from now on - your new "
                "identity's, or someone else's. Your own key loses it.",
                "ed25519:..."))
            if not to:
                return
            try:
                crypto.key_from_text(to)
            except crypto.CryptoError:
                self.notify("That is not a key (ed25519:...)", severity="error")
                return
            if to == s.identity().key_text:
                self.notify("That is your own key", severity="warning")
                return
            if not await self.push_screen_wait(ConfirmDialog(
                    f"Transfer @{name}?", f"From now on the name belongs to\n\n{to}\n\n"
                    "Only that key can give it back.", "Transfer", danger=True)):
                return
        if kind == "domain" and action == "claim":
            record = f"{nm.dns_record_name(name)}  TXT  \"{nm.dns_record_value(s.identity().key_text)}\""
            if not await self.push_screen_wait(ConfirmDialog(
                    f"Claim {name}?",
                    "The registrar looks for this DNS record. Add it at your DNS provider "
                    f"first, then claim:\n\n{record}\n\nKeep it there: the name is renewed "
                    "every month for as long as the record is there.", "Claim")):
                return
        self.set_status(f"Sending your {action} for {name} ...")
        try:
            await naming.claim(s, name, action, to)
        except (SessionError, OSError, ConnectionError, NNTPError, nm.NameProblem) as exc:
            self.set_status("")
            self.notify(f"Could not send the claim: {exc}", severity="error")
            return
        self.set_status("")
        done = {"claim": "the name shows under @ when it is yours",
                "renew": "the new expiry shows under @",
                "transfer": "the name then belongs to the other key",
                "release": "the name is then free"}[action]
        self.notify(f"Sent your {action} of @{name} to the registrar. It decides after about an "
                    f"hour; the answer comes to your private messages, and {done}.", timeout=12)

    @work(group="dialog", exit_on_error=False)
    async def action_show_profile(self, key: str = "") -> None:
        m = None if key else self.current_message
        key = key or (m.author_key if m and m.readable and m.author_key else "")
        if not key:
            key = await self.push_screen_wait(InputDialog(
                "Whose profile?", "Select a post first, or give a key.", "ed25519:..."))
            if not key:
                return
        s = self.session
        mine = key == s.identity().key_text
        hidden = not mine and s.profile_hidden(key)
        if not hidden:
            # all the authors shown at once, so the node cannot tell whose profile
            # the user opens (UN-RFC 0022 section 13, UN-RFC 0041 section 3.2)
            authors = [x.author_key for x in self.messages.values()
                       if x.readable and x.author_key]
            try:
                await s.prefetch_profiles([key.strip(), *authors])
            except (SessionError, OSError, ConnectionError, NNTPError):
                pass                        # profile_of below tries again and says why
        try:
            p = None if hidden else await s.profile_of(key.strip())
        except (SessionError, OSError, ConnectionError, NNTPError, crypto.CryptoError) as exc:
            self.notify(f"Could not read the profile: {exc}", severity="error")
            return
        label = s.identity_label(key, m.from_name if m and m.author_key == key else "")
        gid = self.current_group or ""
        context = ("me" if (m is not None and m.private) or gid.startswith(CONTACT)
                   or gid == s.my_inbox else
                   gid if s.group_entry(gid).get("type") == "closed" else None)
        shared = None
        private = s.private_profile(key, context) if context and not mine else None
        if private is not None:                         # in its own context only (0022, 6)
            p = prof.Profile.from_object(crypto.key_from_text(key), private)
            shared = "with you" if context == "me" else "with this group"
        theirs = {}
        if not mine and not hidden:             # their feed and site, to look at (no following)
            try:
                theirs = await s.personal_groups_of(key)
            except (SessionError, OSError, ConnectionError, NNTPError, crypto.CryptoError):
                theirs = {}
        answer = await self.push_screen_wait(ProfileDialog(
            key, label, p, mine, hidden, shared, theirs=theirs,
            contact=None if mine else s.contact_name(key),
            feed_started=s.has_personal_group("feed"),
            site_started=s.has_personal_group("site"),
            following=not mine and s.is_following(key)))
        if answer in ("hide", "show"):
            s.hide_profile(key, answer == "hide")
            self.notify("Profile hidden" if answer == "hide" else "Profile shown")
        elif answer == "my-feed":
            self.action_start_feed()                # opens it, or starts it after the warning
        elif answer == "my-site":
            self.action_start_site()
        elif answer == "follow":
            await self._follow(key)
        elif answer == "unfollow":
            try:
                await s.unfollow_person(key)
            except (SessionError, ValueError) as exc:
                self.notify(f"Not changed: {exc}", severity="error", timeout=8)
                return
            self.notify(f"Not following {label} any more", timeout=4)
            self.refresh_groups()
        elif answer == "contact":
            name = await self.push_screen_wait(InputDialog(
                "Name this contact", "A name of your own choosing. Only you see it, and nobody "
                "can pretend to be this contact under it: it is tied to the key.", "e.g. Sonia",
                value=(p.short if p is not None and getattr(p, "short", "") else "")
                or label.split("~")[0].lstrip("@")))
            if name:
                try:
                    s.add_contact(key, name)
                except (SessionError, crypto.CryptoError) as exc:
                    self.notify(f"Not added: {exc}", severity="error")
                    return
                self.notify(f"Added {name}")
                await self._render_groups()
        elif answer == "uncontact":
            name = s.contact_name(key) or label
            if await self.push_screen_wait(ConfirmDialog(
                    f"Remove {name}?", "The contact is forgotten. Your messages stay, and the "
                    "chat stays in Chats while you have one - close it there.", "Remove")):
                s.remove_contact(key)
                self.notify(f"{name} is no longer a contact", timeout=4)
                await self._render_groups()
        elif answer in ("their-feed", "their-site"):
            await self._open_personal_of(key, label, answer.split("-")[1])

    async def _open_personal_of(self, key: str, label: str, kind: str = "feed") -> None:
        """Open someone's personal feed or site from their profile, to read without
        following it - if they have started one."""
        s = self.session
        gid = make_group_id(kind, crypto.key_from_text(key))
        if gid not in s.data["subscribed"]:
            self.set_status(f"Opening their {kind} ...")
            try:
                m = await s.fetch_manifest(gid)
            except (SessionError, OSError, ConnectionError, NNTPError) as exc:
                self.set_status("")
                self.notify(f"Could not look: {exc}", severity="error", timeout=8)
                return
            self.set_status("")
            if m is None:
                self.notify(f"{label} has not started a {kind}", timeout=6)
                return
            s.group_entry(gid)["manifest"] = dict(m.raw)
            self.notify(f"Reading {label}'s {kind} without following it. Follow on their "
                        "profile (u) brings their feeds into your Timeline.", timeout=8)
        self.select_group(gid)

    async def _share_target(self) -> tuple[str, str] | None:
        """Where a shared profile goes: this closed group, or a contact's inbox."""
        s = self.session
        gid = self.current_group or ""
        if s.group_entry(gid).get("type") == "closed" and gid in self.groups:
            return gid, f"the members of {s.group_label(gid)}"
        people = s.contacts()
        if not people:
            self.notify("Open a closed group, or add a contact, to share your profile with",
                        severity="warning", timeout=6)
            return None
        key = await self.push_screen_wait(KeyListDialog(
            "Share your profile with", "Only they can read it, and it is shown only to them.",
            people, "Choose"))
        if not key:
            return None
        return personal_inbox_id(crypto.key_from_text(key)), s.contact_name(key) or key[:20]

    @work(group="dialog", exit_on_error=False)
    async def action_share_profile(self) -> None:
        """A profile for one person or a closed group only (UN-RFC 0022 section 6) - for
        those who want no public profile, or another one for friends."""
        s = self.session
        target = await self._share_target()
        if target is None:
            return
        to, who = target
        dlg = EditProfileDialog(s.my_profile())
        answer = await self.push_screen_wait(dlg)
        if not answer or answer == "remove":
            return
        if not await self.push_screen_wait(ConfirmDialog(
                "Share your profile", f"Only {who} can read it, and it is shown only there - "
                "your public profile, if you have one, stays as it is. You can take it back "
                "later.", "Share")):
            return
        try:
            await s.share_profile(to, answer)
        except (SessionError, OSError, ConnectionError, NNTPError) as exc:
            self.notify(f"Not shared: {exc}", severity="error", timeout=8)
            return
        self.notify(f"Profile shared with {who}", timeout=5)

    @work(group="dialog", exit_on_error=False)
    async def action_unshare_profile(self) -> None:
        """Take back a profile shared with someone or a closed group (an empty one)."""
        target = await self._share_target()
        if target is None:
            return
        to, who = target
        try:
            await self.session.withdraw_shared_profile(to)
        except (SessionError, OSError, ConnectionError, NNTPError) as exc:
            self.notify(f"Not taken back: {exc}", severity="error", timeout=8)
            return
        self.notify(f"Shared profile taken back from {who}", timeout=5)

    @work(group="dialog", exit_on_error=False)
    async def action_edit_profile(self) -> None:
        s = self.session
        if not s.has_published_profile() and not await self.push_screen_wait(ConfirmDialog(
                "Before you publish a profile", PROFILE_WARNING, confirm="I understand")):
            return
        current = s.my_profile()
        dlg = EditProfileDialog(current)
        answer = await self.push_screen_wait(dlg)
        if answer is None:
            return
        if answer == "remove":
            if not await self.push_screen_wait(ConfirmDialog(
                    "Remove your profile?", "Nodes stop showing it. Copies others already "
                    "kept cannot be taken back.", confirm="Remove", danger=True)):
                return
            answer = None
        else:
            old = {(l["kind"], l["value"]) for l in (current or {}).get("links", [])}
            new = [l for l in answer.get("links", []) if (l["kind"], l["value"]) not in old
                   and l["kind"] != "onion"]
            if new and s.has_published_profile() and not await self.push_screen_wait(ConfirmDialog(
                    "New links", "Each link ties this identity, and everything it posts, to "
                    "that account:\n\n" + "\n".join(f"{l['kind']}: {l['value']}" for l in new),
                    confirm="Publish")):
                return
        try:
            await s.publish_profile(answer)
            picture = getattr(dlg, "picture", "") if answer else ""
            if picture:                                       # #155: cleaned by the core
                await s.publish_picture_file(Path(picture).expanduser(), alt=s.display_name())
        except (SessionError, OSError, ConnectionError, NNTPError, ValueError) as exc:
            self.notify(f"Not published: {exc}", severity="error")
            return
        self.notify(("Profile and picture published" if getattr(dlg, "picture", "") else
                     "Profile published") if answer else "Profile removed")

    def action_show_key(self) -> None:
        self.push_screen(InfoDialog("Your key - share it to get private messages or invitations",
                                    self.session.identity().key_text))

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        """A failing background task is reported and logged, never fatal."""
        if event.state != WorkerState.ERROR:
            return
        err = event.worker.error
        log = self.vault_path.resolve().parent / "unseenet-tui-errors.log"
        try:
            with open(log, "a") as f:
                f.write(f"--- {datetime.now().isoformat(timespec='seconds')} "
                        f"in {event.worker.name}\n")
                f.write("".join(traceback.format_exception(type(err), err, err.__traceback__)))
            where = f" - details in {log.name}"
        except OSError:
            where = ""
        self.notify(f"Something went wrong ({type(err).__name__}: {err}){where}",
                    severity="error", timeout=10)

    # --- push (UN-RFC 0008 section 2) ---
    def _start_watches(self) -> None:
        for address in self.session.nodes:
            if address not in self._watching:
                self._watching.add(address)
                self.watch_loop(address)

    @work(group="watch", exit_on_error=False)
    async def watch_loop(self, node: str) -> None:
        """Keep a second connection to a node open so new posts show up at once."""
        await self.runtime.watch_node(node, self._on_push,           # (#139)
                                      keep_going=lambda: node in self._watching)

    async def _on_push(self, gid: str, message_id: str) -> None:
        s = self.session
        if gid in s.data["subscribed"] or gid == s.my_inbox:
            self.run_worker(self._fetch_and_show(gid), group="push", exit_on_error=False)

    async def _fetch_and_show(self, gid: str) -> None:
        s = self.session
        before = s.unread_count(gid)
        try:
            await s.fetch_group(gid)
        except (SessionError, OSError, ConnectionError):
            return
        current = self.current_group or ""
        if current == gid or (current.startswith(CONTACT) and gid == s.my_inbox):
            self._render_threads()
        elif gid == s.my_inbox and s.unread_count(gid) > before:
            self.notify("New private message")
        await self._render_groups()

    def get_system_commands(self, screen):
        """Everything the keys do is also in the command palette (Ctrl+P)."""
        yield from super().get_system_commands(screen)
        yield SystemCommand("About Unseenet", "The client, the project, source and documentation  [F1]",
                            self.action_about)
        yield SystemCommand("Tor bridges", "Connect through bridges where Tor is blocked",
                            self.action_bridges)
        yield SystemCommand("Tor: which Tor", "Start our own Tor, or use one already running "
                            "(Tails, Whonix, a system tor)", self.action_tor)
        if portable_root() is not None:
            yield SystemCommand("Update Unseenet", "Get the newest release onto this stick or "
                                "folder, over Tor - data/ is kept", self.action_update)
        if self.session is None:
            return
        commands = [
            ("New post", "Write a new post in the current group  [n]", self.action_new_post),
            ("Reply", "Reply to the selected post  [r]", self.action_reply),
            ("Write a private post", "A private post with a subject, to a contact by name or "
             "to anyone by their key  [m]", self.action_message),
            ("Contacts", "Your address book: chat, write a private post, rename, remove  [c]",
             self.action_contacts),
            ("Your state",
             ("On this device only - keep it in the network again (encrypted)"
              if self.session.state_kept_local() else
              "In the network, encrypted: your groups and settings on every device - or on "
              "this device only"),
             self.action_state_where),
            ("Key ripening",
             ("Paused - resume ripening your key, and what ripening is"
              if self.runtime is not None and self.runtime.ripening_paused else
              "Pause ripening your key (on battery, a warm laptop), and what ripening is"),
             self.action_ripening),
            ("Follow", "Follow a person - the selected post's author, or anyone by key or name; "
             "their feeds come into your Timeline", self.action_follow),
            ("Following", "The people you follow - stop following one", self.action_following),
            ("Start my feed", "Your own feed, public, where anyone can comment - or open it",
             self.action_start_feed),
            (("Pictures in private posts: on" if self.session and self.session.inbox_files()
              else "Pictures in private posts: off"),
             "Whether people can send you pictures in private posts and chats - encrypted, "
             "only you can see them", self.action_toggle_inbox_files),
            ("Start my site", "Your own site of pages, public - or open it; n writes a page",
             self.action_start_site),
            ("Previous version of this page", "Publish the selected page's earlier version "
             "again (a site you write in)", self.action_page_previous),
            ("Remove this page", "Remove the selected page from the site you write in",
             self.action_page_remove),
            ("Share my profile", "With one person or this closed group only - not public",
             self.action_share_profile),
            ("Take back my shared profile", "From a person or this closed group",
             self.action_unshare_profile),
            ("Copy address", "The unseenet: address of this post, page, group or person - to "
             "give to others", self.action_copy_address),
            ("Open address", "Go to an unseenet: address - a group, a post, a page or a person",
             self.action_open_address),
            ("Private posts from new keys",
             ("Hidden: only contacts and keys ripened an hour or more reach you - show all"
              if self.session.private_min_ripe() is not None else
              "Shown - hide those from keys made minutes ago (contacts always come through)"),
             self.action_toggle_private_ripe),
            ("Chat with someone", "A private chat with someone by their key (the author of the "
             "selected post is filled in)", self.action_chat_with),
            ("Browse groups", "See all groups on the node, subscribe or unsubscribe  [g]",
             self.action_browse),
            ("Create group", "Create an open, moderated, channel, closed or inbox group",
             self.action_create_group),
            ("Invite to closed group", "Give someone access to a closed group you own",
             self.action_invite),
            ("Join group or open an address", "Subscribe by group ID, or open an unseenet: "
             "address of a group, a post or a person  [j]", self.action_join),
            ("Address of this post", "The selected post's unseenet: address, to give to others",
             self.action_post_address),
            ("Address of this group", "The group's unseenet: address, to give to others",
             self.action_group_address),
            ("Nodes", "Your nodes: add, remove, default, who runs them, write to an operator  [o]",
             self.action_node_info),
            ("Show my key", "Your key, to share for private messages and invitations  [k]",
             self.action_show_key),
            ("Profile of this author (u)", "Who wrote the selected post: names, about, links, "
             "and their feed and site to look at without following", self.action_show_profile),
            ("Your profile", "Write, change or remove your public profile",
             self.action_edit_profile),
            ("Names", "Claim a name such as @lorensen, see the names you hold  [@]",
             self.action_names),
            ("Edit signature", "The few lines added below '-- ' in your posts  [s]",
             self.action_signature),
            ("Change passphrase", "A new passphrase for this vault file (copies elsewhere "
             "keep the old one)", self.action_change_passphrase),
            ("Notifications: stop" if self.session.notification_settings()["on"] else "Notifications: tell me",
             "A notification (or the bell) when a private message, a reply or a mention lands "
             "in For you - only while this runs", self.action_toggle_notifications),
            ("Notifications: hide who and what" if self.session.notification_settings()["content"]
             else "Notifications: show who and what",
             "Show the sender and the start of the text in notifications (they can show on the "
             "lock screen)", self.action_toggle_notification_content),
            ("Export my data", "Your posts, comments, chat lines and pages as signed, their files, "
             "profile and groups - never your keys; private messages only encrypted",
             self.action_export),
            ("Show recovery words", "The 24 words that bring your identity back on any device "
             "- behind your passphrase" + ("" if self.session.words_written() else
                                          " (not written down yet)"), self.action_show_words),
            ("Copy to another device", "Your identities to a phone, PC or stick: a code or QR "
             "code, then two check words", self.action_copy_to),
            ("Get identity from another device", "Type the code another device shows",
             self.action_copy_from),
            ("Restore from recovery words", "Bring an identity back from its 24 words",
             self.action_restore_words),
            ("Write plain text" if self.session.text_type() == markup.MARKDOWN_TYPE else "Write Markdown",
             "What you write is sent as Markdown (*emphasis*, `code`, :tada:) or as plain text "
             "(UN-RFC 0031)", self.action_toggle_markdown),
            ("About this group", "Its rules, type, owner and settings  [?]", self.action_group_info),
            ("Open the mentioned post", "From a mention notice in For you: the post that names "
             "you, or its group first", self.action_open_mentioned),
            ("Stop telling people you mention" if self.session.notice_settings()["send"]
             else "Tell people you mention", "A short notice in their inbox, so they see it even "
             "if they do not follow the group (UN-RFC 0032)", self.action_toggle_notices_send),
            ("Stop showing mention notices" if self.session.notice_settings()["receive"]
             else "Show mention notices", "Others' notices that a post in a group you do not "
             "follow names you", self.action_toggle_notices_receive),
            ("Go to a #group in this post", "Open a group the selected post names, such as "
             "#dk.snak, or subscribe to it  [#]", self.action_group_ref),
            ("Switch chat / threads", "Show the current group as a chat or as threads  [v]",
             self.action_toggle_view),
            ("Edit group", "Owner: change title, description, rules, view and delivery",
             self.action_edit_group),
            ("Add contact", "Give the selected author, or any key, a name of your own  [a]",
             self.action_add_contact),
            ("Ignore this person", "Hide everything the selected post's author writes - only "
             "you know", self.action_ignore),
            ("People you ignore", "See who you ignore, and show someone again",
             self.action_ignored),
            ("Ask for a name under someone's name", "e.g. sonia@lorensen: the holder of "
             "@lorensen decides", self.action_ask_subname),
            ("Give a name under yours", "Holders of a name: give someone name@yourname",
             self.action_give_subname),
            ("Name requests", "People asking for a name under yours", self.action_name_requests),
            ("Names you gave", "See them, and take one back", self.action_names_given),
            ("Name your posts show", "If you have several names, choose one",
             self.action_primary_name),
            ("Report post", "Tell the group's owner, moderators, maintainer or node operator "
             "about a post that breaks the rules or the law", self.action_report),
            ("Open reported post", "From a report you received: go to the post it is about",
             self.action_open_reported),
            ("Moderation queue", "Moderators: posts waiting for approval in groups you "
             "moderate", self.action_moderation),
            ("Make moderator", "Owner: let someone approve and withdraw posts in this group",
             lambda: self.action_add_role("moderators")),
            ("Make poster", "Owner of a channel: let someone post in it",
             lambda: self.action_add_role("posters")),
            ("Moderators and posters", "Owner: see them, and remove one",
             self.action_roles),
            ("Members of closed group", "Owner: see whom you invited, and remove a member",
             self.action_members),
            ("Ban from group", "Owner: the selected post's author may no longer post in this "
             "group", self.action_ban),
            ("Block in hierarchy", "Maintainer: block the selected post's author in your whole "
             "hierarchy", self.action_block),
            ("Show posts blocked by maintainers" if not self.session.data.get("show_blocked")
             else "Hide posts blocked by maintainers",
             "Maintainers of hierarchies can block people; you decide whether to see them",
             self.action_toggle_blocked),
            ("Show bots" if self.session.hide_bots() else "Hide bots",
             "Posts by keys whose profile says they post automatically (UN-RFC 0030)",
             self.action_toggle_bots),
            ("Banned from group", "Owner: see who is banned and why, and lift a ban",
             self.action_banned),
            ("Rename contact", "Change the name of the selected contact", self.action_rename_contact),
            ("Remove contact", "Forget the selected contact (your messages stay)",
             self.action_remove_contact),
            ("Close chat", "Take this chat - or one you choose - off the list of chats; it "
             "comes back when one of you writes. Hide, archive, remove from chats",
             self.action_close_chat),
            ("Refresh", "Fetch new posts now  [F5]", self.action_refresh),
            ("Private posts", "Open your private posts (chats are under each contact)", self.action_open_inbox),
            ("Mark group as read", "Mark every post in the current group as read",
             self.action_mark_all_read),
            ("Unsubscribe from group", "Stop following the current group and forget it locally",
             self.action_unsubscribe),
            ("Withdraw (delete) post", "Remove, delete, slet, fjern: take your post, comment or "
             "chat line (or any post in a group you own) back from the network  [D]",
             self.action_withdraw),
            ("Pin or unpin post", "Owner or moderator: keep the selected post at the top of the "
             "group, kept by the nodes - or stop (UN-RFC 0033)", self.action_pin),
            ("Pinned posts", "The posts pinned in this group: choose one to read it",
             self.action_pinned_posts),
            ("Next pin", "In a chat: show the next pinned line above it",
             self.action_next_pin),
            ("Show or hide the pins", "Fold the Pinned section or a chat's pinned line away, "
             "per group", self.action_toggle_pins),
            ("Remove group from the network", "Delete a group you own from every node - final",
             self.action_remove_group),
        ]
        for title, help_text, callback in commands:
            yield SystemCommand(title, help_text, callback)

    def _owner_label(self, data: dict) -> str:
        key = data.get("owner", "")
        if key == self.session.identity().key_text:
            return "you"
        name = self.session.contact_name(key)
        return f"{name} √  ({key[:28]}…)" if name else f"{key[:36]}…"

    @work(group="dialog", exit_on_error=False)
    async def action_group_info(self, first_time: bool = False) -> None:
        g = self._need_group()
        if not g:
            return
        data = self.session.manifest_data(g.group_id)
        if not data:
            self.notify("Nothing known about this group yet - try again after a refresh",
                        severity="warning")
            return
        owner = bool(self.session.group_entry(g.group_id).get("owner"))
        result = await self.push_screen_wait(GroupInfoDialog(
            g.group_id, data, self._owner_label(data), owner, first_time,
            node=self.session.node_for(g.group_id) or ""))
        if data.get("rules"):
            self.session.mark_rules_seen(g.group_id)
        if result == "edit":
            self.action_edit_group()

    @work(group="dialog", exit_on_error=False)
    async def action_edit_group(self) -> None:
        g = self._need_group()
        if not g:
            return
        if not self.session.group_entry(g.group_id).get("owner"):
            self.notify("Only the group's owner can change it", severity="warning")
            return
        changes = await self.push_screen_wait(EditGroupDialog(self.session.manifest_data(g.group_id)))
        if not changes:
            return
        self.set_status("Publishing the new version of the group ...")
        try:
            await self.session.update_group(g.group_id, **changes)
        except (SessionError, OSError, ConnectionError, ValueError) as exc:
            self.notify(f"Not changed: {exc}", severity="error", timeout=8)
            return
        self.session.mark_rules_seen(g.group_id)
        self.notify("Group updated")
        self._render_threads()

    @work(group="dialog", exit_on_error=False)
    async def action_ignore(self) -> None:
        """Ignore the selected post's author (#72)."""
        m, s = self.current_message, self.session
        key = (m.sender_key or m.author_key) if m else ""
        if not key or key == s.identity().key_text:
            self.notify("Select a post by the person you want to ignore", severity="warning")
            return
        who = s.identity_label(key, m.from_name)
        if not await self.push_screen_wait(ConfirmDialog(
                f"Ignore {who}?",
                f"{key}\n\nTheir posts and private messages are hidden from you in every "
                "group. Others still see them, and they are not told. You can show them "
                "again under 'People you ignore' (Ctrl+P).", "Ignore")):
            return
        try:
            s.ignore(key, who)
        except (SessionError, crypto.CryptoError) as exc:
            self.notify(f"Not ignored: {exc}", severity="error")
            return
        self.current_message = None
        self.notify(f"Ignoring {who}")
        await self._render_groups()
        self._render_threads()

    @work(group="dialog", exit_on_error=False)
    async def action_ban(self) -> None:
        """Owner: ban the selected post's author from the group (UN-RFC 0004, 5.1)."""
        m, s, gid = self.current_message, self.session, self.current_group
        if not gid or not s.group_entry(gid).get("owner"):
            self.notify("Only a group's owner can ban people from it. You can ignore them "
                        "(Ctrl+P: Ignore this person).", severity="warning")
            return
        if m is None or m.private or not m.author_key or m.author_key == s.identity().key_text:
            self.notify("Select a post by the person you want to ban", severity="warning")
            return
        who = s.identity_label(m.author_key, m.from_name)
        reason = await self.push_screen_wait(InputDialog(
            f"Ban {who} from {s.group_label(gid)}?",
            "Nodes refuse their new posts here, and clients hide the old ones. The ban and "
            "its reason are public: anyone can read them in the group's settings. Give a short "
            "reason:", "e.g. threats against other members"))
        if not reason:
            return
        self.set_status("Banning ...")
        try:
            await s.ban(gid, m.author_key, reason)
        except (SessionError, OSError, ConnectionError, crypto.CryptoError) as exc:
            self.set_status("")
            self.notify(f"Not banned: {exc}", severity="error", timeout=8)
            return
        self.set_status("")
        self.current_message = None
        self.notify(f"{who} is banned from {s.group_label(gid)}")
        self._render_threads()

    # --- names under a name (UN-RFC 0019 section 5.4) ---
    @work(group="dialog", exit_on_error=False)
    async def action_ask_subname(self) -> None:
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
        m, s = self.current_message, self.session
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
        result = await self.push_screen_wait(ReportDialog(m.subject or "(no subject)", targets))
        if not result:
            return
        chosen, note, anonymous = result
        self.set_status("Sending the report ...")
        try:
            n = await s.report(m, chosen, note, anonymous)
        except (SessionError, OSError, ConnectionError) as exc:
            self.set_status("")
            self.notify(f"Not sent: {exc}", severity="error", timeout=8)
            return
        self.set_status("")
        self.notify(f"Reported to {n} " + ("person" if n == 1 else "people"))

    async def action_open_reported(self) -> None:
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
        on = not self.session.hide_bots()
        self.session.set_hide_bots(on)
        self.notify("Hiding posts by bots" if on else "Showing posts by bots, marked (bot)")
        self._render_threads()

    def action_toggle_blocked(self) -> None:
        on = not self.session.data.get("show_blocked")
        self.session.set_show_blocked(on)
        self.notify("Showing posts blocked by maintainers" if on
                    else "Hiding posts blocked by maintainers")
        self._render_threads()

    @work(group="dialog", exit_on_error=False)
    async def action_banned(self) -> None:
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
        gid = self.session.my_inbox
        if gid in self.groups:
            self.select_group(gid)
        else:
            self.notify("Your inbox is not ready yet - it is created when the node is reachable",
                        severity="warning")

    def action_mark_all_read(self) -> None:
        g = self._need_group()
        if not g:
            return
        for m in self.session.cache.get(g.group_id, {}).values():
            self.session.mark_read(m, save=False)
        self.session.save()
        self._render_threads()
        self._save_soon()

    def action_refresh(self) -> None:
        if self.session:
            self.refresh_groups()

    async def action_quit(self) -> None:
        if getattr(self, "runtime", None) is not None:
            self.runtime.stop_ripening()           # a segment must not keep us open
        if self.session:
            self.session.save()
            await self.session.close()
        self.exit()


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="unseenet-tui", description="Unseenet terminal client.")
    p.add_argument("--vault", default=str(default_vault_path()), help="encrypted vault file")
    from . import __version__ as tui_version
    p.add_argument("--version", action="version",
                   version=f"unseenet-tui {tui_version} (unseenet {__version__})")
    args = p.parse_args(argv)
    UnseenetApp(Path(args.vault)).run()


if __name__ == "__main__":
    main()
