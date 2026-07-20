# Deck Selection Report (SOT-1794)

**Selected deck: `decks/candidates/26_stw_champion.csv`** — the champion
`deck.csv` shared by ptcg-agent-matsu / take / ume (identical md5 across all
three repos). It is byte-identical to the `deck.csv` this repo inherited from
the SOT-1793 baseline, so the file content does not change; this report is the
measurement that backs it.

- Date: 2026-07-20
- Agent used for all measurements: `greedy` on both sides (the strongest
  fast agent available in this repo; the MCTS agent is SOT-1795 and was not
  available at selection time — see Limitations)
- Total games: 25,800 (screen 14,040 + confirm 11,760), **faults 0**
  (engine rejects 0, agent exceptions 0, random-legal fallbacks 0)
- Raw artifacts: `docs/deck_selection/screen.json`,
  `docs/deck_selection/confirm.json` (each includes per-pair tallies for
  audit)

## Candidates (26)

- `01`–`25`: the 25 tournament decks (Special Event Turin + NAIC 2026,
  provenance in `decks/candidates/manifest.json`), verified identical
  (md5) to `decks/initial/` in matsu / take / ume.
- `26_stw_champion`: the matsu/take/ume champion `deck.csv`. All three
  repos carry the same file, and it matches none of the 25 tournament
  decks, so the "3 champion decks" collapse to this single extra candidate.
- Validator: all 26 candidates (plus root `deck.csv`) PASS
  `eval/deck_validator.py` (60 cards / ≤4 copies by name with Basic Energy
  exempt / ≥1 Basic Pokémon / ≤1 ACE SPEC).

## Method

Two-stage screen → confirm (`eval/compare_decks.py`), judged by the
**aggregate Wilson 95% CI only** — per-pair small-N win rates are noise
(SOT-1707 lesson: per-deck N=80 has ±0.11 CI) and are recorded only as audit
data. Both stages play every unordered pair once, **mirrors included**, with
the first player alternating every game; per-game agent seeds derive from the
stage seed (the engine's internal RNG is not externally seedable, so
independence across stages is at the agent-decision level plus fresh engine
shuffles). A deck's aggregate = all its games vs the whole field (mirror games
counted once, from the side-A agent's perspective).

1. **Screen** — all 26 candidates, 351 pairs × 40 games = 14,040 games,
   seed 1794001 (~1,035 games per deck).
2. **Confirm** — top-4 finalists only, independent seed 1794100,
   98 pairs × 120 games = 11,760 games; each finalist plays the full
   26-deck field (~3,100 games per finalist) so the statistic stays
   comparable to screening.

## Screen results (N=40/pair, ~1,035 games per deck)

Win rate excludes draws; `[lo, hi]` is the Wilson 95% CI. Unfinished: 0
everywhere.

