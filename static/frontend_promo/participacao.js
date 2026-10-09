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

function capturarParametrosParticipacaoPromocoes() {
    const campanhas = typeof getApiPromoBSelections === 'function' ? getApiPromoBSelections() : [];
    const minima = Number(String(document.getElementById('apiMargemMinima')?.value || 15).replace(',', '.'));
    return JSON.stringify({
        loja: String(document.getElementById('apiLojaSelect')?.value || '').trim(),
        clientId: getPromoClientId(),
        promoA: String(document.getElementById('apiPromoASelect')?.value || '').trim(),
        promoAType: String(document.getElementById('apiPromoASelect')?.selectedOptions?.[0]?.dataset?.promoType || ''),
        campanhas: campanhas.map(promo => [String(promo.value || ''), String(promo.promoType || '')]).sort((a, b) => a[0].localeCompare(b[0])),
        minima: Number.isFinite(minima) ? minima : 15,
        tolerancia: typeof getActionTolerancePct === 'function' ? getActionTolerancePct() : 0,
    });
}

function invalidarContextoParticipacaoPromocoes() {
    (Array.isArray(apiAnalisesPorCampanha) ? apiAnalisesPorCampanha : []).forEach(analise => {
        analise.participacao_contexto_invalidado = true;
        analise.participacao_confirmada = false;
    });
}

function validarContextoReanaliseParticipacao(analise, rows) {
    if (!String(analise?.job_id || '').trim() || analise.participacao_contexto_invalidado === true
        || !analise.participacao_contexto || analise.participacao_contexto !== capturarParametrosParticipacaoPromocoes()
        || rows.some(row => !String(row?.analysis_client_ref || '').trim())) {
        throw new Error('Execute uma nova análise para atualizar os preços das ofertas relâmpago com as regras de margem atuais.');
    }
}

function formatarDiagnosticoPrecoParticipacao(diagnostico) {
    if (!diagnostico || typeof diagnostico !== 'object') return [];
    const linhas = [];
    const money = value => Number(value).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
    const campos = [
        ['initial_price', 'Preço inicial'], ['official_price', 'Proposta atualizada'],
        ['sent_price', 'Preço enviado'], ['standard_price', 'Preço-base'],
        ['sale_price', 'Preço atual de venda'], ['min_price', 'Limite mínimo'], ['max_price', 'Limite máximo'],
    ];
    campos.forEach(([campo, rotulo]) => {
        const valor = diagnostico[campo];
        if (valor !== null && valor !== undefined && valor !== '' && Number.isFinite(Number(valor))) linhas.push(`${rotulo}: ${money(valor)}`);
    });
    const margem = diagnostico.recalculated_margin;
    if (margem !== null && margem !== undefined && margem !== '' && Number.isFinite(Number(margem))) linhas.push(`Margem recalculada: ${Number(margem).toFixed(2).replace('.', ',')}%`);
    if (Number.isSafeInteger(diagnostico.attempt_count) && diagnostico.attempt_count >= 0) linhas.push(`Tentativas de envio: ${diagnostico.attempt_count}`);
    if (diagnostico.decision_code) linhas.push(`Resultado da validação: ${String(diagnostico.decision_code)}`);
    if (diagnostico.ml_error_code) linhas.push(`Código Mercado Livre: ${String(diagnostico.ml_error_code)}`);
    if (Number.isSafeInteger(diagnostico.http_status)) linhas.push(`Resposta Mercado Livre: ${diagnostico.http_status}`);
    return linhas;
}

function textoItemParticipacao(item) {
    const itemId = String(item?.item_id || item?.MLB || item?.mlb || '').trim();
    const sku = String(item?.sku || item?.SKU || '').trim();
    const titulo = String(item?.titulo || item?.title || item?.Título || item?.Titulo || '').trim();
    const partes = [];
    if (sku) partes.push(`SKU ${sku}`);
    if (titulo) partes.push(titulo);
    if (typeof isLinhaOfertaRelampago === 'function' && isLinhaOfertaRelampago(item)) partes.push(formatarJanelaOfertaRelampago(item));
    const ordem = String(item?.client_ref || '').match(/^\d+:(\d+)$/);
    return { itemId: itemId || (ordem ? `Selecionado ${Number(ordem[1]) + 1} sem MLB` : 'Sem MLB'), nota: partes.join(' · ') };
}

