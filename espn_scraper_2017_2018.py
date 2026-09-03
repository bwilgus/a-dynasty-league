import sqlite3
from espn_api.football import League

DB_PATH = r"C:\Users\wilgu\Desktop\Fun\a-dynasty-league\dynasty_data.db"
LEAGUE_ID = 2255318  # Replace with your ESPN League ID (integer)
SWID = "{E9847B8A-5D66-44EE-BC5F-D7E4A554CDC7}"
ESPN_S2 = r"AEAbITOsDs5gtiiTG4JvTzEvrh5n%2F7owp0n7ZJgl7IpQXs2zHJM6TZAJLyoymtnRmak6jmkoWhJ8NEcnUl7ZPerjOXk%2BLgLhfzw8VsXye9wOupr7iXxIlxTCaAsY%2Fr1Fl%2BlTxVbt8fhIgWTox45PXXPUK0zmlFZ1XbFpd9fBH%2BZrbOFlEOHQzp8X1BhPwgpmXi%2Fveog8dlSPxyVtfVxwN9Mic%2BrwY%2B81XJIgr4g65FtXsOap8Zot9Ychkx6IrM4HPMH4eHbR1xfHV8BNS%2FHeImJt"


def populate_espn_season(year: int):
    print(f"\n--- Processing ESPN Season {year} ---")
    
    # Initialize connection to ESPN API
    try:
        league = League(
            league_id=LEAGUE_ID,
            year=year,
            espn_s2=ESPN_S2,
            swid=SWID
        )
    except Exception as e:
        print(f"Failed to connect to ESPN for {year}: {e}")
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    try:
        # 1. Teams & Standings
        print("Extracting teams and season standings...")
        teams_to_insert = []
        standings_to_insert = []

        for team in league.teams:
            team_id = team.team_id
            owner_name = team.owner.strip() if hasattr(team, 'owner') and team.owner else f"Owner_{team_id}"
            team_name = team.team_name.strip()
            
            teams_to_insert.append((year, team_id, owner_name, team_name))
            
            # Standings metrics
            wins = getattr(team, 'wins', 0)
            losses = getattr(team, 'losses', 0)
            ties = getattr(team, 'ties', 0)
            points_for = round(float(getattr(team, 'points_for', 0.0)), 2)
            points_against = round(float(getattr(team, 'points_against', 0.0)), 2)
            # ESPN does not natively track Max PF in legacy APIs; use points_for as fallback
            max_pf = points_for

            standings_to_insert.append((
                year, team_id, wins, losses, ties, points_for, points_against, max_pf
            ))

        cur.executemany(
            "INSERT OR REPLACE INTO teams (year, team_id, owner, team_name) VALUES (?, ?, ?, ?)",
            teams_to_insert
        )
        cur.executemany(
            """INSERT OR REPLACE INTO standings (
                year, team_id, wins, losses, ties, points_for, points_against, max_pf
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            standings_to_insert
        )
        print(f" -> Stored {len(teams_to_insert)} teams and standings.")

        # 2. Matchups and Lineups
        print("Extracting weekly box scores and lineups...")
        games_to_insert = []
        lineups_to_insert = []

        # ESPN regular + playoff weeks for 2017/2018 typically ran weeks 1 to 16 or 17
        total_weeks = getattr(league.settings, 'playoff_team_count', 16)
        playoff_start = getattr(league.settings, 'playoff_start_week', 14)

        for week in range(1, 18):
            try:
                box_scores = league.box_scores(week=week)
            except Exception:
                # Weeks beyond season end return exceptions or empty sets
                break

            if not box_scores:
                continue

            for m_idx, matchup in enumerate(box_scores, start=1):
                game_id = int(f"{year}{week:02d}{m_idx:02d}")
                is_playoff = week >= playoff_start

                home_team = matchup.home_team
                away_team = matchup.away_team

                # Handle Bye weeks
                if not away_team and home_team:
                    paired = [(home_team, None, matchup.home_score, 0.0, matchup.home_lineup)]
                elif not home_team and away_team:
                    paired = [(away_team, None, matchup.away_score, 0.0, matchup.away_lineup)]
                else:
                    paired = [
                        (home_team, away_team, matchup.home_score, matchup.away_score, matchup.home_lineup),
                        (away_team, home_team, matchup.away_score, matchup.home_score, matchup.away_lineup)
                    ]

                for current_team, opp_team, team_score, opp_score, roster in paired:
                    t_id = current_team.team_id
                    opp_id = opp_team.team_id if opp_team else None
                    team_pts = round(float(team_score), 2)
                    opp_pts = round(float(opp_score), 2)

                    is_win = team_pts > opp_pts if opp_team else True
                    is_loss = team_pts < opp_pts if opp_team else False
                    is_tie = team_pts == opp_pts if opp_team else False

                    games_to_insert.append((
                        year, week, game_id, t_id, opp_id,
                        team_pts, 0.0, is_playoff, is_win, is_tie, is_loss
                    ))

                    # Parse player lineups
                    for player in roster:
                        p_id = getattr(player, 'playerId', None)
                        p_name = getattr(player, 'name', 'Unknown')
                        pos = getattr(player, 'position', 'Unknown')
                        slot_pos = getattr(player, 'slot_position', 'BN')
                        p_score = round(float(getattr(player, 'points', 0.0)), 2)
                        p_proj = round(float(getattr(player, 'projected_points', 0.0)), 2)

                        lineups_to_insert.append((
                            year, week, game_id, t_id, p_id, p_name,
                            pos, slot_pos, "Active", p_score, p_proj
                        ))

        cur.executemany(
            """INSERT OR REPLACE INTO games (
                year, week, game_id, team_id, opponent_team_id,
                team_score, team_proj_score, is_playoff, is_win, is_tie, is_loss
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            games_to_insert
        )

        cur.executemany(
            """INSERT OR REPLACE INTO lineups (
                year, week, game_id, team_id, player_id, player_name,
                player_position, player_slot_position, player_status,
                player_score, player_proj_score
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            lineups_to_insert
        )
        print(f" -> Stored {len(games_to_insert)} games and {len(lineups_to_insert)} lineup rows.")

        # 3. Draft Picks
        print("Extracting draft history...")
        draft_picks_to_insert = []
        if hasattr(league, 'draft') and league.draft:
            for pick in league.draft:
                round_num = pick.round_num
                round_pick_no = pick.round_pick
                team_id = pick.team.team_id if hasattr(pick, 'team') and pick.team else 0
                player_id = getattr(pick, 'playerId', None)
                player_name = getattr(pick, 'playerName', 'Unknown')
                
                draft_picks_to_insert.append((
                    int(f"{year}01"),  # synthetic draft_id
                    year,
                    round_num,
                    round_pick_no,
                    team_id,
                    player_id,
                    player_name,
                    "Unknown",
                    "FA",
                    getattr(pick.team, 'owner', f"Team_{team_id}"),
                    getattr(pick.team, 'owner', f"Team_{team_id}")
                ))

            cur.executemany(
                """INSERT OR REPLACE INTO draft_picks (
                    draft_id, year, round, pick_no, team_id,
                    player_id, player_name, position, nfl_team,
                    original_roster, previous_owner
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                draft_picks_to_insert
            )
            print(f" -> Stored {len(draft_picks_to_insert)} draft picks.")

        conn.commit()
        print(f"✓ Season {year} inserted successfully.")

    except Exception as e:
        conn.rollback()
        print(f"Error processing season {year}: {e}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    for yr in [2017, 2018]:
        populate_espn_season(yr)