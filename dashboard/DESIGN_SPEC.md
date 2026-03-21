# Sentinel | Social Alpha — Design Specification

> **Reference Document** — This file contains the full design rationale, wireframe concepts, interaction patterns, and component specifications for the Sentinel dashboard. Claude Code should read this file when building any dashboard component.
>
> See also: `dashboard/CLAUDE.md` for build instructions and tech stack.

---

## 1. Product Vision

Sentinel is a "Bloomberg Terminal for OSINT and Social Sentiment." It ingests massive amounts of real-time social media data, product reviews, and financial news to:

- **Predict stock movements** by correlating social sentiment with price action
- **Identify product innovation gaps** through aspect-based sentiment analysis
- **Surface emerging risks** before they hit mainstream financial news

**Target Users:** Financial analysts, portfolio managers, product strategists, competitive intelligence teams.

**Core UX Principle:** *"Understand the market's mood in under 5 seconds."*

---

## 2. Design System

### 2.1 Color Palette

```
Background Layers:
  base:          #0A0E12   (Rich Black — page background)
  surface:       #161B22   (Deep Grey — card backgrounds)
  surface-alt:   #1C2128   (Elevated Grey — tooltips, dropdowns, overlays)
  border:        #21262D   (Subtle borders between modules)

Semantic Colors:
  bullish:       #00FFC2   (Neon Mint — positive sentiment, price gains)
  bullish-muted: #00FFC233 (Mint at 20% opacity — area fills, backgrounds)
  bearish:       #FF4B2B   (Electric Orange — negative sentiment, losses, alerts)
  bearish-muted: #FF4B2B33 (Orange at 20% opacity — area fills, backgrounds)
  neutral:       #8B949E   (Slate — secondary text, axis labels, grid lines)
  accent-blue:   #58A6FF   (Interactive elements, links, active states)
  volume-spike:  #3B82F6   (Volume anomaly detection markers)

Text:
  text-primary:  #E6EDF3   (Headings, primary content)
  text-secondary:#B1BAC4   (Descriptions, secondary labels)
  text-muted:    #7D8590   (Tertiary, timestamps, fine print)
```

### 2.2 Typography

| Role              | Font          | Weight | Size    | Tracking   |
|-------------------|---------------|--------|---------|------------|
| Page titles       | Inter         | 600    | 20–24px | -0.02em    |
| Section headers   | Inter         | 600    | 14–16px | -0.01em    |
| UI labels/nav     | Inter         | 500    | 13–14px | normal     |
| Body text         | Inter         | 400    | 13–14px | normal     |
| Data values       | Roboto Mono   | 500    | 13–14px | normal     |
| Ticker symbols    | Roboto Mono   | 600    | 14–16px | 0.05em     |
| Timestamps        | Roboto Mono   | 400    | 11–12px | normal     |
| Chart axis labels | Roboto Mono   | 400    | 11px    | normal     |

### 2.3 Spacing & Grid

- **Grid system:** CSS Grid with auto-fit/minmax for responsive behavior
- **Module gap:** 12px (compact) to 16px (comfortable)
- **Card padding:** 16px (small cards) to 20px (large cards)
- **Card border-radius:** 8px
- **Card border:** 1px solid `#21262D`
- **No drop shadows.** Depth is communicated through layered background colors.
- **Negative space is intentional.** Do not fill every gap with content.

### 2.4 Iconography & Indicators

- Alert dots: 8px circles, color = severity (bearish red, volume-spike blue)
- Trend arrows: Small SVG chevrons (↗ bullish, ↘ bearish), colored to match
- Section headers: Use `···` (three-dot menu) icon for card options/settings
- Navigation: Lucide React icons (Home, TrendingUp, Lightbulb, Settings)

---

## 3. View Specifications

### 3.1 Global Pulse (Home Dashboard)

**Purpose:** Personalized, real-time overview. The user understands market mood at a glance.

**Layout:** "Bento-Box" CSS Grid

