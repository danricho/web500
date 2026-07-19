from datetime import datetime, timedelta
from flask import Flask, render_template, send_from_directory, send_file, request, jsonify, redirect, session
from flask_socketio import SocketIO
import os, sys, socket, json, hmac, secrets, subprocess, threading, time, traceback

# TO RUN VIA GUNICORN - EXACTLY ONE WORKER, THE GAME LIVES IN THIS PROCESS'S MEMORY:
# venv/bin/gunicorn -b :4030 -w 1 --threads 100 main:app

base_path = os.path.dirname(sys.argv[0]) +"/"

# THE SHELL COMMAND /dev/restart RUNS. THE LIVE GAME SURVIVES A RESTART - IT IS
# RESTORED FROM data/autosave.json AT STARTUP. SET TO None TO DISABLE THE BUTTON.
# NEEDS PASSWORDLESS sudo (OR A ROOT SERVICE USER) TO WORK UNATTENDED.
RESTART_COMMAND = "sudo systemctl restart web-500-web-server.service"
RESTART_DELAY_S = 0.5 # LET THE HTTP RESPONSE FLUSH BEFORE THE PROCESS IS KILLED

VERSION = "v.2026.07.19.1" # version definition 
                # SINGLE SOURCE OF TRUTH - SHOWN ON THE CLIENT (MODAL CREDITS), LOGGED
                # AT STARTUP AND SERVED BY /dev/uptime. BUMP ON RELEASE.
PORT = 4030 # FLASK DEV SERVER ONLY - GUNICORN BINDS ITSELF (-b :4030 IN THE UNIT/README)
GAME_NAME = "W500.G1" # THE SINGLE IN-MEMORY GAME'S NAME (LOG PREFIX + SAVE FILES)
BOTS_ENABLED = True # MASTER TOGGLE FOR THE LOBBY ADD BOTS BUTTON (SERVER-ENFORCED)
DEFAULT_DEV_USERS = ["Nerd"] # FIRST-RUN auth.json SEED ONLY - EDIT data/auth.json AFTER

SERVICE_START = time.time() # PROCESS START - /dev/uptime REPORTS AGAINST THIS

# QUIETEN WERKZEUG'S PER-REQUEST LINES SO THE GAME'S OWN LOG STAYS READABLE. THE
# LOGGER IS ONLY NEEDED FOR setLevel - OUR OWN log() PRINTER IS DEFINED BELOW.
import logging
werkzeug_log = logging.getLogger('werkzeug')
werkzeug_log.setLevel(logging.ERROR)
app = Flask(__name__)

app.jinja_env.auto_reload = True

