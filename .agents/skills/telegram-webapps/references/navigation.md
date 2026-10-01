# SPA Navigation in Telegram Mini Apps

Telegram Mini Apps run inside a WebView with no address bar and no standard browser chrome. This guide covers routing, the BackButton lifecycle, transitions, scroll management, data passing, and deep links — with working code for each approach.

---

## 1. Routing approaches

Three options exist. Pick based on project complexity.

### Div-based router (simplest)

Show and hide DOM sections directly. No URL changes, no browser history, no server involvement.

```html
<div id="page-home">Home content</div>
<div id="page-settings" style="display:none">Settings content</div>
```

```js
const pages = {
  home:     document.getElementById('page-home'),
  settings: document.getElementById('page-settings'),
};
let currentPage = 'home';

function navigate(page) {
  pages[currentPage].style.display = 'none';
  pages[page].style.display = 'block';
  currentPage = page;
}

// Usage
navigate('settings');
navigate('home');
```

**Tradeoffs:**
- No browser back button support
- No deep linking
- No history — refreshing always returns to the initial state
- Simplest to implement and debug; zero dependencies

---

### Hash router (medium complexity)

Use `location.hash` as the single source of truth. The browser history stack works correctly, so the Telegram desktop preview's back gesture works without any extra code.

```js
// Navigate by setting the hash
function navigate(hash) {
  location.hash = hash; // e.g. '#settings' or '#product/123'
}

// Respond to hash changes (browser back/forward, or direct navigate() calls)
window.addEventListener('hashchange', () => {
  renderPage(location.hash);
});

// Also handle initial load
function renderPage(hash) {
  const [route, param] = hash.replace('#', '').split('/');
  switch (route) {
    case 'settings': showSettings(); break;
    case 'product':  showProduct(param); break;
    default:         showHome(); break;
  }
}

// Initialize on load
renderPage(location.hash || '#home');
```

**Tradeoffs:**
- Browser back/forward works in desktop preview
- Hash appears in the URL (harmless inside the WebView, but visible in dev tools)
- Slightly more code than div-based routing
- Still no server-side routing needed — the hash never reaches the server

---

### Framework routers

**React Router v6**

Mini Apps run entirely client-side, so `BrowserRouter` requires the server to return the same HTML for all paths. Use `HashRouter` instead to avoid a 404 on refresh.

```tsx
import { HashRouter, Routes, Route, useNavigate } from 'react-router-dom';

function App() {
  return (
    <HashRouter>
      <Routes>
        <Route path="/"         element={<Home />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/product/:id" element={<Product />} />
      </Routes>
    </HashRouter>
  );
}

// Inside a component
function Home() {
  const navigate = useNavigate();
  return <button onClick={() => navigate('/settings')}>Settings</button>;
}
```

If your Mini App is served with a static file host that supports a `200` catch-all (Vercel, Netlify, Cloudflare Pages with a `_redirects` file), `BrowserRouter` also works.

**Vue Router**

```js
// router/index.js
import { createRouter, createWebHashHistory } from 'vue-router';

const router = createRouter({
  history: createWebHashHistory(), // hash mode — no server config needed
  routes: [
    { path: '/',         component: () => import('./pages/Home.vue') },
    { path: '/settings', component: () => import('./pages/Settings.vue') },
  ],
});

export default router;
```

**Official Telegram integration package**

`@telegram-apps/react-router-integration` wires the Telegram BackButton to React Router automatically:

```tsx
import { useNavigate, useLocation } from 'react-router-dom';
import { useBackButton } from '@telegram-apps/react-router-integration';

function App() {
  const navigate  = useNavigate();
  const location  = useLocation();
  useBackButton(navigate, location); // handles show/hide/onClick automatically
  // ...
}
```

Install: `npm install @telegram-apps/react-router-integration`

---

## 2. BackButton lifecycle — THE CRITICAL SECTION

`tg.BackButton` is a Telegram-rendered button, not the browser back button. It does nothing until you register a handler. It is also the most common source of subtle bugs in Mini Apps.

### The stacking bug (most common mistake)

`BackButton.onClick()` does NOT replace the previous handler — it adds a new one. Every handler registered is called on every press until it is removed with `offClick`.

```js
// WRONG — if enterSettings() is called twice, the handler fires twice per press
function enterSettings() {
  tg.BackButton.show();
  tg.BackButton.onClick(() => navigate('home')); // stacks on top of the previous call
}
```

This means if the user navigates settings -> home -> settings, pressing back fires the handler three times.

### Correct pattern: a single managed handler

