function intervaloChecagemReferenciaTodas(lojas) {
    const origem = (lojas || []).find((loja) => loja && loja.config_perguntas) || {};
    const config = origem.config_perguntas || {};
    return Math.max(0.25, Math.min(1440, Number(config.intervalo_minutos || 10) || 10));
}

function atualizarConfigPerguntasLocal(nomeLoja, configPerguntas) {
    const chave = chaveLojaCronometro(nomeLoja);
    if (!chave || !configPerguntas) return;
    const loja = state.lojas.find((item) => chaveLojaCronometro(item && item.nome) === chave);
    if (loja) loja.config_perguntas = configPerguntas;
}

const AUTOMACAO_PERGUNTAS_STATUS_INTERVAL_MS = 10000;
const AUTOMACAO_PERGUNTAS_STATUS_TIMEOUT_MS = 5000;
const AUTOMACAO_PERGUNTAS_REFRESH_DELAY_MS = 250;

function contagemNovidadesAutomacao(valor) {
    if (Array.isArray(valor)) return valor.length;
    const numero = Number(valor || 0);
    return Number.isFinite(numero) ? Math.max(0, numero) : 0;
}

function resultadoAutomacaoTemNovidade(resultado) {
    const dados = resultado && typeof resultado === 'object' ? resultado : {};
    const contagens = dados.contagens && typeof dados.contagens === 'object' ? dados.contagens : {};
    return contagemNovidadesAutomacao(contagens.novas_pendentes ?? dados.novas_pendentes) > 0
        || contagemNovidadesAutomacao(contagens.enviadas ?? dados.enviadas) > 0;
}

function normalizarIdsPerguntasAutomacao(valor) {
    if (!Array.isArray(valor)) return [];
    return Array.from(new Set(valor.map((item) => String(item || '').trim()).filter(Boolean)));
}

function statusTemContratoDeltaPerguntas(item) {
    if (!item || typeof item !== 'object') return false;
    return ['change_token', 'question_ids', 'new_question_ids', 'new_questions_count', 'question_snapshot_complete']
        .some((campo) => Object.prototype.hasOwnProperty.call(item, campo));
}

function registrarTokenStatusPerguntas(nomeLoja, item) {
    const chave = chaveLojaCronometro(nomeLoja);
    if (!chave || !item || item.question_snapshot_complete === false) return [];
    const token = String(item.change_token || '').trim();
    if (!token) return [];
    const tokens = state.automacaoPerguntasChangeTokensLojas || {};
    const tokenAnterior = String(tokens[chave] || '').trim();
    const snapshots = state.automacaoPerguntasQuestionIdsLojas || {};
    const temQuestionIds = Array.isArray(item.question_ids);
    const idsAtuais = temQuestionIds ? normalizarIdsPerguntasAutomacao(item.question_ids) : [];
    const tinhaBaselineIds = Object.prototype.hasOwnProperty.call(snapshots, chave);
    const idsAnteriores = new Set(normalizarIdsPerguntasAutomacao(snapshots[chave]));
    tokens[chave] = token;
    state.automacaoPerguntasChangeTokensLojas = tokens;
    if (temQuestionIds) {
        snapshots[chave] = idsAtuais;
        state.automacaoPerguntasQuestionIdsLojas = snapshots;
    }
    if (!tokenAnterior) {
        return temQuestionIds && tinhaBaselineIds
            ? idsAtuais.filter((id) => !idsAnteriores.has(id))
            : [];
    }
    if (tokenAnterior === token) return [];
    if (temQuestionIds && tinhaBaselineIds) {
        return idsAtuais.filter((id) => !idsAnteriores.has(id));
    }
    return normalizarIdsPerguntasAutomacao(item.new_question_ids);
}

function registrarSnapshotsPollPerguntas(data, lojaFallback = '') {
    const snapshots = Array.isArray(data && data.question_snapshots)
        ? data.question_snapshots
        : (Array.isArray(data && data.question_ids) ? [{
            loja: lojaFallback,
            question_ids: data.question_ids,
            question_snapshot_complete: data.question_snapshot_complete
        }] : []);
    const novidades = [];
    snapshots.forEach((snapshot) => {
        if (!snapshot || snapshot.question_snapshot_complete === false) return;
        const loja = String(snapshot.loja || lojaFallback || '').trim();
        const chave = chaveLojaCronometro(loja);
        if (!chave) return;
        const idsAtuais = normalizarIdsPerguntasAutomacao(snapshot.question_ids);
        const anteriores = state.automacaoPerguntasQuestionIdsLojas || {};
        const tinhaBaseline = Object.prototype.hasOwnProperty.call(anteriores, chave);
        const idsAnteriores = new Set(normalizarIdsPerguntasAutomacao(anteriores[chave]));
        anteriores[chave] = idsAtuais;
        state.automacaoPerguntasQuestionIdsLojas = anteriores;
        if (!tinhaBaseline) return;
        const idsNovos = idsAtuais.filter((id) => !idsAnteriores.has(id));
        if (idsNovos.length) novidades.push({ loja, ids: idsNovos });
    });
    return novidades;
}

function tipoStatusAutomacaoEhPerguntas(tipo) {
    const valor = String(tipo || '').trim().toLowerCase();
    return !valor || ['perguntas', 'pergunta', 'perguntas_anuncio', 'questions'].includes(valor);
}

