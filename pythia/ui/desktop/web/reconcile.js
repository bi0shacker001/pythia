/* Pythia web UI: possible duplicates as clusters (UX review A16, V1 and V2).

   The backend finds pairs of records that may be one game (reconciliation.py);
   four folders of one novel gave six pairs, so Reconciliation asked six times.
   This joins the pairs that are one game by droidtop's rule into clusters, keeps
   pairs that are only alike apart (see sameGame), and says for
   each, in words rather than a percentage, why its records look like one game,
   what Pythia suggests, which record the others would fold into, and what moves:

   - "Combine as versions": the records are versions of one game from the same
     kind of source; the others' folders move into the one with most versions.
   - "Same game, another source": the records came from different sources (a
     store and a local folder); folding keeps every source's ids and ownership.

   Plain script: window.PythiaReconcile in the page, module.exports under node. */
(function (root) {
  'use strict';

  const norm = (s) => String(s || '').toLowerCase().replace(/[^\p{L}\p{N}]+/gu, '');

  // droidtop's rule (droidtop SPEC 7m, "What merges, and what only suggests"): records whose names are equal
  // once case and punctuation are dropped (droidtop's GameNaming.nameKey), or part folders side by side, are
  // one game, and that joins them into one cluster. Similar names (0.6 alike) only suggest, one pair at a time:
  // they never join clusters, or "Game" alike to "Alpha Game" and "Beta Game" would chain unrelated games
  // together through a common word (Droidtop/tracker#538 retest B2).
  const sameGame = (p) => p.reason === 'parts' || p.reason === 'same' || (norm(p.a.name) !== '' && norm(p.a.name) === norm(p.b.name));

  function clusters(pairs, isDismissed = () => false) {
    const parent = new Map();
    const find = (x) => { while (parent.get(x) !== x) { parent.set(x, parent.get(parent.get(x))); x = parent.get(x); } return x; };
    const join = (a, b) => { const ra = find(a), rb = find(b); if (ra !== rb) parent.set(rb, ra); };
    const members = new Map(); const pairsOf = new Map();
    const open = pairs.filter((p) => !isDismissed(p));
    for (const p of open) for (const m of [p.a, p.b]) { if (!parent.has(m.game_id)) parent.set(m.game_id, m.game_id); members.set(m.game_id, m); }
    const strong = open.filter(sameGame), alike = open.filter((p) => !sameGame(p));
    for (const p of strong) join(p.a.game_id, p.b.game_id);
    for (const p of strong) { const r = find(p.a.game_id); if (!pairsOf.has(r)) pairsOf.set(r, []); pairsOf.get(r).push(p); }
    const groups = new Map();
    for (const p of strong) for (const m of [p.a, p.b]) { const r = find(m.game_id); if (!groups.has(r)) groups.set(r, new Map()); groups.get(r).set(m.game_id, members.get(m.game_id)); }
    const out = [...groups.entries()].map(([r, list]) => describe([...list.values()], pairsOf.get(r) || []));
    // Each pair of alike names is its own suggestion: once for two records, or two clusters, however many
    // of their records are alike.
    const seen = new Set();
    for (const p of alike) {
      const ra = find(p.a.game_id), rb = find(p.b.game_id);
      if (ra === rb) continue;
      const key = [ra, rb].sort().join('|');
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(describe([p.a, p.b], [p]));
    }
    return out.sort((x, y) => x.name.localeCompare(y.name));
  }

  function describe(list, pairs) {
    list = [...list].sort((a, b) => (b.versions || []).length - (a.versions || []).length || (b.folders || 0) - (a.folders || 0) || String(a.name).localeCompare(String(b.name)));
    const target = list[0];
    const others = list.slice(1);
    const names = [...new Set(list.map((m) => m.name))];
    const parts = pairs.some((p) => p.reason === 'parts');
    const sourceSets = list.map((m) => [...(m.sources || []), ...(m.stores || [])].join('+'));
    const anotherSource = new Set(sourceSets).size > 1 && !parts;
    let reason;
    if (parts) reason = 'Their folders sit side by side under one parent folder.';
    else if (new Set(list.map((m) => norm(m.name))).size === 1) reason = `All are named ${names[0]}.`;
    else reason = `Their names are alike: ${names.join(', ')}.`;
    const versions = [...new Set(list.flatMap((m) => m.versions || []))].sort((x, y) => x.localeCompare(y, undefined, { numeric: true }));
    if (versions.length > 1) reason += ` Versions found: ${versions.join(', ')}.`;
    const what = (m) => ((m.versions || []).length ? `${m.name} ${(m.versions || []).join(', ')}` : m.name);
    return {
      id: list.map((m) => m.game_id).sort().join('+'),
      name: target.name,
      members: list,
      target: target.game_id,
      others: others.map((m) => m.game_id),
      reason,
      suggest: anotherSource ? 'source' : 'versions',
      moves: `${others.map(what).join('; ')} ${others.length === 1 ? 'moves' : 'move'} into ${target.name}${(target.versions || []).length ? ` (${target.versions.join(', ')})` : ''}.`,
    };
  }

  const api = { clusters };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.PythiaReconcile = api;
})(typeof window !== 'undefined' ? window : globalThis);
