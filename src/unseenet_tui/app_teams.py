"""UnseenetApp's team inboxes (UN-RFC 0047, unseenet#341, #13): accepting the post that
adds you as a reader, replying on a team inbox's behalf, forgetting a team key you were
removed from, and for an inbox's owner who reads it.

The core keeps the team keys and checks everything; this shows it.

A mixin of the terminal client's app, split by area (#20); its methods use the app's
widgets and session as self."""

from __future__ import annotations

from textual import work

from unseenet.client.session import SessionError

from .dialogs.teams import TeamReadersDialog


class TeamsMixin:
    def team_of(self, gid: str | None) -> dict | None:
        """``{"group", "name", "reader"}`` when ``gid`` is a team inbox you read."""
        return next((t for t in self.session.team_inboxes() if t["group"] == gid), None) if gid else None

    def action_accept_team(self) -> None:
        """Read the team inbox the selected private post adds you to."""
        self._accept_team()

    @work(exclusive=True, group="dialog", exit_on_error=False)
    async def _accept_team(self) -> None:
        s, m = self.session, self.current_message
        if m is None or not s.team_invitation(m):
            self.notify("Select the post that adds you as a reader first", severity="warning")
            return
        try:
            gid = await s.accept_team_invitation(m)
        except (SessionError, OSError, ConnectionError) as exc:
            self.notify(f"Not accepted: {exc}", severity="error", timeout=8)
            return
        await self._render_groups()
        self.select_group(gid)
        self.notify(f"You read {s.team_name(gid)} now. r answers as the team.", timeout=8)

    def action_forget_team(self) -> None:
        """Remove your team key for the current team inbox from the vault."""
        t = self.team_of(self.current_group)
        if not t:
            self.notify("Open a team inbox you read first", severity="warning")
            return
        self.session.forget_team_key(t["group"])
        self.notify(f"Your team key for {t['name']} is gone.")
        self.run_worker(self._render_groups())

    def action_team_readers(self) -> None:
        """Owner of an inbox: who reads it with a team key; add or remove a reader."""
        self._team_readers()

    @work(exclusive=True, group="dialog", exit_on_error=False)
    async def _team_readers(self) -> None:
        s, gid = self.session, self.current_group
        try:
            data = await s._own_inbox(gid) if gid else None
        except (SessionError, OSError, ConnectionError) as exc:
            self.notify(str(exc), severity="warning", timeout=8)
            return
        if not data or gid == s.my_inbox:
            self.notify("Open an inbox you own first (not your personal one)", severity="warning")
            return
        got = await self.push_screen_wait(TeamReadersDialog(list(data.get("readers", [])),
                                                            data.get("team_name", "")))
        if not got:
            return
        remove, add, name = got
        try:
            for k in remove:
                await s.remove_team_reader(gid, k)
            if add:
                key = await s.add_team_reader(gid, add, name)
                self.notify(f"A team key is in their inbox, to accept ({key[:24]}…).", timeout=8)
            elif name != data.get("team_name", ""):
                await s.update_group(gid, team_name=name or None)
        except (SessionError, OSError, ConnectionError, ValueError) as exc:
            self.notify(f"Not changed: {exc}", severity="error", timeout=8)
            return
        if remove:
            self.notify(f"Removed {len(remove)} reader(s).")
