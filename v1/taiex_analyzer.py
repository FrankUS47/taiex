# -*- coding: utf-8 -*-
"""
台股大盤 K 線與隔日行情分析（全部使用真實資料，抓不到就標示失敗，不會用模擬數據）

資料來源：
  - Yahoo Finance(yfinance)：加權指數歷史、美股指數、費半、VIX、台積電ADR、亞股、匯率、美元指數、美債殖利率、原油、黃金
  - 證交所 TWSE：盤中即時加權指數、三大法人買賣超
  - 期交所 TAIFEX OpenAPI：台指期(TX)近月日盤/盤後行情
  - Google News RSS：台股、Fed、半導體、美中、地緣政治、匯率等新聞標題

用法：
  python taiex_analyzer.py              # 抓一次，產生 taiex_report.html 並開啟瀏覽器
  python taiex_analyzer.py --watch 60   # 每 60 秒自動更新（網頁也會自動重新整理）
  python taiex_analyzer.py --no-open    # 不自動開啟瀏覽器

注意：僅供研究參考，不構成投資建議。
"""
import argparse
import datetime as dt
import html
import json
import textwrap
import re
import time
import webbrowser
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots

import chart_patterns as CP
import predictor as PR

UA = {"User-Agent": "Mozilla/5.0 (compatible; TaiexAnalyzer/1.0)"}
UP, DN = "#d6293e", "#1a9a52"          # 台股習慣：紅漲綠跌
MA_COLORS = ["#e0a800", "#2b6cb0", "#9b59b6", "#e67e22"]
OUT_FILE = Path(__file__).with_name("taiex_report.html")
CACHE_DIR = Path(__file__).with_name("cache")

MARKETS = {  # 名稱: Yahoo 代號
    "S&P500": "^GSPC", "道瓊": "^DJI", "那斯達克": "^IXIC", "費城半導體": "^SOX",
    "VIX恐慌指數": "^VIX", "台積電ADR": "TSM",
    "日經225": "^N225", "韓國KOSPI": "^KS11", "恆生指數": "^HSI",
    "美元/台幣": "TWD=X", "美元指數": "DX-Y.NYB", "美國10年債殖利率": "^TNX",
    "WTI原油": "CL=F", "黃金": "GC=F",
    "S&P500期貨": "ES=F", "那斯達克期貨": "NQ=F", "道瓊期貨": "YM=F", "EWT台灣ETF(美股)": "EWT",
    "德國DAX": "^GDAXI", "英國富時100": "^FTSE", "日圓/美元": "JPY=X", "歐元/美元": "EURUSD=X", "人民幣/美元(離岸)": "CNH=X",
}
NEWS_TOPICS = {
    "台股": "台股 大盤",
    "Fed與利率": "Fed 利率 通膨",
    "半導體與AI": "半導體 輝達 台積電",
    "美中關係": "美中 關稅 出口管制",
    "地緣與油價": "中東 俄烏 油價",
    "匯率與外資": "新台幣 外資",
    "國安基金與官股": "國安基金 護盤 官股",
}
POS_WORDS = ["大漲", "創高", "利多", "降息", "反彈", "買超", "樂觀", "上修", "突破", "回升", "轉強", "收紅", "強勁"]
NEG_WORDS = ["大跌", "重挫", "利空", "升息", "崩", "賣超", "衰退", "下修", "制裁", "戰爭", "衝突", "升溫", "恐慌", "轉弱", "破底", "收黑", "暴跌"]

STATUS = []  # (來源, 是否成功, 訊息)
NAV = ('<p style="font-size:15px">📍 <a class="nv" href="taiex_report.html">大盤分析</a>｜<a class="nv" href="universe.html"><b>🔎 全市場篩選與評價（自訂條件）</b></a></p>')
NAVJS = ("<script>document.querySelectorAll('a.nv').forEach(function(a){a.addEventListener('click',function(e){e.preventDefault();"
         "location.href=a.getAttribute('href').split('?')[0]+'?t='+Date.now()})})</script>")


def safe(name, fn, *a, **k):
    """呼叫資料來源；失敗時記錄原因並回傳 None（絕不捏造資料）。"""
    try:
        r = fn(*a, **k)
        STATUS.append((name, True, "OK"))
        return r
    except Exception as e:  # noqa
        STATUS.append((name, False, f"{type(e).__name__}: {str(e)[:140]}"))
        return None


def num(x):
    s = re.sub(r"[^\d.\-]", "", str(x))
    try:
        return float(s)
    except ValueError:  # 例如 '---'、'-'、空字串
        return None


# ───────────────────────── 資料抓取 ─────────────────────────
def hist(sym, period):
    df = yf.Ticker(sym).history(period=period, auto_adjust=False)
    if df is None or df.empty:
        raise RuntimeError("Yahoo 回傳空資料")
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    return df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])


def quote(sym):
    df = hist(sym, "1mo")
    c = df["Close"]
    return {"last": float(c.iloc[-1]), "pct": float((c.iloc[-1] / c.iloc[-2] - 1) * 100),
            "date": df.index[-1].date()}


def twse_realtime():
    s = requests.Session()
    s.headers.update(UA)
    s.get("https://mis.twse.com.tw/stock/index.jsp", timeout=10)  # 取得 session cookie
    r = s.get("https://mis.twse.com.tw/stock/api/getStockInfo.jsp",
              params={"ex_ch": "tse_t00.tw", "json": "1", "delay": "0", "_": int(time.time() * 1000)}, timeout=10)
    m = r.json()["msgArray"][0]
    y = num(m.get("y"))
    z = num(m.get("z"))
    if z is None or y is None:
        raise RuntimeError("目前無即時成交價（可能非開盤時段）")
    return {"price": z, "prev": y, "pct": (z / y - 1) * 100, "time": m.get("t"), "date": m.get("d")}


def institutional():
    d = dt.date.today()
    for _ in range(7):
        if d.weekday() < 5:
            j = requests.get("https://www.twse.com.tw/rwd/zh/fund/BFI82U",
                             params={"date": d.strftime("%Y%m%d"), "type": "day", "response": "json"},
                             headers=UA, timeout=10).json()
            if j.get("stat") == "OK" and j.get("data"):
                out = {"date": d}
                for row in j["data"]:
                    nm, val = row[0], num(row[3])
                    if val is None:
                        continue
                    val /= 1e8  # 元 → 億元
                    if nm.startswith("外資及陸資") and "外資" not in out:
                        out["外資"] = val
                    elif nm.startswith("投信"):
                        out["投信"] = val
                    elif nm.startswith("合計"):
                        out["合計"] = val
                if "外資" in out:
                    return out
        d -= dt.timedelta(days=1)
    raise RuntimeError("近 7 天查無三大法人資料")


def taifex():
    rows = requests.get("https://openapi.taifex.com.tw/v1/DailyMarketReportFut", headers=UA, timeout=15).json()

    def g(d, *ks):
        for k in ks:
            if d.get(k) not in (None, "", "-"):
                return d[k]

    tx = [x for x in rows if str(g(x, "Contract", "契約") or "").strip() in ("TX", "臺股期貨")]
    if not tx:
        raise RuntimeError(f"找不到 TX 契約；欄位範例：{list(rows[0].keys()) if rows else '空資料'}")
    month = lambda x: str(g(x, "ContractMonth(Week)", "ContractMonth", "到期月份(週別)", "到期月份") or "")
    tx = [x for x in tx if month(x).isdigit()]  # 排除價差單與週契約
    if not tx:
        raise RuntimeError("找不到月份契約")
    near = min(month(x) for x in tx)
    out = {"contract": near}
    for x in tx:
        if month(x) != near:
            continue
        last, chg = num(g(x, "Last", "最後成交價")), num(g(x, "Change", "漲跌價"))
        if last is None:
            continue
        sess = str(g(x, "TradingSession", "交易時段") or "")
        key = "night" if ("盤後" in sess or "after" in sess.lower()) else "day"
        out[key] = {"last": last, "chg": chg or 0.0, "pct": (chg or 0.0) / (last - (chg or 0.0)) * 100,
                    "date": str(g(x, "Date", "日期") or "")}
    if "day" not in out and "night" not in out:
        raise RuntimeError("契約無有效成交價")
    return out


