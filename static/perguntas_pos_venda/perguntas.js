function formatarTempoDesdeAtualizacaoPerguntas(timestamp, agora = Date.now()) {
    const segundos = Math.max(0, Math.floor((agora - Number(timestamp || 0)) / 1000));
    if (segundos < 5) return 'Atualizado agora';
    if (segundos < 60) return `Atualizado há ${segundos} s`;

    const minutos = Math.floor(segundos / 60);
    if (minutos < 60) return `Atualizado há ${minutos} min`;

    const horas = Math.floor(minutos / 60);
    if (horas < 24) return `Atualizado há ${horas} h`;

    const dias = Math.floor(horas / 24);
    return `Atualizado há ${dias} ${dias === 1 ? 'dia' : 'dias'}`;
}

function normalizarTimestampAutomacaoPerguntas(valor) {
    if (valor === null || valor === undefined || valor === '') return 0;
    const numero = Number(valor);
    if (Number.isFinite(numero) && numero > 0) {
        const pareceEpochEmSegundos = numero >= 1000000000 && numero < 100000000000;
        return pareceEpochEmSegundos ? Math.round(numero * 1000) : Math.round(numero);
    }
    const timestamp = new Date(valor).getTime();
    return Number.isFinite(timestamp) && timestamp > 0 ? timestamp : 0;
}

function atualizarIndicadorUltimaAtualizacaoPerguntas() {
    if (!perguntasUltimaAtualizacao) return;
    const checagemEm = normalizarTimestampAutomacaoPerguntas(state.ultimaChecagemAutomacaoPerguntasEm);
    const atualizacaoEm = normalizarTimestampAutomacaoPerguntas(state.ultimaAtualizacaoPerguntasEm);
    const timestamp = atualizacaoEm || checagemEm;
    if (!timestamp) return;
    const atualizadoEm = new Date(timestamp);
    const tempo = formatarTempoDesdeAtualizacaoPerguntas(timestamp);
    perguntasUltimaAtualizacao.textContent = atualizacaoEm
        ? `Lista ${tempo.replace(/^Atualizado/, 'atualizada')}` : 'Lista aguardando atualização';
    perguntasUltimaAtualizacao.dateTime = atualizadoEm.toISOString();
    perguntasUltimaAtualizacao.title = `Última atualização da lista: ${atualizadoEm.toLocaleString('pt-BR')}`;
    const checagem = document.getElementById('perguntas-ultima-checagem');
    if (!checagem) return;
    checagem.textContent = checagemEm ? `Última checagem automática: ${formatarTempoDesdeAtualizacaoPerguntas(checagemEm).replace(/^Atualizado /, '')}` : '';
    if (checagemEm) checagem.dateTime = new Date(checagemEm).toISOString();
}

function registrarUltimaAtualizacaoPerguntas(timestamp = Date.now()) {
    state.ultimaAtualizacaoPerguntasEm = timestamp;
    atualizarIndicadorUltimaAtualizacaoPerguntas();
    if (!state.ultimaAtualizacaoPerguntasTimer) {
        state.ultimaAtualizacaoPerguntasTimer = window.setInterval(
            atualizarIndicadorUltimaAtualizacaoPerguntas,
            1000
        );
    }
}

function registrarUltimaChecagemAutomacaoPerguntas(timestamp = Date.now()) {
    const normalizado = normalizarTimestampAutomacaoPerguntas(timestamp);
    if (!normalizado) return;
    state.ultimaChecagemAutomacaoPerguntasEm = Math.max(
        Number(state.ultimaChecagemAutomacaoPerguntasEm || 0),
        normalizado
    );
    atualizarIndicadorUltimaAtualizacaoPerguntas();
    if (!state.ultimaAtualizacaoPerguntasTimer) {
        state.ultimaAtualizacaoPerguntasTimer = window.setInterval(
            atualizarIndicadorUltimaAtualizacaoPerguntas,
            1000
        );
    }
}

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
    return `${pergunta?.store_id || lojaOrigemItem(pergunta) || ''}::${String(pergunta && pergunta.id || '').trim()}`;
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
        : `<span>${pergunta._itemReady ? 'Sem foto' : 'Carregando...'}</span>`;
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
                <button class="action-btn secondary question-ai-answer-btn" type="button" data-action="gerar-ia" ${pergunta._itemReady && pergunta._detailReady ? '' : 'disabled title="Aguardando dados do anúncio e histórico"'}>Gerar IA</button>
                <button class="action-btn secondary question-ai-cancel-btn hidden" type="button" data-action="cancelar-pesquisa">Cancelar pesquisa</button>
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
            window.JKPerguntasLoading?.salvarRascunho();
            state.perguntaSelecionadaKey = button.dataset.questionSelect || '';
            renderizarPerguntas();
            window.JKPerguntasLoading?.restaurarRascunho();
        });
    });
    renderizarDetalhePerguntaAtendimento(perguntaSelecionada);
    configurarAcoesRespostaPerguntas();
    window.JKPerguntasLoading?.detalhe(perguntaSelecionada);
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
            : `<span>${pergunta._itemReady ? 'Sem foto' : 'Carregando...'}</span>`;
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

