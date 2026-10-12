const app = {
  state: null,
  selectedPath: null,
  filter: 'all',
  sortMode: getStorage('comfyshelf_workflow_sort_mode', 'manual'),
  isDraggingWorkflow: false,
  draggedWorkflowPath: null,
  pendingConfirm: null,
  toastTimer: null,
  loadingTimer: null,
  requestedPath: null,
  scanProgress: null,
  diskRefreshPending: false,
  language: 'en',
  messages: {},
  historyEntries: null,
  bootstrapStarted: false,
  bootstrapCompleted: false,
  bootstrapPoll: null,
  bootstrapAttempts: 0,
  bootstrapRetryAfter: 0,
};

let currentTourStep = 0;

const $ = (id) => document.getElementById(id);
const icon = (name) => `<svg><use href="#i-${name}"/></svg>`;

function t(key, variables = {}) {
  let value = app.messages[key] ?? key;
  for (const [name, replacement] of Object.entries(variables)) value = value.replaceAll(`{${name}}`, String(replacement));
  return value;
}

function getStorage(key, fallback = null) {
  try {
    if (typeof localStorage !== 'undefined') return localStorage.getItem(key) ?? fallback;
  } catch (_) {}
  return fallback;
}

function setStorage(key, value) {
  try {
    if (typeof localStorage !== 'undefined') localStorage.setItem(key, value);
  } catch (_) {}
}

function applyTranslations() {
  if (document.documentElement) document.documentElement.lang = app.language;
  document.querySelectorAll?.('[data-i18n]')?.forEach(node => { node.textContent = t(node.dataset.i18n); });
  document.querySelectorAll?.('[data-i18n-html]')?.forEach(node => { node.innerHTML = t(node.dataset.i18nHtml); });
  document.querySelectorAll?.('[data-i18n-title]')?.forEach(node => { node.title = t(node.dataset.i18nTitle); });
  document.querySelectorAll?.('[data-i18n-aria]')?.forEach(node => { node.setAttribute('aria-label', t(node.dataset.i18nAria)); });
  document.querySelectorAll?.('[data-i18n-placeholder]')?.forEach(node => { node.placeholder = t(node.dataset.i18nPlaceholder); });
  document.querySelectorAll?.('.language-toggle button')?.forEach(button => button.classList.toggle('active', button.dataset.language === app.language));
  try {
    const maxBtn = typeof $ === 'function' ? $('maximizeButton') : null;
    if (maxBtn && typeof setMaximizedButton === 'function') setMaximizedButton(maxBtn.getAttribute('aria-pressed') === 'true');
  } catch (_) {}
  try {
    if (app.state && typeof applyState === 'function') applyState(app.state, app.selectedPath);
  } catch (_) {}
  try {
    if (app.historyEntries && typeof renderHistoryEntries === 'function') renderHistoryEntries(app.historyEntries);
  } catch (_) {}
  try {
    const tourModal = typeof $ === 'function' ? $('tourModal') : null;
    if (tourModal && !tourModal.classList.contains('hidden') && typeof renderTourStep === 'function') {
      renderTourStep(currentTourStep);
    }
  } catch (_) {}
  try {
    if (typeof updateSortDropdownUI === 'function') updateSortDropdownUI();
  } catch (_) {}
}

function installLocale(payload) {
  app.language = payload?.language === 'de' ? 'de' : 'en';
  app.messages = payload?.messages || {};
  applyTranslations();
}

async function changeLanguage(language) {
  if (language === app.language) return;
  try {
    let payload;
    if (window.pywebview?.api) payload = await callApi('set_language', language);
    else payload = {language, messages: await (await fetch(`locales/${language}.json`)).json()};
    installLocale(payload);
  } catch (error) { toast(error.message, true); }
}

function formatBytes(bytes) {
  if (bytes === null || bytes === undefined) return '—';
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  let value = Number(bytes), unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
  return `${value.toFixed(value >= 10 || unit === 0 ? 1 : 2)} ${units[unit]}`;
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
}

function searchableFilename(value) {
  return String(value ?? '').split(/[\\/]/).pop();
}

function cleanCivitaiQuery(value) {
  return searchableFilename(value).replace(/\.(safetensors|ckpt|pt|pth|bin|onnx)$/i, '').trim();
}

function getWorkflowKey(itemOrPath) {
  if (!itemOrPath) return '';
  const path = typeof itemOrPath === 'string' ? itemOrPath : (itemOrPath.path || '');
  return searchableFilename(path);
}

function getCustomWorkflowOrder() {
  try {
    const raw = getStorage('comfyshelf_workflow_order', '[]');
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch (_) {
    return [];
  }
}

function setCustomWorkflowOrder(orderList) {
  try {
    setStorage('comfyshelf_workflow_order', JSON.stringify(orderList));
  } catch (_) {}
}

function renameCustomWorkflowKey(oldPath, newPath) {
  const oldKey = getWorkflowKey(oldPath);
  const newKey = getWorkflowKey(newPath);
  if (!oldKey || !newKey || oldKey === newKey) return;
  const list = getCustomWorkflowOrder();
  const idx = list.indexOf(oldKey);
  if (idx !== -1) {
    list[idx] = newKey;
    setCustomWorkflowOrder(list);
  }
}

function removeCustomWorkflowKey(path) {
  const key = getWorkflowKey(path);
  if (!key) return;
  const list = getCustomWorkflowOrder();
  const next = list.filter(k => k !== key);
  if (next.length !== list.length) {
    setCustomWorkflowOrder(next);
  }
}

function getSortedPackages(packages = []) {
  const list = [...packages];
  const mode = app.sortMode || 'manual';
  if (mode === 'name') {
    const lang = app.language === 'de' ? 'de' : 'en';
    return list.sort((a, b) => (a.name || '').localeCompare(b.name || '', lang, { numeric: true, sensitivity: 'base' }));
  }
  if (mode === 'recent') {
    return list.sort((a, b) => ((b.modified || 0) - (a.modified || 0)) || (a.name || '').localeCompare(b.name || ''));
  }
  // 'manual'
  const order = getCustomWorkflowOrder();
  return list.sort((a, b) => {
    const keyA = searchableFilename(a.path);
    const keyB = searchableFilename(b.path);
    const indexA = order.indexOf(keyA);
    const indexB = order.indexOf(keyB);
    if (indexA !== -1 && indexB !== -1) return indexA - indexB;
    if (indexA !== -1 && indexB === -1) return 1;
    if (indexA === -1 && indexB !== -1) return -1;
    return ((b.modified || 0) - (a.modified || 0)) || (a.name || '').localeCompare(b.name || '');
  });
}

function moveWorkflowInOrder(draggedPath, targetPath, position = 'before') {
  const draggedKey = searchableFilename(draggedPath);
  const targetKey = searchableFilename(targetPath);
  if (!draggedKey || !targetKey || draggedKey === targetKey) return;

  const currentSorted = getSortedPackages(app.state?.packages || []);
  let orderedKeys = currentSorted.map(p => searchableFilename(p.path));

  orderedKeys = orderedKeys.filter(k => k !== draggedKey);
  const targetIndex = orderedKeys.indexOf(targetKey);
  if (targetIndex === -1) return;

  const insertIndex = position === 'after' ? targetIndex + 1 : targetIndex;
  orderedKeys.splice(insertIndex, 0, draggedKey);

  setCustomWorkflowOrder(orderedKeys);

  if (app.sortMode !== 'manual') {
    app.sortMode = 'manual';
    setStorage('comfyshelf_workflow_sort_mode', 'manual');
    updateSortDropdownUI();
    toast(t('sort.switched_manual'));
  }

  renderLibrary();
}

function updateSortDropdownUI() {
  const mode = app.sortMode || 'manual';
  const label = $('workflowSortLabel');
  if (label) {
    const key = `sort.${mode}_short`;
    label.setAttribute('data-i18n', key);
    label.textContent = t(key);
  }
  document.querySelectorAll?.('.sort-option')?.forEach(option => {
    const isSelected = option.dataset.sort === mode;
    option.classList.toggle('selected', isSelected);
  });
}

function toggleSortDropdown(show) {
  const dropdown = $('workflowSortDropdown');
  if (!dropdown) return;
  const shouldOpen = show !== undefined ? Boolean(show) : dropdown.classList.contains('hidden');
  dropdown.classList.toggle('hidden', !shouldOpen);
}

function closeSortDropdown() {
  const dropdown = $('workflowSortDropdown');
  if (dropdown && !dropdown.classList.contains('hidden')) {
    dropdown.classList.add('hidden');
  }
}

function missingFileHtml(item, compact = false) {
  const name = String(item.name || item.filename || '');
  const filename = searchableFilename(name);
  const label = t('missing.copy_name', {name: filename});
  const downloadLabel = t('missing.download_model', {name: filename});
  const searchUrl = item.model_card_url || item.url || `https://huggingface.co/models?search=${encodeURIComponent(filename)}`;
  const searchLabel = t('missing.open_hf_search', {name: filename});
  const civitaiQuery = cleanCivitaiQuery(filename);
  const civitaiUrl = `https://civitai.com/models?query=${encodeURIComponent(civitaiQuery)}`;
  const civitaiLabel = t('missing.open_civitai_search', {name: civitaiQuery});
  const googleQuery = `${filename} huggingface`;
  const googleUrl = `https://www.google.com/search?q=${encodeURIComponent(googleQuery)}`;
  const googleLabel = t('missing.open_google_search', {name: filename});
  return `<div class="${compact ? 'notice-missing-item' : 'missing-item'} missing-file-row">
    ${compact ? '' : icon('alert')}
    <span class="missing-file-copy"><strong title="${escapeHtml(name)}">${escapeHtml(name)}</strong>
      <small>${escapeHtml(String(item.kind || item.type || t('notice.dependency')).replaceAll('_', ' '))}</small></span>
    <span class="missing-file-actions">
      ${filename ? `<button type="button" class="filename-web-button" data-open-url="${escapeHtml(searchUrl)}" title="${escapeHtml(searchLabel)}" aria-label="${escapeHtml(searchLabel)}">${icon('external')}</button>` : ''}
      ${filename ? `<button type="button" class="filename-civitai-button" data-open-url="${escapeHtml(civitaiUrl)}" title="${escapeHtml(civitaiLabel)}" aria-label="${escapeHtml(civitaiLabel)}"><span class="civitai-icon">C</span></button>` : ''}
      ${filename ? `<button type="button" class="filename-google-button" data-open-url="${escapeHtml(googleUrl)}" title="${escapeHtml(googleLabel)}" aria-label="${escapeHtml(googleLabel)}"><span class="google-icon">G</span></button>` : ''}
      ${filename ? `<button type="button" class="filename-download-button" data-download-filename="${escapeHtml(name)}" data-download-kind="${escapeHtml(item.kind || '')}" title="${escapeHtml(downloadLabel)}" aria-label="${escapeHtml(downloadLabel)}">${icon('download')}</button>` : ''}
      ${filename ? `<button type="button" class="filename-copy-button" data-copy-filename="${escapeHtml(filename)}" title="${escapeHtml(label)}" aria-label="${escapeHtml(label)}">${icon('copy')}</button>` : ''}
    </span>
  </div>`;
}

async function writeClipboardText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return;
  } catch (_) { /* Some embedded browsers require the legacy clipboard path. */ }
  const previousFocus = document.activeElement;
  const field = document.createElement('textarea');
  field.value = text;
  field.readOnly = true;
  field.style.cssText = 'position:fixed;left:-9999px;top:0;opacity:0;';
  document.body.appendChild(field);
  try {
    field.focus({preventScroll: true});
    field.select();
    if (!document.execCommand('copy')) throw new Error('Clipboard write failed.');
  } finally {
    field.remove();
    previousFocus?.focus({preventScroll: true});
  }
}

async function copyMissingFilename(event) {
  const button = event.target.closest?.('[data-copy-filename]');
  if (!button || button.disabled) return;
  event.preventDefault();
  event.stopPropagation();
  button.disabled = true;
  try {
    await writeClipboardText(button.dataset.copyFilename);
    toast(t('missing.name_copied', {name: button.dataset.copyFilename}));
  } catch (_) {
    toast(t('missing.copy_failed'), true);
  } finally {
    button.disabled = false;
  }
}

async function copyCustomNodePackage(event) {
  const button = event.target.closest?.('[data-copy-custom-node]');
  if (!button || button.disabled) return;
  event.preventDefault();
  event.stopPropagation();
  const pkgName = button.dataset.copyCustomNode;
  button.disabled = true;
  try {
    await writeClipboardText(pkgName);
    toast(t('toast.custom_node_copied', {name: pkgName}));
  } catch (_) {
    toast(t('missing.copy_failed'), true);
  } finally {
    button.disabled = false;
  }
}

async function openExternalUrlAction(event) {
  const button = event.target.closest?.('[data-open-url]');
  if (!button) return;
  event.preventDefault();
  event.stopPropagation();
  const url = button.dataset.openUrl;
  if (!url) return;
  try {
    await callApi('open_external_url', url);
  } catch (error) {
    toast(error.message, true);
  }
}

let activeDownloadContext = null;

function handleDownloadModelClick(event) {
  const button = event.target.closest?.('[data-download-filename]');
  if (!button) return;
  event.preventDefault();
  event.stopPropagation();
  const filename = button.dataset.downloadFilename;
  const kind = button.dataset.downloadKind || '';
  const archivePath = app.selectedPath || '';
  openDownloadModal(filename, kind, archivePath);
}

function defaultModelFolder(kind) {
  const map = {
    checkpoint: 'checkpoints',
    lora: 'loras',
    vae: 'vae',
    diffusion_model: 'diffusion_models',
    text_encoder: 'text_encoders',
    controlnet: 'controlnet',
    clip_vision: 'clip_vision',
    upscale_model: 'upscale_models',
    style_model: 'style_models',
  };
  return map[String(kind || '').toLowerCase()] || 'checkpoints';
}

async function openDownloadModal(filename, kind = '', archivePath = '') {
  activeDownloadContext = {
    filename,
    kind,
    archivePath: archivePath || app.selectedPath || '',
    expectedHash: '',
  };
  const searchUrl = `https://huggingface.co/models?search=${encodeURIComponent(searchableFilename(filename))}`;
  const civitaiUrl = `https://civitai.com/models?query=${encodeURIComponent(cleanCivitaiQuery(filename))}`;
  const googleUrl = `https://www.google.com/search?q=${encodeURIComponent(searchableFilename(filename) + ' huggingface')}`;
  $('downloadModelName').textContent = filename;
  $('downloadTargetPath').textContent = `models/${defaultModelFolder(kind)}/${searchableFilename(filename)}`;
  $('downloadSourceInput').value = '';
  $('downloadStartButton').disabled = true;
  $('downloadCandidatesBox').classList.add('hidden');
  $('downloadCandidatesList').innerHTML = '';
  $('downloadCandidatesFallback')?.classList.add('hidden');
  if ($('downloadOpenWebBtn')) {
    $('downloadOpenWebBtn').dataset.openUrl = searchUrl;
    $('downloadOpenWebText').textContent = t('download.open_hf_search');
  }
  if ($('downloadSearchHfBtn')) {
    $('downloadSearchHfBtn').dataset.openUrl = searchUrl;
  }
  if ($('downloadCivitaiBtn')) {
    $('downloadCivitaiBtn').dataset.openUrl = civitaiUrl;
  }
  if ($('downloadFallbackCivitaiBtn')) {
    $('downloadFallbackCivitaiBtn').dataset.openUrl = civitaiUrl;
  }
  if ($('downloadGoogleBtn')) {
    $('downloadGoogleBtn').dataset.openUrl = googleUrl;
  }
  if ($('downloadFallbackGoogleBtn')) {
    $('downloadFallbackGoogleBtn').dataset.openUrl = googleUrl;
  }
  $('downloadModal').classList.remove('hidden');

  try {
    const targetInfo = await callApi('get_model_target_path', filename, kind);
    if (targetInfo?.relative_path) {
      $('downloadTargetPath').textContent = targetInfo.relative_path;
    }
  } catch (_) {}

  $('downloadCandidatesHeader').textContent = t('download.searching_hf');
  $('downloadCandidatesBox').classList.remove('hidden');
  try {
    const candidates = await callApi('search_hf_model', filename);
    if (activeDownloadContext?.filename !== filename) return;
    if (Array.isArray(candidates) && candidates.length > 0) {
      $('downloadCandidatesHeader').textContent = t('download.hf_candidates_header');
      $('downloadCandidatesList').innerHTML = candidates.map((cand, idx) => `
        <div class="download-candidate-pill ${idx === 0 ? 'selected' : ''}" data-candidate-url="${escapeHtml(cand.download_url)}" data-candidate-hash="${escapeHtml(cand.sha256 || '')}" data-candidate-repo="${escapeHtml(cand.repo_id)}">
          <div class="candidate-info" style="cursor: pointer; flex: 1;">
            <strong>${escapeHtml(cand.repo_id)}</strong>
            <span>${formatBytes(cand.size)}${cand.sha256 ? ' · SHA-256' : ''}</span>
          </div>
          <div class="candidate-actions">
            <button type="button" class="candidate-ext-btn" data-open-url="https://huggingface.co/${escapeHtml(cand.repo_id)}" title="${escapeHtml(t('download.view_model_card'))}" aria-label="${escapeHtml(t('download.view_model_card'))}">
              ${icon('external')}
            </button>
          </div>
        </div>
      `).join('');
      const top = candidates[0];
      $('downloadSourceInput').value = top.download_url || top.repo_id;
      activeDownloadContext.expectedHash = top.sha256 || '';
      $('downloadStartButton').disabled = false;
      if ($('downloadOpenWebBtn')) {
        $('downloadOpenWebBtn').dataset.openUrl = `https://huggingface.co/${top.repo_id}`;
        $('downloadOpenWebText').textContent = t('download.view_model_card');
      }
      $('downloadCandidatesFallback')?.classList.add('hidden');
    } else {
      $('downloadCandidatesHeader').textContent = t('download.no_hf_candidates');
      $('downloadCandidatesList').innerHTML = '';
      $('downloadCandidatesFallback')?.classList.remove('hidden');
    }
  } catch (_) {
    $('downloadCandidatesHeader').textContent = t('download.no_hf_candidates');
    $('downloadCandidatesFallback')?.classList.remove('hidden');
  }
}

