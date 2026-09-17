import sqlite3
import altair as alt
import numpy as np
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


@st.cache_data(ttl=600)
def load_game_results():
    """Per-team, per-week regular-season result ('Win'/'Loss'/'Tie') plus
    opponent/score detail, used by the weekly results grid below the
    standings table.
    """
    conn = get_connection()
    query = """
        SELECT
            g.year,
            g.week,
            COALESCE(o.real_name, t.owner) AS manager_name,
            COALESCE(oo.real_name, ot.owner) AS opponent_name,
            g.team_score,
            g2.team_score AS opponent_score,
            CASE
                WHEN g.is_tie = 1 THEN 'Tie'
                WHEN g.is_win = 1 THEN 'Win'
                ELSE 'Loss'
            END AS result
        FROM games g
        JOIN teams t ON g.year = t.year AND g.team_id = t.team_id
        LEFT JOIN owners o ON t.owner = o.username
        JOIN games g2 ON g.year = g2.year AND g.week = g2.week AND g.opponent_team_id = g2.team_id
        JOIN teams ot ON g2.year = ot.year AND g2.team_id = ot.team_id
        LEFT JOIN owners oo ON ot.owner = oo.username
        WHERE g.is_playoff = 0
        ORDER BY g.year, g.week;
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    return df


GAME_RESULTS_COLUMNS = [
    "year", "week", "manager_name", "opponent_name", "team_score", "opponent_score", "result",
]


@st.cache_data(ttl=600)
def load_owner_name_map():
    """username -> real_name, for resolving live-season data (which only
    knows Sleeper usernames/display names) to the same real names used
    everywhere else in the app.
    """
    conn = get_connection()
    df = pd.read_sql_query("SELECT DISTINCT username, real_name FROM owners", conn)
    conn.close()
    return dict(zip(df["username"], df["real_name"]))


def resolve_manager_names(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    name_map = load_owner_name_map()
    df = df.copy()
    for col in ("manager_name", "opponent_name"):
        if col in df.columns:
            df[col] = df[col].map(name_map).fillna(df[col])
    return df


@st.cache_data(ttl=300)
def load_live_season_data():
    """Live standings + weekly results for whatever Sleeper season is
    currently in progress, shaped like load_standings()/load_game_results()'s
    output so they can be appended as one more "year" option on the Standings
    tab. Never written to dynasty_data.db -- once the season is complete,
    sleeper_to_db.py (+ the derived-table scripts) makes it permanent history
    and the DB takes over as that year's source.

    Returns (standings_df, game_results_df, metadata) -- metadata has
    year/status/last_played_week -- or (empty df, empty df, None) if no
    games have been played yet this season.
    """
    live = sleeper_live.get_live_season_data()
    standings = resolve_manager_names(sleeper_live.build_live_standings(live))
    metadata = {
        "year": live["year"],
        "status": live.get("status"),
        "last_played_week": live.get("last_played_week"),
    }

    games = live["games"]
    teams = live["teams"]
    reg = games[games["is_playoff"] == 0]
    if not reg.empty and not teams.empty:
        opponent_scores = reg[["team_id", "week", "team_score"]].rename(
            columns={"team_id": "opponent_team_id", "team_score": "opponent_score"}
        )
        reg = reg.merge(opponent_scores, on=["opponent_team_id", "week"], how="left")
        reg = reg.merge(teams[["team_id", "owner"]], on="team_id", how="left")
        reg = reg.rename(columns={"owner": "manager_name"})
        reg = reg.merge(
            teams[["team_id", "owner"]].rename(columns={"team_id": "opponent_team_id", "owner": "opponent_name"}),
            on="opponent_team_id", how="left",
        )
        reg["year"] = live["year"]
        reg["result"] = np.select(
            [reg["is_tie"] == 1, reg["is_win"] == 1],
            ["Tie", "Win"],
            default="Loss",
        )
        game_results = resolve_manager_names(reg[GAME_RESULTS_COLUMNS])
    else:
        game_results = pd.DataFrame(columns=GAME_RESULTS_COLUMNS)

    return standings, game_results, metadata


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


STANDINGS_COLUMN_FORMAT = {
    "W": "{:.0f}",
    "PF": "{:.2f}",
    "PA": "{:.2f}",
    "Max PF": "{:.2f}",
}


def zebra_striped(df: pd.DataFrame):
    """Styler with a faint highlight on every other row, centered headers and
    cells, and STANDINGS_COLUMN_FORMAT number formatting. Rendered via
    st.table rather than st.dataframe -- the latter's grid renderer ignores
    text-align (and most other CSS) from a Styler. st.table's own renderer
    sets its own inline-priority left/right alignment per column dtype, which
    beats a plain Styler rule -- '!important' is needed to actually win.

    The index is blanked out by overwriting its actual labels with empty
    strings (not Styler's .hide(axis="index") / .format_index(), which
    Streamlit's st.table marshalling ignores entirely -- confirmed the index
    column and its header cell render regardless of either). That makes the
    index non-unique, and pandas Styler's .apply()/.map() -- which
    .set_properties() also wraps internally -- refuse to run against a
    non-unique index, so all styling here (striping, alignment) is done as
    plain CSS via set_table_styles instead, with no per-cell/per-row
    Python function.
    """
    df = df.reset_index(drop=True)
    df.index = [""] * len(df)
    format_spec = {col: fmt for col, fmt in STANDINGS_COLUMN_FORMAT.items() if col in df.columns}
    return (
        df.style
        .format(format_spec)
        .set_table_styles([
            {"selector": "th, td", "props": [("text-align", "center !important")]},
            {
                "selector": "tbody tr:nth-child(even)",
                "props": [("background-color", "rgba(255, 255, 255, 0.06)")],
            },
        ])
    )


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
    game_results_df = load_game_results()

    live_standings_df = pd.DataFrame()
    live_game_results_df = pd.DataFrame(columns=GAME_RESULTS_COLUMNS)
    live_meta = None
    try:
        live_standings_df, live_game_results_df, live_meta = load_live_season_data()
    except Exception as e:
        st.warning(f"Couldn't fetch live standings from Sleeper: {e}")

    combined_standings_df = (
        pd.concat([standings_df, live_standings_df], ignore_index=True)
        if not live_standings_df.empty else standings_df
    )
    combined_game_results_df = (
        pd.concat([game_results_df, live_game_results_df], ignore_index=True)
        if not live_game_results_df.empty else game_results_df
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
                f"Live from Sleeper — through week {live_meta['last_played_week']}, "
                f"refreshes every 5 min"
            )

        filtered_standings = combined_standings_df[combined_standings_df["year"] == selected_year].copy()

        if view_mode == "Division Standings":
            for (division_id, division_name), group in filtered_standings.groupby(
                ["division_id", "division_name"], dropna=False
            ):
                st.markdown(f"**{division_name if pd.notna(division_name) else 'No Division'}**")
                group_sorted = group.sort_values(STANDINGS_SORT_COLS, ascending=STANDINGS_SORT_ASC)
                display_group = group_sorted[STANDINGS_DISPLAY_COLS].rename(columns=STANDINGS_DISPLAY_RENAME)
                st.table(zebra_striped(display_group))
        else:
            league_sorted = filtered_standings.sort_values(STANDINGS_SORT_COLS, ascending=STANDINGS_SORT_ASC)
            display_league = league_sorted[STANDINGS_DISPLAY_COLS].rename(columns=STANDINGS_DISPLAY_RENAME)
            st.table(zebra_striped(display_league))

        st.divider()

        chart_order = filtered_standings.sort_values(
            STANDINGS_SORT_COLS, ascending=STANDINGS_SORT_ASC
        )["manager_name"].tolist()

        ppg_chart_col, results_grid_col = st.columns(2)

        with ppg_chart_col:
            st.markdown("**Points Per Game**")
            games_played = filtered_standings["wins"] + filtered_standings["losses"] + filtered_standings["ties"]
            ppg_df = filtered_standings.copy()
            ppg_df["Points Per Game"] = ppg_df["points_for"] / games_played.replace(0, pd.NA)
            ppg_df["Points Per Game Against"] = ppg_df["points_against"] / games_played.replace(0, pd.NA)

            ppg_long = ppg_df.melt(
                id_vars=["manager_name"],
                value_vars=["Points Per Game", "Points Per Game Against"],
                var_name="Stat",
                value_name="Value",
            )

            ppg_chart = (
                alt.Chart(ppg_long)
                .mark_bar()
                .encode(
                    x=alt.X(
                        "manager_name:N", sort=chart_order, title="Owner",
                        axis=alt.Axis(labelAngle=-45, labelOverlap=False),
                    ),
                    xOffset=alt.XOffset("Stat:N", sort=["Points Per Game", "Points Per Game Against"]),
                    y=alt.Y("Value:Q", title="Average Points Per Game"),
                    color=alt.Color(
                        "Stat:N",
                        scale=alt.Scale(
                            domain=["Points Per Game", "Points Per Game Against"],
                            range=["#0072B2", "#E69F00"],
                        ),
                        legend=alt.Legend(title=None),
                    ),
                    tooltip=[
                        alt.Tooltip("manager_name:N", title="Owner"),
                        alt.Tooltip("Stat:N", title="Stat"),
                        alt.Tooltip("Value:Q", title="Value", format=".2f"),
                    ],
                )
            )
            st.altair_chart(ppg_chart, width="stretch")

        with results_grid_col:
            st.markdown("**Weekly Results**")
            year_game_results = combined_game_results_df[combined_game_results_df["year"] == selected_year].copy()
            if not year_game_results.empty:
                year_game_results["letter"] = year_game_results["result"].str[0]

                grid_base = alt.Chart(year_game_results).encode(
                    x=alt.X("week:O", title="Week", axis=alt.Axis(labelAngle=0)),
                    y=alt.Y("manager_name:N", sort=chart_order, title=None),
                )

                grid_rects = grid_base.mark_rect(stroke="#0e1117", strokeWidth=2).encode(
                    color=alt.Color(
                        "result:N",
                        scale=alt.Scale(
                            domain=["Win", "Loss", "Tie"],
                            range=["#2a9147", "#bf2929", "#a6a6a6"],
                        ),
                        legend=alt.Legend(title=None),
                    ),
                    tooltip=[
                        alt.Tooltip("opponent_name:N", title="Opponent"),
                        alt.Tooltip("team_score:Q", title="Score", format=".2f"),
                        alt.Tooltip("opponent_score:Q", title="Opponent Score", format=".2f"),
                    ],
                )
                grid_labels = grid_base.mark_text(fontWeight="bold").encode(
                    text="letter:N",
                    color=alt.condition(alt.datum.result == "Tie", alt.value("black"), alt.value("white")),
                )

                st.altair_chart(grid_rects + grid_labels, width="stretch")
            else:
                st.info("No weekly results available for this season.")

        st.divider()

        whisker_col, h2h_col = st.columns(2)

        with whisker_col:
            st.markdown("**Weekly Scoring Range**")
            if not year_game_results.empty:
                box_chart = alt.Chart(year_game_results).mark_boxplot(
                    extent="min-max", color="#0072B2"
                ).encode(
                    x=alt.X("week:O", title="Week", axis=alt.Axis(labelAngle=0)),
                    y=alt.Y("team_score:Q", title="Points Scored"),
                )

                high_idx = year_game_results.groupby("week")["team_score"].idxmax()
                low_idx = year_game_results.groupby("week")["team_score"].idxmin()
                extremes = pd.concat([
                    year_game_results.loc[high_idx].assign(kind="High"),
                    year_game_results.loc[low_idx].assign(kind="Low"),
                ])

                extreme_color = alt.Color(
                    "kind:N",
                    scale=alt.Scale(domain=["High", "Low"], range=["#2a9147", "#bf2929"]),
                    legend=alt.Legend(title=None),
                )

                extreme_points = alt.Chart(extremes).mark_point(filled=True, size=70).encode(
                    x=alt.X("week:O"),
                    y=alt.Y("team_score:Q"),
                    color=extreme_color,
                    tooltip=[
                        alt.Tooltip("manager_name:N", title="Owner"),
                        alt.Tooltip("team_score:Q", title="Points", format=".2f"),
                        alt.Tooltip("kind:N", title=""),
                    ],
                )
                extreme_labels = alt.Chart(extremes).mark_text(dy=-10, fontSize=9, fontWeight="bold").encode(
                    x=alt.X("week:O"),
                    y=alt.Y("team_score:Q"),
                    text="manager_name:N",
                    color=alt.Color(
                        "kind:N",
                        scale=alt.Scale(domain=["High", "Low"], range=["#2a9147", "#bf2929"]),
                        legend=None,
                    ),
                )

                st.altair_chart(box_chart + extreme_points + extreme_labels, width="stretch")
            else:
                st.info("No weekly scoring data available for this season.")

        with h2h_col:
            st.markdown("**Head-to-Head Records**")
            if not year_game_results.empty:
                h2h_source = year_game_results[
                    year_game_results["manager_name"] != year_game_results["opponent_name"]
                ]
                h2h_agg = h2h_source.groupby(["manager_name", "opponent_name"]).agg(
                    wins=("result", lambda s: (s == "Win").sum()),
                    losses=("result", lambda s: (s == "Loss").sum()),
                    ties=("result", lambda s: (s == "Tie").sum()),
                ).reset_index()

                def _h2h_record(row):
                    if row["wins"] > row["losses"]:
                        return "Winning"
                    if row["losses"] > row["wins"]:
                        return "Losing"
                    return "Even"

                h2h_agg["record"] = h2h_agg.apply(_h2h_record, axis=1)

                h2h_chart = alt.Chart(h2h_agg).mark_rect(
                    stroke="#0e1117", strokeWidth=2
                ).encode(
                    x=alt.X(
                        "opponent_name:N", title=None,
                        scale=alt.Scale(domain=chart_order),
                        axis=alt.Axis(labelAngle=-45, labelOverlap=False),
                    ),
                    y=alt.Y(
                        "manager_name:N", title=None,
                        scale=alt.Scale(domain=chart_order),
                        axis=alt.Axis(labelOverlap=False),
                    ),
                    color=alt.Color(
                        "record:N",
                        scale=alt.Scale(
                            domain=["Winning", "Losing", "Even"],
                            range=["#2a9147", "#bf2929", "#ffffff"],
                        ),
                        legend=alt.Legend(title=None),
                    ),
                    tooltip=[
                        alt.Tooltip("manager_name:N", title="Owner"),
                        alt.Tooltip("opponent_name:N", title="Opponent"),
                        alt.Tooltip("wins:Q", title="Wins"),
                        alt.Tooltip("losses:Q", title="Losses"),
                        alt.Tooltip("ties:Q", title="Ties"),
                    ],
                )
                st.altair_chart(h2h_chart, width="stretch")
            else:
                st.info("No head-to-head data available for this season.")
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