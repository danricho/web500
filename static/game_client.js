// ---------------------------------------------------------------------------
// CLIENT CONFIG — tweakable values live here
// ---------------------------------------------------------------------------
var CONFIG = {
  STORAGE_KEY_PERFECT_CARDS: "web500_perfect_cards",
  // card jitter ("Natural" layout): max random offset (px) / rotation (deg)
  // per card area; hand/kitty fans rotate about a far origin (50% 300%) so
  // keep their rotation small
  JITTER: {
    "#table-kitty card": { x: 5, y: 5, rot: 90 },
    ".p-hand card": { x: 0, y: 3, rot: 0.4 },
    ".p-kitty card": { x: 0, y: 3, rot: 0.4 },
    "#played-cards card": { x: 4, y: 4, rot: 25 },
  },
};

// SUIT_STR/SUIT_DISP/SUIT_COL cover all 6 suit slots (0=Misère..5=No Trumps,
// see CLAUDE.md "Card model") for text/colour lookups; the SVG icon itself
// only exists for suits 1-4 (Misère/No Trumps render as text) - see
// SUIT_ICON_SVG / suitIconSvg() in svg.js.
SUIT_STR = ["Misère", "Spades", "Clubs", "Diamonds", "Hearts", "No Trumps"];
SUIT_DISP = [null, "♠︎", "♣︎", "♦︎", "♥︎", "No Trumps"];
SUIT_COL = [null, "suit-black", "suit-black", "suit-red", "suit-red", ""];
/// RANK index ranges from 3 to 15.
RANK_STR = [
  null,
  null,
  null,
  "3",
  "4",
  "5",
  "6",
  "7",
  "8",
  "9",
  "10",
  "Jack",
  "Queen",
  "King",
  "Ace",
  "Joker",
];
RANK_DISP = [
  null,
  null,
  null,
  "3",
  "4",
  "5",
  "6",
  "7",
  "8",
  "9",
  "10",
  "J",
  "Q",
  "K",
  "A",
  "Jok",
];

// IDENTITY COMES FROM THE SERVER SESSION (SET AT LOGIN), INJECTED BY THE TEMPLATE.
// ADMIN STATUS IS SERVER-COMPUTED FROM auth.json admin_users (SINGLE SOURCE OF TRUTH) -
// THIS FLAG ONLY CONTROLS UI VISIBILITY; THE /admin/* ENDPOINTS ENFORCE FOR REAL.
var username = typeof SESSION_USERNAME !== "undefined" ? SESSION_USERNAME : "";
var isAdminUser = typeof SESSION_IS_ADMIN !== "undefined" ? SESSION_IS_ADMIN : false;
// PINNED PER PAGE-LOAD (NOT RE-READ FROM THE LIVE SESSION) - SEE THE io() CALL BELOW
var tableId = typeof TABLE_ID !== "undefined" ? TABLE_ID : "";

// SET FROM /admin/uptime EACH TIME AN ADMIN OPENS THE SETTINGS MODAL - THE SERVER'S RESTART_COMMAND
// CAN BE None, IN WHICH CASE THE RESTART BUTTON HAS NOTHING TO DO
var restartEnabled = false;

// ADMIN TABLE SELECTOR VALUE, APPENDED TO EVERY ADMIN-SCOPED /admin/* CALL SO AN ADMIN CAN
// ACT ON ANY TABLE VIA #admin-table-select, NOT JUST THEIR OWN (THE SERVER FALLS BACK
// TO THE ADMIN'S OWN SESSION TABLE IF THIS IS EVER MISSING/EMPTY). NOTE THIS ONLY
// SCOPES THE ADMIN ACTION ITSELF - IT DOESN'T MOVE THIS BROWSER'S OWN LIVE VIEW
function adminTableQuery() {
  var t = $("#admin-table-select").val();
  return t ? "?table=" + encodeURIComponent(t) : "";
}
// adminTablesData: name -> {test_mode, skip_delays, ...} FROM THE LAST /api/tables FETCH
// (SEE refreshAdminTableSelect). lastPushData: THE MOST RECENT game_state PUSH - ONLY
// EVER FOR *THIS BROWSER'S OWN* TABLE (tableId). TOGETHER THEY LET
// updateAdminModeLabels() SHOW THE RIGHT TEST MODE / SKIP DELAYS STATE FOR WHICHEVER
// TABLE IS CURRENTLY PICKED IN #admin-table-select, NOT JUST THIS BROWSER'S OWN.
var adminTablesData = {};
var lastPushData = null;
function updateAdminModeLabels() {
  var selected = $("#admin-table-select").val();
  var info =
    selected === tableId && lastPushData
      ? {
          test_mode: lastPushData.test_mode,
          skip_delays: lastPushData.skip_delays,
        }
      : adminTablesData[selected];
  if (!info) return; // nothing known yet for that table - leave the label as-is
  $("#toggle-test").text("TEST MODE: " + (info.test_mode ? "ON" : "OFF"));
  $("#toggle-skip-delays").text(
    "SKIP DELAYS: " + (info.skip_delays ? "ON" : "OFF"),
  );
}
// RE-FETCHES /api/tables, REBUILDS #admin-table-select AND adminTablesData, THEN CALLS
// updateAdminModeLabels(). CALLED (a) EVERY TIME THE SETTINGS MODAL OPENS - SAME
// "RE-ASK ON OPEN" PATTERN AS startUptime(), CATCHES A TABLE CREATED/DELETED SINCE
// PAGE LOAD (THE OPTION LIST IS OTHERWISE ONLY EVER SERVER-RENDERED ONCE) - AND
// (b) RIGHT AFTER THE admin/test AND admin/skipdelays TOGGLE BUTTONS' AJAX CALLS COMPLETE
// (SEE THEIR onclick HANDLERS ABOVE), SINCE TOGGLING A TABLE OTHER THAN THIS
// BROWSER'S OWN NEVER ARRIVES VIA THE LIVE game_state PUSH - WITHOUT THIS THE TEST
// MODE / SKIP DELAYS LABELS WOULD SHOW STALE STATE FOR THAT TABLE UNTIL THE MODAL
// WAS CLOSED AND REOPENED. PRESERVES THE CURRENT SELECTION IF IT STILL EXISTS, ELSE
// FALLS BACK TO THIS BROWSER'S OWN TABLE.
function refreshAdminTableSelect() {
  if (!isAdminUser) return;
  $.getJSON("/api/tables").done(function (list) {
    var current = $("#admin-table-select").val();
    adminTablesData = {};
    var html = "";
    list.forEach(function (t) {
      adminTablesData[t.name] = t;
      html +=
        '<option value="' +
        escapeHtml(t.name) +
        '">Controlling ' +
        escapeHtml(t.name) +
        "</option>";
    });
    $("#admin-table-select").html(html);
    var stillExists = list.some(function (t) {
      return t.name === current;
    });
    $("#admin-table-select").val(stillExists ? current : tableId);
    updateAdminModeLabels();
  });
}
// NAMES ARE COMPARED CASE-INSENSITIVELY (SERVER DOES THE SAME VIA same_name())
function sameName(a, b) {
  return (
    a != null &&
    b != null &&
    String(a).toLowerCase() === String(b).toLowerCase()
  );
}

// BOT NAMES ARRIVE FROM THE SERVER AS "B|Name" / DEV TEST-MODE BOTS AS "D|NAME"
// (RENDERING CONCERN ONLY - THE SERVER KEEPS THE PREFIX). ANYWHERE THE CLIENT
// SHOWS ONE, THE PREFIX RENDERS AS AN ICON INSTEAD - SEE PREFIX_ICON_SVG /
// replacePrefixIcons() IN svg.js. TEXT IS HTML-ESCAPED FIRST BECAUSE THE CALL
// SITES USE .html().
function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
function displayName(text) {
  return replacePrefixIcons(escapeHtml(text));
}
var forceScoreboardDisplay = false;
var lastGameDialog = null; // raw server dialog string (compared before the B| icon substitution)
var playedCardsTableCount = 0; // tracks table card count so jitter re-rolls on trick sweep
var myHandFanSlots = null; // my hand's rendered slot layout during PLAY HAND: card objects with nulls where played cards left gaps (null = no gap layout active)
var perfectCards =
  localStorage.getItem(CONFIG.STORAGE_KEY_PERFECT_CARDS) === "true"; // "Perfect" = no card jitter; default "Natural"

