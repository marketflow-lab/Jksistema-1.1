'use strict';

const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const vm = require('node:vm');
const { searchPublicCargoTracking } = require('../electron_app/main/modules/importacoes-tracking-browser');

const reference = 'TEST00000000';
const request = { type: 'BILLOFLADING', reference };
const shipmentDetails = '\nLatest Status\nIn Transit\nTraffic Term: CY';
const fixtures = {
    cosco: { path: 'notFoundCT', text: 'Cargo tracking\nNo results found' },
    'cma-cgm': { http: 403 },
    hmm: { result: 'not_found' },
    'yang-ming': { result: 'found' },
};
const windows = [];

function createSession(partition) {
    const result = new EventEmitter();
    result.partition = partition;
    result.setPermissionRequestHandler = () => {};
    result.setPermissionCheckHandler = () => {};
    return result;
}

class FixtureBrowserWindow extends EventEmitter {
    constructor(options) {
        super();
        this.options = options;
        this.carrier = options.webPreferences.session.partition.replace('jk-importacoes-carga-', '');
        this.fixture = fixtures[this.carrier];
        this.loadedUrls = [];
        this.webContents = new EventEmitter();
        this.webContents.isDestroyed = () => this.isDestroyed();
        this.webContents.getURL = () => this.cmaResultUrl || this.loadedUrls.at(-1);
        this.webContents.setWindowOpenHandler = (handler) => { this.openHandler = handler; };
        this.webContents.executeJavaScript = (script) => this.executeFixture(script);
        windows.push(this);
    }

    async loadURL(url) {
        this.loadedUrls.push(url);
        if (this.fixture.path) {
            this.webContents.mainFrame = {
                url,
                frames: [{
                    url: 'https://elines.coscoshipping.com/scct/public/ct/' + this.fixture.path,
                    executeJavaScript: async () => ({ text: this.fixture.text, inputValues: [reference] }),
                }],
            };
        }
        this.webContents.emit('did-navigate', {}, url, this.fixture.http || 200);
        if (this.fixture.cmaRecovers) {
            setTimeout(() => this.webContents.emit('did-navigate', {}, url, 200), 0);
        }
    }

    executeCmaFixture(script) {
        const location = new URL(this.webContents.getURL());
        const resultVisible = location.pathname.endsWith('/search');
        const cell = (innerText, visible = true) => ({ innerText, getClientRects: () => visible ? [1] : [] });
        const row = {
            cells: [cell(reference), cell(this.fixture.cmaStatus || 'In Transit')],
            getClientRects: () => [1],
        };
        const table = {
            getClientRects: () => [1],
            querySelectorAll: (selector) => selector.startsWith('thead')
                ? [cell(this.fixture.cmaHeader), cell('Status')]
                : [row],
        };
        const form = { action: this.fixture.cmaAction || '/ebusiness/tracking/search' };
        const input = {
            ...cell(''),
            value: resultVisible ? this.fixture.cmaResultReference ?? this.cmaReference : '',
            maxLength: 40,
            form,
            dispatchEvent() {},
        };
        const button = {
            ...cell('Search'),
            form,
            getAttribute: () => this.fixture.cmaButtonAction || null,
            click: () => {
                this.cmaReference = input.value;
                this.cmaSubmissions = (this.cmaSubmissions || 0) + 1;
                this.cmaResultUrl = location.origin + '/ebusiness/tracking/search';
            },
        };
        const alert = cell(
            'Your shipment was not found, please modify your search',
            resultVisible && !!this.fixture.cmaAlert,
        );
        return vm.runInNewContext(script, {
            URL,
            Event: class {},
            location,
            document: {
                title: this.fixture.cmaChecking ? 'Verifying the device' : 'Tracking',
                body: { innerText: reference },
                querySelector: (selector) => ({ '#Reference': input, '#btnTracking': button, '#trackingAlertError': alert })[selector],
                querySelectorAll: (selector) => selector === 'table' && this.fixture.cmaHeader ? [table] : [],
            },
        });
    }

