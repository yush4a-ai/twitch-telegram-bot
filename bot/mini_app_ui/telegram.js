export function createTelegramAdapter(onBack, onThemeChange) {
  const sdk = window.Telegram?.WebApp;
  let backAttached = false;
  const handleBack = () => onBack();
  const handleTheme = () => onThemeChange(sdk?.colorScheme || 'light');
  if (sdk) {
    sdk.ready();
    sdk.expand();
    try { sdk.requestFullscreen?.(); } catch { /* Older clients can reject fullscreen. */ }
    sdk.onEvent?.('themeChanged', handleTheme);
    sdk.onEvent?.('safeAreaChanged', applyInsets);
    sdk.onEvent?.('contentSafeAreaChanged', applyInsets);
    sdk.onEvent?.('viewportChanged', applyInsets);
    handleTheme();
    applyInsets();
  }
  function applyInsets() {
    const safe = sdk?.safeAreaInset || {};
    const content = sdk?.contentSafeAreaInset || {};
    const root = document.documentElement.style;
    root.setProperty('--top-inset', `${Math.max(0, safe.top || 0, content.top || 0)}px`);
    root.setProperty('--bottom-inset', `${Math.max(0, safe.bottom || 0, content.bottom || 0)}px`);
  }
  return {
    initData: sdk?.initData || '',
    requestWriteAccess() {
      if (!sdk?.requestWriteAccess) return Promise.resolve(null);
      return new Promise((resolve) => {
        let finished = false;
        const finish = (value) => {
          if (finished) return;
          finished = true;
          clearTimeout(timer);
          resolve(value);
        };
        const timer = setTimeout(() => finish(null), 10000);
        try { sdk.requestWriteAccess((granted) => finish(Boolean(granted))); }
        catch { finish(null); }
      });
    },
    syncBack(visible) {
      if (!sdk?.BackButton) return;
      if (visible) {
        if (!backAttached) { sdk.BackButton.onClick(handleBack); backAttached = true; }
        sdk.BackButton.show();
      } else {
        sdk.BackButton.hide();
        if (backAttached) { sdk.BackButton.offClick(handleBack); backAttached = false; }
      }
    },
    dispose() {
      if (backAttached) sdk.BackButton.offClick(handleBack);
      sdk?.offEvent?.('themeChanged', handleTheme);
      sdk?.offEvent?.('safeAreaChanged', applyInsets);
      sdk?.offEvent?.('contentSafeAreaChanged', applyInsets);
      sdk?.offEvent?.('viewportChanged', applyInsets);
    },
  };
}
