/**
 * JK Sistema - IA Sidebar Universal
 * Loader leve: mostra um botao rapido e carrega a sidebar completa sob demanda.
 */
(function () {
  'use strict';

  const VERSION = '20260714-black-jhon-operational-agent-v1';
  const FULL_AUTO_LOAD_DELAY_MS = 30000;
  const WORKER_PREFETCH_DELAY_MS = 10000;
  const FULL_RELOAD_AFTER_NAV_DELAY_MS = 500;
  const WORKER_TIMEOUT_MS = 25000;
  const USER_CLICK_PREFETCH_WAIT_MS = 1200;
  const FULL_SESSION_KEY = 'jk_ia_sidebar_full_loaded_session_v1';
  const CHUNKS = [
    "01-bootstrap-storage-render.part.js",
    "02-ui-modelos.part.js",
    "03-init-shell-codex.part.js",
    "04-mensagens-notificacoes.part.js",
    "05-perguntas-monitor.part.js",
    "06-mensagens-contatos-anexos.part.js",
    "07-mensagens-fluxo.part.js",
    "08-aprovacoes.part.js",
    "09-chat-bootstrap.part.js"
  ];

  if (window.__JK_IA_SIDEBAR_BOOTSTRAPPED__ || window.__JK_IA_SIDEBAR_LIGHT_LOADER__) return;
  window.__JK_IA_SIDEBAR_LIGHT_LOADER__ = true;

  let worker = null;
  let workerIndisponivel = false;
  let workerRequestSeq = 0;
  const workerPendencias = new Map();
  let prefetchedPartsPromise = null;
  let fullLoadingPromise = null;
  let fullLoadingIsAuto = false;
  let abrirDepoisDeCarregar = false;
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

  function chunkUrl(fileName) {
    return '/ia-sidebar/' + fileName + '?v=' + VERSION;
  }

  function safeJson(raw, fallback) {
    try { return JSON.parse(raw || ''); } catch (_) { return fallback; }
  }

  function moduloAtualKey() {
    const arquivo = String((location.pathname.split('/').pop() || '').split('?')[0] || '').toLowerCase();
    return MODULO_ATUAL_ALIASES[arquivo] || '';
  }

  function leftSidebarPermitidoNaTela() {
    const arquivo = String((location.pathname.split('/').pop() || '').split('?')[0] || '').toLowerCase();
    return !['', 'dashboard.html', 'frontend_index.html', 'index.html'].includes(arquivo);
  }

  function moduloPermitido(mod) {
    const permissions = safeJson(localStorage.getItem('permissions') || '{}', {});
    if (!permissions || !Object.keys(permissions).length) return true;
    if (Array.isArray(mod.children)) return mod.children.some(child => moduloPermitido(child));
    return permissions.full === true || permissions[mod.key] === true;
  }

  function filtrarModulosVisiveis(atual) {
    return MODULOS_LATERAIS.map((mod) => {
      if (Array.isArray(mod.children)) {
        const filhos = mod.children.filter(child => child.key !== atual && moduloPermitido(child));
        return filhos.length ? { ...mod, children: filhos } : null;
      }
      if (mod.key === atual || !moduloPermitido(mod)) return null;
      return mod;
    }).filter(Boolean);
  }

  function jaCarregouSidebarCompletaNaSessao() {
    try { return sessionStorage.getItem(FULL_SESSION_KEY) === '1'; } catch (_) { return false; }
  }

  function marcarSidebarCompletaNaSessao() {
    try { sessionStorage.setItem(FULL_SESSION_KEY, '1'); } catch (_) {}
  }

  function ensureWorker() {
    if (workerIndisponivel) return null;
    if (worker) return worker;
    if (typeof Worker !== 'function') {
      workerIndisponivel = true;
      return null;
    }
    try {
      worker = new Worker('/ia-sidebar-worker.js?v=' + VERSION);
      window.__JK_IA_SIDEBAR_WORKER__ = worker;
      worker.addEventListener('message', (event) => {
        const payload = event.data || {};
        const pendencia = workerPendencias.get(payload.id);
        if (!pendencia) return;
        window.clearTimeout(pendencia.timer);
        workerPendencias.delete(payload.id);
        if (payload.ok === false) {
          pendencia.reject(new Error(payload.error || 'Worker da sidebar falhou.'));
          return;
        }
        pendencia.resolve(payload);
      });
      worker.addEventListener('error', (event) => {
        workerIndisponivel = true;
        worker = null;
        const erro = new Error((event && event.message) || 'Worker da sidebar indisponivel.');
        workerPendencias.forEach((pendencia) => {
          window.clearTimeout(pendencia.timer);
          pendencia.reject(erro);
        });
        workerPendencias.clear();
      });
    } catch (error) {
      workerIndisponivel = true;
      worker = null;
    }
    return worker;
  }

  function workerRequest(type, payload, timeoutMs) {
    const alvo = ensureWorker();
    if (!alvo) return Promise.reject(new Error('Worker indisponivel.'));
    const id = 'sidebar-' + Date.now() + '-' + (++workerRequestSeq);
    return new Promise((resolve, reject) => {
      const timer = window.setTimeout(() => {
        workerPendencias.delete(id);
        reject(new Error('Timeout no worker da sidebar: ' + type));
      }, timeoutMs || WORKER_TIMEOUT_MS);
      workerPendencias.set(id, { resolve, reject, timer });
      alvo.postMessage({ id, type, ...(payload || {}) });
    });
  }

  function criarRespostaWorker(payload) {
    return {
      ok: !!payload.responseOk,
      status: Number(payload.status || 0),
      statusText: String(payload.statusText || ''),
      url: String(payload.url || ''),
      headers: new Headers(payload.headers || {}),
      json: async () => payload.hasJson ? payload.json : JSON.parse(payload.text || '{}'),
      text: async () => String(payload.text || ''),
    };
  }

  function normalizarHeaders(headers) {
    const saida = {};
    if (!headers) return saida;
    if (headers instanceof Headers) {
      headers.forEach((value, key) => { saida[key] = value; });
      return saida;
    }
    if (Array.isArray(headers)) {
      headers.forEach((item) => {
        if (Array.isArray(item) && item.length >= 2) saida[item[0]] = item[1];
      });
      return saida;
    }
    Object.keys(headers || {}).forEach((key) => { saida[key] = headers[key]; });
    return saida;
  }

  function deveUsarWorkerJson(input, init) {
    if (!ensureWorker()) return false;
    if (typeof Request !== 'undefined' && input instanceof Request) return false;
    const url = String(input || '');
    if (!url || (!url.startsWith('/api/') && !/^https?:\/\/(?:127\.0\.0\.1|localhost):\d+\/api\//i.test(url))) return false;
    const options = init || {};
    const method = String(options.method || 'GET').toUpperCase();
    if (!/^(GET|POST|PUT|PATCH|DELETE)$/.test(method)) return false;
    if (options.signal) return false;
    if (options.body != null && typeof options.body !== 'string') return false;
    return true;
  }

  function serializarFetchOptions(init) {
    const options = init || {};
    const out = {
      method: options.method || 'GET',
      headers: normalizarHeaders(options.headers),
      cache: options.cache || 'no-store',
      credentials: options.credentials || 'same-origin',
    };
    if (options.body != null) out.body = options.body;
    if (options.mode) out.mode = options.mode;
    return out;
  }

  window.__JK_IA_SIDEBAR_FETCH__ = function sidebarFetch(input, init) {
    if (!deveUsarWorkerJson(input, init)) return fetch(input, init);
    return workerRequest('jsonFetch', {
      url: String(input),
      options: serializarFetchOptions(init),
    }, WORKER_TIMEOUT_MS)
      .then(criarRespostaWorker)
      .catch(() => fetch(input, init));
  };

  function iniciarPrefetchWorker() {
    if (prefetchedPartsPromise || window.__JK_IA_SIDEBAR_BOOTSTRAPPED__) return prefetchedPartsPromise;
    prefetchedPartsPromise = workerRequest('prefetchChunks', {
      files: CHUNKS.map(chunkUrl),
    }, WORKER_TIMEOUT_MS).then((payload) => {
      const partes = Array.isArray(payload.parts) ? payload.parts : [];
      if (partes.length !== CHUNKS.length) throw new Error('Prefetch incompleto da sidebar.');
      return partes;
    }).catch((error) => {
      console.warn('[IA Sidebar] Prefetch por worker indisponivel. Usando fallback.', error);
      return null;
    });
    return prefetchedPartsPromise;
  }

  async function carregarChunksNativo() {
    return Promise.all(CHUNKS.map(async (fileName) => {
      const response = await fetch(chunkUrl(fileName), { cache: 'no-store' });
      if (!response.ok) {
        throw new Error('Falha ao carregar '+fileName+': HTTP '+response.status);
      }
      return response.text();
    }));
  }

  function resolverDepois(ms, valor) {
    return new Promise(resolve => window.setTimeout(() => resolve(valor), ms));
  }

  async function obterPartesParaCarregamento(options) {
    const opts = options || {};
    if (opts.openAfterLoad) {
      if (prefetchedPartsPromise) {
        const partesRapidas = await Promise.race([
          prefetchedPartsPromise,
          resolverDepois(USER_CLICK_PREFETCH_WAIT_MS, null),
        ]);
        if (partesRapidas) return partesRapidas;
      }
      return carregarChunksNativo();
    }
    const partesPrefetch = await iniciarPrefetchWorker();
    return partesPrefetch || await carregarChunksNativo();
  }

  function removerBotaoLeve() {
    document.getElementById('jk-ia-light-fab')?.remove();
    document.getElementById('jk-ia-light-fab-style')?.remove();
  }

  function removerLeftSidebarLeve() {
    const left = document.getElementById('jk-left-sidebar-hotspot');
    if (left && left.dataset.jkLightSidebar === '1') left.remove();
    document.getElementById('jk-left-sidebar-light-style')?.remove();
  }

  function instalarLeftSidebarLeve() {
    if (!leftSidebarPermitidoNaTela()) return;
    if (document.getElementById('jk-left-sidebar-hotspot')) return;

    const style = document.createElement('style');
    style.id = 'jk-left-sidebar-light-style';
    style.textContent = `
      #jk-left-sidebar-hotspot{position:fixed;top:0;left:0;bottom:0;width:18px;z-index:10000;pointer-events:auto}
      #jk-left-sidebar-hotspot::before{content:"";position:absolute;top:0;left:0;bottom:0;width:8px;background:transparent}
      #jk-left-sidebar-menu{position:fixed;top:50%;left:10px;width:360px;max-height:calc(100vh - 28px);overflow-x:hidden;overflow-y:auto;overscroll-behavior:contain;z-index:10001;display:flex;flex-direction:column;align-items:flex-start;gap:8px;padding:7px 0;transform:translate(calc(-100% - 28px),-50%);opacity:0;pointer-events:none;transition:transform .2s cubic-bezier(.4,0,.2,1),opacity .16s ease;scrollbar-width:none;-ms-overflow-style:none}
      #jk-left-sidebar-menu::-webkit-scrollbar{width:0;height:0;display:none}
      #jk-left-sidebar-hotspot:hover #jk-left-sidebar-menu,#jk-left-sidebar-hotspot:focus-within #jk-left-sidebar-menu{transform:translate(0,-50%);opacity:1;pointer-events:auto}
      .jk-left-module-link{position:relative;width:344px;min-height:46px;display:flex;align-items:center;text-decoration:none;color:#e8fffb;overflow:visible;border:0;background:transparent;outline:none;padding:0;cursor:pointer;font:inherit}
      .jk-left-module-icon{position:relative;z-index:2;width:46px;height:46px;border-radius:14px;border:1px solid rgba(120,227,212,.32);display:flex;align-items:center;justify-content:center;background:linear-gradient(145deg,#0f6bbd,#13b99a);box-shadow:0 4px 16px rgba(19,196,160,.34);font-size:1.25rem;line-height:1;transform:scale(1);transform-origin:left center;will-change:transform;transition:transform .18s ease,box-shadow .18s ease,border-color .18s ease}
      .jk-left-module-glyph{display:inline-flex;align-items:center;justify-content:center;line-height:1;transform:rotate(0deg) scale(1);transform-origin:center center;will-change:transform;animation:none}
      .jk-left-module-label{position:absolute;left:78px;top:50%;z-index:5;min-width:118px;max-width:244px;min-height:34px;display:flex;align-items:center;padding:0 13px 0 14px;border-radius:12px;border:1px solid rgba(120,227,212,.3);background:linear-gradient(90deg,rgba(8,43,59,.96),rgba(6,71,84,.92));box-shadow:0 8px 22px rgba(0,0,0,.34);color:#e8fffb;font-size:.76rem;font-weight:900;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;transform:translate(-18px,-50%) scaleX(.25);transform-origin:left center;opacity:0;transition:transform .16s cubic-bezier(.2,.9,.2,1),opacity .12s ease}
      @keyframes jkLeftLightGlyphSpin{from{transform:rotate(0deg) scale(1.14)}to{transform:rotate(360deg) scale(1.14)}}
      .jk-left-module-link:hover .jk-left-module-icon,.jk-left-module-icon:hover,.jk-left-module-link:focus-visible .jk-left-module-icon,.jk-left-module-link.jk-left-module-zoom .jk-left-module-icon{transform:translateX(8px) scale(1.7);border-color:rgba(255,255,255,.82);box-shadow:0 0 0 5px rgba(120,227,212,.22),0 10px 28px rgba(19,196,160,.58)}
      .jk-left-module-link:hover .jk-left-module-glyph,.jk-left-module-icon:hover .jk-left-module-glyph,.jk-left-module-link:focus-visible .jk-left-module-glyph,.jk-left-module-link.jk-left-module-zoom .jk-left-module-glyph{animation:jkLeftLightGlyphSpin .58s linear infinite}
      .jk-left-module-link:hover .jk-left-module-label,.jk-left-module-link:focus-visible .jk-left-module-label,.jk-left-module-group:hover .jk-left-module-label,.jk-left-module-group:focus-within .jk-left-module-label,.jk-left-module-group.submenu-aberto .jk-left-module-label{transform:translate(0,-50%) scaleX(1);opacity:1}
      .jk-left-module-link.modulo-atual{display:none}
      .jk-left-module-group{position:relative;width:344px;min-height:46px;overflow:visible}
      .jk-left-module-group-trigger{width:344px;text-align:left}
      .jk-left-module-submenu{position:absolute;left:78px;top:39px;z-index:9;min-width:178px;max-width:244px;display:flex;flex-direction:column;gap:4px;padding:7px;border-radius:13px;border:1px solid rgba(120,227,212,.32);background:linear-gradient(180deg,rgba(8,43,59,.98),rgba(6,71,84,.96));box-shadow:0 12px 28px rgba(0,0,0,.38);transform:translate(-18px,-6px) scale(.96);transform-origin:left top;opacity:0;pointer-events:none;transition:transform .16s cubic-bezier(.2,.9,.2,1),opacity .12s ease}
      .jk-left-module-group:hover .jk-left-module-submenu,.jk-left-module-group:focus-within .jk-left-module-submenu,.jk-left-module-group.submenu-aberto .jk-left-module-submenu{transform:translate(0,0) scale(1);opacity:1;pointer-events:auto}
      .jk-left-submodule-link{display:flex;align-items:center;gap:8px;min-height:32px;padding:6px 8px;border-radius:9px;color:#e8fffb;text-decoration:none;font-size:.74rem;font-weight:900;line-height:1.12;white-space:nowrap;outline:none}
      .jk-left-submodule-link:hover,.jk-left-submodule-link:focus-visible{background:rgba(120,227,212,.14);box-shadow:inset 0 0 0 1px rgba(120,227,212,.22)}
      .jk-left-submodule-icon{width:23px;height:23px;display:inline-flex;align-items:center;justify-content:center;border-radius:8px;background:rgba(255,255,255,.08);font-size:.94rem;flex:0 0 auto}
    `;
    document.head.appendChild(style);

    const hotspot = document.createElement('div');
    hotspot.id = 'jk-left-sidebar-hotspot';
    hotspot.dataset.jkLightSidebar = '1';
    hotspot.setAttribute('aria-label', 'Menu lateral esquerdo');
    hotspot.tabIndex = 0;
    const menu = document.createElement('nav');
    menu.id = 'jk-left-sidebar-menu';
    menu.setAttribute('aria-label', 'Modulos do sistema');
    hotspot.appendChild(menu);
    document.body.appendChild(hotspot);

    const atual = moduloAtualKey();
    const modulos = filtrarModulosVisiveis(atual);
    const aplicarZoomLink = (link, icon, glyph) => {
      const ativarZoom = () => {
        icon.style.setProperty('transform', 'translateX(8px) scale(1.7)', 'important');
        icon.style.setProperty('border-color', 'rgba(255,255,255,.82)', 'important');
        icon.style.setProperty('box-shadow', '0 0 0 5px rgba(120,227,212,.22),0 10px 28px rgba(19,196,160,.58)', 'important');
        glyph.style.setProperty('animation', 'jkLeftLightGlyphSpin .52s linear infinite', 'important');
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
    const criarLinkModulo = (mod, options) => {
      const opts = options || {};
      const isButton = opts.button === true;
      const link = document.createElement(isButton ? 'button' : 'a');
      link.className = opts.extraClass ? 'jk-left-module-link ' + opts.extraClass : 'jk-left-module-link';
      if (isButton) {
        link.type = 'button';
        link.setAttribute('aria-expanded', 'false');
      } else {
        link.href = mod.href;
      }
      link.title = mod.label;
      link.setAttribute('aria-label', opts.group ? 'Abrir submenu ' + mod.label : 'Abrir modulo ' + mod.label);
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
      link.setAttribute('aria-label', 'Abrir modulo ' + mod.label);
      link.innerHTML = '<span class="jk-left-submodule-icon" aria-hidden="true">' + mod.icon + '</span><span>' + mod.label + '</span>';
      return link;
    };

    modulos.forEach((mod) => {
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
  }

  function marcarBotaoCarregando(carregando, erro) {
    const botao = document.getElementById('jk-ia-light-fab');
    if (!botao) return;
    botao.classList.toggle('loading', !!carregando);
    botao.classList.toggle('error', !!erro);
    botao.title = erro ? 'Nao foi possivel carregar o Black Jhon. Clique para tentar novamente.' : 'Abrir Black Jhon';
  }

  function abrirPainelQuandoDisponivel(tentativas) {
    const fab = document.getElementById('jk-ia-fab');
    if (fab) {
      if (!document.getElementById('jk-codex-panel')?.classList.contains('aberto')) fab.click();
      abrirDepoisDeCarregar = false;
      return;
    }
    if ((tentativas || 0) >= 40) return;
    window.setTimeout(() => abrirPainelQuandoDisponivel((tentativas || 0) + 1), 120);
  }

  async function loadFullSidebar(options) {
    const opts = options || {};
    abrirDepoisDeCarregar = abrirDepoisDeCarregar || !!opts.openAfterLoad;

    if (window.__JK_IA_SIDEBAR_BOOTSTRAPPED__) {
      removerBotaoLeve();
      if (abrirDepoisDeCarregar) abrirPainelQuandoDisponivel();
      return;
    }
    if (fullLoadingPromise) {
      if (!opts.openAfterLoad || !fullLoadingIsAuto) return fullLoadingPromise;
      fullLoadingPromise = null;
      fullLoadingIsAuto = false;
    }

    window.__JK_IA_SIDEBAR_SOURCE_LOADING__ = true;
    fullLoadingIsAuto = !opts.openAfterLoad;
    marcarBotaoCarregando(true, false);
    fullLoadingPromise = (async () => {
      const partes = await obterPartesParaCarregamento(opts);
      if (window.__JK_IA_SIDEBAR_BOOTSTRAPPED__ || document.getElementById('jk-ia-fab')) return;
      const script = document.createElement('script');
      script.text = partes.join('\n') + '\n//# sourceURL=/ia-sidebar/combined.js';
      removerLeftSidebarLeve();
      (document.head || document.documentElement).appendChild(script);
      window.__JK_IA_SIDEBAR_SOURCE_LOADING__ = false;
      window.setTimeout(() => {
        if (document.getElementById('jk-ia-fab')) {
          marcarSidebarCompletaNaSessao();
          fullLoadingIsAuto = false;
          removerBotaoLeve();
          if (abrirDepoisDeCarregar) abrirPainelQuandoDisponivel();
          return;
        }
        window.__JK_IA_SIDEBAR_BOOTSTRAPPED__ = false;
        fullLoadingPromise = null;
        fullLoadingIsAuto = false;
        instalarLeftSidebarLeve();
        marcarBotaoCarregando(false, true);
        console.error('[IA Sidebar] Bundle carregou, mas o painel principal nao foi criado.');
      }, 0);
    })().catch((error) => {
      window.__JK_IA_SIDEBAR_SOURCE_LOADING__ = false;
      fullLoadingPromise = null;
      fullLoadingIsAuto = false;
      marcarBotaoCarregando(false, true);
      console.error('[IA Sidebar] Nao foi possivel carregar os arquivos isolados.', error);
      throw error;
    });
    return fullLoadingPromise;
  }

  window.__JK_IA_SIDEBAR_LOAD_FULL__ = loadFullSidebar;

  function instalarBotaoLeve() {
    if (document.getElementById('jk-ia-light-fab')) return;
    const style = document.createElement('style');
    style.id = 'jk-ia-light-fab-style';
    style.textContent = `
      #jk-ia-light-fab{position:fixed;right:18px;bottom:92px;z-index:10000;width:46px;height:46px;border:1px solid rgba(89,206,255,.55);border-radius:999px;background:#101c37;color:#fff;font:800 14px/1 Arial,sans-serif;box-shadow:0 10px 26px rgba(0,0,0,.34),0 0 18px rgba(63,169,245,.28);cursor:pointer;letter-spacing:0;overflow:hidden;padding:0}
      #jk-ia-light-fab img{display:block;width:100%;height:100%;object-fit:cover;transform:scale(1.12);transform-origin:center}
      #jk-ia-light-fab:hover{transform:translateY(-1px);box-shadow:0 12px 30px rgba(0,0,0,.38),0 0 22px rgba(63,169,245,.38)}
      #jk-ia-light-fab.loading{cursor:wait;opacity:.78}
      #jk-ia-light-fab.loading::after{content:'';position:absolute;inset:6px;border:2px solid rgba(255,255,255,.26);border-top-color:#fff;border-radius:50%;animation:jkIaLightSpin .8s linear infinite}
      #jk-ia-light-fab.error{background:linear-gradient(135deg,#6b1b2a,#d8405f);border-color:rgba(255,132,151,.7)}
      @keyframes jkIaLightSpin{to{transform:rotate(360deg)}}
    `;
    document.head.appendChild(style);

    const botao = document.createElement('button');
    botao.id = 'jk-ia-light-fab';
    botao.type = 'button';
    botao.innerHTML = '<img src="/assets/joao-pretinho-icon.png?v=20260710-black-jhon" alt="">';
    botao.title = 'Abrir Black Jhon';
    botao.setAttribute('aria-label', 'Abrir Black Jhon');
    botao.addEventListener('click', () => {
      loadFullSidebar({ openAfterLoad: true }).catch(() => {});
    });
    document.body.appendChild(botao);
  }

  function iniciarLoaderLeve() {
    const params = new URLSearchParams(location.search || '');
    if (params.get('embed') === 'share' || params.get('modo') === 'compartilhar' || document.body?.classList.contains('share-embed')) {
      return;
    }
    instalarLeftSidebarLeve();
    instalarBotaoLeve();

    if (jaCarregouSidebarCompletaNaSessao()) {
      window.setTimeout(() => {
        loadFullSidebar({ openAfterLoad: false }).catch(() => {});
      }, FULL_RELOAD_AFTER_NAV_DELAY_MS);
      return;
    }

    window.setTimeout(() => { iniciarPrefetchWorker(); }, WORKER_PREFETCH_DELAY_MS);
    window.setTimeout(() => {
      loadFullSidebar({ openAfterLoad: false }).catch(() => {});
    }, FULL_AUTO_LOAD_DELAY_MS);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', iniciarLoaderLeve, { once: true });
  } else {
    iniciarLoaderLeve();
  }
})();
