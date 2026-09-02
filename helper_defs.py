import requests

def get_sleeper_league_id(old_league_id, user_id, season):
    # 1. Fetch all leagues the user is in for the next season
    url = f"https://api.sleeper.app/v1/user/{user_id}/leagues/nfl/{season}"
    leagues = requests.get(url).json()
    
    # 2. Find which one points back to your old ID
    for league in leagues:
        if league.get("previous_league_id") == str(old_league_id):
            return league["league_id"]
    
    return None