@echo off
setlocal
set SMM_PORT=8789
if "%SMM_DATA_DIR%"=="" set SMM_DATA_DIR=%LOCALAPPDATA%\CoMind
if "%SMM_MAP_DIR%"=="" set SMM_MAP_DIR=%USERPROFILE%\comind-maps
set SMM_CHAT_SESSION_DIR=%SMM_DATA_DIR%\chat-sessions
if "%PI_BIN%"=="" (
  if exist "%SMM_DATA_DIR%\pi-runtime\node_modules\.bin\pi.cmd" set PI_BIN=%SMM_DATA_DIR%\pi-runtime\node_modules\.bin\pi.cmd
  if exist "%APPDATA%\npm\pi.cmd" set PI_BIN=%APPDATA%\npm\pi.cmd
)
cd /d "%~dp0"
if not exist "%SMM_DATA_DIR%" mkdir "%SMM_DATA_DIR%"
if not exist "%SMM_CHAT_SESSION_DIR%" mkdir "%SMM_CHAT_SESSION_DIR%"
if not exist "%SMM_DATA_DIR%\private" mkdir "%SMM_DATA_DIR%\private"
if not exist "%SMM_MAP_DIR%" mkdir "%SMM_MAP_DIR%"
"%~dp0\.venv\Scripts\python.exe" backend.py >> "%SMM_DATA_DIR%\comind.log" 2>&1
