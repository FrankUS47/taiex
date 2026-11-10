# -*- coding: utf-8 -*-
"""
預先計算「個股深入評價」並存成 cache/scores.json，讓 universe.html 在網頁上直接篩選評價結果，
使用者不必再到 GitHub Actions 改參數。

做法：從上市個股中，依股價區間（≤30、30～50、50～100、100～300、>300 元）各取成交量較大的前幾檔，
逐檔抓財報（Yahoo）、歷史股價（Yahoo）、新聞（Google News），用與 stock_screener.py 相同的規則評分。
抓不到的項目不計分，也不會補假資料。

用法：
  python stock_scores.py                    # 每個價格區間取 40 檔（共約 200 檔），結果存 cache/scores.json
  python stock_scores.py --per-band 60 --workers 4
  python stock_scores.py --reuse-hours 6    # 若 6 小時內已算過就略過（排程用）
需與 stock_screener.py、taiex_analyzer.py 放在同一資料夾。僅供研究參考，不構成投資建議。
"""
import argparse
import datetime as dt
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

import stock_screener as SS
from taiex_analyzer import STATUS

CACHE_DIR = Path(__file__).with_name("cache")
SCORES = CACHE_DIR / "scores.json"
BANDS = [(0, 30), (30, 50), (50, 100), (100, 300), (300, 1e9)]
NOW = lambda: dt.datetime.now().strftime("%Y-%m-%d %H:%M")


def compact(r):
    rg = SS.nz(r["info"].get("revenueGrowth")) if r["info"] else None
    pack = lambda L: [[int(p), t] for p, t in L]
    return {"c": r["code"], "n": r["name"], "fs": int(r["fs"]), "ts": int(r["ts"]), "ns": int(r["ns"]), "tot": int(r["tot"]),
            "ov": r["overall"], "lg": r["long"], "sh": r["short"], "gr": None if rg is None else round(rg * 100, 1),
            "F": pack(r["F"]), "T": pack(r["T"]), "N": pack(r["N"]),
            "news": [[i["title"], i["link"]] for i in r["items"]],
            "hi": r["tinfo"]["hi"], "lo": r["tinfo"]["lo"], "t": NOW()}


def one(row):
    try:
        return compact(SS.evaluate(row))
    except Exception as e:  # noqa
        print(f"  ✗ {row['Code']} {row['Name']}: {type(e).__name__}: {str(e)[:80]}")
        return None


def write(stocks):
    CACHE_DIR.mkdir(exist_ok=True)
    cutoff = (dt.datetime.now() - dt.timedelta(days=7)).strftime("%Y-%m-%d %H:%M")
    stocks = {k: v for k, v in stocks.items() if v.get("t", "") >= cutoff}  # 超過 7 天的舊評價丟棄
    SCORES.write_text(json.dumps({"generated": dt.datetime.now().isoformat(timespec="seconds"), "count": len(stocks),
                                  "stocks": stocks}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="預先計算個股深入評價")
    ap.add_argument("--per-band", type=int, default=40, help="每個股價區間取成交量前幾檔")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--min-volume", type=float, default=300, help="最低成交量(張)")
    ap.add_argument("--reuse-hours", type=float, default=0, help="快取在這個小時數內就略過重算")
    a = ap.parse_args()
    old = {}
    if SCORES.exists():
        try:
            j = json.loads(SCORES.read_text(encoding="utf-8"))
            old = j.get("stocks", {})
            age = (dt.datetime.now() - dt.datetime.fromisoformat(j["generated"])).total_seconds() / 3600
            if age < a.reuse_hours:
                print(f"快取只有 {age:.1f} 小時（{len(old)} 檔），略過重算。")
                return
        except Exception:  # noqa
            old = {}
    uni = SS.universe()
    uni = uni[uni["vol"] >= a.min_volume * 1000]
    picks = pd.concat([uni[(uni["price"] > lo) & (uni["price"] <= hi)].sort_values("vol", ascending=False).head(a.per_band)
                       for lo, hi in BANDS]).drop_duplicates("Code")
    rows = picks.to_dict("records")
    print(f"評價 {len(rows)} 檔（每個股價區間前 {a.per_band} 檔），使用 {a.workers} 個執行緒…")
    stocks, ok = dict(old), 0
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for i, res in enumerate(ex.map(one, rows), 1):
            if res:
                stocks[res["c"]] = res
                ok += 1
            if i % 20 == 0:
                write(stocks)
                print(f"  進度 {i}/{len(rows)}，成功 {ok}")
    write(stocks)
    print(f"完成：本次成功 {ok}/{len(rows)} 檔，快取共 {len(stocks)} 檔 → {SCORES}")
    STATUS.clear()


if __name__ == "__main__":
    main()
