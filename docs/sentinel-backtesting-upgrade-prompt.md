# Backtesting Page Upgrade — Claude Code Prompt

Paste into Claude Code:

---

Upgrade the backtesting page at `dashboard/src/app/backtesting/page.tsx` with 7 new visualizations. Also update `backtesting/metrics.py` to output the additional data these charts need. Keep all existing content (stat cards, calibration curve, feature importances, confusion matrix, per-ticker table) and ADD the new sections around them.

Follow the existing patterns exactly: Recharts for charts, Tailwind with Sentinel design tokens (card-lg, bg-surface, bg-surface-alt, text-bullish, text-bearish, font-mono, border-border), dark terminal aesthetic.

## Step 1: Update backtesting/metrics.py output

The backtest results JSON saved to `backtesting/results/{timestamp}.json` must include these additional fields alongside the existing ones:

```python
{
    # ... existing fields (accuracy, sharpe, profit_factor, max_drawdown, calibration_curve, feature_importances, per_ticker, confusion_matrix) ...

    # NEW: Equity curve data (cumulative return per day)
    "equity_curve": [
        {"date": "2024-01-02", "sentinel_return": 0.0, "spy_return": 0.0, "regime": "trending_up"},
        {"date": "2024-01-03", "sentinel_return": 0.4, "spy_return": 0.2, "regime": "trending_up"},
        # ... one entry per trading day
    ],

    # NEW: Drawdown time series
    "drawdown_curve": [
        {"date": "2024-01-02", "drawdown": 0.0},
        {"date": "2024-01-03", "drawdown": -0.3},
        # ... one entry per trading day, always <= 0
    ],
    "max_drawdown_date": "2024-06-15",
    "max_drawdown_recovery_days": 12,

    # NEW: Rolling accuracy over time with regime labels
    "accuracy_over_time": [
        {"date": "2024-02-01", "rolling_30d_accuracy": 0.62, "regime": "trending_up"},
        # ... one entry per trading day (starts 30 days in)
    ],

    # NEW: Return by confidence band
    "return_by_confidence": [
        {"band": "50-60%", "avg_return": 0.2, "accuracy": 0.54, "count": 423},
        {"band": "60-70%", "avg_return": 0.6, "accuracy": 0.61, "count": 612},
        {"band": "70-80%", "avg_return": 1.1, "accuracy": 0.67, "count": 389},
        {"band": "80-90%", "avg_return": 1.8, "accuracy": 0.74, "count": 201},
        {"band": "90%+", "avg_return": 2.4, "accuracy": 0.81, "count": 87},
    ],

    # NEW: Signal decay analysis
    "signal_decay": [
        {"hours": 1, "accuracy": 0.55},
        {"hours": 4, "accuracy": 0.59},
        {"hours": 8, "accuracy": 0.62},
        {"hours": 24, "accuracy": 0.65},
        {"hours": 48, "accuracy": 0.67},
        {"hours": 72, "accuracy": 0.68},
        {"hours": 168, "accuracy": 0.61},
    ],

    # NEW: Benchmark comparison
    "benchmarks": {
        "sentinel_ensemble": {"total_return": 28.4, "sharpe": 1.47, "max_drawdown": -8.3, "accuracy": 64.2},
        "sentinel_rule_only": {"total_return": 18.1, "sharpe": 1.02, "max_drawdown": -11.2, "accuracy": 58.7},
        "spy_buy_hold": {"total_return": 22.6, "sharpe": 0.91, "max_drawdown": -12.8, "accuracy": null},
        "random_baseline": {"total_return": -2.1, "sharpe": -0.12, "max_drawdown": -18.4, "accuracy": 50.0},
        "sentiment_only": {"total_return": 12.3, "sharpe": 0.68, "max_drawdown": -14.1, "accuracy": 56.3},
        "technicals_only": {"total_return": 15.7, "sharpe": 0.82, "max_drawdown": -13.5, "accuracy": 57.1},
    },

    # NEW: Per-prediction data for confidence threshold filtering
    "predictions_detail": [
        {"date": "2024-01-02", "ticker": "AAPL", "direction": "bullish", "confidence": 0.72, "outcome": "correct", "return": 1.2, "regime": "trending_up", "horizon_hours": 24},
        # ... every prediction from the backtest
    ]
}
```

Compute these in metrics.py from the existing backtesting data. The equity_curve is computed by summing daily returns from correct/incorrect predictions weighted by confidence. The drawdown_curve is computed from the equity_curve. The accuracy_over_time is a 30-day rolling window over the predictions sorted by date. The return_by_confidence bins predictions into 5 bands and computes average return and accuracy per band. The signal_decay evaluates the same predictions at multiple horizons. The benchmarks run the same date range through simple strategies (buy-and-hold, random, etc.) using yfinance price data.

## Step 2: Update the backtesting page layout

The page layout should be, in order from top to bottom:

