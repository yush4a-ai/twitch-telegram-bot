# UX Patterns Guide for Telegram Mini Apps

This guide covers the patterns, CSS tricks, and JS patterns that make a Mini App feel native rather than like a website dropped into a WebView. All code is copy-paste ready.

---

## 1. Native Feel CSS

These CSS properties are mandatory for any Mini App to feel native. Apply them globally at the top of your stylesheet.

```css
/* ─── Remove blue tap flash on mobile ─────────────────────────────────────── */
*, *::before, *::after {
  -webkit-tap-highlight-color: transparent;
}

/* ─── Prevent double-tap zoom on interactive elements ─────────────────────── */
[role="button"], button, a, label, [tabindex] {
  touch-action: manipulation;
}

/* ─── Prevent pull-to-refresh and bounce on body ──────────────────────────── */
body {
  overscroll-behavior: none;
}

/* ─── Smooth inertia scrolling on scroll containers ───────────────────────── */
.scroll-container {
  overflow-y: auto;
  -webkit-overflow-scrolling: touch;
  overscroll-behavior-y: contain; /* allow inner scroll, block body bounce */
}

/* ─── Anti-aliased text on iOS ────────────────────────────────────────────── */
body {
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}

/* ─── Prevent text selection during swipe gestures ────────────────────────── */
.ui-element {
  user-select: none;
  -webkit-user-select: none;
}
```

### Complete base reset (combine everything above)

```css
/* paste at the very top of your global CSS */
*, *::before, *::after {
  box-sizing: border-box;
  -webkit-tap-highlight-color: transparent;
}

body {
  margin: 0;
  overscroll-behavior: none;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  background-color: var(--tg-theme-bg-color);
  color: var(--tg-theme-text-color);
}

[role="button"], button, a, label, [tabindex] {
  touch-action: manipulation;
}

.scroll-container {
  overflow-y: auto;
  -webkit-overflow-scrolling: touch;
  overscroll-behavior-y: contain;
}
```

---

## 2. Keyboard / Input Handling

This is the most common layout bug in Mini Apps. When the virtual keyboard opens, `viewportHeight` shrinks. Fixed-bottom elements (MainButton, bottom nav) overlap the keyboard or get pushed up. Here is how to handle it correctly.

### Detecting keyboard open/close

```js
const tg = window.Telegram.WebApp;
let lastStableHeight = tg.viewportStableHeight;

tg.onEvent('viewportChanged', ({ isStateStable }) => {
  if (!isStateStable) return; // wait for the animation to settle

  const newHeight = tg.viewportStableHeight;
  // Heuristic: viewport shrinks by more than 15% → keyboard is open
  const keyboardOpen = newHeight < lastStableHeight * 0.85;

  handleKeyboardChange(keyboardOpen, newHeight);
  lastStableHeight = newHeight;
});
```

### Adjusting layout for keyboard

```js
function handleKeyboardChange(keyboardOpen, currentHeight) {
  // Expose the real usable height to CSS
  document.documentElement.style.setProperty('--app-height', `${currentHeight}px`);

  // If you have a fixed form footer that lives above the keyboard:
  const footer = document.querySelector('.form-footer');
  if (footer) {
    footer.style.position = keyboardOpen ? 'relative' : 'fixed';
  }
}
```

```css
/* Use --app-height instead of 100vh everywhere */
.full-screen {
  height: var(--app-height, 100vh);
}
```

### Keeping the focused input visible

```js
document.querySelectorAll('input, textarea').forEach(el => {
  el.addEventListener('focus', () => {
    // Small delay lets the keyboard animation start first
    setTimeout(() => {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }, 300);
  });
});
```

### MainButton and keyboard

MainButton is rendered by Telegram (not in your DOM), so it stays visible when the keyboard opens. On some platforms it overlaps the keyboard — avoid having both a form-submit button and MainButton active at the same time.

Pattern: hide MainButton while any input is focused, restore it on blur.

```js
const tg = window.Telegram.WebApp;
const inputs = document.querySelectorAll('input, textarea, select');

inputs.forEach(input => {
  input.addEventListener('focus', () => tg.MainButton.hide());
  input.addEventListener('blur',  () => {
    // Short delay so a tap on MainButton itself isn't swallowed
    setTimeout(() => tg.MainButton.show(), 100);
  });
});
```

