# -*- coding: utf-8 -*-
import requests
import json
import re

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.atptour.com/",
}

session = requests.Session()

# Try to fetch scores page
try:
    print("Fetching scores page...")
    r = session.get("https://www.atptour.com/en/scores", headers=headers, timeout=20)
    print(f"Status: {r.status_code}")
    if r.status_code == 200:
        # Look for match links
        matches = re.findall(r'href="([^"]*scores/match-centre[^"]*)"', r.text)
        print(f"Found {len(matches)} match links")
        for m in matches[:5]:
            print(m)
        # Also look for JSON data
        if 'pointByPoint' in r.text or 'point-by-point' in r.text:
            print("Has point-by-point references")
    else:
        print(r.text[:200])
except Exception as e:
    print(f"ERR: {e}")
