# -*- coding: utf-8 -*-
"""
走步驗證（walk-forward）回測與歷史類似情境

做法（對應「用 9 日前的資料預測 10 日、與實際比對後修正參數，再用 10 日前的資料預測 11 日……」）：
  1. 對過去每一個交易日 T，只用「T 之前」已知的資料算出各構面分數：
       技術面、K棒型態、圖形型態（用台股加權日K，只取到 T-1 收盤）
       國際與資金面（美股、費半、台積電ADR、EWT、VIX、匯率、美債、原油、歐亞股市等，只取到 T-1 日曆日為止的收盤，
       因為美股 T-1 的盤在台灣時間 T 日清晨收盤，開盤前已知）
  2. 用目前的權重預測 T 日漲跌幅，再與 T 日實際漲跌幅比對（這一筆是「樣本外」預測，權重還沒看過 T 日結果）。
  3. 依誤差用 NLMS 微調各構面權重（往 1.0 拉回，避免被單日雜訊帶偏），再進入下一天。
  4. 一路從約 6 年前走到昨天，得到：樣本外方向命中率、平均誤差、每個分數區間「隔日實際漲跌」的統計、最終權重與校準係數。
  5. 歷史類似情境：以各構面分數與近期漲跌、美股、費半、VIX 做最近鄰比對；另以最近 20 日走勢的形狀找過去相似的區間，
     看它們之後 1 日與 5 日的結果。

限制：新聞情緒、台指期、逐日法人沒有長期歷史，回測不含這些；結果只是歷史統計，不保證未來；
特徵多、樣本相鄰高度相關，容易過度解讀。僅供研究參考，不構成投資建議。

用法：python backtest.py [--period 6y] [--reuse-hours 6]
輸出：cache/model.json（供大盤報告讀取）
"""
import argparse
import datetime as dt
import json
from pathlib import Path

import numpy as np
import pandas as pd

import chart_patterns as CP
import predictor as PR
import taiex_analyzer as T

CACHE_DIR = Path(__file__).with_name("cache")
MODEL = CACHE_DIR / "model.json"
GROUPS = ["技術面", "K棒型態", "圖形型態", "國際與資金面"]
FEATS = ["技術面", "K棒型態", "圖形型態", "國際與資金面", "前1日漲跌", "5日漲跌", "20日漲跌", "美股三大指數", "費半", "VIX"]
MU, LAM, K0 = 0.05, 0.01, 0.12
LABELS = ["偏多", "小幅偏多", "區間震盪", "小幅偏空", "偏空"]


def load_series(period="6y"):
    tw = T.hist("^TWII", period)
    out = {}
    for name, sym in T.MARKETS.items():
        try:
            out[name] = T.hist(sym, period)["Close"].dropna()
        except Exception as e:  # noqa
            print(f"  ✗ {name}({sym})：{str(e)[:60]}")
    return tw, out


def foreign_q(series, target_date):
    """目標交易日 T 開盤前已知的海外報價：每個市場取日期 <= T-1（日曆日）的最後一根日K。"""
    cut = pd.Timestamp(target_date) - pd.Timedelta(days=1)
    Q = {}
    for name, s in series.items():
        i = s.index.searchsorted(cut, side="right") - 1
        if i < 1:
            continue
        d = s.index[i]
        if (cut - d).days > 7:
            continue
        Q[name] = {"last": float(s.iloc[i]), "pct": float((s.iloc[i] / s.iloc[i - 1] - 1) * 100), "date": d.date()}
    return Q


def comparable_groups(sub, Q):
    """回測可得的四個構面分數（不含新聞、台指期、法人）。sub 是到前一個交易日為止的加權日K。"""
    ts = sum(p for p, _ in T.tech(sub)[0])
    ks = max(-3, min(3, sum(p for p, _ in T.kbar(sub))))
    ps = CP.score(CP.detect(sub))
    ms = sum(p for p, _ in T.macro_score(Q, None, None, None, None, sub.index[-1].date())[0])
    return {"技術面": ts, "K棒型態": ks, "圖形型態": ps, "國際與資金面": ms}