function montarLinhasResultadoParticipacao(payload, result) {
    const detalhes = Array.isArray(result?.detalhes) ? result.detalhes : [];
    const detalhePorItem = new Map();
    const detalhePorReferencia = new Map();
    const adicionar = (mapa, chave, detalhe) => mapa.set(chave, [...(mapa.get(chave) || []), detalhe]);
    detalhes.forEach((detalhe) => {
        if (!detalhe || typeof detalhe !== 'object') return;
        const itemId = detalhe?.item_id || detalhe?.MLB || detalhe?.mlb;
        const promotionId = detalhe?.promotion_id || detalhe?.promo_b_id || detalhe?.id;
        if (detalhe.client_ref) adicionar(detalhePorReferencia, chaveResultadoParticipacao(promotionId, detalhe.client_ref), detalhe);
        if (itemId) adicionar(detalhePorItem, chaveResultadoParticipacao(promotionId, itemId), detalhe);
    });

    const promocoes = Array.isArray(payload?.promocoes) ? payload.promocoes : [];
    const referenciasVistas = new Set();
    return promocoes.map((promo, idx) => {
        const promotionId = promo?.promotion_id || promo?.promo_b_id || promo?.id || '';
        const nome = String(promo?.nome || promo?.name || promo?.promo_b_nome || promotionId || `Campanha ${idx + 1}`).trim();
        const confirmados = [];
        const ignorados = [];
        const falhas = [];
        const impedidos = [];
        const indeterminados = [];

        (Array.isArray(promo?.items) ? promo.items : []).forEach((item) => {
            const itemId = item?.item_id || item?.MLB || item?.mlb;
            const chave = chaveResultadoParticipacao(promotionId, itemId);
            const referencia = item.client_ref ? chaveResultadoParticipacao(promotionId, item.client_ref)
                : `${chave}::${String(item.lightning_offer_key || item.offer_id || '')}`;
            if (referenciasVistas.has(referencia)) return;
            referenciasVistas.add(referencia);
            const candidatos = item.client_ref
                ? detalhePorReferencia.get(chaveResultadoParticipacao(promotionId, item.client_ref))
                : detalhePorItem.get(chave);
            const detalhe = candidatos?.length === 1 ? candidatos[0] : null;
            const agendaConfirmada = typeof isLinhaOfertaRelampago === 'function'
                && isLinhaOfertaRelampago(item) && detalhe?.success === true && !detalhe?.error
                && ['applied', 'already_participating'].includes(detalhe?.outcome)
                && formatarDataHoraRelampago(detalhe?.lightning_start_date)
                && formatarDataHoraRelampago(detalhe?.lightning_finish_date);
            const base = textoItemParticipacao(agendaConfirmada ? {
                ...item,
                lightning_start_date: detalhe.lightning_start_date,
                lightning_finish_date: detalhe.lightning_finish_date,
                lightning_schedule_pending: false,
            } : item);
            const mensagem = detalhe?.message || detalhe?.error || '';
            const entrada = { ...base, nota: [base.nota, mensagem].filter(Boolean).join(' · '),
                diagnostico: formatarDiagnosticoPrecoParticipacao(detalhe?.price_diagnostics) };
            const outcome = detalhe?.outcome || (
                detalhe?.success === true && !detalhe.ignored && !detalhe.error ? 'applied'
                    : detalhe?.success === false && !detalhe.ignored ? 'rejected' : 'unknown'
            );
            if (outcome === 'applied' && detalhe.success === true && !detalhe.error) {
                confirmados.push(entrada);
            } else if (outcome === 'already_participating' && detalhe.success === true) {
                ignorados.push(entrada);
            } else if (outcome === 'blocked') {
                impedidos.push(entrada);
            } else if (outcome === 'rejected') {
                falhas.push(entrada);
            } else {
                indeterminados.push({ ...entrada, nota: [base.nota, mensagem || 'Sem confirmação individual. Consulte a participação antes de tentar novamente.'].filter(Boolean).join(' · ') });
            }
        });
        return { nome, confirmados, ignorados, falhas, impedidos, indeterminados };
    });
}

