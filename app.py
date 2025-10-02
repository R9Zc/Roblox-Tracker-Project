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
# *** 1. CRITICAL: REPLACE THIS WITH YOUR EXACT SPREADSHEET NAME ***
# ==========================================================
GOOGLE_SHEET_NAME = "Minute Tracker Data" 
# ==========================================================

# ==========================================================
# *** 2. CRITICAL: LIST YOUR FRIENDS' USER IDs AND NAMES ***
# ==========================================================
FRIENDS_TO_TRACK = {
    5120230728: "jsadujgha", 
    4491738101: "NOTKRZEN", 
    3263707365: "Cyrus_STORM",
}
# ==========================================================

# Roblox API Endpoint and Sheet Names
ROBLOX_STATUS_URL = "https://presence.roblox.com/v1/presence/users"
DATA_SHEET_NAME = "Activity Log"  # Must match the name of your main logging tab
CACHE_SHEET_NAME = "Cache"       # Must match the name of your cache tab

# ---------------------------------------------
# 2. HELPER FUNCTIONS (GOOGLE SHEET CACHE)
# ---------------------------------------------

def get_cached_status(worksheet):
    """Reads the last known status from the Cache sheet (Cell A2)."""
    try:
        # Get the JSON string from the cache sheet (always cell A2)
        json_str = worksheet.acell('A2').value
        if json_str:
            return json.loads(json_str)
    except Exception as e:
        logging.error(f"Error loading cache from Sheet: {e}")
        
    # Initialize cache if the value is empty or broken
    default_state = {"playing": False, "game_name": "N/A", "start_time": None}
    return {uid: default_state for uid in FRIENDS_TO_TRACK}

def save_cached_status(worksheet, status_data):
    """Writes the current status to the Cache sheet (Cell A2)."""
    try:
        json_str = json.dumps(status_data)
        # Update the cache cell (A2) with the new JSON string
        worksheet.update('A2', json_str)
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
            # userPresenceType 1 or 2 means they are online/playing
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
    # --- Connect to Google Sheets ---
    try:
        # Connect to Google Sheets service using credentials.json
        gc = gspread.service_account(filename="credentials.json") 
        spreadsheet = gc.open(GOOGLE_SHEET_NAME)
        
        # *** THE FIX: Explicitly open the worksheets by name ***
        data_worksheet = spreadsheet.worksheet(DATA_SHEET_NAME) 
        cache_worksheet = spreadsheet.worksheet(CACHE_SHEET_NAME)
        # ---------------------------------------------------
        
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
    
    current_time_dt = datetime.datetime.now()
    timestamp_str = current_time_dt.strftime("%Y-%m-%d %H:%M:%S")

    # --- COMPARE AND LOG CHANGES ---
    for uid, friend_name in FRIENDS_TO_TRACK.items():
        current = current_roblox_status.get(uid, {"playing": False, "game_name": "N/A"})
        cached = cached_status.get(uid, {"playing": False, "game_name": "N/A", "start_time": None})
        
        # Prepare data for the new cache
        new_cache[uid] = {
            "playing": current['playing'], 
            "game_name": current['game_name'], 
            "start_time": cached['start_time']
        }

        # 1. STARTED PLAYING
        if not cached['playing'] and current['playing']:
            action = "STARTED PLAYING"
            game = current['game_name']
            logs_to_write.append([timestamp_str, friend_name, action, game, ""]) 
            new_cache[uid]["start_time"] = timestamp_str

        # 2. STOPPED PLAYING
        elif cached['playing'] and not current['playing']:
            action = "STOPPED PLAYING"
            game = cached['game_name']
            duration_minutes = ""
            
            if cached['start_time']:
                try:
                    start_time_dt = datetime.datetime.strptime(cached['start_time'], "%Y-%m-%d %H:%M:%S")
                    duration = current_time_dt - start_time_dt
                    duration_minutes = round(duration.total_seconds() / 60, 2)
                except Exception:
                    pass
            
            logs_to_write.append([timestamp_str, friend_name, action, game, duration_minutes])
            new_cache[uid]["start_time"] = None
            
        # 3. GAME CHANGED
        elif current['playing'] and cached['playing'] and current['game_name'] != cached['game_name']:
            
            # Log STOP for the old game
            old_game = cached['game_name']
            duration_minutes = ""
            if cached['start_time']:
                try:
                    start_time_dt = datetime.datetime.strptime(cached['start_time'], "%Y-%m-%d %H:%M:%S")
                    duration = current_time_dt - start_time_dt
                    duration_minutes = round(duration.total_seconds() / 60, 2)
                except Exception:
                    pass

            logs_to_write.append([timestamp_str, friend_name, "STOPPED PLAYING", old_game, duration_minutes])
            
            # Log START for the new game
            new_game = current['game_name']
            logs_to_write.append([timestamp_str, friend_name, "STARTED PLAYING", new_game, ""])
            
            # Update cache with new start time
            new_cache[uid]["start_time"] = timestamp_str
            
        # 4. If currently playing and no change, keep the old start_time in the cache
        elif current['playing']:
            new_cache[uid]["start_time"] = cached['start_time']


    # --- D. WRITE LOGS AND SAVE CACHE ---
    if logs_to_write:
        data_worksheet.append_rows(logs_to_write)
    
    save_cached_status(cache_worksheet, new_cache)

    return f"SUCCESS: Checked {len(FRIENDS_TO_TRACK)} friends. {len(logs_to_write)} new events logged."
    
# ---------------------------------------------
# 4. WEB ROUTE AND APP RUNNER
# ---------------------------------------------
@app.route('/')
def index():
    result = run_tracking_logic()
    return result

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
