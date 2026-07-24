"""SOT-1892 tests — take-tactics injection (prior/rollout/fallback, opt-in).

Engine-independent. Pins:
- each ported tactic's override (lethal-first, bench insurance, doomed-Active
  attach/evolve guards, Supporter/ability deck guard, prize-trade promotion),
- the champion-invariance contract: with no tactic context firing — and
  everywhere when the flags are OFF — scores and wiring equal the champion's,
- the injection wiring (PlannerConfig flags, RulePolicy/RuleAgent tactics,
  SubmissionAgent FABLE_TACTICS profile).
"""
import os
import sys
import unittest
from types import SimpleNamespace

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import main as submission
from agents.cards import CardIndex
from agents.greedy_agent import GreedyAgent
from agents.observation import adapt
from agents.planner import MctsPlanner, PlannerConfig
from agents.rule_policy import RulePolicy
from agents.take_tactics import (DECK_LOW_THRESHOLD, T_ATTACH_DOOMED,
                                 T_BENCH_INSURANCE, T_EVOLVE_DOOMED,
                                 T_LETHAL, T_SUPPORTER_DECK_GUARD,
                                 TacticalGreedyAgent, TakeTactics)
from tests.support import observation, player, pokemon, select

# Synthetic card master for tactic scenarios (test-only IDs).
# 301: our Basic Water attacker      hp=120, attack 401 (50, cost 1)
# 302: opp Basic Fire (weak Water)   hp=90,  attack 402 (60, cost 1)
# 303: Supporter, pure draw
# 304: our Basic wall (no attacks)   hp=70, 1 prize
# 305: our Basic Water ex (2 prizes) hp=200, attack 403 (120, cost 2)
# 306: Stage1 Water (evolves 301)    hp=140, attack 404 (80, cost 2)
# 307: our Basic with pure-draw ability
# 308: opp Basic Fire bruiser        hp=180, attack 405 (150, cost 2)
def tactics_index() -> CardIndex:
    def pkm(cid, hp, etype, attacks, ex=False, stage1=False, skills=()):
        return SimpleNamespace(
            cardId=cid, cardType=0, retreatCost=1, hp=hp,
            weakness=(3 if cid == 302 else None), resistance=None,
            energyType=etype, basic=not stage1, stage1=stage1, stage2=False,
            ex=ex, megaEx=False, tera=False, aceSpec=False, evolvesFrom=None,
            skills=list(skills), attacks=list(attacks))
    cards = [
        pkm(301, 120, 3, [401]),
        pkm(302, 90, 2, [402]),
        SimpleNamespace(cardId=303, cardType=3, retreatCost=0, hp=0,
                        weakness=None, resistance=None, energyType=0,
                        basic=False, stage1=False, stage2=False, ex=False,
                        megaEx=False, tera=False, aceSpec=False,
                        evolvesFrom=None,
                        skills=[SimpleNamespace(text="Draw 3 cards.")],
                        attacks=[]),
        pkm(304, 70, 3, []),
        pkm(305, 200, 3, [403], ex=True),
        pkm(306, 140, 3, [404], stage1=True),
        pkm(307, 60, 3, [],
            skills=[SimpleNamespace(text="Draw 3 cards.")]),
        pkm(308, 180, 2, [405]),
    ]
    attacks = [
        SimpleNamespace(attackId=401, damage=50, energies=[3]),
        SimpleNamespace(attackId=402, damage=60, energies=[2]),
        SimpleNamespace(attackId=403, damage=120, energies=[3, 3]),
        SimpleNamespace(attackId=404, damage=80, energies=[3, 3]),
        SimpleNamespace(attackId=405, damage=150, energies=[2, 2]),
    ]
    return CardIndex(cards, attacks)


def agents_pair():
    idx = tactics_index()
    return GreedyAgent(seed=0, card_index=idx), \
        TacticalGreedyAgent(seed=0, card_index=idx)


def main_view(options, me=None, opp=None):
    return adapt(observation(select(options, context=0), me=me, opp=opp))


END = {"type": 14}


