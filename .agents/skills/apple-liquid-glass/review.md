# Review mode — audit an existing UI against the system

Use when the user has existing CSS / a page / a component and asks "is this Apple enough / what can be improved / make it consistent". You are a design reviewer with the design-system as the ruler.

## First: find the real visual surface
Code that *generates content* (markdown, JSON, data) usually has no design of its own — the look lives in the **rendering layer**. Before reviewing, locate where pixels are actually decided:
- A content generator (`gen_*.mjs`, a CMS, an API) → the design lever is its **theme CSS / template / component**, not the generator.
- Say so explicitly. Don't pretend to "design-review" a JSON builder; point at the CSS/template it feeds.

## Process
1. **Inventory.** Read the target file(s) in full. Note every color, font, radius, shadow, border, spacing decision.
2. **Score against `design-system.md`** on each axis: ground/surface, color (accent vs grayscale), type (stack + tracking + line-height), spacing/radius/shadow tiers, glass usage, hierarchy, responsive.
3. **Run `checklist.md` + the anti-slop list** (in `SKILL.md`). Flag each violation with `file:line` and the exact offending value.
4. **Prioritize** findings:
   - **P0** — breaks the aesthetic outright (wrong accent hue everywhere, warm ground, fragmented colored cards, generic font as brand face).
   - **P1** — inconsistency / drift (ad-hoc radius, #333 instead of #1d1d1f, hard divider instead of hairline).
   - **P2** — polish (slightly-off radius tier, spare emoji, minor spacing).
5. **Map each fix to a token** (`tokens.css`) and a component/pattern. Never propose a raw value a token already names.
6. **Implement toward `reference.html`**, then **re-render and compare** (screenshot the result; it must read like the same family). Fix until it does.

## Output format
Lead with one honest sentence on where the design lever actually is. Then a prioritized table:

| Pri | Finding (`file:line`) | Now | → Apple |
|---|---|---|---|
| P0 | accent everywhere `rgba(255,125,77)` | warm orange | Apple blue `#0071e3`; grayscale body |
| P0 | `h2 border-left:4px orange` | colored left-bar (fragmentation) | drop bar; hierarchy via weight/size |
| P1 | text `#333` | generic grey | `#1d1d1f` / `#6e6e73` |
| P1 | `hr 2px rgba(0,0,0,.1)` | hard rule | hairline `1px rgba(0,0,0,.07)` |

End with the single highest-leverage change (usually: re-theme the one CSS file that everything renders through).

## Worked example (real)
**Target:** a WeChat-article generator's theme CSS (doocs "default"), reached via two `gen_*.mjs` content scripts.
- **Lever:** the scripts emit markdown/JSON; the look is entirely in `theme.css`. Reviewed the CSS, not the scripts.
- **P0 found:** primary accent was warm orange `rgba(255,125,77)` on links / strong / inline-code / h3; `h2`/`blockquote` had orange colored left-bars + orange tint backgrounds; body text `#333`. Every one is an anti-slop hit (color-as-decoration, colored left-bar fragmentation, generic grey, warm accent).
- **Fix:** accent → Apple blue `#0071e3`; grayscale body `#1d1d1f`/`#6e6e73`; dropped the colored left-bars (heading hierarchy now from weight+size); blockquote → light-grey panel + faint hairline rule; `hr` → hairline; inline code → grey chip; heat-orange reserved only for genuine "hottest" emphasis; titles negative-tracked; removed a 3-line emoji CTA pile per the restraint rule.
- **Result:** the article body now reads as the same Apple family as its (already-Apple) cover image. Verified by re-render.

The lesson: the most valuable review output is often *"these N files all render through ONE theme that's off-system — re-theme that, and everything downstream becomes consistent."*
