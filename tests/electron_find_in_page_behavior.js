const assert = require('assert');
const path = require('path');
const { spawnSync } = require('child_process');

const repoRoot = path.resolve(__dirname, '..');
const electronCli = path.join(repoRoot, 'node_modules', 'electron', 'cli.js');
const probePath = path.join(__dirname, 'helpers', 'electron_find_in_page_behavior_probe.js');
const env = { ...process.env };
delete env.ELECTRON_RUN_AS_NODE;

const result = spawnSync(process.execPath, [electronCli, probePath], {
    cwd: repoRoot,
    env,
    encoding: 'utf8',
    timeout: 30000,
    windowsHide: true
});

assert.strictEqual(result.status, 0, `o Electron deve executar a prova de findInPage\n${result.stderr || result.stdout}`);
const lines = String(result.stdout || '').trim().split(/\r?\n/).filter(Boolean);
assert(lines.length > 0, 'a prova deve retornar o resultado final');
const payload = JSON.parse(lines[lines.length - 1]);
assert.strictEqual(payload.initial.matches, 2, 'a pesquisa deve contar o shell e o iframe visivel, ignorando o iframe oculto');
assert.strictEqual(payload.initial.activeMatchOrdinal, 1, 'a primeira ocorrencia deve ser selecionada');
assert.strictEqual(payload.next.activeMatchOrdinal, 2, 'a navegacao para frente deve selecionar a segunda ocorrencia');
assert.strictEqual(payload.previous.activeMatchOrdinal, 1, 'a navegacao para tras deve retornar a primeira ocorrencia');
console.log('Electron findInPage visible-frame behavior checks passed');
