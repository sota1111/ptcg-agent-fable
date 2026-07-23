"""SOT-1795 tests — determinized MCTS planner, evaluator, MctsAgent.

Engine-independent parts run everywhere; the reproducibility tests pin the
agent-side randomness against a deterministic backend double, and the
full-match test needs the cabt engine bindings (cg/, gitignored) and skips
automatically when absent.
"""
import os
import sys
import unittest
from types import SimpleNamespace

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from agents.evaluator import DEFAULT_WEIGHTS, HeuristicEvaluator
from agents.mcts_agent import MctsAgent
from agents.observation import adapt
from agents.planner import MctsPlanner, PlannerConfig, sample_fills
from tests.support import (observation, player, pokemon, select,
                           synthetic_card_index)

try:
    from cg import game  # noqa: F401
    HAS_ENGINE = True
except Exception:  # pragma: no cover - engine absent (CI)
    HAS_ENGINE = False


def eval_obs(me, opp, result=-1, your_index=0):
    """Engine-search-shaped (attribute-access) observation for the evaluator."""
    players = [me, opp] if your_index == 0 else [opp, me]
    return SimpleNamespace(current=SimpleNamespace(
        result=result, yourIndex=your_index, players=players))


def side(prize=6, active=(), bench=(), hand_count=5, deck_count=40):
    return SimpleNamespace(prize=[None] * prize, active=list(active),
                           bench=list(bench), handCount=hand_count,
                           deckCount=deck_count)


def mon(hp=100, energies=1):
    return SimpleNamespace(hp=hp, energies=[0] * energies)


class TestHeuristicEvaluator(unittest.TestCase):
    def test_terminal_results_are_exact(self):
        ev = HeuristicEvaluator()
        obs = eval_obs(side(), side(), result=0)
        self.assertEqual(ev.evaluate(obs, 0), 1.0)
        self.assertEqual(ev.evaluate(obs, 1), 0.0)
        draw = eval_obs(side(), side(), result=2)
        self.assertEqual(ev.evaluate(draw, 0), 0.5)

    def test_symmetric_position_is_half(self):
        ev = HeuristicEvaluator()
        obs = eval_obs(side(active=[mon()]), side(active=[mon()]))
        self.assertAlmostEqual(ev.evaluate(obs, 0), 0.5)
        self.assertAlmostEqual(ev.evaluate(obs, 1), 0.5)

    def test_prize_lead_dominates(self):
        ev = HeuristicEvaluator()
        ahead = eval_obs(side(prize=2, active=[mon()]),
                         side(prize=6, active=[mon()]))
        self.assertGreater(ev.evaluate(ahead, 0), 0.8)
        self.assertLess(ev.evaluate(ahead, 1), 0.2)

    def test_weights_are_externally_overridable(self):
        flat = HeuristicEvaluator({k: 0.0 for k in DEFAULT_WEIGHTS})
        ahead = eval_obs(side(prize=1), side(prize=6))
        self.assertAlmostEqual(flat.evaluate(ahead, 0), 0.5)

    def test_deck_low_gradient_penalises_thin_own_deck(self):
        # SOT-1697 standing guard: below deck_low_at every missing card
        # costs deck_low; symmetric sides otherwise.
        ev = HeuristicEvaluator({"deck_low": -0.2, "deck_low_at": 14})
        thin = eval_obs(side(deck_count=4), side(deck_count=40))
        self.assertLess(ev.evaluate(thin, 0), 0.5)
        healthy = eval_obs(side(deck_count=14), side(deck_count=40))
        self.assertAlmostEqual(ev.evaluate(healthy, 0), 0.5)

    def test_deck_low_prize_gate_frees_the_endgame(self):
        ev = HeuristicEvaluator({"deck_low": -0.2, "deck_low_at": 14,
                                 "deck_low_prize_gate": 3})
        near_win = eval_obs(side(deck_count=4, prize=2),
                            side(deck_count=40, prize=2))
        self.assertAlmostEqual(ev.evaluate(near_win, 0), 0.5)
        far = eval_obs(side(deck_count=4, prize=6),
                       side(deck_count=40, prize=6))
        self.assertLess(ev.evaluate(far, 0), 0.5)

    def test_facedown_pokemon_counts_without_stats(self):
        ev = HeuristicEvaluator()
        obs = eval_obs(side(active=[None]), side())
        self.assertGreater(ev.evaluate(obs, 0), 0.5)

    def _flat(self, **overrides):
        w = {k: 0.0 for k in DEFAULT_WEIGHTS}
        w["scale"] = 0.6
        w.update(overrides)
        return HeuristicEvaluator(w)

    def test_bench_dev_rewards_a_populated_bench(self):
        # SOT-1863 board-wipe insurance: with bench_dev on, the side holding a
        # bench backup is favoured over an equal side with an empty bench
        # (flat base isolates the bench term from hp/energy/pokemon).
        ev = self._flat(bench_dev=0.3, bench_dev_cap=2)
        obs = eval_obs(side(active=[mon()], bench=[mon(), mon()]),
                       side(active=[mon()], bench=[]))
        self.assertGreater(ev.evaluate(obs, 0), 0.5)
        self.assertLess(ev.evaluate(obs, 1), 0.5)

    def test_bench_dev_saturates_at_the_cap(self):
        # Beyond bench_dev_cap extra bench Pokémon add no bench_dev value
        # (front-loaded on the first backups that prevent a wipe).
        ev = self._flat(bench_dev=0.3, bench_dev_cap=1)
        capped = eval_obs(side(active=[mon()], bench=[mon(), mon(), mon()]),
                          side(active=[mon()], bench=[mon()]))
        self.assertAlmostEqual(ev.evaluate(capped, 0), 0.5)

    def test_bench_dev_off_by_default(self):
        # Champion default (bench_dev_cap=0) adds no bench term at all.
        ev = self._flat(bench_dev=0.3, bench_dev_cap=0)
        obs = eval_obs(side(active=[mon()], bench=[mon(), mon()]),
                       side(active=[mon()], bench=[]))
        self.assertAlmostEqual(ev.evaluate(obs, 0), 0.5)

    def test_evo_ready_rewards_evolved_pokemon(self):
        evolved = SimpleNamespace(hp=100, energies=[0], preEvolution=[{"id": 1}])
        plain = SimpleNamespace(hp=100, energies=[0], preEvolution=[])
        ev = self._flat(evo_ready=0.4)
        obs = eval_obs(side(active=[evolved]), side(active=[plain]))
        self.assertGreater(ev.evaluate(obs, 0), 0.5)


