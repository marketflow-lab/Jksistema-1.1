const assert = require('assert');
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');

function read(relativePath) {
    return fs.readFileSync(path.join(root, relativePath), 'utf8');
}

function compileInlineScripts(relativePath) {
    const html = read(relativePath);
    const scripts = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)]
        .map(match => match[1])
        .filter(source => source.trim());
    scripts.forEach((source, index) => {
        assert.doesNotThrow(
            () => new Function(source),
            `${relativePath}: script inline ${index + 1} possui erro de sintaxe`,
        );
    });
    return html;
}

const admin = compileInlineScripts('static/admin_usuarios.html');
const integrations = compileInlineScripts('static/integracoes.html');
const settings = compileInlineScripts('static/configuracoes.html');

assert(admin.includes('machineSyncProgressOverlay'), 'balão local da sincronização ausente');
assert(admin.includes('atualizarBalaoSincronizacaoMaquinas'), 'controle do balão da sincronização ausente');
assert(admin.includes('await recarregarLojasAposSincronizacao(scopes);'), 'recarga de lojas após importação ausente');
assert(integrations.includes('sharedSyncScreenOverlay'), 'balão central da Central de Integrações ausente');
assert(integrations.includes('window.jkIntegracoesSetSyncProgress'), 'ponte de status do iframe ausente');
assert(integrations.includes('window.jkIntegracoesReloadStores'), 'ponte de recarga das lojas ausente');
assert(integrations.includes('throw error;'), 'falha ao recarregar lojas precisa chegar ao fluxo de importaÃ§Ã£o');
assert(integrations.includes('void loadStores().catch(() => {});'), 'carga inicial precisa tratar a rejeiÃ§Ã£o localmente');
assert(settings.includes("defaultSeverity: 'blocker'"), 'bloqueios do Context Hub perderam sua severidade');
assert(settings.includes('const blockers = findings.filter(finding => finding.blocking);'), 'avisos e bloqueios do Context Hub continuam misturados');
assert.strictEqual(admin, read('admin_usuarios.html'), 'admin_usuarios.html divergiu do espelho oficial');
assert.strictEqual(integrations, read('integracoes.html'), 'integracoes.html divergiu do espelho oficial');
assert.strictEqual(settings, read('configuracoes.html'), 'configuracoes.html divergiu do espelho oficial');

console.log('shared_sync_ui_regression: ok');
