
        function safeJsonParse(value, fallback) {
            try {
                return JSON.parse(value);
            } catch (_err) {
                return fallback;
            }
        }

        if (!verificarSessao()) {
            // verificarSessao redireciona quando não há token.
        }

        const permissions = safeJsonParse(localStorage.getItem('permissions') || '{}', {});
        if (!(permissions.full === true || permissions.perguntas_pos_venda === true)) {
            alert('Você não tem permissão para acessar este módulo.');
            window.location.href = 'dashboard.html';
        }

        const state = {
            lojas: [],
            lojaSelecionada: '',
            perguntas: [],
            paginaPerguntas: 1,
            totalPerguntas: 0,
            tamanhoPaginaPerguntas: 20,
            carregandoPerguntas: false,
            carregandoPosVenda: false,
            posVendaPagina: 1,
            posVendaOffset: 0,
            posVendaNextOffset: null,
            posVendaOffsets: [0],
            posVendaCarregadoPara: '',
            posVendaConversas: [],
            posVendaConversaSelecionada: null,
            posVendaDetalheCarregando: false,
            treinamentoCarregado: false,
            treinamentoTipo: 'perguntas_anuncio',
            treinamentoDados: {
                perguntas_anuncio: { orientacoes: '', updated_at: null },
                pos_venda: { orientacoes: '', updated_at: null }
            },
            produtosTreinamento: [],
            produtosTreinamentoCarregados: false,
            automacaoPerguntasTimer: null,
            automacaoPerguntasRodando: false,
            automacaoPosVendaRodando: false,
            aprovacoesNotificadas: new Set()
        };

        const lojasGrid = document.getElementById('lojas-grid');
        const lojasStatus = document.getElementById('lojas-status');
        const perguntasStatus = document.getElementById('perguntas-status');
        const perguntasSummary = document.getElementById('perguntas-summary');
        const perguntasList = document.getElementById('perguntas-list');
        const perguntasPagination = document.getElementById('perguntas-pagination');
        const statusFiltro = document.getElementById('status-filtro');
        const btnRecarregar = document.getElementById('btn-recarregar');
        const posVendaLojaStatus = document.getElementById('pos-venda-loja-status');
        const posVendaDias = document.getElementById('pos-venda-dias');
        const btnPosVendaRecarregar = document.getElementById('btn-pos-venda-recarregar');
        const posVendaLojasGrid = document.getElementById('pos-venda-lojas-grid');
        const posVendaStatus = document.getElementById('pos-venda-status');
        const posVendaSummary = document.getElementById('pos-venda-summary');
        const posVendaList = document.getElementById('pos-venda-list');
        const posVendaPagination = document.getElementById('pos-venda-pagination');
        const posVendaDetail = document.getElementById('pos-venda-detail');
        const aiTrainingStatus = document.getElementById('ai-training-status');
        const aiTrainingTypeTabs = Array.from(document.querySelectorAll('.training-type-tab'));
        const aiTrainingOrientacoesLabel = document.getElementById('ai-training-orientacoes-label');
        const aiTrainingOrientacoes = document.getElementById('ai-training-orientacoes');
        const aiTrainingSku = document.getElementById('ai-training-sku');
        const aiTrainingSkuInfo = document.getElementById('ai-training-sku-info');
        const aiTrainingPergunta = document.getElementById('ai-training-pergunta');
        const aiTrainingContexto = document.getElementById('ai-training-contexto');
        const aiTrainingChat = document.getElementById('ai-training-chat');
        const aiTrainingChatHead = document.getElementById('ai-training-chat-head');
        const btnAiTrainingSalvar = document.getElementById('btn-ai-training-salvar');
        const btnAiTrainingSimular = document.getElementById('btn-ai-training-simular');

        function escapeHtml(value) {
            return String(value ?? '').replace(/[&<>"']/g, (char) => ({
                '&': '&amp;',
                '<': '&lt;',
                '>': '&gt;',
                '"': '&quot;',
                "'": '&#39;'
            }[char]));
        }

        function corrigirTextoQuebrado(value) {
            const texto = String(value ?? '');
            if (!/[ÃÂ]/.test(texto)) return texto;
            try {
                return decodeURIComponent(escape(texto));
            } catch (_error) {
                return texto;
            }
        }

        function mensagemErro(error) {
            return corrigirTextoQuebrado(error && error.message ? error.message : error);
        }

        function formatarData(value) {
            if (!value) return '-';
            const data = new Date(value);
            if (Number.isNaN(data.getTime())) return value;
            return data.toLocaleString('pt-BR', {
                day: '2-digit',
                month: '2-digit',
                year: 'numeric',
                hour: '2-digit',
                minute: '2-digit'
            });
        }

        function rotuloStatus(status) {
            const mapa = {
                ANSWERED: 'Respondida',
                UNANSWERED: 'Não respondida',
                CLOSED_UNANSWERED: 'Fechada sem resposta',
                UNDER_REVIEW: 'Em revisão',
                BANNED: 'Bloqueada'
            };
            return mapa[String(status || '').toUpperCase()] || (status || '-');
        }

        function classeStatus(status) {
            const valor = String(status || '').toUpperCase();
            if (valor === 'ANSWERED') return 'ok';
            if (valor === 'UNANSWERED' || valor === 'UNDER_REVIEW') return 'warn';
            return 'danger';
        }

        function ordenarPerguntasRecentes(perguntas) {
            return [...perguntas].sort((a, b) => {
                const dataA = new Date(a.date_created || a.last_updated || 0).getTime() || 0;
                const dataB = new Date(b.date_created || b.last_updated || 0).getTime() || 0;
                return dataB - dataA;
            });
        }

        function formatarTempoRespostaML(minutos) {
            const totalMinutos = Number(minutos);
            if (!Number.isFinite(totalMinutos) || totalMinutos < 0) return 'Sem dados';
            const arredondado = Math.round(totalMinutos);
            if (arredondado < 60) return `${arredondado} min`;
            const horas = Math.floor(arredondado / 60);
            const mins = arredondado % 60;
            if (horas < 24) return mins ? `${horas}h ${mins}min` : `${horas}h`;
            const dias = Math.floor(horas / 24);
            const horasRestantes = horas % 24;
            return horasRestantes ? `${dias}d ${horasRestantes}h` : `${dias}d`;
        }

        function textoGanhoVendasML(segmento) {
            if (!segmento || segmento.sales_percent_increase === null || segmento.sales_percent_increase === undefined) {
                return 'Sem projeção de aumento';
            }
            const valor = Number(segmento.sales_percent_increase);
            if (!Number.isFinite(valor)) return 'Sem projeção de aumento';
            return `Potencial de +${valor}% em vendas`;
        }

        function metricTempoRespostaML(titulo, segmento, subtituloPadrao) {
            const tempo = segmento && segmento.response_time !== undefined ? segmento.response_time : null;
            const subtitulo = segmento && segmento.sales_percent_increase !== undefined
                ? textoGanhoVendasML(segmento)
                : subtituloPadrao;
            return `<div class="metric"><strong>${escapeHtml(formatarTempoRespostaML(tempo))}</strong><span>${escapeHtml(titulo)} · ${escapeHtml(subtitulo)}</span></div>`;
        }

        function ativarAba(nome) {
            document.querySelectorAll('.tab-button').forEach((button) => {
                const ativa = button.dataset.tab === nome;
                button.classList.toggle('active', ativa);
                button.setAttribute('aria-selected', ativa ? 'true' : 'false');
            });
            document.querySelectorAll('.tab-content').forEach((section) => {
                section.classList.toggle('active', section.id === 'aba-' + nome);
            });
            if (nome === 'pos-venda' && state.lojaSelecionada) {
                carregarPosVenda();
            }
            if (nome === 'treinar-ai') {
                carregarTreinamentoAI();
                carregarSkusTreinamentoAI();
            }
        }

        function renderizarLojas() {
            if (!state.lojas.length) {
                const vazio = '<div class="empty-state"><div><h2>Nenhuma loja encontrada</h2><p>Cadastre uma loja em Integrações para começar.</p></div></div>';
                lojasGrid.innerHTML = vazio;
                posVendaLojasGrid.innerHTML = vazio;
                perguntasStatus.textContent = 'Nenhuma loja cadastrada em Integrações.';
                perguntasPagination.classList.add('hidden');
                perguntasPagination.innerHTML = '';
                return;
            }

            const lojasHtml = state.lojas.map((loja) => {
                const nome = String(loja.nome || '').trim();
                const conectado = loja.mercadolivre_conectado === true;
                const active = nome === state.lojaSelecionada;
                const seller = loja.seller_id ? `Conta ${escapeHtml(loja.seller_id)}` : 'Mercado Livre pendente';
                const config = loja.config_perguntas || {};
                return `
                    <div class="store-card ${active ? 'active' : ''} ${conectado ? '' : 'disabled'}" data-loja="${escapeHtml(nome)}" tabindex="${conectado ? '0' : '-1'}" aria-disabled="${conectado ? 'false' : 'true'}">
                        <span class="store-name">${escapeHtml(nome)}</span>
                        <span class="store-meta">
                            <span class="badge ${conectado ? 'ok' : 'warn'}">${conectado ? 'ML conectado' : 'Conectar ML'}</span>
                            <span>${seller}</span>
                        </span>
                        <span class="store-options">
                            <label class="store-option">
                                <input class="store-config-checkbox" type="checkbox" data-config="responder_automaticamente" ${config.responder_automaticamente ? 'checked' : ''}>
                                <span>Responder automaticamente</span>
                            </label>
                            <label class="store-option">
                                <input class="store-config-checkbox" type="checkbox" data-config="solicitar_aprovacao" ${config.solicitar_aprovacao ? 'checked' : ''}>
                                <span>Solicitar aprovação antes de mandar para o usuário</span>
                            </label>
                        </span>
                    </div>
                `;
            }).join('');

            lojasGrid.innerHTML = lojasHtml;
            posVendaLojasGrid.innerHTML = lojasHtml;
            vincularEventosLojas(lojasGrid);
            vincularEventosLojas(posVendaLojasGrid);
        }

        function vincularEventosLojas(container) {
            container.querySelectorAll('.store-card').forEach((card) => {
                card.addEventListener('click', (event) => {
                    if (event.target.closest('.store-option')) return;
                    if (card.classList.contains('disabled')) return;
                    selecionarLoja(card.dataset.loja || '');
                });
                card.addEventListener('keydown', (event) => {
                    if (!['Enter', ' '].includes(event.key)) return;
                    if (event.target.closest('.store-option')) return;
                    if (card.classList.contains('disabled')) return;
                    event.preventDefault();
                    selecionarLoja(card.dataset.loja || '');
                });
                card.querySelectorAll('.store-config-checkbox').forEach((input) => {
                    input.addEventListener('click', (event) => event.stopPropagation());
                    input.addEventListener('change', () => salvarConfigLoja(card));
                });
            });
        }

        async function salvarConfigLoja(card) {
            const nome = card.dataset.loja || '';
            if (!nome) return;
            const responderAutomaticamente = !!card.querySelector('[data-config="responder_automaticamente"]')?.checked;
            const solicitarAprovacao = !!card.querySelector('[data-config="solicitar_aprovacao"]')?.checked;
            try {
                const response = await fetch('/api/mercadolivre/perguntas/lojas/config', {
                    method: 'POST',
                    headers: {
                        ...obterAuthHeaders(),
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        loja: nome,
                        responder_automaticamente: responderAutomaticamente,
                        solicitar_aprovacao: solicitarAprovacao
                    })
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(data.detail || 'Erro ao salvar configuração.');
                const loja = state.lojas.find((item) => String(item.nome || '') === nome);
                if (loja) loja.config_perguntas = data.config_perguntas || {};
                lojasStatus.textContent = 'Configuração da loja salva.';
                renderizarLojas();
                iniciarAutomacaoPerguntas();
            } catch (error) {
                alert(`Erro ao salvar configuração da loja: ${mensagemErro(error)}`);
                renderizarLojas();
            }
        }

        function notificarAprovacaoSidebar(aprovacao) {
            const id = String((aprovacao && aprovacao.id) || '').trim();
            if (!id || state.aprovacoesNotificadas.has(id)) return;
            state.aprovacoesNotificadas.add(id);

            if (typeof window.JKIASidebarNotifyApproval === 'function') {
                window.JKIASidebarNotifyApproval(aprovacao);
                return;
            }

            window.__JK_PENDING_IA_APPROVALS__ = window.__JK_PENDING_IA_APPROVALS__ || [];
            window.__JK_PENDING_IA_APPROVALS__.push(aprovacao);
        }

        async function carregarAprovacoesPendentes() {
            try {
                const response = await fetch('/api/mercadolivre/perguntas/aprovacoes', {
                    headers: obterAuthHeaders(),
                    cache: 'no-store'
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(data.detail || 'Erro ao carregar aprovações pendentes.');
                (Array.isArray(data.pendentes) ? data.pendentes : []).forEach(notificarAprovacaoSidebar);
            } catch (error) {
                console.warn('[Perguntas IA] Não foi possível carregar aprovações pendentes:', error);
            }
        }

        function existeLojaComAutomacaoAtiva() {
            return state.lojas.some((loja) => {
                const config = loja.config_perguntas || {};
                return loja.mercadolivre_conectado === true && config.responder_automaticamente === true;
            });
        }

        async function executarAutomacaoPerguntas() {
            if (state.automacaoPerguntasRodando || !existeLojaComAutomacaoAtiva()) return;
            state.automacaoPerguntasRodando = true;
            try {
                const response = await fetch('/api/mercadolivre/perguntas/automacao/poll?max_per_store=3', {
                    method: 'POST',
                    headers: obterAuthHeaders(),
                    cache: 'no-store'
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(data.detail || 'Erro na automação de perguntas.');

                const pendentes = [
                    ...(Array.isArray(data.novas_pendentes) ? data.novas_pendentes : []),
                    ...(Array.isArray(data.pendentes) ? data.pendentes : [])
                ];
                pendentes.forEach(notificarAprovacaoSidebar);

                if (
                    Array.isArray(data.enviadas) &&
                    data.enviadas.length &&
                    !state.carregandoPerguntas &&
                    document.getElementById('aba-perguntas').classList.contains('active')
                ) {
                    carregarPerguntas(state.paginaPerguntas || 1);
                }
            } catch (error) {
                console.warn('[Perguntas IA] Falha ao executar automação:', error);
            } finally {
                state.automacaoPerguntasRodando = false;
            }
        }

        async function executarAutomacaoPosVenda() {
            if (state.automacaoPosVendaRodando || !existeLojaComAutomacaoAtiva()) return;
            state.automacaoPosVendaRodando = true;
            try {
                const params = new URLSearchParams({ max_per_store: '2' });
                if (state.lojaSelecionada) params.set('loja', state.lojaSelecionada);
                const response = await fetch(`/api/mercadolivre/pos-venda/automacao/poll?${params.toString()}`, {
                    method: 'POST',
                    headers: obterAuthHeaders(),
                    cache: 'no-store'
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(data.detail || 'Erro na automação de pós venda.');

                const pendentes = [
                    ...(Array.isArray(data.novas_pendentes) ? data.novas_pendentes : []),
                    ...(Array.isArray(data.pendentes) ? data.pendentes : [])
                ];
                pendentes.forEach(notificarAprovacaoSidebar);

                if (
                    Array.isArray(data.enviadas) &&
                    data.enviadas.length &&
                    !state.carregandoPosVenda &&
                    document.getElementById('aba-pos-venda').classList.contains('active')
                ) {
                    carregarPosVenda(true);
                }
            } catch (error) {
                console.warn('[Pós venda IA] Falha ao executar automação:', error);
            } finally {
                state.automacaoPosVendaRodando = false;
            }
        }

        function iniciarAutomacaoPerguntas() {
            if (state.automacaoPerguntasTimer) {
                clearInterval(state.automacaoPerguntasTimer);
                state.automacaoPerguntasTimer = null;
            }

            carregarAprovacoesPendentes();
            if (!existeLojaComAutomacaoAtiva()) return;

            setTimeout(executarAutomacaoPerguntas, 1500);
            setTimeout(executarAutomacaoPosVenda, 2500);
            state.automacaoPerguntasTimer = setInterval(() => {
                executarAutomacaoPerguntas();
                executarAutomacaoPosVenda();
            }, 60000);
        }

        function renderizarResumo(data) {
            const resumo = data.status_resumo || {};
            const total = Number(data.total || 0);
            const retornadas = Number(data.retornadas || 0);
            const respondidas = Number(resumo.ANSWERED || 0);
            const pendentes = Number(resumo.UNANSWERED || 0);
            const tempoML = data.tempo_resposta_ml || {};
            const metricasTempo = tempoML.available ? [
                metricTempoRespostaML('Tempo médio ML', tempoML.total || {}, 'Últimos 14 dias'),
                metricTempoRespostaML('Horário comercial', tempoML.weekdays_working_hours || {}, 'Seg. a sex. 9h-18h'),
                metricTempoRespostaML('Fora do horário', tempoML.weekdays_extra_hours || {}, 'Seg. a sex. 18h-00h'),
                metricTempoRespostaML('Fim de semana', tempoML.weekend || {}, 'Sáb. e dom.')
            ].join('') : `
                <div class="metric"><strong>Sem dados</strong><span>Tempo de resposta ML · ${escapeHtml(tempoML.erro || 'Métrica indisponível')}</span></div>
            `;
            perguntasSummary.classList.remove('hidden');
            perguntasSummary.innerHTML = `
                <div class="metric"><strong>${retornadas}</strong><span>Perguntas carregadas</span></div>
                <div class="metric"><strong>${total}</strong><span>Total na conta</span></div>
                <div class="metric"><strong>${pendentes}</strong><span>Não respondidas</span></div>
                <div class="metric"><strong>${respondidas}</strong><span>Respondidas</span></div>
                ${metricasTempo}
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

        function renderizarPerguntas() {
            const perguntas = state.perguntas;
            if (!perguntas.length) {
                perguntasList.innerHTML = '<div class="empty-state"><div><h2>Nenhuma pergunta encontrada</h2><p>Não há perguntas para o filtro selecionado nesta loja.</p></div></div>';
                perguntasPagination.classList.add('hidden');
                perguntasPagination.innerHTML = '';
                return;
            }

            const total = state.totalPerguntas || perguntas.length;
            const totalPaginas = Math.max(1, Math.ceil(total / state.tamanhoPaginaPerguntas));
            state.paginaPerguntas = Math.min(Math.max(1, state.paginaPerguntas), totalPaginas);
            const pagina = perguntas;

            perguntasList.innerHTML = pagina.map((pergunta) => {
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
                return `
                    <article class="question-card">
                        <div class="question-body">
                            <div class="question-thumb">${foto}</div>
                            <div>
                                <div class="question-top">
                                    <div>
                                        <h3 class="question-title">${tituloHtml}</h3>
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
                                ${answer}
                            </div>
                        </div>
                    </article>
                `;
            }).join('');
            renderizarPaginacaoPerguntas(state.totalPerguntas || perguntas.length);
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
                const primeiraConectada = state.lojas.find((loja) => loja.mercadolivre_conectado);
                if (!state.lojaSelecionada && primeiraConectada) {
                    state.lojaSelecionada = primeiraConectada.nome || '';
                }
                lojasStatus.textContent = state.lojas.length
                    ? `${state.lojas.length} loja(s) cadastrada(s)`
                    : 'Nenhuma loja cadastrada';
                renderizarLojas();
                atualizarCabecalhoPosVenda();
                iniciarAutomacaoPerguntas();
                if (state.lojaSelecionada) await carregarPerguntas();
            } catch (error) {
                lojasStatus.textContent = 'Erro ao carregar lojas.';
                lojasGrid.innerHTML = `<div class="empty-state"><div><h2>Falha ao carregar lojas</h2><p>${escapeHtml(mensagemErro(error))}</p></div></div>`;
            }
        }

        async function selecionarLoja(nome) {
            if (!nome || state.carregandoPerguntas) return;
            state.lojaSelecionada = nome;
            resetarPaginacaoPosVenda();
            renderizarLojas();
            atualizarCabecalhoPosVenda();
            if (document.getElementById('aba-pos-venda').classList.contains('active')) {
                await carregarPosVenda();
            } else {
                await carregarPerguntas();
            }
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
            perguntasStatus.textContent = `Carregando perguntas de ${state.lojaSelecionada}...`;

            try {
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
                perguntasStatus.textContent = data.interrompido
                    ? `${perguntas.length} pergunta(s) carregada(s). Mostrando 20 por página, das mais recentes para as mais antigas. O Mercado Livre limitou esta busca aos registros mais recentes.`
                    : `${perguntas.length} pergunta(s) carregada(s) da loja ${state.lojaSelecionada}. Mostrando 20 por página, das mais recentes para as mais antigas.`;
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

        function atualizarCabecalhoPosVenda() {
            posVendaLojaStatus.textContent = state.lojaSelecionada
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

        function formatarMoeda(value) {
            const numero = Number(value || 0);
            return numero.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
        }

        function chaveCachePosVenda() {
            return `jk_pos_venda_cache:${state.lojaSelecionada || 'sem-loja'}:${posVendaDias.value}:${state.posVendaOffset}`;
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

        function renderizarResumoPosVenda(data) {
            posVendaSummary.classList.remove('hidden');
            posVendaSummary.innerHTML = `
                <div class="metric"><strong>${Number(data.conversas_total || 0)}</strong><span>Vendas com conversa</span></div>
                <div class="metric"><strong>${Number(data.orders_avaliadas || 0)}</strong><span>Vendas avaliadas</span></div>
                <div class="metric"><strong>${Number(data.orders_total || 0)}</strong><span>Vendas no período</span></div>
                <div class="metric"><strong>${Number(data.dias || 0)}</strong><span>Dias consultados</span></div>
            `;
        }

        function renderizarPaginacaoPosVenda(data, conversas) {
            const temAnterior = state.posVendaPagina > 1;
            const proximo = data && data.next_offset !== null && data.next_offset !== undefined ? Number(data.next_offset) : null;
            const temProxima = proximo !== null && !Number.isNaN(proximo);
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
                        state.posVendaOffset = state.posVendaOffsets[state.posVendaPagina - 1] || 0;
                        carregarPosVenda(true);
                    }
                    if (action === 'next' && temProxima) {
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
                state.posVendaConversas = [];
                state.posVendaConversaSelecionada = null;
                posVendaDetail.classList.add('hidden');
                posVendaDetail.innerHTML = '';
                posVendaList.innerHTML = '<div class="empty-state"><div><h2>Nenhuma conversa encontrada</h2><p>Não há vendas com conversa iniciada no período selecionado.</p></div></div>';
                return;
            }

            state.posVendaConversas = conversas;
            posVendaList.innerHTML = conversas.map((venda, index) => {
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
                const active = state.posVendaConversaSelecionada && String(state.posVendaConversaSelecionada.pack_id || '') === String(venda.pack_id || '');
                return `
                    <article class="question-card conversation-card ${active ? 'active' : ''}" data-index="${index}">
                        <div class="question-body">
                            <div class="question-thumb">${foto}</div>
                            <div>
                        <div class="question-top">
                            <div>
                                <h3 class="question-title">Venda ${escapeHtml(venda.order_id || '-')}</h3>
                                <div class="sale-meta">
                                    <span>Pack ${escapeHtml(venda.pack_id || '-')}</span>
                                    <span>Comprador ${escapeHtml(venda.buyer_nickname || venda.buyer_id || '-')}</span>
                                    <span class="sale-value">${escapeHtml(formatarMoeda(venda.total_amount))}</span>
                                </div>
                            </div>
                            <span class="question-date">${escapeHtml(formatarData(venda.last_message_date || venda.date_created))}</span>
                        </div>
                        <div class="sale-items">${itensTexto}</div>
                        <div class="sale-meta">
                            <span class="badge ${conversaBadge}">${escapeHtml(conversa.status || 'com conversa')}</span>
                            <span>${Number(venda.messages_count || 0)} mensagem(ns)</span>
                            <span>Venda: ${escapeHtml(formatarData(venda.date_created))}</span>
                        </div>
                        <div class="answer-box ${venda.last_message_text ? '' : 'empty'}">${escapeHtml(venda.last_message_text || 'Conversa iniciada sem prévia disponível.')}</div>
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
        }

        function renderizarDetalhePosVenda(conversa, mensagemStatus = '') {
            const mensagens = Array.isArray(conversa.messages) ? conversa.messages : [];
            const itens = Array.isArray(conversa.items) ? conversa.items : [];
            const item = itens[0] || {};
            const titulo = item.title || conversa.item_title || 'Produto';
            const limite = Math.max(1, Number(conversa.seller_max_message_length || 350) || 350);
            const mensagensHtml = mensagens.length
                ? mensagens.map((msg) => `
                    <div class="pos-sale-message ${msg.from_role === 'seller' ? 'seller' : 'buyer'}">
                        <span class="training-message-role">${msg.from_role === 'seller' ? 'Vendedor' : 'Comprador'} · ${escapeHtml(formatarData(msg.date))}</span>
                        ${escapeHtml(msg.text || '-')}
                    </div>
                `).join('')
                : '<div class="answer-box empty">Nenhuma mensagem anterior foi encontrada para esta conversa.</div>';

            posVendaDetail.classList.remove('hidden');
            posVendaDetail.innerHTML = `
                <div class="pos-sale-detail-head">
                    <div>
                        <h3>${escapeHtml(titulo)}</h3>
                        <div class="sale-meta">
                            <span>Pack ${escapeHtml(conversa.pack_id || '-')}</span>
                            <span>Pedido ${escapeHtml(conversa.order_id || '-')}</span>
                            <span>SKU ${escapeHtml(item.sku || '-')}</span>
                            <span>Comprador ${escapeHtml(conversa.buyer_nickname || conversa.buyer_id || '-')}</span>
                        </div>
                    </div>
                    <span class="badge ok">Conversa aberta</span>
                </div>
                <div class="pos-sale-messages">${mensagensHtml}</div>
                <div class="pos-sale-reply">
                    <textarea id="pos-venda-resposta-texto" maxlength="${limite}" placeholder="Digite a resposta para enviar ao comprador"></textarea>
                    <button id="btn-pos-venda-enviar-resposta" class="action-btn" type="button" disabled>Enviar resposta</button>
                </div>
                <div id="pos-venda-resposta-status" class="status-line">${escapeHtml(mensagemStatus || `Limite do Mercado Livre: ${limite} caracteres.`)}</div>
            `;

            const textarea = document.getElementById('pos-venda-resposta-texto');
            const botao = document.getElementById('btn-pos-venda-enviar-resposta');
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
            atualizarEstado();
            posVendaDetail.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }

        async function abrirConversaPosVenda(venda) {
            if (!venda || state.posVendaDetalheCarregando) return;
            state.posVendaConversaSelecionada = venda;
            renderizarPosVenda(state.posVendaConversas);
            posVendaDetail.classList.remove('hidden');
            posVendaDetail.innerHTML = '<div class="status-line">Carregando histórico completo da conversa...</div>';
            state.posVendaDetalheCarregando = true;
            try {
                const params = new URLSearchParams({
                    loja: state.lojaSelecionada,
                    pack_id: venda.pack_id || ''
                });
                if (venda.order_id) params.set('order_id', venda.order_id);
                const response = await fetch(`/api/mercadolivre/pos-venda/conversas/detalhe?${params.toString()}`, {
                    headers: obterAuthHeaders(),
                    cache: 'no-store'
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(data.detail || 'Erro ao carregar conversa.');
                const conversa = data.conversa || {};
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

        async function enviarRespostaPosVenda(conversa, textarea, botao, status) {
            const texto = String(textarea.value || '').trim();
            if (!texto) return;
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
                        loja: state.lojaSelecionada,
                        pack_id: conversa.pack_id || '',
                        buyer_id: conversa.buyer_id || '',
                        texto,
                        max_chars: conversa.seller_max_message_length || 350
                    })
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(data.detail || 'Erro ao enviar resposta.');
                textarea.value = '';
                status.textContent = 'Resposta enviada. Atualizando conversa...';
                await abrirConversaPosVenda(conversa);
                carregarPosVenda(true);
            } catch (error) {
                status.textContent = `Erro ao enviar resposta: ${mensagemErro(error)}`;
                botao.disabled = false;
            }
        }

        function renderizarDadosPosVenda(data, origemCache = false) {
            const conversas = Array.isArray(data.conversas) ? data.conversas : [];
            posVendaStatus.textContent = origemCache
                ? `Exibindo informações salvas de ${state.lojaSelecionada}. Atualizando pela API...`
                : (data.interrompido
                    ? `${conversas.length} venda(s) com conversa. A consulta foi limitada às vendas mais recentes.`
                    : `${conversas.length} venda(s) com conversa iniciada em ${state.lojaSelecionada}.`);
            renderizarResumoPosVenda(data);
            renderizarPosVenda(conversas);
            renderizarPaginacaoPosVenda(data, conversas);
        }

        async function carregarPosVenda(forcar = false) {
            if (!state.lojaSelecionada || state.carregandoPosVenda) return;
            const chave = `${state.lojaSelecionada}:${posVendaDias.value}:${state.posVendaOffset}`;
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
                posVendaStatus.textContent = `Carregando página ${state.posVendaPagina} de vendas com conversa de ${state.lojaSelecionada}...`;
            }

            try {
                const params = new URLSearchParams({
                    loja: state.lojaSelecionada,
                    dias: posVendaDias.value,
                    offset: String(state.posVendaOffset),
                    limit: '20',
                    max_orders: '10000'
                });
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

        function formatarSkuExibicao(valor) {
            const sku = String(valor || '').trim();
            if (/^0\d{2}$/.test(sku)) {
                const numero = parseInt(sku, 10);
                if (numero >= 10 && numero <= 99) return String(numero);
            }
            return sku;
        }

        function obterNomeProdutoCadastro(item) {
            for (const campo of ['nome', 'produto', 'produto_bling', 'nome_bling']) {
                const valor = String((item || {})[campo] || '').trim();
                if (valor) return valor;
            }
            return 'Produto sem nome';
        }

        function obterFotoProdutoCadastro(item) {
            const foto = String((item || {}).foto || (item || {}).imagem || '').trim();
            if (!foto) return '';
            if (/^(https?:\/\/|\/api\/|\/img\/)/i.test(foto)) return foto;
            const nomeArquivo = foto.split(/[\\/]/).pop();
            return nomeArquivo ? `/api/cadastro/foto-arquivo/${encodeURIComponent(nomeArquivo)}` : '';
        }

        function produtoTreinamentoSelecionado() {
            const sku = String(aiTrainingSku.value || '').trim();
            if (!sku) return null;
            return state.produtosTreinamento.find((item) => String(item.sku || '') === sku) || null;
        }

        function renderizarSkuTreinamentoInfo() {
            const produto = produtoTreinamentoSelecionado();
            if (!produto) {
                aiTrainingSkuInfo.classList.add('hidden');
                aiTrainingSkuInfo.innerHTML = '';
                return;
            }

            const nome = obterNomeProdutoCadastro(produto);
            const sku = formatarSkuExibicao(produto.sku);
            const foto = obterFotoProdutoCadastro(produto);
            const fotoHtml = foto
                ? `<img src="${escapeHtml(foto)}" alt="${escapeHtml(nome)}" loading="lazy">`
                : '<span>Sem foto</span>';
            const meta = [
                produto.categoria ? `Categoria ${produto.categoria}` : '',
                produto.marca ? `Marca ${produto.marca}` : '',
                produto.mlb_ids ? `MLB ${produto.mlb_ids}` : ''
            ].filter(Boolean).join(' · ');

            aiTrainingSkuInfo.classList.remove('hidden');
            aiTrainingSkuInfo.innerHTML = `
                <div class="training-sku-thumb">${fotoHtml}</div>
                <div>
                    <div class="training-sku-name">SKU ${escapeHtml(sku)} · ${escapeHtml(nome)}</div>
                    <div class="training-sku-meta">${escapeHtml(meta || 'Dados do cadastro serão enviados junto com a pergunta.')}</div>
                </div>
            `;
        }

        function montarSeletorSkusTreinamento() {
            aiTrainingSku.innerHTML = '';
            const optDefault = document.createElement('option');
            optDefault.value = '';
            optDefault.textContent = state.produtosTreinamento.length
                ? 'Sem SKU selecionado'
                : 'Nenhum SKU cadastrado';
            aiTrainingSku.appendChild(optDefault);

            const ordenados = [...state.produtosTreinamento].sort((a, b) => (
                String(a.sku || '').localeCompare(String(b.sku || ''), undefined, { numeric: true, sensitivity: 'base' })
            ));
            ordenados.forEach((item) => {
                const sku = String(item.sku || '').trim();
                if (!sku) return;
                const option = document.createElement('option');
                option.value = sku;
                option.textContent = `${formatarSkuExibicao(sku)} - ${obterNomeProdutoCadastro(item)}`;
                aiTrainingSku.appendChild(option);
            });
            aiTrainingSku.disabled = !state.produtosTreinamento.length;
            renderizarSkuTreinamentoInfo();
        }

        async function carregarSkusTreinamentoAI() {
            if (state.produtosTreinamentoCarregados) return;
            aiTrainingSku.disabled = true;
            aiTrainingSku.innerHTML = '<option value="">Carregando SKUs...</option>';
            try {
                const response = await fetch('/api/mercadolivre/ia-treinamento/skus', {
                    headers: obterAuthHeaders(),
                    cache: 'no-store'
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(data.detail || 'Erro ao carregar SKUs.');
                const produtos = Array.isArray(data.produtos) ? data.produtos : [];
                state.produtosTreinamento = produtos.filter((item) => String((item || {}).sku || '').trim());
                state.produtosTreinamentoCarregados = true;
                montarSeletorSkusTreinamento();
            } catch (error) {
                aiTrainingSku.innerHTML = '<option value="">Erro ao carregar SKUs</option>';
                aiTrainingSku.disabled = true;
                aiTrainingSkuInfo.classList.add('hidden');
                aiTrainingSkuInfo.innerHTML = '';
                aiTrainingStatus.textContent = `Erro ao carregar SKUs: ${mensagemErro(error)}`;
            }
        }

        function rotuloTipoTreinamento(tipo = state.treinamentoTipo) {
            return tipo === 'pos_venda' ? 'pós-venda' : 'perguntas de anúncio';
        }

        function sincronizarOrientacoesTreinamentoAtual() {
            const tipo = state.treinamentoTipo === 'pos_venda' ? 'pos_venda' : 'perguntas_anuncio';
            state.treinamentoDados[tipo] = {
                ...(state.treinamentoDados[tipo] || {}),
                orientacoes: aiTrainingOrientacoes.value || ''
            };
        }

        function limparChatTreinamento() {
            if (!aiTrainingChat) return;
            aiTrainingChat.innerHTML = '<div class="training-chat-empty">Digite a pergunta do comprador para testar como a IA responderia.</div>';
            aiTrainingPergunta.value = '';
        }

        function atualizarStatusTreinamentoTipo() {
            const dados = state.treinamentoDados[state.treinamentoTipo] || {};
            const rotulo = rotuloTipoTreinamento();
            if (dados.updated_at) {
                aiTrainingStatus.textContent = `Orientações de ${rotulo} salvas em ${formatarData(dados.updated_at)}`;
            } else if (dados.orientacoes) {
                aiTrainingStatus.textContent = `Orientações de ${rotulo} carregadas.`;
            } else {
                aiTrainingStatus.textContent = `Nenhuma orientação de ${rotulo} salva ainda.`;
            }
        }

        function renderizarTipoTreinamento(limparChat = false) {
            const tipo = state.treinamentoTipo === 'pos_venda' ? 'pos_venda' : 'perguntas_anuncio';
            const dados = state.treinamentoDados[tipo] || { orientacoes: '', updated_at: null };

            aiTrainingTypeTabs.forEach((button) => {
                const ativo = button.dataset.trainingType === tipo;
                button.classList.toggle('active', ativo);
                button.setAttribute('aria-selected', ativo ? 'true' : 'false');
            });

            aiTrainingOrientacoesLabel.textContent = tipo === 'pos_venda'
                ? 'Orientações para pós-venda'
                : 'Orientações para perguntas de anúncio';
            aiTrainingOrientacoes.placeholder = tipo === 'pos_venda'
                ? 'Ex.: agradecer a compra, pedir fotos ou vídeo quando necessário, orientar garantia e troca sem prometer aprovação...'
                : 'Ex.: responder sem Markdown, não prometer prazo sem confirmação, pedir modelo/ano quando a aplicação do produto for incerta...';
            if (aiTrainingChatHead) {
                aiTrainingChatHead.textContent = tipo === 'pos_venda'
                    ? 'Simulação de pós-venda'
                    : 'Simulação de pergunta de anúncio';
            }
            aiTrainingOrientacoes.value = dados.orientacoes || '';
            atualizarStatusTreinamentoTipo();
            if (limparChat) limparChatTreinamento();
        }

        function trocarTipoTreinamento(tipo) {
            const novoTipo = tipo === 'pos_venda' ? 'pos_venda' : 'perguntas_anuncio';
            if (novoTipo === state.treinamentoTipo) return;
            sincronizarOrientacoesTreinamentoAtual();
            state.treinamentoTipo = novoTipo;
            renderizarTipoTreinamento(true);
        }

        async function carregarTreinamentoAI() {
            if (state.treinamentoCarregado) return;
            aiTrainingStatus.textContent = 'Carregando orientações...';
            try {
                const response = await fetch('/api/mercadolivre/ia-treinamento', {
                    headers: obterAuthHeaders(),
                    cache: 'no-store'
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(data.detail || 'Erro ao carregar orientações.');

                state.treinamentoDados.perguntas_anuncio = {
                    orientacoes: data.orientacoes_perguntas || data.orientacoes || '',
                    updated_at: data.updated_at_perguntas || data.updated_at || null
                };
                state.treinamentoDados.pos_venda = {
                    orientacoes: data.orientacoes_pos_venda || '',
                    updated_at: data.updated_at_pos_venda || null
                };
                state.treinamentoCarregado = true;
                renderizarTipoTreinamento(false);
            } catch (error) {
                aiTrainingStatus.textContent = `Erro ao carregar orientações: ${mensagemErro(error)}`;
            }
        }

        async function salvarTreinamentoAI() {
            sincronizarOrientacoesTreinamentoAtual();
            const tipoAtual = state.treinamentoTipo === 'pos_venda' ? 'pos_venda' : 'perguntas_anuncio';
            btnAiTrainingSalvar.disabled = true;
            btnAiTrainingSimular.disabled = true;
            aiTrainingStatus.textContent = 'Salvando orientações...';
            try {
                const response = await fetch('/api/mercadolivre/ia-treinamento', {
                    method: 'POST',
                    headers: {
                        ...obterAuthHeaders(),
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        tipo: tipoAtual,
                        orientacoes: aiTrainingOrientacoes.value || ''
                    })
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(data.detail || 'Erro ao salvar orientações.');

                state.treinamentoCarregado = true;
                state.treinamentoDados[tipoAtual] = {
                    orientacoes: aiTrainingOrientacoes.value || '',
                    updated_at: data.updated_at || new Date().toISOString()
                };
                atualizarStatusTreinamentoTipo();
                return data;
            } catch (error) {
                aiTrainingStatus.textContent = `Erro ao salvar orientações: ${mensagemErro(error)}`;
                throw error;
            } finally {
                btnAiTrainingSalvar.disabled = false;
                btnAiTrainingSimular.disabled = false;
            }
        }

        function adicionarMensagemTreinamentoChat(tipo, texto, opcoes = {}) {
            if (!aiTrainingChat) {
                return {
                    setText() {},
                    setError() {}
                };
            }

            const vazio = aiTrainingChat.querySelector('.training-chat-empty');
            if (vazio) vazio.remove();

            const mensagem = document.createElement('div');
            mensagem.className = `training-message ${tipo === 'user' ? 'user' : 'assistant'}${opcoes.error ? ' error' : ''}`;

            const papel = document.createElement('span');
            papel.className = 'training-message-role';
            papel.textContent = tipo === 'user' ? 'Comprador' : 'IA';

            const corpo = document.createElement('div');
            corpo.className = 'training-message-body';
            corpo.textContent = texto || '';

            mensagem.appendChild(papel);
            mensagem.appendChild(corpo);
            aiTrainingChat.appendChild(mensagem);
            aiTrainingChat.scrollTop = aiTrainingChat.scrollHeight;

            return {
                setText(novoTexto) {
                    corpo.textContent = novoTexto || '';
                    aiTrainingChat.scrollTop = aiTrainingChat.scrollHeight;
                },
                setError(novoTexto) {
                    mensagem.classList.add('error');
                    corpo.textContent = novoTexto || '';
                    aiTrainingChat.scrollTop = aiTrainingChat.scrollHeight;
                }
            };
        }

        async function simularTreinamentoAI() {
            const pergunta = (aiTrainingPergunta.value || '').trim();
            if (!pergunta) {
                aiTrainingStatus.textContent = 'Digite uma pergunta para simular.';
                aiTrainingPergunta.focus();
                return;
            }

            adicionarMensagemTreinamentoChat('user', pergunta);
            aiTrainingPergunta.value = '';
            const respostaChat = adicionarMensagemTreinamentoChat('assistant', 'Gerando resposta...');
            try {
                await salvarTreinamentoAI();
                btnAiTrainingSalvar.disabled = true;
                btnAiTrainingSimular.disabled = true;
                aiTrainingStatus.textContent = 'Gerando resposta de simulação...';

                const response = await fetch('/api/mercadolivre/ia-treinamento/simular', {
                    method: 'POST',
                    headers: {
                        ...obterAuthHeaders(),
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        pergunta,
                        tipo: state.treinamentoTipo,
                        sku: aiTrainingSku.value || '',
                        contexto: aiTrainingContexto.value || ''
                    })
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(data.detail || 'Erro ao simular resposta.');

                respostaChat.setText(data.resposta || 'A IA nao retornou uma resposta para esta simulacao.');
                aiTrainingStatus.textContent = 'Simulação gerada com as orientações salvas.';
            } catch (error) {
                respostaChat.setError(`Erro na simulação: ${mensagemErro(error)}`);
                aiTrainingStatus.textContent = `Erro na simulação: ${mensagemErro(error)}`;
            } finally {
                btnAiTrainingSalvar.disabled = false;
                btnAiTrainingSimular.disabled = false;
            }
        }

        document.querySelectorAll('.tab-button').forEach((button) => {
            button.addEventListener('click', () => ativarAba(button.dataset.tab));
        });
        statusFiltro.addEventListener('change', carregarPerguntas);
        btnRecarregar.addEventListener('click', carregarPerguntas);
        posVendaDias.addEventListener('change', () => {
            resetarPaginacaoPosVenda();
            carregarPosVenda(true);
        });
        btnPosVendaRecarregar.addEventListener('click', () => {
            resetarPaginacaoPosVenda();
            carregarPosVenda(true);
        });
        aiTrainingSku.addEventListener('change', renderizarSkuTreinamentoInfo);
        aiTrainingTypeTabs.forEach((button) => {
            button.addEventListener('click', () => trocarTipoTreinamento(button.dataset.trainingType));
        });
        btnAiTrainingSalvar.addEventListener('click', salvarTreinamentoAI);
        btnAiTrainingSimular.addEventListener('click', simularTreinamentoAI);
        aiTrainingPergunta.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault();
                simularTreinamentoAI();
            }
        });

        carregarLojas();
    