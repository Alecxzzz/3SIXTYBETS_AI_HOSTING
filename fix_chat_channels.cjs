// Agrega los 3 canales nuevos y reordena TODO alfabeticamente por nombre.
const fs = require("fs");
const ruta = "C:/Users/AlecS/OneDrive/Desktop/threesixtybets-chat/src/data/channels.js";
const src = fs.readFileSync(ruta, "utf8");

const nuevos = [
  `  {
    id: 44,
    name: "NYMSPORTS",
    status: "ACTIVO",
    ads: false,
    stream: "https://thm-it-roku.otteravision.com/thm/it/it.m3u8",
    geoRestriction: "NONE",
    useProxy: true
  },`,
  `  {
    id: 45,
    name: "SKY SPORTS NFL",
    status: "ACTIVO",
    ads: false,
    stream: "http://stream.bottledesk.net/p/AQNASgYGemc/index.m3u8",
    geoRestriction: "NONE",
    useProxy: false
  },`,
  `  {
    id: 46,
    name: "TENNIS CHANNEL",
    status: "ACTIVO",
    ads: false,
    stream: "https://cdn-ue1-prod.tsv2.amagi.tv/linear/amg01444-tennischannelth-tennischannelnl-samsungnl/playlist.m3u8",
    geoRestriction: "NONE",
    useProxy: true
  },`,
];

const inicio = src.indexOf("[");
const fin = src.lastIndexOf("];");
const cuerpo = src.slice(inicio + 1, fin);
// Bloques: cada objeto empieza con "  {" y termina con "},"
const bloques = cuerpo
  .split(/\n(?=  \{)/)
  .map((b) => b.trim())
  .filter((b) => b.startsWith("{") || b.startsWith("id:"));
const objs = [];
for (const raw of cuerpo.split(/\n(?=  \{)/)) {
  const limpio = raw.trim();
  if (!limpio.startsWith("{")) continue;
  objs.push(limpio.endsWith(",") ? limpio : limpio + ",");
}
for (const n of nuevos) objs.push(n);
const getName = (b) => (b.match(/name: "([^"]+)"/) || [])[1] || "";
objs.sort((a, b) => getName(a).localeCompare(getName(b), "en"));
const salida = "export const channels = [\n" + objs.join("\n") + "\n];\n";
fs.writeFileSync(ruta, salida);
console.log("Escrito:", ruta, "| canales:", objs.length);
