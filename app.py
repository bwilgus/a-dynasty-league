import pandas as pd
from pandas.api.types import is_numeric_dtype
from espn_api.football import League
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from itertools import groupby
import streamlit as st
from helper_defs import get_sleeper_league_id
from config import sleeper_league_id
from config import sleeper_user_id
import requests

league_ids = {2024:sleeper_league_id}
year = 2025
url = 'https://api.sleeper.app/v1/'

#Get each year's league ID
while True:
    id = get_sleeper_league_id(league_ids[year-1], sleeper_user_id, year)
    if id == None:
        break
    else:
        league_ids[year] = get_sleeper_league_id(league_ids[year-1], sleeper_user_id, year)
        year+=1

#Teams
teams_year = []
teams_owner = []
teams_team_id = []
teams_team_name = []

#Games
games_year = []
games_week = []
games_game_id = []
games_team_id = []
games_opponent_team_id = []
games_team_score = []
games_team_projected_score = []
games_is_playoff = []
games_is_win = []
games_is_tie = []
games_is_loss = []

#Drafts
drafts_year = []
drafts_pick = []
drafts_team_id = []
drafts_player_name = []
drafts_player_position = []
drafts_nfl_team = []
drafts_traded_from = []

#Lineups
lineups_year = []
lineups_week = []
lineups_game_id = []
lineups_team_id = []
lineups_player_id = []
lineups_player_name = []
lineups_player_position = []
lineups_player_slot = []
lineups_player_status = []
lineups_player_score = []
lineups_player_projected_score = []

#Power Rankings - TBD

#Transactions - TBD
transactions_transaction_id = []

#Divisions
divisions_year = []
divisions_division_id = []
divisions_division_name = []
divisions_team_id = []

#Champions
champions_year = []
champions_team_id = []
champions_team_name = []

#Eras

#Records



for year in league_ids.keys():
    #Team Information


    #Games Information

    #Draft Information

    #Lineups Information

    #Power Rankings Information

    #Transactions Information
    players = requests.get(url+'nfl/players')
    players = players.json()

    #Divisions Information

    #Champions Information

    #Eras Information

    #Records Information
