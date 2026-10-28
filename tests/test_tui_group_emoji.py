"""#10 (as unseenet-gui#67, UN-RFC 0056): the owner gives the group its own emoji from a
picture file - name, description, the warning before the first - and removes it."""
import io

import pytest

pytest.importorskip("textual")
PIL = pytest.importorskip("PIL.Image")

from textual.widgets import Input  # noqa: E402

from unseenet.client.session import Session  # noqa: E402
from unseenet.core.store import Store  # noqa: E402
from unseenet.core.vault import Vault  # noqa: E402
from unseenet.node.server import Node, NodeConfig  # noqa: E402
from unseenet_tui.app import UnseenetApp  # noqa: E402
from unseenet_tui.dialogs.base import ConfirmDialog, InputDialog  # noqa: E402

from tuihelp import app_ready, press, stack, wait_for  # noqa: E402

KDF = {"ops": 1, "mem": 8192 * 1024}

if not hasattr(Session, "set_group_emoji"):
    pytest.skip("needs the core's group emoji (unseenet#714)", allow_module_level=True)

from unseenet_tui.dialogs.groups import GroupEmojiDialog  # noqa: E402


async def _answer(pilot, app, value):
    assert await wait_for(pilot, lambda: isinstance(app.screen, InputDialog), 400), stack(app)
    await pilot.pause(0.1)
    app.screen.query_one("#value", Input).value = value
    await press(pilot, app, "#ok")


async def test_add_and_remove_an_emoji(tmp_path, monkeypatch):
    import unseenet.client.session as sess
    monkeypatch.setattr(sess, "DEFAULT_POW", 10)
    node = Node(Store(), NodeConfig(min_pow=8, forward_delay=(0, 0), emoji="accept"))
    server = await node.serve("127.0.0.1", 0)
    s = Session(Vault.open(tmp_path / "me.vault", "pw", **KDF), status=lambda *_: None)
    s.new_identity("me", "Me")
    s.add_node(f"127.0.0.1:{server.sockets[0].getsockname()[1]}")
    s.data["starter_groups"] = 99
    await s.ensure_inbox()
    gid = await s.create_group("dk.marsvin", "open")
    await s.close()
    picture = tmp_path / "marsvin.png"
    out = io.BytesIO()
    PIL.new("RGBA", (200, 200), (200, 120, 40, 255)).save(out, "PNG")
    picture.write_bytes(out.getvalue())
    monkeypatch.setenv("UNSEENET_PASSPHRASE", "pw")
    monkeypatch.setenv("UNSEENET_INSECURE_FAST_KDF", "1")
    app = UnseenetApp(tmp_path / "me.vault")
    async with app.run_test(size=(140, 40)) as pilot:
        assert await app_ready(pilot, app), stack(app)
        app.select_group(gid)
        assert await wait_for(pilot, lambda: app.current_group == gid, 400), stack(app)
        app.action_group_emoji()
        assert await wait_for(pilot, lambda: isinstance(app.screen, GroupEmojiDialog), 400), stack(app)
        await pilot.pause(0.1)
        await press(pilot, app, "#add")
        await _answer(pilot, app, str(picture))
        await _answer(pilot, app, "marsvin")
        await _answer(pilot, app, "Et marsvin")
        assert await wait_for(pilot, lambda: isinstance(app.screen, ConfirmDialog), 400), stack(app)
        await press(pilot, app, "#ok")                          # the warning, before the first
        assert await wait_for(pilot, lambda: isinstance(app.screen, GroupEmojiDialog)
                              and app.screen.names == ["marsvin"], 600), stack(app)
        assert app.session.group_emoji(gid)["marsvin"]["alt"] == "Et marsvin"
        await pilot.pause(0.1)
        await press(pilot, app, "#remove")
        assert await wait_for(pilot, lambda: isinstance(app.screen, GroupEmojiDialog)
                              and app.screen.names == [], 600), stack(app)
        assert app.session.group_emoji(gid) == {}
        await pilot.press("escape")
        await pilot.press("q")
    server.close()
