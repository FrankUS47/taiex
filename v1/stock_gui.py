# -*- coding: utf-8 -*-
"""
台股分析工具：圖形選單介面（tkinter）
與 taiex_analyzer.py、stock_screener.py 放在同一資料夾，直接執行本檔即可。
分頁：① 條件篩選  ② 指定個股評價  ③ 大盤分析（可自動更新）
評等為規則式量化評分，不是投資建議。
"""
import json
import queue
import re
import sys
import threading
import tkinter as tk
import traceback
import webbrowser
from contextlib import redirect_stdout
from pathlib import Path
from tkinter import messagebox, ttk

import price_levels as PL
import stock_screener as SS
import taiex_analyzer as TA


FAV_FILE = Path(__file__).with_name("favorites.json")


class LogWriter:
    def __init__(self, q):
        self.q = q

    def write(self, t):
        if t:
            self.q.put(t)

    def flush(self):
        pass


def opt(var, name):
    """欄位值轉數字；空白或「不限」視為不設條件。"""
    t = var.get().strip()
    if t in ("", "不限"):
        return None
    try:
        return float(t)
    except ValueError:
        raise ValueError(f"「{name}」請輸入數字，或選「不限」")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("台股分析工具")
        self.geometry("780x660")
        self.q, self.busy, self.stop = queue.Queue(), False, threading.Event()
        self.auto_open = tk.BooleanVar(value=True)

        nb = ttk.Notebook(self)
        nb.pack(fill="x", padx=10, pady=(10, 4))
        self.build_screen(nb)
        self.build_stock(nb)
        self.build_market(nb)
        self.build_fav(nb)
        self.build_levels(nb)

        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=10)
        ttk.Checkbutton(bar, text="完成後自動開啟報告", variable=self.auto_open).pack(side="left")
        ttk.Button(bar, text="開啟個股報告", command=lambda: self.open_file(SS.OUT_FILE)).pack(side="right", padx=2)
        ttk.Button(bar, text="開啟大盤報告", command=lambda: self.open_file(TA.OUT_FILE)).pack(side="right", padx=2)

        self.status = tk.StringVar(value="就緒")
        ttk.Label(self, textvariable=self.status, foreground="#2b6cb0").pack(anchor="w", padx=10, pady=(6, 0))
        box = ttk.Frame(self)
        box.pack(fill="both", expand=True, padx=10, pady=6)
        self.log = tk.Text(box, height=16, wrap="word", font=("Consolas", 10))
        sb = ttk.Scrollbar(box, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        self.log.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        ttk.Label(self, text="僅供研究參考，不構成投資建議。", foreground="#6b7385").pack(pady=(0, 6))
        self.after(120, self.poll)

    # ── 分頁 ──
    def field(self, parent, r, label, values, default):
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky="w", padx=8, pady=5)
        v = tk.StringVar(value=default)
        ttk.Combobox(parent, textvariable=v, values=values, width=14).grid(row=r, column=1, sticky="w", pady=5)
        return v

    def build_screen(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text="① 條件篩選")
        self.v_price = self.field(f, 0, "股價上限（元）", ["不限", "20", "30", "50", "100", "200", "500"], "50")
        self.v_vol = self.field(f, 1, "最低成交量（張）", ["500", "1000", "3000", "5000", "10000"], "1000")
        self.v_pe = self.field(f, 2, "本益比上限", ["不限", "10", "15", "20", "30"], "不限")
        self.v_yld = self.field(f, 3, "殖利率下限（%）", ["不限", "2", "3", "4", "5", "6"], "不限")
        self.v_top = self.field(f, 4, "深入評價檔數", ["5", "10", "15", "20", "30"], "10")
        self.v_total = self.field(f, 5, "評價後總分下限", ["不限", "0", "3", "8"], "不限")
        self.v_growth = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text="只留營收年成長為正的（評價後再篩）", variable=self.v_growth).grid(row=6, column=0, columnspan=2, sticky="w", padx=8)
        ttk.Label(f, text="可直接輸入數字；檔數越多越慢。僅篩選上市股票。後兩項只對前 N 檔深入評價的結果再篩。", foreground="#6b7385").grid(row=7, column=0, columnspan=3, sticky="w", padx=8)
        ttk.Button(f, text="▶ 執行篩選與評價", command=self.do_screen).grid(row=8, column=0, columnspan=2, sticky="w", padx=8, pady=10)

    def build_stock(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text="② 指定個股評價")
        ttk.Label(f, text="股票代號（可多檔，用空白或逗號分隔）").grid(row=0, column=0, sticky="w", padx=8, pady=(10, 4))
        self.v_codes = tk.StringVar(value="2330 2317")
        ttk.Entry(f, textvariable=self.v_codes, width=40).grid(row=1, column=0, sticky="w", padx=8)
        ttk.Button(f, text="▶ 評價這些股票", command=self.do_stock).grid(row=2, column=0, sticky="w", padx=8, pady=12)

    def build_market(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text="③ 大盤分析")
        ttk.Label(f, text="自動更新間隔（秒，建議 ≥ 60）").grid(row=0, column=0, sticky="w", padx=8, pady=8)
        self.v_iv = tk.StringVar(value="120")
        ttk.Spinbox(f, from_=30, to=3600, increment=30, textvariable=self.v_iv, width=8).grid(row=0, column=1, sticky="w")
        ttk.Button(f, text="▶ 分析一次", command=self.do_market).grid(row=1, column=0, sticky="w", padx=8, pady=6)
        ttk.Button(f, text="⟳ 開始自動更新", command=self.do_watch).grid(row=1, column=1, sticky="w")
        ttk.Button(f, text="■ 停止", command=self.stop.set).grid(row=1, column=2, sticky="w", padx=6)

    def build_fav(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text="④ 常用清單")
        self.favs = self.load_favs()
        self.lb = tk.Listbox(f, height=6, selectmode="extended", width=18)
        self.lb.grid(row=0, column=0, rowspan=4, padx=8, pady=8)
        for c in self.favs:
            self.lb.insert("end", c)
        self.v_new = tk.StringVar()
        ttk.Entry(f, textvariable=self.v_new, width=12).grid(row=0, column=1, sticky="w")
        ttk.Button(f, text="＋ 新增代號", command=self.fav_add).grid(row=0, column=2, sticky="w", padx=4)
        ttk.Button(f, text="－ 刪除選取", command=self.fav_del).grid(row=1, column=1, columnspan=2, sticky="w")
        ttk.Button(f, text="▶ 評價選取（未選則全部）", command=self.fav_run).grid(row=2, column=1, columnspan=2, sticky="w", pady=4)
        ttk.Label(f, text="清單存在 favorites.json，下次開啟仍在。", foreground="#6b7385").grid(row=3, column=1, columnspan=2, sticky="w")

    def build_levels(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text="⑤ 停損停利")
        self.v_lc = self.field(f, 0, "股票代號", [], "2330")
        self.v_lb = self.field(f, 1, "你的買進價（元）", [], "")
        self.v_ll = self.field(f, 2, "張數（可空白）", ["1", "2", "5", "10"], "")
        self.v_lm = self.field(f, 3, "可承受最大虧損（%）", ["3", "5", "7", "10"], "7")
        ttk.Button(f, text="▶ 試算參考價位", command=self.do_levels).grid(row=4, column=0, columnspan=2, sticky="w", padx=8, pady=8)
        ttk.Label(f, text="結果顯示在下方記錄區；僅為依歷史價量算出的參考，不含手續費與證交稅。", foreground="#6b7385").grid(row=5, column=0, columnspan=3, sticky="w", padx=8)

    @staticmethod
    def load_favs():
        try:
            return [str(c) for c in json.loads(FAV_FILE.read_text(encoding="utf-8"))]
        except Exception:  # noqa
            return []

    def save_favs(self):
        try:
            FAV_FILE.write_text(json.dumps(self.favs, ensure_ascii=False), encoding="utf-8")
        except OSError as e:
            messagebox.showwarning("無法儲存", str(e))

    def fav_add(self):
        for c in [c for c in re.split(r"[\s,，、;]+", self.v_new.get().strip()) if c]:
            if c not in self.favs:
                self.favs.append(c)
                self.lb.insert("end", c)
        self.v_new.set("")
        self.save_favs()

    def fav_del(self):
        for i in reversed(self.lb.curselection()):
            self.favs.pop(i)
            self.lb.delete(i)
        self.save_favs()

    def fav_run(self):
        sel = [self.favs[i] for i in self.lb.curselection()] or list(self.favs)
        if not sel:
            return messagebox.showwarning("清單是空的", "請先新增股票代號")
        self.run_stock(["--stock"] + sel)

    # ── 動作 ──
    def do_screen(self):
        try:
            argv = ["--min-volume", str(opt(self.v_vol, "最低成交量") or 0), "--top", str(int(opt(self.v_top, "檔數") or 10))]
            for flag, var, nm in (("--max-price", self.v_price, "股價上限"), ("--max-pe", self.v_pe, "本益比上限"),
                                  ("--min-yield", self.v_yld, "殖利率下限")):
                x = opt(var, nm)
                if x is not None:
                    argv += [flag, str(x)]
            tot = opt(self.v_total, "總分下限")
            if tot is not None:
                argv += ["--min-total", str(tot)]
            if self.v_growth.get():
                argv.append("--growth")
        except ValueError as e:
            return messagebox.showwarning("輸入有誤", str(e))
        self.run_stock(argv)

    def do_stock(self):
        codes = [c for c in re.split(r"[\s,，、;]+", self.v_codes.get().strip()) if c]
        if not codes:
            return messagebox.showwarning("輸入有誤", "請輸入至少一個股票代號")
        self.run_stock(["--stock"] + codes)

    def do_levels(self):
        code = self.v_lc.get().strip()
        try:
            buy = opt(self.v_lb, "買進價")
            lots = opt(self.v_ll, "張數")
            ml = opt(self.v_lm, "最大虧損") or 7.0
        except ValueError as e:
            return messagebox.showwarning("輸入有誤", str(e))
        if not code or not buy or buy <= 0:
            return messagebox.showwarning("輸入有誤", "請輸入股票代號與買進價")
        self.start(lambda: PL.report(code, buy, lots, ml), "試算中…")

    def run_stock(self, argv):
        if not self.auto_open.get():
            argv.append("--no-open")

        def job():
            sys.argv = ["stock_screener.py"] + argv
            SS.main()

        self.start(job, "個股分析中…（需連線抓資料，請稍候）")

    def market_once(self, refresh, open_now):
        TA.build(refresh=refresh)
        if open_now and self.auto_open.get():
            webbrowser.open(TA.OUT_FILE.as_uri())

    def do_market(self):
        self.start(lambda: self.market_once(0, True), "大盤分析中…")

    def do_watch(self):
        try:
            iv = max(30, int(float(self.v_iv.get())))
        except ValueError:
            return messagebox.showwarning("輸入有誤", "間隔秒數請輸入數字")
        self.stop.clear()

        def job():
            first = True
            while not self.stop.is_set():
                try:
                    self.market_once(iv, first)
                except Exception as e:  # noqa
                    print("執行失敗：", e)
                first = False
                self.stop.wait(iv)
            print("已停止自動更新")

        self.start(job, f"自動更新中（每 {iv} 秒）…按「停止」結束")

    # ── 執行緒與記錄 ──
    def start(self, fn, msg):
        if self.busy:
            return messagebox.showinfo("執行中", "目前有工作正在執行，請等它完成（或先按停止）。")
        self.busy = True
        self.status.set(msg)
        threading.Thread(target=self.work, args=(fn,), daemon=True).start()

    def work(self, fn):
        try:
            with redirect_stdout(LogWriter(self.q)):
                fn()
        except SystemExit as e:
            self.q.put(f"結束：{e}\n")
        except Exception:  # noqa
            self.q.put(traceback.format_exc())
        finally:
            self.q.put(None)

    def poll(self):
        try:
            while True:
                m = self.q.get_nowait()
                if m is None:
                    self.busy = False
                    self.status.set("完成")
                else:
                    self.log.insert("end", m)
                    self.log.see("end")
        except queue.Empty:
            pass
        self.after(120, self.poll)

    @staticmethod
    def open_file(p):
        if p.exists():
            webbrowser.open(p.as_uri())
        else:
            messagebox.showinfo("尚無報告", "請先執行一次分析。")


if __name__ == "__main__":
    App().mainloop()
