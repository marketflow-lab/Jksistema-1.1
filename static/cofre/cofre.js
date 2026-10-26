(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const state = {
    servers: [], server: null, channels: [], channel: null,
    offset: 0, total: 0, loaded: 0, lastDay: null, revision: 0, serverRevision: 0,
    request: null, messageRetry: null, displayedChannel: null, mediaController: new AbortController(),
    mediaCache: new Map(), objectUrls: new Set(), observer: null,
    importing: false, searching: null, query: '', trashMode: false,
    totalActive: 0, totalDeleted: 0, summaryRevision: 0,
    messageDrafts: new Map(), channelDrafts: new Map(), mutations: new Map(),
    editor: null, channelEditor: null, deleteTarget: null, deleting: false,
    session: null, sessionInvalidated: false, sessionUnavailable: false, archiveReady: false, accessReady: false,
    sharing: { configured: false, enabled: false, can_publish: false, revision: null, last_sync: null },
    sharingKnown: false, sharingError: false, sharingBusy: false, sharingRequest: null,
    lastSharingAttempt: 0, publishing: false, publishSession: null
  };
  const dateFormat = new Intl.DateTimeFormat('pt-BR', { day: 'numeric', month: 'long', year: 'numeric' });
  const timeFormat = new Intl.DateTimeFormat('pt-BR', { hour: '2-digit', minute: '2-digit' });
  const numberFormat = new Intl.NumberFormat('pt-BR');
  const SHARING_MIN_INTERVAL_MS = 60 * 1000;

  function node(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = String(text);
    return item;
  }

  function externalUrl(value) {
    try {
      const url = new URL(String(value || ''));
      if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) return null;
      return url.href;
    } catch (_error) { return null; }
  }

  function localAssetUrl(value, serverId) {
    if (!value || !serverId) return null;
    try {
      const url = new URL(String(value), window.location.href);
      const prefix = '/api/cofre/arquivos/' + encodeURIComponent(serverId) + '/';
      if (url.origin !== window.location.origin || !url.pathname.startsWith(prefix)) return null;
      if (!/^[a-f0-9]{64}\//i.test(url.pathname.slice(prefix.length))) return null;
      if (url.username || url.password || url.search || url.hash) return null;
      return url.pathname;
    } catch (_error) { return null; }
  }

  function link(value, label, className) {
    const url = externalUrl(value);
    if (!url) return null;
    const anchor = node('a', className, label || url);
    anchor.href = url;
    anchor.target = '_blank';
    anchor.rel = 'noopener noreferrer';
    anchor.referrerPolicy = 'no-referrer';
    return anchor;
  }

  function appendText(parent, text) {
    const value = String(text || '');
    const urls = /https?:\/\/[^\s<>"`]+/gi;
    let start = 0;
    for (const match of value.matchAll(urls)) {
      const raw = match[0].replace(/[.,;:!?\)\]}]+$/, '');
      parent.appendChild(document.createTextNode(value.slice(start, match.index)));
      const anchor = link(raw, raw);
      parent.appendChild(anchor || document.createTextNode(raw));
      start = match.index + raw.length;
    }
    parent.appendChild(document.createTextNode(value.slice(start)));
  }

  function authHeaders() {
    if (typeof obterAuthHeaders !== 'function') throw new Error('Sua sessão do JK está indisponível.');
    const headers = obterAuthHeaders();
    if (!Object.entries(headers).some(([key, value]) => key.toLowerCase() === 'authorization' && /^Bearer\s+\S+$/i.test(String(value)))) {
      throw new Error('Sua sessão do JK está indisponível.');
    }
    // Never authorize the Cofre using the legacy localStorage X-Client-ID fallback.
    return headers;
  }

  function errorMessage(data, fallback) {
    const detail = data && (data.detail || data.message);
    if (typeof detail === 'string') return detail;
    return detail && typeof detail.message === 'string' ? detail.message : fallback;
  }

  function captureSession() {
    const headers = authHeaders();
    let user = {};
    try { user = JSON.parse(localStorage.getItem('user_data') || '{}'); } catch (_error) {}
    const headerValues = Object.keys(headers).sort().map(key => [key.toLowerCase(), String(headers[key])]);
    // Identity and credentials stay in this page's memory, never URLs or persistence.
    const key = JSON.stringify([headerValues, localStorage.getItem('access_token'), user.client_id || '', user.tenant_id || '', user.user_id || user.id || '', user.username || '', user.company_id || user.empresa_id || user.company?.id || '']);
    return { key, headers: { ...headers } };
  }

  function invalidateSession() {
    if (state.sessionInvalidated) return;
    state.sessionInvalidated = true;
    state.sessionUnavailable = false;
    state.serverRevision += 1;
    state.summaryRevision += 1;
    cancelMessages();
    state.session = null;
    state.archiveReady = false;
    state.accessReady = false;
    state.sharingKnown = false;
    state.publishSession = null;
    renderSharing();
    state.totalDeleted = 0;
    updateTrashCount();
    state.messageDrafts.clear();
    state.channelDrafts.clear();
    state.mutations.clear();
    state.editor = null;
    state.channelEditor = null;
    state.deleteTarget = null;
    state.servers = [];
    state.server = null;
    state.channels = [];
    state.channel = null;
    for (const id of ['editMessageDialog', 'editChannelDialog', 'deleteMessageDialog', 'importDialog', 'publishSharingDialog']) $(id).close();
    for (const id of ['editAuthor', 'editContent', 'editChannelName', 'editChannelCategory', 'editChannelTopic']) $(id).value = '';
    $('editMessageLinks').replaceChildren();
    $('editMessageEmbeds').replaceChildren();
    $('deleteMessagePreview').textContent = '';
    $('editMessageSource').removeAttribute('href');
    $('editMessageSource').hidden = true;
    $('mediaTitle').textContent = '';
    $('importForm').reset();
    $('fileLabel').textContent = 'Escolher arquivo';
    for (const id of ['messageSearch', 'toggleTrash', 'editChannel', 'refreshMessages', 'railImport', 'sidebarImport', 'emptyImport', 'detailImport', 'footerImport']) $(id).disabled = true;
    $('messageSearch').value = '';
    $('channelIntro').hidden = true;
    $('emptyState').hidden = false;
    $('emptyDescription').textContent = 'Sua sessão mudou. Reabra o Cofre de Informações para carregar o acervo da conta atual.';
    $('emptyTitle').textContent = 'Sessão alterada';
    $('reauthenticateCofre').hidden = true;
    $('emptyFormat').hidden = true;
    $('channelName').textContent = 'Sessão alterada';
    $('channelTopic').textContent = 'Reabra este módulo para continuar.';
    setChannelsOpen(false);
    renderServers();
    renderChannels();
    renderDetails();
    notice('Sua sessão mudou. O contexto anterior foi encerrado. Reabra o Cofre de Informações para continuar.');
  }

  function pauseUnavailableSession() {
    if (state.sessionInvalidated || state.sessionUnavailable) return;
    state.sessionUnavailable = true;
    state.sharingError = true;
    state.serverRevision += 1;
    state.summaryRevision += 1;
    state.revision += 1;
    state.request?.abort();
    clearTimeout(state.searching);
    // Keep the displayed archive and object URLs; recovery never clears JK storage.
    for (const id of ['messageSearch', 'toggleTrash', 'editChannel', 'refreshMessages']) $(id).disabled = true;
    $('channelList').querySelectorAll('button').forEach(button => { button.disabled = true; });
    $('serverList').querySelectorAll('button').forEach(button => { button.disabled = true; });
    $('loadMore').hidden = true;
    $('timelineStatus').textContent = state.loaded ? 'As informações exibidas foram mantidas. Tente novamente para atualizar.' : '';
    if (!state.archiveReady && !state.channel) {
      $('emptyState').hidden = false;
      $('emptyTitle').textContent = 'Informações indisponíveis no momento';
      $('emptyDescription').textContent = 'Sua sessão do JK continua ativa. Tente novamente para consultar o acervo.';
      $('emptyFormat').hidden = true;
      $('channelName').textContent = 'Consulta indisponível';
      $('channelTopic').textContent = 'Tente novamente em instantes.';
      $('detailServer').textContent = 'Consulta indisponível';
      $('coverageBadge').textContent = 'Consulta indisponível';
      $('coverageDescription').textContent = 'Não foi possível consultar o histórico agora.';
      $('exportDate').textContent = 'A última cópia será exibida quando a consulta estiver disponível.';
    }
    $('reauthenticateCofre').hidden = $('emptyState').hidden;
    renderSharing();
    notice('As informações estão indisponíveis no momento. Tente novamente em instantes.', true);
  }

  function retryReadOnly() {
    if (!state.sessionUnavailable || state.importing || state.publishing || state.mutations.size) return;
    try { ensureSession(); } catch (_error) { return; }
    // A fresh GET boot cannot replay an edit, deletion, import or publication.
    window.location.reload();
  }

  function ensureSession(session = state.session) {
    let matches = false;
    if (!state.sessionInvalidated && session && state.session?.key === session.key) {
      try { matches = captureSession().key === session.key; } catch (_error) {}
    }
    if (!matches) {
      invalidateSession();
      throw new Error('Sua sessão mudou. Reabra o Cofre de Informações para continuar.');
    }
    return session;
  }

  async function request(path, options = {}, session = state.session) {
    ensureSession(session);
    if (state.sessionUnavailable && options.method && !['GET', 'HEAD'].includes(options.method) && path !== '/compartilhamento/sincronizar') {
      const error = new Error('As informações estão indisponíveis no momento. Tente novamente em instantes.');
      error.status = 503;
      throw error;
    }
    const response = await fetch('/api/cofre' + path, {
      cache: 'no-store', credentials: 'same-origin', ...options,
      redirect: 'error',
      headers: { ...session.headers, ...(options.headers || {}) }
    }).catch(error => {
      if (error.name === 'AbortError') throw error;
      throw new Error('Não foi possível consultar o acervo. Tente novamente.');
    });
    ensureSession(session);
    const data = await response.json().catch(() => null);
    ensureSession(session);
    if (!response.ok) {
      if (response.status === 401 && /\bbearer\b/i.test(response.headers.get('WWW-Authenticate') || '')) invalidateSession();
      if (response.status === 503 && data?.detail?.code === 'cofre_sync_unavailable') {
        state.sharingError = true;
        renderSharing();
      }
      if (response.status === 503 && data?.detail?.code === 'cofre_session_unavailable') pauseUnavailableSession();
      const fallback = response.status === 403 ? 'Sua conta não tem acesso ao Cofre de Informações.' : 'Não foi possível carregar o Cofre de Informações. Tente novamente.';
      const error = new Error(errorMessage(data, fallback));
      error.status = response.status;
      throw error;
    }
    if (!data || typeof data !== 'object' || Array.isArray(data)) throw new Error('Não foi possível consultar o acervo. Tente novamente.');
    return data;
  }

  function notice(message, isError) {
    if (state.sessionUnavailable) { message = 'As informações estão indisponíveis no momento. Tente novamente em instantes.'; isError = true; }
    $('notice').hidden = !message;
    $('notice').textContent = message || '';
    $('notice').classList.toggle('error', !!isError);
  }

  function canEditArchive() {
    return state.accessReady && state.sharingKnown && !state.sessionInvalidated && !state.sessionUnavailable &&
      state.sharing.can_publish &&
      (!state.sharing.enabled || !state.sharingError);
  }

  function requireWriteAccess() {
    if (!canEditArchive()) {
      notice(state.sharingKnown ? 'Este acervo está disponível para consulta na sua conta.' : 'Confira o estado do acervo antes de fazer alterações.', true);
      return false;
    }
    if (state.sharingBusy) { notice('Aguarde a sincronização em andamento.'); return false; }
    return true;
  }

  function renderSharing() {
    const sharing = state.sharing;
    const invalid = state.sessionInvalidated;
    const unavailable = state.sessionUnavailable;
    const editable = canEditArchive();
    $('cofreApp').classList.toggle('sharing-ready', state.accessReady && state.sharingKnown);
    $('sharingStrip').classList.toggle('sharing-company', sharing.enabled && !invalid);
    $('sharingStrip').classList.toggle('sharing-error', state.sharingError && !invalid);
    $('sharingStrip').classList.toggle('sharing-busy', state.sharingBusy && !invalid);
    $('sharingLabel').textContent = unavailable ? 'Informações indisponíveis' : invalid ? 'Sessão alterada' : sharing.enabled && !state.sharingError ? 'Compartilhado com a empresa' : state.sharingKnown || state.sharingError ? 'Cópia local' : 'Verificando acervo…';
    const synced = formatDate(sharing.last_sync);
    $('sharingDescription').textContent = unavailable ? 'Sua sessão do JK foi mantida. Tente novamente para consultar o acervo.'
      : invalid ? 'Reabra o módulo para carregar a conta atual.'
      : state.sharingBusy ? state.publishing ? 'Publicando acervo…' : 'Sincronizando acervo…'
      : state.sharingError ? 'Não foi possível sincronizar agora. A cópia disponível foi mantida.'
      : !state.sharingKnown ? 'Confira o acervo para continuar.'
      : sharing.enabled ? (sharing.can_publish ? 'As alterações são compartilhadas.' : 'Somente leitura.') + (synced ? ' Atualizado às ' + timeFormat.format(synced) + '.' : '')
      : sharing.configured && sharing.can_publish ? 'Publique para compartilhar com a empresa.' : 'Disponível neste computador.';
    $('syncSharing').textContent = unavailable ? 'Tentar novamente' : '↻ Sincronizar';
    $('syncSharing').hidden = invalid || (!unavailable && state.sharingKnown && !sharing.configured);
    $('syncSharing').disabled = state.sharingBusy || (!unavailable && !state.accessReady);
    $('publishSharing').hidden = invalid || unavailable || state.sharingError || !state.sharingKnown || !sharing.configured || !sharing.can_publish;
    $('publishSharing').disabled = state.sharingBusy || !state.accessReady;
    for (const control of document.querySelectorAll('[data-cofre-write]')) {
      control.hidden = !editable;
      control.disabled = !editable || state.sharingBusy || (control.id === 'editChannel' && !state.channel);
    }
    if (!editable) {
      $('messages').querySelectorAll('.message-actions').forEach(actions => actions.remove());
      if (state.accessReady && state.sharingKnown && sharing.configured) {
        for (const id of ['editMessageDialog', 'editChannelDialog', 'deleteMessageDialog', 'importDialog']) $(id).close();
      }
    }
    if (!sharing.can_publish && !state.publishing) $('publishSharingDialog').close();
  }

  function applySharingStatus(data) {
    const wasEditable = canEditArchive();
    const wasEnabled = state.sharing.enabled;
    for (const field of ['configured', 'enabled', 'can_publish']) {
      if (typeof data[field] === 'boolean') state.sharing[field] = data[field];
    }
    for (const field of ['revision', 'last_sync']) {
      if (Object.prototype.hasOwnProperty.call(data, field)) state.sharing[field] = data[field];
    }
    state.sharingKnown = true;
    state.sharingError = !!data.error;
    renderSharing();
    return wasEditable !== canEditArchive() || wasEnabled !== state.sharing.enabled;
  }

  async function refreshSharedArchive() {
    if (!state.archiveReady || state.sessionInvalidated) return;
    const navigation = state.serverRevision;
    const serverId = state.server?.id;
    const channelId = state.channel?.id;
    const data = await request('/servidores');
    if (navigation !== state.serverRevision) return;
    const servers = Array.isArray(data.servidores) ? data.servidores : [];
    if (!servers.length) { await loadServers(); return; }
    const selected = servers.find(server => server.id === serverId);
    if (!selected) {
      state.servers = servers;
      await selectServer(servers[0]);
      return;
    }
    const result = await request('/servidores/' + encodeURIComponent(selected.id) + '/canais');
    if (navigation !== state.serverRevision) return;
    state.servers = servers;
    state.server = result.servidor || selected;
    state.channels = Array.isArray(result.canais) ? result.canais : [];
    renderServers();
    const channel = state.channels.find(item => item.id === channelId);
    if (!channel) {
      if (state.channels.length) await selectChannel(state.channels[0]);
      else await selectServer(state.server);
      return;
    }
    state.channel = channel;
    state.query = $('messageSearch').value.trim();
    updateChannelHeading();
    renderChannels();
    renderDetails();
    await loadMessages(true, true);
  }

  async function synchronizeSharing({ initial = false, manual = false, reload = true } = {}) {
    if (state.sessionInvalidated || state.sessionUnavailable) return { changed: false, failed: true };
    if (state.sharingError && !initial && !manual) return { changed: false };
    if (state.sharingRequest) return state.sharingRequest;
    if (state.publishing || state.importing || state.mutations.size) {
      if (manual) notice('Aguarde a alteração em andamento para sincronizar.');
      return { changed: false };
    }
    const now = Date.now();
    if (!initial && !manual && now >= state.lastSharingAttempt && now - state.lastSharingAttempt < SHARING_MIN_INTERVAL_MS) return { changed: false };
    state.lastSharingAttempt = now;
    state.sharingBusy = true;
    renderSharing();
    const task = (async () => {
      try {
        const status = await request('/compartilhamento');
        let accessChanged = applySharingStatus(status);
        let result = { changed: false, ...status };
        if (initial || state.sharing.configured) {
          result = await request('/compartilhamento/sincronizar', { method: 'POST' });
          accessChanged = applySharingStatus(result) || accessChanged;
          if (result.success === false) {
            state.sharingError = true;
            renderSharing();
            return { ...result, changed: false, failed: true };
          }
        }
        if (reload && (result.changed || accessChanged)) await refreshSharedArchive();
        return { ...result, refreshed: reload && !!(result.changed || accessChanged) };
      } catch (_error) {
        if (!state.sessionInvalidated) { state.sharingError = true; renderSharing(); }
        return { changed: false, failed: true };
      } finally {
        state.sharingBusy = false;
        state.sharingRequest = null;
        renderSharing();
      }
    })();
    state.sharingRequest = task;
    return task;
  }

  async function refreshArchive() {
    if (state.sessionInvalidated || state.sessionUnavailable) return;
    const result = await synchronizeSharing();
    if (!state.sessionInvalidated && !state.sessionUnavailable && !result.refreshed && state.channel) await loadMessages(true, true);
  }

  function openPublishSharing() {
    if (!state.sharingKnown || !state.sharing.configured || !state.sharing.can_publish || state.sharingError || state.sessionInvalidated || state.sharingBusy) return;
    if (state.importing || state.mutations.size) { notice('Aguarde a alteração em andamento para publicar.'); return; }
    try { state.publishSession = ensureSession(); } catch (_error) { return; }
    feedback('publishSharingFeedback', '');
    $('syncBeforePublish').hidden = true;
    $('publishSharingDialog').showModal();
    $('cancelPublishSharing').focus();
  }

  async function publishSharing() {
    if (!requireWriteAccess()) return;
    if (state.publishing || !state.publishSession || !state.sharing.can_publish || state.sessionInvalidated) return;
    state.publishing = true;
    state.sharingBusy = true;
    for (const id of ['confirmPublishSharing', 'cancelPublishSharing', 'closePublishSharing']) $(id).disabled = true;
    feedback('publishSharingFeedback', 'Publicando acervo para a empresa…');
    renderSharing();
    try {
      const result = await request('/compartilhamento/publicar', { method: 'POST' }, state.publishSession);
      if (result.success === false) throw new Error('Publicação não concluída.');
      applySharingStatus(result);
      $('publishSharingDialog').close();
      try { await refreshSharedArchive(); }
      catch (_error) {
        if (!state.sessionInvalidated) {
          state.sharingError = true;
          notice('Acervo compartilhado com a empresa. Não foi possível atualizar a visualização agora.', true);
        }
        return;
      }
      notice('Acervo compartilhado com a empresa.');
    } catch (error) {
      if (!state.sessionInvalidated) {
        state.sharingError = true;
        $('syncBeforePublish').hidden = error.status !== 409;
        feedback('publishSharingFeedback', error.status === 409 ? 'Sincronize antes de publicar. Sua cópia foi mantida.' : 'Não foi possível publicar agora. Sua cópia foi mantida.', true);
      }
    } finally {
      state.publishing = false;
      state.sharingBusy = false;
      for (const id of ['confirmPublishSharing', 'cancelPublishSharing', 'closePublishSharing']) $(id).disabled = false;
      renderSharing();
    }
  }

  function initials(name) {
    return String(name || '?').trim().split(/\s+/).filter(Boolean).slice(0, 2).map((word) => Array.from(word)[0]).join('').toUpperCase() || '?';
  }

  function formatDate(value) {
    const date = new Date(value || '');
    return Number.isNaN(date.getTime()) ? null : date;
  }

  function setChannelsOpen(open) {
    $('cofreApp').classList.toggle('channels-open', open);
    $('sidebarScrim').hidden = !open;
    $('openChannels').setAttribute('aria-expanded', String(open));
    if (open) $('channelList').querySelector('button')?.focus();
  }

  function setDetailsOpen(open) {
    $('cofreApp').classList.toggle('details-hidden', !open);
    $('toggleDetails').setAttribute('aria-expanded', String(open));
  }

  function clearMedia() {
    $('mediaDialog').close();
    $('mediaImage').removeAttribute('src');
    state.observer?.disconnect();
    state.observer = null;
    state.mediaController.abort();
    state.mediaController = new AbortController();
    for (const url of state.objectUrls) URL.revokeObjectURL(url);
    state.objectUrls.clear();
    state.mediaCache.clear();
  }

  function cancelMessages() {
    state.revision += 1;
    state.request?.abort();
    state.request = null;
    clearTimeout(state.searching);
    clearMedia();
    state.loaded = 0;
    state.offset = 0;
    state.total = 0;
    state.lastDay = null;
    state.displayedChannel = null;
    state.messageRetry = null;
    $('messages').replaceChildren();
    $('loadMore').hidden = true;
    $('loadMore').disabled = false;
    $('timelineStatus').textContent = '';
  }

  function renderServers() {
    const list = $('serverList');
    list.replaceChildren();
    for (const server of state.servers) {
      const button = node('button', 'server-button', initials(server.name));
      button.title = server.name;
      button.setAttribute('aria-label', server.name);
      button.setAttribute('aria-pressed', String(state.server?.id === server.id));
      button.classList.toggle('active', state.server?.id === server.id);
      button.addEventListener('click', () => {
        selectServer(server);
        if (window.matchMedia('(max-width: 760px)').matches) setChannelsOpen(true);
      });
      list.appendChild(button);
    }
  }

  function renderDetails() {
    const server = state.server;
    $('serverName').textContent = server?.name || 'Seus servidores';
    $('detailServer').textContent = server?.name || 'Seu servidor';
    $('statChannels').textContent = server ? numberFormat.format(server.channel_count || 0) : '—';
    $('statMessages').textContent = server ? numberFormat.format(server.message_count || 0) : '—';
    $('statAttachments').textContent = server ? numberFormat.format(server.attachment_count || 0) : '—';
    const badge = $('coverageBadge');
    badge.classList.toggle('complete', !!server?.history_complete);
    badge.classList.toggle('partial', !!server && !server.history_complete);
    badge.textContent = !server ? 'Aguardando importação' : server.history_complete ? 'Histórico completo na cópia' : 'Histórico parcial';
    $('coverageDescription').textContent = !server ? 'Após importar, confira aqui a cobertura do histórico.' : server.history_complete
      ? 'Todos os canais desta cópia foram marcados como completos na coleta.'
      : 'A cópia inclui os dados coletados. Alguns canais podem ter mensagens anteriores ou anexos ainda não copiados.';
    const exported = formatDate(server?.exported_at);
    $('exportDate').textContent = exported ? dateFormat.format(exported) + ', às ' + timeFormat.format(exported) : server ? 'Data da cópia não informada.' : 'Nenhum servidor importado.';
    $('channelDetails').hidden = !state.channel;
    if (state.channel) {
      $('channelCoverage').textContent = numberFormat.format(state.channel.message_count || 0) + ' mensagens no acervo. ' + (state.channel.history_complete ? 'Histórico marcado como completo na coleta.' : 'Histórico parcial; pode haver mensagens anteriores.');
      const source = externalUrl(state.channel.url);
      $('sourceChannel').hidden = !source;
      if (source) $('sourceChannel').href = source;
      else $('sourceChannel').removeAttribute('href');
    }
  }

  function renderChannels() {
    const list = $('channelList');
    const previous = new Map(Array.from(list.querySelectorAll('details')).map((item) => [item.dataset.category, item.open]));
    list.replaceChildren();
    const categories = new Map();
    for (const channel of state.channels) {
      const category = String(channel.category || 'Canais');
      if (!categories.has(category)) categories.set(category, []);
      categories.get(category).push(channel);
    }
    for (const [category, channels] of categories) {
      const group = node('details', 'channel-category');
      group.dataset.category = category;
      group.open = previous.has(category) ? previous.get(category) : true;
      const summary = node('summary');
      summary.append(node('span', 'category-chevron', '⌄'), node('span', null, category), node('span', 'category-count', channels.length));
      group.appendChild(summary);
      for (const channel of channels) {
        const button = node('button', 'channel-button');
        const active = state.channel?.id === channel.id;
        button.classList.toggle('active', active);
        button.setAttribute('aria-current', active ? 'page' : 'false');
        button.title = channel.name + ' · ' + numberFormat.format(channel.message_count || 0) + ' mensagens';
        button.append(node('span', 'channel-icon', channel.kind === 'voice' ? '◖' : '#'), node('span', 'channel-label', channel.name), node('span', 'channel-message-count', numberFormat.format(channel.message_count || 0)));
        button.addEventListener('click', () => {
          if (state.channel?.id !== channel.id) selectChannel(channel);
          setChannelsOpen(false);
        });
        group.appendChild(button);
      }
      list.appendChild(group);
    }
    if (!state.channels.length) list.appendChild(node('p', 'sidebar-placeholder', 'Nenhum canal nesta cópia. Importe um arquivo com canais para começar.'));
  }

  async function loadServers(preferredId) {
    const data = await request('/servidores');
    state.servers = Array.isArray(data.servidores) ? data.servidores : [];
    if (!state.servers.length) {
      state.server = null;
      state.channel = null;
      state.channels = [];
      cancelMessages();
      renderServers();
      renderChannels();
      renderDetails();
      $('emptyState').hidden = false;
      $('channelIntro').hidden = true;
      $('messageSearch').disabled = true;
      $('refreshMessages').disabled = true;
      $('editChannel').disabled = true;
      $('toggleTrash').disabled = true;
      return;
    }
    const selected = state.servers.find((server) => server.id === preferredId) || state.servers[0];
    await selectServer(selected);
  }

  async function selectServer(server) {
    if (state.sessionUnavailable || state.sessionInvalidated) return;
    const revision = ++state.serverRevision;
    state.server = server;
    state.channel = null;
    state.channels = [];
    cancelMessages();
    $('messageSearch').disabled = true;
    $('messageSearch').value = '';
    $('refreshMessages').disabled = true;
    $('editChannel').disabled = true;
    $('toggleTrash').disabled = true;
    $('emptyState').hidden = true;
    $('channelIntro').hidden = true;
    $('channelName').textContent = server.name;
    $('channelTopic').textContent = 'Carregando canais…';
    $('channelList').replaceChildren(node('p', 'sidebar-placeholder', 'Carregando canais…'));
    $('timelineStatus').textContent = 'Carregando canais…';
    notice('');
    renderServers();
    renderDetails();
    try {
      const data = await request('/servidores/' + encodeURIComponent(server.id) + '/canais');
      if (revision !== state.serverRevision) return;
      state.server = data.servidor || server;
      state.channels = Array.isArray(data.canais) ? data.canais : [];
      renderChannels();
      renderDetails();
      if (state.channels.length) await selectChannel(state.channels[0]);
      else {
        $('timelineStatus').textContent = 'Este servidor ainda não tem canais importados.';
        $('channelTopic').textContent = 'Importe uma cópia para acrescentar canais.';
      }
    } catch (error) {
      if (revision !== state.serverRevision) return;
      $('channelList').replaceChildren(node('p', 'sidebar-placeholder', 'Não foi possível carregar os canais. Selecione o servidor novamente para tentar.'));
      $('timelineStatus').textContent = 'Os canais não puderam ser carregados.';
      notice(error.message, true);
    }
  }

  async function selectChannel(channel) {
    if (state.sessionUnavailable || state.sessionInvalidated) return;
    state.channel = channel;
    state.query = '';
    $('messageSearch').value = '';
    $('messageSearch').disabled = false;
    $('refreshMessages').disabled = false;
    $('editChannel').disabled = false;
    $('toggleTrash').disabled = false;
    updateChannelHeading();
    renderChannels();
    renderDetails();
    await loadMessages(true);
  }

  function authorName(message) {
    if (typeof message.author === 'string') return message.author || 'Autor não informado';
    return message.author?.display_name || message.author?.nickname || message.author?.name || 'Autor não informado';
  }

  function dateDivider(timestamp) {
    const date = formatDate(timestamp);
    const key = date ? date.getFullYear() + '-' + date.getMonth() + '-' + date.getDate() : 'unknown';
    if (key === state.lastDay) return null;
    state.lastDay = key;
    return node('div', 'day-divider', date ? dateFormat.format(date) : 'Data não informada');
  }

  async function authenticatedAsset(path, revision) {
    if (revision !== state.revision) return null;
    if (state.mediaCache.has(path)) return state.mediaCache.get(path);
    const session = ensureSession();
    const promise = (async () => {
      const response = await fetch(path, { headers: session.headers, cache: 'no-store', credentials: 'same-origin', redirect: 'error', signal: state.mediaController.signal });
      ensureSession(session);
      if (!response.ok) {
        if (response.status === 401) invalidateSession();
        if (response.status === 503) {
          const data = await response.json().catch(() => null);
          ensureSession(session);
          if (data?.detail?.code === 'cofre_session_unavailable') pauseUnavailableSession();
        }
        throw new Error('Arquivo indisponível nesta cópia.');
      }
      const blob = await response.blob();
      ensureSession(session);
      if (revision !== state.revision) return null;
      const url = URL.createObjectURL(blob);
      state.objectUrls.add(url);
      return { url, type: blob.type };
    })();
    state.mediaCache.set(path, promise);
    try { return await promise; }
    catch (error) {
      state.mediaCache.delete(path);
      throw error;
    }
  }

  async function downloadAttachment(path, filename, button, revision) {
    button.disabled = true;
    try {
      const asset = await authenticatedAsset(path, revision);
      if (!asset || revision !== state.revision) return;
      const anchor = node('a');
      anchor.href = asset.url;
      anchor.download = String(filename || 'arquivo').replace(/[\\/]/g, '_');
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
    } catch (error) {
      if (error.name !== 'AbortError' && revision === state.revision) notice('Não foi possível baixar este arquivo. Tente novamente.', true);
    } finally { button.disabled = false; }
  }

  function schedulePreview(preview, callback) {
    if (!('IntersectionObserver' in window)) { callback(); return; }
    if (!state.observer) {
      state.observer = new IntersectionObserver((entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          state.observer?.unobserve(entry.target);
          entry.target.loadPreview?.();
        }
      }, { root: $('messageScroll'), rootMargin: '250px' });
    }
    preview.loadPreview = callback;
    state.observer.observe(preview);
  }

  function renderAttachment(attachment) {
    const revision = state.revision;
    const filename = String(attachment.filename || 'Arquivo');
    const path = localAssetUrl(attachment.local_url, state.server?.id);
    const remote = externalUrl(attachment.url);
    const mime = String(attachment.content_type || '').toLowerCase().split(';')[0];
    const image = /^image\/(png|jpeg|gif|webp|avif|bmp)$/.test(mime) || (!mime && /\.(png|jpe?g|gif|webp|avif|bmp)$/i.test(filename));
    const video = /^video\/(mp4|webm|ogg|quicktime)$/.test(mime) || (!mime && /\.(mp4|webm|mov)$/i.test(filename));
    const audio = /^audio\//.test(mime) || (!mime && /\.(mp3|m4a|wav|ogg|flac)$/i.test(filename));
    const card = node('div', 'attachment');
    if (path && (image || video || audio)) {
      const preview = node('div', 'attachment-preview');
      preview.appendChild(node('span', 'attachment-preview-label', 'Carregando prévia…'));
      card.appendChild(preview);
      schedulePreview(preview, async () => {
        try {
          const asset = await authenticatedAsset(path, revision);
          if (!asset || revision !== state.revision) return;
          const media = node(image ? 'img' : video ? 'video' : 'audio');
          media.src = asset.url;
          if (image) {
            media.alt = filename;
            media.loading = 'lazy';
            const open = node('button', 'image-preview-button');
            open.type = 'button';
            open.addEventListener('click', () => {
              if (revision !== state.revision) return;
              $('mediaTitle').textContent = filename;
              $('mediaImage').src = asset.url;
              $('mediaImage').alt = filename;
              $('mediaDialog').showModal();
            });
            open.setAttribute('aria-label', 'Abrir imagem ' + filename);
            open.appendChild(media);
            preview.replaceChildren(open);
          } else {
            media.controls = true;
            media.preload = 'metadata';
            media.setAttribute('aria-label', filename);
            preview.replaceChildren(media);
          }
        } catch (error) {
          if (error.name !== 'AbortError' && revision === state.revision) preview.replaceChildren(node('span', 'attachment-preview-label', 'Prévia indisponível. Tente baixar o arquivo.'));
        }
      });
    }
    const footer = node('div', 'attachment-footer');
    const name = node('span', 'attachment-name', filename);
    name.title = filename;
    footer.append(node('span', 'attachment-symbol', image ? '▧' : video ? '▷' : audio ? '♪' : '▤'), name);
    if (path) {
      const button = node('button', 'attachment-download', '↓');
      button.title = 'Baixar ' + filename;
      button.setAttribute('aria-label', 'Baixar ' + filename);
      button.addEventListener('click', () => downloadAttachment(path, filename, button, revision));
      footer.appendChild(button);
    } else if (remote) {
      const anchor = link(remote, '↗', 'attachment-download');
      anchor.title = 'Abrir arquivo original (não copiado para o Cofre de Informações)';
      anchor.setAttribute('aria-label', 'Abrir arquivo original ' + filename);
      footer.appendChild(anchor);
    } else {
      const missing = node('span', 'attachment-source', 'Indisponível');
      missing.title = 'O arquivo não está incluído nesta cópia.';
      footer.appendChild(missing);
    }
    card.appendChild(footer);
    if (!path) {
      const note = node('div', 'attachment-preview-label', remote ? 'Arquivo original · ainda não copiado' : 'Anexo ausente nesta cópia');
      card.appendChild(note);
    }
    return card;
  }

  function renderMessage(message) {
    const article = node('article', 'message');
    const author = authorName(message);
    const avatar = node('div', 'message-avatar', initials(author));
    const hue = Array.from(author).reduce((total, char) => (total + char.codePointAt(0) * 7) % 360, 0);
    avatar.style.setProperty('--avatar-hue', String(hue));
    avatar.setAttribute('aria-hidden', 'true');
    const body = node('div', 'message-body');
    const meta = node('div', 'message-meta');
    const date = formatDate(message.timestamp);
    const time = node('time', 'message-time', date ? timeFormat.format(date) : 'Horário não informado');
    if (date) { time.dateTime = date.toISOString(); time.title = dateFormat.format(date) + ' às ' + timeFormat.format(date); }
    meta.append(node('span', 'message-author', author), time);
    const source = link(message.source_url, '↗', 'message-source');
    if (source) { source.title = 'Abrir mensagem no Discord'; source.setAttribute('aria-label', 'Abrir mensagem no Discord'); meta.appendChild(source); }
    body.appendChild(meta);
    if (message.content) {
      const content = node('p', 'message-text');
      appendText(content, message.content);
      body.appendChild(content);
    }
    const links = node('div', 'message-links');
    for (const item of (Array.isArray(message.links) ? message.links : [])) {
      if (String(message.content || '').includes(item.url)) continue;
      const anchor = link(item.url, item.text || item.url);
      if (anchor) links.appendChild(anchor);
    }
    if (links.childNodes.length) body.appendChild(links);
    for (const embed of (Array.isArray(message.embeds) ? message.embeds : [])) {
      const box = node('div', 'embed');
      appendText(box, typeof embed === 'string' ? embed : embed?.text || '');
      for (const item of (Array.isArray(embed?.links) ? embed.links : [])) {
        const anchor = link(item.url, item.text || item.url);
        if (anchor) { box.appendChild(node('br')); box.appendChild(anchor); }
      }
      if (box.textContent) body.appendChild(box);
    }
    const attachments = Array.isArray(message.attachments) ? message.attachments : [];
    if (attachments.length) {
      const grid = node('div', 'attachment-grid');
      for (const attachment of attachments) grid.appendChild(renderAttachment(attachment));
      body.appendChild(grid);
    }
    if (!message.content && !links.childNodes.length && !attachments.length && !body.querySelector('.embed')) body.appendChild(node('p', 'message-text', 'Mensagem sem texto na cópia.'));
    article.dataset.messageId = String(message.id);
    appendMessageActions(body, meta, message);
    article.append(avatar, body);
    return article;
  }

  async function loadMessages(reset, preserveScroll = false) {
    if (!state.channel || !state.server || state.sessionInvalidated || state.sessionUnavailable) return;
    const previousScroll = preserveScroll ? $('messageScroll').scrollTop : 0;
    const restoreCount = preserveScroll ? Math.max(state.loaded, 1) : 100;
    const anchor = preserveScroll ? captureScrollAnchor() : null;
    const keepDisplayed = reset && state.displayedChannel?.session === state.session &&
      state.displayedChannel.serverId === state.server.id && state.displayedChannel.channelId === state.channel.id;
    if (reset) {
      if (keepDisplayed) {
        state.revision += 1;
        state.request?.abort();
        clearTimeout(state.searching);
      } else {
        cancelMessages();
        if (!preserveScroll) $('messageScroll').scrollTop = 0;
      }
    }
    state.request?.abort();
    state.request = new AbortController();
    const revision = state.revision;
    const controller = state.request;
    const startOffset = reset ? 0 : state.offset;
    const params = new URLSearchParams({ servidor_id: state.server.id, offset: String(startOffset), limit: '100' });
    if (state.query) params.set('q', state.query);
    if (state.trashMode) params.set('excluidos', 'true');
    $('loadMore').disabled = true;
    $('timelineStatus').textContent = 'Carregando mensagens…';
    notice('');
    try {
      const channelId = state.channel.id;
      const messages = [];
      let data;
      do {
        params.set('offset', String(startOffset + messages.length));
        params.set('limit', String(preserveScroll ? Math.min(200, Math.max(1, restoreCount - messages.length)) : 100));
        data = await request('/canais/' + encodeURIComponent(channelId) + '/mensagens?' + params, { signal: controller.signal });
        if (revision !== state.revision) return;
        const batch = Array.isArray(data.mensagens) ? data.mensagens : [];
        messages.push(...batch);
        if (!preserveScroll || !data.has_more || !batch.length) break;
      } while (messages.length < restoreCount);
      if (keepDisplayed) {
        clearMedia();
        state.loaded = 0;
        state.offset = 0;
        state.total = 0;
        state.lastDay = null;
        $('messages').replaceChildren();
        if (!preserveScroll) $('messageScroll').scrollTop = 0;
      }
      const fragment = document.createDocumentFragment();
      for (const message of messages) {
        const divider = dateDivider(message.timestamp);
        if (divider) fragment.appendChild(divider);
        fragment.appendChild(renderMessage(message));
      }
      $('messages').appendChild(fragment);
      state.loaded += messages.length;
      state.offset += messages.length;
      state.displayedChannel = { session: state.session, serverId: state.server.id, channelId: state.channel.id };
      state.messageRetry = null;
      state.total = Number(data.total) || 0;
      state.totalActive = Number.isFinite(data.total_ativos) ? data.total_ativos : Number(state.channel.message_count) || 0;
      state.totalDeleted = Number.isFinite(data.total_excluidos) ? data.total_excluidos : Number(state.channel.deleted_message_count) || 0;
      state.channel.message_count = state.totalActive;
      state.channel.deleted_message_count = state.totalDeleted;
      updateTrashCount();
      renderChannels();
      renderDetails();
      if (preserveScroll) restoreScrollAnchor(anchor, previousScroll);
      const more = data.has_more === true && messages.length > 0;
      $('loadMore').hidden = !more;
      $('loadMore').disabled = false;
      $('loadMore').textContent = 'Carregar mais mensagens';
      $('timelineStatus').textContent = state.total === 0
        ? state.query ? 'Nenhuma mensagem encontrada para esta busca.' : state.trashMode ? 'A lixeira deste canal está vazia.' : 'Nenhuma mensagem nesta cópia do canal.'
        : numberFormat.format(state.loaded) + ' de ' + numberFormat.format(state.total) + (state.query ? ' mensagens encontradas' : state.trashMode ? ' mensagens na lixeira' : ' mensagens no acervo') + (more ? '' : ' · você chegou ao fim da cópia');
    } catch (error) {
      if (error.name === 'AbortError' || revision !== state.revision) return;
      $('timelineStatus').textContent = 'Não foi possível carregar as mensagens.';
      state.messageRetry = { reset, preserveScroll };
      $('loadMore').hidden = false;
      $('loadMore').disabled = false;
      $('loadMore').textContent = 'Tentar novamente';
      notice(error.message, true);
    }
  }

  function captureScrollAnchor() {
    const top = $('messageScroll').getBoundingClientRect().top;
    const bottom = $('messageScroll').getBoundingClientRect().bottom;
    return Array.from($('messages').querySelectorAll('.message')).filter(article => {
      const rect = article.getBoundingClientRect();
      return rect.bottom > top && rect.top < bottom;
    }).map(article => ({ id: article.dataset.messageId, offset: article.getBoundingClientRect().top - top })).slice(0, 5);
  }

  function restoreScrollAnchor(anchors, previousScroll) {
    const scroll = $('messageScroll');
    scroll.scrollTop = previousScroll;
    const articles = Array.from($('messages').querySelectorAll('.message'));
    for (const anchor of anchors || []) {
      const article = articles.find(item => item.dataset.messageId === anchor.id);
      if (!article) continue;
      scroll.scrollTop += article.getBoundingClientRect().top - scroll.getBoundingClientRect().top - anchor.offset;
      break;
    }
  }

  function updateTrashCount() {
    $('trashCount').hidden = !state.totalDeleted;
    $('trashCount').textContent = numberFormat.format(state.totalDeleted);
  }

  function updateChannelHeading() {
    const channel = state.channel;
    if (!channel) return;
    $('channelName').textContent = channel.name + (state.trashMode ? ' · Lixeira' : '');
    $('channelTopic').textContent = state.trashMode ? 'Itens excluídos deste canal. Restaure para voltar ao acervo.' : channel.topic || 'Mensagens e arquivos preservados neste canal.';
    $('introTitle').textContent = (state.trashMode ? 'Lixeira de #' : '#') + channel.name;
    $('introDescription').textContent = state.trashMode ? 'Os itens excluídos ficam aqui até você restaurá-los.' : channel.topic || 'Este é o histórico do canal guardado no seu Cofre de Informações.';
    $('channelIntro').querySelector('.intro-symbol').textContent = state.trashMode ? '♲' : '#';
    $('emptyState').hidden = true;
    $('channelIntro').hidden = false;
    $('archiveCaption').textContent = state.trashMode ? 'Lixeira recuperável · restaure itens para consultar no acervo' : 'Acervo para consulta · ' + (channel.history_complete ? 'histórico completo na cópia' : 'histórico parcial');
    $('toggleTrash').setAttribute('aria-pressed', String(state.trashMode));
    $('toggleTrash').classList.toggle('active', state.trashMode);
    $('toggleTrash').title = state.trashMode ? 'Voltar ao acervo ativo' : 'Ver lixeira deste canal';
    $('toggleTrash').setAttribute('aria-label', $('toggleTrash').title);
    $('messageSearch').placeholder = state.trashMode ? 'Buscar na lixeira' : 'Buscar neste canal';
    document.querySelector('label[for="messageSearch"]').textContent = $('messageSearch').placeholder;
    state.totalDeleted = Number(channel.deleted_message_count) || 0;
    updateTrashCount();
  }

  function messageContext(message) {
    const context = { session: ensureSession(), serverId: String(state.server.id), channelId: String(state.channel.id), messageId: String(message.id) };
    context.key = JSON.stringify([context.session.key, context.serverId, context.channelId, context.messageId]);
    return context;
  }

  function currentChannel(context) {
    return !state.sessionInvalidated && context.session === state.session && state.server?.id === context.serverId && state.channel?.id === context.channelId;
  }

  function finishMessageMutation(context) {
    state.mutations.delete(context.key);
    if (!currentChannel(context)) return;
    for (const article of $('messages').querySelectorAll('.message')) {
      if (article.dataset.messageId !== context.messageId) continue;
      article.querySelectorAll('.message-action').forEach(button => { button.disabled = false; });
    }
  }

  function messagePath(context) {
    return '/canais/' + encodeURIComponent(context.channelId) + '/mensagens/' + encodeURIComponent(context.messageId);
  }

  function scopedPath(path, context) {
    return path + '?servidor_id=' + encodeURIComponent(context.serverId);
  }

  function cloneLinks(links) {
    return (Array.isArray(links) ? links : []).filter(item => item && typeof item === 'object').map(item => ({ url: String(item.url || ''), text: String(item.text || '') }));
  }

  function feedback(id, message, isError = false) {
    $(id).textContent = message;
    $(id).classList.toggle('error', isError);
  }

  function appendMessageActions(body, meta, message) {
    const context = messageContext(message);
    if (message.edited) meta.appendChild(node('span', 'message-state', 'Editado'));
    const deleted = message.deleted === true || state.trashMode;
    if (deleted) meta.appendChild(node('span', 'message-state deleted-state', 'Na lixeira'));
    if (!canEditArchive()) return;
    const actions = node('div', 'message-actions');
    if (deleted) {
      const restore = node('button', 'message-action restore-action', '↶ Restaurar');
      restore.type = 'button';
      restore.dataset.action = 'restore-message';
      restore.setAttribute('aria-label', 'Restaurar item de ' + authorName(message));
      restore.disabled = state.mutations.has(context.key);
      restore.addEventListener('click', () => restoreMessage(context, restore));
      actions.appendChild(restore);
    } else {
      const edit = node('button', 'message-action', '✎ Editar');
      edit.type = 'button';
      edit.dataset.action = 'edit-message';
      edit.setAttribute('aria-label', 'Editar item de ' + authorName(message));
      edit.disabled = state.mutations.has(context.key);
      edit.addEventListener('click', () => openMessageEditor(message, context));
      const remove = node('button', 'message-action delete-action', '♲ Excluir');
      remove.type = 'button';
      remove.dataset.action = 'delete-message';
      remove.setAttribute('aria-label', 'Excluir item de ' + authorName(message));
      remove.disabled = state.mutations.has(context.key);
      remove.addEventListener('click', () => openDeleteMessage(message, context));
      actions.append(edit, remove);
    }
    body.appendChild(actions);
  }

  function changedMessageDraft() {
    if (!state.editor) return;
    const draft = state.editor.draft;
    draft.revision += 1;
    draft.dirty = true;
    state.messageDrafts.set(state.editor.context.key, draft);
  }

  function renderLinkEditor(container, values, changed, prefix) {
    container.replaceChildren();
    values.forEach((value, index) => {
      const row = node('div', 'editor-link-row');
      const text = node('input', 'edit-input');
      text.value = value.text;
      text.placeholder = 'Nome do link';
      text.setAttribute('aria-label', prefix + ' ' + (index + 1) + ': nome');
      text.addEventListener('input', () => { value.text = text.value; changed(); });
      const url = node('input', 'edit-input');
      url.type = 'url';
      url.value = value.url;
      url.placeholder = 'https://…';
      url.setAttribute('aria-label', prefix + ' ' + (index + 1) + ': endereço');
      url.addEventListener('input', () => { value.url = url.value; changed(); });
      const remove = node('button', 'icon-button', '×');
      remove.type = 'button';
      remove.setAttribute('aria-label', 'Remover ' + prefix.toLowerCase() + ' ' + (index + 1));
      remove.addEventListener('click', () => {
        values.splice(index, 1);
        changed();
        renderLinkEditor(container, values, changed, prefix);
      });
      row.append(text, url, remove);
      container.appendChild(row);
    });
    if (!values.length) container.appendChild(node('p', 'editor-empty', 'Nenhum link adicional.'));
  }

  function renderEmbedEditor() {
    const container = $('editMessageEmbeds');
    const values = state.editor.draft.data.embeds;
    container.replaceChildren();
    values.forEach((embed, index) => {
      const card = node('div', 'editor-embed-card');
      const heading = node('div', 'editor-section-heading');
      const remove = node('button', 'text-button delete-action', 'Remover cartão');
      remove.type = 'button';
      remove.addEventListener('click', () => { values.splice(index, 1); changedMessageDraft(); renderEmbedEditor(); });
      heading.append(node('h4', null, 'Cartão ' + (index + 1)), remove);
      const text = node('textarea', 'edit-input');
      text.rows = 3;
      text.value = embed.text;
      text.setAttribute('aria-label', 'Texto do cartão ' + (index + 1));
      text.addEventListener('input', () => { embed.text = text.value; changedMessageDraft(); });
      const links = node('div', 'editor-links');
      renderLinkEditor(links, embed.links, changedMessageDraft, 'Link do cartão ' + (index + 1));
      const add = node('button', 'text-button', '+ Adicionar link ao cartão');
      add.type = 'button';
      add.addEventListener('click', () => {
        embed.links.push({ text: '', url: '' });
        changedMessageDraft();
        renderLinkEditor(links, embed.links, changedMessageDraft, 'Link do cartão ' + (index + 1));
        links.querySelector('.editor-link-row:last-child input')?.focus();
      });
      card.append(heading, text, links, add);
      container.appendChild(card);
    });
    if (!values.length) container.appendChild(node('p', 'editor-empty', 'Nenhum cartão de referência.'));
  }

  function openMessageEditor(message, context) {
    if (!requireWriteAccess()) return;
    if (state.mutations.has(context.key) && !state.messageDrafts.has(context.key)) {
      notice('Este item tem uma alteração em andamento. Aguarde a conclusão.');
      return;
    }
    try { ensureSession(context.session); } catch (_error) { return; }
    let draft = state.messageDrafts.get(context.key);
    if (!draft) {
      draft = { revision: 0, dirty: false, data: {
        author: authorName(message), content: String(message.content || ''), links: cloneLinks(message.links),
        embeds: (Array.isArray(message.embeds) ? message.embeds : []).map(embed => ({ text: typeof embed === 'string' ? embed : String(embed?.text || ''), links: cloneLinks(embed?.links) }))
      } };
      state.messageDrafts.set(context.key, draft);
    }
    state.editor = { context, draft };
    $('editAuthor').value = draft.data.author;
    $('editContent').value = draft.data.content;
    renderLinkEditor($('editMessageLinks'), draft.data.links, changedMessageDraft, 'Link');
    renderEmbedEditor();
    const source = externalUrl(message.source_url);
    $('editMessageSource').hidden = !source;
    if (source) $('editMessageSource').href = source;
    else $('editMessageSource').removeAttribute('href');
    const pending = state.mutations.has(context.key);
    $('saveEditMessage').disabled = pending;
    $('saveEditMessage').textContent = pending ? 'Salvando…' : 'Salvar alterações';
    feedback('editMessageFeedback', pending ? 'Uma versão deste item está sendo salva.' : draft.dirty ? 'Rascunho recuperado.' : '');
    $('editMessageDialog').showModal();
    $('editContent').focus();
  }

  function validatedLinks(values) {
    return values.map(item => {
      const url = String(item.url || '').trim();
      if (!externalUrl(url)) throw new Error('Cada link precisa de um endereço válido começando com http:// ou https://.');
      return { url, text: String(item.text || '') };
    });
  }

  async function refreshAfterMutation(context, data) {
    const current = currentChannel(context);
    if (current) {
      if (Number.isFinite(data.total_ativos)) state.channel.message_count = data.total_ativos;
      if (Number.isFinite(data.total_excluidos)) state.channel.deleted_message_count = data.total_excluidos;
      state.totalDeleted = Number(state.channel.deleted_message_count) || 0;
      updateTrashCount();
      renderChannels();
      renderDetails();
      state.query = $('messageSearch').value.trim();
      await loadMessages(true, true);
    }
    const revision = ++state.summaryRevision;
    try {
      const summary = await request('/servidores');
      if (revision !== state.summaryRevision) return;
      state.servers = Array.isArray(summary.servidores) ? summary.servidores : state.servers;
      state.server = state.servers.find(server => server.id === state.server?.id) || state.server;
      renderServers();
      renderDetails();
      if (state.sharing.configured) applySharingStatus(await request('/compartilhamento', {}, context.session));
    } catch (_error) { /* The mutation is already saved; the next refresh updates summary. */ }
  }

  async function saveMessage(event) {
    event.preventDefault();
    const session = state.editor;
    if (!requireWriteAccess()) return;
    if (!session || state.mutations.has(session.context.key)) return;
    const { context, draft } = session;
    const revision = draft.revision;
    let payload;
    try {
      payload = { author: draft.data.author, content: draft.data.content, links: validatedLinks(draft.data.links), embeds: draft.data.embeds.map(embed => ({ text: embed.text, links: validatedLinks(embed.links) })) };
    } catch (error) { feedback('editMessageFeedback', error.message, true); return; }
    state.mutations.set(context.key, 'edit');
    $('saveEditMessage').disabled = true;
    $('saveEditMessage').textContent = 'Salvando…';
    feedback('editMessageFeedback', 'Salvando alterações… Você pode continuar editando o rascunho.');
    try {
      const data = await request(scopedPath(messagePath(context), context), { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }, context.session);
      const unchanged = draft.revision === revision;
      if (unchanged) {
        draft.dirty = false;
        if (state.editor === session) {
          $('editMessageDialog').close();
          state.messageDrafts.delete(context.key);
        } else if (state.editor?.draft !== draft) state.messageDrafts.delete(context.key);
      }
      if (state.editor?.draft === draft && $('editMessageDialog').open) feedback('editMessageFeedback', unchanged ? 'Alterações salvas.' : 'A versão enviada foi salva. Seu rascunho contém alterações mais recentes.');
      await refreshAfterMutation(context, data);
      if (currentChannel(context)) notice(unchanged ? 'Item atualizado no Cofre de Informações.' : 'Item salvo. Há alterações mais recentes no rascunho aberto.');
    } catch (error) {
      if (state.editor?.draft === draft && $('editMessageDialog').open) feedback('editMessageFeedback', error.message + ' O rascunho foi mantido.', true);
      else if (currentChannel(context)) notice('Não foi possível salvar o item. O rascunho foi mantido.', true);
    } finally {
      finishMessageMutation(context);
      if (state.editor?.context.key === context.key) {
        $('saveEditMessage').disabled = false;
        $('saveEditMessage').textContent = 'Salvar alterações';
      }
    }
  }

  function openDeleteMessage(message, context) {
    if (!requireWriteAccess()) return;
    if (state.mutations.has(context.key)) { notice('Este item tem uma alteração em andamento. Aguarde a conclusão.'); return; }
    try { ensureSession(context.session); } catch (_error) { return; }
    state.deleteTarget = context;
    $('deleteMessagePreview').textContent = authorName(message) + ': ' + (String(message.content || '').slice(0, 260) || 'Item com links, cartões ou anexos.');
    feedback('deleteMessageFeedback', '');
    $('deleteMessageDialog').showModal();
    $('cancelDeleteMessage').focus();
  }

  async function deleteMessage() {
    const context = state.deleteTarget;
    if (!requireWriteAccess()) return;
    if (!context || state.deleting || state.mutations.has(context.key)) return;
    state.deleting = true;
    state.mutations.set(context.key, 'delete');
    for (const id of ['confirmDeleteMessage', 'cancelDeleteMessage', 'closeDeleteMessage']) $(id).disabled = true;
    feedback('deleteMessageFeedback', 'Movendo item para a lixeira…');
    try {
      const data = await request(scopedPath(messagePath(context), context), { method: 'DELETE' }, context.session);
      $('deleteMessageDialog').close();
      await refreshAfterMutation(context, data);
      if (currentChannel(context)) notice('Item movido para a lixeira. Você pode restaurá-lo neste canal.');
    } catch (error) { if (!state.sessionInvalidated && context.session === state.session) feedback('deleteMessageFeedback', error.message, true); }
    finally {
      state.deleting = false;
      finishMessageMutation(context);
      for (const id of ['confirmDeleteMessage', 'cancelDeleteMessage', 'closeDeleteMessage']) $(id).disabled = false;
    }
  }

  async function restoreMessage(context, button) {
    if (!requireWriteAccess()) return;
    if (state.mutations.has(context.key)) return;
    state.mutations.set(context.key, 'restore');
    button.disabled = true;
    button.textContent = 'Restaurando…';
    try {
      const data = await request(scopedPath(messagePath(context) + '/restaurar', context), { method: 'POST' }, context.session);
      await refreshAfterMutation(context, data);
      if (currentChannel(context)) notice('Item restaurado no acervo deste canal.');
    } catch (error) { if (currentChannel(context)) notice(error.message, true); }
    finally { finishMessageMutation(context); button.disabled = false; button.textContent = '↶ Restaurar'; }
  }

  function channelDraftChanged() {
    if (!state.channelEditor) return;
    const draft = state.channelEditor.draft;
    draft.data = { name: $('editChannelName').value, category: $('editChannelCategory').value, topic: $('editChannelTopic').value };
    draft.revision += 1;
    draft.dirty = true;
    state.channelDrafts.set(state.channelEditor.context.key, draft);
  }

  function openChannelEditor() {
    if (!requireWriteAccess()) return;
    if (!state.channel || !state.server) return;
    let session;
    try { session = ensureSession(); } catch (_error) { return; }
    const context = { session, serverId: String(state.server.id), channelId: String(state.channel.id) };
    context.key = JSON.stringify(['channel', session.key, context.serverId, context.channelId]);
    let draft = state.channelDrafts.get(context.key);
    if (!draft) {
      draft = { revision: 0, dirty: false, data: { name: state.channel.name || '', category: state.channel.category || '', topic: state.channel.topic || '' } };
      state.channelDrafts.set(context.key, draft);
    }
    state.channelEditor = { context, draft };
    $('editChannelName').value = draft.data.name;
    $('editChannelCategory').value = draft.data.category;
    $('editChannelTopic').value = draft.data.topic;
    const pending = state.mutations.has(context.key);
    $('saveEditChannel').disabled = pending;
    $('saveEditChannel').textContent = pending ? 'Salvando…' : 'Salvar canal';
    feedback('editChannelFeedback', pending ? 'Uma versão deste canal está sendo salva.' : draft.dirty ? 'Rascunho recuperado.' : '');
    $('editChannelDialog').showModal();
    $('editChannelName').focus();
  }

  async function saveChannel(event) {
    event.preventDefault();
    const session = state.channelEditor;
    if (!requireWriteAccess()) return;
    if (!session || state.mutations.has(session.context.key)) return;
    const { context, draft } = session;
    const revision = draft.revision;
    const payload = { ...draft.data };
    state.mutations.set(context.key, 'channel');
    $('saveEditChannel').disabled = true;
    $('saveEditChannel').textContent = 'Salvando…';
    feedback('editChannelFeedback', 'Salvando canal… Você pode continuar editando o rascunho.');
    try {
      const data = await request(scopedPath('/canais/' + encodeURIComponent(context.channelId), context), { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }, context.session);
      const unchanged = draft.revision === revision;
      if (unchanged) {
        draft.dirty = false;
        if (state.channelEditor === session) { $('editChannelDialog').close(); state.channelDrafts.delete(context.key); }
        else if (state.channelEditor?.draft !== draft) state.channelDrafts.delete(context.key);
      }
      if (state.channelEditor?.draft === draft && $('editChannelDialog').open) feedback('editChannelFeedback', unchanged ? 'Canal atualizado.' : 'A versão enviada foi salva. Seu rascunho contém alterações mais recentes.');
      if (state.server?.id === context.serverId && data.canal) {
        state.channels = state.channels.map(channel => channel.id === context.channelId ? data.canal : channel);
        if (currentChannel(context)) { state.channel = data.canal; updateChannelHeading(); }
        renderChannels();
        renderDetails();
        notice('Canal atualizado no Cofre de Informações.');
      }
    } catch (error) {
      if (state.channelEditor?.draft === draft && $('editChannelDialog').open) feedback('editChannelFeedback', error.message + ' O rascunho foi mantido.', true);
      else if (currentChannel(context)) notice('Não foi possível salvar o canal. O rascunho foi mantido.', true);
    } finally {
      state.mutations.delete(context.key);
      if (state.channelEditor?.context.key === context.key) { $('saveEditChannel').disabled = false; $('saveEditChannel').textContent = 'Salvar canal'; }
    }
  }

  function bindEditors() {
    $('editMessageForm').addEventListener('submit', saveMessage);
    for (const id of ['closeEditMessage', 'cancelEditMessage']) $(id).addEventListener('click', () => $('editMessageDialog').close());
    $('editAuthor').addEventListener('input', () => { if (state.editor) state.editor.draft.data.author = $('editAuthor').value; changedMessageDraft(); });
    $('editContent').addEventListener('input', () => { if (state.editor) state.editor.draft.data.content = $('editContent').value; changedMessageDraft(); });
    $('addMessageLink').addEventListener('click', () => {
      if (!state.editor) return;
      state.editor.draft.data.links.push({ url: '', text: '' });
      changedMessageDraft();
      renderLinkEditor($('editMessageLinks'), state.editor.draft.data.links, changedMessageDraft, 'Link');
      $('editMessageLinks').querySelector('.editor-link-row:last-child input')?.focus();
    });
    $('addMessageEmbed').addEventListener('click', () => {
      if (!state.editor) return;
      state.editor.draft.data.embeds.push({ text: '', links: [] });
      changedMessageDraft();
      renderEmbedEditor();
      $('editMessageEmbeds').querySelector('.editor-embed-card:last-child textarea')?.focus();
    });
    $('editChannel').addEventListener('click', openChannelEditor);
    $('editChannelForm').addEventListener('submit', saveChannel);
    for (const id of ['editChannelName', 'editChannelCategory', 'editChannelTopic']) $(id).addEventListener('input', channelDraftChanged);
    for (const id of ['closeEditChannel', 'cancelEditChannel']) $(id).addEventListener('click', () => $('editChannelDialog').close());
    $('confirmDeleteMessage').addEventListener('click', deleteMessage);
    for (const id of ['closeDeleteMessage', 'cancelDeleteMessage']) $(id).addEventListener('click', () => { if (!state.deleting) $('deleteMessageDialog').close(); });
    $('deleteMessageDialog').addEventListener('cancel', event => { if (state.deleting) event.preventDefault(); });
  }

  function openImport() {
    if (!requireWriteAccess()) return;
    if (state.sessionInvalidated) return;
    try { ensureSession(); } catch (_error) { return; }
    if (!$('importDialog').open) $('importDialog').showModal();
  }

  function closeImport() {
    if (!state.importing) $('importDialog').close();
  }

  async function importArchive(event) {
    event.preventDefault();
    if (!requireWriteAccess()) return;
    if (state.importing) return;
    const file = $('importFile').files[0];
    if (!file) { $('importFile').focus(); return; }
    if (!/\.(zip|json)$/i.test(file.name)) {
      $('importFeedback').textContent = 'Selecione um arquivo .zip ou .json do Cofre de Informações.';
      $('importFeedback').classList.add('error');
      return;
    }
    const form = new FormData();
    form.append('arquivo', file, file.name);
    state.importing = true;
    $('importFeedback').classList.remove('error');
    $('importFeedback').textContent = 'Importando canais, mensagens e arquivos… Aguarde a conclusão.';
    $('submitImport').textContent = 'Importando…';
    for (const id of ['submitImport', 'cancelImport', 'closeImport', 'importFile']) $(id).disabled = true;
    try {
      const data = await request('/importar', { method: 'POST', body: form });
      $('importDialog').close();
      $('importForm').reset();
      $('fileLabel').textContent = 'Escolher arquivo';
      await loadServers(data.servidor?.id);
      notice(numberFormat.format(data.imported_messages || 0) + ' mensagens importadas. ' + (data.servidor?.history_complete ? 'Confira o histórico e os anexos nos canais.' : 'A cópia tem histórico parcial; confira a cobertura nas informações do acervo.'));
    } catch (error) {
      $('importFeedback').textContent = error.message;
      $('importFeedback').classList.add('error');
    } finally {
      state.importing = false;
      $('submitImport').textContent = 'Importar arquivo';
      for (const id of ['submitImport', 'cancelImport', 'closeImport', 'importFile']) $(id).disabled = false;
    }
  }

  function applySearch() {
    clearTimeout(state.searching);
    const query = $('messageSearch').value.trim();
    state.query = query;
    loadMessages(true);
  }

  async function init() {
    if (window.__JK_AUTH_READY__) await window.__JK_AUTH_READY__;
    if (typeof verificarSessao === 'function' && !verificarSessao()) return;
    let permissions = {};
    try { permissions = JSON.parse(localStorage.getItem('permissions') || '{}'); } catch (_error) {}
    if (!(permissions.full === true || permissions.cofre === true)) {
      window.location.replace('/dashboard.html');
      return;
    }
    state.session = captureSession();
    const checkIdentity = () => { try { ensureSession(); return true; } catch (_error) { return false; } };
    window.addEventListener('storage', event => { if (!event.key || ['access_token', 'user_data', 'permissions'].includes(event.key)) checkIdentity(); });
    const focusSync = () => { if (checkIdentity() && state.archiveReady && !state.sessionUnavailable) synchronizeSharing(); };
    window.addEventListener('focus', focusSync);
    document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') focusSync(); });
    if (window.matchMedia('(max-width: 1180px)').matches) setDetailsOpen(false);
    for (const id of ['railImport', 'sidebarImport', 'emptyImport', 'detailImport', 'footerImport']) $(id).addEventListener('click', openImport);
    $('reauthenticateCofre').addEventListener('click', retryReadOnly);
    $('closeMedia').addEventListener('click', () => $('mediaDialog').close());
    $('closeImport').addEventListener('click', closeImport);
    $('cancelImport').addEventListener('click', closeImport);
    $('importDialog').addEventListener('cancel', (event) => { if (state.importing) event.preventDefault(); });
    $('importForm').addEventListener('submit', importArchive);
    $('importFile').addEventListener('change', () => {
      $('fileLabel').textContent = $('importFile').files[0]?.name || 'Escolher arquivo';
      $('importFeedback').textContent = '';
      $('importFeedback').classList.remove('error');
    });
    $('toggleDetails').addEventListener('click', () => setDetailsOpen($('cofreApp').classList.contains('details-hidden')));
    $('closeDetails').addEventListener('click', () => { setDetailsOpen(false); $('toggleDetails').focus(); });
    $('openChannels').addEventListener('click', () => setChannelsOpen(true));
    $('closeChannels').addEventListener('click', () => { setChannelsOpen(false); $('openChannels').focus(); });
    $('sidebarScrim').addEventListener('click', () => setChannelsOpen(false));
    $('refreshMessages').addEventListener('click', refreshArchive);
    $('syncSharing').addEventListener('click', () => state.sessionUnavailable ? retryReadOnly() : synchronizeSharing({ manual: true }));
    $('publishSharing').addEventListener('click', openPublishSharing);
    $('confirmPublishSharing').addEventListener('click', publishSharing);
    $('syncBeforePublish').addEventListener('click', () => { if (!state.publishing) { $('publishSharingDialog').close(); synchronizeSharing({ manual: true }); } });
    for (const id of ['closePublishSharing', 'cancelPublishSharing']) $(id).addEventListener('click', () => { if (!state.publishing) $('publishSharingDialog').close(); });
    $('publishSharingDialog').addEventListener('cancel', event => { if (state.publishing) event.preventDefault(); });
    bindEditors();
    $('toggleTrash').addEventListener('click', () => {
      state.trashMode = !state.trashMode;
      updateChannelHeading();
      loadMessages(true);
    });
    $('loadMore').addEventListener('click', () => loadMessages(state.messageRetry?.reset || false, state.messageRetry?.preserveScroll || false));
    $('searchForm').addEventListener('submit', (event) => { event.preventDefault(); applySearch(); });
    $('messageSearch').addEventListener('input', () => {
      clearTimeout(state.searching);
      // Invalidate the current response as soon as a newer query is typed.
      state.revision += 1;
      state.request?.abort();
      $('loadMore').disabled = true;
      state.searching = setTimeout(applySearch, 250);
    });
    document.addEventListener('keydown', (event) => {
      if (event.key !== 'Escape' || $('importDialog').open) return;
      setChannelsOpen(false);
      if (window.matchMedia('(max-width: 1180px)').matches) setDetailsOpen(false);
    });
    window.addEventListener('pagehide', () => { state.request?.abort(); clearMedia(); });
    try { applySharingStatus(await request('/compartilhamento')); }
    catch (_error) { if (!state.sessionInvalidated) { state.sharingError = true; renderSharing(); } }
    if (state.sessionInvalidated || state.sessionUnavailable) return;
    state.accessReady = true;
    renderSharing();
    try { await loadServers(); state.archiveReady = true; }
    catch (error) { if (!state.sessionInvalidated && !state.sessionUnavailable) { notice(error.message, true); $('emptyTitle').textContent = 'Informações indisponíveis no momento'; $('emptyDescription').textContent = 'Não foi possível consultar seu acervo agora. Atualize a página para tentar novamente.'; } }
    if (!state.sessionInvalidated && !state.sessionUnavailable) await synchronizeSharing({ initial: true });
  }

  init().catch(() => { notice('Não foi possível iniciar o Cofre de Informações. Atualize a página para tentar novamente.', true); });
})();
