"""One-off backfill: replaces expansion_draft.player (a free-text name) with
player_id, matched to the same Sleeper id space as `lineups`/`draft_picks`/
`players`.

expansion_draft is hand-entered historical data (a 2017 expansion draft),
not derived from any other table, so this can't be a rebuild-from-DB derived
script like build_players.py -- it's a narrow, one-time migration.

Matching, strongest to weakest, never guessing when ambiguous:
  1. Exact name match against the already-built `players` table (cheap, no
     API call, and correct by construction since players.player_name came
     from this same league's own history).
  2. For names never seen in this league's lineups (short-career/backup
     players from the 2017 era), a name-only lookup against Sleeper's live
     `/players/nfl`, same helpers as normalize_player_ids.py, kept only when
     exactly one Sleeper player has that name.
  3. A small hand-verified alias table for confirmed source-data misspellings
     (e.g. "Kenny Golloday" -> Sleeper's "Kenny Golladay"), each cross-checked
     against the real player before being added here.
D/ST rows in this table are spelled as a bare city name ("Tampa D/ST",
"Philadelphia D/ST", "Rams D/ST"), too inconsistent for the nickname lookup
in normalize_player_ids.py, so the 3 that appear here are mapped by hand.

Aborts without writing anything if any name is left unresolved.
"""

import sqlite3

from sleeper_to_db import DB_PATH
from normalize_player_ids import build_sleeper_indices, normalize_name

# Confirmed by hand against Sleeper's live player list.
VERIFIED_ALIASES = {
    "jordan mathews": "jordan matthews",
    "mohammad sanu": "mohamed sanu",
    "kenny golloday": "kenny golladay",
    "kenyon drake": "kenyan drake",
    "charles simms": "charles sims",
    "marquise lee": "marqise lee",
    "jerrick mckinnon": "jerick mckinnon",
}

DST_ABBR = {
    "Philadelphia D/ST": "PHI",
    "Tampa D/ST": "TB",
    "Rams D/ST": "LAR",
}


def resolve_player_ids(cur):
    names = [row[0] for row in cur.execute("SELECT DISTINCT player FROM expansion_draft")]

    resolved = {}
    unresolved = []

    for name in names:
        if name in DST_ABBR:
            resolved[name] = DST_ABBR[name]
            continue

        row = cur.execute(
            "SELECT player_id FROM players WHERE player_name = ?", (name,)
        ).fetchall()
        if len(row) == 1:
            resolved[name] = row[0][0]
        else:
            unresolved.append(name)

    if unresolved:
        by_name_pos, by_name_only, by_espn_id = build_sleeper_indices()
        still_unresolved = []
        for name in unresolved:
            norm = normalize_name(name)
            candidates = by_name_only.get(norm, [])
            if len(candidates) != 1:
                alias = VERIFIED_ALIASES.get(norm)
                candidates = by_name_only.get(alias, []) if alias else []
            if len(candidates) == 1:
                resolved[name] = candidates[0]
            else:
                still_unresolved.append(name)
        unresolved = still_unresolved

    return resolved, unresolved


def backfill_expansion_draft(db_path: str = DB_PATH):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    resolved, unresolved = resolve_player_ids(cur)

    if unresolved:
        print("Aborting -- could not resolve a player_id for:")
        for name in unresolved:
            print(" -", name)
        conn.close()
        return

    cols = {row[1] for row in cur.execute("PRAGMA table_info(expansion_draft)")}
    if "player_id" not in cols:
        cur.execute("ALTER TABLE expansion_draft ADD COLUMN player_id BIGINT")

    for name, player_id in resolved.items():
        cur.execute(
            "UPDATE expansion_draft SET player_id = ? WHERE player = ?",
            (player_id, name),
        )

    cur.execute("ALTER TABLE expansion_draft DROP COLUMN player")
    conn.commit()
    print(f"Resolved and replaced player with player_id for {len(resolved)} names.")
    conn.close()


if __name__ == "__main__":
    backfill_expansion_draft()
