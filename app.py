import asyncio
import json
import logging
import time
import datetime
import pytz # For timezone handling
from typing import Dict, Any, Optional, Tuple, List
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
import os 
import sys

# Import httpx for real async API requests
try:
    import httpx
    IS_SIMULATION_MODE = False
except ImportError:
    IS_SIMULATION_MODE = True
    print("WARNING: 'httpx' not found. Running in SIMULATION MODE. No real API calls will be made.")

# Import gspread for Google Sheets integration
gspread_sheet = None # Global variable to hold the open worksheet
try:
    # Attempt to import gspread and pytz (as specified in requirements.txt)
    import gspread 
    import pytz 
    IS_GSPREAD_AVAILABLE = True
except ImportError:
    IS_GSPREAD_AVAILABLE = False
    print("WARNING: 'gspread' or 'pytz' not found. Session logging will be in-memory only.")


# --- Configuration ---
LOGGING_INTERVAL_SECONDS = 60 

# CONFIRMED USERS AND IDS (5 ACCOUNTS)
USERS_TO_TRACK = {
    "hulk_buster9402": 1992158202,
    "jsadujgha": 5120230728,
    "NOTKRZEN": 4491738101,
    "Cyrus_STORM": 3263707365,
    "TechnoBladeNeverDies": 3206102104, 
}

ROBLOX_PRESENCE_URL = "https://presence.roblox.com/v1/presence/users"
ROBLOX_GAME_DETAIL_URL = "https://games.roblox.com/v1/games/multiget-place-details"

# --- Web Server Configuration for Render Health Check ---
DUMMY_WEB_SERVER_PORT = int(os.environ.get('PORT', 8080))
DUMMY_WEB_SERVER_HOST = '0.0.0.0'

# --- Cache and Store ---
user_tracking_cache: Dict[int, Dict[str, Any]] = {}
db_store: Dict[str, list] = {'sessions': []} # In-memory store for fallback/console logging

# --- Google Sheets Integration ---

def init_gspread():
    """Initializes and returns the Google Sheet Worksheet object."""
    global gspread_sheet
    
    if not IS_GSPREAD_AVAILABLE:
        logging.warning("Gspread is not available. Skipping Sheet initialization.")
        return None

    try:
        # Get necessary credentials from environment variables
        creds_json = os.environ.get("GOOGLE_CREDENTIALS") 
        sheet_key = os.environ.get("SHEET_KEY")
        
        if not creds_json or not sheet_key:
            logging.error("Missing GOOGLE_CREDENTIALS or SHEET_KEY environment variables. Sheet logging disabled.")
            return None

        # Authenticate using the service account JSON
        credentials = json.loads(creds_json)
        gc = gspread.service_account_from_dict(credentials)
        
        # Open the sheet by key/ID
        spreadsheet = gc.open_by_key(sheet_key)
        
        # Select the first worksheet (usually "Sheet1").
        # Note: If your log previously said "Activity Log", this means the first tab is named that.
        worksheet = spreadsheet.sheet1 
        
        # Define the expected header row
        expected_header = [
            "Session ID", "User Name", "Roblox ID", "Game Name", "Game ID",
            "Start Time (UTC)", "End Time (UTC)", "Duration (Minutes)", 
            "Start Time (Local)", "End Time (Local)"
        ]
        
        # Check if header exists and update if necessary
        # Note: This is a synchronous gspread call, fine for initialization
        try:
            current_header = worksheet.row_values(1)
            if current_header != expected_header:
                logging.info(f"Setting up Sheet header row for '{worksheet.title}'...")
                worksheet.update([expected_header])
            else:
                logging.info("Sheet header is correctly set.")
        except Exception as e:
            logging.error(f"Error checking/updating sheet header: {e}")
            
        # Updated log to confirm the worksheet title is active
        logging.critical(f"Successfully connected to Google Sheet: {spreadsheet.title} (Worksheet: {worksheet.title})")
        gspread_sheet = worksheet # Store globally
        return worksheet

    except Exception as e:
        # CRITICAL: This will log authentication or sheet access failure.
        logging.error(f"FATAL ERROR: Failed to initialize Google Sheet or authenticate service account: {e}")
        return None


