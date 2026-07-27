'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(
    path.join(root, 'static', 'favoritos', 'promocoes-efetivacao.js'),
    'utf8'
);

function respostaJson(data, ok = true, status = 200) {
    return {
        ok,
        status,
        async json() {
            return data;
        }
    };
}

function aguardarCondicao(condicao, timeoutMs = 1200) {
    const inicio = Date.now();
    return new Promise((resolve, reject) => {
        const verificar = () => {
            if (condicao()) {
                resolve();
                return;
            }
            if (Date.now() - inicio >= timeoutMs) {
                reject(new Error('Tempo esgotado aguardando condicao do teste.'));
                return;
            }
            setTimeout(verificar, 5);
        };
        verificar();
    });
}

function criarRegistro({ requerValidacao = false } = {}) {
    return {
        itemId: 'MLB1234567890',
        loja: 'JK Pecas',
        anuncio: {
            mlb: 'MLB1234567890',
            sku: 'SKU-TESTE',
            loja: 'JK Pecas',
            preco: 100
        },
        ranking: {
            id: 'MLB9999999999',
            preco: 79
        },
        sim: {
            ok: true,
            preco: 100,
            precoPromocionalCalculado: 79,
            precoPromocional: 79,
            precoCompetitivo: 79,
            percentualPromocao: 21,
            listingTypeIdAtual: requerValidacao ? 'gold_pro' : 'gold_special',
            listingTypeIdAlvo: 'gold_special',
            tipoAnuncioAtual: requerValidacao ? 'Premium' : 'Classico',
            tipoAnuncioAlvo: 'Classico',
            trocarTipoAnuncio: requerValidacao
        }
    };
}

