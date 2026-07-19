# BOT PLAYERS - SEPARATE FROM THE GAME RULES IN game_state.py. BOTS ACT THROUGH THE SAME
# gui_* METHODS AS HUMAN CLIENTS, SO THE GAME NEVER TREATS THEM SPECIALLY BEYOND THEIR
# NAMES. TWO KINDS LIVE HERE:
#
# 1. dev_random_* - THE DEV TEST-MODE BOT ONLY: DELIBERATELY DUMB AND PURELY RANDOM,
#    USED TO EXERCISE THE FULL GAME FLOW (PASSES EVERY BID, THROWS BACK THE KITTY,
#    PLAYS A RANDOM LEGAL CARD). NONE OF IT IS MEANT FOR REUSE BY THE PLAYER BOTS -
#    KEEP ITS BEHAVIOUR STABLE: IT IS THE REGRESSION HARNESS FOR RULE CHANGES.
#
# 2. PlayerBot - THE "SMART" OPPONENT SEATED VIA THE LOBBY'S ADD BOTS BUTTON. DESIGNED
#    AROUND ONE HARD RULE: A BOT MAY ONLY EVER SEE WHAT A HUMAN IN ITS SEAT COULD SEE
#    (OWN CARDS + PUBLIC INFORMATION). THAT IS ENFORCED ARCHITECTURALLY - EVERY DECISION
#    METHOD RECEIVES A build_view() SNAPSHOT AND NEVER THE GAME OBJECT, SO "CHEATING"
#    WOULD BE VISIBLE AS A view-BUILDING CHANGE IN A DIFF, NOT BURIED IN LOGIC.

from random import choice, sample, gauss, getrandbits, uniform
from math import exp

# SHARED BY BOTH BOT KINDS. SEATED NAMES CARRY A KIND PREFIX - THE PREFIX ALONE TELLS
# THE TWO KINDS APART (AND ROUTES THE bot_check DISPATCHER):
#   DEV TEST-MODE BOT: "D|CLUNKER" (NAME FORCED UPPERCASE - VISUALLY LOUD IN UI/LOGS)
#   PLAYER BOT:        "B|Clunker" (NAME AS WRITTEN IN THE LIST)
BOT_NAMES = ["Clunker", "Toaster", "Glitch", "Patchy", "Fumble", "Rusty", "Reboot", "Wobble", "Flaky", "Dented", "Buffer", "Laggy", "Crash", "Buggy", "Misfire"]
DEV_RANDOM_BOT_PREFIX = "D|"
PLAYER_BOT_PREFIX = "B|"

# TRUE IF THIS SEATED NAME IS ONE OF THE DEV RANDOM BOTS (PREFIX + KNOWN BASE NAME,
# COMPARED CASE-INSENSITIVELY SINCE THE SEATED FORM IS UPPERCASED)
def is_dev_random_bot(name):
  if name is None or not str(name).startswith(DEV_RANDOM_BOT_PREFIX):
    return False
  base = str(name)[len(DEV_RANDOM_BOT_PREFIX):]
  return any(_same_name(base, n) for n in BOT_NAMES)

# DEV: SEATS RANDOMLY-CHOSEN DEV BOTS IN EMPTY SEATS - QUEUED WHEN TEST MODE IS ENABLED
def dev_random_seat_bots(game):
  if game.test_mode and game.state_name() == "WAITING FOR PLAYERS":
    seated = [p.name for p in game.players]
    available = [DEV_RANDOM_BOT_PREFIX + name.upper() for name in BOT_NAMES
                 if DEV_RANDOM_BOT_PREFIX + name.upper() not in seated]
    unseated_bots = sample(available, len(available)) # random order, no repeats
    for seat in range(len(game.players)):
      if game.players[seat].name == None and len(unseated_bots):
        game.delay(1)
        game.gui_sit(unseated_bots.pop(0), seat)

# DEV: PERFORMS ONE PURELY RANDOM ACTION FOR THE FOCUSED DEV BOT - QUEUED BY sio_push IN TEST MODE
def dev_random_bot_check(game):
  if not game.test_mode or game.player_focus == None:
    return
  player = game.players[game.player_focus]
  if not is_dev_random_bot(player.name):
    return
  game.delay(1)

  if game.state_name() == "TAKING BIDS":
    game.gui_bid(player.name, {"pass": True, "suit": 1, "tricks": 6})

  elif game.state_name() == "AWARD KITTY":
    game.gui_discard(player.name, {"kitty": [0, 1, 2], "hand": []}) # throw back the kitty cards

  elif game.state_name() == "PLAY HAND":
    if game.joker_nominating:
      game.gui_joker_nominate(player.name, choice(game.joker_lead_suits())) # random available suit
      return
    legal = game.legal_play_indices(game.player_focus)
    if legal:
      game.gui_play(player.name, choice(legal)) # random legal card, not the bot's best


# ---------------------------------------------------------------------------
# PLAYER BOTS ("SMART" OPPONENTS) - SEATED VIA THE LOBBY'S ADD BOTS BUTTON
# ---------------------------------------------------------------------------

# ---- PLAYER BOT PERSONALITY CONFIG ----
# EACH BOT SAMPLES ITS PERSONALITY ONCE AT CREATION: value = clamp(gauss(centre, spread)).
# TUNE CENTRES/SPREADS HERE - LARGE SPREADS ARE INTENDED: BOTS SHOULD FEEL VERY
# DIFFERENT FROM EACH OTHER (ONE NEAR-PERFECT AND CAUTIOUS, THE NEXT FORGETFUL AND
# RECKLESS). THE PARAMETERS ARE SAMPLED AND PERSISTED NOW (SO SAVES AND TESTS SEE THE
# FINAL SHAPE) BUT ONLY START DRIVING DECISIONS AS THE PHASES LAND:
#   retention/inference_hold -> imperfect memory (phase 4)
#   confidence               -> bid over/under-estimation (phase 2/4)
#   error_temp               -> softmax play mistakes (phase 4)
#   signaling                -> low "indication" bids in the strongest suit (phase 4)
#   partner_trust            -> weight given to partner's bid signals (phase 4)
PERSONALITY_CONFIG = {
  #                  centre  spread    lo    hi
  "retention":      (0.85,   0.25,   0.30, 1.00), # CARD-MEMORY KEEP RATE PER TRICK
  "inference_hold": (0.95,   0.15,   0.50, 1.00), # MEMORY FOR VOIDS / OUT-OF-TRUMPS CONCLUSIONS
  "confidence":     (0.00,   1.50,  -3.00, 3.00), # BID BIAS IN TRICKS: -UNDER / +OVER ESTIMATE
  "error_temp":     (0.60,   0.50,   0.05, 2.00), # SOFTMAX TEMPERATURE FOR PLAY MISTAKES
  "signaling":      (0.18,   0.25,   0.00, 0.70), # TENDENCY TO MAKE INDICATION BIDS (KEPT RARE)
  "partner_trust":  (0.75,   0.30,   0.20, 1.00), # WEIGHT GIVEN TO PARTNER'S BID SIGNALS
  "tempo":          (1.00,   0.35,   0.45, 2.20), # THINK-TIME MULTIPLIER: SNAPPY vs DELIBERATE
}

