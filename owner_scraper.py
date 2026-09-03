import sqlite3
import requests

SLEEPER_BASE_URL = "https://api.sleeper.app/v1"
DB_PATH = r"C:\Users\wilgu\Desktop\Fun\a-dynasty-league\dynasty_data.db"


def fetch_json(url: str):
    response = requests.get(url)
    response.raise_for_status()
    return response.json()


def populate_owners(league_id: str, db_path: str = DB_PATH):
    print(f"\nFetching users for Sleeper League ID: {league_id}...")
    try:
        users = fetch_json(f"{SLEEPER_BASE_URL}/league/{league_id}/users")
    except requests.exceptions.HTTPError as e:
        print(f"Error connecting to Sleeper API: {e}")
        return

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Ensure table exists
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS owners (
            user_id TEXT PRIMARY KEY,
            username TEXT,
            real_name TEXT NOT NULL,
            first_name TEXT,
            last_name TEXT
        );
    """
    )

    # Fetch any already mapped users to prevent redundant entry
    cur.execute("SELECT user_id, real_name FROM owners")
    existing_owners = dict(cur.fetchall())

    owners_to_upsert = []

    print(f"Found {len(users)} users in Sleeper. Let's map their real names.\n")

    for u in users:
        user_id = str(u["user_id"])
        username = u.get("display_name", f"User_{user_id}")

        if user_id in existing_owners:
            print(f"[Already Mapped] {username} -> {existing_owners[user_id]}")
            continue

        print("-" * 50)
        print(f"Sleeper Username: {username} (ID: {user_id})")

        # Prompt for Real Name
        while True:
            real_name = input("  Enter Full Real Name (e.g., Jane Smith): ").strip()
            if real_name:
                break
            print("  Name cannot be empty.")

        # Split into first and last name automatically
        name_parts = real_name.split(maxsplit=1)
        first_name = name_parts[0]
        last_name = name_parts[1] if len(name_parts) > 1 else ""

        owners_to_upsert.append((user_id, username, real_name, first_name, last_name))

    if owners_to_upsert:
        cur.executemany(
            """
            INSERT OR REPLACE INTO owners (
                user_id, username, real_name, first_name, last_name
            ) VALUES (?, ?, ?, ?, ?)
        """,
            owners_to_upsert,
        )
        conn.commit()
        print(f"\n✓ Successfully saved {len(owners_to_upsert)} owners into dynasty_data.db.")
    else:
        print("\nNo new owners to insert.")

    conn.close()


if __name__ == "__main__":
    league_input = input("Enter your Sleeper League ID: ").strip()
    if league_input:
        populate_owners(league_input)
    else:
        print("No League ID provided. Exiting.")