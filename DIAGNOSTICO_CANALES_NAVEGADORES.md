# Diagnóstico de canales por navegador

Fuente: `channels.js` — **59 canales**, 98 fuentes (principal + alternativas). Origen CORS simulado: `https://threesixtybets-chat.vercel.app`.

## Reglas que se aplican

| Códec / condición | Safari | Chrome | Edge | Firefox |
|---|---|---|---|---|
| **H.264** | ✅ | ✅ | ✅ | ✅ |
| **AAC** | ✅ | ✅ | ✅ | ✅ |
| **MP3** | ✅ | ✅ | ✅ | ✅ |
| **HEVC/H.265** | ✅ | ❌ | ❌ | ❌ |
| **AC-3** | ✅ | ❌ | ❌ | ❌ |
| **E-AC-3** | ✅ | ❌ | ❌ | ❌ |
| **MPEG-2** | ❌ | ❌ | ❌ | ❌ |
| **MPEG-1** | ❌ | ❌ | ❌ | ❌ |
| **Necesita CORS** | ❌ no (HLS nativo) | ✅ sí | ✅ sí | ✅ sí |
| **http:// en página https** | ❌ bloqueado | ❌ bloqueado | ❌ bloqueado | ❌ bloqueado |

Notas que importan:

- **Safari** reproduce HLS de forma nativa: no usa XHR, por eso **no necesita CORS** y decodifica HEVC y AC-3.
- **Chrome/Edge/Firefox** en escritorio no tienen HLS nativo: usan hls.js (XHR), así que **necesitan CORS** y solo H.264+AAC de forma fiable. HEVC depende de hardware y sistema; no se asume.
- Si el códec no es universal, la única ruta que funciona en todos es **FFmpeg** (`/live/index.m3u8`).

## Resumen

- ✅ **Directo** (tal cual, sin tocar nada): **14**
- 🔀 **Proxy** (`/hls-proxy`, por CORS o por http): **13**
- 🎛️ **Transcode** (`/live/index.m3u8`, por códec): **11**
- ❌ **Caídos** (ni principal ni alternativas): **21**

## Canal por canal

`ruta única` = la que funciona en los cuatro navegadores.