class TestLethalFirst(unittest.TestCase):
    def test_ko_attack_outranks_development(self):
        # 301 (Water) vs 302 (Fire, weak to Water): 50 doubles to 100 >= 80.
        me = player(active=[pokemon(301, energies=[3])],
                    bench=[pokemon(304)],
                    hand=[{"id": 306}])
        opp = player(active=[pokemon(302, hp=80)], hand=None)
        options = [{"type": 13, "attackId": 401},
                   {"type": 9, "area": 2, "index": 0,
                    "inPlayArea": 5, "inPlayIndex": 0},
                   END]
        greedy, tactical = agents_pair()
        view = main_view(options, me, opp)
        scores = tactical.score_options(view)
        self.assertGreaterEqual(scores[0], T_LETHAL)
        self.assertEqual(max(range(3), key=lambda i: scores[i]), 0)
        # Champion ordering kept the development action above the attack.
        base = greedy.score_options(view)
        self.assertGreater(base[1], base[0])

    def test_non_lethal_attack_keeps_champion_score(self):
        me = player(active=[pokemon(301, energies=[3])],
                    bench=[pokemon(304)], hand=[])
        opp = player(active=[pokemon(302, hp=150)], hand=None)
        options = [{"type": 13, "attackId": 401}, END]
        greedy, tactical = agents_pair()
        view = main_view(options, me, opp)
        self.assertEqual(tactical.score_options(view),
                         greedy.score_options(view))


class TestBenchInsurance(unittest.TestCase):
    def test_empty_bench_basic_outranks_everything_else(self):
        me = player(active=[pokemon(301, energies=[3])], bench=[],
                    hand=[{"id": 304}, {"id": 303}], deck_count=40)
        opp = player(active=[pokemon(302, hp=150)], hand=None)
        options = [{"type": 7, "index": 0},   # play the Basic
                   {"type": 7, "index": 1},   # play the Supporter
                   {"type": 13, "attackId": 401}, END]
        _, tactical = agents_pair()
        scores = tactical.score_options(main_view(options, me, opp))
        self.assertGreaterEqual(scores[0], T_BENCH_INSURANCE)
        self.assertEqual(max(range(4), key=lambda i: scores[i]), 0)

    def test_occupied_bench_keeps_champion_scores(self):
        me = player(active=[pokemon(301, energies=[3])],
                    bench=[pokemon(304)],
                    hand=[{"id": 304}, {"id": 303}], deck_count=40)
        opp = player(active=[pokemon(302, hp=150)], hand=None)
        options = [{"type": 7, "index": 0}, {"type": 7, "index": 1}, END]
        greedy, tactical = agents_pair()
        view = main_view(options, me, opp)
        self.assertEqual(tactical.score_options(view),
                         greedy.score_options(view))


class TestDeckOutGuards(unittest.TestCase):
    def test_supporter_below_end_when_deck_low(self):
        me = player(active=[pokemon(301)], bench=[pokemon(304)],
                    hand=[{"id": 303}], deck_count=DECK_LOW_THRESHOLD)
        opp = player(active=[pokemon(302, hp=150)], hand=None)
        options = [{"type": 7, "index": 0}, END]
        _, tactical = agents_pair()
        scores = tactical.score_options(main_view(options, me, opp))
        self.assertEqual(scores[0], T_SUPPORTER_DECK_GUARD)
        self.assertLess(scores[0], scores[1])  # END wins

    def test_supporter_normal_when_deck_healthy(self):
        me = player(active=[pokemon(301)], bench=[pokemon(304)],
                    hand=[{"id": 303}], deck_count=DECK_LOW_THRESHOLD + 1)
        opp = player(active=[pokemon(302, hp=150)], hand=None)
        options = [{"type": 7, "index": 0}, END]
        greedy, tactical = agents_pair()
        view = main_view(options, me, opp)
        self.assertEqual(tactical.score_options(view),
                         greedy.score_options(view))

    def test_pure_draw_ability_guarded_at_thin_deck(self):
        me = player(active=[pokemon(307)], bench=[pokemon(304)],
                    hand=[], deck_count=DECK_LOW_THRESHOLD)
        opp = player(active=[pokemon(302, hp=150)], hand=None)
        options = [{"type": 10, "area": 4, "index": 0}, END]
        _, tactical = agents_pair()
        scores = tactical.score_options(main_view(options, me, opp))
        self.assertLess(scores[0], scores[1])  # END wins