def news(query, n=6, when=True):
    r = requests.get("https://news.google.com/rss/search",
                     params={"q": query + (" when:2d" if when else ""), "hl": "zh-TW", "gl": "TW", "ceid": "TW:zh-Hant"},
                     headers=UA, timeout=12)
    items = []
    for it in ET.fromstring(r.content).iter("item"):
        items.append({"title": it.findtext("title", ""), "link": it.findtext("link", ""),
                      "date": it.findtext("pubDate", "")[:22], "src": it.findtext("source", "")})
        if len(items) >= n:
            break
    if not items:
        raise RuntimeError("無新聞")
    return items


# ───────────────────────── 技術分析 ─────────────────────────
def tech(df):
    c, last, R = df["Close"], float(df["Close"].iloc[-1]), []
    add = lambda p, t: R.append((p, t))
    m = {n: c.rolling(n).mean() for n in (5, 20, 60, 240)}
    for n, nm in ((20, "月線"), (60, "季線"), (240, "年線")):
        v = m[n].iloc[-1]
        if pd.notna(v):
            add(1 if last > v else -1, f"收盤 {last:,.0f} {'站上' if last > v else '跌破'}{nm}(MA{n} {v:,.0f})")
    if pd.notna(m[60].iloc[-1]):
        a, b, d = m[5].iloc[-1], m[20].iloc[-1], m[60].iloc[-1]
        add(2, "均線多頭排列(5>20>60)") if a > b > d else add(-2, "均線空頭排列(5<20<60)") if a < b < d else add(0, "均線糾結，方向未明")
    for i in range(-5, 0):
        a0, a1, b0, b1 = m[5].iloc[i - 1], m[5].iloc[i], m[20].iloc[i - 1], m[20].iloc[i]
        if a0 <= b0 and a1 > b1:
            add(1, "近5日 MA5 上穿 MA20（黃金交叉）")
        if a0 >= b0 and a1 < b1:
            add(-1, "近5日 MA5 下穿 MA20（死亡交叉）")
    if pd.notna(m[20].iloc[-1]):
        bias = (last / m[20].iloc[-1] - 1) * 100
        if bias > 8:
            add(-1, f"月線乖離 {bias:.1f}%，短線過熱")
        elif bias < -8:
            add(1, f"月線乖離 {bias:.1f}%，超跌")
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    r = (100 - 100 / (1 + up / dn.replace(0, np.nan))).iloc[-1]
    if pd.notna(r):
        add(-1 if r > 70 else 1 if r < 30 else 0, f"RSI(14) {r:.0f}" + ("：超買" if r > 70 else "：超賣" if r < 30 else "：中性"))
    dif = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    hh = dif - dif.ewm(span=9, adjust=False).mean()
    add(1 if hh.iloc[-1] > 0 else -1, f"MACD 柱狀體 {hh.iloc[-1]:.1f}，動能{'擴大' if abs(hh.iloc[-1]) > abs(hh.iloc[-2]) else '收斂'}")
    lo9, hi9 = df["Low"].rolling(9).min(), df["High"].rolling(9).max()
    rsv = ((c - lo9) / (hi9 - lo9).replace(0, np.nan) * 100).fillna(50)
    K = D = 50.0
    for v in rsv.iloc[8:]:
        K = K * 2 / 3 + v / 3
        D = D * 2 / 3 + K / 3
    add((0 if K > 80 else 1) if K > D else (0 if K < 20 else -1),
        f"KD(9) K={K:.0f} D={D:.0f}" + ("，高檔鈍化風險" if K > 80 else "，低檔留意反彈" if K < 20 else "，K>D 偏多" if K > D else "，K<D 偏空"))
    v = df["Volume"]
    va = v.iloc[-6:-1].mean()
    if va and va > 0 and v.iloc[-1] > va * 1.3:
        up_day = last > c.iloc[-2]
        add(1 if up_day else -1, f"量能放大({v.iloc[-1] / va:.1f}倍5日均量)，{'價漲量增' if up_day else '價跌量增，賣壓偏重'}")
    v5, vp = v.iloc[-5:].mean(), v.iloc[-10:-5].mean()
    pc5 = c.iloc[-1] / c.iloc[-6] - 1
    if v5 > 0 and vp > 0:
        vr = v5 / vp
        if pc5 > 0.01:
            add(1, f"量價配合：近5日價漲量增（量為前5日 {vr:.2f} 倍）") if vr > 1.15 else add(-1, f"量價背離：近5日價漲量縮（量為前5日 {vr:.2f} 倍），上攻力道待確認") if vr < 0.85 else None
        elif pc5 < -0.01:
            add(-1, f"近5日價跌量增（量為前5日 {vr:.2f} 倍），賣壓偏重") if vr > 1.15 else add(0, f"近5日價跌量縮（量為前5日 {vr:.2f} 倍），賣壓有減輕跡象") if vr < 0.85 else None
    info = {"last": last, "pct": (last / c.iloc[-2] - 1) * 100, "date": df.index[-1].date(),
            "hi": float(df["High"].tail(20).max()), "lo": float(df["Low"].tail(20).min())}
    return R, info


def sc(x, h):  # 以門檻 h 評分 -2..+2
    return 2 if x > h else 1 if x > 0 else -2 if x < -h else -1 if x < 0 else 0


def macro_score(Q, rt, inst, fut, news_data, last_date):
    R = []
    add = lambda p, t: R.append((p, t))
    g = lambda k: Q.get(k)
    if fut and "night" in fut:
        add(sc(fut["night"]["pct"], 0.5), f"台指期盤後 {fut['night']['last']:,.0f}（{fut['night']['pct']:+.2f}%）")
    if fut and "day" in fut and fut["day"]["date"].replace("-", "") == last_date.strftime("%Y%m%d"):
        b = fut["day"]["last"] - float(HIST_LAST_CLOSE)
        add(1 if b > 0 else -1, f"期現貨價差 {b:+.0f} 點（{'正價差偏多' if b > 0 else '逆價差偏空'}）")
    fu = [g(k)["pct"] for k in ("S&P500期貨", "那斯達克期貨") if g(k)]
    if fu:
        add(sc(float(np.mean(fu)), 0.5), f"美股期貨(ES/NQ)平均 {np.mean(fu):+.2f}%（美股收盤後仍近24小時交易，晚間最即時）")
    if g("EWT台灣ETF(美股)"):
        add(sc(g("EWT台灣ETF(美股)")["pct"], 1), f"EWT(美股掛牌的台灣ETF) {g('EWT台灣ETF(美股)')['pct']:+.2f}%")
    eu = [g(k)["pct"] for k in ("德國DAX", "英國富時100") if g(k)]
    if eu:
        add(sc(float(np.mean(eu)), 0.7), f"歐股(德英)平均 {np.mean(eu):+.2f}%")
    if g("費城半導體"):
        add(sc(g("費城半導體")["pct"], 1) * 2, f"費半 {g('費城半導體')['pct']:+.2f}%（權重加倍）")
    if g("台積電ADR"):
        add(sc(g("台積電ADR")["pct"], 1), f"台積電ADR {g('台積電ADR')['pct']:+.2f}%")
    us = [g(k)["pct"] for k in ("S&P500", "道瓊", "那斯達克") if g(k)]
    if us:
        add(sc(float(np.mean(us)), 0.5), f"美股三大指數平均 {np.mean(us):+.2f}%")
    if g("VIX恐慌指數"):
        v = g("VIX恐慌指數")["last"]
        add(-2 if v > 25 else -1 if v > 20 else 1 if v < 15 else 0, f"VIX {v:.1f}")
    asia = [g(k)["pct"] for k in ("日經225", "韓國KOSPI", "恆生指數") if g(k)]
    if asia:
        add(sc(float(np.mean(asia)), 0.7), f"亞股(日韓港)平均 {np.mean(asia):+.2f}%")
    if g("美元/台幣"):
        p = g("美元/台幣")["pct"]
        add(-1 if p > 0.3 else 1 if p < -0.3 else 0, f"美元/台幣 {g('美元/台幣')['last']:.3f}（{p:+.2f}%，{'台幣貶值，外資偏流出' if p > 0.3 else '台幣升值，利外資' if p < -0.3 else '匯率平穩'}）")
    if g("美元指數"):
        p = g("美元指數")["pct"]
        add(-1 if p > 0.5 else 1 if p < -0.5 else 0, f"美元指數 {p:+.2f}%")
    if g("美國10年債殖利率"):
        p = g("美國10年債殖利率")["pct"]
        add(-1 if p > 2 else 1 if p < -2 else 0, f"美債10年殖利率 {g('美國10年債殖利率')['last']:.2f}（日變動 {p:+.1f}%）")
    if g("WTI原油") and g("WTI原油")["pct"] > 3:
        add(-1, f"油價大漲 {g('WTI原油')['pct']:+.1f}%，通膨與成本壓力")
    if inst:
        add(sc(inst["外資"], 50), f"外資買賣超 {inst['外資']:+.1f} 億（{inst['date']}）")
        if "投信" in inst:
            add(sc(inst["投信"], 5), f"投信買賣超 {inst['投信']:+.1f} 億")
    ns = 0
    seen = set()
    for items in (news_data or {}).values():
        for it in items or []:
            if it["title"] in seen:
                continue
            seen.add(it["title"])
            ns += sum(w in it["title"] for w in POS_WORDS) - sum(w in it["title"] for w in NEG_WORDS)
    N = [(max(-1, min(1, round(ns / 5))), f"新聞標題關鍵字粗略情緒 {ns:+d}（僅為參考，易誤判）")] if seen else []
    return R, N


