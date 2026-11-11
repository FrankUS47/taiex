# -*- coding: utf-8 -*-
"""
圖形型態辨識（規則式）：依 IG「10 種最常見圖表形態」與學吧「趨勢線／趨勢判斷」的概念實作。

辨識項目：
  反轉：頭肩頂、頭肩底、雙頂(M頭)、雙底(W底)、圓底、上升楔形、下降楔形
  持續：杯柄、旗形／三角旗、上升三角形、下降三角形
  雙向：對稱三角形、矩形整理
  趨勢：上升通道、下降通道、趨勢破壞警訊（支撐一次比一次低／壓力一次比一次高）

回傳每個型態的名稱、偏多偏空、是否已突破或跌破確認、狀態說明、以及可畫在圖上的線（頸線、壓力線、支撐線）。
型態辨識是機械式近似，容易有誤判與失敗，需搭配成交量與停損，僅供研究參考，不構成投資建議。
"""
import numpy as np
import pandas as pd

HELP = {
    "頭肩頂": "由左肩、頭部、右肩三個高點組成，頭部最高。價格跌破連接兩個低點的頸線，常被視為由多轉空的反轉訊號。",
    "頭肩底": "與頭肩頂相反，由三個低點組成且頭部最低。價格突破頸線，常被視為由空轉多的反轉訊號。",
    "雙頂": "價格兩度攻到相近高點都無法突破，中間回檔的低點是頸線。跌破頸線代表上漲動能耗盡，偏空的反轉。",
    "雙底": "價格兩度探到相近低點都守住，中間反彈的高點是頸線。突破頸線代表跌勢結束，偏多的反轉。",
    "圓底": "價格由跌轉升的緩慢弧形，代表賣壓逐步耗盡、買盤慢慢進場。突破左側高點附近的壓力後，上漲較有續航力。",
    "杯柄": "圓底之後出現小幅回檔（柄），整理完成後突破壓力，多半是上升趨勢的延續。",
    "上升楔形": "高點與低點都往上，但上下兩條線逐漸收斂，上漲動能遞減。跌破下緣常見轉弱。",
    "下降楔形": "高點與低點都往下，但上下兩條線逐漸收斂，下跌動能遞減。突破上緣常見轉強。",
    "旗形／三角旗": "急漲或急跌之後的短期整理，像一面旗子。整理結束後，往原本方向突破的情形較常見。",
    "上升三角形": "壓力大致水平、支撐逐步墊高，買方愈來愈積極。向上突破壓力後，常延續上漲。",
    "下降三角形": "支撐大致水平、壓力逐步下移，賣方愈來愈積極。跌破支撐後，常延續下跌。",
    "對稱三角形": "高點愈來愈低、低點愈來愈高，價格被壓縮。多半順著原趨勢突破，但沒有明確趨勢時方向難料，要等突破再判斷。",
    "矩形整理": "價格在水平的上下沿之間來回，多空拉鋸。突破上沿偏多、跌破下沿偏空，突破前不宜預設方向。",
    "上升通道": "支撐與壓力都逐步墊高，上漲機率較高；若支撐不再墊高，就是趨勢可能轉弱的警訊。",
    "下降通道": "支撐與壓力都逐步下移，下跌機率較高；若壓力不再下移，就是跌勢可能緩和的警訊。",
    "上升趨勢破壞": "上升趨勢中，最近一個低點比前一個低點更低，墊高的規律被破壞。第一次可能是雜訊，連續出現就要提高風險意識。",
    "下降趨勢緩和": "下降趨勢中，最近一個高點比前一個高點更高，壓低的規律被破壞，跌勢可能趨緩，但仍需等突破確認。",
}
TAIL = "型態需等突破或跌破確認，也常失敗，請搭配成交量與停損。"


def pivots(d, n=3):
    h, l = d["High"].to_numpy(float), d["Low"].to_numpy(float)
    out = []
    for i in range(n, len(d) - n):
        if h[i] >= h[i - n:i + n + 1].max():
            out.append((i, float(h[i]), "H"))
        if l[i] <= l[i - n:i + n + 1].min():
            out.append((i, float(l[i]), "L"))
    out.sort(key=lambda x: x[0])
    merged = []
    for p in out:
        if merged and merged[-1][2] == p[2]:
            if (p[2] == "H" and p[1] > merged[-1][1]) or (p[2] == "L" and p[1] < merged[-1][1]):
                merged[-1] = p
        else:
            merged.append(p)
    return merged


def _res(name, bias, confirmed, status, lines, off):
    shift = lambda pts: [[int(i) + off, round(float(y), 2)] for i, y in pts]
    return {"name": name, "bias": bias, "confirmed": bool(confirmed), "status": status,
            "lines": [{"pts": shift(L["pts"]), "label": L["label"]} for L in lines], "help": HELP.get(name, "") + TAIL}


def _ln(x0, y0, x1, y1, label):
    return {"pts": [(x0, y0), (x1, y1)], "label": label}


