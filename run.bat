@echo off
chcp 65001 >nul
title 河南科技大学 选课抢课助手
cd /d "%~dp0"

echo ============================================
echo    河南科技大学 选课抢课助手 启动器
echo ============================================
echo.

REM ---- 0) 检查 Python ----
where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装 Python 3.8+：
    echo        https://www.python.org/downloads/
    echo        安装时务必勾选 "Add Python to PATH"
    echo.
    pause
    exit /b 1
)

REM ---- 1) 检查依赖是否已安装（selenium 与 webdriver-manager）----
python -c "import selenium, webdriver_manager" >nul 2>nul
if errorlevel 1 (
    echo [提示] 尚未安装依赖，开始自动安装...
    echo        正在执行: pip install -r requirements.txt
    echo.
    pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo [错误] 依赖安装失败！
        echo        如果你的 pip 走了一个失效的代理导致连不上，请手动运行：
        echo        pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --proxy^="^"
        echo.
        pause
        exit /b 1
    )
    echo.
    echo [成功] 依赖安装完成。
    echo.
)

REM ---- 2) 启动主程序 ----
echo 正在启动程序...
echo （首次运行会下载匹配的 Edge 驱动，请耐心等待；之后会正常进入界面）
echo.
python jwc_elect.py

REM ---- 3) 程序结束后暂停，方便看日志 ----
echo.
echo ----------------------------
echo 程序已结束。
pause