### Full keyboard-aware form setup

```js
function initKeyboardAwareForm(formEl) {
  const tg = window.Telegram.WebApp;
  let lastStableHeight = tg.viewportStableHeight;

  tg.onEvent('viewportChanged', ({ isStateStable }) => {
    if (!isStateStable) return;
    const h = tg.viewportStableHeight;
    const keyboardOpen = h < lastStableHeight * 0.85;
    document.documentElement.style.setProperty('--app-height', `${h}px`);
    formEl.classList.toggle('keyboard-open', keyboardOpen);
    lastStableHeight = h;
  });

  formEl.querySelectorAll('input, textarea').forEach(el => {
    el.addEventListener('focus', () => {
      tg.MainButton.hide();
      setTimeout(() => el.scrollIntoView({ behavior: 'smooth', block: 'center' }), 300);
    });
    el.addEventListener('blur', () => setTimeout(() => tg.MainButton.show(), 100));
  });
}
```

---

## 3. Loading States

### Skeleton screens

Use Telegram CSS variables so skeletons respect the current theme automatically.

```css
.skeleton {
  background: linear-gradient(
    90deg,
    var(--tg-theme-secondary-bg-color) 25%,
    var(--tg-theme-bg-color)           50%,
    var(--tg-theme-secondary-bg-color) 75%
  );
  background-size: 200% 100%;
  animation: shimmer 1.5s infinite;
  border-radius: 8px;
}

@keyframes shimmer {
  0%   { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}

/* Skeleton text lines */
.skeleton-line        { height: 14px; margin-bottom: 8px; }
.skeleton-line.short  { width: 60%; }
.skeleton-line.long   { width: 90%; }

/* Skeleton avatar / image block */
.skeleton-avatar {
  width: 48px;
  height: 48px;
  border-radius: 50%;
}
```

```html
<!-- Example skeleton card -->
<div class="card">
  <div class="skeleton skeleton-avatar"></div>
  <div style="flex:1; margin-left:12px;">
    <div class="skeleton skeleton-line long"></div>
    <div class="skeleton skeleton-line short"></div>
  </div>
</div>
```

### Replacing skeleton with real content

```js
async function loadProductCard(id) {
  const card = document.getElementById('product-card');
  card.innerHTML = skeletonHTML(); // show skeleton immediately

  const product = await api.getProduct(id);

  card.innerHTML = `
    <img src="${product.imageUrl}" alt="${product.name}" />
    <h2>${product.name}</h2>
    <p>${product.price}</p>
  `;
}

function skeletonHTML() {
  return `
    <div class="skeleton skeleton-avatar"></div>
    <div style="flex:1; margin-left:12px;">
      <div class="skeleton skeleton-line long"></div>
      <div class="skeleton skeleton-line short"></div>
    </div>
  `;
}
```

### MainButton lifecycle during async operations

```js
const tg = window.Telegram.WebApp;

tg.MainButton.setText('Submit').show();
tg.MainButton.onClick(async () => {
  tg.MainButton.showProgress(); // shows spinner, disables button automatically

  try {
    await submitOrder();
    tg.close(); // success → close the Mini App
  } catch (err) {
    tg.MainButton.hideProgress(); // re-enable so the user can retry
    tg.showAlert(`Error: ${err.message}`);
  }
});
```

### Full page loading overlay (for initial load)

```css
.loading-overlay {
  position: fixed;
  inset: 0;
  background: var(--tg-theme-bg-color);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 9999;
  transition: opacity 0.3s ease;
}
.loading-overlay.hidden {
  opacity: 0;
  pointer-events: none;
}
```

```js
const tg = window.Telegram.WebApp;
tg.ready(); // signal to Telegram that the app is loading

async function initApp() {
  await Promise.all([loadUserData(), loadCatalog()]);
  document.querySelector('.loading-overlay').classList.add('hidden');
  tg.expand(); // expand to full height after content is ready
}

initApp();
```

---

## 4. Error States

### Inline field error display

