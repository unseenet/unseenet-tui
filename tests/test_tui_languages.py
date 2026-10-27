"""#57: the language of what you write (unseenet#671, UN-RFC 0052 5a) - suggested in the
compose window and why, what you choose sent; a post's language shown only where it
differs from its group's."""
from datetime import datetime, timezone

import pytest

pytest.importorskip("textual")

from unseenet.client.session import GroupInfo, Message, Session  # noqa: E402
from unseenet.core.vault import Vault  # noqa: E402
from unseenet_tui.app import UnseenetApp  # noqa: E402

from tuihelp import app_ready, laid_out, press, stack, wait_for  # noqa: E402

KDF = {"ops": 1, "mem": 8192 * 1024}
GID = "dk.snak@abcdabcdabcdabcd"


async def _app(tmp_path, monkeypatch):
    path = tmp_path / "me.vault"
    s = Session(Vault.open(path, "pw", **KDF))
    s.new_identity("andreas", "Andreas")
    s.add_node("127.0.0.1:9")
    s.vault.close()
    monkeypatch.setenv("UNSEENET_PASSPHRASE", "pw")
    monkeypatch.setenv("UNSEENET_INSECURE_FAST_KDF", "1")
    return UnseenetApp(path)


async def test_suggested_chosen_and_shown(tmp_path, monkeypatch):
    from unseenet_tui.dialogs.compose import ComposeDialog
    app = await _app(tmp_path, monkeypatch)
    got = []
    async with app.run_test(size=(140, 60)) as pilot:
        assert await app_ready(pilot, app), stack(app)
        s = app.session
        s.subscribe(GID)
        s.group_entry(GID)["manifest"] = {"name": "dk.snak", "type": "open", "languages": ["da"]}
        dlg = ComposeDialog(GroupInfo(GID, type="open"))
        app.push_screen(dlg, got.append)
        assert await wait_for(pilot, lambda: laid_out(app, "#lang")), stack(app)
        assert app.screen.query_one("#lang").value == "da"
        ids = [w.id for w in app.screen.query("Input, TextArea") if w.id in ("body", "lang")]
        assert ids == ["body", "lang"]                                  # under the text
        assert any("the group's" in str(lb.render()) for lb in app.screen.query("Label"))
        app.screen.query_one("#subject").value = "Hi"
        app.screen.query_one("#body").text = "In English\n"
        app.screen.query_one("#lang").value = "en"
        app.screen.action_send()
        assert await wait_for(pilot, lambda: bool(got)), stack(app)
        assert dlg.languages == ["en"]

        date = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
        da = Message(number=1, message_id="<a@x>", group=GID, subject="Hej", from_name="X",
                     author_key="ed25519:" + "a" * 52, date=date, body="x", languages=("da",))
        en = Message(number=2, message_id="<b@x>", group=GID, subject="Hi", from_name="X",
                     author_key="ed25519:" + "a" * 52, date=date, body="x", languages=("en",))
        assert not app.article(da)[0].endswith("·  da")                  # the group's: not said
        assert app.article(en)[0].endswith("·  en")                      # differs: said
        await pilot.press("q")


async def test_the_language_of_chat_lines_here(tmp_path, monkeypatch):
    """Write in ... here: remembered for the group, used for chat lines and comments."""
    app = await _app(tmp_path, monkeypatch)
    async with app.run_test(size=(140, 50)) as pilot:
        assert await app_ready(pilot, app), stack(app)
        s = app.session
        s.subscribe(GID)
        s.group_entry(GID)["manifest"] = {"name": "dk.snak", "type": "open", "languages": ["da"]}
        app.current_group = GID
        app.action_write_language()
        assert await wait_for(pilot, lambda: type(app.screen).__name__ == "InputDialog"), stack(app)
        assert await wait_for(pilot, lambda: laid_out(app, "Input")), stack(app)
        app.screen.query_one("Input").value = "en"
        await press(pilot, app, "#ok")
        assert await wait_for(pilot, lambda: s.suggest_languages(GID, kind="chat") == (["en"], "chosen")), stack(app)
        await pilot.press("q")


async def test_the_language_follows_the_text_until_you_choose(tmp_path, monkeypatch):
    """unseenet#686: at a pause the field takes the text's language - until you type in it."""
    from unseenet_tui.dialogs.compose import ComposeDialog
    app = await _app(tmp_path, monkeypatch)
    async with app.run_test(size=(140, 60)) as pilot:
        assert await app_ready(pilot, app), stack(app)
        s = app.session
        s.subscribe(GID)
        s.group_entry(GID)["manifest"] = {"name": "dk.snak", "type": "open", "languages": ["da"]}
        app.push_screen(ComposeDialog(GroupInfo(GID, type="open")))
        assert await wait_for(pilot, lambda: laid_out(app, "#lang")), stack(app)
        app.screen.query_one("#body").text = "I am a bit busy, and this is something we could pick up later if you would like"
        assert await wait_for(pilot, lambda: app.screen.query_one("#lang").value == "en", 100), stack(app)
        assert "from your text" in str(app.screen.query_one("#lang-why").render())
        app.screen.query_one("#lang").value = "da"                    # yours now
        await pilot.pause(0.1)
        app.screen.query_one("#body").text = "And this is more of the English text, which you would like"
        await pilot.pause(1.0)
        assert app.screen.query_one("#lang").value == "da"
        await pilot.press("escape")
        await pilot.press("q")
