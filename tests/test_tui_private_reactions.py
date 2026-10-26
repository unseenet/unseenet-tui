"""#9: reactions in private chats and posts, on the core's (unseenet#513): the counts
under a private message, "l reacts" where the core allows it, and l in a private chat."""
from datetime import datetime, timezone

import pytest

pytest.importorskip("textual")

from unseenet.client.session import Message, Session  # noqa: E402
from unseenet.client.session import Permission  # noqa: E402
from unseenet.core import reactions as rx  # noqa: E402
from unseenet.core.vault import Vault  # noqa: E402
from unseenet_tui.app import UnseenetApp  # noqa: E402

from tuihelp import app_ready, stack  # noqa: E402

KDF = {"ops": 1, "mem": 8192 * 1024}
SONIA = "ed25519:" + "s" * 52


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


async def test_a_private_message_shows_its_reactions(vault, monkeypatch):
    app = UnseenetApp(vault)
    async with app.run_test(size=(140, 45)) as pilot:
        assert await app_ready(pilot, app), stack(app)
        s = app.session
        msg = Message(1, "<a@x>", s.my_inbox or "inbox@x", subject="", from_name="Sonia",
                      author_key=SONIA, sender_key=SONIA, private=True, encrypted=True,
                      date=datetime.now(timezone.utc), body="Hej\n", kind="chat")
        counts = rx.Counts(by_emoji={"❤️": [SONIA]}, by_key={SONIA: ("❤️", "<r@x>")})
        monkeypatch.setattr(s, "reactions_on", lambda m: counts)
        monkeypatch.setattr(s, "react_permission", lambda g, m=None: Permission(True))
        assert "❤️" in app._reactions_line(msg)
        _, body = app.article(msg)
        assert "l reacts" in body.plain and "❤️" in body.plain
        monkeypatch.setattr(s, "react_permission",
                            lambda g, m=None: Permission(False, "You cannot react to your own post", "own"))
        _, body = app.article(msg)
        assert "l reacts" not in body.plain
        await pilot.press("q")