// LAYS OUT MY HAND FOR RENDERING SO PLAYED CARDS LEAVE GAPS IN THE FAN INSTEAD OF
// THE REMAINING CARDS SHUFFLING LEFT (updateArrayCards TREATS null AS "HIDE BUT
// KEEP THE SLOT", AND :nth-child FAN ANGLES COUNT HIDDEN CARDS). GAPS ONLY DURING
// PLAY HAND - THE DEAL, KITTY PICKUP/DISCARD AND THE TRUMP-LOCK RE-SORT ALL
// LEGITIMATELY RESHAPE THE HAND. THE SERVER HAND STAYS COMPACTED; ITS ORDER IS
// PRESERVED WHEN CARDS ARE PLAYED, SO THE NON-GAP SLOTS IN ORDER MUST EQUAL THE
// SERVER HAND - ANY MISMATCH (RECONNECT/RESTORE MID-HAND, A FRESH HAND) RESETS
// TO THE FLAT LAYOUT.
function myHandFanLayout(handCards) {
  if (gameState != "PLAY HAND") {
    myHandFanSlots = null;
    return handCards;
  }
  var cardKey = function (c) {
    return c.suit + ":" + c.rank;
  };
  var inHand = {};
  handCards.forEach(function (c) {
    inHand[cardKey(c)] = true;
  });
  var slots = null;
  if (myHandFanSlots !== null) {
    slots = myHandFanSlots.map(function (c) {
      return c !== null && inHand[cardKey(c)] ? c : null;
    });
    var kept = slots.filter(function (c) {
      return c !== null;
    });
    if (
      kept.length !== handCards.length ||
      kept.some(function (c, i) {
        return cardKey(c) !== cardKey(handCards[i]);
      })
    ) {
      slots = null;
    }
  }
  if (slots === null) {
    slots = handCards.slice();
  }
  myHandFanSlots = slots;
  return slots;
}
// MAPS A RENDERED SLOT INDEX IN MY HAND TO THE SERVER'S COMPACTED HAND INDEX
// (THE GAPS IN myHandFanSlots DON'T EXIST SERVER-SIDE). IDENTITY WHEN NO GAP
// LAYOUT IS ACTIVE. EVERY legal_plays LOOKUP AND play_card EMIT MUST GO
// THROUGH THIS.
function myHandServerIndex(slotIdx) {
  if (myHandFanSlots === null) {
    return slotIdx;
  }
  var serverIdx = 0;
  for (var i = 0; i < slotIdx && i < myHandFanSlots.length; i++) {
    if (myHandFanSlots[i] !== null) {
      serverIdx++;
    }
  }
  return serverIdx;
}

if (username != "") {
  $(document).prop("title", username + " : Web500");
}

highestBid = [-1, -1];

gameState = "";

$.fn.reverse = [].reverse;
$.fn.visible = function () {
  return this.css("visibility", "visible");
};
$.fn.invisible = function () {
  return this.css("visibility", "hidden");
};
$.fn.attentionGrab = function () {
  // THE FLASHING IS CSS-DRIVEN (attention-flash KEYFRAMES ON .focus),
  // REPEATING EVERY 5s FOR AS LONG AS THE CLASS STAYS ON
  return $(this).addClass("focus");
};
// RENDERS AN ARRAY OF CARD OBJECTS INTO A CONTAINER'S PRE-CLONED <card> ELEMENTS.
// THE ELEMENTS ARE FIXED IN NUMBER (SEE createCards) SO SURPLUS ONES ARE HIDDEN
// RATHER THAN DESTROYED - A null ENTRY MEANS "SEAT PLAYED NOTHING YET".
$.fn.updateArrayCards = function (cards) {
  this.find("card")
    .removeClass("suit-red")
    .removeClass("suit-black")
    .find("p")
    .text("");
  this.find("card").each(function (index, value) {
    if (index < cards.length) {
      card = cards[index];
      if (card === null) {
        $(value).hide();
        return;
      } // continue (finish this loop)
      // THE JOKER-VS-NORMAL FACE IS DRIVEN PURELY BY AN is-joker CLASS ON THE CARD
      // (RULES IN cards.css), NEVER BY jQuery .show()/.hide() ON .joker/.index/.big:
      // ON iOS SAFARI A .show() FIRED WHILE THE CARD ELEMENT IS STILL HIDDEN CAN
      // RESOLVE THE WRONG DEFAULT DISPLAY AND PIN AN INLINE "display: block" ON THE
      // GRID-STACKED .joker DIV - ITS CHILDREN THEN STACK VERTICALLY (JOKER TEXT
      // CENTRED, LOGO OFF THE CARD BOTTOM) UNTIL A REFRESH. CLEARING ANY INLINE
      // DISPLAY ALSO HEALS ELEMENTS STUCK FROM BEFORE THIS SCHEME.
      $(value).find(".joker, .index, .big").css("display", "");
      $(value).toggleClass("is-joker", card.rank == 15);
      // "10" IS THE ONLY TWO-CHARACTER RANK - cards.css TIGHTENS/SQUEEZES IT
      $(value).toggleClass("rank-10", card.rank == 10);
      if (card.rank != 15) {
        $(value).addClass(SUIT_COL[card.suit]);
        $(value)
          .find("p.suit")
          .html(suitIconSvg(card.suit, "inline-icon-cards"));
        $(value).find("p.rank").text(RANK_DISP[card.rank]);
      }
      $(value).show();
    } else {
      $(value).hide();
    }
  });
};
$.currentTimeStamp = function () {
  var time = new Date();
  return time.toLocaleTimeString();
};

// TWO-STEP BUTTON CONFIRMATION: FIRST CLICK ARMS THE BUTTON (RED + "Confirm?") FOR
// 5 SECONDS; A SECOND CLICK WHILE ARMED RUNS action; TIMEOUT QUIETLY RESTORES IT.
function confirmThen(el, action) {
  var $btn = $(el);
  $btn.blur();
  if ($btn.data("armed")) {
    clearTimeout($btn.data("armTimer"));
    $btn
      .data("armed", false)
      .removeClass("button-danger")
      .css("background-color", "")
      .text($btn.data("origText"));
    action();
    return;
  }
  $btn.data("armed", true);
  $btn.data("origText", $btn.text());
  // INLINE STYLE, NOT JUST THE CLASS: ON iOS THE TAP LEAVES A STICKY :hover THAT
  // WOULD OVERRIDE .button-danger AND TURN THE ARMED BUTTON DARK
  $btn
    .addClass("button-danger")
    .css("background-color", "var(--danger-red)")
    .text("Confirm?");
  $btn.data(
    "armTimer",
    setTimeout(function () {
      $btn
        .data("armed", false)
        .removeClass("button-danger")
        .css("background-color", "")
        .text($btn.data("origText"));
    }, 3000),
  );
}

