from flask import Flask
import datetime
import gspread 
import os
import requests
import json
import logging
import pytz 

# Set up basic logging 
logging.basicConfig(level=logging.INFO)

# ---------------------------------------------
# 1. SETUP THE APP
# ---------------------------------------------
app = Flask(__name__)

# ==========================================================
# *** 1. CRITICAL: REPLACE THIS WITH YOUR EXACT SPREADSHEET NAME ***
# ==========================================================
GOOGLE_SHEET_NAME = "Minute Tracker Data" 
# ==========================================================

# ==========================================================
# *** 2. CRITICAL: LIST YOUR FRIENDS' USER IDs AND NAMES ***
# *** V11 FIX: INCLUDING HULK_BUSTER9402 ***
# ==========================================================
FRIENDS_TO_TRACK = {
    5120230728: "jsadujgha", 
    4491738101: "NOTKRZEN", 
    3263707365: "Cyrus_STORM",
    # Based on the logs, adding the fourth user ID and name:
    5188846313: "hulk_buster9402", 
}
# ==========================================================

# Roblox API Endpoint and Sheet Names
ROBLOX_STATUS_URL = "https://presence.roblox.com/v1/presence/users"
DATA_SHEET_NAME = "Activity Log"  
CACHE_SHEET_NAME = "Cache"       

# ---------------------------------------------
# 2. HELPER FUNCTIONS (GOOGLE SHEET CACHE)
# ---------------------------------------------

def get_cached_status(worksheet):
    """Reads the last known status from the Cache sheet (Cell A2)."""
    try:
        json_str = worksheet.acell('A2').value
        if json_str and json_str.strip() not in ('{}', ''):
            return json.loads(json_str)
    except Exception as e:
        logging.error(f"Error loading cache from Sheet: {e}")
        
    # Default state for new or empty cache
    default_state = {"playing": False, "game_name": "Offline", "start_time_utc": None, "place_id": 0}
    return {uid: default_state for uid in FRIENDS_TO_TRACK}

def save_cached_status(worksheet, status_data):
    """Writes the current status to the Cache sheet (Cell A2) using the correct list-of-lists format."""
    try:
        json_str = json.dumps(status_data)
        worksheet.update('A2', [[json_str]])
    except Exception as e:
        logging.error(f"Error saving cache to Sheet: {e}")

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
            
            # V11: ALL status types except Offline (0) are considered 'playing'. 
            is_playing = item['userPresenceType'] in [1, 2, 3] 
            user_presence_type = item['userPresenceType'] 
            
            # --- Game Data Handling (Prioritize Place ID) ---
            game_name = item.get('lastLocation')
            place_id = item.get('placeId', 0) # Get placeId, default to 0
            
            if user_presence_type == 0:
                # Truly offline
                game_name = "Offline" 
            elif is_playing:
                # If online (Type 1, 2, or 3)
                if place_id and place_id != 0:
                    # They are in a real place/game
                    game_name = f"Game ID: {place_id}" 
                else:
                    # They are on the website or just online
                    game_name = "Website/Online"

            current_status[uid] = {
                "playing": is_playing, 
                "game_name": game_name,
                "place_id": place_id # Store the place ID separately
            }
        return current_status
    except Exception as e:
        logging.error(f"Roblox API check failed: {e}")
        return None

