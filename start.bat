@echo off
cd /d "%~dp0"

if not exist venv (
    echo Setting up for the first time, please wait...
    python -m venv venv
)

call venv\Scripts\activate.bat
pip install -r requirements.txt -q

if not exist "%USERPROFILE%\.streamlit" mkdir "%USERPROFILE%\.streamlit"
if not exist "%USERPROFILE%\.streamlit\credentials.toml" (
    echo [general] > "%USERPROFILE%\.streamlit\credentials.toml"
    echo email = "" >> "%USERPROFILE%\.streamlit\credentials.toml"
)

if not exist .env (
    copy .env.example .env
    echo.
    echo IMPORTANT: Open the .env file and add your API key, then run start.bat again.
    pause
    exit /b
)

streamlit run app.py
