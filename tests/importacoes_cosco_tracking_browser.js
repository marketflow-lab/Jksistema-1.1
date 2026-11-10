'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { EventEmitter } = require('events');

const modulePath = path.join(
    __dirname,
    '..',
    'electron_app',
    'main',
    'modules',
    'importacoes-tracking-browser.js',
);
const trackingBrowser = require(modulePath);

const sampleText = `
Booking No 6464481560
B/L Not Ready
Booking Confirmed
Ningbo, CN
POR
Santos, BR
FND
Traffic Term: CY | CY
Latest Status
To Be Shipped
40HQ*1
Full Chain
Container
Laden Return Time
2026-09-07 15:20:00 CST
ATD
2026-09-17 10:29:35 CST
ETD
2026-09-04 12:00:00 CST
ETA
2026-10-08 12:00:00 BRT
Estimated cargo available for
pickup at destination
2026-10-09 19:00:00 BRT
`;

assert.deepStrictEqual(
    trackingBrowser.normalizeTrackingRequest({ type: 'booking', reference: ' cosu6464481560 ' }),
    { type: 'BOOKING', reference: 'COSU6464481560' },
);
assert.throws(
    () => trackingBrowser.normalizeTrackingRequest({ type: 'BOOKING', reference: 'COSU1&redirect=https://evil.test' }),
    /invalida/i,
);
assert.throws(
    () => trackingBrowser.normalizeTrackingRequest({ type: 'URL', reference: 'COSU6464481560' }),
    /invalido/i,
);

const portalUrl = trackingBrowser.buildCoscoTrackingUrl({
    type: 'BOOKING',
    reference: 'COSU6464481560',
});
assert(portalUrl.startsWith('https://elines.coscoshipping.com/ebusiness/cargoTracking?'));
assert(portalUrl.includes('trackingType=BOOKING'));
assert(portalUrl.includes('number=COSU6464481560'));
assert.strictEqual(trackingBrowser.isAllowedCoscoTrackingUrl(portalUrl), true);
assert.strictEqual(trackingBrowser.isAllowedCoscoTrackingUrl('http://elines.coscoshipping.com/ebusiness/cargoTracking'), false);
assert.strictEqual(trackingBrowser.isAllowedCoscoTrackingUrl('https://elines.coscoshipping.com.evil.test/'), false);
assert.strictEqual(trackingBrowser.isAllowedCoscoFrameUrl('https://sub.coscoshipping.com/app'), true);
assert.strictEqual(trackingBrowser.isAllowedCoscoFrameUrl('https://coscoshipping.com.evil.test/app'), false);

const parsed = trackingBrowser.extractCoscoTrackingFromText(sampleText, 'COSU6464481560');
assert.deepStrictEqual(parsed, {
    bookingNumber: '6464481560',
    bookingStatus: 'Booking Confirmed',
    blStatus: 'B/L Not Ready',
    latestStatus: 'To Be Shipped',
    origin: 'Ningbo, CN',
    destination: 'Santos, BR',
    trafficTerm: 'CY | CY',
    equipment: '40HQ*1',
    etd: '2026-09-04 12:00:00 CST',
    ladenReturnTime: '2026-09-07 15:20:00 CST',
    atd: '2026-09-17 10:29:35 CST',
    eta: '2026-10-08 12:00:00 BRT',
    cargoAvailableAt: '2026-10-09 19:00:00 BRT',
});
assert.strictEqual(
    trackingBrowser.extractCoscoTrackingFromText(sampleText, 'COSU0000000000'),
    null,
    'O parser nao pode aceitar o resultado de outra referencia',
);
assert.strictEqual(
    trackingBrowser.extractCoscoTrackingFromText('Booking No 6464481560', 'COSU6464481560'),
    null,
    'O parser deve aguardar o resultado, nao apenas o cabecalho do Booking',
);

