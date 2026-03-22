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
echo [ 1/20] Starting Kafka infrastructure...
start "Kafka Stack" cmd /k "docker-compose up && pause"
echo         Waiting 40s for Kafka to be healthy...
timeout /t 40 /nobreak >nul

:: ---- Activate venv ----
set VENV=.venv\Scripts\activate

:: ---- Core Connectors ----
echo [ 2/20] Starting HackerNews connector...
start "HN Connector" cmd /k "%VENV% && python -m connectors.hacker_news"

echo [ 3/20] Starting Reddit connector (29 subreddits)...
start "Reddit Connector" cmd /k "%VENV% && python -m connectors.reddit"

echo [ 4/20] Starting GitHub connector...
start "GitHub Connector" cmd /k "%VENV% && python -m connectors.github"

echo [ 5/20] Starting SEC Insider connector...
start "SEC Connector" cmd /k "%VENV% && python -m connectors.sec_insider"

echo [ 6/20] Starting Alpaca Market Data...
start "Alpaca Connector" cmd /k "%VENV% && python -m connectors.alpaca_market"

:: ---- News Connectors ----
echo [ 7/20] Starting Financial News (Yahoo RSS)...
start "Financial News" cmd /k "%VENV% && python -m connectors.financial_news"

echo [ 8/20] Starting TechCrunch + Seeking Alpha + Finviz...
start "TechCrunch" cmd /k "%VENV% && python -m connectors.techcrunch"
start "Seeking Alpha" cmd /k "%VENV% && python -m connectors.seeking_alpha"
start "Finviz" cmd /k "%VENV% && python -m connectors.finviz"

echo [ 9/20] Starting Techmeme + Newsletter Feeds...
start "Techmeme" cmd /k "%VENV% && python -m connectors.techmeme"
start "Newsletter Feeds" cmd /k "%VENV% && python -m connectors.newsletter_feeds"

:: ---- Government Data ----
echo [10/20] Starting Government Data (SAM/USAspending/FedReg/SBIR/BLS)...
start "SAM.gov" cmd /k "%VENV% && python -m connectors.sam_gov"
start "USAspending" cmd /k "%VENV% && python -m connectors.usaspending"
start "Federal Register" cmd /k "%VENV% && python -m connectors.federal_register"
start "SBIR" cmd /k "%VENV% && python -m connectors.sbir"
start "Economic Data" cmd /k "%VENV% && python -m connectors.economic_data"

:: ---- Macro/Market ----
echo [11/20] Starting FRED + Binance...
start "FRED Macro" cmd /k "%VENV% && python -m connectors.fred"
start "Binance" cmd /k "%VENV% && python -m connectors.binance"

:: ---- Processing ----
echo [12/20] Starting Normalization layer...
start "Normalization" cmd /k "%VENV% && python -m normalization"

echo [13/20] Starting Cross-Correlation engine...
start "Correlation" cmd /k "%VENV% && python -m correlation"

:: ---- Value Engines ----
echo [14/20] Starting Stock Alpha engine...
start "Stock Alpha" cmd /k "%VENV% && python -m stock_alpha"

echo [15/20] Starting Product Ideation engine...
start "Product Ideation" cmd /k "%VENV% && python -m product_ideation"

:: ---- API Server ----
echo [16/20] Starting Sentinel API server...
start "API Server" cmd /k "%VENV% && python -m api"

:: ---- Dashboard ----
echo [17/20] Starting Sentinel dashboard...
start "Dashboard" cmd /k "cd dashboard && npm run dev"

:: ---- Done ----
echo.
echo  ============================================================
echo   All services launched! (24 connectors + 4 engines + API + UI)
echo.
echo   Kafka UI:    http://localhost:8080
echo   API Server:  http://localhost:8000
echo   Dashboard:   http://localhost:3000
echo.
echo   Data Sources: HN, Reddit (29 subs), GitHub, SEC EDGAR,
echo     Alpaca, Yahoo RSS, TechCrunch, Seeking Alpha, Finviz,
echo     Techmeme, TLDR, Pragmatic Engineer, Sifted, Tech.EU,
echo     SAM.gov, USAspending, Federal Register, SBIR, BLS,
echo     FRED, Binance
echo  ============================================================
echo.
echo  Press any key to open the dashboard in your browser...
pause >nul
start http://localhost:3000
