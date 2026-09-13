import sqlite3
import requests
import json
from typing import Dict, List, Any, Set

DB_PATH = r"C:\Users\wilgu\Desktop\Fun\a-dynasty-league\dynasty_data.db"
LEAGUE_ID = 2255318
SWID = "{E9847B8A-5D66-44EE-BC5F-D7E4A554CDC7}"
ESPN_S2 = r"AEAbITOsDs5gtiiTG4JvTzEvrh5n%2F7owp0n7ZJgl7IpQXs2zHJM6TZAJLyoymtnRmak6jmkoWhJ8NEcnUl7ZPerjOXk%2BLgLhfzw8VsXye9wOupr7iXxIlxTCaAsY%2Fr1Fl%2BlTxVbt8fhIgWTox45PXXPUK0zmlFZ1XbFpd9fBH%2BZrbOFlEOHQzp8X1BhPwgpmXi%2Fveog8dlSPxyVtfVxwN9Mic%2BrwY%2B81XJIgr4g65FtXsOap8Zot9Ychkx6IrM4HPMH4eHbR1xfHV8BNS%2FHeImJt"


SLOT_MAP = {
    0: "QB", 2: "RB", 4: "WR", 6: "TE", 16: "DEF",
    17: "K", 20: "BN", 21: "IR", 23: "FLEX"
}

POSITION_MAP = {
    1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "DEF"
}


def get_base_url(year: int) -> str:
    """Routes 2018+ to the modern segment endpoint and 2017 to leagueHistory."""
    if year >= 2018:
        return f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{year}/segments/0/leagues/{LEAGUE_ID}"
    return f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/leagueHistory/{LEAGUE_ID}"


def fetch_espn_data(url: str, params: Dict[str, Any], headers: Dict[str, str] = None) -> Dict[str, Any]:
    cookies = {"espn_s2": ESPN_S2, "SWID": SWID}
    req_headers = {"User-Agent": "Mozilla/5.0"}
    if headers:
        req_headers.update(headers)

    response = requests.get(url, params=params, cookies=cookies, headers=req_headers)
    response.raise_for_status()
    payload = response.json()
    return payload[0] if isinstance(payload, list) else payload


def get_espn_championship_path(schedule: List[Dict[str, Any]], max_week: int) -> Set[tuple]:
    final_week_games = [
        m for m in schedule
        if m.get("matchupPeriodId") == max_week and m.get("away") and m.get("home")
    ]
    if not final_week_games:
        return set()

    champ_game = final_week_games[0]
    champ_teams = {champ_game["home"]["teamId"], champ_game["away"]["teamId"]}
    valid_playoff_teams = {(max_week, t_id) for t_id in champ_teams}

    for week in range(max_week - 1, max_week - 3, -1):
        for m in schedule:
            if m.get("matchupPeriodId") == week and m.get("away") and m.get("home"):
                h_id = m["home"]["teamId"]
                a_id = m["away"]["teamId"]
                if any((week + 1, t_id) in valid_playoff_teams for t_id in [h_id, a_id]):
                    valid_playoff_teams.add((week, h_id))
                    valid_playoff_teams.add((week, a_id))

    return valid_playoff_teams