function capturarInteracaoPerguntas() {
    const textarea = perguntasDetail && perguntasDetail.querySelector('.question-answer-text');
    const checkbox = perguntasDetail && perguntasDetail.querySelector('.question-save-example-checkbox');
    const ativo = document.activeElement;
    return {
        perguntaSelecionadaKey: state.perguntaSelecionadaKey,
        resposta: textarea ? textarea.value : null,
        checkboxMarcado: checkbox ? checkbox.checked : null,
        proposalId: textarea ? String(textarea.dataset.codexProposalId || '') : '',
        proposalVersion: textarea ? String(textarea.dataset.codexProposalVersion || '') : '',
        proposalHash: textarea ? String(textarea.dataset.codexProposalHash || '') : '',
        codexJobState: obterEstadoJobAtendimentoCodex(state.perguntaSelecionadaKey),
        selectionStart: textarea ? textarea.selectionStart : null,
        selectionEnd: textarea ? textarea.selectionEnd : null,
        selectionDirection: textarea ? textarea.selectionDirection : 'none',
        foco: ativo === textarea ? 'resposta' : (ativo === checkbox ? 'checkbox' : ''),
        textareaScrollTop: textarea ? textarea.scrollTop : 0,
        listaScrollTop: perguntasList ? perguntasList.scrollTop : 0,
        detalheScrollTop: perguntasDetail ? perguntasDetail.scrollTop : 0,
        paginaX: Number(window.scrollX || window.pageXOffset || 0),
        paginaY: Number(window.scrollY || window.pageYOffset || 0)
    };
}

function restaurarInteracaoPerguntas(snapshot) {
    if (!snapshot || snapshot.perguntaSelecionadaKey !== state.perguntaSelecionadaKey) return;
    const textarea = perguntasDetail && perguntasDetail.querySelector('.question-answer-text');
    const checkbox = perguntasDetail && perguntasDetail.querySelector('.question-save-example-checkbox');
    if (textarea && snapshot.resposta !== null) {
        textarea.value = snapshot.resposta;
        if (snapshot.proposalId) textarea.dataset.codexProposalId = snapshot.proposalId;
        if (snapshot.proposalVersion) textarea.dataset.codexProposalVersion = snapshot.proposalVersion;
        if (snapshot.proposalHash) textarea.dataset.codexProposalHash = snapshot.proposalHash;
        textarea.dispatchEvent(new Event('input', { bubbles: true }));
        ajustarAlturaTextareaAtendimento(textarea);
        textarea.scrollTop = snapshot.textareaScrollTop || 0;
    }
    if (checkbox && snapshot.checkboxMarcado !== null) {
        checkbox.checked = snapshot.checkboxMarcado;
        checkbox.dispatchEvent(new Event('change', { bubbles: true }));
    }
    if (snapshot.codexJobState && snapshot.perguntaSelecionadaKey) {
        salvarEstadoJobAtendimentoCodex(snapshot.perguntaSelecionadaKey, snapshot.codexJobState);
        aplicarEstadoJobAtendimentoCodex(snapshot.perguntaSelecionadaKey);
    }
    if (perguntasList) perguntasList.scrollTop = snapshot.listaScrollTop || 0;
    if (perguntasDetail) perguntasDetail.scrollTop = snapshot.detalheScrollTop || 0;
    const alvoFoco = snapshot.foco === 'resposta' ? textarea : (snapshot.foco === 'checkbox' ? checkbox : null);
    if (alvoFoco && typeof alvoFoco.focus === 'function') {
        try { alvoFoco.focus({ preventScroll: true }); } catch (_error) { alvoFoco.focus(); }
    }
    if (textarea && snapshot.foco === 'resposta' && Number.isInteger(snapshot.selectionStart)) {
        textarea.setSelectionRange(snapshot.selectionStart, snapshot.selectionEnd, snapshot.selectionDirection || 'none');
    }
    if (typeof window.scrollTo === 'function') window.scrollTo(snapshot.paginaX || 0, snapshot.paginaY || 0);
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
    const valor = payload?.resposta_sugerida
        ?? payload?.resposta
        ?? payload?.texto
        ?? payload?.answer
        ?? '';
    return String(valor);
}

