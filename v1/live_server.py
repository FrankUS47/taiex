# -*- coding: utf-8 -*-
"""
即時看盤小工具：在你自己的電腦上執行，網頁每幾秒自動更新個股即時價、最佳五檔（委買委賣）、
委買委賣量比、內外盤估算與盤中走勢小圖。比 GitHub 排程快照快很多（只受證交所資料延遲影響，約數秒）。

資料來源：證交所公開資訊觀測站的即時報價介面 mis.twse.com.tw（非正式 API，可能改版或限流，請勿過度頻繁抓取）。

用法：
  python live_server.py                      # 只開放本機：http://127.0.0.1:8000
  python live_server.py --lan                # 開放區域網路，平板用同一個 Wi-Fi 開 http://電腦IP:8000
  python live_server.py --port 8080 --codes 2330,2317,0050
注意：開放區域網路時，同一網路的人都能看到這個頁面（沒有帳號驗證）；請只在自己信任的網路使用。
委買委賣掛單可以隨時撤銷，大量掛單不一定代表真實意圖；內外盤為依成交價與前次最佳買賣價估算，只從啟動後開始累計。
僅供研究參考，不構成投資建議。
"""
import argparse
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import requests

API = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
UA = {"User-Agent": "Mozilla/5.0 (compatible; LiveBook/1.0)"}
SESSION = requests.Session()
SESSION.headers.update(UA)
LOCK = threading.Lock()
STATE = {}          # code -> {"v","ask1","bid1","outer","inner"}
COOKIE_AT = [0.0]


def fnum(x):
    try:
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return None


def split(x):
    return [v for v in str(x or "").split("_") if v]


def ensure_cookie():
    if time.time() - COOKIE_AT[0] > 1200:
        SESSION.get("https://mis.twse.com.tw/stock/index.jsp", timeout=10)
        COOKIE_AT[0] = time.time()


def parse_msg(m, st):
    """把證交所即時報價的一筆資料整理成網頁用的格式，並更新內外盤累計。"""
    z, y = fnum(m.get("z")), fnum(m.get("y"))
    asks = [[fnum(p), fnum(v)] for p, v in zip(split(m.get("a")), split(m.get("f")))]
    bids = [[fnum(p), fnum(v)] for p, v in zip(split(m.get("b")), split(m.get("g")))]
    v = fnum(m.get("v")) or 0.0
    if st.get("v") is not None and v > st["v"] and z is not None:
        dv = v - st["v"]
        if st.get("ask1") is not None and z >= st["ask1"]:
            st["outer"] = st.get("outer", 0) + dv
        elif st.get("bid1") is not None and z <= st["bid1"]:
            st["inner"] = st.get("inner", 0) + dv
    st["v"] = v
    st["ask1"] = asks[0][0] if asks else st.get("ask1")
    st["bid1"] = bids[0][0] if bids else st.get("bid1")
    bt, at = sum(b[1] or 0 for b in bids), sum(a[1] or 0 for a in asks)
    chg = (z - y) if (z is not None and y) else None
    return {"c": m.get("c"), "n": m.get("n"), "z": z, "y": y, "chg": chg, "pct": (chg / y * 100) if chg is not None else None,
            "o": fnum(m.get("o")), "h": fnum(m.get("h")), "l": fnum(m.get("l")), "v": v, "tv": fnum(m.get("tv")),
            "t": m.get("t"), "d": m.get("d"), "u": fnum(m.get("u")), "w": fnum(m.get("w")),
            "asks": asks, "bids": bids, "bt": bt, "at": at, "ratio": (bt / (bt + at)) if (bt + at) > 0 else None,
            "spread": (asks[0][0] - bids[0][0]) if (asks and bids and asks[0][0] and bids[0][0]) else None,
            "outer": st.get("outer", 0), "inner": st.get("inner", 0)}


def fetch(codes):
    ensure_cookie()
    ex = "|".join(f"{mk}_{c}.tw" for c in codes for mk in ("tse", "otc"))
    j = SESSION.get(API, params={"ex_ch": ex, "json": "1", "delay": "0", "_": int(time.time() * 1000)}, timeout=10).json()
    out, seen = [], set()
    with LOCK:
        for m in j.get("msgArray", []):
            c = m.get("c")
            if not c or c in seen:
                continue
            seen.add(c)
            out.append(parse_msg(m, STATE.setdefault(c, {})))
    order = {c: i for i, c in enumerate(codes)}
    return sorted(out, key=lambda r: order.get(r["c"], 99))


