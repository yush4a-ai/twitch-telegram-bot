"""Run the shared Mini App launch adapter against a local Telegram SDK double."""

import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "bot" / "mini_app_ui" / "telegram.js"

PROBE = """
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = readFileSync(process.argv[1]);
const { createTelegramAdapter } = await import(`data:text/javascript;base64,${source.toString('base64')}`);

function launch(options = {}) {
  const calls = [];
  const events = new Map();
  const timers = new Map();
  const styles = new Map();
  const history = {
    state: options.history ?? null,
    replaceState(state) { this.state = state; },
  };
  let timerId = 0;
  globalThis.setTimeout = (callback, ms) => {
    assert.ok(ms > 0 && ms <= 1500, 'fallback must be bounded');
    timers.set(++timerId, callback);
    return timerId;
  };
  globalThis.clearTimeout = id => timers.delete(id);
  globalThis.requestAnimationFrame = callback => { callback(); return 0; };
  globalThis.document = { documentElement: { style: { setProperty: (name, value) => styles.set(name, value) } } };

  const emit = (name, value) => events.get(name)?.(value);
  const sdk = {
    initData: 'signed-fixture-data',
    isFullscreen: options.fullscreen ?? false,
    // The official SDK starts with isExpanded=true before the first viewport event.
    isExpanded: true,
    viewportHeight: 580,
    isVersionAtLeast(version) { assert.equal(version, '8.0'); return options.legacy !== true; },
    ready() { calls.push('ready'); },
    expand() { calls.push('expand'); this.viewportHeight = 740; emit('viewportChanged'); },
    requestFullscreen() {
      calls.push('fullscreen');
      assert.ok(events.has('fullscreenFailed') && events.has('fullscreenChanged'));
      if (options.legacy || options.throwOnRequest) throw Error('WebAppMethodUnsupported');
      if (options.refused) emit('fullscreenFailed', { error: 'UNSUPPORTED' });
    },
    onEvent(name, callback) { events.set(name, callback); },
    offEvent(name, callback) { if (events.get(name) === callback) events.delete(name); },
  };
  if (options.withoutMethod) delete sdk.requestFullscreen;
  const viewport = Object.assign(new EventTarget(), { height: 844, offsetTop: 0 });
  globalThis.window = Object.assign(new EventTarget(), {
    Telegram: options.browser ? undefined : { WebApp: sdk },
    visualViewport: viewport, innerHeight: 844, history,
  });
  const adapter = createTelegramAdapter(() => {});
  const expire = () => { for (const callback of [...timers.values()]) callback(); };
  const fullscreen = active => { sdk.isFullscreen = active; emit('fullscreenChanged'); };
  return { calls, events, timers, styles, history, sdk, viewport, adapter, emit, expire, fullscreen };
}

const cases = {
  success() {
    const app = launch();
    assert.deepEqual(app.calls, ['ready', 'fullscreen']);
    assert.equal(app.adapter.initData, 'signed-fixture-data');
    app.sdk.viewportHeight = 844;
    app.fullscreen(true);
    app.expire();
    assert.deepEqual(app.calls, ['ready', 'fullscreen']);
    assert.equal(app.styles.get('--viewport-height'), '844px');
    app.sdk.safeAreaInset = { top: 44, bottom: 20 };
    app.sdk.contentSafeAreaInset = { top: 42, bottom: 4 };
    app.emit('safeAreaChanged');
    app.emit('contentSafeAreaChanged');
    assert.equal(app.styles.get('--top-inset'), 'calc(max(44px, env(safe-area-inset-top, 0px)) + 42px)');
    app.viewport.height = 520;
    app.viewport.offsetTop = 24;
    app.viewport.dispatchEvent(new Event('resize'));
    assert.equal(app.styles.get('--viewport-height'), '520px');
    assert.equal(app.styles.get('--keyboard-inset'), '300px');
    app.viewport.height = 844;
    app.viewport.offsetTop = 0;
    app.viewport.dispatchEvent(new Event('resize'));
    assert.equal(app.styles.get('--keyboard-inset'), '0px');
  },
  'already fullscreen on entry'() {
    const app = launch({ fullscreen: true });
    app.expire();
    assert.deepEqual(app.calls, ['ready']);
  },
  'already fullscreen reported by native client'() {
    const app = launch();
    app.emit('fullscreenFailed', { error: 'ALREADY_FULLSCREEN' });
    app.expire();
    assert.deepEqual(app.calls, ['ready', 'fullscreen']);
  },
  'client refusal'() {
    const app = launch({ refused: true });
    app.emit('fullscreenFailed', { error: 'UNSUPPORTED' });
    app.expire();
    assert.deepEqual(app.calls, ['ready', 'fullscreen', 'expand']);
    assert.equal(app.styles.get('--viewport-height'), '740px');
  },
  'client does not respond'() {
    const app = launch();
    assert.deepEqual(app.calls, ['ready', 'fullscreen']);
    app.expire();
    app.emit('fullscreenFailed', { error: 'UNSUPPORTED' });
    assert.deepEqual(app.calls, ['ready', 'fullscreen', 'expand']);
  },
  'legacy client exposes unsupported method'() {
    assert.deepEqual(launch({ legacy: true }).calls, ['ready', 'expand']);
  },
  'client lacks method'() {
    assert.deepEqual(launch({ withoutMethod: true }).calls, ['ready', 'expand']);
  },
  'bridge throws'() {
    const app = launch({ throwOnRequest: true });
    app.expire();
    assert.deepEqual(app.calls, ['ready', 'fullscreen', 'expand']);
  },
  'user exits fullscreen'() {
    const app = launch({ history: { existing: true } });
    app.fullscreen(true);
    app.fullscreen(false);
    app.emit('viewportChanged');
    app.expire();
    assert.deepEqual(app.calls, ['ready', 'fullscreen']);
    const reload = launch({ history: app.history.state });
    reload.expire();
    assert.equal(reload.history.state.existing, true);
    assert.deepEqual(reload.calls, ['ready']);
    assert.deepEqual(launch().calls, ['ready', 'fullscreen']);
  },
  'page closes during launch'() {
    const app = launch();
    app.adapter.dispose();
    assert.equal(app.events.size, 0);
    assert.equal(app.timers.size, 0);
    app.expire();
    assert.deepEqual(app.calls, ['ready', 'fullscreen']);
  },
  'plain browser'() {
    const app = launch({ browser: true });
    assert.deepEqual(app.calls, []);
    assert.equal(app.adapter.initData, '');
    assert.equal(app.styles.get('--viewport-height'), '844px');
  },
};

for (const [name, run] of Object.entries(cases)) {
  try { run(); }
  catch (error) { console.error(name, error.message); process.exitCode = 1; }
}
"""


def test_telegram_fullscreen_launch():
    node = shutil.which("node")
    assert node, "Node is required to run the Mini App adapter"
    result = subprocess.run(
        [node, "--input-type=module", "-e", PROBE, str(ADAPTER)],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
