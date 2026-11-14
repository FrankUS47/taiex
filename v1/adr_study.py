# -*- coding: utf-8 -*-
"""
台灣公司在美國的 ADR 與台股隔日走勢的關係研究（歷史統計＋逐步校正）

對象（美股掛牌的台灣公司 ADR，對應的台股）：
  TSM 台積電(2330，1 ADR＝5 股)、UMC 聯電(2303，1 ADR＝5 股)、ASX 日月光投控(3711，1 ADR＝2 股)、CHT 中華電信(2412，1 ADR＝10 股)
  （ADR 清單與換股比例可能變動，請以發行公司公告為準）

做法：
  1. 對台股每個交易日 T，配上「T 的日曆前一天（含）以前」最後一根 ADR 日K（ADR 的美股盤在台灣時間 T 日清晨收盤，開盤前已知），
     算出前夜 ADR 漲跌幅，與 T 日台股的「開盤跳空」（開盤價相對前一日收盤）與「收盤漲跌」比對。
  2. 統計相關係數、迴歸斜率（ADR 每漲跌 1%，台股開盤約跟著動幾 %）、方向一致率，
     並依 ADR 漲跌幅大小分區（<0.5%、0.5～1%、1～2%、2～3%、>3%），看哪個幅度以上台股才明顯跟著 ADR 走。
  3. 逐步校正（走步）：依時間順序，每天先用「截至前一天」的參數預測開盤跳空，再與實際比對，之後才更新參數
     （指數加權迴歸，近期資料權重較高），得到樣本外的命中率與誤差，以及最新的 α、β。
  4. 另算 ADR 折溢價（ADR 價格×匯率÷換股比例，相對台股收盤）。

限制：只用日K，無法分辨開盤前後的盤中消息；台股同時受大盤、匯率、法人與消息影響，ADR 只是其中一項。
僅供研究參考，不構成投資建議。

用法：python adr_study.py [--period 6y] [--reuse-hours 6]；輸出 cache/adr.json
"""
import argparse
import datetime as dt
import json
from pathlib import Path

import numpy as np
import pandas as pd

import taiex_analyzer as T

CACHE_DIR = Path(__file__).with_name("cache")
ADRC = CACHE_DIR / "adr.json"
PAIRS = [("TSM", "2330.TW", "台積電", "2330", 5), ("UMC", "2303.TW", "聯電", "2303", 5),
         ("ASX", "3711.TW", "日月光投控", "3711", 2), ("CHT", "2412.TW", "中華電信", "2412", 10)]
BUCKETS = [(0, 0.5), (0.5, 1), (1, 2), (2, 3), (3, 99)]
LAM = 0.995


def aligned(adr, tw):
    """每個台股交易日 T 配上「日曆日 <= T-1」的最後一根 ADR 日K；同一根 ADR 不重複使用。回傳 (日期, ADR漲跌%, 台股開盤跳空%, 台股收盤漲跌%)。"""
    ac = adr["Close"].dropna()
    apct = ac.pct_change() * 100
    rows, last_used = [], None
    for i in range(1, len(tw)):
        t = tw.index[i]
        cut = t - pd.Timedelta(days=1)
        j = ac.index.searchsorted(cut, side="right") - 1
        if j < 1 or (cut - ac.index[j]).days > 5 or ac.index[j] == last_used:
            continue
        last_used = ac.index[j]
        pc = float(tw["Close"].iloc[i - 1])
        if pc <= 0:
            continue
        rows.append((t.date().isoformat(), float(apct.iloc[j]), (float(tw["Open"].iloc[i]) / pc - 1) * 100, (float(tw["Close"].iloc[i]) / pc - 1) * 100))
    return rows


def bucket_stats(rows):
    a, g, c = (np.array([r[k] for r in rows], float) for k in (1, 2, 3))
    out = []
    for lo, hi in BUCKETS:
        m = (np.abs(a) >= lo) & (np.abs(a) < hi)
        n = int(m.sum())
        out.append({"lo": lo, "hi": hi, "n": n,
                    "agree": float(np.mean(np.sign(a[m]) == np.sign(g[m]))) if n else None,
                    "agree_close": float(np.mean(np.sign(a[m]) == np.sign(c[m]))) if n else None,
                    "aligned_gap": float(np.mean(g[m] * np.sign(a[m]))) if n else None,
                    "abs_gap": float(np.mean(np.abs(g[m]))) if n else None})
    return out


