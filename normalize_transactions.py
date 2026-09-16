"""Normalizes transactions.trans_type to Sleeper's canonical values.

The transactions table was populated by both the ESPN-era and Sleeper-era
scripts, which used inconsistent casing/spelling for the same concepts
(e.g. FREEAGENT, freeagent, free_agent all mean the same thing). This
normalizes everything to Sleeper's own values: trade, waiver, free_agent,
commissioner.

ESPN's "trade_uphold" (a trade that was reviewed and upheld) has no distinct
Sleeper equivalent and is folded into "trade" -- functionally identical to
any other completed trade.

Safe to rerun any time new rows are ingested with old-style casing.
"""

import sqlite3

from sleeper_to_db import DB_PATH

TRANS_TYPE_MAP = {
    "FREEAGENT": "free_agent",
    "freeagent": "free_agent",
    "WAIVER": "waiver",
    "trade_uphold": "trade",
}


def normalize_transactions(db_path: str = DB_PATH):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    total_updated = 0
    for old_value, new_value in TRANS_TYPE_MAP.items():
        cur.execute("UPDATE transactions SET trans_type = ? WHERE trans_type = ?", (new_value, old_value))
        total_updated += cur.rowcount

    conn.commit()
    cur.execute("SELECT trans_type, COUNT(*) FROM transactions GROUP BY trans_type ORDER BY trans_type")
    print(f"Normalized {total_updated} rows. Current trans_type distribution:")
    for trans_type, count in cur.fetchall():
        print(f"  {trans_type}: {count}")
    conn.close()


if __name__ == "__main__":
    normalize_transactions()
