/* Pythia web UI: the page logic (moved out of index.html's inline script on
   2026-10-14 and wired to pythia.ui.backend.Backend over QWebChannel).
   Every page reads the backend through one path (call -> result -> draw);
   a page whose feature has no backend yet still calls its slot and draws the
   "unavailable" answer, so when the slot gains a body nothing here changes.
   Nothing is sampled or invented: no placeholder rows, no counts the backend
   did not report, no toast that claims work was done. See README.md. */
(() => {
'use strict';
const { esc, el, $, $$, notice, confirm, confirmDanger, prompt, infoSheet, flash, stateBlock, fromResult, bindSwitch, menu, fmt } = window.UI;

// ---- Vocabulary: the backend's `kind` is a store or an engine. One name per thing. ----
const SOURCE_KINDS = { steam: 'Steam', gog: 'GOG', egs: 'Epic Games', itch: 'itch.io', f95: 'F95zone' };
const ENGINE_KINDS = { renpy: "Ren'Py", kirikiri: 'KiriKiri', unity: 'Unity', unreal: 'Unreal', godot: 'Godot', rpgmaker: 'RPG Maker', rpgmaker_mv: 'RPG Maker MV/MZ', rpgmaker_vx: 'RPG Maker VX', twine: 'Twine', august: 'AUGUST', buriko: 'BGI', catsystem2: 'CatSystem2', cmvs: 'CMVS', flash_air: 'Flash / AIR' };
const PLATFORM_NAMES = { windows: 'Windows', linux: 'Linux', macos: 'macOS', mac: 'macOS', android: 'Android', 'windows-x86_64': 'Windows', 'windows-i686': 'Windows (32-bit)', 'linux-x86_64': 'Linux', 'linux-i686': 'Linux (32-bit)', 'linux-aarch64': 'Linux (ARM)', web: 'Browser', unknown: 'Unknown' };
const CATEGORY_NAMES = { library: 'Source', engine: 'Engine', downloader: 'Downloader', manager: 'Launcher', metadata: 'Metadata', mod: 'Mods', patcher: 'Patcher', source: 'Remote source' };
const EVENT_NAMES = { game_added: 'Added to the library', version_added: 'Version added', ownership_recorded: 'Ownership recorded', merged: 'Folded in another record', renamed: 'Renamed', update_seen: 'Update seen', f95_update_detected: 'Update seen on F95zone', linked_cross_system: 'Linked to another computer\u2019s record' };
const platformName = (p) => PLATFORM_NAMES[p] || p;
const kindName = (k) => ENGINE_KINDS[k] || SOURCE_KINDS[k] || (k && k !== 'unknown' ? k : null);
const systemName = (uuid) => (uuid === model.mySystem ? 'This computer' : `Computer ${String(uuid || '').slice(0, 8)}`);

// ---- Model: what the backend reported, nothing else ----------------------------------
const model = { games: [], raw: [], plugins: [], accounts: [], info: null, app: null, config: null, mySystem: null, jobs: [], intake: { detected: [], decisions: [], watcher: null }, merges: null, events: [], capabilities: {}, platformStatus: {}, gamesAt: null, ready: false };
let games = [];   // adapted rows the existing renderers read
let plugins = []; // adapted plugin cards
let systems = []; // from the entries' system_uuids

// ---- Backend bridge --------------------------------------------------------------------
let pythia = null;
// Every slot call resolves to a status dict. No bridge or no answer is an error result,
// not an exception, so a page always has something honest to draw.
function call(name, ...args) {
  return new Promise((resolve) => {
    if (!pythia) { ErrorConsole.reportWeb('error', `${name}: not connected to Pythia`, 'bridge'); return resolve({ status: 'error', slot: name, message: 'Pythia is not connected to this page.' }); }
    if (typeof pythia[name] !== 'function') { ErrorConsole.reportWeb('error', `${name}: no such backend slot`, 'bridge'); return resolve({ status: 'error', slot: name, message: `Pythia has no "${name}" yet.` }); }
    try {
      pythia[name](...args, (r) => resolve(r === undefined || r === null ? { status: 'error', slot: name, message: 'Pythia did not answer.' } : r));
    } catch (e) {
      ErrorConsole.reportWeb('error', `${name}: ${e.message}`, 'bridge');
      resolve({ status: 'error', slot: name, message: e.message });
    }
  });
}
const ok = (r) => r && r.status === 'ok';
// A void call whose result only needs acknowledging: flash on success, a caution flash with the reason otherwise.
async function act(name, args, done, { what } = {}) {
  const r = await call(name, ...args);
  if (ok(r)) { if (done) flash(done); return r; }
  flash(r.status === 'unavailable' ? `${what || 'That'} is not built yet: ${r.message}` : `${what || 'That'} failed: ${r.message}`, 'caution');
  return r;
}
// loadInto: the one loading path. Skeletons, the call, then draw(result) or the state block for it.
function loadInto(host, name, args, draw, { what = 'This', actions = [] } = {}) {
  if (!host) return Promise.resolve();
  host.replaceChildren(stateBlock({ kind: 'loading' }));
  // Older slots answer with a bare list (get_downloader_plugins, get_all_plugins): a list is a good answer too.
  const run = () => call(name, ...args).then((r) => { host.innerHTML = ''; if (ok(r) || Array.isArray(r)) draw(r, host); else host.appendChild(fromResult(r, { what, retry: run, actions })); });
  return run();
}

// ---- Real-data adapter (2026-08-03, extended 2026-10-14): one backend.games row -> the row the renderers read.
const ART_CLASSES = 'abcdefghijkl';
function strHash(s) { s = String(s || ''); let h = 0; for (let i = 0; i < s.length; i++) { h = (h * 31 + s.charCodeAt(i)) | 0; } return Math.abs(h); }
function adaptGame(real) {
  const entries = real.entries || [];
  const own = entries.find((e) => e.system_uuid === model.mySystem) || entries[0] || {};
  const name = own.canonical_name || '?';
  const variants = [];
  for (const g of own.versions || []) {
    // Onboarding used to write the folder name as the version when it found no
    // version token (fixed 2026-10-14); records from before carry it still.
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
  const pendingDecision = model.intake.decisions.some((d) => variants.some((v) => v.path === d.path));
  let status = 'ok', statusText = 'No action required';
  if (installed && unknown && !engineKind) { status = 'critical'; statusText = 'Not identified'; }
  if (updates.length) { status = 'pending'; statusText = 'Update available'; }
  if (pendingDecision) { status = 'pending'; statusText = 'Needs a decision'; }
  const platforms = [...new Set(variants.flatMap((v) => v.platforms || []))];
  const systemIds = [...new Set(entries.map((e) => e.system_uuid))];
  return {
    id: real.game_id, name, own, entries, variants,
    source: sourceKind ? SOURCE_KINDS[sourceKind] : (ownedOn.length ? kindName(ownedOn[0]) : (installed ? 'Local folder' : 'Not installed')),
    engine: engineKind ? ENGINE_KINDS[engineKind] : (installed ? 'Not identified' : 'Unknown'),
    engineKind, sourceKind, kinds,
    version: variants.map((v) => v.version).find(Boolean) || null,
    status, statusText, installed, ownedOn, knownOn, updates, added, platforms,
    updated: own.updated_at ? Date.parse(own.updated_at) || 0 : 0,
    art: ART_CLASSES[strHash(real.game_id) % ART_CLASSES.length],
    systems: systemIds.map(systemName), systemIds,
    path: variants.find((v) => v.path)?.path || '',
    note: own.notes || '',
    events: own.timeline_events || [],
    managed: !!own.managed_externally,
    versions: own.versions || [],
    versionCount: (own.versions || []).length,
    mine: own.system_uuid === model.mySystem,
  };
}

function adaptPlugin(real) {
  const kind = (real.categories || []).map((c) => CATEGORY_NAMES[c] || c).join(' · ') || 'Uncategorized';
  return {
    id: real.id, icon: (real.display_name || '?').slice(0, 2).toUpperCase(), name: real.display_name, kind,
    desc: real.origin !== 'Built-in' ? `${real.origin} · ${real.author}` : '',
    state: real.enabled ? 'Ready' : 'Disabled', status: real.enabled ? 'ok' : 'neutral',
    enabled: real.enabled, origin: real.origin, version: real.version, categories: real.categories || [],
  };
}

let state = { view: 'library', history: [], selected: null, filterStatus: 'all', density: localStorage.getItem('pythia-density') || 'comfortable', libraryMode: localStorage.getItem('pythia-desktop-library') || 'list', search: '', source: 'all', engine: 'all', sort: 'name', bulk: new Set(), selectionMode: false, shell: 'desktop', hhPage: 'home', hhHistory: [], hhLibraryMode: localStorage.getItem('pythia-handheld-library') || 'list', hhSearch: '', hhSelected: null, hhSource: 'all', hhSettingsPage: 'root', detailTab: 'overview', settingsPane: 'general' };
const initials = (n) => String(n).split(/\s+/).filter(Boolean).slice(0, 2).map((x) => x[0]).join('').toUpperCase();
// List-level version-count affordance -- only worth a mention when there's more than one.
const versionsBadge = (g) => (g.versionCount > 1 ? ` · ${g.versionCount} versions` : '');
const versionText = (g) => g.version || 'no version';
const routineStatus = (label, kind = 'ok', symbol = '✓') => `<span class="status-icon ${kind}" role="img" aria-label="${esc(label)}" title="${esc(label)}">${symbol}</span>`;
const visibleStatus = (label, kind = 'pending', symbol = '!') => `<span class="status-inline ${kind}"><span class="status-icon ${kind}" aria-hidden="true">${symbol}</span>${esc(label)}</span>`;
const gameStatus = (g) => (g.status === 'ok' ? routineStatus(g.statusText || 'No action required') : visibleStatus(g.statusText, g.status, g.status === 'critical' ? '!' : '↻'));
const fileName = (p) => String(p || '').split(/[\\/]/).pop();

// ---- Navigation ----------------------------------------------------------------------
function navigate(view, push = true) {
  if (view === state.view) { renderCurrent(); return; }
  if (push) state.history.push(state.view);
  state.view = view;
  $$('.view').forEach((v) => v.classList.toggle('active', v.id === `view-${view}`));
  $$('.nav-row[data-nav]').forEach((n) => n.classList.toggle('active', n.dataset.nav === view));
  $('#backButton').disabled = !state.history.length;
  const titles = { library: 'Search games, engines, versions, paths…', downloads: 'Search intake queue…', reconcile: 'Search possible duplicates…', systems: 'Search systems…', launcher: 'Search launcher entries…', saves: 'Search save backups…', activity: 'Search jobs and events…', settings: 'Search settings…' };
  $('#globalSearch').placeholder = titles[view] || 'Search…';
  $('#globalSearch').value = ''; state.search = '';
  renderCurrent();
  window.scrollTo({ top: 0, behavior: 'instant' });
}
function renderCurrent() {
  ({ library: renderLibrary, downloads: renderDownloads, reconcile: renderReconcile, systems: renderSystems, launcher: renderLauncher, saves: renderSaves, activity: renderJobs, settings: renderSettings })[state.view]?.();
}
function filteredGames() {
  const q = state.search.toLowerCase();
  const list = games.filter((g) => {
    const matches = !q || [g.name, g.source, g.engine, g.version, g.path, g.note, ...g.platforms, ...g.systems].join(' ').toLowerCase().includes(q);
    return matches && (state.filterStatus === 'all' || (state.filterStatus === 'pending' ? g.status !== 'ok' : g.status === state.filterStatus)) && (state.source === 'all' || g.source === state.source) && (state.engine === 'all' || g.engine === state.engine);
  });
  const rank = { critical: 0, pending: 1, ok: 2 };
  list.sort((a, b) => (state.sort === 'status' ? (rank[a.status] - rank[b.status]) || a.name.localeCompare(b.name) : state.sort === 'updated' ? b.updated - a.updated : a.name.localeCompare(b.name)));
  return list;
}

// ---- Library -------------------------------------------------------------------------
function renderLibrary() {
  const list = filteredGames();
  const attention = games.filter((g) => g.status !== 'ok').length;
  const nSystems = new Set(games.flatMap((g) => g.systemIds)).size || 1;
  $('#librarySummary').textContent = model.ready ? `${list.length} of ${games.length} shown · ${attention} need attention · ${fmt.n(nSystems, 'system')}` : '';
  const noticeHost = $('#libraryNotice'); noticeHost.innerHTML = '';
  if (!model.ready) noticeHost.appendChild(stateBlock({ kind: 'loading' }));
  else if (attention) noticeHost.appendChild(notice({ tone: 'caution', title: `${fmt.n(attention, 'game')} need attention`, body: 'Not identified, a newer version, or a scan waiting for your decision.', action: { label: 'Show', run: () => { state.filterStatus = 'pending'; renderLibrary(); } } }));
  if (!list.some((g) => g.id === state.selected) && list[0]) state.selected = list[0].id;
  const host = $('#gameList'); const layout = $('#libraryLayout');
  host.classList.toggle('grid-mode', state.libraryMode === 'grid');
  layout.classList.toggle('grid-presentation', state.libraryMode === 'grid');
  $$('[data-library-mode]').forEach((b) => b.classList.toggle('active', b.dataset.libraryMode === state.libraryMode));
  if (!model.ready) { host.innerHTML = ''; host.appendChild(stateBlock({ kind: 'loading' })); $('#gameDetail').innerHTML = ''; return; }
  if (!games.length) { host.innerHTML = ''; host.appendChild(stateBlock({ glyph: '▦', title: 'No games yet', body: 'Add a game folder or scan a library folder; Pythia records what it finds and never moves files.', actions: [{ label: 'Add folder', primary: true, run: addFolder }, { label: 'Scan library', run: scanLibrary }] })); $('#gameDetail').innerHTML = ''; return; }
  if (!list.length) { host.innerHTML = ''; host.appendChild(stateBlock({ title: 'Nothing matches', body: 'Try fewer filters or another search.', actions: [{ label: 'Clear filters', run: () => $('#clearFilters').click() }] })); }
  else if (state.libraryMode === 'grid') {
    host.innerHTML = list.map((g) => `<article class="game-card ${g.id === state.selected ? 'active' : ''}" data-game="${g.id}" tabindex="0" aria-label="${esc(g.name)}">
      <div class="game-card-art art-${g.art}"><input type="checkbox" class="bulk-check" data-bulk-check="${g.id}" ${state.bulk.has(g.id) ? 'checked' : ''} aria-label="Select ${esc(g.name)}" style="position:absolute;z-index:3;right:8px;bottom:8px;width:17px;height:17px;"><span class="game-card-source">${esc(g.source)}</span>${g.status !== 'ok' ? `<span class="game-card-attention ${g.status}" title="${esc(g.statusText)}"></span>` : ''}<strong>${esc(g.name)}</strong></div>
      <div class="game-card-copy"><div class="game-card-meta">${esc(g.engine)} · ${esc(versionText(g))}${versionsBadge(g)}</div>${g.status !== 'ok' ? `<div class="game-card-state ${g.status}">${esc(g.statusText)}</div>` : ''}</div>
    </article>`).join('');
  } else {
    host.innerHTML = list.map((g) => `<article class="game-row ${g.id === state.selected ? 'active' : ''}" data-game="${g.id}" tabindex="0"><input type="checkbox" class="bulk-check" data-bulk-check="${g.id}" ${state.bulk.has(g.id) ? 'checked' : ''} aria-label="Select ${esc(g.name)}"><div class="cover-mini art-${g.art}">${esc(initials(g.name))}</div><div><h3>${esc(g.name)}</h3><div class="game-meta"><span>${esc(g.source)}</span><span>·</span><span>${esc(g.engine)}</span><span>·</span><span>${esc(versionText(g))}${versionsBadge(g)}</span></div></div><div class="status-text ${g.status}">${g.status === 'ok' ? '' : esc(g.statusText)}</div></article>`).join('');
  }
  $$('[data-game]').forEach((row) => { const choose = () => { const id = row.dataset.game; if (state.selectionMode) { state.bulk.has(id) ? state.bulk.delete(id) : state.bulk.add(id); updateBulkToolbar(); renderLibrary(); return; } state.selected = id; state.detailTab = 'overview'; renderLibrary(); }; row.onclick = choose; row.onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); choose(); } }; });
  $$('[data-bulk-check]').forEach((cb) => { cb.onclick = (e) => e.stopPropagation(); cb.onchange = () => { const id = cb.dataset.bulkCheck; cb.checked ? state.bulk.add(id) : state.bulk.delete(id); updateBulkToolbar(); }; });
  renderDetail(games.find((g) => g.id === state.selected) || list[0]);
}
function updateBulkToolbar() { const n = state.bulk.size; document.body.classList.toggle('selection-mode', state.selectionMode); $('#bulkToolbar').classList.toggle('hidden', !state.selectionMode); $('#bulkCount').textContent = n ? `${n} selected` : 'Select games'; $('#selectionModeButton').classList.toggle('primary', state.selectionMode); $('#selectionModeButton').setAttribute('aria-pressed', String(state.selectionMode)); $$('[data-bulk-action]').forEach((b) => (b.disabled = n === 0)); }
async function bulkAction(a) {
  const ids = [...state.bulk]; const names = ids.map((id) => games.find((g) => g.id === id)?.name).filter(Boolean);
  if (a === 'updates') { if (await confirm({ title: 'Check for updates?', body: 'Pythia asks F95zone about every game with an F95zone id. Other stores have no update check yet.', proceed: 'Check' })) { pythia.check_updates(); flash('Checking for updates'); navigate('activity'); } }
  if (a === 'reconcile') navigate('reconcile');
  if (a === 'backup') navigate('saves');
  if (a === 'export') { const dir = await prompt({ title: 'Export a library report', body: 'One JSON file with this computer\u2019s records.', hint: 'C:\\Users\\you\\Documents', kind: 'path', save: 'Export', pick: { label: 'Browse', run: pickFolder('Folder for the report') } }); if (dir) { const r = await act('export_library_report', [dir.trim()], null, { what: 'Export' }); if (ok(r)) flash(`Report written: ${fileName(r.path)}`); } }
  if (a === 'remove') {
    const yes = await confirmDanger({ title: `Remove ${fmt.n(ids.length, 'game')} from the library?`, body: 'Their records leave the library. Files on disk stay where they are.', recommendation: 'We recommend Cancel unless the entries are wrong.', items: names.slice(0, 8).concat(names.length > 8 ? [`and ${names.length - 8} more`] : []), proceed: 'Remove' });
    if (yes) { for (const id of ids) await call('forget_game', id); state.bulk.clear(); state.selectionMode = false; updateBulkToolbar(); flash(`Removed ${fmt.n(ids.length, 'game')}`); }
  }
}

