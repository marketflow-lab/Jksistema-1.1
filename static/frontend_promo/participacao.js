function parsePercentValue(txt) {
    const s = String(txt ?? '').trim().replace('%', '').replace(',', '.');
    const n = Number(s);
    return Number.isFinite(n) ? n : null;
}

function parseMoneyValue(txt) {
    const raw = String(txt ?? '').trim();
    if (!raw) return null;
    const cleaned = raw
        .replace(/R\$/gi, '')
        .replace(/\s+/g, '')
        .replace(/\.(?=\d{3}(\D|$))/g, '')
        .replace(',', '.');
    const n = Number(cleaned);
    return Number.isFinite(n) ? n : null;
}

function getValorLinhaPromoAuto(row, aliases) {
    const valor = getFirstRowValueByAliases(row, aliases);
    if (valor) return valor;
    for (const alias of aliases) {
        if (row && row[alias] !== undefined && row[alias] !== null) {
            const direto = String(row[alias]).trim();
            if (direto) return direto;
        }
    }
    return '';
}

function parseNumeroPromoAuto(valor) {
    if (typeof valor === 'number' && Number.isFinite(valor)) return valor;
    const raw = String(valor ?? '').trim();
    if (!raw) return null;
    const money = parseMoneyValue(raw);
    if (money !== null) return money;
    const pct = parsePercentValue(raw);
    return pct !== null ? pct : null;
}

function chaveResultadoParticipacao(promotionId, itemId) {
    return `${String(promotionId || '').trim().toLowerCase()}::${String(itemId || '').trim().toUpperCase()}`;
}

function textoItemParticipacao(item) {
    const itemId = String(item?.item_id || item?.MLB || item?.mlb || '').trim();
    const sku = String(item?.sku || item?.SKU || '').trim();
    const titulo = String(item?.titulo || item?.title || item?.Título || item?.Titulo || '').trim();
    const partes = [];
    if (sku) partes.push(`SKU ${sku}`);
    if (titulo) partes.push(titulo);
    return { itemId: itemId || '-', nota: partes.join(' · ') };
}

function montarLinhasResultadoParticipacao(payload, result) {
    const detalhes = Array.isArray(result?.detalhes) ? result.detalhes : [];
    const detalhePorItem = new Map();
    detalhes.forEach((detalhe) => {
        const itemId = detalhe?.item_id || detalhe?.MLB || detalhe?.mlb;
        const promotionId = detalhe?.promotion_id || detalhe?.promo_b_id || detalhe?.id;
        if (!itemId) return;
        detalhePorItem.set(chaveResultadoParticipacao(promotionId, itemId), detalhe);
    });

    const promocoes = Array.isArray(payload?.promocoes) ? payload.promocoes : [];
    return promocoes.map((promo, idx) => {
        const promotionId = promo?.promotion_id || promo?.promo_b_id || promo?.id || '';
        const nome = String(promo?.nome || promo?.name || promo?.promo_b_nome || promotionId || `Campanha ${idx + 1}`).trim();
        const confirmados = [];
        const ignorados = [];
        const falhas = [];
        const vistos = new Set();

        (Array.isArray(promo?.items) ? promo.items : []).forEach((item) => {
            const itemId = item?.item_id || item?.MLB || item?.mlb;
            const chave = chaveResultadoParticipacao(promotionId, itemId);
            vistos.add(chave);
            const detalhe = detalhePorItem.get(chave);
            const base = textoItemParticipacao(item);
            if (detalhe?.ignored) {
                ignorados.push({ ...base, nota: detalhe.message || detalhe.status || base.nota });
            } else if (detalhe && (detalhe.success === false || detalhe.error)) {
                falhas.push({ ...base, nota: detalhe.error || detalhe.message || base.nota });
            } else {
                confirmados.push(base);
            }
        });

        detalhes.forEach((detalhe) => {
            const itemId = detalhe?.item_id || detalhe?.MLB || detalhe?.mlb;
            const detalhePromotionId = detalhe?.promotion_id || detalhe?.promo_b_id || detalhe?.id || promotionId;
            const chave = chaveResultadoParticipacao(detalhePromotionId, itemId);
            if (String(detalhePromotionId || '').trim() !== String(promotionId || '').trim()) return;
            if (!itemId || vistos.has(chave)) return;
            const base = textoItemParticipacao(detalhe);
            if (detalhe?.ignored) {
                ignorados.push({ ...base, nota: detalhe.message || detalhe.status || base.nota });
            } else if (detalhe.success === false || detalhe.error) {
                falhas.push({ ...base, nota: detalhe.error || detalhe.message || base.nota });
            }
        });

        return { nome, confirmados, ignorados, falhas };
    });
}

