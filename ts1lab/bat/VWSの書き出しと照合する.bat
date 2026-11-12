@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
if "%~1"=="" (echo VWS の書き出し PNG を入れたフォルダをこの bat にドラッグ＆ドロップしてください & pause & exit /b 1)
python "%~dp0ts1lab\vwstest.py" check "%~1"
echo.
pause
