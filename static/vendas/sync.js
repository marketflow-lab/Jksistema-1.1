function obterClientId() {
    const user = JSON.parse(localStorage.getItem('user_data') || 'null');
    return user && user.client_id ? user.client_id : null;
}

async function fetchComTimeout(url, options = {}, timeoutMs = REQUEST_TIMEOUT_MS) {
    const controller = new AbortController();
    const externalSignal = options.signal;
    const abortFromExternal = () => controller.abort(externalSignal?.reason);
    if (externalSignal) {
        if (externalSignal.aborted) abortFromExternal();
        else externalSignal.addEventListener('abort', abortFromExternal, { once: true });
    }
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
    try {
        return await fetch(url, { ...options, signal: controller.signal });
    } finally {
        clearTimeout(timeoutId);
        if (externalSignal) externalSignal.removeEventListener('abort', abortFromExternal);
    }
}

function aguardarProximoFrame() {
    return new Promise(resolve => requestAnimationFrame(() => resolve()));
}

function agendarSegundoPlano(fn, delayMs = 0) {
    if (typeof requestIdleCallback === 'function') {
        requestIdleCallback(() => setTimeout(fn, delayMs), { timeout: 900 });
        return;
    }
    setTimeout(fn, delayMs);
}

function formatCurrency(v) {
    return Number(v || 0).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
}

function vendaEhEbazarSomatorio(venda) {
    const devolucao = Number(venda?.devolucao || 0) === 1;
    if (devolucao) return false;
    const comprador = String(venda?.comprador || '').toUpperCase();
    const canal = String(venda?.canal || '').toUpperCase();
    return comprador.includes('EBAZAR') || canal.includes('EBAZAR');
}

function abrirDevolucoesVendas() {
    const qs = new URLSearchParams();
    const inicio = getDataIniISO();
    const fim = getDataFimISO();
    if (inicio) qs.set('data_inicio', inicio);
    if (fim) qs.set('data_fim', fim);
    qs.set('loja', lojaSelecionada || '__todas');
    const unidade = unidadeNegocioSelect.value;
    if (unidade && unidade !== '__todos') qs.set('unidade_negocio', unidade);
    window.location.href = `/devolucoes.html?${qs.toString()}`;
}

