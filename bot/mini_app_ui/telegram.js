export function createTelegramAdapter(onBack, onThemeChange = () => {}) {
  const sdk = window.Telegram?.WebApp;
  const viewport = window.visualViewport || null;
  let viewportFrame = 0;
  let backAttached = false;
  let disposed = false;
  const themeListeners = new Set();
  const pending = new Set();
  const safeCall = (method, ...args) => { try { sdk?.[method]?.(...args); } catch {} };
  const handleBack = () => onBack();
  function getTheme() {
    const values = {};
    for (const key of ['bg_color','secondary_bg_color','text_color','hint_color','link_color','button_color','button_text_color',
      'header_bg_color','accent_text_color','section_bg_color','section_header_text_color','subtitle_text_color','destructive_text_color',
      'section_separator_color','bottom_bar_bg_color']) {
      const value = sdk?.themeParams?.[key];
      if (typeof value === 'string' && /^#[0-9a-f]{6}$/i.test(value)) values[key] = value.toLowerCase();
    }
    return { colorScheme:sdk?.colorScheme==='dark'?'dark':'light', themeParams:Object.freeze(values) };
  }
  const handleTheme = () => {
    if (disposed) return;
    const snapshot = getTheme();
    onThemeChange(snapshot);
    for (const listener of themeListeners) listener(snapshot);
  };
  if (sdk) {
    safeCall('ready');
    safeCall('expand');
    safeCall('onEvent', 'themeChanged', handleTheme);
    safeCall('onEvent', 'safeAreaChanged', applyLayout);
    safeCall('onEvent', 'contentSafeAreaChanged', applyLayout);
    safeCall('onEvent', 'viewportChanged', applyLayout);
    safeCall('onEvent', 'fullscreenChanged', applyLayout);
    safeCall('onEvent', 'fullscreenFailed', applyLayout);
    safeCall('requestFullscreen');
    handleTheme();
    applyLayout();
  }
  function applyInsets() {
    if (disposed) return;
    const safe = sdk?.safeAreaInset || {};
    const content = sdk?.contentSafeAreaInset || {};
    const root = document.documentElement.style;
    const inset = value => typeof value === 'number' && Number.isFinite(value) ? Math.min(1024, Math.max(0, value)) : 0;
    for (const side of ['top','right','bottom','left']) {
      // Telegram controls occupy an additional area inside the device safe area.
      // CSS env and safeAreaInset describe the same device edge: never add both.
      root.setProperty(`--${side}-inset`, `calc(max(${inset(safe[side])}px, env(safe-area-inset-${side}, 0px)) + ${inset(content[side])}px)`);
    }
  }
  function applyViewport() {
    // Высота и смещение видимой части окна: на телефоне клавиатура уменьшает
    // visualViewport, но не layout viewport, из-за чего нижняя навигация и
    // модалка оказывались под клавиатурой. Считаем её размер честно.
    if (disposed) return;
    const root = document.documentElement.style;
    const pixels = value => typeof value === 'number' && Number.isFinite(value) ? Math.min(10000, Math.max(0, value)) : 0;
    const layoutHeight = pixels(window.innerHeight) || pixels(document.documentElement.clientHeight);
    const viewHeight = pixels(viewport?.height);
    const offsetTop = pixels(viewport?.offsetTop);
    const sdkHeight = pixels(sdk?.viewportHeight);
    const keyboard = viewHeight > 0 ? pixels(layoutHeight - viewHeight - offsetTop) : 0;
    const height = viewHeight > 0 && sdkHeight > 0 ? Math.min(viewHeight, sdkHeight) : (viewHeight || sdkHeight);
    root.setProperty('--viewport-height', height > 0 ? `${height}px` : '100dvh');
    root.setProperty('--viewport-offset-top', `${offsetTop}px`);
    root.setProperty('--keyboard-inset', `${keyboard}px`);
  }
  function scheduleViewport() {
    if (disposed || viewportFrame) return;
    viewportFrame = requestAnimationFrame(() => { viewportFrame = 0; applyViewport(); });
  }
  function applyLayout() { applyInsets(); applyViewport(); }
  viewport?.addEventListener('resize', scheduleViewport);
  viewport?.addEventListener('scroll', scheduleViewport);
  window.addEventListener('orientationchange', scheduleViewport);
  applyViewport();
  return {
    initData: sdk?.initData || '',
    getTheme,
    subscribeTheme(listener) { if (disposed) return () => {}; themeListeners.add(listener); return () => themeListeners.delete(listener); },
    setColors(tokens) {
      if (disposed) return;
      safeCall('setHeaderColor',tokens.header);
      safeCall('setBackgroundColor',tokens.canvas);
      safeCall('setBottomBarColor',tokens.bottom);
    },
    requestWriteAccess() {
      if (disposed || !sdk?.requestWriteAccess) return Promise.resolve(null);
      return new Promise((resolve) => {
        let finished = false;
        const finish = (value) => {
          if (finished) return;
          finished = true;
          clearTimeout(timer);
          pending.delete(finish);
          resolve(value);
        };
        const timer = setTimeout(() => finish(null), 10000);
        pending.add(finish);
        try { sdk.requestWriteAccess((granted) => finish(Boolean(granted))); }
        catch { finish(null); }
      });
    },
    requestChat(requestId) {
      if (disposed || !sdk?.requestChat || !requestId) return Promise.resolve(null);
      return new Promise((resolve) => {
        let finished = false;
        const finish = (value) => {
          if (finished) return;
          finished = true; clearTimeout(timer); pending.delete(finish); resolve(value);
        };
        const timer = setTimeout(() => finish(null), 120000);
        pending.add(finish);
        try { sdk.requestChat(requestId, (sent) => finish(Boolean(sent))); }
        catch { finish(null); }
      });
    },
    openLink(url) {
      if (sdk?.openLink) sdk.openLink(url);
      else window.open(url, '_blank', 'noopener,noreferrer');
    },
    openInvoice(url) {
      // Открывает счёт Telegram Stars. Возвращает статус оплаты: paid, cancelled,
      // failed, pending или null, если клиент не умеет открывать счета.
      return new Promise((resolve) => {
        if (disposed || !sdk?.openInvoice || typeof url !== 'string' || !url) { resolve(null); return; }
        let settled = false;
        const finish = (status) => { if (!settled) { settled = true; clearTimeout(timer); resolve(status); } };
        const timer = setTimeout(() => finish(null), 180000);
        try { sdk.openInvoice(url, (status) => finish(status)); }
        catch { finish(null); }
      });
    },
    openTelegramLink(url) {
      if (sdk?.openTelegramLink) sdk.openTelegramLink(url);
      else window.open(url, '_blank', 'noopener,noreferrer');
    },
    syncBack(visible) {
      if (disposed || !sdk?.BackButton) return;
      if (visible) {
        if (!backAttached) { sdk.BackButton.onClick(handleBack); backAttached = true; }
        sdk.BackButton.show();
      } else {
        sdk.BackButton.hide();
        if (backAttached) { sdk.BackButton.offClick(handleBack); backAttached = false; }
      }
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      if (backAttached) sdk.BackButton.offClick(handleBack);
      backAttached = false;
      safeCall('offEvent','themeChanged', handleTheme);
      safeCall('offEvent','safeAreaChanged', applyLayout);
      safeCall('offEvent','contentSafeAreaChanged', applyLayout);
      safeCall('offEvent','viewportChanged', applyLayout);
      safeCall('offEvent','fullscreenChanged', applyLayout);
      safeCall('offEvent','fullscreenFailed', applyLayout);
      viewport?.removeEventListener('resize', scheduleViewport);
      viewport?.removeEventListener('scroll', scheduleViewport);
      window.removeEventListener('orientationchange', scheduleViewport);
      if (viewportFrame) cancelAnimationFrame(viewportFrame);
      viewportFrame = 0;
      themeListeners.clear();
      for (const finish of pending) finish(null);
    },
  };
}
