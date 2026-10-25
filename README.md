<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/unseenet/unseenet/main/assets/logo-dark.svg">
    <img src="https://raw.githubusercontent.com/unseenet/unseenet/main/assets/logo-light.svg" alt="Unseenet" width="360">
  </picture>
</p>

<h1 align="center">unseenet-tui</h1>

<p align="center"><b>Usenet, unseen - in your terminal.</b><br>
Discussions you read post by post, chats, private posts, feeds and sites, over Tor.</p>

<p align="center">
  <a href="https://pypi.org/project/unseenet-tui/"><img src="https://img.shields.io/pypi/v/unseenet-tui?label=PyPI" alt="PyPI"></a>
  <a href="https://github.com/unseenet/unseenet-tui/actions/workflows/tests.yml"><img src="https://github.com/unseenet/unseenet-tui/actions/workflows/tests.yml/badge.svg" alt="tests"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue" alt="Apache-2.0"></a>
</p>

<p align="center">
  <img src="docs/img/demo.gif" alt="Unlocking the vault, reading a thread post by post, replying with a quote, chatting and browsing groups" width="820">
</p>

> **Pre-alpha.** Nothing is stable yet, and Unseenet has not had a security
> review. Do not rely on it to protect anyone yet.

## What it is

The terminal client for [Unseenet](https://unseenet.org), a discussion
network in the spirit of the old Usenet and BBSes: groups and chats on
nodes anyone can run, linked over Tor onion services, with nobody in
charge of who may post or what is shown.

- **It starts its own Tor.** No accounts, no public IP, nothing to set up.
- **Everything you have is in one encrypted file** - your identity, your
  groups, what you have read - the *vault*. Lose the stick, and nobody can
  read it.
- **Threads and chats in one place.** Discussion groups show a thread tree
  you read one post at a time and answer with a quote; chat groups scroll
  like a chat; feeds and sites have their own views.
- **The keyboard for daily use, the palette for the rest.** The common
  actions have keys; everything else is in the command palette (`Ctrl+P`).

The same vault opens in the [graphical client](https://github.com/unseenet/unseenet-gui).

## Get it

| | |
|---|---|
| **Portable** (Windows, macOS, Linux) | One folder with Tor inside, nothing to install, runs from a USB stick: [unseenet-portable](https://github.com/unseenet/unseenet-portable) |
| **Bootable** (USB, SSD …) | Boots straight into Unseenet, runs from memory: [unseenet-bootable](https://github.com/unseenet/unseenet-bootable) |
| **With Python** (3.11+, and `tor` installed) | `pipx install unseenet-tui`, then `unseenet-tui` |

First start: choose a passphrase for the vault, a display name, and a node
- the client suggests the project's own and subscribes you to
`unseenet.help`, where you can ask anything.

**The manual:** [Using the terminal client](https://unseenet.org/client/).

## How it works

The client is a thin user interface ([Textual](https://textual.textualize.io/))
on the Unseenet core library, [`unseenet`](https://github.com/unseenet/unseenet),
like every other Unseenet client: the core signs, encrypts, ripens keys and
talks to the nodes; this repository only shows it. How the network itself
works: [How it works](https://unseenet.org/guide/).

## Develop

```bash
pip install -e ".[dev]"
pip install "unseenet @ git+https://github.com/unseenet/unseenet@main"   # the core's main
pytest -q                   # headless, against a local node, no Tor
mkdocs serve                # the manual, alone
```

| | |
|---|---|
| `src/unseenet_tui/app.py` | the client: the app, its screens and dialogs |
| `tests/` | headless tests against a node on localhost (`tuihelp.py`: helpers) |
| `docs/` | the manual, published on unseenet.org by unseenet-docs |
| `tools/` | the screenshots and the recording in the manual |

What is not user interface belongs in the core: when the client needs
something the core lacks, it gets an issue in unseenet.

## Versions and releases

unseenet-tui numbers its own releases, on the
[Unseenet conventions](https://github.com/unseenet/.github/blob/main/CONVENTIONS.md#versions); each names the minor version of the core it needs (now `unseenet>=0.5,<0.6`).
Tags `vX.Y.Z` on `main`; the release workflow publishes to PyPI and makes the GitHub release from
[CHANGELOG.md](CHANGELOG.md).

## Part of Unseenet

| Repository | What it is |
|---|---|
| [unseenet](https://github.com/unseenet/unseenet) | The protocol (UN-RFCs), the core library, the node and the command-line client |
| [unseenet-tui](https://github.com/unseenet/unseenet-tui) | The terminal client |
| [unseenet-gui](https://github.com/unseenet/unseenet-gui) | The graphical client - in a window, booted, or in a browser |
| [unseenet-portable](https://github.com/unseenet/unseenet-portable) | Builds the portable clients, Terminal and Graphical, with Tor inside |
| [unseenet-bootable](https://github.com/unseenet/unseenet-bootable) | Builds the bootable images, Terminal and Graphical |
| [unseenet-docs](https://github.com/unseenet/unseenet-docs) | The site [unseenet.org](https://unseenet.org) |
| [unseenet-entrance](https://github.com/unseenet/unseenet-entrance) | Web entrances: the graphical client in an ordinary browser |
| [unseenet-gateway](https://github.com/unseenet/unseenet-gateway) | Gateways to other networks: Usenet, Nostr, Matrix |
| [unseenet-bots](https://github.com/unseenet/unseenet-bots) | Bots that keep channels filled with current content |
| [unseenet-helpbot](https://github.com/unseenet/unseenet-helpbot) | The bot that answers questions in `unseenet.help` |
| [unseenet-stats](https://github.com/unseenet/unseenet-stats) | Statistics for the network |
| [unseenet-mcp](https://github.com/unseenet/unseenet-mcp) | An MCP server, so a language model can read and post as you (planning) |
| [unseenet-demo](https://github.com/unseenet/unseenet-demo) | Demo people, feeds and sites, made with the command-line client |

## License

[Apache License 2.0](LICENSE). The Unseenet logo: CC BY 4.0.
