import asyncio
import json
import logging
import time
import datetime
import os
import threading
from typing import Dict, Any, List, Optional, Tuple

# --- FLASK SETUP & DEPENDENCY CHECKS ---
# We use Flask to satisfy the hosting environment's port requirement.

try:
    from flask import Flask, jsonify
except ImportError:
    logging.critical("CRITICAL: Missing 'flask' library. Please ensure it is in your requirements.txt.")
    exit(1)

try:
    import httpx
    IS_SIMULATION_MODE = False
except ImportError:
    IS_SIMULATION_MODE = True
    print("WARNING: 'httpx' not found. Running in SIMULATION MODE.")

try:
    import pytz 
except ImportError:
    logging.critical("CRITICAL: Missing 'pytz' library. Please ensure it is in your requirements.txt.")
    exit(1)
    
app = Flask(__name__)


# --- Configuration & State ---
LOGGING_INTERVAL_SECONDS = 60
LOCAL_TIMEZONE = pytz.timezone('America/New_York')

# CONSOLIDATED USER LIST (From your most recent analytics_tracker.py)
USERS_TO_TRACK = {
    "Rushabh": 1992158202,
    "jsadujgha": 5120230728,
    "Saish": 4491738101,
    "Saumya": 3263707365,
    "Shirsh": 3206102104, 
    "Rivan": 6057670047,    
    "Nikunj": 8086548901,     
}

ROBLOX_PRESENCE_URL = "https://presence.roblox.com/v1/presence/users"
ROBLOX_GAME_DETAIL_URL = "https://games.roblox.com/v1/games/multiget-place-details"

# Global State for Hybrid App
user_tracking_cache: Dict[int, Dict[str, Any]] = {}
tracker_running = False 
worker_thread: Optional[threading.Thread] = None

# Google Sheets Globals
gc = None
sessions_worksheet = None

def initialize_gspread():
    """Initializes Google Sheets client using environment variables."""
    global gc, sessions_worksheet
    
    try:
        import gspread
    except ImportError:
        logging.critical("CRITICAL: Missing 'gspread' library. Please ensure it is in your requirements.txt.")
        return False
    
    if IS_SIMULATION_MODE:
        logging.warning("GSpread initialization skipped: Running in SIMULATION MODE.")
        return True # Return True to allow tracking loop to run (but it won't log to sheet)

    try:
        # Load credentials from the environment variable
        creds_json = os.environ.get('GOOGLE_CREDENTIALS')
        if not creds_json:
            logging.critical("FATAL: Missing environment variable GOOGLE_CREDENTIALS. Cannot authenticate Sheets.")
            return False

        sheet_key = os.environ.get('SHEET_KEY')
        if not sheet_key:
            logging.critical("FATAL: Missing environment variable SHEET_KEY. Cannot open Spreadsheet.")
            return False

        # Authenticate using the JSON string
        creds = json.loads(creds_json)
        gc = gspread.service_account_from_dict(creds)
        
        spreadsheet = gc.open_by_key(sheet_key)
        
        try:
            # --- FIX: Changed 'Sessions' to 'Activity Log' as requested ---
            sessions_worksheet = spreadsheet.worksheet('Activity Log')
        except gspread.WorksheetNotFound:
            logging.critical("FATAL: Worksheet named 'Activity Log' not found. Please create it.")
            return False
            
        logging.info("Google Sheets client initialized successfully.")
        return True

    except Exception as e:
        logging.critical(f"Google Sheets setup failed. Error: {e}")
        return False

# --- Tracking Functions (Restructured logic from analytics_tracker.py) ---

async def get_user_tracking_status(user_id: int) -> Optional[Dict[str, Any]]:
    """Fetches the user's last known tracking status from cache."""
    return user_tracking_cache.get(user_id, {
        'user_id': user_id, 
        'user_name': next((name for name, uid in USERS_TO_TRACK.items() if uid == user_id), 'Unknown'),
        'playing': False,
        'active_game_id': 0,
        'game_name': 'N/A',
        'session_start': None,
        'session_id': None
    })

