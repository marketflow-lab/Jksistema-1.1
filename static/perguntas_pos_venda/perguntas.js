function renderizarResumo(data) {
    if (data && data.modo_todas) {
        const resumoTodas = data.status_resumo || {};
        const totalTodas = Number(data.total || 0);
        const retornadasTodas = Number(data.retornadas || 0);
        const respondidasTodas = Number(resumoTodas.ANSWERED || 0);
        const pendentesTodas = Number(resumoTodas.UNANSWERED || 0);
        const lojasConsultadas = Number(data.lojas_consultadas || 0);
        const erros = Array.isArray(data.erros) ? data.erros.length : 0;
        perguntasSummary.classList.remove('hidden');
        perguntasSummary.innerHTML = `
            <div class="metric"><strong>${retornadasTodas}</strong><span>Carreg.</span></div>
            <div class="metric"><strong>${totalTodas}</strong><span>Total</span></div>
            <div class="metric"><strong>${pendentesTodas}</strong><span>Nao resp.</span></div>
            <div class="metric"><strong>${respondidasTodas}</strong><span>Resp.</span></div>
            <div class="metric"><strong>${lojasConsultadas}</strong><span>Contas</span></div>
            <div class="metric"><strong>${erros}</strong><span>Erros</span></div>
        `;
        return;
    }
    const resumo = data.status_resumo || {};
    const total = Number(data.total || 0);
    const retornadas = Number(data.retornadas || 0);
    const respondidas = Number(resumo.ANSWERED || 0);
    const pendentes = Number(resumo.UNANSWERED || 0);
    const tempoML = data.tempo_resposta_ml || {};
    const metricasTempo = tempoML.available ? [
        metricTempoRespostaML('Medio ML', tempoML.total || {}, 'Ultimos 14 dias'),
        metricTempoRespostaML('Comercial', tempoML.weekdays_working_hours || {}, 'Seg. a sex. 9h-18h'),
        metricTempoRespostaML('Fora', tempoML.weekdays_extra_hours || {}, 'Seg. a sex. 18h-00h'),
        metricTempoRespostaML('Fds', tempoML.weekend || {}, 'Sab. e dom.')
    ].join('') : `
        <div class="metric" title="${escapeHtml(tempoML.erro || 'Metrica indisponivel')}"><strong>Sem dados</strong><span>Tempo ML</span></div>
    `;
    perguntasSummary.classList.remove('hidden');
    perguntasSummary.innerHTML = `
        <div class="metric"><strong>${retornadas}</strong><span>Carreg.</span></div>
        <div class="metric"><strong>${total}</strong><span>Total</span></div>
        <div class="metric"><strong>${pendentes}</strong><span>Nao resp.</span></div>
        <div class="metric"><strong>${respondidas}</strong><span>Resp.</span></div>
        ${metricasTempo}
    `;
}

function renderizarChatPerguntas(pergunta) {
    const chat = Array.isArray(pergunta.buyer_question_chat) ? pergunta.buyer_question_chat : [];
    const historicoCount = Number(pergunta.buyer_question_history_count || 0);
    if (historicoCount <= 1 || chat.length <= 2) return '';
    const comprador = escapeHtml(pergunta.buyer_name || pergunta.buyer_nickname || pergunta.from_id || 'Comprador');
    const linhas = chat.map((msg) => {
        const role = String(msg.role || '').toLowerCase() === 'seller' ? 'seller' : 'buyer';
        const label = role === 'seller' ? 'Loja' : comprador;
        return `
            <div class="question-chat-bubble ${role}">
                <span class="training-message-role">${escapeHtml(label)}${msg.date ? ` · ${escapeHtml(formatarData(msg.date))}` : ''}</span>
                ${escapeHtml(msg.text || '-')}
            </div>
        `;
    }).join('');
    return `
        <div class="question-chat">
            <span class="question-chat-title">Histórico deste comprador no anúncio</span>
            ${linhas}
        </div>
    `;
}

function renderizarPaginacaoPerguntas(total) {
    const totalPaginas = Math.max(1, Math.ceil(total / state.tamanhoPaginaPerguntas));
    if (total <= state.tamanhoPaginaPerguntas) {
        perguntasPagination.classList.add('hidden');
        perguntasPagination.innerHTML = '';
        return;
    }

    const inicio = ((state.paginaPerguntas - 1) * state.tamanhoPaginaPerguntas) + 1;
    const fim = Math.min(total, state.paginaPerguntas * state.tamanhoPaginaPerguntas);
    perguntasPagination.classList.remove('hidden');
    perguntasPagination.innerHTML = `
        <span>Mostrando ${inicio}-${fim} de ${total} perguntas</span>
        <div class="pagination-actions">
            <button class="pagination-btn" type="button" data-page="${state.paginaPerguntas - 1}" ${state.paginaPerguntas <= 1 ? 'disabled' : ''}>Anterior</button>
            <span>Página ${state.paginaPerguntas} de ${totalPaginas}</span>
            <button class="pagination-btn" type="button" data-page="${state.paginaPerguntas + 1}" ${state.paginaPerguntas >= totalPaginas ? 'disabled' : ''}>Próxima</button>
        </div>
    `;

    perguntasPagination.querySelectorAll('.pagination-btn').forEach((button) => {
        button.addEventListener('click', () => {
            const pagina = Number(button.dataset.page || 1);
            if (pagina < 1 || pagina > totalPaginas || pagina === state.paginaPerguntas) return;
            carregarPerguntas(pagina);
        });
    });
}

function chavePerguntaAtendimento(pergunta) {
    return `${lojaOrigemItem(pergunta) || ''}::${String(pergunta && pergunta.id || '').trim()}`;
}

function resumoTextoPergunta(texto, limite = 92) {
    const valor = String(texto || '-').replace(/\s+/g, ' ').trim();
    return valor.length > limite ? `${valor.slice(0, limite - 1)}...` : valor;
}