# ONE PERSONALITY DICT, FRESHLY SAMPLED. "seed" IS RESERVED FOR SEEDED/REPRODUCIBLE
# DECISIONS LATER (DETERMINISTIC REPLAYS IN TESTS); UNUSED BY THE PHASE-1 SKELETON.
def sample_personality():
  personality = {}
  for param, (centre, spread, lo, hi) in PERSONALITY_CONFIG.items():
    personality[param] = round(min(hi, max(lo, gauss(centre, spread))), 3)
  personality["seed"] = getrandbits(32)
  return personality

# LOCAL COPY OF game_state.same_name (CASE-INSENSITIVE NAME COMPARE). DUPLICATED HERE
# BECAUSE game_state IMPORTS THIS MODULE AT ITS TOP - IMPORTING BACK WOULD BE CIRCULAR.
def _same_name(a, b):
  return a != None and b != None and str(a).casefold() == str(b).casefold()

# ---------------------------------------------------------------------------
# HAND EVALUATION (PHASE 2) - ALL PURE FUNCTIONS ON (suit, rank) TUPLE LISTS SO THEY
# ARE TRIVIALLY UNIT-TESTABLE AND CANNOT TOUCH GAME STATE. ENCODINGS MATCH
# playing_cards.py: SUITS 1=S 2=C 3=D 4=H (5,15)=JOKER; RANK 11=JACK, 14=ACE.
# ---------------------------------------------------------------------------

SUIT_LEFT_BOWER = {1: 2, 2: 1, 3: 4, 4: 3} # SAME-COLOUR SUIT WHOSE JACK JOINS TRUMPS

# EXPECTED-TRICK VALUES. DELIBERATELY ROUGH: THESE ONLY NEED TO RANK HANDS WELL ENOUGH
# TO PICK SANE BIDS - PLAY QUALITY COMES FROM PHASE 3, CALIBRATION FROM LEAGUE RUNS.
TRUMP_VAL = {"joker": 1.0, "right": 0.95, "left": 0.9, 14: 0.85, 13: 0.7, 12: 0.55}
TRUMP_VAL_SMALL = 0.45   # ANY LOWER TRUMP: WINS LATE TRICKS THROUGH LENGTH
OFF_ACE_VAL = 0.85
OFF_KING_VAL = 0.35      # ONLY WHEN GUARDED (>=2 IN SUIT) - A BARE KING USUALLY FALLS
PARTNER_BASE_TRICKS = 1.5 # FLAT ASSUMED PARTNER CONTRIBUTION UNTIL BID-READING (PHASE 4)

# SPLITS A HAND FOR A CANDIDATE TRUMP SUIT: WHICH CARDS ACT AS TRUMPS (JOKER + BOTH
# BOWERS INCLUDED, LEFT BOWER LEAVES ITS PRINTED SUIT) AND WHAT REMAINS PER OFF-SUIT
def _split_for_trumps(hand, trumps):
  trump_cards, offsuit = [], {s: [] for s in (1, 2, 3, 4) if s != trumps}
  for (suit, rank) in hand:
    if rank == 15:
      trump_cards.append("joker")
    elif rank == 11 and suit == trumps:
      trump_cards.append("right")
    elif rank == 11 and suit == SUIT_LEFT_BOWER.get(trumps):
      trump_cards.append("left")
    elif suit == trumps:
      trump_cards.append(rank)
    else:
      offsuit[suit].append(rank)
  return trump_cards, offsuit

# EXPECTED TRICKS FOR OWN HAND IF suit (1-4) WERE TRUMPS - EXCLUDES PARTNER
def estimate_tricks(hand, trumps):
  trump_cards, offsuit = _split_for_trumps(hand, trumps)
  est = sum(TRUMP_VAL.get(c, TRUMP_VAL_SMALL) for c in trump_cards)
  for suit, ranks in offsuit.items():
    if 14 in ranks:
      est += OFF_ACE_VAL
    if 13 in ranks and len(ranks) >= 2:
      est += OFF_KING_VAL
    # SHORT OFF-SUITS ARE WORTH RUFFS, BUT ONLY WITH ENOUGH TRUMPS TO SPARE
    if len(trump_cards) >= 4:
      if len(ranks) == 0:
        est += 0.5
      elif len(ranks) == 1:
        est += 0.25
  return est

# EXPECTED TRICKS FOR A NO-TRUMPS CONTRACT - ONLY HIGH CARDS AND LONG SUITS SCORE
# (NO BOWERS IN NO TRUMPS; THE JOKER IS THE ONE SURE TRICK)
def estimate_tricks_nt(hand):
  est = 0.0
  by_suit = {s: sorted((r for (su, r) in hand if su == s and r != 15), reverse=True) for s in (1, 2, 3, 4)}
  if any(r == 15 for (_, r) in hand):
    est += 1.0
  for suit, ranks in by_suit.items():
    if 14 in ranks:
      est += 0.9
    if 13 in ranks and len(ranks) >= 2:
      est += 0.5
    if len(ranks) >= 5: # A LONG SUIT RUNS ONCE THE TOP CARDS CLEAR IT
      est += 0.5 * (len(ranks) - 4)
  return est

# TRUE IF THE HAND LOOKS LIKE A MISÈRE (LOSE EVERY TRICK). VERY CONSERVATIVE: THE JOKER
# IS DISQUALIFYING (IN MISÈRE IT IS FORCED TO WIN WHEN UNABLE TO FOLLOW; THE PRE-NOMINATION
# ESCAPE ISN'T PLAYED BY BOTS UNTIL PHASE 4), AS IS ANY ACE/KING OR A SECOND QUEEN
def looks_like_misere(hand):
  ranks = [r for (_, r) in hand]
  return (15 not in ranks and 14 not in ranks and 13 not in ranks
          and ranks.count(12) <= 1)

# ---------------------------------------------------------------------------
# TRICK EVALUATION + TABLE KNOWLEDGE (PHASE 3) - PURE FUNCTIONS ON THE VIEW.
# "KNOWLEDGE" HERE IS PERFECT RECALL OF PUBLIC INFORMATION ONLY (EVERY CARD EVER
# TABLED + OBSERVED FAILURES TO FOLLOW SUIT); PHASE 4 DEGRADES IT THROUGH THE
# PERSONALITY'S MEMORY PARAMETERS. NOTHING HERE MAY READ HIDDEN STATE.
# ---------------------------------------------------------------------------

# THE FULL 43-CARD DECK AS (suit, rank) TUPLES - WHAT "ALL THE TRUMPS" IS COUNTED FROM
ALL_CARDS = ([(s, r) for s in (3, 4) for r in range(4, 15)]     # RED SUITS 4-ACE
             + [(s, r) for s in (1, 2) for r in range(5, 15)]   # BLACK SUITS 5-ACE
             + [(5, 15)])                                        # JOKER

# EFFECTIVE SUIT FOR FOLLOWING/WINNING - MIRRORS THE SERVER'S eff_suit: THE JOKER IS
# TRUMPS (OR ITS PRE-NOMINATED SUIT), THE LEFT BOWER BELONGS TO TRUMPS. trumps IS THE
# EFFECTIVE CONTRACT SUIT (MISÈRE ALREADY MAPPED TO 5)
def eff_suit(card, trumps, joker_prenom=None):
  suit, rank = card
  if rank == 15:
    return joker_prenom if joker_prenom else trumps
  if rank == 11 and suit == SUIT_LEFT_BOWER.get(trumps):
    return trumps
  return card[0]