def walk_forward(rows):
    """依時間順序：先用截至前一天的指數加權迴歸預測開盤跳空，再與實際比對，之後才更新參數。"""
    sw = sx = sy = sxx = sxy = 0.0
    preds = []
    for _, a, g, _c in rows:
        if sw > 30:
            den = sxx * sw - sx * sx
            beta = (sxy * sw - sx * sy) / den if abs(den) > 1e-9 else 0.0
            preds.append((sy - beta * sx) / sw + beta * a)
        else:
            preds.append(None)
        sw, sx, sy, sxx, sxy = sw * LAM + 1, sx * LAM + a, sy * LAM + g, sxx * LAM + a * a, sxy * LAM + a * g
    den = sxx * sw - sx * sx
    beta = (sxy * sw - sx * sy) / den if abs(den) > 1e-9 else 0.0
    alpha = (sy - beta * sx) / sw if sw else 0.0
    ok = [(p, r[2], r[1]) for p, r in zip(preds, rows) if p is not None]
    if not ok:
        return alpha, beta, None
    P, G, A = (np.array(x, float) for x in zip(*ok))
    m = np.abs(A) >= 0.3
    resid = float(np.std(G - P))
    return alpha, beta, {"n": len(ok), "calls": int(m.sum()), "hit": float(np.mean(np.sign(P[m]) == np.sign(G[m]))) if m.any() else None,
                         "mae": float(np.mean(np.abs(P - G))), "mae0": float(np.mean(np.abs(G))), "sigma": resid}


def study_pair(adr_sym, tw_sym, name, code, ratio, fx, period):
    adr, tw = T.hist(adr_sym, period), T.hist(tw_sym, period)
    rows = aligned(adr, tw)
    if len(rows) < 60:
        raise RuntimeError(f"樣本太少（{len(rows)}）")
    a, g, c = (np.array([r[k] for r in rows], float) for k in (1, 2, 3))
    alpha, beta, oos = walk_forward(rows)
    bk = bucket_stats(rows)
    thr = next((b["lo"] for b in bk if b["n"] >= 15 and b["agree"] is not None and b["agree"] >= 0.65), None)
    prem = None
    if fx is not None:
        ad = adr["Close"].dropna()
        s = pd.concat([ad, tw["Close"], fx], axis=1, join="inner").dropna()
        s.columns = ["adr", "tw", "fx"]
        if len(s) > 60:
            p = (s["adr"] * s["fx"] / ratio) / s["tw"] - 1
            p1 = p.tail(250)
            prem = {"now": float(p.iloc[-1] * 100), "mean": float(p1.mean() * 100), "std": float(p1.std() * 100), "date": s.index[-1].date().isoformat()}
    return {"adr": adr_sym, "name": name, "tw": tw_sym, "code": code, "ratio": ratio, "n": len(rows), "first": rows[0][0], "last": rows[-1][0],
            "corr": float(np.corrcoef(a, g)[0, 1]), "corr_close": float(np.corrcoef(a, c)[0, 1]), "r2": float(np.corrcoef(a, g)[0, 1] ** 2),
            "agree": float(np.mean(np.sign(a) == np.sign(g))), "alpha": alpha, "beta": beta, "oos": oos, "buckets": bk, "threshold": thr,
            "recent": [{"date": r[0], "adr": round(r[1], 2), "gap": round(r[2], 2), "close": round(r[3], 2)} for r in rows[-12:]], "premium": prem}


def build_model(period="6y"):
    fx = None
    try:
        fx = T.hist("TWD=X", period)["Close"].dropna()
    except Exception as e:  # noqa
        print("  ✗ 匯率 TWD=X：", str(e)[:60])
    pairs = {}
    for adr_sym, tw_sym, name, code, ratio in PAIRS:
        try:
            pairs[adr_sym] = study_pair(adr_sym, tw_sym, name, code, ratio, fx, period)
            p = pairs[adr_sym]
            print(f"  {adr_sym}→{code}{name}：樣本 {p['n']}，相關 {p['corr']:.2f}，β={p['beta']:.2f}，方向一致率 {p['agree'] * 100:.0f}%")
        except Exception as e:  # noqa
            print(f"  ✗ {adr_sym}→{code}：{type(e).__name__}: {str(e)[:80]}")
    if not pairs:
        raise RuntimeError("沒有任何 ADR 研究成功")
    return {"generated": dt.datetime.now().isoformat(timespec="seconds"), "period": period, "pairs": pairs}


def load_model():
    try:
        return json.loads(ADRC.read_text(encoding="utf-8"))
    except Exception:  # noqa
        return None


def main():
    ap = argparse.ArgumentParser(description="ADR 與台股隔日走勢關係研究")
    ap.add_argument("--period", default="6y")
    ap.add_argument("--reuse-hours", type=float, default=0)
    a = ap.parse_args()
    if ADRC.exists() and a.reuse_hours > 0:
        try:
            age = (dt.datetime.now() - dt.datetime.fromisoformat(json.loads(ADRC.read_text(encoding="utf-8"))["generated"])).total_seconds() / 3600
            if age < a.reuse_hours:
                print(f"ADR 研究只有 {age:.1f} 小時，略過重算。")
                return
        except Exception:  # noqa
            pass
    model = build_model(a.period)
    CACHE_DIR.mkdir(exist_ok=True)
    ADRC.write_text(json.dumps(model, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print("完成 →", ADRC)


if __name__ == "__main__":
    main()