```js
function showFieldError(inputEl, message) {
  // Reuse existing error element or create one
  let errEl = inputEl.nextElementSibling;
  if (!errEl?.classList.contains('field-error')) {
    errEl = document.createElement('div');
    errEl.className = 'field-error';
    inputEl.after(errEl);
  }
  errEl.textContent = message;
  inputEl.style.borderColor = 'var(--tg-theme-destructive-text-color)';
  inputEl.setAttribute('aria-invalid', 'true');
}

function clearFieldError(inputEl) {
  const errEl = inputEl.nextElementSibling;
  if (errEl?.classList.contains('field-error')) errEl.remove();
  inputEl.style.borderColor = '';
  inputEl.removeAttribute('aria-invalid');
}
```

```css
.field-error {
  color: var(--tg-theme-destructive-text-color);
  font-size: 0.8125rem; /* 13px */
  margin-top: 4px;
  padding-left: 2px;
}

input.error, textarea.error {
  border-color: var(--tg-theme-destructive-text-color);
  outline-color: var(--tg-theme-destructive-text-color);
}
```

### Form validation with inline errors

```js
function validateForm(formEl) {
  let valid = true;

  const name = formEl.querySelector('#name');
  if (!name.value.trim()) {
    showFieldError(name, 'Name is required');
    valid = false;
  } else {
    clearFieldError(name);
  }

  const email = formEl.querySelector('#email');
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.value)) {
    showFieldError(email, 'Enter a valid email address');
    valid = false;
  } else {
    clearFieldError(email);
  }

  return valid;
}

formEl.addEventListener('submit', (e) => {
  e.preventDefault();
  if (validateForm(formEl)) submitForm();
});
```

### Non-blocking toast (Telegram has no native toast)

```css
.toast {
  position: fixed;
  bottom: 80px;
  left: 50%;
  transform: translateX(-50%) translateY(20px);
  background: rgba(0, 0, 0, 0.75);
  color: #fff;
  border-radius: 20px;
  padding: 8px 16px;
  font-size: 0.875rem; /* 14px */
  opacity: 0;
  transition: opacity 0.2s ease, transform 0.2s ease;
  z-index: 1000;
  pointer-events: none;
  white-space: nowrap;
  max-width: calc(100vw - 32px);
  text-align: center;
}
.toast.visible {
  opacity: 1;
  transform: translateX(-50%) translateY(0);
}
```

```html
<!-- Place once in your HTML, near </body> -->
<div class="toast" id="toast" role="status" aria-live="polite"></div>
```

```js
let toastTimer = null;

function showToast(message, durationMs = 3000) {
  const toast = document.getElementById('toast');
  toast.textContent = message;
  toast.classList.add('visible');

  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove('visible'), durationMs);
}

// Usage
showToast('Order saved!');
showToast('Something went wrong. Please retry.', 5000);
```

### Error state for full-screen failures (network down, etc.)

```css
.error-screen {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: var(--app-height, 100vh);
  padding: 24px;
  text-align: center;
  gap: 16px;
}
.error-screen__title {
  font-size: 1.125rem;
  font-weight: 600;
  color: var(--tg-theme-text-color);
}
.error-screen__subtitle {
  font-size: 0.9375rem;
  color: var(--tg-theme-hint-color);
}
.error-screen__retry {
  padding: 12px 24px;
  border-radius: 12px;
  background: var(--tg-theme-button-color);
  color: var(--tg-theme-button-text-color);
  border: none;
  font-size: 1rem;
  font-weight: 500;
  cursor: pointer;
  touch-action: manipulation;
}
```

```js
function showErrorScreen(message, onRetry) {
  document.body.innerHTML = `
    <div class="error-screen">
      <div class="error-screen__title">Something went wrong</div>
      <div class="error-screen__subtitle">${message}</div>
      <button class="error-screen__retry" id="retry-btn">Try again</button>
    </div>
  `;
  document.getElementById('retry-btn').addEventListener('click', onRetry);
}
```

---

## 5. SecondaryButton

`SecondaryButton` appears alongside `MainButton` at the bottom of the screen. Use it for two equal-weight CTAs — for example "Confirm" and "Save Draft", or "Pay Now" and "Pay Later".

