# -*- coding: utf-8 -*-
"""
全市場篩選與評價頁（產生 universe.html，條件在網頁上自行設定，全部為真實資料）

資料來源（抓不到的項目會標示失敗，不會補假資料）：
  - 證交所／櫃買：收盤價、成交量、本益比、殖利率、股價淨值比、產業別、權證、申請上市
  - 證交所即時報價（盤中疊加，抓不到就沿用收盤價）
  - cache/scores.json（stock_scores.py）：個股深入評價（基本面＋技術面＋新聞）
  - cache/trends.json（stock_trends.py）：連續上漲／下跌日數、5日與20日漲幅、K棒過長警示

用法：python stock_universe.py [--no-open] [--no-warrants]
僅供研究參考，不構成投資建議。
"""
import argparse
import datetime as dt
import html
import json
import re
import time
import webbrowser
from pathlib import Path

import pandas as pd
import requests

from chart_patterns import HELP, TAIL
from stock_scores import SCORES
from stock_screener import twse_json
from taiex_analyzer import STATUS, UA, expected_last_day, num, safe

OUT_FILE = Path(__file__).with_name("universe.html")
TRENDS = Path(__file__).with_name("cache") / "trends.json"
ETFC = Path(__file__).with_name("cache") / "etf.json"

IND = {"01": "水泥工業", "02": "食品工業", "03": "塑膠工業", "04": "紡織纖維", "05": "電機機械", "06": "電器電纜",
       "08": "玻璃陶瓷", "09": "造紙工業", "10": "鋼鐵工業", "11": "橡膠工業", "12": "汽車工業", "14": "建材營造",
       "15": "航運業", "16": "觀光餐旅", "17": "金融保險", "18": "貿易百貨", "19": "綜合", "20": "其他",
       "21": "化學工業", "22": "生技醫療", "23": "油電燃氣", "24": "半導體", "25": "電腦及週邊", "26": "光電",
       "27": "通信網路", "28": "電子零組件", "29": "電子通路", "30": "資訊服務", "31": "其他電子", "32": "文化創意",
       "33": "農業科技", "34": "電子商務", "35": "綠能環保", "36": "數位雲端", "37": "運動休閒", "38": "居家生活",
       "91": "存託憑證"}


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


def industries():
    m = {}
    d = twse_json("opendata/t187ap03_L")
    if not d or "公司代號" not in d[0]:
        raise RuntimeError(f"欄位不符或空資料：{list(d[0].keys()) if d else '空'}")
    for r in d:
        c = str(r.get("公司代號", "")).strip()
        v = str(r.get("產業別", "")).strip()
        if c:
            m[c] = IND.get(v, v or "未分類")
    return m


def industries_otc():
    m = {}
    d = tpex_json("mopsfin_t187ap03_O")
    if not d:
        raise RuntimeError("空資料")
    for r in d:
        c = str(pick(r, "公司代號", "SecuritiesCompanyCode", "Code") or "").strip()
        v = str(pick(r, "產業別", "SecuritiesIndustryCode", "Industry") or "").strip()
        if c:
            m[c] = IND.get(v, v or "未分類")
    if not m:
        raise RuntimeError(f"欄位不符，實際欄位：{list(d[0].keys())}")
    return m


def realtime_overlay(stocks):
    """用證交所即時報價覆蓋收盤價（盤中才有意義；抓不到就保留收盤價）。
    小批次抓取並重試，成交量大的優先；連續 3 批沒資料就停止。回傳 (覆蓋檔數, 報價時間, 嘗試檔數)。"""
    def new_session():
        s = requests.Session()
        s.headers.update(UA)
        s.get("https://mis.twse.com.tw/stock/index.jsp", timeout=12)
        return s

    ss = new_session()
    ch = {f"{'tse' if r[3] == '上市' else 'otc'}_{r[0]}.tw": r for r in stocks if r[2] in ("股票", "ETF/ETN", "特別股", "TDR")}
    keys = sorted(ch, key=lambda k: -(ch[k][6] or 0))
    n, stamp, empty = 0, "", 0
    for i in range(0, len(keys), 40):
        arr = []
        for attempt in (1, 2):
            try:
                j = ss.get("https://mis.twse.com.tw/stock/api/getStockInfo.jsp",
                           params={"ex_ch": "|".join(keys[i:i + 40]), "json": "1", "delay": "0", "_": int(time.time() * 1000)},
                           timeout=20).json()
                arr = j.get("msgArray", [])
            except Exception:  # noqa
                arr = []
            if arr or attempt == 2:
                break
            time.sleep(1.5)
            ss = new_session()
        empty = 0 if arr else empty + 1
        if empty >= 3:
            break
        for m in arr:
            z, y, v = num(m.get("z")), num(m.get("y")), num(m.get("v"))
            r = ch.get(f"{m.get('ex')}_{m.get('c')}.tw")
            if r and z and y and y > 0:
                r[4], r[5] = z, round((z - y) / y * 100, 2)
                if v is not None:
                    r[6] = round(v)
                n += 1
                stamp = max(stamp, f"{m.get('d', '')} {m.get('t', '')}")
        time.sleep(0.6)
    if not n:
        raise RuntimeError("沒有取得即時成交價（非開盤時段或被擋）")
    return n, stamp, len(keys)


