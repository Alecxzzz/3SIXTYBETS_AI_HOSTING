import base64, re, sys, urllib.request, urllib.parse

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120"}

def get(url):
    req = urllib.request.Request(url, headers=UA)
    return urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")

def b64d(s):
    s = s.replace("-", "+").replace("_", "/")
    s += "=" * (-len(s) % 4)
    return base64.b64decode(s).decode("utf-8", "ignore")

def resolve(channel, code="ca", user="cdnlivetv", plan="free"):
    url = ("https://cdnlivetv.tv/api/v1/channels/player/"
           f"?name={urllib.parse.quote(channel)}&code={code}&user={user}&plan={plan}")
    html = get(url)
    m = re.search(r"var\s+(\w+)=((?:\w+\(\w+\)\+)+\w+\(\w+\))\s*;", html)
    if not m:
        blobs = re.findall(r"'([A-Za-z0-9+/=_-]{40,})'", html)
        return None, [b64d(b) for b in blobs]
    expr = m.group(2)
    stream = ""
    for name in re.findall(r"\w+\((\w+)\)", expr):
        vm = re.search(rf"var\s+{name}='([^']+)'", html)
        if vm:
            stream += b64d(vm.group(1))
    return stream, None

def check(url):
    req = urllib.request.Request(url, headers=UA)
    try:
        r = urllib.request.urlopen(req, timeout=15)
        first = r.read(300).decode("utf-8", "ignore")
        return r.status, first.strip().splitlines()[:3]
    except Exception as e:
        return 0, [str(e)]

if __name__ == "__main__":
    channel = sys.argv[1] if len(sys.argv) > 1 else "Sportsnet Ontario"
    code = sys.argv[2] if len(sys.argv) > 2 else "ca"
    stream, parts = resolve(channel, code)
    print(f"Channel : {channel} ({code})")
    if stream:
        print(f"m3u8    : {stream}")
        st, head = check(stream)
        print(f"Status  : {st}")
        for l in head:
            print(f"  {l}")
    else:
        print("No direct URL found; decoded candidates:")
        for p in parts or []:
            print(" ", p)
