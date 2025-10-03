import asyncio
import json
import logging
import time
from typing import Dict, Any, List, Optional, Tuple

# Configuration
LOGGING_INTERVAL_SECONDS = 60  # Check Roblox presence every minute
USER_ID_TO_TRACK = 1992158202  # Hulk's ID
USER_NAME = "hulk" 
ROBLOX_PRESENCE_URL = "https://presence.roblox.com/v1/presence/users"
ROBLOX_GAME_DETAIL_URL = "https://games.roblox.com/v1/games/multiget-place-details"

# --- Firebase Initialization (Required for Canvas) ---
try:
    from firebase_admin import initialize_app, firestore, credentials
    # Use the canvas global configuration variables
    app_id = typeof __app_id !== 'undefined' ? __app_id : 'default-app-id'
    firebase_config = JSON.parse(__firebase_config)
    
    # Initialize Firebase Admin SDK (using a service account is usually better for backend, 
    # but we'll use credentials inferred from the config/environment if available)
    
    # NOTE: Since this Python script is running locally/not in a proper secure environment, 
    # we simulate the database interaction using a dictionary for simplicity and to avoid 
    # exposing credentials in a non-server context. 
    # For a real application, you MUST set up Firebase Admin SDK authentication properly.
    
    IS_FIREBASE_AVAILABLE = False # Set to False for local simulation
except (ImportError, NameError):
    IS_FIREBASE_AVAILABLE = False

# Fallback for local simulation
if not IS_FIREBASE_AVAILABLE:
    print("Firebase Admin SDK not available. Using in-memory store for simulation.")
    user_tracking_cache: Dict[int, Dict[str, Any]] = {}
    db_store: Dict[str, List[Dict[str, Any]]] = {}

# Firestore setup (placeholder functions for local simulation)
async def get_user_tracking_status(user_id: int) -> Optional[Dict[str, Any]]:
    """Simulates fetching the user's last known tracking status from Firestore."""
    if IS_FIREBASE_AVAILABLE:
        # Actual Firestore logic would go here
        pass
    
    # In-memory simulation
    return user_tracking_cache.get(user_id, {
        'user_id': user_id,
        'user_name': USER_NAME,
        'playing': False,
        'active_game_id': 0,
        'game_name': 'N/A',
        'session_start': None,
        'session_id': None
    })

async def update_user_tracking_status(status: Dict[str, Any]):
    """Simulates updating the user's tracking status in Firestore cache."""
    if IS_FIREBASE_AVAILABLE:
        # Actual Firestore logic would go here (e.g., setDoc)
        pass
    
    # In-memory simulation
    user_tracking_cache[status['user_id']] = status
    logging.debug(f"Cache Updated: {status['user_name']} -> Playing: {status['playing']}")

async def log_session_end(session_log: Dict[str, Any]):
    """Simulates logging a completed session to Firestore sessions collection."""
    if IS_FIREBASE_AVAILABLE:
        # Actual Firestore logic would go here (e.g., addDoc)
        pass
    
    # In-memory simulation
    if 'sessions' not in db_store:
        db_store['sessions'] = []
    db_store['sessions'].append(session_log)
    logging.info(f"Session Logged: {session_log['user_name']} played {session_log['game_name']} for {session_log['duration_minutes']:.2f} mins.")

