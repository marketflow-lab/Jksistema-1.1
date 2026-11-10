'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const htmlPath = path.join(__dirname, '..', 'static', 'importacoes.html');
const html = fs.readFileSync(htmlPath, 'utf8');

const campos = {
    inputBookingEmbarque: 'numero_booking',
    inputArmadorEmbarque: 'armador',
    inputBlEmbarque: 'numero_bl',
    inputContainerEmbarque: 'numero_container',
    inputNavioEmbarque: 'nome_navio',
    inputViagemEmbarque: 'numero_viagem',
    inputImoEmbarque: 'numero_imo',
    inputMmsiEmbarque: 'numero_mmsi',
    inputPolEmbarque: 'porto_origem',
    inputPodEmbarque: 'porto_destino',
    inputEtdEmbarque: 'etd',
    inputEtaEmbarque: 'eta',
};

for (const [id, propriedade] of Object.entries(campos)) {
    assert(html.includes(`id="${id}"`), `Campo ${id} nao encontrado`);
    assert(
        html.includes(`['${id}', '${propriedade}']`),
        `Campo ${id} nao e restaurado ao editar o embarque`,
    );
    assert(
        html.includes(`${propriedade}: valorCampoEmbarque('${id}')`),
        `Campo ${id} nao e persistido no embarque`,
    );
}

for (const snippet of [
    'async function atualizarPosicaoNavioApi(embarque, botao, mostrarMapa = false)',
    "'/api/importacoes/rastreamento/navio?'",
    'headers: Object.assign({}, obterAuthHeaders())',
    "document.getElementById('embarquePosicaoApi')",
    'montarPosicaoNavioApiHtml(payload, mostrarMapa)',
    'Abrir coordenadas no mapa',
]) {
    assert(html.includes(snippet), `Contrato da API AIS ausente: ${snippet}`);
}

const inlineScripts = [...html.matchAll(/<script(?: [^>]*)?>([\s\S]*?)<\/script>/gi)]
    .map((match) => match[1])
    .filter((source) => source.trim());
for (const source of inlineScripts) {
    new Function(source);
}

const helpersStart = html.indexOf('function textoRastreamentoEmbarque(valor)');
const helpersEnd = html.indexOf('function renderEmbarques(todasListas)', helpersStart);
assert(helpersStart >= 0 && helpersEnd > helpersStart, 'Helpers de rastreamento nao encontrados');