class FakeWebContents extends EventEmitter {
    constructor() {
        super();
        this.destroyed = false;
        this.windowOpenHandler = null;
        const emptySnapshot = {
            text: '',
            title: 'Cargo Tracking',
            pathname: '/ebusiness/cargoTracking',
            frameCount: 2,
            inputValues: [],
            hasBookingLabel: false,
            hasLatestStatus: false,
            hasSearch: true,
            hasAccessFailure: false,
            hasLoading: false,
        };
        const externalFrame = {
            url: 'https://evil.test/frame',
            frames: [],
            async executeJavaScript() {
                FakeWebContents.externalFrameExecutions += 1;
                return { ...emptySnapshot, text: sampleText };
            },
        };
        const officialFrame = {
            url: 'https://elines.coscoshipping.com/scct/public/ct/detailCT',
            frames: [],
            async executeJavaScript() {
                return {
                    ...emptySnapshot,
                    text: sampleText.replace('Booking No 6464481560', 'Booking No'),
                    inputValues: ['COSU6464481560'],
                    hasBookingLabel: true,
                    hasLatestStatus: true,
                };
            },
        };
        this.mainFrame = {
            url: 'https://elines.coscoshipping.com/ebusiness/cargoTracking',
            frames: [externalFrame, officialFrame],
            async executeJavaScript() { return emptySnapshot; },
        };
    }
    setWindowOpenHandler(handler) { this.windowOpenHandler = handler; }
    isDestroyed() { return this.destroyed; }
    async executeJavaScript() { return this.mainFrame.executeJavaScript(); }
}
FakeWebContents.externalFrameExecutions = 0;

class FakeBrowserWindow {
    constructor(options) {
        this.options = options;
        this.destroyed = false;
        this.webContents = new FakeWebContents();
        FakeBrowserWindow.last = this;
    }
    async loadURL(url, options) {
        this.loadedUrl = url;
        this.loadOptions = options;
    }
    isDestroyed() { return this.destroyed; }
    destroy() {
        this.destroyed = true;
        this.webContents.destroyed = true;
    }
}

const trackingSession = new EventEmitter();
trackingSession.setPermissionRequestHandler = (handler) => { trackingSession.permissionRequestHandler = handler; };
trackingSession.setPermissionCheckHandler = (handler) => { trackingSession.permissionCheckHandler = handler; };
const fakeSessionModule = {
    fromPartition(partition, options) {
        fakeSessionModule.partition = partition;
        fakeSessionModule.options = options;
        return trackingSession;
    },
};

class FallbackWebContents extends EventEmitter {
    constructor(owner) {
        super();
        this.destroyed = false;
        const baseSnapshot = {
            title: 'Cargo Tracking',
            frameCount: 0,
            inputValues: [],
            hasBookingLabel: true,
            hasLatestStatus: false,
            hasSearch: true,
            hasAccessFailure: false,
            hasLoading: false,
            hasNoResult: false,
        };
        const officialFrame = {
            get url() {
                return /trackingType=BOOKING/i.test(owner.loadedUrl || '')
                    ? 'https://elines.coscoshipping.com/scct/public/ct/detailCT'
                    : 'https://elines.coscoshipping.com/scct/public/ct/notFoundCT';
            },
            frames: [],
            async executeJavaScript() {
                if (!/trackingType=BOOKING/i.test(owner.loadedUrl || '')) {
                    return {
                        ...baseSnapshot,
                        pathname: '/scct/public/ct/notFoundCT',
                        text: 'Cargo Tracking\nB/L No.\nNo results found. Please check the number entered.',
                        inputValues: ['COSU6464481560'],
                        hasNoResult: true,
                    };
                }
                return {
                    ...baseSnapshot,
                    pathname: '/scct/public/ct/detailCT',
                    text: sampleText.replace('Booking No 6464481560', 'Booking No'),
                    inputValues: ['COSU6464481560'],
                    hasLatestStatus: true,
                };
            },
        };
        this.mainFrame = {
            url: 'https://elines.coscoshipping.com/ebusiness/cargoTracking',
            frames: [officialFrame],
            async executeJavaScript() {
                return { ...baseSnapshot, pathname: '/ebusiness/cargoTracking', text: '' };
            },
        };
    }
    setWindowOpenHandler(handler) { this.windowOpenHandler = handler; }
    isDestroyed() { return this.destroyed; }
}

