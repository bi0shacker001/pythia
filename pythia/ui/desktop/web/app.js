/* Pythia web UI: model, backend bridge, jobs, router and pages.
   Everything drawn here comes from pythia.ui.backend.Backend over QWebChannel.
   Nothing is invented: no sample rows, no stand-in art, no counts the backend
   did not report. A capability with no backend yet is either absent or an
   honest "not built yet" note (Computers page). See README.md. */
(function () {
  'use strict';
  const { esc, el, $, $$, notice, confirm, confirmDanger, prompt, infoSheet, flash, chip, pageHeader, buttonBar, row, rows, facts, section, stateBlock, switchControl, menu, FilterModel, fmt } = window.UI;

  // ---- Vocabulary: the backend's `kind` is a store or an engine. One name per thing. ----
  const SOURCE_KINDS = { steam: 'Steam', gog: 'GOG', egs: 'Epic Games', itch: 'itch.io', f95: 'F95zone' };
  const ENGINE_KINDS = { renpy: "Ren'Py", kirikiri: 'KiriKiri', unity: 'Unity', unreal: 'Unreal', godot: 'Godot', rpgmaker: 'RPG Maker', twine: 'Twine', august: 'AUGUST', buriko: 'BGI', catsystem2: 'CatSystem2', cmvs: 'CMVS', flash_air: 'Flash / AIR' };
  const PLATFORM_NAMES = { windows: 'Windows', linux: 'Linux', macos: 'macOS', mac: 'macOS', android: 'Android', 'windows-x86_64': 'Windows', 'windows-i686': 'Windows (32-bit)', 'linux-x86_64': 'Linux', 'linux-i686': 'Linux (32-bit)', 'linux-aarch64': 'Linux (ARM)', web: 'Browser' };
  const CATEGORY_NAMES = { library: 'Game source', engine: 'Engine', downloader: 'Downloader', manager: 'Launcher', metadata: 'Metadata', mod: 'Mods', patcher: 'Patcher', source: 'Source' };
  const EVENT_NAMES = { game_added: 'Added to the library', version_added: 'Version added', ownership_recorded: 'Ownership recorded', merged: 'Merged', renamed: 'Renamed', update_seen: 'Update seen' };
  const platformName = (p) => PLATFORM_NAMES[p] || p;
  const storeName = (p) => SOURCE_KINDS[p] || p;

  // ---- Model ----------------------------------------------------------------
  const model = { games: [], plugins: [], accounts: [], info: null, app: null, mySystem: null, merges: null, decisions: [], detected: [], ready: false };

  function adaptGame(raw) {
    const entries = raw.entries || [];
    const own = entries.find((e) => e.system_uuid === model.mySystem) || entries[0] || {};
    const name = own.canonical_name || '?';
    const variants = [];
    for (const g of own.versions || []) {
      // Onboarding writes the folder name as the version when it finds no version
      // token; that is not a version, so it is shown as none.
      const version = g.version && g.version !== name ? g.version : null;
      for (const v of g.variants || []) variants.push({ ...v, version });
    }
    const kinds = [...new Set(variants.map((v) => v.source?.kind).filter(Boolean))];
    const engineKind = kinds.find((k) => ENGINE_KINDS[k]);
    const sourceKind = kinds.find((k) => SOURCE_KINDS[k]);
    const unknown = kinds.includes('unknown') || (variants.length && !kinds.length);
    const installed = variants.some((v) => v.path);
    const ownedOn = (own.ownership || []).map((o) => o.platform);
    const knownOn = (own.platform_ids || []).map((p) => ({ platform: p.platform, id: p.id }));
    const updates = own.available_updates || [];
    const added = variants.map((v) => v.detected_at).filter(Boolean).sort()[0] || null;
    const attention = [];
    if (installed && unknown && !engineKind) attention.push({ label: 'Not identified', tone: 'caution' });
    if (updates.length) attention.push({ label: 'Update available', tone: 'accent' });
    const platforms = [...new Set(variants.flatMap((v) => v.platforms || []))];
    const systems = [...new Set(entries.map((e) => e.system_uuid))];
    return {
      id: raw.game_id, name, own, entries, variants, versions: own.versions || [],
      engine: engineKind ? ENGINE_KINDS[engineKind] : null,
      source: sourceKind ? SOURCE_KINDS[sourceKind] : (ownedOn.length ? storeName(ownedOn[0]) : null),
      installed, ownedOn, knownOn, updates, added, attention, platforms, systems,
      version: variants.map((v) => v.version).find(Boolean) || null,
      notes: own.notes || '', events: own.timeline_events || [], updatedAt: own.updated_at,
      mine: own.system_uuid === model.mySystem,
      search: [name, engineKind, sourceKind, ...variants.map((v) => v.path), ...variants.map((v) => v.version)].filter(Boolean).join(' '),
    };
  }

  const line2 = (g) => [g.engine, g.source, g.version ? `v${g.version}` : null, g.versions.length > 1 ? fmt.n(g.versions.length, 'version') : null].filter(Boolean).join(' · ') || (g.installed ? 'Installed' : 'Not installed');
  const plateGlyph = (g) => (g.installed ? (g.engine ? g.engine[0] : '•') : '○');

  // ---- Jobs: the backend's own operations, from the call to the completion signal ----
  const jobs = {
    list: [],
    start(kind, title, detail) { const j = { id: `${kind}-${Date.now()}`, kind, title, detail, state: 'running', log: [], at: new Date() }; this.list.unshift(j); if (this.list.length > 50) this.list.pop(); renderChrome(); if (route.page === 'downloads') render(); return j; },
    finish(kind, summary, ok = true) { const j = this.list.find((x) => x.kind === kind && x.state === 'running'); if (!j) return null; j.state = ok ? 'done' : 'failed'; j.summary = summary; j.endedAt = new Date(); renderChrome(); if (route.page === 'downloads') render(); return j; },
    log(line) { const j = this.list.find((x) => x.state === 'running'); if (j) j.log.push(line); if (route.page === 'downloads') render(); },
    running() { return this.list.filter((j) => j.state === 'running'); },
  };

  // ---- Backend bridge -----------------------------------------------------------
  let pythia = null;
  const call = (name, ...args) => new Promise((resolve) => { if (!pythia) return resolve(undefined); pythia[name](...args, resolve); });

  function connect() {
    if (!(window.qt && qt.webChannelTransport)) { renderNoBackend(); return; }
    new QWebChannel(qt.webChannelTransport, (channel) => {
      pythia = channel.objects.pythia;
      pythia.gamesChanged.connect(refreshGames);
      pythia.logLine.connect((line) => jobs.log(line));
      pythia.decisionNeeded.connect((preview) => { model.decisions.push(preview); flash(`${preview.name || preview.path} needs a decision`, 'caution'); renderChrome(); if (route.page === 'review') render(); });
      pythia.onboardComplete.connect((result) => { const ok = result.status === 'ok'; jobs.finish('onboard', ok ? `Added ${result.name || ''}`.trim() : result.message, ok); flash(ok ? 'Folder added' : `Not added: ${result.message}`, ok ? 'info' : 'caution'); });
      pythia.scanComplete.connect((results) => { const ok = results.filter((r) => r.status === 'ok').length; const bad = results.length - ok; jobs.finish('scan', `${fmt.n(ok, 'game')} found${bad ? `, ${bad} not identified` : ''}`); flash(`Scan finished: ${fmt.n(ok, 'game')} found`); });
      pythia.downloadDetected.connect((path) => { model.detected.unshift({ path, at: new Date() }); flash(`New file: ${path.split(/[\\/]/).pop()}`); renderChrome(); if (route.page === 'downloads') render(); });
      pythia.mergeCandidatesReady.connect((c) => { model.merges = c || []; renderChrome(); if (route.page === 'review' || route.page === 'home') render(); });
      pythia.mergeComplete.connect((r) => { flash(r.status === 'ok' ? 'Games merged' : `Not merged: ${r.message}`, r.status === 'ok' ? 'info' : 'caution'); model.merges = null; pythia.refresh_merge_candidates(); });
      pythia.accountSyncComplete.connect((pluginId, result) => { const ok = result.status === 'ok'; jobs.finish(`sync:${pluginId}`, ok ? `${result.linked} linked, ${result.created} new` : result.message, ok); flash(ok ? `Account synced: ${result.linked} linked, ${result.created} new` : `Sync failed: ${result.message}`, ok ? 'info' : 'caution'); });
      pythia.get_system_uuid((uuid) => {
        model.mySystem = uuid;
        Promise.all([call('get_system_info'), call('get_app_info'), call('get_all_plugins'), call('get_account_plugins')]).then(([info, app, plugins, accounts]) => {
          model.info = info; model.app = app; model.plugins = plugins || []; model.accounts = accounts || [];
          model.ready = true;
          refreshGames();
          pythia.refresh_merge_candidates();
        });
      });
    });
  }

  function refreshGames() {
    if (!pythia) return;
    model.games = (pythia.games || []).map(adaptGame);
    renderChrome();
    render();
  }

  function refreshPlugins() { return call('get_all_plugins').then((p) => { model.plugins = p || []; return call('get_account_plugins'); }).then((a) => { model.accounts = a || []; }); }

  // ---- Settings held by the page (Interface) ----------------------------------------
  const prefs = {
    get(k, d) { try { return localStorage.getItem(`pythia.${k}`) ?? d; } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(`pythia.${k}`, v); } catch (e) { /* ignore */ } },
  };
  function applyInterface() {
    const theme = prefs.get('theme', 'ink');
    document.documentElement.dataset.theme = theme;
    const scale = parseFloat(prefs.get('textScale', '1')) || 1;
    document.documentElement.style.setProperty('--text-scale', String(scale));
    document.documentElement.dataset.largeText = scale >= 1.3 ? '1' : '0';
    document.documentElement.dataset.motion = prefs.get('motion', 'on') === 'off' ? 'off' : 'on';
  }

  // ---- Router: #/page/arg --------------------------------------------------------------
  const route = { page: 'home', arg: null, tab: null };
  const navigate = (page, arg) => { location.hash = `#/${page}${arg ? `/${encodeURIComponent(arg)}` : ''}`; };
  function readRoute() {
    const m = location.hash.match(/^#\/([a-z]+)(?:\/([^/]+))?(?:\/([^/]+))?/);
    route.page = m ? m[1] : 'home'; route.arg = m && m[2] ? decodeURIComponent(m[2]) : null; route.tab = m && m[3] ? m[3] : null;
  }
  window.addEventListener('hashchange', () => { readRoute(); render(); $('#main').scrollTop = 0; });

  // ---- Chrome: sidebar counts, bell, page search ---------------------------------------
  const NAV = [
    ['home', 'Home', '⌂'], ['library', 'Library', '▦'], ['review', 'Review', '⇄'], ['downloads', 'Downloads', '⇩'],
    ['computers', 'Computers', '◫'], ['plugins', 'Plugins', '◇'], ['settings', 'Settings', '⚙'],
  ];
  function counts() {
    return {
      library: model.games.length || null,
      review: (model.decisions.length + (model.merges ? model.merges.filter((c) => !dismissed(c)).length : 0)) || null,
      downloads: (jobs.running().length + model.detected.length) || null,
      computers: null, plugins: null,
    };
  }
  function renderChrome() {
    const c = counts();
    const nav = $('#nav'); nav.innerHTML = '';
    NAV.forEach(([id, label, glyph]) => {
      const b = el(`<button class="nav-row" type="button" ${route.page === id || (route.page === 'game' && id === 'library') || (route.page === 'plugin' && id === 'plugins') ? 'aria-current="page"' : ''}><span class="glyph" aria-hidden="true">${glyph}</span><span>${label}</span>${c[id] ? `<span class="count" aria-label="${c[id]}">${c[id]}</span>` : ''}</button>`);
      b.addEventListener('click', () => navigate(id));
      nav.appendChild(b);
    });
    const foot = $('#sidebar-foot');
    foot.textContent = model.ready ? `${fmt.n(model.games.length, 'game')} · ${fmt.n(new Set(model.games.flatMap((g) => g.systems)).size || 1, 'computer')}` : '';
    const bell = $('#bell'); bell.setAttribute('aria-label', `Recent, ${fmt.n(flash.recent.length, 'notification')}`);
    $('#bell-count').textContent = flash.recent.length ? String(flash.recent.length) : '';
  }

  function renderNoBackend() {
    $('#main').replaceChildren(stateBlock({ kind: 'empty', glyph: '⚠', title: 'Not connected', body: 'This page needs Pythia running behind it. Start Pythia with --ui web.' }));
  }

  // ---- Pages ----------------------------------------------------------------------
  function render() {
    if (!pythia) return renderNoBackend();
    const main = $('#main');
    const page = el('<div class="page"></div>');
    const draw = { home: pageHome, library: pageLibrary, game: pageGame, review: pageReview, downloads: pageDownloads, computers: pageComputers, plugins: pagePlugins, plugin: pagePlugin, settings: pageSettings }[route.page] || pageHome;
    draw(page);
    main.replaceChildren(page);
    renderChrome();
    document.title = `Pythia`;
  }

  // -- Home: the focused game as the hero, then what needs you, what arrived, what you own but have not installed.
  function pageHome(page) {
    if (!model.games.length) {
      page.appendChild(pageHeader({ title: 'Home' }));
      page.appendChild(stateBlock({ glyph: '▦', title: 'Where are your games?', body: "They'll show up here once Pythia knows a folder.", actions: [{ label: 'Add a folder', primary: true, run: addFolder }, { label: 'Scan a library', run: scanLibrary }] }));
      return;
    }
    const lastId = prefs.get('lastGame', '');
    const recent = [...model.games].filter((g) => g.added).sort((a, b) => (b.added > a.added ? 1 : -1));
    const hero = model.games.find((g) => g.id === lastId) || recent[0] || model.games[0];
    const h = el(`<section class="hero" aria-label="Last opened"><div class="plate" aria-hidden="true">${plateGlyph(hero)}</div><div style="min-width:0"><h1>${esc(hero.name)}</h1><div class="caption">${esc(line2(hero))}${hero.added ? ` · Added ${fmt.date(hero.added)}` : ''}</div></div></section>`);
    $('div[style]', h).appendChild(buttonBar([{ label: 'Open', role: 'primary', run: () => navigate('game', hero.id) }, { label: 'Library', run: () => navigate('library') }]));
    page.appendChild(h);

    const attention = model.games.filter((g) => g.attention.length);
    const sections = [];
    if (attention.length || model.decisions.length || (model.merges && model.merges.filter((c) => !dismissed(c)).length)) {
      const items = [];
      model.decisions.forEach((d) => items.push(row({ plate: '?', title: d.name || d.path, line2: 'A scan found a folder that may be a game you already have', chips: [{ label: 'Needs a decision', tone: 'caution' }], run: () => navigate('review') })));
      const merges = model.merges ? model.merges.filter((c) => !dismissed(c)).length : 0;
      if (merges) items.push(row({ plate: '⇄', title: fmt.n(merges, 'possible duplicate'), line2: 'Two entries with the same or a similar name', run: () => navigate('review') }));
      attention.slice(0, 5).forEach((g) => items.push(gameRow(g)));
      sections.push(section('Needs attention', rows(items), attention.length > 5 ? { label: `All ${attention.length}`, run: () => { libraryFilter.q.chosen = { state: ['Needs attention'] }; libraryFilter.save(); navigate('library'); } } : null));
    }
    if (recent.length) sections.push(section('Recently added', rows(recent.slice(0, 6).map(gameRow)), { label: 'Library', run: () => navigate('library') }));
    const owned = model.games.filter((g) => !g.installed && g.ownedOn.length);
    if (owned.length) sections.push(section('Owned, not installed', rows(owned.slice(0, 6).map(gameRow)), owned.length > 6 ? { label: `All ${owned.length}`, run: () => { libraryFilter.q.chosen = { state: ['Not installed'] }; libraryFilter.save(); navigate('library'); } } : null));
    sections.forEach((s) => page.appendChild(s));
    const tiles = el('<div class="tiles"></div>');
    [['Library', model.games.length, 'library'], ['Review', counts().review || 0, 'review'], ['Downloads', jobs.running().length, 'downloads'], ['Computers', new Set(model.games.flatMap((g) => g.systems)).size || 1, 'computers']].forEach(([t, n, p]) => {
      const b = el(`<button class="tile" type="button"><span class="n">${n}</span><span class="title">${t}</span></button>`); b.addEventListener('click', () => navigate(p)); tiles.appendChild(b);
    });
    page.appendChild(section('Your library', tiles));
  }

  function gameRow(g, selected = false) {
    return row({ plate: plateGlyph(g), title: g.name, line2: line2(g), chips: g.attention.slice(0, 1), selected, run: () => navigate('game', g.id) });
  }

  // -- Library: one filter model, list or grid, the game page beside it on wide windows.
  const libraryFilter = new FilterModel({
    storageKey: 'pythia.library.filter',
    text: (g) => g.search,
    facets: {
      state: { label: 'State', of: (g) => [g.installed ? 'Installed' : 'Not installed', ...(g.attention.length ? ['Needs attention'] : []), ...(g.updates.length ? ['Update available'] : [])] },
      source: { label: 'Source', of: (g) => (g.source ? [g.source] : []) },
      engine: { label: 'Engine', of: (g) => (g.engine ? [g.engine] : []) },
      platform: { label: 'Platform', of: (g) => g.platforms.map(platformName) },
      computer: { label: 'Computer', of: (g) => g.systems.map((s) => (s === model.mySystem ? 'This computer' : `Computer ${s.slice(0, 8)}`)) },
    },
    sorts: { Name: (g) => g.name, Added: (g) => g.added, Updated: (g) => g.updatedAt },
  });

  function pageLibrary(page, selectedId = null) {
    page.appendChild(pageHeader({ title: 'Library', byline: `${fmt.n(model.games.length, 'game')}` }));
    page.appendChild(buttonBar([{ label: 'Add folder', role: 'primary', run: addFolder }, { label: 'Scan library', run: scanLibrary }]));
    if (!model.games.length) { page.appendChild(stateBlock({ glyph: '▦', title: 'No games yet', body: 'Add a game folder or scan a library folder.', actions: [{ label: 'Add a folder', primary: true, run: addFolder }] })); return; }
    const search = el(`<div class="search"><span class="glyph" aria-hidden="true">⌕</span><input class="input" id="library-search" type="search" placeholder="Game, engine, version or folder" aria-label="Search the library" value="${esc(libraryFilter.q.text)}"></div>`);
    page.appendChild(search);
    const body = el('<div></div>'); page.appendChild(body);
    const view = prefs.get('libraryView', 'list');
    const drawList = () => {
      const filtered = libraryFilter.apply(model.games);
      body.innerHTML = '';
      body.appendChild(libraryFilter.render(model.games, filtered, drawList));
      const toggle = el(`<div class="facets"><button class="facet" type="button" aria-pressed="${view === 'list'}" data-view="list">List</button><button class="facet" type="button" aria-pressed="${view === 'grid'}" data-view="grid">Grid</button></div>`);
      $$('[data-view]', toggle).forEach((b) => b.addEventListener('click', () => { prefs.set('libraryView', b.dataset.view); render(); }));
      body.appendChild(toggle);
      if (!filtered.length) { body.appendChild(stateBlock({ title: 'Nothing matches', body: 'Try fewer filters.', actions: [{ label: 'Clear filters', run: () => { libraryFilter.clear(); $('#library-search').value = ''; drawList(); } }] })); return; }
      const two = el(`<div class="two-pane"><div class="pane-list ${selectedId ? 'has-detail' : ''}"></div><div class="pane-detail"></div></div>`);
      const list = $('.pane-list', two);
      if (view === 'grid') {
        const grid = el('<div class="cards" role="list"></div>');
        filtered.forEach((g) => { const c = el(`<button class="card ${g.id === selectedId ? 'selected' : ''}" type="button" role="listitem"><span class="plate" aria-hidden="true">${plateGlyph(g)}</span><span class="title">${esc(g.name)}</span><span class="line2">${esc(line2(g))}</span>${g.attention.length ? `<span class="chips">${chip(g.attention[0].label, g.attention[0].tone)}</span>` : ''}</button>`); c.addEventListener('click', () => navigate('game', g.id)); grid.appendChild(c); });
        list.appendChild(grid);
      } else {
        list.appendChild(rows(filtered.map((g) => gameRow(g, g.id === selectedId))));
      }
      const detail = $('.pane-detail', two);
      const sel = selectedId && model.games.find((g) => g.id === selectedId);
      if (sel) gameDetail(detail, sel); else detail.appendChild(stateBlock({ title: 'Pick a game', body: 'Its page opens here.' }));
      body.appendChild(two);
    };
    drawList();
    $('#library-search', search).addEventListener('input', (e) => { libraryFilter.q.text = e.target.value; drawList(); });
  }

  // -- Game page: the thing's own header, the ButtonBar, then tabs of facts.
  function pageGame(page) {
    const g = model.games.find((x) => x.id === route.arg);
    if (!g) { page.appendChild(stateBlock({ title: 'Game not found', body: 'It may have been removed.', actions: [{ label: 'Library', run: () => navigate('library') }] })); return; }
    prefs.set('lastGame', g.id);
    pageLibrary(page, g.id);
  }

  function gameDetail(host, g) {
    const back = el('<button class="btn small quiet back" type="button" aria-label="Back to the list">‹ Library</button>'); back.addEventListener('click', () => navigate('library'));
    host.appendChild(back);
    host.appendChild(pageHeader({ plate: plateGlyph(g), title: g.name, byline: [line2(g), g.added ? `Added ${fmt.date(g.added)}` : null].filter(Boolean).join(' · '), chips: g.attention }));
    const bar = el('<div class="buttonbar"></div>'); host.appendChild(bar);
    const folder = g.variants.find((v) => v.path)?.path;
    const buttons = [];
    if (folder) buttons.push({ label: 'Open folder', run: () => pythia.open_path(folder) });
    if (folder) buttons.push({ label: 'Rescan', run: () => { jobs.start('onboard', `Rescan ${g.name}`, folder); pythia.start_onboard(folder); flash('Rescanning'); } });
    buttons.push({ label: '⋯', icon: true, ariaLabel: 'More', menu: [
      { label: 'Rename', run: async () => { const v = await prompt({ title: 'Rename game', value: g.name }); if (v && v.trim() && v.trim() !== g.name) { pythia.rename_game(g.id, v.trim()); flash('Renamed'); } } },
      { label: 'Find duplicates', run: () => navigate('review') },
      { rule: true },
      { label: 'Remove from library', danger: true, run: async () => {
        const ok = await confirmDanger({ title: `Remove ${g.name}?`, body: 'Its record leaves the library. Files on disk stay where they are.', recommendation: 'We recommend Cancel unless the entry is wrong.', proceed: 'Remove' });
        if (ok) { pythia.forget_game(g.id); flash('Removed from library'); navigate('library'); }
      } },
    ] });
    bar.replaceWith(buttonBar(buttons));
    // Managers are asked for after the page is drawn, so the list never waits on a plugin.
    call('find_manager_for_game', g.id).then((managers) => {
      if (!managers || !managers.length) return;
      const m = managers[0];
      const b = el(`<button class="btn primary" type="button">Open in ${esc(m.manager_name)}</button>`);
      if (m.available === false) { b.disabled = true; b.innerHTML += ' <span class="reason">· not installed</span>'; }
      b.addEventListener('click', async () => { if (await confirm({ title: `Open in ${m.manager_name}?`, body: `${m.manager_name} opens and shows ${g.name}.`, proceed: 'Open' })) { pythia.open_in_manager(g.id, m.manager_id); flash(`Opened in ${m.manager_name}`); } });
      $('.buttonbar', host).prepend(b);
    });

    const tabs = [['overview', 'Overview'], ['versions', 'Versions'], ['notes', 'Notes'], ['history', 'History']];
    const current = route.tab && tabs.some((t) => t[0] === route.tab) ? route.tab : 'overview';
    const tabBar = el(`<div class="tabs" role="tablist">${tabs.map(([id, label]) => `<button class="tab" type="button" role="tab" aria-selected="${id === current}" data-tab="${id}">${label}</button>`).join('')}</div>`);
    const pane = el('<div role="tabpanel"></div>');
    const drawTab = (id) => {
      $$('[data-tab]', tabBar).forEach((t) => t.setAttribute('aria-selected', String(t.dataset.tab === id)));
      pane.innerHTML = '';
      ({ overview: tabOverview, versions: tabVersions, notes: tabNotes, history: tabHistory })[id](pane, g);
    };
    $$('[data-tab]', tabBar).forEach((t) => t.addEventListener('click', () => { history.replaceState(null, '', `#/game/${encodeURIComponent(g.id)}/${t.dataset.tab}`); route.tab = t.dataset.tab; drawTab(t.dataset.tab); }));
    host.appendChild(tabBar); host.appendChild(pane); drawTab(current);
  }

  function tabOverview(pane, g) {
    const folder = g.variants.find((v) => v.path)?.path;
    pane.appendChild(facts([
      ['Engine', g.engine || (g.installed ? 'Not identified' : null)],
      ['Source', g.source],
      ['Version', g.version],
      ['Platforms', g.platforms.map(platformName).join(', ')],
      ['Folder', folder, true],
      ['Known on', g.knownOn.map((k) => `${storeName(k.platform)} (${k.id})`).join(', ')],
      ['Owned on', g.ownedOn.map(storeName).join(', ')],
      ['Added', fmt.date(g.added)],
      ['Computers', g.systems.map((s) => (s === model.mySystem ? 'This computer' : `Computer ${s.slice(0, 8)}`)).join(', ')],
    ]));
    if (g.installed && !g.engine) pane.prepend(notice({ tone: 'caution', title: 'Engine not identified', body: "Pythia can't tell what runs this folder yet.", details: `No engine plugin matched ${folder || 'this folder'}. Rescan after adding or enabling an engine plugin, or correct it once Pythia offers to.` }));
    const idRow = el(`<p class="muted" style="margin-top:var(--s3);font-size:var(--t-small)">Game id ${esc(g.id)}</p>`);
    const info = el('<button class="btn small quiet" type="button">Info</button>'); info.addEventListener('click', () => infoSheet({ title: g.name, details: JSON.stringify(g.own, null, 2) }));
    idRow.appendChild(info); pane.appendChild(idRow);
  }

  function tabVersions(pane, g) {
    if (g.updates.length) pane.appendChild(section('Available', rows(g.updates.map((u) => row({ plate: '↑', title: u.version || 'Newer version', line2: [u.source, u.platform, u.seen_at ? `Seen ${fmt.date(u.seen_at)}` : null].filter(Boolean).join(' · '), chips: [{ label: 'Update available', tone: 'accent' }] })))));
    if (!g.versions.length) { pane.appendChild(stateBlock({ title: 'Nothing installed', body: g.ownedOn.length ? `Owned on ${g.ownedOn.map(storeName).join(', ')}, not installed on this computer.` : 'No folder is known for this game.' })); }
    g.versions.forEach((group) => {
      const version = group.version && group.version !== g.name ? `v${group.version}` : 'No version number';
      const items = (group.variants || []).map((v) => row({ plate: '▸', title: [v.language ? `Language ${v.language}` : null, (v.mods || []).length ? `Mods: ${v.mods.map((m) => m.name).join(', ')}` : null].filter(Boolean).join(' · ') || 'Default', line2: v.path || '', value: (v.platforms || []).map(platformName).join(', '), run: v.path ? () => pythia.open_path(v.path) : null }));
      pane.appendChild(section(version, rows(items)));
    });
    const others = g.entries.filter((e) => e.system_uuid !== model.mySystem);
    if (others.length) pane.appendChild(section('On other computers', rows(others.map((e) => row({ plate: '◫', title: `Computer ${e.system_uuid.slice(0, 8)}`, line2: (e.versions || []).map((v) => (v.version && v.version !== e.canonical_name ? `v${v.version}` : 'no version number')).join(', ') || 'Not installed' })))));
    const vinfo = el('<p class="muted" style="font-size:var(--t-small)">Versions found on paired handhelds, and copying a version to another device, arrive with the agent integration.</p>');
    pane.appendChild(vinfo);
  }

  function tabNotes(pane, g) {
    if (!g.mine) { pane.appendChild(stateBlock({ title: 'Notes live on their computer', body: 'This record belongs to another computer, so notes are read only here.' })); if (g.notes) pane.appendChild(el(`<pre class="body" style="white-space:pre-wrap">${esc(g.notes)}</pre>`)); return; }
    const f = el(`<div class="field"><label for="notes">Notes</label><textarea class="input" id="notes">${esc(g.notes)}</textarea></div>`);
    pane.appendChild(f);
    pane.appendChild(buttonBar([{ label: 'Save', role: 'primary', run: () => { pythia.save_notes(g.id, $('#notes', f).value); flash('Saved'); } }]));
  }

  function tabHistory(pane, g) {
    if (!g.events.length) { pane.appendChild(stateBlock({ title: 'No history yet', body: 'Events are recorded as Pythia changes this record.' })); return; }
    pane.appendChild(rows([...g.events].reverse().map((e) => row({ plate: '•', title: EVENT_NAMES[e.type] || e.type, line2: e.detail || '', value: fmt.dateTime(e.timestamp) }))));
  }

  // -- Review: scan decisions and possible duplicates. Nothing merges without a confirm.
  const DISMISS_KEY = 'pythia.review.dismissed';
  const dismissedSet = () => { try { return new Set(JSON.parse(localStorage.getItem(DISMISS_KEY) || '[]')); } catch (e) { return new Set(); } };
  const pairKey = (c) => [c.a.game_id, c.b.game_id].sort().join('+');
  const dismissed = (c) => dismissedSet().has(pairKey(c));

  function pageReview(page) {
    page.appendChild(pageHeader({ title: 'Review', byline: 'Decisions only you can make. Nothing changes until you choose.' }));
    if (model.decisions.length) {
      const items = model.decisions.map((d, i) => {
        const wrap = el('<div></div>');
        const cands = d.candidates || {};
        wrap.appendChild(notice({ tone: 'caution', title: 'Is this a game you already have?', body: `${d.name || d.path}`, details: d.path }));
        const list = [];
        (cands.suggested || []).forEach((s) => list.push(row({ plate: '≈', title: `Same as ${s.canonical_name}`, line2: (s.versions || []).map((v) => v.version).filter(Boolean).join(', ') || 'Known game', run: () => { pythia.resolveDecision(d.path, { action: 'link', game_id: s.game_id }); model.decisions.splice(i, 1); flash('Linked'); render(); } })));
        list.push(row({ plate: '+', title: 'A new game', line2: 'Keep it as its own entry', run: () => { pythia.resolveDecision(d.path, { action: 'new' }); model.decisions.splice(i, 1); flash('Added as a new game'); render(); } }));
        wrap.appendChild(rows(list));
        return wrap;
      });
      const host = el('<div></div>'); items.forEach((i) => host.appendChild(i));
      page.appendChild(section('Scan decisions', host));
    }
    const merges = model.merges;
    if (merges === null) { page.appendChild(section('Possible duplicates', stateBlock({ kind: 'loading' }))); return; }
    const open = merges.filter((c) => !dismissed(c));
    if (!open.length && !model.decisions.length) { page.appendChild(stateBlock({ glyph: '✓', title: 'Nothing to review', body: 'Scan a folder and anything uncertain lands here.', actions: [{ label: 'Scan library', run: scanLibrary }] })); return; }
    if (!open.length) return;
    const host = el('<div></div>');
    open.forEach((c) => {
      const card = el(`<div class="rows" style="margin-bottom:var(--s3)"></div>`);
      // A candidate is reconciliation._summarize(): game_id, name, path, engine_kind, version_count, platforms.
      const side = (x, other) => row({ plate: ENGINE_KINDS[x.engine_kind]?.[0] || '•', title: x.name, line2: [ENGINE_KINDS[x.engine_kind] || SOURCE_KINDS[x.engine_kind] || null, x.version_count ? fmt.n(x.version_count, 'version') : null, x.path].filter(Boolean).join(' · '), trailing: (() => { const b = el('<button class="btn small" type="button">Keep this one</button>'); b.addEventListener('click', () => mergeInto(x, other)); return b; })() });
      card.appendChild(side(c.a, c.b)); card.appendChild(side(c.b, c.a));
      const foot = el(`<div class="row"><span></span><span class="muted" style="font-size:var(--t-small)">Similar names</span><span class="trailing"></span></div>`);
      const not = el('<button class="btn small quiet" type="button">Not the same</button>'); not.addEventListener('click', () => { const s = dismissedSet(); s.add(pairKey(c)); try { localStorage.setItem(DISMISS_KEY, JSON.stringify([...s])); } catch (e) { /* ignore */ } flash('Kept apart'); render(); });
      $('.trailing', foot).appendChild(not); card.appendChild(foot);
      host.appendChild(card);
    });
    page.appendChild(section(`Possible duplicates · ${open.length}`, host));
  }

  async function mergeInto(keep, fold) {
    const ok = await confirmDanger({ title: `Fold "${fold.name}" into "${keep.name}"?`, body: 'Its versions, store ids and ownership move over. The folded record is removed. Files stay where they are.', recommendation: 'We recommend Cancel unless both are the same game.', items: [`Keep: ${keep.path || keep.name}`, `Fold: ${fold.path || fold.name}`], proceed: 'Fold' });
    if (ok) pythia.merge_games(keep.game_id, fold.game_id);
  }

  // -- Downloads: jobs, a watched folder, detected files.
  let watcher = null;
  function pageDownloads(page) {
    page.appendChild(pageHeader({ title: 'Downloads', byline: 'What Pythia is doing, and files it noticed.' }));
    page.appendChild(buttonBar([{ label: 'Scan library', role: 'primary', run: scanLibrary }, { label: 'Add folder', run: addFolder }, watcher ? { label: 'Stop watching', run: () => { pythia.stop_watch(); jobs.finish('watch', 'Stopped'); watcher = null; flash('Stopped watching'); render(); } } : { label: 'Watch a folder', run: watchFolder }]));
    if (model.detected.length) {
      page.appendChild(section('New files', rows(model.detected.map((d, i) => row({ plate: '⇩', title: d.path.split(/[\\/]/).pop(), line2: d.path, value: fmt.time(d.at), trailing: (() => { const b = el('<button class="btn small" type="button">Add to library</button>'); b.addEventListener('click', () => { jobs.start('onboard', `Add ${d.path.split(/[\\/]/).pop()}`, d.path); pythia.start_onboard(d.path); model.detected.splice(i, 1); render(); }); return b; })() })))));
    }
    if (!jobs.list.length) { page.appendChild(stateBlock({ glyph: '⇩', title: 'Nothing running', body: 'Scans, folder adds and account syncs show here with their progress.', actions: [{ label: 'Scan library', primary: true, run: scanLibrary }] })); return; }
    const items = jobs.list.map((j) => {
      const r = row({ plate: j.state === 'running' ? '↻' : j.state === 'done' ? '✓' : '!', title: j.title, line2: j.state === 'running' ? (j.log[j.log.length - 1] || j.detail || 'Running') : (j.summary || j.detail || ''), value: fmt.time(j.at), chips: j.state === 'failed' ? [{ label: 'Failed', tone: 'caution' }] : j.state === 'running' ? [{ label: 'Running', tone: 'info' }] : [], run: j.log.length ? () => infoSheet({ title: j.title, details: j.log.join('\n') }) : null });
      return r;
    });
    page.appendChild(section('Jobs', rows(items)));
    if (jobs.running().length) page.appendChild(el('<div class="progress indeterminate" aria-hidden="true"><span></span></div>'));
  }

  // -- Computers: what the synced folder knows. Pairing, saves and versions are not built yet, and say so.
  function pageComputers(page) {
    page.appendChild(pageHeader({ title: 'Computers', byline: 'Every computer whose library shares your synced folder.' }));
    page.appendChild(notice({ tone: 'info', placement: 'page', title: 'Pairing and save sync are not built yet', body: 'Handhelds, saves and version copies arrive with the droidtop agent integration.', details: 'This page will list paired handhelds, each computer\'s games and apps, save copies per device with their archives, and the version each device has. Until the integration lands, Pythia shows only what the synced folder holds.' }));
    const bySystem = new Map();
    model.games.forEach((g) => g.entries.forEach((e) => { const s = bySystem.get(e.system_uuid) || { uuid: e.system_uuid, games: 0, installed: 0 }; s.games++; if ((e.versions || []).some((v) => (v.variants || []).some((x) => x.path))) s.installed++; bySystem.set(e.system_uuid, s); }));
    if (!bySystem.has(model.mySystem)) bySystem.set(model.mySystem, { uuid: model.mySystem, games: 0, installed: 0 });
    const items = [...bySystem.values()].sort((a, b) => (a.uuid === model.mySystem ? -1 : b.uuid === model.mySystem ? 1 : 0)).map((s) => row({ plate: s.uuid === model.mySystem ? '●' : '◫', title: s.uuid === model.mySystem ? 'This computer' : `Computer ${s.uuid.slice(0, 8)}`, line2: `${fmt.n(s.games, 'game')} · ${s.installed} installed`, value: s.uuid.slice(0, 8), run: () => { libraryFilter.q.chosen = { computer: [s.uuid === model.mySystem ? 'This computer' : `Computer ${s.uuid.slice(0, 8)}`] }; libraryFilter.save(); navigate('library'); } }));
    page.appendChild(section('Computers', rows(items)));
    if (model.info) {
      page.appendChild(section('Synced folder', rows([row({ plate: '▣', title: 'Library folder', line2: model.info.sync_folder, trailing: (() => { const b = el('<button class="btn small" type="button">Open folder</button>'); b.addEventListener('click', () => pythia.open_path(model.info.sync_folder)); return b; })() })])));
      page.appendChild(el('<p class="muted" style="font-size:var(--t-small)">Point your own sync tool (Syncthing, a cloud drive) at this folder on each computer; Pythia reads what the others wrote.</p>'));
    }
  }

  // -- Plugins: Installed only (Pythia has no store or lists yet, so none is drawn).
  const pluginFilter = new FilterModel({ storageKey: 'pythia.plugins.filter', text: (p) => `${p.display_name} ${p.id} ${p.author}`, facets: { kind: { label: 'Kind', of: (p) => (p.categories || []).map((c) => CATEGORY_NAMES[c] || c) }, state: { label: 'State', of: (p) => [p.enabled ? 'On' : 'Turned off'] }, origin: { label: 'Origin', of: (p) => [p.origin] } }, sorts: { Name: (p) => p.display_name } });
  const trustChip = (p) => (p.origin === 'Built-in' ? { label: 'Official', tone: '' } : { label: 'Unofficial', tone: 'caution' });

  function pagePlugins(page) {
    page.appendChild(pageHeader({ title: 'Plugins', byline: `${fmt.n(model.plugins.length, 'plugin')} installed` }));
    const search = el(`<div class="search"><span class="glyph" aria-hidden="true">⌕</span><input class="input" type="search" placeholder="Plugin name" aria-label="Search plugins" value="${esc(pluginFilter.q.text)}"></div>`);
    page.appendChild(search);
    const body = el('<div></div>'); page.appendChild(body);
    const draw = () => {
      const filtered = pluginFilter.apply(model.plugins);
      body.innerHTML = '';
      body.appendChild(pluginFilter.render(model.plugins, filtered, draw));
      if (!filtered.length) { body.appendChild(stateBlock({ title: 'No plugins match', actions: [{ label: 'Clear filters', run: () => { pluginFilter.clear(); draw(); } }] })); return; }
      body.appendChild(rows(filtered.map(pluginRow)));
    };
    draw();
    $('input', search).addEventListener('input', (e) => { pluginFilter.q.text = e.target.value; draw(); });
  }

  function pluginRow(p) {
    const sw = switchControl(p.enabled, (on) => { pythia.set_plugin_enabled(p.id, on); p.enabled = on; flash(on ? `${p.display_name} turned on` : `${p.display_name} turned off`); }, `${p.display_name} on`);
    return row({ plate: esc((p.display_name || '?').slice(0, 2).toUpperCase()), title: p.display_name, line2: `v${p.version} · ${(p.categories || []).map((c) => CATEGORY_NAMES[c] || c).join(', ')}`, chips: [trustChip(p), ...(p.enabled ? [] : [{ label: 'Turned off', tone: '' }])], trailing: sw, run: () => navigate('plugin', p.id) });
  }

  function pagePlugin(page) {
    const p = model.plugins.find((x) => x.id === route.arg);
    if (!p) { page.appendChild(stateBlock({ title: 'Plugin not found', actions: [{ label: 'Plugins', run: () => navigate('plugins') }] })); return; }
    const back = el('<button class="btn small quiet" type="button">‹ Plugins</button>'); back.addEventListener('click', () => navigate('plugins')); page.appendChild(back);
    page.appendChild(pageHeader({ plate: esc(p.display_name.slice(0, 2).toUpperCase()), title: p.display_name, byline: `${p.author} · v${p.version} · ${p.origin}`, chips: [trustChip(p), ...(p.enabled ? [] : [{ label: 'Turned off', tone: '' }])] }));
    if (p.origin !== 'Built-in') page.appendChild(notice({ tone: 'caution', placement: 'page', title: 'Unofficial plugin', body: 'It came from outside Pythia and runs with the same access as Pythia.', details: `Origin: ${p.origin}\nId: ${p.id}\nAuthor: ${p.author}` }));
    page.appendChild(buttonBar([{ label: p.enabled ? 'Turn off' : 'Turn on', role: p.enabled ? 'secondary' : 'primary', run: () => { pythia.set_plugin_enabled(p.id, !p.enabled); p.enabled = !p.enabled; flash(p.enabled ? 'Turned on' : 'Turned off'); render(); } }]));
    page.appendChild(section('What it adds', rows((p.categories || []).map((c) => row({ plate: '◇', title: CATEGORY_NAMES[c] || c })))));
    const account = model.accounts.find((a) => a.id === p.id);
    if (account) page.appendChild(section('Account', accountRows(account)));
    page.appendChild(section('Details', facts([['Id', p.id, true], ['Version', p.version], ['Author', p.author], ['Origin', p.origin]])));
  }

  // One account component, used by the plugin page and Settings > Accounts.
  function accountRows(a) {
    const items = [];
    items.push(row({ plate: a.connected ? '✓' : '○', title: a.display_name, line2: a.connected ? 'Connected' : 'Not connected', trailing: (() => {
      const wrap = el('<span class="trailing"></span>');
      if (a.connected) { const b = el('<button class="btn small" type="button">Sync now</button>'); b.addEventListener('click', () => { jobs.start(`sync:${a.id}`, `Sync ${a.display_name}`, 'Owned games'); pythia.sync_account(a.id); flash('Syncing'); }); wrap.appendChild(b); }
      return wrap;
    })() }));
    a.fields.forEach((f) => items.push(row({ plate: f.secret ? '•' : '＝', title: f.label, line2: f.secret ? 'Kept in your library folder' : '', trailing: (() => { const b = el(`<button class="btn small" type="button">${a.connected ? 'Change' : 'Enter'}</button>`); b.addEventListener('click', async () => { const v = await prompt({ title: `${a.display_name}: ${f.label}`, value: '', hint: f.secret ? 'Paste the key' : f.label }); if (v !== null && v.trim()) { pythia.set_account_credential(a.id, f.key, v.trim()); flash('Saved', 'info', { private: true }); refreshPlugins().then(render); } }); return b; })() })));
    return rows(items);
  }

  // -- Settings: categories and a pane. Configuration only; actions live on the pages.
  const SETTINGS = [['general', 'General'], ['accounts', 'Accounts'], ['interface', 'Interface'], ['advanced', 'Advanced']];
  function pageSettings(page) {
    page.appendChild(pageHeader({ title: 'Settings' }));
    const cat = SETTINGS.some((s) => s[0] === route.arg) ? route.arg : 'general';
    const layout = el('<div class="settings"><nav class="categories" aria-label="Settings categories"></nav><div class="pane"></div></div>');
    SETTINGS.forEach(([id, label]) => { const b = el(`<button class="nav-row" type="button" ${id === cat ? 'aria-current="page"' : ''}>${label}</button>`); b.addEventListener('click', () => navigate('settings', id)); $('.categories', layout).appendChild(b); });
    const pane = $('.pane', layout);
    ({ general: settingsGeneral, accounts: settingsAccounts, interface: settingsInterface, advanced: settingsAdvanced })[cat](pane);
    page.appendChild(layout);
  }

  const openRow = (title, path) => row({ plate: '▣', title, line2: path, trailing: (() => { const b = el('<button class="btn small" type="button">Open folder</button>'); b.addEventListener('click', () => pythia.open_path(path)); return b; })() });

  function settingsGeneral(pane) {
    if (!model.info) return;
    pane.appendChild(section('Folders', rows([openRow('App data', model.info.app_data_folder), openRow('Library folder (synced)', model.info.sync_folder)])));
    const modeSel = el(`<select class="input" style="width:auto" aria-label="Interface on start"><option value="">Choose by screen size</option><option value="desktop">Desktop</option><option value="bigpicture">Big picture</option></select>`);
    call('get_ui_mode').then((m) => { modeSel.value = m || ''; });
    modeSel.addEventListener('change', () => { pythia.set_ui_mode(modeSel.value); flash('Saved'); });
    pane.appendChild(section('Start', rows([row({ plate: '▭', title: 'Interface on start', line2: 'The web interface is reached with --ui web', trailing: modeSel })])));
  }

  function settingsAccounts(pane) {
    if (!model.accounts.length) { pane.appendChild(stateBlock({ title: 'No accounts to connect', body: 'Game-source plugins with accounts show here.' })); return; }
    model.accounts.forEach((a) => pane.appendChild(section(a.display_name, accountRows(a))));
    pane.appendChild(notice({ tone: 'caution', title: 'Keys are kept in the synced folder', body: 'Every computer that syncs the library folder can read them.', details: 'Pythia stores account keys under the library folder\'s credentials/ directory in plain text so a sign-in is done once for every synced computer. Encryption at rest is planned.' }));
  }

  function settingsInterface(pane) {
    const themeSel = el(`<select class="input" style="width:auto" aria-label="Theme"><option value="ink">Ink &amp; Amber (dark)</option><option value="parchment">Parchment (light)</option><option value="cobalt">Slate &amp; Cobalt (dark)</option></select>`);
    themeSel.value = prefs.get('theme', 'ink'); themeSel.addEventListener('change', () => { prefs.set('theme', themeSel.value); applyInterface(); });
    const sizeSel = el(`<select class="input" style="width:auto" aria-label="Text size"><option value="1">Normal</option><option value="1.15">Larger</option><option value="1.3">Large</option><option value="1.5">Largest</option></select>`);
    sizeSel.value = prefs.get('textScale', '1'); sizeSel.addEventListener('change', () => { prefs.set('textScale', sizeSel.value); applyInterface(); });
    const motion = switchControl(prefs.get('motion', 'on') === 'off', (on) => { prefs.set('motion', on ? 'off' : 'on'); applyInterface(); }, 'Reduce motion');
    const viewSel = el(`<select class="input" style="width:auto" aria-label="Library view"><option value="list">List</option><option value="grid">Grid</option></select>`);
    viewSel.value = prefs.get('libraryView', 'list'); viewSel.addEventListener('change', () => prefs.set('libraryView', viewSel.value));
    pane.appendChild(section('Look', rows([row({ plate: '◐', title: 'Theme', trailing: themeSel }), row({ plate: 'Aa', title: 'Text size', trailing: sizeSel }), row({ plate: '≋', title: 'Reduce motion', trailing: motion }), row({ plate: '▦', title: 'Library view', trailing: viewSel })])));
  }

  function settingsAdvanced(pane) {
    const info = model.info || {}; const app = model.app || {};
    pane.appendChild(section('This computer', facts([['Computer id', info.system_uuid, true], ['Pythia', app.version], ['Platform', app.platform], ['Python', app.python]])));
    pane.appendChild(section('Index', rows([row({ plate: '↻', title: 'Rebuild index', line2: 'Reads every record in the library folder again', trailing: (() => { const b = el('<button class="btn small" type="button">Rebuild</button>'); b.addEventListener('click', () => { pythia.rebuild_index(); flash('Index rebuilt'); }); return b; })() })])));
  }

  // ---- Actions shared by pages ------------------------------------------------------------
  const pickFolder = (title) => (done) => call('pick_folder', title).then(done);
  async function addFolder() {
    const path = await prompt({ title: 'Add a game folder', hint: 'C:\\Games\\Example', kind: 'path', save: 'Add', pick: { label: 'Browse', run: pickFolder('Add a game folder') } });
    if (!path || !path.trim()) return;
    jobs.start('onboard', `Add ${path.trim().split(/[\\/]/).pop()}`, path.trim());
    pythia.start_onboard(path.trim());
    flash('Adding folder');
    navigate('downloads');
  }
  async function scanLibrary() {
    const path = await prompt({ title: 'Scan a library folder', hint: 'C:\\Games', kind: 'path', save: 'Scan', pick: { label: 'Browse', run: pickFolder('Scan a library folder') } });
    if (!path || !path.trim()) return;
    jobs.start('scan', `Scan ${path.trim()}`, 'Finds every game folder inside');
    pythia.start_scan(path.trim());
    flash('Scan started');
    navigate('downloads');
  }
  async function watchFolder() {
    const folder = await prompt({ title: 'Watch a download folder', hint: 'C:\\Users\\you\\Downloads', kind: 'path', save: 'Next', pick: { label: 'Browse', run: pickFolder('Folder to watch') } });
    if (!folder || !folder.trim()) return;
    const program = await prompt({ title: 'Program to run while watching', hint: 'Leave empty to only watch', kind: 'path', save: 'Watch', pick: { label: 'Browse', run: (done) => call('pick_file', 'Program to run').then(done) } });
    if (program === null) return;
    watcher = { folder: folder.trim(), program: program.trim() };
    jobs.start('watch', `Watching ${watcher.folder}`, watcher.program ? `Running ${watcher.program}` : 'New files show under New files');
    pythia.start_watch(watcher.program, watcher.folder);
    flash('Watching');
    render();
  }

  // ---- Boot --------------------------------------------------------------------------------
  // A read-only view of the model for the screenshot driver and the console; never written to.
  window.pythiaDebug = () => ({ ready: model.ready, games: model.games.length, merges: model.merges === null ? null : model.merges.length, decisions: model.decisions.length, jobs: jobs.list.length });
  function boot() {
    applyInterface();
    readRoute();
    $('#bell').addEventListener('click', (e) => {
      e.stopPropagation();
      const existing = $('.popover'); if (existing) { existing.remove(); return; }
      const pop = el('<div class="popover" role="dialog" aria-label="Recent"></div>');
      if (!flash.recent.length) pop.appendChild(stateBlock({ title: 'Nothing recent', body: 'Acknowledgements show here for a while.' }));
      else flash.recent.forEach((r) => pop.appendChild(row({ title: r.text, value: fmt.time(r.at) })));
      document.body.appendChild(pop);
      const close = (ev) => { if (!pop.contains(ev.target)) { pop.remove(); document.removeEventListener('mousedown', close); } };
      setTimeout(() => document.addEventListener('mousedown', close), 0);
    });
    $('#settings-button').addEventListener('click', () => navigate('settings'));
    $('#top-search').addEventListener('keydown', (e) => { if (e.key === 'Enter') { libraryFilter.q.text = e.target.value; e.target.value = ''; navigate('library'); render(); } });
    document.addEventListener('keydown', (e) => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); $('#top-search').focus(); } });
    renderChrome();
    connect();
  }
  boot();
})();
