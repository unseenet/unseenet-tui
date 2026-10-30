"""Sharing in the terminal client (UN-RFC 0060, tui#110), as the graphical client does it.

Belongs here: the original a share refers to, framed under a share with a comment and
shown in place of a plain share ("↻ Mette shared"); its row in a list; fetching it through
the core without holding up the view; and Share, Share with a comment, Share into a
group, Send to, Undo share, Open the shared post, hiding shares and share notices - in
the palette (Ctrl+P), with b to share an entry in a feed.

Part of the app (see app.py).
"""

from __future__ import annotations

import asyncio

from rich.text import Text
from textual import work
from textual.markup import escape

from unseenet.client.session import Message, SessionError
from unseenet.client.threads import TIMELINE
from unseenet.nntp import NNTPError

from .dialogs.base import ConfirmDialog, InputDialog, KeyListDialog

SHARE_MARK = "↻"
SHARE_STATES = {
    "fetching": "Fetching the shared post …",
    "withdrawn": "Withdrawn by its author",
    "gone": "No longer in the network",
    "unavailable": "Not available right now",
}
EXCERPT_LINES = 4


def shared_by(m: Message) -> str:
    """Who shared it: "Mette", or in the timeline "Søren and Mette" (only those you
    follow, as the core names them) - never how many."""
    names = list(getattr(m, "shared_by", None) or []) or [m.from_name or "someone"]
    return (", ".join(names[:-1]) + " and " + names[-1]) if len(names) > 1 else names[0]


def shared_text(state: dict | None, label: str = "", share_date=None) -> Text:
    """The original a share refers to, framed as a card (UN-RFC 0060 section 5.1): who,
    where, its subject and first lines - made from what was fetched, never from the share;
    "edited after it was shared" when the version shown is newer than ``share_date``.
    Without it: why not, in a word."""
    if state is None or state.get("state") != "ok" or state.get("message") is None:
        what = SHARE_STATES.get((state or {}).get("state") or "fetching", SHARE_STATES["unavailable"])
        return Text(f"┌ {SHARE_MARK} {what}", style="dim")
    o = state["message"]
    out = Text(f"┌ {SHARE_MARK} ", style="green")
    out.append(o.from_name or "someone", style="bold")
    if label:
        out.append(f" · {label}", style="dim")
    if o.date:
        out.append(" · " + o.date.astimezone().strftime("%d %b %Y"), style="dim")
    if o.kind != "chat" and o.subject:
        out.append("\n│ ", style="green")
        out.append(o.subject, style="bold")
    lines = [ln for ln in (o.body or "").strip().split("\n") if ln.strip()][:EXCERPT_LINES]
    for ln in lines:
        out.append("\n│ ", style="green")
        out.append(ln[:200])
    stamp = o.edited or None
    if share_date is not None and stamp is not None and stamp > share_date:
        out.append("\n│ ", style="green")
        out.append("edited after it was shared", style="italic")
    out.append("\n└ ", style="green")
    out.append("Ctrl+P: Open the shared post", style="dim")
    return out