async def update_user_tracking_status(status: Dict[str, Any]):
    """Updates the user's tracking status in cache."""
    user_tracking_cache[status['user_id']] = status
    logging.debug(f"Cache Updated: {status['user_name']} -> Playing: {status['playing']}, Game: {status['game_name']}")

async def log_session_end(session_log: Dict[str, Any]):
    """Logs a completed session to Google Sheets."""
    if sessions_worksheet is None:
        logging.critical("Session data not logged: Google Sheets client is not ready.")
        return
        
    start_time_utc = datetime.datetime.fromtimestamp(session_log['start_time'], tz=pytz.utc)
    end_time_utc = datetime.datetime.fromtimestamp(session_log['end_time'], tz=pytz.utc)
    
    # Convert to target timezone and format
    start_time_local_str = start_time_utc.astimezone(LOCAL_TIMEZONE).strftime('%Y-%m-%d %H:%M:%S %Z')
    end_time_local_str = end_time_utc.astimezone(LOCAL_TIMEZONE).strftime('%Y-%m-%d %H:%M:%S %Z')

    row_data = [
        session_log['session_id'],
        session_log['user_name'],
        session_log['user_id'],
        session_log['game_name'],
        session_log['game_id'],
        start_time_local_str,
        end_time_local_str,
        f"{session_log['duration_minutes']:.2f}"
    ]
    
    try:
        # Append the row to the 'Activity Log' sheet (Updated comment for clarity)
        sessions_worksheet.append_row(row_data)
        logging.critical(f"Session Logged (Sheet): {session_log['user_name']} played {session_log['duration_minutes']:.2f} mins.")
    except Exception as e:
        logging.error(f"Failed to write to Google Sheets: {e}")


async def fetch_api_data(url: str, method: str = 'POST', data: Optional[Dict] = None) -> Optional[Dict]:
    """Handles real API calls using httpx."""
    
    if IS_SIMULATION_MODE:
        # Simplified simulation: just return empty data
        await asyncio.sleep(0.1)
        return {"userPresences": []} 

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            headers = {'Content-Type': 'application/json'}
            if method == 'POST':
                response = await client.post(url, headers=headers, json=data)
            elif method == 'GET':
                 response = await client.get(url, headers=headers, params=data)
            else:
                return None
            
            response.raise_for_status() 
            return response.json()
            
    except httpx.RequestError as e:
        logging.error(f"Request Error on {url}: {e}")
        return None
    except Exception as e:
        logging.error(f"Unexpected error during API call to {url}: {e}")
        return None

async def _get_game_details(place_id: int) -> Tuple[int, str]:
    """Fetches game name for a given Place ID from the Roblox API."""
    game_name = "Unknown Game"
    if place_id != 0 and not IS_SIMULATION_MODE:
        try:
            response = await fetch_api_data(
                ROBLOX_GAME_DETAIL_URL, method='GET', data={'placeIds': [place_id]}
            )
            if response and isinstance(response, list) and response[0] and response[0].get('name'):
                game_name = response[0]['name']
                return place_id, game_name
        except Exception as e:
            logging.warning(f"Could not fetch game name for ID {place_id}. Error: {e}")
            
    return place_id, game_name


