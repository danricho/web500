# Player bots

The design notes behind the bots seated by the lobby's ADD BOTS button (`bots.py`).
This document records the requirements and reasoning as the behaviour is derived —
keep it updated when bot behaviour discussions produce new rules.

### The one hard rule: no hidden information

A bot may only act on what a human in its seat could see: **its own cards plus public
information** (bids made, cards tabled now or earlier, trick winners, scores). The
server pushes every hand to every client, so this cannot be left to discipline — it is
enforced structurally. Every decision method receives a `build_view()` snapshot and
never the game object; the public history comes from per-hand event logs
(`bid_history`, `trick_history`, `current_trick`) recorded server-side. Any leak would
have to be added to `build_view()`, where a diff makes it obvious. A "permutation
test" (shuffle the hidden cards — neither the view nor the decision may change)
proves it in the headless suite.

### Bots should play like humans, not engines

Requirements gathered so far, and how each maps to a mechanism:

| Human behaviour | Mechanism |
| --- | --- |
| Imperfect memory of cards seen | Per-card recall decays per trick of age (`retention`); salient cards stick (joker never forgotten, bowers/aces/trumps sticky, low pips fade). Forgetting is a deterministic per-bot hash — once forgotten, stays forgotten, and re-fired checks recall identically _(live)_ |
| Conclusions outlive card memories | Void inferences ("seat 2 has no hearts") decay separately and much more slowly (`inference_hold`) than the cards that proved them _(live)_ |
| Reading bids as strength signals | Every genuine bid adjusts suit estimates: an opponent's shown suit is subtracted from ours; low abandoned bids read the same as fought-for ones _(live)_ |
| Partner's bids matter most | Partner's shown suit adds tricks weighted by `partner_trust` (plus a little for its same-colour twin — their jack is our left bower); enables raises into partner's suit _(live)_ |
| Low bids as suit _indications_ | With no genuine bid available, a bot may open a minimum 6-bid in a suit worth showing (`signaling`, per-hand deterministic draw; only as first action, partner silent, table below the sevens, suit estimate ≥ 2.5 tricks). Deliberately rare (`signaling` centre 0.18, capped 0.70) — an occasional habit, not the norm. Getting stuck with the contract is realistic and stays _(live)_ |
| Confidence varies by person | `confidence` biases the trick estimate in whole tricks (under- to over-bidder) _(live)_ |
| Random miscalculations | Play draws from a softmax over move scores (`error_temp`): obvious moves (big score gaps) stay near-certain, close calls sometimes go wrong; miscounting trumps falls out of memory decay for free _(live)_ |
| Everyone plays differently | Every parameter sampled per bot from `PERSONALITY_CONFIG` (tunable centre/spread, deliberately huge variance) _(live)_ |

### Bidding model (live)

Estimate own tricks per candidate contract — trump values including bowers and joker,
off-suit aces/guarded kings, ruff potential from short suits when holding 4+ trumps —
add a flat assumed partner contribution (until bid-reading lands) and the personality's
`confidence` bias. The suit is chosen by the biggest safety margin at the cheapest
level that outranks the table; the **level then rises toward the estimate** (a hand
read as 8 tricks opens at 8, not 6 — `int(est)` keeps a fractional-trick margin), else
pass. Bots only submit bids the server will accept (a local mirror of the server's bid
ranking), and bid misère only over a seven with a hand that expects to lose everything
(no joker, no ace/king).

The winner's self-raise option is almost always declined — raising your own contract
only adds risk. The one human-plausible exception (deliberately incredibly rare):
switching into the suit the **partner showed during the auction** (or its same-colour
twin, where partner's jack becomes our left bower), and only when the hand is worth a
full spare trick beyond the new commitment. A bot never cold-switches into an unshown
suit — a suit change with no reason reads as a bug to the humans at the table (and the
first version of exactly that _was_ a bug: a re-fired bot check let the winner outbid
its own contract; both the bot and `gui_bid` now guard against any action once the
auction is settled).

### Kitty discard model (live)

From the 13 cards, throw the 3 with the least keep-value: joker, trumps, bowers and
off-suit aces are never thrown; otherwise low beats high, and with 4+ trumps a
shortness bonus dumps mid-rank singletons/doubletons to manufacture voids — a short
hand means more chances to trump opposition leads. Misère inverts: throw the strongest
(the joker first — in misère it is forced to win when unable to follow).

### Card play model (live)

Every legal card gets an additive heuristic score; the bot draws from a softmax over
the scores (`error_temp` — score *gaps* are therefore meaningful: obvious moves score
far above alternatives and stay near-certain at any temperature, judgement calls sit
close together and sometimes go wrong). Knowledge feeding the scores is strictly what
a human could know — every card publicly tabled this hand, observed voids ("seat 2
didn't follow hearts"), an upper bound on trumps still out (kitty discards are
unknowable, same as for a human) — filtered through the bot's imperfect memory
(`retention` / `inference_hold`; see the table above).

The heuristics: contractors draw trumps from the top, but eagerness scales with how
many trumps can still be out, and once every opponent has been counted out or observed
void in trumps the bot moves off trumps entirely — remaining trumps are retained to
ruff off-suit tricks (any still "out" can only be the partner's); masters are cashed
unless a known-void opponent can still ruff; defenders lead their partner's bid suit
and almost never lead trumps back at the contractor (that just does the contractor's
trump-drawing for them — only a master trump is worth cashing); following, bots duck under a
winning partner, win as cheaply as possible in last seat, win high in earlier seats,
ruff when void, and otherwise discard the cheapest card from their shortest suit. In
misère the contractor stays under the trick at all costs and sheds its most dangerous
card whenever safe; defenders keep the contractor "on the hook" (biggest card that
still loses) once they're winning a trick, and keep tricks low before they've played.
The joker lead nomination picks the bot's shortest suit (draining opponents exactly
where the bot has least control).

### Status / phases

1. ✅ Infrastructure: view firewall, event logs, personalities, ADD BOTS, persistence
2. ✅ Bidding + kitty discard (above)
3. ✅ Play heuristics (above) — validated by a headless league: smart pair vs
   random-legal pair, 10/10 game wins; plus a hidden-card permutation test (shuffling
   the unseen hands changes neither the view nor the decision — no information leak)
4. ✅ Humanization (the table above) — memory decay, softmax miscalculations, signal
   bids, bid reading. Validated by knob-league (sharp/retentive team beats a
   deliberately degraded one) and units: joker never forgotten, recall deterministic,
   near-zero temperature reproduces argmax, huge score gaps survive high temperature,
   trust-gated raises into partner's suit
5. ✅ Think-time pacing: per-decision ranges (bids 1.5–4s, discard 3–6s, plays
   0.8–2.5s — also throttles the re-fired bot checks), scaled by the `tempo`
   personality (0.45× snappy to 2.2× deliberate), with an occasional ~8% long "tank"
   and a 12s hard cap so the table never wonders if the game hung. Pacing is the one
   place plain randomness is allowed — it is not a decision, so the determinism rule
   doesn't apply. A re-fired check with nothing to do (kitty already discarded,
   bidding settled) skips the sleep entirely — it used to sleep first and no-op
   after, blocking the game's own queued work on the single worker thread

All planned phases are complete. Future refinement ideas belong here: heuristic
calibration via longer league runs, personality presets/difficulty levels, smarter
joker pre-nomination (bots currently never pre-nominate — it's optional), and play
for/against Open Misère if that contract is ever implemented.
