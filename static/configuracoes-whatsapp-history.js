(function () {
  'use strict';

  const PAGE_SIZE = 20;
  const tabButton = document.getElementById('tabWhatsappHistoryBtn');
  const refreshButton = document.getElementById('waHistoryRefresh');
  const conversationList = document.getElementById('waHistoryConversationList');
  const timeline = document.getElementById('waHistoryTimeline');
  const title = document.getElementById('waHistoryTitle');
  const subtitle = document.getElementById('waHistorySubtitle');
  const status = document.getElementById('waHistoryStatus');
  const newerButton = document.getElementById('waHistoryNewer');
  const olderButton = document.getElementById('waHistoryOlder');
  const pageInfo = document.getElementById('waHistoryPageInfo');
  const saveButton = document.getElementById('btnSalvarTopo');

  if (!tabButton || !conversationList || !timeline) return;

  let conversations = [];
  let selectedConversationId = '';
  let currentOffset = 0;
  let currentTotal = 0;
  let loaded = false;

  function authHeaders() {
    return typeof obterAuthHeaders === 'function' ? obterAuthHeaders() : {};
  }

  function setStatus(message, type) {
    status.textContent = message || '';
    status.className = 'status' + (type ? ` ${type}` : '');
  }

  function emptyElement(message) {
    const element = document.createElement('div');
    element.className = 'wa-history-empty';
    element.textContent = message;
    return element;
  }

  function formatDate(value) {
    if (!value) return 'horário indisponível';
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return String(value);
    return new Intl.DateTimeFormat('pt-BR', {
      dateStyle: 'short',
      timeStyle: 'short'
    }).format(parsed);
  }

  async function fetchJson(url) {
    const response = await fetch(url, {
      headers: authHeaders(),
      cache: 'no-store'
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.detail || data.message || `Falha na consulta (${response.status}).`);
    }
    return data;
  }

  function renderConversationList() {
    conversationList.replaceChildren();
    if (!conversations.length) {
      conversationList.appendChild(emptyElement('Nenhuma conversa do WhatsApp foi encontrada para o seu usuário.'));
      return;
    }

    conversations.forEach((conversation) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'wa-history-phone';
      button.classList.toggle('active', conversation.conversation_id === selectedConversationId);
      button.dataset.conversationId = conversation.conversation_id;

      const phone = document.createElement('strong');
      const phoneText = conversation.phone_display || conversation.phone || 'Telefone';
      phone.textContent = conversation.label ? `${conversation.label} · ${phoneText}` : phoneText;
      const count = document.createElement('span');
      const exchanges = Number(conversation.exchange_count || 0);
      const active = Number(conversation.active_count || 0);
      count.textContent = !exchanges && conversation.registered && !conversation.last_inbound_at
        ? 'Cadastrado · aguardando primeira mensagem'
        : `${exchanges} ${exchanges === 1 ? 'interação' : 'interações'}${active ? ` · ${active} em andamento` : ''}`;
      const latest = document.createElement('small');
      latest.textContent = conversation.last_prompt_preview
        || (!conversation.last_inbound_at && conversation.registered
          ? 'Ainda não iniciou uma conversa com o Black Jhon.'
          : `Última atividade: ${formatDate(conversation.updated_at)}`);

      button.append(phone, count, latest);
      button.addEventListener('click', () => selectConversation(conversation.conversation_id));
      conversationList.appendChild(button);
    });
  }

  function createBubble(role, label, text, truncated, taskId) {
    const bubble = document.createElement('div');
    bubble.className = `wa-history-bubble ${role}`;
    const heading = document.createElement('strong');
    heading.textContent = label;
    const body = document.createElement('div');
    body.className = 'wa-history-text';
    body.textContent = text || 'Conteúdo indisponível.';
    bubble.append(heading, body);

    if (truncated) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'wa-history-more';
      button.textContent = 'Abrir texto completo';
      button.addEventListener('click', () => openFullMessage(taskId, role === 'user' ? 'prompt' : 'response', button));
      bubble.appendChild(button);
    }
    return bubble;
  }

  function renderMessages(messages) {
    timeline.replaceChildren();
    if (!messages.length) {
      timeline.appendChild(emptyElement('Ainda não há pares concluídos de pergunta e resposta neste telefone.'));
      return;
    }

    [...messages].reverse().forEach((message) => {
      const exchange = document.createElement('article');
      exchange.className = 'wa-history-exchange';
      const meta = document.createElement('div');
      meta.className = 'wa-history-meta';
      const kind = document.createElement('span');
      kind.className = 'wa-history-kind';
      kind.textContent = 'Mensagem';
      const details = document.createElement('span');
      details.textContent = formatDate(message.completed_at || message.created_at);
      meta.append(kind, details);
      exchange.append(
        meta,
        createBubble('user', 'Você pelo WhatsApp', message.prompt, message.prompt_truncated, message.task_id),
        createBubble('assistant', 'Black Jhon', message.response, message.response_truncated, message.task_id)
      );
      timeline.appendChild(exchange);
    });
    timeline.scrollTop = timeline.scrollHeight;
  }

  function showFullText(label, text) {
    const dialog = document.createElement('dialog');
    dialog.className = 'wa-history-dialog';
    const header = document.createElement('div');
    header.className = 'wa-history-dialog-head';
    const heading = document.createElement('strong');
    heading.textContent = label;
    const close = document.createElement('button');
    close.type = 'button';
    close.textContent = 'Fechar';
    close.addEventListener('click', () => dialog.close());
    header.append(heading, close);
    const pre = document.createElement('pre');
    pre.textContent = text || 'Conteúdo indisponível.';
    dialog.append(header, pre);
    dialog.addEventListener('close', () => dialog.remove());
    document.body.appendChild(dialog);
    dialog.showModal();
  }

  async function openFullMessage(taskId, field, button) {
    if (!selectedConversationId || !taskId) return;
    const original = button.textContent;
    button.disabled = true;
    button.textContent = 'Carregando...';
    try {
      const data = await fetchJson(
        `/api/codex/conversations/whatsapp/${encodeURIComponent(selectedConversationId)}/messages/${encodeURIComponent(taskId)}`
      );
      const message = data.message || {};
      showFullText(field === 'prompt' ? 'Pergunta completa' : 'Resposta completa', message[field] || '');
    } catch (error) {
      setStatus(error.message || 'Não foi possível abrir o texto completo.', 'error');
    } finally {
      button.disabled = false;
      button.textContent = original;
    }
  }

  async function loadMessages(offset) {
    const selected = conversations.find((item) => item.conversation_id === selectedConversationId);
    if (!selected) return;
    currentOffset = Math.max(0, Number(offset || 0));
    const selectedPhone = selected.phone_display || selected.phone || 'Conversa do WhatsApp';
    title.textContent = selected.label ? `${selected.label} · ${selectedPhone}` : selectedPhone;
    subtitle.textContent = !selected.last_inbound_at && selected.registered
      ? 'Telefone cadastrado. Aguardando a primeira mensagem para iniciar o histórico.'
      : 'Histórico somente leitura deste telefone no usuário atual.';
    timeline.replaceChildren(emptyElement('Carregando conversa...'));
    newerButton.disabled = true;
    olderButton.disabled = true;

    try {
      const data = await fetchJson(
        `/api/codex/conversations/whatsapp/${encodeURIComponent(selectedConversationId)}/messages?limit=${PAGE_SIZE}&offset=${currentOffset}`
      );
      const messages = Array.isArray(data.messages) ? data.messages : [];
      currentTotal = Number(data.total || 0);
      renderMessages(messages);
      const first = currentTotal ? currentOffset + 1 : 0;
      const last = Math.min(currentOffset + messages.length, currentTotal);
      pageInfo.textContent = `${first}–${last} de ${currentTotal}`;
      newerButton.disabled = currentOffset <= 0;
      olderButton.disabled = !data.has_more;
      setStatus('Histórico carregado em modo somente leitura.', 'success');
    } catch (error) {
      timeline.replaceChildren(emptyElement('Não foi possível carregar esta conversa.'));
      pageInfo.textContent = '—';
      setStatus(error.message || 'Falha ao carregar a conversa.', 'error');
    }
  }

  function selectConversation(conversationId) {
    selectedConversationId = conversationId;
    currentOffset = 0;
    renderConversationList();
    loadMessages(0);
  }

  async function loadConversations(force) {
    if (loaded && !force) return;
    setStatus('Carregando conversas do seu usuário...', 'loading');
    refreshButton.disabled = true;
    try {
      const data = await fetchJson('/api/codex/conversations/whatsapp?limit=100&offset=0');
      conversations = Array.isArray(data.conversations) ? data.conversations : [];
      loaded = true;
      if (!conversations.some((item) => item.conversation_id === selectedConversationId)) {
        selectedConversationId = conversations[0]?.conversation_id || '';
      }
      renderConversationList();
      if (selectedConversationId) {
        await loadMessages(0);
      } else {
        title.textContent = 'Nenhum telefone com histórico';
        subtitle.textContent = 'Os telefones cadastrados aparecerão aqui mesmo antes da primeira conversa.';
        timeline.replaceChildren(emptyElement('Nenhuma conversa foi encontrada para o seu usuário.'));
        pageInfo.textContent = '0 de 0';
        setStatus('Nenhuma conversa do WhatsApp encontrada.', '');
      }
    } catch (error) {
      conversationList.replaceChildren(emptyElement('Não foi possível carregar os telefones.'));
      setStatus(error.message || 'Falha ao carregar o histórico do WhatsApp.', 'error');
    } finally {
      refreshButton.disabled = false;
    }
  }

  document.querySelectorAll('.tab-btn').forEach((button) => {
    button.addEventListener('click', () => {
      const isHistory = button.dataset.tab === 'whatsapp-history';
      if (saveButton) saveButton.classList.toggle('hidden', isHistory);
      if (isHistory) loadConversations(false);
    });
  });
  refreshButton.addEventListener('click', () => loadConversations(true));
  newerButton.addEventListener('click', () => loadMessages(Math.max(0, currentOffset - PAGE_SIZE)));
  olderButton.addEventListener('click', () => loadMessages(currentOffset + PAGE_SIZE));
}());
