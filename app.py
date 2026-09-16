import sqlite3
import pandas as pd
import streamlit as st

import sleeper_live

DB_PATH = "dynasty_data.db"

st.set_page_config(
    page_title="The Dynasty Historical Register",
    page_icon="🏈",
    layout="wide"
)


def get_connection():
    return sqlite3.connect(DB_PATH, check_same_thread=False)


# --- Database Queries with Streamlit Caching ---
@st.cache_data(ttl=600)
def load_standings():
    conn = get_connection()
    query = """
        SELECT
            s.year,
            s.team_id,
            COALESCE(o.real_name, t.owner) AS manager_name,
            t.team_name,
            d.division_id,
            d.division_name,
            s.wins,
            s.losses,
            s.ties,
            s.points_for,
            s.points_against,
            s.max_pf
        FROM standings s
        JOIN teams t ON s.year = t.year AND s.team_id = t.team_id
        LEFT JOIN owners o ON t.owner = o.username
        LEFT JOIN divisions d ON s.year = d.year AND s.team_id = d.team_id
        WHERE s.week = (SELECT MAX(week) FROM standings s2 WHERE s2.year = s.year)
        ORDER BY s.year DESC, s.wins DESC, s.points_for DESC;
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    return df


@st.cache_data(ttl=600)
def load_manager_efficiency():
    conn = get_connection()
    query = """
        SELECT 
            m.year,
            m.week,
            COALESCE(o.real_name, t.owner) AS manager_name,
            t.team_name,
            m.actual_score,
            m.optimal_score,
            m.efficiency_pct,
            ROUND(m.optimal_score - m.actual_score, 2) AS bench_points_lost
        FROM manager_efficiency m
        JOIN teams t ON m.year = t.year AND m.team_id = t.team_id
        LEFT JOIN owners o ON t.owner = o.username
        ORDER BY m.year DESC, m.week DESC;
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    return df


@st.cache_data(ttl=600)
def load_champions():
    conn = get_connection()
    query = """
        SELECT
            c.year,
            COALESCE(o.real_name, t.owner) AS champion_name
        FROM champions c
        JOIN teams t ON c.year = t.year AND c.champion_team_id = t.team_id
        LEFT JOIN owners o ON t.owner = o.username
        ORDER BY c.year;
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    return df


@st.cache_data(ttl=600)
def load_draft_picks():
    conn = get_connection()
    query = """
        SELECT 
            d.year,
            d.round,
            d.pick_no,
            d.player_name,
            d.position,
            d.nfl_team,
            COALESCE(o.real_name, d.original_roster) AS drafted_by
        FROM draft_picks d
        LEFT JOIN owners o ON d.original_roster = o.username
        ORDER BY d.year DESC, d.round ASC, d.pick_no ASC;
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    return df