function renderResumoTotaisVendas(totais, exibirDevolucoes = true) {
    const avisoComposicao = document.getElementById('composicaoAviso');
    if (avisoComposicao) {
        const composicao = typeof vendasResumoMeta !== 'undefined' ? vendasResumoMeta?.composicao : null;
        const incompleta = Boolean(composicao?.incomplete);
        const legadoSemConfirmacao = Array.isArray(composicao?.warnings) && composicao.warnings.some(aviso => ['legacy_composition_unverified', 'legacy_component_identity_unverified', 'legacy_catalog_unverified'].includes(aviso?.code));
        avisoComposicao.hidden = !incompleta;
        avisoComposicao.textContent = incompleta
            ? `${legadoSemConfirmacao ? 'Há dados do cadastro legado que precisam de confirmação por loja.' : 'Há composições indisponíveis ou inválidas no cadastro.'} As quantidades desses SKUs foram mantidas como vendidas; atualize o Cadastro da loja para conferir as composições.`
            : '';
    }
    const totalPedidos = Number(totais?.pedidos || 0);
    const totalItens = Number(totais?.itens || 0);
    const totalValor = Number(totais?.valor || 0);
    const totalDevolucoes = Number(totais?.devolucoes || 0);
    const itensDevolucao = Number(totais?.itensDevolucao || 0);
    const valorDevolucao = Number(totais?.valorDevolucao || 0);
    const itensLiquidos = Number(totais?.itensLiquidos ?? (totalItens - itensDevolucao));
    const valorLiquido = Number(totais?.valorLiquido ?? (totalValor - valorDevolucao));

    resumo.innerHTML = '';
    const cards = [
        { label: 'Pedidos', value: totalPedidos },
        { label: 'Itens Vendidos', value: totalItens.toLocaleString('pt-BR', { maximumFractionDigits: 8 }) },
        { label: 'Valor Bruto', value: formatCurrency(totalValor) }
    ];
    if (exibirDevolucoes) {
        cards.push(
            { label: 'Devolu\u00e7\u00f5es', value: totalDevolucoes, isDevolucao: true },
            { label: 'Itens Devolvidos', value: itensDevolucao.toLocaleString('pt-BR', { maximumFractionDigits: 8 }), isNegative: true },
            { label: 'Valor Devolvido', value: formatCurrency(valorDevolucao), isNegative: true },
            { label: 'Itens L\u00edquidos', value: itensLiquidos.toLocaleString('pt-BR', { maximumFractionDigits: 8 }), isLiquido: true },
            { label: 'Valor L\u00edquido', value: formatCurrency(valorLiquido), isLiquido: true }
        );
    }
    const cardPalette = {
        'Pedidos':          { border: '#63bbff', bg: 'linear-gradient(160deg,rgba(14,36,66,0.97),rgba(8,22,44,0.99))', h3: '#7ecfff', val: '#ceeeff', glow: 'rgba(99,187,255,0.18)' },
        'Itens Vendidos':   { border: '#4de8d4', bg: 'linear-gradient(160deg,rgba(10,48,48,0.97),rgba(6,30,34,0.99))', h3: '#62e8d8', val: '#b8fff8', glow: 'rgba(77,232,212,0.18)' },
        'Valor Bruto':      { border: '#b67bff', bg: 'linear-gradient(160deg,rgba(32,14,62,0.97),rgba(20,8,42,0.99))', h3: '#c894ff', val: '#ecdeff', glow: 'rgba(182,123,255,0.2)' },
        'Devolu\u00e7\u00f5es':       { border: '#ffb347', bg: 'linear-gradient(160deg,rgba(52,32,6,0.97),rgba(34,20,4,0.99))', h3: '#ffc368', val: '#ffe8b8', glow: 'rgba(255,179,71,0.18)' },
        'Itens Devolvidos': { border: '#ff6b6b', bg: 'linear-gradient(160deg,rgba(54,10,14,0.97),rgba(36,6,8,0.99))', h3: '#ff8a8a', val: '#ffd0d0', glow: 'rgba(255,107,107,0.18)' },
        'Valor Devolvido':  { border: '#ff4e6a', bg: 'linear-gradient(160deg,rgba(60,8,18,0.97),rgba(40,4,12,0.99))', h3: '#ff7090', val: '#ffbdc9', glow: 'rgba(255,78,106,0.18)' },
        'Itens L\u00edquidos':   { border: '#6fe6b0', bg: 'linear-gradient(160deg,rgba(10,48,28,0.97),rgba(6,30,18,0.99))', h3: '#88f0c4', val: '#c8fff0', glow: 'rgba(111,230,176,0.18)' },
        'Valor L\u00edquido':    { border: '#30e8a0', bg: 'linear-gradient(160deg,rgba(6,52,30,0.97),rgba(4,34,20,0.99))', h3: '#4ef2b8', val: '#b0ffdf', glow: 'rgba(48,232,160,0.22)' },
    };
    cards.forEach(c => {
        const div = document.createElement('div');
        div.className = 'card';
        const p = cardPalette[c.label] || { border: '#63bbff', bg: '', h3: 'var(--muted)', val: 'var(--text)', glow: 'rgba(99,187,255,0.14)' };
        div.style.cssText = `background:${p.bg};border-left-color:${p.border};border-color:rgba(104,168,255,0.38);border-left-color:${p.border};box-shadow:0 16px 32px rgba(0,0,0,0.38),0 0 18px ${p.glow},inset 0 1px 0 rgba(255,255,255,0.04);`;
        div.innerHTML = `<h3 style="color:${p.h3}">${escaparHtmlVendas(c.label)}</h3><div class="val" style="color:${p.val};text-shadow:0 0 14px ${p.glow}">${escaparHtmlVendas(c.value)}</div>`;
        if (c.isDevolucao) {
            div.classList.add('card-devolucoes');
            const valueEl = div.querySelector('.val');
            const valueRow = document.createElement('div');
            valueRow.className = 'card-value-action';
            div.insertBefore(valueRow, valueEl);
            valueRow.appendChild(valueEl);

            const btn = document.createElement('button');
            btn.className = 'back-btn card-action-btn';
            btn.style.background = '#9b59b6';
            btn.style.marginTop = '0';
            btn.textContent = 'Ver';
            btn.onclick = abrirDevolucoesVendas;
            valueRow.appendChild(btn);
        }
        resumo.appendChild(div);
    });
}