function resumirResultadoParticipacao(payload, result) {
    const linhas = montarLinhasResultadoParticipacao(payload, result);
    const contar = chave => linhas.reduce((total, linha) => total + linha[chave].length, 0);
    const totalSucesso = contar('confirmados');
    const totalIgnorados = contar('ignorados');
    const totalFalha = contar('falhas') + contar('impedidos') + contar('indeterminados');
    return {
        ...result,
        total_itens: totalSucesso + totalIgnorados + totalFalha,
        total_sucesso: totalSucesso,
        total_ignorados: totalIgnorados,
        total_falha: totalFalha,
        success: totalFalha === 0 && totalSucesso + totalIgnorados > 0,
    };
}

function renderListaResultadoParticipacao(titulo, itens, pagina = 0, chaveLista = '') {
    const lista = Array.isArray(itens) ? itens : [];
    const paginas = Math.max(1, Math.ceil(lista.length / 50));
    const atual = Math.min(paginas - 1, Math.max(0, pagina));
    const visiveis = lista.slice(atual * 50, (atual + 1) * 50);
    const conteudo = visiveis.length
        ? visiveis.map((item) => `
            <div class="promo-result-item">
                <span class="promo-result-item-id">${escapeHtml(item.itemId || '-')}</span>
                ${item.nota ? `<span class="promo-result-item-note">${escapeHtml(item.nota)}</span>` : ''}
                ${(Array.isArray(item.diagnostico) ? item.diagnostico : []).map(linha => `<span class="promo-result-item-note">${escapeHtml(linha)}</span>`).join('')}
            </div>
        `).join('')
        : '<div class="promo-result-empty">Nenhum anúncio.</div>';
    return `
        <div class="promo-result-list" data-result-list="${escapeHtml(chaveLista)}">
            <div class="promo-result-list-title">${escapeHtml(titulo)} (${lista.length})</div>
            ${conteudo}
            ${paginas > 1 ? `<div class="promo-result-pagination">
                <button type="button" class="btn btn-secondary" data-result-page="${atual - 1}" data-result-key="${escapeHtml(chaveLista)}" ${atual === 0 ? 'disabled' : ''}>Anterior</button>
                <span>Página ${atual + 1} de ${paginas}</span>
                <button type="button" class="btn btn-secondary" data-result-page="${atual + 1}" data-result-key="${escapeHtml(chaveLista)}" ${atual === paginas - 1 ? 'disabled' : ''}>Próxima</button>
            </div>` : ''}
        </div>
    `;
}

