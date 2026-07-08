function atualizarCabecalhoPosVenda() {
    posVendaLojaStatus.textContent = todasAsLojasSelecionadas()
        ? `Todas as contas conectadas (${lojasMercadoLivreConectadas().length})`
        : state.lojaSelecionada
        ? `Loja selecionada: ${state.lojaSelecionada}`
        : 'Escolha uma loja conectada.';
}

function resetarPaginacaoPosVenda() {
    state.posVendaPagina = 1;
    state.posVendaOffset = 0;
    state.posVendaNextOffset = null;
    state.posVendaOffsets = [0];
    state.posVendaCarregadoPara = '';
    state.posVendaConversaSelecionada = null;
    posVendaDetail.classList.add('hidden');
    posVendaDetail.innerHTML = '';
}

function obterBuscaPosVenda() {
    return String(posVendaBusca?.value || '').trim();
}

function filtroPosVendaNaoLidasAtivo() {
    return Boolean(posVendaNaoLidas?.checked);
}

function rotuloFiltroPosVenda() {
    return filtroPosVendaNaoLidasAtivo() ? 'não lidas' : 'com conversa';
}

function atualizarCabecalhoMediacao() {
    mediacaoLojaStatus.textContent = todasAsLojasSelecionadas()
        ? 'Selecione uma loja específica'
        : state.lojaSelecionada
        ? `Loja selecionada: ${state.lojaSelecionada}`
        : 'Escolha uma loja conectada.';
}

function resetarMediacoes() {
    state.mediacaoCarregadoPara = '';
    state.mediacaoConversas = [];
    mediacaoSummary.classList.add('hidden');
    mediacaoSummary.innerHTML = '';
    mediacaoList.innerHTML = '';
}

function obterBuscaMediacao() {
    return String(mediacaoBusca?.value || '').trim();
}

function formatarMoeda(value) {
    const numero = Number(value || 0);
    return numero.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
}

function rotuloUltimaMensagem(venda) {
    const explicito = String(venda?.last_message_sender_label || '').trim();
    if (explicito) return explicito;
    const papel = String(venda?.last_message_role || '').trim().toLowerCase();
    if (papel === 'loja' || papel === 'seller' || papel === 'vendedor') return 'Loja';
    if (papel === 'comprador' || papel === 'buyer') return 'Comprador';
    const origem = String(venda?.last_message_from || '').trim();
    const vendedor = String(venda?.seller_id || '').trim();
    if (origem && vendedor && origem === vendedor) return 'Loja';
    return origem ? 'Comprador' : '';
}

function rotuloStatusPedido(status) {
    const valor = String(status || '').trim().toLowerCase();
    const mapa = {
        paid: 'Pago',
        confirmed: 'Confirmado',
        payment_required: 'Aguardando pagamento',
        payment_in_process: 'Pagamento em analise',
        partially_paid: 'Pagamento parcial',
        pending: 'Pendente',
        cancelled: 'Cancelado',
        canceled: 'Cancelado',
        invalid: 'Invalido',
        closed: 'Finalizado',
        refunded: 'Reembolsado'
    };
    if (mapa[valor]) return mapa[valor];
    return valor ? valor.replace(/_/g, ' ').replace(/\b\w/g, (letra) => letra.toUpperCase()) : 'Status nao informado';
}

function classeStatusPedido(status) {
    const valor = String(status || '').trim().toLowerCase();
    if (['paid', 'confirmed', 'closed'].includes(valor)) return 'ok';
    if (['cancelled', 'canceled', 'invalid', 'refunded'].includes(valor)) return 'danger';
    return 'warn';
}

function resumoProdutosVenda(itens, venda) {
    const lista = Array.isArray(itens) ? itens.filter(Boolean) : [];
    if (!lista.length) return venda.item_title || 'Produto da venda';
    const principal = lista[0] || {};
    const titulo = String(principal.title || 'Produto').trim();
    const qtd = principal.quantity ? `${principal.quantity}x ` : '';
    const sku = principal.sku ? `SKU ${principal.sku}` : '';
    const extras = lista.length > 1 ? ` +${lista.length - 1} item(ns)` : '';
    return [qtd + titulo, sku, extras].filter(Boolean).join(' | ');
}

function conversaPosVendaNaoLida(venda) {
    if (!venda || typeof venda !== 'object') return false;
    const direto = [
        venda.nao_lida,
        venda.unread,
        venda.is_unread,
        venda.has_unread,
        venda.has_unread_messages
    ];
    if (direto.some((valor) => valor === true || Number(valor || 0) > 0)) return true;
    if (Number(venda.unread_count || venda.mensagens_nao_lidas || 0) > 0) return true;
    const conversa = venda.conversation_status || {};
    if (conversa && typeof conversa === 'object') {
        const statusDireto = [
            conversa.nao_lida,
            conversa.unread,
            conversa.is_unread,
            conversa.has_unread,
            conversa.has_unread_messages,
            conversa.unread_count,
            conversa.unread_messages
        ];
        if (statusDireto.some((valor) => valor === true || Number(valor || 0) > 0)) return true;
    }
    const papel = String(venda.last_message_role || '').trim().toLowerCase();
    const rotulo = String(venda.last_message_sender_label || '').trim().toLowerCase();
    return papel === 'comprador' || papel === 'buyer' || rotulo === 'comprador';
}

