@echo off
rem ===================================================================
rem  浏览器会话工具：由你本人过 Cloudflare 验证，再把会话存给爬虫用。
rem  双击即可；也可以把网址当参数传：browser-session.cmd "https://..."
rem ===================================================================
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title 浏览器会话工具（本人过验证）

set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

where python >nul 2>nul
if errorlevel 1 (
    echo [x] 没有找到 python。本工具需要 Python 3.10+ 和 playwright：
    echo       python -m pip install playwright
    echo.
    pause
    exit /b 1
)

set "URL=%~1"
if not defined URL (
    echo 请输入要过验证的站点首页或目录页网址
    set /p "URL=网址: "
)
if not defined URL (
    echo [x] 没有输入网址，退出。
    pause
    exit /b 1
)

echo.
echo 即将打开浏览器访问：%URL%
echo 如果页面出现 Cloudflare 验证，请你自己点一下完成它（本工具不绕过验证）。
echo 验证通过后会话会存成：%~dp0browser-state.json，爬虫会自动加载。
echo.

python "%~dp0browser_session.py" "%URL%" --profile
set RC=%ERRORLEVEL%

echo.
if "%RC%"=="0" (
    echo [ok] 会话已保存，现在可以直接跑 run.cmd 或 novel-scraper.exe 抓取了。
) else (
    echo [!] 退出码 %RC%：没有拿到有效会话，详见上面的提示。
)
pause
exit /b %RC%