```
┌──────────────────────────────────────────┬─────────────────────┐
│              HEADER BAR                   │                     │
│  [Logo] SENTINEL | Global Pulse  Live ⏱  │  🔍 Search...       │
├──────────────────────────────────────────┼─────────────────────┤
│                                          │                     │
│         SENTIMENT HEAT-SPHERE            │    SIGNAL FEED      │
│         (D3.js Force Bubble)             │    (Scrolling)      │
│                                          │                     │
│  Each bubble = sector/ticker             │  🔴 NVDA: Abnormal  │
│  Size = volume                           │     volume on       │
│  Color = sentiment                       │     Reddit r/GPU    │
│  Hover = tooltip with keywords           │                     │
│                                          │  🔵 AAPL: +4.2%    │
│        ┌────────────────────┐            │     price corr on   │
│        │ TSLA | 92% Pos     │            │     news break      │
│        │ Keywords: CEO,     │            │                     │
│        │ Supply Chain,      │            │  [Real Feed         │
│        │ Earnings Beat      │            │   Insights...]      │
│        └────────────────────┘            │                     │
├──────────┬──────────┬──────────┬─────────┴─────────────────────┤
│ Macro    │ Top 5    │ Emerging │ Economic                      │
│ Sent vs  │ Sector   │ Risks    │ Sentiments                    │
│ S&P 500  │ Sent.    │          │                               │
│ (dual    │ (h-bar)  │ (h-bar)  │ (h-bar, mixed)               │
│  axis)   │          │          │                               │
├──────────┴──────────┴──────────┴──────────────────────────────┤
│ Timeframe: [1D] [5D] [1M] [YTD] [1Y] [5Y]  ═══●════●═══    │
└───────────────────────────────────────────────────────────────┘
```

#### 3.1.1 Sentiment Heat-Sphere

**Type:** D3.js force-directed bubble chart

**Data shape:**
```json
{
  "sectors": [
    {
      "id": "TSLA",
      "label": "TSLA",
      "sector": "Tech",
      "sentiment": 0.92,
      "volume": 45000,
      "priceChange24h": 4.2,
      "keywords": ["CEO Scandal", "Supply Chain", "Earnings Beat"]
    }
  ]
}
```

**Visual encoding:**
- Bubble radius: `sqrt(volume) * scaleFactor` (square root scale to prevent huge bubbles)
- Bubble color: Continuous gradient from `bearish` (sentiment 0.0) through `neutral` (0.5) to `bullish` (1.0)
- Bubble opacity: 0.8 default, 1.0 on hover
- Y-position bias: Positive price change → higher; negative → lower (subtle, not strict axis)

**Interactions:**
- **Hover:** Freeze all bubble animation. Show tooltip anchored to bubble:
  - Ticker | Sentiment % (e.g., "TSLA | 92% Pos")
  - "Keywords: CEO Scandal, Supply Chain, Earnings Beat"
  - Tooltip style: `surface-alt` bg, 1px `border` color, `text-primary`
- **Click:** Navigate to `/alpha/[ticker]` (Financial Alpha View)
- **Animation:** Gentle continuous force simulation (collision detection, no overlap)

#### 3.1.2 Signal Feed

**Type:** Vertical scrolling card list

**Card anatomy:**
```
┌─────────────────────────────────────┐
│ 🔴 NVDA: Abnormal volume detected  │
│    on Reddit (r/GPU).               │
├─────────────────────────────────────┤
│ 🔵 AAPL: +4.2% price correlation   │
│    on news break.                   │
└─────────────────────────────────────┘
```

- Alert dot color: `bearish` for risk, `volume-spike` for volume anomaly, `bullish` for positive signal
- Text: `text-primary` for ticker + headline, `text-muted` for source
- Card spacing: 8px vertical gap
- Auto-scroll: New items appear at top, push existing items down
- Pause on hover/focus
- Bottom: Text input with placeholder "Real Feed Insights..." for analyst annotations

#### 3.1.3 Bottom Chart Cards

