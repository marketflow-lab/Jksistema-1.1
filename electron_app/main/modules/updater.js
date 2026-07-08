function configureAutoUpdaterFeed() {
    if (!autoUpdater) {
        return { success: false, reason: 'electron-updater nao esta disponivel neste pacote.' };
    }
    if (updateFeedConfigured) {
        return { success: true, source: updateFeedSource || 'cached' };
    }
    const configPath = getAppUpdateConfigPath();
    if (configPath) {
        updateFeedConfigured = true;
        updateFeedSource = 'app-update.yml';
        return { success: true, source: updateFeedSource, configPath };
    }
    const feed = getBundledUpdateFeedConfig();
    if (!feed || !feed.provider || !feed.owner || !feed.repo) {
        return {
            success: false,
            reason: 'Canal de atualizacao nao configurado no pacote.'
        };
    }
    try {
        autoUpdater.setFeedURL(feed);
        updateFeedConfigured = true;
        updateFeedSource = 'package-publish';
        logElectronLifecycle('auto-update-feed-configured', { source: updateFeedSource, feed });
        return { success: true, source: updateFeedSource, feed };
    } catch (err) {
        return {
            success: false,
            reason: getUpdateErrorMessage(err)
        };
    }
}

function getUpdateUnavailableReason() {
    if (!app.isPackaged) {
        return 'Atualizacao automatica funciona apenas no app instalado.';
    }
    if (!getAppUpdateConfigPath()) {
        const feed = getBundledUpdateFeedConfig();
        if (!feed || !feed.provider || !feed.owner || !feed.repo) {
            return 'Este instalador nao possui canal de atualizacao automatica configurado. Use a versao mais recente publicada.';
        }
    }
    return '';
}

function isMlAutomationProtected() {
    return mlAutomationProtectionByWebContents.size > 0;
}

function releaseMlAutomationProtectionForContents(contents) {
    if (!contents || !contents.id) return;
    if (mlAutomationProtectionByWebContents.delete(contents.id)) {
        logElectronLifecycle('ml-automation-protection-released', { webContentsId: contents.id });
        maybeInstallDeferredUpdate();
    }
}

function setMlAutomationProtection(contents, active, reason = '') {
    if (!contents || !contents.id) {
        return { success: false, active: isMlAutomationProtected(), count: mlAutomationProtectionByWebContents.size };
    }
    if (active) {
        const firstLock = !mlAutomationProtectionByWebContents.has(contents.id);
        mlAutomationProtectionByWebContents.set(contents.id, {
            reason: String(reason || 'favoritos'),
            startedAt: Date.now()
        });
        if (firstLock && typeof contents.once === 'function') {
            contents.once('destroyed', () => releaseMlAutomationProtectionForContents(contents));
        }
        logElectronLifecycle('ml-automation-protection-enabled', {
            webContentsId: contents.id,
            reason,
            count: mlAutomationProtectionByWebContents.size
        });
    } else {
        releaseMlAutomationProtectionForContents(contents);
    }
    return { success: true, active: isMlAutomationProtected(), count: mlAutomationProtectionByWebContents.size };
}

function maybeInstallDeferredUpdate() {
    if (!deferredDownloadedUpdateInfo || isMlAutomationProtected() || updateInstallInProgress || !autoUpdater) return;
    const info = deferredDownloadedUpdateInfo;
    deferredDownloadedUpdateInfo = null;
    setTimeout(() => {
        installDownloadedUpdateSafely(info).catch((err) => {
            logElectronLifecycle('auto-update-deferred-install-error', err);
        });
    }, 1200);
}

async function installUpdateNow() {
    if (!autoUpdater) {
        const message = 'electron-updater nao esta disponivel neste pacote.';
        sendUpdateStatus('error', { error: message });
        return { success: false, error: message };
    }
    const feedStatus = configureAutoUpdaterFeed();
    if (!feedStatus.success) {
        const message = feedStatus.reason || 'Canal de atualizacao nao configurado.';
        sendUpdateStatus('error', { error: message });
        return { success: false, error: message };
    }
    registerAutoUpdateEvents();
    updateInstallRequested = true;
    if (downloadedUpdateInfo) {
        await installDownloadedUpdateSafely(downloadedUpdateInfo);
        return { success: true, installing: true };
    }
    sendUpdateStatus('download-requested');
    try {
        await autoUpdater.downloadUpdate();
        return { success: true, downloading: true };
    } catch (err) {
        updateInstallRequested = false;
        const message = getUpdateErrorMessage(err);
        sendUpdateStatus('error', { error: message });
        return { success: false, error: message };
    }
}