def populate_espn_season(year: int):
    print(f"\n[1/4] Fetching base {year} season data from ESPN...")
    base_url = get_base_url(year)
    
    try:
        base_params = {
            "seasonId": year,
            "view": ["mTeam", "mRoster", "mMatchup", "mSettings"]
        }
        season = fetch_espn_data(base_url, base_params)
    except Exception as e:
        print(f"Failed to fetch season data from ESPN: {e}")
        return

    members_map = {
        m["id"]: m.get("displayName", f"User_{m['id']}")
        for m in season.get("members", [])
    }
    divisions_info = season.get("settings", {}).get("scheduleSettings", {}).get("divisions", [])
    div_names = {d["id"]: d["name"] for d in divisions_info}

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Pre-clean only the target tables to prevent duplicates
    for table in ["teams", "divisions", "games", "lineups", "transactions"]:
        cur.execute(f"DELETE FROM {table} WHERE year = ?", (year,))
    conn.commit()

    try:
        # 1. Teams & Divisions
        print("[2/4] Inserting Teams and Divisions...")
        teams_to_insert = []
        divisions_to_insert = []

        for t in season.get("teams", []):
            t_id = t["id"]
            team_name = t.get("name", f"Team {t_id}")
            primary_owner = t.get("primaryOwner") or (t.get("owners") or [""])[0]
            owner_name = members_map.get(primary_owner, f"Owner_{t_id}")

            teams_to_insert.append((year, t_id, owner_name, team_name))

            div_id = t.get("divisionId")
            if div_id is not None:
                divisions_to_insert.append((year, div_id, div_names.get(div_id, f"Division {div_id}"), t_id))

        cur.executemany("INSERT INTO teams (year, team_id, owner, team_name) VALUES (?, ?, ?, ?)", teams_to_insert)
        if divisions_to_insert:
            cur.executemany("INSERT INTO divisions (year, division_id, division_name, team_id) VALUES (?, ?, ?, ?)", divisions_to_insert)

        # 2. Games & Lineups
        print(f"[3/4] Scraping weekly box scores (including bench players)...")
        schedule = season.get("schedule", [])
        max_week = max((m.get("matchupPeriodId", 0) for m in schedule), default=16)
        playoff_start = season.get("settings", {}).get("scheduleSettings", {}).get("matchupPeriodCount", 13) + 1
        champ_path_teams = get_espn_championship_path(schedule, max_week)

        games_to_insert = []
        lineups_to_insert = []
        
        # The magic fix: mMatchupScore retrieves bench players for 2017. mBoxscore is used for 2018+.
        boxscore_view = "mBoxscore" if year >= 2018 else "mMatchupScore"

        for week in range(1, max_week + 1):
            week_params = {
                "seasonId": year,
                "view": [boxscore_view, "mMatchup"],
                "scoringPeriodId": week
            }
            try:
                week_data = fetch_espn_data(base_url, week_params)
            except Exception as e:
                print(f"   Warning: Failed fetching boxscores for week {week}: {e}")
                continue

            week_schedule = [m for m in week_data.get("schedule", []) if m.get("matchupPeriodId") == week]

            for m in week_schedule:
                is_playoff = week >= playoff_start
                m_id = m.get("id", 0)
                game_id = int(f"{year}{week:02d}{m_id:02d}")

                home = m.get("home")
                away = m.get("away")

                pairs = []
                if home and away:
                    h_id = home["teamId"]
                    a_id = away["teamId"]

                    if is_playoff and not ((week, h_id) in champ_path_teams and (week, a_id) in champ_path_teams):
                        continue

                    h_pts = round(float(home.get("totalPoints", 0.0)), 2)
                    a_pts = round(float(away.get("totalPoints", 0.0)), 2)
                    pairs = [(home, h_id, a_id, h_pts, a_pts), (away, a_id, h_id, a_pts, h_pts)]
                elif home:
                    h_id = home["teamId"]
                    if is_playoff and (week, h_id) not in champ_path_teams:
                        continue
                    h_pts = round(float(home.get("totalPoints", 0.0)), 2)
                    pairs = [(home, h_id, None, h_pts, 0.0)]

                for side_obj, t_id, opp_id, score, opp_score in pairs:
                    is_win = score > opp_score if opp_id else True
                    is_loss = score < opp_score if opp_id else False
                    is_tie = score == opp_score if opp_id else False

                    games_to_insert.append((
                        year, week, game_id, t_id, opp_id,
                        score, 0.0, is_playoff, is_win, is_tie, is_loss,
                    ))

                    roster = side_obj.get("rosterForMatchupPeriod", {}).get("entries", [])
                    for entry in roster:
                        p_pool = entry.get("playerPoolEntry", {})
                        player = p_pool.get("player", {})
                        pid = player.get("id")
                        p_name = player.get("fullName", "Unknown")
                        pos = POSITION_MAP.get(player.get("defaultPositionId"), "Unknown")
                        slot_pos = SLOT_MAP.get(entry.get("lineupSlotId"), "BN")
                        status = player.get("injuryStatus", "ACTIVE")
                        p_score = round(float(p_pool.get("appliedStatTotal", 0.0)), 2)

                        p_id_int = int(pid) if pid else None
                        lineups_to_insert.append((
                            year, week, game_id, t_id, p_id_int, p_name,
                            pos, slot_pos, status, p_score, 0.0,
                        ))

        cur.executemany(
            """INSERT INTO games (
                year, week, game_id, team_id, opponent_team_id,
                team_score, team_proj_score, is_playoff, is_win, is_tie, is_loss
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            games_to_insert,
        )
        cur.executemany(
            """INSERT OR REPLACE INTO lineups (
                year, week, game_id, team_id, player_id, player_name,
                player_position, player_slot_position, player_status,
                player_score, player_proj_score
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            lineups_to_insert,
        )

        # 3. Transactions
        print("[4/4] Processing transactions...")
        transactions_to_insert = set()
        trans_counter = 1

        if year >= 2018:
            # For 2018+, we can use the native ESPN mTransactions2 ledger
            # Build a player catalog first to resolve names
            catalog_params = {"seasonId": year, "view": "mRoster"}
            cat_data = fetch_espn_data(base_url, catalog_params)
            player_catalog = {}
            for t in cat_data.get("teams", []):
                for e in t.get("roster", {}).get("entries", []):
                    p = e.get("playerPoolEntry", {}).get("player", {})
                    if p.get("id"):
                        player_catalog[p["id"]] = p.get("fullName", f"Player_{p['id']}")

            # ESPN requires filter headers to expose executed moves in 2018
            filters = {"transactions": {"filterType": {"value": ["WAIVER", "FREEAGENT", "TRADE", "TRADE_ACCEPT", "ROSTER"]}}}
            headers = {"x-fantasy-filter": json.dumps(filters)}

            for week in range(1, 18):
                params = {"view": "mTransactions2", "scoringPeriodId": week, "seasonId": year}
                try:
                    week_data = fetch_espn_data(base_url, params, headers=headers)
                except Exception:
                    continue

                for trans in week_data.get("transactions", []):
                    if trans.get("status") not in ("EXECUTED", "COMPLETE"):
                        continue

                    trans_id = str(trans.get("id", ""))
                    trans_type = trans.get("type", "UNKNOWN").lower()

                    for item in trans.get("items", []):
                        item_type = item.get("type")
                        pid = item.get("playerId")
                        p_name = player_catalog.get(pid, f"Player_{pid}")

                        if item.get("toTeamId") and item["toTeamId"] > 0:
                            transactions_to_insert.add((trans_id, item["toTeamId"], year, week, trans_type, "add", p_name))
                        if item.get("fromTeamId") and item["fromTeamId"] > 0 and item_type in ("DROP", "LINEUP"):
                            transactions_to_insert.add((trans_id, item["fromTeamId"], year, week, trans_type, "drop", p_name))

        else:
            # For 2017, derive transactions using week-over-week roster differentials
            print("   Deriving 2017 transactions via roster differentials...")
            weekly_rosters = {}
            for row in lineups_to_insert:
                wk = row[1]
                t_id = row[3]
                p_name = row[5]
                weekly_rosters.setdefault(wk, {}).setdefault(t_id, set()).add(p_name)
                
            weeks = sorted(weekly_rosters.keys())
            prev_team_rosters = {}
            
            for wk in weeks:
                curr_team_rosters = weekly_rosters[wk]
                if wk > 1 and prev_team_rosters:
                    prev_player_to_team = {p: t for t, players in prev_team_rosters.items() for p in players}
                    curr_player_to_team = {p: t for t, players in curr_team_rosters.items() for p in players}
                    
                    all_teams = set(prev_team_rosters.keys()).union(curr_team_rosters.keys())
                    for t_id in all_teams:
                        prev_p = prev_team_rosters.get(t_id, set())
                        curr_p = curr_team_rosters.get(t_id, set())
                        
                        for p_name in (curr_p - prev_p):
                            orig_team = prev_player_to_team.get(p_name)
                            ttype = "trade" if orig_team and orig_team != t_id else "freeagent"
                            trans_id = f"{year}{wk:02d}_{trans_counter:04d}"
                            trans_counter += 1
                            transactions_to_insert.add((trans_id, t_id, year, wk, ttype, "add", p_name))
                            
                        for p_name in (prev_p - curr_p):
                            new_team = curr_player_to_team.get(p_name)
                            ttype = "trade" if new_team and new_team != t_id else "freeagent"
                            trans_id = f"{year}{wk:02d}_{trans_counter:04d}"
                            trans_counter += 1
                            transactions_to_insert.add((trans_id, t_id, year, wk, ttype, "drop", p_name))
                            
                prev_team_rosters = curr_team_rosters

        if transactions_to_insert:
            cur.executemany(
                """INSERT OR IGNORE INTO transactions (
                    trans_id, team_id, year, week, trans_type, action, player
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                list(transactions_to_insert),
            )
            print(f" -> Stored {len(transactions_to_insert)} transaction events.")

        conn.commit()
        print(f"✓ Season {year} successfully synced into dynasty_data.db.")

    except Exception as e:
        conn.rollback()
        print(f"Error during import: {e}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    year_input = input("Enter ESPN League Year (e.g., 2017, 2018): ").strip()
    if year_input.isdigit():
        populate_espn_season(int(year_input))
    else:
        print("Invalid year provided. Exiting.")