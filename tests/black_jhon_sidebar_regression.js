const assert = require('assert');
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const sidebarDir = path.join(root, 'static', 'ia-sidebar');
const chunks = [
  '01-bootstrap-storage-render.part.js',
  '02-ui-modelos.part.js',
  '03-init-shell-codex.part.js',
  '04-mensagens-notificacoes.part.js',
  '05-perguntas-monitor.part.js',
  '06-mensagens-contatos-anexos.part.js',
  '07-mensagens-fluxo.part.js',
  '08-aprovacoes.part.js',
  '09-chat-bootstrap.part.js',
];

function read(relativePath) {
  return fs.readFileSync(path.join(root, relativePath), 'utf8');
}

const loader = read(path.join('static', 'ia-sidebar.js'));
const render = read(path.join('static', 'ia-sidebar', '01-bootstrap-storage-render.part.js'));
const ui = read(path.join('static', 'ia-sidebar', '02-ui-modelos.part.js'));
const codex = read(path.join('static', 'ia-sidebar', '03-init-shell-codex.part.js'));
const monitor = read(path.join('static', 'ia-sidebar', '05-perguntas-monitor.part.js'));
const approvals = read(path.join('static', 'ia-sidebar', '08-aprovacoes.part.js'));
const bootstrap = read(path.join('static', 'ia-sidebar', '09-chat-bootstrap.part.js'));
const consoleAttachments = read(path.join('backend', 'services', 'codex', 'console', 'attachments.py'));
const consoleQueueWorker = read(path.join('backend', 'services', 'codex', 'console', 'queue_worker.py'));
const consoleConversations = read(path.join('backend', 'services', 'codex', 'console', 'api_conversations.py'));
const consoleState = read(path.join('backend', 'services', 'codex', 'console', 'state.py'));
const consoleRuntime = read(path.join('backend', 'services', 'codex', 'console', 'runtime.py'));
const consoleAgentPrompt = read(path.join('backend', 'services', 'codex', 'console', 'agent_prompt.py'));
const consoleTaskViews = read(path.join('backend', 'services', 'codex', 'console', 'task_views.py'));
const electronBackend = read(path.join('electron_app', 'main', 'modules', 'backend.js'));
const iaSchema = read(path.join('backend', 'schemas', 'ia.py'));
const iaEndpoints = read(path.join('backend', 'services', 'ia_endpoints.py'));
const android = read(path.join('android_app', 'app', 'src', 'main', 'java', 'br', 'com', 'jksistema', 'mobile', 'MainActivity.java'));
const vendasAssistant = read(path.join('static', 'vendas', 'assistente.js'));

assert.match(loader, /20260722-black-jhon-unified/);
assert.match(loader, /Abrir Black Jhon/);
assert.match(loader, /params\.get\('embed'\) === 'share'/);