function applyCardJitter(selector, maxShiftX, maxShiftY, maxRotate) {
  // sets per-card CSS vars consumed by the transform rules in cards.css;
  // "Perfect" layout setting zeroes everything
  $(selector).each(function () {
    var jx = perfectCards ? 0 : (Math.random() * 2 - 1) * maxShiftX;
    var jy = perfectCards ? 0 : (Math.random() * 2 - 1) * maxShiftY;
    var jr = perfectCards ? 0 : (Math.random() * 2 - 1) * maxRotate;
    this.style.setProperty("--jitter-x", jx.toFixed(1) + "px");
    this.style.setProperty("--jitter-y", jy.toFixed(1) + "px");
    this.style.setProperty("--jitter-r", jr.toFixed(1) + "deg");
  });
}
function rollAllCardJitter() {
  $.each(CONFIG.JITTER, function (selector, j) {
    applyCardJitter(selector, j.x, j.y, j.rot);
  });
}
// CLONES THE PROTOTYPE CARD INTO EVERY HAND / KITTY / TABLE SLOT ONCE AT STARTUP.
// EVERY LATER RENDER JUST SHOWS, HIDES AND REWRITES THESE - NO CARDS ARE EVER CREATED
// OR DESTROYED MID-GAME. data-face DECIDES WHETHER A SLOT SHOWS FRONTS OR BACKS.
function createCards() {
  $(".p-kitty").each(function () {
    if ($(this).data("face") == "front") {
      for (var i = 0; i < 3; i++) {
        $("#proto-card card").clone().appendTo(this).find("face.back").hide();
      }
    } else if ($(this).data("face") == "back") {
      for (var i = 0; i < 3; i++) {
        $("#proto-card card").clone().appendTo(this).find("face.front").hide();
      }
    }
  });
  $(".p-hand").each(function () {
    if ($(this).data("face") == "front") {
      for (var i = 0; i < 10; i++) {
        $("#proto-card card").clone().appendTo(this).find("face.back").hide();
      }
    } else if ($(this).data("face") == "back") {
      for (var i = 0; i < 10; i++) {
        $("#proto-card card").clone().appendTo(this).find("face.front").hide();
      }
    }
  });
  $("#table-kitty").each(function () {
    if ($(this).data("face") == "back") {
      for (var i = 0; i < 3; i++) {
        $("#proto-card card").clone().appendTo(this).find("face.front").hide();
      }
    }
  });
  $("#proto-card card")
    .clone()
    .attr("id", "p-me-played")
    .appendTo($("#played-cards"))
    .find("face.back")
    .hide();
  $("#proto-card card")
    .clone()
    .attr("id", "p-next-played")
    .appendTo($("#played-cards"))
    .find("face.back")
    .hide();
  $("#proto-card card")
    .clone()
    .attr("id", "p-partner-played")
    .appendTo($("#played-cards"))
    .find("face.back")
    .hide();
  $("#proto-card card")
    .clone()
    .attr("id", "p-previous-played")
    .appendTo($("#played-cards"))
    .find("face.back")
    .hide();
  rollAllCardJitter();
}
function scrollScoresToBottom() {
  var el = $(".score-table-scroll")[0];
  if (el) {
    el.scrollTop = el.scrollHeight;
  }
}
// SHOWS/HIDES EVERY PANEL FOR ONE (connected, state, focus, seated) COMBINATION.
// CALLED ON EVERY PUSH AND ON CONNECT/DISCONNECT, SO IT MUST SET BOTH SIDES OF EVERY
// TOGGLE - NEVER ASSUME A PANEL'S PREVIOUS VISIBILITY.
function updateComponentVisibility(connected, gameState, meFocus, amSeated) {
  $("#disconnected-icon").toggle(!connected);
  $("#disconnected-modal").toggle(!connected);
  $(".reinit").toggle(connected);
  $("#admin-reinit").toggle(connected);
  $("#admin-delete-table").toggle(connected);
  // WAITING FOR PLAYERS: api/change_table CAN PROPERLY VACATE A SEAT THERE, RATHER
  // THAN LEAVING IT ORPHANED. IN ANY OTHER STATE THE BUTTON STILL SHOWS FOR AN
  // UNSEATED VISITOR (NOTHING TO VACATE) - OTHERWISE SOMEONE WHO NEVER SAT (OR CAME
  // BACK LATER TO A TABLE THAT MOVED ON WITHOUT THEM) WOULD BE STRANDED HERE WITH NO
  // WAY BACK TO THE TABLE PICKER.
  $(".change-table").toggle(
    connected && (gameState == "WAITING FOR PLAYERS" || !amSeated),
  );
  $("#toggle-test").toggle(connected);
  $("#toggle-skip-delays").toggle(connected);
  $("#save-checkpoint").toggle(connected);
  $("#load-checkpoint").toggle(connected);
  $("#clear-checkpoint").toggle(connected);
  $("#restart-service").toggle(connected && restartEnabled);

  $("#lobby-modal").toggle(
    connected &&
      (gameState == "WAITING FOR PLAYERS" || username == "" || !amSeated),
  );
  $("#choose-seat-div").toggle(username != "");

  $(".game-layer").toggle(
    connected &&
      gameState != "WAITING FOR PLAYERS" &&
      username != "" &&
      amSeated,
  );
  $("#bidding-box").toggle(gameState == "TAKING BIDS" && meFocus);

  $(".tricks-display").toggle(["AWARD KITTY", "PLAY HAND"].includes(gameState));

  var scoresWasVisible = $("#scores-modal").is(":visible");
  $("#scores-modal").toggle(
    connected &&
      gameState != "WAITING FOR PLAYERS" &&
      (forceScoreboardDisplay ||
        (gameState == "AWARD POINTS" && username != "" && amSeated)),
  );
  if (!scoresWasVisible && $("#scores-modal").is(":visible")) {
    scrollScoresToBottom();
  }
  $(".unforce-scores").toggle(gameState != "AWARD POINTS");

  if (!connected || gameState != "PLAY HAND") {
    $("#joker-pane").hide(); // RE-SHOWN FROM STATE DATA WHEN A JOKER LEAD IS IN PLAY
  }
  if (!connected || !["AWARD KITTY", "PLAY HAND"].includes(gameState)) {
    $("#trump-indicator").hide(); // RE-SHOWN FROM STATE DATA ONCE A CONTRACT IS LOCKED
  }

  $(".force-scores").toggle(connected && gameState != "WAITING FOR PLAYERS");
}
// GREYS OUT EVERY BID THE CURRENT HIGH BID RULES OUT, AND KEEPS THE SUIT/TRICKS/PASS
// BUTTONS CONSISTENT WITH EACH OTHER. THIS IS CONVENIENCE ONLY - gui_bid() ON THE
// SERVER RE-CHECKS EVERY BID AND IS THE REAL AUTHORITY.
function updateBiddingBoxButtons() {
  //                 1         2         3         4          5         0
  // TRICKS       SPADES    CLUBS    DIAMONDS   HEARTS   NO TRUMPS   MISERE
  // Six            40        60        80       100        120
  // Seven         140       160       180       200        220
  // Misere                                                           250
  // Eight         240       260       280       300        320
  // Nine          340       360       380       400        420
  // Ten           440       460       480
  // Ten                                         500        520

  // update the highest bid display
  if (highestBid[0] < 0) {
    $(".bid-to-beat-title").text("No bids yet.");
    $(".bid-to-beat").text("").removeClass("suit-red suit-black");
  } else {
    $(".bid-to-beat-title").text("Current High Bid: ");
    $(".bid-to-beat")
      .text(
        highestBid[1] === 0
          ? "Misère"
          : highestBid[0] + " " + SUIT_DISP[highestBid[1]],
      )
      .removeClass("suit-red suit-black")
      .addClass(SUIT_COL[highestBid[1]] || "");
  }

  // disable trick buttons if no suit selected yet
  $("#bidding-box .bid-tricks button").prop(
    "disabled",
    $("#bidding-box .bid-suit button.active").length == 0,
  );
  if ($("#bidding-box .bid-suit button.active").length == 0) {
    $("#bidding-box .bid-tricks button").removeClass("active");
  }

  // disable pass button if a suit is selected
  $('#bidding-box button[data-tricks="pass"]').prop(
    "disabled",
    $("#bidding-box .bid-suit button.active").length == 1,
  );

  // disable other suits if suit is selected - except the selected one
  $("#bidding-box .bid-suit button").prop(
    "disabled",
    $("#bidding-box .bid-suit button.active").length == 1,
  );
  $("#bidding-box .bid-suit button.active").prop("disabled", false);

  // disable other tricks if trick is selected - except the selected one
  $("#bidding-box .bid-tricks button").prop(
    "disabled",
    $("#bidding-box .bid-tricks button.active").length == 1,
  );
  $("#bidding-box .bid-tricks button.active").prop("disabled", false);

  // misère may only be bid when the current high bid is a seven
  // (it ranks above any 7 and below any 8, so even 8 spades shuts it out)
  if (highestBid[0] != 7 || highestBid[1] === 0) {
    $('#bidding-box button[data-suit="0"]').prop("disabled", true);
  }

  // if misere selected, only 10 trick button is possible - and it is auto-selected
  // (deselecting misere clears it via the no-suit-selected reset above)
  if ($('#bidding-box button[data-suit="0"].active').length) {
    $("#bidding-box .bid-tricks button").prop("disabled", true);
    $('#bidding-box .bid-tricks button[data-tricks="10"]')
      .prop("disabled", false)
      .addClass("active");
  }

  // when up to 10, only the higher suits should be available
  if (highestBid[0] == 10) {
    $("#bidding-box .bid-suit button").each(function () {
      if ($(this).data("suit") <= highestBid[1]) {
        $(this).prop("disabled", true);
      }
    });
  }

  // if bigger suit selected allow same tricks, else only allow bigger tricks
  if ($("#bidding-box .bid-suit button.active").length) {
    if (highestBid[1] === 0) {
      // misère is the high bid: 8 clubs or higher beats it (250 points sits between
      // 8 spades at 240 and 8 clubs at 260)
      var selSuit = $("#bidding-box .bid-suit button.active").data("suit");
      var minTricks = selSuit === 1 ? 9 : 8; // spades must go to 9 to outrank 250
      $("#bidding-box .bid-tricks button").each(function () {
        if ($(this).data("tricks") < minTricks) {
          $(this).prop("disabled", true);
        }
      });
    } else {
      $("#bidding-box .bid-tricks button").each(function () {
        if (
          $("#bidding-box .bid-suit button.active").data("suit") > highestBid[1]
        ) {
          if ($(this).data("tricks") < highestBid[0]) {
            $(this).prop("disabled", true);
          }
        }
        if (
          $("#bidding-box .bid-suit button.active").data("suit") <=
          highestBid[1]
        ) {
          if ($(this).data("tricks") <= highestBid[0]) {
            $(this).prop("disabled", true);
          }
        }
      });
    }
  }

  // if pass selected, disable all suits and tricks
  if ($("#bidding-box .bid-pass-btn.active").length) {
    $("#bidding-box .bid-suit button")
      .prop("disabled", true)
      .removeClass("active");
    $("#bidding-box .bid-tricks button")
      .prop("disabled", true)
      .removeClass("active");
  }

  // can only submit if a suit and trick button are selected
  $("#bidding-box .bid-submit-btn").prop("disabled", function () {
    if ($('#bidding-box button[data-tricks="pass"]').hasClass("active")) {
      return false;
    }
    if (
      $("#bidding-box .bid-suit button.active").length == 1 &&
      $("#bidding-box .bid-tricks button.active").length == 1
    ) {
      return false;
    }
    return true;
  });

  // FLASH THE OK BUTTON (LIKE THE FOCUS ANIMATION) WHILE A COMPLETE BID OR
  // PASS IS SITTING THERE WAITING TO BE SUBMITTED
  $("#bidding-box .bid-submit-btn").toggleClass(
    "attention",
    !$("#bidding-box .bid-submit-btn").prop("disabled"),
  );
}

