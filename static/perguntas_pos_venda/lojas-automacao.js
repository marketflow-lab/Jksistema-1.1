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

function dadosNotificacaoVazios() {
    return {
        perguntas: 0,
        perguntasParcial: false,
        posVenda: 0,
        posVendaParcial: false,
        erro: ''
    };
}

function dadosNotificacaoLoja(nome) {
    if (nome === TODAS_LOJAS_VALUE) {
        const totais = state.notificacoes.totais || {};
        return {
            perguntas: Number(totais.perguntas || 0),
            perguntasParcial: Boolean(totais.perguntasParcial),
            posVenda: Number(totais.posVenda || 0),
            posVendaParcial: Boolean(totais.posVendaParcial),
            erro: ''
        };
    }
    return state.notificacoes.lojas[chaveLojaCronometro(nome)] || dadosNotificacaoVazios();
}

function formatarContadorNotificacao(valor, parcial = false) {
    const numero = Math.max(0, Number(valor || 0));
    if (numero <= 0) return '';
    const base = numero > 999 ? '999+' : String(numero);
    return parcial && !base.endsWith('+') ? `${base}+` : base;
}

function htmlNotificacoesLoja(nome) {
    const dados = dadosNotificacaoLoja(nome);
    const perguntas = formatarContadorNotificacao(dados.perguntas, dados.perguntasParcial);
    const posVenda = formatarContadorNotificacao(dados.posVenda, dados.posVendaParcial);
    const partes = [];
    if (perguntas) {
        partes.push(`<span class="notification-pill" title="Perguntas não respondidas">${escapeHtml(perguntas)} perguntas</span>`);
    }
    if (posVenda) {
        partes.push(`<span class="notification-pill pos-sale" title="Conversas de pós-venda não lidas">${escapeHtml(posVenda)} pós-venda</span>`);
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
    atualizarBadgeAba(tabPosVendaNotificacao, totais.posVenda, totais.posVendaParcial);
    document.querySelectorAll('[data-store-notifications]').forEach((elemento) => {
        const nome = elemento.dataset.storeNotifications || '';
        elemento.innerHTML = htmlNotificacoesLoja(nome);
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
    const proxima = Number(state.automacaoPerguntasNextChecks[chaveLojaCronometro(nome)] || 0);
    if (!Number.isFinite(proxima) || proxima <= 0) return 'Proxima checagem: aguardando agendamento...';
    const restante = proxima - Date.now();
    if (restante <= 1000) return 'Proxima checagem: agora.';
    return `Proxima checagem em ${formatarCronometroPerguntas(restante)}.`;
}

function atualizarCronometrosAutomacao() {
    document.querySelectorAll('[data-next-check-loja]').forEach((elemento) => {
        const nome = elemento.dataset.nextCheckLoja || '';
        const loja = state.lojas.find((item) => chaveLojaCronometro(item.nome) === chaveLojaCronometro(nome));
        elemento.textContent = textoProximaChecagemLoja(nome, loja);
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
    const textoStatus = alvoTodas ? '' : textoProximaChecagemLoja(nome, loja);
    const atributoProxima = alvoTodas ? '' : ` data-next-check-loja="${escapeHtml(nome)}"`;
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
        carregarTreinamentoAI();
        carregarSkusTreinamentoAI();
    }
}

async function buscarContadorPerguntasNaoRespondidas(nomeLoja) {
    const params = new URLSearchParams({
        loja: nomeLoja,
        status: 'UNANSWERED',
        carregar_todas: 'false',
        offset: '0',
        limit: '1'
    });
    const response = await fetch(`/api/mercadolivre/perguntas?${params.toString()}`, {
        headers: obterAuthHeaders(),
        cache: 'no-store'
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `Erro ao contar perguntas de ${nomeLoja}.`);
    return {
        total: Number(data.total || data.status_resumo?.UNANSWERED || data.retornadas || 0),
        parcial: false
    };
}

async function buscarContadorPosVendaNaoLidas(nomeLoja) {
    const params = new URLSearchParams({
        loja: nomeLoja,
        dias: posVendaDias?.value || '365',
        offset: '0',
        limit: '20',
        max_orders: '10000',
        nao_lidas: 'true'
    });
    const response = await fetch(`/api/mercadolivre/pos-venda/conversas?${params.toString()}`, {
        headers: obterAuthHeaders(),
        cache: 'no-store'
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `Erro ao contar pós-venda de ${nomeLoja}.`);
    const conversas = Array.isArray(data.conversas) ? data.conversas.length : 0;
    return {
        total: Number(data.conversas_total || data.conversas_nao_lidas_total || conversas || 0),
        parcial: data.has_next === true || data.next_offset !== null && data.next_offset !== undefined
    };
}

async function carregarContadoresNotificacoes(forcar = false) {
    const lojas = lojasMercadoLivreConectadas();
    if (!lojas.length) {
        state.notificacoes = {
            carregando: false,
            atualizadoEm: Date.now(),
            lojas: {},
            totais: { perguntas: 0, posVenda: 0 },
            erros: {}
        };
        renderizarNotificacoes();
        return;
    }
    const agora = Date.now();
    if (!forcar && state.notificacoes.atualizadoEm && agora - state.notificacoes.atualizadoEm < 60000) {
        renderizarNotificacoes();
        return;
    }
    if (state.notificacoes.carregando) return;
    state.notificacoes.carregando = true;
    renderizarNotificacoes();
    const resultados = await Promise.all(lojas.map(async (loja) => {
        const nomeLoja = String(loja.nome || '').trim();
        try {
            const [perguntas, posVenda] = await Promise.all([
                buscarContadorPerguntasNaoRespondidas(nomeLoja),
                buscarContadorPosVendaNaoLidas(nomeLoja)
            ]);
            return { nomeLoja, perguntas, posVenda };
        } catch (error) {
            return { nomeLoja, error };
        }
    }));
    const porLoja = {};
    const erros = {};
    const totais = { perguntas: 0, perguntasParcial: false, posVenda: 0, posVendaParcial: false };
    resultados.forEach((resultado) => {
        const chave = chaveLojaCronometro(resultado.nomeLoja);
        if (!chave) return;
        if (resultado.error) {
            erros[chave] = mensagemErro(resultado.error);
            porLoja[chave] = dadosNotificacaoVazios();
            return;
        }
        const dados = {
            perguntas: Number(resultado.perguntas?.total || 0),
            perguntasParcial: Boolean(resultado.perguntas?.parcial),
            posVenda: Number(resultado.posVenda?.total || 0),
            posVendaParcial: Boolean(resultado.posVenda?.parcial),
            erro: ''
        };
        porLoja[chave] = dados;
        totais.perguntas += dados.perguntas;
        totais.posVenda += dados.posVenda;
        totais.perguntasParcial = totais.perguntasParcial || dados.perguntasParcial;
        totais.posVendaParcial = totais.posVendaParcial || dados.posVendaParcial;
    });
    state.notificacoes = {
        carregando: false,
        atualizadoEm: Date.now(),
        lojas: porLoja,
        totais,
        erros
    };
    renderizarNotificacoes();
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
        const active = nome === state.lojaSelecionada;
        const config = loja.config_perguntas || {};
        const intervaloMinutos = Math.max(0.25, Math.min(1440, Number(config.intervalo_minutos || 10) || 10));
        return `
            <div class="store-card ${active ? 'active' : ''} ${conectado ? '' : 'disabled'}" data-loja="${escapeHtml(nome)}" tabindex="${conectado ? '0' : '-1'}" aria-disabled="${conectado ? 'false' : 'true'}">
                <span class="store-card-heading">
                    <span class="store-name">${escapeHtml(nome)}</span>
                    <span class="store-connection-dot ${dotClasse}" title="${escapeHtml(dotTitulo)}" aria-label="${escapeHtml(dotTitulo)}"></span>
                </span>
                <span class="store-meta">
                    ${conectado ? '' : `<span class="badge ${badgeClasse}" title="${escapeHtml(mlMotivo)}">${badgeTexto}</span>`}
                </span>
                <span class="store-notifications" data-store-notifications="${escapeHtml(nome)}">${htmlNotificacoesLoja(nome)}</span>
                <span class="store-options">
                    <label class="store-option">
                        <input class="store-config-checkbox" type="checkbox" data-config="responder_automaticamente" ${config.responder_automaticamente ? 'checked' : ''}>
                        <span>Responder automaticamente</span>
                    </label>
                    <label class="store-option">
                        <input class="store-config-checkbox" type="checkbox" data-config="solicitar_aprovacao" ${config.solicitar_aprovacao ? 'checked' : ''}>
                        <span>Nova IA V2 gera rascunho para aprovação antes de enviar</span>
                    </label>
                    <label class="store-option">
                        <input class="store-config-checkbox" type="checkbox" data-config="habilitar_pos_venda_automatico" ${config.habilitar_pos_venda_automatico ? 'checked' : ''}>
                        <span>Usar estas configurações também no pós-venda</span>
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
    const habilitarPosVendaAutomatico = !!card.querySelector('[data-config="habilitar_pos_venda_automatico"]')?.checked;
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
                habilitar_pos_venda_automatico: habilitarPosVendaAutomatico,
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
                habilitar_pos_venda_automatico: config.habilitar_pos_venda_automatico === true,
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
        const pendentes = Array.isArray(data.pendentes) ? data.pendentes : [];
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

function existeLojaComAutomacaoPosVendaAtiva(lojaNome = '') {
    const filtro = String(lojaNome || '').trim();
    return state.lojas.some((loja) => {
        const config = loja.config_perguntas || {};
        const nome = String(loja.nome || '').trim();
        if (filtro && nome !== filtro) return false;
        return loja.mercadolivre_conectado === true
            && config.responder_automaticamente === true
            && config.habilitar_pos_venda_automatico === true;
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

async function executarAutomacaoPerguntas(lojaNome = '') {
    if (state.automacaoPerguntasRodando || !existeLojaComAutomacaoAtiva()) return;
    state.automacaoPerguntasRodando = true;
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

async function executarAutomacaoPosVenda(lojaNome = '') {
    if (state.automacaoPosVendaRodando || !existeLojaComAutomacaoPosVendaAtiva(lojaNome || state.lojaSelecionada || '')) return;
    state.automacaoPosVendaRodando = true;
    try {
        const params = new URLSearchParams({ max_per_store: '2' });
        if (lojaNome || state.lojaSelecionada) params.set('loja', lojaNome || state.lojaSelecionada);
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
    (state.automacaoPerguntasTimers || []).forEach((timerId) => clearInterval(timerId));
    state.automacaoPerguntasTimers = [];
    if (state.automacaoPerguntasCountdownTimer) {
        clearInterval(state.automacaoPerguntasCountdownTimer);
        state.automacaoPerguntasCountdownTimer = null;
    }
    state.automacaoPerguntasNextChecks = {};

    carregarAprovacoesPendentes();
    const lojasAtivas = lojasComAutomacaoAtiva();
    if (!lojasAtivas.length) {
        atualizarCronometrosAutomacao();
        return;
    }
    iniciarCronometroAutomacao();

    lojasAtivas.forEach((loja, index) => {
        const nomeLoja = String(loja.nome || '').trim();
        const intervaloMs = intervaloAutomacaoLojaMs(loja);
        const delayPerguntas = 250 + (index * 250);
        const delayPosVenda = 750 + (index * 250);
        registrarProximaChecagemLoja(nomeLoja, Date.now() + Math.min(delayPerguntas, delayPosVenda));
        setTimeout(() => {
            executarAutomacaoPerguntas(nomeLoja);
            registrarProximaChecagemLoja(nomeLoja, Date.now() + intervaloMs);
        }, delayPerguntas);
        setTimeout(() => {
            executarAutomacaoPosVenda(nomeLoja);
            registrarProximaChecagemLoja(nomeLoja, Date.now() + intervaloMs);
        }, delayPosVenda);
        const timerId = setInterval(() => {
            registrarProximaChecagemLoja(nomeLoja, Date.now() + intervaloMs);
            executarAutomacaoPerguntas(nomeLoja);
            executarAutomacaoPosVenda(nomeLoja);
        }, intervaloMs);
        state.automacaoPerguntasTimers.push(timerId);
    });
}