function closeDownloadModal() {
  $('downloadModal').classList.add('hidden');
  activeDownloadContext = null;
}

async function startModelDownload() {
  if (!activeDownloadContext) return;
  const source = $('downloadSourceInput').value.trim();
  if (!source) return;
  const { filename, kind, archivePath, expectedHash } = activeDownloadContext;
  const storeInPool = $('downloadStoreInPoolCheckbox').checked;
  closeDownloadModal();

  showBusy(t('download.busy_title'), t('download.busy_detail', {file: filename}));
  try {
    const result = await callApi('download_model', source, filename, kind, archivePath, expectedHash, storeInPool);
    hideBusy();
    toast(t('download.success', {name: filename}));
    await refreshState();
  } catch (error) {
    hideBusy();
    operationError(error);
  }
}

async function callApi(method, ...args) {
  if (!window.pywebview?.api?.[method]) throw new Error(app.messages['errors.unexpected'] || 'The native app interface is not ready yet.');
  const result = await window.pywebview.api[method](...args);
  if (!result?.ok) {
    const error = new Error(app.messages[`errors.${result?.error_code}`] || result?.error || app.messages['common.unknown'] || 'Unknown error');
    error.cancelled = !!result?.cancelled;
    error.code = result?.error_code;
    error.details = result?.details;
    throw error;
  }
  return result.data;
}

function nativeApiReady() {
  return Boolean(
    window.pywebview?.api?.get_locale
    && window.pywebview?.api?.get_state
    && window.pywebview?.api?.report_startup_stage
  );
}

async function reportStartupStage(stage, detail = '') {
  try { await callApi('report_startup_stage', stage, detail); }
  catch (_) { /* Startup diagnostics must never block the interface. */ }
}

async function bootstrapNativeApp(trigger) {
  if (app.bootstrapStarted || !nativeApiReady() || Date.now() < app.bootstrapRetryAfter) return false;
  app.bootstrapStarted = true;
  app.bootstrapAttempts += 1;
  await reportStartupStage('bridge-ready', trigger);
  await reportStartupStage('bootstrap-started', trigger);
  try {
    installLocale(await callApi('get_locale'));
    const state = await callApi('get_state');
    applyState(state, app.selectedPath);
    app.bootstrapCompleted = true;
    clearInterval(app.bootstrapPoll);
    await reportStartupStage('frontend-ready', trigger);
    if (getStorage('comfyshelf_tour_completed') !== 'true') {
      setTimeout(() => openTour(), 500);
    }
    return true;
  } catch (error) {
    await reportStartupStage('bootstrap-failed', error?.message || 'Unknown startup error');
    if (app.bootstrapAttempts < 3) {
      app.bootstrapStarted = false;
      app.bootstrapRetryAfter = Date.now() + 1000;
    } else {
      clearInterval(app.bootstrapPoll);
      toast(error.message, true);
    }
    return false;
  }
}

function beginNativeBootstrap() {
  const attempt = trigger => {
    if (nativeApiReady()) void bootstrapNativeApp(trigger);
  };
  window.addEventListener('pywebviewready', () => attempt('pywebviewready'));
  queueMicrotask(() => attempt('immediate-api-check'));
  app.bootstrapPoll = window.setInterval(() => {
    if (app.bootstrapCompleted) clearInterval(app.bootstrapPoll);
    else attempt('bridge-poll');
  }, 250);
  window.setTimeout(() => clearInterval(app.bootstrapPoll), 12000);
}

function showBusy(title, detail = t('progress.default_detail'), cancellable = true) {
  $('busyTitle').textContent = title;
  $('busyDetail').textContent = detail;
  $('progressPhase').textContent = t('progress.preparing');
  $('progressPercent').textContent = '—';
  $('progressTrack').classList.add('indeterminate');
  $('progressFill').style.width = '0%';
  $('progressFile').textContent = t('progress.preparing_package');
  $('progressAmount').textContent = '';
  $('processLog').innerHTML = '';
  $('cancelOperationButton').disabled = !cancellable;
  $('cancelOperationButton').textContent = cancellable ? t('progress.cancel') : t('progress.wait');
  appendProcessLog(title, 'info');
  $('busyOverlay').classList.remove('hidden');
}
function hideBusy() { $('busyOverlay').classList.add('hidden'); }

function appendProcessLog(message, level = 'info') {
  if (!message) return;
  const log = $('processLog');
  const previous = log.lastElementChild?.querySelector('.log-message')?.textContent;
  if (previous === message) return;
  const row = document.createElement('div');
  row.className = `process-log-line ${level}`;
  const timestamp = new Date();
  const now = [timestamp.getHours(), timestamp.getMinutes(), timestamp.getSeconds()]
    .map(value => String(value).padStart(2, '0'))
    .join(':');
  row.innerHTML = `<span class="log-time">${now}</span><span class="log-message">${escapeHtml(message)}</span>`;
  log.appendChild(row);
  while (log.children.length > 30) log.firstElementChild.remove();
  log.scrollTop = log.scrollHeight;
}

window.workflowProgress = function(event) {
  if (!event) return;
  const phaseKey = `phase.${event.phase || 'processing'}`;
  const phaseText = t(phaseKey);
  $('progressPhase').textContent = phaseText === phaseKey ? (event.phase || t('phase.processing')) : phaseText;
  $('progressFile').textContent = event.file || $('progressPhase').textContent;
  const current = Number(event.current);
  const total = Number(event.total);
  if (Number.isFinite(current) && Number.isFinite(total) && total > 0) {
    const percent = Math.max(0, Math.min(100, (current / total) * 100));
    $('progressTrack').classList.remove('indeterminate');
    $('progressFill').style.width = `${percent}%`;
    $('progressPercent').textContent = `${percent.toFixed(percent >= 10 ? 0 : 1)} %`;
    $('progressAmount').textContent = `${formatBytes(current)} / ${formatBytes(total)}`;
  } else {
    $('progressTrack').classList.add('indeterminate');
    $('progressPercent').textContent = '—';
    $('progressAmount').textContent = '';
  }
  if (event.log) appendProcessLog(event.file ? `${$('progressPhase').textContent}: ${event.file}` : $('progressPhase').textContent, event.level || 'info');
  if (event.cancellable === false) {
    $('cancelOperationButton').disabled = true;
    $('cancelOperationButton').textContent = t('progress.safe_finish');
  }
};

function toast(message, error = false) {
  const el = $('toast');
  $('toastText').textContent = message;
  el.classList.toggle('error', error);
  el.querySelector('use').setAttribute('href', error ? '#i-alert' : '#i-check');
  el.classList.remove('hidden');
  clearTimeout(app.toastTimer);
  app.toastTimer = setTimeout(() => el.classList.add('hidden'), 4200);
}

function operationError(error) {
  if (error.code === 'removal_incomplete' && error.details) {
    const details = error.details;
    const removed = details.removed || [];
    const remaining = details.remaining || [];
    const pathList = (label, paths) => paths.length ? `<div class="missing-detail diagnostic-detail"><span class="missing-detail-label">${t(label)}</span>${paths.map(path => `<div class="missing-item">${icon(label === 'removal.removed' ? 'check' : 'drive')}<span><strong>${escapeHtml(path)}</strong></span></div>`).join('')}</div>` : '';
    openConfirm({
      eyebrow: t('removal.eyebrow'), title: t(removed.length ? 'removal.partial_title' : 'removal.blocked_title'),
      copy: `<p>${t('removal.backup_safe')}</p>${details.restore && !details.restore.dry_run ? `<p>${t('removal.target_loaded')}</p>` : ''}
        <div class="confirm-stat"><span>${t('removal.removed')}</span><strong>${removed.length}</strong></div>
        <div class="confirm-stat"><span>${t('removal.remaining')}</span><strong>${remaining.length}</strong></div>
        ${removalIssuesHtml(details.removal_issues)}${pathList('removal.removed', removed)}${pathList('removal.remaining', remaining)}
        <p>${t('removal.retry_hint')}</p>`,
      wide: true, danger: true, readOnly: true, proceedLabel: t('common.close'), action: async () => {},
    });
    if (window.pywebview?.api) void refreshState();
    return;
  }
  toast(error.cancelled ? t('progress.cancelled') : error.message, !error.cancelled);
}

function removalIssuesHtml(issues = []) {
  if (!issues.length) return '';
  return `<div class="missing-detail diagnostic-detail"><span class="missing-detail-label">${t('removal.issues')}</span>${issues.map(issue => `
    <div class="missing-item">${icon('alert')}<span><strong>${escapeHtml(issue.path)}</strong>
      <small>${escapeHtml(app.messages[`removal.${issue.code}`] || t('removal.filesystem_error'))}</small></span></div>`).join('')}</div>`;
}

function parserWarningText(warning) {
  return app.messages[`parser.${warning.code}`] || t('parser.review');
}

function groupParserWarnings(warnings = []) {
  const groups = new Map();
  const normalizePath = value => String(value || '').replaceAll('\\', '/').toLowerCase();
  for (const warning of warnings) {
    // Keep full paths: equal basenames in different folders are not the same
    // dependency. Unresolved loaders without a filename stay separate by node.
    const key = JSON.stringify(warning.filename
      ? ['file', normalizePath(warning.filename), normalizePath(warning.resolved_path)]
      : ['node', warning.node_type || '', String(warning.node_id ?? ''), warning.field || '']);
    if (!groups.has(key)) groups.set(key, {filename: warning.filename || warning.node_type || '', warnings: []});
    groups.get(key).warnings.push(warning);
  }
  return [...groups.values()];
}

function parserNoticesHtml(warnings = []) {
  return groupParserWarnings(warnings).map(group => {
    const messages = [...new Set(group.warnings.map(parserWarningText))];
    const sources = [...new Set(group.warnings.map(warning =>
      [warning.node_type, warning.node_id != null ? `#${warning.node_id}` : '', warning.field].filter(Boolean).join(' ')
    ).filter(Boolean))];
    const paths = [...new Set(group.warnings.map(warning => warning.resolved_path).filter(Boolean))];
    return `<div class="notice warning parser-notice">
      ${icon('alert')}
      <div class="notice-body">
        <strong class="parser-notice-filename">${escapeHtml(group.filename)}</strong>
        <ul class="parser-notice-messages">${messages.map(message => `<li>${escapeHtml(message)}</li>`).join('')}</ul>
        ${sources.length || paths.length ? `<div class="parser-notice-context">
          ${sources.map(source => `<small>${escapeHtml(source)}</small>`).join('')}
          ${paths.map(path => `<small>${escapeHtml(path)}</small>`).join('')}
        </div>` : ''}
      </div>
    </div>`;
  }).join('');
}

function parserWarningsHtml(warnings = []) {
  if (!warnings.length) return '';
  return `<div class="missing-detail diagnostic-detail"><span class="missing-detail-label">${t('parser.title')}</span>${warnings.map(warning => `
    <div class="missing-item">${icon('alert')}<span><strong>${escapeHtml(warning.filename || warning.node_type)}</strong>
      <small>${escapeHtml(parserWarningText(warning))}</small>
      <small>${escapeHtml(`${warning.node_type} #${warning.node_id} ${warning.field || ''}`)}</small>
      ${warning.resolved_path ? `<small>${escapeHtml(warning.resolved_path)}</small>` : ''}</span></div>`).join('')}</div>`;
}

async function refreshState(selectPath = app.selectedPath) {
  app.requestedPath = selectPath;
  try {
    const state = await callApi('get_state');
    applyState(state, selectPath);
    return !state.loading;
  } catch (error) {
    toast(error.message, true);
    return false;
  }
}

function setScanActive(active) {
  ['refreshButton', 'diskRefreshButton'].forEach(id => {
    $(id).classList.toggle('refreshing', active);
    $(id).disabled = active;
  });
}

function renderLoading() {
  $('emptyState').classList.add('hidden');
  $('detailView').classList.add('hidden');
  $('loadingState').classList.remove('hidden');
  const progress = app.scanProgress;
  $('loadingMessage').textContent = progress ? scanProgressText(progress) : t('scan.configuration');
  $('loadingPackage').textContent = progress?.package || t('loading.responsive');
  clearTimeout(app.loadingTimer);
  app.loadingTimer = setTimeout(() => {
    if (app.state?.loading) {
      $('loadingPackage').textContent = app.scanProgress?.package || t('loading.slow');
    }
  }, 4000);
}

function hideLoading() {
  clearTimeout(app.loadingTimer);
  $('loadingState').classList.add('hidden');
}

function applyState(state, selectPath = app.selectedPath) {
  app.state = state;
  setScanActive(!!state.loading);
  if (!state.configured) {
    hideLoading();
    renderUnconfigured();
    openSettings(true);
    return;
  }
  renderSummary();
  const packages = state.packages || [];
  const desired = packages.find(item => item.path === selectPath) || packages[0];
  app.selectedPath = desired?.path || null;
  renderLibrary();
  if (state.loading && !packages.length) renderLoading();
  else {
    hideLoading();
    renderDetail(desired);
  }
  if (state.scan_error) toast(t('scan.failed'), true);
}

function scanProgressText(progress) {
  return t(`scan.${progress?.stage || 'default'}`, {current: progress?.current || 0, total: progress?.total || 0});
}

window.workflowScanProgress = function(progress) {
  app.scanProgress = progress || null;
  if (!$('loadingState').classList.contains('hidden')) {
    $('loadingMessage').textContent = scanProgressText(progress);
    $('loadingPackage').textContent = progress?.package || t('scan.drive_access');
  }
};

window.workflowStateUpdated = function(state) {
  app.scanProgress = null;
  applyState(state, app.requestedPath || app.selectedPath);
  if (app.diskRefreshPending && !state.loading) {
    app.diskRefreshPending = false;
    toast(t('toast.storage_refreshed'));
  }
};

function renderUnconfigured() {
  $('installationName').textContent = t('summary.not_configured');
  $('comfyPath').textContent = t('summary.choose_comfy');
  $('freeSpace').textContent = '—';
  if ($('archiveFreeSpace')) $('archiveFreeSpace').textContent = '—';
  $('workflowList').innerHTML = `<div class="workflow-list-empty">${t('settings.title')}</div>`;
  $('loadingState').classList.add('hidden');
  $('detailView').classList.add('hidden');
  $('emptyState').classList.remove('hidden');
}