function textoNotaSkuAtendimento(sku) {
    const chave = String(sku || '').trim();
    if (!chave) return '';
    return String((state.treinamentoContexto.notas_sku || {})[chave] || '').trim();
}

function garantirTreinamentoAtendimentoCarregado() {
    if (state.treinamentoCarregado || state.treinamentoAtendimentoCarregando) return;
    state.treinamentoAtendimentoCarregando = true;
    carregarTreinamentoAI()
        .then(() => renderizarPerguntas())
        .catch(() => {})
        .finally(() => {
            state.treinamentoAtendimentoCarregando = false;
        });
}

function renderizarLinhaPerguntaAtendimento(pergunta, selecionada) {
    const lojaOrigem = lojaOrigemItem(pergunta);
    const titulo = pergunta.item_title || pergunta.item_id || 'Anuncio';
    const foto = pergunta.item_thumbnail
        ? `<img src="${escapeHtml(pergunta.item_thumbnail)}" alt="${escapeHtml(titulo)}" loading="lazy" referrerpolicy="no-referrer">`
        : '<span>Sem foto</span>';
    const lojaBadge = lojaOrigem ? `<span class="badge ok">${escapeHtml(lojaOrigem)}</span>` : '';
    const dataKey = chavePerguntaAtendimento(pergunta);
    return `
        <button class="question-row ${selecionada ? 'active' : ''}" type="button" data-question-select="${escapeHtml(dataKey)}">
            <span class="question-row-thumb">${foto}</span>
            <span>
                <span class="question-row-title">${escapeHtml(titulo)}</span>
                <span class="question-row-text">${escapeHtml(resumoTextoPergunta(pergunta.text))}</span>
                <span class="question-row-tags">
                    ${lojaBadge}
                    <span class="badge ${classeStatus(pergunta.status)}">${escapeHtml(rotuloStatus(pergunta.status))}</span>
                </span>
            </span>
            <span class="question-row-date">${escapeHtml(formatarData(pergunta.date_created))}</span>
        </button>
    `;
}

function renderizarDetalhePerguntaAtendimento(pergunta) {
    if (!perguntasDetail) return;
    if (!pergunta) {
        perguntasDetail.innerHTML = '<div class="empty-state"><div><h2>Selecione uma pergunta</h2><p>A resposta, o contexto e as orienta&ccedil;&otilde;es de IA aparecem aqui.</p></div></div>';
        return;
    }
    garantirTreinamentoAtendimentoCarregado();
    const lojaOrigem = lojaOrigemItem(pergunta);
    const lojaBadge = lojaOrigem ? `<span class="badge ok">Loja ${escapeHtml(lojaOrigem)}</span>` : '';
    const titulo = pergunta.item_title || pergunta.item_id || 'Anuncio';
    const tituloHtml = pergunta.item_permalink
        ? `<a href="${escapeHtml(pergunta.item_permalink)}" target="_blank" rel="noopener">${escapeHtml(titulo)}</a>`
        : escapeHtml(titulo);
    const sku = String(pergunta.item_sku || '').trim();
    const comprador = pergunta.buyer_name || pergunta.buyer_nickname || pergunta.from_id || '-';
    const answer = pergunta.answer && pergunta.answer.text
        ? `<div class="answer-box">${escapeHtml(pergunta.answer.text)}</div>`
        : '<div class="answer-box empty">Sem resposta registrada.</div>';
    const chatPerguntas = renderizarChatPerguntas(pergunta);
    const podeResponder = !(pergunta.answer && pergunta.answer.text) && String(pergunta.status || '').toUpperCase() === 'UNANSWERED';
    const dataKey = chavePerguntaAtendimento(pergunta);
    const notaSku = textoNotaSkuAtendimento(sku);
    const linkAnuncio = pergunta.item_permalink
        ? `<a class="action-btn secondary" href="${escapeHtml(pergunta.item_permalink)}" target="_blank" rel="noopener">Abrir an&uacute;ncio</a>`
        : '';
    const composer = podeResponder ? `
        <div class="question-answer-composer">
            <textarea class="question-answer-text" maxlength="2000" placeholder="Digite a resposta manualmente ou gere uma sugestao com IA para editar antes de enviar."></textarea>
            <div class="question-reply-options">
                <label><input class="question-save-example-checkbox" type="checkbox" checked>Usar esta resposta como exemplo da IA</label>
                <span>Salva pergunta, resposta, loja e SKU no treinamento.</span>
            </div>
            <div class="question-answer-actions">
                <button class="action-btn secondary question-ai-answer-btn" type="button" data-action="gerar-ia">Gerar IA</button>
                <button class="action-btn secondary question-save-example-btn" type="button" data-action="salvar-exemplo" disabled>Salvar exemplo</button>
                <button class="action-btn secondary question-send-save-example-btn" type="button" data-action="enviar-salvar-exemplo" disabled>Responder e salvar exemplo</button>
                <button class="action-btn question-send-answer-btn" type="button" data-action="enviar-resposta" disabled>Responder</button>
                <span class="question-answer-status">Limite do Mercado Livre: 2000 caracteres.</span>
            </div>
        </div>
    ` : '';

    perguntasDetail.innerHTML = `
        <article class="question-detail-card" data-question-card data-question-id="${escapeHtml(pergunta.id || '')}" data-question-loja="${escapeHtml(lojaOrigem)}" data-question-key="${escapeHtml(dataKey)}">
            <header class="question-detail-head">
                <div>
                    <div class="question-detail-kicker">
                        ${lojaBadge}
                        <span class="badge ${classeStatus(pergunta.status)}">${escapeHtml(rotuloStatus(pergunta.status))}</span>
                        ${sku ? '<span class="badge warn">SKU ' + escapeHtml(sku) + '</span>' : ''}
                    </div>
                    <h3 class="question-detail-title">${tituloHtml}</h3>
                    <div class="question-detail-meta">
                        <span>An&uacute;ncio ${escapeHtml(pergunta.item_id || '-')}</span>
                        <span>SKU ${escapeHtml(sku || '-')}</span>
                        <span>Comprador ${escapeHtml(comprador)}</span>
                        <span>Pergunta ${escapeHtml(pergunta.id || '-')}</span>
                    </div>
                </div>
                <span class="question-detail-date">${escapeHtml(formatarData(pergunta.date_created))}</span>
            </header>
            <div class="question-detail-body">
                <div class="question-detail-main">
                    <span class="question-detail-label">Pergunta do comprador</span>
                    <div class="question-detail-text">${escapeHtml(pergunta.text || '-')}</div>
                    ${chatPerguntas || answer}
                </div>
                <aside class="question-detail-context">
                    <div class="question-context-card">
                        <h3>Atalhos de decis&atilde;o</h3>
                        <ul>
                            <li>Revise compatibilidade, c&oacute;digo, lado, motor e ano antes de confirmar.</li>
                            <li>Quando faltar dado, pe&ccedil;a foto da etiqueta, c&oacute;digo ou chassi.</li>
                            <li>N&atilde;o prometa aplica&ccedil;&atilde;o se o SKU exigir confer&ecirc;ncia.</li>
                        </ul>
                    </div>
                    <div class="question-context-card">
                        <h3>Dados r&aacute;pidos</h3>
                        <p>SKU ${escapeHtml(sku || '-')} &middot; ${escapeHtml(titulo)}.</p>
                    </div>
                    <div class="question-context-card">
                        <h3>Orienta&ccedil;&otilde;es da IA para este SKU</h3>
                        <textarea class="question-sku-guidance-text" ${sku ? '' : 'disabled'} placeholder="${sku ? 'Aplicacoes confirmadas, excecoes, codigos e cuidados deste SKU.' : 'Sem SKU para salvar orientacoes especificas.'}">${escapeHtml(notaSku)}</textarea>
                        <div class="question-context-actions">
                            <button class="action-btn secondary question-sku-guidance-save" type="button" ${sku ? '' : 'disabled'}>Salvar orienta&ccedil;&atilde;o</button>
                            ${linkAnuncio}
                            <span class="question-answer-status question-sku-guidance-status"></span>
                        </div>
                    </div>
                </aside>
            </div>
            ${composer}
        </article>
    `;
}

