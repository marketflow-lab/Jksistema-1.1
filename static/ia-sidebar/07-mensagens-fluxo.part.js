    function _msgSetChatView(ativo) {
      msgChatAberto = !!ativo;
      document.getElementById('jk-msg-main-view')?.style.setProperty('display', msgChatAberto ? 'none' : 'flex');
      document.getElementById('jk-msg-chat-header')?.classList.toggle('ativo', msgChatAberto);
      document.getElementById('jk-msg-history')?.classList.toggle('ativo', msgChatAberto);
      if (!msgChatAberto) {
        _msgPararDigitandoLocal();
        _msgSetDigitando(false);
      }
      _msgAtualizarSelecao();
      _msgAtualizarDigitandoPoll();
    }

    function _msgStatusHistorico(msg, fromMe) {
      const criado = String(msg && msg.created_at || '').trim();
      if (!fromMe) return criado;
      let status = 'Enviada';
      if (msg && msg.read_at) status = 'Lida';
      else if (msg && msg.delivered_at) status = 'Entregue';
      else if (!String(msg && msg.storage || '').includes('firebase')) status = 'Enviada localmente';
      return criado ? `${criado} - ${status}` : status;
    }

    function _msgRenderCallHistorico(bubble, msg, fromMe) {
      const call = msg && msg.call;
      if (!_msgIsDailyCall(call)) return false;
      const card = document.createElement('div');
      card.className = 'jk-msg-call-card';
      const roomName = String(call.room_name || '').trim();
      if (roomName) card.title = roomName;

      const icon = document.createElement('span');
      icon.className = 'jk-msg-call-icon';
      icon.innerHTML = '&#128249;';
      card.appendChild(icon);

      const info = document.createElement('div');
      info.className = 'jk-msg-call-info';
      const title = document.createElement('span');
      title.className = 'jk-msg-call-title';
      title.textContent = 'Videochamada';

      const text = document.createElement('div');
      text.className = 'jk-msg-call-text';
      text.textContent = fromMe ? 'Voce iniciou uma chamada' : 'Chamada recebida';
      info.appendChild(title);
      info.appendChild(text);

      const actions = document.createElement('div');
      actions.className = 'jk-msg-call-actions';
      const join = document.createElement('button');
      join.type = 'button';
      join.className = 'jk-msg-call-join';
      join.textContent = 'Entrar';
      join.addEventListener('click', () => _msgAbrirSalaReuniao(call, false));
      actions.appendChild(join);

      card.appendChild(info);
      card.appendChild(actions);
      bubble.appendChild(card);
      return true;
    }

    function _msgRenderHistorico(mensagens) {
      const el = document.getElementById('jk-msg-history');
      if (!el) return;
      const lista = Array.isArray(mensagens) ? mensagens : [];
      el.innerHTML = '';
      if (!lista.length) {
        _msgRenderVazio(el, 'Nenhuma mensagem nesta conversa ainda.');
        return;
      }
      const atual = _msgUsernameAtual();
      lista.forEach(msg => {
        const bubble = document.createElement('div');
        const fromMe = String(msg.sender_username || '').toLowerCase() === atual;
        bubble.className = 'jk-msg-bubble ' + (fromMe ? 'me' : 'other');
        const texto = document.createElement('span');
        texto.textContent = String(msg.message || '').trim();
        const meta = document.createElement('span');
        meta.className = 'jk-msg-bubble-meta';
        if (fromMe && !String(msg && msg.storage || '').includes('firebase')) meta.classList.add('local-alert');
        meta.textContent = _msgStatusHistorico(msg, fromMe);
        const callRendered = _msgRenderCallHistorico(bubble, msg, fromMe);
        if (texto.textContent && !callRendered) bubble.appendChild(texto);
        _msgRenderAnexosHistorico(bubble, msg.attachments || []);
        if (meta.textContent) bubble.appendChild(meta);
        el.appendChild(bubble);
      });
      el.scrollTop = el.scrollHeight;
      const body = document.getElementById('jk-msg-body');
      if (body) body.scrollTop = body.scrollHeight;
    }

    async function _msgCarregarHistorico(silencioso = false) {
      if (!msgUsuarioSelecionado || !msgUsuarioSelecionado.username) return;
      const alvo = { ...msgUsuarioSelecionado };
      const cacheKey = _msgChaveHistorico(alvo);
      const cache = msgHistoricoCache.get(cacheKey);
      if (Array.isArray(cache) && cache.length) _msgRenderHistorico(cache);
      if (!silencioso) _msgSetStatus(cache ? `Atualizando chat com ${_msgLabelUsuario(alvo)}...` : `Carregando chat com ${_msgLabelUsuario(alvo)}...`);
      try {
        const params = new URLSearchParams({
          username: String(alvo.username || ''),
          client_id: String(alvo.client_id || _clientId() || 'default'),
          limit: String(MSG_HISTORY_LIMIT),
        });
        const resp = await window.__JK_IA_SIDEBAR_FETCH__(`/api/user/chat/history?${params.toString()}`, {
          method: 'GET',
          headers: _authHeaders(),
          cache: 'no-store',
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok || data.success === false) throw new Error(data.detail || data.message || 'Erro ao carregar historico.');
        if (!msgUsuarioSelecionado || _msgChaveHistorico(msgUsuarioSelecionado) !== cacheKey) return;
        msgHistoricoCache.set(cacheKey, data.messages || []);
        _msgRenderHistorico(data.messages || []);
        const title = document.getElementById('jk-msg-chat-title');
        if (title) title.textContent = `Chat com ${data.other_name || _msgLabelUsuario(alvo)}`;
        const hora = new Date().toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
        _msgSetStatus(`Chat atualizado ${hora}.`);
        void _msgBuscarMensagens().catch(() => {});
      } catch (err) {
        _msgSetStatus(err && err.message ? err.message : 'Nao foi possivel carregar o historico.', true);
      }
    }

    async function _msgAbrirChat(user) {
      const username = String((user && user.username) || '').trim().toLowerCase();
      if (!username) return;
      const atual = _msgUsernameAtual();
      const clientId = String((user && user.client_id) || _clientId() || 'default').trim() || 'default';
      if (username === atual && clientId === (_clientId() || 'default')) {
        _msgSetStatus('Selecione outro usuario para conversar.', true);
        return;
      }
      if (
        msgUsuarioSelecionado &&
        (String(msgUsuarioSelecionado.username || '').toLowerCase() !== username ||
          String(msgUsuarioSelecionado.client_id || _clientId() || 'default') !== clientId)
      ) {
        _msgPararDigitandoLocal();
        msgTypingEnviado = false;
      }
      msgUsuarioSelecionado = {
        username,
        client_id: clientId,
        name: _msgLabelUsuario(user),
      };
      _msgSetChatView(true);
      _msgAtualizarDigitandoPoll();
      void _msgCarregarHistorico();
    }

    function _msgVoltarLista() {
      _msgPararDigitandoLocal();
      _msgSetChatView(false);
      _msgSetStatus('Selecione um usuario para abrir o chat.');
      void _msgCarregarPainel(true);
    }

    function _msgAtualizarSelecao() {
      const selected = document.getElementById('jk-msg-selected');
      const send = document.getElementById('jk-msg-send');
      const video = document.getElementById('jk-msg-video-call');
      if (selected) {
        selected.textContent = msgChatAberto && msgUsuarioSelecionado
          ? `Chat com ${_msgLabelUsuario(msgUsuarioSelecionado)}`
          : 'Selecione um usuario para enviar mensagem.';
      }
      if (send) send.disabled = !(msgChatAberto && msgUsuarioSelecionado);
      if (video) video.disabled = !(msgChatAberto && msgUsuarioSelecionado);
      document.querySelectorAll('.jk-msg-user-item').forEach(btn => {
        const username = String(btn.dataset.username || '').toLowerCase();
        const ativo = msgUsuarioSelecionado && username === String(msgUsuarioSelecionado.username || '').toLowerCase();
        btn.classList.toggle('ativo', !!ativo);
      });
    }

    function _msgRenderInbox(mensagens) {
      const el = document.getElementById('jk-msg-inbox-list');
      if (!el) return;
      const lista = Array.isArray(mensagens) ? mensagens : [];
      const section = el.closest('.jk-msg-inbox-section');
      if (section) section.classList.toggle('jk-msg-section-empty', !lista.length);
      if (!lista.length) {
        _msgRenderVazio(el, 'Nenhuma mensagem nova.');
        return;
      }
      el.innerHTML = '';
      lista.forEach(msg => {
        const card = document.createElement('div');
        card.className = 'jk-msg-card';

        const title = document.createElement('div');
        title.className = 'jk-msg-card-title';
        title.textContent = msg.type === 'chat'
          ? `Chat com ${msg.name || msg.username || 'Usuario'}${Number(msg.unread_count || 0) > 1 ? ` (${msg.unread_count})` : ''}`
          : String(msg.title || 'Mensagem').trim();

        const meta = document.createElement('div');
        meta.className = 'jk-msg-card-meta';
        const sender = String(msg.sender || 'sistema').trim();
        const created = String(msg.created_at || '').trim();
        meta.textContent = msg.type === 'chat'
          ? `${msg.name || msg.username || 'Usuario'}${created ? ' - ' + created : ''}`
          : `${sender}${created ? ' - ' + created : ''}`;

        const text = document.createElement('div');
        text.className = 'jk-msg-card-text';
        text.textContent = _msgIsDailyCall(msg.call)
          ? 'Chamada de video Daily'
          : String(msg.message || msg.last_message || 'Anexo').trim();

        const actions = document.createElement('div');
        actions.className = 'jk-msg-card-actions';
        const action = document.createElement('button');
        action.className = 'jk-msg-small-btn';
        action.type = 'button';
        if (msg.type === 'chat') {
          action.textContent = 'Abrir chat';
          action.addEventListener('click', () => _msgAbrirChat({
            username: msg.username,
            client_id: msg.client_id,
            name: msg.name || msg.username,
          }));
        } else {
          action.textContent = 'Marcar como lida';
          action.addEventListener('click', () => _msgMarcarComoLida(msg.id));
        }
        actions.appendChild(action);

        card.appendChild(title);
        card.appendChild(meta);
        card.appendChild(text);
        card.appendChild(actions);
        el.appendChild(card);
      });
    }

    function _msgRenderUsuarios(usuarios) {
      const el = document.getElementById('jk-msg-online-list');
      if (!el) return;
      const listaBase = (Array.isArray(usuarios) ? usuarios : []).filter(user => user && user.username);
      const vistos = new Set(listaBase.map(user => _msgChaveUsuario(user.username, user.client_id)));
      const extrasNaoLidas = Array.from(msgNaoLidasPorUsuario.values())
        .filter(item => item && item.username && !vistos.has(_msgChaveUsuario(item.username, item.client_id)));
      const lista = listaBase.concat(extrasNaoLidas.map(item => ({
        username: item.username,
        client_id: item.client_id,
        name: item.name || item.username,
        online: false,
        unread_count: item.unread_count,
      })));
      const countEl = document.getElementById('jk-msg-contact-count');
      if (countEl) countEl.textContent = `${lista.length} contato${lista.length === 1 ? '' : 's'}`;
      if (!lista.length) {
        _msgRenderVazio(el, 'Nenhum usuario encontrado.');
        _msgAtualizarSelecao();
        return;
      }
      const atual = _msgUsernameAtual();
      el.innerHTML = '';
      lista.forEach(user => {
        const username = String(user.username || '').trim().toLowerCase();
        const isSelf = username && username === atual;
        const clientId = String(user.client_id || _clientId() || 'default').trim() || 'default';
        const unread = msgNaoLidasPorUsuario.get(_msgChaveUsuario(username, clientId)) || {};
        const unreadCount = Number(user.unread_count || unread.unread_count || 0);
        const btn = document.createElement('button');
        btn.type = 'button';
        const userOnline = _msgUsuarioEstaOnline(user, !!isSelf);
        btn.className = 'jk-msg-user-item';
        btn.classList.toggle('offline', !userOnline);
        btn.classList.toggle('self', !!isSelf);
        btn.dataset.username = username;
        btn.dataset.clientId = clientId;
        btn.disabled = !!isSelf;

        const avatar = document.createElement('span');
        avatar.className = `jk-msg-avatar${_msgAvatarClasse(user)}`;
        avatar.textContent = _msgInicialUsuario(user);

        const info = document.createElement('span');
        info.className = 'jk-msg-user-info';
        const name = document.createElement('span');
        name.className = 'jk-msg-user-name';
        const statusUsuario = _msgStatusUsuarioTexto(user);
        name.textContent = _msgLabelUsuario(user);
        name.title = name.textContent;
        const meta = document.createElement('span');
        meta.className = 'jk-msg-user-meta';
        meta.textContent = _msgSubtituloUsuario(user, isSelf);
        info.appendChild(name);
        info.appendChild(meta);

        const alertDot = document.createElement('span');
        alertDot.className = `jk-msg-contact-alert ${userOnline ? 'online' : 'offline'}`;
        alertDot.title = unreadCount > 0 ? `${unreadCount} mensagem(ns) nao lida(s) | ${statusUsuario}` : statusUsuario;

        const chevron = document.createElement('span');
        chevron.className = 'jk-msg-chevron';
        chevron.setAttribute('aria-hidden', 'true');
        chevron.textContent = '\u203A';

        btn.appendChild(avatar);
        btn.appendChild(info);
        btn.appendChild(alertDot);
        btn.appendChild(chevron);
        if (unreadCount > 0) {
          const badge = document.createElement('span');
          badge.className = 'jk-msg-unread-count';
          badge.textContent = unreadCount > 99 ? '99+' : String(unreadCount);
          badge.title = `${unreadCount} mensagem(ns) nao lida(s)`;
          btn.appendChild(badge);
        }
        if (!isSelf) btn.addEventListener('click', () => _msgAbrirChat(user));
        el.appendChild(btn);
      });
      _msgAtualizarSelecao();
    }

    async function _msgBuscarMensagens() {
      const resp = await window.__JK_IA_SIDEBAR_FETCH__('/api/user/messages', {
        method: 'GET',
        headers: _authHeaders(),
        cache: 'no-store',
      });
      const adminData = await resp.json().catch(() => ({}));
      if (!resp.ok || adminData.success === false) {
        throw new Error(adminData.detail || adminData.message || 'Erro ao buscar mensagens.');
      }

      const adminMessages = (Array.isArray(adminData.messages) ? adminData.messages : []).map(item => ({
        ...item,
        type: 'admin',
      }));
      const chatConversations = Array.isArray(adminData.chat_conversations) ? adminData.chat_conversations : [];
      msgNaoLidasPorUsuario = new Map(chatConversations.map(item => [
        _msgChaveUsuario(item.username, item.client_id),
        {
          username: item.username,
          client_id: item.client_id,
          name: item.name,
          unread_count: Number(item.unread_count || 0),
          call: item.call || null,
        },
      ]));
      _msgNotificarConversas(chatConversations);
      _msgProcessarChamadasRecebidas(chatConversations);
      const chatMessages = chatConversations.map(item => ({
        type: 'chat',
        username: item.username,
        client_id: item.client_id,
        name: item.name,
        unread_count: item.unread_count,
        last_message_id: item.last_message_id,
        last_message: item.last_message,
        message: item.last_message,
        call: item.call || null,
        created_at: item.created_at,
        created_ts: item.created_ts,
      }));
      const summary = adminData.summary && typeof adminData.summary === 'object' ? adminData.summary : {};
      const total = Number(
        summary.unread_count !== undefined
          ? summary.unread_count
          : (Number(adminData.unread_count || adminMessages.length || 0) + Number(summary.user_chat_unread_count || 0))
      );
      _msgSetBadge(total);
      return [...chatMessages, ...adminMessages];
    }

    async function _msgBuscarUsuariosOnline() {
      let contatosAutorizados = null;
      try {
        const resp = await window.__JK_IA_SIDEBAR_FETCH__('/api/user/chat/contacts', {
          method: 'GET',
          headers: _authHeaders(),
          cache: 'no-store',
        });
        const data = await resp.json().catch(() => ({}));
        if (resp.ok && data && data.success !== false && Array.isArray(data.users)) {
          contatosAutorizados = data.users.filter(user => user && user.active !== false);
          msgContatosAutoritativos = true;
        }
      } catch (_) {}

      if (typeof window.jkBuscarUsuariosOnline === 'function') {
        try {
          const data = await window.jkBuscarUsuariosOnline({ realtimeOnly: !!contatosAutorizados });
          if (data && data.success !== false && Array.isArray(data.users)) {
            const users = data.users.filter(user => user && user.active !== false);
            if (contatosAutorizados) {
              const contatosComPresenca = _msgMesclarPresencaUsuarios(contatosAutorizados, users);
              msgContatosAutoritativos = true;
              _msgSalvarUsuariosCache(contatosComPresenca);
              if (_msgListaTemPresencaOnline(contatosComPresenca)) return contatosComPresenca;
            } else {
              msgContatosAutoritativos = false;
              _msgSalvarUsuariosCache(users);
              return users;
            }
          }
        } catch (_) {}
      }

      if (contatosAutorizados) {
        try {
          const resp = await window.__JK_IA_SIDEBAR_FETCH__('/api/admin/users/online', {
            method: 'GET',
            headers: _authHeaders(),
            cache: 'no-store',
          });
          const data = await resp.json().catch(() => ({}));
          if (resp.ok && data && data.success !== false && Array.isArray(data.users)) {
            const users = data.users.filter(user => user && user.active !== false);
            const contatosComPresenca = _msgMesclarPresencaUsuarios(contatosAutorizados, users);
            msgContatosAutoritativos = true;
            _msgSalvarUsuariosCache(contatosComPresenca);
            return contatosComPresenca;
          }
        } catch (_) {}
        msgContatosAutoritativos = true;
        _msgSalvarUsuariosCache(contatosAutorizados);
        return contatosAutorizados;
      }

      try {
        const resp = await window.__JK_IA_SIDEBAR_FETCH__('/api/admin/users/online', {
          method: 'GET',
          headers: _authHeaders(),
          cache: 'no-store',
        });
        const data = await resp.json().catch(() => ({}));
        if (resp.ok && data && data.success !== false && Array.isArray(data.users)) {
          const users = data.users.filter(user => user && user.active !== false);
          msgContatosAutoritativos = false;
          _msgSalvarUsuariosCache(users);
          return users;
        }
      } catch (_) {}

      if (typeof window.jkBuscarMaquinasOnline === 'function') {
        const data = await window.jkBuscarMaquinasOnline();
        const machines = Array.isArray(data && data.machines) ? data.machines : [];
        if (machines.length) {
          const userData = _msgUserData();
          msgContatosAutoritativos = false;
          return [{
            username: _msgUsernameAtual() || 'usuario',
            name: userData.name || userData.username || 'Usuario atual',
            client_id: _clientId(),
            online: true,
            online_count: machines.length,
            machines,
          }];
        }
      }

      msgContatosAutoritativos = false;
      return [];
    }

    async function _msgCarregarPainel(silencioso = false) {
      const usuariosCache = _msgLerUsuariosCache();
      if (usuariosCache.length) _msgRenderUsuarios(usuariosCache);
      if (msgMensagensCache.length) _msgRenderInbox(msgMensagensCache);
      if (msgCarregando) return;
      msgCarregando = true;
      if (!silencioso) _msgSetStatus('Atualizando mensagens e usuarios online...');
      try {
        const msgsPromise = _msgBuscarMensagens()
          .then(mensagens => {
            msgMensagensCache = Array.isArray(mensagens) ? mensagens : [];
            _msgRenderInbox(msgMensagensCache);
            return msgMensagensCache;
          });
        const usersPromise = _msgBuscarUsuariosOnline()
          .then(usuarios => {
            const lista = Array.isArray(usuarios) ? usuarios : [];
            if (msgContatosAutoritativos || lista.length || !usuariosCache.length) _msgRenderUsuarios(lista);
            return lista;
          });
        const [msgsResult, usersResult] = await Promise.allSettled([msgsPromise, usersPromise]);
        if (msgsResult.status === 'rejected') {
          _msgSetStatus(msgsResult.reason?.message || 'Nao foi possivel buscar mensagens.', true);
        } else {
          const hora = new Date().toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
          const complemento = usersResult.status === 'rejected' && usuariosCache.length ? ' (contatos em cache)' : '';
          _msgSetStatus(`Atualizado ${hora}.${complemento}`);
        }
      } finally {
        msgCarregando = false;
      }
    }

    async function _msgMarcarComoLida(messageId) {
      const id = String(messageId || '').trim();
      if (!id) return;
      try {
        const resp = await window.__JK_IA_SIDEBAR_FETCH__(`/api/user/messages/${encodeURIComponent(id)}/read`, {
          method: 'POST',
          headers: _authHeaders(),
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok || data.success === false) throw new Error(data.detail || data.message || 'Erro ao marcar mensagem.');
        await _msgCarregarPainel(true);
      } catch (err) {
        _msgSetStatus(err && err.message ? err.message : 'Nao foi possivel marcar como lida.', true);
      }
    }

    function _msgBuildDailyCallPayload(destino) {
      const hoje = new Date().toISOString().slice(0, 10).replace(/-/g, '');
      const atual = _msgUsernameAtual() || 'usuario';
      const outro = String(destino && destino.username || 'usuario').trim().toLowerCase() || 'usuario';
      return {
        nome: `chamada-${atual}-${outro}-${hoje}`,
        privacidade: 'public',
        idioma: 'pt-BR',
        expira_em_minutos: 180,
        duracao_maxima_minutos: null,
        limitar_participantes: false,
        max_participantes: null,
        nome_host: _msgNomeAtual() || 'Anfitriao JK Sistema',
        iniciar_audio_desligado: false,
        iniciar_video_desligado: false,
        habilitar_prejoin: false,
        habilitar_sala_espera: false,
        habilitar_compartilhar_tela: true,
        habilitar_chat: true,
        habilitar_historico_chat: true,
        habilitar_chat_avancado: true,
        habilitar_pessoas: true,
        habilitar_mao_levantada: true,
        habilitar_reacoes: true,
        habilitar_rede: true,
        habilitar_pip: true,
        habilitar_legendas: false,
        habilitar_cancelamento_ruido: false,
        habilitar_fundo_virtual: true,
        habilitar_salas_grupo: false,
        habilitar_alerta_cpu: true,
        habilitar_participantes_ocultos: false,
        habilitar_chamadas_grandes: false,
        habilitar_simulcast_adaptativo: false,
        exigir_user_id_unico: false,
        habilitar_log_reduzido: false,
        habilitar_dialout: false,
        ejetar_na_expiracao: false,
        modo_gravacao: '',
        criar_token_host: true,
        auto_iniciar_gravacao: false,
        auto_iniciar_transcricao: false,
      };
    }

    async function _msgIniciarVideoChamada() {
      const destino = msgUsuarioSelecionado;
      const btn = document.getElementById('jk-msg-video-call');
      if (!msgChatAberto || !destino || !destino.username) {
        _msgSetStatus('Abra um chat com um usuario antes de iniciar chamada.', true);
        return;
      }
      if (btn) btn.disabled = true;
      _msgSetStatus(`Criando chamada Daily para ${_msgLabelUsuario(destino)}...`);
      try {
        const salaResp = await window.__JK_IA_SIDEBAR_FETCH__('/api/sala-reuniao/salas', {
          method: 'POST',
          headers: _authHeaders(),
          body: JSON.stringify(_msgBuildDailyCallPayload(destino)),
        });
        const salaData = await salaResp.json().catch(() => ({}));
        if (!salaResp.ok || salaData.success === false) {
          throw new Error(salaData.detail || salaData.message || 'Nao foi possivel criar a sala Daily.');
        }
        const room = salaData.room || {};
        const roomUrl = String(room.url || '').trim();
        if (!roomUrl) throw new Error('A Daily nao retornou o link da sala.');
        const call = {
          type: 'daily_video',
          room_name: room.name || '',
          room_url: roomUrl,
          started_at: new Date().toISOString(),
          expires_at: room.expires_at || '',
          created_by: _msgUsernameAtual(),
          created_by_name: _msgNomeAtual(),
          invited_username: destino.username,
          invited_client_id: destino.client_id || _clientId() || 'default',
        };
        const msgResp = await window.__JK_IA_SIDEBAR_FETCH__('/api/user/chat/send', {
          method: 'POST',
          headers: _authHeaders(),
          body: JSON.stringify({
            username: destino.username,
            client_id: destino.client_id || _clientId() || 'default',
            message: 'Chamada de video Daily',
            attachments: [],
            call,
          }),
        });
        const msgData = await msgResp.json().catch(() => ({}));
        if (!msgResp.ok || msgData.success === false) {
          throw new Error(msgData.detail || msgData.message || 'A sala foi criada, mas nao consegui enviar o convite.');
        }
        if (msgData.delivery_available === false) {
          throw new Error(msgData.message || 'A sala foi criada, mas o convite ficou apenas local e nao chegou ao outro usuario.');
        }
        _msgSetStatus(`Chamando ${_msgLabelUsuario(destino)}...`);
        await _msgCarregarHistorico(true);
        await _msgCarregarPainel(true);
        _msgAbrirSalaReuniao({
          ...call,
          host_url: room.host_url || (salaData.host_token ? `${roomUrl}${roomUrl.includes('?') ? '&' : '?'}t=${encodeURIComponent(salaData.host_token)}` : ''),
        }, true);
      } catch (err) {
        _msgSetStatus(err && err.message ? err.message : 'Nao foi possivel iniciar a videochamada.', true);
      } finally {
        _msgAtualizarSelecao();
      }
    }

    async function _msgEnviarMensagem() {
      const destino = msgUsuarioSelecionado;
      const textEl = document.getElementById('jk-msg-text');
      const send = document.getElementById('jk-msg-send');
      const texto = String(textEl?.value || '').trim();
      if (!msgChatAberto || !destino || !destino.username) {
        _msgSetStatus('Abra um chat com um usuario antes de enviar.', true);
        return;
      }
      if (!texto && !msgAnexos.length) {
        _msgSetStatus('Digite a mensagem ou anexe um arquivo antes de enviar.', true);
        return;
      }
      if (send) send.disabled = true;
      _msgPararDigitandoLocal();
      _msgSetStatus('Enviando mensagem...');
      try {
        const resp = await window.__JK_IA_SIDEBAR_FETCH__('/api/user/chat/send', {
          method: 'POST',
          headers: _authHeaders(),
          body: JSON.stringify({
            username: destino.username,
            client_id: destino.client_id || _clientId() || 'default',
            message: texto,
            attachments: msgAnexos,
          }),
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok || data.success === false) throw new Error(data.detail || data.message || 'Erro ao enviar mensagem.');
        if (textEl) textEl.value = '';
        msgAnexos = [];
        _msgRenderAnexosComposer();
        _msgSetStatus(data.message || `Mensagem enviada para ${_msgLabelUsuario(destino)}.`);
        await _msgCarregarHistorico(true);
        await _msgCarregarPainel(true);
      } catch (err) {
        _msgSetStatus(err && err.message ? err.message : 'Nao foi possivel enviar mensagem.', true);
      } finally {
        _msgAtualizarSelecao();
      }
    }

    function _msgIntervaloAtualizacao() {
      if (document.hidden) return MSG_REFRESH_HIDDEN_INTERVAL_MS;
      if (msgChatAberto) return MSG_REFRESH_CHAT_INTERVAL_MS;
      if (msgPanelAberto) return MSG_REFRESH_PANEL_INTERVAL_MS;
      return MSG_REFRESH_CLOSED_INTERVAL_MS;
    }

    async function _msgExecutarAtualizacaoAgendada() {
      if (document.hidden) return;
      if (msgPanelAberto) {
        await _msgCarregarPainel(true);
        if (msgChatAberto) await _msgCarregarHistorico(true);
      } else {
        await _msgBuscarMensagens().catch(() => {});
      }
    }

    function _msgAgendarAtualizacao(delayMs) {
      if (msgRefreshTimer) clearTimeout(msgRefreshTimer);
      const intervalo = Math.max(30000, Number(delayMs) || _msgIntervaloAtualizacao());
      msgRefreshTimer = setTimeout(async () => {
        msgRefreshTimer = null;
        try {
          await _msgExecutarAtualizacaoAgendada();
        } finally {
          _msgAgendarAtualizacao(_msgIntervaloAtualizacao());
        }
      }, intervalo);
    }

    function _msgIniciarAtualizacao() {
      if (!msgRefreshTimer) _msgAgendarAtualizacao(_msgIntervaloAtualizacao());
    }

    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) _msgAgendarAtualizacao(1500);
    });

    // Carregar ou criar conversa inicial
    async function carregarOuCriarConversa() {
      if (convAtualId) {
        renderMsgs();
        return;
      }
      const lista = await _listarConversas();
      if (lista.length > 0) {
        convAtualId = lista[0].id;
        mensagensAtuais = await _carregarMensagens(convAtualId);
      } else {
        convAtualId = _novoId();
        mensagensAtuais = [];
      }
      renderMsgs();
    }

    async function novaConversa() {
      await salvarMensagensAtuais();
      convAtualId = _novoId();
      mensagensAtuais = [];
      const msgsEl = document.getElementById('jk-ia-msgs');
      msgsEl.innerHTML = '';
      addMsg('assistant', 'Olá! Começamos uma nova conversa. Em que posso ajudar?');
      if (convsVisible) renderConvsList();
    }

    async function salvarMensagensAtuais() {
      if (!convAtualId || mensagensAtuais.length === 0) return;
      try {
        await _salvarMensagens(convAtualId, mensagensAtuais);
      } catch (_) {}

      // Atualizar índice local sem depender de chamadas assíncronas.
      try {
        const lista = _listarConversasLocal().filter(c => c.id !== convAtualId);
        const preview = (mensagensAtuais.find(m => m.role === 'user')?.text || 'Conversa').slice(0, 60);
        const data = new Date().toLocaleString('pt-BR', {
          day: '2-digit',
          month: '2-digit',
          year: 'numeric',
          hour: '2-digit',
          minute: '2-digit',
        });
        lista.unshift({ id: convAtualId, data, preview });
        _salvarIndexLocal(lista);
      } catch (_) {}
    }

    const APPROVAL_MSG_PREFIX = '__JK_APPROVAL_CARD_V1__:';