function criarHarness(opcoes = {}) {
    const state = {
        validationMode: opcoes.validationMode || 'success',
        confirmationMode: opcoes.confirmationMode || 'approve',
        requests: [],
        validationCalls: 0,
        mutationCalls: 0,
        confirmationCalls: 0,
        panelUpdates: 0,
        messages: [],
        statusRows: [],
        pendingValidations: [],
        pendingConfirmations: []
    };
    const registro = criarRegistro({ requerValidacao: !!opcoes.requerValidacao });
    const botao = { disabled: false, textContent: 'Aprovar e alterar' };
    const info = { textContent: '' };
    const status = { textContent: '' };
    const opcoesPromocao = {
        usar_promocao: true,
        campanha: {
            id: 'CAMPANHA-1',
            nome: 'Campanha teste',
            tipo: 'SELLER_CAMPAIGN'
        },
        desconto: {
            modo: 'percentual_fixo',
            percentual: 21
        }
    };

    const context = {
        console,
        Map,
        Set,
        Array,
        Number,
        String,
        Boolean,
        Math,
        JSON,
        Promise,
        Date,
        Error,
        URLSearchParams,
        setTimeout,
        clearTimeout,
        window: { electronAPI: null },
        favMlEfetivacaoEmExecucao: false,
        favMlEfetivacaoEmPreparacao: false,
        favMlSkuSelecionado: 'SKU-TESTE',
        favMlLojaSelecionada: 'JK Pecas',
        mlSkuLojaSelecionada: 'JK Pecas',
        skuLojaSelecionada: 'JK Pecas',
        favMlAnunciosSkuAtual: [registro.anuncio],
        favMlSimulacoesSkuAtual: null,
        favMlEfetivarOutrasContasEl: { checked: false },
        favMlEfetivarBtnEl: botao,
        favMlEfetivarInfoEl: info,
        favMlStatusEl: status,
        fetch: async (url, options = {}) => {
            const chamada = {
                url: String(url),
                method: String(options.method || 'GET').toUpperCase(),
                headers: { ...(options.headers || {}) },
                body: options.body ? JSON.parse(options.body) : null
            };
            state.requests.push(chamada);
            if (chamada.url === '/api/favoritos/ml/validar-efetivacao') {
                state.validationCalls += 1;
                if (state.validationMode === 'error') {
                    throw new Error('validacao indisponivel para teste');
                }
                const criarResposta = () => respostaJson({
                    itens: (chamada.body && chamada.body.itens || []).map(item => ({
                        ...item,
                        ok: true,
                        current: 'gold_pro',
                        target: 'gold_special'
                    }))
                });
                if (state.validationMode === 'slow') {
                    return new Promise((resolve, reject) => {
                        state.pendingValidations.push({ resolve: () => resolve(criarResposta()), reject });
                    });
                }
                return criarResposta();
            }
            if (chamada.url === '/api/favoritos/ml/efetivar-promocao') {
                state.mutationCalls += 1;
                return respostaJson({
                    success: true,
                    item_id: chamada.body.item_id,
                    preco_anuncio: chamada.body.preco_anuncio,
                    preco_promocional: chamada.body.preco_promocional,
                    campanha_nome: chamada.body.campanha_nome,
                    promotion_id: chamada.body.campanha_id,
                    listing_type_update: null
                });
            }
            throw new Error(`URL nao stubada: ${chamada.url}`);
        }
    };
    vm.createContext(context);
    vm.runInContext(source, context, { filename: 'promocoes-efetivacao.js' });

    context.garantirPromocaoFavoritosSkuAtual = async () => opcoesPromocao;
    context.carregarFavoritosAnunciosSkuTodasContas = async () => [registro.anuncio];
    context.obterRegistrosSimulacaoFavoritos = () => [registro];
    context.filtrarRegistrosSelecionadosAlteracaoFavoritos = registros => registros;
    context.filtrarRegistrosEfetivaveisFavoritos = registros => registros;
    context.explicarRegistrosNaoEfetivaveisFavoritos = () => 'sem registros validos';
    context.registroFavoritosExigeTrocaTipoAnuncio = item => !!(
        item && item.sim && item.sim.listingTypeIdAtual !== item.sim.listingTypeIdAlvo
    );
    context.skuNormalizarLoja = valor => String(valor || '').trim().toLowerCase();
    context.favoritosLojaSelecionadaParaApi = valor => String(valor || '').trim();
    context.perguntarConfirmacaoEfetivarFavoritos = async () => {
        state.confirmationCalls += 1;
        if (state.confirmationMode === 'deferred') {
            return new Promise(resolve => state.pendingConfirmations.push(resolve));
        }
        return state.confirmationMode === 'approve';
    };
    context.mostrarBalaoFavoritosStatus = mensagem => {
        state.messages.push(String(mensagem || ''));
    };
    context.atualizarPainelEfetivarFavoritos = () => {
        state.panelUpdates += 1;
        botao.disabled = !!(
            context.favMlEfetivacaoEmExecucao
            || context.favMlEfetivacaoEmPreparacao
        );
        if (!botao.disabled) botao.textContent = 'Aprovar e alterar';
    };
    context.limparLogEfetivarFavoritos = () => {};
    context.adicionarStatusEfetivarFavoritos = (tipo, titulo, detalhe) => {
        state.statusRows.push({ tipo, titulo, detalhe });
    };
    context.definirProtecaoAutomacaoMlFavoritos = async () => false;
    context.resolverOpcoesPromocaoEfetivacaoParaLoja = async valor => valor;
    context.headersJsonAutenticado = () => ({
        'Content-Type': 'application/json',
        Authorization: 'Bearer teste-local'
    });
    context.obterAuthHeaders = () => ({ Authorization: 'Bearer teste-local' });
    context.normalizarErroEfetivacaoFavoritos = erro => {
        if (erro && erro.message) return erro.message;
        if (typeof erro === 'string') return erro;
        try {
            return JSON.stringify(erro || {});
        } catch (_err) {
            return String(erro || 'erro');
        }
    };
    context.parsePrecoAnuncioFavoritos = valor => {
        const numero = Number(valor);
        return Number.isFinite(numero) ? numero : null;
    };
    context.formatarPrecoFavoritosMl = valor => `R$ ${Number(valor || 0).toFixed(2)}`;
    context.formatarMargemAnuncioFavoritos = valor => `${Number(valor || 0).toFixed(2)}%`;
    context.carregarFavoritosAnunciosSku = async () => [];
    context.montarHistoricoAlteracoesFavoritosPayload = () => ({ vinculos: [] });
    context.renderizarComparativoEfetivacaoFavoritos = () => {};
    context.agendarOcultarStatusEfetivarFavoritos = () => {};
    context.textoTipoEnvioFavoritos = () => 'sem troca';

    return {
        context,
        state,
        registro,
        botao,
        executar: () => context.efetivarFavoritosMercadoLivreAprovados(),
        resolverValidacao() {
            const pendente = state.pendingValidations.shift();
            assert.ok(pendente, 'deve existir validacao pendente para resolver');
            pendente.resolve();
        },
        confirmar(valor) {
            const resolve = state.pendingConfirmations.shift();
            assert.ok(resolve, 'deve existir confirmacao pendente');
            resolve(!!valor);
        }
    };
}

