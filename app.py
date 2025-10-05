import os
import json
import logging
import time
from datetime import datetime, timezone
import httpx
from flask import Flask, jsonify, request
import gspread
from firebase_admin import initialize_app, firestore, credentials
import asyncio 
import traceback 
# Import modules for explicit timezone handling (if available, relying on system TZ otherwise)
# from zoneinfo import ZoneInfo # Standard in Python 3.9+

# --- Configuration ---
# Fetch credentials and configuration from environment variables
try:
    # Use environment variable for service account credentials
    SERVICE_ACCOUNT_INFO = json.loads(os.environ.get("GOOGLE_CREDENTIALS"))
    SHEET_KEY = os.environ.get("SHEET_KEY")
    # Set default local timezone to IST (India Standard Time)
    TIMEZONE_NAME = os.environ.get("TIMEZONE", "Asia/Kolkata") 
    PORT = int(os.environ.get("PORT", 8080))
    # Cache and Users
    cache_str = os.environ.get("ROBLOX_CACHE", '{}')
    ROBLOX_CACHE = json.loads(cache_str) if cache_str else {}
except Exception as e:
    logging.error(f"Configuration Error: Failed to load environment variables or JSON. {e}")
    SERVICE_ACCOUNT_INFO = {}
    SHEET_KEY = "dummy_key"
    TIMEZONE_NAME = "UTC"
    PORT = 8080
    ROBLOX_CACHE = {}

# --- Firebase Setup ---
try:
    # Use ApplicationDefault credentials for Render if available, otherwise assume no persistence
    firebase_app = initialize_app(credentials.ApplicationDefault())
    db = firestore.client()
except Exception as e:
    logging.warning(f"Firebase initialization failed: {e}. Firestore access will be disabled.")
    db = None 

# --- Global State and Cache ---
# Simple in-memory cache for tracking session state
user_session_cache = ROBLOX_CACHE
app = Flask(__name__)

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

# User IDs to track (Replace with your actual list of Roblox user IDs)
TARGET_USER_IDS = [
    1992158202, # Rushabh
    5120230728, # jsadujgha
    4491738101, # Saish
    3263707365, # Saumya
    3206102104, # Shirsh
    8086548901, # Nikunj
    6057670047, # Rivan
]

# --- Google Sheets Functions ---

def get_session_duration(start_time_utc):
    """Calculates the duration in minutes from a UTC start time to the current time."""
    try:
        # Ensure the start time string is correctly parsed as UTC
        start_dt = datetime.fromisoformat(start_time_utc.replace(" UTC", "+00:00")).astimezone(timezone.utc)
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
        credentials = gspread.service_account_from_dict(SERVICE_ACCOUNT_INFO)
        client = gspread.authorize(credentials)

        # Open the spreadsheet by key
        spreadsheet = client.open_by_key(SHEET_KEY)
        worksheet = spreadsheet.worksheet("Raw_Data") 

        # Find the row by Session ID (assuming Session ID is in column A)
        session_id = session_data['Session_ID']
        cell = worksheet.find(session_id, in_column=1)

        # Prepare end time and duration data
        current_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        
        # Calculate local end time using the configured TIMEZONE_NAME
        # We rely on the system/environment configuration to interpret datetime.now() 
        # based on the TIMEZONE environment variable set in Render.
        current_local = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        duration = get_session_duration(session_data['Start_Time_UTC'])
        
        # Data to update: End Time (G), Duration (H), End Time Local (J)
        end_time_utc_col = 7
        duration_col = 8
        end_time_local_col = 10
        
        # Update cells
        worksheet.update_cell(cell.row, end_time_utc_col, current_utc)
        worksheet.update_cell(cell.row, duration_col, duration)
        worksheet.update_cell(cell.row, end_time_local_col, current_local)
        
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
        credentials = gspread.service_account_from_dict(SERVICE_ACCOUNT_INFO)
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
            '', # End Time UTC (G) - Left blank until session ends
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