def feature_vector(sub, Q, groups):
    c = sub["Close"].to_numpy(float)
    us = [Q[k]["pct"] for k in ("S&P500", "道瓊", "那斯達克") if k in Q]
    return [groups[g] for g in GROUPS] + [
        (c[-1] / c[-2] - 1) * 100, (c[-1] / c[-6] - 1) * 100, (c[-1] / c[-21] - 1) * 100,
        float(np.mean(us)) if us else 0.0, Q.get("費城半導體", {}).get("pct", 0.0), Q.get("VIX恐慌指數", {}).get("last", 18.0)]


def run(period="6y", start=260):
    tw, series = load_series(period)
    w, k = {g: 1.0 for g in GROUPS}, K0
    rows, closes, dates = [], tw["Close"].to_numpy(float), tw.index
    print(f"加權日K {len(tw)} 根（{dates[0].date()}～{dates[-1].date()}），海外序列 {len(series)} 條；走步驗證從第 {start} 根開始…")
    for j in range(start, len(tw)):
        actual = (closes[j] / closes[j - 1] - 1) * 100
        Q = foreign_q(series, dates[j].date())
        sub = tw.iloc[:j]
        g = comparable_groups(sub, Q)
        sc = np.array([g[x] for x in GROUPS], float)
        wv = np.array([w[x] for x in GROUPS])
        wsc = float(wv @ sc)
        rows.append({"date": dates[j].date().isoformat(), "g": [int(v) for v in sc], "wsc": wsc, "pred": k * wsc, "actual": float(actual),
                     "f": [round(float(v), 3) for v in feature_vector(sub, Q, g)]})
        x = sc * k                                   # 看過 T 日結果之後才調整權重（NLMS）
        err = actual - float(wv @ x)
        nx = float(x @ x) + 0.01
        for gi, xi in zip(GROUPS, x):
            w[gi] = min(2.5, max(0.0, w[gi] + MU * err * xi / nx - LAM * (w[gi] - 1.0)))
        if len(rows) >= 30 and len(rows) % 10 == 0:  # 每 10 筆用最近 120 筆校準「每 1 分約對應幾 %」
            S = np.array([r["wsc"] for r in rows[-120:]])
            Rr = np.array([r["actual"] for r in rows[-120:]])
            if float(S @ S) > 0:
                k = float(min(0.5, max(0.02, (S @ Rr) / (S @ S))))
    return rows, w, k, dates


def bucket_stats(rows):
    out = []
    for lab in LABELS:
        a = np.array([r["actual"] for r in rows if PR.label_of(r["wsc"]) == lab])
        out.append({"label": lab, "n": int(len(a)), "up": float(np.mean(a > 0)) if len(a) else None,
                    "avg": float(a.mean()) if len(a) else None, "med": float(np.median(a)) if len(a) else None})
    return out


def summarize(rows):
    S = np.array([r["wsc"] for r in rows])
    A = np.array([r["actual"] for r in rows])
    P = np.array([r["pred"] for r in rows])
    m = np.abs(S) >= 2
    return {"n": int(len(rows)), "first": rows[0]["date"], "last": rows[-1]["date"],
            "calls": int(m.sum()), "hit": float(np.mean(np.sign(S[m]) == np.sign(A[m]))) if m.any() else None,
            "base_up": float(np.mean(A > 0)), "mae": float(np.mean(np.abs(P - A))), "mae0": float(np.mean(np.abs(A))),
            "corr": float(np.corrcoef(S, A)[0, 1]) if len(S) > 5 and S.std() > 0 else None}