function renderizarPerguntas() {
    const perguntas = state.perguntas;
    if (!perguntas.length) {
        const textoVazio = todasAsLojasSelecionadas()
            ? 'Não há perguntas para o filtro selecionado nas contas conectadas.'
            : 'Não há perguntas para o filtro selecionado nesta loja.';
        perguntasList.innerHTML = `<div class="empty-state"><div><h2>Nenhuma pergunta encontrada</h2><p>${escapeHtml(textoVazio)}</p></div></div>`;
        if (perguntasDetail) perguntasDetail.innerHTML = '';
        perguntasPagination.classList.add('hidden');
        perguntasPagination.innerHTML = '';
        return;
    }

    const total = state.totalPerguntas || perguntas.length;
    const totalPaginas = Math.max(1, Math.ceil(total / state.tamanhoPaginaPerguntas));
    state.paginaPerguntas = Math.min(Math.max(1, state.paginaPerguntas), totalPaginas);
    const pagina = perguntas;

    let perguntaSelecionada = pagina.find((pergunta) => chavePerguntaAtendimento(pergunta) === state.perguntaSelecionadaKey);
    if (!perguntaSelecionada) {
        perguntaSelecionada = pagina[0];
        state.perguntaSelecionadaKey = chavePerguntaAtendimento(perguntaSelecionada);
    }
    perguntasList.innerHTML = pagina
        .map((pergunta) => renderizarLinhaPerguntaAtendimento(pergunta, chavePerguntaAtendimento(pergunta) === state.perguntaSelecionadaKey))
        .join('');
    perguntasList.querySelectorAll('[data-question-select]').forEach((button) => {
        button.addEventListener('click', () => {
            state.perguntaSelecionadaKey = button.dataset.questionSelect || '';
            renderizarPerguntas();
        });
    });
    renderizarDetalhePerguntaAtendimento(perguntaSelecionada);
    configurarAcoesRespostaPerguntas();
    renderizarPaginacaoPerguntas(state.totalPerguntas || perguntas.length);
    return;

    perguntasList.innerHTML = pagina.map((pergunta) => {
        const lojaOrigem = lojaOrigemItem(pergunta);
        const lojaBadge = lojaOrigem
            ? `<span class="badge ok">Loja ${escapeHtml(lojaOrigem)}</span>`
            : '';
        const titulo = pergunta.item_title || pergunta.item_id || 'Anúncio';
        const tituloHtml = pergunta.item_permalink
            ? `<a href="${escapeHtml(pergunta.item_permalink)}" target="_blank" rel="noopener">${escapeHtml(titulo)}</a>`
            : escapeHtml(titulo);
        const foto = pergunta.item_thumbnail
            ? `<img src="${escapeHtml(pergunta.item_thumbnail)}" alt="${escapeHtml(titulo)}" loading="lazy" referrerpolicy="no-referrer">`
            : '<span>Sem foto</span>';
        const sku = pergunta.item_sku || '-';
        const comprador = pergunta.buyer_name || pergunta.buyer_nickname || pergunta.from_id || '-';
        const answer = pergunta.answer && pergunta.answer.text
            ? `<div class="answer-box">${escapeHtml(pergunta.answer.text)}</div>`
            : '<div class="answer-box empty">Sem resposta registrada.</div>';
        const chatPerguntas = renderizarChatPerguntas(pergunta);
        const podeResponder = !(pergunta.answer && pergunta.answer.text) && String(pergunta.status || '').toUpperCase() === 'UNANSWERED';
        const composer = podeResponder ? `
            <div class="question-answer-composer">
                <textarea class="question-answer-text" maxlength="2000" placeholder="Digite a resposta manualmente ou gere uma sugestao com IA para editar antes de enviar."></textarea>
                <div class="question-answer-actions">
                    <button class="action-btn secondary question-ai-answer-btn" type="button" data-action="gerar-ia">Gerar resposta com IA</button>
                    <button class="action-btn question-send-answer-btn" type="button" data-action="enviar-resposta" disabled>Enviar resposta</button>
                    <span class="question-answer-status">Limite do Mercado Livre: 2000 caracteres.</span>
                </div>
            </div>
        ` : '';
        return `
            <article class="question-card" data-question-card data-question-id="${escapeHtml(pergunta.id || '')}" data-question-loja="${escapeHtml(lojaOrigem)}">
                <div class="question-body">
                    <div class="question-thumb">${foto}</div>
                    <div>
                        <div class="question-top">
                            <div>
                                <h3 class="question-title">${tituloHtml}</h3>
                                ${lojaBadge}
                                <span class="badge ${classeStatus(pergunta.status)}">${escapeHtml(rotuloStatus(pergunta.status))}</span>
                                <div class="question-meta">
                                    <span>Anúncio ${escapeHtml(pergunta.item_id || '-')}</span>
                                    <span>SKU ${escapeHtml(sku)}</span>
                                    <span>Comprador ${escapeHtml(comprador)}</span>
                                    <span>Pergunta ${escapeHtml(pergunta.id || '-')}</span>
                                </div>
                            </div>
                            <span class="question-date">${escapeHtml(formatarData(pergunta.date_created))}</span>
                        </div>
                        <p class="question-text">${escapeHtml(pergunta.text || '-')}</p>
                        ${chatPerguntas || answer}
                        ${composer}
                    </div>
                </div>
            </article>
        `;
    }).join('');
    configurarAcoesRespostaPerguntas();
    renderizarPaginacaoPerguntas(state.totalPerguntas || perguntas.length);
}

