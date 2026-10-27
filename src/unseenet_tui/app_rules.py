"""UnseenetApp's own rules (UN-RFC 0051, #615): the rules editor, "Why is this post here?",
watching and ignoring threads, and the rows of a group or the timeline as the rules have
them - only when there are rules; without them the lists are exactly as before.

A mixin of the terminal client's app, split by area (#20); its methods use the app's
widgets and session as self."""

from __future__ import annotations

from textual import work

from .dialogs.base import InfoDialog
from unseenet.client.threads import CONTACT, FOR_YOU, TIMELINE


class RulesMixin:
    def scored_rows(self, gid: str, rows: list) -> tuple[list, dict]:
        """The rows to show, as your rules have them, and {Message-ID: result}: hidden
        rows left out, the replies under a folded post left out, "mark read" done. Without
        rules (or for chats, For you and private posts) the rows as they are and {}."""
        s = self.session
        if not gid or not s.rule_set() or gid.startswith(CONTACT) \
                or gid == getattr(s, "my_inbox", None):
            return rows, {}
        rules = getattr(self, "_show_all", None) != gid        # Show all: a look (0051 3)
        if gid == FOR_YOU:                                     # only [foryou] rules (0051 5.5)
            got = [(m, 0, r) for m, r in s.scored_for_you(rules=rules)[:200]]
        elif gid == TIMELINE:
            got = [(m, 0, r) for m, r in s.scored_timeline(200, rules=rules)]
        else:
            got = s.scored_threads(gid, rules=rules)
        out, results, marked = [], {}, False
        folded = None                                  # (depth, Message-ID) of a folded post
        self._folded = {}                              # Message-ID -> replies folded under it
        for m, depth, res in got:
            if folded is not None:
                if depth > folded[0]:                  # under the folded post: not shown
                    self._folded[folded[1]] = self._folded.get(folded[1], 0) + 1
                    continue
                folded = None
            if not res.shown:
                continue
            if "mark read" in res.actions and not s.is_read(m):
                s.mark_read(m, save=False)
                marked = True
            results[m.message_id] = res
            out.append((m, depth))
            if "fold" in res.actions:
                folded = (depth, m.message_id)
        if marked:
            s.save()
        return out, results

    def action_show_all(self) -> None:
        """Show all here, for now: this view without your rules, or back to them (UN-RFC
        0051 section 3). Never saved; it ends when you leave the view."""
        gid = self.current_group
        if not gid:
            return
        self._show_all = None if getattr(self, "_show_all", None) == gid else gid
        self.refresh_view()
        self.notify("Showing all - your rules are paused here" if self._show_all
                    else "Your rules again", timeout=3)

    def action_rules(self) -> None:
        """Your rules (UN-RFC 0051): edit them, checked as you type, and turn them on or off."""
        self._edit_rules()

    @work(exclusive=True, group="rules")
    async def _edit_rules(self) -> None:
        from .rules_dialog import RulesDialog
        s = self.session
        got = await self.push_screen_wait(RulesDialog(s.feed_rules(), s.rules_on(), s.check_feed_rules))
        if not got:
            return
        try:
            rs = s.set_feed_rules(got["text"])
        except Exception as exc:  # noqa: BLE001 - too long: said, nothing kept
            self.notify(str(exc), severity="error", timeout=8)
            return
        s.set_rules_on(got["on"])
        used = f"{len(rs.rules)} rule(s)" + ("" if got["on"] else ", turned off")
        self.notify(f"Rules kept: {used}" + (f"; {len(rs.problems)} line(s) not used" if rs.problems else ""),
                    timeout=5)
        if self.current_group:
            self._render_threads()

    def action_why(self) -> None:
        """Why is this post here: which of your rules gave which points (UN-RFC 0051 10)."""
        m, s = self.current_message, self.session
        if m is None:
            self.notify("Select a post first", severity="warning")
            return
        if not s.rule_set():
            self.push_screen(InfoDialog("Why is this post here?",
                                        "You have no rules (or they are off): the feed is newest "
                                        "first and threads are as they are. Ctrl+P → Your rules."))
            return
        res = getattr(self, "_scores", {}).get(m.message_id)
        if res is None:
            res = __import__("unseenet.client.scoring", fromlist=["score"]).score(
                s.rule_set(), s.post_facts(m), s.scoring_context(),
                "feed" if self.current_group == TIMELINE else "groups")
        self.push_screen(InfoDialog("Why is this post here?", res.explain()))

    def _thread_root(self) -> str | None:
        m = self.current_message
        if m is None:
            self.notify("Select a post first", severity="warning")
            return None
        return m.references[0] if m.references else m.message_id

    def _mark_thread(self, mark: str, said: str) -> None:
        root = self._thread_root()
        if root is None:
            return
        self.session.set_thread_mark(root, mark)
        self.notify(said, timeout=4)
        self._render_threads()

    def action_watch_thread(self) -> None:
        """Watch the selected post's thread (kept in your state, UN-RFC 0044 4.5)."""
        self._mark_thread("watched", "Watching this thread")

    def action_ignore_thread(self) -> None:
        """Ignore the selected post's thread: your rules can hide it (hide if thread in ignored)."""
        self._mark_thread("ignored", "Ignoring this thread")

    def action_unmark_thread(self) -> None:
        """Neither watch nor ignore the selected post's thread."""
        self._mark_thread("", "Neither watched nor ignored")