function renderDetail(g) {
  const host = $('#gameDetail');
  if (!g) { host.innerHTML = ''; host.appendChild(stateBlock({ title: 'Pick a game', body: 'Its details open here.' })); return; }
  const updates = g.updates || [];
  host.innerHTML = `<div class="detail-hero"><div class="cover-large art-${g.art}">${esc(initials(g.name))}</div><div class="detail-copy"><div class="eyebrow">${esc(g.source)} · ${esc(g.engine)}</div><h2>${esc(g.name)}</h2><div class="tag-row">${gameStatus(g)}<span class="tag">${g.version ? `Version ${esc(g.version)}` : 'No version number'}</span><span class="tag">${g.systems.length} system${g.systems.length === 1 ? '' : 's'}</span>${g.managed ? '<span class="tag">Externally managed</span>' : ''}</div><p>${esc(g.note.split('\n')[0] || '')}</p><div class="detail-actions" id="detailActions">${g.path ? '<button class="btn" data-detail-action="open-folder">Open folder</button><button class="btn" data-detail-action="scan">Rescan install</button>' : ''}<button class="btn" data-detail-tab="versions">Manage versions</button><button class="icon-btn" data-detail-action="more" aria-label="More actions">•••</button></div></div></div><div class="detail-tabs"><button class="tab" data-tab="overview">Overview</button><button class="tab" data-tab="engine">Engine &amp; Compatibility</button><button class="tab" data-tab="versions">Versions</button><button class="tab" data-tab="systems">Systems</button><button class="tab" data-tab="notes">Notes</button><button class="tab" data-tab="history">History</button></div>
<div class="tab-pane" data-pane="overview"><div class="stats-grid"><div class="stat"><div class="label">Latest installed</div><div class="value">${esc(versionText(g))}</div></div><div class="stat"><div class="label">Systems</div><div class="value">${g.systems.length}</div><div class="sub">${esc(g.systems.join(' · '))}</div></div><div class="stat"><div class="label">Status</div><div class="value" style="text-align:left;font-size:0.875rem">${gameStatus(g)}</div>${g.added ? `<div class="sub">Added ${esc(fmt.date(g.added))}</div>` : ''}</div></div><div class="kv"><div class="key">Canonical name</div><div class="value">${esc(g.name)} ${g.mine ? '<button class="row-action" style="float:right" data-detail-action="rename" aria-label="Rename">✎</button>' : ''}</div><div class="key">Game ID</div><div class="value mono">${esc(g.id)}</div><div class="key">Source</div><div class="value">${esc(g.source)}</div><div class="key">Engine</div><div class="value">${esc(g.engine)}</div><div class="key">Platforms</div><div class="value">${esc(g.platforms.map(platformName).join(' · ') || '—')}</div><div class="key">Primary path</div><div class="value mono">${esc(g.path || '—')}</div>${g.knownOn.length ? `<div class="key">Known on</div><div class="value">${esc(g.knownOn.map((k) => `${kindName(k.platform) || k.platform} (${k.id})`).join(' · '))}</div>` : ''}${g.ownedOn.length ? `<div class="key">Owned on</div><div class="value">${esc(g.ownedOn.map((k) => kindName(k) || k).join(' · '))}</div>` : ''}</div></div>
<div class="tab-pane" data-pane="engine" id="engineTab"></div>
<div class="tab-pane" data-pane="versions">${updates.length ? `<h3 style="font:500 1.0625rem var(--serif);margin:0 0 9px">Available, not installed</h3>${updates.map((u) => `<div class="available-update"><span class="status-icon pending" aria-hidden="true">↻</span><div><strong>${esc(u.version || 'Newer version')} · ${esc(platformName(u.platform))}</strong><br><small>Seen via ${esc(u.source || 'a store')}${u.detected_at ? ` · ${esc(fmt.date(u.detected_at))}` : ''}</small></div><button class="btn small primary" style="margin-left:auto" data-detail-action="apply-update" data-version="${esc(u.version || '')}">Review update</button></div>`).join('')}` : ''}<h3 style="font:500 1.0625rem var(--serif);margin:14px 0 9px">Installed versions</h3>${g.versions.length ? g.versions.map((v) => `<div class="version-group"><div class="version-head"><strong>${esc(v.version && v.version !== g.name ? v.version : 'No version number')}</strong><small>${(v.variants || []).length} variant${(v.variants || []).length === 1 ? '' : 's'}</small><span class="spacer"></span></div>${(v.variants || []).map((x) => `<div class="variant-row"><div><div class="variant-name">${esc(x.language && x.language !== 'unknown' ? `Language ${x.language}` : 'Default')} ${(x.mods || []).length ? '' : ''}</div><div class="variant-path">${esc(x.path || '')}</div></div><div class="platforms">${(x.platforms || []).map((p) => `<span class="platform">${esc(platformName(p))}</span>`).join('')}</div><div>${(x.mods || []).length ? x.mods.map((m) => `<span class="tag">${esc(m.name || m)}</span>`).join(' ') : '<span style="color:var(--dim);font-size:0.625rem">No mods</span>'}</div><button class="row-action" data-variant-menu="${esc(x.path || '')}" aria-label="Variant actions">•••</button></div>`).join('')}</div>`).join('') : ''}<div id="versionsState"></div><div style="margin-top:10px"><button class="btn small" data-detail-action="add-folder">＋ Add another folder</button></div></div>
<div class="tab-pane" data-pane="systems"><table class="table"><thead><tr><th>System</th><th>Availability</th><th>Versions</th><th>Readiness</th><th></th></tr></thead><tbody>${g.entries.map((e) => `<tr><td class="primary-cell"><strong>${esc(systemName(e.system_uuid))}</strong><small>${e.updated_at ? `Record updated ${esc(fmt.dateTime(e.updated_at))}` : 'Synced record'}</small></td><td>${(e.versions || []).some((v) => (v.variants || []).some((x) => x.path)) ? (e.system_uuid === model.mySystem ? routineStatus('Installed here') : `<span class="status-inline neutral"><span class="status-icon neutral" aria-hidden="true">i</span>Installed there</span>`) : `<span class="status-inline neutral"><span class="status-icon neutral" aria-hidden="true">–</span>Not installed</span>`}</td><td>${esc((e.versions || []).map((v) => (v.version && v.version !== e.canonical_name ? v.version : 'no version')).join(', ') || '—')}</td><td data-readiness="${esc(e.system_uuid)}">${e.system_uuid === model.mySystem ? '…' : '<span class="status-inline neutral"><span class="status-icon neutral" aria-hidden="true">–</span>Not checked here</span>'}</td><td>${e.system_uuid === model.mySystem ? '<button class="btn small" data-detail-tab="engine">Inspect</button>' : ''}</td></tr>`).join('')}</tbody></table></div>
<div class="tab-pane" data-pane="notes">${g.mine ? `<textarea class="notes-area" id="notesArea" aria-label="Notes">${esc(g.note)}</textarea><div style="display:flex;justify-content:flex-end;margin-top:9px"><button class="btn primary" data-detail-action="save-note">Save note</button></div>` : ''}<div id="notesState"></div></div>
<div class="tab-pane" data-pane="history"><div class="timeline">${[...g.events].reverse().map((e) => `<div class="timeline-item"><strong>${esc(EVENT_NAMES[e.type] || e.type)}</strong><p>${esc(e.detail || '')}</p><time>${esc(fmt.dateTime(e.timestamp))}</time></div>`).join('')}</div><div id="historyState"></div></div>`;
  if (!g.versions.length) $('#versionsState', host).appendChild(stateBlock({ title: 'Nothing installed here', body: g.ownedOn.length ? `Owned on ${g.ownedOn.map((k) => kindName(k) || k).join(', ')}, not installed on this computer.` : 'No folder is known for this game on this computer.' }));
  if (!g.mine) $('#notesState', host).appendChild(stateBlock({ title: 'Notes live on their computer', body: `This record belongs to ${g.systems.find((s) => s !== 'This computer') || 'another computer'}; notes are read-only here.${g.note ? `\n\n${g.note}` : ''}` }));
  if (!g.events.length) $('#historyState', host).appendChild(stateBlock({ title: 'No history yet', body: 'Events are recorded as Pythia changes this record.' }));
  $$('[data-tab]', host).forEach((t) => (t.onclick = () => openTab(t.dataset.tab)));
  $$('[data-detail-tab]', host).forEach((t) => (t.onclick = () => openTab(t.dataset.detailTab)));
  $$('[data-detail-action]', host).forEach((b) => (b.onclick = () => detailAction(b.dataset.detailAction, g, b)));
  $$('[data-variant-menu]', host).forEach((b) => (b.onclick = () => menu(b, [{ label: 'Open folder', run: () => act('open_path', [b.dataset.variantMenu], null, { what: 'Open folder' }) }, { label: 'Rescan', run: () => rescan(g, b.dataset.variantMenu) }])));
  openTab(state.detailTab || 'overview');
  // Asked for after the page is drawn, so the list never waits on a plugin.
  call('find_manager_for_game', g.id).then((managers) => {
    if (!Array.isArray(managers) || !managers.length || !$('#detailActions', host)) return;
    const m = managers[0];
    const b = el(`<button class="btn primary" type="button">Open in ${esc(m.manager_name)}${m.available === false ? ' <span class="reason">· not installed</span>' : ''}</button>`);
    if (m.available === false) b.disabled = true;
    b.onclick = async () => { if (await confirm({ title: `Open in ${m.manager_name}?`, body: `${m.manager_name} opens and shows ${g.name}. Pythia does not run the game itself.`, proceed: 'Open' })) { pythia.open_in_manager(g.id, m.manager_id); flash(`Handed to ${m.manager_name}`); } };
    $('#detailActions', host).prepend(b);
  });
  renderEngineTab(g);
}
function renderEngineTab(g) {
  const pane = $('#engineTab'); if (!pane) return;
  if (!g.path) { pane.appendChild(stateBlock({ title: 'Not installed on this computer', body: 'Engine and platform checks need a folder here.' })); return; }
  loadInto(pane, 'get_platform_status', [g.id], (r, host) => {
    model.platformStatus[g.id] = r;
    const readiness = $(`[data-readiness="${model.mySystem}"]`, $('#gameDetail'));
    if (!r.engine) {
      host.appendChild(el(`<div class="engine-banner">${visibleStatus('Not identified', 'critical')}<div><strong>No engine plugin matched this install.</strong><div style="color:var(--text-secondary);font-size:0.6875rem;margin-top:2px">The folder is tracked without an engine assignment. Rescan after turning on the right engine plugin.</div></div><span class="spacer"></span><button class="btn small" data-fix="correct">Correct manually…</button></div>`));
      if (readiness) readiness.innerHTML = visibleStatus('Not identified', 'critical');
    } else {
      const rows = Object.entries(r.platforms || {}).map(([platform, p]) => `<div class="platform-row"><strong>${esc(platformName(platform))}</strong><div class="req">${esc(p.supported ? (p.native ? 'Runs natively' : 'Supported') : (p.requires || []).join(', ') || 'Not supported for this install')}</div>${p.supported ? routineStatus('Ready') : p.fixable ? `<button class="btn small primary" data-fix="${esc(platform)}">Add support</button>` : visibleStatus('Unavailable for this install', 'critical')}</div>`).join('');
      host.appendChild(el(`<div><div class="engine-banner">${routineStatus('Identified')}<div><strong>${esc(r.engine)}</strong></div><span class="spacer"></span><button class="btn small ghost" data-fix="correct">Correct manually…</button></div><div class="platform-status">${rows || ''}</div></div>`));
      if (!rows) host.appendChild(stateBlock({ title: 'No platform report', body: `${r.engine} does not report per-platform needs yet.` }));
      const supported = Object.values(r.platforms || {}).filter((p) => p.supported).length;
      if (readiness) readiness.innerHTML = supported ? routineStatus(`${supported} platform${supported === 1 ? '' : 's'} ready`) : visibleStatus('Needs preparation', 'pending');
    }
    $$('[data-fix]', host).forEach((b) => (b.onclick = async () => {
      if (b.dataset.fix === 'correct') { const r2 = await call('set_engine_kind', g.id, ''); infoSheet({ title: 'Correct engine identification', details: r2.message || 'Not available.' }); return; }
      if (await confirm({ tone: 'caution', title: `Add ${platformName(b.dataset.fix)} support?`, body: `${r.engine} writes its runtime files into the game folder. Nothing is launched.`, details: `Folder: ${g.path}`, proceed: 'Add support' })) { pythia.enable_platform(g.id, b.dataset.fix); flash('Preparing'); navigate('activity'); }
    }));
  }, { what: 'The engine report' });
}
function openTab(name) { state.detailTab = name; $$('.tab', $('#gameDetail')).forEach((t) => t.classList.toggle('active', t.dataset.tab === name)); $$('.tab-pane', $('#gameDetail')).forEach((p) => p.classList.toggle('active', p.dataset.pane === name)); }
function rescan(g, path) { pythia.start_onboard(path || g.path); flash('Rescanning'); navigate('activity'); }
async function detailAction(a, g, btn) {
  if (a === 'open-folder') act('open_path', [g.path], null, { what: 'Open folder' });
  if (a === 'scan') rescan(g);
  if (a === 'rename') { const v = await prompt({ title: 'Rename game', value: g.name }); if (v && v.trim() && v.trim() !== g.name) act('rename_game', [g.id, v.trim()], 'Renamed', { what: 'Rename' }); }
  if (a === 'save-note') act('save_notes', [g.id, $('#notesArea').value], 'Note saved', { what: 'Saving the note' });
  if (a === 'add-folder') addFolder();
  if (a === 'apply-update') { const r = await call('apply_update', g.id, btn.dataset.version || ''); infoSheet({ title: `Update ${btn.dataset.version || ''} for ${g.name}`, details: ok(r) ? 'Started' : r.message }); }
  if (a === 'more') {
    menu(btn, [
      { label: 'Rename', disabled: !g.mine, run: () => detailAction('rename', g) },
      { label: 'Find duplicates', run: () => navigate('reconcile') },
      { label: 'Review save backup', run: () => navigate('saves') },
      { label: 'Review launcher entry', run: () => navigate('launcher') },
      { label: 'Record details', run: () => infoSheet({ title: g.name, details: JSON.stringify(g.own, null, 2), wide: true }) },
      { rule: true },
      { label: 'Remove from library', danger: true, run: async () => { if (await confirmDanger({ title: `Remove ${g.name}?`, body: 'Its record leaves the library. Files on disk stay where they are.', recommendation: 'We recommend Cancel unless the entry is wrong.', items: g.path ? [g.path] : [], proceed: 'Remove' })) act('forget_game', [g.id], 'Removed from library', { what: 'Remove' }); } },
    ]);
  }
}