| Canal | Códec | http | CORS | Safari | Chrome | Edge | Firefox | Ruta única |
|---|---|---|---|---|---|---|---|---|
| **ANALISTAS TV (EN INGLES)** | `H.264` | https | ok | ✅ directo | ✅ directo | ✅ directo | ✅ directo | **directo** |
| **BEIN 1** | `?` | https | no | ❌ caído | ❌ caído | ❌ caído | ❌ caído | **no_soportado** |
| **BEIN SPORTS EXTRA Ñ (español)** | `avc1.640028,mp4a.40.2` | https | ok | ✅ directo | ✅ directo | ✅ directo | ✅ directo | **directo** |
| **BEIN SPORTS USA** | `avc1.640020,mp4a.40.2` | https | ok | ✅ directo | ✅ directo | ✅ directo | ✅ directo | **directo** |
| **BEIN SPORTS XTRA (ingles)** | `avc1.4d4028,mp4a.40.2` | https | no | ✅ directo | 🔀 proxy | 🔀 proxy | 🔀 proxy | **proxy** |
| **CANAL 4 (JUEGOS MLB)** | `AAC` | ⚠️ sí | ok | 🔀 proxy | 🔀 proxy | 🔀 proxy | 🔀 proxy | **proxy** |
| **CANAL AMERICA** | `?` | ⚠️ sí | no | ❌ caído | ❌ caído | ❌ caído | ❌ caído | **no_soportado** |
| **CBS SPORT GOLAZO** | `?` | https | no | ❌ caído | ❌ caído | ❌ caído | ❌ caído | **no_soportado** |
| **CBS UEFA CHAMPIONS LEAGUE** | `?` | https | no | 🎛️ transcode | 🎛️ transcode | 🎛️ transcode | 🎛️ transcode | **transcode** |
| **CLARO SPORTS** | `?` | https | ok | 🎛️ transcode | 🎛️ transcode | 🎛️ transcode | 🎛️ transcode | **transcode** |
| **CLARO SPORTS 2** | `?` | ⚠️ sí | ok | 🎛️ transcode | 🎛️ transcode | 🎛️ transcode | 🎛️ transcode | **transcode** |
| **DAZN1** | `?` | ⚠️ sí | no | ❌ caído | ❌ caído | ❌ caído | ❌ caído | **no_soportado** |
| **DAZN_3** | `avc1.640028,mp4a.40.2` | ⚠️ sí | ok | 🔀 proxy | 🔀 proxy | 🔀 proxy | 🔀 proxy | **proxy** |
| **DAZN_4** | `?` | ⚠️ sí | no | ❌ caído | ❌ caído | ❌ caído | ❌ caído | **no_soportado** |
| **DAZN_5** | `?` | ⚠️ sí | no | ❌ caído | ❌ caído | ❌ caído | ❌ caído | **no_soportado** |
| **DIRECTV SPORTS** | `H.264` | ⚠️ sí | ok | 🔀 proxy | 🔀 proxy | 🔀 proxy | 🔀 proxy | **proxy** |
| **DSPORTS 1** | `H.264` | ⚠️ sí | ok | 🔀 proxy | 🔀 proxy | 🔀 proxy | 🔀 proxy | **proxy** |
| **DSPORTS 2** | `?` | ⚠️ sí | no | ❌ caído | ❌ caído | ❌ caído | ❌ caído | **no_soportado** |
| **ESPN 1** | `mp4a.40.2,avc1.4D6029` | https | ok | ✅ directo | ✅ directo | ✅ directo | ✅ directo | **directo** |
| **ESPN 2** | `mp4a.40.2,avc1.4D6029` | https | ok | ✅ directo | ✅ directo | ✅ directo | ✅ directo | **directo** |
| **ESPN 3** | `mp4a.40.2,avc1.4D6029` | https | ok | ✅ directo | ✅ directo | ✅ directo | ✅ directo | **directo** |
| **ESPN 4** | `mp4a.40.2,avc1.4D6029` | https | ok | ✅ directo | ✅ directo | ✅ directo | ✅ directo | **directo** |
| **ESPN DEPORTES HD** | `avc1.640028,mp4a.40.2` | https | ok | ✅ directo | ✅ directo | ✅ directo | ✅ directo | **directo** |
| **EUROSPORT 1** | `?` | ⚠️ sí | no | ❌ caído | ❌ caído | ❌ caído | ❌ caído | **no_soportado** |
| **EUROSPORT 2** | `H.264` | ⚠️ sí | ok | 🔀 proxy | 🔀 proxy | 🔀 proxy | 🔀 proxy | **proxy** |
| **FOX DEPORTES (ESPAÑOL)** | `avc1.4d401f,mp4a.40.2` | https | ok | ✅ directo | ✅ directo | ✅ directo | ✅ directo | **directo** |
| **FOX DEPORTES 2 ESPAÑOL** | `?` | ⚠️ sí | ok | 🎛️ transcode | 🎛️ transcode | 🎛️ transcode | 🎛️ transcode | **transcode** |
| **FOX SPORTS** | `?` | https | no | 🎛️ transcode | 🎛️ transcode | 🎛️ transcode | 🎛️ transcode | **transcode** |
| **FOX SPORTS 2** | `H.264` | ⚠️ sí | ok | 🔀 proxy | 🔀 proxy | 🔀 proxy | 🔀 proxy | **proxy** |
| **FOX SPORTS CENTROAMERICA** | `?` | ⚠️ sí | no | ❌ caído | ❌ caído | ❌ caído | ❌ caído | **no_soportado** |
| **FS1 (MLB - NFL Y MAS)** | `?` | ⚠️ sí | no | ❌ caído | ❌ caído | ❌ caído | ❌ caído | **no_soportado** |
| **FS2** | `?` | https | no | ❌ caído | ❌ caído | ❌ caído | ❌ caído | **no_soportado** |
| **GOLAZO NETWORK** | `?` | https | no | 🎛️ transcode | 🎛️ transcode | 🎛️ transcode | 🎛️ transcode | **transcode** |
| **LALIGA HYPERMOTION 1** | `?` | https | no | ❌ caído | ❌ caído | ❌ caído | ❌ caído | **no_soportado** |
| **LIGA 1 MAX** | `?` | https | no | ❌ caído | ❌ caído | ❌ caído | ❌ caído | **no_soportado** |
| **MOVISTAR DEPORTES** | `?` | https | no | ❌ caído | ❌ caído | ❌ caído | ❌ caído | **no_soportado** |
| **MOVISTAR LIGA DE DEPORTES** | `?` | https | no | ❌ caído | ❌ caído | ❌ caído | ❌ caído | **no_soportado** |
| **NBA TV** | `avc1.64002a,mp4a.40.2` | ⚠️ sí | ok | 🔀 proxy | 🔀 proxy | 🔀 proxy | 🔀 proxy | **proxy** |
| **NBC SPORTS** | `avc1.64001e,mp4a.40.2` | https | ok | ✅ directo | ✅ directo | ✅ directo | ✅ directo | **directo** |
| **NFL CHANNEL** | `?` | https | no | 🎛️ transcode | 🎛️ transcode | 🎛️ transcode | 🎛️ transcode | **transcode** |
| **NHL NETWORK** | `avc1.640028,mp4a.40.2` | https | no | ✅ directo | 🔀 proxy | 🔀 proxy | 🔀 proxy | **proxy** |
| **NYMSPORTS** | `avc1.64001f,mp4a.40.2` | https | ok | ✅ directo | ✅ directo | ✅ directo | ✅ directo | **directo** |
| **SKY SPORTS** | `H.264` | ⚠️ sí | ok | 🔀 proxy | 🔀 proxy | 🔀 proxy | 🔀 proxy | **proxy** |
| **SKY SPORTS LALIGA** | `?` | ⚠️ sí | no | ❌ caído | ❌ caído | ❌ caído | ❌ caído | **no_soportado** |
| **SKY SPORTS NFL** | `avc1.4d401f,mp4a.40.2` | ⚠️ sí | ok | 🔀 proxy | 🔀 proxy | 🔀 proxy | 🔀 proxy | **proxy** |
| **SKY SPORTS PREMIER LEAGUE** | `avc1.64002a,mp4a.40.2` | ⚠️ sí | ok | 🔀 proxy | 🔀 proxy | 🔀 proxy | 🔀 proxy | **proxy** |
| **SPOR TV 1 BR** | `?` | ⚠️ sí | ok | 🎛️ transcode | 🎛️ transcode | 🎛️ transcode | 🎛️ transcode | **transcode** |
| **SPOR TV 3 BR** | `?` | ⚠️ sí | ok | 🎛️ transcode | 🎛️ transcode | 🎛️ transcode | 🎛️ transcode | **transcode** |
| **SPORTV 2 BR** | `?` | ⚠️ sí | ok | 🎛️ transcode | 🎛️ transcode | 🎛️ transcode | 🎛️ transcode | **transcode** |
| **STAR SPORTS 1** | `?` | ⚠️ sí | no | ❌ caído | ❌ caído | ❌ caído | ❌ caído | **no_soportado** |
| **TELEMUNDO DEPORTES** | `H.264` | https | ok | ✅ directo | ✅ directo | ✅ directo | ✅ directo | **directo** |
| **TENNIS CHANNEL** | `avc1.4d4028,mp4a.40.2` | https | ok | ✅ directo | ✅ directo | ✅ directo | ✅ directo | **directo** |
| **TNT SPORTS** | `avc1.64001f,mp4a.40.2` | https | ok | ✅ directo | ✅ directo | ✅ directo | ✅ directo | **directo** |
| **TUDN** | `H.264` | ⚠️ sí | ok | 🔀 proxy | 🔀 proxy | 🔀 proxy | 🔀 proxy | **proxy** |
| **TUDN MX** | `?` | ⚠️ sí | ok | 🎛️ transcode | 🎛️ transcode | 🎛️ transcode | 🎛️ transcode | **transcode** |
| **TYC SPORTS** | `?` | ⚠️ sí | no | ❌ caído | ❌ caído | ❌ caído | ❌ caído | **no_soportado** |
| **UNIVISION** | `?` | https | no | ❌ caído | ❌ caído | ❌ caído | ❌ caído | **no_soportado** |
| **USA SPORTS** | `?` | https | no | ❌ caído | ❌ caído | ❌ caído | ❌ caído | **no_soportado** |
| **WIN SPORTS** | `?` | ⚠️ sí | no | ❌ caído | ❌ caído | ❌ caído | ❌ caído | **no_soportado** |