PAGE = """<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>即時看盤（五檔）</title><style>
:root{--bg:#f6f7f9;--card:#fff;--tx:#1d2330;--mut:#6b7385;--bd:#dde1e8;--ac:#2b6cb0;--up:#d6293e;--dn:#1a9a52}
@media (prefers-color-scheme:dark){:root{--bg:#14171d;--card:#1d222b;--tx:#e7eaf0;--mut:#9aa3b5;--bd:#303744;--ac:#63a4f0;--up:#ff6b7d;--dn:#4fd18b}}
body{margin:0;background:var(--bg);color:var(--tx);font:15px/1.5 system-ui,'Noto Sans TC','Microsoft JhengHei',sans-serif}main{max-width:1100px;margin:0 auto;padding:12px}
input,select,button{padding:6px 10px;border:1px solid var(--bd);border-radius:6px;background:var(--card);color:var(--tx);font:inherit}button{color:var(--ac);border-color:var(--ac);cursor:pointer}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px;margin-top:12px}.card{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:12px}
.up{color:var(--up)}.dn{color:var(--dn)}.note{font-size:12px;color:var(--mut)}.big{font-size:28px;font-weight:700}table{width:100%;border-collapse:collapse;font-size:14px}
td{padding:2px 6px;position:relative}td.b{text-align:right}.bar{position:absolute;top:2px;bottom:2px;opacity:.18;z-index:0}.bar+span{position:relative;z-index:1}
.ratio{display:flex;height:10px;border-radius:5px;overflow:hidden;margin:6px 0}.ratio div{height:100%}
</style></head><body><main>
<h2 style="margin:6px 0">⚡ 即時看盤（最佳五檔、委買委賣）</h2>
<div><input id="codes" value="__CODES__" size="26" placeholder="股票代號，用逗號分隔"> <select id="iv"><option value="3000">每 3 秒</option><option value="5000" selected>每 5 秒</option><option value="10000">每 10 秒</option></select> <button id="go">開始</button> <button id="stop">停止</button> <span class="note" id="st"></span></div>
<p class="note">資料：證交所即時報價（非正式介面，約有數秒延遲）。僅在盤中有意義。委買委賣掛單可隨時撤銷，大量掛單不代表真實意圖；內外盤為啟動後的估算。僅供研究參考，不構成投資建議。</p>
<div class="grid" id="cards"></div></main><script>
const $=id=>document.getElementById(id),H={};let timer=null;
const esc=s=>String(s==null?"":s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const f=(x,d=2)=>x==null?"—":x.toFixed(d),cl=x=>x>0?"up":x<0?"dn":"";
function spark(c,p){if(p==null)return"";const a=H[c]=(H[c]||[]);if(!a.length||a[a.length-1]!==p)a.push(p);if(a.length>120)a.shift();
 if(a.length<2)return"";const mn=Math.min(...a),mx=Math.max(...a),r=mx-mn||1,pts=a.map((v,i)=>(i/(a.length-1)*280)+","+(34-(v-mn)/r*30)).join(" ");
 return "<svg viewBox='0 0 280 38' style='width:100%;height:38px'><polyline fill='none' stroke='#2b6cb0' stroke-width='1.5' points='"+pts+"'/></svg>"}
function book(r){const mx=Math.max(1,...r.asks.map(a=>a[1]||0),...r.bids.map(b=>b[1]||0));
 const row=(x,col)=>"<tr><td><span class='"+col+"'>"+f(x[0])+"</span></td><td class='b'><div class='bar' style='width:"+((x[1]||0)/mx*100)+"%;right:0;background:var(--"+(col==="up"?"up":"dn")+")'></div><span>"+(x[1]==null?"—":x[1].toLocaleString())+"</span></td></tr>";
 return "<table>"+r.asks.slice().reverse().map(a=>row(a,"dn")).join("")+"<tr><td colspan='2' style='border-top:1px solid var(--bd)'></td></tr>"+r.bids.map(b=>row(b,"up")).join("")+"</table>"}
function hints(r){const h=[];
 if(r.ratio!=null){h.push(r.ratio>=.6?"五檔委買量（"+r.bt.toLocaleString()+"）明顯大於委賣量（"+r.at.toLocaleString()+"），買方掛單較積極。":r.ratio<=.4?"五檔委賣量（"+r.at.toLocaleString()+"）明顯大於委買量（"+r.bt.toLocaleString()+"），賣方掛單較積極。":"五檔委買委賣量大致均衡。")}
 const t=r.outer+r.inner;if(t>0)h.push("啟動後估算：外盤 "+(r.outer/t*100).toFixed(0)+"%、內盤 "+(r.inner/t*100).toFixed(0)+"%（外盤＝以賣價成交，偏主動買；內盤偏主動賣）。");
 if(r.z!=null&&r.u&&r.z>=r.u)h.push("目前在漲停價。");if(r.z!=null&&r.w&&r.z<=r.w)h.push("目前在跌停價。");
 if(r.spread!=null&&r.z)h.push("買賣價差 "+f(r.spread)+"（"+(r.spread/r.z*100).toFixed(2)+"%）。");
 h.push("掛單可隨時撤銷，僅供參考。");return h.map(x=>"<div class='note'>• "+esc(x)+"</div>").join("")}
function card(r){const c=cl(r.chg);return "<div class='card'><b>"+esc(r.n)+"</b> <span class='note'>"+esc(r.c)+"｜"+esc(r.t||"")+"</span><div><span class='big "+c+"'>"+f(r.z)+"</span> <span class='"+c+"'>"+(r.chg==null?"—":(r.chg>0?"+":"")+f(r.chg)+"（"+(r.pct>0?"+":"")+f(r.pct)+"%）")+"</span></div>"+
 "<div class='note'>開 "+f(r.o)+"｜高 "+f(r.h)+"｜低 "+f(r.l)+"｜昨收 "+f(r.y)+"｜累計量 "+(r.v||0).toLocaleString()+" 張</div>"+spark(r.c,r.z)+
 (r.ratio==null?"":"<div class='ratio'><div style='width:"+(r.ratio*100)+"%;background:var(--up)'></div><div style='width:"+((1-r.ratio)*100)+"%;background:var(--dn)'></div></div><div class='note'>委買 "+(r.ratio*100).toFixed(0)+"%｜委賣 "+((1-r.ratio)*100).toFixed(0)+"%</div>")+book(r)+hints(r)+"</div>"}
async function tick(){const codes=$("codes").value.replace(/\\s/g,"");try{const r=await fetch("/api/quote?codes="+encodeURIComponent(codes));const j=await r.json();
 if(j.error){$("st").textContent="錯誤："+j.error;return}$("cards").innerHTML=j.rows.map(card).join("")||"<p class='note'>沒有取得資料（代號是否正確？是否在交易時段？）</p>";$("st").textContent="更新："+new Date().toLocaleTimeString()}catch(e){$("st").textContent="連線失敗："+e}}
function start(){stop();tick();timer=setInterval(tick,+$("iv").value)}function stop(){if(timer)clearInterval(timer);timer=null}
$("go").onclick=start;$("stop").onclick=stop;$("iv").onchange=()=>{if(timer)start()};start();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    codes_default = "2330,2317,0050"

    def log_message(self, *a):
        pass

    def send(self, code, body, ctype):
        b = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/":
            q = parse_qs(u.query).get("codes", [self.codes_default])[0]
            self.send(200, PAGE.replace("__CODES__", q.replace('"', "")), "text/html")
        elif u.path == "/api/quote":
            codes = [c for c in parse_qs(u.query).get("codes", [""])[0].replace("，", ",").split(",") if c.strip()][:20]
            try:
                self.send(200, json.dumps({"rows": fetch([c.strip() for c in codes])}, ensure_ascii=False), "application/json")
            except Exception as e:  # noqa
                self.send(200, json.dumps({"error": f"{type(e).__name__}: {str(e)[:120]}"}, ensure_ascii=False), "application/json")
        else:
            self.send(404, "not found", "text/plain")


def main():
    ap = argparse.ArgumentParser(description="即時看盤（五檔）")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--lan", action="store_true", help="開放區域網路（平板可連線）")
    ap.add_argument("--codes", default="2330,2317,0050")
    a = ap.parse_args()
    Handler.codes_default = a.codes
    host = "0.0.0.0" if a.lan else "127.0.0.1"
    srv = ThreadingHTTPServer((host, a.port), Handler)
    print(f"本機：http://127.0.0.1:{a.port}/")
    if a.lan:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            print(f"區域網路（平板用同一個 Wi-Fi）：http://{s.getsockname()[0]}:{a.port}/")
        except OSError:
            print("區域網路已開放，請用電腦的區網 IP 連線。")
    print("按 Ctrl+C 結束。")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