function obterPerguntaPorId(questionId, loja = '') {
    const id = String(questionId || '').trim();
    const lojaFiltro = String(loja || '').trim();
    return state.perguntas.find((pergunta) => {
        const mesmaPergunta = String(pergunta.id || '').trim() === id;
        if (!mesmaPergunta) return false;
        if (!lojaFiltro) return true;
        return lojaOrigemItem(pergunta) === lojaFiltro;
    }) || null;
}

function setStatusRespostaPergunta(statusEl, texto, tipo = '') {
    if (!statusEl) return;
    statusEl.textContent = texto || '';
    statusEl.classList.toggle('error', tipo === 'error');
    statusEl.classList.toggle('ok', tipo === 'ok');
}

function ajustarAlturaTextareaAtendimento(textarea) {
    if (!textarea) return;
    textarea.style.height = 'auto';
    textarea.style.height = `${Math.max(textarea.scrollHeight + 2, textarea.classList.contains('question-answer-text') ? 96 : 84)}px`;
}

function normalizarSugestaoResposta(valor) {
    return String(valor ?? '').trim();
}

function respostaSugeridaPayload(payload) {
    return normalizarSugestaoResposta(
        payload?.resposta_sugerida
        || payload?.resposta
        || payload?.texto
        || payload?.answer
        || ''
    );
}

function lojaPayloadAtendimento(payload) {
    return normalizarSugestaoResposta(payload?.loja || payload?.conta || payload?.store || '');
}

function questionIdPayloadAtendimento(payload) {
    return normalizarSugestaoResposta(payload?.question_id || payload?.pergunta_id || payload?.id_pergunta || '');
}

function valoresIguaisAtendimento(a, b) {
    return normalizarSugestaoResposta(a).toLowerCase() === normalizarSugestaoResposta(b).toLowerCase();
}

function perguntaCombinaSugestao(pergunta, payload) {
    if (!pergunta || !payload) return false;
    const questionId = questionIdPayloadAtendimento(payload);
    if (questionId && normalizarSugestaoResposta(pergunta.id) !== questionId) return false;
    const loja = lojaPayloadAtendimento(payload);
    if (loja) {
        const lojaPergunta = lojaOrigemItem(pergunta);
        if (lojaPergunta && !valoresIguaisAtendimento(lojaPergunta, loja)) return false;
    }
    if (questionId) return true;

    const sku = normalizarSugestaoResposta(payload.sku || payload.item_sku || payload.seller_sku || '');
    const itemId = normalizarSugestaoResposta(payload.item_id || payload.anuncio || '');
    if (sku && !valoresIguaisAtendimento(pergunta.item_sku, sku)) return false;
    if (itemId && !valoresIguaisAtendimento(pergunta.item_id, itemId)) return false;
    return Boolean(sku || itemId);
}

function encontrarPerguntaDaSugestao(payload) {
    return (state.perguntas || []).find((pergunta) => perguntaCombinaSugestao(pergunta, payload)) || null;
}

function cardPerguntaCombinaSugestao(card, payload) {
    if (!card || !payload) return false;
    const questionId = questionIdPayloadAtendimento(payload);
    if (questionId && normalizarSugestaoResposta(card.dataset.questionId) !== questionId) return false;
    const loja = lojaPayloadAtendimento(payload);
    if (loja) {
        const lojaCard = normalizarSugestaoResposta(card.dataset.questionLoja || '');
        if (lojaCard && !valoresIguaisAtendimento(lojaCard, loja)) return false;
    }
    return Boolean(questionId || loja);
}

function encontrarCardPerguntaSugestao(payload) {
    const cards = Array.from(document.querySelectorAll('[data-question-card]'));
    return cards.find((card) => cardPerguntaCombinaSugestao(card, payload)) || null;
}