function renderSummary() {
  const { installation, validation } = app.state;
  $('installationName').textContent = installation.name;
  $('comfyPath').textContent = installation.comfy_root;
  $('freeSpace').textContent = formatBytes(validation?.comfy_free);
  if ($('archiveFreeSpace')) {
    $('archiveFreeSpace').textContent = formatBytes(validation?.archive_free);
  }
}

const thumbnailCache = new Map();

function renderItemThumbHtml(item) {
  if (thumbnailCache.has(item.path)) {
    return `<img src="${escapeHtml(thumbnailCache.get(item.path))}" alt="">`;
  }
  if (item.has_thumbnail) {
    return `<span class="thumb-pending" data-thumb-path="${escapeHtml(item.path)}">${icon('image')}</span>`;
  }
  return `<span class="thumb-empty">${icon('workflow')}</span>`;
}

async function loadVisibleThumbnails() {
  const elements = document.querySelectorAll?.('[data-thumb-path]') || [];
  for (const el of elements) {
    const path = el.dataset?.thumbPath;
    if (!path || thumbnailCache.has(path)) continue;
    try {
      const data = await callApi('get_archive_thumbnail', path);
      if (data) {
        thumbnailCache.set(path, data);
        const current = document.querySelector?.(`[data-thumb-path="${escapeHtml(path)}"]`);
        if (current) {
          current.outerHTML = `<img src="${escapeHtml(data)}" alt="">`;
        }
      }
    } catch (_) {}
  }
}

function renderDetailThumbnail(pkg) {
  const cover = $('heroCover');
  const img = $('heroCoverImg');
  if (!cover || !img) return Promise.resolve();
  if (!pkg || !pkg.has_thumbnail) {
    cover.classList.add('hidden');
    img.src = '';
    return Promise.resolve();
  }
  const cached = thumbnailCache.get(pkg.path);
  if (cached) {
    img.src = cached;
    cover.classList.remove('hidden');
    return Promise.resolve();
  }
  cover.classList.add('hidden');
  return callApi('get_archive_thumbnail', pkg.path).then(data => {
    if (data && app.selectedPath === pkg.path) {
      thumbnailCache.set(pkg.path, data);
      img.src = data;
      cover.classList.remove('hidden');
      const itemThumb = document.querySelector?.(`[data-thumb-path="${escapeHtml(pkg.path)}"]`);
      if (itemThumb) {
        itemThumb.outerHTML = `<img src="${escapeHtml(data)}" alt="">`;
      }
    }
  }).catch(() => {});
}

function openLightbox() {
  const pkg = selectedPackage();
  const img = $('heroCoverImg');
  if (!pkg || !img || !img.src) return;
  const modalImg = $('lightboxImage');
  const caption = $('lightboxCaption');
  const modal = $('lightboxModal');
  if (modalImg) modalImg.src = img.src;
  if (caption) caption.textContent = pkg.name;
  if (modal) modal.classList.remove('hidden');
}

function closeLightbox() {
  const modal = $('lightboxModal');
  if (modal) modal.classList.add('hidden');
  const modalImg = $('lightboxImage');
  if (modalImg) modalImg.src = '';
}

function stateLabel(state) {
  return t(`state.${state === 'loaded' ? 'loaded' : state === 'partial' ? 'partial' : 'archived'}`);
}

function getMatchInfo(item, query, scope = 'all') {
  if (!query) return { matches: true, reason: null };
  const nameMatch = Boolean(item.name && item.name.toLowerCase().includes(query));
  const matchedModel = (scope === 'all' || scope === 'model') && item.models
    ? item.models.find(m => m.filename && m.filename.toLowerCase().includes(query))
    : null;
  const matchedMissing = (scope === 'all' || scope === 'model') && item.missing
    ? item.missing.find(m => m.name && m.name.toLowerCase().includes(query))
    : null;
  const matchedNode = (scope === 'all' || scope === 'node') && item.custom_nodes
    ? item.custom_nodes.find(cn =>
        (cn.package && cn.package.toLowerCase().includes(query)) ||
        (cn.installed_folder && cn.installed_folder.toLowerCase().includes(query))
      )
    : null;

  if (scope === 'name') {
    return nameMatch ? { matches: true, reason: null } : { matches: false };
  }
  if (scope === 'model') {
    if (matchedModel) return { matches: true, reason: { type: 'model', text: matchedModel.filename } };
    if (matchedMissing) return { matches: true, reason: { type: 'missing', text: matchedMissing.name } };
    return { matches: false };
  }
  if (scope === 'node') {
    if (matchedNode) return { matches: true, reason: { type: 'node', text: matchedNode.package || matchedNode.installed_folder } };
    return { matches: false };
  }

  // scope === 'all':
  if (nameMatch) {
    return { matches: true, reason: null };
  }
  if (matchedModel) {
    return { matches: true, reason: { type: 'model', text: matchedModel.filename } };
  }
  if (matchedMissing) {
    return { matches: true, reason: { type: 'missing', text: matchedMissing.name } };
  }
  if (matchedNode) {
    return { matches: true, reason: { type: 'node', text: matchedNode.package || matchedNode.installed_folder } };
  }
  return { matches: false };
}

function renderLibrary() {
  const list = $('workflowList');
  if (!app.state?.configured) return;
  const query = (app.searchQuery || '').trim().toLowerCase();
  const scope = app.searchScope || 'all';
  const packagesWithMatch = (app.state.packages || []).map(item => {
    return { item, match: getMatchInfo(item, query, scope) };
  }).filter(({ item, match }) => {
    if (app.filter === 'hidden') {
      if (!item.hidden) return false;
    } else {
      if (item.hidden) return false;
      if (app.filter !== 'all') {
        if (app.filter === 'archived') {
          if (item.state !== 'archived' && item.state !== 'partial') return false;
        } else if (item.state !== app.filter) {
          return false;
        }
      }
    }
    return match.matches;
  });

  const sortedPackages = getSortedPackages(packagesWithMatch.map(p => p.item));
  const pkgToMatch = new Map(packagesWithMatch.map(p => [p.item.path, p.match]));
  const sortedPackagesWithMatch = sortedPackages.map(item => ({
    item,
    match: pkgToMatch.get(item.path) || { matches: true, reason: null }
  }));

  const countEl = $('workflowListCount');
  if (countEl) {
    const total = sortedPackages.length;
    countEl.textContent = total === 1 ? t('sort.count_one', {count: 1}) : t('sort.count', {count: total});
  }

  const rows = [];
  if (app.state.loading) rows.push(`<div class="library-scan-status"><span class="loading-pulse"></span><span>${t('library.scanning')}</span></div>`);
  if (!sortedPackages.length && !app.state.loading && !(app.state.scan_warnings || []).length) {
    const emptyMsg = query
      ? t('library.search_empty', {query: escapeHtml((app.searchQuery || '').trim())})
      : (app.state.packages?.length ? t('library.filter_empty') : t('library.empty'));
    rows.push(`<div class="workflow-list-empty">${emptyMsg}</div>`);
  }
  rows.push(...sortedPackagesWithMatch.map(({ item, match }) => {
    const reason = match.reason;
    const matchHintHtml = reason ? `
      <div class="workflow-match-hint" title="${escapeHtml(reason.text)}">
        <svg><use href="#${reason.type === 'node' ? 'i-expand' : reason.type === 'missing' ? 'i-alert' : 'i-drive'}"/></svg>
        <span class="match-type">${escapeHtml(t(`library.match_${reason.type}`))}:</span>
        <span class="match-text">${escapeHtml(reason.text)}</span>
      </div>` : '';
    return `
      <button class="workflow-item ${item.path === app.selectedPath ? 'active' : ''}" data-path="${escapeHtml(item.path)}" data-state="${item.state}" draggable="true">
        <span class="workflow-item-grip" title="${escapeHtml(t('sort.drag_grip'))}" aria-hidden="true">${icon('grip')}</span>
        <div class="workflow-item-thumb">${renderItemThumbHtml(item)}</div>
        <div class="workflow-item-info">
          <div class="workflow-name-wrap">
            <span class="workflow-name">${escapeHtml(item.name)}</span>
            ${item.hidden ? `<span class="workflow-hidden-tag" title="${escapeHtml(t('filter.hidden'))}">${icon('eye-off')}</span>` : ''}
          </div>
          ${matchHintHtml}
          <span class="workflow-meta"><span class="workflow-state">${stateLabel(item.state)}</span><span title="${t('detail.model_size')}">${formatBytes(item.model_bytes)}</span></span>
        </div>
      </button>`;
  }));
  if (!query && (app.filter === 'all' || app.filter === 'archived')) rows.push(...(app.state.scan_warnings || []).map(item => `
    <div class="invalid-package" title="${t('errors.invalid_package')}"><strong>${escapeHtml(item.name)}</strong><span>${t('library.invalid')}</span></div>`));
  list.innerHTML = rows.join('');
  list.querySelectorAll('.workflow-item').forEach(button => {
    button.addEventListener('click', () => {
      app.selectedPath = button.dataset.path;
      renderLibrary();
      renderDetail(app.state.packages.find(item => item.path === app.selectedPath));
    });

    button.addEventListener('dragstart', event => {
      app.isDraggingWorkflow = true;
      app.draggedWorkflowPath = button.dataset.path;
      button.classList.add('dragging');
      if (event.dataTransfer) {
        event.dataTransfer.effectAllowed = 'move';
        event.dataTransfer.setData('text/plain', button.dataset.path);
        event.dataTransfer.setData('application/x-comfyshelf-workflow', button.dataset.path);
      }
    });

    button.addEventListener('dragend', () => {
      app.isDraggingWorkflow = false;
      app.draggedWorkflowPath = null;
      button.classList.remove('dragging');
      list.querySelectorAll('.workflow-item').forEach(el => {
        el.classList.remove('drop-target-before', 'drop-target-after');
      });
    });

    button.addEventListener('dragover', event => {
      if (!app.isDraggingWorkflow || app.draggedWorkflowPath === button.dataset.path) return;
      event.preventDefault();
      if (event.dataTransfer) {
        event.dataTransfer.dropEffect = 'move';
      }
      const rect = button.getBoundingClientRect();
      const midY = rect.top + rect.height / 2;
      const isAfter = event.clientY > midY;
      button.classList.toggle('drop-target-after', isAfter);
      button.classList.toggle('drop-target-before', !isAfter);
    });

    button.addEventListener('dragleave', event => {
      if (!button.contains(event.relatedTarget)) {
        button.classList.remove('drop-target-before', 'drop-target-after');
      }
    });

    button.addEventListener('drop', event => {
      if (!app.isDraggingWorkflow || !app.draggedWorkflowPath) return;
      event.preventDefault();
      event.stopPropagation();
      const dragged = app.draggedWorkflowPath;
      const target = button.dataset.path;
      const isAfter = button.classList.contains('drop-target-after');
      button.classList.remove('drop-target-before', 'drop-target-after');
      if (dragged && target && dragged !== target) {
        moveWorkflowInOrder(dragged, target, isAfter ? 'after' : 'before');
      }
    });
  });

  list?.addEventListener?.('dragover', event => {
    if (!app.isDraggingWorkflow) return;
    event.preventDefault();
    if (event.dataTransfer) {
      event.dataTransfer.dropEffect = 'move';
    }
  });

  loadVisibleThumbnails();
}

function customNodeChipHtml(node) {
  const isInstalled = Boolean(node.installed);
  const statusClass = isInstalled ? 'installed' : 'missing';
  const statusLabel = t(isInstalled ? 'detail.custom_node_installed' : 'detail.custom_node_missing');
  const statusTitle = t(isInstalled ? 'detail.custom_node_status_installed' : 'detail.custom_node_status_missing');
  const targetUrl = node.repository_url || node.git_url || node.search_url;
  const isDirectRepo = Boolean(node.repository_url || node.git_url);
  const linkTitle = t(isDirectRepo ? 'detail.custom_node_open_github' : 'detail.custom_node_search_github');
  const copyTitle = t('detail.custom_node_copy_manager');
  const folderNote = (node.installed_folder && node.installed_folder.toLowerCase() !== String(node.package || '').toLowerCase())
    ? `<small class="custom-node-folder" title="${escapeHtml(node.installed_folder)}">(${escapeHtml(node.installed_folder)})</small>`
    : '';

  return `
    <div class="custom-node-chip ${statusClass}">
      <span class="custom-node-status ${statusClass}" title="${escapeHtml(statusTitle)}">
        ${icon(isInstalled ? 'check' : 'alert')}
        <span>${escapeHtml(statusLabel)}</span>
      </span>
      <span class="custom-node-name" title="${escapeHtml(node.package)}">${escapeHtml(node.package)}</span>
      ${folderNote}
      <div class="custom-node-actions">
        ${targetUrl ? `
          <button type="button" class="custom-node-btn" data-open-url="${escapeHtml(targetUrl)}" title="${escapeHtml(linkTitle)}" aria-label="${escapeHtml(linkTitle)}">
            ${icon('external')}
          </button>` : ''}
        <button type="button" class="custom-node-btn" data-copy-custom-node="${escapeHtml(node.package)}" title="${escapeHtml(copyTitle)}" aria-label="${escapeHtml(copyTitle)}">
          ${icon('copy')}
        </button>
      </div>
    </div>`;
}

