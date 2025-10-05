import logging
import json
import os # <-- NEW: Import for reading environment variables
import time
import requests
from flask import Flask, request
from datetime import datetime, timedelta
# UPDATED: Import both service_account (for file fallback) and service_account_from_dict (for env var)
from gspread import service_account, service_account_from_dict 

# --- Configuration and Initialization ---

# Set up basic logging configuration
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

app = Flask(__name__)

# Global variables for configuration, sheets client, and user presence cache
config = {}
sheet_client = None
user_cache = {}

# --- CRITICAL: HARDCODED USER CONFIGURATION ---
ROBLOX_USER_MAP = {
    "1992158202": "hulk_buster9402",
    "5120230728": "jsadujgha",
    "4491738101": "NOTKRZEN",
    "3263707365": "Cyrus_STORM",
    "3206102104": "TechnoBladeNeverDies"
}
# Assuming SHEET_NAME and WORKSHEET_NAME are consistent from your logs/screenshots
SHEET_NAME = "Roblox Tracker Log" # Replace with your actual Sheet Name if different
WORKSHEET_NAME = "Sheet1" # Replace with your actual Worksheet Name if different

# Configuration Constants
ENV_VAR_CREDS_NAME = 'GOOGLE_CREDENTIALS' # The name of your environment variable (updated)

def load_config():
    """Simulates loading configuration and sets necessary global variables."""
    global config
    
    config['ROBLOX_USER_IDS'] = ROBLOX_USER_MAP
    config['SHEET_NAME'] = SHEET_NAME
    config['WORKSHEET_NAME'] = WORKSHEET_NAME
    
    # Keeping the file path for completeness, but environment variable takes priority
    config['SHEETS_CREDENTIALS_PATH'] = 'YOUR_CREDS_FILE_NAME.json' 
    
    logging.info("Internal configuration loaded.")
    return True

def init_sheet_client():
    """
    Initializes the Google Sheets client, prioritizing credentials from 
    the environment variable or falling back to a file.
    """
    global sheet_client
    
    # 1. Try to load credentials from an environment variable (JSON string)
    creds_json_str = os.environ.get(ENV_VAR_CREDS_NAME)
    
    if creds_json_str:
        logging.info(f"Attempting to initialize Sheets client from environment variable: '{ENV_VAR_CREDS_NAME}'")
        try:
            creds_dict = json.loads(creds_json_str)
            # Use service_account_from_dict for environment variable credentials
            sheet_client = service_account_from_dict(creds_dict)
            logging.info("Google Sheets client initialized successfully from environment variable.")
            return

        except json.JSONDecodeError as e:
            # Critical error if the environment variable exists but is not valid JSON
            logging.critical(f"Failed to decode environment variable '{ENV_VAR_CREDS_NAME}': {e}. Ensure it is a raw JSON string.")
        except Exception as e:
            logging.critical(f"Failed to initialize Sheets client from environment variable: {e}")


    # 2. Fallback to file-based credentials (original logic)
    creds_path = config.get('SHEETS_CREDENTIALS_PATH')
    # Only try file path if the environment variable was not found/failed and the path isn't the placeholder
    if creds_path and creds_path != 'YOUR_CREDS_FILE_NAME.json':
        logging.info(f"Environment variable not available. Falling back to file path: '{creds_path}'")
        try:
            sheet_client = service_account(filename=creds_path)
            logging.info("Google Sheets client initialized successfully from file.")
        except Exception as e:
            # This critical log helps immediately identify if the credential file is missing or invalid
            logging.critical(f"Failed to initialize Google Sheets client from file: {e}")
            sheet_client = None
    else:
        if not sheet_client: # Only log if initialization failed
            logging.critical(f"Failed to initialize Google Sheets client. Neither environment variable ('{ENV_VAR_CREDS_NAME}') nor valid file path was provided.")


# --- Roblox API Interactions ---

def get_roblox_presence(user_ids):
    """Fetches presence data for a list of Roblox user IDs."""
    if not user_ids:
        return []

    url = "https://presence.roblox.com/v1/presence/users"
    headers = {'Content-Type': 'application/json'}
    # The Roblox API expects user IDs as numbers, so we convert them
    int_user_ids = [int(uid) for uid in user_ids]
    payload = {'userIds': int_user_ids}

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
        data = response.json()
        logging.info(f"HTTP Request: POST {url} \"HTTP/1.1 {response.status_code} {response.reason}\"")
        return data.get('userPresences', [])
    except requests.exceptions.RequestException as e:
        logging.error(f"Roblox API Request failed: {e}")
        return []
        
