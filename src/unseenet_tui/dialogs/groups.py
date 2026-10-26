"""Groups: creating, editing, their info, and browsing what the nodes carry.

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

from ..helpers import GROUP_KINDS, ICON_LEGEND, by_hierarchy, group_icons, group_name_problem
from .base import Dialog
from .people import MARKDOWN_HINT, manifest_text, writes_markdown


DELIVERY_CHOICES = [("Discussion - shown as threads", "threads"),
                    ("Chat - shown as a conversation", "chat"),
                    ("Chat, fast delivery - gives up some anonymity", "fastchat"),
                    ("Feed - posts with comments underneath (UN-RFC 0034)", "feed"),
                    ("Data for programs - hidden when people browse", "data")]
DELIVERY = {"threads": ("threads", False), "chat": ("chat", False), "fastchat": ("chat", True),
            "feed": ("feed", False), "data": ("data", False), "pages": ("pages", False)}
SITE_CHOICE = ("Site - pages (UN-RFC 0035)", "pages")   # offered for a site only
# what a group takes (UN-RFC 0028, 4.1); "both" is the manifest's default
KINDS_CHOICES = [("Takes posts and chat lines", "both"),
                 ("Takes posts with a subject only (a discussion group)", "post"),
                 ("Takes chat lines only (a chat group)", "chat")]
KINDS = {"both": None, "post": ["post"], "chat": ["chat"]}

REACTIONS_CHOICES = [("Reactions: as the view has them (feeds, chat: " + " ".join(rx.DEFAULT_SET)
                      + "; discussion: none)", "default"),
                     ("No reactions", "none"), ("Any emoji as a reaction", "any"),
                     ("Your own set of reactions (below)", "custom")]   # UN-RFC 0048 section 4


def reactions_choice(value) -> str:
    """The REACTIONS_CHOICES entry for a manifest's ``reactions``."""
    if value is None:
        return "default"
    if value == "any":
        return "any"
    return "none" if value == [] else "custom"


COMMENTS_CHOICES = [("Only you and your posters write", "none"),
                    ("Readers may comment on posts", "open")]
# what files a group carries (UN-RFC 0027 section 5.1, #496): core.blobs.FILE_CHOICES
FILES_CHOICES = [("No files", "none"),
                 ("Pictures, up to 4 MiB each", "pictures"),
                 ("Pictures and PDFs, up to 4 MiB each", "pictures+pdf")]
FILES_GROUP_TYPES = ("open", "moderated", "channel", "closed")   # closed: encrypted (#497)


def reactions_label(d: dict) -> str:
    """What a group takes as reactions (UN-RFC 0048 section 4), for the group's info."""
    r = rx.reaction_set(d)
    if r.any:
        return "any emoji"
    return " ".join(r.emoji) if r else "none"


def comments_label(d: dict) -> str:
    """How a channel takes comments (UN-RFC 0030), for the group's info."""
    if d.get("comments") != "open":
        return "none - only the owner and posters write"
    days = d.get("comment_days")
    return "anyone may comment" + (f", for {days} days after each post" if days else "")


GROUP_NAME_HELP = ("Lowercase letters, digits and - _ +, parts separated by dots, "
                   "e.g. dk.snak or andlo.test. The first part is the hierarchy.")