async function testarCancelamentoSemPost() {
    const harness = criarHarness({ confirmationMode: 'cancel' });
    await harness.executar();
    assert.strictEqual(harness.state.confirmationCalls, 1);
    assert.strictEqual(harness.state.mutationCalls, 0, 'cancelar nao pode chamar endpoint mutavel');
    assert.strictEqual(harness.context.favMlEfetivacaoEmPreparacao, false, 'cancelamento deve liberar preparacao');
    assert.strictEqual(harness.context.favMlEfetivacaoEmExecucao, false);
    assert.strictEqual(harness.botao.disabled, false, 'botao deve voltar ao estado utilizavel depois de cancelar');
}

async function testarAprovacaoSomenteDepoisDaConfirmacao() {
    const harness = criarHarness({ confirmationMode: 'deferred' });
    const execucao = harness.executar();
    await aguardarCondicao(() => harness.state.confirmationCalls === 1);
    assert.strictEqual(harness.state.mutationCalls, 0, 'nenhuma mutacao pode ocorrer com confirmacao pendente');
    assert.strictEqual(harness.botao.disabled, true, 'botao deve permanecer bloqueado durante preparacao/confirmacao');
    harness.confirmar(true);
    await execucao;
    assert.strictEqual(harness.state.mutationCalls, 1, 'aprovacao deve disparar uma unica alteracao');
    const chamada = harness.state.requests.find(item => item.url.endsWith('/efetivar-promocao'));
    assert.ok(chamada, 'POST de efetivacao deve ser capturado pelo stub');
    assert.strictEqual(chamada.method, 'POST');
    assert.strictEqual(chamada.headers.Authorization, 'Bearer teste-local');
    assert.deepStrictEqual(
        {
            loja: chamada.body.loja,
            sku: chamada.body.sku,
            item_id: chamada.body.item_id,
            campanha_id: chamada.body.campanha_id,
            preco_anuncio: chamada.body.preco_anuncio,
            preco_promocional: chamada.body.preco_promocional,
            preco_ideal: chamada.body.preco_ideal
        },
        {
            loja: 'JK Pecas',
            sku: 'SKU-TESTE',
            item_id: 'MLB1234567890',
            campanha_id: 'CAMPANHA-1',
            preco_anuncio: 100,
            preco_promocional: 79,
            preco_ideal: 79
        }
    );
    assert.strictEqual(harness.context.favMlEfetivacaoEmPreparacao, false);
    assert.strictEqual(harness.context.favMlEfetivacaoEmExecucao, false);
    assert.strictEqual(harness.botao.disabled, false);
}

