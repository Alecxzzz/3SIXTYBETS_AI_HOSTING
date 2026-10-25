import db
from main import http_requests, HLS_USER_AGENT
import re

events = db.list_events() or []
for ev in events:
    print(f"Name: {ev.get('name')}")
    print(f"Sport: {ev.get('sport')}")
    print(f"Stream: {ev.get('stream')}")
    print(f"Type: {ev.get('type')}")
    print(f"Referer: {ev.get('referer')}")
    
    # Test accessing the stream with the referer
    stream = ev.get('stream') or ''
    referer = ev.get('referer') or ''
    
    if stream.startswith('http') and referer.startswith('http'):
        try:
            print(f"\n--- Testing stream access with referer: {referer} ---")
            resp = http_requests.get(
                stream,
                headers={
                    "User-Agent": HLS_USER_AGENT,
                    "Referer": referer,
                },
                timeout=15,
            )
            print(f"Status: {resp.status_code}")
            print(f"Response length: {len(resp.text)}")
            print(f"Response text: {resp.text[:200]}")
            
            # Try to find playback URL
            m = re.search(r'var\s+playbackURL\s+=\s+"([^"]*)"', resp.text)
            if m:
                print(f"Found playbackURL: {m.group(1)}")
            else:
                print("No playbackURL found in response")
                # Look for other patterns
                m2 = re.search(r'file\s*:\s*"([^"]*)"', resp.text)
                if m2:
                    print(f"Found file: {m2.group(1)}")
                m3 = re.search(r'stream_url\s*=\s*"([^"]*)"', resp.text)
                if m3:
                    print(f"Found stream_url: {m3.group(1)}")
        except Exception as e:
            print(f"Error accessing stream: {e}")
    
    print("---")