# ───────────────────────── 圖表 ─────────────────────────
def resample(df, rule):
    """週K、月K。每根K棒的日期標在該期間「最後一個實際有資料的交易日」，
    不用期間結束日（否則本週會被標成還沒到的週五、本月會被標成月底）。"""
    agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}

    def go(rl):
        r = df.resample(rl).agg(agg).dropna()
        last_day = df.index.to_series().resample(rl).last()
        r.index = pd.DatetimeIndex(last_day.reindex(r.index).to_numpy())
        return r

    try:
        return go(rule)
    except ValueError:
        return go(rule.replace("ME", "M"))


RANGE_JS = r"""
(function(){var gd=document.getElementById('{plot_id}');if(!gd)return;
function T(x){return new Date(String(x).replace(' ','T')).getTime()}
function fit(a,b){var c=gd.data[0],lo=1e18,hi=-1e18,vm=0,bar=null,i;
 for(i=0;i<gd.data.length;i++){if(gd.data[i].type==='bar'){bar=gd.data[i];break}}
 for(i=0;i<c.x.length;i++){var t=T(c.x[i]);if((a==null||t>=a)&&(b==null||t<=b)){if(c.low[i]<lo)lo=c.low[i];if(c.high[i]>hi)hi=c.high[i];if(bar&&bar.y[i]>vm)vm=bar.y[i]}}
 if(lo>hi)return;var p=(hi-lo)*0.05||1;Plotly.relayout(gd,{'yaxis.range':[lo-p,hi+p],'yaxis2.range':[0,(vm||1)*1.15]})}
gd.on('plotly_relayout',function(ev){var r0=null,r1=null,k;
 for(k in ev){if(/^xaxis2?\.range\[0\]$/.test(k))r0=ev[k];if(/^xaxis2?\.range\[1\]$/.test(k))r1=ev[k];if(/^xaxis2?\.range$/.test(k)){r0=ev[k][0];r1=ev[k][1]}}
 if(r0!=null&&r1!=null){fit(T(r0),T(r1))}else if(ev['xaxis.autorange']||ev['xaxis2.autorange']){fit(null,null)}});
gd.fitRange=fit;})();
"""
MA_ALL = ["#e0a800", "#2b6cb0", "#9b59b6", "#e67e22", "#16a085", "#7f8c8d"]


def chart(df, title, mas, names, show, daily=False, first=False, overlays=None, visible=None, buttons=None, intraday=False, marks=None):
    """K 線圖：預設顯示最近 show 根，但保留全部歷史，可用上方按鈕、下方滑桿、拖曳縮放調整時間範圍；
    Y 軸會隨可見範圍自動縮放；圖例點一下可顯示或隱藏均線。"""
    d = df.copy()
    for n, nm in zip(mas, names):
        d[nm] = d["Close"].rolling(n).mean()
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.76, 0.24], vertical_spacing=0.03)
    fig.add_trace(go.Candlestick(x=d.index, open=d["Open"], high=d["High"], low=d["Low"], close=d["Close"], name="K線",
                                 increasing_line_color=UP, increasing_fillcolor=UP,
                                 decreasing_line_color=DN, decreasing_fillcolor=DN), 1, 1)
    for k, nm in enumerate(names):
        on = visible is None or nm in visible
        fig.add_trace(go.Scatter(x=d.index, y=d[nm], name=nm, visible=True if on else "legendonly",
                                 line=dict(width=1.5, color=MA_ALL[k % len(MA_ALL)])), 1, 1)
    fig.add_trace(go.Bar(x=d.index, y=d["Volume"], name="成交量",
                         marker_color=[UP if c >= o else DN for o, c in zip(d["Open"], d["Close"])]), 2, 1)
    for pt in overlays or []:
        for ln in pt["lines"]:
            xs = [df.index[min(max(i, 0), len(df) - 1)] for i, _ in ln["pts"]]
            fig.add_trace(go.Scatter(x=xs, y=[y for _, y in ln["pts"]], mode="lines", name=f'{pt["name"]}·{ln["label"]}',
                                     line=dict(width=2, dash="dash", color="#8e44ad")), 1, 1)
    if marks:
        fig.add_trace(go.Scatter(x=[m["date"] for m in marks], y=[m["y"] for m in marks], mode="markers", name="特別長K棒說明",
                                 marker=dict(symbol="diamond", size=11, color=[m["color"] for m in marks], line=dict(width=1, color="#333")),
                                 text=[m["text"] for m in marks], hovertemplate="%{text}<extra>特別長K棒</extra>"), 1, 1)
    vis = d.tail(show)
    lo, hi = float(vis["Low"].min()), float(vis["High"].max())
    pad = (hi - lo) * 0.05 or 1.0
    vmax = float(vis["Volume"].max() or 1)
    x0, x1 = vis.index[0], vis.index[-1]
    span = (x1 - x0) * 0.02 if x1 > x0 else pd.Timedelta(days=1)
    btns = [dict(count=c, label=l, step=st, stepmode="backward") for c, st, l in (buttons or [])] + [dict(step="all", label="全部")]
    fig.update_layout(title=title, height=700, template="plotly_white", hovermode="x unified",
                      legend=dict(orientation="h"), margin=dict(l=50, r=20, t=60, b=30))
    fig.update_xaxes(range=[x0, x1 + span])
    fig.update_xaxes(rangeslider_visible=False, row=1, col=1)
    fig.update_xaxes(rangeslider=dict(visible=True, thickness=0.05), rangeselector=dict(buttons=btns), row=2, col=1)
    fig.update_yaxes(range=[lo - pad, hi + pad], row=1, col=1)
    fig.update_yaxes(range=[0, vmax * 1.15], row=2, col=1)
    if intraday:
        fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"]), dict(bounds=[13.5, 9], pattern="hour")])
    elif daily:
        fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])])
    h = fig.to_html(full_html=False, include_plotlyjs="cdn" if first else False, post_script=RANGE_JS)
    return f'<div class="kc{" intra" if intraday else ""}" data-x0="{x0}" data-x1="{x1 + span}">{h}</div>'


