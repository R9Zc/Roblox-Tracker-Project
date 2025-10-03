from flask import Flask
import datetime
import gspread
import os
import requests
import json
import logging
import pytz

# Set up logging early
logging.basicConfig(level=logging.INFO)

# ------------------- Global Configuration -------------------
app = Flask(__name__)

# *** REPLACE THIS WITH YOUR EXACT SPREADSHEET NAME ***
GOOGLE_SHEET_NAME = "Minute Tracker Data"

# *** LIST YOUR FRIENDS' USER IDS AND NAMES ***
FRIENDS_TO_TRACK = {
    5120230728: "jsadujgha",
    4491738101: "NOTKRZEN",
    3263707365: "Cyrus_STORM",
    1992158202: "hulk_buster9402",
}

# API Endpoints and Sheet Names
ROBLOX_STATUS_URL = "https://presence.roblox.com/v1/presence/users"
ROBLOX_GAME_BULK_URL = "https://games.roblox.com/v1/games/multiget-info?universeIds="
DATA_SHEET_NAME = "Activity Log"
CACHE_SHEET_NAME = "Cache"

# States that should NOT be considered active 'tracking' for duration calculation
NON_TRACKING_STATES = ("Offline", "Game ID Hidden", "Website", "Unknown", "ID Lookup Failed")


# ------------------- Helper Functions -------------------
def get_cached_status(worksheet):
    """Reads the last known status from the Cache sheet (Cell A2)."""
    try:
        json_str = worksheet.acell('A2').value
        if json_str and json_str.strip() not in ('{}', ''):
            return json.loads(json_str)
    except Exception as e:
        logging.error(f"Error loading cache: {e}")
    
    # Initialize cache with defaults if loading fails
    default_state = {"playing": False, "game_name": "Offline", "start_time_utc": None, "active_game_id": 0}
    return {uid: default_state for uid in FRIENDS_TO_TRACK}


def save_cached_status(worksheet, status_data):
    """Writes the current status to the Cache sheet (Cell A2)."""
    try:
        worksheet.update('A2', [[json.dumps(status_data)]])
    except Exception as e:
        logging.error(f"Error saving cache: {e}")


def get_game_names_bulk(universe_ids):
    """Fetches actual game names for a list of Universe IDs in a single, efficient request."""
    if not universe_ids:
        return {}
    try:
        # Construct the URL with comma-separated IDs
        url = f"{ROBLOX_GAME_BULK_URL}{','.join(map(str, universe_ids))}"
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        # Return a dictionary mapping universeId to GameName
        return {g['universeId']: g['name'] for g in data if 'universeId' in g and 'name' in g}
    except Exception as e:
        logging.error(f"Failed to fetch game names: {e}")
        return {}


def check_roblox_status(user_ids):
    """Fetches current status from the Roblox API and determines the game name."""
    try:
        # 1. Fetch raw presence data
        response = requests.post(ROBLOX_STATUS_URL, json={"userIds": list(user_ids)}, timeout=10)
        response.raise_for_status()
        presence = response.json().get('userPresences', [])

        # 2. Collect unique universe IDs and fetch names in bulk
        universe_ids = {item.get('universeId') for item in presence if item.get('universeId')}
        game_name_map = get_game_names_bulk(list(universe_ids))

        current_status = {}
        for item in presence:
            uid = item['userId']
            # userPresenceType: 1=Online, 2=In Game, 3=In Studio
            is_playing = item['userPresenceType'] in [1, 2, 3]

            universe_id = item.get('universeId')
            root_place_id = item.get('rootPlaceId')
            place_id = item.get('placeId')
            last_location = item.get('lastLocation')

            # Prioritize universeId, then rootPlaceId, then placeId for active ID
            active_game_id = universe_id or root_place_id or place_id or 0
            display_game_name = "Offline"

            # 3. Determine the display game name based on status
            if is_playing:
                if universe_id and universe_id in game_name_map:
                    # Case A: Universe ID found and name successfully fetched
                    display_game_name = game_name_map[universe_id]
                
                elif last_location and last_location.strip() not in ["", "Website", "Unknown", None]:
                    # Case B: No Game ID provided (e.g., restricted user), fallback to location text
                    # V21 adjustment: just use the location text for cleaner logs
                    display_game_name = last_location
                
                else:
                    # Case C: Playing, but no ID and no useful text
                    display_game_name = "Game ID Hidden"

            current_status[uid] = {
                "playing": is_playing,
                "game_name": display_game_name,
                "active_game_id": active_game_id
            }

        return current_status
    except Exception as e:
        logging.error(f"Roblox API failed: {e}")
        return None


