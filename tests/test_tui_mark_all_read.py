"""Mark all read (unseenet#625): the palette's catch up marks every post in the group."""
import pytest

pytest.importorskip("textual")

from unseenet.client.session import Session  # noqa: E402
from unseenet_tui.app import UnseenetApp  # noqa: E402
from unseenet.core.store import Store  # noqa: E402
from unseenet.core.vault import Vault  # noqa: E402
from unseenet.node.server import Node, NodeConfig  # noqa: E402

from tuihelp import wait_for  # noqa: E402

KDF = {"ops": 1, "mem": 8192 * 1024}


async def test_tui_mark_all_read(tmp_path, monkeypatch):
    node = Node(Store(), NodeConfig(min_pow=8, forward_delay=(0, 0)))
    server = await node.serve("127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    other = Session(Vault.open(tmp_path / "o.vault", "pw", **KDF), status=lambda *_: None)
    other.new_identity("o", "O")
    other.add_node(f"127.0.0.1:{port}")
    gid = await other.create_group("dk.mange", "open", min_pow=10)
    for i in range(3):
        await other.post(gid, f"Emne {i}", "Tekst\n")
    await other.close()
    vault_path = tmp_path / "me.vault"
    s = Session(Vault.open(vault_path, "pw", **KDF), status=lambda *_: None)
    s.new_identity("me", "Me")
    s.add_node(f"127.0.0.1:{port}")
    s.subscribe(gid)
    await s.close()

    monkeypatch.setenv("UNSEENET_PASSPHRASE", "pw")
    monkeypatch.setenv("UNSEENET_INSECURE_FAST_KDF", "1")
    app = UnseenetApp(vault_path)
    async with app.run_test(size=(140, 40)) as pilot:
        assert await wait_for(pilot, lambda: app.session is not None
                              and app.session.unread_count(gid) == 3, 400)
        app.current_group = gid
        app.action_mark_group_read()
        await pilot.pause()
        assert app.session.unread_count(gid) == 0
    server.close()
