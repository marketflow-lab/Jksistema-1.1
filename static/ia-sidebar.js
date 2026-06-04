/**
 * JK Sistema — IA Sidebar Universal
 * Widget flutuante de chat IA com persistência de conversas por usuário.
 * Inclua este arquivo no <body> de qualquer módulo para ativar.
 */
(function () {
  'use strict';

  function _jkAplicarZoomSidebarEsquerdoExistente() {
    const styleId = 'jk-left-sidebar-zoom-patch';
    if (!document.getElementById(styleId)) {
      const style = document.createElement('style');
      style.id = styleId;
      style.textContent = `
        @keyframes jkLeftModuleGlyphSpin{from{transform:rotate(0deg) scale(1.14);}to{transform:rotate(360deg) scale(1.14);}}
        #jk-left-sidebar-menu{width:360px!important;overflow-x:hidden!important;overflow-y:auto!important;overscroll-behavior:contain!important;scrollbar-width:none!important;-ms-overflow-style:none!important;z-index:10001!important;}
        #jk-left-sidebar-menu::-webkit-scrollbar{width:0!important;height:0!important;display:none!important;}
        .jk-left-module-link{overflow:visible!important;}
        .jk-left-module-label{left:78px!important;z-index:5!important;max-width:244px!important;padding-left:14px!important;border-left:1px solid rgba(120,227,212,.3)!important;border-radius:12px!important;}
        .jk-left-module-icon{transform-origin:left center!important;transition:transform .14s ease,box-shadow .14s ease,border-color .14s ease!important;}
        .jk-left-module-link:hover>.jk-left-module-icon,
        .jk-left-module-link.jk-left-module-zoom>.jk-left-module-icon{transform:translateX(8px) scale(1.7)!important;border-color:rgba(255,255,255,.82)!important;box-shadow:0 0 0 5px rgba(120,227,212,.22),0 10px 28px rgba(19,196,160,.58)!important;}
        .jk-left-module-link:hover .jk-left-module-glyph,
        .jk-left-module-link.jk-left-module-zoom .jk-left-module-glyph{animation:jkLeftModuleGlyphSpin .52s linear infinite!important;}
      `;
      document.head.appendChild(style);
    }

    const normalizarGlyph = (icon) => {
      if (!icon) return null;
      let glyph = Array.from(icon.children || []).find(child => child && child.classList && child.classList.contains('jk-left-module-glyph'));
      if (glyph) return glyph;
      glyph = document.createElement('span');
      glyph.className = 'jk-left-module-glyph';
      while (icon.firstChild) glyph.appendChild(icon.firstChild);
      icon.appendChild(glyph);
      return glyph;
    };

    const ativar = (link) => {
      if (!link) return;
      const icon = link.querySelector('.jk-left-module-icon');
      const glyph = normalizarGlyph(icon);
      link.classList.add('jk-left-module-zoom');
      if (icon) {
        icon.style.setProperty('transform', 'translateX(8px) scale(1.7)', 'important');
        icon.style.setProperty('border-color', 'rgba(255,255,255,.82)', 'important');
        icon.style.setProperty('box-shadow', '0 0 0 5px rgba(120,227,212,.22),0 10px 28px rgba(19,196,160,.58)', 'important');
      }
      if (glyph) glyph.style.setProperty('animation', 'jkLeftModuleGlyphSpin .52s linear infinite', 'important');
    };

    const desativar = (link) => {
      if (!link) return;
      const icon = link.querySelector('.jk-left-module-icon');
      const glyph = icon ? Array.from(icon.children || []).find(child => child && child.classList && child.classList.contains('jk-left-module-glyph')) : null;
      link.classList.remove('jk-left-module-zoom');
      if (icon) {
        icon.style.removeProperty('transform');
        icon.style.removeProperty('border-color');
        icon.style.removeProperty('box-shadow');
      }
      if (glyph) glyph.style.removeProperty('animation');
    };

    document.querySelectorAll('.jk-left-module-icon').forEach(normalizarGlyph);
    if (!window.__JK_LEFT_SIDEBAR_ZOOM_PATCH_BOUND__) {
      window.__JK_LEFT_SIDEBAR_ZOOM_PATCH_BOUND__ = true;
      let ativo = null;
      document.addEventListener('mousemove', (event) => {
        const alvo = event.target && event.target.closest ? event.target.closest('.jk-left-module-link') : null;
        if (alvo === ativo) return;
        desativar(ativo);
        ativo = alvo;
        ativar(ativo);
      }, true);
      document.addEventListener('mouseleave', () => {
        desativar(ativo);
        ativo = null;
      }, true);
      try {
        const observer = new MutationObserver(() => {
          document.querySelectorAll('.jk-left-module-icon').forEach(normalizarGlyph);
        });
        observer.observe(document.documentElement, { childList: true, subtree: true });
      } catch (_) {}
    }
  }

  if (document.readyState === 'loading')
    document.addEventListener('DOMContentLoaded', _jkAplicarZoomSidebarEsquerdoExistente);
  else
    _jkAplicarZoomSidebarEsquerdoExistente();

  if (window.__JK_IA_SIDEBAR_BOOTSTRAPPED__) return;
  window.__JK_IA_SIDEBAR_BOOTSTRAPPED__ = true;
  if (document.getElementById('jk-ia-panel') || document.getElementById('jk-ia-fab')) {
    _jkAplicarZoomSidebarEsquerdoExistente();
    return;
  }

  /* ── Utilitários de auth ── */
  function _token() { return localStorage.getItem('access_token') || ''; }
  function _clientId() {
    try { return (JSON.parse(localStorage.getItem('user_data') || '{}')).client_id || 'default'; }
    catch (_) { return 'default'; }
  }
  function _authHeaders() {
    const t = _token();
    const c = _clientId();
    const h = { 'Content-Type': 'application/json' };
    if (t) h['Authorization'] = 'Bearer ' + t;
    if (c) h['X-Client-ID'] = c;
    return h;
  }
  function _usuarioLocalEhAdmin() {
    try {
      const permissoes = JSON.parse(localStorage.getItem('permissions') || '{}');
      return !!(permissoes && (permissoes.full === true || permissoes.admin_usuarios === true));
    } catch (_) {
      return false;
    }
  }
  function _aplicarPermissaoModeloChat(selectEl, podeEscolher) {
    if (!selectEl) return;
    const permitido = !!podeEscolher;
    selectEl.dataset.podeEscolherModelo = permitido ? 'true' : 'false';
    selectEl.disabled = !permitido;
    selectEl.style.display = permitido ? '' : 'none';
  }

  /* ── Nome do módulo baseado na URL ── */
  function _modulo() {
    const p = location.pathname.replace(/^\/static\//, '').replace(/\.html$/, '').replace(/\//g, '_') || 'inicio';
    return p;
  }
  const IA_GLOBAL_MODULO = 'global';

  /* ── Persistência de conversas (Servidor + fallback localStorage) ── */
  const MAX_MSGS = 80;
  const MAX_CONVS = 20;

  function _storeKey(convId) {
    return 'ia_hist_' + _clientId() + '_' + IA_GLOBAL_MODULO + '_' + convId;
  }
  function _indexKey() {
    return 'ia_convs_' + _clientId() + '_' + IA_GLOBAL_MODULO;
  }
  function _localIndexPrefix() {
    return 'ia_convs_' + _clientId() + '_';
  }
  function _localStorePrefix() {
    return 'ia_hist_' + _clientId() + '_';
  }

  // Fallback localStorage (para compatibilidade)
  function _listarConversasLocal() {
    try {
      const mapa = new Map();
      for (let i = 0; i < localStorage.length; i += 1) {
        const key = localStorage.key(i) || '';
        if (!key.startsWith(_localIndexPrefix())) continue;
        const lista = JSON.parse(localStorage.getItem(key) || '[]');
        if (!Array.isArray(lista)) continue;
        lista.forEach(item => {
          if (!item || !item.id) return;
          if (!mapa.has(item.id)) mapa.set(item.id, item);
        });
      }
      return Array.from(mapa.values()).slice(0, MAX_CONVS);
    } catch (_) { return []; }
  }
  function _salvarIndexLocal(lista) {
    try { localStorage.setItem(_indexKey(), JSON.stringify(lista.slice(0, MAX_CONVS))); } catch (_) {}
  }
  function _carregarMensagensLocal(convId) {
    try {
      const global = JSON.parse(localStorage.getItem(_storeKey(convId)) || '[]');
      if (Array.isArray(global) && global.length) return global;
      for (let i = 0; i < localStorage.length; i += 1) {
        const key = localStorage.key(i) || '';
        if (!key.startsWith(_localStorePrefix()) || !key.endsWith('_' + convId)) continue;
        const msgs = JSON.parse(localStorage.getItem(key) || '[]');
        if (Array.isArray(msgs) && msgs.length) return msgs;
      }
    } catch (_) {}
    return [];
  }
  function _salvarMensagensLocal(convId, msgs) {
    try { localStorage.setItem(_storeKey(convId), JSON.stringify(msgs.slice(-MAX_MSGS))); } catch (_) {}
  }
  function _removerConversaLocal(convId) {
    try {
      localStorage.removeItem(_storeKey(convId));
      for (let i = localStorage.length - 1; i >= 0; i -= 1) {
        const key = localStorage.key(i) || '';
        if (key.startsWith(_localStorePrefix()) && key.endsWith('_' + convId)) {
          localStorage.removeItem(key);
        }
      }
    } catch (_) {}
  }

  // Funções de servidor
  async function _listarConversasServidor() {
    try {
      const r = await fetch('/api/ia/conversas/listar', {
        method: 'GET',
        headers: _authHeaders(),
      });
      if (r.ok) {
        const d = await r.json();
        if (d && d.success === true && Array.isArray(d.conversas)) {
          return d.conversas.map(c => ({ id: c.id, data: String(c.data_atualizacao || '').slice(0, 16), preview: c.titulo }));
        }
      }
    } catch (_) {}
    return _listarConversasLocal();
  }

  async function _carregarConversaServidor(convId) {
    try {
      const r = await fetch(`/api/ia/conversas/${encodeURIComponent(convId)}`, {
        method: 'GET',
        headers: _authHeaders(),
      });
      if (r.ok) {
        const d = await r.json();
        if (d && d.success === true && d.conversa && Array.isArray(d.conversa.mensagens)) {
          return d.conversa.mensagens.map(m => ({ role: m.role, text: m.text }));
        }
      }
    } catch (_) {}
    return _carregarMensagensLocal(convId);
  }

  async function _salvarConversaServidor(convId, modulo, titulo, msgs) {
    _salvarMensagensLocal(convId, msgs);
    try {
      const r = await fetch('/api/ia/conversas/salvar', {
        method: 'POST',
        headers: _authHeaders(),
        body: JSON.stringify({
          conversa_id: convId,
          modulo: modulo,
          titulo: titulo,
          mensagens: msgs.map(m => ({ role: m.role, text: m.text })),
        }),
      });
      if (r.ok) {
        const d = await r.json().catch(() => null);
        return !!(d && d.success === true);
      }
    } catch (_) {}
    return false;
  }

  async function _deletarConversaServidor(convId) {
    try {
      const r = await fetch(`/api/ia/conversas/${encodeURIComponent(convId)}`, {
        method: 'DELETE',
        headers: _authHeaders(),
      });
      if (r.ok) {
        const d = await r.json().catch(() => null);
        if (d && d.success === true) return true;
      }
    } catch (_) {}
    _removerConversaLocal(convId);
    return false;
  }

  // Inicializar lista de conversas (Server first, fallback local)
  async function _listarConversas() {
    return await _listarConversasServidor();
  }

  function _carregarMensagens(convId) {
    // Async, mas retorna promisse
    return _carregarConversaServidor(convId);
  }

  async function _salvarMensagens(convId, msgs) {
    const titulo = msgs.find(m => m.role === 'user')?.text?.slice(0, 60) || 'Conversa';
    return await _salvarConversaServidor(convId, IA_GLOBAL_MODULO, titulo, msgs);
  }

  async function _removerConversa(convId) {
    return await _deletarConversaServidor(convId);
  }

  function _novoId() { return Date.now().toString(36) + Math.random().toString(36).slice(2, 6); }

  /* ── Render de texto para HTML ── */
  function _renderTexto(txt) {
    const escapeHtml = (v) => String(v || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    const bruto = String(txt || '').replace(/\r\n?/g, '\n');
    if (!bruto.trim()) return '';

    function safeHref(url) {
      const href = String(url || '').trim();
      if (/^(https?:\/\/|mailto:|tel:|\/|#)/i.test(href)) return escapeHtml(href).replace(/"/g, '&quot;');
      return '';
    }

    function renderLink(label, url) {
      const href = safeHref(url);
      if (!href) return label;
      return `<a href="${href}" target="_blank" rel="noopener noreferrer">${label}</a>`;
    }

    function normalizarUrlImagem(url) {
      let src = String(url || '').trim();
      if (!src) return '';
      src = src.replace(/^["'`]+|["'`]+$/g, '');
      src = src.replace(/^http:\/\/(http[0-9]*\.mlstatic\.com\/)/i, 'https://$1');
      if (/^cadastro_fotos\//i.test(src)) src = src.split('/').pop();
      if (/^[^\/\\]+\.(?:png|jpe?g|gif|webp|bmp)$/i.test(src)) {
        src = `/api/cadastro/foto-arquivo/${encodeURIComponent(src)}`;
      }
      if (/^(https?:\/\/|\/api\/cadastro\/foto-arquivo\/|\/api\/cadastro\/foto\/|\/api\/ia\/imagens\/|\/img\/)/i.test(src)) {
        return escapeHtml(src).replace(/"/g, '&quot;');
      }
      return '';
    }

    function renderImagem(alt, url) {
      const src = normalizarUrlImagem(url);
      if (!src) return '';
      const altSeguro = escapeHtml(alt || 'Imagem do SKU');
      return `<a class="jk-ia-img-link" href="${src}" target="_blank" rel="noopener noreferrer"><img class="jk-ia-img" src="${src}" data-jk-src="${src}" alt="${altSeguro}" loading="lazy"></a>`;
    }

    function obterExtensaoArquivo(url) {
      const limpo = String(url || '').split('?')[0].split('#')[0];
      const match = limpo.match(/\.([a-z0-9]{2,8})$/i);
      return match ? match[1].toUpperCase() : 'FILE';
    }

    function normalizarUrlArquivo(url) {
      const href = safeHref(url);
      if (!href) return '';
      if (normalizarUrlImagem(url)) return '';
      const alvo = String(url || '').split('?')[0].split('#')[0];
      const ehArquivo = /\.(?:pdf|xlsx?|csv|docx?|pptx?|txt|json|xml|md|log|zip|rar|7z)$/i.test(alvo)
        || /\/download(?:\/|$)/i.test(alvo);
      if (!ehArquivo) return '';
      return href;
    }

    function renderArquivo(label, url) {
      const href = normalizarUrlArquivo(url);
      if (!href) return '';
      const nomeBruto = String(label || '').trim() || decodeURIComponent(String(url || '').split('/').pop() || 'Arquivo');
      const nome = escapeHtml(nomeBruto);
      const ext = escapeHtml(obterExtensaoArquivo(url));
      const acao = ext === 'PDF' ? 'Abrir PDF' : 'Abrir arquivo';
      return `<a class="jk-ia-file-card" href="${href}" target="_blank" rel="noopener noreferrer"><span class="jk-ia-file-icon">${ext}</span><span class="jk-ia-file-info"><span class="jk-ia-file-name">${nome}</span><span class="jk-ia-file-action">${acao}</span></span></a>`;
    }

    function inlineMd(input) {
      let s = escapeHtml(input || '');
      const codigos = [];
      s = s.replace(/`([^`]+)`/g, (_, code) => {
        const imagem = renderImagem('Imagem do SKU', code);
        const arquivo = renderArquivo(code, code);
        const id = codigos.push(imagem || arquivo || `<code>${code}</code>`) - 1;
        return `\u0000CODE${id}\u0000`;
      });
      s = s.replace(/!\[([^\]\n]*)\]\(([^)\s]+)\)/g, (_, alt, url) => renderImagem(alt, url) || '');
      s = s.replace(/\[([^\]\n]+)\]\(([^)\s]+)\)/g, (_, label, url) => renderImagem(label, url) || renderArquivo(label, url) || renderLink(label, url));
      s = s.replace(/(^|[\s>])((?:cadastro_fotos\/)?[^\/\\\s<]+\.(?:png|jpe?g|gif|webp|bmp))/gi, (_, prefix, url) => {
        const imagem = renderImagem('Imagem do SKU', url);
        return imagem ? `${prefix}${imagem}` : `${prefix}${url}`;
      });
      s = s.replace(/(^|[\s>])((?:\/api\/cadastro\/foto-arquivo\/|\/api\/cadastro\/foto\/|\/api\/ia\/imagens\/|\/img\/|https?:\/\/)[^\s<]+\.(?:png|jpe?g|gif|webp|bmp)(?:\?[^\s<]+)?)/gi, (_, prefix, url) => {
        const imagem = renderImagem('Imagem do SKU', url);
        return imagem ? `${prefix}${imagem}` : `${prefix}${url}`;
      });
      s = s.replace(/(^|[\s>])((?:\/api\/|\/static\/|\/img\/|https?:\/\/)[^\s<]+\.(?:pdf|xlsx?|csv|docx?|pptx?|txt|json|xml|md|log|zip|rar|7z)(?:\?[^\s<]+)?)/gi, (_, prefix, url) => {
        const arquivo = renderArquivo(url.split('/').pop(), url);
        return arquivo ? `${prefix}${arquivo}` : `${prefix}${url}`;
      });
      s = s.replace(/(^|[\s(])((?:https?:\/\/)[^\s<)]+)/g, (_, prefix, url) => `${prefix}${renderLink(url, url)}`);
      s = s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
      s = s.replace(/(^|[^*])\*(?!\s)([^*]+?)\*(?!\*)/g, '$1<em>$2</em>');
      s = s.replace(/\u0000CODE(\d+)\u0000/g, (_, idx) => codigos[Number(idx)] || '');
      return s;
    }

    function renderLinhaMidia(linha) {
      const texto = String(linha || '').trim();
      let match = texto.match(/^!\[([^\]\n]*)\]\(([^)\s]+)\)$/);
      if (match) return renderImagem(match[1], match[2]);
      match = texto.match(/^\[([^\]\n]+)\]\(([^)\s]+)\)$/);
      if (match) return renderImagem(match[1], match[2]) || renderArquivo(match[1], match[2]);
      return renderImagem('Imagem gerada', texto) || renderArquivo(texto.split('/').pop(), texto);
    }

    function splitPipeRow(line) {
      const raw = String(line || '').trim();
      const semBorda = raw.replace(/^\|/, '').replace(/\|$/, '');
      return semBorda.split('|').map(c => inlineMd(c.trim()));
    }

    function isTableSep(line) {
      const raw = String(line || '').trim();
      if (!raw.includes('|')) return false;
      const semBorda = raw.replace(/^\|/, '').replace(/\|$/, '');
      const cols = semBorda.split('|').map(c => c.trim());
      if (!cols.length) return false;
      return cols.every(c => /^:?-{3,}:?$/.test(c));
    }

    const lines = bruto.split('\n');
    const out = [];
    let i = 0;

    while (i < lines.length) {
      const line = lines[i] || '';
      const trim = line.trim();

      if (!trim) { i += 1; continue; }

      const midiaHtml = renderLinhaMidia(trim);
      if (midiaHtml) {
        out.push(midiaHtml);
        i += 1;
        continue;
      }

      if (/^```/.test(trim)) {
        const code = [];
        i += 1;
        while (i < lines.length && !/^```/.test((lines[i] || '').trim())) {
          code.push(lines[i] || '');
          i += 1;
        }
        if (i < lines.length) i += 1;
        out.push(`<pre><code>${escapeHtml(code.join('\n'))}</code></pre>`);
        continue;
      }

      if (trim.includes('|') && i + 1 < lines.length && isTableSep(lines[i + 1])) {
        const header = splitPipeRow(lines[i]);
        i += 2;
        const bodyRows = [];
        while (i < lines.length) {
          const rowLine = (lines[i] || '').trim();
          if (!rowLine || !rowLine.includes('|') || isTableSep(rowLine)) break;
          bodyRows.push(splitPipeRow(lines[i]));
          i += 1;
        }
        const headHtml = `<thead><tr>${header.map(c => `<th>${c}</th>`).join('')}</tr></thead>`;
        const bodyHtml = bodyRows.length
          ? `<tbody>${bodyRows.map(r => `<tr>${r.map(c => `<td>${c}</td>`).join('')}</tr>`).join('')}</tbody>`
          : '';
        out.push(`<table>${headHtml}${bodyHtml}</table>`);
        continue;
      }

      if (/^#{1,4}\s+/.test(trim)) {
        const nivel = Math.min(4, (trim.match(/^#+/) || ['#'])[0].length);
        const textoTitulo = trim.replace(/^#{1,6}\s+/, '');
        out.push(`<h${nivel}>${inlineMd(textoTitulo)}</h${nivel}>`);
        i += 1;
        continue;
      }

      if (/^>\s?/.test(trim)) {
        out.push(`<blockquote>${inlineMd(trim.replace(/^>\s?/, ''))}</blockquote>`);
        i += 1;
        continue;
      }

      if (/^(-{3,}|\*{3,}|_{3,})$/.test(trim)) {
        out.push('<hr>');
        i += 1;
        continue;
      }

      if (/^[-*+]\s+\[[ xX]\]\s+/.test(trim)) {
        const itens = [];
        while (i < lines.length && /^\s*[-*+]\s+\[[ xX]\]\s+/.test((lines[i] || '').trim())) {
          const itemRaw = (lines[i] || '').trim();
          const checked = /^\s*[-*+]\s+\[[xX]\]\s+/.test(itemRaw);
          const itemTxt = itemRaw.replace(/^[-*+]\s+\[[ xX]\]\s+/, '');
          itens.push(`<li class="jk-ia-task-item"><input type="checkbox" disabled${checked ? ' checked' : ''}> <span>${inlineMd(itemTxt)}</span></li>`);
          i += 1;
        }
        out.push(`<ul class="jk-ia-task-list">${itens.join('')}</ul>`);
        continue;
      }

      if (/^[-*+]\s+/.test(trim)) {
        const itens = [];
        while (i < lines.length && /^\s*[-*+]\s+/.test((lines[i] || '').trim())) {
          const itemTxt = (lines[i] || '').trim().replace(/^[-*+]\s+/, '');
          itens.push(`<li>${inlineMd(itemTxt)}</li>`);
          i += 1;
        }
        out.push(`<ul>${itens.join('')}</ul>`);
        continue;
      }

      if (/^\d+[.)]\s+/.test(trim)) {
        const itens = [];
        while (i < lines.length && /^\s*\d+[.)]\s+/.test((lines[i] || '').trim())) {
          const itemTxt = (lines[i] || '').trim().replace(/^\d+[.)]\s+/, '');
          itens.push(`<li>${inlineMd(itemTxt)}</li>`);
          i += 1;
        }
        out.push(`<ol>${itens.join('')}</ol>`);
        continue;
      }

      const paragrafo = [];
      while (i < lines.length) {
        const atual = (lines[i] || '').trim();
        if (!atual) break;
        if (/^```/.test(atual)) break;
        if (/^#{1,4}\s+/.test(atual)) break;
        if (/^>\s?/.test(atual)) break;
        if (/^[-*+]\s+/.test(atual)) break;
        if (/^\d+[.)]\s+/.test(atual)) break;
        if (/^(-{3,}|\*{3,}|_{3,})$/.test(atual)) break;
        if (renderLinhaMidia(atual)) break;
        if (atual.includes('|') && i + 1 < lines.length && isTableSep(lines[i + 1])) break;
        paragrafo.push(atual);
        i += 1;
      }
      out.push(`<p>${paragrafo.map(linha => inlineMd(linha)).join('<br>')}</p>`);
    }

    return out.join('');
  }

  /* ── CSS do widget ── */
  function _uniqueList(lista) {
    const vistos = new Set();
    return lista.filter(item => {
      const valor = String(item || '').trim();
      if (!valor || vistos.has(valor)) return false;
      vistos.add(valor);
      return true;
    });
  }

  function _imagemUrlAbsolutaLocal(pathname) {
    const path = String(pathname || '').trim();
    if (!path.startsWith('/')) return [];
    const urls = [];
    try { urls.push(new URL(path, location.origin).href); } catch (_) {}
    urls.push(`http://127.0.0.1:8001${path}`);
    urls.push(`http://localhost:8001${path}`);
    if (location.hostname === '127.0.0.1' || location.hostname === 'localhost') {
      urls.push(`http://127.0.0.1:8012${path}`);
    }
    return _uniqueList(urls);
  }

  function _imagemAlternativasChat(srcOriginal) {
    const raw = String(srcOriginal || '').trim();
    if (!raw) return [];
    const urls = [];
    const add = (url) => { if (url) urls.push(url); };
    const addPath = (path) => {
      add(path);
      _imagemUrlAbsolutaLocal(path).forEach(add);
    };

    add(raw);
    let parsed = null;
    try { parsed = new URL(raw, location.href); } catch (_) { parsed = null; }
    if (parsed && parsed.protocol === 'http:' && /\.mlstatic\.com$/i.test(parsed.hostname)) {
      parsed.protocol = 'https:';
      add(parsed.href);
    }
    const path = parsed ? parsed.pathname + parsed.search : raw;

    if (String(path).startsWith('/api/')) {
      addPath(path);
    }

    const caminhoSemQuery = String(parsed ? parsed.pathname : raw).split('?')[0];
    const matchArquivo = caminhoSemQuery.match(/^\/api\/cadastro\/foto-arquivo\/(.+)$/i);
    if (matchArquivo) {
      const arquivo = matchArquivo[1];
      const tenant = encodeURIComponent(_clientId() || 'default');
      addPath(`/api/cadastro/foto/${tenant}/${arquivo}`);
      addPath(`/api/cadastro/foto/default/${arquivo}`);
    }

    return _uniqueList(urls);
  }

  function _ativarFallbacksImagem(container) {
    if (!container || !container.querySelectorAll) return;
    container.querySelectorAll('img.jk-ia-img').forEach(img => {
      if (img.dataset.jkImgFallbackReady === '1') return;
      const alternativas = _imagemAlternativasChat(img.dataset.jkSrc || img.getAttribute('src') || '');
      if (!alternativas.length) return;
      img.dataset.jkImgFallbackReady = '1';
      img.dataset.jkImgFallbacks = JSON.stringify(alternativas);
      img.dataset.jkImgFallbackIndex = '0';

      const atualizarLink = (url) => {
        const link = img.closest('a.jk-ia-img-link');
        if (link && url) link.href = url;
      };
      atualizarLink(img.getAttribute('src'));

      img.addEventListener('error', () => {
        let lista = [];
        try { lista = JSON.parse(img.dataset.jkImgFallbacks || '[]'); } catch (_) { lista = []; }
        let idx = Number(img.dataset.jkImgFallbackIndex || 0);
        while (idx < lista.length - 1) {
          idx += 1;
          const proxima = lista[idx];
          img.dataset.jkImgFallbackIndex = String(idx);
          if (proxima && proxima !== img.src && proxima !== img.getAttribute('src')) {
            img.classList.remove('jk-ia-img-error');
            img.src = proxima;
            atualizarLink(proxima);
            return;
          }
        }
        img.classList.add('jk-ia-img-error');
        img.title = 'Nao foi possivel carregar esta imagem.';
      });
    });
  }

  function _definirTextoMsg(el, texto) {
    if (!el) return;
    el.innerHTML = _renderTexto(texto);
    _ativarFallbacksImagem(el);
  }

  const CSS = `
  #jk-left-sidebar-hotspot{position:fixed;top:0;left:0;bottom:0;width:18px;z-index:10000;pointer-events:auto;}
  #jk-left-sidebar-hotspot::before{content:"";position:absolute;top:0;left:0;bottom:0;width:8px;background:transparent;}
  #jk-left-sidebar-menu{position:fixed;top:50%;left:10px;width:360px;max-height:calc(100vh - 28px);overflow-x:hidden;overflow-y:auto;overscroll-behavior:contain;z-index:10001;
    display:flex;flex-direction:column;align-items:flex-start;gap:8px;padding:7px 0;transform:translate(calc(-100% - 28px),-50%);
    opacity:0;pointer-events:none;transition:transform .2s cubic-bezier(.4,0,.2,1),opacity .16s ease;scrollbar-width:none;-ms-overflow-style:none;}
  #jk-left-sidebar-menu::-webkit-scrollbar{width:0;height:0;display:none;}
  #jk-left-sidebar-menu::-webkit-scrollbar-track{background:transparent;}
  #jk-left-sidebar-menu::-webkit-scrollbar-thumb{background:transparent;}
  #jk-left-sidebar-hotspot:hover #jk-left-sidebar-menu,
  #jk-left-sidebar-hotspot:focus-within #jk-left-sidebar-menu{transform:translate(0,-50%);opacity:1;pointer-events:auto;}
  .jk-left-module-link{position:relative;width:344px;min-height:46px;display:flex;align-items:center;text-decoration:none;color:#e8fffb;overflow:visible;
    border:0;background:transparent;outline:none;}
  .jk-left-module-icon{position:relative;z-index:2;width:46px;height:46px;border-radius:14px;border:1px solid rgba(120,227,212,.32);
    display:flex;align-items:center;justify-content:center;background:linear-gradient(145deg,#0f6bbd,#13b99a);box-shadow:0 4px 16px rgba(19,196,160,.34);
    font-size:1.25rem;line-height:1;transform:scale(1);transform-origin:left center;will-change:transform;
    transition:transform .18s ease,box-shadow .18s ease,border-color .18s ease;}
  .jk-left-module-glyph{display:inline-flex;align-items:center;justify-content:center;line-height:1;transform:rotate(0deg) scale(1);transform-origin:center center;will-change:transform;animation:none;}
  .jk-left-module-label{position:absolute;left:78px;top:50%;z-index:5;min-width:118px;max-width:244px;min-height:34px;display:flex;align-items:center;
    padding:0 13px 0 14px;border-radius:12px;border:1px solid rgba(120,227,212,.3);
    background:linear-gradient(90deg,rgba(8,43,59,.96),rgba(6,71,84,.92));box-shadow:0 8px 22px rgba(0,0,0,.34);
    color:#e8fffb;font-size:.76rem;font-weight:900;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
    transform:translate(-18px,-50%) scaleX(.25);transform-origin:left center;opacity:0;transition:transform .16s cubic-bezier(.2,.9,.2,1),opacity .12s ease;}
  @keyframes jkLeftModuleGlyphSpin{from{transform:rotate(0deg) scale(1.14);}to{transform:rotate(360deg) scale(1.14);}}
  .jk-left-module-link:hover .jk-left-module-icon,
  .jk-left-module-icon:hover,
  .jk-left-module-link:focus-visible .jk-left-module-icon{transform:translateX(8px) scale(1.7);border-color:rgba(255,255,255,.82);box-shadow:0 0 0 5px rgba(120,227,212,.22),0 10px 28px rgba(19,196,160,.58);}
  .jk-left-module-link:hover .jk-left-module-glyph,
  .jk-left-module-icon:hover .jk-left-module-glyph,
  .jk-left-module-link:focus-visible .jk-left-module-glyph{animation:jkLeftModuleGlyphSpin .58s linear infinite;}
  .jk-left-module-link:hover .jk-left-module-label,
  .jk-left-module-link:focus-visible .jk-left-module-label{transform:translate(0,-50%) scaleX(1);opacity:1;}
  .jk-left-module-link.modulo-atual{display:none;}
  #jk-right-sidebar-hotspot{position:fixed;top:0;right:0;bottom:0;width:18px;z-index:10000;pointer-events:auto;}
  #jk-right-sidebar-hotspot::before{content:"";position:absolute;top:0;right:0;bottom:0;width:8px;background:transparent;}
  #jk-right-sidebar-menu{position:fixed;top:50%;right:10px;display:flex;flex-direction:column;gap:10px;
    transform:translate(calc(100% + 22px),-50%);opacity:0;pointer-events:none;
    transition:transform .2s cubic-bezier(.4,0,.2,1),opacity .16s ease;}
  #jk-right-sidebar-hotspot:hover #jk-right-sidebar-menu,
  #jk-right-sidebar-hotspot:focus-within #jk-right-sidebar-menu,
  #jk-right-sidebar-hotspot.tem-alerta #jk-right-sidebar-menu,
  #jk-right-sidebar-hotspot.menu-aberto #jk-right-sidebar-menu{transform:translate(0,-50%);opacity:1;pointer-events:auto;}
  .jk-right-sidebar-icon{position:relative;width:50px;height:50px;border-radius:14px;border:1px solid rgba(120,227,212,.32);
    background:linear-gradient(145deg,#1888ff,#13c4a0);color:#fff;font-size:1.35rem;cursor:pointer;
    box-shadow:0 4px 18px rgba(19,196,160,.42);display:flex;align-items:center;justify-content:center;
    transition:transform .18s ease,box-shadow .18s ease,border-color .18s ease;}
  .jk-right-sidebar-icon:hover{transform:translateX(-2px) scale(1.04);box-shadow:0 8px 24px rgba(19,196,160,.5);}
  .jk-right-sidebar-icon.ativo{border-color:rgba(255,255,255,.72);box-shadow:0 0 0 3px rgba(120,227,212,.18),0 8px 24px rgba(19,196,160,.48);}
  .jk-right-sidebar-icon.piscando{animation:jkRightSidebarBlink .9s ease-in-out infinite;border-color:rgba(255,255,255,.82);}
  @keyframes jkRightSidebarBlink{
    0%,100%{filter:brightness(1);box-shadow:0 4px 18px rgba(19,196,160,.42);}
    50%{filter:brightness(1.3);box-shadow:0 0 0 5px rgba(239,68,68,.18),0 0 28px rgba(239,68,68,.72);}
  }
  #jk-msg-fab{background:linear-gradient(145deg,#5b8cff,#21b8a3);}
  #jk-msg-badge{position:absolute;top:5px;right:5px;min-width:17px;height:17px;padding:0 5px;border-radius:999px;
    background:#ef4444;color:#fff;border:2px solid #062a22;font-size:.62rem;font-weight:900;line-height:13px;text-align:center;display:none;}
  #jk-msg-badge:not(:empty){display:block;}
  #jk-ia-panel{--jk-ia-panel-width:360px;position:fixed;top:0;right:0;bottom:0;z-index:9999;width:var(--jk-ia-panel-width);min-width:300px;max-width:96vw;
    background:linear-gradient(165deg,#081b2e,#062a22);border-left:1px solid rgba(106,225,203,.28);
    display:flex;flex-direction:column;box-shadow:-6px 0 32px rgba(0,0,0,.55);
    transform:translateX(110%);transition:transform .25s cubic-bezier(.4,0,.2,1);}
  #jk-ia-panel.aberto{transform:translateX(0);}
  #jk-msg-panel{--jk-msg-panel-width:380px;position:fixed;top:0;right:0;bottom:0;z-index:9999;width:var(--jk-msg-panel-width);min-width:300px;max-width:96vw;
    background:linear-gradient(165deg,#091d31,#062a22);border-left:1px solid rgba(106,225,203,.28);
    display:flex;flex-direction:column;box-shadow:-6px 0 32px rgba(0,0,0,.55);
    transform:translateX(110%);transition:transform .25s cubic-bezier(.4,0,.2,1);}
  #jk-msg-panel.aberto{transform:translateX(0);}
  #jk-msg-header{display:flex;align-items:center;gap:8px;padding:12px 14px;border-bottom:1px solid rgba(106,225,203,.18);background:rgba(4,30,48,.72);}
  #jk-msg-header h3{flex:1 1 auto;margin:0;color:#8ee9de;font-size:.96rem;font-weight:800;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
  .jk-msg-hbtn{border:0;background:transparent;color:#8ee9de;cursor:pointer;font-size:1.05rem;padding:0 12px;border-radius:8px;line-height:1;min-height:36px;min-width:40px;height:36px;display:flex;align-items:center;justify-content:center;}
  .jk-msg-hbtn:hover{background:rgba(19,196,160,.18);transform:scale(1.05);}
  #jk-msg-body{flex:1 1 auto;overflow-y:auto;padding:12px;display:flex;flex-direction:column;gap:12px;background:rgba(2,18,30,.4);scrollbar-width:thin;scrollbar-color:rgba(120,227,212,.34) transparent;}
  #jk-msg-body::-webkit-scrollbar{width:7px;}
  #jk-msg-body::-webkit-scrollbar-thumb{background:rgba(120,227,212,.28);border-radius:999px;}
  .jk-msg-status{min-height:18px;color:#9ee8df;font-size:.74rem;font-weight:800;line-height:1.35;}
  #jk-msg-main-view{display:flex;flex-direction:column;gap:12px;}
  #jk-msg-chat-header{display:none;align-items:center;gap:10px;padding:8px 0 8px;position:sticky;top:0;z-index:6;
    background:linear-gradient(165deg,rgba(9,29,49,.98),rgba(6,42,34,.98));box-shadow:0 10px 18px rgba(0,0,0,.24);}
  #jk-msg-chat-header.ativo{display:flex;}
  #jk-msg-back{flex:0 0 34px;width:34px;height:34px;border-radius:8px;border:1px solid rgba(120,227,212,.42);background:rgba(35,142,165,.44);color:#e8fffb;font-weight:900;cursor:pointer;box-shadow:0 8px 18px rgba(0,0,0,.26);}
  #jk-msg-back:hover{background:rgba(36,161,160,.32);border-color:rgba(120,227,212,.64);}
  #jk-msg-chat-title{min-width:0;color:#fff;font-size:.86rem;font-weight:900;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
  #jk-msg-history{display:none;flex:1 1 auto;flex-direction:column;gap:8px;padding:2px 0 8px;}
  #jk-msg-history.ativo{display:flex;}
  #jk-msg-typing{display:none;color:#9ee8df;font-size:.72rem;font-weight:900;min-height:17px;padding:0 2px 6px;}
  #jk-msg-typing.ativo{display:block;}
  .jk-msg-bubble{max-width:86%;border:1px solid rgba(120,227,212,.22);border-radius:13px;padding:8px 10px;color:#e8fffb;background:rgba(23,47,70,.55);font-size:.78rem;line-height:1.42;white-space:pre-wrap;}
  .jk-msg-bubble.me{align-self:flex-end;background:linear-gradient(165deg,#1888ff,#0f65d8);border-color:transparent;color:#fff;border-bottom-right-radius:4px;}
  .jk-msg-bubble.other{align-self:flex-start;border-bottom-left-radius:4px;}
  .jk-msg-bubble-meta{display:block;margin-top:4px;font-size:.62rem;color:rgba(232,255,251,.72);white-space:nowrap;}
  .jk-msg-bubble-meta.local-alert{color:#ffd7a3;}
  .jk-msg-attachments{display:grid;gap:6px;margin-top:7px;}
  .jk-msg-attachment{border:1px solid rgba(120,227,212,.24);border-radius:8px;background:rgba(3,22,32,.48);padding:6px;color:#dffefa;font-size:.7rem;overflow:hidden;}
  .jk-msg-attachment img{display:block;max-width:190px;max-height:150px;border-radius:7px;object-fit:contain;background:rgba(0,0,0,.22);}
  .jk-msg-attachment audio{width:210px;max-width:100%;height:34px;display:block;}
  .jk-msg-attachment a{color:#9ee8df;text-decoration:none;font-weight:900;word-break:break-word;}
  .jk-msg-attachment a:hover{text-decoration:underline;}
  .jk-msg-section{display:flex;flex-direction:column;gap:8px;}
  .jk-msg-section-title{color:#8ee9de;font-size:.78rem;font-weight:900;text-transform:uppercase;letter-spacing:.03em;}
  .jk-msg-list{display:flex;flex-direction:column;gap:8px;}
  #jk-msg-online-list{gap:4px;}
  .jk-msg-empty{color:#cfe7e4;font-size:.78rem;opacity:.72;border:1px dashed rgba(120,227,212,.24);border-radius:8px;padding:10px;background:rgba(4,26,35,.36);}
  .jk-msg-user-item,.jk-msg-card{border:1px solid rgba(120,227,212,.24);border-radius:8px;background:rgba(8,43,59,.56);color:#e8fffb;padding:10px;}
  .jk-msg-user-item{display:flex;align-items:center;gap:6px;text-align:left;cursor:pointer;width:100%;min-height:26px;padding:3px 7px;border-radius:6px;}
  .jk-msg-user-item:hover,.jk-msg-user-item.ativo{border-color:rgba(120,227,212,.62);background:rgba(20,92,85,.48);}
  .jk-msg-user-item:disabled{cursor:default;opacity:.68;}
  .jk-msg-dot{width:6px;height:6px;border-radius:999px;background:#22c55e;box-shadow:0 0 0 2px rgba(34,197,94,.12);flex:0 0 6px;}
  .jk-msg-user-item.offline .jk-msg-dot{background:#ef4444;box-shadow:0 0 0 2px rgba(239,68,68,.11);}
  .jk-msg-user-info{min-width:0;display:grid;gap:0;flex:1 1 auto;}
  .jk-msg-user-name{font-size:.72rem;font-weight:900;line-height:1.1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
  .jk-msg-user-meta{display:none;}
  .jk-msg-unread-count{flex:0 0 auto;border-radius:999px;background:#ef4444;color:#fff;border:1px solid rgba(255,255,255,.3);font-size:.54rem;font-weight:900;min-width:17px;min-height:16px;padding:1px 4px;display:inline-flex;align-items:center;justify-content:center;box-shadow:0 0 8px rgba(239,68,68,.24);}
  .jk-msg-card{display:grid;gap:6px;}
  .jk-msg-card-title{font-size:.82rem;font-weight:900;color:#fff;}
  .jk-msg-card-meta{font-size:.68rem;color:#9ee8df;}
  .jk-msg-card-text{font-size:.78rem;line-height:1.42;white-space:pre-wrap;color:#e8fffb;}
  .jk-msg-card-actions{display:flex;justify-content:flex-end;}
  .jk-msg-small-btn{border:1px solid rgba(120,227,212,.34);border-radius:8px;background:rgba(35,142,165,.22);color:#e8fffb;font-weight:900;font-size:.72rem;min-height:30px;padding:0 9px;cursor:pointer;}
  .jk-msg-small-btn:hover{border-color:rgba(120,227,212,.7);background:rgba(36,161,160,.3);}
  #jk-msg-compose{border-top:1px solid rgba(106,225,203,.18);padding:10px 12px 12px;background:rgba(4,26,35,.92);display:grid;gap:8px;}
  #jk-msg-selected{color:#9ee8df;font-size:.74rem;font-weight:800;min-height:18px;}
  #jk-msg-tools{display:flex;align-items:center;gap:6px;flex-wrap:wrap;}
  .jk-msg-tool-btn{border:1px solid rgba(120,227,212,.28);border-radius:8px;background:rgba(35,142,165,.18);color:#e8fffb;font-weight:900;font-size:.72rem;min-width:32px;height:30px;padding:0 8px;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;}
  .jk-msg-tool-btn:hover,.jk-msg-tool-btn.ativo{border-color:rgba(120,227,212,.7);background:rgba(36,161,160,.32);}
  #jk-msg-emoji-panel{display:none;grid-template-columns:repeat(8,1fr);gap:4px;border:1px solid rgba(120,227,212,.2);border-radius:8px;padding:6px;background:rgba(3,22,32,.9);}
  #jk-msg-emoji-panel.aberto{display:grid;}
  .jk-msg-emoji-choice{border:0;border-radius:6px;background:rgba(35,142,165,.16);min-height:28px;cursor:pointer;font-size:1rem;}
  .jk-msg-emoji-choice:hover{background:rgba(36,161,160,.28);}
  #jk-msg-anexos{display:flex;flex-wrap:wrap;gap:5px;}
  .jk-msg-anexo-chip{display:inline-flex;align-items:center;gap:5px;max-width:100%;border:1px solid rgba(120,227,212,.26);border-radius:999px;background:rgba(25,120,133,.18);color:#dbfffb;font-size:.68rem;font-weight:800;padding:4px 7px;}
  .jk-msg-anexo-chip span{max-width:210px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
  .jk-msg-anexo-chip button{border:0;background:transparent;color:#ffd2d2;font-size:.82rem;font-weight:900;cursor:pointer;padding:0;}
  #jk-msg-text{width:100%;border:1px solid rgba(120,227,212,.26);border-radius:8px;background:rgba(3,22,32,.82);color:#e8fffb;font:inherit;font-size:.78rem;outline:none;padding:8px;}
  #jk-msg-text{min-height:76px;resize:vertical;line-height:1.4;}
  #jk-msg-text:focus{border-color:#53d4b7;box-shadow:0 0 0 2px rgba(83,212,183,.16);}
  #jk-msg-send{border:0;border-radius:8px;background:linear-gradient(165deg,#53d4b7,#33a7d7);color:#03272f;font-weight:900;font-size:.78rem;min-height:36px;cursor:pointer;}
  #jk-msg-send:disabled{cursor:not-allowed;opacity:.55;}
  #jk-ia-resizer{position:absolute;left:-7px;top:0;bottom:0;width:14px;cursor:ew-resize;touch-action:none;z-index:2;}
  #jk-ia-resizer::after{content:"";position:absolute;left:6px;top:12px;bottom:12px;width:2px;border-radius:999px;background:rgba(120,227,212,.18);transition:background .15s ease,box-shadow .15s ease;}
  #jk-ia-resizer:hover::after,#jk-ia-resizer:focus-visible::after,body.jk-ia-resizing #jk-ia-resizer::after{background:rgba(120,227,212,.74);box-shadow:0 0 12px rgba(120,227,212,.42);}
  body.jk-ia-resizing{cursor:ew-resize;user-select:none;}
  #jk-ia-panel-header{display:flex;align-items:center;gap:8px;padding:12px 14px;border-bottom:1px solid rgba(106,225,203,.18);background:rgba(4,30,48,.7);}
  #jk-ia-panel-header h3{flex:1 1 auto;margin:0;color:#8ee9de;font-size:.96rem;font-weight:700;}
  #jk-ia-panel-header{flex-wrap:nowrap;overflow:hidden;}
  #jk-ia-panel-header h3{min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
  #jk-ia-model-sel{
    width:132px !important;
    min-width:132px !important;
    max-width:132px !important;
    flex:0 0 132px !important;
  }
  .jk-ia-hbtn{border:0;background:transparent;color:#8ee9de;cursor:pointer;font-size:1.05rem;padding:0 12px;border-radius:8px;line-height:1;min-height:36px;min-width:40px;height:36px;display:flex;align-items:center;justify-content:center;pointer-events:auto;touch-action:manipulation;user-select:none;-webkit-user-select:none;}
  .jk-ia-hbtn:hover{background:rgba(19,196,160,.18);transform:scale(1.05);}
  #jk-ia-convs-panel{display:none;flex-direction:column;gap:6px;padding:10px;overflow-y:auto;flex:1;background:rgba(4,20,34,.7);}
  #jk-ia-convs-panel.ativo{display:flex;}
  .jk-ia-conv-item{display:flex;align-items:center;gap:8px;padding:9px 10px;border:1px solid rgba(106,225,203,.22);border-radius:10px;cursor:pointer;background:rgba(15,60,80,.3);}
  .jk-ia-conv-item:hover{background:rgba(36,161,160,.2);border-color:rgba(106,225,203,.5);}
  .jk-ia-conv-item.ativa{border-color:#13c4a0;background:rgba(19,196,160,.12);}
  .jk-ia-conv-info{flex:1 1 auto;overflow:hidden;}
  .jk-ia-conv-data{color:#8ee9de;font-size:.7rem;}
  .jk-ia-conv-prev{color:#cde;font-size:.76rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
  .jk-ia-conv-del{border:0;background:transparent;color:#f87171;cursor:pointer;font-size:.85rem;padding:2px 6px;border-radius:6px;opacity:.7;}
  .jk-ia-conv-del:hover{opacity:1;background:rgba(248,113,113,.15);}
  #jk-ia-chat-wrap{display:flex;flex-direction:column;flex:1;overflow:hidden;}
  #jk-ia-status{color:#bdeee7;font-size:.72rem;padding:6px 12px 2px;background:transparent;}
  #jk-ia-msgs{flex:1 1 auto;overflow-y:auto;display:flex;flex-direction:column;gap:10px;padding:10px;scrollbar-width:none;
    background:rgba(2,18,30,.4);}
  #jk-ia-msgs::-webkit-scrollbar{display:none;}
  .jk-ia-msg{max-width:87%;border-radius:16px;padding:9px 12px;font-size:.83rem;line-height:1.45;white-space:pre-wrap;border:1px solid transparent;}
  .jk-ia-msg.user{align-self:flex-end;color:#fff;background:linear-gradient(165deg,#1888ff,#0f65d8);border-bottom-right-radius:4px;}
  .jk-ia-msg.assistant{align-self:flex-start;color:#e8fffb;background:linear-gradient(165deg,rgba(30,83,123,.7),rgba(20,104,90,.55));border-color:rgba(120,227,212,.34);border-bottom-left-radius:4px;}
  .jk-ia-msg.assistant strong{color:#fff;}
  .jk-ia-msg.assistant a{color:#8ee9de;text-decoration:underline;text-underline-offset:2px;}
  .jk-ia-msg.assistant a:hover{color:#c8fff8;}
  .jk-ia-msg.assistant .jk-ia-img-link{display:inline-block;max-width:100%;margin:8px 0;}
  .jk-ia-msg.assistant .jk-ia-img{display:block;max-width:min(220px,100%);max-height:220px;object-fit:contain;border-radius:10px;border:1px solid rgba(120,227,212,.32);background:rgba(4,26,35,.88);padding:4px;}
  .jk-ia-msg.assistant .jk-ia-img-error{border-color:rgba(255,138,128,.55);}
  .jk-ia-msg.assistant .jk-ia-file-card{display:flex;align-items:center;gap:10px;max-width:100%;margin:8px 0;padding:9px 10px;border:1px solid rgba(120,227,212,.3);border-radius:10px;background:rgba(4,26,35,.72);color:#e8fffb;text-decoration:none;}
  .jk-ia-msg.assistant .jk-ia-file-card:hover{border-color:rgba(120,227,212,.62);background:rgba(7,43,57,.86);}
  .jk-ia-msg.assistant .jk-ia-file-icon{flex:0 0 34px;width:34px;height:34px;border-radius:8px;display:inline-flex;align-items:center;justify-content:center;background:rgba(83,212,183,.16);border:1px solid rgba(120,227,212,.24);font-size:.72rem;font-weight:900;color:#8ee9de;}
  .jk-ia-msg.assistant .jk-ia-file-info{min-width:0;display:grid;gap:2px;}
  .jk-ia-msg.assistant .jk-ia-file-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:800;color:#fff;}
  .jk-ia-msg.assistant .jk-ia-file-action{font-size:.74rem;color:#9ee8df;}
  .jk-ia-msg.assistant code{background:rgba(5,28,47,.5);border:1px solid rgba(120,227,212,.3);border-radius:5px;padding:1px 5px;font-size:.77rem;}
  .jk-ia-msg.assistant h1,.jk-ia-msg.assistant h2,.jk-ia-msg.assistant h3,.jk-ia-msg.assistant h4{margin:10px 0 6px;line-height:1.25;color:#fff;}
  .jk-ia-msg.assistant h1{font-size:1.04rem;}
  .jk-ia-msg.assistant h2{font-size:.98rem;}
  .jk-ia-msg.assistant h3{font-size:.93rem;}
  .jk-ia-msg.assistant h4{font-size:.89rem;}
  .jk-ia-msg.assistant p{margin:6px 0;}
  .jk-ia-msg.assistant ul,.jk-ia-msg.assistant ol{margin:6px 0 8px 18px;padding:0;}
  .jk-ia-msg.assistant li{margin:3px 0;}
  .jk-ia-msg.assistant .jk-ia-task-list{list-style:none;margin-left:0;}
  .jk-ia-msg.assistant .jk-ia-task-item{display:flex;align-items:flex-start;gap:7px;}
  .jk-ia-msg.assistant .jk-ia-task-item input{margin-top:3px;accent-color:#53d4b7;}
  .jk-ia-msg.assistant blockquote{margin:8px 0;padding:6px 10px;border-left:3px solid rgba(120,227,212,.6);background:rgba(6,36,51,.45);border-radius:6px;}
  .jk-ia-msg.assistant pre{margin:8px 0;padding:8px 10px;border-radius:8px;background:rgba(5,24,35,.92);border:1px solid rgba(120,227,212,.25);overflow-x:auto;white-space:pre;font-size:.75rem;}
  .jk-ia-msg.assistant hr{border:0;border-top:1px solid rgba(120,227,212,.25);margin:10px 0;}
  .jk-ia-msg.assistant table{width:100%;border-collapse:collapse;margin:8px 0;font-size:.76rem;background:rgba(6,36,51,.45);border:1px solid rgba(120,227,212,.25);border-radius:8px;overflow:hidden;display:block;overflow-x:auto;}
  .jk-ia-msg.assistant thead tr{background:rgba(15,70,85,.45);}
  .jk-ia-msg.assistant th,.jk-ia-msg.assistant td{border:1px solid rgba(120,227,212,.2);padding:6px 8px;text-align:left;white-space:nowrap;}
  .jk-ia-approval-card{display:grid;gap:8px;}
  .jk-ia-approval-title{font-weight:900;color:#fff;}
  .jk-ia-approval-meta{font-size:.73rem;color:#9ee8df;line-height:1.35;}
  .jk-ia-approval-question,.jk-ia-approval-answer{padding:8px;border-radius:8px;background:rgba(4,26,35,.56);border:1px solid rgba(120,227,212,.18);}
  .jk-ia-approval-conversation{display:grid;gap:7px;max-height:280px;overflow:auto;padding:8px;border-radius:8px;background:rgba(4,26,35,.38);border:1px solid rgba(120,227,212,.18);scrollbar-width:thin;scrollbar-color:rgba(120,227,212,.36) transparent;}
  .jk-ia-approval-conversation::-webkit-scrollbar{width:6px;height:6px;}
  .jk-ia-approval-conversation::-webkit-scrollbar-thumb{background:rgba(120,227,212,.28);border-radius:999px;}
  .jk-ia-approval-message{padding:7px 8px;border-radius:9px;border:1px solid rgba(120,227,212,.14);background:rgba(11,48,64,.5);}
  .jk-ia-approval-message.seller{background:rgba(16,95,76,.34);margin-left:18px;}
  .jk-ia-approval-message.buyer{background:rgba(23,47,70,.55);margin-right:18px;}
  .jk-ia-approval-message-head{font-size:.67rem;text-transform:uppercase;letter-spacing:.02em;color:#9ee8df;font-weight:900;margin-bottom:4px;}
  .jk-ia-approval-attachments{display:flex;flex-wrap:wrap;gap:6px;margin-top:7px;}
  .jk-ia-approval-attachment-img{display:block;width:76px;height:76px;object-fit:cover;border-radius:9px;border:1px solid rgba(120,227,212,.28);background:rgba(2,19,29,.75);}
  .jk-ia-approval-file{display:inline-flex;align-items:center;min-height:30px;padding:5px 8px;border-radius:8px;border:1px solid rgba(120,227,212,.26);color:#e8fffb;text-decoration:none;background:rgba(2,19,29,.55);font-size:.72rem;}
  .jk-ia-approval-edit{width:100%;min-height:130px;resize:vertical;border-radius:8px;border:1px solid rgba(120,227,212,.26);background:rgba(3,22,32,.82);color:#e8fffb;padding:8px;font:inherit;font-size:.78rem;line-height:1.45;outline:none;}
  .jk-ia-approval-edit:focus{border-color:#53d4b7;box-shadow:0 0 0 2px rgba(83,212,183,.16);}
  .jk-ia-approval-label{font-size:.7rem;text-transform:uppercase;letter-spacing:.03em;color:#8ee9de;font-weight:900;margin-bottom:4px;}
  .jk-ia-approval-actions{display:flex;flex-wrap:wrap;gap:8px;margin-top:2px;}
  .jk-ia-approval-btn{border:1px solid rgba(120,227,212,.36);border-radius:8px;background:rgba(35,142,165,.22);color:#e8fffb;font-weight:900;font-size:.75rem;min-height:34px;padding:0 10px;cursor:pointer;}
  .jk-ia-approval-btn.primary{background:linear-gradient(165deg,#53d4b7,#33a7d7);border:0;color:#03272f;}
  .jk-ia-approval-btn.danger{border-color:rgba(248,113,113,.45);background:rgba(248,113,113,.16);color:#ffd7d7;}
  .jk-ia-approval-btn:disabled{cursor:not-allowed;opacity:.56;}
  .jk-ia-approval-context-status{align-self:center;flex:1 1 100%;min-height:16px;color:#9ee8df;font-size:.7rem;font-weight:800;line-height:1.35;}
  .jk-ia-approval-status-btn{display:inline-flex;align-items:center;justify-content:center;gap:7px;width:100%;min-height:34px;border-radius:8px;border:1px solid rgba(120,227,212,.34);background:rgba(21,94,79,.24);color:#c9fff5;font-weight:900;font-size:.75rem;padding:0 10px;cursor:default;}
  .jk-ia-approval-status-btn.approved{border-color:rgba(83,212,183,.56);background:rgba(21,128,101,.32);color:#d8fff8;}
  .jk-ia-approval-status-btn.rejected{border-color:rgba(248,113,113,.45);background:rgba(248,113,113,.14);color:#ffd7d7;}
  .jk-ia-approval-status-btn.error{border-color:rgba(251,191,36,.48);background:rgba(251,191,36,.13);color:#fff0bd;}
  .jk-ia-approval-resolved{font-size:.72rem;font-weight:900;color:#9ee8df;border:1px solid rgba(120,227,212,.22);background:rgba(21,94,79,.25);border-radius:8px;padding:7px 8px;}
  .jk-ia-approval-card[data-resolved="1"] .jk-ia-approval-edit{opacity:.68;}
  .jk-ia-msg.loading{color:#b8c7d9;font-style:italic;}
  #jk-ia-anexos{display:flex;flex-wrap:wrap;gap:5px;padding:0 10px;}
  .jk-ia-anx-item{display:inline-flex;align-items:center;gap:5px;border:1px solid rgba(120,227,212,.3);
    background:rgba(25,120,133,.2);border-radius:999px;color:#dbfffb;font-size:.7rem;padding:3px 8px;}
  .jk-ia-anx-item span{max-width:150px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
  .jk-ia-anx-del{border:0;background:transparent;color:#ffd2d2;cursor:pointer;font-size:.85rem;padding:0;line-height:1;}
  #jk-ia-input-row{display:flex;align-items:flex-end;gap:7px;border:1px solid rgba(120,227,212,.26);border-radius:16px;
    background:rgba(4,26,35,.9);padding:7px 8px;margin:6px 10px 12px;}
  #jk-ia-input{flex:1 1 auto;resize:none;min-height:40px;max-height:130px;border-radius:11px;border:0;
    background:transparent;color:#e5e5e5;font:inherit;font-size:.81rem;padding:8px 6px;outline:none;}
  .jk-ia-ibtn{flex:0 0 44px;width:44px;height:44px;border-radius:999px;border:1px solid rgba(120,227,212,.36);
    background:rgba(35,142,165,.22);color:#dbfffb;font-size:1.05rem;cursor:pointer;padding:0;display:flex;align-items:center;justify-content:center;}
  .jk-ia-ibtn:hover{border-color:rgba(120,227,212,.7);background:rgba(36,161,160,.3);transform:scale(1.08);}
  #jk-ia-send{background:linear-gradient(165deg,#53d4b7,#33a7d7);border:0;color:#03272f;font-weight:900;flex:0 0 44px;width:44px;height:44px;font-size:1.1rem;display:flex;align-items:center;justify-content:center;}
  `;

  /* ── HTML do widget ── */
  const HTML = `
  <div id="jk-left-sidebar-hotspot" aria-label="Menu lateral esquerdo" tabindex="0">
    <nav id="jk-left-sidebar-menu" aria-label="Modulos do sistema"></nav>
  </div>
  <div id="jk-right-sidebar-hotspot" aria-label="Menu lateral direito" tabindex="0">
    <div id="jk-right-sidebar-menu" role="toolbar" aria-label="Atalhos laterais">
      <button id="jk-ia-fab" class="jk-right-sidebar-icon" title="Assistente IA" aria-label="Abrir assistente IA">&#129302;</button>
      <button id="jk-msg-fab" class="jk-right-sidebar-icon" title="Mensagens" aria-label="Abrir mensagens">&#128172;<span id="jk-msg-badge" aria-label="Mensagens nao lidas"></span></button>
    </div>
  </div>
  <aside id="jk-ia-panel" role="complementary" aria-label="Assistente IA">
    <div id="jk-ia-resizer" role="separator" aria-orientation="vertical" aria-label="Redimensionar assistente IA" tabindex="0"></div>
    <div id="jk-ia-panel-header">
      <h3>🤖 Assistente IA</h3>
      <select id="jk-ia-model-sel" title="Modelo de IA" style="display:none;background:#0b3040;border:1px solid rgba(120,227,212,.3);color:#8ee9de;font-size:.68rem;border-radius:8px;padding:6px 8px;cursor:pointer;max-width:90px;min-height:36px;">
        <optgroup label="OpenAI">
          <option value="gpt-5.4-nano">Nano</option>
          <option value="gpt-5.4-mini">Mini</option>
          <option value="gpt-5.4">GPT-5.4</option>
          <option value="gpt-5.5">GPT-5.5</option>
        </optgroup>
        <optgroup label="DeepSeek">
          <option value="deepseek-v4-flash">DS V4 Flash</option>
          <option value="deepseek-v4-pro">DS V4 Pro</option>
        </optgroup>
        <optgroup label="Gemini API">
          <option value="gemini-2.5-flash">Gemini 2.5 Flash</option>
          <option value="gemini-2.5-pro">Gemini 2.5 Pro</option>
        </optgroup>
        <optgroup label="Vertex AI (Google Cloud)">
          <option value="vertex:gemini-2.5-flash">Vertex 2.5 Flash</option>
          <option value="vertex:gemini-2.5-pro">Vertex 2.5 Pro</option>
          <option value="vertex:gemini-2.5-flash-lite">Vertex 2.5 Flash-Lite</option>
          <option value="vertex:gemini-2.0-flash">Vertex 2.0 Flash</option>
        </optgroup>
      </select>
      <button class="jk-ia-hbtn" id="jk-ia-btn-historico" title="Ver conversas">🗂</button>
      <button class="jk-ia-hbtn" id="jk-ia-btn-nova" title="Nova conversa">✏️</button>
      <button class="jk-ia-hbtn" id="jk-ia-btn-fechar" title="Fechar">✕</button>
    </div>
    <div id="jk-ia-convs-panel">
      <div style="color:#8ee9de;font-size:.8rem;font-weight:700;padding:4px 0 8px;">Conversas salvas</div>
      <div id="jk-ia-convs-lista"></div>
    </div>
    <div id="jk-ia-chat-wrap">
      <div id="jk-ia-status">Assistente conectado a este módulo.</div>
      <div id="jk-ia-msgs" aria-live="polite"></div>
      <div id="jk-ia-anexos"></div>
      <div id="jk-ia-input-row">
        <button class="jk-ia-ibtn" id="jk-ia-btn-img" title="Enviar foto">🖼</button>
        <button class="jk-ia-ibtn" id="jk-ia-btn-arq" title="Enviar arquivo">📎</button>
        <textarea id="jk-ia-input" placeholder="Pergunte sobre este módulo..." rows="1"></textarea>
        <button class="jk-ia-ibtn" id="jk-ia-send" title="Enviar">↑</button>
      </div>
    </div>
    <input type="file" id="jk-ia-file-img" accept="image/*" multiple style="display:none">
    <input type="file" id="jk-ia-file-arq" accept=".pdf,.txt,.csv,.json,.xml,.md,.log,.xlsx,.xls" multiple style="display:none">
  </aside>
  <aside id="jk-msg-panel" role="complementary" aria-label="Mensagens">
    <div id="jk-msg-header">
      <h3>&#128172; Mensagens</h3>
      <button class="jk-msg-hbtn" id="jk-msg-btn-refresh" title="Atualizar">&#8635;</button>
      <button class="jk-msg-hbtn" id="jk-msg-btn-fechar" title="Fechar">&times;</button>
    </div>
    <div id="jk-msg-body">
      <div class="jk-msg-status" id="jk-msg-status">Aguardando abertura do painel.</div>
      <div id="jk-msg-main-view">
        <section class="jk-msg-section" aria-label="Mensagens recebidas">
          <div class="jk-msg-section-title">Mensagens recebidas</div>
          <div class="jk-msg-list" id="jk-msg-inbox-list"></div>
        </section>
        <section class="jk-msg-section" aria-label="Usuarios">
          <div class="jk-msg-section-title">Usuarios</div>
          <div class="jk-msg-list" id="jk-msg-online-list"></div>
        </section>
      </div>
      <div id="jk-msg-chat-header">
        <button id="jk-msg-back" type="button" title="Voltar para usuarios">&#8592;</button>
        <div id="jk-msg-chat-title">Chat</div>
      </div>
      <div id="jk-msg-history" aria-live="polite"></div>
      <div id="jk-msg-typing" aria-live="polite"></div>
    </div>
    <div id="jk-msg-compose">
      <div id="jk-msg-selected">Selecione um usuario para enviar mensagem.</div>
      <div id="jk-msg-tools" aria-label="Ferramentas da mensagem">
        <button class="jk-msg-tool-btn" id="jk-msg-emoji-btn" type="button" title="Emoji">&#9786;</button>
        <button class="jk-msg-tool-btn" id="jk-msg-img-btn" type="button" title="Imagem ou GIF">IMG</button>
        <button class="jk-msg-tool-btn" id="jk-msg-file-btn" type="button" title="Arquivo">&#128206;</button>
        <button class="jk-msg-tool-btn" id="jk-msg-audio-btn" type="button" title="Gravar audio">&#127908;</button>
      </div>
      <div id="jk-msg-emoji-panel" aria-label="Escolher emoji"></div>
      <div id="jk-msg-anexos" aria-live="polite"></div>
      <textarea id="jk-msg-text" maxlength="2000" placeholder="Digite sua mensagem..." aria-label="Texto da mensagem"></textarea>
      <button id="jk-msg-send" type="button">Enviar mensagem</button>
      <input type="file" id="jk-msg-img-input" accept="image/*,.gif" multiple style="display:none">
      <input type="file" id="jk-msg-file-input" multiple style="display:none">
    </div>
  </aside>
  `;

  function _nomeModeloLimpo(item) {
    const nomesFixos = {
      'gpt-5.4-nano': 'Nano',
      'gpt-5.4-mini': 'Mini',
      'gpt-5.4': 'GPT-5.4',
      'gpt-5.5': 'GPT-5.5',
      'deepseek-v4-flash': 'DS V4 Flash',
      'deepseek-v4-pro': 'DS V4 Pro',
    };
    const nome = String(item?.name || '').trim();
    if (nomesFixos[nome]) return nomesFixos[nome];
    return String(item?.display_name || nome || '').replace(/[�]/g, '').trim();
  }

  function _substituirGrupoModelos(selectEl, label, modelos) {
    if (!selectEl || !Array.isArray(modelos) || !modelos.length) return;
    let grupo = Array.from(selectEl.querySelectorAll('optgroup')).find(el => el.label === label);
    if (!grupo) {
      grupo = document.createElement('optgroup');
      grupo.label = label;
      selectEl.appendChild(grupo);
    }
    grupo.innerHTML = '';
    modelos.forEach(item => {
      const option = document.createElement('option');
      option.value = item.name;
      option.textContent = _nomeModeloLimpo(item);
      if (item.description) option.title = item.description;
      grupo.appendChild(option);
    });
  }

  async function _carregarModelosRemotos(selectEl, storageKey) {
    if (!selectEl) return;
    const valorSalvo = _usuarioLocalEhAdmin() ? (localStorage.getItem(storageKey) || selectEl.value) : '';
    const urls = ['/api/ia/modelos'];
    if (location.hostname === '127.0.0.1' || location.hostname === 'localhost') {
      urls.push('http://127.0.0.1:8012/api/ia/modelos');
    }

    for (const url of urls) {
      try {
        const resp = await fetch(url, { method: 'GET', headers: _authHeaders() });
        if (!resp.ok) {
          if (resp.status !== 405) break;
          continue;
        }
        const data = await resp.json().catch(() => null);
        if (!data || !data.success) return;
        const podeEscolher = data.pode_escolher_modelo_chat === true;
        _aplicarPermissaoModeloChat(selectEl, podeEscolher);
        _substituirGrupoModelos(selectEl, 'OpenAI', data.openai || []);
        _substituirGrupoModelos(selectEl, 'DeepSeek', data.deepseek || []);
        _substituirGrupoModelos(selectEl, 'Gemini API', data.gemini || []);
        _substituirGrupoModelos(selectEl, 'Vertex AI (Google Cloud)', data.vertex || []);
        const valores = Array.from(selectEl.options).map(opt => opt.value);
        const modeloSistema = data?.defaults?.chat || data?.defaults?.sistema || '';
        const valorPreferido = podeEscolher && valores.includes(valorSalvo)
          ? valorSalvo
          : (valores.includes(modeloSistema) ? modeloSistema : selectEl.value);
        if (valorPreferido) selectEl.value = valorPreferido;
        return;
      } catch (_) {}
    }
  }

  /* ═══════════════ Inicialização ═══════════════ */
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
    let msgPanelAberto = false;
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
    let msgMensagensCache = [];
    let msgHistoricoCache = new Map();
    const PANEL_WIDTH_KEY = 'jk_ia_sidebar_width_px';
    const PANEL_MIN_WIDTH = 300;
    const PANEL_MAX_WIDTH = 760;
    const MSG_NOTIFICACOES_KEY = 'jk_msg_notificacoes_exibidas_v1';
    const MSG_REFRESH_INTERVAL_MS = 10000;
    const MSG_ATTACHMENT_MAX_COUNT = 6;
    const MSG_ATTACHMENT_MAX_BYTES = 700 * 1024;
    const MSG_ATTACHMENT_TOTAL_MAX_BYTES = 900 * 1024;
    const MSG_TYPING_POLL_MS = 2000;
    const MSG_USUARIOS_CACHE_KEY = 'jk_msg_usuarios_cache_v1';
    const MSG_USUARIOS_CACHE_TTL_MS = 5 * 60 * 1000;
    const MSG_HISTORY_LIMIT = 50;
    const PERGUNTAS_APPROVALS_NOTIFY_KEY = 'jk_perguntas_aprovacoes_notificadas_v1';
    const PERGUNTAS_MONITOR_OWNER_KEY = 'jk_perguntas_monitor_owner_v1';
    const PERGUNTAS_MONITOR_INTERVAL_MS = 15000;
    const PERGUNTAS_MONITOR_STALE_MS = 45000;
    const perguntasMonitorId = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    let perguntasMonitorTimer = null;
    let perguntasMonitorRodando = false;
    let perguntasAprovacoesNotificadas = null;
    const MODULOS_LATERAIS = [
      { key: 'analise_promo', href: 'frontend_promo.html', icon: '&#128200;', label: 'Promocao ML' },
      { key: 'renovacao_fixa', href: 'renovacao.html', icon: '&#128260;', label: 'Renovacao Fixa' },
      { key: 'anuncios_ml', href: 'anunciosml.html', icon: '&#128230;', label: 'Anuncios ML' },
      { key: 'etiquetas', href: 'frontend_etiquetas.html', icon: '&#127991;', label: 'Etiquetas' },
      { key: 'integracao', href: 'integracoes.html', icon: '&#128279;', label: 'Integracoes' },
      { key: 'cadastro', href: 'cadastro.html', icon: '&#129534;', label: 'Cadastro' },
      { key: 'estoque', href: 'estoque.html', icon: '&#128230;', label: 'Estoque' },
      { key: 'mercado_full', href: 'full.html', icon: '&#128666;', label: 'Full' },
      { key: 'vendas', href: 'vendas.html', icon: '&#128176;', label: 'Vendas' },
      { key: 'favoritos', href: 'favoritos.html', icon: '&#11088;', label: 'Favoritos ML' },
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
      'debug_vendas.html': 'vendas',
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
      return permissions.full === true || permissions[mod.key] === true;
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
      const modulos = MODULOS_LATERAIS.filter(mod => mod.key !== atual && _leftModuloPermitido(mod));
      menu.innerHTML = '';
      modulos.forEach(mod => {
        const link = document.createElement('a');
        link.className = 'jk-left-module-link';
        link.href = mod.href;
        link.title = mod.label;
        link.setAttribute('aria-label', `Abrir modulo ${mod.label}`);
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

        link.appendChild(icon);
        link.appendChild(label);
        menu.appendChild(link);
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

    _leftRenderModulos();
    setupPanelResize();

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
        const body = String(conversa.last_message || 'Nova mensagem recebida.').trim();
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
      return String(approval && (approval.tipo || approval.approval_type) || '').toLowerCase() === 'pos_venda';
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
          mostrarChat();
          togglePanel(true);
          try { notificacao.close(); } catch (_) {}
        };
        setTimeout(() => {
          try { notificacao.close(); } catch (_) {}
        }, 11000);
      } catch (_) {}
    }

    async function _perguntasNotificarWindowsAprovacaoUmaVez(approval) {
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
      const approvalId = _perguntasApprovalId(approval);
      if (!approvalId) return;
      _adicionarNotificacaoAprovacao(approval, { abrirPainel: false });
      await _perguntasNotificarWindowsAprovacaoUmaVez(approval);
    }

    async function _perguntasMonitorBuscarAprovacoes() {
      if (perguntasMonitorRodando || !_token()) return;
      if (!_perguntasMonitorAssumirLideranca()) return;
      perguntasMonitorRodando = true;
      try {
        const response = await fetch('/api/mercadolivre/perguntas/aprovacoes', {
          headers: _authHeaders(),
          cache: 'no-store',
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || data.success === false) return;
        const pendentes = Array.isArray(data.pendentes) ? data.pendentes : [];
        for (const approval of pendentes) {
          await _perguntasNotificarAprovacaoPendente(approval);
        }
      } catch (_) {
      } finally {
        perguntasMonitorRodando = false;
        _perguntasMonitorAssumirLideranca();
      }
    }

    function _perguntasIniciarMonitorGlobal() {
      if (perguntasMonitorTimer || !_token()) return;
      void _perguntasMonitorBuscarAprovacoes();
      perguntasMonitorTimer = setInterval(() => {
        void _perguntasMonitorBuscarAprovacoes();
      }, PERGUNTAS_MONITOR_INTERVAL_MS);
      window.addEventListener('beforeunload', _perguntasMonitorLiberarLideranca);
    }

    function _msgLabelUsuario(user) {
      return String((user && (user.name || user.username || user.email)) || 'Usuario').trim();
    }

    function _msgChaveUsuario(username, clientId) {
      const user = String(username || '').trim().toLowerCase();
      const client = String(clientId || _clientId() || 'default').trim() || 'default';
      return `${user}|${client}`;
    }

    function _msgChaveHistorico(user) {
      return _msgChaveUsuario(user && user.username, user && user.client_id);
    }

    function _msgLerUsuariosCache() {
      if (msgUsuariosCache.length && Date.now() - msgUsuariosCacheTs < MSG_USUARIOS_CACHE_TTL_MS) {
        return msgUsuariosCache;
      }
      try {
        const raw = JSON.parse(localStorage.getItem(MSG_USUARIOS_CACHE_KEY) || '{}') || {};
        const lista = Array.isArray(raw.users) ? raw.users : [];
        const ts = Number(raw.ts || 0);
        if (lista.length && Date.now() - ts < MSG_USUARIOS_CACHE_TTL_MS) {
          msgUsuariosCache = lista;
          msgUsuariosCacheTs = ts;
          return lista;
        }
      } catch (_) {}
      return [];
    }

    function _msgSalvarUsuariosCache(usuarios) {
      const lista = Array.isArray(usuarios) ? usuarios : [];
      if (!lista.length) return;
      msgUsuariosCache = lista;
      msgUsuariosCacheTs = Date.now();
      try {
        localStorage.setItem(MSG_USUARIOS_CACHE_KEY, JSON.stringify({ ts: msgUsuariosCacheTs, users: lista.slice(0, 80) }));
      } catch (_) {}
    }

    function _msgFormatarHorarioVisto(raw) {
      const texto = String(raw || '').trim();
      if (!texto) return '';
      const data = new Date(texto.replace(' ', 'T'));
      if (Number.isNaN(data.getTime())) return texto;
      const agora = new Date();
      const mesmoDia = data.getFullYear() === agora.getFullYear()
        && data.getMonth() === agora.getMonth()
        && data.getDate() === agora.getDate();
      return mesmoDia
        ? data.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })
        : data.toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
    }

    function _msgStatusUsuarioTexto(user) {
      if (user && user.online !== false) return 'Online';
      const direto = String(user && user.last_seen_at || '').trim();
      const maquina = Array.isArray(user && user.all_recent_machines) && user.all_recent_machines[0]
        ? String(user.all_recent_machines[0].last_seen_at || '').trim()
        : '';
      const visto = _msgFormatarHorarioVisto(direto || maquina);
      return visto ? `Offline - visto ${visto}` : 'Offline';
    }

    function _msgRenderVazio(el, texto) {
      if (!el) return;
      el.innerHTML = '';
      const vazio = document.createElement('div');
      vazio.className = 'jk-msg-empty';
      vazio.textContent = texto;
      el.appendChild(vazio);
    }

    function _msgMimeExt(mime) {
      const tipo = String(mime || '').toLowerCase();
      if (tipo.includes('ogg')) return 'ogg';
      if (tipo.includes('mpeg') || tipo.includes('mp3')) return 'mp3';
      if (tipo.includes('wav')) return 'wav';
      if (tipo.includes('webm')) return 'webm';
      return 'webm';
    }

    function _msgDataUrl(anexo) {
      if (!anexo || !anexo.data_base64) return '';
      return `data:${String(anexo.mime_type || 'application/octet-stream')};base64,${anexo.data_base64}`;
    }

    function _msgTotalAnexosBytes(lista = msgAnexos) {
      return (Array.isArray(lista) ? lista : []).reduce((acc, item) => acc + Number(item && item.size || 0), 0);
    }

    function _msgRenderAnexosComposer() {
      const el = document.getElementById('jk-msg-anexos');
      if (!el) return;
      el.innerHTML = '';
      msgAnexos.forEach((anexo, index) => {
        const chip = document.createElement('div');
        chip.className = 'jk-msg-anexo-chip';
        const nome = document.createElement('span');
        nome.textContent = String(anexo.name || 'arquivo');
        const remover = document.createElement('button');
        remover.type = 'button';
        remover.title = 'Remover anexo';
        remover.textContent = 'x';
        remover.addEventListener('click', () => {
          msgAnexos.splice(index, 1);
          _msgRenderAnexosComposer();
        });
        chip.appendChild(nome);
        chip.appendChild(remover);
        el.appendChild(chip);
      });
    }

    function _msgAdicionarAnexo(anexo) {
      if (!anexo || !anexo.data_base64) return false;
      if (msgAnexos.length >= MSG_ATTACHMENT_MAX_COUNT) {
        _msgSetStatus(`Limite de ${MSG_ATTACHMENT_MAX_COUNT} anexos por mensagem.`, true);
        return false;
      }
      const tamanho = Number(anexo.size || 0);
      if (tamanho > MSG_ATTACHMENT_MAX_BYTES) {
        _msgSetStatus('Anexo muito grande. Limite de 700 KB por arquivo.', true);
        return false;
      }
      if (_msgTotalAnexosBytes() + tamanho > MSG_ATTACHMENT_TOTAL_MAX_BYTES) {
        _msgSetStatus('Anexos muito grandes. Limite total de 900 KB por mensagem.', true);
        return false;
      }
      msgAnexos.push(anexo);
      _msgRenderAnexosComposer();
      return true;
    }

    function _msgBlobParaAnexo(blob, nome) {
      return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => {
          const dataUrl = String(reader.result || '');
          const base64 = dataUrl.includes(',') ? dataUrl.split(',').pop() : '';
          resolve({
            name: nome || 'audio.webm',
            mime_type: blob.type || 'application/octet-stream',
            data_base64: base64 || '',
            size: Number(blob.size || 0),
          });
        };
        reader.onerror = reject;
        reader.readAsDataURL(blob);
      });
    }

    async function _msgArquivosSelecionados(files) {
      const lista = Array.from(files || []);
      for (const file of lista) {
        const anexo = await _msgBlobParaAnexo(file, file.name || 'arquivo');
        _msgAdicionarAnexo(anexo);
      }
    }

    function _msgRenderAnexoHistorico(anexo) {
      const item = document.createElement('div');
      item.className = 'jk-msg-attachment';
      const url = _msgDataUrl(anexo);
      const nome = String(anexo && anexo.name || 'arquivo');
      const mime = String(anexo && anexo.mime_type || '').toLowerCase();
      if (url && mime.startsWith('image/')) {
        const img = document.createElement('img');
        img.src = url;
        img.alt = nome;
        item.appendChild(img);
        return item;
      }
      if (url && mime.startsWith('audio/')) {
        const audio = document.createElement('audio');
        audio.controls = true;
        audio.src = url;
        item.appendChild(audio);
        return item;
      }
      const link = document.createElement('a');
      link.href = url || '#';
      link.download = nome;
      link.textContent = nome;
      item.appendChild(link);
      return item;
    }

    function _msgRenderAnexosHistorico(bubble, anexos) {
      const lista = Array.isArray(anexos) ? anexos : [];
      if (!bubble || !lista.length) return;
      const wrap = document.createElement('div');
      wrap.className = 'jk-msg-attachments';
      lista.forEach(anexo => wrap.appendChild(_msgRenderAnexoHistorico(anexo)));
      bubble.appendChild(wrap);
    }

    function _msgToggleEmojiPanel() {
      document.getElementById('jk-msg-emoji-panel')?.classList.toggle('aberto');
    }

    function _msgInserirEmoji(emoji) {
      const input = document.getElementById('jk-msg-text');
      if (!input) return;
      const inicio = input.selectionStart || input.value.length;
      const fim = input.selectionEnd || input.value.length;
      input.value = input.value.slice(0, inicio) + emoji + input.value.slice(fim);
      const pos = inicio + emoji.length;
      input.focus();
      try { input.setSelectionRange(pos, pos); } catch (_) {}
    }

    function _msgMontarEmojiPanel() {
      const panel = document.getElementById('jk-msg-emoji-panel');
      if (!panel || panel.dataset.montado === '1') return;
      panel.dataset.montado = '1';
      ['🙂','😀','😂','😍','👍','🙏','👏','🔥','✅','⭐','⚠️','❤️','😎','🤝','📦','💬'].forEach(emoji => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'jk-msg-emoji-choice';
        btn.textContent = emoji;
        btn.addEventListener('click', () => _msgInserirEmoji(emoji));
        panel.appendChild(btn);
      });
    }

    function _msgSetDigitando(ativo, nome = '') {
      const el = document.getElementById('jk-msg-typing');
      if (!el) return;
      const mostrar = !!(ativo && msgChatAberto && msgUsuarioSelecionado);
      el.classList.toggle('ativo', mostrar);
      el.textContent = mostrar ? `${nome || _msgLabelUsuario(msgUsuarioSelecionado)} digitando...` : '';
    }

    async function _msgEnviarDigitando(typing) {
      if (!msgUsuarioSelecionado || !msgUsuarioSelecionado.username) return;
      const valor = !!typing;
      if (msgTypingEnviado === valor) return;
      msgTypingEnviado = valor;
      try {
        await fetch('/api/user/chat/typing', {
          method: 'POST',
          headers: _authHeaders(),
          body: JSON.stringify({
            username: msgUsuarioSelecionado.username,
            client_id: msgUsuarioSelecionado.client_id || _clientId() || 'default',
            typing: valor,
          }),
        });
      } catch (_) {}
    }

    function _msgMarcarDigitandoLocal() {
      if (!msgChatAberto || !msgUsuarioSelecionado) return;
      void _msgEnviarDigitando(true);
      if (msgTypingStopTimer) clearTimeout(msgTypingStopTimer);
      msgTypingStopTimer = setTimeout(() => {
        msgTypingStopTimer = null;
        void _msgEnviarDigitando(false);
      }, 3200);
    }

    function _msgPararDigitandoLocal() {
      if (msgTypingStopTimer) {
        clearTimeout(msgTypingStopTimer);
        msgTypingStopTimer = null;
      }
      void _msgEnviarDigitando(false);
    }

    async function _msgBuscarDigitando() {
      if (!msgPanelAberto || !msgChatAberto || !msgUsuarioSelecionado || !msgUsuarioSelecionado.username) {
        _msgSetDigitando(false);
        return;
      }
      try {
        const params = new URLSearchParams({
          username: String(msgUsuarioSelecionado.username || ''),
          client_id: String(msgUsuarioSelecionado.client_id || _clientId() || 'default'),
        });
        const resp = await fetch(`/api/user/chat/typing?${params.toString()}`, {
          method: 'GET',
          headers: _authHeaders(),
          cache: 'no-store',
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok || data.success === false) throw new Error(data.detail || data.message || 'Erro ao buscar digitacao.');
        _msgSetDigitando(!!data.typing, data.name || _msgLabelUsuario(msgUsuarioSelecionado));
      } catch (_) {
        _msgSetDigitando(false);
      }
    }

    function _msgAtualizarDigitandoPoll() {
      if (msgTypingPollTimer) {
        clearInterval(msgTypingPollTimer);
        msgTypingPollTimer = null;
      }
      _msgSetDigitando(false);
      if (!msgPanelAberto || !msgChatAberto || !msgUsuarioSelecionado) return;
      void _msgBuscarDigitando();
      msgTypingPollTimer = setInterval(() => void _msgBuscarDigitando(), MSG_TYPING_POLL_MS);
    }

    async function _msgToggleGravacaoAudio() {
      const btn = document.getElementById('jk-msg-audio-btn');
      if (msgMediaRecorder && msgMediaRecorder.state === 'recording') {
        msgMediaRecorder.stop();
        if (btn) btn.classList.remove('ativo');
        return;
      }
      if (!navigator.mediaDevices || !window.MediaRecorder) {
        _msgSetStatus('Gravacao de audio indisponivel neste navegador.', true);
        return;
      }
      try {
        msgAudioChunks = [];
        msgAudioStream = await navigator.mediaDevices.getUserMedia({ audio: true });
        msgMediaRecorder = new MediaRecorder(msgAudioStream);
        msgMediaRecorder.ondataavailable = (event) => {
          if (event.data && event.data.size) msgAudioChunks.push(event.data);
        };
        msgMediaRecorder.onstop = async () => {
          try {
            const mime = msgMediaRecorder.mimeType || 'audio/webm';
            const blob = new Blob(msgAudioChunks, { type: mime });
            const nome = `audio_${new Date().toISOString().replace(/[:.]/g, '-')}.${_msgMimeExt(mime)}`;
            const anexo = await _msgBlobParaAnexo(blob, nome);
            _msgAdicionarAnexo(anexo);
          } catch (err) {
            _msgSetStatus(err && err.message ? err.message : 'Nao foi possivel anexar o audio.', true);
          } finally {
            try { (msgAudioStream?.getTracks?.() || []).forEach(track => track.stop()); } catch (_) {}
            msgAudioStream = null;
            msgMediaRecorder = null;
            msgAudioChunks = [];
          }
        };
        msgMediaRecorder.start();
        if (btn) btn.classList.add('ativo');
        _msgSetStatus('Gravando audio. Clique no microfone novamente para finalizar.');
      } catch (err) {
        if (btn) btn.classList.remove('ativo');
        _msgSetStatus('Nao foi possivel acessar o microfone.', true);
      }
    }

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
        if (texto.textContent) bubble.appendChild(texto);
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
        const resp = await fetch(`/api/user/chat/history?${params.toString()}`, {
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
      if (selected) {
        selected.textContent = msgChatAberto && msgUsuarioSelecionado
          ? `Chat com ${_msgLabelUsuario(msgUsuarioSelecionado)}`
          : 'Selecione um usuario para enviar mensagem.';
      }
      if (send) send.disabled = !(msgChatAberto && msgUsuarioSelecionado);
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
        text.textContent = String(msg.message || msg.last_message || 'Anexo').trim();

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
      const extrasNaoLidas = Array.from(msgNaoLidasPorUsuario.values()).filter(item => item && item.username && !vistos.has(_msgChaveUsuario(item.username, item.client_id)));
      const lista = listaBase.concat(extrasNaoLidas.map(item => ({
        username: item.username,
        client_id: item.client_id,
        name: item.name || item.username,
        online: false,
        unread_count: item.unread_count,
      })));
      if (!lista.length) {
        _msgRenderVazio(el, 'Nenhum usuario encontrado.');
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
        btn.className = 'jk-msg-user-item';
        btn.classList.toggle('offline', user.online === false);
        btn.dataset.username = username;
        btn.dataset.clientId = clientId;
        btn.disabled = !!isSelf;

        const dot = document.createElement('span');
        dot.className = 'jk-msg-dot';

        const info = document.createElement('span');
        info.className = 'jk-msg-user-info';
        const name = document.createElement('span');
        name.className = 'jk-msg-user-name';
        const statusUsuario = _msgStatusUsuarioTexto(user);
        name.textContent = `${_msgLabelUsuario(user)}${isSelf ? ' (voce)' : ''} - ${statusUsuario}`;
        name.title = name.textContent;
        const meta = document.createElement('span');
        meta.className = 'jk-msg-user-meta';
        meta.textContent = statusUsuario;
        info.appendChild(name);
        info.appendChild(meta);

        btn.appendChild(dot);
        btn.appendChild(info);
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
      const [adminResult, chatResult] = await Promise.allSettled([
        fetch('/api/user/messages', {
          method: 'GET',
          headers: _authHeaders(),
          cache: 'no-store',
        }).then(async resp => {
          const data = await resp.json().catch(() => ({}));
          if (!resp.ok || data.success === false) throw new Error(data.detail || data.message || 'Erro ao buscar mensagens.');
          return data;
        }),
        fetch('/api/user/chat/unread', {
          method: 'GET',
          headers: _authHeaders(),
          cache: 'no-store',
        }).then(async resp => {
          const data = await resp.json().catch(() => ({}));
          if (!resp.ok || data.success === false) throw new Error(data.detail || data.message || 'Erro ao buscar chats.');
          return data;
        }),
      ]);

      const adminData = adminResult.status === 'fulfilled' ? adminResult.value : {};
      const chatData = chatResult.status === 'fulfilled' ? chatResult.value : {};
      if (adminResult.status === 'rejected' && chatResult.status === 'rejected') {
        throw adminResult.reason || chatResult.reason || new Error('Erro ao buscar mensagens.');
      }

      const adminMessages = (Array.isArray(adminData.messages) ? adminData.messages : []).map(item => ({
        ...item,
        type: 'admin',
      }));
      const chatConversations = Array.isArray(chatData.conversations) ? chatData.conversations : [];
      msgNaoLidasPorUsuario = new Map(chatConversations.map(item => [
        _msgChaveUsuario(item.username, item.client_id),
        {
          username: item.username,
          client_id: item.client_id,
          name: item.name,
          unread_count: Number(item.unread_count || 0),
        },
      ]));
      _msgNotificarConversas(chatConversations);
      const chatMessages = chatConversations.map(item => ({
        type: 'chat',
        username: item.username,
        client_id: item.client_id,
        name: item.name,
        unread_count: item.unread_count,
        last_message_id: item.last_message_id,
        last_message: item.last_message,
        message: item.last_message,
        created_at: item.created_at,
        created_ts: item.created_ts,
      }));
      const total = Number(adminData.unread_count || adminMessages.length || 0) + Number(chatData.unread_count || 0);
      _msgSetBadge(total);
      return [...chatMessages, ...adminMessages];
    }

    async function _msgBuscarUsuariosOnline() {
      try {
        const resp = await fetch('/api/admin/users/online', {
          method: 'GET',
          headers: _authHeaders(),
          cache: 'no-store',
        });
        const data = await resp.json().catch(() => ({}));
        if (resp.ok && data && data.success !== false && Array.isArray(data.users)) {
          const users = data.users.filter(user => user && user.active !== false);
          _msgSalvarUsuariosCache(users);
          return users;
        }
      } catch (_) {}

      if (typeof window.jkBuscarMaquinasOnline === 'function') {
        const data = await window.jkBuscarMaquinasOnline();
        const machines = Array.isArray(data && data.machines) ? data.machines : [];
        if (machines.length) {
          const userData = _msgUserData();
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
            if (lista.length || !usuariosCache.length) _msgRenderUsuarios(lista);
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
        const resp = await fetch(`/api/user/messages/${encodeURIComponent(id)}/read`, {
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
        const resp = await fetch('/api/user/chat/send', {
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

    function _msgIniciarAtualizacao() {
      if (!msgRefreshTimer) {
        msgRefreshTimer = setInterval(() => {
          if (msgPanelAberto) {
            void _msgCarregarPainel(true);
            if (msgChatAberto) void _msgCarregarHistorico(true);
          }
          else void _msgBuscarMensagens().catch(() => {});
        }, MSG_REFRESH_INTERVAL_MS);
      }
    }

    // Carregar ou criar conversa inicial
    async function carregarOuCriarConversa() {
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

    function _approvalCompactarPayload(valor, depth = 0) {
      if (valor == null) return valor;
      if (typeof valor === 'string') return valor.slice(0, 5000);
      if (typeof valor === 'number' || typeof valor === 'boolean') return valor;
      if (depth > 4) return '';
      if (Array.isArray(valor)) {
        return valor.slice(0, 40).map(item => _approvalCompactarPayload(item, depth + 1));
      }
      if (typeof valor === 'object') {
        const saida = {};
        Object.keys(valor).slice(0, 80).forEach((key) => {
          if (['mercadolivre', 'raw', 'debug'].includes(key)) return;
          saida[key] = _approvalCompactarPayload(valor[key], depth + 1);
        });
        return saida;
      }
      return '';
    }

    function _approvalSerializarMensagem(payload) {
      try {
        return APPROVAL_MSG_PREFIX + JSON.stringify(_approvalCompactarPayload(payload || {}));
      } catch (_) {
        return '';
      }
    }

    function _approvalParseMensagem(texto) {
      const raw = String(texto || '').trimStart();
      if (!raw.startsWith(APPROVAL_MSG_PREFIX)) return null;
      try {
        const payload = JSON.parse(raw.slice(APPROVAL_MSG_PREFIX.length));
        return payload && typeof payload === 'object' ? payload : null;
      } catch (_) {
        return null;
      }
    }

    function _approvalIndexNoHistorico(approvalId) {
      const id = String(approvalId || '').trim();
      if (!id) return -1;
      return mensagensAtuais.findIndex((msg) => {
        if (!msg) return false;
        const payload = _approvalParseMensagem(msg.text);
        return String((payload && payload.id) || '').trim() === id;
      });
    }

    function _approvalSalvarNoHistorico(payload) {
      const id = String((payload && payload.id) || '').trim();
      const texto = _approvalSerializarMensagem(payload);
      if (!id || !texto) return;
      const idx = _approvalIndexNoHistorico(id);
      if (idx >= 0) {
        mensagensAtuais[idx] = { role: 'assistant', text: texto };
      } else {
        mensagensAtuais.push({ role: 'assistant', text: texto });
      }
      try {
        void salvarMensagensAtuais();
        if (convsVisible) renderConvsList();
      } catch (_) {}
    }

    function renderMsgs() {
      const msgsEl = document.getElementById('jk-ia-msgs');
      msgsEl.innerHTML = '';
      if (mensagensAtuais.length === 0) {
        addMsg('assistant', 'Olá! Posso analisar dados desta tela, responder dúvidas ou ajudar com próximos passos.', false);
        return;
      }
      mensagensAtuais.forEach(m => {
        const approvalPayload = _approvalParseMensagem(m && m.text);
        const div = document.createElement('div');
        div.className = 'jk-ia-msg ' + (approvalPayload ? 'assistant' : (m.role || 'assistant'));
        if (approvalPayload) {
          div.innerHTML = '';
          _approvalMontarCard(div, approvalPayload);
        } else {
          _definirTextoMsg(div, m && m.text);
        }
        msgsEl.appendChild(div);
      });
      msgsEl.scrollTop = msgsEl.scrollHeight;
    }

    function addMsg(role, texto, salvar = true) {
      const msgsEl = document.getElementById('jk-ia-msgs');
      const approvalPayload = _approvalParseMensagem(texto);
      const div = document.createElement('div');
      div.className = 'jk-ia-msg ' + (approvalPayload ? 'assistant' : role);
      if (approvalPayload) {
        _approvalMontarCard(div, approvalPayload);
      } else {
        _definirTextoMsg(div, texto);
      }
      msgsEl.appendChild(div);
      msgsEl.scrollTop = msgsEl.scrollHeight;
      if (salvar) {
        mensagensAtuais.push({
          role: approvalPayload ? 'assistant' : role,
          text: approvalPayload ? _approvalSerializarMensagem(approvalPayload) : texto,
        });
        try {
          void salvarMensagensAtuais();
          if (convsVisible) renderConvsList();
        } catch (_) {}
      }
      return div;
    }

    function _approvalTexto(payload) {
      const isPosVenda = String(payload.tipo || payload.approval_type || '').toLowerCase() === 'pos_venda';
      return [
        isPosVenda
          ? 'Aprovação necessária para responder conversa pós-venda do Mercado Livre.'
          : 'Aprovação necessária para responder pergunta do Mercado Livre.',
        `Loja: ${payload.loja || '-'}`,
        `${isPosVenda ? 'Venda/Produto' : 'Anúncio'}: ${payload.titulo || payload.item_id || payload.order_id || '-'}`,
        `SKU: ${payload.sku || '-'}`,
        '',
        `${isPosVenda ? 'Mensagem do comprador' : 'Pergunta'}: ${payload.pergunta || '-'}`,
        '',
        `Resposta sugerida: ${payload.resposta_sugerida || '-'}`
      ].join('\n');
    }

    function _approvalMensagens(payload) {
      const conversa = payload && typeof payload.conversa === 'object' ? payload.conversa : {};
      const listas = [
        conversa && conversa.messages,
        payload && payload.messages,
        payload && payload.mensagens,
      ];
      for (const lista of listas) {
        if (Array.isArray(lista) && lista.length) {
          return lista.filter(item => item && typeof item === 'object');
        }
      }
      const pergunta = String((payload && payload.pergunta) || '').trim();
      return pergunta ? [{ from_role: 'buyer', text: pergunta, attachments: [] }] : [];
    }

    function _approvalAnexos(msg) {
      const listas = [msg && msg.attachments, msg && msg.anexos, msg && msg.images, msg && msg.pictures];
      const anexos = [];
      listas.forEach(lista => {
        if (!Array.isArray(lista)) return;
        lista.forEach(item => {
          if (!item) return;
          if (typeof item === 'string') {
            anexos.push({ url: item, name: 'anexo' });
          } else if (typeof item === 'object') {
            anexos.push(item);
          }
        });
      });
      return anexos;
    }

    function _approvalAnexoEhImagem(anexo) {
      const texto = `${anexo && anexo.mime_type || ''} ${anexo && anexo.type || ''} ${anexo && anexo.name || ''} ${anexo && anexo.url || ''}`;
      return (anexo && anexo.is_image === true) || /image|foto|picture|jpg|jpeg|png|webp|gif/i.test(texto);
    }

    function _approvalAppendAnexos(container, anexos) {
      if (!Array.isArray(anexos) || !anexos.length) return;
      const wrap = document.createElement('div');
      wrap.className = 'jk-ia-approval-attachments';
      anexos.forEach((anexo) => {
        const url = String((anexo && (anexo.url || anexo.href || anexo.src)) || '').trim();
        if (!url) return;
        const nome = String((anexo && (anexo.name || anexo.filename || anexo.id)) || 'anexo').trim();
        const link = document.createElement('a');
        link.href = url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        if (_approvalAnexoEhImagem(anexo)) {
          const img = document.createElement('img');
          img.className = 'jk-ia-approval-attachment-img';
          img.src = url;
          img.alt = nome || 'Imagem enviada pelo comprador';
          img.loading = 'lazy';
          img.referrerPolicy = 'no-referrer';
          link.title = nome || 'Abrir imagem';
          link.appendChild(img);
        } else {
          link.className = 'jk-ia-approval-file';
          link.textContent = nome || 'Abrir anexo';
        }
        wrap.appendChild(link);
      });
      if (wrap.childElementCount) container.appendChild(wrap);
    }

    function _approvalCriarConversa(payload) {
      const mensagens = _approvalMensagens(payload);
      const box = document.createElement('div');
      box.className = 'jk-ia-approval-conversation';
      const label = document.createElement('div');
      label.className = 'jk-ia-approval-label';
      label.textContent = 'Conversa completa';
      box.appendChild(label);
      if (!mensagens.length) {
        const vazio = document.createElement('div');
        vazio.textContent = 'Nenhuma mensagem encontrada nesta aprovacao.';
        box.appendChild(vazio);
        return box;
      }
      mensagens.forEach((msg) => {
        const role = String(msg.from_role || msg.role || '').toLowerCase() === 'seller' ? 'seller' : 'buyer';
        const item = document.createElement('div');
        item.className = `jk-ia-approval-message ${role}`;
        const head = document.createElement('div');
        head.className = 'jk-ia-approval-message-head';
        const data = String(msg.date || msg.created_at || msg.message_date || '').trim();
        head.textContent = `${role === 'seller' ? 'Vendedor' : 'Comprador'}${data ? ' - ' + data : ''}`;
        const texto = document.createElement('div');
        texto.textContent = String(msg.text || msg.plain || msg.message || '').trim() || '(mensagem sem texto)';
        item.appendChild(head);
        item.appendChild(texto);
        _approvalAppendAnexos(item, _approvalAnexos(msg));
        box.appendChild(item);
      });
      return box;
    }

    function _approvalStatusTexto(payload) {
      const status = String((payload && payload.status) || '').toLowerCase();
      if (status === 'approved' || status === 'sent') return '\u2713 Aprovado e enviado ao Mercado Livre';
      if (status === 'rejected') return 'Rejeitado. Nada foi enviado ao Mercado Livre';
      if (status === 'answered_elsewhere') return String(payload.status_message || 'Respondida fora da aprova\u00e7\u00e3o.');
      if (status === 'error') return String(payload.status_message || 'N\u00e3o consegui processar essa aprova\u00e7\u00e3o.');
      return String(payload.status_message || '');
    }

    function _approvalStatusClasse(payload) {
      const status = String((payload && payload.status) || '').toLowerCase();
      if (status === 'approved' || status === 'sent') return 'approved';
      if (status === 'rejected') return 'rejected';
      if (status === 'answered_elsewhere') return 'approved';
      if (status === 'error') return 'error';
      return '';
    }

    function _approvalTextoDaMensagem(msg) {
      return String((msg && (msg.text || msg.plain || msg.message)) || '').trim();
    }

    function _approvalPerguntaParaTreinamento(payload) {
      const perguntaDireta = String((payload && payload.pergunta) || '').trim();
      if (perguntaDireta) return perguntaDireta;
      const mensagens = _approvalMensagens(payload);
      for (let i = mensagens.length - 1; i >= 0; i -= 1) {
        const msg = mensagens[i] || {};
        const role = String(msg.from_role || msg.role || '').toLowerCase();
        if (role === 'seller') continue;
        const texto = _approvalTextoDaMensagem(msg);
        if (texto) return texto;
      }
      return '';
    }

    function _approvalNormalizarExemplosTreinamento(exemplos) {
      return (Array.isArray(exemplos) ? exemplos : [])
        .map((item) => ({
          pergunta: String((item && (item.pergunta || item.question)) || '').trim(),
          resposta: String((item && (item.resposta || item.answer)) || '').trim(),
          sku: String((item && item.sku) || '').trim(),
          observacao: String((item && (item.observacao || item.obs)) || '').trim(),
          updated_at: (item && (item.updated_at || item.created_at)) || null,
        }))
        .filter((item) => item.pergunta && item.resposta)
        .slice(0, 60);
    }

    async function _approvalSalvarNoContextoIA(payload, respostaAtual) {
      payload = payload || {};
      const pergunta = _approvalPerguntaParaTreinamento(payload);
      const resposta = String(respostaAtual || '').trim();
      if (!pergunta) throw new Error('Nao encontrei a pergunta do comprador para salvar.');
      if (!resposta) throw new Error('Informe uma resposta antes de salvar no contexto.');

      const tipo = String(payload.tipo || payload.approval_type || '').toLowerCase() === 'pos_venda'
        ? 'pos_venda'
        : 'perguntas_anuncio';
      const sku = String(payload.sku || '').trim();
      const getResp = await fetch('/api/mercadolivre/ia-treinamento', {
        headers: _authHeaders(),
        cache: 'no-store',
      });
      const data = await getResp.json().catch(() => ({}));
      if (!getResp.ok) throw new Error(data.detail || 'Erro ao carregar o contexto da IA.');

      const exemplosAtuais = _approvalNormalizarExemplosTreinamento(((data.exemplos || {})[tipo]) || []);
      const novoExemplo = {
        pergunta,
        resposta,
        sku,
        observacao: 'Salvo a partir da aprovacao da IA',
        updated_at: new Date().toISOString(),
      };
      const perguntaNorm = pergunta.toLowerCase();
      const skuNorm = sku.toLowerCase();
      const exemplos = [
        novoExemplo,
        ...exemplosAtuais.filter((item) => (
          String(item.pergunta || '').toLowerCase() !== perguntaNorm
          || String(item.sku || '').toLowerCase() !== skuNorm
        )),
      ].slice(0, 60);

      const orientacoes = tipo === 'pos_venda'
        ? String(data.orientacoes_pos_venda || '')
        : String(data.orientacoes_perguntas || data.orientacoes || '');

      const postResp = await fetch('/api/mercadolivre/ia-treinamento', {
        method: 'POST',
        headers: _authHeaders(),
        body: JSON.stringify({
          tipo,
          orientacoes,
          contexto_loja: String(data.contexto_loja || ''),
          compatibilidade_autopecas: String(data.compatibilidade_autopecas || ''),
          proibicoes: String(data.proibicoes || ''),
          exemplos,
        }),
      });
      const postData = await postResp.json().catch(() => ({}));
      if (!postResp.ok) throw new Error(postData.detail || 'Erro ao salvar no contexto da IA.');
      return postData;
    }

    function _approvalMontarStatus(card, payload) {
      const texto = _approvalStatusTexto(payload);
      if (!texto) return;
      card.dataset.resolved = '1';
      const statusBtn = document.createElement('button');
      statusBtn.type = 'button';
      statusBtn.disabled = true;
      statusBtn.className = `jk-ia-approval-status-btn ${_approvalStatusClasse(payload)}`;
      statusBtn.textContent = texto;
      card.appendChild(statusBtn);
    }

    function _approvalMontarCard(container, payload) {
      payload = payload || {};
      const approvalId = String(payload.id || '').trim();
      if (!approvalId || !container) return null;
      window.__JK_IA_APPROVAL_NOTIFIED__ = window.__JK_IA_APPROVAL_NOTIFIED__ || {};
      window.__JK_IA_APPROVAL_NOTIFIED__[approvalId] = true;
      const isPosVenda = String(payload.tipo || payload.approval_type || '').toLowerCase() === 'pos_venda';
      const statusAtual = String(payload.status || 'pending').toLowerCase();
      const resolvida = ['approved', 'sent', 'rejected', 'answered_elsewhere'].includes(statusAtual);

      container.innerHTML = '';
      const card = document.createElement('div');
      card.className = 'jk-ia-approval-card';
      card.dataset.approvalId = approvalId;

      const title = document.createElement('div');
      title.className = 'jk-ia-approval-title';
      title.textContent = resolvida ? 'Aprova\u00e7\u00e3o registrada' : 'Aprova\u00e7\u00e3o necess\u00e1ria';
      card.appendChild(title);

      const meta = document.createElement('div');
      meta.className = 'jk-ia-approval-meta';
      meta.textContent = isPosVenda
        ? `Loja ${payload.loja || '-'} - Pack ${payload.pack_id || '-'} - SKU ${payload.sku || '-'}`
        : `Loja ${payload.loja || '-'} - SKU ${payload.sku || '-'} - ${payload.titulo || payload.item_id || '-'}`;
      card.appendChild(meta);

      card.appendChild(_approvalCriarConversa(payload));

      const answerBox = document.createElement('div');
      answerBox.className = 'jk-ia-approval-answer';
      answerBox.innerHTML = '<div class="jk-ia-approval-label">Resposta sugerida pela IA (edite antes de enviar)</div>';
      const answerText = document.createElement('textarea');
      answerText.className = 'jk-ia-approval-edit';
      answerText.value = payload.resposta_sugerida || '';
      answerText.placeholder = 'Edite a resposta antes de aprovar e enviar...';
      answerText.disabled = resolvida;
      answerBox.appendChild(answerText);
      card.appendChild(answerBox);

      const actions = document.createElement('div');
      actions.className = 'jk-ia-approval-actions';
      const saveContext = document.createElement('button');
      saveContext.className = 'jk-ia-approval-btn';
      saveContext.type = 'button';
      saveContext.textContent = 'Salvar no contexto da IA';
      const saveStatus = document.createElement('span');
      saveStatus.className = 'jk-ia-approval-context-status';
      actions.appendChild(saveContext);
      actions.appendChild(saveStatus);

      if (resolvida) {
        card.appendChild(actions);
        _approvalMontarStatus(card, payload);
        container.appendChild(card);
        saveContext.addEventListener('click', async () => {
          saveContext.disabled = true;
          saveStatus.textContent = 'Salvando...';
          try {
            await _approvalSalvarNoContextoIA(payload, answerText.value || payload.resposta_enviada || payload.resposta_sugerida || '');
            saveContext.textContent = 'Salvo no contexto';
            saveStatus.textContent = 'Esta pergunta e resposta entraram no treino da IA.';
          } catch (error) {
            saveContext.disabled = false;
            saveStatus.textContent = error && error.message ? error.message : 'Erro ao salvar no contexto.';
          }
        });
        return card;
      }

      const approve = document.createElement('button');
      approve.className = 'jk-ia-approval-btn primary';
      approve.type = 'button';
      approve.textContent = 'Aprovar e enviar';
      const reject = document.createElement('button');
      reject.className = 'jk-ia-approval-btn danger';
      reject.type = 'button';
      reject.textContent = 'Rejeitar';
      actions.appendChild(approve);
      actions.appendChild(reject);
      card.appendChild(actions);
      container.appendChild(card);

      saveContext.addEventListener('click', async () => {
        saveContext.disabled = true;
        saveStatus.textContent = 'Salvando...';
        try {
          await _approvalSalvarNoContextoIA(payload, answerText.value || '');
          saveContext.textContent = 'Salvo no contexto';
          saveStatus.textContent = 'Esta pergunta e resposta entraram no treino da IA.';
        } catch (error) {
          saveContext.disabled = false;
          saveStatus.textContent = error && error.message ? error.message : 'Erro ao salvar no contexto.';
        }
      });

      async function responderAcao(url, statusFinal, enviarResposta = false) {
        approve.disabled = true;
        reject.disabled = true;
        saveContext.disabled = true;
        try {
          const body = { approval_id: approvalId };
          if (enviarResposta) body.resposta = answerText.value || '';
          const response = await fetch(url, {
            method: 'POST',
            headers: _authHeaders(),
            body: JSON.stringify(body),
          });
          const data = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(data.detail || 'Falha ao processar aprova\u00e7\u00e3o.');
          const atualizado = {
            ...payload,
            resposta_sugerida: answerText.value || payload.resposta_sugerida || '',
            status: statusFinal,
            status_message: statusFinal === 'approved'
              ? '\u2713 Aprovado e enviado ao Mercado Livre'
              : 'Rejeitado. Nada foi enviado ao Mercado Livre',
            resolved_at: new Date().toISOString(),
          };
          _approvalSalvarNoHistorico(atualizado);
          _approvalMontarCard(container, atualizado);
        } catch (error) {
          approve.disabled = false;
          reject.disabled = false;
          saveContext.disabled = false;
          const erroTexto = `N\u00e3o consegui processar essa aprova\u00e7\u00e3o: ${error && error.message ? error.message : error}`;
          const erro = { ...payload, status: 'error', status_message: erroTexto };
          const antigo = card.querySelector('.jk-ia-approval-status-btn.error');
          if (antigo) antigo.remove();
          _approvalMontarStatus(card, erro);
        }
      }

      approve.addEventListener('click', () => responderAcao('/api/mercadolivre/perguntas/aprovacoes/aprovar', 'approved', true));
      reject.addEventListener('click', () => responderAcao('/api/mercadolivre/perguntas/aprovacoes/rejeitar', 'rejected'));
      return card;
    }

    function _adicionarNotificacaoAprovacao(payload, options = {}) {
      payload = payload || {};
      const approvalId = String(payload.id || '').trim();
      if (!approvalId) return;
      const abrirPainel = options.abrirPainel !== false;
      window.__JK_IA_APPROVAL_NOTIFIED__ = window.__JK_IA_APPROVAL_NOTIFIED__ || {};
      const idxExistente = _approvalIndexNoHistorico(approvalId);
      const jaNotificada = !!window.__JK_IA_APPROVAL_NOTIFIED__[approvalId];
      if (jaNotificada && idxExistente >= 0) {
        if (abrirPainel) {
          mostrarChat();
          togglePanel(true);
        } else {
          _iaAvisarMensagemRecebida();
        }
        return;
      }
      window.__JK_IA_APPROVAL_NOTIFIED__[approvalId] = true;

      mostrarChat();
      if (abrirPainel) {
        togglePanel(true);
      } else {
        _iaAvisarMensagemRecebida();
      }

      if (idxExistente < 0) {
        _approvalSalvarNoHistorico(payload);
        const div = document.createElement('div');
        div.className = 'jk-ia-msg assistant';
        _approvalMontarCard(div, payload);
        document.getElementById('jk-ia-msgs').appendChild(div);
      }
      document.getElementById('jk-ia-msgs').scrollTop = 99999;
      void _perguntasNotificarWindowsAprovacaoUmaVez(payload);
    }

    function _resolverNotificacaoAprovacao(approvalId, mensagem) {
      const id = String(approvalId || '').trim();
      if (!id) return;
      if (Array.isArray(window.__JK_PENDING_IA_APPROVALS__)) {
        window.__JK_PENDING_IA_APPROVALS__ = window.__JK_PENDING_IA_APPROVALS__.filter((item) => String((item && item.id) || '').trim() !== id);
      }
      const idx = _approvalIndexNoHistorico(id);
      let payloadHistorico = idx >= 0 ? _approvalParseMensagem(mensagensAtuais[idx].text) : null;
      const statusAtual = String((payloadHistorico && payloadHistorico.status) || '').toLowerCase();
      if (['approved', 'sent', 'rejected'].includes(statusAtual)) return;
      if (payloadHistorico) {
        payloadHistorico = {
          ...payloadHistorico,
          status: 'answered_elsewhere',
          status_message: mensagem || 'Respondida fora da aprova\u00e7\u00e3o.',
          resolved_at: new Date().toISOString(),
        };
        _approvalSalvarNoHistorico(payloadHistorico);
      }
      document.querySelectorAll('.jk-ia-approval-card').forEach((card) => {
        if (String((card.dataset && card.dataset.approvalId) || '').trim() !== id) return;
        if (payloadHistorico) {
          _approvalMontarCard(card.closest('.jk-ia-msg') || card.parentElement, payloadHistorico);
          return;
        }
        card.dataset.resolved = '1';
        const actions = card.querySelector('.jk-ia-approval-actions');
        if (actions) actions.remove();
        const edit = card.querySelector('.jk-ia-approval-edit');
        if (edit) edit.disabled = true;
        const antigo = card.querySelector('.jk-ia-approval-status-btn');
        if (antigo) antigo.remove();
        _approvalMontarStatus(card, {
          status: 'answered_elsewhere',
          status_message: mensagem || 'Respondida fora da aprova\u00e7\u00e3o.',
        });
      });
    }

    window.JKIASidebarNotifyApproval = (payload, options) => _adicionarNotificacaoAprovacao(payload, options || {});
    window.JKIASidebarResolveApproval = _resolverNotificacaoAprovacao;
    window.addEventListener('jk-ia-approval', (event) => _adicionarNotificacaoAprovacao(event.detail || {}));
    window.addEventListener('jk-ia-approval-resolved', (event) => {
      const detail = event.detail || {};
      _resolverNotificacaoAprovacao(detail.id || detail.approval_id, detail.mensagem || detail.message);
    });
    setTimeout(() => {
      const pendentes = Array.isArray(window.__JK_PENDING_IA_APPROVALS__) ? window.__JK_PENDING_IA_APPROVALS__ : [];
      window.__JK_PENDING_IA_APPROVALS__ = [];
      pendentes.forEach(_adicionarNotificacaoAprovacao);
    }, 0);

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

    function atualizarMenuLateral() {
      document.getElementById('jk-ia-fab')?.classList.toggle('ativo', panelAberto);
      document.getElementById('jk-msg-fab')?.classList.toggle('ativo', msgPanelAberto);
      document.getElementById('jk-right-sidebar-hotspot')?.classList.remove('menu-aberto');
      _sidebarAtualizarAlertas();
    }

    function setIAPanelAberto(aberto) {
      panelAberto = !!aberto;
      if (panelAberto) iaTemMensagemNaoVista = false;
      document.getElementById('jk-ia-panel')?.classList.toggle('aberto', panelAberto);
      atualizarMenuLateral();
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

    function togglePanel(forcar) {
      const abrir = forcar !== undefined ? !!forcar : !panelAberto;
      if (abrir) setMsgPanelAberto(false);
      setIAPanelAberto(abrir);
    }

    function toggleMsgPanel(forcar) {
      const abrir = forcar !== undefined ? !!forcar : !msgPanelAberto;
      if (abrir) setIAPanelAberto(false);
      setMsgPanelAberto(abrir);
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
      const filtros = [];
      document.querySelectorAll('select, input[type="text"], input[type="date"], input[type="search"], input[type="number"], input[type="month"], input[type="week"]')
        .forEach(el => {
          const rawValue = (el.value || '').trim();
          const value = rawValue || (el.selectedOptions && el.selectedOptions[0]?.textContent?.trim()) || '';
          if (!value) return;
          const label = (el.getAttribute('aria-label') || el.labels?.[0]?.textContent || el.placeholder || el.id || el.name || 'filtro')
            .replace(/\s+/g, ' ')
            .trim();
          filtros.push(label + ': ' + value);
        });

      const cards = [];
      document.querySelectorAll('.card, .kpi-card, .resumo-card').forEach(c => {
        const t = c.querySelector('h3,h4,.card-title,.kpi-label')?.textContent?.trim();
        const v = c.querySelector('.val,.value,.kpi-val,.card-value')?.textContent?.trim();
        if (t && v) cards.push(t + ': ' + v);
      });

      const tableHeaders = Array.from(document.querySelectorAll('table thead th'))
        .map(th => th.textContent.replace(/\s+/g, ' ').trim())
        .filter(Boolean)
        .slice(0, 12);
      const tableRows = [];
      document.querySelectorAll('table tbody tr').forEach(tr => {
        if (tableRows.length >= 15) return;
        const cells = Array.from(tr.querySelectorAll('td')).map(td => td.textContent.replace(/\s+/g, ' ').trim());
        const filled = cells.filter(Boolean);
        if (!filled.length) return;
        if (tableHeaders.length && tableHeaders.length === cells.length) {
          const obj = {};
          tableHeaders.forEach((header, idx) => {
            if (cells[idx]) obj[header] = cells[idx];
          });
          if (Object.keys(obj).length) tableRows.push(obj);
          return;
        }
        tableRows.push(filled.join(' | '));
      });

      const listas = [];
      document.querySelectorAll('ul li, ol li').forEach(li => {
        const text = li.textContent.replace(/\s+/g, ' ').trim();
        if (text) listas.push(text);
      });

      const dateValues = Array.from(document.querySelectorAll('input[type="date"], input[type="month"], input[type="week"]'))
        .map(el => (el.value || '').trim())
        .filter(Boolean);
      const periodo = dateValues.slice(0, 2).join(' a ');

      return {
        title: document.title,
        url: location.pathname,
        periodo,
        filtros: filtros.slice(0, 12),
        cards: cards.slice(0, 8),
        table_headers: tableHeaders,
        table_rows: tableRows,
        table: tableRows.map(row => typeof row === 'string' ? row : Object.entries(row).map(([k, v]) => `${k}: ${v}`).join(' | ')).slice(0, 15),
        listas: listas.slice(0, 12),
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

      const historico = mensagensAtuais.slice(-16).map(m => ({ role: m.role, content: m.text.slice(0, 1200) }));
      const contextoTela = _obterContextoTela();
      contextoTela.historico_conversa = historico;
      contextoTela.modulo_atual = _modulo();

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
          const r = await fetch(url, { method: 'POST', headers: _authHeaders(), body });
          const data = await r.json().catch(() => null);
          if (r.ok) { resposta = data?.resposta || 'Sem resposta da IA.'; break; }
          if (r.status !== 405) break;
        } catch (_) {}
      }
      resposta = resposta || 'Não foi possível consultar a IA agora.';

      const approvalResposta = _approvalParseMensagem(resposta);
      if (approvalResposta) {
        aguardando.className = 'jk-ia-msg assistant';
        _approvalMontarCard(aguardando, approvalResposta);
      } else {
        _definirTextoMsg(aguardando, resposta);
      }
      aguardando.classList.remove('loading');
      mensagensAtuais.push({
        role: 'assistant',
        text: approvalResposta ? _approvalSerializarMensagem(approvalResposta) : resposta,
      });
      await salvarMensagensAtuais();
      if (convsVisible) await renderConvsList();
      document.getElementById('jk-ia-msgs').scrollTop = 99999;
      _iaAvisarMensagemRecebida();
    }

    /* ── Eventos ── */
    document.getElementById('jk-ia-fab').addEventListener('click', () => togglePanel());
    document.getElementById('jk-msg-fab').addEventListener('click', () => toggleMsgPanel());
    document.getElementById('jk-ia-btn-fechar').addEventListener('click', () => togglePanel(false));
    document.getElementById('jk-msg-btn-fechar').addEventListener('click', () => toggleMsgPanel(false));
    document.getElementById('jk-msg-btn-refresh').addEventListener('click', () => _msgCarregarPainel());
    document.getElementById('jk-msg-send').addEventListener('click', () => _msgEnviarMensagem());
    document.getElementById('jk-msg-back').addEventListener('click', () => _msgVoltarLista());
    document.getElementById('jk-msg-emoji-btn').addEventListener('click', () => _msgToggleEmojiPanel());
    document.getElementById('jk-msg-img-btn').addEventListener('click', () => document.getElementById('jk-msg-img-input').click());
    document.getElementById('jk-msg-file-btn').addEventListener('click', () => document.getElementById('jk-msg-file-input').click());
    document.getElementById('jk-msg-audio-btn').addEventListener('click', () => _msgToggleGravacaoAudio());
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
      _carregarModelosRemotos(_modelSelSidebar, 'ia_model_sidebar').catch(() => {});
    }
    // Fechar painel ao clicar fora
    _msgPrepararNotificacoesWindows();
    _msgMontarEmojiPanel();
    _msgRenderAnexosComposer();
    _msgAtualizarSelecao();
    _msgIniciarAtualizacao();
    void _msgBuscarMensagens().catch(() => {});
    setTimeout(() => { void _msgBuscarUsuariosOnline().catch(() => {}); }, 1200);
    _perguntasIniciarMonitorGlobal();

    document.addEventListener('click', e => {
      const alvo = e.target;
      const menu = alvo && alvo.closest ? alvo.closest('#jk-right-sidebar-hotspot') : null;
      const iaPanel = document.getElementById('jk-ia-panel');
      const msgPanel = document.getElementById('jk-msg-panel');
      if (panelAberto && iaPanel && !iaPanel.contains(alvo) && !menu) togglePanel(false);
      if (msgPanelAberto && msgPanel && !msgPanel.contains(alvo) && !menu) toggleMsgPanel(false);
    }, true);

    carregarOuCriarConversa().catch(console.error);
  }

  if (document.readyState === 'loading')
    document.addEventListener('DOMContentLoaded', init);
  else
    init();
})();