def institutional_by_stock():
    """證交所三大法人買賣超日報（上市個股，單位換算為張）。取最近一個有資料的交易日。"""
    d = dt.date.today()
    for _ in range(10):
        if d.weekday() < 5:
            j = requests.get("https://www.twse.com.tw/rwd/zh/fund/T86",
                             params={"date": d.strftime("%Y%m%d"), "selectType": "ALLBUT0999", "response": "json"},
                             headers=UA, timeout=25).json()
            if j.get("stat") == "OK" and j.get("data"):
                f = j["fields"]

                def idx(*pats, none=()):
                    for must in pats:
                        for i, x in enumerate(f):
                            if must in x and not any(n in x for n in none):
                                return i

                i_fi = idx("外陸資買賣超", "外資及陸資買賣超")
                i_it = idx("投信買賣超")
                i_dl = idx("自營商買賣超股數", none=("外資", "自行", "避險"))
                i_tot = idx("三大法人買賣超")
                if None in (i_fi, i_it, i_tot):
                    raise RuntimeError(f"欄位不符：{f}")
                g = lambda r, i: 0 if i is None else round((num(r[i]) or 0) / 1000)
                rows = [[str(r[0]).strip(), str(r[1]).strip(), g(r, i_fi), g(r, i_it), g(r, i_dl), g(r, i_tot)] for r in j["data"]]
                return {"date": d.isoformat(), "rows": rows}
        d -= dt.timedelta(days=1)
    raise RuntimeError("近 10 天查無三大法人個股買賣超資料")


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


def load_cache(path, key="stocks"):
    try:
        j = json.loads(path.read_text(encoding="utf-8"))
        return j.get(key, {}), j.get("generated", "")[:16].replace("T", " "), j
    except Exception:  # noqa
        return {}, "", {}