function skuRealPergunta(pergunta) {
    return String(
        pergunta?.item_sku
        || pergunta?.seller_sku
        || pergunta?.sku
        || pergunta?.codigo
        || pergunta?.codigo_produto
        || ''
    ).trim();
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
    if (sku && !valoresIguaisAtendimento(skuRealPergunta(pergunta), sku)) return false;
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

function sugestaoEhPosVenda(payload) {
    if (!payload || typeof payload !== 'object') return false;
    return [payload.tipo, payload.approval_type, payload.origem, payload.ia_origem, payload.ia_finalidade]
        .some((valor) => {
            const marcador = normalizarSugestaoResposta(valor).toLowerCase().replace(/[-\s]+/g, '_');
            return marcador.includes('pos_venda');
        });
}

function registrarIntegracaoSidebarPerguntas() {
    window.JKPerguntasPosVenda = window.JKPerguntasPosVenda || {};
    window.JKPerguntasPosVenda.preencherRespostaPergunta = preencherRespostaPerguntaSugerida;
    window.JKPerguntasPosVenda.preencherRespostaSugerida = function preencherRespostaSugeridaAtendimento(payload, opcoes = {}) {
        if (sugestaoEhPosVenda(payload)) return { ok: false, blocked: true, message: 'Sugestoes de IA estao desativadas no pos-venda.' };
        return preencherRespostaPerguntaSugerida(payload, opcoes);
    };
    window.addEventListener('jk:perguntas-pos-venda:usar-resposta', (event) => {
        const payload = event.detail || {};
        const opcoes = {
            force: payload.force === true,
            focus: payload.focus !== false,
            allowFallback: payload.allowFallback !== false
        };
        if (sugestaoEhPosVenda(payload)) return;
        preencherRespostaPerguntaSugerida(payload, opcoes);
    });
}

registrarIntegracaoSidebarPerguntas();

function configurarAcoesRespostaPerguntas() {
    [perguntasList, perguntasDetail].filter(Boolean).forEach((container) => container.querySelectorAll('[data-question-card]').forEach((card) => {
        const questionId = card.dataset.questionId || '';
        const questionLoja = card.dataset.questionLoja || '';
        const questionKey = card.dataset.questionKey || `${questionLoja}::${questionId}`;
        const textarea = card.querySelector('.question-answer-text');
        const btnGerar = card.querySelector('.question-ai-answer-btn');
        const btnCancelarPesquisa = card.querySelector('.question-ai-cancel-btn');
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
            btnGerar.addEventListener('click', () => gerarRespostaPerguntaIa(
                questionId,
                questionLoja,
                textarea,
                btnEnviar,
                btnGerar,
                btnCancelarPesquisa,
                status
            ));
        }
        if (btnCancelarPesquisa) {
            btnCancelarPesquisa.addEventListener('click', () => cancelarPesquisaAtendimentoCodex(questionKey));
        }
        aplicarEstadoJobAtendimentoCodex(questionKey);
        const contexto = obterPerguntaPorId(questionId, questionLoja);
        if (btnGerar && (!contexto?._itemReady || !contexto?._detailReady)) btnGerar.disabled = true;
        const jobState = obterEstadoJobAtendimentoCodex(questionKey);
        if (jobState && jobState.polling_active && jobState.job_id) {
            garantirPollingJobAtendimentoCodex(questionKey).catch(() => {});
        }
        atualizarBotaoEnviar();
    }));
}

function montarExemploRespostaPergunta(pergunta, resposta) {
    return {
        pergunta: String(pergunta && pergunta.text || '').trim(),
        resposta: String(resposta || ''),
        sku: skuRealPergunta(pergunta),
        observacao: 'Modelo salvo a partir da tela de perguntas',
        updated_at: new Date().toISOString()
    };
}