def build_model(period="6y"):
    rows, w, k, dates = run(period)
    F = np.array([r["f"] for r in rows], float)
    model = {"generated": dt.datetime.now().isoformat(timespec="seconds"), "period": period,
             "weights": {g: round(w[g], 3) for g in GROUPS}, "k": round(k, 4), "oos": summarize(rows),
             "buckets": bucket_stats(rows), "mean": [round(float(v), 4) for v in F.mean(0)],
             "std": [round(float(v) if v > 1e-6 else 1.0, 4) for v in F.std(0)],
             "feats": [[r["date"]] + r["f"] + [round(r["actual"], 3)] for r in rows]}
    return model


# ───────────────────────── 歷史類似情境（給大盤報告用）─────────────────────────
def analog_knn(model, cur, k=25):
    """cur 為目前的特徵向量（順序同 FEATS）。回傳最近鄰日期的隔日漲跌分布。"""
    F = np.array([r[1:1 + len(FEATS)] for r in model["feats"]], float)
    A = np.array([r[-1] for r in model["feats"]], float)
    z = (F - np.array(model["mean"])) / np.array(model["std"])
    zc = (np.array(cur, float) - np.array(model["mean"])) / np.array(model["std"])
    d = np.sqrt(((z - zc) ** 2).sum(1))
    idx = np.argsort(d)[:k]
    a = A[idx]
    return {"k": int(len(idx)), "up": float(np.mean(a > 0)), "avg": float(a.mean()), "med": float(np.median(a)),
            "p10": float(np.percentile(a, 10)), "p90": float(np.percentile(a, 90)),
            "near": [{"date": model["feats"][i][0], "dist": float(d[i]), "next": float(A[i])} for i in idx[:5]]}


def shape_analogs(df, window=20, top=5, gap=10):
    """最近 window 日走勢（相對起點的累積報酬路徑）與過去各區間比對，取最相似且互不重疊的幾段，看之後 1 日與 5 日。"""
    c = df["Close"].to_numpy(float)
    if len(c) < window + 15:
        return []
    cur = c[-window:] / c[-window] - 1
    cands = []
    for e in range(window - 1, len(c) - 6):
        path = c[e - window + 1:e + 1] / c[e - window + 1] - 1
        cands.append((float(np.sqrt(np.mean((path - cur) ** 2))), e))
    cands.sort()
    out = []
    for dist, e in cands:
        if all(abs(e - o["e"]) >= gap + window for o in out):
            out.append({"e": e, "dist": dist, "start": df.index[e - window + 1].date().isoformat(), "end": df.index[e].date().isoformat(),
                        "d1": (c[e + 1] / c[e] - 1) * 100, "d5": (c[e + 5] / c[e] - 1) * 100})
        if len(out) >= top:
            break
    return out


def load_model():
    try:
        return json.loads(MODEL.read_text(encoding="utf-8"))
    except Exception:  # noqa
        return None


def main():
    ap = argparse.ArgumentParser(description="走步驗證回測")
    ap.add_argument("--period", default="6y")
    ap.add_argument("--reuse-hours", type=float, default=0)
    a = ap.parse_args()
    if MODEL.exists() and a.reuse_hours > 0:
        try:
            age = (dt.datetime.now() - dt.datetime.fromisoformat(json.loads(MODEL.read_text(encoding="utf-8"))["generated"])).total_seconds() / 3600
            if age < a.reuse_hours:
                print(f"回測模型只有 {age:.1f} 小時，略過重算。")
                return
        except Exception:  # noqa
            pass
    model = build_model(a.period)
    CACHE_DIR.mkdir(exist_ok=True)
    MODEL.write_text(json.dumps(model, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    o = model["oos"]
    print(f"完成：樣本 {o['n']} 天（{o['first']}～{o['last']}）｜方向性預判 {o['calls']} 次，命中率 {o['hit'] if o['hit'] is None else round(o['hit'] * 100, 1)}%"
          f"（基準：隔日上漲比率 {o['base_up'] * 100:.1f}%）｜MAE {o['mae']:.2f}%（基準 {o['mae0']:.2f}%）")
    print("最終權重：", model["weights"], "｜k =", model["k"], "→", MODEL)


if __name__ == "__main__":
    main()
