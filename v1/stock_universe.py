# -*- coding: utf-8 -*-
"""
全市場資料頁（產生 universe.html，可在網頁上自行設定篩選條件）

內容（全部為真實資料，抓不到的項目會標示失敗，不會補假資料）：
  ① 個股／ETF 篩選：上市（證交所）＋上櫃（櫃買）收盤價、漲跌、成交量、本益比、殖利率、股價淨值比
     可在網頁上即時設定：股價上限、成交量下限、本益比上限、殖利率下限、股價淨值比上限、類別、市場、關鍵字、排序
  ② 上市權證基本資料（證交所）
  ③ 申請上市公司清單（證交所）

用法：python stock_universe.py [--no-open] [--no-warrants]
需與 taiex_analyzer.py、stock_screener.py 放在同一資料夾。
資料為盤後（前一交易日）資料，不是即時報價。僅供研究參考，不構成投資建議。
"""
import argparse
import datetime as dt
import html
import json
import re
import webbrowser
from pathlib import Path

import pandas as pd
import requests

from stock_screener import twse_json
from taiex_analyzer import STATUS, UA, num, safe

OUT_FILE = Path(__file__).with_name("universe.html")


def tpex_json(name):
    url = "https://www.tpex.org.tw/openapi/v1/" + name
    try:
        r = requests.get(url, headers=UA, timeout=25)
    except requests.exceptions.SSLError:
        r = requests.get(url, headers=UA, timeout=25, verify=False)
    r.raise_for_status()
    return r.json()


def pick(d, *keys):
    for k in keys:
        if d.get(k) not in (None, "", "-", "--", "---"):
            return d[k]


def kind(code):
    if re.fullmatch(r"91\d{2}", code):
        return "TDR"
    if code.startswith("00"):
        return "ETF/ETN"
    if re.fullmatch(r"\d{4}", code):
        return "股票"
    if re.fullmatch(r"\d{4}[A-Z]", code):
        return "特別股"
    return "其他"


def row(code, name, mkt, price, chg, shares, val):
    base = price - chg
    pct = round(chg / base * 100, 2) if base > 0 else 0.0
    pe, y, pb = val.get(code, (None, None, None))
    return [code, name, kind(code), mkt, price, pct, round(shares / 1000), pe, y, pb]


def listed():
    px = pd.DataFrame(twse_json("exchangeReport/STOCK_DAY_ALL"))
    miss = [c for c in ("Code", "Name", "TradeVolume", "ClosingPrice") if c not in px.columns]
    if miss:
        raise RuntimeError(f"欄位不符，實際欄位：{list(px.columns)}")
    val = {}
    for r in safe("證交所本益比/殖利率/淨值比(BWIBBU_ALL)", twse_json, "exchangeReport/BWIBBU_ALL") or []:
        val[str(r.get("Code")).strip()] = (num(r.get("PEratio")), num(r.get("DividendYield")), num(r.get("PBratio")))
    out = []
    for r in px.to_dict("records"):
        code, price = str(r["Code"]).strip(), num(r["ClosingPrice"])
        if price and price > 0:
            out.append(row(code, str(r["Name"]).strip(), "上市", price, num(r.get("Change")) or 0.0, num(r["TradeVolume"]) or 0, val))
    if not out:
        raise RuntimeError("無有效收盤價")
    return out


def otc():
    q = tpex_json("tpex_mainboard_daily_close_quotes")
    if not q:
        raise RuntimeError("空資料")
    val = {}
    for r in safe("櫃買本益比/殖利率/淨值比", tpex_json, "tpex_mainboard_peratio_analysis") or []:
        c = str(pick(r, "SecuritiesCompanyCode", "Code") or "").strip()
        val[c] = (num(pick(r, "PriceEarningRatio", "PERatio")), num(pick(r, "YieldRatio", "DividendYield")),
                  num(pick(r, "PriceBookRatio", "PBRatio")))
    out = []
    for r in q:
        code = str(pick(r, "SecuritiesCompanyCode", "Code") or "").strip()
        price = num(pick(r, "Close", "ClosingPrice"))
        if code and price and price > 0:
            out.append(row(code, str(pick(r, "CompanyName", "Name") or code).strip(), "上櫃", price,
                           num(pick(r, "Change")) or 0.0, num(pick(r, "TradingShares", "TradeVolume")) or 0, val))
    if not out:
        raise RuntimeError(f"欄位不符，實際欄位：{list(q[0].keys())}")
    return out