async function salvarTreinamentoAtendimentoPergunta(pergunta, opcoes = {}) {
    const tipo = 'perguntas_anuncio';
    const lojaReal = String(lojaOrigemItem(pergunta) || '').trim();
    if (!lojaReal) {
        throw new Error('Não foi possível identificar a loja real desta pergunta para salvar o treinamento.');
    }
    const sku = skuRealPergunta(pergunta);
    if (!sku) {
        throw new Error('Não foi possível identificar o SKU real desta pergunta para salvar o treinamento.');
    }
    const params = new URLSearchParams({ loja: lojaReal });
    const responseAtual = await fetch(`/api/mercadolivre/ia-treinamento?${params.toString()}`, {
        headers: obterAuthHeaders(),
        cache: 'no-store'
    });
    const treinamentoAtual = await responseAtual.json().catch(() => ({}));
    if (!responseAtual.ok) throw new Error(treinamentoAtual.detail || 'Erro ao carregar o treinamento da loja da pergunta.');

    const exemplos = normalizarExemplosTreinamento(((treinamentoAtual.exemplos || {})[tipo]) || []);
    if (opcoes.exemplo) {
        exemplos.unshift({ ...opcoes.exemplo, sku });
    }
    const payload = {
        tipo,
        loja: lojaReal,
        orientacoes: treinamentoAtual.orientacoes_perguntas || treinamentoAtual.orientacoes || '',
        contexto_loja: treinamentoAtual.contexto_loja || '',
        compatibilidade_autopecas: treinamentoAtual.compatibilidade_autopecas || '',
        proibicoes: treinamentoAtual.proibicoes || '',
        exemplos: normalizarExemplosTreinamento(exemplos).slice(0, 60)
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
    if (String(lojaEscopoTreinamento() || '').trim() === lojaReal) {
        state.treinamentoCarregado = true;
        state.treinamentoDados[tipo] = {
            orientacoes: payload.orientacoes,
            updated_at: data.updated_at || new Date().toISOString(),
            exemplos: normalizarExemplosTreinamento(((data.exemplos || {})[tipo]) || payload.exemplos)
        };
        state.treinamentoContexto = {
            contexto_loja: typeof data.contexto_loja === 'string' ? data.contexto_loja : payload.contexto_loja,
            compatibilidade_autopecas: typeof data.compatibilidade_autopecas === 'string' ? data.compatibilidade_autopecas : payload.compatibilidade_autopecas,
            proibicoes: typeof data.proibicoes === 'string' ? data.proibicoes : payload.proibicoes,
            notas_sku: normalizarNotasTreinamento(data.notas_sku || treinamentoAtual.notas_sku)
        };
        atualizarIndicadorPerfilTreinamento(data);
        renderizarNotasSkuTreinamento();
        renderizarExemplosTreinamento();
        atualizarStatusTreinamentoTipo();
    }
    return data;
}

async function salvarOrientacaoSkuPergunta(questionId, loja, textarea, botao, status) {
    const pergunta = obterPerguntaPorId(questionId, loja);
    const sku = skuRealPergunta(pergunta);
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
    const texto = String(textarea.value || '');
    if (!pergunta || !texto.trim()) return;
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

const CODEX_JOB_STORAGE_PREFIX = 'jk_ppv_codex_job_v2:';

function tenantJobAtendimentoCodex() {
    try {
        if (typeof obterClientId === 'function') return String(obterClientId() || 'default').trim() || 'default';
    } catch (_error) {}
    return 'default';
}

function memoriaJobsAtendimentoCodex() {
    if (!state.codexJobsAtendimento || typeof state.codexJobsAtendimento !== 'object') state.codexJobsAtendimento = {};
    return state.codexJobsAtendimento;
}

function pollsJobsAtendimentoCodex() {
    if (!state.codexPollsAtendimento || typeof state.codexPollsAtendimento !== 'object') state.codexPollsAtendimento = {};
    return state.codexPollsAtendimento;
}

function storageKeyJobAtendimentoCodex(questionKey, tenantScope = tenantJobAtendimentoCodex()) {
    return `${CODEX_JOB_STORAGE_PREFIX}${encodeURIComponent(String(tenantScope || 'default'))}:${encodeURIComponent(String(questionKey || ''))}`;
}

function runtimeKeyJobAtendimentoCodex(questionKey, tenantScope = tenantJobAtendimentoCodex()) {
    return `${String(tenantScope || 'default')}::${String(questionKey || '')}`;
}

function obterEstadoJobAtendimentoCodex(questionKey, tenantScope = tenantJobAtendimentoCodex()) {
    const key = String(questionKey || '');
    if (!key) return null;
    const tenant = String(tenantScope || 'default');
    const memory = memoriaJobsAtendimentoCodex();
    const runtimeKey = runtimeKeyJobAtendimentoCodex(key, tenant);
    if (memory[runtimeKey] && memory[runtimeKey].tenant === tenant) return { ...memory[runtimeKey] };
    try {
        const parsed = JSON.parse(sessionStorage.getItem(storageKeyJobAtendimentoCodex(key, tenant)) || 'null');
        if (parsed && parsed.job_id && parsed.question_key === key && parsed.tenant === tenant) {
            memory[runtimeKey] = parsed;
            return { ...parsed };
        }
    } catch (_error) {}
    return null;
}

function salvarEstadoJobAtendimentoCodex(questionKey, value, tenantScope = tenantJobAtendimentoCodex()) {
    const key = String(questionKey || '');
    if (!key || !value || !value.job_id) return null;
    const tenant = String(tenantScope || 'default');
    const safe = {
        question_key: key,
        tenant,
        job_id: String(value.job_id || ''),
        status_message: String(value.status_message || 'Pesquisa em andamento.'),
        can_cancel: value.can_cancel !== false,
        polling_active: value.polling_active !== false,
        cancelled: value.cancelled === true
    };
    memoriaJobsAtendimentoCodex()[runtimeKeyJobAtendimentoCodex(key, tenant)] = safe;
    try { sessionStorage.setItem(storageKeyJobAtendimentoCodex(key, tenant), JSON.stringify(safe)); } catch (_error) {}
    return { ...safe };
}

function atualizarEstadoJobAtendimentoCodex(questionKey, patch, tenantScope = tenantJobAtendimentoCodex()) {
    const current = obterEstadoJobAtendimentoCodex(questionKey, tenantScope) || {};
    const saved = salvarEstadoJobAtendimentoCodex(questionKey, { ...current, ...(patch || {}) }, tenantScope);
    aplicarEstadoJobAtendimentoCodex(questionKey, tenantScope);
    return saved;
}

function limparEstadoJobAtendimentoCodex(questionKey, tenantScope = tenantJobAtendimentoCodex()) {
    const key = String(questionKey || '');
    delete memoriaJobsAtendimentoCodex()[runtimeKeyJobAtendimentoCodex(key, tenantScope)];
    try { sessionStorage.removeItem(storageKeyJobAtendimentoCodex(key, tenantScope)); } catch (_error) {}
    aplicarEstadoJobAtendimentoCodex(key, tenantScope);
}

function cardsAtuaisJobAtendimentoCodex(questionKey) {
    const cards = [];
    [perguntasDetail, perguntasList].filter(Boolean).forEach((container) => {
        container.querySelectorAll('[data-question-card]').forEach((card) => {
            if (String(card.dataset.questionKey || '') === String(questionKey || '') && !cards.includes(card)) cards.push(card);
        });
    });
    return cards;
}

function aplicarEstadoJobAtendimentoCodex(questionKey, tenantScope = tenantJobAtendimentoCodex()) {
    if (String(tenantScope || 'default') !== tenantJobAtendimentoCodex()) return;
    const jobState = obterEstadoJobAtendimentoCodex(questionKey, tenantScope);
    cardsAtuaisJobAtendimentoCodex(questionKey).forEach((card) => {
        const button = card.querySelector('.question-ai-cancel-btn');
        const generate = card.querySelector('.question-ai-answer-btn');
        const status = card.querySelector('.question-answer-composer .question-answer-status');
        if (jobState && jobState.polling_active) {
            if (button) {
                button.dataset.jobId = jobState.job_id;
                button.disabled = !jobState.can_cancel;
                button.classList.toggle('hidden', !jobState.can_cancel);
            }
            if (generate) generate.disabled = true;
            setStatusRespostaPergunta(status, jobState.status_message || 'Pesquisa em andamento.');
        } else {
            if (button) {
                button.classList.add('hidden');
                button.disabled = false;
                delete button.dataset.jobId;
            }
            if (generate) generate.disabled = false;
        }
    });
}

function aplicarResultadoJobAtendimentoCodex(questionKey, data, tenantScope = tenantJobAtendimentoCodex()) {
    if (String(tenantScope || 'default') !== tenantJobAtendimentoCodex()) return;
    const result = data && data.result && typeof data.result === 'object' ? data.result : (data || {});
    cardsAtuaisJobAtendimentoCodex(questionKey).forEach((card) => {
        const textarea = card.querySelector('.question-answer-text');
        const status = card.querySelector('.question-answer-composer .question-answer-status');
        if (!textarea) return;
        const resposta = String(result.resposta ?? data.resposta ?? '');
        if (!resposta.trim()) {
            textarea.value = '';
            delete textarea.dataset.codexProposalId;
            delete textarea.dataset.codexProposalVersion;
            delete textarea.dataset.codexProposalHash;
            textarea.dispatchEvent(new Event('input', { bubbles: true }));
            const avisoBloqueio = Array.isArray(data?.warnings) && data.warnings.length
                ? ` ${data.warnings[0]}`
                : '';
            setStatusRespostaPergunta(
                status,
                `Nao foi possivel carregar o rascunho.${avisoBloqueio}`,
                'error'
            );
            return;
        }
        textarea.value = resposta;
        textarea.dataset.codexProposalId = String(result.proposal_id || data.proposal_id || data.job_id || '');
        textarea.dataset.codexProposalVersion = String(result.proposal_version || data.proposal_version || 1);
        textarea.dataset.codexProposalHash = String(result.proposal_hash || data.proposal_hash || '');
        textarea.dispatchEvent(new Event('input', { bubbles: true }));
        const draftSource = String(result.draft_source || data?.draft_source || '').trim().toLowerCase();
        const parcial = (result.data_sufficient ?? data?.data_sufficient) === false
            && Boolean(result.completed_with_partial ?? data?.completed_with_partial);
        if (draftSource === 'contextual_fallback') {
            setStatusRespostaPergunta(
                status,
                'Rascunho de contingencia baseado no contexto. Revise antes de enviar.'
            );
            return;
        }
        if (draftSource === 'neutral_fallback') {
            setStatusRespostaPergunta(
                status,
                'Rascunho neutro de contingencia. Revise antes de enviar.'
            );
            return;
        }
        const mensagem = parcial
            ? 'Rascunho gerado com as informacoes disponiveis.'
            : 'Sugestao gerada pelo Black Jhon.';
        setStatusRespostaPergunta(status, mensagem, 'ok');
    });
}

function mensagemProgressoJobAtendimentoCodex(data) {
    const etapa = String(data.current_step || data.agent_state || 'consultar').replaceAll('_', ' ');
    const tentativa = Math.max(0, Number(data.attempt_count || 0));
    const ultimaAtividade = data.last_activity_at
        ? new Date(data.last_activity_at).toLocaleTimeString('pt-BR')
        : 'aguardando primeira atividade';
    let mensagem = String(data.status_message || 'Pesquisa em andamento.').trim();
    if (data.status === 'waiting_retry' && !/nova tentativa/i.test(mensagem)) {
        mensagem += ` Nova tentativa em ${Math.max(0, Number(data.next_retry_in_seconds || 0))}s.`;
    }
    const fila = data.status === 'queued'
        ? ` Fila: ${Math.max(0, Number(data.queue_position || 0))}/${Math.max(0, Number(data.queue_total || 0))}. Em execucao: ${Math.max(0, Number(data.running_total || 0))}.`
        : '';
    return `${mensagem}${fila} Etapa: ${etapa}. Tentativa: ${tentativa}. Ultima atividade: ${ultimaAtividade}.`;
}

async function aguardarJobAtendimentoCodex(jobId, atualizarStatus, cancelamentoLocal = () => false) {
    let falhasPolling = 0;
    let ultimoStatus = null;
    while (true) {
        if (cancelamentoLocal()) {
            const cancelError = new Error('A pesquisa foi cancelada.');
            cancelError.cancelled = true;
            throw cancelError;
        }
        try {
            const response = await fetch(`/api/mercadolivre/assistant/jobs/${encodeURIComponent(jobId)}`, {
                headers: obterAuthHeaders(),
                cache: 'no-store'
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                const error = new Error(mensagemErroApi(data, 'Falha temporaria ao acompanhar o agente Codex.'));
                error.pollingStatus = response.status;
                throw error;
            }
            falhasPolling = 0;
            ultimoStatus = data;
            if (typeof atualizarStatus === 'function') atualizarStatus(mensagemProgressoJobAtendimentoCodex(data), data);
            if (data.status === 'completed') return data;
            if (data.status === 'failed') throw new Error(data.error || 'O agente Codex nao conseguiu gerar a resposta.');
            if (data.status === 'cancelled') {
                const cancelError = new Error('A pesquisa foi cancelada.');
                cancelError.cancelled = true;
                throw cancelError;
            }
        } catch (error) {
            if (error && (error.cancelled || error.pollingStatus === 401 || error.pollingStatus === 403)) throw error;
            falhasPolling += 1;
            const ultimaAtividade = ultimoStatus?.last_activity_at
                ? new Date(ultimoStatus.last_activity_at).toLocaleTimeString('pt-BR')
                : 'ainda nao recebida';
            if (typeof atualizarStatus === 'function') {
                atualizarStatus(
                    `Falha temporaria ao atualizar o andamento (${falhasPolling}). `
                    + `A pesquisa continua no servidor. Ultima atividade: ${ultimaAtividade}. Reconectando...`
                );
            }
        }
        await new Promise((resolve) => setTimeout(resolve, Math.min(5000, 800 + (falhasPolling * 500))));
    }
}

async function garantirPollingJobAtendimentoCodex(questionKey) {
    const key = String(questionKey || '');
    const active = obterEstadoJobAtendimentoCodex(key);
    if (!active || !active.job_id) return null;
    const tenantScope = String(active.tenant || tenantJobAtendimentoCodex());
    const polls = pollsJobsAtendimentoCodex();
    const runtimeKey = runtimeKeyJobAtendimentoCodex(key, tenantScope);
    if (polls[runtimeKey]) return polls[runtimeKey];
    const polling = aguardarJobAtendimentoCodex(
        active.job_id,
        (message, data) => atualizarEstadoJobAtendimentoCodex(key, {
            job_id: active.job_id,
            status_message: message,
            can_cancel: data?.can_cancel !== false,
            polling_active: true
        }, tenantScope),
        () => obterEstadoJobAtendimentoCodex(key, tenantScope)?.cancelled === true
    ).then((data) => {
        aplicarResultadoJobAtendimentoCodex(key, data, tenantScope);
        limparEstadoJobAtendimentoCodex(key, tenantScope);
        return data;
    }).catch((error) => {
        if (error && error.cancelled) {
            cardsAtuaisJobAtendimentoCodex(key).forEach((card) => setStatusRespostaPergunta(
                card.querySelector('.question-answer-composer .question-answer-status'),
                'Pesquisa cancelada pelo usuario.'
            ));
        }
        limparEstadoJobAtendimentoCodex(key, tenantScope);
        throw error;
    }).finally(() => { delete polls[runtimeKey]; });
    polls[runtimeKey] = polling;
    return polling;
}

async function cancelarPesquisaAtendimentoCodex(questionKey) {
    const key = String(questionKey || '');
    const jobState = obterEstadoJobAtendimentoCodex(key);
    const jobId = String(jobState?.job_id || '').trim();
    if (!jobId || jobState.can_cancel === false) return null;
    atualizarEstadoJobAtendimentoCodex(key, { ...jobState, can_cancel: false, status_message: 'Cancelando pesquisa...' });
    try {
        const response = await fetch(`/api/mercadolivre/assistant/jobs/${encodeURIComponent(jobId)}/cancel`, {
            method: 'POST',
            headers: obterAuthHeaders()
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(mensagemErroApi(data, 'Erro ao cancelar a pesquisa.'));
        if (data.status === 'completed') {
            aplicarResultadoJobAtendimentoCodex(key, data);
            limparEstadoJobAtendimentoCodex(key);
            return data;
        }
        if (data.status === 'cancelled') {
            atualizarEstadoJobAtendimentoCodex(key, {
                ...jobState,
                cancelled: true,
                can_cancel: false,
                polling_active: true,
                status_message: data.status_message || 'Pesquisa cancelada.'
            });
            return data;
        }
        atualizarEstadoJobAtendimentoCodex(key, {
            ...jobState,
            can_cancel: data.can_cancel !== false,
            status_message: data.status_message || 'Pesquisa ainda em andamento.'
        });
        return data;
    } catch (error) {
        atualizarEstadoJobAtendimentoCodex(key, { ...jobState, can_cancel: true });
        cardsAtuaisJobAtendimentoCodex(key).forEach((card) => setStatusRespostaPergunta(
            card.querySelector('.question-answer-composer .question-answer-status'),
            `Erro ao cancelar: ${mensagemErro(error)}`,
            'error'
        ));
        throw error;
    }
}

window.aguardarJobAtendimentoCodex = aguardarJobAtendimentoCodex;
window.cancelarPesquisaAtendimentoCodex = cancelarPesquisaAtendimentoCodex;

async function gerarRespostaPerguntaIa(questionId, loja, textarea, btnEnviar, btnGerar, btnCancelarPesquisa, status) {
    const contexto = obterPerguntaPorId(questionId, loja);
    if (!contexto?._itemReady || !contexto?._detailReady) {
        setStatusRespostaPergunta(status, 'Aguarde os dados do anúncio e o histórico antes de gerar a resposta.');
        return;
    }
    const pergunta = obterPerguntaPorId(questionId, loja);
    const lojaResposta = lojaOrigemItem(pergunta) || (todasAsLojasSelecionadas() ? '' : state.lojaSelecionada);
    if (!pergunta || !lojaResposta) return;
    const questionKey = `${lojaResposta}::${String(questionId || '').trim()}`;
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
                resposta_atual: String(textarea.value || ''),
                async: true
            })
        });
        let data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || 'Erro ao gerar resposta com IA.');
        if (data.job_id && data.status !== 'completed') {
            salvarEstadoJobAtendimentoCodex(questionKey, {
                job_id: String(data.job_id),
                status_message: data.status_message || 'Pesquisa em andamento.',
                can_cancel: data.can_cancel !== false,
                polling_active: true,
                cancelled: false
            });
            aplicarEstadoJobAtendimentoCodex(questionKey);
            data = await garantirPollingJobAtendimentoCodex(questionKey);
        }
        aplicarResultadoJobAtendimentoCodex(questionKey, data);
        cardsAtuaisJobAtendimentoCodex(questionKey)[0]?.querySelector('.question-answer-text')?.focus();
    } catch (error) {
        if (error && error.cancelled) {
            setStatusRespostaPergunta(status, 'Pesquisa cancelada pelo usuario.');
        } else {
            setStatusRespostaPergunta(status, `Erro ao gerar IA: ${mensagemErro(error)}`, 'error');
        }
    } finally {
        aplicarEstadoJobAtendimentoCodex(questionKey);
        const currentCard = cardsAtuaisJobAtendimentoCodex(questionKey)[0];
        const currentTextarea = currentCard?.querySelector('.question-answer-text') || textarea;
        const currentGenerate = currentCard?.querySelector('.question-ai-answer-btn') || btnGerar;
        const currentSend = currentCard?.querySelector('.question-send-answer-btn') || btnEnviar;
        currentGenerate.disabled = Boolean(obterEstadoJobAtendimentoCodex(questionKey)?.polling_active);
        currentSend.disabled = !String(currentTextarea?.value || '').trim();
    }
}

