"""#4: notifications in the terminal - from the core's Session.notifications."""
import pytest

pytest.importorskip("textual")

from unseenet.client.session import Session  # noqa: E402
from unseenet.core.vault import Vault  # noqa: E402
from unseenet_tui import notify as nf  # noqa: E402
from unseenet_tui.app import UnseenetApp  # noqa: E402

from tuihelp import app_ready, press, stack, wait_for  # noqa: E402

KDF = {"ops": 1, "mem": 8192 * 1024}
ONE = {"settings": {"on": True, "content": False},
       "items": [{"id": "<a@x>", "group": "inbox@x", "why": "private", "chat": True}], "count": 1}


def test_the_pieces():
    assert nf.desktop_terminal({"TERM_PROGRAM": "iTerm.app"}) and nf.desktop_terminal({"WT_SESSION": "1"})
    assert not nf.desktop_terminal({"TERM": "linux"})
    seq = nf.sequences("Unseenet: hej\x1b]evil\x07")
    assert seq.startswith("\x1b]9;Unseenet: hej]evil\x07") and seq.count("\x1b") == 2   # no injected ESC
    assert nf.text_of(ONE) == "1 new in For you"
    shown = {**ONE, "settings": {"on": True, "content": True},
             "items": [{**ONE["items"][0], "who": "Sonia", "text": "Hej"}]}
    assert nf.text_of(shown) == "Sonia: Hej"
    assert nf.on_screen(ONE["items"][0], "contact:ed25519:x") and not nf.on_screen(ONE["items"][0], "dk.snak@x")


@pytest.fixture
def vault(tmp_path, monkeypatch):
    path = tmp_path / "me.vault"
    s = Session(Vault.open(path, "pw", **KDF))
    s.new_identity("andreas", "Andreas")
    s.add_node("127.0.0.1:9")
    s.vault.close()
    monkeypatch.setenv("UNSEENET_PASSPHRASE", "pw")
    monkeypatch.setenv("UNSEENET_INSECURE_FAST_KDF", "1")
    return path


async def test_asked_once_then_the_bell(vault, monkeypatch):
    rings = []
    app = UnseenetApp(vault)
    monkeypatch.setattr(app, "bell", lambda: rings.append(1))
    monkeypatch.setattr(nf, "desktop_terminal", lambda env=None: False)
    async with app.run_test(size=(140, 45)) as pilot:
        assert await app_ready(pilot, app), stack(app)
        pending = [dict(ONE, settings=app.session.notification_settings())]
        monkeypatch.setattr(app.session, "notifications",
                            lambda: pending.pop() if pending else {"count": 0, "items": [], "settings": app.session.notification_settings()})
        app.check_notifications()
        assert await wait_for(pilot, lambda: type(app.screen).__name__ == "AskDialog"), stack(app)
        await press(pilot, app, "#ok")
        assert await wait_for(pilot, lambda: rings == [1]), stack(app)
        assert app.session.notification_settings()["on"] is True
        await pilot.press("q")


async def test_never_on_the_bootable(vault, monkeypatch):
    monkeypatch.setenv("UNSEENET_BOOTABLE", "1")
    app = UnseenetApp(vault)
    async with app.run_test(size=(140, 45)) as pilot:
        assert await app_ready(pilot, app), stack(app)
        asked = []
        monkeypatch.setattr(app.session, "notifications", lambda: asked.append(1) or ONE)
        app.check_notifications()
        await pilot.pause(0.5)
        assert asked == [] and type(app.screen).__name__ != "AskDialog"
        await pilot.press("q")