function renderListaResultadoParticipacao(titulo, itens) {
    const lista = Array.isArray(itens) ? itens : [];
    const visiveis = lista.slice(0, 80);
    const restante = lista.length - visiveis.length;
    const conteudo = visiveis.length
        ? visiveis.map((item) => `
            <div class="promo-result-item">
                <span class="promo-result-item-id">${escapeHtml(item.itemId || '-')}</span>
                ${item.nota ? `<span class="promo-result-item-note">${escapeHtml(item.nota)}</span>` : ''}
            </div>
        `).join('')
        : '<div class="promo-result-empty">Nenhum anúncio.</div>';
    return `
        <div class="promo-result-list">
            <div class="promo-result-list-title">${escapeHtml(titulo)} (${lista.length})</div>
            ${conteudo}
            ${restante > 0 ? `<div class="promo-result-empty">+${restante} anúncio(s)</div>` : ''}
        </div>
    `;
}

function mostrarResumoParticipacaoPromocoes(payload, result, opcoes = {}) {
    const anterior = document.querySelector('.promo-result-modal-backdrop');
    if (anterior) anterior.remove();

    const totalSucesso = Number(result?.total_sucesso || 0);
    const totalIgnorados = Number(result?.total_ignorados || 0);
    const totalFalha = Number(result?.total_falha || 0);
    const titulo = opcoes.titulo || 'Confirmação de participação';
    const linhas = montarLinhasResultadoParticipacao(payload, result);
    const campanhasHtml = linhas.map((linha) => `
        <section class="promo-result-campaign">
            <div class="promo-result-campaign-title">${escapeHtml(linha.nome)}</div>
            <div class="promo-result-columns">
                ${renderListaResultadoParticipacao('Confirmados', linha.confirmados)}
                ${renderListaResultadoParticipacao('Ignorados', linha.ignorados)}
                ${renderListaResultadoParticipacao('Falhas', linha.falhas)}
            </div>
        </section>
    `).join('') || '<div class="promo-result-empty">Nenhuma campanha retornada.</div>';

    const modal = document.createElement('div');
    modal.className = 'promo-result-modal-backdrop';
    modal.innerHTML = `
        <div class="promo-result-modal" role="dialog" aria-modal="true" aria-label="${escapeHtml(titulo)}">
            <div class="promo-result-modal-header">
                <div>
                    <span class="promo-result-eyebrow">Mercado Livre</span>
                    <h3 class="promo-result-title">${escapeHtml(titulo)}</h3>
                </div>
                <button type="button" class="promo-result-close" data-close-promo-result aria-label="Fechar">×</button>
            </div>
            <div class="promo-result-summary">
                <div class="promo-result-metric success"><strong>${totalSucesso}</strong><span>confirmado(s)</span></div>
                <div class="promo-result-metric ignored"><strong>${totalIgnorados}</strong><span>ignorado(s)</span></div>
                <div class="promo-result-metric failed"><strong>${totalFalha}</strong><span>falha(s)</span></div>
            </div>
            <div class="promo-result-body">${campanhasHtml}</div>
            <div class="promo-result-footer">
                <button type="button" class="btn btn-primary" data-close-promo-result>OK</button>
            </div>
        </div>
    `;

    const fechar = () => {
        document.removeEventListener('keydown', onKeydown);
        modal.remove();
    };
    const onKeydown = (event) => {
        if (event.key === 'Escape') fechar();
    };
    modal.addEventListener('click', (event) => {
        if (event.target === modal || event.target.closest('[data-close-promo-result]')) fechar();
    });
    document.addEventListener('keydown', onKeydown);
    document.body.appendChild(modal);
}

