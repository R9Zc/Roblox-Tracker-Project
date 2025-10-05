import os
import json
import logging
import requests
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from flask import Flask, jsonify, request
import gspread
from gspread.service_account import service_account_from_dict

# --- Configuration Constants (Loaded from Environment Variables) ---

# Sheet access environment variable name
GOOGLE_CREDS_ENV_VAR = 'GOOGLE_CREDENTIALS'

# Sheet Key/ID (read from environment variable)
SHEET_KEY = os.environ.get('SHEET_KEY')

# Timezone for local time logging (e.g., 'America/New_York')
TIMEZONE_STR = os.environ.get('TIMEZONE', 'UTC')
try:
    TIMEZONE = ZoneInfo(TIMEZONE_STR)
except ZoneInfoNotFoundError:
    logging.error(f"Invalid TIMEZONE: {TIMEZONE_STR}. Defaulting to UTC.")
    TIMEZONE = ZoneInfo('UTC')

# User IDs to track (Loaded from an environment variable for security and flexibility)
# Example value for ROBLOX_TRACK_USERS: {"hulk_buster9402": 1992158202, "jsadujgha": 5120230728}
ROBLOX_TRACK_USERS_JSON = os.environ.get('ROBLOX_TRACK_USERS', '{}')
try:
    USER_ID_MAP = json.loads(ROBLOX_TRACK_USERS_JSON)
except json.JSONDecodeError:
    logging.critical("ROBLOX_TRACK_USERS environment variable is invalid JSON. Cannot track users.")
    USER_ID_MAP = {}

# Internal Cache (In-memory storage)
user_cache = {}

# Roblox API Endpoints
ROBLOX_PRESENCE_URL = "https://presence.roblox.com/v1/presence/users"

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Google Sheets Initialization ---
gc = None

def init_sheet_client():
    """Initializes the gspread client using credentials from environment variable."""
    global gc
    if gc is not None:
        return True # Already initialized

    creds_json = os.environ.get(GOOGLE_CREDS_ENV_VAR)

    if not creds_json:
        logging.critical(f"Failed to initialize Google Sheets client: Environment variable '{GOOGLE_CREDS_ENV_VAR}' not found.")
        return False

    if not SHEET_KEY:
        logging.critical("Failed to initialize Google Sheets client: Environment variable 'SHEET_KEY' not found.")
        return False
        
    try:
        # Load credentials from the JSON string in the environment variable
        creds_dict = json.loads(creds_json)
        gc = service_account_from_dict(creds_dict)
        logging.info("Google Sheets client initialized successfully using environment credentials.")
        return True
    except json.JSONDecodeError:
        logging.critical("Failed to parse Google Sheets credentials (JSON Decode Error).")
        return False
    except Exception as e:
        logging.critical(f"Failed to initialize Google Sheets client: {type(e).__name__}: {e}")
        return False

