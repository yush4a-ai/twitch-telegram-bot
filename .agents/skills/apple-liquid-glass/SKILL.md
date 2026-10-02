---
name: apple-liquid-glass
description: "Build Apple-grade web UI — the \"macOS Liquid Glass\" aesthetic. Use when designing or building any page, component, or interface that should feel like the latest Apple (apple.com / Apple Newsroom / macOS): light grey-white ground, unified white surfaces with hairline dividers, restrained typography with negative tracking, frosted glass used only where layers overlap, and color reserved for accent. Covers fluid motion for interactive layers (springs, interruptibility, reduced-motion). Portable across projects (any stack) — ships tokens, copy-paste components, line icons, a reference page, and a self-check. Trigger on \"Apple style\", \"liquid glass\", \"make it look like apple.com\", \"clean Apple UI\", \"苹果风\", \"液态玻璃\"."
---

# Apple Liquid Glass — Design Skill

You are designing in the **latest Apple visual language**: calm, premium, restrained. The goal is that a user feels "this looks like Apple" within 1.5 seconds — not through gimmicks, but through the *sum of small correct decisions*.

> One sentence: **Apple light grey-white ground + unified white surfaces + hairline dividers; glass only where layers truly overlap. Restraint is the luxury.**

## When to use
- Building any web page / component / app screen meant to feel Apple-grade (App / iOS screens: also read `app.md` for the device frame + mobile shell).
- Redesigning a UI that feels fragmented, "carded", or generic.
- The user mentions Apple, apple.com, macOS, liquid glass, 苹果风, 液态玻璃, or "clean / premium / minimal".

## The 5 philosophy rules (decide by these, in order)

1. **Unified surface > fragmented cards.** Many sibling items go on **one white panel with hairline dividers**, not a pile of separately-bordered, separately-tinted cards. Fragmentation is enemy #1.
2. **Glass is seasoning, not the dish.** `backdrop-filter` frost only where **layers actually overlap**: sticky nav, modals/popovers, colored CTA blocks. Plain content areas get solid `#fff` + soft shadow — never glass. (Web has few overlapping layers; over-glassing reads as dirty.)
3. **Restraint is luxury.** A thousand "no"s for one "yes". If whitespace and hierarchy can solve it, don't add a border, fill, icon, or number. Reject data-slop.
4. **Hierarchy from weight + size + grayscale, not color.** The body world is black-white-grey. Color is only for *accent* (one blue), *heat* (one orange), *brand/live*. Never color to fill space.
5. **Apple quality lives in details.** Negative letter-spacing on big titles, `tabular-nums` numbers, hairline dividers, gentle hover lift, 180%-saturation glass — the *total* of these small things is what reads as "Apple".

## Interaction foundations (reason with these names, beyond the visual)

- **Wayfinding — every screen answers four questions:** Where am I? Where can I go? What's there? How do I get out? Never trap the user.
- **Feedback comes in four kinds:** status, completion, warning, error. Validate inline, not on submit; expose ongoing status.
- **Grouping & mapping.** Place a control next to what it affects; arrange controls to mirror what they change. If a control needs a label to be understood, the mapping is weak.
- **Agency & forgiveness.** Easy undo beats confirmation dialogs; reserve confirmations for genuinely destructive, irreversible actions (overuse trains click-through).
- **Direct, specific labels beat safe generic ones.** "Progress" / "Library", not "Home". Specificity creates predictability.

## Two modes

- **Build** — making new UI. Follow the workflow below.
- **Review** — auditing existing CSS / a page / a component for "is this Apple / make it consistent". Use `review.md` (find the real visual surface → score against the system → prioritized findings → fix toward the tokens). The biggest wins are usually one off-system theme file that everything renders through.

## Workflow — Build mode (follow in order)

