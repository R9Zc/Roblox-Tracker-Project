import asyncio
import json
import logging
import time
import datetime
import os
from typing import Dict, Any, List, Optional, Tuple

# --- Dependency Check & Imports ---
# These checks ensure the app fails gracefully if any required library is missing.
try:
    import pytz
except ImportError:
    logging.critical("CRITICAL: Missing 'pytz' library. Please ensure it is in your requirements.txt.")
    exit(1)
    
try:
    import gspread
except ImportError:
    logging.critical("CRITICAL: Missing 'gspread' library. Please ensure it is in your requirements.txt.")
    exit(1)

try:
    import httpx
except ImportError:
    logging.critical("CRITICAL: Missing 'httpx' library. Please ensure it is in your requirements.txt.")
    exit(1)


# --- Configuration ---
LOGGING_INTERVAL_SECONDS = 60  # Check Roblox presence every minute
LOCAL_TIMEZONE = pytz.timezone('America/New_York') # Set to the timezone you want for timestamps

# Google Sheets Configuration
# FIX: Changed variable name to match the one used in the hosting environment (GOOGLE_CREDENTIALS)
GSPREAD_CREDS_ENV_VAR = "GOOGLE_CREDENTIALS"
SPREADSHEET_KEY_ENV_VAR = "SHEET_KEY" # Also updating this to match the image name for consistency
WORKSHEET_NAME = "Sessions" # The name of the worksheet/tab to write to
SHEETS_SCOPE = ['https://www.googleapis.com/auth/spreadsheets']

# CONSOLIDATED USER LIST
USERS_TO_TRACK = {
    "hulk_buster9402": 1992158202,
    "jsadujgha": 5120230728,
    "NOTKRZEN": 4491738101,
    "Cyrus_STORM": 3263707365,
    "TechnoBladeNeverDies": 3206102104, 
    "Rivan": 6057670047,    
    "Nikunj": 8086548901,  
}

ROBLOX_PRESENCE_URL = "https://presence.roblox.com/v1/presence/users"
ROBLOX_GAME_DETAIL_URL = "https://games.roblox.com/v1/games/multiget-place-details"

# --- Global State & Initialization ---
user_tracking_cache: Dict[int, Dict[str, Any]] = {}
sheets_client: Optional[gspread.Client] = None

# --- GSpread Functions ---

def init_gspread() -> Optional[gspread.Client]:
    """Initializes the GSpread client using environment variables."""
    global sheets_client
    
    # 1. Check for the credentials JSON
    creds_json_str = os.environ.get(GSPREAD_CREDS_ENV_VAR)
    if not creds_json_str:
        logging.critical(f"FATAL: Missing environment variable {GSPREAD_CREDS_ENV_VAR}. Cannot authenticate Sheets.")
        return None
        
    try:
        credentials = json.loads(creds_json_str)
        # 2. Use the correct scope (fixes the 'invalid_scope' issue)
        gc = gspread.service_account_from_dict(
            credentials,
            scopes=SHEETS_SCOPE 
        )
        logging.info("Google Sheets client initialized successfully.")
        sheets_client = gc
        return gc
    except Exception as e:
        logging.critical(f"FATAL: Failed to initialize Google Sheets client: {e}")
        return None

async def log_session_end(session_log: Dict[str, Any]):
    """Logs a completed session to the Google Sheet."""
    if not sheets_client:
        logging.error("Sheets client is not initialized. Cannot log session.")
        return
        
    spreadsheet_key = os.environ.get(SPREADSHEET_KEY_ENV_VAR)
    if not spreadsheet_key:
        logging.error(f"FATAL: Missing environment variable {SPREADSHEET_KEY_ENV_VAR}. Cannot log session.")
        return

    # Convert UTC timestamps to the desired local time format (e.g., Eastern Time)
    start_dt_utc = datetime.datetime.fromtimestamp(session_log['start_time'], tz=pytz.utc)
    end_dt_utc = datetime.datetime.fromtimestamp(session_log['end_time'], tz=pytz.utc)
    
    start_dt_local = start_dt_utc.astimezone(LOCAL_TIMEZONE)
    end_dt_local = end_dt_utc.astimezone(LOCAL_TIMEZONE)

    start_time_str = start_dt_local.strftime('%Y-%m-%d %H:%M:%S %Z')
    end_time_str = end_dt_local.strftime('%Y-%m-%d %H:%M:%S %Z')

    # Data row to be appended to the sheet
    row = [
        session_log['session_id'],
        session_log['user_name'],
        session_log['user_id'],
        session_log['game_name'],
        session_log['game_id'],
        start_time_str,
        end_time_str,
        f"{session_log['duration_minutes']:.2f}" # Format as 2 decimal places
    ]
    
    try:
        # Open the sheet and worksheet
        sh = sheets_client.open_by_key(spreadsheet_key)
        worksheet = sh.worksheet(WORKSHEET_NAME)
        
        # Append the row
        worksheet.append_row(row, value_input_option='USER_ENTERED')
        
        logging.critical(f"Session Logged (Sheet): {session_log['user_name']} played {session_log['game_name']} for {session_log['duration_minutes']:.2f} mins.")

    except gspread.exceptions.WorksheetNotFound:
        logging.critical(f"FATAL: Worksheet '{WORKSHEET_NAME}' not found in the spreadsheet. Check the name.")
    except Exception as e:
        logging.error(f"Error writing to Google Sheet: {e}")
    

# --- State Management ---

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


