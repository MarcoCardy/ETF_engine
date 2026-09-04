@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Ambiente del programma non trovato. Eseguire prima l'installazione.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m streamlit run "perpetual_engine\dashboard.py" --server.address=127.0.0.1 --server.port=8501 --server.headless=false --browser.gatherUsageStats=false
if errorlevel 1 (
  echo Impossibile avviare Analisi ETF. Verificare che la porta 8501 sia libera.
  pause
)
