#!/usr/bin/env python3
# DELETES EVERY EMPTY TABLE (WAITING FOR PLAYERS, NO-ONE SEATED) FROM THE RUNNING
# SERVICE VIA /admin/delete_table - THE SAME TEARDOWN THE 300s AUTO-REAPER USES, JUST
# ON DEMAND. COMPANION TO dev_spawn_bot_tables.py FOR LOAD-TESTING TIDY-UP (SPAWNED
# BOT TABLES EMPTY THEMSELVES AT GAME OVER; THIS CLEARS THE LEFTOVERS IMMEDIATELY
# INSTEAD OF WAITING OUT THE REAP TIMER).
#
# DELIBERATELY NEVER TOUCHES A TABLE WITH ANYONE SEATED OR A HAND IN PROGRESS - ONLY
# THE SAME "EMPTY" DEFINITION reap_empty_tables() USES. PURE HTTP, NO LOCAL FILE
# ACCESS NEEDED (delete_table REMOVES THE SAVE DIRECTORY SERVER-SIDE).
#
# RUN:
#   venv/bin/python dev_clear_empty_tables.py [base_url]
# DEFAULT: base_url=http://localhost:4030
# CREDS/ADMIN REQUIREMENT: SEE dev_script_common.py (auth.json, ELSE PROMPT).
# dev_ NAMING: DEVELOPMENT/QA TOOL, NOT AN ADMIN-ROLE CONCEPT (SAME REASONING AS
# dev_random_* / /dev/cards / /dev/logs).

import json
import sys
import urllib.parse

from dev_script_common import admin_session

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:4030"

get = admin_session(BASE)

removed, kept = [], []
for t in json.loads(get("/api/tables")):
    empty = t["state"] == "WAITING FOR PLAYERS" and not any(t["players"])
    if empty:
        get("/admin/delete_table?" + urllib.parse.urlencode({"table": t["name"]}))
        removed.append(t["name"])
    else:
        kept.append(f"{t['name']} ({t['state']}, {len([p for p in t['players'] if p])} seated)")

print(f"removed {len(removed)}: {', '.join(removed) if removed else '-'}")
print(f"kept    {len(kept)}: {', '.join(kept) if kept else '-'}")
