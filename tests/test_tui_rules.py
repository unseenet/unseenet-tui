"""#615: your own rules in the terminal client (UN-RFC 0051) - the editor checked as you
type, a group's rows as the rules have them (and as before without), Why?, threads."""
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("textual")

from unseenet.client.session import Message, Session  # noqa: E402
from unseenet.core.vault import Vault  # noqa: E402
from unseenet_tui.app import UnseenetApp  # noqa: E402
from unseenet_tui import rules_dialog as rd  # noqa: E402

from tuihelp import app_ready, laid_out, press, stack, wait_for  # noqa: E402

KDF = {"ops": 1, "mem": 8192 * 1024}
GID = "dk.snak@abcdabcdabcdabcd"
T0 = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)


def msg(n, mid, subject, refs=(), minutes=0):
    return Message(number=n, message_id=mid, group=GID, subject=subject, from_name="X",
                   author_key="ed25519:" + "a" * 52, date=T0 + timedelta(minutes=minutes),
                   references=list(refs), body=subject)


async def _app(tmp_path, monkeypatch):
    path = tmp_path / "me.vault"
    s = Session(Vault.open(path, "pw", **KDF))
    s.new_identity("andreas", "Andreas")
    s.add_node("127.0.0.1:9")
    s.vault.close()
    monkeypatch.setenv("UNSEENET_PASSPHRASE", "pw")
    monkeypatch.setenv("UNSEENET_INSECURE_FAST_KDF", "1")
    return UnseenetApp(path)


def test_the_suggestion_is_understood():
    from unseenet.client import scoring
    rs = scoring.parse(rd.EXAMPLE)
    assert not rs.problems and len(rs.rules) >= 5


async def test_the_editor(tmp_path, monkeypatch):
    app = await _app(tmp_path, monkeypatch)
    async with app.run_test(size=(140, 50)) as pilot:
        assert await app_ready(pilot, app), stack(app)
        app.action_rules()
        assert await wait_for(pilot, lambda: type(app.screen).__name__ == "RulesDialog"), stack(app)
        assert await wait_for(pilot, lambda: laid_out(app, "#rules")), stack(app)
        box = app.screen.query_one("#rules")
        assert "[feed]" in box.text and "suggestion" in str(app.screen.query_one(".hint").render())
        await press(pilot, app, "#help")                               # the rules in short
        assert await wait_for(pilot, lambda: type(app.screen).__name__ == "InfoDialog"), stack(app)
        from unseenet.client import scoring
        assert app.screen.text == scoring.HELP and "IN SHORT" in app.screen.text
        app.pop_screen()
        await pilot.pause(0.1)
        box.load_text("+10 if autor in following")
        await pilot.pause(0.2)
        assert "did you mean 'author'" in str(app.screen.query_one("#rules-check").render())
        box.load_text("+10 if author in following  # friends")
        await pilot.pause(0.2)
        await press(pilot, app, "#ok")
        assert await wait_for(pilot, lambda: app.session.feed_rules().startswith("+10")), stack(app)
        await pilot.press("q")


async def test_a_group_as_the_rules_have_it(tmp_path, monkeypatch):
    app = await _app(tmp_path, monkeypatch)
    async with app.run_test(size=(140, 50)) as pilot:
        assert await app_ready(pilot, app), stack(app)
        s = app.session
        s.subscribe(GID)
        s.cache[GID] = {m.message_id: m for m in [
            msg(1, "<k@x>", "kage", minutes=1), msg(2, "<p@x>", "politik", minutes=2),
            msg(3, "<k2@x>", "re: kage", refs=["<k@x>"], minutes=3),
            msg(4, "<f@x>", "langt", minutes=4), msg(5, "<f2@x>", "re: langt", refs=["<f@x>"], minutes=5)]}
        app.current_group = GID
        app._render_threads()
        table = app.query_one("#threads")
        plain = [r.value for r in table.rows]
        assert len(plain) == 5 and "+0" not in str(table.get_row("<k@x>")[1])
        s.set_feed_rules('[groups]\n-50 if subject matches "politik"\nhide if score < 0\n'
                         'fold if subject matches "langt"')
        app._render_threads()
        keys = [r.value for r in table.rows]
        assert "<p@x>" not in keys and "<f2@x>" not in keys and "<k2@x>" in keys
        assert "▸ 1 folded" in str(table.get_row("<f@x>")[1])
        assert s.seen_to(GID) == "2026-10-26T12:04:00Z"           # shown: seen (not the folded reply)
        app.current_message = s.cache[GID]["<k@x>"]
        app.action_why()
        assert await wait_for(pilot, lambda: type(app.screen).__name__ == "InfoDialog"), stack(app)
        app.pop_screen()
        app.action_watch_thread()
        assert s.thread_mark("<k@x>") == "watched"
        s.set_rules_on(False)
        app._render_threads()
        assert [r.value for r in table.rows] == plain                 # off: as before
        await pilot.press("q")


async def test_show_all_is_a_look(tmp_path, monkeypatch):
    """#36, UN-RFC 0051 section 3: Show all shows the group without your rules, says so,
    saves nothing, and ends when you leave the group."""
    app = await _app(tmp_path, monkeypatch)
    async with app.run_test(size=(140, 50)) as pilot:
        assert await app_ready(pilot, app), stack(app)
        s = app.session
        s.subscribe(GID)
        s.cache[GID] = {1: msg(1, "<k@x>", "kage"), 2: msg(2, "<p@x>", "politik", minutes=1)}
        s.set_feed_rules('[groups]\nhide if subject matches "politik"\n')
        app.current_group = GID
        app._render_threads()
        assert "<p@x>" not in app.messages                                 # the rule hides it
        app.action_show_all()
        await pilot.pause(0.1)
        assert "<p@x>" in app.messages                                     # shown, for now
        assert "your rules are paused" in str(app.query_one("#threads-title").render())
        assert 'hide if subject matches "politik"' in s.feed_rules()       # nothing changed
        app.select_group("for-you:")
        assert await wait_for(pilot, lambda: app.current_group == "for-you:"), stack(app)
        app.select_group(GID)
        assert await wait_for(pilot, lambda: app.current_group == GID and "<k@x>" in app.messages), stack(app)
        assert "<p@x>" not in app.messages                                 # ended on leaving
        await pilot.press("q")