# --- API Handling ---

async def fetch_api_data(url: str, method: str = 'POST', data: Optional[Dict] = None) -> Optional[Dict]:
    """Handles real API calls using httpx."""
    
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
    if place_id != 0:
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
    """
    Parses the raw presence API response into a simplified tracking state.
    """
    user_presence_type = user_presence.get("userPresenceType", 0)
    
    # userPresenceType: 0=Offline, 1=Online/Website, 2=InGame, 3=InStudio
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
    elif user_presence_type == 3: # In Studio
         game_name = f"Creating in Studio: {game_name}"
    elif active_game_id != 0:
        # If we have a place ID, try to get a more accurate name
        fetched_game_id, fetched_game_name = await _get_game_details(active_game_id)
        if fetched_game_name != "Unknown Game":
            game_name = f"{fetched_game_name} [ID: {active_game_id}]"
    
    # CRITICAL FIX for hidden ID: If playing but ID is 0, explicitly set the name
    elif active_game_id == 0 and is_playing:
         game_name = f"In Game (ID Hidden): {game_name}"

    return is_playing, active_game_id, game_name


async def fetch_all_users_presence_data() -> List[Dict[str, Any]]:
    """Fetches and processes the latest Roblox presence for all users in one batch API call."""
    
    user_data_list: List[Dict[str, Any]] = []
    
    user_ids_to_check = [uid for uid in USERS_TO_TRACK.values() if uid > 0]
    if not user_ids_to_check:
        logging.warning("No valid User IDs to check.")
        return user_data_list
    
    payload = {"userIds": user_ids_to_check} 
    
    response = await fetch_api_data(ROBLOX_PRESENCE_URL, data=payload)
    
    if response and response.get('userPresences'):
        
        user_id_to_name = {uid: name for name, uid in USERS_TO_TRACK.items()}
        
        tasks = []
        for u_presence in response['userPresences']:
            user_id = u_presence.get('userId')
            user_name = user_id_to_name.get(user_id, f"ID_{user_id}")
            
            tasks.append(asyncio.create_task(_process_single_user_presence(user_id, user_name, u_presence)))
            
        processed_results = await asyncio.gather(*tasks)
        
        for result in processed_results:
            if result:
                user_data_list.append(result)
                
    return user_data_list


async def _process_single_user_presence(user_id: int, user_name: str, u_presence: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Helper function to parse and package presence data for a single user."""
    try:
        is_playing, active_game_id, game_name = await _parse_presence(u_presence)
        
        return {
            'user_id': user_id,
            'user_name': user_name,
            'playing': is_playing,
            'active_game_id': active_game_id,
            'game_name': game_name
        }
    except Exception as e:
        logging.error(f"Error processing presence for {user_name} ({user_id}): {e}")
        return None


async def execute_tracking():
    """Main loop to track user presence and log sessions for ALL users."""
    logging.info("Starting batch presence check...")
    
    current_states = await fetch_all_users_presence_data()
    current_time = int(time.time())

    for u in current_states:
        user_id = u['user_id']
        c = await get_user_tracking_status(user_id) # Cached state
        
        # Tracking is based purely on the 'playing' status now (handles hidden game IDs)
        cached_tracking = c['playing']
        current_tracking = u['playing']
        
        
        if not cached_tracking and current_tracking: # START: Was offline, is now playing
            u['session_start'] = current_time
            u['session_id'] = f"SESS_{user_id}_{current_time}"
            logging.critical(f"START Session: {u['user_name']} in game: {u['game_name']}") 
            await update_user_tracking_status(u)
            
        elif cached_tracking and not current_tracking: # END: Was playing, is now offline
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
                # Log to Google Sheets
                await log_session_end(session_log)
                
            # Clear tracking status in cache
            u['session_start'], u['session_id'] = None, None
            await update_user_tracking_status(u)
            
        elif cached_tracking and current_tracking and (u['active_game_id'] != c['active_game_id'] or u['game_name'] != c['game_name']): 
            # SWITCH: Was playing, is in a new game (or name/ID changed)
            
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
                # Log old session to Google Sheets
                await log_session_end(session_log)
            
            # 2. Start new session
            u['session_start'] = current_time
            u['session_id'] = f"SESS_{user_id}_{current_time}"
            logging.critical(f"START Session: {u['user_name']} in NEW game: {u['game_name']}") 
            await update_user_tracking_status(u)
            
        else: # CONTINUE / IDLE: Status hasn't changed
            if current_tracking:
                # Inherit session details if continuing
                u['session_start'], u['session_id'] = c['session_start'], c['session_id']
                logging.debug(f"CONTINUE Session: {u['user_name']} in {u['game_name']}")
            else:
                logging.debug(f"IDLE: {u['user_name']} is offline/on website.")
                
            await update_user_tracking_status(u)


async def main():
    """Sets up logging, initializes Sheets, and runs the tracking loop periodically."""
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    
    if not init_gspread():
        logging.critical("Google Sheets setup failed. Exiting application.")
        return

    # Run the tracking loop
    while True:
        await execute_tracking()
        await asyncio.sleep(LOGGING_INTERVAL_SECONDS)

if __name__ == '__main__':
    # Increase log level for debugging only if needed, setting to INFO for deployment
    if os.environ.get('DEBUG_LOGGING') == 'true':
        logging.getLogger().setLevel(logging.DEBUG)
        
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("\nTracker stopped by user.")
    except Exception as e:
        logging.critical(f"Application failed: {e}")