1. **Banner** (existing — keep as-is)
2. **4 stat cards** (existing — keep as-is)
3. **Interactive confidence threshold slider** (NEW — add ABOVE the equity curve)
   - A range slider from 50% to 95% with a label showing the current threshold and the count of predictions that pass it
   - When the user drags the slider, ALL visualizations below filter to only include predictions at or above that confidence level
   - Use React useState for the threshold and useMemo to filter predictions_detail
   - All stat cards also recalculate based on the filtered data
4. **Equity curve** (NEW — full width, tallest chart on the page ~300px)
   - Recharts AreaChart with two lines: Sentinel cumulative return (teal fill below) and SPY buy-and-hold (gray dashed line)
   - Background ReferenceAreas colored by regime period
   - Tooltip shows date, Sentinel return, SPY return, regime
5. **Drawdown chart** (NEW — full width, ~200px height)
   - Recharts AreaChart inverted. Fill in coral (opacity 0.3). Mark the max drawdown point with a red dot and label showing the drawdown % and recovery days
6. **Accuracy over time** (NEW — full width)
   - Recharts ComposedChart. Line = 30-day rolling accuracy. Background ReferenceAreas colored by regime. Horizontal ReferenceLine at 50% (dashed gray, "random") and 60% (dashed teal, "target")
7. **Return by confidence band + Signal decay** (NEW — 2 columns side by side)
   - LEFT: Recharts BarChart. One bar per confidence band. Color by return: teal positive, coral negative. Label each bar with count.
   - RIGHT: Recharts LineChart. X = hours, Y = accuracy. Line color transitions: teal > 60%, amber 50-60%, gray < 50%. Annotate peak accuracy with "sweet spot" label.
8. **Benchmark comparison** (NEW — full width table or grouped bar chart)
   - Table with columns: Strategy, Total Return, Sharpe, Max Drawdown, Accuracy
   - Sentinel ensemble row highlighted with teal left border
   - Color total return: teal positive, coral negative
   - Or: grouped BarChart with one group per strategy, 3 bars each (return, Sharpe*10, accuracy*100)
9. **Calibration curve** (existing — keep)
10. **Feature importances + Confusion matrix** (existing — keep, side by side)
11. **Per-ticker performance table** (existing — keep, make columns sortable if not already)

## Step 3: Confidence threshold filtering implementation

```typescript
// At the top of the page component:
const [confidenceThreshold, setConfidenceThreshold] = useState(50);
const backtest = useBacktestResults();

const filteredPredictions = useMemo(() => {
  if (!backtest?.results?.predictions_detail) return [];
  return backtest.results.predictions_detail.filter(
    (p: any) => p.confidence * 100 >= confidenceThreshold
  );
}, [backtest, confidenceThreshold]);

// Compute filtered stats from filteredPredictions
const filteredStats = useMemo(() => {
  const total = filteredPredictions.length;
  const correct = filteredPredictions.filter((p: any) => p.outcome === 'correct').length;
  const accuracy = total > 0 ? correct / total : 0;
  const returns = filteredPredictions.map((p: any) => p.return || 0);
  const avgReturn = returns.length > 0 ? returns.reduce((a: number, b: number) => a + b, 0) / returns.length : 0;
  // ... compute filtered Sharpe, drawdown, etc.
  return { total, correct, accuracy, avgReturn };
}, [filteredPredictions]);
```

The slider UI:
```tsx
<div className="flex items-center gap-4 p-4 bg-surface border border-border rounded-card mb-4">
  <span className="text-text-secondary text-sm">Min confidence</span>
  <input
    type="range"
    min={50}
    max={95}
    value={confidenceThreshold}
    onChange={(e) => setConfidenceThreshold(Number(e.target.value))}
    className="flex-1"
  />
  <span className="font-mono text-text-primary font-semibold text-lg">{confidenceThreshold}%</span>
  <span className="text-text-muted text-xs">{filteredPredictions.length} predictions</span>
</div>
```

When threshold is 50% (default), show all data = same as current behavior. As user drags higher, charts filter to show only high-confidence results.

## Chart color reference (use these exact tokens):
- Bullish/correct/positive: #00FFC2
- Bearish/incorrect/negative: #FF4B2B
- Accent blue: #58A6FF
- Amber/warning: #E8A317
- Neutral/gray: #8B949E
- Surface: #161B22
- Surface alt: #1C2128
- Border: #21262D
- Text primary: #E6EDF3
- Text muted: #7D8590

## Mock data

Update `dashboard/data/backtestResults.json` to include all the new fields with realistic sample data. This serves as the fallback when the API isn't running.

## Files modified:
- `backtesting/metrics.py` — add computation of equity_curve, drawdown_curve, accuracy_over_time, return_by_confidence, signal_decay, benchmarks, predictions_detail
- `dashboard/src/app/backtesting/page.tsx` — add 7 new chart sections + confidence slider + filtering logic
- `dashboard/data/backtestResults.json` — add mock data for all new fields

DO NOT TOUCH any other files.
