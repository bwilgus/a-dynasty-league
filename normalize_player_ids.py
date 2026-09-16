"""Normalizes player_id in lineups/draft_picks to Sleeper's ID space.

Before this, player_id meant different things depending on era: ESPN's own
numeric IDs (2017-2023), an even older NFL.com-native numbering (2014-2016),
and Sleeper's own IDs (2024+) -- the same real player has different values
across eras, breaking any join/aggregation on player_id across the platform
switch.

Matching strategy, strongest to weakest, never guessing when ambiguous:
  0. Sleeper's own player list embeds ESPN's id on ~55% of its players
     (the `espn_id` field). Where our stored original id matches that field
     exactly, it's an authoritative, unambiguous match -- no name comparison
     needed at all, and it's immune to spelling/nickname drift between the
     two platforms' name fields (e.g. it resolves "Robby Anderson" AND
     "Robbie Anderson" in one shot, since both rows share the same original
     ESPN id despite the spelling difference). Tried first.
  1. Exact match on (normalized name, position).
  2. If that fails, exact match on normalized name alone, but only if
     exactly one Sleeper player has that name (position drift, e.g. Taysom
     Hill recorded as QB by ESPN but TE by Sleeper).
  3. A small hand-verified alias table for confirmed nickname/full-name/typo
     mismatches (e.g. "Will Fuller V" -> Sleeper's "William Fuller", or
     "Diontae Jackson" -> "Diontae Johnson" for a plain surname typo in the
     source data), each checked by hand -- often cross-referenced against
     the drafting team recorded in draft_picks.nfl_team -- before being added.
Anything still unresolved (no match, or genuinely ambiguous -- e.g. two
different real players who share a name and neither has an espn_id on
file) is left as-is: the original player_id is kept, never guessed.

D/ST is handled separately and deterministically: both eras spell defenses
as a team name (e.g. "Packers D/ST" or "Green Bay Packers"), and Sleeper's
own D/ST player_id is just the team's abbreviation -- so this is a plain
nickname -> abbreviation lookup, not name matching. This also fixes a
pre-existing bug in sleeper_to_db.py where D/ST player_id was stored as
NULL (int() coercion failing silently on non-numeric team codes like "SEA").

Every touched row keeps its original id in `espn_player_id` (added by this
script) and gets `player_id_source` set to 'sleeper' (normalized) or
'unmatched' (left as-is, original id retained in player_id itself).

Safe to rerun.
"""

import sqlite3
from collections import defaultdict

from sleeper_to_db import DB_PATH, SLEEPER_BASE_URL, fetch_json

VALID_POS = {"QB", "RB", "WR", "TE", "K"}
DEF_POSITIONS = {"D/ST", "DEF"}
SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}

# Confirmed by hand against Sleeper's live player list. Only add entries
# here that have been verified unique (by name+position, or cross-checked
# against Sleeper's espn_id field).
VERIFIED_ALIASES = {
    ("will fuller", "WR"): "william fuller",
    ("kenneth gainwell", "RB"): "kenny gainwell",
    ("mitch trubisky", "QB"): "mitchell trubisky",
    ("gabriel davis", "WR"): "gabe davis",
    ("chigoziem okonkwo", "TE"): "chig okonkwo",
    ("nyheim hines", "RB"): "nyheim miller hines",
    ("pat mahomes", "QB"): "patrick mahomes",
    ("deandre swift", "RB"): "dandre swift",
    ("dwayne eskeridge", "WR"): "dee eskridge",
    ("josh palmer", "WR"): "joshua palmer",
    ("jaelen reagor", "WR"): "jalen reagor",
    ("tamarrion terry", "WR"): "tamorrion terry",
    ("marquise hollywood brown", "WR"): "marquise brown",
    # Equanimeous St. Brown's full legal name, as recorded verbatim in draft_picks.
    ("equanimeous tristan imhotep j st brown", "WR"): "equanimeous st brown",
    # Source data has the wrong surname (Sleeper/espn_id/nfl_team all confirm Johnson).
    ("diontae jackson", "WR"): "diontae johnson",
    ("rashad higgins", "WR"): "rashard higgins",
    ("javorious allen", "RB"): "javorius allen",
    ("josh scobey", "K"): "josh scobee",
    ("jay feeley", "K"): "jay feely",
    ("steven hauschka", "K"): "stephen hauschka",
    ("davante parker", "WR"): "devante parker",
}

