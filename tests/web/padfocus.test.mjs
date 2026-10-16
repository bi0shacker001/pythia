// The D-pad's nearest item (docs/SPEC.md 2.1; Droidtop/tracker#538 retest B1): node --test tests/web/*.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { nearest } = require('../../pythia/ui/desktop/web/padfocus.js');
const box = (name, left, top, width, height) => ({ name, getBoundingClientRect: () => ({ left, top, width, height, right: left + width, bottom: top + height }) });
const walk = (items, from, direction) => { const seen = []; for (let at = from, next; (next = nearest(items, at, direction)); at = next) seen.push(next.name); return seen; };

// Settings > Interface as the handheld shell draws it: a full-width "‹ Settings", five narrow choices on the
// left, and a full-width "Open Desktop".
const back = box('‹ Settings', 110, 82, 1120, 46);
const choices = ['Start in', 'Handheld library view', 'Theme', 'Text size', 'Reduce motion'].map((n, i) => box(n, 123, 174 + i * 103, 200, 46));
const desktop = box('Open Desktop', 120, 715, 1100, 52);
const interfacePage = [back, ...choices, desktop];

test('Down from ‹ Settings reaches every choice on Settings > Interface, then Open Desktop', () => {
  assert.deepEqual(walk(interfacePage, back, 'down'), [...choices.map((c) => c.name), 'Open Desktop']);
});

test('Up from Open Desktop climbs back through every choice', () => {
  assert.deepEqual(walk(interfacePage, desktop, 'up'), [...choices.map((c) => c.name).reverse(), '‹ Settings']);
});

test('Up from the first library row reaches the page toolbar before the top bar', () => {
  const topBack = box('Back', 108, 11, 48, 48), search = box('Search', 335, 11, 560, 48);
  const view = box('List', 110, 82, 120, 40), review = box('Review', 600, 82, 120, 40);
  const firstRow = box('Patch Game', 110, 140, 1120, 70);
  assert.ok(['List', 'Review'].includes(nearest([topBack, search, view, review], firstRow, 'up').name));
  assert.equal(nearest([topBack, search], view, 'up').name, 'Back');
});

test('in a cover grid Down goes straight down and Left and Right stay in the row', () => {
  const cell = (r, c) => box(`${r}${c}`, 100 + c * 220, 100 + r * 300, 200, 280);
  const grid = [0, 1].flatMap((r) => [0, 1, 2].map((c) => cell(r, c)));
  const at = (name) => grid.find((g) => g.name === name);
  assert.equal(nearest(grid, at('01'), 'down').name, '11');
  assert.equal(nearest(grid, at('01'), 'right').name, '02');
  assert.equal(nearest(grid, at('01'), 'left').name, '00');
  assert.equal(nearest(grid, at('02'), 'right'), null);
  assert.equal(nearest(grid, at('12'), 'up').name, '02');
});