// ---- Downloads & intake ---------------------------------------------------------------
function renderDownloads() {
  const q = state.view === 'downloads' ? state.search.toLowerCase() : '';
  const { detected, decisions, watcher } = model.intake;
  const running = model.jobs.filter((j) => j.state === 'running').length;
  $('#downloadsSummary').textContent = `${fmt.n(detected.length + decisions.length, 'intake item')} · ${watcher ? `watching ${watcher.folder}` : 'no folder watched'} · ${fmt.n(running, 'job')} running`;
  $('#watchButton').textContent = watcher ? 'Stop watching' : 'Watch a folder';
  $('#intakeCount').textContent = fmt.n(detected.length + decisions.length, 'item');
  const rows = [];
  decisions.forEach((d) => { const s = (d.candidates?.suggested || [])[0]; rows.push({ cells: [fileName(d.path), 'Scan', s ? `${s.canonical_name} · similar name` : 'New game', 'pending', 'Needs decision'], action: '<button class="btn small primary" data-nav="reconcile">Review</button>', text: `${d.path} ${d.name}` }); });
  detected.forEach((d) => rows.push({ cells: [fileName(d.path), 'Watched folder', '—', 'info', `Seen ${fmt.time(d.at)}`], action: `<button class="btn small primary" data-onboard="${esc(d.path)}">Add to library</button> <button class="btn small ghost" data-forget-detected="${esc(d.path)}">Dismiss</button>`, text: d.path }));
  const shown = rows.filter((r) => !q || r.text.toLowerCase().includes(q));
  $('#downloadRows').innerHTML = shown.map((r) => `<tr><td class="primary-cell"><strong>${esc(r.cells[0])}</strong><small>${esc(r.text.split(' ')[0])}</small></td><td>${esc(r.cells[1])}</td><td>${esc(r.cells[2])}</td><td><span class="state-pill ${r.cells[3]}">${esc(r.cells[4])}</span></td><td>${r.action}</td></tr>`).join('');
  const st = $('#intakeState'); st.innerHTML = '';
  if (!rows.length) st.appendChild(stateBlock({ glyph: '⇩', title: 'Nothing waiting', body: watcher ? `New files in ${watcher.folder} show here.` : 'Watch a download folder, or scan a library; anything uncertain lands here.', actions: [{ label: watcher ? 'Scan library' : 'Watch a folder', primary: true, run: watcher ? scanLibrary : watchFolder }] }));
  else if (!shown.length) st.appendChild(stateBlock({ title: 'No intake items match', actions: [{ label: 'Clear search', run: () => { $('#globalSearch').value = ''; state.search = ''; renderDownloads(); } }] }));
  $$('[data-nav="reconcile"]', $('#view-downloads')).forEach((b) => (b.onclick = () => navigate('reconcile')));
  $$('[data-onboard]', $('#view-downloads')).forEach((b) => (b.onclick = () => { pythia.start_onboard(b.dataset.onboard); pythia.forget_detected(b.dataset.onboard, () => {}); flash('Adding'); navigate('activity'); }));
  $$('[data-forget-detected]', $('#view-downloads')).forEach((b) => (b.onclick = () => call('forget_detected', b.dataset.forgetDetected)));
  loadInto($('#downloaderCards'), 'get_downloader_plugins', [], (list, host) => {
    const ds = Array.isArray(list) ? list : [];
    if (!ds.length) { host.appendChild(stateBlock({ title: 'No downloader plugins', body: 'A downloader plugin adds a card here.' })); return; }
    ds.forEach((d) => {
      const watching = watcher && d.enabled;
      const card = el(`<div class="card"><div class="card-head"><div class="card-icon">${esc(d.display_name.slice(0, 2).toUpperCase())}</div><div><strong>${esc(d.display_name)}</strong><small>${esc(d.headless ? 'Can fetch without a window' : 'Opens its own window; Pythia watches the folder')}</small></div></div><div class="card-body"><p>${esc(watching ? `Watching ${watcher.folder}${watcher.program ? ` while ${fileName(watcher.program)} runs` : ''}.` : d.enabled ? 'Not watching a folder right now.' : 'Turned off in Settings > Downloaders.')}</p></div><div class="card-foot">${watching ? routineStatus('Watching') : d.enabled ? visibleStatus('Idle', 'neutral', '–') : visibleStatus('Turned off', 'neutral', '–')}<span class="spacer"></span><button class="btn small" data-watch>${watching ? 'Stop' : 'Watch a folder'}</button></div></div>`);
      $('[data-watch]', card).onclick = () => (watching ? stopWatching() : watchFolder());
      host.appendChild(card);
    });
  }, { what: 'Downloaders' });
}
function stopWatching() { pythia.stop_watch(); flash('Stopped watching'); }

// ---- Reconciliation: scan decisions and possible duplicates ---------------------------
let decisionIndex = 0, chosenCandidate = null;
function renderReconcile() {
  const { decisions } = model.intake;
  const merges = Array.isArray(model.merges) ? model.merges : [];
  const open = merges.filter((c) => !dismissed(c));
  $('#reconcileSummary').textContent = `${fmt.n(decisions.length, 'decision')} queued · ${fmt.n(open.length, 'possible duplicate')} · no files changed`;
  $('#reconcileCount').textContent = decisions.length + open.length ? String(decisions.length + open.length) : '';
  const steps = $$('#reconcileStepper .step');
  steps.forEach((s, i) => { s.classList.toggle('done', decisions.length ? i < 2 : false); s.classList.toggle('active', decisions.length ? i === 2 : false); });
  const area = $('#decisionArea'); area.innerHTML = '';
  if (!decisions.length) area.appendChild(stateBlock({ glyph: '⇄', title: 'No decisions waiting', body: 'When a scan finds a folder that may be a game you already have, it lands here.', actions: [{ label: 'Scan library', run: scanLibrary }] }));
  else {
    if (decisionIndex >= decisions.length) decisionIndex = 0;
    const d = decisions[decisionIndex]; const suggested = d.candidates?.suggested || [];
    if (!chosenCandidate || !suggested.some((s) => s.game_id === chosenCandidate)) chosenCandidate = suggested[0]?.game_id || 'new';
    const det = d.detection || {};
    area.appendChild(el(`<div class="onboard-grid"><section class="panel"><div class="panel-head"><h2>Incoming install</h2><span class="state-pill pending">${suggested.length ? 'Similar name found' : 'New'}</span><div class="panel-actions"><small>${decisionIndex + 1} of ${decisions.length}</small>${decisions.length > 1 ? '<button class="btn small ghost" data-next-decision>Next</button>' : ''}</div></div><div class="panel-body"><div class="path-box">${esc(d.path)}</div><div class="kv" style="margin-top:12px"><div class="key">Detected name</div><div class="value">${esc(d.name || fileName(d.path))}</div><div class="key">Version</div><div class="value">${esc(d.version_label || 'No version number')}</div><div class="key">Engine</div><div class="value">${esc(kindName(det.kind) || 'Not identified')}</div><div class="key">Platforms</div><div class="value">${esc((det.platforms || []).map(platformName).join(' · ') || '—')}</div></div></div></section>
      <section class="panel"><div class="panel-head"><h2>Choose identity</h2><small>Nothing changes until you commit</small></div><div class="panel-body"><div id="candidateList"></div><div class="choice-row"><button class="btn" data-skip-decision>Skip for now</button><button class="btn primary" id="commitDecision">Commit</button></div></div></section></div>`));
    const list = $('#candidateList', area);
    suggested.forEach((s) => list.appendChild(el(`<div class="candidate ${chosenCandidate === s.game_id ? 'selected' : ''}" data-candidate="${esc(s.game_id)}" tabindex="0"><div class="candidate-top"><div class="cover-mini art-${ART_CLASSES[strHash(s.game_id) % ART_CLASSES.length]}">${esc(initials(s.canonical_name || '?'))}</div><div><h3>${esc(s.canonical_name)}</h3><p>${esc(systemName(s.system_uuid))} · ${esc((s.versions || []).map((v) => (v.version && v.version !== s.canonical_name ? v.version : 'no version')).join(', ') || 'no versions')}</p></div></div><p>Link this folder to that game as another version.</p></div>`)));
    list.appendChild(el(`<div class="candidate ${chosenCandidate === 'new' ? 'selected' : ''}" data-candidate="new" style="margin-top:9px" tabindex="0"><div class="candidate-top"><div class="cover-mini art-g">＋</div><div><h3>Create a new game</h3><p>Its own record</p></div></div><p>Use this when the folder is a sequel, spin-off, remake, or unrelated title with a similar name.</p></div>`));
    $$('[data-candidate]', list).forEach((c) => { const pick = () => { chosenCandidate = c.dataset.candidate; $$('[data-candidate]', list).forEach((x) => x.classList.toggle('selected', x === c)); }; c.onclick = pick; c.onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(); } }; });
    $('[data-next-decision]', area)?.addEventListener('click', () => { decisionIndex = (decisionIndex + 1) % decisions.length; chosenCandidate = null; renderReconcile(); });
    $('[data-skip-decision]', area).onclick = () => { decisionIndex = (decisionIndex + 1) % decisions.length; chosenCandidate = null; renderReconcile(); flash('Skipped for now'); };
    $('#commitDecision', area).onclick = async () => {
      const target = suggested.find((s) => s.game_id === chosenCandidate);
      const decision = target ? { action: 'link', game_id: target.game_id } : { action: 'new' };
      const plan = el(`<table class="table"><tbody><tr><td>Identity</td><td>${target ? `Link to ${esc(target.canonical_name)}` : 'Create a new game'}</td></tr><tr><td>Version</td><td>${esc(d.version_label || 'No version number')}</td></tr><tr><td>Filesystem</td><td>Leave the folder where it is</td></tr><tr><td>Launcher changes</td><td>None</td></tr></tbody></table>`);
      if (await confirm({ title: 'Commit this decision?', node: plan, proceed: 'Commit' })) { steps[3].classList.add('active'); pythia.resolveDecision(d.path, decision); chosenCandidate = null; flash('Committed'); }
    };
  }
  const panel = $('#mergePanel'); panel.innerHTML = '';
  $('#mergeCount').textContent = model.merges === null ? '' : fmt.n(open.length, 'pair');
  if (model.merges === null) panel.appendChild(stateBlock({ kind: 'loading' }));
  else if (model.merges && model.merges.status) panel.appendChild(fromResult(model.merges, { what: 'The duplicate search', retry: () => { model.merges = null; renderReconcile(); pythia.refresh_merge_candidates(); } }));
  else if (!open.length) panel.appendChild(stateBlock({ glyph: '✓', title: 'No possible duplicates', body: merges.length ? `${fmt.n(merges.length, 'pair')} marked as not the same.` : 'Two records with the same or a similar name would show here.' }));
  else {
    const q = state.view === 'reconcile' ? state.search.toLowerCase() : '';
    open.filter((c) => !q || `${c.a.name} ${c.b.name} ${c.a.path} ${c.b.path}`.toLowerCase().includes(q)).forEach((c) => {
      const side = (x, other) => `<div class="variant-row" style="grid-template-columns:46px 1fr auto"><div class="cover-mini art-${ART_CLASSES[strHash(x.game_id) % ART_CLASSES.length]}">${esc(initials(x.name || '?'))}</div><div><div class="variant-name"><strong>${esc(x.name)}</strong> · ${esc(kindName(x.engine_kind) || 'Not identified')} · ${fmt.n(x.version_count || 0, 'version')}</div><div class="variant-path">${esc(x.path || 'Not installed here')}</div></div><button class="btn small" data-keep="${esc(x.game_id)}" data-fold="${esc(other.game_id)}">Keep this one</button></div>`;
      const card = el(`<div class="version-group"><div class="version-head"><strong>${esc(c.reason === 'parts' ? 'Parts of one game' : 'Similar names')}</strong><small>${c.reason === 'parts' ? 'Both folders sit under one parent folder' : `${Math.round((c.score || 0) * 100)}% alike`}</small><span class="spacer"></span><button class="btn small ghost" data-not-same>Not the same</button></div>${side(c.a, c.b)}${side(c.b, c.a)}</div>`);
      $$('[data-keep]', card).forEach((b) => (b.onclick = () => mergeInto(c, b.dataset.keep, b.dataset.fold)));
      $('[data-not-same]', card).onclick = () => { const s = dismissedSet(); s.add(pairKey(c)); try { localStorage.setItem(DISMISS_KEY, JSON.stringify([...s])); } catch (e) { /* ignore */ } flash('Kept apart'); renderReconcile(); renderChrome(); };
      panel.appendChild(card);
    });
  }
}
const DISMISS_KEY = 'pythia-review-dismissed';
const dismissedSet = () => { try { return new Set(JSON.parse(localStorage.getItem(DISMISS_KEY) || '[]')); } catch (e) { return new Set(); } };
const pairKey = (c) => [c.a.game_id, c.b.game_id].sort().join('+');
const dismissed = (c) => dismissedSet().has(pairKey(c));
async function mergeInto(c, keepId, foldId) {
  const keep = c.a.game_id === keepId ? c.a : c.b; const fold = c.a.game_id === foldId ? c.a : c.b;
  if (await confirmDanger({ title: `Fold "${fold.name}" into "${keep.name}"?`, body: 'Its versions, store ids and ownership move over. The folded record is removed. Files stay where they are.', recommendation: 'We recommend Cancel unless both are the same game.', items: [`Keep: ${keep.path || keep.name}`, `Fold: ${fold.path || fold.name}`], proceed: 'Fold' })) { pythia.merge_games(keepId, foldId); flash('Folding'); }
}

// ---- Systems & sync --------------------------------------------------------------------
function renderSystems() {
  const q = state.view === 'systems' ? state.search.toLowerCase() : '';
  const list = systems.filter((s) => !q || s.name.toLowerCase().includes(q));
  $('#systemsSummary').textContent = model.ready ? `${fmt.n(systems.length, 'system')} in the synced folder` : '';
  const n = $('#systemsNotice'); n.innerHTML = '';
  if (model.info) n.appendChild(notice({ tone: 'info', placement: 'page', title: 'Systems come from the synced folder', body: 'Point your sync tool at the library folder on each computer.', details: `Library folder: ${model.info.sync_folder}\nPairing handhelds, each device's saves and copying versions between devices are not built yet; "Add system" says so.` }));
  $('#systemCards').innerHTML = list.length ? list.map((s) => `<article class="card"><div class="card-head"><div class="card-icon">${esc(s.icon)}</div><div><strong>${esc(s.name)}</strong><small class="mono">${esc(s.id)}</small></div></div><div class="card-body"><div class="stats-grid" style="grid-template-columns:1fr 1fr;margin-bottom:0"><div class="stat"><div class="label">Tracked</div><div class="value">${esc(s.games)}</div></div><div class="stat"><div class="label">Installed</div><div class="value">${esc(s.installed)}</div></div></div>${s.mine && model.app ? `<p style="margin-top:10px">${esc(model.app.platform)} · Pythia ${esc(model.app.version)}</p>` : ''}</div><div class="card-foot">${s.mine ? routineStatus('This computer') : routineStatus('Synced record', 'neutral', 'i')}<span class="spacer"></span><button class="btn small" data-system-games="${esc(s.id)}">Games</button></div></article>`).join('') : '';
  $$('[data-system-games]').forEach((b) => (b.onclick = () => { state.search = ''; navigate('library'); $('#globalSearch').value = systemName(b.dataset.systemGames); state.search = systemName(b.dataset.systemGames); renderLibrary(); }));
  const head = $('#crossSystemHead'); const rows = $('#crossSystemRows'); const st = $('#crossSystemState'); st.innerHTML = '';
  const cross = games.filter((g) => g.systemIds.length > 1);
  $('#crossSystemCount').textContent = model.ready ? fmt.n(cross.length, 'game on more than one system', 'games on more than one system') : '';
  if (!model.ready) { head.innerHTML = ''; rows.innerHTML = ''; st.appendChild(stateBlock({ kind: 'loading' })); return; }
  if (!cross.length) { head.innerHTML = ''; rows.innerHTML = ''; st.appendChild(stateBlock({ title: systems.length > 1 ? 'No game is on two systems yet' : 'Only this computer so far', body: 'When another computer writes to the synced folder, games known to both are compared here.' })); return; }
  head.innerHTML = `<tr><th>Game</th>${systems.map((s) => `<th>${esc(s.name)}</th>`).join('')}</tr>`;
  rows.innerHTML = cross.map((g) => `<tr><td class="primary-cell"><strong>${esc(g.name)}</strong><small>${esc(g.engine)}</small></td>${systems.map((s) => { const e = g.entries.find((x) => x.system_uuid === s.id); if (!e) return '<td>—</td>'; const v = (e.versions || []).map((x) => (x.version && x.version !== e.canonical_name ? x.version : null)).filter(Boolean); const inst = (e.versions || []).some((x) => (x.variants || []).some((y) => y.path)); return `<td>${inst ? `<span class="state-pill ok">${esc(v.join(', ') || 'installed')}</span>` : '<span class="state-pill info">known</span>'}</td>`; }).join('')}</tr>`).join('');
}
function updateSystems(raw) {
  const by = new Map();
  (raw || []).forEach((r) => (r.entries || []).forEach((e) => { const uuid = e.system_uuid; if (!uuid) return; const s = by.get(uuid) || { games: 0, installed: 0 }; s.games++; if ((e.versions || []).some((v) => (v.variants || []).some((x) => x.path))) s.installed++; by.set(uuid, s); }));
  if (model.mySystem && !by.has(model.mySystem)) by.set(model.mySystem, { games: 0, installed: 0 });
  systems = [...by.entries()].map(([uuid, s]) => ({ id: uuid, icon: uuid === model.mySystem ? 'PC' : uuid.slice(0, 2).toUpperCase(), name: systemName(uuid), games: s.games, installed: s.installed, mine: uuid === model.mySystem })).sort((a, b) => (a.mine ? -1 : b.mine ? 1 : a.name.localeCompare(b.name)));
  if (state.view === 'systems') renderSystems();
}

