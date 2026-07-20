    function _perguntasAprovacoesNotificadasSet() {
      if (perguntasAprovacoesNotificadas) return perguntasAprovacoesNotificadas;
      let lista = [];
      try { lista = JSON.parse(localStorage.getItem(PERGUNTAS_APPROVALS_NOTIFY_KEY) || '[]') || []; } catch (_) { lista = []; }
      perguntasAprovacoesNotificadas = new Set(Array.isArray(lista) ? lista.map(String) : []);
      return perguntasAprovacoesNotificadas;
    }

    function _perguntasSalvarAprovacoesNotificadasSet() {
      try {
        const lista = Array.from(_perguntasAprovacoesNotificadasSet()).slice(-240);
        localStorage.setItem(PERGUNTAS_APPROVALS_NOTIFY_KEY, JSON.stringify(lista));
        perguntasAprovacoesNotificadas = new Set(lista);
      } catch (_) {}
    }

    function _perguntasApprovalId(approval) {
      return String(approval && (approval.id || approval.approval_id || approval.question_id) || '').trim();
    }

    function _perguntasIsPosVenda(approval) {
      if (!approval || typeof approval !== 'object') return false;
      return [approval.tipo, approval.approval_type, approval.origem, approval.ia_origem, approval.ia_finalidade]
        .some((value) => {
          const marker = String(value || '').trim().toLowerCase();
          return marker.includes('pos_venda') || marker.includes('pos-venda');
        });
    }

    function _perguntasTextoPrincipal(approval) {
      const direto = String(approval && approval.pergunta || '').trim();
      if (direto) return direto;
      const conversa = approval && approval.conversa && typeof approval.conversa === 'object' ? approval.conversa : {};
      const last = String(conversa.last_message_text || '').trim();
      if (last) return last;
      const mensagens = Array.isArray(approval && approval.mensagens) ? approval.mensagens : [];
      for (let i = mensagens.length - 1; i >= 0; i -= 1) {
        const msg = mensagens[i] || {};
        const role = String(msg.from_role || msg.role || '').toLowerCase();
        if (role === 'seller') continue;
        const texto = String(msg.text || msg.plain || msg.message || '').trim();
        if (texto) return texto;
      }
      return 'A IA gerou uma resposta e aguarda sua revisao.';
    }

    function _perguntasTituloNotificacao(approval) {
      return _perguntasIsPosVenda(approval)
        ? 'Nova mensagem pos-venda para aprovar'
        : 'Nova pergunta do Mercado Livre para aprovar';
    }

    function _perguntasCorpoNotificacao(approval) {
      const loja = String(approval && approval.loja || '').trim();
      const sku = String(approval && approval.sku || '').trim();
      const titulo = String(approval && (approval.titulo || approval.item_id || approval.pack_id) || '').trim();
      const partes = [];
      if (loja) partes.push(`Loja ${loja}`);
      if (sku) partes.push(`SKU ${sku}`);
      if (titulo) partes.push(titulo);
      const cabecalho = partes.join(' - ');
      const texto = _perguntasTextoPrincipal(approval);
      return `${cabecalho ? cabecalho + '\n' : ''}${texto}`.trim();
    }

    async function _perguntasMostrarNotificacaoWindows(approval) {
      if (_perguntasIsPosVenda(approval)) return;
      const title = _perguntasTituloNotificacao(approval);
      const body = _perguntasCorpoNotificacao(approval);
      if (window.electronAPI && typeof window.electronAPI.showWindowsNotification === 'function') {
        try {
          const result = await window.electronAPI.showWindowsNotification({
            title,
            body,
            silent: false,
          });
          if (result && result.success) return;
        } catch (_) {}
      }

      if (!_msgNotificationDisponivel()) return;
      try {
        if (Notification.permission === 'default') {
          const pedido = Notification.requestPermission();
          if (pedido && typeof pedido.then === 'function') await pedido.catch(() => {});
        }
        if (Notification.permission !== 'granted') return;
        const notificacao = new Notification(title, {
          body,
          tag: `jk-perguntas-${_perguntasApprovalId(approval)}`,
          renotify: true,
        });
        notificacao.onclick = () => {
          try { window.focus(); } catch (_) {}
          _adicionarNotificacaoAprovacao(approval, { abrirPainel: true });
          try { notificacao.close(); } catch (_) {}
        };
        setTimeout(() => {
          try { notificacao.close(); } catch (_) {}
        }, 11000);
      } catch (_) {}
    }

    async function _perguntasNotificarWindowsAprovacaoUmaVez(approval) {
      if (_perguntasIsPosVenda(approval)) return;
      const approvalId = _perguntasApprovalId(approval);
      if (!approvalId) return;
      const status = String(approval && approval.status || 'pending').toLowerCase();
      if (status && status !== 'pending') return;
      const conhecidos = _perguntasAprovacoesNotificadasSet();
      if (conhecidos.has(approvalId)) return;
      conhecidos.add(approvalId);
      _perguntasSalvarAprovacoesNotificadasSet();
      await _perguntasMostrarNotificacaoWindows(approval);
    }

    function _perguntasMonitorAssumirLideranca() {
      try {
        const agora = Date.now();
        const owner = JSON.parse(localStorage.getItem(PERGUNTAS_MONITOR_OWNER_KEY) || '{}') || {};
        const ownerId = String(owner.id || '');
        const ownerTs = Number(owner.ts || 0);
        if (ownerId && ownerId !== perguntasMonitorId && agora - ownerTs < PERGUNTAS_MONITOR_STALE_MS) {
          return false;
        }
        localStorage.setItem(PERGUNTAS_MONITOR_OWNER_KEY, JSON.stringify({ id: perguntasMonitorId, ts: agora }));
        return true;
      } catch (_) {
        return true;
      }
    }

    function _perguntasMonitorLiberarLideranca() {
      try {
        const owner = JSON.parse(localStorage.getItem(PERGUNTAS_MONITOR_OWNER_KEY) || '{}') || {};
        if (String(owner.id || '') === perguntasMonitorId) {
          localStorage.removeItem(PERGUNTAS_MONITOR_OWNER_KEY);
        }
      } catch (_) {}
    }

    async function _perguntasNotificarAprovacaoPendente(approval) {
      if (_perguntasIsPosVenda(approval)) return;
      const approvalId = _perguntasApprovalId(approval);
      if (!approvalId) return;
      _adicionarNotificacaoAprovacao(approval, { abrirPainel: false });
    }

    function _perguntasMonitorOrdenarPendentes(pendentes) {
      return [...(Array.isArray(pendentes) ? pendentes : [])].sort((a, b) => {
        const dataA = Date.parse(String(a && a.created_at || a && a.updated_at || '')) || 0;
        const dataB = Date.parse(String(b && b.created_at || b && b.updated_at || '')) || 0;
        return dataB - dataA;
      });
    }

    function _perguntasMonitorLimitePorCiclo() {
      return panelAberto === true || codexPanelAberto === true
        ? PERGUNTAS_MONITOR_MAX_NOTIFICACOES_ABERTO
        : PERGUNTAS_MONITOR_MAX_NOTIFICACOES_FECHADO;
    }

    function _perguntasMonitorPausaCurta() {
      return new Promise(resolve => setTimeout(resolve, PERGUNTAS_MONITOR_YIELD_MS));
    }

    async function _perguntasMonitorBuscarAprovacoes() {
      if (!_usuarioLocalEhFull() || perguntasMonitorRodando || !_token()) return;
      if (!_perguntasMonitorAssumirLideranca()) return;
      perguntasMonitorRodando = true;
      try {
        const response = await window.__JK_IA_SIDEBAR_FETCH__('/api/mercadolivre/perguntas/aprovacoes', {
          headers: _authHeaders(),
          cache: 'no-store',
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || data.success === false) return;
        const pendentes = _perguntasMonitorOrdenarPendentes(
          (Array.isArray(data.pendentes) ? data.pendentes : []).filter(approval => !_perguntasIsPosVenda(approval))
        );
        const conhecidos = _perguntasAprovacoesNotificadasSet();
        const novas = pendentes.filter((approval) => {
          const id = _perguntasApprovalId(approval);
          return id && !conhecidos.has(id);
        });
        const base = novas.length ? novas : ((panelAberto === true || codexPanelAberto === true) ? pendentes : []);
        if (!base.length) return;
        const limite = Math.max(1, _perguntasMonitorLimitePorCiclo());
        const processar = base.slice(0, limite);
        for (let i = 0; i < processar.length; i += 1) {
          const approval = processar[i];
          await _perguntasNotificarAprovacaoPendente(approval);
          if (i + 1 < processar.length) await _perguntasMonitorPausaCurta();
        }
      } catch (_) {
      } finally {
        perguntasMonitorRodando = false;
        _perguntasMonitorAssumirLideranca();
      }
    }

    function _perguntasIniciarMonitorGlobal() {
      if (!_usuarioLocalEhFull() || perguntasMonitorTimer || perguntasMonitorStartTimer || !_token()) return;
      perguntasMonitorStartTimer = setTimeout(() => {
        perguntasMonitorStartTimer = null;
        void _perguntasMonitorBuscarAprovacoes();
        perguntasMonitorTimer = setInterval(() => {
          void _perguntasMonitorBuscarAprovacoes();
        }, PERGUNTAS_MONITOR_INTERVAL_MS);
      }, PERGUNTAS_MONITOR_INITIAL_DELAY_MS);
      window.addEventListener('beforeunload', _perguntasMonitorLiberarLideranca);
    }