def detect(df, lookback=140, n=3):
    """df 需含 High/Low/Close（依日期遞增）。回傳型態清單；座標為 df 內的 0-based 位置（含 lookback 位移）。"""
    df = df.dropna(subset=["High", "Low", "Close"])
    d = df.tail(lookback).reset_index(drop=True)
    off = len(df) - len(d)
    m = len(d)
    if m < 30:
        return []
    c = d["Close"].to_numpy(float)
    last = m - 1
    P = pivots(d, n)
    res = []

    # ── 雙頂／雙底 ──
    if len(P) >= 3:
        a, b, e = P[-3:]
        near = lambda x, y: abs(x - y) / ((x + y) / 2) <= 0.03
        if a[2] == "H" and b[2] == "L" and e[2] == "H" and near(a[1], e[1]) and e[0] - a[0] >= 8 \
                and (min(a[1], e[1]) - b[1]) / min(a[1], e[1]) >= 0.05 and last - e[0] <= 25:
            ok = c[-1] < b[1]
            res.append(_res("雙頂", -1, ok, f"{'已跌破' if ok else '形成中，頸線'} {b[1]:.2f}",
                            [_ln(a[0], a[1], e[0], e[1], "雙頂"), _ln(a[0], b[1], last, b[1], "頸線")], off))
        if a[2] == "L" and b[2] == "H" and e[2] == "L" and near(a[1], e[1]) and e[0] - a[0] >= 8 \
                and (b[1] - max(a[1], e[1])) / max(a[1], e[1]) >= 0.05 and last - e[0] <= 25:
            ok = c[-1] > b[1]
            res.append(_res("雙底", 1, ok, f"{'已突破' if ok else '形成中，頸線'} {b[1]:.2f}",
                            [_ln(a[0], a[1], e[0], e[1], "雙底"), _ln(a[0], b[1], last, b[1], "頸線")], off))

    # ── 頭肩頂／頭肩底 ──
    if len(P) >= 5:
        p1, p2, p3, p4, p5 = P[-5:]
        if last - p5[0] <= 30:
            if [x[2] for x in P[-5:]] == list("HLHLH") and p3[1] > max(p1[1], p5[1]) * 1.03 \
                    and abs(p1[1] - p5[1]) / ((p1[1] + p5[1]) / 2) <= 0.08:
                neck = p2[1] + (p4[1] - p2[1]) * (last - p2[0]) / max(p4[0] - p2[0], 1)
                ok = c[-1] < neck
                res.append(_res("頭肩頂", -1, ok, f"{'已跌破' if ok else '形成中，頸線現約'} {neck:.2f}",
                                [_ln(p2[0], p2[1], last, neck, "頸線")], off))
            if [x[2] for x in P[-5:]] == list("LHLHL") and p3[1] < min(p1[1], p5[1]) * 0.97 \
                    and abs(p1[1] - p5[1]) / ((p1[1] + p5[1]) / 2) <= 0.08:
                neck = p2[1] + (p4[1] - p2[1]) * (last - p2[0]) / max(p4[0] - p2[0], 1)
                ok = c[-1] > neck
                res.append(_res("頭肩底", 1, ok, f"{'已突破' if ok else '形成中，頸線現約'} {neck:.2f}",
                                [_ln(p2[0], p2[1], last, neck, "頸線")], off))

    # ── 圓底／杯柄 ──
    k = min(60, m)
    y = c[-k:]
    x = np.arange(k)
    if k >= 40:
        a2, b2, c2 = np.polyfit(x, y, 2)
        fit = a2 * x ** 2 + b2 * x + c2
        ss = ((y - fit) ** 2).sum()
        r2 = 1 - ss / max(((y - y.mean()) ** 2).sum(), 1e-9)
        vx = -b2 / (2 * a2) if a2 else -1
        rim = y[:5].mean()
        if a2 > 0 and 0.25 * k <= vx <= 0.75 * k and r2 >= 0.55 and (rim - fit.min()) / rim >= 0.08 and y[-1] >= 0.85 * rim:
            hi15 = y[-15:].max()
            handle = abs(hi15 - rim) / rim <= 0.04 and 0.03 <= (hi15 - y[-1]) / hi15 <= 0.12 and int(np.argmax(y[-15:])) < 12
            curve = [(m - k + int(t), fit[int(t)]) for t in np.linspace(0, k - 1, 9)]
            res.append(_res("杯柄" if handle else "圓底", 1, y[-1] > rim * 1.0 and not handle,
                            "柄部整理中，留意突破左緣高點" if handle else ("已回到左緣高點附近" if y[-1] > rim * 0.97 else "弧形右側回升中"),
                            [{"pts": curve, "label": "杯柄" if handle else "圓底"}, _ln(m - k, rim, last, rim, "左緣高點")], off))

    # ── 趨勢線型態：三角形、楔形、矩形、通道 ──
    win = 70
    H = [p for p in P if p[2] == "H" and p[0] >= m - win]
    L = [p for p in P if p[2] == "L" and p[0] >= m - win]
    if len(H) >= 2 and len(L) >= 2:
        x0 = min(H[0][0], L[0][0])
        if last - x0 >= 15:
            ph = np.polyfit([p[0] for p in H], [p[1] for p in H], 1)
            pl = np.polyfit([p[0] for p in L], [p[1] for p in L], 1)
            hi = lambda t: ph[0] * t + ph[1]
            lo = lambda t: pl[0] * t + pl[1]
            pxm = c[-30:].mean()
            sh, sl = ph[0] / pxm * 100, pl[0] / pxm * 100  # 每根K線的斜率（%）
            w0, w1 = hi(x0) - lo(x0), hi(last) - lo(last)
            conv = w1 / w0 if w0 > 0 else 9
            flat = lambda s: abs(s) < 0.04
            name = bias = None
            if w0 > 0 and 0 < conv < 0.75:
                if flat(sh) and sl > 0.05:
                    name, bias = "上升三角形", 1
                elif flat(sl) and sh < -0.05:
                    name, bias = "下降三角形", -1
                elif sh < -0.05 and sl > 0.05:
                    name, bias = "對稱三角形", 0
                elif sh > 0.05 and sl > sh:
                    name, bias = "上升楔形", -1
                elif sl < -0.05 and sh < sl:
                    name, bias = "下降楔形", 1
            elif w0 > 0 and 0.75 <= conv <= 1.3:
                if flat(sh) and flat(sl) and w1 / pxm >= 0.04:
                    name, bias = "矩形整理", 0
                elif sh > 0.12 and sl > 0.12:
                    name, bias = "上升通道", 1
                elif sh < -0.12 and sl < -0.12:
                    name, bias = "下降通道", -1
            if name:
                up_b, dn_b = c[-1] > hi(last) * 1.005, c[-1] < lo(last) * 0.995
                broke = "向上突破" if up_b else "向下跌破" if dn_b else ""
                ok = (up_b and bias >= 0 and name not in ("上升楔形",)) or (dn_b and bias <= 0 and name not in ("下降楔形",))
                res.append(_res(name, bias, ok and name not in ("上升通道", "下降通道"), broke or "形成中",
                                [_ln(x0, hi(x0), last, hi(last), "壓力線"), _ln(x0, lo(x0), last, lo(last), "支撐線")], off))
    # ── 趨勢破壞警訊（學吧）──
    Ls, Hs = [p for p in P if p[2] == "L"], [p for p in P if p[2] == "H"]
    if len(Ls) >= 3 and Ls[-3][1] < Ls[-2][1] and Ls[-1][1] < Ls[-2][1] and last - Ls[-1][0] <= 30:
        res.append(_res("上升趨勢破壞", -1, False, f"最近低點 {Ls[-1][1]:.2f} 低於前低 {Ls[-2][1]:.2f}",
                        [_ln(Ls[-2][0], Ls[-2][1], last, Ls[-2][1], "前低")], off))
    if len(Hs) >= 3 and Hs[-3][1] > Hs[-2][1] and Hs[-1][1] > Hs[-2][1] and last - Hs[-1][0] <= 30:
        res.append(_res("下降趨勢緩和", 1, False, f"最近高點 {Hs[-1][1]:.2f} 高於前高 {Hs[-2][1]:.2f}",
                        [_ln(Hs[-2][0], Hs[-2][1], last, Hs[-2][1], "前高")], off))

    # ── 旗形／三角旗：急漲急跌後的短整理 ──
    if m >= 30:
        h, l = d["High"].to_numpy(float), d["Low"].to_numpy(float)
        for w in range(5, 21):
            base = c[m - w - 1]
            prior = base / c[max(0, m - w - 21)] - 1
            seg_hi, seg_lo = h[-w:].max(), l[-w:].min()
            if abs(prior) >= 0.08 and (seg_hi - seg_lo) <= 0.5 * abs(prior) * base:
                up_b, dn_b = c[-1] > seg_hi * 0.995 and prior > 0, c[-1] < seg_lo * 1.005 and prior < 0
                res.append(_res("旗形／三角旗", 1 if prior > 0 else -1, up_b or dn_b,
                                ("已往原方向突破" if (up_b or dn_b) else f"整理 {w} 根，前一波{'上漲' if prior > 0 else '下跌'} {abs(prior) * 100:.0f}%"),
                                [_ln(m - w, seg_hi, last, seg_hi, "整理區上緣"), _ln(m - w, seg_lo, last, seg_lo, "整理區下緣")], off))
                break
    return res


def short(p):
    return p["name"] + ("(多)" if p["bias"] > 0 else "(空)" if p["bias"] < 0 else "(中)") + ("✓" if p["confirmed"] else "")


def score(res, cap=3):
    """已確認的型態計 ±2，形成中計 ±1，合計限制在 ±cap。"""
    return max(-cap, min(cap, sum(p["bias"] * (2 if p["confirmed"] else 1) for p in res)))