// ---- Launcher entries and Save backups: wired, honest, not built yet ----------------------
function renderLauncher() {
  $('#launcherSummary').textContent = ''; $('#launcherRows').innerHTML = ''; $('#launcherCount').textContent = '';
  loadInto($('#launcherState'), 'get_launcher_entries', [], (r) => {
    const entries = r.entries || [];
    $('#launcherSummary').textContent = `${fmt.n(entries.length, 'tracked entry', 'tracked entries')}`;
    $('#launcherRows').innerHTML = entries.map((e) => `<tr><td class="primary-cell"><strong>${esc(e.game)}</strong><small>${esc(e.version || '')}</small></td><td>${esc(e.system)}</td><td>${esc(e.manager)}</td><td>${e.drifted ? '<span class="state-pill critical">Drifted</span>' : routineStatus('Current')}</td><td></td></tr>`).join('');
    if (!entries.length) $('#launcherState').appendChild(stateBlock({ title: 'No launcher entries', body: 'Entries appear once a launcher plugin writes one.' }));
  }, { what: 'Launcher entries' });
}
function renderSaves() {
  $('#savesSummary').textContent = ''; $('#saveRows').innerHTML = '';
  loadInto($('#savesState'), 'get_save_backups', [], (r) => {
    const backups = r.backups || [];
    $('#savesSummary').textContent = `${fmt.n(backups.length, 'tracked game')}`;
    $('#saveRows').innerHTML = backups.map((b) => `<tr><td class="primary-cell"><strong>${esc(b.game)}</strong><small>${esc(b.system || '')}</small></td><td>${esc(fmt.dateTime(b.last_snapshot))}</td><td>${esc(b.state)}</td><td>${esc(b.size || '—')}</td><td></td></tr>`).join('');
    if (!backups.length) $('#savesState').appendChild(stateBlock({ title: 'No save backups yet' }));
  }, { what: 'Save backups' });
}

// ---- Activity & jobs ---------------------------------------------------------------------
function renderJobs() {
  const q = state.view === 'activity' ? state.search.toLowerCase() : '';
  const running = model.jobs.filter((j) => j.state === 'running').length;
  $('#activitySummary').textContent = `${fmt.n(model.jobs.length, 'job')} · ${running} running`;
  $('#jobsTotal').textContent = model.jobs.length ? `${model.jobs.length} total` : '';
  const shown = model.jobs.filter((j) => !q || [j.title, j.detail, j.summary, j.state].join(' ').toLowerCase().includes(q));
  $('#jobRows').innerHTML = shown.map((j) => `<tr><td class="primary-cell"><strong>${esc(j.title)}</strong><small>${esc(j.state === 'running' ? (j.log[j.log.length - 1] || j.detail || 'Running') : (j.summary || j.detail || ''))}</small></td><td style="width:260px"><div class="progress ${j.state === 'done' ? 'ok' : j.state === 'failed' ? 'critical' : 'indeterminate'}"><span style="width:${j.state === 'running' ? 40 : 100}%"></span></div></td><td>${j.state === 'done' ? routineStatus('Completed') : j.state === 'failed' ? visibleStatus('Failed', 'critical') : visibleStatus('Running', 'pending', '↻')}</td><td>${esc(fmt.time(j.started_at))}</td><td><button class="btn small" data-job-log="${esc(j.id)}">Inspect</button></td></tr>`).join('');
  const st = $('#jobsState'); st.innerHTML = '';
  if (!model.jobs.length) st.appendChild(stateBlock({ glyph: '≋', title: 'Nothing has run yet', body: 'Scans, folder adds, account syncs and update checks show here with their progress.', actions: [{ label: 'Scan library', primary: true, run: scanLibrary }] }));
  else if (!shown.length) st.appendChild(stateBlock({ title: 'No jobs match' }));
  $$('[data-job-log]').forEach((b) => (b.onclick = () => { const j = model.jobs.find((x) => x.id === b.dataset.jobLog); infoSheet({ title: j.title, details: [`${j.detail}`, `Started ${fmt.dateTime(j.started_at)}`, j.ended_at ? `Ended ${fmt.dateTime(j.ended_at)} · ${j.summary}` : 'Still running', '', ...j.log].join('\n') }); }));
  const events = model.events.filter((e) => e.source !== 'web' && (!q || e.message.toLowerCase().includes(q))).slice(-60).reverse();
  $('#eventCount').textContent = model.events.length ? `${model.events.length} entries` : '';
  $('#eventLog').innerHTML = events.map((e) => `<div class="timeline-item"><strong>${e.level === 'error' ? visibleStatus('Error', 'critical') + ' ' : e.level === 'warn' ? visibleStatus('Warning', 'pending') + ' ' : ''}${esc(e.message.split('\n')[0])}</strong>${e.message.includes('\n') ? `<p>${esc(e.message.split('\n').slice(1, 3).join(' '))}</p>` : ''}<time>${esc(fmt.time(e.ts))} · ${esc(e.source)}</time></div>`).join('');
  const es = $('#eventState'); es.innerHTML = '';
  if (!events.length) es.appendChild(stateBlock({ title: model.events.length ? 'No events match' : 'No events yet', body: 'Everything Pythia logs during this run shows here.' }));
}

// ---- Settings ------------------------------------------------------------------------------
function renderSettings() { renderSettingsSearch(); renderSettingsPane(state.settingsPane); }
function renderSettingsSearch() { const q = state.search.toLowerCase(); $$('.form-row', $('#view-settings')).forEach((r) => r.classList.toggle('hidden', !!q && !r.innerText.toLowerCase().includes(q))); }
function openSettingsPane(name) { state.settingsPane = name; const btn = $$('[data-settings]').find((b) => b.dataset.settings === name); if (btn) { $$('[data-settings]').forEach((x) => x.classList.toggle('active', x === btn)); $$('.settings-pane').forEach((p) => p.classList.toggle('active', p.dataset.pane === name)); } if (state.view !== 'settings') navigate('settings'); else renderSettingsPane(name); }
function renderSettingsPane(name) {
  const info = model.info || {}; const app = model.app || {};
  if (name === 'general') { $('#appDataPath').textContent = info.app_data_folder || ''; call('get_ui_mode').then((m) => { $('#uiModeSelect').value = typeof m === 'string' ? m : ''; }); }
  if (name === 'library') { loadInto($('#libraryFolders'), 'get_library_folders', [], (r, host) => { (r.folders || []).forEach((f) => host.appendChild(el(`<div class="account-row"><div class="card-icon">F</div><div><strong>${esc(f.path)}</strong></div></div>`))); if (!(r.folders || []).length) host.appendChild(stateBlock({ title: 'No folders tracked' })); }, { what: 'Tracked folders', actions: [{ label: 'Scan a folder', primary: true, run: scanLibrary }] }); loadInto($('#scanSettings'), 'get_scan_settings', [], () => {}, { what: 'Scan settings' }); }
  if (['sources', 'downloaders', 'metadata', 'managers', 'engines', 'mods', 'patchers'].includes(name)) {
    const cat = { sources: 'library', downloaders: 'downloader', metadata: 'metadata', managers: 'manager', engines: 'engine', mods: 'mod', patchers: 'patcher' }[name];
    renderCapabilityList($(`[data-capability-list="${cat}"]`), cat);
    if (name === 'downloaders') loadInto($('#toolsState'), 'get_tools', [], (r, host) => { if (!(r.tools || []).length) host.appendChild(stateBlock({ title: 'No tools downloaded' })); }, { what: 'Downloaded tools' });
  }
  if (name === 'plugins') { renderPlugins(); loadInto($('#pluginRepos'), 'get_plugin_repositories', [], (r, host) => { if (!(r.repositories || []).length) host.appendChild(stateBlock({ title: 'No repositories added' })); }, { what: 'Plugin repositories' }); }
  if (name === 'sync') { $('#syncFolderPath').textContent = info.sync_folder || ''; $('#systemIdField').value = info.system_uuid || ''; loadInto($('#transferSettings'), 'get_transfer_settings', [], () => {}, { what: 'Game transfer' }); loadInto($('#storageRoles'), 'get_storage_roles', [], () => {}, { what: 'Storage roles' }); }
  if (name === 'advanced') { $('#dbPath').textContent = info.app_data_folder || ''; $('#schemaVersion').innerHTML = app.schema_version ? `${routineStatus('Up to date')}v${esc(app.schema_version)}` : ''; $('#logLevelSelect').value = model.config?.log_level === 'debug' ? 'debug' : 'info'; $('#consoleHint').textContent = model.config?.error_console ? 'On for this run. Ctrl+Shift+E toggles it.' : 'Off. Start Pythia with --error-console, or set error_console = True in pythia.conf.'; }
}
function renderCapabilityList(host, category) {
  if (!host) return;
  loadInto(host, 'get_capabilities', [category], (r, h) => {
    const list = r.plugins || [];
    if (!list.length) { h.appendChild(stateBlock({ title: `No ${CATEGORY_NAMES[category].toLowerCase()} plugins`, body: 'A plugin of this kind adds a row here.' })); return; }
    list.forEach((p) => {
      const s = p.state || {}; let status, extra = '', sub = '';
      if (s.error) status = visibleStatus('Check failed', 'critical');
      else if (s.unknown) status = visibleStatus('Not reported', 'neutral', '–');
      else if (category === 'library') { status = s.has_account ? (s.connected ? routineStatus('Connected') : visibleStatus('Not connected', 'pending')) : routineStatus('Folder detection only', 'neutral', 'i'); if (s.has_account) extra = `<button class="btn small" data-account="${esc(p.id)}">Account</button>`; }
      else if (category === 'manager') { status = s.available ? routineStatus('Installed here') : visibleStatus('Not found on this computer', 'pending'); sub = `Opens games from ${(s.sources || []).map((k) => kindName(k) || k).join(', ') || 'any source'}`; }
      else if (category === 'engine') { status = s.installed ? routineStatus('Ready') : visibleStatus('Tool missing', 'pending'); sub = s.tool_version ? `Tool ${s.tool_version}` : ''; }
      else if (category === 'downloader') { status = s.watching ? routineStatus('Watching') : routineStatus('Idle', 'neutral', '–'); sub = s.watching ? `Watching ${s.watching}` : ''; extra = `<button class="btn small" data-watch-config>${s.watching ? 'Stop' : 'Watch a folder'}</button>`; }
      else status = p.enabled ? routineStatus('Ready') : visibleStatus('Turned off', 'neutral', '–');
      if (!p.enabled) status = visibleStatus('Turned off', 'neutral', '–');
      const row = el(`<div class="account-row"><div class="card-icon">${esc(p.display_name.slice(0, 2).toUpperCase())}</div><div><strong>${esc(p.display_name)}</strong>${sub || s.error ? `<small>${esc(s.error || sub)}</small>` : ''}</div><div class="row-status">${status}<button class="toggle" aria-label="Enable ${esc(p.display_name)}"><span></span></button>${extra}</div></div>`);
      bindSwitch($('.toggle', row), p.enabled, (on) => act('set_plugin_enabled', [p.id, on], on ? `${p.display_name} turned on` : `${p.display_name} turned off`, { what: 'Turning the plugin' }).then((res) => { if (ok(res)) { refreshPlugins().then(() => renderCapabilityList(host, category)); } }), `Enable ${p.display_name}`);
      $('[data-account]', row)?.addEventListener('click', () => accountDialog(p.id));
      $('[data-watch-config]', row)?.addEventListener('click', () => (s.watching ? stopWatching() : watchFolder()));
      h.appendChild(row);
    });
  }, { what: `${CATEGORY_NAMES[category]} plugins` });
}
async function accountDialog(pluginId) {
  const a = model.accounts.find((x) => x.id === pluginId);
  if (!a) { infoSheet({ title: 'No account for this source', details: 'This source has no account fields.' }); return; }
  const body = el(`<div></div>`);
  body.appendChild(el(`<p>${esc(a.connected ? 'Connected. Keys are kept in the library folder; every synced computer can read them.' : 'Enter each field to connect. Keys are kept in the library folder; every synced computer can read them.')}</p>`));
  a.fields.forEach((f) => { const row = el(`<div class="form-row" style="grid-template-columns:1fr auto"><div class="form-label"><strong>${esc(f.label)}</strong><small>${f.secret ? 'Secret' : ''}</small></div><button class="btn small">${a.connected ? 'Change' : 'Enter'}</button></div>`); $('button', row).onclick = async () => { const v = await prompt({ title: `${a.display_name}: ${f.label}`, hint: f.secret ? 'Paste the key' : f.label }); if (v !== null && v.trim()) { const r = await act('set_account_credential', [a.id, f.key, v.trim()], null, { what: 'Saving' }); if (ok(r)) { flash('Saved', 'info', { private: true }); refreshPlugins(); } } }; body.appendChild(row); });
  if (a.connected) { const b = el('<button class="btn primary" style="margin-top:10px">Sync owned games now</button>'); b.onclick = () => { pythia.sync_account(a.id); flash('Syncing'); navigate('activity'); }; body.appendChild(b); }
  infoSheet({ title: `${a.display_name} account`, node: body });
}
function renderPlugins() {
  const q = state.view === 'settings' ? state.search.toLowerCase() : '';
  const st = $('#pluginsState'); st.innerHTML = '';
  if (!model.ready) { $('#pluginCards').innerHTML = ''; st.appendChild(stateBlock({ kind: 'loading' })); return; }
  const list = plugins.filter((p) => !q || [p.name, p.kind, p.desc, p.state, p.origin, p.version].join(' ').toLowerCase().includes(q));
  $('#pluginCards').innerHTML = list.map((p) => `<article class="card"><div class="card-head"><div class="card-icon">${esc(p.icon)}</div><div><strong>${esc(p.name)}</strong><small>${esc(p.kind)} · ${esc(p.origin === 'Built-in' ? 'Official' : 'Unofficial')} · ${esc(p.version)}</small></div><button class="toggle" data-plugin="${esc(p.id)}" style="margin-left:auto" aria-label="${p.enabled ? 'Disable' : 'Enable'} ${esc(p.name)}"><span></span></button></div>${p.desc ? `<div class="card-body"><p>${esc(p.desc)}</p></div>` : ''}<div class="card-foot">${!p.enabled ? visibleStatus('Turned off', 'neutral', '–') : routineStatus('On')}<span class="spacer"></span><button class="btn small" data-plugin-details="${esc(p.id)}">Details</button></div></article>`).join('');
  if (!plugins.length) st.appendChild(stateBlock({ title: 'No plugins found', body: 'Built-in plugins ship with Pythia; drop-in folders and Python packages add more.' }));
  else if (!list.length) st.appendChild(stateBlock({ title: 'No plugins match' }));
  $$('[data-plugin]').forEach((t) => { const p = plugins.find((x) => x.id === t.dataset.plugin); bindSwitch(t, p.enabled, (on) => act('set_plugin_enabled', [p.id, on], on ? `${p.name} turned on` : `${p.name} turned off`, { what: 'Turning the plugin' }).then((r) => { if (ok(r)) refreshPlugins(); else renderPlugins(); })); });
  $$('[data-plugin-details]').forEach((b) => (b.onclick = () => { const p = plugins.find((x) => x.id === b.dataset.pluginDetails); const a = model.accounts.find((x) => x.id === p.id); infoSheet({ title: p.name, details: [`Id: ${p.id}`, `Version: ${p.version}`, `Origin: ${p.origin}`, `Adds: ${p.kind}`, p.origin !== 'Built-in' ? 'Unofficial: it came from outside Pythia and runs with the same access as Pythia.' : 'Official: built into Pythia.', a ? `Account: ${a.connected ? 'connected' : 'not connected'} (${a.fields.map((f) => f.label).join(', ')})` : ''].filter(Boolean).join('\n') }); }));
}