function renderResumo(lista, devolucoesLista = [], exibirDevolucoes = true) {
    if (dadosModoResumo && resumoTotaisVendas) {
        renderResumoTotaisVendas(resumoTotaisVendas, exibirDevolucoes);
        return;
    }
    const listaSomatorio = (lista || []).filter(item => !vendaEhEbazarSomatorio(item));
    const totalPedidos = listaSomatorio.length;
    const totalItens = listaSomatorio.reduce((acc, cur) => acc + Number(cur.quantidade || 0), 0);
    const totalValor = listaSomatorio.reduce((acc, cur) => acc + Number(cur.valor || 0), 0);
    
    // Calcular totais de devoluções
    const totalDevolucoes = devolucoesLista.length;
    const itensDevolucao = devolucoesLista.reduce((acc, cur) => acc + Number(cur.quantidade || 0), 0);
    const valorDevolucao = devolucoesLista.reduce((acc, cur) => acc + Number(cur.valor_total || 0), 0);
    
    // Totais líquidos (vendas - devoluções)
    const itensLiquidos = totalItens - itensDevolucao;
    const valorLiquido = totalValor - valorDevolucao;
    
    resumo.innerHTML = '';
    const cards = [
        { label: 'Pedidos', value: totalPedidos },
        { label: 'Itens Vendidos', value: totalItens.toLocaleString('pt-BR', { maximumFractionDigits: 8 }) },
        { label: 'Valor Bruto', value: formatCurrency(totalValor) }
    ];

    if (exibirDevolucoes) {
        cards.push(
            { label: 'Devolu\u00e7\u00f5es', value: totalDevolucoes, isDevolucao: true },
            { label: 'Itens Devolvidos', value: itensDevolucao.toLocaleString('pt-BR', { maximumFractionDigits: 8 }), isNegative: true },
            { label: 'Valor Devolvido', value: formatCurrency(valorDevolucao), isNegative: true },
            { label: 'Itens L\u00edquidos', value: itensLiquidos.toLocaleString('pt-BR', { maximumFractionDigits: 8 }), isLiquido: true },
            { label: 'Valor L\u00edquido', value: formatCurrency(valorLiquido), isLiquido: true }
        );
    }
    // Paleta moderna por tipo de card
    const cardPalette = {
        'Pedidos':          { border: '#63bbff', bg: 'linear-gradient(160deg,rgba(14,36,66,0.97),rgba(8,22,44,0.99))', h3: '#7ecfff', val: '#ceeeff', glow: 'rgba(99,187,255,0.18)' },
        'Itens Vendidos':   { border: '#4de8d4', bg: 'linear-gradient(160deg,rgba(10,48,48,0.97),rgba(6,30,34,0.99))', h3: '#62e8d8', val: '#b8fff8', glow: 'rgba(77,232,212,0.18)' },
        'Valor Bruto':      { border: '#b67bff', bg: 'linear-gradient(160deg,rgba(32,14,62,0.97),rgba(20,8,42,0.99))', h3: '#c894ff', val: '#ecdeff', glow: 'rgba(182,123,255,0.2)' },
        'Devolu\u00e7\u00f5es':       { border: '#ffb347', bg: 'linear-gradient(160deg,rgba(52,32,6,0.97),rgba(34,20,4,0.99))', h3: '#ffc368', val: '#ffe8b8', glow: 'rgba(255,179,71,0.18)' },
        'Itens Devolvidos': { border: '#ff6b6b', bg: 'linear-gradient(160deg,rgba(54,10,14,0.97),rgba(36,6,8,0.99))', h3: '#ff8a8a', val: '#ffd0d0', glow: 'rgba(255,107,107,0.18)' },
        'Valor Devolvido':  { border: '#ff4e6a', bg: 'linear-gradient(160deg,rgba(60,8,18,0.97),rgba(40,4,12,0.99))', h3: '#ff7090', val: '#ffbdc9', glow: 'rgba(255,78,106,0.18)' },
        'Itens L\u00edquidos':   { border: '#6fe6b0', bg: 'linear-gradient(160deg,rgba(10,48,28,0.97),rgba(6,30,18,0.99))', h3: '#88f0c4', val: '#c8fff0', glow: 'rgba(111,230,176,0.18)' },
        'Valor L\u00edquido':    { border: '#30e8a0', bg: 'linear-gradient(160deg,rgba(6,52,30,0.97),rgba(4,34,20,0.99))', h3: '#4ef2b8', val: '#b0ffdf', glow: 'rgba(48,232,160,0.22)' },
    };
    cards.forEach(c => {
        const div = document.createElement('div');
        div.className = 'card';
        const p = cardPalette[c.label] || { border: '#63bbff', bg: '', h3: 'var(--muted)', val: 'var(--text)', glow: 'rgba(99,187,255,0.14)' };
        div.style.cssText = `background:${p.bg};border-left-color:${p.border};border-color:rgba(104,168,255,0.38);border-left-color:${p.border};box-shadow:0 16px 32px rgba(0,0,0,0.38),0 0 18px ${p.glow},inset 0 1px 0 rgba(255,255,255,0.04);`;
        div.innerHTML = `<h3 style="color:${p.h3}">${escaparHtmlVendas(c.label)}</h3><div class="val" style="color:${p.val};text-shadow:0 0 14px ${p.glow}">${escaparHtmlVendas(c.value)}</div>`;
        if (c.isDevolucao) {
            div.classList.add('card-devolucoes');
            const valueEl = div.querySelector('.val');
            const valueRow = document.createElement('div');
            valueRow.className = 'card-value-action';
            div.insertBefore(valueRow, valueEl);
            valueRow.appendChild(valueEl);

            const btn = document.createElement('button');
            btn.className = 'back-btn card-action-btn';
            btn.style.background = '#9b59b6';
            btn.style.marginTop = '0';
            btn.textContent = 'Ver';
            btn.onclick = abrirDevolucoesVendas;
            valueRow.appendChild(btn);
        }
        resumo.appendChild(div);
    });
}