async function enviarRespostaPerguntaManual(questionId, loja, textarea, btnEnviar, btnGerar, status, opcoes = {}) {
    const pergunta = obterPerguntaPorId(questionId, loja);
    const texto = String(textarea.value || '');
    const lojaResposta = lojaOrigemItem(pergunta) || (todasAsLojasSelecionadas() ? '' : state.lojaSelecionada);
    if (!pergunta || !lojaResposta || !texto.trim()) return;
    const skuResposta = skuRealPergunta(pergunta);
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
                item_id: itemIdResposta,
                proposal_id: String(textarea.dataset.codexProposalId || ''),
                proposal_version: Number(textarea.dataset.codexProposalVersion || 0),
                proposal_hash: String(textarea.dataset.codexProposalHash || '')
            })
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(mensagemErroApi(data, 'Erro ao enviar resposta.'));
        pergunta.answer = {
            text: data.resposta ?? texto,
            status: 'ANSWERED',
            date_created: new Date().toISOString()
        };
        pergunta.status = 'ANSWERED';
        try { window.JKPerguntasLoading?.invalidar(lojaResposta, pergunta); } catch (_) { /* Envio já confirmado. */ }
        delete textarea.dataset.codexProposalId;
        delete textarea.dataset.codexProposalVersion;
        delete textarea.dataset.codexProposalHash;
        if (opcoes.salvarExemplo) {
            try {
                await salvarTreinamentoAtendimentoPergunta(pergunta, { exemplo: montarExemploRespostaPergunta(pergunta, texto) });
            } catch (_) {
                perguntasStatus.textContent = 'Resposta enviada. Não foi possível salvar o exemplo da IA.';
                renderizarPerguntas();
                carregarContadoresNotificacoes(true);
                return;
            }
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
    window.JKPerguntasLoading?.verificarSessao();
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

async function selecionarLoja(nome, storeId = '') {
    if (!nome) return;
    window.JKPerguntasLoading?.salvarRascunho();
    guardarEdicaoTreinamento();
    state.lojaSelecionada = nome;
    state.lojaSelecionadaStoreId = String(storeId || '');
    sincronizarLojaTreinamento();
    state.lojaConfiguracaoPerguntas = todasAsLojasSelecionadas() ? TODAS_LOJAS_VALUE : nome;
    resetarPaginacaoPosVenda();
    resetarMediacoes();
    renderizarLojas();
    atualizarCabecalhoPosVenda();
    atualizarCabecalhoMediacao();
    if (treinamentoVisivel()) {
        await Promise.all([carregarTreinamentoAI(true), carregarSkusTreinamentoAI()]);
    } else if (document.getElementById('aba-pos-venda').classList.contains('active')) {
        await carregarPosVenda();
    } else if (document.getElementById('aba-mediacao').classList.contains('active')) {
        if (todasAsLojasSelecionadas()) {
            mediacaoStatus.textContent = 'Selecione uma loja especifica para consultar mediacoes.';
            return;
        }
        await carregarMediacoes(true);
    } else {
        await carregarPerguntas(1, { background: false });
    }
}

async function carregarPerguntasTodasLojas() {
    return window.JKPerguntasLoading.carregar(state.paginaPerguntas || 1);
}

async function carregarPerguntas(pagina = 1, opcoes = {}) {
    return window.JKPerguntasLoading.carregar(pagina, opcoes);
}
