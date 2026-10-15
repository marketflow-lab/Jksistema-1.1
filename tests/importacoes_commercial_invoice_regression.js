'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const importacoesPath = path.join(__dirname, '..', 'static', 'importacoes.html');
const detalhePath = path.join(__dirname, '..', 'static', 'importacoes_lista.html');
const importacoes = fs.readFileSync(importacoesPath, 'utf8');
const detalhe = fs.readFileSync(detalhePath, 'utf8');

const requiredImportacoes = [
    'data-commercial-invoice',
    'async function baixarCommercialInvoice(lista, btn)',
    'async function carregarListaCommercialInvoice(listaId, prepararInvoice = false)',
    "btn.textContent = 'Verificando...'",
    'await carregarListaCommercialInvoice(listaId)',
    '/commercial-invoice',
    "{ method: 'GET', headers: { ...obterAuthHeaders() } }",
    'await persistirInvoiceLegadoSeNecessario(listaAtualizada, listaId);',
    'body: JSON.stringify({ numero_invoice: invoiceLocal })',
    'const nomeBaseArquivo = String(invoice || listaAtualizada.nome_lista || listaId).trim();',
    "const nomeSeguroArquivo = nomeBaseArquivo.replace(/[^A-Za-z0-9_-]+/g, '_').replace(/^_+|_+$/g, '') || 'lista';",
    "const blob = await resp.blob();",
    "if (!blob || !blob.size) throw new Error('O Commercial Invoice foi gerado vazio.');",
    'URL.createObjectURL(blob)',
    'window.alert(mensagem);',
];

for (const snippet of requiredImportacoes) {
    if (!importacoes.includes(snippet)) {
        throw new Error(`Contrato do Commercial Invoice ausente: ${snippet}`);
    }
}

const forbiddenImportacoes = [
    'function itemAprovadoCommercialInvoice(item)',
    'function pendenciasCommercialInvoice(lista, listaId)',
    'function avisarPendenciasCommercialInvoice(lista, listaId)',
    'if (avisarPendenciasCommercialInvoice(listaAtualizada, listaId)) return;',
    'A Commercial Invoice ainda não pode ser gerada.',
    'Aprove todos os SKUs:',
];

for (const snippet of forbiddenImportacoes) {
    if (importacoes.includes(snippet)) {
        throw new Error(`Pre-validacao indevida ainda bloqueia o Commercial Invoice: ${snippet}`);
    }
}

if (!detalhe.includes("'numero_invoice', 'supplier', 'currency'")) {
    throw new Error('O numero da Invoice deve ser persistido no backend junto aos dados logisticos');
}

for (const html of [importacoes, detalhe]) {
    const inlineScripts = [...html.matchAll(/<script(?: [^>]*)?>([\s\S]*?)<\/script>/gi)]
        .map((match) => match[1])
        .filter((source) => source.trim());
    for (const source of inlineScripts) new Function(source);
}

const flowStart = importacoes.indexOf('function carregarInvoiceLocal(listaId)');
const flowEnd = importacoes.indexOf('function setFx(valorTexto, horaTexto)', flowStart);
if (flowStart < 0 || flowEnd < 0) {
    throw new Error('Fluxo do download do Commercial Invoice nao foi encontrado');
}
const flowSource = importacoes.slice(flowStart, flowEnd);

function resposta({ ok = true, json = {}, blobSize = 0, filename = '' } = {}) {
    return {
        ok,
        headers: {
            get(nome) {
                return String(nome).toLowerCase() === 'content-disposition' && filename
                    ? `attachment; filename="${filename}"`
                    : null;
            },
        },
        async json() { return json; },
        async blob() { return { size: blobSize }; },
    };
}