function renderDetail(pkg) {
  if (!pkg) {
    $('detailView').classList.add('hidden');
    $('emptyState').classList.remove('hidden');
    renderDetailThumbnail(null);
    return;
  }
  $('emptyState').classList.add('hidden');
  $('detailView').classList.remove('hidden');
  renderDetailThumbnail(pkg);
  $('packageName').textContent = pkg.name;
  $('packagePath').textContent = pkg.path;
  $('renameButton').disabled = false;
  const hideBtn = $('hideWorkflowButton');
  if (hideBtn) {
    const isLoaded = pkg.state === 'loaded' || pkg.state === 'partial';
    if (isLoaded) {
      hideBtn.disabled = true;
      hideBtn.title = t('detail.hide_loaded_prevent');
      hideBtn.setAttribute?.('aria-label', t('detail.hide_loaded_prevent'));
      hideBtn.classList?.remove?.('is-hidden');
      hideBtn.innerHTML = icon('eye-off');
    } else if (pkg.hidden) {
      hideBtn.disabled = false;
      hideBtn.title = t('detail.unhide_workflow');
      hideBtn.setAttribute?.('aria-label', t('detail.unhide_workflow'));
      hideBtn.classList?.add?.('is-hidden');
      hideBtn.innerHTML = icon('eye');
    } else {
      hideBtn.disabled = false;
      hideBtn.title = t('detail.hide_workflow');
      hideBtn.setAttribute?.('aria-label', t('detail.hide_workflow'));
      hideBtn.classList?.remove?.('is-hidden');
      hideBtn.innerHTML = icon('eye-off');
    }
  }
  $('modelCount').textContent = String(pkg.model_count);
  $('modelPresence').textContent = t('detail.local_models', {count: pkg.present_models});
  $('modelSize').textContent = formatBytes(pkg.model_bytes);
  const presentBytes = (pkg.models || []).filter(model => model.present).reduce((sum, model) => sum + Number(model.size || 0), 0);
  const missingBytes = (pkg.models || []).filter(model => !model.present).reduce((sum, model) => sum + Number(model.size || 0), 0);
  const impactKey = pkg.state === 'loaded' ? 'detail.currently_local_bytes' : pkg.state === 'partial' ? 'detail.still_needed' : 'detail.needed_to_load';
  const impactBytes = pkg.state === 'loaded' ? presentBytes : missingBytes;
  $('storageImpactLabel').textContent = t(impactKey, {size: formatBytes(impactBytes)});
  $('heroMetric').title = t('detail.package_size_tooltip', {size: formatBytes(pkg.archive_size)});
  $('modelStorageMode').textContent = t(pkg.deduplicated ? 'detail.storage_pool' : 'detail.storage_package');
  $('modelStorageLabel').textContent = t(pkg.deduplicated ? 'detail.shared_pool' : 'detail.saved_in_package');

  const customNodes = pkg.custom_nodes || [];
  const customNodeCount = customNodes.length;
  const customNodesInstalled = customNodes.filter(n => n.installed).length;
  const customNodesMissing = customNodeCount - customNodesInstalled;

  if (customNodeCount > 0) {
    $('customNodeCount').textContent = `${customNodesInstalled} / ${customNodeCount}`;
    const subEl = $('customNodeSubtitle');
    if (subEl) {
      subEl.textContent = customNodesMissing === 0
        ? t('detail.custom_nodes_all_installed')
        : t('detail.custom_nodes_missing_count', {count: customNodesMissing});
    }
  } else {
    $('customNodeCount').textContent = '0';
    const subEl = $('customNodeSubtitle');
    if (subEl) subEl.textContent = t('detail.documented_only');
  }

  const pill = $('statusPill');
  pill.textContent = stateLabel(pkg.state);
  pill.className = `status-pill ${pkg.state}`;
  $('statusRail').className = `status-rail ${pkg.state}`;
  const hiddenPill = $('hiddenPill');
  if (hiddenPill) {
    hiddenPill.classList.toggle('hidden', !pkg.hidden);
  }

  renderIntegrity(pkg);

  $('modelList').innerHTML = pkg.models.length ? pkg.models.map(model => `
    <div class="dependency-row">
      <span class="dep-state ${model.present ? 'present' : ''}">${icon(model.present ? 'check' : 'download')}</span>
      <span class="dep-copy"><strong title="${escapeHtml(model.filename)}">${escapeHtml(model.filename)}</strong><span>${escapeHtml((model.kind || '').replaceAll('_', ' '))} · ${t(model.present ? 'detail.model_present' : 'detail.model_archived')}${model.present && model.link_type ? ` · <span class="dep-link-badge ${model.link_type}">${model.link_type === 'hardlink' ? '⚡ ' + t('detail.link_hardlink') : (model.link_type === 'symlink' ? '🔗 ' + t('detail.link_symlink') : '📁 ' + t('detail.link_copy'))}</span>` : ''}</span></span>
      <span class="dep-size">${formatBytes(model.size)}</span>
    </div>`).join('') : `<div class="workflow-list-empty">${t('detail.no_models')}</div>`;

  const notices = [];
  const missingDependencies = pkg.missing || [];
  if (!pkg.complete) notices.push({
    warning: true,
    text: missingDependencies.length === 1
      ? t('notice.missing_one')
      : t('notice.missing_many', {count: missingDependencies.length}),
    missing: missingDependencies,
  });
  if (pkg.state === 'partial') notices.push({warning: true, text: t('notice.partial')});
  if (pkg.complete && pkg.state !== 'partial') notices.push({warning: false, text: t('notice.complete')});
  notices.push({sharing: true});
  if (pkg.deduplicated) notices.push({warning: false, text: t('storage.shared_notice')});
  notices.push({parser: true});
  notices.push({warning: false, text: t('parser.scope')});
  notices.push({warning: false, text: t('notice.custom_nodes')});
  if (customNodeCount > 0) {
    if (customNodesMissing > 0) {
      notices.push({
        warning: true,
        text: customNodesMissing === 1
          ? t('notice.custom_nodes_missing_one')
          : t('notice.custom_nodes_missing_many', {count: customNodesMissing}),
        subtext: t('notice.custom_nodes_missing_hint')
      });
    } else {
      notices.push({warning: false, text: t('notice.custom_nodes_all_ready')});
    }
  }
  $('noticeList').innerHTML = notices.map(n => n.parser ? parserNoticesHtml(pkg.parser_warnings) : n.sharing ? sharedFilesHtml(pkg.models, {library: true}) : `
    <div class="notice ${n.warning ? 'warning' : ''}">
      ${icon(n.warning ? 'alert' : 'shield')}
      <div class="notice-body">
        <span>${escapeHtml(n.text)}</span>
        ${n.subtext ? `<small class="notice-subtext">${escapeHtml(n.subtext)}</small>` : ''}
        ${n.missing?.length ? `<div class="notice-missing-list">
          ${n.missing.map(item => missingFileHtml(item, true)).join('')}
        </div>` : ''}
      </div>
    </div>`).join('');
  $('customNodeList').innerHTML = customNodes.length
    ? customNodes.map(node => customNodeChipHtml(node)).join('')
    : `<span class="tag">${t('detail.no_custom_nodes')}</span>`;

  const primary = $('primaryActionButton');
  const secondary = $('secondaryActionButton');
  const tertiary = $('tertiaryActionButton');
  const storage = $('storageButton');
  storage.innerHTML = `${icon('drive')}${t('storage.optimize_button')}`;
  storage.classList.toggle('hidden', !!pkg.deduplicated);
  storage.onclick = managePackageStorage;
  const otherActive = (app.state.packages || []).some(item =>
    item.path !== pkg.path && (item.state === 'loaded' || item.state === 'partial')
  );
  if (tertiary) tertiary.classList.add('hidden');

  if (pkg.state === 'loaded') {
    primary.innerHTML = `${icon('upload')}${t('actions.unload')}`;
    primary.onclick = () => previewPackageAction('unload');
    secondary.classList.add('hidden');
  } else if (pkg.state === 'archived') {
    if (otherActive) {
      primary.innerHTML = `${icon('switch')}${t('actions.switch')}`;
      primary.onclick = previewWorkflowSwitch;
      secondary.innerHTML = `${icon('download')}${t('actions.load_additional')}`;
      secondary.onclick = () => previewPackageAction('restore');
      secondary.classList.remove('hidden');
    } else {
      primary.innerHTML = `${icon('download')}${t('actions.load')}`;
      primary.onclick = () => previewPackageAction('restore');
      secondary.classList.add('hidden');
    }
  } else {
    if (otherActive) {
      primary.innerHTML = `${icon('switch')}${t('actions.switch')}`;
      primary.onclick = previewWorkflowSwitch;
      secondary.innerHTML = `${icon('download')}${t('actions.load_missing')}`;
      secondary.onclick = () => previewPackageAction('restore');
      secondary.classList.remove('hidden');
      if (tertiary) {
        tertiary.innerHTML = `${icon('upload')}${t('actions.unload_present')}`;
        tertiary.onclick = () => previewPackageAction('unload');
        tertiary.classList.remove('hidden');
      }
    } else {
      primary.innerHTML = `${icon('download')}${t('actions.load_missing')}`;
      primary.onclick = () => previewPackageAction('restore');
      secondary.innerHTML = `${icon('upload')}${t('actions.unload_present')}`;
      secondary.onclick = () => previewPackageAction('unload');
      secondary.classList.remove('hidden');
    }
  }
}

function openSettings(required = false) {
  const installation = app.state?.installation;
  $('comfyRootInput').value = installation?.comfy_root || '';
  $('archiveRootInput').value = installation?.archive_root || '';
  if ($('linkModeSelect')) $('linkModeSelect').value = installation?.link_mode || 'auto';
  if ($('autoCopyWorkflowCheckbox')) {
    $('autoCopyWorkflowCheckbox').checked = getStorage('comfyshelf_auto_copy') !== 'false';
  }
  $('settingsValidation').classList.add('hidden');
  $('settingsCloseButton').classList.toggle('hidden', required);
  $('settingsCancelButton').classList.toggle('hidden', required);
  $('settingsModal').classList.remove('hidden');
}
function closeSettings() { if (app.state?.configured) $('settingsModal').classList.add('hidden'); }

async function browseFolder(kind) {
  try {
    const path = await callApi('choose_folder', kind);
    if (path) $(kind === 'comfy' ? 'comfyRootInput' : 'archiveRootInput').value = path;
  } catch (error) { toast(error.message, true); }
}

async function saveSettings() {
  const comfy = $('comfyRootInput').value.trim();
  const archive = $('archiveRootInput').value.trim();
  const linkMode = $('linkModeSelect')?.value || 'auto';
  const box = $('settingsValidation');
  if (!comfy || !archive) {
    box.textContent = t('settings.choose_both');
    box.className = 'validation-box';
    return;
  }
  if ($('autoCopyWorkflowCheckbox')) {
    setStorage('comfyshelf_auto_copy', $('autoCopyWorkflowCheckbox').checked ? 'true' : 'false');
  }
  showBusy(t('settings.checking'), t('settings.checking_detail'), false);
  try {
    const result = await callApi('save_settings', comfy, archive, linkMode);
    hideBusy();
    const warnings = result.validation.warnings || [];
    if (warnings.length) {
      box.textContent = t('settings.warning');
      box.className = 'validation-box warning';
    } else {
      $('settingsModal').classList.add('hidden');
    }
    toast(t('settings.saved'));
    await refreshState();
    if (!warnings.length) $('settingsModal').classList.add('hidden');
  } catch (error) {
    hideBusy();
    box.textContent = error.message;
    box.className = 'validation-box';
  }
}

async function inspectAndPackWorkflow(path) {
  if (!path) return;
  showBusy(t('pack.analyzing'), t('pack.analyzing_detail'));
  const report = await callApi('inspect_for_pack', path);
  hideBusy();
  const missing = report.missing || [];
  const exists = (app.state.packages || []).some(item => item.path.toLowerCase() === report.output_path.toLowerCase());
  const missingDetails = missing.length ? `
    <div class="missing-detail">
      <span class="missing-detail-label">${t('pack.missing_label')}</span>
      ${missing.map(item => missingFileHtml(item)).join('')}
    </div>` : '';
  const detectedNodes = report.custom_nodes || [];
  const missingNodesCount = detectedNodes.filter(n => !n.installed).length;
  const nodeStat = detectedNodes.length ? `
    <div class="confirm-stat"><span>${t('detail.custom_nodes_used')}</span><strong>${detectedNodes.length}${missingNodesCount ? ` (${missingNodesCount} ${t('detail.custom_node_missing')})` : ''}</strong></div>` : '';
  const copy = `
    <p>${t('pack.copy', {workflow: escapeHtml(report.workflow)})}</p>
    <div class="confirm-stat"><span>${t('pack.detected')}</span><strong>${report.assets.length}</strong></div>
    <div class="confirm-stat"><span>${t('pack.missing')}</span><strong>${missing.length}</strong></div>
    ${nodeStat}
    ${missingDetails}
    ${parserWarningsHtml(report.parser_warnings)}
    <p>${t('parser.scope')}</p>
    <div class="confirm-stat"><span>${t('common.target')}</span><strong>${escapeHtml(report.output_path)}</strong></div>
    ${exists ? `<p>${t('pack.replace')}</p>` : ''}`;
  openConfirm({
    eyebrow: t('pack.eyebrow'), title: t(missing.length ? 'pack.title_incomplete' : 'pack.title'),
    copy, danger: false, proceedLabel: t(missing.length ? 'pack.proceed_incomplete' : 'pack.proceed'),
    showIncomplete: missing.length > 0,
    action: async () => {
      showBusy(t('pack.running'), t('pack.running_detail'));
      const result = await callApi('pack_workflow', path, missing.length > 0, exists);
      hideBusy(); toast(t('pack.done', {name: report.workflow})); await refreshState(result.output_path);
    }
  });
}

async function addWorkflow() {
  if (!app.state?.configured) { openSettings(true); return; }
  try {
    const path = await callApi('choose_workflow');
    if (!path) return;
    await inspectAndPackWorkflow(path);
  } catch (error) { hideBusy(); operationError(error); }
}

async function inspectAndImportArchive(path) {
  if (!path) return;
  showBusy(t('import.inspecting'), t('import.inspecting_detail'));
  const report = await callApi('inspect_archive_import', path);
  hideBusy();
  const missing = report.missing || [];
  const extras = report.extra_models || [];
  const direct = report.direct_load || {bytes_required:0, bytes_free:0, present_models:0, blockers:[]};
  const directBlocked = (direct.blockers || []).length > 0;
  const missingDetails = missing.length ? `
    <div class="missing-detail">
      <span class="missing-detail-label">${t('import.missing_label')}</span>
      ${missing.map(item => missingFileHtml(item)).join('')}
    </div>` : '';
  const replacementText = report.source_is_target
    ? t('import.replace_selected')
    : report.target_exists
      ? t('import.replace_existing')
      : t('import.add_library');
  const copy = `
    <p>${t('import.copy', {package: escapeHtml(report.package)})}</p>
    <div class="import-chain">
      <div class="import-chain-row">${icon('check')}<span>${t('import.paths_ok')}<small>${t('import.paths_hint')}</small></span></div>
      <div class="import-chain-row ${missing.length ? 'warning' : ''}">${icon(missing.length ? 'alert' : 'check')}<span>${t('import.models_matched', {matched: report.matched_count, total: report.assets.length})}<small>${t('import.models_hint')}</small></span></div>
      <div class="import-chain-row">${icon('shield')}<span>${t('import.manifest')}<small>${t('import.manifest_hint')}</small></span></div>
    </div>
    <div class="confirm-stat"><span>${t('import.extras')}</span><strong>${extras.length}</strong></div>
    <div class="confirm-stat"><span>${t('common.target')}</span><strong>${escapeHtml(report.target_path)}</strong></div>
    ${missingDetails}
    ${parserWarningsHtml(report.parser_warnings)}
    <p>${t('parser.scope')}</p>
    <p>${escapeHtml(replacementText)}</p>
    <div class="destination-heading"><span>${t('import.after')}</span><small>${t('import.choose_state')}</small></div>
    <div class="import-destinations" role="radiogroup" aria-label="${t('import.destination')}">
      <label class="import-destination ${report.archive_space_sufficient ? '' : 'blocked'}">
        <input type="radio" name="importDestination" value="archive" checked ${report.archive_space_sufficient ? '' : 'disabled'}>
        <span class="destination-icon">${icon('shelf')}</span>
        <span class="destination-copy"><strong>${t('import.seal_archive')}</strong><small>${t('import.seal_archive_hint')}</small><em>${t('import.temp_free', {needed: formatBytes(report.reseal_bytes_required), free: formatBytes(report.archive_bytes_free)})}</em></span>
      </label>
      <label class="import-destination ${directBlocked || !report.archive_space_sufficient ? 'blocked' : ''}">
        <input type="radio" name="importDestination" value="load" ${directBlocked || !report.archive_space_sufficient ? 'disabled' : ''}>
        <span class="destination-icon">${icon('drive')}</span>
        <span class="destination-copy"><strong>${t('import.seal_load')}</strong><small>${directBlocked ? t('import.direct_blocked') : t('import.seal_load_hint')}</small><em>${t('import.required_present', {required: formatBytes(direct.bytes_required), present: direct.present_models})}</em></span>
      </label>
    </div>`;
  openConfirm({
    eyebrow: t('import.eyebrow'),
    title: t(missing.length ? 'import.title_incomplete' : 'import.title'),
    copy,
    danger: false,
    wide: true,
    proceedLabel: t(missing.length ? 'import.proceed_incomplete' : 'import.proceed'),
    showIncomplete: missing.length > 0,
    disabled: !report.archive_space_sufficient,
    action: async () => {
      const destination = document.querySelector('input[name="importDestination"]:checked')?.value || 'archive';
      const loadAfterImport = destination === 'load';
      showBusy(
        t(loadAfterImport ? 'import.running_load' : 'import.running'),
        t(loadAfterImport ? 'import.running_load_detail' : 'import.running_detail')
      );
      const result = await callApi(
        'import_archive', path, missing.length > 0, report.target_exists, loadAfterImport
      );
      hideBusy();
      toast(t(loadAfterImport ? 'import.done_load' : 'import.done', {name: result.package}));
      await refreshState(result.output_path);
    }
  });
  document.querySelectorAll('input[name="importDestination"]').forEach(option => {
    option.addEventListener('change', () => {
      $('confirmProceedButton').textContent = option.value === 'load'
        ? t('import.proceed_load')
        : t(missing.length ? 'import.proceed_incomplete' : 'import.proceed');
    });
  });
}

async function importPackage() {
  if (!app.state?.configured) { openSettings(true); return; }
  try {
    const path = await callApi('choose_archive');
    if (!path) return;
    await inspectAndImportArchive(path);
  } catch (error) { hideBusy(); operationError(error); }
}

function readFileAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result;
      const base64 = typeof result === 'string' && result.includes(',')
        ? result.split(',')[1]
        : result;
      resolve(base64);
    };
    reader.onerror = () => reject(new Error('Failed to read dropped file'));
    reader.readAsDataURL(file);
  });
}

