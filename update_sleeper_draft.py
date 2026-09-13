import sqlite3
import requests

# Configuration
DRAFT_ID = "1180101814984351745"  # Replace with your actual Sleeper draft ID
DB_PATH = r"C:\Users\wilgu\Desktop\Fun\a-dynasty-league\dynasty_data.db"

# API Endpoints
draft_url = f"https://api.sleeper.app/v1/draft/{DRAFT_ID}"
picks_url = f"https://api.sleeper.app/v1/draft/{DRAFT_ID}/picks"
traded_picks_url = f"https://api.sleeper.app/v1/draft/{DRAFT_ID}/traded_picks"

# 1. Fetch draft metadata
draft_resp = requests.get(draft_url).json()
draft_year = int(draft_resp.get("season", 2026))

# Sleeper maps the column on the draft board (draft_slot) to the original roster ID
slot_to_roster_id = draft_resp.get("slot_to_roster_id") or {}

# 2. Fetch draft picks and traded picks
picks = requests.get(picks_url).json()
traded_picks = requests.get(traded_picks_url).json()

# 3. Map traded picks to track the previous owner
# Keyed by (round, original_roster, current_owner) -> previous_owner
traded_map = {}
for tp in traded_picks:
    r = tp.get("round")
    orig_roster = tp.get("roster_id")
    owner = tp.get("owner_id")
    prev_owner = tp.get("previous_owner_id")
    
    traded_map[(r, orig_roster, owner)] = prev_owner

# 4. Connect to SQLite database
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# Clear existing rows for this draft_id before inserting
cursor.execute('DELETE FROM draft_picks WHERE draft_id = ?', (DRAFT_ID,))

rows_to_insert = []
for pick in picks:
    round_no = pick.get("round")
    pick_no = pick.get("pick_no")
    draft_slot = pick.get("draft_slot")
    current_owner = pick.get("roster_id")  # The team that ultimately made the pick
    
    metadata = pick.get("metadata", {})
    player_id = pick.get("player_id")
    player_name = f"{metadata.get('first_name', '')} {metadata.get('last_name', '')}".strip()
    position = metadata.get("position", "UNKNOWN")
    nfl_team = metadata.get("team")

    # Determine original roster for this pick based on the draft board slot
    original_roster = slot_to_roster_id.get(str(draft_slot))
    
    if original_roster is not None:
        original_roster = int(original_roster)
    else:
        # Fallback just in case the draft object is missing the mapping
        original_roster = current_owner 
        
    # Match with trade history to find the previous owner
    previous_owner = traded_map.get((round_no, original_roster, current_owner))

    rows_to_insert.append((
        DRAFT_ID,
        draft_year,
        round_no,
        pick_no,
        current_owner,
        player_id,
        player_name if player_name else "Undrafted / Unknown",
        position,
        nfl_team,
        str(original_roster) if original_roster else None,
        str(previous_owner) if previous_owner else None
    ))

cursor.executemany('''
    INSERT INTO draft_picks (
        draft_id, year, round, pick_no, team_id, player_id, 
        player_name, position, nfl_team, original_roster, previous_owner
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
''', rows_to_insert)

conn.commit()
conn.close()

print(f"Successfully synced {len(rows_to_insert)} draft picks for draft ID {DRAFT_ID}.")