    async executeFixture(script) {
        if (this.carrier === 'cma-cgm') return this.executeCmaFixture(script);
        if (/submitTrackingForm/.test(script)) {
            this.submitted = true;
            return true;
        }
        return this.submitted ? this.fixture.result : this.fixture.initialStatus || 'waiting';
    }

    show() { this.visible = true; }
    isDestroyed() { return !!this.destroyed; }
    destroy() { this.destroyed = true; this.emit('closed'); }
}

const dependencies = {
    BrowserWindow: FixtureBrowserWindow,
    session: { fromPartition: createSession },
    timeoutMs: 60,
};

(async () => {
    const discovered = await searchPublicCargoTracking(request, dependencies);
    assert.deepEqual(discovered, {
        success: true,
        results: [
            { carrier: 'cosco', status: 'not_found' },
            { carrier: 'cma-cgm', status: 'unavailable' },
            { carrier: 'hmm', status: 'not_found' },
            { carrier: 'yang-ming', status: 'found' },
        ],
        opened: false,
    });
    assert.equal(new Set(windows.map((win) => win.options.webPreferences.session.partition)).size, 4);
    assert(windows.every((win) => win.isDestroyed()), 'A descoberta deve fechar cada janela');
    assert(windows.every((win) => win.options.webPreferences.sandbox && win.openHandler().action === 'deny'));
    assert.equal(windows[0].loadedUrls.length, 1, 'Uma rejeição não pode mudar o tipo de BL para Booking');
    assert.match(windows[0].loadedUrls[0], /trackingType=BILLOFLADING/);

    fixtures.cosco = { path: 'detailCT', text: 'Booking No: ' + reference + shipmentDetails };
    const wrongType = await searchPublicCargoTracking({ ...request, carrier: 'cosco' }, dependencies);
    assert.deepEqual(wrongType, { success: true, results: [{ carrier: 'cosco', status: 'unavailable' }], opened: true });
    fixtures.cosco.text = 'B/L No: ' + reference + shipmentDetails;
    const exactCosco = await searchPublicCargoTracking({ ...request, carrier: 'cosco' }, dependencies);
    assert.deepEqual(exactCosco, { success: true, results: [{ carrier: 'cosco', status: 'found' }], opened: true });

    const blockedCma = windows.find((win) => win.carrier === 'cma-cgm');
    assert.deepEqual(blockedCma.loadedUrls, ['https://www.cma-cgm.com/ebusiness/tracking']);
    assert.equal(blockedCma.cmaSubmissions, undefined, 'Um 403 persistente não pode receber o BL');

    fixtures['cma-cgm'] = { http: 403, cmaRecovers: true, cmaAlert: true };
    const cmaAfterVerification = await searchPublicCargoTracking({ ...request, carrier: 'cma-cgm' }, dependencies);
    assert.deepEqual(cmaAfterVerification, {
        success: true, results: [{ carrier: 'cma-cgm', status: 'not_found' }], opened: true,
    });
    const recoveredCma = windows.at(-1);
    assert.deepEqual(recoveredCma.loadedUrls, ['https://www.cma-cgm.com/ebusiness/tracking']);
    assert.equal(recoveredCma.cmaReference, reference);
    assert.equal(recoveredCma.cmaSubmissions, 1);

    fixtures['cma-cgm'] = { cmaAlert: true, cmaResultReference: 'OTHER-REFERENCE' };
    const wrongCmaReference = await searchPublicCargoTracking({ ...request, carrier: 'cma-cgm' }, dependencies);
    assert.equal(wrongCmaReference.results[0].status, 'unavailable', 'A rejeição precisa corresponder à referência pesquisada');

    fixtures['cma-cgm'] = { cmaChecking: true };
    const cmaChecking = await searchPublicCargoTracking({ ...request, carrier: 'cma-cgm' }, dependencies);
    assert.equal(cmaChecking.results[0].status, 'unavailable');
    assert.equal(windows.at(-1).cmaSubmissions, undefined, 'O formulário não pode ser usado durante a verificação');

    for (const invalidAction of [
        { cmaAction: '/ebusiness/other' },
        { cmaButtonAction: 'https://unrelated.test/ebusiness/tracking/search' },
    ]) {
        fixtures['cma-cgm'] = invalidAction;
        const rejectedAction = await searchPublicCargoTracking({ ...request, carrier: 'cma-cgm' }, dependencies);
        assert.equal(rejectedAction.results[0].status, 'unavailable');
        assert.equal(windows.at(-1).cmaSubmissions, undefined, 'A referência só pode ir ao formulário oficial');
    }

    fixtures['cma-cgm'] = { cmaHeader: 'Booking No' };
    const wrongCmaType = await searchPublicCargoTracking({ ...request, carrier: 'cma-cgm' }, dependencies);
    assert.equal(wrongCmaType.results[0].status, 'unavailable');
    fixtures['cma-cgm'].cmaHeader = 'B/L No.';
    fixtures['cma-cgm'].cmaStatus = 'Not found';
    const rejectedCma = await searchPublicCargoTracking({ ...request, carrier: 'cma-cgm' }, dependencies);
    assert.equal(rejectedCma.results[0].status, 'unavailable', 'Uma referência ao lado de rejeição não é resultado de carga');
    fixtures['cma-cgm'].cmaStatus = 'In Transit';
    const exactCma = await searchPublicCargoTracking({ ...request, carrier: 'cma-cgm' }, dependencies);
    assert.equal(exactCma.results[0].status, 'found');

    const before = windows.length;
    const rejectedHmm = await searchPublicCargoTracking({ ...request, carrier: 'hmm' }, dependencies);
    assert.deepEqual(rejectedHmm, { success: true, results: [{ carrier: 'hmm', status: 'not_found' }], opened: true });
    assert.equal(windows.length, before + 1, 'A seleção deve consultar apenas o portal escolhido');
    assert.equal(windows.at(-1).visible, true, 'A rejeição não pode esconder a página oficial aberta');

    fixtures.hmm.initialStatus = 'not_found';
    const staleHmm = await searchPublicCargoTracking({ ...request, carrier: 'hmm' }, dependencies);
    assert.equal(staleHmm.results[0].status, 'unavailable', 'Uma rejeição anterior não confirma a consulta atual');
    windows.at(-1).destroy();
    assert(windows.every((win) => win.isDestroyed()));

    const { CARGO_FORM_PROVIDERS } = require('../electron_app/main/modules/importacoes-cargo-tracking-providers');
    const yangMing = CARGO_FORM_PROVIDERS['yang-ming'];
    const resultScript = yangMing.buildResultScript(request);
    const rejection = "Sorry, we can't identify your input.";
    const visibleElement = {
        isConnected: true,
        closest: () => null,
        getBoundingClientRect: () => ({ width: 100, height: 20 }),
        checkVisibility: () => true,
    };
    function inspectYangMingRejection(tabReference, showPanel) {
        const panelId = 'react-aria-test-tabpanel-empty-' + tabReference;
        const tab = {
            ...visibleElement,
            id: 'react-aria-test-tab-empty-' + tabReference,
            innerText: tabReference + ' ✕',
            getAttribute: (name) => name === 'aria-controls' ? panelId : null,
        };
        const panel = {
            ...visibleElement,
            innerText: rejection,
            getAttribute: (name) => name === 'role' ? 'tabpanel' : null,
            checkVisibility: () => showPanel,
        };
        const document = {
            title: 'Cargo Tracking',
            body: { innerText: rejection },
            querySelectorAll: (selector) => selector.includes('[aria-selected="true"]') ? [tab] : [],
            getElementById: (id) => id === panelId ? panel : null,
        };
        return vm.runInNewContext(resultScript, { URL, location: { href: yangMing.url }, document });
    }
    assert.equal(inspectYangMingRejection(reference, true), 'not_found');
    assert.equal(inspectYangMingRejection('OTHER0000000', true), 'waiting');
    assert.equal(inspectYangMingRejection(reference, false), 'waiting');
    console.log('OK: descoberta de carga, verificação de identidade, abertura isolada e rejeição HMM.');
})().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