# NFL team nickname (last word of the team name, either "Packers D/ST" or
# "Green Bay Packers" style) -> Sleeper's team-abbreviation D/ST id.
NICKNAME_TO_ABBR = {
    "cardinals": "ARI", "falcons": "ATL", "ravens": "BAL", "bills": "BUF",
    "panthers": "CAR", "bears": "CHI", "bengals": "CIN", "browns": "CLE",
    "cowboys": "DAL", "broncos": "DEN", "lions": "DET", "packers": "GB",
    "texans": "HOU", "colts": "IND", "jaguars": "JAX", "chiefs": "KC",
    "chargers": "LAC", "rams": "LAR", "raiders": "LV", "dolphins": "MIA",
    "vikings": "MIN", "patriots": "NE", "saints": "NO", "giants": "NYG",
    "jets": "NYJ", "eagles": "PHI", "steelers": "PIT", "seahawks": "SEA",
    "49ers": "SF", "niners": "SF", "buccaneers": "TB", "titans": "TEN",
    "redskins": "WAS", "washington": "WAS", "commanders": "WAS",
}


def normalize_name(name):
    name = (name or "").replace("\xa0", " ").lower()
    for ch in (".", ",", "'", "’", "‘", "`"):
        name = name.replace(ch, "")
    for ch in ("-", "–", "—"):
        name = name.replace(ch, " ")
    tokens = [t for t in name.split() if t]
    if tokens and tokens[-1] in SUFFIXES:
        tokens = tokens[:-1]
    return " ".join(tokens)


def normalize_pos(pos):
    return (pos or "").replace("\xa0", "").strip().upper()


def dst_abbr(name):
    nickname = name.replace("D/ST", "").strip().split()[-1].lower()
    return NICKNAME_TO_ABBR.get(nickname)


def build_sleeper_indices():
    players = fetch_json(f"{SLEEPER_BASE_URL}/players/nfl")

    by_name_pos = defaultdict(list)
    by_name_only = defaultdict(list)
    by_espn_id = defaultdict(list)
    for pid, info in players.items():
        pos = info.get("position")
        full_name = info.get("full_name") or f"{info.get('first_name', '')} {info.get('last_name', '')}".strip()

        espn_id = info.get("espn_id")
        if espn_id:
            by_espn_id[str(espn_id)].append(pid)

        if not full_name:
            continue
        norm = normalize_name(full_name)
        by_name_only[norm].append(pid)
        if pos in VALID_POS:
            by_name_pos[(norm, pos)].append(pid)

    return by_name_pos, by_name_only, by_espn_id


def match_by_name(name, pos, by_name_pos, by_name_only):
    norm_name = normalize_name(name)
    norm_pos = normalize_pos(pos)

    candidates = by_name_pos.get((norm_name, norm_pos), [])
    if len(candidates) == 1:
        return candidates[0]

    candidates = by_name_only.get(norm_name, [])
    if len(candidates) == 1:
        return candidates[0]

    alias = VERIFIED_ALIASES.get((norm_name, norm_pos))
    if alias:
        candidates = by_name_pos.get((alias, norm_pos), []) or by_name_only.get(alias, [])
        if len(candidates) == 1:
            return candidates[0]

    return None


def add_columns(cur, table):
    cols = {row[1] for row in cur.execute(f"PRAGMA table_info({table})")}
    if "espn_player_id" not in cols:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN espn_player_id TEXT")
    if "player_id_source" not in cols:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN player_id_source TEXT")


