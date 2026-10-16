/* Pythia web UI: ordering a game's versions (UX review A13; owner decision 3:
   droidtop's version model, docs/SPEC.md 1.3).

   compareLoose is droidtop's Versions.compareLoose (runtime-common), so both
   order versions alike: dotted parts compare as numbers where they are numbers
   (0.10 is newer than 0.9) and as text where they are not (0.8.3b after 0.8.3);
   a missing part is zero (1.2 is newer than 1.1.9); a version with no label is
   the oldest.

   A label orders only when it is a version number: digits first, an optional
   leading v, dotted parts, and at most a short letter suffix with digits
   (1.0b2, 2.0rc3, 0.8.09r1). "Final", "Ch.3", "Episode 4", "0.9-public" and
   "Remastered" do not, and when a game has one of them among its versions the
   order is unknown: Pythia says so and lists them, and the person sets Current,
   rather than guessing.

   Plain script: window.PythiaVersions in the page, module.exports under node. */
(function (root) {
  'use strict';

  function compareLoose(a, b) {
    a = String(a || ''); b = String(b || '');
    if (!a || !b) return a.length - b.length;
    const left = a.replace(/^v(?=\d)/i, '').split(/[._-]/);
    const right = b.replace(/^v(?=\d)/i, '').split(/[._-]/);
    for (let i = 0; i < Math.max(left.length, right.length); i++) {
      const l = left[i] !== undefined ? left[i] : '0';
      const r = right[i] !== undefined ? right[i] : '0';
      const ln = parseInt((l.match(/^\d+/) || ['0'])[0], 10);
      const rn = parseInt((r.match(/^\d+/) || ['0'])[0], 10);
      if (ln !== rn) return ln < rn ? -1 : 1;
      const lt = l.replace(/^\d+/, ''); const rt = r.replace(/^\d+/, '');
      if (lt !== rt) return lt < rt ? -1 : 1;
    }
    return 0;
  }

  const ORDERABLE = /^v?\d+(?:[._]\d+)*(?:[a-z]{1,3}\d*)?$/i;
  const orderable = (label) => !label || ORDERABLE.test(String(label).trim());

  // groups: [{ label, ... }] (label '' = no version). Returns the groups newest first when they
  // order, else as they were, with the labels that could not be placed.
  function order(groups) {
    const list = [...groups];
    const unknown = list.map((g) => g.label).filter((l) => !orderable(l));
    if (unknown.length && list.length > 1) return { ordered: false, list, latest: null, unknown };
    list.sort((x, y) => compareLoose(y.label, x.label));
    return { ordered: true, list, latest: list.length ? list[0].label || null : null, unknown: [] };
  }

  // A long path keeps its start and its end: /home/me/Gam…/Novel v1.2 (Spanish).
  function middleEllipsis(text, max = 64) {
    text = String(text || '');
    if (text.length <= max) return text;
    const keep = max - 1; const head = Math.ceil(keep * 0.4);
    return `${text.slice(0, head)}…${text.slice(text.length - (keep - head))}`;
  }

  const api = { compareLoose, orderable, order, middleEllipsis };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.PythiaVersions = api;
})(typeof window !== 'undefined' ? window : globalThis);