async function handleDroppedFile(file) {
  if (!file) return;
  const name = String(file.name || '').toLowerCase();
  if (!name.endsWith('.png') && !name.endsWith('.json') && !name.endsWith('.zip')) {
    toast(t('drop.unsupported_file'), true);
    return;
  }
  if (!app.state?.configured) { openSettings(true); return; }

  showBusy(t('drop.processing'), t('drop.processing_detail'));
  let targetPath = file.pywebviewFullPath || file.path || null;
  if (!targetPath) {
    try {
      const base64 = await readFileAsBase64(file);
      targetPath = await callApi('stage_dropped_file', file.name, base64);
    } catch (err) {
      hideBusy();
      operationError(err);
      return;
    }
  }

  try {
    if (name.endsWith('.zip')) {
      await inspectAndImportArchive(targetPath);
    } else {
      await inspectAndPackWorkflow(targetPath);
    }
  } catch (error) {
    hideBusy();
    operationError(error);
  }
}

function renderIntegrity(pkg) {
  if (!pkg) return;
  const check = pkg.verification || {state: 'unknown'};
  const state = ['verified', 'invalid', 'stale'].includes(check.state) ? check.state : 'unknown';
  const key = {verified:'valid', invalid:'invalid', stale:'stale', unknown:'unknown'}[state];
  const label = $('integrityLabel');
  label.className = `integrity-label ${state === 'verified' ? 'valid' : state}`;
  label.innerHTML = `${icon(state === 'stale' ? 'alert' : 'shield')}${escapeHtml(t(`detail.integrity_${key}`))}`;
  const date = check.checked_at ? new Date(check.checked_at) : null;
  label.title = date && !Number.isNaN(date.getTime())
    ? `${t('detail.integrity_checked', {date: date.toLocaleString(app.language === 'de' ? 'de-DE' : 'en-GB', {hour12:false})})}\n${t('detail.integrity_cached_hint')}`
    : t('detail.integrity_unknown');
  if (check.persisted === false) label.title += `\n${t('verify.not_saved')}`;
  if (state === 'stale') label.title += `\n${t('detail.integrity_stale_hint')}`;
}

async function verifySelected() {
  const pkg = app.state?.packages?.find(item => item.path === app.selectedPath);
  if (!pkg) return;
  showBusy(t('verify.running'), t('verify.detail'));
  try {
    const result = await callApi('verify_archive', pkg.path);
    pkg.verification = result.verification || {state: result.valid ? 'verified' : 'invalid'};
    hideBusy(); renderDetail(pkg);
    const changed = pkg.verification.state === 'stale';
    const unsaved = pkg.verification.persisted === false;
    toast(t(changed ? 'verify.changed' : !result.valid ? 'verify.invalid' : unsaved ? 'verify.not_saved' : 'verify.valid'), changed || unsaved || !result.valid);
  } catch (error) {
    hideBusy();
    operationError(error);
    await refreshState(pkg.path);
  }
}

async function managePackageStorage() {
  const pkg = selectedPackage();
  if (!pkg) return;
  if (pkg.deduplicated) return;
  openConfirm({
    eyebrow: t('storage.eyebrow'),
    title: t('storage.optimize_title'),
    copy: `<p>${t('storage.optimize_copy')}</p><div class="confirm-stat"><span>${t('storage.model_payload')}</span><strong>${formatBytes(pkg.model_bytes)}</strong></div>`,
    proceedLabel: t('storage.optimize_proceed'),
    action: async () => {
      showBusy(t('storage.optimize_running'), t('storage.optimize_detail'));
      const result = await callApi('optimize_archive', pkg.path);
      hideBusy();
      toast(t('storage.optimize_done', {size: formatBytes(result.reused_object_bytes)}));
      await refreshState(pkg.path);
    },
  });
}

async function showSelectedWorkflowFile() {
  const pkg = selectedPackage();
  if (!pkg) return;
  const button = $('showWorkflowFileButton');
  button.disabled = true;
  try {
    const report = await callApi('show_workflow_file', pkg.path);
    if (report.exists) { toast(t('workflow_file.shown')); return; }
    openConfirm({
      eyebrow: t('workflow_file.button'), title: t('workflow_file.missing'),
      copy: `<p>${t('workflow_file.copy')}</p><p class="workflow-file-path">${escapeHtml(report.path)}</p><p>${t('workflow_file.note')}</p>`,
      proceedLabel: t('workflow_file.restore'),
      action: async () => {
        showBusy(t('workflow_file.restoring'), t('workflow_file.note'), false);
        try {
          await callApi('restore_workflow_file', pkg.path, report.token);
          toast(t('workflow_file.restored'));
        } finally { hideBusy(); }
      },
    });
  } catch (error) { operationError(error); }
  finally { button.disabled = false; }
}

async function copySelectedWorkflowJson(options = {}) {
  const pkg = selectedPackage();
  if (!pkg) return;
  const button = $('copyWorkflowButton');
  if (button) button.disabled = true;

  try {
    const report = await callApi('get_workflow_json', pkg.path);
    if (!report?.workflow) throw new Error('No workflow data returned');
    const jsonStr = typeof report.workflow === 'string'
      ? report.workflow
      : JSON.stringify(report.workflow, null, 2);
    await writeClipboardText(jsonStr);

    if (button) {
      button.classList.add('copied');
      const textEl = $('copyWorkflowText');
      if (textEl) textEl.textContent = t('workflow.copied');
      setTimeout(() => {
        button.classList.remove('copied');
        if (textEl) textEl.textContent = t('workflow.copy_button');
      }, 2500);
    }

    if (!options.silent) {
      toast(t('workflow.copy_success'));
    }
  } catch (err) {
    toast(t('workflow.copy_failed') + ': ' + (err.message || ''), true);
  } finally {
    if (button) button.disabled = false;
  }
}

async function openComfyWebUi() {
  try {
    await callApi('open_external_url', 'http://127.0.0.1:8188');
    toast(t('summary.web_opened'));
  } catch (err) {
    toast(err.message, true);
  }
}

const TOUR_STEPS = [
  {
    icon: '#i-shelf',
    titleKey: 'tour.step1_title',
    copyKey: 'tour.step1_copy',
    highlightKey: 'tour.step1_highlight',
    highlightIcon: '#i-drive',
  },
  {
    icon: '#i-refresh',
    titleKey: 'tour.step2_title',
    copyKey: 'tour.step2_copy',
    highlightKey: 'tour.step2_highlight',
    highlightIcon: '#i-check',
  },
  {
    icon: '#i-copy',
    titleKey: 'tour.step3_title',
    copyKey: 'tour.step3_copy',
    highlightKey: 'tour.step3_highlight',
    highlightIcon: '#i-workflow',
  },
  {
    icon: '#i-shield',
    titleKey: 'tour.step4_title',
    copyKey: 'tour.step4_copy',
    highlightKey: 'tour.step4_highlight',
    highlightIcon: '#i-shield',
  },
];

function renderTourStep(index) {
  currentTourStep = Math.max(0, Math.min(index, TOUR_STEPS.length - 1));
  const step = TOUR_STEPS[currentTourStep];
  const total = TOUR_STEPS.length;

  const badge = $('tourStepBadge');
  if (badge) badge.textContent = t('tour.step', {current: currentTourStep + 1, total});

  const iconEl = $('tourIcon');
  if (iconEl?.querySelector('use')) iconEl.querySelector('use').setAttribute('href', step.icon);

  const titleEl = $('tourTitle');
  if (titleEl) titleEl.textContent = t(step.titleKey);

  const copyEl = $('tourCopy');
  if (copyEl) copyEl.textContent = t(step.copyKey);

  const highlightIcon = $('tourHighlightIcon');
  if (highlightIcon?.querySelector('use')) highlightIcon.querySelector('use').setAttribute('href', step.highlightIcon);

  const highlightText = $('tourHighlightText');
  if (highlightText) highlightText.textContent = t(step.highlightKey);

  const dots = $('tourDots')?.children || [];
  for (let i = 0; i < dots.length; i++) {
    dots[i].classList.toggle('active', i === currentTourStep);
  }

  const prevBtn = $('tourPrevButton');
  if (prevBtn) prevBtn.classList.toggle('hidden', currentTourStep === 0);

  const nextBtn = $('tourNextButton');
  if (nextBtn) {
    nextBtn.textContent = currentTourStep === total - 1 ? t('tour.finish') : t('tour.next');
  }
}

function openTour(force = false) {
  if (!force && getStorage('comfyshelf_tour_completed') === 'true') return;
  currentTourStep = 0;
  renderTourStep(0);
  $('tourModal')?.classList.remove('hidden');
}

function closeTour() {
  $('tourModal')?.classList.add('hidden');
  setStorage('comfyshelf_tour_completed', 'true');
}

function nextTourStep() {
  if (currentTourStep >= TOUR_STEPS.length - 1) {
    closeTour();
  } else {
    renderTourStep(currentTourStep + 1);
  }
}

function prevTourStep() {
  if (currentTourStep > 0) {
    renderTourStep(currentTourStep - 1);
  }
}

async function scanSelectedDependencies() {
  const pkg = selectedPackage();
  if (!pkg) return;
  showBusy(t('dependencies.running'), t('dependencies.hint'));
  try {
    const report = await callApi('scan_archive_dependencies', pkg.path);
    hideBusy();
    const addable = report.counts.found > 0;
    const noSpace = report.space_required > report.space_free;
    const rows = report.rows.map(row => {
      const state = ['found', 'included', 'ambiguous', 'missing'].includes(row.state) ? row.state : 'missing';
      const source = row.source === 'pool' ? row.pool?.package : row.local?.path;
      return `<li class="dependency-scan-row ${state}">
        <div class="dependency-scan-name"><strong>${escapeHtml(row.name)}</strong><span>${escapeHtml(row.kind.replaceAll('_', ' '))}</span></div>
        <span class="dependency-scan-status">${escapeHtml(t(`dependencies.${state}`))}</span>
        ${source ? `<small class="dependency-scan-source">${escapeHtml(t(`dependencies.source_${row.source}`))}: ${escapeHtml(source)}</small>` : ''}
        ${state === 'ambiguous' && row.candidates?.length ? `<details><summary>${t('dependencies.candidates')}</summary><ul>${row.candidates.map(value => `<li>${escapeHtml(value)}</li>`).join('')}</ul></details>` : ''}
      </li>`;
    }).join('');
    openConfirm({
      wide: true, eyebrow: t('dependencies.eyebrow'), title: t('dependencies.title'),
      copy: `<p>${t('dependencies.readonly')}</p>
        <p class="dependency-scan-summary">${t('dependencies.counts', report.counts)}</p>
        <ul class="dependency-scan-results">${rows || `<li>${t('dependencies.empty')}</li>`}</ul>
        ${parserWarningsHtml(report.parser_warnings)}
        ${report.warnings?.length ? `<p class="dependency-scan-warning">${t('dependencies.skipped')} ${report.warnings.map(escapeHtml).join(', ')}</p>` : ''}
        ${addable ? `<p>${t('dependencies.confirm_hint')}</p><p>${t('dependencies.space', {size: formatBytes(report.space_required), free: formatBytes(report.space_free)})}</p>` : `<p>${t('dependencies.nothing')}</p>`}
        ${addable && noSpace ? `<p class="dependency-scan-warning">${t('errors.dependency_space')}</p>` : ''}`,
      proceedLabel: t(addable ? 'dependencies.add' : 'common.close'),
      readOnly: !addable, disabled: addable && noSpace,
      action: async () => {
        if (!addable) return;
        showBusy(t('dependencies.adding'), t('dependencies.hash_hint'));
        const result = await callApi('complete_archive_dependencies', pkg.path, report.token);
        hideBusy();
        await refreshState(pkg.path);
        openConfirm({
          wide: true, readOnly: true, eyebrow: t('dependencies.eyebrow'), title: t('dependencies.done', {count: result.added}),
          copy: `<p>${t(result.complete ? 'dependencies.complete' : 'dependencies.incomplete')}</p>
            <p>${t('dependencies.reused', {size: formatBytes(result.reused_object_bytes)})}</p>
            <p>${t('dependencies.backup')}</p><p class="dependency-backup-path">${escapeHtml(result.backup)}</p>
            ${result.verification?.state === 'stale' ? `<p class="dependency-scan-warning">${t('verify.changed')}</p>` : ''}
            ${result.verification?.persisted === false ? `<p class="dependency-scan-warning">${t('verify.not_saved')}</p>` : ''}`,
          proceedLabel: t('common.close'), action: async () => {},
        });
      },
    });
  } catch (error) { hideBusy(); operationError(error); }
}

function showPortableExportPreview(pkg, report) {
  const missing = report.missing || [];
  const blockers = report.blockers || [];
  const copy = `<div class="export-summary">
    <p>${t('storage.export_includes')}</p>
    <p class="export-exclusion">${t('storage.export_excludes')}</p>
    <div class="export-destination"><span>${t('common.target')}</span><strong>${escapeHtml(report.output_path)}</strong></div>
    <div class="confirm-stat"><span>${t('storage.export_space')}</span><strong>${formatBytes(report.bytes_required)}</strong></div>
    <div class="confirm-stat"><span>${t('storage.export_free')}</span><strong>${formatBytes(report.bytes_free)}</strong></div>
    <p class="export-footnote">${t('storage.export_unchanged')}</p>
    ${!report.complete ? `<div class="missing-detail"><span class="missing-detail-label">${t('storage.export_missing')}</span>${missing.map(item => missingFileHtml(item)).join('')}${!missing.length ? `<p>${t('storage.export_incomplete')}</p>` : ''}</div>` : ''}
    ${blockers.length ? `<div class="missing-detail diagnostic-detail">${blockers.map(item => `<p>${escapeHtml(t(`export_blocker.${item.code}`))}${item.path ? `<br><strong>${escapeHtml(item.path)}</strong>` : ''}</p>`).join('')}</div>` : ''}
  </div>`;
  openConfirm({
    eyebrow: t('storage.export_button'), title: t('storage.export_title'), copy,
    wide: true, showIncomplete: !report.complete, disabled: blockers.length > 0,
    proceedLabel: t('storage.export_proceed'),
    action: async (flags = {}) => {
      showBusy(t('storage.export_running'), t('storage.export_detail'));
      const result = await callApi('export_portable_archive', pkg.path, report.output_path, !!flags.allowIncomplete);
      hideBusy();
      toast(t('storage.export_done', {name: result.output_path}));
    },
  });
}

async function exportSelectedPackage() {
  const pkg = selectedPackage();
  if (!pkg) return;
  const button = $('exportButton');
  button.disabled = true;
  try {
    const output = await callApi('choose_export_destination', pkg.path);
    if (!output) return;
    showBusy(t('storage.export_checking'), t('storage.export_checking_detail'));
    const report = await callApi('preview_portable_archive', pkg.path, output);
    hideBusy();
    showPortableExportPreview(pkg, report);
  } catch (error) { hideBusy(); operationError(error); }
  finally { button.disabled = false; }
}

async function optimizeEntireLibrary() {
  showBusy(t('library_optimize.inspecting'), t('library_optimize.inspecting_detail'));
  try {
    const report = await callApi('inspect_library_optimization');
    hideBusy();
    if (!report.package_count) {
      toast(t('library_optimize.none'));
      return;
    }
    closeSettings();
    openConfirm({
      eyebrow: t('library_optimize.eyebrow'),
      title: t('library_optimize.title'),
      wide: true,
      copy: `
        <p>${t('library_optimize.copy')}</p>
        <div class="confirm-stat"><span>${t('library_optimize.packages')}</span><strong>${report.package_count}</strong></div>
        <div class="confirm-stat"><span>${t('library_optimize.models')}</span><strong>${report.model_count}</strong></div>
        <div class="confirm-stat"><span>${t('library_optimize.payload')}</span><strong>${formatBytes(report.model_bytes)}</strong></div>
        <div class="confirm-stat"><span>${t('library_optimize.already_shared')}</span><strong>${report.already_shared}</strong></div>`,
      proceedLabel: t('library_optimize.proceed'),
      action: async () => {
        showBusy(t('library_optimize.running'), t('library_optimize.running_detail'));
        const result = await callApi('optimize_library');
        hideBusy();
        toast(t('library_optimize.done', {count: result.optimized_count}));
        await refreshState(app.selectedPath);
      },
    });
  } catch (error) { hideBusy(); operationError(error); }
}

