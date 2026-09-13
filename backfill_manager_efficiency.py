"""Backfills manager_efficiency for years whose lineup data already lives in
dynasty_data.db (ESPN seasons), using the same optimal-lineup logic as
sleeper_to_db.py, applied to the locally stored lineups instead of the live
Sleeper API.

2017 is intentionally excluded: its player_slot_position values are corrupted
(every player is recorded as slot "QB"), so an optimal lineup can't be derived
from it until the ESPN 2017 scrape is fixed.
"""

import sqlite3
from collections import defaultdict

from sleeper_to_db import DB_PATH, FLEX_ELIGIBILITY, calculate_optimal_score

BENCH_SLOTS = {"BE", "BN", "IR", "TAXI"}

# Legacy ESPN flex slot name -> same eligibility as Sleeper's "FLEX"
FLEX_ELIGIBILITY.setdefault("RB/WR/TE", FLEX_ELIGIBILITY["FLEX"])


def backfill(db_path: str = DB_PATH, years=range(2018, 2024)):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    years_clause = ",".join(str(y) for y in years)
    cur.execute(
        f"""
        SELECT year, week, team_id, player_id, player_position, player_slot_position, player_score
        FROM lineups
        WHERE year IN ({years_clause})
        ORDER BY year, week, team_id
        """
    )

    rosters = defaultdict(list)
    for year, week, team_id, player_id, position, slot, score in cur.fetchall():
        rosters[(year, week, team_id)].append((player_id, position, slot, score))

    efficiency_to_insert = []
    for (year, week, team_id), rows in rosters.items():
        active_slots = [slot for _, _, slot, _ in rows if slot not in BENCH_SLOTS]
        player_scores = {str(pid): score for pid, _, _, score in rows}
        player_positions = {str(pid): pos for pid, pos, _, _ in rows}

        optimal_score = calculate_optimal_score(active_slots, player_scores, player_positions)
        actual_score = round(sum(score for _, _, slot, score in rows if slot not in BENCH_SLOTS), 2)
        efficiency_pct = (
            round((actual_score / optimal_score) * 100.0, 2)
            if optimal_score > 0
            else (100.0 if actual_score == 0 else 0.0)
        )
        efficiency_to_insert.append((year, week, team_id, actual_score, optimal_score, efficiency_pct))

    cur.executemany(
        """
        INSERT OR REPLACE INTO manager_efficiency (
            year, week, team_id, actual_score, optimal_score, efficiency_pct
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        efficiency_to_insert,
    )
    conn.commit()
    print(f"Inserted/updated {len(efficiency_to_insert)} manager_efficiency rows for years {list(years)}.")
    conn.close()


if __name__ == "__main__":
    backfill()
