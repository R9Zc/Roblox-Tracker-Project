import os
import json
import logging
import time
from datetime import datetime, timezone
import httpx
from flask import Flask, jsonify, request
import gspread # Changed import structure
from firebase_admin import initialize_app, firestore, credentials

# --- Configuration ---
# Fetch credentials and configuration from environment variables
try:
    # Use environment variable for service account credentials
    SERVICE_ACCOUNT_INFO = json.loads(os.environ.get("GOOGLE_CREDENTIALS"))
    SHEET_KEY = os.environ.get("SHEET_KEY")
    TIMEZONE_NAME = os.environ.get("TIMEZONE", "America/New_York")
    PORT = int(os.environ.get("PORT", 8080))
    # Cache and Users
    ROBLOX_CACHE = json.loads(os.environ.get("ROBLOX_CACHE", '{"default": "value"}'))
except Exception as e:
    logging.error(f"Configuration Error: Failed to load environment variables or JSON. {e}")
    # Use placeholder values to allow function definitions to proceed, but expect failure later
    SERVICE_ACCOUNT_INFO = {}
    SHEET_KEY = "dummy_key"
    TIMEZONE_NAME = "UTC"
    PORT = 8080
    ROBLOX_CACHE = {}

# --- Firebase Setup (Assuming Firebase setup is separate or handles internal credentials) ---
# Initialize Firebase (assuming application default credentials or other method is used)
try:
    firebase_app = initialize_app(credentials.ApplicationDefault())
    db = firestore.client()
except Exception as e:
    logging.warning(f"Firebase initialization failed: {e}. Firestore access will be disabled.")
    db = None # Set to None if initialization fails

# --- Global State and Cache ---
# Simple in-memory cache for tracking session state
user_session_cache = ROBLOX_CACHE
app = Flask(__name__)

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

# User IDs to track (Replace with your actual list of Roblox user IDs)
TARGET_USER_IDS = [
    1992158202, # hulk_buster9402
    5120230728, # jsadujgha
    4491738101, # NOTKRZEN
    3263707365, # Cyrus_STORM
    3206102104, # TechnoBladeNeverDies
]

# --- Google Sheets Functions ---

def get_session_duration(start_time_utc):
    """Calculates the duration in minutes from a UTC start time to the current time."""
    try:
        start_dt = datetime.fromisoformat(start_time_utc.replace("Z", "+00:00")).astimezone(timezone.utc)
        current_dt = datetime.now(timezone.utc)
        duration_seconds = (current_dt - start_dt).total_seconds()
        return round(duration_seconds / 60, 2)
    except Exception as e:
        logging.error(f"Error calculating duration: {e}")
        return 0.0

def update_google_sheet(user_id, session_data):
    """Updates the Google Sheet with session data (end time and duration)."""
    if not SHEET_KEY:
        logging.error("SHEET_KEY is not set. Cannot update sheet.")
        return

    try:
        # Authenticate using the service account key loaded from the environment
        credentials = gspread.service_account_from_dict(SERVICE_ACCOUNT_INFO) # Changed function path
        client = gspread.authorize(credentials)

        # Open the spreadsheet by key
        spreadsheet = client.open_by_key(SHEET_KEY)
        worksheet = spreadsheet.worksheet("Raw_Data") # Assuming the tracking sheet is named 'Raw_Data'

        # Find the row by Session ID (assuming Session ID is in column A)
        session_id = session_data['Session_ID']
        cell = worksheet.find(session_id, in_column=1)

        # Prepare end time and duration data
        current_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        duration = get_session_duration(session_data['Start_Time_UTC'])
        
        # Data to update: End Time (G), Duration (H), End Time Local (J)
        end_time_col = 7
        duration_col = 8
        end_time_local_col = 10
        
        # Update cells
        worksheet.update_cell(cell.row, end_time_col, current_utc)
        worksheet.update_cell(cell.row, duration_col, duration)
        
        # Calculate and update local time
        start_dt = datetime.fromisoformat(session_data['Start_Time_UTC'].replace(" UTC", "+00:00"))
        local_dt = start_dt.astimezone(datetime.now(timezone.utc).tzinfo).replace(tzinfo=None)
        
        # NOTE: Since gspread does not automatically handle timezone conversions for the end time,
        # we will simply log the UTC end time and rely on Google Sheets formulas for local conversion.
        # However, to be helpful, let's just write the current UTC time.
        
        logging.info(f"Session {session_id} ended. Duration: {duration} mins.")
        
    except gspread.exceptions.CellNotFound:
        logging.error(f"Session ID {session_id} not found in sheet. Cannot end session.")
    except Exception as e:
        logging.error(f"Failed to update Google Sheet: {e}")