function sharedFilesHtml(candidates = [], {library = false, force = false} = {}) {
  const files = new Map();
  const normalize = value => String(value).replaceAll('\\', '/').toLowerCase();
  for (const item of candidates) {
    const references = library ? item.library_shared_by : item.shared_by;
    if (!references?.length) continue;
    const path = String(item.archive_path || item.path || item.filename || '');
    const key = normalize(path);
    if (!files.has(key)) files.set(key, {path, references: new Map()});
    for (const reference of references) {
      const workflowPath = typeof reference === 'string' ? reference : reference.path;
      if (!workflowPath) continue;
      files.get(key).references.set(normalize(workflowPath), {
        path: String(workflowPath),
        name: typeof reference === 'string'
          ? searchableFilename(reference).replace(/\.json$/i, '')
          : String(reference.name || searchableFilename(workflowPath)),
      });
    }
  }
  const rows = [...files.values()].filter(item => item.references.size);
  if (!rows.length && !force) return '';
  const workflowCount = new Set(rows.flatMap(item => [...item.references.keys()])).size;
  const summary = t('sharing.counts', {files: rows.length, workflows: workflowCount});
  const prefix = library ? 'sharing.library' : 'sharing.warning';
  return `<section class="sharing-box ${library ? 'sharing-library' : 'sharing-warning'}" aria-label="${escapeHtml(t(`${prefix}_title`))}">
    <div class="sharing-heading">${icon(library ? 'shelf' : 'alert')}<strong>${escapeHtml(t(`${prefix}_title`))}</strong></div>
    <p class="sharing-impact">${escapeHtml(t(`${prefix}_impact`))}</p>
    ${rows.length ? `<p class="sharing-counts">${escapeHtml(summary)}</p>
      <details class="sharing-details"><summary>${escapeHtml(t('sharing.details'))}</summary>
        <ul class="sharing-files">${rows.map(item => `<li>
          <strong class="sharing-filename">${escapeHtml(searchableFilename(item.path))}</strong>
          <small class="sharing-path">${escapeHtml(item.path)}</small>
          <ul class="sharing-workflows">${[...item.references.values()].map(reference => `<li>
            <strong>${escapeHtml(reference.name)}</strong><small class="sharing-path">${escapeHtml(reference.path)}</small>
          </li>`).join('')}</ul>
        </li>`).join('')}</ul>
        ${library ? `<p class="sharing-scope">${escapeHtml(t(`${prefix}_scope`))}</p>` : ''}
      </details>` : `<p>${escapeHtml(t('sharing.details_unavailable'))}</p>`}
    ${!library ? `<p class="sharing-scope">${escapeHtml(t(`${prefix}_scope`))}</p>` : ''}
  </section>`;
}

async function previewPackageAction(actionName) {
  const pkg = app.state?.packages?.find(item => item.path === app.selectedPath);
  if (!pkg) return;
  showBusy(t(actionName === 'restore' ? 'restore.preparing' : 'restore.unload_preparing'), t('restore.checking'));
  try {
    const preview = await callApi('preview_action', actionName, pkg.path);
    hideBusy();
    const isRestore = actionName === 'restore';
    const count = isRestore ? preview.candidates.length : preview.candidates.length;
    const bytes = isRestore ? preview.bytes_required : preview.bytes_reclaimable;
    const shared = !isRestore && preview.candidates.some(item => item.shared_by?.length);
    const conflictCount = preview.conflicts?.length || 0;
    const copy = `
      <p>${t(isRestore ? 'restore.copy' : 'restore.unload_copy')}</p>
      ${shared ? sharedFilesHtml(preview.candidates) : ''}
      <div class="confirm-stat"><span>${t(isRestore ? 'restore.restorable' : 'restore.unloadable')}</span><strong>${count}</strong></div>
      <div class="confirm-stat"><span>${t(isRestore ? 'restore.required' : 'restore.reclaimed')}</span><strong>${formatBytes(bytes)}</strong></div>
      ${isRestore && preview.bytes_linkable ? `<div class="confirm-stat"><span>${t('restore.instant_link')}</span><strong>${formatBytes(preview.bytes_linkable)} (${t('restore.zero_storage')})</strong></div>` : ''}
      ${!isRestore && preview.bytes_unlinked ? `<div class="confirm-stat"><span>${t('restore.instant_unlink')}</span><strong>${formatBytes(preview.bytes_unlinked)} (${t('restore.pool_preserved')})</strong></div>` : ''}
      <div class="confirm-stat"><span>${t('common.conflicts')}</span><strong>${conflictCount}</strong></div>
      ${removalIssuesHtml(preview.removal_issues)}
      ${!isRestore && preview.pool_preparation_required ? `<p>${t('restore.pool_prepare')}</p>` : ''}`;
    openConfirm({
      eyebrow: t('restore.eyebrow'),
      title: t(isRestore ? 'restore.title' : 'restore.unload_title'),
      copy, danger: !isRestore, proceedLabel: t(isRestore ? 'restore.proceed' : 'restore.unload_proceed', {count}),
      showIncomplete: !pkg.complete, showShared: shared, wide: shared,
      disabled: conflictCount > 0 || !!preview.removal_issues?.length,
      action: async ({allowIncomplete, allowShared}) => {
        showBusy(t(isRestore ? 'restore.running' : 'restore.unload_running'), t(isRestore ? 'restore.running_detail' : 'restore.unload_running_detail'));
        await callApi('execute_action', actionName, pkg.path, allowIncomplete, allowShared);
        hideBusy();
        const autoCopy = getStorage('comfyshelf_auto_copy') !== 'false';
        if (isRestore && autoCopy) {
          try {
            await copySelectedWorkflowJson({silent: true});
            toast(t('toast.restored_and_copied'));
          } catch (_) {
            toast(t('restore.done'));
          }
        } else {
          toast(t(isRestore ? 'restore.done' : 'restore.unload_done'));
        }
        await refreshState(pkg.path);
      }
    });
  } catch (error) { hideBusy(); operationError(error); }
}

async function previewWorkflowSwitch() {
  const pkg = app.state?.packages?.find(item => item.path === app.selectedPath);
  if (!pkg) return;
  showBusy(t('switch.preparing'), t('switch.preparing_detail'));
  try {
    const preview = await callApi('preview_switch', pkg.path);
    hideBusy();
    const conflictCount = (preview.conflicts?.length || 0) + (preview.restore?.conflicts?.length || 0);
    const shared = (preview.shared_count || 0) > 0 || !!preview.unload_candidates?.some(item => item.shared_by?.length);
    const hardBlockers = (preview.blockers || []).filter(item => !item.toLowerCase().includes('unvollständig') && !item.toLowerCase().includes('referenziert'));
    const copy = `
      <p>${t('switch.copy', {name: escapeHtml(pkg.name)})}</p>
      ${shared ? sharedFilesHtml(preview.unload_candidates, {force: true}) : ''}
      <div class="confirm-stat"><span>${t('switch.load')}</span><strong>${formatBytes(preview.bytes_required)}</strong></div>
      ${preview.bytes_linkable ? `<div class="confirm-stat"><span>${t('restore.instant_link')}</span><strong>${formatBytes(preview.bytes_linkable)} (${t('restore.zero_storage')})</strong></div>` : ''}
      <div class="confirm-stat"><span>${t('switch.unload')}</span><strong>${formatBytes(preview.bytes_reclaimable)}</strong></div>
      ${preview.bytes_unlinked ? `<div class="confirm-stat"><span>${t('restore.instant_unlink')}</span><strong>${formatBytes(preview.bytes_unlinked)} (${t('restore.pool_preserved')})</strong></div>` : ''}
      <div class="confirm-stat"><span>${t('switch.other')}</span><strong>${preview.source_packages.length}</strong></div>
      <div class="confirm-stat"><span>${t('common.conflicts')}</span><strong>${conflictCount}</strong></div>
      ${removalIssuesHtml(preview.removal_issues)}
      ${hardBlockers.length ? `<div class="missing-detail"><span class="missing-detail-label">${t('switch.blocked')}</span>${hardBlockers.map(() => `<div class="missing-item">${icon('alert')}<span><strong>${t('switch.blocker')}</strong></span></div>`).join('')}</div>` : ''}`;
    openConfirm({
      eyebrow: t('switch.eyebrow'), title: t('switch.title'), copy,
      danger: false, proceedLabel: t('switch.proceed'),
      showIncomplete: !pkg.complete, showShared: shared, wide: shared,
      disabled: conflictCount > 0 || hardBlockers.length > 0,
      action: async ({allowIncomplete, allowShared}) => {
        showBusy(t('switch.running'), t('switch.running_detail'));
        const result = await callApi('execute_switch', pkg.path, allowIncomplete, allowShared);
        hideBusy();
        const autoCopy = getStorage('comfyshelf_auto_copy') !== 'false';
        if (autoCopy) {
          try {
            await copySelectedWorkflowJson({silent: true});
            toast(t('toast.switched_and_copied', {name: result.package}));
          } catch (_) {
            toast(t('switch.done', {name: result.package}));
          }
        } else {
          toast(t('switch.done', {name: result.package}));
        }
        await refreshState(pkg.path);
      }
    });
  } catch (error) {
    hideBusy();
    operationError(error);
  }
}

async function refreshDiskState() {
  const button = $('diskRefreshButton');
  button.classList.add('refreshing');
  button.disabled = true;
  app.diskRefreshPending = true;
  try {
    const refreshed = await refreshState(app.selectedPath);
    if (refreshed) {
      app.diskRefreshPending = false;
      toast(t('toast.storage_refreshed'));
    }
  } finally {
    if (!app.state?.loading) {
      button.classList.remove('refreshing');
      button.disabled = false;
    }
  }
}

const historyIcons = {
  archive: 'upload', import: 'import', import_load: 'download', restore: 'download',
  unload: 'upload', switch: 'switch', rename: 'edit', optimize: 'drive', export: 'upload', optimize_library: 'drive',
  unload_partial: 'alert', switch_partial: 'alert'
};

function renderHistoryEntries(entries) {
  app.historyEntries = entries;
  $('historyList').innerHTML = entries.length ? entries.map(entry => {
    const labelKey = `history.${entry.action}`;
    const label = app.messages[labelKey] || t('history.action');
    const iconName = historyIcons[entry.action] || 'history';
    const date = new Date(entry.timestamp);
    return `<div class="history-item">
      <span class="history-icon">${icon(iconName)}</span>
      <span class="history-copy"><strong>${escapeHtml(entry.package)}</strong><span>${escapeHtml(label)}</span></span>
      <span class="history-meta"><time>${date.toLocaleString(app.language === 'de' ? 'de-DE' : 'en-US', {dateStyle:'short', timeStyle:'short'})}</time><span>${entry.bytes ? formatBytes(entry.bytes) : ''}</span></span>
    </div>`;
  }).join('') : `<div class="history-empty">${t('history.empty')}</div>`;
}

async function openHistory() {
  $('historyModal').classList.remove('hidden');
  $('historyList').innerHTML = `<div class="history-empty">${t('history.loading')}</div>`;
  try {
    const entries = await callApi('get_history');
    renderHistoryEntries(entries);
  } catch (error) { $('historyList').innerHTML = `<div class="history-empty">${escapeHtml(error.message)}</div>`; }
}

function closeHistory() { $('historyModal').classList.add('hidden'); }

async function clearHistory() {
  try { await callApi('clear_history'); await openHistory(); toast(t('history.cleared')); }
  catch (error) { toast(error.message, true); }
}

let modelPoolData = null;
let modelPoolFilter = 'all';
let modelPoolSearch = '';

function categoryLabel(kind) {
  const key = `model_pool.categories.${kind}`;
  const translated = t(key);
  if (translated !== key) return translated;
  return kind ? kind.charAt(0).toUpperCase() + kind.slice(1) : 'Model';
}

async function openModelPool() {
  $('modelPoolModal').classList.remove('hidden');
  $('modelPoolList').innerHTML = `<div class="pool-model-empty">${t('history.loading')}</div>`;
  $('modelPoolSearchInput').value = modelPoolSearch = '';
  $('modelPoolSearchClear').classList.add('hidden');
  modelPoolFilter = 'all';
  await loadModelPoolData();
}

function closeModelPool() {
  $('modelPoolModal').classList.add('hidden');
}

async function loadModelPoolData() {
  try {
    modelPoolData = await callApi('inspect_model_pool');
    renderModelPool();
  } catch (err) {
    $('modelPoolList').innerHTML = `<div class="pool-model-empty">${escapeHtml(err.message || String(err))}</div>`;
  }
}

function selectWorkflowByPath(archivePath) {
  if (!archivePath || !app.state?.packages) return;
  const pkg = app.state.packages.find(item => item.path === archivePath || item.path.endsWith(archivePath));
  if (pkg) {
    app.selectedPath = pkg.path;
    renderLibrary();
    renderDetail(pkg);
    const activeEl = document.querySelector(`.workflow-item[data-path="${CSS.escape(pkg.path)}"]`);
    if (activeEl) activeEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }
}

function renderModelPool() {
  if (!modelPoolData) return;
  const { summary, categories, models } = modelPoolData;

  $('poolStatModels').textContent = summary.total_models || 0;
  const refCount = summary.total_references || 0;
  $('poolStatRefs').textContent = `${refCount} ${refCount === 1 ? 'Referenz' : (app.language === 'de' ? 'Referenzen' : 'references')}`;
  $('poolStatSize').textContent = formatBytes(summary.total_bytes || 0);
  $('poolStatDisk').textContent = app.language === 'de' ? 'im Speicherpool' : 'in storage pool';
  $('poolStatSaved').textContent = formatBytes(summary.saved_bytes || 0);
  const savedPct = summary.referenced_bytes > 0 ? Math.round((summary.saved_bytes / summary.referenced_bytes) * 100) : 0;
  $('poolStatSavedPercent').textContent = `${savedPct}% ${app.language === 'de' ? 'Deduplikationsrate' : 'deduplicated'}`;
  $('poolStatLoaded').textContent = summary.active_in_comfy_count || 0;
  $('poolStatLoadedSize').textContent = formatBytes(summary.active_in_comfy_bytes || 0);

  $('modelPoolPath').textContent = summary.pool_directory || summary.archive_root || '—';

  const totalCount = models ? models.length : 0;
  let catHtml = `
    <button class="pool-cat-chip ${modelPoolFilter === 'all' ? 'active' : ''}" data-cat="all">
      <span>${t('model_pool.all_categories')}</span>
      <span class="pool-cat-badge">${totalCount}</span>
    </button>
  `;
  for (const cat of (categories || [])) {
    const isAct = modelPoolFilter === cat.id;
    catHtml += `
      <button class="pool-cat-chip ${isAct ? 'active' : ''}" data-cat="${escapeHtml(cat.id)}">
        <span>${escapeHtml(categoryLabel(cat.id))}</span>
        <span class="pool-cat-badge">${cat.count}</span>
      </button>
    `;
  }
  const catContainer = $('modelPoolCategories');
  catContainer.innerHTML = catHtml;
  catContainer.querySelectorAll('.pool-cat-chip').forEach(btn => {
    btn.addEventListener('click', () => {
      modelPoolFilter = btn.dataset.cat;
      renderModelPool();
    });
  });

  const q = modelPoolSearch.trim().toLowerCase();
  const filtered = (models || []).filter(m => {
    if (modelPoolFilter !== 'all' && m.kind !== modelPoolFilter) return false;
    if (q) {
      const matchName = (m.filename || '').toLowerCase().includes(q);
      const matchKind = (m.kind || '').toLowerCase().includes(q);
      const matchSha = (m.sha256 || '').toLowerCase().includes(q);
      const matchWf = (m.workflows || []).some(w => (w.name || '').toLowerCase().includes(q));
      if (!matchName && !matchKind && !matchSha && !matchWf) return false;
    }
    return true;
  });

  if (!filtered.length) {
    $('modelPoolList').innerHTML = `<div class="pool-model-empty">${t('model_pool.no_models')}</div>`;
    return;
  }

  $('modelPoolList').innerHTML = filtered.map(m => {
    const kindClass = `pool-kind-${m.kind || 'model'}`;
    const statusClass = m.present_in_comfy ? 'active' : 'archived';
    const statusText = m.present_in_comfy ? t('model_pool.active_in_comfy') : t('model_pool.archived_in_pool');

    const wfCount = m.workflows?.length || 0;
    const wfListHtml = (m.workflows || []).map(w => {
      return `<button class="pool-wf-badge" data-archive="${escapeHtml(w.archive_path || '')}" title="${escapeHtml(w.name)}">${icon('workflow')}${escapeHtml(w.name)}</button>`;
    }).join('');

    const shortHash = m.short_hash || (m.sha256 ? m.sha256.substring(0, 10) : '');

    return `
      <div class="pool-model-card">
        <div class="pool-model-header">
          <div class="pool-model-title-wrap">
            <span class="pool-kind-badge ${kindClass}">${escapeHtml(categoryLabel(m.kind))}</span>
            <span class="pool-model-name" title="${escapeHtml(m.filename)}">${escapeHtml(m.filename)}</span>
          </div>
          <div class="pool-model-meta-right">
            <span class="pool-status-pill ${statusClass}">${statusText}</span>
            <span class="pool-model-size">${formatBytes(m.size || 0)}</span>
          </div>
        </div>
        <div class="pool-model-details">
          <div class="pool-model-workflows">
            <span>${wfCount === 1 ? t('model_pool.used_in_one') : t('model_pool.used_in', {count: wfCount})}:</span>
            ${wfListHtml}
          </div>
          <div class="pool-model-actions">
            ${shortHash ? `<button class="pool-action-icon-btn copy-hash-btn" data-hash="${escapeHtml(m.sha256)}" title="${t('model_pool.copy_hash')} (${shortHash})">${icon('shield')}</button>` : ''}
            <button class="pool-action-icon-btn copy-name-btn" data-filename="${escapeHtml(m.filename)}" title="${t('model_pool.copy_name')}">${icon('copy')}</button>
          </div>
        </div>
      </div>
    `;
  }).join('');

  $('modelPoolList').querySelectorAll('.pool-wf-badge').forEach(badge => {
    badge.addEventListener('click', (e) => {
      e.stopPropagation();
      const archivePath = badge.dataset.archive;
      if (archivePath) {
        closeModelPool();
        selectWorkflowByPath(archivePath);
      }
    });
  });

  $('modelPoolList').querySelectorAll('.copy-name-btn').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const fn = btn.dataset.filename;
      try {
        await navigator.clipboard.writeText(fn);
        toast(`${t('model_pool.copied')}: ${fn}`);
      } catch (_) {}
    });
  });

  $('modelPoolList').querySelectorAll('.copy-hash-btn').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const hash = btn.dataset.hash;
      try {
        await navigator.clipboard.writeText(hash);
        toast(`${t('model_pool.copied')} SHA-256`);
      } catch (_) {}
    });
  });
}

