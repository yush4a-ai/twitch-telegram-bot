# Patterns — page-level recipes & decision trees

Components (`components.md`) are the bricks; this is how you lay them into a page. Pick the container, the archetype, then run the decision trees for the calls that vary.

---

## Container choice
- **Reading column `max-width: 720px`** — anything meant to be read top-to-bottom: article, detail page, form/settings, a single Q&A.
- **Grid/dense column `max-width: 1080px`** — anything scanned: home, index/archive, topic grids, dashboards.
- Centered, side padding `22px`, section gap `clamp(34px,6vw,56px)`.

## Page archetypes

### A. Detail / reading page (article, answer)
```
[glass nav · back pill]
[hero: eyebrow · H1 (negative tracking) · lede]
[body: flat prose — NO card wrapper; long body line-height ≥1.85]
[meta/source row: hairline above; byline · date · "read original" · share]
[back-to-top / related — restrained]
```
Key: the body text and any hero image are **one continuous reading surface** — do not box the prose in a card. Source/meta sits under a hairline, not in a tinted box.

### B. Index / list page (archive, topics, tips)
```
[glass nav]
[hero: section title · one-line desc]
[unified panel list, grouped → group label + one panel of hairline-separated rows]
[ "view all →" link, quiet]
```
Key: **unified panel + hairlines**, grouped by date/cluster. Never a wall of separately-bordered cards.

### C. Home / dashboard
```
[glass nav · date · Ask-AI pill]
[hero: live eyebrow · H1 · stat line (tabular-nums)]
[cover/focus block (optional)]
[primary feed: unified list — with a segmented control to switch unified / grid / focus]
[secondary section(s): cards or panel]
[colored CTA block (glass + orbs)]
[subscribe / footer]
```
Key: the primary feed defaults to the **unified list** (solves fragmentation); offer grid/focus as a segmented toggle, don't show all three at once.

### D. Form / settings
```
[grouped panels — each group = one white panel of setting rows]
[row = label left, control right; hairline between rows; section label above each panel]
```
Key: same panel-not-cards rule; controls right-aligned; generous row height (≥44px touch).

## Decision trees

**Unified panel vs card grid**
- Same-kind sibling rows, scannable, mostly text → **unified panel + hairlines**.
- Genuinely independent objects, each with its own image/thumb, meant to be picked → **card grid** (`auto-fill, minmax(290px,1fr)`, gap 14px, hover lift).
- When unsure → unified panel. Fragmentation is the bigger sin.

**Glass vs solid**
- Do layers overlap here? (sticky nav over scroll, modal over page, label over a colored CTA) → **glass**.
- Flat content on the page ground? → **solid `#fff` + soft shadow**. Never glass.

**Color vs grayscale**
- Is this an *accent* (primary action/link), *heat* (hottest), *brand/platform*, or *live*? → use that one token.
- Otherwise → grayscale (weight + size carry the hierarchy).
- Two accent colors competing in one view = wrong. One per view.

**Add an element vs remove**
- Does it carry meaning a reader needs? Keep. Does it exist to look "rich/techy/designed"? Remove. (Extra icons, stat padding, decorative emoji, borders that a hairline or whitespace already implies.)

## Section rhythm
- Hero enters fast (eyebrow → title → one lede line), no long preamble.
- Body breathes; sections separated by `clamp(34px,6vw,56px)`, not even tiny gaps.
- Panels are as **short or tall as their content** — don't pad everything to equal height.
- Numbers in `tabular-nums`; titles negative-tracked; dividers are hairlines.

## Responsive recipe
- Build once with `clamp()`; never two designs.
- ~`680px`: two-column → one; hide secondary info (`.hide-sm`); pick the denser default (e.g. focus flow on mobile home); touch targets ≥44px.