function sendUpdateStatus(status, payload = {}) {
    const message = { status, ...payload };
    logElectronLifecycle('auto-update-status', message);
    for (const win of BrowserWindow.getAllWindows()) {
        try {
            if (!win.isDestroyed()) {
                win.webContents.send('auto-update-status', message);
            }
        } catch (_err) {}
    }
}

function withTimeout(promise, timeoutMs, fallbackValue) {
    return new Promise((resolve, reject) => {
        const timer = setTimeout(() => resolve(fallbackValue), timeoutMs);
        promise
            .then((value) => {
                clearTimeout(timer);
                resolve(value);
            })
            .catch((err) => {
                clearTimeout(timer);
                reject(err);
            });
    });
}

async function prepareOpenWorkForUpdate(reason = 'auto-update') {
    sendUpdateStatus('saving-work', { reason });
    const windows = BrowserWindow.getAllWindows().filter((win) => win && !win.isDestroyed());
    const results = [];
    for (const win of windows) {
        try {
            const payload = JSON.stringify({ reason });
            const result = await withTimeout(
                win.webContents.executeJavaScript(`
                    (async () => {
                        if (typeof window.jkElectronPrepareForUpdate !== 'function') {
                            return { success: true, reason: 'no-renderer-handler' };
                        }
                        return await window.jkElectronPrepareForUpdate(${payload});
                    })()
                `, true),
                15000,
                { success: false, timedOut: true }
            );
            results.push(result);
        } catch (err) {
            results.push({ success: false, error: getUpdateErrorMessage(err) });
        }
    }
    await flushPersistentSessions();
    sendUpdateStatus('work-saved', { reason, results });
    return { success: true, results };
}

async function installDownloadedUpdateSafely(info = null) {
    if (!autoUpdater || updateInstallInProgress) return;
    if (isMlAutomationProtected()) {
        deferredDownloadedUpdateInfo = info || deferredDownloadedUpdateInfo;
        sendUpdateStatus('deferred', {
            reason: 'favoritos-em-execucao',
            updateInfo: normalizeUpdateInfo(info)
        });
        return;
    }
    updateInstallInProgress = true;
    try {
        await prepareOpenWorkForUpdate('update-install');
        sendUpdateStatus('installing', { updateInfo: normalizeUpdateInfo(info) });
        autoUpdater.quitAndInstall(true, true);
    } catch (err) {
        updateInstallInProgress = false;
        const message = getUpdateErrorMessage(err);
        sendUpdateStatus('error', { error: message });
    }
}

function registerAutoUpdateEvents() {
    if (!autoUpdater || updateEventsRegistered) return false;
    updateEventsRegistered = true;

    autoUpdater.autoDownload = true;
    autoUpdater.autoInstallOnAppQuit = true;
    autoUpdater.allowPrerelease = false;

    autoUpdater.on('checking-for-update', () => {
        sendUpdateStatus('checking');
    });
    autoUpdater.on('update-available', (info) => {
        downloadedUpdateInfo = null;
        updateInstallRequested = true;
        sendUpdateStatus('available', {
            updateInfo: normalizeUpdateInfo(info),
            autoDownload: true,
            autoInstall: true
        });
    });
    autoUpdater.on('update-not-available', (info) => {
        downloadedUpdateInfo = null;
        updateInstallRequested = false;
        sendUpdateStatus('not-available', { updateInfo: normalizeUpdateInfo(info) });
    });
    autoUpdater.on('download-progress', (progress) => {
        sendUpdateStatus('downloading', {
            percent: Math.round(Number(progress && progress.percent || 0)),
            transferred: progress && progress.transferred || 0,
            total: progress && progress.total || 0
        });
    });
    autoUpdater.on('error', (err) => {
        updateInstallRequested = false;
        sendUpdateStatus('error', { error: getUpdateErrorMessage(err) });
    });
    autoUpdater.on('update-downloaded', (info) => {
        downloadedUpdateInfo = info || {};
        sendUpdateStatus('downloaded', {
            updateInfo: normalizeUpdateInfo(info),
            autoInstall: true,
            installRequested: true
        });
        installDownloadedUpdateSafely(info).catch((err) => {
            logElectronLifecycle('auto-update-auto-install-error', err);
        });
    });
    return true;
}

