    async function renderConvsList() {
      const lista = await _listarConversas();
      const el = document.getElementById('jk-ia-convs-lista');
      if (!el) return;
      if (lista.length === 0) {
        el.innerHTML = '<div style="color:#8ee9de;font-size:.75rem;opacity:.6">Nenhuma conversa salva ainda.</div>';
        return;
      }
      el.innerHTML = '';
      lista.forEach(conv => {
        const item = document.createElement('div');
        item.className = 'jk-ia-conv-item' + (conv.id === convAtualId ? ' ativa' : '');
        item.innerHTML = `<div class="jk-ia-conv-info"><div class="jk-ia-conv-data">${conv.data}</div><div class="jk-ia-conv-prev">${(conv.preview || '').replace(/</g,'&lt;')}</div></div><button class="jk-ia-conv-del" data-id="${conv.id}" title="Apagar">🗑</button>`;
        item.querySelector('.jk-ia-conv-info').addEventListener('click', async () => {
          await salvarMensagensAtuais();
          convAtualId = conv.id;
          mensagensAtuais = await _carregarMensagens(conv.id);
          mostrarChat();
          renderMsgs();
          await renderConvsList();
        });
        item.querySelector('.jk-ia-conv-del').addEventListener('click', async (e) => {
          e.stopPropagation();
          await _removerConversa(conv.id);
          if (conv.id === convAtualId) await novaConversa();
          else await renderConvsList();
        });
        el.appendChild(item);
      });
    }

    function mostrarChat() {
      document.getElementById('jk-ia-convs-panel').classList.remove('ativo');
      document.getElementById('jk-ia-chat-wrap').style.display = 'flex';
      convsVisible = false;
    }

    async function mostrarConvs() {
      document.getElementById('jk-ia-convs-panel').classList.add('ativo');
      document.getElementById('jk-ia-chat-wrap').style.display = 'none';
      convsVisible = true;
      await renderConvsList();
    }

    let iaConversaInicialPromise = null;

    function garantirConversaInicial() {
      if (iaConversaInicialPromise) return iaConversaInicialPromise;
      const statusEl = document.getElementById('jk-ia-status');
      if (statusEl && !convAtualId) statusEl.textContent = 'Carregando conversa...';
      iaConversaInicialPromise = carregarOuCriarConversa()
        .catch((error) => {
          iaConversaInicialPromise = null;
          if (statusEl) statusEl.textContent = 'Nao foi possivel carregar a conversa agora.';
          throw error;
        })
        .finally(() => {
          if (statusEl && statusEl.textContent === 'Carregando conversa...') statusEl.textContent = '';
        });
      return iaConversaInicialPromise;
    }

    function atualizarMenuLateral() {
      document.getElementById('jk-ia-fab')?.classList.toggle('ativo', panelAberto || codexPanelAberto);
      document.getElementById('jk-questions-fab')?.classList.toggle('ativo', perguntasPanelAberto);
      document.getElementById('jk-msg-fab')?.classList.toggle('ativo', msgPanelAberto);
      document.getElementById('jk-right-sidebar-hotspot')?.classList.remove('menu-aberto');
      _sidebarAtualizarAlertas();
    }

    function setIAPanelAberto(aberto) {
      panelAberto = !!aberto;
      if (panelAberto) iaTemMensagemNaoVista = false;
      document.getElementById('jk-ia-panel')?.classList.toggle('aberto', panelAberto);
      atualizarMenuLateral();
      if (panelAberto) {
        void garantirConversaInicial().catch(console.error);
        if (!iaModelosLazyStarted) {
          iaModelosLazyStarted = true;
          const select = document.getElementById('jk-ia-model-sel');
          if (select) _carregarModelosRemotos(select, 'ia_model_sidebar').catch(() => {});
        }
      }
    }

    function setMsgPanelAberto(aberto) {
      msgPanelAberto = !!aberto;
      if (msgPanelAberto) msgTemMensagemNaoVista = false;
      if (!msgPanelAberto) _msgPararDigitandoLocal();
      document.getElementById('jk-msg-panel')?.classList.toggle('aberto', msgPanelAberto);
      atualizarMenuLateral();
      if (msgPanelAberto) {
        _msgIniciarAtualizacao();
        void _msgCarregarPainel();
        if (msgChatAberto) void _msgCarregarHistorico(true);
      }
      _msgAtualizarDigitandoPoll();
    }

    function setCodexPanelAberto(aberto) {
      codexPanelAberto = !!aberto;
      if (codexPanelAberto) codexTemMensagemNaoVista = false;
      document.getElementById('jk-codex-panel')?.classList.toggle('aberto', codexPanelAberto);
      _codexSalvarEstadoPainel();
      atualizarMenuLateral();
      if (codexPanelAberto) {
        _codexAtualizarVisibilidade();
        if (!codexMessagesAtuais.length) _codexRenderizarHistoricoLocal();
        if (!codexLazyStarted) {
          codexLazyStarted = true;
          void _codexCarregarStatus(true);
          if (codexInitialTaskId) {
            const taskId = codexInitialTaskId;
            codexInitialTaskId = '';
            void _codexRestaurarTarefaAtiva(taskId);
          }
        } else {
          void _codexCarregarStatus(true);
        }
        document.getElementById('jk-ia-fab')?.classList.remove('piscando');
      }
    }

    function setQuestionsPanelAberto(aberto) {
      perguntasPanelAberto = !!aberto && _usuarioLocalEhFull();
      const panel = document.getElementById('jk-questions-panel');
      if (panel) {
        panel.hidden = !_usuarioLocalEhFull();
        panel.classList.toggle('aberto', perguntasPanelAberto);
      }
      atualizarMenuLateral();
      if (perguntasPanelAberto) {
        _questionsMarcarComoVistas(_questionsItensOrdenados());
        void _questionsCarregarPendentes({ marcarVistas: true });
      }
    }

    function togglePanel(forcar) {
      const abrir = forcar !== undefined ? !!forcar : !panelAberto;
      if (abrir) {
        setMsgPanelAberto(false);
        setCodexPanelAberto(false);
        setQuestionsPanelAberto(false);
      }
      setIAPanelAberto(abrir);
    }

    function toggleMsgPanel(forcar) {
      const abrir = forcar !== undefined ? !!forcar : !msgPanelAberto;
      if (abrir) {
        setIAPanelAberto(false);
        setCodexPanelAberto(false);
        setQuestionsPanelAberto(false);
      }
      setMsgPanelAberto(abrir);
    }

    function toggleCodexPanel(forcar) {
      const abrir = forcar !== undefined ? !!forcar : !codexPanelAberto;
      if (abrir) {
        setIAPanelAberto(false);
        setMsgPanelAberto(false);
        setQuestionsPanelAberto(false);
      }
      setCodexPanelAberto(abrir);
    }

    function toggleQuestionsPanel(forcar) {
      const abrir = forcar !== undefined ? !!forcar : !perguntasPanelAberto;
      if (abrir) {
        setIAPanelAberto(false);
        setCodexPanelAberto(false);
        setMsgPanelAberto(false);
      }
      setQuestionsPanelAberto(abrir);
    }

    function toggleBlackJhonPanel(forcar) {
      toggleCodexPanel(forcar);
    }

    /* ── Anexos ── */
    function renderAnexos() {
      const el = document.getElementById('jk-ia-anexos');
      el.innerHTML = '';
      anexos.forEach((a, i) => {
        const item = document.createElement('div');
        item.className = 'jk-ia-anx-item';
        item.innerHTML = `<span title="${a.name}">${a.name}</span><button class="jk-ia-anx-del" data-idx="${i}">✕</button>`;
        item.querySelector('.jk-ia-anx-del').addEventListener('click', () => {
          anexos.splice(i, 1);
          renderAnexos();
        });
        el.appendChild(item);
      });
    }

    async function adicionarArquivos(files) {
      for (const file of Array.from(files || [])) {
        if (file.size > 5 * 1024 * 1024) continue;
        try {
          const b64 = await new Promise((res, rej) => {
            const reader = new FileReader();
            reader.onload = e => res(e.target.result.split(',')[1] || '');
            reader.onerror = rej;
            reader.readAsDataURL(file);
          });
          anexos.push({ name: file.name, mime_type: file.type || 'application/octet-stream', data_base64: b64 });
        } catch (_) {}
      }
      renderAnexos();
    }

    /* ── Contexto automático da tela ── */
    function _obterContextoTela() {
      const filters = [];
      const entityRefs = {};
      let storeMode = 'none';
      let multiStore = false;
      let explicitStore = '';
      document.querySelectorAll('select, input[type="text"], input[type="date"], input[type="search"], input[type="number"], input[type="month"], input[type="week"]')
        .forEach(el => {
          if (filters.length >= 12 || el.closest('#jk-ia-panel,#jk-codex-panel,#jk-questions-panel,#jk-msg-panel') || el.offsetParent === null) return;
          const rawValue = (el.value || '').trim();
          const value = rawValue || (el.selectedOptions && el.selectedOptions[0]?.textContent?.trim()) || '';
          if (!value) return;
          const label = (el.getAttribute('aria-label') || el.labels?.[0]?.textContent || el.placeholder || el.id || el.name || 'filtro')
            .replace(/\s+/g, ' ')
            .trim().slice(0, 120);
          const cleanValue = String(value).replace(/\s+/g, ' ').trim().slice(0, 240);
          const key = String(el.name || el.id || label).replace(/[^a-zA-Z0-9_-]+/g, '_').slice(0, 80);
          filters.push({ key, label, value: cleanValue });
          const normalizedLabel = label.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
          if (/\b(loja|conta)\b/.test(normalizedLabel)) {
            if (/^(todas?|todos?|all)$/i.test(cleanValue)) {
              storeMode = 'all';
              multiStore = true;
              explicitStore = '';
            } else {
              storeMode = 'single';
              multiStore = false;
              explicitStore = cleanValue;
              entityRefs.store = cleanValue;
            }
          }
          if (/\b(sku|codigo)\b/.test(normalizedLabel)) entityRefs.sku = cleanValue.slice(0, 100);
          if (/\b(mlb|anuncio|item)\b/.test(normalizedLabel) && /MLB\d+/i.test(cleanValue)) {
            entityRefs.item_id = (cleanValue.match(/MLB\d+/i) || [''])[0].toUpperCase();
          }
        });

      const metrics = [];
      document.querySelectorAll('.card, .kpi-card, .resumo-card').forEach(c => {
        if (metrics.length >= 8 || c.closest('#jk-ia-panel,#jk-codex-panel,#jk-questions-panel,#jk-msg-panel') || c.offsetParent === null) return;
        const t = c.querySelector('h3,h4,.card-title,.kpi-label')?.textContent?.trim();
        const v = c.querySelector('.val,.value,.kpi-val,.card-value')?.textContent?.trim();
        if (t && v) metrics.push({
          label: t.replace(/\s+/g, ' ').slice(0, 120),
          value: v.replace(/\s+/g, ' ').slice(0, 240),
        });
      });

      const dateValues = Array.from(document.querySelectorAll('input[type="date"], input[type="month"], input[type="week"]'))
        .filter(el => !el.closest('#jk-ia-panel,#jk-codex-panel,#jk-questions-panel,#jk-msg-panel') && el.offsetParent !== null)
        .map(el => (el.value || '').trim())
        .filter(Boolean);
      let selectedText = '';
      try { selectedText = String(window.getSelection?.().toString() || '').replace(/\s+/g, ' ').trim().slice(0, 600); } catch (_) {}

      return {
        schema_version: 'sidebar-turn-v2',
        surface: 'sidebar_chat',
        conversation_mode: 'quick_chat',
        conversation_id: convAtualId,
        captured_at: new Date().toISOString(),
        route: {
          title: String(document.title || '').replace(/\s+/g, ' ').trim().slice(0, 200),
          pathname: String(location.pathname || '').slice(0, 500),
          module: String(_modulo() || '').slice(0, 100),
        },
        period: { start: dateValues[0] || '', end: dateValues[1] || dateValues[0] || '' },
        filters,
        metrics,
        selection: selectedText ? { type: 'text', text: selectedText } : {},
        entity_refs: entityRefs,
        store_mode: storeMode,
        multi_store: multiStore,
        store: explicitStore,
      };
    }

    function _normalizarComandoLocal(texto) {
      return String(texto || '')
        .normalize('NFD')
        .replace(/[\u0300-\u036f]/g, '')
        .toLowerCase()
        .trim();
    }

    function _devePreencherPesquisasFavoritos(texto) {
      const comando = _normalizarComandoLocal(texto);
      if (!comando) return false;
      const modulo = _normalizarComandoLocal(_modulo());
      if (!modulo.includes('favoritos') && typeof window.JKFavoritosPreencherPesquisasIA !== 'function') return false;

      const falaDePesquisa = (
        comando.includes('pesquisa 1') ||
        comando.includes('pesquisa 2') ||
        comando.includes('pesquisas') ||
        comando.includes('pesquisa um') ||
        comando.includes('pesquisa dois') ||
        comando.includes('campo de pesquisa') ||
        comando.includes('campo pesquisa') ||
        comando.includes('campo pesquisar') ||
        comando.includes('campos de pesquisa') ||
        comando.includes('campos pesquisa') ||
        comando.includes('campos pesquisar') ||
        comando.includes('coluna de pesquisa') ||
        comando.includes('coluna pesquisa') ||
        comando.includes('coluna pesquisar') ||
        (comando.includes('pesquisar') && comando.includes('campo'))
      );
      const pedeAcao = (
        comando.includes('preencha') ||
        comando.includes('preencher') ||
        comando.includes('complete') ||
        comando.includes('completar') ||
        comando.includes('gerar') ||
        comando.includes('gere') ||
        comando.includes('salvar') ||
        comando.includes('atualizar')
      );
      return falaDePesquisa && pedeAcao;
    }

    function _extrairSkusFavoritos(texto) {
      const bruto = _normalizarComandoLocal(texto);
      const encontrados = [];
      const regex = /\bsku\s*[:#-]?\s*([a-z0-9][a-z0-9._/-]{0,40})/g;
      let match;
      while ((match = regex.exec(bruto)) !== null) {
        let sku = String(match[1] || '').replace(/[.,;:)]+$/g, '').trim();
        if (sku && !encontrados.includes(sku)) encontrados.push(sku);
      }
      return encontrados;
    }

    async function _executarAcaoLocal(pergunta) {
      if (_devePreencherPesquisasFavoritos(pergunta)) {
        const fn = window.JKFavoritosPreencherPesquisasIA;
        if (typeof fn !== 'function') {
          return {
            handled: true,
            text: 'Eu entendi o pedido, mas a tela de Favoritos ainda nao terminou de carregar a acao de preenchimento. Aguarde a lista aparecer e peca novamente.'
          };
        }

        const comando = _normalizarComandoLocal(pergunta);
        const sobrescrever = (
          comando.includes('sobrescrev') ||
          comando.includes('substitu') ||
          comando.includes('corrig') ||
          comando.includes('refaca') ||
          comando.includes('refazer')
        );
        const skus = _extrairSkusFavoritos(pergunta);
        const resultado = await fn({ origem: 'ia-sidebar', sobrescrever, skus });
        if (resultado && resultado.success) {
          const atualizadas = Number(resultado.atualizadas || 0);
          const total = Number(resultado.total || 0);
          const alvoTexto = skus.length ? ` para ${skus.map(s => `SKU ${s.toUpperCase()}`).join(', ')}` : '';
          return {
            handled: true,
            text: atualizadas
              ? `Pronto. Preenchi e salvei Pesquisa 1 e Pesquisa 2${alvoTexto} em ${atualizadas} SKU(s) (${total} analisado(s)).`
              : `Executei a IA${alvoTexto}, mas nenhum SKU recebeu valor novo. Verifique se ja havia Pesquisa 1/Pesquisa 2 preenchidas ou se o SKU esta na loja/filtro atual.`
          };
        }

        return {
          handled: true,
          text: `Nao consegui preencher as pesquisas agora: ${(resultado && resultado.error) || 'erro desconhecido'}.`
        };
      }
      return null;
    }

    /* ── Envio ao backend ── */
    async function enviar(perguntaManual) {
      const pergunta = (perguntaManual || document.getElementById('jk-ia-input')?.value || '').trim();
      const anx = [...anexos];
      if (!pergunta && !anx.length) return;

      const perguntaFinal = pergunta || 'Analise os anexos enviados.';
      addMsg('user', perguntaFinal + (anx.length ? '\n\nAnexos: ' + anx.map(a => a.name).join(', ') : ''));
      const input = document.getElementById('jk-ia-input');
      if (input) input.value = '';
      anexos = [];
      renderAnexos();

      const aguardando = addMsg('assistant', 'Pensando...', false);
      aguardando.classList.add('loading');

      const historico = mensagensAtuais.slice(-10).map(_approvalHistoricoChat).filter(Boolean);
      const contextoTela = _obterContextoTela();

      try {
        const acaoLocal = await _executarAcaoLocal(perguntaFinal);
        if (acaoLocal && acaoLocal.handled) {
          const respostaLocal = acaoLocal.text || 'Acao executada.';
          _definirTextoMsg(aguardando, respostaLocal);
          aguardando.classList.remove('loading');
          mensagensAtuais.push({ role: 'assistant', text: respostaLocal });
          await salvarMensagensAtuais();
          if (convsVisible) await renderConvsList();
          document.getElementById('jk-ia-msgs').scrollTop = 99999;
          _iaAvisarMensagemRecebida();
          return;
        }
      } catch (err) {
        const respostaLocal = `Nao consegui executar a acao na tela: ${err && err.message ? err.message : err}`;
        _definirTextoMsg(aguardando, respostaLocal);
        aguardando.classList.remove('loading');
        mensagensAtuais.push({ role: 'assistant', text: respostaLocal });
        await salvarMensagensAtuais();
        _iaAvisarMensagemRecebida();
        return;
      }

      const modelSelect = document.getElementById('jk-ia-model-sel');
      const modelEscolhido = modelSelect && modelSelect.dataset.podeEscolherModelo === 'true'
        ? modelSelect.value
        : '';
      const body = JSON.stringify({
        message: perguntaFinal,
        page: _modulo(),
        model: modelEscolhido,
        context: contextoTela,
        history: historico,
        attachments: anx,
        conversa_id: convAtualId,
        modulo: IA_GLOBAL_MODULO,
        conversa_mensagens: mensagensAtuais.slice(-MAX_MSGS).map(m => ({ role: m.role, text: m.text })),
      });

      const urls = ['/api/ia/chat'];
      if (location.hostname === '127.0.0.1' || location.hostname === 'localhost')
        urls.push('http://127.0.0.1:8012/api/ia/chat');

      let resposta = null;
      for (const url of urls) {
        try {
          const r = await window.__JK_IA_SIDEBAR_FETCH__(url, { method: 'POST', headers: _authHeaders(), body });
          const data = await r.json().catch(() => null);
          if (r.ok) {
            resposta = data?.resposta || 'Sem resposta da IA.';
            window.__JK_IA_LAST_DIAGNOSTIC__ = data?.diagnostico_ia || null;
            break;
          }
          if (r.status !== 405) break;
        } catch (_) {}
      }
      resposta = resposta || 'Não foi possível consultar a IA agora.';

      const approvalResposta = _approvalParseMensagem(resposta);
      if (approvalResposta) {
        aguardando.remove();
        _adicionarNotificacaoAprovacao(approvalResposta, { abrirPainel: true });
        return;
      }
      _definirTextoMsg(aguardando, resposta);
      aguardando.classList.remove('loading');
      mensagensAtuais.push({
        role: 'assistant',
        text: resposta,
      });
      await salvarMensagensAtuais();
      if (convsVisible) await renderConvsList();
      document.getElementById('jk-ia-msgs').scrollTop = 99999;
      _iaAvisarMensagemRecebida();
    }

    /* ── Eventos ── */
    document.getElementById('jk-ia-fab').addEventListener('click', () => toggleBlackJhonPanel());
    document.getElementById('jk-questions-fab')?.addEventListener('click', () => toggleQuestionsPanel());
    document.getElementById('jk-msg-fab').addEventListener('click', () => toggleMsgPanel());
    document.getElementById('jk-questions-close')?.addEventListener('click', () => toggleQuestionsPanel(false));
    document.getElementById('jk-questions-refresh')?.addEventListener('click', () => {
      void _questionsCarregarPendentes({ marcarVistas: true });
    });
    document.getElementById('jk-ia-btn-fechar').addEventListener('click', () => togglePanel(false));
    document.getElementById('jk-codex-new').addEventListener('click', () => { void _codexReiniciarMemoria(); });
    document.getElementById('jk-codex-history-toggle')?.addEventListener('click', () => _codexToggleHistorico());
    document.getElementById('jk-codex-history-refresh')?.addEventListener('click', () => _codexCarregarListaHistorico());
    document.getElementById('jk-codex-close').addEventListener('click', () => toggleCodexPanel(false));
    document.getElementById('jk-codex-refresh').addEventListener('click', () => _codexCarregarStatus());
    document.getElementById('jk-codex-report-settings')?.addEventListener('click', () => { void _codexAbrirConfiguracoesRelatorio(); });
    document.getElementById('jk-codex-report-settings-close')?.addEventListener('click', () => _codexFecharConfiguracoesRelatorio());
    document.getElementById('jk-codex-report-settings-cancel')?.addEventListener('click', () => _codexFecharConfiguracoesRelatorio());
    document.getElementById('jk-codex-report-settings-save')?.addEventListener('click', () => { void _codexSalvarConfiguracoesRelatorio(); });
    document.getElementById('jk-codex-report-settings-dialog')?.addEventListener('click', event => {
      if (event.target === event.currentTarget) _codexFecharConfiguracoesRelatorio();
    });
    _codexAplicarSettings();
    ['jk-codex-access', 'jk-codex-model', 'jk-codex-reasoning', 'jk-codex-speed'].forEach(id => {
      document.getElementById(id)?.addEventListener('change', () => {
        if (!_usuarioLocalEhFull()) {
          _codexAtualizarVisibilidade();
          return;
        }
        _codexPersistSettings();
        _codexAtualizarConfigTooltips();
      });
    });
    document.getElementById('jk-codex-add-toggle')?.addEventListener('click', () => {
      if (!_usuarioLocalEhFull()) return;
      const menu = document.getElementById('jk-codex-add-menu');
      if (menu) menu.hidden = !menu.hidden;
    });
    document.getElementById('jk-codex-path-toggle')?.addEventListener('click', () => {
      if (!_usuarioLocalEhFull()) return;
      const row = document.getElementById('jk-codex-path-row');
      if (row) {
        row.hidden = !row.hidden;
        if (!row.hidden) document.getElementById('jk-codex-path-input')?.focus();
      }
    });
    document.getElementById('jk-codex-goal-toggle')?.addEventListener('click', () => {
      if (!_usuarioLocalEhFull()) return;
      const row = document.getElementById('jk-codex-goal-row');
      if (row) {
        row.hidden = !row.hidden;
        if (!row.hidden) document.getElementById('jk-codex-goal-input')?.focus();
      }
    });
    document.getElementById('jk-codex-plan-toggle')?.addEventListener('click', event => {
      if (!_usuarioLocalEhFull()) return;
      const btn = event.currentTarget;
      const ativo = !btn.classList.contains('is-active');
      btn.classList.toggle('is-active', ativo);
      btn.setAttribute('aria-pressed', ativo ? 'true' : 'false');
      _codexPersistSettings();
    });
    document.getElementById('jk-codex-report')?.addEventListener('click', () => {
      if (_usuarioLocalEhFull()) _codexGerarRelatorioAssistente();
    });
    document.getElementById('jk-codex-capabilities')?.addEventListener('click', () => {
      if (_usuarioLocalEhFull()) _codexMostrarCapacidades();
    });
    document.getElementById('jk-codex-path-add')?.addEventListener('click', () => _codexAdicionarPathAtual());
    document.getElementById('jk-codex-attach-file')?.addEventListener('click', () => {
      if (_usuarioLocalEhFull()) document.getElementById('jk-codex-file-input')?.click();
    });
    document.getElementById('jk-codex-file-input')?.addEventListener('change', event => {
      void _codexUploadArquivos(event.target?.files || []).then(() => {
        if (event.target) event.target.value = '';
      });
    });
    _codexInstalarDropZone(document.getElementById('jk-codex-panel'));
    _codexInstalarDropZone(document.getElementById('jk-codex-compose'));
    document.getElementById('jk-codex-path-input')?.addEventListener('keydown', event => {
      if (event.key === 'Enter') {
        event.preventDefault();
        _codexAdicionarPathAtual();
      }
    });
    document.getElementById('jk-codex-goal-input')?.addEventListener('change', () => _codexPersistSettings());
    document.getElementById('jk-codex-readonly').addEventListener('click', () => _codexCriarTarefa());
    document.getElementById('jk-codex-voice')?.addEventListener('click', () => { void _codexVoiceToggle(); });
    document.getElementById('jk-codex-voice-cancel')?.addEventListener('click', () => _codexVoiceCancel());
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && codexVoiceState === 'recording') {
        event.preventDefault();
        _codexVoiceCancel();
      }
    });
    document.getElementById('jk-codex-approve').addEventListener('click', () => _codexAprovarAtual());
    document.getElementById('jk-codex-cancel').addEventListener('click', () => _codexCancelarAtual());
    document.getElementById('jk-codex-input').addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && codexVoiceState === 'recording') {
        event.preventDefault();
        _codexVoiceCancel();
        return;
      }
      if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        _codexCriarTarefa();
      }
    });
    document.getElementById('jk-codex-input').addEventListener('paste', event => {
      if (!_usuarioLocalEhFull()) return;
      const items = Array.from(event.clipboardData?.items || []);
      let files = Array.from(event.clipboardData?.files || []);
      if (!files.length) {
        files = items.filter(item => item.kind === 'file').map(item => item.getAsFile()).filter(Boolean);
      }
      if (!files.length) return;
      event.preventDefault();
      void _codexUploadArquivos(files);
    });
    document.getElementById('jk-msg-btn-fechar').addEventListener('click', () => toggleMsgPanel(false));
    document.getElementById('jk-msg-btn-refresh').addEventListener('click', () => _msgCarregarPainel());
    document.getElementById('jk-msg-send').addEventListener('click', () => _msgEnviarMensagem());
    document.getElementById('jk-msg-back').addEventListener('click', () => _msgVoltarLista());
    document.getElementById('jk-msg-emoji-btn').addEventListener('click', () => _msgToggleEmojiPanel());
    document.getElementById('jk-msg-img-btn').addEventListener('click', () => document.getElementById('jk-msg-img-input').click());
    document.getElementById('jk-msg-file-btn').addEventListener('click', () => document.getElementById('jk-msg-file-input').click());
    document.getElementById('jk-msg-audio-btn').addEventListener('click', () => _msgToggleGravacaoAudio());
    document.getElementById('jk-msg-video-call').addEventListener('click', () => _msgIniciarVideoChamada());
    document.getElementById('jk-msg-call-answer').addEventListener('click', () => _msgAtenderChamadaRecebida());
    document.getElementById('jk-msg-call-decline').addEventListener('click', () => { void _msgRecusarChamadaRecebida(); });
    document.getElementById('jk-msg-image-modal-copy').addEventListener('click', () => { void _msgCopiarImagemTelaCheia(); });
    document.getElementById('jk-msg-image-modal-close').addEventListener('click', () => _msgFecharImagemTelaCheia());
    document.getElementById('jk-msg-image-modal').addEventListener('click', event => {
      if (event.target === event.currentTarget || event.target?.classList?.contains('jk-msg-image-modal-stage')) {
        _msgFecharImagemTelaCheia();
      }
    });
    document.addEventListener('keydown', event => {
      const modal = document.getElementById('jk-msg-image-modal');
      if (event.key === 'Escape' && modal && !modal.hidden) _msgFecharImagemTelaCheia();
    });
    document.getElementById('jk-msg-img-input').addEventListener('change', e => _msgArquivosSelecionados(e.target.files).then(() => e.target.value = ''));
    document.getElementById('jk-msg-file-input').addEventListener('change', e => _msgArquivosSelecionados(e.target.files).then(() => e.target.value = ''));
    document.getElementById('jk-msg-text').addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey && !e.ctrlKey && !e.altKey && !e.isComposing) {
        e.preventDefault();
        _msgEnviarMensagem();
      }
    });
    document.getElementById('jk-msg-text').addEventListener('input', () => _msgMarcarDigitandoLocal());
    document.getElementById('jk-msg-text').addEventListener('paste', e => {
      const items = Array.from(e.clipboardData?.items || []);
      const files = items.filter(it => it.kind === 'file').map(it => it.getAsFile()).filter(Boolean);
      if (!files.length) return;
      e.preventDefault();
      void _msgArquivosSelecionados(files);
    });
    document.getElementById('jk-ia-btn-nova').addEventListener('click', () => { mostrarChat(); void novaConversa(); });
    document.getElementById('jk-ia-btn-historico').addEventListener('click', () => convsVisible ? mostrarChat() : mostrarConvs());
    document.getElementById('jk-ia-send').addEventListener('click', () => enviar());
    document.getElementById('jk-ia-btn-img').addEventListener('click', () => document.getElementById('jk-ia-file-img').click());
    document.getElementById('jk-ia-btn-arq').addEventListener('click', () => document.getElementById('jk-ia-file-arq').click());
    document.getElementById('jk-ia-file-img').addEventListener('change', e => adicionarArquivos(e.target.files).then(() => e.target.value = ''));
    document.getElementById('jk-ia-file-arq').addEventListener('change', e => adicionarArquivos(e.target.files).then(() => e.target.value = ''));
    document.getElementById('jk-ia-input').addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); enviar(); } });
    document.getElementById('jk-ia-input').addEventListener('paste', e => {
      const items = Array.from(e.clipboardData?.items || []);
      const imgItems = items.filter(it => it.kind === 'file' && it.type.startsWith('image/'));
      if (imgItems.length === 0) return;
      e.preventDefault();
      const files = imgItems.map(it => it.getAsFile()).filter(Boolean);
      if (files.length) adicionarArquivos(files);
    });
    // Seletor de modelo — persiste preferência no localStorage
    const _modelSelSidebar = document.getElementById('jk-ia-model-sel');
    if (_modelSelSidebar) {
      _aplicarPermissaoModeloChat(_modelSelSidebar, _usuarioLocalEhAdmin());
      if (_usuarioLocalEhAdmin()) {
        const _savedModelSidebar = localStorage.getItem('ia_model_sidebar');
        if (_savedModelSidebar) _modelSelSidebar.value = _savedModelSidebar;
      }
      _modelSelSidebar.addEventListener('change', () => {
        if (_modelSelSidebar.dataset.podeEscolherModelo === 'true') {
          localStorage.setItem('ia_model_sidebar', _modelSelSidebar.value);
        }
      });
    }
    // Fechar painel ao clicar fora
    _msgPrepararNotificacoesWindows();
    _msgMontarEmojiPanel();
    _msgRenderAnexosComposer();
    _msgAtualizarSelecao();
    setTimeout(() => { void _msgBuscarUsuariosOnline().catch(() => {}); }, 10 * 60 * 1000);
    if (_usuarioLocalEhFull()) setTimeout(() => { _perguntasIniciarMonitorGlobal(); }, 2 * 60 * 1000);
    _codexRemoverPerguntasMlDoHistoricoLocal();
    _codexRemoverAlertasAutomaticosDoHistoricoLocal();
    _questionsRenderLista();
    _codexAtualizarVisibilidade();
    const codexEstadoInicial = _codexLerEstadoPainel();
    codexInitialTaskId = String(codexEstadoInicial.task_id || '').trim();
    codexConversationGeneration = Math.max(1, Number(codexEstadoInicial.conversation_generation || 1));
    _codexSetThreadId('');
    codexHistoryVisible = false;
    document.getElementById('jk-codex-history-panel')?.classList.remove('ativo');
    document.getElementById('jk-codex-messages')?.addEventListener('scroll', () => {
      if (window.__JK_CODEX_SCROLL_SAVE_TIMER__) clearTimeout(window.__JK_CODEX_SCROLL_SAVE_TIMER__);
      window.__JK_CODEX_SCROLL_SAVE_TIMER__ = setTimeout(() => _codexSalvarEstadoPainel(), 180);
    });
    window.addEventListener('beforeunload', () => {
      if (codexPollTimer) clearTimeout(codexPollTimer);
      if (codexActionPollTimer) clearTimeout(codexActionPollTimer);
      codexFullTextCache.clear();
      _codexSalvarEstadoPainel();
      _msgPararToqueChamada();
    });

    document.addEventListener('click', e => {
      const alvo = e.target;
      const menu = alvo && alvo.closest ? alvo.closest('#jk-right-sidebar-hotspot') : null;
      const callModal = alvo && alvo.closest ? alvo.closest('#jk-msg-call-modal') : null;
      if (callModal) return;
      const iaPanel = document.getElementById('jk-ia-panel');
      const codexPanel = document.getElementById('jk-codex-panel');
      const questionsPanel = document.getElementById('jk-questions-panel');
      const msgPanel = document.getElementById('jk-msg-panel');
      if (panelAberto && iaPanel && !iaPanel.contains(alvo) && !menu) togglePanel(false);
      if (codexPanelAberto && codexPanel && !codexPanel.contains(alvo) && !menu) toggleCodexPanel(false);
      if (perguntasPanelAberto && questionsPanel && !questionsPanel.contains(alvo) && !menu) toggleQuestionsPanel(false);
      if (msgPanelAberto && msgPanel && !msgPanel.contains(alvo) && !menu) toggleMsgPanel(false);
    }, true);
  }

  if (document.readyState === 'loading')
    document.addEventListener('DOMContentLoaded', init);
  else
    init();
})();
