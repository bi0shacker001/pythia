/* Pythia web UI: the error console (testing aid). Off by default; on with
   `--error-console` or `error_console = True` in pythia.conf (Backend.get_ui_config).
   It shows ONE log live: Backend's uiLog, which is Pythia's own logger (every
   warning and error, failed slot calls with their redacted arguments, progress
   lines) plus the page's own errors, which this file sends to
   Backend.report_web_error so they travel the same path. Entries: time, level,
   source (python | pythia | agent | web), the slot when one is involved, the message.
   Controls: level and source filters, Clear, Copy, dock bottom or right.
   Ctrl+Shift+E toggles it; it works over the desktop and the handheld shell. */
(function () {
  'use strict';
  const { esc, el, $, $$, flash } = window.UI;

  const LEVEL_RANK = { debug: 0, info: 1, warn: 2, error: 3 };
  const store = { entries: [], enabled: false, open: false, level: 'warn', source: 'all', dock: 'bottom' };
  let bridge = null;
  let panel = null;
  const prefs = {
    get(k, d) { try { return localStorage.getItem(`pythia-console-${k}`) ?? d; } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(`pythia-console-${k}`, v); } catch (e) { /* storage may be unavailable */ } },
  };

  function add(entry) {
    store.entries.push(entry);
    if (store.entries.length > 1000) store.entries.splice(0, store.entries.length - 1000);
    if (panel && store.open) drawLines();
    updateBadge();
  }

  // The page's own errors go to Pythia's log (and come back through uiLog);
  // without a bridge they stay local so the console still shows them.
  function reportWeb(level, message, context) {
    if (bridge && bridge.report_web_error) bridge.report_web_error(level, message, context || '', () => {});
    else add({ ts: new Date().toISOString(), level, source: 'web', slot: null, message: context ? `${message} (${context})` : message });
  }

  function captureWebErrors() {
    window.addEventListener('error', (e) => {
      const where = e.filename ? `${e.filename.split('/').pop()}:${e.lineno}` : '';
      reportWeb('error', e.message || String(e.error || 'Script error'), where);
    });
    window.addEventListener('unhandledrejection', (e) => {
      const r = e.reason;
      reportWeb('error', `Unhandled rejection: ${r && r.message ? r.message : String(r)}`, r && r.stack ? r.stack.split('\n')[1]?.trim() : '');
    });
    const origError = console.error.bind(console);
    console.error = (...args) => { origError(...args); reportWeb('error', args.map((a) => (a && a.stack) || (typeof a === 'object' ? JSON.stringify(a) : String(a))).join(' '), 'console.error'); };
    const origWarn = console.warn.bind(console);
    console.warn = (...args) => { origWarn(...args); reportWeb('warn', args.map((a) => String(a)).join(' '), 'console.warn'); };
  }

  function visible() {
    return store.entries.filter((e) => (store.level === 'all' || (LEVEL_RANK[e.level] ?? 1) >= LEVEL_RANK[store.level]) && (store.source === 'all' || e.source === store.source));
  }

  function lineText(e) {
    return `${e.ts} ${e.level.toUpperCase().padEnd(5)} ${e.source.padEnd(6)} ${e.slot ? e.slot + ': ' : ''}${e.message}`;
  }

  function drawLines() {
    const body = $('.console-body', panel);
    const list = visible();
    const atBottom = body.scrollTop + body.clientHeight >= body.scrollHeight - 8;
    body.innerHTML = list.length
      ? list.slice(-400).map((e) => `<div class="console-line ${esc(e.level)}"><time>${esc(new Date(e.ts).toLocaleTimeString(undefined, { hour12: false }))}</time><span class="lvl">${esc(e.level)}</span><span class="src">${esc(e.source)}</span><span class="msg">${e.slot ? `<span class="slot">${esc(e.slot)}</span>` : ''}${esc(e.message)}</span></div>`).join('')
      : `<div class="console-empty">Nothing at this level yet. ${store.entries.length ? `${store.entries.length} entries are hidden by the filters.` : 'Failures and warnings show here as they happen.'}</div>`;
    $('.console-count', panel).textContent = `${list.length} of ${store.entries.length}`;
    if (atBottom) body.scrollTop = body.scrollHeight;
  }

  function updateBadge() {
    const n = store.entries.filter((e) => e.level === 'error' || e.level === 'warn').length;
    $$('[data-console-toggle] .utility-count').forEach((b) => { b.textContent = n ? String(n) : ''; b.classList.toggle('hidden', !n); });
  }

  function build() {
    panel = el(`<section class="console-panel hidden dock-bottom" aria-label="Error console">
      <div class="console-head">
        <strong>Error console</strong>
        <label>Level <select data-level><option value="all">All</option><option value="info">Info and up</option><option value="warn" selected>Warnings and errors</option><option value="error">Errors</option></select></label>
        <label>Source <select data-source><option value="all">All</option><option value="python">Python</option><option value="pythia">Progress</option><option value="agent">Handhelds (agent)</option><option value="web">Web page</option></select></label>
        <span class="console-count"></span>
        <span class="spacer"></span>
        <button class="btn small" type="button" data-copy>Copy</button>
        <button class="btn small" type="button" data-clear>Clear</button>
        <button class="btn small" type="button" data-dock title="Dock on the right or at the bottom">Dock</button>
        <button class="btn small" type="button" data-close aria-label="Close the console">×</button>
      </div>
      <div class="console-body" role="log" aria-live="polite"></div>
    </section>`);
    document.body.appendChild(panel);
    $('[data-level]', panel).value = store.level;
    $('[data-source]', panel).value = store.source;
    $('[data-level]', panel).onchange = (e) => { store.level = e.target.value; prefs.set('level', store.level); drawLines(); };
    $('[data-source]', panel).onchange = (e) => { store.source = e.target.value; prefs.set('source', store.source); drawLines(); };
    $('[data-copy]', panel).onclick = () => { const text = visible().map(lineText).join('\n'); navigator.clipboard?.writeText(text); flash(`Copied ${visible().length} lines`, 'info', { private: true }); };
    $('[data-clear]', panel).onclick = () => { store.entries = []; if (bridge && bridge.clear_ui_log) bridge.clear_ui_log(() => {}); drawLines(); updateBadge(); };
    $('[data-dock]', panel).onclick = () => setDock(store.dock === 'bottom' ? 'right' : 'bottom');
    $('[data-close]', panel).onclick = () => setOpen(false);
    setDock(store.dock);
  }

  function setDock(dock) {
    store.dock = dock; prefs.set('dock', dock);
    panel.classList.toggle('dock-bottom', dock === 'bottom');
    panel.classList.toggle('dock-right', dock === 'right');
    document.body.classList.toggle('console-bottom', dock === 'bottom');
  }

  function setOpen(open) {
    if (!store.enabled) return;
    store.open = open; prefs.set('open', open ? '1' : '0');
    panel.classList.toggle('hidden', !open);
    document.body.classList.toggle('console-open', open);
    if (open) drawLines();
  }

  // enable(): called once the backend says the console is on. The existing entries
  // come from get_ui_log; everything after arrives through uiLog.
  function enable(b) {
    bridge = b;
    store.enabled = true;
    $$('[data-console-toggle]').forEach((x) => x.classList.remove('hidden'));
    if (bridge) {
      bridge.uiLog.connect((e) => add(e));
      bridge.get_ui_log((r) => { if (r && r.status === 'ok') { store.entries = (r.entries || []).concat(store.entries); if (store.open) drawLines(); updateBadge(); } });
    }
    if (prefs.get('open', '0') === '1') setOpen(true);
  }

  function init() {
    store.level = prefs.get('level', 'warn'); store.source = prefs.get('source', 'all'); store.dock = prefs.get('dock', 'bottom');
    build();
    captureWebErrors();
    $$('[data-console-toggle]').forEach((x) => x.addEventListener('click', () => setOpen(!store.open)));
    document.addEventListener('keydown', (e) => { if (e.ctrlKey && e.shiftKey && e.key.toLowerCase() === 'e') { e.preventDefault(); setOpen(!store.open); } });
  }

  window.ErrorConsole = { init, enable, add, setOpen, reportWeb, get entries() { return store.entries; } };
})();
