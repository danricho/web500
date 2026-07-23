from datetime import datetime, timedelta
from flask import Flask, render_template, send_from_directory, send_file, request, jsonify, redirect, session
from flask_socketio import SocketIO, join_room, rooms
import os, sys, socket, json, hmac, secrets, subprocess, threading, time, traceback, re

# TO RUN VIA GUNICORN - EXACTLY ONE WORKER, THE GAME LIVES IN THIS PROCESS'S MEMORY:
# venv/bin/gunicorn -b :4030 -w 1 --threads 100 main:app

base_path = os.path.dirname(sys.argv[0]) +"/"

SERVICE_UNIT_NAME = "web-500-web-server.service" # THIS HOST'S SYSTEMD UNIT - SHARED BY
                # RESTART_COMMAND BELOW AND dev/logs' journalctl CALL

# THE SHELL COMMAND /admin/restart RUNS. THE LIVE GAME SURVIVES A RESTART - IT IS
# RESTORED FROM data/autosave.json AT STARTUP. SET TO None TO DISABLE THE BUTTON.
# NEEDS PASSWORDLESS sudo (OR A ROOT SERVICE USER) TO WORK UNATTENDED.
RESTART_COMMAND = f"sudo systemctl restart {SERVICE_UNIT_NAME}"
RESTART_DELAY_S = 0.5 # LET THE HTTP RESPONSE FLUSH BEFORE THE PROCESS IS KILLED

VERSION = "v.2026.07.22.1" # version definition
                # SINGLE SOURCE OF TRUTH - SHOWN ON THE CLIENT (MODAL CREDITS), LOGGED
                # AT STARTUP AND SERVED BY /admin/uptime. BUMP ON RELEASE.
PORT = 4030 # FLASK DEV SERVER ONLY - GUNICORN BINDS ITSELF (-b :4030 IN THE UNIT/README)
BOTS_ENABLED = True # MASTER TOGGLE FOR THE LOBBY ADD BOTS BUTTON (SERVER-ENFORCED)
DEFAULT_ADMIN_USERS = ["Nerd"] # FIRST-RUN auth.json SEED ONLY - EDIT data/auth.json AFTER

SERVICE_START = time.time() # PROCESS START - /admin/uptime REPORTS AGAINST THIS

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
# data/auth.json holds {"passcode": ..., "admin_users": [...]} and is created with a
# random passcode on first run (edit it to set your own, then restart).
# data/secret_key.txt holds the persistent session-signing key so logins survive
# service restarts. Both live in the gitignored data/ directory.
# ---------------------------------------------------------------------------
AUTH_FILE = os.path.join(DATA_DIR, "auth.json")
SECRET_KEY_FILE = os.path.join(DATA_DIR, "secret_key.txt")

def load_or_create_auth():
  os.makedirs(DATA_DIR, exist_ok=True)
  if not os.path.exists(AUTH_FILE):
    config = {"passcode": secrets.token_urlsafe(8), "admin_users": DEFAULT_ADMIN_USERS}
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

def is_admin_user():
  user = current_user()
  return user != None and any(same_name(user, d) for d in AUTH.get("admin_users", []))

# THE CALLER'S OWN TABLE, RESOLVED FROM THE LIVE SESSION - CORRECT FOR ORDINARY HTTP
# ROUTES (EACH REQUEST ALREADY REFLECTS session AS OF *THIS* REQUEST, SO THERE'S NO
# STALENESS RISK). SOCKET EVENT HANDLERS MUST NOT USE THIS - SEE _socket_table() BELOW.
def current_table():
  return tables.get(session.get("table_id"))

# SORTS "TABLE 2" BEFORE "TABLE 10" (PLAIN STRING SORT WOULD DO THE OPPOSITE)
def _table_sort_key(name):
  return int(name[6:]) if name.startswith("TABLE ") and name[6:].isdigit() else 0