async def _parse_presence(user_presence: Dict[str, Any]) -> Tuple[bool, int, str]:
    """Parses the raw presence API response into a simplified tracking state."""
    user_presence_type = user_presence.get("userPresenceType", 0)
    
    is_playing = user_presence_type > 1 
    
    active_game_id = user_presence.get("placeId") or user_presence.get("rootPlaceId") or user_presence.get("universeId")
    try:
        active_game_id = int(active_game_id or 0)
    except (TypeError, ValueError):
        active_game_id = 0
        
    game_name = user_presence.get("lastLocation", "N/A")

    if not is_playing:
        game_name = "Website / Offline"
        active_game_id = 0
    elif user_presence_type == 3:
         game_name = f"Creating in Studio: {game_name}"
    elif active_game_id != 0:
        fetched_game_id, fetched_game_name = await _get_game_details(active_game_id)
        if fetched_game_name != "Unknown Game":
            game_name = f"{fetched_game_name} [ID: {active_game_id}]"
    
    elif active_game_id == 0 and is_playing:
         game_name = f"In Game (ID Hidden): {game_name}"

    return is_playing, active_game_id, game_name


async def fetch_all_users_presence_data() -> List[Dict[str, Any]]:
    """Fetches and processes the latest Roblox presence for all users."""
    user_data_list: List[Dict[str, Any]] = []
    
    user_ids_to_check = [uid for uid in USERS_TO_TRACK.values() if uid > 0]
    if not user_ids_to_check:
        logging.warning("No valid User IDs to check.")
        return user_data_list
    
    payload = {"userIds": user_ids_to_check} 
    response = await fetch_api_data(ROBLOX_PRESENCE_URL, data=payload)
    
    if response and response.get('userPresences'):
        user_id_to_name = {uid: name for name, uid in USERS_TO_TRACK.items()}
        tasks = [asyncio.create_task(_process_single_user_presence(
            u_presence.get('userId'), user_id_to_name.get(u_presence.get('userId')), u_presence))
            for u_presence in response['userPresences'] if u_presence.get('userId')]
            
        processed_results = await asyncio.gather(*tasks)
        user_data_list = [result for result in processed_results if result]
                
    return user_data_list


