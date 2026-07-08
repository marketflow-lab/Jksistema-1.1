  function init() {
    // Injeta CSS
    const style = document.createElement('style');
    style.textContent = CSS;
    document.head.appendChild(style);

    // Injeta HTML
    const wrap = document.createElement('div');
    wrap.innerHTML = HTML;
    while (wrap.firstChild) document.body.appendChild(wrap.firstChild);

    // Estado
    let panelAberto = false;
    let convsVisible = false;
    let anexos = [];
    let convAtualId = null;
    let mensagensAtuais = [];
    let resizeState = null;
    let codexResizeState = null;
    let msgPanelAberto = false;
    let codexPanelAberto = false;
    let codexTaskAtual = null;
    let codexThreadId = localStorage.getItem('jk_codex_thread_id') || '';
    let codexConversationId = '';
    let codexPollTimer = null;
    let codexActionPollTimer = null;
    let codexActionRunAtual = null;
    let codexHistoryVisible = false;
    let codexHistoryTasks = [];
    let codexPollFailures = {};
    let codexRenderedFinalTasks = new Set();
    let codexPaths = [];
    let codexUploadedAttachments = [];
    let codexMessagesAtuais = [];
    let codexAssistantTimer = null;
    let codexAssistantLastReportId = '';
    let codexAssistantAutoReportRunning = false;
    let codexLazyStarted = false;
    let msgChatAberto = false;
    let msgUsuarioSelecionado = null;
    let msgRefreshTimer = null;
    let msgCarregando = false;
    let iaTemMensagemNaoVista = false;
    let msgTemMensagemNaoVista = false;
    let msgNotificacoesConhecidas = null;
    let msgNaoLidasPorUsuario = new Map();
    let msgAnexos = [];
    let msgMediaRecorder = null;
    let msgAudioChunks = [];
    let msgAudioStream = null;
    let msgTypingPollTimer = null;
    let msgTypingStopTimer = null;
    let msgTypingEnviado = false;
    let msgUsuariosCache = [];
    let msgUsuariosCacheTs = 0;
    let msgContatosAutoritativos = false;
    let msgMensagensCache = [];
    let msgHistoricoCache = new Map();
    let msgCallRinging = null;
    let msgCallRingTimer = null;
    let msgCallAudioCtx = null;
    let msgCallSeenSet = null;
    const PANEL_WIDTH_KEY = 'jk_ia_sidebar_width_px';
    const PANEL_MIN_WIDTH = 300;
    const PANEL_MAX_WIDTH = 760;
    const CODEX_PANEL_WIDTH_KEY = 'jk_codex_sidebar_width_px';
    const CODEX_PANEL_MIN_WIDTH = 330;
    const CODEX_PANEL_MAX_WIDTH = 920;
    const MSG_NOTIFICACOES_KEY = 'jk_msg_notificacoes_exibidas_v1';
    const CODEX_SETTINGS_KEY = 'jk_codex_settings_v1';
    const CODEX_HISTORY_KEY = 'jk_codex_history_v1';
    const CODEX_ACTIVE_CONVERSATION_KEY = 'jk_codex_active_conversation_id_v2';
    const CODEX_HISTORY_PREFIX = 'jk_codex_history_v2';
    const CODEX_PANEL_STATE_PREFIX = 'jk_codex_panel_state_v2';
    const CODEX_ASSISTANT_SEEN_KEY = 'jk_codex_assistant_seen_v1';
    const CODEX_ASSISTANT_REPORTED_KEY = 'jk_codex_assistant_reported_v2';
    const CODEX_HISTORY_LIMIT = 120;
    const CODEX_PROACTIVE_INTERVAL_MS = 30 * 60 * 1000;
    const CODEX_ASSISTANT_DISPLAY_NAME = 'João Pretinho';
    const CODEX_UPLOAD_MAX_COUNT = 20;
    const CODEX_UPLOAD_MAX_BYTES = 25 * 1024 * 1024;
    const CODEX_UPLOAD_TOTAL_MAX_BYTES = 100 * 1024 * 1024;
    const MSG_REFRESH_CLOSED_INTERVAL_MS = 5 * 60 * 1000;
    const MSG_REFRESH_PANEL_INTERVAL_MS = 60 * 1000;
    const MSG_REFRESH_CHAT_INTERVAL_MS = 90 * 1000;
    const MSG_REFRESH_HIDDEN_INTERVAL_MS = 10 * 60 * 1000;
    const MSG_ATTACHMENT_MAX_COUNT = 6;
    const MSG_ATTACHMENT_MAX_BYTES = 700 * 1024;
    const MSG_ATTACHMENT_TOTAL_MAX_BYTES = 900 * 1024;
    const MSG_TYPING_POLL_MS = 8000;
    const MSG_USUARIOS_CACHE_KEY = 'jk_msg_usuarios_cache_v1';
    const MSG_USUARIOS_CACHE_TTL_MS = 5 * 60 * 1000;
    const MSG_HISTORY_LIMIT = 50;
    const MSG_CALL_RING_SEEN_KEY = 'jk_msg_call_seen_v1';
    const MSG_CALL_RING_MAX_AGE_MS = 3 * 60 * 1000;
    const PERGUNTAS_APPROVALS_NOTIFY_KEY = 'jk_perguntas_aprovacoes_notificadas_v1';
    const PERGUNTAS_MONITOR_OWNER_KEY = 'jk_perguntas_monitor_owner_v1';
    const PERGUNTAS_MONITOR_INITIAL_DELAY_MS = 20000;
    const PERGUNTAS_MONITOR_INTERVAL_MS = 60000;
    const PERGUNTAS_MONITOR_STALE_MS = 90000;
    const PERGUNTAS_MONITOR_MAX_NOTIFICACOES_FECHADO = 4;
    const PERGUNTAS_MONITOR_MAX_NOTIFICACOES_ABERTO = 12;
    const PERGUNTAS_MONITOR_YIELD_MS = 40;
    const perguntasMonitorId = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    let perguntasMonitorStartTimer = null;
    let perguntasMonitorTimer = null;
    let perguntasMonitorRodando = false;
    let perguntasAprovacoesNotificadas = null;
    const MODULOS_LATERAIS = [
      {
        key: 'mercado_livre',
        icon: '&#128722;',
        label: 'Mercado Livre',
        children: [
          { key: 'analise_promo', href: 'frontend_promo.html', icon: '&#128200;', label: 'Promocao ML' },
          { key: 'renovacao_fixa', href: 'renovacao.html', icon: '&#128260;', label: 'Renovacao Fixa' },
          { key: 'anuncios_ml', href: 'anunciosml.html', icon: '&#128230;', label: 'Anuncios ML' },
          { key: 'mercado_full', href: 'full.html', icon: '&#128666;', label: 'Full' },
          { key: 'favoritos', href: 'favoritos.html', icon: '&#11088;', label: 'Favoritos ML' },
        ],
      },
      { key: 'etiquetas', href: 'frontend_etiquetas.html', icon: '&#127991;', label: 'Etiquetas' },
      {
        key: 'operacional_grupo',
        icon: '&#128736;',
        label: 'Operacional',
        children: [
          { key: 'integracao', href: 'integracoes.html', icon: '&#128279;', label: 'Integracoes' },
          { key: 'estoque', href: 'estoque.html', icon: '&#128230;', label: 'Estoque' },
          { key: 'cadastro', href: 'cadastro.html', icon: '&#129534;', label: 'Cadastro' },
        ],
      },
      { key: 'vendas', href: 'vendas.html', icon: '&#128176;', label: 'Vendas' },
      { key: 'perguntas_pos_venda', href: 'perguntas_pos_venda.html', icon: '&#128172;', label: 'Perguntas e pos venda' },
      { key: 'medias_compras', href: 'medias_compras.html', icon: '&#128202;', label: 'Medias e Pedidos' },
      { key: 'impostos', href: 'impostos.html', icon: '&#128184;', label: 'Impostos' },
      { key: 'simulador', href: 'simulador.html', icon: '&#129518;', label: 'Simulador' },
      { key: 'configuracoes', href: 'configuracoes.html', icon: '&#9881;', label: 'Configuracoes' },
      { key: 'importacoes', href: 'importacoes.html', icon: '&#128229;', label: 'Importacoes' },
      { key: 'admin_usuarios', href: 'admin_usuarios.html', icon: '&#128101;', label: 'Central de Usuarios' },
    ];
    const MODULO_ATUAL_ALIASES = {
      'frontend_promo.html': 'analise_promo',
      'promo.html': 'analise_promo',
      'renovacao.html': 'renovacao_fixa',
      'anunciosml.html': 'anuncios_ml',
      'anunciosml_campanha.html': 'anuncios_ml',
      'frontend_etiquetas.html': 'etiquetas',
      'integracoes.html': 'integracao',
      'cadastro.html': 'cadastro',
      'cadastro_incluir.html': 'cadastro',
      'cadastro_editar.html': 'cadastro',
      'cadastro_editar_item.html': 'cadastro',
      'estoque.html': 'estoque',
      'full.html': 'mercado_full',
      'vendas.html': 'vendas',
      'vendas_sku.html': 'vendas',
      'devolucoes.html': 'vendas',
      'devolucoes_sku.html': 'vendas',
      'favoritos.html': 'favoritos',
      'pesquisa_ml.html': 'favoritos',
      'produtos_sem_venda.html': 'favoritos',
      'perguntas_pos_venda.html': 'perguntas_pos_venda',
      'medias_compras.html': 'medias_compras',
      'impostos.html': 'impostos',
      'simulador.html': 'simulador',
      'configuracoes.html': 'configuracoes',
      'importacoes.html': 'importacoes',
      'importacoes_lista.html': 'importacoes',
      'importacoes_sku.html': 'importacoes',
      'admin_usuarios.html': 'admin_usuarios',
    };

    function _leftSafeJson(raw, fallback) {
      try { return JSON.parse(raw || ''); } catch (_) { return fallback; }
    }

    function _leftModuloAtualKey() {
      const arquivo = String((location.pathname.split('/').pop() || '').split('?')[0] || '').toLowerCase();
      return MODULO_ATUAL_ALIASES[arquivo] || '';
    }

    function _leftSidebarPermitidoNaTela() {
      const arquivo = String((location.pathname.split('/').pop() || '').split('?')[0] || '').toLowerCase();
      return !['', 'dashboard.html', 'frontend_index.html', 'index.html'].includes(arquivo);
    }

    function _leftModuloPermitido(mod) {
      const permissions = _leftSafeJson(localStorage.getItem('permissions') || '{}', {});
      if (!permissions || !Object.keys(permissions).length) return true;
      if (Array.isArray(mod.children)) {
        return mod.children.some(child => _leftModuloPermitido(child));
      }
      return permissions.full === true || permissions[mod.key] === true;
    }

    function _leftFiltrarModulosVisiveis(atual) {
      return MODULOS_LATERAIS.map(mod => {
        if (Array.isArray(mod.children)) {
          const filhosPermitidos = mod.children.filter(child => child.key !== atual && _leftModuloPermitido(child));
          return filhosPermitidos.length ? { ...mod, children: filhosPermitidos } : null;
        }
        if (mod.key === atual || !_leftModuloPermitido(mod)) return null;
        return mod;
      }).filter(Boolean);
    }

    function _leftRenderModulos() {
      const menu = document.getElementById('jk-left-sidebar-menu');
      if (!menu) return;
      const hotspot = document.getElementById('jk-left-sidebar-hotspot');
      if (!_leftSidebarPermitidoNaTela()) {
        menu.innerHTML = '';
        if (hotspot) hotspot.style.display = 'none';
        return;
      }
      if (hotspot) hotspot.style.display = '';
      const atual = _leftModuloAtualKey();
      const modulos = _leftFiltrarModulosVisiveis(atual);
      menu.innerHTML = '';
      const aplicarZoomLink = (link, icon, glyph) => {
        const ativarZoom = () => {
          icon.style.setProperty('transform', 'translateX(8px) scale(1.7)', 'important');
          icon.style.setProperty('border-color', 'rgba(255,255,255,.82)', 'important');
          icon.style.setProperty('box-shadow', '0 0 0 5px rgba(120,227,212,.22),0 10px 28px rgba(19,196,160,.58)', 'important');
          glyph.style.setProperty('animation', 'jkLeftModuleGlyphSpin .52s linear infinite', 'important');
        };
        const removerZoom = () => {
          icon.style.removeProperty('transform');
          icon.style.removeProperty('border-color');
          icon.style.removeProperty('box-shadow');
          glyph.style.removeProperty('animation');
        };
        link.addEventListener('mouseenter', ativarZoom);
        link.addEventListener('mouseleave', removerZoom);
        link.addEventListener('focus', ativarZoom);
        link.addEventListener('blur', removerZoom);
        icon.addEventListener('mouseenter', ativarZoom);
        icon.addEventListener('mouseleave', removerZoom);
      };
      const criarLinkModulo = (mod, options = {}) => {
        const isButton = options.button === true;
        const link = document.createElement(isButton ? 'button' : 'a');
        link.className = options.extraClass ? `jk-left-module-link ${options.extraClass}` : 'jk-left-module-link';
        if (isButton) {
          link.type = 'button';
          link.setAttribute('aria-expanded', 'false');
        } else {
          link.href = mod.href;
        }
        link.title = mod.label;
        link.setAttribute('aria-label', options.group ? `Abrir submenu ${mod.label}` : `Abrir modulo ${mod.label}`);
        if (mod.key === atual) link.classList.add('modulo-atual');

        const icon = document.createElement('span');
        icon.className = 'jk-left-module-icon';
        icon.setAttribute('aria-hidden', 'true');
        const glyph = document.createElement('span');
        glyph.className = 'jk-left-module-glyph';
        glyph.innerHTML = mod.icon;
        icon.appendChild(glyph);

        const label = document.createElement('span');
        label.className = 'jk-left-module-label';
        label.textContent = mod.label;

        aplicarZoomLink(link, icon, glyph);

        link.appendChild(icon);
        link.appendChild(label);
        return link;
      };
      const criarLinkSubmodulo = (mod) => {
        const link = document.createElement('a');
        link.className = 'jk-left-submodule-link';
        link.href = mod.href;
        link.title = mod.label;
        link.setAttribute('aria-label', `Abrir modulo ${mod.label}`);
        link.innerHTML = `<span class="jk-left-submodule-icon" aria-hidden="true">${mod.icon}</span><span>${mod.label}</span>`;
        return link;
      };
      modulos.forEach(mod => {
        if (Array.isArray(mod.children)) {
          const group = document.createElement('div');
          group.className = 'jk-left-module-group';
          const trigger = criarLinkModulo(mod, { button: true, group: true, extraClass: 'jk-left-module-group-trigger' });
          const submenu = document.createElement('div');
          submenu.className = 'jk-left-module-submenu';
          submenu.setAttribute('role', 'menu');
          submenu.setAttribute('aria-label', mod.label);
          mod.children.forEach(child => submenu.appendChild(criarLinkSubmodulo(child)));
          trigger.addEventListener('click', (event) => {
            event.preventDefault();
            const aberto = !group.classList.contains('submenu-aberto');
            group.classList.toggle('submenu-aberto', aberto);
            trigger.setAttribute('aria-expanded', aberto ? 'true' : 'false');
          });
          group.addEventListener('mouseleave', () => {
            group.classList.remove('submenu-aberto');
            trigger.setAttribute('aria-expanded', 'false');
          });
          group.appendChild(trigger);
          group.appendChild(submenu);
          menu.appendChild(group);
          return;
        }
        menu.appendChild(criarLinkModulo(mod));
      });
      _jkAplicarZoomSidebarEsquerdoExistente();
    }

    function clampPanelWidth(width) {
      const viewportMax = Math.max(PANEL_MIN_WIDTH, (window.innerWidth || document.documentElement.clientWidth || 0) - 32);
      const max = Math.max(PANEL_MIN_WIDTH, Math.min(PANEL_MAX_WIDTH, viewportMax));
      const valor = Number(width);
      if (!Number.isFinite(valor)) return 360;
      return Math.max(PANEL_MIN_WIDTH, Math.min(max, Math.round(valor)));
    }

    function setPanelWidth(width, salvar = false) {
      const panel = document.getElementById('jk-ia-panel');
      if (!panel) return;
      const finalWidth = clampPanelWidth(width);
      panel.style.setProperty('--jk-ia-panel-width', `${finalWidth}px`);
      document.getElementById('jk-ia-resizer')?.setAttribute('aria-valuenow', String(finalWidth));
      if (salvar) {
        try { localStorage.setItem(PANEL_WIDTH_KEY, String(finalWidth)); } catch (_) {}
      }
    }

    function startPanelResize(event) {
      const panel = document.getElementById('jk-ia-panel');
      if (!panel) return;
      const point = event.touches?.[0] || event;
      resizeState = {
        startX: point.clientX,
        startWidth: panel.getBoundingClientRect().width,
      };
      document.body.classList.add('jk-ia-resizing');
      document.addEventListener('mousemove', movePanelResize);
      document.addEventListener('mouseup', stopPanelResize);
      document.addEventListener('touchmove', movePanelResize, { passive: false });
      document.addEventListener('touchend', stopPanelResize);
      document.addEventListener('touchcancel', stopPanelResize);
      event.preventDefault();
    }

    function movePanelResize(event) {
      if (!resizeState) return;
      const point = event.touches?.[0] || event;
      setPanelWidth(resizeState.startWidth + (resizeState.startX - point.clientX));
      event.preventDefault();
    }

    function stopPanelResize() {
      if (!resizeState) return;
      resizeState = null;
      document.body.classList.remove('jk-ia-resizing');
      document.removeEventListener('mousemove', movePanelResize);
      document.removeEventListener('mouseup', stopPanelResize);
      document.removeEventListener('touchmove', movePanelResize);
      document.removeEventListener('touchend', stopPanelResize);
      document.removeEventListener('touchcancel', stopPanelResize);
      setPanelWidth(document.getElementById('jk-ia-panel')?.getBoundingClientRect().width || 360, true);
    }

    function setupPanelResize() {
      const resizer = document.getElementById('jk-ia-resizer');
      if (!resizer) return;
      let savedWidth = 360;
      try { savedWidth = Number(localStorage.getItem(PANEL_WIDTH_KEY)) || 360; } catch (_) {}
      setPanelWidth(savedWidth);
      resizer.setAttribute('aria-valuemin', String(PANEL_MIN_WIDTH));
      resizer.setAttribute('aria-valuemax', String(PANEL_MAX_WIDTH));
      resizer.addEventListener('mousedown', startPanelResize);
      resizer.addEventListener('touchstart', startPanelResize, { passive: false });
      resizer.addEventListener('keydown', (event) => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        const current = document.getElementById('jk-ia-panel')?.getBoundingClientRect().width || 360;
        let next = current;
        if (event.key === 'ArrowLeft') next = current + 24;
        if (event.key === 'ArrowRight') next = current - 24;
        if (event.key === 'Home') next = PANEL_MIN_WIDTH;
        if (event.key === 'End') next = PANEL_MAX_WIDTH;
        setPanelWidth(next, true);
        event.preventDefault();
      });
      window.addEventListener('resize', () => setPanelWidth(document.getElementById('jk-ia-panel')?.getBoundingClientRect().width || 360, true));
    }

    function clampCodexPanelWidth(width) {
      const viewportMax = Math.max(CODEX_PANEL_MIN_WIDTH, (window.innerWidth || document.documentElement.clientWidth || 0) - 32);
      const max = Math.max(CODEX_PANEL_MIN_WIDTH, Math.min(CODEX_PANEL_MAX_WIDTH, viewportMax));
      const valor = Number(width);
      if (!Number.isFinite(valor)) return 410;
      return Math.max(CODEX_PANEL_MIN_WIDTH, Math.min(max, Math.round(valor)));
    }

    function setCodexPanelWidth(width, salvar = false) {
      const panel = document.getElementById('jk-codex-panel');
      if (!panel) return;
      const finalWidth = clampCodexPanelWidth(width);
      panel.style.setProperty('--jk-codex-panel-width', `${finalWidth}px`);
      document.getElementById('jk-codex-resizer')?.setAttribute('aria-valuenow', String(finalWidth));
      if (salvar) {
        try { localStorage.setItem(CODEX_PANEL_WIDTH_KEY, String(finalWidth)); } catch (_) {}
      }
    }

    function startCodexPanelResize(event) {
      const panel = document.getElementById('jk-codex-panel');
      if (!panel) return;
      const point = event.touches?.[0] || event;
      codexResizeState = {
        startX: point.clientX,
        startWidth: panel.getBoundingClientRect().width,
      };
      document.body.classList.add('jk-codex-resizing');
      document.addEventListener('mousemove', moveCodexPanelResize);
      document.addEventListener('mouseup', stopCodexPanelResize);
      document.addEventListener('touchmove', moveCodexPanelResize, { passive: false });
      document.addEventListener('touchend', stopCodexPanelResize);
      document.addEventListener('touchcancel', stopCodexPanelResize);
      event.preventDefault();
    }

    function moveCodexPanelResize(event) {
      if (!codexResizeState) return;
      const point = event.touches?.[0] || event;
      setCodexPanelWidth(codexResizeState.startWidth + (codexResizeState.startX - point.clientX));
      event.preventDefault();
    }

    function stopCodexPanelResize() {
      if (!codexResizeState) return;
      codexResizeState = null;
      document.body.classList.remove('jk-codex-resizing');
      document.removeEventListener('mousemove', moveCodexPanelResize);
      document.removeEventListener('mouseup', stopCodexPanelResize);
      document.removeEventListener('touchmove', moveCodexPanelResize);
      document.removeEventListener('touchend', stopCodexPanelResize);
      document.removeEventListener('touchcancel', stopCodexPanelResize);
      setCodexPanelWidth(document.getElementById('jk-codex-panel')?.getBoundingClientRect().width || 410, true);
    }

    function setupCodexPanelResize() {
      const resizer = document.getElementById('jk-codex-resizer');
      if (!resizer) return;
      let savedWidth = 410;
      try { savedWidth = Number(localStorage.getItem(CODEX_PANEL_WIDTH_KEY)) || 410; } catch (_) {}
      setCodexPanelWidth(savedWidth);
      resizer.setAttribute('aria-valuemin', String(CODEX_PANEL_MIN_WIDTH));
      resizer.setAttribute('aria-valuemax', String(CODEX_PANEL_MAX_WIDTH));
      resizer.addEventListener('mousedown', startCodexPanelResize);
      resizer.addEventListener('touchstart', startCodexPanelResize, { passive: false });
      resizer.addEventListener('keydown', (event) => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        const current = document.getElementById('jk-codex-panel')?.getBoundingClientRect().width || 410;
        let next = current;
        if (event.key === 'ArrowLeft') next = current + 24;
        if (event.key === 'ArrowRight') next = current - 24;
        if (event.key === 'Home') next = CODEX_PANEL_MIN_WIDTH;
        if (event.key === 'End') next = CODEX_PANEL_MAX_WIDTH;
        setCodexPanelWidth(next, true);
        event.preventDefault();
      });
      window.addEventListener('resize', () => setCodexPanelWidth(document.getElementById('jk-codex-panel')?.getBoundingClientRect().width || 410, true));
    }

    _leftRenderModulos();
    setupPanelResize();
    setupCodexPanelResize();

    function _msgUserData() {
      try { return JSON.parse(localStorage.getItem('user_data') || '{}') || {}; }
      catch (_) { return {}; }
    }

    function _msgUsernameAtual() {
      const data = _msgUserData();
      return String(data.username || data.user || data.email || '').trim().toLowerCase();
    }

    function _msgSetStatus(texto, erro = false) {
      const el = document.getElementById('jk-msg-status');
      if (!el) return;
      el.textContent = texto || '';
      el.style.color = erro ? '#ffd1d1' : '#9ee8df';
    }

    function _sidebarAtualizarAlertas() {
      const iaAlerta = iaTemMensagemNaoVista && !panelAberto;
      const msgAlerta = msgTemMensagemNaoVista && !msgPanelAberto;
      document.getElementById('jk-ia-fab')?.classList.toggle('piscando', iaAlerta);
      document.getElementById('jk-msg-fab')?.classList.toggle('piscando', msgAlerta);
      document.getElementById('jk-right-sidebar-hotspot')?.classList.toggle('tem-alerta', iaAlerta || msgAlerta);
    }

    function _iaAvisarMensagemRecebida() {
      if (!panelAberto) iaTemMensagemNaoVista = true;
      _sidebarAtualizarAlertas();
    }

    function _codexReadStoredSettings() {
      try {
        const data = JSON.parse(localStorage.getItem(CODEX_SETTINGS_KEY) || '{}') || {};
        return data && typeof data === 'object' ? data : {};
      } catch (_) {
        return {};
      }
    }

    function _codexSetSelectValue(id, value) {
      const el = document.getElementById(id);
      if (!el) return;
      const val = String(value || '');
      const hasOption = Array.from(el.options || []).some(opt => opt.value === val);
      if (hasOption) el.value = val;
      _codexAtualizarConfigTooltips();
    }

    function _codexAtualizarConfigTooltips() {
      const items = [
        ['jk-codex-access', 'Acesso'],
        ['jk-codex-model', 'Modelo'],
        ['jk-codex-reasoning', 'Raciocinio'],
        ['jk-codex-speed', 'Velocidade'],
      ];
      items.forEach(([id, label]) => {
        const select = document.getElementById(id);
        if (!select) return;
        const option = select.options && select.options[select.selectedIndex];
        const value = option ? option.textContent.trim() : '';
        const title = value ? `${label}: ${value}` : label;
        select.title = title;
        const wrapper = select.closest('.jk-codex-select-wrap');
        if (wrapper) wrapper.title = title;
      });
    }

    function _codexAtualizarPathChips() {
      const box = document.getElementById('jk-codex-path-chips');
      if (!box) return;
      box.innerHTML = '';
      codexPaths.forEach((path, idx) => {
        const chip = document.createElement('span');
        chip.className = 'jk-codex-chip';
        const label = document.createElement('span');
        label.textContent = path;
        label.title = path;
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.textContent = 'x';
        btn.title = 'Remover';
        btn.addEventListener('click', () => {
          codexPaths.splice(idx, 1);
          _codexAtualizarPathChips();
          _codexPersistSettings();
        });
        chip.appendChild(label);
        chip.appendChild(btn);
        box.appendChild(chip);
      });
    }

    function _codexFormatBytes(bytes) {
      const value = Number(bytes || 0);
      if (!Number.isFinite(value) || value <= 0) return '0 B';
      const units = ['B', 'KB', 'MB', 'GB'];
      let size = value;
      let idx = 0;
      while (size >= 1024 && idx < units.length - 1) {
        size /= 1024;
        idx += 1;
      }
      return `${size >= 10 || idx === 0 ? size.toFixed(0) : size.toFixed(1)} ${units[idx]}`;
    }

    function _codexTotalAttachmentBytes() {
      return codexUploadedAttachments
        .filter(item => item && item.status !== 'error')
        .reduce((acc, item) => acc + Number(item && item.size || 0), 0);
    }

    function _codexPasteTimestamp() {
      const d = new Date();
      const pad = n => String(n).padStart(2, '0');
      return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
    }

    function _codexNomeArquivoUpload(file, index = 0) {
      const raw = String(file && file.name || '').trim();
      if (raw) return raw;
      const mime = String(file && file.type || '').toLowerCase();
      let ext = 'bin';
      if (mime.includes('png')) ext = 'png';
      else if (mime.includes('jpeg') || mime.includes('jpg')) ext = 'jpg';
      else if (mime.includes('webp')) ext = 'webp';
      else if (mime.includes('gif')) ext = 'gif';
      else if (mime.includes('bmp')) ext = 'bmp';
      else if (mime.includes('svg')) ext = 'svg';
      return `imagem-colada-${_codexPasteTimestamp()}${index ? '-' + index : ''}.${ext}`;
    }

    function _codexRenderAttachmentChips() {
      const box = document.getElementById('jk-codex-attachment-chips');
      if (!box) return;
      box.innerHTML = '';
      codexUploadedAttachments.forEach((anexo, idx) => {
        const chip = document.createElement('span');
        chip.className = 'jk-codex-chip';
        if (anexo.status === 'uploading') chip.classList.add('uploading');
        if (anexo.status === 'error') chip.classList.add('error');
        const label = document.createElement('span');
        const name = String(anexo.name || 'arquivo');
        const meta = anexo.status === 'uploading'
          ? 'enviando'
          : anexo.status === 'error'
            ? String(anexo.error || 'erro')
            : _codexFormatBytes(anexo.size);
        label.textContent = `${name} (${meta})`;
        label.title = anexo.path || name;
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.textContent = 'x';
        btn.title = 'Remover anexo';
        btn.addEventListener('click', () => {
          codexUploadedAttachments.splice(idx, 1);
          _codexRenderAttachmentChips();
        });
        chip.appendChild(label);
        chip.appendChild(btn);
        box.appendChild(chip);
      });
    }

    function _codexAttachmentPaths() {
      return codexUploadedAttachments
        .filter(item => item && item.status !== 'uploading' && item.status !== 'error' && item.path)
        .map(item => String(item.path || '').trim())
        .filter(Boolean);
    }

    function _codexPathsParaTarefa(basePaths = []) {
      const result = [];
      [...(Array.isArray(basePaths) ? basePaths : []), ..._codexAttachmentPaths()].forEach(path => {
        const text = String(path || '').trim();
        if (text && !result.includes(text)) result.push(text);
      });
      return result.slice(0, 80);
    }

    function _codexTemUploadPendente() {
      return codexUploadedAttachments.some(item => item && item.status === 'uploading');
    }

    async function _codexUploadArquivos(files) {
      const lista = Array.from(files || []).filter(Boolean);
      if (!lista.length) return;
      const ativos = codexUploadedAttachments.filter(item => item && item.status !== 'error').length;
      if (ativos + lista.length > CODEX_UPLOAD_MAX_COUNT) {
        _codexSetStatus(`Limite de ${CODEX_UPLOAD_MAX_COUNT} arquivos por tarefa.`, true);
        return;
      }
      const invalidos = lista.filter(file => Number(file.size || 0) > CODEX_UPLOAD_MAX_BYTES);
      if (invalidos.length) {
        _codexSetStatus(`Arquivo acima de 25 MB: ${_codexNomeArquivoUpload(invalidos[0])}`, true);
        return;
      }
      const total = lista.reduce((acc, file) => acc + Number(file.size || 0), _codexTotalAttachmentBytes());
      if (total > CODEX_UPLOAD_TOTAL_MAX_BYTES) {
        _codexSetStatus('Limite total de 100 MB por tarefa excedido.', true);
        return;
      }
      const pendentes = lista.map((file, idx) => ({
        id: `pending-${Date.now()}-${idx}-${Math.random().toString(16).slice(2)}`,
        name: _codexNomeArquivoUpload(file, idx),
        size: Number(file.size || 0),
        mime_type: file.type || 'application/octet-stream',
        status: 'uploading',
      }));
      codexUploadedAttachments.push(...pendentes);
      _codexRenderAttachmentChips();
      _codexSetStatus(`Enviando ${lista.length} arquivo(s) para o ${CODEX_ASSISTANT_DISPLAY_NAME}...`);
      try {
        const form = new FormData();
        form.append('conversation_id', _codexGetActiveConversationId(true));
        lista.forEach((file, idx) => {
          form.append('files', file, pendentes[idx].name);
        });
        const data = await _codexFetchJson('/api/admin/codex/attachments', {
          method: 'POST',
          body: form,
        });
        const enviados = Array.isArray(data && data.attachments) ? data.attachments : [];
        const pendingIds = new Set(pendentes.map(item => item.id));
        codexUploadedAttachments = codexUploadedAttachments.filter(item => !pendingIds.has(item.id));
        codexUploadedAttachments.push(...enviados.map(item => ({ ...item, status: 'ok' })));
        _codexRenderAttachmentChips();
        _codexSetStatus(`${enviados.length || lista.length} arquivo(s) anexado(s).`);
      } catch (err) {
        const message = _codexErroCurto(err, 'Falha ao enviar anexo.');
        pendentes.forEach(item => {
          item.status = 'error';
          item.error = message;
        });
        _codexRenderAttachmentChips();
        _codexSetStatus(message, true);
      }
    }

    function _codexEventoTemArquivos(event) {
      const types = Array.from(event && event.dataTransfer && event.dataTransfer.types || []);
      return types.includes('Files');
    }

    function _codexInstalarDropZone(el) {
      if (!el || el.dataset.codexDropzone === '1') return;
      el.dataset.codexDropzone = '1';
      ['dragenter', 'dragover'].forEach(type => {
        el.addEventListener(type, event => {
          if (!_codexEventoTemArquivos(event)) return;
          event.preventDefault();
          event.stopPropagation();
          document.getElementById('jk-codex-compose')?.classList.add('is-dragover');
        });
      });
      el.addEventListener('dragleave', event => {
        if (!el.contains(event.relatedTarget)) {
          document.getElementById('jk-codex-compose')?.classList.remove('is-dragover');
        }
      });
      el.addEventListener('drop', event => {
        if (!_codexEventoTemArquivos(event)) return;
        event.preventDefault();
        event.stopPropagation();
        document.getElementById('jk-codex-compose')?.classList.remove('is-dragover');
        void _codexUploadArquivos(event.dataTransfer?.files || []);
      });
    }

    function _codexAplicarSettings() {
      const saved = _codexReadStoredSettings();
      _codexSetSelectValue('jk-codex-access', saved.access || 'read_only');
      _codexSetSelectValue('jk-codex-model', saved.model || 'gpt-5.5');
      _codexSetSelectValue('jk-codex-reasoning', saved.reasoning_effort || 'xhigh');
      _codexSetSelectValue('jk-codex-speed', saved.speed || 'standard');
      codexPaths = Array.isArray(saved.paths) ? saved.paths.map(String).filter(Boolean).slice(0, 20) : [];
      const goal = document.getElementById('jk-codex-goal-input');
      if (goal) goal.value = String(saved.goal || '');
      const plan = document.getElementById('jk-codex-plan-toggle');
      if (plan) {
        const ativo = saved.planning_mode === true;
        plan.classList.toggle('is-active', ativo);
        plan.setAttribute('aria-pressed', ativo ? 'true' : 'false');
      }
      _codexAtualizarPathChips();
    }

    function _codexAccessToRuntime(accessValue) {
      const access = String(accessValue || 'read_only');
      if (access === 'full_access') return { access, sandbox: 'full_access', approval_mode: 'full_access' };
      if (access === 'auto') return { access, sandbox: 'workspace_write', approval_mode: 'auto' };
      if (access === 'request') return { access, sandbox: 'workspace_write', approval_mode: 'request' };
      return { access: 'read_only', sandbox: 'read_only', approval_mode: 'read_only' };
    }

    function _codexCollectSettings(forcedAccess = '') {
      const accessEl = document.getElementById('jk-codex-access');
      const access = forcedAccess || accessEl?.value || 'read_only';
      const runtime = _codexAccessToRuntime(access);
      const speed = document.getElementById('jk-codex-speed')?.value || 'standard';
      return {
        access: runtime.access,
        sandbox: runtime.sandbox,
        approval_mode: runtime.approval_mode,
        model: document.getElementById('jk-codex-model')?.value || 'gpt-5.5',
        reasoning_effort: document.getElementById('jk-codex-reasoning')?.value || 'xhigh',
        speed,
        service_tier: speed === 'fast' ? 'priority' : '',
        goal: String(document.getElementById('jk-codex-goal-input')?.value || '').trim(),
        planning_mode: document.getElementById('jk-codex-plan-toggle')?.classList.contains('is-active') === true,
        paths: codexPaths.slice(0, 20),
      };
    }

    function _codexPersistSettings() {
      try {
        const settings = _codexCollectSettings();
        localStorage.setItem(CODEX_SETTINGS_KEY, JSON.stringify({
          access: settings.access,
          model: settings.model,
          reasoning_effort: settings.reasoning_effort,
          speed: settings.speed,
          goal: settings.goal,
          planning_mode: settings.planning_mode,
          paths: settings.paths,
        }));
      } catch (_) {}
    }

    function _codexAdicionarPathAtual() {
      const input = document.getElementById('jk-codex-path-input');
      const path = String(input?.value || '').trim();
      if (!path) return;
      if (!codexPaths.includes(path)) codexPaths.push(path);
      if (input) input.value = '';
      _codexAtualizarPathChips();
      _codexPersistSettings();
    }

    function _codexElementoIgnoradoTela(el) {
      return !!(el && el.closest && el.closest(
        '#jk-codex-panel,#jk-ia-panel,#jk-msg-panel,#jk-right-sidebar-hotspot,#jk-left-sidebar-hotspot,script,style,noscript,template'
      ));
    }

    function _codexElementoVisivelTela(el) {
      if (!el || _codexElementoIgnoradoTela(el)) return false;
      try {
        const style = window.getComputedStyle(el);
        if (!style || style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity || 1) === 0) return false;
        const rect = el.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
      } catch (_) {
        return false;
      }
    }

    function _codexTextoLimpoTela(texto, limit = 240) {
      const clean = String(texto || '').replace(/\s+/g, ' ').trim();
      return clean.length > limit ? clean.slice(0, limit) : clean;
    }

    function _codexLabelControleTela(el) {
      if (!el) return '';
      return _codexTextoLimpoTela(
        el.getAttribute('aria-label') ||
        el.labels?.[0]?.textContent ||
        el.closest('label')?.textContent ||
        el.placeholder ||
        el.name ||
        el.id ||
        el.textContent ||
        ''
      );
    }

    function _codexValorControleTela(el) {
      if (!el) return '';
      const tag = String(el.tagName || '').toLowerCase();
      const type = String(el.getAttribute('type') || '').toLowerCase();
      const label = `${el.id || ''} ${el.name || ''} ${_codexLabelControleTela(el)}`;
      const sensitive = /senha|password|token|secret|segredo|chave|api[_-]?key/i.test(label);
      if (sensitive) return el.value ? '[preenchido]' : '';
      if (tag === 'select') {
        return _codexTextoLimpoTela(el.selectedOptions?.[0]?.textContent || el.value || '', 160);
      }
      if (tag === 'textarea' || tag === 'input') {
        if (type === 'password' || type === 'hidden' || type === 'file') return '';
        return _codexTextoLimpoTela(el.value || '', 220);
      }
      return '';
    }

    function _codexControlesVisiveisTela() {
      const controles = [];
      document.querySelectorAll('button,a,input,select,textarea,[role="button"],[aria-label]').forEach(el => {
        if (controles.length >= 60 || !_codexElementoVisivelTela(el)) return;
        const tag = String(el.tagName || '').toLowerCase();
        const label = _codexLabelControleTela(el);
        const value = _codexValorControleTela(el);
        const role = el.getAttribute('role') || tag;
        if (!label && !value) return;
        controles.push({
          role,
          label,
          value,
          href: tag === 'a' ? String(el.getAttribute('href') || '').slice(0, 240) : '',
        });
      });
      return controles;
    }

    function _codexTextoVisivelTela(limit = 6000) {
      const chunks = [];
      const seen = new Set();
      const selectors = [
        'h1,h2,h3,h4,h5,h6',
        'p,label,button,a,summary',
        'th,td,li',
        '[role="heading"],[aria-label]',
        'input,select,textarea',
      ].join(',');
      document.querySelectorAll(selectors).forEach(el => {
        if (chunks.join('\n').length >= limit || !_codexElementoVisivelTela(el)) return;
        const tag = String(el.tagName || '').toLowerCase();
        let text = '';
        if (tag === 'input' || tag === 'select' || tag === 'textarea') {
          const label = _codexLabelControleTela(el);
          const value = _codexValorControleTela(el);
          text = [label, value].filter(Boolean).join(': ');
        } else {
          text = _codexTextoLimpoTela(el.innerText || el.textContent || el.getAttribute('aria-label') || '', 320);
        }
        if (!text || seen.has(text)) return;
        seen.add(text);
        chunks.push(text);
      });
      return chunks.join('\n').slice(0, limit);
    }

    function _codexObterContextoTelaAtual() {
      let base = {};
      try {
        base = _obterContextoTela();
      } catch (_) {
        base = {};
      }
      let selection = '';
      try {
        selection = _codexTextoLimpoTela(window.getSelection?.().toString() || '', 1500);
      } catch (_) {
        selection = '';
      }
      return {
        ...base,
        title: document.title,
        url: location.pathname,
        url_completa: location.href,
        pathname: location.pathname,
        modulo_atual: _modulo(),
        visible_text: _codexTextoVisivelTela(6000),
        controls: _codexControlesVisiveisTela(),
        selection,
        viewport: {
          width: Math.round(window.innerWidth || 0),
          height: Math.round(window.innerHeight || 0),
          scroll_x: Math.round(window.scrollX || 0),
          scroll_y: Math.round(window.scrollY || 0),
        },
      };
    }

    function _codexApiUrls(path) {
      const p = String(path || '');
      const urls = [];
      if (!p.startsWith('/')) return urls;
      const addBase = (base) => {
        const clean = String(base || '').trim().replace(/\/+$/g, '');
        if (clean) urls.push(clean + p);
      };
      if (location.protocol === 'http:' || location.protocol === 'https:') {
        urls.push(p);
        addBase(location.origin);
      }
      [
        window.JK_API_BASE,
        window.JK_BACKEND_BASE,
        localStorage.getItem('jk_api_base'),
        localStorage.getItem('jk_backend_base'),
        'http://127.0.0.1:8001',
        'http://localhost:8001',
        'http://127.0.0.1:8012',
        'http://localhost:8012',
      ].forEach(addBase);
      return _uniqueList(urls);
    }

    function _codexTextoVisual(texto) {
      return String(texto || '')
        .replace(/Codex interno/g, CODEX_ASSISTANT_DISPLAY_NAME)
        .replace(/Codex Console/g, CODEX_ASSISTANT_DISPLAY_NAME)
        .replace(/codex_assistant/g, 'joao_pretinho')
        .replace(/\bCodex\b/g, CODEX_ASSISTANT_DISPLAY_NAME);
    }

    function _codexSetStatus(texto, erro = false) {
      const el = document.getElementById('jk-codex-status');
      if (!el) return;
      el.textContent = _codexTextoVisual(texto) || '';
      el.style.color = erro ? '#ffd1d1' : '#c7d2fe';
    }

    function _codexErroCurto(err, fallback = 'Falha no Codex.') {
      const msg = err && err.message ? String(err.message) : '';
      if (/Nao consegui acessar o backend do Codex|Failed to fetch|Load failed|NetworkError/i.test(msg)) {
        return 'Nao consegui acessar o backend local do Codex. Verifique se o app local esta aberto na porta 8001.';
      }
      return msg || fallback;
    }

    function _codexFmtNumero(valor) {
      const n = Number(valor || 0);
      if (!Number.isFinite(n) || n <= 0) return '';
      try {
        return new Intl.NumberFormat('pt-BR').format(Math.round(n));
      } catch (_) {
        return String(Math.round(n));
      }
    }

    function _codexNumero(...valores) {
      for (const valor of valores) {
        const n = Number(valor || 0);
        if (Number.isFinite(n) && n > 0) return n;
      }
      return 0;
    }

    function _codexContextoTexto(stats) {
      const dados = stats && typeof stats === 'object' ? stats : {};
      const estimado = _codexNumero(dados.estimated_input_tokens, dados.estimated_context_tokens);
      const screenChars = _codexNumero(dados.screen_context_chars);
      const promptChars = _codexNumero(dados.prompt_chars);
      const historyChars = _codexNumero(dados.history_chars);
      const controls = _codexNumero(dados.controls_count);
      const rows = _codexNumero(dados.table_rows_count);
      const main = estimado
        ? `${_codexFmtNumero(estimado)} tokens estimados`
        : (screenChars ? `${_codexFmtNumero(screenChars)} caracteres` : 'Aguardando contexto');
      const detalhes = [];
      if (promptChars) detalhes.push(`${_codexFmtNumero(promptChars)} prompt`);
      if (screenChars) detalhes.push(`${_codexFmtNumero(screenChars)} tela`);
      if (historyChars) detalhes.push(`${_codexFmtNumero(historyChars)} historico`);
      if (controls) detalhes.push(`${_codexFmtNumero(controls)} controles`);
      if (rows) detalhes.push(`${_codexFmtNumero(rows)} linhas`);
      return { main, detail: detalhes.join(' | ') || 'Sem contexto extra detectado.' };
    }

    function _codexTokensTexto(usage) {
      const dados = usage && typeof usage === 'object' ? usage : {};
      const total = dados.total && typeof dados.total === 'object' ? dados.total : {};
      const last = dados.last && typeof dados.last === 'object' ? dados.last : {};
      const totalTokens = _codexNumero(total.total_tokens, dados.total_tokens, last.total_tokens);
      const inputTokens = _codexNumero(total.input_tokens, dados.input_tokens, last.input_tokens);
      const outputTokens = _codexNumero(total.output_tokens, dados.output_tokens, last.output_tokens);
      const reasoningTokens = _codexNumero(total.reasoning_output_tokens, dados.reasoning_output_tokens, last.reasoning_output_tokens);
      const lastInput = _codexNumero(last.input_tokens);
      const modelWindow = _codexNumero(dados.model_context_window, dados.context_window);
      const main = totalTokens ? `${_codexFmtNumero(totalTokens)} tokens usados` : 'Aguardando tokens reais';
      const detalhes = [];
      if (inputTokens) detalhes.push(`${_codexFmtNumero(inputTokens)} entrada`);
      if (outputTokens) detalhes.push(`${_codexFmtNumero(outputTokens)} saida`);
      if (reasoningTokens) detalhes.push(`${_codexFmtNumero(reasoningTokens)} raciocinio`);
      if (modelWindow) {
        const janelaBase = lastInput || inputTokens;
        const pct = janelaBase ? Math.ceil((janelaBase / modelWindow) * 100) : 0;
        detalhes.push(`janela ${_codexFmtNumero(modelWindow)}${pct ? ` (${pct}%)` : ''}`);
      }
      return { main, detail: detalhes.join(' | ') || 'O uso real aparece durante a execucao.' };
    }

    function _codexUsoContextoPercentual(stats, usage) {
      const dados = stats && typeof stats === 'object' ? stats : {};
      const uso = usage && typeof usage === 'object' ? usage : {};
      const total = uso.total && typeof uso.total === 'object' ? uso.total : {};
      const last = uso.last && typeof uso.last === 'object' ? uso.last : {};
      const estimado = _codexNumero(dados.estimated_input_tokens, dados.estimated_context_tokens);
      const inputTokens = _codexNumero(last.input_tokens, total.input_tokens, uso.input_tokens, estimado);
      const janela = _codexNumero(
        uso.model_context_window,
        uso.context_window,
        dados.model_context_window,
        dados.context_window,
        dados.context_soft_limit
      );
      if (!inputTokens || !janela) {
        return { pct: 0, label: 'Ctx --%', title: 'Contexto ainda sem janela calculada.' };
      }
      const pct = Math.max(1, Math.min(999, Math.ceil((inputTokens / janela) * 100)));
      return {
        pct,
        label: `Ctx ${pct}%`,
        title: `${_codexFmtNumero(inputTokens)} de ${_codexFmtNumero(janela)} tokens de contexto`,
      };
    }

    function _codexStatusTexto(task) {
      const raw = String(task?.live_status || '').trim();
      if (raw) return raw;
      const status = String(task?.status || '').trim();
      if (status === 'awaiting_approval') return 'Aguardando aprovacao antes de alterar arquivos.';
      if (status === 'queued') return 'Tarefa na fila.';
      if (status === 'running') return 'Codex trabalhando.';
      if (status === 'completed') return 'Codex concluiu.';
      if (status === 'failed') return 'Codex falhou.';
      if (status === 'canceled' || status === 'cancel_requested') return 'Tarefa cancelada.';
      return 'Aguardando tarefa.';
    }

    function _codexStatusComDetalhe(base, task) {
      const principal = String(base || '').trim();
      const detalhe = String(task?.live_status || '').trim();
      if (!detalhe) return principal;
      const normalizar = value => String(value || '').replace(/\.+$/g, '').trim().toLowerCase();
      if (normalizar(detalhe) === normalizar(principal)) return principal;
      return `${principal} ${detalhe}`;
    }

    function _codexSetLiveBlock(id, titulo, texto) {
      const box = document.getElementById(id);
      if (!box) return;
      const conteudo = String(texto || '').trim();
      box.hidden = !conteudo;
      box.replaceChildren();
      if (!conteudo) return;
      const title = document.createElement('strong');
      title.textContent = titulo;
      const body = document.createElement('div');
      body.textContent = conteudo;
      box.append(title, body);
    }

    function _codexLogKindLabel(kind) {
      const value = String(kind || '').toLowerCase();
      if (value === 'command') return 'CMD';
      if (value === 'reasoning') return 'PENSAR';
      if (value === 'warning') return 'AVISO';
      if (value === 'error') return 'ERRO';
      if (value === 'tokens') return 'TOKENS';
      if (value === 'answer') return 'RESPOSTA';
      if (value === 'tool') return 'FERR.';
      if (value === 'source') return 'FONTE';
      if (value === 'data') return 'DADOS';
      if (value === 'confidence') return 'CONF.';
      if (value === 'fallback') return 'PROX.';
      return 'STATUS';
    }

    function _codexObservabilidadeLinhas(task, liveStatus) {
      const obs = task && typeof task.observability === 'object' ? task.observability : {};
      const linhas = [];
      const add = (kind, text) => {
        const value = String(text || '').trim();
        if (value) linhas.push({ kind, text: value });
      };
      add('status', liveStatus || obs.current_status || '');
      add('tool', obs.last_tool ? `${obs.last_tool}${obs.last_tool_module ? ` | ${obs.last_tool_module}` : ''}` : '');
      const fontes = Array.isArray(obs.sources) ? obs.sources.filter(Boolean) : [];
      add('source', obs.source || fontes.slice(0, 3).join(', '));
      const registros = Number(obs.records || 0);
      const confianca = String(obs.confidence || '').trim();
      if (obs.last_tool || registros || confianca) {
        add('data', `${_codexFmtNumero(registros)} registro(s)${confianca ? ` | confianca ${confianca}` : ''}`);
      }
      const falhas = Array.isArray(obs.failures) ? obs.failures.filter(Boolean) : [];
      if (falhas.length) add('warning', falhas.slice(0, 2).join(' | '));
      const fallbacks = Array.isArray(obs.next_fallbacks) ? obs.next_fallbacks.filter(Boolean) : [];
      if (fallbacks.length) add('fallback', fallbacks.slice(0, 5).join(', '));
      if (!linhas.length) add('status', liveStatus || 'Joao Pretinho pronto.');
      return linhas.slice(0, 7);
    }

    function _codexRenderRuntime(task) {
      const box = document.getElementById('jk-codex-runtime');
      if (!box) return;
      if (!task) {
        box.hidden = true;
        _codexSetLiveBlock('jk-codex-reasoning', '', '');
        _codexSetLiveBlock('jk-codex-live-answer', '', '');
        document.getElementById('jk-codex-log-list')?.replaceChildren();
        const pctEl = document.getElementById('jk-codex-context-pct');
        if (pctEl) {
          pctEl.textContent = 'Ctx --%';
          pctEl.title = 'Contexto usado';
        }
        return;
      }
      box.hidden = false;
      const liveStatus = _codexStatusTexto(task);
      const liveEl = document.getElementById('jk-codex-live-status');
      if (liveEl) liveEl.textContent = _codexTextoVisual(liveStatus);

      const contexto = _codexContextoTexto(task.context_stats);
      const ctxUsed = document.getElementById('jk-codex-context-used');
      const ctxDetail = document.getElementById('jk-codex-context-detail');
      if (ctxUsed) ctxUsed.textContent = contexto.main;
      if (ctxDetail) ctxDetail.textContent = contexto.detail;

      const tokens = _codexTokensTexto(task.token_usage);
      const tokenUsed = document.getElementById('jk-codex-token-used');
      const tokenDetail = document.getElementById('jk-codex-token-detail');
      if (tokenUsed) tokenUsed.textContent = tokens.main;
      if (tokenDetail) tokenDetail.textContent = tokens.detail;
      const pct = _codexUsoContextoPercentual(task.context_stats, task.token_usage);
      const pctEl = document.getElementById('jk-codex-context-pct');
      if (pctEl) {
        pctEl.textContent = pct.label;
        pctEl.title = pct.title;
      }
      box.title = [_codexTextoVisual(liveStatus), contexto.main, tokens.main]
        .map(value => String(value || '').trim())
        .filter(Boolean)
        .join(' | ');

      _codexSetLiveBlock('jk-codex-reasoning', 'Resumo do raciocinio', task.reasoning_summary || task.live_plan || '');
      const status = String(task.status || '');
      _codexSetLiveBlock('jk-codex-live-answer', 'Resposta parcial', status === 'completed' ? '' : (task.live_answer || ''));

      const lista = document.getElementById('jk-codex-log-list');
      if (!lista) return;
      lista.replaceChildren();
      const logs = Array.isArray(task.logs) ? task.logs.slice(-8) : [];
      const obsLinhas = _codexObservabilidadeLinhas(task, liveStatus);
      const linhas = obsLinhas.length ? obsLinhas : (logs.length ? logs : [{ kind: 'status', text: liveStatus }]);
      linhas.forEach(log => {
        const row = document.createElement('div');
        row.className = 'jk-codex-log-row';
        const kind = document.createElement('small');
        kind.textContent = _codexLogKindLabel(log && log.kind);
        const text = document.createElement('span');
        text.textContent = _codexTextoVisual((log && log.text) || '');
        row.append(kind, text);
        lista.appendChild(row);
      });
    }

    function _codexEhStatusEnvioObsoleto(role, texto) {
      if (String(role || '').trim() !== 'status') return false;
      const normalizado = String(texto || '').trim().replace(/\s+/g, ' ').toLowerCase();
      return normalizado === 'tarefa enviada ao codex.'
        || normalizado === 'tarefa enviada ao codex'
        || normalizado === 'tarefa criada para acesso mutavel. confirme para executar.'
        || normalizado === 'tarefa criada para acesso mutavel. confirme para executar';
    }

    function _codexSafeStorageId(value, fallback = 'default') {
      let text = String(value || '').trim();
      try {
        text = text.normalize('NFD').replace(/[\u0300-\u036f]/g, '');
      } catch (_) {}
      text = text.replace(/[^a-zA-Z0-9_-]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 80);
      return text || fallback;
    }

    function _codexUsuarioEscopo() {
      let usuario = 'user';
      try {
        const data = JSON.parse(localStorage.getItem('user_data') || '{}') || {};
        usuario = data.username || data.user || data.email || data.nome || data.name || usuario;
      } catch (_) {}
      return _codexSafeStorageId(`${_clientId()}_${usuario}`);
    }

    function _codexLerMapaConversaAtiva() {
      try {
        const raw = JSON.parse(localStorage.getItem(CODEX_ACTIVE_CONVERSATION_KEY) || '{}');
        if (raw && typeof raw === 'object' && !Array.isArray(raw)) return raw;
      } catch (_) {}
      return {};
    }

    function _codexSalvarMapaConversaAtiva(map) {
      try { localStorage.setItem(CODEX_ACTIVE_CONVERSATION_KEY, JSON.stringify(map || {})); } catch (_) {}
    }

    function _codexDefaultConversationId() {
      return `universal_${_codexUsuarioEscopo()}`;
    }

    function _codexNovoConversationId() {
      return `conv_${_codexUsuarioEscopo()}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`;
    }

    function _codexSetActiveConversationId(value) {
      const id = _codexSafeStorageId(value || _codexDefaultConversationId());
      codexConversationId = id;
      const map = _codexLerMapaConversaAtiva();
      map[_codexUsuarioEscopo()] = id;
      _codexSalvarMapaConversaAtiva(map);
      return id;
    }

    function _codexGetActiveConversationId(create = true) {
      if (codexConversationId) return codexConversationId;
      const map = _codexLerMapaConversaAtiva();
      const id = _codexSafeStorageId(map[_codexUsuarioEscopo()] || '', '');
      if (id) {
        codexConversationId = id;
        return id;
      }
      if (!create) return '';
      return _codexSetActiveConversationId(_codexDefaultConversationId());
    }

    function _codexHistoricoStorageKey(conversationId = '') {
      const id = _codexSafeStorageId(conversationId || _codexGetActiveConversationId(true));
      return `${CODEX_HISTORY_PREFIX}_${_codexUsuarioEscopo()}_${id}`;
    }

    function _codexPanelStateStorageKey() {
      return `${CODEX_PANEL_STATE_PREFIX}_${_codexUsuarioEscopo()}`;
    }

    function _codexLerEstadoPainel() {
      try {
        const raw = JSON.parse(localStorage.getItem(_codexPanelStateStorageKey()) || '{}');
        return raw && typeof raw === 'object' && !Array.isArray(raw) ? raw : {};
      } catch (_) {
        return {};
      }
    }

    function _codexSalvarEstadoPainel(extra = {}) {
      try {
        const lista = document.getElementById('jk-codex-messages');
        const atual = _codexLerEstadoPainel();
        const payload = {
          ...atual,
          ...extra,
          conversation_id: _codexGetActiveConversationId(true),
          thread_id: codexThreadId || '',
          task_id: String(codexTaskAtual?.task_id || atual.task_id || ''),
          panel_open: !!codexPanelAberto,
          history_visible: !!codexHistoryVisible,
          scroll_top: lista ? lista.scrollTop : Number(atual.scroll_top || 0),
          updated_at: new Date().toISOString(),
        };
        localStorage.setItem(_codexPanelStateStorageKey(), JSON.stringify(payload));
      } catch (_) {}
    }

    function _codexLerHistoricoLocal() {
      try {
        const key = _codexHistoricoStorageKey();
        let rawText = localStorage.getItem(key);
        if (!rawText && _codexGetActiveConversationId(true) === _codexDefaultConversationId()) {
          rawText = localStorage.getItem(CODEX_HISTORY_KEY);
          if (rawText) {
            try { localStorage.setItem(key, rawText); } catch (_) {}
          }
        }
        const raw = JSON.parse(rawText || '[]') || [];
        if (!Array.isArray(raw)) return [];
        return raw.map(item => ({
          role: String(item?.role || 'assistant'),
          text: String(item?.text || ''),
          classExtra: String(item?.classExtra || ''),
          task_id: String(item?.task_id || ''),
          report_formats: Array.isArray(item?.report_formats) ? item.report_formats.map(fmt => String(fmt || '').trim()).filter(Boolean) : [],
          at: String(item?.at || ''),
        })).filter(item => item.text && !_codexEhStatusEnvioObsoleto(item.role, item.text)).slice(-CODEX_HISTORY_LIMIT);
      } catch (_) {
        return [];
      }
    }

    function _codexSalvarHistoricoLocal() {
      try {
        const limpo = codexMessagesAtuais
          .filter(item => item && item.text && !_codexEhStatusEnvioObsoleto(item.role, item.text))
          .slice(-CODEX_HISTORY_LIMIT);
        localStorage.setItem(_codexHistoricoStorageKey(), JSON.stringify(limpo));
        _codexSalvarEstadoPainel();
      } catch (_) {}
    }

    function _codexRenderizarHistoricoLocal() {
      const lista = document.getElementById('jk-codex-messages');
      if (!lista) return;
      lista.innerHTML = '';
      codexMessagesAtuais = _codexLerHistoricoLocal();
      if (!codexMessagesAtuais.length) {
        _codexAddMsg('status', 'Codex interno pronto para verificar o ambiente local.', '', { persist: false });
        return;
      }
      codexMessagesAtuais.forEach(item => {
        const msg = _codexAddMsg(item.role, item.text, item.classExtra || '', { persist: false });
        if (msg && String(item.classExtra || '').includes('codex-report')) {
          _codexAppendReportActions(msg, item.task_id, item.report_formats);
        }
      });
      _codexSalvarHistoricoLocal();
      const estado = _codexLerEstadoPainel();
      const savedConversation = String(estado.conversation_id || '').trim();
      const savedScroll = Number(estado.scroll_top || 0);
      setTimeout(() => {
        if (savedConversation === _codexGetActiveConversationId(true) && Number.isFinite(savedScroll) && savedScroll > 0) {
          lista.scrollTop = savedScroll;
        } else {
          lista.scrollTop = lista.scrollHeight + 9999;
        }
      }, 0);
      if (codexThreadId) _codexSetStatus('Contexto Codex restaurado.');
    }

    function _codexTaskMessageClass(task) {
      const kind = String(task?.message_kind || '').trim().toLowerCase();
      if (kind === 'report' || task?.report_id || String(task?.model || '') === 'codex-assistant-report') return 'codex-report';
      return '';
    }

    function _codexHistoricoFromTasks(tasks) {
      const lista = Array.isArray(tasks)
        ? tasks.slice().sort((a, b) => _codexTaskTimeMs(a, 'created_at') - _codexTaskTimeMs(b, 'created_at'))
        : [];
      const entries = [];
      lista.forEach(task => {
        const taskId = String(task?.task_id || '');
        const prompt = String(task?.prompt || '').trim();
        const finalResponse = String(task?.final_response || '').trim();
        const error = String(task?.error || '').trim();
        const status = String(task?.status || '').trim();
        if (task?.thread_id && !codexThreadId) {
          codexThreadId = String(task.thread_id || '').trim();
          if (codexThreadId) localStorage.setItem('jk_codex_thread_id', codexThreadId);
        }
        const reportId = String(task?.report_id || '').trim();
        const taskRefId = reportId || taskId;
        const reportFormats = Array.isArray(task?.report_formats) ? task.report_formats : [];
        if (prompt) entries.push({ role: 'user', text: prompt, classExtra: '', task_id: taskId, at: task?.created_at || '' });
        if (finalResponse) entries.push({ role: 'assistant', text: finalResponse, classExtra: _codexTaskMessageClass(task), task_id: taskRefId, report_formats: reportFormats, at: task?.completed_at || '' });
        else if (error) entries.push({ role: 'assistant', text: error, classExtra: '', task_id: taskId, at: task?.completed_at || '' });
        else if (status === 'awaiting_approval') entries.push({ role: 'status', text: 'Tarefa aguardando confirmacao para executar.', classExtra: '', task_id: taskId, at: task?.created_at || '' });
      });
      return entries.slice(-CODEX_HISTORY_LIMIT);
    }

    function _codexTaskTimeMs(task, preferredKey = '') {
      const keys = preferredKey
        ? [preferredKey, 'completed_at', 'started_at', 'created_at']
        : ['completed_at', 'started_at', 'created_at'];
      for (const key of keys) {
        const raw = String(task?.[key] || '').trim();
        if (!raw) continue;
        const parsed = Date.parse(raw);
        if (Number.isFinite(parsed)) return parsed;
      }
      return 0;
    }

    function _codexConversationKey(task) {
      const conversation = String(task?.conversation_id || '').trim();
      if (conversation) return conversation;
      const thread = String(task?.thread_id || '').trim();
      if (thread) return `thread_${_codexSafeStorageId(thread)}`;
      const taskId = String(task?.task_id || '').trim();
      return taskId ? `task_${_codexSafeStorageId(taskId)}` : `tmp_${Math.random().toString(16).slice(2)}`;
    }

    function _codexConversationGroups(tasks) {
      const map = new Map();
      const lista = Array.isArray(tasks) ? tasks.slice() : [];
      lista.forEach(task => {
        if (!task || typeof task !== 'object') return;
        const key = _codexConversationKey(task);
        const group = map.get(key) || {
          conversation_id: key,
          thread_id: String(task.thread_id || ''),
          tasks: [],
          latest: task,
          last_at: 0,
          first_at: 0,
        };
        group.tasks.push(task);
        const time = _codexTaskTimeMs(task);
        if (!group.last_at || time >= group.last_at) {
          group.last_at = time;
          group.latest = task;
        }
        const first = _codexTaskTimeMs(task, 'created_at');
        if (!group.first_at || (first && first < group.first_at)) group.first_at = first;
        if (!group.thread_id && task.thread_id) group.thread_id = String(task.thread_id || '');
        map.set(key, group);
      });
      return Array.from(map.values())
        .map(group => ({
          ...group,
          tasks: group.tasks.slice().sort((a, b) => _codexTaskTimeMs(a, 'created_at') - _codexTaskTimeMs(b, 'created_at')),
        }))
        .sort((a, b) => (b.last_at || 0) - (a.last_at || 0));
    }

    function _codexFindConversationGroup(conversationId) {
      const id = String(conversationId || '').trim();
      if (!id) return null;
      return _codexConversationGroups(codexHistoryTasks).find(group => group.conversation_id === id)
        || null;
    }

    function _codexDataCurta(value) {
      const raw = String(value || '').trim();
      if (!raw) return '';
      const d = new Date(raw);
      if (!Number.isNaN(d.getTime())) {
        try {
          return d.toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
        } catch (_) {}
      }
      return raw.replace('T', ' ').replace('Z', '').slice(0, 16);
    }

    function _codexTaskTitulo(task) {
      const prompt = String(task?.prompt || '').replace(/\s+/g, ' ').trim();
      if (prompt) return prompt.slice(0, 120);
      const response = String(task?.final_response || task?.error || '').replace(/\s+/g, ' ').trim();
      return response ? response.slice(0, 120) : 'Conversa sem titulo';
    }

    function _codexRenderHistoricoTasks(tasks) {
      const panel = document.getElementById('jk-codex-history-panel');
      const list = document.getElementById('jk-codex-history-list');
      if (!panel || !list) return;
      list.replaceChildren();
      const groups = _codexConversationGroups(tasks);
      if (!groups.length) {
        const empty = document.createElement('div');
        empty.className = 'jk-codex-history-empty';
        empty.textContent = 'Nenhuma conversa salva ainda.';
        list.appendChild(empty);
        return;
      }
      groups.forEach(group => {
        const task = group.latest || {};
        const conversationId = String(group.conversation_id || '');
        const row = document.createElement('div');
        row.className = 'jk-codex-history-item';
        if (conversationId === _codexGetActiveConversationId(false) || (codexTaskAtual && _codexConversationKey(codexTaskAtual) === conversationId)) row.classList.add('is-active');

        const info = document.createElement('div');
        info.style.minWidth = '0';
        const title = document.createElement('div');
        title.className = 'jk-codex-history-title';
        title.textContent = _codexTextoVisual(_codexTaskTitulo(task));
        const meta = document.createElement('div');
        meta.className = 'jk-codex-history-meta';
        const pct = _codexUsoContextoPercentual(task?.context_stats, task?.token_usage);
        meta.textContent = [
          _codexDataCurta(task?.completed_at || task?.created_at),
          `${group.tasks.length} pergunta${group.tasks.length === 1 ? '' : 's'}`,
          String(task?.status || '').trim(),
          pct.pct ? pct.label : '',
        ].filter(Boolean).join(' | ');
        info.append(title, meta);

        const actions = document.createElement('div');
        actions.className = 'jk-codex-history-actions';
        const open = document.createElement('button');
        open.type = 'button';
        open.textContent = 'Abrir';
        open.title = 'Abrir conversa';
        open.addEventListener('click', () => _codexAbrirTarefaHistorico(conversationId));
        const del = document.createElement('button');
        del.type = 'button';
        del.className = 'danger';
        del.textContent = 'Excluir';
        del.title = 'Excluir conversa';
        del.addEventListener('click', () => _codexExcluirTarefaHistorico(conversationId));
        actions.append(open, del);
        row.append(info, actions);
        list.appendChild(row);
      });
    }

    async function _codexCarregarListaHistorico() {
      try {
        const data = await _codexFetchJson('/api/admin/codex/tasks?limit=100');
        codexHistoryTasks = Array.isArray(data && data.tasks) ? data.tasks : [];
        _codexRenderHistoricoTasks(codexHistoryTasks);
        return codexHistoryTasks;
      } catch (err) {
        const list = document.getElementById('jk-codex-history-list');
        if (list) {
          list.replaceChildren();
          const empty = document.createElement('div');
          empty.className = 'jk-codex-history-empty';
          empty.textContent = _codexErroCurto(err, 'Nao foi possivel carregar o historico.');
          list.appendChild(empty);
        }
        return [];
      }
    }

    async function _codexToggleHistorico(force) {
      const panel = document.getElementById('jk-codex-history-panel');
      if (!panel) return;
      codexHistoryVisible = typeof force === 'boolean' ? force : !codexHistoryVisible;
      panel.classList.toggle('ativo', codexHistoryVisible);
      _codexSalvarEstadoPainel();
      if (codexHistoryVisible) await _codexCarregarListaHistorico();
    }

    async function _codexAbrirTarefaHistorico(conversationId) {
      const id = String(conversationId || '').trim();
      if (!id) return;
      try {
        let group = _codexFindConversationGroup(id);
        if (!group && (id.startsWith('task:') || id.startsWith('task_'))) {
          const taskId = id.startsWith('task:') ? id.slice(5) : id.slice(5);
          const data = await _codexFetchJson('/api/admin/codex/tasks/' + encodeURIComponent(taskId));
          const task = data && data.task;
          if (task) group = { conversation_id: id, thread_id: String(task.thread_id || ''), tasks: [task], latest: task };
        }
        if (!group || !group.tasks.length) return;
        const latest = group.latest || group.tasks[group.tasks.length - 1];
        codexTaskAtual = latest;
        const activeConversation = String(latest.conversation_id || group.conversation_id || '').trim();
        if (activeConversation) _codexSetActiveConversationId(activeConversation);
        if (group.thread_id || latest.thread_id) {
          codexThreadId = String(group.thread_id || latest.thread_id || '');
          localStorage.setItem('jk_codex_thread_id', codexThreadId);
        }
        codexMessagesAtuais = _codexHistoricoFromTasks(group.tasks);
        _codexSalvarHistoricoLocal();
        _codexRenderizarHistoricoLocal();
        _codexMostrarAprovacao(latest);
        _codexRenderRuntime(latest);
        _codexSalvarEstadoPainel({ task_id: String(latest.task_id || '') });
        _codexSetStatus('Conversa carregada do historico.');
        _codexRenderHistoricoTasks(codexHistoryTasks);
      } catch (err) {
        _codexSetStatus(_codexErroCurto(err, 'Nao foi possivel abrir a conversa.'), true);
      }
    }

    async function _codexExcluirTarefaHistorico(conversationId) {
      const id = String(conversationId || '').trim();
      if (!id) return;
      const group = _codexFindConversationGroup(id);
      const tasks = group && group.tasks.length
        ? group.tasks
        : ((id.startsWith('task:') || id.startsWith('task_')) ? [{ task_id: id.slice(5) }] : []);
      const ids = tasks.map(task => String(task?.task_id || '').trim()).filter(Boolean);
      try {
        let deletedIds = ids.slice();
        try {
          const data = await _codexFetchJson('/api/admin/codex/conversations/' + encodeURIComponent(id), { method: 'DELETE' });
          if (Array.isArray(data && data.task_ids)) deletedIds = data.task_ids.map(item => String(item || '').trim()).filter(Boolean);
        } catch (err) {
          if (!ids.length) throw err;
          for (const taskId of ids) {
            await _codexFetchJson('/api/admin/codex/tasks/' + encodeURIComponent(taskId), { method: 'DELETE' });
          }
        }
        const idSet = new Set(deletedIds.length ? deletedIds : ids);
        codexHistoryTasks = codexHistoryTasks.filter(task => {
          const taskId = String(task?.task_id || '');
          return _codexConversationKey(task) !== id && !idSet.has(taskId);
        });
        try { localStorage.removeItem(_codexHistoricoStorageKey(id)); } catch (_) {}
        const eraAtiva = id === _codexGetActiveConversationId(false) || (codexTaskAtual && (_codexConversationKey(codexTaskAtual) === id || idSet.has(String(codexTaskAtual.task_id || ''))));
        if (eraAtiva) {
          codexTaskAtual = null;
          codexThreadId = '';
          codexMessagesAtuais = [];
          _codexSetActiveConversationId(_codexNovoConversationId());
          try {
            localStorage.removeItem('jk_codex_thread_id');
          } catch (_) {}
          _codexRenderRuntime(null);
          _codexMostrarAprovacao(null);
          document.getElementById('jk-codex-messages')?.replaceChildren();
          _codexAddMsg('status', 'Conversa excluida do historico.', '', { persist: false });
          _codexSalvarEstadoPainel({ task_id: '' });
        }
        _codexRenderHistoricoTasks(codexHistoryTasks);
        _codexSetStatus('Conversa excluida.');
      } catch (err) {
        _codexSetStatus(_codexErroCurto(err, 'Nao foi possivel excluir a conversa.'), true);
      }
    }

    async function _codexCarregarHistoricoPersistido() {
      const deveAtualizarHistorico = codexMessagesAtuais.length <= 1;
      try {
        const data = await _codexFetchJson('/api/admin/codex/tasks?limit=100');
        const tasks = Array.isArray(data && data.tasks) ? data.tasks : [];
        codexHistoryTasks = tasks;
        if (codexHistoryVisible) _codexRenderHistoricoTasks(codexHistoryTasks);
        const groups = _codexConversationGroups(tasks);
        const activeId = _codexGetActiveConversationId(false);
        let selectedGroup = activeId ? groups.find(group => String(group.conversation_id || '') === activeId) || null : null;
        if (!selectedGroup && !activeId && deveAtualizarHistorico) selectedGroup = groups[0] || null;
        if (selectedGroup && selectedGroup.conversation_id) _codexSetActiveConversationId(selectedGroup.conversation_id);
        const latest = selectedGroup ? selectedGroup.latest : null;
        if (latest) {
          codexTaskAtual = latest;
          if (latest.thread_id) {
            codexThreadId = String(latest.thread_id || '');
            localStorage.setItem('jk_codex_thread_id', codexThreadId);
          }
          _codexMostrarAprovacao(latest);
          _codexRenderRuntime(latest);
          const latestStatus = String(latest.status || '');
          if (latest.task_id && ['queued', 'running', 'awaiting_approval', 'cancel_requested'].includes(latestStatus)) {
            _codexPollTask(latest.task_id);
          }
          _codexSalvarEstadoPainel({ task_id: String(latest.task_id || '') });
        }
        if (!deveAtualizarHistorico) return;
        const historico = _codexHistoricoFromTasks(selectedGroup ? selectedGroup.tasks : (latest ? [latest] : []));
        if (!historico.length) return;
        codexMessagesAtuais = historico;
        _codexSalvarHistoricoLocal();
        _codexRenderizarHistoricoLocal();
      } catch (_) {}
    }

    function _codexNovaConversa() {
      codexThreadId = '';
      _codexSetActiveConversationId(_codexNovoConversationId());
      codexTaskAtual = null;
      codexActionRunAtual = null;
      if (codexActionPollTimer) clearTimeout(codexActionPollTimer);
      codexActionPollTimer = null;
      codexPollFailures = {};
      codexRenderedFinalTasks = new Set();
      codexMessagesAtuais = [];
      try {
        localStorage.removeItem('jk_codex_thread_id');
        localStorage.removeItem(_codexHistoricoStorageKey());
      } catch (_) {}
      document.getElementById('jk-codex-messages')?.replaceChildren();
      _codexMostrarAprovacao(null);
      _codexRenderRuntime(null);
      _codexSalvarEstadoPainel({ task_id: '', scroll_top: 0 });
      _codexSetStatus('Nova conversa Codex pronta.');
      _codexAddMsg('status', 'Nova conversa iniciada sem contexto anterior.', '', { persist: false });
    }

    function _codexAddMsg(role, texto, classExtra = '', options = {}) {
      const lista = document.getElementById('jk-codex-messages');
      if (!lista) return null;
      const rawText = String(texto || '');
      const text = role === 'user' ? rawText : _codexTextoVisual(rawText);
      if (_codexEhStatusEnvioObsoleto(role, text)) return null;
      const div = document.createElement('div');
      div.className = `jk-codex-msg ${role || 'assistant'} ${classExtra || ''}`.trim();
      if (role === 'assistant') {
        div.innerHTML = _renderTexto(text);
      } else {
        div.textContent = text;
      }
      lista.appendChild(div);
      lista.scrollTop = lista.scrollHeight + 9999;
      if (options.persist !== false && text) {
        codexMessagesAtuais.push({
          role: String(role || 'assistant'),
          text,
          classExtra: String(classExtra || ''),
          task_id: String(options.task_id || ''),
          report_formats: Array.isArray(options.report_formats) ? options.report_formats.map(fmt => String(fmt || '').trim()).filter(Boolean) : [],
          at: new Date().toISOString(),
        });
        codexMessagesAtuais = codexMessagesAtuais.slice(-CODEX_HISTORY_LIMIT);
        _codexSalvarHistoricoLocal();
      }
      return div;
    }

    async function _codexFetchJson(path, options = {}) {
      let ultimoErro = null;
      let ultimoHttpErro = null;
      const urls = _codexApiUrls(path);
      for (const url of urls) {
        try {
          const resp = await window.__JK_IA_SIDEBAR_FETCH__(url, {
            ...options,
            headers: { ..._authHeaders(), ...(options.headers || {}) },
            cache: 'no-store',
          });
          const data = await resp.json().catch(() => ({}));
          if (!resp.ok) {
            const detail = data && (data.detail || data.message || data.error);
            const httpErro = new Error(detail || `HTTP ${resp.status}`);
            httpErro.status = resp.status;
            httpErro.url = url;
            ultimoHttpErro = httpErro;
            if (resp.status !== 404 && resp.status !== 405) throw httpErro;
            ultimoErro = httpErro;
            continue;
          }
          return data;
        } catch (err) {
          if (err && err.status && err.status !== 404 && err.status !== 405) throw err;
          ultimoErro = err;
        }
      }
      const msg = ultimoErro && ultimoErro.message ? String(ultimoErro.message) : '';
      if (ultimoHttpErro) throw ultimoHttpErro;
      if (msg && !/failed to fetch|load failed|networkerror/i.test(msg)) throw ultimoErro;
      throw new Error('Nao consegui acessar o backend do Codex. Verifique se o app local esta aberto na porta 8001. URLs testadas: ' + urls.join(', '));
    }

    function _codexAssistantSeen() {
      try {
        const data = JSON.parse(localStorage.getItem(CODEX_ASSISTANT_SEEN_KEY) || '{}') || {};
        return data && typeof data === 'object' ? data : {};
      } catch (_) {
        return {};
      }
    }

    function _codexAssistantSaveSeen(data) {
      try { localStorage.setItem(CODEX_ASSISTANT_SEEN_KEY, JSON.stringify(data || {})); } catch (_) {}
    }

    function _codexAssistantReported() {
      try {
        const data = JSON.parse(localStorage.getItem(CODEX_ASSISTANT_REPORTED_KEY) || '{}') || {};
        return data && typeof data === 'object' ? data : {};
      } catch (_) {
        return {};
      }
    }

    function _codexAssistantSaveReported(data) {
      try { localStorage.setItem(CODEX_ASSISTANT_REPORTED_KEY, JSON.stringify(data || {})); } catch (_) {}
    }

    function _codexSuggestionId(item) {
      const direct = String(item && item.id || '').trim();
      if (direct) return direct;
      return [
        item && item.source || 'codex_assistant',
        item && item.title || '',
        item && item.detail || '',
        item && item.created_at || '',
      ].map(value => String(value || '').trim()).filter(Boolean).join('|');
    }

    function _codexAssistantMarkSeen(suggestions) {
      const seen = _codexAssistantSeen();
      (Array.isArray(suggestions) ? suggestions : []).forEach(item => {
        const id = _codexSuggestionId(item);
        if (id) seen[id] = new Date().toISOString();
      });
      _codexAssistantSaveSeen(seen);
    }

    function _codexAssistantHasUnseen(suggestions) {
      const seen = _codexAssistantSeen();
      return (Array.isArray(suggestions) ? suggestions : []).some(item => {
        const id = _codexSuggestionId(item);
        return id && !seen[id] && String(item.severity || '') !== 'ok';
      });
    }

    function _codexSuggestionReportPrompt(items) {
      const alerts = (Array.isArray(items) ? items : []).filter(Boolean);
      const lines = [
        'Gere um relatorio operacional completo destes alertas proativos e mostre o relatorio integral no chat.',
        'Liste todos os SKUs envolvidos em tabelas, sem resumir somente os primeiros.',
        'Para cada SKU, explique motivo, impacto comercial, dados usados e acao recomendada.',
        'Quando falar de ruptura ou reposicao, considere apenas saldo de loja; nao use estoque Full no calculo.',
        'Explique em linguagem simples onde consultou os dados, periodo, loja/conta, quantidade de registros e avisos de dados incompletos. Nao mostre codigos internos de ferramentas.',
      ];
      alerts.forEach((item, index) => {
        const title = String(item && item.title || 'Alerta Codex').trim();
        const detail = String(item && item.detail || '').trim();
        const recommendation = String(item && item.recommendation || '').trim();
        lines.push('');
        lines.push(`Alerta ${index + 1}: ${title}`);
        if (detail) lines.push(`Detalhe: ${detail}`);
        if (recommendation) lines.push(`Acao sugerida: ${recommendation}`);
        if (item && item.source) lines.push(`Fonte: ${item.source}`);
        if (item && item.created_at) lines.push(`Horario do alerta: ${item.created_at}`);
      });
      return lines.filter(Boolean).join('\n');
    }

    async function _codexGerarRelatorioAutomaticoAlertas(items) {
      if (codexAssistantAutoReportRunning) return;
      const alerts = (Array.isArray(items) ? items : []).filter(item => {
        const id = _codexSuggestionId(item);
        return id && String(item && item.severity || '') !== 'ok';
      });
      if (!alerts.length) return;
      const reported = _codexAssistantReported();
      const novos = alerts.filter(item => {
        const id = _codexSuggestionId(item);
        return id && !reported[id];
      });
      if (!novos.length) return;
      codexAssistantAutoReportRunning = true;
      try {
        _codexSetStatus('Gerando relatorio completo dos alertas no chat...');
        const report = await _codexGerarRelatorioAssistente(_codexSuggestionReportPrompt(novos));
        if (report) {
          const now = new Date().toISOString();
          novos.forEach(item => {
            const id = _codexSuggestionId(item);
            if (id) reported[id] = now;
          });
          _codexAssistantSaveReported(reported);
        }
      } finally {
        codexAssistantAutoReportRunning = false;
      }
    }

    function _codexAddSuggestionMsg(item, seen) {
      const id = _codexSuggestionId(item);
      if (!id || seen[id] || String(item && item.severity || '') === 'ok') return false;
      const title = String(item && item.title || 'Alerta Codex').trim();
      const detail = String(item && item.detail || '').trim();
      const recommendation = String(item && item.recommendation || '').trim();
      const meta = [item && item.source || 'codex_assistant', item && item.created_at || ''].filter(Boolean).join(' | ');
      const text = [
        `## ${title}`,
        detail,
        recommendation ? `**Acao sugerida:** ${recommendation}` : '',
        meta ? `Fonte: \`${meta}\`` : '',
      ].filter(Boolean).join('\n\n');
      const msg = _codexAddMsg('assistant', text, 'codex-alert', { task_id: id });
      if (msg) {
        const actions = document.createElement('div');
        actions.className = 'jk-codex-msg-actions';
        const report = document.createElement('button');
        report.type = 'button';
        report.className = 'jk-codex-tool';
        report.textContent = 'Relatorio completo';
        report.title = 'Gerar relatorio completo deste alerta no chat';
        report.addEventListener('click', () => {
          const prompt = [
            'Gere um relatorio operacional completo deste alerta e mostre o relatorio integral no chat.',
            'Liste todos os SKUs envolvidos em tabela, sem resumir somente os primeiros.',
            'Explique o motivo do risco, impacto comercial, dados usados e acao recomendada por SKU.',
            'Quando falar de ruptura ou reposicao, considere apenas saldo de loja; nao use estoque Full no calculo.',
            `Alerta: ${title}`,
            `Detalhe: ${detail}`,
            recommendation ? `Acao sugerida: ${recommendation}` : '',
            item && item.source ? `Fonte: ${item.source}` : '',
          ].filter(Boolean).join('\n');
          void _codexGerarRelatorioAssistente(prompt);
        });
        actions.appendChild(report);
        msg.appendChild(actions);
      }
      seen[id] = new Date().toISOString();
      return true;
    }

    function _codexRenderSugestoes(suggestions) {
      const box = document.getElementById('jk-codex-suggestions');
      if (!box) return;
      const items = (Array.isArray(suggestions) ? suggestions : []).slice(0, 4);
      box.replaceChildren();
      box.hidden = true;
      const unseen = _codexAssistantHasUnseen(items) && !codexPanelAberto;
      const seen = _codexAssistantSeen();
      let added = false;
      items.forEach(item => {
        if (_codexAddSuggestionMsg(item, seen)) added = true;
      });
      if (added) _codexAssistantSaveSeen(seen);
      void _codexGerarRelatorioAutomaticoAlertas(items);
      document.getElementById('jk-codex-fab')?.classList.toggle('piscando', unseen);
      document.getElementById('jk-right-sidebar-hotspot')?.classList.toggle('tem-alerta', unseen || iaTemMensagemNaoVista || msgTemMensagemNaoVista);
    }

    async function _codexCarregarSugestoes() {
      if (!_codexAtualizarVisibilidade()) return [];
      try {
        const data = await _codexFetchJson('/api/admin/codex/assistant/suggestions');
        const suggestions = Array.isArray(data && data.suggestions) ? data.suggestions : [];
        _codexRenderSugestoes(suggestions);
        return suggestions;
      } catch (_) {
        return [];
      }
    }

    async function _codexRodarProativo(force = false) {
      if (!_codexAtualizarVisibilidade()) return;
      try {
        _codexSetStatus(force ? 'Codex verificando dados proativamente...' : 'Codex verificando alertas de 30 minutos...');
        const data = await _codexFetchJson('/api/admin/codex/assistant/proactive/run', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ force: !!force, screen_context: _codexObterContextoTelaAtual() }),
        });
        const suggestions = Array.isArray(data && data.suggestions) ? data.suggestions : [];
        _codexRenderSugestoes(suggestions);
        if (data && data.status === 'completed') {
          _codexSetStatus('Codex verificou alertas proativos.');
        }
      } catch (err) {
        _codexSetStatus(_codexErroCurto(err, 'Falha ao verificar alertas proativos.'), true);
      }
    }

    function _codexRelatorioId(report) {
      return String((report && report.report_id) || '').trim();
    }

    function _codexReportFormats(report) {
      const downloads = report && report.downloads && typeof report.downloads === 'object' ? report.downloads : {};
      const preferred = Array.isArray(report && report.chat_download_formats) ? report.chat_download_formats : [];
      const formats = preferred.length ? preferred : ['pdf', 'xlsx'];
      return formats.map(fmt => String(fmt || '').toLowerCase()).filter(fmt => ['xlsx', 'pdf'].includes(fmt) && downloads[fmt]);
    }

    async function _codexAbrirRelatorio(reportId, fmt) {
      const id = String(reportId || '').trim();
      const format = String(fmt || 'html').trim().toLowerCase();
      if (!id) return;
      const path = `/api/admin/codex/assistant/reports/${encodeURIComponent(id)}/download?format=${encodeURIComponent(format)}&inline=1`;
      const viewer = window.open('about:blank', '_blank');
      if (viewer) {
        try {
          viewer.opener = null;
          viewer.document.title = 'Abrindo relatorio João Pretinho...';
          viewer.document.body.innerHTML = '<p style="font-family:Arial,sans-serif;padding:24px;">Abrindo relatorio João Pretinho...</p>';
        } catch (_) {}
      }
      let ultimoErro = null;
      for (const url of _codexApiUrls(path)) {
        try {
          const resp = await fetch(url, { headers: _authHeaders(), cache: 'no-store' });
          if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
          const blob = await resp.blob();
          const objectUrl = URL.createObjectURL(blob);
          if (viewer && !viewer.closed) {
            viewer.location.href = objectUrl;
          } else {
            const a = document.createElement('a');
            a.href = objectUrl;
            a.target = '_blank';
            a.rel = 'noopener';
            document.body.appendChild(a);
            a.click();
            a.remove();
          }
          setTimeout(() => URL.revokeObjectURL(objectUrl), 10 * 60 * 1000);
          return;
        } catch (err) {
          ultimoErro = err;
        }
      }
      if (viewer && !viewer.closed) {
        try {
          viewer.document.body.innerHTML = '<p style="font-family:Arial,sans-serif;padding:24px;color:#991b1b;">Nao foi possivel abrir o relatorio João Pretinho.</p>';
        } catch (_) {}
      }
      throw ultimoErro || new Error('Falha ao abrir relatorio.');
    }

    async function _codexBaixarRelatorio(reportId, fmt) {
      const id = String(reportId || '').trim();
      const format = String(fmt || '').trim().toLowerCase();
      if (!id || !format) return;
      const path = `/api/admin/codex/assistant/reports/${encodeURIComponent(id)}/download?format=${encodeURIComponent(format)}`;
      let ultimoErro = null;
      for (const url of _codexApiUrls(path)) {
        try {
          const resp = await fetch(url, { headers: _authHeaders(), cache: 'no-store' });
          if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
          const blob = await resp.blob();
          const objectUrl = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = objectUrl;
          a.download = `joao_pretinho_${id}.${format}`;
          document.body.appendChild(a);
          a.click();
          a.remove();
          setTimeout(() => URL.revokeObjectURL(objectUrl), 10 * 60 * 1000);
          return;
        } catch (err) {
          ultimoErro = err;
        }
      }
      throw ultimoErro || new Error('Falha ao baixar relatorio.');
    }

    function _codexReportFormatLabel(fmt) {
      const format = String(fmt || '').toLowerCase();
      if (format === 'xlsx') return 'Planilha XLSX';
      if (format === 'pdf') return 'PDF';
      return format.toUpperCase();
    }

    function _codexReportChatText(report, intro) {
      const reportId = _codexRelatorioId(report);
      const title = String(report && report.title || 'Relatorio Joao Pretinho').trim();
      const text = String(report && report.chat_text || '').trim();
      if (text) return text;
      return [
        intro || 'Relatorio Joao Pretinho gerado.',
        '',
        `# ${title}`,
        reportId ? `ID: \`${reportId}\`` : '',
        '',
        'O conteudo detalhado nao veio no metadata deste relatorio antigo. Use os botoes abaixo para baixar PDF ou planilha.'
      ].filter(Boolean).join('\n');
    }

    function _codexAppendReportActions(msg, reportId, formats = null) {
      const id = String(reportId || '').trim();
      if (!msg || !id || msg.querySelector('.jk-codex-report-downloads')) return;
      const rawFormats = Array.isArray(formats) && formats.length ? formats : ['pdf', 'xlsx'];
      const unique = Array.from(new Set(rawFormats.map(fmt => String(fmt || '').trim().toLowerCase()).filter(fmt => ['pdf', 'xlsx', 'html'].includes(fmt))));
      if (!unique.length) return;
      const actions = document.createElement('div');
      actions.className = 'jk-codex-msg-actions jk-codex-report-downloads';
      unique.forEach(fmt => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'jk-codex-tool';
        btn.textContent = _codexReportFormatLabel(fmt);
        btn.addEventListener('click', async () => {
          try {
            _codexSetStatus(`Baixando relatorio ${_codexReportFormatLabel(fmt)}...`);
            await _codexBaixarRelatorio(id, fmt);
            _codexSetStatus('Relatorio enviado para download.');
          } catch (err) {
            _codexSetStatus(_codexErroCurto(err, 'Falha ao baixar relatorio.'), true);
          }
        });
        actions.appendChild(btn);
      });
      msg.appendChild(actions);
    }

    function _codexAddReportCard(report, intro = 'Relatorio Codex gerado.') {
      const reportId = _codexRelatorioId(report);
      if (!reportId) return;
      const reportFormats = _codexReportFormats(report);
      const msg = _codexAddMsg('assistant', _codexReportChatText(report, intro), 'codex-report', { task_id: reportId, report_formats: reportFormats });
      if (!msg) return;
      _codexAppendReportActions(msg, reportId, reportFormats);
      const lista = document.getElementById('jk-codex-messages');
      lista.scrollTop = lista.scrollHeight + 9999;
    }

    async function _codexGerarRelatorioAssistente(prompt = '') {
      if (!_codexAtualizarVisibilidade()) return;
      try {
        _codexSetStatus('Interpretando pedido e consultando dados...');
        const data = await _codexFetchJson('/api/admin/codex/assistant/reports', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            prompt: String(prompt || document.getElementById('jk-codex-input')?.value || 'relatorio operacional João Pretinho').trim(),
            screen_context: _codexObterContextoTelaAtual(),
            thread_id: codexThreadId || '',
            conversation_id: _codexGetActiveConversationId(true),
          }),
        });
        if (data && data.report) {
          _codexAddReportCard(data.report);
          const steps = Array.isArray(data.report.status_steps) ? data.report.status_steps : [];
          const lastStep = steps.length ? String(steps[steps.length - 1] || '').trim() : '';
          _codexSetStatus(lastStep ? `Relatorio pronto: ${lastStep}` : 'Relatorio Codex pronto.');
          return data.report;
        }
      } catch (err) {
        _codexSetStatus(_codexErroCurto(err, 'Falha ao gerar relatorio Codex.'), true);
      }
      return null;
    }

    async function _codexMostrarCapacidades() {
      if (!_codexAtualizarVisibilidade()) return;
      try {
        _codexSetStatus('Carregando capacidades...');
        const data = await _codexFetchJson('/api/admin/codex/capabilities/modules');
        const modules = Array.isArray(data?.modules) ? data.modules : [];
        const linhas = ['O que o Joao Pretinho sabe fazer:'];
        modules.slice(0, 14).forEach(mod => {
          const total = Number(mod?.total || 0);
          if (!total) return;
          const partes = [];
          if (mod.consultar) partes.push(`${mod.consultar} consultas`);
          if (mod.diagnosticar) partes.push(`${mod.diagnosticar} diagnosticos`);
          if (mod.gerar_relatorio) partes.push(`${mod.gerar_relatorio} relatorios`);
          if (mod.preparar_acao) partes.push(`${mod.preparar_acao} preparos`);
          if (mod.executar_acao_aprovada) partes.push(`${mod.executar_acao_aprovada} acoes aprovadas`);
          linhas.push(`- ${mod.label || mod.module}: ${partes.join(', ') || `${total} capacidades`}`);
        });
        linhas.push('');
        linhas.push('Consultas rodam direto. Acoes que alteram Bling, Mercado Livre, banco, arquivos ou sincronizacao continuam exigindo aprovacao.');
        _codexAddMsg('assistant', linhas.join('\n'));
        _codexSetStatus(`Catalogo carregado: ${Number(data?.total_capabilities || 0)} capacidades.`);
      } catch (err) {
        _codexSetStatus(_codexErroCurto(err, 'Nao foi possivel carregar capacidades.'), true);
      }
    }

    async function _codexRodarAnaliseDiaria(force = false) {
      if (!_codexAtualizarVisibilidade()) return;
      try {
        const data = await _codexFetchJson('/api/admin/codex/assistant/daily-analysis/run', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ force: !!force, screen_context: _codexObterContextoTelaAtual() }),
        });
        if (data && data.suggestions) _codexRenderSugestoes(data.suggestions);
        const report = data && data.report;
        const reportId = _codexRelatorioId(report);
        if (data && data.status === 'completed' && reportId && reportId !== codexAssistantLastReportId) {
          codexAssistantLastReportId = reportId;
          _codexAddReportCard(report, 'Analise diaria de vendas e estoque concluida.');
          const steps = Array.isArray(report.status_steps) ? report.status_steps : [];
          const lastStep = steps.length ? String(steps[steps.length - 1] || '').trim() : '';
          if (lastStep) _codexSetStatus(`Analise diaria pronta: ${lastStep}`);
        }
      } catch (_) {}
    }

    function _codexIniciarAssistenteProativo() {
      if (!_codexAtualizarVisibilidade() || codexAssistantTimer) return;
      void _codexCarregarSugestoes();
      void _codexRodarProativo(false);
      void _codexRodarAnaliseDiaria(false);
      codexAssistantTimer = setInterval(() => {
        void _codexRodarProativo(false);
        void _codexRodarAnaliseDiaria(false);
      }, CODEX_PROACTIVE_INTERVAL_MS);
    }

    function _codexAtualizarVisibilidade() {
      const full = _usuarioLocalEhFull();
      document.getElementById('jk-codex-fab')?.classList.toggle('is-hidden', !full);
      if (!full && codexPanelAberto) setCodexPanelAberto(false);
      return full;
    }

    async function _codexCarregarStatus(silencioso = false) {
      if (!_codexAtualizarVisibilidade()) {
        _codexSetStatus('Codex disponivel apenas para administrador full.', true);
        return null;
      }
      if (!silencioso) _codexSetStatus('Verificando Codex...');
      try {
        const data = await _codexFetchJson('/api/admin/codex/status');
        const msg = data && data.message ? data.message : 'Status do Codex recebido.';
        _codexSetStatus(msg, !(data && data.ready));
        return data;
      } catch (err) {
        _codexSetStatus(_codexErroCurto(err, 'Nao foi possivel verificar o Codex.'), true);
        return null;
      }
    }

    function _codexMostrarAprovacao(task) {
      const box = document.getElementById('jk-codex-approval');
      const text = document.getElementById('jk-codex-approval-text');
      if (!box) return;
      const ativo = !!(task && task.status === 'awaiting_approval');
      box.classList.toggle('ativo', ativo);
      if (ativo && text) {
        text.textContent = `Confirmar execucao João Pretinho em ${task.sandbox || 'workspace_write'} para: ${(task.prompt || '').slice(0, 120)}`;
      }
    }

    function _codexAplicarTask(task) {
      if (!task) return;
      codexTaskAtual = task;
      if (task.conversation_id) _codexSetActiveConversationId(task.conversation_id);
      if (task.task_id) {
        codexHistoryTasks = [task].concat(codexHistoryTasks.filter(item => String(item?.task_id || '') !== String(task.task_id || '')));
        if (codexHistoryVisible) _codexRenderHistoricoTasks(codexHistoryTasks);
      }
      if (task.task_id) codexPollFailures[task.task_id] = 0;
      _codexMostrarAprovacao(task);
      _codexRenderRuntime(task);
      const status = String(task.status || '');
      if (status === 'awaiting_approval') {
        _codexSetStatus('Aguardando confirmacao de acesso mutavel.');
      } else if (status === 'queued') {
        _codexSetStatus(_codexStatusComDetalhe('Codex na fila...', task));
      } else if (status === 'running') {
        _codexSetStatus(_codexStatusComDetalhe('Codex trabalhando...', task));
      } else if (status === 'failed') {
        _codexSetStatus(task.error || 'Codex falhou.', true);
        if (!codexRenderedFinalTasks.has(task.task_id)) {
          codexRenderedFinalTasks.add(task.task_id);
          _codexAddMsg('assistant', task.error || 'Codex falhou.', '', { task_id: task.task_id });
        }
      } else if (status === 'completed') {
        _codexSetStatus('Codex concluiu.');
        if (task.thread_id) {
          codexThreadId = String(task.thread_id || '');
          localStorage.setItem('jk_codex_thread_id', codexThreadId);
        }
        if (!codexRenderedFinalTasks.has(task.task_id)) {
          codexRenderedFinalTasks.add(task.task_id);
          _codexAddMsg('assistant', task.final_response || 'Codex concluiu sem resposta final.', '', { task_id: task.task_id });
        }
      } else if (status === 'canceled' || status === 'cancel_requested') {
        _codexSetStatus('Tarefa Codex cancelada.');
      }
      _codexSalvarEstadoPainel({ task_id: String(task.task_id || '') });
    }

    async function _codexPollTask(taskId) {
      if (!taskId) return;
      if (codexPollTimer) clearTimeout(codexPollTimer);
      try {
        const data = await _codexFetchJson('/api/admin/codex/tasks/' + encodeURIComponent(taskId));
        const task = data && data.task;
        codexPollFailures[taskId] = 0;
        _codexAplicarTask(task);
        const status = task && String(task.status || '');
        if (['queued', 'running', 'awaiting_approval', 'cancel_requested'].includes(status)) {
          codexPollTimer = setTimeout(() => _codexPollTask(taskId), status === 'awaiting_approval' ? 3500 : 1800);
        }
      } catch (err) {
        const failures = (codexPollFailures[taskId] || 0) + 1;
        codexPollFailures[taskId] = failures;
        const baseTask = codexTaskAtual && codexTaskAtual.task_id === taskId
          ? { ...codexTaskAtual }
          : { task_id: taskId, status: 'running', logs: [] };
        const reconectando = failures < 30;
        const msg = reconectando
          ? `Reconectando ao Codex local... tentativa ${failures}`
          : _codexErroCurto(err, 'Falha ao atualizar tarefa Codex.');
        baseTask.live_status = msg;
        const logs = Array.isArray(baseTask.logs) ? baseTask.logs.slice(-7) : [];
        baseTask.logs = logs.concat([{ kind: reconectando ? 'warning' : 'error', text: msg }]);
        codexTaskAtual = baseTask;
        _codexRenderRuntime(baseTask);
        _codexSetStatus(reconectando ? 'Reconectando ao Codex local...' : msg, !reconectando);
        if (reconectando) {
          codexPollTimer = setTimeout(() => _codexPollTask(taskId), 2500);
        }
      }
    }

    function _codexActionRiskText(risk) {
      const value = String(risk || '').toLowerCase();
      if (value === 'destructive') return 'Risco alto: pode remover, cancelar, resetar ou desconectar dados.';
      if (value === 'external_write') return 'Altera dados locais e pode chamar integracoes externas.';
      return 'Altera dados locais do JK Sistema.';
    }

    function _codexActionParamLines(params) {
      const data = params && typeof params === 'object' ? params : {};
      const keys = Object.keys(data).filter(key => !['body', 'query', 'path_params'].includes(key));
      const lines = keys.map(key => `${key}: ${data[key] === true ? 'sim' : data[key] === false ? 'nao' : String(data[key] || '-')}`);
      if (data.body && typeof data.body === 'object' && Object.keys(data.body).length) lines.push('body: JSON informado');
      if (data.path_params && typeof data.path_params === 'object' && Object.keys(data.path_params).length) lines.push('path_params: JSON informado');
      return lines;
    }

    function _codexActionFormatProgress(run) {
      const progress = run && run.progress && typeof run.progress === 'object' ? run.progress : {};
      const pct = progress.percentual !== undefined ? `${progress.percentual}%` : '';
      const msg = String(progress.mensagem || progress.etapa || run?.live_status || '').trim();
      return [pct, msg].filter(Boolean).join(' - ') || String(run?.live_status || run?.status || '').trim();
    }

    function _codexFindActionRunCard(lista, runId) {
      const wanted = String(runId || '');
      return Array.from(lista.querySelectorAll('.jk-codex-action-run'))
        .find(card => String(card?.dataset?.runId || '') === wanted) || null;
    }

    function _codexRenderActionRun(run) {
      if (!run || !run.run_id) return null;
      const lista = document.getElementById('jk-codex-messages');
      if (!lista) return null;
      let card = _codexFindActionRunCard(lista, run.run_id);
      if (!card) {
        const msg = _codexAddMsg('assistant', ' ', 'codex-action-run', { persist: false, task_id: run.run_id });
        if (!msg) return null;
        msg.innerHTML = '';
        card = document.createElement('div');
        card.className = 'jk-codex-action-card jk-codex-action-run';
        card.dataset.runId = String(run.run_id);
        msg.appendChild(card);
      }
      const action = run.action || {};
      const status = String(run.status || '').toLowerCase();
      const logs = Array.isArray(run.logs) ? run.logs.slice(-4) : [];
      card.innerHTML = '';
      const title = document.createElement('div');
      title.className = 'jk-codex-action-title';
      title.textContent = action.label || 'Acao do sistema';
      const meta = document.createElement('div');
      meta.className = 'jk-codex-action-meta';
      meta.textContent = `Status: ${status || '-'}${run.run_id ? ' | ID: ' + String(run.run_id).slice(0, 8) : ''}`;
      const detail = document.createElement('div');
      detail.className = 'jk-codex-action-detail';
      detail.textContent = _codexActionFormatProgress(run) || 'Acompanhando execucao...';
      card.appendChild(title);
      card.appendChild(meta);
      card.appendChild(detail);
      if (logs.length) {
        const logBox = document.createElement('div');
        logBox.className = 'jk-codex-action-logs';
        logBox.textContent = logs.join('\n');
        card.appendChild(logBox);
      }
      if (run.error) {
        const error = document.createElement('div');
        error.className = 'jk-codex-action-error';
        error.textContent = String(run.error || '');
        card.appendChild(error);
      }
      const actions = document.createElement('div');
      actions.className = 'jk-codex-msg-actions';
      if (['queued', 'running', 'cancel_requested'].includes(status) && action.cancellable !== false) {
        const cancel = document.createElement('button');
        cancel.type = 'button';
        cancel.className = 'jk-codex-btn danger';
        cancel.textContent = 'Cancelar';
        cancel.addEventListener('click', () => _codexCancelarActionRun(run.run_id));
        actions.appendChild(cancel);
      }
      if (actions.childElementCount) card.appendChild(actions);
      lista.scrollTop = lista.scrollHeight + 9999;
      return card;
    }

    function _codexProposalRespostaTelaPayload(proposal) {
      const action = proposal && proposal.action || {};
      const actionId = String(proposal && (proposal.action_id || action.id) || '').trim();
      const params = proposal && proposal.params || {};
      if (!params || typeof params !== 'object') return null;
      const ehPergunta = actionId === 'ml.pergunta_responder';
      const ehPosVenda = actionId === 'ml.pos_venda_responder';
      if (!ehPergunta && !ehPosVenda) return null;
      const resposta = String(params.resposta || params.texto || '').trim();
      if (!resposta) return null;
      return {
        ...params,
        tipo: ehPosVenda ? 'pos_venda' : 'perguntas_anuncio',
        approval_type: ehPosVenda ? 'pos_venda' : '',
        resposta_sugerida: resposta,
        source: 'codex-action-proposal',
      };
    }

    function _codexUsarRespostaNaTela(proposal, opcoes = {}) {
      const detail = _codexProposalRespostaTelaPayload(proposal);
      if (!detail) return { ok: false, message: 'Esta proposta nao tem texto de resposta para copiar.' };
      detail.force = opcoes.force === true;
      detail.focus = opcoes.focus !== false;
      const api = window.JKPerguntasPosVenda;
      if (api && typeof api.preencherRespostaSugerida === 'function') {
        return api.preencherRespostaSugerida(detail, {
          force: detail.force,
          focus: detail.focus,
          allowFallback: false,
        });
      }
      const pagina = String((location.pathname.split('/').pop() || '').split('?')[0] || '').toLowerCase();
      if (pagina !== 'perguntas_pos_venda.html') {
        return { ok: false, message: 'Abra a tela de Perguntas e pos venda para usar a sugestao.' };
      }
      try {
        window.dispatchEvent(new CustomEvent('jk:perguntas-pos-venda:usar-resposta', { detail }));
        return { ok: true, message: 'Sugestao enviada para a tela.' };
      } catch (_) {
        return { ok: false, message: 'Nao foi possivel copiar a sugestao para a tela.' };
      }
    }

    function _codexRenderActionProposal(proposal) {
      if (!proposal || !proposal.proposal_id) return;
      const msg = _codexAddMsg('assistant', ' ', 'codex-action-proposal', { persist: false, task_id: proposal.proposal_id });
      if (!msg) return;
      msg.innerHTML = '';
      const card = document.createElement('div');
      card.className = 'jk-codex-action-card';
      card.dataset.proposalId = String(proposal.proposal_id);
      const action = proposal.action || {};
      const title = document.createElement('div');
      title.className = 'jk-codex-action-title';
      title.textContent = proposal.title || action.label || 'Funcao do sistema';
      const meta = document.createElement('div');
      meta.className = 'jk-codex-action-meta';
      meta.textContent = `${action.module || 'sistema'} | ${proposal.action_id || action.id || '-'} | ${proposal.risk || action.risk_level || 'local_write'}`
        + (proposal.resolved_from_history ? ' | contexto da conversa' : '');
      const risk = document.createElement('div');
      risk.className = 'jk-codex-action-risk';
      risk.textContent = _codexActionRiskText(proposal.risk || action.risk_level);
      const summary = document.createElement('div');
      summary.className = 'jk-codex-action-detail';
      summary.textContent = proposal.summary || 'Revise os dados antes de aprovar.';
      const params = document.createElement('div');
      params.className = 'jk-codex-action-detail';
      const paramLines = _codexActionParamLines(proposal.params || {});
      const accountLines = Array.isArray(proposal.accounts) && proposal.accounts.length
        ? ['Contas: ' + proposal.accounts.join(', ')]
        : [];
      const entityLines = Array.isArray(proposal.entities) && proposal.entities.length
        ? ['Itens: ' + proposal.entities.map(item => `${item.type || 'Item'} ${item.id || ''}`.trim()).join(', ')]
        : [];
      params.textContent = [...accountLines, ...entityLines, ...paramLines].length
        ? [...accountLines, ...entityLines, ...paramLines].join('\n')
        : 'Sem parametros adicionais.';
      const effects = document.createElement('div');
      effects.className = 'jk-codex-action-detail';
      const sideEffects = Array.isArray(proposal.side_effects) && proposal.side_effects.length ? proposal.side_effects : action.side_effects;
      effects.textContent = Array.isArray(sideEffects) && sideEffects.length
        ? sideEffects.join('\n')
        : 'Esta acao altera dados do sistema.';
      const after = document.createElement('div');
      after.className = 'jk-codex-action-detail';
      after.textContent = proposal.after_intent || (proposal.can_execute === false
        ? 'Esta proposta ainda nao possui executor seguro.'
        : 'Ao confirmar, a acao sera enfileirada.');
      const actions = document.createElement('div');
      actions.className = 'jk-codex-msg-actions';
      const respostaTelaPayload = _codexProposalRespostaTelaPayload(proposal);
      if (respostaTelaPayload) {
        const useInScreen = document.createElement('button');
        useInScreen.type = 'button';
        useInScreen.className = 'jk-codex-btn';
        useInScreen.textContent = 'Usar na caixa de resposta';
        useInScreen.addEventListener('click', () => {
          const resultado = _codexUsarRespostaNaTela(proposal, { force: true, focus: true });
          _codexSetStatus(resultado && resultado.message ? resultado.message : 'Resposta copiada para a tela.', !(resultado && resultado.ok));
        });
        actions.appendChild(useInScreen);
      }
      const approve = document.createElement('button');
      approve.type = 'button';
      approve.className = 'jk-codex-btn primary';
      approve.textContent = proposal.can_execute === false ? 'Sem executor' : 'Confirmar';
      approve.disabled = proposal.can_execute === false;
      approve.addEventListener('click', () => _codexAprovarActionProposal(proposal.proposal_id, card));
      const cancel = document.createElement('button');
      cancel.type = 'button';
      cancel.className = 'jk-codex-btn danger';
      cancel.textContent = 'Cancelar';
      cancel.addEventListener('click', () => {
        card.classList.add('is-canceled');
        approve.disabled = true;
        cancel.disabled = true;
        _codexSetStatus('Acao cancelada antes da execucao.');
      });
      actions.appendChild(approve);
      actions.appendChild(cancel);
      card.appendChild(title);
      card.appendChild(meta);
      card.appendChild(risk);
      card.appendChild(summary);
      card.appendChild(params);
      card.appendChild(effects);
      card.appendChild(after);
      card.appendChild(actions);
      msg.appendChild(card);
      _codexSetStatus(proposal.can_execute === false ? 'Proposta preparada. Executor seguro pendente.' : 'Acao reconhecida. Confirme para executar.');
      if (respostaTelaPayload) {
        window.setTimeout(() => {
          const resultado = _codexUsarRespostaNaTela(proposal, { force: false, focus: false });
          if (resultado && resultado.ok) _codexSetStatus(resultado.message || 'Resposta copiada para a tela.');
        }, 0);
      }
    }

    function _codexHistoryForActionProposal() {
      return (codexMessagesAtuais || []).slice(-14).map(item => ({
        role: String(item && item.role || ''),
        text: String(item && item.text || '').slice(0, 1000),
      })).filter(item => item.text);
    }

    function _codexHistoryForPrompt() {
      return (codexMessagesAtuais || []).slice(-24).map(item => ({
        role: String(item && item.role || ''),
        text: String(item && item.text || '').slice(0, 2400),
        task_id: String(item && item.task_id || ''),
        kind: String(item && item.kind || ''),
      })).filter(item => item.text && (item.role === 'user' || item.role === 'assistant'));
    }

    function _codexNormalizarTextoAcao(texto) {
      let value = String(texto || '').toLowerCase();
      try {
        value = value.normalize('NFD').replace(/[\u0300-\u036f]/g, '');
      } catch (_) {}
      return value.replace(/\s+/g, ' ').trim();
    }

    function _codexPedidoPareceAcaoMutavel(prompt) {
      const text = _codexNormalizarTextoAcao(prompt);
      if (!text) return false;
      if (/^(voce\s+)?(consegue|pode|sabe|tem como|e possivel|da para)\b/.test(text)) return false;
      if (/\b(verifique|verificar|olhe|analise|analisar|me diga|explique|qual|quais|como|quanto|quando|liste|listar|mostre|mostrar|consulta|consultar|busca|buscar)\b/.test(text)) return false;
      if (/\bresponda\b/.test(text) && /\b(com|texto|mensagem|resposta pronta|essa resposta|esta resposta|pergunta|pos venda|pos-venda|mercado livre|mercadolivre)\b/.test(text)) return true;
      if (/\b(envie|enviar|aprovar|aprove|publique|publicar)\b/.test(text) && /\b(pergunta|resposta|mercado livre|mercadolivre|anuncio|bling)\b/.test(text)) return true;
      return /\b(sincronize|sincronizar|sincroniza|sicronize|sicronizar|baixar|baixe|atualize|atualizar|altere|alterar|ajuste|ajustar|corrija|corrigir|mude|mudar|forcar|force|re-sincronizar|reprocessar|salve|salvar|remova|remover|exclua|excluir|limpe|limpar|cancele|cancelar|execute|executar|rode|rodar)\b/.test(text);
    }

    async function _codexTryCriarActionProposal(prompt, screenContext) {
      if (!_codexPedidoPareceAcaoMutavel(prompt)) return false;
      try {
        const data = await _codexFetchJson('/api/admin/codex/actions/proposals', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            message: prompt,
            conversation_id: _codexGetActiveConversationId(true),
            screen_context: screenContext,
            history: _codexHistoryForActionProposal(),
          }),
        });
        if (!data || data.matched === false) return false;
        if (data.needs_input) {
          _codexAddMsg('assistant', data.message || 'Preciso de mais parametros para executar essa acao.');
          _codexSetStatus('Parametros obrigatorios ausentes.', true);
          return true;
        }
        if (data.proposal) {
          _codexRenderActionProposal(data.proposal);
          return true;
        }
        return false;
      } catch (err) {
        if (err && (err.status === 404 || err.status === 405)) return false;
        _codexSetStatus(_codexErroCurto(err, 'Falha ao preparar acao do sistema.'), true);
        _codexAddMsg('assistant', _codexErroCurto(err, 'Falha ao preparar acao do sistema.'));
        return true;
      }
    }

    async function _codexAprovarActionProposal(proposalId, card) {
      const id = String(proposalId || '').trim();
      if (!id) return;
      if (card) {
        card.querySelectorAll('button').forEach(btn => { btn.disabled = true; });
        card.classList.add('is-approved');
      }
      _codexSetStatus('Confirmando acao do sistema...');
      try {
        const data = await _codexFetchJson('/api/admin/codex/actions/proposals/' + encodeURIComponent(id) + '/approve', {
          method: 'POST',
        });
        const run = data && data.run;
        if (run && run.run_id) {
          codexActionRunAtual = run;
          _codexRenderActionRun(run);
          _codexPollActionRun(run.run_id);
        }
      } catch (err) {
        _codexSetStatus(_codexErroCurto(err, 'Falha ao aprovar acao.'), true);
        if (card) card.querySelectorAll('button').forEach(btn => { btn.disabled = false; });
      }
    }

    async function _codexPollActionRun(runId) {
      const id = String(runId || '').trim();
      if (!id) return;
      if (codexActionPollTimer) clearTimeout(codexActionPollTimer);
      try {
        const data = await _codexFetchJson('/api/admin/codex/actions/runs/' + encodeURIComponent(id));
        const run = data && data.run;
        codexActionRunAtual = run || codexActionRunAtual;
        _codexRenderActionRun(run);
        const status = String(run && run.status || '').toLowerCase();
        const progressText = _codexActionFormatProgress(run);
        if (progressText) _codexSetStatus(progressText);
        if (['queued', 'running', 'cancel_requested'].includes(status)) {
          codexActionPollTimer = setTimeout(() => _codexPollActionRun(id), 1800);
        } else if (status === 'completed') {
          _codexSetStatus('Acao do sistema concluida.');
        } else if (status === 'failed') {
          _codexSetStatus(run.error || 'Acao do sistema falhou.', true);
        }
      } catch (err) {
        _codexSetStatus(_codexErroCurto(err, 'Falha ao atualizar acao.'), true);
      }
    }

    async function _codexCancelarActionRun(runId) {
      const id = String(runId || '').trim();
      if (!id) return;
      try {
        const data = await _codexFetchJson('/api/admin/codex/actions/runs/' + encodeURIComponent(id) + '/cancel', {
          method: 'POST',
        });
        _codexRenderActionRun(data && data.run);
        _codexSetStatus('Cancelamento solicitado.');
      } catch (err) {
        _codexSetStatus(_codexErroCurto(err, 'Falha ao cancelar acao.'), true);
      }
    }

    async function _codexCriarTarefa(forcedAccess = '') {
      const input = document.getElementById('jk-codex-input');
      const promptDigitado = String(input && input.value || '').trim();
      if (_codexTemUploadPendente()) {
        _codexSetStatus('Aguarde o envio dos anexos antes de enviar a tarefa.', true);
        return;
      }
      const settings = _codexCollectSettings(forcedAccess);
      const pathsParaTarefa = _codexPathsParaTarefa(settings.paths);
      const temAnexos = _codexAttachmentPaths().length > 0;
      const prompt = promptDigitado || (temAnexos ? 'Analise os arquivos enviados.' : '');
      if (!prompt) return;
      if (!forcedAccess) _codexPersistSettings();
      const screenContext = _codexObterContextoTelaAtual();
      _codexGetActiveConversationId(true);
      _codexAddMsg('user', prompt);
      if (input) input.value = '';
      if (!temAnexos && _codexPedidoPareceAcaoMutavel(prompt)) {
        _codexSetStatus('Verificando se o pedido corresponde a uma acao aprovavel...');
        const actionHandled = await _codexTryCriarActionProposal(prompt, screenContext);
        if (actionHandled) return;
      }
      _codexSetStatus('Enviando tarefa ao Codex com contexto da tela...');
      try {
        const data = await _codexFetchJson('/api/admin/codex/tasks', {
          method: 'POST',
          body: JSON.stringify({
            prompt,
            sandbox: settings.sandbox,
            approval_mode: settings.approval_mode,
            model: settings.model,
            reasoning_effort: settings.reasoning_effort,
            speed: settings.speed,
            service_tier: settings.service_tier,
            goal: settings.goal,
            planning_mode: settings.planning_mode,
            paths: pathsParaTarefa,
            screen_context: screenContext,
            history: _codexHistoryForPrompt(),
            thread_id: codexThreadId || '',
            conversation_id: _codexGetActiveConversationId(true),
          }),
        });
        const task = data && data.task;
        if (task && task.task_id) {
          for (let i = codexMessagesAtuais.length - 1; i >= 0; i -= 1) {
            const item = codexMessagesAtuais[i];
            if (item && item.role === 'user' && !item.task_id && String(item.text || '') === prompt) {
              item.task_id = String(task.task_id || '');
              break;
            }
          }
          _codexSalvarHistoricoLocal();
          codexHistoryTasks = [task].concat(codexHistoryTasks.filter(t => String(t?.task_id || '') !== String(task.task_id || '')));
          if (codexHistoryVisible) _codexRenderHistoricoTasks(codexHistoryTasks);
        }
        _codexAplicarTask(task);
        codexUploadedAttachments = [];
        _codexRenderAttachmentChips();
        if (task && task.task_id) _codexPollTask(task.task_id);
      } catch (err) {
        const msg = _codexErroCurto(err, 'Falha ao criar tarefa Codex.');
        _codexSetStatus(msg, true);
        _codexAddMsg('assistant', msg);
      }
    }

    async function _codexAprovarAtual() {
      const taskId = codexTaskAtual && codexTaskAtual.task_id;
      if (!taskId) return;
      _codexSetStatus('Confirmando execucao Codex...');
      try {
        const data = await _codexFetchJson('/api/admin/codex/tasks/' + encodeURIComponent(taskId) + '/approve', { method: 'POST' });
        const task = data && data.task;
        _codexAplicarTask(task);
        _codexPollTask(taskId);
      } catch (err) {
        _codexSetStatus(_codexErroCurto(err, 'Falha ao aprovar tarefa Codex.'), true);
      }
    }

    async function _codexCancelarAtual() {
      const taskId = codexTaskAtual && codexTaskAtual.task_id;
      if (!taskId) return;
      try {
        const data = await _codexFetchJson('/api/admin/codex/tasks/' + encodeURIComponent(taskId) + '/cancel', { method: 'POST' });
        _codexAplicarTask(data && data.task);
        _codexMostrarAprovacao(null);
      } catch (err) {
        _codexSetStatus(_codexErroCurto(err, 'Falha ao cancelar tarefa Codex.'), true);
      }
    }
