from flask import Flask
import datetime
import gspread 
import os
import requests
import json
import logging

# Set up basic logging (useful for debugging on Render)
logging.basicConfig(level=logging.INFO)

# ---------------------------------------------
# 1. SETUP THE APP
# ---------------------------------------------
app = Flask(__name__)

# ==========================================================
# *** 1. CRITICAL: REPLACE THIS WITH YOUR EXACT SHEET NAME ***
# ==========================================================
GOOGLE_SHEET_NAME = "Minute Tracker Data" 
# ==========================================================

# ==========================================================
# *** 2. CRITICAL: LIST YOUR FRIENDS' USER IDs AND NAMES ***
# (Ensure these IDs are correct for your friends)
# ==========================================================
FRIENDS_TO_TRACK = {
    5120230728: "jsadujgha", 
    4491738101: "NOTKRZEN", 
    3263707365: "Cyrus_STORM",
}
# ==========================================================

# Roblox API Endpoint and Cache File setup
ROBLOX_STATUS_URL = "https://presence.roblox.com/v1/presence/users"
CACHE_FILE = "status_cache.json"

# ---------------------------------------------
# 2. HELPER FUNCTIONS
# ---------------------------------------------

def get_cached_status():
    """Reads the last known status from the cache file."""
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Error reading cache file: {e}")
            pass # Continue to initialize cache if read fails
    
    # Initialize cache with defaults and a start_time key
    default_state = {"playing": False, "game_name": "N/A", "start_time": None}
    return {uid: default_state for uid in FRIENDS_TO_TRACK}

def save_cached_status(status_data):
    """Writes the current status to the cache file."""
    try:
        with open(CACHE_FILE, 'w') as f:
            json.dump(status_data, f)
    except Exception as e:
        logging.error(f"Error writing cache file: {e}")

def check_roblox_status(user_ids):
    """Fetches current status from the Roblox API."""
    try:
        response = requests.post(ROBLOX_STATUS_URL, 
                                 json={"userIds": list(user_ids)}, 
                                 timeout=10)
        response.raise_for_status()
        
        presence = response.json().get('userPresences', [])
        current_status = {}
        for item in presence:
            uid = item['userId']
            is_playing = item['userPresenceType'] in [1, 2] 
            game_name = item.get('lastLocation') if is_playing else "N/A"
            
            current_status[uid] = {
                "playing": is_playing, 
                "game_name": game_name
            }
        return current_status
    except Exception as e:
        logging.error(f"Roblox API check failed: {e}")
        return None

# ---------------------------------------------
# 3. THE MAIN TRACKING LOGIC
# ---------------------------------------------
def run_tracking_logic():
    try:
        # Connect to Google Sheets
        gc = gspread.service_account(filename="credentials.json") 
        spreadsheet = gc.open(GOOGLE_SHEET_NAME)
        worksheet = spreadsheet.sheet1 
    except Exception as e:
        logging.error(f"Google Sheets connection failed: {e}")
        return f"ERROR: Google Sheets connection failed. Details: {e}"

    # Get cached and current status
    cached_status = get_cached_status()
    current_roblox_status = check_roblox_status(FRIENDS_TO_TRACK.keys())
    
    if not current_roblox_status:
        return "ERROR: Could not fetch status from Roblox API."

    new_cache = {}
    logs_to_write = []
    
    # Get current time for logging
    current_time_dt = datetime.datetime.now()
    timestamp_str = current_time_dt.strftime("%Y-%m-%d %H:%M:%S")

    # --- COMPARE AND LOG CHANGES ---
    for uid, friend_name in FRIENDS_TO_TRACK.items():
        current = current_roblox_status.get(uid, {"playing": False, "game_name": "N/A"})
        
        # Ensure cached status has the start_time key for comparison
        cached = cached_status.get(uid, {"playing": False, "game_name": "N/A", "start_time": None})
        
        new_cache[uid] = {
            "playing": current['playing'], 
            "game_name": current['game_name'], 
            "start_time": cached['start_time']
        }

        # 1. STARTED PLAYING: Log the start and save the start_time to cache
        if not cached['playing'] and current['playing']:
            action = "STARTED PLAYING"
            game = current['game_name']
            
            # Log: Timestamp, Friend, Action, Game, Time Period (Blank for start)
            logs_to_write.append([timestamp_str, friend_name, action, game, ""]) 
            logging.info(f"{friend_name} {action} {game}")
            
            # Update cache with the start time
            new_cache[uid]["start_time"] = timestamp_str

        # 2. STOPPED PLAYING: Log the stop and calculate duration
        elif cached['playing'] and not current['playing']:
            action = "STOPPED PLAYING"
            game = cached['game_name'] # Use the game from cache
            duration_minutes = ""
            
            # Calculate duration if a start_time exists in the cache
            if cached['start_time']:
                try:
                    start_time_dt = datetime.datetime.strptime(cached['start_time'], "%Y-%m-%d %H:%M:%S")
                    duration = current_time_dt - start_time_dt
                    duration_minutes = round(duration.total_seconds() / 60, 2)
                except Exception as e:
                    logging.error(f"Error calculating duration for {friend_name}: {e}")
            
            # Log: Timestamp, Friend, Action, Game, Time Period (Calculated)
            logs_to_write.append([timestamp_str, friend_name, action, game, duration_minutes])
            logging.info(f"{friend_name} {action} {game} for {duration_minutes} min")
            
            # Reset start_time in cache
            new_cache[uid]["start_time"] = None
            
        # 3. GAME CHANGED: Log a stop event, then a start event, and calculate duration for the first session
        elif current['playing'] and cached['playing'] and current['game_name'] != cached['game_name']:
            
            # --- Log STOP for the old game ---
            old_game = cached['game_name']
            duration_minutes = ""
            if cached['start_time']:
                try:
                    start_time_dt = datetime.datetime.strptime(cached['start_time'], "%Y-%m-%d %H:%M:%S")
                    duration = current_time_dt - start_time_dt
                    duration_minutes = round(duration.total_seconds() / 60, 2)
                except Exception as e:
                    logging.error(f"Error calculating duration for {friend_name} game change stop: {e}")

            logs_to_write.append([timestamp_str, friend_name, "STOPPED PLAYING", old_game, duration_minutes])
            
            # --- Log START for the new game ---
            new_game = current['game_name']
            logs_to_write.append([timestamp_str, friend_name, "STARTED PLAYING", new_game, ""])