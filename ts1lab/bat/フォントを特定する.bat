@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
if "%~1"=="" (echo .ts1 をこの bat にドラッグ＆ドロップしてください（PC のフォントと字形を照合します） & pause & exit /b 1)
python "%~dp0ts1lab\run.py" identify %*
echo.
pause
