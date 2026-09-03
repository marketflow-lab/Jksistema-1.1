'use strict';

const fs = require('fs');
const path = require('path');
const { app, BrowserWindow, session } = require('electron');
const trackingBrowser = require(path.join(
    __dirname,
    '..',
    '..',
    'electron_app',
    'main',
    'modules',
    'importacoes-tracking-browser.js',
));

app.disableHardwareAcceleration();

app.whenReady().then(async () => {
    const reference = String(process.env.JK_COSCO_TEST_REFERENCE || '').trim();
    const type = String(process.env.JK_COSCO_TEST_TYPE || 'BOOKING').trim().toUpperCase();
    if (!reference) throw new Error('Referencia de teste ausente.');
    const result = await trackingBrowser.trackCoscoShipment(
        { type, reference },
        { BrowserWindow, session, timeoutMs: 30000, includeDiagnostics: true },
    );
    const tracking = result && result.tracking && typeof result.tracking === 'object'
        ? result.tracking
        : {};
    const fieldCount = Object.values(tracking).filter(Boolean).length;
    const serialized = JSON.stringify({
        success: !!(result && result.success),
        code: String(result && result.code || ''),
        fieldCount,
        officialSource: /COSCO Shipping/i.test(String(result && result.source || '')),
        requestedReferenceType: String(result && result.requestedReferenceType || ''),
        resolvedReferenceType: String(result && result.resolvedReferenceType || ''),
        fallbackUsed: !!(result && result.fallbackUsed),
        diagnostics: result && result.diagnostics || null,
    });
    const outputPath = String(process.env.JK_COSCO_TEST_OUTPUT || '').trim();
    if (outputPath) fs.writeFileSync(outputPath, `${serialized}\n`, 'utf8');
    process.stdout.write(`${serialized}\n`);
    app.quit();
}).catch((error) => {
    process.stderr.write(`${String(error && error.message || 'Falha no teste Electron.')}\n`);
    app.exit(1);
});
