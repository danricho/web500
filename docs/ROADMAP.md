# Web500 Roadmap

The single source of truth for planned work, ranked by value/effort, best bang-for-buck first.
Short version: everything here is opportunistic - nothing gates hosting.
Each item's summary line is the quick reference — the indented detail underneath
carries the diagnostics and fix shapes.

1. **Logging audit of a real game** — capture the full server log of a real game
   (humans + bots, start to game over), read it end to end, and turn what's found into
   a concrete list of log improvements. Cheap, and everything else about improving the
   logs depends on it.

   _Detail (easy, mostly reading):_ save the journal for one complete real game
   (`journalctl -u ... > game.log`), then walk it asking, per line: did this earn its
   place? Look for noise (keep-alive/push chatter, repeated poll output, anything
   fired once a second), missing signal (can every bid, play, transition and error be
   reconstructed without the client?), redundancy (same event logged at two layers),
   and readability (are the colour conventions and prefixes consistent; can a hand be
   followed by eye?). Output is not code — it's a ranked list of specific changes
   (drop/demote these lines, add these, reformat those) appended here as follow-up
   items.

1. **Change player without re-entering the passcode** — the session already proves the
   passcode was entered once; switching display name shouldn't demand it again. Small
   convenience win for shared/family devices.

   _Detail (easy, 1–2 hours, one design caveat):_ today "authenticated" simply means
   `session['username']` is set, so a name change currently requires the full login
   round-trip. Add an authed marker at login (e.g. `session['authed'] = True`), then a
   "CHANGE PLAYER" action in the settings modal that clears only the username and
   routes to a name-entry form which skips the passcode field when `authed` is set.
   The caveat: seats are keyed by name, and there is no un-sit mechanism — a seated
   player who changes name orphans their old seat until reinit. So either restrict the
   action to players not currently seated in a live hand, or present it as "switch to
   another (possibly seated) identity" — resuming an existing seat under its original
   spelling already works via the case-insensitive name match. Also exclude `B|`/`D|`
   prefixed names so nobody logs in as a bot.