Always remove the previous handler before registering a new one. Keep one reference.

```js
const tg = window.Telegram.WebApp;
let _backHandler = null;

function setBackHandler(fn) {
  // Remove the previous handler using the exact same reference
  if (_backHandler) tg.BackButton.offClick(_backHandler);

  _backHandler = fn;

  if (fn) {
    tg.BackButton.show();
    tg.BackButton.onClick(fn);
  } else {
    tg.BackButton.hide();
  }
}

// Usage — entering and leaving a page
function enterSettings() {
  setBackHandler(() => navigate('home'));
}

function exitSettings() {
  setBackHandler(null); // hides the button and clears the handler
}
```

`offClick` requires the **exact same function reference** that was passed to `onClick`. This is why storing `_backHandler` as a module-level variable is essential — passing a new arrow function to `offClick` does nothing.

---

### React hook for BackButton

```tsx
import { useEffect } from 'react';

const tg = window.Telegram.WebApp;

/**
 * Registers a BackButton handler for the lifetime of the component.
 * Pass null to hide the button (use on root/home screens).
 */
export function useBackButton(handler: (() => void) | null) {
  useEffect(() => {
    if (!handler) {
      tg.BackButton.hide();
      return;
    }

    tg.BackButton.show();
    tg.BackButton.onClick(handler);

    // Cleanup: remove this exact reference when the component unmounts
    // or when handler changes
    return () => {
      tg.BackButton.offClick(handler);
      tg.BackButton.hide();
    };
  }, [handler]); // re-run when the handler function reference changes
}

// Usage inside a page component:
function SettingsPage() {
  const navigate = useNavigate();
  // Wrap in useCallback so the reference is stable across renders
  const goBack = useCallback(() => navigate('/'), [navigate]);
  useBackButton(goBack);
  return <div>Settings</div>;
}

// Usage on home page — hide the button:
function HomePage() {
  useBackButton(null);
  return <div>Home</div>;
}
```

**Important:** pass a stable function reference. If you write `useBackButton(() => navigate('/'))` inline, the reference changes every render, causing the effect to re-run and the cleanup/re-register cycle to fire on every render. Use `useCallback`.

---

### Route-aware BackButton (vanilla JS hash router)

```js
const tg = window.Telegram.WebApp;
const ROOT_ROUTES = ['', 'home']; // routes where the back button should be hidden

// Call this whenever the route changes
function onRouteChange(hash) {
  const route = hash.replace('#', '').split('/')[0];
  if (ROOT_ROUTES.includes(route)) {
    setBackHandler(null);   // home screen — hide the button
  } else {
    setBackHandler(() => history.back()); // use browser history
  }
}

window.addEventListener('hashchange', () => onRouteChange(location.hash));
onRouteChange(location.hash); // initialize on load
```

---

## 3. Page transitions

Smooth transitions make a Mini App feel native. The key constraint: Telegram's WebView runs on older WebKit versions on some devices, so keep CSS simple.

### Slide transition (CSS + JS)

```css
/* All pages sit in the same stacking context */
.page-container {
  position: relative;
  width: 100%;
  height: 100%;
  overflow: hidden;
}

.page {
  position: absolute;
  inset: 0;
  overflow-y: auto;
  transition: transform 0.25s ease;
  /* Start off-screen to the right */
  transform: translateX(100%);
}

.page.active {
  transform: translateX(0);
}

.page.exiting {
  transform: translateX(-100%);
}

/* Respect the user's motion preference */
@media (prefers-reduced-motion: reduce) {
  .page {
    transition: none;
  }
}
```

```js
let activePageId = 'page-home';

function navigate(nextId) {
  if (nextId === activePageId) return;

  const current = document.getElementById(activePageId);
  const next    = document.getElementById(nextId);

  // Step 1: position next page off to the right (already done by CSS default)
  // Step 2: trigger the transition in the next frame so the browser sees the
  //         'entering' position before animating to 'active'
  next.style.display = 'block';

  requestAnimationFrame(() => {
    current.classList.add('exiting');    // slide current left
    next.classList.add('active');        // slide next in from right

    current.addEventListener('transitionend', () => {
      current.classList.remove('exiting');
      current.style.display = 'none';
    }, { once: true }); // { once: true } auto-removes the listener after firing

    activePageId = nextId;
  });
}
```

**Going back** (reverse direction):