class TestSampleFills(unittest.TestCase):
    DECK = [101] * 20 + [102] * 20 + [103] * 20

    def rng(self, seed=7):
        from agents.rng import Rng
        return Rng(seed)

    def raw_obs(self, me=None, opp=None):
        return observation(select([]), me=me, opp=opp)

    def test_fill_sizes_match_visible_counts(self):
        me = player(active=[pokemon(101)], deck_count=30, hand_count=4,
                    prize=6, hand=[{"id": 103}] * 4)
        opp = player(active=[pokemon(102)], deck_count=25, hand_count=7,
                     prize=5)
        fills = sample_fills(self.raw_obs(me, opp), self.DECK, self.rng(),
                             synthetic_card_index())
        self.assertEqual(len(fills.my_deck), 30)
        self.assertEqual(len(fills.my_prize), 6)
        self.assertEqual(len(fills.opp_deck), 25)
        self.assertEqual(len(fills.opp_prize), 5)
        self.assertEqual(len(fills.opp_hand), 7)
        self.assertEqual(fills.opp_active, [])

    def test_visible_cards_are_excluded_from_own_pool(self):
        # 19 copies of 102 visible in my discard -> at most 1 more 102 in
        # my hidden zones (20 in deck total).
        me = player(discard=[{"id": 102}] * 19, deck_count=41, prize=0,
                    hand_count=0, hand=[])
        fills = sample_fills(self.raw_obs(me=me), self.DECK, self.rng(),
                             synthetic_card_index())
        self.assertLessEqual(fills.my_deck.count(102), 1)

    def test_facedown_opponent_active_predicted_as_basic(self):
        opp = player(active=[None], deck_count=30, hand_count=5)
        fills = sample_fills(self.raw_obs(opp=opp), self.DECK, self.rng(),
                             synthetic_card_index())
        self.assertEqual(len(fills.opp_active), 1)
        self.assertTrue(synthetic_card_index().card(fills.opp_active[0]).basic)

    def test_opponent_deck_fill_contains_a_basic(self):
        opp = player(deck_count=10, hand_count=5, prize=6)
        fills = sample_fills(self.raw_obs(opp=opp), self.DECK, self.rng(),
                             synthetic_card_index())
        idx = synthetic_card_index()
        self.assertTrue(any(idx.card(c).basic for c in fills.opp_deck))

    def test_same_rng_seed_same_fills(self):
        obs = self.raw_obs()
        a = sample_fills(obs, self.DECK, self.rng(3), synthetic_card_index())
        b = sample_fills(obs, self.DECK, self.rng(3), synthetic_card_index())
        self.assertEqual(a, b)