def warrants():
    d = twse_json("opendata/t187ap37_L")
    if not d or "權證代號" not in d[0]:
        raise RuntimeError(f"欄位不符或空資料：{list(d[0].keys()) if d else '空'}")
    g = lambda r, k: r.get(k) or ""
    return [[g(r, "權證代號"), g(r, "權證簡稱"), g(r, "權證類型"), g(r, "標的證券/指數"),
             g(r, "最新履約價格(元)/履約指數"), g(r, "最後交易日"), g(r, "履約截止日")] for r in d]


def applying():
    d = twse_json("company/applylistingLocal")
    if not d:
        raise RuntimeError("空資料")
    names = {"Code": "代號", "Company": "公司", "ApplicationDate": "申請日", "Chairman": "董事長", "Capital": "資本額(千元)"}
    rows = []
    for r in d:
        k, v = list(r.keys()), list(r.values())
        # 證交所此資料集的欄位名稱與內容曾整體錯位一欄（序號被當成代號），偵測到就校正
        if len(v) > 1 and re.fullmatch(r"\d{1,3}", str(v[0]).strip()) and re.fullmatch(r"\d{4,6}", str(v[1]).strip()):
            r = dict(zip(k, v[1:]))
        rows.append(r)
    cols = list(rows[0].keys())
    return {"cols": [names.get(c, c) for c in cols], "rows": [[str(r.get(c, "")) for c in cols] for r in rows]}


