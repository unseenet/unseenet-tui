"""UnseenetApp's push from the nodes, and what follows it in the class (UN-RFC 0008 2).

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


# Commands that need the network: while Unseenet is not connected yet, the palette says
# "Waiting for the network" for them and they wait instead of failing. What works
# offline - your rules, nodes, contacts, settings, writing (posts wait in the queue) -
# is not here.
NEEDS_NETWORK = frozenset({
    "Update Unseenet", "Follow", "Start your feed", "Start your site", "Publish a folder as this site",
    "Share your profile", "Take back your shared profile", "Open address", "Browse groups",
    "Create group", "Invite to closed group", "Join group or open an address", "Profile of this author (u)",
    "Your profile", "Names", "Copy to another device", "Get identity from another device", "Edit group",
    "Ask for a name under someone's name", "Give a name under yours", "Name requests", "Names you gave",
    "Name your posts show", "Report post", "Open reported post", "Moderation queue", "Make moderator",
    "Make poster", "Moderators and posters", "Members of closed group", "Ban from group",
    "Block in hierarchy", "Banned from group", "Withdraw (delete) post", "Pin or unpin post",
    "Remove group from the network", "Spam reports", "Become a spam report issuer",
    "Your spam reports", "Publish a spam report"})


class PushMixin:

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
            waiting = not getattr(self, "_connected", False)
            yield SystemCommand("Update Unseenet", ("Waiting for the network · " if waiting else "") +
                                "Get the newest release onto this stick or folder, over Tor - data/ is kept",
                                self._waiting_for_network if waiting else self.action_update)
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
            ("Start your feed", "Your own feed, public, where anyone can comment - or open it",
             self.action_start_feed),
            (("Pictures in private posts: on" if self.session and self.session.inbox_files()
              else "Pictures in private posts: off"),
             "Whether people can send you pictures in private posts and chats - encrypted, "
             "only you can see them", self.action_toggle_inbox_files),
            ("Start your site", "Your own site of pages, public - or open it; n writes a page",
             self.action_start_site),
            ("Previous version of this page", "Publish the selected page's earlier version "
             "again (a site you write in)", self.action_page_previous),
            ("Remove this page", "Remove the selected page from the site you write in",
             self.action_page_remove),
            ("Your rules", "Your own rules for your feed and groups: what comes first, what is "
             "hidden, folded or highlighted - only you see them", self.action_rules),
            ("Why is this post here?", "Which of your rules gave the selected post its place",
             self.action_why),
            ("Watch this thread", "Keep an eye on the selected post's thread", self.action_watch_thread),
            ("Ignore this thread", "Your rules can hide it: hide if thread in ignored",
             self.action_ignore_thread),
            ("Neither watch nor ignore this thread", "Undo watching or ignoring it",
             self.action_unmark_thread),
            ("Insert emoji", "Pick an emoji by name or category - its :shortcode: goes where "
             "you write; typing :ta offers them too", self.action_insert_emoji),
            ("Publish a folder as this site", "A folder of Markdown or HTML files as the pages of "
             "the site you write in - only what changed is sent", self.action_publish_folder),
            ("Share your profile", "With one person or this closed group only - not public",
             self.action_share_profile),
            ("Take back your shared profile", "From a person or this closed group",
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
            ("Show your key", "Your key, to share for private messages and invitations  [k]",
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
            ("Export your data", "Your posts, comments, chat lines and pages as signed, their files, "
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
            ("Show anyway", "The selected post is hidden by a spam report: show it here",
             self.action_show_reported),
            ("Stop following this report issuer", "Its reports hide nothing for you any more "
             "(also one your hierarchy names)", self.action_stop_following_issuer),
            ("Spam reports", "The issuers whose reports hide posts for you: on, off, add one "
             "(UN-RFC 0045)", self.action_report_issuers),
            ("Your spam reports" if self.session.is_issuer() else "Become a spam report issuer",
             "Publish reports of spam that others may choose to follow", self.action_issuing),
            ("Publish a spam report", "Issuers: report the selected post, or the one a report "
             "sent to you names - publicly, signed by you", self.action_publish_report),
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
            if title in NEEDS_NETWORK and not getattr(self, "_connected", False):
                # not connected yet: said, not run - ready by itself once connected
                help_text = "Waiting for the network · " + help_text
                callback = self._waiting_for_network
            yield SystemCommand(title, help_text, callback)

    def _waiting_for_network(self) -> None:
        self.notify("Waiting for the network: this is ready as soon as Unseenet is connected.",
                    severity="warning", timeout=5)

    def _owner_label(self, data: dict) -> str:
        key = data.get("owner", "")
        if key == self.session.identity().key_text:
            return "you"
        name = self.session.contact_name(key)
        return f"{name} √  ({key[:28]}…)" if name else f"{key[:36]}…"

    @work(group="dialog", exit_on_error=False)
    async def action_group_info(self, first_time: bool = False) -> None:
        """i: the group's info (first_time: as it is first opened, with its rules)."""
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
        """The owner's settings of the group shown."""
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