@st.cache_data(ttl=600)
def load_expansion_draft():
    conn = get_connection()
    query = """
        SELECT
            e.phase,
            e.round,
            COALESCE(o.real_name, e.owner) AS manager_name,
            e.taken_from,
            COALESCE(p.player_name, 'Player ' || e.player_id) AS player_name,
            p.player_position
        FROM expansion_draft e
        LEFT JOIN (SELECT DISTINCT owner_id, real_name FROM owners) o ON e.owner = o.owner_id
        LEFT JOIN players p ON e.player_id = p.player_id
        ORDER BY e.phase, e.round, manager_name;
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    return df


@st.cache_data(ttl=300)
def load_live_standings():
    """Live standings for whatever Sleeper season is currently in progress,
    shaped like load_standings()'s output (same columns) so it can be
    appended as one more "year" option on the Standings tab. Never written
    to dynasty_data.db -- once the season is complete, sleeper_to_db.py (+
    the derived-table scripts) makes it permanent history and the DB takes
    over as that year's source.

    Returns (standings_df, metadata) -- metadata has year/status/
    last_played_week -- or (empty df, None) if no games have been played
    yet this season.
    """
    live = sleeper_live.get_live_season_data()
    standings = sleeper_live.build_live_standings(live)
    metadata = {
        "year": live["year"],
        "status": live.get("status"),
        "last_played_week": live.get("last_played_week"),
    }
    return standings, metadata


# --- UI & Layout ---
st.markdown(
    """
    <style>
    div[data-baseweb="tab-list"] {
        justify-content: center;
    }
    </style>
    <h1 style='text-align: center;'>The Dynasty Historical Register</h1>
    """,
    unsafe_allow_html=True,
)

(
    tab_standings,
    tab_eff,
    tab_point_records,
    tab_wins_losses,
    tab_h2h,
    tab_draft,
    tab_expansion,
) = st.tabs(
    [
        "Yearly Summary",
        "Manager Efficiency",
        "Point Records",
        "Wins and Losses",
        "Head-to-Head Records",
        "Draft History",
        "Expansion Draft",
    ]
)

# --- TAB: Yearly Summary ---
STANDINGS_SORT_COLS = ["wins", "points_for", "points_against"]
STANDINGS_SORT_ASC = [False, False, True]  # most wins, then most PF, then fewest PA
STANDINGS_DISPLAY_COLS = ["manager_name", "team_name", "wins", "losses", "ties", "points_for", "points_against", "max_pf"]
STANDINGS_DISPLAY_RENAME = {
    "manager_name": "Manager",
    "team_name": "Team",
    "wins": "W",
    "losses": "L",
    "ties": "T",
    "points_for": "PF",
    "points_against": "PA",
    "max_pf": "Max PF",
}


def full_height(df: pd.DataFrame) -> int:
    """Pixel height that fits every row of df with no internal scrollbar."""
    return 38 + 35 * len(df) + 3

with tab_standings:
    champions_df = load_champions()
    if not champions_df.empty:
        st.markdown(
            "<h3 style='text-align:center'><strong><em>Champions</em></strong></h3>",
            unsafe_allow_html=True,
        )
        champ_cols = st.columns(len(champions_df))
        for col, (_, row) in zip(champ_cols, champions_df.iterrows()):
            with col:
                st.markdown(
                    f"<div style='text-align:center'>"
                    f"<div style='font-weight:600'>{row['year']}</div>"
                    f"<div style='font-size:0.85em'>{row['champion_name']}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
        st.divider()

    standings_df = load_standings()

    live_standings_df = pd.DataFrame()
    live_meta = None
    try:
        live_standings_df, live_meta = load_live_standings()
    except Exception as e:
        st.warning(f"Couldn't fetch live standings from Sleeper: {e}")

    combined_standings_df = (
        pd.concat([standings_df, live_standings_df], ignore_index=True)
        if not live_standings_df.empty else standings_df
    )

    if not combined_standings_df.empty:
        years = sorted(combined_standings_df["year"].unique(), reverse=True)
        live_years = set(live_standings_df["year"].unique()) if not live_standings_df.empty else set()

        selected_year = st.selectbox(
            "Select Season", years, key="standings_year",
            format_func=lambda y: f"{y} (Live)" if y in live_years else str(y),
        )
        view_mode = st.radio(
            "View", ["League-Wide Standings", "Division Standings"],
            horizontal=True, key="standings_view_mode"
        )

        if selected_year in live_years and live_meta:
            st.caption(
                f"Live from Sleeper — through week {live_meta['last_played_week']} "
                f"({live_meta['status']}), refreshes every 5 min"
            )

        filtered_standings = combined_standings_df[combined_standings_df["year"] == selected_year].copy()

        # Season KPIs
        col1, col2, col3 = st.columns(3)
        top_pf = filtered_standings.loc[filtered_standings["points_for"].idxmax()]
        col1.metric("Points Leader", top_pf["manager_name"], f"{top_pf['points_for']} PF")

        top_max = filtered_standings.loc[filtered_standings["max_pf"].idxmax()]
        col2.metric("Max PF Leader", top_max["manager_name"], f"{top_max['max_pf']} Max PF")

        most_wins = filtered_standings.loc[filtered_standings["wins"].idxmax()]
        col3.metric("Top Record", most_wins["manager_name"], f"{most_wins['wins']}-{most_wins['losses']}")

        st.divider()

        if view_mode == "Division Standings":
            for (division_id, division_name), group in filtered_standings.groupby(
                ["division_id", "division_name"], dropna=False
            ):
                st.markdown(f"**{division_name if pd.notna(division_name) else 'No Division'}**")
                group_sorted = group.sort_values(STANDINGS_SORT_COLS, ascending=STANDINGS_SORT_ASC)
                display_group = group_sorted[STANDINGS_DISPLAY_COLS].rename(columns=STANDINGS_DISPLAY_RENAME)
                st.dataframe(
                    display_group,
                    use_container_width=True,
                    hide_index=True,
                    height=full_height(display_group),
                )
        else:
            league_sorted = filtered_standings.sort_values(STANDINGS_SORT_COLS, ascending=STANDINGS_SORT_ASC)
            display_league = league_sorted[STANDINGS_DISPLAY_COLS].rename(columns=STANDINGS_DISPLAY_RENAME)
            st.dataframe(
                display_league,
                use_container_width=True,
                hide_index=True,
                height=full_height(display_league),
            )
    else:
        st.info("No standings records found in the database.")

# --- TAB: Manager Efficiency ---
with tab_eff:
    eff_df = load_manager_efficiency()
    if not eff_df.empty:
        eff_years = sorted(eff_df["year"].unique(), reverse=True)
        selected_eff_year = st.selectbox("Select Season", eff_years, key="eff_year")
        year_eff = eff_df[eff_df["year"] == selected_eff_year]

        # Aggregate season manager skill
        manager_summary = year_eff.groupby("manager_name").agg(
            avg_efficiency=("efficiency_pct", "mean"),
            total_points_lost=("bench_points_lost", "sum")
        ).reset_index().sort_values(by="avg_efficiency", ascending=False)

        st.subheader("Season Lineup Setting Efficiency")
        st.dataframe(
            manager_summary.rename(columns={
                "manager_name": "Manager",
                "avg_efficiency": "Avg Efficiency %",
                "total_points_lost": "Total Bench Points Wasted"
            }).style.format({
                "Avg Efficiency %": "{:.2f}%",
                "Total Bench Points Wasted": "{:.2f}"
            }),
            use_container_width=True,
            hide_index=True
        )

        st.divider()
        st.subheader("Weekly Breakdown")
        st.dataframe(
            year_eff[[
                "week", "manager_name", "actual_score", "optimal_score", "efficiency_pct", "bench_points_lost"
            ]].rename(columns={
                "week": "Week",
                "manager_name": "Manager",
                "actual_score": "Actual Score",
                "optimal_score": "Optimal Score",
                "efficiency_pct": "Efficiency %",
                "bench_points_lost": "Bench Pts Lost"
            }),
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info("No manager efficiency records found in the database.")

# --- TAB: Point Records ---
with tab_point_records:
    st.info("Coming soon.")

# --- TAB: Wins and Losses ---
with tab_wins_losses:
    st.info("Coming soon.")

# --- TAB: Head-to-Head Records ---
with tab_h2h:
    st.info("Coming soon.")

# --- TAB: Draft History ---
with tab_draft:
    draft_df = load_draft_picks()
    if not draft_df.empty:
        draft_years = sorted(draft_df["year"].unique(), reverse=True)
        selected_draft_year = st.selectbox("Select Draft Season", draft_years, key="draft_year")
        
        filtered_draft = draft_df[draft_df["year"] == selected_draft_year]
        st.dataframe(
            filtered_draft[[
                "round", "pick_no", "drafted_by", "player_name", "position", "nfl_team"
            ]].rename(columns={
                "round": "Round",
                "pick_no": "Pick",
                "drafted_by": "Manager",
                "player_name": "Player",
                "position": "Pos",
                "nfl_team": "NFL Team"
            }),
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info("No draft records found in the database.")

# --- TAB: Expansion Draft ---
with tab_expansion:
    expansion_df = load_expansion_draft()
    if not expansion_df.empty:
        phases = list(expansion_df["phase"].unique())
        selected_phase = st.selectbox("Select Phase", phases, key="expansion_phase")

        filtered_expansion = expansion_df[expansion_df["phase"] == selected_phase]
        st.dataframe(
            filtered_expansion[[
                "round", "manager_name", "taken_from", "player_name", "player_position"
            ]].rename(columns={
                "round": "Round",
                "manager_name": "Manager",
                "taken_from": "Taken From",
                "player_name": "Player",
                "player_position": "Pos",
            }),
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info("No expansion draft records found in the database.")