def log(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] MAIN APP/SERVER :::::::: \033[31m{message}\033[0m")

log(f"STARTING SERVICE ({VERSION}) AT http://{socket.gethostname()}:{PORT}")

from game_state import *
import bots

# ---------------------------------------------------------------------------
# SIMPLE AUTH: SHARED GAME PASSCODE + SIGNED SESSION COOKIE. IDENTITY COMES ONLY
# FROM THE SESSION SET AT LOGIN - SOCKET ACTIONS IGNORE CLIENT-SUPPLIED NAMES.
# data/auth.json holds {"passcode": ..., "dev_users": [...]} and is created with a
# random passcode on first run (edit it to set your own, then restart).
# data/secret_key.txt holds the persistent session-signing key so logins survive
# service restarts. Both live in the gitignored data/ directory.
# ---------------------------------------------------------------------------
AUTH_FILE = os.path.join(DATA_DIR, "auth.json")
SECRET_KEY_FILE = os.path.join(DATA_DIR, "secret_key.txt")

def load_or_create_auth():
  os.makedirs(DATA_DIR, exist_ok=True)
  if not os.path.exists(AUTH_FILE):
    config = {"passcode": secrets.token_urlsafe(8), "dev_users": DEFAULT_DEV_USERS}
    with open(AUTH_FILE, "w") as f:
      json.dump(config, f, indent=2)
    log(f"CREATED {AUTH_FILE} WITH A RANDOM PASSCODE - EDIT IT TO SET YOUR OWN")
  with open(AUTH_FILE) as f:
    return json.load(f)

def load_or_create_secret_key():
  os.makedirs(DATA_DIR, exist_ok=True)
  if not os.path.exists(SECRET_KEY_FILE):
    with open(SECRET_KEY_FILE, "w") as f:
      f.write(secrets.token_hex(32))
    log(f"CREATED {SECRET_KEY_FILE} (SESSION SIGNING KEY)")
  with open(SECRET_KEY_FILE) as f:
    return f.read().strip()

AUTH = load_or_create_auth()
app.config['SECRET_KEY'] = load_or_create_secret_key()
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=90)

def current_user():
  return session.get("username")

def is_dev_user():
  user = current_user()
  return user != None and any(same_name(user, d) for d in AUTH.get("dev_users", []))

socketio = SocketIO(app, transports=['polling'], logger=False)

game = GameStateMachine(GAME_NAME, socketio_init=socketio)
game.socketio = socketio

# WORKER-JOB FAILURES: THE FULL TRACEBACK IS ALREADY LOGGED BY threaded_schedule -
# THIS HOOK ADDITIONALLY SURFACES A TOAST SO EVERYONE SEES SOMETHING BROKE
schedule_t.on_error = lambda job_name, tb: game.sio_toast(
  f"'{job_name}' failed - check the service logs", kind="danger", seconds=8,
  category="SERVER ERROR")

# RESUME THE PERSISTED GAME (IF ANY) - /api/reinit CLEARS IT FOR A TRUE FRESH START.
# THE "GAME RESTORED" TOAST CAN'T FIRE HERE (NO CLIENT IS CONNECTED YET AT STARTUP),
# SO IT IS DEFERRED TO THE FIRST SOCKET CONNECT - BY THEN THE OLD CLIENTS ARE ALL
# RECONNECTING AND THE BROADCAST REACHES THEM
restored_toast_pending = False
if os.path.exists(AUTOSAVE_FILE):
  log(f"AUTOSAVE FOUND - RESTORING GAME STATE FROM {AUTOSAVE_FILE}")
  restored_toast_pending = game.restore_state(AUTOSAVE_FILE)

# KEEP-ALIVE: is_push_needed RE-PUSHES ONLY IF NOTHING WENT OUT IN THE LAST 10s.
# QUEUED (NOT CALLED) SO IT RUNS ON THE SINGLE WORKER THAT SERIALISES ALL GAME WORK.
schedule.every(1).seconds.do(schedule_t.jobqueue.put, game.is_push_needed)
# FOCUS-IDLE NUDGE: "Are you there X?" TOAST WHEN A HUMAN SITS ON FOCUS TOO LONG
schedule.every(1).seconds.do(schedule_t.jobqueue.put, game.check_focus_idle)

@app.route('/', defaults={'path': ''}, methods=['GET', 'POST'])
@app.route('/<path:path>', methods=['GET', 'POST'])
def index(path):
  global players, game, test_driver_enabled

  # REQUEST-LOG VERBOSITY DIAL. EACH p_* HELPER PRINTS ONLY IF ITS LEVEL IS AT OR
  # BELOW log_level. AT THE "warning" DEFAULT NOTHING BELOW PRINTS - RAISE IT TO
  # "debug" OR "info" TO SEE REQUESTS GO BY WHILE DEBUGGING.
  log_level = "warning"

  def p_debug(*args):
    if log_level in ['debug']:
      print( f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} \33[Flask\33[39m:\33[36m", *args, f"\33[39m", sep=" ")
  def p_event(*args):
    if log_level in ['debug', 'info']:
      print( f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} \33[97mFlask\33[39m:\33[94m", *args, f"\33[39m", sep=" ")
  def p_warning(*args):
    if log_level in ['debug', 'info', 'warning']:
      print( f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} \33[97mFlask\33[39m:\33[33m", *args, f"\33[39m", sep=" ")
  def p_error(*args):
    if log_level in ['debug', 'info', 'warning', 'error']:
      print( f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} \33[97mFlask\33[39m:\33[31m", *args, f"\33[39m", sep=" ")

  # KEEP SEARCH ENGINES / POLITE CRAWLERS AWAY - SITE IS PRIVATE
  if path == "robots.txt":
    return app.response_class("User-agent: *\nDisallow: /\n", mimetype="text/plain")

  if path == '':
    if not current_user():
      p_event("HMTL Request: Login")
      return render_template('login.j2.html')
    p_event("HMTL Request: Game (Client)")
    return render_template('game_client.j2.html', home=True)

  # DEV ENDPOINTS REQUIRE A LOGGED-IN DEV USER; API HELPERS ANY LOGGED-IN PLAYER
  if path.startswith("dev/") and not is_dev_user():
    return "forbidden", 403
  if path.startswith("api/") and not current_user():
    return "forbidden", 403

  if path == "api/client_trigger_push":
    game.sio_push()
    
  if path == "api/reinit": # ANY LOGGED-IN PLAYER MAY RESET THE GAME (SETTINGS MODAL)
    log(f"Flask Request to reinitialise the game (by '{current_user()}')")
    if game.state_name() != "DEALING":
      game.__init__(GAME_NAME, socketio_init=socketio)
      game.clear_autosave() # init means a true fresh start - forget the persisted game
      game.sio_push()
      game.sio_toast(f"Game reinitialised by {current_user()}", kind="warning", seconds=6, category="GAME MANAGEMENT")
    return "ok"

  if path == "dev/cards": # VISUAL REVIEW OF THE CARD BACK + ALL 43 FACES (SHARED PROTO CARD)
    return render_template('cards_review.j2.html')

  if path == "dev/save":
    log(f"Flask Request to save dev checkpoint (DEV FEATURE)")
    game.save_state(CHECKPOINT_FILE, reason="(dev checkpoint)")
    game.sio_toast(f"Checkpoint saved by {current_user()}", kind="success", category="GAME MANAGEMENT")
    return "ok"

  if path == "dev/load":
    log(f"Flask Request to load dev checkpoint (DEV FEATURE)")
    user = current_user() # CAPTURED NOW - THE WORKER THREAD HAS NO REQUEST CONTEXT
    def load_and_toast():
      if game.restore_state(CHECKPOINT_FILE):
        game.sio_toast(f"Checkpoint loaded by {user}", kind="success", seconds=6, category="GAME MANAGEMENT")
      else:
        game.sio_toast(f"Checkpoint load FAILED (requested by {user})", kind="danger", seconds=6, category="GAME MANAGEMENT")
    schedule_t.jobqueue.put(load_and_toast) # on the worker so it can't interleave with auto jobs
    return "ok"

  if path == "dev/clearchk":
    log(f"Flask Request to clear dev checkpoint (DEV FEATURE)")
    if os.path.exists(CHECKPOINT_FILE):
      os.remove(CHECKPOINT_FILE)
      game.sio_toast(f"Checkpoint cleared by {current_user()}", category="GAME MANAGEMENT")
    else:
      game.sio_toast("No checkpoint to clear", category="SERVER ERROR")
    return "ok"

  if path == "dev/test":
    log(f"Flask Request to toggle test mode automation (DEV FEATURE)")
    game.test_mode = not game.test_mode
    if game.test_mode:
      schedule_t.jobqueue.put(lambda: bots.dev_random_seat_bots(game))
    game.sio_push() # also queues a dev_random_bot_check if test mode is now on
    game.sio_toast(f"Test mode {'enabled' if game.test_mode else 'disabled'} by {current_user()}",
                   kind="warning" if game.test_mode else "info", category="GAME MANAGEMENT")
    return "ok"
  if path == "dev/skipdelays":
    log(f"Flask Request to toggle skip delays (DEV FEATURE)")
    game.skip_delays = not game.skip_delays
    game.sio_push()
    game.sio_toast(f"Skip delays {'enabled' if game.skip_delays else 'disabled'} by {current_user()}",
                   kind="warning" if game.skip_delays else "info", category="GAME MANAGEMENT")
    return "ok"

  if path == "dev/uptime":
    return jsonify({"version": VERSION,
                    "started": SERVICE_START, "uptime": time.time() - SERVICE_START,
                    "restart_enabled": bool(RESTART_COMMAND)})

  if path == "dev/restart":
    if not RESTART_COMMAND:
      return "restart command not configured", 501
    log(f"Flask Request to RESTART THE SERVICE (by '{current_user()}'): {RESTART_COMMAND}")
    # TOAST FIRST FOR THE BEST CHANCE OF REACHING THE OPEN LONG-POLLS BEFORE THE
    # PROCESS DIES (RESTART_DELAY_S LATER)
    game.sio_toast(f"Service restarting now (by {current_user()}) - hold tight...", kind="warning", seconds=8, category="SERVER")
    game.save_state(AUTOSAVE_FILE, reason="(pre-restart)") # THE GAME IS RESTORED FROM THIS ON THE WAY BACK UP
    def do_restart():
      time.sleep(RESTART_DELAY_S) # THE RESPONSE BELOW MUST REACH THE CLIENT FIRST - THIS KILLS US
      try:
        subprocess.Popen(RESTART_COMMAND, shell=True, start_new_session=True) # DETACHED: SURVIVES OUR OWN DEATH
      except Exception as e:
        log(f"RESTART COMMAND FAILED TO LAUNCH: {e}")
    threading.Thread(target=do_restart, daemon=True).start() # NOT THE GAME WORKER QUEUE - MUST NOT BLOCK GAME JOBS
    return "ok"
    
  if path == "api/last-game":
    return jsonify(games) # IN-MEMORY ONLY - EMPTIES ON RESTART

  return redirect("/")

# AUTH ROUTES (EXPLICIT RULES WIN OVER THE CATCH-ALL ABOVE)
@app.route('/login', methods=['POST'])
def login():
  name = (request.form.get('name') or "").strip()[:20]
  passcode = request.form.get('passcode') or ""
  if not name:
    return render_template('login.j2.html', error="Please enter a name.")
  if not hmac.compare_digest(passcode, str(AUTH.get("passcode", ""))):
    log(f"FAILED LOGIN ATTEMPT for name '{name}'")
    return render_template('login.j2.html', error="Wrong passcode.")
  # NAMES ARE CASE-INSENSITIVE: IF THIS NAME IS ALREADY SEATED IN THE GAME, ADOPT THE
  # SEATED SPELLING SO DISPLAY STAYS AS FIRST WRITTEN
  for player in game.players:
    if same_name(player.name, name):
      name = player.name
      break
  session.permanent = True
  session['username'] = name
  log(f"LOGIN: '{name}'")
  return redirect("/")

@app.route('/logout')
def logout():
  log(f"LOGOUT: '{current_user()}'")
  session.clear()
  return redirect("/")

# TELL SEARCH ENGINES NOT TO INDEX ANYTHING, INCLUDING STATIC ASSETS
@app.after_request
def add_noindex_header(response):
  response.headers['X-Robots-Tag'] = 'noindex, nofollow'
  return response

# DATA PROVIDED TO ALL TEMPLATES
@app.context_processor
def inject_data():
    return {
      'timeNow': datetime.now(),
      'version': VERSION,
      'session_username': current_user() or "",
      'session_is_dev': is_dev_user(),
      'bots_enabled': BOTS_ENABLED,
    }

# ---------------------------------------------------------------------------
# SOCKET HANDLERS - THIN ROUTING ONLY, ALL RULES LIVE IN game_state.py.
# IDENTITY COMES FROM THE SIGNED SESSION, NEVER FROM CLIENT-SUPPLIED DATA (THE
# CLIENT SENDS A `username` FIELD; IT IS DELIBERATELY IGNORED).
# EACH ACTION AUTOSAVES AFTERWARDS SO A RESTART RESUMES AT THE LATEST MOVE. THE
# gui_* METHODS PUSH STATE THEMSELVES VIA dialog(), SO ONLY THE HANDLERS THAT
# CHANGE STATE WITHOUT A DIALOG PUSH EXPLICITLY.
# ---------------------------------------------------------------------------
# SOCKET-HANDLER FAILURES: FLASK-SOCKETIO SWALLOWS HANDLER EXCEPTIONS INTO ITS OWN
# LOGGER, SO A FAILED PLAYER ACTION (E.G. gui_play RAISING) WOULD OTHERWISE DIE
# SILENTLY - NO GAME LOG LINE, NO FEEDBACK, THE CLICK JUST DOES NOTHING. MIRRORS THE
# WORKER-QUEUE on_error HOOK: FULL TRACEBACK TO THE LOG + A DANGER TOAST FOR EVERYONE.
@socketio.on_error_default
def handle_socket_error(e):
  event = request.event["message"] if hasattr(request, "event") else "?"
  log(f"SOCKET HANDLER FAILED: '{event}'\n{traceback.format_exc()}")
  game.sio_toast(f"'{event}' failed - check the service logs", kind="danger", seconds=8, category="SERVER ERROR")

@socketio.on('connect')
def handle_connect(data):
  global restored_toast_pending
  if not current_user():
    return False # reject unauthenticated socket connections
  game.sio_push() # send the newcomer the whole current state
  if restored_toast_pending: # STARTUP RESTORE NOTICE, ONCE, ON THE FIRST CONNECT
    restored_toast_pending = False
    game.sio_toast("Game restored from autosave (service restarted)", kind="success", seconds=6, category="SERVER")

@socketio.on('seat_request')
def handle_seat_request(data):
  if not current_user():
    return
  game.gui_sit(current_user(), position=(data["seat_i"] - 1))
  game.sio_push()
  game.autosave()

@socketio.on('bid_submit')
def handle_bid_submit(data):
  if not current_user():
    return
  game.gui_bid(current_user(), data['bid'])
  game.autosave()

@socketio.on('discard_submit')
def handle_discard_submit(data):
  if not current_user():
    return
  game.gui_discard(current_user(), data['discard'])
  game.autosave()

@socketio.on('play_card')
def handle_play_card(data):
  if not current_user():
    return
  game.gui_play(current_user(), data['card'])
  game.autosave()

@socketio.on('joker_nominate')
def handle_joker_nominate(data):
  if not current_user():
    return
  game.gui_joker_nominate(current_user(), data['suit'])
  game.autosave()

@socketio.on('add_bots')
def handle_add_bots(data):
  if not BOTS_ENABLED: # SERVER-SIDE ENFORCEMENT - THE CLIENT ONLY HIDES THE BUTTON
    return
  if not current_user():
    return
  # ONLY A SEATED PLAYER MAY SUMMON BOTS: OTHERWISE THEY'D FILL ALL FOUR SEATS, LOCK
  # THE REQUESTER OUT, AND THE TABLE WOULD RE-DEAL FOREVER (BOTS ALONE CAN ALL-PASS).
  # THE CLIENT HIDES THE BUTTON UNTIL SEATED TOO - THIS IS THE AUTHORITATIVE CHECK
  if not any(same_name(current_user(), p.name) for p in game.players):
    return
  log(f"'{current_user()}' requested bots to fill the empty seats")
  game.sio_toast(f"{current_user()} is filling the empty seats with bots...", category="PLAYERS")
  # ON THE WORKER QUEUE (NOT INLINE) BECAUSE SEATING PACES ITSELF WITH game.delay() AND
  # EACH gui_sit MAY TRIGGER state_trans - SAME SERIALISATION RULE AS ALL GAME MUTATIONS.
  # NO EXPLICIT AUTOSAVE HERE: EACH gui_sit AUTOSAVES VIA ITS OWN move_state/state_trans
  # PATH ONCE THE TABLE FILLS, AND A HALF-SEATED LOBBY IS HARMLESS TO LOSE
  schedule_t.jobqueue.put(lambda: bots.seat_player_bots(game))

if __name__ == '__main__':

    app.run(host='0.0.0.0', port=PORT, use_reloader=False, debug=True)
