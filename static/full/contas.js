function renderSelectContasFull(selectEl) {
    if (!selectEl) return;
    const atual = selectEl.value || CONTA_TODAS_FULL;
    const opcoes = [`<option value="${CONTA_TODAS_FULL}">Todas as contas</option>`].concat(
        lojasMl.map(item => {
            const nome = String(item.nome || '').trim();
            return nome ? `<option value="${escapeHtml(nome)}">${escapeHtml(nome)}</option>` : '';
        }).filter(Boolean)
    );
    selectEl.innerHTML = opcoes.join('');
    if ([...selectEl.options].some(opt => opt.value === atual)) {
        selectEl.value = atual;
    }
}

function renderContasVendasFull() {
    renderSelectContasFull(fullSalesAccountEl);
    renderSelectContasFull(fullAbcAccountEl);
}

function totalContasTextoFull() {
    return lojasMl.length ? `${lojasMl.length} loja(s) vinculada(s)` : 'Nenhuma loja vinculada';
}

function atualizarTotaisContasFull() {
    [lojasMlTotalEl, lojasMlTotalVendasEl, lojasMlTotalEnviarEl, lojasMlTotalAbcEl, lojasMlTotalTransitoEl].forEach(el => {
        if (el) el.textContent = totalContasTextoFull();
    });
}

function renderAccountCardsFull(container, aba, selecionada, options = {}) {
    if (!container) return;
    const cards = [];
    if (options.allowAll) {
        const selected = selecionada === CONTA_TODAS_FULL ? ' selected' : '';
        cards.push(`
            <button class="loja-chip all-accounts${selected}" type="button" data-account-card="1" data-account-tab="${aba}" data-conta="${CONTA_TODAS_FULL}">
                <span>Todas as contas</span>
                <small>Consolidado</small>
            </button>
        `);
    }
    lojasMl.forEach(item => {
        const nomeRaw = String(item.nome || '').trim();
        if (!nomeRaw) return;
        const nome = escapeHtml(nomeRaw);
        const userId = item.user_id ? `<small>ID ${escapeHtml(item.user_id)}</small>` : '<small>ML conectado</small>';
        const selected = nomeRaw === selecionada ? ' selected' : '';
        cards.push(`<button class="loja-chip${selected}" type="button" data-account-card="1" data-account-tab="${aba}" data-conta="${nome}" data-loja="${nome}"><span>${nome}</span>${userId}</button>`);
    });
    if (!cards.length) {
        container.innerHTML = `<span class="loja-chip empty">${escapeHtml(options.emptyText || 'Nenhuma loja com Mercado Livre autenticado em Integracoes.')}</span>`;
        return;
    }
    container.innerHTML = cards.join('');
}

function renderTodosCardsContasFull() {
    renderAccountCardsFull(lojasMlListaEl, 'estoque', lojaSelecionada, {
        emptyText: 'Nenhuma loja com Mercado Livre autenticado em Integracoes.'
    });
    renderAccountCardsFull(lojasMlListaVendasEl, 'vendas', fullSalesAccountEl ? fullSalesAccountEl.value || CONTA_TODAS_FULL : CONTA_TODAS_FULL, { allowAll: true });
    renderAccountCardsFull(lojasMlListaEnviarEl, 'enviar', enviarFullContaSelecionada, {
        emptyText: 'Nenhuma loja com Mercado Livre autenticado em Integracoes.'
    });
    renderAccountCardsFull(lojasMlListaAbcEl, 'abc', fullAbcAccountEl ? fullAbcAccountEl.value || CONTA_TODAS_FULL : CONTA_TODAS_FULL, { allowAll: true });
    renderAccountCardsFull(lojasMlListaTransitoEl, 'transito', transitoContaSelecionada, {
        emptyText: 'Nenhuma loja com Mercado Livre autenticado em Integracoes.'
    });
}

const coberturaCategorias = {
    todos: { label: 'Todos', className: '', ordem: -1 },
    sem_estoque: { label: 'Sem Estoque', className: '', ordem: 0 },
    critico: { label: 'Critico', className: 'risk', ordem: 1 },
    baixo: { label: 'Baixo', className: 'risk', ordem: 2 },
    ideal: { label: 'Ideal', className: 'ideal', ordem: 3 },
    alto: { label: 'Alto', className: 'capital', ordem: 4 },
    muito_alto: { label: 'Muito Alto', className: 'capital', ordem: 5 },
    excessivo: { label: 'Excessivo', className: 'capital', ordem: 6 },
    sem_venda: { label: 'Sem Venda', className: 'capital', ordem: 7 }
};

const coberturaFiltros = {
    todos: ['sem_estoque', 'critico', 'baixo', 'ideal', 'alto', 'muito_alto', 'excessivo', 'sem_venda'],
    sem_estoque: ['sem_estoque'],
    critico: ['critico'],
    baixo: ['baixo'],
    ideal: ['ideal'],
    alto: ['alto'],
    muito_alto: ['muito_alto'],
    excessivo: ['excessivo'],
    sem_venda: ['sem_venda'],
    risco: ['critico', 'baixo'],
    capital: ['alto', 'muito_alto', 'excessivo', 'sem_venda']
};

function chaveSkuFull(row) {
    return String(sku(row) || '').trim().toUpperCase();
}

function montarMapaVendasSku(lista) {
    const mapa = new Map();
    (Array.isArray(lista) ? lista : []).forEach(row => {
        const key = String(primeiro(row, ['sku', 'SKU', 'codigo']) || '').trim().toUpperCase();
        if (!key) return;
        const atual = mapa.get(key) || { qtd: 0, valor: 0, pedidos: new Set() };
        atual.qtd += numero(row.quantidade);
        atual.valor += numero(row.valor);
        const pedido = String(primeiro(row, ['numero', 'pedido', 'id_pedido', 'id']) || '').trim();
        if (pedido) atual.pedidos.add(pedido);
        mapa.set(key, atual);
    });
    return mapa;
}

function chaveMesVenda(row) {
    const dataRaw = String(primeiro(row, ['data', 'data_venda', 'created_at']) || '').trim();
    const match = dataRaw.match(/^(\d{4})-(\d{2})/);
    if (match) return `${match[1]}-${match[2]}`;
    const matchBr = dataRaw.match(/^(\d{2})\/(\d{2})\/(\d{4})/);
    if (matchBr) return `${matchBr[3]}-${matchBr[2]}`;
    const data = new Date(dataRaw);
    if (Number.isNaN(data.getTime())) return '';
    return `${data.getFullYear()}-${String(data.getMonth() + 1).padStart(2, '0')}`;
}

