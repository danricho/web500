// ---------------------------------------------------------------------------
// SVG.JS — single source of truth for every SVG icon the client injects or
// swaps at runtime (robot/bot markers, suit icons, small toolbar icons).
// Every entry is a fully-formed `<svg>...</svg>` string, never a bare path,
// so a visual swap only ever touches this file. Loaded by game_client.js,
// cards_review.j2.html AND choose_table.j2.html — none of those last two can
// load game_client.js itself (cards_review would open a socket and drive a
// game DOM it doesn't have; choose_table deliberately carries no
// jQuery/socket.io at all - see its own comment) but neither has a problem
// with this file: it's pure data + functions, no game/DOM logic, and the
// jQuery bits below are opt-in (only defined if jQuery is present). Card
// encodings (suit numbers) match playing_cards.py — see CLAUDE.md "Card
// model".
// ---------------------------------------------------------------------------

// SUIT ICONS — keyed by the numeric suit (1=Spades 2=Clubs 3=Diamonds
// 4=Hearts; Misère/No Trumps have no icon, rendered as text by callers).
// No class baked in here on purpose: callers pick the size via CSS class
// per context — "inline-icon" (1em, buttons/chips) or "inline-icon-cards"
// (0.75em, on-card corners) — see styling.css and suitIconSvg() below.
var SUIT_ICON_SVG = {
  4: `<svg xmlns="http://www.w3.org/2000/svg" fill="currentColor" class="bootstrap-icons bi-suit-heart-fill" viewBox="0 0 16 16"><path d="M4 1c2.21 0 4 1.755 4 3.92C8 2.755 9.79 1 12 1s4 1.755 4 3.92c0 3.263-3.234 4.414-7.608 9.608a.513.513 0 0 1-.784 0C3.234 9.334 0 8.183 0 4.92 0 2.755 1.79 1 4 1"/></svg>`,
  3: `<svg xmlns="http://www.w3.org/2000/svg" fill="currentColor" class="bootstrap-icons bi-suit-diamond-fill" viewBox="0 0 16 16"><path d="M2.45 7.4 7.2 1.067a1 1 0 0 1 1.6 0L13.55 7.4a1 1 0 0 1 0 1.2L8.8 14.933a1 1 0 0 1-1.6 0L2.45 8.6a1 1 0 0 1 0-1.2"/></svg>`,
  2: `<svg xmlns="http://www.w3.org/2000/svg" fill="currentColor" class="bootstrap-icons bi-suit-club-fill" viewBox="0 0 16 16"><path d="M11.5 12.5a3.5 3.5 0 0 1-2.684-1.254 20 20 0 0 0 1.582 2.907c.231.35-.02.847-.438.847H6.04c-.419 0-.67-.497-.438-.847a20 20 0 0 0 1.582-2.907 3.5 3.5 0 1 1-2.538-5.743 3.5 3.5 0 1 1 6.708 0A3.5 3.5 0 1 1 11.5 12.5"/></svg>`,
  1: `<svg xmlns="http://www.w3.org/2000/svg" fill="currentColor" class="bootstrap-icons bi-suit-spade-fill" viewBox="0 0 16 16"><path d="M7.184 11.246A3.5 3.5 0 0 1 1 9c0-1.602 1.14-2.633 2.66-4.008C4.986 3.792 6.602 2.33 8 0c1.398 2.33 3.014 3.792 4.34 4.992C13.86 6.367 15 7.398 15 9a3.5 3.5 0 0 1-6.184 2.246 20 20 0 0 0 1.582 2.907c.231.35-.02.847-.438.847H6.04c-.419 0-.67-.497-.438-.847a20 20 0 0 0 1.582-2.907"/></svg>`,
};