# ABSOLUTE "PRECIOUSNESS" OF A CARD UNDER THE CONTRACT - USED FOR "DON'T WASTE HIGH
# CARDS" / "SHED DANGEROUS CARDS" DECISIONS. STRICTLY MONOTONE IN STRENGTH:
# JOKER > RIGHT BOWER > LEFT BOWER > OTHER TRUMPS BY RANK > PLAIN CARDS BY RANK
def card_strength(card, trumps, joker_prenom=None):
  suit, rank = card
  if rank == 15:
    return 15 if joker_prenom else 37 # PRE-NOMINATED: MERELY THE TOP CARD OF A PLAIN SUIT
  if trumps in (1, 2, 3, 4):
    if rank == 11 and suit == trumps:
      return 36
    if rank == 11 and suit == SUIT_LEFT_BOWER.get(trumps):
      return 35
    if suit == trumps:
      return 20 + rank
  return rank

# WHICH CARD (INDEX INTO cards, PLAY ORDER) IS WINNING THE TRICK SO FAR - SAME WALK
# THE SERVER DOES: BEST TRUMP IF ANY, ELSE BEST CARD OF THE LED SUIT
def trick_winner(cards, trick_suit, trumps, joker_prenom=None):
  best_i = None
  best_key = (-1, -1)
  for i, card in enumerate(cards):
    es = eff_suit(card, trumps, joker_prenom)
    # es == trumps COVERS NO-TRUMPS TOO: THE UN-NOMINATED JOKER'S EFFECTIVE SUIT IS 5
    # THERE AND IT MUST STILL BEAT EVERYTHING (SERVER DOES THE SAME VIA eff_trumps)
    if es == trumps:
      tier = 2
    elif es == trick_suit:
      tier = 1
    else:
      continue # OFF-SUIT NON-TRUMP CAN NEVER WIN
    key = (tier, card_strength(card, trumps, joker_prenom))
    if key > best_key:
      best_key, best_i = key, i
  return best_i

# EVERY CARD PUBLICLY TABLED THIS HAND (COMPLETED TRICKS + THE TRICK IN PROGRESS)
def cards_seen(view):
  seen = []
  for trick in view["trick_history"]:
    seen += [(c["suit"], c["rank"]) for c in trick["cards"]]
  seen += [(c["suit"], c["rank"]) for c in view["current_trick"]]
  return seen

# ---------------------------------------------------------------------------
# IMPERFECT MEMORY (PHASE 4). HUMANS DON'T RECALL EVERY TABLED CARD - MEMORY DECAYS
# WITH AGE, BUT SALIENT CARDS (JOKER, BOWERS, ACES, TRUMPS) STICK. ALL "RANDOMNESS"
# IS A DETERMINISTIC HASH OF (BOT SEED, FACT, WHEN) SO A RE-FIRED CHECK OR A REPLAYED
# TEST GETS IDENTICAL RECALL, AND A FACT ONCE FORGOTTEN STAYS FORGOTTEN (THE KEEP
# PROBABILITY ONLY SHRINKS WITH AGE WHILE THE DRAW STAYS FIXED - NO FLICKER).
# ---------------------------------------------------------------------------

# DETERMINISTIC "RANDOM" IN [0,1) FROM INTS/STRINGS (INT/TUPLE HASHES ARE STABLE,
# UNLIKE str HASHES - KEEP STRINGS OUT OF PRODUCTION KEYS OR ACCEPT PER-PROCESS SALT)
def _det01(*key):
  return (hash(key) % 100003) / 100003.0

# HOW STICKY A TABLED CARD IS, 0..1 - A PERMANENT RECALL FLOOR REGARDLESS OF AGE.
# EVERYONE REMEMBERS THE JOKER FALLING; NOBODY REMEMBERS THE 6 OF CLUBS
def _salience(card, trumps):
  suit, rank = card
  if rank == 15:
    return 1.0 # THE JOKER IS NEVER FORGOTTEN
  if trumps in (1, 2, 3, 4) and rank == 11 and suit in (trumps, SUIT_LEFT_BOWER.get(trumps)):
    return 0.85 # BOWERS
  if rank == 14:
    return 0.7 # ACES
  if trumps in (1, 2, 3, 4) and suit == trumps:
    return 0.5 # PLAIN TRUMPS
  if rank in (12, 13):
    return 0.25 # QUEENS/KINGS
  return 0.0 # LOW PIPS FADE FAST

# THE SUBSET OF PUBLICLY TABLED CARDS THIS BOT STILL REMEMBERS. THE TRICK IN PROGRESS
# IS ALWAYS RECALLED (IT'S ON THE TABLE RIGHT NOW). retention IS THE PER-TRICK-OF-AGE
# KEEP RATE; keep = max(retention^age, salience) AGAINST A FIXED PER-FACT DRAW.
# FORGOTTEN CARDS SIMPLY VANISH FROM THE COUNTS - WHICH IS EXACTLY HOW A HUMAN
# MISCOUNTS TRUMPS BY ONE AND DRAWS A ROUND TOO MANY
def remembered_cards(view, personality):
  t = 5 if view["trumps"] in (0, 5, None) else view["trumps"]
  seed, retention = personality["seed"], personality["retention"]
  now = len(view["trick_history"])
  seen = []
  for trick_i, trick in enumerate(view["trick_history"]):
    age = now - trick_i
    for c in trick["cards"]:
      card = (c["suit"], c["rank"])
      keep = max(retention ** age, _salience(card, t))
      if _det01(seed, 1, card[0], card[1], trick_i) < keep:
        seen.append(card)
  seen += [(c["suit"], c["rank"]) for c in view["current_trick"]]
  return seen

# TRUMPS THE THREE OTHER SEATS MIGHT STILL HOLD: ALL TRUMP CARDS OF THE CONTRACT MINUS
# THE TABLED ONES MINUS MY OWN. (SOME MAY BE BURIED IN THE KITTY DISCARDS - UNKNOWABLE,
# SO THE COUNT IS AN UPPER BOUND, EXACTLY AS IT IS FOR A HUMAN.)
def trumps_still_out(view, trumps, seen=None):
  if trumps not in (1, 2, 3, 4):
    return 0
  accounted = (seen if seen != None else cards_seen(view)) + view["hand"]
  return sum(1 for c in ALL_CARDS
             if eff_suit(c, trumps) == trumps and c not in accounted)

# OBSERVED VOIDS: seat -> SET OF SUITS THEY FAILED TO FOLLOW. THE SAME CONCLUSION A
# HUMAN DRAWS AT THE TABLE. (THE LEFT-BOWER-ONLY ESCAPE CAN FOOL THIS - IT FOOLS
# HUMANS TOO, AND IT IS RARE.)
# personality=None MEANS PERFECT RECALL; OTHERWISE VOID CONCLUSIONS DECAY WITH
# inference_hold - MUCH SLOWER THAN CARD MEMORY, BECAUSE HUMANS KEEP THE CONCLUSION
# ("SHE HAS NO HEARTS") LONG AFTER FORGETTING THE CARDS THAT PROVED IT
def observed_voids(view, trumps, joker_prenom=None, personality=None):
  voids = {0: set(), 1: set(), 2: set(), 3: set()}
  now = len(view["trick_history"])
  tricks = view["trick_history"] + ([{"cards": view["current_trick"],
                                      "trick_suit": view["trick_suit"]}]
                                    if view["current_trick"] else [])
  for trick_i, trick in enumerate(tricks):
    led = trick.get("trick_suit")
    if led not in (1, 2, 3, 4):
      continue
    for played in trick["cards"][1:]: # THE LEADER SET THE SUIT, ONLY FOLLOWERS TELL
      if eff_suit((played["suit"], played["rank"]), trumps, joker_prenom) != led:
        if personality != None:
          age = now - trick_i # 0 FOR THE TRICK IN PROGRESS
          hold = personality["inference_hold"] ** max(age, 0)
          if _det01(personality["seed"], 2, played["seat"], led) >= hold:
            continue # THE CONCLUSION HAS FADED
        voids[played["seat"]].add(led)
  return voids

