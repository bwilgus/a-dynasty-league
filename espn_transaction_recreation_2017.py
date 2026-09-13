import sqlite3
from typing import Dict, Set

DB_PATH = r"C:\Users\wilgu\Desktop\Fun\a-dynasty-league\dynasty_data.db"
YEAR = 2017


def reconstruct_2017_transactions():
    print(f"Connecting to database to reconstruct {YEAR} transactions...")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Pre-clean existing 2017 transaction records
    cur.execute("DELETE FROM transactions WHERE year = ?", (YEAR,))

    # Get all active weeks in chronological order
    cur.execute(
        "SELECT DISTINCT week FROM lineups WHERE year = ? ORDER BY week ASC",
        (YEAR,),
    )
    weeks = [row[0] for row in cur.fetchall()]

    if not weeks:
        print(f"No lineup rows found for {YEAR}. Make sure lineups are populated first.")
        conn.close()
        return

    trans_counter = 1
    transactions_to_insert = set()
    prev_team_rosters: Dict[int, Set[str]] = {}

    for week in weeks:
        cur.execute(
            """
            SELECT team_id, player_name 
            FROM lineups 
            WHERE year = ? AND week = ?
            """,
            (YEAR, week),
        )
        current_rows = cur.fetchall()

        current_team_rosters: Dict[int, Set[str]] = {}
        for t_id, p_name in current_rows:
            current_team_rosters.setdefault(t_id, set()).add(p_name)

        # Compare consecutive weeks starting at Week 2
        if week > 1 and prev_team_rosters:
            prev_player_to_team = {
                p: t_id
                for t_id, players in prev_team_rosters.items()
                for p in players
            }
            curr_player_to_team = {
                p: t_id
                for t_id, players in current_team_rosters.items()
                for p in players
            }

            all_teams = set(prev_team_rosters.keys()).union(current_team_rosters.keys())

            for t_id in all_teams:
                prev_p = prev_team_rosters.get(t_id, set())
                curr_p = current_team_rosters.get(t_id, set())

                added_players = curr_p - prev_p
                dropped_players = prev_p - curr_p

                # 1. Adds and Incoming Trades
                for p_name in added_players:
                    orig_team = prev_player_to_team.get(p_name)
                    trans_type = "trade" if (orig_team is not None and orig_team != t_id) else "freeagent"
                    trans_id = f"{YEAR}{week:02d}_{trans_counter:04d}"
                    trans_counter += 1

                    transactions_to_insert.add((
                        trans_id, t_id, YEAR, week, trans_type, "add", p_name
                    ))

                # 2. Drops and Outgoing Trades
                for p_name in dropped_players:
                    new_team = curr_player_to_team.get(p_name)
                    trans_type = "trade" if (new_team is not None and new_team != t_id) else "freeagent"
                    trans_id = f"{YEAR}{week:02d}_{trans_counter:04d}"
                    trans_counter += 1

                    transactions_to_insert.add((
                        trans_id, t_id, YEAR, week, trans_type, "drop", p_name
                    ))

        prev_team_rosters = current_team_rosters

    if transactions_to_insert:
        cur.executemany(
            """
            INSERT OR IGNORE INTO transactions (
                trans_id, team_id, year, week, trans_type, action, player
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            list(transactions_to_insert),
        )
        conn.commit()
        print(f"✓ Successfully generated and inserted {len(transactions_to_insert)} transaction events for {YEAR}.")
    else:
        print("No transaction events could be derived.")

    conn.close()


if __name__ == "__main__":
    reconstruct_2017_transactions()