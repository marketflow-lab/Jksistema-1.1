'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const canonical = fs.readFileSync(path.join(root, 'estoque.html'), 'utf8');
const served = fs.readFileSync(path.join(root, 'static', 'estoque.html'), 'utf8');

assert.strictEqual(served, canonical, 'os espelhos da tela de estoque divergiram');
assert(canonical.includes('id="abaAtual"'), 'a visão de estoque atual deve ser preservada');
assert(canonical.includes('id="abaHistorico"'), 'a visão de histórico deve continuar disponível');

for (const id of [
  'histDataInicio',
  'histDataFim',
  'histIntervalo',
  'histIncluirGeral',
  'histIncluirSku',
  'histSkuInput',
  'histSkuOpcoes',
  'btnHistoricoCarregar',
  'historicoStatus',
  'historicoResumo',
  'historicoTHead',
  'historicoTBody',
]) {
  assert(canonical.includes(`id="${id}"`), `controle do histórico ausente: ${id}`);
}

for (const intervalo of ['atualizacao', 'dia', 'semana', 'mes']) {
  assert(canonical.includes(`<option value="${intervalo}"`), `agrupamento ausente: ${intervalo}`);
}

assert(
  /id="histIncluirGeral"\s+checked/.test(canonical),
  'Total geral da loja deve iniciar marcado',
);
assert(
  /id="histIncluirSku"[^>]*>/.test(canonical) && !/id="histIncluirSku"[^>]*checked/.test(canonical),
  'SKU individual deve iniciar desmarcado',
);
assert(
  /id="histSkuInput"[^>]*\sdisabled(?:\s|>)/.test(canonical),
  'campo de SKU deve iniciar desabilitado',
);
assert(canonical.includes('list="histSkuOpcoes"'), 'campo de SKU deve usar sugestões pesquisáveis');
assert(canonical.includes("event.key !== 'Enter'"), 'Enter deve carregar o SKU pelo teclado');
assert(canonical.includes('aria-live="polite"'), 'status do histórico deve ser anunciado por leitor de tela');
assert(canonical.includes('role="tablist"'), 'abas devem expor semântica acessível');
assert(/id="tabAtual"[^>]*tabindex="0"[^>]*aria-selected="true"/.test(canonical), 'aba ativa inicial deve ser focável');
assert(/id="tabHistorico"[^>]*tabindex="-1"[^>]*aria-selected="false"/.test(canonical), 'aba inativa inicial deve sair do tab order');

for (const trecho of [
  "intervalo: histIntervalo.value",
  "incluir_geral: String(incluirGeral)",
  "incluir_sku: String(incluirSku)",
  "if (incluirSku) params.set('sku', sku)",
  "paramsGeral.delete('sku')",
  "paramsLegado.set('intervalo', 'dia')",
]) {
  assert(canonical.includes(trecho), `contrato de consulta ausente: ${trecho}`);
}

for (const trecho of [
  'series.geral',
  'series.sku',
  'series_por_sku',
  'saldo_retroativo',
  'event_ids',
  '__payloadGeralCompat',
  'atualizarOpcoesSkuHistorico',
  'historicoRequestSeq',
  'historicoAbortController',
  'consultaHistoricoObsoleta',
  'serieHistoricoTemSaldo',
  'navegarTabsPorTeclado',
]) {
  assert(canonical.includes(trecho), `compatibilidade de série ausente: ${trecho}`);
}

assert(canonical.includes('Total geral da loja'), 'tabela deve separar o total geral');
assert(canonical.includes('Saldo SKU'), 'tabela deve separar o saldo individual');
assert(canonical.includes('Loja (Full separado)'), 'interface deve explicitar que Full não entra no saldo da loja');
assert(canonical.includes("return numero === null ? '—'"), 'ponto ausente não pode ser apresentado como zero');
assert(!canonical.includes('chart.js'), 'tela de estoque não deve ganhar dependência gráfica pesada');

