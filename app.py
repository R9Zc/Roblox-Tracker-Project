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

# --- Firebase Initialization (Required for Canvas) ---
try:
    # IMPORTANT: Ensure firebase_admin is installed: pip install firebase-admin
    from firebase_admin import initialize_app, firestore, credentials
    # Replace with actual credential loading if running outside an initialized environment
    # cred = credentials.Certificate("path/to/your/serviceAccountKey.json")
    # app = initialize_app(cred)
    # db = firestore.client()
    IS_FIREBASE_AVAILABLE = False # Set to True if app initialization is successful
    db = None
except (ImportError, NameError):
    IS_FIREBASE_AVAILABLE = False
    print("Firebase Admin SDK not available. Using in-memory store for simulation.")

# --- API Simulation ---
async def fetch_api_data(url: str, method: str = 'POST', data: Optional[Dict] = None) -> Optional[Dict]:
    """Simulates API fetch with a realistic delay and data structure."""
    await asyncio.sleep(0.5)

    if url == ROBLOX_PRESENCE_URL:
        if data and data.get('userIds') == [USER_ID_TO_TRACK]:
            # Simulate being in a private or unknown experience (common case)
            raw_data = {
                "userPresences": [
                    {
                        "userPresenceType": 2, # 2 means InGame
                        "lastLocation": "A Private Experience",
                        "placeId": 0, # Often 0 or None for private
                        "rootPlaceId": None,
                        "universeId": 123456, # Example Universe ID (can be used as fallback)
                        "userId": USER_ID_TO_TRACK,
                        "lastOnline": f"{time.time()}"
                    }
                ]
            }
            logging.debug(f"Simulated Presence Data:\n{json.dumps(raw_data, indent=2)}")
            return raw_data
        return {"userPresences": []}
    
    # Simulating the game detail lookup (if needed, though not strictly used in current logic)
    elif ROBLOX_GAME_DETAIL_URL in url:
        if data and data.get('placeIds'):
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
        
        # In-memory cache for local simulation/initial state
        self._user_tracking_cache: Dict[str, Any] = {}
        self._db_store: Dict[str, list] = {'sessions': []} # For local session logging simulation

    # --- Persistence Layer ---

    async def get_user_tracking_status(self) -> Dict[str, Any]:
        """Retrieves the current tracking status, either from Firestore or cache."""
        user_id_str = str(self.user_id)
        
        if IS_FIREBASE_AVAILABLE and self.db:
            # Firestore implementation: Get the single tracking document
            # NOTE: For Canvas, the path should be: 
            # /artifacts/{appId}/public/data/roblox_tracker/tracking_status_{user_id}
            try:
                doc_ref = self.db.collection('roblox_tracker').document(user_id_str)
                doc = await doc_ref.get()
                if doc.exists:
                    return doc.to_dict()
            except Exception as e:
                logging.error(f"Firestore read error: {e}")
        
        # Fallback to in-memory cache or default state
        return self._user_tracking_cache.get(user_id_str, {
            'user_id': self.user_id,
            'user_name': self.user_name,
            'playing': False,
            'active_game_id': 0,
            'game_name': 'N/A',
            'session_start': None,
            'session_id': None
        })

    async def update_user_tracking_status(self, status: Dict[str, Any]):
        """Updates the current tracking status in Firestore or cache."""
        user_id_str = str(self.user_id)

        if IS_FIREBASE_AVAILABLE and self.db:
            try:
                doc_ref = self.db.collection('roblox_tracker').document(user_id_str)
                await doc_ref.set(status) # Overwrite current status
            except Exception as e:
                logging.error(f"Firestore write error (status update): {e}")

        # Update cache for local use/immediate next read
        self._user_tracking_cache[user_id_str] = status
        logging.debug(f"Cache Updated: {self.user_name} -> Playing: {status['playing']}, Game: {status['game_name']}")


    async def log_session_end(self, session_log: Dict[str, Any]):
        """Logs a completed session to Firestore or in-memory store."""
        
        if IS_FIREBASE_AVAILABLE and self.db:
            # Firestore implementation: Add a new session document to the collection
            # NOTE: For Canvas, the path should be: 
            # /artifacts/{appId}/public/data/roblox_sessions
            try:
                col_ref = self.db.collection(self.db_collection)
                await col_ref.add(session_log)
            except Exception as e:
                logging.error(f"Firestore write error (session log): {e}")

        # Local simulation logging
        self._db_store['sessions'].append(session_log)
        logging.info(f"Session Logged: {session_log['user_name']} played {session_log['game_name']} for {session_log['duration_minutes']:.2f} mins.")

    # --- Presence & Game Logic ---

    async def _get_game_details(self, place_id: int) -> Tuple[int, str]:
        """Fetches detailed game info if a placeId is available."""
        game_name = "Unknown Game"
        
        if place_id != 0:
             # In a real scenario, you'd use the Robox Game Detail API here.
             # We use the simulation function for consistency.
            try:
                response = await fetch_api_data(
                    f"{ROBLOX_GAME_DETAIL_URL}?placeIds={place_id}", 
                    method='GET' # Correctly set method for this API endpoint
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

        # Optional: Attempt to fetch a better game name if ID is present and location is generic
        if active_game_id != 0 and game_name in ["A Private Experience", "N/A"]:
            active_game_id, game_name = await self._get_game_details(active_game_id)

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
            logging.info(f"START Session: {u['user_name']} in game: {u['game_name']} ({current_game_id})")
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
            logging.info(f"START Session: {u['user_name']} in NEW game: {u['game_name']} ({current_game_id})")
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
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    
    # Initialize the tracker instance
    tracker = RobloxTracker(
        user_id=USER_ID_TO_TRACK, 
        user_name=USER_NAME, 
        db_client=db
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
        # We only run main() to handle the loop and ensure one tracker check before the loop starts
        asyncio.run(main())
    except RuntimeError as e:
        if "Event loop is closed" in str(e):
            print("Tracking completed (simulated).")
        else:
            raise
    except KeyboardInterrupt:
        print("\nTracker stopped by user.")