class _ExplodingBackend:
    """Planner backend double: every world creation fails."""
    calls = 0

    def begin(self, raw_obs, fills, manual_coin=True):
        self.calls += 1
        raise RuntimeError("no engine")

    def end(self):
        pass


def main_view(n_options=3):
    opts = [{"type": 13, "attackId": 201 + i, "number": 0} for i in
            range(n_options)]
    return adapt(observation(
        select(opts, sel_type=0, context=0, min_count=1, max_count=1),
        me=player(active=[pokemon(101, energies=[3])]),
        opp=player(active=[pokemon(102)])))


class TestMctsPlanner(unittest.TestCase):
    def planner(self, backend, **overrides):
        return MctsPlanner(own_deck=[101] * 60,
                           config=PlannerConfig(**overrides),
                           backend=backend,
                           card_index=synthetic_card_index())

    def rng(self, seed=5):
        from agents.rng import Rng
        return Rng(seed)

    def test_forced_selection_skips_search(self):
        view = adapt(observation(
            select([{"type": 1}], min_count=1, max_count=1)))
        # min == max == n(=1): only one legal action, backend never touched.
        planner = self.planner(backend=None)
        self.assertEqual(planner.plan(view, self.rng()), [0])
        self.assertTrue(planner.last_stats.get("forced"))

    def test_degrades_to_greedy_prior_when_no_world_builds(self):
        backend = _ExplodingBackend()
        planner = self.planner(backend, n_worlds=2)
        action = planner.plan(main_view(), self.rng())
        self.assertEqual(len(action), 1)
        self.assertEqual(planner.degraded_count, 1)
        self.assertTrue(planner.last_stats.get("degraded"))
        self.assertGreater(backend.calls, 0)

    def test_config_parameters_are_external(self):
        cfg = PlannerConfig(n_worlds=7, uct_c=0.3, rollout="random",
                            time_budget_s=1.5)
        planner = MctsPlanner(own_deck=[101] * 60, config=cfg, backend=None,
                              card_index=synthetic_card_index())
        self.assertEqual(planner.config.n_worlds, 7)
        self.assertEqual(planner.config.uct_c, 0.3)
        self.assertEqual(planner.config.rollout, "random")

    def test_root_deck_guard_defaults_off(self):
        # fable delta: matsu's screens rejected the root guard, so
        # deck_guard_threshold defaults to 0 (disabled); the standing steer
        # is the evaluator gradient + the rule-policy draw guard.
        self.assertEqual(PlannerConfig().deck_guard_threshold, 0)
        planner = self.planner(None)
        low = adapt(observation(
            select([{"type": 7, "index": 0}, {"type": 13, "attackId": 201}]),
            me=player(active=[pokemon(101)], hand=[{"id": 103}],
                      hand_count=1, deck_count=1),
            opp=player(active=[pokemon(102)])))
        self.assertEqual(len(planner._root_candidates(low, self.rng())[0]), 2)

    def test_deck_guard_threshold_boundary_filters_pure_draw(self):
        opts = [
            {"type": 7, "index": 0},  # supporter 103: pure draw
            {"type": 13, "attackId": 201},
        ]
        planner = self.planner(None, deck_guard_threshold=4)
        low = adapt(observation(
            select(opts),
            me=player(active=[pokemon(101)], hand=[{"id": 103}],
                      hand_count=1, deck_count=4),
            opp=player(active=[pokemon(102)])))
        above = adapt(observation(
            select(opts),
            me=player(active=[pokemon(101)], hand=[{"id": 103}],
                      hand_count=1, deck_count=5),
            opp=player(active=[pokemon(102)])))
        self.assertEqual(planner._root_candidates(low, self.rng())[0], [[1]])
        self.assertEqual(len(planner._root_candidates(above, self.rng())[0]),
                         2)

    def test_deck_guard_never_removes_lethal(self):
        view = adapt(observation(
            select([{"type": 7, "index": 0},
                    {"type": 13, "attackId": 201}]),
            me=player(active=[pokemon(101)], hand=[{"id": 103}],
                      hand_count=1, deck_count=1),
            opp=player(active=[pokemon(102, hp=40)], prize=1)))
        planner = self.planner(None, deck_guard_threshold=8)
        candidates, _ = planner._root_candidates(view, self.rng())
        self.assertEqual(candidates, [[1]])
        self.assertTrue(planner._is_lethal_option(view, 1))

    def test_deck_guard_prize_gate_frees_the_endgame_dig(self):
        opts = [
            {"type": 7, "index": 0},  # supporter 103: pure draw
            {"type": 13, "attackId": 201},
        ]
        planner = self.planner(None, deck_guard_threshold=4,
                               deck_guard_prize_gate=3)
        near_win = adapt(observation(
            select(opts),
            me=player(active=[pokemon(101)], hand=[{"id": 103}],
                      hand_count=1, deck_count=4, prize=2),
            opp=player(active=[pokemon(102)])))
        far = adapt(observation(
            select(opts),
            me=player(active=[pokemon(101)], hand=[{"id": 103}],
                      hand_count=1, deck_count=4, prize=3),
            opp=player(active=[pokemon(102)])))
        self.assertEqual(len(planner._root_candidates(near_win,
                                                      self.rng())[0]), 2)
        self.assertEqual(planner._root_candidates(far, self.rng())[0], [[1]])

    def test_zero_budget_still_returns_a_legal_action(self):
        # Anytime contract: deadline already passed -> greedy prior comes
        # back immediately (worlds may build, but no iteration runs).
        backend = _ExplodingBackend()
        planner = self.planner(backend, n_worlds=2)
        action = planner.plan(main_view(), self.rng(), budget_s=0.0)
        self.assertEqual(len(action), 1)
        self.assertIn(action[0], (0, 1, 2))

    def test_best_action_aggregates_visits_across_worlds(self):
        w1 = SimpleNamespace(root=SimpleNamespace(
            edges=[[[0], None, 5, 2.0], [[1], None, 9, 6.0]]))
        w2 = SimpleNamespace(root=SimpleNamespace(
            edges=[[[0], None, 6, 3.0], [[1], None, 4, 2.0]]))
        best = MctsPlanner._best_action([[0], [1]], [w1, w2])
        self.assertEqual(best, [1])  # 13 visits vs 11

    def test_deviate_margin_keeps_greedy_prior_without_evidence(self):
        w = SimpleNamespace(root=SimpleNamespace(
            edges=[[[0], None, 10, 5.5], [[1], None, 12, 6.7]]))
        # Challenger wins on visits but its mean (0.558) is within the
        # margin of the incumbent's (0.550) -> stay with the greedy prior.
        self.assertEqual(MctsPlanner._best_action([[0], [1]], [w]), [1])
        self.assertEqual(MctsPlanner._best_action([[0], [1]], [w], 0.05), [0])
        # A clear challenger still deviates.
        w2 = SimpleNamespace(root=SimpleNamespace(
            edges=[[[0], None, 10, 3.0], [[1], None, 12, 9.0]]))
        self.assertEqual(MctsPlanner._best_action([[0], [1]], [w2], 0.05),
                         [1])


