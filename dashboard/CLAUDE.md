# Sentinel | Social Alpha — Dashboard Frontend

## Role
You are a Senior Lead UX/UI Designer, Data Visualization Expert, and Frontend Engineer building a modern "Bloomberg Terminal for OSINT and Social Sentiment." This dashboard ingests massive real-time social media data, product reviews, and financial news to predict stock movements and identify product innovation gaps.

## Core Design Philosophy
- **Exceptionally informational yet minimalist.** High information density with zero clutter.
- **Glanceability first.** A user should understand the market's "mood" in under 5 seconds.
- **Actionable insights over raw data dumps.** Every pixel earns its place.
- **Dark-mode financial terminal aesthetic.** Professional, focused, low eye strain.

## Tech Stack
- **Framework:** Next.js 14+ (App Router, TypeScript)
- **Styling:** Tailwind CSS (dark mode default)
- **Visualizations:** D3.js for custom viz (heat-sphere, force-directed graphs), Recharts or Chart.js for standard charts
- **Data:** Mock JSON in `/data/` during development; real API integration later
- **Fonts:** Inter (UI labels, navigation), Roboto Mono (data points, numbers, tickers)

## Design System Tokens

### Color Palette
| Token          | Hex       | Usage                              |
|----------------|-----------|-------------------------------------|
| `base`         | `#0A0E12` | Page background                    |
| `surface`      | `#161B22` | Card/module backgrounds            |
| `surface-alt`  | `#1C2128` | Elevated surfaces, tooltips        |
| `bullish`      | `#00FFC2` | Positive sentiment, gains, success |
| `bearish`      | `#FF4B2B` | Negative sentiment, losses, alerts |
| `neutral`      | `#8B949E` | Secondary text, axes, borders      |
| `text-primary` | `#E6EDF3` | Primary text                       |
| `text-muted`   | `#7D8590` | Muted/tertiary text                |
| `accent-blue`  | `#58A6FF` | Links, interactive highlights      |
| `volume-spike` | `#3B82F6` | Volume anomaly indicators          |

### Typography
- UI labels/nav: `Inter`, weights 400/500/600
- Data/numbers/tickers: `Roboto Mono`, weights 400/500
- Headings: `Inter`, weight 600, tracking tight
- Minimum data font size: 11px (legibility on dense dashboards)

### Spacing & Layout
- Use a responsive CSS Grid ("Bento-Box" layout)
- Card border-radius: 8px
- Card padding: 16px–20px
- Module gap: 12px–16px
- Use negative space deliberately — do not fill every gap

### Component Patterns
- **Cards:** Dark surface (`#161B22`), subtle 1px border (`#21262D`), no drop shadows
- **Tooltips:** `surface-alt` background, 1px border matching neutral, appear on hover with 150ms delay
- **Alert badges:** Small colored dot (bearish red 🔴 or volume-spike blue 🔵) — no large icons
- **Charts:** Transparent backgrounds, grid lines at 10% opacity, axis text in `neutral` color
- **Scrolling feeds:** Smooth auto-scroll, pause on hover, newest items enter from top

## Architecture — Three Core Views

### 1. Global Pulse (Home Dashboard — `/`)
The default landing view. "Bento-box" grid layout.

**Components (build in this order):**
1. **Header Bar** — Sentinel logo, "Global Pulse" title, live timer (countdown/elapsed), search bar (tickers & topics)
2. **Sentiment Heat-Sphere** (center, largest module)
   - D3.js force-directed bubble chart
   - Each bubble = sector or ticker
   - Bubble size = mention volume
   - Bubble color = sentiment (bullish gradient → bearish gradient)
   - Y-axis position hint = 24h price change direction
   - Hover: freeze animation, show tooltip with ticker, sentiment %, top 3 driving keywords
   - Click: navigate to Financial Alpha View for that ticker
