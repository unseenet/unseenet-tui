"""UnseenetApp's spam reports (UN-RFC 0045, unseenet#122, #11): a post hidden by a report
shown as hidden - who hid it and why - with Show anyway and Stop following in the
palette; the issuers you follow; and being an issuer: starting your reports group,
publishing a report of the selected post, and publishing one someone sent you.

Each reader decides, and nothing is hidden silently: the core marks a hidden post
(``Message.reported``) and the list, the reader and the chat say so in its place.

A mixin of the terminal client's app, split by area (#20); its methods use the app's
widgets and session as self."""

from __future__ import annotations

from textual import work

from unseenet.client.session import SessionError

from .dialogs.reports import IssuersDialog, PublishReportDialog, StartIssuerDialog

REASON_TEXT = {"spam": "spam", "flood": "flooding", "phishing": "phishing", "malware": "malware",
               "illegal": "illegal", "other": "other"}
HIDDEN_MARK = "⊘"


def reported_text(m) -> str:
    """"Hidden by Spamjægeren: spam" for a post hidden by a report, "" for others."""
    r = getattr(m, "reported", None)
    if not r:
        return ""
    return (f"Hidden by {r['label']}: {REASON_TEXT.get(r['reason'], r['reason'])}"
            + (" (its author)" if r["kind"] == "key" else "") + (" · automatic" if r["automatic"] else ""))


class ReportsMixin:
    # --- a hidden post ---
    def action_show_reported(self) -> None:
        """Show the selected post anyway, though a report hides it (on this device)."""
        m = self.current_message
        if m is None or not getattr(m, "reported", None):
            self.notify("Select a post hidden by a report first", severity="warning")
            return
        self.session.show_reported(m.message_id)
        self.refresh_view()

    def action_stop_following_issuer(self) -> None:
        """Stop following the issuer whose report hides the selected post."""
        m = self.current_message
        r = getattr(m, "reported", None) if m else None
        if not r:
            self.notify("Select a post hidden by a report first", severity="warning")
            return
        self.session.unfollow_issuer(r["issuer"])
        self.notify(f"{r['label']}'s reports no longer hide anything for you.")
        self.refresh_view()

    def refresh_view(self) -> None:
        """Draw the current group again, after what is hidden changed."""
        if self.current_group:
            self._render_threads()

    # --- the issuers you follow ---
    def action_report_issuers(self) -> None:
        """The spam report issuers that hide posts for you: turn them on or off, add one."""
        self._report_issuers()

    @work(exclusive=True, group="dialog", exit_on_error=False)
    async def _report_issuers(self) -> None:
        s = self.session
        got = await self.push_screen_wait(IssuersDialog(s.report_issuers(), s.group_label))
        if not got:
            return
        changes, key, label = got
        try:
            for k, on in changes.items():
                s.follow_issuer(k) if on else s.unfollow_issuer(k)
            if key:
                s.follow_issuer(key, label)
        except SessionError as exc:
            self.notify(str(exc), severity="error")
            return
        if changes or key:
            self.set_status("Reading the issuers' reports ...")
            try:
                await s.refresh_reports()
            except (SessionError, OSError, ConnectionError):
                pass                                    # read with the next refresh
            self.set_status("")
            self.refresh_view()
            self.notify("Your report issuers are kept.")

    # --- being an issuer ---
    def action_issuing(self) -> None:
        """Become a spam report issuer, or publish a report someone sent you."""
        self._issuing()

    @work(exclusive=True, group="dialog", exit_on_error=False)
    async def _issuing(self) -> None:
        s = self.session
        if not s.is_issuer():
            got = await self.push_screen_wait(StartIssuerDialog())
            if not got:
                return
            try:
                await s.start_reports(*got)
            except (SessionError, OSError, ConnectionError) as exc:
                self.notify(f"Not started: {exc}", severity="error", timeout=8)
                return
            self.notify("Your reports group is started. Publish a report from the palette on a "
                        "post, or on a report someone sent you.", timeout=8)
            return
        self.notify("You are an issuer: select a post (or a report sent to you in Private posts) "
                    "and choose Publish a spam report in the palette.", timeout=8)

    def action_publish_report(self) -> None:
        """As an issuer: publish a report of the selected post, or of the post a report
        someone sent you names."""
        self._publish_report()

    @work(exclusive=True, group="dialog", exit_on_error=False)
    async def _publish_report(self) -> None:
        s, m = self.session, self.current_message
        if not s.is_issuer():
            self.notify("Become a spam report issuer first (in the palette)", severity="warning")
            return
        if m is None:
            self.notify("Select a post first", severity="warning")
            return
        found = s.received_report(m) if m.private else None
        if found:
            mid, gid, author, reason, what = *found, m.subject
        elif not m.private and m.author_key != s.identity().key_text:
            mid, gid, author, reason, what = m.message_id, m.group, m.author_key, "spam", m.subject
        else:
            self.notify("Select someone else's post, or a report sent to you", severity="warning")
            return
        got = await self.push_screen_wait(PublishReportDialog(what or mid, bool(author), reason))
        if not got:
            return
        reason, with_key, days, note = got
        from datetime import datetime, timedelta, timezone
        until = None if days >= 365 else datetime.now(timezone.utc) + timedelta(days=days)
        try:
            await s.publish_report([(mid, gid, reason)],
                                   [(author, reason, until)] if with_key else [], note)
        except (SessionError, OSError, ConnectionError) as exc:
            self.notify(f"Not published: {exc}", severity="error", timeout=8)
            return
        self.notify("Report published.")
