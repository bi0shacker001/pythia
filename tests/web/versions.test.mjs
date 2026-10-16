// Version order (UX review A13): node --test tests/web/*.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const V = require('../../pythia/ui/desktop/web/versions.js');
const labels = (r) => r.list.map((g) => g.label);

test('compareLoose orders as droidtop does', () => {
  assert.ok(V.compareLoose('0.10', '0.9') > 0);
  assert.ok(V.compareLoose('1.2', '1.1.9') > 0);
  assert.ok(V.compareLoose('0.8.3b', '0.8.3') > 0);
  assert.ok(V.compareLoose('', '0.1') < 0);
  assert.equal(V.compareLoose('v1.2', '1.2'), 0);
});

test('1.0, 1.1 and 1.2 order newest first whatever the discovery order', () => {
  const r = V.order([{ label: '1.1' }, { label: '1.2' }, { label: '1.0' }]);
  assert.equal(r.ordered, true);
  assert.deepEqual(labels(r), ['1.2', '1.1', '1.0']);
  assert.equal(r.latest, '1.2');
});

test('labels that are not version numbers make the order unknown', () => {
  for (const odd of ['Final', 'Ch.3', 'Episode 4', '0.9-public', 'Remastered']) {
    const r = V.order([{ label: '1.0' }, { label: odd }]);
    assert.equal(r.ordered, false, odd);
    assert.equal(r.latest, null, odd);
    assert.deepEqual(r.unknown, [odd]);
  }
});

test('letter suffixes and a missing label still order', () => {
  const r = V.order([{ label: '1.0b2' }, { label: '' }, { label: '0.8.09r1' }, { label: '2.0rc3' }]);
  assert.equal(r.ordered, true);
  assert.deepEqual(labels(r), ['2.0rc3', '1.0b2', '0.8.09r1', '']);
});

test('one version alone is its own latest, whatever its label', () => {
  assert.equal(V.order([{ label: 'Final' }]).latest, 'Final');
});

test('middle ellipsis keeps both ends', () => {
  const p = '/home/someone/Games/adult/renpy/UX Test Novel v1.2 (Spanish translation)/game';
  const short = V.middleEllipsis(p, 40);
  assert.equal(short.length, 40);
  assert.ok(short.startsWith('/home/so'));
  assert.ok(short.endsWith('translation)/game'));
  assert.equal(V.middleEllipsis('/short', 40), '/short');
});