function chaveCachePosVenda() {
    const filtro = filtroPosVendaNaoLidasAtivo() ? 'nao-lidas' : 'todas';
    if (todasAsLojasSelecionadas()) {
        return `jk_pos_venda_cache:${TODAS_LOJAS_VALUE}:${posVendaDias.value}:pagina-${state.posVendaPagina}:${filtro}:${obterBuscaPosVenda().toLowerCase()}`;
    }
    return `jk_pos_venda_cache:${state.lojaSelecionada || 'sem-loja'}:${posVendaDias.value}:${state.posVendaOffset}:${filtro}:${obterBuscaPosVenda().toLowerCase()}`;
}

function salvarCachePosVenda(data) {
    try {
        localStorage.setItem(chaveCachePosVenda(), JSON.stringify({
            saved_at: new Date().toISOString(),
            data
        }));
    } catch (error) {
        console.warn('[Pós venda] Não foi possível salvar cache:', error);
    }
}

function carregarCachePosVenda() {
    try {
        const bruto = localStorage.getItem(chaveCachePosVenda());
        if (!bruto) return null;
        const payload = JSON.parse(bruto);
        return payload && typeof payload === 'object' ? payload : null;
    } catch (_error) {
        return null;
    }
}

function ordenarConversasPosVendaRecentes(conversas) {
    return [...(conversas || [])].sort((a, b) => {
        const dataA = new Date(a.last_message_date || a.date_created || 0).getTime() || 0;
        const dataB = new Date(b.last_message_date || b.date_created || 0).getTime() || 0;
        return dataB - dataA;
    });
}

