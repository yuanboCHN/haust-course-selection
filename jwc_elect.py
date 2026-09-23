#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
河南科技大学 选课抢课助手
==========================
运行方式：连好内网/VPN 后，python jwc_elect.py

原理：
  - 学校教务系统经过深信服 aTrust 网关，只有真实浏览器能通过认证。
  - 因此本程序用 Selenium 驱动你本机的 Edge 浏览器，
    程序启动浏览器后，你在浏览器里手动登录（含验证码/aTrust 认证），
    进入选课页面后，程序自动接管：
        到点 -> 按志愿顺序循环尝试抢课 -> 检测成功/满员 -> 自动换下一志愿。

本文件基于“选课系统关闭、无法抓取真实 DOM”的现状编写，
所有需要定位页面的选择器都集中在下方 CONFIG，系统开放后抓包填入即可。
"""

import sys
import json
import time
import threading
import datetime
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox

from selenium import webdriver
from selenium.webdriver.edge.options import Options
from selenium.webdriver.edge.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.microsoft import EdgeChromiumDriverManager

# ============================================================
# 配置区（系统开放后，按 README 里的抓包说明填写真实选择器）
# ============================================================
CONFIG = {
    # 选课页面地址（登录后进入的页面）
    "elect_page_url": "https://jwgl-haust-edu-cn-s.haust.edu.cn/eams/stdElectCourse.action",

    # --- 以下是选择器，需要按真实页面调整 ---
    # 选课列表中，每一行课程的定位器（用 XPath 表示“一行”）
    "row_locator": "xpath://tr[contains(@id,'course')]",
    # 每行内“课程号”所在单元格
    "course_no_cell": ".//td[1]",
    # 每行内“选课/选它”按钮
    "elect_button": ".//a[contains(text(),'选课') or contains(text(),'选择')]",
    # 选课操作成功的提示文本关键词（页面或弹窗里应出现）
    "success_keyword": ["成功", "已选"],
    # 选课名额已满的提示文本关键词
    "full_keyword": ["满", "已满", "没有名额", "不足"],

    # 开抢前，刷新候选课程的周期（秒）
    "refresh_interval": 2,
    # 每次尝试间隔（秒），避免频率过高被风控
    "try_interval": 0.8,
    # 到点前提前量（秒）：提前这么早开始进入抢课轮询
    "advance_seconds": 0.0,
}

EDGE_PATH = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
SETTINGS_FILE = "settings.json"


# ============================================================
# 工具函数
# ============================================================
def log(msg, box):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}\n"
    box.configure(state="normal")
    box.insert(tk.END, line)
    box.see(tk.END)
    box.configure(state="disabled")


def now_str():
    return datetime.datetime.now().strftime("%H:%M:%S")


# ============================================================
# 抢课引擎
# ============================================================
class Elector:
    """单线程抢课逻辑：给定 driver 和志愿队列，到点后逐个尝试。"""

    def __init__(self, driver, courses, target_dt, config, logfn):
        self.driver = driver
        self.courses = courses          # [ {name, no}, ... ] 志愿顺序
        self.target_dt = target_dt      # datetime
        self.cfg = config
        self.log = logfn
        self.running = True
        self.finished = False

    def stop(self):
        self.running = False

    def _eval_locator(self, loc):
        """解析 'xpath://...' 或 'css:...' 选择器。"""
        if loc.startswith("xpath:"):
            return By.XPATH, loc[len("xpath:"):]
        if loc.startswith("css:"):
            return By.CSS_SELECTOR, loc[len("css:"):]
        return By.XPATH, loc

    def run(self):
        self.log(f"已进入抢课模式，目标时间 {self.target_dt.strftime('%H:%M:%S.%f')[:-3]}")
        adv = self.cfg.get("advance_seconds", 0.0)
        # 等待到点
        while self.running:
            now = datetime.datetime.now()
            if now >= self.target_dt.replace(microsecond=0) - \
               datetime.timedelta(seconds=adv):
                break
            time.sleep(0.05)
        if not self.running:
            return
        self.log(">>> 开抢！开始按志愿循环尝试 <<<")
        time.sleep(0.01)

        # 按志愿顺序循环
        for idx, c in enumerate(self.courses, 1):
            if not self.running:
                break
            self.log(f"---- 尝试第 {idx} 志愿：{c.get('name','')}（{c.get('no','')}） ----")
            ok, detail = self._try_course(c)
            if ok:
                self.log(f"✔ 第 {idx} 志愿抢课成功！{detail}")
                self.finished = True
                break
            else:
                self.log(f"✘ 第 {idx} 志愿结果：{detail}（自动进入下一志愿）")
                # 给上一志愿的点击请求一点落地时间
                time.sleep(self.cfg.get("try_interval", 0.8))

        if self.running and not self.finished:
            self.log(">>> 所有志愿均已尝试完毕，流程结束。 <<<")
        self.log("=== 本轮抢课结束 ===")

    def _try_course(self, course):
        """
        尝试抢某一门课。核心逻辑：
          1) 刷新/确保在选课页面
          2) 在表格里定位该课程所在行
          3) 点击该行的选课按钮
          4) 等待返回，判断 成功 or 满员/失败
        由于当前系统未开放无法抓真实 DOM，这里用 CONFIG 选择器驱动，
        并做好异常兜底。系统开放后按 README 校准选择器即可。
        """
        try:
            # 如果 driver 不在选课页，先跳转
            if self.cfg["elect_page_url"] not in (self.driver.current_url or ""):
                self.log("跳转到选课页面 ...")
                self.driver.get(self.cfg["elect_page_url"])
                time.sleep(2)

            row_by, row_sel = self._eval_locator(self.cfg["row_locator"])
            rows = self.driver.find_elements(row_by, row_sel)
            self.log(f"当前候选课程行数：{len(rows)}")

            target_row = None
            for row in rows:
                try:
                    no_cell = row.find_element(By.XPATH, self.cfg["course_no_cell"])
                    cell_text = (no_cell.text or "").strip()
                    # 若用户填了课程号，则精确匹配；否则匹配课程名
                    if course.get("no") and course["no"] in cell_text:
                        target_row = row
                        break
                    if course.get("name") and course["name"] in cell_text:
                        target_row = row
                        break
                except Exception:
                    continue

            if target_row is None:
                return False, "未在列表中找到该课程（可能未开放或名称不符）"

            btn = target_row.find_element(By.XPATH, self.cfg["elect_button"])
            btn.click()
            self.log("已点击选课按钮，等待服务器响应 ...")
            time.sleep(self.cfg.get("try_interval", 0.8))

            # 处理可能的 alert（青果常弹 alert）
            try:
                alert = self.driver.switch_to.alert
                txt = alert.text
                alert.accept()
                self.log(f"弹窗内容：{txt}")
                if any(k in txt for k in self.cfg.get("success_keyword", [])):
                    return True, f"弹窗提示：{txt}"
                if any(k in txt for k in self.cfg.get("full_keyword", [])):
                    return False, f"弹窗提示：{txt}"
                return False, f"弹窗未知结果：{txt}"
            except Exception:
                pass

            # 无 alert，则检查页面是否出现成功/满员关键词
            page = self.driver.page_source
            if any(k in page for k in self.cfg.get("success_keyword", [])):
                return True, "页面出现成功提示"
            if any(k in page for k in self.cfg.get("full_keyword", [])):
                return False, "页面出现满员提示"
            return False, "未检测到明确结果（可能还需人工确认）"

        except Exception as e:
            return False, f"异常：{type(e).__name__}: {e}"


# ============================================================
# 图形界面
# ============================================================
class App:
    def __init__(self, root):
        self.root = root
        root.title("河南科技大学 选课抢课助手")
        root.geometry("760x640")

        self.driver = None
        self.elector = None
        self.courses = []
        self.settings = self._load_settings()

        self._build_ui()
        self._apply_settings()

    # ---------- 界面构建 ----------
    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}

        # 目标设置
        fr = ttk.LabelFrame(self.root, text="目标设置")
        fr.pack(fill="x", **pad)

        ttk.Label(fr, text="选课页面URL:").grid(row=0, column=0, sticky="w", **pad)
        self.var_url = tk.StringVar()
        ttk.Entry(fr, textvariable=self.var_url, width=70).grid(row=0, column=1, columnspan=3, sticky="we", **pad)

        ttk.Label(fr, text="开抢时间:").grid(row=1, column=0, sticky="w", **pad)
        self.var_time = tk.StringVar()
        ttk.Entry(fr, textvariable=self.var_time, width=20).grid(row=1, column=1, sticky="w", **pad)
        ttk.Label(fr, text="格式 12:00:00").grid(row=1, column=2, sticky="w")
        ttk.Button(fr, text="现在时刻", command=lambda: self.var_time.set(
            datetime.datetime.now().strftime("%H:%M:%S"))).grid(row=1, column=3, sticky="w", **pad)

        # 志愿列表
        fr2 = ttk.LabelFrame(self.root, text="选课志愿（按优先级从上到下）")
        fr2.pack(fill="both", expand=True, **pad)

        cols = ("no", "name")
        self.tree = ttk.Treeview(fr2, columns=cols, show="headings", height=8)
        self.tree.heading("no", text="课程号")
        self.tree.heading("name", text="课程名")
        self.tree.column("no", width=120, anchor="center")
        self.tree.column("name", width=300)
        self.tree.pack(side="left", fill="both", expand=True, **pad)

        sbar = ttk.Scrollbar(fr2, orient="vertical", command=self.tree.yview)
        sbar.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=sbar.set)

        # 志愿编辑按钮
        fr3 = ttk.Frame(self.root)
        fr3.pack(fill="x", **pad)
        ttk.Button(fr3, text="添加志愿", command=self.add_course).pack(side="left", **pad)
        ttk.Button(fr3, text="删除选中", command=self.del_course).pack(side="left", **pad)
        ttk.Button(fr3, text="上移", command=lambda: self.move(-1)).pack(side="left", **pad)
        ttk.Button(fr3, text="下移", command=lambda: self.move(1)).pack(side="left", **pad)

        # 控制按钮
        fr4 = ttk.Frame(self.root)
        fr4.pack(fill="x", **pad)
        self.btn_open = ttk.Button(fr4, text="① 打开浏览器并登录", command=self.open_browser)
        self.btn_open.pack(side="left", **pad)
        self.btn_arm = ttk.Button(fr4, text="② 进入选课页待命", command=self.arm)
        self.btn_arm.pack(side="left", **pad)
        self.btn_go = ttk.Button(fr4, text="③ 开始抢课", command=self.go)
        self.btn_go.pack(side="left", **pad)
        self.btn_stop = ttk.Button(fr4, text="停止", command=self.stop)
        self.btn_stop.pack(side="left", **pad)

        # 日志
        fr5 = ttk.LabelFrame(self.root, text="运行日志")
        fr5.pack(fill="both", expand=True, **pad)
        self.logbox = scrolledtext.ScrolledText(fr5, height=10, state="disabled")
        self.logbox.pack(fill="both", expand=True, **pad)

        self._logv("就绪。请先在主机上连好内网或 VPN。")

    # ---------- 逻辑 ----------
    def _logv(self, msg):
        log(msg, self.logbox)

    def add_course(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("添加志愿")
        dlg.geometry("360x140")
        dlg.transient(self.root)
        tk.Label(dlg, text="课程号:").grid(row=0, column=0, sticky="w", padx=10, pady=8)
        v_no = tk.StringVar(); tk.Entry(dlg, textvariable=v_no, width=30).grid(row=0, column=1, padx=10)
        tk.Label(dlg, text="课程名:").grid(row=1, column=0, sticky="w", padx=10, pady=8)
        v_name = tk.StringVar(); tk.Entry(dlg, textvariable=v_name, width=30).grid(row=1, column=1, padx=10)

        def ok():
            no = v_no.get().strip(); name = v_name.get().strip()
            if not no and not name:
                messagebox.showwarning("提示", "课程号和课程名至少填一个")
                return
            self.courses.append({"no": no, "name": name})
            self.tree.insert("", tk.END, values=(no, name))
            self._logv(f"添加志愿：{name or no}")
            dlg.destroy()
        ttk.Button(dlg, text="确定", command=ok).grid(row=2, column=1, sticky="e", padx=10, pady=10)

    def _selected_index(self):
        sel = self.tree.selection()
        if not sel:
            return None
        return self.tree.index(sel[0])

    def del_course(self):
        i = self._selected_index()
        if i is None:
            return
        item = self.tree.get_children()[i]
        self.tree.delete(item)
        del self.courses[i]

    def move(self, delta):
        i = self._selected_index()
        if i is None:
            return
        j = i + delta
        if j < 0 or j >= len(self.courses):
            return
        self.courses[i], self.courses[j] = self.courses[j], self.courses[i]
        items = self.tree.get_children()
        vals = [self.tree.item(it, "values") for it in items]
        self.tree.delete(*items)
        for no, name in vals:
            self.tree.insert("", tk.END, values=(no, name))
        self.tree.selection_set(self.tree.get_children()[j])

    def open_browser(self):
        if self.driver is not None:
            self._logv("浏览器已打开。")
            return
        def work():
            try:
                self._logv("正在启动 Edge（首次会自动下载 EdgeDriver，请稍候）...")
                opts = Options()
                opts.binary_location = EDGE_PATH
                dpath = EdgeChromiumDriverManager().install()
                self.driver = webdriver.Edge(service=Service(dpath), options=opts)
                url = self.var_url.get().strip() or CONFIG["elect_page_url"]
                self.driver.get(url)
                self._logv("浏览器已打开。请在新窗口里手动登录，并进入选课页面。")
                self._logv("当前地址：" + self.driver.current_url)
            except Exception as e:
                self._logv(f"启动浏览器失败：{type(e).__name__}: {e}")
        threading.Thread(target=work, daemon=True).start()

    def arm(self):
        if not self.driver:
            messagebox.showwarning("提示", "请先①打开浏览器并登录")
            return
        self._logv("已就位。请确认浏览器停留在选课页面；设置好开抢时间与志愿后点③。")

    def go(self):
        if not self.courses:
            messagebox.showwarning("提示", "请先添加至少一个志愿")
            return
        if not self.driver:
            messagebox.showwarning("提示", "请先①打开浏览器并登录")
            return
        ts = self.var_time.get().strip()
        try:
            h, m, s = (int(x) for x in ts.split(":"))
        except Exception:
            messagebox.showwarning("提示", "开抢时间格式错误，应为 12:00:00")
            return
        now = datetime.datetime.now()
        target = now.replace(hour=h, minute=m, second=s, microsecond=0)
        if target <= now:
            messagebox.showwarning("提示", "开抢时间已过，请设置为未来时间")
            return
        self._save_settings()
        self.elector = Elector(self.driver, list(self.courses), target, CONFIG, self._logv)
        threading.Thread(target=self.elector.run, daemon=True).start()
        self._logv(f"已进入待命，将于 {ts} 开抢（共 {len(self.courses)} 个志愿）。")

    def stop(self):
        if self.elector:
            self.elector.stop()
            self._logv("已发送停止指令。")

    # ---------- 设置持久化 ----------
    def _load_settings(self):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _apply_settings(self):
        if self.settings.get("url"):
            self.var_url.set(self.settings["url"])
        if self.settings.get("time"):
            self.var_time.set(self.settings["time"])
        for c in self.settings.get("courses", []):
            self.courses.append(c)
            self.tree.insert("", tk.END, values=(c.get("no", ""), c.get("name", "")))

    def _save_settings(self):
        data = {
            "url": self.var_url.get().strip(),
            "time": self.var_time.get().strip(),
            "courses": self.courses,
        }
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self._logv(f"保存设置失败：{e}")


def _fix_tcl_env():
    """
    修复被外部环境（如某些运行时/代理里的 OpenSquilla gateway）污染的
    TCL_LIBRARY / TK_LIBRARY 变量。它们可能指向版本不匹配的 Tcl/Tk 库，
    导致 tkinter 无法初始化。这里把它们重置为 Python 自带库的路径。
    """
    import os, sys
    base = sys.base_prefix  # 例如 D:\\Py3106
    tcl_root = os.path.join(base, "tcl")
    if not os.path.isdir(tcl_root):
        return  # 无 tcl 目录就不干预
    # 只要当前指向的目录不在 Python 自带 tcl 下，就重置
    for var, sub in (("TCL_LIBRARY", "tcl8.6"), ("TK_LIBRARY", "tk8.6")):
        cur = os.environ.get(var)
        want = os.path.join(tcl_root, sub)
        if os.path.isdir(want) and (not cur or not os.path.normpath(cur).startswith(os.path.normpath(tcl_root))):
            os.environ[var] = want


def main():
    _fix_tcl_env()
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()