function aplicarStatusBackendAutomacaoPerguntas(data) {
    const itens = Array.isArray(data && data.lojas) ? data.lojas : [];
    const statusAnterior = state.automacaoPerguntasStatusLojas || {};
    const porLoja = {};
    let ultimaChecagem = normalizarTimestampAutomacaoPerguntas(data && data.atualizado_em);
    let proximaChecagemGlobal = normalizarTimestampAutomacaoPerguntas(data && data.proxima_checagem_em);
    const lojasComNovidade = [];

    itens.forEach((item) => {
        if (!item || !tipoStatusAutomacaoEhPerguntas(item.tipo)) return;
        const nome = String(item.loja || '').trim();
        const chave = chaveLojaCronometro(nome);
        if (!chave) return;
        const atualizadoEm = normalizarTimestampAutomacaoPerguntas(item.ultima_checagem || item.updated_at);
        const proximaChecagem = normalizarTimestampAutomacaoPerguntas(item.proxima_checagem || item.next_check_at);
        const executando = item.executando === true || item.running === true;
        const sucesso = item.sucesso !== undefined ? item.sucesso : item.success;
        const contagens = item.contagens && typeof item.contagens === 'object' ? item.contagens : {};
        const statusAnteriorLoja = statusAnterior[chave];
        const anteriorEm = Number(statusAnteriorLoja && statusAnteriorLoja.updated_at_ms || 0);
        const statusAtual = {
            ...item,
            loja: nome,
            running: executando,
            success: sucesso,
            enviadas: Number(contagens.enviadas ?? item.enviadas ?? 0),
            novas_pendentes: Number(contagens.novas_pendentes ?? item.novas_pendentes ?? 0),
            erros: Number(contagens.erros ?? item.erros ?? 0),
            updated_at_ms: atualizadoEm,
            next_check_at_ms: proximaChecagem
        };
        const usaContratoDelta = statusTemContratoDeltaPerguntas(item);
        const idsNovos = usaContratoDelta ? registrarTokenStatusPerguntas(nome, item) : [];
        if (idsNovos.length) {
            lojasComNovidade.push(nome);
        } else if (!usaContratoDelta && anteriorEm > 0 && atualizadoEm > anteriorEm && resultadoAutomacaoTemNovidade(statusAtual)) {
            lojasComNovidade.push(nome);
        }
        porLoja[chave] = statusAtual;
        ultimaChecagem = Math.max(ultimaChecagem, atualizadoEm);
        if (proximaChecagem) {
            state.automacaoPerguntasNextChecks[chave] = proximaChecagem;
            proximaChecagemGlobal = proximaChecagemGlobal
                ? Math.min(proximaChecagemGlobal, proximaChecagem)
                : proximaChecagem;
        }
    });

    state.automacaoPerguntasStatusDisponivel = true;
    state.automacaoPerguntasStatusErro = '';
    state.automacaoPerguntasStatusLojas = porLoja;
    state.automacaoPerguntasWorkerIniciado = Boolean(data && data.worker_iniciado);
    state.automacaoPerguntasBackendExecutando = Boolean(data && data.executando)
        || Object.values(porLoja).some((item) => item.running === true);
    if (proximaChecagemGlobal) state.automacaoPerguntasProximaChecagemBackendEm = proximaChecagemGlobal;
    if (ultimaChecagem) registrarUltimaChecagemAutomacaoPerguntas(ultimaChecagem);
    lojasComNovidade.forEach((nome) => agendarRecarregamentoPerguntasAposPoll(nome));
    atualizarCronometrosAutomacao();
}

