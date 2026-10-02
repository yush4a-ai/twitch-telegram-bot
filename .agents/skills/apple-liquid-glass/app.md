# App / iOS screens — the mobile shell layer

The core skill is desktop-web scale (720/1080 containers, hover lift). An **App screen** is different: it lives inside a device, is touch-first, and needs iOS chrome the web system doesn't ship — device frame, status bar, large-title nav, tab bar, bottom sheet, safe areas. This file adds that shell. **Everything else stays the same**: tokens, unified-panel-not-cards, glass-only-on-overlap, grayscale + one accent, tabular-nums, materialize motion.

> Read this when the task is "an app / iOS screen / mobile mockup / app 原型." Use the core files (`design-system.md`, `components.md`) for the *content*; use this file for the *shell around it*.

## Iron rule — never hand-roll the device frame

Eyeballing bezel / island / status-bar / home-indicator pixels **will** drift (island off-center, time colliding with the notch, content under the island). Use the exact spec below verbatim. Values are iPhone 15 Pro / 16 / 15 logical points (the Dynamic Island generation).

```
screen (points)      393 × 852
device corner radius 55        bezel (padding) 12
Dynamic Island       125 × 37  ·  top 11  ·  centered  ·  radius 999
safe-area top (status bar)   59      safe-area bottom 34
home indicator       139 × 5   ·  centered  ·  bottom 8
nav bar compact      44        large-title expanded area ~96 (44 + 52)
tab bar              49 content + 34 safe = 83
```

Mobile-first spacing uses safe areas, not magic numbers: `padding-bottom: max(16px, env(safe-area-inset-bottom))`.

## 1. Device frame (bezel + island + status bar + home indicator)

```html
<div class="ios">
  <div class="ios-screen">
    <div class="ios-island"></div>
    <div class="ios-status">
      <span class="t">9:41</span>
      <span class="i"><!-- signal / wifi / battery: SF Symbols in-app; simple bars ok in a mock --></span>
    </div>
    <!-- app content: nav + scroll + tab bar -->
    <div class="ios-home"></div>
  </div>
</div>
```
```css
.ios{ position:relative; width:393px; height:852px; background:#000;
  border-radius:55px; padding:12px; box-shadow:0 50px 100px rgba(0,0,0,.5); }
.ios-screen{ position:relative; width:100%; height:100%; background:var(--bg);
  border-radius:44px; overflow:hidden; display:flex; flex-direction:column; }
.ios-island{ position:absolute; top:11px; left:50%; transform:translateX(-50%);
  width:125px; height:37px; background:#000; border-radius:999px; z-index:60; }
.ios-status{ flex:none; height:59px; display:flex; align-items:center; justify-content:space-between;
  padding:0 32px; font-size:15px; font-weight:600; color:var(--text); }
.ios-status .i{ display:flex; align-items:center; gap:5px; font-size:13px; }
.ios-home{ position:absolute; bottom:8px; left:50%; transform:translateX(-50%);
  width:139px; height:5px; border-radius:999px; background:rgba(0,0,0,.85); z-index:80; }
```
- On a **dark** screen, status text and home bar go white (`#fff` / `rgba(255,255,255,.9)`).
- The home indicator is `z-index:80` — **above** sheets and tab bar, so it never disappears under an overlay (a common hand-roll bug).
- The status-bar right cluster in a real app is SF Symbols; in a static mock, simple bars/glyphs read fine — don't over-invest.

## 2. Large-title nav (collapses on scroll — the signature iOS move)

Large title at rest; on scroll it shrinks to a compact centered bar with a scroll-edge hairline. This *is* iOS navigation — a static 34px title reads as "web page in a phone."

```html
<header class="nav-c" id="navc"><span class="nav-c-t">钱包</span></header>
<div class="scroll" id="scroll">
  <h1 class="lt">钱包</h1>
  <!-- content -->
</div>
```
```css
.nav-c{ position:absolute; top:59px; left:0; right:0; height:44px; z-index:50;
  display:flex; align-items:center; justify-content:center;
  background:rgba(245,245,247,.72); backdrop-filter:saturate(180%) blur(20px); -webkit-backdrop-filter:saturate(180%) blur(20px);
  border-bottom:1px solid transparent; opacity:0; transition:opacity .25s, border-color .25s; pointer-events:none; }
.nav-c.show{ opacity:1; border-bottom-color:var(--hairline); }
.nav-c-t{ font-size:16px; font-weight:600; letter-spacing:-0.01em; }
.lt{ padding:6px 20px 10px; font-size:34px; font-weight:700; letter-spacing:-0.02em; }
```
```js
const sc=document.getElementById('scroll'), nc=document.getElementById('navc');
sc.addEventListener('scroll',()=>nc.classList.toggle('show', sc.scrollTop>44),{passive:true});
```
Compact bar 44 + status 59 = content starts at 103 when pinned. Left = back chevron / title; right = one action (`+`, Edit). One action max — restraint holds on mobile too.

## 3. Tab bar (glass, safe-area, one accent)

Real line icons — **read `icons.md`** for the set + rules (grayscale `currentColor`, accent only on the active tab, one 24-grid stroke). Base recipe inlined below; swap the four glyphs to fit the app.