function selectedPackage() {
  return app.state?.packages?.find(item => item.path === app.selectedPath);
}

function showRenameError(message = '') {
  const validation = $('renameValidation');
  validation.textContent = message;
  validation.classList.toggle('hidden', !message);
}

function openRename() {
  const pkg = selectedPackage();
  if (!pkg) return;
  $('renameInput').value = pkg.name;
  showRenameError();
  $('renameModal').classList.remove('hidden');
  requestAnimationFrame(() => {
    $('renameInput').focus();
    $('renameInput').select();
  });
}

function closeRename() {
  $('renameModal').classList.add('hidden');
  showRenameError();
}

async function saveRename() {
  const pkg = selectedPackage();
  if (!pkg) { closeRename(); return; }
  const newName = $('renameInput').value.trim();
  if (!newName) { showRenameError(t('rename.required')); return; }

  const button = $('renameSaveButton');
  button.disabled = true;
  showRenameError();
  try {
    const result = await callApi('rename_archive', pkg.path, newName);
    renameCustomWorkflowKey(pkg.path, result.path);
    closeRename();
    await refreshState(result.path);
    toast(result.changed ? t('rename.done', {name: result.name}) : t('rename.unchanged'));
  } catch (error) {
    showRenameError(error.message);
    $('renameInput').focus();
  } finally {
    button.disabled = false;
  }
}

async function toggleHideSelected() {
  const pkg = selectedPackage();
  if (!pkg) return;
  if (pkg.state === 'loaded' || pkg.state === 'partial') {
    toast(t('detail.hide_loaded_prevent'), true);
    return;
  }
  try {
    const result = await callApi('toggle_hide_workflow', pkg.path);
    pkg.hidden = !!result.hidden;
    toast(t(result.hidden ? 'detail.hidden_toast' : 'detail.unhidden_toast', {name: pkg.name}));
    renderLibrary();
    renderDetail(pkg);
  } catch (error) {
    toast(error.message, true);
  }
}

let currentDeleteInspection = null;

async function openDeleteModal() {
  const pkg = selectedPackage();
  if (!pkg) return;

  $('deleteIntro').textContent = t('delete.intro', {
    name: pkg.name,
    size: formatBytes(pkg.archive_size || 0),
  });

  $('deleteLoadedWarning').classList.toggle('hidden', pkg.state !== 'loaded');
  $('deleteMethodTrash').checked = true;
  $('deleteOrphansOption').classList.add('hidden');
  $('deleteOrphansCheckbox').checked = false;
  currentDeleteInspection = null;

  $('deleteModal').classList.remove('hidden');

  try {
    const inspection = await callApi('inspect_archive_deletion', pkg.path);
    currentDeleteInspection = inspection;
    if (inspection && inspection.orphaned_models && inspection.orphaned_models.length > 0) {
      $('deleteOrphansLabel').textContent = t('delete.clean_pool', {
        count: inspection.orphaned_models.length,
        size: formatBytes(inspection.orphaned_bytes || 0),
      });
      $('deleteOrphansOption').classList.remove('hidden');
      $('deleteOrphansCheckbox').checked = true;
    }
  } catch (_) {}
}

function closeDeleteModal() {
  $('deleteModal').classList.add('hidden');
  currentDeleteInspection = null;
}

async function confirmDeleteArchive() {
  const pkg = selectedPackage();
  if (!pkg) { closeDeleteModal(); return; }

  const sendToTrash = $('deleteMethodTrash').checked;
  const deleteOrphans = !$('deleteOrphansOption').classList.contains('hidden') && $('deleteOrphansCheckbox').checked;

  const button = $('deleteConfirmButton');
  button.disabled = true;
  try {
    const result = await callApi('delete_archive', pkg.path, sendToTrash, deleteOrphans);
    removeCustomWorkflowKey(pkg.path);
    closeDeleteModal();
    renderDetail(null);
    await refreshState();
    toast(sendToTrash ? t('delete.success_trash', {name: result.name}) : t('delete.success_permanent', {name: result.name}));
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

function setMaximizedButton(maximized) {
  const button = $('maximizeButton');
  const label = t(maximized ? 'window.restore' : 'window.maximize');
  button.querySelector('use').setAttribute('href', maximized ? '#i-restore' : '#i-max');
  button.setAttribute('aria-label', label);
  button.setAttribute('title', label);
  button.setAttribute('aria-pressed', String(maximized));
}

async function toggleWindowMaximize() {
  const api = window.pywebview?.api?.toggle_maximize_window;
  if (!api) return;
  const button = $('maximizeButton');
  button.disabled = true;
  try { setMaximizedButton(await api()); }
  finally { button.disabled = false; }
}

function openConfirm(options) {
  app.pendingConfirm = options;
  document.querySelector('.confirm-modal').classList.toggle('wide', !!options.wide);
  $('confirmEyebrow').textContent = options.eyebrow;
  $('confirmTitle').textContent = options.title;
  $('confirmCopy').innerHTML = options.copy;
  $('confirmCopy').scrollTop = 0;
  $('confirmCancelButton').classList.toggle('hidden', !!options.readOnly);
  $('confirmIcon').classList.toggle('danger', !!options.danger);
  $('confirmIcon').innerHTML = icon(options.danger ? 'alert' : 'shield');
  $('confirmProceedButton').textContent = options.proceedLabel;
  $('confirmProceedButton').disabled = !!options.disabled;
  $('incompleteOption').classList.toggle('hidden', !options.showIncomplete);
  $('sharedOption').classList.toggle('hidden', !options.showShared);
  $('allowIncompleteCheckbox').checked = false;
  $('allowSharedCheckbox').checked = false;
  $('confirmModal').classList.remove('hidden');
}
function closeConfirm() { $('confirmModal').classList.add('hidden'); app.pendingConfirm = null; }
async function proceedConfirm() {
  if (!app.pendingConfirm) return;
  if (app.pendingConfirm.showIncomplete && !$('allowIncompleteCheckbox').checked) {
    toast(t('confirm.incomplete_required'), true); return;
  }
  if (app.pendingConfirm.showShared && !$('allowSharedCheckbox').checked) {
    toast(t('confirm.shared_required'), true); return;
  }
  const action = app.pendingConfirm.action;
  const flags = {allowIncomplete: $('allowIncompleteCheckbox').checked, allowShared: $('allowSharedCheckbox').checked};
  closeConfirm();
  try { await action(flags); } catch (error) { hideBusy(); operationError(error); }
}

function installEvents() {
  $('copyWorkflowButton')?.addEventListener('click', () => copySelectedWorkflowJson());
  $('openWebUiButton')?.addEventListener('click', openComfyWebUi);
  $('showWorkflowFileButton').addEventListener('click', showSelectedWorkflowFile);
  $('dependencyScanButton').addEventListener('click', scanSelectedDependencies);
  $('exportButton').addEventListener('click', exportSelectedPackage);
  $('minimizeButton').addEventListener('click', () => window.pywebview?.api?.minimize_window());
  $('maximizeButton').addEventListener('click', toggleWindowMaximize);
  $('closeButton').addEventListener('click', () => window.pywebview?.api?.close_window());
  document.querySelector('.titlebar-drag').addEventListener('dblclick', toggleWindowMaximize);
  $('refreshButton').addEventListener('click', () => refreshState());
  $('diskRefreshButton').addEventListener('click', refreshDiskState);
  $('historyButton').addEventListener('click', openHistory);
  $('historyCloseButton').addEventListener('click', closeHistory);
  $('historyDoneButton').addEventListener('click', closeHistory);
  $('clearHistoryButton').addEventListener('click', clearHistory);
  $('modelPoolButton')?.addEventListener('click', openModelPool);
  $('modelPoolFooterButton')?.addEventListener('click', openModelPool);
  $('modelPoolCloseButton')?.addEventListener('click', closeModelPool);
  $('modelPoolDoneButton')?.addEventListener('click', closeModelPool);
  $('modelPoolModal')?.addEventListener('click', event => {
    if (event.target === $('modelPoolModal')) closeModelPool();
  });
  $('modelPoolSearchInput')?.addEventListener('input', event => {
    modelPoolSearch = event.target.value;
    $('modelPoolSearchClear')?.classList.toggle('hidden', !modelPoolSearch);
    renderModelPool();
  });
  $('modelPoolSearchClear')?.addEventListener('click', () => {
    $('modelPoolSearchInput').value = modelPoolSearch = '';
    $('modelPoolSearchClear').classList.add('hidden');
    renderModelPool();
  });
  $('renameButton').addEventListener('click', openRename);
  $('hideWorkflowButton')?.addEventListener('click', toggleHideSelected);
  $('renameCloseButton').addEventListener('click', closeRename);
  $('renameCancelButton').addEventListener('click', closeRename);
  $('renameSaveButton').addEventListener('click', saveRename);
  $('renameInput').addEventListener('input', () => showRenameError());
  $('renameInput').addEventListener('keydown', event => {
    if (event.key === 'Enter') { event.preventDefault(); saveRename(); }
    if (event.key === 'Escape') closeRename();
  });
  $('deletePackageButton').addEventListener('click', openDeleteModal);
  $('deleteCloseButton').addEventListener('click', closeDeleteModal);
  $('deleteCancelButton').addEventListener('click', closeDeleteModal);
  $('deleteConfirmButton').addEventListener('click', confirmDeleteArchive);
  $('heroCover')?.addEventListener('click', openLightbox);
  $('lightboxCloseButton')?.addEventListener('click', closeLightbox);
  $('lightboxModal')?.addEventListener('click', event => {
    if (event.target === $('lightboxModal')) closeLightbox();
  });
  $('tourHelpButton')?.addEventListener('click', () => openTour(true));
  $('tourCloseButton')?.addEventListener('click', closeTour);
  $('tourSkipButton')?.addEventListener('click', closeTour);
  $('tourNextButton')?.addEventListener('click', nextTourStep);
  $('tourPrevButton')?.addEventListener('click', prevTourStep);
  $('tourModal')?.addEventListener('click', event => {
    if (event.target === $('tourModal')) closeTour();
  });
  window.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !$('lightboxModal')?.classList.contains('hidden')) {
      closeLightbox();
    }
    if (event.key === 'Escape' && !$('tourModal')?.classList.contains('hidden')) {
      closeTour();
    }
    if (event.key === 'Escape' && !$('modelPoolModal')?.classList.contains('hidden')) {
      closeModelPool();
    }
  });
  $('settingsButton').addEventListener('click', () => openSettings(false));
  $('settingsCloseButton').addEventListener('click', closeSettings);
  $('settingsCancelButton').addEventListener('click', closeSettings);
  $('browseComfyButton').addEventListener('click', () => browseFolder('comfy'));
  $('browseArchiveButton').addEventListener('click', () => browseFolder('archive'));
  $('saveSettingsButton').addEventListener('click', saveSettings);
  $('openLogButton').addEventListener('click', async () => { try { await callApi('open_diagnostic_log'); } catch (e) { toast(e.message, true); } });
  $('optimizeLibraryButton').addEventListener('click', optimizeEntireLibrary);
  document.querySelectorAll('.language-toggle button').forEach(button => button.addEventListener('click', () => changeLanguage(button.dataset.language)));
  $('addWorkflowButton').addEventListener('click', addWorkflow);
  $('importPackageButton').addEventListener('click', importPackage);
  $('emptyAddButton').addEventListener('click', addWorkflow);
  $('verifyButton').addEventListener('click', verifySelected);
  $('confirmCancelButton').addEventListener('click', closeConfirm);
  $('confirmProceedButton').addEventListener('click', proceedConfirm);
  $('noticeList').addEventListener('click', copyMissingFilename);
  $('noticeList').addEventListener('click', handleDownloadModelClick);
  $('confirmCopy').addEventListener('click', copyMissingFilename);
  $('confirmCopy').addEventListener('click', handleDownloadModelClick);
  $('downloadCloseButton')?.addEventListener('click', closeDownloadModal);
  $('downloadCancelButton')?.addEventListener('click', closeDownloadModal);
  $('downloadStartButton')?.addEventListener('click', startModelDownload);
  $('downloadSourceInput')?.addEventListener('input', () => {
    $('downloadStartButton').disabled = !$('downloadSourceInput').value.trim();
  });
  $('downloadCandidatesList')?.addEventListener('click', event => {
    if (event.target.closest?.('[data-open-url]')) {
      return;
    }
    const pill = event.target.closest?.('.download-candidate-pill');
    if (!pill) return;
    document.querySelectorAll('.download-candidate-pill').forEach(p => p.classList.remove('selected'));
    pill.classList.add('selected');
    $('downloadSourceInput').value = pill.dataset.candidateUrl || '';
    if (activeDownloadContext) {
      activeDownloadContext.expectedHash = pill.dataset.candidateHash || '';
    }
    if ($('downloadOpenWebBtn') && pill.dataset.candidateRepo) {
      $('downloadOpenWebBtn').dataset.openUrl = `https://huggingface.co/${pill.dataset.candidateRepo}`;
      $('downloadOpenWebText').textContent = t('download.view_model_card');
    }
    $('downloadStartButton').disabled = false;
  });
  window.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !$('downloadModal')?.classList.contains('hidden')) {
      closeDownloadModal();
    }
  });
  document.body.addEventListener('click', event => {
    const button = event.target.closest?.('[data-open-url]');
    if (button) {
      openExternalUrlAction(event);
    }
  });
  $('customNodeList')?.addEventListener('click', event => {
    if (event.target.closest?.('[data-copy-custom-node]')) {
      copyCustomNodePackage(event);
    }
  });
  $('openArchiveButton').addEventListener('click', async () => { try { await callApi('open_archive_folder'); } catch (e) { toast(e.message, true); } });
  $('copyLogsButton').addEventListener('click', async () => {
    const text = [...$('processLog').querySelectorAll('.process-log-line')].map(row => row.textContent.trim()).join('\n');
    try { await navigator.clipboard.writeText(text); toast(t('progress.copied')); } catch (_) { toast(t('progress.copy_failed'), true); }
  });
  $('cancelOperationButton').addEventListener('click', async () => {
    const button = $('cancelOperationButton');
    button.disabled = true;
    button.textContent = t('progress.cancel_pending');
    appendProcessLog(t('progress.cancel_requested'), 'info');
    try { await window.pywebview?.api?.cancel_operation(); } catch (_) {}
  });
  document.querySelectorAll('.filter-chip').forEach(chip => chip.addEventListener('click', () => {
    document.querySelectorAll('.filter-chip').forEach(item => item.classList.remove('active'));
    chip.classList.add('active'); app.filter = chip.dataset.filter; renderLibrary();
  }));

  document.querySelectorAll('.search-scope-chip').forEach(chip => chip.addEventListener('click', () => {
    document.querySelectorAll('.search-scope-chip').forEach(item => item.classList.remove('active'));
    chip.classList.add('active');
    app.searchScope = chip.dataset.scope || 'all';
    renderLibrary();
  }));

  const searchInput = $('workflowSearchInput');
  const searchClear = $('searchClearButton');
  if (searchInput) {
    searchInput.addEventListener('input', () => {
      app.searchQuery = searchInput.value;
      if (searchClear) searchClear.classList.toggle('hidden', !searchInput.value);
      renderLibrary();
    });
    searchInput.addEventListener('keydown', event => {
      if (event.key === 'Escape') {
        searchInput.value = '';
        app.searchQuery = '';
        if (searchClear) searchClear.classList.add('hidden');
        renderLibrary();
        searchInput.blur();
      }
    });
  }
  if (searchClear) {
    searchClear.addEventListener('click', () => {
      if (searchInput) {
        searchInput.value = '';
        searchInput.focus();
      }
      app.searchQuery = '';
      searchClear.classList.add('hidden');
      renderLibrary();
    });
  }
  window.addEventListener('keydown', event => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'f') {
      const activeTag = document.activeElement?.tagName;
      if (activeTag !== 'INPUT' && activeTag !== 'TEXTAREA') {
        event.preventDefault();
        searchInput?.focus();
        searchInput?.select();
      }
    }
  });

  $('workflowSortButton')?.addEventListener('click', event => {
    event.stopPropagation();
    toggleSortDropdown();
  });
  document.querySelectorAll('.sort-option').forEach(option => {
    option.addEventListener('click', event => {
      event.stopPropagation();
      const nextMode = option.dataset.sort;
      if (nextMode && nextMode !== app.sortMode) {
        app.sortMode = nextMode;
        setStorage('comfyshelf_workflow_sort_mode', nextMode);
        updateSortDropdownUI();
        renderLibrary();
      }
      closeSortDropdown();
    });
  });
  document.addEventListener('click', event => {
    if (!event.target.closest?.('#workflowSortContainer')) {
      closeSortDropdown();
    }
  });
  updateSortDropdownUI();

  let dragCounter = 0;
  const dropOverlay = $('dropOverlay');
  window.addEventListener('dragenter', event => {
    if (app.isDraggingWorkflow || !event.dataTransfer?.types?.includes('Files')) return;
    event.preventDefault();
    dragCounter++;
    if (dropOverlay) {
      dropOverlay.classList.remove('hidden');
      dropOverlay.classList.add('active');
    }
  });
  window.addEventListener('dragover', event => {
    if (app.isDraggingWorkflow || !event.dataTransfer?.types?.includes('Files')) return;
    event.preventDefault();
    if (event.dataTransfer) {
      event.dataTransfer.dropEffect = 'copy';
    }
  });
  window.addEventListener('dragleave', event => {
    if (app.isDraggingWorkflow || !event.dataTransfer?.types?.includes('Files')) return;
    event.preventDefault();
    dragCounter--;
    if (dragCounter <= 0) {
      dragCounter = 0;
      if (dropOverlay) {
        dropOverlay.classList.add('hidden');
        dropOverlay.classList.remove('active');
      }
    }
  });
  window.addEventListener('drop', async event => {
    if (app.isDraggingWorkflow) {
      app.isDraggingWorkflow = false;
      app.draggedWorkflowPath = null;
      return;
    }
    if (!event.dataTransfer?.types?.includes('Files')) return;
    event.preventDefault();
    dragCounter = 0;
    if (dropOverlay) {
      dropOverlay.classList.add('hidden');
      dropOverlay.classList.remove('active');
    }
    const files = event.dataTransfer?.files;
    if (files && files.length > 0) {
      await handleDroppedFile(files[0]);
    }
  });
}

