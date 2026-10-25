import db
from main import obtener_candidatos

# Test the obtener_candidatos function
candidatos = obtener_candidatos()
print(f"Found {len(candidatos)} candidates:")
for url, ref in candidatos[:5]:  # Show first 5
    print(f"  - {url}")
    print(f"    Referer: {ref}")