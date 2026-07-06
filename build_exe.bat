@echo off
rem Builds a standalone EliteTrader.exe (no Python needed to run it).
cd /d "%~dp0"
if not exist .venv (
    echo Run run.bat once first to create the environment.
    exit /b 1
)
.venv\Scripts\python -m pip install --quiet pyinstaller
.venv\Scripts\pyinstaller --noconfirm --onefile --windowed --name EliteTrader ^
  --add-data "ui;ui" ^
  --hidden-import webview.platforms.winforms ^
  --hidden-import webview.platforms.edgechromium ^
  app.py
echo.
echo Done: dist\EliteTrader.exe
