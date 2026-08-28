'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
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

    const ipcSource = fs.readFileSync(path.join(__dirname, '..', 'electron_app', 'main', 'modules', 'ipc.js'), 'utf8');
    assert(ipcSource.includes("ipcMain.handle('importacoes-cosco-tracking'"));
    assert(ipcSource.includes('assertTrustedImportacoesTrackingIpcSender(event)'));
    assert(ipcSource.includes("parsed.protocol === 'http:'"));
    assert(ipcSource.includes("importacoes\\.html$"));
    for (const preloadPath of ['preload.js', 'electron_tab_preload.js', path.join('electron_app', 'preload.js')]) {
        const preloadSource = fs.readFileSync(path.join(__dirname, '..', preloadPath), 'utf8');
        assert(preloadSource.includes("trackCoscoShipment: (payload) => ipcRenderer.invoke('importacoes-cosco-tracking'"));
    }
    const installerManifest = JSON.parse(fs.readFileSync(
        path.join(__dirname, '..', 'electron_app', 'installer-required-resources.json'),
        'utf8',
    ));
    assert(installerManifest.requiredSourceFiles.includes('electron_app/main/modules/importacoes-tracking-browser.js'));
    assert(installerManifest.requiredPackagedFiles.includes('local_app/electron_app/main/modules/importacoes-tracking-browser.js'));
    const electronPackage = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'electron_app', 'package.json'), 'utf8'));
    const localAppResource = electronPackage.build.extraResources.find((item) => item.to === 'local_app');
    assert(localAppResource.filter.includes('electron_app/main/modules/**'));

    console.log('OK: parser COSCO, navegador isolado, origem IPC, preloads e pacote validados.');
})().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