def intraday5():
    """加權指數 5 分鐘K（近 5 個交易日）；用於「當日」走勢。"""
    h = yf.Ticker("^TWII").history(period="5d", interval="5m")
    if h is None or h.empty:
        raise RuntimeError("Yahoo 無 5 分鐘資料（非開盤時段或資料源限制）")
    h.index = pd.to_datetime(h.index).tz_localize(None)
    return h[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])


# ───────────────────────── 報告 ─────────────────────────
CSS = """body{font:15px/1.6 system-ui,'Noto Sans TC','Microsoft JhengHei',sans-serif;margin:0;background:#f6f7f9;color:#1d2330}
main{max-width:1050px;margin:0 auto;padding:16px}.card{background:#fff;border:1px solid #dde1e8;border-radius:10px;padding:14px;margin:14px 0}
h1{font-size:22px}h2{font-size:17px;margin:0 0 8px}table{border-collapse:collapse;width:100%;font-size:14px}th,td{border-bottom:1px solid #eee;padding:5px 8px;text-align:left}
.up{color:#d6293e}.dn{color:#1a9a52}.big{font-size:20px;font-weight:700}.note{font-size:13px;color:#6b7385}.bad{color:#b45309}li{margin:3px 0}a{color:#2b6cb0}"""


def cls(x):
    return "up" if x > 0 else "dn" if x < 0 else ""


def li(R):
    tri = lambda p: '<span class="up">▲</span>' if p > 0 else '<span class="dn">▼</span>' if p < 0 else '<span class="note">▬</span>'
    return "".join(f"<li>{tri(p)} {html.escape(t)}（{p:+d}）</li>" for p, t in R)


LIVE_HTML = """<div class="card" id="livecard"><div id="stale" style="display:none;background:#fff3cd;border:1px solid #e0a800;border-radius:6px;padding:8px;margin-bottom:8px"></div>
<b>⚡ 瀏覽器即時報價</b> <span class="note">（打開本頁時由你的瀏覽器直接向 Yahoo 取得；若來源網站不允許，會顯示無法連線，並以上方報告產生時的資料為準）</span>
<div id="liveq" class="note" style="margin-top:6px">載入中…</div>
<button onclick="hardReload()">重新載入最新版</button></div>
<script>
var GEN=new Date("__GEN__"),REFRESH=__REFRESH__;
function hardReload(){location.replace(location.pathname+"?t="+Date.now())}
function age(){var m=Math.round((Date.now()-GEN.getTime())/60000),el=document.getElementById("stale");
 if(m>20){el.style.display="";el.innerHTML="⚠ 這份報告是 <b>"+m+"</b> 分鐘前產生的（"+GEN.toLocaleString()+"）。可能是瀏覽器暫存的舊頁面，請按「重新載入最新版」；若仍然很舊，代表 GitHub 排程尚未更新（排程可能延遲）。"}else{el.style.display="none"}}
age();setInterval(age,30000);if(REFRESH>0)setTimeout(hardReload,REFRESH*1000);
var SYMS=[["^TWII","加權指數"],["TSM","台積電ADR"],["EWT","EWT台灣ETF"],["ES=F","S&P500期貨"],["NQ=F","那斯達克期貨"],["TWD=X","美元/台幣"]];
function one(s){return fetch("https://query1.finance.yahoo.com/v8/finance/chart/"+encodeURIComponent(s[0])+"?interval=1m&range=1d").then(function(r){if(!r.ok)throw 0;return r.json()}).then(function(j){var m=j.chart.result[0].meta,p=m.regularMarketPrice,y=m.chartPreviousClose,c=p-y,k=c/y*100;
 return "<span style='margin-right:14px'>"+s[1]+" <b>"+p.toLocaleString(undefined,{maximumFractionDigits:2})+"</b> <span class='"+(c>0?"up":c<0?"dn":"")+"'>"+(c>0?"+":"")+c.toFixed(2)+"（"+(k>0?"+":"")+k.toFixed(2)+"%）</span> <span class='note'>"+new Date(m.regularMarketTime*1000).toLocaleTimeString()+"</span></span>"})}
function live(){Promise.all(SYMS.map(function(s){return one(s).catch(function(){return null})})).then(function(a){var ok=a.filter(Boolean);
 document.getElementById("liveq").innerHTML=ok.length?ok.join("")+"<div class='note'>來源：Yahoo（可能延遲）。每 20 秒更新。</div>":"瀏覽器無法直連即時報價（來源網站限制或網路問題）。請參考報告產生時的資料，或在自己的電腦執行 live_server.py 取得即時報價與五檔。"})}
live();setInterval(live,20000);
</script>"""
RANGE_HTML = """<div class="note" style="margin:6px 0">時間範圍：<input type="date" id="rg1"> ～ <input type="date" id="rg2"> <button onclick="applyRange()">套用</button>
　或最近 <input type="number" id="rgn" value="60" min="5" style="width:70px"> 根K線 <button onclick="lastN()">套用</button> <button onclick="resetRange()">恢復預設</button><br>
每張圖上方有 5日／1個月／3個月／1年／全部 按鈕，下方的滑桿可拖曳調整範圍；圖例點一下可顯示或隱藏均線（日K另有 10日、半年線 MA120）。「當日走勢」是 5 分鐘K，預設顯示最近一個交易日。</div>
<script>
function kcs(){return Array.prototype.slice.call(document.querySelectorAll('.kc:not(.intra) .plotly-graph-div'))}
function applyRange(){var s=document.getElementById('rg1').value,e=document.getElementById('rg2').value;if(!s||!e)return;kcs().forEach(function(g){Plotly.relayout(g,{'xaxis.range':[s,e],'xaxis2.range':[s,e]})})}
function lastN(){var n=+document.getElementById('rgn').value||60;kcs().forEach(function(g){var x=g.data[0].x,s=x[Math.max(0,x.length-n)],e=x[x.length-1];Plotly.relayout(g,{'xaxis.range':[s,e],'xaxis2.range':[s,e]})})}
function resetRange(){document.querySelectorAll('.kc').forEach(function(w){var g=w.querySelector('.plotly-graph-div');if(!g)return;var a=w.dataset.x0,b=w.dataset.x1;Plotly.relayout(g,{'xaxis.range':[a,b],'xaxis2.range':[a,b]})})}
</script>"""