# THE HIGHEST UNSEEN CARD OF A PLAIN SUIT STILL OUTSIDE MY HAND - FOR "IS THIS CARD
# THE MASTER OF ITS SUIT" CHECKS WHEN CONSIDERING CASHING IT
def is_master_of_suit(card, view, trumps, joker_prenom=None, seen=None):
  accounted = (seen if seen != None else cards_seen(view)) + view["hand"]
  mine = card_strength(card, trumps, joker_prenom)
  es = eff_suit(card, trumps, joker_prenom)
  for other in ALL_CARDS:
    if other in accounted:
      continue
    if eff_suit(other, trumps, joker_prenom) == es and card_strength(other, trumps, joker_prenom) > mine:
      return False
  return True

# PARTNER'S LAST GENUINELY BID SUIT THIS AUCTION (1-4), OR None - THE DEFENDERS' "LEAD
# YOUR PARTNER'S SUIT" CONVENTION
def partner_shown_suit(view):
  partner = (view["seat"] + 2) % 4
  suits = [b["suit"] for b in view["bid_history"]
           if b["seat"] == partner and not b["pass"] and b["suit"] in (1, 2, 3, 4)]
  return suits[-1] if suits else None

# ---------------------------------------------------------------------------
# MOVE SCORING (PHASE 3) - ONE ADDITIVE SCORE PER LEGAL CARD. THE CONSTANTS ONLY NEED
# TO ORDER MOVES SENSIBLY; ABSOLUTE VALUES ARE MEANINGLESS. PHASE 4 DRAWS FROM A
# SOFTMAX OVER THESE SCORES (error_temp), SO KEEPING SCORE *GAPS* MEANINGFUL MATTERS:
# OBVIOUS MOVES SHOULD SCORE FAR ABOVE ALTERNATIVES, JUDGEMENT CALLS CLOSE TOGETHER.
# ---------------------------------------------------------------------------
def score_moves(view, personality=None):
  is_misere = view["trumps"] == 0
  t = 5 if view["trumps"] in (0, 5) else view["trumps"] # EFFECTIVE TRUMPS FOR PLAY LOGIC
  prenom = view["joker_prenom"]
  hand, seat = view["hand"], view["seat"]
  partner = (seat + 2) % 4
  table = view["current_trick"]
  table_cards = [(c["suit"], c["rank"]) for c in table]
  leading = len(table) == 0
  trick_size = 3 if view["sitting_out"] != None else 4
  last_to_play = len(table) == trick_size - 1

  contractor = next((i for i, b in enumerate(view["bids"]) if b["passed"] == "WINNER"), None)
  on_contract_team = contractor != None and contractor % 2 == seat % 2
  opponents = [s for s in range(4) if s % 2 != seat % 2 and s != view["sitting_out"]]

  # WITH A PERSONALITY, ALL TABLE KNOWLEDGE FLOWS THROUGH THAT BOT'S IMPERFECT MEMORY;
  # WITHOUT ONE (DIRECT CALLS, TESTS) RECALL IS PERFECT
  seen = remembered_cards(view, personality) if personality != None else cards_seen(view)
  voids = observed_voids(view, t, prenom, personality)
  opp_trumps = trumps_still_out(view, view["trumps"], seen) # 0 FOR NT/MISÈRE BY DEFINITION
  suit_len = {s: sum(1 for (su, r) in hand if su == s and r != 15) for s in (1, 2, 3, 4)}

  win_i = trick_winner(table_cards, view["trick_suit"], t, prenom) if table_cards else None
  winner_seat = table[win_i]["seat"] if win_i != None else None
  partner_winning = winner_seat == partner

  # WOULD PLAYING c TAKE THE TRICK AS IT STANDS (IGNORING PLAYERS STILL TO COME -
  # THE SAME SIMPLIFICATION A QUICK HUMAN READ MAKES)
  def wins_now(c):
    if leading:
      return True
    return trick_winner(table_cards + [c], view["trick_suit"], t, prenom) == len(table_cards)

  scores = {}
  for i in view["legal_plays"]:
    c = hand[i]
    strength = card_strength(c, t, prenom)
    es = eff_suit(c, t, prenom)
    is_trump = es == t # IN NT THIS IS ONLY EVER THE (UN-NOMINATED) JOKER

    # ---- MISÈRE: THE CONTRACTOR MUST LOSE EVERY TRICK; DEFENDERS MUST NOT LET THEM ----
    if is_misere:
      if seat == contractor:
        if leading:
          s = -strength                       # LEAD THE LOWEST CARD WE OWN
        elif wins_now(c):
          s = -50 - strength                  # WINNING = CONTRACT FAILS: LAST RESORT ONLY
        else:
          s = strength                        # SAFE TRICK: SHED THE MOST DANGEROUS CARD
      else:
        contractor_played = any(p["seat"] == contractor for p in table)
        contractor_winning = winner_seat == contractor
        if contractor_played and contractor_winning:
          # THEY'RE ON THE HOOK - STAY UNDER THEM, BURNING OUR BIGGEST CARD THAT STILL LOSES
          s = strength if not wins_now(c) else -20 - strength
        else:
          s = -strength                       # BEFORE THEY PLAY: KEEP THE TRICK LOW, FORCE THEM UP
      scores[i] = round(s, 3)
      continue

    # ---- SUIT CONTRACTS AND NO TRUMPS ----
    if leading:
      master = is_master_of_suit(c, view, t, prenom, seen)
      # OPPONENTS WHO MIGHT STILL HOLD TRUMPS: NOT YET OBSERVED FAILING TO FOLLOW A
      # TRUMP LEAD. ONCE EVERY OPPONENT HAS SHOWN VOID, ANY TRUMPS STILL "OUT" CAN ONLY
      # BE THE PARTNER'S - DRAWING THEM WOULD BLEED OUR OWN SIDE'S RUFFING POWER
      opps_may_hold_trumps = [o for o in opponents if t not in voids[o]]
      if is_trump:
        if not on_contract_team:
          # LEADING TRUMPS BACK AT THE CONTRACTOR JUST DOES THEIR TRUMP-DRAWING FOR
          # THEM AND STRIPS OUR SIDE'S RUFFING POWER - A MASTER TRUMP MAY STILL BE
          # CASHED, ANYTHING ELSE IS HEAVILY DISCOURAGED
          s = 2 if master else -6
        elif opp_trumps > 0 and opps_may_hold_trumps:
          # DRAW TRUMPS FROM THE TOP - BUT EAGERNESS SCALES WITH HOW MANY CAN STILL BE
          # OUT: WITH ONLY ONE OR TWO LEFT, MOVING OFF TRUMPS AND KEEPING OURS FOR
          # RUFFS BEATS CHASING THE LAST STRAGGLER (CASHING A MASTER SCORES 8+ BELOW)
          s = 2.5 + min(opp_trumps, 4) * 1.3 + (4 if master else 0) + strength * 0.05
        else:
          # OPPONENTS DRAINED (COUNTED OUT OR ALL OBSERVED VOID): RETAIN TRUMPS TO RUFF
          # OFF-SUIT TRICKS LATER - EVEN A MASTER TRUMP LEAD IS NOW A LOW PRIORITY
          s = 3 if master else -4
      else:
        if master:
          # CASH A MASTER - UNLESS AN OPPONENT IS KNOWN VOID AND CAN STILL RUFF IT
          ruffable = opp_trumps > 0 and any(es in voids[o] for o in opponents)
          s = 8 - (7 if ruffable else 0) + strength * 0.05
        else:
          # DEVELOPING LEAD: LOW FROM LENGTH; AVOID LEADING INTO A KNOWN RUFF
          s = 2 + suit_len.get(es, 0) * 0.3 - strength * 0.15
          if opp_trumps > 0 and any(es in voids[o] for o in opponents):
            s -= 4
      if not on_contract_team:
        shown = partner_shown_suit(view)
        if shown and es == shown:
          s += 4                              # DEFENDERS LEAD THEIR PARTNER'S BID SUIT
    else:
      if partner_winning:
        # DUCK UNDER PARTNER: LOWEST CARD BEST, OVERTAKING THEM IS ACTIVELY BAD
        s = 8 - strength * 0.3 - (6 if wins_now(c) else 0)
      elif wins_now(c):
        if last_to_play:
          s = 12 - strength * 0.2             # LAST SEAT: WIN AS CHEAPLY AS POSSIBLE
        else:
          s = 9 + strength * 0.05             # EARLIER SEATS: WIN HIGH TO HOLD THE TRICK
      else:
        # CANNOT WIN: THROW THE CHEAPEST CARD, PREFERRING SHORT SUITS (WORKS TOWARD
        # RUFFS IN SUIT CONTRACTS, COSTS NOTHING IN NT)
        s = 4 - strength * 0.4 - suit_len.get(es, 0) * 0.2
    scores[i] = round(s, 3)
  return scores

