"""#5: a site's owner chooses whether search engines may index it (UN-RFC 0035 section 3)."""
import pytest

pytest.importorskip("textual")

from unseenet.client.session import Session  # noqa: E402
from unseenet.core.vault import Vault  # noqa: E402
from unseenet_tui.app import EditGroupDialog, UnseenetApp  # noqa: E402

from tuihelp import app_ready, press, stack  # noqa: E402

KDF = {"ops": 1, "mem": 8192 * 1024}


async def test_the_index_choice_for_sites_only(tmp_path, monkeypatch):
    path = tmp_path / "me.vault"
    s = Session(Vault.open(path, "pw", **KDF))
    s.new_identity("andreas", "Andreas")
    s.add_node("127.0.0.1:9")
    s.vault.close()
    monkeypatch.setenv("UNSEENET_PASSPHRASE", "pw")
    monkeypatch.setenv("UNSEENET_INSECURE_FAST_KDF", "1")
    app = UnseenetApp(path)
    async with app.run_test(size=(140, 50)) as pilot:
        assert await app_ready(pilot, app), stack(app)
        got = []
        edit = EditGroupDialog({"name": "dk.side", "view": "pages", "type": "channel", "index": "no",
                                "min_pow": 20})
        app.push_screen(edit, got.append)
        await pilot.pause(0.2)
        assert edit.query_one("#index").value == "no"
        assert "outlive the pages" in str(edit.query_one("#index-warning").render())
        edit.query_one("#index").value = "yes"
        await press(pilot, app, "#ok")
        assert got and got[0]["index"] == "yes"
        threads = EditGroupDialog({"name": "dk.snak", "view": "threads", "type": "open", "min_pow": 20})
        app.push_screen(threads)
        await pilot.pause(0.2)
        assert not threads.query("#index")
        app.pop_screen()
        await pilot.press("q")
