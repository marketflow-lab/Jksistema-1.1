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
  const aiModel = byId('waAiModel');
  const codexReasoning = byId('waCodexReasoning');
  const codexReasoningWrap = byId('waCodexReasoningWrap');
  const enabled = byId('waEnabled');
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
    const headers = { ...authHeaders(), ...(options.headers || {}) };
    if (options.body && !headers['Content-Type']) headers['Content-Type'] = 'application/json';
    const response = await fetch(path, { cache: 'no-store', ...options, headers });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.success === false) {
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

  function updateCodexReasoningVisibility() {
    const codexSelected = String(aiModel.value || '').toLowerCase().startsWith('codex:');
    codexReasoningWrap.classList.toggle('hidden', !codexSelected);
    codexReasoning.disabled = !codexSelected;
  }

  async function loadAiModels(selected = '') {
    if (aiModelsLoaded) return;
    const data = await request('/api/ia/modelos');
    const groups = [
      { label: 'Codex', itens: data.codex || [] },
      { label: 'Vertex AI', itens: data.vertex || [] },
      { label: 'OpenAI', itens: data.openai || [] },
      { label: 'DeepSeek', itens: data.deepseek || [] },
      { label: 'Gemini API', itens: data.gemini || [] },
    ];
    if (typeof preencherSelectModeloIA === 'function') {
      preencherSelectModeloIA(aiModel, groups, selected || 'codex:gpt-5.5', 'codex:gpt-5.5');
    } else {
      aiModel.replaceChildren();
      groups.forEach(groupInfo => {
        if (!Array.isArray(groupInfo.itens) || !groupInfo.itens.length) return;
        const group = document.createElement('optgroup');
        group.label = groupInfo.label;
        groupInfo.itens.forEach(model => {
          const option = document.createElement('option');
          option.value = String(model.name || '');
          option.textContent = String(model.display_name || model.name || '');
          group.appendChild(option);
        });
        aiModel.appendChild(group);
      });
      aiModel.value = selected || 'codex:gpt-5.5';
    }
    aiModelsLoaded = true;
    updateCodexReasoningVisibility();
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

  function renderTemplates(templates) {
    const box = byId('waTemplates');
    box.replaceChildren();
    const expected = [
      'jk_joao_tarefa_concluida',
      'jk_joao_aprovacao_pendente',
      'jk_joao_alerta_operacional',
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
    const welcomeInput = byId('userWaWelcomeMessage');
    const sendWelcomeInput = byId('userWaSendWelcome');
    if (!box || !counter || !saveButton || !nameInput || !numberInput || !questionsInput || !weeklyInput || !monthlyInput || !welcomeInput || !sendWelcomeInput) return;
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
      const questions = phoneOption('Enviar sugestão de pergunta do Mercado Livre', settings.send_ml_question_suggestions !== false, remoteMachine);
      const weekly = phoneOption('Enviar relatório semanal', settings.send_weekly_report === true, remoteMachine);
      const monthly = phoneOption('Enviar relatório mensal', settings.send_monthly_report === true, remoteMachine);
      options.append(questions.wrapper, weekly.wrapper, monthly.wrapper);
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
          send_ml_question_suggestions: questions.input.checked,
          send_weekly_report: weekly.input.checked,
          send_monthly_report: monthly.input.checked,
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
    const joao = payload.joao || {};
    const runtime = payload.runtime || {};
    const counts = worker.counts || {};
    const usage = worker.usage || {};
    const zero = worker.zero_cost || payload.zero_cost || {};
    const meta = worker.meta || {};
    if (firstRender) {
      workerUrl.value = payload.worker_url || '';
      businessPhone.value = payload.business_phone || '';
      aiModel.dataset.selectedModel = payload.ai_model || 'codex:gpt-5.5';
      codexReasoning.value = payload.codex_reasoning_effort || 'xhigh';
      firstRender = false;
    }
    enabled.checked = payload.enabled === true;
    bridgeToken.value = '';
    bridgeToken.placeholder = payload.bridge_token_configured
      ? 'Token configurado automaticamente'
      : 'Execute a implantação para gerar o token';

    updateCodexReasoningVisibility();
    const health = byId('waHealthGrid');
    health.replaceChildren(
      healthItem('Worker', worker.worker === true),
      healthItem('D1', worker.d1 === true),
      healthItem('KV mídia', worker.kv === true),
      healthItem('Meta', meta.configured === true),
      healthItem('Whisper', whisper.ready === true),
      healthItem('João', joao.ready === true),
    );
    renderPersonalNumbers(payload);
    byId('waPolicyUntil').textContent = `${zero.policy_valid === false ? 'VENCIDA · ' : ''}${zero.valid_until || zero.policy_valid_until || '—'}`;
    byId('waLastProcessing').textContent = runtime.last_processing_at || '—';
    const progress = Math.max(0, Math.min(100, Number(whisper.progress_percent || 0)));
    byId('waWhisperProgress').value = progress;
    byId('waWhisperText').textContent = whisper.ready
      ? `Pronto · ${formatBytes(whisper.downloaded_bytes)} · CPU int8`
      : `${whisper.download_status || 'indisponível'} · ${progress}% · ${formatBytes(whisper.downloaded_bytes)}`;
    byId('waUsageText').textContent = [
      `KV ativo: ${formatBytes(usage.media_active_bytes)} / 900 MB`,
      `uploads: ${Number(usage.media_uploads_month || 0)} / 10.000`,
      `downloads: ${Number(usage.media_downloads_month || 0)} / 50.000`,
    ].join(' · ');
    byId('waPendingText').textContent = [
      `entrada pendente: ${Number(counts.inbox_pending || 0)}`,
      `saída/alertas retidos: ${Number(counts.outbox_pending || 0)}`,
      `tarefas locais: ${Number(runtime.pending_local_tasks || 0)}`,
    ].join(' · ');
    renderTemplates(worker.templates || []);
    errorBox.textContent = runtime.last_error || whisper.download_error || worker.error || '';
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
      ai_model: String(aiModel.value || 'codex:gpt-5.5').trim(),
      codex_reasoning_effort: String(codexReasoning.value || 'xhigh').trim(),
      enabled: enabled.checked,
    };
    const data = await request('/api/admin/whatsapp/config', { method: 'PUT', body: JSON.stringify(payload) });
    render(data);
    if (data.ai_model) aiModel.value = data.ai_model;
    if (data.codex_reasoning_effort) codexReasoning.value = data.codex_reasoning_effort;
    updateCodexReasoningVisibility();
    return data;
  }));

  byId('waTest').addEventListener('click', () => action('waTest', 'Testando...', () =>
    request('/api/admin/whatsapp/test', { method: 'POST' })
  ));

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
        send_ml_question_suggestions: byId('userWaNewQuestions').checked,
        send_weekly_report: byId('userWaNewWeekly').checked,
        send_monthly_report: byId('userWaNewMonthly').checked,
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

  byId('waSyncTemplates').addEventListener('click', () => action('waSyncTemplates', 'Sincronizando...', () =>
    request('/api/admin/whatsapp/templates/sync', { method: 'POST', body: JSON.stringify({ create_missing: true }) })
  ));

  targetUser.addEventListener('change', () => {
    if (latestPayload) renderPersonalNumbers(latestPayload);
    byId('waPairingResult').textContent = '';
  });
  aiModel.addEventListener('change', updateCodexReasoningVisibility);
  tab.addEventListener('click', () => loadStatus(true));
  loadStatus(true).then(async () => {
    await Promise.all([
      loadTargetUsers(),
      loadAiModels((latestPayload && latestPayload.ai_model) || aiModel.dataset.selectedModel || 'codex:gpt-5.5'),
    ]);
  }).catch(error => setStatus(error.message, 'error'));
  window.setInterval(() => {
    if (document.getElementById('tab-whatsapp')?.classList.contains('active')) loadStatus(true);
  }, 5000);
})();
