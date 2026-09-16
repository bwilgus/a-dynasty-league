"""Rebuilds the `players` table as a player-id-keyed name/position lookup.

`lineups.player_name` is whatever the source platform (ESPN or Sleeper) called
a player *in that season* -- never normalized -- so the same real person can
have multiple spellings across years even though `normalize_player_ids.py`
has already unified `player_id` to Sleeper's id space (e.g. player_id 956
appears as both "Mark Ingram II" and "Mark Ingram"). This script resolves
that by taking, per `player_id`, the row from that player's most recent
(year, week) appearance in `lineups` -- one name/position per id, derived
entirely from what's already in the DB (no external API calls).

For players whose last appearance predates the Sleeper era (2024+), the
chosen name is just the last ESPN-era spelling, not necessarily Sleeper's
canonical spelling -- it's internally consistent (one name per id), not
guaranteed authoritative.

Safe to rerun any time `lineups` changes.
"""

import sqlite3

from sleeper_to_db import DB_PATH


def build_players(db_path: str = DB_PATH):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute(
        """
        SELECT player_id, player_name, player_position, year, week,
               espn_player_id, player_id_source
        FROM (
            SELECT
                player_id, player_name, player_position, year, week,
                espn_player_id, player_id_source,
                ROW_NUMBER() OVER (
                    PARTITION BY player_id
                    ORDER BY year DESC, week DESC
                ) AS rn
            FROM lineups
            WHERE player_id IS NOT NULL
        )
        WHERE rn = 1
        """
    )
    players_to_insert = cur.fetchall()

    cur.execute("DROP TABLE IF EXISTS players")
    cur.execute(
        """
        -- BIGINT, not INTEGER PRIMARY KEY: D/ST rows store player_id as a team
        -- abbreviation (e.g. 'LAC'), same as lineups.player_id itself.
        CREATE TABLE players (
            player_id BIGINT PRIMARY KEY,
            player_name TEXT NOT NULL,
            player_position TEXT,
            last_year BIGINT NOT NULL,
            last_week BIGINT NOT NULL,
            espn_player_id TEXT,
            player_id_source TEXT
        )
        """
    )
    cur.executemany(
        """
        INSERT INTO players (
            player_id, player_name, player_position, last_year, last_week,
            espn_player_id, player_id_source
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        players_to_insert,
    )
    conn.commit()
    print(f"Rebuilt players with {len(players_to_insert)} rows.")
    conn.close()


if __name__ == "__main__":
    build_players()