class TestMctsAgent(unittest.TestCase):
    def test_planner_exception_falls_back_to_greedy(self):
        agent = MctsAgent(seed=1, deck=[101] * 60,
                          card_index=synthetic_card_index())

        class Boom:
            def plan(self, view, rng, budget_s=None):
                raise RuntimeError("boom")

        agent._planner = Boom()
        action = agent.act(main_view(2).raw)
        self.assertEqual(len(action), 1)
        self.assertEqual(agent.planner_fallbacks, 1)
        self.assertEqual(agent.fallback_count, 0)  # inner fallback caught it

    def test_budget_violations_counted_with_injected_clock(self):
        times = iter([0.0, 10.0])  # decision takes 10s > 0.1s budget

        class Instant:
            def plan(self, view, rng, budget_s=None):
                return [0]

        agent = MctsAgent(seed=1, deck=[101] * 60,
                          card_index=synthetic_card_index(),
                          clock=lambda: next(times))
        agent._planner = Instant()
        agent.act(main_view(2).raw)
        self.assertEqual(agent.budget_violations, 1)
        self.assertEqual(agent.move_times, [10.0])

    def test_eval_weights_override_builds_the_evaluator(self):
        # FABLE_CONFIG rides eval_weights through the constructor kwargs.
        agent = MctsAgent(seed=1, deck=[101] * 60,
                          card_index=synthetic_card_index(),
                          eval_weights={"deck_low": -0.2, "deck_low_at": 14})
        self.assertIsInstance(agent._evaluator, HeuristicEvaluator)
        self.assertEqual(agent._evaluator.weights["deck_low"], -0.2)
        self.assertEqual(agent._evaluator.weights["deck_low_at"], 14)