class CreateGroupDialog(Dialog):
    def compose(self) -> ComposeResult:
        """Creating a group: name, type, view, kinds, comments, files, reactions and rules."""
        with Vertical(classes="dialog"):
            yield Label("Create a group", classes="title")
            yield Input(placeholder="Name, e.g. dk.snak", id="name")
            yield Label(GROUP_NAME_HELP, id="name-help", classes="hint", markup=False)
            yield Select([("Open - anyone can read and post", "open"),
                          ("Moderated - posts need a moderator", "moderated"),
                          ("Channel - only you post", "channel"),
                          ("Closed - members only, encrypted", "closed"),
                          ("Inbox - anyone writes, only you read", "inbox")],
                         value="open", allow_blank=False, id="type")
            yield Select([("Replicated to other nodes", "everywhere"),
                          ("Only on this node (local)", "local")],
                         value="everywhere", allow_blank=False, id="where")
            yield Select(DELIVERY_CHOICES + [SITE_CHOICE], value="threads", allow_blank=False,
                         id="view")
            yield Label("", id="view-help", classes="hint", markup=False)
            yield Select(KINDS_CHOICES, value="both", allow_blank=False, id="kinds")
            yield Select(COMMENTS_CHOICES, value="none", allow_blank=False, id="comments")
            yield Select(FILES_CHOICES, value="none", allow_blank=False, id="files")
            yield Input(placeholder="Title (optional; keep it vague for closed groups)", id="title")
            yield Label("Rules (optional) - shown to people before they post:", classes="hint")
            yield TextArea("", id="rules", soft_wrap=True)
            yield Checkbox(MARKDOWN_HINT, id="markdown", value=writes_markdown(self.app, None, ""))
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Create", variant="primary", id="ok")

    def on_mount(self) -> None:
        """Size the rules field, and the focus in the name."""
        self.query_one("#rules", TextArea).styles.height = 6
        self.query_one("#comments", Select).display = False

    @on(Select.Changed, "#type")
    def _type_changed(self, event: Select.Changed) -> None:
        # only a channel can be opened for comments (UN-RFC 0030)
        self.query_one("#comments", Select).display = event.value == "channel"
        # files only where they are not encrypted, for now (#497)
        self.query_one("#files", Select).display = event.value in FILES_GROUP_TYPES
        if event.value != "channel" and self.query_one("#view", Select).value == "pages":
            self.query_one("#view", Select).value = "threads"   # a site is a channel

    @on(Select.Changed, "#view")
    def _view_changed(self, event: Select.Changed) -> None:
        """A site is a channel (UN-RFC 0035 section 3); a feed's channel takes comments."""
        help_label = self.query_one("#view-help", Label)
        kind = self.query_one("#type", Select)
        if event.value == "pages":
            kind.value = "channel"
            help_label.update("A site is a channel: you, and the posters you add, write its "
                              "pages. n writes the first page; index is the home page.")
        elif event.value == "feed":
            if kind.value == "channel":
                self.query_one("#comments", Select).value = "open"
            help_label.update("A feed: entries newest first, comments under each. As a "
                              "channel only you post and anyone comments; open: anyone posts.")
        else:
            help_label.update("")

    @on(Input.Changed, "#name")
    def _check_name(self, event: Input.Changed) -> None:
        help_label = self.query_one("#name-help", Label)
        name = event.value.strip().lower()
        problem = group_name_problem(name) if name else None
        help_label.update(f"Not valid yet: {problem}" if problem else GROUP_NAME_HELP)
        help_label.set_class(bool(problem), "error")

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        name = self.query_one("#name", Input).value.strip().lower()
        problem = group_name_problem(name) if name else "a name is required"
        if problem:                       # stay open, keep what was typed
            # said under the name field, not in a toast that would cover the buttons
            help_label = self.query_one("#name-help", Label)
            help_label.update(f"Not valid yet: {problem}")
            help_label.set_class(True, "error")
            self.query_one("#name", Input).focus()
            return
        view, fast = DELIVERY[self.query_one("#view", Select).value]
        self.dismiss({"name": name, "gtype": self.query_one("#type", Select).value,
                      "title": self.query_one("#title", Input).value.strip(),
                      "local": self.query_one("#where", Select).value == "local",
                      "rules": self.query_one("#rules", TextArea).text.strip(),
                      "rules_type": markup.text_type_for(
                          self.query_one("#markdown", Checkbox).value,
                          self.query_one("#rules", TextArea).text),
                      "view": view, "low_latency": fast,
                      "kinds": KINDS[self.query_one("#kinds", Select).value],
                      "comments": self.query_one("#comments", Select).value,
                      "attachments": (FILE_CHOICES[self.query_one("#files", Select).value]
                                      if self.query_one("#type", Select).value
                                      in FILES_GROUP_TYPES else None)})


