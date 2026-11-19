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
  let timerId = 0;
  globalThis.setTimeout = callback => { timers.set(++timerId, callback); return timerId; };
  globalThis.clearTimeout = id => timers.delete(id);
  globalThis.requestAnimationFrame = callback => { callback(); return 0; };
  globalThis.document = { documentElement: { style: { setProperty: (name, value) => styles.set(name, value) } } };

  const emit = (name, value) => events.get(name)?.(value);
  const sdk = {
    initData: 'signed-fixture-data',
    isFullscreen: options.fullscreen ?? false,
    viewportHeight: options.height ?? 580,
    isVersionAtLeast(version) { assert.equal(version, '8.0'); return options.legacy !== true; },
    ready() { calls.push('ready'); },
    expand() { calls.push('expand'); },
    requestFullscreen() { calls.push('fullscreen'); },
    exitFullscreen() { calls.push('exit'); },
    onEvent(name, callback) { events.set(name, callback); },
    offEvent(name, callback) { if (events.get(name) === callback) events.delete(name); },
  };
  const viewport = Object.assign(new EventTarget(), { height: 844, offsetTop: 0 });
  globalThis.window = Object.assign(new EventTarget(), {
    Telegram: options.browser ? undefined : { WebApp: sdk },
    visualViewport: viewport, innerHeight: 844, history: { state: null },
  });
  const adapter = createTelegramAdapter(() => {});
  const expire = () => { for (const callback of [...timers.values()]) callback(); };
  const fullscreen = active => { sdk.isFullscreen = active; emit('fullscreenChanged'); };
  return { calls, events, timers, styles, sdk, viewport, adapter, emit, expire, fullscreen };
}

const cases = {
  'supported client opens at its chosen height'() {
    const app = launch();
    app.expire();
    assert.deepEqual(app.calls, ['ready']);
    assert.equal(app.adapter.initData, 'signed-fixture-data');
    assert.equal(app.styles.get('--viewport-height'), '580px');
  },
  'legacy client opens at its chosen height'() {
    const app = launch({ legacy: true });
    app.expire();
    assert.deepEqual(app.calls, ['ready']);
    assert.equal(app.styles.get('--viewport-height'), '580px');
  },
  'client already in fullscreen'() {
    const app = launch({ fullscreen: true, height: 844 });
    app.expire();
    assert.deepEqual(app.calls, ['ready']);
    assert.equal(app.styles.get('--viewport-height'), '844px');
  },
  'manual fullscreen, safe areas and keyboard'() {
    const app = launch();
    app.sdk.viewportHeight = 844;
    app.sdk.safeAreaInset = { top: 44, bottom: 20 };
    app.sdk.contentSafeAreaInset = { top: 42, bottom: 4 };
    app.fullscreen(true);
    assert.equal(app.styles.get('--viewport-height'), '844px');
    assert.equal(app.styles.get('--top-inset'), 'calc(max(44px, env(safe-area-inset-top, 0px)) + 42px)');
    app.sdk.safeAreaInset.bottom = 24;
    app.emit('safeAreaChanged');
    assert.equal(app.styles.get('--bottom-inset'), 'calc(max(24px, env(safe-area-inset-bottom, 0px)) + 4px)');
    app.sdk.contentSafeAreaInset.bottom = 6;
    app.emit('contentSafeAreaChanged');
    assert.equal(app.styles.get('--bottom-inset'), 'calc(max(24px, env(safe-area-inset-bottom, 0px)) + 6px)');
    app.viewport.height = 520;
    app.viewport.offsetTop = 24;
    app.viewport.dispatchEvent(new Event('resize'));
    assert.equal(app.styles.get('--viewport-height'), '520px');
    assert.equal(app.styles.get('--keyboard-inset'), '300px');
    app.viewport.height = 844;
    app.viewport.offsetTop = 0;
    app.viewport.dispatchEvent(new Event('resize'));
    assert.equal(app.styles.get('--keyboard-inset'), '0px');
    app.sdk.viewportHeight = 580;
    app.fullscreen(false);
    app.emit('viewportChanged');
    app.expire();
    assert.deepEqual(app.calls, ['ready']);
    assert.equal(app.styles.get('--viewport-height'), '580px');
  },
  'page disposes its listeners'() {
    const app = launch();
    app.adapter.dispose();
    assert.equal(app.events.size, 0);
    assert.equal(app.timers.size, 0);
    assert.deepEqual(app.calls, ['ready']);
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


def test_telegram_launch_keeps_the_client_window():
    node = shutil.which("node")
    assert node, "Node is required to run the Mini App adapter"
    result = subprocess.run(
        [node, "--input-type=module", "-e", PROBE, str(ADAPTER)],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