// ---- Actions shared by pages ------------------------------------------------------------
const pickFolder = (title) => (done) => call('pick_folder', title).then((v) => done(typeof v === 'string' ? v : ''));
async function addFolder() {
  const path = await prompt({ title: 'Add a game folder', body: 'Pythia records the folder as it is; nothing is moved.', hint: 'G:\\games\\Example', kind: 'path', save: 'Add', pick: { label: 'Browse', run: pickFolder('Add a game folder') } });
  if (!path || !path.trim()) return;
  pythia.start_onboard(path.trim()); flash('Adding folder'); navigate('activity');
}
async function scanLibrary() {
  const path = await prompt({ title: 'Scan a library folder', body: 'Every game folder inside is recorded. Scanning is read-only apart from a small marker file in each game folder.', hint: 'G:\\games', kind: 'path', save: 'Scan', pick: { label: 'Browse', run: pickFolder('Scan a library folder') } });
  if (!path || !path.trim()) return;
  pythia.start_scan(path.trim()); flash('Scan started'); navigate('activity');
}
async function importSource() {
  const connected = model.accounts.filter((a) => a.connected);
  if (!connected.length) { infoSheet({ title: 'No connected source', details: `Connect a game source first (Settings > Sources > Account). Sources with accounts: ${model.accounts.map((a) => a.display_name).join(', ') || 'none'}.` }); return; }
  const id = await prompt({ title: 'Import from source', kind: 'select', options: connected.map((a) => ({ value: a.id, label: a.display_name })), value: connected[0].id, save: 'Import' });
  if (id) { pythia.sync_account(id); flash('Importing'); navigate('activity'); }
}
async function watchFolder() {
  const folder = await prompt({ title: 'Watch a download folder', hint: 'C:\\Users\\you\\Downloads', kind: 'path', save: 'Next', pick: { label: 'Browse', run: pickFolder('Folder to watch') } });
  if (!folder || !folder.trim()) return;
  const program = await prompt({ title: 'Program to run while watching', body: 'Leave empty to only watch the folder.', hint: 'C:\\Tools\\F95Checker.exe', kind: 'path', save: 'Watch', pick: { label: 'Browse', run: (done) => call('pick_file', 'Program to run').then((v) => done(typeof v === 'string' ? v : '')) } });
  if (program === null) return;
  pythia.start_watch(program.trim(), folder.trim()); flash('Watching'); navigate('downloads');
}
async function exportDiagnostics() {
  const dir = await prompt({ title: 'Export diagnostics', body: 'The log file plus app, system and plugin facts. No credentials.', hint: 'C:\\Users\\you\\Desktop', kind: 'path', save: 'Export', pick: { label: 'Browse', run: pickFolder('Folder for the diagnostics') } });
  if (!dir) return;
  const r = await act('export_diagnostics', [dir.trim()], null, { what: 'Export' });
  if (ok(r)) flash(`Diagnostics written: ${fileName(r.report)}`);
}
function bindAction(elm) { elm.onclick = () => action(elm.dataset.action); }
async function action(a) {
  if (a === 'scan') scanLibrary();
  if (a === 'add-folder') addFolder();
  if (a === 'watch-folder') (model.intake.watcher ? stopWatching() : watchFolder());
  if (a === 'rebuild-cache') act('rebuild_index', [], 'Index rebuilt', { what: 'Rebuilding the index' });
  if (a === 'add-system') { const r = await call('get_transfer_settings'); infoSheet({ title: 'Add a system', details: r.message || 'Point your sync tool at the library folder on the other computer; it appears here once it writes a record.' }); }
  if (a === 'reload-launcher') renderLauncher();
  if (a === 'backup-now') { const r = await call('get_save_backups'); infoSheet({ title: 'Back up saves', details: r.message || 'Started' }); }
  if (a === 'clear-jobs') act('clear_finished_jobs', [], 'Cleared', { what: 'Clearing' });
  if (a === 'export-diagnostics') exportDiagnostics();
  if (a === 'install-plugin') infoSheet({ title: 'Install a plugin', details: `Drop a plugin folder (with its manifest) into the plugins folder under ${model.info?.app_data_folder || 'the app data folder'}, or pip-install a package that declares a pythia.plugins entry point, then restart Pythia. Plugins from outside Pythia are marked Unofficial.` });
  if (a === 'reset-identity') { if (await confirmDanger({ title: 'Reset this computer\u2019s identity?', body: 'Every record this computer wrote would belong to a computer that no longer exists.', recommendation: 'We recommend Cancel.', proceed: 'Reset' })) { const r = await call('reset_identity'); flash(ok(r) ? 'Identity reset' : r.message, ok(r) ? 'info' : 'caution'); } }
}

