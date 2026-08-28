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
    'async function atualizarPosicaoNavioApi(embarque, botao)',
    "'/api/importacoes/rastreamento/navio?'",
    'headers: Object.assign({}, obterAuthHeaders())',
    "document.getElementById('embarquePosicaoApi')",
    'montarPosicaoNavioApiHtml(payload)',
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
    `${helpersSource}
    return {
        identificarArmadorEmbarque,
        obterReferenciaRastreamentoEmbarque,
        criarUrlRastreamentoCarga,
        obterIdentificadorAisEmbarque,
        erroIdentificadorAisEmbarque,
        criarUrlRastreamentoNavioApi,
        abrirRastreamentoCarga,
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
    async () => ({ ok: true, json: async () => ({}) }),
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
assert(armadorExplicitoUrl.startsWith('https://www.google.com/search?q='));
assert(decodeURIComponent(armadorExplicitoUrl).includes('MSC'));

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
            return { getAttribute: () => 'portal' };
        },
    },
});
assert.strictEqual(opened.length, 1, 'Portal deve abrir apenas pela acao explicita do usuario');
assert.strictEqual(opened[0][0], coscoUrl);

helpers.abrirRastreamentoCarga({ armador: 'CMA CGM', numero_booking: 'BOOKING-123' });
assert.strictEqual(trackingCalls.length, 0, 'CMA CGM nao deve acionar o leitor exclusivo da COSCO');
assert(createdElements[0].innerHTML.includes('consulta automática no balão ainda não está disponível para CMA CGM'));
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
assert(html.includes('estado.url = payload.portalUrl'), 'Portal deve passar a apontar para o tipo que encontrou a carga');

const meta = helpers.montarMetaRastreamentoEmbarqueHtml({
    ...exemplo,
    numero_booking: '<script>alert(1)</script>',
}, true, true);
assert(meta.includes('&lt;script&gt;alert(1)&lt;/script&gt;'));
assert(!meta.includes('<script>'));
for (const label of ['Booking', 'Navio', 'Viagem', 'IMO', 'MMSI', 'Rota', 'ETD', 'ETA', 'Rastrear carga', 'Atualizar posição AIS']) {
    assert(meta.includes(label), `Resumo nao mostra ${label}`);
}

const posicaoHtml = helpers.montarPosicaoNavioApiHtml({
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
});
assert(posicaoHtml.includes('&lt;CMA CGM IRON&gt;'));
assert(posicaoHtml.includes('&lt;SANTOS&gt;'));
assert(posicaoHtml.includes('openstreetmap.org'));
assert(!posicaoHtml.includes('<CMA CGM IRON>'));

console.log('OK: campos, persistencia, API AIS, balao e links de rastreamento validados.');