assert.strictEqual((ui.match(/id="jk-ia-fab"/g) || []).length, 1, 'deve existir um unico FAB do Black Jhon');
assert.doesNotMatch(ui, /id="jk-codex-fab"/);
assert.match(ui, /data-primary-ai="codex"/);
assert.match(ui, /<h3>Black Jhon<\/h3>/);
assert.match(ui, /Codex principal/);
assert.match(ui, /title="Reiniciar memoria"/);
assert.match(ui, /container-name:jk-codex-panel/);
assert.match(ui, /@container jk-codex-panel \(max-width:560px\)/);
assert.match(ui, /\.jk-ia-table-responsive td::before\{content:attr\(data-label\)/);
assert.match(render, /class="jk-ia-table-wrap"/);
assert.match(render, /class="jk-ia-table-responsive" data-columns=/);
assert.match(render, /data-label=/);

assert.match(codex, /const CODEX_ASSISTANT_DISPLAY_NAME = 'Black Jhon'/);
assert.match(codex, /const BLACK_JHON_PRIMARY_AI = 'codex'/);
assert.match(codex, /_codexFetchJson\('\/api\/codex\/status'/);
assert.match(codex, /_codexFetchJson\('\/api\/codex\/tasks'/);
assert.match(codex, /_codexFetchJson\('\/api\/codex\/tasks\/' \+ encodeURIComponent/);
assert.match(codex, /\/api\/codex\/conversations\//);
assert.match(codex, /\/api\/codex\/conversations\/current\/reset/);
assert.match(codex, /summary=true&channel=unified/);
assert.doesNotMatch(codex, /function _codexNovoConversationId/);
assert.match(codex, /function _codexReiniciarMemoria/);
assert.match(bootstrap, /_codexReiniciarMemoria/);
assert.match(codex, /\/api\/codex\/tasks\/' \+ encodeURIComponent\(taskId\) \+ '\/cancel'/);
assert.match(codex, /\/api\/admin\/codex\/attachments/);
assert.match(codex, /\/api\/admin\/codex\/tasks\/' \+ encodeURIComponent\(taskId\) \+ '\/approve'/);
assert.doesNotMatch(codex, /\/api\/admin\/codex\/status/);
assert.doesNotMatch(codex, /\/api\/admin\/codex\/tasks\?limit/);
assert.doesNotMatch(codex, /_codexFetchJson\('\/api\/admin\/codex\/tasks',/);
assert.match(codex, /\/api\/ia\/chat/);
assert.match(codex, /sandbox[^\n]+=== 'read_only'/);
assert.match(codex, /!temAnexos/);
assert.match(codex, /!mutableIntent/);
assert.match(codex, /fallback_read_only: true/);
assert.match(codex, /operationalFailureCount[^\n]+>= 2/);
assert.match(codex, /_codexMesclarHistoricoComEspeciais/);
assert.match(codex, /item && item\.kind !== 'approval'/);
assert.match(codex, /item\.content_kind !== 'report'/);
assert.match(codex, /_blackJhonPrepararAcaoLocalFavoritos/);
assert.match(codex, /Confirmar preenchimento em Favoritos/);
assert.match(codex, /panel\.dataset\.accessProfile = full \? 'full' : 'read_only'/);
assert.match(codex, /const settings = _codexCollectSettings\(full \? forcedAccess : 'read_only'\)/);
assert.doesNotMatch(codex, /const pathsParaTarefa = full \? _codexPathsParaTarefa\(settings\.paths\) : \[\]/);
assert.match(codex, /const attachmentIds = full \? _codexAttachmentIds\(\) : \[\]/);
assert.match(codex, /attachments: attachmentIds/);
assert.match(codex, /reference_paths: \[\]/);
assert.match(codex, /paths: \[\]/);
assert.match(codex, /if \(\[401, 403\]\.includes\(status\)\) return false/);
assert.match(codex, /function _codexSettingsStorageKey\(\)/);
assert.match(codex, /function _codexThreadStorageKey\(\)/);
assert.match(codex, /!rawText && _usuarioLocalEhFull\(\)/);
assert.match(codex, /CODEX_COORDINATOR_FIRST_DELAY_MS = 60 \* 1000/);
assert.match(codex, /CODEX_COORDINATOR_RETRY_MS = 30 \* 1000/);
assert.match(codex, /CODEX_COORDINATOR_IDLE_MS = 15 \* 1000/);
assert.match(codex, /CODEX_PROACTIVE_INTERVAL_MS = 30 \* 60 \* 1000/);
assert.match(codex, /CODEX_MANUAL_CONTEXT_CACHE_MS = 5 \* 1000/);
assert.match(codex, /CODEX_MANUAL_DOM_MAX_ELEMENTS = 300/);
assert.match(codex, /CODEX_REPORT_PREVIEW = 600/);
assert.match(codex, /Ver relatório detalhado/);
assert.match(codex, /item && item\.report_prompt/);
assert.match(codex, /sales_ranking\\b\/gi, 'Ranking de vendas'/);
assert.doesNotMatch(codex, /reportId \? `ID:/);
assert.match(codex, /CODEX_FULL_TEXT_CACHE_MAX = 4/);
assert.match(codex, /document\.visibilityState/);
assert.match(codex, /!panelVisivel/);
assert.match(codex, /codexAssistantCoordinatorRunning/);
assert.match(codex, /event\.isTrusted === false/);
assert.match(codex, /scrollDoPainelFechado/);
assert.match(codex, /await _codexCarregarSugestoes\(\)[\s\S]*?await _codexRodarProativo\(false\)[\s\S]*?await _codexRodarAnaliseSemanal\(false\)/);
assert.match(codex, /\/api\/admin\/codex\/assistant\/weekly-analysis\/run/);
const coordinator = codex.match(/async function _codexExecutarCoordenador\(\)[\s\S]*?function _codex/);
assert.ok(coordinator, 'coordenador Black Jhon nao encontrado');
assert.doesNotMatch(coordinator[0], /_codexRodarAnaliseDiaria\(/, 'coordenador nao deve mais gerar relatorio diario');
assert.match(coordinator[0], /_codexRodarAnaliseSemanal\(/, 'coordenador deve manter relatorio semanal');
assert.match(codex, /\/api\/admin\/codex\/assistant\/report-settings/);
assert.match(codex, /profile: profileScope\.profile/);
assert.match(codex, /profile: 'import_order'/);
assert.match(codex, /top_actions/);
assert.match(ui, /id="jk-codex-report-settings"/);
assert.match(ui, /id="jk-codex-report-settings-dialog"/);
assert.match(codex, /compact: true, screen_context: _codexObterContextoTelaAtual\(\{ background: true \}\)/);
assert.match(consoleAttachments, /def _codex_canonical_conversation_id/);
assert.match(consoleQueueWorker, /def _codex_run_conversation_queue/);
assert.match(consoleConversations, /def codex_reset_current_conversation/);
assert.ok((codex.match(/compact: true, screen_context: _codexObterContextoTelaAtual\(\{ background: true \}\)/g) || []).length >= 2);
assert.match(codex, /\/api\/codex\/tasks\?limit=100&summary=true/);
assert.doesNotMatch(codex, /function _codexCarregarHistoricoPersistido/);
assert.doesNotMatch(codex, /_codexGerarRelatorioAutomaticoAlertas|codexAssistantAutoReportRunning/);
assert.match(codex, /chat_text_preview/);
assert.match(codex, /function _codexAppendFullTextAction/);
assert.match(codex, /function _codexAbrirTextoCompleto/);
assert.match(codex, /content_id: prepared\.content_id/);
assert.match(codex, /function _codexCacheFullText/);
assert.match(codex, /while \(codexFullTextCache\.size > CODEX_FULL_TEXT_CACHE_MAX\)/);
assert.match(codex, /contentId\.startsWith\('local_'\)/);
assert.match(codex, /function _codexCompactTaskForMemory/);
assert.match(codex, /delete compact\.screen_context/);
assert.match(codex, /function _codexUrlSeguraTela/);
assert.doesNotMatch(codex, /url_completa: location\.href/);
assert.match(codex, /codexPollGeneration \+= 1/);
assert.match(codex, /if \(codexPollTimer\) clearTimeout\(codexPollTimer\)/);
assert.match(codex, /const taskPollGeneration = _codexInvalidarPollAtual\(\)/);
assert.match(codex, /String\(codexTaskAtual\.task_id\) !== String\(taskId\)/);

assert.match(bootstrap, /function toggleBlackJhonPanel/);
const toggleBlackJhon = bootstrap.match(/function toggleBlackJhonPanel\(forcar\) \{[\s\S]*?\n    \}/)?.[0] || '';
assert.match(toggleBlackJhon, /toggleCodexPanel\(forcar\)/);
assert.doesNotMatch(toggleBlackJhon, /_usuarioLocalEhFull|togglePanel/);
const openCodexPanel = bootstrap.match(/function setCodexPanelAberto\(aberto\) \{[\s\S]*?\n    \}/)?.[0] || '';
assert.match(openCodexPanel, /_codexCarregarStatus/);
assert.doesNotMatch(openCodexPanel, /_codexCarregarHistoricoPersistido|_codexCarregarSugestoes|_codexRodarProativo|_codexRodarAnaliseDiaria/);
assert.match(bootstrap, /if \(_usuarioLocalEhFull\(\)\) _codexIniciarAssistenteProativo\(\)/);
assert.match(bootstrap, /_codexPararAssistenteProativo\(\)/);
assert.match(bootstrap, /codexFullTextCache\.clear\(\)/);
assert.doesNotMatch(bootstrap, /jk-codex-fab/);

assert.match(approvals, /_approvalRenderNoPainelBlackJhon/);
assert.match(approvals, /jk-codex-messages/);
assert.match(approvals, /function _adicionarNotificacaoAprovacao[\s\S]*?if \(!_usuarioLocalEhFull\(\)\) return/);
assert.match(monitor, /function _perguntasIniciarMonitorGlobal[\s\S]*?if \(!_usuarioLocalEhFull\(\)/);
assert.doesNotMatch(monitor, /togglePanel\(true\)/);

assert.match(consoleState, /BLACK_JHON_DISPLAY_NAME = "Black Jhon"/);
assert.match(consoleRuntime, /_codex_bool_env\("JK_CODEX_CONSOLE_ENABLED", False\)/);
assert.match(electronBackend, /JK_CODEX_CONSOLE_ENABLED: process\.env\.JK_CODEX_CONSOLE_ENABLED \|\| 'true'/);
assert.match(electronBackend, /set "JK_CODEX_CONSOLE_ENABLED=\$\{cmdValue\(process\.env\.JK_CODEX_CONSOLE_ENABLED \|\| 'true'\)\}"/);
assert.match(consoleAgentPrompt, /O Codex e sua IA principal de raciocinio e execucao/);
assert.match(consoleTaskViews, /['"]mutable_intent['"]:\s*bool\(task\.get\(['"]mutable_intent['"]\)\)/);
assert.match(iaSchema, /fallback_read_only: bool = False/);
assert.match(iaEndpoints, /resposta_imagem = None/);
assert.match(android, /jk-codex-panel/);
assert.doesNotMatch(android, /fab\.textContent='x'/);
assert.match(vendasAssistant, /window\.__JK_IA_SIDEBAR_LOAD_FULL__/);

const combined = chunks.map(file => fs.readFileSync(path.join(sidebarDir, file), 'utf8')).join('\n');
new Function(combined);

console.log('BLACK_JHON_SIDEBAR_REGRESSION_OK');