function installResizeHandles() {
  let drag = null;
  let pending = false;
  let latestSize = null;

  async function applyLatestSize() {
    if (pending || !latestSize || !window.pywebview?.api?.resize_window) return;
    pending = true;
    const next = latestSize;
    latestSize = null;
    try { await window.pywebview.api.resize_window(next.width, next.height, next.edge); }
    finally {
      pending = false;
      if (latestSize) requestAnimationFrame(applyLatestSize);
    }
  }

  document.querySelectorAll('.resize-handle').forEach(handle => {
    handle.addEventListener('pointerdown', event => {
      if (event.button !== 0) return;
      handle.setPointerCapture(event.pointerId);
      drag = {
        edge: handle.dataset.edge,
        startX: event.screenX,
        startY: event.screenY,
        width: window.innerWidth,
        height: window.innerHeight,
      };
      event.preventDefault();
    });
    handle.addEventListener('pointermove', event => {
      if (!drag || !handle.hasPointerCapture(event.pointerId)) return;
      const dx = event.screenX - drag.startX;
      const dy = event.screenY - drag.startY;
      latestSize = {
        edge: drag.edge,
        width: drag.width + (drag.edge.includes('w') ? -dx : drag.edge.includes('e') ? dx : 0),
        height: drag.height + (drag.edge.includes('n') ? -dy : drag.edge.includes('s') ? dy : 0),
      };
      requestAnimationFrame(applyLatestSize);
    });
    const stop = event => {
      if (handle.hasPointerCapture(event.pointerId)) handle.releasePointerCapture(event.pointerId);
      drag = null;
    };
    handle.addEventListener('pointerup', stop);
    handle.addEventListener('pointercancel', stop);
  });
}

function renderDemo() {
  app.state = {
    configured: true,
    installation: {name:'Example ComfyUI', comfy_root:'C:\\AI\\ComfyUI', archive_root:'E:\\WorkflowArchive'},
    validation: {comfy_free: 18930000000},
    packages: [{name:'Minimax Music 3 — Text to Music', path:'E:\\WorkflowArchive\\Minimax Music 3.zip', archive_size:9427000000, complete:false, state:'archived', present_models:0, model_count:3, model_bytes:22016000000, modified:1,
      models:[
        {filename:'minimax_music3_dit_fp16.safetensors',kind:'diffusion_model',size:12600000000,present:false},
        {filename:'minimax_music3_text_encoder_pruned_int8_convrot.safetensors',kind:'text_encoder',size:9200000000,present:false},
        {filename:'minimax_music3_dav.safetensors',kind:'vae',size:216000000,present:false}],
      missing:[{kind:'diffusion_model',name:'minimax_music3_dit_fp16.safetensors'}], custom_nodes:[{package:'ComfyUI-Pixaroma'}], workflow:{}}]
  };
  renderSummary(); app.selectedPath = app.state.packages[0].path; renderLibrary(); renderDetail(app.state.packages[0]);
}

installEvents();
installResizeHandles();
const demoParams = new URLSearchParams(location.search);
if (demoParams.has('demo')) {
  const demoLanguage = demoParams.get('lang') === 'de' ? 'de' : 'en';
  fetch(`locales/${demoLanguage}.json`).then(response => response.json()).then(messages => {
  installLocale({language: demoLanguage, messages});
  renderDemo();
  if (demoParams.has('loaded')) {
    app.state.packages[0].state = 'loaded';
    app.state.packages[0].present_models = app.state.packages[0].model_count;
    renderLibrary();
    renderDetail(app.state.packages[0]);
  }
  if (demoParams.has('workflowfile')) {
    // Browser-only fixture: no native bridge and no filesystem changes.
    window.pywebview = {api:{show_workflow_file:async () => ({ok:true, data:{
      exists:false, token:'demo-json',
      path:'J:/ComfyUI/user/default/workflows/My music collection/Minimax Music 3 - Text to Music.json',
    }})}};
    showSelectedWorkflowFile();
  }
  if (demoParams.has('dependencies')) {
    // Browser-only UI fixture. It does not connect to files or the native API.
    window.pywebview = {api:{scan_archive_dependencies:async () => ({ok:true, data:{
      token:'demo', counts:{included:1, found:2, missing:1, ambiguous:1},
      space_required:1048576, space_free:48000000000, warnings:[], parser_warnings:[], rows:[
        {name:'qwen_image_vae.safetensors', kind:'vae', state:'included'},
        {name:'qwen_2.5_vl_7b_fp8_scaled.safetensors', kind:'text_encoder', state:'found', source:'pool', pool:{package:'Qwen Image Edit.zip'}},
        {name:'Qwen-Image-2512-Lightning-4steps-V1.0-fp32.safetensors', kind:'lora', state:'found', source:'local', local:{path:'J:/ComfyUI/models/loras/Qwen-Image-2512-Lightning-4steps-V1.0-fp32.safetensors'}},
        {name:'qwen_image_2512_fp8_e4m3fn.safetensors', kind:'diffusion_model', state:'missing'},
        {name:'portraits/identity-detail.safetensors', kind:'lora', state:'ambiguous', candidates:['Portrait collection.zip: aaaa…', 'Alternative portraits.zip: bbbb…']},
      ],
    }})}};
    scanSelectedDependencies();
  }
  if (demoParams.has('loading')) {
    app.state.loading = true;
    app.state.packages = [];
    app.scanProgress = {stage:'package', current:1, total:2, package:'Example_Image_Workflow.zip'};
    applyState(app.state, null);
  }
  if (demoParams.has('maximized')) setMaximizedButton(true);
  if (demoParams.has('verification')) {
    const pkg = selectedPackage();
    const value = demoParams.get('verification');
    pkg.verification = {state:['verified', 'invalid', 'stale'].includes(value) ? value : 'unknown',
      checked_at:'2026-09-03T10:15:00Z', persisted:true};
    renderDetail(pkg);
  }
  if (demoParams.has('features')) {
    app.state.packages.push({
      name:'Qwen Image Edit', path:'E:\\WorkflowArchive\\Qwen Image Edit.zip',
      archive_size:16300000000, complete:true, state:'loaded', present_models:2,
      model_count:2, model_bytes:16300000000, modified:0,
      models:[{filename:'qwen_image_edit_fp8.safetensors',kind:'diffusion_model',size:16300000000,present:true}],
      missing:[], custom_nodes:[], workflow:{}
    });
    renderLibrary();
    renderDetail(app.state.packages[0]);
  }
  if (demoParams.has('long')) {
    const pkg = app.state.packages[0];
    pkg.missing = [{kind:'lora', name:'Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors', reason:'nicht gefunden'}];
    pkg.complete = false;
    renderDetail(pkg);
  }
  if (demoParams.has('parser')) {
    const pkg = app.state.packages[0];
    pkg.parser_warnings = [
      {code:'heuristic', node_type:'ExampleCustomLoader', node_id:'12', field:'widgets_values[0]', filename:'minimax_music3_dit_fp16.safetensors'},
      {code:'path-mismatch', node_type:'VAELoader', node_id:'14', field:'widgets_values[0]', filename:'audio/alternative/minimax_music3_dav.safetensors'},
    ];
    renderDetail(pkg);
  }
  if (demoParams.has('removal')) {
    const remaining = Array.from({length:12}, (_, i) => `C:\\AI\\ComfyUI\\models\\loras\\long-subfolder\\example-model-${i + 1}.safetensors`);
    operationError({code:'removal_incomplete', details:{
      removed:['C:\\AI\\ComfyUI\\models\\vae\\previous-model.safetensors'], remaining,
      removal_issues:[{path:remaining[0], code:'file_locked'}],
      restore:{dry_run:false, restored:['target-model.safetensors']},
    }});
  }
  if (demoParams.has('export')) {
    const pkg = selectedPackage();
    const incomplete = demoParams.has('long');
    showPortableExportPreview(pkg, {
      output_path: 'E:\\Portable Workflows\\Workflows to share with friends\\Minimax Music 3 - Text to Music - portable.zip',
      complete: !incomplete, missing: incomplete ? pkg.missing : [], blockers: [],
      bytes_required: 22240000000, bytes_free: 48200000000,
    });
  }
  if (demoParams.has('shared')) {
    const pkg = selectedPackage();
    const references = Array.from({length: demoParams.has('long') ? 8 : 2}, (_, index) =>
      `C:\\AI\\ComfyUI\\user\\default\\workflows\\Music collection\\Another music workflow ${index + 1}.json`);
    const candidates = pkg.models.slice(0, 2).map(model => ({
      path: `C:\\AI\\ComfyUI\\models\\audio\\${model.filename}`, shared_by: references,
    }));
    pkg.models[0].library_shared_by = references.map((path, index) => ({name: `Music workflow ${index + 1}`, path: `E:\\Archive\\Music workflow ${index + 1}.zip`}));
    pkg.models[0].archive_path = `models/diffusion_models/${pkg.models[0].filename}`;
    renderDetail(pkg);
    if (demoParams.get('shared') !== 'library') openConfirm({
      eyebrow:t('restore.eyebrow'), title:t('restore.unload_title'), wide:true, danger:true,
      copy:sharedFilesHtml(candidates), showShared:true, showIncomplete:demoParams.has('long'),
      proceedLabel:t('restore.unload_proceed', {count:2}), action:async () => {},
    });
  }
  if (demoParams.has('history')) {
    renderHistoryEntries([
      {timestamp:new Date().toISOString(), action:'switch', package:'Minimax Music 3 — Text to Music', detail:'Workflow gewechselt: 2 alte Dateien entladen', bytes:28600000000},
      {timestamp:new Date(Date.now()-3600000).toISOString(), action:'import_load', package:'Qwen Image Edit', detail:'Paket importiert und in ComfyUI geladen', bytes:16300000000},
      {timestamp:new Date(Date.now()-86400000).toISOString(), action:'archive', package:'Example Image Workflow', detail:'Workflow-Paket erstellt', bytes:31900000000}
    ]);
    $('historyModal').classList.remove('hidden');
  } else if (demoParams.has('progress')) {
    showBusy(t('pack.running'), t('pack.running_detail'));
    window.workflowProgress({phase:'pack', log:true});
    window.workflowProgress({phase:'pack', file:'minimax_music3_text_encoder_pruned_int8_convrot.safetensors', current:5140000000, total:9200000000, log:true});
  } else if (demoParams.has('import')) {
    openConfirm({
      eyebrow:t('import.eyebrow'), title:t('import.title'), danger:false,
      wide:true, proceedLabel:t('import.proceed'), showIncomplete:false,
      copy:`<p>${t('import.copy', {package:'Minimax Music 3 — Text to Music'})}</p>
        <div class="import-chain">
          <div class="import-chain-row">${icon('check')}<span>${t('import.paths_ok')}<small>${t('import.paths_hint')}</small></span></div>
          <div class="import-chain-row">${icon('check')}<span>${t('import.models_matched',{matched:3,total:3})}<small>${t('import.models_hint')}</small></span></div>
          <div class="import-chain-row">${icon('shield')}<span>${t('import.manifest')}<small>${t('import.manifest_hint')}</small></span></div>
        </div>
        <div class="confirm-stat"><span>${t('import.extras')}</span><strong>0</strong></div>
        <div class="confirm-stat"><span>${t('common.target')}</span><strong>E:\\WorkflowArchive\\Minimax Music 3.zip</strong></div>
        <p>${t('import.replace_selected')}</p>
        <div class="destination-heading"><span>${t('import.after')}</span><small>${t('import.choose_state')}</small></div>
        <div class="import-destinations">
          <label class="import-destination"><input type="radio" name="importDestination" checked><span class="destination-icon">${icon('shelf')}</span><span class="destination-copy"><strong>${t('import.seal_archive')}</strong><small>${t('import.seal_archive_hint')}</small><em>${t('import.temp_free',{needed:'13.34 GiB',free:'44.27 GiB'})}</em></span></label>
          <label class="import-destination"><input type="radio" name="importDestination"><span class="destination-icon">${icon('drive')}</span><span class="destination-copy"><strong>${t('import.seal_load')}</strong><small>${t('import.seal_load_hint')}</small><em>${t('import.required_present',{required:'13.34 GiB',present:0})}</em></span></label>
        </div>`
    });
  }
  });
} else {
  beginNativeBootstrap();
}