class GroupInfoDialog(Dialog):
    """Title, type, owner and rules of a group (UN-RFC 0014)."""

    def __init__(self, gid: str, data: dict, owner_label: str, can_edit: bool, first_time: bool,
                 node: str = ""):
        super().__init__()
        self.gid, self.data, self.owner_label, self.node = gid, data, owner_label, node
        self.can_edit, self.first_time = can_edit, first_time

    def compose(self) -> ComposeResult:
        """A group's info: its address, owner, type, view, rules, retention, node and
        settings."""
        d = self.data
        kinds = GROUP_KINDS
        lines = [f"Group:     {self.gid}",
                 f"Type:      {kinds.get(d.get('type', ''), d.get('type', '?'))}",
                 f"Owner:     {self.owner_label}",
                 f"Shown as:  {d.get('view', 'threads')}"
                 + ("   fast delivery (less anonymity about who posts from where)"
                    if d.get("low_latency") else ""),
                 "Takes:     " + {("post",): "posts with a subject only (a discussion group)",
                                  ("chat",): "chat lines only (a chat group)"}.get(
                     tuple(d.get("kinds") or ()), "posts and chat lines"),
                 *([f"Comments:  {comments_label(d)}"] if d.get("type") == "channel" else []),
                 *([f"Reactions: {reactions_label(d)}"] if d.get("type") != "inbox" else []),
                 f"Postage:   {d.get('min_pow', 0)} bits     Kept for: {d.get('retention_days', 90)} days"
                 + ("     Local to one node" if d.get("local") else ""),
                 f"Version:   {d.get('serial', 1)}"]
        if self.node:
            lines.append(f"Read from: {self.node}")
        label = getattr(self.app.session, "identity_label", None)
        for role, title in (("moderators", "Moderators:"), ("posters", "Posters:  ")):
            if d.get(role):
                names = [label(k) if label else k[:20] for k in d[role][:6]]
                lines.append(f"{title} " + ", ".join(names) + (" ..." if len(d[role]) > 6 else ""))
        bans = [b for b in d.get("banned", []) if isinstance(b, dict)]
        if bans:                                  # public on purpose (UN-RFC 0004, 5.1)
            lines.append(f"Banned:    {len(bans)} "
                         + ("person" if len(bans) == 1 else "people") + " - "
                         + "; ".join(f"{b.get('key', '')[8:16]}...: {b.get('reason') or 'no reason given'}"
                                     for b in bans[:5]) + (" ..." if len(bans) > 5 else ""))
        from unseenet.core import portals as _portals
        found = _portals.portals(d)
        if found:                                  # UN-RFC 0023 3.5: always shown
            lines += ["", "Also published outside Tor, through portals:"]
            lines += ["  " + line for line in _portals.describe(found).splitlines()]
        if d.get("description"):
            lines += ["", d["description"]]
        with Vertical(classes="dialog"):
            yield Label(("Before you post - the rules of " if self.first_time and d.get("rules")
                         else "About ") + (d.get("title") or self.gid.rpartition("@")[0]),
                        classes="title", markup=False)
            yield Static("\n".join(lines), markup=False)
            yield Label("Rules" if d.get("rules") else "This group has no rules.", classes="title")
            if d.get("rules"):
                with VerticalScroll(id="rules-view"):
                    yield Static(manifest_text(d["rules"], d.get("rules_type")), markup=False)
            with Horizontal(classes="buttons"):
                if self.can_edit:
                    yield Button("Edit group", id="edit")
                yield Button("OK" if self.first_time else "Close", variant="primary", id="cancel")

    def on_mount(self) -> None:
        """Show the rules as Markdown or plain text, as the group says."""
        if self.data.get("rules"):
            self.query_one("#rules-view").styles.max_height = 14

    @on(Button.Pressed, "#edit")
    def _edit(self) -> None:
        self.dismiss("edit")