function pedirConfirmacaoParticipacaoCampanhaApi({ nome, total, loja, promo, analise, campanha }) {
    return new Promise((resolve) => {
        const anterior = document.querySelector('.promo-confirm-modal-backdrop');
        if (anterior) anterior.remove();

        const promotionId = String(promo?.promotion_id || analise?.promo_b_id || campanha?.id || '').trim();
        const promotionType = String(promo?.promotion_type || analise?.promo_b_type || campanha?.type || campanha?.promotion_type || '').trim();
        const campanhaResumo = {
            ...(campanha || {}),
            id: promotionId || campanha?.id,
            type: promotionType || campanha?.type,
            promotion_type: promotionType || campanha?.promotion_type,
            name: nome || campanha?.name || campanha?.title,
            title: nome || campanha?.title || campanha?.name,
        };
        const activeCount = parsePromoCount(analise?.active_count ?? analise?.activeCount ?? analise?.active);
        const eligibleCount = parsePromoCount(analise?.eligible_count ?? analise?.eligibleCount ?? analise?.eligible);
        if (activeCount !== null) campanhaResumo.active_count = activeCount;
        if (eligibleCount !== null) campanhaResumo.eligible_count = eligibleCount;

        const totalAtivos = resolvePromoActiveCount(campanhaResumo);
        const totalElegiveis = resolvePromoEligibleCount(campanhaResumo);
        const periodo = formatPromoDateRange(campanhaResumo);
        const status = String(campanhaResumo.status || '').trim();
        const items = Array.isArray(promo?.items) ? promo.items : [];
        const amostraItems = items
            .slice(0, 5)
            .map((item) => String(item?.item_id || item?.sku || item?.titulo || '').trim())
            .filter(Boolean);
        const outrosItems = Math.max(0, total - amostraItems.length);
        const amostraTexto = amostraItems.length
            ? `${amostraItems.join(' · ')}${outrosItems > 0 ? ` · +${outrosItems} outro(s)` : ''}`
            : '';
        const cards = [
            { label: 'Anúncios que serão enviados', value: String(total), highlight: true },
            { label: 'Campanha', value: nome || 'Campanha sem nome' },
            { label: 'Loja', value: loja || 'Não informada' },
            { label: 'Status / Tipo', value: [status, promotionType].filter(Boolean).join(' · ') || '-' },
            { label: 'ID da campanha', value: promotionId || '-' },
            { label: 'Janela da campanha', value: periodo && periodo !== '-' ? periodo : '-' },
            { label: 'Ativos no ML', value: totalAtivos !== null ? String(totalAtivos) : '-' },
            { label: 'Elegíveis no ML', value: totalElegiveis !== null ? String(totalElegiveis) : '-' },
        ];
        const cardsHtml = cards.map((card) => `
            <div class="promo-confirm-card ${card.highlight ? 'promo-confirm-card--highlight' : ''}">
                <span>${escapeHtml(card.label)}</span>
                <strong>${escapeHtml(card.value)}</strong>
            </div>
        `).join('');

        const modal = document.createElement('div');
        modal.className = 'promo-confirm-modal-backdrop';
        modal.innerHTML = `
            <div class="promo-confirm-modal" role="dialog" aria-modal="true" aria-labelledby="promoConfirmTitle">
                <div class="promo-result-modal-header">
                    <div>
                        <span class="promo-result-eyebrow">Ação no Mercado Livre</span>
                        <h3 class="promo-result-title" id="promoConfirmTitle">Confirmar entrada na campanha</h3>
                    </div>
                    <button type="button" class="promo-result-close" data-cancel-promo-participation aria-label="Fechar">×</button>
                </div>
                <div class="promo-confirm-body">
                    <p class="promo-confirm-lead">
                        Esta confirmação vai enviar os anúncios marcados como <strong>Participar</strong> para a campanha selecionada.
                        Confira o resumo antes de continuar.
                    </p>
                    <div class="promo-confirm-grid">${cardsHtml}</div>
                    ${amostraTexto ? `
                        <div class="promo-confirm-alert">
                            <strong>Primeiros anúncios na fila</strong>
                            <span>${escapeHtml(amostraTexto)}</span>
                        </div>
                    ` : ''}
                    <div class="promo-confirm-alert">
                        <strong>Antes de continuar</strong>
                        <span>A ação chama a API do Mercado Livre e tenta incluir esses anúncios na promoção. Se algum item não deveria participar, cancele e ajuste a decisão na tabela.</span>
                    </div>
                </div>
                <div class="promo-confirm-footer">
                    <button type="button" class="btn btn-secondary" data-cancel-promo-participation>Cancelar</button>
                    <button type="button" class="btn btn-success" data-confirm-promo-participation>Confirmar entrada</button>
                </div>
            </div>
        `;

        let resolvido = false;
        const finalizar = (valor) => {
            if (resolvido) return;
            resolvido = true;
            document.removeEventListener('keydown', onKeydown);
            modal.remove();
            resolve(valor);
        };
        const onKeydown = (event) => {
            if (event.key === 'Escape') finalizar(false);
        };
        modal.addEventListener('click', (event) => {
            if (event.target === modal || event.target.closest('[data-cancel-promo-participation]')) finalizar(false);
            if (event.target.closest('[data-confirm-promo-participation]')) finalizar(true);
        });
        document.addEventListener('keydown', onKeydown);
        document.body.appendChild(modal);
        setTimeout(() => modal.querySelector('[data-confirm-promo-participation]')?.focus(), 0);
    });
}