def pred_card(d, w, entry, weighted, now, pc, cur_today, tot):
    """預測追蹤與自動調整卡片。"""
    wst = PR.weights(d)[1]
    rows = ""
    for k in PR.COMPS:
        hit = wst[k]["hit"]
        rows += f'<tr><td>{k}</td><td>{w[k]:.2f}</td><td>{"—" if hit is None else format(hit * 100, ".0f") + "%"}</td><td>{wst[k]["n"]}</td></tr>'
    done = [e for e in d["entries"] if e.get("actual_pct") is not None][-10:][::-1]
    hist = "".join(
        f'<tr><td>{e["for_date"]}</td><td>{e["label"]}（{e.get("weighted", e["raw"]):+.1f}）</td><td class="{cls(e["actual_pct"])}">{e["actual_pct"]:+.2f}%</td>'
        f'<td>{"—" if abs(e.get("weighted", e["raw"])) < 2 else ("命中" if (e.get("weighted", e["raw"]) > 0) == (e["actual_pct"] > 0) else "未命中")}</td></tr>' for e in done)
    live = ""
    tgt = PR.target_date(now)
    if cur_today and tgt == now.date() and PR.session_fraction(now) > 0 and entry:
        adj, wl, lp = PR.live_adjust(entry["weighted"], pc, now)
        live = (f'<p><b>盤中修正：</b>盤前預判 {entry["label"]}（加權 {entry["weighted"]:+.1f}）；目前盤中 <span class="{cls(pc)}">{pc:+.2f}%</span>（相當 {lp:+.1f} 分）；'
                f'依已開盤時間比例混合（實際走勢占 {wl * 100:.0f}%）→ <b>修正後 {PR.label_of(adj)}（{adj:+.1f}）</b>。</p>')
    _, _, info = PR.weights(d)
    return ('<div class="card"><h2>預測追蹤與自動調整</h2>'
            f'<p>目標交易日 <b>{tgt}</b>：原始總分 {tot:+d}，依各構面權重加權後 <b>{weighted:+.1f}</b> → <b>{PR.label_of(weighted)}</b>。'
            f'已結算 {info["done"]} 天；方向性預判（|分數|≥2）{info["calls"]} 次，命中 {info["ok"]} 次。</p>{live}'
            '<table><tr><th>構面</th><th>目前權重</th><th>方向命中率</th><th>樣本數</th></tr>' + rows + '</table>'
            + ('<h3 style="font-size:15px;margin:10px 0 4px">最近已結算的預判（預判＝目標日開盤前最後一次）</h3><table><tr><th>日期</th><th>預判</th><th>實際漲跌</th><th>結果</th></tr>' + hist + '</table>' if hist else
               '<p class="note">還沒有已結算的預判。每個目標交易日收盤後（約 13:45 之後）才會用實際漲跌結算並調整權重。</p>')
            + '<p class="note">自動調整方式：每次產生報告都記錄當時五個構面的分數；目標日收盤後比對實際漲跌，算出各構面的方向命中率，'
              '權重 = 1 ＋（2×命中率−1，限制在 0.5～1.5）×（樣本數/20 最多 1），樣本少時幾乎維持 1.0。盤中則把盤前預判與目前實際漲跌依已開盤時間比例混合。'
              '這只是依自己歷史命中率做的簡單校準，樣本少時不具統計意義，不保證越調越準；刪除 cache/predictions.json 即重置。</p></div>')


def yahoo_intraday():
    """證交所即時資料抓不到時的備援：Yahoo 加權指數 1 分鐘資料（可能延遲）。"""
    h = yf.Ticker("^TWII").history(period="1d", interval="1m")
    if h is None or h.empty:
        raise RuntimeError("Yahoo 無盤中分鐘資料（非開盤時段或資料源延遲）")
    ts = h.index[-1]
    return {"price": float(h["Close"].iloc[-1]), "time": ts.strftime("%Y-%m-%d %H:%M"), "date": ts.date()}


def current_quote(df, rt, yi):
    """目前大盤報價：證交所即時 → Yahoo 分鐘 → 最新日K，依序備援。"""
    c = df["Close"]
    if rt:
        return {"price": rt["price"], "prev": rt["prev"], "src": "證交所即時", "time": f'{rt["date"]} {rt["time"]}'}
    if yi:
        prev = float(c.iloc[-2]) if (yi["date"] == df.index[-1].date() and len(c) > 1) else float(c.iloc[-1])
        return {"price": yi["price"], "prev": prev, "src": "Yahoo 盤中分鐘（可能延遲）", "time": yi["time"]}
    return {"price": float(c.iloc[-1]), "prev": float(c.iloc[-2]), "src": "最新日K收盤", "time": str(df.index[-1].date())}


def fill_from_5m(df, d5):
    """日K落後時，用 Yahoo 近 5 日的 5 分鐘K彙整成日K補上（含盤中尚未收盤的當日K）。回傳 (df, 補了幾根)。"""
    last = df.index[-1]
    rows = []
    for day, x in d5.groupby(d5.index.normalize()):
        if day > last:
            rows.append((day, float(x["Open"].iloc[0]), float(x["High"].max()), float(x["Low"].min()),
                         float(x["Close"].iloc[-1]), float(x["Volume"].sum())))
    if not rows:
        return df, 0
    ext = pd.DataFrame(rows, columns=["Date", "Open", "High", "Low", "Close", "Volume"]).set_index("Date")
    return pd.concat([df, ext]).sort_index(), len(ext)


