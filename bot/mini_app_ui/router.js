const TABS = {
  viewer: ['home', 'streamers', 'profile'],
  streamer: ['channel', 'posts', 'profile'],
};

export function createRouter(onChange) {
  let savedMode = 'viewer';
  try { savedMode = localStorage.getItem('ts-app-mode') === 'streamer' ? 'streamer' : 'viewer'; } catch {}
  let state = { mode: savedMode, tab: TABS[savedMode][0], detail: null };
  const stack = [];
  const positions = new Map();
  const key = (item) => `${item.mode}:${item.tab}:${item.detail || ''}`;
  function navigate(next, push = true) {
    positions.set(key(state), window.scrollY);
    if (push) stack.push(state);
    state = next;
    onChange(state, stack.length > 0);
    requestAnimationFrame(() => window.scrollTo(0, positions.get(key(state)) || 0));
  }
  return {
    get state() { return state; },
    setMode(mode) {
      if (!TABS[mode] || mode === state.mode) return;
      try { localStorage.setItem('ts-app-mode', mode); } catch {}
      navigate({ mode, tab: TABS[mode][0], detail: null });
    },
    setTab(tab) {
      if (!TABS[state.mode].includes(tab) || (tab === state.tab && !state.detail)) return;
      navigate({ ...state, tab, detail: null });
    },
    openDetail(detail) { navigate({ ...state, detail }); },
    back() {
      if (!stack.length) return false;
      navigate(stack.pop(), false);
      return true;
    },
    tabs(mode = state.mode) { return TABS[mode]; },
    refresh() { onChange(state, stack.length > 0); },
  };
}
