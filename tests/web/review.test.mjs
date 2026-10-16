// One Review queue (UX review A18): node --test tests/web/*.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const R = require('../../pythia/ui/desktop/web/review.js');
const game = (id, paths, extra = {}) => ({ id, variants: paths.map((path) => ({ path })), updates: [], unidentified: false, ...extra });

test('a game in several sections counts once', () => {
  const games = [game('novel', ['/l/n1', '/l/n2'], { updates: [{ version: '2' }] }), game('copy', ['/l/c'])];
  const q = R.queue({
    games,
    decisions: [{ path: '/l/n2' }],
    clusters: [{ members: [{ game_id: 'novel' }, { game_id: 'copy' }] }],
  });
  assert.equal(q.affected, 2);
  assert.equal(q.decisions[0].gameId, 'novel');
  assert.equal(q.updates.length, 1);
});

test('a decision on an unrecorded folder and a download each count', () => {
  const q = R.queue({ games: [game('x', ['/a'], { unidentified: true })], decisions: [{ path: '/new' }], detected: [{ path: '/dl/game.zip' }] });
  assert.equal(q.affected, 3);
  assert.equal(q.decisions[0].gameId, null);
  assert.equal(q.unidentified.length, 1);
  assert.equal(q.downloads.length, 1);
});

test('an empty queue counts nothing', () => {
  const q = R.queue({});
  assert.equal(q.affected, 0);
  assert.deepEqual(q.conflicts, []);
});
