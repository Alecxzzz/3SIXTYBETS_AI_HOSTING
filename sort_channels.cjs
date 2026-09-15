// Reordena el array de channels.js alfabéticamente por nombre (orden natural EN).
const fs = require("fs");
const src = fs.readFileSync("channels.js", "utf8");
const start = src.indexOf("[");
const end = src.lastIndexOf("];");
const body = src.slice(start + 1, end);
const lines = body.split(/\r?\n/).filter((l) => l.trim());
const getName = (l) => l.match(/name: "([^"]+)"/)[1];
lines.sort((a, b) => getName(a).localeCompare(getName(b), "en"));
fs.writeFileSync("channels.js", src.slice(0, start) + "[\n" + lines.join("\n") + "\n];\n" + src.slice(end + 3));
console.log("Reordenado OK");
