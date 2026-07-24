"""take-champion tactics as GreedyAgent score overrides (SOT-1892, opt-in).

ptcg-agent-take (rule-based, Kaggle 収束 575.5) proved a small set of
tactics that fable's generic greedy prior lacks. This module ports exactly
those, as *score adjustments* over `GreedyAgent.score_options`, so the same
lens can be injected — each independently opt-in, all default OFF — into

- the MCTS root action prior          (`PlannerConfig.tactics_prior`),
- the rollout policy                  (`PlannerConfig.tactics_rollout`),
- the Greedy/Rule fallback layers     (`SubmissionAgent(tactics=...)`).

Ported tactics (take sources in parentheses):

1. KO即取り (S_LETHAL, SOT-1635): an attack that Knocks Out the defender
   outranks every development action — win tempo now. Greedy's champion
   ordering keeps attacks below development because attacking ends the
   turn; a KO is the exception take proved.
2. 場切れガード (S_BENCH_INSURANCE, SOT-1694): with an EMPTY bench, playing
   a Basic Pokémon outranks everything but a winning attack — an empty
   bench is one Knock Out from a no-active loss (SOT-1835: 盤面全滅 was
   fable's most common Kaggle loss cause).
3. Doomed-Active ガード (S_ATTACH_DOOMED / S_EVOLVE_DOOMED, SOT-1682/1694):
   never attach to — and deprioritise evolving — an Active the opponent
   Knocks Out next turn, unless the play enables a KO first; the resource
   dies with the Pokémon.
4. Supporter 山切れガード (S_DECK_GUARD, SOT-1694): with the own deck at or
   below the guard level, Supporters (draw-heavy) rank below END — deck-out
   was 26% of take's 25-deck mirror losses.
5. プライズトレード昇格 (SOT-1682/1730): TO_ACTIVE/SWITCH promotion ranks
   by can-fire-now, then the net prize race of the trade it starts, then
   energy deficit / damage / cheaper prize gift / HP.

Like every fable evaluation term, all inputs are card ATTRIBUTES via
`CardIndex` (HP, damage, energy costs, ex/megaEx, weakness) — no card-ID
special cases. Unknown cards/attacks degrade to the unadjusted greedy score.
"""
from .cards import CardIndex
from .greedy_agent import GreedyAgent
from .observation import View

# SelectContext / OptionType / AreaType values (cg/api.py), as plain ints.
_CTX_MAIN = 0
_CTX_SWITCH = 3
_CTX_TO_ACTIVE = 4
_OT_PLAY = 7
_OT_ATTACH = 8
_OT_EVOLVE = 9
_OT_ABILITY = 10
_OT_ATTACK = 13
_AREA_HAND = 2
_AREA_ACTIVE = 4

# Own-deck size at or below which the Supporter/ability draw guard fires
# (take DECK_LOW_THRESHOLD, SOT-1694: every turn start forcibly draws one
# card, so this is the remaining safety margin in turns). Matches fable's
# rule_policy.DECK_RESERVE so the policy layers agree on "thin".
DECK_LOW_THRESHOLD = 6

# Override bands on the GreedyAgent score scale (champion scores span ~0-150:
# END 0 < RETREAT 4 < ATTACK ~20-32 < PLAY 40-70 < ATTACH 45-55 < ABILITY 60
# < EVOLVE 70-150). Ordering mirrors take's S_* bands: lethal > bench
# insurance > (champion ordering) > doomed evolve > non-lethal attack;
# guarded plays fall below END so they are only chosen when nothing else is
# offered.
T_LETHAL = 400.0            # + damage/prize tie-break: KO now
T_BENCH_INSURANCE = 300.0   # + HP tie-break: rebuild an empty bench
T_EVOLVE_DOOMED = 30.0      # above a non-lethal swing (evolving is free),
                            # below every other development action
T_ATTACH_DOOMED = -1.0      # below END: the Energy dies with the Pokémon
T_SUPPORTER_DECK_GUARD = -0.5   # below END: stop digging the own deck
T_ABILITY_DECK_GUARD = -0.4     # below END: pure-draw ability at a thin deck


