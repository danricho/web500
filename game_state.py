from datetime import datetime
from time import sleep
from random import randint
import json
import os
import shutil
import threading
import uuid

from playing_cards import *
from dotdict import dotdict as dd

import threaded_schedule
from threaded_schedule import schedule as schedule
import applog

import bots # BOT PLAYERS (INCL. THE ADMIN TEST-MODE BOT) LIVE OUTSIDE THE GAME RULES
import ntfy # OPTIONAL ntfy PUSH NOTIFICATIONS (DISABLED BY DEFAULT - SEE ntfy.py)

# SINGLE WORKER ON PURPOSE: ITS JOB QUEUE IS WHAT SERIALISES EVERY GAME MUTATION.
# LONG-RUNNING WORK (auto_deal, auto_points, state_trans) IS QUEUED, NEVER CALLED
# DIRECTLY, SO TWO PLAYERS' ACTIONS CAN'T INTERLEAVE. KEEP IT AT workers=1.
schedule_t = threaded_schedule.ThreadedSchedule(workers=1, verbose=False)
schedule_t.check_for_due_thread()

games = [] # FINISHED GAMES THIS PROCESS LIFETIME - IN MEMORY ONLY, SERVED BY /api/last-game

# NAMES ARE STORED/DISPLAYED AS FIRST WRITTEN BUT COMPARED CASE-INSENSITIVELY
def same_name(a, b):
  return a != None and b != None and str(a).casefold() == str(b).casefold()

# RE-READ FROM data/auth.json EACH CALL (RARE - ONLY GATES THE ntfy SEAT-JOIN NOTIFICATION
# BELOW) RATHER THAN CACHED, SO AN EDITED admin_users LIST TAKES EFFECT WITHOUT A RESTART -
# SAME "NO CACHING" CHOICE ntfy.py MAKES FOR ITS OWN CONFIG. MIRRORS main.py's
# is_admin_user() (SESSION-BASED, CACHED AUTH) BUT CAN'T REUSE IT - gui_sit() HAS ONLY A
# NAME, NOT A FLASK SESSION, AND main.py IMPORTS game_state, NOT THE OTHER WAY ROUND.
def _name_is_admin(name):
  try:
    with open(os.path.join(DATA_DIR, "auth.json")) as f:
      admin_users = json.load(f).get("admin_users", [])
  except Exception:
    return False
  return any(same_name(name, a) for a in admin_users)

# SAVE FILES: AUTOSAVE PERSISTS THE LIVE GAME ACROSS SERVICE RESTARTS (LOADED AT STARTUP,
# CLEARED BY /api/reinit); CHECKPOINT IS THE MANUAL ADMIN SAVE/LOAD SLOT (SEPARATE FILE).
# EACH TABLE GETS ITS OWN SUBDIRECTORY - SEE THE TABLE REGISTRY BELOW.
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
TABLES_DIR = os.path.join(DATA_DIR, "tables")
SAVE_VERSION = 3 # BUMP WHENEVER THE SAVED STATE SHAPE CHANGES - OLD FILES ARE THEN REFUSED

# ---------------------------------------------------------------------------
# TABLE REGISTRY (MULTI-TABLE SUPPORT). A "TABLE" IS JUST A GameStateMachine
# INSTANCE; ITS NAME DOUBLES AS THE Socket.IO ROOM NAME AND THE data/tables/<name>/
# SAVE-DIRECTORY NAME - NO SEPARATE ID CONCEPT. NAMES ARE GENERIC AND SELF-DERIVED
# ("TABLE 1", "TABLE 2", ...): SCAN THE REGISTRY FOR THE HIGHEST EXISTING N AND PICK
# N+1. A NUMBER FREED BY A DELETED TABLE IS DELIBERATELY REUSABLE (Dan's call) - IT'S
# delete_table's _deleted FLAG (CHECKED IN sio_push), NOT NAME UNIQUENESS, THAT STOPS
# A REMOVED TABLE FROM BLEEDING INTO WHATEVER NEW TABLE NEXT GETS ITS OLD NAME/ROOM.
# ---------------------------------------------------------------------------
tables = {} # name -> GameStateMachine, IN-MEMORY REGISTRY OF EVERY LIVE TABLE
TABLE_EMPTY_REAP_S = 300 # A TABLE WITH NO SEATED PLAYER FOR THIS LONG IS AUTO-REMOVED
_table_registry_lock = threading.Lock() # GUARDS "COMPUTE NEXT NAME + INSERT" AS ONE STEP
                                         # (THE APP RUNS 100 THREADS - TWO CONCURRENT
                                         # /api/create_table CALLS COULD OTHERWISE COLLIDE)

def table_dir(name):
  return os.path.join(TABLES_DIR, name)
def table_autosave_path(name):
  return os.path.join(table_dir(name), "autosave.json")
def table_checkpoint_path(name):
  return os.path.join(table_dir(name), "checkpoint.json")

def _next_table_name():
  numbers = [int(n[6:]) for n in tables if n.startswith("TABLE ") and n[6:].isdigit()]
  return f"TABLE {(max(numbers) + 1) if numbers else 1}"

# CREATES AND REGISTERS A FRESH TABLE, NAMED AND PATHED BY THE REGISTRY ITSELF.
def create_table(socketio_init):
  with _table_registry_lock:
    name = _next_table_name()
    t = GameStateMachine(name, socketio_init=socketio_init)
    t.autosave_path = table_autosave_path(name)
    t.checkpoint_path = table_checkpoint_path(name)
    tables[name] = t
  return t

# BOOT-TIME: POPULATES THE REGISTRY FROM data/tables/<name>/autosave.json ON DISK.
# ONE-TIME MIGRATION FIRST: IF THE OLD FLAT data/autosave.json (+ checkpoint.json)
# EXISTS AND THE NEW LAYOUT DOESN'T YET, MOVE THEM INTO data/tables/TABLE 1/ SO A
# LIVE DEPLOYED GAME SURVIVES THIS CHANGE. A FRESH INSTALL (NEITHER LAYOUT PRESENT)
# SEEDS ONE EMPTY TABLE SO THE APP ISN'T EMPTY ON FIRST BOOT. RETURNS True IF ANY
# TABLE WAS ACTUALLY RESTORED FROM A SAVE FILE (DRIVES THE "GAME RESTORED" TOAST).
def load_tables_from_disk(socketio_init):
  os.makedirs(TABLES_DIR, exist_ok=True)

  legacy_autosave = os.path.join(DATA_DIR, "autosave.json")
  legacy_checkpoint = os.path.join(DATA_DIR, "checkpoint.json")
  if not os.listdir(TABLES_DIR) and os.path.exists(legacy_autosave):
    legacy_dir = table_dir("TABLE 1")
    os.makedirs(legacy_dir, exist_ok=True)
    os.replace(legacy_autosave, table_autosave_path("TABLE 1"))
    if os.path.exists(legacy_checkpoint):
      os.replace(legacy_checkpoint, table_checkpoint_path("TABLE 1"))
    print(f"[MIGRATION] Moved legacy single-table save into {legacy_dir}")

  found_any = False
  restored_any = False
  for entry in sorted(os.listdir(TABLES_DIR)):
    autosave_path = table_autosave_path(entry)
    if not os.path.isdir(table_dir(entry)) or not os.path.exists(autosave_path):
      continue
    found_any = True
    t = GameStateMachine(entry, socketio_init=socketio_init)
    t.autosave_path = autosave_path
    t.checkpoint_path = table_checkpoint_path(entry)
    if t.restore_state(autosave_path):
      restored_any = True
    tables[entry] = t

  if not found_any:
    create_table(socketio_init) # FRESH INSTALL - SEED ONE EMPTY TABLE

  return restored_any

# ONCE-A-SECOND FAN-OUT (QUEUED FROM main.py): RE-DERIVES THE TARGET LIST FROM THE
# LIVE REGISTRY EVERY TICK, SO TABLES CREATED/REMOVED AT RUNTIME ARE PICKED UP
# WITHOUT RE-REGISTERING JOBS. EACH TABLE'S OWN KEEP-ALIVE/IDLE-NUDGE RUNS ON THE
# SHARED WORKER, SAME AS EVERY OTHER GAME MUTATION.
def poll_all_tables():
  for t in list(tables.values()):
    schedule_t.jobqueue.put(t.is_push_needed)
    schedule_t.jobqueue.put(t.check_focus_idle)
  schedule_t.jobqueue.put(reap_empty_tables)

# TEARS DOWN A TABLE: TELLS ANY CONNECTED CLIENT VIA A DEDICATED table_closed EVENT
# (NOT A TOAST - THE ROOM WON'T EXIST TO PUSH TO AFTERWARDS, SO THE CLIENT WOULD
# OTHERWISE JUST GO SILENTLY STALE FOREVER), THEN REMOVES IT FROM THE REGISTRY AND
# DELETES ITS data/tables/<name>/ DIRECTORY. SHARED BY reap_empty_tables() (BELOW) AND
# THE ADMIN-ONLY /admin/delete_table ROUTE (MANUAL, ANY TABLE, REGARDLESS OF STATE).
# _deleted (TRANSIENT, CHECKED BY sio_push) STOPS THE OBJECT ITSELF: A TABLE WITH TEST
# MODE ON (OR SEATED PLAYER BOTS) IS SELF-PERPETUATING - EVERY sio_push() QUEUES
# bot_check, WHICH MAKES A BOT ACT, WHICH CALLS sio_push() AGAIN - AND REMOVING IT FROM
# THE REGISTRY ALONE DOESN'T BREAK THAT CHAIN. WITHOUT THIS FLAG THE "DELETED" OBJECT
# KEEPS RUNNING IN THE BACKGROUND, STILL PUSHING STATE TO ITS OLD SOCKET.IO ROOM
# (room=self.name) FOREVER - AND SINCE _next_table_name() CAN HAND OUT THAT SAME NAME
# AGAIN, A BRAND NEW TABLE CAN END UP SHARING A ROOM WITH A ZOMBIE STILL BROADCASTING
# ITS OWN (STALE) test_mode/player_bots STATE INTO IT. CONFIRMED LIVE VIA THE JOURNAL.
def delete_table(name, reason):
  t = tables.get(name)
  if t is None:
    return False
  if t.socketio is not None:
    t.socketio.emit('table_closed', data={"reason": f"'{name}' has been closed ({reason})"}, room=name)
  del tables[name]
  t._deleted = True
  shutil.rmtree(table_dir(name), ignore_errors=True)
  t.log(f"Removed ({reason})")
  return True

# EMPTY-TABLE AUTO-REMOVAL: A TABLE IS "EMPTY" WHILE WAITING FOR PLAYERS WITH EVERY
# SEAT VACANT. _empty_since (TRANSIENT - RESET BY __init__ LIKE EVERY OTHER GAMEPLAY
# FIELD, SO A GAME-OVER RESET OR AN EXPLICIT REINIT CORRECTLY RESTARTS THE COUNTDOWN
# RATHER THAN INHERITING STALE TIMING) TRACKS WHEN IT BECAME EMPTY; ONCE THAT'S BEEN
# TRUE CONTINUOUSLY FOR TABLE_EMPTY_REAP_S, delete_table() REMOVES IT.
def reap_empty_tables():
  now = datetime.now()
  for name in list(tables.keys()):
    t = tables[name]
    is_empty = t.state_name() == "WAITING FOR PLAYERS" and all(p.name is None for p in t.players)
    if not is_empty:
      t._empty_since = None
      continue
    if t._empty_since is None:
      t._empty_since = now
      continue
    if (now - t._empty_since).total_seconds() > TABLE_EMPTY_REAP_S:
      delete_table(name, f"empty for {TABLE_EMPTY_REAP_S}s+")

