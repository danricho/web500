# Development notes

Implementation detail for people modifying the code. For what the game is and how to
install and host it, see [README.md](../README.md) (its architecture section covers
the state-machine diagram this file builds on). Bot behaviour has its own document:
[BOTS.md](BOTS.md). Planned work lives in [ROADMAP.md](ROADMAP.md).

## Architecture

```
main.py               Flask app + Socket.IO event handlers; thin routing layer only
game_state.py         GameStateMachine — all game rules, state and flow (the core file);
                      also owns the table registry (any number of tables run at once)
bots.py               bot players: lobby-seatable PlayerBot (view-restricted, per-bot
                      personality) + the predictable random dev test-mode bot
playing_cards.py      Card / Deck classes, suit & rank constants, trump-aware sorting
threaded_schedule.py  ThreadedSchedule — worker thread + job queue + `schedule` poller;
                      a failing job logs its traceback and fires the on_error hook
                      (wired to a SERVER ERROR toast); the worker always survives
dotdict.py            dict subclass with attribute access (players/teams/bids)
templates/game_client.j2.html    single-page client UI for one table (Jinja2)
templates/choose_table.j2.html   table picker shown between login and the game client
static/game_client.js            client logic: renders pushed state, emits player actions
static/svg.js                    single lookup for every SVG icon the client swaps in at
                                 runtime (suit icons, bot-name icons, toolbar icons)
data/                 runtime files (gitignored): auth, session key, data/tables/<name>/
                      per-table saves
```

The server is authoritative; clients never compute game logic. Every browser connected
to a table receives that table's entire game state on every push and emits player
actions (seat, bid, discard, play) back as Socket.IO events, scoped to that table's
room. Any number of tables can run at once, each independently — pick or create one
after logging in. Hiding opponents' cards is a client-side rendering concern only.

The full state-machine table and diagram live in the README's
[architecture section](../README.md#the-game-state-machine); mechanics for anyone
working on the code are under "State machine mechanics" below.

### Player bots

The lobby's ADD BOTS button fills empty seats with server-side bot players — they
bid, discard and play with human-like personalities (imperfect memory, varying
confidence, occasional miscalculations) and only ever see what a human in their
seat could see. Design notes, behaviour model and phase status live in
[BOTS.md](BOTS.md).

## Running for development

Requires Python 3.

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt

# dev server (Flask built-in)
venv/bin/python main.py

# or exactly how production runs it
venv/bin/gunicorn -b :4030 -w 1 --threads 100 main:app
```

Open `http://localhost:4030`, log in, pick or create a table, take a seat, and (for a
solo test drive) visit `/admin/test` to seat three bots at that table.

> **Important:** every table lives in process memory, so gunicorn must run with
> **exactly one worker** (`-w 1`). Multiple workers would each have their own,
> different set of tables. Concurrency comes from threads (`--threads 100`).

Template, JS and CSS changes reach clients on a page refresh (auto-reload +
cache-busted URLs); only `.py` changes need a server restart.

## Multi-table

