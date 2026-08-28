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

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0server.ps1" %*
set "KNOWLEDGEDEBT_EXIT=%ERRORLEVEL%"

echo.
echo KnowledgeDebt 服务器启动流程已结束，退出码：%KNOWLEDGEDEBT_EXIT%。
pause
exit /b %KNOWLEDGEDEBT_EXIT%
