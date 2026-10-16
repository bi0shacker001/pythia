/* Pythia web UI: the library query (UX review A11, "query model").

   A query is words and filters. Every word must match, in any order, ignoring
   case, accents and punctuation ("renpy" finds "Ren'Py", "novel 1.2" finds
   "UX Test Novel" version 1.2). A word is looked for in the game's name, source,
   engine, notes, platforms and systems, and in every version, folder, mod,
   language and kind it has, not only the latest.

   Filters:
     source:<text>   the source contains it      engine:<text>  the engine contains it
     kind:<text>     a folder of that kind       installed      installed on this computer
     has:update      a newer version was seen    has:nolink     no store link recorded
   A filter value may be quoted: source:"local folder".

   Plain script: window.PythiaQuery in the page, module.exports under node
   (tests/web/query.test.mjs). */
(function (root) {
  'use strict';

  function norm(text) {
    return String(text == null ? '' : text)
      .toLowerCase()
      .normalize('NFKD')
      .replace(/[̀-ͯ]/g, '')
      .replace(/[^\p{L}\p{N}\s]+/gu, '');
  }

  const FILTERS = ['source', 'engine', 'kind', 'has'];
  const FLAGS = ['installed'];

  function parse(text) {
    const words = [];
    const filters = [];
    const re = /(\w+):"([^"]*)"|(\w+):(\S+)|"([^"]*)"|(\S+)/g;
    let m;
    while ((m = re.exec(String(text || '')))) {
      const key = (m[1] || m[3] || '').toLowerCase();
      const value = m[2] !== undefined ? m[2] : m[4];
      if (key && FILTERS.includes(key)) { filters.push({ key, value: norm(value) }); continue; }
      const word = m[5] !== undefined ? m[5] : m[0];
      if (!key && FLAGS.includes(word.toLowerCase())) { filters.push({ key: word.toLowerCase(), value: '' }); continue; }
      const n = norm(word);
      if (n.trim()) words.push(...n.split(/\s+/).filter(Boolean));
    }
    return { words, filters, empty: !words.length && !filters.length };
  }

  const variantsOf = (g) => (g.versions || []).flatMap((v) => v.variants || []);
  const modName = (m) => (m && typeof m === 'object' ? m.name : m);

  function haystack(g) {
    if (g.__haystack !== undefined) return g.__haystack;
    const parts = [g.name, g.source, g.engine, g.version, g.path, g.note, ...(g.platforms || []), ...(g.systems || [])];
    for (const v of g.versions || []) {
      parts.push(v.version);
      for (const x of v.variants || []) parts.push(x.path, x.language, x.kind, ...(x.mods || []).map(modName));
    }
    const text = norm(parts.filter(Boolean).join(' '));
    try { Object.defineProperty(g, '__haystack', { value: text, enumerable: false }); } catch (e) { /* frozen */ }
    return text;
  }

  function filterMatches(g, f) {
    if (f.key === 'source') return norm(g.source).includes(f.value);
    if (f.key === 'engine') return norm(g.engine).includes(f.value);
    if (f.key === 'kind') return variantsOf(g).some((x) => norm(x.kind || 'game').includes(f.value));
    if (f.key === 'installed') return !!g.installed;
    if (f.key === 'has') {
      if (f.value === 'update') return (g.updates || []).length > 0;
      if (f.value === 'nolink') return !(g.knownOn || []).length;
      return false;
    }
    return true;
  }

  function matches(g, q) {
    if (!q || q.empty) return true;
    if (!q.filters.every((f) => filterMatches(g, f))) return false;
    if (!q.words.length) return true;
    const text = haystack(g);
    // A word matches inside the text with or without its spaces ("renpy" in "ren py").
    const squashed = text.replace(/\s+/g, '');
    return q.words.every((w) => text.includes(w) || squashed.includes(w));
  }

  const api = { norm, parse, matches, haystack };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.PythiaQuery = api;
})(typeof window !== 'undefined' ? window : globalThis);
