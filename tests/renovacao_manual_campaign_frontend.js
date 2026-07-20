'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'static', 'renovacao.html'), 'utf8');
const inlineScript = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)]
    .map(match => match[1])
    .join('\n');

assert.doesNotThrow(() => new Function(inlineScript), 'JavaScript inline da tela deve compilar');

for (const id of [
    'btn-abrir-criacao-manual-renovacao',
    'renovacao-criacao-manual-backdrop',
    'renovacao-criacao-manual-titulo',
    'renovacao-criacao-manual-form',
    'renovacao-criacao-manual-nome',
    'renovacao-criacao-manual-inicio',
    'renovacao-criacao-manual-fim',
    'btn-criar-promocao-manual-renovacao',
]) {
    assert(html.includes(`id="${id}"`), `controle ausente: ${id}`);
}

assert(html.includes('role="dialog"'));
assert(html.includes('aria-modal="true"'));
assert(html.includes('aria-labelledby="renovacao-criacao-manual-titulo"'));
assert(html.includes('aria-describedby="renovacao-criacao-manual-descricao"'));
assert(html.includes('aria-live="polite"'));
assert(html.includes('Cria uma campanha vazia no Mercado Livre'));
assert(html.includes('use <strong>Sincronizar promoção</strong>'));

assert(inlineScript.includes("fetch('/api/renovacao/campanha'"));
assert(inlineScript.includes("method: 'POST'"));
assert(inlineScript.includes("body: JSON.stringify({ loja, nome, start_date: inicio, finish_date: fim })"));
assert(!inlineScript.includes("body: JSON.stringify({ loja, nome, start_date: inicio, finish_date: fim, promotion_type"));

assert(inlineScript.includes('await carregarCampanhasRenovacao();'));
assert(inlineScript.includes('restaurarSelecoesAposCriacaoManualRenovacao(origemCampanhaId, origemSyncId'));
assert(inlineScript.includes('selectDestino.value = novaId;'));
assert(inlineScript.includes('fecharDialogCriacaoManualRenovacao(true, true);'));

// O fluxo já existente continua separado e com o contrato anterior.
assert(inlineScript.includes('async function criarCampanhaMesSeguinte()'));
assert(inlineScript.includes("fetch('/api/renovacao/criar-proximo-mes'"));
assert(inlineScript.includes('campanha_id: campanhaId'));

function createElement(overrides = {}) {
    return {
        value: '',
        textContent: '',
        className: '',
        hidden: false,
        disabled: false,
        style: {},
        focus() {},
        reset() {},
        ...overrides,
    };
}

const elements = new Map([
    ['renovacao-loja', createElement({ value: 'JK Peças' })],
    ['renovacao-criacao-manual-nome', createElement({ value: 'Promo Teste' })],
    ['renovacao-criacao-manual-inicio', createElement({ value: '2099-07-20' })],
    ['renovacao-criacao-manual-fim', createElement({ value: '2099-08-03' })],
    ['btn-criar-promocao-manual-renovacao', createElement({ textContent: 'Criar no Mercado Livre' })],
    ['btn-fechar-criacao-manual-renovacao', createElement()],
    ['btn-cancelar-criacao-manual-renovacao', createElement()],
    ['renovacao-campanha', createElement({ value: 'ORIGEM-1' })],
    ['renovacao-sync-origem', createElement({ value: 'ORIGEM-1' })],
    ['renovacao-api-status', createElement()],
    ['renovacao-criacao-manual-backdrop', createElement({ hidden: false })],
    ['renovacao-criacao-manual-form', createElement()],
]);
const fetchCalls = [];

const sandbox = {
    console,
    Date,
    setInterval() {},
    clearInterval() {},
    document: {
        activeElement: null,
        addEventListener() {},
        getElementById(id) { return elements.get(id) || null; },
        querySelectorAll() { return []; },
        createElement() { return createElement(); },
    },
    window: { setTimeout(callback) { callback(); } },
    confirm() { return true; },
    obterAuthHeaders() { return { Authorization: 'Bearer teste' }; },
    async fetch(url, options) {
        fetchCalls.push({ url, options });
        return {
            ok: false,
            async json() { return { detail: 'Maximum period cannot exceed the allowed limit.' }; },
        };
    },
};
vm.createContext(sandbox);
vm.runInContext(`${inlineScript}\nthis.renovacaoManualTeste = { validarDadosPromocaoManualRenovacao, criarPromocaoManualRenovacao };`, sandbox);
const fecharDialogOriginal = sandbox.fecharDialogCriacaoManualRenovacao;

const validar = sandbox.renovacaoManualTeste.validarDadosPromocaoManualRenovacao;
const referencia = new Date(2026, 6, 20, 8, 30, 0, 0);
assert.strictEqual(validar('Loja', 'Promo', '2026-07-20', '2026-08-03', referencia), '');
assert.match(validar('', 'Promo', '2026-07-20', '2026-07-20', referencia), /Selecione a loja/);
assert.match(validar('Loja', '', '2026-07-20', '2026-07-20', referencia), /nome da promoção/);
assert.match(validar('Loja', 'Promo', '', '2026-07-20', referencia), /datas de início e término/);
assert.match(validar('Loja', 'Promo', '2026-02-30', '2026-03-01', referencia), /datas válidas/);
assert.match(validar('Loja', 'Promo', '2026-7-20', '2026-07-21', referencia), /datas válidas/);
assert.match(validar('Loja', 'Promo', '2026-07-19', '2026-07-20', referencia), /não pode ser anterior a hoje/);
assert.match(validar('Loja', 'Promo', '2026-07-21', '2026-07-20', referencia), /não pode ser anterior à data de início/);
assert.strictEqual(validar('Loja', 'Promo', '2026-07-20', '2026-09-30', referencia), '');

