/* Pythia web UI: where the D-pad moves focus in the handheld shell (docs/SPEC.md 2.1;
   UX review A5 and A7).

   nearest(items, from, direction) is the item nearest to `from` in that direction,
   measured edge to edge rather than centre to centre, so a narrow choice under a
   full-width button counts as below it (Droidtop/tracker#538 retest B1: Down from
   "‹ Settings" skipped every choice on Settings > Interface). Up and Down take any
   item on a later row, preferring the one straight below or above; Left and Right
   stay in the row. Each item only needs getBoundingClientRect().
*/
(function (root) {
  function nearest(items, from, direction) {
    const r = from.getBoundingClientRect();
    const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    const across = direction === 'left' || direction === 'right';
    let best = null, bestScore = Infinity;
    for (const x of items) {
      if (x === from) continue;
      const q = x.getBoundingClientRect(), qx = q.left + q.width / 2, qy = q.top + q.height / 2;
      const sameRow = q.top < r.bottom - 2 && q.bottom > r.top + 2;
      const valid = direction === 'left' ? sameRow && qx < cx
        : direction === 'right' ? sameRow && qx > cx
          : !sameRow && (direction === 'up' ? qy < cy : qy > cy);
      if (!valid) continue;
      const gapX = Math.max(0, q.left - r.right, r.left - q.right), gapY = Math.max(0, q.top - r.bottom, r.top - q.bottom);
      // The gap along the direction counts, the gap across it counts more; centres only break ties.
      const score = (across ? gapX + gapY * 2.2 : gapY + gapX * 2.2) + (Math.abs(qx - cx) + Math.abs(qy - cy)) / 1000;
      if (score < bestScore) { best = x; bestScore = score; }
    }
    return best;
  }

  const api = { nearest };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.PythiaPad = api;
})(typeof window !== 'undefined' ? window : globalThis);
