"""UnseenetApp's the chat view: chat lines, sending, files in chat (UN-RFC 0008 5).

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


from .app_reports import HIDDEN_MARK, reported_text  # noqa: E402


class ChatMixin:

    # --- chat view (UN-RFC 0008 section 5) ---
    @work(exclusive=True, group="emoji")
    async def action_insert_emoji(self) -> None:
        """Insert emoji (the palette, #6): pick one by name or category; its :shortcode:
        goes into the post being written, or else the chat box."""
        from .emoji import EmojiPickerDialog
        top, target = self.screen, None
        if top.query("#body") and isinstance(top.query_one("#body"), TextArea):
            target = top.query_one("#body", TextArea)       # the post editor
        elif self.query("#chat-input"):
            target = self.query_one("#chat-input", TextArea)
        code = await self.push_screen_wait(EmojiPickerDialog())
        if code and target is not None:
            target.insert(f":{code}: ")
            target.focus()

    def view_of(self, gid: str | None) -> str:
        """How a group is shown here: threads, chat, feed or pages, as the core says."""
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
            if getattr(m, "reported", None):                # hidden by a report (UN-RFC 0045)
                line = Text(f"{when} ", style="dim")
                line.append(f"{who}: {HIDDEN_MARK} {reported_text(m)}  (Ctrl+P: Show anyway)", style="dim italic")
                log.write(line)
                continue
            if self.label_covered(m):                        # a warning you blur or hide (0053)
                line = Text(f"{when} ", style="dim")
                line.append(f"{who}: ", style="bold")
                line.append_text(self.warning_text(m) or Text())
                line.append("  (Ctrl+P: Show anyway)", style="dim italic")
                log.write(line)
                continue
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
        """Send mention notices when you name someone, or not (UN-RFC 0032)."""
        on = not self.session.notice_settings()["send"]
        self.session.set_notice_settings(send=on)
        self.notify("People you mention by name are told in their inbox (you can say no per "
                    "post)" if on else "People you mention are no longer told; they see it if "
                    "they follow the group")

    def action_toggle_notices_receive(self) -> None:
        """Take mention notices from others, or not."""
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
            self.action_group_ref(f"!{name}@{fp}")
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
        """Show the group as threads or as a chat, for you."""
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
        """Send a chat line (with files) through the core, showing the postage work."""
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
        t = self.session.team_sender(m) if m.private else None    # on an inbox's behalf (0047, 8)
        if t:
            return (t["name"] or "a team inbox") + ("" if t["verified"] else " (not verified)")
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
        paused = ("  ·  [b]showing all - your rules are paused here[/b] (Ctrl+P: Show all again to go back)"
                  if getattr(self, "_show_all", None) == gid else "")
        if gid == FOR_YOU:
            c = self.session.for_you_counts()
            self.query_one("#threads-title", Static).update(
                f"{REPLY_MARK} For you: replies to you and posts that name you  ·  "
                f"{REPLY_MARK} {c.replies} new replies, {MENTION_MARK} {c.mentions} new mentions  ·  "
                f"from all your groups{paused}")
            return
        if gid.startswith("tag:"):                           # everything with a tag (0052, 7)
            n = len(self.messages)
            self.query_one("#threads-title", Static).update(
                f"# {gid[4:]}  ·  {n} post{'s' if n != 1 else ''} tagged so in your groups and feeds, "
                "newest first  ·  the authors' tags, the groups', labellers' topics")
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
        team = self.team_of(gid)                            # a team inbox you read (0047)
        if team:
            rules += ("  ·  you reply as the team" if team["reader"] else
                      "  ·  the owner removed you as a reader (Ctrl+P: Forget this team inbox)")
        reported = sum(n for _, _, n in self.session.hidden_by_reports(gid))
        rules += f"  ·  {reported} hidden by reports (Ctrl+P: Show anyway)" if reported else ""
        hidden = self.session.hidden_by_blocks(gid)
        blocked = ("  ·  " + "; ".join(f"{n} hidden: blocked in {h}.*: {r}" for h, r, n in hidden[:2])
                   if hidden else "")
        paused = ("  ·  [b]showing all - your rules are paused[/b] (Ctrl+P: Show all again)"
                  if getattr(self, "_show_all", None) == gid else "")
        self.query_one("#threads-title", Static).update(
            f"{name}  ·  {kind}{fast}  ·  {total} posts, {unread} unread{rules}{blocked}{paused}")

    def article(self, msg: Message) -> tuple[str, Text]:
        """The header (markup) and the body of a message, as the reader shows them."""
        date = msg.date.astimezone().strftime("%A %d %B %Y %H:%M") if msg.date else ""
        if getattr(msg, "origin_network", ""):               # UN-RFC 0049 section 5.1
            date = (f"posted {date}\n{ORIGIN_MARK} {escape(origin_text(msg))} "
                    "[dim](the date it was first published is the poster's claim)[/dim]")
        if msg.edited:                                       # UN-RFC 0042, 4.2
            date += f"  (edited {edited_when(msg)})"
        langs = list(getattr(msg, "languages", ()))           # only where it differs (0052 5a)
        main = list(self.session.group_info(msg.group).languages) if msg.group and not msg.private else []
        if langs and not (main and all(x in main for x in langs)):
            date += "  ·  " + " · ".join(langs)
        if msg.to_key:
            who = f"you  ·  to [b]{escape(self._key_label(msg.to_key))}[/b]"
        elif msg.private and msg.readable and not msg.sender_key:
            who = "anonymous (the sender did not identify themselves)"
        elif msg.private and self.session.team_sender(msg):    # never shown as a person (0047, 8)
            t = self.session.team_sender(msg)
            who = (f"[b]{escape(t['name'] or 'a team inbox')}[/b]  ·  "
                   + ("written on the inbox's behalf, verified" if t["verified"]
                      else "claims to write for an inbox - not verified"))
        else:
            petname = self.session.contact_name(msg.author_key)
            registered = " ".join("@" + c.name for c in naming.names_of(self.session, msg.author_key))
            who = (f"{escape(msg.from_name)}  ·  key {escape(msg.author_key[:24])}…  ({msg.author_fp})"
                   + ("  ·  verified" if msg.sender_key else "")
                   + (f"  ·  registered [b]{escape(registered)}[/b]" if registered else "")
                   + (f"  ·  your contact [b]{escape(petname)}[/b]" if petname else ""))
        if msg.private and msg.readable and self.session.received_report(msg):   # a report to you
            date += ("\n[b]A report: Ctrl+P Open reported post"
                     + (", or Publish a spam report[/b]" if self.session.issuer_names() else "[/b]"))
        if getattr(msg, "team_invite", None):              # a team key for you (0047, 6)
            date += "\n[b]Ctrl+P: Read this inbox with the team[/b]"
        note = ("\n@ private message you sent - only they can read the copy on the node"
                if msg.to_key else
                "\n@ private message - only you can read it" if msg.private
                else "\n∩ end-to-end encrypted" if msg.encrypted else "")
        if not msg.private and self.session.write_permission(
                msg.group, reply_to=msg, kind=None).code == "comments_closed":
            note += "\n✕ Comments closed"                  # where Reply would be (#181)
        head = f"[b]{escape(msg.subject)}[/b]\nFrom: {who}\nDate: {date}{note}"
        flags = self.warning_text(msg)                       # warnings over the subject (0053)
        if flags is not None:
            head = flags.markup + "\n" + head
        if getattr(msg, "reported", None):                  # hidden by a report (UN-RFC 0045, 7.3)
            head = f"[b]{HIDDEN_MARK} {escape(reported_text(msg))}[/b]\nFrom: {who}\nDate: {date}"
            return head, Text("\nAn issuer you follow reports this post, so it is hidden.\n"
                              "Ctrl+P: Show anyway - or Stop following this report issuer.", style="dim")
        if msg.readable and self.label_covered(msg):         # blurred or hidden, as you chose
            st = self.label_state(msg)
            words = ", ".join(w["warning"] for w in st["warnings"]) or "your rules"
            body = Text(f"\nText and pictures are hidden: {words}.\n", style="dim")
            body.append("Ctrl+P: Show anyway", style="bold")
            return head, body
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
            topics = self.topics_line(msg)                    # tags and topics (UN-RFC 0052)
            if topics is not None:
                body.append("\n\n")
                body.append(topics)
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
        """Enter: read the selected post on its own screen."""
        if self.current_message is None:
            self.notify("Select a post first", severity="warning")
            return
        if self.session.is_notice(self.current_message):     # go to the post it is about
            self.action_open_mentioned()
            return
        self.push_screen(ReadScreen(self.current_message))

    @work(exclusive=True, group="save", exit_on_error=False)
    async def _save_soon(self) -> None:
        await asyncio.sleep(2)
        self.session.save()
        await self._render_groups()
