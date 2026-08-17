const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const canonical = fs.readFileSync(path.join(root, 'static', 'configuracoes.html'), 'utf8');
const mirror = fs.readFileSync(path.join(root, 'configuracoes.html'), 'utf8');
const ui = fs.readFileSync(path.join(root, 'static', 'configuracoes-whatsapp.js'), 'utf8');
const historyUi = fs.readFileSync(path.join(root, 'static', 'configuracoes-whatsapp-history.js'), 'utf8');
function readPythonTree(directory) {
  return fs.readdirSync(directory, { withFileTypes: true })
    .flatMap((entry) => {
      const fullPath = path.join(directory, entry.name);
      if (entry.isDirectory()) return readPythonTree(fullPath);
      return entry.isFile() && entry.name.endsWith('.py') ? [fs.readFileSync(fullPath, 'utf8')] : [];
    });
}
const service = [
  fs.readFileSync(path.join(root, 'backend', 'services', 'whatsapp_bridge.py'), 'utf8'),
  ...readPythonTree(path.join(root, 'backend', 'services', 'whatsapp')),
].join('\n');
const bridgeContracts = fs.readFileSync(path.join(root, 'backend', 'services', 'whatsapp', 'contracts.py'), 'utf8');
const bridgeFormatting = fs.readFileSync(path.join(root, 'backend', 'services', 'whatsapp', 'formatting.py'), 'utf8');
const bridgeGateway = fs.readFileSync(path.join(root, 'backend', 'services', 'whatsapp', 'gateway.py'), 'utf8');
const bridgeSettings = fs.readFileSync(path.join(root, 'backend', 'services', 'whatsapp', 'settings.py'), 'utf8');
const bridgeMedia = fs.readFileSync(path.join(root, 'backend', 'services', 'whatsapp', 'media.py'), 'utf8');
const bridgeMessage = fs.readFileSync(path.join(root, 'backend', 'services', 'whatsapp', 'message.py'), 'utf8');
const bridgeIntent = fs.readFileSync(path.join(root, 'backend', 'services', 'whatsapp', 'intent.py'), 'utf8');
const bridgeRetryPolicy = fs.readFileSync(path.join(root, 'backend', 'services', 'whatsapp', 'retry_policy.py'), 'utf8');
const bridgeToolResults = fs.readFileSync(path.join(root, 'backend', 'services', 'whatsapp', 'tool_results.py'), 'utf8');
const codexConsole = fs.readFileSync(path.join(root, 'backend', 'services', 'codex_console.py'), 'utf8');
const codexWorkerSetup = fs.readFileSync(path.join(root, 'backend', 'services', 'codex', 'console', 'worker_setup.py'), 'utf8');
const bridgeStore = fs.readFileSync(path.join(root, 'backend', 'services', 'whatsapp_bridge_store.py'), 'utf8');
const reportFiles = fs.readFileSync(path.join(root, 'backend', 'services', 'whatsapp_report_files.py'), 'utf8');
const setup = fs.readFileSync(path.join(root, 'scripts', 'setup-whatsapp-zero-cost.ps1'), 'utf8');
const wranglerExample = fs.readFileSync(path.join(root, 'cloudflare', 'whatsapp-gateway', 'wrangler.example.toml'), 'utf8');
const gatewaySourceDir = path.join(root, 'cloudflare', 'whatsapp-gateway', 'src');
const gateway = fs.readdirSync(gatewaySourceDir)
  .filter((name) => name.endsWith('.ts'))
  .map((name) => fs.readFileSync(path.join(gatewaySourceDir, name), 'utf8'))
  .join('\n');
const manifest = JSON.parse(fs.readFileSync(path.join(root, 'electron_app', 'installer-required-resources.json'), 'utf8'));

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