class _ScriptedBackend:
    """Deterministic engine double: stepping action [1] wins for player 0,
    any other root action loses. Engine responses are a pure function of the
    action, so any run-to-run variation could only come from agent-side
    randomness — which must all be seed-derived."""

    def __init__(self):
        self.next_sid = 0

    def begin(self, raw_obs, fills, manual_coin=True):
        self.next_sid += 1
        sel = SimpleNamespace(option=[SimpleNamespace(type=13, number=0)] * 3,
                              minCount=1, maxCount=1, context=0)
        obs = SimpleNamespace(
            select=sel,
            current=SimpleNamespace(result=-1, yourIndex=0, turn=1))
        return self.next_sid, obs

    def step(self, sid, action):
        self.next_sid += 1
        result = 0 if action == [1] else 1
        obs = SimpleNamespace(
            select=None,
            current=SimpleNamespace(result=result, yourIndex=0, turn=1))
        return self.next_sid, obs

    def release(self, sid):
        pass

    def end(self):
        pass


class TestAgentSideReproducibility(unittest.TestCase):
    """同一シード+同一局面→同一着手, scoped to agent-side randomness.

    The real search API consumes a non-injectable engine RNG (shuffle
    effects), so the reproducibility guarantee is specified with engine
    responses held fixed — every OTHER source of randomness (fill sampling,
    coin sampling, candidate generation, tie-break jitter) is exercised here
    and must be a deterministic function of the injected seed."""

    CFG = dict(n_worlds=3, max_iterations=16, time_budget_s=30.0,
               max_root_actions=3, max_child_actions=3)

    def _agent(self, seed):
        return MctsAgent(seed=seed, deck=[101] * 60,
                         card_index=synthetic_card_index(),
                         backend=_ScriptedBackend(), **self.CFG)

    def test_same_seed_same_observation_same_action(self):
        obs = main_view(3).raw
        actions = [self._agent(seed=7).act(obs) for _ in range(3)]
        self.assertEqual(actions[0], actions[1])
        self.assertEqual(actions[0], actions[2])

    def test_search_finds_the_scripted_win(self):
        for seed in (1, 2, 3):
            self.assertEqual(self._agent(seed).act(main_view(3).raw), [1])

    def test_repro_holds_across_a_decision_sequence(self):
        # Per-decision child streams: decision k must not depend on how
        # much randomness earlier decisions consumed beyond the stream name.
        obs_seq = [main_view(3).raw, main_view(2).raw, main_view(3).raw]
        a, b = self._agent(11), self._agent(11)
        self.assertEqual([a.act(o) for o in obs_seq],
                         [b.act(o) for o in obs_seq])


