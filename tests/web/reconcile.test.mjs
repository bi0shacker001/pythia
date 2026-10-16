// Possible duplicates as clusters (UX review A16): node --test tests/web/*.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const R = require('../../pythia/ui/desktop/web/reconcile.js');
const rec = (id, name, versions, sources = ['renpy'], stores = []) => ({ game_id: id, name, versions, sources, stores, folders: versions.length || 1 });
const pairsOf = (list, reason = 'name') => list.flatMap((a, i) => list.slice(i + 1).map((b) => ({ a, b, reason, score: 1 })));

test('four records of one novel are one cluster, not six pairs', () => {
  const novel = [rec('b', 'UX Test Novel', ['1.1']), rec('a', 'UX Test Novel', ['1.0']), rec('c', 'UX Test Novel', ['1.2']), rec('d', 'UX Test Novel', ['1.2'])];
  const out = R.clusters(pairsOf(novel));
  assert.equal(out.length, 1);
  assert.equal(out[0].members.length, 4);
  assert.equal(out[0].suggest, 'versions');
  assert.match(out[0].reason, /All are named UX Test Novel\. Versions found: 1\.0, 1\.1, 1\.2\./);
  assert.match(out[0].moves, /move into UX Test Novel/);
  assert.doesNotMatch(out[0].reason, /%/);
});

test('a store record and a folder record are the same game from another source', () => {
  const out = R.clusters(pairsOf([rec('s', 'Portal 2', [], ['steam'], ['steam']), rec('f', 'Portal 2', ['1.0'])]));
  assert.equal(out[0].suggest, 'source');
  assert.equal(out[0].target, 'f');
  assert.deepEqual(out[0].others, ['s']);
});

test('part folders say why, and separate clusters stay apart', () => {
  const serial = [rec('p1', 'UX Test Serial', []), rec('p2', 'UX Test Serial', [])];
  const other = [rec('x', 'Zeta Quest', ['1.0']), rec('y', 'Zeta Quests', ['0.9'])];
  const out = R.clusters([...pairsOf(serial, 'parts'), ...pairsOf(other)]);
  assert.equal(out.length, 2);
  assert.match(out.find((c) => c.name === 'UX Test Serial').reason, /side by side under one parent folder/);
  assert.match(out.find((c) => c.name === 'Zeta Quest').reason, /names are alike: Zeta Quest, Zeta Quests/);
});

test('pairs marked not the same do not join clusters', () => {
  const a = rec('a', 'X', ['1']); const b = rec('b', 'X', ['2']);
  assert.equal(R.clusters(pairsOf([a, b]), () => true).length, 0);
});
