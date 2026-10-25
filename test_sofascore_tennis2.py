# -*- coding: utf-8 -*-
import requests
import json
import time

session = requests.Session()

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Referer": "https://www.sofascore.com/",
    "Origin": "https://www.sofascore.com",
    "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

# First visit the site to get cookies
try:
    print("Visiting main site...")
    r = session.get("https://www.sofascore.com/", headers=headers, timeout=20)
    print(f"Main: {r.status_code}")
    time.sleep(2)
except Exception as e:
    print(f"ERR1: {e}")

# Try search
try:
    url = "https://api.sofascore.com/api/v1/search/events"
    r = session.get(url, params={"q": "Paul"}, headers=headers, timeout=15)
    print(f"SEARCH Paul -> {r.status_code}")
    if r.status_code != 200:
        print(r.text[:150])
    else:
        print("SUCCESS!")
        data = r.json()
        print(json.dumps(data)[:200])
except Exception as e:
    print(f"ERR2: {e}")