async function consultarStatusAutomacaoPerguntas() {
    if (state.automacaoPerguntasStatusCarregando) return null;
    const geracao = Number(state.automacaoPerguntasGeracao || 0);
    const controller = typeof AbortController === 'function' ? new AbortController() : null;
    const timeoutId = controller
        ? setTimeout(() => controller.abort(), AUTOMACAO_PERGUNTAS_STATUS_TIMEOUT_MS)
        : null;
    state.automacaoPerguntasStatusCarregando = true;
    try {
        const response = await fetch('/api/mercadolivre/perguntas/automacao/status', {
            method: 'GET',
            headers: obterAuthHeaders(),
            cache: 'no-store',
            ...(controller ? { signal: controller.signal } : {})
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || 'Status da checagem automática indisponível.');
        if (geracao !== Number(state.automacaoPerguntasGeracao || 0)) return null;
        aplicarStatusBackendAutomacaoPerguntas(data || {});
        return data;
    } catch (error) {
        if (geracao !== Number(state.automacaoPerguntasGeracao || 0)) return null;
        state.automacaoPerguntasStatusDisponivel = false;
        state.automacaoPerguntasStatusErro = mensagemErro(error);
        atualizarCronometrosAutomacao();
        return null;
    } finally {
        if (timeoutId) clearTimeout(timeoutId);
        if (geracao === Number(state.automacaoPerguntasGeracao || 0)) {
            state.automacaoPerguntasStatusCarregando = false;
        }
    }
}

function iniciarConsultaStatusAutomacaoPerguntas() {
    if (state.automacaoPerguntasStatusTimer) clearInterval(state.automacaoPerguntasStatusTimer);
    state.automacaoPerguntasStatusTimer = null;
    const consultaInicial = consultarStatusAutomacaoPerguntas();
    state.automacaoPerguntasStatusTimer = setInterval(
        () => { void consultarStatusAutomacaoPerguntas(); },
        AUTOMACAO_PERGUNTAS_STATUS_INTERVAL_MS
    );
    return consultaInicial;
}

function dadosNotificacaoVazios() {
    return {
        perguntas: 0,
        perguntasParcial: false,
        erro: ''
    };
}

function dadosNotificacaoLoja(nome, storeId = '') {
    if (nome === TODAS_LOJAS_VALUE) {
        const totais = state.notificacoes.totais || {};
        return {
            perguntas: Number(totais.perguntas || 0),
            perguntasParcial: Boolean(totais.perguntasParcial),
            erro: ''
        };
    }
    const matches = lojasMercadoLivreConectadas().filter(loja => loja.nome === nome);
    const canonical = String(storeId || (matches.length === 1 ? matches[0].store_id : '') || '');
    if (!canonical) return dadosNotificacaoVazios();
    return state.notificacoes.lojas[canonical] || (matches.length === 1 ? state.notificacoes.lojas[chaveLojaCronometro(nome)] : null) || dadosNotificacaoVazios();
}

function formatarContadorNotificacao(valor, parcial = false) {
    if (valor === null) return '?';
    const numero = Math.max(0, Number(valor || 0));
    if (numero <= 0) return '';
    const base = numero > 999 ? '999+' : String(numero);
    return parcial && !base.endsWith('+') ? `${base}+` : base;
}

function htmlNotificacoesLoja(nome, storeId = '') {
    const dados = dadosNotificacaoLoja(nome, storeId);
    const perguntas = formatarContadorNotificacao(dados.perguntas, dados.perguntasParcial);
    const partes = [];
    if (perguntas) {
        partes.push(`<span class="notification-pill" title="Perguntas não respondidas">${escapeHtml(perguntas)} perguntas</span>`);
    }
    return partes.join('');
}

function atualizarBadgeAba(elemento, valor, parcial = false) {
    if (!elemento) return;
    const texto = formatarContadorNotificacao(valor, parcial);
    elemento.textContent = texto || '0';
    elemento.classList.toggle('hidden', !texto);
}

function renderizarNotificacoes() {
    const totais = state.notificacoes.totais || {};
    atualizarBadgeAba(tabPerguntasNotificacao, totais.perguntas, totais.perguntasParcial);
    document.querySelectorAll('[data-store-notifications]').forEach((elemento) => {
        const nome = elemento.dataset.storeNotifications || '';
        elemento.innerHTML = htmlNotificacoesLoja(nome, elemento.dataset.storeId || '');
    });
}

function todasAsLojasSelecionadas() {
    return state.lojaSelecionada === TODAS_LOJAS_VALUE;
}

function lojaOrigemItem(item) {
    return String(
        item && (
            item.loja ||
            item.nome_loja ||
            item.store ||
            item._loja ||
            item.loja_nome ||
            ''
        ) || ''
    ).trim();
}

function rotuloEscopoSelecionado() {
    if (todasAsLojasSelecionadas()) return 'todas as contas conectadas';
    return state.lojaSelecionada || '';
}

function anexarLojaOrigem(item, nomeLoja) {
    return {
        ...(item || {}),
        loja: lojaOrigemItem(item) || String(nomeLoja || '').trim()
    };
}

function somarStatusResumoPerguntas(resultados) {
    return resultados.reduce((acc, resultado) => {
        const resumo = resultado && resultado.data && resultado.data.status_resumo ? resultado.data.status_resumo : {};
        Object.entries(resumo).forEach(([status, valor]) => {
            acc[status] = Number(acc[status] || 0) + Number(valor || 0);
        });
        return acc;
    }, {});
}

function textoProximaChecagemLoja(nome, loja) {
    const config = loja && loja.config_perguntas ? loja.config_perguntas : {};
    if (!loja || loja.mercadolivre_conectado !== true) return 'Proxima checagem: Mercado Livre desconectado.';
    if (config.responder_automaticamente !== true) return 'Proxima checagem: automacao desligada.';
    const chave = chaveLojaCronometro(nome);
    const statusBackend = state.automacaoPerguntasStatusLojas[chave] || {};
    if (statusBackend.running === true || state.automacaoPerguntasRodandoLojas.has(chave)) {
        return 'Checagem automatica em andamento agora.';
    }
    if (statusBackend.success === false && statusBackend.erro) {
        return `Ultima checagem falhou: ${String(statusBackend.erro).slice(0, 120)}`;
    }
    const proxima = Number(state.automacaoPerguntasNextChecks[chave] || 0);
    if (!Number.isFinite(proxima) || proxima <= 0) return 'Proxima checagem: aguardando agendamento...';
    const restante = proxima - Date.now();
    if (restante <= 1000) return 'Proxima checagem: agora.';
    return `Proxima checagem em ${formatarCronometroPerguntas(restante)}.`;
}

function textoResumoAutomacaoTodas() {
    const lojasAtivas = lojasComAutomacaoAtiva();
    if (!lojasAtivas.length) return 'Checagem automatica desligada nas contas conectadas.';

    const statusLojas = Object.values(state.automacaoPerguntasStatusLojas || {});
    const executandoBackend = state.automacaoPerguntasBackendExecutando === true
        || statusLojas.some((item) => item && item.running === true);
    const executandoLocal = state.automacaoPerguntasRodandoLojas.size;
    if (executandoBackend || executandoLocal) {
        const quantidade = Math.max(1, statusLojas.filter((item) => item && item.running === true).length, executandoLocal);
        return `${lojasAtivas.length} conta(s) ativa(s) · checando ${quantidade} agora.`;
    }

    const falhas = statusLojas.filter((item) => item && (item.success === false || item.erro)).length;
    const proximas = lojasAtivas
        .map((loja) => Number(state.automacaoPerguntasNextChecks[chaveLojaCronometro(loja.nome)] || 0))
        .filter((timestamp) => Number.isFinite(timestamp) && timestamp > 0);
    const proxima = proximas.length
        ? Math.min(...proximas)
        : Number(state.automacaoPerguntasProximaChecagemBackendEm || 0);
    const partes = [`${lojasAtivas.length} conta(s) ativa(s)`];
    if (proxima > 0) {
        const restante = proxima - Date.now();
        partes.push(restante <= 1000 ? 'proxima checagem agora' : `proxima em ${formatarCronometroPerguntas(restante)}`);
    } else {
        partes.push('aguardando agendamento');
    }
    if (falhas) partes.push(`${falhas} com falha na ultima checagem`);
    if (state.automacaoPerguntasStatusDisponivel && !state.automacaoPerguntasWorkerIniciado) {
        partes.push('worker do servidor iniciando; acompanhamento local ativo');
    }
    if (!state.automacaoPerguntasStatusDisponivel && state.automacaoPerguntasStatusErro) {
        partes.push('acompanhamento local ativo');
    }
    return `${partes.join(' · ')}.`;
}

function atualizarCronometrosAutomacao() {
    document.querySelectorAll('[data-next-check-loja]').forEach((elemento) => {
        const nome = elemento.dataset.nextCheckLoja || '';
        const loja = state.lojas.find((item) => chaveLojaCronometro(item.nome) === chaveLojaCronometro(nome));
        elemento.textContent = textoProximaChecagemLoja(nome, loja);
    });
    document.querySelectorAll('[data-automation-summary]').forEach((elemento) => {
        elemento.textContent = textoResumoAutomacaoTodas();
    });
}

function registrarProximaChecagemLoja(nome, timestamp) {
    const chave = chaveLojaCronometro(nome);
    if (!chave) return;
    state.automacaoPerguntasNextChecks[chave] = timestamp;
    atualizarCronometrosAutomacao();
}

function iniciarCronometroAutomacao() {
    if (state.automacaoPerguntasCountdownTimer) {
        clearInterval(state.automacaoPerguntasCountdownTimer);
        state.automacaoPerguntasCountdownTimer = null;
    }
    atualizarCronometrosAutomacao();
    state.automacaoPerguntasCountdownTimer = setInterval(atualizarCronometrosAutomacao, 1000);
}

function lojaParaConfigurarChecagemPerguntas() {
    const lojas = lojasMercadoLivreConectadas();
    if (!lojas.length) return null;
    if (todasAsLojasSelecionadas() && state.lojaConfiguracaoPerguntas === TODAS_LOJAS_VALUE) {
        return lojas[0] || null;
    }
    const nomePreferido = todasAsLojasSelecionadas()
        ? (state.lojaConfiguracaoPerguntas || (lojas[0] && lojas[0].nome) || '')
        : state.lojaSelecionada;
    return lojas.find((item) => chaveLojaCronometro(item.nome) === chaveLojaCronometro(nomePreferido)) || lojas[0] || null;
}

function renderizarControlesAutomacaoSelecionada() {
    if (!perguntasAutomationControls) return;
    const lojasConectadas = lojasMercadoLivreConectadas();
    const loja = lojaParaConfigurarChecagemPerguntas();
    if (!lojasConectadas.length || !loja || loja.mercadolivre_conectado !== true) {
        perguntasAutomationControls.innerHTML = '<span class="store-next-check automation-next-check">Escolha uma loja para configurar a checagem.</span>';
        return;
    }
    const alvoTodas = todasAsLojasSelecionadas() && state.lojaConfiguracaoPerguntas === TODAS_LOJAS_VALUE;
    const nome = String(loja.nome || '').trim();
    state.lojaConfiguracaoPerguntas = alvoTodas ? TODAS_LOJAS_VALUE : nome;
    const config = loja.config_perguntas || {};
    const intervaloMinutos = alvoTodas
        ? intervaloChecagemReferenciaTodas(lojasConectadas)
        : Math.max(0.25, Math.min(1440, Number(config.intervalo_minutos || 10) || 10));
    const seletorLoja = todasAsLojasSelecionadas() ? `
            <select id="perguntas-loja-intervalo" aria-label="Loja para configurar checagem">
                <option value="${TODAS_LOJAS_VALUE}" ${alvoTodas ? 'selected' : ''}>Todas as contas conectadas</option>
                ${lojasConectadas.map((item) => {
                    const itemNome = String(item.nome || '').trim();
                    return `<option value="${escapeHtml(itemNome)}" ${!alvoTodas && chaveLojaCronometro(itemNome) === chaveLojaCronometro(nome) ? 'selected' : ''}>${escapeHtml(itemNome)}</option>`;
                }).join('')}
            </select>
        ` : `<strong>${escapeHtml(nome)}</strong>`;
    const textoStatus = alvoTodas ? textoResumoAutomacaoTodas() : textoProximaChecagemLoja(nome, loja);
    const atributoProxima = alvoTodas
        ? ' data-automation-summary="true"'
        : ` data-next-check-loja="${escapeHtml(nome)}"`;
    const statusAutomacao = textoStatus
        ? `<span class="store-next-check automation-next-check"${atributoProxima}>${escapeHtml(textoStatus)}</span>`
        : '';
    perguntasAutomationControls.innerHTML = `
        <label class="automation-interval-control">
            <span>Checagem automatica</span>
            ${seletorLoja}
            <span>a cada</span>
            <input id="perguntas-intervalo-global" type="number" min="0.25" max="1440" step="0.25" value="${intervaloMinutos}" aria-label="Intervalo de checagem em minutos">
            <span>minuto(s)</span>
            <button class="store-config-save" type="button" id="btn-salvar-intervalo-perguntas">Aplicar</button>
            ${statusAutomacao}
        </label>
    `;
    const input = document.getElementById('perguntas-intervalo-global');
    const button = document.getElementById('btn-salvar-intervalo-perguntas');
    const select = document.getElementById('perguntas-loja-intervalo');
    const salvar = () => salvarIntervaloLojaSelecionada();
    if (button) button.addEventListener('click', salvar);
    if (select) {
        select.addEventListener('change', () => {
            state.lojaConfiguracaoPerguntas = select.value || '';
            renderizarControlesAutomacaoSelecionada();
        });
    }
    if (input) {
        input.addEventListener('keydown', (event) => {
            if (event.key === 'Enter') {
                event.preventDefault();
                salvar();
                input.blur();
            }
        });
        input.addEventListener('change', salvar);
    }
    atualizarCronometrosAutomacao();
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
    if (nome === 'perguntas' && state.automacaoPerguntasRefreshLojas.size) {
        agendarRecarregamentoPerguntasAposPoll('', 0);
    }
    if (nome === 'solicitacao') {
        window.JKSolicitacoes?.carregar(1);
    }
    if (nome === 'pos-venda' && state.lojaSelecionada) {
        carregarPosVenda();
    }
    if (nome === 'mediacao' && state.lojaSelecionada && !todasAsLojasSelecionadas()) {
        carregarMediacoes();
    }
    if (nome === 'mediacao' && todasAsLojasSelecionadas()) {
        atualizarCabecalhoMediacao();
        resetarMediacoes();
        mediacaoStatus.textContent = 'Selecione uma loja especifica para consultar mediacoes.';
    }
    if (nome === 'treinar-ai') {
        sincronizarLojaTreinamento();
        atualizarTreinamentoVisivel(true);
    }
}

async function buscarContadorPerguntasNaoRespondidas(nomeLoja) {
    const loja = lojasMercadoLivreConectadas().find(item => item.nome === nomeLoja);
    const data = await window.JKPerguntasLoading.request('resumo', { store_id: loja?.store_id || '', metricas: 'false' });
    const resumo = (data.lojas || [])[0];
    if (!resumo || resumo.perguntas == null) throw new Error(resumo?.erro || 'Contador indisponível');
    return { total: Number(resumo.perguntas), parcial: Boolean(resumo.partial || resumo.stale) };
}

async function carregarContadoresNotificacoes(forcar = false, nomesLojas = []) {
    const lojas = lojasMercadoLivreConectadas();
    if (!lojas.length || state.notificacoes.carregando) return;
    if (!forcar && Date.now() - Number(state.notificacoes.atualizadoEm || 0) < 60000) return;
    state.notificacoes.carregando = true;
    const selecionadas = nomesLojas.length ? lojas.filter(loja => nomesLojas.includes(chaveLojaCronometro(loja.nome))) : lojas;
    try {
        const data = await window.JKPerguntasLoading.request('resumo', { store_ids: selecionadas.map(loja => loja.store_id).join(','), forcar: String(forcar), metricas: 'false' });
        const porLoja = { ...(state.notificacoes.lojas || {}) }, erros = { ...(state.notificacoes.erros || {}) };
        for (const loja of selecionadas) {
            const key = String(loja.store_id);
            const resumo = (data.lojas || []).find(item => String(item.store_id) === String(loja.store_id));
            if (!resumo || resumo.perguntas == null || resumo.erro) {
                erros[key] = resumo?.erro || 'Contador indisponível';
                porLoja[key] = { ...(porLoja[key] || {}), perguntas: porLoja[key]?.perguntas ?? null, perguntasParcial: true, erro: erros[key] };
            } else {
                delete erros[key];
                porLoja[key] = { perguntas: Number(resumo.perguntas), perguntasParcial: Boolean(resumo.partial || resumo.stale), erro: '' };
            }
        }
        state.notificacoes = { carregando: false, atualizadoEm: Date.now(), lojas: porLoja, erros,
            totais: { perguntas: Object.values(porLoja).reduce((sum, item) => sum + Number(item.perguntas || 0), 0), perguntasParcial: Object.values(porLoja).some(item => item.perguntasParcial) } };
    } catch (error) {
        if (error.status === 401 || error.status === 403) state.notificacoes = { lojas: {}, totais: {}, erros: {} };
        else {
            state.notificacoes.totais.perguntasParcial = true;
            selecionadas.forEach(loja => { const key = String(loja.store_id); state.notificacoes.lojas[key] = { ...(state.notificacoes.lojas[key] || {}), perguntas: state.notificacoes.lojas[key]?.perguntas ?? null, perguntasParcial: true, erro: 'Contador indisponível' }; });
        }
    } finally { state.notificacoes.carregando = false; renderizarNotificacoes(); }
}

function renderizarLojas() {
    if (!state.lojas.length) {
        const vazio = '<div class="empty-state"><div><h2>Nenhuma loja encontrada</h2><p>Cadastre uma loja em Integrações para começar.</p></div></div>';
        lojasGrid.innerHTML = vazio;
        posVendaLojasGrid.innerHTML = vazio;
        perguntasStatus.textContent = 'Nenhuma loja cadastrada em Integrações.';
        perguntasPagination.classList.add('hidden');
        perguntasPagination.innerHTML = '';
        renderizarControlesAutomacaoSelecionada();
        return;
    }

    const lojasConectadas = lojasMercadoLivreConectadas();
    const todasActive = todasAsLojasSelecionadas();
    const todasHtml = lojasConectadas.length ? `
        <div class="store-card all-stores-card ${todasActive ? 'active' : ''}" data-loja="${TODAS_LOJAS_VALUE}" tabindex="0" aria-disabled="false">
            <span class="store-card-heading">
                <span class="store-name">Todas as contas</span>
            </span>
            <span class="store-notifications" data-store-notifications="${TODAS_LOJAS_VALUE}">${htmlNotificacoesLoja(TODAS_LOJAS_VALUE)}</span>
        </div>
    ` : '';
    const lojasHtml = state.lojas.map((loja) => {
        const nome = String(loja.nome || '').trim();
        const conectado = loja.mercadolivre_conectado === true;
        const mlStatus = String(loja.mercadolivre_status || '').trim();
        const precisaReconectar = !conectado && mlStatus === 'reautenticar';
        const mlMotivo = String(loja.mercadolivre_motivo || '').trim();
        const badgeClasse = conectado ? 'ok' : (precisaReconectar ? 'danger' : 'warn');
        const badgeTexto = precisaReconectar ? 'Reconectar ML' : 'Conectar ML';
        const dotClasse = conectado ? '' : (precisaReconectar ? 'danger' : 'warn');
        const dotTitulo = conectado ? 'Mercado Livre conectado' : badgeTexto;
        const active = state.lojaSelecionadaStoreId ? state.lojaSelecionadaStoreId === String(loja.store_id) : nome === state.lojaSelecionada;
        const config = loja.config_perguntas || {};
        const intervaloMinutos = Math.max(0.25, Math.min(1440, Number(config.intervalo_minutos || 10) || 10));
        return `
            <div class="store-card ${active ? 'active' : ''} ${conectado ? '' : 'disabled'}" data-loja="${escapeHtml(nome)}" data-store-id="${escapeHtml(loja.store_id || '')}" tabindex="${conectado ? '0' : '-1'}" aria-disabled="${conectado ? 'false' : 'true'}">
                <span class="store-card-heading">
                    <span class="store-name">${escapeHtml(nome)}</span>
                    <span class="store-connection-dot ${dotClasse}" title="${escapeHtml(dotTitulo)}" aria-label="${escapeHtml(dotTitulo)}"></span>
                </span>
                <span class="store-meta">
                    ${conectado ? '' : `<span class="badge ${badgeClasse}" title="${escapeHtml(mlMotivo)}">${badgeTexto}</span>`}
                </span>
                <span class="store-notifications" data-store-notifications="${escapeHtml(nome)}" data-store-id="${escapeHtml(loja.store_id || '')}">${htmlNotificacoesLoja(nome, loja.store_id)}</span>
                <span class="store-options">
                    <label class="store-option">
                        <input class="store-config-checkbox" type="checkbox" data-config="responder_automaticamente" ${config.responder_automaticamente ? 'checked' : ''}>
                        <span>Gerar sugestões automaticamente</span>
                    </label>
                    <label class="store-option">
                        <input class="store-config-checkbox" type="checkbox" data-config="solicitar_aprovacao" checked disabled>
                        <span>Aprovação obrigatória antes de qualquer envio</span>
                    </label>
                    <label class="store-option whatsapp-approval-option">
                        <input class="store-config-checkbox" type="checkbox" data-config="notificar_whatsapp_aprovacoes" ${config.notificar_whatsapp_aprovacoes ? 'checked' : ''}>
                        <span>Enviar sugestão ao WhatsApp cadastrado com Aprovar, Negar e Gerar nova resposta</span>
                    </label>
                    <label class="store-option interval-option">
                        <span>Checar perguntas a cada</span>
                        <input class="store-config-interval" type="number" min="0.25" max="1440" step="0.25" data-config="intervalo_minutos" value="${intervaloMinutos}" aria-label="Intervalo de checagem em minutos para ${escapeHtml(nome)}">
                        <span>minuto(s)</span>
                        <button class="store-config-save" type="button" data-config-action="salvar_intervalo">Aplicar</button>
                        <span class="store-next-check" data-next-check-loja="${escapeHtml(nome)}">${escapeHtml(textoProximaChecagemLoja(nome, loja))}</span>
                    </label>
                </span>
            </div>
        `;
    }).join('');

    lojasGrid.innerHTML = todasHtml + lojasHtml;
    posVendaLojasGrid.innerHTML = todasHtml + lojasHtml;
    vincularEventosLojas(lojasGrid);
    vincularEventosLojas(posVendaLojasGrid);
    renderizarControlesAutomacaoSelecionada();
    montarSeletorEscopoTreinamento();
    renderizarNotificacoes();
}

function vincularEventosLojas(container) {
    container.querySelectorAll('.store-card').forEach((card) => {
        card.addEventListener('click', (event) => {
            if (event.target.closest('.store-option')) return;
            if (card.classList.contains('disabled')) return;
            selecionarLoja(card.dataset.loja || '', card.dataset.storeId || '');
        });
        card.addEventListener('keydown', (event) => {
            if (!['Enter', ' '].includes(event.key)) return;
            if (event.target.closest('.store-option')) return;
            if (card.classList.contains('disabled')) return;
            event.preventDefault();
            selecionarLoja(card.dataset.loja || '', card.dataset.storeId || '');
        });
        card.querySelectorAll('.store-config-checkbox').forEach((input) => {
            input.addEventListener('click', (event) => event.stopPropagation());
            input.addEventListener('change', () => salvarConfigLoja(card));
        });
        card.querySelectorAll('.store-config-interval').forEach((input) => {
            input.addEventListener('click', (event) => event.stopPropagation());
            input.addEventListener('keydown', (event) => {
                event.stopPropagation();
                if (event.key === 'Enter') {
                    event.preventDefault();
                    salvarConfigLoja(card);
                    input.blur();
                }
            });
            input.addEventListener('change', () => salvarConfigLoja(card));
            input.addEventListener('blur', () => salvarConfigLoja(card));
        });
        card.querySelectorAll('[data-config-action="salvar_intervalo"]').forEach((button) => {
            button.addEventListener('mousedown', (event) => event.preventDefault());
            button.addEventListener('click', (event) => {
                event.preventDefault();
                event.stopPropagation();
                salvarConfigLoja(card);
            });
        });
    });
}

async function salvarConfigLoja(card) {
    const nome = card.dataset.loja || '';
    if (!nome) return;
    const responderAutomaticamente = !!card.querySelector('[data-config="responder_automaticamente"]')?.checked;
    const solicitarAprovacao = !!card.querySelector('[data-config="solicitar_aprovacao"]')?.checked;
    const notificarWhatsappAprovacoes = !!card.querySelector('[data-config="notificar_whatsapp_aprovacoes"]')?.checked;
    const lojaAtual = state.lojas.find((item) => String(item.nome || '') === nome);
    const configAtual = lojaAtual && lojaAtual.config_perguntas ? lojaAtual.config_perguntas : {};
    const intervaloInput = card.querySelector('[data-config="intervalo_minutos"]');
    const intervaloMinutos = Math.max(0.25, Math.min(1440, Number((intervaloInput && intervaloInput.value) || configAtual.intervalo_minutos || 10) || 10));
    if (intervaloInput) intervaloInput.value = String(intervaloMinutos);
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
                solicitar_aprovacao: solicitarAprovacao,
                notificar_whatsapp_aprovacoes: notificarWhatsappAprovacoes,
                habilitar_pos_venda_automatico: false,
                intervalo_minutos: intervaloMinutos
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

async function salvarIntervaloLojaSelecionada() {
    const alvoTodas = todasAsLojasSelecionadas() && state.lojaConfiguracaoPerguntas === TODAS_LOJAS_VALUE;
    const loja = lojaParaConfigurarChecagemPerguntas();
    if (!loja) return;
    state.lojaConfiguracaoPerguntas = alvoTodas ? TODAS_LOJAS_VALUE : String(loja.nome || '').trim();
    const input = document.getElementById('perguntas-intervalo-global');
    const config = loja.config_perguntas || {};
    const intervaloMinutos = Math.max(0.25, Math.min(1440, Number(input?.value || config.intervalo_minutos || 10) || 10));
    if (input) input.value = String(intervaloMinutos);
    try {
        if (alvoTodas) {
            const response = await fetch('/api/mercadolivre/perguntas/lojas/config-lote', {
                method: 'POST',
                headers: {
                    ...obterAuthHeaders(),
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    intervalo_minutos: intervaloMinutos,
                    somente_conectadas: true
                })
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(data.detail || 'Erro ao salvar intervalo em lote.');
            (data.atualizadas || []).forEach((item) => {
                atualizarConfigPerguntasLocal(item.loja, item.config_perguntas);
            });
            lojasStatus.textContent = `Intervalo aplicado em ${Number(data.total || 0)} conta(s).`;
            renderizarLojas();
            iniciarAutomacaoPerguntas();
            return;
        }

        const response = await fetch('/api/mercadolivre/perguntas/lojas/config', {
            method: 'POST',
            headers: {
                ...obterAuthHeaders(),
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                loja: loja.nome || '',
                responder_automaticamente: config.responder_automaticamente === true,
                solicitar_aprovacao: config.solicitar_aprovacao === true,
                notificar_whatsapp_aprovacoes: config.notificar_whatsapp_aprovacoes === true,
                habilitar_pos_venda_automatico: false,
                intervalo_minutos: intervaloMinutos
            })
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || 'Erro ao salvar intervalo.');
        loja.config_perguntas = data.config_perguntas || {};
        lojasStatus.textContent = 'Intervalo de checagem salvo.';
        renderizarLojas();
        iniciarAutomacaoPerguntas();
    } catch (error) {
        alert(`Erro ao salvar intervalo: ${mensagemErro(error)}`);
        renderizarControlesAutomacaoSelecionada();
    }
}

function aprovacaoEhPosVenda(aprovacao) {
    if (!aprovacao || typeof aprovacao !== 'object') return false;
    return [
        aprovacao.tipo,
        aprovacao.approval_type,
        aprovacao.origem,
        aprovacao.ia_origem,
        aprovacao.ia_finalidade
    ].some((valor) => {
        const marcador = String(valor || '').trim().toLowerCase();
        return marcador.includes('pos_venda') || marcador.includes('pos-venda');
    });
}

function notificarAprovacaoSidebar(aprovacao) {
    if (aprovacaoEhPosVenda(aprovacao)) return;
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
        const pendentes = (Array.isArray(data.pendentes) ? data.pendentes : [])
            .filter((item) => !aprovacaoEhPosVenda(item));
        const idsPendentes = new Set(pendentes.map((item) => String((item && item.id) || '').trim()).filter(Boolean));
        Array.from(state.aprovacoesNotificadas).forEach((id) => {
            if (idsPendentes.has(id)) return;
            state.aprovacoesNotificadas.delete(id);
            if (typeof window.JKIASidebarResolveApproval === 'function') {
                window.JKIASidebarResolveApproval(id, 'Respondida fora da aprovacao.');
            } else {
                window.dispatchEvent(new CustomEvent('jk-ia-approval-resolved', {
                    detail: { id, mensagem: 'Respondida fora da aprovacao.' }
                }));
            }
        });
        pendentes.forEach(notificarAprovacaoSidebar);
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

function lojasComAutomacaoAtiva() {
    return state.lojas.filter((loja) => {
        const config = loja.config_perguntas || {};
        return loja.mercadolivre_conectado === true && config.responder_automaticamente === true;
    });
}

function intervaloAutomacaoLojaMs(loja) {
    const config = loja && loja.config_perguntas ? loja.config_perguntas : {};
    const minutos = Math.max(0.25, Math.min(1440, Number(config.intervalo_minutos || 10) || 10));
    return Math.round(minutos * 60 * 1000);
}

function lojaComAutomacaoPerguntasAtiva(nomeLoja) {
    const chave = chaveLojaCronometro(nomeLoja);
    if (!chave) return existeLojaComAutomacaoAtiva();
    return lojasComAutomacaoAtiva().some((loja) => chaveLojaCronometro(loja.nome) === chave);
}

function deveExecutarPollFrontendPerguntas() {
    return !(state.automacaoPerguntasStatusDisponivel && state.automacaoPerguntasWorkerIniciado);
}

function lojaComNovidadeAfetaSelecaoPerguntas(nomeLoja) {
    if (todasAsLojasSelecionadas()) return true;
    return chaveLojaCronometro(nomeLoja) === chaveLojaCronometro(state.lojaSelecionada);
}

function agendarRecarregamentoPerguntasAposPoll(nomeLoja = '', delay = AUTOMACAO_PERGUNTAS_REFRESH_DELAY_MS) {
    const chave = chaveLojaCronometro(nomeLoja);
    if (chave) state.automacaoPerguntasRefreshLojas.add(chave);
    state.automacaoPerguntasRefreshPendente = true;
    if (state.automacaoPerguntasRefreshTimer) return;
    const geracao = Number(state.automacaoPerguntasGeracao || 0);
    state.automacaoPerguntasRefreshTimer = setTimeout(async () => {
        state.automacaoPerguntasRefreshTimer = null;
        if (geracao !== Number(state.automacaoPerguntasGeracao || 0)) return;
        if (!state.automacaoPerguntasRefreshPendente) return;
        if (state.carregandoPerguntas) {
            agendarRecarregamentoPerguntasAposPoll(nomeLoja, Math.max(250, Number(delay) || 0));
            return;
        }

        state.automacaoPerguntasRefreshPendente = false;
        const lojasPendentes = Array.from(state.automacaoPerguntasRefreshLojas || []);
        lojasMercadoLivreConectadas().filter(loja => lojasPendentes.includes(chaveLojaCronometro(loja.nome))).forEach(loja => window.JKPerguntasLoading?.invalidar(loja.store_id));
        const tarefas = [Promise.resolve().then(() => carregarContadoresNotificacoes(true, lojasPendentes))];
        const abaPerguntas = document.getElementById('aba-perguntas');
        const afetaSelecao = lojasPendentes.some((loja) => lojaComNovidadeAfetaSelecaoPerguntas(loja));
        if (afetaSelecao && abaPerguntas && abaPerguntas.classList.contains('active')) {
            tarefas.push(Promise.resolve()
                .then(() => carregarPerguntas(state.paginaPerguntas || 1, { background: true, preservarInteracao: true }))
                .then((atualizou) => {
                    if (!atualizou) return;
                    if (todasAsLojasSelecionadas()) state.automacaoPerguntasRefreshLojas.clear();
                    else state.automacaoPerguntasRefreshLojas.delete(chaveLojaCronometro(state.lojaSelecionada));
                }));
        }
        await Promise.allSettled(tarefas);
    }, Math.max(0, Number(delay) || 0));
}

async function executarAutomacaoPerguntas(lojaNome = '', geracaoEsperada = state.automacaoPerguntasGeracao) {
    const chave = chaveLojaCronometro(lojaNome) || '__todas__';
    if (!deveExecutarPollFrontendPerguntas()) return null;
    if (!lojaComAutomacaoPerguntasAtiva(lojaNome)) return null;
    if (state.automacaoPerguntasRodandoLojas.has(chave)) return null;
    state.automacaoPerguntasRodandoLojas.add(chave);
    atualizarCronometrosAutomacao();
    try {
        const params = new URLSearchParams({ max_per_store: '3' });
        if (lojaNome) params.set('loja', lojaNome);
        const response = await fetch(`/api/mercadolivre/perguntas/automacao/poll?${params.toString()}`, {
            method: 'POST',
            headers: obterAuthHeaders(),
            cache: 'no-store'
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || 'Erro na automação de perguntas.');
        if (Number(geracaoEsperada) !== Number(state.automacaoPerguntasGeracao || 0)) return data;

        const pendentes = [
            ...(Array.isArray(data.novas_pendentes) ? data.novas_pendentes : []),
            ...(Array.isArray(data.pendentes) ? data.pendentes : [])
        ];
        pendentes.forEach(notificarAprovacaoSidebar);
        registrarUltimaChecagemAutomacaoPerguntas(data.atualizado_em || Date.now());
        const novidadesSnapshot = registrarSnapshotsPollPerguntas(data, lojaNome);
        if (novidadesSnapshot.length) {
            novidadesSnapshot.forEach((novidade) => agendarRecarregamentoPerguntasAposPoll(novidade.loja));
        } else if (!Array.isArray(data.question_snapshots) && !Array.isArray(data.question_ids) && resultadoAutomacaoTemNovidade(data)) {
            agendarRecarregamentoPerguntasAposPoll(lojaNome);
        }
        void consultarStatusAutomacaoPerguntas();
        return data;
    } catch (error) {
        console.warn(`[Perguntas IA] Falha ao executar automação de ${lojaNome || 'todas as contas'}:`, error);
        return null;
    } finally {
        state.automacaoPerguntasRodandoLojas.delete(chave);
        atualizarCronometrosAutomacao();
    }
}

function pararAutomacaoPerguntas() {
    state.automacaoPerguntasGeracao = Number(state.automacaoPerguntasGeracao || 0) + 1;
    (state.automacaoPerguntasTimers || []).forEach((timerId) => clearInterval(timerId));
    state.automacaoPerguntasTimers = [];
    (state.automacaoPerguntasStartupTimers || []).forEach((timerId) => clearTimeout(timerId));
    state.automacaoPerguntasStartupTimers = [];
    if (state.automacaoPerguntasCountdownTimer) clearInterval(state.automacaoPerguntasCountdownTimer);
    state.automacaoPerguntasCountdownTimer = null;
    if (state.automacaoPerguntasStatusTimer) clearInterval(state.automacaoPerguntasStatusTimer);
    state.automacaoPerguntasStatusTimer = null;
    if (state.automacaoPerguntasRefreshTimer) clearTimeout(state.automacaoPerguntasRefreshTimer);
    state.automacaoPerguntasRefreshTimer = null;
    state.automacaoPerguntasRefreshPendente = false;
    state.automacaoPerguntasStatusCarregando = false;
    state.automacaoPerguntasRodandoLojas.clear();
    state.automacaoPerguntasRefreshLojas.clear();
    state.automacaoPerguntasNextChecks = {};
}

function iniciarAutomacaoPerguntas() {
    pararAutomacaoPerguntas();
    const geracao = Number(state.automacaoPerguntasGeracao || 0);
    void carregarAprovacoesPendentes();
    iniciarCronometroAutomacao();
    const consultaStatusInicial = iniciarConsultaStatusAutomacaoPerguntas();

    const lojasAtivas = lojasComAutomacaoAtiva();
    if (!lojasAtivas.length) {
        atualizarCronometrosAutomacao();
        return;
    }

    lojasAtivas.forEach((loja, index) => {
        const nomeLoja = String(loja.nome || '').trim();
        const intervaloMs = intervaloAutomacaoLojaMs(loja);
        const delayPerguntas = 250 + (index * 250);
        registrarProximaChecagemLoja(nomeLoja, Date.now() + delayPerguntas);

        const startupPerguntas = setTimeout(async () => {
            await consultaStatusInicial;
            if (geracao !== Number(state.automacaoPerguntasGeracao || 0)) return;
            if (!deveExecutarPollFrontendPerguntas()) return;
            void executarAutomacaoPerguntas(nomeLoja, geracao);
            registrarProximaChecagemLoja(nomeLoja, Date.now() + intervaloMs);
        }, delayPerguntas);
        state.automacaoPerguntasStartupTimers.push(startupPerguntas);

        const timerId = setInterval(() => {
            if (geracao !== Number(state.automacaoPerguntasGeracao || 0)) return;
            if (!deveExecutarPollFrontendPerguntas()) return;
            registrarProximaChecagemLoja(nomeLoja, Date.now() + intervaloMs);
            void executarAutomacaoPerguntas(nomeLoja, geracao);
        }, intervaloMs);
        state.automacaoPerguntasTimers.push(timerId);
    });
}