class TestDoomedActiveGuards(unittest.TestCase):
    def doom_players(self, active):
        # 308 with one Energy affords attack 405 (cost 2 <= 1+1): 150 damage.
        me = player(active=[active], bench=[pokemon(304)], hand=[{"id": 306}])
        opp = player(active=[pokemon(308, hp=180, energies=[2])], hand=None)
        return me, opp

    def test_attach_to_doomed_active_below_end(self):
        me, opp = self.doom_players(pokemon(301, hp=120, energies=[3]))
        options = [{"type": 8, "inPlayArea": 4, "inPlayIndex": 0},
                   {"type": 8, "inPlayArea": 5, "inPlayIndex": 0}, END]
        greedy, tactical = agents_pair()
        view = main_view(options, me, opp)
        scores = tactical.score_options(view)
        self.assertEqual(scores[0], T_ATTACH_DOOMED)
        self.assertLess(scores[0], scores[2])          # below END
        base = greedy.score_options(view)
        self.assertEqual(scores[1], base[1])           # bench attach kept

    def test_attach_kept_when_it_can_take_the_ko_first(self):
        # 305 with 1 Energy: +1 affords attack 403 (120) — KOs a 100 HP
        # defender first, so the doomed Active still gets fed.
        me = player(active=[pokemon(305, hp=100, energies=[3])],
                    bench=[pokemon(304)], hand=[])
        opp = player(active=[pokemon(308, hp=100, energies=[2])], hand=None)
        options = [{"type": 8, "inPlayArea": 4, "inPlayIndex": 0}, END]
        greedy, tactical = agents_pair()
        view = main_view(options, me, opp)
        self.assertEqual(tactical.score_options(view),
                         greedy.score_options(view))

    def test_evolve_doomed_active_ranks_below_development(self):
        # 301 at 40/120: evolving to 306 leaves 140-80=60 HP < 150 incoming,
        # and 306 (+1 Energy affords 404: 80) cannot KO 308 (180) first.
        me, opp = self.doom_players(pokemon(301, hp=40, energies=[3]))
        options = [{"type": 9, "area": 2, "index": 0,
                    "inPlayArea": 4, "inPlayIndex": 0},
                   {"type": 7, "index": 0}, END]
        me["hand"] = [{"id": 306}]
        _, tactical = agents_pair()
        scores = tactical.score_options(main_view(options, me, opp))
        self.assertEqual(scores[0], T_EVOLVE_DOOMED)
        self.assertLess(scores[0], scores[1])  # develop elsewhere first


class TestPrizeTradePromotion(unittest.TestCase):
    def promotion_view(self, bench, opp_active, context=4):
        me = player(active=[], bench=bench, hand=[])
        opp = player(active=[opp_active], hand=None)
        options = [{"type": 3, "area": 5, "index": i, "playerIndex": 0}
                   for i in range(len(bench))]
        return adapt(observation(select(options, context=context),
                                 me=me, opp=opp))

    def test_firing_attacker_promoted_over_wall(self):
        view = self.promotion_view(
            [pokemon(304), pokemon(305, energies=[3, 3])],
            pokemon(302, hp=80))
        _, tactical = agents_pair()
        scores = tactical.score_options(view)
        self.assertGreater(scores[1], scores[0])

    def test_cheap_fodder_over_ex_gift_when_both_die(self):
        # 308 (2 Energy) hits for 150: both candidates die next turn and
        # neither KOs back — promoting the 2-prize ex concedes the race
        # (SOT-1730), so the 1-prize wall soaks the hit instead.
        view = self.promotion_view(
            [pokemon(304, hp=50), pokemon(305, hp=50, energies=[3, 3])],
            pokemon(308, hp=180, energies=[2, 2]))
        _, tactical = agents_pair()
        scores = tactical.score_options(view)
        self.assertGreater(scores[0], scores[1])

    def test_rule_policy_tactics_promotes_fodder(self):
        idx = tactics_index()
        me = player(active=[], bench=[pokemon(304, hp=50),
                                      pokemon(305, hp=50, energies=[3, 3])],
                    hand=[])
        opp = player(active=[pokemon(308, hp=180, energies=[2, 2])],
                     hand=None)
        options = [{"type": 3, "area": 5, "index": i, "playerIndex": 0}
                   for i in range(2)]
        view = adapt(observation(select(options, context=4), me=me, opp=opp))
        self.assertEqual(RulePolicy(card_index=idx, tactics=True).choose(view),
                         [0])
        # Champion readiness override still promotes the loaded ex.
        self.assertEqual(RulePolicy(card_index=idx).choose(view), [1])