All four cards share consistent styling:
- Background: `surface`
- Border: 1px `border` color
- Header: Section title (Inter 600, 14px) + `···` menu icon
- Chart area: Transparent bg, grid lines at `neutral` 10% opacity

**Card 1 — Macro Sentiment vs. S&P 500:**
- Dual-axis line chart
- Left Y-axis: Sentiment score (60–130 range, labeled)
- Right Y-axis: S&P 500 price (300–350 range, labeled)
- X-axis: Years (2000–2020)
- Sentiment line: `bullish` color
- S&P line: `neutral` or `accent-blue`

**Card 2 — Top 5 Sector Sentiment:**
- Horizontal bar chart
- Categories: Tech, TSLA, NVDA, Economy, Retail
- Bar color: `bullish`
- X-axis: 0–100

**Card 3 — Emerging Risks:**
- Horizontal bar chart
- Categories: Retail, Energy, AMZN, Contesting, Scandal
- Bar color: `bearish`
- X-axis: 0–100

**Card 4 — Economic Sentiments:**
- Horizontal bar chart
- Categories: Emerging Risks, Forehoting, Encorrencies, Teronaration, Other
- Bar colors: Mixed `bullish` and `bearish` to show directional sentiment
- X-axis: 0–80

#### 3.1.4 Timeframe Selector

- Quick presets row: `1D | 5D | 1M | YTD | 1Y | 5Y` as pill buttons
- Active preset: `accent-blue` background, white text
- Inactive: `surface` background, `text-muted` text
- Below presets: Dual-handle range slider
  - Track: `neutral` at 20% opacity
  - Active range: `accent-blue`
  - Handles: 12px circles, `accent-blue` fill
- **Progressive loading:** Only request high-resolution data for visible time window. Zooming in triggers "Detail Fetch."

---

### 3.2 Financial Alpha View

**Purpose:** Layer sentiment data directly onto stock price action to reveal correlations.

**Layout:**
```
┌───────────────────────────────────────────────────────────────┐
│ [← Back]  TSLA — Tesla Inc.   $248.52  ▲ +4.2%   Sent: 92%  │
├─────────────────────────────────────────┬─────────────────────┤
│                                         │                     │
│       DUAL-AXIS TEMPORAL PLOT           │   NEWS INSIGHTS     │
│                                         │                     │
│  ┃ Candlestick (price)    ┃ Sentiment  │   📰 "Tesla Q4      │
│  ┃ █ █ █ █ ┊ ┊ ┊ ┊       ┃ area fill  │      earnings beat  │
│  ┃ █ █ █ █ ┊ ┊ ┊ ┊       ┃            │      expectations"  │
│  ┃         ╌╌╌╌ (ghost)   ┃            │      — Reuters      │
│  ┃         ░░░░ (conf.)   ┃            │                     │
│  ┃    📌 📌               ┃            │   📰 "CEO denies    │
│  ┃                        ┃            │      supply chain    │
│  └────────────────────────┘            │      disruption"    │
│                                         │      — Bloomberg    │
├─────────────────────────────────────────┴─────────────────────┤
│ [1D] [5D] [1M] [YTD] [1Y] [5Y]  ═══●════●═══                │
└───────────────────────────────────────────────────────────────┘
```

**Dual-Axis Temporal Plot:**
- Primary Y-axis: Candlestick chart (green candles = up day, red candles = down day)
- Secondary Y-axis: Aggregate sentiment score as gradient area fill behind candles
  - Fill: `bullish-muted` when sentiment > 0.5, `bearish-muted` when < 0.5
- **"Prediction Ghost":** Future predicted prices rendered as:
  - Dashed line (2px dash, `accent-blue`)
  - Confidence interval: Shaded area that widens further into the future
  - Confidence fill: `accent-blue` at 10% opacity
- **Sentiment Pins:** Small diamond markers (📌) at points of peak sentiment velocity
  - Click pin → News Insight Overlay appears showing the headline/tweet that triggered the move
  - Overlay: `surface-alt` bg, close button, timestamp, source, headline text

