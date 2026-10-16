'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'static/admin_usuarios.html'), 'utf8');
function section(start, end) {
    const first = html.indexOf(start);
    const last = html.indexOf(end, first);
    assert(first >= 0 && last > first, `Missing source section: ${start}`);
    return html.slice(first, last);
}
const formatter = section('    function mensagemErroSincronizacao(', '    function setMachineSyncStatus(');
const message = new Function(`${formatter}; return mensagemErroSincronizacao;`)();
assert.strictEqual(String(new Error({ code: 'example', message: 'Mensagem real' }).message), '[object Object]');
assert.strictEqual(message({ detail: 'Compartilhamento pausado.' }), 'Compartilhamento pausado.');
assert.strictEqual(message({ detail: { code: 'shared_sync_store_identity_invalid', message: 'Loja ausente no destino.' } }),
    'Loja ausente no destino. (código: shared_sync_store_identity_invalid)');
assert.strictEqual(message({ message: 'Falha de conexão.' }), 'Falha de conexão.');
assert.strictEqual(message({ detail: [{ msg: 'Campo obrigatório', loc: ['body', 'operation_id'], input: 'PRIVATE_TEST_VALUE' },
    { msg: 'Formato inválido' }, { msg: 'Campo obrigatório' }] }), 'Campo obrigatório; Formato inválido');
assert.strictEqual(message({ detail: { message: 'Snapshot inválido', code: 'bad_snapshot', file: 'PRIVATE_TEST_VALUE',
    authorization: 'PRIVATE_TEST_VALUE', input: { password: 'PRIVATE_TEST_VALUE' } } }), 'Snapshot inválido (código: bad_snapshot)');
assert.strictEqual(message({ detail: { code: 'stores_busy' } }, 'Falha ao importar.'), 'Falha ao importar. (código: stores_busy)');
assert.strictEqual(message({ detail: { code: 'stores_busy', message: 'Bloqueado: stores_busy.' } }), 'Bloqueado: stores_busy.');
assert.strictEqual(message({ detail: { input: 'PRIVATE_TEST_VALUE' } }, 'Falha ao importar.'), 'Falha ao importar.');
assert.strictEqual(message({ detail: { code: '<script>PRIVATE_TEST_VALUE</script>' } }, 'Falha ao importar.'), 'Falha ao importar.');
assert.strictEqual(message(null, 'Falha ao importar.'), 'Falha ao importar.');
const cycle = {};
cycle.detail = cycle;
assert.strictEqual(message(cycle, 'Falha ao importar.'), 'Falha ao importar.');
const preview = section('    async function criarPreviaSincronizacaoManual(', '    async function executarSincronizacaoMinhasMaquinas(');
const run = section('    async function executarVinculoCompartilhamentoUsuario(', '    function enviarVinculoCompartilhamentoUsuario(');
async function runScenario(responses) {
    const calls = [], status = [], notifications = [];
    const context = {
        Set, Number, String, Array, Math,
        compartilhamentoUsuariosCache: { links: [{ id: 'link-test', scopes: ['cadastro'] }] },
        sincronizacaoFirebasePronta: () => true,
        atualizarInterfaceProvisionamentoFirebase: () => {},
        setUserShareStatus: (...args) => status.push(args),
        setUserShareProcesso: (...args) => status.push(args),
        machineIdAtual: () => 'pc:test',
        obterAuthHeaders: () => ({}),
        confirm: () => true,
        fetch: async (url, options) => {
            calls.push({ url, body: JSON.parse(options.body) });
            const response = responses.shift();
            assert(response, 'Unexpected extra request');
            return { ok: response.ok, json: async () => response.data };
        },
        atualizarCacheHistoricoFavoritosSincronizado: async () => notifications.push('cache'),
        carregarCompartilhamentoEntreUsuarios: async () => notifications.push('reload'),
        resumoResultadosSync: () => 'Cadastro recebido.',
    };
    vm.createContext(context);
    vm.runInContext(`${formatter}\n${preview}\n${run}`, context);
    await context.executarVinculoCompartilhamentoUsuario('link-test', 'pull');
    return { calls, status, notifications };
}
(async () => {
    const detail = { code: 'cadastro_photo_config_required', message: 'Configuração de fotos ausente.' };
    const beforePreview = await runScenario([{ ok: false, data: { detail } }]);
    assert.strictEqual(beforePreview.calls.length, 1);
    assert(beforePreview.calls[0].url.endsWith('/preview'));
    assert.strictEqual(beforePreview.status.at(-1)[2], 'Configuração de fotos ausente. (código: cadastro_photo_config_required)');
    assert.strictEqual(beforePreview.status.at(-1)[3], true);
    assert.deepStrictEqual(beforePreview.notifications, []);
    const afterPreview = await runScenario([{ ok: true, data: { success: true, operation_id: 'preview-test', totals: {} } },
        { ok: false, data: { detail } }]);
    assert.strictEqual(afterPreview.calls.length, 2);
    assert(afterPreview.calls[1].url.endsWith('/pull'));
    assert.strictEqual(afterPreview.calls[1].body.operation_id, 'preview-test');
    assert.strictEqual(afterPreview.status.at(-1)[2], 'Configuração de fotos ausente. (código: cadastro_photo_config_required)');
    assert.strictEqual(afterPreview.status.at(-1)[3], true);
    assert.deepStrictEqual(afterPreview.notifications, []);
    const success = await runScenario([{ ok: true, data: { success: true, operation_id: 'preview-test', totals: {} } },
        { ok: true, data: { success: true, results: [{ scope: 'cadastro', file_count: 1 }] } }]);
    assert.deepStrictEqual(success.notifications, ['cache', 'reload']);
    assert.strictEqual(success.status.at(-1)[2], 'Novidades recebidas. Cadastro recebido.');
    console.log('shared_sync_error_details: formatter, preview failure, pull failure, and success passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
