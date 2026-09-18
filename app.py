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
def load_all_games_detail():
    """Every game (regular season and playoff) with opponent/score detail
    and the years.modern_era flag, for the Point Records tab. Unlike
    load_game_results(), this is not restricted to regular season -- the
    Point Records filters need to be able to isolate playoff games too.
    """
    conn = get_connection()
    query = """
        SELECT
            g.year,
            g.week,
            g.is_playoff,
            COALESCE(o.real_name, t.owner) AS manager_name,
            COALESCE(oo.real_name, ot.owner) AS opponent_name,
            g.team_score,
            g2.team_score AS opponent_score,
            y.modern_era
        FROM games g
        JOIN teams t ON g.year = t.year AND g.team_id = t.team_id
        LEFT JOIN owners o ON t.owner = o.username
        JOIN games g2 ON g.year = g2.year AND g.week = g2.week AND g.opponent_team_id = g2.team_id
        JOIN teams ot ON g2.year = ot.year AND g2.team_id = ot.team_id
        LEFT JOIN owners oo ON ot.owner = oo.username
        LEFT JOIN years y ON CAST(g.year AS TEXT) = y.year
        ORDER BY g.year, g.week;
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    df["is_playoff"] = df["is_playoff"].astype(bool)
    df["modern_era"] = df["modern_era"].astype(bool)
    return df


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
    table.standings-table {
        width: 100%;
        border-collapse: collapse;
    }
    table.standings-table th, table.standings-table td {
        padding: 8px 12px;
        border-bottom: 1px solid rgba(255, 255, 255, 0.15);
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
    "PF": "{:,.2f}",
    "PA": "{:,.2f}",
    "Max PF": "{:,.2f}",
}


def zebra_striped(df: pd.DataFrame):
    """Styler with a faint highlight on every other row, centered headers and
    cells, and STANDINGS_COLUMN_FORMAT number formatting.

    Rendered as raw HTML via st.markdown (see render_standings_table) rather
    than st.table or st.dataframe: st.dataframe's grid renderer ignores
    text-align from a Styler entirely, and st.table's Styler marshalling
    both ignores .hide(axis="index") (the index column renders regardless)
    and applies its own left/right text-align that beats a plain Styler
    rule. Neither is fixable from the Styler side -- raw HTML has neither
    problem, so .hide(axis="index") actually works and plain text-align
    (no !important) is enough.
    """
    df = df.reset_index(drop=True)

    def _stripe(row):
        style = "background-color: rgba(255, 255, 255, 0.06)" if row.name % 2 else ""
        return [style] * len(row)

    format_spec = {col: fmt for col, fmt in STANDINGS_COLUMN_FORMAT.items() if col in df.columns}
    return (
        df.style
        .hide(axis="index")
        .apply(_stripe, axis=1)
        .format(format_spec)
        .set_properties(**{"text-align": "center"})
        .set_table_styles([{"selector": "th", "props": [("text-align", "center")]}])
        .set_table_attributes('class="standings-table"')
    )


def render_standings_table(df: pd.DataFrame):
    # Wrapped in a horizontally-scrollable div: a raw HTML table sizes to
    # its content and will otherwise overflow past its container (bleeding
    # into a neighboring st.columns() table) instead of wrapping/shrinking.
    st.markdown(
        f'<div style="overflow-x: auto;">{zebra_striped(df).to_html()}</div>',
        unsafe_allow_html=True,
    )


def comma_format(df: pd.DataFrame, columns: list) -> pd.DataFrame:
    """Formats the given point-value columns as comma-grouped, fixed
    2-decimal strings (e.g. 19,901.84) -- trades away numeric click-to-sort
    on just these columns, which is acceptable here since each table is
    already ranked by its defining metric.
    """
    df = df.copy()
    for col in columns:
        df[col] = df[col].map("{:,.2f}".format)
    return df


def render_centered_table(df: pd.DataFrame):
    """Renders a DataFrame as a plain, centered HTML table (headers and
    cells), the same way render_standings_table does -- st.dataframe's grid
    renderer ignores text-align from a Styler entirely, so centering only
    actually works via raw HTML. Trades away st.dataframe's native
    click-to-sort, scrolling, and boolean-checkbox rendering (booleans show
    as plain True/False text instead).
    """
    styler = (
        df.style
        .hide(axis="index")
        .set_table_styles([{"selector": "th, td", "props": [("text-align", "center")]}])
        .set_table_attributes('class="standings-table"')
    )
    st.markdown(f'<div style="overflow-x: auto;">{styler.to_html()}</div>', unsafe_allow_html=True)


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

        sort_col_1, sort_col_2 = st.columns(2)
        sort_by = sort_col_1.selectbox(
            "Sort by", list(STANDINGS_DISPLAY_RENAME.values()),
            index=list(STANDINGS_DISPLAY_RENAME.values()).index("W"),
            key="standings_sort_by",
        )
        sort_order = sort_col_2.radio(
            "Order", ["Descending", "Ascending"], horizontal=True, key="standings_sort_order",
        )
        sort_ascending = sort_order == "Ascending"

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
                display_group = group[STANDINGS_DISPLAY_COLS].rename(columns=STANDINGS_DISPLAY_RENAME)
                display_group = display_group.sort_values(sort_by, ascending=sort_ascending)
                render_standings_table(display_group)
        else:
            display_league = filtered_standings[STANDINGS_DISPLAY_COLS].rename(columns=STANDINGS_DISPLAY_RENAME)
            display_league = display_league.sort_values(sort_by, ascending=sort_ascending)
            render_standings_table(display_league)

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
    games_detail_df = load_all_games_detail()

    if games_detail_df.empty:
        st.info("No game records found in the database.")
    else:
        pr_col1, pr_col2, pr_col3, pr_col4, pr_col5, pr_col6 = st.columns(6)
        pr_season_type = pr_col1.selectbox(
            "Season Type", ["All", "Regular Season", "Playoff"], key="pr_season_type"
        )
        pr_modern_era = pr_col2.selectbox(
            "Modern Era", ["All", "True", "False"], key="pr_modern_era"
        )
        pr_direction = pr_col3.radio(
            "Highest/Lowest", ["Highest", "Lowest"], horizontal=True, key="pr_direction"
        )
        pr_top_n = pr_col4.selectbox(
            "Highest/Lowest N", [5, 10, 25, 50, 100], index=1, key="pr_top_n"
        )
        max_seasons = games_detail_df["year"].nunique()
        pr_min_seasons = pr_col5.number_input(
            "Minimum Seasons", min_value=0, max_value=max_seasons, value=0, step=1, key="pr_min_seasons",
        )
        all_years = sorted(games_detail_df["year"].unique())
        pr_seasons = pr_col6.multiselect(
            "Seasons", all_years, default=[], placeholder="All seasons", key="pr_seasons",
        )
        pr_ascending = pr_direction == "Lowest"

        pr_filtered = games_detail_df.copy()
        if pr_seasons:
            pr_filtered = pr_filtered[pr_filtered["year"].isin(pr_seasons)]
        if pr_season_type == "Regular Season":
            pr_filtered = pr_filtered[~pr_filtered["is_playoff"]]
        elif pr_season_type == "Playoff":
            pr_filtered = pr_filtered[pr_filtered["is_playoff"]]
        if pr_modern_era != "All":
            pr_filtered = pr_filtered[pr_filtered["modern_era"] == (pr_modern_era == "True")]

        if pr_min_seasons > 0:
            seasons_played_map = pr_filtered.groupby("manager_name")["year"].nunique()
            qualified_owners = seasons_played_map[seasons_played_map >= pr_min_seasons].index
            pr_filtered = pr_filtered[pr_filtered["manager_name"].isin(qualified_owners)]

        st.divider()

        # --- Points All Time ---
        all_time = pr_filtered.groupby("manager_name").agg(
            seasons_played=("year", "nunique"),
            points_for=("team_score", "sum"),
            points_against=("opponent_score", "sum"),
        ).reset_index()
        all_time["points_differential"] = all_time["points_for"] - all_time["points_against"]
        all_time = all_time.sort_values("points_for", ascending=pr_ascending).head(pr_top_n).reset_index(drop=True)
        all_time.insert(0, "Rank", range(1, len(all_time) + 1))
        all_time = all_time.round(2).rename(columns={
            "manager_name": "Owner",
            "seasons_played": "Seasons Played",
            "points_for": "Points For",
            "points_against": "Points Against",
            "points_differential": "Margin",
        })
        all_time = comma_format(all_time, ["Points For", "Points Against", "Margin"])

        # --- Points Against All Time ---
        against_all_time = pr_filtered.groupby("manager_name").agg(
            seasons_played=("year", "nunique"),
            points_for=("team_score", "sum"),
            points_against=("opponent_score", "sum"),
        ).reset_index()
        against_all_time["points_differential"] = against_all_time["points_for"] - against_all_time["points_against"]
        against_all_time = against_all_time.sort_values(
            "points_against", ascending=pr_ascending
        ).head(pr_top_n).reset_index(drop=True)
        against_all_time.insert(0, "Rank", range(1, len(against_all_time) + 1))
        against_all_time = against_all_time.round(2).rename(columns={
            "manager_name": "Owner",
            "seasons_played": "Seasons Played",
            "points_for": "Points For",
            "points_against": "Points Against",
            "points_differential": "Margin",
        })
        against_all_time = comma_format(against_all_time, ["Points For", "Points Against", "Margin"])

        # --- Points Per Game ---
        per_game = pr_filtered.groupby("manager_name").agg(
            seasons_played=("year", "nunique"),
            games_played=("year", "size"),
            points_for=("team_score", "sum"),
            points_against=("opponent_score", "sum"),
        ).reset_index()
        per_game["points_for"] = per_game["points_for"] / per_game["games_played"]
        per_game["points_against"] = per_game["points_against"] / per_game["games_played"]
        per_game["points_differential"] = per_game["points_for"] - per_game["points_against"]
        per_game = per_game.sort_values("points_for", ascending=pr_ascending).head(pr_top_n).reset_index(drop=True)
        per_game.insert(0, "Rank", range(1, len(per_game) + 1))
        per_game = per_game.round(2).rename(columns={
            "manager_name": "Owner",
            "seasons_played": "Seasons Played",
            "points_for": "Points For",
            "points_against": "Points Against",
            "points_differential": "Margin",
        })
        per_game = comma_format(per_game, ["Points For", "Points Against", "Margin"])

        # --- Points Per Game Against ---
        per_game_against = pr_filtered.groupby("manager_name").agg(
            seasons_played=("year", "nunique"),
            games_played=("year", "size"),
            points_for=("team_score", "sum"),
            points_against=("opponent_score", "sum"),
        ).reset_index()
        per_game_against["points_for"] = per_game_against["points_for"] / per_game_against["games_played"]
        per_game_against["points_against"] = per_game_against["points_against"] / per_game_against["games_played"]
        per_game_against["points_differential"] = per_game_against["points_for"] - per_game_against["points_against"]
        per_game_against = per_game_against.sort_values(
            "points_against", ascending=pr_ascending
        ).head(pr_top_n).reset_index(drop=True)
        per_game_against.insert(0, "Rank", range(1, len(per_game_against) + 1))
        per_game_against = per_game_against.round(2).rename(columns={
            "manager_name": "Owner",
            "seasons_played": "Seasons Played",
            "points_for": "Points For",
            "points_against": "Points Against",
            "points_differential": "Margin",
        })
        per_game_against = comma_format(per_game_against, ["Points For", "Points Against", "Margin"])

        # --- Highest Single Season Scores ---
        season_scores = pr_filtered.groupby(["manager_name", "year"]).agg(
            season_points=("team_score", "sum"),
            points_against=("opponent_score", "sum"),
        ).reset_index()
        season_scores["points_differential"] = season_scores["season_points"] - season_scores["points_against"]
        season_scores = season_scores.sort_values(
            "season_points", ascending=pr_ascending
        ).head(pr_top_n).reset_index(drop=True)
        season_scores.insert(0, "Rank", range(1, len(season_scores) + 1))
        season_scores = season_scores.round(2).rename(columns={
            "season_points": "Season Points",
            "manager_name": "Owner",
            "year": "Year",
            "points_against": "Points Against",
            "points_differential": "Margin",
        })
        season_scores = comma_format(season_scores, ["Season Points", "Points Against", "Margin"])

        # --- Single Season Points Against ---
        season_scores_against = pr_filtered.groupby(["manager_name", "year"]).agg(
            season_points=("team_score", "sum"),
            points_against=("opponent_score", "sum"),
        ).reset_index()
        season_scores_against["points_differential"] = (
            season_scores_against["season_points"] - season_scores_against["points_against"]
        )
        season_scores_against = season_scores_against.sort_values(
            "points_against", ascending=pr_ascending
        ).head(pr_top_n).reset_index(drop=True)
        season_scores_against.insert(0, "Rank", range(1, len(season_scores_against) + 1))
        season_scores_against = season_scores_against.round(2).rename(columns={
            "points_against": "Season Points Against",
            "manager_name": "Owner",
            "year": "Year",
            "season_points": "Season Points",
            "points_differential": "Margin",
        })
        season_scores_against = comma_format(
            season_scores_against, ["Season Points Against", "Season Points", "Margin"]
        )

        # --- Highest Single Game Score ---
        game_scores = pr_filtered.copy()
        game_scores["points_differential"] = game_scores["team_score"] - game_scores["opponent_score"]
        game_scores = game_scores.sort_values(
            "team_score", ascending=pr_ascending
        ).head(pr_top_n).reset_index(drop=True)
        game_scores.insert(0, "Rank", range(1, len(game_scores) + 1))
        game_scores = game_scores.round(2).rename(columns={
            "team_score": "Points",
            "manager_name": "Owner",
            "opponent_name": "Opponent",
            "year": "Year",
            "week": "Week",
            "is_playoff": "Playoff Game?",
            "opponent_score": "Points Against",
            "points_differential": "Margin",
        })
        game_scores = comma_format(game_scores, ["Points", "Points Against", "Margin"])

        # --- Single Game Score Combined ---
        # Each physical game has a mirrored row (one per team's perspective)
        # with an identical combined total -- collapsed down to a single row
        # per physical game, same as Tie Games.
        combined_scores = pr_filtered.copy()
        combined_scores["combined_points"] = combined_scores["team_score"] + combined_scores["opponent_score"]
        combined_scores["_pair_key"] = combined_scores.apply(
            lambda r: tuple(sorted([r["manager_name"], r["opponent_name"]])), axis=1
        )
        combined_scores = combined_scores.drop_duplicates(
            subset=["year", "week", "_pair_key"]
        ).drop(columns="_pair_key")
        combined_scores = combined_scores.sort_values(
            "combined_points", ascending=pr_ascending
        ).head(pr_top_n).reset_index(drop=True)
        combined_scores.insert(0, "Rank", range(1, len(combined_scores) + 1))
        combined_scores = combined_scores.round(2).rename(columns={
            "combined_points": "Points",
            "manager_name": "Owner",
            "opponent_name": "Opponent",
            "year": "Year",
            "week": "Week",
            "is_playoff": "Playoff Game?",
        })
        combined_scores = comma_format(combined_scores, ["Points"])

        # --- Points In a Loss ---
        loss_scores = pr_filtered[pr_filtered["team_score"] < pr_filtered["opponent_score"]].copy()
        loss_scores["points_differential"] = loss_scores["team_score"] - loss_scores["opponent_score"]
        loss_scores = loss_scores.sort_values(
            "team_score", ascending=pr_ascending
        ).head(pr_top_n).reset_index(drop=True)
        loss_scores.insert(0, "Rank", range(1, len(loss_scores) + 1))
        loss_scores = loss_scores.round(2).rename(columns={
            "team_score": "Points",
            "manager_name": "Owner",
            "opponent_name": "Opponent",
            "year": "Year",
            "week": "Week",
            "is_playoff": "Playoff Game?",
            "opponent_score": "Points Against",
            "points_differential": "Margin",
        })
        loss_scores = comma_format(loss_scores, ["Points", "Points Against", "Margin"])

        # --- Points In a Win ---
        win_scores = pr_filtered[pr_filtered["team_score"] > pr_filtered["opponent_score"]].copy()
        win_scores["points_differential"] = win_scores["team_score"] - win_scores["opponent_score"]
        win_scores = win_scores.sort_values(
            "team_score", ascending=pr_ascending
        ).head(pr_top_n).reset_index(drop=True)
        win_scores.insert(0, "Rank", range(1, len(win_scores) + 1))
        win_scores = win_scores.round(2).rename(columns={
            "team_score": "Points",
            "manager_name": "Owner",
            "opponent_name": "Opponent",
            "year": "Year",
            "week": "Week",
            "is_playoff": "Playoff Game?",
            "opponent_score": "Points Against",
            "points_differential": "Margin",
        })
        win_scores = comma_format(win_scores, ["Points", "Points Against", "Margin"])

        # --- Highest Point Margins ---
        margins = pr_filtered.copy()
        margins["margin"] = margins["team_score"] - margins["opponent_score"]
        margins = margins[margins["margin"] > 0]
        margins = margins.sort_values("margin", ascending=pr_ascending).head(pr_top_n).reset_index(drop=True)
        margins.insert(0, "Rank", range(1, len(margins) + 1))
        margins = margins.round(2).rename(columns={
            "margin": "Margin",
            "manager_name": "Owner",
            "opponent_name": "Opponent",
            "year": "Year",
            "week": "Week",
            "is_playoff": "Playoff Game?",
            "team_score": "Points For",
            "opponent_score": "Points Against",
        })
        margins = comma_format(margins, ["Margin", "Points For", "Points Against"])

        # --- Tie Games ---
        # A copy of Point Margin, but agnostic of the filters above (uses
        # games_detail_df directly, not pr_filtered), no Rank column, and
        # restricted to margin == 0. Each tied game has a mirrored row (one
        # per team's perspective, both with identical scores) -- collapsed
        # down to a single row per physical game.
        tie_games = games_detail_df.copy()
        tie_games["margin"] = tie_games["team_score"] - tie_games["opponent_score"]
        tie_games = tie_games[tie_games["margin"] == 0]
        tie_games["_pair_key"] = tie_games.apply(
            lambda r: tuple(sorted([r["manager_name"], r["opponent_name"]])), axis=1
        )
        tie_games = tie_games.drop_duplicates(subset=["year", "week", "_pair_key"]).drop(columns="_pair_key")
        tie_games = tie_games.sort_values(["year", "week"]).reset_index(drop=True)
        if not tie_games.empty:
            tie_games = tie_games.round(2).rename(columns={
                "margin": "Margin",
                "manager_name": "Owner",
                "opponent_name": "Opponent",
                "year": "Year",
                "week": "Week",
                "is_playoff": "Playoff Game?",
                "team_score": "Points For",
                "opponent_score": "Points Against",
            })
            tie_games = comma_format(tie_games, ["Margin", "Points For", "Points Against"])

        def show_table(container, title, df, columns, empty_message):
            with container:
                st.markdown(f"**{title}**")
                if df.empty:
                    st.info(empty_message)
                else:
                    render_centered_table(df[columns])

        row1_col1, row1_col2 = st.columns(2)
        show_table(
            row1_col1, "Points All Time", all_time,
            ["Rank", "Owner", "Seasons Played", "Points For", "Points Against", "Margin"],
            "No games match the selected filters.",
        )
        show_table(
            row1_col2, "Points Against All Time", against_all_time,
            ["Rank", "Owner", "Seasons Played", "Points Against", "Points For", "Margin"],
            "No games match the selected filters.",
        )

        row2_col1, row2_col2 = st.columns(2)
        show_table(
            row2_col1, "Points Per Game", per_game,
            ["Rank", "Owner", "Seasons Played", "Points For", "Points Against", "Margin"],
            "No games match the selected filters.",
        )
        show_table(
            row2_col2, "Points Per Game Against", per_game_against,
            ["Rank", "Owner", "Seasons Played", "Points Against", "Points For", "Margin"],
            "No games match the selected filters.",
        )

        row3_col1, row3_col2 = st.columns(2)
        show_table(
            row3_col1, "Single Season Points", season_scores,
            ["Rank", "Season Points", "Owner", "Year", "Points Against", "Margin"],
            "No games match the selected filters.",
        )
        show_table(
            row3_col2, "Single Season Points Against", season_scores_against,
            ["Rank", "Season Points Against", "Owner", "Year", "Season Points", "Margin"],
            "No games match the selected filters.",
        )

        row4_col1, row4_col2 = st.columns(2)
        show_table(
            row4_col1, "Single Game Points - Team", game_scores,
            ["Rank", "Points", "Owner", "Opponent", "Year", "Week", "Playoff Game?", "Points Against", "Margin"],
            "No games match the selected filters.",
        )
        show_table(
            row4_col2, "Single Games Points Combined", combined_scores,
            ["Rank", "Points", "Owner", "Opponent", "Year", "Week", "Playoff Game?"],
            "No games match the selected filters.",
        )

        row5_col1, row5_col2 = st.columns(2)
        show_table(
            row5_col1, "Points In a Loss", loss_scores,
            ["Rank", "Points", "Owner", "Opponent", "Year", "Week", "Playoff Game?", "Points Against", "Margin"],
            "No games match the selected filters.",
        )
        show_table(
            row5_col2, "Points In a Win", win_scores,
            ["Rank", "Points", "Owner", "Opponent", "Year", "Week", "Playoff Game?", "Points Against", "Margin"],
            "No games match the selected filters.",
        )

        row6_col1, row6_col2 = st.columns(2)
        show_table(
            row6_col1, "Point Margin", margins,
            ["Rank", "Margin", "Owner", "Opponent", "Year", "Week", "Playoff Game?", "Points For", "Points Against"],
            "No games match the selected filters.",
        )
        show_table(
            row6_col2, "Tie Games", tie_games,
            ["Margin", "Owner", "Opponent", "Year", "Week", "Playoff Game?", "Points For", "Points Against"],
            "No tie games found.",
        )

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