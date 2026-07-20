"""fable submission entry point — provisional (SOT-1793).

Starting point is the official sample submission: return the 60-card deck on
the initial call, then a random LEGAL selection each turn. Hardened only to
respect minCount as well as maxCount and to run from either the repo root or
the Kaggle agent directory. The fable algorithm proper replaces the policy in
a later issue (SOT-1795).
"""
import os
import random

from cg.api import Observation, to_observation_class


def read_deck_csv() -> list:
    """Read deck.csv (repo root locally, /kaggle_simulations/agent/ on Kaggle).

    Returns:
        list[int]: A list of 60 card IDs in the deck.
    """
    file_path = "deck.csv"
    if not os.path.exists(file_path):
        file_path = "/kaggle_simulations/agent/" + file_path
    with open(file_path, "r") as file:
        csv = file.read().split("\n")
    return [int(csv[i]) for i in range(60)]


def agent(obs_dict: dict) -> list:
    """PTCG agent: initial call returns the deck, then a legal random action.

    Each element in the returned list must be >= 0 and < len(obs.select.option).
    The list length must be between obs.select.minCount and obs.select.maxCount
    (inclusive), with no duplicate elements.
    """
    obs: Observation = to_observation_class(obs_dict)
    if obs.select is None:
        # Initial selection: return the 60-card deck.
        return read_deck_csv()

    n = len(obs.select.option)
    hi = min(max(obs.select.maxCount, 0), n)
    lo = min(max(obs.select.minCount, 0), hi)
    return sorted(random.sample(range(n), random.randint(lo, hi)))