function obterValorExibicaoSku(item) {
    const valorVendido = Number(item?.valor || 0);
    if (valorVendido > 0) return valorVendido;
    const valorDevolucao = Number(item?.valorDevolucao || 0);
    return valorDevolucao > 0 ? valorDevolucao : 0;
}

function renderTabelaAgrupada(lista) {
    tabelaRenderToken += 1;
    const tokenAtual = tabelaRenderToken;
    tHead.innerHTML = '';
    tBody.innerHTML = '';
    if (!lista.length) {
        if (!syncEmAndamento) {
            statusEl.className = 'status-bar empty';
            statusEl.textContent = 'Nenhum registro encontrado.';
        }
        return;
    }
    if (!syncEmAndamento) {
        statusEl.textContent = '';
    }
    const cols = ['sku','produto','pedidos','itens','devolucoes','valor'];
    const trHead = document.createElement('tr');
    cols.forEach(col => {
        const th = document.createElement('th');
        th.textContent = String(col || '').toUpperCase();
        trHead.appendChild(th);
    });
    tHead.appendChild(trHead);

    let index = 0;
    const CHUNK = 180;

    const renderChunk = () => {
        if (tokenAtual !== tabelaRenderToken) return;
        const frag = document.createDocumentFragment();
        const fim = Math.min(index + CHUNK, lista.length);

        for (; index < fim; index++) {
            const item = lista[index];
            const tr = document.createElement('tr');
            tr.style.cursor = 'pointer';
            tr.setAttribute('data-sku', item.sku || '');
            cols.forEach(col => {
                const td = document.createElement('td');
                let val = item[col];
                if (col === 'valor') val = formatCurrency(obterValorExibicaoSku(item));
                if (col === 'itens') val = Number(val || 0).toLocaleString('pt-BR', { maximumFractionDigits: 8 });
                td.textContent = val;
                if (col === 'itens' && Number(item.itensComposicao || 0) > 0) {
                    td.title = `${Number(item.itensDiretos || 0).toLocaleString('pt-BR', { maximumFractionDigits: 8 })} diretos + ${Number(item.itensComposicao).toLocaleString('pt-BR', { maximumFractionDigits: 8 })} por composição. Pedidos e valores permanecem no SKU vendido originalmente.`;
                }
                if (col === 'devolucoes' && Number(item.itensDevolucaoComposicao || 0) > 0) {
                    td.title = `${Number(item.itensDevolucao || 0).toLocaleString('pt-BR', { maximumFractionDigits: 8 })} itens devolvidos, sendo ${Number(item.itensDevolucaoComposicao).toLocaleString('pt-BR', { maximumFractionDigits: 8 })} por composição. A ocorrência e o valor permanecem no SKU original.`;
                }

                if (col === 'devolucoes' && Number(item.devolucoes || 0) > 0) {
                    td.style.color = '#ff6b6b';
                    td.style.fontWeight = '700';
                }

                if (col === 'valor') {
                    const valorVendido = Number(item.valor || 0);
                    const valorDevolucao = Number(item.valorDevolucao || 0);
                    if (valorVendido > 0) {
                        td.style.color = '#6fe6b0';
                        td.style.fontWeight = '700';
                    } else if (valorDevolucao > 0) {
                        td.style.color = '#ff6b6b';
                        td.style.fontWeight = '700';
                        td.title = 'Valor devolvido';
                    }
                }

                tr.appendChild(td);
            });
            frag.appendChild(tr);
        }

        tBody.appendChild(frag);

        if (index < lista.length) {
            requestAnimationFrame(renderChunk);
        }
    };

    requestAnimationFrame(renderChunk);
}

function renderBotoesLojas(lojas) {
    lojaBotoes.innerHTML = '';
    const todosBtn = document.createElement('button');
    todosBtn.textContent = 'Todas as lojas';
    todosBtn.className = 'loja-btn' + (lojaSelecionada === '__todas' ? ' active' : '');
    todosBtn.onclick = () => { lojaSelecionada = '__todas'; atualizarSelecao(); };
    lojaBotoes.appendChild(todosBtn);

    lojas.forEach(l => {
        const btn = document.createElement('button');
        btn.textContent = l.nome;
        btn.className = 'loja-btn' + (lojaSelecionada === l.nome ? ' active' : '');
        btn.onclick = () => { lojaSelecionada = l.nome; atualizarSelecao(); };
        lojaBotoes.appendChild(btn);
    });
}

