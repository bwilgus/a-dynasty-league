import sqlite3
import pandas as pd
import streamlit as st

DB_PATH = "dynasty_data.db"

st.set_page_config(
    page_title="Dynasty League Hub",
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
            COALESCE(o.real_name, t.owner) AS manager_name,
            t.team_name,
            s.wins,
            s.losses,
            s.ties,
            s.points_for,
            s.points_against,
            s.max_pf
        FROM standings s
        JOIN teams t ON s.year = t.year AND s.team_id = t.team_id
        LEFT JOIN owners o ON t.owner = o.username
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


# --- UI & Layout ---
st.title("🏈 Dynasty League Dashboard")

tab_standings, tab_eff, tab_draft = st.tabs(["Standings & Records", "Manager Efficiency", "Rookie Drafts"])

# --- TAB 1: Standings ---
with tab_standings:
    standings_df = load_standings()
    if not standings_df.empty:
        years = sorted(standings_df["year"].unique(), reverse=True)
        selected_year = st.selectbox("Select Season", years, key="standings_year")
        
        filtered_standings = standings_df[standings_df["year"] == selected_year].copy()
        
        # Season KPIs
        col1, col2, col3 = st.columns(3)
        top_pf = filtered_standings.loc[filtered_standings["points_for"].idxmax()]
        col1.metric("Points Leader", top_pf["manager_name"], f"{top_pf['points_for']} PF")

        top_max = filtered_standings.loc[filtered_standings["max_pf"].idxmax()]
        col2.metric("Max PF Leader", top_max["manager_name"], f"{top_max['max_pf']} Max PF")

        most_wins = filtered_standings.loc[filtered_standings["wins"].idxmax()]
        col3.metric("Top Record", most_wins["manager_name"], f"{most_wins['wins']}-{most_wins['losses']}")

        st.divider()
        
        display_standings = filtered_standings[[
            "manager_name", "team_name", "wins", "losses", "ties", "points_for", "points_against", "max_pf"
        ]].rename(columns={
            "manager_name": "Manager",
            "team_name": "Team",
            "wins": "W",
            "losses": "L",
            "ties": "T",
            "points_for": "PF",
            "points_against": "PA",
            "max_pf": "Max PF"
        })
        st.dataframe(display_standings, use_container_width=True, hide_index=True)
    else:
        st.info("No standings records found in the database.")

# --- TAB 2: Manager Efficiency ---
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

# --- TAB 3: Rookie Drafts ---
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