from random import shuffle

# ---------------------------------------------------------------------------
# CARD ENCODING - MIRRORED IN static/game_client.js. KEEP THE TWO IN STEP.
#
# SUITS 0-5. 1-4 ARE REAL SUITS, RANKED LOW TO HIGH (SPADES < CLUBS < DIAMONDS <
# HEARTS) EXACTLY AS 500 BIDDING RANKS THEM - THE SORT BELOW LEANS ON THAT ORDER.
# 0 (MISÈRE) AND 5 (NO TRUMPS) ARE CONTRACT TYPES, NOT SUITS A CARD CAN HOLD -
# THE ONE EXCEPTION IS THE JOKER, STORED AS SUIT 5 SO IT OUTRANKS EVERY REAL SUIT.
#
# RANKS 3-15. 3-10 ARE FACE VALUES, 11=JACK .. 14=ACE, 15=JOKER. RANK 3 IS ONLY
# LEGAL IN RED SUITS AND Deck(fill=True) NEVER ACTUALLY DEALS ONE (SEE __init__).
# ---------------------------------------------------------------------------
SUIT_STR  = ["Misère","Spades","Clubs","Diamonds","Hearts","No Trumps"]
SUIT_LETTER  = ["M","S","C","D","H","N"]
SUIT_DISP = [None,"♠","♣","♦","♥",""] # not displayable for python output.
SUIT_LEFT_BOWER = [None,2,1,4,3,None] # trumps suit -> the same-colour suit whose jack is the left bower
RANK_STR  = [None,None,None,"3","4","5","6","7","8","9","10","Jack","Queen","King","Ace","Joker"]
RANK_LETTER  = [None,None,None,"3","4","5","6","7","8","9","10","J","Q","K","A","JOK"]
RANK_DISP = [None,None,None,"3","4","5","6","7","8","9","10","J","Q","K","A","Jok"]

class Card(object):
  def __init__(self, suit=1, rank=3):
    self.suit = suit
    self.rank = rank
  def __str__(self):
    # SUIT 5 IS THE JOKER'S HOME - IT HAS NO SUIT TO NAME
    if self.suit == 5:
      return RANK_LETTER[self.rank]
    else:
      return RANK_LETTER[self.rank] + "-" + SUIT_LETTER[self.suit]
  def full_str(self):
    if self.suit == 5:
      return RANK_STR[self.rank]
    else:
      return RANK_STR[self.rank] + " of " + SUIT_STR[self.suit]
  def __dict__(self):
    return {"suit": self.suit, "rank": self.rank}