# ---------------------------------------------
# 3. THE MAIN TRACKING LOGIC
# ---------------------------------------------
def execute_tracking():
    # --- Connect to Google Sheets ---
    try:
        creds_json = os.environ.get('GOOGLE_CREDENTIALS')
        if not creds_json:
            return "ERROR: GOOGLE_CREDENTIALS environment variable is missing."
        
        gc = gspread.service_account_from_dict(json.loads(creds_json)) 
        
        spreadsheet = gc.open(GOOGLE_SHEET_NAME)
        
        data_worksheet = spreadsheet.worksheet(DATA_SHEET_NAME) 
        cache_worksheet = spreadsheet.worksheet(CACHE_SHEET_NAME)
        
    except Exception as e:
        logging.error(f"Google Sheets connection failed: {e}")
        return f"ERROR: Google Sheets connection failed. Details: {e}"

    # Get cached and current status
    cached_status = get_cached_status(cache_worksheet)
    current_roblox_status = check_roblox_status(FRIENDS_TO_TRACK.keys())
    
    if not current_roblox_status:
        return "ERROR: Could not fetch status from Roblox API."

    new_cache = {}
    logs_to_write = []
    
    # --- TIME HANDLING: Use UTC for internal caching and IST for logging ---
    try:
        ist_tz = pytz.timezone('Asia/Kolkata')
        current_time_utc = datetime.datetime.now(pytz.utc)
        current_time_ist = current_time_utc.astimezone(ist_tz)
        timestamp_log_str = current_time_ist.strftime("%Y-%m-%d %H:%M:%S")
        timestamp_cache_str = current_time_utc.strftime("%Y-%m-%d %H:%M:%S+00:00") # UTC format for cache
    except Exception:
        # Fallback if pytz is missing
        current_time_utc = datetime.datetime.now(datetime.timezone.utc)
        timestamp_log_str = current_time_utc.strftime("%Y-%m-%d %H:%M:%S (UTC)")
        timestamp_cache_str = current_time_utc.strftime("%Y-%m-%d %H:%M:%S+00:00")
        

    # --- COMPARE AND LOG CHANGES ---
    for uid, friend_name in FRIENDS_TO_TRACK.items():
        # Fallback for missing keys in old cache
        cached_default = {"playing": False, "game_name": "Offline", "start_time_utc": None, "place_id": 0}
        cached = cached_status.get(uid, cached_default)
        
        current = current_roblox_status.get(uid, {"playing": False, "game_name": "Offline", "place_id": 0})
        
        # Start with the cached state for the next cache update
        new_cache[uid] = cached.copy()
        
        # Determine if the user is in a state with a game ID
        cached_in_game_id = cached['playing'] and cached['place_id'] != 0
        current_in_game_id = current['playing'] and current['place_id'] != 0

        # 1. STARTED PLAYING A REAL GAME (No Game ID -> Has Game ID)
        if not cached_in_game_id and current_in_game_id:
            action = "STARTED PLAYING"
            game = current['game_name'] # Will be "Game ID: XXX"
            logs_to_write.append([timestamp_log_str, friend_name, action, game, ""]) 
            
            # UPDATE NEW CACHE STATE: Log the start time, playing=True, and the game ID
            new_cache[uid]["playing"] = True
            new_cache[uid]["game_name"] = current['game_name']
            new_cache[uid]["place_id"] = current['place_id']
            new_cache[uid]["start_time_utc"] = timestamp_cache_str

        # 2. STOPPED PLAYING A REAL GAME (Has Game ID -> No Game ID OR Offline)
        elif cached_in_game_id and not current_in_game_id:
            action = "STOPPED PLAYING"
            game = cached['game_name'] # Use cached name (Game ID: XXX) for the log
            duration_minutes = ""
            
            if cached['start_time_utc']:
                try:
                    start_time_utc_dt = datetime.datetime.strptime(cached['start_time_utc'], "%Y-%m-%d %H:%M:%S+00:00")
                    start_time_utc_aware = pytz.utc.localize(start_time_utc_dt)
                    
                    duration = current_time_utc.replace(tzinfo=pytz.utc) - start_time_utc_aware
                    duration_minutes = round(duration.total_seconds() / 60, 2)
                except Exception as e:
                    logging.error(f"Error calculating duration for {friend_name}: {e}")
            
            logs_to_write.append([timestamp_log_str, friend_name, action, game, duration_minutes])
            
            # UPDATE NEW CACHE STATE: Reset tracking data
            new_cache[uid]["playing"] = current['playing']
            new_cache[uid]["game_name"] = current['game_name'] # Set name to 'Website/Online' or 'Offline'
            new_cache[uid]["place_id"] = current['place_id'] # Store the new place_id (0)
            new_cache[uid]["start_time_utc"] = None

        # 3. GAME CHANGED (While playing A game)
        # Check if they are still in a game ID state AND the place ID changed
        elif cached_in_game_id and current_in_game_id and current['place_id'] != cached['place_id']:
            # Log the stop of the old game and the start of the new one
            # STOP LOG
            stop_action = "STOPPED PLAYING"
            stop_game = cached['game_name']
            stop_duration = "" # We will not calculate duration here for simplicity in a game-switch log
            logs_to_write.append([timestamp_log_str, friend_name, stop_action, stop_game, stop_duration])

            # START LOG
            start_action = "STARTED PLAYING"
            start_game = current['game_name']
            logs_to_write.append([timestamp_log_str, friend_name, start_action, start_game, ""])
            
            # UPDATE NEW CACHE STATE (Set up for the new game)
            new_cache[uid]["game_name"] = current['game_name']
            new_cache[uid]["place_id"] = current['place_id']
            new_cache[uid]["start_time_utc"] = timestamp_cache_str
            
        # 4. Website/Online State Flip (No log, but update cache to reflect current status)
        elif not cached_in_game_id and not current_in_game_id:
            # If they are both outside of a Game ID, just update the name/status 
            # (e.g., flip from Offline to Website/Online) without logging.
            new_cache[uid]["playing"] = current['playing']
            new_cache[uid]["game_name"] = current['game_name']
            new_cache[uid]["place_id"] = current['place_id']
        
        # 5. Otherwise, no meaningful change to track.


    # --- WRITE LOGS AND SAVE CACHE ---
    if logs_to_write:
        data_worksheet.append_rows(logs_to_write)
    
    save_cached_status(cache_worksheet, new_cache)

    return f"SUCCESS: Checked {len(FRIENDS_TO_TRACK)} friends. {len(logs_to_write)} new events logged."
    
# ---------------------------------------------
# 4. WEB ROUTES
# ---------------------------------------------

# Main tracker route (runs on both / and /track)
@app.route('/track')
@app.route('/') 
def main_tracker_route():
    result = execute_tracking()
    return result

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
