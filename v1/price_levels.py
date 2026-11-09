# -*- coding: utf-8 -*-
"""
停損／停利參考價位試算（以真實歷史股價計算，不含手續費與證交稅）

輸入：股票代號、你的買進價（可選：張數、可承受最大虧損%）
輸出：由「近期支撐壓力、均線、ATR 波動幅度、風險報酬比」算出的參考價位。
這些只是依價量算出的參考點，不是買賣建議，也不保證價格會在這些位置反轉。

用法：python price_levels.py 2330 600 --lots 2 --max-loss 7
需與 taiex_analyzer.py、stock_screener.py 放在同一資料夾。
"""
import argparse

import numpy as np

from stock_screener import load


def tick(p):
    """台股升降單位。"""
    return 0.01 if p < 10 else 0.05 if p < 50 else 0.1 if p < 100 else 0.5 if p < 500 else 1 if p < 1000 else 5


def rnd(p):
    t = tick(p)
    return round(round(p / t) * t, 2)


def calc(code, buy, lots=None, max_loss=7.0):
    sym, df = load(code)
    c, h, l = df["Close"], df["High"], df["Low"]
    last = float(c.iloc[-1])
    tr = np.maximum(h - l, np.maximum((h - c.shift()).abs(), (l - c.shift()).abs()))
    atr = float(tr.ewm(alpha=1 / 14, adjust=False).mean().iloc[-1])
    ma20, ma60 = float(c.rolling(20).mean().iloc[-1]), float(c.rolling(60).mean().iloc[-1])
    lo20, hi20, hi60 = float(l.tail(20).min()), float(h.tail(20).max()), float(h.tail(60).max())

    fixed = buy * (1 - max_loss / 100)
    stops = sorted({(n, rnd(v)) for n, v in (
        ("近20日低點（支撐）", lo20), ("月線 MA20", ma20), ("季線 MA60", ma60),
        ("買價 − 2×ATR", buy - 2 * atr), (f"固定 {max_loss:g}%", fixed)) if v == v and v < buy}, key=lambda x: -x[1])
    ok = [v for _, v in stops if buy - v >= atr and (buy - v) / buy * 100 <= max_loss]
    ref = max(ok) if ok else rnd(fixed)
    risk = buy - ref
    targets = sorted({(n, rnd(v)) for n, v in (
        ("近20日高點（壓力）", hi20), ("近60日高點（壓力）", hi60), ("買價 + 2×ATR", buy + 2 * atr),
        ("買價 + 3×ATR", buy + 3 * atr), ("風險報酬 1:2", buy + 2 * risk), ("風險報酬 1:3", buy + 3 * risk)) if v > buy}, key=lambda x: x[1])
    return dict(sym=sym, date=df.index[-1].date(), last=last, atr=atr, buy=buy, ref_stop=ref, risk=risk,
                stops=stops, targets=targets, lots=lots, max_loss=max_loss)


def report(code, buy, lots=None, max_loss=7.0):
    r = calc(code, buy, lots, max_loss)
    b = r["buy"]
    pl = (r["last"] / b - 1) * 100
    print(f"{code}（{r['sym']}）最新收盤 {r['last']:,.2f}（{r['date']}），你的買價 {b:,.2f}，目前損益 {pl:+.2f}%")
    print(f"ATR(14) 約 {r['atr']:.2f} 元（{r['atr'] / b * 100:.1f}% 的日均波動幅度）\n")
    print("【停損參考價位】（低於買價者）")
    for n, v in r["stops"]:
        print(f"  {v:>10,.2f}  跌幅 {(v / b - 1) * 100:+.1f}%  {n}")
    print(f"  → 依「至少距買價 1 個 ATR、且不超過 {r['max_loss']:g}% 虧損」取最近一個作為參考停損：{r['ref_stop']:,.2f}"
          f"（每股風險 {r['risk']:.2f}，{r['risk'] / b * 100:.1f}%）")
    if r["lots"]:
        sh = r["lots"] * 1000
        print(f"  → {r['lots']:g} 張（{sh:,.0f} 股）若觸及參考停損，約虧 {r['risk'] * sh:,.0f} 元（未含手續費與證交稅）")
    print("\n【停利／目標參考價位】（高於買價者）")
    for n, v in r["targets"]:
        rr = (v - b) / r["risk"] if r["risk"] > 0 else 0
        print(f"  {v:>10,.2f}  漲幅 {(v / b - 1) * 100:+.1f}%  報酬風險比 {rr:.1f}  {n}")
    if not r["targets"]:
        print("  買價已高於近期高點與各 ATR 目標，沒有可參考的上方壓力。")
    print("\n註：停損太近容易被正常波動洗出場，太遠則單筆虧損變大；這些價位只是依歷史價量算出的參考，"
          "不構成投資建議，我不是財務顧問，請依自己的風險承受度決定。")
    return r


def main():
    ap = argparse.ArgumentParser(description="停損停利參考價位試算")
    ap.add_argument("code")
    ap.add_argument("buy", type=float)
    ap.add_argument("--lots", type=float)
    ap.add_argument("--max-loss", type=float, default=7.0)
    a = ap.parse_args()
    report(a.code, a.buy, a.lots, a.max_loss)


if __name__ == "__main__":
    main()
