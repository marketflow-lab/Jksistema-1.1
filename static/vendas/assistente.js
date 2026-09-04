function abrirSidebarVendas() {
    if (!graficosContainerVendas) return;
    graficosContainerVendas.classList.remove('observacoes-fechado');
    document.body.classList.add('sidebar-observacoes-aberto');
    document.querySelectorAll('.back-main-btn').forEach(btn => {
        btn.style.display = 'none';
        btn.style.visibility = 'hidden';
        btn.style.pointerEvents = 'none';
    });
}

function fecharSidebarVendas() {
    if (!graficosContainerVendas) return;
    graficosContainerVendas.classList.add('observacoes-fechado');
    document.body.classList.remove('sidebar-observacoes-aberto');
    document.querySelectorAll('.back-main-btn').forEach(btn => {
        btn.style.display = '';
        btn.style.visibility = '';
        btn.style.pointerEvents = '';
    });
}

function alternarAbaSidebarVendas(aba, forcarFechamentoSeAtiva = false) {
    const assistenteAtivo = aba === 'assistente';
    const painelAtivo = assistenteAtivo
        ? sidebarPainelAssistenteVendas?.classList.contains('active')
        : sidebarPainelObservacoesVendas?.classList.contains('active');
    const sidebarAberto = !graficosContainerVendas?.classList.contains('observacoes-fechado');

    if (forcarFechamentoSeAtiva && painelAtivo && sidebarAberto) {
        fecharSidebarVendas();
        return;
    }

    btnSidebarObservacoesVendas?.classList.toggle('active', !assistenteAtivo);
    btnSidebarAssistenteVendas?.classList.toggle('active', assistenteAtivo);
    btnSidebarObservacoesVendas?.setAttribute('aria-selected', String(!assistenteAtivo));
    btnSidebarAssistenteVendas?.setAttribute('aria-selected', String(assistenteAtivo));
    sidebarPainelObservacoesVendas?.classList.toggle('active', !assistenteAtivo);
    sidebarPainelAssistenteVendas?.classList.toggle('active', assistenteAtivo);
    abrirSidebarVendas();
}

function escaparHtmlAssistenteVendas(texto) {
    return String(texto || '')
        .replaceAll('&', '&')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}

