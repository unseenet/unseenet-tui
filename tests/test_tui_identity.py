"""#15: recovery words and copying to another device in the terminal client (UN-RFC 0050)."""
import asyncio
import os
import time

import pytest

pytest.importorskip("textual")

from textual.widgets import Input, Select, Static, TextArea  # noqa: E402

from unseenet.client.session import Session  # noqa: E402
from unseenet.core import recovery as rc  # noqa: E402
from unseenet.core.vault import Vault  # noqa: E402
from unseenet_tui import identity_dialogs as idd  # noqa: E402
from unseenet_tui.app import UnseenetApp  # noqa: E402

from tuihelp import app_ready, laid_out, press, stack, wait_for  # noqa: E402

KDF = {"ops": 1, "mem": 8192 * 1024}


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setenv("UNSEENET_PASSPHRASE", "pw")
    monkeypatch.setenv("UNSEENET_INSECURE_FAST_KDF", "1")


def _vault(tmp_path, name="me.vault", identity=True):
    path = tmp_path / name
    s = Session(Vault.open(path, "pw", **KDF))
    if identity:
        s.new_identity("andreas", "Andreas")
    s.add_node("127.0.0.1:9")                 # nobody answers: no network in these tests
    words = s.recovery_words("pw") if identity else None
    s.vault.close()
    return path, words


def screen_is(app, name: str) -> bool:
    return type(app.screen).__name__ == name


# --- pieces --------------------------------------------------------------------------

def test_the_qr_code_and_the_words_grid():
    qr = idd.qr_text("unseenet-copy:1:" + "a" * 56 + ".onion:7-guitar-tunnel")
    lines = qr.plain.splitlines()
    assert len(lines) > 15 and len({len(x) for x in lines}) == 1      # square, no ragged lines
    assert set(qr.plain) <= set(" ▀▄█\n")
    grid = idd.words_grid(rc.words_from_seed(bytes(32)))
    assert grid.splitlines()[0].startswith(" 1. abandon") and "24. art" in grid


def test_where_words_may_not_be_saved(tmp_path):
    assert idd.same_medium(tmp_path, tmp_path / ".")
    assert idd.looks_like_cloud(tmp_path / "Dropbox" / "words.txt")
    assert not idd.looks_like_cloud(tmp_path / "words.txt")


# --- showing the words (section 4.2) -----------------------------------------------

async def test_show_words_behind_the_passphrase_and_check_three(tmp_path):
    path, words = _vault(tmp_path)
    app = UnseenetApp(path)
    async with app.run_test(size=(140, 50)) as pilot:
        assert await app_ready(pilot, app), stack(app)
        assert not app.session.words_written()
        app.action_show_words()
        assert await wait_for(pilot, lambda: laid_out(app, "#pw")), stack(app)
        app.screen.query_one("#pw", Input).value = "wrong"
        await press(pilot, app, "#ok")
        assert await wait_for(pilot, lambda: bool(app.screen.query(".error"))), stack(app)
        app.screen.query_one("#pw", Input).value = "pw"
        await press(pilot, app, "#ok")
        assert await wait_for(pilot, lambda: screen_is(app, "ShowWordsDialog"), 400), stack(app)
        shown = str(app.screen.query_one("#words", Static).render())
        assert words[0] in shown and words[23] in shown
        await press(pilot, app, "#ok")
        assert await wait_for(pilot, lambda: screen_is(app, "CheckWordsDialog")), stack(app)
        check = app.screen
        for p in check.positions:
            check.query_one(f"#w{p}", Input).value = "nope"
        await press(pilot, app, "#ok")
        assert screen_is(app, "CheckWordsDialog")                       # wrong: stays
        for p in check.positions:
            check.query_one(f"#w{p}", Input).value = words[p - 1][:4].upper()
        await press(pilot, app, "#ok")
        assert await wait_for(pilot, lambda: app.session.words_written()), stack(app)
        await pilot.press("q")


# --- restoring (section 4.4) -------------------------------------------------------

