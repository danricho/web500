# SHARED PLUMBING FOR THE dev_* TABLE SCRIPTS (spawn_bot_tables / clear_empty_tables /
# clear_bot_tables): REPO PATH + ADMIN HTTP SESSION AGAINST THE RUNNING SERVICE.
# CREDENTIALS: data/auth.json WHEN PRESENT, ELSE AN INTERACTIVE PROMPT - AND AGAIN IF
# A LOGIN ATTEMPT FAILS (E.G. THE RUNNING SERVICE WAS STARTED AGAINST A DIFFERENT
# auth.json THAN THE ONE ON DISK). THE NAME MUST BE AN ADMIN (auth.json's admin_users)
# OR THE /admin/* CALLS THE SCRIPTS MAKE WILL 403.

import getpass
import http.cookiejar
import json
import os
import sys
import urllib.parse
import urllib.request

REPO = os.path.dirname(os.path.abspath(__file__))

# LOGS IN AND RETURNS get(path) -> response body, WITH THE SESSION COOKIE ATTACHED.
# EXITS THE PROCESS AFTER 3 FAILED LOGINS.
def admin_session(base):
  admin, passcode = None, None
  try:
    with open(os.path.join(REPO, "data", "auth.json")) as f:
      auth = json.load(f)
    admin = auth["admin_users"][0]
    passcode = auth["passcode"]
  except Exception:
    pass

  jar = http.cookiejar.CookieJar()
  opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

  def get(path):
    return opener.open(base + path, timeout=10).read().decode()

  def try_login(name, code):
    opener.open(base + "/login",
                data=urllib.parse.urlencode({"name": name, "passcode": code}).encode(),
                timeout=10)
    return any(c.name == "session" for c in jar)

  for attempt in range(3):
    if admin is None or passcode is None or (attempt > 0):
      admin = input(f"admin username{f' [{admin}]' if admin else ''}: ").strip() or admin
      passcode = getpass.getpass("passcode: ")
    if try_login(admin, passcode):
      return get
    print("login failed")
  sys.exit("giving up after 3 failed logins")
