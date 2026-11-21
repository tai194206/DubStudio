@echo off
chcp 65001 >nul
setlocal

set "CHROME_EXE="
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" (
    set "CHROME_EXE=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
) else if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" (
    set "CHROME_EXE=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
) else if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" (
    set "CHROME_EXE=%LocalAppData%\Google\Chrome\Application\chrome.exe"
)

if "%CHROME_EXE%"=="" (
    echo [LOI] Khong tim thay Google Chrome tren may!
    echo Vui long kiem tra lai duong dan cai dat Chrome.
    pause
    exit /b 1
)

set "PROFILE_DIR=%~dp0..\chrome_profile"
if not exist "%PROFILE_DIR%" mkdir "%PROFILE_DIR%"

echo ============================================================================
echo  DUBSTUDIO V5 - MO CHROME THAT KET NOI CDP (PORT 9222) CHO GOOGLE FLOW
echo ============================================================================
echo  Chrome  : %CHROME_EXE%
echo  Profile : %PROFILE_DIR%
echo.
echo  HUONG DAN NHANH:
echo   1. Lan dau tien: dang nhap tai khoan Google cua ban tren cua so Chrome nay.
echo   2. Mo san du an Google Flow can chay (hoac tao du an moi).
echo   3. GIU NGUYEN cua so Chrome nay, sau do chay flow_bot.py trong terminal.
echo ============================================================================

start "" "%CHROME_EXE%" --remote-debugging-port=9222 --user-data-dir="%PROFILE_DIR%" "https://labs.google/fx/tools/flow"
endlocal
