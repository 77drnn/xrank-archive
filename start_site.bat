@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo X RANK ARCHIVE - 必要なライブラリを準備します...
py -m pip install -r requirements.txt
echo データを更新します...
py update_xrank.py
if errorlevel 1 echo [警告] データ更新に失敗しました。公開元に接続できるか確認してください。
echo.
echo ブラウザを開きます: http://127.0.0.1:8000
start "" http://127.0.0.1:8000
py -m http.server 8000 --bind 127.0.0.1
pause