class TestChampionInvariance(unittest.TestCase):
    def test_untouched_contexts_score_identically(self):
        me = player(active=[pokemon(301, energies=[3])],
                    bench=[pokemon(304)], hand=[{"id": 303}], deck_count=40)
        opp = player(active=[pokemon(302, hp=150)], hand=None)
        for context in (7, 8, 24, 38):  # TO_HAND/DISCARD/LOOK/DRAW_COUNT
            options = [{"type": 3, "area": 3, "index": 0, "playerIndex": 0},
                       {"type": 0, "number": 2}]
            view = adapt(observation(select(options, context=context),
                                     me=me, opp=opp))
            greedy, tactical = agents_pair()
            self.assertEqual(tactical.score_options(view),
                             greedy.score_options(view), context)

    def test_planner_defaults_keep_the_champion_greedy(self):
        planner = MctsPlanner(own_deck=[301] * 60, config=PlannerConfig(),
                              card_index=tactics_index())
        self.assertIs(planner._prior_agent, planner._greedy)
        self.assertIs(planner._rollout_agent, planner._greedy)
        self.assertFalse(PlannerConfig().tactics_prior)
        self.assertFalse(PlannerConfig().tactics_rollout)

    def test_planner_flags_swap_in_the_tactical_agent(self):
        idx = tactics_index()
        prior_only = MctsPlanner(
            own_deck=[301] * 60, card_index=idx,
            config=PlannerConfig(tactics_prior=True))
        self.assertIsInstance(prior_only._prior_agent, TacticalGreedyAgent)
        self.assertIs(prior_only._rollout_agent, prior_only._greedy)
        both = MctsPlanner(
            own_deck=[301] * 60, card_index=idx,
            config=PlannerConfig(tactics_prior=True, tactics_rollout=True))
        self.assertIsInstance(both._rollout_agent, TacticalGreedyAgent)
        self.assertIs(both._prior_agent, both._rollout_agent)  # shared

    def test_fable_config_has_no_tactics_keys(self):
        self.assertFalse(set(submission.FABLE_CONFIG)
                         & {"tactics_prior", "tactics_rollout"})


class TestSubmissionWiring(unittest.TestCase):
    DECK = list(range(1, 61))

    def make(self, tactics=frozenset()):
        return submission.SubmissionAgent(
            seed=1, deck=self.DECK, card_index=tactics_index(),
            tactics=tactics)

    def test_default_is_champion(self):
        agent = self.make()
        self.assertFalse(agent._mcts.config.tactics_prior)
        self.assertFalse(agent._mcts.config.tactics_rollout)
        self.assertNotIsInstance(agent._greedy, TacticalGreedyAgent)

    def test_full_profile_arms_all_three_layers(self):
        agent = self.make(frozenset({"prior", "rollout", "fallback"}))
        self.assertTrue(agent._mcts.config.tactics_prior)
        self.assertTrue(agent._mcts.config.tactics_rollout)
        self.assertIsInstance(agent._greedy, TacticalGreedyAgent)
        self.assertTrue(agent._rule._policy._tactics)

    def test_tactics_profile_parsing(self):
        cases = [("", frozenset()),
                 ("prior", {"prior"}),
                 ("prior, rollout", {"prior", "rollout"}),
                 ("full", {"prior", "rollout", "fallback"}),
                 ("all", {"prior", "rollout", "fallback"}),
                 ("bogus", frozenset())]
        for raw, expected in cases:
            self.assertEqual(submission.tactics_profile(raw),
                             frozenset(expected), raw)


if __name__ == "__main__":
    unittest.main()
