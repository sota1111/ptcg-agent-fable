"""Value-net pipeline tests (SOT-1837).

Covers the acceptance-criterion "一致テスト" (train-forward == exported pure-
Python inference) plus feature-extraction parity across the two observation
shapes, terminal handling, the feature-version guard, and the MctsAgent wiring.
Engine-independent (no cabt) so it runs in the standard unittest suite.
"""
import os
import random
import tempfile
import unittest
from types import SimpleNamespace

from agents.evaluator import HeuristicEvaluator, make_evaluator
from agents.learned_value import LearnedEvaluator
from agents.mcts_agent import MctsAgent
from agents.value_features import (FEATURE_DIM, FEATURE_NAMES, FEATURE_VERSION,
                                   extract)
from agents.value_net import ValueNet

from tests import support


def _obj_pokemon(hp, energies):
    return SimpleNamespace(hp=hp, energies=list(energies))


def _obj_side(prize_left, active, bench, hand, deck):
    return SimpleNamespace(
        prize=[None] * prize_left, active=active, bench=bench,
        handCount=hand, deckCount=deck)


class TestFeatures(unittest.TestCase):
    def test_dim_matches_names(self):
        self.assertEqual(FEATURE_DIM, len(FEATURE_NAMES))
        self.assertGreater(FEATURE_DIM, 0)

    def test_extract_is_deterministic_and_bounded(self):
        obs = support.observation(
            support.select([{"type": 14}]),
            me=support.player(active=[support.pokemon(101, hp=120,
                                                      energies=[1, 1])],
                              deck_count=40, hand_count=5, prize=3),
            opp=support.player(active=[support.pokemon(102, hp=60)],
                               deck_count=44, hand_count=6, prize=5))
        f1 = extract(obs, 0)
        f2 = extract(obs, 0)
        self.assertEqual(f1, f2)
        self.assertEqual(len(f1), FEATURE_DIM)
        # normalized features stay in a sane band (no divide surprises)
        self.assertTrue(all(-2.0 <= v <= 2.0 for v in f1))

    def test_dict_and_object_observations_agree(self):
        """The raw battle dict (self-play) and the engine dataclass (inference)
        must produce identical features for the same logical board."""
        obs_dict = support.observation(
            support.select([{"type": 14}]),
            me=support.player(
                active=[support.pokemon(101, hp=120, energies=[1, 1])],
                bench=[support.pokemon(103, hp=60, energies=[1])],
                deck_count=38, hand_count=4, prize=2),
            opp=support.player(active=[support.pokemon(102, hp=70,
                                                       energies=[1])],
                               deck_count=50, hand_count=7, prize=6))
        me_obj = _obj_side(2, [_obj_pokemon(120, [1, 1])],
                           [_obj_pokemon(60, [1])], 4, 38)
        opp_obj = _obj_side(6, [_obj_pokemon(70, [1])], [], 7, 50)
        obs_obj = SimpleNamespace(current=SimpleNamespace(
            yourIndex=0, players=[me_obj, opp_obj]))
        self.assertEqual(extract(obs_dict, 0), extract(obs_obj, 0))

    def test_empty_observation_is_neutral(self):
        self.assertEqual(extract(SimpleNamespace(current=None), 0),
                         [0.0] * FEATURE_DIM)


class TestValueNet(unittest.TestCase):
    def test_forward_in_unit_interval(self):
        net = ValueNet.init(8, random.Random(0))
        y = net.forward([0.1] * FEATURE_DIM)
        self.assertTrue(0.0 <= y <= 1.0)

    def test_roundtrip_is_exact(self):
        net = ValueNet.init(12, random.Random(3))
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "w.json")
            net.save(p)
            net2 = ValueNet.load(p)
        xs = [[random.Random(i).uniform(-1, 1) for _ in range(FEATURE_DIM)]
              for i in range(20)]
        self.assertEqual([net.forward(x) for x in xs],
                         [net2.forward(x) for x in xs])

    def test_training_separates_two_labels(self):
        net = ValueNet.init(8, random.Random(1))
        a = [0.9] * FEATURE_DIM
        b = [-0.9] * FEATURE_DIM
        for _ in range(200):
            net.train_epoch([a, b], [1.0, 0.0], lr=0.3)
        self.assertGreater(net.forward(a), net.forward(b))


