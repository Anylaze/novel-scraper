@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title 小说爬虫

rem 用 UTF-8 输出，避免中文乱码
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

where python >nul 2>nul
if errorlevel 1 (
    echo [x] 没有找到 python。请先安装 Python 3.10+ 并勾选 "Add to PATH"。
    echo     下载： https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

python "%~dp0start.py"
set RC=%ERRORLEVEL%

echo.
pause
exit /b %RC%
