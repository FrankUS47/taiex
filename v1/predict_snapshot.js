// 個股預測快照：用 universe.html 裡「同一套」JavaScript 算出每檔的預測（避免兩套邏輯不一致），
// 在每個交易日開盤前凍結（開盤後不再更動），存到 cache/stock_pred.json；隔天起就能和實際比對。
// 同時依「前一個資料日」的預測 vs 實際，逐日校準預測幅度的縮放係數（cal）。
// 用法：node predict_snapshot.js universe.html cache/stock_pred.json
// 測試用：PRED_NOW=2026-11-13T08:30:00 可指定「現在」的時間。
const fs = require("fs"), vm = require("vm"), path = require("path");
const htmlPath = process.argv[2] || "universe.html", outPath = process.argv[3] || "cache/stock_pred.json";
const html = fs.readFileSync(htmlPath, "utf8");
const m = html.match(/<script>([\s\S]*)<\/script>\s*<\/body>/) || html.match(/<script>([\s\S]*)<\/script>/);
if (!m) { console.log("找不到頁面程式碼，略過"); process.exit(0); }
const els = {};
const mk = id => els[id] || (els[id] = { value: "", textContent: "", innerHTML: "", style: {}, checked: false, classList: { toggle() {} } });
global.document = { getElementById: mk, querySelectorAll: () => [] };
global.location = { pathname: "/", replace() {}, href: "" };
global.setTimeout = () => 0; global.setInterval = () => 0;
global.localStorage = { getItem() { return null; }, setItem() {} };
vm.runInThisContext(m[1]);
const D = vm.runInThisContext("D"), predNow = vm.runInThisContext("predNow");
const now = process.env.PRED_NOW ? new Date(process.env.PRED_NOW) : new Date();
const pad = n => String(n).padStart(2, "0");
const ymd = d => d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
const mins = d => d.getHours() * 60 + d.getMinutes();
function targetDate(d) {  // 平日 13:30 前預測今天，之後預測下一個平日（未考慮國定假日）
  const t = new Date(d);
  if (d.getDay() >= 1 && d.getDay() <= 5 && mins(d) < 13 * 60 + 30) return ymd(t);
  do { t.setDate(t.getDate() + 1); } while (t.getDay() === 0 || t.getDay() === 6);
  return ymd(t);
}
let store = { snaps: [], cal: { hist: {}, value: 1 } };
try { store = JSON.parse(fs.readFileSync(outPath, "utf8")); } catch (e) {}
store.snaps = store.snaps || []; store.cal = store.cal || { hist: {}, value: 1 };

// 1) 校準：資料日已收盤，就用該日的盤前快照（原始預測）對實際漲跌做通過原點的迴歸
const DD = D.data_date, today = ymd(now);
const final = DD && (DD < today || (DD === today && mins(now) >= 13 * 60 + 45));
if (final && store.cal.hist[DD] == null) {
  const snap = store.snaps.find(x => x.for === DD && !x.late);
  if (snap) {
    let sxy = 0, sxx = 0, n = 0;
    D.stocks.forEach(s => { const v = snap.stocks[s[0]]; if (v && v[2]) { sxy += v[2] * s[5]; sxx += v[2] * v[2]; n++; } });
    if (n > 200 && sxx > 0) { store.cal.hist[DD] = Math.max(-1, Math.min(3, sxy / sxx)); console.log("校準 " + DD + "：斜率 " + store.cal.hist[DD].toFixed(3) + "（" + n + " 檔）"); }
  }
}
const keys = Object.keys(store.cal.hist).sort().slice(-60);
const nh = {}; keys.forEach(k => nh[k] = +store.cal.hist[k].toFixed(4)); store.cal.hist = nh;
const last = keys.slice(-20).map(k => nh[k]);
store.cal.value = last.length ? +Math.max(0, Math.min(2, last.reduce((a, b) => a + b, 0) / last.length)).toFixed(3) : 1;

// 2) 新快照：預測下一個目標交易日；目標日開盤（09:00）之後就凍結，不再更動
const N = targetDate(now), preOpen = !(ymd(now) === N && mins(now) >= 9 * 60);
const snap = { for: N, made: ymd(now) + " " + pad(now.getHours()) + ":" + pad(now.getMinutes()), late: !preOpen, cal: store.cal.value, stocks: {} };
let cnt = 0;
D.stocks.forEach(s => {
  const p = predNow(s[0], false);
  if (p && isFinite(p.close) && isFinite(p.gap)) { snap.stocks[s[0]] = [+p.total.toFixed(2), +p.gap.toFixed(3), +p.close.toFixed(3)]; cnt++; }
});
const idx = store.snaps.findIndex(x => x.for === N);
if (idx < 0) { store.snaps.push(snap); console.log("建立 " + N + " 的預測快照（" + cnt + " 檔）" + (snap.late ? "，注意：已過開盤，標記為盤中補建，不列入比對" : "")); }
else if (preOpen) { store.snaps[idx] = snap; console.log("更新 " + N + " 的盤前預測快照（" + cnt + " 檔）"); }
else console.log(N + " 已開盤，盤前預測快照維持凍結");
store.snaps.sort((a, b) => a.for < b.for ? -1 : 1);
store.snaps = store.snaps.slice(-4);
fs.mkdirSync(path.dirname(outPath), { recursive: true });
fs.writeFileSync(outPath, JSON.stringify(store));
console.log("校準係數 " + store.cal.value + "；快照 " + store.snaps.map(x => x.for + (x.late ? "(補)" : "")).join("、") + " → " + outPath);
