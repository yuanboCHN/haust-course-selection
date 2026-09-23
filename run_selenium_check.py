#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
河南科技大学 选课抢课助手 — 环境自检 & 最小启动
====================================================
版本: v0.1 (环境验证版)
作用: 验证当前机器能否用 Selenium 驱动 Edge 打开教务系统。

用法:
    python run_selenium_check.py
"""

import sys
import time
from selenium import webdriver
from selenium.webdriver.edge.options import Options
from selenium.webdriver.edge.service import Service
from webdriver_manager.microsoft import EdgeChromiumDriverManager

EDGE_PATH = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
TARGET_URL = "https://jwgl-haust-edu-cn-s.haust.edu.cn/eams/homeExt.action"


def main():
    print("[1/4] 配置 Edge 选项 ...")
    opts = Options()
    opts.binary_location = EDGE_PATH
    # 保留一个可见窗口（aTrust 引导认证需要真人可见；后面正式版可改 headless 前的调试）
    # opts.add_argument("--headless=new")

    print("[2/4] 准备 EdgeDriver (webdriver-manager) ...")
    driver_path = EdgeChromiumDriverManager().install()
    print(f"      EdgeDriver 位于: {driver_path}")

    service = Service(driver_path)
    driver = webdriver.Edge(service=service, options=opts)

    try:
        print(f"[3/4] 打开教务系统 ...")
        driver.get(TARGET_URL)
        time.sleep(6)
        print("[4/4] 当前页面标题:", driver.title)
        print("      当前 URL  :", driver.current_url)
        print("      => 若被 302 到 vpn/cas 或停在登录页，均属预期（说明环境链路通）。")
    finally:
        # 不自动关闭，方便你肉眼确认浏览器是否正常起来
        input("\n回车后关闭浏览器窗口 ...")
        driver.quit()


if __name__ == "__main__":
    main()