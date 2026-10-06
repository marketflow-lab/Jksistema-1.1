'use strict';
const assert = require('assert');
const path = require('path');
const fs = require('fs');
const vm = require('vm');
const readline = require('readline');
const { spawn } = require('child_process');
const { harness, terms } = require('./favoritos_checkpoint_client');

async function run() {
  const child = spawn(process.env.JK_TEST_PYTHON || 'python',
    ['-u', 'tests/helpers/favoritos_checkpoint_api_fixture.py'],
    { cwd: path.resolve(__dirname, '..'), env: { ...process.env, PYTHONUTF8: '1' }, stdio: ['pipe', 'pipe', 'pipe'] });
  const pending = new Map();
  let sequence = 0;
  let stderr = '';
  child.stderr.on('data', data => { stderr += data; });
  child.on('exit', code => {
    for (const entry of pending.values()) entry.reject(new Error('API fixture exited ' + code + ': ' + stderr));
    pending.clear();
  });
  const lines = readline.createInterface({ input: child.stdout });
  lines.on('line', line => {
    const response = JSON.parse(line);
    const entry = pending.get(response.id);
    if (entry) { pending.delete(response.id); entry.resolve(response); }
  });
  const server = {
    async fetch(url, options = {}) {
      const id = ++sequence;
      const promise = new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
      child.stdin.write(JSON.stringify({ id, url, method: options.method || 'GET', body: options.body ? JSON.parse(options.body) : undefined }) + '\n');
      const result = await promise;
      return { ok: result.status === 200, status: result.status, json: async () => result.data };
    }
  };
  function renderer() {
    const result = harness(server);
    vm.runInContext(fs.readFileSync(path.resolve(__dirname, '../static/favoritos/v2/execution/01-collection-evidence.js'), 'utf8'), result.ctx);
    result.ctx.obterImagemAnuncioFavoritos = item => item.imagem || '';
    result.ctx.anuncioFavoritosTemLink = item => !!item.id;
    result.ctx.anuncioFavoritosTemDadosAvant = item => !!item.vendasFonte;
    result.ctx.montarAuditoriaPesquisaFavoritos = () => ({ motivos_incompletos: {}, origens_dados: {}, amostras_incompletos: [] });
    const resumoReal = result.ctx.montarResumoPesquisaFavoritos;
    result.ctx.montarResumoPesquisaFavoritos = (...args) => resumoReal(...args);
    return result;
  }
  try {
    const first = renderer();
    await first.ctx.rankearAvulsoMercadoLivre({ termos: terms, sku: '1005-K' });
    assert.strictEqual(first.outcomes.length, 1, String(first.errors.at(-1)));
    assert.strictEqual(first.outcomes[0].coleta_status, 'parcial');
    assert.strictEqual(first.ctx.mlHistoricoFavoritosCache[0].grupos[0].checkpoint_ref.estado_coleta, 'parcial');
    const original = first.checkpoints.items[0];
    assert.strictEqual(original.sku, '1005-K');
    assert.strictEqual(first.ctx.mlHistoricoFavoritosCache[0].grupos[0].avulso, false);
    const get = await server.fetch('/api/favoritos/ranking/checkpoints/' + original.id + '?loja=Loja%20A');
    const pool = (await get.json()).checkpoint;
    assert.strictEqual(pool.candidatos.length, 3);
    assert.strictEqual(pool.candidatos[0].vendasFonte, 'avantpro');
    assert.strictEqual(pool.resumo_coleta.length, 3);
    assert.ok(Object.hasOwn(pool.resumo_coleta[0], 'tempo_navegacao_ms'));
    const originalHistory = JSON.stringify(first.ctx.mlHistoricoFavoritosCache[0]);
    const reopened = renderer();
    await reopened.checkpoints.list();
    await reopened.checkpoints.retomar(original.id, original.revision);
    assert.deepStrictEqual(reopened.calls.map(call => call.term.campo), [2]);
    assert.strictEqual(reopened.outcomes.length, 1, String(reopened.errors.at(-1)));
    assert.strictEqual(reopened.ctx.mlHistoricoFavoritosCache[0].grupos[0].checkpoint_ref.retomada_numero, 2);
    const updated = first.checkpoints.items[0];
    const oldAgain = await server.fetch('/api/favoritos/ranking/checkpoints/' + updated.id + '?loja=Loja%20A');
    assert.strictEqual((await oldAgain.json()).checkpoint.candidatos.length, 3);
    assert.strictEqual(JSON.stringify(first.ctx.mlHistoricoFavoritosCache[0]), originalHistory);
    const zero = renderer();
    zero.ctx.coletarAnunciosPrimeiraPaginaFavoritosSemFluxoAntigo = async () => {
      const result = []; Object.defineProperty(result, '__favoritosTempoEsgotado', { value: true }); return result;
    };
    await zero.ctx.rankearAvulsoMercadoLivre({ termos: [terms[1]] });
    assert.strictEqual(zero.outcomes[0].coleta_status, 'parcial');
    assert.strictEqual(zero.ctx.mlHistoricoFavoritosCache.length, 0);
    const excluded = renderer();
    excluded.ctx.calcularRankingAvulsoFavoritos = async (context, items, summaries) => ({
      ...context.info, anuncios: [], removidos_ia: [], resumo_coleta: summaries, duracao_execucao_ms: 300
    });
    await excluded.ctx.rankearAvulsoMercadoLivre({ termos: [terms[0]] });
    assert.strictEqual(excluded.outcomes.length, 1, String(excluded.errors.at(-1)));
    assert.strictEqual(excluded.outcomes[0].coleta_status, 'parcial');
    assert.strictEqual(excluded.ctx.mlHistoricoFavoritosCache.length, 0, 'candidatos excluídos não geram histórico falso');
    const excludedMeta = excluded.checkpoints.items[0];
    assert.strictEqual(excludedMeta.total_candidatos, 1, 'pool anterior aos filtros fica preservado');
    assert.strictEqual(excludedMeta.total_rankeados, 0);
    assert.strictEqual(excludedMeta.termos[0].estado, 'concluida');
    const excludedReopened = renderer();
    await excludedReopened.checkpoints.list();
    await excludedReopened.checkpoints.retomar(excludedMeta.id, excludedMeta.revision);
    assert.strictEqual(excludedReopened.calls.length, 0, 'retomar filtros não repete pesquisas concluídas');
    assert.strictEqual(excludedReopened.outcomes.length, 1, String(excludedReopened.errors.at(-1)));
    assert.strictEqual(excludedReopened.outcomes[0].anuncios[0].vendasFonte, 'avantpro');
    assert.strictEqual(excludedReopened.ctx.mlHistoricoFavoritosCache.length, 1);
    const lost = renderer();
    let loseOnce = true;
    lost.ctx.fetch = async (url, options) => {
      const response = await server.fetch(url, options);
      if (url.endsWith('/finalizar') && loseOnce) { loseOnce = false; throw new TypeError('ACK perdido após commit'); }
      return response;
    };
    await lost.ctx.rankearAvulsoMercadoLivre({ termos: [terms[0]] });
    assert.strictEqual(lost.outcomes.length, 1, String(lost.errors.at(-1)));
    assert.strictEqual(lost.ctx.mlHistoricoFavoritosCache.length, 1);
    assert.strictEqual(lost.calls.length, 1);
    console.log('Favoritos real JS/FastAPI/SQLite roundtrip checks passed');
  } finally {
    child.stdin.end();
    if (child.exitCode === null) {
      await new Promise(resolve => {
        const timer = setTimeout(() => { child.kill(); resolve(); }, 5000);
        child.once('exit', () => { clearTimeout(timer); resolve(); });
      });
    }
    lines.close();
  }
}
run().catch(error => { console.error(error); process.exitCode = 1; });
