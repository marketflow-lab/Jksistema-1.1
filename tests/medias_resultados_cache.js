'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

function fixture(responses) {
  let now = 0, id = 0;
  const timers = new Map(), calls = [], policies = [];
  const context = { console, DOMException, AbortController, URLSearchParams,
    Date: class extends Date { static now() { return now; } },
    setTimeout(callback, delay) { const key = ++id; timers.set(key, { callback, at: now + delay }); return key; },
    clearTimeout(key) { timers.delete(key); },
    document: {}, JKAuthenticatedMedia: { atualizarPoliticaRaiz(_, value) { policies.push(value); } },
    JKMedias: { modules: {}, defineModule(name, dependencies, factory) { this.modules[name] = factory({ modules: this.modules }); } },
  };
  context.JKMedias.modules.api = { request: async (url, options) => {
    calls.push({ url, at: now, options });
    const data = responses[Math.min(calls.length - 1, responses.length - 1)];
    if (typeof data === 'function') return data(options);
    return { ok: data.status < 400, status: data.status, json: async () => data.body };
  } };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../static/medias_compras/resultados.js'), 'utf8'), context);
  return { api: context.JKMedias.modules.resultados, calls, timers, policies, async run(promise) {
    let done = false, result, error;
    promise.then(value => { done = true; result = value; }, reason => { done = true; error = reason; });
    for (let step = 0; step < 200 && !done; step++) {
      for (let micro = 0; micro < 20; micro++) await Promise.resolve();
      if (done) break;
      const next = [...timers.entries()].sort((a, b) => a[1].at - b[1].at)[0];
      if (!next) throw new Error('Promise pending without timer');
      timers.delete(next[0]); now = next[1].at; next[1].callback();
    }
    if (error) throw error;
    assert(done, 'polling must be bounded');
    assert.strictEqual(timers.size, 0, 'no timer leaks');
    return result;
  } };
}
const meta = (state, version = 'one') => ({ estado: state, versao: version, stale: state !== 'ready', atualizando: state !== 'ready', gerado_em: '2026-10-16T12:00:00Z' });
const waiting = { status: 202, body: { dados: null, _cache: meta('waiting') } };

(async () => {
  {
    const old = { itens: [{ sku: 'synthetic', compra_sugerida: 2 }], _cache: meta('refreshing') };
    const f = fixture([{ status: 200, body: old }, waiting, { status: 200, body: { itens: [{ sku: 'synthetic', compra_sugerida: 3 }], _cache: meta('ready', 'two') } }]);
    const received = [];
    const result = await f.run(f.api.acompanharResultado('/view?meses=6', { receber(data) { received.push(data); data.itens[0].compra_sugerida = 99; } }));
    assert.strictEqual(result.atual, true);
    assert.strictEqual(result.dados.itens[0].compra_sugerida, 3, 'working edits cannot mutate shared payload');
    assert.strictEqual(old.itens[0].compra_sugerida, 2);
    assert.strictEqual(received.length, 2, '202 must never be painted as an empty dataset');
    assert(f.calls.every(call => call.url === '/view?meses=6&cache_mode=stale'));
  }
  {
    const f = fixture([waiting]); const states = [];
    const result = await f.run(f.api.acompanharResultado('/list', { receber() { assert.fail('no complete result'); }, estado: (_, pending) => states.push(pending) }));
    assert.strictEqual(result.atual, false); assert.strictEqual(result.pendente, true);
    assert.strictEqual(states.at(-1), true);
    assert.deepStrictEqual(f.calls.map(call => call.at), [0, 1000, 2000, 3000, 4000, 5000, 7000, 9000, 11000, 16000, 21000, 26000, 30000]);
  }
  {
    const f = fixture([{ status: 200, body: { itens: [], _cache: { ...meta('error'), atualizando: false, erro: { code: 'conflict', message: 'temporary' } } } }]);
    const result = await f.run(f.api.acompanharResultado('/view'));
    assert.strictEqual(result.atual, false); assert.strictEqual(f.calls.length, 1);
  }
  {
    const f = fixture([{ status: 403, body: { detail: 'Acesso negado' } }]);
    await assert.rejects(f.run(f.api.acompanharResultado('/view')), /Acesso negado/);
  }
  {
    const f = fixture([{ status: 200, body: { itens: [{ sku: 'legacy' }] } }]);
    const result = await f.run(f.api.acompanharResultado('/view'));
    assert.strictEqual(result.atual, true, 'legacy servers remain compatible');
  }
  {
    const f = fixture([() => new Promise(() => {})]);
    const result = await f.run(f.api.acompanharResultado('/view'));
    assert.strictEqual(result.pendente, true, 'hung transport cannot freeze UI indefinitely');
    assert.strictEqual(f.calls[0].options.signal.aborted, true);
  }
  for (const changed of [false, true]) {
    const old = { itens: [{ sku: 'synthetic' }], _cache: { ...meta('refreshing'), autoridade: 'a', politica_fotos: 'p1' } };
    const next = { ...waiting, body: { dados: null, _cache: { ...meta('waiting'), autoridade: changed ? 'b' : 'a', politica_fotos: 'p2' } } };
    const f = fixture([{ status: 200, body: old }, next]); let unavailable = 0;
    const result = await f.run(f.api.acompanharResultado('/view', {
      receber() { assert.strictEqual(f.policies[0], 'p1', 'policy applied before rendering'); },
      indisponivel(value) { assert.strictEqual(value.autoridade, changed ? 'b' : 'a'); unavailable++; },
    }));
    assert(unavailable > 0);
    assert.strictEqual(result.pendente, true);
    assert.strictEqual(result.dados === null, changed, 'hard invalidation removes previously returned data');
    assert.strictEqual(f.policies.at(-1), 'p2');
  }
  {
    const old = { itens: [{ sku: 'synthetic' }], _cache: { ...meta('refreshing'), autoridade: 'a' } };
    const f = fixture([{ status: 200, body: old }, { status: 202, body: { dados: null, _cache: { ...meta('waiting'), autoridade: 'a', pendente_fontes: true } } },
      { status: 200, body: { ...old, _cache: { ...meta('ready'), autoridade: 'a' } } }]);
    let painted = 0;
    const result = await f.run(f.api.acompanharResultado('/view', { receber() { painted++; } }));
    assert.strictEqual(result.atual, true);
    assert.strictEqual(painted, 2, 'same version is repainted after strong invalidation');
  }
  console.log('medias result cache polling: 9 scenarios OK');
})().catch(error => { console.error(error); process.exitCode = 1; });
