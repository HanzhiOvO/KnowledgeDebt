@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0server.ps1" configure
set "KNOWLEDGEDEBT_EXIT=%ERRORLEVEL%"

echo.
echo KnowledgeDebt AI 配置流程已结束，退出码：%KNOWLEDGEDEBT_EXIT%。
pause
exit /b %KNOWLEDGEDEBT_EXIT%