# --- API Handling ---
# We use a simple in-line fetch simulation since external network calls are restricted in this environment.
async def fetch_api_data(url: str, method: str = 'POST', data: Optional[Dict] = None) -> Optional[Dict]:
    """
    Simulates fetching data from the Roblox API endpoints.
    NOTE: In a real environment, this would require `requests` or `httpx` to make network calls.
    Since we cannot make external network calls, this function will return mock data 
    or be executed outside the environment. 
    """
    await asyncio.sleep(0.5)  # Simulate network latency

    # For the purpose of demonstration and debugging the user's logic:
    if url == ROBLOX_PRESENCE_URL:
        # In a real environment, the response for a user with privacy issues might look like this:
        if data and data.get('userIds') == [USER_ID_TO_TRACK]:
            
            # --- DEBUGGING LOG FOR HULK (1992158202) ---
            # If 'hulk' is currently in a game but has privacy settings on, the API returns 'InGame' (type 2) 
            # but the game IDs are NULL.
            raw_data = {
                "userPresences": [
                    {
                        "userPresenceType": 2, # 2 means InGame
                        "lastLocation": "A Private Experience",
                        "placeId": None,
                        "rootPlaceId": None,
                        "gameId": None,
                        "universeId": None,
                        "userId": USER_ID_TO_TRACK,
                        "lastOnline": f"{time.time()}"
                    }
                ]
            }
            logging.warning(f"!!! DEBUG HULK (1992158202) RAW PRESENCE DATA !!!\n{json.dumps(raw_data, indent=2)}")
            # --- END DEBUG LOG ---
            
            return raw_data
            
        return {"userPresences": []}
        
    elif ROBLOX_GAME_DETAIL_URL in url:
        # Mock response for game details (if we ever got a placeId)
        if data and data.get('placeIds'):
            return [
                {"placeId": data['placeIds'][0], "name": f"Unknown Game ({data['placeIds'][0]})"}
            ]

    return None

# --- Main Tracking Logic ---

async def get_game_names_robust(user_presence: Dict[str, Any]) -> Tuple[bool, int, str]:
    """
    Analyzes presence data and attempts to find the game ID and name.
    
    Returns: (is_playing, active_game_id, game_name)
    """
    user_presence_type = user_presence.get("userPresenceType", 0)
    
    # Presence Types: 0=Offline, 1=Online/Website, 2=InGame, 3=InStudio
    is_playing = user_presence_type == 2
    
    active_game_id = user_presence.get("placeId") or user_presence.get("rootPlaceId") or user_presence.get("universeId")
    
    # Convert active_game_id to int, handling None/null values gracefully
    try:
        active_game_id = int(active_game_id)
    except (TypeError, ValueError):
        active_game_id = 0 # If any ID is null, set to 0
    
    game_name = user_presence.get("lastLocation", "N/A")
    
    # If we have a game ID and are playing, try to get the real name
    if is_playing and active_game_id != 0:
        # In a real app, we would make a second API call here to get the proper name
        # For simulation, we assume lastLocation is the best we have unless we 
        # get proper IDs.
        logging.info(f"Game ID found: {active_game_id}. Trying to fetch details...")
        # Since active_game_id is 0 in the case of the user's privacy issue, 
        # this block is what is currently being skipped.
        pass

    return is_playing, active_game_id, game_name

async def fetch_user_presence_data(user_id: int) -> Optional[Dict[str, Any]]:
    """Fetches and processes the latest Roblox presence for a single user."""
    payload = {"userIds": [user_id]}
    
    try:
        response = await fetch_api_data(ROBLOX_PRESENCE_URL, data=payload)
        
        if response and response.get('userPresences'):
            u = response['userPresences'][0]
            
            is_playing, active_game_id, game_name = await get_game_names_robust(u)
            
            return {
                'user_id': user_id,
                'user_name': USER_NAME, # Using hardcoded name for this example
                'playing': is_playing,
                'active_game_id': active_game_id,
                'game_name': game_name
            }
        
    except Exception as e:
        logging.error(f"Error fetching presence for {user_id}: {e}")
        
    return None