async function carregarPosVendaLojaAgregada(nomeLoja, quantidadeNecessaria, busca) {
    const filtroNaoLidas = filtroPosVendaNaoLidasAtivo();
    let offset = 0;
    let nextOffset = 0;
    let chamadas = 0;
    const conversas = [];
    const conversasVistas = new Set();
    let resumo = {
        orders_total: 0,
        orders_avaliadas: 0,
        conversas_total: 0,
        has_next: false,
        dias: Number(posVendaDias.value || 0)
    };

    while (conversas.length < quantidadeNecessaria && nextOffset !== null && nextOffset !== undefined && chamadas < 8) {
        const params = new URLSearchParams({
            loja: nomeLoja,
            dias: posVendaDias.value,
            offset: String(offset),
            limit: '20',
            max_orders: '10000'
        });
        if (busca) params.set('busca', busca);
        if (filtroNaoLidas) params.set('nao_lidas', 'true');
        const response = await fetch(`/api/mercadolivre/pos-venda/conversas?${params.toString()}`, {
            headers: obterAuthHeaders(),
            cache: 'no-store'
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || `Erro ao carregar pós venda de ${nomeLoja}.`);
        resumo = {
            orders_total: Number(data.orders_total || resumo.orders_total || 0),
            orders_avaliadas: Number(resumo.orders_avaliadas || 0) + Number(data.orders_avaliadas || 0),
            conversas_total: Math.max(Number(resumo.conversas_total || 0), Number(data.conversas_total || 0)),
            has_next: data.has_next === true || data.next_offset !== null && data.next_offset !== undefined,
            dias: Number(data.dias || resumo.dias || 0)
        };
        const lote = Array.isArray(data.conversas) ? data.conversas : [];
        lote.forEach((conversa) => {
            const chave = `${nomeLoja}:${conversa.pack_id || ''}:${conversa.order_id || ''}`;
            if (conversasVistas.has(chave)) return;
            conversasVistas.add(chave);
            conversas.push(anexarLojaOrigem(conversa, nomeLoja));
        });
        nextOffset = data.next_offset;
        if (nextOffset === null || nextOffset === undefined || busca) break;
        const novoOffset = Number(nextOffset);
        if (!Number.isFinite(novoOffset) || novoOffset <= offset) break;
        offset = novoOffset;
        chamadas += 1;
    }

    return {
        loja: nomeLoja,
        conversas,
        resumo,
        has_next: resumo.has_next
    };
}

async function carregarPosVendaTodasLojas(busca) {
    const lojas = lojasMercadoLivreConectadas();
    const quantidadeNecessaria = Math.max(20, state.posVendaPagina * 20);
    const resultados = await Promise.all(lojas.map(async (loja) => {
        const nomeLoja = String(loja.nome || '').trim();
        try {
            return await carregarPosVendaLojaAgregada(nomeLoja, quantidadeNecessaria, busca);
        } catch (error) {
            return { loja: nomeLoja, error };
        }
    }));
    const sucessos = resultados.filter((resultado) => resultado.conversas);
    const erros = resultados
        .filter((resultado) => resultado.error)
        .map((resultado) => ({ loja: resultado.loja, erro: mensagemErro(resultado.error) }));
    const todasConversas = ordenarConversasPosVendaRecentes(sucessos.flatMap((resultado) => resultado.conversas || []));
    const inicio = (state.posVendaPagina - 1) * 20;
    const fim = inicio + 20;
    const pagina = todasConversas.slice(inicio, fim);
    const hasNext = todasConversas.length > fim || sucessos.some((resultado) => resultado.has_next);
    return {
        success: true,
        modo_todas: true,
        dias: Number(posVendaDias.value || 0),
        lojas_consultadas: sucessos.length,
        lojas_total: lojas.length,
        erros,
        orders_total: sucessos.reduce((acc, resultado) => acc + Number(resultado.resumo?.orders_total || 0), 0),
        orders_avaliadas: sucessos.reduce((acc, resultado) => acc + Number(resultado.resumo?.orders_avaliadas || 0), 0),
        conversas_total: todasConversas.length,
        nao_lidas: filtroPosVendaNaoLidasAtivo(),
        has_next: hasNext,
        next_offset: hasNext ? fim : null,
        conversas: pagina
    };
}

function renderizarResumoPosVenda(data) {
    const rotuloFiltro = filtroPosVendaNaoLidasAtivo() ? 'Vendas não lidas' : 'Vendas com conversa';
    if (data && data.modo_todas) {
        const erros = Array.isArray(data.erros) ? data.erros.length : 0;
        posVendaSummary.classList.remove('hidden');
        posVendaSummary.innerHTML = `
            <div class="metric"><strong>${Number(data.conversas_total || 0)}</strong><span>${rotuloFiltro}</span></div>
            <div class="metric"><strong>${Number(data.orders_avaliadas || 0)}</strong><span>Vendas avaliadas</span></div>
            <div class="metric"><strong>${Number(data.lojas_consultadas || 0)}</strong><span>Contas consultadas</span></div>
            <div class="metric"><strong>${Number(data.dias || 0)}</strong><span>Dias consultados</span></div>
            <div class="metric"><strong>${erros}</strong><span>Contas com erro</span></div>
        `;
        return;
    }
    posVendaSummary.classList.remove('hidden');
    posVendaSummary.innerHTML = `
        <div class="metric"><strong>${Number(data.conversas_total || 0)}</strong><span>${rotuloFiltro}</span></div>
        <div class="metric"><strong>${Number(data.orders_avaliadas || 0)}</strong><span>Vendas avaliadas</span></div>
        <div class="metric"><strong>${Number(data.orders_total || 0)}</strong><span>Vendas no período</span></div>
        <div class="metric"><strong>${Number(data.dias || 0)}</strong><span>Dias consultados</span></div>
    `;
}

function renderizarPaginacaoPosVenda(data, conversas) {
    const temAnterior = state.posVendaPagina > 1;
    const modoTodas = data && data.modo_todas === true;
    const proximo = data && data.next_offset !== null && data.next_offset !== undefined ? Number(data.next_offset) : null;
    const temProxima = modoTodas
        ? data.has_next === true
        : proximo !== null && !Number.isNaN(proximo);
    state.posVendaNextOffset = temProxima ? proximo : null;

    if (!temAnterior && !temProxima) {
        posVendaPagination.classList.add('hidden');
        posVendaPagination.innerHTML = '';
        return;
    }

    posVendaPagination.classList.remove('hidden');
    posVendaPagination.innerHTML = `
        <span>Página ${state.posVendaPagina} · ${conversas.length} venda(s) com conversa</span>
        <div class="pagination-actions">
            <button class="pagination-btn" type="button" data-action="prev" ${temAnterior ? '' : 'disabled'}>Anterior</button>
            <button class="pagination-btn" type="button" data-action="next" ${temProxima ? '' : 'disabled'}>Próxima</button>
        </div>
    `;

    posVendaPagination.querySelectorAll('.pagination-btn').forEach((button) => {
        button.addEventListener('click', () => {
            const action = button.dataset.action;
            if (action === 'prev' && temAnterior) {
                state.posVendaPagina -= 1;
                if (modoTodas) {
                    state.posVendaOffset = 0;
                } else {
                    state.posVendaOffset = state.posVendaOffsets[state.posVendaPagina - 1] || 0;
                }
                carregarPosVenda(true);
            }
            if (action === 'next' && temProxima) {
                if (modoTodas) {
                    state.posVendaPagina += 1;
                    state.posVendaOffset = 0;
                    carregarPosVenda(true);
                    return;
                }
                if (state.posVendaOffsets.length <= state.posVendaPagina) {
                    state.posVendaOffsets.push(proximo);
                }
                state.posVendaPagina += 1;
                state.posVendaOffset = proximo;
                carregarPosVenda(true);
            }
        });
    });
}

function renderizarPosVenda(conversas) {
    if (!conversas.length) {
        const busca = obterBuscaPosVenda();
        const somenteNaoLidas = filtroPosVendaNaoLidasAtivo();
        state.posVendaConversas = [];
        state.posVendaConversaSelecionada = null;
        state.posVendaModoDetalhe = false;
        posVendaDetail.classList.add('hidden');
        posVendaDetail.innerHTML = '';
        posVendaList.classList.remove('hidden');
        posVendaList.innerHTML = busca
            ? `<div class="empty-state"><div><h2>Nenhuma venda encontrada</h2><p>Nenhuma conversa foi encontrada para "${escapeHtml(busca)}".</p></div></div>`
            : somenteNaoLidas
            ? `<div class="empty-state"><div><h2>Nenhuma conversa não lida</h2><p>${escapeHtml(todasAsLojasSelecionadas() ? 'Não há conversas não lidas nas contas conectadas neste período.' : 'Não há conversas não lidas no período selecionado.')}</p></div></div>`
            : `<div class="empty-state"><div><h2>Nenhuma conversa encontrada</h2><p>${escapeHtml(todasAsLojasSelecionadas() ? 'Não há vendas com conversa iniciada nas contas conectadas neste período.' : 'Não há vendas com conversa iniciada no período selecionado.')}</p></div></div>`;
        return;
    }

    state.posVendaConversas = conversas;
    posVendaList.innerHTML = conversas.map((venda, index) => {
        const lojaOrigem = lojaOrigemItem(venda);
        const lojaBadge = lojaOrigem
            ? `<span class="badge ok">Loja ${escapeHtml(lojaOrigem)}</span>`
            : '';
        const itens = Array.isArray(venda.items) ? venda.items : [];
        const itemComFoto = itens.find((item) => item.thumbnail) || itens[0] || {};
        const foto = itemComFoto.thumbnail
            ? `<img src="${escapeHtml(itemComFoto.thumbnail)}" alt="${escapeHtml(itemComFoto.title || 'Produto')}" loading="lazy" referrerpolicy="no-referrer">`
            : '<span>Sem foto</span>';
        const itensTexto = itens.length
            ? itens.map((item) => {
                const qtd = item.quantity ? `${escapeHtml(item.quantity)}x ` : '';
                const sku = item.sku ? ` · SKU ${escapeHtml(item.sku)}` : '';
                return `${qtd}${escapeHtml(item.title || 'Produto')}${sku}`;
            }).join('<br>')
            : escapeHtml(venda.item_title || 'Produtos da venda');
        const conversa = venda.conversation_status || {};
        const conversaBadge = conversa.status === 'active' ? 'ok' : (conversa.status ? 'warn' : 'danger');
        const naoLida = conversaPosVendaNaoLida(venda);
        const naoLidaBadge = naoLida
            ? `<span class="badge warn">${Number(venda.unread_count || venda.mensagens_nao_lidas || 0) > 1 ? `${Number(venda.unread_count || venda.mensagens_nao_lidas || 0)} não lidas` : 'Não lida'}</span>`
            : '';
        const active = state.posVendaConversaSelecionada
            && String(state.posVendaConversaSelecionada.pack_id || '') === String(venda.pack_id || '')
            && (!lojaOrigem || lojaOrigemItem(state.posVendaConversaSelecionada) === lojaOrigem);
        const ultimaMensagem = escapeHtml(venda.last_message_text || 'Conversa iniciada sem prévia disponível.');
        const ultimaMensagemRotulo = venda.last_message_text ? rotuloUltimaMensagem(venda) : '';
        const ultimaMensagemCabecalho = ultimaMensagemRotulo ? `<span class="last-message-sender">Última mensagem: ${escapeHtml(ultimaMensagemRotulo)}</span>` : '';
        const statusPedido = rotuloStatusPedido(venda.status);
        const statusPedidoClasse = classeStatusPedido(venda.status);
        return `
            <article class="question-card conversation-card ${active ? 'active' : ''} ${naoLida ? 'unread' : ''}" data-index="${index}">
                <div class="question-body">
                    <div class="question-thumb">${foto}</div>
                    <div>
                <div class="question-top">
                    <div>
                        <h3 class="question-title">Pedido ${escapeHtml(venda.order_id || '-')}</h3>
                        ${lojaBadge}
                        <div class="sale-meta pos-sale-primary-meta">
                            <span>Pack ${escapeHtml(venda.pack_id || '-')}</span>
                            <span>Comprador ${escapeHtml(venda.buyer_nickname || venda.buyer_id || '-')}</span>
                            <span class="sale-value">${escapeHtml(formatarMoeda(venda.total_amount))}</span>
                        </div>
                    </div>
                    <span class="question-date">${escapeHtml(formatarData(venda.last_message_date || venda.date_created))}</span>
                </div>
                <div class="sale-items pos-sale-card-product">${escapeHtml(resumoProdutosVenda(itens, venda))}</div>
                <div class="sale-meta pos-sale-secondary-meta">
                    ${naoLidaBadge}
                    <span class="badge order-status ${statusPedidoClasse}">Pedido: ${escapeHtml(statusPedido)}</span>
                    <span>${Number(venda.messages_count || 0)} mensagem(ns)</span>
                    <span>Venda: ${escapeHtml(formatarData(venda.date_created))}</span>
                </div>
                <div class="answer-box pos-sale-last-message ${venda.last_message_text ? '' : 'empty'}">${ultimaMensagemCabecalho}${ultimaMensagem}</div>
                    </div>
                </div>
            </article>
        `;
    }).join('');
    posVendaList.querySelectorAll('.conversation-card').forEach((card) => {
        card.addEventListener('click', () => {
            const index = Number(card.dataset.index || -1);
            const venda = state.posVendaConversas[index];
            if (venda) abrirConversaPosVenda(venda);
        });
    });
    posVendaList.classList.toggle('hidden', !!state.posVendaModoDetalhe);
}

function abrirPaginaDetalhePosVenda(mensagem = '') {
    state.posVendaModoDetalhe = true;
    posVendaSummary.classList.add('hidden');
    posVendaList.classList.add('hidden');
    posVendaPagination.classList.add('hidden');
    posVendaDetail.classList.remove('hidden');
    posVendaDetail.classList.add('full-page');
    if (mensagem) posVendaStatus.textContent = mensagem;
}

function voltarParaListaPosVenda() {
    state.posVendaModoDetalhe = false;
    state.posVendaConversaSelecionada = null;
    posVendaDetail.classList.add('hidden');
    posVendaDetail.classList.remove('full-page');
    posVendaDetail.innerHTML = '';
    posVendaList.classList.remove('hidden');
    if (state.posVendaConversas.length) {
        posVendaSummary.classList.remove('hidden');
        if (posVendaPagination.innerHTML.trim()) posVendaPagination.classList.remove('hidden');
        posVendaStatus.textContent = todasAsLojasSelecionadas()
            ? `${state.posVendaConversas.length} venda(s) ${rotuloFiltroPosVenda()} nas contas conectadas.`
            : `${state.posVendaConversas.length} venda(s) ${rotuloFiltroPosVenda()} em ${state.lojaSelecionada}.`;
        renderizarPosVenda(state.posVendaConversas);
    }
}

function renderizarDetalhePosVenda(conversa, mensagemStatus = '') {
    const lojaOrigem = lojaOrigemItem(conversa);
    const mensagens = Array.isArray(conversa.messages) ? conversa.messages : [];
    const itens = Array.isArray(conversa.items) ? conversa.items : [];
    const item = itens[0] || {};
    const titulo = item.title || conversa.item_title || 'Produto';
    const nomeComprador = conversa.buyer_name || conversa.buyer_nickname || conversa.buyer_id || '-';
    const limite = Math.max(1, Number(conversa.seller_max_message_length || 350) || 350);
    const statusPedido = rotuloStatusPedido(conversa.status);
    const statusPedidoClasse = classeStatusPedido(conversa.status);
    const produtosHtml = itens.length
        ? itens.map((produto) => {
            const foto = produto.thumbnail
                ? `<img src="${escapeHtml(produto.thumbnail)}" alt="${escapeHtml(produto.title || 'Produto')}" loading="lazy" referrerpolicy="no-referrer">`
                : '<div class="question-thumb"><span>Sem foto</span></div>';
            return `
                <div class="pos-sale-product">
                    ${foto}
                    <div>
                        <h4>${escapeHtml(produto.title || 'Produto')}</h4>
                        <div class="sale-meta">
                            <span>SKU ${escapeHtml(produto.sku || '-')}</span>
                            <span>MLB ${escapeHtml(produto.id || '-')}</span>
                            <span>${escapeHtml(produto.quantity || 1)} unidade(s)</span>
                        </div>
                    </div>
                </div>
            `;
        }).join('')
        : '<div class="answer-box empty">Nenhum produto foi encontrado nos dados da venda.</div>';
    const mensagensHtml = mensagens.length
        ? mensagens.map((msg) => `
            <div class="pos-sale-message ${msg.from_role === 'seller' ? 'seller' : 'buyer'}">
                <span class="training-message-role">${msg.from_role === 'seller' ? 'Vendedor' : `Comprador ${escapeHtml(nomeComprador)}`} · ${escapeHtml(formatarData(msg.date))}</span>
                <span class="pos-sale-message-text">${escapeHtml(String(msg.text || '-').trim())}</span>
            </div>
        `).join('')
        : '<div class="answer-box empty">Nenhuma mensagem anterior foi encontrada para esta conversa.</div>';

    abrirPaginaDetalhePosVenda(`Conversa da venda ${conversa.order_id || conversa.pack_id || '-'} aberta${lojaOrigem ? ` na loja ${lojaOrigem}` : ''}.`);
    posVendaDetail.classList.remove('hidden');
    posVendaDetail.innerHTML = `
        <div class="pos-sale-detail-head">
            <div>
                <h3>${escapeHtml(titulo)}</h3>
                <div class="sale-meta">
                    ${lojaOrigem ? `<span>Loja ${escapeHtml(lojaOrigem)}</span>` : ''}
                    <span>Pack ${escapeHtml(conversa.pack_id || '-')}</span>
                    <span>Pedido ${escapeHtml(conversa.order_id || '-')}</span>
                    <span>SKU ${escapeHtml(item.sku || '-')}</span>
                    <span>Comprador ${escapeHtml(nomeComprador)}</span>
                </div>
            </div>
            <div class="pos-sale-detail-actions">
                <button id="btn-pos-venda-voltar-lista" class="action-btn secondary" type="button">Voltar para lista</button>
                <span class="badge order-status ${statusPedidoClasse}">Pedido: ${escapeHtml(statusPedido)}</span>
            </div>
        </div>
        <div class="pos-sale-info-grid">
            <div class="pos-sale-info-card"><span>Valor da venda</span><strong>${escapeHtml(formatarMoeda(conversa.total_amount))}</strong></div>
            <div class="pos-sale-info-card"><span>Status do pedido</span><strong>${escapeHtml(statusPedido)}</strong></div>
            <div class="pos-sale-info-card"><span>Data da venda</span><strong>${escapeHtml(formatarData(conversa.date_created || conversa.date_closed))}</strong></div>
            <div class="pos-sale-info-card"><span>Comprador</span><strong>${escapeHtml(nomeComprador)}</strong></div>
            <div class="pos-sale-info-card"><span>Mensagens</span><strong>${Number(conversa.messages_count || mensagens.length || 0)}</strong></div>
        </div>
        <div class="pos-sale-products">${produtosHtml}</div>
        <div class="pos-sale-messages">${mensagensHtml}</div>
        <div class="pos-sale-reply">
            <textarea id="pos-venda-resposta-texto" maxlength="${limite}" placeholder="Digite a resposta para enviar ao comprador"></textarea>
            <div class="pos-sale-reply-actions">
                <button id="btn-pos-venda-gerar-ia" class="action-btn secondary" type="button">Gerar resposta com IA</button>
                <button id="btn-pos-venda-enviar-resposta" class="action-btn" type="button" disabled>Enviar resposta</button>
            </div>
        </div>
        <div id="pos-venda-resposta-status" class="status-line">${escapeHtml(mensagemStatus || `Limite do Mercado Livre: ${limite} caracteres.`)}</div>
    `;

    const textarea = document.getElementById('pos-venda-resposta-texto');
    const botao = document.getElementById('btn-pos-venda-enviar-resposta');
    const botaoIa = document.getElementById('btn-pos-venda-gerar-ia');
    const botaoVoltar = document.getElementById('btn-pos-venda-voltar-lista');
    const status = document.getElementById('pos-venda-resposta-status');
    const atualizarEstado = () => {
        const texto = textarea.value.trim();
        botao.disabled = !texto || state.posVendaDetalheCarregando;
        status.textContent = texto
            ? `${texto.length}/${limite} caracteres.`
            : `Limite do Mercado Livre: ${limite} caracteres.`;
    };
    textarea.addEventListener('input', atualizarEstado);
    botao.addEventListener('click', () => enviarRespostaPosVenda(conversa, textarea, botao, status));
    botaoIa.addEventListener('click', () => gerarRespostaIaPosVenda(conversa, textarea, botaoIa, botao, status));
    botaoVoltar.addEventListener('click', voltarParaListaPosVenda);
    atualizarEstado();
    posVendaDetail.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function conversaPosVendaCombinaSugestao(conversa, payload) {
    if (!conversa || !payload) return false;
    const loja = lojaPayloadAtendimento(payload);
    if (loja) {
        const lojaConversa = lojaOrigemItem(conversa);
        if (lojaConversa && !valoresIguaisAtendimento(lojaConversa, loja)) return false;
    }
    const packId = normalizarSugestaoResposta(payload.pack_id || payload.pack || '');
    const orderId = normalizarSugestaoResposta(payload.order_id || payload.pedido || '');
    const buyerId = normalizarSugestaoResposta(payload.buyer_id || payload.comprador || '');
    if (packId && normalizarSugestaoResposta(conversa.pack_id) !== packId) return false;
    if (orderId && normalizarSugestaoResposta(conversa.order_id) !== orderId) return false;
    if (buyerId && normalizarSugestaoResposta(conversa.buyer_id) !== buyerId) return false;
    return Boolean(packId || orderId || buyerId);
}

function preencherRespostaPosVendaSugerida(payload, opcoes = {}) {
    const resposta = respostaSugeridaPayload(payload);
    if (!resposta) return { ok: false, message: 'A sugestao nao trouxe texto de resposta.' };
    const conversa = state.posVendaConversaSelecionada;
    if (!conversa || !conversaPosVendaCombinaSugestao(conversa, payload)) {
        return { ok: false, message: 'Abra a conversa pos-venda correspondente para usar esta sugestao.' };
    }
    const textarea = document.getElementById('pos-venda-resposta-texto');
    const status = document.getElementById('pos-venda-resposta-status');
    if (!textarea) return { ok: false, message: 'A caixa de resposta do pos-venda nao esta aberta.' };
    const jaTemTexto = Boolean(normalizarSugestaoResposta(textarea.value));
    if (jaTemTexto && opcoes.force !== true) {
        if (status) status.textContent = 'Sugestao da IA disponivel; o campo ja tinha texto.';
        return { ok: false, skipped: true, message: 'O campo de resposta ja tinha texto.' };
    }
    textarea.value = resposta;
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
    if (opcoes.focus !== false) {
        textarea.focus();
        textarea.setSelectionRange(textarea.value.length, textarea.value.length);
    }
    if (status) status.textContent = 'Sugestao da IA copiada para a resposta. Revise antes de enviar.';
    return { ok: true, message: 'Resposta copiada para a caixa de texto.' };
}

function registrarIntegracaoSidebarPosVenda() {
    window.JKPerguntasPosVenda = window.JKPerguntasPosVenda || {};
    const preencherAnterior = window.JKPerguntasPosVenda.preencherRespostaSugerida;
    window.JKPerguntasPosVenda.preencherRespostaPosVenda = preencherRespostaPosVendaSugerida;
    window.JKPerguntasPosVenda.preencherRespostaSugerida = function preencherRespostaSugeridaAtendimento(payload, opcoes = {}) {
        const tipo = normalizarSugestaoResposta(payload?.tipo || payload?.approval_type || '').toLowerCase();
        if (tipo === 'pos_venda') return preencherRespostaPosVendaSugerida(payload, opcoes);
        if (typeof preencherAnterior === 'function') return preencherAnterior(payload, opcoes);
        return { ok: false, message: 'Tela de atendimento ainda nao pronta para receber a sugestao.' };
    };
}

registrarIntegracaoSidebarPosVenda();

async function abrirConversaPosVenda(venda) {
    if (!venda || state.posVendaDetalheCarregando) return;
    const lojaConversa = lojaOrigemItem(venda) || (todasAsLojasSelecionadas() ? '' : state.lojaSelecionada);
    if (!lojaConversa) {
        posVendaStatus.textContent = 'Não foi possível identificar a loja desta conversa.';
        return;
    }
    state.posVendaConversaSelecionada = venda;
    abrirPaginaDetalhePosVenda('Carregando histórico completo da conversa...');
    renderizarPosVenda(state.posVendaConversas);
    posVendaDetail.innerHTML = '<div class="status-line">Carregando histórico completo da conversa...</div>';
    state.posVendaDetalheCarregando = true;
    try {
        const params = new URLSearchParams({
            loja: lojaConversa,
            pack_id: String(venda.pack_id || '')
        });
        if (venda.order_id) params.set('order_id', String(venda.order_id));
        const response = await fetch(`/api/mercadolivre/pos-venda/conversas/detalhe?${params.toString()}`, {
            headers: obterAuthHeaders(),
            cache: 'no-store'
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || 'Erro ao carregar conversa.');
        const conversa = anexarLojaOrigem(data.conversa || {}, lojaConversa);
        state.posVendaConversaSelecionada = conversa;
        state.posVendaDetalheCarregando = false;
        renderizarPosVenda(state.posVendaConversas);
        renderizarDetalhePosVenda(conversa);
    } catch (error) {
        posVendaDetail.innerHTML = `<div class="answer-box empty">Erro ao carregar conversa: ${escapeHtml(mensagemErro(error))}</div>`;
    } finally {
        state.posVendaDetalheCarregando = false;
    }
}

async function gerarRespostaIaPosVenda(conversa, textarea, botaoIa, botaoEnviar, status) {
    if (!conversa || !textarea || !botaoIa) return;
    const lojaConversa = lojaOrigemItem(conversa) || (todasAsLojasSelecionadas() ? '' : state.lojaSelecionada);
    if (!lojaConversa) {
        status.textContent = 'Não foi possível identificar a loja desta conversa.';
        return;
    }
    botaoIa.disabled = true;
    if (botaoEnviar) botaoEnviar.disabled = true;
    status.textContent = 'Gerando resposta com IA...';
    try {
        const response = await fetch('/api/mercadolivre/pos-venda/conversas/gerar-resposta', {
            method: 'POST',
            headers: {
                ...obterAuthHeaders(),
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                loja: lojaConversa,
                pack_id: String(conversa.pack_id || ''),
                order_id: String(conversa.order_id || ''),
                buyer_id: String(conversa.buyer_id || ''),
                max_chars: Number(conversa.seller_max_message_length || 350)
            })
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(mensagemErroApi(data, 'Erro ao gerar resposta com IA.'));
        textarea.value = String(data.resposta || '').trim();
        textarea.dispatchEvent(new Event('input', { bubbles: true }));
        status.textContent = `Resposta gerada com IA${data.model ? ` (${data.model})` : ''}. Revise e edite antes de enviar.`;
    } catch (error) {
        status.textContent = `Erro ao gerar resposta com IA: ${mensagemErro(error)}`;
    } finally {
        botaoIa.disabled = false;
        if (botaoEnviar) botaoEnviar.disabled = !String(textarea.value || '').trim();
    }
}

async function enviarRespostaPosVenda(conversa, textarea, botao, status) {
    const texto = String(textarea.value || '').trim();
    if (!texto) return;
    const lojaConversa = lojaOrigemItem(conversa) || (todasAsLojasSelecionadas() ? '' : state.lojaSelecionada);
    if (!lojaConversa) {
        status.textContent = 'Não foi possível identificar a loja desta conversa.';
        return;
    }
    botao.disabled = true;
    status.textContent = 'Enviando resposta ao Mercado Livre...';
    try {
        const response = await fetch('/api/mercadolivre/pos-venda/conversas/responder', {
            method: 'POST',
            headers: {
                ...obterAuthHeaders(),
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                loja: lojaConversa,
                pack_id: String(conversa.pack_id || ''),
                order_id: String(conversa.order_id || ''),
                buyer_id: String(conversa.buyer_id || ''),
                texto,
                max_chars: Number(conversa.seller_max_message_length || 350),
                conversa
            })
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(mensagemErroApi(data, 'Erro ao enviar resposta.'));
        textarea.value = '';
        status.textContent = 'Resposta enviada. Atualizando conversa...';
        await abrirConversaPosVenda(anexarLojaOrigem(conversa, lojaConversa));
        carregarPosVenda(true);
        carregarContadoresNotificacoes(true);
    } catch (error) {
        status.textContent = `Erro ao enviar resposta: ${mensagemErro(error)}`;
        botao.disabled = false;
    }
}

function renderizarDadosPosVenda(data, origemCache = false) {
    const conversas = Array.isArray(data.conversas) ? data.conversas : [];
    const busca = obterBuscaPosVenda();
    const rotuloEscopo = rotuloEscopoSelecionado();
    const rotuloFiltro = rotuloFiltroPosVenda();
    const rotuloTotalFiltro = filtroPosVendaNaoLidasAtivo() ? 'conversa(s) não lida(s)' : 'conversa(s)';
    posVendaStatus.textContent = origemCache
        ? `Exibindo informações salvas de ${rotuloEscopo}. Atualizando pela API...`
        : data.modo_todas
            ? (busca
                ? `${Number(data.conversas_total || conversas.length || 0)} resultado(s) para "${busca}" em todas as contas.`
                : `${conversas.length} venda(s) nesta página, de ${Number(data.conversas_total || conversas.length || 0)} ${rotuloTotalFiltro} carregada(s) em ${Number(data.lojas_consultadas || 0)} conta(s).`)
            : (busca
                ? `${Number(data.conversas_total || conversas.length || 0)} resultado(s) para "${busca}" em ${state.lojaSelecionada}.`
                : data.interrompido
                ? `${conversas.length} venda(s) ${rotuloFiltro}. A consulta foi limitada às vendas mais recentes.`
                : `${conversas.length} venda(s) ${rotuloFiltro} em ${state.lojaSelecionada}.`);
    renderizarResumoPosVenda(data);
    renderizarPosVenda(conversas);
    renderizarPaginacaoPosVenda(data, conversas);
    if (state.posVendaModoDetalhe) {
        const lojaDetalhe = lojaOrigemItem(state.posVendaConversaSelecionada);
        abrirPaginaDetalhePosVenda(`Conversa da venda ${state.posVendaConversaSelecionada?.order_id || state.posVendaConversaSelecionada?.pack_id || '-'} aberta${lojaDetalhe ? ` na loja ${lojaDetalhe}` : ''}.`);
    }
}

async function carregarPosVenda(forcar = false) {
    if (!state.lojaSelecionada || state.carregandoPosVenda) return;
    const busca = obterBuscaPosVenda();
    const filtroNaoLidas = filtroPosVendaNaoLidasAtivo();
    const filtroChave = filtroNaoLidas ? 'nao-lidas' : 'todas';
    const chave = todasAsLojasSelecionadas()
        ? `${TODAS_LOJAS_VALUE}:${posVendaDias.value}:${state.posVendaPagina}:${filtroChave}:${busca.toLowerCase()}`
        : `${state.lojaSelecionada}:${posVendaDias.value}:${state.posVendaOffset}:${filtroChave}:${busca.toLowerCase()}`;
    const cacheLocal = carregarCachePosVenda();
    if (!forcar && state.posVendaCarregadoPara === chave && !cacheLocal) return;

    state.carregandoPosVenda = true;
    btnPosVendaRecarregar.disabled = true;
    if (cacheLocal && cacheLocal.data) {
        renderizarDadosPosVenda(cacheLocal.data, true);
    } else {
        posVendaSummary.classList.add('hidden');
        posVendaList.innerHTML = '';
        posVendaPagination.classList.add('hidden');
        posVendaPagination.innerHTML = '';
        posVendaStatus.textContent = busca
            ? `Buscando "${busca}" nas vendas ${rotuloFiltroPosVenda()} de ${rotuloEscopoSelecionado()}...`
            : `Carregando página ${state.posVendaPagina} de vendas ${rotuloFiltroPosVenda()} de ${rotuloEscopoSelecionado()}...`;
    }

    try {
        if (todasAsLojasSelecionadas()) {
            const dataTodas = await carregarPosVendaTodasLojas(busca);
            salvarCachePosVenda(dataTodas);
            renderizarDadosPosVenda(dataTodas, false);
            state.posVendaCarregadoPara = chave;
            return;
        }
        const params = new URLSearchParams({
            loja: state.lojaSelecionada,
            dias: posVendaDias.value,
            offset: String(state.posVendaOffset),
            limit: '20',
            max_orders: '10000'
        });
        if (busca) params.set('busca', busca);
        if (filtroNaoLidas) params.set('nao_lidas', 'true');
        const response = await fetch(`/api/mercadolivre/pos-venda/conversas?${params.toString()}`, {
            headers: obterAuthHeaders(),
            cache: 'no-store'
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || 'Erro ao carregar pós venda.');
        salvarCachePosVenda(data);
        renderizarDadosPosVenda(data, false);
        state.posVendaCarregadoPara = chave;
    } catch (error) {
        posVendaStatus.textContent = cacheLocal && cacheLocal.data
            ? `Erro ao atualizar pela API: ${mensagemErro(error)}. Mantendo as informações salvas.`
            : `Erro ao carregar pós venda: ${mensagemErro(error)}`;
        if (!cacheLocal || !cacheLocal.data) posVendaList.innerHTML = '';
    } finally {
        state.carregandoPosVenda = false;
        btnPosVendaRecarregar.disabled = false;
    }
}
