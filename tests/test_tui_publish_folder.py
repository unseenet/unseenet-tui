"""#19: publishing a folder as a site in the terminal client, on Session.publish_site."""
import pytest

pytest.importorskip("textual")

from unseenet.client.session import Session  # noqa: E402
from unseenet.core.vault import Vault  # noqa: E402
from unseenet_tui import site_publish as sp  # noqa: E402
from unseenet_tui.app import UnseenetApp  # noqa: E402

from tuihelp import app_ready, press, stack, wait_for  # noqa: E402

KDF = {"ops": 1, "mem": 8192 * 1024}
PLAN = {"changes": [{"path": "index", "title": "Hjem", "action": "changed", "source": "index.md", "files": []},
                    {"path": "ny", "title": "Ny", "action": "new", "source": "ny.md", "files": ["a.jpg"]},
                    {"path": "om", "title": "Om", "action": "kept", "source": None, "files": []}],
        "notes": ["index.md:3: HTML turned into Markdown"], "problems": [], "sent": 0}


def test_the_plan_as_text():
    text = sp.plan_text(PLAN)
    assert "~ /index  Hjem  - changed" in text and "+ /ny  Ny  - new  (1 picture)" in text
    assert "not in the folder - kept" in text and "HTML turned" in text
    assert sp.to_send(PLAN) == 2 and sp.to_send({"changes": []}) == 0


async def test_publishing_a_folder(tmp_path, monkeypatch):
    path = tmp_path / "me.vault"
    s = Session(Vault.open(path, "pw", **KDF))
    s.new_identity("andreas", "Andreas")
    s.add_node("127.0.0.1:9")
    s.vault.close()
    monkeypatch.setenv("UNSEENET_PASSPHRASE", "pw")
    monkeypatch.setenv("UNSEENET_INSECURE_FAST_KDF", "1")
    folder = tmp_path / "site"
    folder.mkdir()
    calls = []
    app = UnseenetApp(path)
    async with app.run_test(size=(140, 45)) as pilot:
        assert await app_ready(pilot, app), stack(app)

        async def publish_site(gid, f, *, dry_run=False, prune=False, progress=None):
            calls.append((f, dry_run, prune))
            return PLAN if dry_run else {**PLAN, "sent": 2}
        monkeypatch.setattr(app.session, "publish_site", publish_site)
        monkeypatch.setattr(type(app), "_is_site", lambda self, gid: True)
        monkeypatch.setattr(type(app), "_writes_pages", lambda self, gid: True)
        monkeypatch.setattr(type(app), "select_group", lambda self, gid: None)
        app.current_group = "site@abcdabcdabcdabcd"
        app.action_publish_folder()
        assert await wait_for(pilot, lambda: type(app.screen).__name__ == "FolderDialog"), stack(app)
        app.screen.query_one("#folder").value = str(tmp_path / "nothing")
        await press(pilot, app, "#ok")
        assert "not a folder" in str(app.screen.query_one("#error").render())
        app.screen.query_one("#folder").value = str(folder)
        app.screen.query_one("#prune").value = True
        await press(pilot, app, "#ok")
        assert await wait_for(pilot, lambda: type(app.screen).__name__ == "PlanDialog"), stack(app)
        assert "Publish 2 pages" in str(app.screen.query_one("#ok").label)
        await press(pilot, app, "#ok")
        assert await wait_for(pilot, lambda: len(calls) == 2), stack(app)
        assert calls == [(str(folder), True, True), (str(folder), False, True)]
        await pilot.press("q")
