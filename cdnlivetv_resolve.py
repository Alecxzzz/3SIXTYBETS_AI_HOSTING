import base64, re, sys, urllib.request, urllib.parse, random

# User-Agents rotativos. cdnlivetv.tv limita reproducciones por IP+UA: usar
# siempre el mismo UA hace que el cap 'provider-cap' salte antes. Rotar entre
# varios (moviles Android, que son los que mas se usan para ver TV) reparte el
# limite y de paso devuelve streams mas estables.
USER_AGENTS = [
    "Mozilla/5.0 (Linux; Android 9; SM-J400M) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/89.0.4389.105 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 10; SM-A415F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/86.0.4240.198 Mobile Safari/537.36 OPR/61.1.3076.56625",
    "Mozilla/5.0 (Linux; Android 9; SCV35) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/89.0.4389.105 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 7.0; SAMSUNG SM-J710MN) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/13.0 Chrome/83.0.4103.106 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; arm; Android 7.0; SM-A510F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/81.0.4044.138 YaBrowser/20.4.4.76.00 SA/1 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 10; Wiko U520AS) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/86.0.4240.185 Mobile Safari/537.36",
]

# UA de escritorio como respaldo (algunos streams solo funcionan en desktop).
UA_DESKTOP = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"

# El que se usa en la proxima peticion. Se rota sola.
_ua_actual = [random.choice(USER_AGENTS)]


def headers_ua(ua=None):
    """Headers con un UA (rotativo por defecto)."""
    ua = ua or _ua_actual[0]
    return {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }


def rotar_ua():
    """Cambia el UA para la siguiente peticion."""
    anterior = _ua_actual[0]
    while _ua_actual[0] == anterior:
        _ua_actual[0] = random.choice(USER_AGENTS)
    return _ua_actual[0]


def get(url, reintentos=3):
    """GET con UA rotativo. Si falla, prueba con otro UA antes de rendirse."""
    ultimo_error = None
    for _ in range(max(1, reintentos)):
        req = urllib.request.Request(url, headers=headers_ua())
        try:
            return urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")
        except Exception as exc:
            ultimo_error = exc
            rotar_ua()
    raise ultimo_error if ultimo_error else RuntimeError("sin respuesta")

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
    """Prueba un m3u8. Usa el UA rotativo (el mismo que resuelve)."""
    try:
        req = urllib.request.Request(url, headers=headers_ua())
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