class GameStateMachine:

  # FINITE STATE MACHINE

  # STATE 0 : WAITING FOR PLAYERS : gui_sit() by GUI
  # STATE 1 : DEALING : auto_deal() by transition via queue
  # STATE 2 : TAKING BIDS : gui_bid() by GUI
  # STATE 3 : AWARD KITTY : gui_discard() by GUI
  # STATE 4 : PLAY HAND : gui_play() by GUI
  # STATE 5 : AWARD POINTS : auto_points() by transition via queue

  # STRUCTURAL TUNING CONSTANTS (THE SHORT INLINE delay(1..3) PACING LITERALS ARE
  # DELIBERATELY NOT LIFTED - THEY ARE LOCAL DRAMA, NOT BEHAVIOUR)
  GAME_OVER_SCORE = 500       # ±THRESHOLD THAT ENDS THE GAME (WITH SCORES UNEQUAL)
  NEW_HAND_COUNTDOWN_S = 10   # LIVE-COUNTDOWN PAUSE BETWEEN HANDS
  GAME_OVER_LINGER_S = 10     # HOLD THE FINISHED-GAME SCREEN BEFORE THE RESET COUNTDOWN
  GAME_RESET_COUNTDOWN_S = 10 # LIVE COUNTDOWN TO THE FRESH GAME AFTER GAME OVER
  PUSH_KEEPALIVE_S = 10       # RE-PUSH STATE IF CLIENTS HEARD NOTHING FOR THIS LONG

  def __init__(self, name="W500.GX", socketio_init=None):
    
    self.states = [
      "WAITING FOR PLAYERS", # PLAYERS ARE CONNECTING AND SITTING
      "DEALING",             # THE DEALER IS DEALING CARDS (ANIMATION)
      "TAKING BIDS",         # THE BIDDING STAGE
      "AWARD KITTY",         # THE WINNING BIDDER GETS THE KITTY AND THROW EXCESS CARDS
      "PLAY HAND",           # DURING THE GAME (TRICK TAKING PHASE)
      "AWARD POINTS"         # AFTER TRICK TAKING, SCORES ARE UPDATED
    ]
    self.state = 0

    self.name = name
    self.started_at = datetime.now().strftime("%Y%m%dT%H%M%S")
    self.game_dialog = "" # LATEST CLIENT-FACING MESSAGE - SET ONLY VIA THE dlg_* BUILDERS
    self.socketio = socketio_init
    self.last_push = datetime.now() # DRIVES THE 10s KEEP-ALIVE RE-PUSH
    # EMPTY-TABLE REAP TRACKING (reap_empty_tables IN THIS MODULE) - TRANSIENT, RESET
    # HERE UNCONDITIONALLY LIKE EVERY OTHER GAMEPLAY FIELD (UNLIKE autosave_path BELOW):
    # __init__ ALSO CLEARS EVERY SEAT, SO A FRESH TABLE, AN ADMIN REINIT, AND A GAME-OVER
    # AUTO-RESET ALL CORRECTLY RESTART THE 5-MINUTE COUNTDOWN FROM THIS EXACT MOMENT
    self._empty_since = None

    # SAVE-FILE LOCATIONS - SET BY THE TABLE REGISTRY (create_table/load_tables_from_disk)
    # RIGHT AFTER CONSTRUCTION, NOT A CONSTRUCTOR PARAM (SAME PATTERN AS self.socketio
    # BEING RE-ASSIGNED POST-CONSTRUCTION IN main.py). DEFAULT None (ONLY IF NOT ALREADY
    # SET) SO to_dict() NEVER KeyErrors ON A GameStateMachine CONSTRUCTED OUTSIDE THE
    # REGISTRY - BUT __init__ ALSO RUNS IN PLACE FOR RESET (API/REINIT, GAME-OVER), SO
    # AN ALREADY-REGISTERED TABLE MUST KEEP ITS PATHS ACROSS THAT, NOT HAVE THEM WIPED
    if not hasattr(self, "autosave_path"):
      self.autosave_path = None
    if not hasattr(self, "checkpoint_path"):
      self.checkpoint_path = None

    # SEAT ORDER IS PLAY ORDER (CLOCKWISE). TEAMS ARE SEAT PARITY: 0+2 vs 1+3.
    # bid.won DOUBLES AS THIS HAND'S TRICK COUNT; table IS THE CARD CURRENTLY PLAYED.
    self.players = [
      dd({"name": None, "hand": None, "table": None, "kitty": None, "bid": {"suit": None, "tricks": None, "won": None, "passed": False}}),
      dd({"name": None, "hand": None, "table": None, "kitty": None, "bid": {"suit": None, "tricks": None, "won": None, "passed": False}}),
      dd({"name": None, "hand": None, "table": None, "kitty": None, "bid": {"suit": None, "tricks": None, "won": None, "passed": False}}),
      dd({"name": None, "hand": None, "table": None, "kitty": None, "bid": {"suit": None, "tricks": None, "won": None, "passed": False}})
    ]
    self.teams = [dd({"score": 0, "history": []}),dd({"score": 0, "history": []})]
    self.player_focus = None # SEAT 0-3; WHOSE TURN IT IS, OR None FOR "NO-ONE MAY ACT"
    self.dealer = None       # SEAT 0-3; ROTATES EACH HAND, PICKED AT RANDOM FOR THE FIRST

    self.deck = Deck(fill=True)
    self.kitty = None

    self.trumps = None     # CONTRACT SUIT: 1-4, 5 NO TRUMPS, 0 MISÈRE, None BEFORE THE BID LOCKS
    self.trick_suit = None # SUIT THAT MUST BE FOLLOWED THIS TRICK; None UNTIL SOMEONE LEADS
    self.sitting_out = None # MISÈRE: SEAT INDEX OF THE CONTRACTOR'S PARTNER (PLAYS NO PART IN THE HAND)
    # NO TRUMPS / MISÈRE JOKER-LEAD RULES (PAGAT): LEADING THE JOKER REQUIRES NOMINATING A
    # SUIT NOT YET LED THIS HAND (ILLEGAL ONCE ALL FOUR HAVE BEEN LED, EXCEPT THE LAST TRICK)
    self.suits_led = []           # SUITS (1-4) LED SO FAR THIS HAND
    self.joker_nominating = False # TRUE WHILE THE JOKER LEADER IS CHOOSING A SUIT
    self.joker_nomination = None  # SUIT (1-4) THE OTHERS MUST FOLLOW THIS TRICK
    # CONTRACTOR'S PRE-LEAD NOMINATION (PAGAT): A CONTRACTOR HOLDING THE JOKER MAY, BEFORE
    # THE LEAD TO THE FIRST TRICK, DECLARE IT THE HIGHEST CARD OF A SUIT FOR THE WHOLE HAND
    self.joker_prenom = None       # SUIT (1-4) THE JOKER BELONGS TO ALL HAND, OR None
    self.joker_prenom_open = False # TRUE WHILE THE CONTRACTOR MAY STILL PRE-NOMINATE
    self.table_stale = False       # TRUE WHILE THE WON TRICK LINGERS ON THE TABLE; CLEARED AT THE NEXT LEAD

    # PUBLIC EVENT LOGS OF THE CURRENT HAND. THE PLAYER BOTS' ONLY SOURCE OF "WHAT HAS
    # HAPPENED" (bots.build_view) - THEY MUST NEVER READ HIDDEN STATE, ONLY WHAT A HUMAN
    # AT THE TABLE HAS SEEN. ALL PLAIN JSON, SO to_dict()/restore_state() CARRY THEM AND
    # CLIENTS COULD RENDER THEM LATER. ALL THREE RESET IN auto_deal (NEW HAND).
    self.bid_history = []   # EVERY BID IN ORDER: {"seat", "pass", "suit", "tricks"}
    self.trick_history = [] # COMPLETED TRICKS: {"cards": [{"seat","suit","rank"}...], "winner", "trick_suit", "joker_nomination"}
    self.current_trick = [] # CARDS TABLED SO FAR THIS TRICK, IN PLAY ORDER (MOVES TO trick_history WHEN WON)

    # PLAYER BOTS SEATED VIA THE LOBBY'S ADD BOTS BUTTON: {seat: {"name", "personality"}}.
    # PLAIN JSON OWNED BY bots.py (THE GAME NEVER READS INSIDE IT) - KEPT ON THE GAME SO
    # to_dict()/restore_state() PERSIST BOTS ACROSS SERVICE RESTARTS FOR FREE. RESET BY
    # __init__ MEANS BOTS EVAPORATE AT GAME END / REINIT.
    self.player_bots = {}

    # ADMIN: temp modes' controls (TOGGLED VIA /admin/test AND /admin/skipdelays)
    self.test_mode = False   # SEATS BOTS AND LETS THEM ACT ON EVERY PUSH
    self.skip_delays = False # MAKES self.delay() A NO-OP, SO A HAND PLAYS OUT INSTANTLY
    self.debug_mode = True   # GATES self.debug() - THE STATE-TRANSITION TRACE

    self.log(f"** Game (Re)initialised. **", color=applog.RED)
    self.log(f"Starting in S{self.state} '{self.state_name()}'", color=applog.RED)
    self.dlg_waiting_for_players()

  # THE WHOLE GAME AS PLAIN JSON-ABLE DATA - USED FOR BOTH THE CLIENT PUSH AND THE SAVE
  # FILES, SO EVERY FIELD ADDED HERE MUST ALSO BE READ BACK IN restore_state().
  # NOTE: THIS SENDS EVERY PLAYER'S HAND TO EVERY CLIENT - HIDING OPPONENTS' CARDS IS
  # PURELY A CLIENT-SIDE RENDERING CONCERN.
  def to_dict(self):
    dictionary = dd({attr:value for attr, value in self.__dict__.items()})
    dictionary.deck = self.deck.__dict__() if type(self.deck) != type(None) else None
    dictionary.kitty = self.kitty.__dict__() if type(self.kitty) != type(None) else None
    dictionary.players = []
    for player_index, player in enumerate(self.players):
      if type(player) != type(None):
        dictionary.players.append(dd({
          "hand": player["hand"].__dict__() if type(player['hand']) != type(None) else None,
          "kitty": player["kitty"].__dict__() if type(player['kitty']) != type(None) else None,
          "table": player["table"].__dict__() if type(player['table']) != type(None) else None,
          "bid": player['bid'],
          "name": player['name'],
          "legal_plays": self.legal_play_indices(player_index), # CLIENT DARKENS CARDS NOT IN THIS LIST
        }))
    del dictionary["socketio"]  # NOT SERIALISABLE
    del dictionary["last_push"] # PER-PROCESS TIMING, MEANINGLESS TO CLIENTS AND SAVES
    del dictionary["autosave_path"]   # DEPLOYMENT DETAIL, SET BY THE TABLE REGISTRY,
    del dictionary["checkpoint_path"] # NEVER PART OF THE GAME DATA ITSELF
    # UNDERSCORE-PREFIXED FIELDS ARE TRANSIENT BY CONVENTION (E.G. check_focus_idle's
    # _focus_* TRACKING, WHICH HOLDS A datetime) - NEVER PUSHED TO CLIENTS OR SAVED
    for key in [k for k in dictionary if k.startswith("_")]:
      del dictionary[key]
    return dictionary
  def state_name(self):
    if len(self.states) > self.state:
      return self.states[self.state]
    return "UNDEFINED STATE"

  # WRITES THE CURRENT STATE TO path (ATOMICALLY - TEMP FILE THEN RENAME)
  def save_state(self, path, reason=""):
    # [AUTOSAVE] TAG SPECIFICALLY FOR self.autosave_path WRITES (VS THE MANUAL/ADMIN
    # CHECKPOINT PATH, WHICH KEEPS THE GENERIC [LOG] TAG) - autosave() FIRES ON EVERY
    # move_state() TRANSITION, SO IT'S BY FAR THE NOISIEST save_state() CALLER
    tag = "AUTOSAVE" if path == self.autosave_path else "LOG"
    try:
      snapshot = self.to_dict()
      snapshot["save_version"] = SAVE_VERSION
      os.makedirs(os.path.dirname(path), exist_ok=True)
      # UNIQUE PER CALL (NOT JUST path + ".tmp") - move_state() AUTOSAVES ON THE WORKER
      # THREAD WHILE EVERY SOCKET HANDLER IN main.py ALSO AUTOSAVES RIGHT AFTER ITS
      # gui_* CALL, ON ITS OWN THREAD. TWO CONCURRENT SAVES TO THE SAME TABLE SHARING ONE
      # FIXED .tmp PATH COULD CLOBBER EACH OTHER MID-WRITE OR RACE os.replace() (SEEN AS
      # A LOGGED "SAVE FAILED ... No such file or directory", SELF-HEALING BUT WORTH
      # CLOSING PROPERLY) - A UNIQUE SUFFIX MEANS CONCURRENT WRITERS NEVER SHARE A PATH.
      tmp_path = f"{path}.{uuid.uuid4().hex}.tmp"
      with open(tmp_path, "w") as f:
        json.dump(snapshot, f)
      os.replace(tmp_path, path)
      self.log(f"Saved state to {os.path.basename(path)}. {reason}", tag=tag)
    except Exception as e:
      self.log(f"SAVE FAILED for {os.path.basename(path)}: {e}", tag=tag)

  # PERSISTENCE LAYER: CALLED AT EVERY SAFE CHECKPOINT SO A SERVICE RESTART CAN RESUME THE GAME
  def autosave(self):
    if self.autosave_path:
      self.save_state(self.autosave_path)

  def clear_autosave(self):
    if self.autosave_path and os.path.exists(self.autosave_path):
      os.remove(self.autosave_path)
      self.log(f"Cleared {os.path.basename(self.autosave_path)}.", tag="AUTOSAVE")

  # REBUILDS THE GAME IN PLACE FROM A SAVE FILE, RE-QUEUES ANY PENDING AUTO WORK, PUSHES.
  # RETURNS FALSE (AND LEAVES STATE UNTOUCHED) IF THE FILE IS MISSING/CORRUPT/WRONG VERSION.
  def restore_state(self, path):
    try:
      with open(path) as f:
        data = json.load(f)
      if data.get("save_version") != SAVE_VERSION:
        raise ValueError(f"unsupported save_version {data.get('save_version')}")

      def deck_from(deck_data):
        if deck_data == None: return None
        deck = Deck(fill=False)
        for c in deck_data["cards"]:
          deck.add_card(Card(c["suit"], c["rank"]))
        return deck
      def card_from(card_data):
        if card_data == None: return None
        return Card(card_data["suit"], card_data["rank"])

      # BUILD EVERYTHING BEFORE TOUCHING self SO A BAD FILE CAN'T HALF-RESTORE
      new_players = []
      for p in data["players"]:
        new_players.append(dd({
          "name": p["name"],
          "hand": deck_from(p["hand"]),
          "kitty": deck_from(p["kitty"]),
          "table": card_from(p["table"]),
          "bid": dd(p["bid"]),
        }))
      new_teams = [dd({"score": t["score"], "history": [dd(h) for h in t["history"]]}) for t in data["teams"]]
      new_deck = deck_from(data["deck"])
      new_kitty = deck_from(data["kitty"])

      self.state = data["state"]
      # NOTE: self.name IS DELIBERATELY NOT RESTORED FROM data["name"] - THE INSTANCE'S
      # NAME IS SET ONCE BY THE TABLE REGISTRY AT CONSTRUCTION (IT DOUBLES AS THE
      # Socket.IO ROOM NAME AND THE data/tables/<name>/ SAVE-DIRECTORY NAME), AND MUST
      # NEVER CHANGE FROM A RESTORE - data["name"] IS KEPT IN THE SAVE FILE FOR
      # READABILITY ONLY
      self.game_dialog = data["game_dialog"]
      self.started_at = data["started_at"]
      self.player_focus = data["player_focus"]
      self.dealer = data["dealer"]
      self.trumps = data["trumps"]
      self.trick_suit = data["trick_suit"]
      self.sitting_out = data.get("sitting_out", None)
      self.suits_led = data.get("suits_led", [])
      self.joker_nominating = data.get("joker_nominating", False)
      self.joker_nomination = data.get("joker_nomination", None)
      self.joker_prenom = data.get("joker_prenom", None)
      self.joker_prenom_open = data.get("joker_prenom_open", False)
      self.table_stale = data.get("table_stale", False)
      self.test_mode = data.get("test_mode", False)
      self.skip_delays = data.get("skip_delays", False)
      # PUBLIC EVENT LOGS + PLAYER BOTS - .get DEFAULTS KEEP OLDER SAVES LOADABLE WITHOUT
      # A SAVE_VERSION BUMP (SAME TOLERANT PATTERN AS THE JOKER FIELDS ABOVE). AN OLD
      # SAVE JUST RESUMES WITH EMPTY LOGS / NO BOTS
      self.bid_history = data.get("bid_history", [])
      self.trick_history = data.get("trick_history", [])
      self.current_trick = data.get("current_trick", [])
      # JSON TURNS THE INT SEAT KEYS INTO STRINGS - CONVERT BACK SO player_bots ALWAYS
      # HAS INT KEYS IN MEMORY (bots.py LOOKS UP BY game.player_focus, AN INT)
      self.player_bots = {int(seat): blob for seat, blob in (data.get("player_bots") or {}).items()}
      for i, player in enumerate(new_players):
        self.players[i] = player
      for i, team in enumerate(new_teams):
        self.teams[i] = team
      self.deck = new_deck
      self.kitty = new_kitty
    except Exception as e:
      self.log(f"RESTORE FAILED from {os.path.basename(path)}: {e}", tag="AUTOSAVE")
      return False

    self.log(f"Restored state from {os.path.basename(path)}: S{self.state} '{self.state_name()}'", tag="AUTOSAVE")

    # RE-QUEUE / REDO WORK THE OLD PROCESS HAD PENDING - OTHER GUI-DRIVEN STATES NEED NOTHING
    if self.state_name() == "DEALING":
      schedule_t.jobqueue.put(self.auto_deal) # re-deal from scratch (hand hadn't started)
    elif self.state_name() == "AWARD POINTS":
      if len(list(filter(lambda player: player.bid.tricks != None, self.players))):
        schedule_t.jobqueue.put(self.auto_points) # bids not yet cleared -> scoring never ran
      else:
        schedule_t.jobqueue.put(self.state_trans) # scoring done -> continue to next hand / game end
    elif self.state_name() == "AWARD KITTY":
      # THE S2->S3 AUTOSAVE FIRES IN move_state(3) BEFORE state_trans AWARDS THE KITTY /
      # SETS TRUMPS / SETS FOCUS - REDO THAT SETUP DETERMINISTICALLY FROM THE BIDS
      for player_index, player in enumerate(self.players):
        if player.bid.passed == False and player.bid.suit != None: # the contract winner
          if len(self.kitty.cards): # kitty never handed over - redo the award
            self.kitty.move_cards(player.kitty, 3)
            self.trumps = player.bid.suit
            self.sitting_out = (player_index + 2) % 4 if self.trumps == 0 else None
            for player_i in self.players:
              player_i.kitty.sort(self.trumps)
              player_i.hand.sort(self.trumps)
            self.player_focus = player_index
            self.dlg_trumps_now_discard(self.trumps, player.name)
          elif len(player.kitty.cards) == 0: # discard already submitted - move the game on
            schedule_t.jobqueue.put(self.state_trans)
          else: # kitty held, discard pending (manual checkpoint) - ensure the contractor is focused
            self.player_focus = player_index
          break

    self.autosave() # a restore (plus any redo above) is now the latest state - persist it

    self.sio_push()
    return True
  def delay(self, seconds):
    if not self.skip_delays:
      sleep(seconds)
  def countdown(self, seconds, dlg_fn):
    # LIVE COUNTDOWN: RE-ISSUE THE DIALOG EACH SECOND (EVERY dialog() CALL PUSHES)
    for remaining in range(seconds, 0, -1):
      dlg_fn(remaining)
      self.delay(1)
  def log(self, message, color="", tag="LOG"):
    applog.tabled(self.name, f"[{tag}] {message}", color=color)
  def debug(self, message, color=applog.RED):
    if self.debug_mode:
        applog.tabled(self.name, f"[DEBUG] {message}", color=color)

  def dialog(self, message, color=applog.BOLD + applog.GREEN):
    self.game_dialog = message
    applog.tabled(self.name, f"[DIALOG] {message}", color=color)
    self.sio_push()
  def move_state(self, tgt):
    self.state = tgt
    applog.tabled(self.name, f"[TRANSITION] to S{self.state}: '{self.state_name()}'", color=applog.RED)
    self.autosave() # every transition is a persistence checkpoint
    self.sio_push()
  # SEAT INDEX OF THE CONTRACT HOLDER (THE ONLY UN-PASSED PLAYER ONCE THE CONTRACT LOCKS)
  def contractor_index(self):
    for i, player in enumerate(self.players):
      if player.bid.suit != None and player.bid.passed == False:
        return i
    return None

  # NEXT SEAT CLOCKWISE, SKIPPING A SAT-OUT MISÈRE PARTNER
  def next_seat(self, seat):
    nxt = (seat + 1) % 4
    if nxt == self.sitting_out:
      nxt = (nxt + 1) % 4
    return nxt

  def hand_cards_status(self):
      count = 0
      max_count = 0
      min_count = 1e20
      active_players = [p for i, p in enumerate(self.players) if i != self.sitting_out]
      for player in active_players:
        if player.hand == None:
          min_count = 0
          break
        count += len(player.hand.cards)
        if len(player.hand.cards) > max_count:
          max_count = len(player.hand.cards)
        if len(player.hand.cards) < min_count:
          min_count = len(player.hand.cards)
      total_count = count
      average_count = total_count/len(active_players)
      count_variance = max_count - min_count

      stats = {
        "total": total_count,
        "average": average_count,
        "min": min_count,
        "max": max_count,
        "variance": count_variance
      }

      # HAND SIZES ALONE TELL US WHERE WE ARE: EVERYONE EQUAL MEANS NO PART-PLAYED
      # TRICK, A SPREAD OF ONE MEANS SOME HAVE PLAYED AND SOME HAVEN'T. ANYTHING
      # WIDER IS IMPOSSIBLE AND MEANS THE STATE IS CORRUPT.
      if stats["total"] == 0:
        return "hand complete"
      if stats["variance"] == 0:
        return "between tricks"
      if stats["variance"] == 1:
        return "trick in progress"
      return "error"

  # THE ONLY PLACE STATE CHANGES ARE DECIDED. INSPECTS THE CURRENT STATE, WORKS OUT
  # WHETHER IT IS TIME TO MOVE, AND FALLS THROUGH DOING NOTHING IF NOT. ALWAYS QUEUED
  # ONTO THE WORKER RATHER THAN CALLED DIRECTLY.
  def state_trans(self):

    # CHECK FOR S0 -> S1
    if self.state_name() == "WAITING FOR PLAYERS":
      no_empty_seats = not len(list(filter(lambda player: player.name == None, self.players)))
      if no_empty_seats: # ALL THE PLAYERS ARE HERE - next state
        self.debug(f"{self.state_name()} : NO EMPTY SEATS -> TRANSITION TO S1")
        schedule_t.jobqueue.put(self.auto_deal)
        self.move_state(1)
        return
      else:
        self.debug(f"{self.state_name()} : STILL EMPTY SEATS -> NO TRANSITION")
        return
    
    # CHECK FOR S1 -> S2
    elif self.state_name() == "DEALING":
      if self.player_focus != None: # happens when deal is complete.        
        self.debug(f"{self.state_name()} : PLAYER HAS FOCUS -> TRANSITION TO S2")
        self.move_state(2)
        self.dlg_waiting_for_bid(self.players[self.player_focus].name)
        return
      else:        
        self.debug(f"{self.state_name()} : NO PLAYER HAS FOCUS -> NO TRANSITION")
        return

    # CHECK FOR S2 -> S3 or S2 -> S1
    elif self.state_name() == "TAKING BIDS":
      for player_index, player in enumerate(self.players):
        if player.bid.passed == "WINNER":
          
          self.debug(f"{self.state_name()} : WINNING BIDDER -> TRANSITION TO S3")

          self.player_focus = None
          player.bid.passed = False
          self.move_state(3)

          self.delay(2)
                
          self.kitty.move_cards(player.kitty, 3)
          self.trumps = player.bid.suit
          self.sitting_out = (player_index + 2) % 4 if self.trumps == 0 else None # MISÈRE: PARTNER PLAYS NO PART
          for player_i in self.players:
            player_i.kitty.sort(self.trumps)
            player_i.hand.sort(self.trumps)
          self.dlg_gets_kitty(player.name)

          self.delay(2)

          self.player_focus = player_index # notify winner to discard
          self.dlg_trumps_now_discard(self.trumps, player.name)

          return
        
      if len(list(filter(lambda player: player.bid.passed == True, self.players))) == len(self.players): # ALL PASSED. (== True EXCLUDES THE "WINNER..." FLAG VALUES)
        
        self.debug(f"{self.state_name()} : NO-ONE BID -> TRANSITION TO S1")
        for player_i in self.players:
          player_i.bid = {"suit": None, "tricks": None, "won": None, "passed": False}                    
        schedule_t.jobqueue.put(self.auto_deal)
        self.move_state(1)
        return
      
      else:
        self.debug(f"{self.state_name()} : STILL BIDDING -> NO TRANSITION")
        return

    # CHECK FOR S3 -> S4
    elif self.state_name() == "AWARD KITTY":
      for player in self.players:
        if player.kitty:
          if len(player.kitty.cards):            
            self.debug(f"{self.state_name()} : PLAYER STILL HAS EXTRA CARDS -> NO TRANSITION")
            return
      self.debug(f"{self.state_name()} : NO-ONE HAS EXTRA CARDS -> TRANSITION TO S4")
      # NO TRUMPS / MISÈRE: A CONTRACTOR HOLDING THE JOKER MAY PRE-NOMINATE ITS SUIT
      # BEFORE LEADING TO THE FIRST TRICK - OPEN THAT WINDOW NOW (CLOSES ON FIRST LEAD)
      if self.trumps in (0, 5):
        contractor = self.contractor_index()
        if contractor != None and len(list(filter(lambda card: card.rank == 15, self.players[contractor].hand.cards))):
          self.joker_prenom_open = True
          self.log(f"{self.players[contractor].name} holds the Joker - pre-nomination available until the first lead.", color=applog.RED)
      self.dlg_first_lead(self.players[self.player_focus].name, self.trumps)
      self.move_state(4)
      return

    # CHECK FOR S4 -> S5
    elif self.state_name() == "PLAY HAND":
      # MISÈRE FAILS THE MOMENT THE CONTRACTOR WINS A TRICK - THE HAND ENDS IMMEDIATELY
      misere_failed = False
      if self.trumps == 0:
        contractor = self.contractor_index()
        misere_failed = contractor != None and (self.players[contractor].bid.won or 0) > 0
      if self.hand_cards_status() == "hand complete" or misere_failed: # transition if hand complete

        self.debug(f"{self.state_name()} : HAND IS COMPLETE -> TRANSITION TO S5")
        
        self.player_focus = None
        self.move_state(5)
        schedule_t.jobqueue.put(self.auto_points)        
        return
    
    # CHECK FOR S5 -> S6 or S5 -> S1
    elif self.state_name() == "AWARD POINTS":
      # GAME ENDS AT ≥500 (WIN) OR ≤-500 ("OUT THE BACK DOOR" - THAT TEAM LOSES), SCORES UNEQUAL
      scores = [self.teams[0].score, self.teams[1].score]
      if (max(scores) < self.GAME_OVER_SCORE and min(scores) > -self.GAME_OVER_SCORE) or scores[0] == scores[1]: # NOT FINISHED.
        # NEW HAND
        self.countdown(self.NEW_HAND_COUNTDOWN_S, self.dlg_new_hand_soon)

        self.move_state(1)
        schedule_t.jobqueue.put(self.auto_deal)

      else: # GAME OVER

        winning_team = int(self.teams[1].score > self.teams[0].score)

        self.dlg_game_finished(self.players[winning_team].name, self.players[winning_team+2].name, back_door=min(scores) <= -self.GAME_OVER_SCORE)
        games.append(self.to_dict())
        self.delay(self.GAME_OVER_LINGER_S)

        self.countdown(self.GAME_RESET_COUNTDOWN_S, self.dlg_game_reset_soon)

        self.__init__(self.name, socketio_init=self.socketio)
        self.autosave() # autosave becomes a fresh S0 game, not the finished one

    pass # FELL THROUGH EVERY CHECK - STAY PUT
      
  # PUSHES THE ENTIRE GAME STATE TO EVERY CLIENT AT THIS TABLE (room=self.name - THE
  # Socket.IO ROOM NAME DOUBLES AS THE TABLE NAME). THERE ARE NO PER-EVENT DELTAS -
  # CLIENTS RE-RENDER FROM EACH FULL SNAPSHOT.
  # _deleted (SET BY delete_table) STOPS HERE, NOT JUST AT THE REGISTRY: THIS IS THE
  # ONE CHOKEPOINT EVERY BOT-DRIVEN SELF-PERPETUATION RUNS THROUGH (bot_check IS ONLY ever
  # QUEUED FROM THIS METHOD), SO SKIPPING IT ENTIRELY BOTH STOPS A DELETED TABLE FROM
  # BROADCASTING INTO ITS OLD (POSSIBLY REUSED) ROOM AND STARVES ITS BOTS OF ANY FURTHER
  # TURN - THE DEAD OBJECT SIMPLY STOPS, IT DOESN'T NEED AN EXPLICIT KILL SWITCH ANYWHERE ELSE.
  def sio_push(self):
    if getattr(self, "_deleted", False):
      return
    if self.socketio is not None:
      self.socketio.emit('game_state', data={**self.to_dict()}, room=self.name)
      self.last_push = datetime.now()
    else:
      pass # NO SOCKET (HEADLESS/TEST USE) - NOTHING TO PUSH TO
    # EVERY STATE CHANGE GIVES WHICHEVER BOT HOLDS THE FOCUSED SEAT A CHANCE TO ACT.
    # bots.bot_check ROUTES ADMIN TEST-MODE BOTS vs SEATED PLAYER BOTS; THE GUARD SKIPS
    # THE QUEUE CHURN ENTIRELY WHEN NO BOTS EXIST (THE COMMON HUMANS-ONLY GAME)
    if self.test_mode or self.player_bots:
      schedule_t.jobqueue.put(lambda: bots.bot_check(self))

  # OCCASIONAL SERVER-SENT NOTICE (CHECKPOINT SAVED, RESTART IMMINENT, ...) SHOWN AS A
  # CLIENT TOAST. DELIBERATELY A DEDICATED EVENT, NOT PART OF THE game_state PUSH: THE
  # 10s KEEP-ALIVE RE-PUSHES FULL STATE, SO A TOAST EMBEDDED THERE WOULD REPLAY.
  # category IS A BOLD ALL-CAPS HEADING RENDERED ABOVE THE MESSAGE (SERVER ERROR /
  # SERVER / GAME MANAGEMENT / PLAYERS). audience="admin" IS COSMETIC CLIENT-SIDE
  # FILTERING ONLY (THE SESSION_IS_ADMIN FLAG) - CURRENTLY UNUSED (ALL TOASTS GO TO
  # EVERYONE AT THIS TABLE) BUT KEPT FOR FUTURE USE. NOTHING SENSITIVE MAY EVER RIDE
  # IN A TOAST. room=self.name SCOPES IT TO THIS TABLE ONLY - schedule_t.on_error IS
  # THE ONE DELIBERATE EXCEPTION (SEE main.py), STILL A PROCESS-WIDE BROADCAST.
  def sio_toast(self, text, kind="info", seconds=4, audience=None, category=None, logit=True):
    if logit:
      self.log(f"{kind}: {(category + ': ') if category else ''}{text}", color=applog.MAGENTA, tag="TOAST")
    if self.socketio is not None:
      self.socketio.emit('toast', data={"text": text, "kind": kind, "seconds": seconds,
                                        "audience": audience, "category": category}, room=self.name)

  # ONCE-A-SECOND POLL (QUEUED FROM main.py LIKE is_push_needed): WHEN A HUMAN HAS
  # HELD FOCUS IN A GUI-DRIVEN STATE FOR FOCUS_NUDGE_S, NUDGE THEM WITH A TOAST -
  # REPEATING EVERY FOCUS_NUDGE_S UNTIL FOCUS OR STATE MOVES. THE TRACKING FIELDS
  # SELF-INITIALISE ON FIRST POLL: TRANSIENT BY DESIGN, NEVER SAVED/RESTORED.
  # WALL-CLOCK TIMESTAMPS, SO THE POLL SURVIVES QUEUING BURSTS BEHIND WORKER SLEEPS.
  FOCUS_NUDGE_S = 10
  def check_focus_idle(self):
    now = datetime.now()
    state_name = self.state_name()
    # A CHEAP, STRICTLY-INCREASING-PER-TURN ORDINAL WITHIN THE CURRENT HAND, FOLDED
    # INTO THE STINT KEY BELOW. PLAIN (player_focus, state) ISN'T UNIQUE ENOUGH ON ITS
    # OWN: A SEAT CAN LEGITIMATELY REGAIN FOCUS SEVERAL TIMES IN THE SAME STATE
    # (ANOTHER BIDDING ROUND ON A REMAINING BIDDER; ONE TURN PER TRICK DURING PLAY).
    # NORMALLY EVERY INTERVENING SEAT'S TURN PRODUCES A DIFFERENT KEY AND RE-ARMS THE
    # TIMER, BUT THE SHARED MULTI-TABLE WORKER (ONE THREAD SERIALISING EVERY TABLE'S
    # WORK, SEE poll_all_tables) CAN NOW LEAVE THIS ONCE-A-SECOND POLL BACKLOGGED LONG
    # ENOUGH TO SKIP RIGHT OVER AN ENTIRE INTERVENING VISIT WHEN OTHER TABLES ARE BUSY
    # - LANDING BACK ON A KEY THAT COINCIDENTALLY MATCHES ONE FROM SEVERAL TURNS AGO,
    # INHERITING ITS STALE _focus_since AND NUDGING ALMOST INSTANTLY. THE ORDINAL BELOW
    # MAKES EVERY VISIT UNIQUE REGARDLESS OF HOW MANY POLLS GOT SKIPPED.
    if state_name == "TAKING BIDS":
      turn_ordinal = len(self.bid_history)
    elif state_name == "PLAY HAND":
      turn_ordinal = len(self.trick_history) * 4 + len(self.current_trick)
    else:
      turn_ordinal = 0
    key = (self.player_focus, self.state, turn_ordinal) # focus pointer + state + turn identify a "stint"
    if key != getattr(self, "_focus_key", None):
      self._focus_key, self._focus_since = key, now
      return
    if self.player_focus == None:
      return
    if state_name not in ("TAKING BIDS", "AWARD KITTY", "PLAY HAND"):
      return
    name = self.players[self.player_focus].name
    if self.player_focus in self.player_bots or bots.is_dev_random_bot(name):
      return # BOTS PACE THEMSELVES - ONLY HUMANS GET NUDGED
    if (now - self._focus_since).total_seconds() >= self.FOCUS_NUDGE_S:
      self._focus_since = now # RE-ARM: NUDGE AGAIN AFTER ANOTHER FOCUS_NUDGE_S
      self.sio_toast(f"Are you there {name}?", category="PLAYERS", logit=False)

  # KEEP-ALIVE, POLLED ONCE A SECOND: RE-PUSHES ONLY IF THE CLIENTS HAVE HEARD NOTHING
  # FOR 10s, SO A CLIENT THAT MISSED A PUSH RE-SYNCS WITHOUT NEEDING A REFRESH.
  def is_push_needed(self):
    if (datetime.now() - self.last_push).seconds > self.PUSH_KEEPALIVE_S:
      self.sio_push()

  # POINTS A BID IS WORTH (THE STANDARD AVONDALE TABLE). RETURNS 0 FOR ANY COMBINATION
  # THAT ISN'T A REAL BID, WHICH IS ALSO HOW gui_bid's bid_rank() SPOTS A BOGUS BID.
  def get_score(self, tricks, suit):

    #                1         2         3         4          5         0
    # Tricks       Spades    Clubs    Diamonds   Hearts   No Trumps   Misere
    # Six            40        60        80       100        120
    # Seven         140       160       180       200        220
    # Misere                                                           250
    # Eight         240       260       280       300        320
    # Nine          340       360       380       400        420
    # Ten           440       460       480
    # Ten                                         500        520

    scores = {
      6: { 1: 40, 2: 60, 3: 80, 4: 100, 5: 120 },
      7: { 1: 140, 2: 160, 3: 180, 4: 200, 5: 220 },
      8: { 1: 240, 2: 260, 3: 280, 4: 300, 5: 320 },
      9: { 1: 340, 2: 360, 3: 380, 4: 400, 5: 420 },
      10: { 0: 250, 1: 440, 2: 460, 3: 480, 4: 500, 5: 520 },
    }

    if tricks in scores:
      if suit in scores[tricks]:
        return scores[tricks][suit]
    return 0

  # DIALOG BUILDERS - ALL CLIENT-FACING MESSAGE TEXT LIVES HERE.
  # GAME LOGIC MUST NEVER DEPEND ON DIALOG TEXT - USE STATE FLAGS (E.G. bid.passed) INSTEAD.
  def dlg_waiting_for_players(self):
    self.dialog(f"Waiting for players.")
  def dlg_welcome(self, name):
    self.dialog(f"Welcome, {name}!")
  def dlg_dealer(self, name, randomly_chosen=False):
    if randomly_chosen:
      self.dialog(f"{name} is the dealer (randomly chosen).")
    else:
      self.dialog(f"{name} is the dealer.")
  def dlg_dealing_complete(self):
    self.dialog(f"Dealing complete.")
  def dlg_waiting_for_bid(self, name):
    self.dialog(f"Waiting for {name}'s bid.")
  def dlg_passed_no_bids_redeal(self, name):
    self.dialog(f"{name} passed. No-one bid... Re-deal.")
  def dlg_passed_next_bidder(self, name, next_name):
    self.dialog(f"{name} passed. {next_name}'s bid.")
  def dlg_bid_next_bidder(self, name, tricks, suit, next_name):
    self.dialog(f"{name} bid {tricks} {SUIT_STR[suit]}. {next_name}'s bid.")
  def dlg_bid_increase_option(self, winner_name, passer_name=None):
    if passer_name:
      self.dialog(f"{passer_name} passed. {winner_name} won the bidding... Increase bid?")
    else:
      self.dialog(f"{winner_name} won the bidding... Increase bid?")
  def dlg_bid_increased(self, name, tricks, suit):
    self.dialog(f"{name} increased their bid to {tricks} {SUIT_STR[suit]}.")
  def dlg_bid_not_increased(self, name):
    self.dialog(f"{name} didn't increase their bid.")
  def dlg_gets_kitty(self, name):
    self.dialog(f"{name} gets kitty.")
  def dlg_trumps_now_discard(self, trumps, name):
    if trumps == 0:
      self.dialog(f"Misère! {name} plays alone. {name} to discard 3.")
    else:
      self.dialog(f"{SUIT_STR[trumps]} is trumps. {name} to discard 3.")
  def dlg_first_lead(self, name, trumps):
    if trumps == 0:
      self.dialog(f"{name} to lead - Misère, no trumps.")
    else:
      self.dialog(f"{name} to lead - {SUIT_STR[trumps]} is trumps.")
  def dlg_lead_suit(self, name, suit):
    self.dialog(f"{name} lead {SUIT_STR[suit]}.")
  def dlg_joker_lead_nominating(self, name):
    self.dialog(f"{name} led the Joker - nominating a suit.")
  def dlg_joker_nominated(self, name, suit):
    self.dialog(f"{name} led the Joker - follow {SUIT_STR[suit]} if you can.")
  def dlg_joker_prenominated(self, name, suit):
    self.dialog(f"{name}'s Joker is the highest {SUIT_STR[suit].rstrip('s')} this hand.")
  def dlg_won_trick(self, name):
    self.dialog(f"{name} won that trick.")
  def dlg_next_lead(self, name, trumps):
    if trumps == 0:
      self.dialog(f"{name}'s lead - Misère, no trumps.")
    else:
      self.dialog(f"{name}'s lead - {SUIT_STR[trumps]} is trumps.")
  def dlg_misere_failed(self, name):
    self.dialog(f"{name} won a trick - the Misère has failed!")
  def dlg_hand_complete(self):
    self.dialog(f"Hand complete.")
  def dlg_contract_summary(self, name, partner_name, tricks, suit):
    if suit == 0:
      self.dialog(f"{name} had a Misère contract (playing alone).")
    else:
      self.dialog(f"{name} and {partner_name} had a {tricks} {SUIT_STR[suit]} contract.")
  def dlg_contract_result(self, tricks_won, points):
    if points > 0:
      self.dialog(f"They won {tricks_won} tricks - {points} points awarded.")
    else:
      self.dialog(f"They won {tricks_won} tricks - {-points} points deducted.")
  def dlg_misere_result(self, name, points):
    if points > 0:
      self.dialog(f"Misère made - {points} points awarded.")
    else:
      self.dialog(f"Misère failed - {-points} points deducted.")
  def dlg_opposition_result(self, name, partner_name, tricks_won, points):
    self.dialog(f"{name} and {partner_name} (opposition) won {tricks_won} tricks - {points} points.")
  def dlg_misere_opposition_result(self, name, partner_name):
    self.dialog(f"{name} and {partner_name} score nothing against a Misère.")
  def dlg_scores_first_team(self, name, partner_name, score):
    self.dialog(f"Scores: {name} and {partner_name} on {score} points.")
  def dlg_scores_second_team(self, name, partner_name, score):
    self.dialog(f"{name} and {partner_name} on {score} points.")
  def dlg_new_hand_soon(self, remaining):
    self.dialog(f"New hand will start in {remaining} second{'s' if remaining != 1 else ''}.")
  def dlg_game_finished(self, name, partner_name, back_door=False):
    if back_door:
      self.dialog(f"Game finished - \"Out the back door\" (-{self.GAME_OVER_SCORE})! Congratulations {name} & {partner_name}!")
    else:
      self.dialog(f"Game finished. Congratulations {name} & {partner_name}!")
  def dlg_game_reset_soon(self, remaining):
    self.dialog(f"Game will be reset in {remaining} second{'s' if remaining != 1 else ''}.")

  # STATE 0 FUNCTION CALLED BY GUI
  def gui_sit(self, name, position):
    if self.state_name() == "WAITING FOR PLAYERS": # only applicable to S0

      if any(same_name(name, x.name) for x in self.players):
        self.log(f"{'gui_sit'.ljust(12)}: {name} is already seated.")
        return
         
      if 0 <= position <= 3:
        if self.players[position].name != None:
          self.log(f"{'gui_sit'.ljust(12)}: {self.players[position].name} is already in seat {position}.")
          return
        
      self.players[position].name = name

      self.dlg_welcome(name)
      # HUMAN, NON-ADMIN JOINS ONLY - BOTH BOT KINDS ARE SEATED VIA THIS SAME gui_sit() CALL
      # AND ARE NAME-PREFIXED (SEE bots.py), SO A PREFIX CHECK IS ENOUGH TO SKIP "ADD
      # BOTS"/TEST-MODE SPAM WITHOUT A SEPARATE "IS THIS A BOT" PARAMETER THREADED THROUGH
      # THE CALL. ADMINS ARE EXCLUDED TOO (Dan's CALL) - THE NOTIFICATION IS FOR THE HOST TO
      # KNOW SOMEONE ELSE SHOWED UP, NOT TO PING THEM ABOUT THEIR OWN SEAT.
      if (not str(name).startswith(bots.DEV_RANDOM_BOT_PREFIX)
          and not str(name).startswith(bots.PLAYER_BOT_PREFIX)
          and not _name_is_admin(name)):
        ntfy.send("web500", f"{name} sat down at {self.name}")
      self.player_focus = None
      self.sio_push()
      schedule_t.jobqueue.put(self.state_trans)

  # STATE 1 FUNCTION TRIGGERED BY TRANSITION
  def auto_deal(self):
    if self.state_name() == "DEALING":

      self.player_focus = None # NO-ONE MAY ACT DURING THE DEAL (COVERS ALL ROUTES INTO S1)

      # clear bids
      for player in self.players:
        player.bid = dd({"suit": None, "tricks": None, "won": None, "passed": False})

      self.log(f"No trumps suit.")
      self.trumps = None
      self.sitting_out = None
      self.suits_led = []
      self.joker_nominating = False
      self.joker_nomination = None
      self.joker_prenom = None
      self.joker_prenom_open = False
      self.table_stale = False

      # FRESH HAND = FRESH PUBLIC EVENT LOGS (BIDDING AND TRICKS ARE PER-HAND KNOWLEDGE)
      self.bid_history = []
      self.trick_history = []
      self.current_trick = []

      self.log(f"Created fresh deck.")
      self.deck = Deck(fill=True)

      self.log(f"Shuffled Deck")
      self.deck.shuffle()

      self.log(f"Emptying hands for players and empty kitty")
      for player in self.players:
        player.hand = Deck(fill=False)
        player.kitty = Deck(fill=False)
        player.table = None
      self.kitty = Deck(fill=False)

      # FIRST DEALER IS RANDOM, THEREAFTER IT PASSES CLOCKWISE EACH HAND
      if self.dealer == None:
        self.dealer = randint(0,3)
        self.dlg_dealer(self.players[self.dealer].name, randomly_chosen=True)
      else:
        self.dealer += 1
        self.dealer = self.dealer % 4
        self.dlg_dealer(self.players[self.dealer].name)
      self.delay(3)

      # AUSTRALIAN 500 DEAL: THREE ROUNDS OF 3/4/3 CARDS EACH, STARTING LEFT OF THE
      # DEALER, WITH ONE CARD TO THE KITTY AFTER EACH ROUND (10 EACH + 3 KITTY = 43).
      # PUSH AND PAUSE PER ROUND SO CLIENTS SEE THE DEAL ANIMATE.
      self.log(f"Dealing cards to players")
      for number_cards in [3,4,3]:
        for i in range(1,5):
          dealing_to = (self.dealer + i) % 4
          self.deck.move_cards(self.players[dealing_to].hand, number_cards)
        self.deck.move_cards(self.kitty, 1)
        self.sio_push()
        self.delay(2)

      # NO CONTRACT YET, SO LAY THE CARDS OUT NO-TRUMPS STYLE UNTIL TRUMPS ARE KNOWN
      self.log(f"Sort hands (and kitty) by No Trumps")
      for player in self.players:
        player.hand.sort(5) # SORT BY NO TRUMPS
      self.kitty.sort(5)

      self.dlg_dealing_complete()
      self.delay(1)

      self.player_focus = (self.dealer + 1) % 4 # BIDDING OPENS LEFT OF THE DEALER

      # TRANSITION TO NEXT STATE
      schedule_t.jobqueue.put(self.state_trans)
  
  # STATE 2 FUNCTION CALLED BY GUI
  def gui_bid(self, name, bid):

    # BIDS ARE ORDERED BY RANK, FOLLOWING THE AVONDALE POINTS TABLE: MISÈRE (250) RANKS
    # ABOVE 8 SPADES (240) AND BELOW 8 CLUBS (260), SO OUTBIDDING A MISÈRE TAKES 8 CLUBS
    # OR HIGHER. MISÈRE IS SUIT 0 / TRICKS 10.
    def bid_rank(tricks, suit):
      if tricks == None or suit == None:
        return 0
      if suit == 0:
        return (8 * 6 + 1) * 2 + 1 if tricks == 10 else 0 # between 8 spades and 8 clubs
      if self.get_score(tricks, suit) == 0:
        return 0 # not a real bid
      return (tricks * 6 + suit) * 2

    # (RANK, TRICKS, SUIT) OF THE STRONGEST BID ON THE TABLE - (0, None, None) IF NO BIDS
    def current_highest_bid():
      best = (0, None, None)
      for player in self.players:
        rank = bid_rank(player.bid.tricks, player.bid.suit)
        if rank > best[0]:
          best = (rank, player.bid.tricks, player.bid.suit)
      return best

    def current_highest_bidder_score():
      return current_highest_bid()[0]

    def active_bidders():
      return list(filter(lambda player: player.bid.passed == False, self.players))

    if self.state_name() == "TAKING BIDS" and self.player_focus != None:

      bid=dd(bid)

      # ONCE A WINNER IS CROWNED THE AUCTION IS SETTLED AND ONLY state_trans REMAINS -
      # ANY FURTHER BID/PASS (DOUBLE-CLICK, RE-FIRED BOT CHECK) WOULD RE-OPEN IT AND
      # COULD EVEN REPLACE THE CONTRACT SUIT VIA THE WINNER OUTBIDDING THEMSELVES
      if any(p.bid.passed == "WINNER" for p in self.players):
        self.log(f"{'gui_bid'.ljust(12)}: Bidding already settled - action from {name} ignored.")
        return

      if not any(same_name(name, p.name) for p in self.players):
        self.log(f"{'gui_bid'.ljust(12)}: Attempted bid from non-player ({name}).")
        self.sio_push()
        return
      if not same_name(self.players[self.player_focus].name, name):
        self.log(f"{'gui_bid'.ljust(12)}: Attempted bid from non-focused player ({name}).")
        self.sio_push()
        return

      for player in self.players:
        if same_name(player.name, name):

          # DEAL WITH PASSES FIRST
          if bid['pass']:

            player.bid.passed = True
            # PUBLIC RECORD FOR THE PLAYER BOTS (A PASS CARRIES INFORMATION TOO). THIS
            # ONE APPEND ALSO CATCHES THE WINNER DECLINING THEIR INCREASE OPTION, SINCE
            # THAT ARRIVES AS A PASS AND FLOWS THROUGH HERE
            self.bid_history.append({"seat": self.player_focus, "pass": True, "suit": None, "tricks": None})

            if len(active_bidders()) == 0 and current_highest_bidder_score() == 0: # NO-ONE BID!
              self.player_focus = None
              self.dlg_passed_no_bids_redeal(name)
              self.delay(3)
              schedule_t.jobqueue.put(self.state_trans)

            elif len(active_bidders()) == 1 and current_highest_bidder_score() > 0: # BIDDING WON - SETUP FOR OPTIONAL BID INCREASE
              winner = active_bidders()[0]
              winner_seat = 0
              for player in self.players:
                if winner.name == player.name:
                  self.player_focus = winner_seat
                  break
                else:
                  winner_seat += 1
              winner.bid.passed = "WINNER_INCREASE_OPTION"
              self.dlg_bid_increase_option(winner.name, passer_name=name)

            elif len(active_bidders()) > 0: # NORMAL PASS
              self.player_focus = (self.player_focus + 1) % 4
              while self.players[self.player_focus].bid.passed == True:
                self.player_focus = (self.player_focus + 1) % 4
              self.dlg_passed_next_bidder(name, self.players[self.player_focus].name)

            elif len(active_bidders()) == 0 and current_highest_bidder_score() > 0: # FINISHED: WINNER DIDN'T INCREASE BID (0 bidders)
              player.bid.passed = "WINNER"
              self.dlg_bid_not_increased(player.name)
              schedule_t.jobqueue.put(self.state_trans)
              
          # NOT A PASS
          else:

            highest = current_highest_bid()

            # MISÈRE MAY ONLY BE BID WHEN THE CURRENT HIGH BID IS A SEVEN (PAGAT: "Misère
            # can only be bid after someone has bid seven")
            if bid.suit == 0 and highest[1] != 7:
              self.log(f"{'gui_bid'.ljust(12)}: Misère bid from {name} rejected - only allowed over a bid of seven.")
              self.sio_push()
              return

            if bid_rank(bid.tricks, bid.suit) > highest[0]: # NEW HIGHER-RANKED BID

              player.bid.suit = bid.suit
              player.bid.tricks = bid.tricks
              # PUBLIC RECORD FOR THE PLAYER BOTS - ONLY ACCEPTED BIDS ARE APPENDED
              # (REJECTED/WEAK BIDS RETURNED ABOVE WERE NEVER ANNOUNCED TO THE TABLE)
              self.bid_history.append({"seat": self.player_focus, "pass": False, "suit": bid.suit, "tricks": bid.tricks})

              if len(active_bidders()) > 1: # NORMAL BID - MORE BIDDERS AVAILABLE
                
                self.player_focus = (self.player_focus + 1) % 4
                while self.players[self.player_focus].bid.passed == True:
                  self.player_focus = (self.player_focus + 1) % 4
                self.dlg_bid_next_bidder(name, player.bid.tricks, player.bid.suit, self.players[self.player_focus].name)
                return
                
              else:
                if player.bid.passed == "WINNER_INCREASE_OPTION": # FINISHED: WINNER DID INCREASE BID
                  player.bid.passed = "WINNER"
                  self.dlg_bid_increased(player.name, player.bid.tricks, player.bid.suit)
                  schedule_t.jobqueue.put(self.state_trans)
                  return
                else:
                  player.bid.passed = "WINNER_INCREASE_OPTION" # FINISHED: FIRST BID WINNER... GIVE INCREASE OPTION
                  self.dlg_bid_increase_option(player.name)
              
            elif bid_rank(bid.tricks, bid.suit): # VALID BID BUT NOT STRONG ENOUGH
              self.log(f"{'gui_bid'.ljust(12)}: Weak bid from {name} - {bid.tricks} x {bid.suit}.")
              self.sio_push()
              return
            else:
              self.log(f"{'gui_bid'.ljust(12)}: Invalid bid from {name} - {bid.tricks} x {bid.suit}.")
              self.sio_push()
              return
          
          return      
      return

  # STATE 3 FUNCTION CALLED BY GUI
  def gui_discard(self, name, discard):
    if self.state_name() == "AWARD KITTY":
      for player_index, player in enumerate(self.players):
        if not player.bid.passed:
          if same_name(self.players[player_index].name, name):
            # A CONTRACTOR WITH NO KITTY CARDS HAS ALREADY DISCARDED - IGNORE A REPEAT
            # SUBMISSION (DOUBLE-CLICK, LAGGY CLIENT, OR A RE-FIRED BOT CHECK) RATHER
            # THAN STRIPPING 3 MORE CARDS FROM THE ALREADY-TRIMMED HAND
            if len(player.kitty.cards) == 0:
              self.log(f"{'gui_discard'.ljust(12)}: Repeat discard from {name} ignored.")
              return
            for index in reversed(discard['kitty']):        
              self.players[player_index].kitty.remove_card_index(index)
            for index in reversed(discard['hand']):      
              self.players[player_index].hand.remove_card_index(index)

            while len(self.players[player_index].kitty.cards):
              self.players[player_index].kitty.move_cards(self.players[player_index].hand, 1)
            self.players[player_index].hand.sort(self.trumps)
            self.sio_push()
            
            self.player_focus = player_index

            schedule_t.jobqueue.put(self.state_trans)

  # SUITS STILL AVAILABLE FOR A JOKER-LEAD NOMINATION (SUITS 1-4 NOT YET LED THIS HAND)
  def joker_lead_suits(self):
    return [suit for suit in (1, 2, 3, 4) if suit not in self.suits_led]

  # RETURNS TRUE IF THE CARD AT index IN hand_cards MAY LEGALLY BE PLAYED ON THE CURRENT TRICK
  def allowed_to_play(self, index, hand_cards):

    left_bower_suit = {1:2, 2:1, 3:4, 4:3}
    is_misere = self.trumps == 0
    trumps = 5 if is_misere else self.trumps # MISÈRE PLAYS AS NO TRUMPS (JOKER IS SUIT 5)

    if self.trick_suit == None:
      # LEADING: NO TRUMPS / MISÈRE JOKER LEAD IS ILLEGAL ONCE ALL FOUR SUITS HAVE BEEN
      # LED (NO NOMINATION LEFT TO MAKE) - EXCEPT TO THE LAST TRICK. A PRE-NOMINATED
      # JOKER IS EXEMPT: LEADING IT JUST LEADS ITS DECLARED SUIT
      if trumps == 5 and hand_cards[index].rank == 15 and len(hand_cards) > 1 and not self.joker_prenom:
        if not self.joker_lead_suits():
          return False
      return True # ANY OTHER LEAD IS FREE

    # A THROWAWAY COPY: THE CHECKS BELOW REWRITE .suit TO THE CARD'S *EFFECTIVE* SUIT
    # (JOKER AND LEFT BOWER COUNT AS TRUMPS), AND THE REAL CARD MUST NOT BE MUTATED.
    card = Card()
    card.rank = hand_cards[index].rank
    card.suit = hand_cards[index].suit

    if card.rank == 15: # JOKER
      card.suit = self.joker_prenom if self.joker_prenom else trumps # PRE-NOMINATED JOKER BELONGS TO ITS DECLARED SUIT, ELSE TRUMPS
    if card.rank == 11: # JACK - LEFT-BOWER EXCEPTION
      if card.suit == left_bower_suit.get(trumps): # LEFT BOWER IS REGARDED TRUMPS SUIT (NO BOWERS IN NO TRUMPS / MISERE)
        card.suit = trumps
    if card.suit == self.trick_suit:
      return True # FOLLOWING SUIT IS ALWAYS FINE
    if not len(list(filter(lambda card: card.suit == self.trick_suit, hand_cards))): # DONT HAVE A CARD OF TRICK SUIT - DIRECTLY
      if self.trick_suit == trumps: # someone lead trumps
        if not self.joker_prenom and len(list(filter(lambda card: card.rank == 15, hand_cards))):
          return False # if have the joker and tried to play off trumps when trumps was lead.
        if len(list(filter(lambda card: (card.rank == 11 and card.suit == left_bower_suit.get(trumps)), hand_cards))):
          return False # if have the left-bower and tried to play off trumps when trumps was lead.
      if self.joker_prenom and self.trick_suit == self.joker_prenom and card.rank != 15:
        if len(list(filter(lambda card: card.rank == 15, hand_cards))):
          return False # PRE-NOMINATED JOKER COUNTS AS THE LED SUIT - MUST FOLLOW WITH IT
      if is_misere and not self.joker_prenom and card.rank != 15 and len(list(filter(lambda card: card.rank == 15, hand_cards))):
        return False # MISÈRE: THE (UN-NOMINATED) JOKER MUST BE PLAYED WHEN UNABLE TO FOLLOW SUIT
      return True # VOID IN THE LED SUIT AND NO FORCED CARD APPLIES - PLAY ANYTHING

    # THE LED SUIT IS HELD, SO NORMALLY IT MUST BE FOLLOWED. THE ONE ESCAPE: THE SOLE
    # "MATCHING" CARD IS THE LEFT BOWER, WHICH REALLY BELONGS TO TRUMPS - ITS OWNER IS
    # VOID IN THE LED SUIT AFTER ALL AND MAY PLAY ANYTHING (INCLUDING KEEPING THE BOWER).
    if len(list(filter(lambda card: card.suit == self.trick_suit, hand_cards))) == 1: # IF NOT FOLLOWING SUIT BECAUSE ONLY OPTION IS LEFT BOWER, ANY CARD IS ALLOWED.
      if list(filter(lambda card: card.suit == self.trick_suit, hand_cards))[0].rank == 11:
        if list(filter(lambda card: card.suit == self.trick_suit, hand_cards))[0].suit == left_bower_suit.get(trumps):
          return True
    return False # HOLDING THE LED SUIT - MUST FOLLOW IT

  # LIST OF HAND INDICES player_index MAY LEGALLY PLAY RIGHT NOW - None OUTSIDE PLAY HAND.
  # SENT TO CLIENTS IN EVERY PUSH SO THEY CAN DARKEN UNPLAYABLE CARDS.
  def legal_play_indices(self, player_index):
    player = self.players[player_index]
    if self.state_name() != "PLAY HAND" or player.hand == None:
      return None
    if player_index == self.sitting_out: # MISÈRE: SAT-OUT PARTNER NEVER PLAYS
      return None
    if self.joker_nominating: # NO CARD MAY BE PLAYED UNTIL THE JOKER'S SUIT IS NOMINATED
      return []
    return [i for i in range(len(player.hand.cards)) if self.allowed_to_play(i, player.hand.cards)]

  # STATE 4 FUNCTION CALLED BY GUI
  def gui_play(self, name, card_index):
    
    # PLAYS card_index FROM THE FOCUSED PLAYER'S HAND. IGNORES ANYTHING FROM THE WRONG
    # PLAYER OR AN ILLEGAL CARD - THE SERVER IS AUTHORITATIVE, THE CLIENT ONLY DARKENS.

    # BRANCHES ON HOW MANY CARDS ARE ALREADY ON THE TABLE:
    #   FIRST  - SETS trick_suit, MAY DIVERT INTO A JOKER SUIT NOMINATION
    #   MIDDLE - MUST FOLLOW SUIT (see allowed_to_play)
    #   LAST   - COMPLETES THE TRICK: FINDS THE WINNER, SCORES IT, MOVES PLAY ON

    # (SUIT/RANK ENCODING LIVES AT THE TOP OF playing_cards.py.)
    
    if self.state_name() == "PLAY HAND" and self.player_focus != None:
      if same_name(self.players[self.player_focus].name, name):

        if self.joker_nominating: # NO PLAYS UNTIL THE JOKER LEADER NOMINATES A SUIT
          self.log(f"Play from {name} ignored - waiting on their joker suit nomination.")
          self.sio_push()
          return

        # THE WON TRICK LINGERS ON THE TABLE UNTIL THE WINNER LEADS - CLEAR IT NOW,
        # BEFORE THE LEAD/MIDDLE/LAST BRANCHING COUNTS THE TABLE CARDS
        if self.table_stale:
          for player_data in self.players:
            player_data.table = None
          self.table_stale = False

        is_misere = self.trumps == 0
        eff_trumps = 5 if is_misere else self.trumps # MISÈRE PLAYS AS NO TRUMPS
        trick_size = 3 if self.sitting_out != None else 4 # MISÈRE: PARTNER SITS OUT
        table_cards = list(filter(lambda player: player.table != None, self.players))

        if len(table_cards) == 0: # first card - allowed no matter what and sets the trick suit.

          card = self.players[self.player_focus].hand.cards[card_index]

          # THE CONTRACTOR'S PRE-NOMINATION WINDOW CLOSES AT THE FIRST LEAD OF THE HAND
          # (LATER TRICKS IT IS ALREADY CLOSED - HARMLESS)
          self.joker_prenom_open = False

          # NO TRUMPS / MISÈRE JOKER LEAD: LEADER MUST NOMINATE A SUIT NOT YET LED THIS
          # HAND (SO ILLEGAL ONCE ALL FOUR HAVE BEEN LED) - EXCEPT TO THE LAST TRICK,
          # WHERE EVERYONE'S FINAL CARD FALLS AND NO NOMINATION IS NEEDED. A PRE-NOMINATED
          # JOKER SKIPS ALL THIS: LEADING IT SIMPLY LEADS ITS DECLARED SUIT
          if eff_trumps == 5 and card.rank == 15 and not self.joker_prenom and len(self.players[self.player_focus].hand.cards) > 1:
            if not self.joker_lead_suits():
              self.log(f"Joker lead from {name} rejected - all four suits have been led.")
              self.sio_push()
              return
            self.players[self.player_focus].table = card # copy card to table
            self.players[self.player_focus].hand.remove_card_index(card_index) # remove card from hand
            # PUBLIC RECORD FOR THE PLAYER BOTS - THE JOKER IS TABLED (VISIBLE TO ALL)
            # EVEN THOUGH THE TRICK SUIT AWAITS THE NOMINATION
            self.current_trick.append({"seat": self.player_focus, "suit": card.suit, "rank": card.rank})
            self.trick_suit = None # SET BY gui_joker_nominate()
            self.joker_nominating = True
            self.joker_nomination = None
            self.log(f"{name} led the Joker - awaiting suit nomination.")
            self.dlg_joker_lead_nominating(name)
            return

          # SET THE TRICK SUIT
          self.trick_suit = card.suit
          if card.rank == 15: # JOKER
            self.trick_suit = self.joker_prenom if self.joker_prenom else eff_trumps # PRE-NOMINATED JOKER LEADS ITS DECLARED SUIT, ELSE TRUMPS
          if card.rank == 11: # JACK - LEFT-BOWER EXCEPTION
            left_bower_suit = {1:2, 2:1, 3:4, 4:3}
            if card.suit == left_bower_suit.get(eff_trumps): # LEFT BOWER IS REGARDED TRUMPS SUIT (NO BOWERS IN NO TRUMPS / MISERE)
              self.trick_suit = eff_trumps
          if self.trick_suit in (1, 2, 3, 4) and self.trick_suit not in self.suits_led:
            self.suits_led.append(self.trick_suit) # TRACKED FOR THE JOKER-LEAD RESTRICTION

          # MOVE THE CARD TO THE TABLE
          self.players[self.player_focus].table = self.players[self.player_focus].hand.cards[card_index] # copy card to table
          self.players[self.player_focus].hand.remove_card_index(card_index) # remove card from hand
          # PUBLIC RECORD FOR THE PLAYER BOTS: EVERY TABLED CARD, IN PLAY ORDER
          self.current_trick.append({"seat": self.player_focus, "suit": card.suit, "rank": card.rank})

          # MOVE ON
          self.dlg_lead_suit(self.players[self.player_focus].name, self.trick_suit)
          self.log(f"{name} played {self.players[self.player_focus].table.full_str()}.") # LOG IS CLEANER IF THIS IS AFTER THE LEAD DIALOG
          self.player_focus = self.next_seat(self.player_focus) # move focus to next player
          self.sio_push() # push to clients

        elif len(table_cards) < trick_size - 1: # middle cards of the trick

          # if following suit or have no cards of that suit
          if self.allowed_to_play(card_index, self.players[self.player_focus].hand.cards):
            self.players[self.player_focus].table = self.players[self.player_focus].hand.cards[card_index] # copy card to table
            self.players[self.player_focus].hand.remove_card_index(card_index) # remove card from hand
            # PUBLIC RECORD FOR THE PLAYER BOTS: EVERY TABLED CARD, IN PLAY ORDER
            played = self.players[self.player_focus].table
            self.current_trick.append({"seat": self.player_focus, "suit": played.suit, "rank": played.rank})
            self.log(f"{name} played {self.players[self.player_focus].table.full_str()}.")
            self.player_focus = self.next_seat(self.player_focus) # move focus to next player
            self.sio_push() # push to clients

        else: # last card of trick
          # if following suit or have no cards of that suit
          if self.allowed_to_play(card_index, self.players[self.player_focus].hand.cards) and self.players[self.player_focus].table == None:
            played_by = self.player_focus
            self.players[played_by].table = self.players[played_by].hand.cards[card_index] # copy card to table
            self.players[played_by].hand.remove_card_index(card_index) # remove card from hand
            # PUBLIC RECORD FOR THE PLAYER BOTS: EVERY TABLED CARD, IN PLAY ORDER
            played = self.players[played_by].table
            self.current_trick.append({"seat": played_by, "suit": played.suit, "rank": played.rank})
            self.log(f"{name} played {self.players[played_by].table.full_str()}.")
            self.player_focus = None # TRICK COMPLETE - NO-ONE MAY ACT (OR BE HIGHLIGHTED) UNTIL WINNER IS DETERMINED
            self.sio_push() # push to clients

            # who won the trick?
            table_cards = Deck(fill=False)
            for i, player_data in enumerate(self.players):
              if player_data.table == None: # MISÈRE: SAT-OUT PARTNER HAS NO CARD ON THE TABLE
                continue
              table_card = player_data.table
              table_card.player_index = i # TAG SO THE SORTED ORDER CAN BE MAPPED BACK TO A SEAT
              table_cards.add_card(table_card)
            table_cards.sort(self.trumps)
            # EFFECTIVE SUIT FOR JOKER AND LEFT BOWER - MUST NOT MUTATE THE CARD:
            # THE TABLE STILL HOLDS THESE OBJECTS AND dlg_won_trick PUSHES THEM TO CLIENTS
            def eff_suit(card):
              if card.rank == 15: # JOKER: PRE-NOMINATED SUIT IF DECLARED, ELSE TRUMPS
                return self.joker_prenom if self.joker_prenom else eff_trumps
              if card.rank == 11: # JACK - LEFT-BOWER EXCEPTION
                left_bower_suit = {1:2, 2:1, 3:4, 4:3}
                if card.suit == left_bower_suit.get(eff_trumps): # LEFT BOWER IS REGARDED TRUMPS SUIT (NO BOWERS IN NO TRUMPS / MISERE)
                  return eff_trumps
              return card.suit
            # THE SORT PUT THE STRONGEST CARD FIRST, BUT IT MAY BE AN OFF-SUIT CARD THAT
            # CANNOT WIN. WALK DOWN IN STRENGTH TO THE FIRST CARD THAT IS EITHER A TRUMP
            # OR OF THE LED SUIT - THAT ONE TAKES THE TRICK. THE SCAN CANNOT RUN OFF THE
            # END: THE LEAD ITSELF IS ALWAYS OF THE LED SUIT.
            winning_card_index = 0
            while eff_suit(table_cards.cards[winning_card_index]) != eff_trumps and eff_suit(table_cards.cards[winning_card_index]) != self.trick_suit:
              winning_card_index += 1
            winner_index = table_cards.cards[winning_card_index].player_index
            # PUBLIC RECORD FOR THE PLAYER BOTS: THE COMPLETED TRICK, CAPTURED BEFORE
            # trick_suit/joker_nomination ARE CLEARED BELOW SO A BOT CAN LATER RECONSTRUCT
            # WHO FAILED TO FOLLOW WHAT (VOID / OUT-OF-TRUMPS INFERENCE)
            self.trick_history.append({"cards": self.current_trick, "winner": winner_index,
                                       "trick_suit": self.trick_suit, "joker_nomination": self.joker_nomination})
            self.current_trick = []
            if self.players[winner_index].bid.won == None:
              self.players[winner_index].bid.won = 1
            else:
              self.players[winner_index].bid.won += 1

            self.trick_suit = None
            self.joker_nomination = None # NOMINATION ONLY BINDS THE TRICK IT WAS MADE FOR
            self.player_focus = None # NO-ONE MAY ACT (AND NO CLIENT HIGHLIGHT) UNTIL NEXT LEAD IS KNOWN
            self.dlg_won_trick(self.players[winner_index].name)
            self.delay(2)

            if is_misere and winner_index == self.contractor_index(): # MISÈRE FAILS ON THE CONTRACTOR'S FIRST WON TRICK
              self.dlg_misere_failed(self.players[winner_index].name) # CARDS STAY VISIBLE THROUGH THE DIALOG
              self.delay(2)
              for player_data in self.players:
                player_data.table = None
              self.sio_push() # push to clients
              schedule_t.jobqueue.put(self.state_trans)
            elif self.hand_cards_status() == 'between tricks': # between tricks. Hand still going.
              self.table_stale = True # WON TRICK LINGERS UNTIL THE WINNER LEADS THE NEXT CARD
              self.player_focus = winner_index
              self.dlg_next_lead(self.players[winner_index].name, self.trumps)
            else: # LAST TRICK OF THE HAND: LINGER 1.5x THE USUAL PAUSE BEFORE THE SCOREBOARD
              self.delay(3)
              for player_data in self.players:
                player_data.table = None
              self.sio_push() # push to clients
              schedule_t.jobqueue.put(self.state_trans)

  # STATE 4 FUNCTION CALLED BY GUI - TWO NOMINATION FLAVOURS (NO TRUMPS / MISÈRE):
  # 1. PRE-LEAD: THE CONTRACTOR (HOLDING THE JOKER) DECLARES ITS SUIT FOR THE WHOLE HAND,
  #    ONLY WHILE joker_prenom_open (BEFORE THE FIRST LEAD).
  # 2. LEAD-TIME: THE JOKER LEADER NAMES THE SUIT THE OTHERS MUST FOLLOW THIS TRICK,
  #    ONLY WHILE gui_play() LEFT joker_nominating SET.
  def gui_joker_nominate(self, name, suit):
    if self.state_name() != "PLAY HAND" or self.player_focus == None:
      return
    try:
      suit = int(suit)
    except (TypeError, ValueError):
      suit = None

    if not self.joker_nominating: # PRE-LEAD FLAVOUR (OR NOTHING TO DO)
      if not self.joker_prenom_open:
        return
      contractor = self.contractor_index()
      if contractor == None or not same_name(self.players[contractor].name, name):
        self.log(f"Joker pre-nomination from non-contractor ({name}) ignored.")
        return
      if suit not in (1, 2, 3, 4):
        self.log(f"Joker pre-nomination of {suit} from {name} rejected - not a suit.")
        self.sio_push()
        return
      self.joker_prenom = suit
      self.joker_prenom_open = False
      self.players[contractor].hand.sort(self.trumps, joker_suit=suit) # SHOW THE JOKER WITH ITS DECLARED SUIT
      self.log(f"{name} pre-nominated the Joker as the highest {SUIT_STR[suit]} for this hand.")
      self.dlg_joker_prenominated(name, suit)
      return

    if not same_name(self.players[self.player_focus].name, name):
      self.log(f"Nomination from non-leading player ({name}) ignored.")
      return
    if suit not in self.joker_lead_suits():
      self.log(f"Nomination of {suit} from {name} rejected - not an available suit.")
      self.sio_push()
      return

    self.trick_suit = suit
    self.suits_led.append(suit) # A NOMINATED SUIT COUNTS AS LED
    self.joker_nomination = suit
    self.joker_nominating = False
    self.log(f"{name} nominated {SUIT_STR[suit]} for their Joker lead.")
    self.player_focus = self.next_seat(self.player_focus)
    self.dlg_joker_nominated(name, suit)

  # STATE 5 FUNCTION CALLED BY GUI
  def auto_points(self):
    
    contracted_player_index = -1
    for i, player in enumerate(self.players):
      if not player.bid.passed:
        contracted_player_index = i
      if player.bid.won == None:
        player.bid.won = 0
    
    contracted_tricks = self.players[contracted_player_index].bid.tricks
    is_misere = self.players[contracted_player_index].bid.suit == 0

    if is_misere:
      # MISÈRE: CONTRACTOR PLAYS ALONE AND MUST LOSE EVERY TRICK; OPPONENTS SCORE NOTHING
      # EITHER WAY. HAND MAY HAVE ENDED EARLY, SO COUNT ACTUAL WON TRICKS PER SIDE.
      contractors_tricks = self.players[contracted_player_index].bid.won
      opponents_tricks = self.players[(contracted_player_index + 1) % 4].bid.won + self.players[(contracted_player_index + 3) % 4].bid.won
      contractor_points = 250 if contractors_tricks == 0 else -250
      opponent_points = 0
    else:
      contractors_tricks = self.players[contracted_player_index].bid.won + self.players[(contracted_player_index + 2) % 4].bid.won
      opponents_tricks = 10 - contractors_tricks

      if contractors_tricks >= contracted_tricks:
        # "SLAM" rule: winning all 10 tricks scores a minimum of 250
        if contractors_tricks == 10 and self.get_score(self.players[contracted_player_index].bid.tricks, self.players[contracted_player_index].bid.suit) < 250:
          contractor_points = 250
        else:
          contractor_points = self.get_score(self.players[contracted_player_index].bid.tricks, self.players[contracted_player_index].bid.suit)
      else:
        contractor_points = - ( self.get_score(self.players[contracted_player_index].bid.tricks, self.players[contracted_player_index].bid.suit) )

      opponent_points = 10 * opponents_tricks # OPPONENTS ALWAYS SCORE 10 PER TRICK TAKEN

    # APPLY BOTH SIDES' POINTS AND RECORD A SCOREBOARD ROW EACH. SEATS ARE TEAMED BY
    # PARITY, SO THE CONTRACTOR'S TEAM IS index % 2 AND THE OPPONENTS' IS THE OTHER.
    self.teams[contracted_player_index % 2].score += contractor_points
    self.teams[contracted_player_index % 2].history.append(dd({"points": contractor_points, "tricks": contractors_tricks, "contracted": True, "contract": [self.players[contracted_player_index].name, self.players[contracted_player_index].bid.tricks, self.players[contracted_player_index].bid.suit] }))
    self.teams[(contracted_player_index + 1) % 2].score += opponent_points
    self.teams[(contracted_player_index + 1) % 2].history.append(dd({"points": opponent_points, "tricks": opponents_tricks, "contracted": False, "contract": [self.players[contracted_player_index].name, self.players[contracted_player_index].bid.tricks, self.players[contracted_player_index].bid.suit] }))

    # SINGLE-LINE, GREP-FRIENDLY HAND SUMMARY FOR OFFLINE BOT-TUNING ANALYSIS (SEE
    # docs/BOTS.md) - EVERYTHING A REVIEW NEEDS (ALL 4 SEATS, BID VS ACTUAL, AND THE
    # CONTRACTOR'S confidence PERSONALITY IF THEY'RE A SMART BOT) IN ONE LINE, RATHER
    # THAN RECONSTRUCTED FROM THE SEPARATE PLAYER-FACING dlg_* DIALOGS BELOW
    contractor_bot = self.player_bots.get(contracted_player_index)
    confidence = f"{contractor_bot['personality']['confidence']:.3f}" if contractor_bot else "n/a"
    self.log(
      f"[HAND_SUMMARY] contractor={self.players[contracted_player_index].name} "
      f"partner={self.players[(contracted_player_index + 2) % 4].name} "
      f"bid={contracted_tricks} suit={SUIT_STR[self.players[contracted_player_index].bid.suit]} "
      f"won={contractors_tricks} {'MADE' if contractor_points > 0 else 'FAILED'} "
      f"confidence={confidence} "
      f"opp1={self.players[(contracted_player_index + 1) % 4].name} "
      f"opp2={self.players[(contracted_player_index + 3) % 4].name}"
    )

    # WALK THE RESULT OUT ONE DIALOG AT A TIME - EACH dialog() PUSHES, SO THE PAUSES
    # ARE WHAT THE PLAYERS ACTUALLY SEE ON THE SCOREBOARD
    self.dlg_hand_complete()
    self.delay(2)

    self.dlg_contract_summary(self.players[contracted_player_index].name, self.players[(contracted_player_index + 2) % 4].name, self.players[contracted_player_index].bid.tricks, self.players[contracted_player_index].bid.suit)
    self.delay(2)

    if is_misere:
      self.dlg_misere_result(self.players[contracted_player_index].name, contractor_points)
    else:
      self.dlg_contract_result(contractors_tricks, contractor_points)
    self.delay(2)

    if is_misere:
      self.dlg_misere_opposition_result(self.players[(contracted_player_index+1)%4].name, self.players[(contracted_player_index+3)%4].name)
    else:
      self.dlg_opposition_result(self.players[(contracted_player_index+1)%4].name, self.players[(contracted_player_index+3)%4].name, opponents_tricks, opponent_points)
    self.delay(2)

    self.dlg_scores_first_team(self.players[0].name, self.players[2].name, self.teams[0].score)
    self.delay(2)

    self.dlg_scores_second_team(self.players[1].name, self.players[3].name, self.teams[1].score)
    self.delay(2)

    for player in self.players:
      player.bid = dd({"suit": None, "tricks": None, "won": None, "passed": False})

    self.autosave() # scores applied + bids cleared: a restore from here re-queues state_trans, not auto_points

    schedule_t.jobqueue.put(self.state_trans)

