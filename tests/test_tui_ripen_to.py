"""Level 4 as a choice in the terminal client (#93, unseenet#740, as unseenet-gui#241):
"Ripen to level 4" in the palette says what it costs and gives, then the key is ripened
to 4 on this device; "Ripen to level 3" takes it back."""

import pytest

pytest.importorskip("textual")

from unseenet.client.session import Session  # noqa: E402
from unseenet_tui.app import ConfirmDialog, UnseenetApp  # noqa: E402
from unseenet_tui.app_feeds import rough  # noqa: E402
from unseenet.core.store import Store  # noqa: E402
from unseenet.core.vault import Vault  # noqa: E402
from unseenet.node.server import Node, NodeConfig  # noqa: E402

from tuihelp import press, stack, wait_for  # noqa: E402

KDF = {"ops": 1, "mem": 8192 * 1024}


def test_rough():
    assert rough(30) == "about a minute"
    assert rough(1200) == "about 20 minutes"
    assert rough(3 * 3600) == "about 3 hours"
    assert rough(12 * 86400) == "about 12 days"


async def test_tui_ripen_to_4_and_back(tmp_path, monkeypatch):
    node = Node(Store(), NodeConfig(min_pow=8, forward_delay=(0, 0)))
    server = await node.serve("127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    vault_path = tmp_path / "me.vault"
    s = Session(Vault.open(vault_path, "pw", **KDF), status=lambda *_: None)
    s.new_identity("me", "Me")
    s.add_node(f"127.0.0.1:{port}")
    s.save()
    await s.close()

    monkeypatch.setenv("UNSEENET_PASSPHRASE", "pw")
    monkeypatch.setenv("UNSEENET_INSECURE_FAST_KDF", "1")
    app = UnseenetApp(vault_path)
    async with app.run_test(size=(140, 40)) as pilot:
        assert await wait_for(pilot, lambda: app.runtime is not None, 300)
        assert app._ripen_target() == 3
        app.action_ripen_to()
        assert await wait_for(pilot, lambda: isinstance(app.screen, ConfirmDialog)), stack(app)
        assert "a quarter of the work" in str(app.screen.text)
        await press(pilot, app, "#ok")
        assert await wait_for(pilot, lambda: app.session.ripening_target() == 4, 200), stack(app)
        assert app._ripen_target() == 4

        app.action_ripen_to()                                    # and back to 3
        assert await wait_for(pilot, lambda: isinstance(app.screen, ConfirmDialog)), stack(app)
        assert "stops at level 3" in str(app.screen.text)
        await press(pilot, app, "#ok")
        assert await wait_for(pilot, lambda: app.session.ripening_target() == 3, 200), stack(app)
    server.close()