1. **SVG-based card faces** — replace the HTML/CSS-built card faces with one
   parameterised SVG template (back, front, joker inside; settable index text, four
   suit options, big centre display, optional picture-card centres). Performance
   question already answered: not a factor.

   _Detail (medium; design settled, drawing work is the bulk):_ today a card is the
   shared proto markup (`templates/_proto_card.j2.html`, cloned by both the client
   and `/dev/cards`) filled by `updateArrayCards` (suit glyphs from `SUIT_SVG`
   paths, rank text) and styled by `cards.css` — which carries real fragility (the
   iOS Safari display-resolution quirk documented above `updateArrayCards`, the
   `rank-10` letter-spacing squeeze, the joker grid-stack). **Performance:** cloning
   inline SVG ≈ cloning HTML — same DOM machinery, same paint pipeline, and at this
   scale (~60 card elements worst case, 44 on `/dev/cards`) unmeasurable; with
   `<use>` refs instances get _cheaper_ than the current HTML (shared art lives once
   in a hidden `<defs>` sprite, shadow-tree content isn't duplicated per clone).
   **What it buys:** one `viewBox` scales the whole card as a unit (kills the
   `rank-10` squeeze, px font tuning, stays crisp under the phone `scale()`), and
   SVG internal layout doesn't depend on CSS display resolution (likely kills the
   iOS quirk). **Costs:** text positioning is manual (`x`/`y`/`text-anchor` —
   layout tweaks become coordinate edits), and picture-card centres are new drawing
   work (the CSS deck shows big rank text for J/Q/K). **Framework shape** — defs
   once, then per-card instances the JS fills exactly like today (class toggles +
   `.text()` + one `href` swap instead of an innerHTML glyph fill):

   ```html
   <!-- ONCE, hidden: shared art. Fills use currentColor so the card's
        suit-red/suit-black class colours everything via CSS `color:`.
        (CSS can't reach inside <use> shadow trees - currentColor/custom
        properties are the only styling that pierces them.) -->
   <svg width="0" height="0" style="position:absolute">
     <defs>
       <path id="suit-spade" fill="currentColor" d="..." />
       <path id="suit-club" fill="currentColor" d="..." />
       <path id="suit-diamond" fill="currentColor" d="..." />
       <path id="suit-heart" fill="currentColor" d="..." />
       <g id="pic-jack">...</g> <g id="pic-queen">...</g> <g id="pic-king">...</g>
       <g id="joker-art">...</g>
       <pattern id="back-pattern">...</pattern>
     </defs>
   </svg>

   <!-- PER CARD (the proto to clone), 120x180 like the CSS card -->
   <svg class="card" viewBox="0 0 120 180">
     <rect class="card-bg" width="120" height="180" rx="6" />
     <g class="front">
       <g class="index">
         <text class="rank" x="14" y="26" text-anchor="middle">Q</text>
         <use class="suit" href="#suit-heart" x="6" y="32" width="16" height="16" />
       </g>
       <!-- second index: same content, rotated about the card centre -->
       <g class="index index-flipped" transform="rotate(180 60 90)">
         <text class="rank" x="14" y="26" text-anchor="middle">Q</text>
         <use class="suit" href="#suit-heart" x="6" y="32" width="16" height="16" />
       </g>
       <g class="big">
         <text class="rank" x="60" y="108" text-anchor="middle">Q</text>
         <use class="picture" href="#pic-queen" style="display:none" />
       </g>
       <g class="joker" style="display:none"><use href="#joker-art" /></g>
     </g>
     <g class="back" style="display:none">
       <rect width="120" height="180" fill="url(#back-pattern)" />
     </g>
   </svg>
   ```

   `updateArrayCards` changes are mechanical and stay idempotent: `is-joker` /
   `suit-red` / `suit-black` class toggles unchanged, rank via
   `.find("text.rank").text(...)`, suit via
   `.find("use.suit").attr("href", "#suit-…")` (replacing the `SUIT_SVG` innerHTML
   fill), variant groups shown/hidden by class. jQuery `.clone()` of existing
   inline SVG nodes works fine (only _creating_ SVG from strings needs namespace
   care, which `appendSvg` already handles). Constraint: assets must be vendored
   (no CDN), matching the existing front-end policy — so CC0/public-domain art
   only (LGPL decks like Bellot's SVG-cards and Aguilar's vector-cards are out).
   Candidate court-card sources, all full-card SVGs needing a one-off extraction
   (strip border/indexes/pips, keep the centre group as `<g id="pic-…">`):
   [Dmitry Fomin's English pattern cards](https://commons.wikimedia.org/wiki/Category:SVG_English_pattern_playing_cards)
   (Wikimedia Commons, CC0, per-card files, clean flat vectors — easiest to gut),
   [RevK's SVG playing cards](https://www.me.uk/cards/) (CC0, Goodall & Son
   19th-century court designs, [generator source](https://github.com/revk/SVG-playing-cards) —
   best traditional look), and
   [Byron Knoll's vector cards](https://github.com/notpeter/Vector-Playing-Cards)
   (public domain, but potrace-vectorised scans — heavy paths, third choice).
   Note the courts are per rank × suit (12 unique centres, not 3) unless
   deliberately flattened to one generic J/Q/K each, and traditional court art is
   multi-colour — it keeps its own fills in `<defs>` rather than riding the
   `currentColor` red/black scheme. Validation is easy — `/dev/cards` shows all
   43 faces + back at once. Touches `_proto_card.j2.html`, `cards.css`,
   `updateArrayCards` and `/dev/cards`.

1. **State-change refactor** — cheap but zero player value; half stale already. Finish
   cheaply or close.

   _Detail (medium):_ partly stale: `move_state()` already exists. Remaining work is
   thinning repetitive debug/dialog blocks per branch of `state_trans()`.

1. **Let a lone winning bidder resign instead of contracting** — when everyone else has
   passed, offer "resign" alongside the increase option, so a regretted bid needn't be
   played out. Contained change, but needs a scoring decision before any code.

   _Detail (medium, needs a rules decision first):_ the hook already exists — once only
   one bidder remains, `gui_bid()` flags them `bid.passed = "WINNER_INCREASE_OPTION"`
   and `dlg_bid_increase_option()` offers increase-or-pass. Resign becomes a third
   choice at that same moment, before the kitty is seen. The open question is scoring,
   and it must be settled first: does the would-be contractor lose the bid value, do
   the opponents score anything, or is it a plain re-deal (with or without a penalty)?
   Check the pagat "Australian Four-handed Five Hundred" section for a sanctioned
   variant rather than inventing one — and note the bidder here has not yet seen the
   kitty, so this is regret at the bid, not at the hand. Touches: `gui_bid()` (a new
   flag value beside `"WINNER"` / `"WINNER_INCREASE_OPTION"`), the S2 branch of
   `state_trans()` (route to re-deal or scoring instead of AWARD KITTY), a new `dlg_*`
   builder, the bidding box UI, and the rules modal.

1. **Observer/spectator mode** — an explicit "just watching" option that only ever
   sees table cards, bids and the public event log — never any player's hand, even a
   seated player's own. Distinct from today's implicit spectating (anyone unseated
   already sees the full push, hands included) - a genuine hand-hiding mode needs
   server-side filtering, not just client-side hiding.

   _Detail (medium, needs a design decision first):_ today "every client receives every
   player's hand — hiding opponents' cards is a client-side rendering concern, not a
   server one" (see CLAUDE.md) is fine for players (each has a real reason to trust the
   others), but an observer with the raw payload could trivially inspect every hand in
   the browser console. That means `to_dict()`/`sio_push()` can no longer send one
   identical payload to the whole room — an observer's push must have every
   `players[i].hand` (and arguably `kitty`) nulled out server-side before it leaves the
   process, while seated players keep receiving their own full state. Needs a design
   decision on the shape: per-viewer filtering at push time (one `to_dict()` per
   audience — "own hand" vs "hidden") is the honest fix but touches the core
   `sio_push()` broadcast path meaningfully; a cheaper first cut could restrict observer
   status to spectators only (never a seated player choosing to self-blind) and filter
   just their own connection's pushes. Touches: `to_dict()`/`sio_push()` (per-audience
   payload), the lobby/table-picker UI (an explicit "observe" choice, not just staying
   unseated), and probably a per-connection flag alongside `session['table_id']`.

1. **Save/restore idle windows** — saves written between "action queued work" and
   "worker ran it" restore into a stuck game; several known windows, all rare races.
   Low priority.

   _Detail (medium, touches fragile spots):_ root cause: `gui_*` actions run on socket
   handler threads and autosave right after each action, but the follow-up work
   (`state_trans`) sits on the job queue, and **queued-but-unexecuted jobs are not part
   of the save**. A save written in that gap restores into a state whose trigger has
   evaporated. Known windows:
   - **WAITING FOR PLAYERS** with all 4 seats named (4th `gui_sit` autosaves before
     `state_trans` runs) — stuck; re-seating won't re-trigger.
   - **TAKING BIDS** with all players passed (re-deal pending) — stuck, focus `None`.
   - **TAKING BIDS** with a `bid.passed == "WINNER"` flag (kitty award pending) — stuck.
   - **PLAY HAND** mid-trick with every active player's table card set (trick
     evaluation pending) — stuck; the original headline case.
   - **PLAY HAND** hand-complete / misère-failed (scoring transition pending) — stuck.
   - **PLAY HAND** between tricks during the won-trick pause, if a _concurrent_ action
     autosaves — stuck and unrecoverable: the trick winner exists only in a local
     variable, so no redo can know who leads.
   - **AWARD POINTS** mid-dialog via a concurrent save — not idle but worse: restore
     re-queues `auto_points` and applies the scores twice.

   Fix shapes: defensively queue `state_trans` on restore into S0 / TAKING BIDS /
   PLAY HAND (it no-ops when nothing is due) + a trick-evaluation redo for the full
   table; set `player_focus` to the trick winner _before_ the won-trick pause (or
   persist a `last_trick_winner`); clear/flag bids immediately after scores are applied
   in `auto_points`.