def write_new_session_to_sheet(session_data):
    """Writes a new active session row to the Google Sheet."""
    if not SHEET_KEY:
        logging.error("SHEET_KEY is not set. Cannot write new session.")
        return

    try:
        # Authenticate using the service account key
        credentials = gspread.service_account_from_dict(SERVICE_ACCOUNT_INFO) # Changed function path
        client = gspread.authorize(credentials)
        
        spreadsheet = client.open_by_key(SHEET_KEY)
        worksheet = spreadsheet.worksheet("Raw_Data")
        
        # Format the data for insertion
        row = [
            session_data['Session_ID'],
            session_data['User_Name'],
            session_data['Roblox_ID'],
            session_data['Game_Name'],
            session_data['Game_ID'],
            session_data['Start_Time_UTC'],
            '', # End Time (G) - Left blank until session ends
            '', # Duration (H) - Left blank until session ends
            session_data['Start_Time_Local'],
            ''  # End Time Local (J) - Left blank until session ends
        ]
        
        # Append the new row
        worksheet.append_row(row, value_input_option='USER_ENTERED')
        logging.info(f"New session started for {session_data['User_Name']}. Session ID: {session_data['Session_ID']}")

    except Exception as e:
        logging.error(f"Failed to write new session to Google Sheet: {e}")

# --- Roblox API Logic ---

async def check_user_presence(user_id):
    """Fetches presence data for a single Roblox user ID."""
    url = "https://presence.roblox.com/v1/presence/users"
    headers = {'Content-Type': 'application/json'}
    payload = {"userIds": [user_id]}

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status() # Raise an exception for bad status codes
            data = response.json()

            # Presence Status Map: 0=Offline, 1=Online, 2=InGame, 3=InStudio
            presence = data['userPresences'][0]
            
            is_playing = presence['userPresenceType'] == 2 # InGame
            game_id = presence.get('universeId', 0) if is_playing else 0
            game_name = presence.get('lastLocation', 'Website / Offline')
            
            if game_name == 'Website / Offline':
                is_playing = False # Override in case presence type was 1 (Online) but location is generic
                
            return {
                'Roblox_ID': user_id,
                'is_playing': is_playing,
                'game_id': game_id,
                'game_name': game_name,
                'user_name': presence.get('username', 'Unknown')
            }
        except httpx.HTTPError as e:
            logging.error(f"HTTP error fetching presence for {user_id}: {e}")
            return None
        except Exception as e:
            logging.error(f"General error fetching presence for {user_id}: {e}")
            return None

