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
    1992158202: "hulk_buster9402", 
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
    default_state = {"playing": False, "game_name": "Offline", "start_time_utc": None, "active_game_id": 0}
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
        
        # V19: Log to capture the RAW JSON response (for final confirmation)
        raw_api_data = {"userPresences": presence}
        logging.info(f"API Raw Response: {json.dumps(raw_api_data)}") 
        
        
        for item in presence:
            uid = item['userId']
            
            is_playing = item['userPresenceType'] in [1, 2, 3] 
            user_presence_type = item['userPresenceType'] 
            
            # --- V19: TRIPLE ID CHECK ---
            active_game_id = 0
            id_type = ""
            
            # 1. Check universeId (most common reliable ID for a game)
            universe_id = item.get('universeId')
            if universe_id is not None and universe_id != 0:
                active_game_id = universe_id
                id_type = "Universe ID"
            
            # 2. Check rootPlaceId (ID of the starting place in a game)
            elif active_game_id == 0:
                root_place_id = item.get('rootPlaceId')
                if root_place_id is not None and root_place_id != 0:
                    active_game_id = root_place_id
                    id_type = "Root Place ID"

            # 3. Check placeId (ID of the current specific server/place)
            elif active_game_id == 0:
                place_id = item.get('placeId')
                if place_id is not None and place_id != 0:
                    active_game_id = place_id
                    id_type = "Place ID"
            
            # --- END TRIPLE ID CHECK ---
            
            # Default display name
            display_game_name = "Unknown"

            # Determine if they are in a real game (Playing AND found a valid ID > 0)
            is_in_real_game = is_playing and active_game_id != 0

            if user_presence_type == 0:
                display_game_name = "Offline"
            elif is_playing:
                if is_in_real_game:
                    # Found an ID: Log the ID type and value
                    display_game_name = f"{id_type}: {active_game_id}" 
                else:
                    # V19 FALLBACK: No ID was found, but they are playing.
                    # Check the 'lastLocation' text field as a last resort.
                    last_location = item.get('lastLocation')
                    if last_location and last_location.strip() not in ["", "Website", "Unknown"]:
                        # If lastLocation contains meaningful text, use it.
                        display_game_name = f"Text Name: {last_location}"
                    else:
                        # Otherwise, fall back to "Website/Online" or "Game ID Hidden"
                        display_game_name = "Game ID Hidden"
            
            current_status[uid] = {
                "playing": is_playing, 
                "game_name": display_game_name, 
                "active_game_id": active_game_id # Store the authoritative ID (0 if not found)
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
    
    # --- TIME HANDLING ---
    try:
        ist_tz = pytz.timezone('Asia/Kolkata')
        current_time_utc = datetime.datetime.now(pytz.utc)
        current_time_ist = current_time_utc.astimezone(ist_tz)
        timestamp_log_str = current_time_ist.strftime("%Y-%m-%d %H:%M:%S")
        timestamp_cache_str = current_time_utc.strftime("%Y-%m-%d %H:%M:%S+00:00")
    except Exception:
        current_time_utc = datetime.datetime.now(datetime.timezone.utc)
        timestamp_log_str = current_time_utc.strftime("%Y-%m-%d %H:%M:%S (UTC)")
        timestamp_cache_str = current_time_utc.strftime("%Y-%m-%d %H:%M:%S+00:00")
        

    # --- COMPARE AND LOG CHANGES ---
    for uid, friend_name in FRIENDS_TO_TRACK.items():
        # V19: Default now uses active_game_id
        cached_default = {"playing": False, "game_name": "Offline", "start_time_utc": None, "active_game_id": 0}
        cached = cached_status.get(uid, cached_default)
        current = current_roblox_status.get(uid, {"playing": False, "game_name": "Offline", "active_game_id": 0})
        
        new_cache[uid] = cached.copy()
        
        # V19: Determine if the user is in a state with a non-zero ACTIVE GAME ID
        # Note: If active_game_id is 0, we still check game_name because it might contain the text fallback
        cached_is_tracking = cached['playing'] and cached['game_name'] not in ("Offline", "Website/Online", "Game ID Hidden")
        current_is_tracking = current['playing'] and current['game_name'] not in ("Offline", "Website/Online", "Game ID Hidden")
        
        # --- V19: Internal Logic Debug Log ---
        log_message = f"[{friend_name}] Cache State: playing={cached['playing']}, gName='{cached['game_name']}' | Current State: playing={current['playing']}, gName='{current['game_name']}'"
        logging.info(log_message)
        # -------------------------------------


        # 1. STARTED PLAYING A REAL GAME (No Tracking -> Has Tracking)
        if not cached_is_tracking and current_is_tracking:
            action = "STARTED PLAYING"
            game = current['game_name'] 
            logs_to_write.append([timestamp_log_str, friend_name, action, game, ""]) 
            
            new_cache[uid]["playing"] = True
            new_cache[uid]["game_name"] = current['game_name']
            new_cache[uid]["active_game_id"] = current['active_game_id']
            new_cache[uid]["start_time_utc"] = timestamp_cache_str
            logging.info(f"[{friend_name}] -> LOGGING START (Path 1)") 

        # 2. STOPPED PLAYING A REAL GAME (Has Tracking -> No Tracking OR Offline)
        elif cached_is_tracking and not current_is_tracking:
            action = "STOPPED PLAYING"
            game = cached['game_name'] # Use cached name for the log
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
            
            new_cache[uid]["playing"] = current['playing']
            new_cache[uid]["game_name"] = current['game_name']
            new_cache[uid]["active_game_id"] = current['active_game_id'] 
            new_cache[uid]["start_time_utc"] = None
            logging.info(f"[{friend_name}] -> LOGGING STOP (Path 2)") 

        # 3. GAME CHANGED (While playing A game that is being tracked)
        # Compare game_name or active_game_id for a switch
        elif cached_is_tracking and current_is_tracking and current['game_name'] != cached['game_name']:
            # Log the stop of the old game and the start of the new one
            # STOP LOG
            stop_action = "STOPPED PLAYING"
            stop_game = cached['game_name']
            logs_to_write.append([timestamp_log_str, friend_name, stop_action, stop_game, ""]) # No duration for switch

            # START LOG
            start_action = "STARTED PLAYING"
            start_game = current['game_name']
            logs_to_write.append([timestamp_log_str, friend_name, start_action, start_game, ""])
            
            new_cache[uid]["game_name"] = current['game_name']
            new_cache[uid]["active_game_id"] = current['active_game_id']
            new_cache[uid]["start_time_utc"] = timestamp_cache_str
            logging.info(f"[{friend_name}] -> LOGGING GAME SWITCH (Path 3)") 

        # 4. State Flip (No log, but update cache to reflect current status, even if it's "Game ID Hidden")
        else:
            # This path handles: No tracking change, or switching between Offline/Website/Game ID Hidden
            # Silent update to keep the cache fresh.
            new_cache[uid]["playing"] = current['playing']
            new_cache[uid]["game_name"] = current['game_name']
            new_cache[uid]["active_game_id"] = current['active_game_id']
            
            # If they just stopped playing, clear start_time_utc
            if not current['playing'] or current['game_name'] in ("Offline", "Website/Online", "Game ID Hidden"):
                new_cache[uid]["start_time_utc"] = None
            else:
                 # If they are currently playing and we just started tracking them, but we don't have a start time, set one.
                if new_cache[uid]["start_time_utc"] is None:
                    new_cache[uid]["start_time_utc"] = timestamp_cache_str

            logging.info(f"[{friend_name}] -> SILENT CACHE UPDATE (Path 4/5)") 
            
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
