@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
if "%~1"=="" (echo .ts1 をこの bat にドラッグ＆ドロップしてください（条件を 1 つずつ変えた確認用 .ts1 を作ります） & pause & exit /b 1)
python "%~dp0ts1lab\vwstest.py" make "%~1"
echo.
pause