// PREFIX ICONS — player names arrive from the server as "PREFIX|Name" (bot
// markers, see CLAUDE.md "Player bots" / "Admin endpoints"); displayName()
// swaps the prefix for one of these. No wrapping span - sized/aligned directly
// on the <svg> via the ".bot-icon" CSS class (Bootstrap Icons' own documented
// inline-with-text convention: em-based size + a small negative vertical-align
// offset, both scaling together so it stays centered against the surrounding
// text at any font-size - see styling.css). Trailing space after each svg is
// deliberate (visual gap before the name text).
var PREFIX_ICON_SVG = {
  "B|": '<svg xmlns="http://www.w3.org/2000/svg" fill="currentColor" class="bootstrap-icons bi-robot bot-icon" viewBox="0 0 16 16"><path d="M6 12.5a.5.5 0 0 1 .5-.5h3a.5.5 0 0 1 0 1h-3a.5.5 0 0 1-.5-.5M3 8.062C3 6.76 4.235 5.765 5.53 5.886a26.6 26.6 0 0 0 4.94 0C11.765 5.765 13 6.76 13 8.062v1.157a.93.93 0 0 1-.765.935c-.845.147-2.34.346-4.235.346s-3.39-.2-4.235-.346A.93.93 0 0 1 3 9.219zm4.542-.827a.25.25 0 0 0-.217.068l-.92.9a25 25 0 0 1-1.871-.183.25.25 0 0 0-.068.495c.55.076 1.232.149 2.02.193a.25.25 0 0 0 .189-.071l.754-.736.847 1.71a.25.25 0 0 0 .404.062l.932-.97a25 25 0 0 0 1.922-.188.25.25 0 0 0-.068-.495c-.538.074-1.207.145-1.98.189a.25.25 0 0 0-.166.076l-.754.785-.842-1.7a.25.25 0 0 0-.182-.135"/><path d="M8.5 1.866a1 1 0 1 0-1 0V3h-2A4.5 4.5 0 0 0 1 7.5V8a1 1 0 0 0-1 1v2a1 1 0 0 0 1 1v1a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-1a1 1 0 0 0 1-1V9a1 1 0 0 0-1-1v-.5A4.5 4.5 0 0 0 10.5 3h-2zM14 7.5V13a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V7.5A3.5 3.5 0 0 1 5.5 4h5A3.5 3.5 0 0 1 14 7.5"/></svg> ',
  "D|": '<svg xmlns="http://www.w3.org/2000/svg" fill="currentColor" class="bootstrap-icons bi-nut-fill bot-icon" viewBox="0 0 16 16"><path d="M4.58 1a1 1 0 0 0-.868.504l-3.428 6a1 1 0 0 0 0 .992l3.428 6A1 1 0 0 0 4.58 15h6.84a1 1 0 0 0 .868-.504l3.429-6a1 1 0 0 0 0-.992l-3.429-6A1 1 0 0 0 11.42 1zm5.018 9.696a3 3 0 1 1-3-5.196 3 3 0 0 1 3 5.196"/></svg> ',
};

// APPEND ICONS — 24x24 toolbar-style icons (materialdesignicons.com), only
// ever used via $.fn.appendSvg below, which stamps an id/extra class onto a
// fresh clone per call site.
var APPEND_ICON_SVG = {
  "cloud-arrow-down": `<svg class="bootstrap-icons bi-cloud-arrow-down" xmlns="http://www.w3.org/2000/svg" width="24" height="24" fill="currentColor" viewBox="0 0 16 16" stroke="none"><path fill-rule="evenodd" d="M7.646 10.854a.5.5 0 0 0 .708 0l2-2a.5.5 0 0 0-.708-.708L8.5 9.293V5.5a.5.5 0 0 0-1 0v3.793L6.354 8.146a.5.5 0 1 0-.708.708l2 2z"/><path d="M4.406 3.342A5.53 5.53 0 0 1 8 2c2.69 0 4.923 2 5.166 4.579C14.758 6.804 16 8.137 16 9.773 16 11.569 14.502 13 12.687 13H3.781C1.708 13 0 11.366 0 9.318c0-1.763 1.266-3.223 2.942-3.593.143-.863.698-1.723 1.464-2.383zm.653.757c-.757.653-1.153 1.44-1.153 2.056v.448l-.445.049C2.064 6.805 1 7.952 1 9.318 1 10.785 2.23 12 3.781 12h8.906C13.98 12 15 10.988 15 9.773c0-1.216-1.02-2.228-2.313-2.228h-.5v-.5C12.188 4.825 10.328 3 8 3a4.53 4.53 0 0 0-2.941 1.1z"/></svg>`,
  "cloud-slash": `<svg class="bootstrap-icons bi-cloud-slash" xmlns="http://www.w3.org/2000/svg" width="24" height="24" fill="currentColor" class="bi bi-cloud-slash" viewBox="0 0 16 16"><path fill-rule="evenodd" d="M3.112 5.112a3 3 0 0 0-.17.613C1.266 6.095 0 7.555 0 9.318 0 11.366 1.708 13 3.781 13H11l-1-1H3.781C2.231 12 1 10.785 1 9.318c0-1.365 1.064-2.513 2.46-2.666l.446-.05v-.447q0-.113.018-.231zm2.55-1.45-.725-.725A5.5 5.5 0 0 1 8 2c2.69 0 4.923 2 5.166 4.579C14.758 6.804 16 8.137 16 9.773a3.2 3.2 0 0 1-1.516 2.711l-.733-.733C14.498 11.378 15 10.626 15 9.773c0-1.216-1.02-2.228-2.313-2.228h-.5v-.5C12.188 4.825 10.328 3 8 3c-.875 0-1.678.26-2.339.661z"/><path d="m13.646 14.354-12-12 .708-.708 12 12z"/></svg>`,
  // below are based on bootstrap icons *-square-fill with characters in font "Inter" from Google Fonts
  "dealer-chip": `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M9.1,5.7C8.7,5.5,8.3,5.4,7.8,5.4H6.7v5.2h1c0.5,0,1-0.1,1.4-0.3c0.3-0.2,0.6-0.5,0.8-0.9C10,9.1,10.1,8.6,10.1,8 c0-0.6-0.1-1.1-0.3-1.5C9.7,6.2,9.4,5.9,9.1,5.7z"/><path d="M14,0H2C0.9,0,0,0.9,0,2v12c0,1.1,0.9,2,2,2h12c1.1,0,2-0.9,2-2V2C16,0.9,15.1,0,14,0z M11.4,10.3 c-0.3,0.6-0.8,1.2-1.4,1.5c-0.6,0.3-1.4,0.5-2.2,0.5H4.9V3.8h3c0.8,0,1.6,0.2,2.2,0.5c0.6,0.3,1.1,0.8,1.4,1.5 c0.3,0.6,0.5,1.4,0.5,2.3C11.9,8.9,11.7,9.7,11.4,10.3z"/></svg>`,
  "contractor-chip": `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M14,0H2C0.9,0,0,0.9,0,2v12c0,1.1,0.9,2,2,2h12c1.1,0,2-0.9,2-2V2C16,0.9,15.1,0,14,0z M6.4,9.5c0.2,0.4,0.4,0.7,0.7,0.9 c0.3,0.2,0.7,0.3,1.1,0.3c0.2,0,0.4,0,0.6-0.1c0.2-0.1,0.4-0.1,0.5-0.3c0.2-0.1,0.3-0.3,0.4-0.4C9.8,9.8,9.9,9.6,10,9.4l0-0.2h1.8 l-0.1,0.3c-0.1,0.4-0.2,0.8-0.4,1.2c-0.2,0.4-0.5,0.7-0.8,0.9c-0.3,0.3-0.7,0.5-1.1,0.6c-0.4,0.1-0.8,0.2-1.3,0.2 c-0.7,0-1.4-0.2-2-0.5c-0.6-0.4-1.1-0.9-1.4-1.5C4.4,9.7,4.3,8.9,4.3,8c0-0.9,0.2-1.7,0.5-2.3c0.3-0.7,0.8-1.2,1.4-1.5 c0.6-0.4,1.3-0.5,2-0.5c0.5,0,0.9,0.1,1.3,0.2c0.4,0.1,0.8,0.3,1.1,0.6c0.3,0.3,0.6,0.6,0.8,0.9c0.2,0.4,0.3,0.8,0.4,1.2L11.8,7H10 l0-0.2c0-0.2-0.1-0.4-0.2-0.6C9.6,6,9.5,5.8,9.3,5.7C9.2,5.6,9,5.5,8.8,5.4C8.6,5.4,8.4,5.4,8.2,5.4c-0.4,0-0.8,0.1-1.1,0.3 S6.5,6.2,6.4,6.5C6.2,6.9,6.1,7.5,6.1,8C6.1,8.6,6.2,9.1,6.4,9.5z"/></svg>`,
};

