# Developers: the terminal client's files

The terminal client is a [Textual](https://textual.textualize.io) app on the Unseenet
core's `Session`. Its code is split by area (unseenet-tui#20), the same way as the core's
Session: one class, `UnseenetApp`, put together in `app.py` from mixins.

- **`app.py`** builds `UnseenetApp(GroupsMixin, ..., PinsMixin, App)` and keeps its layout,
  startup and notifications, its class attributes (BINDINGS, CSS, reactives) and its
  **`@on` handlers**: Textual collects those from the class's own namespace only, so in a
  mixin they would silently stop working. It also re-exports every name that used to live
  in it, so `from unseenet_tui.app import ComposeDialog` keeps working.
- **`app_*.py`** are the mixins, one per area; their methods use the app's widgets and
  session as `self`. The mixins come before `App` in the bases, so their methods win over
  App's.
- **`dialogs/`** holds the dialogs by area, on `dialogs/base.py`'s `Dialog`; **`screens.py`**
  the read screen, the chat box and About; **`helpers.py`** what all of them share.
- Nothing imports `app.py` back, so there are no import cycles.
- Every file opens with what belongs in it and every public function and method has a
  docstring; `tests/test_docstrings.py` fails on a new one without.

## The files

| File | What belongs in it |
|------|--------------------|
| `app.py` | Unseenet TUI client (Textual) |
| `app_actions.py` | UnseenetApp's the keys' actions: new post, reply, read, mark, refresh |
| `app_addresses.py` | UnseenetApp's addresses: opening and copying them (UN-RFC 0031 3.4.1) |
| `app_chat.py` | UnseenetApp's the chat view: chat lines, sending, files in chat (UN-RFC 0008 5) |
| `app_feeds.py` | UnseenetApp's feeds and following (UN-RFC 0034) |
| `app_groups.py` | UnseenetApp's the group list: filling it, choosing, reading and counting groups |
| `app_identity.py` | UnseenetApp's recovery words and copying an identity (UN-RFC 0050) |
| `app_names.py` | UnseenetApp's names, profiles, moderation and more (UN-RFC 0019 5.4) |
| `app_pages.py` | UnseenetApp's sites and pages, and the dialogs and tools after them in the class |
| `app_pins.py` | UnseenetApp's pinned posts, and the class's last tools (UN-RFC 0033 7) |
| `app_push.py` | UnseenetApp's push from the nodes, and what follows it in the class (UN-RFC 0008 2) |
| `dialogs/base.py` | The dialogs everything builds on: Dialog, and the input, info, confirm and key-list dialogs |
| `dialogs/compose.py` | Writing: posts, pages, the preview, the signature, pins and reactions |
| `dialogs/groups.py` | Groups: creating, editing, their info, and browsing what the nodes carry |
| `dialogs/moderation.py` | Reporting and the moderators' tools |
| `dialogs/nodes.py` | Your nodes: the list, adding one, and a node's info |
| `dialogs/people.py` | People: contacts, profiles (UN-RFC 0022) and names (UN-RFC 0019) |
| `dialogs/setup.py` | Unlocking, setting up, the passphrase and Tor bridges |
| `export_dialog.py` | Export my data (#14, on the core's export, unseenet#577 / UN-RFC 0049 section 7): what an export holds and does not, private posts and chats only when chosen - then a passphrase, twice - and where it is saved (Downloads, else the home folder) |
| `helpers.py` | The module-level helpers and constants the TUI's parts share: marks, names of networks and groups, node details, icons |
| `identity_dialogs.py` | Recovery words and copying to another device (UN-RFC 0050, unseenet-tui#15): the dialogs |
| `notify.py` | Notifications when something lands in For you (unseenet-tui#4), the terminal way |
| `screens.py` | The screens and widgets: the chat box, reading a post, files on it, About |
