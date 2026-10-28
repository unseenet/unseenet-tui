"""The module-level helpers and constants the TUI's parts share: marks, names of networks and groups, node details, icons.

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


REFRESH_SECONDS = 60           # a fallback; new posts normally arrive by push (UN-RFC 0008)
REPO_URL, DOCS_URL = about.REPO_URL, about.DOCS_URL
ACROSS = (FOR_YOU, TIMELINE)             # views across groups: act in the post's own group
FEED_MARK = "≡"                          # the timeline in the group list (UN-RFC 0034)
TIMELINE_MARK = "•"                      # a group whose entries come into it (0034 6.2)
START_FEED = ("action", "start-feed")    # "Start your feed" at the top of the group list (#495)
START_SITE = ("action", "start-site")    # ... and "Start your site", the same way
CHATS = ("action", "chats")              # "Private chats" at the top: every chat, in a list (#39)
CHATS_SHOWN = 3                          # unread chats under it; the rest behind Show all (#73)
CONTACTS = ("action", "contacts")        # "Your contacts" at the top, beside the private items
FOLDED = "folded:"                       # a feed's "View N more comments" row (#34)
FEED_SHOWN = 1                           # comments under an entry before "View more", as the GUI
COMMENT_PAGE = 20                        # ... and how many more each time
OLDER = "older:"                         # "View older threads" at the end of a large group (#86)
SHOWN_FIRST = 200                        # threads, entries or chat lines shown first, as the GUI


def newest_rows(rows: list, limit: int, chat: bool) -> tuple[list, bool]:
    """A large group's rows cut to its newest (#86), the same as the core answers the GUI
    (UN-RFC 0046, ``limit``): the last ``limit`` lines of a chat (they come oldest first),
    else the first ``limit`` threads or feed entries (newest first), each with every row
    under it. Returns the rows and whether older ones were left out."""
    if chat:
        return (rows[-limit:], True) if len(rows) > limit else (rows, False)
    out, starts = [], 0
    for row in rows:
        if row[1] == 0:
            starts += 1
            if starts > limit:
                return out, True
        out.append(row)
    return out, False
CLIP_LINES = 10                          # an entry's text in a feed before "… more"
SITE_MARK = "▤"                          # your site in the group list (UN-RFC 0035)
REPLY_MARK, MENTION_MARK = "◄", "♦"   # in the basic character set (Windows console)


def for_you_marks(c) -> str:
    """' ◄1 ♦2' for a ForYouCount: replies and mentions apart (#170); '' when none."""
    return ((f" {REPLY_MARK}{c.replies}" if c.replies else "")
            + (f" {MENTION_MARK}{c.mentions}" if c.mentions else ""))


def for_you_news(before, now) -> str | None:
    """The notification for new replies/mentions since the last refresh, or None."""
    replies, mentions = now.replies - before.replies, now.mentions - before.mentions
    parts = []
    if replies > 0:
        parts.append(f"{REPLY_MARK} {replies} new {'reply' if replies == 1 else 'replies'} "
                     "to your posts")
    if mentions > 0:
        parts.append(f"{MENTION_MARK} {mentions} new {'post' if mentions == 1 else 'posts'} "
                     "naming you")
    if not parts:
        return None
    return "  ·  ".join(parts) + ". See 'For you' at the top of your groups."


def sess_default_pow() -> int:
    """The core's default proof of work, read when used so tests can lower it."""
    return _session_module.DEFAULT_POW


def group_name_problem(name: str) -> str | None:
    """Why a group name is not valid (UN-RFC 0004 section 1), or None."""
    if len(name) > 128:
        return "at most 128 characters"
    bad = sorted({c for c in name if not re.match(r"[a-z0-9.+_-]", c)})
    if bad:
        shown = " ".join("space" if c == " " else repr(c) for c in bad)
        return f"these characters are not allowed: {shown}"
    if name.startswith(".") or name.endswith(".") or ".." in name:
        return "a dot must sit between two parts"
    if any(not part[0].isalnum() for part in name.split(".")):
        return "each part must start with a letter or digit"
    return None


GROUP_KINDS = {"open": "open - anyone can read and post", "moderated": "moderated",
               "channel": "channel - only chosen people post",
               "closed": "closed - members only, encrypted",
               "inbox": "inbox - anyone writes, only the owner reads"}


def known_node_details(n) -> str:
    """A known node (UN-RFC 0010) as a few lines, to choose the right one."""
    lines = [n.description or "(no description)"]
    if n.operator:
        lines.append(f"Run by:   {n.operator}")
    lines.append(f"Address:  {n.address}")
    return "\n".join(lines)


def node_details(address: str, info: dict | None, known=None, groups: int = 0,
                 default: bool = False) -> str:
    """One of your nodes: what it says about itself (UN-RFC 0011) and the node list."""
    i = info or {}
    p = i.get("policy", {})
    name = i.get("name") or (known.name if known else "") or address
    lines = [name + ("   ♦ default node" if default else "")]
    about = i.get("description") or (known.description if known else "")
    if about:
        lines.append(about)
    operator = i.get("operator") or (known.operator if known else "")
    if operator:
        lines.append(f"Run by:   {operator}")
    lines.append(f"Address:  {address}")
    if info is None:
        lines.append("Not reachable right now.")
    elif p:
        lines.append(f"Policy:   new groups {p.get('new_groups', '?')}, kept "
                     f"{p.get('retention_days', '?')} days, postage {p.get('min_pow', '?')} "
                     f"bits, logs {p.get('logging', '?')}")
        if p.get("excludes"):
            lines.append(f"Leaves out: {p['excludes']}")
    lines.append(f"Groups:   {groups} of yours are read from here"
                 + ("   ·   in the project's node list" if known else ""))
    return "\n".join(lines)


# Group icons: who may write and read, then how the group is shown. Only
# characters from WGL4, the set every terminal font has - even the classic
# Windows console with Consolas - and one cell wide each.
ICONS = {"threads": "≡", "chat": "»", "data": "¤", "moderated": "√", "channel": "►",
         "closed": "∩", "inbox": "@", "local": "⌂", "unknown": "·"}
_ONION = re.compile(r"^[a-z2-7]{56}\.onion(:\d{1,5})?$")   # what an n= hint may be
PIN_MARK = "[P]"                 # a pinned post (UN-RFC 0033 section 7.2): no emoji in WGL4
ICON_LEGEND = ("≡ posts  » chat  ¤ data for programs  √ moderated  ► channel  ∩ closed  "
               "@ inbox  ⌂ local (one node)  · not known yet  • in your timeline")


ORIGIN_MARK = "←"               # first published elsewhere (UN-RFC 0049); in WGL4
NETWORK_NAMES = {"facebook": "Facebook", "x": "X", "twitter": "X", "instagram": "Instagram",
                 "mastodon": "Mastodon", "bluesky": "Bluesky", "tiktok": "TikTok", "nostr": "Nostr",
                 "matrix": "Matrix", "usenet": "Usenet", "fidonet": "FidoNet",
                 "unseenet": "another Unseenet key"}


def network_name(network: str) -> str:
    """A network's name as people know it (facebook -> Facebook), for imported posts."""
    return NETWORK_NAMES.get(network, network.capitalize())


def origin_text(m: Message) -> str:
    """UN-RFC 0049 section 5.1: "Imported from Facebook · originally 3 August 2026" - the
    origin date is the poster's claim, never shown as the time of posting."""
    if not getattr(m, "origin_network", ""):
        return ""
    text = "Imported from " + network_name(m.origin_network)
    if m.origin_date:
        d = m.origin_date.astimezone()
        text += f" · originally {d.day} {d.strftime('%B %Y')}"
    if getattr(m, "origin_proven", ""):           # the old key, proven (UN-RFC 0049 7.3)
        text += f" · proven from the key {m.origin_proven[:20]}…"
    return text


def edited_when(m: Message) -> str:
    """When a post was edited: the time today, else the date (UN-RFC 0042, 4.2)."""
    at = m.edited.astimezone()
    return at.strftime("%H:%M") if at.date() == datetime.now().astimezone().date() \
        else at.strftime("%d %b %H:%M")


def group_icons(gtype: str | None, data: dict | None = None) -> str:
    """Two icons and a space between, e.g. '∩ »' for a closed chat group."""
    data = data or {}
    gtype = gtype or data.get("type")
    if not gtype:
        return f"{ICONS['unknown']}  "
    if gtype == "inbox":
        return f"{ICONS['inbox']}  "
    access = ICONS.get(gtype) if gtype in ("moderated", "channel", "closed") else \
        (ICONS["local"] if data.get("local") else " ")
    view = {"chat": ICONS["chat"], "data": ICONS["data"]}.get(data.get("view"), ICONS["threads"])
    return f"{access} {view}"


def is_personal_inbox(group_id: str) -> bool:
    """Whether a group id is someone's personal inbox (UN-RFC 0009)."""
    return group_id.split("@", 1)[0] == INBOX_NAME


def by_hierarchy(groups: list[GroupInfo]) -> list[tuple[str, list[GroupInfo]]]:
    """Group by the first part of the name, as Usenet hierarchies (dk.*, comp.*, ...).

    A hierarchy with a single group is returned as ('', [group]) so it can be shown
    without a folder of its own.
    """
    buckets: dict[str, list[GroupInfo]] = {}
    for g in groups:
        name = g.group_id.rpartition("@")[0]
        buckets.setdefault(name.split(".", 1)[0], []).append(g)
    out = []
    for top in sorted(buckets):
        items = sorted(buckets[top], key=lambda g: (g.group_id.rpartition("@")[0], g.group_id))
        out.append((top if len(items) > 1 else "", items))
    return out

CSS = """
Screen { background: $surface; }
#groups-pane { width: 34; border-right: solid $primary-darken-2; }
#groups-title, #threads-title { background: $primary-darken-2; color: $text; padding: 0 1; text-style: bold; }
#groups { height: 1fr; }
#groups-filter { display: none; }
#groups-filter.shown { display: block; }
#threads { height: 45%; }
#article { height: 1fr; padding: 0 1; border-top: solid $primary-darken-2; }
#article-head { color: $text-muted; }
#chat { height: 1fr; display: none; }
#chat-log { height: 1fr; padding: 0 1; }
#chat-pins { height: auto; padding: 0 1; background: $boost; display: none; }
#chat-pins.shown { display: block; }
#chat-input { margin: 0 1; height: auto; min-height: 3; max-height: 8;
  border: tall $border-blurred; padding: 0 1; }
#chat-input:focus { border: tall $accent; }
#status { height: 1; background: $boost; color: $text-muted; padding: 0 1; }
.unread { text-style: bold; }

ModalScreen { align: center middle; }
.dialog { width: 80; max-width: 100%; height: auto; max-height: 100%; overflow-y: auto;
          border: thick $primary; background: $panel; padding: 1 2; }
.dialog.list { width: 90%; max-width: 130; height: 94%; overflow-y: hidden; padding: 0 1; }
.dialog Label { width: 100%; }
.dialog.list Tree, .dialog.list DataTable { height: 1fr; min-height: 3; }
.dialog .details { height: auto; max-height: 10; margin-top: 1; padding: 0 1;
                   border-left: thick $primary-darken-2; color: $text; }
#settings-help { height: auto; max-height: 30; padding: 0 1; }
#profile-view { height: auto; max-height: 24; margin-top: 1; padding: 0 1;
                border-left: thick $primary-darken-2; }
#names-details { margin: 1 0 1 0; }        /* the key line must not touch the field below */
.dialog Label.title { text-style: bold; margin-bottom: 1; }
.dialog Input, .dialog Select { margin-bottom: 1; }
.dialog.compose { width: 96%; height: 94%; max-height: 100%; }
.dialog.about { width: 90; max-width: 100%; height: 94%; overflow-y: hidden; }
.dialog.about #about-text { height: 1fr; padding-right: 1; }
.dialog.compose #body { height: 1fr; margin-bottom: 1; }
.dialog.compose .metarow { height: auto; }
.dialog.compose .metarow .langcol { width: 34; height: auto; }
.dialog.compose .metarow #tags { width: 1fr; }
.dialog.compose .warnlist { height: auto; display: none; }
.dialog.compose .warnlist.open { display: block; }
.dialog.compose .warnlist Checkbox { width: auto; margin-right: 1; }
.dialog.compose.big { width: 100%; height: 100%; }
.dialog.compose.big .metarow, .dialog.compose.big #foot { display: none; }
.dialog .buttons { height: auto; align-horizontal: right; dock: bottom; }
.dialog Button { margin-left: 1; min-width: 8; width: auto; }
.dialog .hint { color: $text-muted; }
.dialog.report { width: 96; max-width: 100%; height: 94%; overflow-y: hidden; }
.dialog.report VerticalScroll, .dialog.issuers VerticalScroll { height: 1fr; }
.dialog.report #note { height: 8; min-height: 5; }
.dialog.report > .hint { margin-bottom: 1; }
.dialog.report Checkbox, .dialog.issuers Checkbox { margin: 0; }
.dialog.issuers { width: 96; max-width: 100%; height: 80%; overflow-y: hidden; }
.dialog #policy { height: 6; margin-bottom: 1; }
.dialog .error { color: $error; }
#groups { padding: 0 1; }
"""