**News Insights Panel:**
- Right sidebar or slide-out drawer
- Chronologically ordered
- Each item: headline text, source badge (Reuters, Bloomberg, Reddit, etc.), timestamp
- Click to expand: full snippet + link to source

---

### 3.3 Product Innovation View

**Purpose:** Move from macro market signals to micro product-feature insights.

**Layout:**
```
┌───────────────────────────────────────────────────────────────┐
│ Product Innovation & Ideation                                 │
├───────────────────────────────────────────────────────────────┤
│ Channel: [Amazon] [Twitter/X] [Reddit] [YouTube]  | Filters  │
├───────────────────────────┬───────────────────────────────────┤
│                           │                                   │
│   TOP 5 CRITICISMS        │   TOP 5 REQUESTED FEATURES       │
│   (The Pain)              │   (The Opportunity)               │
│                           │                                   │
│   ████████████ Battery    │   ████████████ Longer battery    │
│   ██████████   Screen     │   ██████████   USB-C             │
│   ████████     Price      │   ████████     Repairability     │
│   ██████       Software   │   ██████       AI features       │
│   ████         Durability │   ████         Modular design    │
│                           │                                   │
├───────────────────────────┴───────────────────────────────────┤
│                                                               │
│              INNOVATION GAP MAP                               │
│                                                               │
│  Importance │ ★ INNOVATION   │                               │
│  to User    │    ZONE        │  (items here = opportunity)   │
│    (high)   │                │                               │
│             ├────────────────┤                               │
│    (low)    │                │                               │
│             └────────────────┘                               │
│               (low)  Satisfaction  (high)                    │
│                                                               │
└───────────────────────────────────────────────────────────────┘
```

**Channel Slicer:**
- Segmented control / toggle group at the top
- Active channel: `accent-blue` background
- Switching channels animates chart data transition (300ms ease)

**Feature Friction Board:**
- Two side-by-side cards with distinct headers
- "Top 5 Criticisms": Header with `bearish` accent, bar color intensity = emotional anger/frustration detected by NLP
- "Top 5 Requested Features": Header with `bullish` accent, bar color intensity = request frequency
- Bar labels on left, horizontal bars extending right
- Bar length = mention volume, color saturation = intensity

**Innovation Gap Map:**
- Scatter plot
- X-axis: "Current Satisfaction" (low → high)
- Y-axis: "Importance to User" (low → high)
- Each dot = a product feature/aspect
- Dot size = volume of mentions
- Top-left quadrant (High Importance, Low Satisfaction) auto-highlighted with dashed border and label "Innovation Zone"
- Quadrant fill: `bullish-muted` at 5% opacity
- Hover dot: tooltip showing feature name, satisfaction score, importance score, sample quote

---

## 4. Navigation & Responsiveness

### 4.1 Desktop Sidebar (≥1280px)
- Width: 56px collapsed (icon-only), 200px expanded (on hover)
- Position: Fixed left
- Items: Home, Financial Alpha, Product Innovation, Settings
- Active indicator: 3px left border in `accent-blue`
- Transition: 200ms ease width expansion

### 4.2 Tablet (768–1279px)
- Sidebar: Always collapsed (icon-only), no expansion
- Grid: 2-column layout
- Signal Feed: Collapsible drawer (swipe or toggle)
- Charts: Stack vertically if needed

### 4.3 Mobile (<768px)
- No sidebar. Use bottom tab bar (4 icons)
- Single-column layout
- Charts: Simplified to sparklines or small bar charts
- Signal Feed: Full-screen sheet (pull up from bottom)
- Heat-Sphere: Simplified to a sortable list or small bubble view

---

## 5. Interaction Patterns

### 5.1 Filtering Large Datasets
- **Quick presets** handle 80% of use cases (1D, 5D, 1M, YTD, 1Y, 5Y)
- **Range slider** for custom windows
- **Progressive disclosure:** Start with low-resolution overview; zooming triggers high-resolution data fetch
- **Debounced updates:** 200ms debounce on slider changes before triggering data refresh
- All filter state lives in URL params for shareability

