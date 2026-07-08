function configureMainMenusAndTray() {
    if (!app || typeof app.on !== 'function') {
        return { success: false, reason: 'electron-app-not-ready' };
    }
    return { success: true, installed: false };
}

function destroyMainMenusAndTray() {
    return { success: true };
}