## Qué hacer con cada grupo

**✅ Directo (14)** — se reproducen sin proxy ni FFmpeg (https + CORS + H.264/AAC): ANALISTAS TV (EN INGLES), BEIN SPORTS EXTRA Ñ (español), BEIN SPORTS USA, ESPN 1, ESPN 2, ESPN 3, ESPN 4, ESPN DEPORTES HD, FOX DEPORTES (ESPAÑOL), NBC SPORTS, NYMSPORTS, TELEMUNDO DEPORTES, TENNIS CHANNEL, TNT SPORTS

**🔀 Proxy (13)** — son http:// o no mandan CORS. Con `/hls-proxy` funcionan en los cuatro: BEIN SPORTS XTRA (ingles), CANAL 4 (JUEGOS MLB), DAZN_3, DIRECTV SPORTS, DSPORTS 1, EUROSPORT 2, FOX SPORTS 2, NBA TV, NHL NETWORK, SKY SPORTS, SKY SPORTS NFL, SKY SPORTS PREMIER LEAGUE, TUDN

**🎛️ Transcode (11)** — códec que el navegador no decodifica (HEVC/AC-3/MPEG o desconocido). Necesitan FFmpeg para verse en todos: CBS UEFA CHAMPIONS LEAGUE, CLARO SPORTS, CLARO SPORTS 2, FOX DEPORTES 2 ESPAÑOL, FOX SPORTS, GOLAZO NETWORK, NFL CHANNEL, SPOR TV 1 BR, SPOR TV 3 BR, SPORTV 2 BR, TUDN MX

