/* Pythia web UI: the component library. One mechanism per job:
   - notice()           a warning or note with a tone (info, caution, danger)
   - confirm()/confirmDanger()/prompt()/infoSheet()  the one dialog card
   - flash()            a corner acknowledgement; feeds Recent
   - stateBlock()       loading, empty and error states, always with a next action
   - pageHeader()/buttonBar()/chip()/row()/facts()  the page anatomy
   - FilterModel        one query per list: text, facets with counts, sort + direction
   - menu()             a small context menu (the "..." of a page)
   Behaviour and tokens are documented in README.md. */
(function () {
  'use strict';

  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const el = (html) => { const t = document.createElement('template'); t.innerHTML = html.trim(); return t.content.firstElementChild; };
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

  // ---- Copy budgets (README "Copy"): warn in the console so a reviewer sees them, never cut silently.
  const BUDGET = { noticeTitle: 40, noticeBody: 90, label: 3 };
  function checkCopy(kind, text) {
    if (!text) return;
    if (kind === 'title' && text.length > BUDGET.noticeTitle) console.warn(`[copy] notice title over ${BUDGET.noticeTitle}: "${text}"`);
    if (kind === 'body' && text.length > BUDGET.noticeBody) console.warn(`[copy] notice body over ${BUDGET.noticeBody}: "${text}"`);
    if (kind === 'label' && text.trim().split(/\s+/).length > BUDGET.label) console.warn(`[copy] label over ${BUDGET.label} words: "${text}"`);
  }

  const TONE_WORD = { info: 'Note', caution: 'Caution', danger: 'Danger' };

  // ---- Notice -------------------------------------------------------------
  // tone: info | caution | danger. placement: row (default) | page.
  // title states the condition (<= 40), body the outcome (<= 90), action <= 3 words,
  // details go to the Info sheet. A notice cannot be dismissed: it is state.
  function notice({ tone = 'info', title, body, action, details, source, placement = 'row' }) {
    checkCopy('title', title); checkCopy('body', body); if (action) checkCopy('label', action.label);
    const node = el(`<div class="notice ${tone} ${placement}" role="status" aria-live="polite">
      <span class="glyph g-${tone}" aria-hidden="true"></span>
      <div class="text"><div class="title"><span class="visually-hidden">${TONE_WORD[tone]}, </span>${esc(title)}${source ? `<span class="source">from ${esc(source)}</span>` : ''}</div>${body ? `<div class="body">${esc(body)}</div>` : ''}</div>
    </div>`);
    if (action) {
      const b = el(`<button class="btn small" type="button">${esc(action.label)}</button>`);
      b.addEventListener('click', action.run);
      node.appendChild(b);
    }
    if (details) {
      const b = el(`<button class="btn small quiet" type="button" aria-label="Info: ${esc(title)}">Info</button>`);
      b.addEventListener('click', () => infoSheet({ title, details }));
      node.appendChild(b);
    }
    return node;
  }

  // ---- Dialogs ------------------------------------------------------------
  // One card. Safe choice first (left) and focused; the risky choice last, in the
  // danger fill on a danger dialog. Esc and the scrim mean Cancel. Focus returns
  // to the element that opened the dialog.
  let dialogDepth = 0;
  function openDialog({ cls = '', html, onOpen, onClose }) {
    const opener = document.activeElement;
    const scrim = el(`<div class="scrim"><div class="dialog ${cls}" role="dialog" aria-modal="true">${html}</div></div>`);
    const card = $('.dialog', scrim);
    const h = $('h2', card); if (h) { h.id = `dlg-title-${++dialogDepth}`; card.setAttribute('aria-labelledby', h.id); }
    let closed = false;
    const close = (result) => {
      if (closed) return; closed = true;
      scrim.remove();
      document.removeEventListener('keydown', onKey, true);
      if (opener && opener.focus) opener.focus();
      onClose(result);
    };
    const onKey = (e) => {
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(null); }
      if (e.key === 'Tab') {
        const f = $$('button, [href], input, select, textarea, summary', card).filter((x) => !x.disabled);
        if (!f.length) return;
        const first = f[0], last = f[f.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    };
    scrim.addEventListener('mousedown', (e) => { if (e.target === scrim) close(null); });
    document.addEventListener('keydown', onKey, true);
    document.body.appendChild(scrim);
    onOpen(card, close);
    return close;
  }

  const buttonsHtml = (cancel, proceed, proceedCls) =>
    `<div class="buttons wide"><button class="btn" type="button" data-cancel>${esc(cancel)}</button><button class="btn ${proceedCls}" type="button" data-proceed>${esc(proceed)}</button></div>`;

  // confirm: info or caution tone, no fill on the proceed button.
  function confirm({ tone = 'info', title, body, details, cancel = 'Cancel', proceed = 'Continue' }) {
    return new Promise((resolve) => {
      openDialog({
        cls: tone,
        html: `<div class="head"><span class="glyph g-${tone}" aria-hidden="true"></span><h2>${esc(title)}</h2></div>
          ${body ? `<div class="body">${esc(body)}</div>` : ''}
          ${details ? `<details class="details"><summary>Details</summary><pre>${esc(details)}</pre></details>` : ''}
          ${buttonsHtml(cancel, proceed, '')}`,
        onOpen: (card, close) => { $('[data-cancel]', card).focus(); $('[data-cancel]', card).onclick = () => close(false); $('[data-proceed]', card).onclick = () => close(true); },
        onClose: (r) => resolve(r === true),
      });
    });
  }

  // confirmDanger: Steam-shaped. Octagon glyph, outcome, a "We recommend <button>." line,
  // the affected items, Details, then Cancel (left, focused) and the risky button (right, danger fill).
  function confirmDanger({ title, body, recommendation, items = [], details, cancel = 'Cancel', proceed }) {
    return new Promise((resolve) => {
      openDialog({
        cls: 'danger',
        html: `<div class="head"><span class="glyph g-danger" aria-hidden="true"></span><h2>${esc(title)}</h2></div>
          <div class="body">${esc(body)}</div>
          ${recommendation ? `<div class="recommend">${esc(recommendation)}</div>` : ''}
          ${items.length ? `<ul class="list">${items.map((i) => `<li>${esc(i)}</li>`).join('')}</ul>` : ''}
          ${details ? `<details class="details"><summary>Details</summary><pre>${esc(details)}</pre></details>` : ''}
          ${buttonsHtml(cancel, proceed, 'danger')}`,
        onOpen: (card, close) => { $('[data-cancel]', card).focus(); $('[data-cancel]', card).onclick = () => close(false); $('[data-proceed]', card).onclick = () => close(true); },
        onClose: (r) => resolve(r === true),
      });
    });
  }

  // prompt: Steam's rename dialog. A title line, the field, small Cancel and Save on the right.
  // Enter saves (Ctrl+Enter when multiline), Esc cancels and restores the old value.
  // kind: plain | path | url | number. Returns the text, or null when cancelled.
  function prompt({ title, value = '', hint = '', kind = 'plain', multiline = false, save = 'Save', pick }) {
    return new Promise((resolve) => {
      const inputHtml = multiline
        ? `<textarea class="input" id="prompt-field">${esc(value)}</textarea>`
        : `<input class="input ${kind === 'path' ? 'mono' : ''}" id="prompt-field" type="${kind === 'number' ? 'number' : 'text'}" value="${esc(value)}" placeholder="${esc(hint)}" autocomplete="off">`;
      openDialog({
        cls: 'prompt',
        html: `<h2>${esc(title)}</h2>
          <div class="field"><label for="prompt-field" class="visually-hidden">${esc(title)}</label>${inputHtml}</div>
          <div class="buttons">${pick ? `<button class="btn" type="button" data-pick style="margin-right:auto">${esc(pick.label)}</button>` : ''}<button class="btn" type="button" data-cancel>Cancel</button><button class="btn primary" type="button" data-proceed>${esc(save)}</button></div>`,
        onOpen: (card, close) => {
          const field = $('#prompt-field', card);
          field.focus(); if (!multiline) field.select();
          field.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && (!multiline || e.ctrlKey)) { e.preventDefault(); close(field.value); }
          });
          $('[data-cancel]', card).onclick = () => close(null);
          $('[data-proceed]', card).onclick = () => close(field.value);
          if (pick) $('[data-pick]', card).onclick = () => pick.run((v) => { if (v) field.value = v; field.focus(); });
        },
        onClose: (r) => resolve(typeof r === 'string' ? r : null),
      });
    });
  }

  // infoSheet: the "why" and exact details, copyable.
  function infoSheet({ title, details }) {
    openDialog({
      cls: 'info',
      html: `<div class="head"><span class="glyph g-info" aria-hidden="true"></span><h2>${esc(title)}</h2></div>
        <pre class="body" style="white-space:pre-wrap;font-family:inherit">${esc(details)}</pre>
        <div class="buttons"><button class="btn" type="button" data-copy>Copy</button><button class="btn primary" type="button" data-cancel>Close</button></div>`,
      onOpen: (card, close) => {
        $('[data-cancel]', card).focus();
        $('[data-cancel]', card).onclick = () => close(null);
        $('[data-copy]', card).onclick = () => { navigator.clipboard?.writeText(details); flash('Copied'); };
      },
      onClose: () => {},
    });
  }

  // ---- Flash + Recent ------------------------------------------------------
  // An acknowledgement in the corner: past tense, no period ("Saved"). Stays at least
  // 4 s, takes no action, and is appended to Recent (the bell) unless private.
  const recent = [];
  const recentListeners = new Set();
  function flash(text, tone = 'info', { private: priv = false } = {}) {
    const host = $('#flashes');
    const node = el(`<div class="flash ${tone}" role="status" aria-live="polite"><span class="text">${esc(text)}</span></div>`);
    host.appendChild(node);
    setTimeout(() => node.remove(), 4000);
    if (!priv) {
      recent.unshift({ text, tone, at: new Date() });
      if (recent.length > 50) recent.pop();
      recentListeners.forEach((f) => f(recent));
    }
  }
  flash.recent = recent;
  flash.onRecent = (f) => recentListeners.add(f);

  // ---- Page anatomy --------------------------------------------------------
  function chip(label, tone = '') {
    const glyph = tone === 'caution' || tone === 'danger' ? `<span class="g-${tone}" aria-hidden="true"></span>` : '';
    return `<span class="chip ${tone}">${glyph}${esc(label)}</span>`;
  }

  function pageHeader({ plate, title, byline, chips = [] }) {
    return el(`<header class="page-header">${plate !== undefined ? `<div class="plate" aria-hidden="true">${plate}</div>` : ''}<div style="min-width:0"><h1>${esc(title)}</h1>${byline ? `<div class="byline">${esc(byline)}</div>` : ''}${chips.length ? `<div class="chips">${chips.map((c) => chip(c.label, c.tone)).join('')}</div>` : ''}</div></header>`);
  }

  // buttons: [{label, run, role: primary|secondary|danger, disabled, reason, menu:[...]}]
  // At most one primary; destructive actions go in the "..." menu after a rule.
  function buttonBar(buttons) {
    const bar = el('<div class="buttonbar" role="group"></div>');
    for (const b of buttons) {
      if (b.rule) { bar.appendChild(el('<span class="rule" aria-hidden="true"></span>')); continue; }
      checkCopy('label', b.label);
      const btn = el(`<button class="btn ${b.role === 'primary' ? 'primary' : b.role === 'danger' ? 'danger' : ''} ${b.icon ? 'icon' : ''}" type="button" ${b.ariaLabel ? `aria-label="${esc(b.ariaLabel)}"` : ''}>${esc(b.label)}${b.disabled && b.reason ? ` <span class="reason">· ${esc(b.reason)}</span>` : ''}</button>`);
      if (b.disabled) btn.disabled = true;
      if (b.menu) btn.addEventListener('click', () => menu(btn, b.menu));
      else if (b.run) btn.addEventListener('click', b.run);
      bar.appendChild(btn);
    }
    return bar;
  }

  // row: a place (chevron), a setting (value or control) or a fact. A row that does nothing is not drawn.
  function row({ plate, title, line2, value, chips: rowChips = [], trailing, run, selected, press = !!run }) {
    const node = el(`<${press ? 'button type="button"' : 'div'} class="row ${press ? 'press' : ''}" ${selected ? 'aria-selected="true"' : ''}>
      ${plate !== undefined ? `<span class="plate" aria-hidden="true">${plate}</span>` : '<span></span>'}
      <span style="min-width:0"><span class="title">${esc(title)}</span>${line2 ? `<span class="line2" style="display:block">${esc(line2)}</span>` : ''}</span>
      <span class="trailing">${rowChips.map((c) => chip(c.label, c.tone)).join('')}${value !== undefined ? `<span class="value">${esc(value)}</span>` : ''}${run && !trailing ? '<span class="chevron" aria-hidden="true">›</span>' : ''}</span>
    </${press ? 'button' : 'div'}>`);
    if (trailing) $('.trailing', node).appendChild(trailing);
    if (run) node.addEventListener('click', (e) => { if (e.target.closest('.trailing button, .trailing input')) return; run(e); });
    return node;
  }

  function facts(pairs) {
    return el(`<dl class="facts">${pairs.filter((p) => p[1] !== undefined && p[1] !== null && p[1] !== '').map(([k, v, mono]) => `<div class="k">${esc(k)}</div><div class="v ${mono ? 'mono' : ''}">${esc(v)}</div>`).join('')}</dl>`);
  }

  function section(label, body, more) {
    const node = el(`<section class="section"><h2 class="section-label">${esc(label)}</h2></section>`);
    if (more) { const m = el(`<button class="btn small quiet more" type="button">${esc(more.label)}</button>`); m.addEventListener('click', more.run); $('.section-label', node).appendChild(m); }
    if (body) node.appendChild(body);
    return node;
  }

  function rows(items) { const node = el('<div class="rows"></div>'); items.forEach((r) => node.appendChild(r)); return node; }

  // stateBlock: loading (skeletons), empty (title <= 32, one body line, a next action) and error (a caution notice).
  function stateBlock({ kind = 'empty', glyph = '', title, body, actions = [] }) {
    if (kind === 'loading') return el('<div class="rows" aria-busy="true"><div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div></div>');
    const node = el(`<div class="state">${glyph ? `<span class="glyph" aria-hidden="true">${glyph}</span>` : ''}<div class="title">${esc(title)}</div>${body ? `<div>${esc(body)}</div>` : ''}<div class="actions"></div></div>`);
    const host = $('.actions', node);
    actions.forEach((a) => { const b = el(`<button class="btn ${a.primary ? 'primary' : ''}" type="button">${esc(a.label)}</button>`); b.addEventListener('click', a.run); host.appendChild(b); });
    if (!actions.length) host.remove();
    return node;
  }

  function switchControl(on, onChange, label) {
    const b = el(`<button class="switch" type="button" role="switch" aria-checked="${on ? 'true' : 'false'}" aria-label="${esc(label)}"></button>`);
    b.addEventListener('click', () => { const next = b.getAttribute('aria-checked') !== 'true'; b.setAttribute('aria-checked', String(next)); onChange(next); });
    return b;
  }

  // ---- Menu ----------------------------------------------------------------
  function menu(anchor, items) {
    $$('.menu').forEach((m) => m.remove());
    const node = el(`<div class="menu" role="menu">${items.map((i, n) => i.rule ? '<hr>' : `<button type="button" role="menuitem" class="${i.danger ? 'danger-item' : ''}" data-i="${n}">${esc(i.label)}</button>`).join('')}</div>`);
    const r = anchor.getBoundingClientRect();
    node.style.top = `${r.bottom + 4}px`;
    node.style.left = `${Math.min(r.left, window.innerWidth - 220)}px`;
    document.body.appendChild(node);
    const close = () => { node.remove(); document.removeEventListener('mousedown', outside, true); document.removeEventListener('keydown', key, true); anchor.focus(); };
    const outside = (e) => { if (!node.contains(e.target)) close(); };
    const key = (e) => { if (e.key === 'Escape') { e.preventDefault(); close(); } };
    document.addEventListener('mousedown', outside, true);
    document.addEventListener('keydown', key, true);
    $$('button', node).forEach((b) => b.addEventListener('click', () => { const i = items[+b.dataset.i]; close(); i.run(); }));
    $('button', node)?.focus();
  }

  // ---- FilterModel -----------------------------------------------------------
  // One query per list. facetDefs: {key: {label, of: (item) => [values]}}.
  // Facets show only the values the list holds and how many each has; the sort
  // flips on the second pick; blanks sort last in both directions.
  class FilterModel {
    constructor({ facets, sorts, text = (i) => '' , storageKey }) {
      this.facets = facets; this.sorts = sorts; this.textOf = text; this.storageKey = storageKey;
      this.q = { text: '', chosen: {}, sort: Object.keys(sorts)[0], dir: 1 };
      try { const saved = storageKey && JSON.parse(localStorage.getItem(storageKey)); if (saved) this.q = { ...this.q, ...saved, text: '' }; } catch (e) { /* storage may be unavailable */ }
    }
    save() { try { if (this.storageKey) localStorage.setItem(this.storageKey, JSON.stringify({ chosen: this.q.chosen, sort: this.q.sort, dir: this.q.dir })); } catch (e) { /* ignore */ } }
    toggle(key, value) { const set = new Set(this.q.chosen[key] || []); set.has(value) ? set.delete(value) : set.add(value); this.q.chosen[key] = [...set]; if (!set.size) delete this.q.chosen[key]; this.save(); }
    setSort(key) { if (this.q.sort === key) this.q.dir = -this.q.dir; else { this.q.sort = key; this.q.dir = 1; } this.save(); }
    clear() { this.q.text = ''; this.q.chosen = {}; this.save(); }
    get active() { return !!this.q.text || Object.keys(this.q.chosen).length > 0; }
    apply(items) {
      const t = this.q.text.trim().toLowerCase();
      let out = items.filter((i) => {
        if (t && !this.textOf(i).toLowerCase().includes(t)) return false;
        for (const [key, values] of Object.entries(this.q.chosen)) {
          const have = this.facets[key].of(i);
          if (!values.some((v) => have.includes(v))) return false;
        }
        return true;
      });
      const s = this.sorts[this.q.sort];
      out.sort((a, b) => {
        const va = s(a), vb = s(b);
        const ba = va === null || va === undefined || va === '', bb = vb === null || vb === undefined || vb === '';
        if (ba && bb) return 0; if (ba) return 1; if (bb) return -1;
        return (typeof va === 'string' ? va.localeCompare(vb, undefined, { sensitivity: 'base' }) : va - vb) * this.q.dir;
      });
      return out;
    }
    counts(items, key) {
      const c = new Map();
      items.forEach((i) => this.facets[key].of(i).forEach((v) => c.set(v, (c.get(v) || 0) + 1)));
      return [...c.entries()].sort((a, b) => b[1] - a[1] || String(a[0]).localeCompare(String(b[0])));
    }
    // Draws the facet chips (values with counts), the sort chips and the "12 of 80" line.
    render(items, filtered, onChange) {
      const wrap = el('<div></div>');
      const facetsEl = el('<div class="facets" role="group" aria-label="Filters"></div>');
      for (const [key, def] of Object.entries(this.facets)) {
        const counts = this.counts(items, key);
        if (counts.length < 2 && !(this.q.chosen[key] || []).length) continue;
        facetsEl.appendChild(el(`<span class="muted" style="font-size:var(--t-small)">${esc(def.label)}</span>`));
        counts.forEach(([v, n]) => {
          const on = (this.q.chosen[key] || []).includes(v);
          const b = el(`<button class="facet" type="button" aria-pressed="${on}">${esc(def.name ? def.name(v) : v)} <span class="n">${n}</span></button>`);
          b.addEventListener('click', () => { this.toggle(key, v); onChange(); });
          facetsEl.appendChild(b);
        });
      }
      const sortEl = el(`<span class="muted" style="font-size:var(--t-small);margin-left:auto">Sort</span>`);
      facetsEl.appendChild(sortEl);
      for (const key of Object.keys(this.sorts)) {
        const on = this.q.sort === key;
        const b = el(`<button class="facet" type="button" aria-pressed="${on}">${esc(key)}${on ? (this.q.dir > 0 ? ' ↑' : ' ↓') : ''}</button>`);
        b.addEventListener('click', () => { this.setSort(key); onChange(); });
        facetsEl.appendChild(b);
      }
      wrap.appendChild(facetsEl);
      if (this.active) {
        const line = el(`<div class="filter-line" aria-live="polite"><span>${filtered.length} of ${items.length}</span></div>`);
        const clear = el('<button class="btn small quiet" type="button">Clear</button>');
        clear.addEventListener('click', () => { this.clear(); onChange(); });
        line.appendChild(clear);
        wrap.appendChild(line);
      }
      return wrap;
    }
  }

  // ---- Formatting (one formatter, so dates and counts read the same everywhere) ----
  const fmt = {
    date(iso) { if (!iso) return ''; const d = new Date(iso); if (isNaN(d)) return ''; return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: d.getFullYear() === new Date().getFullYear() ? undefined : 'numeric' }); },
    dateTime(iso) { if (!iso) return ''; const d = new Date(iso); if (isNaN(d)) return ''; return d.toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }); },
    time(d) { return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }); },
    n(n, one, many) { return `${n} ${n === 1 ? one : many ?? one + 's'}`; },
  };

  window.UI = { esc, el, $, $$, notice, confirm, confirmDanger, prompt, infoSheet, flash, chip, pageHeader, buttonBar, row, rows, facts, section, stateBlock, switchControl, menu, FilterModel, fmt };
})();