// ---- Handheld shell -----------------------------------------------------------------------
function resolveInitialShell() {
  const explicit = new URLSearchParams(location.search).get('ui');
  if (explicit === 'handheld' || explicit === 'desktop') return explicit;
  const pref = localStorage.getItem('pythia-startup-shell') || 'auto';
  if (pref === 'desktop' || pref === 'handheld') return pref;
  if (pref === 'last') return localStorage.getItem('pythia-last-shell') || 'desktop';
  const coarse = window.matchMedia && matchMedia('(pointer: coarse)').matches;
  const compact = window.innerWidth <= 900 || (coarse && window.innerWidth <= 1400) || (window.innerHeight <= 650 && window.innerWidth <= 1200);
  return compact ? 'handheld' : 'desktop';
}
function enterHandheld(page = 'home') { state.shell = 'handheld'; state.hhPage = page; localStorage.setItem('pythia-last-shell', 'handheld'); document.body.classList.add('handheld-active'); $('#handheldShell').classList.remove('hidden'); renderHandheld(); setTimeout(() => $('.hh-nav.active')?.focus(), 0); }
function exitHandheld() { state.shell = 'desktop'; localStorage.setItem('pythia-last-shell', 'desktop'); document.body.classList.remove('handheld-active'); $('#handheldShell').classList.add('hidden'); setTimeout(() => $('#enterHandheldButton')?.focus(), 0); }
function navigateHandheld(page, push = true) { if (page === state.hhPage) { renderHandheld(); return; } if (push) state.hhHistory.push(state.hhPage); state.hhPage = page; state.hhSelected = null; if (page !== 'settings') state.hhSettingsPage = 'root'; renderHandheld(); }
function handheldTitle() {
  if (state.hhPage === 'settings' && state.hhSettingsPage !== 'root') {
    const labels = { library: 'Library', capabilities: 'Plugin capabilities', plugins: 'Installed plugins', sync: 'Systems & sync', interface: 'Interface', advanced: 'Advanced', sources: 'Sources', downloaders: 'Downloaders', metadata: 'Metadata', managers: 'Managers', engines: 'Engine detection', mods: 'Mods', patchers: 'Patchers' };
    return [labels[state.hhSettingsPage] || 'Settings', 'Settings'];
  }
  const attention = games.filter((g) => g.status !== 'ok').length; const running = model.jobs.filter((j) => j.state === 'running').length;
  const { detected, decisions, watcher } = model.intake;
  return { home: ['Home', model.ready ? `${attention} need attention · ${fmt.n(running, 'job')} running` : 'Connecting'], library: ['Library', `${games.length} games`], attention: ['Needs attention', `${attention} items`], downloads: ['Downloads & intake', `${fmt.n(detected.length + decisions.length, 'item')} · ${watcher ? 'watching' : 'not watching'}`], systems: ['Systems', `${fmt.n(systems.length, 'system')}`], settings: ['Settings', 'Library, plugins, systems, and interface'], detail: ['Game details', 'Versions, systems, and actions'], decision: ['Review', 'No changes applied'], plan: ['Available update', 'Review before applying'] }[state.hhPage] || ['Pythia', ''];
}
function renderHandheld() {
  const [title, sub] = handheldTitle(); $('#hhTitle').textContent = title; $('#hhSubtitle').textContent = sub;
  $('#hhBack').disabled = !state.hhHistory.length && !['detail', 'decision', 'plan'].includes(state.hhPage) && !(state.hhPage === 'settings' && state.hhSettingsPage !== 'root');
  $$('[data-hh-nav]').forEach((b) => b.classList.toggle('active', b.dataset.hhNav === state.hhPage));
  const running = model.jobs.filter((j) => j.state === 'running').length;
  $('#hhFooterRight').textContent = model.ready ? `${fmt.n(games.length, 'game')} · ${fmt.n(running, 'job')} running` : 'Connecting';
  const q = state.hhSearch.toLowerCase();
  const page = state.hhPage;
  if (page === 'home') renderHHHome(); else if (page === 'library') renderHHLibrary(q); else if (page === 'attention') renderHHAttention(q); else if (page === 'downloads') renderHHDownloads(); else if (page === 'systems') renderHHSystems(); else if (page === 'settings') renderHHSettings(); else if (page === 'detail') renderHHDetail(games.find((g) => g.id === state.hhSelected)); else if (page === 'decision') renderHHDecision(games.find((g) => g.id === state.hhSelected)); else if (page === 'plan') renderHHPlan(games.find((g) => g.id === state.hhSelected));
  bindHandheldActions();
}
function hhSummaryGame(g) { return `<button class="hh-summary" data-hh-game="${g.id}"><span class="art art-${g.art}">${esc(initials(g.name))}</span><span><strong>${esc(g.name)}</strong><small>${esc(g.engine)} · ${esc(versionText(g))}${versionsBadge(g)} · ${esc(g.systems.join(' / '))}</small></span>${gameStatus(g)}</button>`; }
function hhJob(j) { return `<div class="hh-summary" style="cursor:default"><span class="art" style="background:${j.state === 'failed' ? 'var(--critical-soft-token)' : 'var(--informational-soft)'};color:${j.state === 'failed' ? 'var(--critical-token)' : 'var(--informational)'}">${j.state === 'running' ? '↻' : j.state === 'done' ? '✓' : '!'}</span><span><strong>${esc(j.title)}</strong><small>${esc(j.state === 'running' ? (j.log[j.log.length - 1] || j.detail || 'Running') : (j.summary || ''))}</small></span>${j.state === 'running' ? visibleStatus('Running', 'pending', '↻') : j.state === 'done' ? routineStatus('Done') : visibleStatus('Failed', 'critical')}</div>`; }
function renderHHHome() {
  const host = $('#hhContent'); host.innerHTML = '';
  if (!model.ready) { host.appendChild(stateBlock({ kind: 'loading' })); return; }
  const attention = games.filter((g) => g.status !== 'ok'); const decisions = model.intake.decisions.length; const jobs = model.jobs.slice(0, 4);
  const failed = model.jobs.filter((j) => j.state === 'failed').length;
  const recent = [...games].filter((g) => g.added).sort((a, b) => (b.added > a.added ? 1 : -1)).slice(0, 3);
  host.innerHTML = `<div class="hh-stack">${attention.length ? `<section class="hh-alert-strip"><span class="status-icon critical" aria-hidden="true">!</span><div><strong>${attention.length} items need attention</strong><small>${decisions ? `${fmt.n(decisions, 'decision')} waiting · ` : ''}${fmt.n(model.jobs.filter((j) => j.state === 'running').length, 'job')} running</small></div><button class="hh-action" style="margin-left:auto" data-hh-nav-jump="attention">Review</button></section>` : ''}
    ${games.length ? `<section class="hh-panel"><div class="hh-panel-head"><h2>Needs attention</h2><span class="spacer"></span><button class="hh-action" data-hh-nav-jump="attention">All ${attention.length}</button></div><div class="hh-panel-body hh-summary-list" id="hhAttention"></div></section>` : ''}
    <section class="hh-panel"><div class="hh-panel-head"><h2>Active work</h2><span class="spacer"></span><small>${fmt.n(model.jobs.filter((j) => j.state === 'running').length, 'job')} running</small></div><div class="hh-panel-body hh-summary-list" id="hhJobs"></div></section>
    <section class="hh-panel"><div class="hh-panel-head"><h2>${games.length ? 'Recently added' : 'Your library'}</h2><span class="spacer"></span><button class="hh-action" data-hh-nav-jump="library">Library</button></div><div class="hh-panel-body hh-summary-list" id="hhRecent"></div></section>
    ${failed ? `<section class="hh-alert-strip" style="border-color:color-mix(in srgb,var(--warning) 45%,var(--border));background:var(--warning-soft)"><span class="status-icon pending" aria-hidden="true">!</span><div><strong>${fmt.n(failed, 'job')} failed</strong><small>${esc(model.jobs.find((j) => j.state === 'failed').summary)}</small></div><button class="hh-action" style="margin-left:auto" data-hh-jobs>Details</button></section>` : ''}</div>`;
  const a = $('#hhAttention', host); if (a) { a.innerHTML = attention.slice(0, 4).map(hhSummaryGame).join(''); if (!attention.length) a.appendChild(stateBlock({ glyph: '✓', title: 'Nothing needs attention' })); }
  const j = $('#hhJobs', host); j.innerHTML = jobs.map(hhJob).join(''); if (!jobs.length) j.appendChild(stateBlock({ title: 'Nothing has run yet', body: 'Scans and adds show here.', actions: [{ label: 'Scan library', run: scanLibrary }] }));
  const r = $('#hhRecent', host); r.innerHTML = recent.map(hhSummaryGame).join(''); if (!games.length) r.appendChild(stateBlock({ glyph: '▦', title: 'No games yet', body: 'Add a folder or scan a library.', actions: [{ label: 'Add folder', primary: true, run: addFolder }] }));
}
function handheldGames(q = '') { return games.filter((g) => !q || [g.name, g.source, g.engine, g.version, g.statusText, ...g.systems].join(' ').toLowerCase().includes(q)); }
function renderHHLibrary(q = '') {
  const sources = [...new Set(games.map((g) => g.source))].sort();
  const list = handheldGames(q).filter((g) => state.hhSource === 'all' || g.source === state.hhSource);
  const host = $('#hhContent');
  host.innerHTML = `<div class="hh-toolbar"><div class="hh-seg"><button data-hh-library-mode="list" class="${state.hhLibraryMode === 'list' ? 'active' : ''}">☰ List</button><button data-hh-library-mode="grid" class="${state.hhLibraryMode === 'grid' ? 'active' : ''}">▦ Grid</button></div><select class="hh-filter" id="hhSourceFilter"><option value="all">All sources</option>${sources.map((s) => `<option value="${esc(s)}" ${state.hhSource === s ? 'selected' : ''}>${esc(s)}</option>`).join('')}</select><button class="hh-action" data-hh-nav-jump="attention">Needs attention</button><span class="spacer"></span><small>${list.length} of ${games.length}</small></div>${state.hhLibraryMode === 'grid' ? `<div class="hh-library-grid">${list.map((g) => `<button class="hh-game-card" data-hh-game="${g.id}"><div class="hh-card-art art-${g.art}"><strong>${esc(g.name)}</strong></div><div class="hh-card-copy"><small>${esc(g.engine)} · ${esc(versionText(g))}${versionsBadge(g)}</small>${gameStatus(g)}</div></button>`).join('')}</div>` : `<div class="hh-library-list">${list.map((g) => `<button class="hh-game-row" data-hh-game="${g.id}"><span class="art art-${g.art}">${esc(initials(g.name))}</span><span><strong>${esc(g.name)}</strong><small>${esc(g.source)} · ${esc(g.engine)} · ${esc(versionText(g))}${versionsBadge(g)}</small></span><span class="system">${esc(g.systems.join(' · '))}</span><span class="state">${gameStatus(g)}</span></button>`).join('')}</div>`}`;
  if (!model.ready) host.appendChild(stateBlock({ kind: 'loading' }));
  else if (!games.length) host.appendChild(stateBlock({ glyph: '▦', title: 'No games yet', body: 'Add a folder or scan a library.', actions: [{ label: 'Add folder', primary: true, run: addFolder }, { label: 'Scan library', run: scanLibrary }] }));
  else if (!list.length) host.appendChild(stateBlock({ title: 'Nothing matches', actions: [{ label: 'Clear', run: () => { state.hhSource = 'all'; state.hhSearch = ''; $('#hhSearch').value = ''; renderHandheld(); } }] }));
}
function renderHHAttention(q = '') {
  const list = handheldGames(q).filter((g) => g.status !== 'ok'); const host = $('#hhContent');
  host.innerHTML = `<div class="hh-toolbar"><span>${list.length} items</span><span class="spacer"></span><button class="hh-action" data-hh-nav-jump="library">Library</button></div><div class="hh-issue-list">${list.map((g) => `<button class="hh-issue ${g.status}" data-hh-decision="${g.id}"><span class="symbol">${g.status === 'critical' ? '!' : '↻'}</span><span><strong>${esc(g.name)}</strong><small>${esc(g.statusText)} · ${esc(g.engine)} · ${esc(versionText(g))}</small></span><span class="go">›</span></button>`).join('')}</div>`;
  if (!list.length) host.appendChild(stateBlock({ glyph: '✓', title: 'Nothing needs attention', body: 'Not identified, newer versions and scan decisions show here.' }));
}
function renderHHDownloads() {
  const { detected, decisions, watcher } = model.intake; const host = $('#hhContent');
  host.innerHTML = `<div class="hh-stack"><section class="hh-panel"><div class="hh-panel-head"><h2>Intake queue</h2><span class="spacer"></span><button class="hh-action primary" data-hh-add>＋ Add</button></div><div class="hh-panel-body hh-summary-list" id="hhIntake">${decisions.map((d) => `<button class="hh-summary" data-hh-review><span class="art art-a">${esc(initials(d.name || fileName(d.path)))}</span><span><strong>${esc(fileName(d.path))}</strong><small>Scan · ${(d.candidates?.suggested || []).length ? `similar to ${esc(d.candidates.suggested[0].canonical_name)}` : 'new game'}</small></span>${visibleStatus('Decide', 'pending')}</button>`).join('')}${detected.map((d) => `<button class="hh-summary" data-hh-onboard="${esc(d.path)}"><span class="art art-d">⇩</span><span><strong>${esc(fileName(d.path))}</strong><small>Watched folder · ${esc(fmt.time(d.at))}</small></span>${visibleStatus('Add', 'pending', '＋')}</button>`).join('')}</div></section><section class="hh-panel"><div class="hh-panel-head"><h2>Watcher</h2><span class="spacer"></span><button class="hh-action" data-hh-watch>${watcher ? 'Stop' : 'Watch a folder'}</button></div><div class="hh-panel-body hh-summary-list"><div class="hh-summary" style="cursor:default"><span class="art" style="background:${watcher ? 'var(--positive-soft)' : 'var(--surface-2)'};color:${watcher ? 'var(--positive)' : 'var(--text-secondary)'}">⇩</span><span><strong>${esc(watcher ? watcher.folder : 'Not watching')}</strong><small>${esc(watcher ? (watcher.program ? `Running ${fileName(watcher.program)}` : 'Folder only') : 'New files in a watched folder show above')}</small></span>${watcher ? routineStatus('Watching') : visibleStatus('Idle', 'neutral', '–')}</div></div></section></div>`;
  if (!decisions.length && !detected.length) $('#hhIntake', host).appendChild(stateBlock({ title: 'Nothing waiting', body: 'Scan results needing a decision and new downloads land here.' }));
}
function renderHHSystems() {
  const host = $('#hhContent');
  host.innerHTML = `<div class="hh-system-grid">${systems.map((s) => `<article class="hh-system"><h2>${esc(s.name)}</h2><p>${esc(s.mine && model.app ? model.app.platform : 'Synced record')} · ${fmt.n(s.games, 'game')}</p><div class="line"><span>Installed</span><b>${s.installed}</b></div><div class="line"><span>Record</span><b style="color:var(--positive)">${s.mine ? 'This computer' : 'Synced'}</b></div></article>`).join('')}</div><section class="hh-panel" style="margin-top:10px"><div class="hh-panel-head"><h2>Library folder</h2><span class="spacer"></span><small>Shared by every system</small></div><div class="hh-panel-body"><div class="hh-setting"><strong class="mono" style="font-size:0.75rem;word-break:break-all">${esc(model.info?.sync_folder || '')}</strong><small>Pairing handhelds and copying versions between devices are not built yet.</small><button class="hh-action" data-open-path="sync_folder">Open folder</button></div></div></section>`;
  if (!systems.length) host.prepend(stateBlock({ kind: model.ready ? 'empty' : 'loading', title: 'No systems yet' }));
}
function renderHHSettings() {
  const page = state.hhSettingsPage; const host = $('#hhContent');
  const back = `<button class="hh-action" data-hh-settings-page="root">‹ Settings</button>`;
  if (page === 'root') {
    host.innerHTML = `<div class="hh-settings-menu"><button class="hh-settings-link" data-hh-settings-page="library"><span><strong>Library</strong><small>Tracked folders and scanning</small></span><span class="go">›</span></button><button class="hh-settings-link" data-hh-settings-page="capabilities"><span><strong>Plugin capabilities</strong><small>Sources, downloaders, metadata, managers, engines, mods, and patchers</small></span><span class="go">›</span></button><button class="hh-settings-link" data-hh-settings-page="plugins"><span><strong>Installed plugins</strong><small>${plugins.length} installed</small></span><span class="go">›</span></button><button class="hh-settings-link" data-hh-settings-page="sync"><span><strong>Systems & sync</strong><small>Metadata folder and game transfer</small></span><span class="go">›</span></button><button class="hh-settings-link" data-hh-settings-page="interface"><span><strong>Interface</strong><small>Theme, text size, startup shell, and library view</small></span><span class="go">›</span></button><button class="hh-settings-link" data-hh-settings-page="advanced"><span><strong>Advanced</strong><small>Diagnostics and recovery</small></span><span class="go">›</span></button></div>`;
  } else if (page === 'capabilities') {
    host.innerHTML = `<div class="hh-stack">${back}<div class="hh-settings-menu">${[['sources', 'Sources'], ['downloaders', 'Downloaders'], ['metadata', 'Metadata'], ['managers', 'Managers'], ['engines', 'Engine detection'], ['mods', 'Mods'], ['patchers', 'Patchers']].map((x) => `<button class="hh-settings-link" data-hh-settings-page="${x[0]}"><span><strong>${x[1]}</strong></span><span class="go">›</span></button>`).join('')}</div></div>`;
  } else if (['sources', 'downloaders', 'metadata', 'managers', 'engines', 'mods', 'patchers'].includes(page)) {
    const cat = { sources: 'library', downloaders: 'downloader', metadata: 'metadata', managers: 'manager', engines: 'engine', mods: 'mod', patchers: 'patcher' }[page];
    host.innerHTML = `<div class="hh-stack">${back}<div class="hh-capability-list" id="hhCapabilities"></div></div>`;
    loadInto($('#hhCapabilities', host), 'get_capabilities', [cat], (r, h) => {
      (r.plugins || []).forEach((p) => { const s = p.state || {}; const status = !p.enabled ? visibleStatus('Turned off', 'neutral', '–') : s.error ? visibleStatus('Check failed', 'critical') : cat === 'library' && s.has_account ? (s.connected ? routineStatus('Connected') : visibleStatus('Not connected', 'pending')) : cat === 'manager' ? (s.available ? routineStatus('Installed here') : visibleStatus('Not found', 'pending')) : routineStatus('Ready'); const row = el(`<div class="hh-capability"><span class="art">${esc(p.display_name.slice(0, 2).toUpperCase())}</span><span><strong>${esc(p.display_name)}</strong>${s.error ? `<small>${esc(s.error)}</small>` : ''}</span><span class="controls">${status}<button class="toggle" aria-label="Enable ${esc(p.display_name)}"><span></span></button></span></div>`); bindSwitch($('.toggle', row), p.enabled, (on) => act('set_plugin_enabled', [p.id, on], on ? 'Turned on' : 'Turned off', { what: 'Turning the plugin' }).then(() => refreshPlugins().then(renderHandheld))); h.appendChild(row); });
      if (!(r.plugins || []).length) h.appendChild(stateBlock({ title: `No ${CATEGORY_NAMES[cat].toLowerCase()} plugins` }));
    }, { what: `${CATEGORY_NAMES[cat]} plugins` });
  } else if (page === 'library') {
    host.innerHTML = `<div class="hh-stack">${back}<div class="hh-capability-list" id="hhFolders"></div><button class="hh-action primary" data-hh-scan>Scan a folder</button></div>`;
    loadInto($('#hhFolders', host), 'get_library_folders', [], (r, h) => { if (!(r.folders || []).length) h.appendChild(stateBlock({ title: 'No folders tracked' })); }, { what: 'Tracked folders' });
  } else if (page === 'plugins') {
    host.innerHTML = `<div class="hh-stack">${back}<div class="hh-capability-list">${plugins.map((p) => `<div class="hh-capability"><span class="art">${esc(p.icon)}</span><span><strong>${esc(p.name)}</strong><small>${esc(p.kind)} · ${esc(p.version)} · ${p.origin === 'Built-in' ? 'Official' : 'Unofficial'}</small></span><span class="controls">${p.enabled ? routineStatus('On') : visibleStatus('Turned off', 'neutral', '–')}<button class="toggle" data-hh-plugin="${esc(p.id)}" aria-label="Enable ${esc(p.name)}"><span></span></button></span></div>`).join('')}</div></div>`;
    $$('[data-hh-plugin]', host).forEach((t) => { const p = plugins.find((x) => x.id === t.dataset.hhPlugin); bindSwitch(t, p.enabled, (on) => act('set_plugin_enabled', [p.id, on], on ? 'Turned on' : 'Turned off', { what: 'Turning the plugin' }).then(() => refreshPlugins().then(renderHandheld))); });
    if (!plugins.length) host.appendChild(stateBlock({ title: 'No plugins found' }));
  } else if (page === 'sync') {
    host.innerHTML = `<div class="hh-stack">${back}<div class="hh-setting"><strong>Metadata folder</strong><small class="mono" style="word-break:break-all">${esc(model.info?.sync_folder || '')}</small><button class="hh-action" data-open-path="sync_folder">Open folder</button></div><div class="hh-setting"><strong>This system's ID</strong><small class="mono">${esc(model.info?.system_uuid || '')}</small></div><div class="hh-setting"><strong>Warn on metadata conflicts</strong><button class="toggle" data-option="warn_conflicts" aria-label="Warn on metadata conflicts"><span></span></button></div><div id="hhTransfer"></div></div>`;
    loadInto($('#hhTransfer', host), 'get_transfer_settings', [], () => {}, { what: 'Game transfer' });
  } else if (page === 'interface') {
    const startup = localStorage.getItem('pythia-startup-shell') || 'auto';
    host.innerHTML = `<div class="hh-stack">${back}<div class="hh-settings"><div class="hh-setting"><strong>Startup shell</strong><select id="hhStartupShell"><option value="auto" ${startup === 'auto' ? 'selected' : ''}>Auto-detect at startup</option><option value="desktop" ${startup === 'desktop' ? 'selected' : ''}>Always Desktop</option><option value="handheld" ${startup === 'handheld' ? 'selected' : ''}>Always Handheld</option><option value="last" ${startup === 'last' ? 'selected' : ''}>Remember last shell</option></select></div><div class="hh-setting"><strong>Handheld library view</strong><select id="hhLibraryDefault"><option value="list" ${state.hhLibraryMode === 'list' ? 'selected' : ''}>List</option><option value="grid" ${state.hhLibraryMode === 'grid' ? 'selected' : ''}>Cover grid</option></select></div><div class="hh-setting"><strong>Theme</strong><select id="hhTheme"><option value="ink">Ink &amp; Amber</option><option value="parchment">Parchment</option><option value="cobalt">Slate &amp; Cobalt</option></select></div><div class="hh-setting"><strong>Text size</strong><select id="hhTextScale"><option value="1">Normal</option><option value="1.15">Larger</option><option value="1.3">Large</option><option value="1.5">Largest</option></select></div><div class="hh-setting"><strong>Reduce motion</strong><button class="toggle" id="hhMotion" aria-label="Reduce motion"><span></span></button></div><div class="hh-setting"><strong>Desktop interface</strong><button class="hh-action" id="hhSettingsDesktop" style="width:100%">Open Desktop</button></div></div></div>`;
    $('#hhTheme').value = document.documentElement.dataset.theme || 'ink'; $('#hhTextScale').value = prefs.get('text-scale', '1');
    bindSwitch($('#hhMotion'), prefs.get('motion', 'on') === 'off', (on) => { prefs.set('motion', on ? 'off' : 'on'); applyInterface(); }, 'Reduce motion');
  } else {
    host.innerHTML = `<div class="hh-stack">${back}<div class="hh-setting"><strong>Pythia</strong><small>${esc(model.app ? `${model.app.version} · ${model.app.platform} · schema v${model.app.schema_version}` : '')}</small></div><div class="hh-setting"><strong>Export diagnostics</strong><small>The log file plus app, system and plugin facts.</small><button class="hh-action" data-hh-action="export-diagnostics">Export…</button></div><div class="hh-setting"><strong>Rebuild cache.db</strong><button class="hh-action" data-hh-action="rebuild-cache">Rebuild</button></div><div class="hh-setting"><strong>Error console</strong><small>${esc(model.config?.error_console ? 'On for this run.' : 'Off. Start with --error-console or set error_console = True in pythia.conf.')}</small>${model.config?.error_console ? '<button class="hh-action" data-console-toggle>Open console</button>' : ''}</div><div class="hh-setting"><strong>Reset local identity</strong><small>Gives this computer a new ID.</small><button class="hh-action" data-hh-action="reset-identity">Review reset…</button></div></div>`;
    $$('[data-console-toggle]', host).forEach((b) => (b.onclick = () => ErrorConsole.setOpen(true)));
  }
  $$('[data-option]', host).forEach((t) => bindOption(t));
}
function renderHHDetail(g) {
  if (!g) { navigateHandheld('library', false); return; }
  const host = $('#hhContent');
  host.innerHTML = `<div class="hh-stack"><section class="hh-panel"><div class="hh-panel-body"><div class="hh-decision-head"><div class="hh-decision-art art-${g.art}">${esc(initials(g.name))}</div><div><h1 style="margin:0">${esc(g.name)}</h1><div class="meta">${esc(g.source)} · ${esc(g.engine)} · ${esc(versionText(g))}</div><div style="margin-top:10px">${gameStatus(g)}</div></div></div><div class="actions" style="display:flex;gap:8px;flex-wrap:wrap;margin-top:12px" id="hhDetailActions">${g.path ? `<button class="hh-action" data-hh-open="${esc(g.path)}">Open folder</button><button class="hh-action" data-hh-rescan="${g.id}">Rescan</button>` : ''}${g.status !== 'ok' ? `<button class="hh-action" data-hh-decision="${g.id}">Review</button>` : ''}<button class="hh-action" data-hh-nav-jump="systems">Systems</button></div></div></section><section class="hh-detail-section"><h2>Overview</h2><p style="margin:0;color:var(--text-secondary);line-height:1.5">${esc(g.note || '')}</p><div class="hh-plan" style="margin-top:10px"><div class="hh-plan-row"><b>Path</b><span class="mono">${esc(g.path || 'Not installed here')}</span></div><div class="hh-plan-row"><b>Platforms</b><span>${esc(g.platforms.map(platformName).join(' · ') || '—')}</span></div><div class="hh-plan-row"><b>Systems</b><span>${esc(g.systems.join(' · '))}</span></div>${g.added ? `<div class="hh-plan-row"><b>Added</b><span>${esc(fmt.date(g.added))}</span></div>` : ''}</div></section><section class="hh-detail-section"><h2>Versions</h2><div class="hh-summary-list">${g.versions.map((v) => `<div class="hh-summary" style="cursor:default"><span class="art art-${g.art}">${esc((v.version && v.version !== g.name ? v.version : '?').slice(0, 4))}</span><span><strong>${esc(v.version && v.version !== g.name ? v.version : 'No version number')}</strong><small>${(v.variants || []).length} installed variant(s)</small></span>${routineStatus('Installed')}</div>`).join('')}${g.updates.map((u) => `<button class="hh-summary" data-hh-plan="${g.id}"><span class="art" style="background:var(--warning-soft);color:var(--warning)">＋</span><span><strong>${esc(u.version || 'Newer version')}</strong><small>${esc(u.source || '')} · ${esc(platformName(u.platform))}</small></span>${visibleStatus('Available', 'pending', '↻')}</button>`).join('')}</div></section></div>`;
  if (!g.versions.length) $('.hh-summary-list', host).appendChild(stateBlock({ title: 'Nothing installed here' }));
  call('find_manager_for_game', g.id).then((managers) => { if (!Array.isArray(managers) || !managers.length) return; const m = managers[0]; const b = el(`<button class="hh-action primary" ${m.available === false ? 'disabled' : ''}>Open in ${esc(m.manager_name)}${m.available === false ? ' · not installed' : ''}</button>`); b.onclick = async () => { if (await confirm({ title: `Open in ${m.manager_name}?`, body: `${m.manager_name} opens and shows ${g.name}.`, proceed: 'Open' })) { pythia.open_in_manager(g.id, m.manager_id); flash(`Handed to ${m.manager_name}`); } }; $('#hhDetailActions', host)?.prepend(b); });
}
function renderHHDecision(g) {
  if (!g) { navigateHandheld('attention', false); return; }
  const d = model.intake.decisions.find((x) => g.variants.some((v) => v.path === x.path));
  const choices = [];
  if (d) choices.push({ k: 'decide', primary: true, title: 'Decide on the scan result', sub: 'Link it to a game you have, or keep it as its own.' });
  if (g.updates.length) choices.push({ k: 'plan', primary: !d, title: 'Review the available update', sub: `${g.updates[0].version || 'A newer version'} was seen on ${g.updates[0].source || 'a store'}.` });
  if (g.path) choices.push({ k: 'rescan', title: 'Rescan the folder', sub: 'Identify it again with the engine plugins that are on.' });
  if (g.path) choices.push({ k: 'open', title: 'Open the folder', sub: 'See what is inside.' });
  choices.push({ k: 'remind', title: 'Remind me later', sub: 'Keep it in Needs attention.' });
  choices.push({ k: 'remove', title: 'Remove from library', sub: 'Files stay on disk.' });
  $('#hhContent').innerHTML = `<section class="hh-decision"><div class="hh-decision-head"><div class="hh-decision-art art-${g.art}">${esc(initials(g.name))}</div><div><div class="hh-state ${g.status}">${esc(g.statusText)}</div><h1>${esc(g.name)}</h1><p>${esc(g.status === 'critical' ? 'No engine plugin recognised this folder, so Pythia cannot say what it needs to run.' : g.updates.length ? 'A newer version than the installed one was seen.' : 'A scan found a folder that may be a game you already have.')}</p><div class="hh-facts"><div class="hh-fact"><small>Installed</small><strong>${esc(versionText(g))}</strong></div><div class="hh-fact"><small>Observed</small><strong>${esc(g.updates[0]?.version || g.statusText)}</strong></div><div class="hh-fact"><small>Systems</small><strong>${g.systems.length}</strong></div></div></div></div><div class="hh-choice-grid">${choices.map((c) => `<button class="hh-choice ${c.primary ? 'primary' : ''}" data-hh-choice="${c.k}"><strong>${esc(c.title)}</strong><small>${esc(c.sub)}</small></button>`).join('')}</div></section>`;
}
function renderHHPlan(g) {
  if (!g) { navigateHandheld('attention', false); return; }
  const u = g.updates[0];
  $('#hhContent').innerHTML = `<section class="hh-decision"><div class="hh-state pending">Seen, not applied</div><h1 style="margin:5px 0 0;font:500 1.625rem var(--serif)">Available update · ${esc(g.name)}</h1><div class="hh-plan">${u ? `<div class="hh-plan-row"><b>Installed</b><span>${esc(versionText(g))}</span></div><div class="hh-plan-row"><b>Available</b><span>${esc(u.version || '?')} · ${esc(platformName(u.platform))}</span></div><div class="hh-plan-row"><b>Seen via</b><span>${esc(u.source || '?')}${u.detected_at ? ` on ${esc(fmt.date(u.detected_at))}` : ''}</span></div><div class="hh-plan-row"><b>Files</b><span>Pythia records updates; it does not download or apply them yet.</span></div>` : '<div class="hh-plan-row"><b>Update</b><span>No update is recorded for this game.</span></div>'}</div><div class="actions" style="display:flex;justify-content:flex-end;gap:8px;margin-top:14px"><button class="hh-action" data-hh-plan-back>Back</button><button class="hh-action primary" data-hh-apply ${u ? '' : 'disabled'}>Apply update</button></div></section>`;
}
function bindHandheldActions() {
  const host = $('#handheldShell');
  $$('[data-hh-nav-jump]', host).forEach((b) => (b.onclick = () => navigateHandheld(b.dataset.hhNavJump)));
  $$('[data-hh-game]', host).forEach((b) => (b.onclick = () => { state.hhHistory.push(state.hhPage); state.hhSelected = b.dataset.hhGame; state.hhPage = 'detail'; renderHandheld(); }));
  $$('[data-hh-decision]', host).forEach((b) => (b.onclick = () => { state.hhHistory.push(state.hhPage); state.hhSelected = b.dataset.hhDecision; state.hhPage = 'decision'; renderHandheld(); }));
  $$('[data-hh-plan]', host).forEach((b) => (b.onclick = () => { state.hhHistory.push(state.hhPage); state.hhSelected = b.dataset.hhPlan; state.hhPage = 'plan'; renderHandheld(); }));
  $$('[data-hh-library-mode]', host).forEach((b) => (b.onclick = () => { state.hhLibraryMode = b.dataset.hhLibraryMode; localStorage.setItem('pythia-handheld-library', state.hhLibraryMode); renderHandheld(); }));
  $('#hhSourceFilter')?.addEventListener('change', (e) => { state.hhSource = e.target.value; renderHandheld(); });
  $$('[data-hh-settings-page]', host).forEach((b) => (b.onclick = () => { state.hhSettingsPage = b.dataset.hhSettingsPage; renderHandheld(); }));
  $$('[data-hh-choice]', host).forEach((b) => (b.onclick = async () => {
    const g = games.find((x) => x.id === state.hhSelected); const k = b.dataset.hhChoice;
    if (k === 'decide') { exitHandheld(); navigate('reconcile'); }
    else if (k === 'plan') { state.hhHistory.push('decision'); state.hhPage = 'plan'; renderHandheld(); }
    else if (k === 'rescan') rescanHH(g);
    else if (k === 'open') act('open_path', [g.path], null, { what: 'Open folder' });
    else if (k === 'remind') { flash('Kept in Needs attention'); navigateHandheld('attention', false); }
    else if (k === 'remove') { if (await confirmDanger({ title: `Remove ${g.name}?`, body: 'Its record leaves the library. Files on disk stay where they are.', recommendation: 'We recommend Cancel unless the entry is wrong.', items: g.path ? [g.path] : [], proceed: 'Remove' })) { await act('forget_game', [g.id], 'Removed from library', { what: 'Remove' }); navigateHandheld('attention', false); } }
  }));
  $$('[data-hh-open]', host).forEach((b) => (b.onclick = () => act('open_path', [b.dataset.hhOpen], null, { what: 'Open folder' })));
  $$('[data-hh-rescan]', host).forEach((b) => (b.onclick = () => rescanHH(games.find((x) => x.id === b.dataset.hhRescan))));
  $$('[data-hh-onboard]', host).forEach((b) => (b.onclick = () => { pythia.start_onboard(b.dataset.hhOnboard); pythia.forget_detected(b.dataset.hhOnboard, () => {}); flash('Adding'); }));
  $$('[data-hh-review]', host).forEach((b) => (b.onclick = () => { exitHandheld(); navigate('reconcile'); }));
  $$('[data-hh-watch]', host).forEach((b) => (b.onclick = () => (model.intake.watcher ? stopWatching() : watchFolder())));
  $$('[data-hh-add]', host).forEach((b) => (b.onclick = addFolder));
  $$('[data-hh-scan]', host).forEach((b) => (b.onclick = scanLibrary));
  $$('[data-hh-jobs]', host).forEach((b) => (b.onclick = () => infoSheet({ title: 'Jobs', details: model.jobs.map((j) => `${j.state.toUpperCase()} ${j.title}: ${j.summary || j.detail}`).join('\n') })));
  $$('[data-hh-action]', host).forEach((b) => (b.onclick = () => action(b.dataset.hhAction)));
  $$('[data-open-path]', host).forEach((b) => (b.onclick = () => openKnownPath(b.dataset.openPath)));
  $('[data-hh-plan-back]')?.addEventListener('click', () => { state.hhPage = 'decision'; renderHandheld(); });
  $('[data-hh-apply]')?.addEventListener('click', async () => { const g = games.find((x) => x.id === state.hhSelected); const r = await call('apply_update', g.id, g.updates[0]?.version || ''); infoSheet({ title: 'Apply update', details: ok(r) ? 'Started' : r.message }); });
  $('#hhStartupShell')?.addEventListener('change', (e) => { localStorage.setItem('pythia-startup-shell', e.target.value); const desktop = $('#startupShellSelect'); if (desktop) desktop.value = e.target.value; });
  $('#hhLibraryDefault')?.addEventListener('change', (e) => { state.hhLibraryMode = e.target.value; localStorage.setItem('pythia-handheld-library', e.target.value); });
  $('#hhTheme')?.addEventListener('change', (e) => applyTheme(e.target.value));
  $('#hhTextScale')?.addEventListener('change', (e) => { prefs.set('text-scale', e.target.value); applyInterface(); });
  $('#hhSettingsDesktop')?.addEventListener('click', exitHandheld);
}
function rescanHH(g) { if (!g || !g.path) return; pythia.start_onboard(g.path); flash('Rescanning'); navigateHandheld('home', false); }
function moveHandheldFocus(direction) {
  const items = $$('#handheldShell button:not([disabled]),#handheldShell input:not([disabled]),#handheldShell select:not([disabled])').filter((x) => x.offsetParent !== null); if (!items.length) return;
  const current = document.activeElement; const from = current && items.includes(current) ? current : items[0]; const r = from.getBoundingClientRect(); const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
  let best = null, bestScore = Infinity;
  for (const x of items) { if (x === from) continue; const q = x.getBoundingClientRect(), xx = q.left + q.width / 2, y = q.top + q.height / 2, dx = xx - cx, dy = y - cy; const valid = direction === 'left' ? dx < 0 && Math.abs(dx) >= Math.abs(dy) * .35 : direction === 'right' ? dx > 0 && Math.abs(dx) >= Math.abs(dy) * .35 : direction === 'up' ? dy < 0 && Math.abs(dy) >= Math.abs(dx) * .35 : dy > 0 && Math.abs(dy) >= Math.abs(dx) * .35; if (!valid) continue; const primary = direction === 'left' || direction === 'right' ? Math.abs(dx) : Math.abs(dy), cross = direction === 'left' || direction === 'right' ? Math.abs(dy) : Math.abs(dx), score = primary + cross * 2.2; if (score < bestScore) { best = x; bestScore = score; } }
  (best || from).focus();
}

