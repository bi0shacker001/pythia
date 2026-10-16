/* Pythia web UI: the shared components (ported from the ui-overhaul branch and
   applied to the existing page). One mechanism per job:
   - notice()                                   a warning or note with a tone (info, caution, danger)
   - confirm()/confirmDanger()/prompt()/infoSheet()  the one dialog card (Steam-shaped)
   - flash()                                    a corner acknowledgement; feeds Recent
   - stateBlock()                               loading, empty, unavailable and error states, with a next action
   - fromResult()                               a backend result -> the state block for it
   - menu()                                     a small context menu (the "..." of a thing)
   - bindSwitch()                               the existing .toggle markup as a real switch
   - fmt                                        one formatter for dates and counts
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
      const b = el(`<button class="btn small ghost" type="button" aria-label="Info: ${esc(title)}">Info</button>`);
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

  // confirm: info or caution tone, no fill on the proceed button. `body` may hold a node.
  function confirm({ tone = 'info', title, body, node, details, cancel = 'Cancel', proceed = 'Continue' }) {
    return new Promise((resolve) => {
      openDialog({
        cls: tone,
        html: `<div class="head"><span class="glyph g-${tone}" aria-hidden="true"></span><h2>${esc(title)}</h2></div>
          ${body ? `<div class="body">${esc(body)}</div>` : ''}<div class="field-wrap" data-node></div>
          ${details ? `<details class="details"><summary>Details</summary><pre>${esc(details)}</pre></details>` : ''}
          ${buttonsHtml(cancel, proceed, '')}`,
        onOpen: (card, close) => {
          const slot = $('[data-node]', card); if (node) slot.appendChild(node); else slot.remove();
          $('[data-cancel]', card).focus(); $('[data-cancel]', card).onclick = () => close(false); $('[data-proceed]', card).onclick = () => close(true);
        },
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
          ${buttonsHtml(cancel, proceed, 'danger-fill')}`,
        onOpen: (card, close) => { $('[data-cancel]', card).focus(); $('[data-cancel]', card).onclick = () => close(false); $('[data-proceed]', card).onclick = () => close(true); },
        onClose: (r) => resolve(r === true),
      });
    });
  }

  // prompt: Steam's rename dialog. A title line, the field, small Cancel and Save on the right.
  // Enter saves (Ctrl+Enter when multiline), Esc cancels and restores the old value.
  // kind: plain | path | url | number | select (options: [{value, label}]). Returns the text, or null when cancelled.
  function prompt({ title, value = '', hint = '', kind = 'plain', multiline = false, save = 'Save', pick, options = [], body = '' }) {
    return new Promise((resolve) => {
      let inputHtml;
      if (multiline) inputHtml = `<textarea class="notes-area" id="prompt-field" style="min-height:140px">${esc(value)}</textarea>`;
      else if (kind === 'select') inputHtml = `<select class="field" id="prompt-field">${options.map((o) => `<option value="${esc(o.value)}" ${o.value === value ? 'selected' : ''}>${esc(o.label)}</option>`).join('')}</select>`;
      else inputHtml = `<input class="field ${kind === 'path' ? 'mono' : ''}" id="prompt-field" type="${kind === 'number' ? 'number' : 'text'}" value="${esc(value)}" placeholder="${esc(hint)}" autocomplete="off">`;
      openDialog({
        cls: 'prompt',
        html: `<h2>${esc(title)}</h2>${body ? `<div class="body" style="margin-top:6px">${esc(body)}</div>` : ''}
          <div class="field-wrap"><label for="prompt-field" class="visually-hidden">${esc(title)}</label>${inputHtml}</div>
          <div class="buttons">${pick ? `<button class="btn" type="button" data-pick style="margin-right:auto">${esc(pick.label)}</button>` : ''}<button class="btn" type="button" data-cancel>Cancel</button><button class="btn primary" type="button" data-proceed>${esc(save)}</button></div>`,
        onOpen: (card, close) => {
          const field = $('#prompt-field', card);
          field.focus(); if (!multiline && field.select) field.select();
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

  // infoSheet: the "why" and exact details, copyable. `node` draws structured content instead of text.
  function infoSheet({ title, details = '', node, wide = false }) {
    openDialog({
      cls: `info ${wide ? 'wide' : ''}`,
      html: `<div class="head"><span class="glyph g-info" aria-hidden="true"></span><h2>${esc(title)}</h2></div>
        ${node ? '<div class="body" data-node></div>' : `<pre class="body" style="white-space:pre-wrap;font-family:inherit;font-size:0.875rem;color:var(--text-primary)">${esc(details)}</pre>`}
        <div class="buttons">${details ? '<button class="btn" type="button" data-copy>Copy</button>' : ''}<button class="btn primary" type="button" data-cancel>Close</button></div>`,
      onOpen: (card, close) => {
        if (node) $('[data-node]', card).appendChild(node);
        $('[data-cancel]', card).focus();
        $('[data-cancel]', card).onclick = () => close(null);
        const copy = $('[data-copy]', card); if (copy) copy.onclick = () => { navigator.clipboard?.writeText(details); flash('Copied'); };
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

  // ---- State blocks ----------------------------------------------------------
  // kind: loading (skeletons) | empty | unavailable (no backend yet) | error (a reason and Retry).
  // Never a bare title, never an endless spinner; empty and unavailable name the next action.
  function stateBlock({ kind = 'empty', glyph, title, body, actions = [], details }) {
    if (kind === 'loading') return el('<div aria-busy="true" aria-label="Loading"><div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div></div>');
    const g = glyph !== undefined ? glyph : kind === 'error' ? '!' : kind === 'unavailable' ? 'i' : '';
    const node = el(`<div class="state-block ${kind}" role="status">${g ? `<span class="glyph" aria-hidden="true">${esc(g)}</span>` : ''}<div class="title">${esc(title)}</div>${body ? `<div class="body">${esc(body)}</div>` : ''}<div class="actions"></div></div>`);
    const host = $('.actions', node);
    actions.forEach((a) => { const b = el(`<button class="btn ${a.primary ? 'primary' : ''}" type="button">${esc(a.label)}</button>`); b.addEventListener('click', a.run); host.appendChild(b); });
    if (details) { const b = el('<button class="btn ghost" type="button">Info</button>'); b.addEventListener('click', () => infoSheet({ title, details })); host.appendChild(b); }
    if (!host.children.length) host.remove();
    return node;
  }

  // fromResult: a backend result ({status: unavailable|error, message}) -> its state block.
  // `retry` draws Retry on an error; `what` names the thing ("Save backups").
  function fromResult(result, { what = 'This', retry, actions = [] } = {}) {
    if (!result || typeof result !== 'object') return stateBlock({ kind: 'error', title: `${what} did not answer`, body: 'Pythia returned nothing for this page.', actions: retry ? [{ label: 'Retry', run: retry }] : [] });
    if (result.status === 'unavailable') return stateBlock({ kind: 'unavailable', title: result.title || 'Not built yet', body: result.message, actions });
    const acts = retry ? [{ label: 'Retry', primary: true, run: retry }, ...actions] : actions;
    return stateBlock({ kind: 'error', title: `${what} could not be loaded`, body: result.message || 'No reason was given.', actions: acts, details: result.slot ? `${result.slot}: ${result.message}` : undefined });
  }

  // ---- Switch: the existing .toggle markup as a real switch --------------------
  function bindSwitch(node, on, onChange, label) {
    node.setAttribute('role', 'switch');
    node.setAttribute('aria-checked', on ? 'true' : 'false');
    if (label) node.setAttribute('aria-label', label);
    node.classList.toggle('on', !!on);
    node.onclick = () => {
      if (node.disabled) return;
      const next = node.getAttribute('aria-checked') !== 'true';
      node.setAttribute('aria-checked', String(next)); node.classList.toggle('on', next);
      onChange(next, node);
    };
    return node;
  }

  // ---- Menu ----------------------------------------------------------------
  // `returnTo`: where focus goes when the menu closes (default: the anchor).
  function menu(anchor, items, { returnTo } = {}) {
    $$('.ctx-menu').forEach((m) => m.remove());
    const node = el(`<div class="ctx-menu" role="menu">${items.map((i, n) => i.rule ? '<hr>' : `<button type="button" role="menuitem" class="${i.danger ? 'danger-item' : ''}" data-i="${n}" ${i.disabled ? 'disabled' : ''}>${esc(i.label)}</button>`).join('')}</div>`);
    const r = anchor.getBoundingClientRect();
    node.style.top = `${Math.min(r.bottom + 4, window.innerHeight - 40)}px`;
    node.style.left = `${Math.min(r.left, window.innerWidth - 220)}px`;
    document.body.appendChild(node);
    const close = () => { node.remove(); document.removeEventListener('mousedown', outside, true); document.removeEventListener('keydown', key, true); (returnTo && returnTo.isConnected ? returnTo : anchor).focus(); };
    const outside = (e) => { if (!node.contains(e.target)) close(); };
    const key = (e) => { if (e.key === 'Escape') { e.preventDefault(); close(); } };
    document.addEventListener('mousedown', outside, true);
    document.addEventListener('keydown', key, true);
    $$('button', node).forEach((b) => b.addEventListener('click', () => { const i = items[+b.dataset.i]; close(); i.run(); }));
    $('button:not([disabled])', node)?.focus();
  }

  // ---- Formatting (one formatter, so dates and counts read the same everywhere) ----
  const fmt = {
    date(iso) { if (!iso) return ''; const d = new Date(iso); if (isNaN(d)) return ''; return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: d.getFullYear() === new Date().getFullYear() ? undefined : 'numeric' }); },
    dateTime(iso) { if (!iso) return ''; const d = new Date(iso); if (isNaN(d)) return ''; return d.toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }); },
    time(d) { d = d instanceof Date ? d : new Date(d); if (isNaN(d)) return ''; return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' }); },
    n(n, one, many) { return `${n} ${n === 1 ? one : many ?? one + 's'}`; },
    ago(iso) { if (!iso) return ''; const s = Math.max(0, (Date.now() - new Date(iso)) / 1000); if (s < 60) return `${Math.round(s)}s ago`; if (s < 3600) return `${Math.round(s / 60)}m ago`; if (s < 86400) return `${Math.round(s / 3600)}h ago`; return fmt.date(iso); },
  };

  window.UI = { esc, el, $, $$, notice, confirm, confirmDanger, prompt, infoSheet, flash, stateBlock, fromResult, bindSwitch, menu, fmt };
})();
