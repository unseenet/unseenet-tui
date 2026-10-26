"""#14: Export your data in the terminal client, on the core's Session.export."""
import zipfile

import pytest

pytest.importorskip("textual")

from unseenet.client.session import Session  # noqa: E402
from unseenet.core.vault import Vault  # noqa: E402
from unseenet_tui import export_dialog as ed  # noqa: E402
from unseenet_tui.app import UnseenetApp  # noqa: E402

from tuihelp import app_ready, press, stack, wait_for  # noqa: E402

KDF = {"ops": 1, "mem": 8192 * 1024}


def test_where(tmp_path):
    assert ed.default_path("abcdefgh12", tmp_path).parent == tmp_path        # no Downloads
    (tmp_path / "Downloads").mkdir()
    p = ed.default_path("abcdefgh12", tmp_path)
    assert p.parent.name == "Downloads" and p.name.startswith("unseenet-export-abcdefgh-")


async def test_export_and_the_passphrase(tmp_path, monkeypatch):
    path = tmp_path / "me.vault"
    s = Session(Vault.open(path, "pw", **KDF))
    s.new_identity("andreas", "Andreas")
    s.add_node("127.0.0.1:9")
    s.vault.close()
    monkeypatch.setenv("UNSEENET_PASSPHRASE", "pw")
    monkeypatch.setenv("UNSEENET_INSECURE_FAST_KDF", "1")
    out = tmp_path / "x.zip"
    monkeypatch.setattr(ed, "default_path", lambda fp, home=None: out)
    app = UnseenetApp(path)
    async with app.run_test(size=(140, 45)) as pilot:
        assert await app_ready(pilot, app), stack(app)
        app.action_export()
        assert await wait_for(pilot, lambda: type(app.screen).__name__ == "ExportDialog"), stack(app)
        await press(pilot, app, "#ok")
        assert await wait_for(pilot, lambda: out.exists()), stack(app)
        assert "export.json" in zipfile.ZipFile(out).namelist()
        app.action_export()                                 # with private: a passphrase twice
        assert await wait_for(pilot, lambda: type(app.screen).__name__ == "ExportDialog"), stack(app)
        app.screen.query_one("#private").value = True
        app.screen.query_one("#pw").value = "lang sætning"
        app.screen.query_one("#pw2").value = "en anden"
        await press(pilot, app, "#ok")
        assert "differ" in str(app.screen.query_one("#error").render())
        app.screen.query_one("#pw2").value = "lang sætning"
        await press(pilot, app, "#ok")
        sealed = tmp_path / "x.zip.sealed"
        assert await wait_for(pilot, lambda: sealed.exists()), stack(app)
        assert not sealed.read_bytes().startswith(b"PK")
        await pilot.press("q")
