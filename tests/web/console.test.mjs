// The page's errors reach Pythia's log with the error console off (Droidtop/tracker#538 D6).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

function load() {
  const listeners = {};
  const quiet = { error() {}, warn() {}, log() {} };
  const node = () => ({ appendChild() {}, classList: { toggle() {}, add() {}, remove() {} }, addEventListener() {}, querySelector: () => null, querySelectorAll: () => [], style: {} });
  const window = {
    UI: { esc: (s) => String(s), el: node, $: node, $$: () => [], flash() {} },
    addEventListener: (k, f) => { (listeners[k] ||= []).push(f); },
  };
  const ctx = { window, console: quiet, document: { body: node(), addEventListener() {}, createElement: node }, localStorage: { getItem: () => null, setItem() {} } };
  vm.createContext(ctx);
  vm.runInContext(readFileSync(new URL('../../pythia/ui/desktop/web/console.js', import.meta.url), 'utf8'), ctx);
  return { ErrorConsole: window.ErrorConsole, ctx, listeners };
}

test('errors before and after the channel connects reach the backend, console off', () => {
  const { ErrorConsole, ctx, listeners } = load();
  ErrorConsole.init();
  const sent = [];
  ctx.console.error('early');
  ErrorConsole.attach({ report_web_error: (level, message, context) => sent.push([level, message, context]) });
  ctx.console.error('late');
  listeners.error.forEach((f) => f({ message: 'boom', filename: 'x/app.js', lineno: 3 }));
  assert.deepEqual(sent.map((s) => s[1]), ['early', 'late', 'boom']);
  assert.equal(sent[2][2], 'app.js:3');
});