def get_game_details(presence):
    """
    Extracts the most reliable game name and ID from a presence object.
    
    Returns: (game_name, game_id_str)
    """
    game_name = "Website / Offline"
    game_id = 0
    
    # 'userPresenceType': 0=Offline, 1=Online/Website, 2=In-Game, 3=In-Studio
    is_playing = presence.get('userPresenceType') == 2
    
    if is_playing:
        location = presence.get('lastLocation')
        
        # Check if lastLocation is a meaningful string. If it's None, or an empty string, use default.
        if location and location.strip():
            game_name = location
        else:
            game_name = 'In Game (Name Hidden or Private)' # Updated default name
        
        # Try to find any available ID. Roblox uses placeId, rootPlaceId, or universeId.
        # We prioritize the most specific ID that is not None or 0.
        place_id = presence.get('placeId')
        root_place_id = presence.get('rootPlaceId')
        universe_id = presence.get('universeId')

        # Find the first truthy (non-zero/non-None) ID
        if place_id and place_id != 0:
            game_id = place_id
        elif root_place_id and root_place_id != 0:
            game_id = root_place_id
        elif universe_id and universe_id != 0:
            game_id = universe_id
    elif presence.get('userPresenceType') == 3:
        game_name = "Roblox Studio"
        
    game_id_str = str(game_id) if game_id else '0'
    return game_name, game_id_str


# --- Sheets Logging Function (Critical Section) ---

def log_session_to_sheet(session_data):
    """
    Logs a completed user session to the configured Google Sheet.
    Includes detailed logging of the failure type and data attempted.
    """
    if not sheet_client:
        logging.error("Sheet client not initialized. Cannot log session.")
        return False

    # Format the row data
    start_time_str = session_data['start_time'].strftime("%Y-%m-%d %H:%M:%S")
    end_time_str = session_data['end_time'].strftime("%Y-%m-%d %H:%M:%S")
    duration_str = f"{session_data['duration_minutes']:.2f}"

    # IMPORTANT: The sheet_row now includes the actual game_id
    sheet_row = [
        session_data['session_id'],
        session_data['username'],
        session_data['user_id'],
        session_data['game_name'],
        session_data['game_id'], # <-- Now using the extracted Game ID
        start_time_str,
        end_time_str,
        duration_str
    ]

    try:
        # 1. Open the sheet and target the worksheet
        sheet = sheet_client.open(config['SHEET_NAME']).worksheet(config['WORKSHEET_NAME'])
        
        # 2. Append the formatted row to the sheet
        sheet.append_row(sheet_row)

        logging.critical(f"Session Logged to Sheet SUCCESS: {session_data['username']} played {session_data['duration_minutes']:.2f} mins (ID: {session_data['game_id']}).")
        return True

    except Exception as e:
        # This critical logging block helps isolate permission, structure, or connection issues
        logging.critical(
            "--------------------------------------------------"
            f"\nCRITICAL WRITE FAILURE: Failed to write to Google Sheet for {session_data['username']}."
            f"\nError Type: {type(e).__name__}"
            f"\nError Message: {str(e)}"
            f"\nData Attempted: {sheet_row}"
            "\n--------------------------------------------------"
        )
        return False


# --- Core Logic and Cache Management ---

