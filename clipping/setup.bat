@echo off
REM Instalacija za Windows. Dvoklik ili pokreni u CMD-u jednom.
cd /d "%~dp0"
where ffmpeg >nul 2>nul || winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install -U pip
pip install -r requirements.txt
echo.
echo Gotovo. Ako je ffmpeg upravo instaliran, ZATVORI i ponovno otvori CMD.
echo Svaki put prije rada:  .venv\Scripts\activate
echo Probaj:  python clipper.py "https://www.youtube.com/watch?v=..."
pause