async def check_user_presence(client, user_id):
    """Fetches presence data for a single Roblox user ID using the shared client."""
    url = "https://presence.roblox.com/v1/presence/users"
    headers = {'Content-Type': 'application/json'}
    payload = {"userIds": [user_id]}

    try:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status() 
        data = response.json()

        presence = data['userPresences'][0]
        
        is_playing = presence['userPresenceType'] == 2 # 2 = InGame
        game_id = presence.get('universeId', 0) if is_playing else 0
        game_name = presence.get('lastLocation', 'Website / Offline')
        
        if game_name == 'Website / Offline':
            is_playing = False 
            
        # *** NEW ROBUSTNESS CHECK ***
        # If the user is marked 'InGame' but the game_id is 0/null (due to privacy settings or old game), 
        # we treat them as 'not playing' for tracking purposes, as we can't log the session without an ID.
        if is_playing and game_id == 0:
            logging.warning(f"User {user_id} ({presence.get('username', 'Unknown')}) is 'InGame' but game_id is 0/null. Treating as Offline for tracking.")
            is_playing = False
        # ***************************

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
    
    global user_session_cache
    
    current_time_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    # datetime.now() will use the server's time, which should be configured to IST 
    # via the TIMEZONE_NAME (Asia/Kolkata) env variable set by the platform.
    current_time_local = datetime.now().strftime("%Y-%m-%d %H:%M:%S") 
    
    # 1. Fetch data for all users concurrently
    # Initialize results to an empty list to prevent the 'NoneType' error during iteration
    results = [] 
    try:
        # This structure guarantees the tasks are created and awaited correctly within the client's lifecycle.
        async with httpx.AsyncClient(timeout=20) as client:
            # Create a list of awaitable tasks
            tasks = [check_user_presence(client, uid) for uid in TARGET_USER_IDS]
            
            # Run all tasks concurrently and wait for all results
            results = await asyncio.gather(*tasks) 
    except Exception as e:
        # If the entire fetch block fails (e.g., network issue), results remains []
        logging.error(f"Async data fetch failed entirely: {e}")
        
    # --- DEFENSIVE CHECK ---
    # Although results is initialized to [], this check provides extra safety
    if results is None: 
        logging.error("Asyncio.gather returned None. Using empty list.")
        results = []
    # -----------------------

    # 2. Process results
    for result in results: 
        if not result:
            continue
            
        user_id = result['Roblox_ID']
        is_playing_now = result['is_playing']
        cache_key = str(user_id)
        
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
            session_to_end = user_session_cache.get(cache_key, {})
            
            if not session_to_end.get('Session_ID'):
                logging.warning(f"Attempted to end session for {user_name} but Session_ID was missing from cache.")
            else:
                logging.debug(f"END Session: {user_name} left {session_to_end.get('Game', 'an unknown game')}.")

                # Update Google Sheet with end time and duration
                update_google_sheet(user_id, session_to_end)

            # Clear cache entry for this session
            user_session_cache[cache_key] = {'Playing': False, 'Game': 'Website / Offline'}
            
        elif is_playing_now and was_playing:
            # CONTINUE Session: Still playing 
            session_id = user_session_cache[cache_key].get('Session_ID', 'N/A')
            logging.debug(f"CONTINUE Session: {user_name} in {user_session_cache[cache_key]['Game']}: ID: {session_id}")

        else:
            # IDLE: Still offline/on website
            logging.debug(f"IDLE: {user_name} is offline/on website.")
            user_session_cache[cache_key] = {'Playing': False, 'Game': 'Website / Offline'}
            
        logging.debug(f"Cache Updated: {user_name} -> Playing: {user_session_cache[cache_key]['Playing']}, Game: {user_session_cache[cache_key]['Game']}")

    # 3. Update the persistent cache (Firestore)
    if db:
        try:
            cache_ref = db.collection('app_cache').document('roblox_tracker')
            # Convert user IDs in cache keys back to strings if necessary for JSON storage
            storable_cache = {str(k): v for k, v in user_session_cache.items()}
            cache_ref.set({'cache_data': storable_cache})
            logging.info("Successfully saved session cache to Firestore.")
        except Exception as e:
            logging.error(f"Failed to save session cache to Firestore: {e}")

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
        # This is where the whole async process starts.
        asyncio.run(run_presence_check())
        
        return jsonify({"status": "success", "message": "Tracking completed."}), 200
    except Exception as e:
        # Log the full traceback for better debugging
        logging.error(f"Tracking run failed: {e}\n{traceback.format_exc()}")
        return jsonify({"status": "error", "message": f"Tracking failed: {e}"}), 500

@app.route('/')
def index():
    """Simple status page."""
    return f"Roblox Tracker Project is running. Trigger tracking via the /track endpoint. Current Timezone: {TIMEZONE_NAME}"

# --- Entry Point ---

if __name__ == '__main__':
    logging.info(f"Starting Flask app on port {PORT}...")
    app.run(host='0.0.0.0', port=PORT, debug=False)
else:
    logging.info(f"Flask app initialized for Gunicorn.")
