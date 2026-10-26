@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".env" (
  copy ".env.example" ".env" >nul
  echo Создан .env. Впиши BOT_TOKEN и OWNER_ID, затем снова запусти start.bat.
  notepad ".env"
  pause
  exit /b
)
if not exist ".venv\Scripts\python.exe" python -m venv .venv
call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip
pip install -r requirements.txt
python bot.py
pause