# ------------------- Main Tracking Logic -------------------
def execute_tracking():
    """Fetches status, compares to cache, logs events, and updates cache."""
    # 1. Sheet connection setup
    try:
        creds_json = os.environ.get('GOOGLE_CREDENTIALS')
        if not creds_json:
            return "ERROR: GOOGLE_CREDENTIALS missing."
        gc = gspread.service_account_from_dict(json.loads(creds_json))
        spreadsheet = gc.open(GOOGLE_SHEET_NAME)
        data_ws = spreadsheet.worksheet(DATA_SHEET_NAME)
        cache_ws = spreadsheet.worksheet(CACHE_SHEET_NAME)
    except Exception as e:
        logging.error(f"Google Sheets connection failed: {e}")
        return f"ERROR: Google Sheets connection failed. Details: {e}"

    # 2. Fetch statuses
    cached_status = get_cached_status(cache_ws)
    current_status = check_roblox_status(FRIENDS_TO_TRACK.keys())
    if not current_status:
        return "ERROR: Could not fetch Roblox status."

    new_cache = {}
    logs = []

    # 3. Time handling
    ist_tz = pytz.timezone('Asia/Kolkata')
    now_utc = datetime.datetime.now(pytz.utc)
    now_ist = now_utc.astimezone(ist_tz)
    timestamp_log = now_ist.strftime("%Y-%m-%d %H:%M:%S")
    timestamp_cache = now_utc.strftime("%Y-%m-%d %H:%M:%S+00:00")

    # 4. Compare and log changes
    for uid, name in FRIENDS_TO_TRACK.items():
        # Retrieve or set default values
        cached = cached_status.get(uid, {"playing": False, "game_name": "Offline", "start_time_utc": None, "active_game_id": 0})
        current = current_status.get(uid, {"playing": False, "game_name": "Offline", "active_game_id": 0})

        new_cache[uid] = cached.copy()
        
        # Determine if the current state is a trackable game
        cached_tracking = cached['playing'] and cached['game_name'] not in NON_TRACKING_STATES
        current_tracking = current['playing'] and current['game_name'] not in NON_TRACKING_STATES

        logging.info(f"[{name}] Cache: {cached['game_name']} | Current: {current['game_name']}")

        # START: Not tracked -> Tracked
        if not cached_tracking and current_tracking:
            logs.append([timestamp_log, name, "STARTED PLAYING", current['game_name'], ""])
            new_cache[uid].update({
                "playing": True,
                "game_name": current['game_name'],
                "active_game_id": current['active_game_id'],
                "start_time_utc": timestamp_cache
            })

        # STOP: Tracked -> Not tracked (or Offline)
        elif cached_tracking and not current_tracking:
            duration = ""
            if cached['start_time_utc']:
                try:
                    start_dt = pytz.utc.localize(datetime.datetime.strptime(cached['start_time_utc'], "%Y-%m-%d %H:%M:%S+00:00"))
                    duration = round((now_utc - start_dt).total_seconds() / 60, 2)
                except Exception as e:
                    logging.error(f"Duration calc error: {e}")
                    
            logs.append([timestamp_log, name, "STOPPED PLAYING", cached['game_name'], duration])
            new_cache[uid].update({
                "playing": current['playing'],
                "game_name": current['game_name'],
                "active_game_id": current['active_game_id'],
                "start_time_utc": None # Session ended
            })

        # SWITCH: Tracked -> Tracked, but game name OR ID changed (V21 Improvement)
        elif cached_tracking and current_tracking and (current['game_name'] != cached['game_name'] or current['active_game_id'] != cached['active_game_id']):
            # Log stop of old game
            logs.append([timestamp_log, name, "STOPPED PLAYING", cached['game_name'], ""])
            # Log start of new game
            logs.append([timestamp_log, name, "STARTED PLAYING", current['game_name'], ""])
            new_cache[uid].update({
                "game_name": current['game_name'],
                "active_game_id": current['active_game_id'],
                "start_time_utc": timestamp_cache # Reset start time for the new session
            })

        # SILENT UPDATE: Continuous session (Tracked->Tracked with no change) or continuous non-tracking
        else:
            new_cache[uid].update({
                "playing": current['playing'],
                "game_name": current['game_name'],
                "active_game_id": current['active_game_id'],
                # Preserve the start time if still tracking, otherwise set to None
                "start_time_utc": cached['start_time_utc'] if cached_tracking else (timestamp_cache if current_tracking else None)
            })

    # 5. Write data and save cache
    if logs:
        data_ws.append_rows(logs)

    save_cached_status(cache_ws, new_cache)
    return f"SUCCESS: Checked {len(FRIENDS_TO_TRACK)} friends. {len(logs)} new events logged."


# ------------------- Flask Routes -------------------
@app.route('/')
@app.route('/track')
def main_route():
    return execute_tracking()


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