function mostrarResumoParticipacaoPromocoes(payload, result, opcoes = {}) {
    const anterior = document.querySelector('.promo-result-modal-backdrop');
    if (anterior) anterior.remove();

    const resumo = resumirResultadoParticipacao(payload, result);
    const totalSucesso = resumo.total_sucesso;
    const totalIgnorados = resumo.total_ignorados;
    const totalFalha = resumo.total_falha;
    const titulo = opcoes.titulo || 'Confirmação de participação';
    const linhas = montarLinhasResultadoParticipacao(payload, result);
    const listas = new Map();
    const categorias = [['confirmados', 'Inclusão confirmada'], ['ignorados', 'Já participa'], ['impedidos', 'Impedimento técnico'], ['falhas', 'Recusados'], ['indeterminados', 'Resultado não confirmado']];
    const campanhasHtml = linhas.map((linha, idx) => `
        <section class="promo-result-campaign">
            <div class="promo-result-campaign-title">${escapeHtml(linha.nome)}</div>
            <div class="promo-result-columns">
                ${categorias.map(([campo, rotulo]) => {
                    const chave = `${idx}-${campo}`;
                    listas.set(chave, { titulo: rotulo, itens: linha[campo] });
                    return renderListaResultadoParticipacao(rotulo, linha[campo], 0, chave);
                }).join('')}
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
                <div class="promo-result-metric ignored"><strong>${totalIgnorados}</strong><span>já participa(m)</span></div>
                <div class="promo-result-metric failed"><strong>${totalFalha}</strong><span>não concluído(s)</span></div>
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
        const botao = event.target.closest('[data-result-page]');
        if (botao && !botao.disabled) {
            const chave = botao.dataset.resultKey;
            const lista = listas.get(chave);
            if (lista) botao.closest('[data-result-list]').outerHTML = renderListaResultadoParticipacao(lista.titulo, lista.itens, Number(botao.dataset.resultPage), chave);
        }
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
        const selecionadasAnalise = typeof obterLinhasSelecionadasPromocoes === 'function' ? obterLinhasSelecionadasPromocoes(analise?.data) : [];
        const linhaDaOferta = item => {
            if (item.client_ref) {
                const canonica = selecionadasAnalise.find(row => row.analysis_client_ref === item.client_ref);
                if (canonica) return canonica;
            }
            const indice = String(item?.client_ref || '').match(/^\d+:(\d+)$/)?.[1];
            return indice !== undefined ? (selecionadasAnalise[Number(indice)] || item) : item;
        };
        const amostraItems = items
            .slice(0, 5)
            .map((item) => {
                const identificacao = String(item?.item_id || item?.sku || item?.titulo || '').trim();
                if (promotionType.toUpperCase() !== 'LIGHTNING') return identificacao;
                const estoque = obterEstoqueReservadoRelampago(item);
                const aviso = linhaDaOferta(item).action_ja_participa === true ? 'Já participa'
                    : (estoque !== null ? `Estoque reservado: ${estoque}` : 'Estoque reservado não informado');
                return [identificacao, formatarJanelaOfertaRelampago(item), aviso].join(' · ');
            })
            .filter(Boolean);
        const agendasPendentes = promotionType.toUpperCase() === 'LIGHTNING'
            ? items.filter(item => temAgendaPendenteRelampago(item)).length : 0;
        const outrosItems = Math.max(0, total - amostraItems.length);
        const amostraTexto = amostraItems.length
            ? `${amostraItems.join(' · ')}${outrosItems > 0 ? ` · +${outrosItems} outro(s)` : ''}`
            : '';
        const cards = [
            { label: promotionType.toUpperCase() === 'LIGHTNING' ? 'Ofertas selecionadas para processamento' : 'Anúncios selecionados para processamento', value: String(total), highlight: true },
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
        const impedimentosRelampago = promotionType.toUpperCase() === 'LIGHTNING'
            ? Array.from(new Set(selecionadasAnalise.map(row => obterAvisoParticipacaoPromocoes(row)).filter(motivo => motivo && motivo !== 'Já participa'))) : [];

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
                    ${promotionType.toUpperCase() === 'LIGHTNING' ? `
                        <div class="promo-confirm-alert">
                            <strong>Atualização da proposta</strong>
                            <span>Antes da entrada, o preço oficial será conferido. Se a proposta mudar, a margem será recalculada com o mínimo e a tolerância desta análise. A oferta só será enviada se continuar aprovada.</span>
                        </div>
                    ` : ''}
                    ${agendasPendentes ? `
                        <div class="promo-confirm-alert">
                            <strong>Agenda das ofertas relâmpago</strong>
                            <span>${agendasPendentes} oferta(s) serão enviadas à API com a agenda ainda indisponível. Data e horário serão informados pelo Mercado Livre após a inclusão.</span>
                        </div>
                    ` : ''}
                    ${impedimentosRelampago.length ? `
                        <div class="promo-confirm-alert">
                            <strong>Ofertas com impedimento</strong>
                            <span>${escapeHtml(impedimentosRelampago.join(' · '))}</span>
                        </div>
                    ` : ''}
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

function montarPayloadParticipacoesPromocoes(indiceCampanha = null, opcoes = {}) {
    const loja = document.getElementById('apiLojaSelect')?.value || '';
    const clientId = getPromoClientId();
    const analises = Array.isArray(apiAnalisesPorCampanha) && apiAnalisesPorCampanha.length
        ? apiAnalisesPorCampanha
        : [{ promo_b_id: '', promo_b_type: '', promo_b_nome: 'Promocao analisada', data: currentData || [] }];
    const promocoes = [];
    const jobsRelampago = new Set();

    analises.forEach((analise, idx) => {
        if (indiceCampanha !== null && Number(indiceCampanha) !== idx) return;
        const rows = Array.isArray(analise?.data) ? analise.data : [];
        const selecionadas = obterLinhasSelecionadasPromocoes(rows);
        if (selecionadas.length && (analise.origem_loja !== loja || analise.origem_client_id !== clientId)) {
            throw new Error('A análise não pertence à loja e ao cliente atuais. Execute uma nova análise antes de confirmar.');
        }
        const campanha = findApiPromoBCampaignById(analise?.promo_b_id) || {};
        const promotionId = String(analise?.promo_b_id || campanha?.id || '').trim();
        const promotionType = String(analise?.promo_b_type || campanha?.type || campanha?.promotion_type || '').trim();
        const nome = promotionType.toUpperCase() === 'LIGHTNING' ? 'Ofertas relâmpago'
            : String(analise?.promo_b_nome || campanha?.name || campanha?.title || `Promocao ${idx + 1}`).trim();
        const items = selecionadas.map((row, indiceSelecionado) => {
            const relampago = promotionType.toUpperCase() === 'LIGHTNING';
            if (relampago && (row.lightning_action_blocked === true || obterMotivoEstoqueRelampago(row))) return null;
            if (opcoes.automatica && Object.prototype.hasOwnProperty.call(row || {}, 'action_tecnico_apto')) {
                const apto = String(row.action_tecnico_apto ?? '').trim().toLowerCase();
                if (!['1', 'true', 'sim', 'yes'].includes(apto)) return null;
            }
            if (opcoes.automatica && !relampago && Object.prototype.hasOwnProperty.call(row || {}, 'action_financeiro_exato')) {
                const contextoExato = row?.action_financeiro_exato;
                const contextoExatoTexto = String(contextoExato ?? '').trim().toLowerCase();
                if (!(contextoExato === true || ['1', 'true', 'sim', 'yes'].includes(contextoExatoTexto))) return null;
            }
            const itemId = getValorLinhaPromoAuto(row, ['MLB', 'mlb', 'Item ID', 'item_id', 'Anúncio', 'Anuncio']);
            const temContextoAcao = ['action_promotion_id', 'action_offer_id', 'action_deal_price', 'action_discount_percentage', 'action_financeiro_exato'].some(campo => Object.prototype.hasOwnProperty.call(row, campo));
            const actionOfferId = String(getValorLinhaPromoAuto(row, temContextoAcao ? ['action_offer_id'] : ['action_offer_id', 'offer_id', 'offerId', 'ref_id', 'refId']) || '').trim();
            const dealPrice = parseNumeroPromoAuto(getValorLinhaPromoAuto(row, temContextoAcao ? ['action_deal_price'] : ['action_deal_price', 'deal_price', 'preco_promocional_ml', 'Preço Promocional ML', 'Preco Promocional ML', 'Preço Final ML', 'Preco Final ML', 'Preco final ML', 'Preço Final Promoção 2', 'Preco Final Promocao 2']));
            const discountPct = parseNumeroPromoAuto(getValorLinhaPromoAuto(row, temContextoAcao ? ['action_discount_percentage'] : ['action_discount_percentage', 'ML % Campanha', '% Fixa', 'Desconto ML %', 'discount_percentage', 'percentual']));
            return {
                client_ref: String(row.analysis_client_ref || `${idx}:${indiceSelecionado}`).trim(),
                item_id: itemId,
                action_promotion_id: String(row.action_promotion_id || '').trim(),
                offer_id: actionOfferId,
                deal_price: dealPrice,
                discount_percentage: discountPct,
                sku: getValorLinhaPromoAuto(row, ['SKU', 'sku']),
                titulo: getValorLinhaPromoAuto(row, ['Título', 'Titulo', 'title']),
                ...(relampago ? {
                    lightning_offer_key: String(row.lightning_offer_key || '').trim(),
                    lightning_start_date: String(row.lightning_start_date || '').trim(),
                    lightning_finish_date: String(row.lightning_finish_date || '').trim(),
                    lightning_schedule_pending: row.lightning_schedule_pending === true,
                    lightning_stock_min: row.lightning_stock_min ?? null,
                    lightning_stock_max: row.lightning_stock_max ?? null,
                    lightning_stock: obterEstoqueReservadoRelampago(row),
                    lightning_action_blocked: row.lightning_action_blocked === true,
                    lightning_action_reason: String(row.lightning_action_reason || row.lightning_action_blocked_reason || '').trim(),
                } : {}),
            };
        }).filter(Boolean);

        if (items.length) {
            if (promotionType.toUpperCase() === 'LIGHTNING') {
                validarContextoReanaliseParticipacao(analise, selecionadas);
                jobsRelampago.add(String(analise.job_id).trim());
            }
            promocoes.push({
                promotion_id: promotionId,
                promotion_type: promotionType,
                nome,
                items,
            });
        }
    });

    if (jobsRelampago.size > 1) throw new Error('Execute uma nova análise conjunta antes de confirmar essas ofertas relâmpago.');
    return { loja, promocoes, price_policy: jobsRelampago.size ? 'refresh_and_reanalyse' : 'exact',
        ...(jobsRelampago.size ? { analysis_job_id: Array.from(jobsRelampago)[0] } : {}) };
}

function aplicarAtualizacoesParticipacaoPromocoes(payload, result, analisesOriginais) {
    const analisesAtuais = Array.isArray(apiAnalisesPorCampanha) ? apiAnalisesPorCampanha : [];
    const campos = new Set([
        '%', 'Preço Final', 'Frete', 'Tarifa', 'Imposto', 'Valor Líquido', 'Margem', 'Custo', 'reference_margin', 'reference_status',
        'Preço Final ML', 'deal_price', 'preco_promocional_ml', 'preco_final_ml_display', 'ML % Campanha',
        'Tarifa ML', 'Frete ML', 'frete_ml_exato', 'frete_ml_fonte', 'Frete Gratis ML', 'Imposto ML',
        'Desconto ML', 'Valor líquido ML', 'Margem ML', 'action_deal_price', 'action_discount_percentage', 'action_margem_ml',
        'action_tarifa_ml', 'action_valor_liquido_ml', 'action_financeiro_exato', 'action_financeiro_estimado',
        'action_financeiro_estimativa_motivo', 'action_financeiro_motivo', 'action_desconto_ml',
        'action_impedimento_tecnico', 'action_tecnico_apto', 'action_ja_participa',
        'pricing_promotion_id', 'pricing_offer_id', 'pricing_price', 'pricing_source',
        '_jk_tarifa_ml_exata', '_jk_tarifa_ml_liquida', '_jk_tarifa_ml_fonte', '_jk_tarifa_ml_estimada',
        '_jk_tarifa_ml_estimativa_motivo', '_jk_desconto_ml_confiavel', '_jk_desconto_ml_fonte',
        '_jk_desconto_ml_estimado', '_jk_desconto_ml_estimativa_motivo',
    ]);
    const detalhes = Array.isArray(result?.detalhes) ? result.detalhes : [];
    const vistos = new Set();
    const contagens = new Map();
    detalhes.forEach(detalhe => {
        const chave = chaveResultadoParticipacao(detalhe?.promotion_id, detalhe?.client_ref);
        contagens.set(chave, (contagens.get(chave) || 0) + 1);
    });
    detalhes.forEach(detalhe => {
        const ref = String(detalhe?.client_ref || '').trim();
        const promoId = String(detalhe?.promotion_id || '').trim();
        if (!ref || !promoId || !detalhe.analysis_row_updates || typeof detalhe.analysis_row_updates !== 'object') return;
        const chave = chaveResultadoParticipacao(promoId, ref);
        if (vistos.has(chave) || contagens.get(chave) !== 1) return;
        vistos.add(chave);
        const selecionada = payload?.promocoes?.find(promo => String(promo.promotion_id || '').trim() === promoId);
        const enviado = selecionada?.items?.find(item => item.client_ref === ref);
        if (!enviado) return;
        const candidatas = (Array.isArray(analisesOriginais) ? analisesOriginais : []).filter(analise =>
            analisesAtuais.includes(analise) && String(analise.promo_b_id || '').trim() === promoId
            && (!payload.analysis_job_id || String(analise.job_id || '').trim() === payload.analysis_job_id)
            && analise.origem_loja === payload.loja && analise.origem_client_id === getPromoClientId());
        if (candidatas.length !== 1 || payload.loja !== (document.getElementById('apiLojaSelect')?.value || '')) return;
        const rows = (Array.isArray(candidatas[0].data) ? candidatas[0].data : []).filter(row => row.analysis_client_ref === ref);
        if (rows.length !== 1) return;
        const row = rows[0];
        const itemId = getValorLinhaPromoAuto(row, ['MLB', 'item_id', 'mlb']);
        if (String(enviado.item_id || '').trim().toUpperCase() !== String(itemId || '').trim().toUpperCase()
            || (detalhe.item_id && String(detalhe.item_id).trim().toUpperCase() !== String(itemId || '').trim().toUpperCase())) return;
        Object.entries(detalhe.analysis_row_updates).forEach(([campo, valor]) => {
            const canonico = campos.has(campo) ? campo
                : (typeof _keysAreEquivalent === 'function' ? Array.from(campos).find(nome => _keysAreEquivalent(campo, nome)) : null);
            if (canonico && (valor === null || ['string', 'boolean'].includes(typeof valor) || (typeof valor === 'number' && Number.isFinite(valor)))) row[canonico] = valor;
        });
        row.participacao_price_diagnostics = detalhe.price_diagnostics || null;
        if (typeof atualizarLinhaFinanceiraParticipacaoPromocoes === 'function') atualizarLinhaFinanceiraParticipacaoPromocoes(row);
    });
}

async function executarParticipacaoAutomaticaPromocoes() {
    const prefs = getApiAutoPrefs();
    if (!prefs.enabled) return null;
    const bloqueadasRelampago = (Array.isArray(apiAnalisesPorCampanha) ? apiAnalisesPorCampanha : [])
        .filter(analise => String(analise?.promo_b_type || '').toUpperCase() === 'LIGHTNING')
        .flatMap(analise => obterLinhasSelecionadasPromocoes(analise.data))
        .map(row => obterAvisoParticipacaoPromocoes(row))
        .filter(motivo => motivo && motivo !== 'Já participa');
    const avisoRelampago = bloqueadasRelampago.length
        ? ` ${bloqueadasRelampago.length} oferta(s) relâmpago sem envio automático: ${Array.from(new Set(bloqueadasRelampago)).join(' · ')}` : '';
    const payload = montarPayloadParticipacoesPromocoes(null, { automatica: true });
    const total = payload.promocoes.reduce((sum, promo) => sum + (Array.isArray(promo.items) ? promo.items.length : 0), 0);
    if (!payload.loja || total <= 0) {
        atualizarStatusAutomacaoPromo(`Análise concluída sem itens aptos para envio automático.${avisoRelampago}`);
        return null;
    }
    if (prefs.approvalRequired) {
        atualizarStatusAutomacaoPromo(`Analise concluida. ${total} anuncio(s) ficaram marcados como Participar, mas a aprovacao automatica esta desativada.${avisoRelampago}`);
        return null;
    }
    atualizarStatusAutomacaoPromo(`Entrando em ${total} anuncio(s) nas promocoes...`);
    const analisesOriginais = [...apiAnalisesPorCampanha];
    iniciarStatusParticipacaoPromocoes({ nome: 'promocoes selecionadas', total, loja: payload.loja });
    const resp = await fetch('/api/promo/aplicar-participacoes', {
        method: 'POST',
        headers: getAuthHeadersWithClient({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(payload),
    });
    const result = resumirResultadoParticipacao(payload, await resp.json().catch(() => ({})));
    aplicarAtualizacoesParticipacaoPromocoes(payload, result, analisesOriginais);
    if (!resp.ok) {
        const erro = buildApiErrorMessage(resp, result, 'Erro ao entrar nas promocoes');
        falharStatusParticipacaoPromocoes({ nome: 'promocoes selecionadas', erro });
        throw new Error(erro);
    }
    const msg = `Entrada concluida: ${result.total_sucesso || 0} sucesso(s), ${result.total_falha || 0} falha(s).${avisoRelampago}`;
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
    let payload;
    try {
        payload = montarPayloadParticipacoesPromocoes(index);
    } catch (err) {
        alert(err.message);
        return;
    }
    const promo = payload.promocoes[0];
    const total = promo && Array.isArray(promo.items) ? promo.items.length : 0;
    if (!payload.loja || !promo || total <= 0) {
        const motivos = String(analise.promo_b_type || '').toUpperCase() === 'LIGHTNING'
            ? Array.from(new Set(obterLinhasSelecionadasPromocoes(analise.data).map(row => obterAvisoParticipacaoPromocoes(row)).filter(Boolean))) : [];
        alert(motivos.length ? motivos.join(' · ') : 'Essa campanha não tem anúncios marcados como Participar.');
        return;
    }
    const nome = promo.nome || analise.promo_b_nome || `Campanha ${index + 1}`;
    if (analise.participacao_em_andamento) return;
    const revisaoSelecao = Number(analise.participacao_selecao_revisao || 0);
    const campanha = findApiPromoBCampaignById(promo.promotion_id || analise.promo_b_id) || {};
    const ok = await pedirConfirmacaoParticipacaoCampanhaApi({ nome, total, loja: payload.loja, promo, analise, campanha });
    if (!ok) {
        atualizarStatusAutomacaoPromo(`Confirmacao cancelada. Campanha ${nome} nao foi enviada ao Mercado Livre.`);
        return;
    }
    if (analise.participacao_em_andamento || Number(analise.participacao_selecao_revisao || 0) !== revisaoSelecao || apiAnalisesPorCampanha[index] !== analise || analise.origem_loja !== (document.getElementById('apiLojaSelect')?.value || '') || analise.origem_client_id !== getPromoClientId()) {
        alert('A seleção, a loja, o cliente ou a análise mudou durante a confirmação. Abra novamente a confirmação da análise atual.');
        return;
    }
    try {
        if (JSON.stringify(montarPayloadParticipacoesPromocoes(index)) !== JSON.stringify(payload)) throw new Error('Os parâmetros ou a seleção mudaram. Abra novamente a confirmação da análise atual.');
    } catch (err) {
        alert(err.message);
        return;
    }

    atualizarStatusAutomacaoPromo(`Confirmando participação em ${total} anúncio(s) da campanha ${nome}...`);
    analise.participacao_em_andamento = true;
    analise.participacao_confirmada = false;
    iniciarStatusParticipacaoPromocoes({ nome, total, loja: payload.loja });
    let resultadoRecebido = null;
    try {
        const resp = await fetch('/api/promo/aplicar-participacoes', {
            method: 'POST',
            headers: getAuthHeadersWithClient({ 'Content-Type': 'application/json' }),
            body: JSON.stringify(payload),
        });
        const result = resumirResultadoParticipacao(payload, await resp.json().catch(() => ({})));
        resultadoRecebido = result;
        aplicarAtualizacoesParticipacaoPromocoes(payload, result, [analise]);
        if (!resp.ok) {
            throw new Error(buildApiErrorMessage(resp, result, 'Erro ao confirmar participação'));
        }
        const msg = `Campanha ${nome}: ${result.total_sucesso || 0} sucesso(s), ${result.total_falha || 0} falha(s).`;
        analise.participacao_confirmada = result.success && analise.participacao_contexto_invalidado !== true
            && (!analise.participacao_contexto || analise.participacao_contexto === capturarParametrosParticipacaoPromocoes())
            && Number(analise.participacao_selecao_revisao || 0) === revisaoSelecao;
        analise.participacao_resultado = result;
        renderApiAnalysisTabs();
        atualizarStatusAutomacaoPromo(msg);
        concluirStatusParticipacaoPromocoes({ nome, result });
        mostrarResumoParticipacaoPromocoes(payload, result, { titulo: `Campanha ${nome}` });
    } catch (err) {
        const result = resultadoRecebido || resumirResultadoParticipacao(payload, { detalhes: [] });
        analise.participacao_resultado = result;
        renderApiAnalysisTabs();
        falharStatusParticipacaoPromocoes({ nome, erro: err && err.message ? err.message : err });
        atualizarStatusAutomacaoPromo(`Erro ao confirmar participação: ${err && err.message ? err.message : err}`);
        mostrarResumoParticipacaoPromocoes(payload, result, { titulo: `Campanha ${nome}: resultado não confirmado` });
    } finally {
        analise.participacao_em_andamento = false;
    }
}
