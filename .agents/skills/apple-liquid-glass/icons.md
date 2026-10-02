# Icons — the line-icon layer

The core skill ships tokens, panels, glass, motion, and the App shell — but **no icons**. Left unfilled, agents reach for grey filled squares (placeholder slop) or emoji (never Apple). This file adds a small, curated **line-icon** set so an icon is a real icon, and teaches when *not* to use one.

> Read this when a screen needs icons: tab bars, object-list rows, nav/button actions. The App shell (`app.md`) points here for its tab bar.

## The 4 rules

1. **`currentColor`, grayscale by default — accent only for action/active.** Icons inherit text color; they sit at `--text-3` grey like body chrome.染 accent **只**在图标本身即操作或激活态时（激活的 tab、主操作按钮）。绝不彩色图标堆叠 — that's the rainbow-icon slop the core skill already forbids for color.
2. **An icon must earn its place.** Not every row / title / stat / bullet gets one — decorative icons *are* slop (SKILL.md anti-slop #5). A pure-text settings list is **cleaner without** leading icons. Add an icon only when it aids scanning (object-type rows) or names an action (nav/button).
3. **One stroke, one grid, one viewBox.** All icons: `viewBox="0 0 24 24"`, `stroke-width:1.75`, round caps/joins. The **only** allowed second weight is *line (default) → filled (active)* for the current iOS tab item. Never mix line and fill anywhere else, never mix stroke widths.
4. **Icon size ≠ touch target.** A 22px icon still needs a **≥44px hit box** — pad the control or make the whole row the target. (Same rule as `app.md` §5.)

## Size table

| Context | px | Treatment |
|---|---|---|
| Tab bar | 26 | line → **filled + accent** when active |
| Nav / toolbar action | 22 | line, grey (accent if it's the primary action) |
| List-row leading | 20–22 | line, grey |
| Inline / meta | 15–16 | line, grey |
| Button leading | 17–18 | line, matches button label color |

## Base recipe

```html
<svg class="ic" viewBox="0 0 24 24" width="22" height="22" fill="none"
     stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">
  <!-- paste path(s) from the core set below -->
</svg>
```
```css
.ic{ color:var(--text-3); flex:none; }   /* grey by default — inherits currentColor */
/* override ONLY on action/active: */
.ic.on, a:active .ic, .btn-primary .ic{ color:var(--accent); }
```
Size per context by setting `width`/`height` (keep the `24` viewBox fixed — that's how one grid holds).

## Core set (24-grid — paste the inner path(s) into the recipe)

```
chevron-right   <path d="M9 6l6 6-6 6"/>
chevron-down    <path d="M6 9l6 6 6-6"/>
arrow-left      <path d="M19 12H5"/><path d="M12 19l-7-7 7-7"/>
plus            <path d="M12 5v14"/><path d="M5 12h14"/>
x               <path d="M18 6 6 18"/><path d="M6 6l12 12"/>
check           <path d="M5 12l5 5L20 6"/>
search          <circle cx="11" cy="11" r="7"/><path d="M21 21l-4.35-4.35"/>
bell            <path d="M6 9a6 6 0 0 1 12 0c0 6 2.5 8 2.5 8h-17S6 15 6 9"/><path d="M10.3 21a1.9 1.9 0 0 0 3.4 0"/>
user            <circle cx="12" cy="8" r="4"/><path d="M4 20a8 8 0 0 1 16 0"/>
home            <path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V21h14V9.5"/>
credit-card     <rect x="2.5" y="5" width="19" height="14" rx="2.5"/><path d="M2.5 10h19"/>
wallet          <rect x="3" y="6" width="18" height="13" rx="2.5"/><path d="M3 10.5h18"/><circle cx="17" cy="14.5" r="1.25"/>
settings        <circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1"/>
share (iOS)     <path d="M12 3v12"/><path d="M8 7l4-4 4 4"/><path d="M6 12v6a1.5 1.5 0 0 0 1.5 1.5h9A1.5 1.5 0 0 0 18 18v-6"/>
trash           <path d="M4 7h16"/><path d="M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/><path d="M6 7l1 13a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-13"/>
```

Need more? These are Lucide (24-grid, `stroke-width:1.75` matches) — pull any other glyph from **lucide.dev** into the same recipe. On a real Apple platform, prefer **SF Symbols** (not web-distributable — don't inline SF Symbol assets on the web).

## Active / selected — the one allowed second weight

```css
.tab .ic{ color:var(--text-3); }         /* inactive: line, grey */
.tab.on .ic{ color:var(--accent); }      /* active: accent (ideally the filled variant) */
```
Line+grey for inactive tabs, accent for the one active tab. The *ideal* active state also swaps the line glyph for a filled one (iOS tab bar behavior) — a single accent color is the acceptable floor.

## Where icons belong

✅ **Use:**
- Tab bar (every tab).
- **Object-type list rows** — payment / file / person / card: leading icon + trailing `chevron-right` for drill-down.
- **Nav / button actions** — one per side (`+`, `arrow-left`, `search`), named by the glyph.

❌ **Don't:**
- Pure-text settings lists (cleaner without leading icons).
- Every title / stat / bullet (decoration = slop).
- Emoji as icons (never Apple).

## Self-check
- [ ] One stroke width + one 24-grid viewBox everywhere; no line/fill mix except the active tab.
- [ ] Icons are `currentColor` grey by default; accent appears **only** on action/active elements.
- [ ] No "an icon on every row" decoration; icons earn their place.
- [ ] Every icon control has a ≥44px hit box (icon px ≠ touch target).
- [ ] Real line icons — not grey filled squares, not emoji.

Credit: icon paths from **Lucide** (ISC License, © Lucide Contributors, lucide.dev).
