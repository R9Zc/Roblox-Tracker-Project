import asyncio
import json
import logging
import time
from typing import Dict, Any, Optional, Tuple, List
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
import os 

# --- Configuration ---
LOGGING_INTERVAL_SECONDS = 60 

# IMPORTANT: Add all the users you want to track here.
# *** CONFIRMED USERS AND IDS ***
USERS_TO_TRACK = {
    "hulk_buster9402": 1992158202,
    "jsadujgha": 5120230728,
    "NOTKRZEN": 4491738101,
    "Cyrus_STORM": 3263707365,
}

ROBLOX_PRESENCE_URL = "https://presence.roblox.com/v1/presence/users"
ROBLOX_GAME_DETAIL_URL = "https://games.roblox.com/v1/games/multiget-place-details"

# --- SIMULATION CONTROL STATE (Now used for all users in simulation) ---
SIMULATION_STATE = "OFFLINE" 

# --- Web Server Configuration for Render Health Check ---
DUMMY_WEB_SERVER_PORT = int(os.environ.get('PORT', 8080))
DUMMY_WEB_SERVER_HOST = '0.0.0.0'

# --- Firebase Initialization (Required for Canvas) ---
db = None 
try:
    from firebase_admin import initialize_app, firestore, credentials
    IS_FIREBASE_AVAILABLE = False 
except (ImportError, NameError):
    IS_FIREBASE_AVAILABLE = False
    print("Firebase Admin SDK not available. Using in-memory store for simulation.")

# --- API Simulation (In-memory store for simulation) ---
if not IS_FIREBASE_AVAILABLE:
    # Cache and Store now hold data for ALL users
    user_tracking_cache: Dict[int, Dict[str, Any]] = {}
    db_store: Dict[str, list] = {'sessions': []}

# --- API Simulation ---
async def fetch_api_data(url: str, method: str = 'POST', data: Optional[Dict] = None) -> Optional[Dict]:
    """Simulates API fetch with a realistic delay and data structure."""
    global SIMULATION_STATE
    await asyncio.sleep(0.5)

    if url == ROBLOX_PRESENCE_URL:
        # For multi-user presence, we still need to iterate through the requested user IDs
        requested_ids = data.get('userIds', [])
        user_presences = []
        
        # Determine the user ID of the main tracker, now 'hulk_buster9402'
        main_tracker_id = USERS_TO_TRACK.get("hulk_buster9402", 0)

        for user_id in requested_ids:
            # Simple simulation: all requested users will mirror the global SIMULATION_STATE
            if SIMULATION_STATE == "PLAYING" and user_id == main_tracker_id:
                presence_type = 2 
                last_location = "A Private Experience"
                place_id = 12345 # Example ID
                universe_id = 123456
            elif SIMULATION_STATE == "PLAYING":
                 # Simulate friends being in a different, unique game
                 presence_type = 2 
                 last_location = "Friend's Unique Experience"
                 place_id = 50000 + user_id # Unique placeholder ID
                 universe_id = 500000 + user_id
            else:
                presence_type = 0 
                last_location = "Website"
                place_id = 0
                universe_id = 0
                
            user_presences.append({
                "userPresenceType": presence_type,
                "lastLocation": last_location,
                "placeId": place_id,
                "rootPlaceId": None,
                "universeId": universe_id,
                "userId": user_id,
                "lastOnline": f"{time.time()}"
            })
            
        return {"userPresences": user_presences}
    
    elif ROBLOX_GAME_DETAIL_URL in url and data and data.get('placeIds'):
        place_id = data['placeIds'][0]
        if place_id != 0:
             return [{"placeId": place_id, "name": f"Mega Awesome Game ({place_id})", "universeId": 123456}]
        else:
             return [{"placeId": 0, "name": "Private Server", "universeId": 0}]
    return None

