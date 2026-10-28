"""#45: in Edit group, your own set of reactions (UN-RFC 0048 section 4) is picked with the
emoji picker - the glyph goes in, once, at most 12 - and the field and its button are
only there while the own set is chosen."""
import pytest

pytest.importorskip("textual")

from textual.widgets import Select  # noqa: E402

from unseenet.client.session import Session  # noqa: E402
from unseenet.core import markup  # noqa: E402
from unseenet.core.vault import Vault  # noqa: E402
from unseenet_tui.app import UnseenetApp  # noqa: E402
from unseenet_tui.dialogs.groups import EditGroupDialog  # noqa: E402

from tuihelp import app_ready, press, stack, wait_for  # noqa: E402

KDF = {"ops": 1, "mem": 8192 * 1024}
DATA = {"v": 1, "name": "alt.snak", "type": "open", "owner": "ed25519:x", "serial": 1,
        "reactions": ["🎉"]}


async def _app(tmp_path, monkeypatch):
    path = tmp_path / "me.vault"
    s = Session(Vault.open(path, "pw", **KDF))
    s.new_identity("andreas", "Andreas")
    s.add_node("127.0.0.1:9")
    s.vault.close()
    monkeypatch.setenv("UNSEENET_PASSPHRASE", "pw")
    monkeypatch.setenv("UNSEENET_INSECURE_FAST_KDF", "1")
    return UnseenetApp(path)


def _code(glyph):
    return next(c for c, g in markup.emoji_table().items() if g == glyph)


async def test_pick_your_own_set_with_the_emoji_picker(tmp_path, monkeypatch):
    app = await _app(tmp_path, monkeypatch)
    got = []
    async with app.run_test(size=(140, 60)) as pilot:
        assert await app_ready(pilot, app), stack(app)
        app.push_screen(EditGroupDialog(dict(DATA)), got.append)
        assert await wait_for(pilot, lambda: type(app.screen).__name__ == "EditGroupDialog"), stack(app)
        await pilot.pause(0.2)
        edit = app.screen
        assert edit.query_one("#own-set").display                     # the own set is chosen
        await press(pilot, app, "#add-emoji")
        assert await wait_for(pilot, lambda: type(app.screen).__name__ == "EmojiPickerDialog"), stack(app)
        picker = app.screen
        assert "your set" in str(picker.query_one(".title").render())
        picker.dismiss(_code("❤️"))                                   # as a pick in the list does
        assert await wait_for(pilot, lambda: app.screen is edit), stack(app)
        box = edit.query_one("#reaction_set")
        assert await wait_for(pilot, lambda: box.value == "🎉 ❤️"), box.value   # the glyph, not :heart:
        edit.add_to_own_set(_code("🎉"))                              # once only
        assert box.value == "🎉 ❤️"
        box.value = "😀 😃 😄 😁 😆 😅 🤣 😂 🙂 🙃 😉 😊"
        edit.add_to_own_set(_code("❤️"))                              # at most 12
        assert len(box.value.split()) == 12
        box.value = "❤️ 👏"
        await press(pilot, app, "#ok")
        assert await wait_for(pilot, lambda: bool(got)), stack(app)
        assert got[0]["reactions"] == ["❤️", "👏"]
        await pilot.press("q")


async def test_the_picker_only_with_your_own_set(tmp_path, monkeypatch):
    app = await _app(tmp_path, monkeypatch)
    async with app.run_test(size=(140, 60)) as pilot:
        assert await app_ready(pilot, app), stack(app)
        app.push_screen(EditGroupDialog({**DATA, "reactions": None}))
        assert await wait_for(pilot, lambda: type(app.screen).__name__ == "EditGroupDialog"), stack(app)
        await pilot.pause(0.2)
        edit = app.screen
        assert not edit.query_one("#own-set").display                 # as the view has them
        edit.query_one("#reactions", Select).value = "custom"
        assert await wait_for(pilot, lambda: edit.query_one("#own-set").display), stack(app)
        await pilot.press("q")