```js
function navigateBack(prevId) {
  const current = document.getElementById(activePageId);
  const prev    = document.getElementById(prevId);

  prev.style.display = 'block';
  // Place previous page off to the left
  prev.style.transform = 'translateX(-100%)';

  requestAnimationFrame(() => {
    prev.style.transform = '';        // animate back to center
    prev.classList.add('active');
    current.style.transform = 'translateX(100%)'; // exit to the right

    current.addEventListener('transitionend', () => {
      current.classList.remove('active');
      current.style.transform = '';
      current.style.display = 'none';
    }, { once: true });

    activePageId = prevId;
  });
}
```

---

## 4. Scroll position preservation

When a user navigates away from a scrolled page and returns, they expect to land at the same position.

```js
const scrollPositions = {};

function navigate(fromId, toId) {
  // Save current scroll before switching
  const fromEl = document.getElementById(fromId);
  if (fromEl) {
    scrollPositions[fromId] = fromEl.scrollTop;
  }

  // Switch pages (hide/show or transition logic here)
  fromEl.style.display = 'none';
  const toEl = document.getElementById(toId);
  toEl.style.display = 'block';

  // Restore scroll position, or reset to top on first visit
  toEl.scrollTop = scrollPositions[toId] ?? 0;
}
```

For React with React Router, use the `useLocation` scroll restoration pattern:

```tsx
import { useEffect, useRef } from 'react';
import { useLocation } from 'react-router-dom';

const scrollCache = new Map<string, number>();

export function ScrollRestoration() {
  const location = useLocation();
  const containerRef = useRef<HTMLDivElement>(null);

  // Save scroll on unmount / route change
  useEffect(() => {
    const container = containerRef.current;
    return () => {
      if (container) scrollCache.set(location.pathname, container.scrollTop);
    };
  }, [location.pathname]);

  // Restore scroll on mount
  useEffect(() => {
    const container = containerRef.current;
    if (container) {
      container.scrollTop = scrollCache.get(location.pathname) ?? 0;
    }
  }, [location.pathname]);

  return <div ref={containerRef} style={{ height: '100%', overflowY: 'auto' }}>
    {/* page content */}
  </div>;
}
```

---

## 5. Passing data between pages

### Simple: URL params with the hash router

Encode data directly in the hash. Parse it in the renderer.

```js
// Navigate to a product page
function openProduct(id) {
  location.hash = `#product/${id}`;
}

// In the renderer
function renderPage(hash) {
  const [route, param] = hash.replace('#', '').split('/');
  if (route === 'product') {
    showProduct(param); // param is the product ID string
  }
}
```

For more complex data, base64-encode a JSON object:

```js
function openProductWithData(product) {
  const encoded = btoa(JSON.stringify({ id: product.id, name: product.name }));
  location.hash = `#product/${encoded}`;
}

// In the renderer
function renderProduct(encoded) {
  const data = JSON.parse(atob(encoded));
  // data.id, data.name
}
```

---

### Module-level store (vanilla JS)

A shared singleton that any module can import:

```js
// store.js
export const store = {
  selectedProductId: null,
  cartItems: [],
  user: null,
};

// In a page module
import { store } from './store.js';

function openProduct(id) {
  store.selectedProductId = id;
  navigate('product');
}

// In the product page module
import { store } from './store.js';

function initProductPage() {
  const product = getProductById(store.selectedProductId);
  renderProduct(product);
}
```

---

### React: context or navigation state (React Router)

Pass lightweight data via navigation state (no URL pollution):

```tsx
import { useNavigate, useLocation } from 'react-router-dom';

// Sender
function ProductList() {
  const navigate = useNavigate();
  return (
    <button onClick={() => navigate('/product', { state: { id: 123, name: 'Widget' } })}>
      Open Widget
    </button>
  );
}

// Receiver
function ProductPage() {
  const location = useLocation();
  const { id, name } = location.state as { id: number; name: string };
  return <div>{name} (ID: {id})</div>;
}
```

For app-wide state (cart, user, etc.) use React Context or a state library (Zustand, Jotai):

```tsx
// CartContext.tsx
import { createContext, useContext, useState } from 'react';

const CartContext = createContext<{ items: number[]; add: (id: number) => void } | null>(null);

export function CartProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<number[]>([]);
  const add = (id: number) => setItems(prev => [...prev, id]);
  return <CartContext.Provider value={{ items, add }}>{children}</CartContext.Provider>;
}

export const useCart = () => {
  const ctx = useContext(CartContext);
  if (!ctx) throw new Error('useCart must be used inside CartProvider');
  return ctx;
};
```

---

## 6. Deep link routing via start_param

When a user taps a `t.me/yourbot/yourapp?startapp=...` link, Telegram passes the value after `startapp=` as `tg.initDataUnsafe.start_param`. Use this to route directly to a specific page on launch.

`start_param` constraints: A-Z, a-z, 0-9, `-`, `_` only. Maximum 512 characters.

```js
const tg = window.Telegram.WebApp;

