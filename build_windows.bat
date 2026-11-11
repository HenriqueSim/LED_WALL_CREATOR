@echo off
rem LED Wall Creator - build the Windows app and its installer on this PC.
rem Needs: Python 3.10+ (run run_windows.bat once first) and Inno Setup 6 (https://jrsoftware.org/isdl.php,
rem or: winget install JRSoftware.InnoSetup).
rem Result: python\dist\LEDWallCreator-Setup.exe  (and the plain app in python\dist\LEDWallCreator\)
setlocal
cd /d "%~dp0python"
if not exist ".venv\Scripts\python.exe" (
    echo Run run_windows.bat once first - it sets up Python for the app.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt pyinstaller || goto :fail
echo Building the app (a few minutes)...
".venv\Scripts\python.exe" -m PyInstaller --noconfirm LEDWallCreator.spec || goto :fail

set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (
    echo.
    echo The app is built in python\dist\LEDWallCreator\ but Inno Setup 6 was not found,
    echo so no installer was made. Install it from https://jrsoftware.org/isdl.php and run this again.
    pause
    exit /b 1
)
for /f "tokens=2 delims==" %%v in ('findstr /b "__version__" ledwall\version.py') do set "VER=%%~v"
set "VER=%VER: =%"
set "VER=%VER:"=%"
echo Building the installer for version %VER%...
"%ISCC%" /Qp "/DAppVersion=%VER%" installer\LEDWallCreator.iss || goto :fail
echo.
echo Done: python\dist\LEDWallCreator-Setup.exe
explorer /select,"%cd%\dist\LEDWallCreator-Setup.exe"
pause
exit /b 0

:fail
echo.
echo The build failed - see the messages above.
pause
exit /b 1