async function checkForUpdates(manual = false) {
    const unavailableReason = getUpdateUnavailableReason();
    if (unavailableReason) {
        const result = {
            success: false,
            skipped: true,
            reason: unavailableReason
        };
        sendUpdateStatus('skipped', { reason: result.reason });
        return result;
    }
    if (!autoUpdater) {
        const result = {
            success: false,
            skipped: true,
            reason: 'electron-updater nao esta disponivel neste pacote.'
        };
        sendUpdateStatus('skipped', { reason: result.reason });
        return result;
    }
    const feedStatus = configureAutoUpdaterFeed();
    if (!feedStatus.success) {
        const result = {
            success: false,
            skipped: true,
            reason: feedStatus.reason || 'Canal de atualizacao nao configurado.'
        };
        sendUpdateStatus('skipped', { reason: result.reason });
        return result;
    }
    if (updateCheckInProgress) {
        sendUpdateStatus('checking', { reason: 'Verificacao de atualizacao ja em andamento.' });
        return { success: true, checking: true };
    }

    registerAutoUpdateEvents();
    updateCheckInProgress = true;
    try {
        const result = await withUpdateCheckTimeout(
            autoUpdater.checkForUpdates(),
            AUTO_UPDATE_CHECK_TIMEOUT_MS,
            'Tempo esgotado ao consultar atualizacao. Confira a internet ou se a release foi publicada no GitHub.'
        );
        const updateInfo = normalizeUpdateInfo(result && result.updateInfo);
        const currentVersion = app.getVersion();
        const latestVersion = updateInfo && updateInfo.version ? updateInfo.version : currentVersion;
        const hasNewVersion = !!(updateInfo && updateInfo.version && updateInfo.version !== currentVersion);
        if (manual && hasNewVersion) {
            sendUpdateStatus('manual-update-available', {
                currentVersion,
                latestVersion,
                updateInfo
            });
        } else if (manual) {
            sendUpdateStatus('up-to-date', {
                currentVersion,
                latestVersion,
                updateInfo
            });
        }
        return {
            success: true,
            currentVersion,
            latestVersion,
            upToDate: !hasNewVersion,
            available: hasNewVersion,
            updateInfo
        };
    } catch (err) {
        const message = getUpdateErrorMessage(err);
        sendUpdateStatus('error', { error: message });
        return {
            success: false,
            error: message
        };
    } finally {
        updateCheckInProgress = false;
    }
}

function scheduleAutoUpdateCheck() {
    if (process.env.JK_DISABLE_AUTO_UPDATE === '1') {
        logElectronLifecycle('auto-update-disabled-by-env');
        return;
    }
    if (!app.isPackaged || !autoUpdater) {
        logElectronLifecycle('auto-update-skipped', {
            packaged: app.isPackaged,
            updaterAvailable: !!autoUpdater
        });
        return;
    }
    const unavailableReason = getUpdateUnavailableReason();
    if (unavailableReason) {
        logElectronLifecycle('auto-update-skipped', { reason: unavailableReason });
        return;
    }
    const feedStatus = configureAutoUpdaterFeed();
    if (!feedStatus.success) {
        logElectronLifecycle('auto-update-skipped', { reason: feedStatus.reason || 'Canal de atualizacao nao configurado.' });
        return;
    }
    registerAutoUpdateEvents();
    const timer = setTimeout(() => {
        checkForUpdates(false).catch((err) => {
            sendUpdateStatus('error', { error: getUpdateErrorMessage(err) });
        });
    }, AUTO_UPDATE_START_DELAY_MS);
    if (typeof timer.unref === 'function') timer.unref();
}