async def test_first_start_restores_from_words(tmp_path, monkeypatch):
    _, words = _vault(tmp_path, "old.vault")
    path = tmp_path / "new.vault"
    asked = []

    async def restore_from_network(self, name=None):       # the network part: core's tests
        return {"state": "read", "fingerprint": self.identity().fingerprint, "label": "",
                "profile": None, "key": self.identity().key_text,
                "closed_groups": [{"group": "familie@abcd", "label": "familie", "owner": "ed25519:x",
                                   "mine": False}]}

    async def ask_to_be_invited(self, group_id, note=""):
        asked.append(group_id)
        return "<m@x>"
    monkeypatch.setattr(Session, "restore_from_network", restore_from_network)
    monkeypatch.setattr(Session, "ask_to_be_invited", ask_to_be_invited)
    app = UnseenetApp(path)
    async with app.run_test(size=(140, 50)) as pilot:
        assert await wait_for(pilot, lambda: laid_out(app, "#how"), 300), stack(app)
        app.screen.query_one("#how", Select).value = "restore"
        app.screen.query_one("#known", Select).value = "__other__"
        app.screen.query_one("#node", Input).value = "127.0.0.1:9"
        await press(pilot, app, "#ok")
        assert await wait_for(pilot, lambda: screen_is(app, "RestoreDialog")), stack(app)
        area = app.screen.query_one("#words", TextArea)
        area.text = " ".join(words[:23] + ["xyzzy"])
        await pilot.pause()
        assert "not a recovery word" in str(app.screen.query_one("#words-state").render())
        area.text = "\n".join(w[:4] for w in words)
        await pilot.pause()
        assert "fit together" in str(app.screen.query_one("#words-state").render())
        await press(pilot, app, "#ok")
        assert await wait_for(pilot, lambda: screen_is(app, "ClosedGroupsDialog"), 400), stack(app)
        assert app.session.identity().seed == rc.seed_from_words(words)
        assert app.session.words_written()
        await press(pilot, app, "#ok")                              # ask the owner
        assert await wait_for(pilot, lambda: asked == ["familie@abcd"]), stack(app)
        await pilot.press("q")


# --- copying (section 3), with the core's copy stubbed ------------------------------

class FakeOffer:
    def __init__(self):
        self.code, self.qr = "7-guitar-tunnel", "unseenet-copy:1:" + "a" * 56 + ".onion:7-guitar-tunnel"
        self.expires, self.state, self.error = time.time() + 600, "waiting", ""
        self.confirmed = None

    async def wait_for_check(self, timeout=None):
        await asyncio.sleep(0.2)
        self.state = "check"
        return ("ocean", "zebra")

    async def confirm(self, match):
        self.confirmed = match
        return {"state": "done" if match else "cancelled", "added": ["andreas"], "error": ""}

    async def cancel(self):
        self.state = "cancelled"


async def test_copy_to_shows_the_code_then_the_check_words(tmp_path, monkeypatch):
    path, _ = _vault(tmp_path)
    offer = FakeOffer()
    chosen = {}

    async def start_copy(self, names=None, group_secrets=True, meeting_nodes=None):
        chosen.update(names=names, group_secrets=group_secrets)
        return offer
    monkeypatch.setattr(Session, "start_copy", start_copy)
    app = UnseenetApp(path)
    async with app.run_test(size=(140, 60)) as pilot:
        assert await app_ready(pilot, app), stack(app)
        app.action_copy_to()
        assert await wait_for(pilot, lambda: screen_is(app, "CopyChoiceDialog")), stack(app)
        app.screen.query_one("#secrets").value = False
        await press(pilot, app, "#ok")
        assert await wait_for(pilot, lambda: screen_is(app, "CopyOfferDialog")), stack(app)
        assert "7-guitar-tunnel" in str(app.screen.query_one("#code", Static).render())
        assert await wait_for(pilot, lambda: app.screen.check == ("ocean", "zebra")), stack(app)
        await press(pilot, app, "#ok")
        assert await wait_for(pilot, lambda: offer.confirmed is True), stack(app)
        assert chosen == {"names": ["andreas"], "group_secrets": False}
        await pilot.press("q")


async def test_get_from_another_device_by_code(tmp_path, monkeypatch):
    path, _ = _vault(tmp_path)
    other = Session(Vault.open(tmp_path / "other.vault", "pw", **KDF))
    other.new_identity("anon", "Anon")
    seed = other.data["identities"]["anon"]["seed"]

    class FakeReceive:
        check_words = ("ocean", "zebra")

        def __init__(self, session):
            self.session = session

        async def finish(self):
            await asyncio.sleep(0.2)
            self.session.data["identities"]["anon"] = {"seed": seed, "display": "Anon"}
            self.session.save()
            return ["anon"]

        async def cancel(self):
            pass

    async def receive_copy(self, code, meeting_nodes=None):
        assert code == "7-guitar-tunnel"
        return FakeReceive(self)
    monkeypatch.setattr(Session, "receive_copy", receive_copy)
    app = UnseenetApp(path)
    async with app.run_test(size=(140, 50)) as pilot:
        assert await app_ready(pilot, app), stack(app)
        app.action_copy_from()
        assert await wait_for(pilot, lambda: laid_out(app, "#code")), stack(app)
        app.screen.query_one("#code", Input).value = "7-guitar-tunnel"
        await press(pilot, app, "#ok")
        assert await wait_for(pilot, lambda: "anon" in app.session.data["identities"], 300), stack(app)
        assert await wait_for(pilot, lambda: not screen_is(app, "CopyWaitDialog")), stack(app)
        await pilot.press("q")