# LOCAL MIRROR OF gui_bid's bid_rank SO THE BOT ONLY EVER SUBMITS BIDS THE SERVER WILL
# ACCEPT - A REJECTED BID WOULD RE-FOCUS THE BOT AND LOOP FOREVER ON THE SAME BAD BID.
# MISÈRE (SUIT 0, TRICKS 10) RANKS BETWEEN 8 SPADES (240) AND 8 CLUBS (260)
def bid_rank(tricks, suit):
  if tricks == None or suit == None:
    return 0
  if suit == 0:
    return (8 * 6 + 1) * 2 + 1 if tricks == 10 else 0
  if not (6 <= tricks <= 10 and 1 <= suit <= 5):
    return 0
  return (tricks * 6 + suit) * 2

# THE ONLY WINDOW A PlayerBot GETS ONTO THE GAME. EVERYTHING IN HERE IS EITHER THE BOT'S
# OWN CARDS OR INFORMATION EVERY HUMAN AT THE TABLE CAN SEE (BIDS, TABLED CARDS, PAST
# TRICKS, SCORES). DELIBERATELY EXCLUDED: OTHER PLAYERS' HANDS, THE UNDEALT DECK, THE
# UNAWARDED KITTY. CARDS ARE PASSED AS (suit, rank) TUPLES, NOT Card OBJECTS, SO A BOT
# CANNOT MUTATE THE LIVE GAME THROUGH ITS VIEW.
def build_view(game, seat):
  me = game.players[seat]
  def tuples(deck):
    return [(c.suit, c.rank) for c in deck.cards] if deck != None else []
  return {
    "seat": seat,
    "state": game.state_name(),
    "names": [p.name for p in game.players],           # PUBLIC: WHO SITS WHERE
    "hand": tuples(me.hand),                           # OWN CARDS ONLY
    "kitty": tuples(me.kitty),                         # OWN KITTY ONLY (CONTRACTOR AFTER AWARD - EMPTY OTHERWISE)
    "legal_plays": game.legal_play_indices(seat),      # SERVER-COMPUTED, SAME LIST THE CLIENT GETS
    "trumps": game.trumps,
    "trick_suit": game.trick_suit,
    "dealer": game.dealer,
    "sitting_out": game.sitting_out,
    "scores": [team.score for team in game.teams],
    # CURRENT PUBLIC BID STATE PER SEAT (WHAT THE BIDDING BOX SHOWS EVERYONE)
    "bids": [{"suit": p.bid.suit, "tricks": p.bid.tricks, "passed": p.bid.passed} for p in game.players],
    # EVENT LOGS - THE BOT'S ONLY SOURCE OF "WHAT HAS HAPPENED": EVERY BID IN ORDER AND
    # EVERY CARD PUBLICLY TABLED. FUTURE MEMORY/INFERENCE PHASES READ THESE, NEVER HANDS.
    "bid_history": list(game.bid_history),
    "trick_history": [dict(t) for t in game.trick_history],
    "current_trick": list(game.current_trick),
    # JOKER STATE (ALL PUBLIC ONCE IN PLAY)
    "suits_led": list(game.suits_led),
    "joker_nominating": game.joker_nominating,
    "joker_nomination": game.joker_nomination,
    "joker_prenom": game.joker_prenom,
    "joker_prenom_open": game.joker_prenom_open,
    "joker_lead_options": game.joker_lead_suits() if game.joker_nominating else [],
  }

