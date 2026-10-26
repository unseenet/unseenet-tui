"""#3: imported posts say where they were first published (UN-RFC 0049 section 5.1)."""
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("textual")

from unseenet.client.session import Message, Session  # noqa: E402
from unseenet.core.vault import Vault  # noqa: E402
from unseenet_tui.app import UnseenetApp, network_name, origin_text  # noqa: E402

from tuihelp import app_ready, stack  # noqa: E402

KDF = {"ops": 1, "mem": 8192 * 1024}
NOW = datetime.now(timezone.utc)


def imported(**kw):
    return Message(1, "<a@x>", "dk.snak@abcdabcdabcdabcd", subject="Ferie", from_name="Bob",
                   author_key="ed25519:" + "b" * 52, date=NOW, body="Se her\n",
                   origin_network="facebook", origin_date=datetime(2025, 8, 3, 14, 20, tzinfo=timezone.utc), **kw)


def test_the_text():
    assert origin_text(imported()) == "Imported from Facebook · originally 3 August 2025"
    assert network_name("x") == "X" and network_name("someplace") == "Someplace"
    plain = Message(2, "<b@x>", "dk.snak@abcdabcdabcdabcd", date=NOW)
    assert origin_text(plain) == ""
    assert imported().old_on_arrival and not Message(3, "<c@x>", "g@x", date=NOW).old_on_arrival


async def test_the_reader_gives_the_time_of_posting_and_the_claim(tmp_path, monkeypatch):
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
        head, _ = app.article(imported())
        assert "posted " in head and "Imported from Facebook · originally 3 August 2025" in head
        assert "claim" in head
        head, _ = app.article(Message(2, "<b@x>", "dk.snak@abcdabcdabcdabcd", subject="x",
                                      from_name="Bob", author_key="ed25519:" + "b" * 52, date=NOW))
        assert "Imported" not in head and "posted " not in head
        await pilot.press("q")
