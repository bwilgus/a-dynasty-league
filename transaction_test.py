import json
import sqlite3
import requests
from typing import Dict, Any, List

DB_PATH = r"C:\Users\wilgu\Desktop\Fun\a-dynasty-league\dynasty_data.db"
LEAGUE_ID = 2255318
SWID = "{E9847B8A-5D66-44EE-BC5F-D7E4A554CDC7}"
ESPN_S2 = r"AEAbITOsDs5gtiiTG4JvTzEvrh5n%2F7owp0n7ZJgl7IpQXs2zHJM6TZAJLyoymtnRmak6jmkoWhJ8NEcnUl7ZPerjOXk%2BLgLhfzw8VsXye9wOupr7iXxIlxTCaAsY%2Fr1Fl%2BlTxVbt8fhIgWTox45PXXPUK0zmlFZ1XbFpd9fBH%2BZrbOFlEOHQzp8X1BhPwgpmXi%2Fveog8dlSPxyVtfVxwN9Mic%2BrwY%2B81XJIgr4g65FtXsOap8Zot9Ychkx6IrM4HPMH4eHbR1xfHV8BNS%2FHeImJt"
YEAR = 2018

BASE_URL = f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{YEAR}/segments/0/leagues/{LEAGUE_ID}"

COOKIES = {"espn_s2": ESPN_S2, "SWID": SWID}
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def fetch_player_catalog() -> Dict[int, str]:
    """Builds a player ID -> Name mapping from 2018 team rosters."""
    print("Fetching 2018 player directory from team rosters...")
    params = {"view": "mRoster"}
    try:
        resp = requests.get(BASE_URL, params=params, cookies=COOKIES, headers=HEADERS)
        resp.raise_for_status()
        data = resp.json()
        payload = data[0] if isinstance(data, list) else data

        catalog = {}
        for team in payload.get("teams", []):
            for entry in team.get("roster", {}).get("entries", []):
                p = entry.get("playerPoolEntry", {}).get("player", {})
                if p.get("id"):
                    catalog[p["id"]] = p.get("fullName", f"Player_{p['id']}")
        print(f" -> Catalog built with {len(catalog)} players.")
        return catalog
    except Exception as e:
        print(f" -> Warning: Failed to build player catalog: {e}")
        return {}


def check_and_extract_2018():
    player_catalog = fetch_player_catalog()
    extracted_records = []

    print("\n--- Method A: Global mTransactions2 Check ---")
    resp = requests.get(BASE_URL, params={"view": "mTransactions2"}, cookies=COOKIES, headers=HEADERS)
    if resp.status_code == 200:
        data = resp.json()
        payload = data[0] if isinstance(data, list) else data
        trans_list = payload.get("transactions", [])
        print(f"Global mTransactions2 returned {len(trans_list)} records.")
        for t in trans_list:
            if t.get("status") in ("EXECUTED", "COMPLETE"):
                extracted_records.append(t)

    print("\n--- Method B: Weekly Segmented Scoring Period Check ---")
    for week in range(1, 18):
        params = {
            "view": "mTransactions2",
            "scoringPeriodId": week
        }
        resp = requests.get(BASE_URL, params=params, cookies=COOKIES, headers=HEADERS)
        if resp.status_code == 200:
            data = resp.json()
            payload = data[0] if isinstance(data, list) else data
            week_trans = payload.get("transactions", [])
            executed = [t for t in week_trans if t.get("status") in ("EXECUTED", "COMPLETE")]
            if executed:
                print(f"Week {week}: Found {len(executed)} executed transactions.")
                extracted_records.extend(executed)

    print("\n--- Method C: kona_league_communication Message Feed ---")
    filter_activity = {
        "topics": {
            "filterType": {"value": ["ACTIVITY_TRANSACTIONS"]},
            "limit": 500,
            "limitPerMessageSet": {"value": 500}
        }
    }
    comm_headers = {**HEADERS, "x-fantasy-filter": json.dumps(filter_activity)}
    resp = requests.get(BASE_URL, params={"view": "kona_league_communication"}, cookies=COOKIES, headers=comm_headers)
    print(f"kona_league_communication HTTP Status: {resp.status_code}")
    if resp.status_code == 200:
        data = resp.json()
        payload = data[0] if isinstance(data, list) else data
        topics = payload.get("topics", [])
        print(f"Found {len(topics)} communication/activity events.")
        for top in topics:
            for msg in top.get("messages", []):
                trans_payload = msg.get("targetId") or msg.get("message")
                # Parse message structure if activity feed returned items
                if trans_payload:
                    print(f"Sample activity message: {msg}")

    # Deduplicate extracted transaction objects by transaction ID
    unique_transactions = {t["id"]: t for t in extracted_records if "id" in t}
    print(f"\nTotal unique executed transactions identified: {len(unique_transactions)}")

    if not unique_transactions:
        print("No raw transaction objects returned from the ESPN 2018 endpoints.")
        return

    # Parse into transactions table schema using a set to deduplicate identical rows
    unique_rows = set()
    for trans_id, trans in unique_transactions.items():
        trans_type = trans.get("type", "UNKNOWN").lower()
        week = trans.get("scoringPeriodId", 1)

        for item in trans.get("items", []):
            item_type = item.get("type")
            pid = item.get("playerId")
            p_name = player_catalog.get(pid, f"Player_{pid}")

            # Process Adds
            if item.get("toTeamId") and item["toTeamId"] > 0:
                unique_rows.add((
                    str(trans_id), item["toTeamId"], YEAR, week, trans_type, "add", p_name
                ))

            # Process Drops
            if item.get("fromTeamId") and item["fromTeamId"] > 0 and item_type in ("DROP", "LINEUP"):
                unique_rows.add((
                    str(trans_id), item["fromTeamId"], YEAR, week, trans_type, "drop", p_name
                ))

    rows_to_insert = list(unique_rows)

    if rows_to_insert:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("DELETE FROM transactions WHERE year = ?", (YEAR,))
        cur.executemany(
            """
            INSERT OR IGNORE INTO transactions (
                trans_id, team_id, year, week, trans_type, action, player
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows_to_insert
        )
        conn.commit()
        conn.close()
        print(f"✓ Successfully inserted {len(rows_to_insert)} transaction events into dynasty_data.db.")


if __name__ == "__main__":
    check_and_extract_2018()