# ONE SEATED PLAYER BOT. PERSISTENT STATE (NAME, PERSONALITY, LATER MEMORY/INFERENCES)
# LIVES AS PLAIN JSON DATA IN game.player_bots[seat] - NOT ON THIS OBJECT - SO THE
# EXISTING to_dict()/restore_state() PERSISTENCE COVERS BOTS FOR FREE AND A SERVICE
# RESTART RESUMES THEM MID-HAND. THIS CLASS IS A STATELESS WRAPPER REBUILT PER DECISION.
#
# PHASE 1: SKELETON ONLY - DECISIONS MIRROR THE DEV RANDOM BOT (PASS / THROW BACK KITTY /
# RANDOM LEGAL CARD) BUT FLOW THROUGH THE view-ONLY INTERFACE, SO LATER PHASES SWAP THE
# INSIDES OF THESE METHODS WITHOUT TOUCHING ANY PLUMBING.
class PlayerBot:

  def __init__(self, state):
    self.state = state # THE game.player_bots[seat] BLOB: {"name", "personality", ...}
    self.name = state["name"]
    self.personality = state["personality"]

  # RETURNS THE gui_bid PAYLOAD (PHASE 2). ESTIMATE OWN TRICKS PER CANDIDATE CONTRACT,
  # ADD THE ASSUMED PARTNER CONTRIBUTION AND THE PERSONALITY'S confidence BIAS, THEN BID
  # THE *MINIMUM* LEVEL THAT OUTRANKS THE TABLE IF THE ESTIMATE COVERS IT - ELSE PASS.
  # ONLY BIDS THE SERVER WILL ACCEPT ARE EVER RETURNED (SEE bid_rank) SO A BOT CAN'T
  # WEDGE THE BIDDING BY REPEATING A REJECTED BID.
  def decide_bid(self, view):
    PASS = {"pass": True, "suit": 1, "tricks": 6}

    # AS THE LAST ACTIVE BIDDER THE SERVER OFFERS ONE OPTIONAL SELF-RAISE. ALMOST ALWAYS
    # DECLINE (RAISING OUR OWN CONTRACT ONLY ADDS RISK) - THE ONE HUMAN-PLAUSIBLE
    # EXCEPTION IS SWITCHING INTO THE SUIT (OR COLOUR) OUR PARTNER SHOWED DURING THE
    # AUCTION, AND ONLY WHEN OUR OWN HAND IS COMFORTABLY BETTER THERE. INCREDIBLY RARE
    # BY DESIGN: NEEDS A PARTNER SIGNAL *AND* A FULL SPARE TRICK BEYOND THE COMMITMENT
    if view["bids"][view["seat"]]["passed"] == "WINNER_INCREASE_OPTION":
      return self._maybe_increase_into_partners_suit(view) or PASS

    # STRONGEST BID CURRENTLY ON THE TABLE (SAME SCAN THE SERVER DOES)
    high_rank, high_tricks = 0, None
    for b in view["bids"]:
      r = bid_rank(b["tricks"], b["suit"])
      if r > high_rank:
        high_rank, high_tricks = r, b["tricks"]

    # HOW MANY TRICKS WE BELIEVE OUR SIDE CAN TAKE, PER CONTRACT. confidence IS THE
    # PERSONALITY'S SYSTEMATIC OVER/UNDER-ESTIMATION IN WHOLE TRICKS (-3..+3)
    belief = self.personality["confidence"] + PARTNER_BASE_TRICKS
    estimates = {s: estimate_tricks(view["hand"], s) + belief for s in (1, 2, 3, 4)}
    estimates[5] = estimate_tricks_nt(view["hand"]) + belief

    # BID READING (PHASE 4): EVERY BID AT THE TABLE IS INFORMATION. THE PARTNER'S
    # SHOWN SUIT PROMISES TRICKS THERE (WEIGHTED BY HOW MUCH THIS BOT TRUSTS PARTNER
    # SIGNALS), AND ITS SAME-COLOUR TWIN GAINS A LITTLE (THEIR JACK IS OUR LEFT
    # BOWER). AN OPPONENT'S SHOWN SUIT MEANS THE STRENGTH THERE IS THEIRS, NOT OURS.
    # LOW ABANDONED BIDS COUNT THE SAME AS FOUGHT-FOR ONES - AN INDICATION IS AN
    # INDICATION (SEE SIGNAL BIDS BELOW; THIS IS THE READING SIDE OF THAT CONTRACT)
    trust = self.personality["partner_trust"]
    partner = (view["seat"] + 2) % 4
    for b in view["bid_history"]:
      if b["pass"] or b["suit"] not in (1, 2, 3, 4):
        continue
      if b["seat"] == partner:
        estimates[b["suit"]] += 1.5 * trust
        estimates[SUIT_LEFT_BOWER[b["suit"]]] += 0.5 * trust
      elif b["seat"] != view["seat"]:
        estimates[b["suit"]] -= 1.2

    # MISÈRE: ONLY LEGAL OVER A SEVEN (SERVER RULE - BIDDING IT OTHERWISE WOULD LOOP),
    # AND ONLY WITH A HAND THAT EXPECTS TO LOSE EVERYTHING
    if high_tricks == 7 and looks_like_misere(view["hand"]):
      return {"pass": False, "suit": 0, "tricks": 10}

    # CHEAPEST SUFFICIENT BID: FOR EACH CONTRACT, THE LOWEST TRICK COUNT THAT OUTRANKS
    # THE TABLE; KEEP IT ONLY IF OUR ESTIMATE REACHES THAT LEVEL. OF THE SURVIVORS TAKE
    # THE ONE WITH THE BIGGEST SAFETY MARGIN (ESTIMATE MINUS COMMITMENT)
    best = None # (margin, tricks, suit)
    for suit, est in estimates.items():
      for tricks in range(6, 11):
        if bid_rank(tricks, suit) > high_rank:
          if est >= tricks and (best is None or est - tricks > best[0]):
            best = (est - tricks, tricks, suit)
          break # ONLY THE CHEAPEST LEVEL PER SUIT - HIGHER ONES JUST SHRINK THE MARGIN
    if best:
      # THE SUIT IS CHOSEN BY THE MARGIN AT THE CHEAPEST SUFFICIENT LEVEL, BUT THE
      # LEVEL THEN RISES TOWARD THE ESTIMATE - A HAND READ AS 8 TRICKS OPENS AT 8,
      # NOT 6. int(est) KEEPS A FRACTIONAL-TRICK SAFETY MARGIN, AND THE confidence
      # PERSONALITY STILL SHIFTS THE ESTIMATE EITHER WAY
      margin, tricks, suit = best
      tricks = max(tricks, min(10, int(estimates[suit])))
      return {"pass": False, "suit": suit, "tricks": tricks}

    # SIGNAL BID (PHASE 4): NOT STRONG ENOUGH TO GENUINELY CONTRACT, BUT SOME HUMANS
    # OPEN A MINIMUM BID IN THEIR BEST SUIT ANYWAY *AS A MESSAGE TO PARTNER*, EXPECTING
    # TO BE OUTBID. GATES: THE PERSONALITY'S signaling TENDENCY (DETERMINISTIC PER
    # HAND), FIRST ACTION THIS AUCTION, PARTNER SILENT SO FAR, TABLE STILL BELOW THE
    # SEVENS (STILL CHEAP TO OUTBID), AND A SUIT ACTUALLY WORTH SHOWING. GETTING STUCK
    # WITH THE CONTRACT WHEN EVERYONE PASSES IS REALISTIC AND STAYS
    i_have_acted = any(b["seat"] == view["seat"] for b in view["bid_history"])
    partner_acted_real = any(b["seat"] == partner and not b["pass"] for b in view["bid_history"])
    if (not i_have_acted and not partner_acted_real
        and high_rank < bid_rank(7, 1)
        and _det01(self.personality["seed"], 4, tuple(view["hand"])) < self.personality["signaling"]):
      raw = {s: estimate_tricks(view["hand"], s) for s in (1, 2, 3, 4)}
      show = max(raw, key=lambda s: (raw[s], -s))
      if raw[show] >= 2.5: # A SUIT WORTH SHOWING, NOT A BLUFF FROM NOTHING
        for tricks in range(6, 11):
          if bid_rank(tricks, show) > high_rank:
            if tricks == 6: # ONLY EVER A MINIMUM BID - A JUMP WOULD READ AS GENUINE
              return {"pass": False, "suit": show, "tricks": 6}
            break
    return PASS

  # THE RARE INCREASE-OPTION SUIT SWITCH. RETURNS A BID DICT OR None (= DECLINE).
  # REASONED LIKE A HUMAN WOULD: "MY PARTNER SHOWED HEARTS AND MY HAND IS ACTUALLY
  # BETTER THERE THAN IN MY OWN BID" - NEVER A COLD SWITCH INTO AN UNSHOWN SUIT
  def _maybe_increase_into_partners_suit(self, view):
    me = view["bids"][view["seat"]]
    partner = (view["seat"] + 2) % 4
    # PARTNER'S LAST REAL BID THIS AUCTION (bid_history SURVIVES THEIR LATER PASS)
    partner_suits = [b["suit"] for b in view["bid_history"]
                     if b["seat"] == partner and not b["pass"] and b["suit"] in (1, 2, 3, 4)]
    if not partner_suits or me["suit"] not in (1, 2, 3, 4, 5):
      return None
    shown = partner_suits[-1]
    own_rank = bid_rank(me["tricks"], me["suit"])
    belief = self.personality["confidence"] + PARTNER_BASE_TRICKS
    best = None # (margin, tricks, suit)
    # PARTNER'S SUIT ITSELF, OR ITS SAME-COLOUR TWIN (WHERE THEIR JACK IS OUR LEFT BOWER)
    for cand in {shown, SUIT_LEFT_BOWER[shown]} - {me["suit"]}:
      est = estimate_tricks(view["hand"], cand) + belief
      for tricks in range(6, 11):
        if bid_rank(tricks, cand) > own_rank:
          if est >= tricks + 1.0 and (best is None or est - tricks > best[0]):
            best = (est - tricks, tricks, cand)
          break
    if best:
      return {"pass": False, "suit": best[2], "tricks": best[1]}
    return None

  # RETURNS THE gui_discard PAYLOAD (PHASE 2): FROM THE 13 CARDS (HAND + KITTY) THROW
  # THE 3 WITH THE LEAST KEEP-VALUE. INDICES ARE PER SOURCE LIST, ASCENDING, EXACTLY
  # 3 IN TOTAL - gui_discard REMOVES BLINDLY, SO THE COUNT CONTRACT LIVES HERE
  def decide_discard(self, view):
    trumps = view["trumps"]

    # MISÈRE: EVERY HIGH CARD IS A LIABILITY - THROW THE 3 STRONGEST. THE JOKER IS THE
    # WORST CARD OF ALL (FORCED TO WIN WHEN UNABLE TO FOLLOW), SO IT GOES FIRST
    if trumps == 0:
      def liability(card):
        suit, rank = card
        return 100 if rank == 15 else rank
      keep_value = lambda card: -liability(card)
    else:
      # SUIT CONTRACTS AND NO TRUMPS: NEVER THROW TRUMPS/JOKER/BOWERS OR OFF-SUIT ACES;
      # OTHERWISE PREFER EMPTYING SHORT OFF-SUITS AND THROW LOW BEFORE HIGH
      cards = view["hand"] + view["kitty"]
      suit_len = {s: sum(1 for (su, r) in cards if su == s and r != 15) for s in (1, 2, 3, 4)}
      # SHORTNESS ONLY PAYS IF THERE ARE TRUMPS TO RUFF WITH: WITH 4+ TRUMPS THE LENGTH
      # TERM IS STRONG ENOUGH TO DUMP A MID-RANK SINGLETON/DOUBLETON AND MANUFACTURE A
      # VOID (SHORT HAND = MORE CHANCES TO TRUMP OPPOSITION LEADS); WITH FEW TRUMPS IT
      # DROPS TO A TIE-BREAK SO THE BOT JUST THROWS ITS LOWEST JUNK
      n_trumps = sum(1 for (su, r) in cards
                     if r == 15 or su == trumps or (r == 11 and su == SUIT_LEFT_BOWER.get(trumps)))
      length_weight = 3 if (trumps in (1, 2, 3, 4) and n_trumps >= 4) else 1
      def keep_value(card):
        suit, rank = card
        if rank == 15:
          return 1000 # JOKER
        if trumps in (1, 2, 3, 4):
          if suit == trumps or (rank == 11 and suit == SUIT_LEFT_BOWER.get(trumps)):
            return 900 + rank # ANY TRUMP OUTKEEPS ANY OFF-SUIT CARD
        if rank == 14:
          return 800 # OFF-SUIT ACES ARE SURE-ISH TRICKS IN ANY CONTRACT
        return rank + suit_len[suit] * length_weight

    # TAG EVERY CARD WITH ITS SOURCE LIST + INDEX, TAKE THE 3 CHEAPEST TO KEEP
    tagged = ([("hand", i, c) for i, c in enumerate(view["hand"])]
              + [("kitty", i, c) for i, c in enumerate(view["kitty"])])
    throw = sorted(tagged, key=lambda t: keep_value(t[2]))[:3]
    return {"hand": sorted(i for src, i, c in throw if src == "hand"),
            "kitty": sorted(i for src, i, c in throw if src == "kitty")}

  # RETURNS A HAND INDEX FROM view["legal_plays"] (SERVER RE-CHECKS LEGALITY ANYWAY).
  # SCORE EVERY LEGAL MOVE (THROUGH THIS BOT'S IMPERFECT MEMORY), THEN DRAW FROM A
  # SOFTMAX OVER THE SCORES: error_temp IS THE MISCALCULATION DIAL. OBVIOUS MOVES
  # (BIG SCORE GAPS) STAY NEAR-CERTAIN AT ANY TEMPERATURE; CLOSE JUDGEMENT CALLS
  # SOMETIMES GO WRONG, EXACTLY LIKE A HUMAN'S. THE DRAW IS A DETERMINISTIC HASH OF
  # (SEED, TRICK STATE) SO RE-FIRED CHECKS AND REPLAYS PICK THE SAME CARD.
  def decide_play(self, view):
    legal = view["legal_plays"]
    if not legal:
      return None
    if len(legal) == 1:
      return legal[0]
    scores = score_moves(view, self.personality)
    temp = max(self.personality.get("error_temp", 0.05), 0.01)
    ordered = sorted(legal) # STABLE ORDER FOR THE CUMULATIVE WALK
    top = max(scores.get(i, 0) for i in ordered)
    weights = [exp((scores.get(i, 0) - top) / temp) for i in ordered]
    r = _det01(self.personality["seed"], 3,
               len(view["trick_history"]), len(view["current_trick"]),
               tuple(view["hand"])) * sum(weights)
    for i, w in zip(ordered, weights):
      r -= w
      if r <= 0:
        return i
    return ordered[-1] # FLOAT DUST FALLBACK

  # RETURNS THE SUIT (1-4) TO NOMINATE AFTER LEADING THE JOKER (NO TRUMPS / MISÈRE).
  # NOMINATE WHERE WE ARE SHORTEST: THE OTHERS ARE FORCED TO FOLLOW THAT SUIT, SO IT
  # DRAINS THEIR CARDS EXACTLY WHERE WE HAVE THE LEAST CONTROL LEFT
  def decide_joker_nomination(self, view):
    options = view["joker_lead_options"]
    def my_count(s):
      return sum(1 for (su, r) in view["hand"] if su == s and r != 15)
    return min(options, key=lambda s: (my_count(s), s))