Any number of tables run concurrently in one process. A "table" is a `GameStateMachine`
instance whose `name` (e.g. `"TABLE 1"`) is simultaneously its registry key
(`game_state.tables: dict[str, GameStateMachine]`), its Socket.IO room name, and its
`data/tables/<name>/` save-directory name — deliberately no separate id. Names are
self-derived: `_next_table_name()` scans the registry for the highest existing number
and picks the next one (a reaped table's number is never reused).

Flow: login (shared passcode, table-agnostic) → a small server-rendered table picker
(`templates/choose_table.j2.html` — lists tables and occupants via `/api/tables`, polled
every 3s; create or join) → the single-page game client for the chosen table.
`session['table_id']` records the choice, but a connected socket is **pinned to the
table its page was rendered for**, not the live session: `TABLE_ID` is baked into the
page and sent on the `io({query: {table_id}})` handshake, re-sent unchanged on every
reconnect (this app is polling-transport-only with routine reconnect churn, not just
full page loads). The `connect` handler reads it from there and calls `join_room()`
before the first push. Socket **action** handlers (`seat_request` etc.) resolve their
target table from the socket's own joined room (`rooms()`) rather than the session too
— the same reasoning one layer up: a stale tab must never have its pushes pinned to one
table while its clicks silently act on another.

A full table can't be joined by a newcomer; someone already seated there can still
rejoin. Tables with no seated player for `TABLE_EMPTY_REAP_S` (300s) are removed
automatically via `game_state.delete_table()`, which also deletes the save directory;
a removed table's lingering spectator (if any) gets a dedicated `table_closed` socket
event, not a toast, since the room won't exist to push to afterwards — the client
redirects itself to `/`. `delete_table()` is shared with the admin-only
`/admin/delete_table` route (manual removal of any table, regardless of state — see
"Admin endpoints" below).

The single shared `ThreadedSchedule` worker (see "State machine mechanics" below)
serialises mutations across **every** table, deliberately not split per-table — simple
starting point, revisit only if one table's queued work ever visibly delays another's.

## Admin endpoints & test mode

Plain HTTP GET helpers. `admin/*` requires a logged-in session whose name is in
`auth.json`'s `admin_users`; `api/*` requires any logged-in session. The admin-only
section of the settings modal is the client-side front end for most of these, including
a table selector (`#admin-table-select`) that scopes the `?table=`-aware ones below to
any table, not just the admin's own (it only scopes the admin action — it doesn't move
the admin's own connected view to that table). The option list is server-rendered at page
load, then refreshed by `refreshAdminTableSelect()` (`/api/tables`) every time the
settings modal opens, same "re-ask on open" pattern as the uptime display — otherwise a
table created or deleted after page load would leave it stale. For the same reason, the
TEST MODE / SKIP DELAYS button labels read from that same `/api/tables` data for
whichever table is selected (falling back to the live `game_state` push only when the
selected table is this browser's own) rather than always showing this table's state:

| Endpoint                   | Effect                                                                                                                  |
| -------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| `/api/tables`              | JSON list of every table: name, state, seated players, test_mode/skip_delays, and (personalised to the caller) whether it's full / they're already seated there. |
| `/api/select_table`        | Join a table (`?id=<name>`). 403s if full and the caller isn't already seated there.                                    |
| `/api/create_table`        | Create a fresh table and select it, in one round trip.                                                                  |
| `/api/change_table`        | Back to the table picker. Only meaningful while WAITING FOR PLAYERS (properly vacates the seat then); mid-hand it's unreachable through the UI. |
| `/api/reinit`              | Re-initialise the **caller's own table** and clear its autosave (refused mid-deal). Any logged-in non-admin player, via the settings modal. Admins get `/admin/reinit` instead. |
| `/admin/reinit`            | Admin-only: re-initialise any table, chosen via `?table=`/`#admin-table-select` (falls back to the admin's own table). |
| `/admin/delete_table`      | Admin-only: permanently delete any table (`?table=`-aware) — deregisters it, deletes its save directory, tells any connected client via `table_closed`. Not gated on state — works mid-hand too. |
| `/admin/test`              | Toggle test mode on the target table (`?table=`-aware): seats three [dev-random bots](#player-bots) and lets them act on their turn. |
| `/admin/save` / `/admin/load` | Write / restore the target table's manual checkpoint (`?table=`-aware).                                              |
| `/dev/cards`                | Card-appearance review page: the card back plus all 43 faces in a scrollable grid, cloned from the same proto card as the game client (`templates/_proto_card.j2.html`). Still admin-gated (see `main.py`), but a development/QA tool rather than an admin-role concept, so it keeps the `dev/` path. Process-global, no table involved. |
| `/admin/clearchk`          | Delete the target table's manual checkpoint file (`?table=`-aware).                                                     |
| `/admin/skipdelays`        | Toggle skipping of all dramatic pauses on the target table (`?table=`-aware).                                           |
| `/admin/uptime`            | JSON: version, process start time, uptime in seconds, and whether a restart command is configured. Process-global.      |
| `/admin/restart`           | Run `RESTART_COMMAND` (see `main.py`) to restart the service. Autosaves every table first; each is restored on the way back up. Process-global. |
| `/api/client_trigger_push` | Force a full state push to the caller's own table only.                                                                 |
| `/api/last-game`           | JSON dump of games completed this process lifetime (in-memory), across every table.                                    |

There is no automated test suite. Verification is manual: run the server, enable test
mode and skip-delays, and watch the coloured logs — every state transition, dialog and
action is logged.

## Server/client contract

The server is authoritative; clients never compute game logic. A client:

1. Connects via Socket.IO (polling transport, `table_id` on the query string — see
   "Multi-table" above) and receives that table's full state.
2. Emits actions — `seat_request`, `bid_submit`, `discard_submit`, `play_card`,
   `joker_nominate`, `add_bots` — which `main.py` resolves to a target table (via the
   socket's own joined room, never the live session — see "Multi-table") and routes to
   the matching `gui_*` method on it (`add_bots` instead queues
   `bots.seat_player_bots`, and requires the requester to already be seated). A global
   `@socketio.on_error_default` handler catches any exception a socket handler raises
   (flask-socketio would otherwise swallow it silently), logs the traceback and toasts
   a SERVER ERROR — unscoped (every table), since the failure may have happened before
   any table could even be resolved.
3. Receives the **entire game state** on every `game_state` push, room-scoped to that
   table. There are no deltas. A keep-alive job (per table) re-pushes state if nothing
   was sent for 10 seconds.
4. May receive a `toast` event (`sio_toast(text, kind, seconds, audience, category,
   logit)`, a `GameStateMachine` method beside `sio_push()`, room-scoped to that table)
   — an occasional notice rendered as a top-right popup stack, coloured by `kind`, with
   a bold all-caps `category` heading above the message (one of SERVER ERROR / SERVER /
   GAME MANAGEMENT / PLAYERS, tinted to match the `kind`). Deliberately a separate
   event, not part of the `game_state` push (the keep-alive re-push would replay an
   embedded toast). `audience: "admin"` toasts are filtered client-side on the admin flag —
   cosmetic only, nothing sensitive rides in a toast; currently no call site uses it
   (every toast goes to everyone at that table) but the plumbing stays for future use.
   `logit=False` skips the server log line (used by the repeating nudge so it can't
   spam the log). Table-scoped call sites: checkpoint save/load/clear, table reinit,
   test-mode and skip-delays toggles, ADD BOTS, startup autosave-restore (deferred to
   the first socket connect — nobody is listening at startup), and a focus-idle nudge
   ("Are you there X?" via `check_focus_idle`, polled once a second, when a human holds
   focus for 10s+, repeating every 10s until they act). **Unscoped** (called directly
   on the `socketio` instance, not through any one table): worker-job and
   socket-handler error reporting, and service restart (affects every table at once).
5. May receive a `table_closed` event if its table was removed (empty-table reaping —
   see "Multi-table") — the client redirects itself to `/`, landing back on the table
   picker, since the room won't receive any further pushes.

Note: every client at a table receives every player's hand *at that table*. Hiding
opponents' cards is purely a client-side rendering concern — don't build anything
security-sensitive on top of this. (A genuinely hand-hiding "observer" viewer mode is
an idea in [ROADMAP.md](ROADMAP.md), not yet designed or built.)

## State machine mechanics

The six states and their transitions are diagrammed in the [Architecture](#architecture)
section above. Key mechanics for anyone working on the code:

- **All transitions go through `state_trans()`.** It inspects the current state, decides
  whether the conditions to move on are met, and performs the side effects of the
  transition. Don't move state anywhere else.
- **Single-threaded mutation via a job queue.** Long-running work (`auto_deal`,
  `auto_points`, `state_trans` itself) is never called directly — it is enqueued on
  `schedule_t.jobqueue` and executed by the single `ThreadedSchedule` worker thread.
  That one worker is what serialises all game mutations; keep it that way.
- **Pacing goes through `self.delay()`**, not bare `sleep()`, so the `skip_delays` admin
  flag can bypass every pause.
- **`player_focus`** (0–3 or `None`) is the single "whose turn is it" pointer used across
  bidding, discarding and play. `None` means nobody may act.
- **Teams are seat parity** — seats 0+2 vs 1+3; `teams[i % 2]` maps player to team.
- **Bidding endgame**: when only one active bidder remains, they get a single opportunity
  to raise their own bid before the contract locks. This is tracked with
  `bid.passed = "WINNER"` / `"WINNER_INCREASE_OPTION"` flags — game logic must key off
  these flags, never off dialog text.
- **Dialog text** shown to players is built exclusively in the `dlg_*` methods. Game
  logic must never depend on dialog strings.
- **Bid ordering is rank-based, following the points table.** `bid_rank()` (inside
  `gui_bid`) orders bids by tricks-then-suit, with misère (suit 0, tricks 10) slotted
  between 8 Spades (240 points) and 8 Clubs (260 points) — so outbidding a misère (250
  points) takes 8 Clubs or higher, matching the bid values table. Misère is also
  rejected outright unless the current high bid is a seven.
- **Misère adds no states** — it reuses the same six-state flow with three twists,
  all keyed off `trumps == 0`:
  - At contract lock (S2→S3), the contractor's partner's seat index goes into
    `self.sitting_out`. From then on they are skipped everywhere: turn rotation
    (`next_seat()`), trick size (3 cards), hand-completion checks
    (`hand_cards_status()`), and `legal_play_indices()` (returns `None` for them).
    `auto_deal` clears `sitting_out` for the next hand.
  - Play logic treats misère as No Trumps: `Deck.sort()` and the joker/bower handling
    map `trumps 0 → 5`; there are no bowers, the joker is the only "trump", and it
    **must** be played when its holder can't follow suit (unless it was pre-nominated
    into a suit — see below).
- **Joker leads in No Trumps / Misère need a suit nomination.** `gui_play()` intercepts
  the lead: the joker goes to the table, `joker_nominating` is set, and everyone's
  `legal_plays` is empty until the leader picks a suit (client shows a temporary top-right
  panel; `joker_nominate` socket event → `gui_joker_nominate()`). The chosen suit becomes
  `trick_suit` for the trick and is recorded in `suits_led` — a nomination must be a suit
  not yet led this hand (tracked at every lead), which also makes the joker unleadable
  once all four suits have been led, except to the last trick where no nomination is
  needed (everyone's final card falls anyway).
- **Joker pre-nomination (No Trumps / Misère).** A contractor holding the joker may
  declare its suit before the first lead (`joker_prenom_open` window, opened at the
  S3→S4 transition; same top-right panel and `joker_nominate` socket event, routed by
  `gui_joker_nominate()`). Declaring is optional — leading any card closes the window.
  Once declared (`joker_prenom`), the joker is the highest card of that suit for the
  whole hand: it must follow that suit, leading it leads that suit (no lead-time
  nomination), it wins any trick of its suit and wins nothing played off-suit — which
  lets a misère contractor discard it. The misère forced-joker rule no longer applies
  to a pre-nominated joker.
  - The S4→S5 transition fires early if the contractor has won any trick
    (checked in both `gui_play` and `state_trans`) — a failed misère never plays out
    the remaining tricks. Scoring in `auto_points` is ±250 for the contracting team
    and always 0 for the opponents, and it counts each side's actual won tricks
    because the hand may have ended early.
- **Game over**: a team at ±500 with scores unequal ends the game; the finished game is
  appended to the module-level `games` list (in-memory only) and the machine
  re-`__init__`s itself back to state 0.

## Persistence

Two JSON files **per table**, under `data/tables/<name>/` (gitignored, created on
demand — e.g. `data/tables/TABLE 1/autosave.json`), written atomically (temp file +
`os.replace`). `self.autosave_path`/`self.checkpoint_path` are set on each table by the
registry (`create_table()`/`load_tables_from_disk()`) as plain post-construction
attributes, not constructor params — same pattern as `self.socketio` — and excluded
from `to_dict()` (they're a deployment detail, not game data). A one-time boot migration
moves a legacy flat `data/autosave.json`/`checkpoint.json` into `data/tables/TABLE 1/`
if the new per-table layout doesn't exist yet, so an existing single-table deployment
keeps its live game across the upgrade.

- **`autosave.json`** — written at every safe checkpoint: each state transition, the end
  of scoring, after game-over re-init, and after every socket action. Loaded at startup,
  so every table survives restarts. `/api/reinit` (own table) / `/admin/reinit` (any
  table) deletes it.
- **`checkpoint.json`** — manual admin slot (`/admin/save` / `/admin/load`, both
  `?table=`-aware for admins).

`restore_state()` rebuilds `Deck`/`Card`/`dotdict` objects in place, rewrites the
autosave, then re-queues any pending automatic work: a game restored mid-DEALING
re-deals; restored in AWARD POINTS it either runs scoring (if it never ran) or moves on.
States driven by player input need nothing re-queued. A restore into AWARD KITTY also
re-derives `trumps` and (for misère) `sitting_out` from the bids, since the S2→S3
autosave fires before that setup runs. Newer fields (`sitting_out`, `suits_led`, the
joker-nomination and pre-nomination pairs) are read tolerantly (`data.get(...)`). A missing/corrupt/
wrong-version file logs the problem and leaves the fresh game untouched. Bump
`SAVE_VERSION` when changing the state shape (the multi-table persistence-layout change
itself didn't need this — only the file *location* moved, not the shape). Note
`restore_state()` deliberately never restores `self.name` from the save file's own
`data["name"]` — a table's name is set once by the registry at construction (it doubles
as the room/directory name) and must never drift from a restore.

## Card model

Numeric encodings, mirrored by the client:

- **Suits:** 0 = Misère, 1 = Spades, 2 = Clubs, 3 = Diamonds, 4 = Hearts, 5 = No Trumps.
- **Ranks:** 3–14 = card faces (11 = Jack, 14 = Ace), **15 = Joker** (stored suit 5).
- **Deck:** 43 cards — red suits 4–Ace, black suits 5–Ace, one joker.
- The left bower (jack of the same-colour suit as trumps) counts as a trump for
  following, leading and trick evaluation; `SUIT_LEFT_BOWER` holds the suit mapping.
  Follow-suit legality lives in `allowed_to_play()`; `legal_play_indices()` wraps it and
  is included in every state push.

## Naming conventions

All identifiers follow these rules. Fix non-compliant names when touching code; never
rename save-file keys without bumping `SAVE_VERSION` (old autosaves are then refused).

### Python (PEP 8)

| Thing | Style | Example |
| --- | --- | --- |
| Modules | `snake_case.py` | `game_state.py` |
| Classes | `PascalCase` | `GameStateMachine`, `PlayerBot` |
| Functions & methods | `snake_case()` | `legal_play_indices()` |
| Variables & parameters | `snake_case` | `trick_suit` |
| Constants (module- or class-level, never reassigned) | `UPPER_SNAKE` | `SAVE_VERSION`, `FOCUS_NUDGE_S` |
| Module-level mutable singletons | `snake_case` (not constants) | `game`, `socketio`, `schedule_t` |
| Internal helpers / private attributes | leading underscore | `_det01()`, `_focus_since` |
| Throwaway/unused values | bare `_` | `for _ in range(4)` |

Never shadow builtins or module names in parameters or locals (`str`, `time`, `id`).

**Documented exception:** the `dotdict` class stays lowercase — deliberate, it mimics
the `dict` builtin it subclasses.

**Domain prefixes (codified existing practice):** `gui_*` client-action handlers,
`auto_*` queued automatic phases, `dlg_*` dialog builders, `sio_*` socket emitters,
`handle_*` Socket.IO event handlers in `main.py`, `decide_*` bot decision methods,
`dev_random_*` dev-only bot code.

### JavaScript

| Thing | Style | Example |
| --- | --- | --- |
| Functions | `camelCase()` | `updateComponentVisibility()` |
| Local variables | `camelCase` | `vacantSeats`, `uptimeTimer` |
| jQuery-wrapped objects | `$` prefix + camelCase | `$toast` |
| Top-level constants | `UPPER_SNAKE` | `CONFIG`, `SUIT_DISP`, `SESSION_IS_ADMIN` |
| Server-pushed data properties | `snake_case`, never renamed client-side (they mirror Python attributes 1:1) | `data.legal_plays` |

The snake_case-property rule is the boundary: `data.joker_prenom` stays as-is when
accessing server state, but the moment it lands in a local variable the local is
camelCase (`var jokerPrenom = data.joker_prenom`). `var` vs `let`/`const` is a
modernisation question, not a naming one — out of scope here.

### HTML / CSS

| Thing | Style | Example |
| --- | --- | --- |
| Element ids | `kebab-case` | `#toast-stack`, `#joker-pane` |
| CSS classes | `kebab-case` | `.modal-btn-row` |
| data-attributes | `kebab-case` | `data-seat-index` |

### JSON / config / save files / wire protocol

| Thing | Style | Example |
| --- | --- | --- |
| JSON keys (auth.json, autosave/checkpoint, state pushes) | `snake_case`, mirroring the Python attribute exactly | `player_focus` |
| Socket.IO event names | `snake_case` | `seat_request` |
| localStorage keys | `web500_` prefix + snake_case | `web500_perfect_cards` |