(async () => {
    let prevented = false;
    await sandbox.renovacaoManualTeste.criarPromocaoManualRenovacao({ preventDefault() { prevented = true; } });
    assert(prevented, 'submit precisa impedir navegação do formulário');
    assert.strictEqual(fetchCalls.length, 1);
    assert.strictEqual(fetchCalls[0].url, '/api/renovacao/campanha');
    assert.strictEqual(fetchCalls[0].options.method, 'POST');
    assert.deepStrictEqual(JSON.parse(fetchCalls[0].options.body), {
        loja: 'JK Peças',
        nome: 'Promo Teste',
        start_date: '2099-07-20',
        finish_date: '2099-08-03',
    });
    assert.strictEqual(elements.get('renovacao-criacao-manual-nome').value, 'Promo Teste', 'erro deve preservar nome');
    assert.strictEqual(elements.get('renovacao-criacao-manual-inicio').value, '2099-07-20', 'erro deve preservar início');
    assert.strictEqual(elements.get('renovacao-criacao-manual-fim').value, '2099-08-03', 'erro deve preservar fim');
    assert.strictEqual(elements.get('renovacao-criacao-manual-backdrop').hidden, false, 'erro deve manter diálogo aberto');
    assert.strictEqual(elements.get('btn-criar-promocao-manual-renovacao').disabled, false, 'botão deve ser reativado');
    assert.strictEqual(elements.get('btn-criar-promocao-manual-renovacao').textContent, 'Criar no Mercado Livre');
    assert.strictEqual(elements.get('renovacao-api-status').textContent, 'Maximum period cannot exceed the allowed limit.');
    assert.match(elements.get('renovacao-api-status').className, /error/);

    let request = null;
    let recargas = 0;
    let restauracao = null;
    let limparDialog = null;
    sandbox.fetch = async (url, options) => {
        request = { url, options };
        return {
            ok: true,
            async json() {
                return {
                    nova_campanha: {
                        id: 'C-MLB999',
                        nome: 'Promo Teste',
                        start_date: '2099-07-20T00:00:00',
                        finish_date: '2099-08-03T23:59:59',
                        promotion_type: 'SELLER_CAMPAIGN',
                        sub_type: 'FLEXIBLE_PERCENTAGE',
                    },
                };
            },
        };
    };
    sandbox.carregarCampanhasRenovacao = async () => { recargas += 1; };
    sandbox.restaurarSelecoesAposCriacaoManualRenovacao = (...args) => { restauracao = args; };
    sandbox.fecharDialogCriacaoManualRenovacao = limpar => { limparDialog = limpar; };

    await sandbox.renovacaoManualTeste.criarPromocaoManualRenovacao({ preventDefault() {} });

    assert.strictEqual(request.url, '/api/renovacao/campanha');
    assert.strictEqual(request.options.method, 'POST');
    assert.deepStrictEqual(
        JSON.parse(request.options.body),
        { loja: 'JK Peças', nome: 'Promo Teste', start_date: '2099-07-20', finish_date: '2099-08-03' },
    );
    assert.strictEqual(recargas, 1, 'sucesso deve recarregar campanhas uma vez');
    assert.strictEqual(limparDialog, true, 'sucesso deve fechar e limpar o dialogo');
    assert.strictEqual(restauracao[0], 'ORIGEM-1');
    assert.strictEqual(restauracao[1], 'ORIGEM-1');
    assert.strictEqual(restauracao[2].id, 'C-MLB999');
    assert.match(elements.get('renovacao-api-status').className, /ok/);

    sandbox.fecharDialogCriacaoManualRenovacao = fecharDialogOriginal;
    recargas = 0;
    restauracao = null;
    elements.get('renovacao-loja').value = 'JK Peças';
    sandbox.fetch = async () => {
        assert.strictEqual(elements.get('renovacao-loja').disabled, true, 'loja deve ficar bloqueada durante o POST');
        assert.strictEqual(elements.get('btn-fechar-criacao-manual-renovacao').disabled, true, 'fechar deve ficar bloqueado durante o POST');
        assert.strictEqual(elements.get('btn-cancelar-criacao-manual-renovacao').disabled, true, 'cancelar deve ficar bloqueado durante o POST');
        elements.get('renovacao-loja').value = 'Outra Loja';
        return {
            ok: true,
            async json() {
                return { nova_campanha: { id: 'C-MLB1000', nome: 'Promo Teste' } };
            },
        };
    };

    await sandbox.renovacaoManualTeste.criarPromocaoManualRenovacao({ preventDefault() {} });

    assert.strictEqual(recargas, 0, 'troca de loja durante a resposta nao deve recarregar outra conta');
    assert.strictEqual(restauracao, null, 'troca de loja durante a resposta nao deve injetar campanha em outra conta');
    assert.match(elements.get('renovacao-api-status').textContent, /criada na loja JK Peças/);
    assert.strictEqual(elements.get('renovacao-loja').disabled, false, 'loja deve ser liberada ao terminar');
    console.log('renovacao manual campaign frontend: OK');
})().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
