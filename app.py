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
        
    default_state = {"playing": False, "game_name": "N/A", "start_time_utc": None}
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
            
            # userPresenceType: 0=Offline, 1=In Game, 2=In Studio, 3=Online/Website
            is_playing = item['userPresenceType'] in [1, 2, 3] 
            
            # --- Game Name Fix (V3) ---
            game_name = item.get('lastLocation')
            place_id = item.get('placeId')
            
            if not game_name or game_name.strip() == "":
                if place_id and place_id != 0:
                    game_name = f"Game ID: {place_id}"
                else:
                    game_name = "N/A"

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
    
    # --- TIME HANDLING FIX: Use UTC for internal caching and IST for logging ---
    ist_tz = pytz.timezone('Asia/Kolkata')
    current_time_utc = datetime.datetime.now(pytz.utc)
    current_time_ist = current_time_utc.astimezone(ist_tz)
    timestamp_log_str = current_time_ist.strftime("%Y-%m-%d %H:%M:%S")
    timestamp_cache_str = current_time_utc.strftime("%Y-%m-%d %H:%M:%S+00:00") # UTC format for cache
        

    # --- COMPARE AND LOG CHANGES ---
    for uid, friend_name in FRIENDS_TO_TRACK.items():
        # Using a new key for start time in the cache logic
        current = current_roblox_status.get(uid, {"playing": False, "game_name": "N/A"})
        cached = cached_status.get(uid, {"playing": False, "game_name": "N/A", "start_time_utc": None})
        
        current_game_name = current['game_name']
        cached_game_name = cached['game_name']

        # Prepare data for the new cache (IMPORTANT: Copy the current status)
        new_cache[uid] = {
            "playing": current['playing'], 
            "game_name": current_game_name, 
            "start_time_utc": cached['start_time_utc'] # Preserve old UTC start time
        }

        # 1. STARTED PLAYING (OFFLINE -> ONLINE)
        if not cached['playing'] and current['playing']:
            action = "STARTED PLAYING"
            game = current_game_name
            logs_to_write.append([timestamp_log_str, friend_name, action, game, ""]) 
            # Set the new start time in UTC
            new_cache[uid]["start_time_utc"] = timestamp_cache_str

        # 2. STOPPED PLAYING (ONLINE -> OFFLINE)
        elif cached['playing'] and not current['playing']:
            action = "STOPPED PLAYING"
            game = cached_game_name 
            duration_minutes = ""
            
            if cached['start_time_utc']:
                try:
                    # Parse UTC string from cache
                    start_time_utc_dt = datetime.datetime.strptime(cached['start_time_utc'], "%Y-%m-%d %H:%M:%S+00:00")
                    start_time_utc_aware = pytz.utc.localize(start_time_utc_dt)
                    
                    duration = current_time_utc - start_time_utc_aware
                    duration_minutes = round(duration.total_seconds() / 60, 2)
                except Exception as e:
                    logging.error(f"Error calculating duration for {friend_name}: {e}")
            
            logs_to_write.append([timestamp_log_str, friend_name, action, game, duration_minutes])
            # Clear start time when they stop
            new_cache[uid]["start_time_utc"] = None
            
        # 3. Game Changed (While still playing): Update game name, but preserve UTC start time.
        elif current['playing'] and cached['playing'] and current_game_name != cached_game_name:
            # We don't log this, but we update the cached game name for the eventual STOP log
            new_cache[uid]["game_name"] = current_game_name
            
        # 4. Still Playing Same Game: Preserve original UTC start_time. No logging.


    # --- WRITE LOGS AND SAVE CACHE ---
    if logs_to_write:
        data_worksheet.append_rows(logs_to_write)
    
    save_cached_status(cache_worksheet, new_cache)

    return f"SUCCESS: Checked {len(FRIENDS_TO_TRACK)} friends. {len(logs_to_write)} new events logged."
    
# ---------------------------------------------
# 4. WEB ROUTES
# ---------------------------------------------

# Primary tracking route (used by Cron-Job)
@app.route('/track')
# Health check route (used by Render's internal monitor)
@app.route('/') 
def main_tracker_route():
    result = execute_tracking()
    return result

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
