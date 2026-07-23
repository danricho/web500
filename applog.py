# SHARED LOG-LINE PRINTER - THE SINGLE SOURCE OF TRUTH FOR THE "[YYYY-MM-DD HH:MM:SS] ..."
# SHAPE EVERY PART OF THIS APP PRINTS (main.py's log(), GameStateMachine's log()/debug()/
# dialog()/move_state() in game_state.py). ONE PLACE NOW OWNS THE FORMAT, WHICH MATTERS
# BECAUSE /dev/logs (SEE main.py) WHITELISTS LINES ON THIS EXACT TIMESTAMP BRACKET TO
# TELL OUR OWN OUTPUT APART FROM GUNICORN/systemd/sudo NOISE.
from datetime import datetime
import re
from html import escape

# NAMED ANSI CODES - THE ONLY PLACE THIS APP SHOULD SPELL OUT A RAW "\033[...m" ESCAPE.
# EVERY color= ARGUMENT PASSED TO scoped()/tabled() (FROM main.py AND game_state.py)
# SHOULD REFERENCE ONE OF THESE RATHER THAN A LITERAL, SO THE PALETTE STAYS IN ONE PLACE.
# EACH ONE'S SGR CODE IS ALSO A KEY IN _CSS_BY_SGR BELOW - ADD BOTH TOGETHER.
RESET = "\033[0m"
BOLD = "\033[1m"
RED = "\033[31m"
GREEN = "\033[32m"
BLUE = "\033[94m"
CYAN = "\033[36m"
MAGENTA = "\033[35m"

def _timestamp():
  return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# "[ts] SCOPE :::: message" - main.py's SHAPE (NO PER-CALLER BREAKDOWN - FLASK ROUTES
# AREN'T NAMED METHODS THE WAY GameStateMachine's ARE)
def scoped(scope, message, color=""):
  print(f"[{_timestamp()}] {color}{scope} :::: {message}{RESET}")

# "[ts] SCOPE | message" - GameStateMachine's SHAPE (ONE LOG PER TABLE)
def tabled(scope, message, color=""):
  print(f"[{_timestamp()}] {color}{scope} | {message}{RESET}")

# CSS EQUIVALENT PER SGR CODE (THE NUMBER(S) INSIDE "\033[...m") - THE SAME PALETTE THE
# TERMINAL USES, RE-EXPRESSED FOR /dev/logs (SEE main.py) TO RENDER IN THE BROWSER. THE
# COLORS ARE THE APP'S OWN DESIGN-TOKEN CSS VARIABLES (static/styling.css's --fg-hue-*),
# NOT HARDCODED HEX, SO THIS STAYS IN STEP WITH ANY THEME TWEAK THERE.
_CSS_BY_SGR = {
  "1": "font-weight:bold",
  "31": "color:var(--fg-hue-5)",   # RED
  "32": "color:var(--fg-hue-4)",   # GREEN
  "35": "color:var(--fg-hue-3)",   # MAGENTA
  "36": "color:var(--fg-hue-1)",   # CYAN
  "94": "color:var(--fg-hue-2)",   # BLUE
}
_SGR_RE = re.compile(r"\x1b\[([\d;]*)m")

# CONVERTS ONE PRINTED LINE'S EMBEDDED "\033[...m" CODES INTO EQUIVALENT <span style=...>
# HTML - EVERY SGR CODE THIS APP ACTUALLY EMITS (SEE THE NAMED CONSTANTS ABOVE) IS COVERED
# BY _CSS_BY_SGR; AN UNKNOWN/UNMAPPED CODE IS SILENTLY IGNORED (TEXT STAYS UNSTYLED RATHER
# THAN BREAKING THE LINE). PLAIN TEXT SEGMENTS ARE HTML-ESCAPED HERE, SO THE CALLER MUST
# RENDER THE RESULT WITH JINJA'S |safe (IT IS ALREADY-ESCAPED HTML, NOT RAW TEXT).
def to_html(line):
  out = []
  active = []  # CSS DECLARATIONS CURRENTLY "ON" - A BARE RESET (CODE 0) CLEARS ALL OF THEM
  pos = 0
  for m in _SGR_RE.finditer(line):
    text = line[pos:m.start()]
    if text:
      out.append(f'<span style="{";".join(active)}">{escape(text)}</span>' if active else escape(text))
    codes = m.group(1).split(";") if m.group(1) else ["0"]
    for code in codes:
      if code in ("", "0"):
        active = []
      elif code in _CSS_BY_SGR:
        active.append(_CSS_BY_SGR[code])
    pos = m.end()
  tail = line[pos:]
  if tail:
    out.append(f'<span style="{";".join(active)}">{escape(tail)}</span>' if active else escape(tail))
  return "".join(out)