function criarAmbiente(fetchImpl, armazenamento = '{}') {
    const status = [];
    const alertas = [];
    const links = [];
    const urlsCriadas = [];
    const urlsRevogadas = [];
    const localStorage = {
        getItem(chave) {
            return chave === 'importacoes_lista_resumo_editavel' ? armazenamento : null;
        },
    };
    const window = {
        JKDataPedidoLista: { async aguardarSalvamento() { return true; } },
        alert(mensagem) { alertas.push(mensagem); },
    };
    const URL = {
        createObjectURL(blob) {
            urlsCriadas.push(blob);
            return 'blob:commercial-invoice';
        },
        revokeObjectURL(url) { urlsRevogadas.push(url); },
    };
    const document = {
        body: {
            appendChild(link) { links.push(link); },
        },
        createElement(tag) {
            assert.strictEqual(tag, 'a');
            return {
                href: '',
                download: '',
                clicked: false,
                removed: false,
                click() { this.clicked = true; },
                remove() { this.removed = true; },
            };
        },
    };
    const funcoes = new Function(
        'localStorage',
        'fetch',
        'obterAuthHeaders',
        'setStatus',
        'window',
        'URL',
        'document',
        'setTimeout',
        `${flowSource}\nreturn { baixarCommercialInvoice };`
    )(
        localStorage,
        fetchImpl,
        () => ({ Authorization: 'Bearer teste' }),
        (mensagem) => status.push(mensagem),
        window,
        URL,
        document,
        (callback) => { callback(); return 1; },
    );
    return { ...funcoes, status, alertas, links, urlsCriadas, urlsRevogadas };
}

async function executarDownloadComSucesso(listaAtualizada, listaInicial = {}) {
    const chamadas = [];
    const listaId = listaAtualizada.id;
    const ambiente = criarAmbiente(async (url, options = {}) => {
        chamadas.push({ url, options });
        if (url.endsWith('/commercial-invoice')) {
            return resposta({ blobSize: 32 });
        }
        return resposta({ json: { lista: listaAtualizada } });
    });
    const botao = { textContent: 'Commercial Invoice', disabled: false };
    await ambiente.baixarCommercialInvoice({ id: listaId, ...listaInicial }, botao);
    assert.strictEqual(chamadas[0].url,
        '/api/medias-compras/listas-pedidos/' + encodeURIComponent(listaId) + '?somente_salvos=true',
        'Verificacao deve consultar o snapshot salvo sem recalcular impostos e frete');
    assert.strictEqual(chamadas[0].options.method, 'GET');
    assert.strictEqual(chamadas[0].options.cache, 'no-store', 'Verificacao deve buscar os dados atuais');
    assert.strictEqual(chamadas[0].options.headers.Authorization, 'Bearer teste');
    return { ambiente, botao, chamadas };
}

async function testarRascunhoSemCamposObrigatorios() {
    const { ambiente, botao, chamadas } = await executarDownloadComSucesso({
        id: 'lista-rascunho',
        nome_lista: 'JK 48',
        numero_invoice: '',
        supplier: '',
        itens: [{ sku: '001', compra_aprovada: false, compra_aprovada_em: '' }],
    });

    const chamadaDownload = chamadas.find((item) => item.url.endsWith('/commercial-invoice'));
    assert.ok(chamadaDownload, 'Lista incompleta deve chegar ao endpoint de download');
    assert.strictEqual(chamadaDownload.options.method, 'GET');
    assert.strictEqual(chamadaDownload.options.headers.Authorization, 'Bearer teste');
    assert.deepStrictEqual(ambiente.alertas, [], 'Campos ausentes nao devem exibir erro');
    assert.strictEqual(ambiente.links.length, 1);
    assert.strictEqual(ambiente.links[0].download, 'commercial_invoice_JK_48.xlsx');
    assert.strictEqual(ambiente.links[0].clicked, true);
    assert.strictEqual(ambiente.links[0].removed, true);
    assert.strictEqual(ambiente.urlsCriadas.length, 1);
    assert.deepStrictEqual(ambiente.urlsRevogadas, ['blob:commercial-invoice']);
    assert.strictEqual(botao.disabled, false);
    assert.strictEqual(botao.textContent, 'Commercial Invoice');
    assert.strictEqual(ambiente.status.at(-1), 'Commercial Invoice gerado com sucesso.');
}

async function testarPrecedenciaDoNomeDoArquivo() {
    const comInvoice = await executarDownloadComSucesso({
        id: 'lista-invoice',
        nome_lista: 'JK 48',
        numero_invoice: 'INV 7/2026',
        itens: [],
    }, { numero_invoice: 'INV-ANTIGA', nome_lista: 'Nome desatualizado' });
    assert.strictEqual(comInvoice.ambiente.links[0].download, 'commercial_invoice_INV_7_2026.xlsx');

    const somenteId = await executarDownloadComSucesso({
        id: 'lista-por-id',
        nome_lista: '',
        numero_invoice: '',
        itens: [],
    });
    assert.strictEqual(somenteId.ambiente.links[0].download, 'commercial_invoice_lista-por-id.xlsx');
    assert.notStrictEqual(somenteId.ambiente.links[0].download, 'commercial_invoice_.xlsx');
}

