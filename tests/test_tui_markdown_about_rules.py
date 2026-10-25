"""Markdown in a profile's about and a group's rules (#211, UN-RFC 0022 4.1, 0014 2.1)."""

import json

from unseenet.core import crypto, markup
from unseenet.core import profiles as prof
from unseenet.core.groups import Manifest, make_manifest

HOME = """# Andreas

I make **Unseenet** - Usenet, unseen.

- Python and Tor
- [the code](https://github.com/unseenet/unseenet)

![me](blob1:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa)
"""


def _types(nodes, out=None):
    out = [] if out is None else out
    for n in nodes:
        if isinstance(n, list):
            _types(n, out)
            continue
        out.append(n["t"])
        for key in ("c", "items"):
            if isinstance(n.get(key), list):
                _types(n[key], out)
    return out


def test_tui_shows_markdown_profiles_and_rules():
    import pytest
    pytest.importorskip("textual")
    from unseenet_tui.app import manifest_text
    shown = manifest_text(HOME, markup.MARKDOWN_TYPE).plain
    assert "Andreas" in shown and "# Andreas" not in shown and "**" not in shown
    assert "[image: me]" in shown
    assert "# Andreas" in manifest_text(HOME, None).plain   # plain stays as written