```js
const tg = window.Telegram.WebApp;

// Show both buttons
tg.MainButton.setText('Confirm Order').show();
tg.MainButton.onClick(confirmOrder);

tg.SecondaryButton.setText('Save Draft').show();
tg.SecondaryButton.onClick(saveDraft);
```

### Styling SecondaryButton

```js
// Match SecondaryButton to the app's secondary palette
tg.SecondaryButton.setParams({
  text: 'Save Draft',
  color: tg.themeParams.secondary_bg_color,
  text_color: tg.themeParams.text_color,
  is_active: true,
  is_visible: true,
});

// SecondaryButton appears to the LEFT of MainButton on the bottom bar
```

### Dynamic visibility based on state

```js
function updateButtons(formState) {
  if (formState === 'empty') {
    tg.MainButton.setText('Skip').show();
    tg.SecondaryButton.hide();
  } else if (formState === 'dirty') {
    tg.MainButton.setText('Continue').show();
    tg.SecondaryButton.setText('Save Draft').show();
  } else if (formState === 'saved') {
    tg.MainButton.setText('Submit').show();
    tg.SecondaryButton.setText('Discard').show();
  }
}
```

### Disable while loading

```js
async function handleConfirm() {
  // Disable both buttons during async work
  tg.MainButton.showProgress();
  tg.SecondaryButton.setParams({ is_active: false });

  try {
    await submitOrder();
    tg.close();
  } catch (err) {
    tg.MainButton.hideProgress();
    tg.SecondaryButton.setParams({ is_active: true });
    tg.showAlert(`Error: ${err.message}`);
  }
}
```

---

## 6. SettingsButton

`SettingsButton` renders a gear icon in the Mini App header. Use it for app preferences accessible from any screen, so you don't need to clutter your main UI with a settings entry point.

```js
const tg = window.Telegram.WebApp;

// Show the gear icon
tg.SettingsButton.show();

// Listen for taps
tg.onEvent('settingsButtonClicked', () => {
  navigate('settings'); // your SPA router call
});
```

### Hiding during sensitive flows

```js
// Hide during checkout / payment — the user should not leave mid-flow
function startCheckout() {
  tg.SettingsButton.hide();
  showCheckoutScreen();
}

function exitCheckout() {
  tg.SettingsButton.show();
  showHomeScreen();
}
```

### Combining with BackButton for nested navigation

```js
const tg = window.Telegram.WebApp;

function navigate(screen) {
  const isRoot = screen === 'home';

  // BackButton only on non-root screens
  isRoot ? tg.BackButton.hide() : tg.BackButton.show();

  // SettingsButton only on root — hide in settings itself to avoid recursion
  (isRoot) ? tg.SettingsButton.show() : tg.SettingsButton.hide();

  renderScreen(screen);
}

tg.onEvent('backButtonClicked',     () => navigate('home'));
tg.onEvent('settingsButtonClicked', () => navigate('settings'));
```

---

## 7. Closing Confirmation

Enable `closingConfirmation` whenever the user has unsaved changes. This causes Telegram to show a native "Are you sure?" dialog if the user tries to close the app.

```js
const tg = window.Telegram.WebApp;
let isDirty = false;

// Mark dirty on any form change
form.addEventListener('input', () => {
  if (!isDirty) {
    isDirty = true;
    tg.enableClosingConfirmation();
  }
});

// Clear after successful save
async function save() {
  await api.save(collectFormData());
  isDirty = false;
  tg.disableClosingConfirmation();
  showToast('Saved!');
}

// Explicit discard via in-app button
discardBtn.addEventListener('click', () => {
  tg.showConfirm('Discard changes?', (confirmed) => {
    if (confirmed) {
      isDirty = false;
      tg.disableClosingConfirmation();
      navigate('home');
    }
  });
});
```

### Automatic dirty-tracking utility