class TestConsistency(unittest.TestCase):
    def test_train_forward_equals_reloaded_inference(self):
        """AC1 一致テスト: the exported JSON, reloaded through the pure-Python
        inference path, reproduces the trained net's predictions exactly."""
        rng = random.Random(7)
        net = ValueNet.init(16, rng)
        X = [[rng.uniform(-1, 1) for _ in range(FEATURE_DIM)]
             for _ in range(120)]
        y = [1.0 if sum(x) > 0 else 0.0 for x in X]
        for _ in range(30):
            net.train_epoch(X, y, lr=0.25)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "value.json")
            net.save(p)
            reloaded = ValueNet.load(p)
        gap = max(abs(net.forward(x) - reloaded.forward(x)) for x in X)
        self.assertLessEqual(gap, 1e-9)


class TestLearnedEvaluator(unittest.TestCase):
    def _net(self):
        return ValueNet.init(8, random.Random(0))

    def test_terminal_states_are_exact(self):
        ev = LearnedEvaluator(self._net())
        win = SimpleNamespace(current=SimpleNamespace(result=0))
        loss = SimpleNamespace(current=SimpleNamespace(result=1))
        draw = SimpleNamespace(current=SimpleNamespace(result=2))
        self.assertEqual(ev.evaluate(win, 0), 1.0)
        self.assertEqual(ev.evaluate(loss, 0), 0.0)
        self.assertEqual(ev.evaluate(draw, 0), 0.5)

    def test_matches_heuristic_terminal_contract(self):
        heur = HeuristicEvaluator()
        learned = LearnedEvaluator(self._net())
        for result, root in ((0, 0), (1, 0), (0, 1), (2, 0)):
            obs = SimpleNamespace(current=SimpleNamespace(result=result))
            self.assertEqual(heur.evaluate(obs, root),
                             learned.evaluate(obs, root))

    def test_non_terminal_uses_network_in_range(self):
        ev = LearnedEvaluator(self._net())
        obs = SimpleNamespace(current=SimpleNamespace(
            result=-1, yourIndex=0,
            players=[_obj_side(3, [_obj_pokemon(120, [1, 1])], [], 5, 40),
                     _obj_side(5, [_obj_pokemon(70, [1])], [], 6, 44)]))
        v = ev.evaluate(obs, 0)
        self.assertTrue(0.0 <= v <= 1.0)

    def test_feature_version_mismatch_raises(self):
        net = self._net()
        net.feature_version = FEATURE_VERSION + 99
        with self.assertRaises(ValueError):
            LearnedEvaluator(net)


class TestWiring(unittest.TestCase):
    def _weights_path(self, d):
        net = ValueNet.init(8, random.Random(0))
        p = os.path.join(d, "value.json")
        net.save(p)
        return p

    def test_make_evaluator_learned_spec(self):
        with tempfile.TemporaryDirectory() as d:
            ev = make_evaluator({"learned": self._weights_path(d)})
        self.assertIsInstance(ev, LearnedEvaluator)

    def test_make_evaluator_unknown_spec_raises(self):
        with self.assertRaises(ValueError):
            make_evaluator({"nonsense": 1})

    def test_mcts_agent_value_net_kwarg_builds_learned_evaluator(self):
        deck = [101] * 60
        with tempfile.TemporaryDirectory() as d:
            agent = MctsAgent(seed=1, deck=deck,
                              value_net=self._weights_path(d))
        self.assertIsInstance(agent._evaluator, LearnedEvaluator)

    def test_default_mcts_agent_stays_heuristic(self):
        """Champion path must be untouched: no value_net -> heuristic (or None,
        which the planner defaults to HeuristicEvaluator)."""
        agent = MctsAgent(seed=1, deck=[101] * 60)
        self.assertNotIsInstance(agent._evaluator, LearnedEvaluator)


if __name__ == "__main__":
    unittest.main()