async def _process_single_user_presence(user_id: int, user_name: str, u_presence: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Helper function to parse and package presence data for a single user."""
    try:
        is_playing, active_game_id, game_name = await _parse_presence(u_presence)
        
        return {
            'user_id': user_id, 'user_name': user_name, 'playing': is_playing,
            'active_game_id': active_game_id, 'game_name': game_name
        }
    except Exception as e:
        logging.error(f"Error processing presence for {user_name} ({user_id}): {e}")
        return None


async def execute_tracking():
    """Main function to track user presence and log sessions."""
    # Only skip logging if GSpread failed initialization AND we are not in simulation mode
    if sessions_worksheet is None and not IS_SIMULATION_MODE:
        logging.critical("Tracking skipped: Google Sheets is not initialized.")
        return

    logging.info("Starting batch presence check...")
    
    current_states = await fetch_all_users_presence_data()
    current_time = int(time.time())

    for u in current_states:
        user_id = u['user_id']
        c = await get_user_tracking_status(user_id) # Cached state
        
        cached_tracking = c['playing']
        current_tracking = u['playing']
        
        # LOGIC FOR START, END, SWITCH, CONTINUE
        
        if not cached_tracking and current_tracking: # START
            u['session_start'] = current_time
            u['session_id'] = f"SESS_{user_id}_{current_time}"
            logging.critical(f"START Session: {u['user_name']} in game: {u['game_name']}") 
            await update_user_tracking_status(u)
            
        elif cached_tracking and not current_tracking: # END
            if c['session_start'] is None:
                logging.warning(f"No session_start found for ending session for {u['user_name']}. Skipping log.")
            else:
                session_duration = current_time - c['session_start']
                session_log = {
                    'user_id': c['user_id'], 'user_name': c['user_name'], 'game_id': c['active_game_id'],
                    'game_name': c['game_name'], 'start_time': c['session_start'], 'end_time': current_time,
                    'duration_seconds': session_duration, 'duration_minutes': session_duration / 60.0,
                    'session_id': c['session_id']
                }
                logging.critical(f"END Session: {u['user_name']} left. Duration: {session_log['duration_minutes']:.2f} mins.")
                await log_session_end(session_log)
                
            u['session_start'], u['session_id'] = None, None
            await update_user_tracking_status(u)
            
        elif cached_tracking and current_tracking and (u['active_game_id'] != c['active_game_id'] or u['game_name'] != c['game_name']): 
            # SWITCH
            if c['session_start'] is None:
                logging.warning(f"No session_start found for game switch for {u['user_name']}. Skipping old session log.")
            else:
                # 1. Log old session end
                session_duration = current_time - c['session_start']
                session_log = {
                    'user_id': c['user_id'], 'user_name': c['user_name'], 'game_id': c['active_game_id'],
                    'game_name': c['game_name'], 'start_time': c['session_start'], 'end_time': current_time,
                    'duration_seconds': session_duration, 'duration_minutes': session_duration / 60.0,
                    'session_id': c['session_id']
                }
                logging.critical(f"SWITCH Game: {u['user_name']} ended old session. Duration: {session_log['duration_minutes']:.2f} mins.")
                await log_session_end(session_log)
            
            # 2. Start new session
            u['session_start'] = current_time
            u['session_id'] = f"SESS_{user_id}_{current_time}"
            logging.critical(f"START Session: {u['user_name']} in NEW game: {u['game_name']}") 
            await update_user_tracking_status(u)
            
        else: # CONTINUE / IDLE
            if current_tracking:
                u['session_start'], u['session_id'] = c['session_start'], c['session_id']
                logging.debug(f"CONTINUE Session: {u['user_name']} in {u['game_name']}")
            else:
                logging.debug(f"IDLE: {u['user_name']} is offline/on website.")
                
            await update_user_tracking_status(u)


# --- Worker Thread Setup (Runs the asyncio loop) ---

def worker_loop():
    """Initializes gspread, sets up logging, and runs the main tracking logic."""
    global tracker_running
    
    # Set to DEBUG to ensure we see maximum output if issues occur
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
    
    # Initialize GSpread before entering the loop
    if not initialize_gspread() and not IS_SIMULATION_MODE:
        logging.critical("Deployment aborted: GSpread failed to initialize. Check environment variables.")
        return # Exit the worker thread if initialization failed.
    
    logging.critical("Background Worker Thread: Starting tracking loop.")
    
    tracker_running = True
    try:
        while tracker_running:
            # Create a new event loop for this thread's async operations
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            loop.run_until_complete(execute_tracking()) 
            loop.close()
            time.sleep(LOGGING_INTERVAL_SECONDS)
            
    except Exception as e:
        logging.critical(f"Background Worker Thread failed: {e}")
        tracker_running = False
    finally:
        logging.info("Background Worker Thread stopped.")


# --- FLASK ROUTES (The Web Server Part) ---

@app.before_request
def start_worker_thread():
    """Starts the background worker thread on the first request."""
    global worker_thread
    # Only start if the thread hasn't been started or has crashed
    if worker_thread is None or not worker_thread.is_alive():
        logging.info("Starting background worker thread...")
        worker_thread = threading.Thread(target=worker_loop, daemon=True)
        worker_thread.start()

@app.route('/', methods=['GET'])
def home():
    """The main endpoint to satisfy the platform's health check on port 8080."""
    status = "RUNNING" if tracker_running and worker_thread and worker_thread.is_alive() else "INITIALIZING"
    return jsonify({
        "status": status,
        "message": "Roblox Presence Tracker is running in a background thread.",
        "worker_thread_status": status,
        "tracking_interval": f"{LOGGING_INTERVAL_SECONDS} seconds",
        "simulation_mode": IS_SIMULATION_MODE,
        "worker_is_alive": worker_thread.is_alive() if worker_thread else False
    }), 200

if __name__ == '__main__':
    # This block is for local testing only. In production, Gunicorn handles this via the Procfile.
    start_worker_thread()
    app.run(host='0.0.0.0', port=os.environ.get('PORT', 8080), debug=True)
