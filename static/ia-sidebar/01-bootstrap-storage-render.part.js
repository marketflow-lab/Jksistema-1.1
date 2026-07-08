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
  function _usuarioLocalEhFull() {
    try {
      const permissoes = JSON.parse(localStorage.getItem('permissions') || '{}');
      return !!(permissoes && permissoes.full === true);
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
      const r = await window.__JK_IA_SIDEBAR_FETCH__('/api/ia/conversas/listar', {
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
      const r = await window.__JK_IA_SIDEBAR_FETCH__(`/api/ia/conversas/${encodeURIComponent(convId)}`, {
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
      const r = await window.__JK_IA_SIDEBAR_FETCH__('/api/ia/conversas/salvar', {
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
      const r = await window.__JK_IA_SIDEBAR_FETCH__(`/api/ia/conversas/${encodeURIComponent(convId)}`, {
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
      const caminho = src.split('?')[0].split('#')[0];
      const ehImagemPorExtensao = /\.(?:png|jpe?g|gif|webp|bmp)$/i.test(caminho);
      const ehEndpointImagem = /^(\/api\/cadastro\/foto-arquivo\/|\/api\/cadastro\/foto\/|\/api\/ia\/imagens\/|\/img\/)/i.test(src);
      const ehImagemMl = /^https?:\/\/http[0-9]*\.mlstatic\.com\//i.test(src);
      if (ehImagemPorExtensao || ehEndpointImagem || ehImagemMl) {
        return escapeHtml(src).replace(/"/g, '&quot;');
      }
      return '';
    }

    function renderImagem(alt, url) {
      const src = normalizarUrlImagem(url);
      if (!src) return '';
      const altSeguro = escapeHtml(alt || 'Imagem do SKU');
      return `<a class="jk-ia-img-link" href="${src}" target="_blank" rel="noopener noreferrer"><img class="jk-ia-img" src="${src}" data-jk-src="${src}" alt="${altSeguro}" loading="lazy"><span class="jk-ia-img-fallback">${altSeguro}</span></a>`;
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
      s = s.replace(/!\[([^\]\n]*)\]\(([^)\s]+)\)/g, (_, alt, url) => renderImagem(alt, url) || renderLink(alt || url, url));
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
      if (match) return renderImagem(match[1], match[2]) || renderLink(escapeHtml(match[1] || match[2]), match[2]);
      match = texto.match(/^\[([^\]\n]+)\]\(([^)\s]+)\)$/);
      if (match) return renderImagem(match[1], match[2]) || renderArquivo(match[1], match[2]) || renderLink(escapeHtml(match[1]), match[2]);
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
        const link = img.closest('a.jk-ia-img-link');
        if (link) link.classList.add('is-broken');
        img.title = 'Nao foi possivel carregar esta imagem.';
      });
    });
  }
