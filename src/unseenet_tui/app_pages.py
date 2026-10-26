"""UnseenetApp's sites and pages, and the dialogs and tools after them in the class.

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


class PagesMixin:

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
        """Browse the groups your nodes carry, to follow some."""
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
        """Create a group, then show it."""
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
        """Invite a key to the closed group shown."""
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
        """The group's address, to copy (UN-RFC 0031 3.4.1)."""
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
        """A profile: the key given, or the author of the selected post."""
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
        """Edit your profile and publish it."""
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
        """Your key, to give people for private messages and invitations."""
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
