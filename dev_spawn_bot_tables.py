#!/usr/bin/env python3
# SPAWNS N SELF-PLAYING ALL-PLAYER-BOT TABLES INTO THE RUNNING SERVICE. FOR LOAD/
# SOAK TESTING (E.G. THE PER-TABLE WORKER CHANGE) - PLAYER BOTS CAN'T OTHERWISE BE
# SEATED WITHOUT A SEATED HUMAN (add_bots IS DELIBERATELY GATED THAT WAY).
#
# HOW: FOR EACH TABLE IT (1) CREATES A FRESH TABLE VIA /api/create_table, (2) WRITES A
# HAND-BUILT checkpoint.json INTO data/tables/<name>/ - A DEALING-STATE (S1) SNAPSHOT
# WITH FOUR RANDOM PLAYER BOTS SEATED - AND (3) TRIGGERS /admin/load?table=<name>.
# restore_state() RE-QUEUES auto_deal FOR A SAVE RESTORED IN DEALING, SO THE TABLE
# DEALS AND PLAYS ITSELF FOREVER (BOTS EVAPORATE ON REINIT/DELETE AS USUAL).
#
# RUN (from the repo root, any user that can reach the service + write data/tables/):
#   venv/bin/python dev_spawn_bot_tables.py [count] [base_url]
# DEFAULTS: count=3, base_url=http://localhost:4030
# MUST RUN ON THE SERVICE HOST - IT WRITES checkpoint.json STRAIGHT INTO data/tables/,
# SO CREDENTIALS (data/auth.json, OR THE INTERACTIVE PROMPT WHEN THAT'S MISSING/STALE)
# NEVER LEAVE THE MACHINE. dev_ NAMING: DEVELOPMENT/QA TOOL, NOT AN ADMIN-ROLE CONCEPT
# (SAME REASONING AS dev_random_* / /dev/cards / /dev/logs).
# CLEANUP: delete the tables via the admin table selector's DELETE TABLE (or just
# reinit them - the bots evaporate and the empty table reaps itself after 300s).

import json
import os
import sys
import time
import urllib.parse
from random import sample

from dev_script_common import REPO, admin_session
import game_state
import bots

COUNT = int(sys.argv[1]) if len(sys.argv) > 1 else 3
BASE = sys.argv[2] if len(sys.argv) > 2 else "http://localhost:4030"

get = admin_session(BASE)

def table_names():
    return {t["name"] for t in json.loads(get("/api/tables"))}

created = []
for n in range(COUNT):
    before = table_names()
    get("/api/create_table")
    new = table_names() - before
    if len(new) != 1:
        sys.exit(f"couldn't identify the created table (diff={new})")
    name = new.pop()

    # BUILD THE ALL-BOT DEALING SNAPSHOT WITH THE REAL to_dict() SO THE SHAPE ALWAYS
    # MATCHES THE RUNNING CODE'S restore_state() EXPECTATIONS
    t = game_state.GameStateMachine(name, socketio_init=None)
    for seat, bot in enumerate(bots.PLAYER_BOT_PREFIX + b for b in sample(bots.BOT_NAMES, 4)):
        t.players[seat].name = bot
        t.player_bots[seat] = {"name": bot, "personality": bots.sample_personality()}
    t.state = 1  # DEALING - RESTORE RE-QUEUES auto_deal, THE TABLE PLAYS ITSELF
    snapshot = t.to_dict()
    snapshot["save_version"] = game_state.SAVE_VERSION

    table_dir = os.path.join(REPO, "data", "tables", name)
    os.makedirs(table_dir, exist_ok=True)
    with open(os.path.join(table_dir, "checkpoint.json"), "w") as f:
        json.dump(snapshot, f)

    get("/admin/load?" + urllib.parse.urlencode({"table": name}))
    created.append((name, [t.players[s].name for s in range(4)]))

time.sleep(2)  # LET THE QUEUED RESTORES/DEALS START BEFORE THE FINAL READBACK
states = {t["name"]: t["state"] for t in json.loads(get("/api/tables"))}
for name, seated in created:
    print(f"{name}: {states.get(name, 'GONE?')} - {', '.join(seated)}")

game_state.schedule_t.stop()  # THE import SPAWNED THE MODULE WORKER THREADS - LET THE SCRIPT EXIT
