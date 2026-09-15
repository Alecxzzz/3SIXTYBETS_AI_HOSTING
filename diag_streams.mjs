// Diagnóstico de streams: verifica si cada URL responde y devuelve un playlist m3u8 válido.
import { readFileSync } from "node:fs";

const src = readFileSync("channels.js", "utf8");
const re = /name:\s*"([^"]+)"[\s\S]*?stream:\s*"([^"]+)"/g;
const items = [];
let m;
while ((m = re.exec(src))) items.push({ name: m[1], url: m[2] });

async function test({ name, url }) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), 12000);
  try {
    const res = await fetch(url, {
      signal: ctrl.signal,
      redirect: "follow",
      headers: { "User-Agent": "VLC/3.0.20 LibVLC/3.0.20", Accept: "*/*" },
    });
    const text = await res.text();
    const isM3u8 = text.includes("#EXTM3U");
    const status = res.ok && isM3u8 ? "OK" : `FALLA (HTTP ${res.status}${isM3u8 ? "" : ", sin #EXTM3U"})`;
    return { name, url, status };
  } catch (e) {
    return { name, url, status: `FALLA (${e.cause?.code || e.name})` };
  } finally {
    clearTimeout(t);
  }
}

const results = await Promise.all(items.map(test));
for (const r of results) console.log(`${r.status.startsWith("OK") ? "✅" : "❌"} | ${r.status} | ${r.name}`);
console.log(`\nTotal: ${results.length} | OK: ${results.filter(r=>r.status==="OK").length} | Falla: ${results.filter(r=>r.status!=="OK").length}`);