PAGE = """<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>全市場篩選</title><style>
:root{--bg:#f6f7f9;--card:#fff;--tx:#1d2330;--mut:#6b7385;--bd:#dde1e8;--ac:#2b6cb0;--up:#d6293e;--dn:#1a9a52}
@media (prefers-color-scheme:dark){:root{--bg:#14171d;--card:#1d222b;--tx:#e7eaf0;--mut:#9aa3b5;--bd:#303744;--ac:#63a4f0;--up:#ff6b7d;--dn:#4fd18b}}
body{margin:0;background:var(--bg);color:var(--tx);font:15px/1.6 system-ui,'Noto Sans TC','Microsoft JhengHei',sans-serif}
main{max-width:1100px;margin:0 auto;padding:12px}.card{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:12px;margin:10px 0}
h1{font-size:21px}.g{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px}label{display:block;font-size:12px;color:var(--mut)}
input,select{width:100%;box-sizing:border-box;padding:6px;border:1px solid var(--bd);border-radius:6px;background:var(--bg);color:var(--tx);font:inherit}
input[type=checkbox]{width:auto;margin-right:4px}button{background:transparent;color:var(--ac);border:1px solid var(--ac);border-radius:6px;padding:6px 14px;font:inherit;cursor:pointer;margin:4px 6px 0 0}button.on{background:var(--ac);color:#fff}
.w{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:14px}th,td{border-bottom:1px solid var(--bd);padding:5px 8px;text-align:left;white-space:nowrap}
.up{color:var(--up)}.dn{color:var(--dn)}.note{font-size:13px;color:var(--mut)}a{color:var(--ac)}.chk label{display:inline;margin-right:10px;font-size:14px;color:var(--tx)}
</style></head><body><main>
<h1>🔎 全市場篩選（上市、上櫃、ETF、權證、申請上市）</h1>
<p style="font-size:15px">📍 <a href="taiex_report.html">大盤分析</a>｜<a href="universe.html"><b>🔎 全市場篩選</b></a>｜<a href="stock_report.html">個股深入評價</a></p>
<p class="note">資料日期：__DATE__（盤後資料，非即時報價）｜資料更新：__NOW__｜條件在你的瀏覽器即時篩選，不需重新執行。僅供研究參考，不構成投資建議。</p>
<div><button class="tb on" data-t="s" onclick="tab('s')">個股／ETF</button><button class="tb" data-t="w" onclick="tab('w')">權證</button><button class="tb" data-t="a" onclick="tab('a')">申請上市</button></div>

<section id="ts" class="card"><div class="g">
<div><label>股價上限（元）</label><input id="pmax" type="number" step="any" oninput="render()"></div>
<div><label>成交量下限（張）</label><input id="vmin" type="number" step="any" oninput="render()"></div>
<div><label>本益比上限（須為正）</label><input id="emax" type="number" step="any" oninput="render()"></div>
<div><label>殖利率下限（%）</label><input id="ymin" type="number" step="any" oninput="render()"></div>
<div><label>股價淨值比上限</label><input id="bmax" type="number" step="any" oninput="render()"></div>
<div><label>代號／名稱關鍵字</label><input id="q" oninput="render()"></div>
<div><label>排序</label><select id="sort" onchange="render()"><option value="vol">成交量（大到小）</option><option value="yld">殖利率（高到低）</option><option value="pe">本益比（低到高）</option><option value="up">漲幅（大到小）</option><option value="dn">跌幅（大到小）</option><option value="px">股價（低到高）</option></select></div></div>
<div class="chk" style="margin-top:8px">類別：__KINDS__　市場：<label><input type="checkbox" class="m" value="上市" checked onchange="render()"> 上市</label><label><input type="checkbox" class="m" value="上櫃" checked onchange="render()"> 上櫃</label>
<button onclick="reset()">重設條件</button></div>
<div style="margin-top:10px;padding:10px;border:1px dashed var(--bd);border-radius:8px"><b>想對這組條件做「個股深入評價」（財報＋技術＋新聞）？</b>
<p class="note">深入評價要在雲端抓財報與新聞，網頁內無法即時計算，所以這裡的篩選不會直接改變「個股深入評價」頁。請複製下面的參數，到 GitHub 的 Run workflow 貼進 stock_args 欄位執行，幾分鐘後該頁才會更新。深入評價只涵蓋上市個股，不含 ETF、上櫃；股價淨值比條件不會帶入。</p>
<div class="g"><div><label>深入評價檔數</label><input id="top" type="number" value="10" min="1" max="30" oninput="render()"></div><div><label>&nbsp;</label><label style="font-size:14px;color:var(--tx)"><input type="checkbox" id="gr" onchange="render()">只留營收年成長為正</label></div></div>
<p><code id="args" style="word-break:break-all"></code></p><button onclick="copyArgs()">複製參數</button><a id="gh" href="#" target="_blank" rel="noopener">開啟 Run workflow 頁面 →</a><p class="note" id="cm"></p></div>
<p class="note" id="cnt"></p><div class="w"><table id="st"></table></div></section>

<section id="tw" class="card" style="display:none"><div class="g"><div><label>關鍵字（代號、名稱、標的）</label><input id="q2" oninput="renderW()"></div>
<div><label>類型</label><select id="wt" onchange="renderW()"><option value="">全部</option><option>認購</option><option>認售</option></select></div></div>
<p class="note" id="cnt2"></p><div class="w"><table id="wtb"></table></div></section>

<section id="ta" class="card" style="display:none"><p class="note">證交所公開的本國公司申請上市清單（欄位依證交所原始資料；此資料集曾有欄位錯位，程式已自動校正，仍請以證交所官網為準）。申請上櫃與公開申購（抽籤）資訊本頁未涵蓋。</p><div class="w"><table id="atb"></table></div></section>

<div class="card"><b>資料來源狀態</b><table>__STATUS__</table></div>
</main><script>
const D=__DATA__;
const $=id=>document.getElementById(id),esc=s=>String(s==null?"":s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const nv=id=>{const v=$(id).value.trim();return v===""?null:parseFloat(v)},fx=(x,d)=>x==null?"—":x.toFixed(d);
function tab(t){["s","w","a"].forEach(k=>$("t"+k).style.display=k===t?"":"none");document.querySelectorAll(".tb").forEach(b=>b.classList.toggle("on",b.dataset.t===t))}
const SORT={vol:(a,b)=>b[6]-a[6],yld:(a,b)=>(b[8]==null?-1:b[8])-(a[8]==null?-1:a[8]),pe:(a,b)=>(a[7]==null||a[7]<=0?1e9:a[7])-(b[7]==null||b[7]<=0?1e9:b[7]),up:(a,b)=>b[5]-a[5],dn:(a,b)=>a[5]-b[5],px:(a,b)=>a[4]-b[4]};
function reset(){["pmax","vmin","emax","ymin","bmax","q"].forEach(i=>$(i).value="");$("sort").value="vol";render()}
function argStr(){const a=[],pm=nv("pmax"),vm=nv("vmin"),em=nv("emax"),ym=nv("ymin");
 if(pm!=null)a.push("--max-price "+pm);a.push("--min-volume "+(vm==null?0:vm));
 if(em!=null)a.push("--max-pe "+em);if(ym!=null)a.push("--min-yield "+ym);
 a.push("--top "+(nv("top")||10));if($("gr").checked)a.push("--growth");return a.join(" ")}
function copyArgs(){const t=$("args").textContent;
 if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(t).then(()=>{$("cm").textContent="已複製，請貼到 Run workflow 的 stock_args 欄位。"},()=>{$("cm").textContent="無法自動複製，請長按上方參數手動複製。"})}else{$("cm").textContent="請長按上方參數手動複製。"}}
(function(){const h=location.hostname;if(h.endsWith(".github.io")){const u=h.split(".")[0],r=location.pathname.split("/")[1];if(r)$("gh").href="https://github.com/"+u+"/"+r+"/actions/workflows/taiex-report.yml";else $("gh").style.display="none"}else $("gh").style.display="none"})();
function render(){
 $("args").textContent=argStr();
 const pm=nv("pmax"),vm=nv("vmin"),em=nv("emax"),ym=nv("ymin"),bm=nv("bmax"),q=$("q").value.trim().toLowerCase();
 const ks=[...document.querySelectorAll(".k:checked")].map(x=>x.value),ms=[...document.querySelectorAll(".m:checked")].map(x=>x.value);
 const r=D.stocks.filter(s=>ks.includes(s[2])&&ms.includes(s[3])&&(pm==null||s[4]<=pm)&&(vm==null||s[6]>=vm)&&(em==null||(s[7]!=null&&s[7]>0&&s[7]<=em))&&(ym==null||(s[8]!=null&&s[8]>=ym))&&(bm==null||(s[9]!=null&&s[9]<=bm))&&(!q||(s[0]+s[1]).toLowerCase().includes(q))).sort(SORT[$("sort").value]);
 $("cnt").textContent="符合 "+r.length+" 檔"+(r.length>400?"（僅顯示前 400 檔，請加條件縮小）":"");
 $("st").innerHTML="<tr><th>代號</th><th>名稱</th><th>類別</th><th>市場</th><th>收盤</th><th>漲跌%</th><th>量(張)</th><th>本益比</th><th>殖利率%</th><th>淨值比</th></tr>"+r.slice(0,400).map(s=>"<tr><td><a href='https://goodinfo.tw/tw/StockDetail.asp?STOCK_ID="+esc(s[0])+"' target='_blank' rel='noopener'>"+esc(s[0])+"</a></td><td>"+esc(s[1])+"</td><td>"+s[2]+"</td><td>"+s[3]+"</td><td>"+fx(s[4],2)+"</td><td class='"+(s[5]>0?"up":s[5]<0?"dn":"")+"'>"+(s[5]>0?"+":"")+s[5].toFixed(2)+"</td><td>"+s[6].toLocaleString()+"</td><td>"+fx(s[7],1)+"</td><td>"+fx(s[8],2)+"</td><td>"+fx(s[9],2)+"</td></tr>").join("")}
function renderW(){
 const q=$("q2").value.trim().toLowerCase(),t=$("wt").value;
 const r=D.warrants.filter(w=>(!t||String(w[2]).includes(t))&&(!q||w.slice(0,4).join(" ").toLowerCase().includes(q)));
 $("cnt2").textContent=D.warrants.length?("符合 "+r.length+" 檔"+(r.length>300?"（僅顯示前 300 檔，請加關鍵字）":"")):"本次未取得權證資料（見下方資料來源狀態）";
 $("wtb").innerHTML=D.warrants.length?"<tr><th>代號</th><th>簡稱</th><th>類型</th><th>標的</th><th>履約價</th><th>最後交易日</th><th>履約截止日</th></tr>"+r.slice(0,300).map(w=>"<tr>"+w.map(x=>"<td>"+esc(x)+"</td>").join("")+"</tr>").join(""):""}
function renderA(){const a=D.apply;$("atb").innerHTML=a&&a.rows.length?"<tr>"+a.cols.map(c=>"<th>"+esc(c)+"</th>").join("")+"</tr>"+a.rows.map(r=>"<tr>"+r.map(x=>"<td>"+esc(x)+"</td>").join("")+"</tr>").join(""):"<tr><td>本次未取得申請上市資料（見下方資料來源狀態）</td></tr>"}
render();renderW();renderA();
</script></body></html>"""