for (const trecho of [
  'new AbortController()',
  'historicoAbortController.abort()',
  'signal.aborted || consultaHistoricoObsoleta(',
  "histDataInicio.addEventListener('change', marcarFiltrosHistoricoAlterados)",
  "histDataFim.addEventListener('change', marcarFiltrosHistoricoAlterados)",
  "histSkuInput.addEventListener('input', marcarFiltrosHistoricoAlterados)",
  "tabAtual.addEventListener('keydown', navegarTabsPorTeclado)",
  "tabHistorico.addEventListener('keydown', navegarTabsPorTeclado)",
]) {
  assert(canonical.includes(trecho), `proteção de interação ausente: ${trecho}`);
}
assert(
  /if \(signal\.aborted \|\| consultaHistoricoObsoleta\([\s\S]+?\)\) return;\s+renderHistorico\(data\);/.test(canonical),
  'resposta obsoleta deve ser descartada antes da renderização',
);
assert(
  /function marcarFiltrosHistoricoAlterados\(\) \{\s+invalidarConsultaHistorico\(\);/.test(canonical),
  'mudança de filtros deve cancelar e invalidar a consulta ativa',
);
assert(
  /function atualizarSelecao\(\) \{\s+invalidarConsultaHistorico\(\);/.test(canonical),
  'mudança de loja deve cancelar e invalidar a consulta ativa',
);

const scripts = [...canonical.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)]
  .map(match => match[1])
  .filter(source => source.trim());
scripts.forEach((source, index) => {
  assert.doesNotThrow(
    () => new Function(source),
    `script inline ${index + 1} da tela de estoque possui erro de sintaxe`,
  );
});

const inlineScript = scripts[scripts.length - 1];
function carregarFuncao(nome) {
  const inicio = inlineScript.indexOf(`function ${nome}(`);
  assert(inicio >= 0, `função não encontrada para teste executável: ${nome}`);
  const abre = inlineScript.indexOf('{', inicio);
  let nivel = 0;
  let fim = -1;
  for (let indice = abre; indice < inlineScript.length; indice += 1) {
    if (inlineScript[indice] === '{') nivel += 1;
    if (inlineScript[indice] === '}') {
      nivel -= 1;
      if (nivel === 0) {
        fim = indice + 1;
        break;
      }
    }
  }
  assert(fim > abre, `não foi possível extrair a função: ${nome}`);
  const fonte = inlineScript.slice(inicio, fim);
  return new Function(`${fonte}; return ${nome};`)();
}

const consultaObsoleta = carregarFuncao('consultaHistoricoObsoleta');
assert.strictEqual(consultaObsoleta(7, 7, 'estado-a', 'estado-a'), false);
assert.strictEqual(consultaObsoleta(7, 8, 'estado-a', 'estado-a'), true, 'sequência antiga deve ser obsoleta');
assert.strictEqual(consultaObsoleta(7, 7, 'estado-a', 'estado-b'), true, 'filtros novos devem tornar resposta obsoleta');

const temSaldo = carregarFuncao('serieHistoricoTemSaldo');
assert.strictEqual(temSaldo([]), false);
assert.strictEqual(temSaldo([{ saldo: null }, { saldo: undefined }]), false, 'série somente nula deve ser vazia');
assert.strictEqual(temSaldo([{ saldo: 0 }]), true, 'saldo zero é histórico válido');
assert.strictEqual(temSaldo([{ saldo: null }, { saldo: 12 }]), true);

const indiceTab = carregarFuncao('indiceTabDestinoPorTecla');
assert.strictEqual(indiceTab('ArrowRight', 0, 2), 1);
assert.strictEqual(indiceTab('ArrowRight', 1, 2), 0, 'ArrowRight deve circular');
assert.strictEqual(indiceTab('ArrowLeft', 0, 2), 1, 'ArrowLeft deve circular');
assert.strictEqual(indiceTab('Home', 1, 2), 0);
assert.strictEqual(indiceTab('End', 0, 2), 1);

console.log('estoque historico interface contract: OK');
