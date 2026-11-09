# -*- coding: utf-8 -*-
"""
個股篩選與評價分析（上市股票；全部使用真實資料，抓不到就標示失敗，不會用模擬數據）

需與 taiex_analyzer.py 放在同一資料夾（重複使用其中的抓價、技術分析、新聞與圖表函式）。

資料來源：
  - 證交所 OpenAPI：全部上市股票收盤價、成交量、本益比、殖利率、股價淨值比
  - Yahoo Finance：財報指標(ROE、毛利率、負債、成長率、現金流)、季度營收與稅後淨利、歷史股價
  - Google News RSS：個股新聞標題

用法（PyCharm 可在 Run Configuration 的 Parameters 填入）：
  python stock_screener.py --max-price 50                          # 股價 50 元以下的篩選
  python stock_screener.py --max-price 80 --max-pe 15 --min-yield 4 --top 10
  python stock_screener.py --stock 2330 2317                       # 評價指定個股
  參數：--max-price 股價上限  --min-volume 最低成交量(張,預設1000)  --max-pe 本益比上限
        --min-yield 殖利率下限(%)  --top 深入評價檔數(預設10)  --no-open 不開啟瀏覽器

評等為規則式量化評分，不是投資建議。
"""
import argparse
import datetime as dt
import html
import time
import webbrowser
from pathlib import Path

import pandas as pd
import requests
import urllib3

import taiex_analyzer as T
from taiex_analyzer import STATUS, UA, chart, hist, news, num, safe, tech

urllib3.disable_warnings()
OUT_FILE = Path(__file__).with_name("stock_report.html")


# ───────────────────────── 資料抓取 ─────────────────────────
def twse_json(path):
    url = "https://openapi.twse.com.tw/v1/" + path
    try:
        r = requests.get(url, headers=UA, timeout=20)
    except requests.exceptions.SSLError:  # 部分 Python 版本驗證證交所憑證會失敗
        r = requests.get(url, headers=UA, timeout=20, verify=False)
    r.raise_for_status()
    return r.json()


def universe():
    df = pd.DataFrame(twse_json("exchangeReport/STOCK_DAY_ALL"))
    miss = [c for c in ("Code", "Name", "TradeVolume", "ClosingPrice") if c not in df.columns]
    if miss:
        raise RuntimeError(f"欄位不符，實際欄位：{list(df.columns)}")
    base = pd.DataFrame({"Code": df["Code"].astype(str), "Name": df["Name"],
                         "price": df["ClosingPrice"].map(num), "vol": df["TradeVolume"].map(num)})
    base = base[base["Code"].str.fullmatch(r"\d{4}") & ~base["Code"].str.startswith("00")]  # 一般個股，排除 ETF
    val = safe("證交所本益比/殖利率(BWIBBU_ALL)", twse_json, "exchangeReport/BWIBBU_ALL")
    for col in ("pe", "yld", "pb"):
        base[col] = float("nan")
    if val:
        v = pd.DataFrame(val)
        mp = {"PEratio": "pe", "DividendYield": "yld", "PBratio": "pb"}
        if "Code" in v.columns:
            v = v.rename(columns=mp)
            keep = ["Code"] + [c for c in mp.values() if c in v.columns]
            v = v[keep].copy()
            for c in keep[1:]:
                v[c] = v[c].map(num)
            base = base.drop(columns=[c for c in keep[1:]]).merge(v, on="Code", how="left")
            for c in ("pe", "yld", "pb"):
                if c not in base.columns:
                    base[c] = float("nan")
    return base.dropna(subset=["price"])


def load(code):
    for suf in (".TW", ".TWO"):  # 上市 / 上櫃
        try:
            return code + suf, hist(code + suf, "2y")
        except Exception:  # noqa
            pass
    raise RuntimeError("Yahoo 查無此代號的股價資料")


def fundamentals(sym):
    tk = T.yf.Ticker(sym)
    info = tk.info or {}
    if not info:
        raise RuntimeError("Yahoo 無財報資料")
    qy = {}
    try:
        q = tk.quarterly_income_stmt
        if q is not None and q.shape[1] >= 5:
            for key, nm in (("Total Revenue", "rev"), ("Net Income", "ni")):
                if key in q.index:
                    a, b = q.loc[key].iloc[0], q.loc[key].iloc[4]
                    if pd.notna(a) and pd.notna(b) and b > 0:
                        qy[nm] = (a / b - 1) * 100
    except Exception:  # noqa
        pass
    return info, qy


def stock_news(code, name):
    return news(f"{code} {name} 股票", 6)


