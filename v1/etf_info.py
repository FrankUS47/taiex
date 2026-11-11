# -*- coding: utf-8 -*-
"""
ETF 資料：成交量較大的 ETF 的淨值、折溢價、殖利率、持股本益比、基金規模（資料取自 Yahoo Finance）。
結果存成 cache/etf.json，供 universe.html 的 ETF 欄位使用。

說明：
  - ETF 沒有公司財報，所以「本益比」是持股本益比、「殖利率」是近期配息殖利率、「淨值比」＝市價÷淨值（接近 1 代表沒有明顯折溢價）。
  - Yahoo 對台股 ETF 的資料常有缺漏，抓不到的欄位就留空，不會補假資料。
用法：python etf_info.py [--top 80] [--reuse-hours 6]
需與 stock_universe.py 放在同一資料夾。僅供研究參考，不構成投資建議。
"""
import argparse
import datetime as dt
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yfinance as yf

import stock_universe as U
from taiex_analyzer import STATUS, safe

CACHE_DIR = Path(__file__).with_name("cache")
ETFC = CACHE_DIR / "etf.json"


def num(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def one(item):
    code, sym, price = item
    try:
        info = yf.Ticker(sym).info or {}
    except Exception:  # noqa
        return code, None
    nav = num(info.get("navPrice"))
    y = num(info.get("yield"))
    if y is None:
        y = num(info.get("trailingAnnualDividendYield"))
    pe = num(info.get("trailingPE"))
    aum = num(info.get("totalAssets"))
    rec = {"nav": round(nav, 2) if nav else None,
           "pn": round(price / nav, 4) if (nav and price) else None,
           "y": round(y * 100, 2) if y is not None else None,   # Yahoo 的 yield 為比例（0.03＝3%）
           "pe": round(pe, 1) if pe else None,
           "aum": aum}
    return code, (rec if any(v is not None for v in rec.values()) else None)


def main():
    ap = argparse.ArgumentParser(description="ETF 資料")
    ap.add_argument("--top", type=int, default=80, help="取成交量前幾大的 ETF")
    ap.add_argument("--reuse-hours", type=float, default=0)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    old = {}
    if ETFC.exists():
        try:
            j = json.loads(ETFC.read_text(encoding="utf-8"))
            old = j.get("stocks", {})
            age = (dt.datetime.now() - dt.datetime.fromisoformat(j["generated"])).total_seconds() / 3600
            if age < a.reuse_hours:
                print(f"ETF 快取只有 {age:.1f} 小時（{len(old)} 檔），略過重算。")
                return
        except Exception:  # noqa
            old = {}
    rows = (safe("上市清單", U.listed) or []) + (safe("上櫃清單", U.otc) or [])
    etfs = sorted((r for r in rows if r[2] == "ETF/ETN"), key=lambda r: -r[6])[:a.top]
    items = [(r[0], r[0] + (".TW" if r[3] == "上市" else ".TWO"), r[4]) for r in etfs]
    if not items:
        raise SystemExit("取不到 ETF 清單")
    print(f"抓取 {len(items)} 檔 ETF 資料…")
    out = dict(old)
    got = 0
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for code, rec in ex.map(one, items):
            if rec:
                out[code] = rec
                got += 1
    CACHE_DIR.mkdir(exist_ok=True)
    ETFC.write_text(json.dumps({"generated": dt.datetime.now().isoformat(timespec="seconds"), "count": len(out), "stocks": out},
                               ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"完成：本次取得 {got}/{len(items)} 檔，快取共 {len(out)} 檔 → {ETFC}")
    STATUS.clear()


if __name__ == "__main__":
    main()