```html
<nav class="tabbar">
  <a class="tab on">
    <svg class="ic" viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="6" width="18" height="13" rx="2.5"/><path d="M3 10.5h18"/><circle cx="17" cy="14.5" r="1.25"/></svg>钱包</a>
  <a class="tab">
    <svg class="ic" viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><rect x="2.5" y="5" width="19" height="14" rx="2.5"/><path d="M2.5 10h19"/></svg>卡片</a>
  <a class="tab">
    <svg class="ic" viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14"/><path d="M5 12h14"/></svg>添加</a>
  <a class="tab">
    <svg class="ic" viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="4"/><path d="M4 20a8 8 0 0 1 16 0"/></svg>我的</a>
</nav>
```
```css
.tabbar{ flex:none; height:83px; padding:8px 0 34px; display:flex; justify-content:space-around; align-items:flex-start;
  background:rgba(245,245,247,.82); backdrop-filter:saturate(180%) blur(20px); -webkit-backdrop-filter:saturate(180%) blur(20px);
  border-top:1px solid var(--hairline); }
.tab{ display:flex; flex-direction:column; align-items:center; gap:3px; width:64px;
  font-size:10px; font-weight:500; color:var(--text-3); text-decoration:none; }
.tab.on{ color:var(--accent); }              /* active = the one accent */
.tab .ic{ color:currentColor; }              /* icon inherits the tab color: grey inactive, accent active */
```
- Exactly **one** accent tab (the active one); the rest are `--text-3` grey. Never color multiple tabs. Because `.ic` uses `stroke:currentColor`, the label color drives the icon — no separate icon-color rule.
- Real Lucide line icons (see `icons.md`); the active tab's ideal is a **filled** variant, single accent color is the acceptable floor. Never leave grey filled squares — that's placeholder slop.
- `padding-bottom:34px` is the home-indicator safe area — or `max(8px, env(safe-area-inset-bottom))` on device.

## 4. Bottom sheet (edge-anchored — distinct from the centered modal)

The core `motion.md` modal materializes at screen center. An **iOS sheet rises from the bottom edge**, has a grab handle, rounds only its top, and keeps the home indicator visible above it.

```css
.sheet{ position:absolute; left:0; right:0; bottom:0; z-index:71; background:var(--surface);
  border-radius:22px 22px 0 0; padding:10px 20px max(24px, env(safe-area-inset-bottom));
  box-shadow:0 -8px 40px rgba(0,0,0,.16);
  transform:translateY(100%); transition:transform .4s var(--ease-spring); }
.sheet.open{ transform:translateY(0); }
.sheet .grab{ width:38px; height:5px; border-radius:999px; background:var(--faint); margin:0 auto 16px; }
.scrim{ position:absolute; inset:0; z-index:70; background:rgba(0,0,0,.28);
  opacity:0; pointer-events:none; transition:opacity .25s ease; }
.scrim.open{ opacity:1; pointer-events:auto; }
```
- Enter/exit **same path** (from bottom, dismiss to bottom) — inherits `--ease-spring`, interruptible (transition, not keyframe).
- Grab handle `--faint`; top corners `--r-panel` (22), bottom square (glued to the edge).
- Home indicator stays `z-index:80` above the sheet. Detents (half / full) are the iOS norm — a resting half-sheet is often better than full.
- `prefers-reduced-motion`: cross-fade, no travel (same three media queries as `motion.md`).

## 5. Mobile-first rules (override the desktop defaults)

- **Container = the screen**, not 720/1080. Content padding 16px; list rows full-bleed inside their panel.
- **Touch ≥ 44px** hit area for every control (a 26px toggle still needs a 44px box — pad it or make the row the target).
- **No `:hover` as the only affordance** — touch has no hover. Use `:active` (`transform:scale(.98)`, pointer-down) for feedback; keep hover as progressive enhancement.
- **Safe areas, not constants**: `env(safe-area-inset-top/bottom)`; the frame above encodes 59/34 for the mock.
- Type holds the same scale, but large title 34px (iOS standard), nav-compact 16-17px, list row title 15-16px, tab label 10px.

## 6. Restraint still rules (the App traps)

- **The colored moment is ONE element** (a balance / featured card as the glass-gradient hero). Everything else grayscale on white panels — same as the CTA rule on web.
- **Don't color list values by status.** Income green / expense red is the "color-as-status" trap — iOS Wallet keeps amounts grayscale (the sign carries meaning). Reserve color for the one accent. (A single `--live` dot for genuine live state is still fine.)
- Transactions / settings / any sibling list → **one unified panel + hairlines**, never per-row cards. The web rule is identical; mobile just makes fragmentation tempting because rows have icons.

## App self-check (add to the core checklist)
- [ ] Device frame from the exact spec (island centered, status/home not eyeballed); home indicator above overlays.
- [ ] Large title collapses to a compact glass bar on scroll (not a static title).
- [ ] Tab bar: glass, safe-area bottom, exactly one accent tab, real icons (or flagged placeholder).
- [ ] Bottom sheet rises from the edge (grab handle, top-rounded), not a centered modal; reduced-motion handled.
- [ ] Touch targets ≥44px; feedback on `:active`, not hover-only; safe-area insets used.
- [ ] Still grayscale + one accent — list values not colored by status; one colored hero max.