def log_session_to_sheet(session_data: dict) -> bool:
    """Logs a completed session row to the Google Sheet."""
    if not init_sheet_client():
        logging.error("Sheet client not available. Cannot log session.")
        return False
    
    # 1. Open the Sheet and Worksheet
    try:
        sheet = gc.open_by_key(SHEET_KEY)
        # Assumes logging to the first worksheet
        worksheet = sheet.get_worksheet(0)
    except Exception as e:
        logging.critical(f"CRITICAL WRITE FAILURE: Failed to open sheet/worksheet. Error: {e}")
        return False

    # 2. Convert times to UTC strings
    start_time_utc = datetime.fromtimestamp(session_data['start_time'], tz=ZoneInfo('UTC'))
    end_time_utc = datetime.fromtimestamp(session_data['end_time'], tz=ZoneInfo('UTC'))

    start_time_str_utc = start_time_utc.strftime("%Y-%m-%d %H:%M:%S UTC")
    end_time_str_utc = end_time_utc.strftime("%Y-%m-%d %H:%M:%S UTC")

    # 3. Convert times to local timezone strings
    start_time_local = start_time_utc.astimezone(TIMEZONE)
    end_time_local = end_time_utc.astimezone(TIMEZONE)
    
    # Format the timezone abbreviation correctly
    tz_abbr = TIMEZONE.tzname(start_time_local)
    
    start_time_str_local = start_time_local.strftime(f"%Y-%m-%d %H:%M:%S {tz_abbr}")
    end_time_str_local = end_time_local.strftime(f"%Y-%m-%d %H:%M:%S {tz_abbr}")

    # 4. Prepare the row data (must match spreadsheet columns A-J)
    sheet_row = [
        session_data['session_id'],                   # A: Session ID
        session_data['username'],                     # B: User Name
        session_data['user_id'],                      # C: Roblox ID
        session_data['game_name'],                    # D: Game Name
        session_data['game_id'],                      # E: Game ID (0 if hidden)
        start_time_str_utc,                           # F: Start Time (UTC)
        end_time_str_utc,                             # G: End Time (UTC)
        f"{session_data['duration_minutes']:.2f}",    # H: Duration (Minutes)
        start_time_str_local,                         # I: Start Time (Local)
        end_time_str_local,                           # J: End Time (Local)
    ]

    # 5. Append the row
    try:
        worksheet.append_row(sheet_row)
        logging.info(f"SUCCESS: Logged session {session_data['session_id']} for {session_data['username']}")
        return True
    except Exception as e:
        # Log critical error including the data that failed to write
        logging.critical(
            f"CRITICAL WRITE FAILURE: Failed to append row to sheet. Error Type: {type(e).__name__}, "
            f"Error Message: {e}. Data Attempted: {sheet_row}"
        )
        return False

# --- Roblox Data Helpers ---

def get_game_details(presence: dict) -> tuple[int, str]:
    """
    Extracts the most reliable Game ID and Name from the Roblox presence data.
    Returns: (game_id, game_name)
    """
    # 1. Determine if user is playing
    user_presence_type = presence.get("userPresenceType", 0)
    is_playing = user_presence_type == 2 # 2 means InGame

    if not is_playing:
        return 0, "Website / Offline"

    # 2. Extract potential IDs
    game_id_candidates = [
        presence.get("placeId"),
        presence.get("rootPlaceId"),
        presence.get("universeId")
    ]
    
    active_game_id = 0
    # Find the first non-None, non-zero ID
    for candidate in game_id_candidates:
        if candidate is not None and candidate != 0:
            try:
                active_game_id = int(candidate)
                break
            except (ValueError, TypeError):
                continue

    # 3. Extract Game Name
    game_name = presence.get("lastLocation", "N/A")

    # Robust check: If playing but name is empty or uninformative
    if not game_name or game_name.strip() in ["N/A", "In Game", ""]:
        if active_game_id != 0:
            game_name = f"In Game (ID: {active_game_id})"
        else:
            # This happens when ID is hidden due to privacy settings
            game_name = "In Game (ID Hidden)"

    return active_game_id, game_name

