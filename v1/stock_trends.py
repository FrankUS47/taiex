# -*- coding: utf-8 -*-
"""
走勢統計：對上市、上櫃的股票與 ETF 計算
  - 連續上漲／下跌日數（正數＝連漲，負數＝連跌；盤中含當日最新價）
  - 5日、20日漲跌幅
  - K棒警示：長紅K、長黑K、振幅特別大、向上／向下跳空
  - 圖形型態：頭肩、雙頂底、圓底、杯柄、楔形、旗形、三角形、矩形、通道（含可畫圖的 K 線與型態線）
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

import chart_patterns as CP
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
    data = yf.download(" ".join(symbols), period="6mo", interval="1d", group_by="ticker", auto_adjust=False,
                       progress=False, threads=True)
    recs, sers = {}, {}
    for s in symbols:
        try:
            df = data if len(symbols) == 1 else data[s]
            ohlc = df[["Open", "High", "Low", "Close"]].dropna()
            r = stats(ohlc)
            if not r:
                continue
            pats = CP.detect(ohlc) if len(ohlc) >= 60 else []
            pats = sorted(pats, key=lambda q: (not q["confirmed"], q["bias"] == 0))[:2]
            r.append([CP.short(q) for q in pats])
            recs[s] = r
            if pats:
                n = len(ohlc)
                k = min(70, n)
                off = n - k
                vol = float(df["Volume"].dropna().iloc[-1]) if "Volume" in df else 0.0
                sers[s] = {"v": vol, "b": [[round(float(x), 2) for x in row] for row in ohlc.tail(k).to_numpy()],
                           "l": [{"p": [[i - off, y] for i, y in ln["pts"]], "t": ln["label"]} for q in pats for ln in q["lines"]]}
        except Exception:  # noqa
            continue
    return recs, sers


def write(stocks, series):
    CACHE_DIR.mkdir(exist_ok=True)
    top = dict(sorted(series.items(), key=lambda kv: -kv[1].get("v", 0))[:250])  # 只保留成交量較大的 250 檔型態圖
    TRENDS.write_text(json.dumps({"generated": dt.datetime.now().isoformat(timespec="seconds"), "count": len(stocks),
                                  "stocks": stocks, "series": top}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="走勢統計與圖形型態")
    ap.add_argument("--min-volume", type=float, default=100, help="最低成交量(張)")
    ap.add_argument("--chunk", type=int, default=80)
    ap.add_argument("--reuse-hours", type=float, default=0)
    ap.add_argument("--max-minutes", type=float, default=20)
    a = ap.parse_args()
    old, old_ser = {}, {}
    if TRENDS.exists():
        try:
            j = json.loads(TRENDS.read_text(encoding="utf-8"))
            old, old_ser = j.get("stocks", {}), j.get("series", {})
            age = (dt.datetime.now() - dt.datetime.fromisoformat(j["generated"])).total_seconds() / 3600
            if age < a.reuse_hours:
                print(f"走勢快取只有 {age:.2f} 小時（{len(old)} 檔），略過重算。")
                return
        except Exception:  # noqa
            old, old_ser = {}, {}
    rows = (safe("上市股票清單", U.listed) or []) + (safe("上櫃股票清單", U.otc) or [])
    sym = {}
    for r in rows:
        if r[2] in ("股票", "ETF/ETN") and r[6] >= a.min_volume:
            sym[r[0] + (".TW" if r[3] == "上市" else ".TWO")] = r[0]
    if not sym:
        raise SystemExit("取不到股票清單")
    keys, res, ser, t0 = list(sym), {}, {}, time.time()
    print(f"計算 {len(keys)} 檔的走勢統計與圖形型態（每批 {a.chunk} 檔）…")
    for i in range(0, len(keys), a.chunk):
        if time.time() - t0 > a.max_minutes * 60:
            print("已達時間上限，先存目前結果。")
            break
        recs, sers = batch(keys[i:i + a.chunk])
        for s, v in recs.items():
            res[sym[s]] = v
        for s, v in sers.items():
            ser[sym[s]] = v
        print(f"  進度 {min(i + a.chunk, len(keys))}/{len(keys)}，已取得 {len(res)} 檔，型態圖 {len(ser)} 檔")
        time.sleep(1)
    merged = {**old, **res}  # 這次沒抓到的沿用舊資料
    series = {c: v for c, v in old_ser.items() if c not in res}
    series.update(ser)
    write(merged, series)
    print(f"完成：本次取得 {len(res)} 檔，快取共 {len(merged)} 檔，型態圖 {min(len(series), 250)} 檔 → {TRENDS}")
    STATUS.clear()


if __name__ == "__main__":
    main()
