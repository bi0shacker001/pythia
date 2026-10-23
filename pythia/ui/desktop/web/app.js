/* Pythia web UI: the page logic (moved out of index.html's inline script on
   2026-10-14 and wired to pythia.ui.backend.Backend over QWebChannel).
   Every page reads the backend through one path (call -> result -> draw);
   a page whose feature has no backend yet still calls its slot and draws the
   "unavailable" answer, so when the slot gains a body nothing here changes.
   Nothing is sampled or invented: no placeholder rows, no counts the backend
   did not report, no toast that claims work was done. See README.md. */
(() => {
'use strict';
const { esc, el, $, $$, notice, confirm, confirmDanger, prompt, infoSheet, flash, stateBlock, fromResult, bindSwitch, menu, fmt, cycleRow } = window.UI;

// ---- Vocabulary: the backend's `kind` is a store or an engine. One name per thing. ----
const SOURCE_KINDS = { steam: 'Steam', gog: 'GOG', egs: 'Epic Games', itch: 'itch.io', f95: 'F95zone' };
const ENGINE_KINDS = { renpy: "Ren'Py", kirikiri: 'KiriKiri', unity: 'Unity', unreal: 'Unreal', godot: 'Godot', rpgmaker: 'RPG Maker', rpgmaker_mv: 'RPG Maker MV/MZ', rpgmaker_vx: 'RPG Maker VX', twine: 'Twine', august: 'AUGUST', buriko: 'BGI', catsystem2: 'CatSystem2', cmvs: 'CMVS', flash_air: 'Flash / AIR' };
const PLATFORM_NAMES = { windows: 'Windows', linux: 'Linux', macos: 'macOS', mac: 'macOS', android: 'Android', 'windows-x86_64': 'Windows', 'windows-i686': 'Windows (32-bit)', 'linux-x86_64': 'Linux', 'linux-i686': 'Linux (32-bit)', 'linux-aarch64': 'Linux (ARM)', web: 'Browser', unknown: 'Unknown' };
const CATEGORY_NAMES = { library: 'Source', engine: 'Engine', downloader: 'Downloader', manager: 'Launcher', metadata: 'Metadata', mod: 'Mods', patcher: 'Patcher', source: 'Remote source' };
const EVENT_NAMES = { store_linked: 'Store link added', store_unlinked: 'Store link removed', folder_described: 'Folder described', folder_linked: 'Folder linked to a version', current_set: 'Current version set', game_added: 'Added to the library', version_added: 'Version added', ownership_recorded: 'Ownership recorded', merged: 'Folded in another record', renamed: 'Renamed', update_seen: 'Update seen', f95_update_detected: 'Update seen on F95zone', linked_cross_system: 'Linked to another computer\u2019s record' };
const platformName = (p) => PLATFORM_NAMES[p] || p;
const kindName = (k) => ENGINE_KINDS[k] || SOURCE_KINDS[k] || (k && k !== 'unknown' ? k : null);
const systemName = (uuid) => (uuid === model.mySystem ? 'This computer' : `Computer ${String(uuid || '').slice(0, 8)}`);

// ---- Model: what the backend reported, nothing else ----------------------------------
const model = { managers: {}, storeUrls: {}, games: [], raw: [], plugins: [], accounts: [], info: null, app: null, config: null, mySystem: null, jobs: [], intake: { detected: [], decisions: [], watcher: null }, merges: null, events: [], capabilities: {}, platformStatus: {}, gamesAt: null, ready: false };
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
      pythia[name](...args, (r) => {
        if (r === undefined || r === null) return resolve({ status: 'error', slot: name, message: 'Pythia did not answer.' });
        // A slow slot answers "pending" at once; its result arrives as asyncResult with the same request id.
        if (r.status === 'pending' && r.request) return awaitAsync(r.request, resolve);
        resolve(r);
      });
    } catch (e) {
      ErrorConsole.reportWeb('error', `${name}: ${e.message}`, 'bridge');
      resolve({ status: 'error', slot: name, message: e.message });
    }
  });
}
const asyncWaiting = new Map(); // request id -> resolve
const asyncEarly = new Map(); // request id -> a result that arrived before its call's answer
function awaitAsync(request, resolve) {
  if (asyncEarly.has(request)) { resolve(asyncEarly.get(request)); asyncEarly.delete(request); return; }
  asyncWaiting.set(request, resolve);
}
function asyncArrived(r) {
  if (!r || !r.request) return;
  const resolve = asyncWaiting.get(r.request);
  if (resolve) { asyncWaiting.delete(r.request); resolve(r); } else asyncEarly.set(r.request, r);
}
const ok = (r) => r && r.status === 'ok';
// A void call whose result only needs acknowledging: flash on success, a caution flash with the reason otherwise.
async function act(name, args, done, { what } = {}) {
  const r = await call(name, ...args);
  if (ok(r)) { if (done) flash(done); return r; }
  flash(r.status === 'unavailable' ? `${what || 'That'} is not available: ${r.message}` : `${what || 'That'} failed: ${r.message}`, 'caution');
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
// Versions newest first by droidtop's comparison (versions.js), the latest installed, and Current.
function versionModel(own, name) {
  const groups = (own.versions || []).map((v) => ({ ...v, label: v.version && v.version !== name ? v.version : '' }));
  const r = PythiaVersions.order(groups);
  const current = own.current_version && groups.some((v) => v.version === own.current_version) ? own.current_version : null;
  return { versions: r.list, latest: r.latest, orderKnown: r.ordered, unorderedLabels: r.unknown, current, version: current || (r.ordered ? r.latest : null) };
}
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
  if (installed && unknown && !engineKind) { status = 'neutral'; statusText = 'Not identified'; }
  if (updates.length) { status = 'pending'; statusText = 'Update available'; }
  if (pendingDecision) { status = 'pending'; statusText = 'Needs a decision'; }
  const platforms = [...new Set(variants.flatMap((v) => v.platforms || []))];
  const lastPlayed = Math.max(0, ...variants.map((v) => Number(v.lastplayed) || 0)) * 1000;
  const playtime = variants.reduce((n, v) => n + (parseFloat(v.playtime) || 0), 0);
  const systemIds = [...new Set(entries.map((e) => e.system_uuid))];
  return {
    id: real.game_id, name, own, entries, variants,
    source: sourceKind ? SOURCE_KINDS[sourceKind] : (ownedOn.length ? kindName(ownedOn[0]) : (installed ? 'Local folder' : 'Not installed')),
    engine: engineKind ? ENGINE_KINDS[engineKind] : (installed ? 'Not identified' : 'Unknown'),
    engineKind, sourceKind, kinds,
    ...versionModel(own, name),
    parts: [...new Set(variants.map((v) => v.part).filter(Boolean))].sort((a, b) => a.localeCompare(b, undefined, { numeric: true })),
    partsInstalled: variants.filter((v) => v.part).every((v) => v.path),
    status, statusText, installed, ownedOn, knownOn, updates, added, platforms, unidentified: !!(installed && unknown && !engineKind),
    updated: own.updated_at ? Date.parse(own.updated_at) || 0 : 0,
    art: ART_CLASSES[strHash(real.game_id) % ART_CLASSES.length],
    cover: real.cover || '', lastPlayed, playtime,
    systems: systemIds.map(systemName), systemIds,
    path: variants.find((v) => v.path)?.path || '',
    note: own.notes || '',
    events: own.timeline_events || [],
    managed: !!own.managed_externally,
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

let state = { view: 'library', history: [], selected: null, filterStatus: 'all', density: localStorage.getItem('pythia-density') || 'comfortable', libraryMode: localStorage.getItem('pythia-desktop-library') || 'list', search: '', source: 'all', engine: 'all', sort: 'name', bulk: new Set(), selectionMode: false, shell: 'desktop', hhPage: 'home', hhHistory: [], hhLibraryMode: localStorage.getItem('pythia-handheld-library') || 'list', hhSearch: '', hhSelected: null, hhSource: 'all', hhSettingsPage: 'root', hhReviewSection: '', detailTab: 'overview', settingsPane: 'general', pluginKind: '', libraryShelf: localStorage.getItem('pythia-library-shelf') || 'overview', libraryPlace: { search: '', scroll: 0 } };
// A game's art: its cover when this computer has one (pythia/covers.py), else the drawn art and its initials.
const artAttrs = (g, cls) => `class="${cls} art-${g.art}${g.cover ? ' has-cover' : ''}"${g.cover ? ` style="background-image:url('${esc(g.cover.replace(/'/g, '%27'))}')"` : ''}`;
const artLetters = (g) => (g.cover ? '' : esc(initials(g.name)));
const playedText = (g) => (g.lastPlayed ? `Played ${fmt.ago(new Date(g.lastPlayed).toISOString())}` : '');
const playtimeText = (g) => (g.playtime >= 1 ? `${Math.round(g.playtime)} h played` : g.playtime > 0 ? `${Math.max(1, Math.round(g.playtime * 60))} min played` : '');
const initials = (n) => String(n).split(/\s+/).filter(Boolean).slice(0, 2).map((x) => x[0]).join('').toUpperCase();
// List-level version-count affordance -- only worth a mention when there's more than one.
// Parts (Part1, Week 2: droidtop's segments, UX review A16) are counted apart from versions.
const partsText = (g) => `${g.parts.length} parts${g.partsInstalled ? ', all installed' : ''}`;
const versionsBadge = (g) => (g.parts.length > 1 ? (g.current || g.latest ? ` · ${partsText(g)}` : '') : g.versionCount > 1 ? ` · ${g.versionCount} versions` : '');
// What a row and the hero call the game's version (UX review A13): Current when set, else the latest installed,
// else, when the versions cannot be ordered, that the person sets Current.
// A folder's name (UX review A14): its kind, language, flags and mods ("Translation · Spanish"), else its folder name.
const KIND_LABELS = { game: 'Game', mod: 'Mod', patch: 'Patch', translation: 'Translation' };
const FLAG_LABELS = { mtl: 'Machine translation', uncensored: 'Uncensored' };
function languageName(code) { if (!code || code === 'unknown') return ''; if (code === 'mtl') return FLAG_LABELS.mtl; return (model.vocab?.languages || {})[code] || code.toUpperCase(); }
function variantName(x) {
  const parts = [];
  if (x.kind && x.kind !== 'game') parts.push(KIND_LABELS[x.kind] || x.kind);
  if (languageName(x.language)) parts.push(languageName(x.language));
  for (const f of x.flags || []) parts.push(FLAG_LABELS[f] || f);
  for (const m of x.mods || []) parts.push(m.name || m);
  return [...new Set(parts)].join(' · ') || fileName(x.path) || 'Folder';
}
// Store links (UX review A15): on the game or on one folder; only some stores have an update check.
const storeName = (platform) => (model.stores?.stores || {})[platform] || kindName(platform) || platform;
const shortId = (id) => String(id).replace(/^https?:\/\//, '');
function storeLinksHtml(g, path, links) {
  const checks = model.stores?.update_checks || ['f95'];
  const rows = (links || []).map((l) => `<span class="store-link"><span class="tag">${esc(storeName(l.platform))} ${esc(shortId(l.id))}</span>${checks.includes(l.platform) ? '' : ' <small>No update check for this store yet.</small>'}${g.mine ? ` <button class="row-action" type="button" data-remove-link="${esc(l.platform)}" data-link-path="${esc(path)}">Remove</button>` : ''}</span>`).join(' ');
  return `${rows || '<span class="store-none">None</span>'}${g.mine ? ` <button class="btn small" type="button" data-add-link data-link-path="${esc(path)}">Add link…</button>` : ''}`;
}
async function addStoreLink(g, path) {
  const text = await prompt({ title: path ? `Store link for ${fileName(path)}` : `Store link for ${g.name}`, body: 'An F95zone thread, an itch.io page, a Steam or GOG store page, or a DLsite work (RJ code or link).', hint: 'https://f95zone.to/threads/…', save: 'Link', pick: { label: 'Paste link', run: (done) => call('read_clipboard').then((r) => done(ok(r) ? r.text : '')) } });
  if (!text || !text.trim()) return;
  const r = await act('set_store_link', [g.id, path, text.trim()], null, { what: 'Linking' });
  if (ok(r)) flash(`Linked to ${r.link.store}${r.link.note ? `. ${r.link.note}` : ''}`);
}
function bindStoreLinks(g, host) {
  $$('[data-add-link]', host).forEach((b) => (b.onclick = () => addStoreLink(g, b.dataset.linkPath)));
  $$('[data-remove-link]', host).forEach((b) => (b.onclick = () => act('remove_store_link', [g.id, b.dataset.linkPath, b.dataset.removeLink], 'Link removed', { what: 'Removing the link' })));
}
const versionText = (g) => g.current || (g.parts.length && !g.latest ? partsText(g) : null) || (g.orderKnown ? g.latest || 'no version' : 'order unknown, set Current');
const routineStatus = (label, kind = 'ok', symbol = '✓') => `<span class="status-icon ${kind}" role="img" aria-label="${esc(label)}" title="${esc(label)}">${symbol}</span>`;
const visibleStatus = (label, kind = 'pending', symbol = '!') => `<span class="status-inline ${kind}"><span class="status-icon ${kind}" aria-hidden="true">${symbol}</span>${esc(label)}</span>`;
const gameStatus = (g) => (g.status === 'ok' ? routineStatus(g.statusText || 'No action required') : visibleStatus(g.statusText, g.status, g.unidentified ? '?' : '↻'));
const fileName = (p) => String(p || '').split(/[\\/]/).pop();

// ---- Navigation ----------------------------------------------------------------------
// Old page names (menus, links from other pages) lead to where that page went in the UI round.
const MOVED_VIEWS = { systems: 'shared', launcher: 'sources' };
function navigate(view, push = true) {
  view = MOVED_VIEWS[view] || view;
  if (view === state.view) { renderCurrent(); return; }
  if (state.view === 'library') saveLibraryPlace();
  if (push) state.history.push(state.view);
  state.view = view;
  $$('.view').forEach((v) => v.classList.toggle('active', v.id === `view-${view}`));
  $$('.nav-row[data-nav]').forEach((n) => n.classList.toggle('active', n.dataset.nav === view));
  $('#backButton').disabled = !state.history.length;
  const titles = { look: 'Search…', library: 'Search games, engines, versions, paths…', downloads: 'Search…', reconcile: 'Search possible duplicates…', devices: 'Search devices…', sources: 'Search launchers and stores…', shared: 'Search computers…', saves: 'Search save backups…', activity: 'Search jobs and events…', settings: 'Search settings…' };
  $('#globalSearch').placeholder = titles[view] || 'Search…';
  // The library keeps its place (query, selection, scroll, collection) across navigation (UX review A11).
  state.search = view === 'library' ? state.libraryPlace.search : '';
  $('#globalSearch').value = state.search;
  renderCurrent();
  window.scrollTo({ top: 0, behavior: 'instant' });
  if (view === 'library') restoreLibraryPlace();
}
function renderCurrent() {
  ({ look: () => renderLook(), library: renderLibrary, downloads: renderDownloads, reconcile: renderReconcile, devices: renderDevices, sources: renderSources, shared: renderShared, saves: renderSaves, activity: renderJobs, settings: renderSettings })[state.view]?.();
}
function filteredGames() {
  const q = PythiaQuery.parse(state.search);
  const list = games.filter((g) => {
    const matches = PythiaQuery.matches(g, q);
    const shelf = state.libraryShelf === 'installed' ? g.installed : state.libraryShelf === 'attention' ? g.status !== 'ok' : true;
    return matches && shelf && (state.filterStatus === 'all' || (state.filterStatus === 'pending' ? g.status !== 'ok' : g.status === state.filterStatus)) && (state.source === 'all' || g.source === state.source) && (state.engine === 'all' || g.engine === state.engine);
  });
  const rank = { critical: 0, pending: 1, neutral: 2, ok: 3 };
  list.sort((a, b) => (state.sort === 'status' ? (rank[a.status] - rank[b.status]) || a.name.localeCompare(b.name) : state.sort === 'updated' ? b.updated - a.updated : state.sort === 'played' ? (b.lastPlayed - a.lastPlayed) || a.name.localeCompare(b.name) : state.sort === 'added' ? String(b.added || '').localeCompare(String(a.added || '')) : a.name.localeCompare(b.name)));
  return list;
}

// ---- Library -------------------------------------------------------------------------
// Shelves (droidtop SPEC 7i, UI round): a row each for what is worth picking up, from what Pythia knows.
const SHELVES = [
  { id: 'played', title: 'Continue playing', pick: (all) => all.filter((g) => g.lastPlayed).sort((a, b) => b.lastPlayed - a.lastPlayed) },
  { id: 'added', title: 'Recently added', pick: (all) => all.filter((g) => g.added).sort((a, b) => String(b.added).localeCompare(String(a.added))) },
  { id: 'updates', title: 'Update available', pick: (all) => all.filter((g) => g.updates.length) },
  { id: 'attention', title: 'Needs attention', pick: (all) => all.filter((g) => g.status !== 'ok' && !g.updates.length), view: 'attention' },
  { id: 'installed', title: 'Installed', pick: (all) => all.filter((g) => g.installed).sort((a, b) => a.name.localeCompare(b.name)), view: 'installed' },
];
const SHELF_MAX = 12;
function capsuleHtml(g) {
  return `<button class="capsule" data-capsule="${g.id}" title="${esc(g.name)}"><span ${artAttrs(g, 'capsule-art')}>${artLetters(g)}${g.status !== 'ok' ? `<span class="game-card-attention ${g.status}" aria-label="${esc(g.statusText)}"></span>` : ''}</span><span class="capsule-plate"><strong>${esc(g.name)}</strong><small>${esc(playedText(g) || versionText(g))}</small></span></button>`;
}
function renderShelves() {
  const host = $('#libraryShelves');
  const q = PythiaQuery.parse(state.search);
  const all = games.filter((g) => PythiaQuery.matches(g, q));
  host.innerHTML = '';
  SHELVES.forEach((sh) => {
    const list = sh.pick(all);
    if (!list.length) return;
    const more = list.length > SHELF_MAX ? `<button class="btn small ghost" data-shelf-more="${sh.view || 'all'}" data-shelf-sort="${sh.id}">All ${list.length} ›</button>` : '';
    host.appendChild(el(`<section class="shelf" aria-label="${esc(sh.title)}"><div class="shelf-head"><h2>${esc(sh.title)}</h2><small>${list.length}</small><span class="spacer"></span>${more}</div><div class="shelf-row">${list.slice(0, SHELF_MAX).map(capsuleHtml).join('')}</div></section>`));
  });
  if (!host.children.length) host.appendChild(stateBlock({ title: 'Nothing matches', body: 'Try another search.' }));
  $$('[data-capsule]', host).forEach((b) => (b.onclick = () => openGame(b.dataset.capsule)));
  $$('[data-shelf-more]', host).forEach((b) => (b.onclick = () => { const sort = { played: 'played', added: 'added' }[b.dataset.shelfSort]; if (sort) { state.sort = sort; $('#sortSelect').value = sort; } setLibraryShelf(b.dataset.shelfMore); }));
}
function setLibraryShelf(shelf) { state.libraryShelf = shelf; try { localStorage.setItem('pythia-library-shelf', shelf); } catch {} renderLibrary(); }
// A capsule opens its game: the list, with the game picked and its page beside it.
function openGame(id) { state.selected = id; state.detailTab = 'overview'; if (state.libraryShelf === 'overview') state.libraryShelf = 'all'; renderLibrary(); $(`#gameList [data-game="${id}"]`)?.scrollIntoView({ block: 'nearest' }); }
function renderLibrary() {
  const overview = state.libraryShelf === 'overview' && games.length > 0;
  $$('[data-library-shelf]').forEach((b) => { b.classList.toggle('active', b.dataset.libraryShelf === state.libraryShelf); b.setAttribute('aria-selected', String(b.dataset.libraryShelf === state.libraryShelf)); });
  $('#libraryShelves').classList.toggle('hidden', !overview);
  $('#libraryLayout').classList.toggle('hidden', overview);
  if (overview && model.ready) { $('#librarySummary').textContent = `${fmt.n(games.length, 'game')} · ${games.filter((g) => g.installed).length} installed here`; $('#libraryNotice').innerHTML = ''; renderShelves(); return; }
  const list = filteredGames();

  const nSystems = new Set(games.flatMap((g) => g.systemIds)).size || 1;
  $('#librarySummary').textContent = model.ready ? `${list.length} of ${games.length} shown${nSystems > 1 ? ` · ${fmt.n(nSystems, 'computer')}` : ''}` : '';
  const noticeHost = $('#libraryNotice'); noticeHost.innerHTML = '';
  if (!model.ready) noticeHost.appendChild(stateBlock({ kind: 'loading' }));
  if (!list.some((g) => g.id === state.selected) && list[0]) state.selected = list[0].id;
  const host = $('#gameList'); const layout = $('#libraryLayout');
  host.classList.toggle('grid-mode', state.libraryMode === 'grid');
  layout.classList.toggle('grid-presentation', state.libraryMode === 'grid');
  $$('[data-library-mode]').forEach((b) => b.classList.toggle('active', b.dataset.libraryMode === state.libraryMode));
  if (!model.ready) { host.innerHTML = ''; host.appendChild(stateBlock({ kind: 'loading' })); $('#gameDetail').innerHTML = ''; return; }
  if (!games.length) { host.innerHTML = ''; host.appendChild(stateBlock({ glyph: '▦', title: 'No games yet', body: 'Add a game folder or scan a library folder; Pythia records what it finds and never moves files.', actions: [{ label: 'Add folder', primary: true, run: addFolder }, { label: 'Scan library', run: scanLibrary }] })); $('#gameDetail').innerHTML = ''; return; }
  if (!list.length) { host.innerHTML = ''; host.appendChild(stateBlock({ title: 'Nothing matches', body: 'Try fewer filters or another search.', actions: [{ label: 'Clear filters', run: () => $('#clearFilters').click() }] })); }
  else drawGameList(host, list);
  renderDetail(games.find((g) => g.id === state.selected) || list[0]);
}
// ---- Library: list, place and query models (UX review A11) ------------------------------------
// The list redraws only when what it shows changes; a click changes the selection and the detail pane.
// Longer lists draw only the rows near the viewport.
const SEARCH_DEBOUNCE_MS = 150;
const VIRTUAL_MIN = 150;
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
function gameItemHtml(g) {
  if (state.libraryMode === 'grid') return `<article class="game-card ${g.id === state.selected ? 'active' : ''}" data-game="${g.id}" tabindex="0" aria-label="${esc(g.name)}">
      <div ${artAttrs(g, 'game-card-art')}><label class="bulk-hit" style="position:absolute;z-index:3;right:0;bottom:0;width:48px;height:48px;align-items:center;justify-content:center;cursor:pointer"><input type="checkbox" class="bulk-check" data-bulk-check="${g.id}" ${state.bulk.has(g.id) ? 'checked' : ''} aria-label="Select ${esc(g.name)}" style="width:22px;height:22px;margin:0"></label><span class="game-card-source">${esc(g.source)}</span>${g.status !== 'ok' ? `<span class="game-card-attention ${g.status}" title="${esc(g.statusText)}"></span>` : ''}<strong>${esc(g.name)}</strong></div>
      <div class="game-card-copy"><div class="game-card-meta">${esc(g.engine)} · ${esc(versionText(g))}${versionsBadge(g)}</div>${g.status !== 'ok' ? `<div class="game-card-state ${g.status}">${esc(g.statusText)}</div>` : ''}</div>
    </article>`;
  return `<article class="game-row ${g.id === state.selected ? 'active' : ''}" data-game="${g.id}" tabindex="0"><input type="checkbox" class="bulk-check" data-bulk-check="${g.id}" ${state.bulk.has(g.id) ? 'checked' : ''} aria-label="Select ${esc(g.name)}"><div ${artAttrs(g, 'cover-mini')}>${artLetters(g)}</div><div><h3>${esc(g.name)}</h3><div class="game-meta"><span>${esc(g.source)}</span><span>·</span><span>${esc(g.engine)}</span><span>·</span><span>${esc(versionText(g))}${versionsBadge(g)}</span>${g.lastPlayed ? `<span>·</span><span>${esc(playedText(g))}</span>` : ''}</div></div><div class="status-text ${g.status}">${g.status === 'ok' ? '' : esc(g.statusText)}</div></article>`;
}
function drawGameList(host, list) {
  host._list = list;
  const before = host._virtual;
  const same = before && before.mode === state.libraryMode && before.width === host.clientWidth;
  host._virtual = list.length >= VIRTUAL_MIN ? { rowH: same ? before.rowH : 0, cols: same ? before.cols : 1, gap: same ? before.gap : 0, first: -1, mode: state.libraryMode, width: host.clientWidth } : null;
  if (!host._virtual) { host.innerHTML = list.map(gameItemHtml).join(''); return; }
  drawGameWindow(host, true);
}
function drawGameWindow(host, force = false) {
  const v = host._virtual; const list = host._list; if (!v || !list) return;
  const grid = v.mode === 'grid';
  if (!v.rowH) {
    const keep = host.scrollTop;
    host.innerHTML = list.slice(0, 40).map(gameItemHtml).join('');
    const items = $$('[data-game]', host);
    const top = (x) => x.getBoundingClientRect().top;
    v.cols = grid ? Math.max(1, getComputedStyle(host).gridTemplateColumns.split(' ').filter(Boolean).length) : 1;
    v.gap = grid ? parseFloat(getComputedStyle(host).rowGap) || 0 : 0;
    v.rowH = items[v.cols] ? top(items[v.cols]) - top(items[0]) : (items[0]?.offsetHeight || 64) + v.gap;
    if (!v.rowH || v.rowH < 8) v.rowH = 64;
    host.innerHTML = `<div style="height:${Math.ceil(list.length / v.cols) * v.rowH}px"></div>`;
    host.scrollTop = keep;
  }
  const rows = Math.ceil(list.length / v.cols);
  const first = Math.max(0, Math.floor(host.scrollTop / v.rowH) - 4);
  if (!force && first === v.first) return;
  v.first = first;
  const last = Math.min(rows, first + Math.ceil((host.clientHeight || 600) / v.rowH) + 8);
  const span = grid ? 'grid-column:1/-1;' : '';
  const gapFix = (h) => Math.max(0, h - (grid && h ? v.gap : 0));
  host.innerHTML = `<div class="v-spacer" aria-hidden="true" style="${span}height:${gapFix(first * v.rowH)}px"></div>${list.slice(first * v.cols, last * v.cols).map(gameItemHtml).join('')}<div class="v-spacer" aria-hidden="true" style="${span}height:${gapFix((rows - last) * v.rowH)}px"></div>`;
}
// A click changes only the selection: the old and new rows and the detail pane.
function chooseGame(id) {
  const g = games.find((x) => x.id === id); if (!g) return;
  if (state.selectionMode) {
    state.bulk.has(id) ? state.bulk.delete(id) : state.bulk.add(id);
    $$(`[data-bulk-check="${id}"]`).forEach((cb) => (cb.checked = state.bulk.has(id)));
    updateBulkToolbar(); return;
  }
  if (state.selected !== id) { $$(`#gameList [data-game="${state.selected}"]`).forEach((r) => r.classList.remove('active')); }
  state.selected = id; state.detailTab = 'overview';
  $$(`#gameList [data-game="${id}"]`).forEach((r) => r.classList.add('active'));
  renderDetail(g);
}
function bindGameList() {
  const host = $('#gameList');
  host.addEventListener('click', (e) => { if (e.target.closest('.bulk-hit, [data-bulk-check]')) { e.stopPropagation(); return; } const row = e.target.closest('[data-game]'); if (row) chooseGame(row.dataset.game); });
  host.addEventListener('keydown', (e) => { const row = e.target.closest('[data-game]'); if (row && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); chooseGame(row.dataset.game); } });
  host.addEventListener('change', (e) => { const cb = e.target.closest('[data-bulk-check]'); if (!cb) return; const id = cb.dataset.bulkCheck; cb.checked ? state.bulk.add(id) : state.bulk.delete(id); updateBulkToolbar(); });
  host.addEventListener('contextmenu', (e) => { const row = e.target.closest('[data-game]'); const g = row && games.find((x) => x.id === row.dataset.game); if (!g) return; e.preventDefault(); chooseGame(g.id); openActionMenu('game', row, g); });
  host.addEventListener('scroll', () => requestAnimationFrame(() => drawGameWindow(host)), { passive: true });
}
// Place: the query, selection, scroll and collection (the sidebar's filter) survive leaving the library.
function saveLibraryPlace() { state.libraryPlace = { search: state.search, scroll: $('#gameList').scrollTop }; }
function restoreLibraryPlace() {
  const host = $('#gameList'); host.scrollTop = state.libraryPlace.scroll || 0; drawGameWindow(host, true);
  host.scrollTop = state.libraryPlace.scroll || 0;
}
// Back on the desktop: the ‹ button, Alt+Left, the mouse's back button, and Backspace outside a text field.
function goBack() { if (state.shell === 'desktop' && !$('#backButton').disabled) $('#backButton').click(); }

// One action menu per kind of item (UX review A11): the game page's •••, a right-click on a row and the
// handheld X all read these.
const goTo = (desktopView, handheldPage) => (state.shell === 'handheld' ? navigateHandheld(handheldPage) : navigate(desktopView));
const ACTION_MENUS = {
  game: (g, { open } = {}) => [
    ...(open ? [{ label: 'Open', run: open }] : []),
    ...(g.path ? [{ label: 'Rescan', run: () => (state.shell === 'handheld' ? rescanHH(g) : rescan(g)) }, { label: 'Open folder', run: () => openFolder(g.path) }] : []),
    ...(model.managers[g.id] || []).slice(0, 1).map((m) => ({ label: `Open in ${m.manager_name}`, disabled: m.available === false, run: () => openInManager(g, m) })),
    { label: 'Manage versions', run: () => (state.shell === 'handheld' ? hhJumpSection('versions') : openTab('versions')) },
    ...(g.knownOn.length ? [{ label: 'Open store page', run: () => openStorePage(g) }] : []),
    { label: 'Rename', disabled: !g.mine, run: () => detailAction('rename', g) },
    { label: 'Find duplicates', run: () => (state.shell === 'handheld' ? hhOpenReview('duplicates') : navigate('reconcile')) },
    { label: 'Save backups', run: () => goTo('saves', 'saves') },
    { label: 'Launcher entries', run: () => goTo('launcher', 'launcher') },
    { label: 'Info', run: () => infoSheet({ title: g.name, details: [`Game ID: ${g.id}`, `Source: ${g.source}`, `Engine: ${g.engine}`, `Version: ${versionText(g)}`, `Folder: ${g.path || 'Not installed here'}`, `Systems: ${g.systems.join(', ')}`, '', JSON.stringify(g.own, null, 2)].join('\n'), wide: true }) },
    { rule: true },
    { label: 'Remove from library', danger: true, run: () => removeGame(g) },
  ],
  variant: (g, path) => [
    ...(g.mine ? [{ label: 'Kind and language…', run: () => describeFolder(g, path) }, { label: 'Store link…', run: () => addStoreLink(g, path) }] : []),
    ...(g.mine && (findVariant(g, path)?.kind || 'game') !== 'game' ? [{ label: 'Link to its version…', run: () => linkFolder(g, path) }] : []),
    { label: 'Rescan', run: () => rescan(g, path) },
    { label: 'Open folder', run: () => openFolder(path) },
    { label: 'Info', run: () => infoSheet({ title: `${g.name}: folder`, details: path }) },
  ],
  review: (g) => [
    { label: 'Decide…', run: () => (state.shell === 'handheld' ? leaveHandheld({ body: 'Deciding on a scan result is on the desktop layout\u2019s Review page.', then: () => navigate('reconcile'), remember: false }) : navigate('reconcile')) },
    ...(g.path ? [{ label: 'Rescan', run: () => (state.shell === 'handheld' ? rescanHH(g) : rescan(g)) }, { label: 'Open folder', run: () => openFolder(g.path) }] : []),
    { label: 'Info', run: () => infoSheet({ title: g.name, details: `${g.statusText}\nFolder: ${g.path || 'Not installed here'}` }) },
  ],
};
const findVariant = (g, path) => g.variants.find((x) => x.path === path);
async function describeFolder(g, path) {
  const x = findVariant(g, path) || {};
  const kind = await prompt({ title: `What is ${fileName(path)}?`, kind: 'select', options: (model.vocab?.kinds || Object.keys(KIND_LABELS)).map((k) => ({ value: k, label: KIND_LABELS[k] || k })), value: x.kind || 'game', save: 'Next' });
  if (kind === null) return;
  const langs = Object.entries(model.vocab?.languages || {}).sort((a, b) => a[1].localeCompare(b[1]));
  const language = await prompt({ title: 'Its language', kind: 'select', options: [{ value: '', label: 'No language' }, ...langs.map(([code, name]) => ({ value: code, label: name }))], value: x.language && x.language !== 'mtl' ? x.language : '', save: 'Save' });
  if (language === null) return;
  act('set_variant_details', [g.id, path, kind, language], 'Folder described', { what: 'Describing the folder' });
}
// One action: a translation, patch or mod folder is linked to the version it applies to.
async function linkFolder(g, path) {
  const x = findVariant(g, path) || {};
  const options = [{ value: '', label: 'Not linked' }, ...g.versions.filter((v) => v.label).map((v) => ({ value: v.version, label: v.label }))];
  const base = await prompt({ title: `Which version is ${fileName(path)} for?`, kind: 'select', options, value: x.base_version || '', save: 'Link' });
  if (base === null) return;
  act('link_variant_to_version', [g.id, path, base], base ? `Linked to ${base}` : 'Unlinked', { what: 'Linking the folder' });
}
function openActionMenu(type, anchor, ...args) { menu(anchor, ACTION_MENUS[type](...args), { returnTo: anchor }); }
async function removeGame(g) {
  if (await confirmDanger({ title: `Remove ${g.name}?`, body: 'Its record leaves the library. Files on disk stay where they are.', recommendation: 'We recommend Cancel unless the entry is wrong.', items: g.path ? [g.path] : [], proceed: 'Remove' })) act('forget_game', [g.id], 'Removed from library', { what: 'Remove' });
}

function updateBulkToolbar() { const n = state.bulk.size; document.body.classList.toggle('selection-mode', state.selectionMode); $('#bulkToolbar').classList.toggle('hidden', !state.selectionMode); $('#bulkCount').textContent = n ? `${n} selected` : 'Select games'; $('#selectionModeButton').classList.toggle('primary', state.selectionMode); $('#selectionModeButton').setAttribute('aria-pressed', String(state.selectionMode)); $$('[data-bulk-action]').forEach((b) => (b.disabled = n === 0)); }
async function bulkAction(a) {
  const ids = [...state.bulk]; const names = ids.map((id) => games.find((g) => g.id === id)?.name).filter(Boolean);
  if (a === 'updates') { if (await confirm({ title: 'Check for updates?', body: 'Pythia asks F95zone about every game with an F95zone id. Other stores have no update check yet.', proceed: 'Check' })) { pythia.check_updates(); flash('Checking for updates'); navigate('activity'); } }
  if (a === 'reconcile') navigate('reconcile');
  if (a === 'backup') navigate('saves');
  if (a === 'export') { const dir = await prompt({ title: 'Export a library report', body: 'One JSON file with this computer\u2019s records.', hint: pathHint('C:\\Users\\you\\Documents', '~/Documents'), kind: 'path', check: checkPath('writable'), save: 'Export', pick: { label: 'Browse', run: pickFolder('Folder for the report') } }); if (dir) { const r = await act('export_library_report', [dir.trim()], null, { what: 'Export' }); if (ok(r)) flash(`Report written: ${fileName(r.path)}`); } }
  if (a === 'remove') {
    const yes = await confirmDanger({ title: `Remove ${fmt.n(ids.length, 'game')} from the library?`, body: 'Their records leave the library. Files on disk stay where they are.', recommendation: 'We recommend Cancel unless the entries are wrong.', items: names.slice(0, 8).concat(names.length > 8 ? [`and ${names.length - 8} more`] : []), proceed: 'Remove' });
    if (yes) { for (const id of ids) await call('forget_game', id); state.bulk.clear(); state.selectionMode = false; updateBulkToolbar(); flash(`Removed ${fmt.n(ids.length, 'game')}`); }
  }
}

// One section list per game, for both shells (UX review A12): desktop tabs, one handheld scroll with LB/RB.
const GAME_PARTS = [
  { id: 'facts', title: 'Record', html: (g) => `<div class="kv"><div class="key">Name</div><div class="value">${esc(g.name)} ${g.mine ? '<button class="row-action" style="float:right" data-detail-action="rename" aria-label="Rename">✎</button>' : ''}</div><div class="key">Game ID</div><div class="value mono">${esc(g.id)}</div><div class="key">Source</div><div class="value">${esc(g.source)}</div><div class="key">Engine</div><div class="value">${esc(g.engine)}</div><div class="key">Platforms</div><div class="value">${esc(g.platforms.map(platformName).join(' · ') || '—')}</div><div class="key">Folder</div><div class="value mono">${esc(g.path || '—')}</div>${g.knownOn.length ? `<div class="key">Store ids</div><div class="value mono">${esc(g.knownOn.map((k) => `${storeName(k.platform)} ${k.id}`).join(' · '))}</div>` : ''}</div>`, bind: null },
  { id: 'engine', title: 'Engine &amp; Compatibility', html: (g, updates) => ``, bind: (g, pane) => renderEngineTab(g, pane) },
  { id: 'versions', title: 'Versions', html: (g, updates) => `${updates.length ? `<h3 style="font:500 1.0625rem var(--serif);margin:0 0 9px">Available, not installed</h3>${updates.map((u) => `<div class="available-update"><span class="status-icon pending" aria-hidden="true">↻</span><div><strong>${esc(u.version || 'Newer version')} · ${esc(platformName(u.platform))}</strong><br><small>${u.for ? `For ${esc(fileName(u.for))} · ` : ''}Seen via ${esc(u.source || 'a store')}${u.detected_at ? ` · ${esc(fmt.date(u.detected_at))}` : ''}</small></div><button class="btn small primary" style="margin-left:auto" data-detail-action="apply-update" data-version="${esc(u.version || '')}">Review update</button></div>`).join('')}` : ''}<h3 style="font:500 1.0625rem var(--serif);margin:14px 0 9px">Installed versions</h3>${g.parts.length ? `<h3 style="font:500 1.0625rem var(--serif);margin:0 0 9px">Parts</h3><p class="order-note">${esc(partsText(g))}</p>${g.parts.map((part) => `<div class="version-group"><div class="version-head"><strong>${esc(part)}</strong><small>${esc(g.variants.filter((x) => x.part === part).map((x) => (x.version ? `version ${x.version}` : '')).filter(Boolean).join(', '))}</small></div>${g.variants.filter((x) => x.part === part).map((x) => `<div class="variant-row"><div><div class="variant-name">${esc(variantName(x))}</div><div class="variant-path mono" aria-label="${esc(x.path || '')}">${esc(PythiaVersions.middleEllipsis(x.path || '', 72))}</div></div><div></div><div></div><button class="row-action" data-variant-menu="${esc(x.path || '')}" aria-label="Folder actions">•••</button></div>`).join('')}</div>`).join('')}` : ''}${!g.orderKnown ? `<p class="order-note">${visibleStatus('Order unknown', 'pending', '?')} These labels are not version numbers, so Pythia does not guess which is newest: ${esc(g.unorderedLabels.join(', '))}. Set one as Current.</p>` : ''}${g.versions.length ? g.versions.filter((v) => (v.variants || []).filter((x) => !x.part).some((x) => !x.part)).map((v) => `<div class="version-group"><div class="version-head"><strong>${esc(v.label || 'No version number')}</strong>${g.orderKnown && v.label && v.label === g.latest ? '<span class="tag">Latest installed</span>' : ''}<small>${(v.variants || []).filter((x) => !x.part).length} variant${(v.variants || []).filter((x) => !x.part).length === 1 ? '' : 's'}</small><span class="spacer"></span>${g.mine ? (g.current === v.version ? '<span class="tag ok">Current</span> <button class="btn small ghost" type="button" data-set-current="">Clear</button>' : `<button class="btn small" type="button" data-set-current="${esc(v.version || '')}">Set as current</button>`) : ''}</div>${(v.variants || []).filter((x) => !x.part).map((x) => `<div class="variant-row"><div><div class="variant-name">${esc(variantName(x))}${x.base_version ? ` <span class="tag">for ${esc(x.base_version)}</span>` : ''}${(x.platform_ids || []).map((l) => ` <span class="tag">${esc(storeName(l.platform))} ${esc(shortId(l.id))}</span>`).join('')}</div><div class="variant-path mono" aria-label="${esc(x.path || '')}">${esc(PythiaVersions.middleEllipsis(x.path || '', 72))}</div></div><div class="platforms">${(x.platforms || []).map((p) => `<span class="platform">${esc(platformName(p))}</span>`).join('')}</div><div>${(x.mods || []).length ? x.mods.map((m) => `<span class="tag">${esc(m.name || m)}</span>`).join(' ') : '<span style="color:var(--dim);font-size:0.625rem">No mods</span>'}</div><button class="row-action" data-variant-menu="${esc(x.path || '')}" aria-label="Variant actions">•••</button></div>`).join('')}</div>`).join('') : ''}<div data-versions-state></div><div style="margin-top:10px"><button class="btn small" data-detail-action="add-folder">＋ Add another folder</button></div>`, bind: (g, pane) => { $$('[data-set-current]', pane).forEach((b) => (b.onclick = () => act('set_current_version', [g.id, b.dataset.setCurrent], b.dataset.setCurrent ? `Current: ${b.dataset.setCurrent}` : 'Current cleared', { what: 'Setting Current' }))); if (!g.versions.length) $('[data-versions-state]', pane).appendChild(stateBlock({ title: 'Nothing installed here', body: g.ownedOn.length ? `Owned on ${g.ownedOn.map((k) => kindName(k) || k).join(', ')}, not installed on this computer.` : 'No folder is known for this game on this computer.' })); } },
  { id: 'devices', title: 'Devices', html: (g, updates) => `<table class="table"><thead><tr><th>Computer</th><th>Availability</th><th>Versions</th><th>Readiness</th><th></th></tr></thead><tbody>${g.entries.map((e) => `<tr><td class="primary-cell"><strong>${esc(systemName(e.system_uuid))}</strong><small>${e.updated_at ? `Record updated ${esc(fmt.dateTime(e.updated_at))}` : 'Synced record'}</small></td><td>${(e.versions || []).some((v) => (v.variants || []).some((x) => x.path)) ? (e.system_uuid === model.mySystem ? routineStatus('Installed here') : `<span class="status-inline neutral"><span class="status-icon neutral" aria-hidden="true">i</span>Installed there</span>`) : `<span class="status-inline neutral"><span class="status-icon neutral" aria-hidden="true">–</span>Not installed</span>`}</td><td>${esc((e.versions || []).map((v) => (v.version && v.version !== e.canonical_name ? v.version : 'no version')).join(', ') || '—')}</td><td data-readiness="${esc(e.system_uuid)}">${e.system_uuid === model.mySystem ? '…' : '<span class="status-inline neutral"><span class="status-icon neutral" aria-hidden="true">–</span>Not checked here</span>'}</td><td></td></tr>`).join('')}</tbody></table>`, bind: null },
  { id: 'notes', title: 'Notes', html: (g, updates) => `${g.mine ? `<textarea class="notes-area" id="notesArea" aria-label="Notes">${esc(g.note)}</textarea><div style="display:flex;justify-content:flex-end;margin-top:9px"><button class="btn primary" data-detail-action="save-note">Save note</button></div>` : ''}<div data-notes-state></div>`, bind: (g, pane) => { if (!g.mine) $('[data-notes-state]', pane).appendChild(stateBlock({ title: 'Notes live on their computer', body: `This record belongs to ${g.systems.find((s) => s !== 'This computer') || 'another computer'}; notes are read-only here.${g.note ? `\n\n${g.note}` : ''}` })); } },
  { id: 'saves', title: 'Saves', html: (g, updates) => ``, bind: (g, pane) => renderSavesTab(g, false, pane) },
  { id: 'history', title: 'History', html: (g, updates) => `<div class="timeline">${[...g.events].reverse().map((e) => `<div class="timeline-item"><strong>${esc(EVENT_NAMES[e.type] || e.type)}</strong><p>${esc(e.detail || '')}</p><time>${esc(fmt.dateTime(e.timestamp))}</time></div>`).join('')}</div><div data-history-state></div>`, bind: (g, pane) => { if (!g.events.length) $('[data-history-state]', pane).appendChild(stateBlock({ title: 'No history yet', body: 'Events are recorded as Pythia changes this record.' })); } },
];
// The game page's tabs (UI round): Overview, Versions, Saves and Details. Notes are on Overview; the record with
// its IDs and paths, the engine report, each computer and the history are on Details.
const GAME_PART = Object.fromEntries(GAME_PARTS.map((p) => [p.id, p]));
const paneHead = (t) => `<h3 class="pane-head">${t}</h3>`;
function overviewHtml(g) {
  const version = g.current || (g.orderKnown ? g.latest || 'No version number' : 'Order unknown');
  return `<div class="stats-grid"><div class="stat"><div class="label">Version</div><div class="value">${esc(version)}</div><div class="sub">${g.current ? 'Current' : g.orderKnown ? (g.latest ? 'Latest installed' : '') : 'Set one as Current'}${g.versionCount > 1 ? ` · ${g.versionCount} versions` : ''}</div></div><div class="stat"><div class="label">Played</div><div class="value">${esc(playtimeText(g) || (g.lastPlayed ? 'Under a minute' : 'Not yet'))}</div><div class="sub">${esc(playedText(g))}</div></div><div class="stat"><div class="label">Added</div><div class="value">${esc(g.added ? fmt.date(g.added) : '—')}</div><div class="sub">${esc(g.systems.length > 1 ? `On ${g.systems.length} computers` : g.installed ? 'Installed here' : 'Not installed here')}</div></div></div><div class="facts">${g.knownOn.length ? `<div><span class="facts-key">Store</span>${storeLinksHtml(g, '', g.knownOn)}</div>` : ''}${g.ownedOn.length ? `<div><span class="facts-key">Owned on</span>${esc(g.ownedOn.map((k) => kindName(k) || k).join(' · '))}</div>` : ''}<div><span class="facts-key">Engine</span>${esc(g.engine)}</div></div>`;
}
const GAME_SECTIONS = [
  { id: 'overview', title: 'Overview', html: (g, updates) => `${overviewHtml(g)}${paneHead('Notes')}${GAME_PART.notes.html(g, updates)}`, bind: (g, pane) => { bindStoreLinks(g, pane); GAME_PART.notes.bind(g, pane); } },
  GAME_PART.versions,
  GAME_PART.saves,
  { id: 'details', title: 'Details', html: (g, updates) => `${paneHead('Record')}${GAME_PART.facts.html(g)}${paneHead('Engine and compatibility')}<div data-engine-host></div>${paneHead('On each computer')}${GAME_PART.devices.html(g, updates)}${paneHead('History')}${GAME_PART.history.html(g, updates)}`, bind: (g, pane) => { renderEngineTab(g, $('[data-engine-host]', pane)); GAME_PART.history.bind(g, pane); } },
];
// Tabs that became parts of another tab keep working as names (menus, Review's links).
const MOVED_TABS = { engine: 'details', devices: 'details', systems: 'details', history: 'details', notes: 'overview' };
// The primary slot (UX review A12, owner decision 1): Play, Install or Update, first and largest. Its space
// is drawn before the backend answers, so nothing in the hero moves when it does.
function fillPrimarySlot(g, slot, cls) {
  if (!slot) return;
  call('get_play_state', g.id).then((r) => {
    if (!slot.isConnected) return;
    if (!ok(r)) { slot.innerHTML = `<button class="${cls}" type="button" disabled data-reason="${esc(r.message || '')}">Play</button>`; return; }
    const p = r.primary; model.storeUrls[g.id] = r.store_url || null;
    const label = p.action === 'install' ? 'Install' : 'Play';
    slot.innerHTML = `<button class="${cls}" type="button" ${p.enabled ? '' : 'disabled'} data-reason="${esc(p.reason || '')}">${label}${p.enabled && p.via === 'steam' ? ' <span class="reason">· Steam</span>' : ''}</button>`;
    const b = $('button', slot);
    if (!p.enabled) { b.title = ''; slot.insertAdjacentElement('beforeend', el(`<small class="slot-reason">${esc(p.reason || '')}</small>`)); }
    const start = async () => { const r2 = await act('play_game', [g.id], null, { what: `Starting ${g.name}` }); if (ok(r2)) flash(`Started ${g.name}${r2.via === 'steam' ? ' in Steam' : ''}`); };
    // Steam opens its own window: in the handheld shell that is a hand-off, asked first (UX review A3).
    b.onclick = () => (p.via === 'steam' ? handOff({ kind: 'launcher', launcher: 'steam', title: `Play ${g.name} in Steam?`, body: 'Steam opens and starts it.', run: start }) : start());
    if (state.shell === 'handheld') hhKeepDisabledReachable();
  });
}
// Launchers that can show this game, asked for once the page is drawn; "Open in <launcher>" is in its menu.
function loadManagers(g) {
  call('find_manager_for_game', g.id).then((r) => { if (ok(r)) model.managers[g.id] = r.managers || []; else ErrorConsole.reportWeb('warn', `find_manager_for_game: ${r.message}`, 'find_manager_for_game'); });
}
function openStorePage(g) {
  const url = model.storeUrls[g.id];
  if (!url) { infoSheet({ title: 'No store page known', details: `${g.name} has store ids (${g.knownOn.map((k) => `${k.platform}: ${k.id}`).join(', ')}) but no page Pythia knows how to open.` }); return; }
  handOff({ kind: 'link', title: 'Open the store page?', body: 'Your web browser opens.', run: () => act('open_url', [url], null, { what: 'Opening the browser' }) });
}
function updateSlotHtml(g) {
  // Owner decision 2 (docs/SPEC.md 1.2): an update Pythia cannot apply yet is shown, disabled, with why.
  if (!g.updates.length) return '';
  return `<button class="btn" type="button" disabled data-reason="Pythia records updates but does not download them yet.">Update to ${esc(g.updates[0].version || 'the newer version')} <span class="reason">· not built yet</span></button>`;
}
function renderDetail(g) {
  const host = $('#gameDetail');
  if (!g) { host.innerHTML = ''; host.appendChild(stateBlock({ title: 'Pick a game', body: 'Its details open here.' })); return; }
  const updates = g.updates || [];
  state.detailTab = MOVED_TABS[state.detailTab] || state.detailTab;
  host.innerHTML = `<div class="detail-hero"><div ${artAttrs(g, 'cover-large')}>${artLetters(g)}</div><div class="detail-copy"><div class="eyebrow">${esc(g.source)} · ${esc(g.engine)}</div><h2>${esc(g.name)}</h2><div class="tag-row">${g.status !== 'ok' ? gameStatus(g) : ''}<span class="tag">${g.current ? `Current ${esc(g.current)}` : g.parts.length && !g.latest ? esc(partsText(g)) : g.orderKnown ? (g.latest ? `Version ${esc(g.latest)}` : 'No version number') : 'Order unknown, set Current'}</span>${g.lastPlayed ? `<span class="tag">${esc(playedText(g))}</span>` : ''}${g.managed ? '<span class="tag">Managed by its launcher</span>' : ''}</div><p>${esc(g.note.split('\n')[0] || '')}</p><div class="detail-actions" id="detailActions"><span class="primary-slot" data-primary-slot><button class="btn primary big" type="button" disabled>Checking…</button></span>${updateSlotHtml(g)}<button class="btn" data-detail-action="more" aria-haspopup="menu">More…</button></div></div></div><div class="detail-tabs">${GAME_SECTIONS.map((sec) => `<button class="tab" data-tab="${sec.id}">${sec.title}</button>`).join('')}</div>
${GAME_SECTIONS.map((sec) => `<div class="tab-pane" data-pane="${sec.id}">${sec.html(g, updates)}</div>`).join('')}`;
  GAME_SECTIONS.forEach((sec) => sec.bind && sec.bind(g, $(`[data-pane="${sec.id}"]`, host)));
  $$('[data-tab]', host).forEach((t) => (t.onclick = () => openTab(t.dataset.tab)));
  $$('[data-detail-tab]', host).forEach((t) => (t.onclick = () => openTab(t.dataset.detailTab)));
  $$('[data-detail-action]', host).forEach((b) => (b.onclick = () => detailAction(b.dataset.detailAction, g, b)));
  $$('[data-variant-menu]', host).forEach((b) => (b.onclick = () => openActionMenu('variant', b, g, b.dataset.variantMenu)));
  openTab(state.detailTab || 'overview');
  fillPrimarySlot(g, $('[data-primary-slot]', host), 'btn primary big');
  loadManagers(g);
}
// Where the droidtop agent finds this game's saves (backend: get_game_saves, the agent's saves crate).
function renderSavesTab(g, fetch = false, pane = $('#gameDetail [data-pane="saves"]')) {
  if (!pane) return;
  loadInto(pane, 'get_game_saves', [g.id, fetch], (r, host) => {
    const files = r.files || [];
    if (!r.patterns) {
      host.appendChild(stateBlock({ title: 'Where its saves are is not known', body: `droidtop knows where a game keeps its saves from the Ludusavi manifest, by its key (${r.key}) or title. ${fetch ? 'The manifest has no entry for it, or it could not be downloaded (see the log).' : 'Download the manifest to look it up.'}`, actions: fetch ? [] : [{ label: 'Download the manifest and look', run: () => renderSavesTab(g, true, pane) }] }));
      return;
    }
    host.appendChild(el(`<div class="kv"><div class="key">Looked for</div><div class="mono" style="word-break:break-all">${(r.patterns || []).map(esc).join('<br>')}</div></div>`));
    if (!files.length) { host.appendChild(stateBlock({ title: 'No save files here yet', body: 'Nothing matches those places on this computer now. They sync with paired handhelds once there are some.' })); return; }
    host.appendChild(el(`<table class="table"><thead><tr><th>File</th><th></th></tr></thead><tbody>${files.map((f) => `<tr><td class="primary-cell"><strong>${esc(f.name)}</strong><small class="mono">${esc(f.path)}</small></td><td><button class="btn small" data-open-save="${esc(f.path)}">Show</button></td></tr>`).join('')}</tbody></table>`));
    $$('[data-open-save]', host).forEach((b) => (b.onclick = () => openFolder(b.dataset.openSave.replace(/[\\/][^\\/]*$/, ''), 'Opening the folder')));
  }, { what: "This game's saves" });
}
function renderEngineTab(g, pane = $('#gameDetail [data-pane="engine"]')) {
  if (!pane) return;
  if (!g.path) { pane.appendChild(stateBlock({ title: 'Not installed on this computer', body: 'Engine and platform checks need a folder here.' })); return; }
  loadInto(pane, 'get_platform_status', [g.id], (r, host) => {
    model.platformStatus[g.id] = r;
    const readiness = $(`[data-readiness="${model.mySystem}"]`, $('#gameDetail'));
    if (!r.engine) {
      host.appendChild(el(`<div class="engine-banner">${visibleStatus('Not identified', 'neutral', '?')}<div><strong>No engine plugin matched this install.</strong><div style="color:var(--text-secondary);font-size:0.6875rem;margin-top:2px">The folder is tracked without an engine assignment. Rescan after turning on the right engine plugin.</div></div><span class="spacer"></span><button class="btn small" data-fix="correct">Correct manually…</button></div>`));
      if (readiness) readiness.innerHTML = visibleStatus('Not identified', 'neutral', '?');
    } else {
      const rows = Object.entries(r.platforms || {}).map(([platform, p]) => `<div class="platform-row"><strong>${esc(platformName(platform))}</strong><div class="req">${esc(p.supported ? (p.native ? 'Runs natively' : 'Supported') : (p.requires || []).join(', ') || 'Not supported for this install')}</div>${p.supported ? routineStatus('Ready') : p.fixable ? `<button class="btn small primary" data-fix="${esc(platform)}">Add support</button>` : visibleStatus('Not available for this install', 'neutral', '–')}</div>`).join('');
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
function openTab(name) { name = MOVED_TABS[name] || name; state.detailTab = name; $$('.tab', $('#gameDetail')).forEach((t) => t.classList.toggle('active', t.dataset.tab === name)); $$('.tab-pane', $('#gameDetail')).forEach((p) => p.classList.toggle('active', p.dataset.pane === name)); }
function rescan(g, path) { pythia.start_onboard(path || g.path); flash('Rescanning'); navigate('activity'); }
async function detailAction(a, g, btn) {
  if (a === 'open-folder') openFolder(g.path);
  if (a === 'store-page') openStorePage(g);
  if (a === 'scan') rescan(g);
  if (a === 'rename') { const v = await prompt({ title: 'Rename game', value: g.name }); if (v && v.trim() && v.trim() !== g.name) act('rename_game', [g.id, v.trim()], 'Renamed', { what: 'Rename' }); }
  if (a === 'save-note') act('save_notes', [g.id, $('#notesArea').value], 'Note saved', { what: 'Saving the note' });
  if (a === 'add-folder') addFolder();
  if (a === 'apply-update') { const r = await call('apply_update', g.id, btn.dataset.version || ''); infoSheet({ title: `Update ${btn.dataset.version || ''} for ${g.name}`, details: ok(r) ? 'Started' : r.message }); }
  if (a === 'more') openActionMenu('game', btn, g);
}

// ---- Downloads & intake ---------------------------------------------------------------
// Modes (backend pythia.modes): manager mode is #214's library and version manager; while Pythia is the
// droidtop agent it is forced on, and the switch shows locked with the reason instead of hiding.
async function refreshModes() {
  const r = await call('get_modes');
  model.modes = ok(r) ? r : null;
  if (!ok(r)) model.modesError = r;
  return r;
}
function managerPaused(page) { const m = model.modes?.manager; return !!m && !m.on && (m.pages || []).includes(page); }
function managerOffBlock(what) {
  return stateBlock({ title: 'Manager mode is off', body: `${what} is part of manager mode.`, actions: [{ label: 'Turn manager mode on', run: async () => { const r = await act('set_manager_mode', [true], 'Manager mode on', { what: 'Turning manager mode on' }); if (ok(r)) { await refreshModes(); renderCurrent(); } } }] });
}
const SYNC_CONSENT = 'Pythia will serve your paired droidtop handhelds from this computer. To do that it listens on your network (TCP and UDP port 47610, TCP 47612 while pairing, UDP 47611 for reaching it away from home) and tells a public discovery server this computer\u2019s address, so a paired handheld can find it away from home. If droidtop-agent runs here, Pythia turns it off while it serves. Your system may ask whether Pythia may use the network: allow it on private networks. Nothing of this happens until you turn it on.';
const modeInfo = (title, details) => { const b = el('<button class="btn small ghost" type="button">Info</button>'); b.onclick = () => infoSheet({ title, details }); return b; };
// Sync with droidtop (UX review A9): agent mode, named by its outcome; "agent" is said only in Info. Its place is Devices.
function syncModeRow(r, redraw) {
  const sync = r.sync || {};
  const syncState = !sync.available ? 'Not available' : sync.on ? (sync.serving ? 'On' : 'Starting') : sync.serving ? 'Off when Pythia quits' : 'Off';
  const row = el(`<div class="form-row"><div class="form-label"><strong>Sync with droidtop</strong><small>Your paired devices get this library, its saves and its launches from Pythia. ${esc(syncState)}.</small>${sync.available ? '' : `<small class="locked-reason">${esc(sync.reason || '')}</small>`}</div><span class="mode-controls"><button class="toggle" data-sync-mode aria-label="Sync with droidtop"><span></span></button></span></div>`);
  $('.mode-controls', row).prepend(modeInfo('Sync with droidtop', sync.info || ''));
  const sw = $('[data-sync-mode]', row);
  bindSwitch(sw, !!sync.on, async (on) => {
    // Turning it on is the consent to use the network (docs/SPEC.md 1.7): said plainly first, nothing listens before.
    if (on && !(await confirm({ title: 'Turn on Sync with droidtop?', body: SYNC_CONSENT, proceed: 'Turn on' }))) { redraw(); return; }
    if (!on && !(await confirm({ title: 'Turn off Sync with droidtop?', body: 'Pythia stops serving your handhelds when it quits. If Pythia had turned droidtop-agent off, it is turned back on.', proceed: 'Turn off' }))) { redraw(); return; }
    await act(on ? 'serve_from_pythia_again' : 'turn_agent_back_on', [], on ? 'Sync with droidtop on' : 'Sync with droidtop off', { what: 'Changing Sync with droidtop' });
    await refreshModes();
    redraw();
  });
  if (!sync.available) { sw.disabled = true; sw.dataset.reason = sync.reason || ''; }
  return row;
}
// Service mode (docs/SPEC.md 1.10, windowcast#467): off until the person turns it on after reading what it does;
// each change asks Windows for administrator approval. Its place is Devices: it is what a paired device may see.
function serviceModeRow(r, redraw) {
  const svc = r.service || {};
  const row = el(`<div class="form-row"><div class="form-label"><strong>Answer Windows security screens</strong><small>A paired device can see and answer this computer's UAC prompts and its lock and sign-in screen (service mode). ${esc(svc.state || '')}.</small>${svc.available ? '' : `<small class="locked-reason">${esc(svc.reason || '')}</small>`}</div><span class="mode-controls"><button class="toggle" data-service-mode aria-label="Service mode"><span></span></button></span></div>`);
  $('.mode-controls', row).prepend(modeInfo('Service mode', svc.consent || svc.reason || ''));
  if (svc.installed && !svc.on) {
    const remove = el('<button class="btn small ghost" type="button">Remove the helper</button>');
    remove.onclick = async () => {
      if (!(await confirm({ title: 'Remove the secure-screen helper?', body: 'Windows asks for administrator approval. Service mode stays off.', proceed: 'Remove' }))) return;
      await act('set_service_mode', ['uninstall'], 'Secure-screen helper removed', { what: 'Removing the secure-screen helper' });
      redraw();
    };
    $('.mode-controls', row).prepend(remove);
  }
  const sw = $('[data-service-mode]', row);
  bindSwitch(sw, !!svc.on, async (on) => {
    if (on && !(await confirm({ title: 'Turn on service mode?', body: svc.consent || '', proceed: 'Turn on' }))) { redraw(); return; }
    if (!on && !(await confirm({ title: 'Turn off service mode?', body: 'Windows asks for administrator approval. The helper stays installed but does nothing until you turn it on again.', proceed: 'Turn off' }))) { redraw(); return; }
    await act('set_service_mode', [on ? 'enable' : 'disable'], on ? 'Service mode on' : 'Service mode off', { what: on ? 'Turning service mode on' : 'Turning service mode off' });
    redraw();
  });
  if (!svc.available) { sw.disabled = true; sw.dataset.reason = svc.reason || ''; }
  return row;
}
// A row in Settings for something whose place is a page of its own: what it is now, and a way there.
function linkRow(title, small, view) {
  const row = el(`<div class="form-row"><div class="form-label"><strong>${esc(title)}</strong><small>${esc(small)}</small></div><button class="btn small" type="button">Open ${esc(VIEW_NAMES[view] || view)} <span class="nav-out">›</span></button></div>`);
  $('button', row).onclick = () => navigate(view);
  return row;
}
const VIEW_NAMES = { devices: 'Devices', sources: 'Game sources', shared: 'Shared library' };
function renderModes(host) {
  if (!host) return;
  loadInto(host, 'get_modes', [], (r, h) => {
    model.modes = r;
    const m = r.manager || {}; const sync = r.sync || {}; const svc = r.service || {};
    const row = el(`<div class="form-row"><div class="form-label"><strong>Manager mode</strong><small>The library and version manager: tagging game folders, versions against the latest release, and tagging downloads with their game and kind.</small>${m.locked ? `<small class="locked-reason">${visibleStatus('Kept on while Sync with droidtop is on', 'neutral', '🔒')}</small>` : ''}</div><span class="mode-controls"><button class="toggle" data-manager-mode aria-label="Manager mode"><span></span></button></span></div>`);
    if (m.locked) $('.mode-controls', row).prepend(modeInfo('Manager mode is kept on', m.reason || ''));
    const sw = $('[data-manager-mode]', row);
    bindSwitch(sw, !!m.on, async (on, node) => {
      const r2 = await act('set_manager_mode', [on], on ? 'Manager mode on' : 'Manager mode off', { what: 'Changing manager mode' });
      if (!ok(r2)) { node.classList.toggle('on', !on); node.setAttribute('aria-checked', String(!on)); }
      renderModes(host);
    });
    if (m.locked) { sw.disabled = true; sw.dataset.reason = 'Kept on while Sync with droidtop is on.'; }
    h.appendChild(row);
    h.appendChild(linkRow('Sync with droidtop', `${!sync.available ? 'Not available' : sync.on ? 'On' : 'Off'}. Pairing, what each device gets, and security screens are on the Devices page.`, 'devices'));
    if (svc.available || svc.on) h.appendChild(linkRow('Service mode', `${svc.on ? 'On' : 'Off'}. It is on the Devices page.`, 'devices'));
    if (state.shell === 'handheld') hhKeepDisabledReachable();
  }, { what: 'Modes' });
}
function renderDownloads() {
  const { watcher } = model.intake;
  const running = model.jobs.filter((j) => j.state === 'running').length;
  $('#downloadsSummary').textContent = `${watcher ? `Watching ${watcher.folder}` : 'No folder watched'} · ${fmt.n(running, 'job')} running · new downloads wait in Review`;
  $('#watchButton').textContent = watcher ? 'Stop watching' : 'Watch a folder';
  const paused = managerPaused('downloads');
  $('#watchButton').disabled = paused && !watcher;
  const dn = $('#downloadsNotice'); dn.innerHTML = '';
  if (paused) dn.appendChild(managerOffBlock('Watching download folders and tagging what arrives'));
  loadInto($('#downloaderCards'), 'get_downloader_plugins', [], (list, host) => {
    const ds = Array.isArray(list) ? list : [];
    if (!ds.length) { host.appendChild(stateBlock({ title: 'No downloader plugins', body: 'A downloader plugin adds a card here.' })); return; }
    ds.forEach((d) => {
      // Only the downloader that started the watch is watching (Droidtop/tracker#538 retest D4).
      const watching = !!watcher && d.enabled && watcher.plugin === d.id;
      const card = el(`<div class="card"><div class="card-head"><div class="card-icon">${esc(d.display_name.slice(0, 2).toUpperCase())}</div><div><strong>${esc(d.display_name)}</strong><small>${esc(d.headless ? 'Can fetch without a window' : 'Opens its own window; Pythia watches the folder')}</small></div></div><div class="card-body"><p>${esc(watching ? `Watching ${watcher.folder}${watcher.program ? ` while ${fileName(watcher.program)} runs` : ''}.` : d.enabled ? 'Not watching a folder right now.' : 'Turned off in Settings > Downloaders.')}</p></div><div class="card-foot">${watching ? routineStatus('Watching') : d.enabled ? visibleStatus('Idle', 'neutral', '–') : visibleStatus('Turned off', 'neutral', '–')}<span class="spacer"></span><button class="btn small" data-watch>${watching ? 'Stop' : 'Watch a folder'}</button></div></div>`);
      $('[data-watch]', card).onclick = () => (watching ? stopWatching() : watchFolder(d.id));
      host.appendChild(card);
    });
  }, { what: 'Downloaders' });
}
function stopWatching() { pythia.stop_watch(); flash('Stopped watching'); }

// ---- Review: everything waiting on the person, in one queue (UX review A18; review.js) --------
let decisionIndex = 0, chosenCandidate = null;
const reviewQueue = () => PythiaReview.queue({ games, decisions: model.intake.decisions, clusters: Array.isArray(model.merges) ? mergeClusters() : [], detected: model.intake.detected });
function reviewSection(id, items, fill) {
  const sec = $(id); sec.hidden = !items.length;
  const count = $('[data-count]', sec); if (count) count.textContent = items.length ? String(items.length) : '';
  if (items.length && fill) { const body = $('[data-body]', sec); body.innerHTML = ''; fill(body); }
}
function reviewGameRow(g, sub, actions) {
  const row = el(`<div class="review-row"><span class="art art-${g.art}">${esc(initials(g.name))}</span><span class="review-copy"><strong>${esc(g.name)}</strong><small>${esc(sub)}</small></span><span class="review-actions"><button class="btn small ghost" data-show>Show</button></span></div>`);
  $('[data-show]', row).onclick = () => { state.filterStatus = 'all'; state.selected = g.id; navigate('library'); };
  (actions || []).forEach((x) => { const b = el(`<button class="btn small${x.primary ? ' primary' : ''}">${esc(x.label)}</button>`); b.onclick = x.run; $('.review-actions', row).prepend(b); });
  return row;
}
function renderReconcile() {
  const q = reviewQueue();
  const { decisions } = model.intake;
  const merges = Array.isArray(model.merges) ? model.merges : [];
  const loadingMerges = model.merges === null || model.merges === undefined;
  const sections = [[q.decisions, 'scan decision'], [q.clusters, 'possible duplicate'], [q.unidentified, 'game not identified', 'games not identified'], [q.updates, 'update seen', 'updates seen'], [q.downloads, 'new download']].filter(([list]) => list.length);
  $('#reviewSummary').textContent = !model.ready ? '' : q.affected ? `${fmt.n(q.affected, 'game')} affected · ${sections.map(([list, one, many]) => (list.length === 1 ? `1 ${one}` : `${list.length} ${many || `${one}s`}`)).join(' · ')} · nothing changes until you decide` : 'Nothing waiting';
  const empty = $('#reviewEmpty'); empty.innerHTML = '';
  if (!model.ready) empty.appendChild(stateBlock({ kind: 'loading' }));
  else if (!q.affected && !loadingMerges && !(model.merges && model.merges.status)) empty.appendChild(stateBlock({ glyph: '✓', title: 'Nothing to review', body: 'Scan decisions, possible duplicates, games not identified, updates seen and new downloads land here. Save sync conflicts will too, once save sync is built.', actions: [{ label: 'Scan library', run: scanLibrary }] }));
  reviewSection('#reviewDecisions', model.ready ? decisions : []);
  $('#reviewDuplicates').hidden = !model.ready || (!loadingMerges && !(model.merges && model.merges.status) && !q.clusters.length);
  reviewSection('#reviewUnidentified', model.ready ? q.unidentified : [], (body) => q.unidentified.forEach((g) => body.appendChild(reviewGameRow(g, `${g.source} · ${g.path ? PythiaVersions.middleEllipsis(g.path, 64) : 'no folder'}`, g.path ? [{ label: 'Rescan', run: () => rescan(g) }] : []))));
  reviewSection('#reviewUpdates', model.ready ? q.updates : [], (body) => q.updates.forEach((g) => body.appendChild(reviewGameRow(g, `${g.updates.map((u) => u.version || 'A newer version').join(', ')} seen via ${g.updates[0].source || 'a store'} · installed ${versionText(g)}`, [{ label: 'Review update', primary: true, run: () => { state.selected = g.id; state.detailTab = 'versions'; navigate('library'); } }]))));
  reviewSection('#reviewDownloads', model.ready ? q.downloads : [], (body) => q.downloads.forEach((d) => {
    const row = el(`<div class="review-row"><span class="art art-d">⇩</span><span class="review-copy"><strong>${esc(fileName(d.path))}</strong><small>${esc(PythiaVersions.middleEllipsis(d.path, 64))} · seen ${esc(fmt.time(d.at))}</small></span><span class="review-actions"><button class="btn small primary" data-add>Add to library</button><button class="btn small ghost" data-forget>Forget</button></span></div>`);
    $('[data-add]', row).onclick = () => { pythia.start_onboard(d.path); pythia.forget_detected(d.path, () => {}); flash('Adding'); navigate('activity'); };
    $('[data-forget]', row).onclick = () => call('forget_detected', d.path);
    body.appendChild(row);
  }));
  reviewSection('#reviewConflicts', q.conflicts);
  const area = $('#decisionArea'); area.innerHTML = '';
  if (decisions.length) {
    if (decisionIndex >= decisions.length) decisionIndex = 0;
    const d = decisions[decisionIndex]; const suggested = d.candidates?.suggested || [];
    if (!chosenCandidate || !suggested.some((s) => s.game_id === chosenCandidate)) chosenCandidate = suggested[0]?.game_id || 'new';
    const det = d.detection || {};
    area.appendChild(el(`<div class="onboard-grid"><section class="panel"><div class="panel-head"><h2>Incoming install</h2><span class="state-pill pending">${suggested.length ? 'Similar name found' : 'New'}</span><div class="panel-actions"><small>${decisionIndex + 1} of ${decisions.length}</small>${decisions.length > 1 ? '<button class="btn small ghost" data-next-decision>Next</button>' : ''}</div></div><div class="panel-body"><div class="path-box">${esc(d.path)}</div><div class="kv" style="margin-top:12px"><div class="key">Detected name</div><div class="value">${esc(d.name || fileName(d.path))}</div><div class="key">Version</div><div class="value">${esc(d.version_label || 'No version number')}</div><div class="key">Kind</div><div class="value">${esc(KIND_LABELS[d.kind] || 'Game')}${(d.flags || []).length ? ` · ${esc(d.flags.map((f) => FLAG_LABELS[f] || f).join(' · '))}` : ''}</div><div class="key">Language</div><div class="value">${esc(languageName(d.language) || '—')}</div><div class="key">Engine</div><div class="value">${esc(kindName(det.kind) || 'Not identified')}</div><div class="key">Platforms</div><div class="value">${esc((det.platforms || []).map(platformName).join(' · ') || '—')}</div></div></div></section>
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
      if (await confirm({ title: 'Commit this decision?', node: plan, proceed: 'Commit' })) { pythia.resolveDecision(d.path, decision); chosenCandidate = null; flash('Committed'); }
    };
  }
  const panel = $('#mergePanel'); panel.innerHTML = '';
  const groups = q.clusters;
  $('#mergeCount').textContent = model.merges === null || !groups.length ? '' : String(groups.length);
  if (model.merges === null) panel.appendChild(stateBlock({ kind: 'loading' }));
  else if (model.merges && model.merges.status) panel.appendChild(fromResult(model.merges, { what: 'The duplicate search', retry: () => { model.merges = null; renderReconcile(); pythia.refresh_merge_candidates(); } }));
  else if (!groups.length) panel.appendChild(stateBlock({ glyph: '✓', title: 'No possible duplicates', body: merges.length ? 'Those found are marked as not the same.' : 'Records with the same or a similar name would show here.' }));
  else {
    const needle = state.view === 'reconcile' ? state.search.toLowerCase() : '';
    groups.filter((c) => !needle || c.members.some((m) => `${m.name} ${m.path}`.toLowerCase().includes(needle))).forEach((c) => panel.appendChild(clusterCard(c)));
  }
}
// One card per cluster of possible duplicates (UX review A16; reconcile.js): why they look like one game, the
// record the others fold into (chosen by default, changeable), what moves, and the suggested action.
const SUGGEST_LABELS = { versions: 'Combine as versions', source: 'Same game, another source' };
function clusterCard(c, { handheld = false } = {}) {
  const choice = (m) => `<label class="cluster-member"><input type="radio" name="keep-${esc(c.id)}" value="${esc(m.game_id)}" ${m.game_id === c.target ? 'checked' : ''}><span><strong>${esc(m.name)}</strong> · ${esc((m.versions || []).join(', ') || 'no version')} · ${esc([...(m.sources || []).map((k) => kindName(k) || k), ...(m.stores || []).map((k) => storeName(k))].join(', ') || 'Not identified')}<br><small class="mono">${esc(PythiaVersions.middleEllipsis(m.path || 'Not installed here', 72))}</small></span></label>`;
  const card = el(`<section class="${handheld ? 'hh-panel' : 'version-group'} cluster" data-cluster="${esc(c.id)}"><div class="${handheld ? 'hh-panel-head' : 'version-head'}"><strong>${esc(c.name)}</strong><small>${fmt.n(c.members.length, 'record')}</small></div><div class="${handheld ? 'hh-panel-body' : ''}"><p class="look-reason">${esc(c.reason)}</p><div class="cluster-members" role="radiogroup" aria-label="The record to keep">${c.members.map(choice).join('')}</div><p class="look-reason" data-moves>${esc(c.moves)}</p><div class="choice-row"><button class="${handheld ? 'hh-action primary' : 'btn primary'}" type="button" data-combine>${esc(SUGGEST_LABELS[c.suggest])}</button><button class="${handheld ? 'hh-action' : 'btn ghost'}" type="button" data-apart>Not the same</button></div></div></section>`);
  const keepId = () => ($(`input[name="keep-${CSS.escape(c.id)}"]:checked`, card) || {}).value || c.target;
  $$('input[type=radio]', card).forEach((r) => (r.onchange = () => { const keep = c.members.find((m) => m.game_id === keepId()); const others = c.members.filter((m) => m !== keep); $('[data-moves]', card).textContent = `${others.map((m) => ((m.versions || []).length ? `${m.name} ${m.versions.join(', ')}` : m.name)).join('; ')} ${others.length === 1 ? 'moves' : 'move'} into ${keep.name}.`; }));
  $('[data-combine]', card).onclick = async () => {
    const keep = c.members.find((m) => m.game_id === keepId()); const others = c.members.filter((m) => m !== keep);
    if (await confirmDanger({ title: `${SUGGEST_LABELS[c.suggest]}?`, body: `${$('[data-moves]', card).textContent} Their versions, store ids and ownership move over; the other records are removed. Files stay where they are.`, recommendation: 'We recommend Cancel unless they are the same game.', items: others.map((m) => m.path || m.name), proceed: SUGGEST_LABELS[c.suggest] })) { pythia.merge_cluster(keep.game_id, others.map((m) => m.game_id)); flash('Combining'); }
  };
  $('[data-apart]', card).onclick = () => {
    const set = dismissedSet(); for (let i = 0; i < c.members.length; i++) for (let j = i + 1; j < c.members.length; j++) set.add([c.members[i].game_id, c.members[j].game_id].sort().join('+'));
    try { localStorage.setItem(DISMISS_KEY, JSON.stringify([...set])); } catch (e) { /* storage may be unavailable */ }
    flash('Kept apart'); renderCurrent(); renderChrome(); if (state.shell === 'handheld') renderHandheld();
  };
  return card;
}
const mergeClusters = () => PythiaReconcile.clusters(Array.isArray(model.merges) ? model.merges : [], dismissed);
const DISMISS_KEY = 'pythia-review-dismissed';
const dismissedSet = () => { try { return new Set(JSON.parse(localStorage.getItem(DISMISS_KEY) || '[]')); } catch (e) { return new Set(); } };
const pairKey = (c) => [c.a.game_id, c.b.game_id].sort().join('+');
const dismissed = (c) => dismissedSet().has(pairKey(c));
async function mergeInto(c, keepId, foldId) {
  const keep = c.a.game_id === keepId ? c.a : c.b; const fold = c.a.game_id === foldId ? c.a : c.b;
  if (await confirmDanger({ title: `Fold "${fold.name}" into "${keep.name}"?`, body: 'Its versions, store ids and ownership move over. The folded record is removed. Files stay where they are.', recommendation: 'We recommend Cancel unless both are the same game.', items: [`Keep: ${keep.path || keep.name}`, `Fold: ${fold.path || fold.name}`], proceed: 'Fold' })) { pythia.merge_games(keepId, foldId); flash('Folding'); }
}

// ---- Systems & sync --------------------------------------------------------------------
function renderShared() {
  const q = state.view === 'shared' ? state.search.toLowerCase() : '';
  $('#syncFolderPath').textContent = model.info?.sync_folder || '';
  loadInto($('#transferSettings'), 'get_transfer_settings', [], () => {}, { what: 'Game transfer' });
  loadInto($('#storageRoles'), 'get_storage_roles', [], () => {}, { what: 'Storage roles' });
  const list = systems.filter((s) => !q || s.name.toLowerCase().includes(q));
  $('#systemsSummary').textContent = model.ready ? `Computers that keep their records in one library folder · ${fmt.n(systems.length, 'computer')} so far` : '';
  const n = $('#systemsNotice'); n.innerHTML = '';
  if (model.info && systems.length < 2) n.appendChild(notice({ tone: 'info', placement: 'page', title: 'Share this library with another computer', body: 'Point a sync tool (Syncthing, a network drive, a cloud folder) at the library folder below on each computer. Each computer then shows here, with the games it has.', details: 'Handhelds do not use this folder: they pair on the Devices page.' }));
  $('#systemCards').innerHTML = list.length ? list.map((s) => `<article class="card"><div class="card-head"><div class="card-icon">${esc(s.icon)}</div><div><strong>${esc(s.name)}</strong><small>${s.mine ? esc(model.app ? `${model.app.platform} · Pythia ${model.app.version}` : 'This computer') : 'Another computer'}</small></div></div><div class="card-body"><div class="stats-grid" style="grid-template-columns:1fr 1fr;margin-bottom:0"><div class="stat"><div class="label">Tracked</div><div class="value">${esc(s.games)}</div></div><div class="stat"><div class="label">Installed</div><div class="value">${esc(s.installed)}</div></div></div></div><div class="card-foot"><span class="spacer"></span><button class="btn small" data-system-games="${esc(s.id)}">Games</button></div></article>`).join('') : '';
  $$('[data-system-games]').forEach((b) => (b.onclick = () => { state.search = ''; navigate('library'); $('#globalSearch').value = systemName(b.dataset.systemGames); state.search = systemName(b.dataset.systemGames); renderLibrary(); }));
  const head = $('#crossSystemHead'); const rows = $('#crossSystemRows'); const st = $('#crossSystemState'); st.innerHTML = '';
  const cross = games.filter((g) => g.systemIds.length > 1);
  $('#crossSystemCount').textContent = model.ready ? fmt.n(cross.length, 'game on more than one system', 'games on more than one system') : '';
  if (!model.ready) { head.innerHTML = ''; rows.innerHTML = ''; st.appendChild(stateBlock({ kind: 'loading' })); return; }
  if (!cross.length) { head.innerHTML = ''; rows.innerHTML = ''; st.appendChild(stateBlock({ title: systems.length > 1 ? 'No game is on two computers yet' : 'Only this computer so far', body: 'When another computer writes to the library folder, games known to both are compared here.' })); return; }
  head.innerHTML = `<tr><th>Game</th>${systems.map((s) => `<th>${esc(s.name)}</th>`).join('')}</tr>`;
  rows.innerHTML = cross.map((g) => `<tr><td class="primary-cell"><strong>${esc(g.name)}</strong><small>${esc(g.engine)}</small></td>${systems.map((s) => { const e = g.entries.find((x) => x.system_uuid === s.id); if (!e) return '<td>—</td>'; const v = (e.versions || []).map((x) => (x.version && x.version !== e.canonical_name ? x.version : null)).filter(Boolean); const inst = (e.versions || []).some((x) => (x.variants || []).some((y) => y.path)); return `<td>${inst ? `<span class="state-pill ok">${esc(v.join(', ') || 'installed')}</span>` : '<span class="state-pill info">known</span>'}</td>`; }).join('')}</tr>`).join('');
}
// ---- Handhelds: this computer as the droidtop agent (backend: pythia.droidtop) -----------
// Pythia's own droidtop module (pythia._native) holds the pairings; in a source tree where it
// was never built every slot answers "unavailable" and the panel says so.
let pairPoll = null;
// Whether this build starts Pythia's own games for a handheld (get_agent_status.host_launch).
let hostLaunch = false;
function renderDevices() { renderHandhelds(); }
function stopPairPoll() { if (pairPoll) { clearInterval(pairPoll); pairPoll = null; } }
function renderHandhelds() {
  const agentHost = $('#agentState'); const host = $('#handheldState');
  if (!agentHost || !host) return;
  // The handhelds list waits for the status, so its "Start games" words match this build.
  const status = loadInto(agentHost, 'get_agent_status', [], (st, h) => {
    const s = st.serving || {};
    hostLaunch = !!st.host_launch;
    // Sync off: one notice with its switch (renderAgentTakeover), and no serving line or addresses that do not apply.
    const modes = refreshModes().then(() => {
      if (!model.modes) return;
      const rows = el('<div class="device-modes"></div>');
      rows.appendChild(syncModeRow(model.modes, renderHandhelds));
      if ((model.modes.service || {}).available || (model.modes.service || {}).on) rows.appendChild(serviceModeRow(model.modes, renderHandhelds));
      h.prepend(rows);
    });
    if (!(st.agent || {}).pythia_serves) { renderAgentTakeover(h, st.agent || {}); return modes; }
    const line = s.state === 'serving' ? `${routineStatus('Serving')} Handhelds reach this computer as ${esc(st.identity?.name || '')}.`
      : s.state === 'stopped' ? visibleStatus(s.reason || 'Not serving handhelds', 'pending')
      : visibleStatus('Not serving handhelds yet', 'neutral', '–');
    h.appendChild(el(`<p>${line}</p>`));
    if ((st.places || []).length) h.appendChild(el(`<p><small>Addresses: ${(st.places || []).map((p) => `<span class="mono">${esc(p.address)}</span> (${esc(p.label)})`).join(' · ')}</small></p>`));
    renderAgentTakeover(h, st.agent || {});
  }, { what: 'This computer as a droidtop agent' });
  const startGamesText = () => (hostLaunch ? "Start games: it may start a game on this computer and play it there: the game runs here and only its windows are streamed to it through windowcast (the games Pythia tracks), or through Steam Link for Steam games (Steam must be running here)." : "Start games: it may start this computer's Steam games and play them through Steam Link; the game runs here, and Steam must be running here.");
  status.then(() => loadInto(host, 'get_handhelds', [], (r, h) => {
    const devices = r.devices || [];
    $('#handheldSummary').textContent = fmt.n(devices.length, 'paired device');
    $('#devicesCount').textContent = devices.length || '';
    if (!devices.length) { h.appendChild(stateBlock({ glyph: '▭', title: 'Pair your handheld', body: 'On the handheld, open droidtop > Settings > Computers > Pair a computer. Then choose Pair a device here: both screens show one code to compare. Another computer running Pythia pairs the same way.', actions: [{ label: 'Pair a device', primary: true, run: () => pairDevice() }] })); return; }
    const kindName = (d) => (d.kind === 'computer' ? 'Computer' : 'Handheld');
    // What this computer offers each device (droidtop-agent DESIGN section 20): a handheld starts with
    // its library and saves, a computer with nothing. "Saves win" is a handheld's only.
    const offered = (d) => d.offered || { library: d.kind !== 'computer', saves: d.kind !== 'computer' };
    const swRow = (attr, id, label, small) => `<div class="form-row"><div class="form-label"><strong>${esc(label)}</strong><small>${small}</small></div><button class="toggle" ${attr}="${esc(id)}" aria-label="${esc(label)}"><span></span></button></div>`;
    const seen = (d) => (d.last_seen_ms ? `seen ${fmt.ago(new Date(d.last_seen_ms).toISOString())}` : 'not seen yet');
    devices.forEach((d) => {
      const card = el(`<article class="device"><div class="device-head"><div class="card-icon">${d.kind === 'computer' ? 'PC' : '▭'}</div><div class="device-name"><strong>${esc(d.name)}</strong><small>${kindName(d)} · ${esc(seen(d))}</small></div><div class="inline-actions"><button class="btn small" data-device-games>Its games</button><button class="btn small" data-device-syncs>What syncs</button><button class="btn small ghost" data-device-more aria-haspopup="menu">More…</button></div></div><div class="device-switches">${swRow('data-offer-library', d.id, 'Share this library with it', 'It sees this computer’s games and can copy one when asked there.')}${swRow('data-offer-saves', d.id, 'Share saves', 'Saves are kept in step both ways. Needs the library.')}${d.kind === 'computer' ? '' : swRow('data-handheld-primary', d.id, 'Its saves win', 'When saves changed on both sides, this handheld’s copy is used and this computer’s goes to Save backups. Off: the newest wins.')}${swRow('data-handheld-launch', d.id, 'Let it start games here', esc(startGamesText().replace(/^Start games: /, '')))}</div></article>`);
      h.appendChild(card);
      $('[data-device-syncs]', card).onclick = () => showDeviceSyncs(d);
      $('[data-device-games]', card).onclick = () => showOfferedGames(d);
      const forget = async () => { if (await confirmDanger({ title: `Forget ${d.name}?`, body: 'It can no longer sync with this computer until you pair it again.', recommendation: 'We recommend Cancel.', proceed: 'Forget' })) await act('forget_handheld', [d.id], `Forgot ${d.name}`, { what: 'Forgetting it' }); };
      const details = () => infoSheet({ title: d.name, details: [`${kindName(d)}`, `Device ID: ${d.id}`, `Last seen: ${d.last_seen_ms ? fmt.dateTime(new Date(d.last_seen_ms).toISOString()) : 'never'}`, d.last_address ? `Last address: ${d.last_address}` : ''].filter(Boolean).join('\n') });
      const more = $('[data-device-more]', card);
      more.onclick = () => menu(more, [{ label: 'Details', run: details }, { label: 'Forget…', run: forget }], { returnTo: more });
      const offer = offered(d);
      const flip = (node, on) => { node.classList.toggle('on', !on); node.setAttribute('aria-checked', String(!on)); };
      const offerSwitch = (what, word) => bindSwitch($(`[data-offer-${what}]`, card), !!offer[what], async (on, node) => { const r2 = await act('set_device_offer', [d.id, what, on], on ? `Sharing ${word} with ${d.name}` : `No longer sharing ${word} with ${d.name}`, { what: 'Changing it' }); if (!ok(r2)) flip(node, on); else renderHandhelds(); });
      offerSwitch('library', 'this library');
      offerSwitch('saves', 'saves');
      if (d.kind !== 'computer') bindSwitch($('[data-handheld-primary]', card), !!d.primary, async (on, node) => { const r2 = await act('set_primary_handheld', [on ? d.id : ''], on ? `${d.name}'s saves win` : 'The newest saves win', { what: 'Changing it' }); if (!ok(r2)) flip(node, on); else renderHandhelds(); });
      bindSwitch($('[data-handheld-launch]', card), !!d.launch, async (on, node) => { const r2 = await act('set_handheld_launch', [d.id, on], on ? `${d.name} may start games here` : `${d.name} may no longer start games here`, { what: 'Changing it' }); if (!ok(r2)) flip(node, on); });
    });
  }, { what: 'Handhelds' }));
  renderHandheldGames();
}
// droidtop-agent, the separate program: Pythia turns it off while it serves the handhelds (keeping its
// start at sign-in to put back), and syncs its pairings, library and save copies both ways.
function renderAgentTakeover(h, a) {
  const running = a.running || [];
  if (!a.pythia_serves) {
    h.appendChild(el(`<p><small>You can pair a device while this is off; it syncs with this computer once it is on.</small></p>`));
    return;
  }
  if (running.length) {
    h.appendChild(el(`<p>${visibleStatus(`droidtop-agent is still running (${running.map((p) => `${p.name} ${p.pid}`).join(', ')}) and did not stop when asked, so it serves the handhelds instead of Pythia.`, 'pending')}</p>`));
    return;
  }
  if (!a.turned_off) return;
  const since = a.turned_off.since ? fmt.dateTime(new Date(a.turned_off.since).toISOString()) : '';
  h.appendChild(el(`<p><small>droidtop-agent is turned off while Pythia serves your handhelds${since ? ` (since ${esc(since)})` : ''}. Its pairings, library and save copies are kept in step with Pythia's.</small></p>`));
  const b = el('<p><button class="btn small">Turn droidtop-agent back on…</button></p>');
  b.querySelector('button').onclick = async () => {
    if (!(await confirmDanger({ title: 'Turn droidtop-agent back on?', body: 'Its start at sign-in is put back as it was, with Pythia\'s pairings and library written into its folder. Pythia keeps serving your handhelds until it quits, and leaves them to droidtop-agent from its next start.', recommendation: 'We recommend Cancel unless you want droidtop-agent to serve your handhelds instead of Pythia.', proceed: 'Turn it back on' }))) return;
    const r = await act('turn_agent_back_on', [], 'droidtop-agent starts again at your next sign-in', { what: 'Turning droidtop-agent back on' });
    if (ok(r)) renderHandhelds();
  };
  h.appendChild(b);
}
// What each paired handheld has installed, from the library it syncs with this computer.
function renderHandheldGames() {
  const host = $('#handheldGames'); if (!host) return;
  $('#handheldGamesSummary').textContent = '';
  loadInto(host, 'get_handheld_games', [], (r, h) => {
    const handhelds = r.handhelds || [];
    if (!handhelds.length) { h.appendChild(stateBlock({ title: 'No handheld is paired', body: 'A paired handheld\'s games show here once it syncs with this computer.' })); return; }
    $('#handheldGamesSummary').textContent = fmt.n(handhelds.reduce((n, d) => n + d.games.length, 0), 'game');
    handhelds.forEach((d) => {
      h.appendChild(el(`<h3 style="font:500 1rem var(--serif);margin:6px 0">${esc(d.name)} <small>${esc(fmt.n(d.games.length, 'game'))}</small></h3>`));
      if (!d.games.length) { h.appendChild(stateBlock({ title: 'Nothing from it yet', body: 'It sends the games it has when it syncs with this computer.' })); return; }
      h.appendChild(el(`<table class="table"><thead><tr><th>Game</th><th>Platform</th><th>Version</th></tr></thead><tbody>${d.games.map((g) => `<tr><td class="primary-cell"><strong>${esc(g.title)}</strong><small class="mono">${esc(g.key)}</small></td><td>${esc(g.platform ? platformName(g.platform) : '—')}</td><td>${esc(g.version || '—')}</td></tr>`).join('')}</tbody></table>`));
    });
  }, { what: 'Handheld games' });
}
async function pairHandheld() {
  const code = await prompt({ title: 'Pair a handheld', body: 'On the handheld: Settings > Computers > Pair a computer. Type the 6 digits it shows, or the text of its QR code.', hint: '123 456', save: 'Pair' });
  if (!code || !code.trim()) return;
  const r = await call('pair_with_code', code.trim());
  if (ok(r)) flash('Pairing…'); else flash(r.status === 'unavailable' ? r.message : `Not paired: ${r.message}`, 'caution');
}
// One pairing-code panel for both shells: `host` is where it draws, `showing()` whether its page is still open,
// `paired()` redraws that page's handheld list.
async function showPairCode(host = $('#pairShowing'), showing = () => state.view === 'devices', paired = renderHandhelds) {
  if (!host) return;
  stopPairPoll();
  const r = await call('show_pairing_code');
  host.innerHTML = '';
  if (!ok(r)) { host.appendChild(fromResult(r, { what: 'Showing a code' })); return; }
  const stop = async () => { stopPairPoll(); await call('cancel_pairing'); host.innerHTML = ''; };
  const box = el(`<div class="panel" style="margin-bottom:13px"><div class="panel-body"><p>On the handheld: Settings > Computers > Pair a computer > Use a code from the computer, and type:</p><p><strong class="mono" style="font-size:2rem;letter-spacing:0.1em">${esc(r.code.slice(0, 3))} ${esc(r.code.slice(3))}</strong></p><p>${(r.places || []).map((p) => `<span class="mono">${esc(p.address)}</span> <small>${esc(p.label)}</small>`).join('<br>')}</p><p><small>The code works for ${Math.ceil((r.seconds || 0) / 60)} minutes. For a device with a camera, the same invitation as text:</small><br><small class="mono" style="word-break:break-all">${esc(r.invite)}</small></p><p id="pairOutcome"></p><button class="btn small" data-pair-stop>Stop</button></div></div>`);
  host.appendChild(box);
  $('[data-pair-stop]', box).onclick = stop;
  pairPoll = setInterval(async () => {
    if (!showing()) { stop(); return; }
    const s = await call('pairing_status');
    if (!ok(s) || s.state === 'waiting') return;
    stopPairPoll();
    if (s.state === 'paired') { host.innerHTML = ''; flash(`Paired with ${s.paired}`); paired(); }
    else if (s.state === 'failed') { $('#pairOutcome', box).innerHTML = visibleStatus(s.error || 'Pairing stopped', 'critical'); }
  }, 1500);
}
// ---- Pair a device: one code on both screens (docs/SPEC.md 7.1-7.5; UX review AG5) -------------
// While the panel is open this computer can be picked (droidtop's Pair a computer, or another Pythia's "Pair with
// another computer"), and only then. Both screens then show one code; the person says whether they match on both.
const PAIR_NETWORK = 'To pair, Pythia talks to the handheld or the other computer over your home network: it listens on TCP and UDP port 47610 and TCP 47612 while you pair. Your system may ask whether Pythia may use the network: allow it on private networks.';
let pairCompare = null;
function stopPairCompare() { if (pairCompare) { clearInterval(pairCompare); pairCompare = null; } }
async function pairDevice(host = $('#pairShowing'), showing = () => state.view === 'devices', paired = renderHandhelds) {
  if (!host) return;
  stopPairPoll(); stopPairCompare();
  // Pairing a handheld is mostly for Sync with droidtop: offered here once, so the first pairing also turns it on.
  if (!model.modes) await refreshModes();
  const sync = model.modes?.sync;
  if (sync?.available && !sync.on && (await confirm({ title: 'Turn on Sync with droidtop too?', body: `A paired device syncs with this computer only while Sync with droidtop is on. ${SYNC_CONSENT}`, cancel: 'Pair only', proceed: 'Turn on and pair' }))) {
    await act('serve_from_pythia_again', [], 'Sync with droidtop on', { what: 'Turning on Sync with droidtop' });
    await refreshModes();
  }
  const told = await call('pairing_network_explained');
  if (ok(told) && !told.explained) {
    if (!(await confirm({ title: 'Pairing uses your network', body: PAIR_NETWORK, proceed: 'Continue' }))) return;
    await call('mark_pairing_network_explained');
  }
  const r = await call('start_pair_offer');
  host.innerHTML = '';
  if (!ok(r)) { host.appendChild(fromResult(r, { what: 'Pairing' })); return; }
  const box = el('<div class="panel" style="margin-bottom:13px"><div class="panel-body" data-pair-body></div></div>');
  host.appendChild(box);
  const body = $('[data-pair-body]', box);
  const close = async () => { stopPairCompare(); await call('stop_pair_offer'); host.innerHTML = ''; };
  // mode: waiting (offering), finding (this screen looks for computers), code (both screens compare), done.
  let waitedFrom = 0; let answering = false; let mode = 'waiting'; let drawnLate = null;
  const fallbacks = () => '<button class="btn small" data-pair-type>Type a code instead</button> <button class="btn small" data-pair-show>Show a code instead</button>';
  const wire = () => {
    $('[data-pair-stop]', body)?.addEventListener('click', close);
    $('[data-pair-type]', body)?.addEventListener('click', async () => { await close(); pairHandheld(); });
    $('[data-pair-show]', body)?.addEventListener('click', async () => { await close(); showPairCode(host, showing, paired); });
    $('[data-pair-wait]', body)?.addEventListener('click', () => { waitedFrom = null; drawnLate = null; });
    $('[data-pair-find]', body)?.addEventListener('click', () => { mode = 'finding'; findComputers(body, wire, showing); });
  };
  const drawWaiting = (seconds) => {
    const late = waitedFrom !== null && seconds - (waitedFrom || 0) >= 60;
    const left = Math.max(0, 60 - (seconds - (waitedFrom || 0)));
    if (drawnLate === late) { const secs = $('[data-pair-secs]', body); if (secs) secs.textContent = `(${left} s)`; return; }
    drawnLate = late;
    body.innerHTML = late
      ? `<p>${visibleStatus('Nothing has picked this computer yet', 'pending')}</p><p>Check that the handheld is on the same network as this computer, and that droidtop is open on Settings > Computers > Pair a computer.</p><p><button class="btn small" data-pair-wait>Keep waiting</button> ${fallbacks()} <button class="btn small ghost" data-pair-stop>Stop</button></p>`
      : `<p><strong>Waiting for a handheld or computer…</strong> <small data-pair-secs>(${left} s)</small></p><p>On the handheld: droidtop > Settings > Computers > Pair a computer, and pick <strong>Pythia on ${esc(r.name || 'this computer')}</strong>. On another computer: Pythia > Devices > Pair a device > Pair with another computer.</p><p><button class="btn small" data-pair-find>Pair with another computer</button> ${fallbacks()} <button class="btn small ghost" data-pair-stop>Stop</button></p>`;
    wire();
  };
  const drawCode = (code, name) => {
    mode = 'code';
    body.innerHTML = `<p>${name ? `Pairing with <strong>${esc(name)}</strong>. ` : ''}Does the other screen show this code?</p><p><strong class="mono" style="font-size:2.4rem;letter-spacing:0.12em">${esc(code.slice(0, 3))} ${esc(code.slice(3))}</strong></p><p><button class="btn primary" data-pair-same>Same code</button> <button class="btn" data-pair-differ>They don't match</button></p><p id="pairAnswer"></p>`;
    const answer = async (same) => {
      answering = true;
      $$('button', body).forEach((b) => (b.disabled = true));
      $('#pairAnswer', body).innerHTML = visibleStatus(same ? 'Waiting for the other device to answer…' : 'Telling the other device…', 'neutral', '…');
      const a = await call('confirm_pairing', same);
      mode = 'done'; stopPairCompare(); await call('stop_pair_offer');
      if (ok(a)) { host.innerHTML = ''; flash(`Paired with ${a.paired}`); paired(); return; }
      // The crates' words nest ("Nothing was paired: pairing failed: ..."); the page says it once.
      const why = String(a.message || '').replace(/^Nothing was paired: /, '').replace(/^pairing failed: /, '').replace(/; nothing was paired$/, '');
      body.innerHTML = `<p>${visibleStatus(same ? `Not paired: ${why}. Nothing was saved on either device.` : 'Not paired: the codes did not match. Nothing was saved on either device.', 'critical')}</p><p><button class="btn small" data-pair-again>Try again</button> ${fallbacks()}</p>`;
      $('[data-pair-again]', body).onclick = () => pairDevice(host, showing, paired);
      wire();
    };
    $('[data-pair-same]', body).onclick = () => answer(true);
    $('[data-pair-differ]', body).onclick = () => answer(false);
  };
  let shown = '';
  const tick = async () => {
    if (!showing()) { close(); return; }
    if (answering) return;
    const st = await call('pair_compare_status');
    if (!ok(st)) return;
    if (st.state === 'offering') { if (mode !== 'waiting') return; if (waitedFrom === null) waitedFrom = st.seconds; drawWaiting(st.seconds); shown = 'offering'; }
    else if (st.state === 'comparing' && shown !== `code:${st.code}`) { shown = `code:${st.code}`; drawCode(st.code, st.name); }
    else if (st.state === 'failed' && shown !== 'failed' && mode !== 'done') { shown = 'failed'; mode = 'done'; stopPairCompare(); body.innerHTML = `<p>${visibleStatus(`Not paired: ${st.error}`, 'critical')}</p><p><button class="btn small" data-pair-again>Try again</button> ${fallbacks()}</p>`; $('[data-pair-again]', body).onclick = () => pairDevice(host, showing, paired); wire(); }
  };
  pairCompare = setInterval(tick, 1000);
  tick();
  // Picking another computer: this screen shows the code at once.
  async function findComputers(target, rewire, still) {
    target.innerHTML = `<p>${visibleStatus('Looking for computers on this network…', 'neutral', '…')}</p>`;
    const f = await call('find_computers');
    if (!still()) return;
    const list = ok(f) ? f.computers || [] : [];
    if (!list.length) {
      target.innerHTML = `<p>${visibleStatus(ok(f) ? 'No computer is offering to pair on this network.' : f.message, 'pending')}</p><p>On the other computer, open Pythia's Devices page and choose Pair a device. It must be on the same network.</p><p><button class="btn small" data-pair-find>Look again</button> <button class="btn small ghost" data-pair-stop>Stop</button></p>`;
      rewire(); return;
    }
    target.innerHTML = `<p>Pick the computer to pair with:</p><p>${list.map((c, i) => `<button class="btn small" data-pick="${i}">${esc(c.program === 'pythia' ? `Pythia on ${c.name}` : c.name)}</button>`).join(' ')}</p><p><button class="btn small ghost" data-pair-stop>Stop</button></p>`;
    $$('[data-pick]', target).forEach((b) => (b.onclick = async () => {
      answering = true;
      target.innerHTML = `<p>${visibleStatus('Connecting…', 'neutral', '…')}</p>`;
      const p = await call('pick_computer', list[Number(b.dataset.pick)]);
      if (!ok(p)) { answering = false; mode = 'finding'; target.innerHTML = `<p>${visibleStatus(`Not paired: ${p.message}`, 'critical')}</p><p><button class="btn small" data-pair-find>Look again</button> <button class="btn small ghost" data-pair-stop>Stop</button></p>`; rewire(); return; }
      answering = false; shown = `code:${p.code}`; drawCode(p.code, p.name);
    }));
    rewire();
  }
}
// What a paired device offers this computer (droidtop-agent DESIGN section 20): its games, each to copy
// here or to play there. The device decides what it answers; reaching it can take a few seconds.
async function showOfferedGames(d) {
  const r = await call('device_offered', d.id);
  if (!ok(r)) { flash(r.message || 'Not available', 'caution'); return; }
  const what = [r.offered?.library && 'its library', r.offered?.saves && 'its saves'].filter(Boolean);
  const games = r.games || [];
  const said = what.length ? `${esc(r.name || d.name)} offers this computer ${what.join(' and ')}.` : `${esc(r.name || d.name)} offers this computer nothing. It can offer its library on its own Devices page.`;
  const size = (n) => (n ? `${(n / (1024 * 1024 * 1024)).toFixed(1)} GiB` : '');
  const node = el(`<div><p>${said}</p>${games.length ? `<table class="table"><thead><tr><th>Game</th><th>Version</th><th>Size</th><th></th></tr></thead><tbody>${games.map((g, i) => `<tr><td class="primary-cell"><strong>${esc(g.title)}</strong>${g.accepted ? '<small>Copied here before</small>' : ''}</td><td>${esc(g.version || '')}</td><td>${size(g.size)}</td><td><button class="btn small" data-offered-copy="${i}">Copy here</button>${r.offered?.launch ? ` <button class="btn small" data-offered-play="${i}">Play there</button>` : ''}</td></tr>`).join('')}</tbody></table><p><small>Copy here puts the game in this computer's first game folder as a new folder and adds it to the library; nothing already here is changed. Play there starts it on ${esc(r.name || d.name)}, if it lets this computer, and only the game's windows are streamed here.</small></p>` : ''}</div>`);
  $$('[data-offered-copy]', node).forEach((b) => (b.onclick = async () => { const g = games[Number(b.dataset.offeredCopy)]; b.disabled = true; await act('accept_offered_game', [d.id, g.key, g.title], `Copying ${g.title}; it shows under Activity`, { what: 'Copying it' }); }));
  $$('[data-offered-play]', node).forEach((b) => (b.onclick = async () => { const g = games[Number(b.dataset.offeredPlay)]; b.disabled = true; const r2 = await call('play_on_device', d.id, g.key, g.title); flash(r2.message || (ok(r2) ? 'Started there.' : 'It did not start.'), ok(r2) ? 'info' : 'caution'); b.disabled = false; }));
  infoSheet({ title: `On ${r.name || d.name}`, node, wide: true });
}
// What syncs with a paired device (UX review AG6), from the backend's sync model; game files never sync.
async function showDeviceSyncs(d) {
  const r = await call('device_syncs', d.id);
  if (!ok(r)) { flash(r.message || 'Not available', 'caution'); return; }
  const node = el(`<div><table class="table"><tbody>${(r.rows || []).map((row) => `<tr><td><strong>${esc(row.what)}</strong></td><td>${row.syncs ? routineStatus('Syncs') : visibleStatus('Does not sync', 'neutral', '–')}</td><td><small>${esc(row.detail)}</small></td></tr>`).join('')}</tbody></table></div>`);
  infoSheet({ title: `What syncs with ${r.name || d.name}`, node, wide: true });
}
function updateSystems(raw) {
  const by = new Map();
  (raw || []).forEach((r) => (r.entries || []).forEach((e) => { const uuid = e.system_uuid; if (!uuid) return; const s = by.get(uuid) || { games: 0, installed: 0 }; s.games++; if ((e.versions || []).some((v) => (v.variants || []).some((x) => x.path))) s.installed++; by.set(uuid, s); }));
  if (model.mySystem && !by.has(model.mySystem)) by.set(model.mySystem, { games: 0, installed: 0 });
  systems = [...by.entries()].map(([uuid, s]) => ({ id: uuid, icon: uuid === model.mySystem ? 'PC' : uuid.slice(0, 2).toUpperCase(), name: systemName(uuid), games: s.games, installed: s.installed, mine: uuid === model.mySystem })).sort((a, b) => (a.mine ? -1 : b.mine ? 1 : a.name.localeCompare(b.name)));
  if (state.view === 'shared') renderShared();
}

// ---- Launcher entries and Save backups: wired, honest, not built yet ----------------------
// What each launcher on this computer lists (backend get_launcher_entries, from droidtop's scanner), against the library.
function renderSources() {
  loadInto($('#libraryFolders'), 'get_library_folders', [], (r, host) => { (r.folders || []).forEach((f) => host.appendChild(el(`<div class="account-row"><div class="card-icon">F</div><div><strong>${esc(f.path)}</strong></div></div>`))); if (!(r.folders || []).length) host.appendChild(stateBlock({ title: 'No game folder yet', body: 'Add a folder that holds games, or scan one that holds many.' })); }, { what: 'Game folders', actions: [{ label: 'Scan a folder', primary: true, run: scanLibrary }] });
  loadInto($('#scanSettings'), 'get_scan_settings', [], () => {}, { what: 'Scan settings' });
  renderCapabilityList($('#view-sources [data-capability-list="library"]'), 'library');
  renderLauncher();
}
function renderLauncher() {
  $('#launcherSummary').textContent = ''; $('#launcherRows').innerHTML = '';
  const q = state.view === 'sources' ? state.search.toLowerCase() : '';
  loadInto($('#launcherState'), 'get_launcher_entries', [], (r) => {
    const entries = r.entries || [];
    const shown = entries.filter((e) => !q || [e.game, e.manager, e.folder].join(' ').toLowerCase().includes(q));
    const untracked = entries.filter((e) => e.state === 'untracked').length;
    $('#launcherSummary').textContent = `${fmt.n(entries.length, 'game')} · ${fmt.n(untracked, 'game')} not in the library${shown.length === entries.length ? '' : ` · ${shown.length} shown`}`;
    const stateCell = (e) => (e.state === 'tracked' ? `${routineStatus('In library')} In library`
      : e.state === 'drifted' ? `<span class="state-pill pending">Folder moved</span><small class="mono">Library: ${esc(e.library_folder || '')}</small>`
      : '<span class="state-pill info">Not in library</span>');
    $('#launcherRows').innerHTML = shown.map((e, i) => `<tr><td class="primary-cell"><strong>${esc(e.game)}</strong><small class="mono">${esc(e.folder)}</small></td><td>${esc(e.manager)}</td><td>${stateCell(e)}</td><td>${e.state === 'untracked' ? `<button class="btn small" data-record-entry="${i}">Record</button>` : ''}</td></tr>`).join('');
    $$('[data-record-entry]', $('#launcherRows')).forEach((b) => (b.onclick = async () => {
      const e = shown[Number(b.dataset.recordEntry)]; b.disabled = true;
      const r2 = await act('record_launcher_entry', [e.folder, e.key], `Recorded ${e.game}`, { what: `Recording ${e.game}` });
      if (ok(r2)) renderLauncher(); else b.disabled = false;
    }));
    if (!entries.length) $('#launcherState').appendChild(stateBlock({ title: 'No launcher lists a game here', body: 'Games that Steam, Epic, GOG, Amazon, itch, Battle.net, Lutris, Bottles or Minigalaxy have installed on this computer show here.' }));
    else if (!shown.length) $('#launcherState').appendChild(stateBlock({ title: 'No launcher entries match' }));
  }, { what: 'Launcher entries' });
}
function renderSaves() {
  $('#savesSummary').textContent = ''; $('#saveRows').innerHTML = '';
  loadInto($('#savesState'), 'get_save_backups', [], (r) => {
    const backups = r.backups || [];
    $('#savesSummary').textContent = `${fmt.n(backups.length, 'kept copy', 'kept copies')}`;
    $('#saveRows').innerHTML = backups.map((b) => `<tr><td class="primary-cell"><strong>${esc(b.game)}</strong><small>${esc(b.system || '')}</small></td><td>${esc(fmt.dateTime(b.last_snapshot))}</td><td>${esc(b.state)}</td><td>${esc(b.size || '—')}</td><td><button class="btn small" data-restore-copy="${esc(b.path)}">Put back…</button></td></tr>`).join('');
    backups.forEach((b) => {
      const btn = $(`[data-restore-copy="${CSS.escape(b.path)}"]`, $('#saveRows'));
      if (!btn) return;
      btn.onclick = async () => {
        if (!(await confirm({ tone: 'caution', title: `Put this copy of ${b.game}'s saves back?`, body: 'The saves there now are kept first, as this computer\'s own copy, so nothing is lost.', details: `${b.system}\n${b.path}`, proceed: 'Put back' }))) return;
        btn.disabled = true;
        const r = await act('restore_save_copy', [b.key || '', b.path, b.title || ''], null, { what: 'Putting the saves back' });
        btn.disabled = false;
        if (ok(r)) { if (r.files) flash(`Put back ${fmt.n(r.files, 'file')}`); else flash('Nothing in that copy matches where the game keeps its saves; nothing was put back.', 'caution'); renderSaves(); }
      };
    });
    if (!backups.length) $('#savesState').appendChild(stateBlock({ title: 'No copies of saves kept', body: 'A copy is kept here when a sync with a handheld would overwrite saves that differ.' }));
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
// Settings pages that became pages of their own, or one Plugins page, in the UI round.
const MOVED_PANES = { library: ['sources'], sync: ['shared'], sources: ['plugins', 'library'], downloaders: ['plugins', 'downloader'], metadata: ['plugins', 'metadata'], managers: ['plugins', 'manager'], engines: ['plugins', 'engine'], mods: ['plugins', 'mod'], patchers: ['plugins', 'patcher'] };
function openSettingsPane(name) {
  const moved = MOVED_PANES[name];
  if (moved && moved.length === 1) { navigate(moved[0]); return; }
  if (moved) { name = moved[0]; state.pluginKind = moved[1]; }
  state.settingsPane = name; const btn = $$('[data-settings]').find((b) => b.dataset.settings === name); if (btn) { $$('[data-settings]').forEach((x) => x.classList.toggle('active', x === btn)); $$('.settings-pane').forEach((p) => p.classList.toggle('active', p.dataset.pane === name)); } if (state.view !== 'settings') navigate('settings'); else renderSettingsPane(name); }
function renderSettingsPane(name) {
  const info = model.info || {}; const app = model.app || {};
  if (name === 'general') renderModes($('#modesState'));
  if (name === 'general') { $('#appDataPath').textContent = info.app_data_folder || '';  }
  if (name === 'plugins') { renderPlugins(!state.search); loadInto($('#pluginRepos'), 'get_plugin_repositories', [], (r, host) => { if (!(r.repositories || []).length) host.appendChild(stateBlock({ title: 'No repositories added' })); }, { what: 'Plugin repositories' }); }
  if (name === 'advanced') { $('#systemIdField').value = info.system_uuid || ''; $('#dbPath').textContent = info.app_data_folder || ''; $('#schemaVersion').innerHTML = app.schema_version ? `${routineStatus('Up to date')}v${esc(app.schema_version)}` : ''; $('#logLevelSelect').value = model.config?.log_level === 'debug' ? 'debug' : 'info'; $('#consoleHint').textContent = model.config?.error_console ? 'On for this run. Ctrl+Shift+E toggles it.' : 'Off. Start Pythia with --error-console, or set error_console = True in pythia.conf.'; }
}
// Settings shows what can be set (UI review 2026-10-23, U4): a section whose slot answers "not built yet",
// or a plugin kind with no plugin, is hidden, and so is a page left with nothing. The slots stay, so a
// section comes back on its own once it is built or a plugin of that kind is installed.
async function trimSettings() {
  const view = $('#view-settings');
  const hide = (node) => node?.closest('.form-section')?.classList.add('hidden');
  const unbuilt = [['get_library_folders', '#libraryFolders'], ['get_scan_settings', '#scanSettings'], ['get_transfer_settings', '#transferSettings'], ['get_storage_roles', '#storageRoles']];
  await Promise.all(unbuilt.map(([slot, sel]) => call(slot).then((r) => { if (r?.status === 'unavailable') hide($(sel)); })));
  // A panel left with only hidden sections goes too (Game sources' folders while they are not built).
  $$('.panel').forEach((panel) => { const secs = $$('.form-section', panel); if (secs.length && secs.every((x) => x.classList.contains('hidden'))) panel.classList.add('hidden'); });
  call('get_tools').then((r) => { if (r?.status === 'unavailable') $$('[data-action="tools"]').forEach((b) => b.classList.add('hidden')); });
  $$('.settings-pane', view).forEach((pane) => {
    const empty = $$('.form-section', pane).every((sec) => sec.classList.contains('hidden'));
    const btn = $$('[data-settings]', view).find((b) => b.dataset.settings === pane.dataset.pane);
    if (btn) btn.classList.toggle('hidden', empty);
  });
}
// What one plugin's kind says about it now: a store's account, a launcher's presence here, an engine tool.
function capabilityState(category, p) {
  const s = p.state || {}; let status, extra = '', sub = '';
  if (s.error) status = visibleStatus('Check failed', 'critical');
  else if (s.unknown) status = visibleStatus('Not reported', 'neutral', '–');
  else if (category === 'library') { status = s.has_account ? (s.connected ? routineStatus('Connected') : visibleStatus('Not connected', 'neutral', '–')) : ''; if (s.has_account) extra = `<button class="btn small" data-account="${esc(p.id)}">Account</button>`; }
  else if (category === 'manager') { status = s.available ? routineStatus('Installed here') : visibleStatus('Not on this computer', 'neutral', '–'); sub = `Opens games from ${(s.sources || []).map((k) => kindName(k) || k).join(', ') || 'any source'}`; }
  else if (category === 'engine') { status = s.installed ? routineStatus('Ready') : visibleStatus('Tool missing', 'pending'); sub = s.tool_version ? `Tool ${s.tool_version}` : ''; }
  else if (category === 'downloader') { status = s.watching ? routineStatus('Watching') : routineStatus('Idle', 'neutral', '–'); sub = s.watching ? `Watching ${s.watching}` : ''; extra = `<button class="btn small" data-watch-config>${s.watching ? 'Stop' : 'Watch a folder'}</button>`; }
  else status = p.enabled ? routineStatus('Ready') : visibleStatus('Turned off', 'neutral', '–');
  if (!p.enabled) status = visibleStatus('Turned off', 'neutral', '–');
  return { status, extra, sub: s.error || sub };
}
function capabilityRow(category, p, redraw) {
  const { status, extra, sub } = capabilityState(category, p);
  const row = el(`<div class="account-row"><div class="card-icon">${esc(p.display_name.slice(0, 2).toUpperCase())}</div><div><strong>${esc(p.display_name)}</strong>${sub ? `<small>${esc(sub)}</small>` : ''}</div><div class="row-status">${status}${extra}<button class="btn small ghost" data-plugin-details="${esc(p.id)}">Details</button><button class="toggle" aria-label="Turn on ${esc(p.display_name)}"><span></span></button></div></div>`);
  bindSwitch($('.toggle', row), p.enabled, (on) => act('set_plugin_enabled', [p.id, on], on ? `${p.display_name} turned on` : `${p.display_name} turned off`, { what: 'Turning the plugin' }).then((res) => { if (ok(res)) refreshPlugins().then(redraw); }), `Turn on ${p.display_name}`);
  $('[data-account]', row)?.addEventListener('click', () => accountDialog(p.id));
  $('[data-watch-config]', row)?.addEventListener('click', () => ((p.state || {}).watching ? stopWatching() : watchFolder(p.id)));
  $('[data-plugin-details]', row).onclick = () => pluginDetails(p.id);
  return row;
}
function renderCapabilityList(host, category) {
  if (!host) return;
  loadInto(host, 'get_capabilities', [category], (r, h) => {
    const list = r.plugins || [];
    if (!list.length) { h.appendChild(stateBlock({ title: `No ${CATEGORY_NAMES[category].toLowerCase()} plugins`, body: 'A plugin of this kind adds a row here.' })); return; }
    list.forEach((p) => h.appendChild(capabilityRow(category, p, () => renderCapabilityList(host, category))));
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
// One Plugins page (UI round): every plugin once, by kind, with its kind's own state; a filter by kind and the
// page's search. Engine detectors are many and alike, so "All" folds them into one row that opens.
const PLUGIN_KINDS = ['library', 'downloader', 'metadata', 'manager', 'engine', 'mod', 'patcher', 'source'];
const PLUGIN_KIND_NAMES = { library: 'Stores', downloader: 'Downloaders', metadata: 'Metadata', manager: 'Launchers', engine: 'Engine detection', mod: 'Mods', patcher: 'Patchers', source: 'Remote sources' };
function pluginDetails(id) {
  const p = plugins.find((x) => x.id === id); if (!p) return;
  const a = model.accounts.find((x) => x.id === p.id);
  infoSheet({ title: p.name, details: [`Id: ${p.id}`, `Version: ${p.version}`, `Origin: ${p.origin}`, `Adds: ${p.kind}`, p.origin !== 'Built-in' ? 'Unofficial: it came from outside Pythia and runs with the same access as Pythia.' : 'Official: built into Pythia.', a ? `Account: ${a.connected ? 'connected' : 'not connected'} (${a.fields.map((f) => f.label).join(', ')})` : ''].filter(Boolean).join('\n') });
}
async function renderPlugins(fresh = false) {
  const st = $('#pluginsState'); const list = $('#pluginList'); const filter = $('#pluginFilter');
  if (!st || !list) return;
  if (!model.ready) { list.innerHTML = ''; st.replaceChildren(stateBlock({ kind: 'loading' })); return; }
  if (fresh || !model.capabilities) {
    if (!model.capabilities) st.replaceChildren(stateBlock({ kind: 'loading' }));
    const answers = await Promise.all(PLUGIN_KINDS.map((k) => call('get_capabilities', k).then((r) => [k, ok(r) ? r.plugins || [] : []])));
    model.capabilities = Object.fromEntries(answers);
  }
  st.innerHTML = '';
  const caps = model.capabilities;
  const kinds = PLUGIN_KINDS.filter((k) => (caps[k] || []).length);
  if (state.pluginKind && !kinds.includes(state.pluginKind)) state.pluginKind = '';
  const kind = state.pluginKind || '';
  filter.innerHTML = ['', ...kinds].map((k) => `<button class="chip${k === kind ? ' active' : ''}" data-plugin-kind="${k}" aria-pressed="${k === kind}">${esc(k ? PLUGIN_KIND_NAMES[k] : 'All')} <small>${k ? caps[k].length : plugins.length}</small></button>`).join('');
  $$('[data-plugin-kind]', filter).forEach((b) => (b.onclick = () => { state.pluginKind = b.dataset.pluginKind; renderPlugins(); }));
  const q = state.view === 'settings' ? state.search.toLowerCase() : '';
  const matches = (p) => !q || [p.display_name, p.id, PLUGIN_KIND_NAMES[kindOf(p.id)] || ''].join(' ').toLowerCase().includes(q);
  // A plugin's first kind is where it is listed under All; its other kinds list it again when picked.
  const kindOf = (id) => kinds.find((k) => caps[k].some((x) => x.id === id));
  list.innerHTML = '';
  const redraw = () => renderPlugins(true);
  if (kind) {
    caps[kind].filter(matches).forEach((p) => list.appendChild(capabilityRow(kind, p, redraw)));
  } else {
    const seen = new Set();
    kinds.forEach((k) => {
      const mine = caps[k].filter((p) => !seen.has(p.id) && matches(p));
      mine.forEach((p) => seen.add(p.id));
      if (!mine.length) return;
      if (k === 'engine' && !q) {
        const ready = mine.filter((p) => p.enabled && (p.state || {}).installed !== false).length;
        const fold = el(`<details class="plugin-fold"><summary class="account-row"><div class="card-icon">EN</div><div><strong>Engine detection</strong><small>${esc(fmt.n(mine.length, 'detector'))} · ${ready} ready</small></div><div class="row-status"><span class="btn small ghost">Show</span></div></summary></details>`);
        mine.forEach((p) => fold.appendChild(capabilityRow(k, p, redraw)));
        list.appendChild(fold);
        return;
      }
      list.appendChild(el(`<h4 class="plugin-kind">${esc(PLUGIN_KIND_NAMES[k])}</h4>`));
      mine.forEach((p) => list.appendChild(capabilityRow(k, p, redraw)));
    });
  }
  if (!plugins.length) st.appendChild(stateBlock({ title: 'No plugins found', body: 'Built-in plugins ship with Pythia; drop-in folders and Python packages add more.' }));
  else if (!list.children.length) st.appendChild(stateBlock({ title: 'No plugins match' }));
}

// ---- Look, Review, Add (UX review A16) -------------------------------------------------------
// One renderer for both shells: proposed games from a Look, each kept or not, new or linked to a game already
// here, with its folders' kind and language and a store link; Add writes only what is kept.
function lookEdit(p) {
  if (!model.lookEdits) model.lookEdits = {};
  if (!model.lookEdits[p.id]) model.lookEdits[p.id] = { keep: true, link_to: p.match ? p.match.game_id : null, store_link: '', folders: Object.fromEntries(p.folders.map((f) => [f.path, { kind: f.kind, language: f.language || '' }])) };
  return model.lookEdits[p.id];
}
function choiceHtml(attrs, label, options, value) {
  if (state.shell === 'handheld') return cycleRow({ label, options, value }).replace('<button ', `<button ${attrs} `);
  return `<select class="field small-select" ${attrs} aria-label="${esc(label)}">${options.map((o) => `<option value="${esc(o.value)}" ${String(o.value) === String(value ?? '') ? 'selected' : ''}>${esc(o.label)}</option>`).join('')}</select>`;
}
const kindOptions = () => (model.vocab?.kinds || Object.keys(KIND_LABELS)).map((k) => ({ value: k, label: KIND_LABELS[k] || k }));
const languageOptions = () => [{ value: '', label: 'No language' }, ...Object.entries(model.vocab?.languages || {}).sort((a, b) => a[1].localeCompare(b[1])).map(([v, l]) => ({ value: v, label: l }))];
function lookCounts(p) {
  const bits = [];
  if (p.versions.length) bits.push(fmt.n(p.versions.length, 'version'));
  if (p.parts.length) bits.push(fmt.n(p.parts.length, 'part'));
  if (p.translations) bits.push(fmt.n(p.translations, 'translation'));
  return bits.join(' · ') || fmt.n(p.folders.length, 'folder');
}
function renderLook(host = $('#lookHost'), summaryHost = $('#lookSummary')) {
  if (!host) return;
  const r = model.look || { status: 'none' };
  if (summaryHost) summaryHost.textContent = r.status === 'ok' ? `${r.summary}. Nothing is added until you Add.` : '';
  host.innerHTML = '';
  if (r.status === 'none') { host.appendChild(stateBlock({ title: 'Nothing to review', body: 'Look through a folder to see the games in it before anything is added.', actions: [{ label: 'Look in a folder', primary: true, run: scanLibrary }] })); return; }
  if (r.status === 'looking') { host.appendChild(stateBlock({ kind: 'loading', title: `Looking in ${fileName(r.root)}`, body: `${fmt.n(r.counts?.folders || 0, 'folder')} looked at, ${fmt.n(r.counts?.games || 0, 'game')} so far.` })); return; }
  if (r.status === 'error') { host.appendChild(stateBlock({ kind: 'error', title: 'Could not look there', body: r.message || '' })); return; }
  if (r.status === 'added') { host.appendChild(stateBlock({ glyph: '✓', title: r.summary || 'Added', body: (r.failures || []).map((f) => `${f.name}: ${f.message}`).join('\n') || 'They are in the library now.', actions: [{ label: 'Show the library', primary: true, run: () => (state.shell === 'handheld' ? navigateHandheld('library') : navigate('library')) }] })); return; }
  const proposals = r.proposals || [];
  const kept = proposals.filter((p) => lookEdit(p).keep).length;
  const bar = el(`<div class="look-bar"><span>${fmt.n(kept, 'game')} of ${proposals.length} kept</span><span class="spacer"></span>${choiceHtml('data-bulk-kind', 'Set kind for the kept games', [{ value: '', label: 'Set kind…' }, ...kindOptions()], '')}${choiceHtml('data-bulk-language', 'Set language for the kept games', [{ value: '-', label: 'Set language…' }, ...languageOptions()], '-')}<button class="btn" type="button" data-look-discard>Discard</button><button class="btn primary" type="button" data-look-add ${kept ? '' : 'disabled'}>Add ${fmt.n(kept, 'game')}</button></div>`);
  host.appendChild(bar);
  for (const p of proposals) {
    const e = lookEdit(p);
    const matchOptions = [{ value: '', label: 'A new game' }, ...(p.match ? [{ value: p.match.game_id, label: `Link to ${p.match.name}` }] : [])];
    const row = el(`<article class="look-row ${e.keep ? '' : 'left-out'}" data-look="${esc(p.id)}">
      <div class="look-head"><label class="look-keep"><input type="checkbox" data-look-keep ${e.keep ? 'checked' : ''}> <strong>${esc(p.name)}</strong></label><span class="tag">${esc(kindName(p.engine) || ENGINE_KINDS[p.engine] || p.engine || 'Not identified')}</span><span class="tag">${esc(lookCounts(p))}</span><span class="spacer"></span>${choiceHtml('data-look-match', 'Add as', matchOptions, e.link_to || '')}</div>
      ${p.match ? `<p class="look-reason">${esc(p.match.reason)}.</p>` : ''}
      <div class="look-folders">${p.folders.map((f) => `<div class="look-folder" data-look-folder="${esc(f.path)}"><div class="look-folder-main"><span class="mono" aria-label="${esc(f.path)}">${esc(PythiaVersions.middleEllipsis(f.path, 80))}</span><small>${esc([f.part, f.version && `version ${f.version}`, ...(f.mods || [])].filter(Boolean).join(' · ') || 'No version number')}</small></div>${choiceHtml('data-folder-kind', 'Kind', kindOptions(), e.folders[f.path].kind)}${choiceHtml('data-folder-language', 'Language', languageOptions(), e.folders[f.path].language)}</div>`).join('')}</div>
      <div class="look-link"><label>Store link <input class="field" data-look-link placeholder="https://f95zone.to/threads/…" value="${esc(e.store_link)}"></label></div>
    </article>`);
    host.appendChild(row);
  }
  for (const s of (r.skipped || []).concat(r.errors || [])) host.appendChild(el(`<p class="look-reason">${esc(fileName(s.path))}: ${esc(s.message)}</p>`));
  const proposalOf = (node) => proposals.find((p) => p.id === node.closest('[data-look]').dataset.look);
  $$('[data-look-keep]', host).forEach((cb) => (cb.onchange = () => { lookEdit(proposalOf(cb)).keep = cb.checked; renderLook(host, summaryHost); }));
  $$('[data-look-match]', host).forEach((c) => c.addEventListener('change', () => { lookEdit(proposalOf(c)).link_to = c.value || null; }));
  $$('[data-look-link]', host).forEach((i) => (i.oninput = () => { lookEdit(proposalOf(i)).store_link = i.value; }));
  $$('[data-folder-kind], [data-folder-language]', host).forEach((c) => c.addEventListener('change', () => { const f = lookEdit(proposalOf(c)).folders[c.closest('[data-look-folder]').dataset.lookFolder]; if (c.matches('[data-folder-kind]')) f.kind = c.value; else f.language = c.value; }));
  const bulk = (attr, key) => { const c = $(attr, host); c.addEventListener('change', () => { if (c.value === '' || c.value === '-') return; for (const p of proposals) { const ed = lookEdit(p); if (ed.keep) for (const path of Object.keys(ed.folders)) ed.folders[path][key] = c.value; } renderLook(host, summaryHost); }); };
  bulk('[data-bulk-kind]', 'kind'); bulk('[data-bulk-language]', 'language');
  $('[data-look-discard]', host).onclick = () => { pythia.discard_look(); model.lookEdits = {}; };
  $('[data-look-add]', host).onclick = async () => {
    const rows = proposals.map((p) => { const ed = lookEdit(p); return { id: p.id, name: p.name, keep: ed.keep, link_to: ed.link_to, store_link: ed.store_link, folders: p.folders.map((f) => ({ path: f.path, version: f.version, kind: ed.folders[f.path].kind, language: ed.folders[f.path].language })) }; });
    const r2 = await act('add_from_look', [rows], null, { what: 'Adding' }); if (ok(r2)) { model.look = { status: 'adding' }; host.innerHTML = ''; host.appendChild(stateBlock({ kind: 'loading', title: 'Adding' })); }
  };
  if (state.shell === 'handheld') hhKeepDisabledReachable();
}

// ---- Actions shared by pages ------------------------------------------------------------
const pickFolder = (title) => (done) => call('pick_folder', title).then((v) => done(typeof v === 'string' ? v : ''));
// Example paths in this OS's shape (no C:\ on Linux), and the dialog's check before anything starts
// (backend check_path: game, folder, writable or program).
const onWindows = () => /^win/i.test((model.app && model.app.os) || navigator.platform || '');
const pathHint = (win, other) => (onWindows() ? win : other);
// External hand-offs (UX review A3, conflict C4): anything that leaves Pythia (a folder, a launcher,
// a web page). In the handheld shell each asks first, Cancel focused, since it leaves a pad-driven
// screen for a mouse one. On desktop only "Open in <launcher>" asks, the first time per launcher.
async function handOff({ kind = 'folder', title, body = '', launcher = '', run }) {
  if (state.shell === 'handheld') {
    if (!(await confirm({ title, body: `${body ? `${body} ` : ''}It opens outside Pythia's handheld screen.`, proceed: 'Open' }))) return;
  } else if (kind === 'launcher') {
    let asked = [];
    try { asked = JSON.parse(prefs.get('launchers-asked', '[]')) || []; } catch (e) { asked = []; }
    if (!asked.includes(launcher)) {
      if (!(await confirm({ title, body, proceed: 'Open' }))) return;
      prefs.set('launchers-asked', JSON.stringify([...asked, launcher]));
    }
  }
  run();
}
const openFolder = (path, what = 'Open folder') => handOff({ title: 'Open the folder?', body: path ? `Your file manager opens ${path}.` : '', run: () => act('open_path', [path || ''], null, { what }) });
const openInManager = (g, m) => handOff({ kind: 'launcher', launcher: m.manager_id, title: `Open in ${m.manager_name}?`, body: `${m.manager_name} opens and shows ${g.name}. Pythia does not run the game itself.`, run: () => { pythia.open_in_manager(g.id, m.manager_id); flash(`Handed to ${m.manager_name}`); } });
const checkPath = (want) => async (value) => { const r = await call('check_path', value, want); if (!ok(r)) throw new Error(r.message || 'no answer'); return r.reason; };
async function addFolder() {
  const path = await prompt({ title: 'Add a game folder', body: 'Pythia records the folder as it is; nothing is moved.', hint: pathHint('G:\\games\\Example', '~/Games/Example'), kind: 'path', check: checkPath('game'), save: 'Add', pick: { label: 'Browse', run: pickFolder('Add a game folder') } });
  if (!path || !path.trim()) return;
  pythia.start_onboard(path.trim()); flash('Adding folder'); navigate('activity');
}
// The games the stores on this computer have installed, from droidtop's scanner (backend start_store_scan).
async function findStoreGames() {
  const r = await call('start_store_scan');
  if (ok(r)) { flash('Finding store games'); navigate('activity'); } else flash(r.status === 'unavailable' ? r.message : `Could not ask the stores: ${r.message}`, 'caution');
}
async function scanLibrary() {
  const path = await prompt({ title: 'Scan a library folder', body: 'Pythia looks through it and shows what it found. Nothing is added until you Add, and nothing is ever written into your game folders.', hint: pathHint('G:\\games', '~/Games'), kind: 'path', check: checkPath('folder'), save: 'Look', pick: { label: 'Browse', run: pickFolder('Scan a library folder') } });
  if (!path || !path.trim()) return;
  // Look first, then Review, then Add (UX review A16): nothing is written until Add.
  const r = await act('start_look', [path.trim()], null, { what: 'Looking' }); if (!ok(r)) return;
  model.lookEdits = {}; if (state.shell === 'handheld') navigateHandheld('look'); else navigate('look');
}
async function importSource() {
  const connected = model.accounts.filter((a) => a.connected);
  if (!connected.length) { infoSheet({ title: 'No connected source', details: `Connect a game source first (Settings > Sources > Account). Sources with accounts: ${model.accounts.map((a) => a.display_name).join(', ') || 'none'}.` }); return; }
  const id = await prompt({ title: 'Import from source', kind: 'select', options: connected.map((a) => ({ value: a.id, label: a.display_name })), value: connected[0].id, save: 'Import' });
  if (id) { pythia.sync_account(id); flash('Importing'); navigate('activity'); }
}
async function watchFolder(pluginId = '') {
  const folder = await prompt({ title: 'Watch a download folder', hint: pathHint('C:\\Users\\you\\Downloads', '~/Downloads'), kind: 'path', check: checkPath('folder'), save: 'Next', pick: { label: 'Browse', run: pickFolder('Folder to watch') } });
  if (!folder || !folder.trim()) return;
  const program = await prompt({ title: 'Program to run while watching', body: 'Leave empty to only watch the folder.', hint: pathHint('C:\\Tools\\F95Checker.exe', '~/Applications/F95Checker'), kind: 'path', check: checkPath('program'), save: 'Watch', pick: { label: 'Browse', run: (done) => call('pick_file', 'Program to run').then((v) => done(typeof v === 'string' ? v : '')) } });
  if (program === null) return;
  if (pluginId) pythia.start_watch_for(pluginId, program.trim(), folder.trim()); else pythia.start_watch(program.trim(), folder.trim());
  flash('Watching'); navigate('downloads');
}
async function exportDiagnostics() {
  const dir = await prompt({ title: 'Export diagnostics', body: 'The log file plus app, system and plugin facts. No credentials.', hint: pathHint('C:\\Users\\you\\Desktop', '~/Desktop'), kind: 'path', check: checkPath('writable'), save: 'Export', pick: { label: 'Browse', run: pickFolder('Folder for the diagnostics') } });
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
  if (a === 'tools') menuAction('tools');
  if (a === 'backup-now') {
    flash('Backing up saves…');
    const r = await call('back_up_saves_now');
    if (!ok(r)) { infoSheet({ title: 'Back up saves', node: fromResult(r, { what: 'Backing up now' }) }); return; }
    const parts = [`${fmt.n(r.backed_up, 'game')} backed up (${fmt.n(r.files, 'file')})`];
    if (r.no_saves) parts.push(`${fmt.n(r.no_saves, 'game')} with no saves here yet`);
    if (r.unknown) parts.push(`${fmt.n(r.unknown, 'game')} with no known save location`);
    if ((r.failures || []).length) parts.push(`${fmt.n(r.failures.length, 'failure')}: ${r.failures.map((f) => `${f.title} (${f.message})`).join('; ')}`);
    flash(parts.join(' · '), (r.failures || []).length ? 'caution' : 'info');
    if (state.view === 'saves') renderSaves();
  }
  if (a === 'pair-handheld') pairHandheld();
  if (a === 'show-pair-code') showPairCode();
  if (a === 'pair-device') pairDevice();
  if (a === 'clear-jobs') act('clear_finished_jobs', [], 'Cleared', { what: 'Clearing' });
  if (a === 'export-diagnostics') exportDiagnostics();
  if (a === 'install-plugin') infoSheet({ title: 'Install a plugin', details: `Drop a plugin folder (with its manifest) into the plugins folder under ${model.info?.app_data_folder || 'the app data folder'}, or pip-install a package that declares a pythia.plugins entry point, then restart Pythia. Plugins from outside Pythia are marked Unofficial.` });
  if (a === 'reset-identity') { if (await confirmDanger({ title: 'Reset this computer\u2019s identity?', body: 'Every record this computer wrote would belong to a computer that no longer exists.', recommendation: 'We recommend Cancel.', proceed: 'Reset' })) { const r = await call('reset_identity'); flash(ok(r) ? 'Identity reset' : r.message, ok(r) ? 'info' : 'caution'); } }
}

// ---- Handheld shell -----------------------------------------------------------------------
// Start in (UX review A8): Pythia keeps it (backend get_start), not the page's storage. ?ui= wins for a run;
// a choice an earlier page kept in its own storage moves to Pythia once.
function explicitShell() { const ui = new URLSearchParams(location.search).get('ui'); return ui === 'handheld' || ui === 'desktop' ? ui : null; }
async function startShell() {
  let moved = null;
  try {
    const old = localStorage.getItem('pythia-startup-shell'); const last = localStorage.getItem('pythia-last-shell');
    if (old) { moved = old; localStorage.removeItem('pythia-startup-shell'); }
    if (last) { localStorage.removeItem('pythia-last-shell'); await call('remember_shell', last); }
  } catch (e) { /* storage may be unavailable */ }
  if (moved && moved !== 'auto') await call('set_start_in', moved);
  const r = await call('get_start'); if (!ok(r)) return;
  model.start = r; renderStartIn();
  if (!explicitShell() && r.shell === 'handheld' && state.shell !== 'handheld') enterHandheld('home');
}
const START_IN_OPTIONS = [{ value: 'auto', label: 'Auto-detect' }, { value: 'desktop', label: 'Desktop' }, { value: 'handheld', label: 'Handheld' }, { value: 'last', label: 'Remember last' }];
function startInNote() { const d = model.start?.device; return d ? `Auto-detect: ${d.device || 'this computer'} ${d.handheld ? 'is a handheld, so Handheld' : 'is not a known handheld, so Desktop'}.` : ''; }
function renderStartIn() { const sel = $('#startupShellSelect'); if (sel && model.start) sel.value = model.start.start_in; const note = $('#startInNote'); if (note) note.textContent = startInNote(); }
async function setStartIn(value) { const r = await act('set_start_in', [value], 'Saved', { what: 'Start in' }); if (ok(r)) { model.start = { ...model.start, start_in: value }; renderStartIn(); } }
// The remembered shell (Settings > Interface > Remember last shell) changes only on a deliberate switch (UX review A4).
function rememberShell(shell) { call('remember_shell', shell); }
// The handheld shell's size from the window's short side (UX review A6): 800 px is 1, 1080 px about
// 1.35, at most 1.6. Text size multiplies on top (tokens.css). The desktop stays at 1.
function applyHandheldScale() {
  const short = Math.min(window.innerWidth, window.innerHeight);
  const scale = state.shell === 'handheld' ? Math.min(1.6, Math.max(1, short / 800)) : 1;
  document.documentElement.style.setProperty('--hh-scale', scale.toFixed(3));
}
window.addEventListener('resize', applyHandheldScale);
function enterHandheld(page = 'home', { remember = false } = {}) { state.shell = 'handheld'; state.hhPage = page; state.hhHistory = []; if (remember) rememberShell('handheld'); applyHandheldScale(); document.body.classList.add('handheld-active'); $('#handheldShell').classList.remove('hidden'); renderHandheld(); setTimeout(() => hhEnterZone('content') || hhEnterZone('rail'), 0); }
function exitHandheld({ remember = false } = {}) { state.shell = 'desktop'; if (remember) rememberShell('desktop'); applyHandheldScale(); document.body.classList.remove('handheld-active'); $('#handheldShell').classList.add('hidden'); setTimeout(() => $('#enterHandheldButton')?.focus(), 0); }
// Leaving the handheld shell is only ever deliberate (the rail's Desktop, the top bar's, Menu > Desktop,
// or a page only the desktop has), and asks first with Cancel focused. Back never leaves it.
async function leaveHandheld({ body = 'The desktop layout needs a mouse or touch; the pad does not drive it.', then = null, remember = true } = {}) {
  if (!(await confirm({ title: 'Leave handheld mode?', body, proceed: 'Switch to desktop' }))) return;
  exitHandheld({ remember });
  if (then) then();
}
const HH_RAIL_PAGES = ['home', 'library', 'attention', 'downloads', 'systems', 'activity', 'settings'];
// A subpage's parent when it was reached without history (a deep link, a refresh).
const HH_PARENTS = { look: 'home', detail: 'library', decision: 'attention', plan: 'attention', saves: 'home', launcher: 'home' };
function navigateHandheld(page, push = true, reviewSection = '') { state.hhReviewSection = page === 'attention' ? reviewSection : ''; if (page === state.hhPage) { renderHandheld(); return; } if (HH_RAIL_PAGES.includes(page)) state.hhHistory = []; else if (push) state.hhHistory.push(state.hhPage); state.hhPage = page; state.hhSelected = null; if (page !== 'settings') state.hhSettingsPage = 'root'; renderHandheld(); }
const hhOpenReview = (section) => navigateHandheld('attention', true, section);
function handheldTitle() {
  if (state.hhPage === 'settings' && state.hhSettingsPage !== 'root') {
    const labels = { general: 'General', library: 'Library', capabilities: 'Plugin capabilities', plugins: 'Installed plugins', sync: 'Systems & sync', interface: 'Interface', advanced: 'Advanced', sources: 'Sources', downloaders: 'Downloaders', metadata: 'Metadata', managers: 'Managers', engines: 'Engine detection', mods: 'Mods', patchers: 'Patchers' };
    return [labels[state.hhSettingsPage] || 'Settings', 'Settings'];
  }
  const running = model.jobs.filter((j) => j.state === 'running').length;
  const { watcher } = model.intake; const review = model.ready ? reviewQueue().affected : 0;
  const reviewTitles = { decisions: 'Scan decisions', duplicates: 'Possible duplicates', unidentified: 'Not identified', updates: 'Updates seen', downloads: 'New downloads' };
  return { home: ['Home', model.ready ? `${fmt.n(review, 'game')} to review · ${fmt.n(running, 'job')} running` : 'Connecting'], library: ['Library', `${games.length} games`], attention: [reviewTitles[state.hhReviewSection] || 'Review', state.hhReviewSection ? 'Review' : `${fmt.n(review, 'game')} affected`], downloads: ['Downloads', watcher ? `Watching ${fileName(watcher.folder)}` : 'Not watching'], systems: ['Systems', `${fmt.n(systems.length, 'system')}`], settings: ['Settings', 'Library, plugins, systems, and interface'], look: ['Review what was found', model.look?.status === 'ok' ? model.look.summary : 'Nothing is added until you Add'], activity: ['Activity & jobs', `${fmt.n(running, 'job')} running`], saves: ['Save backups', 'Copies of saves kept on this computer'], launcher: ['Launcher entries', "What this computer's launchers list"], detail: ['Game details', 'Versions, systems, and actions'], decision: ['Review', 'No changes applied'], plan: ['Available update', 'Review before applying'] }[state.hhPage] || ['Pythia', ''];
}
function renderHandheld() {
  const [title, sub] = handheldTitle(); $('#hhTitle').textContent = title; $('#hhSubtitle').textContent = sub;
  const hhBackButton = $('#hhBack'); hhBackButton.disabled = false; hhBackButton.removeAttribute('aria-disabled'); hhBackButton.classList.remove('is-disabled');
  hhBackButton.style.visibility = state.hhPage === 'home' ? 'hidden' : '';
  $$('[data-hh-nav]').forEach((b) => b.classList.toggle('active', b.dataset.hhNav === state.hhPage));
  const running = model.jobs.filter((j) => j.state === 'running').length;
  $('#hhFooterRight').textContent = model.ready ? `${fmt.n(games.length, 'game')} · ${fmt.n(running, 'job')} running` : 'Connecting';
  const q = state.hhSearch.toLowerCase();
  const page = state.hhPage;
  if (page === 'home') renderHHHome(); else if (page === 'library') renderHHLibrary(q); else if (page === 'attention') renderHHAttention(q); else if (page === 'downloads') renderHHDownloads(); else if (page === 'systems') renderHHSystems(); else if (page === 'settings') renderHHSettings(); else if (page === 'detail') renderHHDetail(games.find((g) => g.id === state.hhSelected)); else if (page === 'decision') renderHHDecision(games.find((g) => g.id === state.hhSelected)); else if (page === 'plan') renderHHPlan(games.find((g) => g.id === state.hhSelected)); else if (page === 'activity') renderHHActivity(); else if (page === 'saves') renderHHSaves(); else if (page === 'launcher') renderHHLauncher(); else if (page === 'look') { const h = $('#hhContent'); h.innerHTML = '<div class="hh-stack"><p id="hhLookSummary"></p><div id="hhLookHost"></div></div>'; renderLook($('#hhLookHost'), $('#hhLookSummary')); }
  bindHandheldActions();
  hhKeepDisabledReachable();
  hhKeepFocus();
}
function hhSummaryGame(g) { return `<button class="hh-summary" data-hh-game="${g.id}"><span class="art art-${g.art}">${esc(initials(g.name))}</span><span><strong>${esc(g.name)}</strong><small>${esc(g.engine)} · ${esc(versionText(g))}${versionsBadge(g)} · ${esc(g.systems.join(' / '))}</small></span>${gameStatus(g)}</button>`; }
function hhJob(j) { return `<div class="hh-summary" style="cursor:default"><span class="art" style="background:${j.state === 'failed' ? 'var(--critical-soft-token)' : 'var(--informational-soft)'};color:${j.state === 'failed' ? 'var(--critical-token)' : 'var(--informational)'}">${j.state === 'running' ? '↻' : j.state === 'done' ? '✓' : '!'}</span><span><strong>${esc(j.title)}</strong><small>${esc(j.state === 'running' ? (j.log[j.log.length - 1] || j.detail || 'Running') : (j.summary || ''))}</small></span>${j.state === 'running' ? visibleStatus('Running', 'pending', '↻') : j.state === 'done' ? routineStatus('Done') : visibleStatus('Failed', 'critical')}</div>`; }
function renderHHHome() {
  const host = $('#hhContent'); host.innerHTML = '';
  if (!model.ready) { host.appendChild(stateBlock({ kind: 'loading' })); return; }
  const review = reviewQueue(); const jobs = model.jobs.slice(0, 4);
  const failed = model.jobs.filter((j) => j.state === 'failed').length;
  const recent = [...games].filter((g) => g.added).sort((a, b) => (b.added > a.added ? 1 : -1)).slice(0, 3);
  host.innerHTML = `<div class="hh-stack">
    ${games.length || review.affected ? `<section class="hh-panel"><div class="hh-panel-head"><h2>Review</h2><span class="spacer"></span><button class="hh-action" data-hh-nav-jump="attention">${review.affected ? `All ${review.affected}` : 'Open'}</button></div><div class="hh-panel-body hh-summary-list" id="hhAttention"></div></section>` : ''}
    <section class="hh-panel"><div class="hh-panel-head"><h2>Active work</h2><span class="spacer"></span><small>${fmt.n(model.jobs.filter((j) => j.state === 'running').length, 'job')} running</small></div><div class="hh-panel-body hh-summary-list" id="hhJobs"></div></section>
    <section class="hh-panel"><div class="hh-panel-head"><h2>${games.length ? 'Recently added' : 'Your library'}</h2><span class="spacer"></span><button class="hh-action" data-hh-nav-jump="library">Library</button></div><div class="hh-panel-body hh-summary-list" id="hhRecent"></div></section>
    <section class="hh-panel"><div class="hh-panel-head"><h2>More</h2></div><div class="hh-panel-body hh-toolbar"><button class="hh-action" data-hh-nav-jump="activity">Activity &amp; jobs</button><button class="hh-action" data-hh-nav-jump="saves">Save backups</button><button class="hh-action" data-hh-nav-jump="launcher">Launcher entries</button></div></section>
    ${failed ? `<section class="hh-alert-strip" style="border-color:color-mix(in srgb,var(--warning) 45%,var(--border));background:var(--warning-soft)"><span class="status-icon pending" aria-hidden="true">!</span><div><strong>${fmt.n(failed, 'job')} failed</strong><small>${esc(model.jobs.find((j) => j.state === 'failed').summary)}</small></div><button class="hh-action" style="margin-left:auto" data-hh-jobs>Details</button></section>` : ''}</div>`;
  const a = $('#hhAttention', host); if (a) { a.innerHTML = hhReviewLines(review).slice(0, 4).join(''); if (!review.affected) a.appendChild(stateBlock({ glyph: '✓', title: 'Nothing to review' })); }
  const j = $('#hhJobs', host); j.innerHTML = jobs.map(hhJob).join(''); if (!jobs.length) j.appendChild(stateBlock({ title: 'Nothing has run yet', body: 'Scans and adds show here.', actions: [{ label: 'Scan library', run: scanLibrary }] }));
  const r = $('#hhRecent', host); r.innerHTML = recent.map(hhSummaryGame).join(''); if (!games.length) r.appendChild(stateBlock({ glyph: '▦', title: 'No games yet', body: 'Add a folder or scan a library.', actions: [{ label: 'Add folder', primary: true, run: addFolder }] }));
}
function handheldGames(q = '') { const query = PythiaQuery.parse(q); return games.filter((g) => PythiaQuery.matches(g, query) || (!query.filters.length && PythiaQuery.norm(g.statusText).includes(PythiaQuery.norm(q)))); }
function renderHHLibrary(q = '') {
  const sources = [...new Set(games.map((g) => g.source))].sort();
  const list = handheldGames(q).filter((g) => state.hhSource === 'all' || g.source === state.hhSource).sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base', numeric: true }));
  const host = $('#hhContent');
  host.innerHTML = `<div class="hh-toolbar"><div class="hh-seg"><button data-hh-library-mode="list" class="${state.hhLibraryMode === 'list' ? 'active' : ''}">☰ List</button><button data-hh-library-mode="grid" class="${state.hhLibraryMode === 'grid' ? 'active' : ''}">▦ Grid</button></div>${cycleRow({ id: 'hhSourceFilter', label: 'Source', options: [{ value: 'all', label: 'All sources' }, ...sources.map((s) => ({ value: s, label: s }))], value: state.hhSource })}<button class="hh-action" data-hh-nav-jump="attention">Review</button><span class="spacer"></span><small>${list.length} of ${games.length}</small></div>${state.hhLibraryMode === 'grid' ? `<div class="hh-library-grid">${list.map((g) => `<button class="hh-game-card" data-hh-game="${g.id}"><div class="hh-card-art art-${g.art}"><strong>${esc(g.name)}</strong></div><div class="hh-card-copy"><small>${esc(g.engine)} · ${esc(versionText(g))}${versionsBadge(g)}</small>${gameStatus(g)}</div></button>`).join('')}</div>` : `<div class="hh-library-list">${list.map((g) => `<button class="hh-game-row" data-hh-game="${g.id}"><span class="art art-${g.art}">${esc(initials(g.name))}</span><span><strong>${esc(g.name)}</strong><small>${esc(g.source)} · ${esc(g.engine)} · ${esc(versionText(g))}${versionsBadge(g)}</small></span><span class="system">${esc(g.systems.join(' · '))}</span><span class="state">${gameStatus(g)}</span></button>`).join('')}</div>`}`;
  if (!model.ready) host.appendChild(stateBlock({ kind: 'loading' }));
  else if (!games.length) host.appendChild(stateBlock({ glyph: '▦', title: 'No games yet', body: 'Add a folder or scan a library.', actions: [{ label: 'Add folder', primary: true, run: addFolder }, { label: 'Scan library', run: scanLibrary }] }));
  else if (!list.length) host.appendChild(stateBlock({ title: 'Nothing matches', actions: [{ label: 'Clear', run: () => { state.hhSource = 'all'; state.hhSearch = ''; $('#hhSearch').value = ''; renderHandheld(); } }] }));
}
// The handheld's Review (UX review A18): the same queue as the desktop's, one line per section, each opening its items.
function hhReviewLines(review) {
  const line = (key, n, title, sub, symbol, kind = 'pending') => (n ? `<button class="hh-issue ${kind}" data-hh-review-open="${key}"><span class="symbol">${symbol}</span><span><strong>${esc(title)}</strong><small>${esc(sub)}</small></span><span class="go">›</span></button>` : '');
  return [
    line('decisions', review.decisions.length, fmt.n(review.decisions.length, 'scan decision'), 'Folders that may be a game you already have', '?'),
    line('duplicates', review.clusters.length, fmt.n(review.clusters.length, 'possible duplicate'), review.clusters.slice(0, 3).map((c) => c.name).join(', '), '⇄'),
    line('unidentified', review.unidentified.length, `${review.unidentified.length} not identified`, review.unidentified.slice(0, 3).map((g) => g.name).join(', '), '?', 'neutral'),
    line('updates', review.updates.length, fmt.n(review.updates.length, 'update') + ' seen', review.updates.slice(0, 3).map((g) => g.name).join(', '), '↻'),
    line('downloads', review.downloads.length, fmt.n(review.downloads.length, 'new download'), review.downloads.slice(0, 3).map((d) => fileName(d.path)).join(', '), '⇩'),
  ].filter(Boolean);
}
function renderHHAttention(q = '') {
  const host = $('#hhContent');
  if (!model.ready) { host.replaceChildren(stateBlock({ kind: 'loading' })); return; }
  const review = reviewQueue(); const section = state.hhReviewSection;
  const needle = PythiaQuery.norm(q);
  const games_ = (list) => list.filter((g) => !needle || PythiaQuery.norm(`${g.name} ${g.path}`).includes(needle));
  if (!section) {
    host.innerHTML = `<div class="hh-toolbar"><span>${fmt.n(review.affected, 'game')} affected</span><span class="spacer"></span><button class="hh-action" data-hh-nav-jump="library">Library</button></div><div class="hh-issue-list">${hhReviewLines(review).join('')}</div>`;
    if (!review.affected) host.appendChild(stateBlock({ glyph: '✓', title: 'Nothing to review', body: 'Scan decisions, possible duplicates, games not identified, updates seen and new downloads land here.' }));
    if (model.merges === null || model.merges === undefined) host.appendChild(el('<p class="look-reason">Still looking for possible duplicates.</p>'));
    return;
  }
  const back = '<button class="hh-action" data-hh-review-open="">‹ Review</button>';
  if (section === 'duplicates') { host.innerHTML = `<div class="hh-toolbar">${back}</div><div id="hhReviewBody"></div>`; renderHHDuplicates($('#hhReviewBody', host)); return; }
  if (section === 'downloads') {
    host.innerHTML = `<div class="hh-stack"><div class="hh-toolbar">${back}</div><div class="hh-summary-list">${review.downloads.map((d) => `<button class="hh-summary" data-hh-onboard="${esc(d.path)}"><span class="art art-d">⇩</span><span><strong>${esc(fileName(d.path))}</strong><small>Watched folder · ${esc(fmt.time(d.at))}</small></span>${visibleStatus('Add', 'pending', '＋')}</button>`).join('')}</div></div>`;
    if (!review.downloads.length) host.appendChild(stateBlock({ glyph: '✓', title: 'No new downloads' }));
    return;
  }
  if (section === 'decisions') {
    host.innerHTML = `<div class="hh-stack"><div class="hh-toolbar">${back}</div><div class="hh-summary-list">${review.decisions.map((d) => `<button class="hh-summary" data-hh-review><span class="art art-a">${esc(initials(d.name || fileName(d.path)))}</span><span><strong>${esc(fileName(d.path))}</strong><small>${(d.candidates?.suggested || []).length ? `Similar to ${esc(d.candidates.suggested[0].canonical_name)}` : 'New game'}</small></span>${visibleStatus('Decide', 'pending')}</button>`).join('')}</div></div>`;
    if (!review.decisions.length) host.appendChild(stateBlock({ glyph: '✓', title: 'No scan decisions waiting' }));
    return;
  }
  const list = games_(section === 'updates' ? review.updates : review.unidentified);
  host.innerHTML = `<div class="hh-toolbar">${back}<span class="spacer"></span><span>${list.length} items</span></div><div class="hh-issue-list">${list.map((g) => `<button class="hh-issue ${section === 'updates' ? 'pending' : 'neutral'}" data-hh-decision="${g.id}"><span class="symbol">${section === 'updates' ? '↻' : '?'}</span><span><strong>${esc(g.name)}</strong><small>${esc(section === 'updates' ? `${g.updates.map((u) => u.version || 'Newer version').join(', ')} seen · installed ${versionText(g)}` : `Not identified · ${g.source}`)}</small></span><span class="go">›</span></button>`).join('')}</div>`;
  if (!list.length) host.appendChild(stateBlock({ glyph: '✓', title: section === 'updates' ? 'No updates seen' : 'Every game is identified' }));
}
function renderHHDownloads() {
  const { watcher } = model.intake; const host = $('#hhContent');
  if (managerPaused('downloads')) { host.replaceChildren(managerOffBlock('Watching download folders and tagging what arrives')); return; }
  host.innerHTML = `<div class="hh-stack"><section class="hh-panel"><div class="hh-panel-head"><h2>Watcher</h2><span class="spacer"></span><button class="hh-action primary" data-hh-add>＋ Add folder</button><button class="hh-action" data-hh-watch>${watcher ? 'Stop' : 'Watch a folder'}</button></div><div class="hh-panel-body hh-summary-list"><div class="hh-summary" style="cursor:default"><span class="art" style="background:${watcher ? 'var(--positive-soft)' : 'var(--surface-2)'};color:${watcher ? 'var(--positive)' : 'var(--text-secondary)'}">⇩</span><span><strong>${esc(watcher ? watcher.folder : 'Not watching')}</strong><small>${esc(watcher ? (watcher.program ? `Running ${fileName(watcher.program)}` : 'Folder only') : 'New files in a watched folder wait in Review')}</small></span>${watcher ? routineStatus('Watching') : visibleStatus('Idle', 'neutral', '–')}</div></div></section></div>`;
}
function renderHHSystems() {
  const host = $('#hhContent');
  host.innerHTML = `<div class="hh-system-grid">${systems.map((s) => `<article class="hh-system"><h2>${esc(s.name)}</h2><p>${esc(s.mine && model.app ? model.app.platform : 'Synced record')} · ${fmt.n(s.games, 'game')}</p><div class="line"><span>Installed</span><b>${s.installed}</b></div><div class="line"><span>Record</span><b style="color:var(--positive)">${s.mine ? 'This computer' : 'Synced'}</b></div></article>`).join('')}</div><section class="hh-panel" style="margin-top:10px"><div class="hh-panel-head"><h2>Paired devices</h2><span class="spacer"></span><button class="hh-action primary" data-hh-pair-device>Pair a device</button><button class="hh-action" data-hh-pair>Type a code</button><button class="hh-action" data-hh-pair-show>Show a code</button></div><div class="hh-panel-body"><div id="hhPairShowing"></div><div class="hh-summary-list" id="hhHandhelds"></div></div></section><section class="hh-panel" style="margin-top:10px"><div class="hh-panel-head"><h2>Library folder</h2><span class="spacer"></span><small>Shared by every system</small></div><div class="hh-panel-body"><div class="hh-setting"><strong class="mono" style="font-size:0.75rem;word-break:break-all">${esc(model.info?.sync_folder || '')}</strong><small>Copying versions between devices is not built yet.</small><button class="hh-action" data-open-path="sync_folder">Open folder</button></div></div></section>`;
  if (!systems.length) host.prepend(stateBlock({ kind: model.ready ? 'empty' : 'loading', title: 'No systems yet' }));
  $('[data-hh-pair]', host).onclick = pairHandheld;
  $('[data-hh-pair-device]', host).onclick = () => pairDevice($('#hhPairShowing', host), () => state.shell === 'handheld' && state.hhPage === 'systems', renderHandheld);
  $('[data-hh-pair-show]', host).onclick = () => showPairCode($('#hhPairShowing', host), () => state.shell === 'handheld' && state.hhPage === 'systems', renderHandheld);
  loadInto($('#hhHandhelds', host), 'get_handhelds', [], (r, h) => {
    const devices = r.devices || [];
    if (!devices.length) { h.appendChild(stateBlock({ title: 'No device is paired', body: 'A droidtop handheld: on it open Settings > Computers > Pair a computer, then choose Type a code here and type the code it shows. Another computer running Pythia: choose Pair a device on both.' })); return; }
    devices.forEach((d) => {
      const row = el(`<button class="hh-summary" type="button"><span class="art art-a">${esc(initials(d.name))}</span><span><strong>${esc(d.name)}</strong><small>${d.kind === 'computer' ? 'Computer' : 'Handheld'} · ${d.last_seen_ms ? `Seen ${esc(fmt.dateTime(new Date(d.last_seen_ms).toISOString()))}` : 'Not seen yet'}${d.primary ? ' · its saves win' : ''} · A: what syncs</small></span>${routineStatus('Paired')}</button>`);
      row.onclick = () => showDeviceSyncs(d);
      h.appendChild(row);
    });
  }, { what: 'Handhelds' });
}
function renderHHSettings() {
  const page = state.hhSettingsPage; const host = $('#hhContent');
  const back = `<button class="hh-action" data-hh-settings-page="root">‹ Settings</button>`;
  if (page === 'root') {
    host.innerHTML = `<div class="hh-settings-menu"><button class="hh-settings-link" data-hh-settings-page="general"><span><strong>General</strong><small>Modes and the application data folder</small></span><span class="go">›</span></button><button class="hh-settings-link" data-hh-settings-page="library"><span><strong>Library</strong><small>Tracked folders and scanning</small></span><span class="go">›</span></button><button class="hh-settings-link" data-hh-settings-page="capabilities"><span><strong>Plugin capabilities</strong><small>Sources, downloaders, metadata, managers, engines, mods, and patchers</small></span><span class="go">›</span></button><button class="hh-settings-link" data-hh-settings-page="plugins"><span><strong>Installed plugins</strong><small>${plugins.length} installed</small></span><span class="go">›</span></button><button class="hh-settings-link" data-hh-settings-page="sync"><span><strong>Systems & sync</strong><small>Metadata folder and game transfer</small></span><span class="go">›</span></button><button class="hh-settings-link" data-hh-settings-page="interface"><span><strong>Interface</strong><small>Theme, text size, startup shell, and library view</small></span><span class="go">›</span></button><button class="hh-settings-link" data-hh-settings-page="advanced"><span><strong>Advanced</strong><small>Diagnostics and recovery</small></span><span class="go">›</span></button></div>`;
  } else if (page === 'capabilities') {
    host.innerHTML = `<div class="hh-stack">${back}<div class="hh-settings-menu">${[['sources', 'Sources'], ['downloaders', 'Downloaders'], ['metadata', 'Metadata'], ['managers', 'Managers'], ['engines', 'Engine detection'], ['mods', 'Mods'], ['patchers', 'Patchers']].map((x) => `<button class="hh-settings-link" data-hh-settings-page="${x[0]}"><span><strong>${x[1]}</strong></span><span class="go">›</span></button>`).join('')}</div></div>`;
  } else if (['sources', 'downloaders', 'metadata', 'managers', 'engines', 'mods', 'patchers'].includes(page)) {
    const cat = { sources: 'library', downloaders: 'downloader', metadata: 'metadata', managers: 'manager', engines: 'engine', mods: 'mod', patchers: 'patcher' }[page];
    host.innerHTML = `<div class="hh-stack">${back}<div class="hh-capability-list" id="hhCapabilities"></div></div>`;
    loadInto($('#hhCapabilities', host), 'get_capabilities', [cat], (r, h) => {
      (r.plugins || []).forEach((p) => { const s = p.state || {}; const status = !p.enabled ? visibleStatus('Turned off', 'neutral', '–') : s.error ? visibleStatus('Check failed', 'critical') : cat === 'library' && s.has_account ? (s.connected ? routineStatus('Connected') : visibleStatus('Not connected', 'pending')) : cat === 'manager' ? (s.available ? routineStatus('Installed here') : visibleStatus('Not found', 'pending')) : routineStatus('Ready'); const row = el(`<div class="hh-capability"><span class="art">${esc(p.display_name.slice(0, 2).toUpperCase())}</span><span><strong>${esc(p.display_name)}</strong>${s.error ? `<small>${esc(s.error)}</small>` : ''}</span><span class="controls">${status}<button class="toggle" aria-label="Enable ${esc(p.display_name)}"><span></span></button></span></div>`); bindSwitch($('.toggle', row), p.enabled, (on) => act('set_plugin_enabled', [p.id, on], on ? 'Turned on' : 'Turned off', { what: 'Turning the plugin' }).then(() => refreshPlugins().then(renderHandheld))); if (cat === 'library' && s.has_account) { const connect = el(`<button class="hh-action">${s.connected ? 'Account…' : 'Connect…'}</button>`); connect.onclick = () => accountDialog(p.id); $('.controls', row).prepend(connect); } h.appendChild(row); });
      if (!(r.plugins || []).length) h.appendChild(stateBlock({ title: `No ${CATEGORY_NAMES[cat].toLowerCase()} plugins` }));
    }, { what: `${CATEGORY_NAMES[cat]} plugins` });
  } else if (page === 'general') {
    host.innerHTML = `<div class="hh-stack">${back}<div class="hh-settings" id="hhModes"></div><div class="hh-setting" id="hhPads"></div><div class="hh-setting"><strong>Application data folder</strong><small class="mono" style="word-break:break-all">${esc(model.info?.app_data_folder || '')}</small><button class="hh-action" data-open-path="app_data_folder">Open folder</button></div></div>`;
    renderModes($('#hhModes', host));
    // Controllers (SPEC 2.7): read by the backend, or why not.
    call('start_pad').then((r) => { model.pad = r; const box = $('#hhPads', host); if (!box) return; const pads = (ok(r) && r.pads) || []; box.innerHTML = `<strong>Controllers</strong><small>${esc(!ok(r) ? r.message : !r.available ? r.reason : pads.length ? pads.map((p) => p.name).join(' · ') : 'None connected. Keyboard and mouse work too.')}</small>`; });
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
    const startup = model.start?.start_in || 'auto';
    host.innerHTML = `<div class="hh-stack">${back}<div class="hh-settings"><div class="hh-setting"><strong>Start in</strong>${cycleRow({ id: 'hhStartupShell', label: 'Start in', options: START_IN_OPTIONS, value: startup })}<small>${esc(startInNote())}</small></div><div class="hh-setting"><strong>Handheld library view</strong>${cycleRow({ id: 'hhLibraryDefault', label: 'Handheld library view', options: [{ value: 'list', label: 'List' }, { value: 'grid', label: 'Cover grid' }], value: state.hhLibraryMode })}</div><div class="hh-setting"><strong>Theme</strong>${cycleRow({ id: 'hhTheme', label: 'Theme', options: [{ value: 'ink', label: 'Ink & Amber' }, { value: 'parchment', label: 'Parchment' }, { value: 'cobalt', label: 'Slate & Cobalt' }], value: document.documentElement.dataset.theme || 'ink' })}</div><div class="hh-setting"><strong>Text size</strong>${cycleRow({ id: 'hhTextScale', label: 'Text size', options: [{ value: '1', label: 'Normal' }, { value: '1.15', label: 'Larger' }, { value: '1.3', label: 'Large' }, { value: '1.5', label: 'Largest' }], value: prefs.get('text-scale', '1') })}</div><div class="hh-setting"><strong>Reduce motion</strong><button class="toggle" id="hhMotion" aria-label="Reduce motion"><span></span></button></div><div class="hh-setting"><strong>Desktop interface</strong><button class="hh-action" id="hhSettingsDesktop" style="width:100%">Open Desktop</button></div></div></div>`;
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
  // The same primary slot and sections as the desktop page (UX review A12); LB/RB jump between sections.
  host.innerHTML = `<div class="hh-stack"><section class="hh-panel"><div class="hh-panel-body"><div class="hh-decision-head"><div class="hh-decision-art art-${g.art}">${esc(initials(g.name))}</div><div><h1 style="margin:0">${esc(g.name)}</h1><div class="meta">${esc(g.source)} · ${esc(g.engine)} · ${esc(versionText(g))}</div><div style="margin-top:10px">${gameStatus(g)}</div></div></div><div class="actions hh-detail-actions" id="hhDetailActions"><span class="primary-slot" data-primary-slot><button class="hh-action primary" type="button" disabled>Checking…</button></span>${g.status !== 'ok' ? `<button class="hh-action" data-hh-decision="${g.id}">Review</button>` : ''}${g.updates.length ? `<button class="hh-action" disabled data-reason="Pythia records updates but does not download them yet.">Update to ${esc(g.updates[0].version || 'the newer version')}</button>` : ''}${g.knownOn.length ? '<button class="hh-action" data-hh-store>Open store page</button>' : ''}<button class="hh-action" data-hh-actions>Actions</button></div></div></section>${GAME_SECTIONS.map((sec) => `<section class="hh-detail-section" data-hh-section="${sec.id}"><h2>${sec.title}</h2><div class="hh-section-body">${sec.html(g, g.updates)}</div></section>`).join('')}</div>`;
  GAME_SECTIONS.forEach((sec) => sec.bind && sec.bind(g, $(`[data-hh-section="${sec.id}"] .hh-section-body`, host)));
  $$('[data-detail-action]', host).forEach((b) => (b.onclick = () => detailAction(b.dataset.detailAction, g, b)));
  $$('[data-variant-menu]', host).forEach((b) => (b.onclick = () => openActionMenu('variant', b, g, b.dataset.variantMenu)));
  $('[data-hh-actions]', host).onclick = (e) => openActionMenu('game', e.currentTarget, g);
  const store = $('[data-hh-store]', host); if (store) store.onclick = () => openStorePage(g);
  fillPrimarySlot(g, $('[data-primary-slot]', host), 'hh-action primary');
  loadManagers(g);
}
function hhJumpSection(id) {
  const sec = $(`#hhContent [data-hh-section="${id}"]`); if (!sec) return;
  sec.setAttribute('tabindex', '-1'); sec.scrollIntoView({ block: 'start' }); (hhFocusables(sec)[0] || sec).focus();
}
function renderHHDecision(g) {
  if (!g) { navigateHandheld('attention', false); return; }
  const d = model.intake.decisions.find((x) => g.variants.some((v) => v.path === x.path));
  const choices = [];
  if (d) choices.push({ k: 'decide', primary: true, title: 'Decide on the scan result', sub: 'Link it to a game you have, or keep it as its own.' });
  if (g.updates.length) choices.push({ k: 'plan', primary: !d, title: 'Review the available update', sub: `${g.updates[0].version || 'A newer version'} was seen on ${g.updates[0].source || 'a store'}.` });
  if (g.path) choices.push({ k: 'rescan', title: 'Rescan the folder', sub: 'Identify it again with the engine plugins that are on.' });
  if (g.path) choices.push({ k: 'open', title: 'Open the folder', sub: 'See what is inside.' });
  choices.push({ k: 'remind', title: 'Remind me later', sub: 'Keep it in Review.' });
  choices.push({ k: 'remove', title: 'Remove from library', sub: 'Files stay on disk.' });
  $('#hhContent').innerHTML = `<section class="hh-decision"><div class="hh-decision-head"><div class="hh-decision-art art-${g.art}">${esc(initials(g.name))}</div><div><div class="hh-state ${g.status}">${esc(g.statusText)}</div><h1>${esc(g.name)}</h1><p>${esc(g.unidentified ? 'No engine plugin recognised this folder, so Pythia cannot say what it needs to run.' : g.updates.length ? 'A newer version than the installed one was seen.' : 'A scan found a folder that may be a game you already have.')}</p><div class="hh-facts"><div class="hh-fact"><small>Installed</small><strong>${esc(versionText(g))}</strong></div><div class="hh-fact"><small>Observed</small><strong>${esc(g.updates[0]?.version || g.statusText)}</strong></div><div class="hh-fact"><small>Systems</small><strong>${g.systems.length}</strong></div></div></div></div><div class="hh-choice-grid">${choices.map((c) => `<button class="hh-choice ${c.primary ? 'primary' : ''}" data-hh-choice="${c.k}"><strong>${esc(c.title)}</strong><small>${esc(c.sub)}</small></button>`).join('')}</div></section>`;
}
function renderHHPlan(g) {
  if (!g) { navigateHandheld('attention', false); return; }
  const u = g.updates[0];
  $('#hhContent').innerHTML = `<section class="hh-decision"><div class="hh-state pending">Seen, not applied</div><h1 style="margin:5px 0 0;font:500 1.625rem var(--serif)">Available update · ${esc(g.name)}</h1><div class="hh-plan">${u ? `<div class="hh-plan-row"><b>Installed</b><span>${esc(versionText(g))}</span></div><div class="hh-plan-row"><b>Available</b><span>${esc(u.version || '?')} · ${esc(platformName(u.platform))}</span></div><div class="hh-plan-row"><b>Seen via</b><span>${esc(u.source || '?')}${u.detected_at ? ` on ${esc(fmt.date(u.detected_at))}` : ''}</span></div><div class="hh-plan-row"><b>Files</b><span>Pythia records updates; it does not download or apply them yet.</span></div>` : '<div class="hh-plan-row"><b>Update</b><span>No update is recorded for this game.</span></div>'}</div><div class="actions" style="display:flex;justify-content:flex-end;gap:8px;margin-top:14px"><button class="hh-action" data-hh-plan-back>Back</button><button class="hh-action primary" data-hh-apply data-reason="No newer version has been seen for this game." ${u ? '' : 'disabled'}>Apply update</button></div></section>`;
}
// ---- Handheld pages the desktop has (Droidtop/tracker#538): the same slots, drawn for the handheld -------------
function hhList(rows) { return `<div class="hh-summary-list">${rows.join('')}</div>`; }
function hhRow({ art = '', title, sub = '', status = '', actions = '' }) { return `<div class="hh-summary" style="cursor:default"><span class="art">${esc(art)}</span><span><strong>${esc(title)}</strong>${sub ? `<small>${esc(sub)}</small>` : ''}</span>${status}${actions}</div>`; }
function renderHHActivity() {
  const host = $('#hhContent');
  host.innerHTML = `<div class="hh-stack"><section class="hh-panel"><div class="hh-panel-head"><h2>Jobs</h2><span class="spacer"></span><button class="hh-action" data-hh-clear-jobs>Clear completed</button></div><div class="hh-panel-body hh-summary-list" id="hhJobList"></div></section><section class="hh-panel"><div class="hh-panel-head"><h2>Event log</h2></div><div class="hh-panel-body hh-summary-list" id="hhEvents"></div></section></div>`;
  const jobs = $('#hhJobList', host); jobs.innerHTML = model.jobs.map(hhJob).join('');
  if (!model.jobs.length) jobs.appendChild(stateBlock({ title: 'Nothing has run yet', body: 'Scans, adds and syncs show here.' }));
  const events = $('#hhEvents', host); const recent = (model.events || []).slice(-40).reverse();
  events.innerHTML = recent.map((e) => hhRow({ art: e.level === 'error' ? '!' : e.level === 'warn' ? '!' : 'i', title: e.message, sub: `${fmt.time ? fmt.time(e.ts) : e.ts} · ${e.source}` })).join('');
  if (!recent.length) events.appendChild(stateBlock({ title: 'No events yet' }));
  $('[data-hh-clear-jobs]', host).onclick = async () => { await act('clear_finished_jobs', [], 'Cleared', { what: 'Clearing' }); renderHHActivity(); };
}
function renderHHSaves() {
  const host = $('#hhContent');
  host.innerHTML = `<div class="hh-stack"><div class="hh-toolbar"><button class="hh-action primary" data-action="backup-now">Back up now</button></div><div id="hhSaveList"></div></div>`;
  $('[data-action="backup-now"]', host).onclick = () => action('backup-now').then(() => { if (state.hhPage === 'saves') renderHHSaves(); });
  loadInto($('#hhSaveList', host), 'get_save_backups', [], (r, h) => {
    const backups = r.backups || [];
    h.innerHTML = hhList(backups.map((b, i) => hhRow({ art: '⟲', title: b.game, sub: `${b.system || ''} · ${fmt.dateTime(b.last_snapshot)} · ${b.size || ''}`, actions: `<button class="hh-action" data-hh-restore="${i}">Put back…</button>` })));
    $$('[data-hh-restore]', h).forEach((btn) => (btn.onclick = async () => {
      const b = backups[Number(btn.dataset.hhRestore)];
      if (!(await confirm({ tone: 'caution', title: `Put this copy of ${b.game}'s saves back?`, body: 'The saves there now are kept first, as this computer\'s own copy, so nothing is lost.', details: `${b.system}\n${b.path}`, proceed: 'Put back' }))) return;
      const r2 = await act('restore_save_copy', [b.key || '', b.path, b.title || ''], null, { what: 'Putting the saves back' });
      if (ok(r2)) { flash(r2.files ? `Put back ${fmt.n(r2.files, 'file')}` : 'Nothing in that copy matches where the game keeps its saves; nothing was put back.', r2.files ? 'info' : 'caution'); renderHHSaves(); }
    }));
    if (!backups.length) h.appendChild(stateBlock({ title: 'No copies of saves kept', body: 'Back up now keeps one; a sync with a handheld keeps one before it overwrites saves that differ.' }));
  }, { what: 'Save backups' });
}
function renderHHLauncher() {
  const host = $('#hhContent'); host.innerHTML = '<div id="hhLauncherList"></div>';
  loadInto($('#hhLauncherList', host), 'get_launcher_entries', [], (r, h) => {
    const entries = r.entries || [];
    const label = { tracked: routineStatus('In library'), drifted: '<span class="state-pill pending">Folder moved</span>', untracked: '<span class="state-pill info">Not in library</span>' };
    h.innerHTML = hhList(entries.map((e, i) => hhRow({ art: '▶', title: e.game, sub: `${e.manager} · ${e.folder}`, status: label[e.state] || '', actions: e.state === 'untracked' ? `<button class="hh-action" data-hh-record="${i}">Record</button>` : '' })));
    $$('[data-hh-record]', h).forEach((btn) => (btn.onclick = async () => { const e = entries[Number(btn.dataset.hhRecord)]; btn.disabled = true; const r2 = await act('record_launcher_entry', [e.folder, e.key], `Recorded ${e.game}`, { what: `Recording ${e.game}` }); if (ok(r2)) renderHHLauncher(); else btn.disabled = false; }));
    if (!entries.length) h.appendChild(stateBlock({ title: 'No launcher lists a game here', body: 'Games Steam, Epic, GOG, Amazon, itch, Battle.net, Lutris, Bottles or Minigalaxy have installed show here.' }));
  }, { what: 'Launcher entries' });
}
function renderHHDuplicates(host = $('#hhContent')) {
  if (model.merges === null || model.merges === undefined) { host.replaceChildren(stateBlock({ kind: 'loading' })); pythia.refresh_merge_candidates(); return; }
  if (model.merges.status) { host.replaceChildren(fromResult(model.merges, { what: 'The duplicate search', retry: () => { model.merges = null; renderHandheld(); } })); return; }
  const groups = mergeClusters();
  if (!groups.length) { host.replaceChildren(stateBlock({ glyph: '✓', title: 'No possible duplicates', body: 'Records with the same or a similar name would show here.' })); return; }
  const stack = el('<div class="hh-stack"></div>'); groups.forEach((c) => stack.appendChild(clusterCard(c, { handheld: true }))); host.replaceChildren(stack);
}
function bindHandheldActions() {
  const host = $('#handheldShell');
  $$('[data-hh-nav-jump]', host).forEach((b) => (b.onclick = () => navigateHandheld(b.dataset.hhNavJump)));
  $$('[data-hh-review-open]', host).forEach((b) => (b.onclick = () => { state.hhReviewSection = b.dataset.hhReviewOpen; renderHandheld(); }));
  $$('[data-hh-game]', host).forEach((b) => (b.onclick = () => { state.hhHistory.push(state.hhPage); state.hhSelected = b.dataset.hhGame; state.hhPage = 'detail'; renderHandheld(); }));
  $$('[data-hh-decision]', host).forEach((b) => (b.onclick = () => { state.hhHistory.push(state.hhPage); state.hhSelected = b.dataset.hhDecision; state.hhPage = 'decision'; renderHandheld(); }));
  $$('[data-hh-plan]', host).forEach((b) => (b.onclick = () => { state.hhHistory.push(state.hhPage); state.hhSelected = b.dataset.hhPlan; state.hhPage = 'plan'; renderHandheld(); }));
  $$('[data-hh-library-mode]', host).forEach((b) => (b.onclick = () => { state.hhLibraryMode = b.dataset.hhLibraryMode; localStorage.setItem('pythia-handheld-library', state.hhLibraryMode); renderHandheld(); }));
  $('#hhSourceFilter')?.addEventListener('change', (e) => { const onIt = document.activeElement === e.target; state.hhSource = e.target.value; renderHandheld(); if (onIt) $('#hhSourceFilter')?.focus(); });
  $$('[data-hh-settings-page]', host).forEach((b) => (b.onclick = () => { state.hhSettingsPage = b.dataset.hhSettingsPage; renderHandheld(); }));
  $$('[data-hh-choice]', host).forEach((b) => (b.onclick = async () => {
    const g = games.find((x) => x.id === state.hhSelected); const k = b.dataset.hhChoice;
    if (k === 'decide') leaveHandheld({ body: 'Deciding on a scan result is on the desktop layout\u2019s Review page.', then: () => navigate('reconcile'), remember: false });
    else if (k === 'plan') { state.hhHistory.push('decision'); state.hhPage = 'plan'; renderHandheld(); }
    else if (k === 'rescan') rescanHH(g);
    else if (k === 'open') openFolder(g.path);
    else if (k === 'remind') { flash('Kept in Review'); navigateHandheld('attention', false); }
    else if (k === 'remove') { if (await confirmDanger({ title: `Remove ${g.name}?`, body: 'Its record leaves the library. Files on disk stay where they are.', recommendation: 'We recommend Cancel unless the entry is wrong.', items: g.path ? [g.path] : [], proceed: 'Remove' })) { await act('forget_game', [g.id], 'Removed from library', { what: 'Remove' }); navigateHandheld('attention', false); } }
  }));
  $$('[data-hh-open]', host).forEach((b) => (b.onclick = () => openFolder(b.dataset.hhOpen)));
  $$('[data-hh-rescan]', host).forEach((b) => (b.onclick = () => rescanHH(games.find((x) => x.id === b.dataset.hhRescan))));
  $$('[data-hh-onboard]', host).forEach((b) => (b.onclick = () => { pythia.start_onboard(b.dataset.hhOnboard); pythia.forget_detected(b.dataset.hhOnboard, () => {}); flash('Adding'); }));
  $$('[data-hh-review]', host).forEach((b) => (b.onclick = () => leaveHandheld({ body: 'Deciding on a scan result is on the desktop layout\u2019s Review page.', then: () => navigate('reconcile'), remember: false })));
  $$('[data-hh-watch]', host).forEach((b) => (b.onclick = () => (model.intake.watcher ? stopWatching() : watchFolder())));
  $$('[data-hh-add]', host).forEach((b) => (b.onclick = addFolder));
  $$('[data-hh-scan]', host).forEach((b) => (b.onclick = scanLibrary));
  $$('[data-hh-jobs]', host).forEach((b) => (b.onclick = () => navigateHandheld('activity')));
  $$('[data-hh-action]', host).forEach((b) => (b.onclick = () => action(b.dataset.hhAction)));
  $$('[data-open-path]', host).forEach((b) => (b.onclick = () => openKnownPath(b.dataset.openPath)));
  $('[data-hh-plan-back]')?.addEventListener('click', () => { state.hhPage = 'decision'; renderHandheld(); });
  $('[data-hh-apply]')?.addEventListener('click', async () => { const g = games.find((x) => x.id === state.hhSelected); const r = await call('apply_update', g.id, g.updates[0]?.version || ''); infoSheet({ title: 'Apply update', details: ok(r) ? 'Started' : r.message }); });
  $('#hhStartupShell')?.addEventListener('change', (e) => setStartIn(e.target.value));
  $('#hhLibraryDefault')?.addEventListener('change', (e) => { state.hhLibraryMode = e.target.value; localStorage.setItem('pythia-handheld-library', e.target.value); });
  $('#hhTheme')?.addEventListener('change', (e) => applyTheme(e.target.value));
  $('#hhTextScale')?.addEventListener('change', (e) => { prefs.set('text-scale', e.target.value); applyInterface(); });
  $('#hhSettingsDesktop')?.addEventListener('click', () => leaveHandheld());
}
function rescanHH(g) { if (!g || !g.path) return; pythia.start_onboard(g.path); flash('Rescanning'); navigateHandheld('home', false); }
// Handheld focus (Droidtop/tracker#538): the D-pad moves within one zone (the rail, the top bar, the page, or an open
// dialog or menu) and crosses to the next zone only at its edge, so Down from the top bar enters the page, not the rail.
// ---- The pad model (docs/SPEC.md, section 2; UX review A7) -----------------------------------
// Zones: the rail, the strip (the top bar and the page's own toolbar), the content, and an open dialog or
// menu. Focus moves within a zone and crosses only at its edge; each zone remembers its place on each page.
const hhVisible = (x) => x.offsetParent !== null && getComputedStyle(x).visibility !== 'hidden';
function hhFocusables(root) { return root ? $$('button:not([disabled]),input:not([disabled]),select:not([disabled]),[tabindex="0"]', root).filter(hhVisible) : []; }
const hhNearest = (items, from, direction) => window.PythiaPad.nearest(items, from, direction);
function hhFocus(node) { if (!node) return false; node.focus(); node.scrollIntoView({ block: 'nearest', inline: 'nearest' }); return true; }
function hhZoneOf(node) {
  if (!node || !node.closest) return null;
  if (node.closest('.hh-rail')) return 'rail';
  if (node.closest('.hh-topbar') || node.closest('#hhContent .hh-toolbar')) return 'strip';
  if (node.closest('#hhContent')) return 'content';
  return null;
}
function hhZoneItems(zone) {
  if (zone === 'rail') return hhFocusables($('.hh-rail'));
  if (zone === 'strip') return [...hhFocusables($('.hh-topbar')), ...hhFocusables($('#hhContent .hh-toolbar'))];
  return hhFocusables($('#hhContent')).filter((x) => !x.closest('.hh-toolbar'));
}
// A place is the item, not its position: a game, a settings link, an id; else its index in the zone.
const HH_PLACE_ATTRS = ['hhGame', 'hhDecision', 'hhPlan', 'hhSettingsPage', 'hhNav', 'hhNavJump', 'hhAction', 'hhLibraryMode', 'hhChoice'];
const hhPlaces = {};
const hhPlaceKey = () => `${state.hhPage}|${state.hhPage === 'settings' ? state.hhSettingsPage : ['detail', 'decision', 'plan'].includes(state.hhPage) ? state.hhSelected : ''}`;
function hhLocator(node, items) {
  if (node.id) return { id: node.id };
  const attr = HH_PLACE_ATTRS.find((a) => node.dataset[a] !== undefined);
  if (attr) return { attr, value: node.dataset[attr] };
  return { index: items.indexOf(node) };
}
function hhFind(place, items) {
  if (!place) return null;
  if (place.id) return items.find((x) => x.id === place.id) || null;
  if (place.attr) return items.find((x) => x.dataset[place.attr] === place.value) || null;
  return items[place.index] || null;
}
function hhRemember(node) {
  const zone = hhZoneOf(node); if (!zone) return;
  hhPlaces[`${hhPlaceKey()}|${zone}`] = hhLocator(node, hhZoneItems(zone));
  hhPlaces[`${hhPlaceKey()}|zone`] = zone;
}
function hhEnterZone(zone) {
  const items = hhZoneItems(zone);
  return hhFocus(hhFind(hhPlaces[`${hhPlaceKey()}|${zone}`], items) || (zone === 'rail' ? $('.hh-rail .hh-nav.active') : null) || items[0]);
}
function hhFocusRail() { return hhEnterZone('rail'); }
function hhFocusPage() { return hhEnterZone('content'); }
function moveHandheldFocus(direction) {
  const overlay = [...$$('.ctx-menu'), ...$$('.scrim')].pop();
  const current = document.activeElement;
  if (overlay) { const items = hhFocusables(overlay); if (!items.length) return; hhFocus(items.includes(current) ? hhNearest(items, current, direction) || current : items[0]); return; }
  const zone = hhZoneOf(current);
  if (!zone) { hhEnterZone('content') || hhEnterZone('rail'); return; }
  const items = hhZoneItems(zone);
  const next = items.includes(current) ? hhNearest(items, current, direction) : items[0];
  if (next) { hhFocus(next); return; }
  // Up and Down cross to the nearest item in that direction, so Up from the first row reaches the page's own
  // toolbar before the top bar (retest B1); Left and Right land on the zone's remembered place.
  if (direction === 'left' && zone !== 'rail') hhEnterZone('rail');
  else if (zone === 'content' && direction === 'up') hhFocus(hhNearest(hhZoneItems('strip'), current, 'up')) || hhEnterZone('strip');
  else if (zone === 'strip' && direction === 'down') hhFocus(hhNearest(hhZoneItems('content'), current, 'down')) || hhEnterZone('content') || hhEnterZone('rail');
  else if (zone === 'rail' && direction === 'right') hhEnterZone('content') || hhEnterZone('strip');
}
// After a page is drawn: focus stays where it is if that is still in the shell; else it returns to the
// place this page last had (the row you left), else the first content item.
function hhKeepFocus() {
  const current = document.activeElement;
  if (current && current.isConnected && hhZoneOf(current)) { hhHints(); return; }
  const zone = hhPlaces[`${hhPlaceKey()}|zone`] || 'content';
  hhEnterZone(zone) || hhEnterZone('content') || hhEnterZone('rail');
  hhHints();
}
// A disabled control stays focusable in the handheld shell and says why, inline (SPEC 2.5): its reason is
// data-reason or its title. A does nothing on it.
function hhKeepDisabledReachable() {
  $$('#handheldShell button[disabled]').forEach((b) => {
    b.disabled = false; b.setAttribute('aria-disabled', 'true'); b.classList.add('is-disabled');
    const reason = b.dataset.reason || b.title;
    if (reason && !b.nextElementSibling?.classList.contains('disabled-reason')) b.insertAdjacentElement('afterend', el(`<small class="disabled-reason">${esc(reason)}</small>`));
  });
}
document.addEventListener('click', (e) => { const b = e.target.closest?.('#handheldShell [aria-disabled="true"]'); if (b) { e.preventDefault(); e.stopImmediatePropagation(); } }, true);
document.addEventListener('focusin', (e) => { if (state.shell === 'handheld') { hhRemember(e.target); hhHints(); } });
// The one Back (UX review A4): B, Escape, the footer's Back and the on-screen ‹. A settings subpage goes
// to the settings root, a subpage to where it was opened from (or its parent), a rail page to Home, and
// Home stays. It never leaves the shell.
function hhBack() {
  if (state.hhPage === 'settings' && state.hhSettingsPage !== 'root') { state.hhSettingsPage = 'root'; renderHandheld(); return; }
  if (state.hhPage === 'attention' && state.hhReviewSection) { state.hhReviewSection = ''; renderHandheld(); return; }
  if (state.hhPage === 'home') return;
  if (!HH_RAIL_PAGES.includes(state.hhPage)) {
    const back = state.hhHistory.pop() || HH_PARENTS[state.hhPage] || 'home';
    state.hhPage = back; if (HH_RAIL_PAGES.includes(back)) state.hhSelected = null; if (back !== 'settings') state.hhSettingsPage = 'root';
    renderHandheld(); return;
  }
  state.hhHistory = []; state.hhPage = 'home'; state.hhSelected = null; state.hhSettingsPage = 'root'; renderHandheld();
}
// The page's strip, for LB and RB (SPEC 2.2): the library's source filter, the settings pages, a game page's sections.
const HH_SETTINGS_PAGES = ['general', 'library', 'capabilities', 'plugins', 'sync', 'interface', 'advanced'];
function hhStrip() {
  if (state.hhPage === 'library' && $('#hhSourceFilter')) return { label: 'Source', step: (d) => UI.cycleStep($('#hhSourceFilter'), d) };
  if (state.hhPage === 'settings' && state.hhSettingsPage !== 'root') return { label: 'Pages', step: (d) => { const i = HH_SETTINGS_PAGES.indexOf(state.hhSettingsPage); state.hhSettingsPage = HH_SETTINGS_PAGES[(i + d + HH_SETTINGS_PAGES.length) % HH_SETTINGS_PAGES.length]; renderHandheld(); } };
  const sections = $$('#hhContent .hh-detail-section, #hhContent > .hh-stack > section');
  if (state.hhPage === 'detail' && sections.length > 1) return { label: 'Sections', step: (d) => {
    const top = $('#hhContent').getBoundingClientRect().top;
    let at = sections.findIndex((s) => s.contains(document.activeElement));
    if (at < 0) at = Math.max(0, sections.findIndex((s) => s.getBoundingClientRect().bottom > top + 8));
    const next = sections[Math.min(sections.length - 1, Math.max(0, at + d))];
    next.setAttribute('tabindex', '-1'); next.scrollIntoView({ block: 'start' }); (hhFocusables(next)[0] || next).focus();
  } };
  return null;
}
// X: the focused item's action menu, which holds Info (SPEC 2.2; the shared menu model is A11).
function hhItemMenu(node) {
  const gameId = node?.dataset?.hhGame; const reviewId = node?.dataset?.hhDecision;
  const g = games.find((x) => x.id === (gameId || reviewId)); if (!g) return null;
  return gameId ? () => openActionMenu('game', node, g, { open: () => node.click() }) : () => openActionMenu('review', node, g);
}
// Start: the Pythia menu (SPEC 2.2).
function hhMenu(anchor) {
  const returnTo = document.activeElement;
  menu(anchor || $('#hhMenuHint'), [
    { label: 'Activity & jobs', run: () => navigateHandheld('activity') },
    { label: 'Save backups', run: () => navigateHandheld('saves') },
    { label: 'Launcher entries', run: () => navigateHandheld('launcher') },
    { label: 'Review', run: () => hhOpenReview('') },
    { label: 'Add a game…', run: () => $('#hhAdd').click() },
    ...(model.config?.error_console ? [{ label: 'Error console', run: () => ErrorConsole.setOpen(true) }] : []),
    { rule: true },
    { label: 'Switch to the desktop layout', run: () => leaveHandheld() },
    { label: 'Quit Pythia', run: async () => { if (await confirm({ title: 'Quit Pythia?', body: 'Running jobs stop.', proceed: 'Quit' })) pythia.quit_app(); } },
  ], { returnTo });
}
// One press from the backend's controller reader (pythia/pad.py), only while Pythia has the focus. Directions
// and B go through the keyboard's path, so a choice row, a dialog and Back behave the same for both.
function hhPadAction(name) {
  if (state.shell !== 'handheld') return;
  const key = (k) => document.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true }));
  const overlay = [...$$('.ctx-menu'), ...$$('.scrim')].pop();
  const f = document.activeElement;
  if (['up', 'down', 'left', 'right'].includes(name)) key(`Arrow${name[0].toUpperCase()}${name.slice(1)}`);
  else if (name === 'a') { if (f && f !== document.body && !f.classList.contains('hh-hint')) f.click(); }
  else if (name === 'b') key('Escape');
  else if (overlay) return;
  else if (name === 'x') hhItemMenu(f)?.();
  else if (name === 'y') hhFocus($('#hhSearch'));
  else if (name === 'l1' || name === 'r1') hhStrip()?.step(name === 'l1' ? -1 : 1);
  else if (name === 'start') hhMenu();
  else if (name === 'select' && state.hhPage === 'library') $(`[data-hh-library-mode="${state.hhLibraryMode === 'grid' ? 'list' : 'grid'}"]`)?.click();
}
// The footer names only what dispatches here (SPEC 2.3), with the Win 4's Xbox labels; each hint is also a button.
function hhHints() {
  const host = $('#hhHints'); if (!host) return;
  const overlay = [...$$('.ctx-menu'), ...$$('.scrim')].pop();
  const f = document.activeElement;
  const canBack = overlay || state.hhPage !== 'home' || (state.hhPage === 'settings' && state.hhSettingsPage !== 'root');
  const hints = [];
  if (f && f !== document.body && f.getAttribute('aria-disabled') !== 'true' && (hhZoneOf(f) || overlay?.contains(f))) hints.push(['a', 'A', 'Select', 'hhSelectHint']);
  if (canBack) hints.push(['b', 'B', overlay ? 'Close' : 'Back', 'hhBackHint']);
  if (!overlay) {
    if (hhItemMenu(f)) hints.push(['x', 'X', 'Actions']);
    hints.push(['y', 'Y', 'Search']);
    const strip = hhStrip(); if (strip) hints.push(['r1', 'LB RB', strip.label]);
    if (state.hhPage === 'library') hints.push(['select', '⧉', state.hhLibraryMode === 'grid' ? 'List' : 'Grid']);
    hints.push(['start', '☰', 'Menu', 'hhMenuHint']);
  }
  host.innerHTML = hints.map(([action, glyph, label, id]) => `<button class="hh-hint" type="button" tabindex="-1" data-pad="${action}" ${id ? `id="${id}"` : ''}><span class="pad-glyph">${esc(glyph)}</span> ${esc(label)}</button>`).join('');
  $$('[data-pad]', host).forEach((b) => (b.onclick = (e) => (b.dataset.pad === 'start' ? hhMenu(e.currentTarget) : hhPadAction(b.dataset.pad))));
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
  if (a === 'handheld') enterHandheld('home', { remember: true });
  if (a === 'refresh-all') { const r = await call('sync_all_accounts'); if (ok(r)) { flash(r.started.length ? `Syncing ${fmt.n(r.started.length, 'source')}` : 'No connected source to refresh', r.started.length ? 'info' : 'caution'); if (r.started.length) navigate('activity'); } else flash(`Refresh failed: ${r.message}`, 'caution'); }
  if (a === 'check-updates') { pythia.check_updates(); flash('Checking for updates'); navigate('activity'); }
  if (a === 'duplicates') { navigate('reconcile'); $('#mergePanel').scrollIntoView({ behavior: 'smooth', block: 'start' }); }
  if (a === 'rebuild-cache') act('rebuild_index', [], 'Index rebuilt', { what: 'Rebuilding the index' });
  if (a === 'tools') { const r = await call('get_tools'); infoSheet({ title: 'Downloadable tools', node: fromResult(r, { what: 'Downloadable tools' }) }); }
  if (a === 'about') { const app = model.app || {}; infoSheet({ title: 'About Pythia', details: [`Pythia ${app.version || ''}`, app.platform || '', `Python ${app.python || ''}`, `Schema v${app.schema_version || ''}`, '', 'Pythia is largely based on Lutris. License: GPL-3.0-or-later.', `Log file: ${app.log_file || ''}`, `Config: ${app.config_file || ''}`].join('\n') }); }
  if (a === 'issue') handOff({ kind: 'link', title: 'Open the issue tracker?', body: 'Your web browser opens.', run: () => act('open_url', [model.app?.issues_url || 'https://github.com/bi0shacker001/pythia/issues'], 'Opened the issue tracker', { what: 'Opening the browser' }) });
  if (a === 'docs') handOff({ kind: 'link', title: 'Open the documentation?', body: 'Your web browser opens.', run: () => act('open_url', [model.app?.docs_url || 'https://github.com/bi0shacker001/pythia#readme'], 'Opened the documentation', { what: 'Opening the browser' }) });
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
function openKnownPath(key) { const info = model.info || {}; const app = model.app || {}; const path = key === 'log_file_dir' ? String(app.log_file || '').replace(/[\\/][^\\/]*$/, '') : info[key]; openFolder(path); }

// ---- Chrome: counts, the computer card, the sync indicator, the status bar -------------------
function renderChrome() {
  const running = model.jobs.filter((j) => j.state === 'running').length;
  const set = (id, n) => { const x = $(id); if (x) x.textContent = n ? String(n) : ''; };
  const review = model.ready ? reviewQueue().affected : 0;
  set('#allCount', games.length); set('#reviewCount', review); set('#activityCount', running);
  $('#reviewCount')?.setAttribute('aria-label', review ? `${fmt.n(review, 'game')} to review` : '');
  
  $('#sidebarProgress').classList.toggle('hidden', !running);
  const mine = games.filter((g) => g.mine && g.installed).length;
  $('#sidebarSystemText').innerHTML = model.ready ? `${esc(model.app?.platform || '')}<br>${esc(fmt.n(mine, 'install'))} here` : 'Connecting to Pythia…';
  const nSystems = new Set(games.flatMap((g) => g.systemIds)).size || 1;
  $('#statusRight').textContent = model.ready ? `${fmt.n(games.length, 'game')} · ${fmt.n(games.filter((g) => g.installed).length, 'installed here', 'installed here')}` : '';
  $('#statusSyncLabel').textContent = !pythia ? 'Not connected' : nSystems > 1 ? `Library shared by ${nSystems} computers` : 'Library on this computer only';
  // The top bar never claims success (UX review A1): it says when Pythia is not connected or jobs failed,
  // and otherwise only when the library was last read, without a check mark.
  const ind = $('#syncIndicator');
  const failed = model.jobs.filter((j) => j.state === 'failed');
  if (!pythia) ind.innerHTML = `${visibleStatus('Not connected', 'critical')}`;
  else if (failed.length) { ind.innerHTML = `<button class="status-button" type="button">${visibleStatus(`${fmt.n(failed.length, 'job')} failed`, 'critical')}</button>`; $('button', ind).onclick = () => navigate('activity'); }
  else ind.innerHTML = `<span id="syncLabel" class="status-quiet">${model.gamesAt ? `Read ${esc(fmt.ago(model.gamesAt))}` : 'Reading the library…'}</span>`;
}
setInterval(() => { const l = $('#syncLabel'); if (l && model.gamesAt) l.textContent = `Read ${fmt.ago(model.gamesAt)}`; }, 15000);
// A job that has just failed raises a critical toast that stays until dismissed, with Show opening the job.
function announceFailedJobs(before, after) {
  const was = new Map((before || []).map((j) => [j.id, j.state]));
  after.filter((j) => j.state === 'failed' && was.get(j.id) !== 'failed').forEach((j) => {
    flash(`${j.title} failed: ${j.summary || j.detail || 'no reason given'}`, 'critical', { action: { label: 'Show', run: () => showJob(j.id) } });
  });
}
function showJob(id) {
  navigate('activity');
  const j = model.jobs.find((x) => x.id === id); if (!j) return;
  infoSheet({ title: j.title, details: [`${j.detail}`, `Started ${fmt.dateTime(j.started_at)}`, j.summary || '', '', ...(j.log || [])].join('\n') });
}

// ---- Backend bridge: connect, signals, initial loads ------------------------------------------
function refreshGames() {
  if (!pythia) return;
  model.raw = pythia.games || [];
  games = model.raw.map(adaptGame);
  model.gamesAt = new Date().toISOString();
  populateFilterOptions(); updateSystems(model.raw); renderChrome();
  if (state.shell === 'handheld') renderHandheld(); else if (['library', 'shared', 'reconcile'].includes(state.view)) renderCurrent();
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
    ErrorConsole.attach(pythia); // the page's errors reach Pythia's log whether or not the console is on
    pythia.asyncResult.connect(asyncArrived);
    window.pythia = pythia;
    pythia.gamesChanged.connect(refreshGames);
    // Controllers come from the backend only (SPEC 2.7); the page does not read the Gamepad API.
    pythia.padAction.connect(hhPadAction);
    pythia.lookChanged.connect((r) => { model.look = r; if (r.status === 'added') pythia.refresh_merge_candidates(); if (state.view === 'look') renderLook(); if (state.shell === 'handheld' && state.hhPage === 'look') renderHandheld(); });
    call('get_look').then((r) => { if (ok(r) || r.status) model.look = r; });
    call('get_folder_vocabulary').then((r) => { if (ok(r)) { model.vocab = r; renderCurrent(); } });
    call('describe_store_links').then((r) => { if (ok(r)) model.stores = r; });
    trimSettings();
    startShell();
    call('start_pad').then((r) => { model.pad = r; if (ok(r) && !r.available) ErrorConsole.reportWeb('warn', r.reason || 'Controllers are not read', 'start_pad'); });
    pythia.modesChanged.connect(() => refreshModes().then(() => renderCurrent()));
    pythia.handheldsChanged.connect(() => { if (state.view === 'devices') renderHandhelds(); if (state.shell === 'handheld' && state.hhPage === 'systems') renderHandheld(); });
    pythia.pairingFinished.connect((r) => flash(r.status === 'ok' ? `Paired with ${r.paired}` : `Not paired: ${r.message}`, r.status === 'ok' ? 'info' : 'caution'));
    pythia.logLine.connect(() => { if (state.view === 'activity') renderJobs(); });
    pythia.uiLog.connect((e) => { model.events.push(e); if (model.events.length > 500) model.events.shift(); if (state.view === 'activity') renderJobs(); });
    pythia.jobsChanged.connect((jobs) => { announceFailedJobs(model.jobs, jobs || []); model.jobs = jobs || []; renderChrome(); if (state.view === 'activity' || state.view === 'downloads') renderCurrent(); if (state.shell === 'handheld' && state.hhPage === 'home') renderHandheld(); });
    pythia.intakeChanged.connect((i) => { model.intake = { detected: i.detected || [], decisions: i.decisions || [], watcher: i.watcher || null }; games = model.raw.map(adaptGame); renderChrome(); if (['downloads', 'reconcile', 'library'].includes(state.view)) renderCurrent(); if (state.shell === 'handheld') renderHandheld(); });
    pythia.decisionNeeded.connect((d) => flash(`${d.name || fileName(d.path)} needs a decision`, 'caution'));
    // A failure is the failed job's own critical toast (announceFailedJobs); only success is acknowledged here.
    pythia.onboardComplete.connect((r) => { if (r.status === 'ok') { flash(`Added ${r.name || ''}`.trim()); pythia.refresh_merge_candidates(); } });
    pythia.scanComplete.connect((results) => { const okN = (results || []).filter((r) => r.status === 'ok').length; flash(`Scan finished: ${fmt.n(okN, 'game')} found`); pythia.refresh_merge_candidates(); });
    pythia.downloadDetected.connect((path) => flash(`New file: ${fileName(path)}`));
    pythia.mergeCandidatesReady.connect((c) => { model.merges = c || []; renderChrome(); if (state.view === 'reconcile') renderReconcile(); if (state.shell === 'handheld' && ['home', 'attention', 'duplicates'].includes(state.hhPage)) renderHandheld(); });
    pythia.mergeComplete.connect((r) => { flash(r.status === 'ok' ? 'Records folded' : `Not folded: ${r.message}`, r.status === 'ok' ? 'info' : 'caution'); model.merges = null; if (state.view === 'reconcile') renderReconcile(); pythia.refresh_merge_candidates(); });
    pythia.accountSyncComplete.connect((pluginId, r) => { flash(r.status === 'ok' ? `Synced: ${r.linked} linked, ${r.created} new` : `Sync failed: ${r.message}`, r.status === 'ok' ? 'info' : 'caution'); refreshPlugins(); });
    pythia.updateCheckComplete.connect((r) => flash(r.status === 'ok' ? `Update check: ${r.checked} checked, ${r.updated} with an update` : `Update check failed: ${r.message}`, r.status === 'ok' ? 'info' : 'caution'));
    pythia.platformEnableComplete.connect((r) => { flash(r.status === 'ok' || !r.status ? `${platformName(r.platform)} prepared` : `Could not prepare: ${r.message}`, r.status === 'ok' || !r.status ? 'info' : 'caution'); if (state.view === 'library') renderLibrary(); });
    pythia.get_system_uuid((uuid) => {
      model.mySystem = uuid;
      Promise.all([call('get_system_info'), call('get_app_info'), call('get_ui_config'), call('get_jobs'), call('get_intake'), call('get_ui_log'), refreshPlugins(), refreshModes()]).then(([info, app, config, jobs, intake, log]) => {
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
  bindGameList();
  document.addEventListener('keydown', (e) => {
    const typing = ['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement?.tagName) || document.activeElement?.isContentEditable;
    if (state.shell !== 'desktop' || $('.scrim')) return;
    if ((e.altKey && e.key === 'ArrowLeft') || (e.key === 'Backspace' && !typing)) { e.preventDefault(); goBack(); }
  });
  window.addEventListener('mouseup', (e) => { if (e.button === 3) { e.preventDefault(); goBack(); } });
  $('#backButton').onclick = () => { const prev = state.history.pop(); if (prev) navigate(prev, false); $('#backButton').disabled = !state.history.length; };
  const searchSoon = debounce(() => renderCurrent(), SEARCH_DEBOUNCE_MS);
  $('#globalSearch').addEventListener('input', (e) => { state.search = e.target.value; searchSoon(); });
  document.addEventListener('keydown', (e) => {
    if (state.shell === 'handheld') {
      if (e.defaultPrevented) return; // a dialog or menu already handled this key (its Escape, say)
      const overlay = $('.scrim') || $('.ctx-menu');
      const typing = ['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement?.tagName) && ['ArrowLeft', 'ArrowRight'].includes(e.key);
      if (['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(e.key) && !typing) { e.preventDefault(); moveHandheldFocus(e.key.replace('Arrow', '').toLowerCase()); return; }
      if (overlay) return; // the dialog or menu handles Escape itself
      if (e.key === 'Escape') { e.preventDefault(); hhBack(); }
      if (e.key === 'ContextMenu' || (e.key === 'F10' && e.shiftKey)) { e.preventDefault(); hhMenu(); }
      return;
    }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); $('#globalSearch').focus(); }
  });
  $('#sourceFilter').onchange = (e) => { state.source = e.target.value; renderLibrary(); };
  $('#engineFilter').onchange = (e) => { state.engine = e.target.value; renderLibrary(); };
  $('#sortSelect').onchange = (e) => { state.sort = e.target.value; renderLibrary(); };
  $('#clearFilters').onclick = () => { state.source = 'all'; state.engine = 'all'; state.filterStatus = 'all'; state.search = ''; $('#globalSearch').value = ''; $('#sourceFilter').value = 'all'; $('#engineFilter').value = 'all'; renderLibrary(); };
  $$('[data-library-shelf]').forEach((b) => (b.onclick = () => setLibraryShelf(b.dataset.libraryShelf)));
  $$('[data-library-mode]').forEach((b) => (b.onclick = () => { state.libraryMode = b.dataset.libraryMode; localStorage.setItem('pythia-desktop-library', state.libraryMode); $('#desktopLibrarySelect').value = state.libraryMode; renderLibrary(); }));
  $$('[data-settings]').forEach((b) => (b.onclick = () => openSettingsPane(b.dataset.settings)));
  $('#themeButton').onclick = (e) => { e.stopPropagation(); toggleThemePopover(); };
  $$('[data-theme-choice]').forEach((b) => (b.onclick = () => { applyTheme(b.dataset.themeChoice); toggleThemePopover(false); flash(`Theme: ${b.textContent.trim()}`, 'info', { private: true }); }));
  $('#themeSelect').onchange = (e) => applyTheme(e.target.value);
  $('#textScaleSelect').onchange = (e) => { prefs.set('text-scale', e.target.value); applyInterface(); };
  $('#densitySelect').onchange = (e) => { state.density = e.target.value; prefs.set('density', state.density); applyInterface(); };
  bindSwitch($('#motionToggle'), prefs.get('motion', 'on') === 'off', (on) => { prefs.set('motion', on ? 'off' : 'on'); applyInterface(); }, 'Reduce motion');
  $('#logLevelSelect').onchange = (e) => act('set_log_level', [e.target.value], 'Log level saved', { what: 'Log level' });
  document.addEventListener('click', (e) => { if (!e.target.closest('#themePopover') && !e.target.closest('#themeButton')) toggleThemePopover(false); if (!e.target.closest('#addPopover') && !e.target.closest('#addButton')) { $('#addPopover').classList.add('hidden'); $('#addButton').setAttribute('aria-expanded', 'false'); } if (!e.target.closest('.menu-item')) closeAllMenus(); });
  $('#recentButton').onclick = (e) => { e.stopPropagation(); const existing = $('.recent-popover'); if (existing) { existing.remove(); return; } const pop = el('<div class="recent-popover" role="dialog" aria-label="Recent"></div>'); if (!flash.recent.length) pop.appendChild(stateBlock({ title: 'Nothing recent', body: 'Acknowledgements show here for a while.' })); else flash.recent.forEach((r) => pop.appendChild(el(`<div class="line"><span>${esc(r.text)}</span><time>${esc(fmt.time(r.at))}</time></div>`))); document.body.appendChild(pop); const close = (ev) => { if (!pop.contains(ev.target)) { pop.remove(); document.removeEventListener('mousedown', close); } }; setTimeout(() => document.addEventListener('mousedown', close), 0); };
  $('#enterHandheldButton').onclick = () => enterHandheld('home', { remember: true }); $('#exitHandheldRail').onclick = () => leaveHandheld(); $('#exitHandheldTop').onclick = () => leaveHandheld();
  // The footer's hints are buttons too (touch and mouse); pressing one keeps focus where it was, so Select acts on it.
  $$('.hh-hint').forEach((b) => b.addEventListener('mousedown', (e) => e.preventDefault()));
  $$('[data-hh-nav]').forEach((b) => (b.onclick = () => navigateHandheld(b.dataset.hhNav)));
  $('#hhBack').onclick = () => hhBack();
  const hhSearchSoon = debounce(() => renderHandheld(), SEARCH_DEBOUNCE_MS);
  $('#hhSearch').oninput = (e) => { state.hhSearch = e.target.value; hhSearchSoon(); };
  $('#hhAdd').onclick = addFolder;
  $$('[data-bulk-action]').forEach((b) => (b.onclick = () => bulkAction(b.dataset.bulkAction)));
  $('#selectionModeButton').onclick = () => { state.selectionMode = !state.selectionMode; if (!state.selectionMode) state.bulk.clear(); updateBulkToolbar(); renderLibrary(); };
  $('#bulkSelectAll').onclick = () => { filteredGames().forEach((g) => state.bulk.add(g.id)); updateBulkToolbar(); renderLibrary(); };
  $('#bulkClear').onclick = () => { state.bulk.clear(); state.selectionMode = false; updateBulkToolbar(); renderLibrary(); };
  $('#addButton').onclick = (e) => { e.stopPropagation(); const pop = $('#addPopover'); const show = pop.classList.contains('hidden'); pop.classList.toggle('hidden', !show); $('#addButton').setAttribute('aria-expanded', String(show)); toggleThemePopover(false); };
  $$('[data-add-choice]').forEach((b) => (b.onclick = () => { const kind = b.dataset.addChoice; $('#addPopover').classList.add('hidden'); $('#addButton').setAttribute('aria-expanded', 'false'); closeAllMenus(); if (kind === 'scan') scanLibrary(); else if (kind === 'stores') findStoreGames(); else if (kind === 'download') navigate('downloads'); else if (kind === 'folder') addFolder(); else importSource(); }));
  $('#refreshMerges').onclick = () => { model.merges = null; renderReconcile(); pythia.refresh_merge_candidates(); };
  renderStartIn(); $('#desktopLibrarySelect').value = state.libraryMode; $('#handheldLibrarySelect').value = state.hhLibraryMode;
  $('#startupShellSelect').onchange = (e) => setStartIn(e.target.value);
  $('#desktopLibrarySelect').onchange = (e) => { state.libraryMode = e.target.value; localStorage.setItem('pythia-desktop-library', e.target.value); renderLibrary(); };
  $('#handheldLibrarySelect').onchange = (e) => { state.hhLibraryMode = e.target.value; localStorage.setItem('pythia-handheld-library', e.target.value); };
  $$('[data-menu]').forEach((b) => (b.onclick = (e) => { e.stopPropagation(); toggleMenu(b.dataset.menu); }));
  $$('[data-menu-action]').forEach((b) => (b.onclick = () => menuAction(b.dataset.menuAction)));
  $$('[data-menu-nav]').forEach((b) => (b.onclick = () => { if (b.dataset.filter) state.filterStatus = b.dataset.filter; navigate(b.dataset.menuNav); closeAllMenus(); }));
  $$('[data-menu-density]').forEach((b) => (b.onclick = () => { state.density = b.dataset.menuDensity; prefs.set('density', state.density); applyInterface(); closeAllMenus(); }));
  $$('[data-menu-theme]').forEach((b) => (b.onclick = () => { applyTheme(b.dataset.menuTheme); closeAllMenus(); }));
  $$('[data-nav-link]').forEach((b) => (b.onclick = () => navigate(b.dataset.navLink)));
  $$('[data-settings-nav]').forEach((b) => (b.onclick = () => { openSettingsPane(b.dataset.settingsNav); closeAllMenus(); }));
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeAllMenus(); });
  renderChrome(); renderLibrary();
  if (explicitShell() === 'handheld') enterHandheld('home');
  connect();
}
setup();

// A read-only view of the model for the screenshot driver and the console; never written to.
window.pythiaDebug = () => ({ ready: model.ready, connected: !!pythia, games: games.length, attention: games.filter((g) => g.status !== 'ok').length, jobs: model.jobs.length, decisions: model.intake.decisions.length, merges: Array.isArray(model.merges) ? model.merges.length : model.merges, events: model.events.length, errorConsole: !!model.config?.error_console, view: state.view, shell: state.shell });
window.pythiaUI = { navigate, enterHandheld, exitHandheld, leaveHandheld, hhBack, hhKeepDisabledReachable, navigateHandheld, openSettingsPane, openTab, state, call };
})();
