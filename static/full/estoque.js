function renderLinhaVariacoes(row) {
    const lista = variacoes(row);
    if (!lista.length) return '';
    const linhas = lista.map((variacao, indice) => `
        <tr>
            <td>${escapeHtml(tituloVariacao(variacao, indice))}</td>
            <td class="sku">${escapeHtml(idVariacao(variacao))}</td>
            <td class="sku">${escapeHtml(skuVariacao(variacao))}</td>
            <td>${escapeHtml(inventoryVariacao(variacao))}</td>
            <td class="num">${formatarNumero(estoqueVariacao(variacao))}</td>
            <td class="num">${formatarNumero(indisponivelVariacao(variacao))}</td>
            <td class="num">${formatarNumero(totalVariacao(variacao))}</td>
            <td class="num">${formatarNumero(vendidosVariacao(variacao))}</td>
            <td class="num">${formatarMoeda(precoVariacao(variacao, row))}</td>
        </tr>
    `).join('');
    return `
        <tr class="variation-row">
            <td colspan="11">
                <div class="variation-panel">
                    <div class="variation-title">Variações de ${escapeHtml(idAnuncio(row) || titulo(row))}</div>
                    <div class="variation-table-wrap">
                        <table class="variation-table">
                            <thead>
                                <tr>
                                    <th>Variação</th>
                                    <th>ID variação</th>
                                    <th>SKU</th>
                                    <th>Inventory ID</th>
                                    <th class="num">Estoque Full</th>
                                    <th class="num">Indisponível</th>
                                    <th class="num">Total Full</th>
                                    <th class="num">Vendidos</th>
                                    <th class="num">Preço</th>
                                </tr>
                            </thead>
                            <tbody>${linhas}</tbody>
                        </table>
                    </div>
                </div>
            </td>
        </tr>
    `;
}

function renderLojasMl() {
    renderContasVendasFull();
    atualizarTotaisContasFull();
    renderTodosCardsContasFull();
}

function ordenarLista(lista) {
    const modo = ordenarEl.value;
    return lista.slice().sort((a, b) => {
        if (modo === 'estoque_asc') return estoqueFull(a) - estoqueFull(b) || sku(a).localeCompare(sku(b), 'pt-BR', { numeric: true });
        if (modo === 'preco_desc') return preco(b) - preco(a) || sku(a).localeCompare(sku(b), 'pt-BR', { numeric: true });
        if (modo === 'preco_asc') return preco(a) - preco(b) || sku(a).localeCompare(sku(b), 'pt-BR', { numeric: true });
        if (modo === 'sku_az') return sku(a).localeCompare(sku(b), 'pt-BR', { numeric: true });
        if (modo === 'titulo_az') return titulo(a).localeCompare(titulo(b), 'pt-BR', { numeric: true });
        return estoqueFull(b) - estoqueFull(a) || sku(a).localeCompare(sku(b), 'pt-BR', { numeric: true });
    });
}

function filtrar() {
    const termo = buscaEl.value.trim().toLowerCase();
    let lista = dados;
    if (termo) {
        lista = lista.filter(row => [idAnuncio(row), sku(row), titulo(row), loja(row), statusAnuncio(row)]
            .some(valor => String(valor).toLowerCase().includes(termo)));
    }
    lista = ordenarLista(lista);
    renderResumo(lista);
    renderTabela(lista);
    renderCoberturaFull(lista);
    if (!lojaSelecionada) {
        setStatus('Clique em uma loja para buscar os anuncios Full pelo Mercado Livre.', 'empty');
        return;
    }
    setStatus(lista.length ? `${lista.length} anuncio(s) Full encontrados em ${lojaSelecionada}.` : `Nenhum anuncio Full encontrado em ${lojaSelecionada}.`, lista.length ? '' : 'empty');
}

function renderResumo(lista) {
    const totalEstoque = lista.reduce((acc, row) => acc + estoqueFull(row), 0);
    const totalVendidos = lista.reduce((acc, row) => acc + vendidos(row), 0);
    const valorEstoque = lista.reduce((acc, row) => acc + (estoqueFull(row) * preco(row)), 0);
    resumoEl.innerHTML = [
        ['Loja selecionada', lojaSelecionada || '-'],
        ['Anuncios Full', lista.length],
        ['Estoque Full', formatarNumero(totalEstoque)],
        ['Vendidos', formatarNumero(totalVendidos)],
        ['Valor estoque', formatarMoeda(valorEstoque)],
        ['Lojas ML', lojasMl.length],
    ].map(([label, valor]) => `<div class="summary-card"><span>${label}</span><strong>${valor}</strong></div>`).join('');
}

function renderTabela(lista) {
    if (!lista.length) {
        setTabelaMensagem(lojaSelecionada ? 'Nenhum anuncio Full para exibir.' : 'Selecione uma loja para consultar os anuncios Full.');
        return;
    }
    tbody.innerHTML = lista.map((row, indice) => {
        const listaVariacoes = variacoes(row);
        const chave = chaveVariacoes(row, indice);
        const aberta = variacoesAbertas.has(chave);
        const botaoVariacoes = listaVariacoes.length
            ? `<button class="variation-toggle" type="button" data-variation-key="${escapeHtml(chave)}" aria-expanded="${aberta ? 'true' : 'false'}">Variações (${listaVariacoes.length})</button>`
            : '';
        const linhaPrincipal = `
            <tr>
                <td>${foto(row) ? `<img class="thumb" src="${escapeHtml(foto(row))}" alt="">` : '-'}</td>
                <td class="sku">${escapeHtml(idAnuncio(row) || '-')}</td>
                <td class="sku"><div>${escapeHtml(sku(row) || '-')}</div>${botaoVariacoes}</td>
                <td class="title">${escapeHtml(titulo(row))}</td>
                <td>${escapeHtml(loja(row))}</td>
                <td class="num">${formatarNumero(estoqueFull(row))}</td>
                <td class="num">${formatarNumero(vendidos(row))}</td>
                <td class="num">${formatarMoeda(preco(row))}</td>
                <td>${escapeHtml(statusAnuncio(row))}</td>
                <td>${escapeHtml(tipoAnuncio(row))}</td>
                <td>${linkAnuncio(row) ? `<a class="link" href="${escapeHtml(linkAnuncio(row))}" target="_blank" rel="noopener">Abrir</a>` : '-'}</td>
            </tr>
        `;
        return linhaPrincipal + (listaVariacoes.length && aberta ? renderLinhaVariacoes(row) : '');
    }).join('');
}