```js
function trackDirtyState(formEl) {
  const tg = window.Telegram.WebApp;
  const initialValues = new FormData(formEl);
  let dirty = false;

  formEl.addEventListener('input', () => {
    const current = new FormData(formEl);
    const changed = [...initialValues.keys()].some(
      key => initialValues.get(key) !== current.get(key)
    );
    if (changed && !dirty) {
      dirty = true;
      tg.enableClosingConfirmation();
    } else if (!changed && dirty) {
      dirty = false;
      tg.disableClosingConfirmation();
    }
  });

  return {
    markClean() {
      dirty = false;
      tg.disableClosingConfirmation();
    },
    isDirty: () => dirty,
  };
}

// Usage
const formGuard = trackDirtyState(document.querySelector('form'));
saveBtn.addEventListener('click', async () => {
  await api.save(collectFormData());
  formGuard.markClean();
});
```

---

## 8. Dark Mode for Non-CSS Content

CSS variables (`var(--tg-theme-*)`) update automatically when the user switches themes. These non-CSS rendering contexts do **not** — you must re-draw them manually.

### Canvas

```js
const tg = window.Telegram.WebApp;
const canvas = document.getElementById('my-canvas');
const ctx = canvas.getContext('2d');

function drawChart() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  // Always read theme params at draw time, not at init time
  ctx.fillStyle   = tg.themeParams.text_color;
  ctx.strokeStyle = tg.themeParams.button_color;
  ctx.font        = '14px -apple-system, BlinkMacSystemFont, sans-serif';

  // ... your drawing logic ...
}

tg.onEvent('themeChanged', drawChart); // re-draw on theme switch
drawChart(); // initial draw
```

### Inline SVGs

Use `currentColor` and CSS vars in SVG `fill`/`stroke` — they inherit from CSS automatically, so dark mode just works.

```svg
<!-- SVG that follows the theme -->
<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24">
  <!-- currentColor inherits from CSS color property -->
  <path fill="currentColor" d="M12 2a10 10 0 1 0 0 20A10 10 0 0 0 12 2Z"/>
  <!-- CSS var for fine-grained control -->
  <circle cx="12" cy="12" r="4" fill="var(--tg-theme-button-color)"/>
</svg>
```

```css
/* The SVG's currentColor picks this up */
.icon { color: var(--tg-theme-text-color); }
.icon-accent { color: var(--tg-theme-button-color); }
```

### Chart.js

```js
const tg = window.Telegram.WebApp;

function getChartColors() {
  return {
    labelColor:      tg.themeParams.text_color,
    borderColor:     tg.themeParams.button_color,
    backgroundColor: hexToRgba(tg.themeParams.button_color, 0.15),
    gridColor:       hexToRgba(tg.themeParams.hint_color, 0.2),
  };
}

function hexToRgba(hex, alpha) {
  const [r, g, b] = hex.match(/\w\w/g).map(x => parseInt(x, 16));
  return `rgba(${r},${g},${b},${alpha})`;
}

const chart = new Chart(ctx, {
  type: 'line',
  data: {
    labels: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri'],
    datasets: [{
      label: 'Revenue',
      data: [120, 190, 80, 250, 200],
      borderColor: getChartColors().borderColor,
      backgroundColor: getChartColors().backgroundColor,
    }]
  },
  options: {
    color: getChartColors().labelColor,
    scales: {
      x: { ticks: { color: getChartColors().labelColor },
           grid:  { color: getChartColors().gridColor } },
      y: { ticks: { color: getChartColors().labelColor },
           grid:  { color: getChartColors().gridColor } },
    },
  },
});

tg.onEvent('themeChanged', () => {
  const colors = getChartColors();
  chart.data.datasets[0].borderColor     = colors.borderColor;
  chart.data.datasets[0].backgroundColor = colors.backgroundColor;
  chart.options.color                    = colors.labelColor;
  chart.options.scales.x.ticks.color    = colors.labelColor;
  chart.options.scales.x.grid.color     = colors.gridColor;
  chart.options.scales.y.ticks.color    = colors.labelColor;
  chart.options.scales.y.grid.color     = colors.gridColor;
  chart.update();
});
```

### Lottie animations

