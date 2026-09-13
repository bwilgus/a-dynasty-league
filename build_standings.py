"""Rebuilds the `standings` table as weekly cumulative standings.

Previously `standings` held one row per (year, team_id) with season-final
totals, and only 4 of 12 seasons were ever populated. This script recreates
it with one row per (year, week, team_id) — cumulative wins/losses/ties/
points_for/points_against/max_pf through that week — derived entirely from
the existing `games` and `manager_efficiency` tables.

Standings are regular-season-only (matches the league's existing convention):
each year only gets rows for weeks 1..N, where N is that year's last
regular-season week (games.is_playoff = 0). Playoff results don't affect
standings.

max_pf (cumulative optimal/"max possible" points) is NULL for year < 2018,
since there's no usable bench/lineup data before then (2014-2016 have no
lineups at all, and 2017's lineup slot data is corrupted).
"""

import sqlite3
from collections import defaultdict

from sleeper_to_db import DB_PATH


def build_standings(db_path: str = DB_PATH):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Regular-season length per year.
    cur.execute("SELECT year, MAX(week) FROM games WHERE is_playoff = 0 GROUP BY year")
    reg_season_length = dict(cur.fetchall())

    # Team roster per year.
    cur.execute("SELECT DISTINCT year, team_id FROM teams ORDER BY year, team_id")
    teams_by_year = defaultdict(list)
    for year, team_id in cur.fetchall():
        teams_by_year[year].append(team_id)

    # Regular-season game stats, keyed by (year, week, team_id).
    cur.execute(
        """
        SELECT year, week, team_id, is_win, is_loss, is_tie, team_score, opponent_team_id
        FROM games
        WHERE is_playoff = 0
        """
    )
    game_stats = {}
    for year, week, team_id, is_win, is_loss, is_tie, team_score, opponent_team_id in cur.fetchall():
        game_stats[(year, week, team_id)] = (is_win, is_loss, is_tie, team_score, opponent_team_id)

    # Weekly optimal ("max possible") score, keyed by (year, week, team_id). Only 2018+.
    cur.execute("SELECT year, week, team_id, optimal_score FROM manager_efficiency WHERE year >= 2018")
    optimal_scores = {}
    for year, week, team_id, optimal_score in cur.fetchall():
        optimal_scores[(year, week, team_id)] = optimal_score

    standings_to_insert = []
    for year, team_ids in teams_by_year.items():
        last_week = reg_season_length.get(year)
        if last_week is None:
            continue

        for team_id in team_ids:
            wins = losses = ties = 0
            points_for = points_against = 0.0
            max_pf = 0.0 if year >= 2018 else None

            for week in range(1, last_week + 1):
                stats = game_stats.get((year, week, team_id))
                if stats:
                    is_win, is_loss, is_tie, team_score, opponent_team_id = stats
                    wins += is_win
                    losses += is_loss
                    ties += is_tie
                    points_for += team_score
                    opp_stats = game_stats.get((year, week, opponent_team_id))
                    if opp_stats:
                        points_against += opp_stats[3]

                if year >= 2018:
                    max_pf += optimal_scores.get((year, week, team_id), 0.0)

                standings_to_insert.append((
                    year,
                    week,
                    team_id,
                    wins,
                    losses,
                    ties,
                    round(points_for, 2),
                    round(points_against, 2),
                    round(max_pf, 2) if max_pf is not None else None,
                ))

    cur.execute("DROP TABLE IF EXISTS standings")
    cur.execute(
        """
        CREATE TABLE standings (
            year INTEGER NOT NULL,
            week INTEGER NOT NULL,
            team_id INTEGER NOT NULL,
            wins INTEGER NOT NULL,
            losses INTEGER NOT NULL,
            ties INTEGER NOT NULL,
            points_for REAL NOT NULL,
            points_against REAL NOT NULL,
            max_pf REAL,
            PRIMARY KEY (year, week, team_id)
        )
        """
    )
    cur.executemany(
        """
        INSERT INTO standings (
            year, week, team_id, wins, losses, ties, points_for, points_against, max_pf
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        standings_to_insert,
    )
    conn.commit()
    print(f"Rebuilt standings with {len(standings_to_insert)} rows across {len(teams_by_year)} years.")
    conn.close()


if __name__ == "__main__":
    build_standings()
