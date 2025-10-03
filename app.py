from flask import Flask, jsonify
import datetime
import gspread
import os
import requests
import json
import logging
import pytz
import time

# ------------------- Logging -------------------
logging.basicConfig(level=logging.INFO)

# ------------------- App -------------------
app = Flask(__name__)

# ------------------- Config -------------------
GOOGLE_SHEET_NAME = "Minute Tracker Data"
FRIENDS_TO_TRACK = {
    5120230728: "jsadujgha",
    4491738101: "NOTKRZEN",
    3263707365: "Cyrus_STORM",
    1992158202: "hulk_buster9402",
}

ROBLOX_STATUS_URL = "https://presence.roblox.com/v1/presence/users"
DATA_SHEET_NAME = "Activity Log"
CACHE_SHEET_NAME = "Cache"

NON_TRACKING_STATES = ("Offline", "Game ID Hidden", "Website", "Unknown", "ID Lookup Failed")

# ------------------- Helper Functions -------------------
def get_cached_status(worksheet):
    """Reads the last known status from the Cache sheet (Cell A2)."""
    try:
        val = worksheet.acell('A2').value
        if val and val.strip() not in ('{}', ''):
            return json.loads(val)
    except Exception as e:
        logging.error(f"Error loading cache: {e}")
    default_state = {"playing": False, "game_name": "Offline", "start_time_utc": None, "active_game_id": 0}
    return {uid: default_state for uid in FRIENDS_TO_TRACK}

def save_cached_status(worksheet, status_data):
    """Writes the current status to the Cache sheet (Cell A2)."""
    try:
        worksheet.update(range_name='A2', values=[[json.dumps(status_data)]])
    except Exception as e:
        logging.error(f"Error saving cache: {e}")

def get_game_names_robust(presence_list, retries=2, delay=1):
    """
    Robust game name fetch:
    1. Try placeId via multiget-place-details
    2. If missing, try rootPlaceId
    3. If still missing, use universeId via multiget-universe
    """
    place_ids = set()
    root_ids = set()
    universe_ids = set()

    for item in presence_list:
        if item.get('placeId') not in (None, 0):
            place_ids.add(item['placeId'])
        if item.get('rootPlaceId') not in (None, 0):
            root_ids.add(item['rootPlaceId'])
        if item.get('universeId') not in (None, 0):
            universe_ids.add(item['universeId'])

    game_map = {}

    # 1️⃣ Fetch by placeId
    if place_ids:
        url = "https://games.roblox.com/v1/games/multiget-place-details?placeIds=" + ",".join(map(str, place_ids))
        for attempt in range(retries):
            try:
                resp = requests.get(url, timeout=5)
                resp.raise_for_status()
                data = resp.json().get("data", [])
                for game in data:
                    pid = game.get("id")
                    if pid: game_map[pid] = game.get("name", "Unknown Game")
                break
            except Exception as e:
                logging.warning(f"PlaceID fetch attempt {attempt+1} failed: {e}")
                time.sleep(delay)

    # 2️⃣ Fetch by rootPlaceId for missing place IDs
    missing_root_ids = [rid for rid in root_ids if rid not in game_map]
    if missing_root_ids:
        url = "https://games.roblox.com/v1/games/multiget-place-details?placeIds=" + ",".join(map(str, missing_root_ids))
        for attempt in range(retries):
            try:
                resp = requests.get(url, timeout=5)
                resp.raise_for_status()
                data = resp.json().get("data", [])
                for game in data:
                    rid = game.get("id")
                    if rid: game_map[rid] = game.get("name", "Unknown Game")
                break
            except Exception as e:
                logging.warning(f"RootPlaceID fetch attempt {attempt+1} failed: {e}")
                time.sleep(delay)

    # 3️⃣ Fetch by universeId if still missing
    missing_universe_ids = [uid for uid in universe_ids if uid not in game_map]
    if missing_universe_ids:
        url = "https://games.roblox.com/v1/games/multiget?universeIds=" + ",".join(map(str, missing_universe_ids))
        for attempt in range(retries):
            try:
                resp = requests.get(url, timeout=5)
                resp.raise_for_status()
                data = resp.json().get("data", [])
                for game in data:
                    uid = game.get("universeId")
                    if uid: game_map[uid] = game.get("name", "Unknown Game")
                break
            except Exception as e:
                logging.warning(f"UniverseID fetch attempt {attempt+1} failed: {e}")
                time.sleep(delay)

    # Map presence items to final game name
    status_map = {}
    for item in presence_list:
        uid = item['userId']
        name = "Game ID Hidden"
        pid = item.get('placeId')
        rid = item.get('rootPlaceId')
        uid_id = item.get('universeId')

        if pid in game_map:
            name = game_map[pid]
        elif rid in game_map:
            name = game_map[rid]
        elif uid_id in game_map:
            name = game_map[uid_id]
        elif item.get('lastLocation') not in (None, "", "Website", "Unknown"):
            name = item['lastLocation']

        status_map[uid] = {
            "playing": item.get('userPresenceType') in [1,2,3],
            "game_name": name,
            "active_game_id": uid_id or rid or pid or 0
        }

    logging.info(f"Final status map: {status_map}")
    return status_map