# --- Tracker Class ---
class RobloxTracker:
    """Encapsulates the logic and state for tracking a single Roblox user."""
    def __init__(self, user_id: int, user_name: str, db_client: Any):
        self.user_id = user_id
        self.user_name = user_name
        self.db = db_client
        self.db_collection = "roblox_sessions"
        
        # Use the global cache/store for all trackers to share
        if not IS_FIREBASE_AVAILABLE:
            self._user_tracking_cache = user_tracking_cache
            self._db_store = db_store
        else:
            self._user_tracking_cache: Dict[int, Any] = {} # This will not be used in non-simulated mode
            self._db_store: Dict[str, list] = {'sessions': []} # This will not be used in non-simulated mode


    async def get_user_tracking_status(self) -> Dict[str, Any]:
        """Retrieves the current tracking status, either from Firestore or cache."""
        # Note: This logic now correctly fetches/initializes status based on the specific user ID
        if not IS_FIREBASE_AVAILABLE:
             return self._user_tracking_cache.get(self.user_id, {
                'user_id': self.user_id, 'user_name': self.user_name, 'playing': False,
                'active_game_id': 0, 'game_name': 'N/A', 'session_start': None, 'session_id': None
            })
        return {
            'user_id': self.user_id, 'user_name': self.user_name, 'playing': False,
            'active_game_id': 0, 'game_name': 'N/A', 'session_start': None, 'session_id': None
        }

    async def update_user_tracking_status(self, status: Dict[str, Any]):
        """Updates the current tracking status in Firestore or cache."""
        if IS_FIREBASE_AVAILABLE and self.db:
            pass
        
        if not IS_FIREBASE_AVAILABLE:
            self._user_tracking_cache[self.user_id] = status
        logging.debug(f"Cache Updated: {self.user_name} -> Playing: {status['playing']}, Game: {status['game_name']}")


    async def log_session_end(self, session_log: Dict[str, Any]):
        """Logs a completed session to Firestore or in-memory store."""
        if IS_FIREBASE_AVAILABLE and self.db:
            pass

        if not IS_FIREBASE_AVAILABLE:
            self._db_store['sessions'].append(session_log)
        logging.critical(f"Session Logged: {session_log['user_name']} played {session_log['game_name']} for {session_log['duration_minutes']:.2f} mins.")

    # --- Presence & Game Logic ---
    async def _get_game_details(self, place_id: int) -> Tuple[int, str]:
        game_name = "Unknown Game"
        if place_id != 0:
            try:
                response = await fetch_api_data(
                    ROBLOX_GAME_DETAIL_URL, method='POST', data={'placeIds': [place_id]}
                )
                if response and response[0] and response[0].get('name'):
                    game_name = response[0]['name']
            except Exception as e:
                logging.warning(f"Could only fetch game name for ID {place_id}. Error: {e}")
        return place_id, game_name

    async def _parse_presence(self, user_presence: Dict[str, Any]) -> Tuple[bool, int, str]:
        user_presence_type = user_presence.get("userPresenceType", 0)
        is_playing = user_presence_type == 2 
        active_game_id = user_presence.get("placeId") or user_presence.get("rootPlaceId") or user_presence.get("universeId")
        try:
            active_game_id = int(active_game_id or 0)
        except (TypeError, ValueError):
            active_game_id = 0
        game_name = user_presence.get("lastLocation", "N/A")

        if not is_playing:
            game_name = "Website / Offline"
        elif active_game_id == 0:
            game_name = user_presence.get("lastLocation", "Unidentifiable Experience")
        else:
            game_name_base = game_name
            if game_name in ["A Private Experience", "N/A", ""]:
                fetched_game_id, fetched_game_name = await self._get_game_details(active_game_id)
                if fetched_game_name not in ["Unknown Game", "Private Server"]:
                    game_name_base = fetched_game_name
            game_name = f"{game_name_base} [ID: {active_game_id}]"

        return is_playing, active_game_id, game_name

    async def fetch_current_presence_data(self) -> Optional[Dict[str, Any]]:
        """Fetches the raw presence data for the tracked user."""
        # The API call now requests data for ALL users in the USERS_TO_TRACK map
        payload = {"userIds": list(USERS_TO_TRACK.values())} 
        
        try:
            response = await fetch_api_data(ROBLOX_PRESENCE_URL, data=payload)
            if response and response.get('userPresences'):
                
                # Filter the list of presences to find the one matching THIS tracker's user_id
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
        except Exception as e:
            logging.error(f"Error fetching presence for {self.user_id}: {e}")
        return None

    # --- Main Tracking Loop Logic ---
    async def execute_tracking(self):
        """The core logic to check status and manage sessions for a SINGLE user."""
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
        
        # Logic for START, END, SWITCH, and CONTINUE/IDLE...
        
        if not cached_tracking and current_tracking: # START
            u['session_start'] = current_time
            u['session_id'] = f"SESS_{self.user_id}_{current_time}"
            logging.critical(f"START Session: {u['user_name']} in game: {u['game_name']}") 
            await self.update_user_tracking_status(u)
            
        elif cached_tracking and not current_tracking: # END
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
            
        elif cached_tracking and current_tracking and current_game_id != cached_game_id: # SWITCH
            if c['session_start'] is None:
                logging.warning("No session_start found for a game switch.")
            else:
                session_duration = current_time - c['session_start']
                session_log = {
                    'user_id': c['user_id'], 'user_name': c['user_name'], 'game_id': cached_game_id,
                    'game_name': c['game_name'], 'start_time': c['session_start'], 'end_time': current_time,
                    'duration_seconds': session_duration, 'duration_minutes': session_duration / 60.0,
                    'session_id': c['session_id']
                }
                logging.critical(f"SWITCH Game: {u['user_name']} ended old session. Duration: {session_log['duration_minutes']:.2f} mins.")
                await self.log_session_end(session_log)
            
            u['session_start'] = current_time
            u['session_id'] = f"SESS_{self.user_id}_{current_time}"
            logging.critical(f"START Session: {u['user_name']} in NEW game: {u['game_name']}") 
            await self.update_user_tracking_status(u)
            
        else: # CONTINUE / IDLE
            if current_tracking:
                u['session_start'], u['session_id'] = c['session_start'], c['session_id']
                logging.debug(f"CONTINUE Session: {u['user_name']} in {u['game_name']}")
            else:
                logging.debug(f"IDLE: {u['user_name']} is offline/on website.")
                
            await self.update_user_tracking_status(u)
        
        if not IS_FIREBASE_AVAILABLE and db_store.get('sessions'):
            # Only print the simulation log once after all trackers run to avoid clutter
            pass 


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
    
    # Create a list of tracker instances, one for each user
    trackers: List[RobloxTracker] = []
    for user_name, user_id in USERS_TO_TRACK.items():
        # Ensure the user ID is a non-zero integer before adding the tracker
        if isinstance(user_id, int) and user_id > 0:
            trackers.append(RobloxTracker(user_id=user_id, user_name=user_name, db_client=db))
        else:
             logging.error(f"Skipping user '{user_name}'. ID is missing or invalid: {user_id}")
    
    if not trackers:
        logging.critical("ERROR: No valid user IDs found in USERS_TO_TRACK. Shutting down loop.")
        return # Exit the loop if no users are being tracked

    # Run the tracking loop
    while True:
        # We run the tracking logic for ALL users concurrently
        await asyncio.gather(*[tracker.execute_tracking() for tracker in trackers])
        
        # After all checks are done, print the aggregated session log (for simulation mode only)
        if not IS_FIREBASE_AVAILABLE and db_store.get('sessions'):
            print("\n--- SIMULATED SESSIONS LOG ---")
            for session in db_store['sessions']:
                print(f"[{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(session['end_time']))}] {session['user_name']} played '{session['game_name']}' ({session['game_id']}) for {session['duration_minutes']:.2f} minutes.")
            print("----------------------------\n")
            
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
    try:
        asyncio.run(main())
    except Exception as e:
        logging.error(f"Application failed to run: {e}")
