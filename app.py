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
ROBLOX_GAME_BULK_URL = "https://games.roblox.com/v1/games/multiget-info"
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


def get_game_names_bulk(universe_ids, retries=2, delay=1):
    """Fetches game names using POST JSON for reliability."""
    if not universe_ids:
        return {}
    for attempt in range(retries):
        try:
            resp = requests.post(
                ROBLOX_GAME_BULK_URL,
                json={"universeIds": universe_ids},
                timeout=5
            )
            resp.raise_for_status()
            data = resp.json()
            return {g['universeId']: g['name'] for g in data if 'universeId' in g and 'name' in g}
        except Exception as e:
            logging.warning(f"Bulk game fetch attempt {attempt+1} failed: {e}")
            if attempt < retries - 1:
                time.sleep(delay)
    return {}


def check_roblox_status(user_ids):
    """Fetches current status from Roblox and determines game names."""
    try:
        resp = requests.post(ROBLOX_STATUS_URL, json={"userIds": list(user_ids)}, timeout=10)
        resp.raise_for_status()
        presence = resp.json().get('userPresences', [])

        universe_ids = {item.get('universeId') for item in presence if item.get('universeId') not in (None, 0)}
        game_name_map = get_game_names_bulk(list(universe_ids))

        status = {}
        for item in presence:
            uid = item['userId']
            is_playing = item['userPresenceType'] in [1, 2, 3]

            universe_id = item.get('universeId')
            root_place_id = item.get('rootPlaceId')
            place_id = item.get('placeId')
            last_location = item.get('lastLocation')

            active_game_id = universe_id or root_place_id or place_id or 0
            display_name = "Offline"

            if is_playing:
                if universe_id in game_name_map:
                    display_name = game_name_map[universe_id]
                elif last_location and last_location.strip() not in ("", "Website", "Unknown", None):
                    display_name = last_location
                else:
                    display_name = "Game ID Hidden"

            status[uid] = {
                "playing": is_playing,
                "game_name": display_name or "Unknown Game",
                "active_game_id": active_game_id
            }
        return status
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

        cached_tracking = c['playing'] and c['game_name'] not in NON_TRACKING_STATES
        current_tracking = u['playing'] and u['game_name'] not in NON_TRACKING_STATES

        logging.info(f"[{name}] Cache: {c['game_name']} | Current: {u['game_name']}")

        if not cached_tracking and current_tracking:
            logs.append([ts_log, name, "STARTED PLAYING", u['game_name'], ""])
            new_cache[uid].update({
                "playing": True,
                "game_name": u['game_name'],
                "active_game_id": u['active_game_id'],
                "start_time_utc": ts_cache
            })

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

        elif cached_tracking and current_tracking and (u['game_name'] != c['game_name'] or u['active_game_id'] != c['active_game_id']):
            logs.append([ts_log, name, "STOPPED PLAYING", c['game_name'], ""])
            logs.append([ts_log, name, "STARTED PLAYING", u['game_name'], ""])
            new_cache[uid].update({
                "game_name": u['game_name'],
                "active_game_id": u['active_game_id'],
                "start_time_utc": ts_cache
            })

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
    return execute_tracking()


@app.route('/status')
def status_route():
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
