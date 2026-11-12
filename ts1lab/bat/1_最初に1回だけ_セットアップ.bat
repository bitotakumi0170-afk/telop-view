@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
echo VWSフォトショブリッジツール: 必要な部品を入れます（Python 3 が必要です）
python -m pip install -r "%~dp0ts1lab\requirements.txt"
if errorlevel 1 (echo. & echo ※ 失敗しました。Python 3 が入っているか確認してください) else (echo. & echo セットアップ完了)
echo.
pause