def check_roblox_status(user_ids):
    """Fetches current status from Roblox and determines game names using robust lookup."""
    try:
        resp = requests.post(ROBLOX_STATUS_URL, json={"userIds": list(user_ids)}, timeout=10)
        resp.raise_for_status()
        presence = resp.json().get('userPresences', [])
        return get_game_names_robust(presence)
    except Exception as e:
        logging.error(f"Roblox API check failed: {e}")
        return {}

# ------------------- Main Tracking -------------------
def execute_tracking():
    """Fetches status, compares to cache, logs events, and updates cache."""
    try:
        creds_json = os.environ.get('GOOGLE_CREDENTIALS')
        if not creds_json:
            return "ERROR: GOOGLE_CREDENTIALS missing."
        gc = gspread.service_account_from_dict(json.loads(creds_json))
        spreadsheet = gc.open(GOOGLE_SHEET_NAME)
        data_ws = spreadsheet.worksheet(DATA_SHEET_NAME)
        cache_ws = spreadsheet.worksheet(CACHE_SHEET_NAME)
    except Exception as e:
        logging.error(f"Google Sheets connection failed: {e}")
        return f"ERROR: Google Sheets connection failed. {e}"

    cached = get_cached_status(cache_ws)
    current = check_roblox_status(FRIENDS_TO_TRACK.keys())
    if not current:
        return "ERROR: Could not fetch Roblox status."

    new_cache = {}
    logs = []

    now_utc = datetime.datetime.now(pytz.utc)
    now_ist = now_utc.astimezone(pytz.timezone('Asia/Kolkata'))
    ts_log = now_ist.strftime("%Y-%m-%d %H:%M:%S")
    ts_cache = now_utc.strftime("%Y-%m-%d %H:%M:%S+00:00")

    for uid, name in FRIENDS_TO_TRACK.items():
        c = cached.get(uid, {"playing": False, "game_name": "Offline", "start_time_utc": None, "active_game_id": 0})
        u = current.get(uid, {"playing": False, "game_name": "Offline", "active_game_id": 0})
        new_cache[uid] = c.copy()

        # Use active_game_id (universeId) for session tracking
        cached_tracking = c['playing'] and c['active_game_id'] not in (0,)
        current_tracking = u['playing'] and u['active_game_id'] not in (0,)

        logging.info(f"[{name}] Cache: {c['game_name']} ({c['active_game_id']}) | Current: {u['game_name']} ({u['active_game_id']})")

        # START: Not tracked -> Tracked
        if not cached_tracking and current_tracking:
            logs.append([ts_log, name, "STARTED PLAYING", u['game_name'], ""])
            new_cache[uid].update({
                "playing": True,
                "game_name": u['game_name'],
                "active_game_id": u['active_game_id'],
                "start_time_utc": ts_cache
            })

        # STOP: Tracked -> Not tracked (or Offline)
        elif cached_tracking and not current_tracking:
            duration = ""
            if c['start_time_utc']:
                try:
                    start_dt = pytz.utc.localize(datetime.datetime.strptime(c['start_time_utc'], "%Y-%m-%d %H:%M:%S+00:00"))
                    duration = round((now_utc - start_dt).total_seconds() / 60, 2)
                except Exception as e:
                    logging.error(f"Duration calc error: {e}")

            logs.append([ts_log, name, "STOPPED PLAYING", c['game_name'], duration])
            new_cache[uid].update({
                "playing": u['playing'],
                "game_name": u['game_name'],
                "active_game_id": u['active_game_id'],
                "start_time_utc": None
            })

        # SWITCH: Tracked -> Tracked, but active_game_id changed
        elif cached_tracking and current_tracking and u['active_game_id'] != c['active_game_id']:
            logs.append([ts_log, name, "STOPPED PLAYING", c['game_name'], ""])
            logs.append([ts_log, name, "STARTED PLAYING", u['game_name'], ""])
            new_cache[uid].update({
                "game_name": u['game_name'],
                "active_game_id": u['active_game_id'],
                "start_time_utc": ts_cache
            })

        # SILENT UPDATE: Continuous session or continuous non-tracking
        else:
            new_cache[uid].update({
                "playing": u['playing'],
                "game_name": u['game_name'],
                "active_game_id": u['active_game_id'],
                "start_time_utc": c['start_time_utc'] if cached_tracking else (ts_cache if current_tracking else None)
            })

    if logs:
        data_ws.append_rows(logs)
    save_cached_status(cache_ws, new_cache)

    return f"SUCCESS: Checked {len(FRIENDS_TO_TRACK)} friends. {len(logs)} new events logged."

# ------------------- Flask Routes -------------------
@app.route('/')
@app.route('/track')
def track_route():
    """Primary endpoint to execute the tracking logic."""
    return execute_tracking()

@app.route('/status')
def status_route():
    """Returns the live cache state as a JSON response for debugging."""
    try:
        creds_json = os.environ.get('GOOGLE_CREDENTIALS')
        if not creds_json:
            return jsonify({"error": "GOOGLE_CREDENTIALS missing."}), 500

        gc = gspread.service_account_from_dict(json.loads(creds_json))
        spreadsheet = gc.open(GOOGLE_SHEET_NAME)
        cache_ws = spreadsheet.worksheet(CACHE_SHEET_NAME)

        cached_data = get_cached_status(cache_ws)
        friendly_output = {FRIENDS_TO_TRACK.get(uid, str(uid)): data for uid, data in cached_data.items()}
        return jsonify(friendly_output)
    except Exception as e:
        logging.error(f"Status route failed: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
