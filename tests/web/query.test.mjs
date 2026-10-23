// The library query (UX review A11): node --test tests/web
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const Q = require('../../pythia/ui/desktop/web/query.js');

const novel = {
  name: 'UX Test Novel', source: 'Local folder', engine: "Ren'Py", version: '1.2', path: '/games/UX Test Novel-1.2',
  installed: true, updates: [], knownOn: [], platforms: ['linux'], systems: ['This computer'],
  versions: [
    { version: '1.0', variants: [{ path: '/games/UX Test Novel-1.0', language: 'en', mods: [] }] },
    { version: '1.2', variants: [{ path: '/games/UX Test Novel v1.2 (Spanish translation)', language: 'es', kind: 'translation', mods: [{ name: 'uncensor' }] }] },
  ],
};
const quest = {
  name: 'Zeta Quest', source: 'F95zone', engine: 'RPG Maker', version: '0.9', installed: false,
  updates: [{ version: '1.0' }], knownOn: [{ platform: 'f95', id: '123' }], versions: [], favourite: true,
};
const find = (text) => [novel, quest].filter((g) => Q.matches(g, Q.parse(text))).map((g) => g.name);

test('every word must match, in any order', () => {
  assert.deepEqual(find('novel 1.2'), ['UX Test Novel']);
  assert.deepEqual(find('1.2 novel'), ['UX Test Novel']);
  assert.deepEqual(find('novel quest'), []);
  assert.deepEqual(find(''), ['UX Test Novel', 'Zeta Quest']);
});

test('case, accents and punctuation are ignored', () => {
  assert.deepEqual(find('renpy'), ['UX Test Novel']);
  assert.deepEqual(find("REN'PY"), ['UX Test Novel']);
  assert.deepEqual(find('rpgmaker'), ['Zeta Quest']);
  assert.deepEqual(find('Zéta'), ['Zeta Quest']);
});

test('all versions, mods, languages and kinds are searched', () => {
  assert.deepEqual(find('1.0 novel'), ['UX Test Novel']);
  assert.deepEqual(find('uncensor'), ['UX Test Novel']);
  assert.deepEqual(find('spanish'), ['UX Test Novel']);
  assert.deepEqual(find('translation'), ['UX Test Novel']);
});

test('filters', () => {
  assert.deepEqual(find('source:f95'), ['Zeta Quest']);
  assert.deepEqual(find('source:"local folder"'), ['UX Test Novel']);
  assert.deepEqual(find('engine:renpy'), ['UX Test Novel']);
  assert.deepEqual(find('installed'), ['UX Test Novel']);
  assert.deepEqual(find('favourite'), ['Zeta Quest']);
  assert.deepEqual(find('favorite'), ['Zeta Quest']);
  assert.deepEqual(find('has:update'), ['Zeta Quest']);
  assert.deepEqual(find('has:nolink'), ['UX Test Novel']);
  assert.deepEqual(find('kind:translation'), ['UX Test Novel']);
  assert.deepEqual(find('kind:game'), ['UX Test Novel']);
  assert.deepEqual(find('installed quest'), []);
});

test('parse keeps words and filters apart', () => {
  const q = Q.parse('novel source:f95 installed "1.2"');
  assert.deepEqual(q.words, ['novel', '12']);
  assert.deepEqual(q.filters.map((f) => f.key), ['source', 'installed']);
});
