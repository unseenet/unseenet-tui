<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/unseenet/unseenet/main/assets/logo-dark.svg">
    <img src="https://raw.githubusercontent.com/unseenet/unseenet/main/assets/logo-light.svg" alt="Unseenet" width="360">
  </picture>
</p>

# unseenet-tui

**The Unseenet terminal client.** Groups and threads read one post at a
time, chats, private posts, feeds and sites, your people and your nodes - in
a terminal, over Tor. Built on the [unseenet](https://github.com/unseenet/unseenet)
core library like every other client; this repository is only the user
interface ([Textual](https://textual.textualize.io/)).

> Status: **pre-alpha**, like the rest of Unseenet. Not security reviewed.

> **Being set up** ([unseenet#548](https://github.com/unseenet/unseenet/issues/548)).
> Until the move is finished the terminal client is released from
> unseenet (`pipx install "unseenet[tui]"`); this repository takes over then.

## Use it

- **Portable or bootable**, with Tor inside: the
  [Portable · Terminal and Bootable · Terminal builds](https://unseenet.org/portable/).
- **With Python** (3.11+, and `tor` installed): `pipx install unseenet-tui`,
  then `unseenet-tui`.

How to use it: [Using the terminal client](https://unseenet.org/client/)
(from [`docs/client.md`](docs/client.md)).

## Develop

```bash
pip install -e ".[dev]"     # the core comes from PyPI; for its main:
pip install "unseenet @ git+https://github.com/unseenet/unseenet@main"
pytest -q                   # headless, against a local node, no Tor
mkdocs serve                # the docs page, alone
```

| | |
|---|---|
| `src/unseenet_tui/app.py` | the client: the app, its screens and dialogs |
| `tests/` | headless tests against a node on localhost (`tuihelp.py`: helpers) |
| `docs/` | the manual, gathered into unseenet.org by unseenet-docs |
| `tools/docs_screenshots.py`, `tools/docs_recording.py` | the screenshots and the recording in the docs |

Everything that is not the user interface belongs in the core: when the
client needs something the core lacks, it gets an issue in unseenet, and
changes in the core that the client must follow get an issue here.

## Versions and releases

This client numbers its own releases, on the
[Unseenet conventions](https://github.com/unseenet/.github/blob/main/CONVENTIONS.md#versions);
each release names the minor version of the core it needs
(`unseenet>=0.5,<0.6`). Tags `vX.Y.Z` on `main`; `release.yml` checks the
tag against `__version__` and `CHANGELOG.md`, publishes to PyPI and makes
the GitHub release.

## Part of Unseenet

Unseenet is made in several repositories, each released on its own:

| Repository | What it is |
|---|---|
| [unseenet](https://github.com/unseenet/unseenet) | The protocol (UN-RFCs), the core library, the node and the command-line client |
| [unseenet-tui](https://github.com/unseenet/unseenet-tui) | The terminal client (moving here from unseenet, unseenet#548) |
| [unseenet-gui](https://github.com/unseenet/unseenet-gui) | The graphical client - in a window, booted, or in a browser |
| [unseenet-portable](https://github.com/unseenet/unseenet-portable) | Builds the portable clients, Terminal and Graphical, with Tor inside (unseenet#549) |
| [unseenet-bootable](https://github.com/unseenet/unseenet-bootable) | Builds the bootable images, Terminal and Graphical, and later a node image (unseenet#550) |
| [unseenet-docs](https://github.com/unseenet/unseenet-docs) | The site [unseenet.org](https://unseenet.org), gathering the docs of every repository (unseenet#547) |
| [unseenet-entrance](https://github.com/unseenet/unseenet-entrance) | Web entrances: the graphical client in an ordinary browser |
| [unseenet-gateway](https://github.com/unseenet/unseenet-gateway) | Gateways to other networks: Usenet, Nostr, Matrix |
| [unseenet-bots](https://github.com/unseenet/unseenet-bots) | Bots that keep channels filled with current content |
| [unseenet-helpbot](https://github.com/unseenet/unseenet-helpbot) | The bot that answers questions in `unseenet.help` |
| [unseenet-stats](https://github.com/unseenet/unseenet-stats) | Statistics for the network |
| [unseenet-mcp](https://github.com/unseenet/unseenet-mcp) | An MCP server, so a language model can read and post as you (planning) |
| [unseenet-demo](https://github.com/unseenet/unseenet-demo) | Demo people, feeds and sites, made with the command-line client |

## License

[Apache License 2.0](LICENSE).