async def run_presence_check():
    """Main asynchronous function to check all users and update sessions."""
    
    # 1. Fetch data for all users concurrently
    async with httpx.AsyncClient() as client:
        tasks = [check_user_presence(uid) for uid in TARGET_USER_IDS]
        results = await client.aclose() # Ensure client is closed after requests

    current_time_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    current_time_local = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 2. Process results
    for result in results:
        if not result:
            continue
            
        user_id = result['Roblox_ID']
        is_playing_now = result['is_playing']
        cache_key = str(user_id)
        
        # Check if user was playing in the last check
        was_playing = user_session_cache.get(cache_key, {}).get('Playing', False)
        
        user_name = result['user_name']
        logging.debug(f"Checking presence for {user_name} ({user_id})...")

        # --- Transition Logic ---

        if is_playing_now and not was_playing:
            # START Session: User just started playing
            session_id = f"SESS_{int(time.time())}_{user_id}"
            
            new_session_data = {
                'Session_ID': session_id,
                'User_Name': user_name,
                'Roblox_ID': user_id,
                'Game_Name': result['game_name'],
                'Game_ID': result['game_id'],
                'Start_Time_UTC': current_time_utc,
                'Start_Time_Local': current_time_local,
            }
            
            # Update cache
            user_session_cache[cache_key] = {
                'Playing': True, 
                'Game': result['game_name'],
                'Session_ID': session_id,
                'Start_Time_UTC': current_time_utc
            }
            logging.info(f"START Session: {user_name} in {result['game_name']}. ID: {session_id}")

            # Write to Google Sheet
            write_new_session_to_sheet(new_session_data)
            
        elif not is_playing_now and was_playing:
            # END Session: User stopped playing (was playing, but isn't now)
            session_to_end = user_session_cache[cache_key]
            
            logging.debug(f"END Session: {user_name} left {session_to_end['Game']}.")

            # Update Google Sheet with end time and duration
            update_google_sheet(user_id, session_to_end)

            # Clear cache entry for this session
            user_session_cache[cache_key] = {'Playing': False, 'Game': 'Website / Offline'}
            
        elif is_playing_now and was_playing:
            # CONTINUE Session: Still playing the same game (or a different one, but we assume continuous session)
            session_id = user_session_cache[cache_key]['Session_ID']
            logging.debug(f"CONTINUE Session: {user_name} in {user_session_cache[cache_key]['Game']}: ID: {session_id}")
            
            # Optional: If the game changed, you might end the old session and start a new one here.
            # For simplicity, we keep the session running as long as they are 'Playing: True'

        else:
            # IDLE: Still offline/on website (wasn't playing, isn't playing)
            logging.debug(f"IDLE: {user_name} is offline/on website.")
            user_session_cache[cache_key] = {'Playing': False, 'Game': 'Website / Offline'}
            
        logging.debug(f"Cache Updated: {user_name} -> Playing: {user_session_cache[cache_key]['Playing']}, Game: {user_session_cache[cache_key]['Game']}")

    # 3. Update the persistent cache (environment variable)
    # This simulates saving state back to the hosting platform's environment variable.
    # NOTE: Render does not natively support updating environment variables from the running app,
    # so this is the part that will fail in a true Render environment. 
    # For a persistent solution, you MUST use a database like Firestore or Redis.
    # Since we initialized Firestore, we should use it!
    
    if db:
        try:
            cache_ref = db.collection('app_cache').document('roblox_tracker')
            # Convert user IDs in cache keys back to strings if necessary for JSON storage
            storable_cache = {str(k): v for k, v in user_session_cache.items()}
            cache_ref.set({'cache_data': storable_cache})
            logging.info("Successfully saved session cache to Firestore.")
        except Exception as e:
            logging.error(f"Failed to save session cache to Firestore: {e}")
            
    # As a fallback (for non-firestore environments)
    # os.environ["ROBLOX_CACHE"] = json.dumps(user_session_cache) # This line will not work on Render

# --- Flask Routes ---

@app.route('/track')
def track_sessions():
    """
    The main endpoint triggered by the Cron Job.
    Runs the asynchronous presence check.
    """
    logging.info(f"Received request to /track endpoint.")
    try:
        # Since Flask is synchronous, we run the async code in a synchronous context.
        import asyncio
        asyncio.run(run_presence_check())
        return jsonify({"status": "success", "message": "Tracking completed."}), 200
    except Exception as e:
        logging.error(f"Tracking run failed: {e}")
        return jsonify({"status": "error", "message": f"Tracking failed: {e}"}), 500

@app.route('/')
def index():
    """Simple status page."""
    return f"Roblox Tracker Project is running. Trigger tracking via the /track endpoint. Current Timezone: {TIMEZONE_NAME}"

# --- Entry Point ---

if __name__ == '__main__':
    # When running locally, Flask uses the standard run method
    logging.info(f"Starting Flask app on port {PORT}...")
    app.run(host='0.0.0.0', port=PORT, debug=False)
else:
    # When running under Gunicorn, app is the WSGI entry point
    logging.info(f"Flask app initialized for Gunicorn.")
