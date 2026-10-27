"""#6: emoji in the terminal client - :ta completion and the palette's picker, on the
core's find_emoji, inserting the :shortcode:."""
import pytest

pytest.importorskip("textual")

from unseenet.client.session import Session  # noqa: E402
from unseenet.core.vault import Vault  # noqa: E402
from unseenet_tui import emoji as em  # noqa: E402
from unseenet_tui.app import UnseenetApp  # noqa: E402

from tuihelp import app_ready, laid_out, stack, wait_for  # noqa: E402

KDF = {"ops": 1, "mem": 8192 * 1024}


def test_what_is_typed():
    assert em.typed_query("hello :ta") == ":ta" and em.typed_query(":tad") == ":tad"
    assert em.typed_query("hello :t") is None and em.typed_query("12:30") is None
    assert em.typed_query("done :tada: ") is None
    codes = em.suggestions("party :tad")
    assert codes and codes[0] == "tada" and len(codes) <= em.MAX
    assert "▸🎉 :tada:" in em.suggestion_text(["tada"], 0) and em.suggestion_text([], 0) == ""


async def _app(tmp_path, monkeypatch):
    path = tmp_path / "me.vault"
    s = Session(Vault.open(path, "pw", **KDF))
    s.new_identity("andreas", "Andreas")
    s.add_node("127.0.0.1:9")
    s.vault.close()
    monkeypatch.setenv("UNSEENET_PASSPHRASE", "pw")
    monkeypatch.setenv("UNSEENET_INSECURE_FAST_KDF", "1")
    return UnseenetApp(path)


async def test_completion_in_the_chat_box(tmp_path, monkeypatch):
    app = await _app(tmp_path, monkeypatch)
    async with app.run_test(size=(140, 45)) as pilot:
        assert await app_ready(pilot, app), stack(app)
        box = app.query_one("#chat-input")
        box.display = True
        box.parent.display = True
        sent = []
        orig = type(box).Submitted.__init__

        def counting(self, b, value):
            sent.append(value)
            orig(self, b, value)
        monkeypatch.setattr(type(box).Submitted, "__init__", counting)
        box.focus()
        await pilot.press("p", "a", "r", "t", "y", "space", "colon", "t", "a", "d")
        await pilot.pause(0.1)
        assert box.emoji_codes and box.emoji_codes[0] == "tada"
        assert app.query_one("#chat-emoji").display
        await pilot.press("enter")                        # takes it, does not send
        await pilot.pause(0.1)
        assert box.text == "party :tada: " and not sent and not box.emoji_codes
        await pilot.press("colon", "h", "e", "a")
        await pilot.pause(0.1)
        assert box.emoji_codes
        await pilot.press("escape")
        await pilot.pause(0.1)
        assert not box.emoji_codes and box.text.endswith(":hea")
        await pilot.press("q")


async def test_the_picker_inserts_where_you_write(tmp_path, monkeypatch):
    app = await _app(tmp_path, monkeypatch)
    async with app.run_test(size=(140, 45)) as pilot:
        assert await app_ready(pilot, app), stack(app)
        box = app.query_one("#chat-input")
        app.action_insert_emoji()
        assert await wait_for(pilot, lambda: type(app.screen).__name__ == "EmojiPickerDialog"), stack(app)
        assert await wait_for(pilot, lambda: laid_out(app, "#emoji-search")), stack(app)
        app.screen.query_one("#emoji-search").value = "tada"
        await pilot.pause(0.1)
        await pilot.press("enter")
        assert await wait_for(pilot, lambda: ":tada:" in box.text), stack(app)
        await pilot.press("q")