async function carregarLojasMl(force = false) {
    setStatus('Carregando lojas com Mercado Livre vinculado...');
    btnAtualizar.disabled = true;
    try {
        const lojasPayload = await fetchJsonFullCached(
            chaveCacheFull('lojas'),
            '/api/full/lojas-mercadolivre',
            { ttlMs: FULL_CACHE_TTL_MS, force }
        );
        lojasMl = Array.isArray(lojasPayload) ? lojasPayload : (Array.isArray(lojasPayload.lojas) ? lojasPayload.lojas : []);
        if (lojaSelecionada && !lojasMl.some(item => String(item.nome || '') === lojaSelecionada)) {
            lojaSelecionada = '';
            dados = [];
            variacoesAbertas.clear();
        }
        if (enviarFullContaSelecionada && !lojasMl.some(item => String(item.nome || '') === enviarFullContaSelecionada)) {
            enviarFullContaSelecionada = '';
            enviarFullDados = [];
        }
        if (transitoContaSelecionada && !lojasMl.some(item => String(item.nome || '') === transitoContaSelecionada)) {
            transitoContaSelecionada = '';
        }
        renderLojasMl();
        renderResumo(dados);
        renderCoberturaFull(dados);
        renderEnviarFull(enviarFullDados);
        renderTransitoFull();
        if (!lojaSelecionada) {
            setTabelaMensagem('Clique em uma loja para consultar os anuncios Full.');
            setStatus(lojasMl.length ? 'Clique em uma loja para buscar os anuncios Full pelo Mercado Livre.' : 'Nenhuma loja com Mercado Livre vinculado.', lojasMl.length ? 'empty' : 'error');
        }
    } catch (e) {
        dados = [];
        lojasMl = [];
        lojaSelecionada = '';
        enviarFullContaSelecionada = '';
        enviarFullDados = [];
        transitoContaSelecionada = '';
        transitoEnvios = [];
        variacoesAbertas.clear();
        renderLojasMl();
        renderResumo([]);
        renderCoberturaFull([]);
        renderEnviarFull([]);
        renderTransitoFull();
        setTabelaMensagem('Nenhum item para exibir.');
        setStatus(e && e.message ? e.message : 'Erro ao carregar lojas do Mercado Livre.', 'error');
    } finally {
        btnAtualizar.disabled = false;
    }
}

async function carregarAnunciosLoja(nomeLoja, force = false) {
    const nome = String(nomeLoja || '').trim();
    if (!nome || carregando) return;
    lojaSelecionada = nome;
    dados = [];
    vendasFull60Map = new Map();
    vendasFull60Ready = false;
    variacoesAbertas.clear();
    buscaEl.value = '';
    carregando = true;
    btnAtualizar.disabled = true;
    renderLojasMl();
    renderResumo([]);
    renderCoberturaFull([]);
    setTabelaMensagem(`Buscando anuncios Full da loja ${nome}...`);
    setStatus(`Buscando anuncios Full da loja ${nome} pelo Mercado Livre...`);
    try {
        dados = await buscarAnunciosFullLoja(nome, force);
        filtrar();
        carregarCoberturaFull(force);
    } catch (e) {
        dados = [];
        vendasFull60Map = new Map();
        vendasFull60Ready = false;
        renderResumo([]);
        renderCoberturaFull([]);
        setTabelaMensagem('Nenhum anuncio Full para exibir.');
        setStatus(e && e.message ? e.message : 'Erro ao carregar anuncios Full da loja.', 'error');
    } finally {
        carregando = false;
        btnAtualizar.disabled = false;
    }
}

function selecionarContaFull(aba, conta) {
    const valor = String(conta || '').trim();
    if (!valor) return;
    if (aba === 'estoque') {
        carregarAnunciosLoja(valor);
        return;
    }
    if (aba === 'vendas') {
        if (fullSalesAccountEl) fullSalesAccountEl.value = valor || CONTA_TODAS_FULL;
        vendasFullCarregado = false;
        renderLojasMl();
        carregarVendasFull();
        return;
    }
    if (aba === 'abc') {
        if (fullAbcAccountEl) fullAbcAccountEl.value = valor || CONTA_TODAS_FULL;
        abcCarregado = false;
        renderLojasMl();
        carregarCurvaAbcFull();
        return;
    }
    if (aba === 'enviar') {
        if (valor === CONTA_TODAS_FULL) return;
        enviarFullContaSelecionada = valor;
        enviarFullDados = [];
        renderLojasMl();
        carregarEnviarFull();
    }
    if (aba === 'transito') {
        if (valor === CONTA_TODAS_FULL) return;
        transitoContaSelecionada = valor;
        renderLojasMl();
        renderTransitoFull();
        carregarTransitoFull(true);
    }
}
