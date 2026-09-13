# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A personal fantasy football dynasty league project: a set of standalone Python scripts that scrape/ingest league history into a single SQLite database (`dynasty_data.db`), plus a Streamlit dashboard (`app.py`) that reads from it. There is no test suite, build step, or package structure — every `.py` file in the root is a self-contained script run directly with `python`.

## Commands

```bash
pip install -r requirements.txt   # streamlit, pandas, espn-api, matplotlib
streamlit run app.py              # launch the dashboard
python sleeper_to_db.py           # re-ingest current Sleeper season (see below)
python backfill_manager_efficiency.py   # recompute manager_efficiency from games/lineups
python build_standings.py         # recompute standings from games/manager_efficiency
python build_playoff_results.py   # recompute playoff_results/champions from games
python normalize_transactions.py  # normalize transactions.trans_type to Sleeper's canonical values
```

Scraper/one-off scripts (`owner_scraper.py`, `update_sleeper_draft.py`, `espn_scraper_2017_2018.py`, `espn_transaction_recreation_2017.py`, `espn_player_name_fixer.py`, `transaction_test.py`) are run ad hoc and generally need a league/draft ID edited in the file or entered at an interactive prompt — check each file's top-of-file constants before running.

**Always back up `dynasty_data.db` (`cp dynasty_data.db dynasty_data.db.bak`) before running any script that writes to it.** Several scripts do full-table `DROP`/`INSERT OR REPLACE` operations.

## Architecture

### Everything revolves around `dynasty_data.db`

There's no ORM or schema file — table shapes are only discoverable via `PRAGMA table_info(...)` / `sqlite_master`, or by reading the `INSERT`/`CREATE TABLE` statements in the scripts that populate them. Key tables: `teams`, `standings`, `games`, `lineups`, `manager_efficiency`, `playoff_results`, `champions`, `draft_picks`, `transactions`, `owners`, `divisions`, `power_rankings`.

`archive_df_final` is a legacy, pre-normalization table (originally named `df_final`) from whatever tool/spreadsheet seeded this database's schema. It covers only 2014–2020, keys managers by bare first name instead of `team_id` (some of whom, e.g. "Alan", "John", "Michael", "Bryan", aren't even in the current `owners` table — they left before the Sleeper era), and isn't joined against anything else. Treat it as read-only historical reference, not a table to build features on — `champions`/`playoff_results` are its normalized, `team_id`-keyed replacement (validated to agree with it exactly for all 7 overlapping years).

### Two kinds of scripts

1. **Ingestion scripts** — pull from an external API/source and write raw per-season data (`teams`, `standings` historically, `games`, `lineups`, `transactions`, `draft_picks`). One script per data era (see below).
2. **Derived-table scripts** (`backfill_manager_efficiency.py`, `build_standings.py`, `build_playoff_results.py`, `normalize_transactions.py`) — compute/clean up purely from what's already in the DB, with no external API calls. They fully rebuild their target table (`DROP`/`CREATE`, `INSERT OR REPLACE`, or idempotent `UPDATE`s) and are safe to rerun any time the upstream tables change. **New derived-table work should follow this pattern** rather than re-deriving stats from a live API.

### Data eras & sources

The league has moved platforms twice, and `games`/`lineups` data shape differs by era:

