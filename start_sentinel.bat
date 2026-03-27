@echo off
title Sentinel Launcher
cd /d C:\Users\micha\projects\osint-pipeline

echo ============================================
echo   Sentinel ^| Social Alpha - Starting...
echo ============================================

:: 1. Start Docker Kafka stack
echo [1/6] Starting Kafka stack...
docker-compose up -d

:: Wait for Kafka to be healthy
echo [2/6] Waiting for Kafka to be ready...
timeout /t 15 /nobreak >/dev/null

:: 3. Start connectors (no-auth sources)
echo [3/6] Starting connectors...
start "Sentinel: HackerNews"       cmd /k "cd /d C:\Users\micha\projects\osint-pipeline && conda activate base && python -m connectors.hacker_news"
start "Sentinel: TechCrunch"       cmd /k "cd /d C:\Users\micha\projects\osint-pipeline && conda activate base && python -m connectors.techcrunch"
start "Sentinel: Techmeme"         cmd /k "cd /d C:\Users\micha\projects\osint-pipeline && conda activate base && python -m connectors.techmeme"
start "Sentinel: Finviz"           cmd /k "cd /d C:\Users\micha\projects\osint-pipeline && conda activate base && python -m connectors.finviz"
start "Sentinel: Newsletters"      cmd /k "cd /d C:\Users\micha\projects\osint-pipeline && conda activate base && python -m connectors.newsletter_feeds"
start "Sentinel: SeekingAlpha"     cmd /k "cd /d C:\Users\micha\projects\osint-pipeline && conda activate base && python -m connectors.seeking_alpha"
start "Sentinel: FederalRegister"  cmd /k "cd /d C:\Users\micha\projects\osint-pipeline && conda activate base && python -m connectors.federal_register"
start "Sentinel: SAM.gov"          cmd /k "cd /d C:\Users\micha\projects\osint-pipeline && conda activate base && python -m connectors.sam_gov"
start "Sentinel: USAspending"      cmd /k "cd /d C:\Users\micha\projects\osint-pipeline && conda activate base && python -m connectors.usaspending"
start "Sentinel: SBIR"             cmd /k "cd /d C:\Users\micha\projects\osint-pipeline && conda activate base && python -m connectors.sbir"
start "Sentinel: EconomicData"     cmd /k "cd /d C:\Users\micha\projects\osint-pipeline && conda activate base && python -m connectors.economic_data"
start "Sentinel: OpenInsider"      cmd /k "cd /d C:\Users\micha\projects\osint-pipeline && conda activate base && python -m connectors.openinsider"
start "Sentinel: Binance"          cmd /k "cd /d C:\Users\micha\projects\osint-pipeline && conda activate base && python -m connectors.binance"

:: Connectors that need API keys (uncomment if keys are set in .env)
:: start "Sentinel: Reddit"         cmd /k "cd /d C:\Users\micha\projects\osint-pipeline && conda activate base && python -m connectors.reddit"
:: start "Sentinel: GitHub"         cmd /k "cd /d C:\Users\micha\projects\osint-pipeline && conda activate base && python -m connectors.github"
:: start "Sentinel: TrustPilot"     cmd /k "cd /d C:\Users\micha\projects\osint-pipeline && conda activate base && python -m connectors.trustpilot"
:: start "Sentinel: SEC Insider"    cmd /k "cd /d C:\Users\micha\projects\osint-pipeline && conda activate base && python -m connectors.sec_insider"
:: start "Sentinel: FinancialNews"  cmd /k "cd /d C:\Users\micha\projects\osint-pipeline && conda activate base && python -m connectors.financial_news"
:: start "Sentinel: UnusualWhales"  cmd /k "cd /d C:\Users\micha\projects\osint-pipeline && conda activate base && python -m connectors.unusual_whales"
:: start "Sentinel: FRED"           cmd /k "cd /d C:\Users\micha\projects\osint-pipeline && conda activate base && python -m connectors.fred"
:: start "Sentinel: GoogleTrends"   cmd /k "cd /d C:\Users\micha\projects\osint-pipeline && conda activate base && python -m connectors.google_trends"
:: start "Sentinel: ProductHunt"    cmd /k "cd /d C:\Users\micha\projects\osint-pipeline && conda activate base && python -m connectors.producthunt"
:: start "Sentinel: Alpaca"         cmd /k "cd /d C:\Users\micha\projects\osint-pipeline && conda activate base && python -m connectors.alpaca_market"

:: 4. Start processing pipeline
echo [4/6] Starting normalization and correlation...
timeout /t 5 /nobreak >/dev/null
start "Sentinel: Normalization"    cmd /k "cd /d C:\Users\micha\projects\osint-pipeline && conda activate base && python -m normalization"
start "Sentinel: Correlation"      cmd /k "cd /d C:\Users\micha\projects\osint-pipeline && conda activate base && python -m correlation"

:: 5. Start value engines
echo [5/6] Starting value engines...
timeout /t 5 /nobreak >/dev/null
start "Sentinel: StockAlpha"       cmd /k "cd /d C:\Users\micha\projects\osint-pipeline && conda activate base && python -m stock_alpha"
start "Sentinel: ProductIdeation"  cmd /k "cd /d C:\Users\micha\projects\osint-pipeline && conda activate base && python -m product_ideation"

:: 6. Start dashboard
echo [6/6] Starting dashboard...
start "Sentinel: Dashboard"        cmd /k "cd /d C:\Users\micha\projects\osint-pipeline\dashboard && npm run dev"

echo.
echo ============================================
echo   Sentinel is running!
echo   Dashboard:  http://localhost:3000
echo   Kafka UI:   http://localhost:8080
echo ============================================
echo.
echo Close this window to keep everything running.
echo To stop everything: docker-compose down ^& close all Sentinel windows.
pause