# FILLS EVERY EMPTY SEAT WITH A FRESH PlayerBot - QUEUED BY THE add_bots SOCKET EVENT.
# REGISTERS THE BOT IN game.player_bots BEFORE SITTING SO THE VERY FIRST STATE PUSH
# ALREADY KNOWS THE SEAT IS A BOT; IF gui_sit REFUSES (SEAT RACE WITH A HUMAN) THE
# REGISTRATION IS ROLLED BACK.
def seat_player_bots(game):
  if game.state_name() != "WAITING FOR PLAYERS":
    return # SEATS ONLY EXIST TO FILL IN S0
  taken = [p.name for p in game.players]
  # SEATED FORM IS "B|Name". A HUMAN MAY HAVE LOGGED IN UNDER A BOT NAME - EXCLUDE IT
  # (CASE-INSENSITIVELY)
  available = [PLAYER_BOT_PREFIX + n for n in BOT_NAMES
               if not any(_same_name(PLAYER_BOT_PREFIX + n, t) for t in taken)]
  unseated = sample(available, len(available)) # RANDOM ORDER: WHICH BOTS TURN UP VARIES GAME TO GAME
  for seat in range(len(game.players)):
    if game.players[seat].name == None and len(unseated):
      name = unseated.pop(0)
      game.player_bots[seat] = {"name": name, "personality": sample_personality()}
      game.delay(1) # PACED SO CLIENTS SEE THE BOTS SIT DOWN ONE BY ONE
      game.gui_sit(name, seat)
      if not _same_name(game.players[seat].name, name): # SEAT RACE LOST - UNDO
        del game.player_bots[seat]
      else: # LOG THE SAMPLED TUNING SO A GAME'S BOT BEHAVIOUR CAN BE READ BACK LATER
        personality = game.player_bots[seat]["personality"]
        knobs = " ".join(f"{k}={personality[k]}" for k in PERSONALITY_CONFIG)
        game.log(f"{name} seated with tuning: {knobs} seed={personality['seed']}")
  # PERSIST THE NEW SEATING EVEN IF THE TABLE DIDN'T FILL (A PARTIAL LOBBY OTHERWISE
  # ONLY AUTOSAVES ONCE THE S0->S1 TRANSITION FIRES) - A RESTART THEN KEEPS THE BOTS
  game.autosave()

