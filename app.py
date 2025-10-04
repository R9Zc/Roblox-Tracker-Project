import asyncio
import json
import logging
import time
from typing import Dict, Any, Optional, Tuple

# --- Configuration ---
LOGGING_INTERVAL_SECONDS = 60 # Check Roblox presence every minute
USER_ID_TO_TRACK = 1992158202 # Example ID (Hulk's ID)
USER_NAME = "Hulk_Tracker" # User name for logging
ROBLOX_PRESENCE_URL = "https://presence.roblox.com/v1/presence/users"
ROBLOX_GAME_DETAIL_URL = "https://games.roblox.com/v1/games/multiget-place-details"

# --- SIMULATION CONTROL STATE (NEW) ---
# Set to "OFFLINE" to simulate the user not being in a game.
# Set to "PLAYING" to simulate the user starting a game.
SIMULATION_STATE = "OFFLINE" 

# --- Firebase Initialization (Required for Canvas) ---
db = None # Initialize db globally to prevent NameError if firebase_admin import fails
try:
    # IMPORTANT: Ensure firebase_admin is installed: pip install firebase-admin
    from firebase_admin import initialize_app, firestore, credentials
    # If using Firebase Admin SDK, uncomment the lines below:
    # app = initialize_app(credentials.Certificate("serviceAccountKey.json"))
    # db = firestore.client()
    IS_FIREBASE_AVAILABLE = False # Set to True if app initialization is successful
except (ImportError, NameError):
    IS_FIREBASE_AVAILABLE = False
    print("Firebase Admin SDK not available. Using in-memory store for simulation.")

# --- API Simulation (In-memory store for simulation) ---
if not IS_FIREBASE_AVAILABLE:
    # These global variables are used by the tracker in simulation mode
    user_tracking_cache: Dict[int, Dict[str, Any]] = {}
    db_store: Dict[str, list] = {'sessions': []}