class EditGroupDialog(Dialog):
    """Owner: change title, description, rules, view and delivery (a new manifest)."""

    def __init__(self, data: dict):
        super().__init__()
        self.data = data

    def compose(self) -> ComposeResult:
        """The owner's settings of a group: title, description, rules, view, comments,
        files, reactions, pins, pages."""
        d = self.data
        current = "fastchat" if d.get("low_latency") else d.get("view", "threads")
        if current not in DELIVERY:                  # a view this client does not know
            current = "threads"
        with Vertical(classes="dialog compose"):
            yield Label(f"Edit {d.get('name', '')} - every node gets the new version", classes="title",
                        markup=False)
            yield Input(value=d.get("title", ""), placeholder="Title", id="title")
            yield Input(value=d.get("description", ""), placeholder="Description (one paragraph)",
                        id="description")
            yield Select(DELIVERY_CHOICES + ([SITE_CHOICE] if current == "pages"
                                             or d.get("type") == "channel" else []),
                         value=current, allow_blank=False, id="view")   # a site: channels only
            if d.get("type") == "channel":
                yield Select(COMMENTS_CHOICES, value=d.get("comments", "none"), allow_blank=False,
                             id="comments")
                yield Input(value=str(d.get("comment_days") or ""), id="comment_days",
                            placeholder="Days a thread takes comments (empty: always)")
            if d.get("type") != "inbox":                 # UN-RFC 0048 section 4.1
                r = d.get("reactions")
                yield Select(REACTIONS_CHOICES, value=reactions_choice(r), allow_blank=False,
                             id="reactions")
                yield Input(value=" ".join(r) if isinstance(r, list) else "", id="reaction_set",
                            placeholder="Your own set: up to 12 emoji, e.g. 🎉 ❤️ 👏")
            if d.get("type") in FILES_GROUP_TYPES:      # #496
                choice = file_choice(d.get("attachments"))
                choices = FILES_CHOICES + ([("Files as they are now (limits set elsewhere)",
                                             "custom")] if choice == "custom" else [])
                yield Select(choices, value=choice, allow_blank=False, id="files")
            yield Input(value=str(d.get("min_pow", 0)), placeholder="Minimum postage in bits (16-24)",
                        id="min_pow")
            yield Label("Rules - shown to people before their first post:", classes="hint")
            yield TextArea(d.get("rules", ""), id="body", soft_wrap=True)
            yield Checkbox(MARKDOWN_HINT, id="markdown",
                           value=writes_markdown(self.app, d.get("rules_type"), d.get("rules", "")))
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Publish", variant="primary", id="ok")

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        try:
            min_pow = int(self.query_one("#min_pow", Input).value.strip() or 0)
        except ValueError:
            self.app.notify("Postage must be a number", severity="error")
            return
        if not 0 <= min_pow <= 28:
            self.app.notify("Postage between 0 and 28 bits, please", severity="error")
            return
        view, fast = DELIVERY[self.query_one("#view", Select).value]
        changes = {"title": self.query_one("#title", Input).value.strip(),
                   "description": self.query_one("#description", Input).value.strip(),
                   "rules": self.query_one("#body", TextArea).text.strip(),
                   "view": view, "low_latency": fast, "min_pow": min_pow}
        changes["rules_type"] = markup.text_type_for(self.query_one("#markdown", Checkbox).value,
                                                     changes["rules"])
        if self.data.get("type") == "channel":
            days = self.query_one("#comment_days", Input).value.strip()
            if days and (not days.isdigit() or int(days) < 1):
                self.app.notify("Comment days: a whole number of days, or empty", severity="error")
                return
            changes["comments"] = self.query_one("#comments", Select).value
            changes["comment_days"] = int(days) if days else None
        if self.query("#files"):
            choice = self.query_one("#files", Select).value
            if choice != "custom":                       # custom limits are kept as they are
                changes["attachments"] = FILE_CHOICES[choice]
        if self.query("#reactions"):                     # UN-RFC 0048 section 4.1
            choice = self.query_one("#reactions", Select).value
            if choice == "custom":
                own = self.query_one("#reaction_set", Input).value.replace(",", " ").split()
                if not own or len(own) > rx.MAX_SET or not all(rx.is_emoji(e) for e in own):
                    self.app.notify(f"Your own set: 1 to {rx.MAX_SET} emoji, with spaces "
                                    "between", severity="error")
                    return
                changes["reactions"] = list(dict.fromkeys(own))
            else:
                changes["reactions"] = {"default": None, "none": [], "any": "any"}[choice]
        self.dismiss(changes)


