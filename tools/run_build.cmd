@echo off
rem ===================================================================
rem  重新打包便携版 exe（开发者用，普通用户不需要跑这个）
rem  成品：..\new\novel-scraper.exe（源码和构建缓存都留在本文件夹里）
rem ===================================================================
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0.."

set OUT=..\new
python -m PyInstaller --noconfirm --clean --onefile --console --log-level=WARN ^
  --name novel-scraper ^
  --distpath "%OUT%" --workpath "_build" --specpath "_build" ^
  --add-data "%CD%\sites.json;." ^
  --hidden-import tools.launcher ^
  --exclude-module tkinter --exclude-module matplotlib --exclude-module numpy ^
  --exclude-module pandas --exclude-module PIL --exclude-module pytest ^
  --exclude-module IPython ^
  "tools\build_entry.py"

echo.
if errorlevel 1 (echo [x] 打包失败) else (echo [ok] 产物：%OUT%\novel-scraper.exe)
pause
