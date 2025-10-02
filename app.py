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
# ==========================================================
FRIENDS_TO_TRACK = {
    5120230728: "jsadujgha", 
    4491738101: "NOTKRZEN", 
    3263707365: "Cyrus_STORM",
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
        
    default_state = {"playing": False, "game_name": "N/A", "start_time": None}
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
            is_playing = item['userPresenceType'] in [1, 2] 
            game_name = item.get('lastLocation') if is_playing and item.get('lastLocation') else "N/A"
            
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
    
    # --- TIMEZONE FIX: Convert time to IST ---
    try:
        ist = pytz.timezone('Asia/Kolkata')
        current_time_dt = datetime.datetime.now(ist)
        timestamp_str = current_time_dt.strftime("%Y-%m-%d %H:%M:%S")
    except NameError:
        current_time_dt = datetime.datetime.now()
        timestamp_str = current_time_dt.strftime("%Y-%m-%d %H:%M:%S (UTC/Server Time)")
        logging.error("Pytz not found. Check requirements.txt.")
        

    # --- COMPARE AND LOG CHANGES ---
    for uid, friend_name in FRIENDS_TO_TRACK.items():
        current = current_roblox_status.get(uid, {"playing": False, "game_name": "N/A"})
        cached = cached_status.get(uid, {"playing": False, "game_name": "N/A", "start_time": None})
        
        current_game_name = current['game_name']
        cached_game_name = cached['game_name']

        # --- Prepare data for the new cache ---
        new_cache[uid] = {
            "playing": current['playing'], 
            "game_name": current_game_name, 
            "start_time": cached['start_time']
        }

        # 1. STARTED PLAYING (OFFLINE -> ONLINE)
        if not cached['playing'] and current['playing']:
            action = "STARTED PLAYING"
            game = current_game_name
            logs_to_write.append([timestamp_str, friend_name, action, game, ""]) 
            new_cache[uid]["start_time"] = timestamp_str

        # 2. STOPPED PLAYING (ONLINE -> OFFLINE)
        elif cached['playing'] and not current['playing']:
            action = "STOPPED PLAYING"
            game = cached_game_name 
            duration_minutes = ""
            
            if cached['start_time']:
                try:
                    start_time_dt = datetime.datetime.strptime(cached['start_time'], "%Y-%m-%d %H:%M:%S")
                    ist_tz = pytz.timezone('Asia/Kolkata')
                    start_time_dt_aware = ist_tz.localize(start_time_dt)
                    
                    duration = current_time_dt - start_time_dt_aware
                    duration_minutes = round(duration.total_seconds() / 60, 2)
                except Exception as e:
                    logging.error(f"Error calculating duration for {friend_name}: {e}")
            
            logs_to_write.append([timestamp_str, friend_name, action, game, duration_minutes])
            new_cache[uid]["start_time"] = None
            
        # 3. If currently playing and the game changed: 
        #    We update the cache but DO NOT log the event.
        elif current['playing'] and cached['playing'] and current_game_name != cached_game_name:
            new_cache[uid]["start_time"] = timestamp_str
            
        # 4. If currently playing, no change in status, and game didn't change: 
        #    We preserve the original start_time.
        elif current['playing']:
            new_cache[uid]["start_time"] = cached['start_time']


    # --- D. WRITE LOGS AND SAVE CACHE ---
    if logs_to_write:
        data_worksheet.append_rows(logs_to_write)
    
    save_cached_status(cache_worksheet, new_cache)

    return f"SUCCESS: Checked {len(FRIENDS_TO_TRACK)} friends. {len(logs_to_write)} new events logged."
    
# ---------------------------------------------
# 4. WEB ROUTES
# ---------------------------------------------

# This is the primary route that runs the script (used by your external scheduler)
@app.route('/track')
def track():
    result = execute_tracking()
    return result

# This is the new, empty route used by Render's health check
@app.route('/')
def health_check():
    return "OK"

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
```eof

---