PAGE = """<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>全市場篩選與評價</title><style>
:root{--bg:#f6f7f9;--card:#fff;--tx:#1d2330;--mut:#6b7385;--bd:#dde1e8;--ac:#2b6cb0;--up:#d6293e;--dn:#1a9a52}
@media (prefers-color-scheme:dark){:root{--bg:#14171d;--card:#1d222b;--tx:#e7eaf0;--mut:#9aa3b5;--bd:#303744;--ac:#63a4f0;--up:#ff6b7d;--dn:#4fd18b}}
body{margin:0;background:var(--bg);color:var(--tx);font:15px/1.6 system-ui,'Noto Sans TC','Microsoft JhengHei',sans-serif}
main{max-width:none;margin:0 auto;padding:12px}.card{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:12px;margin:10px 0}
h1{font-size:21px}.g{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px}label{display:block;font-size:12px;color:var(--mut)}
input,select{width:100%;box-sizing:border-box;padding:6px;border:1px solid var(--bd);border-radius:6px;background:var(--bg);color:var(--tx);font:inherit}
input[type=checkbox]{width:auto;margin-right:4px}button{background:transparent;color:var(--ac);border:1px solid var(--ac);border-radius:6px;padding:6px 14px;font:inherit;cursor:pointer;margin:4px 6px 0 0}button.on{background:var(--ac);color:#fff}
.w{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:14px}th,td{border-bottom:1px solid var(--bd);padding:5px 8px;text-align:left;white-space:nowrap}
.up{color:var(--up)}.dn{color:var(--dn)}.note{font-size:13px;color:var(--mut)}a{color:var(--ac)}.chk label{display:inline;margin-right:10px;font-size:14px;color:var(--tx)}
details{margin-top:8px}summary{cursor:pointer;color:var(--ac)}#indbox label{display:inline-block;margin:2px 10px 2px 0;font-size:14px;color:var(--tx)}
#st{font-size:13px}th.srt{cursor:pointer;user-select:none}th.srt:hover{background:rgba(43,108,176,.12)}
th.c-code,td.c-code{position:sticky;left:0;background:var(--card);z-index:2;min-width:64px}
th.c-name,td.c-name{position:sticky;left:64px;background:var(--card);z-index:2;box-shadow:2px 0 3px rgba(0,0,0,.08)}
.bd{display:inline-block;padding:1px 8px;border-radius:10px;font-weight:600}#colbox label{display:inline-block;margin:2px 10px 2px 0;font-size:14px;color:var(--tx)}
</style></head><body><main>
<h1>🔎 全市場篩選與評價（上市、上櫃、ETF、權證、申請上市）</h1>
<p style="font-size:15px">📍 <a class="nv" href="taiex_report.html">大盤分析</a>｜<a class="nv" href="universe.html"><b>🔎 全市場篩選與評價</b></a></p>
<p class="note">報價：__DATE__｜資料更新：__NOW__｜條件在你的瀏覽器即時篩選，不需重新執行。僅供研究參考，不構成投資建議。</p>
<div id="stale" style="display:none;background:#fff3cd;color:#1d2330;border:1px solid #e0a800;border-radius:6px;padding:8px;margin:6px 0"></div>
<div><button class="tb on" data-t="s" onclick="tab('s')">個股／ETF</button><button class="tb" data-t="b" onclick="tab('b')">類股強弱</button><button class="tb" data-t="i" onclick="tab('i')">法人買賣超</button><button class="tb" data-t="w" onclick="tab('w')">權證</button><button class="tb" data-t="a" onclick="tab('a')">申請上市</button></div>

<section id="ts" class="card"><div class="g">
<div><label>股價上限（元）</label><input id="pmax" type="number" step="any" oninput="render()"></div>
<div><label>成交量下限（張）</label><input id="vmin" type="number" step="any" oninput="render()"></div>
<div><label>本益比上限（須為正）</label><input id="emax" type="number" step="any" oninput="render()"></div>
<div><label>殖利率下限（%）</label><input id="ymin" type="number" step="any" oninput="render()"></div>
<div><label>股價淨值比上限</label><input id="bmax" type="number" step="any" oninput="render()"></div>
<div><label>連續上漲 ≥ N 日</label><input id="upn" type="number" step="1" min="1" oninput="render()"></div>
<div><label>連續下跌 ≥ N 日</label><input id="dnn" type="number" step="1" min="1" oninput="render()"></div>
<div><label>K棒警示</label><select id="kb" onchange="render()"><option value="">全部</option><option value="任一">任一異常（長K／振幅大／跳空）</option><option value="長紅">長紅K</option><option value="長黑">長黑K</option><option value="振幅大">振幅特別大</option><option value="跳空">跳空缺口</option></select></div>
<div><label>圖形型態</label><select id="pt" onchange="render()"><option value="">全部</option></select></div>
<div><label>型態方向</label><select id="pb" onchange="render()"><option value="">全部</option><option value="多">偏多型態</option><option value="空">偏空型態</option></select></div>
<div><label>評價總分下限</label><input id="smin" type="number" step="any" oninput="render()"></div>
<div><label>評等</label><select id="ov" onchange="render()"><option value="">全部</option><option>偏買進</option><option>觀望偏多</option><option>中性</option><option>觀望偏空</option><option>偏賣出</option></select></div>
<div><label>代號／名稱關鍵字</label><input id="q" oninput="render()"></div>
<div><label>排序</label><select id="sort" onchange="SORTC={k:null,d:1};render()"><option value="vol">成交量（大到小）</option><option value="yld">殖利率（高到低）</option><option value="pe">本益比（低到高）</option><option value="up">當日漲幅（大到小）</option><option value="dn">當日跌幅（大到小）</option><option value="px">股價（低到高）</option><option value="stk">連續上漲日數（多到少）</option><option value="stkd">連續下跌日數（多到少）</option><option value="c5">5日漲幅（大到小）</option><option value="c20">20日漲幅（大到小）</option><option value="tot">評價總分（高到低）</option></select></div></div>
<div class="chk" style="margin-top:8px">類別：__KINDS__　市場：<label><input type="checkbox" class="m" value="上市" checked onchange="render()"> 上市</label><label><input type="checkbox" class="m" value="上櫃" checked onchange="render()"> 上櫃</label>
<label><input type="checkbox" id="pc" onchange="render()">只看已確認型態</label><label><input type="checkbox" id="sc" onchange="render()">只看已評價</label><label><input type="checkbox" id="gr" onchange="render()">只留營收年成長為正</label>
<button onclick="reset()">重設條件</button></div>
<details><summary>產業別（點開選擇，預設全選）</summary><div style="margin:6px 0"><button onclick="allInd(true)">全選</button><button onclick="allInd(false)">全不選</button></div><div id="indbox"></div></details>
<details><summary>顯示欄位（表格太寬時，可隱藏不需要的欄位；點欄位名稱可排序）</summary><div style="margin:6px 0"><button onclick="colPreset('lite')">精簡</button><button onclick="colPreset('full')">完整</button></div><div id="colbox"></div></details>
<p class="note" id="sn"></p><p class="note" id="cnt"></p>
<p class="note">顏色與百分比：<span class="up">紅色＝偏多（偏買進）傾向</span>、<span class="dn">綠色＝偏空（偏賣出）傾向</span>，百分比越大程度越強。「技術傾向」由圖形型態（已確認 ±2、形成中 ±1）、連續漲跌 3 日以上、20 日漲跌超過 5%、K 棒警示（長紅黑、跳空）加總，最多 ±100%；「綜合傾向」由深入評價總分換算（僅有評價的股票）。皆為規則式量化傾向，不是買賣建議，也不保證未來表現。</p><div class="w"><table id="st"></table></div></section>

<section id="tb" class="card" style="display:none"><p class="note">依目前報價計算各產業（僅一般股票）的平均漲跌、上漲與下跌家數與總成交量；點產業名稱會跳到篩選頁並只選該產業。</p><div class="w"><table id="btb"></table></div></section>

<section id="ti" class="card" style="display:none"><div class="g">
<div><label>對象</label><select id="iw" onchange="renderI()"><option value="2">外資及陸資</option><option value="3">投信</option><option value="4">自營商</option><option value="5">三大法人合計</option></select></div>
<div><label>方向</label><select id="idir" onchange="renderI()"><option value="buy">買超</option><option value="sell">賣超</option></select></div>
<div><label>顯示前 N 檔</label><input id="in" type="number" value="30" min="5" max="200" oninput="renderI()"></div>
<div><label>股價上限（元，可空白）</label><input id="ip" type="number" step="any" oninput="renderI()"></div>
<div><label>產業</label><select id="ii" onchange="renderI()"><option value="">全部</option></select></div></div>
<p class="note" id="inote"></p>
<p class="note">國安基金不公布逐檔每日買賣明細，只有進場時的公告與新聞，請看大盤分析頁的「國安基金與官股」新聞。這裡的法人資料僅涵蓋上市個股，上櫃與券商分點（官股行庫）資料未納入。</p>
<div class="w"><table id="itb"></table></div></section>

<section id="tw" class="card" style="display:none"><div class="g"><div><label>關鍵字（代號、名稱、標的）</label><input id="q2" oninput="renderW()"></div>
<div><label>類型</label><select id="wt" onchange="renderW()"><option value="">全部</option><option>認購</option><option>認售</option></select></div></div>
<p class="note" id="cnt2"></p><div class="w"><table id="wtb"></table></div></section>

<section id="ta" class="card" style="display:none"><p class="note">證交所公開的本國公司申請上市清單（欄位依證交所原始資料；此資料集曾有欄位錯位，程式已自動校正，仍請以證交所官網為準）。申請上櫃與公開申購（抽籤）資訊本頁未涵蓋。</p><div class="w"><table id="atb"></table></div></section>

<div class="card"><b>資料來源狀態</b><table>__STATUS__</table></div>
</main><script>
const D=__DATA__;
const $=id=>document.getElementById(id),esc=s=>String(s==null?"":s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const nv=id=>{const v=$(id).value.trim();return v===""?null:parseFloat(v)},fx=(x,d)=>x==null?"—":x.toFixed(d);
const SC=D.scores||{},TR=D.trends||{},SER=D.series||{},HELPD=D.help||{},ETF=D.etf||{},ind=s=>s[10]||"未分類";
const fe=(s,x,d)=>x==null?(s[2]==="ETF/ETN"?"<span class='note'>不適用</span>":"—"):x.toFixed(d);
const INDS=[...new Set(D.stocks.map(ind))].sort((a,b)=>a.localeCompare(b,"zh-Hant"));
function tab(t){["s","b","i","w","a"].forEach(k=>$("t"+k).style.display=k===t?"":"none");document.querySelectorAll(".tb").forEach(b=>b.classList.toggle("on",b.dataset.t===t))}
const tr0=c=>TR[c]?TR[c][0]:0;
const SORT={vol:(a,b)=>b[6]-a[6],yld:(a,b)=>(b[8]==null?-1:b[8])-(a[8]==null?-1:a[8]),pe:(a,b)=>(a[7]==null||a[7]<=0?1e9:a[7])-(b[7]==null||b[7]<=0?1e9:b[7]),up:(a,b)=>b[5]-a[5],dn:(a,b)=>a[5]-b[5],px:(a,b)=>a[4]-b[4],
 stk:(a,b)=>tr0(b[0])-tr0(a[0]),stkd:(a,b)=>tr0(a[0])-tr0(b[0]),c5:(a,b)=>(TR[b[0]]?TR[b[0]][1]:-999)-(TR[a[0]]?TR[a[0]][1]:-999),c20:(a,b)=>(TR[b[0]]?TR[b[0]][2]:-999)-(TR[a[0]]?TR[a[0]][2]:-999),
 tot:(a,b)=>(SC[b[0]]?SC[b[0]].tot:-99)-(SC[a[0]]?SC[a[0]].tot:-99)};
$("indbox").innerHTML=INDS.map(n=>"<label><input type='checkbox' class='ind' value='"+esc(n)+"' checked onchange='render()'>"+esc(n)+"</label>").join("");
function allInd(v){document.querySelectorAll(".ind").forEach(x=>x.checked=v);render()}
function reset(){["pmax","vmin","emax","ymin","bmax","q","smin","upn","dnn"].forEach(i=>$(i).value="");$("sort").value="vol";SORTC={k:null,d:1};$("ov").value="";$("kb").value="";$("pt").value="";$("pb").value="";$("pc").checked=false;$("sc").checked=false;$("gr").checked=false;allInd(true)}
function focusInd(n){document.querySelectorAll(".ind").forEach(x=>x.checked=(x.value===n));tab("s");render()}
const li=a=>a.map(x=>"<li>"+(x[0]>0?"<span class='up'>▲</span>":x[0]<0?"<span class='dn'>▼</span>":"<span class='note'>▬</span>")+" "+esc(x[1])+"（"+(x[0]>0?"+":"")+x[0]+"）</li>").join("");
function det(c){const e=$("d_"+c);if(e)e.style.display=e.style.display==="none"?"":"none"}
function svgChart(c){const S=SER[c];if(!S)return"";
 const B=S.b,W=640,H=240,pl=8,pr=60,pt=8,pb=8,n=B.length;let mx=-1e9,mn=1e9;
 B.forEach(b=>{mx=Math.max(mx,b[1]);mn=Math.min(mn,b[2])});S.l.forEach(L=>L.p.forEach(p=>{mx=Math.max(mx,p[1]);mn=Math.min(mn,p[1])}));
 const pad=(mx-mn)*.05;mx+=pad;mn-=pad;const cw=(W-pl-pr)/n,X=i=>pl+cw*(i+.5),Y=v=>pt+(mx-v)/(mx-mn)*(H-pt-pb);
 let o="<svg viewBox='0 0 "+W+" "+H+"' style='width:100%;max-width:760px;background:var(--bg);border:1px solid var(--bd);border-radius:6px'>";
 B.forEach((b,i)=>{const col=b[3]>=b[0]?"#d6293e":"#1a9a52";o+="<line x1='"+X(i)+"' x2='"+X(i)+"' y1='"+Y(b[1])+"' y2='"+Y(b[2])+"' stroke='"+col+"'/><rect x='"+(X(i)-cw*.35)+"' width='"+cw*.7+"' y='"+Y(Math.max(b[0],b[3]))+"' height='"+Math.max(1,Math.abs(Y(b[0])-Y(b[3])))+"' fill='"+col+"'/>"});
 S.l.forEach(L=>{const q=L.p.map(p=>[X(Math.min(Math.max(p[0],0),n-1)),Y(p[1])]);o+="<polyline fill='none' stroke='#8e44ad' stroke-width='1.6' stroke-dasharray='5 3' points='"+q.map(a=>a.join(",")).join(" ")+"'/><text x='"+(W-pr+3)+"' y='"+(q[q.length-1][1]+3)+"' font-size='10' fill='#8e44ad'>"+esc(L.t)+"</text>"});
 return o+"</svg><p class='note'>紫色虛線為型態線（頸線、壓力線、支撐線等），最近 "+n+" 根日K。</p>"}
function patHtml(c,pats){let h="<p><b>圖形型態：</b>"+esc(pats.join("、"))+"</p>"+svgChart(c);
 pats.forEach(x=>{const b=x.replace(/[(].*$/,"");if(HELPD[b])h+="<p class='note'><b>"+esc(b)+"</b>："+esc(HELPD[b])+"</p>"});return h}
function etfHtml(c){const e=ETF[c];if(!e)return"";const f=(x,d)=>x==null?"—":x.toFixed(d);
 return "<p><b>ETF 資料（Yahoo）：</b>淨值 "+f(e.nav,2)+"｜折溢價 "+(e.pn==null?"—":((e.pn-1)*100).toFixed(2)+"%")+"｜規模 "+(e.aum==null?"—":(e.aum/1e8).toFixed(1)+" 億")+"｜殖利率 "+f(e.y,2)+"%｜持股本益比 "+f(e.pe,1)+"</p>"}
function detHtml(c){const k=SC[c],t=TR[c];let h=etfHtml(c);
 if(t&&t[5]&&t[5].length)h+=patHtml(c,t[5]);
 if(t)h+="<p><b>走勢：</b>"+(t[0]>0?"連續上漲 "+t[0]+" 日":t[0]<0?"連續下跌 "+(-t[0])+" 日":"無連續漲跌")+"；5日 "+t[1]+"%、20日 "+t[2]+"%"+(t[3]?"；K棒警示："+esc(t[3]):"")+"（最新K棒實體 "+t[4]+"%）</p>";
 if(!k)return h+"此檔尚無深入評價資料。";
 return h+"<b>基本面 "+k.fs+"</b><ul>"+(li(k.F)||"<li>Yahoo 無財報資料，不計分</li>")+"</ul><b>技術面 "+k.ts+"</b><ul>"+li(k.T)+"</ul><b>消息面 "+k.ns+"</b><ul>"+(li(k.N)||"<li>無新聞資料</li>")+"</ul>"+
 (k.news.length?"<b>相關新聞</b><ul>"+k.news.map(n=>"<li><a href='"+(/^https?:/.test(n[1])?esc(n[1]):"#")+"' target='_blank' rel='noopener'>"+esc(n[0])+"</a></li>").join("")+"</ul>":"")+
 "<p class='note'>近20日壓力 "+k.hi.toFixed(2)+"、支撐 "+k.lo.toFixed(2)+"；評價時間 "+esc(k.t)+"。規則式評分，不是買賣建議。</p>"}
var SORTC={k:null,d:1},HID=new Set(["kind","mkt","ns"]);
try{const j=JSON.parse(localStorage.getItem("uni_hid")||"null");if(Array.isArray(j))HID=new Set(j)}catch(e){}
const na=s=>s[2]==="ETF/ETN"?"<span class='note'>不適用</span>":"—";
const pcs=v=>v==null?"—":"<span class='"+(v>0?"up":v<0?"dn":"")+"'>"+(v>0?"+":"")+v+"</span>";
const rankOv={"偏買進":2,"觀望偏多":1,"中性":0,"觀望偏空":-1,"偏賣出":-2};
function techPct(c){const t=TR[c];if(!t)return null;let p=0;
 (t[5]||[]).forEach(x=>{const sg=x.includes("(多)")?1:x.includes("(空)")?-1:0;p+=sg*(x.includes("✓")?2:1)});
 if(t[0]>=3)p+=1;else if(t[0]<=-3)p-=1;if(t[2]!=null){if(t[2]>5)p+=1;else if(t[2]<-5)p-=1}
 const kb=t[3]||"";if(kb.includes("長紅"))p+=1;if(kb.includes("長黑"))p-=1;if(kb.includes("跳空漲"))p+=1;if(kb.includes("跳空跌"))p-=1;
 return Math.max(-100,Math.min(100,Math.round(p/6*100)))}
function tendPct(c){const k=SC[c];return k?Math.max(-100,Math.min(100,Math.round(k.tot/10*100))):null}
function badge(p,label){if(p==null)return"—";const a=(0.10+0.35*Math.abs(p)/100).toFixed(2),col=p>0?"214,41,62":p<0?"26,154,82":"130,138,155";
 return "<span class='bd' title='規則式量化傾向，不是買賣建議' style='background:rgba("+col+","+a+")'>"+(label?label+" ":"")+(p>0?"+":"")+p+"%</span>"}
const TRv=(c,i)=>TR[c]?TR[c][i]:null;
const COLS=[
 {k:"code",l:"代號",v:s=>s[0],s:1,r:(s,c)=>"<a href='https://goodinfo.tw/tw/StockDetail.asp?STOCK_ID="+c+"' target='_blank' rel='noopener' onclick='event.stopPropagation()'>"+c+"</a>"},
 {k:"name",l:"名稱",v:s=>s[1],s:1,r:s=>esc(s[1])},
 {k:"px",l:"現價",v:s=>s[4],r:s=>fx(s[4],2)},
 {k:"pct",l:"漲跌%",v:s=>s[5],r:s=>"<span class='"+(s[5]>0?"up":s[5]<0?"dn":"")+"'>"+(s[5]>0?"+":"")+s[5].toFixed(2)+"</span>"},
 {k:"vol",l:"量(張)",v:s=>s[6],r:s=>s[6].toLocaleString()},
 {k:"tend",l:"綜合傾向",v:s=>tendPct(s[0]),r:s=>badge(tendPct(s[0]),SC[s[0]]?SC[s[0]].ov:"")},
 {k:"tech",l:"技術傾向",v:s=>techPct(s[0]),r:s=>badge(techPct(s[0]))},
 {k:"stk",l:"連續",v:s=>TRv(s[0],0),r:s=>{const t=TR[s[0]];return t?(t[0]>0?"<span class='up'>▲"+t[0]+"</span>":t[0]<0?"<span class='dn'>▼"+(-t[0])+"</span>":"—"):"—"}},
 {k:"c5",l:"5日%",v:s=>TRv(s[0],1),r:s=>pcs(TRv(s[0],1))},
 {k:"c20",l:"20日%",v:s=>TRv(s[0],2),r:s=>pcs(TRv(s[0],2))},
 {k:"kb",l:"K棒警示",v:s=>TRv(s[0],3)||null,s:1,r:s=>{const t=TR[s[0]];return t&&t[3]?esc(t[3]):"—"}},
 {k:"pat",l:"圖形型態",v:s=>{const t=TR[s[0]];return t&&t[5]&&t[5].length?t[5].join("、"):null},s:1,r:s=>{const t=TR[s[0]];return t&&t[5]&&t[5].length?esc(t[5].join("、")):"—"}},
 {k:"pe",l:"本益比",v:s=>s[7],r:s=>fe(s,s[7],1)},
 {k:"yld",l:"殖利率%",v:s=>s[8],r:s=>fe(s,s[8],2)},
 {k:"pb",l:"淨值比",v:s=>s[9],r:s=>fe(s,s[9],2)},
 {k:"fs",l:"基本面",v:s=>SC[s[0]]?SC[s[0]].fs:null,r:s=>SC[s[0]]?SC[s[0]].fs:na(s)},
 {k:"ts",l:"技術面",v:s=>SC[s[0]]?SC[s[0]].ts:null,r:s=>SC[s[0]]?SC[s[0]].ts:na(s)},
 {k:"ns",l:"消息",v:s=>SC[s[0]]?SC[s[0]].ns:null,r:s=>SC[s[0]]?SC[s[0]].ns:na(s)},
 {k:"tot",l:"總分",v:s=>SC[s[0]]?SC[s[0]].tot:null,r:s=>SC[s[0]]?"<b>"+SC[s[0]].tot+"</b>":na(s)},
 {k:"ind",l:"產業",v:ind,s:1,r:s=>esc(ind(s))},
 {k:"kind",l:"類別",v:s=>s[2],s:1,r:s=>s[2]},
 {k:"mkt",l:"市場",v:s=>s[3],s:1,r:s=>s[3]}];
$("colbox").innerHTML=COLS.map(c=>"<label><input type='checkbox' class='col' value='"+c.k+"'"+(HID.has(c.k)?"":" checked")+" onchange='setCols()'>"+c.l+"</label>").join("");
function setCols(){HID=new Set([...document.querySelectorAll(".col")].filter(x=>!x.checked).map(x=>x.value));try{localStorage.setItem("uni_hid",JSON.stringify([...HID]))}catch(e){}render()}
function colPreset(m){const keep=new Set(["code","name","px","pct","vol","tend","tech","stk","pat"]);
 document.querySelectorAll(".col").forEach(x=>{x.checked=(m==="full")||keep.has(x.value)});setCols()}
function sortCol(k){if(SORTC.k!==k)SORTC={k:k,d:1};else if(SORTC.d===1)SORTC={k:k,d:-1};else SORTC={k:null,d:1};render()}
function render(){
 const pm=nv("pmax"),vm=nv("vmin"),em=nv("emax"),ym=nv("ymin"),bm=nv("bmax"),sm=nv("smin"),un=nv("upn"),dn=nv("dnn"),kb=$("kb").value,ov=$("ov").value,gr=$("gr").checked,so=$("sc").checked,q=$("q").value.trim().toLowerCase(),pt=$("pt").value,pb=$("pb").value,pc=$("pc").checked;
 const ks=[...document.querySelectorAll(".k:checked")].map(x=>x.value),ms=[...document.querySelectorAll(".m:checked")].map(x=>x.value);
 const isel=new Set([...document.querySelectorAll(".ind:checked")].map(x=>x.value)),ifilt=isel.size<INDS.length;
 const r=D.stocks.filter(s=>{const k=SC[s[0]],t=TR[s[0]],pats=(t&&t[5])||[];return ks.includes(s[2])&&ms.includes(s[3])&&(!ifilt||isel.has(ind(s)))&&(pm==null||s[4]<=pm)&&(vm==null||s[6]>=vm)&&(em==null||(s[7]!=null&&s[7]>0&&s[7]<=em))&&(ym==null||(s[8]!=null&&s[8]>=ym))&&(bm==null||(s[9]!=null&&s[9]<=bm))&&(!q||(s[0]+s[1]).toLowerCase().includes(q))
  &&(un==null||(t&&t[0]>=un))&&(dn==null||(t&&t[0]<=-dn))&&(!kb||(t&&t[3]&&(kb==="任一"||t[3].includes(kb))))&&(!pt||pats.some(x=>x.startsWith(pt)))&&(!pb||pats.some(x=>x.includes("("+pb+")")))&&(!pc||pats.some(x=>x.includes("✓")))
  &&(!so||k)&&(sm==null||(k&&k.tot>=sm))&&(!ov||(k&&k.ov===ov))&&(!gr||(k&&k.gr!=null&&k.gr>0))});
 if(SORTC.k){const c=COLS.find(x=>x.k===SORTC.k),d=SORTC.d;r.sort((a,b)=>{const x=c.v(a),y=c.v(b);if(x==null&&y==null)return 0;if(x==null)return 1;if(y==null)return -1;return (c.s?String(x).localeCompare(String(y),"zh-Hant"):x-y)*d})}
 else r.sort(SORT[$("sort").value]);
 $("cnt").textContent="符合 "+r.length+" 檔"+(r.length>300?"（僅顯示前 300 檔，請加條件縮小）":"")+"｜點欄位名稱排序（再點反向，第三次取消）｜點任一列可展開走勢與評價理由";
 const vis=COLS.filter(c=>!HID.has(c.k));
 $("st").innerHTML="<tr>"+vis.map(c=>"<th class='srt c-"+c.k+"' data-k='"+c.k+"' onclick='sortCol(this.dataset.k)'>"+c.l+(SORTC.k===c.k?(SORTC.d===1?" ▲":" ▼"):"")+"</th>").join("")+"</tr>"+
 r.slice(0,300).map(s=>{const c=esc(s[0]);return "<tr data-c='"+c+"' onclick='det(this.dataset.c)' style='cursor:pointer'>"+vis.map(x=>"<td class='c-"+x.k+"'>"+x.r(s,c)+"</td>").join("")+"</tr><tr id='d_"+c+"' style='display:none'><td colspan='"+vis.length+"' style='white-space:normal'>"+detHtml(s[0])+"</td></tr>"}).join("")}
function renderB(){const g={};D.stocks.filter(s=>s[2]==="股票").forEach(s=>{const n=ind(s),o=g[n]||(g[n]={n,c:0,p:0,u:0,d:0,v:0});o.c++;o.p+=s[5];o.v+=s[6];if(s[5]>0)o.u++;else if(s[5]<0)o.d++});
 const a=Object.values(g).map(o=>({...o,avg:o.p/o.c})).sort((x,y)=>y.avg-x.avg);
 $("btb").innerHTML="<tr><th>產業</th><th>檔數</th><th>平均漲跌%</th><th>上漲</th><th>下跌</th><th>總成交量(張)</th></tr>"+a.map(o=>"<tr><td><a href='#' onclick=\\"focusInd(this.dataset.n);return false\\" data-n='"+esc(o.n)+"'>"+esc(o.n)+"</a></td><td>"+o.c+"</td><td class='"+(o.avg>0?"up":o.avg<0?"dn":"")+"'>"+(o.avg>0?"+":"")+o.avg.toFixed(2)+"</td><td>"+o.u+"</td><td>"+o.d+"</td><td>"+o.v.toLocaleString()+"</td></tr>").join("")}
function renderI(){const I=D.inst;
 if(!I){$("inote").textContent="本次未取得法人買賣超資料（見下方資料來源狀態）。";$("itb").innerHTML="";return}
 const col=+$("iw").value,dir=$("idir").value,n=Math.max(5,+$("in").value||30),pm=nv("ip"),ic=$("ii").value,P={};
 D.stocks.forEach(s=>{P[s[0]]=s});
 const r=I.rows.filter(x=>{const s=P[x[0]];return(dir==="buy"?x[col]>0:x[col]<0)&&(pm==null||(s&&s[4]<=pm))&&(!ic||(s&&ind(s)===ic))}).sort((a,b)=>dir==="buy"?b[col]-a[col]:a[col]-b[col]);
 $("inote").textContent="資料日期："+I.date+"（證交所三大法人買賣超日報；單位：張；估算金額＝張數×現價，非官方金額）｜符合 "+r.length+" 檔";
 $("itb").innerHTML="<tr><th>排名</th><th>代號</th><th>名稱</th><th>產業</th><th>現價</th><th>漲跌%</th><th>買賣超(張)</th><th>估算金額(億)</th><th>外資</th><th>投信</th><th>自營商</th></tr>"+
 r.slice(0,n).map((x,i)=>{const s=P[x[0]],v=x[col],cl=v>0?"up":"dn",f=k=>"<td class='"+(k>0?"up":k<0?"dn":"")+"'>"+(k>0?"+":"")+k.toLocaleString()+"</td>";
  return "<tr><td>"+(i+1)+"</td><td><a href='https://goodinfo.tw/tw/StockDetail.asp?STOCK_ID="+esc(x[0])+"' target='_blank' rel='noopener'>"+esc(x[0])+"</a></td><td>"+esc(x[1])+"</td><td>"+(s?esc(ind(s)):"—")+"</td><td>"+(s?fx(s[4],2):"—")+"</td><td class='"+(s&&s[5]>0?"up":s&&s[5]<0?"dn":"")+"'>"+(s?(s[5]>0?"+":"")+s[5].toFixed(2):"—")+"</td><td class='"+cl+"'><b>"+(v>0?"+":"")+v.toLocaleString()+"</b></td><td>"+(s?(v*s[4]/1e5).toFixed(2):"—")+"</td>"+f(x[2])+f(x[3])+f(x[4])+"</tr>"}).join("")}
function renderW(){
 const q=$("q2").value.trim().toLowerCase(),t=$("wt").value;
 const r=D.warrants.filter(w=>(!t||String(w[2]).includes(t))&&(!q||w.slice(0,4).join(" ").toLowerCase().includes(q)));
 $("cnt2").textContent=D.warrants.length?("符合 "+r.length+" 檔"+(r.length>300?"（僅顯示前 300 檔，請加關鍵字）":"")):"本次未取得權證資料（見下方資料來源狀態）";
 $("wtb").innerHTML=D.warrants.length?"<tr><th>代號</th><th>簡稱</th><th>類型</th><th>標的</th><th>履約價</th><th>最後交易日</th><th>履約截止日</th></tr>"+r.slice(0,300).map(w=>"<tr>"+w.map(x=>"<td>"+esc(x)+"</td>").join("")+"</tr>").join(""):""}
function renderA(){const a=D.apply;$("atb").innerHTML=a&&a.rows.length?"<tr>"+a.cols.map(c=>"<th>"+esc(c)+"</th>").join("")+"</tr>"+a.rows.map(r=>"<tr>"+r.map(x=>"<td>"+esc(x)+"</td>").join("")+"</tr>").join(""):"<tr><td>本次未取得申請上市資料（見下方資料來源狀態）</td></tr>"}
const PN=[...new Set(Object.values(TR).flatMap(t=>(t[5]||[]).map(x=>x.replace(/[(].*$/,""))))].sort();
$("pt").innerHTML+=PN.map(n=>"<option>"+esc(n)+"</option>").join("");
const nsc=Object.keys(SC).length,ntr=Object.keys(TR).length,npat=Object.values(TR).filter(t=>Array.isArray(t[5])).length,nhit=Object.values(TR).filter(t=>Array.isArray(t[5])&&t[5].length).length;
$("sn").textContent=(nsc?("深入評價 "+nsc+" 檔（評價時間 "+(D.scored_at||"—")+"；依股價區間取成交量較大者，其餘顯示「—」）。"):"尚未取得深入評價資料。")+(ntr?("走勢統計 "+ntr+" 檔（"+(D.trends_at||"—")+"；連續漲跌依日K計算，盤中含當日最新價）。"+(D.trends_asof?"日K最新至 "+D.trends_asof+(D.trends_stale?"（⚠ 落後，可能是 Yahoo 資料延遲或尚未補上最新一日；國定假日除外）":"")+"。":"")+(npat?"圖形型態：其中 "+nhit+" 檔偵測到明確型態（"+Object.keys(SER).length+" 檔附 K 線圖）。":"⚠ 走勢統計是舊版格式、沒有圖形型態資料：請確認 stock_trends.py 與 chart_patterns.py 已更新到 v1，並重新執行。")):"尚未取得走勢統計。")+"ETF 的本益比＝持股本益比、殖利率＝近期配息殖利率、淨值比＝市價÷淨值（接近 1 代表沒有明顯折溢價），取自 Yahoo，台股 ETF 常有缺漏，缺漏顯示「ETF不適用」；ETF 不做基本面評分。評價為規則式評分，不是買賣建議。";
$("ii").innerHTML+=INDS.map(n=>"<option>"+esc(n)+"</option>").join("");
render();renderB();renderI();renderW();renderA();
var GEN=new Date("__GEN__");
function hardReload(){location.replace(location.pathname+"?t="+Date.now())}
function age(){var m=Math.round((Date.now()-GEN.getTime())/60000),el=$("stale");
 if(m>20){el.style.display="";el.innerHTML="⚠ 這份頁面是 <b>"+m+"</b> 分鐘前產生的。可能是瀏覽器暫存的舊頁面，請 <a href='#' onclick='hardReload();return false'>重新載入最新版</a>；若仍然很舊，代表 GitHub 排程尚未更新。"}else{el.style.display="none"}}
age();setInterval(age,30000);setTimeout(hardReload,300000);
document.querySelectorAll("a.nv").forEach(function(a){a.addEventListener("click",function(e){e.preventDefault();location.href=a.getAttribute("href").split("?")[0]+"?t="+Date.now()})});
</script></body></html>"""


