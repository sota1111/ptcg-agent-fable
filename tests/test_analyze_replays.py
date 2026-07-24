"""Unit tests for the SOT-1894 step-level replay analyzer.

Builds a tiny synthetic kaggle-environments replay (2 agents, a handful of
steps) and checks seat resolution, prize-race decisive-break detection,
multi-prize KO tagging and decision extraction — no network, no real replays.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis import analyze_replays as ar


def _obs(turn, my_prizes_left, opp_prizes_left, your_index=0, select=None,
         logs=(), energy_attached=True):
    return {
        "current": {
            "yourIndex": your_index,
            "turn": turn,
            "firstPlayer": 0,
            "result": -1,
            "energyAttached": energy_attached,
            "players": [
                {"active": [], "bench": [], "deckCount": 30, "handCount": 5,
                 "prize": [None] * (my_prizes_left if your_index == 0
                                    else opp_prizes_left)},
                {"active": [], "bench": [], "deckCount": 30, "handCount": 5,
                 "prize": [None] * (opp_prizes_left if your_index == 0
                                    else my_prizes_left)},
            ],
        },
        "select": select,
        "logs": list(logs),
    }


def _agent(obs, status="INACTIVE", action=None, reward=0):
    return {"observation": obs, "status": status,
            "action": action or [], "reward": reward}


MAIN_SELECT = {
    "type": 2, "context": 0, "minCount": 1, "maxCount": 1,
    "option": [{"type": 13, "attackId": 1047},  # ATTACK
               {"type": 14}],                    # END
}


def synthetic_replay():
    """We (seat 1) draw even to turn 3, concede a 2-prize KO, never recover."""
    steps = [
        # t0: setup, nobody decided yet
        [_agent(_obs(0, 6, 6, 0)), _agent(_obs(0, 6, 6, 1))],
        # t1: even race; we must act on a MAIN select (answered at t2)
        [_agent(_obs(1, 6, 6, 0)),
         _agent(_obs(1, 6, 6, 1, select=MAIN_SELECT), status="ACTIVE")],
        # t2: we chose END (index 1) although ATTACK was available
        [_agent(_obs(2, 6, 6, 0)),
         _agent(_obs(2, 6, 6, 1), action=[1])],
        # t3: still even — this is the last lead>=0 step (decisive break)
        [_agent(_obs(3, 6, 6, 0)), _agent(_obs(3, 6, 6, 1))],
        # t4: opponent takes a 2-prize KO (their prizes-left 6 -> 4)
        [_agent(_obs(4, 4, 6, 0)), _agent(_obs(4, 6, 4, 1))],
        # t5: terminal — opponent finishes prizes (reason 1)
        [_agent(_obs(5, 0, 6, 0), status="DONE", reward=1),
         _agent(_obs(5, 6, 0, 1, logs=[{"type": 23, "reason": 1}]),
                status="DONE", reward=-1)],
    ]
    return {"info": {"TeamNames": ["someone", "sota1111"]}, "steps": steps}


class AnalyzeReplayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.replay_dir_orig = ar.REPLAY_DIR
        ar.REPLAY_DIR = self.tmp.name
        with open(os.path.join(self.tmp.name, "42.json"), "w") as fh:
            json.dump(synthetic_replay(), fh)
        self.row = {"episode_id": 42, "our_index": 0,  # wrong on purpose:
                    # TeamNames must override it to seat 1
                    "opp_team_id": 7, "opp_rating": 640.0, "our_rating": 600.0}

    def tearDown(self):
        ar.REPLAY_DIR = self.replay_dir_orig
        self.tmp.cleanup()

    def test_episode_analysis(self):
        r = ar.analyze_episode(self.row, names={7: "rival"})
        self.assertIsNotNone(r)
        self.assertEqual(r["opponent"], "rival")
        self.assertEqual(r["reason"], "prize_race_lost")
        # seat resolved via TeamNames -> we never took a prize, they took 6
        self.assertEqual(r["final_lead"], -6)
        self.assertTrue(r["never_led"])
        self.assertFalse(r["late_reversal"])
        # decisive break = turn 3 (last even step before the permanent deficit)
        self.assertEqual(r["decisive_turn"], 3)
        # the turn-4 double KO is tagged (plus the compressed terminal jump)
        self.assertEqual(len(r["multi_prize_kos_conceded"]), 2)
        self.assertEqual(r["multi_prize_kos_conceded"][0]["prizes"], 2)
        # decision extraction: MAIN select answered with END while ATTACK open
        mains = [d for d in r["decisions_at_break"] if d["context"] == "MAIN"]
        self.assertEqual(len(mains), 0)  # turn 1 is outside break window 2..4
        self.assertEqual(r["end_with_attack_available"], 0)

    def test_decision_extraction_window(self):
        with open(os.path.join(self.tmp.name, "42.json")) as fh:
            steps = json.load(fh)["steps"]
        decisions = ar._decisions(steps, our_index=1, turn_lo=1, turn_hi=1)
        self.assertEqual(len(decisions), 1)
        d = decisions[0]
        self.assertEqual(d["context"], ar._ctx_name(0))
        self.assertEqual(d["chosen_types"], [ar._opt_name(14)])
        self.assertTrue(d["attack_available"])
        self.assertTrue(d["chose_end"])

    def test_aggregate(self):
        r = ar.analyze_episode(self.row, names={})
        agg = ar.aggregate([r])
        self.assertEqual(agg["losses_analyzed"], 1)
        self.assertEqual(agg["loss_reasons"], {"prize_race_lost": 1})
        self.assertEqual(agg["multi_prize_ko_conceded_rate"], 1.0)
        self.assertEqual(agg["never_led_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()