// THE WHOLE CLIENT: EVERY PUSH CARRIES THE COMPLETE GAME STATE AND THIS REDRAWS FROM
// IT. NO GAME LOGIC HERE - THE SERVER DECIDES, THIS ONLY RENDERS WHAT IT IS TOLD.
function processGameStateData(data) {
  $("#sync-icon").show().fadeOut(1000);
  $("#chosen-username").text(username);
  if (username != "") {
    $(document).prop("title", username + " | Web500");
  } else {
    $(document).prop("title", "Web500");
  }
  var haveAUsername = username != "";

  var prevGameState = gameState;
  gameState = data.states[data.state]; // update global game state

  // re-roll all card jitter once per deal (entering DEALING), never mid-hand
  if (gameState == "DEALING" && prevGameState != "DEALING") {
    rollAllCardJitter();
  }
  console.log(
    "GAME MACHINE LOG | " +
      $.currentTimeStamp() +
      " | " +
      gameState +
      " | " +
      data.game_dialog,
  );

  var newDialogTimer;
  // dialogs render via .html() (robot icon for bot names), so the "changed?" check
  // compares the raw server string, not the rendered text
  if (lastGameDialog !== data.game_dialog) {
    clearTimeout(newDialogTimer);
    $(".game-dialog").addClass("dialog-new");
    newDialogTimer = setTimeout(function () {
      $(".game-dialog").removeClass("dialog-new");
    }, 100);
  }
  lastGameDialog = data.game_dialog;
  $(".game-dialog").html(displayName(data.game_dialog));

  // determine my seat
  idxMe = -1;
  $.each(data.players, function (index, value) {
    if (sameName(value.name, username)) {
      idxMe = index;
    }
  });
  var amSeated = idxMe != -1;

  // update component/panel visibilities
  updateComponentVisibility(
    true,
    gameState,
    idxMe == data.player_focus,
    amSeated,
  );

  // MISÈRE WATERMARK ON THE TABLE WHILE A MISÈRE CONTRACT IS BEING PLAYED
  $("#misere-mode").toggle(
    data.trumps === 0 && ["AWARD KITTY", "PLAY HAND"].includes(gameState),
  );

  // JOKER NOMINATION PANE (TOP RIGHT) - THREE USES (MUTUALLY EXCLUSIVE, SERVER-ENFORCED):
  // 1. CONTRACTOR'S PRE-LEAD NOMINATION OFFER - SHOWN ONLY TO THE CONTRACTOR (SHOWING
  //    IT TO OTHERS WOULD REVEAL THEY HOLD THE JOKER)
  // 2. A DECLARED PRE-NOMINATION - SHOWN TO EVERYONE FOR THE REST OF THE HAND
  // 3. LEAD-TIME NOMINATION (JOKER LED) - SUIT PICKER, THEN THE SUIT FOR THE TRICK
  var jokerNominated =
    data.joker_nomination !== null && data.joker_nomination !== undefined;
  var jokerPrenominated =
    data.joker_prenom !== null && data.joker_prenom !== undefined;
  var prenomOffer = data.joker_prenom_open && data.player_focus == idxMe;
  var showJokerPane =
    gameState == "PLAY HAND" &&
    amSeated &&
    (data.joker_nominating ||
      jokerNominated ||
      jokerPrenominated ||
      prenomOffer);
  $("#joker-pane").toggle(showJokerPane);
  if (showJokerPane) {
    var iAmNominator =
      (data.joker_nominating && data.player_focus == idxMe) || prenomOffer;
    $("#joker-nominate-btns").toggle(iAmNominator);
    $("#joker-nominated").toggle(jokerNominated || jokerPrenominated);
    if (prenomOffer) {
      $(".joker-pane-title").text("Declare Joker suit or just lead a card.");
    } else if (data.joker_nominating) {
      // .html() + displayName so bot names render the robot icon
      $(".joker-pane-title").html(
        iAmNominator
          ? "Nominate a suit for your Joker"
          : displayName(data.players[data.player_focus].name) +
              " nominating Joker's suit..",
      );
    } else if (jokerPrenominated) {
      $(".joker-pane-title").text("Joker is the highest of:");
      $(".joker-nominated-suit")
        .html(SUIT_DISP[data.joker_prenom] + " " + SUIT_STR[data.joker_prenom])
        .removeClass("suit-red suit-black")
        .addClass(SUIT_COL[data.joker_prenom] || "");
    } else {
      $(".joker-pane-title").text("Joker led - suit to follow:");
      $(".joker-nominated-suit")
        .html(
          SUIT_DISP[data.joker_nomination] +
            " " +
            SUIT_STR[data.joker_nomination],
        )
        .removeClass("suit-red suit-black")
        .addClass(SUIT_COL[data.joker_nomination] || "");
    }
    // LEAD-TIME NOMINATION ONLY: THE SUIT MUST NOT HAVE BEEN LED EARLIER THIS HAND.
    // A PRE-NOMINATION IS UNRESTRICTED (NOTHING HAS BEEN LED YET ANYWAY)
    $(".joker-suit-btn").each(function () {
      $(this).prop(
        "disabled",
        !prenomOffer && (data.suits_led || []).includes($(this).data("suit")),
      );
    });
  }

  // TRUMP SUIT INDICATOR (TOP RIGHT) - REMINDER OF THE CONTRACT'S TRUMPS.
  // HIDDEN UNTIL THE CONTRACT LOCKS AND WHENEVER THE JOKER PANE NEEDS THE CORNER
  var showTrumpIndicator =
    ["AWARD KITTY", "PLAY HAND"].includes(gameState) &&
    data.trumps !== null &&
    data.trumps !== undefined &&
    !showJokerPane;
  $("#trump-indicator").toggle(showTrumpIndicator);
  if (showTrumpIndicator) {
    $("#trump-indicator button")
      .html(
        data.trumps === 0
          ? "Misère"
          : data.trumps === 5
            ? "<span class='bold'>N.T.</span>"
            : suitIconSvg(data.trumps, "inline-icon-cards"),
      )
      .removeClass("suit-red suit-black")
      .addClass(SUIT_COL[data.trumps] || "");
  }

  // ADMIN: show mode states as text rather than ambiguous red/green colouring.
  // lastPushData/updateAdminModeLabels ALSO HANDLE #admin-table-select POINTING AT A
  // TABLE OTHER THAN THIS BROWSER'S OWN (data HERE IS ALWAYS THIS TABLE'S PUSH ONLY)
  lastPushData = data;
  updateAdminModeLabels();

  // label and update seat selection buttons. Scoped to .seat-select: the div also
  // holds the ADD BOTS button, and indexing order2seati past 3 would crash the render
  $("#choose-seat-div button.seat-select").each(function (index, value) {
    order2seati = [0, 2, 1, 3];
    if (data.players[order2seati[index]] !== null) {
      if (data.players[order2seati[index]].name !== null) {
        $(value)
          .removeClass("button-success")
          .prop("disabled", true)
          // .html() + displayName so bot names render the robot icon
          .html(displayName(data.players[order2seati[index]].name));
        return;
      }
    }
    $(value)
      .prop("disabled", false)
      .addClass("button-success")
      .text("Seat " + (order2seati[index] + 1));
    return;
  });
  // disable all seat selection buttons once seated.
  if (amSeated) {
    $("#choose-seat-div button.seat-select").prop("disabled", true);
  }
  // ADD BOTS only makes sense once I'm seated (server refuses otherwise - an unseated
  // requester would lock themselves out of a bot-filled table) and a seat is vacant.
  var vacantSeats = data.players.some(function (p) {
    return p === null || p.name === null; // seat entries can be null pre-init (see loop above)
  });
  $("#add-bots")
    .toggle(BOTS_ENABLED && amSeated && vacantSeats)
    .prop("disabled", false); // re-arm the click handler's own debounce disable

  // if data is null o undefined stuff may break - progress with full seats, while seated and named.
  var gameStarted = !["WAITING FOR PLAYERS"].includes(gameState);
  if (gameStarted && haveAUsername && amSeated) {
    idxNext = (idxMe + 1) % 4;
    idxPartner = (idxMe + 2) % 4;
    idxPrevious = (idxMe + 3) % 4;

    // MAP PLAYERS TO ELEMENTS
    data.players[idxMe].seat_element_id = "#p-me";
    data.players[idxNext].seat_element_id = "#p-next";
    data.players[idxPartner].seat_element_id = "#p-partner";
    data.players[idxPrevious].seat_element_id = "#p-previous";

    // MISÈRE: DARKEN THE SAT-OUT PARTNER'S CARDS
    $(".player").removeClass("sitting-out");
    if (data.sitting_out !== null && data.sitting_out !== undefined) {
      $(data.players[data.sitting_out].seat_element_id).addClass("sitting-out");
    }

    // UPDATE THE PLAYER SEATS
    $.each(data.players, function (index, playerData) {
      seat = playerData.seat_element_id;

      // NAME LABEL
      if (playerData.name === null) {
        $(seat + " span.name").html("[EMPTY]");
      } else {
        // .html() + displayName so bot names render the robot icon
        $(seat + " span.name").html(displayName(playerData.name));
      }
      // DEALER CHIP
      if (index == data.dealer) {
        $(seat + " span.dealer-chip").show();
      } else {
        $(seat + " span.dealer-chip").hide();
      }

      // HIGHLIGHT FOCUS PLAYER
      if (index == data.player_focus) {
        $(seat + " .p-info").attentionGrab();
      } else {
        $(seat + " .p-info").removeClass("focus");
      }

      // UPDATE PLAYER HAND (MINE VIA THE GAP-KEEPING FAN LAYOUT)
      if (playerData.hand === null) {
        $(seat + " .p-hand card").hide();
        if (index == idxMe) {
          myHandFanSlots = null;
        }
      } else if (index == idxMe) {
        $(seat + " .p-hand").updateArrayCards(
          myHandFanLayout(playerData.hand.cards),
        );
      } else {
        $(seat + " .p-hand").updateArrayCards(playerData.hand.cards);
      }

      // DARKEN UNPLAYABLE CARDS IN MY HAND WHILE IT IS MY TURN (SERVER SENDS legalPlays INDICES)
      if (index == idxMe) {
        var legalPlays = playerData.legal_plays;
        var darken =
          gameState == "PLAY HAND" &&
          data.player_focus == idxMe &&
          legalPlays !== null;
        // cardIdx IS THE RENDERED SLOT; legal_plays INDEXES THE COMPACTED SERVER
        // HAND, SO TRANSLATE (GAP SLOTS GET A NEIGHBOUR'S VERDICT - HARMLESS,
        // THEY'RE HIDDEN)
        $("#p-me .p-hand card").each(function (cardIdx) {
          if (darken && !legalPlays.includes(myHandServerIndex(cardIdx))) {
            $(this).find(".joker, .big, .index").addClass("quarter-opac");
          } else {
            $(this).find(".joker, .big, .index").removeClass("quarter-opac");
          }
        });
      }

      // UPDATE PLAYER KITTY
      if (playerData.kitty === null) {
        $(seat + " .p-kitty card").hide();
      } else {
        $(seat + " .p-kitty").updateArrayCards(playerData.kitty.cards);
      }
      // UPDATE PLAYER BID
      if (playerData.bid.suit === null) {
        if (playerData.bid.passed == true) {
          bidHtml = "<small class='bid-passed'>Didn't bid</small>";
          $(seat + " .bid-display")
            .removeClass("bold")
            .html(bidHtml);
          $(seat + " .bid-display")
            .parent()
            .show();
        } else {
          bidHtml = "No bid yet";
          $(seat + " .bid-display")
            .addClass("bold")
            .html(bidHtml);
          $(seat + " .bid-display")
            .parent()
            .hide();
        }
      } else {
        var bidPassed = playerData.bid.passed == true;
        // PASSED NO TRUMPS BIDS ABBREVIATE TO "x N.T." TO KEEP THE LINE SHORT
        var suitDisp =
          bidPassed && playerData.bid.suit === 5
            ? "N.T."
            : SUIT_DISP[playerData.bid.suit];
        bidHtml =
          "<span class='" +
          (SUIT_COL[playerData.bid.suit] || "") +
          "'>" +
          (playerData.bid.suit === 0
            ? "Misère"
            : playerData.bid.tricks + " " + suitDisp) +
          "</span>";
        if (bidPassed) {
          bidHtml =
            "<small class='bid-passed'>" + bidHtml + " (passed)</small>";
          $(seat + " .bid-display")
            .removeClass("bold")
            .html(bidHtml);
        } else {
          $(seat + " .bid-display")
            .addClass("bold")
            .html(bidHtml);
        }
        $(seat + " .bid-display")
          .parent()
          .show();
      }
    });

    // UPDATE TABLE KITTY
    if (data.kitty === null) {
      $("#table-kitty card").hide();
    } else {
      $("#table-kitty").updateArrayCards(data.kitty.cards);
    }
    // UPDATE TABLE CARDS
    if (
      data.players[idxMe].hand === null ||
      data.players[idxNext].hand === null ||
      data.players[idxPartner].hand === null ||
      data.players[idxPrevious].hand === null
    ) {
      $("#played-cards cards").hide();
    } else {
      var tableCards = [
        data.players[idxMe].table,
        data.players[idxNext].table,
        data.players[idxPartner].table,
        data.players[idxPrevious].table,
      ];
      var tableCount = tableCards.filter(function (c) {
        return c !== null;
      }).length;
      // fresh toss for the next trick: the count only ever drops when the won
      // trick is swept - to empty (hand end) or straight to the winner's lead
      // (the lingering trick cleared by their next card)
      if (tableCount < playedCardsTableCount) {
        var j = CONFIG.JITTER["#played-cards card"];
        applyCardJitter("#played-cards card", j.x, j.y, j.rot);
      }
      playedCardsTableCount = tableCount;
      $("#played-cards").updateArrayCards(tableCards);
      // STACK THE TABLE CARDS IN PLAY ORDER (LATER CARDS PAINT ON TOP): THE DOM
      // ORDER IS FIXED SEAT ORDER, SO WITHOUT THIS A LATER PLAY CAN RENDER UNDER
      // AN EARLIER ONE. current_trick IS THE PLAY-ORDER RECORD; DURING THE
      // WON-TRICK LINGER IT HAS ALREADY BEEN SWEPT TO trick_history, SO FALL BACK
      // TO THE LAST COMPLETED TRICK WHILE CARDS ARE STILL ON THE TABLE.
      var trickOrder = data.current_trick || [];
      var trickHistory = data.trick_history || [];
      if (
        trickOrder.length === 0 &&
        tableCount > 0 &&
        trickHistory.length > 0
      ) {
        trickOrder = trickHistory[trickHistory.length - 1].cards;
      }
      var playedCardIds = {};
      playedCardIds[idxMe] = "#p-me-played";
      playedCardIds[idxNext] = "#p-next-played";
      playedCardIds[idxPartner] = "#p-partner-played";
      playedCardIds[idxPrevious] = "#p-previous-played";
      $("#played-cards card").removeClass(
        "play-order-1 play-order-2 play-order-3 play-order-4",
      );
      trickOrder.forEach(function (play, order) {
        $(playedCardIds[play.seat]).addClass("play-order-" + (order + 1));
      });
    }

    // HIGHEST BID SO FAR... ranked the same way the server's gui_bid ranks bids:
    // misère (suit 0, tricks 10) sits between 8 spades (240) and 8 clubs (260),
    // matching the bid values table
    function bidRank(tricks, suit) {
      if (tricks == null || suit == null) return 0;
      if (suit === 0) return (8 * 6 + 1) * 2 + 1; // misère: above 8♠, below 8♣
      return (tricks * 6 + suit) * 2;
    }
    highestBid = [-1, -1];
    highestBidder = -1;
    var highestBidRank = 0;
    $.each(data.players, function (index, playerData) {
      var rank = bidRank(playerData.bid.tricks, playerData.bid.suit);
      if (rank > highestBidRank) {
        highestBidRank = rank;
        highestBid = [playerData.bid.tricks, playerData.bid.suit];
        highestBidder = index;
      }
    });

    updateBiddingBoxButtons();

    // SHOW BID WINNER CHIP AND TRICKS
    if (["AWARD KITTY", "PLAY HAND"].includes(gameState)) {
      $(data.players[highestBidder].seat_element_id + " .bid-winner").show(); // CHIP
      $.each(data.players, function (index, playerData) {
        $(playerData.seat_element_id + " .tricks-display span").each(
          function (starIndex, element) {
            if (starIndex + 1 < playerData.bid.tricks) {
              $(element).addClass("c-dark");
            } else {
              $(element).addClass("c-gray"); // NOT SHOWING MUCH...
            }
          },
        ); // TRICKS
      });

      // UPDATE THE PLAYER TRICKS PER SEAT
      $.each(data.players, function (index, playerData) {
        seat = playerData.seat_element_id;

        if (index === data.sitting_out) {
          // MISÈRE: THE CONTRACTOR'S PARTNER TAKES NO PART IN THE HAND
          $(seat + " .tricks-display").html("Sitting out");
          return;
        }

        if (data.trumps === 0) {
          // MISÈRE: OBJECTIVE TEXT INSTEAD OF THE TRICK BAR / COUNTS
          if (data.players[highestBidder].bid.won > 0) {
            $(seat + " .tricks-display").html("Misère failed!");
          } else if (index == highestBidder) {
            // FULL TEXT ONLY ON MY OWN (WIDE) PANEL; SIDE PANELS ARE NARROW
            $(seat + " .tricks-display").html(
              index == idxMe ? "Must lose ALL tricks!" : "Must lose!",
            );
          } else {
            // OPPONENTS' OBJECTIVE: MAKE THE MISÈRE BIDDER TAKE A TRICK
            $(seat + " .tricks-display").html(
              index == idxMe
                ? // displayName so a bot contractor's name renders the robot icon
                  "Make " +
                    displayName(data.players[highestBidder].name) +
                    " win a trick!"
                : "Force a win!",
            );
          }
          return;
        }

        if (index == idxMe) {
          $(seat + " .tricks-display").empty();
          wins = playerData.bid.won;
          partnerWins = data.players[idxMe].bid.won;
          losses =
            data.players[idxNext].bid.won + data.players[idxPrevious].bid.won;
          partnerWins = data.players[idxPartner].bid.won;

          if (highestBidder == idxMe || highestBidder == idxPartner) {
            contract = data.players[highestBidder].bid.tricks;
          } else {
            // DEFENDERS' TARGET: DENY THE CONTRACT — ONE MORE THAN THE LEFTOVERS
            contract = 10 - data.players[highestBidder].bid.tricks + 1;
          }

          for (i = 1; i < 11; i++) {
            trickDiv = $("<div>").css({
              display: "inline-block",
              width: "8%",
              height: "1rem",
            }); //,"border":"1px solid var(--fg-lighter)
            if (i == 1) {
              trickDiv.css({
                "border-top-left-radius": "0.3rem",
                "border-bottom-left-radius": "0.3rem",
              });
            } else {
              trickDiv.css({ "border-left": "1px solid var(--fg-lighter)" });
            }
            if (i == 10) {
              trickDiv.css({
                "border-top-right-radius": "0.3rem",
                "border-bottom-right-radius": "0.3rem",
              });
            }

            if (i <= wins) {
              trickDiv.css({ "background-color": "var(--fg-hue-4-darkest)" });
            } // won tricks
            else if (i <= wins + partnerWins) {
              trickDiv.css({ "background-color": "var(--fg-hue-4-darker)" });
            } // partner's won tricks
            else if (i <= contract && i >= 11 - losses) {
              trickDiv.css({ "background-color": "var(--fg-hue-5-2)" });
            } // lost and needed tricks
            else if (i <= Math.min(contract, 10 - losses)) {
              trickDiv.css({
                "background-color": "var(--fg-hue-4)",
                margin: "0.25rem 0rem",
                height: "0.5rem",
              });
            } // needed tricks
            else if (i >= 11 - losses) {
              trickDiv.css({ "background-color": "var(--fg-hue-5-lighter)" });
            } // lost but not needed tricks
            else {
              trickDiv.css({
                "background-color": "var(--fg-light)",
                margin: "0.25rem 0rem",
                height: "0.5rem",
              });
            } // unneeded tricks
            trickDiv.appendTo(seat + " .tricks-display");
          }
        } else {
          if (playerData.bid.won === null) {
            $(seat + " .tricks-display").html("0 tricks won");
          } else {
            $(seat + " .tricks-display").html(
              playerData.bid.won +
                (playerData.bid.won == 1 ? " trick won" : " tricks won"),
            );
          }
        }
      });
    } else {
      $(".bid-winner").hide(); // CHIP
      $("#p-me card").removeClass("selected");
    }

    if (!["AWARD KITTY"].includes(gameState)) {
      $("#discard-pane").hide();
    }
  } else {
    $("card").hide();
  }

  // SCOREBOARD TABLE - ONE ROW PER HAND: CUMULATIVE SCORE, THEN THE HAND'S AWARD ON A SECOND LINE
  function scoreCell(entry, runningTotal) {
    if (!entry) {
      return "";
    }
    var detail;
    if (entry.contracted && entry.contract[2] === 0) {
      detail = "Misère " + (entry.points >= 0 ? "made" : "failed");
    } else if (entry.contracted) {
      detail =
        entry.tricks +
        "/" +
        entry.contract[1] +
        " " +
        SUIT_STR[entry.contract[2]];
    } // won / bid suit
    else if (entry.contract[2] === 0) {
      detail = "vs Misère"; // opponents score nothing either way
    } else {
      detail = entry.tricks + " trick" + (entry.tricks == 1 ? "" : "s");
    }
    // contract detail coloured green (made) / red (failed), running total emphasised
    // - classes styled in styling.css
    if (entry.contracted) {
      detail =
        "<span class='" +
        (entry.points >= 0 ? "score-made" : "score-failed") +
        "'>" +
        detail +
        "</span>";
    }
    var award = (entry.points >= 0 ? "+" : "") + entry.points;
    return (
      "<span class='score-running'>" +
      runningTotal +
      "</span><br><small>" +
      award +
      " | " +
      detail +
      "</small>"
    );
  }
  var s1 = data.teams[0].score,
    s2 = data.teams[1].score;
  var gameOver =
    (Math.max(s1, s2) >= 500 || Math.min(s1, s2) <= -500) && s1 != s2;
  var winner = gameOver ? (s1 > s2 ? 0 : 1) : -1;
  var scoreRows = "";
  var running = [0, 0];
  var numHands = Math.max(
    data.teams[0].history.length,
    data.teams[1].history.length,
  );
  for (var h = 0; h < numHands; h++) {
    var e1 = data.teams[0].history[h];
    var e2 = data.teams[1].history[h];
    if (e1) {
      running[0] += e1.points;
    }
    if (e2) {
      running[1] += e2.points;
    }
    var lastRow = h == numHands - 1;
    scoreRows +=
      "<tr>" +
      "<td" +
      (lastRow && winner == 0 ? " class='score-winner'" : "") +
      ">" +
      scoreCell(e1, running[0]) +
      "</td>" +
      "<td" +
      (lastRow && winner == 1 ? " class='score-winner'" : "") +
      ">" +
      scoreCell(e2, running[1]) +
      "</td>" +
      "</tr>";
  }
  if (numHands == 0) {
    scoreRows =
      "<tr><td colspan='2' class='italic' style='color: var(--primary-color-light)'>No scores yet…</td></tr>";
  }
  $(".score-rows").html(scoreRows);
  if ($("#scores-modal").is(":visible")) {
    scrollScoresToBottom();
  }

  // displayName() PER NAME (NOT ON THE JOINED STRING) SO escapeHtml() INSIDE IT
  // NEVER MANGLES THE <br> INTO &lt;br&gt; - .html() + displayName SO BOT NAMES
  // RENDER THE ROBOT ICON
  $(".team-1-players").html(
    displayName(data.players[0].name) + "<br>" + displayName(data.players[2].name),
  );
  $(".team-2-players").html(
    displayName(data.players[1].name) + "<br>" + displayName(data.players[3].name),
  );
}