async def execute_tracking():
    """Main loop to track user presence and log sessions."""
    logging.info("Tracking loop started...")
    
    # 1. Fetch current and previous state
    user_id = USER_ID_TO_TRACK
    
    current_state = await fetch_user_presence_data(user_id)
    cached_state = await get_user_tracking_status(user_id)
    
    if not current_state:
        # If API call failed, assume no change and retry later
        logging.warning("API call failed to return current state. Skipping tracking logic.")
        return

    # Use clearer variables for readability
    u = current_state
    c = cached_state

    # Define the tracking condition: True if the user is in a game AND we have a valid game ID
    cached_tracking = c['playing'] and c['active_game_id'] != 0
    current_tracking = u['playing'] and u['active_game_id'] != 0
    
    current_time = int(time.time())

    # --- Session Management Logic ---

    # Case 1: START TRACKING (Went from NOT playing to PLAYING with a valid ID)
    if not cached_tracking and current_tracking:
        u['session_start'] = current_time
        u['session_id'] = f"SESS_{user_id}_{current_time}"
        logging.info(f"START Session: {u['user_name']} in game: {u['game_name']} ({u['active_game_id']})")
        await update_user_tracking_status(u)

    # Case 2: CONTINUE TRACKING (Still playing the same game)
    elif cached_tracking and current_tracking and u['active_game_id'] == c['active_game_id']:
        # Update cache to keep session alive, inherit session ID and start time
        u['session_start'] = c['session_start']
        u['session_id'] = c['session_id']
        await update_user_tracking_status(u)
        logging.debug(f"CONTINUE Session: {u['user_name']}")

    # Case 3: END TRACKING (Left the game, or API started reporting invalid data)
    elif cached_tracking and not current_tracking:
        session_duration = current_time - c['session_start']
        session_log = {
            'user_id': c['user_id'],
            'user_name': c['user_name'],
            'game_id': c['active_game_id'],
            'game_name': c['game_name'],
            'start_time': c['session_start'],
            'end_time': current_time,
            'duration_seconds': session_duration,
            'duration_minutes': session_duration / 60.0
        }
        
        logging.info(f"END Session: {u['user_name']} left. Duration: {session_log['duration_minutes']:.2f} mins.")
        await log_session_end(session_log)

        # Clear tracking status in cache
        u['session_start'] = None
        u['session_id'] = None
        await update_user_tracking_status(u)
    
    # Case 4: Not tracked and not playing (Do nothing)
    # Case 5: Switched games (This is covered by a combination of END + START)
    elif cached_tracking and current_tracking and u['active_game_id'] != c['active_game_id']:
         # Simulate End (Case 3 logic)
        session_duration = current_time - c['session_start']
        session_log = {
            'user_id': c['user_id'],
            'user_name': c['user_name'],
            'game_id': c['active_game_id'],
            'game_name': c['game_name'],
            'start_time': c['session_start'],
            'end_time': current_time,
            'duration_seconds': session_duration,
            'duration_minutes': session_duration / 60.0
        }
        logging.info(f"SWITCH Game: {u['user_name']} ended old session. Duration: {session_log['duration_minutes']:.2f} mins.")
        await log_session_end(session_log)
        
        # Simulate Start (Case 1 logic)
        u['session_start'] = current_time
        u['session_id'] = f"SESS_{user_id}_{current_time}"
        logging.info(f"START Session: {u['user_name']} in NEW game: {u['game_name']} ({u['active_game_id']})")
        await update_user_tracking_status(u)
    
    # 2. Check and print sessions in simulation mode
    if not IS_FIREBASE_AVAILABLE and db_store.get('sessions'):
        print("\n--- SIMULATED SESSIONS LOG ---")
        for session in db_store['sessions']:
            print(f"[{session['end_time']}] {session['user_name']} played '{session['game_name']}' ({session['game_id']}) for {session['duration_minutes']:.2f} minutes.")
        print("----------------------------\n")


async def main():
    """Sets up logging and runs the tracking loop periodically."""
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    
    while True:
        await execute_tracking()
        await asyncio.sleep(LOGGING_INTERVAL_SECONDS)

# Run the main async function (Assuming a compatible environment like Python 3.7+)
if __name__ == '__main__':
    try:
        # In this simulated environment, we run the first check immediately
        asyncio.run(execute_tracking()) 
        # And then start the loop
        asyncio.run(main())
    except RuntimeError as e:
        # Handles "RuntimeError: Event loop is closed" on some platforms like Colab
        if "Event loop is closed" in str(e):
            print("Tracking completed (simulated).")
        else:
            raise
    except KeyboardInterrupt:
        print("\nTracker stopped by user.")
