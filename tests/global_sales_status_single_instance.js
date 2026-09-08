'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const globalUi = fs.readFileSync(path.join(root, 'static', 'auth', 'global-ui.js'), 'utf8');
const integracoes = fs.readFileSync(path.join(root, 'static', 'integracoes.html'), 'utf8');
const adminUsuarios = fs.readFileSync(path.join(root, 'static', 'admin_usuarios.html'), 'utf8');

assert.doesNotThrow(() => new Function(globalUi), 'global-ui.js possui erro de sintaxe');

const monitorStart = globalUi.indexOf('(function initGlobalVendasSyncMonitor()');
const nextGlobalComponent = globalUi.indexOf('(function initGlobalAiSidebar()', monitorStart);
const embeddedGuard = globalUi.indexOf('if (!deveExibirStatusGlobalNesteDocumento()) return;', monitorStart);
const monitorInit = globalUi.indexOf('window.__jkSyncMonitorInit = true;', monitorStart);
const widgetCreation = globalUi.indexOf("el.id = 'jk-global-sync-widget';", monitorStart);

assert(monitorStart >= 0, 'monitor global de vendas ausente');
assert(nextGlobalComponent > monitorStart, 'limite do monitor global de vendas ausente');
assert(embeddedGuard > monitorStart, 'monitor global perdeu a guarda de conteudo embutido');
assert(embeddedGuard < monitorInit, 'conteudo embutido ainda inicializa o monitor global');
assert(embeddedGuard < widgetCreation, 'conteudo embutido ainda pode criar o widget global');
assert.match(
    globalUi,
    /function deveExibirStatusGlobalNesteDocumento\(search = window\.location\.search\) \{\s*return !new URLSearchParams\(search \|\| ''\)\.has\('embed'\);\s*\}/,
    'decisao do status global precisa seguir o marcador explicito embed',
);

const predicateSource = globalUi.match(
    /function deveExibirStatusGlobalNesteDocumento\(search = window\.location\.search\) \{\s*return !new URLSearchParams\(search \|\| ''\)\.has\('embed'\);\s*\}/,
)[0];
const deveExibirStatusGlobalNesteDocumento = new Function(`return (${predicateSource});`)();

assert.strictEqual(deveExibirStatusGlobalNesteDocumento(''), true, 'pagina principal perdeu o status global');
assert.strictEqual(deveExibirStatusGlobalNesteDocumento('?tab=compartilhar'), true, 'modulo principal perdeu o status global');
assert.strictEqual(deveExibirStatusGlobalNesteDocumento('?embed=share'), false, 'iframe Compartilhar ainda exibe status duplicado');
assert.strictEqual(deveExibirStatusGlobalNesteDocumento('?embed=outro'), false, 'conteudo embutido generico ainda exibe status global');

const embeddedWindow = { location: { search: '?embed=share' } };
const forbiddenDocument = new Proxy({}, {
    get() {
        throw new Error('conteudo embutido tentou acessar o DOM para criar o status');
    },
});
assert.doesNotThrow(
    () => new Function('window', 'document', 'URLSearchParams', globalUi.slice(monitorStart, nextGlobalComponent))(
        embeddedWindow,
        forbiddenDocument,
        URLSearchParams,
    ),
    'conteudo embutido ainda executa o monitor global',
);
assert.strictEqual(embeddedWindow.__jkSyncMonitorInit, undefined, 'iframe embutido marcou o monitor como iniciado');

assert.match(integracoes, /data-src="\/admin_usuarios\.html\?embed=share"/, 'iframe Compartilhar perdeu o marcador embed');
assert.match(integracoes, /<script src="\/auth\.js[^>]*><\/script>/, 'modulo Integracoes perdeu o carregador do status global');
assert.match(adminUsuarios, /<script src="\/auth\.js[^>]*><\/script>/, 'pagina de usuarios perdeu o carregador global');

console.log('global_sales_status_single_instance: ok');