function preencherTextareaPerguntaComSugestao(card, resposta, opcoes = {}) {
    if (!card) return { ok: false, message: 'Pergunta nao encontrada na tela.' };
    const textarea = card.querySelector('.question-answer-text');
    if (!textarea) return { ok: false, message: 'A pergunta nao esta aberta para resposta.' };
    const status = card.querySelector('.question-answer-composer .question-answer-status');
    const jaTemTexto = Boolean(normalizarSugestaoResposta(textarea.value));
    if (jaTemTexto && opcoes.force !== true) {
        setStatusRespostaPergunta(status, 'Sugestao da IA disponivel; o campo ja tinha texto.', 'ok');
        return { ok: false, skipped: true, message: 'O campo de resposta ja tinha texto.' };
    }
    textarea.value = resposta;
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
    ajustarAlturaTextareaAtendimento(textarea);
    if (opcoes.focus !== false) {
        textarea.focus();
        textarea.setSelectionRange(textarea.value.length, textarea.value.length);
    }
    setStatusRespostaPergunta(status, 'Sugestao da IA copiada para a resposta. Revise antes de enviar.', 'ok');
    return { ok: true, message: 'Resposta copiada para a caixa de texto.' };
}

function preencherRespostaPerguntaSugerida(payload, opcoes = {}) {
    const resposta = respostaSugeridaPayload(payload);
    if (!resposta) return { ok: false, message: 'A sugestao nao trouxe texto de resposta.' };

    let card = encontrarCardPerguntaSugestao(payload);
    if (!card) {
        const pergunta = encontrarPerguntaDaSugestao(payload);
        if (pergunta) {
            state.perguntaSelecionadaKey = chavePerguntaAtendimento(pergunta);
            renderizarPerguntas();
            card = encontrarCardPerguntaSugestao(payload) || perguntasDetail?.querySelector('[data-question-card]');
        }
    }
    if (!card && opcoes.allowFallback !== false && !questionIdPayloadAtendimento(payload)) {
        card = perguntasDetail?.querySelector('[data-question-card]') || document.querySelector('[data-question-card]');
    }
    return preencherTextareaPerguntaComSugestao(card, resposta, opcoes);
}

function registrarIntegracaoSidebarPerguntas() {
    window.JKPerguntasPosVenda = window.JKPerguntasPosVenda || {};
    window.JKPerguntasPosVenda.preencherRespostaPergunta = preencherRespostaPerguntaSugerida;
    window.JKPerguntasPosVenda.preencherRespostaSugerida = function preencherRespostaSugeridaAtendimento(payload, opcoes = {}) {
        const tipo = normalizarSugestaoResposta(payload?.tipo || payload?.approval_type || '').toLowerCase();
        if (tipo === 'pos_venda' && typeof window.JKPerguntasPosVenda.preencherRespostaPosVenda === 'function') {
            return window.JKPerguntasPosVenda.preencherRespostaPosVenda(payload, opcoes);
        }
        return preencherRespostaPerguntaSugerida(payload, opcoes);
    };
    window.addEventListener('jk:perguntas-pos-venda:usar-resposta', (event) => {
        const payload = event.detail || {};
        const tipo = normalizarSugestaoResposta(payload?.tipo || payload?.approval_type || '').toLowerCase();
        const opcoes = {
            force: payload.force === true,
            focus: payload.focus !== false,
            allowFallback: payload.allowFallback !== false
        };
        if (tipo === 'pos_venda' && typeof window.JKPerguntasPosVenda.preencherRespostaPosVenda === 'function') {
            window.JKPerguntasPosVenda.preencherRespostaPosVenda(payload, opcoes);
            return;
        }
        preencherRespostaPerguntaSugerida(payload, opcoes);
    });
}

registrarIntegracaoSidebarPerguntas();

function configurarAcoesRespostaPerguntas() {
    [perguntasList, perguntasDetail].filter(Boolean).forEach((container) => container.querySelectorAll('[data-question-card]').forEach((card) => {
        const questionId = card.dataset.questionId || '';
        const questionLoja = card.dataset.questionLoja || '';
        const textarea = card.querySelector('.question-answer-text');
        const btnGerar = card.querySelector('.question-ai-answer-btn');
        const btnEnviar = card.querySelector('.question-send-answer-btn');
        const btnEnviarSalvar = card.querySelector('.question-send-save-example-btn');
        const btnSalvarExemplo = card.querySelector('.question-save-example-btn');
        const checkboxSalvarExemplo = card.querySelector('.question-save-example-checkbox');
        const textareaSku = card.querySelector('.question-sku-guidance-text');
        const btnSalvarSku = card.querySelector('.question-sku-guidance-save');
        const statusSku = card.querySelector('.question-sku-guidance-status');
        const status = card.querySelector('.question-answer-composer .question-answer-status');
        if (textareaSku && btnSalvarSku) {
            ajustarAlturaTextareaAtendimento(textareaSku);
            textareaSku.addEventListener('input', () => ajustarAlturaTextareaAtendimento(textareaSku));
            btnSalvarSku.addEventListener('click', () => salvarOrientacaoSkuPergunta(questionId, questionLoja, textareaSku, btnSalvarSku, statusSku));
        }
        if (!textarea || !btnEnviar) return;

        const atualizarBotaoEnviar = () => {
            const vazio = !textarea.value.trim();
            btnEnviar.disabled = vazio;
            if (btnEnviarSalvar) btnEnviarSalvar.disabled = vazio;
            if (btnSalvarExemplo) btnSalvarExemplo.disabled = vazio;
            ajustarAlturaTextareaAtendimento(textarea);
        };
        textarea.addEventListener('input', atualizarBotaoEnviar);
        btnEnviar.addEventListener('click', () => enviarRespostaPerguntaManual(questionId, questionLoja, textarea, btnEnviar, btnGerar, status));
        if (btnEnviarSalvar) {
            btnEnviarSalvar.addEventListener('click', () => enviarRespostaPerguntaManual(questionId, questionLoja, textarea, btnEnviarSalvar, btnGerar, status, { salvarExemplo: true, botoesExtras: [btnEnviar, btnSalvarExemplo] }));
        }
        if (btnSalvarExemplo) {
            btnSalvarExemplo.addEventListener('click', () => salvarExemploRespostaPergunta(questionId, questionLoja, textarea, btnSalvarExemplo, status));
        }
        if (checkboxSalvarExemplo && btnEnviarSalvar) {
            checkboxSalvarExemplo.addEventListener('change', () => {
                btnEnviarSalvar.classList.toggle('hidden', !checkboxSalvarExemplo.checked);
            });
        }
        if (btnGerar) {
            btnGerar.addEventListener('click', () => gerarRespostaPerguntaIa(questionId, questionLoja, textarea, btnEnviar, btnGerar, status));
        }
        atualizarBotaoEnviar();
    }));
}