def main():
    ap = argparse.ArgumentParser(description="全市場篩選與評價頁")
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--no-warrants", action="store_true", help="不抓權證（資料量大、平板載入較慢時可略過）")
    a = ap.parse_args()
    STATUS.clear()
    stocks = (safe("上市收盤價/ETF(證交所STOCK_DAY_ALL)", listed) or []) + (safe("上櫃收盤價(櫃買OpenAPI)", otc) or [])
    if not stocks:
        raise SystemExit("上市與上櫃資料都抓不到，請看上方錯誤訊息")
    rt = safe("證交所即時報價（個股，盤中疊加）", realtime_overlay, stocks)
    imap = {**(safe("櫃買產業別(t187ap03_O)", industries_otc) or {}), **(safe("證交所產業別(t187ap03_L)", industries) or {})}
    for r in stocks:
        r.append("ETF/ETN" if r[2] == "ETF/ETN" else "存託憑證" if r[2] == "TDR" else imap.get(r[0], "未分類"))
    inst = safe("證交所三大法人個股買賣超(T86)", institutional_by_stock)
    war = [] if a.no_warrants else (safe("上市權證基本資料(t187ap37_L)", warrants) or [])
    app = safe("申請上市公司(applylistingLocal)", applying)
    scores, scored_at, _ = load_cache(SCORES)
    trends, trends_at, tj = load_cache(TRENDS)
    etf, _, _ = load_cache(ETFC)
    for r in stocks:
        e = etf.get(r[0]) if r[2] == "ETF/ETN" else None
        if e:
            if r[7] is None:
                r[7] = e.get("pe")
            if r[8] is None:
                r[8] = e.get("y")
            if r[9] is None and e.get("nav") and r[4]:
                r[9] = round(r[4] / e["nav"], 3)
    STATUS.append(("深入評價快取(cache/scores.json)", bool(scores), f"{len(scores)} 檔" if scores else "尚無快取或讀取失敗"))
    STATUS.append(("走勢統計快取(cache/trends.json)", bool(trends), f"{len(trends)} 檔" if trends else "尚無快取或讀取失敗"))
    order = ["股票", "ETF/ETN", "特別股", "TDR", "其他"]
    kinds = sorted({s[2] for s in stocks}, key=lambda k: order.index(k) if k in order else 9)
    kh = "".join(f'<label><input type="checkbox" class="k" value="{k}" {"checked" if k in ("股票", "ETF/ETN") else ""} onchange="render()"> {k}</label>' for k in kinds)
    st = "".join(f'<tr><td>{html.escape(n)}</td><td>{"成功" if ok else "失敗"}</td><td>{html.escape(m)}</td></tr>' for n, ok, m in STATUS)
    data = json.dumps({"stocks": stocks, "warrants": war, "apply": app, "scores": scores, "scored_at": scored_at,
                       "trends": trends, "trends_at": trends_at, "inst": inst,
                       "series": tj.get("series", {}), "etf": etf,
                       "trends_asof": tj.get("asof", ""), "trends_stale": bool(tj.get("asof")) and tj.get("asof") < expected_last_day().isoformat(), "help": {k: v for k, v in HELP.items()}, "pattern_tail": TAIL}, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    src = f"證交所即時（{rt[1]}，覆蓋 {rt[0]}/{rt[2]} 檔，其餘為最近收盤）" if rt else "最近一個交易日的收盤價（即時報價抓不到或非開盤時段）"
    page = (PAGE.replace("__DATA__", data).replace("__KINDS__", kh).replace("__STATUS__", st)
            .replace("__NOW__", f"{dt.datetime.now():%Y-%m-%d %H:%M}").replace("__GEN__", dt.datetime.now().astimezone().isoformat(timespec="seconds")).replace("__DATE__", html.escape(src)))
    OUT_FILE.write_text(page, encoding="utf-8")
    print(f"股票/ETF {len(stocks)} 檔、權證 {len(war)} 檔、申請上市 {len(app['rows']) if app else 0} 家、評價 {len(scores)} 檔、走勢 {len(trends)} 檔 → {OUT_FILE}")
    for n, ok, m in STATUS:
        if not ok:
            print(f"  ✗ {n}: {m}")
    if not a.no_open:
        webbrowser.open(OUT_FILE.as_uri())


if __name__ == "__main__":
    main()