# ───────────────────────── 評分 ─────────────────────────
def nz(x):
    return None if x is None or (isinstance(x, float) and x != x) else x


def score_fund(info, qy, pe, yld, pb):
    R = []
    add = lambda p, t: R.append((p, t))
    g = lambda k: nz(info.get(k))
    roe, gm, pm, de, rg, eg, fcf = (g("returnOnEquity"), g("grossMargins"), g("profitMargins"),
                                    g("debtToEquity"), g("revenueGrowth"), g("earningsGrowth"), g("freeCashflow"))
    if roe is not None:
        roe *= 100
        add(2 if roe >= 15 else 1 if roe >= 8 else -2 if roe < 0 else 0, f"ROE {roe:.1f}%")
    if gm is not None:
        gm *= 100
        add(1 if gm >= 30 else 0, f"毛利率 {gm:.1f}%")
    if pm is not None:
        pm *= 100
        add(1 if pm > 10 else -2 if pm < 0 else 0, f"淨利率 {pm:.1f}%")
    if de is not None:
        add(1 if de < 50 else -1 if de > 150 else 0, f"負債權益比 {de:.0f}%")
    if rg is not None:
        rg *= 100
        add(2 if rg > 10 else 1 if rg > 0 else -2 if rg < -10 else -1, f"營收年成長 {rg:+.1f}%")
    if eg is not None:
        eg *= 100
        add(2 if eg > 10 else 1 if eg > 0 else -2 if eg < -10 else -1, f"盈餘年成長 {eg:+.1f}%")
    for k, nm in (("rev", "最近一季營收"), ("ni", "最近一季稅後淨利")):
        if k in qy:
            add(1 if qy[k] > 0 else -1, f"{nm}年增 {qy[k]:+.1f}%")
    pe = nz(pe) if nz(pe) is not None else g("trailingPE")
    if pe is not None:
        add(2 if 0 < pe <= 15 else 1 if 0 < pe <= 22 else -1 if (pe <= 0 or pe > 35) else 0, f"本益比 {pe:.1f}")
    if nz(yld) is not None:
        add(2 if yld >= 4 else 1 if yld >= 2.5 else 0, f"殖利率 {yld:.2f}%")
    pb = nz(pb) if nz(pb) is not None else g("priceToBook")
    if pb is not None:
        add(1 if 0 < pb <= 1.5 else -1 if pb > 4 else 0, f"股價淨值比 {pb:.2f}")
    if fcf is not None:
        add(1 if fcf > 0 else -1, f"自由現金流為{'正' if fcf > 0 else '負'}")
    return R


def score_news(items):
    ns = sum(sum(w in i["title"] for w in T.POS_WORDS) - sum(w in i["title"] for w in T.NEG_WORDS) for i in items)
    p = max(-2, min(2, round(ns / 3)))
    return [(p, f"新聞標題關鍵字粗略情緒 {ns:+d}（僅供參考，易誤判）")]


def verdict(fs, ts, ns):
    tot = fs + ts + ns
    overall = "偏買進" if tot >= 8 else "觀望偏多" if tot >= 3 else "中性" if tot >= -2 else "觀望偏空" if tot >= -6 else "偏賣出"
    long_ = "長期體質佳" if fs >= 7 else "長期尚可" if fs >= 3 else "長期偏弱"
    short = "短線偏強" if ts >= 4 else "短線中性" if ts > -4 else "短線偏弱"
    return tot, overall, long_, short


def evaluate(row):
    code, name = row["Code"], row["Name"]
    sym, df = load(code)
    info, qy = safe(f"財報 {code}", fundamentals, sym) or ({}, {})
    items = safe(f"新聞 {code}", stock_news, code, name) or []
    F = score_fund(info, qy, row.get("pe"), row.get("yld"), row.get("pb")) if info else []
    Tc, tinfo = tech(df)
    N = score_news(items) if items else []
    fs, ts, ns = (sum(p for p, _ in x) for x in (F, Tc, N))
    tot, overall, long_, short = verdict(fs, ts, ns)
    return dict(code=code, name=name, sym=sym, df=df, info=info, F=F, T=Tc, N=N, items=items, tinfo=tinfo,
                fs=fs, ts=ts, ns=ns, tot=tot, overall=overall, long=long_, short=short, price=tinfo["last"],
                has_fin=bool(info))


# ───────────────────────── 報告 ─────────────────────────
EXTRA = ".tag{padding:2px 8px;border-radius:10px;color:#fff;font-size:12px;background:#8a92a3}.buy{background:#d6293e}.sell{background:#1a9a52}.wrap{overflow-x:auto}"