def normalize_table(cur, table, name_col, pos_col, by_name_pos, by_name_only, by_espn_id, def_positions):
    add_columns(cur, table)

    # Capture the true original id once, before any normalization touches player_id.
    cur.execute(f"UPDATE {table} SET espn_player_id = CAST(player_id AS TEXT) WHERE year < 2024 AND espn_player_id IS NULL")

    def_placeholders = ",".join("?" for _ in def_positions)

    # Tier 0: authoritative id match via Sleeper's own embedded espn_id field.
    # Grouped by original id (not name), so it fixes every spelling variant
    # of the same real person's name in one shot.
    cur.execute(
        f"""
        SELECT DISTINCT espn_player_id FROM {table}
        WHERE year < 2024 AND {pos_col} NOT IN ({def_placeholders}) AND espn_player_id IS NOT NULL
        """,
        tuple(def_positions),
    )
    orig_ids = [row[0] for row in cur.fetchall()]
    id_matched = 0
    for orig_id in orig_ids:
        candidates = by_espn_id.get(orig_id, [])
        if len(candidates) == 1:
            cur.execute(
                f"UPDATE {table} SET player_id = ?, player_id_source = 'sleeper' WHERE year < 2024 AND espn_player_id = ?",
                (candidates[0], orig_id),
            )
            id_matched += 1

    # Tier 1-3: name-based, for whatever tier 0 didn't resolve.
    cur.execute(
        f"""
        SELECT DISTINCT {name_col}, {pos_col} FROM {table}
        WHERE year < 2024 AND {pos_col} NOT IN ({def_placeholders})
          AND (player_id_source IS NULL OR player_id_source != 'sleeper')
        """,
        tuple(def_positions),
    )
    pairs = cur.fetchall()

    name_matched = 0
    unmatched = 0
    for name, pos in pairs:
        if name is None:
            continue
        sleeper_id = match_by_name(name, pos, by_name_pos, by_name_only)
        if sleeper_id:
            cur.execute(
                f"UPDATE {table} SET player_id = ?, player_id_source = 'sleeper' WHERE year < 2024 AND {name_col} = ? AND {pos_col} = ?",
                (sleeper_id, name, pos),
            )
            name_matched += 1
        else:
            cur.execute(
                f"UPDATE {table} SET player_id_source = 'unmatched' WHERE year < 2024 AND {name_col} = ? AND {pos_col} = ?",
                (name, pos),
            )
            unmatched += 1

    # D/ST: deterministic nickname -> abbreviation lookup.
    cur.execute(
        f"SELECT DISTINCT {name_col} FROM {table} WHERE year < 2024 AND {pos_col} IN ({def_placeholders})",
        tuple(def_positions),
    )
    dst_matched = 0
    dst_unmatched = 0
    for (name,) in cur.fetchall():
        if name is None:
            continue
        abbr = dst_abbr(name)
        if abbr:
            cur.execute(
                f"UPDATE {table} SET player_id = ?, player_id_source = 'sleeper' WHERE year < 2024 AND {name_col} = ? AND {pos_col} IN ({def_placeholders})",
                (abbr, name, *def_positions),
            )
            dst_matched += 1
        else:
            cur.execute(
                f"UPDATE {table} SET player_id_source = 'unmatched' WHERE year < 2024 AND {name_col} = ? AND {pos_col} IN ({def_placeholders})",
                (name, *def_positions),
            )
            dst_unmatched += 1

    # Sleeper-native rows (2024+) are already in the right space.
    cur.execute(f"UPDATE {table} SET player_id_source = 'sleeper' WHERE year >= 2024 AND player_id_source IS NULL")

    return id_matched, name_matched, dst_matched, unmatched + dst_unmatched


def fix_sleeper_native_dst(cur, table, name_col, pos_col, def_positions):
    """Fixes the pre-existing bug where 2024+ D/ST player_id was stored as NULL."""
    placeholders = ",".join("?" for _ in def_positions)
    cur.execute(
        f"SELECT DISTINCT {name_col} FROM {table} WHERE year >= 2024 AND {pos_col} IN ({placeholders}) AND player_id IS NULL",
        tuple(def_positions),
    )
    names = [row[0] for row in cur.fetchall()]
    fixed = 0
    for name in names:
        abbr = dst_abbr(name)
        if abbr:
            cur.execute(
                f"UPDATE {table} SET player_id = ?, player_id_source = 'sleeper' WHERE year >= 2024 AND {name_col} = ? AND player_id IS NULL",
                (abbr, name),
            )
            fixed += 1
    return fixed


def normalize_player_ids(db_path: str = DB_PATH):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    print("Fetching Sleeper player list...")
    by_name_pos, by_name_only, by_espn_id = build_sleeper_indices()
    print(f"Indexed {sum(len(v) for v in by_name_pos.values())} (name,pos) entries, {len(by_espn_id)} espn_id entries.")

    print("\nNormalizing lineups...")
    id_matched, name_matched, dst_matched, unmatched = normalize_table(
        cur, "lineups", "player_name", "player_position", by_name_pos, by_name_only, by_espn_id, DEF_POSITIONS
    )
    print(f"  {id_matched} matched via espn_id, {name_matched} matched via name, {dst_matched} D/ST matched, {unmatched} unmatched.")
    fixed = fix_sleeper_native_dst(cur, "lineups", "player_name", "player_position", DEF_POSITIONS)
    print(f"  Fixed {fixed} pre-existing NULL D/ST player_id names in 2024+ rows.")

    print("\nNormalizing draft_picks...")
    id_matched, name_matched, dst_matched, unmatched = normalize_table(
        cur, "draft_picks", "player_name", "position", by_name_pos, by_name_only, by_espn_id, DEF_POSITIONS
    )
    print(f"  {id_matched} matched via espn_id, {name_matched} matched via name, {dst_matched} D/ST matched, {unmatched} unmatched.")
    fixed = fix_sleeper_native_dst(cur, "draft_picks", "player_name", "position", DEF_POSITIONS)
    print(f"  Fixed {fixed} pre-existing NULL D/ST player_id names in 2024+ rows.")

    conn.commit()
    conn.close()


if __name__ == "__main__":
    normalize_player_ids()