class FallbackBrowserWindow {
    constructor(options) {
        this.options = options;
        this.destroyed = false;
        this.loadedUrls = [];
        this.webContents = new FallbackWebContents(this);
        FallbackBrowserWindow.instances.push(this);
    }
    async loadURL(url) {
        this.loadedUrl = url;
        this.loadedUrls.push(url);
    }
    isDestroyed() { return this.destroyed; }
    destroy() {
        this.destroyed = true;
        this.webContents.destroyed = true;
    }
}
FallbackBrowserWindow.instances = [];

class PublicVesselBrowserWindow extends EventEmitter {
    constructor(options) {
        super();
        this.options = options;
        this.loadedUrls = [];
        this.webContents = new EventEmitter();
        this.webContents.isDestroyed = () => this.isDestroyed();
        this.webContents.setWindowOpenHandler = (handler) => { this.openHandler = handler; };
        this.webContents.stop = () => {};
        PublicVesselBrowserWindow.last = this;
    }
    loadURL(url) {
        this.loadedUrls.push(url);
        const response = PublicVesselBrowserWindow.responses.shift();
        if (response !== undefined) {
            this.webContents.emit('did-navigate', {}, response.url || url, response.status);
        }
        // Map tiles can keep the normal load promise pending after the page opens.
        return new Promise(() => {});
    }
    setTitle() {}
    show() { this.visible = true; }
    isDestroyed() { return !!this.destroyed; }
    destroy() {
        this.destroyed = true;
        this.emit('closed');
    }
}

