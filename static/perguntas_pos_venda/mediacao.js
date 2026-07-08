function renderizarResumoMediacao(data) {
    mediacaoSummary.classList.remove('hidden');
    mediacaoSummary.innerHTML = `
        <div class="metric"><strong>${Number(data.conversas_total || 0)}</strong><span>Casos em aberto</span></div>
        <div class="metric"><strong>${Number(data.mediacoes_total || 0)}</strong><span>Mediações abertas</span></div>
        <div class="metric"><strong>${Number(data.devolucoes_total || 0)}</strong><span>Devoluções em andamento</span></div>
        <div class="metric"><strong>${Number(data.orders_avaliadas || 0)}</strong><span>Vendas avaliadas</span></div>
        <div class="metric"><strong>${Number(data.dias || 0)}</strong><span>Dias consultados</span></div>
    `;
}

function renderizarMediacoes(mediacoes) {
    if (!mediacoes.length) {
        const busca = obterBuscaMediacao();
        state.mediacaoConversas = [];
        mediacaoList.innerHTML = busca
            ? `<div class="empty-state"><div><h2>Nenhum caso encontrado</h2><p>Nenhuma venda em mediação ou devolução foi encontrada para "${escapeHtml(busca)}".</p></div></div>`
            : '<div class="empty-state"><div><h2>Nenhum caso aberto</h2><p>Não há mediações abertas ou devoluções em andamento no período selecionado.</p></div></div>';
        return;
    }

    state.mediacaoConversas = mediacoes;
    mediacaoList.innerHTML = mediacoes.map((venda) => {
        const itens = Array.isArray(venda.items) ? venda.items : [];
        const itemComFoto = itens.find((item) => item.thumbnail) || itens[0] || {};
        const foto = itemComFoto.thumbnail
            ? `<img src="${escapeHtml(itemComFoto.thumbnail)}" alt="${escapeHtml(itemComFoto.title || 'Produto')}" loading="lazy" referrerpolicy="no-referrer">`
            : '<span>Sem foto</span>';
        const itensTexto = itens.length
            ? itens.map((item) => {
                const qtd = item.quantity ? `${escapeHtml(item.quantity)}x ` : '';
                const sku = item.sku ? ` · SKU ${escapeHtml(item.sku)}` : '';
                const mlb = item.id ? ` · MLB ${escapeHtml(item.id)}` : '';
                return `${qtd}${escapeHtml(item.title || 'Produto')}${sku}${mlb}`;
            }).join('<br>')
            : escapeHtml(venda.item_title || 'Produtos da venda');
        const atualizada = venda.claim_last_updated || venda.claim_date_created || venda.date_created;
        const motivoTexto = venda.claim_reason_detail || venda.claim_reason_name || '';
        const tipoClaim = String(venda.claim_kind || venda.claim_type || '').toLowerCase();
        const ehDevolucao = tipoClaim === 'devolucao' || tipoClaim === 'return' || tipoClaim === 'returns';
        const rotuloTipo = ehDevolucao ? 'Devolução' : 'Mediação';
        const classeTipo = ehDevolucao ? 'danger' : 'warn';
        const motivo = motivoTexto
            ? `Motivo: ${escapeHtml(motivoTexto)}`
            : (ehDevolucao ? 'Devolução em andamento no Mercado Livre.' : 'Mediação aberta no Mercado Livre.');
        return `
            <article class="question-card">
                <div class="question-body">
                    <div class="question-thumb">${foto}</div>
                    <div>
                        <div class="question-top">
                            <div>
                                <h3 class="question-title">Venda ${escapeHtml(venda.order_id || '-')}</h3>
                                <div class="sale-meta">
                                    <span>Claim ${escapeHtml(venda.claim_id || '-')}</span>
                                    <span>Pack ${escapeHtml(venda.pack_id || '-')}</span>
                                    <span>Comprador ${escapeHtml(venda.buyer_nickname || venda.buyer_id || '-')}</span>
                                    <span class="sale-value">${escapeHtml(formatarMoeda(venda.total_amount))}</span>
                                </div>
                            </div>
                            <span class="question-date">${escapeHtml(formatarData(atualizada))}</span>
                        </div>
                        <div class="sale-items">${itensTexto}</div>
                        <div class="sale-meta">
                            <span class="badge ${classeTipo}">${rotuloTipo}</span>
                            <span>Status: ${escapeHtml(venda.claim_status || '-')}</span>
                            <span>Etapa: ${escapeHtml(venda.claim_stage || '-')}</span>
                            <span>Venda: ${escapeHtml(formatarData(venda.date_created))}</span>
                        </div>
                        <div class="answer-box">${motivo}</div>
                    </div>
                </div>
            </article>
        `;
    }).join('');
}

async function carregarMediacoes(forcar = false) {
    if (!state.lojaSelecionada || state.carregandoMediacoes) return;
    const busca = obterBuscaMediacao();
    const chave = `${state.lojaSelecionada}:${mediacaoDias.value}:${busca.toLowerCase()}`;
    if (!forcar && state.mediacaoCarregadoPara === chave) return;

    state.carregandoMediacoes = true;
    btnMediacaoRecarregar.disabled = true;
    mediacaoSummary.classList.add('hidden');
    mediacaoList.innerHTML = '';
    mediacaoStatus.textContent = busca
        ? `Buscando "${busca}" nas vendas em mediação ou devolução de ${state.lojaSelecionada}...`
        : `Carregando vendas em mediação ou devolução de ${state.lojaSelecionada}...`;

    try {
        const params = new URLSearchParams({
            loja: state.lojaSelecionada,
            dias: mediacaoDias.value,
            limit: '20',
            max_claims: '300'
        });
        if (busca) params.set('busca', busca);
        const response = await fetch(`/api/mercadolivre/pos-venda/mediacoes?${params.toString()}`, {
            headers: obterAuthHeaders(),
            cache: 'no-store'
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || 'Erro ao carregar mediações.');
        const mediacoes = Array.isArray(data.conversas) ? data.conversas : [];
        mediacaoStatus.textContent = busca
            ? `${Number(data.conversas_total || mediacoes.length || 0)} caso(s) para "${busca}" em ${state.lojaSelecionada}.`
            : `${Number(data.conversas_total || mediacoes.length || 0)} venda(s) em mediação ou devolução em ${state.lojaSelecionada}.`;
        renderizarResumoMediacao(data);
        renderizarMediacoes(mediacoes);
        state.mediacaoCarregadoPara = chave;
    } catch (error) {
        mediacaoStatus.textContent = `Erro ao carregar casos: ${mensagemErro(error)}`;
        mediacaoList.innerHTML = '';
    } finally {
        state.carregandoMediacoes = false;
        btnMediacaoRecarregar.disabled = false;
    }
}