async function testarValidacaoLentaComLatch() {
    const harness = criarHarness({
        requerValidacao: true,
        validationMode: 'slow',
        confirmationMode: 'approve'
    });
    const primeira = harness.executar();
    await aguardarCondicao(() => harness.state.validationCalls === 1);
    assert.strictEqual(harness.botao.disabled, true, 'validacao lenta deve bloquear o botao imediatamente');
    assert.ok(
        harness.state.messages.some(texto => /validando no mercado livre/i.test(texto)),
        'validacao lenta deve ter estado visivel'
    );
    const segunda = harness.executar();
    await new Promise(resolve => setTimeout(resolve, 60));
    assert.strictEqual(harness.state.validationCalls, 1, 'clique repetido nao pode iniciar outra validacao');
    harness.resolverValidacao();
    await Promise.all([primeira, segunda]);
    assert.strictEqual(harness.state.confirmationCalls, 1, 'validacao concluida deve abrir uma confirmacao');
    assert.strictEqual(harness.state.mutationCalls, 1, 'latch deve preservar uma unica mutacao');
    const validacao = harness.state.requests.find(item => item.url.endsWith('/validar-efetivacao'));
    assert.deepStrictEqual(validacao.body, {
        itens: [{
            loja: 'JK Pecas',
            item_id: 'MLB1234567890',
            preco_anuncio_alvo: 100,
            listing_type_id_alvo: 'gold_special',
            tipo_anuncio_alvo: 'Classico'
        }]
    });
    assert.strictEqual(harness.context.favMlEfetivacaoEmPreparacao, false);
    assert.strictEqual(harness.context.favMlEfetivacaoEmExecucao, false);
    assert.strictEqual(harness.botao.disabled, false);
}

async function testarErroLiberaPreparacao() {
    const harness = criarHarness({
        requerValidacao: true,
        validationMode: 'error',
        confirmationMode: 'approve'
    });
    await harness.executar();
    assert.strictEqual(harness.state.mutationCalls, 0, 'falha de validacao nao pode chamar mutacao');
    assert.ok(
        harness.state.messages.some(texto => /erro ao validar anuncios/i.test(texto)),
        'falha deve ser explicada ao usuario'
    );
    assert.strictEqual(harness.context.favMlEfetivacaoEmPreparacao, false, 'erro deve liberar latch');
    assert.strictEqual(harness.context.favMlEfetivacaoEmExecucao, false);
    assert.strictEqual(harness.botao.disabled, false);

    harness.state.validationMode = 'success';
    harness.state.confirmationMode = 'cancel';
    await harness.executar();
    assert.strictEqual(harness.state.validationCalls, 2, 'nova tentativa deve funcionar depois do erro');
    assert.strictEqual(harness.state.confirmationCalls, 1, 'nova tentativa valida deve chegar a confirmacao');
    assert.strictEqual(harness.state.mutationCalls, 0, 'cancelamento da nova tentativa continua sem mutacao');
}

async function testarExcecaoPainelLiberaPreparacao() {
    const harness = criarHarness({ confirmationMode: 'cancel' });
    const atualizarPainelOriginal = harness.context.atualizarPainelEfetivarFavoritos;
    harness.context.atualizarPainelEfetivarFavoritos = () => {
        throw new Error('falha de interface simulada');
    };
    await assert.rejects(
        harness.executar(),
        /falha de interface simulada/,
        'excecao ao atualizar o painel deve ser observavel no teste'
    );
    assert.strictEqual(
        harness.context.favMlEfetivacaoEmPreparacao,
        false,
        'finally deve liberar o latch mesmo se a primeira atualizacao do painel falhar'
    );
    assert.strictEqual(harness.context.favMlEfetivacaoEmExecucao, false);

    harness.context.atualizarPainelEfetivarFavoritos = atualizarPainelOriginal;
    await harness.executar();
    assert.strictEqual(harness.state.confirmationCalls, 1, 'uma nova tentativa deve funcionar depois da excecao visual');
    assert.strictEqual(harness.state.mutationCalls, 0);
}