def li(R):
    return "".join(f"<li>{'🔺' if p > 0 else '🔻' if p < 0 else '➖'} {html.escape(t)}（{p:+d}）</li>" for p, t in R) or "<li>無資料</li>"


def tagcls(o):
    return "buy" if "買" in o or "多" in o else "sell" if "賣" in o or "空" in o else ""


def render(filters, cand, results, charts=5):
    p = [f'<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><title>個股篩選與評價</title><style>{T.CSS}{EXTRA}</style></head><body><main>',
         "<h1>🔍 個股篩選與評價分析</h1>",
         f'<p class="note">產生時間：{dt.datetime.now():%Y-%m-%d %H:%M:%S}。評等為規則式量化評分，不是買賣建議。</p>']
    if filters:
        cnt = f"；初篩符合者共 <b>{len(cand)}</b> 檔" if cand is not None else ""
        p.append(f'<div class="card"><h2>篩選條件</h2>{html.escape("；".join(filters))}{cnt}</div>')
    if cand is not None and len(cand):
        rows = "".join(f'<tr><td>{r.Code}</td><td>{html.escape(str(r.Name))}</td><td>{r.price:,.2f}</td><td>{r.vol / 1000:,.0f}</td>'
                       f'<td>{"—" if pd.isna(r.pe) else f"{r.pe:.1f}"}</td><td>{"—" if pd.isna(r.yld) else f"{r.yld:.2f}"}</td>'
                       f'<td>{"—" if pd.isna(r.pb) else f"{r.pb:.2f}"}</td></tr>' for r in cand.head(60).itertuples())
        p.append(f'<div class="card"><h2>符合條件清單（依本益比、殖利率、股價淨值比、成交量預排，前60檔）</h2><div class="wrap"><table><tr><th>代號</th><th>名稱</th><th>收盤</th><th>成交量(張)</th><th>本益比</th><th>殖利率%</th><th>股價淨值比</th></tr>{rows}</table></div></div>')
    if results:
        rs = sorted(results, key=lambda x: -x["tot"])
        rows = "".join(f'<tr><td>{r["code"]}</td><td>{html.escape(r["name"])}</td><td>{r["price"]:,.2f}</td><td>{r["fs"]:+d}</td><td>{r["ts"]:+d}</td><td>{r["ns"]:+d}</td>'
                       f'<td><b>{r["tot"]:+d}</b></td><td><span class="tag {tagcls(r["overall"])}">{r["overall"]}</span></td><td>{r["long"]}</td><td>{r["short"]}</td></tr>' for r in rs)
        p.append(f'<div class="card"><h2>個股評價排行</h2><div class="wrap"><table><tr><th>代號</th><th>名稱</th><th>股價</th><th>基本面</th><th>技術面</th><th>消息面</th><th>總分</th><th>量化評等</th><th>長期</th><th>短期</th></tr>{rows}</table></div></div>')
        for i, r in enumerate(rs):
            p.append(f'<div class="card"><h2>{r["code"]} {html.escape(r["name"])}　<span class="tag {tagcls(r["overall"])}">{r["overall"]}</span>　總分 {r["tot"]:+d}</h2>')
            p.append(f'<p>股價 {r["price"]:,.2f}（{r["tinfo"]["pct"]:+.2f}%，{r["tinfo"]["date"]}）｜{r["long"]}｜{r["short"]}｜近20日壓力 {r["tinfo"]["hi"]:,.2f}、支撐 {r["tinfo"]["lo"]:,.2f}</p>')
            p.append(f'<b>基本面（{r["fs"]:+d}）</b><ul>{li(r["F"]) if r["has_fin"] else "<li>Yahoo 無財報資料，基本面不計分</li>"}</ul>')
            p.append(f'<b>技術面（{r["ts"]:+d}）</b><ul>{li(r["T"])}</ul><b>消息面（{r["ns"]:+d}）</b><ul>{li(r["N"])}</ul>')
            if r["items"]:
                p.append("<b>相關新聞</b><ul>" + "".join(f'<li><a href="{html.escape(x["link"])}" target="_blank" rel="noopener">{html.escape(x["title"])}</a> <span class="note">{html.escape(x["date"])}</span></li>' for x in r["items"]) + "</ul>")
            if i < charts and len(r["df"]) > 60:
                p.append(chart(r["df"], f'{r["code"]} {r["name"]} 日K', [5, 20, 60, 240], ["週線MA5", "月線MA20", "季線MA60", "年線MA240"], 250, True, i == 0))
            p.append("</div>")
    st = "".join(f'<tr><td>{html.escape(n)}</td><td class="{"dn" if ok else "bad"}">{"成功" if ok else "失敗"}</td><td>{html.escape(m)}</td></tr>' for n, ok, m in STATUS)
    p.append(f'<div class="card"><h2>資料來源狀態</h2><table>{st}</table><p class="note">失敗的來源不會被估算，該項不計入評分。</p></div>')
    p.append('<p class="note">評分門檻為作者設定，僅反映過去財報與價量，不保證未來表現；新聞情緒為關鍵字計數，容易誤判。這不是投資建議，我不是財務顧問，下單前請自行研究並控管風險。</p></main></body></html>')
    OUT_FILE.write_text("".join(p), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="個股篩選與評價分析")
    ap.add_argument("--max-price", type=float)
    ap.add_argument("--min-volume", type=float, default=1000, help="最低成交量(張)")
    ap.add_argument("--max-pe", type=float)
    ap.add_argument("--min-yield", type=float)
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--growth", action="store_true", help="評價後只留營收年成長為正者")
    ap.add_argument("--min-total", type=float, help="評價後只留總分 >= 此值者")
    ap.add_argument("--stock", nargs="+", help="指定股票代號，例如 2330 2317")
    ap.add_argument("--no-open", action="store_true")
    a = ap.parse_args()
    STATUS.clear()
    uni = safe("證交所上市收盤價(STOCK_DAY_ALL)", universe)
    filters, cand, targets = [], None, []
    if a.stock:
        for c in a.stock:
            r = uni[uni["Code"] == c].iloc[0].to_dict() if uni is not None and (uni["Code"] == c).any() else {"Code": c, "Name": c, "pe": None, "yld": None, "pb": None}
            targets.append(r)
    else:
        if uni is None:
            raise SystemExit("無法取得證交所資料，請看上方錯誤訊息")
        cand = uni[uni["vol"] >= a.min_volume * 1000].copy()
        filters.append(f"成交量 ≥ {a.min_volume:.0f} 張")
        if a.max_price is not None:
            cand = cand[cand["price"] <= a.max_price]
            filters.append(f"股價 ≤ {a.max_price:g} 元")
        if a.max_pe is not None:
            cand = cand[(cand["pe"] > 0) & (cand["pe"] <= a.max_pe)]
            filters.append(f"本益比 ≤ {a.max_pe:g}")
        if a.min_yield is not None:
            cand = cand[cand["yld"] >= a.min_yield]
            filters.append(f"殖利率 ≥ {a.min_yield:g}%")

        def pre(r):
            s = 0
            if pd.notna(r.pe):
                s += 2 if 0 < r.pe <= 15 else 1 if 0 < r.pe <= 22 else -1 if (r.pe <= 0 or r.pe > 35) else 0
            if pd.notna(r.yld):
                s += 2 if r.yld >= 4 else 1 if r.yld >= 2.5 else 0
            if pd.notna(r.pb):
                s += 1 if 0 < r.pb <= 1.5 else -1 if r.pb > 4 else 0
            return s

        cand["pre"] = [pre(r) for r in cand.itertuples()]
        cand = cand.sort_values(["pre", "vol"], ascending=False)
        print(f"符合條件 {len(cand)} 檔，深入評價前 {a.top} 檔…")
        targets = [r._asdict() if hasattr(r, "_asdict") else r for r in cand.head(a.top).to_dict("records")]
    results = []
    for t in targets:
        r = safe(f"評價 {t['Code']}", evaluate, t)
        if r:
            results.append(r)
            print(f"  {r['code']} {r['name']}: 總分 {r['tot']:+d} {r['overall']}")
        time.sleep(0.5)
    n0 = len(results)
    if a.growth:
        results = [r for r in results if (nz(r["info"].get("revenueGrowth")) or 0) > 0]
        filters.append("營收年成長 > 0（深入評價後再篩，Yahoo 無資料者排除）")
    if a.min_total is not None:
        results = [r for r in results if r["tot"] >= a.min_total]
        filters.append(f"總分 ≥ {a.min_total:g}（深入評價後再篩）")
    if n0 != len(results):
        print(f"評價後條件篩選：{n0} 檔 → {len(results)} 檔（只對前 {a.top} 檔深入評價；想看更多請加大檔數）")
    render(filters, cand, results)
    print("報告：", OUT_FILE)
    for n, ok, m in STATUS:
        if not ok:
            print(f"  ✗ {n}: {m}")
    if not a.no_open:
        webbrowser.open(OUT_FILE.as_uri())


if __name__ == "__main__":
    main()