class BrowseTree(Tree):
    """The tree of all groups. Space or Enter subscribes; with the mouse, only a
    click on the [ ] box does - a click on the rest of the line only shows what
    the group is."""
    BINDINGS = [Binding("space", "toggle_subscription", "Subscribe", show=False)]
    BOX = 3                                      # "[x]" at the start of a group's label

    class Toggle(Message_):
        def __init__(self, node) -> None:
            super().__init__()
            self.node = node

    def action_toggle_subscription(self) -> None:
        """Space: follow the group under the cursor, or stop."""
        node = self.cursor_node
        if node is None:
            return
        if isinstance(node.data, GroupInfo):
            self.post_message(self.Toggle(node))
        else:
            node.toggle()                        # a hierarchy: open or close it

    async def _on_click(self, event) -> None:
        meta = event.style.meta
        node = self.get_node_at_line(meta["line"]) if "line" in meta else None
        if node is None or not isinstance(node.data, GroupInfo) or meta.get("toggle"):
            return                               # Tree's own handler: hierarchies as before
        event.prevent_default()                  # not Tree's: a click must not subscribe
        self.cursor_line = meta["line"]          # shows the group's details
        region = self._get_label_region(meta["line"])
        x = event.x + self.scroll_offset.x - self.gutter.left
        if region is not None and region.x <= x < region.x + self.BOX:
            self.post_message(self.Toggle(node))


# Typing in a list moves to its search box with the first letter in it; an Input
# that selects all on focus would let the next letter replace it (Textual >= 0.86).
NO_SELECT_ON_FOCUS = ({"select_on_focus": False}
                      if "select_on_focus" in inspect.signature(Input.__init__).parameters else {})


