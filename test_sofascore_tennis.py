# -*- coding: utf-8 -*-
import requests
import json
from datetime import datetime, timedelta
import time

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Referer": "https://www.sofascore.com/",
    "Origin": "https://www.sofascore.com",
    "Connection": "keep-alive",
}

# Try to search for tennis events
def search_tennis(q):
    try:
        url = f"https://api.sofascore.com/api/v1/search/events"
        r = requests.get(url, params={"q": q}, headers=headers, timeout=15)
        print(f"SEARCH {q} -> {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            # print(json.dumps(data, indent=2)[:500])
            return data
        else:
            print(r.text[:200])
    except Exception as e:
        print(f"ERR: {e}")
    return None

# Try to get point by point
def get_pbp(event_id):
    try:
        url = f"https://api.sofascore.com/api/v1/event/{event_id}/point-by-point"
        r = requests.get(url, headers=headers, timeout=15)
        print(f"PBP {event_id} -> {r.status_code}")
        if r.status_code == 200:
            return r.json()
        else:
            print(r.text[:200])
    except Exception as e:
        print(f"ERR: {e}")
    return None

# Try to get timeline
def get_timeline(event_id):
    try:
        url = f"https://api.sofascore.com/api/v1/event/{event_id}/timeline"
        r = requests.get(url, headers=headers, timeout=15)
        print(f"TIMELINE {event_id} -> {r.status_code}")
        if r.status_code == 200:
            return r.json()
        else:
            print(r.text[:200])
    except Exception as e:
        print(f"ERR: {e}")
    return None

if __name__ == "__main__":
    # Search for some tennis players from today
    print("=== TESTING SOFASCORE UNOFFICIAL API ===\n")
    search_tennis("Tommy Paul")
    time.sleep(2)
    search_tennis("Fritz Paul")
    time.sleep(2)
    # Also try general tennis
    search_tennis("tennis")