- **2014–2016: NFL.com.** No ingestion script for this era exists in the repo — this data lives only in `dynasty_data.db` with no reproducible source. `lineups` has no rows at all for these years (no bench/slot data), so per-player stats (`manager_efficiency`, `standings.max_pf`) are not computable.
- **2017–2023: ESPN** (`espn_scraper_2017_2018.py`, `espn_transaction_recreation_2017.py`, `espn_player_name_fixer.py`). Despite its name, `espn_scraper_2017_2018.py` is generic — it prompts for a year at runtime (`populate_espn_season(year)`) and its `get_base_url` branches on `year >= 2018` (modern segment endpoint) vs `year < 2018` (`leagueHistory` endpoint) — so it's the ingestion path for the whole 2017–2023 range, not just its namesake two years. Uses ESPN's `lineupSlotId`/position IDs, stored as slot values like `D/ST`, `BE` (bench), `RB/WR/TE` (flex), `IR`.
- **2024–present: Sleeper** (`sleeper_to_db.py`, `owner_scraper.py`, `update_sleeper_draft.py`, `config.py`, `helper_defs.py`). Uses Sleeper's roster/matchup API, with slot values like `DEF`, `BN` (bench), `FLEX`. Sleeper reassigns a new `league_id` each season (chained via `previous_league_id`); `helper_defs.get_sleeper_league_id()` walks that chain, and the current one lives in `config.py`.

Any code touching `lineups`/`player_slot_position` across years must account for these different vocabularies (see `FLEX_ELIGIBILITY` in `sleeper_to_db.py` and how `backfill_manager_efficiency.py` extends it with the ESPN-era alias).

### Known data caveats

- **2017 `lineups.player_slot_position` is corrupted**: every row reads `"QB"` regardless of the player's actual slot, from how `espn_scraper_2017_2018.py` handles the `leagueHistory` endpoint (the `year < 2018` branch of `get_base_url`). Not fixed — 2017 is excluded from `manager_efficiency`/`standings.max_pf` as a result.
- **2017 `games` win/loss/points don't fully reconcile** with older hand-entered records (off by half a game and a few points for at least one team) — likely tied to ongoing `game_id` cleanup for that season.
- **`manager_efficiency` and `standings.max_pf` are only meaningful for year >= 2018** — no usable bench/lineup data exists before then (see above).
- **`standings` is keyed `(year, week, team_id)`** and holds regular-season-only cumulative rows (no rows for playoff weeks — wins/losses/points freeze at the end of the regular season, matching this league's existing convention). `app.py`'s `load_standings()` and the Standings tab still assume one row per team per year (`idxmax()` over the whole season) and have **not** been updated for the new shape — this is a known follow-up, not yet done.
- **This league does not use FAAB** — waivers are priority-order, not budget-bid — so there's no bid-amount data to capture from either platform's transaction API, and none should be expected.
- **The playoff bracket is championship-path only, by design.** This league never tracked consolation/"toilet bowl" games, so `games`/`playoff_results` only ever contain the winners-bracket path (verified across all 12 years: a 4-team Quarterfinal round with no byes, a 4-team Semifinal round — the 2 quarterfinal winners plus 2 teams that got a first-round bye — then a 2-team Championship). Don't read the absence of consolation games as missing data to backfill.
- **`team_id` can be `0`** (2014 has a real team, Bryan Greenberg, at `team_id=0`). Don't use falsy/truthy checks (`if team_id:`) on it in any downstream code (SQL, Python, or JS) — always compare explicitly (`team_id is not None` / `!== null`).
- **Draft-pick trade history is incomplete.** `sleeper_to_db.py`'s transaction ingestion only reads `adds`/`drops` from Sleeper's transaction payload; it doesn't parse the payload's separate `draft_picks` list, so trades involving future picks don't show the pick side anywhere. `draft_picks.original_roster`/`previous_owner` capture the *end result* of pick trades (who ultimately owns/used a pick) for 2017+, but not 2014–2016, and not the transaction itself. Pre-Sleeper-era (2014–2023) pick trade context can be partially reconstructed from https://adynastyleague.wordpress.com/the-draft/ (each year's page has a draft results table with a "Via" column showing trade chains, e.g. "1.01 Steven ... via Cody").
- **Player IDs are not stable across the ESPN→Sleeper cutover.** ESPN-era (2017–2023) `player_id` values are ESPN's own IDs (e.g. Tom Brady is `2330` every ESPN year); Sleeper-era (2024+) IDs are Sleeper's own, much smaller numeric space. The same real player has two different `player_id` values depending on era — joining/aggregating a player's history across the platform switch must currently go through `player_name`, not `player_id`.