class BrowseDialog(Dialog):
    """All groups on the node, by hierarchy; Enter toggles subscription. Typing (or /)
    searches the list, locally (#13)."""
    CANCEL_RESULT = True
    BINDINGS = [Binding("slash", "search", "Search")]

    def __init__(self, groups: list[GroupInfo]):
        super().__init__()
        self.groups = groups
        self.known = {g.group_id for g in groups}
        self.current: GroupInfo | None = None
        self.peeked: dict[str, tuple[dict, int] | str] = {}   # group -> (manifest, posts) or error
        self.fetched: dict[str, dict] = {}       # manifests read for a type: search (UN-RFC 0041)
        self.query_text = ""

    def compose(self) -> ComposeResult:
        """Browsing what the nodes carry: a filter and the tree of hierarchies and groups."""
        with Vertical(classes="dialog list"):
            several = len(self.app.session.nodes) > 1
            yield Label("Groups on your nodes" if several else "Groups on this node", classes="title")
            yield Label("Space, Enter or a click on [ ]: subscribe or unsubscribe. "
                        "Enter on a hierarchy opens or closes it.  Esc: close.", classes="hint")
            yield Label(ICON_LEGEND, classes="hint", markup=False)
            yield Input(placeholder=f"Search (/ or just type): {groupfilter.HELP}",
                        id="browse-filter", **NO_SELECT_ON_FOCUS)
            yield Label("", id="browse-count", classes="hint", markup=False)
            tree: Tree = BrowseTree("groups", id="browse-tree")
            tree.show_root = False
            self._fill(tree, self.groups, searching=False)
            yield tree
            yield Static("Move to a group to see what it is.", id="group-details",
                         classes="details", markup=False)
            with Horizontal(classes="buttons"):
                yield Button("OK", variant="primary", id="cancel")

    def _fill(self, tree: Tree, groups: list[GroupInfo], searching: bool) -> None:
        tree.clear()
        expand = searching or len(groups) <= 15
        for top, items in by_hierarchy(groups):
            parent = tree.root
            if top:
                subs = sum(1 for g in items if g.subscribed)
                parent = tree.root.add(Text(f"{top}.*   {len(items)} groups"
                                            + (f", {subs} subscribed" if subs else "")),
                                       expand=expand or subs > 0)
            for g in items:
                parent.add_leaf(self._label(g), data=g)
        if not groups:
            tree.root.add_leaf(Text("No groups match your search." if searching else
                                    "No groups yet - use Ctrl+P, Create group, to create one."))

    def _known_data(self) -> dict:
        out = dict(self.fetched)
        out.update({gid: p[0] for gid, p in self.peeked.items() if isinstance(p, tuple)})
        return out

    def _refilter(self) -> None:
        s = self.app.session
        shown = s.filter_groups(self.groups, self.query_text, self._known_data())
        f = groupfilter.parse(self.query_text)
        self._fill(self.query_one("#browse-tree", Tree), shown, searching=bool(f))
        count = (f"{len(shown)} of {len(self.groups)} groups" if f else "")
        if f.unknown:
            count += ("  -  " if count else "") + "not understood: " + " ".join(f.unknown)
        if f.needs_manifests and any(not g.type and g.group_id not in self._known_data()
                                     for g in self.groups):
            count += "  -  reading group types ..."
            self._fetch_types()
        self.query_one("#browse-count", Label).update(count)

    @work(exclusive=True, group="types", exit_on_error=False)
    async def _fetch_types(self) -> None:
        try:
            await self.app.session.group_manifests(self.groups, self.fetched)
        except (SessionError, OSError, ConnectionError, NNTPError, ValueError,
                asyncio.IncompleteReadError) as exc:
            self.app.notify(f"Could not read the group types: {exc}", severity="warning")
            for g in self.groups:                 # do not ask again in this dialog
                self.fetched.setdefault(g.group_id, {})
        if self.is_attached:
            self._refilter()

    @on(Input.Changed, "#browse-filter")
    def _search(self, event: Input.Changed) -> None:
        self.query_text = event.value
        self._refilter()

    @on(Input.Submitted, "#browse-filter")
    def _search_done(self, event: Input.Submitted) -> None:
        self.query_one("#browse-tree", Tree).focus()

    def action_search(self) -> None:
        """/: to the filter."""
        self.query_one("#browse-filter", Input).focus()

    def action_cancel(self) -> None:
        """Esc: clear the filter first, then close."""
        box = self.query_one("#browse-filter", Input)
        if box.value:                            # Esc clears the search first
            box.value = ""
            self.query_one("#browse-tree", Tree).focus()
            return
        super().action_cancel()

    def on_key(self, event) -> None:
        """Just typing in the list starts a search."""
        char = event.character
        if (getattr(self.focused, "id", None) == "browse-tree" and char and char.isprintable()
                and char not in (" ", "/")):
            box = self.query_one("#browse-filter", Input)
            box.focus()
            box.insert_text_at_cursor(char)
            event.stop()
            event.prevent_default()

    def _label(self, g: GroupInfo) -> Text:
        mark = "[x]" if g.subscribed else "[ ]"
        peek = self.peeked.get(g.group_id)
        data = peek[0] if isinstance(peek, tuple) else (
            self.fetched.get(g.group_id) or self.app.session.manifest_data(g.group_id))
        icons = group_icons(g.type, data)
        where = ""
        if len(self.app.session.nodes) > 1 and g.nodes:
            where = f"   @ {self.app.session.node_label(g.nodes[0])}" + (f" +{len(g.nodes) - 1}" if len(g.nodes) > 1 else "")
        return Text(f"{mark} {icons} {self.app.session.group_label(g.group_id, self.known)}   {g.description}{where}")

    def on_mount(self) -> None:
        """The focus in the tree."""
        self.query_one("#browse-tree", Tree).focus()

    @on(Tree.NodeHighlighted, "#browse-tree")
    def _highlighted(self, event: Tree.NodeHighlighted) -> None:
        g = event.node.data
        self.current = g if isinstance(g, GroupInfo) else None
        if self.current is None:
            return
        self._show(g)
        if g.group_id not in self.peeked:
            self._peek(g)

    @work(group="peek", exit_on_error=False)
    async def _peek(self, g: GroupInfo) -> None:
        # Not exclusive: cancelling a request half-way would break the connection.
        # Wait a moment instead, so scrolling through the list asks for nothing.
        await asyncio.sleep(0.5)
        if self.current is not g or g.group_id in self.peeked:
            return
        self.peeked[g.group_id] = "asking the node ..."
        self._show(g)
        try:
            self.peeked[g.group_id] = await asyncio.wait_for(
                self.app.session.peek_group(g.group_id, g.nodes[0] if g.nodes else None), 60)
        except (SessionError, OSError, ConnectionError, NNTPError, ValueError,
                asyncio.TimeoutError, asyncio.IncompleteReadError) as exc:
            self.peeked[g.group_id] = f"could not ask the node ({exc})"
        peek = self.peeked[g.group_id]
        if isinstance(peek, tuple) and peek[0].get("type"):
            g.type = peek[0]["type"]          # its icons, now that they are known
            node = self.query_one("#browse-tree", Tree).cursor_node
            if node is not None and node.data is g:
                node.set_label(self._label(g))
        if self.current is g:
            self._show(g)

    def _owner(self, g: GroupInfo, data: dict) -> str:
        from unseenet.known_nodes import NODES_GROUP
        s = self.app.session
        fp = g.group_id.rpartition("@")[2]
        if fp == s.my_inbox.rpartition("@")[2]:
            return "you"
        if fp == NODES_GROUP.rpartition("@")[2]:
            return "the Unseenet project"
        name = s.contact_name(data["owner"]) if data.get("owner") else None
        return f"{name} √ ({fp})" if name else fp

    def _show(self, g: GroupInfo) -> None:
        peek = self.peeked.get(g.group_id)
        data = peek[0] if isinstance(peek, tuple) else self.app.session.manifest_data(g.group_id)
        lines = [g.group_id]
        title = data.get("title") or g.description
        if title:
            lines.append(title)
        if data.get("description") and data["description"] != title:
            lines.append(data["description"])
        if data:
            facts = [GROUP_KINDS.get(data.get("type", ""), data.get("type", "?"))]
            if isinstance(peek, tuple):
                facts.append(f"{peek[1]} posts")
            facts.append(f"kept {data.get('retention_days', 90)} days")
            lines.append("Type:   " + ",  ".join(facts))
        elif isinstance(peek, str):
            lines.append(f"({peek})")
        lines.append(f"Owner:  {self._owner(g, data)}")
        warning = naming.group_warning(self.app.session, g.group_id, self.known)
        if warning:
            lines.append(("Name:   " if warning.startswith("Endorsed") else "! ") + warning)
        if len(g.nodes) > 1 or len(self.app.session.nodes) > 1:
            lines.append("On:     " + ", ".join(self.app.session.node_label(n) for n in g.nodes))
        if data.get("type") == "closed":
            lines.append("Members only: you need an invitation from the owner to read it.")
        if self._not_mine_inbox(g):
            lines.append("An inbox: anyone can write to it, only its owner can read it.")
        if data.get("view") == "data":
            lines.append("Data for programs (lists, certificates), not posts for people.")
        if data.get("rules"):
            first = data["rules"].strip().splitlines()[0]
            lines.append(f"Rules:  {first[:90]}" + (" ..." if len(data["rules"].strip()) > len(first) else ""))
        try:
            self.query_one("#group-details", Static).update("\n".join(lines))
        except Exception:  # noqa: BLE001  (the dialog was closed meanwhile)
            pass

    def _not_mine_inbox(self, g: GroupInfo) -> bool:
        """An inbox someone else owns (a person's, or a node operator's contact
        inbox): you can write to it, but only its owner can read it."""
        peek = self.peeked.get(g.group_id)
        data = peek[0] if isinstance(peek, tuple) else self.app.session.manifest_data(g.group_id)
        kind = data.get("type") or g.type
        return kind == "inbox" and g.group_id.rpartition("@")[2] != \
            self.app.session.my_inbox.rpartition("@")[2]

    @on(Tree.NodeSelected, "#browse-tree")
    def _selected(self, event: Tree.NodeSelected) -> None:
        self._toggle(event.node)

    @on(BrowseTree.Toggle)
    def _clicked(self, event: "BrowseTree.Toggle") -> None:
        self._toggle(event.node)

    def _toggle(self, node) -> None:
        g = node.data
        if not isinstance(g, GroupInfo):
            return
        if not g.subscribed and self._not_mine_inbox(g):
            self.app.notify(f"{self.app.session.group_label(g.group_id, self.known)} is an inbox: anyone can write to it, only its owner "
                            "can read it. To write to a node's operator: Ctrl+P, Your nodes, "
                            "Details, Write to operator.", severity="warning", timeout=10)
            return
        g.subscribed = not g.subscribed
        self.app.session.subscribe(g.group_id, g.subscribed,
                                   node=g.nodes[0] if g.subscribed and g.nodes else None)
        node.set_label(self._label(g))
