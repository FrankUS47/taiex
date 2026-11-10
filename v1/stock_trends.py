# -*- coding: utf-8 -*-
"""
走勢統計：對上市、上櫃的股票與 ETF 計算
  - 連續上漲／下跌日數（正數＝連漲，負數＝連跌；盤中含當日最新價）
  - 5日、20日漲跌幅
  - K棒警示：長紅K、長黑K、振幅特別大、向上／向下跳空
結果存成 cache/trends.json，供 universe.html 篩選與排序。

資料來源：Yahoo Finance 日K（批次下載）。抓不到的股票會略過，不補假資料。
用法：
  python stock_trends.py                       # 全部成交量 >= 100 張的股票與 ETF
  python stock_trends.py --reuse-hours 1       # 快取在 1 小時內就略過
  python stock_trends.py --max-minutes 20      # 最多跑 20 分鐘，時間到就存檔結束
需與 stock_universe.py 放在同一資料夾。僅供研究參考，不構成投資建議。
"""
import argparse
import datetime as dt
import json
import time
from pathlib import Path

import pandas as pd
import yfinance as yf

import stock_universe as U
from taiex_analyzer import STATUS, safe

CACHE_DIR = Path(__file__).with_name("cache")
TRENDS = CACHE_DIR / "trends.json"


def stats(df):
    """df 需含 Open/High/Low/Close（依日期遞增）。回傳 [連續日數, 5日%, 20日%, K棒警示, 最新K棒實體%]。"""
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    if len(df) < 8:
        return None
    c = df["Close"]
    streak, sign = 0, 0
    for x in reversed(c.diff().dropna().to_list()):
        sg = 1 if x > 0 else -1 if x < 0 else 0
        if sg == 0 or (sign and sg != sign):
            break
        sign = sign or sg
        streak += 1
    c5 = (c.iloc[-1] / c.iloc[-6] - 1) * 100 if len(c) >= 6 else None
    c20 = (c.iloc[-1] / c.iloc[-21] - 1) * 100 if len(c) >= 21 else None
    o, h, l = df["Open"], df["High"], df["Low"]
    body, rng = (c - o).abs(), h - l
    avg_b, avg_r = body.iloc[-21:-1].mean(), rng.iloc[-21:-1].mean()
    flags = []
    if avg_b > 0 and body.iloc[-1] >= 2 * avg_b and body.iloc[-1] >= 0.6 * rng.iloc[-1]:
        flags.append("長紅" if c.iloc[-1] > o.iloc[-1] else "長黑")
    if avg_r > 0 and rng.iloc[-1] >= 2 * avg_r:
        flags.append("振幅大")
    if l.iloc[-1] > h.iloc[-2]:
        flags.append("跳空漲")
    elif h.iloc[-1] < l.iloc[-2]:
        flags.append("跳空跌")
    pct = lambda v: None if v is None else round(float(v), 1)
    return [streak * sign, pct(c5), pct(c20), "、".join(flags), round(float(body.iloc[-1] / o.iloc[-1] * 100), 2) if o.iloc[-1] else 0.0]


def batch(symbols):
    data = yf.download(" ".join(symbols), period="3mo", interval="1d", group_by="ticker", auto_adjust=False,
                       progress=False, threads=True)
    out = {}
    for s in symbols:
        try:
            df = data if len(symbols) == 1 else data[s]
            r = stats(df[["Open", "High", "Low", "Close"]])
            if r:
                out[s] = r
        except Exception:  # noqa
            continue
    return out


def write(stocks):
    CACHE_DIR.mkdir(exist_ok=True)
    TRENDS.write_text(json.dumps({"generated": dt.datetime.now().isoformat(timespec="seconds"), "count": len(stocks),
                                  "stocks": stocks}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="走勢統計")
    ap.add_argument("--min-volume", type=float, default=100, help="最低成交量(張)")
    ap.add_argument("--chunk", type=int, default=80)
    ap.add_argument("--reuse-hours", type=float, default=0)
    ap.add_argument("--max-minutes", type=float, default=20)
    a = ap.parse_args()
    old = {}
    if TRENDS.exists():
        try:
            j = json.loads(TRENDS.read_text(encoding="utf-8"))
            old = j.get("stocks", {})
            age = (dt.datetime.now() - dt.datetime.fromisoformat(j["generated"])).total_seconds() / 3600
            if age < a.reuse_hours:
                print(f"走勢快取只有 {age:.2f} 小時（{len(old)} 檔），略過重算。")
                return
        except Exception:  # noqa
            old = {}
    rows = (safe("上市股票清單", U.listed) or []) + (safe("上櫃股票清單", U.otc) or [])
    sym = {}
    for r in rows:
        if r[2] in ("股票", "ETF/ETN") and r[6] >= a.min_volume:
            sym[r[0] + (".TW" if r[3] == "上市" else ".TWO")] = r[0]
    if not sym:
        raise SystemExit("取不到股票清單")
    keys, res, t0 = list(sym), {}, time.time()
    print(f"計算 {len(keys)} 檔的走勢統計（每批 {a.chunk} 檔）…")
    for i in range(0, len(keys), a.chunk):
        if time.time() - t0 > a.max_minutes * 60:
            print("已達時間上限，先存目前結果。")
            break
        for s, v in batch(keys[i:i + a.chunk]).items():
            res[sym[s]] = v
        print(f"  進度 {min(i + a.chunk, len(keys))}/{len(keys)}，已取得 {len(res)} 檔")
        time.sleep(1)
    merged = {**old, **res}  # 這次沒抓到的沿用舊資料
    write(merged)
    print(f"完成：本次取得 {len(res)} 檔，快取共 {len(merged)} 檔 → {TRENDS}")
    STATUS.clear()


if __name__ == "__main__":
    main()
