"""#442: files on posts you read (UN-RFC 0027 sections 6.4, 9.2): BLOB for readers,
checking a blob, alternative text, and pictures decoded in a separate process."""
import io
import struct
import zlib

import pytest
from PIL import Image

import unseenet.client.session as sess
from unseenet.client import decode, media
from unseenet.client.session import Session, SessionError
from unseenet.core import blobs as bl
from unseenet.core.store import Store
from unseenet.core.vault import Vault
from unseenet.node.server import Node, NodeConfig


def png(w=40, h=30, colour=(200, 30, 30)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (w, h), colour).save(out, "PNG")
    return out.getvalue()


def gif(frames=3) -> bytes:
    out = io.BytesIO()
    imgs = [Image.new("RGB", (10, 10), (i * 60, 0, 0)) for i in range(frames)]
    imgs[0].save(out, "GIF", save_all=True, append_images=imgs[1:], duration=50, loop=0)
    return out.getvalue()


@pytest.fixture
async def net(tmp_path, monkeypatch):
    monkeypatch.setattr(sess, "DEFAULT_POW", 10)
    made, servers = [], []

    async def node(**blobs):
        n = Node(Store(), NodeConfig(min_pow=8, forward_delay=(0, 0), blobs=blobs or None))
        server = await n.serve("127.0.0.1", 0)
        servers.append(server)
        return f"127.0.0.1:{server.sockets[0].getsockname()[1]}"

    def session(name, addr):
        s = Session(Vault.open(tmp_path / f"{name}.vault", "pw", ops=1, mem=8192 * 1024),
                    status=lambda *_: None)
        s.new_identity(name, name.title())
        s.add_node(addr)
        made.append(s)
        return s
    yield node, session
    for s in made:
        await s.close()
    for server in servers:
        server.close()


async def _group_with_a_picture(me, octets, alt="En rød firkant"):
    gid = await me.create_group("dk.billeder", "open", min_pow=10,
                                extra={"attachments": {"max_size": 1048576}})
    f = media.prepare("foto.png", octets)
    f = media.Prepared(f.name, f.media_type, f.octets, f.note, alt, f.dim)
    await me.post(gid, "Se her", "Et billede\n", files=[f])
    return gid, f


def test_the_terminal_lists_files_and_saves_them_under_safe_names(tmp_path):
    from unseenet_tui import app as tui
    text = tui.attachments_text([{"blob": "b", "type": "image/png", "size": 12345,
                                  "name": "foto.png", "alt": "En hund", "dim": [40, 30]}]).plain
    assert "foto.png - picture, 12 KiB, 40×30" in text and "En hund" in text
    (tmp_path / "a.png").write_text("x")
    assert tui.save_name(tmp_path, "../../etc/a.png") == tmp_path / "a (2).png"   # never a path
    assert tui.save_name(tmp_path, ".bashrc") == tmp_path / "bashrc"              # nothing hidden
    assert tui.save_name(tmp_path, "") == tmp_path / "file"