// ---- Menu bar, theme, interface preferences ----------------------------------------------
function closeAllMenus() { $$('.menu-dropdown').forEach((d) => d.classList.add('hidden')); }
function toggleMenu(name) { const d = $(`#menu-${name}`); const willShow = d.classList.contains('hidden'); closeAllMenus(); if (willShow) d.classList.remove('hidden'); }
async function menuAction(a) {
  closeAllMenus();
  if (a === 'exit') { if (await confirm({ title: 'Quit Pythia?', body: 'Running jobs stop.', proceed: 'Quit' })) pythia.quit_app(); }
  if (a === 'preferences') navigate('settings');
  if (a === 'rename') { const g = games.find((x) => x.id === state.selected); if (g) detailAction('rename', g); }
  if (a === 'notes') { navigate('library'); openTab('notes'); }
  if (a === 'handheld') enterHandheld('home');
  if (a === 'refresh-all') { const r = await call('sync_all_accounts'); if (ok(r)) { flash(r.started.length ? `Syncing ${fmt.n(r.started.length, 'source')}` : 'No connected source to refresh', r.started.length ? 'info' : 'caution'); if (r.started.length) navigate('activity'); } else flash(`Refresh failed: ${r.message}`, 'caution'); }
  if (a === 'check-updates') { pythia.check_updates(); flash('Checking for updates'); navigate('activity'); }
  if (a === 'duplicates') { navigate('reconcile'); $('#mergePanel').scrollIntoView({ behavior: 'smooth', block: 'start' }); }
  if (a === 'rebuild-cache') act('rebuild_index', [], 'Index rebuilt', { what: 'Rebuilding the index' });
  if (a === 'tools') { const r = await call('get_tools'); infoSheet({ title: 'Downloadable tools', node: fromResult(r, { what: 'Downloadable tools' }) }); }
  if (a === 'about') { const app = model.app || {}; infoSheet({ title: 'About Pythia', details: [`Pythia ${app.version || ''}`, app.platform || '', `Python ${app.python || ''}`, `Schema v${app.schema_version || ''}`, '', 'Pythia is largely based on Lutris. License: GPL-3.0-or-later.', `Log file: ${app.log_file || ''}`, `Config: ${app.config_file || ''}`].join('\n') }); }
  if (a === 'issue') act('open_url', [model.app?.issues_url || 'https://github.com/bi0shacker001/pythia/issues'], 'Opened the issue tracker', { what: 'Opening the browser' });
  if (a === 'docs') act('open_url', [model.app?.docs_url || 'https://github.com/bi0shacker001/pythia#readme'], 'Opened the documentation', { what: 'Opening the browser' });
}
const prefs = { get(k, d) { try { return localStorage.getItem(`pythia-${k}`) ?? d; } catch (e) { return d; } }, set(k, v) { try { localStorage.setItem(`pythia-${k}`, v); } catch (e) { /* storage may be unavailable */ } } };
function applyTheme(theme) { const next = ['ink', 'parchment', 'cobalt'].includes(theme) ? theme : 'ink'; document.documentElement.dataset.theme = next; prefs.set('theme', next); const select = $('#themeSelect'); if (select) select.value = next; $$('[data-theme-choice]').forEach((b) => b.classList.toggle('active', b.dataset.themeChoice === next)); }
function applyInterface() {
  const scale = parseFloat(prefs.get('text-scale', '1')) || 1;
  document.documentElement.style.setProperty('--text-scale', String(scale));
  document.documentElement.dataset.largeText = scale >= 1.3 ? '1' : '0';
  document.documentElement.dataset.motion = prefs.get('motion', 'on') === 'off' ? 'off' : 'on';
  const sel = $('#textScaleSelect'); if (sel) sel.value = String(scale);
  const dens = state.density; document.documentElement.style.setProperty('--row-pad', dens === 'compact' ? '7px' : dens === 'spacious' ? '14px' : '10px'); $$('[data-density]').forEach((x) => x.classList.toggle('active', x.dataset.density === dens)); const ds = $('#densitySelect'); if (ds) ds.value = dens;
}
function toggleThemePopover(force) { const pop = $('#themePopover'); const show = typeof force === 'boolean' ? force : pop.classList.contains('hidden'); pop.classList.toggle('hidden', !show); }
// Options with no backend yet: the switch asks the backend, which answers "unavailable", and the switch goes back.
function bindOption(t) { bindSwitch(t, false, async (on, node) => { const r = await call('set_option', t.dataset.option, on ? 'true' : 'false'); if (!ok(r)) { node.setAttribute('aria-checked', 'false'); node.classList.remove('on'); flash(r.status === 'unavailable' ? r.message : `Could not change it: ${r.message}`, 'caution'); } else flash('Saved'); }); }
function openKnownPath(key) { const info = model.info || {}; const app = model.app || {}; const path = key === 'log_file_dir' ? String(app.log_file || '').replace(/[\\/][^\\/]*$/, '') : info[key]; act('open_path', [path || ''], null, { what: 'Open folder' }); }

// ---- Chrome: counts, the computer card, the sync indicator, the status bar -------------------
function renderChrome() {
  const attention = games.filter((g) => g.status !== 'ok').length;
  const running = model.jobs.filter((j) => j.state === 'running').length;
  const { detected, decisions } = model.intake;
  const merges = Array.isArray(model.merges) ? model.merges.filter((c) => !dismissed(c)).length : 0;
  const set = (id, n) => { const x = $(id); if (x) x.textContent = n ? String(n) : ''; };
  set('#allCount', games.length); set('#attentionCount', attention); set('#downloadsCount', detected.length + decisions.length); set('#reconcileCount', decisions.length + merges); set('#activityCount', running);
  const jc = $('#jobsCount'); jc.textContent = running ? String(running) : ''; jc.classList.toggle('hidden', !running);
  $('#sidebarProgress').classList.toggle('hidden', !running);
  const mine = games.filter((g) => g.mine && g.installed).length;
  $('#sidebarSystemText').innerHTML = model.ready ? `${esc(model.app?.platform || '')}<br>${esc(fmt.n(mine, 'install'))} here · ${esc(fmt.n(decisions.length, 'decision'))} waiting` : 'Connecting to Pythia…';
  const nSystems = new Set(games.flatMap((g) => g.systemIds)).size || 1;
  $('#statusRight').textContent = model.ready ? `${fmt.n(games.length, 'game')} · ${fmt.n(nSystems, 'system')}` : '';
  $('#statusSyncLabel').textContent = !pythia ? 'Not connected' : nSystems > 1 ? `Synced across ${nSystems} systems` : 'This system only';
  const ind = $('#syncIndicator');
  if (!pythia) ind.innerHTML = `${visibleStatus('Not connected', 'critical')}`;
  else ind.innerHTML = `${routineStatus('Library read')}<span id="syncLabel">${model.gamesAt ? esc(fmt.ago(model.gamesAt)) : '…'}</span>`;
}
setInterval(() => { const l = $('#syncLabel'); if (l && model.gamesAt) l.textContent = fmt.ago(model.gamesAt); }, 15000);