class Deck(object):
  # fill=True BUILDS THE 43-CARD 500 DECK: BLACK SUITS 5-ACE (10 EACH), RED SUITS
  # 4-ACE (11 EACH), PLUS ONE JOKER = 43. fill=False GIVES AN EMPTY DECK, WHICH IS
  # HOW HANDS, KITTIES AND THE TRICK PILE ARE MADE.
  def __init__(self, fill=True):
    self.cards=[]
    if fill:
      for suit in range(1,3): # Create the black cards
        for rank in range(5, 15):
          new_card = Card(suit, rank)
          self.cards.append(new_card)
      for suit in range(3,5): # Create the red cards
        for rank in range(4, 15):
          new_card = Card(suit, rank)
          self.cards.append(new_card)
      new_card = Card(5, 15) # create the Joker
      self.cards.append(new_card)
  def __repr__(self):
    return str(self)
  def __str__(self):
    string = "["
    if len(self.cards):
      for card in self.cards:
        string = string + str(card) + ", "
      return string[:-2] + f"] ({len(self.cards)} cards)"
    else:
      return string + f"] ({len(self.cards)} cards)"
  def __dict__(self):
    return {"cards": [card.__dict__() for card in self.cards]}
  def add_card(self, card):
    self.cards.append(card)
  def remove_card(self, card):
    self.cards.remove(card)
  def remove_card_index(self, index):
    del self.cards[index]
  def pop_card(self, i=-1):
    return self.cards.pop(i)
  def move_cards(self, other_deck, num):
    # DEALS OFF THE END OF THIS DECK - THE DEAL, THE KITTY AWARD AND DISCARDS ALL USE THIS
    for i in range(num):
      other_deck.add_card(self.pop_card())
  def shuffle(self):
    shuffle(self.cards)
  def get_index(self, find_card):
    for index,card in enumerate(self.cards):
      if card == find_card:
        return index
    return -1

  def sort(self, trumps, joker_suit=None):
    
    # ORDERS THE DECK STRONGEST-FIRST FOR `trumps` - cards[0] IS THE BEST CARD, SUITS RUN
    # IN DESCENDING SUIT NUMBER. USED BOTH TO LAY A HAND OUT AND TO PICK A TRICK WINNER.

    # RANKING, HIGHEST FIRST: JOKER > RIGHT BOWER (JACK OF TRUMPS) > LEFT BOWER (JACK OF
    # THE SAME-COLOUR SUIT) > OTHER TRUMPS > OFF-SUIT BY SUIT THEN RANK.

    # HAND-ROLLED BUBBLE SORT RATHER THAN sort(key=...): "STRONGER THAN" IN 500 IS NOT A
    # TOTAL ORDER (TWO CARDS OF DIFFERENT NON-TRUMP SUITS DON'T COMPARE), SO THERE IS NO
    # SINGLE SORT KEY. EACH TRUMP SUIT GETS ITS OWN NEAR-IDENTICAL BRANCH BECAUSE THE
    # LEFT BOWER'S SUIT DIFFERS PER TRUMPS.

    # CAUTION: BEING STRONGEST-FIRST, cards[0] IS NOT NECESSARILY THE TRICK WINNER - IT
    # MAY BE AN OFF-SUIT CARD THAT CANNOT WIN AT ALL. SEE THE WINNER SCAN IN gui_play().
    
    if trumps == 0: # Misère plays as No Trumps - same ordering, no bowers, joker highest
      trumps = 5

    # EACH PASS BUBBLES THE WEAKEST REMAINING CARD DOWN TO THE END, SO THE TAIL GROWS
    # SORTED AND THE COMPARED RANGE SHRINKS BY ONE EACH TIME. EVERY SWAP BELOW MOVES
    # THE STRONGER CARD (c2) EARLIER - HENCE STRONGEST-FIRST.
    for reducing_range in range(len(self.cards)):
      for card_index in range(len(self.cards)-1-reducing_range):
        c1 = self.cards[card_index]
        c2 = self.cards[card_index + 1]

        # THE `None` BRANCHES BELOW ARE LOAD-BEARING, NOT DEAD: THEY MEAN "c1 ALREADY
        # OUTRANKS c2 BY THIS RULE - STOP HERE". WITHOUT THEM THE CHAIN WOULD FALL
        # THROUGH TO A LOWER-PRIORITY elif (E.G. PLAIN SUIT ORDER) AND WRONGLY SWAP,
        # DEMOTING A JOKER OR A BOWER. THE elif ORDER *IS* THE RANKING.

        if trumps == 5: # No trumps
          if c2.rank == 15: # Joker needs bumping up
            self.cards[card_index], self.cards[card_index + 1] = self.cards[card_index + 1], self.cards[card_index]
          elif c1.rank == 15:
            None # keep the Joker safe
          elif c2.suit > c1.suit: # second card's suit is larger, push first down
            self.cards[card_index], self.cards[card_index + 1] = self.cards[card_index + 1], self.cards[card_index]
          elif c2.suit == c1.suit and c2.rank > c1.rank: # second card's suit is same but rank is higher, push first down
            self.cards[card_index], self.cards[card_index + 1] = self.cards[card_index + 1], self.cards[card_index]

        elif trumps == 4: # Hearts is trumps (left bower is the jack of Diamonds, suit 3).
          if c2.rank == 15: # Joker needs bumping up
            self.cards[card_index], self.cards[card_index + 1] = self.cards[card_index + 1], self.cards[card_index]
          elif c1.rank == 15:
            None # keep the Joker safe
          elif c2.rank == 11 and c2.suit == trumps:  # second card is the right bower, push first down
            self.cards[card_index], self.cards[card_index + 1] = self.cards[card_index + 1], self.cards[card_index]
          elif c1.rank == 11 and c1.suit == trumps:
            None
          elif c2.rank == 11 and c2.suit == 3:  # second card is the left bower, push first down
            self.cards[card_index], self.cards[card_index + 1] = self.cards[card_index + 1], self.cards[card_index]
          elif c1.rank == 11 and c1.suit == 3:
            None
          elif c2.suit == trumps and c1.suit != trumps: # second card's suit is a trump, but first's isn't, push first down
            self.cards[card_index], self.cards[card_index + 1] = self.cards[card_index + 1], self.cards[card_index]
          elif c2.suit > c1.suit and c1.suit != trumps: # second card's suit is larger, push first down
            self.cards[card_index], self.cards[card_index + 1] = self.cards[card_index + 1], self.cards[card_index]
          elif c2.suit == c1.suit and c2.rank > c1.rank: # second card's suit is same but rank is higher, push first down
            self.cards[card_index], self.cards[card_index + 1] = self.cards[card_index + 1], self.cards[card_index]

        elif trumps == 3: # Diamonds is trumps (left bower is the jack of Hearts, suit 4).
          if c2.rank == 15: # Joker needs bumping up
            self.cards[card_index], self.cards[card_index + 1] = self.cards[card_index + 1], self.cards[card_index]
          elif c1.rank == 15:
            None # keep the Joker safe
          elif c2.rank == 11 and c2.suit == trumps:  # second card is the right bower, push first down
            self.cards[card_index], self.cards[card_index + 1] = self.cards[card_index + 1], self.cards[card_index]
          elif c1.rank == 11 and c1.suit == trumps:
            None
          elif c2.rank == 11 and c2.suit == 4:  # second card is the left bower, push first down
            self.cards[card_index], self.cards[card_index + 1] = self.cards[card_index + 1], self.cards[card_index]
          elif c1.rank == 11 and c1.suit == 4:
            None
          elif c2.suit == trumps and c1.suit != trumps: # second card's suit is a trump, but first's isn't, push first down
            self.cards[card_index], self.cards[card_index + 1] = self.cards[card_index + 1], self.cards[card_index]
          elif c2.suit > c1.suit and c1.suit != trumps: # second card's suit is larger, push first down
            self.cards[card_index], self.cards[card_index + 1] = self.cards[card_index + 1], self.cards[card_index]
          elif c2.suit == c1.suit and c2.rank > c1.rank: # second card's suit is same but rank is higher, push first down
            self.cards[card_index], self.cards[card_index + 1] = self.cards[card_index + 1], self.cards[card_index]

        elif trumps == 2: # Clubs is trumps (left bower is the jack of Spades, suit 1).
          if c2.rank == 15: # Joker needs bumping up
            self.cards[card_index], self.cards[card_index + 1] = self.cards[card_index + 1], self.cards[card_index]
          elif c1.rank == 15:
            None # keep the Joker safe
          elif c2.rank == 11 and c2.suit == trumps:  # second card is the right bower, push first down
            self.cards[card_index], self.cards[card_index + 1] = self.cards[card_index + 1], self.cards[card_index]
          elif c1.rank == 11 and c1.suit == trumps:
            None
          elif c2.rank == 11 and c2.suit == 1:  # second card is the left bower, push first down
            self.cards[card_index], self.cards[card_index + 1] = self.cards[card_index + 1], self.cards[card_index]
          elif c1.rank == 11 and c1.suit == 1:
            None
          elif c2.suit == trumps and c1.suit != trumps: # second card's suit is a trump, but first's isn't, push first down
            self.cards[card_index], self.cards[card_index + 1] = self.cards[card_index + 1], self.cards[card_index]
          elif c2.suit > c1.suit and c1.suit != trumps: # second card's suit is larger, push first down
            self.cards[card_index], self.cards[card_index + 1] = self.cards[card_index + 1], self.cards[card_index]
          elif c2.suit == c1.suit and c2.rank > c1.rank: # second card's suit is same but rank is higher, push first down
            self.cards[card_index], self.cards[card_index + 1] = self.cards[card_index + 1], self.cards[card_index]

        elif trumps == 1: # Spades is trumps (left bower is the jack of Clubs, suit 2).
          if c2.rank == 15: # Joker needs bumping up
            self.cards[card_index], self.cards[card_index + 1] = self.cards[card_index + 1], self.cards[card_index]
          elif c1.rank == 15:
            None # keep the Joker safe
          elif c2.rank == 11 and c2.suit == trumps:  # second card is the right bower, push first down
            self.cards[card_index], self.cards[card_index + 1] = self.cards[card_index + 1], self.cards[card_index]
          elif c1.rank == 11 and c1.suit == trumps:
            None
          elif c2.rank == 11 and c2.suit == 2:  # second card is the left bower, push first down
            self.cards[card_index], self.cards[card_index + 1] = self.cards[card_index + 1], self.cards[card_index]
          elif c1.rank == 11 and c1.suit == 2:
            None
          elif c2.suit == trumps and c1.suit != trumps: # second card's suit is a trump, but first's isn't, push first down
            self.cards[card_index], self.cards[card_index + 1] = self.cards[card_index + 1], self.cards[card_index]
          elif c2.suit > c1.suit and c1.suit != trumps: # second card's suit is larger, push first down
            self.cards[card_index], self.cards[card_index + 1] = self.cards[card_index + 1], self.cards[card_index]
          elif c2.suit == c1.suit and c2.rank > c1.rank: # second card's suit is same but rank is higher, push first down
            self.cards[card_index], self.cards[card_index + 1] = self.cards[card_index + 1], self.cards[card_index]

    # PRE-NOMINATED JOKER (NO TRUMPS / MISÈRE): DISPLAY IT AS THE TOP CARD OF ITS DECLARED
    # SUIT RATHER THAN OUT IN FRONT. THE SORT ABOVE LEAVES SUITS IN DESCENDING SUIT-NUMBER
    # ORDER, SO INSERT BEFORE THE FIRST CARD OF A SUIT <= THE DECLARED ONE.
    if joker_suit:
      for i, card in enumerate(self.cards):
        if card.rank == 15:
          joker = self.cards.pop(i)
          insert_at = len(self.cards)
          for j, other in enumerate(self.cards):
            if other.suit <= joker_suit:
              insert_at = j
              break
          self.cards.insert(insert_at, joker)
          break