def fetch_presence_data(user_ids: list[int]) -> dict:
    """Fetches raw presence data from Roblox API."""
    try:
        response = requests.post(
            ROBLOX_PRESENCE_URL,
            json={"userIds": user_ids},
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching Roblox presence: {e}")
        return {}

# --- Session Tracking Logic ---

def check_presence_and_update_cache():
    """Fetches presence data, checks for session changes, and updates the cache/logs sessions."""
    # Use standard library datetime
    current_time = int(datetime.now().timestamp())
    
    # 1. Fetch data for all users
    user_ids = list(USER_ID_MAP.values())
    if not user_ids:
        logging.warning("No users configured to track in ROBLOX_TRACK_USERS.")
        return

    raw_data = fetch_presence_data(user_ids)
    user_presences = {p['userId']: p for p in raw_data.get('userPresences', [])}

    # 2. Process each user
    for username, user_id in USER_ID_MAP.items():
        presence = user_presences.get(user_id)
        if not presence:
            continue

        # Get current state from Roblox
        current_game_id, current_game_name = get_game_details(presence)
        current_playing = current_game_id != 0

        # Get cached state (or initialize it)
        if user_id not in user_cache:
            user_cache[user_id] = {
                'user_id': user_id,
                'username': username,
                'playing': False,
                'game_id': 0,
                'game_name': "N/A",
                'session_start': None,
                'session_id': None
            }

        c = user_cache[user_id]
        
        logging.info(f"Checking presence for {username} ({user_id})...")
        logging.debug(f"Cached State: Playing={c['playing']}, Game='{c['game_name']}' (ID: {c['game_id']})")
        logging.debug(f"Current State: Playing={current_playing}, Game='{current_game_name}' (ID: {current_game_id})")

        # --- A. Session End Condition (User left game OR switched game) ---
        should_end_session = (
            c['playing'] and 
            (
                not current_playing or                                    # User left the game
                current_game_id != c['game_id']                          # User switched to a different game/ID
            )
        )

        if should_end_session:
            # Log the old session end
            session_duration = current_time - c['session_start']
            session_log = {
                'session_id': c['session_id'],
                'user_id': c['user_id'],
                'username': c['username'],
                'game_id': c['game_id'],
                'game_name': c['game_name'],
                'start_time': c['session_start'],
                'end_time': current_time,
                'duration_minutes': session_duration / 60.0
            }
            log_session_to_sheet(session_log)
            
            # Reset cache for the user
            c['playing'] = False
            c['game_id'] = 0
            c['game_name'] = "N/A"
            c['session_start'] = None
            c['session_id'] = None
            
            logging.info(f"Session Ended/Switched: {c['username']} session {session_log['session_id']}")

        # --- B. Session Start Condition (User started playing OR finished a switch) ---
        if current_playing and (not c['playing'] or current_game_id != c['game_id']):
            
            # Start a new session
            new_session_id = f"SESS_{user_id}_{current_time}"
            
            c['playing'] = True
            c['game_id'] = current_game_id
            c['game_name'] = current_game_name
            c['session_start'] = current_time
            c['session_id'] = new_session_id
            
            logging.info(f"START Session: {username} in game '{current_game_name}' (ID: {current_game_id}). Session ID: {new_session_id}")

        # --- C. Continue Condition (Still playing the same game) ---
        elif c['playing'] and current_playing and current_game_id == c['game_id']:
            # No action needed, session continues. Just ensure cache reflects current state
            c['game_name'] = current_game_name
            logging.debug(f"CONTINUE Session: {username}")
        
        # Cache updated implicitly by modifying the dict 'c' which is a reference to user_cache[user_id]

# --- Flask App Setup ---

app = Flask(__name__)

# Route to trigger the tracking check manually
@app.route('/track', methods=['GET'])
def track_users():
    """Endpoint to manually trigger the user presence tracking."""
    # Ensure client is initialized before tracking starts
    if not init_sheet_client():
        return jsonify({"status": "error", "message": "Google Sheets client failed to initialize. Check GOOGLE_CREDENTIALS."}), 500
        
    check_presence_and_update_cache()
    
    # Return a status report
    response = {
        "status": "success",
        "message": "Tracking check executed.",
        "cache_size": len(user_cache),
        "tracked_users": list(USER_ID_MAP.keys()),
        "current_cache_state": user_cache
    }
    return jsonify(response)

# Simple root route for health check
@app.route('/')
def health_check():
    """Health check route for the deployment service."""
    return jsonify({"status": "ready", "service": "Roblox Presence Tracker"})

# --- Application Entry Point ---
# The Gunicorn server (from Procfile) will call app:app directly.
# This block is primarily for local testing.
if __name__ == '__main__':
    # Initialize the sheet client on startup to catch errors early
    init_sheet_client()
    
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