# ONE DECISION FOR THE FOCUSED PLAYER BOT, IF THE FOCUSED SEAT IS ONE. BUILDS THE
# RESTRICTED VIEW, ASKS THE BOT, APPLIES THE ANSWER THROUGH THE SAME gui_* CALLS A
# HUMAN'S CLIENT WOULD MAKE. THE NAME CROSS-CHECK DROPS STALE REGISTRATIONS (E.G. A
# RESTORED SAVE WHERE THE SEAT NOW HOLDS SOMEONE ELSE).
def player_bot_check(game):
  seat = game.player_focus
  if seat == None or seat not in game.player_bots:
    return
  blob = game.player_bots[seat]
  if not _same_name(game.players[seat].name, blob["name"]):
    del game.player_bots[seat] # STALE ENTRY - SEAT NO LONGER HOLDS THIS BOT
    return
  bot = PlayerBot(blob)

  # DECIDE WHETHER THERE IS ANYTHING TO DO *BEFORE* PAYING THE THINK-TIME SLEEP.
  # bot_check FIRES ON EVERY PUSH, SO A RE-FIRED CHECK WITH NOTHING LEFT TO DO (KITTY
  # ALREADY DISCARDED, BIDDING SETTLED) WOULD OTHERWISE SLEEP 3-12s ON THE SINGLE
  # WORKER THREAD AND ONLY THEN NO-OP - BLOCKING THE state_trans QUEUED BEHIND IT
  # (THE FIRST-LEAD DIALOG WOULD ARRIVE LONG AFTER THE KITTY VANISHED). THE SAME
  # CHEAP GUARDS STILL RUN AGAIN ON THE POST-SLEEP SNAPSHOT BELOW (STATE MAY MOVE
  # WHILE WE SLEEP) - THIS PRE-CHECK ONLY SKIPS THE POINTLESS SLEEP.
  pre = build_view(game, seat)
  if pre["state"] == "TAKING BIDS":
    pre_passed = pre["bids"][pre["seat"]]["passed"]
    if not (pre_passed == False or pre_passed == "WINNER_INCREASE_OPTION"):
      return
  elif pre["state"] == "AWARD KITTY":
    if not pre["kitty"]:
      return
  elif pre["state"] == "PLAY HAND":
    if not pre["joker_nominating"] and not pre["legal_plays"]:
      return
  else:
    return # NO BOT DECISIONS EXIST IN OTHER STATES - NOTHING TO THINK ABOUT

  # HUMAN-ISH THINK TIME, VARIED PER DECISION KIND (DISCARDS TAKE LONGEST, ROUTINE CARD
  # PLAYS ARE QUICK), SCALED BY THE BOT'S tempo PERSONALITY (SNAPPY vs DELIBERATE
  # PLAYERS), WITH AN OCCASIONAL LONG "TANK" LIKE A HUMAN STUCK ON A DECISION.
  # PLAIN RANDOM IS FINE HERE - PACING IS NOT A DECISION, SO THE DETERMINISM RULE
  # DOESN'T APPLY. SLEEPING ON THE SINGLE WORKER THREAD ALSO THROTTLES THE RE-FIRED
  # CHECKS THAT EVERY PUSH QUEUES - WITHOUT IT THE BOTS MACHINE-GUN EVENTS FASTER THAN
  # HUMANS CAN READ THE DIALOGS. THE VIEW IS SNAPSHOTTED *AFTER* THE SLEEP SO THE
  # DECISION USES CURRENT STATE. skip_delays (DEV) BYPASSES AS ALWAYS
  THINK = {"TAKING BIDS": (1.5, 4.0), "AWARD KITTY": (3.0, 6.0), "PLAY HAND": (0.8, 2.5)}
  lo, hi = THINK.get(game.state_name(), (0.5, 1.0))
  think = uniform(lo, hi) * blob["personality"].get("tempo", 1.0)
  if uniform(0.0, 1.0) < 0.08:
    think *= 2.5 # THE OCCASIONAL TANK
  game.delay(min(think, 12.0)) # HARD CAP: NEVER LEAVE THE TABLE WONDERING IF IT HUNG
  view = build_view(game, seat)

  if view["state"] == "TAKING BIDS":
    # ACT ONLY WHILE GENUINELY IN THE AUCTION: passed IS False (STILL BIDDING) OR THE
    # SERVER IS OFFERING THE WINNER'S SELF-RAISE. ONCE "WINNER" IS SET, A RE-FIRED
    # CHECK MUST NOT BID AGAIN - IT WOULD OUTBID ITS OWN CONTRACT (SEEN LIVE: A 6C
    # CONTRACT TURNED INTO 6H AFTER THE WINNER "DECLINED" THE INCREASE)
    my_passed = view["bids"][view["seat"]]["passed"]
    if my_passed == False or my_passed == "WINNER_INCREASE_OPTION":
      game.gui_bid(bot.name, bot.decide_bid(view))

  elif view["state"] == "AWARD KITTY":
    # ONLY WHILE OUR KITTY IS STILL HELD: bot_check FIRES ON EVERY PUSH, AND WITHOUT
    # THIS GUARD A SECOND FIRING WOULD DISCARD 3 MORE CARDS FROM THE ALREADY-TRIMMED
    # HAND (AND AGAIN, UNTIL THE HAND WAS EMPTY - FOUND THE HARD WAY IN LEAGUE TESTING)
    if view["kitty"]:
      game.gui_discard(bot.name, bot.decide_discard(view))

  elif view["state"] == "PLAY HAND":
    if view["joker_nominating"]:
      game.delay(uniform(0.5, 1.5)) # AND A LITTLE EXTRA TO "CHOOSE" THE SUIT
      game.gui_joker_nominate(bot.name, bot.decide_joker_nomination(view))
      return
    card_index = bot.decide_play(view)
    if card_index != None:
      game.gui_play(bot.name, card_index)

# THE SINGLE HOOK sio_push() QUEUES AFTER EVERY STATE CHANGE. ROUTES TO WHICHEVER BOT
# KIND HOLDS THE FOCUSED SEAT - EACH CHECK NO-OPS UNLESS ITS OWN KIND IS FOCUSED, SO
# DEV TEST MODE AND PLAYER BOTS CAN EVEN COEXIST (D|/B| PREFIXES KEEP NAMES DISTINCT).
def bot_check(game):
  if game.test_mode:
    dev_random_bot_check(game)
  player_bot_check(game)