# --- API Call Implementation (Real or Simulated) ---
async def fetch_api_data(url: str, method: str = 'POST', data: Optional[Dict] = None) -> Optional[Dict]:
    """Handles both real and simulated API calls."""
    
    if IS_SIMULATION_MODE:
        # --- SIMULATION LOGIC ---
        await asyncio.sleep(0.5)
        # ... (omitted simulation logic for brevity)
        return None
        # --- END SIMULATION LOGIC ---

    # --- REAL API LOGIC ---
    try:
        async with httpx.AsyncClient() as client:
            headers = {'Content-Type': 'application/json'}
            if method == 'POST':
                response = await client.post(url, headers=headers, json=data, timeout=10.0)
            elif method == 'GET':
                 response = await client.get(url, headers=headers, timeout=10.0)
            else:
                logging.error(f"Unsupported HTTP method: {method}")
                return None
            
            response.raise_for_status() 
            return response.json()
            
    except httpx.HTTPStatusError as e:
        logging.error(f"HTTP Error {e.response.status_code} on {url}: {e}")
    except httpx.RequestError as e:
        logging.error(f"Request Error on {url}: {e}")
    except Exception as e:
        logging.error(f"Unexpected error during API call to {url}: {e}")
        
    return None

# --- Tracker Class ---
class RobloxTracker:
    """Encapsulates the logic and state for tracking a single Roblox user."""
    def __init__(self, user_id: int, user_name: str, db_client: Any):
        self.user_id = user_id
        self.user_name = user_name
        self.db = db_client # Placeholder (not used for Firestore)
        
        # Use the global cache/store for all trackers to share
        self._user_tracking_cache = user_tracking_cache
        self._db_store = db_store


    async def get_user_tracking_status(self) -> Dict[str, Any]:
        """Retrieves the current tracking status from cache."""
        return self._user_tracking_cache.get(self.user_id, {
            'user_id': self.user_id, 'user_name': self.user_name, 'playing': False,
            'active_game_id': 0, 'game_name': 'N/A', 'session_start': None, 'session_id': None
        })

    async def update_user_tracking_status(self, status: Dict[str, Any]):
        """Updates the current tracking status in cache."""
        self._user_tracking_cache[self.user_id] = status
        logging.debug(f"Cache Updated: {self.user_name} -> Playing: {status['playing']}, Game: {status['game_name']}")


    async def log_session_end(self, session_log: Dict[str, Any]):
        """Logs a completed session to Google Sheet (if available) and in-memory store."""
        
        # Get the configured local timezone, default to UTC if not set
        timezone_str = os.environ.get("TIMEZONE", "UTC") 
        try:
            local_tz = pytz.timezone(timezone_str)
        except (pytz.exceptions.UnknownTimeZoneError, NameError):
            local_tz = pytz.utc
            logging.warning(f"TIMEZONE not properly set or pytz missing. Defaulting to UTC.")
            
        # Convert Unix timestamps to UTC datetime objects
        start_dt_utc = datetime.datetime.fromtimestamp(session_log['start_time'], tz=pytz.utc)
        end_dt_utc = datetime.datetime.fromtimestamp(session_log['end_time'], tz=pytz.utc)

        # Convert to local timezone
        start_dt_local = start_dt_utc.astimezone(local_tz)
        end_dt_local = end_dt_utc.astimezone(local_tz)
        
        # Data row for the Sheet
        row_data = [
            session_log['session_id'],
            session_log['user_name'],
            str(session_log['user_id']), # Ensure IDs are strings for gspread
            session_log['game_name'],
            str(session_log['game_id']), # Ensure IDs are strings for gspread
            start_dt_utc.strftime('%Y-%m-%d %H:%M:%S %Z'),
            end_dt_utc.strftime('%Y-%m-%d %H:%M:%S %Z'),
            f"{session_log['duration_minutes']:.2f}",
            start_dt_local.strftime('%Y-%m-%d %H:%M:%S %Z'),
            end_dt_local.strftime('%Y-%m-%d %H:%M:%S %Z'),
        ]

        if gspread_sheet:
            try:
                # CRITICAL LOGGING ADDED HERE TO CONFIRM WE REACH THE WRITE ATTEMPT
                logging.critical(f"SHEET WRITE ATTEMPT: Log for {session_log['user_name']} (Duration: {session_log['duration_minutes']:.2f} mins). Data: {row_data}")

                # Use asyncio.to_thread to run the synchronous gspread operation without blocking
                await asyncio.to_thread(gspread_sheet.append_row, row_data) 
                logging.critical(f"Session Logged to Sheet SUCCESS: {session_log['user_name']} played {session_log['duration_minutes']:.2f} mins.")
            except Exception as e:
                # --- ENHANCED ERROR LOGGING ---
                logging.error("-" * 50)
                logging.error(f"CRITICAL WRITE FAILURE: Failed to write to Google Sheet for {self.user_name}.")
                logging.error(f"Error Type: {type(e).__name__}")
                logging.error(f"Error Message: {e}")
                logging.error(f"Data Attempted: {row_data}")
                logging.error("-" * 50)
                
        # Log to in-memory store for console output (Always done)
        self._db_store['sessions'].append(session_log)
        logging.critical(f"Session Logged: {session_log['user_name']} played {session_log['game_name']} for {session_log['duration_minutes']:.2f} mins.")

    # --- Presence & Game Logic (omitted for brevity, remains unchanged) ---
    async def _get_game_details(self, place_id: int) -> Tuple[int, str]:
        """Fetches game name for a given Place ID from the Roblox API."""
        # ... (implementation omitted for brevity, remains unchanged)
        game_name = "Unknown Game"
        if place_id != 0:
            try:
                response = await fetch_api_data(
                    ROBLOX_GAME_DETAIL_URL, method='POST', data={'placeIds': [place_id]}
                )
                if response and isinstance(response, list) and response[0] and response[0].get('name'):
                    game_name = response[0]['name']
            except Exception as e:
                logging.warning(f"Could not fetch game name for ID {place_id}. Error: {e}")
        return place_id, game_name


    async def _parse_presence(self, user_presence: Dict[str, Any]) -> Tuple[bool, int, str]:
        """
        Parses the raw presence API response into a simplified tracking state.
        
        Uses type > 1 (InGame, InStudio) to handle hidden/private server status.
        """
        user_presence_type = user_presence.get("userPresenceType", 0)
        
        # userPresenceType: 0=Offline, 1=Online/Website, 2=InGame, 3=InStudio
        # We consider any type > 1 as 'playing' or 'active'.
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
            fetched_game_id, fetched_game_name = await self._get_game_details(active_game_id)
            if fetched_game_name not in ["Unknown Game", "Private Server"]:
                game_name = f"{fetched_game_name} [ID: {active_game_id}]"
        elif active_game_id == 0 and is_playing:
             # Case where they are InGame (type 2) but game ID is masked/0 (likely private server or high privacy)
             game_name = f"In Game (ID Hidden): {game_name}"
        
        return is_playing, active_game_id, game_name

    async def fetch_current_presence_data(self) -> Optional[Dict[str, Any]]:
        """Fetches the current presence data for the tracked user from the API."""
        # ... (implementation omitted for brevity, remains unchanged)
        payload = {"userIds": list(USERS_TO_TRACK.values())} 
        
        response = await fetch_api_data(ROBLOX_PRESENCE_URL, data=payload)
        
        if response and response.get('userPresences'):
            user_presence = next(
                (u for u in response['userPresences'] if u.get('userId') == self.user_id),
                None
            )
            
            if user_presence:
                is_playing, active_game_id, game_name = await self._parse_presence(user_presence)
                return {
                    'user_id': self.user_id, 'user_name': self.user_name, 'playing': is_playing,
                    'active_game_id': active_game_id, 'game_name': game_name
                }
        return None

    # --- Main Tracking Loop Logic (omitted for brevity, remains unchanged) ---
    async def execute_tracking(self):
        """The core logic to check status and manage sessions for a SINGLE user."""
        # ... (implementation omitted for brevity, remains unchanged)
        logging.info(f"Checking presence for {self.user_name} ({self.user_id})...")
        
        current_state = await self.fetch_current_presence_data()
        cached_state = await self.get_user_tracking_status()

        if not current_state:
            logging.warning(f"API call failed for {self.user_name}. Skipping this interval.")
            return

        u = current_state
        c = cached_state
        current_time = int(time.time())
        cached_tracking, current_tracking = c['playing'], u['playing']
        current_game_id, cached_game_id = u['active_game_id'], c['active_game_id']
        
        
        if not cached_tracking and current_tracking: # START: Was offline, is now playing
            u['session_start'] = current_time
            u['session_id'] = f"SESS_{self.user_id}_{current_time}"
            logging.critical(f"START Session: {u['user_name']} in game: {u['game_name']}") 
            await self.update_user_tracking_status(u)
            
        elif cached_tracking and not current_tracking: # END: Was playing, is now offline
            if c['session_start'] is None:
                logging.warning("No session_start found for an ending session.")
            else:
                session_duration = current_time - c['session_start']
                session_log = {
                    'user_id': c['user_id'], 'user_name': c['user_name'], 'game_id': cached_game_id,
                    'game_name': c['game_name'], 'start_time': c['session_start'], 'end_time': current_time,
                    'duration_seconds': session_duration, 'duration_minutes': session_duration / 60.0,
                    'session_id': c['session_id']
                }
                logging.critical(f"END Session: {u['user_name']} left. Duration: {session_log['duration_minutes']:.2f} mins.")
                await self.log_session_end(session_log)
            u['session_start'], u['session_id'] = None, None
            await self.update_user_tracking_status(u)
            
        elif cached_tracking and current_tracking and current_game_id != cached_game_id: # SWITCH: Was playing, is in a new game (or ID changed)
            is_game_id_change_significant = (current_game_id != 0 or cached_game_id != 0)
            
            if is_game_id_change_significant:
                if c['session_start'] is None:
                    logging.warning("No session_start found for a game switch.")
                else:
                    # Log old session end
                    session_duration = current_time - c['session_start']
                    session_log = {
                        'user_id': c['user_id'], 'user_name': c['user_name'], 'game_id': cached_game_id,
                        'game_name': c['game_name'], 'start_time': c['session_start'], 'end_time': current_time,
                        'duration_seconds': session_duration, 'duration_minutes': session_duration / 60.0,
                        'session_id': c['session_id']
                    }
                    logging.critical(f"SWITCH Game: {u['user_name']} ended old session. Duration: {session_log['duration_minutes']:.2f} mins.")
                    await self.log_session_end(session_log)
                
                # Start new session
                u['session_start'] = current_time
                u['session_id'] = f"SESS_{self.user_id}_{current_time}"
                logging.critical(f"START Session: {u['user_name']} in NEW game: {u['game_name']}") 
                await self.update_user_tracking_status(u)
            else:
                 # If both IDs are 0, it's a continue in a hidden/private game
                 u['session_start'], u['session_id'] = c['session_start'], c['session_id']
                 logging.debug(f"CONTINUE Session (Hidden ID): {u['user_name']} in {u['game_name']}")
                 await self.update_user_tracking_status(u)
            
        else: # CONTINUE / IDLE: Status hasn't changed (still playing same game or still offline)
            if current_tracking:
                u['session_start'], u['session_id'] = c['session_start'], c['session_id']
                logging.debug(f"CONTINUE Session: {u['user_name']} in {u['game_name']}")
            else:
                logging.debug(f"IDLE: {u['user_name']} is offline/on website.")
                
            await self.update_user_tracking_status(u)
        
        # Print the aggregated session log (for in-memory store only)
        if not IS_GSPREAD_AVAILABLE and self.user_id == list(USERS_TO_TRACK.values())[0] and db_store.get('sessions'):
            print("\n--- SIMULATED SESSIONS LOG ---")
            for session in db_store['sessions']:
                start_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(session['start_time']))
                end_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(session['end_time']))
                print(f"[{session['user_name']}] Played '{session['game_name']}' from {start_time_str} to {end_time_str} ({session['duration_minutes']:.2f} mins).")
            print("----------------------------\n")


