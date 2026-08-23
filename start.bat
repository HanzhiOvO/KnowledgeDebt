@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul

where powershell.exe >nul 2>nul
if errorlevel 1 (
  echo ERROR: PowerShell was not found on this Windows installation.
  pause
  exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
set "KNOWLEDGEDEBT_EXIT=%ERRORLEVEL%"

rem Safety cleanup: ensure no orphan API/Web process stays alive after the console closes.
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /c:":8123 " ^| findstr "LISTENING"') do taskkill /PID %%P /T /F >nul 2>nul
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /c:":3000 " ^| findstr "LISTENING"') do taskkill /PID %%P /T /F >nul 2>nul

echo.
echo KnowledgeDebt / Zhizhai start script finished with exit code %KNOWLEDGEDEBT_EXIT%.
pause
exit /b %KNOWLEDGEDEBT_EXIT%