def inst_history(dates):
    """逐日三大法人買賣超（億元）：證交所 BFI82U，結果快取在 cache/inst_hist.json，只補抓缺的日期。"""
    path = CACHE_DIR / "inst_hist.json"
    try:
        cache = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa
        cache = {}
    today = dt.date.today()
    miss = [d for d in dates if d.isoformat() not in cache][-45:]
    fails = 0
    for d in miss:
        try:
            j = requests.get("https://www.twse.com.tw/rwd/zh/fund/BFI82U",
                             params={"date": d.strftime("%Y%m%d"), "type": "day", "response": "json"}, headers=UA, timeout=12).json()
            if j.get("stat") == "OK" and j.get("data"):
                rec = {}
                for row in j["data"]:
                    nm, v = row[0], num(row[3])
                    if v is None:
                        continue
                    v /= 1e8
                    if nm.startswith("外資及陸資") and "fi" not in rec:
                        rec["fi"] = round(v, 1)
                    elif nm.startswith("投信"):
                        rec["it"] = round(v, 1)
                    elif nm.startswith("合計"):
                        rec["tot"] = round(v, 1)
                cache[d.isoformat()] = rec
            elif d < today:
                cache[d.isoformat()] = {}     # 該日沒有資料（假日等），不再重抓
            fails = 0
        except Exception:  # noqa
            fails += 1
            if fails >= 3:
                break
        time.sleep(0.3)
    try:
        CACHE_DIR.mkdir(exist_ok=True)
        path.write_text(json.dumps(cache, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    except OSError:
        pass
    return {k: v for k, v in cache.items() if v}


def news_on(day, n=3):
    """某一天的台股相關新聞標題（Google News 的 after:／before: 日期條件）。"""
    d1 = day + dt.timedelta(days=1)
    return news(f"台股 大盤 after:{day.isoformat()} before:{d1.isoformat()}", n, when=False)


def long_bars(df, inst, top=6, back=60, with_news=True):
    """找出最近 back 根日K中特別長的K棒（長紅、長黑、振幅特別大、跳空），並整理可能的原因。"""
    d = df.tail(back + 25).copy()
    body, rng = (d["Close"] - d["Open"]).abs(), d["High"] - d["Low"]
    ab, ar = body.shift().rolling(20).mean(), rng.shift().rolling(20).mean()
    av = d["Volume"].shift().rolling(20).mean()
    ma20, ma60 = d["Close"].rolling(20).mean(), d["Close"].rolling(60).mean()
    out = []
    for i in range(max(len(d) - back, 21), len(d)):
        r, pv = d.iloc[i], d.iloc[i - 1]
        flags = []
        if ab.iloc[i] > 0 and body.iloc[i] >= 2 * ab.iloc[i] and body.iloc[i] >= 0.6 * rng.iloc[i]:
            flags.append("長紅" if r["Close"] > r["Open"] else "長黑")
        if ar.iloc[i] > 0 and rng.iloc[i] >= 2 * ar.iloc[i]:
            flags.append("振幅大")
        if r["Low"] > pv["High"]:
            flags.append("跳空漲")
        elif r["High"] < pv["Low"]:
            flags.append("跳空跌")
        if not flags:
            continue
        day = d.index[i].date()
        pct = (r["Close"] / pv["Close"] - 1) * 100
        dirn = 1 if r["Close"] > r["Open"] else -1 if r["Close"] < r["Open"] else 0
        L = [f"開 {r['Open']:,.0f}／高 {r['High']:,.0f}／低 {r['Low']:,.0f}／收 {r['Close']:,.0f}，漲跌 {pct:+.2f}%；實體為近20日平均的 {body.iloc[i] / ab.iloc[i]:.1f} 倍、振幅 {rng.iloc[i] / ar.iloc[i]:.1f} 倍"]
        if av.iloc[i] > 0 and r["Volume"] > 0:
            q = r["Volume"] / av.iloc[i]
            L.append(f"成交量為20日均量的 {q:.1f} 倍（{'明顯放量，資金關注度高' if q >= 1.5 else '量縮，追價意願有限' if q <= 0.7 else '量能一般'}）")
        for nm, ma in (("月線(MA20)", ma20), ("季線(MA60)", ma60)):
            if pd.notna(ma.iloc[i - 1]) and pd.notna(ma.iloc[i]):
                if pv["Close"] < ma.iloc[i - 1] and r["Close"] > ma.iloc[i]:
                    L.append(f"收盤由下往上站上{nm}（{ma.iloc[i]:,.0f}），可能引發技術面買盤")
                elif pv["Close"] > ma.iloc[i - 1] and r["Close"] < ma.iloc[i]:
                    L.append(f"收盤由上往下跌破{nm}（{ma.iloc[i]:,.0f}），可能引發停損或技術面賣壓")
        if "跳空漲" in flags:
            L.append("向上跳空：開盤前利多或隔夜國際市場走強，缺口是否被回補是後續關鍵")
        if "跳空跌" in flags:
            L.append("向下跳空：開盤前利空或隔夜國際市場走弱，缺口是否被回補是後續關鍵")
        ih = inst.get(day.isoformat()) if inst else None
        if ih and "tot" in ih:
            same = "與K棒方向一致，可能是原因之一" if (dirn > 0 and ih["tot"] > 0) or (dirn < 0 and ih["tot"] < 0) else "與K棒方向相反，推動力可能來自其他買賣者" if dirn else ""
            L.append(f"三大法人：外資 {ih.get('fi', 0):+.1f} 億、投信 {ih.get('it', 0):+.1f} 億、合計 {ih['tot']:+.1f} 億（{same}）")
        out.append({"date": day, "dir": dirn, "flags": flags, "pct": pct, "high": float(r["High"]), "lines": L, "news": []})
    out = out[-top:]
    if with_news:
        for b in out:
            b["news"] = safe(f"當日新聞 {b['date']}", news_on, b["date"]) or []
    return out


def twse_fill(df):
    """Yahoo 的日K若落後，用證交所「加權指數歷史」補上較新的日K（無成交量，以 0 表示）。"""
    last, today, add = df.index[-1], dt.date.today(), []
    for y, m in sorted({(last.year, last.month), (today.year, today.month)}):
        j = requests.get("https://www.twse.com.tw/rwd/zh/TAIEX/MI_5MINS_HIST",
                         params={"date": f"{y}{m:02d}01", "response": "json"}, headers=UA, timeout=12).json()
        if j.get("stat") != "OK":
            continue
        for r in j.get("data", []):
            yy, mm, dd = r[0].split("/")
            d = pd.Timestamp(int(yy) + 1911, int(mm), int(dd))
            if d > last:
                o, h, l, c = (num(x) for x in r[1:5])
                if None not in (o, h, l, c):
                    add.append((d, o, h, l, c))
    if not add:
        return df, 0
    ext = pd.DataFrame(add, columns=["Date", "Open", "High", "Low", "Close"]).set_index("Date")
    ext = ext[~ext.index.duplicated()]
    ext["Volume"] = 0.0
    return pd.concat([df, ext]).sort_index(), len(ext)


def expected_last_day():
    """依目前（台灣）時間估計最新應有的交易日；未考慮國定假日。"""
    now = dt.datetime.now()
    d = now.date()
    if now.hour < 14:
        d -= dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d


def kbar(df):
    """K棒型態判讀（紅K＝收盤高於開盤，黑K＝收盤低於開盤）。以最近 1～3 根日K判斷。"""
    d = df.tail(25)
    o, h, l, c = (d[k].to_numpy(float) for k in ("Open", "High", "Low", "Close"))
    n, R = len(d), []
    add = lambda p, t: R.append((p, t))
    body, rng = np.abs(c - o), h - l
    up_s, lo_s = h - np.maximum(o, c), np.minimum(o, c) - l
    red, blk = c > o, c < o
    avg = body[-21:-1].mean() if n > 5 else body.mean()
    r = rng[-1] if rng[-1] > 0 else 1e-9
    dn_trend = n > 5 and c[-1] < c[-6]
    kind = "紅K" if red[-1] else "黑K" if blk[-1] else "平盤K"
    add(0, f"最新一根為{kind}：開 {o[-1]:,.0f}／高 {h[-1]:,.0f}／低 {l[-1]:,.0f}／收 {c[-1]:,.0f}；實體 {body[-1]:,.0f} 點，上影線 {up_s[-1]:,.0f}，下影線 {lo_s[-1]:,.0f}（近20日平均實體 {avg:,.0f}）")
    long_ = body[-1] >= 1.5 * avg and body[-1] >= 0.6 * r
    if long_ and red[-1]:
        add(1, "長紅K：實體明顯大於平均，買盤強勢")
    elif long_ and blk[-1]:
        add(-1, "長黑K：實體明顯大於平均，賣壓沉重")
    if body[-1] <= 0.1 * r:
        add(0, "十字星：多空僵持，常是變盤訊號，需看隔日方向確認")
    elif lo_s[-1] >= 2 * body[-1] and up_s[-1] <= 0.3 * r:
        add(1, "錘子線（下跌後下影線長，有買盤承接）") if dn_trend else add(-1, "吊人線（上漲後下影線長，留意高檔轉折）")
    elif up_s[-1] >= 2 * body[-1] and lo_s[-1] <= 0.3 * r:
        add(-1, "流星線（上漲後上影線長，上檔賣壓大）") if not dn_trend else add(1, "倒錘線（下跌後出現試探買盤，需隔日確認）")
    if n >= 2:
        if blk[-2] and red[-1] and o[-1] <= c[-2] and c[-1] >= o[-2] and body[-1] > body[-2]:
            add(2, "多頭吞噬：紅K實體完全包住前一根黑K，止跌轉強訊號")
        elif red[-2] and blk[-1] and o[-1] >= c[-2] and c[-1] <= o[-2] and body[-1] > body[-2]:
            add(-2, "空頭吞噬：黑K實體完全包住前一根紅K，轉弱訊號")
        elif body[-2] >= 1.2 * avg and max(o[-1], c[-1]) <= max(o[-2], c[-2]) and min(o[-1], c[-1]) >= min(o[-2], c[-2]) and body[-1] < 0.6 * body[-2]:
            add(0, "母子（孕育）線：本根在前一根長K實體內，動能收斂，留意變盤")
        if l[-1] > h[-2]:
            add(1, "向上跳空缺口：留意缺口是否被回補")
        elif h[-1] < l[-2]:
            add(-1, "向下跳空缺口：留意缺口是否被回補")
    if n >= 3:
        mid1 = (o[-3] + c[-3]) / 2
        if blk[-3] and body[-3] >= 1.2 * avg and body[-2] <= 0.4 * avg and max(o[-2], c[-2]) <= c[-3] and red[-1] and c[-1] > mid1:
            add(2, "晨星：長黑、星線、長紅組合，底部反轉訊號")
        elif red[-3] and body[-3] >= 1.2 * avg and body[-2] <= 0.4 * avg and min(o[-2], c[-2]) >= c[-3] and blk[-1] and c[-1] < mid1:
            add(-2, "夜星：長紅、星線、長黑組合，頭部反轉訊號")
        if all(red[-3:]) and c[-3] < c[-2] < c[-1] and o[-3] <= o[-2] <= c[-3] and o[-2] <= o[-1] <= c[-2]:
            add(2, "紅三兵：連三紅K步步墊高，多方延續（漲多時留意追高風險）")
        elif all(blk[-3:]) and c[-3] > c[-2] > c[-1] and c[-3] <= o[-2] <= o[-3] and c[-2] <= o[-1] <= o[-2]:
            add(-2, "黑三鴉：連三黑K步步走低，空方延續")
    k, kind_s = 0, red[-1]
    for i in range(n - 1, -1, -1):
        if (red[i] if kind_s else blk[i]):
            k += 1
        else:
            break
    if k >= 4 and (red[-1] or blk[-1]):
        add(0, f"連續 {k} 根{'紅' if kind_s else '黑'}K，" + ("漲多留意乖離過大與獲利了結" if kind_s else "跌深留意是否出現反彈"))
    return R


def build(refresh=0):
    global HIST_LAST_CLOSE
    STATUS.clear()
    df = safe("加權指數歷史(Yahoo ^TWII)", hist, "^TWII", "6y")
    if df is None or len(df) < 60:
        raise RuntimeError("無法取得加權指數歷史資料，請檢查網路或稍後再試（詳見上方錯誤訊息）")
    n_fill = 0
    res = safe("證交所加權指數補最新日K(MI_5MINS_HIST)", twse_fill, df)
    if res:
        df, n_fill = res
    d5 = safe("Yahoo加權指數5分鐘K(當日走勢)", intraday5)
    if d5 is not None and df.index[-1].date() < expected_last_day():
        r5 = safe("Yahoo 5分鐘K彙整補日K", fill_from_5m, df, d5)
        if r5:
            df, n5 = r5
            n_fill += n5
    HIST_LAST_CLOSE = float(df["Close"].iloc[-1])
    Q = {k: safe(f"{k}({s})", quote, s) for k, s in MARKETS.items()}
    Q = {k: v for k, v in Q.items() if v}
    rt = safe("證交所即時指數", twse_realtime)
    inst = safe("證交所三大法人", institutional)
    fut = safe("期交所台指期", taifex)
    yi = None if rt else safe("Yahoo加權指數1分鐘(即時備援)", yahoo_intraday)
    cur = current_quote(df, rt, yi)
    nws = {k: safe(f"新聞:{k}", news, q) for k, q in NEWS_TOPICS.items()}

    T, info = tech(df)
    K = kbar(df)
    PT = CP.detect(df)
    ps = CP.score(PT)
    M, N = macro_score(Q, rt, inst, fut, nws, info["date"])
    ts, ms, ns = (sum(p for p, _ in x) for x in (T, M, N))
    ks = max(-3, min(3, sum(p for p, _ in K)))
    tot = ts + ks + ps + ms + ns
    now = dt.datetime.now()
    comps = {"技術面": ts, "K棒型態": ks, "圖形型態": ps, "國際與資金面": ms, "消息面": ns}
    weighted, w, entry, pdat = float(tot), {k: 1.0 for k in PR.COMPS}, None, None
    try:
        pdat = PR.load()
        PR.update_outcomes(pdat, df, now)
        w = PR.weights(pdat)[0]
        weighted = sum(w[k] * v for k, v in comps.items())
        entry = PR.record(pdat, now, comps, tot, weighted, float(df["Close"].iloc[-1]), w)
        PR.save(pdat)
        STATUS.append(("預測紀錄與權重調整(cache/predictions.json)", True, f"已結算 {PR.weights(pdat)[2]['done']} 天"))
    except Exception as e:  # noqa
        STATUS.append(("預測紀錄與權重調整(cache/predictions.json)", False, f"{type(e).__name__}: {str(e)[:100]}"))
    label = PR.label_of(weighted)
    c = "up" if weighted >= 2 else "dn" if weighted <= -2 else ""

    p = ['<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><title>台股大盤分析</title>']
    p.append(f"<style>{CSS}</style></head><body><main><h1>📊 台股大盤 K 線與隔日行情分析</h1>{NAV}")
    p.append(f'<p class="note">本頁為定時更新的快照（非逐筆即時）。報告產生時間：{dt.datetime.now():%Y-%m-%d %H:%M:%S}　歷史資料至 {info["date"]}（Yahoo 可能有延遲）。美股等海外資料為其最近一個收盤/報價日。</p>')
    exp = expected_last_day()
    if info["date"] < exp:
        p.append(f'<p class="bad">⚠ 日K最新只到 {info["date"]}，依今天時間應至少到 {exp}（若剛好遇到國定假日則屬正常）。資料可能落後，分析請留意時效。</p>')
    if n_fill:
        p.append(f'<p class="note">已補上 {n_fill} 根較新的日K（來源為證交所或 Yahoo 5 分鐘K彙整；補上的K棒成交量可能為 0 或不完整）。</p>')
    dd = [f"日K至 {info['date']}"]
    if inst:
        dd.append(f"三大法人 {inst['date']}")
    if fut:
        dd.append("台指期 " + "／".join(f"{n}{fut[k]['date']}" for k, n in (("day", "日盤"), ("night", "盤後")) if k in fut))
    if Q.get("S&P500"):
        dd.append(f"美股 {Q['S&P500']['date']}")
    p.append('<p class="note">資料日期一覽：' + "｜".join(dd) + "</p>")
    p.append(LIVE_HTML.replace("__GEN__", now.astimezone().isoformat(timespec="seconds")).replace("__REFRESH__", str(int(refresh))))
    p.append('<div class="card">')
    chg = cur["price"] - cur["prev"]
    pc = chg / cur["prev"] * 100
    p.append(f'<div class="big">加權指數 <b>{cur["price"]:,.2f}</b> <span class="{cls(chg)}">{chg:+,.2f}（{pc:+.2f}%）</span></div>')
    p.append(f'<div class="note">來源：{cur["src"]}｜資料時間：{cur["time"]}｜昨收 {cur["prev"]:,.2f}</div>')
    chips = "　".join(f'{k} <span class="{cls(Q[k]["pct"])}">{Q[k]["pct"]:+.2f}%</span>' for k in
                    ("S&P500", "那斯達克", "費城半導體", "台積電ADR", "EWT台灣ETF(美股)", "S&P500期貨", "那斯達克期貨", "VIX恐慌指數", "美元/台幣") if k in Q)
    if chips:
        p.append(f'<div class="note">海外最新（相對前一收盤，Yahoo 可能延遲）：{chips}<br>美股現金交易時段約台灣時間 22:30～05:00（冬令；夏令為 21:30～04:00），期貨近 24 小時交易，晚上的隔日台股判斷以美股、期貨與 EWT 為主。</div>')
    p.append(f'<div class="big">{"今日" if PR.target_date(now) == now.date() else "隔日"}預判：<span class="{c}">{label}</span>（加權後 {weighted:+.1f}；原始總分 {tot:+d}＝技術面 {ts:+d}＋K棒型態 {ks:+d}＋圖形型態 {ps:+d}＋國際與資金面 {ms:+d}＋消息面 {ns:+d}）</div>')
    p.append(f'<p>近20日壓力約 <b>{info["hi"]:,.0f}</b>，支撐約 <b>{info["lo"]:,.0f}</b>；有效突破壓力偏多，跌破支撐轉弱。</p></div>')
    today_s = now.strftime("%Y%m%d")
    if pdat is not None:
        p.append(pred_card(pdat, w, entry, weighted, now, pc, today_s in cur["time"].replace("-", ""), tot))
    p.append('<div class="card"><h2>K 線圖（可調整時間範圍）</h2>')
    p.append(RANGE_HTML)
    ih = safe("三大法人歷史(逐日BFI82U，快取)", inst_history, [x.date() for x in df.index[-45:]])
    LB = long_bars(df, ih or {})
    marks = []
    for b in LB:
        txt = "<br>".join(["<b>" + str(b["date"]) + " " + "、".join(b["flags"]) + "</b>"] + [x for ln in b["lines"] for x in textwrap.wrap(ln, 34)] +
                          [x for t in b["news"][:2] for x in textwrap.wrap("新聞：" + t["title"], 34)] + ["委買委賣為盤中即時資料，歷史K棒無法回溯"])
        marks.append({"date": pd.Timestamp(b["date"]), "y": b["high"] * 1.012, "color": UP if b["dir"] > 0 else DN if b["dir"] < 0 else "#8a92a3", "text": txt})
    first = True
    if d5 is not None and len(d5) > 10:
        p.append(chart(d5, "當日走勢（5 分鐘K；MA12＝1 小時、MA48＝4 小時；按鈕可看當日／2日／5日）", [12, 48], ["MA12(1小時)", "MA48(4小時)"],
                       min(len(d5), 60), first=first, buttons=[(1, "day", "當日"), (2, "day", "2日")], intraday=True))
        first = False
    p.append(chart(df, "日K（週線MA5／月線MA20／季線MA60／半年線MA120／年線MA240）", [5, 10, 20, 60, 120, 240],
                   ["週線MA5", "MA10", "月線MA20", "季線MA60", "半年線MA120", "年線MA240"], 250, True, first, PT, marks=marks,
                   visible=["週線MA5", "月線MA20", "季線MA60", "年線MA240"],
                   buttons=[(5, "day", "5日"), (1, "month", "1個月"), (3, "month", "3個月"), (6, "month", "6個月"), (1, "year", "1年"), (3, "year", "3年")]))
    p.append(chart(resample(df, "W-FRI"), "週K", [5, 13, 26, 52], ["MA5週", "MA13週(季)", "MA26週(半年)", "MA52週(年)"], 156,
                   buttons=[(3, "month", "3個月"), (6, "month", "6個月"), (1, "year", "1年"), (3, "year", "3年")]))
    p.append(chart(resample(df, "ME"), "月K", [3, 6, 12, 24], ["MA3月", "MA6月", "MA12月(年)", "MA24月"], 120,
                   buttons=[(1, "year", "1年"), (3, "year", "3年"), (5, "year", "5年")]))
    p.append("</div>")
    if LB:
        items = ""
        for b in LB[::-1]:
            col = "up" if b["dir"] > 0 else "dn" if b["dir"] < 0 else ""
            news_h = "".join(f'<li class="note">當日新聞：<a href="{html.escape(t["link"])}" target="_blank" rel="noopener">{html.escape(t["title"])}</a></li>' for t in b["news"][:3])
            items += (f'<li><b class="{col}">{b["date"]} {"、".join(b["flags"])}（{b["pct"]:+.2f}%）</b><ul>'
                      + "".join(f"<li>{html.escape(x)}</li>" for x in b["lines"]) + news_h + "</ul></li>")
        p.append(f'<div class="card"><h2>特別長的K棒說明（日K圖上的菱形標記，滑鼠移上去也看得到）</h2><ul>{items}</ul>'
                 '<p class="note">原因是依當日價量、均線位置、跳空、三大法人買賣超與當日新聞標題整理的「可能原因」，不代表因果。'
                 '委買委賣、台指期等為盤中即時資料，歷史K棒無法回溯；盤中請看個股篩選頁或 live_server.py 的即時五檔。</p></div>')
    p.append(f'<div class="card"><h2>K棒型態分析（日K，看最近 1～3 根）</h2><ul>{li(K)}</ul><p class="note">型態需搭配所處位置與量能判讀，單一型態可靠度有限；K棒型態分數最多計 ±3 分。</p></div>')
    if PT:
        cards = "".join(f'<li><b class="{cls(q["bias"])}">{html.escape(q["name"])}（{"偏多" if q["bias"] > 0 else "偏空" if q["bias"] < 0 else "中性"}'
                        f'{"、已確認" if q["confirmed"] else "、形成中"}）</b>：{html.escape(q["status"])}<br><span class="note">{html.escape(q["help"])}</span></li>' for q in PT)
        p.append(f'<div class="card"><h2>圖形型態分析（日K，紫色虛線已畫在日K圖上）</h2><ul>{cards}</ul><p class="note">依 IG 與學吧介紹的常見型態做規則式辨識，已確認計 ±2、形成中計 ±1，合計最多 ±3 分；容易有誤判與失敗，請搭配成交量與停損。</p></div>')
    else:
        p.append('<div class="card"><h2>圖形型態分析（日K）</h2><p class="note">目前沒有符合條件的明確型態（頭肩、雙頂底、圓底、杯柄、楔形、旗形、三角形、矩形、通道）。</p></div>')
    p.append(f'<div class="card"><h2>技術面（日K）</h2><ul>{li(T)}</ul></div>')
    p.append(f'<div class="card"><h2>國際、期貨、匯率與資金面</h2><ul>{li(M) or "<li>無資料</li>"}</ul>')
    p.append(f'<h2>消息面</h2><ul>{li(N) or "<li>無新聞資料</li>"}</ul></div>')
    rows = "".join(f'<tr><td>{k}</td><td>{v["last"]:,.2f}</td><td class="{cls(v["pct"])}">{v["pct"]:+.2f}%</td><td>{v["date"]}</td></tr>' for k, v in Q.items())
    p.append(f'<div class="card"><h2>國際市場與匯率</h2><table><tr><th>項目</th><th>最新</th><th>漲跌</th><th>日期</th></tr>{rows}</table></div>')
    if fut:
        fr = "".join(f'<li>{n}：{fut[k]["last"]:,.0f}（{fut[k]["chg"]:+.0f} 點，{fut[k]["pct"]:+.2f}%）日期 {fut[k]["date"]}</li>'
                     for k, n in (("day", "日盤"), ("night", "盤後")) if k in fut)
        p.append(f'<div class="card"><h2>台指期近月（{fut["contract"]}）</h2><ul>{fr}</ul></div>')
    p.append('<div class="card"><h2>國際大事與新聞標題（Google News）</h2>')
    for k, items in nws.items():
        if items:
            p.append(f"<b>{k}</b><ul>" + "".join(f'<li><a href="{html.escape(i["link"])}" target="_blank" rel="noopener">{html.escape(i["title"])}</a> <span class="note">{html.escape(i["date"])}</span></li>' for i in items) + "</ul>")
    p.append("</div>")
    st = "".join(f'<tr><td>{html.escape(n)}</td><td class="{"dn" if ok else "bad"}">{"成功" if ok else "失敗"}</td><td>{html.escape(m)}</td></tr>' for n, ok, m in STATUS)
    p.append(f'<div class="card"><h2>資料來源狀態</h2><table>{st}</table><p class="note">失敗的來源不會被估算或補數字，該項不計入評分。</p></div>')
    p.append('<p class="note">本報告為規則式技術與總體指標加總，並非預測，不構成投資建議；我不是財務顧問，投資前請自行研究並控管風險。</p></main>' + NAVJS + '</body></html>')
    OUT_FILE.write_text("".join(p), encoding="utf-8")
    print(f"[{dt.datetime.now():%H:%M:%S}] 加權 {cur['price']:,.2f}（{chg:+,.2f}／{pc:+.2f}%，{cur['src']}）｜隔日預判：{label}（{tot:+d}）→ {OUT_FILE}")
    for n, ok, m in STATUS:
        if not ok:
            print(f"  ✗ {n}: {m}")


def main():
    ap = argparse.ArgumentParser(description="台股大盤 K 線與隔日行情分析")
    ap.add_argument("--watch", type=int, default=0, help="每 N 秒更新一次（建議 >= 60）；0 表示只跑一次")
    ap.add_argument("--no-open", action="store_true", help="不自動開啟瀏覽器")
    ap.add_argument("--refresh", type=int, default=0, help="報告網頁每 N 秒自動重新載入（排程發佈用，例如 300）")
    a = ap.parse_args()
    first = True
    while True:
        try:
            build(refresh=max(a.watch, a.refresh, 0))
            if first and not a.no_open:
                webbrowser.open(OUT_FILE.as_uri())
            first = False
        except Exception as e:  # noqa
            print("執行失敗：", e)
            if not a.watch:
                raise
        if not a.watch:
            break
        time.sleep(max(a.watch, 30))


if __name__ == "__main__":
    main()
