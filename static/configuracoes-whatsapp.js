(function () {
  'use strict';

  const permissions = JSON.parse(localStorage.getItem('permissions') || '{}');
  if (permissions.full !== true) return;

  const tab = document.getElementById('tabWhatsappBtn');
  const card = document.getElementById('cardWhatsappJoao');
  if (!tab || !card) return;
  tab.classList.remove('hidden');
  card.classList.remove('hidden');

  const byId = id => document.getElementById(id);
  const workerUrl = byId('waWorkerUrl');
  const bridgeToken = byId('waBridgeToken');
  const businessPhone = byId('waBusinessPhone');
  const targetUser = byId('waTargetUser');
  const conversationAgentModel = byId('waConversationAgentModel');
  const conversationAgentReasoning = byId('waConversationAgentReasoning');
  const taskAgentModel = byId('waTaskAgentModel');
  const taskAgentReasoning = byId('waTaskAgentReasoning');
  const conversationInterval = byId('waConversationInterval');
  const conversationWorkers = byId('waConversationWorkers');
  const conversationRuntimePool = byId('waConversationRuntimePool');
  const globalTaskAgents = byId('waGlobalTaskAgents');
  const enabled = byId('waEnabled');
  const voiceModel = byId('waVoiceModel');
  const voiceName = byId('waVoiceName');
  const voiceMaxMinutes = byId('waVoiceMaxMinutes');
  const voiceMaxConcurrent = byId('waVoiceMaxConcurrent');
  const statusBox = byId('waStatus');
  const errorBox = byId('waLastError');
  let firstRender = true;
  let busy = false;
  let latestPayload = null;
  let aiModelsLoaded = false;
  let userPanelTarget = null;
  const DEFAULT_WELCOME_MESSAGE = 'Olá! Seu número foi cadastrado no WhatsApp do JK Sistema. Seja bem-vindo(a)! Você já pode conversar com o Black Jhon por aqui.';

  function authHeaders() {
    if (typeof obterAuthHeaders === 'function') return obterAuthHeaders();
    const token = localStorage.getItem('access_token') || localStorage.getItem('token') || '';
    return token ? { Authorization: 'Bearer ' + token } : {};
  }

  async function request(path, options = {}) {
    const acceptFailure = options.acceptFailure === true;
    if ('acceptFailure' in options) {
      options = { ...options };
      delete options.acceptFailure;
    }
    const headers = { ...authHeaders(), ...(options.headers || {}) };
    if (options.body && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
    const response = await fetch(path, { cache: 'no-store', ...options, headers });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || (!acceptFailure && data.success === false)) {
      throw new Error(data.detail || data.error || data.message || `Erro HTTP ${response.status}`);
    }
    return data;
  }

  function setStatus(text, kind = '') {
    statusBox.className = 'status' + (kind ? ' ' + kind : '');
    statusBox.textContent = text || '';
  }

  function selectedTarget() {
    const option = targetUser.selectedOptions[0];
    return option && option.dataset.username && option.dataset.clientId
      ? { username: option.dataset.username, client_id: option.dataset.clientId }
      : null;
  }

  function normalizedUserTarget(user) {
    const username = String(user && user.username || '').trim().toLowerCase();
    const clientId = String(user && user.client_id || '').trim();
    return username && clientId ? { username, client_id: clientId } : null;
  }

  function setUserPhoneStatus(text, kind = '') {
    const box = byId('userWaStatus');
    if (!box) return;
    box.className = 'status' + (kind ? ' ' + kind : '');
    box.textContent = text || '';
  }

  window.jkWhatsappSelecionarUsuario = function (user) {
    userPanelTarget = normalizedUserTarget(user);
    if (userPanelTarget) {
      const match = Array.from(targetUser.options || []).find(option =>
        option.dataset.username === userPanelTarget.username && option.dataset.clientId === userPanelTarget.client_id
      );
      if (match) targetUser.value = match.value;
    }
    setUserPhoneStatus('');
    if (latestPayload) renderUserPhoneSettings(latestPayload);
  };

  async function loadTargetUsers() {
    const previous = selectedTarget();
    const data = await request('/api/admin/users');
    const users = Array.isArray(data.users) ? data.users.filter(user => user && user.active !== false) : [];
    const current = latestPayload && latestPayload.current_user ? latestPayload.current_user : null;
    if (current && current.username && !users.some(user => String(user.username || '').toLowerCase() === current.username && String(user.client_id || '') === current.client_id)) {
      users.unshift({ username: current.username, client_id: current.client_id, name: 'Usuário conectado', active: true });
    }
    users.sort((a, b) => String(a.username || '').localeCompare(String(b.username || ''), 'pt-BR'));
    targetUser.replaceChildren();
    users.forEach(user => {
      const option = document.createElement('option');
      option.value = `${String(user.client_id || '')}:${String(user.username || '').toLowerCase()}`;
      option.dataset.username = String(user.username || '').toLowerCase();
      option.dataset.clientId = String(user.client_id || '');
      option.textContent = `${user.username}${user.name ? ` — ${user.name}` : ''} (#${user.client_id || '-'})`;
      targetUser.appendChild(option);
    });
    const preferred = previous || current;
    if (preferred) {
      const match = Array.from(targetUser.options).find(option => option.dataset.username === preferred.username && option.dataset.clientId === preferred.client_id);
      if (match) targetUser.value = match.value;
    }
    targetUser.disabled = !users.length;
    if (latestPayload) renderPersonalNumbers(latestPayload);
  }

  async function loadAiModels(selectedConversation = '', selectedTask = '') {
    if (aiModelsLoaded) return;
    const data = await request('/api/ia/modelos');
    const models = (Array.isArray(data.codex) ? data.codex : []).map(item => ({
      value: String(item.name || '').replace(/^codex:/i, ''),
      label: String(item.display_name || item.name || ''),
    })).filter(item => item.value);
    [['gpt-5.6-luna', 'Codex conversacional (gpt-5.6-luna)'], ['gpt-5.6-sol', 'Codex de tarefa (gpt-5.6-sol)']].forEach(([value, label]) => {
      if (!models.some(item => item.value === value)) models.push({ value, label });
    });
    function fill(select, selected, fallback) {
      select.replaceChildren();
      models.forEach(model => {
        const option = document.createElement('option');
        option.value = model.value;
        option.textContent = model.label || model.value;
        select.appendChild(option);
      });
      select.value = selected || fallback;
      if (!select.value) {
        const option = document.createElement('option');
        option.value = selected || fallback;
        option.textContent = selected || fallback;
        select.appendChild(option);
        select.value = selected || fallback;
      }
    }
    fill(conversationAgentModel, selectedConversation, 'gpt-5.6-luna');
    fill(taskAgentModel, selectedTask, 'gpt-5.6-sol');
    aiModelsLoaded = true;
  }

  function formatBytes(value) {
    let bytes = Number(value || 0);
    if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB'];
    let unit = 0;
    while (bytes >= 1024 && unit < units.length - 1) { bytes /= 1024; unit += 1; }
    return `${bytes.toFixed(unit ? 1 : 0)} ${units[unit]}`;
  }

  function healthItem(label, ok) {
    const item = document.createElement('div');
    item.className = 'wa-health-item ' + (ok ? 'ok' : 'fail');
    item.textContent = `${ok ? 'OK' : 'BLOQUEADO'} · ${label}`;
    return item;
  }

  function formatDuration(value) {
    const seconds = Math.max(0, Number(value || 0));
    if (!Number.isFinite(seconds)) return 'duração indisponível';
    const minutes = Math.floor(seconds / 60);
    const remainder = Math.floor(seconds % 60);
    return minutes ? `${minutes} min ${String(remainder).padStart(2, '0')} s` : `${remainder} s`;
  }

  function renderVoice(payload) {
    const voice = payload.voice || {};
    const gateway = voice.gateway || {};
    const gatewayOpenAi = gateway.openai || {};
    const sip = gateway.sip || {};
    const local = voice.local || {};
    const state = byId('waVoiceState');
    const ready = voice.ready === true;
    const enabledVoice = voice.enabled === true;
    state.className = 'wa-voice-state' + (ready ? ' ready' : '');
    state.textContent = ready ? 'Pronta' : (enabledVoice ? 'Habilitada, aguardando validação' : 'Desabilitada');

    byId('waVoiceHealthGrid').replaceChildren(
      healthItem('Chave OpenAI existente', voice.api_key_configured === true),
      healthItem('Chaves sincronizadas', voice.key_match === true),
      healthItem('Realtime disponível no backend', local.dependency_installed === true),
      healthItem('Webhook OpenAI', gatewayOpenAi.webhook_secret_configured === true),
      healthItem('Projeto OpenAI', gatewayOpenAi.project_configured === true),
      healthItem('VPS SIP', sip.configured === true),
    );
    if (voiceModel) voiceModel.value = voice.model || 'gpt-realtime-2.1';
    if (voiceName) voiceName.value = voice.name || 'cedar';
    if (voiceMaxMinutes) voiceMaxMinutes.value = String(voice.max_call_minutes || 30);
    if (voiceMaxConcurrent) voiceMaxConcurrent.value = String(voice.max_concurrent_calls || 3);

    const counts = gateway.counts || {};
    const active = Number(local.active_call_count || counts.active_calls || 0);
    const callsToday = Number(counts.calls_today || 0);
    byId('waVoiceSummary').textContent = `${active} ligação(ões) ativa(s) · ${callsToday} hoje · limite de ${voice.max_concurrent_calls || 3} simultânea(s) · ${voice.max_call_minutes || 30} min por ligação. Consumo é mostrado por duração e tokens, sem estimar cobrança fora do faturamento da OpenAI.`;

    const callsBox = byId('waVoiceCalls');
    callsBox.replaceChildren();
    const calls = Array.isArray(voice.recent_calls) ? voice.recent_calls.slice(0, 5) : [];
    if (!calls.length) {
      const empty = document.createElement('div');
      empty.className = 'small';
      empty.textContent = 'Nenhuma ligação registrada para este usuário.';
      callsBox.appendChild(empty);
    } else {
      calls.forEach(call => {
        const row = document.createElement('div');
        row.className = 'wa-voice-call';
        const detail = document.createElement('div');
        const name = document.createElement('strong');
        name.textContent = `Telefone final ${call.phone_suffix || '—'} · ${call.status || 'indisponível'}`;
        const usage = call.usage || {};
        const totalTokens = Number(usage.total_tokens || 0);
        const meta = document.createElement('small');
        meta.textContent = `${formatDuration(call.duration_seconds)}${totalTokens ? ` · ${totalTokens.toLocaleString('pt-BR')} tokens` : ''}`;
        detail.append(name, meta);
        row.appendChild(detail);
        callsBox.appendChild(row);
      });
    }
    byId('waVoiceEnable').disabled = ready;
    byId('waVoiceDisable').disabled = !enabledVoice;
  }

  function renderTemplates(templates) {
    const box = byId('waTemplates');
    box.replaceChildren();
    const expected = [
      'jk_joao_tarefa_concluida',
      'jk_joao_aprovacao_pendente',
      'jk_joao_alerta_operacional',
      'jk_black_jhon_nova_pergunta',
    ];
    const indexed = new Map((Array.isArray(templates) ? templates : []).map(item => [String(item.name || ''), item]));
    expected.forEach(name => {
      const item = indexed.get(name) || {};
      const row = document.createElement('div');
      row.className = 'wa-template-item';
      const label = document.createElement('span');
      label.textContent = name;
      const state = document.createElement('strong');
      state.textContent = item.status ? `${item.status} / ${item.category || '-'}` : 'não sincronizado';
      const blocked = !item.status || ['PENDING', 'REJECTED', 'PAUSED', 'DISABLED', 'MISSING'].includes(String(item.status).toUpperCase());
      if (blocked) {
        state.textContent += ' · BLOQUEIO';
        state.style.color = '#ffd0cc';
      }
      row.append(label, state);
      box.appendChild(row);
    });
  }

  function renderPersonalNumbers(payload) {
    const target = selectedTarget();
    const allNumbers = Array.isArray(payload.personal_numbers) ? payload.personal_numbers : [];
    const list = target ? allNumbers.filter(item => item.username === target.username && item.client_id === target.client_id) : [];
    const limit = Math.max(1, Math.min(3, Number(payload.binding_limit || 3)));
    const box = byId('waPersonalPhones');
    box.replaceChildren();
    byId('waPersonalPhoneCount').textContent = `${list.length}/${limit}`;

    if (!list.length) {
      const empty = document.createElement('div');
      empty.className = 'wa-binding-item';
      const text = document.createElement('small');
      text.textContent = 'Nenhum número pessoal vinculado.';
      empty.appendChild(text);
      box.appendChild(empty);
    }

    list.forEach((item, index) => {
      const row = document.createElement('div');
      row.className = 'wa-binding-item';
      const identity = document.createElement('div');
      const phone = document.createElement('span');
      phone.textContent = item.phone_masked || `Número ${index + 1}`;
      const detail = document.createElement('small');
      const machineLabel = item.this_machine ? 'esta máquina' : 'outra máquina autorizada';
      detail.textContent = ` · ${item.access_label || (item.full_access ? 'Acesso total com confirmação pelo WhatsApp' : 'Somente consultas autorizadas')} · ${machineLabel}`;
      identity.append(phone, detail);
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'btn-danger';
      remove.textContent = 'Remover';
      remove.addEventListener('click', () => {
        if (!confirm(`Desvincular ${phone.textContent}?`)) return;
        action('waRevoke', 'Removendo...', () => request('/api/admin/whatsapp/revoke', {
          method: 'POST',
          body: JSON.stringify({ subject_id: item.subject_id, username: item.username, client_id: item.client_id }),
        }));
      });
      row.append(identity, remove);
      box.appendChild(row);
    });

    const pairButton = byId('waPair');
    const limitReached = !target || list.length >= limit;
    pairButton.dataset.keepDisabled = String(limitReached);
    pairButton.disabled = limitReached;
    pairButton.title = !target ? 'Selecione um usuário' : (limitReached ? `Limite de ${limit} números atingido` : `${limit - list.length} vaga(s) disponível(is)`);
    const revokeAll = byId('waRevoke');
    revokeAll.dataset.keepDisabled = String(!list.length);
    revokeAll.disabled = !list.length;
    renderUserPhoneSettings(payload);
  }

  function phoneOption(label, checked, disabled = false) {
    const wrapper = document.createElement('label');
    wrapper.className = 'check-item';
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.checked = checked === true;
    input.disabled = disabled;
    const text = document.createElement('span');
    text.textContent = label;
    wrapper.append(input, text);
    return { wrapper, input };
  }

  function displayPhoneNumber(value, fallback = '') {
    const digits = String(value || '').replace(/\D/g, '');
    if (digits.length === 13 && digits.startsWith('55')) {
      return `+55 (${digits.slice(2, 4)}) ${digits.slice(4, 9)}-${digits.slice(9)}`;
    }
    if (digits.length === 12 && digits.startsWith('55')) {
      return `+55 (${digits.slice(2, 4)}) ${digits.slice(4, 8)}-${digits.slice(8)}`;
    }
    return digits ? `+${digits}` : fallback;
  }

  async function runUserPhoneAction(button, label, task, successMessage = 'Configuração do telefone salva.') {
    if (!button || button.disabled) return null;
    const old = button.textContent;
    button.disabled = true;
    button.textContent = label;
    setUserPhoneStatus(label, 'loading');
    try {
      const result = await task();
      if (result && result.status && result.status.worker_url !== undefined) render(result.status);
      else if (result && result.worker_url !== undefined) render(result);
      setUserPhoneStatus(successMessage, 'success');
      return result;
    } catch (error) {
      setUserPhoneStatus(error.message, 'error');
      return null;
    } finally {
      button.disabled = button.dataset.keepDisabled === 'true';
      button.textContent = old;
    }
  }

  function renderUserPhoneSettings(payload) {
    const box = byId('userWaPhones');
    const counter = byId('userWaPhoneCount');
    const saveButton = byId('userWaSavePhone');
    const nameInput = byId('userWaNewPhoneName');
    const numberInput = byId('userWaNewPhoneNumber');
    const questionsInput = byId('userWaNewQuestions');
    const weeklyInput = byId('userWaNewWeekly');
    const monthlyInput = byId('userWaNewMonthly');
    const voiceInput = byId('userWaNewVoice');
    const primaryInput = byId('userWaNewPrimary');
    const welcomeInput = byId('userWaWelcomeMessage');
    const sendWelcomeInput = byId('userWaSendWelcome');
    if (!box || !counter || !saveButton || !nameInput || !numberInput || !questionsInput || !weeklyInput || !monthlyInput || !voiceInput || !primaryInput || !welcomeInput || !sendWelcomeInput) return;
    const target = userPanelTarget;
    const allNumbers = Array.isArray(payload.personal_numbers) ? payload.personal_numbers : [];
    const list = target
      ? allNumbers.filter(item => item.username === target.username && item.client_id === target.client_id)
      : [];
    const limit = Math.max(1, Math.min(3, Number(payload.binding_limit || 3)));
    counter.textContent = `${list.length}/${limit}`;
    counter.classList.toggle('empty', !list.length);
    const registrationDisabled = !target || list.length >= limit;
    saveButton.dataset.keepDisabled = String(registrationDisabled);
    saveButton.disabled = registrationDisabled;
    nameInput.disabled = registrationDisabled;
    numberInput.disabled = registrationDisabled;
    questionsInput.disabled = registrationDisabled;
    weeklyInput.disabled = registrationDisabled;
    monthlyInput.disabled = registrationDisabled;
    voiceInput.disabled = registrationDisabled;
    primaryInput.disabled = registrationDisabled;
    welcomeInput.disabled = registrationDisabled;
    sendWelcomeInput.disabled = registrationDisabled;
    saveButton.title = !target
      ? 'Selecione um usuário.'
      : (list.length >= limit ? `Limite de ${limit} números atingido.` : 'Salvar nome, número e configurações.');
    box.replaceChildren();

    if (!target) {
      const empty = document.createElement('div');
      empty.className = 'small';
      empty.textContent = 'Selecione um usuário da lista para configurar os telefones.';
      box.appendChild(empty);
      return;
    }
    if (!list.length) {
      const empty = document.createElement('div');
      empty.className = 'user-phone-card';
      empty.textContent = 'Nenhum telefone cadastrado para este usuário. Preencha o nome e o número acima para salvar.';
      box.appendChild(empty);
      return;
    }

    list.forEach((item, index) => {
      const settings = item.notification_settings || {};
      const remoteMachine = item.this_machine === false;
      const card = document.createElement('div');
      card.className = 'user-phone-card';

      const head = document.createElement('div');
      head.className = 'user-phone-card__head';
      const identity = document.createElement('div');
      identity.className = 'user-phone-card__identity';
      const title = document.createElement('strong');
      title.textContent = settings.label || item.phone_masked || `Telefone ${index + 1}`;
      const detail = document.createElement('small');
      detail.textContent = `${displayPhoneNumber(item.phone_number, item.phone_masked || 'número vinculado')} · ${remoteMachine ? 'configurado em outra máquina' : 'configurado nesta máquina'}`;
      identity.append(title, detail);
      head.append(identity);
      card.appendChild(head);

      const labelCaption = document.createElement('label');
      labelCaption.className = 'label';
      labelCaption.textContent = 'Nome para identificar o telefone';
      const labelInput = document.createElement('input');
      labelInput.type = 'text';
      labelInput.maxLength = 60;
      labelInput.placeholder = 'Ex.: Comercial, Gerência ou Financeiro';
      labelInput.value = settings.label || '';
      labelInput.disabled = remoteMachine;
      card.append(labelCaption, labelInput);

      const options = document.createElement('div');
      options.className = 'user-phone-options';
      const primary = phoneOption('Número principal: compartilhar a conversa com a Sidebar', settings.is_primary === true, remoteMachine);
      const questions = phoneOption('Enviar sugestão de pergunta do Mercado Livre', settings.send_ml_question_suggestions !== false, remoteMachine);
      const weekly = phoneOption('Enviar relatório semanal', settings.send_weekly_report === true, remoteMachine);
      const monthly = phoneOption('Enviar relatório mensal', settings.send_monthly_report === true, remoteMachine);
      const voiceCalls = phoneOption('Permitir ligações com o Black Jhon', settings.allow_voice_calls === true, remoteMachine);
      options.append(primary.wrapper, questions.wrapper, weekly.wrapper, monthly.wrapper, voiceCalls.wrapper);
      card.appendChild(options);

      const behaviorCaption = document.createElement('label');
      behaviorCaption.className = 'label';
      behaviorCaption.textContent = 'Como a IA deve se comportar com este usuário';
      const behaviorInput = document.createElement('textarea');
      behaviorInput.maxLength = 2000;
      behaviorInput.rows = 4;
      behaviorInput.placeholder = 'Ex.: Responda de forma objetiva, use linguagem simples e sempre informe a loja e o período consultado.';
      behaviorInput.value = settings.ai_behavior || '';
      behaviorInput.disabled = remoteMachine;
      const behaviorHint = document.createElement('div');
      behaviorHint.className = 'small';
      behaviorHint.textContent = 'Esta orientação vale somente para este número e não altera as permissões de acesso.';
      card.append(behaviorCaption, behaviorInput, behaviorHint);

      const actions = document.createElement('div');
      actions.className = 'actions';
      const save = document.createElement('button');
      save.type = 'button';
      save.className = 'btn-save';
      save.textContent = remoteMachine ? 'Configure na outra máquina' : 'Salvar configurações';
      save.disabled = remoteMachine;
      save.addEventListener('click', () => runUserPhoneAction(save, 'Salvando...', () => request('/api/admin/whatsapp/phone-settings', {
        method: 'PUT',
        body: JSON.stringify({
          subject_id: item.subject_id,
          username: item.username,
          client_id: item.client_id,
          label: labelInput.value.trim(),
          is_primary: primary.input.checked,
          send_ml_question_suggestions: questions.input.checked,
          send_weekly_report: weekly.input.checked,
          send_monthly_report: monthly.input.checked,
          allow_voice_calls: voiceCalls.input.checked,
          ai_behavior: behaviorInput.value.trim(),
        }),
      })));
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'btn-danger';
      remove.textContent = 'Remover telefone';
      remove.addEventListener('click', () => {
        if (!confirm(`Desvincular ${item.phone_masked || 'este telefone'}?`)) return;
        runUserPhoneAction(remove, 'Removendo...', () => request('/api/admin/whatsapp/revoke', {
          method: 'POST',
          body: JSON.stringify({ subject_id: item.subject_id, username: item.username, client_id: item.client_id }),
        }));
      });
      actions.append(save, remove);
      card.appendChild(actions);
      box.appendChild(card);
    });
  }

  function render(payload) {
    latestPayload = payload;
    const worker = payload.worker || {};
    const whisper = payload.whisper || {};
    const audioMessages = payload.audio_messages || {};
    const joao = payload.joao || {};
    const runtime = payload.runtime || {};
    const counts = worker.counts || {};
    const inboundMedia = worker.inbound_media || {};
    const inboundMediaCounts = inboundMedia.counts || {};
    const usage = worker.usage || {};
    const zero = worker.zero_cost || payload.zero_cost || {};
    const meta = worker.meta || {};
    if (firstRender) {
      workerUrl.value = payload.worker_url || '';
      businessPhone.value = payload.business_phone || '';
      conversationAgentModel.dataset.selectedModel = payload.conversation_agent_model || 'gpt-5.6-luna';
      taskAgentModel.dataset.selectedModel = payload.task_agent_model || 'gpt-5.6-sol';
      conversationAgentReasoning.value = payload.conversation_agent_reasoning || 'low';
      taskAgentReasoning.value = 'low';
      conversationInterval.value = String(payload.conversation_interval_seconds || 30);
      conversationWorkers.value = String(payload.conversation_worker_count || 4);
      conversationRuntimePool.value = String(payload.conversation_runtime_pool_size || 4);
      globalTaskAgents.value = String(payload.max_active_task_agents_global || 12);
      firstRender = false;
    }
    enabled.checked = payload.enabled === true;
    bridgeToken.value = '';
    bridgeToken.placeholder = payload.bridge_token_configured
      ? 'Token configurado automaticamente'
      : 'Execute a implantação para gerar o token';

    const health = byId('waHealthGrid');
    const dualRuntime = payload.dual_agent_runtime || {};
    health.replaceChildren(
      healthItem('Worker', worker.worker === true),
      healthItem('D1', worker.d1 === true),
      healthItem('KV mídia', worker.kv === true),
      healthItem('Meta', meta.configured === true),
      healthItem('Áudio WhatsApp', audioMessages.ready === true || (audioMessages.ready === undefined && whisper.ready === true)),
      healthItem('João', joao.ready === true),
      healthItem('Codex conversa', dualRuntime.ready === true),
      healthItem('Codex tarefa', Boolean(payload.task_agent_model)),
    );
    renderPersonalNumbers(payload);
    byId('waPolicyUntil').textContent = `${zero.policy_valid === false ? 'VENCIDA · ' : ''}${zero.valid_until || zero.policy_valid_until || '—'}`;
    byId('waLastProcessing').textContent = runtime.last_processing_at || '—';
    const progress = Math.max(0, Math.min(100, Number(whisper.progress_percent || 0)));
    byId('waWhisperProgress').value = progress;
    byId('waWhisperText').textContent = whisper.ready
      ? `Pronto · ${formatBytes(whisper.downloaded_bytes)} · CPU int8`
      : `${whisper.download_status || 'indisponível'} · ${progress}% · ${formatBytes(whisper.downloaded_bytes)}`;
    const audioReady = audioMessages.ready === true || (audioMessages.ready === undefined && whisper.ready === true);
    const audioPreflight = audioMessages.preflight || {};
    const audioQueue = audioMessages.queue || {};
    const runnerReady = audioMessages.runner_ready === true || audioPreflight.runner_ready === true;
    const modelReady = audioMessages.model_ready === true || audioPreflight.model_integrity === 'verified_sha256' || whisper.ready === true;
    const dependenciesReady = audioMessages.dependencies_ready === true
      || (audioPreflight.dependency_installed === true && audioPreflight.av_installed === true);
    const queueWaiting = Number(audioMessages.queue_depth || audioQueue.waiting || 0);
    const queueActive = Number(audioQueue.active || 0);
    const audioParts = [
      audioReady ? 'Pronto' : (audioMessages.state || whisper.download_status || 'indisponível'),
      `runner: ${runnerReady ? 'OK' : 'bloqueado'}`,
      `modelo: ${modelReady ? 'OK' : 'bloqueado'}`,
      `dependências: ${dependenciesReady ? 'OK' : 'bloqueadas'}`,
      `fila: ${queueWaiting}/4 · ativo: ${queueActive}/1`,
    ];
    if (audioMessages.last_error_code) audioParts.push(`último erro: ${audioMessages.last_error_code}`);
    byId('waWhisperText').textContent = audioParts.join(' · ');
    byId('waUsageText').textContent = [
      `KV ativo: ${formatBytes(usage.media_active_bytes)} / 900 MB`,
      `uploads: ${Number(usage.media_uploads_month || 0)} / 10.000`,
      `downloads: ${Number(usage.media_downloads_month || 0)} / 50.000`,
    ].join(' · ');
    byId('waPendingText').textContent = [
      `entrada pendente: ${Number(counts.inbox_pending || 0)}`,
      `mídias aguardando retry: ${Number(inboundMediaCounts.waiting_retry || counts.media_retry_pending || 0)}`,
      `saída/alertas retidos: ${Number(counts.outbox_pending || 0)}`,
      `dead letters: ${Number(counts.dead_letters || 0)}`,
      `bloqueadas por template: ${Number(counts.template_blocked_outbox || 0)}`,
      `tarefas locais: ${Number(runtime.pending_local_tasks || 0)}`,
    ].join(' · ');
    const templates = [...(worker.templates || [])];
    (worker.template_blockers || []).forEach(item => {
      if (!templates.some(existing => existing.name === item.name)) templates.push(item);
    });
    renderTemplates(templates);
    renderVoice(payload);
    errorBox.textContent = runtime.last_error
      || audioMessages.last_error_code
      || whisper.download_error
      || inboundMedia.migration_required
      || worker.error
      || '';
  }

  async function loadStatus(silent = false) {
    if (busy && silent) return;
    try {
      const data = await request('/api/admin/whatsapp/status');
      render(data);
      if (!silent) setStatus('Status atualizado.', 'success');
    } catch (error) {
      if (!silent) setStatus(error.message, 'error');
    }
  }

  async function action(buttonId, label, task) {
    if (busy) return;
    busy = true;
    const button = byId(buttonId);
    const old = button.textContent;
    button.disabled = true;
    button.textContent = label;
    setStatus(label, 'loading');
    try {
      const result = await task();
      if (result && result.status && result.status.worker_url !== undefined) render(result.status);
      setStatus('Operação concluída.', 'success');
      await loadStatus(true);
      return result;
    } catch (error) {
      setStatus(error.message, 'error');
      return null;
    } finally {
      busy = false;
      button.disabled = button.dataset.keepDisabled === 'true';
      button.textContent = old;
    }
  }

  byId('waSave').addEventListener('click', () => action('waSave', 'Salvando...', async () => {
    const payload = {
      worker_url: workerUrl.value.trim(),
      business_phone: businessPhone.value.trim(),
      agent_architecture: 'dual_codex',
      response_provider_policy: (latestPayload && latestPayload.response_provider_policy) || 'codex_only',
      conversation_agent_model: String(conversationAgentModel.value || 'gpt-5.6-luna').trim(),
      conversation_agent_reasoning: String(conversationAgentReasoning.value || 'low').trim(),
      task_agent_model: String(taskAgentModel.value || 'gpt-5.6-sol').trim(),
      task_agent_reasoning: 'low',
      conversation_interval_seconds: Number(conversationInterval.value || 30),
      wait_message_after_seconds: 15,
      wait_message_repeat_seconds: 30,
      partial_delivery_debounce_seconds: 2,
      job_deadline_seconds: 0,
      max_subtasks_per_job: 6,
      progress_messages_enabled: false,
      max_active_task_agents_per_conversation: 6,
      conversation_worker_count: Number(conversationWorkers.value || 4),
      conversation_runtime_pool_size: Math.max(Number(conversationRuntimePool.value || 4), Number(conversationWorkers.value || 4)),
      max_active_task_agents_global: Number(globalTaskAgents.value || 12),
      preserve_order_per_phone: true,
      data_selection_enabled: true,
      data_selection_worker_count: 4,
      data_selection_runtime_pool_size: 4,
      ai_model: (latestPayload && latestPayload.ai_model) || 'codex:gpt-5.5',
      codex_reasoning_effort: 'low',
      codex_reasoning_policy: 'fixed',
      codex_reasoning_max: 'low',
      orchestration_mode: 'all_when_codex_selected',
      progress_interval_seconds: 8,
      progress_explain_wait: false,
      active_task_policy: 'steer_or_queue',
      voice_model: String(voiceModel && voiceModel.value || 'gpt-realtime-2.1'),
      voice_transcription_model: 'gpt-4o-transcribe',
      voice_name: String(voiceName && voiceName.value || 'cedar'),
      voice_language: 'pt-BR',
      voice_max_call_minutes: Number(voiceMaxMinutes && voiceMaxMinutes.value || 30),
      voice_silence_timeout_seconds: 90,
      voice_long_task_offer_seconds: 90,
      voice_max_concurrent_calls: Number(voiceMaxConcurrent && voiceMaxConcurrent.value || 3),
      voice_progress_interval_seconds: 8,
      enabled: enabled.checked,
    };
    const data = await request('/api/admin/whatsapp/config', { method: 'PUT', body: JSON.stringify(payload) });
    render(data);
    if (data.conversation_agent_model) conversationAgentModel.value = data.conversation_agent_model;
    if (data.task_agent_model) taskAgentModel.value = data.task_agent_model;
    if (data.conversation_agent_reasoning) conversationAgentReasoning.value = data.conversation_agent_reasoning;
    if (data.task_agent_reasoning) taskAgentReasoning.value = data.task_agent_reasoning;
    if (data.conversation_interval_seconds) conversationInterval.value = String(data.conversation_interval_seconds);
    if (data.conversation_worker_count) conversationWorkers.value = String(data.conversation_worker_count);
    if (data.conversation_runtime_pool_size) conversationRuntimePool.value = String(data.conversation_runtime_pool_size);
    if (data.max_active_task_agents_global) globalTaskAgents.value = String(data.max_active_task_agents_global);
    return data;
  }));

  byId('waTest').addEventListener('click', () => action('waTest', 'Testando...', () =>
    request('/api/admin/whatsapp/test', { method: 'POST' })
  ));

  async function voiceAction(buttonId, pendingLabel, task) {
    if (busy) return;
    busy = true;
    const button = byId(buttonId);
    const box = byId('waVoiceActionStatus');
    const original = button.textContent;
    button.disabled = true;
    button.textContent = pendingLabel;
    box.className = 'status loading';
    box.textContent = pendingLabel;
    try {
      const result = await task();
      if (buttonId === 'waVoicePreflight') {
        box.className = 'status ' + (result.ready === true ? 'success' : 'error');
        box.textContent = result.ready === true
          ? 'Ligações prontas: chave, Realtime, webhook, projeto, SIP, gateway e backend foram validados.'
          : 'A validação encontrou pendências. Confira os itens bloqueados acima antes de habilitar.';
      } else {
        box.className = 'status success';
        box.textContent = buttonId === 'waVoiceEnable' ? 'Ligações habilitadas.' : 'Ligações desabilitadas.';
      }
      await loadStatus(true);
      return result;
    } catch (error) {
      box.className = 'status error';
      box.textContent = error.message || 'Falha na configuração das ligações.';
      button.disabled = false;
      return null;
    } finally {
      busy = false;
      button.textContent = original;
    }
  }

  byId('waVoicePreflight').addEventListener('click', () => voiceAction(
    'waVoicePreflight',
    'Validando...',
    () => request('/api/admin/whatsapp/voice/preflight', { method: 'POST' }),
  ));
  byId('waVoiceEnable').addEventListener('click', () => {
    if (!confirm('Habilitar ligações somente leitura nos telefones autorizados?')) return;
    voiceAction('waVoiceEnable', 'Habilitando...', () => request('/api/admin/whatsapp/voice/enable', {
      method: 'POST',
      body: JSON.stringify({ confirmed: true }),
    }));
  });
  byId('waVoiceDisable').addEventListener('click', () => {
    if (!confirm('Desabilitar novas ligações com o Black Jhon?')) return;
    voiceAction('waVoiceDisable', 'Desabilitando...', () => request('/api/admin/whatsapp/voice/disable', {
      method: 'POST',
      body: JSON.stringify({ confirmed: true }),
    }));
  });

  byId('waPair').addEventListener('click', () => action('waPair', 'Gerando...', async () => {
    const target = selectedTarget();
    if (!target) throw new Error('Selecione o usuário que utilizará este número.');
    const data = await request('/api/admin/whatsapp/pairing-code', {
      method: 'POST',
      body: JSON.stringify(target),
    });
    const box = byId('waPairingResult');
    box.className = 'status success';
    box.textContent = `${data.instruction} O código expira em 10 minutos, só pode ser usado uma vez e há ${data.remaining_slots} vaga(s) antes deste vínculo.`;
    return data;
  }));

  byId('userWaSavePhone').addEventListener('click', async () => {
    const target = userPanelTarget;
    const name = byId('userWaNewPhoneName').value.trim();
    const phoneNumber = byId('userWaNewPhoneNumber').value.trim();
    const welcomeMessage = byId('userWaWelcomeMessage').value.trim();
    const sendWelcomeMessage = byId('userWaSendWelcome').checked;
    if (!target) return setUserPhoneStatus('Selecione um usuário antes de salvar o telefone.', 'error');
    if (!name) return setUserPhoneStatus('Informe um nome para identificar o telefone.', 'error');
    if (!phoneNumber) return setUserPhoneStatus('Informe o número do WhatsApp.', 'error');
    if (sendWelcomeMessage && !welcomeMessage) return setUserPhoneStatus('Escreva a mensagem de boas-vindas antes de marcar o envio.', 'error');
    const button = byId('userWaSavePhone');
    const data = await runUserPhoneAction(button, 'Salvando...', () => request('/api/admin/whatsapp/phones', {
      method: 'POST',
      body: JSON.stringify({
        ...target,
        name,
        phone_number: phoneNumber,
        is_primary: byId('userWaNewPrimary').checked,
        send_ml_question_suggestions: byId('userWaNewQuestions').checked,
        send_weekly_report: byId('userWaNewWeekly').checked,
        send_monthly_report: byId('userWaNewMonthly').checked,
        allow_voice_calls: byId('userWaNewVoice').checked,
        welcome_message: welcomeMessage,
        send_welcome_message: sendWelcomeMessage,
      }),
    }), 'Telefone cadastrado e salvo.');
    if (!data) return;
    const welcome = data.welcome_message || {};
    if (welcome.requested === true) {
      if (welcome.status === 'sent') {
        setUserPhoneStatus('Telefone cadastrado e mensagem de boas-vindas enviada.', 'success');
      } else if (welcome.status === 'waiting_free_window') {
        setUserPhoneStatus('Telefone cadastrado. Pela regra da Meta, a boas-vindas será enviada automaticamente depois que esse telefone mandar a primeira mensagem.', 'success');
      } else if (['queued', 'retry'].includes(welcome.status)) {
        setUserPhoneStatus('Telefone cadastrado. A mensagem de boas-vindas foi encaminhada para envio.', 'success');
      } else {
        setUserPhoneStatus(`Telefone cadastrado, mas a mensagem de boas-vindas não foi enviada: ${welcome.error || welcome.status || 'erro desconhecido'}.`, 'error');
      }
    }
    byId('userWaNewPhoneName').value = '';
    byId('userWaNewPhoneNumber').value = '';
    byId('userWaNewQuestions').checked = true;
    byId('userWaNewWeekly').checked = false;
    byId('userWaNewMonthly').checked = false;
    byId('userWaNewVoice').checked = false;
    byId('userWaNewPrimary').checked = false;
    byId('userWaWelcomeMessage').value = DEFAULT_WELCOME_MESSAGE;
    byId('userWaSendWelcome').checked = true;
  });

  byId('userWaSendAdhoc').addEventListener('click', async () => {
    const phoneNumber = byId('userWaAdhocPhone').value.trim();
    const message = byId('userWaAdhocMessage').value.trim();
    if (!phoneNumber) return setUserPhoneStatus('Informe o número que receberá a mensagem.', 'error');
    if (!message) return setUserPhoneStatus('Escreva a mensagem que deseja enviar.', 'error');
    const button = byId('userWaSendAdhoc');
    const data = await runUserPhoneAction(button, 'Enviando...', () => request('/api/admin/whatsapp/messages', {
      method: 'POST',
      body: JSON.stringify({ phone_number: phoneNumber, message }),
    }), 'Mensagem encaminhada para envio.');
    if (!data) return;
    const delivery = data.delivery || {};
    if (delivery.status === 'sent') {
      setUserPhoneStatus('Mensagem avulsa enviada pelo WhatsApp.', 'success');
    } else if (delivery.status === 'waiting_free_window') {
      setUserPhoneStatus('Mensagem salva. Ela será enviada quando este número abrir a janela de atendimento no WhatsApp.', 'success');
    } else if (['queued', 'retry'].includes(delivery.status)) {
      setUserPhoneStatus('Mensagem avulsa encaminhada para envio.', 'success');
    } else {
      setUserPhoneStatus(`A mensagem não foi enviada: ${delivery.error || delivery.status || 'erro desconhecido'}.`, 'error');
      return;
    }
    byId('userWaAdhocPhone').value = '';
    byId('userWaAdhocMessage').value = '';
  });

  byId('userWaRefresh').addEventListener('click', async () => {
    const button = byId('userWaRefresh');
    await runUserPhoneAction(button, 'Atualizando...', async () => {
      const data = await request('/api/admin/whatsapp/status');
      render(data);
      return data;
    }, 'Telefones atualizados.');
  });

  byId('waRevoke').addEventListener('click', () => {
    const target = selectedTarget();
    if (!target) return setStatus('Selecione um usuário.', 'error');
    if (!confirm(`Desvincular todos os números de ${target.username}?`)) return;
    action('waRevoke', 'Desvinculando...', () => request('/api/admin/whatsapp/revoke', {
      method: 'POST',
      body: JSON.stringify({ revoke_all: true, ...target }),
    }));
  });

  byId('waDownloadWhisper').addEventListener('click', () => action('waDownloadWhisper', 'Iniciando...', () =>
    request('/api/admin/whatsapp/whisper/download', { method: 'POST' })
  ));

  byId('waAudioPreflight').addEventListener('click', () => action('waAudioPreflight', 'Validando áudio...', async () => {
    const result = await request('/api/admin/whatsapp/audio/preflight', { method: 'POST', acceptFailure: true });
    if (result.success === false) {
      const audio = result.audio_messages || {};
      const preflight = audio.preflight || {};
      throw new Error(`Áudio local bloqueado: ${audio.last_error_code || preflight.error_code || 'preflight_failed'}.`);
    }
    return result;
  }));

  byId('waSyncTemplates').addEventListener('click', () => action('waSyncTemplates', 'Sincronizando...', () =>
    request('/api/admin/whatsapp/templates/sync', { method: 'POST', body: JSON.stringify({ create_missing: true }) })
  ));

  targetUser.addEventListener('change', () => {
    if (latestPayload) renderPersonalNumbers(latestPayload);
    byId('waPairingResult').textContent = '';
  });
  tab.addEventListener('click', () => loadStatus(true));
  loadStatus(true).then(async () => {
    await Promise.all([
      loadTargetUsers(),
      loadAiModels(
        (latestPayload && latestPayload.conversation_agent_model) || conversationAgentModel.dataset.selectedModel || 'gpt-5.6-luna',
        (latestPayload && latestPayload.task_agent_model) || taskAgentModel.dataset.selectedModel || 'gpt-5.6-sol',
      ),
    ]);
  }).catch(error => setStatus(error.message, 'error'));
  window.setInterval(() => {
    if (document.getElementById('tab-whatsapp')?.classList.contains('active')) loadStatus(true);
  }, 5000);
})();
