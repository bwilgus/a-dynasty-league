"""Backfills pick_transactions for the Sleeper era (2024-present).

sleeper_to_db.py's transaction ingestion originally only read adds/drops from
Sleeper's transaction payload, ignoring the payload's separate "draft_picks"
list (present on trades that include a future pick). This script re-queries
just the transactions endpoint -- not a full league resync -- for every
Sleeper season already in the DB and fills in the pick side of those trades.

Safe to rerun (INSERT OR REPLACE keyed on trans_id/pick_season/pick_round/
original_team_id).
"""

import sqlite3

from sleeper_to_db import DB_PATH, SLEEPER_BASE_URL, fetch_json
from helper_defs import get_sleeper_league_id
import config

PICK_TRANSACTIONS_DDL = """
    CREATE TABLE IF NOT EXISTS pick_transactions (
        trans_id TEXT NOT NULL,
        trade_year INTEGER NOT NULL,
        trade_week INTEGER NOT NULL,
        pick_season INTEGER NOT NULL,
        pick_round INTEGER NOT NULL,
        original_team_id INTEGER NOT NULL,
        from_team_id INTEGER NOT NULL,
        to_team_id INTEGER NOT NULL,
        PRIMARY KEY (trans_id, pick_season, pick_round, original_team_id)
    )
"""


def get_sleeper_league_ids(db_path: str = DB_PATH):
    """Walks forward from config.sleeper_league_id to find every Sleeper season's league_id."""
    league_id = config.sleeper_league_id
    league_ids = []
    while league_id:
        info = fetch_json(f"{SLEEPER_BASE_URL}/league/{league_id}")
        season = int(info.get("season"))
        league_ids.append((season, league_id))
        next_id = get_sleeper_league_id(league_id, config.sleeper_user_id, season + 1)
        league_id = next_id
    return league_ids


def backfill_pick_transactions(db_path: str = DB_PATH):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(PICK_TRANSACTIONS_DDL)

    league_ids = get_sleeper_league_ids(db_path)
    print(f"Found Sleeper seasons: {league_ids}")

    pick_transactions_to_insert = []
    for year, league_id in league_ids:
        for week in range(1, 19):
            weekly_trans = fetch_json(f"{SLEEPER_BASE_URL}/league/{league_id}/transactions/{week}")
            if not weekly_trans:
                continue

            for t in weekly_trans:
                if t.get("status") != "complete":
                    continue

                trans_id = str(t.get("transaction_id"))
                for pick in (t.get("draft_picks") or []):
                    pick_transactions_to_insert.append((
                        trans_id,
                        year,
                        week,
                        int(pick.get("season")),
                        pick.get("round"),
                        pick.get("roster_id"),
                        pick.get("previous_owner_id"),
                        pick.get("owner_id"),
                    ))

    cur.executemany(
        """
        INSERT OR REPLACE INTO pick_transactions (
            trans_id, trade_year, trade_week, pick_season, pick_round,
            original_team_id, from_team_id, to_team_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        pick_transactions_to_insert,
    )
    conn.commit()
    print(f"Stored {len(pick_transactions_to_insert)} pick trade records.")
    conn.close()


if __name__ == "__main__":
    backfill_pick_transactions()