def check_presence_and_update_cache():
    """Main loop for checking presence and logging completed sessions."""
    if not config or 'ROBLOX_USER_IDS' not in config:
        logging.error("Configuration or ROBLOX_USER_IDS missing.")
        return

    # User IDs are retrieved as strings from the dictionary keys
    user_ids = list(config['ROBLOX_USER_IDS'].keys())
    
    # 1. Fetch current presence data
    presence_data = get_roblox_presence(user_ids)
    current_time = datetime.now()

    for presence in presence_data:
        # Ensure we treat user ID as a string for dictionary key lookup
        user_id = str(presence.get('userId'))
        username = config['ROBLOX_USER_IDS'].get(user_id, f"Unknown User ({user_id})")
        
        logging.info(f"Checking presence for {username} ({user_id})...")

        # Get current game details (Name and ID)
        game_name, game_id = get_game_details(presence)
        
        # 'userPresenceType': 0=Offline, 1=Online/Website, 2=In-Game, 3=In-Studio
        is_playing = presence.get('userPresenceType') == 2

        # --- Cache Initialization ---
        if user_id not in user_cache:
            user_cache[user_id] = {
                'username': username,
                'playing': is_playing,
                'game': game_name,
                'game_id': game_id, # <-- Store the ID on initialization
                'start_time': current_time,
                'session_id': f"SESS_{current_time.strftime('%Y%m%d%H%M%S')}_{user_id}"
            }
            logging.debug(f"Cache Initialized: {username} -> Playing: {is_playing}, Game: {game_name} (ID: {game_id})")
            continue

        cached_data = user_cache[user_id]

        # --- Transition Logic ---
        
        # Check if the session should end (left game OR switched games)
        # Note: We track by game_id for switches, as the name might change, but the ID should be stable.
        should_end_session = (cached_data['playing'] and not is_playing) or \
                             (cached_data['playing'] and is_playing and cached_data['game_id'] != game_id)
        
        if should_end_session:
            duration = current_time - cached_data['start_time']
            duration_minutes = duration.total_seconds() / 60

            session_data = {
                'session_id': cached_data['session_id'],
                'username': username,
                'user_id': user_id,
                'game_name': cached_data['game'],
                'game_id': cached_data['game_id'], # <-- Log cached ID
                'start_time': cached_data['start_time'],
                'end_time': current_time,
                'duration_minutes': duration_minutes
            }
            
            # Log the old session to the sheet
            log_session_to_sheet(session_data) 
            
            # If the user is still playing (i.e., switched games), start a new session
            if is_playing and cached_data['game_id'] != game_id:
                new_session_id = f"SESS_{current_time.strftime('%Y%m%d%H%M%S')}_{user_id}"
                user_cache[user_id].update({
                    'playing': True,
                    'game': game_name,
                    'game_id': game_id,
                    'start_time': current_time,
                    'session_id': new_session_id
                })
                logging.debug(f"SWITCH Session: {username} started playing {game_name} (ID: {game_id}).")
            else:
                # If they stopped playing (now idle/offline)
                user_cache[user_id].update({
                    'playing': False,
                    'game': game_name,
                    'game_id': game_id, # Update game_id to 0 or current status
                })
                logging.debug(f"IDLE: {username} is offline/on website.")


        # Case 3: Was IDLE, started playing -> Start new session
        elif not cached_data['playing'] and is_playing:
            new_session_id = f"SESS_{current_time.strftime('%Y%m%d%H%M%S')}_{user_id}"
            user_cache[user_id].update({
                'playing': True,
                'game': game_name,
                'game_id': game_id,
                'start_time': current_time,
                'session_id': new_session_id
            })
            logging.debug(f"START Session: {username} started playing {game_name} (ID: {game_id}).")

        # Case 4: Continued state (playing same game or still idle)
        elif is_playing and cached_data['playing'] and cached_data['game_id'] == game_id:
            # Still playing the same game, update the name in cache just in case
            user_cache[user_id]['game'] = game_name 
            logging.debug(f"CONTINUE Session: {username} in {game_name}")
            
        elif not is_playing and not cached_data['playing']:
             # Still idle
            user_cache[user_id].update({'game': game_name, 'game_id': game_id})
            logging.debug(f"IDLE: {username} is {game_name}.")


# --- Flask Routes ---

@app.route('/')
def index():
    """Simple status page."""
    status = "Online" if sheet_client else "Degraded (Sheet Client Failed)"
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Roblox Presence Tracker</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body {{ font-family: sans-serif; padding: 20px; background-color: #1e1e1e; color: #f0f0f0; }}
            h1, h2 {{ color: #4CAF50; }}
            pre {{ background-color: #2b2b2b; padding: 15px; border-radius: 8px; overflow-x: auto; white-space: pre-wrap; }}
            strong {{ color: #FFD700; }}
        </style>
    </head>
    <body>
        <h1>Roblox Presence Tracker Status</h1>
        <p>Service Status: <strong>{status}</strong></p>
        <p>This service is running on **port 5000** and can be triggered via the <code>/track</code> endpoint.</p>
        <h2>Current Cache (Game ID is now tracked)</h2>
        <pre>{json.dumps(user_cache, indent=2, default=str)}</pre>
    </body>
    </html>
    """
    return html

@app.route('/track')
def track_presence():
    """Endpoint to manually or automatically trigger the presence check."""
    logging.info("Starting presence check triggered by /track endpoint.")
    check_presence_and_update_cache()
    return "Presence check initiated successfully.", 200

# --- Application Startup ---

if __name__ == '__main__':
    if load_config():
        init_sheet_client() 
        
        # Initial check to populate the cache immediately on startup
        check_presence_and_update_cache()
        
        # Run Flask application on port 5000, which is standard for web services
        app.run(host='0.0.0.0', port=5000)