async function atualizarSelecao() {
    unidadeNegocioSelect.value = '__todos';
    Array.from(lojaBotoes.children).forEach(btn => {
        btn.classList.toggle('active', btn.textContent === 'Todas as lojas' ? lojaSelecionada === '__todas' : btn.textContent === lojaSelecionada);
    });
    if (typeof reaplicarPeriodoGraficoAposMudancaFiltro === 'function') {
        const periodoAtualizado = await reaplicarPeriodoGraficoAposMudancaFiltro();
        if (periodoAtualizado === false) return;
    } else if (typeof invalidarPeriodoGraficoPendente === 'function') {
        invalidarPeriodoGraficoPendente();
    }

    autoSyncAoTrocarLoja = true;
    try {
        // Sempre recarrega os dados da loja selecionada para manter as lojas virtuais atualizadas.
        await carregarVendas({ retornoRapido: true });
    } catch (_e) {
        const dadosDaLojaSelecionada = (lojaSelecionada && lojaSelecionada !== '__todas')
            ? dados.filter(row => mesmaLoja(row.loja_conta, lojaSelecionada))
            : dados;
        atualizarUnidadesNegocio(dadosDaLojaSelecionada, mapeamentoUnidades || {});
        await filtrar();
    } finally {
        autoSyncAoTrocarLoja = false;
    }
    agendarSegundoPlano(() => carregarGrafico(), 80);
}

function atualizarUnidadesNegocio(lista, mapeamento) {
    // Armazenar o mapeamento globalmente para uso posterior
    if (mapeamento) {
        mapeamentoUnidades = mapeamento;
    }
    
    // Coletar IDs únicos de unidade_negocio dos dados
    const unidadeIds = new Set((lista || []).map(r => String(r.unidade_negocio)).filter(Boolean));
    if (vendasResumoMeta && Array.isArray(vendasResumoMeta.unidades)) {
        vendasResumoMeta.unidades.forEach(unidade => {
            const valor = String(unidade || '').trim();
            if (valor) unidadeIds.add(valor);
        });
    }
    const atual = unidadeNegocioSelect.value;
    
    unidadeNegocioSelect.innerHTML = '<option value="__todos">Todas as lojas virtuais</option>';
    
    // Ordenar IDs e criar options
    const idsOrdenados = Array.from(unidadeIds).sort();
    idsOrdenados.forEach(id => {
        const opt = document.createElement('option');
        opt.value = id; // Valor é o ID
        // Texto é o nome amigável do mapeamento ou fallback para o ID
        const nomeBase = mapeamentoUnidades[id] || mapeamento?.[id] || String(id || '').trim();
        const nomeAmigavel = String(nomeBase || '').replace(/^\s*Unidade\s+/i, '').trim() || String(id || '').trim();
        opt.textContent = nomeAmigavel;
        unidadeNegocioSelect.appendChild(opt);
    });
    
    // Um período sem vendas pode ter devoluções: preserve o filtro explícito.
    // A troca de loja já limpa a seleção em atualizarSelecao.
    if (atual && atual !== '__todos' && !idsOrdenados.includes(atual)) {
        const opt = document.createElement('option');
        opt.value = atual;
        opt.textContent = mapeamentoUnidades[atual] || atual;
        unidadeNegocioSelect.appendChild(opt);
    }
    unidadeNegocioSelect.value = atual || '__todos';
}

function diasIntervalo(inicio, fim) {
    const out = [];
    if (!inicio || !fim) return out;
    let d = new Date(inicio);
    const end = new Date(fim);
    while (d <= end) {
        out.push(d.toISOString().slice(0,10));
        d.setDate(d.getDate() + 1);
    }
    return out;
}

function extrairDatasComDadosVendas(vendas, devolucoes = []) {
    if (dadosModoResumo && vendasResumoMeta && Array.isArray(vendasResumoMeta.dias_com_dados)) {
        return new Set(vendasResumoMeta.dias_com_dados.map(d => String(d || '').slice(0, 10)).filter(Boolean));
    }
    const datas = new Set();
    (vendas || []).forEach(r => {
        const d = String(r?.data || '').slice(0, 10);
        if (d) datas.add(d);
    });
    (devolucoes || []).forEach(r => {
        const d = String(r?.data_emissao || r?.data || '').slice(0, 10);
        if (d) datas.add(d);
    });
    return datas;
}

function datasFaltantes(lista, inicio, fim, devolucoes = []) {
    if (!inicio || !fim) return [];
    const todas = diasIntervalo(inicio, fim);
    const presentes = extrairDatasComDadosVendas(lista, devolucoes);
    return todas.filter(d => !presentes.has(d));
}

function diasNoPeriodo(inicio, fim) {
    if (!inicio || !fim) return 0;
    const d1 = new Date(inicio);
    const d2 = new Date(fim);
    return Math.floor((d2 - d1) / (1000*60*60*24)) + 1;
}

function parseIsoDate(iso) {
    const [ano, mes, dia] = String(iso || '').split('-').map(Number);
    return new Date(ano, (mes || 1) - 1, dia || 1);
}

function toIsoDate(dateObj) {
    const y = dateObj.getFullYear();
    const m = String(dateObj.getMonth() + 1).padStart(2, '0');
    const d = String(dateObj.getDate()).padStart(2, '0');
    return `${y}-${m}-${d}`;
}

function addDaysIso(iso, dias) {
    const d = parseIsoDate(iso);
    d.setDate(d.getDate() + dias);
    return toIsoDate(d);
}