1. **Read `design-system.md`** — the full spec (color, type, spacing, radius, shadow, glass recipe, components, responsive).
2. **Drop in `tokens.css`** — the `:root` custom properties. Reference everything via `var(--…)`; never hard-code raw hex/px that a token already names.
3. **Pick a page recipe from `patterns.md`** — container (720 read / 1080 grid), archetype (detail / index / home / form), and run its decision trees (panel vs cards, glass vs solid, color vs grayscale).
4. **Compose from `components.md`** — copy the proven snippets (nav, unified panel list, card/grid, segmented pill, tag, button, colored CTA, live dot). Adapt content, keep structure + tokens.
5. **If it's an App / iOS screen** (mobile mockup, app 原型, anything inside a phone) — **read `app.md`** for the mobile shell the core files don't ship: device frame (exact iPhone spec — never hand-roll the island/status bar/home indicator), large-title-collapse nav, tab bar, edge-anchored bottom sheet, safe areas, touch targets. The *content* still comes from `components.md`; `app.md` is only the shell around it, and the same rules hold (unified panels, glass-on-overlap, grayscale + one accent, no color-by-status on list values). **Needs icons** (tab bar, object-type rows, actions)? Read `icons.md` — the line-icon layer (curated Lucide inline set, one 24-grid stroke, grayscale `currentColor`, accent only on action/active, and the restraint rules so icons earn their place — no grey squares, no emoji).
6. **If the UI has interactive layers** (dialog / sheet / popover / drawer / anything summoned & dismissed) — **read `motion.md`** and apply the fluid-motion treatment: pointer-down feedback, spring-like easing tokens, same-path enter/exit, materialize (blur+scale+opacity together), interruptibility, and the three reduced-* media queries. Static content keeps the tiny motion budget.
7. **Check against `reference.html`** — open it; your output must look like it belongs on the same page. If a screenshot diverges, fix toward the reference.
8. **Run the gate** — `checklist.md` (or the condensed self-check below) before claiming done; interactive layers also pass the motion self-check at the end of `motion.md`; App screens pass the App self-check at the end of `app.md`.

> Portable: these files assume **no framework**. In React/Vue/etc., translate the CSS to your styling system but keep the *exact* token values, the panel-not-cards pattern, and the glass-only-on-overlap rule. Match the visual output; don't copy structure that doesn't fit.

## Anti-slop — what makes it NOT Apple (avoid every one)

- ❌ **Fragmented cards**: each item with its own border + tint + colored left-bar. → one panel + hairlines.
- ❌ **Glass everywhere**: blurring plain content blocks. → glass only on nav/overlay/CTA.
- ❌ **Color as decoration**: tinted backgrounds, multi-color, rainbow gradients washing the page. → grayscale body, one accent.
- ❌ **Generic fonts** (Inter/Roboto/Arial as the brand face). → system SF / PingFang stack, negative tracking on titles.
- ❌ **Warm cream / beige / kraft "paper"** ground — the over-used AI look. → cool `#f5f5f7`.
- ❌ **Purple-gradient-on-white** hero, hard black borders, heavy single drop-shadows, emoji confetti, "in today's fast-paced world" filler.
- ❌ **Even, timid spacing**: everything equally padded. → intentional whitespace, sections breathe (`clamp(34px,6vw,56px)`), panels short or tall as needed.
- ❌ Numbers/stats added to look "data-rich". → only numbers that carry meaning, in `tabular-nums`.

## Self-check (gate before "done")
- [ ] Ground `#f5f5f7` (cool), content on `#fff` surfaces, container centered (`max-width` 720 reading / 1080 grid).
- [ ] Sibling items use **one panel + hairline** (`rgba(0,0,0,0.07)`), not fragmented cards.
- [ ] Glass appears **only** on nav / overlay / CTA. Plain blocks are solid white + soft shadow.
- [ ] Big titles have **negative letter-spacing**; long body line-height ≥ 1.85; numbers `tabular-nums`.
- [ ] Radius + shadow taken from the **named token tiers**, not ad-hoc values.
- [ ] Accent color is blue / heat-orange / brand only — everything else grayscale.
- [ ] Layout uses flex/grid + `gap` (not inline + margin); touch targets ≥ 44px; `clamp()` for responsive; mobile collapses to one column, secondary info hidden.
- [ ] Removed avoidable noise (extra icons, stats, decoration, emoji).
- [ ] Visually matches `reference.html`.

## Files in this skill
- `design-system.md` — full spec + token table + decision guidance.
- `tokens.css` — `:root` custom properties (portable; drop into any project).
- `components.md` — copy-paste HTML+CSS for every core component.
- `patterns.md` — page-level recipes (containers, archetypes) + decision trees.
- `checklist.md` — the standalone "done" gate.
- `motion.md` — fluid interaction layer: springs/easing, interruptibility, same-path + anchored origin, materialize, gesture physics, reduced-motion (adapted from emilkowalski/skills apple-design, MIT).
- `app.md` — App / iOS shell layer: device frame (exact iPhone spec), status bar, large-title-collapse nav, tab bar, edge-anchored bottom sheet, safe areas, mobile-first rules.
- `icons.md` — line-icon layer: Lucide-based inline set (24-grid, one stroke), grayscale `currentColor` with accent only on action/active, size table, restraint rules.
- `review.md` — Review-mode workflow for auditing existing UI.
- `reference.html` — rendered "living style guide" (open it; your ground truth).