```js
const tg = window.Telegram.WebApp;

const anim = lottie.loadAnimation({
  container: document.getElementById('lottie-container'),
  renderer: 'svg',
  loop: true,
  autoplay: true,
  path: '/animations/success.json',
});

// SVG renderer inherits currentColor — set on the container
function applyLottieTheme() {
  const container = document.getElementById('lottie-container');
  container.style.color = tg.themeParams.text_color;
}

tg.onEvent('themeChanged', applyLottieTheme);
applyLottieTheme();
```

---

## 9. Animation Performance

Stick to GPU-composited properties. Layout-triggering animations cause jank on lower-end Android devices, which is where most Telegram users are.

### What to animate (fast — GPU composited)

```css
/* ✅ Use these */
.card {
  transition: transform 0.2s ease, opacity 0.2s ease;
  will-change: transform; /* hint the browser — use sparingly */
}

/* Slide in from below */
.card.entering {
  transform: translateY(16px);
  opacity: 0;
}
.card.entered {
  transform: translateY(0);
  opacity: 1;
}

/* Scale feedback on tap */
.pressable:active {
  transform: scale(0.97);
}
```

### What NOT to animate (slow — triggers layout)

```css
/* ❌ Never animate these */
.card { transition: height 0.2s ease; }      /* layout recalc every frame */
.card { transition: width 0.2s ease; }
.card { transition: top 0.2s ease; }
.card { transition: margin 0.2s ease; }
.card { transition: padding 0.2s ease; }

/* ✅ Replace height animation with transform */
.expandable {
  overflow: hidden;
  /* Animate max-height only when no better option — it's slower than transform */
}
.expandable-inner {
  /* Use transform: scaleY() for expand/collapse with transform-origin: top */
  transform-origin: top;
  transition: transform 0.25s ease, opacity 0.25s ease;
}
.expandable.collapsed .expandable-inner {
  transform: scaleY(0);
  opacity: 0;
}
```

### Staggered list entry animation

```js
function animateListIn(listEl) {
  const items = listEl.querySelectorAll('.list-item');
  items.forEach((item, i) => {
    item.style.setProperty('--delay', `${i * 40}ms`);
  });
}
```

```css
.list-item {
  opacity: 0;
  transform: translateY(12px);
  animation: slide-in 0.3s ease var(--delay, 0ms) forwards;
}

@keyframes slide-in {
  to { opacity: 1; transform: translateY(0); }
}
```

### Page transition (push/pop pattern)

```css
.page {
  position: absolute;
  inset: 0;
  background: var(--tg-theme-bg-color);
  transition: transform 0.3s ease, opacity 0.3s ease;
}

/* Incoming page starts off to the right */
.page.slide-enter      { transform: translateX(100%); }
.page.slide-enter-done { transform: translateX(0); }

/* Outgoing page exits to the left */
.page.slide-exit      { transform: translateX(0); }
.page.slide-exit-done { transform: translateX(-30%); opacity: 0; }
```

---

## 10. Tap Target Sizes

- Minimum 44×44pt (Apple HIG) / 48×48dp (Material Design)
- Telegram's own UI uses 48dp+ for all interactive elements
- Small icons need padding added to reach the minimum hit area

### Icon button pattern

```css
.icon-btn {
  min-width: 44px;
  min-height: 44px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  touch-action: manipulation;
  border: none;
  background: none;
  padding: 0;
  border-radius: 50%;
  color: var(--tg-theme-text-color);
  /* Provide visual feedback without layout change */
  transition: background-color 0.15s ease;
}

.icon-btn:active {
  background-color: var(--tg-theme-secondary-bg-color);
}

/* The visual icon inside can be smaller */
.icon-btn svg,
.icon-btn img {
  width: 24px;
  height: 24px;
  pointer-events: none; /* prevent ghost clicks on SVG children */
}
```

### List item tap targets

```css
.list-item {
  display: flex;
  align-items: center;
  min-height: 48px;       /* 48dp minimum */
  padding: 12px 16px;
  gap: 12px;
  cursor: pointer;
  touch-action: manipulation;
  user-select: none;
  -webkit-user-select: none;
  transition: background-color 0.1s ease;
}

.list-item:active {
  background-color: var(--tg-theme-secondary-bg-color);
}
```

### Checkbox / radio — extend the hit area

