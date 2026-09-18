"""Live, non-persisted view of whatever Sleeper season is currently in progress.

Reuses fetch_league_dataframes() from sleeper_to_db.py -- the exact same
row-building logic the ingestion script uses to populate dynasty_data.db --
so the live data is shaped identically to the historical tables, but this
module never opens or writes to the SQLite file. Once a season finishes, run
sleeper_to_db.py (and the derived-table scripts: build_standings.py,
backfill_manager_efficiency.py, build_playoff_results.py, etc.) to make it
permanent/historical; until then, this is how the app shows that season's
in-progress data.

This will keep working unmodified as new seasons roll over, since it walks
Sleeper's previous_league_id chain (via backfill_pick_transactions.get_sleeper_league_ids)
starting from the startup league in config.py rather than hardcoding a
season's league_id.
"""

from typing import Any, Dict, Tuple

import pandas as pd

from backfill_pick_transactions import get_sleeper_league_ids
from sleeper_to_db import SLEEPER_BASE_URL, fetch_json, fetch_league_dataframes


def get_current_league_id() -> Tuple[str, Dict[str, Any]]:
    """Returns (league_id, league_info) for the furthest-along season in the
    chain rooted at config.sleeper_league_id -- i.e. the current season,
    whether it's in_season, pre_draft, or complete.
    """
    league_ids = get_sleeper_league_ids()
    _, league_id = max(league_ids, key=lambda pair: pair[0])
    league_info = fetch_json(f"{SLEEPER_BASE_URL}/league/{league_id}")
    return league_id, league_info


def get_last_completed_nfl_week() -> int:
    """The most recent NFL week whose games are all finished, per Sleeper's
    global state endpoint. Sleeper advances state["week"] at the start of
    the next real-world week (early Tuesday, right after Monday Night
    Football) -- so state["week"] - 1 is the last week guaranteed to be
    fully final. Used to exclude the current, possibly still in-progress,
    week from live scoring (see get_live_season_data()).
    """
    state = fetch_json(f"{SLEEPER_BASE_URL}/state/nfl")
    return int(state.get("week", 0)) - 1


def get_live_season_data() -> Dict[str, Any]:
    """Fetches the current season's data in the same shape as dynasty_data.db.

    Callers should cache this behind a short TTL (e.g. st.cache_data(ttl=300))
    -- a single call makes dozens of Sleeper API requests (one per week of
    matchups/transactions, plus draft/roster/player lookups).
    """
    league_id, league_info = get_current_league_id()
    frames = fetch_league_dataframes(league_id)

    # Sleeper pre-populates matchups for the rest of the season on a fixed
    # schedule, so unplayed future weeks still show up as real (zero-score)
    # rows rather than being absent. Trim any week with no points scored by
    # anyone yet -- otherwise every team looks like it has a pile of 0-0 ties.
    games = frames["games"]
    if not games.empty:
        week_totals = games.groupby("week")["team_score"].sum()
        played_weeks = week_totals[week_totals > 0].index
        last_played_week = int(played_weeks.max()) if len(played_weeks) else 0
    else:
        last_played_week = 0

    # The points-scored check above still lets a week in, in progress
    # through, e.g. a Thursday-night game reports a nonzero score for two
    # teams while the rest of that week's games haven't kicked off -- which
    # would otherwise hand out win/loss records off unfinished matchups.
    # Cap at the last NFL week Sleeper considers fully complete.
    last_played_week = min(last_played_week, get_last_completed_nfl_week())

    frames["games"] = games[games["week"] <= last_played_week].reset_index(drop=True)
    frames["lineups"] = frames["lineups"][frames["lineups"]["week"] <= last_played_week].reset_index(drop=True)
    frames["manager_efficiency"] = frames["manager_efficiency"][
        frames["manager_efficiency"]["week"] <= last_played_week
    ].reset_index(drop=True)

    frames["league_id"] = league_id
    frames["status"] = league_info.get("status")
    frames["name"] = league_info.get("name")
    frames["last_played_week"] = last_played_week
    return frames


def build_live_standings(frames: Dict[str, Any]) -> pd.DataFrame:
    """Cumulative regular-season standings computed in-memory from the live
    games/manager_efficiency/teams/divisions DataFrames, mirroring
    build_standings.py's logic (regular-season-only, i.e. games.is_playoff
    == 0; max_pf is the running sum of weekly optimal_score).

    Column names/order match app.py's load_standings() exactly (year,
    team_id, manager_name, team_name, division_id, division_name, wins,
    losses, ties, points_for, points_against, max_pf) so the two can be
    pd.concat()-ed into one dropdown/table.
    """
    games = frames["games"]
    eff = frames["manager_efficiency"]
    teams = frames["teams"]
    divisions = frames["divisions"]

    columns = [
        "year", "team_id", "manager_name", "team_name",
        "division_id", "division_name",
        "wins", "losses", "ties", "points_for", "points_against", "max_pf",
    ]
    reg = games[games["is_playoff"] == 0]
    if reg.empty or teams.empty:
        return pd.DataFrame(columns=columns)

    agg = reg.groupby("team_id").agg(
        wins=("is_win", "sum"),
        losses=("is_loss", "sum"),
        ties=("is_tie", "sum"),
        points_for=("team_score", "sum"),
    ).reset_index()

    points_against = (
        reg.groupby("opponent_team_id")["team_score"]
        .sum()
        .rename("points_against")
        .reset_index()
        .rename(columns={"opponent_team_id": "team_id"})
    )
    agg = agg.merge(points_against, on="team_id", how="left")
    agg["points_against"] = agg["points_against"].fillna(0.0)

    if not eff.empty:
        max_pf = eff.groupby("team_id")["optimal_score"].sum().rename("max_pf").reset_index()
        agg = agg.merge(max_pf, on="team_id", how="left")
    else:
        agg["max_pf"] = None

    result = teams[["team_id", "owner", "team_name"]].merge(agg, on="team_id", how="left")
    if not divisions.empty:
        result = result.merge(divisions[["team_id", "division_id", "division_name"]], on="team_id", how="left")
    else:
        result["division_id"] = None
        result["division_name"] = None

    for col in ("wins", "losses", "ties"):
        result[col] = result[col].fillna(0).astype(int)
    result["points_for"] = result["points_for"].fillna(0.0).round(2)
    result["points_against"] = result["points_against"].fillna(0.0).round(2)
    result["max_pf"] = result["max_pf"].round(2)
    result["year"] = frames["year"]
    result = result.rename(columns={"owner": "manager_name"})

    return result[columns].sort_values(["wins", "points_for"], ascending=False).reset_index(drop=True)


if __name__ == "__main__":
    league_id, league_info = get_current_league_id()
    print(f"Current league: {league_info.get('name')} ({league_id}) -- season {league_info.get('season')}, status {league_info.get('status')}")

    data = get_live_season_data()
    print(f"Teams: {len(data['teams'])}, Games: {len(data['games'])}, Lineups: {len(data['lineups'])}, "
          f"Transactions: {len(data['transactions'])}, Draft picks: {len(data['draft_picks'])}")

    print("\nLive standings:")
    print(build_live_standings(data).to_string(index=False))
