    function _msgSetBadge(total) {
      const badge = document.getElementById('jk-msg-badge');
      if (!badge) return;
      const n = Math.max(0, Number(total || 0));
      badge.textContent = n ? (n > 99 ? '99+' : String(n)) : '';
      msgTemMensagemNaoVista = n > 0;
      _sidebarAtualizarAlertas();
    }

    function _msgNotificacoesSet() {
      if (msgNotificacoesConhecidas) return msgNotificacoesConhecidas;
      let lista = [];
      try { lista = JSON.parse(localStorage.getItem(MSG_NOTIFICACOES_KEY) || '[]') || []; } catch (_) { lista = []; }
      msgNotificacoesConhecidas = new Set(Array.isArray(lista) ? lista.map(String) : []);
      return msgNotificacoesConhecidas;
    }

    function _msgSalvarNotificacoesSet() {
      try {
        const lista = Array.from(_msgNotificacoesSet()).slice(-120);
        localStorage.setItem(MSG_NOTIFICACOES_KEY, JSON.stringify(lista));
        msgNotificacoesConhecidas = new Set(lista);
      } catch (_) {}
    }

    function _msgNotificationDisponivel() {
      return typeof window !== 'undefined' && 'Notification' in window;
    }

    function _msgSolicitarPermissaoNotificacao() {
      if (!_msgNotificationDisponivel() || Notification.permission !== 'default') return;
      try {
        const pedido = Notification.requestPermission();
        if (pedido && typeof pedido.catch === 'function') pedido.catch(() => {});
      } catch (_) {}
    }

    function _msgPrepararNotificacoesWindows() {
      if (!_msgNotificationDisponivel() || Notification.permission !== 'default') return;
      const pedir = () => {
        document.removeEventListener('pointerdown', pedir, true);
        document.removeEventListener('keydown', pedir, true);
        _msgSolicitarPermissaoNotificacao();
      };
      document.addEventListener('pointerdown', pedir, true);
      document.addEventListener('keydown', pedir, true);
    }

    function _msgNotificacaoKey(conversa) {
      const user = String(conversa && conversa.username || '').trim().toLowerCase();
      const client = String(conversa && conversa.client_id || 'default').trim() || 'default';
      const id = String(conversa && conversa.last_message_id || '').trim();
      const ts = String(conversa && conversa.created_ts || '').trim();
      return `${user}|${client}|${id || ts || String(conversa && conversa.last_message || '').slice(0, 80)}`;
    }

    function _msgNotificarConversas(conversas) {
      if (!_msgNotificationDisponivel() || Notification.permission !== 'granted') return;
      const lista = Array.isArray(conversas) ? conversas : [];
      const conhecidos = _msgNotificacoesSet();
      let alterou = false;
      lista.forEach(conversa => {
        if (!conversa || Number(conversa.unread_count || 0) <= 0) return;
        const key = _msgNotificacaoKey(conversa);
        if (!key || conhecidos.has(key)) return;
        const nome = String(conversa.name || conversa.username || 'Usuario').trim();
        const body = _msgIsDailyCall(conversa.call)
          ? `${nome} esta chamando por video.`
          : String(conversa.last_message || 'Nova mensagem recebida.').trim();
        try {
          const notificacao = new Notification(`${nome} te enviou uma mensagem`, {
            body,
            tag: `jk-chat-${String(conversa.username || '').toLowerCase()}-${String(conversa.client_id || 'default')}`,
            renotify: true,
          });
          notificacao.onclick = () => {
            try { window.focus(); } catch (_) {}
            toggleMsgPanel(true);
            void _msgAbrirChat({
              username: conversa.username,
              client_id: conversa.client_id,
              name: nome,
            });
            try { notificacao.close(); } catch (_) {}
          };
          setTimeout(() => {
            try { notificacao.close(); } catch (_) {}
          }, 9000);
          conhecidos.add(key);
          alterou = true;
        } catch (_) {}
      });
      if (alterou) _msgSalvarNotificacoesSet();
    }

    function _msgCallSeen() {
      if (msgCallSeenSet) return msgCallSeenSet;
      let lista = [];
      try { lista = JSON.parse(localStorage.getItem(MSG_CALL_RING_SEEN_KEY) || '[]') || []; } catch (_) { lista = []; }
      msgCallSeenSet = new Set(Array.isArray(lista) ? lista.map(String) : []);
      return msgCallSeenSet;
    }

    function _msgSalvarCallSeen() {
      try {
        const lista = Array.from(_msgCallSeen()).slice(-120);
        localStorage.setItem(MSG_CALL_RING_SEEN_KEY, JSON.stringify(lista));
        msgCallSeenSet = new Set(lista);
      } catch (_) {}
    }

    function _msgMarcarCallSeen(id) {
      const key = String(id || '').trim();
      if (!key) return;
      _msgCallSeen().add(key);
      _msgSalvarCallSeen();
    }

    function _msgIsDailyCall(call) {
      return !!(call && typeof call === 'object' && String(call.type || '') === 'daily_video' && String(call.room_url || call.url || '').trim());
    }

    function _msgCallUrl(call, preferHost = false) {
      const raw = preferHost ? (call && (call.host_url || call.room_url || call.url)) : (call && (call.room_url || call.url || call.host_url));
      const texto = String(raw || '').trim();
      if (!texto) return '';
      try {
        const url = new URL(texto, window.location.href);
        const host = url.hostname.toLowerCase();
        if (url.protocol !== 'https:' || !(host === 'daily.co' || host.endsWith('.daily.co'))) return '';
        return url.href;
      } catch (_) {
        return '';
      }
    }

    function _msgNomeAtual() {
      const data = _msgUserData();
      return String(data.name || data.nome || data.username || data.user || _msgUsernameAtual() || 'Usuario').trim();
    }

    function _msgSalaReuniaoUrl(call, preferHost = false) {
      const url = _msgCallUrl(call, preferHost);
      if (!url) return '';
      const params = new URLSearchParams();
      params.set('join', url);
      if (call && call.room_name) params.set('room_name', String(call.room_name || ''));
      const path = `/sala_reuniao.html?${params.toString()}`;
      try {
        return new URL(path, window.location.origin || window.location.href).href;
      } catch (_) {
        return path;
      }
    }

    function _msgAbrirSalaReuniao(call, preferHost = false) {
      const target = _msgSalaReuniaoUrl(call, preferHost);
      if (!target) {
        _msgSetStatus('Link Daily da chamada indisponivel.', true);
        return;
      }
      let sentToShell = false;
      try {
        if (window.top && window.top !== window && typeof window.top.postMessage === 'function') {
          window.top.postMessage({
            channel: 'jk-open-module-tab',
            payload: { url: target, title: 'Sala de Reuniao' },
          }, '*');
          sentToShell = true;
        }
      } catch (_) {}
      if (sentToShell) return;
      try {
        window.location.assign(target);
      } catch (_) {
        window.location.href = target;
      }
    }

    function _msgTocarChamadaPulso() {
      try {
        const AudioCtx = window.AudioContext || window.webkitAudioContext;
        if (!AudioCtx) return;
        if (!msgCallAudioCtx) msgCallAudioCtx = new AudioCtx();
        if (msgCallAudioCtx.state === 'suspended') msgCallAudioCtx.resume().catch(() => {});
        const now = msgCallAudioCtx.currentTime;
        [0, 0.34].forEach((offset, idx) => {
          const osc = msgCallAudioCtx.createOscillator();
          const gain = msgCallAudioCtx.createGain();
          osc.type = 'sine';
          osc.frequency.value = idx ? 620 : 520;
          gain.gain.setValueAtTime(0.0001, now + offset);
          gain.gain.exponentialRampToValueAtTime(0.12, now + offset + 0.03);
          gain.gain.exponentialRampToValueAtTime(0.0001, now + offset + 0.28);
          osc.connect(gain);
          gain.connect(msgCallAudioCtx.destination);
          osc.start(now + offset);
          osc.stop(now + offset + 0.3);
        });
      } catch (_) {}
    }

    function _msgIniciarToqueChamada() {
      _msgPararToqueChamada();
      _msgTocarChamadaPulso();
      msgCallRingTimer = setInterval(_msgTocarChamadaPulso, 1300);
    }

    function _msgPararToqueChamada() {
      if (msgCallRingTimer) {
        clearInterval(msgCallRingTimer);
        msgCallRingTimer = null;
      }
    }

    function _msgFecharModalChamada() {
      _msgPararToqueChamada();
      const modal = document.getElementById('jk-msg-call-modal');
      if (modal) modal.hidden = true;
    }

    async function _msgNotificarChamadaWindows(chamada) {
      const nome = String(chamada && chamada.name || chamada && chamada.username || 'Usuario').trim();
      if (window.electronAPI && typeof window.electronAPI.showWindowsNotification === 'function') {
        try {
          await window.electronAPI.showWindowsNotification({
            title: 'Chamada de video',
            body: `${nome} esta chamando. Abra o chat para atender.`,
            silent: false,
          });
          return;
        } catch (_) {}
      }
      if (!_msgNotificationDisponivel() || Notification.permission !== 'granted') return;
      try {
        new Notification('Chamada de video', {
          body: `${nome} esta chamando.`,
          tag: `jk-video-call-${String(chamada && chamada.messageId || '')}`,
          renotify: true,
        });
      } catch (_) {}
    }

    function _msgMostrarChamadaRecebida(chamada) {
      if (!chamada || !_msgIsDailyCall(chamada.call)) return;
      const id = String(chamada.messageId || '').trim();
      if (!id || _msgCallSeen().has(id)) return;
      msgCallRinging = chamada;
      const modal = document.getElementById('jk-msg-call-modal');
      const avatar = document.getElementById('jk-msg-call-avatar');
      const title = document.getElementById('jk-msg-call-title');
      const subtitle = document.getElementById('jk-msg-call-subtitle');
      const nome = String(chamada.name || chamada.username || 'Usuario').trim();
      if (avatar) avatar.textContent = _msgInicialUsuario({ name: nome, username: chamada.username });
      if (title) title.textContent = `${nome} esta chamando`;
      if (subtitle) subtitle.textContent = 'Videochamada Daily pelo JK Sistema.';
      if (modal) modal.hidden = false;
      _msgIniciarToqueChamada();
      void _msgNotificarChamadaWindows(chamada);
    }

    function _msgAtenderChamadaRecebida() {
      const chamada = msgCallRinging;
      if (!chamada) return;
      _msgMarcarCallSeen(chamada.messageId);
      _msgFecharModalChamada();
      msgCallRinging = null;
      if (chamada.username) {
        void _msgAbrirChat({
          username: chamada.username,
          client_id: chamada.client_id,
          name: chamada.name || chamada.username,
        });
      }
      _msgAbrirSalaReuniao(chamada.call, false);
    }

    async function _msgRecusarChamadaRecebida() {
      const chamada = msgCallRinging;
      if (chamada && chamada.messageId) _msgMarcarCallSeen(chamada.messageId);
      _msgFecharModalChamada();
      msgCallRinging = null;
      if (chamada && chamada.username) {
        try {
          await window.__JK_IA_SIDEBAR_FETCH__('/api/user/chat/send', {
            method: 'POST',
            headers: _authHeaders(),
            body: JSON.stringify({
              username: chamada.username,
              client_id: chamada.client_id || _clientId() || 'default',
              message: 'Chamada recusada.',
              attachments: [],
            }),
          });
        } catch (_) {}
      }
    }

    function _msgProcessarChamadasRecebidas(conversas) {
      const lista = Array.isArray(conversas) ? conversas : [];
      const agora = Date.now();
      for (const conversa of lista) {
        if (!conversa || Number(conversa.unread_count || 0) <= 0 || !_msgIsDailyCall(conversa.call)) continue;
        const id = String(conversa.last_message_id || '').trim();
        if (!id || _msgCallSeen().has(id)) continue;
        const createdTs = Number(conversa.created_ts || 0);
        if (createdTs && agora - createdTs * 1000 > MSG_CALL_RING_MAX_AGE_MS) {
          _msgMarcarCallSeen(id);
          continue;
        }
        _msgMostrarChamadaRecebida({
          messageId: id,
          username: conversa.username,
          client_id: conversa.client_id,
          name: conversa.name || conversa.username,
          call: conversa.call,
          created_ts: createdTs,
        });
        break;
      }
    }