function montarExemploRespostaPergunta(pergunta, resposta) {
    return {
        pergunta: String(pergunta && pergunta.text || '').trim(),
        resposta: String(resposta || '').trim(),
        sku: String(pergunta && pergunta.item_sku || '').trim(),
        observacao: 'Modelo salvo a partir da tela de perguntas',
        updated_at: new Date().toISOString()
    };
}

async function salvarTreinamentoAtendimentoPergunta(pergunta, opcoes = {}) {
    await carregarTreinamentoAI().catch(() => {});
    const tipo = 'perguntas_anuncio';
    const sku = String(pergunta && pergunta.item_sku || '').trim();
    const exemplos = normalizarExemplosTreinamento((state.treinamentoDados[tipo] || {}).exemplos || []);
    if (opcoes.exemplo) {
        exemplos.unshift(opcoes.exemplo);
    }
    state.treinamentoDados[tipo] = {
        ...(state.treinamentoDados[tipo] || {}),
        exemplos: normalizarExemplosTreinamento(exemplos).slice(0, 60)
    };
    if (sku && Object.prototype.hasOwnProperty.call(opcoes, 'notasSku')) {
        const textoNotas = String(opcoes.notasSku || '').trim();
        if (textoNotas) {
            state.treinamentoContexto.notas_sku[sku] = textoNotas;
        } else {
            delete state.treinamentoContexto.notas_sku[sku];
        }
    }
    const payload = {
        tipo,
        loja: lojaEscopoTreinamento(),
        orientacoes: (state.treinamentoDados[tipo] || {}).orientacoes || '',
        contexto_loja: state.treinamentoContexto.contexto_loja || '',
        compatibilidade_autopecas: state.treinamentoContexto.compatibilidade_autopecas || '',
        proibicoes: state.treinamentoContexto.proibicoes || '',
        exemplos: normalizarExemplosTreinamento((state.treinamentoDados[tipo] || {}).exemplos || [])
    };
    if (sku && Object.prototype.hasOwnProperty.call(opcoes, 'notasSku')) {
        payload.sku = sku;
        payload.notas_sku = String(opcoes.notasSku || '').trim();
    }
    const response = await fetch('/api/mercadolivre/ia-treinamento', {
        method: 'POST',
        headers: {
            ...obterAuthHeaders(),
            'Content-Type': 'application/json'
        },
        body: JSON.stringify(payload)
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || 'Erro ao salvar treinamento da IA.');
    state.treinamentoCarregado = true;
    state.treinamentoDados[tipo] = {
        orientacoes: (state.treinamentoDados[tipo] || {}).orientacoes || '',
        updated_at: data.updated_at || new Date().toISOString(),
        exemplos: normalizarExemplosTreinamento(((data.exemplos || {})[tipo]) || (state.treinamentoDados[tipo] || {}).exemplos || [])
    };
    state.treinamentoContexto = {
        contexto_loja: typeof data.contexto_loja === 'string' ? data.contexto_loja : (state.treinamentoContexto.contexto_loja || ''),
        compatibilidade_autopecas: typeof data.compatibilidade_autopecas === 'string' ? data.compatibilidade_autopecas : (state.treinamentoContexto.compatibilidade_autopecas || ''),
        proibicoes: typeof data.proibicoes === 'string' ? data.proibicoes : (state.treinamentoContexto.proibicoes || ''),
        notas_sku: normalizarNotasTreinamento(data.notas_sku || state.treinamentoContexto.notas_sku)
    };
    renderizarNotasSkuTreinamento();
    renderizarExemplosTreinamento();
    atualizarStatusTreinamentoTipo();
    return data;
}

async function salvarOrientacaoSkuPergunta(questionId, loja, textarea, botao, status) {
    const pergunta = obterPerguntaPorId(questionId, loja);
    const sku = String(pergunta && pergunta.item_sku || '').trim();
    if (!pergunta || !sku) return;
    botao.disabled = true;
    setStatusRespostaPergunta(status, 'Salvando orientacao do SKU...');
    try {
        await salvarTreinamentoAtendimentoPergunta(pergunta, { notasSku: textarea.value || '' });
        setStatusRespostaPergunta(status, 'Orientacao salva.', 'ok');
    } catch (error) {
        setStatusRespostaPergunta(status, `Erro ao salvar: ${mensagemErro(error)}`, 'error');
    } finally {
        botao.disabled = false;
    }
}

async function salvarExemploRespostaPergunta(questionId, loja, textarea, botao, status) {
    const pergunta = obterPerguntaPorId(questionId, loja);
    const texto = String(textarea.value || '').trim();
    if (!pergunta || !texto) return;
    botao.disabled = true;
    setStatusRespostaPergunta(status, 'Salvando resposta como exemplo da IA...');
    try {
        await salvarTreinamentoAtendimentoPergunta(pergunta, {
            exemplo: montarExemploRespostaPergunta(pergunta, texto)
        });
        setStatusRespostaPergunta(status, 'Exemplo salvo no treinamento da IA.', 'ok');
    } catch (error) {
        setStatusRespostaPergunta(status, `Erro ao salvar exemplo: ${mensagemErro(error)}`, 'error');
    } finally {
        botao.disabled = !textarea.value.trim();
    }
}

async function gerarRespostaPerguntaIa(questionId, loja, textarea, btnEnviar, btnGerar, status) {
    const pergunta = obterPerguntaPorId(questionId, loja);
    const lojaResposta = lojaOrigemItem(pergunta) || (todasAsLojasSelecionadas() ? '' : state.lojaSelecionada);
    if (!pergunta || !lojaResposta) return;
    btnGerar.disabled = true;
    btnEnviar.disabled = true;
    setStatusRespostaPergunta(status, 'Gerando sugestao com IA...');
    try {
        const response = await fetch('/api/mercadolivre/perguntas/resposta/gerar', {
            method: 'POST',
            headers: {
                ...obterAuthHeaders(),
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                loja: lojaResposta,
                pergunta,
                resposta_atual: String(textarea.value || '').trim()
            })
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || 'Erro ao gerar resposta com IA.');
        textarea.value = data.resposta || '';
        textarea.dispatchEvent(new Event('input'));
        setStatusRespostaPergunta(status, 'Sugestao gerada. Revise e edite antes de enviar.', 'ok');
        textarea.focus();
    } catch (error) {
        setStatusRespostaPergunta(status, `Erro ao gerar IA: ${mensagemErro(error)}`, 'error');
    } finally {
        btnGerar.disabled = false;
        btnEnviar.disabled = !textarea.value.trim();
    }
}

async function enviarRespostaPerguntaManual(questionId, loja, textarea, btnEnviar, btnGerar, status, opcoes = {}) {
    const pergunta = obterPerguntaPorId(questionId, loja);
    const texto = String(textarea.value || '').trim();
    const lojaResposta = lojaOrigemItem(pergunta) || (todasAsLojasSelecionadas() ? '' : state.lojaSelecionada);
    if (!pergunta || !lojaResposta || !texto) return;
    const skuResposta = String((pergunta.item_sku || pergunta.sku || pergunta.seller_sku || '')).trim();
    const itemIdResposta = String((pergunta.item_id || '')).trim();
    const botoesExtras = Array.isArray(opcoes.botoesExtras) ? opcoes.botoesExtras.filter(Boolean) : [];
    btnEnviar.disabled = true;
    botoesExtras.forEach((botao) => { botao.disabled = true; });
    if (btnGerar) btnGerar.disabled = true;
    setStatusRespostaPergunta(status, opcoes.salvarExemplo ? 'Enviando resposta e salvando exemplo da IA...' : 'Enviando resposta ao Mercado Livre...');
    try {
        const response = await fetch('/api/mercadolivre/perguntas/responder', {
            method: 'POST',
            headers: {
                ...obterAuthHeaders(),
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                loja: lojaResposta,
                question_id: questionId,
                resposta: texto,
                pergunta,
                sku: skuResposta,
                item_id: itemIdResposta
            })
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(mensagemErroApi(data, 'Erro ao enviar resposta.'));
        pergunta.answer = {
            text: data.resposta || texto,
            status: 'ANSWERED',
            date_created: new Date().toISOString()
        };
        pergunta.status = 'ANSWERED';
        if (opcoes.salvarExemplo) {
            await salvarTreinamentoAtendimentoPergunta(pergunta, {
                exemplo: montarExemploRespostaPergunta(pergunta, texto)
            });
        }
        perguntasStatus.textContent = opcoes.salvarExemplo
            ? `Resposta enviada ao Mercado Livre pela loja ${lojaResposta} e salva como exemplo da IA.`
            : `Resposta enviada ao Mercado Livre pela loja ${lojaResposta}.`;
        renderizarPerguntas();
        carregarContadoresNotificacoes(true);
    } catch (error) {
        setStatusRespostaPergunta(status, `Erro ao enviar: ${mensagemErro(error)}`, 'error');
        btnEnviar.disabled = !textarea.value.trim();
        botoesExtras.forEach((botao) => { botao.disabled = !textarea.value.trim(); });
        if (btnGerar) btnGerar.disabled = false;
    }
}

async function carregarLojas() {
    lojasStatus.textContent = 'Carregando lojas...';
    try {
        const response = await fetch('/api/mercadolivre/perguntas/lojas', {
            headers: obterAuthHeaders(),
            cache: 'no-store'
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || 'Erro ao carregar lojas.');
        state.lojas = Array.isArray(data.lojas) ? data.lojas : [];
        const haLojasConectadas = lojasMercadoLivreConectadas().length > 0;
        const lojaSelecionadaAtual = state.lojas.find((loja) => String(loja.nome || '') === String(state.lojaSelecionada || ''));
        if (!haLojasConectadas) {
            state.lojaSelecionada = '';
            state.lojaConfiguracaoPerguntas = '';
        } else if (
            !state.lojaSelecionada ||
            todasAsLojasSelecionadas() ||
            !lojaSelecionadaAtual ||
            lojaSelecionadaAtual.mercadolivre_conectado !== true
        ) {
            state.lojaSelecionada = TODAS_LOJAS_VALUE;
        }
        if (todasAsLojasSelecionadas()) {
            state.lojaConfiguracaoPerguntas = TODAS_LOJAS_VALUE;
        } else if (!state.lojaConfiguracaoPerguntas) {
            state.lojaConfiguracaoPerguntas = state.lojaSelecionada;
        }
        lojasStatus.textContent = state.lojas.length
            ? `${state.lojas.length} loja(s) cadastrada(s)`
            : 'Nenhuma loja cadastrada';
        renderizarLojas();
        atualizarCabecalhoPosVenda();
        atualizarCabecalhoMediacao();
        iniciarAutomacaoPerguntas();
        if (state.lojaSelecionada) await carregarPerguntas();
        carregarContadoresNotificacoes(true);
    } catch (error) {
        lojasStatus.textContent = 'Erro ao carregar lojas.';
        lojasGrid.innerHTML = `<div class="empty-state"><div><h2>Falha ao carregar lojas</h2><p>${escapeHtml(mensagemErro(error))}</p></div></div>`;
    }
}

async function selecionarLoja(nome) {
    if (!nome || state.carregandoPerguntas) return;
    state.lojaSelecionada = nome;
    state.lojaConfiguracaoPerguntas = todasAsLojasSelecionadas() ? TODAS_LOJAS_VALUE : nome;
    resetarPaginacaoPosVenda();
    resetarMediacoes();
    renderizarLojas();
    atualizarCabecalhoPosVenda();
    atualizarCabecalhoMediacao();
    if (document.getElementById('aba-pos-venda').classList.contains('active')) {
        await carregarPosVenda();
    } else if (document.getElementById('aba-mediacao').classList.contains('active')) {
        if (todasAsLojasSelecionadas()) {
            mediacaoStatus.textContent = 'Selecione uma loja especifica para consultar mediacoes.';
            return;
        }
        await carregarMediacoes(true);
    } else {
        await carregarPerguntas();
    }
}

async function carregarPerguntasTodasLojas() {
    const lojas = lojasMercadoLivreConectadas();
    if (!lojas.length) {
        return {
            questions: [],
            total: 0,
            retornadas: 0,
            status_resumo: {},
            lojas_consultadas: 0,
            erros: []
        };
    }
    const limitePorLoja = Math.min(
        100,
        Math.max(state.tamanhoPaginaPerguntas, state.paginaPerguntas * state.tamanhoPaginaPerguntas)
    );
    const resultados = await Promise.all(lojas.map(async (loja) => {
        const nomeLoja = String(loja.nome || '').trim();
        try {
            const params = new URLSearchParams({
                loja: nomeLoja,
                carregar_todas: 'false',
                offset: '0',
                limit: String(limitePorLoja)
            });
            if (statusFiltro.value) params.set('status', statusFiltro.value);
            const response = await fetch(`/api/mercadolivre/perguntas?${params.toString()}`, {
                headers: obterAuthHeaders(),
                cache: 'no-store'
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(data.detail || `Erro ao carregar perguntas de ${nomeLoja}.`);
            return { loja: nomeLoja, data };
        } catch (error) {
            return { loja: nomeLoja, error };
        }
    }));
    const sucessos = resultados.filter((resultado) => resultado.data);
    const erros = resultados
        .filter((resultado) => resultado.error)
        .map((resultado) => ({ loja: resultado.loja, erro: mensagemErro(resultado.error) }));
    const todasPerguntas = ordenarPerguntasRecentes(sucessos.flatMap((resultado) => {
        const perguntas = Array.isArray(resultado.data.questions) ? resultado.data.questions : [];
        return perguntas.map((pergunta) => anexarLojaOrigem(pergunta, resultado.loja));
    }));
    const inicio = (state.paginaPerguntas - 1) * state.tamanhoPaginaPerguntas;
    const fim = inicio + state.tamanhoPaginaPerguntas;
    const pagina = todasPerguntas.slice(inicio, fim);
    const hasNext = sucessos.some((resultado) => (
        resultado.data &&
        resultado.data.next_offset !== null &&
        resultado.data.next_offset !== undefined
    ));
    return {
        questions: pagina,
        total_carregado: todasPerguntas.length,
        total_paginacao: Math.max(todasPerguntas.length, fim + (hasNext ? 1 : 0)),
        total: sucessos.reduce((acc, resultado) => acc + Number(resultado.data.total || 0), 0),
        retornadas: todasPerguntas.length,
        status_resumo: somarStatusResumoPerguntas(sucessos),
        lojas_consultadas: sucessos.length,
        lojas_total: lojas.length,
        erros,
        has_next: hasNext,
        interrompido: sucessos.some((resultado) => resultado.data && resultado.data.interrompido),
        modo_todas: true
    };
}

async function carregarPerguntas(pagina = 1) {
    if (!state.lojaSelecionada) return;
    state.carregandoPerguntas = true;
    btnRecarregar.disabled = true;
    perguntasSummary.classList.add('hidden');
    perguntasList.innerHTML = '';
    perguntasPagination.classList.add('hidden');
    perguntasPagination.innerHTML = '';
    state.perguntas = [];
    state.totalPerguntas = 0;
    state.paginaPerguntas = Math.max(1, Number(pagina) || 1);
    perguntasStatus.textContent = todasAsLojasSelecionadas()
        ? `Carregando ${lojasMercadoLivreConectadas().length} conta(s)...`
        : `Carregando perguntas de ${state.lojaSelecionada}...`;

    try {
        if (todasAsLojasSelecionadas()) {
            const dataTodas = await carregarPerguntasTodasLojas();
            const perguntasTodas = ordenarPerguntasRecentes(Array.isArray(dataTodas.questions) ? dataTodas.questions : []);
            state.perguntas = perguntasTodas;
            state.totalPerguntas = Number(dataTodas.total_paginacao || dataTodas.total_carregado || perguntasTodas.length || 0);
            perguntasStatus.textContent = '';
            renderizarResumo(dataTodas);
            renderizarPerguntas();
            return;
        }
        const offset = (state.paginaPerguntas - 1) * state.tamanhoPaginaPerguntas;
        const params = new URLSearchParams({
            loja: state.lojaSelecionada,
            carregar_todas: 'false',
            offset: String(offset),
            limit: String(state.tamanhoPaginaPerguntas)
        });
        if (statusFiltro.value) params.set('status', statusFiltro.value);
        const response = await fetch(`/api/mercadolivre/perguntas?${params.toString()}`, {
            headers: obterAuthHeaders(),
            cache: 'no-store'
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || 'Erro ao carregar perguntas.');
        const perguntas = ordenarPerguntasRecentes(Array.isArray(data.questions) ? data.questions : []);
        state.perguntas = perguntas;
        state.totalPerguntas = Number(data.total || perguntas.length || 0);
        perguntasStatus.textContent = data.interrompido ? 'Busca limitada pelo ML.' : '';
        renderizarResumo(data);
        renderizarPerguntas();
    } catch (error) {
        perguntasStatus.textContent = `Erro ao carregar perguntas: ${mensagemErro(error)}`;
        perguntasList.innerHTML = '';
        perguntasPagination.classList.add('hidden');
        perguntasPagination.innerHTML = '';
    } finally {
        state.carregandoPerguntas = false;
        btnRecarregar.disabled = false;
    }
}
