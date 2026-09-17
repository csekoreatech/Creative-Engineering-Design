@echo off
chcp 65001 >nul
REM ============================================================================
REM install_requirements.bat
REM
REM install_requirements.ps1 (PowerShell 스크립트)을 실행 정책 제한 없이
REM 실행해 주는 CMD용 래퍼(wrapper) 파일입니다.
REM 탐색기에서 이 파일을 더블클릭하거나, CMD 창에서 실행하면 됩니다.
REM ============================================================================

setlocal
set SCRIPT_DIR=%~dp0

echo ============================================================
echo  MultiLanguageAI 필수 모듈 자동 설치 스크립트 실행 (CMD)
echo ============================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%install_requirements.ps1"

endlocal
pause