# ADMIN-ONLY TABLE RESOLUTION FOR /admin/* ROUTES: AN OPTIONAL ?table= QUERY ARG (SET BY
# #admin-table-select IN THE SETTINGS MODAL) OVERRIDES current_table(), SO AN ADMIN CAN
# ACT ON ANY TABLE, NOT JUST THEIR OWN. SAFE TO EXPOSE ONLY BECAUSE EVERY admin/* ROUTE
# IS ALREADY GATED ON is_admin_user() BEFORE THIS IS EVER CALLED (SEE THE path.startswith
# ("admin/") CHECK BELOW) - THIS DOES NOT MOVE THE ADMIN'S OWN LIVE VIEW TO THAT TABLE,
# ONLY SCOPES THE ADMIN ACTION ITSELF (SEE THE COMMENT ON adminTableQuery() IN
# game_client.js)
def admin_target_table():
  table_param = request.args.get("table")
  if table_param:
    return tables.get(table_param)
  return current_table()

# PERSONALISED TABLE LIST FOR THE CURRENT SESSION (CHOOSE-TABLE PAGE + /api/tables) -
# "already_seated" LETS SOMEONE WHO'S ALREADY SEATED AT A NOW-FULL TABLE STILL REJOIN
# IT (E.G. AFTER session['table_id'] WENT STALE) EVEN THOUGH NEWCOMERS CAN'T
def _table_summaries():
  user = current_user()
  return [
    {
      "name": t.name,
      "state": t.state_name(),
      "players": [p.name for p in t.players],
      "full": all(p.name is not None for p in t.players),
      "already_seated": any(same_name(user, p.name) for p in t.players),
      "test_mode": t.test_mode,
      "skip_delays": t.skip_delays,
    }
    for t in sorted(tables.values(), key=lambda t: _table_sort_key(t.name))
  ]

socketio = SocketIO(app, transports=['polling'], logger=False)

# TABLE REGISTRY: LOADS EVERY data/tables/<name>/ SAVE FROM DISK (MIGRATING A LEGACY
# SINGLE-TABLE data/autosave.json IN PLACE IF FOUND, OR SEEDING ONE FRESH "TABLE 1" ON
# A CLEAN INSTALL) - SEE load_tables_from_disk() IN game_state.py. THE "GAME RESTORED"
# TOAST CAN'T FIRE HERE (NO CLIENT IS CONNECTED YET AT STARTUP), SO IT IS DEFERRED TO
# THE FIRST SOCKET CONNECT - BY THEN THE OLD CLIENTS ARE ALL RECONNECTING AND THE
# ROOM-SCOPED PUSH REACHES THEM (SEE handle_connect)
restored_toast_pending = load_tables_from_disk(socketio)

# WORKER-JOB FAILURES: THE FULL TRACEBACK IS ALREADY LOGGED BY threaded_schedule -
# THIS HOOK ADDITIONALLY SURFACES A TOAST. A FAILING JOB CAN BELONG TO ANY TABLE (OR
# NONE), SO THIS IS A TRUE UNSCOPED BROADCAST (NO room=) STRAIGHT VIA socketio, NOT
# THROUGH ANY PARTICULAR TABLE'S sio_toast() - RARE OPS-VISIBILITY SIGNAL, NOT
# GAMEPLAY, SO EVERYONE SEEING IT IS FINE
schedule_t.on_error = lambda job_name, tb: socketio.emit(
  'toast', data={"text": f"'{job_name}' failed - check the service logs",
                 "kind": "danger", "seconds": 8, "audience": None,
                 "category": "SERVER ERROR"})

# KEEP-ALIVE / FOCUS-IDLE / EMPTY-TABLE REAP: ONE REGISTRATION FANS OUT TO EVERY
# TABLE IN THE REGISTRY EACH TICK (poll_all_tables IN game_state.py), SO TABLES
# CREATED/REMOVED AT RUNTIME ARE PICKED UP WITHOUT RE-REGISTERING JOBS. QUEUED (NOT
# CALLED) SO IT RUNS ON THE SINGLE WORKER THAT SERIALISES ALL GAME WORK.
schedule.every(1).seconds.do(schedule_t.jobqueue.put, poll_all_tables)

