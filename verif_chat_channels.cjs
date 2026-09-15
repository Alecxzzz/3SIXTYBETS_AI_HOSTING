// Verifica el channels.js de threesixtybets-chat
const fs = require("fs");
const s = fs.readFileSync("C:/Users/AlecS/OneDrive/Desktop/threesixtybets-chat/src/data/channels.js", "utf8");
const names = [...s.matchAll(/name: "([^"]+)"/g)].map((m) => m[1]);
const ids = [...s.matchAll(/id: (\d+)/g)].map((m) => Number(m[1]));
const sorted = [...names].sort((a, b) => a.localeCompare(b, "en"));
console.log("Orden alfabetico correcto:", JSON.stringify(names) === JSON.stringify(sorted));
console.log("Total canales:", names.length, "| IDs unicos:", new Set(ids).size === ids.length);
console.log("Nuevos presentes:", ["NYMSPORTS", "SKY SPORTS NFL", "TENNIS CHANNEL"].every((n) => names.includes(n)));
console.log(names.join(" | "));
