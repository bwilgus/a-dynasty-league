import sqlite3
import requests
from typing import Dict, List, Any, Set

DB_PATH = r"C:\Users\wilgu\Desktop\Fun\a-dynasty-league\dynasty_data.db"
LEAGUE_ID = 2255318
SWID = "{E9847B8A-5D66-44EE-BC5F-D7E4A554CDC7}"
ESPN_S2 = r"AEAbITOsDs5gtiiTG4JvTzEvrh5n%2F7owp0n7ZJgl7IpQXs2zHJM6TZAJLyoymtnRmak6jmkoWhJ8NEcnUl7ZPerjOXk%2BLgLhfzw8VsXye9wOupr7iXxIlxTCaAsY%2Fr1Fl%2BlTxVbt8fhIgWTox45PXXPUK0zmlFZ1XbFpd9fBH%2BZrbOFlEOHQzp8X1BhPwgpmXi%2Fveog8dlSPxyVtfVxwN9Mic%2BrwY%2B81XJIgr4g65FtXsOap8Zot9Ychkx6IrM4HPMH4eHbR1xfHV8BNS%2FHeImJt"


# ESPN Slot ID -> Lineup Slot Name
SLOT_MAP = {
    0: "QB",
    2: "RB",
    4: "WR",
    6: "TE",
    16: "DEF",
    17: "K",
    20: "BN",
    21: "IR",
    23: "FLEX",
}

# ESPN Default Position ID -> Position Abbreviation
POSITION_MAP = {
    1: "QB",
    2: "RB",
    3: "WR",
    4: "TE",
    5: "K",
    16: "DEF",
}


def fetch_espn_data(params: Dict[str, Any]) -> Dict[str, Any]:
    url = f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/leagueHistory/{LEAGUE_ID}"
    cookies = {"espn_s2": ESPN_S2, "SWID": SWID}
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    response = requests.get(url, params=params, cookies=cookies, headers=headers)
    response.raise_for_status()
    payload = response.json()
    return payload[0] if isinstance(payload, list) else payload


def get_espn_championship_path(schedule: List[Dict[str, Any]], max_week: int) -> Set[tuple]:
    """
    Traces backward from the final championship week to isolate strictly
    matchups leading to the title, omitting consolation brackets.
    """
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