@app.route('/', defaults={'path': ''}, methods=['GET', 'POST'])
@app.route('/<path:path>', methods=['GET', 'POST'])
def index(path):
  global players, test_driver_enabled

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
    target = current_table()
    if target is None:
      p_event("HMTL Request: Choose Table")
      return render_template('choose_table.j2.html', tables=_table_summaries())
    p_event("HMTL Request: Game (Client)")
    # table_id IS BAKED INTO THE PAGE (SEE game_client.j2.html) AND SENT ON THE
    # io() CALL'S QUERY STRING - THE connect HANDLER READS IT FROM THERE, NOT FROM
    # THE LIVE SESSION, SO ORDINARY POLLING-TRANSPORT RECONNECTS (WHICH HAPPEN
    # ROUTINELY, NOT JUST ON A FULL RELOAD) STAY PINNED TO THE TABLE THIS PAGE WAS
    # RENDERED FOR EVEN IF A DIFFERENT TAB SHARING THE SAME SESSION COOKIE LATER
    # SWITCHES TABLES
    # admin_tables POPULATES #admin-table-select (ADMIN-ONLY MARKUP) - ONLY WORTH COMPUTING
    # FOR AN ACTUAL ADMIN
    admin_tables = sorted(tables.keys(), key=_table_sort_key) if is_admin_user() else None
    return render_template('game_client.j2.html', home=True, table_id=target.name, admin_tables=admin_tables)

  # ADMIN ENDPOINTS REQUIRE A LOGGED-IN ADMIN USER; API HELPERS ANY LOGGED-IN PLAYER.
  # dev/cards AND dev/logs ARE DEVELOPMENT TOOLS (VISUAL CARD-RENDERING QA, RAW SERVICE
  # LOG VIEWING), NOT AN ADMIN-USER CONCEPT - SAME JUSTIFICATION AS
  # dev_random_bot/DEVELOPMENT.md - BUT STILL GATED ON is_admin_user() LIKE EVERYTHING
  # ELSE HERE, SO THEY NEED THEIR OWN EXPLICIT CHECK.
  if (path.startswith("admin/") or path in ("dev/cards", "dev/logs")) and not is_admin_user():
    return "forbidden", 403
  if path.startswith("api/") and not current_user():
    return "forbidden", 403

  if path == "api/tables": # JSON TABLE LIST (SAME SHAPE choose_table.j2.html IS RENDERED WITH)
    return jsonify(_table_summaries())

  if path == "api/select_table": # JOIN AN EXISTING TABLE (CHOOSE-TABLE PAGE)
    target = tables.get(request.args.get("id"))
    if target is None:
      return "no such table", 404
    already_seated_here = any(same_name(current_user(), p.name) for p in target.players)
    # A FULL TABLE CAN'T BE JOINED BY A NEWCOMER - THE POINT OF MULTIPLE TABLES IS TO
    # CREATE ANOTHER ONE, NOT SPECTATE A FULL ONE. SOMEONE ALREADY SEATED HERE (E.G.
    # session['table_id'] WENT STALE) CAN STILL REJOIN THEIR OWN TABLE THOUGH.
    if not already_seated_here and all(p.name is not None for p in target.players):
      return "table is full", 403
    # NAME-CASING ADOPTION (RELOCATED FROM login() - THERE'S NO TABLE TO CHECK
    # AGAINST AT LOGIN TIME ANY MORE): IF THIS NAME IS ALREADY SEATED AT *THIS*
    # TABLE, ADOPT ITS EXACT SPELLING
    for player in target.players:
      if same_name(player.name, current_user()):
        session['username'] = player.name
        break
    session['table_id'] = target.name
    log(f"'{current_user()}' selected table '{target.name}'")
    return redirect("/")

  if path == "api/create_table": # CREATE + SELECT A FRESH TABLE (CHOOSE-TABLE PAGE)
    target = create_table(socketio)
    session['table_id'] = target.name
    log(f"'{current_user()}' created table '{target.name}'")
    return redirect("/")

  if path == "api/change_table": # BACK TO THE TABLE PICKER - EVEN IF ALREADY SEATED
    target = current_table()
    if target is not None and target.state_name() == "WAITING FOR PLAYERS":
      # NOTHING IS AT STAKE YET (NO CARDS DEALT) - PROPERLY VACATE THE SEAT RATHER
      # THAN ORPHANING IT, SO THE TABLE PICKER'S "PLAYERS ALREADY HERE" LIST STAYS
      # ACCURATE. ONCE DEALING HAS STARTED THERE'S NO UN-SIT MECHANISM (SEE THE
      # "Change player" ROADMAP ITEM) - THE SEAT STAYS ORPHANED THEN (BELOW).
      for player in target.players:
        if same_name(player.name, current_user()):
          player.name = None
          break
      target.sio_push()
      target.autosave()
    session.pop('table_id', None)
    log(f"'{current_user()}' returned to table selection")
    return redirect("/")

  if path == "api/client_trigger_push":
    target = current_table()
    if target:
      target.sio_push()

  if path == "api/reinit": # ANY LOGGED-IN PLAYER MAY RESET THEIR OWN TABLE (SETTINGS MODAL)
    target = current_table()
    if target is None:
      return "no table selected", 400
    log(f"Flask Request to reinitialise table '{target.name}' (by '{current_user()}')")
    if target.state_name() != "DEALING":
      target.__init__(target.name, socketio_init=socketio) # KEEPS THE TABLE'S OWN NAME
      target.clear_autosave() # init means a true fresh start - forget the persisted game
      target.sio_push()
      target.sio_toast(f"Table reinitialised by {current_user()}", kind="warning", seconds=6, category="GAME MANAGEMENT")
    return "ok"

  if path == "dev/cards": # VISUAL REVIEW OF THE CARD BACK + ALL 43 FACES (SHARED PROTO CARD)
    return render_template('cards_review.j2.html')

  if path == "dev/logs": # RAW SERVICE LOG VIEWER - READS VIA THE SAME PASSWORDLESS
    # sudo journalctl RESTART_COMMAND ALREADY RELIES ON, SO NO EXTRA HOST SETUP NEEDED.
    # ?n= CONTROLS HOW MANY TRAILING LINES (DEFAULTS TO 1000; NON-DIGIT/MISSING FALLS BACK).
    n = request.args.get("n", "1000")
    n = n if n.isdigit() else "1000"
    try:
      result = subprocess.run(
        ["sudo", "-n", "journalctl", "-u", SERVICE_UNIT_NAME, "--no-pager", "-n", n],
        capture_output=True, text=True, timeout=10)
      log_text = result.stdout if result.returncode == 0 else (result.stdout + result.stderr)
      # WHITELIST, NOT BLACKLIST: KEEP ONLY LINES THAT CARRY OUR OWN log()/debug()/
      # dialog()/state_trans() TIMESTAMP TAG ("[YYYY-MM-DD HH:MM:SS]" WITH NO TIMEZONE -
      # THAT'S WHAT DISTINGUISHES IT FROM GUNICORN'S OWN "[... +1000]" BOOT-LINE FORMAT),
      # I.E. SOMETHING THIS REPO ACTUALLY PRINTED. THIS DROPS ALL OF: sudo'S PAM/AUDIT
      # LINES, systemd's START/STOP/KILL CHATTER, AND GUNICORN'S OWN [INFO] BOOT LINES -
      # NONE OF WHICH HAVE A print() CALL BACKING THEM IN THIS REPO - IN ONE RULE, NO
      # PER-PATTERN BLACKLIST TO MAINTAIN. ALSO STRIPS journalctl'S OWN
      # "Mon DD HH:MM:SS host process[pid]: " PREFIX SO EACH LINE STARTS AT OUR BRACKET.
      # (NOTE: A HANDFUL OF REPO print()s DON'T USE THIS BRACKET FORMAT - THE ONE-TIME
      # save_state MIGRATION LINE, THE RARELY-ENABLED FLASK p_debug/p_event/... REQUEST
      # LOG, AND threaded_schedule's WORKER JOB FAILED TRACEBACK - THOSE ARE FILTERED OUT
      # TOO. WORKER JOB FAILURES STILL REACH PLAYERS VIA THE on_error DANGER TOAST; A
      # RAW `sudo journalctl` STILL SHOWS EVERYTHING FOR DEEPER DEBUGGING.)
      TIMESTAMP_RE = re.compile(r"\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]")
      kept = []
      for line in log_text.splitlines():
        m = TIMESTAMP_RE.search(line)
        if m:
          kept.append(line[m.start():])
      log_text = "\n".join(kept)
    except Exception as e:
      log_text = f"FAILED TO READ JOURNAL: {e}"
    return render_template('dev_logs.j2.html', log_text=log_text, lines=n)

  if path == "admin/reinit": # ADMIN-ONLY: REINIT ANY TABLE VIA #admin-table-select
    target = admin_target_table()
    if target is None:
      return "no table selected", 400
    log(f"Flask Request to reinitialise table '{target.name}' (ADMIN FEATURE, by '{current_user()}')")
    if target.state_name() != "DEALING":
      target.__init__(target.name, socketio_init=socketio) # KEEPS THE TABLE'S OWN NAME
      target.clear_autosave()
      target.sio_push()
      target.sio_toast(f"Table reinitialised by {current_user()} (admin)", kind="warning", seconds=6, category="GAME MANAGEMENT")
    return "ok"

  if path == "admin/delete_table": # ADMIN-ONLY: PERMANENTLY DELETE ANY TABLE (SAME TEARDOWN AS REAPING)
    target = admin_target_table()
    if target is None:
      return "no table selected", 400
    name = target.name
    log(f"Flask Request to DELETE table '{name}' (ADMIN FEATURE, by '{current_user()}')")
    delete_table(name, f"deleted by {current_user()}")
    return "ok"

  if path == "admin/save":
    target = admin_target_table()
    if target is None:
      return "no table selected", 400
    log(f"Flask Request to save admin checkpoint (ADMIN FEATURE)")
    target.save_state(target.checkpoint_path, reason="(admin checkpoint)")
    target.sio_toast(f"Checkpoint saved by {current_user()}", kind="success", category="GAME MANAGEMENT")
    return "ok"

  if path == "admin/load":
    target = admin_target_table()
    if target is None:
      return "no table selected", 400
    log(f"Flask Request to load admin checkpoint (ADMIN FEATURE)")
    user = current_user() # CAPTURED NOW - THE WORKER THREAD HAS NO REQUEST CONTEXT
    def load_and_toast():
      if target.restore_state(target.checkpoint_path):
        target.sio_toast(f"Checkpoint loaded by {user}", kind="success", seconds=6, category="GAME MANAGEMENT")
      else:
        target.sio_toast(f"Checkpoint load FAILED (requested by {user})", kind="danger", seconds=6, category="GAME MANAGEMENT")
    schedule_t.jobqueue.put(load_and_toast) # on the worker so it can't interleave with auto jobs
    return "ok"

  if path == "admin/clearchk":
    target = admin_target_table()
    if target is None:
      return "no table selected", 400
    log(f"Flask Request to clear admin checkpoint (ADMIN FEATURE)")
    if os.path.exists(target.checkpoint_path):
      os.remove(target.checkpoint_path)
      target.sio_toast(f"Checkpoint cleared by {current_user()}", category="GAME MANAGEMENT")
    else:
      target.sio_toast("No checkpoint to clear", category="SERVER ERROR")
    return "ok"

  if path == "admin/test":
    target = admin_target_table()
    if target is None:
      return "no table selected", 400
    log(f"Flask Request to toggle test mode automation (ADMIN FEATURE)")
    target.test_mode = not target.test_mode
    if target.test_mode:
      schedule_t.jobqueue.put(lambda: bots.dev_random_seat_bots(target))
    target.sio_push() # also queues a dev_random_bot_check if test mode is now on
    target.sio_toast(f"Test mode {'enabled' if target.test_mode else 'disabled'} by {current_user()}",
                   kind="warning" if target.test_mode else "info", category="GAME MANAGEMENT")
    return "ok"
  if path == "admin/skipdelays":
    target = admin_target_table()
    if target is None:
      return "no table selected", 400
    log(f"Flask Request to toggle skip delays (ADMIN FEATURE)")
    target.skip_delays = not target.skip_delays
    target.sio_push()
    target.sio_toast(f"Skip delays {'enabled' if target.skip_delays else 'disabled'} by {current_user()}",
                   kind="warning" if target.skip_delays else "info", category="GAME MANAGEMENT")
    return "ok"

  if path == "admin/uptime":
    return jsonify({"version": VERSION,
                    "started": SERVICE_START, "uptime": time.time() - SERVICE_START,
                    "restart_enabled": bool(RESTART_COMMAND)})

  if path == "admin/restart":
    if not RESTART_COMMAND:
      return "restart command not configured", 501
    log(f"Flask Request to RESTART THE SERVICE (by '{current_user()}'): {RESTART_COMMAND}")
    # TOAST FIRST FOR THE BEST CHANCE OF REACHING THE OPEN LONG-POLLS BEFORE THE
    # PROCESS DIES (RESTART_DELAY_S LATER). RESTART AFFECTS EVERY TABLE, SO THIS IS A
    # TRUE UNSCOPED BROADCAST, NOT ROOM-SCOPED TO ANY ONE TABLE
    socketio.emit('toast', data={"text": f"Service restarting now (by {current_user()}) - hold tight...",
                                  "kind": "warning", "seconds": 8, "audience": None, "category": "SERVER"})
    for t in tables.values(): # EVERY TABLE IS RESTORED FROM ITS OWN AUTOSAVE ON THE WAY BACK UP
      t.save_state(t.autosave_path, reason="(pre-restart)")
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
  # NAME-CASING ADOPTION (IF THIS NAME IS ALREADY SEATED SOMEWHERE, ADOPT ITS EXACT
  # SPELLING) HAPPENS AT TABLE-SELECTION TIME NOW, NOT HERE - LOGIN HAS NO TABLE YET
  # TO CHECK AGAINST (SEE /api/select_table)
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
      'session_is_admin': is_admin_user(),
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
# WORKER-QUEUE on_error HOOK: FULL TRACEBACK TO THE LOG + A DANGER TOAST. UNSCOPED
# BROADCAST, NOT ROOM-SCOPED - THE FAILURE MAY HAVE HAPPENED BEFORE ANY TABLE COULD
# BE RESOLVED (E.G. DURING connect ITSELF), SO THERE'S NO RELIABLE TABLE TO SCOPE TO.
@socketio.on_error_default
def handle_socket_error(e):
  event = request.event["message"] if hasattr(request, "event") else "?"
  log(f"SOCKET HANDLER FAILED: '{event}'\n{traceback.format_exc()}")
  socketio.emit('toast', data={"text": f"'{event}' failed - check the service logs",
                                "kind": "danger", "seconds": 8, "audience": None,
                                "category": "SERVER ERROR"})