const opened = [];
const statuses = [];
const alerts = [];
const trackingCalls = [];
const createdElements = [];
let responderConsultaAis = async () => ({ ok: true, json: async () => ({}) });
function fakeElement() {
    const listeners = {};
    return {
        hidden: true,
        isConnected: true,
        innerHTML: '',
        offsetWidth: 520,
        offsetHeight: 420,
        style: {},
        setAttribute() {},
        addEventListener(type, callback) { listeners[type] = callback; },
        contains() { return false; },
        getBoundingClientRect() { return { left: 100, top: 80, bottom: 112 }; },
        listeners,
    };
}
const fakeDocument = {
    body: { appendChild() {} },
    documentElement: { clientWidth: 1280, clientHeight: 800 },
    createElement() {
        const element = fakeElement();
        createdElements.push(element);
        return element;
    },
    addEventListener() {},
    getElementById: () => null,
};
const fakeWindow = {
    innerWidth: 1280,
    innerHeight: 800,
    addEventListener() {},
    open: (...args) => opened.push(args),
    electronAPI: {
        trackCoscoShipment(payload) {
            trackingCalls.push(payload);
            return new Promise(() => {});
        },
    },
};
const helpersSource = html.slice(helpersStart, helpersEnd);
const makeHelpers = new Function(
    'escaparHtml',
    'setStatus',
    'alert',
    'window',
    'document',
    'fetch',
    'obterAuthHeaders',
    'numero',
    `let detalheEmbarqueAtivo = {};
    ${helpersSource}
    return {
        atualizarPosicaoNavioApi,
        trocarDetalheEmbarque() { detalheEmbarqueAtivo = {}; },
        identificarArmadorEmbarque,
        obterReferenciaRastreamentoEmbarque,
        criarUrlRastreamentoCarga,
        obterIdentificadorAisEmbarque,
        erroIdentificadorAisEmbarque,
        criarUrlRastreamentoNavioApi,
        abrirRastreamentoCarga,
        consultarPortaisRastreamentoCarga,
        montarRastreamentoCargaPopoverHtml,
        montarPosicaoNavioApiHtml,
        montarMetaRastreamentoEmbarqueHtml
    };`,
);
const helpers = makeHelpers(
    (value) => String(value).replace(/[&<>"']/g, (char) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    })[char]),
    (message) => statuses.push(message),
    (message) => alerts.push(message),
    fakeWindow,
    fakeDocument,
    (...args) => responderConsultaAis(...args),
    () => ({ Authorization: 'Bearer teste' }),
    (value) => String(value),
);

const exemplo = {
    numero_booking: 'COSU6464481560',
    nome_navio: 'CMA CGM IRON',
    numero_viagem: '0BDPBW',
    numero_imo: '9996678',
    numero_mmsi: '249369000',
    porto_origem: 'NINGBO',
    porto_destino: 'SANTOS',
    etd: '04-Sep',
    eta: '08-Oct',
};

assert.deepStrictEqual(
    helpers.identificarArmadorEmbarque(exemplo),
    { codigo: 'cosco', nome: 'COSCO Shipping' },
    'Prefixo COSU deve identificar a COSCO Shipping',
);

const coscoUrl = helpers.criarUrlRastreamentoCarga(exemplo);
assert(coscoUrl.startsWith('https://elines.coscoshipping.com/ebusiness/cargoTracking?'));
assert(coscoUrl.includes('trackingType=BOOKING'));
assert(coscoUrl.includes('number=COSU6464481560'));

const cmaUrl = helpers.criarUrlRastreamentoCarga({
    armador: 'CMA CGM',
    numero_booking: 'BOOKING 123',
});
assert(cmaUrl.startsWith('https://www.cma-cgm.com/ebusiness/tracking?'));
assert(cmaUrl.includes('SearchBy=Booking'));
assert(cmaUrl.includes('Reference=BOOKING%20123'));

const armadorExplicitoUrl = helpers.criarUrlRastreamentoCarga({
    armador: 'MSC',
    numero_booking: 'COSU6464481560',
});
assert.strictEqual(armadorExplicitoUrl, '', 'Um armador sem portal integrado não deve enviar a referência ao Google');
const blSemArmador = { numero_bl: 'SZPM12345678', numero_booking: 'BOOK-FIXTURE' };
assert.strictEqual(helpers.identificarArmadorEmbarque(blSemArmador), null, 'SZPM sozinho não confirma o armador');
assert.strictEqual(helpers.criarUrlRastreamentoCarga(blSemArmador), '');
assert.strictEqual(helpers.identificarArmadorEmbarque({ numero_bl: 'HDMUSZPM12345678' }).codigo, 'hmm');
assert.strictEqual(helpers.identificarArmadorEmbarque({ armador: 'Yang Ming Marine Transport' }).codigo, 'yang-ming');

assert.strictEqual(
    helpers.obterReferenciaRastreamentoEmbarque({
        numero_booking: 'BOOK',
        numero_bl: 'BL',
        numero_container: 'CONT',
    }).valor,
    'BOOK',
    'Booking deve ter prioridade sobre BL e container',
);

assert.strictEqual(
    helpers.criarUrlRastreamentoCarga({ numero_bl: 'COSU123' }).includes('trackingType=BILLOFLADING'),
    true,
    'BL COSU deve abrir o rastreamento oficial da COSCO',
);

assert.deepStrictEqual(
    helpers.obterIdentificadorAisEmbarque(exemplo),
    { tipo: 'imo', valor: '9996678', rotulo: 'IMO' },
);
assert.strictEqual(
    helpers.criarUrlRastreamentoNavioApi(exemplo),
    '/api/importacoes/rastreamento/navio?imo=9996678',
);
assert.strictEqual(helpers.erroIdentificadorAisEmbarque(exemplo), '');
assert(helpers.erroIdentificadorAisEmbarque({ numero_imo: '9996679' }).includes('dígito'));
assert(helpers.erroIdentificadorAisEmbarque({ numero_mmsi: '123' }).includes('9 números'));

assert.strictEqual(helpers.abrirRastreamentoCarga({}), '');
assert(alerts.pop().includes('Booking'));
assert.strictEqual(opened.length, 0, 'Nao deve abrir janela sem referencia de carga');

helpers.abrirRastreamentoCarga(exemplo);
assert.strictEqual(opened.length, 0, 'Clique inicial deve manter o resultado dentro do app');
assert.deepStrictEqual(trackingCalls.pop(), { type: 'BOOKING', reference: 'COSU6464481560' });
assert(statuses.pop().includes('balão'));
assert(createdElements[0].innerHTML.includes('Dados cadastrados no embarque'));
assert(createdElements[0].innerHTML.includes('CMA CGM IRON / 0BDPBW'));
assert(createdElements[0].innerHTML.includes('Abrir portal oficial'));
createdElements[0].listeners.click({
    target: {
        closest() {
            return { getAttribute: (name) => name === 'data-rastreamento-carga-acao' ? 'portal' : 'cosco' };
        },
    },
});
assert.strictEqual(opened.length, 1, 'Portal deve abrir apenas pela acao explicita do usuario');
assert.strictEqual(opened[0][0], coscoUrl);

helpers.abrirRastreamentoCarga({ armador: 'CMA CGM', numero_booking: 'BOOKING-123' });
assert.strictEqual(trackingCalls.length, 0, 'CMA CGM nao deve acionar o leitor exclusivo da COSCO');
assert(createdElements[0].innerHTML.includes('Use o portal oficial da CMA CGM'));
assert(createdElements[0].innerHTML.includes('Abrir portal oficial'));

const popoverHtml = helpers.montarRastreamentoCargaPopoverHtml({
    embarque: exemplo,
    referencia: helpers.obterReferenciaRastreamentoEmbarque(exemplo),
    armador: helpers.identificarArmadorEmbarque(exemplo),
    url: coscoUrl,
    loading: false,
    error: '',
    payload: {
        success: true,
        source: '<COSCO>',
        retrievedAt: '2026-08-26T15:00:00Z',
        requestedReferenceType: 'BILLOFLADING',
        resolvedReferenceType: 'BOOKING',
        fallbackUsed: true,
        tracking: {
            bookingNumber: '6464481560',
            bookingStatus: '<Booking Confirmed>',
            blStatus: 'B/L Not Ready',
            latestStatus: '<To Be Shipped>',
            origin: 'Ningbo, CN',
            destination: 'Santos, BR',
            trafficTerm: 'CY | CY',
            equipment: '40HQ*1',
            etd: '2026-09-04 12:00:00 CST',
            ladenReturnTime: '2026-09-07 15:20:00 CST',
            atd: '2026-09-17 10:29:35 CST',
            eta: '2026-10-08 12:00:00 BRT',
            cargoAvailableAt: '2026-10-09 19:00:00 BRT',
        },
    },
});
assert(popoverHtml.includes('&lt;To Be Shipped&gt;'));
assert(popoverHtml.includes('&lt;Booking Confirmed&gt;'));
assert(popoverHtml.includes('&lt;COSCO&gt;'));
assert(!popoverHtml.includes('<To Be Shipped>'));
assert(popoverHtml.includes('Tipo consultado'));
assert(popoverHtml.includes('Booking (tentativa automática após BL)'));
assert(popoverHtml.includes('40HQ*1'));
assert(popoverHtml.includes('2026-10-09 19:00:00 BRT'));
assert(popoverHtml.includes('Trajeto da carga'));
assert(popoverHtml.includes('2026-09-17 10:29:35 CST'));
assert(popoverHtml.includes('Chegada prevista'));
assert(html.includes('estado.url = payload.portalUrl'), 'Portal deve passar a apontar para o tipo que encontrou a carga');

const meta = helpers.montarMetaRastreamentoEmbarqueHtml({
    ...exemplo,
    numero_booking: '<script>alert(1)</script>',
}, true, true);
assert(meta.includes('&lt;script&gt;alert(1)&lt;/script&gt;'));
assert(!meta.includes('<script>'));
for (const label of ['Booking', 'Navio', 'Viagem', 'IMO', 'MMSI', 'Rota', 'ETD', 'ETA', 'Rastrear carga', 'Atualizar posição AIS', 'Ver mapa AIS']) {
    assert(meta.includes(label), `Resumo nao mostra ${label}`);
}

const dadosPosicaoAis = {
    provider: { nome: '<Datalastic>' },
    navio: { nome: '<CMA CGM IRON>', imo: '9996678', mmsi: '249369000' },
    posicao: {
        latitude: -23.95,
        longitude: -46.31,
        velocidade_nos: 17.2,
        curso_graus: 221,
        status_navegacao: '<under way>',
        atualizada_em_utc: '2026-08-26T14:00:00Z',
    },
    viagem: { destino: '<SANTOS>', eta_utc: '2026-10-08T08:00:00Z' },
};
const posicaoHtml = helpers.montarPosicaoNavioApiHtml(dadosPosicaoAis);
assert(posicaoHtml.includes('&lt;CMA CGM IRON&gt;'));
assert(posicaoHtml.includes('&lt;SANTOS&gt;'));
assert(posicaoHtml.includes('openstreetmap.org'));
assert(!posicaoHtml.includes('<CMA CGM IRON>'));

async function verificarConsultaPublicaAis() {
    const painel = fakeElement();
    fakeDocument.getElementById = (id) => id === 'embarquePosicaoApi' ? painel : null;
    const botao = { disabled: false };
    const consultas = [];
    fakeWindow.electronAPI.openPublicVesselPosition = async (identificadores) => {
        assert(botao.disabled, 'Botão deve aguardar a abertura da consulta');
        consultas.push(identificadores);
        return identificadores.mapa
            ? { success: true, source: 'VesselFinder', view: 'mapa' }
            : { success: true, source: 'Maritime Optima', view: 'ficha' };
    };

    const chaveAusente = {
        ok: false,
        status: 503,
        json: async () => ({ detail: { codigo: 'api_nao_configurada' } }),
    };
    responderConsultaAis = async (url, options) => {
        assert.strictEqual(url, '/api/importacoes/rastreamento/navio?imo=9996678');
        assert.strictEqual(options.headers.Authorization, 'Bearer teste');
        return chaveAusente;
    };
    await helpers.atualizarPosicaoNavioApi(exemplo, botao);
    assert.deepStrictEqual(consultas, [{ imo: '9996678', mmsi: '249369000', mapa: false }]);
    assert(painel.textContent.includes('navegador do JK'));
    assert(painel.textContent.includes('Maritime Optima'));
    assert(!botao.disabled);

    await helpers.atualizarPosicaoNavioApi(exemplo, botao, true);
    assert.deepStrictEqual(consultas[1], { imo: '9996678', mmsi: '249369000', mapa: true });
    assert(painel.textContent.includes('Mapa AIS aberto no navegador do JK'));

    responderConsultaAis = async () => ({
        ok: true,
        json: async () => dadosPosicaoAis,
    });
    await helpers.atualizarPosicaoNavioApi(exemplo, botao, true);
    assert.strictEqual(consultas.length, 2, 'API disponível não deve abrir navegador');
    assert(painel.innerHTML.includes('Datalastic'));
    assert(painel.innerHTML.includes('openstreetmap.org/export/embed.html'));
    assert(painel.innerHTML.includes('marker=-23.95%2C-46.31'));
    assert(!helpers.montarPosicaoNavioApiHtml({ posicao: { latitude: null, longitude: null } }, true).includes('<iframe'));

    responderConsultaAis = async () => ({
        ok: false,
        status: 401,
        json: async () => ({ detail: 'Acesso negado' }),
    });
    await helpers.atualizarPosicaoNavioApi(exemplo, botao);
    assert.strictEqual(consultas.length, 2, 'Acesso negado não deve abrir navegador');
    assert.strictEqual(painel.textContent, 'Acesso negado');

    responderConsultaAis = async () => chaveAusente;
    const consultaAnterior = helpers.atualizarPosicaoNavioApi(exemplo, botao);
    helpers.trocarDetalheEmbarque();
    painel.textContent = 'Outro embarque';
    await consultaAnterior;
    assert.strictEqual(consultas.length, 2, 'O embarque anterior não deve abrir uma consulta');
    assert.strictEqual(painel.textContent, 'Outro embarque');
    assert(!botao.disabled);

    const electronAPI = fakeWindow.electronAPI;
    delete fakeWindow.electronAPI;
    await helpers.atualizarPosicaoNavioApi(exemplo, botao, true);
    assert(painel.innerHTML.includes('https://wingo-marine.com/ship/249369000'));
    assert(painel.innerHTML.includes('rel="noopener noreferrer"'));
    assert(!botao.disabled);

    fakeWindow.electronAPI = electronAPI;
    electronAPI.openPublicVesselPosition = async () => { throw new Error('No handler registered'); };
    await helpers.atualizarPosicaoNavioApi({ numero_imo: exemplo.numero_imo }, botao, true);
    assert(painel.innerHTML.includes('https://www.vesselfinder.com/?imo=9996678'));
    assert(!painel.innerHTML.includes('No handler registered'));
    assert(!botao.disabled);
}

async function verificarBuscaDoArmador() {
    const consultas = [];
    let statusYangMing = 'found';
    fakeWindow.electronAPI.searchPublicCargoTracking = async (solicitacao) => {
        consultas.push(solicitacao);
        return { success: true, results: [
            { carrier: 'cosco', status: 'not_found' },
            { carrier: 'cma-cgm', status: 'unavailable' },
            { carrier: 'hmm', status: 'not_found' },
            { carrier: 'yang-ming', status: statusYangMing },
        ], opened: !!solicitacao.carrier };
    };
    helpers.abrirRastreamentoCarga(blSemArmador);
    const painel = createdElements[0];
    assert(painel.innerHTML.includes('Buscar armador'));
    assert.strictEqual(consultas.length, 0, 'Abrir o balão ainda não deve transmitir o BL');

    await helpers.consultarPortaisRastreamentoCarga();
    assert.deepStrictEqual(consultas, [{ type: 'BILLOFLADING', reference: blSemArmador.numero_bl, carrier: undefined }]);
    assert(painel.innerHTML.includes('Referência localizada em Yang Ming'));
    assert(painel.innerHTML.includes('Consulta indisponível'));
    assert(!painel.innerHTML.includes('google.com'));
    assert(painel.innerHTML.includes('Ver em Yang Ming'));
    assert.strictEqual(blSemArmador.armador, undefined, 'A consulta não deve alterar o cadastro');

    await helpers.consultarPortaisRastreamentoCarga('yang-ming');
    assert.deepStrictEqual(consultas[1], { type: 'BILLOFLADING', reference: blSemArmador.numero_bl, carrier: 'yang-ming' });

    statusYangMing = 'not_found';
    await helpers.consultarPortaisRastreamentoCarga();
    assert(painel.innerHTML.includes('Os portais que responderam não encontraram a referência.'));
    assert(painel.innerHTML.includes('Consulta indisponível'));
    assert(!painel.innerHTML.includes('Ver em Yang Ming'));
    assert(painel.innerHTML.includes('data-rastreamento-carga-acao="portal" data-rastreamento-armador="cma-cgm"'));

    delete fakeWindow.electronAPI.searchPublicCargoTracking;
    helpers.abrirRastreamentoCarga(blSemArmador);
    assert(painel.innerHTML.includes('busca automática está disponível no JK para desktop'));
    assert(!painel.innerHTML.includes('Pesquisar portal oficial'));
    assert(painel.innerHTML.includes('HMM'));
}

verificarBuscaDoArmador().then(verificarConsultaPublicaAis).then(() => {
    console.log('OK: campos, persistencia, consulta AIS e rastreamento da carga validados.');
}).catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
