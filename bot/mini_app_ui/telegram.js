export function createTelegramAdapter(onBack, onThemeChange = () => {}) {
  const sdk = window.Telegram?.WebApp;
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
    safeCall('onEvent', 'safeAreaChanged', applyInsets);
    safeCall('onEvent', 'contentSafeAreaChanged', applyInsets);
    safeCall('onEvent', 'viewportChanged', applyInsets);
    safeCall('onEvent', 'fullscreenChanged', applyInsets);
    safeCall('onEvent', 'fullscreenFailed', applyInsets);
    safeCall('requestFullscreen');
    handleTheme();
    applyInsets();
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
    for (const [name,value] of [['viewport-height',sdk?.viewportHeight],['viewport-stable-height',sdk?.viewportStableHeight]]) {
      root.setProperty(`--${name}`, typeof value === 'number' && Number.isFinite(value) && value > 0 && value <= 10000 ? `${value}px` : '100dvh');
    }
  }
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
      safeCall('offEvent','safeAreaChanged', applyInsets);
      safeCall('offEvent','contentSafeAreaChanged', applyInsets);
      safeCall('offEvent','viewportChanged', applyInsets);
      safeCall('offEvent','fullscreenChanged', applyInsets);
      safeCall('offEvent','fullscreenFailed', applyInsets);
      themeListeners.clear();
      for (const finish of pending) finish(null);
    },
  };
}
