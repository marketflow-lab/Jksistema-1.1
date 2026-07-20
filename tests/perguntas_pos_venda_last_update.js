'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'static', 'perguntas_pos_venda', 'perguntas.js'), 'utf8');
const runtime = fs.readFileSync(path.join(root, 'static', 'perguntas_pos_venda', 'runtime.js'), 'utf8');
const styles = fs.readFileSync(path.join(root, 'static', 'perguntas_pos_venda', 'styles.css'), 'utf8');
const canonicalHtml = fs.readFileSync(path.join(root, 'static', 'perguntas_pos_venda.html'), 'utf8');
const mirrorHtml = fs.readFileSync(path.join(root, 'perguntas_pos_venda.html'), 'utf8');

assert.strictEqual(mirrorHtml, canonicalHtml, 'o espelho HTML da tela deve permanecer alinhado');
assert(canonicalHtml.includes('id="perguntas-ultima-atualizacao"'), 'o indicador deve existir na toolbar');
assert(runtime.includes("document.getElementById('perguntas-ultima-atualizacao')"), 'o runtime deve ligar o indicador ao DOM');
assert(styles.includes('.perguntas-last-update'), 'o indicador deve ter estilo próprio na toolbar');

const helpersStart = source.indexOf('function formatarTempoDesdeAtualizacaoPerguntas');
const helpersEnd = source.indexOf('function renderizarResumo', helpersStart);
assert(helpersStart >= 0 && helpersEnd > helpersStart, 'os helpers do indicador devem existir');

let agora = 100000;
class FakeDate extends Date {
    static now() {
        return agora;
    }
}

const state = {
    ultimaAtualizacaoPerguntasEm: 0,
    ultimaAtualizacaoPerguntasTimer: null
};
const indicador = { textContent: '', dateTime: '', title: '' };
let intervalosCriados = 0;
let atualizarIntervalo = null;
const windowMock = {
    setInterval(callback) {
        intervalosCriados += 1;
        atualizarIntervalo = callback;
        return intervalosCriados;
    }
};

const helpers = new Function(
    'state',
    'perguntasUltimaAtualizacao',
    'window',
    'Date',
    `${source.slice(helpersStart, helpersEnd)}; return {
        formatarTempoDesdeAtualizacaoPerguntas,
        registrarUltimaAtualizacaoPerguntas
    };`
)(state, indicador, windowMock, FakeDate);

assert.strictEqual(helpers.formatarTempoDesdeAtualizacaoPerguntas(100000, 100000), 'Atualizado agora');
assert.strictEqual(helpers.formatarTempoDesdeAtualizacaoPerguntas(100000, 112000), 'Atualizado há 12 s');
assert.strictEqual(helpers.formatarTempoDesdeAtualizacaoPerguntas(100000, 161000), 'Atualizado há 1 min');
assert.strictEqual(helpers.formatarTempoDesdeAtualizacaoPerguntas(100000, 10900000), 'Atualizado há 3 h');
assert.strictEqual(helpers.formatarTempoDesdeAtualizacaoPerguntas(100000, 86500000), 'Atualizado há 1 dia');
assert.strictEqual(helpers.formatarTempoDesdeAtualizacaoPerguntas(100000, 172900000), 'Atualizado há 2 dias');

helpers.registrarUltimaAtualizacaoPerguntas();
assert.strictEqual(indicador.textContent, 'Atualizado agora');
assert.strictEqual(intervalosCriados, 1, 'a primeira atualização deve iniciar um único relógio');

agora = 112000;
atualizarIntervalo();
assert.strictEqual(indicador.textContent, 'Atualizado há 12 s');

agora = 161000;
helpers.registrarUltimaAtualizacaoPerguntas();
assert.strictEqual(indicador.textContent, 'Atualizado agora');
assert.strictEqual(intervalosCriados, 1, 'novas atualizações não devem duplicar o relógio');

const carregarStart = source.indexOf('async function carregarPerguntas(pagina');
const carregarSource = source.slice(carregarStart);
const catchStart = carregarSource.indexOf('} catch (error) {');
assert(carregarStart >= 0 && catchStart > 0, 'o fluxo de carregamento deve existir');
assert.strictEqual(
    (carregarSource.slice(0, catchStart).match(/registrarUltimaAtualizacaoPerguntas\(\)/g) || []).length,
    2,
    'os dois fluxos de sucesso devem registrar a atualização'
);
assert.strictEqual(
    (carregarSource.slice(catchStart).match(/registrarUltimaAtualizacaoPerguntas\(\)/g) || []).length,
    0,
    'o fluxo de erro não deve redefinir o horário'
);
assert(
    carregarSource.includes('if (Number(dataTodas.lojas_consultadas || 0) > 0) registrarUltimaAtualizacaoPerguntas();'),
    'falha total de todas as contas deve preservar o horário anterior'
);

console.log('OK: indicador de ultima atualizacao das perguntas validado.');
