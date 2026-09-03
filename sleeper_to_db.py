import sqlite3
import requests
from typing import Dict, Any, List, Set

SLEEPER_BASE_URL = "https://api.sleeper.app/v1"
DB_PATH = r"C:\Users\wilgu\Desktop\Fun\a-dynasty-league\dynasty_data.db"

FLEX_ELIGIBILITY = {
    "FLEX": {"RB", "WR", "TE"},
    "SUPER_FLEX": {"QB", "RB", "WR", "TE"},
    "REC_FLEX": {"WR", "TE"},
    "WRRB_FLEX": {"RB", "WR"},
    "IDP_FLEX": {"DL", "LB", "DB"},
}


def fetch_json(url: str) -> Any:
    """Helper to fetch and parse JSON from Sleeper API."""
    response = requests.get(url)
    response.raise_for_status()
    return response.json()


def calculate_optimal_score(
    roster_positions: List[str],
    player_scores: Dict[str, float],
    player_positions: Dict[str, str],
) -> float:
    """Computes the maximum potential starting score for a roster."""
    active_slots = [p for p in roster_positions if p not in ("BN", "IR", "TAXI")]

    available_players = []
    for pid, score in player_scores.items():
        pos = player_positions.get(str(pid), "Unknown")
        available_players.append({"id": pid, "pos": pos, "score": max(0.0, score)})

    available_players.sort(key=lambda x: x["score"], reverse=True)

    base_slots = [s for s in active_slots if s not in FLEX_ELIGIBILITY]
    flex_slots = [s for s in active_slots if s in FLEX_ELIGIBILITY]

    used_player_ids = set()
    optimal_score = 0.0

    # 1. Fill positional slots
    for slot in base_slots:
        for p in available_players:
            if p["id"] not in used_player_ids and p["pos"] == slot:
                used_player_ids.add(p["id"])
                optimal_score += p["score"]
                break

    # 2. Fill flex slots
    for slot in flex_slots:
        eligible_positions = FLEX_ELIGIBILITY.get(slot, set())
        for p in available_players:
            if p["id"] not in used_player_ids and p["pos"] in eligible_positions:
                used_player_ids.add(p["id"])
                optimal_score += p["score"]
                break

    return round(optimal_score, 2)


def get_championship_path_rosters(league_id: str, playoff_week_start: int) -> Set[tuple]:
    """Finds matchups strictly on the direct path to the league championship."""
    try:
        bracket = fetch_json(f"{SLEEPER_BASE_URL}/league/{league_id}/winners_bracket")
    except requests.exceptions.HTTPError:
        return set()

    if not bracket:
        return set()

    max_round = max(m.get("r", 1) for m in bracket)
    champ_matches = [
        m
        for m in bracket
        if m.get("r") == max_round
        and "l" not in (m.get("t1_from") or {})
        and "l" not in (m.get("t2_from") or {})
    ]
    if not champ_matches:
        champ_matches = [bracket[-1]]
    champ_match = champ_matches[0]

    valid_match_ids = set()
    queue = [champ_match["m"]]
    bracket_by_m = {m["m"]: m for m in bracket}

    while queue:
        curr_m = queue.pop(0)
        valid_match_ids.add(curr_m)
        node = bracket_by_m.get(curr_m)
        if not node:
            continue
        if node.get("t1_from") and "w" in node["t1_from"]:
            queue.append(node["t1_from"]["w"])
        if node.get("t2_from") and "w" in node["t2_from"]:
            queue.append(node["t2_from"]["w"])

    valid_playoff_teams = set()
    for m in bracket:
        if m["m"] in valid_match_ids:
            week = playoff_week_start + m["r"] - 1
            for key in ("t1", "t2"):
                val = m.get(key)
                if isinstance(val, int):
                    valid_playoff_teams.add((week, val))

    return valid_playoff_teams


