# OPTIONAL PUSH NOTIFICATIONS VIA A SELF-HOSTED ntfy (https://ntfy.sh/) SERVER.
# DISABLED BY DEFAULT. CONFIG LIVES IN data/ntfy.json (GITIGNORED, LIKE auth.json AND
# secret_key.txt) - AUTO-CREATED WITH enabled:false ON FIRST RUN SO A FRESH CHECKOUT
# NEVER PHONES HOME; EDIT THAT FILE (NO RESTART NEEDED - RELOADED EACH SEND) TO POINT
# IT AT A REAL SERVER/TOPIC. send() FIRES A DAEMON THREAD WITH A SHORT TIMEOUT SO A
# SLOW/DEAD ntfy SERVER CAN NEVER STALL A GAME ACTION (SAME REASONING AS /admin/restart's
# DEFERRED Popen IN main.py).
import json
import os
import threading
import urllib.request

import applog

NTFY_FILE = None  # SET BY init() ONCE game_state.DATA_DIR IS KNOWN

def init(data_dir):
  global NTFY_FILE
  NTFY_FILE = os.path.join(data_dir, "ntfy.json")
  if not os.path.exists(NTFY_FILE):
    with open(NTFY_FILE, "w") as f:
      json.dump({
        "enabled": False,
        "server": "https://ntfy.example.com",
        "topic": "web500",
        "auth_token": None,  # OPTIONAL BEARER TOKEN, FOR A SELF-HOSTED SERVER WITH AUTH
      }, f, indent=2)
    applog.scoped("NTFY", f"created {NTFY_FILE} (disabled by default) - edit it to enable", color=applog.CYAN)

def _load_config():
  try:
    with open(NTFY_FILE) as f:
      return json.load(f)
  except Exception:
    return {}

def _post(server, topic, auth_token, title, message):
  url = server.rstrip("/") + "/" + topic
  req = urllib.request.Request(url, data=message.encode("utf-8"), method="POST")
  # SOME REVERSE PROXIES / WAFs BLOCK urllib's DEFAULT "Python-urllib/x.y" UA STRING
  # OUTRIGHT (SEEN AS A 403 AGAINST A KNOWN-WORKING ntfy TOPIC) - ANY OTHER STRING PASSES.
  req.add_header("User-Agent", "web500-ntfy/1.0")
  req.add_header("Title", title)
  if auth_token:
    req.add_header("Authorization", f"Bearer {auth_token}")
  try:
    urllib.request.urlopen(req, timeout=5)
  except Exception as e:
    applog.scoped("NTFY", f"send failed: {e}", color=applog.RED)

# FIRE-AND-FORGET - NEVER RAISES, NEVER BLOCKS THE CALLER. NO-OP IF DISABLED/UNCONFIGURED
# OR IF init() WAS NEVER CALLED (e.g. A SCRIPT IMPORTING game_state DIRECTLY, NOT VIA main.py).
def send(title, message):
  if NTFY_FILE is None:
    return
  config = _load_config()
  if not config.get("enabled"):
    return
  server = config.get("server")
  topic = config.get("topic")
  if not server or not topic:
    return
  threading.Thread(target=_post, args=(server, topic, config.get("auth_token"), title, message), daemon=True).start()