1. **Save game transcript** — record who played what in what order per trick plus all
   bid events (probably timestamped), persisted per game, served as an HTML table at
   an endpoint.

   _Detail (medium, substrate exists):_ the public event logs already capture exactly
   the right events in order — `bid_history` (every bid/pass with seat) and
   `trick_history` (every card with seat, per trick) were built for the bots — but they
   reset each deal (`auto_deal`) and aren't timestamped. Needed: add timestamps at
   append time, accumulate per-hand snapshots across the game (plus the contract and
   points outcome per hand), persist to disk on game end (reuse the `save_state`
   atomic-write pattern; finished games currently only append to the in-memory `games`
   list dumped by `/api/last-game`), and render an HTML table endpoint (hand → bids →
   tricks in play order, seat names resolved).

1. **Open Misère** — Misère logic now exists; the blocker is UI: no known way to show an
   exposed hand while staying usable on phones. Last.

   _Detail (hard, needs a design first):_ rules are Misère with the contractor's hand
   played face-up on the table (scores 500; check pagat for its exact bid ranking). The
   Misère engine now exists; the blocker is UI, not logic: no obvious way to display a
   fifth, exposed hand that stays usable on phone screens (portrait or landscape).
   Needs a design idea before any code.

1. **Player presence / disconnect notices (maybe, one day)** — toast "X lost
   connection" / "X is back online" and a per-seat indicator. Built once and
   deliberately reverted (2026-07-19): under the polling transport, disconnect
   detection was unreliable enough that fixing it (ping tuning, pagehide close,
   heartbeats) cost more complexity than a friendly table game justifies.

   _Detail (medium, design exists):_ the shape that worked: per-name socket-sid sets
   (multi-device players never false-alarm), only the last client dropping starts a
   ~30s debounce, only seated humans announced, "back online" only after an announced
   loss, per-player `disconnected` flag riding the state push for a chip icon.
   Revisit only if the transport story changes (e.g. websockets) or players actually
   ask for it.