def populate_league_data(league_id: str, db_path: str = DB_PATH):
    print(f"\n[1/6] Fetching league metadata for League ID: {league_id}...")
    try:
        league_info = fetch_json(f"{SLEEPER_BASE_URL}/league/{league_id}")
    except requests.exceptions.HTTPError as e:
        print(f"Error fetching league: {e}. Please check that the League ID is valid.")
        return

    year = int(league_info.get("season", 0))
    settings = league_info.get("settings", {})
    playoff_week_start = settings.get("playoff_week_start", 15)

    print("[2/6] Fetching users, rosters, and player database...")
    users = fetch_json(f"{SLEEPER_BASE_URL}/league/{league_id}/users")
    rosters = fetch_json(f"{SLEEPER_BASE_URL}/league/{league_id}/rosters")
    all_players = fetch_json(f"{SLEEPER_BASE_URL}/players/nfl")

    user_map = {u["user_id"]: u for u in users}
    roster_reserve_map = {r["roster_id"]: set(r.get("reserve") or []) for r in rosters}
    roster_taxi_map = {r["roster_id"]: set(r.get("taxi") or []) for r in rosters}
    roster_owner_map = {
        r["roster_id"]: user_map.get(r.get("owner_id"), {}).get("display_name", f"Team {r['roster_id']}")
        for r in rosters
    }
    player_pos_map = {pid: info.get("position", "Unknown") for pid, info in all_players.items()}

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    try:
        # 1. Teams & Standings
        teams_to_insert = []
        divisions_to_insert = []
        standings_to_insert = []

        for r in rosters:
            team_id = r["roster_id"]
            owner_id = r.get("owner_id")
            user = user_map.get(owner_id, {})

            owner_name = user.get("display_name", f"Owner_{team_id}")
            team_name = (
                r.get("metadata", {}).get("team_name")
                or user.get("metadata", {}).get("team_name")
                or f"{owner_name}'s Team"
            )
            teams_to_insert.append((year, team_id, owner_name, team_name))

            division_id = r.get("settings", {}).get("division")
            if division_id is not None:
                div_name = league_info.get("metadata", {}).get(
                    f"division_{division_id}", f"Division {division_id}"
                )
                divisions_to_insert.append((year, division_id, div_name, team_id))

            st = r.get("settings", {})
            wins = st.get("wins", 0)
            losses = st.get("losses", 0)
            ties = st.get("ties", 0)
            points_for = round(st.get("fpts", 0) + (st.get("fpts_decimal", 0) / 100.0), 2)
            points_against = round(st.get("fpts_against", 0) + (st.get("fpts_against_decimal", 0) / 100.0), 2)
            max_pf = round(st.get("ppts", 0) + (st.get("ppts_decimal", 0) / 100.0), 2)
            standings_to_insert.append((year, team_id, wins, losses, ties, points_for, points_against, max_pf))

        cur.executemany("INSERT OR REPLACE INTO teams (year, team_id, owner, team_name) VALUES (?, ?, ?, ?)", teams_to_insert)
        cur.executemany(
            "INSERT OR REPLACE INTO standings (year, team_id, wins, losses, ties, points_for, points_against, max_pf) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            standings_to_insert,
        )
        if divisions_to_insert:
            cur.executemany(
                "INSERT OR REPLACE INTO divisions (year, division_id, division_name, team_id) VALUES (?, ?, ?, ?)",
                divisions_to_insert,
            )

        print(f" -> Stored {len(teams_to_insert)} teams, {len(standings_to_insert)} standings records.")

        # 2. Matchups, Lineups & Efficiency
        print("[3/6] Scraping weekly matchups, starting lineups, and manager efficiency...")
        games_to_insert = []
        lineups_to_insert = []
        efficiency_to_insert = []
        roster_positions = league_info.get("roster_positions", [])
        valid_championship_teams = get_championship_path_rosters(league_id, playoff_week_start)

        for week in range(1, 19):
            matchups = fetch_json(f"{SLEEPER_BASE_URL}/league/{league_id}/matchups/{week}")
            if not matchups:
                continue

            # Calculate Manager Efficiency for all participating rosters
            for team_entry in matchups:
                team_id = team_entry["roster_id"]
                actual_score = float(team_entry.get("points", 0.0))
                team_players = team_entry.get("players") or []
                players_points = team_entry.get("players_points") or {}
                roster_player_scores = {
                    str(pid): float(players_points.get(str(pid), 0.0))
                    for pid in team_players
                    if pid and pid != "0"
                }

                optimal_score = calculate_optimal_score(roster_positions, roster_player_scores, player_pos_map)
                efficiency_pct = round((actual_score / optimal_score) * 100.0, 2) if optimal_score > 0 else (100.0 if actual_score == 0 else 0.0)
                efficiency_to_insert.append((year, week, team_id, actual_score, optimal_score, efficiency_pct))

            # Group games by matchup_id
            matchup_groups: Dict[int, List[Dict[str, Any]]] = {}
            for m in matchups:
                m_id = m.get("matchup_id")
                if m_id is not None:
                    matchup_groups.setdefault(m_id, []).append(m)

            for m_id, paired_teams in matchup_groups.items():
                is_playoff = week >= playoff_week_start

                if is_playoff:
                    paired_teams = [t for t in paired_teams if (week, t["roster_id"]) in valid_championship_teams]
                    if not paired_teams:
                        continue

                current_game_id = int(f"{year}{week:02d}{m_id:02d}")

                for team_entry in paired_teams:
                    team_id = team_entry["roster_id"]
                    team_score = float(team_entry.get("points", 0.0))
                    team_proj = 0.0

                    opponent_entry = next((t for t in paired_teams if t["roster_id"] != team_id), None)
                    if opponent_entry:
                        opp_id = opponent_entry["roster_id"]
                        opp_score = float(opponent_entry.get("points", 0.0))
                        is_win = team_score > opp_score
                        is_loss = team_score < opp_score
                        is_tie = team_score == opp_score
                    else:
                        opp_id = None
                        is_win = True
                        is_loss = False
                        is_tie = False

                    games_to_insert.append((year, week, current_game_id, team_id, opp_id, team_score, team_proj, is_playoff, is_win, is_tie, is_loss))

                    all_roster_players = team_entry.get("players") or []
                    starters = team_entry.get("starters") or []
                    players_points = team_entry.get("players_points") or {}
                    starter_slots = [pos for pos in roster_positions if pos not in ("BN", "IR", "TAXI")]

                    starter_slot_map = {}
                    for idx, pid in enumerate(starters):
                        if pid and pid != "0":
                            starter_slot_map[str(pid)] = starter_slots[idx] if idx < len(starter_slots) else "FLEX"

                    team_reserve_set = roster_reserve_map.get(team_id, set())
                    team_taxi_set = roster_taxi_map.get(team_id, set())

                    for pid in all_roster_players:
                        if not pid or pid == "0":
                            continue

                        pid_str = str(pid)
                        p_info = all_players.get(pid_str, {})
                        p_name = f"{p_info.get('first_name', '')} {p_info.get('last_name', '')}".strip() or pid_str
                        pos = p_info.get("position", "Unknown")
                        status = p_info.get("status", "Active")
                        p_score = float(players_points.get(pid_str, 0.0))

                        if pid_str in starter_slot_map:
                            slot_pos = starter_slot_map[pid_str]
                        elif pid_str in team_reserve_set:
                            slot_pos = "IR"
                        elif pid_str in team_taxi_set:
                            slot_pos = "TAXI"
                        else:
                            slot_pos = "BN"

                        try:
                            p_id_int = int(pid)
                        except ValueError:
                            p_id_int = None

                        lineups_to_insert.append((year, week, current_game_id, team_id, p_id_int, p_name, pos, slot_pos, status, p_score, 0.0))

        cur.executemany(
            """
            INSERT OR REPLACE INTO games (
                year, week, game_id, team_id, opponent_team_id,
                team_score, team_proj_score, is_playoff, is_win, is_tie, is_loss
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            games_to_insert,
        )

        cur.executemany(
            """
            INSERT OR REPLACE INTO lineups (
                year, week, game_id, team_id, player_id, player_name,
                player_position, player_slot_position, player_status,
                player_score, player_proj_score
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            lineups_to_insert,
        )

        cur.executemany(
            """
            INSERT OR REPLACE INTO manager_efficiency (
                year, week, team_id, actual_score, optimal_score, efficiency_pct
            ) VALUES (?, ?, ?, ?, ?, ?)
        """,
            efficiency_to_insert,
        )

        print(f" -> Stored {len(games_to_insert)} games, {len(lineups_to_insert)} lineups, {len(efficiency_to_insert)} efficiency rows.")

        # 3. Transactions
        print("[4/6] Scraping transactions...")
        transactions_to_insert = []
        for week in range(1, 19):
            trans_url = f"{SLEEPER_BASE_URL}/league/{league_id}/transactions/{week}"
            weekly_trans = fetch_json(trans_url)
            if not weekly_trans:
                continue

            for t in weekly_trans:
                if t.get("status") != "complete":
                    continue

                trans_id = str(t.get("transaction_id"))
                trans_type = t.get("type")

                for pid, roster_id in (t.get("adds") or {}).items():
                    p_info = all_players.get(str(pid), {})
                    p_name = f"{p_info.get('first_name', '')} {p_info.get('last_name', '')}".strip() or str(pid)
                    transactions_to_insert.append((trans_id, roster_id, year, week, trans_type, "add", p_name))

                for pid, roster_id in (t.get("drops") or {}).items():
                    p_info = all_players.get(str(pid), {})
                    p_name = f"{p_info.get('first_name', '')} {p_info.get('last_name', '')}".strip() or str(pid)
                    transactions_to_insert.append((trans_id, roster_id, year, week, trans_type, "drop", p_name))

        cur.executemany(
            "INSERT OR REPLACE INTO transactions (trans_id, team_id, year, week, trans_type, action, player) VALUES (?, ?, ?, ?, ?, ?, ?)",
            transactions_to_insert,
        )
        print(f" -> Stored {len(transactions_to_insert)} transactions.")

        # 4. Draft Picks
        print("[5/6] Scraping draft history...")
        drafts = fetch_json(f"{SLEEPER_BASE_URL}/league/{league_id}/drafts")
        draft_picks_to_insert = []

        for draft in drafts:
            if draft.get("status") != "complete":
                continue

            raw_draft_id = draft.get("draft_id")
            if raw_draft_id == '1082380613652430848':
                continue

            draft_id_val = int("".join(filter(str.isdigit, str(raw_draft_id))) or 0)
            draft_year = int(draft.get("season", year))

            picks = fetch_json(f"{SLEEPER_BASE_URL}/draft/{raw_draft_id}/picks")

            for pick in picks:
                round_num = pick.get("round")
                pick_no = pick.get("draft_slot") or pick.get("pick_no")
                team_id = pick.get("roster_id")
                pid = pick.get("player_id")

                try:
                    player_id = int(pid) if pid else None
                except ValueError:
                    player_id = None

                meta = pick.get("metadata", {})
                first_name = meta.get("first_name", "")
                last_name = meta.get("last_name", "")
                player_name = f"{first_name} {last_name}".strip()

                if not player_name and pid:
                    p_info = all_players.get(str(pid), {})
                    player_name = f"{p_info.get('first_name', '')} {p_info.get('last_name', '')}".strip() or str(pid)

                position = meta.get("position") or all_players.get(str(pid), {}).get("position", "Unknown")
                nfl_team = meta.get("team") or all_players.get(str(pid), {}).get("team", "FA")

                orig_roster_id = pick.get("original_roster_id") or team_id
                prev_roster_id = pick.get("previous_roster_id") or orig_roster_id
                original_roster = roster_owner_map.get(orig_roster_id, str(orig_roster_id))
                previous_owner = roster_owner_map.get(prev_roster_id, str(prev_roster_id))

                draft_picks_to_insert.append(
                    (
                        draft_id_val,
                        draft_year,
                        round_num,
                        pick_no,
                        team_id,
                        player_id,
                        player_name,
                        position,
                        nfl_team,
                        original_roster,
                        previous_owner,
                    )
                )

        if draft_picks_to_insert:
            cur.executemany(
                """
                INSERT OR REPLACE INTO draft_picks (
                    draft_id, year, round, pick_no, team_id,
                    player_id, player_name, position, nfl_team,
                    original_roster, previous_owner
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                draft_picks_to_insert,
            )
            print(f" -> Stored {len(draft_picks_to_insert)} draft picks.")

        print("[6/6] Committing changes to SQLite database...")
        conn.commit()
        print("✓ Complete! Data successfully inserted into dynasty_data.db.")

    except Exception as e:
        conn.rollback()
        print(f"Error encountered during sync: {e}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    league_input = input("Enter your Sleeper League ID: ").strip()
    if league_input:
        populate_league_data(league_input)
    else:
        print("No League ID provided. Exiting.")