# --- API Simulation ---
async def fetch_api_data(url: str, method: str = 'POST', data: Optional[Dict] = None) -> Optional[Dict]:
    """Simulates API fetch with a realistic delay and data structure."""
    global SIMULATION_STATE # Access the global control variable
    await asyncio.sleep(0.5)

    if url == ROBLOX_PRESENCE_URL:
        if data and data.get('userIds') == [USER_ID_TO_TRACK]:
            
            # --- Dynamically set presence based on SIMULATION_STATE ---
            if SIMULATION_STATE == "PLAYING":
                presence_type = 2 # InGame
                last_location = "A Private Experience"
                place_id = 0
                universe_id = 123456
            else:
                presence_type = 0 # Offline/Website
                last_location = "Website"
                place_id = 0
                universe_id = 0
                
            raw_data = {
                "userPresences": [
                    {
                        "userPresenceType": presence_type,
                        "lastLocation": last_location,
                        "placeId": place_id,
                        "rootPlaceId": None,
                        "universeId": universe_id,
                        "userId": USER_ID_TO_TRACK,
                        "lastOnline": f"{time.time()}"
                    }
                ]
            }
            logging.debug(f"Simulated Presence Data:\n{json.dumps(raw_data, indent=2)}")
            return raw_data
        return {"userPresences": []}
    
    # Simulating the game detail lookup
    elif ROBLOX_GAME_DETAIL_URL in url and data and data.get('placeIds'):
        place_id = data['placeIds'][0]
        if place_id != 0:
             # If placeId is non-zero, return a real game name
             return [{"placeId": place_id, "name": f"Mega Awesome Game ({place_id})", "universeId": 123456}]
        else:
             # If placeId is 0, this lookup is usually futile, return minimal info
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
        
        # In-memory cache pointers for local simulation
        if not IS_FIREBASE_AVAILABLE:
            self._user_tracking_cache = user_tracking_cache
            self._db_store = db_store
        else:
            self._user_tracking_cache: Dict[int, Any] = {}
            self._db_store: Dict[str, list] = {'sessions': []}


    # --- Persistence Layer ---
    # (Simplified for simulation. Real FB code is commented out.)

    async def get_user_tracking_status(self) -> Dict[str, Any]:
        """Retrieves the current tracking status, either from Firestore or cache."""
        
        # Fallback to in-memory cache or default state
        if not IS_FIREBASE_AVAILABLE:
             # Use the global cache in simulation mode
             return self._user_tracking_cache.get(self.user_id, {
                'user_id': self.user_id,
                'user_name': self.user_name,
                'playing': False,
                'active_game_id': 0,
                'game_name': 'N/A',
                'session_start': None,
                'session_id': None
            })

        # Default state if not found in FB and not in simulation mode
        return {
            'user_id': self.user_id,
            'user_name': self.user_name,
            'playing': False,
            'active_game_id': 0,
            'game_name': 'N/A',
            'session_start': None,
            'session_id': None
        }

    async def update_user_tracking_status(self, status: Dict[str, Any]):
        """Updates the current tracking status in Firestore or cache."""
        if IS_FIREBASE_AVAILABLE and self.db:
            # Firestore implementation here...
            pass
        
        # Update cache for local use/immediate next read (only used in simulation mode)
        if not IS_FIREBASE_AVAILABLE:
            self._user_tracking_cache[self.user_id] = status
        logging.debug(f"Cache Updated: {self.user_name} -> Playing: {status['playing']}, Game: {status['game_name']}")


    async def log_session_end(self, session_log: Dict[str, Any]):
        """Logs a completed session to Firestore or in-memory store."""
        
        if IS_FIREBASE_AVAILABLE and self.db:
            # Firestore implementation here...
            pass

        # Local simulation logging
        if not IS_FIREBASE_AVAILABLE:
            self._db_store['sessions'].append(session_log)
        logging.info(f"Session Logged: {session_log['user_name']} played {session_log['game_name']} for {session_log['duration_minutes']:.2f} mins.")

    # --- Presence & Game Logic ---

    async def _get_game_details(self, place_id: int) -> Tuple[int, str]:
        """Fetches detailed game info if a placeId is available."""
        game_name = "Unknown Game"
        
        if place_id != 0:
             # Use the simulation function for consistency.
            try:
                response = await fetch_api_data(
                    ROBLOX_GAME_DETAIL_URL, 
                    method='POST',
                    data={'placeIds': [place_id]}
                )
                if response and response[0] and response[0].get('name'):
                    game_name = response[0]['name']
            except Exception as e:
                logging.warning(f"Could not fetch game name for ID {place_id}. Error: {e}")
        
        return place_id, game_name

    async def _parse_presence(self, user_presence: Dict[str, Any]) -> Tuple[bool, int, str]:
        """Robustly extracts tracking info from a single presence dictionary."""
        user_presence_type = user_presence.get("userPresenceType", 0)
        is_playing = user_presence_type == 2 # 2 means InGame

        # Prioritize placeId, then rootPlaceId, then universeId
        active_game_id = user_presence.get("placeId") or user_presence.get("rootPlaceId") or user_presence.get("universeId")
        try:
            active_game_id = int(active_game_id or 0) # Ensure it's an integer, defaulting to 0
        except (TypeError, ValueError):
            active_game_id = 0

        game_name = user_presence.get("lastLocation", "N/A")

        # Logic to generate the immediate game name for logging
        if not is_playing:
            game_name = "N/A"
        elif active_game_id == 0:
            # Case: Playing, but no identifiable ID (e.g., in Studio, or truly private status)
            game_name = user_presence.get("lastLocation", "Unidentifiable Experience")
        else:
            # Case: Playing and we have an ID (universeId or placeId)
            
            # 1. Attempt to get the actual name if the location is generic
            if game_name in ["A Private Experience", "N/A", ""]:
                # Set it to "Searching Game" initially
                game_name = "Searching Game" 
                
                # Fetch actual details if ID is available
                fetched_game_id, fetched_game_name = await self._get_game_details(active_game_id)

                if fetched_game_name not in ["Unknown Game", "Private Server"]:
                    game_name = fetched_game_name

            # 2. Add the Game ID to the name for clear logging (New Requirement)
            game_name = f"{game_name} [ID: {active_game_id}]"

        # Note: If is_playing is True, active_game_id will be 123456 in the simulation, 
        # leading to "Searching Game [ID: 123456]" (due to the simulation's return value).

        return is_playing, active_game_id, game_name

    async def fetch_current_presence_data(self) -> Optional[Dict[str, Any]]:
        """Fetches the raw presence data for the tracked user."""
        payload = {"userIds": [self.user_id]}
        try:
            response = await fetch_api_data(ROBLOX_PRESENCE_URL, data=payload)
            if response and response.get('userPresences'):
                u = response['userPresences'][0]
                is_playing, active_game_id, game_name = await self._parse_presence(u)
                
                return {
                    'user_id': self.user_id,
                    'user_name': self.user_name,
                    'playing': is_playing,
                    'active_game_id': active_game_id,
                    'game_name': game_name
                }
        except Exception as e:
            logging.error(f"Error fetching presence for {self.user_id}: {e}")
        return None

    # --- Main Tracking Loop Logic ---
    async def execute_tracking(self):
        """The core logic to check status and manage sessions."""
        logging.info(f"Checking presence for {self.user_name} ({self.user_id})...")
        
        current_state = await self.fetch_current_presence_data()
        cached_state = await self.get_user_tracking_status()

        if not current_state:
            logging.warning("API call failed or returned no data. Skipping this interval.")
            return

        u = current_state
        c = cached_state
        
        cached_tracking = c['playing']
        current_tracking = u['playing']
        current_game_id = u['active_game_id']
        cached_game_id = c['active_game_id']
        current_time = int(time.time())

        # 1. START SESSION: Not playing -> Now playing
        if not cached_tracking and current_tracking:
            u['session_start'] = current_time
            u['session_id'] = f"SESS_{self.user_id}_{current_time}"
            logging.info(f"START Session: {u['user_name']} in game: {u['game_name']}") # Use new formatted game_name
            await self.update_user_tracking_status(u)
            
        # 2. END SESSION: Was playing -> Now not playing
        elif cached_tracking and not current_tracking:
            if c['session_start'] is None:
                logging.warning("No session_start found for an ending session. Data inconsistency.")
                u['session_start'] = None
                u['session_id'] = None
                await self.update_user_tracking_status(u)
                return
                
            session_duration = current_time - c['session_start']
            session_log = {
                'user_id': c['user_id'],
                'user_name': c['user_name'],
                'game_id': cached_game_id,
                'game_name': c['game_name'],
                'start_time': c['session_start'],
                'end_time': current_time,
                'duration_seconds': session_duration,
                'duration_minutes': session_duration / 60.0,
                'session_id': c['session_id']
            }
            logging.info(f"END Session: {u['user_name']} left. Duration: {session_log['duration_minutes']:.2f} mins.")
            await self.log_session_end(session_log)
            
            u['session_start'] = None
            u['session_id'] = None
            await self.update_user_tracking_status(u)
            
        # 3. SWITCH GAME: Playing (Game A) -> Playing (Game B)
        elif cached_tracking and current_tracking and current_game_id != cached_game_id:
            # End old session (Game A)
            if c['session_start'] is None:
                logging.warning("No session_start found for a game switch. Data inconsistency.")
            else:
                session_duration = current_time - c['session_start']
                session_log = {
                    'user_id': c['user_id'],
                    'user_name': c['user_name'],
                    'game_id': cached_game_id,
                    'game_name': c['game_name'],
                    'start_time': c['session_start'],
                    'end_time': current_time,
                    'duration_seconds': session_duration,
                    'duration_minutes': session_duration / 60.0,
                    'session_id': c['session_id']
                }
                logging.info(f"SWITCH Game: {u['user_name']} ended old session. Duration: {session_log['duration_minutes']:.2f} mins.")
                await self.log_session_end(session_log)
            
            # Start new session (Game B)
            u['session_start'] = current_time
            u['session_id'] = f"SESS_{self.user_id}_{current_time}"
            logging.info(f"START Session: {u['user_name']} in NEW game: {u['game_name']}") # Use new formatted game_name
            await self.update_user_tracking_status(u)
            
        # 4. CONTINUE SESSION: Playing (Game A) -> Still Playing (Game A) or not playing -> still not playing
        else:
            # If playing, ensure session details (start/id) persist in the cache
            if current_tracking:
                u['session_start'] = c['session_start']
                u['session_id'] = c['session_id']
                logging.debug(f"CONTINUE Session: {u['user_name']}")
            else:
                logging.debug(f"IDLE: {u['user_name']}")
                
            await self.update_user_tracking_status(u)
        
        # Display simulated log if not using Firebase
        if not IS_FIREBASE_AVAILABLE and self._db_store.get('sessions'):
            print("\n--- SIMULATED SESSIONS LOG ---")
            for session in self._db_store['sessions']:
                print(f"[{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(session['end_time']))}] {session['user_name']} played '{session['game_name']}' ({session['game_id']}) for {session['duration_minutes']:.2f} minutes.")
            print("----------------------------\n")


# --- Main Async Runner ---
async def main():
    """Initializes the tracker and runs the continuous loop."""
    # Set logging level to DEBUG to see more details, including the simulation data/flow
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    
    # Initialize the tracker instance
    tracker = RobloxTracker(
        user_id=USER_ID_TO_TRACK, 
        user_name=USER_NAME, 
        db_client=db # Safely passes None if Firebase is not available
    )
    
    # Run the initial check (in case we stop after the first run)
    await tracker.execute_tracking()
    
    # Run the continuous tracking loop
    while True:
        await tracker.execute_tracking()
        logging.debug(f"Sleeping for {LOGGING_INTERVAL_SECONDS} seconds...")
        await asyncio.sleep(LOGGING_INTERVAL_SECONDS)

if __name__ == '__main__':
    try:
        # To test the start session, set SIMULATION_STATE = "PLAYING" here, run, then stop.
        # To test the end session, ensure the cache is set (by running PLAYING first), 
        # then set SIMULATION_STATE = "OFFLINE" and run again.
        asyncio.run(main())
    except RuntimeError as e:
        if "Event loop is closed" in str(e):
            print("Tracking completed (simulated).")
        else:
            raise
    except KeyboardInterrupt:
        print("\nTracker stopped by user.")
