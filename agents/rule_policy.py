"""竹式 rule policy (SOT-1795) — explicit per-context decisions, no random holes.

ptcg-agent-take's SOT-1682/1694 lesson: the biggest cheap win over a generic
scorer is an explicit SelectContext dispatch that covers EVERY context the
engine can ask about, so nothing ever falls through to a random-legal pick.
This module encodes that as data + a thin policy class over fable's View:

- `COUNT_MODE` maps ALL 49 shipped SelectContext values (cg/api.py:68-118) to
  an explicit how-many rule ("min" / "max" / deck-guarded variants). Unknown
  future contexts (the competition may append enum values) default to "min" —
  commit to as little as allowed, never crash.
- `RulePolicy.choose(view)` ranks options with GreedyAgent's card-attribute
  scoring, applies per-context ordering overrides (setup Active is HP-first,
  promotion is readiness-first — SOT-1682), and picks the context's count.
- The self-deck-out guard (SOT-1697: deck-out dominated matsu's losses) lives
  here for the policy layer: draw/search selections (DRAW_COUNT, TO_HAND)
  flip from "max" to "min" once the own deck is at or below `deck_reserve`.
  The MCTS layer gets the same steer from the evaluator's `deck_low`
  gradient; this table also drives the planner's in-tree candidate counts.

`RuleAgent` wraps the policy as layer 3 of the submission fallback chain
(MCTS -> Greedy -> Rule -> random-legal; main.py).
"""
from . import actions
from .base import BaseAgent
from .greedy_agent import GreedyAgent
from .observation import View

# --- SelectContext (cg/api.py:68-118) ---
CTX_MAIN = 0
CTX_SETUP_ACTIVE = 1
CTX_SETUP_BENCH = 2
CTX_SWITCH = 3
CTX_TO_ACTIVE = 4
CTX_TO_HAND = 7
CTX_NOT_MOVE = 12
CTX_DRAW_COUNT = 38
CTX_COIN_HEAD = 46

# How many options to take per context: "min" (pay/commit as little as
# allowed), "max" (take everything beneficial), "draw" (max, but min under
# the deck guard). All 49 shipped contexts are listed EXPLICITLY — this
# table being total is the point (take's zero-random-fallback discipline).
COUNT_MODE = {
    0: "max",     # MAIN (min==max==1 in practice)
    1: "min",     # SETUP_ACTIVE_POKEMON: exactly the required Active
    2: "max",     # SETUP_BENCH_POKEMON: bench everything allowed
    3: "min",     # SWITCH: the one replacement
    4: "min",     # TO_ACTIVE: the one promotion
    5: "max",     # TO_BENCH: more Pokémon in play
    6: "max",     # TO_FIELD: more Pokémon in play
    7: "draw",    # TO_HAND: gain cards — unless it digs the own deck out
    8: "min",     # DISCARD: cost
    9: "min",     # TO_DECK: cost
    10: "min",    # TO_DECK_BOTTOM: cost
    11: "min",    # TO_PRIZE: cost
    12: "max",    # NOT_MOVE: keep as much as allowed where it is
    13: "max",    # DAMAGE_COUNTER: place all allowed damage
    14: "max",    # DAMAGE_COUNTER_ANY
    15: "max",    # DAMAGE
    16: "max",    # REMOVE_DAMAGE_COUNTER: heal as much as allowed
    17: "max",    # HEAL
    18: "max",    # EVOLVES_FROM: evolving is development
    19: "max",    # EVOLVES_TO
    20: "max",    # DEVOLVE (targets ranked like damage: hit the opponent)
    21: "max",    # ATTACH_FROM: attach (to the best own Pokémon)
    22: "max",    # ATTACH_TO
    23: "min",    # DETACH_FROM: cost
    24: "max",    # LOOK: information is free
    25: "min",    # EFFECT_TARGET: unknown benefit — commit minimally
    26: "min",    # DISCARD_ENERGY_CARD: cost
    27: "min",    # DISCARD_TOOL_CARD: cost
    28: "min",    # SWITCH_ENERGY_CARD: minimal disturbance
    29: "min",    # DISCARD_CARD_OR_ATTACHED_CARD: cost
    30: "min",    # DISCARD_ENERGY: cost
    31: "min",    # TO_HAND_ENERGY: cost
    32: "min",    # TO_DECK_ENERGY: cost
    33: "min",    # SWITCH_ENERGY: minimal disturbance
    34: "min",    # SKILL_ORDER: order prompt — take the natural order
    35: "max",    # ATTACK (choose which): best attack
    36: "max",    # DISABLE_ATTACK: disable the strongest threat
    37: "max",    # EVOLVE: development
    38: "draw",   # DRAW_COUNT: max, min under the deck guard (SOT-1697)
    39: "max",    # DAMAGE_COUNTER_COUNT
    40: "max",    # REMOVE_DAMAGE_COUNTER_COUNT
    41: "max",    # IS_FIRST: yes — go first
    42: "max",    # MULLIGAN: yes — draw on the opponent's mulligan
    43: "max",    # ACTIVATE: yes — our own effects are worth activating
    44: "max",    # FIRST_EFFECT: yes
    45: "min",    # MORE_DEVOLVE: no — never repeat effects we cannot value
    46: "max",    # COIN_HEAD: yes (pure chance; a fixed call is fine)
    47: "max",    # AFFECT_SPECIAL_CONDITION: worst condition on the opponent
    48: "max",    # RECOVER_SPECIAL_CONDITION: recover the worst first
}
# YES/NO contexts where "max" above means answering YES; the ordering
# override below ranks the YES option first for exactly these.
YES_CONTEXTS = frozenset({41, 42, 43, 44, 46})