### 5.2 Hover & Click Behaviors
- Hover: Preview/inspect (tooltips, highlights)
- Click: Navigate or drill down (open detail view, expand panel)
- All hover effects have 150ms transition delay (prevent flicker on fast mouse movement)
- Focus states for keyboard accessibility (use `accent-blue` ring, 2px offset)

### 5.3 Loading States
- Skeleton screens matching card dimensions (pulsing `surface-alt` blocks)
- Never show empty cards. Always show skeleton or "No data available" state.
- Chart data transitions: 300ms ease-in-out animation on data change

---

## 6. Mock Data Schemas

### signals.json
```json
[
  {
    "id": "sig-001",
    "type": "bearish",
    "ticker": "NVDA",
    "headline": "Abnormal volume detected on Reddit (r/GPU).",
    "source": "Reddit",
    "timestamp": "2025-03-20T14:32:00Z",
    "confidence": 0.87
  }
]
```

### sectors.json
```json
[
  {
    "id": "TSLA",
    "label": "TSLA",
    "sector": "Tech",
    "sentiment": 0.92,
    "volume": 45000,
    "priceChange24h": 4.2,
    "keywords": ["CEO Scandal", "Supply Chain", "Earnings Beat"]
  }
]
```

### sectorSentiment.json
```json
{
  "topSectors": [
    { "name": "Tech", "score": 87 },
    { "name": "TSLA", "score": 72 },
    { "name": "NVDA", "score": 58 },
    { "name": "Economy", "score": 41 },
    { "name": "Retail", "score": 34 }
  ],
  "emergingRisks": [
    { "name": "Retail", "score": 89 },
    { "name": "Energy", "score": 76 },
    { "name": "AMZN", "score": 68 },
    { "name": "Contesting", "score": 55 },
    { "name": "Scandal", "score": 48 }
  ]
}
```

### stockHistory.json (per ticker)
```json
{
  "ticker": "TSLA",
  "company": "Tesla Inc.",
  "currentPrice": 248.52,
  "change24h": 4.2,
  "sentimentScore": 0.92,
  "candles": [
    { "date": "2025-03-19", "open": 240, "high": 250, "low": 238, "close": 248.52, "volume": 12000000 }
  ],
  "sentimentTimeline": [
    { "date": "2025-03-19", "score": 0.88 }
  ],
  "predictions": [
    { "date": "2025-03-21", "predicted": 252, "confidenceLow": 245, "confidenceHigh": 260 }
  ]
}
```

### productFeatures.json
```json
{
  "channel": "Amazon",
  "product": "Smartphone X",
  "criticisms": [
    { "feature": "Battery Life", "volume": 12400, "intensity": 0.91 },
    { "feature": "Screen Quality", "volume": 8900, "intensity": 0.72 }
  ],
  "requests": [
    { "feature": "Longer Battery", "volume": 15200, "intensity": 0.88 },
    { "feature": "USB-C", "volume": 11300, "intensity": 0.79 }
  ],
  "gapMap": [
    { "feature": "Battery", "satisfaction": 0.3, "importance": 0.95, "volume": 12400 },
    { "feature": "Camera", "satisfaction": 0.85, "importance": 0.8, "volume": 5600 }
  ]
}
```

---

## 7. Accessibility Requirements

- All interactive elements keyboard-navigable
- Focus rings visible (`accent-blue`, 2px)
- Charts: Provide data table fallback (accessible via screen reader)
- Color is never the only differentiator — always pair with shape, position, or label
- Minimum contrast ratio: 4.5:1 for body text, 3:1 for large text
- ARIA labels on all icon-only buttons and chart elements

---

## 8. Performance Targets

- First Contentful Paint: < 1.5s
- Time to Interactive: < 3s
- Chart render (with mock data): < 500ms
- D3 force simulation: 60fps target, degrade gracefully on mobile
- Bundle size budget: < 200KB initial JS (code-split per view)