function initRouting() {
  const startParam = tg.initDataUnsafe?.start_param;

  // No deep link — stay on home
  if (!startParam) return;

  // Simple case: the param IS the route name
  if (startParam === 'settings') {
    navigate('settings');
    return;
  }

  if (startParam === 'cart') {
    navigate('cart');
    return;
  }

  // Complex case: base64url-encoded JSON payload
  // base64url uses - and _ instead of + and /,  and omits padding =
  try {
    // Restore padding that base64url strips (add 0–3 '=' chars to reach a multiple of 4)
    const padded = startParam + '='.repeat((4 - startParam.length % 4) % 4);
    // Convert base64url to standard base64
    const b64      = padded.replace(/-/g, '+').replace(/_/g, '/');
    const payload  = JSON.parse(atob(b64));
    // payload.page tells us where to go; the rest is page-specific data
    navigate(payload.page, payload);
  } catch {
    // Unrecognized or malformed deep link — stay on home
    console.warn('Invalid start_param:', startParam);
  }
}

// Call during app initialization, after Telegram.WebApp.ready()
tg.ready();
initRouting();
```

**Generating deep links from your backend (Node.js):**

```js
// Simple route
const link = 'https://t.me/yourbot/yourapp?startapp=settings';

// Complex payload
const payload  = { page: 'product', id: 123 };
const encoded  = Buffer.from(JSON.stringify(payload)).toString('base64url');
// base64url handles padding and URL-safe chars automatically in Node 16+
const deepLink = `https://t.me/yourbot/yourapp?startapp=${encoded}`;
```

**Generating deep links in the browser:**

```js
function encodeDeepLink(payload) {
  const json    = JSON.stringify(payload);
  const b64     = btoa(String.fromCharCode(...new TextEncoder().encode(json))); // handle unicode
  const b64url  = b64.replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  return `https://t.me/yourbot/yourapp?startapp=${b64url}`;
}

encodeDeepLink({ page: 'product', id: 123 });
// → "https://t.me/yourbot/yourapp?startapp=eyJwYWdlIjoicHJvZHVjdCIsImlkIjoxMjN9"
```

---

## 7. Gotchas

**BackButton is not the browser back button.**
It is a Telegram-side button that fires your registered handler. It does nothing until you call `BackButton.onClick(fn)`. Even when visible, without a handler, pressing it has no effect.

**`offClick` requires the exact same function reference.**
This fails silently:
```js
tg.BackButton.onClick(() => navigate('home'));   // registers anonymous fn
tg.BackButton.offClick(() => navigate('home'));  // does NOTHING — different reference
```
Always store the reference and pass the stored variable to `offClick`.

**iOS hardware back swipe is not intercepted by `tg.BackButton`.**
On iPhone, the edge-swipe gesture triggers the WebView's own history navigation. If you use a hash router, this works correctly. If you use a div-based router with no history, the swipe does nothing or closes the Mini App. There is no way to intercept the iOS swipe gesture.

**Never navigate by changing `window.location.href`.**
```js
window.location.href = '/settings'; // WRONG — reloads the entire Mini App
```
This causes a full page reload and loses all app state. Use your router's navigate function instead.

**`window.location.hash` changes are the only safe URL-level navigation.**
Changing the hash does not reload the page, does not break the WebView, and correctly updates the browser history stack.

**In fullscreen mode (Bot API 8.0+), `BackButton` may not render.**
When `tg.requestFullscreen()` is active, the Telegram chrome (including the BackButton) may be hidden. Always provide an in-app back button in fullscreen layouts as a fallback.

**Always call `tg.ready()` before `tg.initDataUnsafe`.**
`initDataUnsafe` is populated synchronously, but calling `tg.ready()` signals to Telegram that the app has loaded and dismisses the loading indicator. Do it early.

```js
const tg = window.Telegram.WebApp;
tg.ready();  // call this first
tg.expand(); // optional: expand to full height
// Now safe to read tg.initDataUnsafe, tg.colorScheme, etc.
```

**`BackButton.hide()` does not call `offClick`.**
Hiding the button does not remove the handler. If you later call `BackButton.show()` and `onClick` again without calling `offClick` first, the handler stacks. Always use the `setBackHandler` pattern (section 2) rather than calling `show`/`hide`/`onClick` directly.