async function testarConsultaCompletaSomenteParaNcmPendente() {
    const casos = [
        [{ NCM: '8543.70.99' }, false],
        [{ ncm: '85437099' }, false],
        [{ NCM: '', ncm: '85437099' }, false],
        [{ NCM: '0' }, false],
        [{ NCM: 0 }, true],
        [{ NCM: 85437099 }, true],
        [{ NCM: 8543.7099 }, true],
        [{}, true],
        [{ NCM: 'pendente', ncm: '85437099' }, true],
        [{ NCM: 'ABC123' }, true],
        [{ NCM: '8543-70-99' }, true],
        [{ 'Código NCM': '85437099' }, true],
        ...['Quantidade', 'Valor unidade', 'Valor total'].flatMap(campo =>
            [undefined, '', '2', -1, Infinity, NaN].map(valor => [{ NCM: '85437099', [campo]: valor }, true])),
        ...['quantity', 'Cost', 'Subtotal', 'Quantity', 'QUANTIDADE', 'VALOR UNIDADE', 'Sub Total USD', 'PRÉÇO']
            .map(campo => [{ NCM: '85437099', [campo]: 2 }, true]),
        [{ NCM: '85437099', Quantidade: 0, 'Valor unidade': 0, 'Valor total': 0 }, false],
    ];
    for (const [item, esperaCompleta] of casos) {
        const consultas = [];
        const ambiente = criarAmbiente(async (url) => {
            if (url.endsWith('/commercial-invoice')) return resposta({ blobSize: 12 });
            consultas.push(url);
            const somenteSalvos = url.endsWith('?somente_salvos=true');
            return resposta({ json: { lista: {
                id: 'ncm', numero_invoice: somenteSalvos ? 'INV-SALVA' : 'INV-ATUALIZADA',
                itens: [{ SKU: '001', Quantidade: 2, 'Valor unidade': 10, 'Valor total': 20,
                    ...(somenteSalvos ? item : { NCM: '85437099' }) }],
            } } });
        });
        await ambiente.baixarCommercialInvoice({ id: 'ncm', numero_invoice: 'INV-ANTIGA' }, { textContent: 'Commercial Invoice' });
        const esperadas = ['/api/medias-compras/listas-pedidos/ncm?somente_salvos=true'];
        if (esperaCompleta) esperadas.push('/api/medias-compras/listas-pedidos/ncm');
        assert.deepStrictEqual(consultas, esperadas, 'Invoice legada deve consultar o detalhe completo para preservar valores e NCM: ' + JSON.stringify(item));
        assert.strictEqual(ambiente.links[0].download, esperaCompleta ? 'commercial_invoice_INV-ATUALIZADA.xlsx' : 'commercial_invoice_INV-SALVA.xlsx');
        assert.deepStrictEqual(ambiente.alertas, []);
    }
}

async function testarPersistenciaOpcionalDaInvoiceLegada() {
    const chamadas = [];
    const armazenamento = JSON.stringify({ legado: { numero_invoice: 'INV LEGADO' } });
    const ambiente = criarAmbiente(async (url, options = {}) => {
        chamadas.push({ url, options });
        if (url.endsWith('/commercial-invoice')) return resposta({ blobSize: 12 });
        if (options.method === 'PUT') return resposta();
        return resposta({ json: { lista: { id: 'legado', numero_invoice: '', nome_lista: 'Lista antiga', itens: [] } } });
    }, armazenamento);

    await ambiente.baixarCommercialInvoice({ id: 'legado', nome_lista: 'Lista antiga' }, { textContent: 'Commercial Invoice' });
    const chamadaPut = chamadas.find((item) => item.options.method === 'PUT');
    assert.ok(chamadaPut, 'Invoice existente apenas no navegador deve continuar sendo persistida');
    assert.deepStrictEqual(JSON.parse(chamadaPut.options.body), { numero_invoice: 'INV LEGADO' });
    assert.ok(chamadas.some((item) => item.url.endsWith('/commercial-invoice')));
    assert.strictEqual(ambiente.links[0].download, 'commercial_invoice_INV_LEGADO.xlsx');
}

