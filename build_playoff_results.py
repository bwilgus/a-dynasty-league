"""Builds `playoff_results` and `champions` from the existing `games` table.

This league's playoff bracket is single-elimination on the championship path
only (confirmed 2014-2025): a "Quarterfinal" round with no byes, then a
"Semifinal" round (the 2 quarterfinal winners plus 2 teams that got a bye),
then a 2-team "Championship". Non-viable/consolation games are intentionally
not tracked anywhere in this league's data, so playoff_results only ever
has 3 rounds per year.

`champions` is a small summary derived from playoff_results' final round.
"""

import sqlite3
from collections import defaultdict

from sleeper_to_db import DB_PATH

ROUND_NAMES = {1: "Quarterfinal", 2: "Semifinal", 3: "Championship"}


def build_playoff_results(db_path: str = DB_PATH):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute(
        """
        SELECT year, week, team_id, opponent_team_id, team_score, is_win, is_loss, is_tie
        FROM games
        WHERE is_playoff = 1
        ORDER BY year, week
        """
    )
    rows_by_year_week = defaultdict(list)
    for year, week, team_id, opponent_team_id, team_score, is_win, is_loss, is_tie in cur.fetchall():
        rows_by_year_week[(year, week)].append(
            (team_id, opponent_team_id, team_score, is_win, is_loss, is_tie)
        )

    weeks_by_year = defaultdict(set)
    for year, week in rows_by_year_week:
        weeks_by_year[year].add(week)

    playoff_results_to_insert = []
    champions_to_insert = []

    for year, weeks in weeks_by_year.items():
        ordered_weeks = sorted(weeks)
        n_rounds = len(ordered_weeks)

        for round_num, week in enumerate(ordered_weeks, start=1):
            # Always label the last round "Championship" regardless of round count.
            if round_num == n_rounds:
                round_name = "Championship"
            else:
                round_name = ROUND_NAMES.get(round_num, f"Round {round_num}")

            for team_id, opponent_team_id, team_score, is_win, is_loss, is_tie in rows_by_year_week[(year, week)]:
                playoff_results_to_insert.append(
                    (year, week, round_name, team_id, opponent_team_id, team_score, is_win, is_loss, is_tie)
                )

                if round_num == n_rounds and is_win:
                    champions_to_insert.append((year, team_id, team_score, opponent_team_id))

    # champions_to_insert currently has (year, champion, champion_score, runner_up); fill runner_up_score.
    scores_by_year_team = {
        (year, team_id): team_score
        for year, week, team_id, opponent_team_id, team_score, is_win, is_loss, is_tie in (
            (y, w, t, o, s, wn, ls, ti)
            for (y, w), lst in rows_by_year_week.items()
            for (t, o, s, wn, ls, ti) in lst
        )
    }
    champions_final = [
        (year, champ_team, runner_up, round(champ_score, 2), round(scores_by_year_team[(year, runner_up)], 2))
        for year, champ_team, champ_score, runner_up in champions_to_insert
    ]

    cur.execute("DROP TABLE IF EXISTS playoff_results")
    cur.execute(
        """
        CREATE TABLE playoff_results (
            year INTEGER NOT NULL,
            week INTEGER NOT NULL,
            round TEXT NOT NULL,
            team_id INTEGER NOT NULL,
            opponent_team_id INTEGER NOT NULL,
            team_score REAL NOT NULL,
            is_win BOOLEAN NOT NULL,
            is_loss BOOLEAN NOT NULL,
            is_tie BOOLEAN NOT NULL,
            PRIMARY KEY (year, week, team_id)
        )
        """
    )
    cur.executemany(
        """
        INSERT INTO playoff_results (
            year, week, round, team_id, opponent_team_id, team_score, is_win, is_loss, is_tie
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        playoff_results_to_insert,
    )

    cur.execute("DROP TABLE IF EXISTS champions")
    cur.execute(
        """
        CREATE TABLE champions (
            year INTEGER NOT NULL PRIMARY KEY,
            champion_team_id INTEGER NOT NULL,
            runner_up_team_id INTEGER NOT NULL,
            champion_score REAL NOT NULL,
            runner_up_score REAL NOT NULL
        )
        """
    )
    cur.executemany(
        """
        INSERT INTO champions (
            year, champion_team_id, runner_up_team_id, champion_score, runner_up_score
        ) VALUES (?, ?, ?, ?, ?)
        """,
        champions_final,
    )

    conn.commit()
    print(f"Rebuilt playoff_results with {len(playoff_results_to_insert)} rows and champions with {len(champions_final)} rows.")
    conn.close()


if __name__ == "__main__":
    build_playoff_results()
