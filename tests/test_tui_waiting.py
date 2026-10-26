"""While Unseenet is not connected yet, the palette says what waits for the network, and
such a command waits instead of failing; what works offline (your rules) is as always."""
import pytest

pytest.importorskip("textual")

from unseenet.client import events as ev  # noqa: E402
from unseenet.client.session import Session  # noqa: E402
from unseenet.core.vault import Vault  # noqa: E402
from unseenet_tui.app import UnseenetApp  # noqa: E402

from tuihelp import app_ready, stack  # noqa: E402

KDF = {"ops": 1, "mem": 8192 * 1024}


async def test_the_palette_while_not_connected(tmp_path, monkeypatch):
    path = tmp_path / "me.vault"
    s = Session(Vault.open(path, "pw", **KDF))
    s.new_identity("andreas", "Andreas")
    s.add_node("127.0.0.1:9")
    s.vault.close()
    monkeypatch.setenv("UNSEENET_PASSPHRASE", "pw")
    monkeypatch.setenv("UNSEENET_INSECURE_FAST_KDF", "1")
    app = UnseenetApp(path)
    async with app.run_test(size=(140, 45)) as pilot:
        assert await app_ready(pilot, app), stack(app)
        app._connected = False
        cmds = {c.title: c for c in app.get_system_commands(app.screen)}
        assert cmds["Browse groups"].help.startswith("Waiting for the network")
        assert cmds["Your rules"].help.startswith("Your own rules")             # offline: as always
        said = []
        monkeypatch.setattr(app, "notify", lambda m, **kw: said.append(m))
        cmds["Browse groups"].callback()
        assert said and "ready as soon as" in said[0]
        app._on_event(ev.Refreshed())                                        # connected
        cmds = {c.title: c for c in app.get_system_commands(app.screen)}
        assert not cmds["Browse groups"].help.startswith("Waiting")
        assert cmds["Browse groups"].callback == app.action_browse
        await pilot.press("q")
