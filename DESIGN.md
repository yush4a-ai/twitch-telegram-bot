# TwitchSignalBot — web design system

<!-- impeccable:design-schema 1 -->

## Direction

Операционная консоль владельца: плотная, спокойная, читаемая. Визуальная связь с существующими `bot_avatar.png` и `bot_welcome_banner.png`: глубокий navy, холодный фиолетовый и точечный красный сигнал эфира. Числа и состояния важнее декора. Первый экран отвечает на вопросы «бот работает?», «что происходит сейчас?» и «где очередь или отказ?».

## Tokens

| Роль | Dark | Light |
| --- | --- | --- |
| canvas | `#0B1020` | `#F3F3FA` |
| surface | `#151C31` | `#FFFFFF` |
| surface raised | `#1D2540` | `#E9EAF5` |
| text primary | `#F4F5FF` | `#171B31` |
| text secondary | `#BFC6DF` | `#4B536C` |
| border | `#39415C` | `#D4D8E8` |
| accent | `#AD86FF` | `#6A39C5` |
| success | `#82DCC2` | `#146F59` |
| warning | `#F1C779` | `#8B5700` |
| danger / live | `#FF727D` | `#AF2039` |

Semantic tokens `--color-canvas`, `--color-surface`, `--color-raised`, `--color-text`, `--color-muted`, `--color-border`, `--color-accent`, `--color-good`, `--color-warn`, `--color-danger`. Status always uses text plus color; no color-only meaning. Focus ring accent, 2 px with offset. Spacing scale 4/8/12/16/24/32/48 px. Radius 12–16 px for grouped surfaces; small controls may be pills. No decorative gradients or glass.

## Typography

System UI for body to keep Cyrillic and Latin legible without a CDN. Fixed-width system stack only for timestamps, latency and queue counts. Page title 32–40 px; section labels 20–24 px; body 14–16 px with line-height at least 1.45. Tabular lining numerals for metrics; longest Russian labels wrap without clipping. No faux illustration or glyph icons.

## Layout and components

- Header: product, `STAGING` marker, refreshed time, refresh/logout controls.
- Health section: three unequal content columns on desktop, stacked on phone. Explicit status, explanation, and 2–4 useful values per subsystem.
- Audience: compact aligned metrics, without interchangeable promotional cards.
- Live: table with channel, placements, viewers and observed time; at narrow widths each row becomes a labeled vertical item.
- Operations: queues, errors, resources in clear grouped rows. Only source-backed values.
- Login: plain protected entry, field label, error near form, no operational data until authenticated.
- Empty/loading/error/stale states occupy the same semantic regions as loaded content. Refresh remains usable after errors. Buttons have hover/focus/active/disabled feedback; reduced motion removes transitions.

## Responsive and accessibility

Test at 360, 390, 768 and 1440 px. Max content width 1440 px, fluid gutters 16–32 px. Avoid document-level horizontal overflow. Touch controls at least 44 px tall. Visible keyboard focus; semantic headings, form labels, table headers, live region for refresh status. Dark and light via `prefers-color-scheme`, with contrast checked against actual rendered colors. Native Telegram screens are separate R7 work.

## Evidence and acceptance

Agent-selected initial direction from incumbent brand assets and R2 brief; owner taste acceptance pending. Visual baselines must come from real browser screenshots after functional tests. No fabricated audience, revenue or delivery signals.
