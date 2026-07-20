"""PTCG battle agents for fable — baseline scaffold (SOT-1793).

Adapted from the proven ptcg-agent-matsu SOT-1671 baseline layers:

- observation.py : [1] Observation Adapter (raw obs dict -> information-set View)
- actions.py     : [2] Action Enumerator (obs.select is the single source of truth)
- random_agent.py / greedy_agent.py : baseline policies
- rng.py         : single externally-seeded RNG (no global random)
- cards.py       : card-attribute feature index (unknown IDs -> defaults)

The fable algorithm proper (SOT-1795) plugs in as further AGENT_TYPES.
"""
from .base import BaseAgent
from .greedy_agent import GreedyAgent
from .random_agent import RandomAgent
from .rng import Rng

AGENT_TYPES = {
    "random": RandomAgent,
    "greedy": GreedyAgent,
}


def make_agent(name: str, seed: int, deck=None, **kwargs) -> BaseAgent:
    """Factory: agent name -> instance. Raises KeyError for unknown names."""
    return AGENT_TYPES[name](seed=seed, deck=deck, **kwargs)