(async () => {
    const result = await trackingBrowser.trackCoscoShipment(
        { type: 'BOOKING', reference: 'COSU6464481560' },
        { BrowserWindow: FakeBrowserWindow, session: fakeSessionModule },
    );
    assert.strictEqual(result.success, true);
    assert.strictEqual(result.tracking.latestStatus, 'To Be Shipped');
    assert.strictEqual(FakeWebContents.externalFrameExecutions, 0, 'Frame externo nao pode ser lido');
    assert.strictEqual(fakeSessionModule.partition, 'jk-importacoes-cosco-public');
    assert.deepStrictEqual(fakeSessionModule.options, { cache: true });
    assert.strictEqual(FakeBrowserWindow.last.options.show, false);
    assert.strictEqual(FakeBrowserWindow.last.options.webPreferences.nodeIntegration, false);
    assert.strictEqual(FakeBrowserWindow.last.options.webPreferences.contextIsolation, true);
    assert.strictEqual(FakeBrowserWindow.last.options.webPreferences.sandbox, true);
    assert.strictEqual(FakeBrowserWindow.last.webContents.windowOpenHandler().action, 'deny');
    let prevented = false;
    FakeBrowserWindow.last.webContents.emit(
        'will-navigate',
        { preventDefault() { prevented = true; } },
        'https://evil.test/',
    );
    assert.strictEqual(prevented, true, 'Navegacao para origem externa deve ser bloqueada');
    let permissionAllowed = true;
    trackingSession.permissionRequestHandler(null, 'geolocation', (allowed) => { permissionAllowed = allowed; });
    assert.strictEqual(permissionAllowed, false);
    assert.strictEqual(trackingSession.permissionCheckHandler(), false);
    assert.strictEqual(trackingSession.listenerCount('will-download'), 0);
    assert.strictEqual(FakeBrowserWindow.last.destroyed, true, 'Janela oculta deve ser destruida apos a leitura');

    const fallbackResult = await trackingBrowser.trackCoscoShipment(
        { type: 'BILLOFLADING', reference: 'COSU6464481560' },
        { BrowserWindow: FallbackBrowserWindow, session: fakeSessionModule, includeDiagnostics: true },
    );
    assert.strictEqual(fallbackResult.success, true);
    assert.strictEqual(fallbackResult.requestedReferenceType, 'BILLOFLADING');
    assert.strictEqual(fallbackResult.resolvedReferenceType, 'BOOKING');
    assert.strictEqual(fallbackResult.fallbackUsed, true);
    assert(fallbackResult.portalUrl.includes('trackingType=BOOKING'));
    assert.strictEqual(FallbackBrowserWindow.instances.length, 1);
    assert(FallbackBrowserWindow.instances[0].loadedUrls[0].includes('trackingType=BILLOFLADING'));
    assert(FallbackBrowserWindow.instances[0].loadedUrls[1].includes('trackingType=BOOKING'));
    assert(FallbackBrowserWindow.instances.every(instance => instance.destroyed));

    const publicDependencies = { BrowserWindow: PublicVesselBrowserWindow, session: fakeSessionModule };
    const vessel = { imo: '9660011', mmsi: '636026610' };
    PublicVesselBrowserWindow.responses = [{ status: 200 }];
    const map = await trackingBrowser.openPublicVesselPosition(
        { ...vessel, mapa: true, bl: 'PRIVATE-TEST-BL' },
        publicDependencies,
    );
    const mapWindow = PublicVesselBrowserWindow.last;
    assert.deepStrictEqual(map, { success: true, source: 'VesselFinder', view: 'mapa' });
    assert.deepStrictEqual(mapWindow.loadedUrls, ['https://www.vesselfinder.com/?imo=9660011']);
    assert.strictEqual(mapWindow.visible, true);
    assert.strictEqual(fakeSessionModule.partition, 'jk-importacoes-ais-public');
    assert.strictEqual(mapWindow.options.webPreferences.preload, undefined);
    assert.strictEqual(mapWindow.options.webPreferences.sandbox, true);
    assert.strictEqual(mapWindow.openHandler().action, 'deny');
    let redirectBlocked = false;
    mapWindow.webContents.emit(
        'will-redirect',
        { preventDefault() { redirectBlocked = true; } },
        'http://127.0.0.1/importacoes.html',
        false,
        true,
    );
    assert.strictEqual(redirectBlocked, true);

    const wingoPage = 'https://wingo-marine.com/ship/ym-movement-636026610';
    PublicVesselBrowserWindow.responses = [{ status: 403 }, { status: 200, url: wingoPage }];
    const fallbackMap = await trackingBrowser.openPublicVesselPosition({ ...vessel, mapa: true }, publicDependencies);
    assert.deepStrictEqual(fallbackMap, { success: true, source: 'Wingo Marine', view: 'mapa' });
    assert.strictEqual(mapWindow.isDestroyed(), true);
    const wingoContents = PublicVesselBrowserWindow.last.webContents;
    for (const [url, mustBlock] of [
        [wingoPage, false],
        ['https://wingo-marine.com/ship/another-ship-356189000', true],
        ['https://wingo-marine.com/login', true],
        ['https://www.vesselfinder.com/?imo=9660011', true],
    ]) {
        let blocked = false;
        wingoContents.emit('will-redirect', { preventDefault() { blocked = true; } }, url, false, true);
        assert.strictEqual(blocked, mustBlock);
    }

    PublicVesselBrowserWindow.responses = [{ status: 403 }, { status: 403 }, { status: 200 }];
    const fallbackPosition = await trackingBrowser.openPublicVesselPosition(
        { ...vessel, mapa: true },
        publicDependencies,
    );
    assert.deepStrictEqual(fallbackPosition, { success: true, source: 'Maritime Optima', view: 'ficha' });
    assert.deepStrictEqual(PublicVesselBrowserWindow.last.loadedUrls, [
        'https://www.vesselfinder.com/?imo=9660011',
        'https://wingo-marine.com/ship/636026610',
        'https://maritimeoptima.com/public/vessels/pages/imo:9660011/mmsi:636026610/vessel.html',
    ]);
    assert.strictEqual(mapWindow.isDestroyed(), true);

    PublicVesselBrowserWindow.responses = [{ status: 200 }];
    const search = await trackingBrowser.openPublicVesselPosition({ mmsi: vessel.mmsi }, publicDependencies);
    assert.deepStrictEqual(search, { success: true, source: 'MyShipTracking', view: 'busca' });
    assert.deepStrictEqual(PublicVesselBrowserWindow.last.loadedUrls, [
        'https://www.myshiptracking.com/vessels?name=636026610',
    ]);

    PublicVesselBrowserWindow.responses = [];
    const unavailable = await trackingBrowser.openPublicVesselPosition(
        { imo: vessel.imo },
        { ...publicDependencies, timeoutMs: 25 },
    );
    assert.strictEqual(unavailable.success, false);
    assert.strictEqual(PublicVesselBrowserWindow.last.isDestroyed(), true);
    assert.strictEqual(trackingSession.listenerCount('will-download'), 0);

    const ipcSource = fs.readFileSync(path.join(__dirname, '..', 'electron_app', 'main', 'modules', 'ipc.js'), 'utf8');
    assert(ipcSource.includes("ipcMain.handle('importacoes-cosco-tracking'"));
    assert(ipcSource.includes("ipcMain.handle('importacoes-cargo-tracking-search'"));
    assert(ipcSource.includes("ipcMain.handle('importacoes-ais-browser-open'"));
    assert(ipcSource.includes('assertTrustedImportacoesTrackingIpcSender(event)'));
    assert(ipcSource.includes("parsed.protocol === 'http:'"));
    assert(ipcSource.includes("importacoes\\.html$"));
    for (const preloadPath of ['preload.js', 'electron_tab_preload.js', path.join('electron_app', 'preload.js')]) {
        const preloadSource = fs.readFileSync(path.join(__dirname, '..', preloadPath), 'utf8');
        assert(preloadSource.includes("trackCoscoShipment: (payload) => ipcRenderer.invoke('importacoes-cosco-tracking'"));
        let bridge;
        const calls = [];
        vm.runInNewContext(preloadSource, {
            require: () => ({
                contextBridge: {
                    exposeInMainWorld(name, api) { if (name === 'electronAPI') bridge = api; },
                },
                ipcRenderer: {
                    invoke(channel, payload) { calls.push({ channel, payload: { ...payload } }); },
                },
            }),
            window: { addEventListener() {} },
            process: { on() {} },
        });
        assert.strictEqual(typeof bridge?.openPublicVesselPosition, 'function', preloadPath);
        bridge.openPublicVesselPosition({ ...vessel, mapa: true, bl: 'PRIVATE-TEST-BL' });
        const cargoQuery = { type: 'BILLOFLADING', reference: 'TEST00012345', carrier: 'hmm' };
        bridge.searchPublicCargoTracking({ ...cargoQuery, url: 'https://example.invalid/' });
        assert.deepStrictEqual(calls, [{
            channel: 'importacoes-ais-browser-open',
            payload: { ...vessel, mapa: true },
        }, {
            channel: 'importacoes-cargo-tracking-search',
            payload: cargoQuery,
        }], preloadPath);
    }
    const installerManifest = JSON.parse(fs.readFileSync(
        path.join(__dirname, '..', 'electron_app', 'installer-required-resources.json'),
        'utf8',
    ));
    assert(installerManifest.requiredSourceFiles.includes('electron_app/main/modules/importacoes-tracking-browser.js'));
    assert(installerManifest.requiredPackagedFiles.includes('local_app/electron_app/main/modules/importacoes-tracking-browser.js'));
    assert(installerManifest.requiredSourceFiles.includes('electron_app/main/modules/importacoes-cargo-tracking-providers.js'));
    assert(installerManifest.requiredPackagedFiles.includes('local_app/electron_app/main/modules/importacoes-cargo-tracking-providers.js'));
    const electronPackage = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'electron_app', 'package.json'), 'utf8'));
    const localAppResource = electronPackage.build.extraResources.find((item) => item.to === 'local_app');
    assert(localAppResource.filter.includes('electron_app/main/modules/**'));

    console.log('OK: rastreamento COSCO e AIS, navegador isolado, origem IPC, preloads e pacote validados.');
})().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
