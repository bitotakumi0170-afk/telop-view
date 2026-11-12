@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
if "%~1"=="" (echo Photoshop で保存した .psd をこの bat にドラッグ＆ドロップしてください & pause & exit /b 1)
python "%~dp0ts1lab\run.py" to-ts1 %*
echo.
pause
