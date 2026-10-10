// Functional tests of existing frontend functions without a native window.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.join(__dirname, '..', 'gui');
const code = fs.readFileSync(path.join(root, 'app.js'), 'utf8').split('\ninstallEvents();')[0];

async function run(language) {
  const context = vm.createContext({window: {}, console, setTimeout: () => 1, clearTimeout() {}, clearInterval() {}});
  vm.runInContext(code, context);
  context.messages = JSON.parse(fs.readFileSync(path.join(root, 'locales', `${language}.json`), 'utf8'));
  vm.runInContext(`
    app.messages = messages;
    app.language = ${JSON.stringify(language)};
    var refreshed = 0;
    var confirmation = null;
    refreshState = async () => { refreshed += 1; return true; };
    openConfirm = options => { confirmation = options; };
    showBusy = () => {};
    hideBusy = () => {};
  `, context);
  context.response = {ok: false, error_code: 'removal_incomplete', details: {
    removed: ['C:/Comfy/models/old.safetensors'], remaining: ['C:/Comfy/models/<locked>.safetensors'],
    removal_issues: [{path: 'C:/Comfy/models/<locked>.safetensors', code: 'file_locked'}],
    restore: {dry_run: false, restored: ['new.safetensors']},
  }};
  await vm.runInContext(`
    window.pywebview = {api: {execute_action: async () => response}};
    callApi('execute_action').catch(error => operationError(error));
  `, context);
  assert.equal(context.refreshed, 1);
  assert.ok(context.confirmation.copy.includes(context.messages['removal.target_loaded']));
  assert.ok(context.confirmation.copy.includes(context.messages['removal.file_locked']));
  assert.ok(context.confirmation.copy.includes('&lt;locked&gt;'));
  assert.ok(!context.confirmation.copy.includes('<locked>'));
  assert.equal(context.confirmation.proceedLabel, context.messages['common.close']);

  context.preview = {candidates: [], conflicts: [], bytes_reclaimable: 0,
    removal_issues: [{path: 'C:/blocked.safetensors', code: 'read_only'}]};
  await vm.runInContext(`
    app.state = {packages: [{path:'example.zip', complete:true}]};
    app.selectedPath = 'example.zip';
    window.pywebview.api.preview_action = async () => ({ok:true, data:preview});
    previewPackageAction('unload');
  `, context);
  assert.equal(context.confirmation.disabled, true);
  assert.ok(context.confirmation.copy.includes('C:/blocked.safetensors'));
  assert.ok(context.confirmation.copy.includes(context.messages['removal.read_only']));

  // Local removal warnings expose exact model-to-workflow references, not pool metadata.
  context.sharedCandidate = {path:'C:/Comfy/models/vae/<shared>.safetensors',
    shared_by:['C:/Comfy/user/default/workflows/<Music>.json', 'c:\\comfy\\user\\default\\workflows\\<Music>.json',
      'C:/Comfy/user/second/workflows/<Music>.json']};
  context.preview = {candidates: [context.sharedCandidate], conflicts: [], bytes_reclaimable: 42};
  await vm.runInContext("previewPackageAction('unload')", context);
  assert.equal(context.confirmation.showShared, true);
  assert.equal(context.confirmation.wide, true);
  assert.equal(context.confirmation.disabled, false);
  assert.ok(context.confirmation.copy.includes('sharing-warning'));
  assert.ok(context.confirmation.copy.includes(context.messages['sharing.warning_title']));
  assert.ok(context.confirmation.copy.includes('&lt;shared&gt;.safetensors'));
  assert.ok(context.confirmation.copy.includes('&lt;Music&gt;'));
  assert.ok(!context.confirmation.copy.includes('<Music>'));
  assert.ok(context.confirmation.copy.includes('<details class="sharing-details">'));
  assert.ok(context.confirmation.copy.includes(context.messages['sharing.counts'].replace('{files}', 1).replace('{workflows}', 2)));
  assert.equal(vm.runInContext('sharedFilesHtml([])', context), '');
  assert.equal(vm.runInContext('sharedFilesHtml([sharedCandidate], {library:true})', context), '');
  const librarySharing = vm.runInContext("sharedFilesHtml([{filename:'shared.safetensors', library_shared_by:[{name:'<Renamed>',path:'E:/Archive/<Renamed>.zip'}]}], {library:true})", context);
  assert.ok(librarySharing.includes('sharing-library'));
  assert.ok(librarySharing.includes('&lt;Renamed&gt;'));
  assert.ok(librarySharing.includes(context.messages['sharing.library_scope']));
  await vm.runInContext("previewPackageAction('restore')", context);
  assert.equal(context.confirmation.showShared, false);
  assert.ok(!context.confirmation.copy.includes('sharing-warning'));
  context.switchPreview = {unload_candidates:[context.sharedCandidate], shared_count:1, conflicts:[],
    blockers:[], restore:{conflicts:[]}, source_packages:['Previous'], bytes_required:50, bytes_reclaimable:42};
  await vm.runInContext(`
    window.pywebview.api.preview_switch = async () => ({ok:true, data:switchPreview});
    previewWorkflowSwitch();
  `, context);
  assert.equal(context.confirmation.showShared, true);
  assert.equal(context.confirmation.wide, true);
  assert.ok(context.confirmation.copy.includes('sharing-warning'));
  assert.ok(context.confirmation.copy.includes('&lt;shared&gt;'));
  context.switchPreview.unload_candidates = [];
  await vm.runInContext('previewWorkflowSwitch()', context);
  assert.equal(context.confirmation.showShared, true);
  assert.ok(context.confirmation.copy.includes(context.messages['sharing.details_unavailable']));
  context.switchPreview.shared_count = 0;
  await vm.runInContext('previewWorkflowSwitch()', context);
  assert.equal(context.confirmation.showShared, false);
  assert.ok(!context.confirmation.copy.includes('sharing-warning'));

  const html = vm.runInContext(`parserWarningsHtml([{code:'heuristic', node_id:'9', node_type:'MyLoader', field:'properties.file', filename:'<model>.safetensors'}])`, context);
  assert.ok(html.includes('&lt;model&gt;'));
  assert.ok(html.includes('MyLoader #9 properties.file'));
  assert.ok(html.includes(context.messages['parser.heuristic']));

  // Sidebar diagnostics are grouped by full file reference, not basename.
  context.groupedWarnings = [
    {code:'heuristic', node_id:'20', node_type:'SAMLoader', field:'widgets_values[0]', filename:'sam_vit_b_01ec64.pth', resolved_path:'J:/Comfy/models/sams/sam_vit_b_01ec64.pth'},
    {code:'basename-fallback', node_id:'20', node_type:'SAMLoader', field:'widgets_values[0]', filename:'sam_vit_b_01ec64.pth', resolved_path:'J:/Comfy/models/sams/sam_vit_b_01ec64.pth'},
    {code:'heuristic', node_id:'21', node_type:'SAMLoader', field:'widgets_values[0]', filename:'sam_vit_b_01ec64.pth', resolved_path:'J:/Comfy/models/sams/sam_vit_b_01ec64.pth'},
  ];
  const warningsBefore = JSON.stringify(context.groupedWarnings);
  const groupedHtml = vm.runInContext('parserNoticesHtml(groupedWarnings)', context);
  assert.equal((groupedHtml.match(/class="notice warning parser-notice"/g) || []).length, 1);
  assert.equal((groupedHtml.match(/<li>/g) || []).length, 2);
  assert.equal((groupedHtml.match(/SAMLoader #20 widgets_values\[0\]/g) || []).length, 1);
  assert.ok(groupedHtml.includes('SAMLoader #21 widgets_values[0]'));
  assert.ok(groupedHtml.includes(context.messages['parser.heuristic']));
  assert.ok(groupedHtml.includes(context.messages['parser.basename-fallback']));
  assert.equal(JSON.stringify(context.groupedWarnings), warningsBefore);
  assert.equal(vm.runInContext('parserNoticesHtml()', context), '');
  context.separateWarnings = [
    {code:'heuristic', filename:'folderA/model.safetensors'},
    {code:'heuristic', filename:'folderB/model.safetensors'},
    {code:'heuristic', filename:'same.safetensors', resolved_path:'C:/one/same.safetensors'},
    {code:'heuristic', filename:'same.safetensors', resolved_path:'C:/two/same.safetensors'},
    {code:'unresolved-loader', node_id:1, node_type:'Loader', field:''},
    {code:'unresolved-loader', node_id:2, node_type:'Loader', field:''},
  ];
  assert.equal(vm.runInContext('groupParserWarnings(separateWarnings).length', context), 6);
  context.slashWarnings = [{code:'heuristic', filename:'folder\\model.safetensors'}, {code:'path-mismatch', filename:'FOLDER/model.safetensors'}];
  assert.equal(vm.runInContext('groupParserWarnings(slashWarnings).length', context), 1);
  const escapedNotice = vm.runInContext(`parserNoticesHtml([{filename:'<model>.pth', code:'unknown', node_id:'<id>', node_type:'<Loader>', field:'<field>', resolved_path:'C:/<path>'}])`, context);
  for (const field of ['model', 'id', 'Loader', 'field', 'path']) {
    assert.ok(escapedNotice.includes('&lt;' + field + '&gt;'));
    assert.ok(!escapedNotice.includes('<' + field + '>'));
  }
  assert.ok(escapedNotice.includes(context.messages['parser.review']));

  // Search-friendly names retain the extension and spelling, but not folder paths.
  for (const name of ['models/loras/nested/Ä model-v1.safetensors', 'models\\vae\\Ä model-v1.safetensors', 'Ä model-v1.safetensors']) {
    context.filenameInput = name;
    assert.equal(vm.runInContext('searchableFilename(filenameInput)', context), 'Ä model-v1.safetensors');
  }
  context.missingFile = {name: 'folder/<model>"&.safetensors', kind: 'text_encoder'};
  for (const compact of [true, false]) {
    const row = vm.runInContext(`missingFileHtml(missingFile, ${compact})`, context);
    assert.ok(row.includes('data-copy-filename="&lt;model&gt;&quot;&amp;.safetensors"'));
    assert.ok(row.includes('data-download-filename="folder/&lt;model&gt;&quot;&amp;.safetensors"'));
    assert.ok(row.includes('data-open-url="https://huggingface.co/models?search='));
    assert.ok(row.includes('data-open-url="https://civitai.com/models?query='));
    assert.ok(row.includes('filename-web-button'));
    assert.ok(row.includes('filename-civitai-button'));
    assert.ok(row.includes('filename-download-button'));
    assert.ok(row.includes('type="button"'));
    assert.ok(row.includes('aria-label="'));
    assert.ok(!row.includes('<model>'));
  }
  assert.ok(!vm.runInContext('missingFileHtml({})', context).includes('data-copy-filename'));
  assert.ok(!vm.runInContext('missingFileHtml({})', context).includes('data-download-filename'));
  assert.ok(!vm.runInContext('missingFileHtml({})', context).includes('filename-web-button'));
  assert.ok(!vm.runInContext('missingFileHtml({})', context).includes('filename-civitai-button'));
  assert.equal(vm.runInContext("defaultModelFolder('checkpoint')", context), 'checkpoints');
  assert.equal(vm.runInContext("defaultModelFolder('lora')", context), 'loras');
  assert.equal(vm.runInContext("defaultModelFolder('vae')", context), 'vae');

  context.clipboardWrites = [];
  context.navigator = {clipboard: {writeText: async text => { context.clipboardWrites.push(text); }}};
  context.copyButton = {dataset: {copyFilename: 'Ä model-v1.safetensors'}, disabled: false};
  context.copyEvent = {
    target: {closest: () => context.copyButton},
    preventDefault() { this.prevented = true; },
    stopPropagation() { this.stopped = true; },
  };
  vm.runInContext('var lastToast; toast = (message, error = false) => { lastToast = {message, error}; };', context);
  await vm.runInContext('copyMissingFilename(copyEvent)', context);
  assert.deepEqual(context.clipboardWrites, ['Ä model-v1.safetensors']);
  assert.equal(context.lastToast.error, false);
  assert.equal(context.lastToast.message, context.messages['missing.name_copied'].replace('{name}', 'Ä model-v1.safetensors'));
  assert.equal(context.copyButton.disabled, false);
  assert.ok(context.copyEvent.prevented && context.copyEvent.stopped);

  // Embedded-browser fallback also removes its temporary field and restores focus.
  let appended = 0, removed = 0, focused = 0, selected = 0, copied = 0;
  let fallbackSucceeds = true;
  const field = {style: {}, focus() {}, select() { selected++; }, remove() { removed++; }};
  context.document = {
    activeElement: {focus() { focused++; }},
    createElement: name => { assert.equal(name, 'textarea'); return field; },
    body: {appendChild() { appended++; }},
    execCommand: command => { assert.equal(command, 'copy'); copied++; return fallbackSucceeds; },
  };
  context.navigator = {};
  await vm.runInContext('copyMissingFilename(copyEvent)', context);
  assert.equal(field.value, 'Ä model-v1.safetensors');
  assert.equal(context.lastToast.error, false);
  assert.equal(appended, 1); assert.equal(removed, 1); assert.equal(focused, 1); assert.equal(selected, 1); assert.equal(copied, 1);
  fallbackSucceeds = false;
  context.navigator = {clipboard: {writeText: async () => { throw new Error('Permission denied'); }}};
  await vm.runInContext('copyMissingFilename(copyEvent)', context);
  assert.equal(context.lastToast.message, context.messages['missing.copy_failed']);
  assert.equal(context.lastToast.error, true);
  assert.equal(context.copyButton.disabled, false);
  assert.equal(removed, 2); assert.equal(focused, 2);

  // Both dynamically rendered confirmation dialogs use the same copyable rows.
  context.report = {workflow: 'Test', package: 'Test', output_path: 'test.zip', missing: [context.missingFile], assets: [], extra_models: []};
  await vm.runInContext(`
    app.state = {configured: true, packages: []};
    window.pywebview.api.choose_workflow = async () => ({ok:true, data:'workflow.json'});
    window.pywebview.api.inspect_for_pack = async () => ({ok:true, data:report});
    window.pywebview.api.choose_archive = async () => ({ok:true, data:'test.zip'});
    window.pywebview.api.inspect_archive_import = async () => ({ok:true, data:report});
    addWorkflow();
  `, context);
  assert.ok(context.confirmation.copy.includes('data-copy-filename="&lt;model&gt;&quot;&amp;.safetensors"'));
  // Import installs extra controls after opening its dialog.
  context.document.getElementById = () => ({querySelectorAll: () => [], querySelector: () => null});
  await vm.runInContext('importPackage()', context);
  assert.ok(context.confirmation.copy.includes('data-copy-filename="&lt;model&gt;&quot;&amp;.safetensors"'));

  // Export is independent of pool optimization and needs an explicit destination.
  const exportButton = {disabled: false};
  context.document.getElementById = id => { assert.equal(id, 'exportButton'); return exportButton; };
  context.exportReport = {output_path: 'E:/Chosen/<folder>/Travel.zip', complete: false,
    missing: [context.missingFile], blockers: [], bytes_required: 20000000, bytes_free: 90000000};
  context.exportCalls = [];
  await vm.runInContext(`
    app.state = {packages: [{path:'example.zip', complete:false, deduplicated:true}]};
    app.selectedPath = 'example.zip';
    confirmation = null;
    window.pywebview.api.choose_export_destination = async () => ({ok:true, data:null});
    window.pywebview.api.preview_portable_archive = async (...args) => {
      exportCalls.push(args); return {ok:true, data:exportReport};
    };
    exportSelectedPackage();
  `, context);
  assert.equal(context.confirmation, null);
  assert.equal(context.exportCalls.length, 0);
  assert.equal(exportButton.disabled, false);
  await vm.runInContext(`
    window.pywebview.api.choose_export_destination = async () => ({ok:true, data:exportReport.output_path});
    exportSelectedPackage();
  `, context);
  assert.equal(context.exportCalls.length, 1);
  assert.equal(context.confirmation.showIncomplete, true);
  assert.equal(context.confirmation.disabled, false);
  assert.equal(exportButton.disabled, false);
  assert.ok(context.confirmation.copy.includes(context.messages['storage.export_includes']));
  assert.ok(context.confirmation.copy.includes(context.messages['storage.export_excludes']));
  assert.ok(context.confirmation.copy.includes('&lt;folder&gt;'));
  assert.ok(context.confirmation.copy.includes('data-copy-filename="&lt;model&gt;&quot;&amp;.safetensors"'));
  await vm.runInContext(`
    window.pywebview.api.export_portable_archive = async (...args) => {
      exportCalls.push(args); return {ok:true, data:{output_path:exportReport.output_path}};
    };
    confirmation.action({allowIncomplete:true});
  `, context);
  assert.deepEqual(Array.from(context.exportCalls[1]), ['example.zip', context.exportReport.output_path, true]);
  assert.equal(context.lastToast.error, false);
  context.exportReport.complete = true;
  context.exportReport.missing = [];
  context.exportReport.blockers = [{code:'space'}];
  vm.runInContext('showPortableExportPreview(selectedPackage(), exportReport)', context);
  assert.equal(context.confirmation.disabled, true);
  assert.equal(context.confirmation.showIncomplete, false);
  assert.ok(context.confirmation.copy.includes(context.messages['export_blocker.space']));

  // Expanding references never replaces explicit consent to remove shared files.
  const consent = {checked:false};
  context.document.getElementById = id => {
    assert.ok(['allowSharedCheckbox', 'allowIncompleteCheckbox'].includes(id));
    return id === 'allowSharedCheckbox' ? consent : {checked:false};
  };
  vm.runInContext(`
    var sharedActions = 0;
    closeConfirm = () => { app.pendingConfirm = null; };
    app.pendingConfirm = {showShared:true, action:async flags => {
      if (flags.allowShared) sharedActions++;
    }};
  `, context);
  await vm.runInContext('proceedConfirm()', context);
  assert.equal(context.sharedActions, 0);
  assert.equal(context.lastToast.message, context.messages['confirm.shared_required']);
  consent.checked = true;
  await vm.runInContext('proceedConfirm()', context);
  assert.equal(context.sharedActions, 1);

  // The displayed check comes from persisted backend state, not a session Map.
  const integrityLabel = {};
  context.document.getElementById = id => { assert.equal(id, 'integrityLabel'); return integrityLabel; };
  context.persistedCheck = {state:'verified', checked_at:'2026-09-03T10:15:00+00:00', persisted:true};
  vm.runInContext('renderIntegrity({verification:persistedCheck})', context);
  assert.equal(integrityLabel.className, 'integrity-label valid');
  assert.ok(integrityLabel.innerHTML.includes(context.messages['detail.integrity_valid']));
  assert.ok(integrityLabel.title.includes('2026'));
  assert.ok(integrityLabel.title.includes(context.messages['detail.integrity_cached_hint']));
  context.persistedCheck.state = 'stale';
  vm.runInContext('renderIntegrity({verification:persistedCheck})', context);
  assert.equal(integrityLabel.className, 'integrity-label stale');
  assert.ok(integrityLabel.title.includes(context.messages['detail.integrity_stale_hint']));
  context.persistedCheck.state = 'invalid';
  vm.runInContext('renderIntegrity({verification:persistedCheck})', context);
  assert.equal(integrityLabel.className, 'integrity-label invalid');
  vm.runInContext('renderIntegrity({})', context);
  assert.ok(integrityLabel.innerHTML.includes(context.messages['detail.integrity_unknown']));
  context.verifyResult = {valid:true, verification:{state:'verified', checked_at:'2026-09-03T10:15:00Z', persisted:true}};
  await vm.runInContext(`
    var originalRenderDetail = renderDetail;
    renderDetail = renderIntegrity;
    app.state = {packages:[{path:'verified.zip'}]};
    app.selectedPath = 'verified.zip';
    window.pywebview.api.verify_archive = async () => ({ok:true, data:verifyResult});
    verifySelected();
  `, context);
  assert.equal(integrityLabel.className, 'integrity-label valid');
  assert.equal(context.lastToast.message, context.messages['verify.valid']);
  context.verifyResult.verification.persisted = false;
  await vm.runInContext('verifySelected()', context);
  assert.equal(context.lastToast.message, context.messages['verify.not_saved']);
  assert.equal(context.lastToast.error, true);
  assert.ok(integrityLabel.title.includes(context.messages['verify.not_saved']));
  context.verifyResult.valid = false;
  context.verifyResult.verification = {state:'stale', checked_at:'2026-09-03T10:15:00Z', persisted:true};
  await vm.runInContext('verifySelected()', context);
  assert.equal(context.lastToast.message, context.messages['verify.changed']);
  assert.equal(integrityLabel.className, 'integrity-label stale');
  const previousRefreshes = context.refreshed;
  vm.runInContext('window.pywebview.api.verify_archive = async () => ({ok:false, cancelled:true});', context);
  await vm.runInContext('verifySelected()', context);
  assert.equal(context.refreshed, previousRefreshes + 1);
  vm.runInContext('renderDetail = originalRenderDetail;', context);

  // Discovery is read-only. Adding requires the exact preview token and a
  // separate confirmation; filenames and peer package names are escaped.
  context.dependencyReport = {token:'preview-token', counts:{included:1, found:1, missing:1, ambiguous:1},
    space_required:100, space_free:1000, warnings:['<unreadable>.zip'], parser_warnings:[], rows:[
      {name:'<shared>.safetensors', kind:'text_encoder', state:'found', source:'pool', pool:{package:'<peer>.zip'}},
      {name:'included.safetensors', kind:'vae', state:'included'},
      {name:'missing.safetensors', kind:'lora', state:'missing'},
      {name:'ambiguous.safetensors', kind:'lora', state:'ambiguous', candidates:['<one>.zip', '<two>.zip']},
    ]};
  context.dependencyCalls = [];
  await vm.runInContext(`
    window.pywebview.api.scan_archive_dependencies = async () => ({ok:true, data:dependencyReport});
    window.pywebview.api.complete_archive_dependencies = async (...args) => {
      dependencyCalls.push(args);
      return {ok:true, data:{added:1, complete:false, reused_object_bytes:123, backup:'E:/<backup>.zip.bak'}};
    };
    scanSelectedDependencies();
  `, context);
  assert.equal(context.dependencyCalls.length, 0);
  assert.equal(context.confirmation.wide, true);
  assert.equal(context.confirmation.disabled, false);
  assert.ok(context.confirmation.copy.includes('&lt;shared&gt;.safetensors'));
  assert.ok(context.confirmation.copy.includes('&lt;peer&gt;.zip'));
  assert.ok(context.confirmation.copy.includes('&lt;one&gt;.zip'));
  assert.ok(context.confirmation.copy.includes(context.messages['dependencies.readonly']));
  await vm.runInContext('confirmation.action()', context);
  assert.deepEqual(Array.from(context.dependencyCalls[0]), ['verified.zip', 'preview-token']);
  assert.equal(context.confirmation.readOnly, true);
  assert.ok(context.confirmation.copy.includes('&lt;backup&gt;.zip.bak'));
  assert.ok(context.confirmation.copy.includes(context.messages['dependencies.incomplete']));
  context.dependencyReport.space_free = 0;
  await vm.runInContext('scanSelectedDependencies()', context);
  assert.equal(context.confirmation.disabled, true);
  context.dependencyReport.counts.found = 0;
  await vm.runInContext('scanSelectedDependencies()', context);
  assert.equal(context.confirmation.readOnly, true);
  assert.equal(context.confirmation.proceedLabel, context.messages['common.close']);
  await vm.runInContext('confirmation.action()', context);
  assert.equal(context.dependencyCalls.length, 1);

  // Showing a local JSON is immediate. Restoring a missing one needs consent,
  // carries the exact preview token, and never invokes a model action.
  const workflowButton = {disabled:false};
  context.document.getElementById = id => { assert.equal(id, 'showWorkflowFileButton'); return workflowButton; };
  context.workflowReport = {exists:true, path:'C:/<workflow>.json', token:'json-token'};
  context.workflowRestores = [];
  await vm.runInContext(`
    confirmation = null;
    window.pywebview.api.show_workflow_file = async () => ({ok:true, data:workflowReport});
    window.pywebview.api.restore_workflow_file = async (...args) => {
      workflowRestores.push(args); return {ok:true, data:{restored:true}};
    };
    showSelectedWorkflowFile();
  `, context);
  assert.equal(context.confirmation, null);
  assert.equal(context.workflowRestores.length, 0);
  assert.equal(workflowButton.disabled, false);
  assert.equal(context.lastToast.message, context.messages['workflow_file.shown']);
  context.workflowReport.exists = false;
  await vm.runInContext('showSelectedWorkflowFile()', context);
  assert.equal(context.workflowRestores.length, 0);
  assert.ok(context.confirmation.copy.includes('&lt;workflow&gt;.json'));
  assert.ok(context.confirmation.copy.includes(context.messages['workflow_file.note']));
  assert.equal(context.confirmation.proceedLabel, context.messages['workflow_file.restore']);
  await vm.runInContext('confirmation.action()', context);
  assert.deepEqual(Array.from(context.workflowRestores[0]), ['verified.zip', 'json-token']);
  assert.equal(context.lastToast.message, context.messages['workflow_file.restored']);
  await vm.runInContext(`
    window.pywebview.api.show_workflow_file = async () => ({ok:false, error_code:'workflow_integrity'});
    showSelectedWorkflowFile();
  `, context);
  assert.equal(workflowButton.disabled, false);
  assert.equal(context.lastToast.message, context.messages['errors.workflow_integrity']);

  // Deleting an archive handles trash vs permanent and optional orphaned pool cleanup.
  const deleteElements = {
    deleteIntro: {},
    deleteLoadedWarning: {classList: {toggle() {}}},
    deleteMethodTrash: {checked: true},
    deleteMethodPermanent: {checked: false},
    deleteOrphansOption: {classList: {toggle() {}, add() {}, remove() {}, contains(c) { return c === 'hidden' ? false : false; }}},
    deleteOrphansCheckbox: {checked: false},
    deleteOrphansLabel: {},
    deleteModal: {classList: {add() { deleteElements.deleteModal.hidden = true; }, remove() { deleteElements.deleteModal.hidden = false; }}},
    deleteConfirmButton: {disabled: false},
    detailView: {classList: {add() {}, remove() {}}},
    emptyState: {classList: {add() {}, remove() {}}},
  };
  context.document.getElementById = id => deleteElements[id] || {classList: {add(){}, remove(){}, toggle(){}, contains(){ return false; }}, addEventListener(){}};
  context.deleteInspectCalls = [];
  context.deleteCalls = [];
  vm.runInContext(`
    window.pywebview.api.inspect_archive_deletion = async (...args) => {
      deleteInspectCalls.push(args);
      return {ok: true, data: {name: 'TestPackage', orphaned_models: [{sha256: 'xyz'}], orphaned_bytes: 1024}};
    };
    window.pywebview.api.delete_archive = async (...args) => {
      deleteCalls.push(args);
      return {ok: true, data: {name: 'TestPackage', trashed: true}};
    };
    app.state = {packages: [{name: 'TestPackage', path: 'E:/Archive/TestPackage.zip', archive_size: 2048, state: 'archived'}]};
    app.selectedPath = 'E:/Archive/TestPackage.zip';
  `, context);
  await vm.runInContext('openDeleteModal()', context);
  assert.equal(deleteElements.deleteModal.hidden, false);
  assert.equal(context.deleteInspectCalls.length, 1);
  assert.equal(deleteElements.deleteOrphansCheckbox.checked, true);
  assert.ok(deleteElements.deleteIntro.textContent.includes('TestPackage'));

  await vm.runInContext('confirmDeleteArchive()', context);
  assert.equal(context.deleteCalls.length, 1);
  assert.deepEqual(Array.from(context.deleteCalls[0]), ['E:/Archive/TestPackage.zip', true, true]);
  assert.equal(deleteElements.deleteModal.hidden, true);
  assert.ok(context.lastToast.message.includes('TestPackage'));

  // Thumbnails render in list and detail hero, and lightbox opens and closes.
  const heroCover = {classList: {add(){ heroCover.hidden = true; }, remove(){ heroCover.hidden = false; }}, hidden: true};
  const heroCoverImg = {src: ''};
  const lightboxModal = {classList: {add(){ lightboxModal.hidden = true; }, remove(){ lightboxModal.hidden = false; }}, hidden: true};
  const lightboxImage = {src: ''};
  const lightboxCaption = {textContent: ''};
  const workflowList = {innerHTML: '', querySelectorAll(){ return []; }};
  const thumbElements = {
    heroCover, heroCoverImg, lightboxModal, lightboxImage, lightboxCaption, workflowList,
    detailView: {classList: {add(){}, remove(){}}},
    emptyState: {classList: {add(){}, remove(){}}},
    packageName: {textContent: ''},
    packagePath: {textContent: ''},
    renameButton: {disabled: false},
    modelCount: {textContent: ''},
    modelPresence: {textContent: ''},
    modelSize: {textContent: ''},
    storageImpactLabel: {textContent: ''},
    heroMetric: {title: ''},
    modelStorageMode: {textContent: ''},
    modelStorageLabel: {textContent: ''},
    customNodeCount: {textContent: ''},
    customNodeSubtitle: {textContent: ''},
    statusPill: {className: ''},
    statusRail: {className: ''},
    modelList: {innerHTML: ''},
    noticeList: {innerHTML: ''},
    customNodeList: {innerHTML: ''},
    primaryActionButton: {innerHTML: ''},
    secondaryActionButton: {innerHTML: '', classList: {add(){}, remove(){}}},
    storageButton: {innerHTML: '', classList: {toggle(){}}},
    showWorkflowFileButton: {disabled: false},
  };
  context.document.getElementById = id => thumbElements[id] || {classList: {add(){}, remove(){}, toggle(){}, contains(){ return false; }}, addEventListener(){}};
  context.thumbCalls = [];
  vm.runInContext(`
    window.pywebview.api.get_archive_thumbnail = async (path) => {
      thumbCalls.push(path);
      return {ok: true, data: 'data:image/png;base64,samplethumb'};
    };
    app.state = {
      configured: true,
      packages: [
        {
          name: 'WithThumb',
          path: 'E:/Archive/WithThumb.zip',
          has_thumbnail: true,
          state: 'archived',
          model_bytes: 100,
          models: [
            {filename: 'model.safetensors', kind: 'checkpoint', size: 100, present: true, link_type: 'hardlink'},
            {filename: 'symlinked.safetensors', kind: 'lora', size: 50, present: true, link_type: 'symlink'},
            {filename: 'copied.safetensors', kind: 'vae', size: 30, present: true, link_type: 'copy'}
          ],
          custom_nodes: [
            {package: 'ComfyUI-Impact-Pack', installed: true, installed_folder: 'ComfyUI-Impact-Pack', git_url: 'https://github.com/ltdrdata/ComfyUI-Impact-Pack'},
            {package: 'missing-pack', installed: false, search_url: 'https://github.com/search?q=missing-pack&type=repositories'}
          ]
        },
        {name: 'NoThumb', path: 'E:/Archive/NoThumb.zip', has_thumbnail: false, state: 'archived', model_bytes: 200, models: []}
      ]
    };
    app.selectedPath = 'E:/Archive/WithThumb.zip';
    renderLibrary();
    renderDetail(app.state.packages[0]);
  `, context);
  assert.equal(thumbElements.customNodeCount.textContent, '1 / 2');
  assert.ok(thumbElements.customNodeSubtitle.textContent.includes('1'));
  assert.ok(thumbElements.customNodeList.innerHTML.includes('custom-node-chip installed'));
  assert.ok(thumbElements.customNodeList.innerHTML.includes('custom-node-chip missing'));
  assert.ok(thumbElements.customNodeList.innerHTML.includes(context.messages['detail.custom_node_installed']));
  assert.ok(thumbElements.customNodeList.innerHTML.includes(context.messages['detail.custom_node_missing']));
  assert.ok(thumbElements.customNodeList.innerHTML.includes('data-open-url="https://github.com/ltdrdata/ComfyUI-Impact-Pack"'));
  assert.ok(thumbElements.customNodeList.innerHTML.includes('data-copy-custom-node="ComfyUI-Impact-Pack"'));
  assert.ok(thumbElements.noticeList.innerHTML.includes(context.messages['notice.custom_nodes_missing_one']));
  assert.ok(thumbElements.noticeList.innerHTML.includes(context.messages['notice.custom_nodes_missing_hint']));

  context.copiedText = '';
  context.writeClipboardText = async text => { context.copiedText = text; };
  context.toastText = '';
  context.toast = msg => { context.toastText = msg; };
  context.fakeCopyBtn = {dataset: {copyCustomNode: 'ComfyUI-Impact-Pack'}, disabled: false, closest() { return this; }};
  await vm.runInContext("copyCustomNodePackage({target: fakeCopyBtn, preventDefault(){}, stopPropagation(){}})", context);
  assert.equal(context.copiedText, 'ComfyUI-Impact-Pack');
  assert.ok(context.toastText.includes('ComfyUI-Impact-Pack'));

  const externalUrlCalls = [];
  context.window.pywebview.api.open_external_url = async url => { externalUrlCalls.push(url); return {ok: true}; };
  context.fakeOpenBtn = {dataset: {openUrl: 'https://github.com/ltdrdata/ComfyUI-Impact-Pack'}, closest() { return this; }};
  await vm.runInContext("openExternalUrlAction({target: fakeOpenBtn, preventDefault(){}, stopPropagation(){}})", context);
  assert.equal(externalUrlCalls.length, 1);
  assert.equal(externalUrlCalls[0], 'https://github.com/ltdrdata/ComfyUI-Impact-Pack');
  await vm.runInContext('renderDetailThumbnail(app.state.packages[0])', context);
  assert.equal(heroCover.hidden, false);
  assert.equal(heroCoverImg.src, 'data:image/png;base64,samplethumb');
  assert.ok(thumbElements.modelList.innerHTML.includes('dep-link-badge hardlink'));
  assert.ok(thumbElements.modelList.innerHTML.includes('Hardlink'));
  assert.ok(thumbElements.modelList.innerHTML.includes('dep-link-badge symlink'));
  assert.ok(thumbElements.modelList.innerHTML.includes('Symlink'));
  assert.ok(thumbElements.modelList.innerHTML.includes('dep-link-badge copy'));
  assert.ok(thumbElements.modelList.innerHTML.includes(context.messages['detail.link_copy']));

  vm.runInContext('openLightbox()', context);
  assert.equal(lightboxModal.hidden, false);
  assert.equal(lightboxImage.src, 'data:image/png;base64,samplethumb');
  assert.equal(lightboxCaption.textContent, 'WithThumb');

  vm.runInContext('closeLightbox()', context);
  assert.equal(lightboxModal.hidden, true);
  assert.equal(lightboxImage.src, '');

  // Settings link mode test
  const saveSettingsCalls = [];
  context.window.pywebview.api.save_settings = async (...args) => {
    saveSettingsCalls.push(args);
    return {ok: true, data: {validation: {warnings: []}}};
  };
  const comfyRootInput = {value: 'C:/ComfyUI'};
  const archiveRootInput = {value: 'E:/Archive'};
  const linkModeSelect = {value: 'hardlink'};
  const settingsValidation = {classList: {add(){}, remove(){}}, className: '', textContent: ''};
  const settingsModal = {classList: {add(){}, remove(){}}};
  const settingsElements = {
    comfyRootInput, archiveRootInput, linkModeSelect,
    settingsValidation, settingsModal,
    settingsCloseButton: {classList: {toggle(){}}},
    settingsCancelButton: {classList: {toggle(){}}},
  };
  context.document.getElementById = id => settingsElements[id] || thumbElements[id] || {classList: {add(){}, remove(){}, toggle(){}, contains(){ return false; }}, addEventListener(){}};

  await vm.runInContext('saveSettings()', context);
  assert.equal(saveSettingsCalls.length, 1);
  assert.deepEqual(saveSettingsCalls[0], ['C:/ComfyUI', 'E:/Archive', 'hardlink']);

  vm.runInContext(`
    app.state.installation = {comfy_root: 'C:/ComfyUI', archive_root: 'E:/Archive', link_mode: 'symlink'};
    openSettings();
  `, context);
  assert.equal(linkModeSelect.value, 'symlink');

  // Drag & drop tests
  context.toastText = '';
  await vm.runInContext("handleDroppedFile({name: 'unsupported.exe', path: 'C:/unsupported.exe'})", context);
  assert.equal(context.toastText, context.messages['drop.unsupported_file']);

  let stagedArgs = null;
  context.window.pywebview.api.stage_dropped_file = async (name, b64) => {
    stagedArgs = {name, b64};
    return {ok: true, data: `C:/staging/${name}`};
  };

  let inspectPackPath = null;
  context.window.pywebview.api.inspect_for_pack = async (path) => {
    inspectPackPath = path;
    return {ok: true, data: {workflow: 'dropped', assets: [], missing: [], custom_nodes: [], output_path: 'C:/out.zip'}};
  };

  let inspectImportPath = null;
  context.window.pywebview.api.inspect_archive_import = async (path) => {
    inspectImportPath = path;
    return {ok: true, data: {package: 'dropped', assets: [], missing: [], target_path: 'C:/target.zip', archive_space_sufficient: true}};
  };

  // Dropping .png with path directly inspects for pack
  await vm.runInContext("handleDroppedFile({name: 'flux_dev.png', path: 'D:/flux_dev.png'})", context);
  assert.equal(inspectPackPath, 'D:/flux_dev.png');
  assert.ok(context.confirmation.proceedLabel.includes(context.messages['pack.proceed']));

  // Dropping .zip with path directly inspects for import
  await vm.runInContext("handleDroppedFile({name: 'bundle.zip', path: 'D:/bundle.zip'})", context);
  assert.equal(inspectImportPath, 'D:/bundle.zip');

  // Dropping .json without direct path uses FileReader mock and stage_dropped_file
  context.FileReader = class {
    readAsDataURL(file) {
      this.result = 'data:application/json;base64,eyJub2RlcyI6W119';
      this.onload();
    }
  };
  await vm.runInContext("handleDroppedFile({name: 'browser_dropped.json'})", context);
  assert.equal(stagedArgs.name, 'browser_dropped.json');
  assert.equal(stagedArgs.b64, 'eyJub2RlcyI6W119');
  assert.equal(inspectPackPath, 'C:/staging/browser_dropped.json');

  // Search filtering tests
  context.workflowList = {innerHTML: '', querySelectorAll(){ return []; }};
  context.document.getElementById = id => (id === 'workflowList' ? context.workflowList : {classList: {add(){}, remove(){}, toggle(){}, contains(){ return false; }}, addEventListener(){}, setAttribute(){}});

  vm.runInContext(`
    app.state = {
      configured: true,
      packages: [
        {name: 'Flux Dev Inpainting', path: 'E:/Archive/Flux.zip', state: 'archived', models: [{filename: 'flux1-dev.safetensors'}], missing: [], custom_nodes: []},
        {name: 'SDXL Fast Anime', path: 'E:/Archive/SDXL.zip', state: 'archived', models: [{filename: 'sdxl_lightning.safetensors'}], missing: [], custom_nodes: [{package: 'ComfyUI-Impact-Pack'}]},
        {name: 'Music Generator', path: 'E:/Archive/Music.zip', state: 'archived', models: [{filename: 'minimax_music.safetensors'}], missing: [], custom_nodes: []}
      ]
    };
    app.filter = 'all';
    app.searchQuery = '';
    renderLibrary();
  `, context);
  assert.ok(context.workflowList.innerHTML.includes('Flux Dev Inpainting'));
  assert.ok(context.workflowList.innerHTML.includes('SDXL Fast Anime'));
  assert.ok(context.workflowList.innerHTML.includes('Music Generator'));

  // Filter by workflow name (matches title, no match hint needed)
  vm.runInContext("app.searchScope = 'all'; app.searchQuery = 'Flux'; renderLibrary();", context);
  assert.ok(context.workflowList.innerHTML.includes('Flux Dev Inpainting'));
  assert.ok(!context.workflowList.innerHTML.includes('SDXL Fast Anime'));
  assert.ok(!context.workflowList.innerHTML.includes('Music Generator'));
  assert.ok(!context.workflowList.innerHTML.includes('workflow-match-hint'));

  // Filter by model filename (shows match hint with model filename)
  vm.runInContext("app.searchScope = 'all'; app.searchQuery = 'lightning'; renderLibrary();", context);
  assert.ok(!context.workflowList.innerHTML.includes('Flux Dev Inpainting'));
  assert.ok(context.workflowList.innerHTML.includes('SDXL Fast Anime'));
  assert.ok(!context.workflowList.innerHTML.includes('Music Generator'));
  assert.ok(context.workflowList.innerHTML.includes('workflow-match-hint'));
  assert.ok(context.workflowList.innerHTML.includes(context.messages['library.match_model']));
  assert.ok(context.workflowList.innerHTML.includes('sdxl_lightning.safetensors'));

  // Filter by custom node package name (shows match hint with node package)
  vm.runInContext("app.searchScope = 'all'; app.searchQuery = 'impact'; renderLibrary();", context);
  assert.ok(!context.workflowList.innerHTML.includes('Flux Dev Inpainting'));
  assert.ok(context.workflowList.innerHTML.includes('SDXL Fast Anime'));
  assert.ok(!context.workflowList.innerHTML.includes('Music Generator'));
  assert.ok(context.workflowList.innerHTML.includes('workflow-match-hint'));
  assert.ok(context.workflowList.innerHTML.includes(context.messages['library.match_node']));
  assert.ok(context.workflowList.innerHTML.includes('ComfyUI-Impact-Pack'));

  // Scope: 'name' strictly ignores models and nodes
  vm.runInContext("app.searchScope = 'name'; app.searchQuery = 'lightning'; renderLibrary();", context);
  assert.ok(!context.workflowList.innerHTML.includes('SDXL Fast Anime'));

  // Scope: 'model' strictly filters by models
  vm.runInContext("app.searchScope = 'model'; app.searchQuery = 'lightning'; renderLibrary();", context);
  assert.ok(context.workflowList.innerHTML.includes('SDXL Fast Anime'));
  vm.runInContext("app.searchScope = 'model'; app.searchQuery = 'impact'; renderLibrary();", context);
  assert.ok(!context.workflowList.innerHTML.includes('SDXL Fast Anime'));

  // Scope: 'node' strictly filters by custom nodes
  vm.runInContext("app.searchScope = 'node'; app.searchQuery = 'impact'; renderLibrary();", context);
  assert.ok(context.workflowList.innerHTML.includes('SDXL Fast Anime'));

  // No results shows search empty message with query
  vm.runInContext("app.searchScope = 'all'; app.searchQuery = 'nonexistent'; renderLibrary();", context);
  assert.ok(context.workflowList.innerHTML.includes(context.messages['library.search_empty'].replace('{query}', 'nonexistent')));

  // Resetting search restores all
  vm.runInContext("app.searchScope = 'all'; app.searchQuery = ''; renderLibrary();", context);
  assert.ok(context.workflowList.innerHTML.includes('Flux Dev Inpainting'));
  assert.ok(context.workflowList.innerHTML.includes('SDXL Fast Anime'));
  assert.ok(context.workflowList.innerHTML.includes('Music Generator'));
}

(async () => {
  await run('en');
  await run('de');

  // Verify HTML markup and button tooltip attributes
  const indexHtml = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
  assert.ok(indexHtml.includes('id="addWorkflowButton" data-i18n-title="library.archive_workflow_hint"'));
  assert.ok(indexHtml.includes('id="importPackageButton" data-i18n-title="library.import_package_hint"'));
  assert.ok(indexHtml.includes('id="emptyAddButton" data-i18n-title="library.archive_workflow_hint"'));
  assert.ok(indexHtml.includes('id="dropOverlay"'));
  assert.ok(indexHtml.includes('data-i18n="drop.title"'));
  assert.ok(indexHtml.includes('data-i18n="drop.subtitle"'));
  assert.ok(indexHtml.includes('id="workflowSearchInput"'));
  assert.ok(indexHtml.includes('data-i18n-placeholder="library.search_placeholder"'));
  assert.ok(indexHtml.includes('id="searchClearButton"'));
  assert.ok(indexHtml.includes('id="i-search"'));
  assert.ok(indexHtml.includes('class="search-scope-row"'));
  assert.ok(indexHtml.includes('data-scope="all"'));
  assert.ok(indexHtml.includes('data-scope="name"'));
  assert.ok(indexHtml.includes('data-scope="model"'));
  assert.ok(indexHtml.includes('data-scope="node"'));
  assert.ok(indexHtml.includes('id="downloadModal"'));
  assert.ok(indexHtml.includes('id="downloadModelName"'));
  assert.ok(indexHtml.includes('id="downloadTargetPath"'));
  assert.ok(indexHtml.includes('id="downloadWebLinkRow"'));
  assert.ok(indexHtml.includes('id="downloadOpenWebBtn"'));
  assert.ok(indexHtml.includes('id="downloadCivitaiBtn"'));
  assert.ok(indexHtml.includes('id="downloadCandidatesFallback"'));
  assert.ok(indexHtml.includes('id="downloadSearchHfBtn"'));
  assert.ok(indexHtml.includes('id="downloadFallbackCivitaiBtn"'));
  assert.ok(indexHtml.includes('id="downloadSourceInput"'));
  assert.ok(indexHtml.includes('id="downloadCandidatesBox"'));
  assert.ok(indexHtml.includes('id="downloadStoreInPoolOption"'));
  assert.ok(indexHtml.includes('id="downloadStartButton"'));

  for (const lang of ['de', 'en']) {
    const loc = JSON.parse(fs.readFileSync(path.join(root, 'locales', `${lang}.json`), 'utf8'));
    for (const key of [
      'library.archive_workflow_hint',
      'library.import_package_hint',
      'drop.title',
      'drop.subtitle',
      'drop.processing',
      'drop.processing_detail',
      'drop.unsupported_file',
      'library.search_placeholder',
      'library.search_aria',
      'library.search_clear',
      'library.search_empty',
      'search.scope',
      'search.scope_all',
      'search.scope_name',
      'search.scope_model',
      'search.scope_node',
      'library.match_model',
      'library.match_node',
      'library.match_missing',
      'missing.copy_name',
      'missing.download_model',
      'missing.open_hf_search',
      'missing.open_civitai_search',
      'download.eyebrow',
      'download.title',
      'download.intro',
      'download.model_label',
      'download.target_label',
      'download.web_label',
      'download.view_model_card',
      'download.open_hf_search',
      'download.open_civitai_search',
      'download.source_label',
      'download.source_placeholder',
      'download.source_hint',
      'download.searching_hf',
      'download.no_hf_candidates',
      'download.hf_candidates_header',
      'download.store_in_pool',
      'download.store_in_pool_hint',
      'download.start_button',
      'download.busy_title',
      'download.busy_detail',
      'download.success',
      'phase.download',
    ]) {
      assert.ok(loc[key], `Missing key ${key} in ${lang}.json`);
    }
  }

  console.log('Frontend EN/DE tests passed: workflow JSON reveal/restore confirmation, grouped parser notices, dependency scan/add and backup results, persistent verification, shared-file consent, diagnostics, copy buttons, pack/import, portable export, archive deletion modal, PNG thumbnails with lightbox, instant-load link modes with badges, custom nodes live status, Drag & Drop file staging, and library instant search with match hints and scopes.');
})().catch(error => { console.error(error); process.exitCode = 1; });