$(document).ready(function () {
  createCards(); // clone the cards throughout from the prototype

  $("#game-system-status")
    .appendSvg("cloud-arrow-down", "sync-icon", "m-1 mx-2 f-primary")
    .appendSvg("cloud-slash", "disconnected-icon", "m-1 mx-2 f-danger");
  $(".dealer-chip").appendSvg("dealer-chip", "", "c-dark bg-lighter ms-3");
  $(".bid-winner").appendSvg("contractor-chip", "", "f-green bg-lighter ms-3");

  // BID/JOKER-NOMINATION SUIT BUTTONS: MARKUP-FREE data-suit="N" BUTTONS IN THE
  // TEMPLATE, ICON FILLED IN HERE FROM THE SAME suitIconSvg() LOOKUP THE CARDS
  // USE - KEEPS THE SUIT SVGS DEFINED IN EXACTLY ONE PLACE (svg.js)
  $(".bid-suit-btn[data-suit], .joker-suit-btn[data-suit]").fillSuitIcons(
    "inline-icon",
  );

  // socketio based long polling. table_id ON THE QUERY STRING IS RE-SENT UNCHANGED
  // ON EVERY RECONNECT (NOT JUST THE INITIAL CONNECT), WHICH IS WHAT KEEPS THIS
  // SOCKET PINNED TO THE TABLE THE PAGE WAS RENDERED FOR (SEE main.py's connect HANDLER)
  var socket = io({ query: { table_id: tableId } });

  // Socket.IO long-polling drops and reconnects in normal operation, so a raw
  // disconnect event flashes the "disconnected" screen for a second. Debounce it:
  // only show the disconnected UI after 5s of continuous disconnection; any
  // reconnect within the grace period cancels it and the player never notices.
  var disconnectTimer = null;
  socket.on("connect", function (data, cb) {
    console.clear();
    console.table(Object.assign({}, localStorage)); // show persisted user settings
    if (disconnectTimer !== null) {
      clearTimeout(disconnectTimer);
      disconnectTimer = null;
    }
    updateComponentVisibility(true, null, null, null);
    stateDialogLog = [];
  });
  socket.on("disconnect", function (data, cb) {
    // the small red corner icon shows IMMEDIATELY (honest connection indicator);
    // only the full-screen disconnected modal waits out the 5s grace period.
    // reconnect hides it via updateComponentVisibility(true, ...)
    $("#disconnected-icon").show();
    if (disconnectTimer !== null) return; // grace period already running
    disconnectTimer = setTimeout(function () {
      disconnectTimer = null;
      if (!socket.connected) {
        updateComponentVisibility(false, null, null, null);
      }
    }, 3000);
  });
  socket.on("game_state", function (data, cb) {
    processGameStateData(data);
  });

  // SERVER-SENT TOASTS - A DEDICATED EVENT, DELIBERATELY NOT PART OF THE gameState
  // PUSH (THE 10s KEEP-ALIVE RE-SENDS FULL STATE AND WOULD REPLAY AN EMBEDDED TOAST).
  // category IS A BOLD ALL-CAPS HEADING ABOVE THE MESSAGE. audience "admin" IS COSMETIC
  // FILTERING ONLY (currently unused server-side) - NOTHING SENSITIVE ARRIVES IN A TOAST.
  socket.on("toast", function (data) {
    if (data.audience === "admin" && !isAdminUser) return;
    var $toast = $("<div>").addClass("toast toast-" + (data.kind || "info"));
    if (data.category) {
      $toast.append($("<div>").addClass("toast-category").text(data.category));
    }
    $toast.append(
      $("<div>")
        .addClass("toast-text")
        .html(displayName(data.text || "")),
    );
    $("#toast-stack").append($toast);
    setTimeout(
      function () {
        $toast.addClass("toast-hide"); // fade out (css transition), then remove
        setTimeout(function () {
          $toast.remove();
        }, 700);
      },
      (data.seconds || 4) * 1000,
    );
  });

  // THIS TABLE WAS REMOVED SERVER-SIDE (EMPTY-TABLE REAP, game_state.py's
  // reap_empty_tables) - THE ONLY WAY TO STILL BE CONNECTED HERE IS AS A LINGERING
  // SPECTATOR (A SEATED PLAYER KEEPS THE TABLE FROM EVER COUNTING AS EMPTY). THE ROOM
  // WON'T RECEIVE ANY FURTHER PUSHES, SO GO STRAIGHT BACK TO THE TABLE PICKER RATHER
  // THAN SIT ON A PAGE THAT WILL NEVER UPDATE AGAIN.
  socket.on("table_closed", function (data) {
    window.location.href = "/";
  });

  $(".unforce-scores").click(function () {
    forceScoreboardDisplay = false;
    $("#scores-modal").toggle(
      gameState == "AWARD POINTS" && username != "" && amSeated,
    );
  });

  $(".force-scores").click(function () {
    forceScoreboardDisplay = true;
    $("#scores-modal").toggle(forceScoreboardDisplay);
    scrollScoresToBottom();
  });

  $(".toggle-bid-ref").click(function () {
    $("#bid-ref-modal").toggle();
  });

  $(".hide-bid-ref").click(function () {
    $("#bid-ref-modal").hide();
  });

  $(".show-rules").click(function () {
    $("#rules-modal").show();
    $("#rules-modal .rules-scroll").scrollTop(0);
  });

  $(".hide-rules").click(function () {
    $("#rules-modal").hide();
  });

  $(".show-settings").click(function () {
    $("#settings-modal").toggle();
    // ADMIN SECTION LIVES IN THIS MODAL: RE-ASK /admin/uptime AND REFRESH THE TABLE
    // SELECTOR ON EVERY OPEN (SAME RATIONALE AS THE UPTIME RE-ASK - CATCHES A TABLE
    // CREATED/DELETED SINCE PAGE LOAD)
    if (isAdminUser && $("#settings-modal").is(":visible")) {
      startUptime();
      refreshAdminTableSelect();
    } else stopUptime();
  });
  $("#admin-table-select").change(updateAdminModeLabels);

  $(".hide-settings").click(function () {
    $("#settings-modal").hide();
    stopUptime();
  });

  // CLICKING THE BLURRED BACKDROP (OUTSIDE THE BOX) CLOSES DISMISSIBLE MODALS.
  // LOBBY + DISCONNECTED ARE STATE-DRIVEN AND STAY; SCOREBOARD REUSES ITS
  // UNFORCE LOGIC SO IT STAYS PINNED DURING AWARD POINTS.
  function closeOnBackdrop(modalSelector, close) {
    $(modalSelector).on("click", function (e) {
      if (!$(e.target).closest(".modal-box").length) {
        close();
      }
    });
  }
  closeOnBackdrop("#settings-modal", function () {
    $("#settings-modal").hide();
    stopUptime();
  });
  closeOnBackdrop("#bid-ref-modal", function () {
    $("#bid-ref-modal").hide();
  });
  closeOnBackdrop("#rules-modal", function () {
    $("#rules-modal").hide();
  });
  closeOnBackdrop("#scores-modal", function () {
    $(".unforce-scores").first().trigger("click");
  });

  // SERVICE UPTIME: THE SERVER IS ASKED ONCE PER MODAL OPEN AND THE DISPLAY TICKS
  // LOCALLY FROM THERE - RE-ASKING ON OPEN IS WHAT CATCHES A RESTART UNDER A PAGE
  // THAT WAS NEVER RELOADED.
  var uptimeSeconds = null;
  var uptimeTimer = null;

  function formatUptime(s) {
    s = Math.floor(s);
    var d = Math.floor(s / 86400);
    var h = Math.floor((s % 86400) / 3600);
    var m = Math.floor((s % 3600) / 60);
    var parts = [];
    if (d) parts.push(d + "d");
    if (d || h) parts.push(h + "h");
    parts.push(m + "m");
    parts.push((s % 60) + "s");
    return parts.join(" ");
  }

  function startUptime() {
    stopUptime();
    $.getJSON("/admin/uptime")
      .done(function (data) {
        uptimeSeconds = data.uptime;
        restartEnabled = !!data.restart_enabled;
        $("#restart-service").toggle(restartEnabled);
        $("#service-uptime").text(
          "SERVICE UPTIME: " + formatUptime(uptimeSeconds),
        );
        uptimeTimer = setInterval(function () {
          uptimeSeconds += 1;
          $("#service-uptime").text(
            "SERVICE UPTIME: " + formatUptime(uptimeSeconds),
          );
        }, 1000);
      })
      .fail(function () {
        $("#service-uptime").text("SERVICE UPTIME: unavailable");
      });
  }

  function stopUptime() {
    if (uptimeTimer) clearInterval(uptimeTimer);
    uptimeTimer = null;
  }

  function updateCardLayoutButton() {
    $("#toggle-card-layout").text(
      perfectCards ? "CARDS: PERFECT" : "CARDS: NATURAL",
    );
  }
  updateCardLayoutButton();

  $("#toggle-card-layout").click(function () {
    $(this).blur();
    perfectCards = !perfectCards;
    localStorage.setItem(CONFIG.STORAGE_KEY_PERFECT_CARDS, perfectCards);
    updateCardLayoutButton();
    // takes effect immediately: zeroes all jitter when perfect,
    // fresh roll when back to natural
    rollAllCardJitter();
  });

  // DESTRUCTIVE / DISRUPTIVE ACTIONS GO THROUGH THE TWO-STEP CONFIRM
  $(".reinit").click(function () {
    confirmThen(this, function () {
      $.ajax({ url: "/api/reinit" });
    });
  });
  $("#admin-reinit").click(function () {
    confirmThen(this, function () {
      $.ajax({ url: "/admin/reinit" + adminTableQuery() });
    });
  });
  $("#admin-delete-table").click(function () {
    confirmThen(this, function () {
      $.ajax({ url: "/admin/delete_table" + adminTableQuery() });
      // THE DROPDOWN NOW HAS A STALE (DELETED) ENTRY UNTIL THE MODAL NEXT REOPENS -
      // REFRESH IT NOW SO IT DOESN'T LINGER SELECTABLE
      refreshAdminTableSelect();
    });
  });
  $(".change-table").click(function () {
    confirmThen(this, function () {
      window.location.href = "/api/change_table";
    });
  });
  $("#save-checkpoint").click(function () {
    confirmThen(this, function () {
      $.ajax({ url: "/admin/save" + adminTableQuery() });
    });
  });
  $("#load-checkpoint").click(function () {
    confirmThen(this, function () {
      $.ajax({ url: "/admin/load" + adminTableQuery() });
    });
  });
  $("#clear-checkpoint").click(function () {
    confirmThen(this, function () {
      $.ajax({ url: "/admin/clearchk" + adminTableQuery() });
    });
  });
  $("#clear-local-data").click(function () {
    confirmThen(this, function () {
      // WIPE EVERY PERSISTED web500 SETTING, THEN RELOAD SO DEFAULTS RE-APPLY
      Object.keys(localStorage).forEach(function (key) {
        if (key.includes("web500")) {
          localStorage.removeItem(key);
        }
      });
      window.location.reload();
    });
  });
  $("#restart-service").click(function () {
    confirmThen(this, function () {
      // THE SERVER ANSWERS BEFORE IT DIES, THEN THE SOCKET DROPS AND RECONNECTS ON
      // ITS OWN - THE GAME COMES BACK FROM THE AUTOSAVE.
      // THE LOCAL ONCE-A-SECOND TICKER WOULD OTHERWISE OVERWRITE "restarting..." AND
      // CARRY ON FROM THE OLD UPTIME (THE MODAL ONLY RE-ASKS THE SERVER ON OPEN):
      // STOP THE TICKER, THEN POLL /admin/uptime UNTIL THE SERVICE IS BACK AND RESTART
      // THE DISPLAY FROM THE FRESH (NEAR-ZERO) VALUE.
      stopUptime();
      $("#service-uptime").text("SERVICE UPTIME: restarting...");
      $.ajax({ url: "/admin/restart" });
      var retries = 10;
      var restartPoll = setInterval(function () {
        if (!$("#settings-modal").is(":visible") || retries-- <= 0) {
          clearInterval(restartPoll); // modal closed or gave up - open re-asks anyway
          return;
        }
        $.getJSON("/admin/uptime").done(function () {
          clearInterval(restartPoll);
          startUptime();
        });
      }, 3000);
    });
  });
  $("#logout").click(function () {
    confirmThen(this, function () {
      window.location.href = "/logout";
    });
  });
  // plain refresh, no confirm needed (harmless - all state re-renders from the next
  // server push)
  $("#reload-client").click(function () {
    window.location.reload();
  });

  $(".seat-select").click(function () {
    socket.emit("seat_request", {
      seat_i: $(this).data("seat-number"),
      username: username,
    });
  });

  // Fill the remaining empty seats with server-side player bots (bots.py). No payload
  // needed - identity comes from the session, the server fills whatever is vacant.
  $("#add-bots").click(function () {
    $(this).prop("disabled", true); // debounce: seating is paced server-side (~1s/bot)
    socket.emit("add_bots", {});
  });

  $("#bidding-box button").click(function () {
    $(this).blur();
    if (!$(this).hasClass("toggle-bid-ref")) {
      $(this).toggleClass("active"); // THE BID VALUES "?" BUTTON IS NOT A SELECTION
    }
    tricks = $(this).data("tricks");
    suit = $(this).data("suit");

    // MISÈRE AND ITS AUTO-SELECTED 10 ACT AS A PAIR: DESELECTING THE 10 ALSO
    // CLEARS MISÈRE (OTHERWISE THE UPDATE BELOW WOULD JUST RE-SELECT THE 10)
    if (
      $(this).is('.bid-tricks-btn[data-tricks="10"]') &&
      !$(this).hasClass("active") &&
      $('#bidding-box button[data-suit="0"].active').length
    ) {
      $('#bidding-box button[data-suit="0"]').removeClass("active");
    }

    updateBiddingBoxButtons();

    // submission of bid
    if ($(this).hasClass("bid-submit-btn")) {
      bid = {
        suit: $("#bidding-box button.bid-suit-btn.active").data("suit"),
        tricks: $("#bidding-box button.bid-tricks-btn.active").data("tricks"),
        pass: $("#bidding-box button.bid-pass-btn.active").length == 1,
      };
      socket.emit("bid_submit", { bid: bid, username: username });
      $("#bidding-box button").removeClass("active");
      $("#bidding-box").hide();
    }
  });

  $(".joker-suit-btn").click(function () {
    $(this).blur();
    socket.emit("joker_nominate", {
      suit: $(this).data("suit"),
      username: username,
    });
  });

  $("#p-me card").click(function () {
    if (gameState == "AWARD KITTY") {
      $(this).toggleClass("selected");
      if ($("#p-me card.selected").length == 3) {
        $("#discard-pane").show();
      } else {
        $("#discard-pane").hide();
      }
    } else if (gameState == "PLAY HAND") {
      socket.emit("play_card", {
        // RENDERED SLOT -> COMPACTED SERVER HAND INDEX (FAN GAPS DON'T EXIST
        // SERVER-SIDE)
        card: myHandServerIndex($("#p-me .p-hand card").index($(this))),
        username: username,
      });
    }
  });

  $("#discard-pane button").click(function () {
    discarded = { kitty: [], hand: [] };

    $("#p-me .p-kitty card").each(function (index, card) {
      if ($(card).hasClass("selected")) {
        discarded.kitty.push(index);
      }
    });
    $("#p-me .p-hand card").each(function (index, card) {
      if ($(card).hasClass("selected")) {
        discarded.hand.push(index);
      }
    });
    $("#p-me card").removeClass("selected");
    socket.emit("discard_submit", { discard: discarded, username: username });
  });
});