def main():
    ap = argparse.ArgumentParser(description="全市場篩選頁")
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--no-warrants", action="store_true", help="不抓權證（資料量大、平板載入較慢時可略過）")
    a = ap.parse_args()
    STATUS.clear()
    stocks = (safe("上市收盤價/ETF(證交所STOCK_DAY_ALL)", listed) or []) + (safe("上櫃收盤價(櫃買OpenAPI)", otc) or [])
    if not stocks:
        raise SystemExit("上市與上櫃資料都抓不到，請看上方錯誤訊息")
    war = [] if a.no_warrants else (safe("上市權證基本資料(t187ap37_L)", warrants) or [])
    app = safe("申請上市公司(applylistingLocal)", applying)
    kinds = sorted({s[2] for s in stocks}, key=lambda k: ["股票", "ETF/ETN", "特別股", "TDR", "其他"].index(k) if k in ["股票", "ETF/ETN", "特別股", "TDR", "其他"] else 9)
    kh = "".join(f'<label><input type="checkbox" class="k" value="{k}" {"checked" if k in ("股票", "ETF/ETN") else ""} onchange="render()"> {k}</label>' for k in kinds)
    st = "".join(f'<tr><td>{html.escape(n)}</td><td>{"成功" if ok else "失敗"}</td><td>{html.escape(m)}</td></tr>' for n, ok, m in STATUS)
    data = json.dumps({"stocks": stocks, "warrants": war, "apply": app}, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    page = (PAGE.replace("__DATA__", data).replace("__KINDS__", kh).replace("__STATUS__", st)
            .replace("__NOW__", f"{dt.datetime.now():%Y-%m-%d %H:%M}").replace("__DATE__", "最近一個交易日"))
    OUT_FILE.write_text(page, encoding="utf-8")
    print(f"股票/ETF {len(stocks)} 檔、權證 {len(war)} 檔、申請上市 {len(app['rows']) if app else 0} 家 → {OUT_FILE}")
    for n, ok, m in STATUS:
        if not ok:
            print(f"  ✗ {n}: {m}")
    if not a.no_open:
        webbrowser.open(OUT_FILE.as_uri())


if __name__ == "__main__":
    main()
