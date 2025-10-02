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
# ==========================================================
FRIENDS_TO_TRACK = {
    5120230728: "jsadujgha", 
    4491738101: "NOTKRZEN", 
    3263707365: "Cyrus_STORM",
}
# ==========================================================

# Roblox API Endpoint and Environment Variable Key
ROBLOX_STATUS_URL = "https://presence.roblox.com/v1/presence/users"
CACHE_ENV_KEY = "ROBLOX_CACHE" # The name of the environment variable

# ---------------------------------------------
# 2. HELPER FUNCTIONS (NOW USING ENVIRONMENT VARIABLES)
# ---------------------------------------------

def get_cached_status():
    """Reads the last known status from the ROBLOX_CACHE environment variable."""
    # Get the raw JSON string from the environment variable
    json_str = os.environ.get(CACHE_ENV_KEY)
    
    if json_str:
        try:
            return json.loads(json_str)
        except Exception as e:
            logging.error(f"Error loading cache JSON: {e}")
            pass # Use default cache if loading fails
            
    # Initialize cache if the variable is empty or broken
    default_state = {"playing": False, "game_name": "N/A", "start_time": None}
    return {uid: default_state for uid in FRIENDS_TO_TRACK}

def save_cached_status(status_data):
    """Prints the new status JSON string to the console for Render to capture."""
    # We don't save the variable here; Render captures the output in Step 2.
    # The output MUST be exactly 'RENDER_SET_ENV_START{"key":"ROBLOX_CACHE", "value":"..."}RENDER_SET_ENV_END'
    
    # We must reset the variable in the environment for the next run.
    json_str = json.dumps(status_data)
    
    # This specific print statement tells Render to update the environment variable
    # We will set up the corresponding Render setting in the next step
    print(f'RENDER_SET_ENV_START{{"key":"{CACHE_ENV_KEY}", "value":{json.dumps(json_str)}}}RENDER_SET_ENV_END')

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
# 3. THE MAIN TRACKING LOGIC (NO LOGIC CHANGE)
# ---------------------------------------------
def run_tracking_logic():
    # ... [The rest of the run_tracking_logic function is the same] ...
    # (The logic for connecting to sheets, checking status, logging starts/stops, 
    # and calculating duration remains IDENTICAL to the previous version.)
    # We only changed the get_cached_status and save_cached_status helper functions.
    
    # Note: For brevity here, assume you paste the full logic from the prior working version
    # (The one with duration calculation) here, keeping only the updated helper functions above.
    
    # --- Connect to Google Sheets ---
    try:
        gc = gspread.service_account(filename="credentials.json") 
        spreadsheet = gc.open(GOOGLE_SHEET_NAME)
        worksheet = spreadsheet.sheet1 
    except Exception as e:
        logging.error(f"Google Sheets connection failed: {e}")
        return f"ERROR: Google Sheets connection failed. Details: {e}"

    # --- Get cached and current status ---
    cached_status = get_cached_status()
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
        worksheet.append_rows(logs_to_write)
    
    save_cached_status(new_cache)

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