function montarPeriodosEmFila(inicio, fim, maxDiasPorLote = 365) {
    if (!inicio || !fim) return [];
    const totalDias = diasNoPeriodo(inicio, fim);
    if (totalDias <= 0) return [];

    const lotes = [];
    let atualInicio = inicio;
    while (true) {
        const loteFimCandidato = addDaysIso(atualInicio, maxDiasPorLote - 1);
        const atualFim = loteFimCandidato > fim ? fim : loteFimCandidato;
        lotes.push({ data_inicio: atualInicio, data_fim: atualFim });
        if (atualFim >= fim) break;
        atualInicio = addDaysIso(atualFim, 1);
    }
    return lotes;
}

function montarPeriodosFaltantes(inicio, fim, listaDados, listaDevolucoes) {
    if (!inicio || !fim) return [];
    const datasPeriodo = diasIntervalo(inicio, fim);
    if (!datasPeriodo.length) return [];

    const presentes = extrairDatasComDadosVendas(listaDados, listaDevolucoes);

    const faltantes = datasPeriodo.filter(d => !presentes.has(d));
    if (!faltantes.length) return [];

    const periodos = [];
    let inicioBloco = faltantes[0];
    let anterior = faltantes[0];

    for (let i = 1; i < faltantes.length; i++) {
        const atual = faltantes[i];
        const diaSeguinte = addDaysIso(anterior, 1);
        if (atual !== diaSeguinte) {
            periodos.push({ data_inicio: inicioBloco, data_fim: anterior });
            inicioBloco = atual;
        }
        anterior = atual;
    }

    periodos.push({ data_inicio: inicioBloco, data_fim: anterior });
    return periodos;
}

function totalDiasDosPeriodos(periodos) {
    return (periodos || []).reduce((acc, p) => acc + diasNoPeriodo(p.data_inicio, p.data_fim), 0);
}

function contextoPromptFaltantes() {
    return [
        lojaSelecionada || '__todas',
        getDataIniISO() || '',
        getDataFimISO() || ''
    ].join('|');
}

function setStatusProgress(step, total, texto, etapas) {
    const percent = Math.max(0, Math.min(100, Math.round((step / total) * 100)));
    const stepsHtml = etapas.map((e, i) => {
        const done = i < step;
        return `<div style="margin:2px 0; color:${done ? '#9bd1ff' : '#aaa'};">${done ? '&#9989;' : '&#9203;'} ${escaparHtmlVendas(e)}</div>`;
    }).join('');
    statusEl.className = 'status-bar loading';
    statusEl.innerHTML = `${spinnerHtml}${escaparHtmlVendas(texto)}
        <div style="margin-top:8px;background:#222;border-radius:6px;overflow:hidden;">
            <div style="height:8px;width:${percent}%;background:#4facfe;"></div>
        </div>
        <div style="margin-top:6px;font-size:0.85rem;color:#9bd1ff;">${percent}%</div>
        <div style="margin-top:6px;font-size:0.9rem;">${stepsHtml}</div>`;
}

function obterNomesLojasParaSync() {
    return (lojasDisponiveis || [])
        .map(l => String(l?.nome || '').trim())
        .filter(Boolean);
}

function cancelarSyncAutomaticoVendas() {
    if (autoSyncTimer) {
        clearTimeout(autoSyncTimer);
        autoSyncTimer = null;
    }
}

