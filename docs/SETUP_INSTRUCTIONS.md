# How to integrate these documents into your osint-pipeline repo

## Step 1: Create the docs directory
```
cd C:\Users\micha\Projects\osint-pipeline
mkdir docs
```

## Step 2: Copy the strategic docs into docs/
```
copy BEHAVIORAL_DESIGN_SPEC.md docs\BEHAVIORAL_DESIGN_SPEC.md
copy PRODUCT_STRATEGY.md docs\PRODUCT_STRATEGY.md
copy README.md docs\README.md
```

## Step 3: Add the design principles to CLAUDE.md

Open CLAUDE.md in your editor. Find the line that says:

```
## Development Commands
```

Paste the ENTIRE contents of CLAUDE_MD_ADDITION.md ABOVE that line
(right after the "Current state:" paragraph in Project Overview).

This adds ~40 lines of enforceable design rules that Claude Code checks
against every time it writes user-facing code.

## Step 4: Commit
```
git add docs/ CLAUDE.md
git commit -m "Add behavioral design spec, product strategy, and UX design principles to CLAUDE.md"
git push
```

## Step 5: Using in Claude Code sessions

For MOST coding sessions, Claude Code automatically reads CLAUDE.md and
gets the design principles. That's enough for routine feature work.

For UX-HEAVY sessions (building the morning briefing, prediction streaks,
notifications, Explain panel), start the session with:

```
Read docs/BEHAVIORAL_DESIGN_SPEC.md first. Then build [feature].
```

For PLANNING sessions (deciding what to build next, scoping new features):

```
Read docs/PRODUCT_STRATEGY.md for context. Then help me scope [feature].
```

## Why this three-tier structure works

1. CLAUDE.md (auto-loaded, ~40 lines added): Contains only the enforceable
   rules. Claude Code checks these constraints while writing code without
   burning context window on strategic prose.

2. docs/BEHAVIORAL_DESIGN_SPEC.md (loaded on demand): Full engagement
   architecture with psychological rationale, engagement loops, anti-patterns,
   notification tiers, and daily user journey. Referenced when building
   user-facing features.

3. docs/PRODUCT_STRATEGY.md (loaded on demand): Competitive analysis,
   roadmap phases, monetization tiers, revenue projections. Referenced
   when scoping new features or making prioritization decisions.

The key insight: Claude Code's context window is limited. Stuffing 10K words
of strategy into CLAUDE.md means less room for actual code context. The
three-tier approach gives Claude Code the rules it needs automatically, and
the full rationale on demand.
