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
    context.formatarPrecoFavoritosMl = valor => `R$ ${Number(valor || 0).toFixed(2)}`;
    context.formatarMargemAnuncioFavoritos = valor => `${Number(valor || 0).toFixed(2)}%`;
    context.carregarFavoritosAnunciosSku = async () => [];
    context.montarHistoricoAlteracoesFavoritosPayload = () => ({ vinculos: [] });
    context.salvarHistoricoAlteracoesFavoritosProcesso = () => null;
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
            preco_promocional: chamada.body.preco_promocional
        },
        {
            loja: 'JK Pecas',
            sku: 'SKU-TESTE',
            item_id: 'MLB1234567890',
            campanha_id: 'CAMPANHA-1',
            preco_anuncio: 100,
            preco_promocional: 79
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

async function main() {
    await testarCancelamentoSemPost();
    await testarAprovacaoSomenteDepoisDaConfirmacao();
    await testarValidacaoLentaComLatch();
    await testarErroLiberaPreparacao();
    await testarExcecaoPainelLiberaPreparacao();
    console.log('OK: aprovacao, cancelamento, validacao lenta, latch e erros respeitam o contrato sem rede real.');
}

main().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