_OT_YES, _OT_NO = 1, 2

# Own deck size at or below which draw/search selections stop maximising
# (SOT-1697 self-deck-out guard, policy layer). Distinct from the evaluator's
# smoother deck_low_at=14 gradient: this is the hard floor for explicit
# "how many cards do you dig" prompts.
DECK_RESERVE = 6


def preferred_count(context, lo: int, hi: int, deck_count=None,
                    deck_reserve: int = DECK_RESERVE) -> int:
    """Per-context selection count (used by RulePolicy AND the planner's
    in-tree candidate enumeration — the 竹式事前分岐 integration point).

    `deck_count` is the ACTING side's remaining deck; None skips the guard.
    Unknown/future contexts commit to the minimum.
    """
    mode = COUNT_MODE.get(context, "min")
    if mode == "draw":
        guarded = deck_count is not None and deck_count <= deck_reserve
        mode = "min" if guarded else "max"
    return hi if mode == "max" else lo


class RulePolicy:
    """Deterministic context-dispatched policy over fable's View."""

    def __init__(self, card_index=None):
        self._greedy = GreedyAgent(seed=0, card_index=card_index)

    @property
    def cards(self):
        return self._greedy.cards

    def choose(self, view: View) -> list:
        sel = view.select
        lo, hi = actions.count_bounds(sel)
        n = len(sel.options)
        if n == 0:
            return []
        order = self._ordered_options(view)
        k = preferred_count(sel.context, lo, hi,
                            deck_count=view.me.deck_count)
        k = min(max(k, lo), hi)  # always legal, whatever the table says
        return sorted(order[:k])

    # ---- ordering -------------------------------------------------------

    def _ordered_options(self, view: View) -> list:
        context = view.select.context
        key = self._ordering_override(view, context)
        if key is None:
            scores = self._greedy.score_options(view)
            return sorted(range(len(scores)), key=lambda i: (-scores[i], i))
        return sorted(range(len(view.select.options)),
                      key=lambda i: (-key(i), i))

    def _ordering_override(self, view: View, context):
        """Context-specific ranking keys where greedy's generic card value
        is the wrong lens (SOT-1682 tactics)."""
        if context == CTX_SETUP_ACTIVE:
            # Active must survive the opening race: HP first.
            return lambda i: self._option_card_hp(view, i)
        if context in (CTX_SWITCH, CTX_TO_ACTIVE):
            # Promote the readiest Pokémon: attached Energy, then HP.
            return lambda i: self._option_readiness(view, i)
        if context in YES_CONTEXTS:
            return lambda i: (1.0 if view.select.options[i].type == _OT_YES
                              else 0.0)
        if context == 45:  # MORE_DEVOLVE: prefer NO
            return lambda i: (1.0 if view.select.options[i].type == _OT_NO
                              else 0.0)
        return None

    def _option_card_hp(self, view: View, i: int) -> float:
        opt = view.select.options[i]
        raw = opt.raw
        pokemon = view.find_pokemon(raw.get("playerIndex", view.your_index),
                                    raw.get("area"), raw.get("index"))
        if pokemon is not None:
            return float(pokemon.hp)
        card_id = self._option_card_id(view, opt)
        return float(self.cards.card(card_id).hp)

    def _option_readiness(self, view: View, i: int) -> float:
        opt = view.select.options[i]
        raw = opt.raw
        pokemon = view.find_pokemon(raw.get("playerIndex", view.your_index),
                                    raw.get("area"), raw.get("index"))
        if pokemon is None:
            card_id = self._option_card_id(view, opt)
            return float(self.cards.card(card_id).hp)
        return 100.0 * len(pokemon.energies) + float(pokemon.hp)

    def _option_card_id(self, view: View, opt):
        raw = opt.raw
        return self._greedy._resolve_card_id(
            view, raw.get("area"), raw.get("index"),
            raw.get("playerIndex", view.your_index))


class RuleAgent(BaseAgent):
    """Rule-policy agent — fallback layer 3 and a bench contestant."""

    def __init__(self, seed: int, deck=None, card_index=None):
        super().__init__(seed, deck)
        self._policy = RulePolicy(card_index=card_index)

    def choose(self, view: View) -> list:
        return self._policy.choose(view)