async function testarParcialExplicitaEtapasETerminal() {
    const harness = criarHarness({ requerValidacao: true });
    const resultado = {
        success: false,
        outcome: 'partial_failure',
        retryable: false,
        retry_requires_approval: true,
        preco_anuncio_atual: 287.22,
        preco_anuncio_alvo: 214.33,
        current_state: {
            status: 'active',
            listing_type_name: 'Classico',
            price: 287.22
        },
        listing_type_update: {
            changed: true,
            current_name: 'Premium',
            target_name: 'Classico'
        },
        stages: {
            preflight: { status: 'completed' },
            promotion_removal: { status: 'skipped' },
            listing_type: { status: 'completed' },
            price: { status: 'failed' },
            promotion: { status: 'not_started' },
            verification: { status: 'not_started' }
        }
    };
    const item = {
        itemId: harness.registro.itemId,
        registro: harness.registro,
        resultado,
        erro: 'Cannot update item [status:active, has_bids:true] | price is not modifiable.'
    };
    const detalhe = harness.context.descreverFalhaEfetivacaoFavoritos(item);
    assert.match(detalhe, /tipo alterado: Premium -> Classico/i);
    assert.doesNotMatch(detalhe, /tipo previsto/i);
    assert.match(detalhe, /preco nao alterado: R\$ 287\.22/i);
    assert.match(detalhe, /campanha nao iniciada/i);
    assert.match(detalhe, /bloqueio terminal/i);
    assert.match(detalhe, /nao ha repeticao automatica/i);

    const vinculo = harness.context.montarVinculoHistoricoAlteracaoFavoritos(item, false, 'SKU-TESTE');
    assert.strictEqual(vinculo.outcome, 'partial_failure');
    assert.strictEqual(vinculo.retryable, false);
    assert.strictEqual(vinculo.retry_requires_approval, true);
    assert.strictEqual(vinculo.terminal, true);
    assert.strictEqual(vinculo.stages.listing_type.status, 'completed');
    assert.strictEqual(vinculo.stages.price.status, 'failed');
    assert.strictEqual(vinculo.stages.promotion.status, 'not_started');
}

async function testarHistoricoSoConfirmaDepoisDoServidor() {
    const harness = criarHarness();
    const idsConfirmados = [];
    harness.context.registrarHistoricoAlteracoesFavoritos = () => ({ id: 'alt_teste_1' });
    harness.context.confirmarSalvamentoHistoricoFavoritosServidor = async ids => {
        idsConfirmados.push(...ids);
        return { success: true, ids };
    };
    const confirmado = await harness.context.salvarHistoricoAlteracoesFavoritosProcesso({ vinculos: [{}] });
    assert.strictEqual(confirmado.confirmado, true);
    assert.deepStrictEqual(idsConfirmados, ['alt_teste_1']);

    harness.context.confirmarSalvamentoHistoricoFavoritosServidor = async () => ({
        success: false,
        erro: 'servidor indisponivel'
    });
    const pendente = await harness.context.salvarHistoricoAlteracoesFavoritosProcesso({ vinculos: [{}] });
    assert.strictEqual(pendente.confirmado, false);
    assert.strictEqual(pendente.pendente, true);
    assert.match(pendente.erro, /servidor indisponivel/i);
}

async function testarEstadoRemotoIncertoNaoAfirmaAusenciaDeAlteracao() {
    const harness = criarHarness({ requerValidacao: true });
    const resultado = {
        success: false,
        outcome: 'partial_unknown',
        retryable: true,
        retry_requires_approval: true,
        current_state: { status: 'active', price: 214.33 },
        stages: {
            preflight: { status: 'completed' },
            promotion_removal: { status: 'completed' },
            listing_type: { status: 'skipped' },
            price: { status: 'completed' },
            promotion: { status: 'unknown' },
            verification: { status: 'unknown' }
        }
    };
    const item = {
        itemId: harness.registro.itemId,
        registro: harness.registro,
        resultado,
        erro: 'A resposta remota nao pode ser reconciliada.'
    };
    const detalhe = harness.context.descreverFalhaEfetivacaoFavoritos(item);
    assert.match(detalhe, /estado remoto incerto/i);
    assert.match(detalhe, /campanha pode ter sido aplicada/i);
    assert.doesNotMatch(detalhe, /nao foi alterado/i);

    const vinculo = harness.context.montarVinculoHistoricoAlteracaoFavoritos(item, false, 'SKU-TESTE');
    assert.strictEqual(vinculo.outcome, 'partial_unknown');
    assert.strictEqual(vinculo.status_texto, 'Estado remoto incerto');
    assert.strictEqual(vinculo.stages.promotion.status, 'unknown');
    assert.strictEqual(vinculo.stages.verification.status, 'unknown');
}