function aguardar(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

async function aguardarFimSyncAtual() {
    if (cancelSolicitado) throw new Error('Sincronização cancelada.');
    const payload = await vendasSyncMonitor.waitForInactive();
    if (cancelSolicitado) throw new Error('Sincronização cancelada.');
    return payload;
}

let envioSyncVendasPromise = null;

function pedidoSyncVendasConfirmado(job, pedido) {
    const mesmaIdentidade = pedido.store_id
        ? String(job?.store_id || '') === String(pedido.store_id)
        : mesmaLoja(job?.loja, pedido.loja);
    return mesmaIdentidade
        && String(job?.data_inicio || '') === pedido.data_inicio
        && String(job?.data_fim || '') === pedido.data_fim
        && Boolean(job?.forcar_resync) === Boolean(pedido.forcar_resync)
        && !['cancelado', 'pausado', 'paused', 'complete', 'concluido', 'finalizado'].includes(String(job?.status || '').toLowerCase());
}

async function recuperarSyncLojaAposFalhaFetch(pedido, erroOriginal) {
    const mensagem = String(erroOriginal?.message || erroOriginal || '').toLowerCase();
    if (!mensagem.includes('failed to fetch') && erroOriginal?.name !== 'TypeError' && erroOriginal?.name !== 'AbortError') {
        return null;
    }
    for (let tentativa = 1; tentativa <= 3; tentativa++) {
        if (cancelSolicitado) return null;
        await aguardar(1000 * tentativa);
        try {
            const payload = await vendasSyncMonitor.refresh({ propagateError: true });
            const jobs = []
                .concat(Array.isArray(payload?.automacao?.jobs) ? payload.automacao.jobs : [])
                .concat(Array.isArray(payload?.active_jobs) ? payload.active_jobs : [])
                .concat(Array.isArray(payload?.progress?.jobs) ? payload.progress.jobs : []);
            const jobConfirmado = jobs.find(job => pedidoSyncVendasConfirmado(job, pedido));
            const configuracao = (payload?.automacao?.lojas || []).find(loja => (
                pedido.store_id
                    ? String(loja?.store_id || '') === String(pedido.store_id)
                    : mesmaLoja(loja?.loja, pedido.loja)
            ));
            if (jobConfirmado && (!pedido.automatico || configuracao?.habilitado)) {
                return {
                    queued: true,
                    recovered_after_fetch_error: true,
                    job_id: jobConfirmado.job_id || jobConfirmado.id || null
                };
            }
        } catch (_erroConfirmacao) {}
    }
    return null;
}

async function iniciarSyncLoja(lojaNome, periodo = null, opcoes = {}) {
    const loja = (lojasDisponiveis || []).find(item => mesmaLoja(item?.nome, lojaNome));
    const pedido = {
        loja: lojaNome,
        data_inicio: periodo?.data_inicio || getDataIniISO(),
        data_fim: periodo?.data_fim || getDataFimISO(),
        forcar_resync: opcoes.forcar_resync ?? Boolean(document.getElementById('forcarSync')?.checked),
        automatico: opcoes.automatico ?? Boolean(document.getElementById('syncAutomatico')?.checked)
    };
    if (loja?.store_id) pedido.store_id = String(loja.store_id);

    let resp;
    try {
        resp = await fetchComTimeout('/api/vendas/sync', {
            method: 'POST',
            headers: obterAuthHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify(pedido),
            signal: syncController?.signal
        });
    } catch (erroFetch) {
        const recuperado = await recuperarSyncLojaAposFalhaFetch(pedido, erroFetch);
        if (recuperado) return recuperado;
        throw erroFetch;
    }

    const result = await resp.json().catch(() => ({}));
    if (!resp.ok) {
        const detalhe = result.detail?.message || result.detail;
        throw new Error(typeof detalhe === 'string' ? detalhe : 'Erro ao registrar a sincronização.');
    }
    if (!result.accepted && !result.started && !result.queued) {
        throw new Error('O servidor não confirmou o registro deste período. Tente atualizar novamente.');
    }
    return result;
}

async function executarSync(opcoes = {}) {
    if (syncEmAndamento) return;
    cancelarSyncAutomaticoVendas();
    fecharCalendariosData();

    const dataIniIso = getDataIniISO();
    const dataFimIso = getDataFimISO();
    if (!dataIniIso || !dataFimIso) {
        alert('Informe data inicial e final para sincronizar.');
        return;
    }
    periodoSelecionadoPeloUsuario = true;
    const lojaSelecionadaNoInicio = lojaSelecionada;
    const opcoesSync = {
        forcar_resync: Boolean(document.getElementById('forcarSync')?.checked),
        automatico: Boolean(document.getElementById('syncAutomatico')?.checked)
    };
    const diasSelecionados = diasNoPeriodo(dataIniIso, dataFimIso);
    if (diasSelecionados <= 0) {
        alert('A data final deve ser igual ou posterior à data inicial.');
        return;
    }
    // A presença de vendas não comprova a conclusão de um dia. O servidor recebe
    // todo o intervalo e conserva os checkpoints por loja, mesmo após fechar a tela.
    const periodoCompleto = { data_inicio: dataIniIso, data_fim: dataFimIso };
    clientId = clientId || obterClientId();
    if (!clientId) { window.location.href = '/frontend_index.html'; return; }

    cancelSolicitado = false;
    syncEmAndamento = true;
    syncController = new AbortController();
    btnSync.disabled = true;
    btnCancel.disabled = true;
    statusEl.className = 'status-bar loading';
    statusEl.innerHTML = `${spinnerHtml}Atualizando lista de lojas...`;
    const lojasAtualizadas = await carregarLojas({ silencioso: true });
    if (cancelSolicitado || syncController.signal.aborted) {
        syncEmAndamento = false;
        setSyncButtons(false);
        syncController = null;
        cancelSolicitado = false;
        statusEl.className = 'status-bar';
        statusEl.textContent = '⏹️ Sincronização cancelada.';
        return;
    }
    if (!lojasAtualizadas) {
        syncEmAndamento = false;
        setSyncButtons(false);
        syncController = null;
        statusEl.className = 'status-bar error';
        statusEl.textContent = 'Não foi possível atualizar a lista de lojas. A sincronização não foi iniciada.';
        return;
    }
    if (
        lojaSelecionada !== lojaSelecionadaNoInicio
        || getDataIniISO() !== dataIniIso
        || getDataFimISO() !== dataFimIso
    ) {
        syncEmAndamento = false;
        setSyncButtons(false);
        syncController = null;
        statusEl.className = 'status-bar error';
        statusEl.textContent = 'Os filtros mudaram durante a atualização das lojas. Clique em Atualizar novamente.';
        return;
    }
    const lojaSelecionadaAindaExiste = lojaSelecionadaNoInicio === '__todas'
        || (lojasDisponiveis || []).some(loja => mesmaLoja(loja?.nome, lojaSelecionadaNoInicio));
    if (!lojaSelecionadaAindaExiste) {
        syncEmAndamento = false;
        setSyncButtons(false);
        syncController = null;
        statusEl.className = 'status-bar error';
        statusEl.textContent = 'A loja selecionada não está mais cadastrada. Selecione outra loja antes de sincronizar.';
        return;
    }
    setSyncButtons(true);
    iniciarMonitoramentoProgresso();

    const lojasParaSincronizar = lojaSelecionadaNoInicio === '__todas'
        ? obterNomesLojasParaSync()
        : [lojaSelecionadaNoInicio];

    if (!lojasParaSincronizar.length) {
        alert('Nenhuma loja dispon\u00edvel para sincronizar.');
        syncEmAndamento = false;
        setSyncButtons(false);
        syncController = null;
        pararMonitoramentoProgresso();
        return;
    }

    const falhas = [];
    let registradas = 0;
    try {
        // Registrar todas as lojas antes de acompanhar a execução: a fila pertence
        // ao servidor e não depende de manter esta página aberta.
        for (let idx = 0; idx < lojasParaSincronizar.length; idx++) {
            if (cancelSolicitado) break;
            const lojaAtual = lojasParaSincronizar[idx];
            statusEl.className = 'status-bar loading';
            statusEl.innerHTML = `${spinnerHtml}<strong>Registrando período para ${idx + 1} de ${lojasParaSincronizar.length} loja(s)...</strong>`;
            try {
                envioSyncVendasPromise = iniciarSyncLoja(lojaAtual, periodoCompleto, opcoesSync);
                await envioSyncVendasPromise;
                registradas += 1;
            } catch (erro) {
                if (cancelSolicitado) break;
                falhas.push({ loja: lojaAtual, mensagem: erro.message || 'Falha de comunicação.' });
            } finally {
                envioSyncVendasPromise = null;
            }
        }
    } finally {
        const foiPausada = cancelSolicitado;
        syncController = null;
        cancelSolicitado = false;
        syncEmAndamento = false;
        setSyncButtons(false);
        const estado = await vendasSyncMonitor.refresh();
        aplicarEstadoMonitorSyncVendas(estado);
        if (falhas.length) {
            statusEl.className = 'status-bar error';
            statusEl.innerHTML = `<strong>Período registrado em ${registradas} loja(s).</strong><br>`
                + falhas.map(falha => `${escaparHtmlVendas(falha.loja)}: ${escaparHtmlVendas(falha.mensagem)}`).join('<br>')
                + '<br>Clique em Atualizar para tentar registrar as lojas restantes.';
        } else if (!foiPausada && !estado?.active) {
            statusEl.className = 'status-bar';
            statusEl.textContent = `Período registrado em ${registradas} loja(s). Acompanhe as pendências de sincronização abaixo.`;
        }
    }
}

btnCancel.addEventListener('click', async () => {
    const estado = vendasSyncMonitor.getLastPayload();
    if (!syncEmAndamento && !estado?.automacao?.habilitadas && !estado?.automacao?.pendentes) return;
    cancelSolicitado = true;
    btnCancel.disabled = true;
    statusEl.className = 'status-bar loading';
    statusEl.innerHTML = `${spinnerHtml}Pausando sincronização e atualização automática de todas as lojas...`;
    try {
        // A pausa deve chegar depois do registro em curso para não ser anulada por ele.
        if (envioSyncVendasPromise) await envioSyncVendasPromise.catch(() => {});
        const resp = await fetchComTimeout('/api/vendas/sync/cancel', {
            method: 'POST',
            headers: obterAuthHeaders()
        });
        if (!resp.ok) throw new Error('O servidor não confirmou a pausa. Tente novamente.');
        if (syncController) syncController.abort();
        const checkbox = document.getElementById('syncAutomatico');
        if (checkbox) checkbox.checked = false;
        aplicarEstadoMonitorSyncVendas(await vendasSyncMonitor.refresh());
        statusEl.className = 'status-bar';
        statusEl.textContent = 'Sincronização pausada. O progresso confirmado foi preservado.';
    } catch (erro) {
        cancelSolicitado = false;
        setSyncButtons(syncEmAndamento);
        statusEl.className = 'status-bar error';
        statusEl.textContent = erro.message || 'Não foi possível pausar a sincronização.';
    } finally {
        if (!syncController) cancelSolicitado = false;
    }
});
