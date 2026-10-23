// SPDX-License-Identifier: GPL-3.0-or-later
'use strict';
const $ = (s) => document.querySelector(s);
const token = location.hash.slice(1);
const state = {devices: [], apps: [], library: [], purchases: [], account: null, jobs: [], busy: false,
  page: 1, total: 0, purchaseMode: 'purchases', seen: new Set(), promptId: '', active: null, ready: false};
let pendingInstall = null;
let toastTimer;

function text(node, value) { node.textContent = value == null ? '' : String(value); }
function toast(message) { text($('#toast'), message); $('#toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 6000); }
async function api(path, body) {
  const response = await fetch('/api/' + path, {method: body === undefined ? 'GET' : 'POST',
    headers: {'X-PhoneBridge-Token': token, ...(body === undefined ? {} : {'Content-Type': 'application/json'})},
    ...(body === undefined ? {} : {body: JSON.stringify(body)})});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Операция не выполнена.');
  return result;
}
function view(name) {
  document.querySelectorAll('.view').forEach((node) => { node.hidden = node.id !== 'view-' + name; });
  document.querySelectorAll('.nav').forEach((node) => node.classList.toggle('active', node.dataset.view === name));
  text($('#current-section'), {devices: 'На iPhone', library: 'Библиотека IPA', apple: 'Учётная запись Apple', activity: 'Журнал операций', about: 'О приложении'}[name]);
}
document.querySelectorAll('[data-view]').forEach(node => node.addEventListener('click', () => view(node.dataset.view)));
document.querySelectorAll('[data-close]').forEach(node => node.addEventListener('click', () => $('#' + node.dataset.close).close()));

function setBusy(value) {
  state.busy = value;
  ['refresh-devices', 'read-apps', 'manual-download', 'import-ipa', 'login', 'check-account', 'logout', 'get-purchases', 'prev-page', 'next-page', 'confirm-install'].forEach(id => {
    $('#' + id).disabled = value;
  });
  document.querySelectorAll('[data-operation]').forEach(node => { node.disabled = value; });
  $('#setup').disabled = value || state.ready;
  $('#store-search button').disabled = value;
}

async function action(name, data = {}) {
  if (state.busy) return toast('Сначала завершите текущую операцию.');
  setBusy(true);
  try {
    const result = await api('actions', {action: name, ...data});
    state.active = result.job_id;
    await poll();
  } catch (error) { setBusy(false); toast(error.message); }
}

function empty(container, icon, title, description, buttonText, callback) {
  container.replaceChildren();
  const box = document.createElement('div'); box.className = 'empty';
  const symbol = document.createElement('div'); symbol.className = 'empty-icon'; text(symbol, icon);
  const heading = document.createElement('h3'); text(heading, title);
  const paragraph = document.createElement('p'); text(paragraph, description);
  box.append(symbol, heading, paragraph);
  if (buttonText) { const b = document.createElement('button'); b.className = 'button small'; text(b, buttonText); b.addEventListener('click', callback); box.append(b); }
  container.append(box);
}

function appTable(container, items, mode) {
  container.replaceChildren();
  const table = document.createElement('table');
  const thead = document.createElement('thead');
  const hr = document.createElement('tr');
  ['ПРИЛОЖЕНИЕ', 'ВЕРСИЯ', mode === 'library' ? 'РАЗМЕР' : 'APP STORE ID', ''].forEach(label => { const th = document.createElement('th'); text(th, label); hr.append(th); });
  thead.append(hr); table.append(thead);
  const body = document.createElement('tbody');
  for (const item of items) {
    const row = document.createElement('tr');
    const nameCell = document.createElement('td');
    const name = document.createElement('div'); name.className = 'app-name';
    const icon = document.createElement('span'); icon.className = 'app-icon'; text(icon, (item.name || '?').slice(0, 1).toUpperCase());
    const label = document.createElement('div');
    const strong = document.createElement('strong'); text(strong, item.name || item.bundle_id);
    const small = document.createElement('small'); text(small, item.bundle_id || '—'); label.append(strong, small); name.append(icon, label); nameCell.append(name);
    const versionCell = document.createElement('td'); text(versionCell, item.version || '—');
    const idCell = document.createElement('td'); text(idCell, mode === 'library' ? fileSize(item.size) : (item.app_id || '—'));
    const actionCell = document.createElement('td'); const button = document.createElement('button'); button.className = 'button small'; button.dataset.operation = '1'; button.disabled = state.busy;
    text(button, mode === 'library' ? 'Установить ↗' : 'Скачать ↓');
    button.addEventListener('click', () => mode === 'library' ? chooseInstall(item) : chooseDownload(item));
    actionCell.append(button); row.append(nameCell, versionCell, idCell, actionCell); body.append(row);
  }
  table.append(body); container.append(table);
}

function fileSize(bytes) { if (!bytes) return '—'; return bytes >= 1024 ** 3 ? (bytes / 1024 ** 3).toFixed(2) + ' ГБ' : (bytes / 1024 ** 2).toFixed(1) + ' МБ'; }
function filter(items, term) { const q = term.trim().toLocaleLowerCase(); return items.filter(x => [x.name, x.bundle_id, x.app_id].join(' ').toLocaleLowerCase().includes(q)); }
function renderApps() {
  const items = filter(state.apps, $('#device-filter').value);
  if (items.length) appTable($('#device-list'), items, 'device');
  else if (state.apps.length) empty($('#device-list'), '⌕', 'Нет совпадений', 'Попробуй другое название.');
  else empty($('#device-list'), '▣', state.devices.length ? 'Телефон подключён' : 'Начни со старого iPhone', state.devices.length ? 'Выбери телефон и нажми «Получить приложения».' : 'Подключи iPhone к Mac кабелем. Здесь появятся установленные на нём приложения.');
}
function renderLibrary() {
  text($('#nav-library-count'), state.library.length); text($('#library-summary'), state.library.length + ' ' + (state.library.length % 100 >= 11 && state.library.length % 100 <= 14 ? 'приложений' : state.library.length % 10 === 1 ? 'приложение' : state.library.length % 10 >= 2 && state.library.length % 10 <= 4 ? 'приложения' : 'приложений'));
  const items = filter(state.library, $('#library-filter').value);
  if (items.length) appTable($('#library-list'), items, 'library');
  else empty($('#library-list'), '▤', state.library.length ? 'Нет совпадений' : 'Твой архив начинается здесь', state.library.length ? 'Попробуй другой запрос.' : 'Скачай приложение по ID или добавь уже сохранённый файл IPA.', state.library.length ? null : 'Добавить IPA', importIpa);
}
function renderPurchases() {
  if (state.purchases.length) appTable($('#purchase-list'), state.purchases, 'purchases');
  else empty($('#purchase-list'), '◎', 'Список пока пуст', 'После входа нажми «Получить список». Можно также загрузить приложение по его ID.');
  $('#pagination').hidden = state.purchaseMode !== 'purchases' || !state.total;
  text($('#page-label'), 'Страница ' + state.page + ' · Всего: ' + state.total);
  $('#prev-page').disabled = state.busy || state.page <= 1;
  $('#next-page').disabled = state.busy || state.page * 100 >= state.total;
}
function renderDevices() {
  text($('#nav-device-count'), state.devices.length);
  text($('#device-summary'), state.devices.length ? (state.devices.length === 1 ? state.devices[0].name : 'Подключено: ' + state.devices.length) : 'iPhone не найден');
  text($('#device-caption'), state.devices.length ? state.devices.map(d => d.ios ? 'iOS ' + d.ios : 'Подключён по USB').join(' · ') : 'Подключи телефон кабелем');
  for (const id of ['source-device', 'target-device']) {
    const select = $('#' + id), previous = select.value;
    select.replaceChildren();
    const placeholder = document.createElement('option'); placeholder.value = ''; text(placeholder, state.devices.length ? 'Выбери iPhone' : 'Нет подключённых устройств'); select.append(placeholder);
    for (const device of state.devices) {
      const option = document.createElement('option'); option.value = device.udid;
      text(option, device.name + (device.ios ? ' · iOS ' + device.ios : '') + ' · …' + device.udid.slice(-6)); select.append(option);
    }
    if (state.devices.some(x => x.udid === previous)) select.value = previous;
    else if (state.devices.length === 1) select.value = state.devices[0].udid;
  }
  renderApps();
}
function renderAccount() {
  const a = state.account;
  text($('#account-summary'), a ? (a.email || 'Вход выполнен') : 'Вход не проверен');
  text($('#account-caption'), a ? 'Готово к запросу приложений' : 'Нужна для загрузки IPA');
  text($('#account-name'), a ? (a.name || 'Учётная запись подключена') : 'Подключи свою учётную запись');
  text($('#account-description'), a ? a.email : 'Для загрузки приложений нужен вход и, при запросе Apple, код подтверждения.');
}

function chooseDownload(item = {}) {
  const owned = state.purchases.find(x => x.bundle_id === item.bundle_id);
  $('#download-id').value = item.app_id || owned?.app_id || '';
  $('#download-bundle').value = item.bundle_id || '';
  $('#download-version').value = '';
  $('#download-dialog').showModal();
}
$('#download-form').addEventListener('submit', event => {
  event.preventDefault();
  const appId = $('#download-id').value.trim(), bundle = $('#download-bundle').value.trim();
  if ((!appId || !/^\d+$/.test(appId)) && !bundle) return toast('Укажи числовой App Store ID или идентификатор приложения.');
  if (appId && !/^\d+$/.test(appId)) return toast('App Store ID должен содержать только цифры.');
  $('#download-dialog').close();
  action('download', {app_id: appId, bundle_id: bundle, version_id: $('#download-version').value.trim()});
});
$('#manual-download').addEventListener('click', () => chooseDownload());

function chooseInstall(item) {
  const device = state.devices.find(x => x.udid === $('#target-device').value);
  if (!device) return toast('Сначала подключи новый iPhone и выбери его над списком.');
  pendingInstall = {filename: item.filename, udid: device.udid};
  text($('#install-description'), item.name + ' · ' + item.version + '\nТелефон: ' + device.name + ' · …' + device.udid.slice(-6));
  $('#install-dialog').showModal();
}
$('#confirm-install').addEventListener('click', () => {
  $('#install-dialog').close();
  if (pendingInstall) { action('install', pendingInstall); pendingInstall = null; }
});

function importIpa() {
  if (state.busy) return toast('Дождись завершения текущей операции.');
  if (window.webkit?.messageHandlers?.native) window.webkit.messageHandlers.native.postMessage({action: 'import'});
  else $('#import-dialog').showModal();
}
window.nativeImport = function(path) { action('import', {path}); };
$('#import-ipa').addEventListener('click', importIpa);
$('#import-form').addEventListener('submit', event => { event.preventDefault(); $('#import-dialog').close(); action('import', {path: $('#import-path').value.trim()}); $('#import-path').value = ''; });
$('#show-library').addEventListener('click', () => {
  if (window.webkit?.messageHandlers?.native) window.webkit.messageHandlers.native.postMessage({action: 'library'});
  else toast('Папка Library находится рядом с PhoneBridge.app.');
});
$('#refresh-devices').addEventListener('click', () => action('devices'));
$('#read-apps').addEventListener('click', () => {
  const udid = $('#source-device').value;
  if (!udid) return toast('Подключи и выбери старый iPhone.');
  action('apps', {udid});
});
$('#source-device').addEventListener('change', () => { state.apps = []; renderApps(); });
$('#device-filter').addEventListener('input', renderApps);
$('#library-filter').addEventListener('input', renderLibrary);
$('#library-refresh').addEventListener('click', refreshLibrary);
$('#login').addEventListener('click', () => action('login'));
$('#check-account').addEventListener('click', () => action('account'));
$('#logout').addEventListener('click', () => action('logout'));
$('#setup').addEventListener('click', () => action('setup'));
$('#get-purchases').addEventListener('click', () => { state.page = 1; state.purchaseMode = 'purchases'; action('purchases', {page: 1}); });
$('#prev-page').addEventListener('click', () => action('purchases', {page: state.page - 1}));
$('#next-page').addEventListener('click', () => action('purchases', {page: state.page + 1}));
$('#store-search').addEventListener('submit', event => { event.preventDefault(); action('search', {term: $('#store-term').value.trim()}); });

$('#auth-form').addEventListener('submit', async event => {
  event.preventDefault();
  const input = $('#auth-value'), value = input.value;
  input.value = ''; $('#auth-dialog').close();
  try { await api('answer', {job_id: state.active, value}); } catch (error) { toast(error.message); }
});
async function cancelActive() { $('#auth-dialog').close(); $('#auth-value').value = ''; if (state.active) { try { await api('cancel', {job_id: state.active}); } catch (error) { toast(error.message); } } }
$('#auth-close').addEventListener('click', cancelActive);
$('#auth-dialog').addEventListener('cancel', event => { event.preventDefault(); cancelActive(); });
$('#cancel-job').addEventListener('click', cancelActive);

function renderJob(job) {
  const banner = $('#job-banner'); banner.hidden = !job;
  if (!job) return;
  banner.className = 'job-banner ' + job.status;
  const elapsed = Number(job.elapsed_seconds || 0);
  const clock = Math.floor(elapsed / 60) + ':' + String(elapsed % 60).padStart(2, '0');
  text($('#job-title'), job.title + (job.status === 'running' ? '… · ' + clock : job.status === 'done' ? ' — готово' : job.status === 'cancelled' ? ' — отменено' : ' — ошибка'));
  text($('#job-message'), job.error?.message || job.events.at(-1)?.text || 'Ожидание ответа…');
  $('#cancel-job').hidden = job.status !== 'running';
  $('#job-details').hidden = !job.error; text($('#job-error'), job.error?.details || '');
  if (job.prompt && job.prompt.id !== state.promptId) {
    state.promptId = job.prompt.id;
    $('#code-help').hidden = job.prompt.kind !== 'code';
    text($('#auth-label'), job.prompt.label);
    text($('#auth-hint'), job.prompt.kind === 'code' ? 'Разреши вход на доверенном устройстве и введи шестизначный код Apple.' : job.prompt.kind === 'keychain' ? 'Это пароль отдельного хранилища ipatool. Он отличается от пароля Apple.' : 'Ввод передаётся локальному ipatool. Он выполняет запрос к Apple.');
    const input = $('#auth-value'); input.value = '';
    input.type = ['password', 'keychain'].includes(job.prompt.kind) ? 'password' : 'text';
    input.inputMode = job.prompt.kind === 'code' ? 'numeric' : job.prompt.kind === 'email' ? 'email' : 'text';
    if (!$('#auth-dialog').open) $('#auth-dialog').showModal(); input.focus();
  }
  if (job.status !== 'running' && $('#auth-dialog').open) { $('#auth-dialog').close(); $('#auth-value').value = ''; }
}
function renderActivity() {
  const box = $('#activity-list'); box.replaceChildren();
  if (!state.jobs.length) return empty(box, '≋', 'Операций пока нет', 'Здесь появятся результаты подключения, загрузки и установки.');
  for (const job of [...state.jobs].reverse()) {
    const card = document.createElement('article'); card.className = 'activity';
    const heading = document.createElement('h3'); text(heading, job.title);
    const status = document.createElement('span'); status.className = 'state'; text(status, {running: 'Выполняется', done: 'Готово', error: 'Ошибка', cancelled: 'Отменено'}[job.status]); heading.append(status); card.append(heading);
    for (const event of job.events) { const p = document.createElement('p'), time = document.createElement('time'); text(time, event.time); p.append(time, document.createTextNode(event.text)); card.append(p); }
    if (job.error) { const p = document.createElement('p'); text(p, job.error.message); card.append(p); }
    box.append(card);
  }
}
async function refreshLibrary() { try { state.library = (await api('library')).files; renderLibrary(); } catch (error) { toast(error.message); } }
async function completed(job) {
  if (job.status !== 'done') return;
  const result = job.result;
  switch (job.action) {
    case 'devices': state.devices = result; renderDevices(); break;
    case 'apps': state.apps = result; renderApps(); break;
    case 'login': case 'account': state.account = result; renderAccount(); break;
    case 'logout': state.account = null; state.purchases = []; state.total = 0; renderAccount(); renderPurchases(); break;
    case 'purchases': state.purchases = (result.apps || []).map(normalize); state.page = result.page || 1; state.total = result.totalCount || result.count || 0; state.purchaseMode = 'purchases'; renderPurchases(); break;
    case 'search': state.purchases = (result.apps || []).map(normalize); state.purchaseMode = 'search'; renderPurchases(); break;
    case 'download': case 'import': await refreshLibrary(); toast('IPA добавлен в библиотеку.'); break;
    case 'install': toast('Установка завершена. Проверь запуск приложения на iPhone.'); break;
    case 'setup': toast('Компоненты подключения готовы.'); break;
  }
}
function normalize(app) { return {name: app.name || app.trackName, bundle_id: app.bundleID || app.bundleId, app_id: String(app.id || app.trackId || ''), version: app.version}; }

let polling = false;
async function poll() {
  if (polling) return;
  polling = true;
  try {
    const status = await api('status'); state.jobs = status.jobs; state.ready = status.device_ready;
    text($('#setup-state'), state.ready ? 'Подключение подготовлено.' : 'Компоненты пока не подготовлены.');
    const active = state.jobs.find(x => x.status === 'running');
    state.active = active?.id || state.active;
    setBusy(Boolean(active));
    const latest = active || state.jobs.at(-1);
    renderJob(latest);
    for (const job of state.jobs) if (job.status !== 'running' && !state.seen.has(job.id)) { state.seen.add(job.id); await completed(job); }
    renderActivity();
  } catch (error) { if (!state.jobs.length) toast(error.message); }
  finally { polling = false; }
}
async function boot() {
  renderDevices(); renderLibrary(); renderPurchases(); renderAccount(); renderActivity();
  if (!token) return toast('Запусти PhoneBridge.app или открой адрес из команды запуска.');
  await poll(); await refreshLibrary();
  if (state.ready && !state.busy) await action('devices');
  setInterval(poll, 1000);
  setInterval(() => api('heartbeat', {alive: true}).catch(() => {}), 15000);
}
boot();