async function testarSucessoEHistoricoPreferemValoresObservados() {
    const harness = criarHarness();
    const data = {
        success: true,
        completed: true,
        preco_anuncio: 100,
        preco_promocional: 79,
        campanha_nome: 'Campanha teste',
        promotion_id: 'CAMPANHA-1',
        observados_autoritativos: {
            base_price: 100.05,
            final_price: 78.95
        },
        preco_confirmacao: {
            standard_price: 100.03,
            item_price: 79.02
        },
        verificacao: {
            standard_price: 100.03,
            promotion_price_raw: 78.97,
            price_info: {
                standard_price: 100.03,
                price: 78.97
            }
        }
    };
    const item = { itemId: harness.registro.itemId, registro: harness.registro, data };
    const detalhe = harness.context.descreverSucessoEfetivacaoFavoritos(item);
    assert.match(detalhe, /preco cheio observado: R\$ 100\.05/i);
    assert.match(detalhe, /preco final promocional observado: R\$ 78\.95/i);
    assert.doesNotMatch(detalhe, /preco cheio aplicado: R\$ 100\.00/i);

    const historico = harness.context.montarSimulacaoHistoricoAlteracaoFavoritos(harness.registro, data);
    assert.strictEqual(historico.preco_ideal, 79);
    assert.strictEqual(historico.preco_previsto, 100);
    assert.strictEqual(historico.preco_promocional_previsto, 79);
    assert.strictEqual(historico.preco_aplicado, 100.05);
    assert.strictEqual(historico.preco_promocional_aplicado, 78.95);

    const fallback = {
        success: true,
        completed: true,
        fallback_sem_promocao_aplicado: true,
        preco_anuncio: 82,
        preco_promocional: null,
        observados_autoritativos: {
            final_price: 82.06
        },
        preco_confirmacao_fallback: {
            standard_price: 82.04,
            item_price: 82.04
        },
        fallback_motivo: 'Campanha nao mantida no Mercado Livre.'
    };
    const itemFallback = { itemId: harness.registro.itemId, registro: harness.registro, data: fallback };
    const detalheFallback = harness.context.descreverSucessoEfetivacaoFavoritos(itemFallback);
    assert.match(detalheFallback, /preco direto observado: R\$ 82\.06/i);
    assert.doesNotMatch(detalheFallback, /preco cheio aplicado/i);
    const historicoFallback = harness.context.montarSimulacaoHistoricoAlteracaoFavoritos(harness.registro, fallback);
    assert.strictEqual(historicoFallback.preco_aplicado, 82.06);
    assert.strictEqual(historicoFallback.preco_promocional_aplicado, null);
}

async function main() {
    await testarCancelamentoSemPost();
    await testarAprovacaoSomenteDepoisDaConfirmacao();
    await testarValidacaoLentaComLatch();
    await testarErroLiberaPreparacao();
    await testarExcecaoPainelLiberaPreparacao();
    await testarParcialExplicitaEtapasETerminal();
    await testarEstadoRemotoIncertoNaoAfirmaAusenciaDeAlteracao();
    await testarHistoricoSoConfirmaDepoisDoServidor();
    await testarSucessoEHistoricoPreferemValoresObservados();
    console.log('OK: aprovacao, preflight de preco, parcial terminal, historico confirmado, latch e erros respeitam o contrato sem rede real.');
}

main().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