function montarPayloadParticipacoesPromocoes(indiceCampanha = null) {
    const loja = document.getElementById('apiLojaSelect')?.value || '';
    const analises = Array.isArray(apiAnalisesPorCampanha) && apiAnalisesPorCampanha.length
        ? apiAnalisesPorCampanha
        : [{ promo_b_id: '', promo_b_type: '', promo_b_nome: 'Promocao analisada', data: currentData || [] }];
    const promocoes = [];

    analises.forEach((analise, idx) => {
        if (indiceCampanha !== null && Number(indiceCampanha) !== idx) return;
        const rows = Array.isArray(analise?.data) ? analise.data : [];
        const campanha = findApiPromoBCampaignById(analise?.promo_b_id) || {};
        const promotionId = String(analise?.promo_b_id || campanha?.id || '').trim();
        const promotionType = String(analise?.promo_b_type || campanha?.type || campanha?.promotion_type || '').trim();
        const nome = String(analise?.promo_b_nome || campanha?.name || campanha?.title || `Promocao ${idx + 1}`).trim();
        const items = rows.map((row) => {
            const decisao = getValorLinhaPromoAuto(row, ['Ação', 'Acao', 'Participar ou não', 'Participar ou nao']);
            if (!isDecisaoParticipar(decisao)) return null;
            const itemId = getValorLinhaPromoAuto(row, ['MLB', 'mlb', 'Item ID', 'item_id', 'Anúncio', 'Anuncio']);
            if (!itemId) return null;
            const dealPrice = parseNumeroPromoAuto(getValorLinhaPromoAuto(row, ['deal_price', 'preco_promocional_ml', 'Preço Promocional ML', 'Preco Promocional ML', 'Preço Final ML', 'Preco Final ML', 'Preco final ML', 'Preço Final Promoção 2', 'Preco Final Promocao 2']));
            const discountPct = parseNumeroPromoAuto(getValorLinhaPromoAuto(row, ['ML % Campanha', '% Fixa', 'Desconto ML %', 'discount_percentage', 'percentual']));
            return {
                item_id: itemId,
                deal_price: dealPrice,
                discount_percentage: discountPct,
                sku: getValorLinhaPromoAuto(row, ['SKU', 'sku']),
                titulo: getValorLinhaPromoAuto(row, ['Título', 'Titulo', 'title']),
            };
        }).filter(Boolean);

        if (promotionId && items.length) {
            promocoes.push({
                promotion_id: promotionId,
                promotion_type: promotionType,
                nome,
                items,
            });
        }
    });

    return { loja, promocoes };
}

