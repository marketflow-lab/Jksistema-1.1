(function iniciarSolicitacoesIA() {
    const PAGE_SIZE = 20;
    const endpoint = '/api/mercadolivre/assistant/solicitacoes';
    const aba = document.getElementById('aba-solicitacao');
    const lojaStatus = document.getElementById('solicitacoes-loja-status');
    const statusLine = document.getElementById('solicitacoes-status');
    const summary = document.getElementById('solicitacoes-summary');
    const list = document.getElementById('solicitacoes-list');
    const pagination = document.getElementById('solicitacoes-pagination');
    const statusFilter = document.getElementById('solicitacoes-status-filtro');
    const reloadButton = document.getElementById('btn-solicitacoes-recarregar');

    let currentPage = 1;
    let requestGeneration = 0;
    let requestController = null;

    function text(value) {
        return String(value ?? '').trim();
    }

    function firstValue(object, names, fallback = '') {
        const source = object && typeof object === 'object' ? object : {};
        for (const name of names) {
            if (source[name] !== undefined && source[name] !== null && source[name] !== '') {
                return source[name];
            }
        }
        return fallback;
    }

    function allStoresSelected() {
        return state.lojaSelecionada === TODAS_LOJAS_VALUE;
    }

    function selectedStoreId() {
        if (allStoresSelected()) return '';
        const canonical = text(state.lojaSelecionadaStoreId);
        if (canonical) return canonical;
        const matches = lojasMercadoLivreConectadas().filter((loja) => (
            text(loja && loja.nome) === text(state.lojaSelecionada)
        ));
        return matches.length === 1 ? text(matches[0].store_id) : '';
    }

    function active() {
        return Boolean(aba && aba.classList.contains('active'));
    }

    function statusKey(value) {
        return text(value).toLowerCase().replace(/[\s-]+/g, '_');
    }

    function statusLabel(value) {
        const key = statusKey(value);
        const labels = {
            queued: 'Na fila',
            pending: 'Pendente',
            created: 'Registrada',
            running: 'Em andamento',
            processing: 'Em andamento',
            retrying: 'Nova tentativa',
            waiting_retry: 'Nova tentativa',
            pending_action: 'Aguardando ação',
            awaiting_action: 'Aguardando ação',
            needs_attention: 'Requer atenção',
            completed: 'Concluída',
            complete: 'Concluída',
            succeeded: 'Concluída',
            success: 'Concluída',
            failed: 'Com falha',
            error: 'Com falha',
            cancelled: 'Cancelada',
            canceled: 'Cancelada'
        };
        return labels[key] || text(value) || 'Sem status';
    }

    function statusClass(value) {
        const key = statusKey(value);
        if (['completed', 'complete', 'succeeded', 'success'].includes(key)) return 'ok';
        if (['failed', 'error', 'cancelled', 'canceled'].includes(key)) return 'danger';
        if (['pending_action', 'awaiting_action', 'needs_attention', 'retrying', 'waiting_retry'].includes(key)) return 'warn';
        return 'running';
    }

    function typeLabel(value) {
        const key = statusKey(value);
        const labels = {
            pergunta: 'Pergunta de anúncio',
            perguntas: 'Pergunta de anúncio',
            question: 'Pergunta de anúncio',
            perguntas_anuncio: 'Pergunta de anúncio',
            pos_venda: 'Pós-venda',
            post_sale: 'Pós-venda',
            mediacao: 'Mediação',
            mediation: 'Mediação'
        };
        return labels[key] || text(value) || 'Solicitação da IA';
    }

    function stepLabel(value) {
        const key = statusKey(value);
        const labels = {
            entender: 'Entendendo a solicitação',
            consultar: 'Consultando dados e evidências',
            validar: 'Validando as evidências',
            responder: 'Preparando a conclusão',
            aprovar: 'Preparando a revisão final'
        };
        return labels[key] || text(value);
    }

    function storeIdentity(item) {
        const storeId = text(firstValue(item, ['store_id', 'storeId']));
        const sellerId = text(firstValue(item, ['seller_id', 'sellerId']));
        const siteId = text(firstValue(item, ['site_id', 'siteId']));
        return {
            storeId,
            sellerId,
            siteId,
            name: text(firstValue(item, ['loja', 'store_name', 'nome_loja'], storeId ? `Loja ${storeId}` : 'Loja não identificada')),
            key: [storeId || 'sem-store', sellerId || 'sem-seller', siteId || 'sem-site'].join(':')
        };
    }

    function normalizeList(value) {
        if (value === undefined || value === null || value === '') return [];
        return Array.isArray(value) ? value.filter((item) => item !== undefined && item !== null && item !== '') : [value];
    }

    function objectText(value) {
        if (value === undefined || value === null) return '';
        if (typeof value !== 'object') return text(value);
        return text(firstValue(value, [
            'texto', 'text', 'mensagem', 'message', 'resumo', 'summary', 'descricao', 'description',
            'titulo', 'title', 'rotulo', 'label', 'intent', 'nome', 'name', 'fonte', 'source', 'resultado', 'result', 'id', 'status'
        ]));
    }

    function conclusionParts(item) {
        const value = firstValue(item, ['conclusao', 'conclusion'], {});
        if (value && typeof value === 'object' && !Array.isArray(value)) {
            return {
                operational: text(firstValue(value, ['operacional', 'operational', 'resultado', 'result', 'resumo', 'summary', 'mensagem', 'message', 'status'])),
                textual: text(firstValue(value, ['texto', 'text', 'resposta', 'answer', 'conclusao_textual', 'textual_conclusion']))
            };
        }
        const operational = text(firstValue(item, [
            'conclusao_operacional', 'operational_conclusion', 'status_message', 'completion_reason'
        ]));
        const scalar = text(value);
        return {
            operational: operational || scalar,
            textual: text(firstValue(item, ['conclusao_textual', 'textual_conclusion', 'answer_text', 'resposta'], scalar !== operational ? scalar : ''))
        };
    }

    function pendingAction(item) {
        const explicit = objectText(firstValue(item, [
            'acao_pendente', 'pending_action', 'action_required', 'next_action', 'proxima_acao'
        ]));
        if (explicit) return explicit;
        const agentState = statusKey(firstValue(item, ['agent_state']));
        return ['pending_action', 'awaiting_action', 'needs_attention'].includes(agentState)
            ? text(firstValue(item, ['status_message', 'completion_reason'], 'A solicitação requer uma ação do operador.'))
            : '';
    }

    function evidenceText(value) {
        if (!value || typeof value !== 'object') return text(value);
        const label = objectText(value);
        const source = text(firstValue(value, ['fonte', 'source', 'origem']));
        const stateValue = text(firstValue(value, ['estado', 'state', 'status']));
        const confidence = firstValue(value, ['confidence', 'confianca']);
        const confidenceText = confidence === '' || confidence === undefined || confidence === null
            ? ''
            : `confiança ${text(confidence)}`;
        return Array.from(new Set([label, source, stateValue, confidenceText].filter(Boolean))).join(' · ');
    }

    function timeline(item) {
        const supplied = normalizeList(firstValue(item, ['steps', 'timeline', 'linha_tempo'])).map((step) => {
            if (step && typeof step === 'object') {
                return {
                    label: stepLabel(firstValue(step, ['step', 'etapa', 'state', 'status', 'label'], 'Etapa atual')),
                    at: firstValue(step, ['at', 'em', 'created_at', 'updated_at', 'timestamp'])
                };
            }
            return { label: text(step), at: '' };
        }).filter((step) => step.label);
        if (supplied.length) return supplied;

        const generated = [];
        const createdAt = firstValue(item, ['created_at', 'criado_em']);
        const updatedAt = firstValue(item, ['updated_at', 'atualizado_em']);
        const completedAt = firstValue(item, ['completed_at', 'concluido_em']);
        if (createdAt) generated.push({ label: 'Solicitação registrada', at: createdAt });
        if (updatedAt && text(updatedAt) !== text(createdAt)) {
            generated.push({
                label: stepLabel(firstValue(item, ['current_step', 'agent_state', 'status_message'])) || statusLabel(item.status),
                at: updatedAt
            });
        }
        if (completedAt && !generated.some((step) => text(step.at) === text(completedAt))) {
            generated.push({ label: 'Processamento concluído', at: completedAt });
        }
        return generated;
    }

    function identifiers(item) {
        const fields = [
            ['Solicitação', firstValue(item, ['job_id', 'request_id', 'id'])],
            ['Pergunta', firstValue(item, ['question_id'])],
            ['Anúncio', firstValue(item, ['item_id'])],
            ['Proposta', firstValue(item, ['proposal_id'])],
            ['Referência', firstValue(item, ['subject_id', 'subject_key'])]
        ];
        return fields.filter((entry) => text(entry[1])).map(([label, value]) => (
            `<span><strong>${escapeHtml(label)}:</strong> ${escapeHtml(text(value))}</span>`
        )).join('');
    }

    function renderListBlock(title, values, className = '') {
        const items = normalizeList(values).map((value) => evidenceText(value)).filter(Boolean);
        if (!items.length) return '';
        return `
            <section class="solicitacao-info-block ${className}">
                <h4>${escapeHtml(title)}</h4>
                <ul>${items.map((item) => `<li>${escapeHtml(item)}</li>`).join('')}</ul>
            </section>
        `;
    }

    function renderCard(item) {
        const store = storeIdentity(item);
        const status = firstValue(item, ['status', 'agent_state']);
        const stage = stepLabel(firstValue(item, ['current_step', 'etapa_atual', 'status_message', 'agent_state'], 'Aguardando atualização'));
        const attempts = Number(firstValue(item, ['attempt_count', 'tentativas'], 0)) || 0;
        const retries = Number(firstValue(item, ['retry_count', 'retries'], 0)) || 0;
        const conclusions = conclusionParts(item);
        const action = pendingAction(item);
        const steps = timeline(item);
        const evidences = firstValue(item, ['evidencias', 'evidences', 'evidence'], []);
        const warnings = firstValue(item, ['avisos', 'warnings'], []);
        const createdAt = firstValue(item, ['created_at', 'criado_em']);
        const updatedAt = firstValue(item, ['updated_at', 'atualizado_em']);
        const completedAt = firstValue(item, ['completed_at', 'concluido_em']);

        return `
            <article class="solicitacao-card">
                <header class="solicitacao-card-head">
                    <div>
                        <span class="solicitacao-eyebrow">${escapeHtml(typeLabel(firstValue(item, ['task_type', 'tipo', 'type'])))}</span>
                        <h3>${escapeHtml(stage)}</h3>
                    </div>
                    <div class="solicitacao-card-badges">
                        <span class="badge solicitacao-store-badge" title="Store ID ${escapeHtml(store.storeId || '-')}">${escapeHtml(store.name)}</span>
                        <span class="badge ${statusClass(status)}">${escapeHtml(statusLabel(status))}</span>
                    </div>
                </header>

                <div class="solicitacao-identifiers">${identifiers(item)}</div>

                <div class="solicitacao-overview">
                    <section class="solicitacao-info-block">
                        <h4>Execução</h4>
                        <dl>
                            <div><dt>Etapa atual</dt><dd>${escapeHtml(stage)}</dd></div>
                            <div><dt>Tentativas</dt><dd>${attempts}</dd></div>
                            <div><dt>Repetições</dt><dd>${retries}</dd></div>
                        </dl>
                    </section>
                    <section class="solicitacao-info-block">
                        <h4>Linha do tempo</h4>
                        ${steps.length ? `<ol class="solicitacao-timeline">${steps.map((step) => `
                            <li><span>${escapeHtml(step.label)}</span>${step.at ? `<time>${escapeHtml(formatarData(step.at))}</time>` : ''}</li>
                        `).join('')}</ol>` : '<p>Aguardando a primeira etapa registrada.</p>'}
                    </section>
                </div>

                <div class="solicitacao-details-grid">
                    ${conclusions.operational ? `
                        <section class="solicitacao-info-block conclusion">
                            <h4>Conclusão operacional</h4>
                            <p>${escapeHtml(conclusions.operational)}</p>
                        </section>
                    ` : ''}
                    ${conclusions.textual ? `
                        <section class="solicitacao-info-block conclusion">
                            <h4>Conclusão da IA</h4>
                            <p>${escapeHtml(conclusions.textual)}</p>
                        </section>
                    ` : ''}
                    ${action ? `
                        <section class="solicitacao-info-block pending-action">
                            <h4>Ação pendente</h4>
                            <p>${escapeHtml(action)}</p>
                        </section>
                    ` : ''}
                    ${renderListBlock('Evidências utilizadas', evidences, 'evidences')}
                    ${renderListBlock('Avisos', warnings, 'warnings')}
                </div>

                <footer class="solicitacao-card-times">
                    ${createdAt ? `<span><strong>Criada:</strong> ${escapeHtml(formatarData(createdAt))}</span>` : ''}
                    ${updatedAt ? `<span><strong>Atualizada:</strong> ${escapeHtml(formatarData(updatedAt))}</span>` : ''}
                    ${completedAt ? `<span><strong>Concluída:</strong> ${escapeHtml(formatarData(completedAt))}</span>` : ''}
                    ${store.sellerId ? `<span><strong>Seller:</strong> ${escapeHtml(store.sellerId)}</span>` : ''}
                    ${store.siteId ? `<span><strong>Site:</strong> ${escapeHtml(store.siteId)}</span>` : ''}
                </footer>
            </article>
        `;
    }

    function renderItems(items) {
        if (!items.length) {
            list.innerHTML = '<div class="empty-state"><div><h2>Nenhuma solicitação encontrada</h2><p>As solicitações da IA deste escopo aparecerão aqui com seu andamento e conclusão.</p></div></div>';
            return;
        }
        if (!allStoresSelected()) {
            list.innerHTML = items.map(renderCard).join('');
            return;
        }

        const groups = new Map();
        items.forEach((item) => {
            const identity = storeIdentity(item);
            if (!groups.has(identity.key)) groups.set(identity.key, { identity, items: [] });
            groups.get(identity.key).items.push(item);
        });
        list.innerHTML = Array.from(groups.values()).map(({ identity, items: storeItems }) => `
            <section class="solicitacoes-store-group" data-store-id="${escapeHtml(identity.storeId)}">
                <header class="solicitacoes-store-head">
                    <div>
                        <span>Loja</span>
                        <h3>${escapeHtml(identity.name)}</h3>
                    </div>
                    <p>${storeItems.length} solicitação(ões) nesta página${identity.storeId ? ` · Store ID ${escapeHtml(identity.storeId)}` : ''}</p>
                </header>
                <div class="solicitacoes-store-items">${storeItems.map(renderCard).join('')}</div>
            </section>
        `).join('');
    }

    function renderSummary(data, total) {
        const statusSummary = data && typeof data.status_resumo === 'object' && data.status_resumo
            ? data.status_resumo
            : {};
        const metrics = Object.entries(statusSummary).filter((entry) => Number(entry[1]) > 0);
        summary.innerHTML = `
            <span class="metric"><strong>${total}</strong><span>Total</span></span>
            ${metrics.map(([key, value]) => `<span class="metric"><strong>${Number(value) || 0}</strong><span>${escapeHtml(statusLabel(key))}</span></span>`).join('')}
        `;
        summary.classList.remove('hidden');
    }

    function renderPagination(total, limit, offset, data) {
        const safeLimit = Math.max(1, Number(limit || PAGE_SIZE));
        const page = Math.floor(Math.max(0, Number(offset || 0)) / safeLimit) + 1;
        const totalPages = Math.max(1, Math.ceil(Number(total || 0) / safeLimit));
        const paginationAlias = data && data.pagination && typeof data.pagination === 'object' ? data.pagination : {};
        const hasNext = paginationAlias.has_next !== undefined
            ? paginationAlias.has_next === true
            : page < totalPages;
        currentPage = page;
        if (Number(total || 0) <= safeLimit) {
            pagination.classList.add('hidden');
            pagination.innerHTML = '';
            return;
        }
        pagination.innerHTML = `
            <span>Página ${page} de ${totalPages}</span>
            <div class="pagination-actions">
                <button class="pagination-btn" type="button" data-solicitacoes-page="${page - 1}" ${page <= 1 ? 'disabled' : ''}>Anterior</button>
                <button class="pagination-btn" type="button" data-solicitacoes-page="${page + 1}" ${!hasNext ? 'disabled' : ''}>Próxima</button>
            </div>
        `;
        pagination.classList.remove('hidden');
        pagination.querySelectorAll('[data-solicitacoes-page]').forEach((button) => {
            button.addEventListener('click', () => carregarSolicitacoes(Number(button.dataset.solicitacoesPage || 1)));
        });
    }

    function updateHeader() {
        if (!state.lojaSelecionada) {
            lojaStatus.textContent = 'Escolha uma loja conectada.';
            return;
        }
        lojaStatus.textContent = allStoresSelected()
            ? 'Todas as contas · agrupadas por loja'
            : `Loja selecionada: ${state.lojaSelecionada}`;
    }

    function reset() {
        currentPage = 1;
        state.solicitacoes = [];
        state.paginaSolicitacoes = 1;
        state.totalSolicitacoes = 0;
        requestGeneration += 1;
        if (requestController) requestController.abort();
        requestController = null;
        summary.classList.add('hidden');
        summary.innerHTML = '';
        pagination.classList.add('hidden');
        pagination.innerHTML = '';
        list.innerHTML = '';
        updateHeader();
    }

    async function carregarSolicitacoes(page = 1) {
        updateHeader();
        if (!state.lojaSelecionada) {
            reset();
            statusLine.textContent = 'Escolha uma loja conectada.';
            return;
        }

        const storeId = selectedStoreId();
        if (!allStoresSelected() && !storeId) {
            reset();
            statusLine.textContent = 'Não foi possível identificar a loja pelo Store ID. Selecione novamente a conta.';
            return;
        }

        const safePage = Math.max(1, Number(page || 1));
        const generation = ++requestGeneration;
        if (requestController) requestController.abort();
        requestController = typeof AbortController === 'function' ? new AbortController() : null;
        reloadButton.disabled = true;
        statusLine.textContent = allStoresSelected()
            ? 'Carregando solicitações de todas as contas...'
            : `Carregando solicitações de ${state.lojaSelecionada}...`;

        const params = new URLSearchParams({
            limit: String(PAGE_SIZE),
            offset: String((safePage - 1) * PAGE_SIZE)
        });
        if (storeId) params.set('store_id', storeId);
        if (statusFilter.value) params.set('status', statusFilter.value);

        try {
            const response = await fetch(`${endpoint}?${params.toString()}`, {
                headers: obterAuthHeaders(),
                cache: 'no-store',
                ...(requestController ? { signal: requestController.signal } : {})
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(mensagemErroApi(data, 'Erro ao carregar solicitações da IA.'));
            if (generation !== requestGeneration) return;

            const items = Array.isArray(data.solicitacoes)
                ? data.solicitacoes
                : (Array.isArray(data.items) ? data.items : []);
            const aliasPagination = data.pagination && typeof data.pagination === 'object' ? data.pagination : {};
            const total = Number(data.total ?? aliasPagination.total ?? items.length) || 0;
            const limit = Number(data.limit ?? aliasPagination.page_size ?? PAGE_SIZE) || PAGE_SIZE;
            const offset = Number(data.offset ?? ((Number(aliasPagination.page || safePage) - 1) * limit)) || 0;
            currentPage = Math.floor(offset / limit) + 1;
            state.solicitacoes = items;
            state.paginaSolicitacoes = currentPage;
            state.totalSolicitacoes = total;
            renderItems(items);
            renderSummary(data, total);
            renderPagination(total, limit, offset, data);
            statusLine.textContent = total
                ? `${total} solicitação(ões) no escopo selecionado. Atualizado em ${new Date().toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })}.`
                : 'Nenhuma solicitação encontrada no escopo selecionado.';
        } catch (error) {
            if (generation !== requestGeneration || (error && error.name === 'AbortError')) return;
            list.innerHTML = `<div class="empty-state"><div><h2>Falha ao carregar solicitações</h2><p>${escapeHtml(mensagemErro(error))}</p></div></div>`;
            summary.classList.add('hidden');
            pagination.classList.add('hidden');
            statusLine.textContent = 'Não foi possível atualizar as solicitações da IA.';
        } finally {
            if (generation === requestGeneration) {
                requestController = null;
                reloadButton.disabled = false;
            }
        }
    }

    async function carregar(page = 1) {
        return carregarSolicitacoes(page);
    }

    statusFilter.addEventListener('change', () => carregar(1));
    reloadButton.addEventListener('click', () => carregar(currentPage));

    window.JKSolicitacoes = Object.freeze({
        carregar,
        resetar: reset,
        atualizarCabecalho: updateHeader,
        estaVisivel: active
    });
    updateHeader();
}());
