@echo off
REM Build a single double-clickable dist\GameRec.exe (GUI, no console, asks for admin).
REM Run from the repo root (--paths . lets PyInstaller find the gamerec package) in a venv with requirements-dev.txt installed.
pyinstaller --noconfirm --clean ^
  --onefile --windowed --uac-admin ^
  --name GameRec ^
  --collect-submodules scapy --collect-submodules pynput ^
  --hidden-import win32gui --hidden-import win32process ^
  --paths . --collect-submodules gamerec ^
  scripts\run_gamerec.py
echo.
echo Done: dist\GameRec.exe
