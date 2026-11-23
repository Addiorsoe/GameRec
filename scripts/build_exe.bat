@echo off
REM Build dist\GameRec\GameRec.exe  (run from repo root in a venv with requirements-dev.txt installed)
pyinstaller --noconfirm --clean --name GameRec --windowed --uac-admin ^
  --collect-submodules scapy --collect-submodules pynput ^
  --hidden-import win32gui --hidden-import win32process ^
  scripts\run_gamerec.py
echo.
echo Done: dist\GameRec\GameRec.exe
