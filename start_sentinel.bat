@echo off
title Sentinel Launcher
cd /d C:\Users\micha\projects\osint-pipeline
set MESSAGE_BUS=sqlite

echo ============================================
echo   Sentinel - Starting (SQLite mode)
echo ============================================

echo [1/5] Cleaning up...
taskkill /F /FI "WINDOWTITLE eq S-*" >nul 2>&1
if not exist "data" mkdir data
timeout /t 2 /nobreak >nul

echo [2/5] Starting connectors...
start "S-HackerNews" /min python -m connectors.hacker_news
start "S-TechCrunch" /min python -m connectors.techcrunch
start "S-Techmeme" /min python -m connectors.techmeme
start "S-Finviz" /min python -m connectors.finviz
start "S-Newsletters" /min python -m connectors.newsletter_feeds
start "S-SeekingAlpha" /min python -m connectors.seeking_alpha
start "S-FedRegister" /min python -m connectors.federal_register
start "S-SAMgov" /min python -m connectors.sam_gov
start "S-USAspending" /min python -m connectors.usaspending
start "S-SBIR" /min python -m connectors.sbir
start "S-EconomicData" /min python -m connectors.economic_data
start "S-OpenInsider" /min python -m connectors.openinsider
start "S-Binance" /min python -m connectors.binance
start "S-FinNews" /min python -m connectors.financial_news
start "S-SECInsider" /min python -m connectors.sec_insider
start "S-FRED" /min python -m connectors.fred
start "S-Alpaca" /min python -m connectors.alpaca_market
start "S-Earnings" /min python -m connectors.earnings_calendar
start "S-ProductHunt" /min python -m connectors.producthunt

echo [3/5] Starting pipeline...
timeout /t 5 /nobreak >nul
start "S-Normalization" /min python -m normalization
start "S-Correlation" /min python -m correlation

echo [4/5] Starting engines...
timeout /t 5 /nobreak >nul
start "S-StockAlpha" /min python -m stock_alpha
start "S-ProductIdeation" /min python -m product_ideation
start "S-Tracker" /min python -m stock_alpha.tracker

echo [5/5] Starting API and dashboard...
timeout /t 3 /nobreak >nul
start "S-API" /min python -m api.server
timeout /t 2 /nobreak >nul
start "S-Dashboard" /min cmd /k "cd /d C:\Users\micha\projects\osint-pipeline\dashboard && npx next dev -p 3000"

echo.
echo ============================================
echo   Sentinel is running!
echo   Dashboard: http://localhost:3000
echo   API:       http://localhost:8000
echo ============================================
echo.
echo To stop: taskkill /F /FI "WINDOWTITLE eq S-*"
pause