class SharesMixin:
    # --- the original, fetched without holding up the view (section 6.3) ---------------
    def _share_state(self, m: Message) -> dict | None:
        """What is known of the original ``m`` refers to; starts fetching it if nothing is."""
        of, group = m.share_of, m.share_group
        if not of or not group:
            return None
        st = self.session.share_target_state(of, group)
        if st is None:
            pending = self.__dict__.setdefault("_share_fetching", set())
            if (of, group) not in pending:
                pending.add((of, group))
                self._fetch_share(of, group)
        return st

    @work(group="shares", exit_on_error=False)
    async def _fetch_share(self, of: str, group: str) -> None:
        try:
            await self.session.fetch_share_target(of, group)
        except (SessionError, NNTPError, OSError, ConnectionError, asyncio.TimeoutError):
            pass
        finally:
            self.__dict__.setdefault("_share_fetching", set()).discard((of, group))
        self._render_threads()
        self._refresh_article()

    def _refresh_article(self) -> None:
        """Draw the selected post again, now that its original came (or did not)."""
        msg = self.current_message
        if msg is None:
            return
        gid = self.current_group
        feedish = bool(gid) and (gid == TIMELINE or self.view_of(gid) == "feed")
        head, body = self.article(msg, cut=feedish)
        for widget in self.query("#article-head"):
            widget.update(head)
        for widget in self.query("#article-body"):
            widget.update(body)

    def _share_label(self, group: str) -> str:
        try:
            return self.session.group_label(group)
        except (KeyError, ValueError):
            return group.split("@")[0]

    def share_card_text(self, m: Message) -> Text | None:
        """Under a share with a comment, or a share sent to you: the original as a card."""
        if not m.share_of or m.kind == "share":
            return None
        return shared_text(self._share_state(m), self._share_label(m.share_group), m.date)

    def plain_share_article(self, m: Message, cut: bool = False) -> tuple[str, Text]:
        """A plain share in the read view (section 5.2): "↻ Mette shared", then the original
        as its author wrote it - comments and reactions go to it, where it was posted."""
        st = self._share_state(m)
        line = f"[b]{SHARE_MARK} {escape(shared_by(m))} shared[/b]"
        if st is not None and st.get("state") == "ok" and st.get("message") is not None:
            o = st["message"]
            head, body = self.article(o, cut=cut)
            body.append("\n\n")
            body.append(f"In {self._share_label(o.group)} - Ctrl+P: Open the shared post, to "
                        "comment or react where it was posted", style="dim italic")
            return line + "\n" + head, body
        what = SHARE_STATES.get((st or {}).get("state") or "fetching", SHARE_STATES["unavailable"])
        return line, Text("\n" + what, style="dim")

    def share_row(self, m: Message) -> str | None:
        """A plain share's line in a list: "↻ Mette shared: Mit marsvin hoster"; None when
        it is not shown - its original gone, or shares hidden here (section 5.4)."""
        if m.kind != "share" or not m.share_of:
            return None
        if self.session.hides_shares(m.group):
            return ""
        st = self._share_state(m)
        if st is not None and st.get("state") in ("withdrawn", "gone"):
            return ""
        what = (st["message"].subject or (st["message"].body or "").strip().split("\n")[0][:60]) \
            if st and st.get("message") is not None else "a post"
        who = shared_by(m)
        return (f"{SHARE_MARK} shared by {who}: " if getattr(m, "shared_by", None)
                else f"{SHARE_MARK} {who} shared: ") + what

    # --- what can be done, and where (section 5.5) -----------------------------------
    def _share_subject(self) -> Message | None:
        m = self.current_message
        if m is None:
            self.notify("Select a post first", severity="warning")
            return None
        allowed = self.session.share_permission(m)
        if not allowed:
            self.notify(allowed.reason + (" - a request, not a protection: anyone who reads "
                                          "it can copy its words" if allowed.code == "asked_not" else ""),
                        severity="warning", timeout=8)
            return None
        return m

    def can_share(self) -> bool:
        """Whether b (Share) does something here: a feed, and a post that can be shared."""
        m = self.current_message
        gid = self.current_group or ""
        feedish = bool(gid) and (gid == TIMELINE or (gid in self.groups and self.view_of(gid) == "feed"))
        return feedish and m is not None and bool(self.session.share_permission(m))

    def _shared_notice(self, mid: str) -> None:
        self.notify("Shared. Shares are public: anyone can see that you shared it.", timeout=6)
        asyncio.get_running_loop().create_task(self.session.send_share_notice(mid))

    @work(group="dialog", exit_on_error=False)
    async def action_share(self) -> None:
        """b: share the selected post into your feed (UN-RFC 0060)."""
        m = self._share_subject()
        if m is None:
            return
        try:
            mid = await self.session.share(m)
        except SessionError as exc:
            if "Start your feed" in str(exc):
                self.notify("Start your feed first: Ctrl+P Start your feed", severity="warning", timeout=8)
            else:
                self.notify(f"Not shared: {exc}", severity="error", timeout=8)
            return
        self._shared_notice(mid)

    @work(group="dialog", exit_on_error=False)
    async def action_share_comment(self) -> None:
        """Share the selected post into your feed with words of your own."""
        m = self._share_subject()
        if m is None:
            return
        text = await self.push_screen_wait(InputDialog(
            "Share with a comment", "Your words, shown with the post as a card under them. "
            "Public, like any post.", "Why it is worth reading"))
        if not text:
            return
        try:
            mid = await self.session.share(m, text)
        except SessionError as exc:
            self.notify(f"Not shared: {exc}", severity="error", timeout=8)
            return
        self._shared_notice(mid)

    @work(group="dialog", exit_on_error=False)
    async def action_share_into(self) -> None:
        """Bring the selected post into another group, with a comment where it needs one
        - at most three groups besides your feed (section 7.2)."""
        s = self.session
        m = self._share_subject()
        if m is None:
            return
        places = [d for d in s.share_destinations(m) if not d["own_feed"] and d["allowed"]]
        if not places:
            self.notify("None of your groups takes shares, or you shared this into three already",
                        severity="warning", timeout=8)
            return
        gid = await self.push_screen_wait(KeyListDialog(
            "Share into a group", "At most three groups besides your feed. A discussion group "
            "needs a few words: why you bring it there.",
            [(d["group"], d["label"] + ("  (shared here)" if d["shared"] else "")
              + ("" if d["plain"] else "  - needs a comment")) for d in places], "Share"))
        if not gid:
            return
        place = next(d for d in places if d["group"] == gid)
        text = await self.push_screen_wait(InputDialog(
            "Share into " + place["label"], "Your words" + ("" if place["plain"] else
                                                          " - this group needs them") + ".",
            "Why you bring it here"))
        if not text and not place["plain"]:
            self.notify("Not shared: a discussion group needs a few words", severity="warning")
            return
        try:
            mid = await s.share(m, text or "", into=gid)
        except SessionError as exc:
            self.notify(f"Not shared: {exc}", severity="error", timeout=8)
            return
        self._shared_notice(mid)

    @work(group="dialog", exit_on_error=False)
    async def action_send_share(self) -> None:
        """Send the selected post to one person, privately (section 7.4)."""
        s = self.session
        m = self._share_subject()
        if m is None:
            return
        people = s.contacts()                                  # (key, name)
        if people:
            to = await self.push_screen_wait(KeyListDialog(
                "Send to…", "Only they can read it; nobody else is told.", people, "Send"))
        else:
            to = await self.push_screen_wait(InputDialog(
                "Send to…", "Their key. Only they can read it; nobody else is told.", "ed25519:..."))
        if not to:
            return
        text = await self.push_screen_wait(InputDialog(
            "A few words", "If you like - or OK without.", "Look at this"))
        try:
            await s.send_share(m, to, text or "")
        except SessionError as exc:
            self.notify(f"Not sent: {exc}", severity="error", timeout=8)
            return
        self.notify("Sent", timeout=4)

    @work(group="dialog", exit_on_error=False)
    async def action_unshare(self) -> None:
        """Undo your share: the selected one, or your shares of the selected post."""
        s, m = self.session, self.current_message
        if m is None:
            self.notify("Select your share, or the post you shared", severity="warning")
            return
        mine = m.author_key == s.identity().key_text and m.share_of
        target = m.share_of if mine else s.share_target(m)[0]
        where = [m.group] if mine else list(s.my_shares(target))
        if not where:
            self.notify("You have not shared this", severity="warning")
            return
        if not await self.push_screen_wait(ConfirmDialog(
                "Undo share", "Take back your share" + ("" if len(where) == 1 else "s") + " of it? "
                "Those who saw it may remember; it is withdrawn from the network.", "Undo share")):
            return
        try:
            done = await s.unshare(target, m.group if mine else None)
        except SessionError as exc:
            self.notify(str(exc), severity="error", timeout=8)
            return
        self.notify("Taken back" if done else "Nothing to take back", timeout=4)
        self.refresh_groups()

    @work(group="dialog", exit_on_error=False)
    async def action_open_shared(self) -> None:
        """Open the original a share refers to - or, from a share notice, the share."""
        s, m = self.session, self.current_message
        if m is None:
            return
        if s.is_share_notice(m):
            s.mark_read(m)
            await self._open_post(m.shared_in, m.shared_as)
        elif m.share_of and m.share_group:
            st = s.share_target_state(m.share_of, m.share_group)
            o = st.get("message") if st else None
            await self._open_post(m.share_group, o.message_id if o else m.share_of)
        else:
            self.notify("Select a share, or a share notice in For you", severity="warning")

    async def _open_post(self, gid: str, msgid: str) -> None:
        """A post in its group: followed first if you agree (as an address opens)."""
        s = self.session
        if gid not in s.data["subscribed"]:
            try:
                manifest = await s.fetch_manifest(gid)
            except (SessionError, OSError, ConnectionError, NNTPError) as exc:
                self.notify(f"Could not look: {exc}", severity="error", timeout=8)
                return
            if manifest is None:
                self.notify(f"{s.group_label(gid)} is not on your nodes", severity="warning", timeout=8)
                return
            if not await self.push_screen_wait(ConfirmDialog(
                    "Follow this group?", f"The post is in {s.group_label(gid)}. Follow it to read "
                    "it here?", "Follow")):
                return
            s.subscribe(gid)
            self.refresh_groups()
        self._open_at = msgid
        self.select_group(gid)

    def action_toggle_hide_shares(self) -> None:
        """Hide plain shares in this feed, or show them again (section 5.4)."""
        gid = self.current_group
        if not gid or gid not in self.groups:
            self.session.set_hide_shares(None, not self.session.hidden_shares()["all"])
            on = self.session.hidden_shares()["all"]
            self.notify("Shares hidden in feeds and the timeline" if on else "Shares show again")
        else:
            on = not self.session.hides_shares(gid)
            self.session.set_hide_shares(gid, on)
            self.notify(f"Shares hidden in {self.session.group_label(gid)}" if on else "Shares show here again")
        self._render_threads()

    def action_toggle_hide_all_shares(self) -> None:
        """Hide plain shares everywhere, or show them again."""
        on = not self.session.hidden_shares()["all"]
        self.session.set_hide_shares(None, on)
        self.notify("Shares hidden in feeds and the timeline" if on else "Shares show again")
        self._render_threads()

    def action_toggle_share_notices(self) -> None:
        """Be told when your posts are shared, or not (section 8)."""
        on = not self.session.share_notices_on()
        self.session.set_share_notices(on)
        self.notify("When someone shares your post, you are told in For you" if on
                    else "You are no longer told when your posts are shared")

    def share_commands(self) -> list[tuple]:
        """The palette's sharing commands (Ctrl+P), as (name, help, action)."""
        s = self.session
        return [
            ("Share", "The selected post into your feed - public  [b in a feed]", self.action_share),
            ("Share with a comment", "Your words with the selected post as a card under them",
             self.action_share_comment),
            ("Share into a group", "Bring the selected post to another group - at most three "
             "besides your feed", self.action_share_into),
            ("Send to", "The selected post to one person, privately", self.action_send_share),
            ("Undo share", "Take back your share of the selected post", self.action_unshare),
            ("Open the shared post", "The original a share refers to - or, from a share "
             "notice in For you, the share", self.action_open_shared),
            ("Hide shares here" if not (self.current_group and s.hides_shares(self.current_group))
             else "Show shares here", "Plain shares in this feed - posts with words of their own "
             "still show", self.action_toggle_hide_shares),
            ("Show shares in feeds and the timeline" if s.hidden_shares()["all"]
             else "Hide shares in feeds and the timeline", "Plain shares everywhere",
             self.action_toggle_hide_all_shares),
            ("Stop showing share notices" if s.share_notices_on() else "Show share notices",
             "Being told in For you when someone shares your post (UN-RFC 0060)",
             self.action_toggle_share_notices),
        ]
