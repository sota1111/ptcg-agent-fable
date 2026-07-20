"""SubmissionAgent (main.py, SOT-1795) — engine-free unit tests.

Covers the FABLE_CONFIG wiring (incl. the deck-preservation eval weights),
the remaining-time budget governor (budget steps down as cumulative think
time grows, Greedy handoff at exhaustion), and REACH of every layer of the
fallback chain: MCTS exception -> Greedy -> Rule -> raw legal action,
including the initial deck call.
"""
import unittest
from types import SimpleNamespace

import main as submission
from main import BUDGET_SCHEDULE, FABLE_CONFIG, SubmissionAgent
from tests import support

DECK = list(range(1, 61))


def make_submission_agent(clock=None):
    """Engine-free SubmissionAgent (synthetic card master, fake clock)."""
    return SubmissionAgent(seed=1, deck=DECK, clock=clock or FakeClock(),
                           card_index=support.synthetic_card_index())


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class StubAgent:
    """Records calls; optionally raises. Mimics the BaseAgent surface."""

    def __init__(self, action=(0,), raises=False):
        self.action = list(action)
        self.raises = raises
        self.calls = 0
        self.fallback_count = 0
        self.decision_count = 0
        self.config = SimpleNamespace(time_budget_s=None)
        self.budget_violations = 0
        self.planner_fallbacks = 0
        self.degraded_count = 0

    def act(self, obs):
        self.calls += 1
        if self.raises:
            raise RuntimeError("stub failure")
        return list(self.action)


def decision_obs():
    return support.observation(support.select([{"type": 0}, {"type": 0}]))


class TestFableConfig(unittest.TestCase):
    def test_mcts_core_uses_fable_config(self):
        agent = make_submission_agent()
        for key, value in FABLE_CONFIG.items():
            if key == "eval_weights":
                continue  # not a PlannerConfig field; checked below
            self.assertEqual(getattr(agent._mcts.config, key), value, key)
        # Unpinned fields keep the documented PlannerConfig defaults.
        self.assertEqual(agent._mcts.config.uct_c, 1.4)
        self.assertEqual(agent._mcts.config.rollout, "greedy")
        self.assertEqual(agent._mcts.config.deck_guard_threshold, 0)

    def test_deck_preservation_weights_are_on_from_v1(self):
        # SOT-1697 standing self-deck-out steer (main.py FABLE_CONFIG).
        agent = make_submission_agent()
        weights = agent._mcts._evaluator.weights
        for key, value in FABLE_CONFIG["eval_weights"].items():
            self.assertEqual(weights[key], value, key)

    def test_config_constant_is_not_mutated_by_construction(self):
        make_submission_agent()
        self.assertIn("eval_weights", FABLE_CONFIG)

    def test_module_entrypoint_builds_submission_agent(self):
        self.assertIsNone(submission._agent)  # lazy until first agent() call


class TestBudgetGovernor(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.agent = make_submission_agent(clock=self.clock)
        self.stub = StubAgent()
        self.agent._mcts = self.stub

    def test_budget_steps_down_with_cumulative_think_time(self):
        expected = [(0.0, 0.8), (299.9, 0.8), (300.0, 0.4), (419.9, 0.4),
                    (420.0, 0.2), (509.9, 0.2)]
        for spent, budget in expected:
            self.agent.think_time_s = spent
            self.assertEqual(self.agent.current_budget(), budget, spent)
        self.agent.think_time_s = 510.0
        self.assertIsNone(self.agent.current_budget())

    def test_budget_is_applied_to_the_mcts_config(self):
        self.agent.think_time_s = 350.0
        self.agent.act(decision_obs())
        self.assertEqual(self.stub.config.time_budget_s, 0.4)
        self.assertEqual(self.stub.calls, 1)

    def test_exhausted_clock_hands_off_to_greedy(self):
        self.agent.think_time_s = BUDGET_SCHEDULE[-1][0]
        action = self.agent.act(decision_obs())
        self.assertEqual(self.stub.calls, 0)  # search never invoked
        self.assertEqual(self.agent.greedy_handoffs, 1)
        self.assertIsInstance(action, list)

    def test_think_time_accumulates_from_the_clock(self):
        original = self.stub.act

        def slow_act(obs):
            self.clock.t += 1.5
            return original(obs)

        self.stub.act = slow_act
        self.agent.act(decision_obs())
        self.assertAlmostEqual(self.agent.think_time_s, 1.5)
        self.assertEqual(len(self.agent.move_times), 1)
        self.assertAlmostEqual(self.agent.move_times[0], 1.5)


class TestFallbackChain(unittest.TestCase):
    """Reach test for each layer: MCTS -> Greedy -> Rule -> raw legal."""

    def setUp(self):
        self.agent = make_submission_agent()

    def test_mcts_exception_falls_back_to_greedy(self):
        self.agent._mcts = StubAgent(raises=True)
        greedy_before = self.agent._greedy.decision_count
        action = self.agent.act(decision_obs())
        self.assertEqual(self.agent.emergency_fallbacks, 1)
        self.assertEqual(self.agent._greedy.decision_count, greedy_before + 1)
        self.assertIsInstance(action, list)
        self.assertTrue(all(0 <= i < 2 for i in action))

    def test_mcts_and_greedy_fail_falls_back_to_rule(self):
        self.agent._mcts = StubAgent(raises=True)
        self.agent._greedy = StubAgent(raises=True)
        action = self.agent.act(decision_obs())
        self.assertEqual(self.agent.emergency_fallbacks, 2)
        self.assertEqual(self.agent._rule.decision_count, 1)  # rule reached
        self.assertEqual(self.agent._rule.fallback_count, 0)
        sel = decision_obs()["select"]
        self.assertTrue(sel["minCount"] <= len(action) <= sel["maxCount"])

    def test_triple_failure_falls_back_to_raw_legal_action(self):
        self.agent._mcts = StubAgent(raises=True)
        self.agent._greedy = StubAgent(raises=True)
        self.agent._rule = StubAgent(raises=True)
        obs = decision_obs()
        action = self.agent.act(obs)
        self.assertEqual(self.agent.emergency_fallbacks, 3)
        sel = obs["select"]
        self.assertTrue(sel["minCount"] <= len(action) <= sel["maxCount"])
        self.assertTrue(all(0 <= i < len(sel["option"]) for i in action))
        self.assertEqual(len(set(action)), len(action))

    def test_initial_deck_call_returns_deck_even_when_agents_fail(self):
        self.agent._mcts = StubAgent(raises=True)
        self.agent._greedy = StubAgent(raises=True)
        self.agent._rule = StubAgent(raises=True)
        obs = support.observation(None)
        self.assertEqual(self.agent.act(obs), DECK)
        # The deck call is not a decision: no move time is recorded.
        self.assertEqual(self.agent.move_times, [])

    def test_counters_proxy_the_inner_agents(self):
        agent = make_submission_agent()
        self.assertEqual(agent.fallback_count, 0)
        self.assertEqual(agent.budget_violations, 0)
        self.assertEqual(agent.planner_fallbacks, 0)
        self.assertEqual(agent.degraded_count, 0)
        self.assertEqual(agent.decision_count, 0)


if __name__ == "__main__":
    unittest.main()