function formatarMarkdownBasicoAssistenteVendas(texto) {
    const bruto = String(texto || '').replace(/\r\n?/g, '\n');
    if (!bruto.trim()) return '';

    function safeHref(url) {
        const href = String(url || '').trim();
        if (/^(https?:\/\/|mailto:|tel:|\/|#)/i.test(href)) {
            return escaparHtmlAssistenteVendas(href).replace(/"/g, '&quot;');
        }
        return '';
    }

    function renderLink(label, url) {
        const href = safeHref(url);
        if (!href) return label;
        return `<a href="${href}" target="_blank" rel="noopener noreferrer">${label}</a>`;
    }

    function normalizarUrlImagemAssistente(url) {
        let src = String(url || '').trim();
        if (!src) return '';
        src = src.replace(/^["'`]+|["'`]+$/g, '');
        const helper = globalThis.JKAuthenticatedMedia;
        const normalizada = helper && typeof helper.normalizarUrlFotoCadastro === 'function'
            ? helper.normalizarUrlFotoCadastro(src)
            : '';
        if (normalizada) {
            src = normalizada;
        } else if (
            /^(?:cadastro_fotos\/|\/api\/cadastro\/(?:foto-arquivo\/|foto\/))/i.test(src)
            || /^[^\/\\]+\.(?:png|jpe?g|gif|webp|bmp)$/i.test(src)
        ) {
            return '';
        }
        if (/^(https?:\/\/|\/\/|\/api\/cadastro\/foto-arquivo\/|\/api\/cadastro\/foto\/|\/api\/ia\/imagens\/|\/img\/)/i.test(src)) {
            return escaparHtmlAssistenteVendas(src).replace(/"/g, '&quot;');
        }
        return '';
    }

    function ehUrlFotoCadastroProtegida(url) {
        const helper = globalThis.JKAuthenticatedMedia;
        if (helper && typeof helper.ehUrlProtegidaCadastro === 'function') {
            return helper.ehUrlProtegidaCadastro(url);
        }
        return /^(\/api\/cadastro\/foto-arquivo\/|\/api\/cadastro\/foto\/)/i.test(String(url || '').trim());
    }

    function renderImagemAssistente(alt, url) {
        const src = normalizarUrlImagemAssistente(url);
        if (!src) return '';
        const altSeguro = escaparHtmlAssistenteVendas(alt || 'Imagem do SKU');
        const protegida = ehUrlFotoCadastroProtegida(src);
        const atributoLink = protegida ? `data-jk-auth-link="${src}"` : `href="${src}"`;
        const atributoImagem = protegida ? `data-jk-auth-src="${src}"` : `src="${src}"`;
        return `<a class="sidebar-ai-img-link" ${atributoLink} target="_blank" rel="noopener noreferrer"><img class="sidebar-ai-img" ${atributoImagem} alt="${altSeguro}" loading="lazy"></a>`;
    }

    function obterExtensaoArquivoAssistente(url) {
        const limpo = String(url || '').split('?')[0].split('#')[0];
        const match = limpo.match(/\.([a-z0-9]{2,8})$/i);
        return match ? match[1].toUpperCase() : 'FILE';
    }

    function normalizarUrlArquivoAssistente(url) {
        const href = safeHref(url);
        if (!href) return '';
        if (normalizarUrlImagemAssistente(url)) return '';
        const alvo = String(url || '').split('?')[0].split('#')[0];
        const ehArquivo = /\.(?:pdf|xlsx?|csv|docx?|pptx?|txt|json|xml|md|log|zip|rar|7z)$/i.test(alvo)
            || /\/download(?:\/|$)/i.test(alvo);
        if (!ehArquivo) return '';
        return href;
    }

    function renderArquivoAssistente(label, url) {
        const href = normalizarUrlArquivoAssistente(url);
        if (!href) return '';
        const nomeBruto = String(label || '').trim() || decodeURIComponent(String(url || '').split('/').pop() || 'Arquivo');
        const nome = escaparHtmlAssistenteVendas(nomeBruto);
        const ext = escaparHtmlAssistenteVendas(obterExtensaoArquivoAssistente(url));
        const acao = ext === 'PDF' ? 'Abrir PDF' : 'Abrir arquivo';
        return `<a class="sidebar-ai-file-card" href="${href}" target="_blank" rel="noopener noreferrer"><span class="sidebar-ai-file-icon">${ext}</span><span class="sidebar-ai-file-info"><span class="sidebar-ai-file-name">${nome}</span><span class="sidebar-ai-file-action">${acao}</span></span></a>`;
    }

    function inlineMd(input) {
        let s = escaparHtmlAssistenteVendas(input || '');
        const codigos = [];
        s = s.replace(/`([^`]+)`/g, (_, code) => {
            const imagem = renderImagemAssistente('Imagem do SKU', code);
            const arquivo = renderArquivoAssistente(code, code);
            const id = codigos.push(imagem || arquivo || `<code>${code}</code>`) - 1;
            return `\u0000CODE${id}\u0000`;
        });
        s = s.replace(/!\[([^\]\n]*)\]\(([^)\s]+)\)/g, (_, alt, url) => renderImagemAssistente(alt, url) || '');
        s = s.replace(/\[([^\]\n]+)\]\(([^)\s]+)\)/g, (_, label, url) => renderImagemAssistente(label, url) || renderArquivoAssistente(label, url) || renderLink(label, url));
        s = s.replace(/(^|[\s>])((?:cadastro_fotos\/[^\\\s<]+|lojas\/[^\\\s<]+|[^\/\\\s<]+)\.(?:png|jpe?g|gif|webp|bmp))/gi, (_, prefix, url) => {
            const imagem = renderImagemAssistente('Imagem do SKU', url);
            return imagem ? `${prefix}${imagem}` : `${prefix}${url}`;
        });
        s = s.replace(/(^|[\s>])((?:\/api\/cadastro\/foto-arquivo\/|\/api\/cadastro\/foto\/|\/api\/ia\/imagens\/|\/img\/|https?:\/\/|\/\/)[^\s<]+\.(?:png|jpe?g|gif|webp|bmp)(?:\?[^\s<]+)?)/gi, (_, prefix, url) => {
            const imagem = renderImagemAssistente('Imagem do SKU', url);
            return imagem ? `${prefix}${imagem}` : `${prefix}${url}`;
        });
        s = s.replace(/(^|[\s>])((?:\/api\/|\/static\/|\/img\/|https?:\/\/)[^\s<]+\.(?:pdf|xlsx?|csv|docx?|pptx?|txt|json|xml|md|log|zip|rar|7z)(?:\?[^\s<]+)?)/gi, (_, prefix, url) => {
            const arquivo = renderArquivoAssistente(url.split('/').pop(), url);
            return arquivo ? `${prefix}${arquivo}` : `${prefix}${url}`;
        });
        s = s.replace(/(^|[\s(])((?:https?:\/\/)[^\s<)]+)/g, (_, prefix, url) => `${prefix}${renderLink(url, url)}`);
        s = s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
        s = s.replace(/(^|[^*])\*(?!\s)([^*]+?)\*(?!\*)/g, '$1<em>$2</em>');
        s = s.replace(/\u0000CODE(\d+)\u0000/g, (_, idx) => codigos[Number(idx)] || '');
        return s;
    }

    function referenciasScopedValidasLinhaAssistente(linha) {
        const texto = String(linha || '').trim();
        const padrao = /(?:cadastro_fotos\/lojas|lojas|\/api\/cadastro\/foto-arquivo\/lojas|\/api\/cadastro\/foto\/[^\/\\\s<>()]+\/lojas)\/[^\/\\\s<>()]+\/[^\\\s<>()]+\.(?:png|jpe?g|gif|webp|bmp)(?:\?[^\s<>()]+)?/gi;
        const referencias = [];
        for (const match of texto.matchAll(padrao)) {
            const original = String(match[0] || '').trim();
            const normalizada = normalizarUrlImagemAssistente(original);
            if (
                normalizada
                && ehUrlFotoCadastroProtegida(normalizada)
                && /\/lojas\//i.test(normalizada)
            ) referencias.push({ original, normalizada });
        }
        return referencias;
    }

    function renderLinhaMidiaAssistente(linha) {
        const texto = String(linha || '').trim();
        const scoped = referenciasScopedValidasLinhaAssistente(texto);
        if (scoped.length !== 1) return '';
        let match = texto.match(/^!\[([^\]\n]*)\]\(([^)\s]+)\)$/);
        if (match) return renderImagemAssistente(match[1], match[2]);
        match = texto.match(/^\[([^\]\n]+)\]\(([^)\s]+)\)$/);
        if (match) return renderImagemAssistente(match[1], match[2]) || renderArquivoAssistente(match[1], match[2]);
        if (scoped[0].original === texto) {
            return renderImagemAssistente('Imagem gerada', scoped[0].normalizada);
        }
        return '';
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

        if (!trim) {
            i += 1;
            continue;
        }

        const midiaHtml = renderLinhaMidiaAssistente(trim);
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
            out.push(`<pre><code>${escaparHtmlAssistenteVendas(code.join('\n'))}</code></pre>`);
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
                itens.push(`<li class="sidebar-ai-task-item"><input type="checkbox" disabled${checked ? ' checked' : ''}> <span>${inlineMd(itemTxt)}</span></li>`);
                i += 1;
            }
            out.push(`<ul class="sidebar-ai-task-list">${itens.join('')}</ul>`);
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
            if (renderLinhaMidiaAssistente(atual)) break;
            if (atual.includes('|') && i + 1 < lines.length && isTableSep(lines[i + 1])) break;
            paragrafo.push(atual);
            i += 1;
        }
        out.push(`<p>${paragrafo.map(linha => inlineMd(linha)).join('<br>')}</p>`);
    }

    return out.join('');
}

function definirTextoMensagemAssistenteVendas(el, texto, tipo) {
    if (!el) return;
    if (tipo === 'assistant') {
        el.innerHTML = formatarMarkdownBasicoAssistenteVendas(texto || '');
    } else {
        el.textContent = texto || '';
    }
}

function formatarTamanhoBytesAssistenteVendas(bytes) {
    const n = Number(bytes || 0);
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function limparAnexosAssistenteVendas() {
    anexosAssistenteVendas = [];
    if (sidebarAiAnexosVendas) sidebarAiAnexosVendas.innerHTML = '';
    if (sidebarAiFileInputVendas) sidebarAiFileInputVendas.value = '';
}

function renderizarAnexosAssistenteVendas() {
    if (!sidebarAiAnexosVendas) return;
    sidebarAiAnexosVendas.innerHTML = '';
    anexosAssistenteVendas.forEach((anexo, idx) => {
        const item = document.createElement('div');
        item.className = 'sidebar-ai-anexo-item';

        const nome = document.createElement('span');
        nome.title = anexo.name || 'anexo';
        nome.textContent = `${anexo.name || 'anexo'} (${formatarTamanhoBytesAssistenteVendas(anexo.size)})`;

        const btnRemover = document.createElement('button');
        btnRemover.className = 'sidebar-ai-anexo-remover';
        btnRemover.type = 'button';
        btnRemover.setAttribute('aria-label', `Remover anexo ${anexo.name || ''}`);
        btnRemover.textContent = '×';
        btnRemover.addEventListener('click', () => {
            anexosAssistenteVendas.splice(idx, 1);
            renderizarAnexosAssistenteVendas();
        });

        item.appendChild(nome);
        item.appendChild(btnRemover);
        sidebarAiAnexosVendas.appendChild(item);
    });
}

function lerArquivoComoBase64AssistenteVendas(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => {
            const raw = String(reader.result || '');
            const b64 = raw.includes(',') ? raw.split(',')[1] : '';
            resolve(b64 || '');
        };
        reader.onerror = () => reject(new Error(`Falha ao ler arquivo: ${file?.name || ''}`));
        reader.readAsDataURL(file);
    });
}

async function adicionarArquivosAssistenteVendas(fileList) {
    const arquivos = Array.from(fileList || []);
    if (!arquivos.length) return;

    for (const file of arquivos) {
        if (anexosAssistenteVendas.length >= IA_MAX_ANEXOS) {
            alert(`Limite de ${IA_MAX_ANEXOS} anexos por mensagem.`);
            break;
        }
        if ((file?.size || 0) > IA_MAX_ANEXO_BYTES) {
            alert(`Arquivo muito grande: ${file.name}. Máximo por arquivo: ${formatarTamanhoBytesAssistenteVendas(IA_MAX_ANEXO_BYTES)}.`);
            continue;
        }
        try {
            const dataBase64 = await lerArquivoComoBase64AssistenteVendas(file);
            if (!dataBase64) continue;
            anexosAssistenteVendas.push({
                name: file.name || 'anexo',
                mime_type: file.type || 'application/octet-stream',
                data_base64: dataBase64,
                size: file.size || 0
            });
        } catch (e) {
            console.warn('[IA] Falha ao anexar arquivo:', e);
        }
    }

    renderizarAnexosAssistenteVendas();
    if (sidebarAiFileInputVendas) sidebarAiFileInputVendas.value = '';
}

function adicionarMensagemAssistenteVendas(tipo, texto) {
    if (!sidebarAiChatVendas) return;
    const msg = document.createElement('div');
    msg.className = `sidebar-ai-msg ${tipo}`;
    definirTextoMensagemAssistenteVendas(msg, texto, tipo);
    if (tipo === 'assistant' && /consultando/i.test(texto)) {
        msg.classList.add('loading');
    }
    sidebarAiChatVendas.appendChild(msg);
    sidebarAiChatVendas.scrollTop = sidebarAiChatVendas.scrollHeight;
    if (tipo !== 'assistant' || !/pensando/i.test(texto)) {
        iaMensagensAtuais.push({ role: tipo, text: texto });
        _iaSalvarMsgsAtuais();
    }
    return msg;
}

function obterChaveMesAssistenteVendas(valor) {
    const texto = String(valor || '').trim();
    let match = texto.match(/^(\d{4})-(\d{2})/);
    if (match) return `${match[1]}-${match[2]}`;
    match = texto.match(/^(\d{2})\/(\d{2})\/(\d{4})/);
    if (match) return `${match[3]}-${match[2]}`;
    return '';
}

function arredondarNumeroAssistenteVendas(valor) {
    return Number(Number(valor || 0).toFixed(2));
}

function topItensAssistenteVendas(mapa, campoQtd, campoValor, limite = 5) {
    return Object.values(mapa || {})
        .sort((a, b) => {
            const diffQtd = Number(b[campoQtd] || 0) - Number(a[campoQtd] || 0);
            if (diffQtd !== 0) return diffQtd;
            const diffValor = Number(b[campoValor] || 0) - Number(a[campoValor] || 0);
            if (diffValor !== 0) return diffValor;
            return String(a.sku || '').localeCompare(String(b.sku || ''), 'pt-BR');
        })
        .slice(0, limite)
        .map(item => ({
            ...item,
            [campoQtd]: arredondarNumeroAssistenteVendas(item[campoQtd]),
            [campoValor]: arredondarNumeroAssistenteVendas(item[campoValor])
        }));
}

function obterDevolucoesAtivasAssistenteVendas() {
    const unidadeSel = unidadeNegocioSelect?.value || '__todos';
    const devolucoesPorLoja = (lojaSelecionada && lojaSelecionada !== '__todas')
        ? (devolucaoItens || []).filter(dev => mesmaLoja(dev.loja_conta, lojaSelecionada))
        : (devolucaoItens || []);

    if (!unidadeSel || unidadeSel === '__todos') return devolucoesPorLoja;

    const unidadeAlvo = normalizarChaveFiltro(unidadeSel, true);
    return devolucoesPorLoja.filter(dev => {
        const unidadeDev = normalizarChaveFiltro(
            dev?.unidade_negocio_virtual || dev?.unidade_negocio || '',
            true
        );
        return unidadeDev === unidadeAlvo;
    });
}

function montarAnaliseMensalAssistenteVendas() {
    const meses = {};
    const garantirMes = (mes) => {
        if (!meses[mes]) {
            meses[mes] = {
                mes_ano: mes,
                pedidos_total: 0,
                quantidade_vendida: 0,
                valor_vendido: 0,
                quantidade_devolvida: 0,
                valor_devolvido: 0,
                skus_vendidos: {},
                skus_devolvidos: {}
            };
        }
        return meses[mes];
    };

    (Array.isArray(dadosFiltrados) ? dadosFiltrados : []).forEach(r => {
        if (typeof vendaEhEbazarSomatorio === 'function' && vendaEhEbazarSomatorio(r)) return;
        const sku = String(r?.sku || '').trim();
        const produto = String(r?.produto || '').trim();
        if (sku.toLowerCase() === 'sku' && produto.toLowerCase() === 'produto') return;
        const mes = obterChaveMesAssistenteVendas(r?.data);
        if (!mes) return;
        const qtd = Number(r?.itens ?? r?.quantidade ?? 0);
        const valor = Number(r?.valor || 0);
        const atual = garantirMes(mes);
        atual.pedidos_total += Number(r?.__resumo ? (r?.pedidos || 0) : 1);
        atual.quantidade_vendida += qtd;
        atual.valor_vendido += valor;

        const chaveSku = sku || 'N/D';
        if (!atual.skus_vendidos[chaveSku]) {
            atual.skus_vendidos[chaveSku] = {
                sku: chaveSku,
                produto: produto || '-',
                quantidade_vendida: 0,
                valor_vendido: 0
            };
        }
        atual.skus_vendidos[chaveSku].quantidade_vendida += qtd;
        atual.skus_vendidos[chaveSku].valor_vendido += valor;
    });

    obterDevolucoesAtivasAssistenteVendas().forEach(dev => {
        const mes = obterChaveMesAssistenteVendas(dev?.data_emissao || dev?.data);
        if (!mes) return;
        const qtd = Number(dev?.quantidade || 0);
        const valor = Number(dev?.valor_total || 0);
        const sku = String(dev?.sku || '').trim() || 'N/D';
        const produto = String(dev?.descricao || dev?.produto || '').trim() || '-';
        const atual = garantirMes(mes);
        atual.quantidade_devolvida += qtd;
        atual.valor_devolvido += valor;

        if (!atual.skus_devolvidos[sku]) {
            atual.skus_devolvidos[sku] = {
                sku,
                produto,
                quantidade_devolvida: 0,
                valor_devolvido: 0
            };
        }
        atual.skus_devolvidos[sku].quantidade_devolvida += qtd;
        atual.skus_devolvidos[sku].valor_devolvido += valor;
    });

    const listaMeses = Object.values(meses)
        .sort((a, b) => String(a.mes_ano).localeCompare(String(b.mes_ano)))
        .slice(-24)
        .map(mes => ({
            mes_ano: mes.mes_ano,
            pedidos_total: mes.pedidos_total,
            quantidade_vendida: arredondarNumeroAssistenteVendas(mes.quantidade_vendida),
            valor_vendido: arredondarNumeroAssistenteVendas(mes.valor_vendido),
            quantidade_devolvida: arredondarNumeroAssistenteVendas(mes.quantidade_devolvida),
            valor_devolvido: arredondarNumeroAssistenteVendas(mes.valor_devolvido),
            top_skus_vendidos: topItensAssistenteVendas(mes.skus_vendidos, 'quantidade_vendida', 'valor_vendido', 5),
            top_skus_devolvidos: topItensAssistenteVendas(mes.skus_devolvidos, 'quantidade_devolvida', 'valor_devolvido', 5)
        }));

    if (!listaMeses.length) return null;

    return {
        mes_campeao_valor: [...listaMeses].sort((a, b) => Number(b.valor_vendido || 0) - Number(a.valor_vendido || 0))[0],
        mes_campeao_quantidade: [...listaMeses].sort((a, b) => Number(b.quantidade_vendida || 0) - Number(a.quantidade_vendida || 0))[0],
        top_meses_valor: [...listaMeses].sort((a, b) => Number(b.valor_vendido || 0) - Number(a.valor_vendido || 0)).slice(0, 5),
        top_meses_quantidade: [...listaMeses].sort((a, b) => Number(b.quantidade_vendida || 0) - Number(a.quantidade_vendida || 0)).slice(0, 5),
        meses: listaMeses
    };
}

function obterResumoTelaVendas() {
    const periodo = periodoTexto?.textContent?.trim() || 'Periodo nao selecionado';
    const loja = lojaSelecionada === '__todas' ? 'Todas as lojas' : (lojaSelecionada || unidadeNegocioSelect?.value || 'Loja nao selecionada');
    const totalLinhas = Array.isArray(dadosFiltrados) ? dadosFiltrados.length : 0;
    const cards = Array.from(resumo?.querySelectorAll('.card') || []).map(card => {
        const titulo = card.querySelector('h3')?.textContent?.trim();
        const valor = card.querySelector('.val')?.textContent?.trim();
        return titulo && valor ? `${titulo}: ${valor}` : '';
    }).filter(Boolean).slice(0, 5);

    // Agrega SKUs diretamente dos dados filtrados para a IA enxergar ranking completo
    const skuMap = {};
    (Array.isArray(dadosFiltrados) ? dadosFiltrados : []).forEach(r => {
        const sku = r.sku || 'N/D';
        if (!skuMap[sku]) skuMap[sku] = { sku, produto: r.produto || '', itens: 0, valor: 0 };
        skuMap[sku].itens += Number(r.itens ?? r.quantidade ?? 0);
        skuMap[sku].valor += Number(r.valor || 0);
    });
    const skusOrdenados = Object.values(skuMap)
        .sort((a, b) => (b.itens - a.itens) || (b.valor - a.valor));
    const skusRanking = skusOrdenados.slice(0, 30)
        .map(s => ({ sku: s.sku, produto: s.produto, qtd_vendida: s.itens, valor_total: parseFloat(s.valor.toFixed(2)) }));

    const ociosos = Array.from(graficoOciososVendas?.querySelectorAll('li') || []).map(li => li.textContent.trim()).filter(Boolean).slice(0, 5);

    return {
        title: 'Vendas',
        url: location.pathname,
        periodo,
        loja,
        data_inicio: getDataIniISO() || '',
        data_fim: getDataFimISO() || '',
        totalLinhas,
        cards,
        skus_ranking: skusRanking,
        analise_mensal: montarAnaliseMensalAssistenteVendas(),
        table: ociosos
    };
}

function obterHistoricoAssistenteVendas() {
    return Array.from(sidebarAiChatVendas?.querySelectorAll('.sidebar-ai-msg') || []).slice(-8).map((msg) => ({
        role: msg.classList.contains('user') ? 'user' : 'assistant',
        content: msg.textContent || ''
    })).filter((msg) => msg.content.trim());
}

async function chamarAssistenteBackendVendas(pergunta, anexos = []) {
    const selectModelo = document.getElementById('iaModelSelectVendas');
    const _modelSel = selectModelo && selectModelo.dataset.podeEscolherModelo === 'true' ? selectModelo.value : '';
    const payload = JSON.stringify({
        message: pergunta,
        page: 'Vendas',
        model: _modelSel,
        context: obterResumoTelaVendas(),
        history: obterHistoricoAssistenteVendas(),
        attachments: anexos
    });
    const urls = ['/api/ia/chat'];
    if (location.hostname === '127.0.0.1' || location.hostname === 'localhost') {
        urls.push('http://127.0.0.1:8012/api/ia/chat');
    }

    let ultimoErro = null;
    for (const url of urls) {
        try {
            const resp = await fetch(url, {
                method: 'POST',
                headers: {
                    ...obterAuthHeaders(),
                    'Content-Type': 'application/json'
                },
                body: payload
            });
            let data = null;
            try {
                data = await resp.json();
            } catch (_e) {
                data = null;
            }
            if (resp.ok) {
                return data?.resposta || 'A IA não retornou resposta.';
            }
            ultimoErro = new Error(resp.status === 405
                ? 'O servidor local ainda está com uma versão antiga. Reinicie o programa para ativar a IA.'
                : (data?.detail || resp.statusText || 'Falha ao consultar o Black Jhon.'));
            if (resp.status !== 405) break;
        } catch (error) {
            if (!ultimoErro) ultimoErro = error;
        }
    }
    throw ultimoErro || new Error('Falha ao consultar o Black Jhon.');
}

async function enviarPerguntaAssistenteVendas(perguntaManual) {
    const pergunta = (perguntaManual || sidebarAiInputVendas?.value || '').trim();
    const anexos = anexosAssistenteVendas.map(({ name, mime_type, data_base64 }) => ({ name, mime_type, data_base64 }));
    if (!pergunta && !anexos.length) return;

    const perguntaFinal = pergunta || 'Analise os anexos enviados.';
    const resumoAnexos = anexos.length
        ? `\n\nAnexos: ${anexos.map(a => a.name).join(', ')}`
        : '';
    adicionarMensagemAssistenteVendas('user', `${perguntaFinal}${resumoAnexos}`);
    if (sidebarAiInputVendas) sidebarAiInputVendas.value = '';
    limparAnexosAssistenteVendas();
    const aguardando = adicionarMensagemAssistenteVendas('assistant', 'Pensando...');
    try {
        const resposta = await chamarAssistenteBackendVendas(perguntaFinal, anexos);
        definirTextoMensagemAssistenteVendas(aguardando, resposta, 'assistant');
        aguardando.classList.remove('loading');
        // "Pensando..." não entra no array; sempre adiciona a resposta real.
        iaMensagensAtuais.push({ role: 'assistant', text: resposta });
        _iaSalvarMsgsAtuais();
    } catch (error) {
        definirTextoMensagemAssistenteVendas(aguardando, error?.message === 'Method Not Allowed'
            ? 'O servidor local ainda está com uma versão antiga. Reinicie o programa para ativar a IA.'
            : (error?.message || 'Não foi possível consultar a IA.'), 'assistant');
        aguardando.classList.remove('loading');
    }
}

function substituirGrupoModelosIA(selectEl, label, modelos) {
    if (!selectEl || !Array.isArray(modelos) || !modelos.length) return;
    let grupo = Array.from(selectEl.querySelectorAll('optgroup')).find((el) => el.label === label);
    if (!grupo) {
        grupo = document.createElement('optgroup');
        grupo.label = label;
        selectEl.appendChild(grupo);
    }
    grupo.innerHTML = '';
    modelos.forEach((item) => {
        const option = document.createElement('option');
        option.value = item.name;
        option.textContent = item.display_name || item.name;
        if (item.description) option.title = item.description;
        grupo.appendChild(option);
    });
}

async function carregarModelosAssistenteVendas() {
    const selectEl = document.getElementById('iaModelSelectVendas');
    if (!selectEl) return;
    const valorSalvo = usuarioLocalEhAdminChatVendas() ? (localStorage.getItem('ia_model_vendas') || selectEl.value) : '';
    const urls = ['/api/ia/modelos'];
    if (location.hostname === '127.0.0.1' || location.hostname === 'localhost') {
        urls.push('http://127.0.0.1:8012/api/ia/modelos');
    }
    for (const url of urls) {
        try {
            const resp = await fetch(url, {
                method: 'GET',
                headers: obterAuthHeaders()
            });
            if (!resp.ok) {
                if (resp.status !== 405) break;
                continue;
            }
            const data = await resp.json().catch(() => null);
            if (!data || !data.success) return;
            const podeEscolher = data.pode_escolher_modelo_chat === true;
            aplicarPermissaoModeloChatVendas(selectEl, podeEscolher);
            substituirGrupoModelosIA(selectEl, 'OpenAI', data.openai || []);
            substituirGrupoModelosIA(selectEl, 'DeepSeek', data.deepseek || []);
            substituirGrupoModelosIA(selectEl, 'Gemini API', data.gemini || []);
            substituirGrupoModelosIA(selectEl, 'Vertex AI (Google Cloud)', data.vertex || []);
            const valores = Array.from(selectEl.options).map(opt => opt.value);
            const modeloSistema = data?.defaults?.sistema || '';
            const valorPreferido = podeEscolher && valores.includes(valorSalvo)
                ? valorSalvo
                : (valores.includes(modeloSistema) ? modeloSistema : selectEl.value);
            if (valorPreferido) selectEl.value = valorPreferido;
            return;
        } catch (_error) {}
    }
}

btnSidebarObservacoesVendas?.addEventListener('click', () => alternarAbaSidebarVendas('observacoes', true));
document.getElementById('btnNovaConvVendas')?.addEventListener('click', iaNovaConversa);
document.getElementById('btnHistoricoConvVendas')?.addEventListener('click', iaMostrarHistorico);
// Seletor de modelo — persiste preferência no localStorage
const _iaModelSel = document.getElementById('iaModelSelectVendas');
if (_iaModelSel) {
    aplicarPermissaoModeloChatVendas(_iaModelSel, usuarioLocalEhAdminChatVendas());
    if (usuarioLocalEhAdminChatVendas()) {
        const _savedModel = localStorage.getItem('ia_model_vendas');
        if (_savedModel) _iaModelSel.value = _savedModel;
    }
    _iaModelSel.addEventListener('change', () => {
        if (_iaModelSel.dataset.podeEscolherModelo === 'true') {
            localStorage.setItem('ia_model_vendas', _iaModelSel.value);
        }
    });
    carregarModelosAssistenteVendas().catch(() => {});
}
btnSidebarAssistenteVendas?.addEventListener('click', () => {
    const fab = document.getElementById('jk-ia-fab');
    if (fab) {
        fab.click();
        return;
    }
    const lightFab = document.getElementById('jk-ia-light-fab');
    if (lightFab) {
        lightFab.click();
        return;
    }
    if (typeof window.__JK_IA_SIDEBAR_LOAD_FULL__ === 'function') {
        void window.__JK_IA_SIDEBAR_LOAD_FULL__({ openAfterLoad: true });
    }
});
btnSidebarAiEnviarVendas?.addEventListener('click', () => enviarPerguntaAssistenteVendas());
btnSidebarAiAnexoVendas?.addEventListener('click', () => sidebarAiFileInputVendas?.click());
sidebarAiFileInputVendas?.addEventListener('change', async (event) => {
    await adicionarArquivosAssistenteVendas(event?.target?.files || []);
});
sidebarAiInputVendas?.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        enviarPerguntaAssistenteVendas();
    }
});
sidebarAiInputVendas?.addEventListener('paste', (event) => {
    const items = Array.from(event.clipboardData?.items || []);
    const imgItems = items.filter(it => it.kind === 'file' && it.type.startsWith('image/'));
    if (imgItems.length === 0) return;
    event.preventDefault();
    const files = imgItems.map(it => it.getAsFile()).filter(Boolean);
    if (files.length) adicionarArquivosAssistenteVendas(files);
});
if (graficosContainerVendas && navActionsVendas) {
    // Inicializa estado dos botões no carregamento
    graficosContainerVendas.classList.add('observacoes-fechado');
    document.body.classList.remove('sidebar-observacoes-aberto');
    
    // Oculta TODOS os botões .back-main-btn diretamente
    document.querySelectorAll('.back-main-btn').forEach(btn => {
        btn.style.display = '';
        btn.style.visibility = '';
        btn.style.pointerEvents = '';
    });
}