// ---- Backend bridge: connect, signals, initial loads ------------------------------------------
function refreshGames() {
  if (!pythia) return;
  model.raw = pythia.games || [];
  games = model.raw.map(adaptGame);
  model.gamesAt = new Date().toISOString();
  populateFilterOptions(); updateSystems(model.raw); renderChrome();
  if (state.shell === 'handheld') renderHandheld(); else if (['library', 'systems', 'reconcile'].includes(state.view)) renderCurrent();
}
function populateFilterOptions() {
  const fill = (sel, values, allLabel) => { const current = sel.value; sel.innerHTML = `<option value="all">${allLabel}</option>` + values.map((v) => `<option value="${esc(v)}">${esc(v)}</option>`).join(''); sel.value = values.includes(current) ? current : 'all'; };
  fill($('#sourceFilter'), [...new Set(games.map((g) => g.source))].sort(), 'All sources');
  fill($('#engineFilter'), [...new Set(games.map((g) => g.engine))].sort(), 'All engines');
}
function refreshPlugins() { return call('get_all_plugins').then((p) => { if (Array.isArray(p)) { model.plugins = p; plugins = p.map(adaptPlugin); } return call('get_account_plugins'); }).then((a) => { if (Array.isArray(a)) model.accounts = a; if (state.view === 'settings' && state.settingsPane === 'plugins') renderPlugins(); }); }
function connect() {
  if (!(window.qt && qt.webChannelTransport)) { renderChrome(); renderNoBackend(); return; }
  new QWebChannel(qt.webChannelTransport, (channel) => {
    pythia = channel.objects.pythia;
    window.pythia = pythia;
    pythia.gamesChanged.connect(refreshGames);
    pythia.logLine.connect(() => { if (state.view === 'activity') renderJobs(); });
    pythia.uiLog.connect((e) => { model.events.push(e); if (model.events.length > 500) model.events.shift(); if (state.view === 'activity') renderJobs(); });
    pythia.jobsChanged.connect((jobs) => { model.jobs = jobs || []; renderChrome(); if (state.view === 'activity' || state.view === 'downloads') renderCurrent(); if (state.shell === 'handheld' && state.hhPage === 'home') renderHandheld(); });
    pythia.intakeChanged.connect((i) => { model.intake = { detected: i.detected || [], decisions: i.decisions || [], watcher: i.watcher || null }; games = model.raw.map(adaptGame); renderChrome(); if (['downloads', 'reconcile', 'library'].includes(state.view)) renderCurrent(); if (state.shell === 'handheld') renderHandheld(); });
    pythia.decisionNeeded.connect((d) => flash(`${d.name || fileName(d.path)} needs a decision`, 'caution'));
    pythia.onboardComplete.connect((r) => flash(r.status === 'ok' ? `Added ${r.name || ''}`.trim() : `Not added: ${r.message}`, r.status === 'ok' ? 'info' : 'caution'));
    pythia.scanComplete.connect((results) => { const okN = (results || []).filter((r) => r.status === 'ok').length; flash(`Scan finished: ${fmt.n(okN, 'game')} found`); pythia.refresh_merge_candidates(); });
    pythia.downloadDetected.connect((path) => flash(`New file: ${fileName(path)}`));
    pythia.mergeCandidatesReady.connect((c) => { model.merges = c || []; renderChrome(); if (state.view === 'reconcile') renderReconcile(); });
    pythia.mergeComplete.connect((r) => { flash(r.status === 'ok' ? 'Records folded' : `Not folded: ${r.message}`, r.status === 'ok' ? 'info' : 'caution'); model.merges = null; if (state.view === 'reconcile') renderReconcile(); pythia.refresh_merge_candidates(); });
    pythia.accountSyncComplete.connect((pluginId, r) => { flash(r.status === 'ok' ? `Synced: ${r.linked} linked, ${r.created} new` : `Sync failed: ${r.message}`, r.status === 'ok' ? 'info' : 'caution'); refreshPlugins(); });
    pythia.updateCheckComplete.connect((r) => flash(r.status === 'ok' ? `Update check: ${r.checked} checked, ${r.updated} with an update` : `Update check failed: ${r.message}`, r.status === 'ok' ? 'info' : 'caution'));
    pythia.platformEnableComplete.connect((r) => { flash(r.status === 'ok' || !r.status ? `${platformName(r.platform)} prepared` : `Could not prepare: ${r.message}`, r.status === 'ok' || !r.status ? 'info' : 'caution'); if (state.view === 'library') renderLibrary(); });
    pythia.get_system_uuid((uuid) => {
      model.mySystem = uuid;
      Promise.all([call('get_system_info'), call('get_app_info'), call('get_ui_config'), call('get_jobs'), call('get_intake'), call('get_ui_log'), refreshPlugins()]).then(([info, app, config, jobs, intake, log]) => {
        if (ok(info)) model.info = info; if (ok(app)) model.app = app; if (ok(config)) model.config = config;
        if (ok(jobs)) model.jobs = jobs.jobs || []; if (ok(intake)) model.intake = { detected: intake.detected || [], decisions: intake.decisions || [], watcher: intake.watcher || null };
        if (ok(log)) model.events = log.entries || [];
        if (model.config?.error_console) ErrorConsole.enable(pythia);
        model.ready = true;
        refreshGames();
        renderCurrent();
        if (state.shell === 'handheld') renderHandheld();
        pythia.refresh_merge_candidates();
      });
    });
  });
}
function renderNoBackend() {
  $$('.view').forEach((v) => { v.innerHTML = ''; v.appendChild(stateBlock({ kind: 'error', title: 'Not connected to Pythia', body: 'This page needs Pythia running behind it. Start Pythia with --ui web.' })); });
  $('#hhContent').appendChild(stateBlock({ kind: 'error', title: 'Not connected to Pythia', body: 'Start Pythia with --ui web.' }));
  ErrorConsole.reportWeb('error', 'No QWebChannel transport: the page is not running inside Pythia', 'bridge');
}

// ---- Setup -------------------------------------------------------------------------------------
function setup() {
  applyTheme(prefs.get('theme', 'ink')); applyInterface();
  ErrorConsole.init();
  updateBulkToolbar();
  $$('[data-nav]').forEach((b) => b.addEventListener('click', () => navigate(b.dataset.nav)));
  $$('[data-filter-nav]').forEach((b) => (b.onclick = () => { state.filterStatus = b.dataset.filterNav; navigate('library'); renderLibrary(); }));
  $$('[data-action]').forEach(bindAction);
  $$('[data-open-path]:not(#handheldShell [data-open-path])').forEach((b) => (b.onclick = () => openKnownPath(b.dataset.openPath)));
  $$('[data-option]:not(#handheldShell [data-option])').forEach(bindOption);
  $$('[data-option-action]').forEach((b) => (b.onclick = async () => { const r = await call(b.dataset.optionAction, ''); infoSheet({ title: b.textContent.replace(/…$/, ''), node: fromResult(r, { what: b.textContent.replace(/…$/, '') }) }); }));
  $('#backButton').onclick = () => { const prev = state.history.pop(); if (prev) navigate(prev, false); $('#backButton').disabled = !state.history.length; };
  $('#globalSearch').addEventListener('input', (e) => { state.search = e.target.value; renderCurrent(); });
  document.addEventListener('keydown', (e) => {
    if (state.shell === 'handheld') { if ($('.scrim')) return; if (['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(e.key)) { e.preventDefault(); moveHandheldFocus(e.key.replace('Arrow', '').toLowerCase()); } if (e.key === 'Escape') { e.preventDefault(); if (state.hhHistory.length) { state.hhPage = state.hhHistory.pop(); renderHandheld(); } else exitHandheld(); } return; }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); $('#globalSearch').focus(); }
  });
  $('#sourceFilter').onchange = (e) => { state.source = e.target.value; renderLibrary(); };
  $('#engineFilter').onchange = (e) => { state.engine = e.target.value; renderLibrary(); };
  $('#sortSelect').onchange = (e) => { state.sort = e.target.value; renderLibrary(); };
  $('#clearFilters').onclick = () => { state.source = 'all'; state.engine = 'all'; state.filterStatus = 'all'; state.search = ''; $('#globalSearch').value = ''; $('#sourceFilter').value = 'all'; $('#engineFilter').value = 'all'; renderLibrary(); };
  $$('[data-library-mode]').forEach((b) => (b.onclick = () => { state.libraryMode = b.dataset.libraryMode; localStorage.setItem('pythia-desktop-library', state.libraryMode); $('#desktopLibrarySelect').value = state.libraryMode; renderLibrary(); }));
  $$('[data-density]').forEach((b) => (b.onclick = () => { state.density = b.dataset.density; prefs.set('density', state.density); applyInterface(); }));
  $$('[data-settings]').forEach((b) => (b.onclick = () => openSettingsPane(b.dataset.settings)));
  $('#themeButton').onclick = (e) => { e.stopPropagation(); toggleThemePopover(); };
  $$('[data-theme-choice]').forEach((b) => (b.onclick = () => { applyTheme(b.dataset.themeChoice); toggleThemePopover(false); flash(`Theme: ${b.textContent.trim()}`, 'info', { private: true }); }));
  $('#themeSelect').onchange = (e) => applyTheme(e.target.value);
  $('#textScaleSelect').onchange = (e) => { prefs.set('text-scale', e.target.value); applyInterface(); };
  $('#densitySelect').onchange = (e) => { state.density = e.target.value; prefs.set('density', state.density); applyInterface(); };
  bindSwitch($('#motionToggle'), prefs.get('motion', 'on') === 'off', (on) => { prefs.set('motion', on ? 'off' : 'on'); applyInterface(); }, 'Reduce motion');
  $('#uiModeSelect').onchange = (e) => { pythia.set_ui_mode(e.target.value); flash('Saved'); };
  $('#logLevelSelect').onchange = (e) => act('set_log_level', [e.target.value], 'Log level saved', { what: 'Log level' });
  document.addEventListener('click', (e) => { if (!e.target.closest('#themePopover') && !e.target.closest('#themeButton')) toggleThemePopover(false); if (!e.target.closest('#addPopover') && !e.target.closest('#addButton')) { $('#addPopover').classList.add('hidden'); $('#addButton').setAttribute('aria-expanded', 'false'); } if (!e.target.closest('.menu-item')) closeAllMenus(); });
  $('#recentButton').onclick = (e) => { e.stopPropagation(); const existing = $('.recent-popover'); if (existing) { existing.remove(); return; } const pop = el('<div class="recent-popover" role="dialog" aria-label="Recent"></div>'); if (!flash.recent.length) pop.appendChild(stateBlock({ title: 'Nothing recent', body: 'Acknowledgements show here for a while.' })); else flash.recent.forEach((r) => pop.appendChild(el(`<div class="line"><span>${esc(r.text)}</span><time>${esc(fmt.time(r.at))}</time></div>`))); document.body.appendChild(pop); const close = (ev) => { if (!pop.contains(ev.target)) { pop.remove(); document.removeEventListener('mousedown', close); } }; setTimeout(() => document.addEventListener('mousedown', close), 0); };
  $('#enterHandheldButton').onclick = () => enterHandheld('home'); $('#exitHandheldRail').onclick = exitHandheld; $('#exitHandheldTop').onclick = exitHandheld;
  $$('[data-hh-nav]').forEach((b) => (b.onclick = () => navigateHandheld(b.dataset.hhNav)));
  $('#hhBack').onclick = () => { if (state.hhPage === 'settings' && state.hhSettingsPage !== 'root') { state.hhSettingsPage = 'root'; renderHandheld(); return; } if (state.hhHistory.length) { state.hhPage = state.hhHistory.pop(); renderHandheld(); } else if (['detail', 'decision', 'plan'].includes(state.hhPage)) navigateHandheld('library', false); };
  $('#hhSearch').oninput = (e) => { state.hhSearch = e.target.value; renderHandheld(); };
  $('#hhAdd').onclick = addFolder;
  $$('[data-bulk-action]').forEach((b) => (b.onclick = () => bulkAction(b.dataset.bulkAction)));
  $('#selectionModeButton').onclick = () => { state.selectionMode = !state.selectionMode; if (!state.selectionMode) state.bulk.clear(); updateBulkToolbar(); renderLibrary(); };
  $('#bulkSelectAll').onclick = () => { filteredGames().forEach((g) => state.bulk.add(g.id)); updateBulkToolbar(); renderLibrary(); };
  $('#bulkClear').onclick = () => { state.bulk.clear(); state.selectionMode = false; updateBulkToolbar(); renderLibrary(); };
  $('#addButton').onclick = (e) => { e.stopPropagation(); const pop = $('#addPopover'); const show = pop.classList.contains('hidden'); pop.classList.toggle('hidden', !show); $('#addButton').setAttribute('aria-expanded', String(show)); toggleThemePopover(false); };
  $$('[data-add-choice]').forEach((b) => (b.onclick = () => { const kind = b.dataset.addChoice; $('#addPopover').classList.add('hidden'); $('#addButton').setAttribute('aria-expanded', 'false'); closeAllMenus(); if (kind === 'scan') scanLibrary(); else if (kind === 'download') navigate('downloads'); else if (kind === 'folder') addFolder(); else importSource(); }));
  $('#refreshMerges').onclick = () => { model.merges = null; renderReconcile(); pythia.refresh_merge_candidates(); };
  const startupPref = localStorage.getItem('pythia-startup-shell') || 'auto'; $('#startupShellSelect').value = startupPref; $('#desktopLibrarySelect').value = state.libraryMode; $('#handheldLibrarySelect').value = state.hhLibraryMode;
  $('#startupShellSelect').onchange = (e) => localStorage.setItem('pythia-startup-shell', e.target.value);
  $('#desktopLibrarySelect').onchange = (e) => { state.libraryMode = e.target.value; localStorage.setItem('pythia-desktop-library', e.target.value); renderLibrary(); };
  $('#handheldLibrarySelect').onchange = (e) => { state.hhLibraryMode = e.target.value; localStorage.setItem('pythia-handheld-library', e.target.value); };
  $$('[data-menu]').forEach((b) => (b.onclick = (e) => { e.stopPropagation(); toggleMenu(b.dataset.menu); }));
  $$('[data-menu-action]').forEach((b) => (b.onclick = () => menuAction(b.dataset.menuAction)));
  $$('[data-menu-nav]').forEach((b) => (b.onclick = () => { if (b.dataset.filter) state.filterStatus = b.dataset.filter; navigate(b.dataset.menuNav); closeAllMenus(); }));
  $$('[data-menu-density]').forEach((b) => (b.onclick = () => { $$('[data-density]').find((x) => x.dataset.density === b.dataset.menuDensity)?.click(); closeAllMenus(); }));
  $$('[data-menu-theme]').forEach((b) => (b.onclick = () => { applyTheme(b.dataset.menuTheme); closeAllMenus(); }));
  $$('[data-settings-nav]').forEach((b) => (b.onclick = () => { openSettingsPane(b.dataset.settingsNav); closeAllMenus(); }));
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeAllMenus(); });
  renderChrome(); renderLibrary();
  if (resolveInitialShell() === 'handheld') enterHandheld('home');
  connect();
}
setup();

// A read-only view of the model for the screenshot driver and the console; never written to.
window.pythiaDebug = () => ({ ready: model.ready, connected: !!pythia, games: games.length, attention: games.filter((g) => g.status !== 'ok').length, jobs: model.jobs.length, decisions: model.intake.decisions.length, merges: Array.isArray(model.merges) ? model.merges.length : model.merges, events: model.events.length, errorConsole: !!model.config?.error_console, view: state.view, shell: state.shell });
window.pythiaUI = { navigate, enterHandheld, exitHandheld, navigateHandheld, openSettingsPane, openTab, state };
})();