```css
/* The label IS the tap target, not the input */
.option-label {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 44px;
  padding: 8px 0;
  cursor: pointer;
  touch-action: manipulation;
  user-select: none;
  -webkit-user-select: none;
}

/* Visually hide the native input but keep it accessible */
.option-label input[type="checkbox"],
.option-label input[type="radio"] {
  position: absolute;
  opacity: 0;
  width: 0;
  height: 0;
}
```

---

## 11. Typography

### Font stack

```css
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
               Oxygen, Ubuntu, sans-serif;
}
```

This renders as San Francisco on iOS/macOS, Roboto on Android, and Segoe UI on Windows — matching the platform's native UI.

### Type scale (matching Telegram's own scale)

Use `rem` — it respects the user's system font-size preference. Some Telegram users have accessibility font scaling enabled, and `px` breaks that.

```css
:root {
  font-size: 16px; /* 1rem base */
}

/* ─── Telegram-matched type scale ─────────────────────────────────────────── */
.text-title       { font-size: 1.0625rem; font-weight: 600; line-height: 1.3; }  /* 17px */
.text-body        { font-size: 1rem;      font-weight: 400; line-height: 1.5; }  /* 16px */
.text-callout     { font-size: 0.9375rem; font-weight: 400; line-height: 1.4; }  /* 15px */
.text-caption     { font-size: 0.8125rem; font-weight: 400; line-height: 1.4; }  /* 13px */
.text-tiny        { font-size: 0.6875rem; font-weight: 400; line-height: 1.3; }  /* 11px */

/* Color helpers using TG vars */
.text-primary     { color: var(--tg-theme-text-color); }
.text-secondary   { color: var(--tg-theme-hint-color); }
.text-accent      { color: var(--tg-theme-link-color); }
.text-destructive { color: var(--tg-theme-destructive-text-color); }
```

### Complete typography example

```css
/* Page title */
h1 {
  font-size: 1.375rem; /* 22px */
  font-weight: 700;
  line-height: 1.25;
  color: var(--tg-theme-text-color);
  margin: 0 0 8px;
}

/* Section header (like Telegram's section titles in Settings) */
.section-header {
  font-size: 0.8125rem; /* 13px */
  font-weight: 500;
  letter-spacing: 0.03em;
  text-transform: uppercase;
  color: var(--tg-theme-hint-color);
  padding: 16px 16px 4px;
}

/* List item primary text */
.list-item__primary {
  font-size: 1rem; /* 16px */
  color: var(--tg-theme-text-color);
}

/* List item secondary / caption text */
.list-item__secondary {
  font-size: 0.8125rem; /* 13px */
  color: var(--tg-theme-hint-color);
  margin-top: 2px;
}

/* Numeric badge / count */
.badge {
  font-size: 0.75rem; /* 12px */
  font-weight: 600;
  line-height: 1;
  letter-spacing: -0.01em;
}
```

---

## Quick Reference

| Pattern | Key rule |
|---|---|
| Tap flash | `*{ -webkit-tap-highlight-color: transparent }` |
| Double-tap zoom | `touch-action: manipulation` on interactive elements |
| Body bounce | `body { overscroll-behavior: none }` |
| Keyboard layout | Listen `viewportChanged`, set `--app-height` CSS var |
| Keyboard + MainButton | Hide MainButton on focus, show on blur |
| Loading | `tg.MainButton.showProgress()` disables + shows spinner |
| Skeleton | Use `var(--tg-theme-*)` colors so it respects dark mode |
| Toast | Fixed position, 80px from bottom (above MainButton) |
| Dark mode canvas | Re-draw inside `tg.onEvent('themeChanged', fn)` |
| SVG dark mode | Use `currentColor` and `var(--tg-theme-*)` directly in SVG |
| Animation | Only `transform` and `opacity` — never `height`/`width`/`top` |
| Tap targets | Min 44×44px; use padding, not icon size |
| Font sizes | Use `rem`, not `px`; base = 16px |
| Closing confirmation | `tg.enableClosingConfirmation()` when form is dirty |
| SettingsButton | Hide during payment / multi-step flows |
| SecondaryButton | Disable with `setParams({ is_active: false })` during async |
