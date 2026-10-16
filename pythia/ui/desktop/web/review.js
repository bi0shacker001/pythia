/* Pythia web UI: one Review queue (UX review A18).

   What waits on the person was spread over three places, each with its own
   count: "Needs attention" (a Library filter), "Downloads & intake" (the
   intake queue) and Reconciliation (scan decisions and possible duplicates).
   One scan of the test library showed three numbers, and a folder waiting for
   a decision was counted in all three. This is the one queue they all read:
   sections by kind, and one count, the games affected, where a game counts
   once however many sections name it.

   A game is its game id. A scan decision names a folder: it counts as the game
   that folder is recorded under, or as the folder itself when it is recorded
   under none. A new download counts as its file.

   Plain script: window.PythiaReview in the page, module.exports under node. */
(function (root) {
  'use strict';

  function queue({ games = [], decisions = [], clusters = [], detected = [], conflicts = [] } = {}) {
    const byPath = new Map();
    for (const g of games) for (const v of g.variants || []) if (v.path) byPath.set(v.path, g.id);
    const affected = new Set();
    const decided = decisions.map((d) => {
      const gameId = byPath.get(d.path) || null;
      affected.add(gameId || `path:${d.path}`);
      return { ...d, gameId };
    });
    for (const c of clusters) for (const m of c.members || []) affected.add(m.game_id);
    const unidentified = games.filter((g) => g.unidentified);
    const updates = games.filter((g) => (g.updates || []).length);
    for (const g of [...unidentified, ...updates]) affected.add(g.id);
    for (const d of detected) affected.add(`file:${d.path}`);
    for (const c of conflicts) affected.add(c.game_id || `path:${c.path}`);
    return { decisions: decided, clusters, unidentified, updates, downloads: detected, conflicts, affected: affected.size };
  }

  const api = { queue };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.PythiaReview = api;
})(typeof window !== 'undefined' ? window : globalThis);