// Returns a suit icon as markup with the given CSS class applied - "inline-icon"
// for buttons/chips, "inline-icon-cards" for on-card suit corners (see
// styling.css). Suits without an icon (Misère/No Trumps) return "".
function suitIconSvg(suit, cls) {
  var markup = SUIT_ICON_SVG[suit];
  if (!markup) return "";
  return markup.replace("<svg ", '<svg class="' + cls + '" ');
}

// Swaps every "PREFIX|" occurrence in text for its PREFIX_ICON_SVG markup -
// one pass over the lookup instead of one .replace() per prefix, so a new
// bot-style prefix icon only ever needs a new PREFIX_ICON_SVG entry above.
function replacePrefixIcons(text) {
  var out = text;
  for (var prefix in PREFIX_ICON_SVG) {
    out = out.split(prefix).join(PREFIX_ICON_SVG[prefix]);
  }
  return out;
}

// jQuery plugins below - guarded so plain-vanilla-JS pages (choose_table.j2.html,
// which deliberately carries no jQuery) can still load this file for the
// lookups/functions above without $ being defined.
if (typeof $ !== "undefined" && $.fn) {
  // clones a named APPEND_ICON_SVG icon, stamps an id/extra class onto its
  // <svg> root, and appends it to the matched elements.
  $.fn.appendSvg = function (iconName, newId, extraClass) {
    $(APPEND_ICON_SVG[iconName])
      .attr("id", newId)
      .addClass(extraClass)
      .appendTo(this);
    return this;
  };

  // fills every matched element's data-suit="N" (1-4) with its suit icon at
  // the given CSS class - used to inject the bid/joker-nomination suit
  // buttons' icons at page load instead of baking the SVG markup into the
  // template (see game_client.j2.html .bid-suit-btn / .joker-suit-btn).
  $.fn.fillSuitIcons = function (cls) {
    return this.each(function () {
      var icon = suitIconSvg($(this).data("suit"), cls);
      if (icon) $(this).prepend(icon);
    });
  };
}