# --- Dummy Web Server (For Render Health Check) ---

class HealthCheckHandler(BaseHTTPRequestHandler):
    """A minimal handler that just confirms the service is running."""
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Tracker worker is running and port is open.")

def run_dummy_webserver(host=DUMMY_WEB_SERVER_HOST, port=DUMMY_WEB_SERVER_PORT):
    """Starts the dummy web server in a separate thread."""
    server_address = (host, port)
    
    print(f"--- RENDER SERVICE STARTUP: Attempting to open port {port} on {host} ---")
    
    try:
        httpd = HTTPServer(server_address, HealthCheckHandler)
        logging.critical(f"SUCCESS: Dummy web server is now serving on http://{host}:{port}")
        httpd.serve_forever()
    except Exception as e:
        logging.error(f"FATAL ERROR: Failed to start dummy web server on port {port}. Error: {e}")


# --- Main Async Runner ---
async def main_tracker_loop():
    """Initializes the tracker for each user and runs the continuous loop."""
    
    # NEW STEP: Initialize GSpread before starting the loop
    if IS_GSPREAD_AVAILABLE:
        init_gspread()
        
    # Create a list of tracker instances, one for each user
    trackers: List[RobloxTracker] = []
    # ... (omitted user list logic for brevity)
    for user_name, user_id in USERS_TO_TRACK.items():
        if isinstance(user_id, int) and user_id > 0:
            trackers.append(RobloxTracker(user_id=user_id, user_name=user_name, db_client=None))
        else:
             logging.error(f"Skipping user '{user_name}'. ID is missing or invalid: {user_id}")
    
    if not trackers:
        logging.critical("ERROR: No valid user IDs found in USERS_TO_TRACK. Shutting down loop.")
        return 

    # Run the tracking loop
    while True:
        await asyncio.gather(*[tracker.execute_tracking() for tracker in trackers])
        await asyncio.sleep(LOGGING_INTERVAL_SECONDS)

async def main():
    """Sets up logging and runs the tracker and dummy web server concurrently."""
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
    
    # 1. Start the dummy web server
    server_thread = threading.Thread(target=run_dummy_webserver, daemon=True)
    server_thread.start()
    
    # 2. Run the main tracking loop
    await main_tracker_loop()


if __name__ == '__main__':
    # ... (omitted simulation mode warning for brevity)
    if IS_SIMULATION_MODE:
        print("\n=======================================================")
        print("!!! WARNING: httpx is missing. Tracker will not work. !!!")
        print("!!! Please ensure 'httpx' is in your requirements.txt. !!!")
        print("=======================================================\n")
        
    try:
        asyncio.run(main())
    except Exception as e:
        logging.error(f"Application failed to run: {e}")
