"""UnseenetApp's recovery words and copying an identity (UN-RFC 0050).

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


class IdentityMixin:

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
        """Get an identity from another device (UN-RFC 0050 3.6), then go on with it."""
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
        """About Unseenet and this client."""
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

    async def _link_card(self, body: str):
        """A link card for the first link in what was written (UN-RFC 0059): made over Tor
        and shown before it is sent - with it or without. None when cards are off, there
        is no link, or the page gives no title."""
        s = self.session
        url = s.card_url(body) if s.link_cards() else None
        if not url:
            return None
        self.set_status("Making a preview of the link over Tor ...")
        try:
            draft = await s.make_card(url)
        except SessionError as exc:
            self.notify(f"No link preview: {exc}", timeout=6)
            return None
        finally:
            self.set_status("")
        if not draft.title.strip():
            if draft.note:
                self.notify(draft.note, timeout=6)
            return None
        from .dialogs.base import CardDialog
        from .screens import card_text
        text = card_text(draft.card()).plain + ("\n\nWith the page's picture." if draft.picture else "")
        return draft if await self.push_screen_wait(CardDialog(text)) else None

    @work(group="post", exit_on_error=False)
    async def compose_post(self, g: GroupInfo, subject: str = "", quote: str = "",
                           references: list[str] | None = None) -> None:
        """The compose window for a post or reply in a group, then send it."""
        data = self.session.manifest_data(g.group_id)
        answered = self.messages.get(references[-1]) if references else None   # its language (#686)
        own_days = (not references and data.get("type") in ("open", "moderated", "closed", "channel")
                     and not data.get("local") and g.view != "chat"
                     and not (data.get("type") == "channel" and data.get("comments") != "open"))
        dlg = ComposeDialog(
            g, subject, quote, bool(references), signature=self.session.signature(), reply_to=answered,
            has_charter=bool(data.get("charter")), own_days=own_days,     # UN-RFC 0030 3.1
            comment_days_max=data.get("comment_days") if isinstance(data.get("comment_days"), int) else None,
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
        card = await self._link_card(body)               # UN-RFC 0059
        self.set_status("Sending (computing postage) ...")
        try:
            msgid = await self.session.post(g.group_id, subject, body, references, files=files,
                                            card=card,
                                            tags=dlg.tags or None,         # UN-RFC 0052
                                            languages=dlg.languages,       # and 5a
                                            comment_days=dlg.comment_days)  # UN-RFC 0030 3.1
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