class TakeTactics:
    """Context-gated take-tactic overrides over greedy option scores."""

    def __init__(self, card_index: CardIndex):
        self._cards = card_index

    # ---- public API -------------------------------------------------------

    def adjust(self, view: View, scores: list) -> list:
        """Return `scores` with the take-tactic overrides applied.

        Only MAIN and promotion (SWITCH/TO_ACTIVE) selections are touched;
        every other context keeps the champion scores unchanged.
        """
        context = view.select.context
        if context == _CTX_MAIN:
            return self._adjust_main(view, scores)
        if context in (_CTX_SWITCH, _CTX_TO_ACTIVE):
            return self._promotion_scores(view, scores)
        return scores

    def promotion_score(self, view: View, option_index: int) -> float:
        """Prize-trade promotion score for one option (RulePolicy hook)."""
        raw = view.select.options[option_index].raw
        pokemon = view.find_pokemon(raw.get("playerIndex", view.your_index),
                                    raw.get("area"), raw.get("index"))
        defender = view.opp.active[0] if view.opp.active else None
        return self._promotion_score(pokemon, defender)

    # ---- MAIN -------------------------------------------------------------

    def _adjust_main(self, view: View, scores: list) -> list:
        cards = self._cards
        out = list(scores)
        my_active = view.me.active[0] if view.me.active else None
        my_card = (cards.card(my_active.card_id)
                   if my_active is not None else None)
        defender = view.opp.active[0] if view.opp.active else None
        defender_card = (cards.card(defender.card_id)
                         if defender is not None else None)
        bench_empty = not any(p is not None for p in view.me.bench)
        deck_low = view.me.deck_count <= DECK_LOW_THRESHOLD
        # Doomed Active (SOT-1682): the opponent's affordable best attack
        # Knocks Out our current Active next turn.
        incoming = self._incoming_damage(defender, my_card)
        active_doomed = (my_active is not None
                         and 0 < my_active.hp <= incoming)
        for i, opt in enumerate(view.select.options):
            t = opt.type
            raw = opt.raw
            if t == _OT_ATTACK:
                dmg = self._option_attack_damage(raw, my_card, defender_card)
                if defender is not None and 0 < defender.hp <= dmg:
                    prize = (defender_card.prize_value
                             if defender_card is not None else 1)
                    out[i] = T_LETHAL + 0.01 * dmg + 10.0 * prize
            elif t == _OT_PLAY:
                card = self._hand_card(view, raw.get("index"))
                if card is None:
                    continue
                if card.card_type == 0 and card.basic and bench_empty:
                    out[i] = T_BENCH_INSURANCE + 0.01 * card.hp
                elif card.card_type == 3 and deck_low:  # SUPPORTER
                    out[i] = T_SUPPORTER_DECK_GUARD
            elif t == _OT_ATTACH:
                if (active_doomed and raw.get("inPlayArea") == _AREA_ACTIVE
                        and not self._enables_ko(my_active, my_card,
                                                 defender, defender_card)):
                    out[i] = T_ATTACH_DOOMED
            elif t == _OT_EVOLVE:
                if (my_active is not None
                        and raw.get("inPlayArea") == _AREA_ACTIVE
                        and raw.get("area") == _AREA_HAND):
                    evo = self._hand_card(view, raw.get("index"))
                    if evo is None:
                        continue
                    # Damage counters persist through evolution: the evolved
                    # HP is the evolution's max HP minus damage already taken.
                    evolved_hp = evo.hp - max(
                        0, my_active.max_hp - my_active.hp)
                    incoming_evo = self._incoming_damage(defender, evo)
                    if (incoming_evo >= evolved_hp
                            and not self._enables_ko(my_active, evo,
                                                     defender, defender_card)):
                        out[i] = T_EVOLVE_DOOMED
            elif t == _OT_ABILITY and deck_low:
                pokemon = view.find_pokemon(
                    view.your_index, raw.get("area"), raw.get("index"))
                if (pokemon is not None
                        and cards.card(pokemon.card_id).pure_draw):
                    out[i] = T_ABILITY_DECK_GUARD
        return out

    # ---- promotion (TO_ACTIVE / SWITCH) ------------------------------------

    def _promotion_scores(self, view: View, scores: list) -> list:
        defender = view.opp.active[0] if view.opp.active else None
        out = []
        for opt in view.select.options:
            raw = opt.raw
            pokemon = view.find_pokemon(
                raw.get("playerIndex", view.your_index),
                raw.get("area"), raw.get("index"))
            out.append(self._promotion_score(pokemon, defender))
        return out

    def _promotion_score(self, pokemon, defender) -> float:
        """Scalarised take promotion tuple (SOT-1682/1730 to_active_handler):
        (fire_rank, race, -deficit, dmg, -prize, hp), lexicographic via
        band spacing (each term's range is bounded below the next step)."""
        cards = self._cards
        card = (cards.card(pokemon.card_id) if pokemon is not None else None)
        defender_card = (cards.card(defender.card_id)
                         if defender is not None else None)
        best = self._best_attack(card)
        viable = best is not None and best.damage > 0
        if pokemon is not None and viable:
            deficit = max(0, best.energy_cost - len(pokemon.energies))
        else:
            deficit = 99  # never going to attack: last resort
        can_fire = 1 if (viable and deficit <= 0) else 0
        dmg = self._best_damage_vs(card, defender_card)
        ko_now = bool(can_fire and defender is not None
                      and 0 < defender.hp <= dmg)
        incoming = self._incoming_damage(defender, card)
        dies_next = (pokemon is not None and 0 < pokemon.hp <= incoming)
        prize = card.prize_value if card is not None else 1
        defender_prize = (defender_card.prize_value
                          if defender_card is not None else 1)
        # Net prizes of the trade this promotion starts (SOT-1730): a KO now
        # earns the defender's prizes, dying next turn concedes our own.
        race = (defender_prize if ko_now else 0) - (prize if dies_next else 0)
        # A promotion conceding 2+ net prizes without a return KO is a
        # multi-prize gift, not an attacker: rank it with non-firing bodies.
        fire_rank = can_fire if race > -2 else 0
        hp = float(pokemon.hp) if pokemon is not None else 0.0
        return (fire_rank * 1e6 + race * 1e5 + (99 - min(deficit, 99)) * 1e3
                + min(float(dmg), 999.0) + (3 - prize) * 0.1 + hp * 1e-4)

    # ---- card-attribute helpers --------------------------------------------

    def _hand_card(self, view: View, hand_index):
        hand = view.me.hand_card_ids or []
        if hand_index is None or not (0 <= hand_index < len(hand)):
            return None
        return self._cards.card(hand[hand_index])

    def _best_attack(self, card):
        """Highest-damage attack of `card` (None when it has no attacks)."""
        if card is None:
            return None
        best = None
        for aid in card.attack_ids:
            atk = self._cards.attack(aid)
            if best is None or atk.damage > best.damage:
                best = atk
        return best

    @staticmethod
    def _adjusted_damage(damage, attacker_card, defender_card) -> float:
        dmg = float(damage)
        if attacker_card is None or defender_card is None:
            return dmg
        if (defender_card.weakness is not None
                and defender_card.weakness == attacker_card.energy_type):
            return dmg * 2
        if (defender_card.resistance is not None
                and defender_card.resistance == attacker_card.energy_type):
            return max(0.0, dmg - 30.0)
        return dmg

    def _option_attack_damage(self, raw, my_card, defender_card) -> float:
        atk = self._cards.attack(raw.get("attackId"))
        return self._adjusted_damage(atk.damage, my_card, defender_card)

    def _best_damage_vs(self, card, defender_card) -> float:
        """Max weakness/resistance-adjusted damage `card` deals the defender."""
        if card is None:
            return 0.0
        best = 0.0
        for aid in card.attack_ids:
            atk = self._cards.attack(aid)
            best = max(best, self._adjusted_damage(
                atk.damage, card, defender_card))
        return best

    def _incoming_damage(self, opp_pokemon, my_card, headroom: int = 1):
        """Largest damage the opponent's Active could deal us next turn.

        Only attacks the opponent could actually PAY for count — cost at most
        their attached Energy plus `headroom` (the one manual attachment they
        get next turn) — so the doom logic never triggers on attacks that are
        turns away from usable (take SOT-1682)."""
        if opp_pokemon is None or opp_pokemon.card_id is None:
            return 0.0
        opp_card = self._cards.card(opp_pokemon.card_id)
        affordable = len(opp_pokemon.energies) + headroom
        worst = 0.0
        for aid in opp_card.attack_ids:
            atk = self._cards.attack(aid)
            if atk.energy_cost > affordable:
                continue
            worst = max(worst, self._adjusted_damage(
                atk.damage, opp_card, my_card))
        return worst

    def _enables_ko(self, pokemon, card, defender, defender_card,
                    headroom: int = 1) -> bool:
        """True iff `pokemon` (as `card`, +headroom Energy) can KO the
        defender THIS turn — even a dying Active should take the Knock Out
        first (take SOT-1682 doomed-Active escape hatch)."""
        if pokemon is None or card is None or defender is None:
            return False
        affordable = len(pokemon.energies) + headroom
        for aid in card.attack_ids:
            atk = self._cards.attack(aid)
            if atk.energy_cost > affordable:
                continue
            if 0 < defender.hp <= self._adjusted_damage(
                    atk.damage, card, defender_card):
                return True
        return False


class TacticalGreedyAgent(GreedyAgent):
    """GreedyAgent with the take-tactic overrides applied to every score.

    Drop-in for GreedyAgent wherever a policy ranks options by
    `score_options` (root prior, rollout, fallback layers). With no tactic
    context firing, scores — and therefore choices — equal GreedyAgent's.
    """

    def __init__(self, seed: int, deck=None, card_index=None):
        super().__init__(seed, deck=deck, card_index=card_index)
        self._tactics = None

    @property
    def tactics(self) -> TakeTactics:
        if self._tactics is None:
            self._tactics = TakeTactics(self.cards)
        return self._tactics

    def score_options(self, view: View) -> list:
        return self.tactics.adjust(view, super().score_options(view))
