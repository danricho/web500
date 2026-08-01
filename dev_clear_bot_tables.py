#!/usr/bin/env python3
# DELETES EVERY TABLE WITH NO HUMAN SEATED - I.E. EVERY SEAT IS EITHER VACANT OR A
# BOT (B|/D| NAME PREFIX) - VIA /admin/delete_table, REGARDLESS OF GAME STATE (A
# MID-HAND ALL-BOT GAME IS FAIR GAME; THAT'S THE POINT). THE HEAVY-DUTY COMPANION TO
# dev_clear_empty_tables.py FOR TEARING DOWN A dev_spawn_bot_tables.py LOAD-TEST
# SESSION IN ONE GO INSTEAD OF WAITING FOR EVERY BOT GAME TO FINISH. EMPTY TABLES
# COUNT AS "NO HUMAN" AND GO TOO. ANY TABLE WITH AT LEAST ONE HUMAN SEATED IS KEPT -
# EVEN ONE HUMAN AMONG THREE BOTS.
#
# RUN:
#   venv/bin/python dev_clear_bot_tables.py [base_url] [--dry-run]
# DEFAULT: base_url=http://localhost:4030; --dry-run LISTS WHAT WOULD GO, DELETES NOTHING.
# CREDS/ADMIN REQUIREMENT: SEE dev_script_common.py (auth.json, ELSE PROMPT).
# dev_ NAMING: DEVELOPMENT/QA TOOL, NOT AN ADMIN-ROLE CONCEPT (SAME REASONING AS
# dev_random_* / /dev/cards / /dev/logs).

import json
import sys
import urllib.parse

from dev_script_common import admin_session
from bots import PLAYER_BOT_PREFIX, DEV_RANDOM_BOT_PREFIX  # SINGLE SOURCE FOR THE PREFIXES

args = [a for a in sys.argv[1:] if a != "--dry-run"]
DRY_RUN = "--dry-run" in sys.argv[1:]
BASE = args[0] if args else "http://localhost:4030"

get = admin_session(BASE)

def is_human(name):
    return name is not None and not str(name).startswith((PLAYER_BOT_PREFIX, DEV_RANDOM_BOT_PREFIX))

removed, kept = [], []
for t in json.loads(get("/api/tables")):
    if any(is_human(p) for p in t["players"]):
        kept.append(f"{t['name']} ({t['state']}, humans: {', '.join(p for p in t['players'] if is_human(p))})")
        continue
    if not DRY_RUN:
        get("/admin/delete_table?" + urllib.parse.urlencode({"table": t["name"]}))
    removed.append(f"{t['name']} ({t['state']})")

verb = "would remove" if DRY_RUN else "removed"
print(f"{verb} {len(removed)}: {', '.join(removed) if removed else '-'}")
print(f"kept {len(kept)}: {', '.join(kept) if kept else '-'}")
