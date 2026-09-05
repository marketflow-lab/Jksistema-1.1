const fs = require('fs');
const path = require('path');
const vm = require('vm');

const MAIN_MODULES = [
    path.join(__dirname, 'electron_app', 'main', 'modules', 'local-app-paths.js'),
    path.join(__dirname, 'electron_app', 'main', 'modules', 'private-credential-bootstrap.js'),
    path.join(__dirname, 'electron_app', 'main', 'modules', 'updater.js'),
    path.join(__dirname, 'electron_app', 'main', 'modules', 'backend.js'),
    path.join(__dirname, 'electron_app', 'main', 'modules', 'window.js'),
    path.join(__dirname, 'electron_app', 'main', 'modules', 'menus-tray.js'),
    path.join(__dirname, 'electron_app', 'main', 'modules', 'favoritos-worker-browser.js'),
    path.join(__dirname, 'electron_app', 'main', 'modules', 'ipc.js')
];

let electronBootstrapSource = '';
for (const modulePath of MAIN_MODULES) {
    electronBootstrapSource += `\n// ==== bootstrap: ${path.basename(modulePath)} ====\n`;
    electronBootstrapSource += fs.readFileSync(modulePath, 'utf8');
    electronBootstrapSource += '\n';
}

const runElectronBootstrap = vm.runInThisContext(
    `(function(require, __dirname, __filename, module, exports) {\n${electronBootstrapSource}\n})`,
    {
        filename: path.join(__dirname, 'electron_app', 'main', 'bootstrap.js'),
        lineOffset: -1,
        columnOffset: 0,
        displayErrors: true
    }
);

runElectronBootstrap(require, __dirname, __filename, module, exports);