| # | Deck | Win rate | Wilson 95% | n | draws |
|---|------|----------|------------|---|-------|
| 1 | 26_stw_champion | 0.8198 | [0.7953, 0.8420] | 1038 | 2 |
| 2 | 16_crustle_mysterious_rock_inn | 0.7691 | [0.7424, 0.7937] | 1035 | 5 |
| 3 | 15_marnie_s_grimmsnarl_ex | 0.7112 | [0.6828, 0.7381] | 1032 | 8 |
| 4 | 20_cynthia_s_garchomp_ex | 0.6744 | [0.6452, 0.7024] | 1029 | 11 |
| 5 | 14_mega_lucario_ex | 0.6089 | [0.5788, 0.6382] | 1033 | 7 |
| 6 | 01_dragapult | 0.6006 | [0.5704, 0.6300] | 1034 | 6 |
| 7 | 02_raging_bolt_ogerpon | 0.5874 | [0.5572, 0.6171] | 1035 | 5 |
| 8 | 11_lillie_s_clefairy | 0.5844 | [0.5541, 0.6140] | 1037 | 3 |
| 9 | 21_lillie_s_clefairy_ex_naic_champion | 0.5815 | [0.5512, 0.6112] | 1037 | 3 |
| 10 | 03_dragapult_blaziken | 0.5800 | [0.5496, 0.6098] | 1031 | 9 |
| 11 | 17_rocket_s_mewtwo_ex | 0.5667 | [0.5362, 0.5967] | 1027 | 13 |
| 12 | 23_slowking_naic_4th | 0.5488 | [0.5172, 0.5799] | 964 | 76 |
| 13 | 08_ogerpon_box | 0.5382 | [0.5078, 0.5684] | 1033 | 7 |
| 14 | 25_mega_lopunny_ex | 0.5261 | [0.4956, 0.5564] | 1034 | 6 |
| 15 | 05_dragapult_dudunsparce | 0.5184 | [0.4879, 0.5488] | 1032 | 8 |
| 16 | 10_hop_s_trevenant | 0.5145 | [0.4840, 0.5449] | 1032 | 8 |
| 17 | 09_slowking | 0.4995 | [0.4678, 0.5312] | 951 | 89 |
| 18 | 04_dragapult_dusknoir | 0.4985 | [0.4681, 0.5290] | 1033 | 7 |
| 19 | 19_ethan_s_typhlosion | 0.4679 | [0.4376, 0.4985] | 1028 | 12 |
| 20 | 22_dragapult_ex_naic_2nd | 0.4492 | [0.4191, 0.4796] | 1033 | 7 |
| 21 | 06_hydrapple | 0.4058 | [0.3762, 0.4361] | 1030 | 10 |
| 22 | 13_festival_lead | 0.3259 | [0.2980, 0.3551] | 1034 | 6 |
| 23 | 18_rocket_s_honchkrow | 0.2015 | [0.1782, 0.2270] | 1037 | 3 |
| 24 | 12_alakazam_dudunsparce | 0.1942 | [0.1713, 0.2194] | 1035 | 5 |
| 25 | 24_n_s_zoroark_ex_naic_10th | 0.1333 | [0.1140, 0.1554] | 1035 | 5 |
| 26 | 07_n_s_zoroark_n | 0.0992 | [0.0825, 0.1189] | 1038 | 2 |

### 足切り (screen cut)

Finalists = **top 4**. The cut line is CI-based: #4 garchomp's lower bound
(0.6452) clears #5 mega_lucario's upper bound (0.6382), so ranks 1–4 form a
CI-separated leading group and every deck from rank 5 down is separated from
the group's tail. Ranks 5–26 were eliminated. (#1's lower bound 0.7953
already cleared #2's upper bound 0.7937 at screen, but only by 0.0016 —
hence the confirm stage.)

## Confirm results (top-4, independent seed, N=120/pair, ~3,100 games per finalist)

| # | Deck | Win rate | Wilson 95% | n | draws |
|---|------|----------|------------|---|-------|
| 1 | 26_stw_champion | 0.8165 | [0.8025, 0.8297] | 3112 | 8 |
| 2 | 16_crustle_mysterious_rock_inn | 0.7646 | [0.7494, 0.7792] | 3110 | 10 |
| 3 | 15_marnie_s_grimmsnarl_ex | 0.7171 | [0.7010, 0.7327] | 3097 | 23 |
| 4 | 20_cynthia_s_garchomp_ex | 0.6555 | [0.6386, 0.6720] | 3097 | 23 |

### 順位確定条件

All four adjacent CIs are pairwise disjoint:

- #1 lo 0.8025 > #2 hi 0.7792 (gap 0.0233)
- #2 lo 0.7494 > #3 hi 0.7327 (gap 0.0167)
- #3 lo 0.7010 > #4 hi 0.6720 (gap 0.0290)

The confirm ranking matches the screen ranking, and `26_stw_champion` is
CI-separated from the runner-up in both independent measurements →
**selection is confirmed, no tie-break needed**.

## Decision

`deck.csv` = `26_stw_champion` (validator PASS). The repo's existing
`deck.csv` already had this content (inherited from the SOT-1793 baseline),
so the selection ratifies it with measurement rather than changing it.

## Limitations

- The proxy agent is `greedy`; deck strength under the SOT-1795 MCTS agent
  may differ. If SOT-1796's improvement cycle shows the champion underperforms
  under MCTS, re-run `eval/compare_decks.py` with `--agent <new-agent>` —
  the protocol is agent-agnostic.
- Mirror games are included per the issue spec; they pull every deck's
  aggregate toward 0.5 by the same 1/26 weight, compressing but not
  reordering differences.