# RESOLVES WHICH TABLE *THIS CONNECTED SOCKET* BELONGS TO, FOR USE INSIDE ACTION
# HANDLERS (seat_request, play_card, ...). DELIBERATELY NOT current_table() (WHICH
# READS THE LIVE session) - A SOCKET'S ROOM MEMBERSHIP IS FIXED AT connect() TIME
# (SEE handle_connect) AND MUST STAY THAT WAY FOR THE LIFE OF THE CONNECTION, EVEN IF
# session['table_id'] LATER CHANGES FROM ANOTHER TAB SHARING THE SAME COOKIE -
# OTHERWISE THIS SOCKET'S PUSHES (PINNED TO ITS JOINED ROOM) AND ITS ACTIONS (IF THEY
# READ THE LIVE SESSION) COULD SILENTLY DIVERGE, ACTING ON A DIFFERENT TABLE THAN THE
# ONE THE PLAYER IS ACTUALLY LOOKING AT
def _socket_table():
  for room in rooms():
    if room in tables:
      return tables[room]
  return None

@socketio.on('connect')
def handle_connect(data):
  global restored_toast_pending
  if not current_user():
    return False # reject unauthenticated socket connections
  # table_id RIDES THE io() HANDSHAKE'S QUERY STRING (SET FROM THE PAGE RENDER, SEE
  # index() ABOVE) - NOT THE LIVE SESSION - SO IT'S RE-SENT UNCHANGED ON EVERY
  # ORDINARY RECONNECT, NOT JUST A FULL PAGE LOAD (SEE THE COMMENT AT THE RENDER
  # SITE). REJECT IF IT DOESN'T RESOLVE TO A LIVE TABLE (E.G. ALREADY REAPED).
  target = tables.get(request.args.get("table_id"))
  if target is None:
    return False
  join_room(target.name) # MUST HAPPEN BEFORE THE PUSH BELOW OR THIS SOCKET MISSES IT
  target.sio_push() # send the newcomer the whole current state (room-scoped)
  if restored_toast_pending: # STARTUP RESTORE NOTICE, ONCE, ON THE FIRST CONNECT
    restored_toast_pending = False
    target.sio_toast("Game restored from autosave (service restarted)", kind="success", seconds=6, category="SERVER")