async function executarParticipacaoAutomaticaPromocoes() {
    const prefs = getApiAutoPrefs();
    if (!prefs.enabled) return null;
    const payload = montarPayloadParticipacoesPromocoes();
    const total = payload.promocoes.reduce((sum, promo) => sum + (Array.isArray(promo.items) ? promo.items.length : 0), 0);
    if (!payload.loja || total <= 0) {
        atualizarStatusAutomacaoPromo('Analise concluida, mas nao ha itens com acao Participar.');
        return null;
    }
    if (prefs.approvalRequired) {
        atualizarStatusAutomacaoPromo(`Analise concluida. ${total} anuncio(s) ficaram marcados como Participar, mas a aprovacao automatica esta desativada.`);
        return null;
    }
    atualizarStatusAutomacaoPromo(`Entrando em ${total} anuncio(s) nas promocoes...`);
    iniciarStatusParticipacaoPromocoes({ nome: 'promocoes selecionadas', total, loja: payload.loja });
    const resp = await fetch('/api/promo/aplicar-participacoes', {
        method: 'POST',
        headers: getAuthHeadersWithClient({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(payload),
    });
    const result = await resp.json().catch(() => ({}));
    if (!resp.ok) {
        const erro = buildApiErrorMessage(resp, result, 'Erro ao entrar nas promocoes');
        falharStatusParticipacaoPromocoes({ nome: 'promocoes selecionadas', erro });
        throw new Error(erro);
    }
    const msg = `Entrada concluida: ${result.total_sucesso || 0} sucesso(s), ${result.total_falha || 0} falha(s).`;
    atualizarStatusAutomacaoPromo(msg);
    concluirStatusParticipacaoPromocoes({ nome: 'promocoes selecionadas', result });
    mostrarResumoParticipacaoPromocoes(payload, result, { titulo: 'Entrada concluída nas promoções' });
    return result;
}

async function confirmarParticipacaoCampanhaApi(index) {
    const analise = Array.isArray(apiAnalisesPorCampanha) ? apiAnalisesPorCampanha[index] : null;
    if (!analise) {
        alert('Campanha não encontrada na análise.');
        return;
    }
    const payload = montarPayloadParticipacoesPromocoes(index);
    const promo = payload.promocoes[0];
    const total = promo && Array.isArray(promo.items) ? promo.items.length : 0;
    if (!payload.loja || !promo || total <= 0) {
        alert('Essa campanha não tem anúncios marcados como Participar.');
        return;
    }
    const nome = promo.nome || analise.promo_b_nome || `Campanha ${index + 1}`;
    const campanha = findApiPromoBCampaignById(promo.promotion_id || analise.promo_b_id) || {};
    const ok = await pedirConfirmacaoParticipacaoCampanhaApi({ nome, total, loja: payload.loja, promo, analise, campanha });
    if (!ok) {
        atualizarStatusAutomacaoPromo(`Confirmacao cancelada. Campanha ${nome} nao foi enviada ao Mercado Livre.`);
        return;
    }

    atualizarStatusAutomacaoPromo(`Confirmando participação em ${total} anúncio(s) da campanha ${nome}...`);
    iniciarStatusParticipacaoPromocoes({ nome, total, loja: payload.loja });
    try {
        const resp = await fetch('/api/promo/aplicar-participacoes', {
            method: 'POST',
            headers: getAuthHeadersWithClient({ 'Content-Type': 'application/json' }),
            body: JSON.stringify(payload),
        });
        const result = await resp.json().catch(() => ({}));
        if (!resp.ok) {
            throw new Error(buildApiErrorMessage(resp, result, 'Erro ao confirmar participação'));
        }
        const msg = `Campanha ${nome}: ${result.total_sucesso || 0} sucesso(s), ${result.total_falha || 0} falha(s).`;
        analise.participacao_confirmada = Number(result.total_falha || 0) === 0;
        analise.participacao_resultado = result;
        renderApiAnalysisTabs();
        atualizarStatusAutomacaoPromo(msg);
        concluirStatusParticipacaoPromocoes({ nome, result });
        mostrarResumoParticipacaoPromocoes(payload, result, { titulo: `Campanha ${nome}` });
    } catch (err) {
        falharStatusParticipacaoPromocoes({ nome, erro: err && err.message ? err.message : err });
        atualizarStatusAutomacaoPromo(`Erro ao confirmar participação: ${err && err.message ? err.message : err}`);
        alert(err && err.message ? err.message : 'Erro ao confirmar participação.');
    }
}
