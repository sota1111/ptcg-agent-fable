"""SOT-1795 tests — 竹式 rule policy (context table totality, deck guard,
ordering overrides, RuleAgent legality). Engine-independent."""
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from agents import actions
from agents.observation import adapt
from agents.rule_policy import (COUNT_MODE, DECK_RESERVE, YES_CONTEXTS,
                                RuleAgent, RulePolicy, preferred_count)
from tests.support import (observation, player, pokemon, select,
                           synthetic_card_index)

N_CONTEXTS = 49  # SelectContext 0..48 (cg/api.py:68-118)


def policy():
    return RulePolicy(card_index=synthetic_card_index())


class TestCountModeTable(unittest.TestCase):
    def test_table_is_total_over_all_shipped_contexts(self):
        # The point of the 竹式 table (SOT-1682/1694): EVERY context the
        # engine can ask about has an explicit entry — no random fallback.
        self.assertEqual(set(COUNT_MODE), set(range(N_CONTEXTS)))

    def test_modes_are_known(self):
        self.assertTrue(set(COUNT_MODE.values()) <= {"min", "max", "draw"})

    def test_preferred_count_min_max(self):
        self.assertEqual(preferred_count(8, 1, 3), 1)    # DISCARD: min
        self.assertEqual(preferred_count(16, 1, 3), 3)   # REMOVE_DC: max

    def test_unknown_future_context_commits_to_min(self):
        self.assertEqual(preferred_count(99, 1, 3), 1)
        self.assertEqual(preferred_count(-1, 0, 2), 0)

    def test_draw_guard_boundary(self):
        # DRAW_COUNT (38): max normally, min at deck <= DECK_RESERVE.
        self.assertEqual(preferred_count(38, 0, 3, deck_count=None), 3)
        self.assertEqual(preferred_count(38, 0, 3,
                                         deck_count=DECK_RESERVE + 1), 3)
        self.assertEqual(preferred_count(38, 0, 3,
                                         deck_count=DECK_RESERVE), 0)
        # TO_HAND (7) is guarded the same way.
        self.assertEqual(preferred_count(7, 0, 2, deck_count=DECK_RESERVE), 0)


class TestRulePolicyLegality(unittest.TestCase):
    def test_every_context_yields_a_legal_action(self):
        # Zero-random-fallback discipline: a generic 4-option selection is
        # answered legally for ALL 49 contexts (and two unknown ones).
        pol = policy()
        opts = [{"type": 0, "number": i} for i in range(4)]
        for context in list(range(N_CONTEXTS)) + [77, 200]:
            for lo, hi in ((0, 4), (1, 1), (2, 3)):
                view = adapt(observation(
                    select(opts, context=context, min_count=lo,
                           max_count=hi)))
                action = pol.choose(view)
                actions.validate(view.select, action)  # raises if illegal
                self.assertTrue(lo <= len(action) <= hi, (context, lo, hi))

    def test_rule_agent_no_fallbacks_on_synthetic_views(self):
        agent = RuleAgent(seed=3, deck=[101] * 60,
                          card_index=synthetic_card_index())
        opts = [{"type": 0, "number": i} for i in range(3)]
        for context in range(N_CONTEXTS):
            obs = observation(select(opts, context=context, min_count=1,
                                     max_count=2))
            action = agent.act(obs)
            self.assertTrue(1 <= len(action) <= 2)
        self.assertEqual(agent.fallback_count, 0)
        self.assertEqual(agent.decision_count, N_CONTEXTS)

    def test_initial_deck_call_returns_the_deck(self):
        agent = RuleAgent(seed=3, deck=list(range(1, 61)),
                          card_index=synthetic_card_index())
        self.assertEqual(agent.act(observation(None)),
                         list(range(1, 61)))


class TestOrderingOverrides(unittest.TestCase):
    def test_setup_active_prefers_hp(self):
        # SETUP_ACTIVE (1): hand has 102 (60 HP) and 101 (120 HP) — the
        # Active pick must be the higher-HP 101 (SOT-1682: survive the race).
        view = adapt(observation(
            select([{"type": 3, "area": 2, "index": 0},
                    {"type": 3, "area": 2, "index": 1}],
                   context=1, min_count=1, max_count=1),
            me=player(hand=[{"id": 102}, {"id": 101}], hand_count=2)))
        self.assertEqual(policy().choose(view), [1])

    def test_promotion_prefers_readiness(self):
        # TO_ACTIVE (4): bench 0 has no Energy, bench 1 has two — promote 1.
        view = adapt(observation(
            select([{"type": 3, "area": 5, "index": 0},
                    {"type": 3, "area": 5, "index": 1}],
                   context=4, min_count=1, max_count=1),
            me=player(bench=[pokemon(101), pokemon(102, energies=[2, 2])])))
        self.assertEqual(policy().choose(view), [1])

    def test_yes_contexts_answer_yes(self):
        for context in sorted(YES_CONTEXTS):
            view = adapt(observation(
                select([{"type": 2}, {"type": 1}],  # NO first, YES second
                       context=context, min_count=1, max_count=1)))
            self.assertEqual(policy().choose(view), [1], context)

    def test_more_devolve_answers_no(self):
        view = adapt(observation(
            select([{"type": 1}, {"type": 2}],  # YES first, NO second
                   context=45, min_count=1, max_count=1)))
        self.assertEqual(policy().choose(view), [1])

    def test_draw_context_maximises_until_deck_guard(self):
        opts = [{"type": 0, "number": i} for i in range(3)]
        healthy = adapt(observation(
            select(opts, context=38, min_count=0, max_count=3),
            me=player(deck_count=40)))
        thin = adapt(observation(
            select(opts, context=38, min_count=0, max_count=3),
            me=player(deck_count=DECK_RESERVE)))
        self.assertEqual(len(policy().choose(healthy)), 3)
        self.assertEqual(policy().choose(thin), [])


if __name__ == "__main__":
    unittest.main()