@socketio.on('seat_request')
def handle_seat_request(data):
  if not current_user():
    return
  target = _socket_table()
  if target is None:
    return
  target.gui_sit(current_user(), position=(data["seat_i"] - 1))
  target.sio_push()
  target.autosave()

@socketio.on('bid_submit')
def handle_bid_submit(data):
  if not current_user():
    return
  target = _socket_table()
  if target is None:
    return
  target.gui_bid(current_user(), data['bid'])
  target.autosave()

@socketio.on('discard_submit')
def handle_discard_submit(data):
  if not current_user():
    return
  target = _socket_table()
  if target is None:
    return
  target.gui_discard(current_user(), data['discard'])
  target.autosave()

@socketio.on('play_card')
def handle_play_card(data):
  if not current_user():
    return
  target = _socket_table()
  if target is None:
    return
  target.gui_play(current_user(), data['card'])
  target.autosave()

@socketio.on('joker_nominate')
def handle_joker_nominate(data):
  if not current_user():
    return
  target = _socket_table()
  if target is None:
    return
  target.gui_joker_nominate(current_user(), data['suit'])
  target.autosave()

@socketio.on('add_bots')
def handle_add_bots(data):
  if not BOTS_ENABLED: # SERVER-SIDE ENFORCEMENT - THE CLIENT ONLY HIDES THE BUTTON
    return
  if not current_user():
    return
  target = _socket_table()
  if target is None:
    return
  # ONLY A SEATED PLAYER MAY SUMMON BOTS: OTHERWISE THEY'D FILL ALL FOUR SEATS, LOCK
  # THE REQUESTER OUT, AND THE TABLE WOULD RE-DEAL FOREVER (BOTS ALONE CAN ALL-PASS).
  # THE CLIENT HIDES THE BUTTON UNTIL SEATED TOO - THIS IS THE AUTHORITATIVE CHECK
  if not any(same_name(current_user(), p.name) for p in target.players):
    return
  log(f"'{current_user()}' requested bots to fill the empty seats at '{target.name}'")
  target.sio_toast(f"{current_user()} is filling the empty seats with bots...", category="PLAYERS")
  # ON THE WORKER QUEUE (NOT INLINE) BECAUSE SEATING PACES ITSELF WITH game.delay() AND
  # EACH gui_sit MAY TRIGGER state_trans - SAME SERIALISATION RULE AS ALL GAME MUTATIONS.
  # NO EXPLICIT AUTOSAVE HERE: EACH gui_sit AUTOSAVES VIA ITS OWN move_state/state_trans
  # PATH ONCE THE TABLE FILLS, AND A HALF-SEATED LOBBY IS HARMLESS TO LOSE
  schedule_t.jobqueue.put(lambda: bots.seat_player_bots(target))

if __name__ == '__main__':

    app.run(host='0.0.0.0', port=PORT, use_reloader=False, debug=True)