3. **Signal Feed** (right sidebar)
   - Vertical scrolling alert cards
   - Each card: alert dot (color-coded), ticker, insight text, source (e.g., "Reddit r/GPU")
   - Bottom: text input "Real Feed Insights..." for analyst notes
4. **Bottom Row — 4 Chart Cards:**
   - "Macro Sentiment vs. S&P 500" — dual-axis line chart (sentiment left Y, S&P right Y, time X)
   - "Top 5 Sector Sentiment" — horizontal bar chart, bullish-colored bars
   - "Emerging Risks" — horizontal bar chart, bearish-colored bars
   - "Economic Sentiments" — horizontal bar chart, mixed bullish/bearish
5. **Timeframe Selector** (bottom strip)
   - Quick presets: 1D | 5D | 1M | YTD | 1Y | 5Y
   - Dual-handle range slider for custom windows
   - Progressive data loading: only fetch high-res data for visible window

### 2. Financial Alpha View (`/alpha/[ticker]`)
Solves the "Correlation Problem" — layering sentiment onto price action.

**Components:**
1. **Dual-Axis Temporal Plot** (main)
   - Primary Y: Candlestick chart (historical + predicted)
   - Secondary Y: Gradient-filled area chart = aggregate sentiment score
   - "Prediction Ghost": Future prices as dashed line with confidence shaded area (widens over time)
   - Sentiment Annotations: Small interactive pins at sentiment velocity peaks; click → News Insight Overlay
2. **News Insights Panel** (side or overlay)
   - Headlines/tweets that triggered sentiment spikes
   - Timestamped, source-attributed
3. **Ticker Header** — Company name, current price, change %, sentiment score badge

### 3. Product Innovation View (`/innovation`)
The "Gap Finder" — macro market to micro feature analysis.

**Components:**
1. **Channel Slicer** — Segmented control: Amazon | Twitter/X | Reddit | YouTube
2. **Feature Friction Board** — Two high-contrast cards:
   - "Top 5 Criticisms" (The Pain) — horizontal bars, color intensity = emotional anger/frustration
   - "Top 5 Requested Features" (The Opportunity) — horizontal bars, bullish colored
3. **Innovation Gap Map** — Scatter plot: X = "Current Satisfaction", Y = "Importance to User"
   - Top-left quadrant auto-highlighted as "Innovation Zone"
4. **Time & Product Filters** — Slice by time period, product type, brand

## Navigation
- **Left sidebar** (collapsed by default, icon-only): Home (Global Pulse), Financial Alpha, Product Innovation, Settings
- Expand on hover to show labels
- Active view highlighted with accent-blue indicator
- Mobile: bottom tab bar instead of sidebar

## Responsive Behavior
- Desktop (≥1280px): Full bento grid
- Tablet (768–1279px): Stack modules into 2-column grid, signal feed collapses to drawer
- Mobile (<768px): Single column, bottom nav tabs, charts simplified to sparklines

## Data Files
- Place all mock data in `/data/` directory
- Use descriptive filenames: `mockSentiment.json`, `mockSignals.json`, `mockSectors.json`, `mockStockTSLA.json`
- Structure mock data to mirror expected API response shapes

## Code Standards
- TypeScript strict mode
- All components in `/components/` with PascalCase naming
- Views/pages in `/app/` following Next.js App Router conventions
- Extract D3 logic into custom hooks (`/hooks/useForceSimulation.ts`, etc.)
- Keep chart components pure — data transformation happens in hooks or utils
- IMPORTANT: Every chart/viz component must accept data as props (no hardcoded data inside components)
- Tailwind classes only — no inline styles, no CSS modules unless absolutely necessary

## Build Order (Recommended)
1. Project scaffold + layout shell + design tokens in Tailwind config
2. Sidebar nav + header
3. Sentiment Heat-Sphere (D3)
4. Signal Feed sidebar
5. Bottom chart cards (4 modules)
6. Timeframe selector + range slider
7. Financial Alpha View
8. Product Innovation View
9. Responsive polish + mobile
10. Replace mock data with API hooks