async function testarErroRealDoEndpoint() {
    const chamadas = [];
    const ambiente = criarAmbiente(async (url, options = {}) => {
        chamadas.push({ url, options });
        if (url.endsWith('/commercial-invoice')) {
            return resposta({ ok: false, json: { detail: 'Falha real no gerador' } });
        }
        return resposta({ json: { lista: { id: 'lista-erro', numero_invoice: '', supplier: '', itens: [] } } });
    });
    const botao = { textContent: 'Commercial Invoice', disabled: false };

    await ambiente.baixarCommercialInvoice({ id: 'lista-erro' }, botao);

    assert.ok(chamadas.some((item) => item.url.endsWith('/commercial-invoice')));
    assert.deepStrictEqual(ambiente.alertas, ['Falha real no gerador']);
    assert.strictEqual(ambiente.status.at(-1), 'Falha real no gerador');
    assert.strictEqual(ambiente.links.length, 0);
    assert.strictEqual(botao.disabled, false);
    assert.strictEqual(botao.textContent, 'Commercial Invoice');
}

async function testarErroNaLeituraSalvaImpedeGeracao() {
    const chamadas = [];
    const ambiente = criarAmbiente(async (url) => {
        chamadas.push(url);
        return resposta({ ok: false, json: { detail: 'Lista indisponivel para verificacao' } });
    });
    const botao = { textContent: 'Commercial Invoice', disabled: false };
    await ambiente.baixarCommercialInvoice({ id: 'lista-indisponivel', numero_invoice: 'INV-ANTIGA' }, botao);
    assert.deepStrictEqual(chamadas, ['/api/medias-compras/listas-pedidos/lista-indisponivel?somente_salvos=true']);
    assert.deepStrictEqual(ambiente.alertas, ['Lista indisponivel para verificacao']);
    assert.strictEqual(ambiente.links.length, 0, 'Falha da leitura atual nao pode gerar documento com dados do cartao');
    assert.strictEqual(botao.disabled, false);
    assert.strictEqual(botao.textContent, 'Commercial Invoice');
}

async function testarErroAoCompletarNcmImpedeGeracao() {
    const chamadas = [];
    const ambiente = criarAmbiente(async (url) => {
        chamadas.push(url);
        if (url.endsWith('?somente_salvos=true')) {
            return resposta({ json: { lista: { id: 'ncm-pendente', itens: [{ SKU: '001' }] } } });
        }
        return resposta({ ok: false, json: { detail: 'Falha ao completar NCM' } });
    });
    await ambiente.baixarCommercialInvoice({ id: 'ncm-pendente' }, { textContent: 'Commercial Invoice' });
    assert.deepStrictEqual(chamadas, [
        '/api/medias-compras/listas-pedidos/ncm-pendente?somente_salvos=true',
        '/api/medias-compras/listas-pedidos/ncm-pendente',
    ]);
    assert.deepStrictEqual(ambiente.alertas, ['Falha ao completar NCM']);
    assert.strictEqual(ambiente.links.length, 0);
}

async function testarArquivoVazioComoErroReal() {
    const ambiente = criarAmbiente(async (url) => {
        if (url.endsWith('/commercial-invoice')) return resposta({ blobSize: 0 });
        return resposta({ json: { lista: { id: 'lista-vazia', itens: [] } } });
    });

    await ambiente.baixarCommercialInvoice(
        { id: 'lista-vazia' },
        { textContent: 'Commercial Invoice', disabled: false }
    );

    assert.deepStrictEqual(ambiente.alertas, ['O Commercial Invoice foi gerado vazio.']);
    assert.strictEqual(ambiente.links.length, 0);
}

(async () => {
    await testarRascunhoSemCamposObrigatorios();
    await testarPrecedenciaDoNomeDoArquivo();
    await testarConsultaCompletaSomenteParaNcmPendente();
    await testarPersistenciaOpcionalDaInvoiceLegada();
    await testarErroRealDoEndpoint();
    await testarErroNaLeituraSalvaImpedeGeracao();
    await testarErroAoCompletarNcmImpedeGeracao();
    await testarArquivoVazioComoErroReal();
    console.log('OK: Commercial Invoice gera rascunho sem campos obrigatorios e preserva erros reais.');
})().catch((erro) => {
    console.error(erro);
    process.exitCode = 1;
});