def populate_espn_season(year: int, db_path: str = DB_PATH):
    print(f"\n[1/4] Fetching base {year} season data (teams, schedule, divisions)...")
    try:
        base_params = {
            "seasonId": year,
            "view": ["mTeam", "mRoster", "mMatchup", "mSettings"]
        }
        season = fetch_espn_data(base_params)
    except Exception as e:
        print(f"Failed to fetch base season data: {e}")
        return

    members_map = {
        m["id"]: m.get("displayName", f"User_{m['id']}")
        for m in season.get("members", [])
    }
    divisions_info = season.get("settings", {}).get("scheduleSettings", {}).get("divisions", [])
    div_names = {d["id"]: d["name"] for d in divisions_info}

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    try:
        # 1. Teams, Standings & Divisions
        print("[2/4] Inserting Teams, Standings, and Divisions...")
        teams_to_insert = []
        divisions_to_insert = []
        standings_to_insert = []

        for t in season.get("teams", []):
            t_id = t["id"]
            team_name = t.get("name", f"Team {t_id}")
            primary_owner = t.get("primaryOwner") or (t.get("owners") or [""])[0]
            owner_name = members_map.get(primary_owner, f"Owner_{t_id}")

            teams_to_insert.append((year, t_id, owner_name, team_name))

            div_id = t.get("divisionId")
            if div_id is not None:
                divisions_to_insert.append((
                    year,
                    div_id,
                    div_names.get(div_id, f"Division {div_id}"),
                    t_id,
                ))

            overall = t.get("record", {}).get("overall", {})
            wins = overall.get("wins", 0)
            losses = overall.get("losses", 0)
            ties = overall.get("ties", 0)
            points_for = round(float(overall.get("pointsFor", 0.0)), 2)
            points_against = round(float(overall.get("pointsAgainst", 0.0)), 2)
            max_pf = points_for

            standings_to_insert.append((
                year, t_id, wins, losses, ties, points_for, points_against, max_pf
            ))

        cur.executemany(
            "INSERT OR REPLACE INTO teams (year, team_id, owner, team_name) VALUES (?, ?, ?, ?)",
            teams_to_insert,
        )
        cur.executemany(
            """INSERT OR REPLACE INTO standings (
                year, team_id, wins, losses, ties, points_for, points_against, max_pf
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            standings_to_insert,
        )
        if divisions_to_insert:
            cur.executemany(
                "INSERT OR REPLACE INTO divisions (year, division_id, division_name, team_id) VALUES (?, ?, ?, ?)",
                divisions_to_insert,
            )

        print(f" -> Stored {len(teams_to_insert)} teams, {len(divisions_to_insert)} division mappings, and {len(standings_to_insert)} standings.")

        # 2. Matchups and Weekly Boxscore Lineups
        print("[3/4] Scraping weekly box scores and player lineups (Weeks 1 to 16)...")
        schedule = season.get("schedule", [])
        max_week = max((m.get("matchupPeriodId", 0) for m in schedule), default=16)
        playoff_start = season.get("settings", {}).get("scheduleSettings", {}).get("matchupPeriodCount", 13) + 1
        champ_path_teams = get_espn_championship_path(schedule, max_week)

        games_to_insert = []
        lineups_to_insert = []

        for week in range(1, max_week + 1):
            print(f"   Fetching Week {week} boxscores...")
            week_params = {
                "seasonId": year,
                "view": ["mBoxscore", "mMatchup"],
                "scoringPeriodId": week
            }
            try:
                week_data = fetch_espn_data(week_params)
            except Exception as e:
                print(f"   Warning: Could not fetch boxscores for week {week}: {e}")
                continue

            week_schedule = [
                m for m in week_data.get("schedule", [])
                if m.get("matchupPeriodId") == week
            ]

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

                    # Filter out consolation bracket playoff games
                    if is_playoff and not (
                        (week, h_id) in champ_path_teams and (week, a_id) in champ_path_teams
                    ):
                        continue

                    h_pts = round(float(home.get("totalPoints", 0.0)), 2)
                    a_pts = round(float(away.get("totalPoints", 0.0)), 2)

                    pairs = [
                        (home, h_id, a_id, h_pts, a_pts),
                        (away, a_id, h_id, a_pts, h_pts),
                    ]
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
                        year,
                        week,
                        game_id,
                        t_id,
                        opp_id,
                        score,
                        0.0,
                        is_playoff,
                        is_win,
                        is_tie,
                        is_loss,
                    ))

                    roster = side_obj.get("rosterForMatchupPeriod", {}).get("entries", [])
                    for entry in roster:
                        p_pool = entry.get("playerPoolEntry", {})
                        player = p_pool.get("player", {})
                        p_id = player.get("id")
                        p_name = player.get("fullName", "Unknown")
                        pos = POSITION_MAP.get(player.get("defaultPositionId"), "Unknown")
                        slot_pos = SLOT_MAP.get(entry.get("lineupSlotId"), "BN")
                        status = player.get("injuryStatus", "ACTIVE")
                        p_score = round(float(p_pool.get("appliedStatTotal", 0.0)), 2)

                        lineups_to_insert.append((
                            year,
                            week,
                            game_id,
                            t_id,
                            p_id,
                            p_name,
                            pos,
                            slot_pos,
                            status,
                            p_score,
                            0.0,
                        ))

        cur.executemany(
            """INSERT OR REPLACE INTO games (
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

        print(f" -> Stored {len(games_to_insert)} games and {len(lineups_to_insert)} lineup rows.")

        # 3. Commit
        print("[4/4] Committing changes to SQLite database...")
        conn.commit()
        print(f"✓ Season {year} successfully imported.")

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