class TestProgressiveWidening(unittest.TestCase):
    """SOT-1864: progressive widening in _select_edge (opt-in, default OFF)."""

    def _planner(self, **overrides):
        return MctsPlanner(own_deck=[101] * 60,
                           config=PlannerConfig(**overrides),
                           backend=None, card_index=synthetic_card_index())

    def test_disabled_by_default_keeps_champion_selection(self):
        # PlannerConfig default must leave pw OFF so FABLE_CONFIG (which never
        # sets pw_enabled) is byte-identical to the champion's search.
        self.assertFalse(PlannerConfig().pw_enabled)
        # Two explored edges; the lower-prior edge has a decisive Q lead, so
        # PUCT (no widening) exploits it.
        node = SimpleNamespace(
            actor=0, priors=[0.6, 0.4],
            edges=[[[0], None, 10, 1.0], [[1], None, 10, 9.0]])
        best = self._planner()._select_edge(node, root_player=0)
        self.assertEqual(best[0], [1])  # high-Q, lower-prior edge

    def test_widening_blocks_low_prior_edges_until_visited(self):
        # Same node, but widening capped to the single top-prior edge
        # (k=ceil(0.2*sqrt(21))=1) hides the tempting low-prior edge entirely.
        node = SimpleNamespace(
            actor=0, priors=[0.6, 0.4],
            edges=[[[0], None, 10, 1.0], [[1], None, 10, 9.0]])
        best = self._planner(pw_enabled=True, pw_c=0.2, pw_alpha=0.5) \
            ._select_edge(node, root_player=0)
        self.assertEqual(best[0], [0])  # top-prior edge, low-prior one blocked

    def test_widening_unlocks_more_edges_as_visits_grow(self):
        # With enough node visits the cap exceeds the edge count, so the same
        # node considers every edge again (k=ceil(1.0*sqrt(101))>2).
        node = SimpleNamespace(
            actor=0, priors=[0.6, 0.4],
            edges=[[[0], None, 50, 5.0], [[1], None, 50, 45.0]])
        best = self._planner(pw_enabled=True, pw_c=1.0, pw_alpha=0.5) \
            ._select_edge(node, root_player=0)
        self.assertEqual(best[0], [1])  # widened set includes the high-Q edge


@unittest.skipUnless(HAS_ENGINE, "cabt engine (cg/) not available")
class TestDeepSearchOnEngine(unittest.TestCase):
    """SOT-1864: a depth>=2 + progressive-widening config plays a full engine
    match with zero rejects/fallbacks (the acceptance 'fault 0' requirement)."""

    DEEP_CFG = dict(n_worlds=2, max_iterations=40, time_budget_s=30.0,
                    rollout_turns=1, rollout_depth=20, max_root_actions=4,
                    max_child_actions=4, max_tree_depth=2,
                    pw_enabled=True, pw_c=1.0, pw_alpha=0.5)

    @staticmethod
    def _deck():
        with open(os.path.join(REPO, "deck.csv")) as f:
            return [int(x) for x in f.read().split("\n")[:60]]

    def test_depth2_widening_full_match_no_faults(self):
        os.chdir(REPO)
        from eval.bench import play_match
        a = MctsAgent(seed=1, deck=self._deck(), **self.DEEP_CFG)
        b = MctsAgent(seed=2, deck=self._deck(), **self.DEEP_CFG)
        result, decisions, reject, exception = play_match(a, b)
        self.assertIn(result, (0, 1, 2))
        self.assertFalse(reject)
        self.assertFalse(exception)
        self.assertGreater(decisions, 0)
        for agent in (a, b):
            self.assertEqual(agent.fallback_count, 0)
            self.assertEqual(agent.planner_fallbacks, 0)
            self.assertEqual(agent.degraded_count, 0)


@unittest.skipUnless(HAS_ENGINE, "cabt engine (cg/) not available")
class TestMctsOnEngine(unittest.TestCase):
    """One full engine match with a fast search config (test-sized)."""

    REPRO_CFG = dict(n_worlds=2, max_iterations=4, time_budget_s=30.0,
                     rollout_turns=1, rollout_depth=20, max_root_actions=4,
                     max_child_actions=4)

    def _agent(self, seed):
        return MctsAgent(seed=seed, deck=self._deck(), **self.REPRO_CFG)

    @staticmethod
    def _deck():
        with open(os.path.join(REPO, "deck.csv")) as f:
            return [int(x) for x in f.read().split("\n")[:60]]

    def test_full_match_no_rejects_no_fallbacks(self):
        os.chdir(REPO)
        from eval.bench import play_match
        a, b = self._agent(1), self._agent(2)
        result, decisions, reject, exception = play_match(a, b)
        self.assertIn(result, (0, 1, 2))
        self.assertFalse(reject)
        self.assertFalse(exception)
        self.assertGreater(decisions, 0)
        for agent in (a, b):
            self.assertEqual(agent.fallback_count, 0)
            self.assertEqual(agent.planner_fallbacks, 0)


if __name__ == "__main__":
    unittest.main()
