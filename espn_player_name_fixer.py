import sqlite3
import requests
import json

DB_PATH = r"C:\Users\wilgu\Desktop\Fun\a-dynasty-league\dynasty_data.db"
LEAGUE_ID = 2255318
SWID = "{E9847B8A-5D66-44EE-BC5F-D7E4A554CDC7}"
ESPN_S2 = r"AEAbITOsDs5gtiiTG4JvTzEvrh5n%2F7owp0n7ZJgl7IpQXs2zHJM6TZAJLyoymtnRmak6jmkoWhJ8NEcnUl7ZPerjOXk%2BLgLhfzw8VsXye9wOupr7iXxIlxTCaAsY%2Fr1Fl%2BlTxVbt8fhIgWTox45PXXPUK0zmlFZ1XbFpd9fBH%2BZrbOFlEOHQzp8X1BhPwgpmXi%2Fveog8dlSPxyVtfVxwN9Mic%2BrwY%2B81XJIgr4g65FtXsOap8Zot9Ychkx6IrM4HPMH4eHbR1xfHV8BNS%2FHeImJt"
YEAR = 2018

cookies = {"espn_s2": ESPN_S2, "SWID": SWID}
headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

def resolve_missing_transaction_players():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # 1. Find all unmapped player IDs in the transactions table
    cur.execute(
        """
        SELECT DISTINCT player 
        FROM transactions 
        WHERE year = ? AND player LIKE 'Player_%'
        """, 
        (YEAR,)
    )
    unmapped_rows = cur.fetchall()
    
    if not unmapped_rows:
        print("No unmapped 'Player_<id>' records found in transactions.")
        conn.close()
        return

    # Extract integer player IDs
    missing_ids = [int(r[0].replace("Player_", "")) for r in unmapped_rows]
    print(f"Found {len(missing_ids)} unmapped players. Resolving against ESPN master player directory...")

    # 2. Query ESPN's master player directory using kona_player_info
    url = f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{YEAR}/segments/0/leagues/{LEAGUE_ID}"
    
    # Filter ESPN for these specific player IDs
    player_filter = {
        "players": {
            "filterIds": {"value": missing_ids}
        }
    }
    
    req_headers = {
        **headers,
        "x-fantasy-filter": json.dumps(player_filter)
    }
    
    params = {"view": "kona_player_info"}

    resp = requests.get(url, params=params, cookies=cookies, headers=req_headers)
    
    if resp.status_code != 200:
        print(f"Failed to query ESPN player directory: HTTP {resp.status_code}")
        conn.close()
        return

    payload = resp.json()
    players_list = payload.get("players", [])

    id_to_name = {}
    for p_entry in players_list:
        p = p_entry.get("player", {})
        pid = p.get("id")
        full_name = p.get("fullName")
        if pid and full_name:
            id_to_name[pid] = full_name

    # 3. Update the database rows
    updated_count = 0
    for pid, real_name in id_to_name.items():
        cur.execute(
            """
            UPDATE transactions
            SET player = ?
            WHERE year = ? AND player = ?
            """,
            (real_name, YEAR, f"Player_{pid}")
        )
        updated_count += cur.rowcount

    conn.commit()
    conn.close()
    print(f"✓ Successfully mapped {len(id_to_name)} players and updated {updated_count} transaction rows.")

if __name__ == "__main__":
    resolve_missing_transaction_players()