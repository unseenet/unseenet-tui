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

from .app_groups import GroupsMixin  # noqa: E402 - the app's parts by area (#20)
from .app_chat import ChatMixin  # noqa: E402 - the app's parts by area (#20)
from .app_actions import ActionsMixin  # noqa: E402 - the app's parts by area (#20)
from .app_feeds import FeedsMixin  # noqa: E402 - the app's parts by area (#20)
from .app_addresses import AddressesMixin  # noqa: E402 - the app's parts by area (#20)
from .app_identity import IdentityMixin  # noqa: E402 - the app's parts by area (#20)
from .app_pages import PagesMixin  # noqa: E402 - the app's parts by area (#20)
from .app_push import PushMixin  # noqa: E402 - the app's parts by area (#20)
from .app_names import NamesMixin  # noqa: E402 - the app's parts by area (#20)
from .app_pins import PinsMixin  # noqa: E402 - the app's parts by area (#20)
from .app_rules import RulesMixin  # noqa: E402 - your own rules (UN-RFC 0051)


class UnseenetApp(GroupsMixin, ChatMixin, ActionsMixin, FeedsMixin, AddressesMixin, IdentityMixin, PagesMixin, PushMixin, NamesMixin, PinsMixin, RulesMixin, App):
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
        Binding("k", "show_key", "Your key", show=False),
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
        """Esc: out of the chat box or the filter, back to the list."""
        if getattr(self.focused, "id", None) != "chat-input" and (
                self.group_query or getattr(self.focused, "id", None) == "groups-filter"):
            self._close_group_search()               # Esc ends a search of your groups
            return
        self.query_one("#chat-log").focus()          # now the letters are shortcuts again

    def action_write(self) -> None:
        """w: into the chat box of the chat shown."""
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
        """The app's layout: the header, the group list, the threads or chat, the status
        line and footer."""
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
                    yield Static("", id="chat-emoji", markup=False)      # :ta completion (#6)
        yield Static("", id="status", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        """Set up the threads table's columns, then start."""
        box = self.query_one("#chat-input", ChatBox)
        box.emoji_bar = self.query_one("#chat-emoji", Static)
        box.emoji_bar.display = False
        table = self.query_one("#threads", DataTable)
        table.add_column(" ", key="flag", width=2)
        table.add_column("Subject", key="subject", width=44)
        table.add_column("From", key="from", width=20)
        table.add_column("Date", key="date", width=16)
        self.start()

    def set_status(self, text: str) -> None:
        """The status line's text (nothing when the app is closing)."""
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
        """Open the vault (asking for the passphrase, or set up a first identity), show what
        is kept, then go online."""
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
            if isinstance(event, ev.Refreshed):                  # the network reached, or not
                self._connected = not event.error
            self._notify_soon()                                  # #4: anything for you?

    # --- notifications (#4): what is new comes from the core, telling is ours ---
    def _notify_soon(self) -> None:
        timer = getattr(self, "_notify_timer", None)
        if timer is not None:
            timer.stop()
        self._notify_timer = self.set_timer(1.0, self.check_notifications)

    @work(group="notify", exclusive=True, exit_on_error=False)
    async def check_notifications(self) -> None:
        """Ask the core what is new in For you and tell, ask first, or stay quiet as the
        reader chose (#4)."""
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
        """Export your data (#14, UN-RFC 0049 section 7): never keys; private messages only
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
        """Turn notifications on or off, kept with your settings in the core."""
        s = self.session
        on = s.notification_settings()["on"] is True
        s.set_notification_settings(on=not on)
        self.notify("No more notifications." if on else
                    "Notifications on - they say only \"new in For you\" unless you choose otherwise.")

    def action_toggle_notification_content(self) -> None:
        """Show or hide who wrote and what in notifications."""
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

    @on(Input.Changed, "#groups-filter")
    def _groups_search(self, event: Input.Changed) -> None:
        self.group_query = event.value
        self.run_worker(self._render_groups(), exclusive=True, group="render-groups")

    @on(Input.Submitted, "#groups-filter")
    def _groups_search_done(self, event: Input.Submitted) -> None:
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

    @on(ChatBox.Submitted, "#chat-input")
    def _chat_submit(self, event: ChatBox.Submitted) -> None:
        text = event.value.strip()
        if not text or not self.current_group or event.input.disabled:
            return
        event.input.value = ""
        files = self.chat_files.pop(self.current_group, [])
        self._show_chat_files(self.current_group)
        self.send_chat(self.current_group, text, files)

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


def main(argv=None) -> None:
    """unseenet-tui: open a vault (the default, or --vault) and run the terminal client."""
    p = argparse.ArgumentParser(prog="unseenet-tui", description="Unseenet terminal client.")
    p.add_argument("--vault", default=str(default_vault_path()), help="encrypted vault file")
    from . import __version__ as tui_version
    p.add_argument("--version", action="version",
                   version=f"unseenet-tui {tui_version} (unseenet {__version__})")
    args = p.parse_args(argv)
    UnseenetApp(Path(args.vault)).run()


if __name__ == "__main__":
    main()
