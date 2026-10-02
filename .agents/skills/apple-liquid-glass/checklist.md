# Checklist — the gate before "done"

Run this on any page/component before claiming it's finished. Every box must be checked or consciously waived. Pair it with a visual compare against `reference.html`.

## Surface & layout
- [ ] Page ground is `#f5f5f7` (COOL — not warm cream/beige/off-white).
- [ ] Content sits on `#fff` surfaces; container centered (`max-width` 720 reading / 1080 grid), side padding 22px.
- [ ] Sibling same-kind items use **one panel + hairline dividers** (`rgba(0,0,0,0.07)`), not separate bordered/tinted cards.
- [ ] Layout is flex/grid + `gap` — not inline elements + margin.
- [ ] Sections separated by `clamp(34px,6vw,56px)`; panels sized to content, not all equal-padded.

## Glass
- [ ] `backdrop-filter` appears **only** on sticky nav / overlay / colored CTA.
- [ ] No glass on plain content cards, list rows, or between white blocks (those are solid white + soft shadow).
- [ ] Glass-on-color has blurred light orbs behind it (something to refract).

## Type
- [ ] System font stack (SF / PingFang) — no Inter/Roboto/Arial as the brand face.
- [ ] Big titles have **negative letter-spacing** (bigger → more negative).
- [ ] Long body line-height ≥ 1.85.
- [ ] Numbers use `tabular-nums`.
- [ ] CJK↔Latin have a half-width space between them.

## Color
- [ ] Body world is grayscale; color appears only as accent (one blue) / heat (one orange) / brand / live.
- [ ] No tinted backgrounds, colored left-bars, multi-color, or gradient washes on plain content.
- [ ] At most one accent color competing in a single view.

## Radius & shadow
- [ ] Radius taken from named tiers (pill 999 / thumb 12 / card 18 / panel 22 / hero 26) — no ad-hoc values.
- [ ] Shadows are two-layer (tight contact + soft spread); no single hard drop-shadow; no hard black borders.

## Motion & a11y
- [ ] Hover = gentle lift (`translateY(-2~-3px)`) or row tint, 0.15–0.25s.
- [ ] No opacity-fade entrance keyframes on async-rendered content.
- [ ] Touch targets ≥ 44px; mobile collapses to one column, secondary info hidden.

## Restraint
- [ ] Removed avoidable noise: extra icons, stat padding, decorative emoji, "in today's world" filler.
- [ ] Every element carries meaning; nothing is there just to look "rich / techy / designed".

## Final
- [ ] Side-by-side, it looks like it belongs on the same page as `reference.html`.

## Motion (only if the UI has interactive layers — full list in motion.md)
- [ ] Press feedback on pointer-down; enter/exit same path, origin anchored to trigger.
- [ ] `--ease-spring`/`--ease-out-quart` (no `linear`/default `ease`); no overshoot without momentum.
- [ ] Glass materializes (blur+scale+opacity together); only `transform`/`opacity` animated.
- [ ] `prefers-reduced-motion` / `prefers-reduced-transparency` / `prefers-contrast` all handled.

## Interaction foundations
- [ ] Every screen answers: where am I / where can I go / what's there / how out.
- [ ] Controls sit next to what they affect; labels are specific, not generic.
- [ ] Inline validation; undo over confirmation dialogs (confirm only irreversible).
- [ ] Sticky chrome uses scroll-edge (hairline appears only when content scrolls beneath), not a permanent border.
