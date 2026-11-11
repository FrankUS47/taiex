# -*- coding: utf-8 -*-
"""
預測紀錄、對照實際走勢，並保守地校準各組成的權重。

做法：
  1. 每次產生大盤報告都記錄「為哪一天的盤」做的預測（盤前預測：目標日 09:00 之前最後一次的結果）與各組成分數。
  2. 目標日收盤後，用實際漲跌回填結果，統計每個組成（技術面、K棒型態、圖形型態、國際與資金面、消息面）的方向命中率。
  3. 權重 = 1 ＋（2×命中率 − 1，限制在 0.5～1.5 之間）× min(1, 樣本數/20)。樣本少時幾乎維持 1.0，避免被少數幾天帶偏。
  4. 盤中：把「盤前預測」與「目前實際漲跌」依已開盤的時間比例混合，產生盤中修正（開得越久，實際走勢占比越高）。
紀錄存在 cache/predictions.json（GitHub Actions 會隨快取保留）。
這只是依自己的歷史命中率做的簡單校準，樣本少時不具統計意義，不保證未來更準，僅供研究參考，不構成投資建議。
"""
import datetime as dt
import json
from pathlib import Path

CACHE = Path(__file__).with_name("cache")
LOG = CACHE / "predictions.json"
COMPS = ["技術面", "K棒型態", "圖形型態", "國際與資金面", "消息面"]
OPEN, CLOSE = (9, 0), (13, 30)


def load():
    try:
        d = json.loads(LOG.read_text(encoding="utf-8"))
        d.setdefault("entries", [])
        return d
    except Exception:  # noqa
        return {"entries": []}


def save(d):
    CACHE.mkdir(exist_ok=True)
    cutoff = (dt.date.today() - dt.timedelta(days=200)).isoformat()
    d["entries"] = [e for e in d["entries"] if e["for_date"] >= cutoff]
    LOG.write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def target_date(now):
    """預測的目標交易日：平日 13:30 前是今天，否則是下一個平日（未考慮國定假日）。"""
    d = now.date()
    if now.weekday() < 5 and (now.hour, now.minute) < CLOSE:
        return d
    d += dt.timedelta(days=1)
    while d.weekday() >= 5:
        d += dt.timedelta(days=1)
    return d


def session_fraction(now):
    """已開盤時間占 09:00～13:30 的比例（0～1）；盤前為 0，收盤後為 1。"""
    t = now.hour * 60 + now.minute
    a, b = OPEN[0] * 60, CLOSE[0] * 60 + CLOSE[1]
    return 0.0 if t <= a else 1.0 if t >= b else (t - a) / (b - a)


def label_of(x):
    return "偏多" if x >= 6 else "小幅偏多" if x >= 2 else "區間震盪" if x >= -1 else "小幅偏空" if x >= -5 else "偏空"


def update_outcomes(d, df, now):
    """用日K回填已結束的目標日實際漲跌；當天要等 13:45 之後才定案。"""
    idx = {x.date().isoformat(): i for i, x in enumerate(df.index)}
    c = df["Close"]
    for e in d["entries"]:
        if e.get("actual_pct") is not None or e["for_date"] not in idx or idx[e["for_date"]] == 0:
            continue
        done = e["for_date"] < now.date().isoformat() or (now.hour, now.minute) >= (13, 45)
        if done:
            i = idx[e["for_date"]]
            e["actual_pct"] = round(float(c.iloc[i] / c.iloc[i - 1] - 1) * 100, 2)


def weights(d):
    """依已完成的預測計算各組成權重。回傳 (權重, 統計, 整體命中率資訊)。"""
    done = [e for e in d["entries"] if e.get("actual_pct") is not None and abs(e["actual_pct"]) >= 0.05]
    w, stat = {}, {}
    for k in COMPS:
        n = h = 0
        for e in done:
            v = e["comps"].get(k, 0)
            if v:
                n += 1
                h += (v > 0) == (e["actual_pct"] > 0)
        hit = h / n if n else None
        raw = max(0.5, min(1.5, 2 * hit)) if hit is not None else 1.0
        w[k] = round(1 + (raw - 1) * min(1.0, n / 20), 2)
        stat[k] = {"n": n, "hit": hit}
    calls = [e for e in done if abs(e.get("weighted", e["raw"])) >= 2]
    ok = sum(((e.get("weighted", e["raw"]) > 0) == (e["actual_pct"] > 0)) for e in calls)
    return w, stat, {"calls": len(calls), "ok": ok, "done": len(done)}


def record(d, now, comps, raw, weighted, base_close, w):
    """記錄預測。目標日 09:00 之前的執行才會覆蓋盤前預測；開盤後不改寫盤前預測。"""
    fd = target_date(now).isoformat()
    pre_open = fd > now.date().isoformat() or (now.hour, now.minute) < OPEN
    old = next((e for e in d["entries"] if e["for_date"] == fd), None)
    if old and not pre_open:
        return old
    e = {"for_date": fd, "made_at": now.strftime("%Y-%m-%d %H:%M"), "comps": {k: int(v) for k, v in comps.items()},
         "raw": int(raw), "weighted": round(weighted, 1), "label": label_of(weighted), "base_close": round(base_close, 2), "w": w}
    if old:
        old.update(e)
        return old
    d["entries"].append(e)
    return e


def live_adjust(pre_weighted, pct_now, now):
    """盤中修正：把盤前預測分數與目前實際漲跌換算的分數，依已開盤時間比例混合。"""
    f = session_fraction(now)
    live_pts = max(-6.0, min(6.0, pct_now / 0.4))
    wl = 0.0 if f <= 0 else min(0.85, 0.3 + 0.7 * f)
    return round(pre_weighted * (1 - wl) + live_pts * wl, 1), round(wl, 2), round(live_pts, 1)