**❌ Caídos (21)** — no responden ni el principal ni las alternativas: BEIN 1, CANAL AMERICA, CBS SPORT GOLAZO, DAZN1, DAZN_4, DAZN_5, DSPORTS 2, EUROSPORT 1, FOX SPORTS CENTROAMERICA, FS1 (MLB - NFL Y MAS), FS2, LALIGA HYPERMOTION 1, LIGA 1 MAX, MOVISTAR DEPORTES, MOVISTAR LIGA DE DEPORTES, SKY SPORTS LALIGA, STAR SPORTS 1, TYC SPORTS, UNIVISION, USA SPORTS, WIN SPORTS

## Cómo usarlo en el reproductor

```js
const RUTA = { directo: (u) => u,
               proxy: (u) => `/hls-proxy?url=${encodeURIComponent(u)}`,
               transcode: (u) => `/live/index.m3u8?url=${encodeURIComponent(u)}` };
// Safari puede ir directo aunque falte CORS; Chrome/Edge/Firefox no.
const esSafari = /^((?!chrome|android).)*safari/i.test(navigator.userAgent);
const ruta = esSafari && canal.navegadores.Safari !== 'transcode'
           ? canal.navegadores.Safari : canal.navegadores.Chrome;
hls.loadSource(RUTA[ruta](canal.stream));
```

