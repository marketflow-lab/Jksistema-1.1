(function () {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const state = {
    servers: [], server: null, channels: [], channel: null,
    offset: 0, total: 0, loaded: 0, lastDay: null, revision: 0, serverRevision: 0,
    request: null, mediaController: new AbortController(),
    mediaCache: new Map(), objectUrls: new Set(), observer: null,
    importing: false, searching: null, query: '', trashMode: false,
    totalActive: 0, totalDeleted: 0, summaryRevision: 0,
    messageDrafts: new Map(), channelDrafts: new Map(), mutations: new Map(),
    editor: null, channelEditor: null, deleteTarget: null, deleting: false,
    session: null, sessionInvalidated: false
  };
  const dateFormat = new Intl.DateTimeFormat('pt-BR', { day: 'numeric', month: 'long', year: 'numeric' });
  const timeFormat = new Intl.DateTimeFormat('pt-BR', { hour: '2-digit', minute: '2-digit' });
  const numberFormat = new Intl.NumberFormat('pt-BR');

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
    if (typeof obterAuthHeaders !== 'function') throw new Error('Sua sessão está indisponível. Entre novamente no JK Sistema.');
    return obterAuthHeaders();
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
    const key = JSON.stringify([headerValues, localStorage.getItem('access_token'), user.client_id || '', user.tenant_id || '', user.user_id || user.id || '', user.username || '']);
    return { key, headers: { ...headers } };
  }

  function invalidateSession() {
    if (state.sessionInvalidated) return;
    state.sessionInvalidated = true;
    state.serverRevision += 1;
    state.summaryRevision += 1;
    cancelMessages();
    state.session = null;
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
    for (const id of ['editMessageDialog', 'editChannelDialog', 'deleteMessageDialog', 'importDialog']) $(id).close();
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
    $('channelName').textContent = 'Sessão alterada';
    $('channelTopic').textContent = 'Reabra este módulo para continuar.';
    setChannelsOpen(false);
    renderServers();
    renderChannels();
    renderDetails();
    notice('Sua sessão mudou. O contexto anterior foi encerrado. Reabra o Cofre de Informações para continuar.');
  }

  function ensureSession(session = state.session) {
    if (state.sessionInvalidated || !session || captureSession().key !== session.key || state.session?.key !== session.key) {
      invalidateSession();
      throw new Error('Sua sessão mudou. Reabra o Cofre de Informações para continuar.');
    }
    return session;
  }

  async function request(path, options = {}, session = state.session) {
    ensureSession(session);
    const response = await fetch('/api/cofre' + path, {
      cache: 'no-store', credentials: 'same-origin', ...options,
      headers: { ...session.headers, ...(options.headers || {}) }
    });
    ensureSession(session);
    const data = await response.json().catch(() => ({}));
    ensureSession(session);
    if (!response.ok) {
      const fallback = response.status === 403 ? 'Sua conta não tem acesso ao Cofre de Informações.' : 'Não foi possível carregar o Cofre de Informações. Tente novamente.';
      throw new Error(errorMessage(data, fallback));
    }
    return data;
  }

  function notice(message, isError) {
    $('notice').hidden = !message;
    $('notice').textContent = message || '';
    $('notice').classList.toggle('error', !!isError);
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
      return;
    }
    const selected = state.servers.find((server) => server.id === preferredId) || state.servers[0];
    await selectServer(selected);
  }

  async function selectServer(server) {
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
      const response = await fetch(path, { headers: session.headers, cache: 'no-store', credentials: 'same-origin', signal: state.mediaController.signal });
      if (!response.ok) throw new Error('Arquivo indisponível nesta cópia.');
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
    if (!state.channel || !state.server) return;
    const previousScroll = preserveScroll ? $('messageScroll').scrollTop : 0;
    const restoreCount = preserveScroll ? Math.max(state.loaded, 1) : 100;
    const anchor = preserveScroll ? captureScrollAnchor() : null;
    if (reset) {
      cancelMessages();
      if (!preserveScroll) $('messageScroll').scrollTop = 0;
    }
    state.request?.abort();
    state.request = new AbortController();
    const revision = state.revision;
    const controller = state.request;
    const params = new URLSearchParams({ servidor_id: state.server.id, offset: String(state.offset), limit: '100' });
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
        params.set('offset', String(state.offset + messages.length));
        params.set('limit', String(preserveScroll ? Math.min(200, Math.max(1, restoreCount - messages.length)) : 100));
        data = await request('/canais/' + encodeURIComponent(channelId) + '/mensagens?' + params, { signal: controller.signal });
        if (revision !== state.revision) return;
        const batch = Array.isArray(data.mensagens) ? data.mensagens : [];
        messages.push(...batch);
        if (!preserveScroll || !data.has_more || !batch.length) break;
      } while (messages.length < restoreCount);
      const fragment = document.createDocumentFragment();
      for (const message of messages) {
        const divider = dateDivider(message.timestamp);
        if (divider) fragment.appendChild(divider);
        fragment.appendChild(renderMessage(message));
      }
      $('messages').appendChild(fragment);
      state.loaded += messages.length;
      state.offset += messages.length;
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
    } catch (_error) { /* The mutation is already saved; the next refresh updates summary. */ }
  }

  async function saveMessage(event) {
    event.preventDefault();
    const session = state.editor;
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
    if (state.sessionInvalidated) return;
    try { ensureSession(); } catch (_error) { return; }
    if (!$('importDialog').open) $('importDialog').showModal();
  }

  function closeImport() {
    if (!state.importing) $('importDialog').close();
  }

  async function importArchive(event) {
    event.preventDefault();
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
    const checkIdentity = () => { try { ensureSession(); } catch (_error) {} };
    window.addEventListener('storage', event => { if (!event.key || ['access_token', 'user_data', 'permissions'].includes(event.key)) checkIdentity(); });
    window.addEventListener('focus', checkIdentity);
    document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') checkIdentity(); });
    if (window.matchMedia('(max-width: 1180px)').matches) setDetailsOpen(false);
    for (const id of ['railImport', 'sidebarImport', 'emptyImport', 'detailImport', 'footerImport']) $(id).addEventListener('click', openImport);
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
    $('refreshMessages').addEventListener('click', () => loadMessages(true));
    bindEditors();
    $('toggleTrash').addEventListener('click', () => {
      state.trashMode = !state.trashMode;
      updateChannelHeading();
      loadMessages(true);
    });
    $('loadMore').addEventListener('click', () => loadMessages(false));
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
    try { await loadServers(); }
    catch (error) { notice(error.message, true); $('emptyDescription').textContent = 'Não foi possível consultar seu acervo agora. Atualize a página para tentar novamente.'; }
  }

  init().catch(() => { notice('Não foi possível iniciar o Cofre de Informações. Atualize a página ou entre novamente.', true); });
})();
