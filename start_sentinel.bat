@echo off
title Sentinel | Social Alpha — Full Pipeline
color 0A
echo.
echo  ============================================================
echo   SENTINEL ^| Social Alpha — Starting Full Pipeline
echo  ============================================================
echo.

cd /d C:\Users\micha\projects\osint-pipeline

:: ---- Infrastructure ----
echo [ 1/12] Starting Kafka infrastructure...
start "Kafka Stack" cmd /k "docker-compose up && pause"
echo         Waiting 40s for Kafka to be healthy...
timeout /t 40 /nobreak >nul

:: ---- Activate venv ----
set VENV=.venv\Scripts\activate

:: ---- Connectors ----
echo [ 2/12] Starting HackerNews connector...
start "HN Connector" cmd /k "%VENV% && python -m connectors.hacker_news"

echo [ 3/12] Starting TrustPilot connector...
start "TrustPilot Connector" cmd /k "%VENV% && python -m connectors.trustpilot"

echo [ 4/12] Starting Reddit connector...
start "Reddit Connector" cmd /k "%VENV% && python -m connectors.reddit"

echo [ 5/12] Starting GitHub connector...
start "GitHub Connector" cmd /k "%VENV% && python -m connectors.github"

echo [ 6/12] Starting SEC Insider connector...
start "SEC Connector" cmd /k "%VENV% && python -m connectors.sec_insider"

echo [ 7/12] Starting Google Trends connector...
start "Trends Connector" cmd /k "%VENV% && python -m connectors.google_trends"

:: ---- Processing ----
echo [ 8/12] Starting Normalization layer...
start "Normalization" cmd /k "%VENV% && python -m normalization"

echo [ 9/12] Starting Cross-Correlation engine...
start "Correlation" cmd /k "%VENV% && python -m correlation"

:: ---- Value Engines ----
echo [10/12] Starting Stock Alpha engine...
start "Stock Alpha" cmd /k "%VENV% && python -m stock_alpha"

echo [11/12] Starting Product Ideation engine...
start "Product Ideation" cmd /k "%VENV% && python -m product_ideation"

:: ---- Dashboard ----
echo [12/12] Starting Sentinel dashboard...
start "Dashboard" cmd /k "cd dashboard && npm run dev"

:: ---- Done ----
echo.
echo  ============================================================
echo   All 12 services launched!
echo.
echo   Kafka UI:    http://localhost:8080
echo   Dashboard:   http://localhost:3000
echo.
echo   Note: FinBERT (~440MB) and PyABSA (~500MB) models will
echo   auto-download on first run of the value engines.
echo   TrustPilot connector needs TRUSTPILOT_DOMAINS env var.
echo  ============================================================
echo.
echo  Press any key to open the dashboard in your browser...
pause >nul
start http://localhost:3000