assert(canonical === mirror, 'configuracoes.html mirror diverged from static canonical');
assert(canonical.includes('data-tab="whatsapp"'), 'WhatsApp tab missing');
assert(canonical.includes('id="cardWhatsappJoao" class="card hidden"'), 'full-only WhatsApp card missing');
assert(canonical.includes('/configuracoes-whatsapp.js?v='), 'WhatsApp controller not loaded');
assert(canonical.includes('id="waPersonalPhones"'), 'multi-number list missing');
assert(canonical.includes('id="waPersonalPhoneCount"'), 'multi-number counter missing');
assert(canonical.includes('id="waTargetUser"'), 'per-user selector missing');
assert(canonical.includes('id="waConversationAgentModel"'), 'conversation Codex selector missing');
assert(canonical.includes('id="waTaskAgentModel"'), 'task Codex selector missing');
assert(canonical.includes('id="waConversationInterval"'), 'conversation interval selector missing');
assert(canonical.includes('id="userWhatsappSettings"'), 'per-user WhatsApp phone settings are missing');
assert(canonical.includes('id="userWaNewPhoneName"'), 'direct phone name field is missing');
assert(canonical.includes('id="userWaNewPhoneNumber"'), 'direct phone number field is missing');
assert(!canonical.includes('id="userWaNewPrimary"'), 'removed primary phone selector is still present');
assert(canonical.includes('id="userWaSavePhone"'), 'direct phone save action is missing');
assert(!canonical.includes('id="userWaWelcomeMessage"'), 'removed welcome message field is still present');
assert(!canonical.includes('id="userWaSendWelcome"'), 'removed welcome option is still present');
assert(canonical.includes('Canal exclusivo para perguntas, separado da conversa da Sidebar'), 'question-only channel guidance is missing');
assert(!canonical.includes('id="userWaAdhocPhone"'), 'removed ad hoc recipient field is still present');
assert(!canonical.includes('id="userWaAdhocMessage"'), 'removed ad hoc message field is still present');
assert(!canonical.includes('id="userWaSendAdhoc"'), 'removed ad hoc send action is still present');
assert(!canonical.includes('id="waVoicePreflight"'), 'removed voice preflight action is still present');
assert(!canonical.includes('id="waVoiceEnable"'), 'removed voice enable action is still present');
assert(!canonical.includes('id="userWaNewVoice"'), 'removed per-phone voice permission is still present');
assert(canonical.includes('O nome e o número são salvos diretamente'), 'direct registration guidance is missing');
assert(!canonical.includes('setting-card wa-voice-card'), 'removed voice card is still present');
assert(canonical.includes('id="userWaPhones"'), 'user phone settings list is missing');
assert(ui.includes("permissions.full !== true"), 'UI is not restricted to full administrators');
assert(ui.includes('/api/admin/whatsapp/status'), 'status endpoint missing from UI');
assert(ui.includes('/api/admin/whatsapp/pairing-code'), 'pairing endpoint missing from UI');
assert(ui.includes('payload.binding_limit || 3'), 'three-number limit missing from UI');
assert(ui.includes('item.username === target.username && item.client_id === target.client_id'), 'numbers are not grouped by selected user');
assert(ui.includes('subject_id: item.subject_id, username: item.username, client_id: item.client_id'), 'individual number revoke missing');
assert(ui.includes('JSON.stringify({ revoke_all: true, ...target })'), 'per-user revoke-all option missing');
assert(ui.includes("request('/api/admin/users')"), 'JK user list is not loaded');
assert(ui.includes("request('/api/admin/whatsapp/phone-settings'"), 'per-phone notification settings endpoint is not used');
assert(ui.includes("request('/api/admin/whatsapp/phones'"), 'direct phone registration endpoint is not used');
assert(!ui.includes('send_welcome_message'), 'removed welcome setting is still written by the UI');
assert(ui.includes('Enviar sugestão de pergunta do Mercado Livre'), 'Mercado Livre question preference is missing');
assert(!ui.includes("phoneOption('Enviar relatório semanal'"), 'weekly report preference must not be displayed');
assert(!ui.includes("phoneOption('Enviar relatório mensal'"), 'monthly report preference must not be displayed');
assert(ui.includes('Como a IA deve se comportar com este usuário'), 'per-phone AI behavior field is missing');
assert(ui.includes('ai_behavior: behaviorInput.value.trim()'), 'per-phone AI behavior is not saved by the UI');
assert(!ui.includes('is_primary:'), 'removed primary setting is still written by the UI');
assert(!ui.includes('send_weekly_report:'), 'removed weekly report setting is still written by the UI');
assert(!ui.includes('send_monthly_report:'), 'removed monthly report setting is still written by the UI');
assert(!ui.includes('allow_voice_calls:'), 'removed voice setting is still written by the UI');
assert(!ui.includes("request('/api/admin/whatsapp/voice/"), 'removed voice endpoints are still used by the UI');
assert(!historyUi.includes("interaction_type === 'call'"), 'removed call history branch is still present');
assert(!historyUi.includes('pela ligação'), 'removed call transcript label is still present');
assert(ui.includes('window.jkWhatsappSelecionarUsuario'), 'selected JK user is not connected to phone settings');
assert(ui.includes("request('/api/ia/modelos')"), 'AI catalog is not loaded for WhatsApp');
assert(ui.includes("agent_architecture: 'dual_codex'"), 'dual Codex architecture is not saved');
assert(ui.includes("conversation_agent_model: String(conversationAgentModel.value || 'gpt-5.6-luna').trim()"), 'Luna conversation model is not saved');
assert(ui.includes("task_agent_model: String(taskAgentModel.value || 'gpt-5.6-sol').trim()"), 'Sol task model is not saved');
assert(ui.includes('progress_messages_enabled: false'), 'legacy progress messages must stay disabled');
assert(ui.includes('jk_black_jhon_nova_pergunta'), 'new-question utility template is not displayed');
assert(ui.includes('jk_black_jhon_nova_pergunta_v2'), 'question-suggestion quick-reply template is not displayed');
assert(ui.includes('BLOQUEIO'), 'pending or rejected templates are not shown as explicit blockers');
assert(ui.includes('/api/admin/whatsapp/whisper/download'), 'Whisper endpoint missing from UI');
assert(!ui.includes('payload.bridge_token_configured ? payload.bridge_token'), 'UI must never render the saved bridge token');
assert(service.includes('TRANSCRIPTION_TIMEOUT_SECONDS = 600'), 'transcription timeout changed');
assert(service.includes('POLL_SECONDS = 3'), 'bridge poll interval must be 3 seconds');
assert(service.includes('CLAIM_LIMIT = 5'), 'bridge claim limit changed');
assert(service.includes('TYPING_REFRESH_SECONDS = 20'), 'typing refresh must remain at 20 seconds');
assert(service.includes('TYPING_MAX_SECONDS = 0'), 'typing heartbeat must not have a total deadline');
assert(service.includes('/typing"'), 'local bridge typing endpoint missing');
assert(service.includes('pairing_pending'), 'pairing no longer auto-activates the local bridge');
assert(bridgeSettings.includes('WHATSAPP_AI_DEFAULT_MODEL = "codex:gpt-5.5"'), 'WhatsApp Codex default changed');
assert(service.includes('def _create_selected_ai_task('), 'selected WhatsApp AI is not routed');
assert(service.includes('def _progress_pulse_worker('), 'WhatsApp Codex progress worker is missing');
assert(service.includes('/progress"'), 'WhatsApp progress endpoint is not called');
assert(service.includes('config.get("progress_messages_enabled") is False'), 'legacy progress pulse is not disabled by configuration');
assert(service.includes('def _process_dual_codex_message('), 'dual Codex WhatsApp orchestrator is missing');
assert(service.includes('def _complete_dual_worker_pending('), 'worker result handoff to Luna is missing');
assert(bridgeRetryPolicy.includes('WHATSAPP_RETRY_DELAYS_SECONDS = (2, 5, 15)'), 'bounded retry intervals changed');
assert(bridgeRetryPolicy.includes('WHATSAPP_MAX_RETRY_ATTEMPTS = 3'), 'bounded retry limit changed');
assert(bridgeSettings.includes('WHATSAPP_JOB_DEADLINE_DEFAULT = 0'), 'Black Jhon total deadline must stay disabled');
assert(service.includes('WHATSAPP_ML_RESEARCH_DEADLINE_SECONDS = 0'), 'Mercado Livre total deadline must stay disabled');
assert(service.includes('def _normalize_tool_result_contract('), 'tool result contract normalization is missing');
assert(!service.includes('def _deterministic_direct_query_plan('), 'obsolete deterministic direct router is still present');
assert(bridgeSettings.includes('WHATSAPP_RESPONSE_PROVIDER_POLICY_DEFAULT = "codex_only"'), 'Codex-only response policy is not the default');
assert(ui.includes('response_provider_policy:'), 'response provider policy is not preserved by the UI');
assert(ui.includes('data_selection_worker_count:'), 'data-selection capacity is not saved by the UI');
assert(!ui.includes('function_manager_worker_count:'), 'legacy function-manager settings are still written by the UI');
assert(service.includes('def _local_web_fallback_context('), 'local web fallback circuit breaker is missing');
assert(bridgeStore.includes('PRAGMA journal_mode=WAL'), 'SQLite bridge state is not configured for WAL');
assert(bridgeStore.includes('CREATE TABLE IF NOT EXISTS assistant_jobs'), 'persistent AssistantJob table is missing');
assert(bridgeStore.includes('CREATE TABLE IF NOT EXISTS audit_events'), 'immutable approval audit table is missing');
assert(reportFiles.includes('REPORT_FILE_TTL_SECONDS = 7 * 24 * 60 * 60'), 'report retention is not seven days');
assert(reportFiles.includes('REPORT_DOCUMENT_MAX_BYTES = 10 * 1024 * 1024'), 'document size limit changed');
assert(gateway.includes('async function messageProgress('), 'gateway progress endpoint is missing');
assert(gateway.includes('requestedStatus === "retry"'), 'Codex runtime failures are not preserved for retry');
assert(gateway.includes('"task_conversation"'), 'conversation-agent proactive event is not supported');
assert(bridgeContracts.includes('class WhatsappPhoneSettingsRequest(BaseModel)'), 'phone settings request contract is missing');
assert(bridgeContracts.includes('class WhatsappPhoneRegistrationRequest(BaseModel)'), 'direct phone registration contract is missing');
assert(service.includes('from backend.services.whatsapp.contracts import ('), 'bridge compatibility facade is missing');
assert(bridgeFormatting.includes('def _whatsapp_response_parts('), 'WhatsApp formatting component is missing');
assert(bridgeGateway.includes('def gateway_request('), 'WhatsApp gateway component is missing');
assert(bridgeSettings.includes('def dual_agent_settings('), 'WhatsApp settings component is missing');
assert(bridgeMedia.includes('def image_mime('), 'WhatsApp media component is missing');
assert(bridgeMessage.includes('def message_prompt('), 'WhatsApp message component is missing');
assert(bridgeIntent.includes('def mutation_intent('), 'WhatsApp intent component is missing');
assert(bridgeRetryPolicy.includes('def worker_disposition('), 'WhatsApp retry component is missing');
assert(bridgeToolResults.includes('def stock_balance_contract('), 'WhatsApp tool-result component is missing');
assert(service.includes('def whatsapp_bridge_update_phone_settings('), 'phone settings endpoint is missing');
assert(service.includes('def _normalize_phone_ai_behavior('), 'per-phone AI behavior normalization is missing');
assert(service.includes('"phone_ai_behavior": phone_ai_behavior'), 'per-phone AI behavior is not attached to WhatsApp tasks');
assert(bridgeMessage.includes('Instrucoes administrativas especificas para atender este numero'), 'per-phone AI behavior is not applied to provider prompts');
assert(codexWorkerSetup.includes('Instrucao administrativa especifica para este numero de WhatsApp'), 'per-phone AI behavior is not applied as a Codex developer instruction');
assert(service.includes('def whatsapp_bridge_register_phone('), 'direct phone registration endpoint is missing');
assert(service.includes('def whatsapp_bridge_send_adhoc_message('), 'ad hoc message endpoint is missing');
assert(service.includes('def whatsapp_bridge_voice_preflight('), 'voice preflight service is missing');
assert(service.includes('def whatsapp_bridge_voice_enable('), 'voice enable service is missing');
assert(service.includes('Mensagens avulsas estao desativadas'), 'ad hoc messages are not blocked by the question-only policy');
assert(service.includes('Ligacoes foram removidas'), 'legacy voice routes do not explain that calls were removed');
assert(service.includes('settings["send_ml_question_suggestions"]'), 'question suggestions do not honor the selected phone preference');
assert(!service.includes('def _start_phone_notification_report_scan('), 'removed scheduled report scanner is still present');
assert(!service.includes('def scheduled_report_period('), 'removed scheduled report implementation is still present');
assert(bridgeSettings.includes('WHATSAPP_CHANNEL_MODE = "question_replies_only_v1"'), 'question-only channel mode is missing');
assert(bridgeSettings.includes('WHATSAPP_CONVERSATION_SCOPE = "whatsapp_phone_isolated"'), 'WhatsApp and Sidebar are not declared isolated');
assert(bridgeIntent.includes('def question_only_block_reason('), 'question-only inbound guard is missing');
assert(!service.includes('codex_actions.create_proposal('), 'removed generic WhatsApp action proposal flow is still present');
const taskTransitionStart = service.indexOf('def _forward_task_transitions(');
const taskTransitionEnd = service.indexOf('\ndef ', taskTransitionStart + 4);
const taskTransitionForwarder = service.slice(taskTransitionStart, taskTransitionEnd);
assert(taskTransitionStart >= 0, 'task transition state mirror is missing');
assert(!taskTransitionForwarder.includes('_post_proactive('), 'sidebar tasks must never be forwarded to WhatsApp');
assert(wranglerExample.includes('database_id = "REPLACE_WITH_D1_DATABASE_ID"'), 'D1 placeholder changed');
assert(setup.includes('database_id = "REPLACE_WITH_D1_DATABASE_ID"'), 'setup no longer replaces the D1 placeholder');
assert(wranglerExample.includes('META_GRAPH_API_VERSION = "REPLACE_WITH_CURRENT_META_GRAPH_VERSION"'), 'Graph API placeholder changed');
assert(wranglerExample.includes('TYPING_PULSES_DAY_LIMIT = "10000"'), 'typing daily limit missing from Wrangler example');
assert(setup.includes('REPLACE_WITH_CURRENT_META_GRAPH_VERSION'), 'setup no longer replaces the Graph API placeholder');
assert(gateway.includes('MAX_BINDINGS_PER_USER = 3'), 'gateway per-user binding limit changed');
assert(gateway.includes('binding_limit_reached'), 'gateway no longer blocks a fourth number');
assert(gateway.includes('async function registerBinding('), 'gateway direct binding registration is missing');
assert(gateway.includes('/bridge/bindings/register'), 'gateway direct binding route is missing');
assert(gateway.includes('/bridge/welcome'), 'gateway welcome route is missing');
assert(gateway.includes('/bridge/messages/send'), 'gateway ad hoc message route is missing');
assert(gateway.includes('feature_removed_question_only_mode'), 'removed gateway routes do not return the compatibility error');
assert(!gateway.includes('async function welcomeMessage('), 'removed welcome sender is still present');
assert(!gateway.includes('async function adhocMessage('), 'removed ad hoc sender is still present');
assert(gateway.includes('/bridge/heartbeat'), 'local heartbeat route is missing');
assert(gateway.includes('const documentMatch = url.pathname.match(') && gateway.includes('/document$/);'), 'authenticated message document route is missing');
assert(gateway.includes('/bridge/proactive/document'), 'authenticated proactive document route is missing');
assert(gateway.includes('bridge_heartbeats'), 'gateway heartbeat persistence is missing');
assert(gateway.includes('offline_notified_at'), 'offline notification deduplication is missing');
assert(gateway.includes('template_not_approved'), 'template approval blocker is missing');
assert(gateway.includes('jk_black_jhon_nova_pergunta'), 'new-question utility template definition is missing');
assert(gateway.includes('jk_black_jhon_nova_pergunta_v2'), 'versioned question-suggestion template definition is missing');
assert(gateway.includes('ppv_view_pending'), 'question-suggestion quick-reply payload is missing');
assert(gateway.includes('type: "QUICK_REPLY", text: "Ver sugestao"'), 'question-suggestion quick-reply button is missing');
assert(gateway.includes('/webhooks/openai/realtime'), 'legacy OpenAI Realtime route is missing');
assert(!gateway.includes('verifyOpenAIWebhook'), 'removed OpenAI Realtime implementation is still present');
assert(!gateway.includes("message_type='adhoc_text'"), 'removed ad hoc outbox implementation is still present');
assert(!gateway.includes('Este número não possui permissão para acessar o Black Jhon'), 'removed unauthorized access sender is still present');
assert(!gateway.includes("event_type='unauthorized_access_notice'"), 'removed unauthorized access outbox flow is still present');
assert(!gateway.includes('["weekly_report", "monthly_report"].includes(eventType)'), 'removed scheduled report event types are still supported');
assert(gateway.includes('typing_indicator: { type: "text" }'), 'Meta typing payload missing');
assert(gateway.includes('TYPING_PULSES_DAY_LIMIT'), 'typing daily free-tier guard missing');
assert(gateway.includes('O erro tecnico ficou registrado no JK Sistema'), 'gateway exposes raw local errors to WhatsApp');
assert(manifest.requiredSourceFiles.includes('backend/services/whatsapp_bridge.py'), 'bridge missing from installer verification');
const whatsappComponentFiles = [
  'backend/services/whatsapp/__init__.py',
  'backend/services/whatsapp/contracts.py',
  'backend/services/whatsapp/formatting.py',
  'backend/services/whatsapp/gateway.py',
  'backend/services/whatsapp/settings.py',
  'backend/services/whatsapp/media.py',
  'backend/services/whatsapp/message.py',
  'backend/services/whatsapp/intent.py',
  'backend/services/whatsapp/retry_policy.py',
  'backend/services/whatsapp/tool_results.py',
];
for (const relativeFile of whatsappComponentFiles) {
  assert(manifest.requiredSourceFiles.includes(relativeFile), `${relativeFile} missing from source verification`);
  assert(manifest.requiredPackagedFiles.includes(`local_app/${relativeFile}`), `${relativeFile} missing from package verification`);
  assert(manifest.requiredPackagedSourceParity.includes(relativeFile), `${relativeFile} missing from package parity verification`);
}
assert(
  manifest.requiredSourceDirectories.some(item => item.path === 'backend/services/whatsapp' && item.minFiles >= 11),
  'WhatsApp component source directory is not protected by installer verification',
);
assert(
  manifest.requiredPackagedDirectories.some(item => item.path === 'local_app/backend/services/whatsapp' && item.minFiles >= 11),
  'WhatsApp component packaged directory is not protected by installer verification',
);
assert(manifest.requiredSourceFiles.includes('backend/services/whatsapp_bridge_store.py'), 'SQLite bridge store missing from installer verification');
assert(manifest.requiredSourceFiles.includes('backend/services/whatsapp_report_files.py'), 'report document runtime missing from installer verification');
assert(!manifest.requiredSourceFiles.includes('backend/services/whatsapp_voice.py'), 'removed voice runtime is still packaged');
assert(!manifest.requiredSourceFiles.includes('backend/services/whatsapp/report_scheduling.py'), 'removed report scheduler is still packaged');
assert(!manifest.requiredSourceFiles.includes('backend/services/whatsapp/runtime/scheduler.py'), 'removed runtime scheduler is still packaged');
assert(manifest.requiredSourceFiles.includes('backend/services/codex_whatsapp_agents.py'), 'dual Codex runtime missing from installer verification');
assert(manifest.requiredSourceFiles.includes('static/configuracoes-whatsapp.js'), 'WhatsApp UI missing from installer verification');
assert(manifest.forbiddenLocalAppFilters.includes('info/**'), 'local secrets/model directory must remain unpackaged');

console.log('WHATSAPP_CONFIG_REGRESSION_OK');
