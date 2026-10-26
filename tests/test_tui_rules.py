"""#615: your own rules in the terminal client (UN-RFC 0051) - the editor checked as you
type, a group's rows as the rules have them (and as before without), Why?, threads."""
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("textual")

from unseenet.client.session import Message, Session  # noqa: E402
from unseenet.core.vault import Vault  # noqa: E402
from unseenet_tui.app import UnseenetApp  # noqa: E402
from unseenet_tui import rules_dialog as rd  # noqa: E402

from tuihelp import app_ready, press, stack, wait_for  # noqa: E402

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
        box = app.screen.query_one("#rules")
        assert "[feed]" in box.text and "suggestion" in str(app.screen.query_one(".hint").render())
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
