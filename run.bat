@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
set "LINK_EXPAND_ENV=%LOCALAPPDATA%\LinkExpand\venv"
pushd "%~dp0"
if errorlevel 1 goto fail
if exist "%LINK_EXPAND_ENV%\Scripts\python.exe" goto ready
where py >nul 2>nul
if errorlevel 1 goto usepython
py -3 -m venv "%LINK_EXPAND_ENV%"
if errorlevel 1 goto fail
goto ready
:usepython
where python >nul 2>nul
if errorlevel 1 goto missing
python -m venv "%LINK_EXPAND_ENV%"
if errorlevel 1 goto fail
:ready
"%LINK_EXPAND_ENV%\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto fail
"%LINK_EXPAND_ENV%\Scripts\python.exe" -m linkexpand.setup_browser
if errorlevel 1 goto fail
"%LINK_EXPAND_ENV%\Scripts\python.exe" -m linkexpand.server %*
if errorlevel 1 goto fail
popd
exit /b 0
:missing
echo Please install Python 3.10 or newer, then run this file again.
echo https://www.python.org/downloads/windows/
pause
exit /b 1
:fail
echo Startup failed. Check the error above.
popd
pause
exit /b 1
