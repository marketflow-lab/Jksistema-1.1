'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'static', 'perguntas_pos_venda', 'perguntas.js'), 'utf8');
const functionSource = source.slice(source.indexOf('async function salvarTreinamentoAtendimentoPergunta('), source.indexOf('async function salvarOrientacaoSkuPergunta('));

(async () => {
  const calls = [];
  const oldModels = Array.from({ length: 61 }, (_, index) => ({ sku: '001', pergunta: `Pergunta ${index}`, resposta: 'Texto', custom: index }));
  let conflict = false;
  const sandbox = {
    URLSearchParams,
    lojaOrigemItem: question => question.loja,
    skuRealPergunta: question => question.sku || '',
    lojasMercadoLivreConectadas: () => [{ nome: 'Loja A', store_id: 'store-a' }, { nome: 'Loja B', store_id: 'store-b' }],
    exemplosDoSkuTreinamento: (examples, sku) => examples.filter(item => item.sku === sku).map(item => ({ ...item })),
    obterAuthHeaders: () => ({}),
    erroRespostaTreinamento: (data, fallback) => data.detail?.message || fallback,
    lojaEscopoTreinamento: () => 'store-b',
    fetch: async (url, options) => {
      calls.push({ url, options });
      if (options.method === 'POST') return { ok: !conflict, json: async () => conflict ? { detail: { message: 'O Obsidian mudou.' } } : { store_id: 'store-a' } };
      return { ok: true, json: async () => ({ store_id: 'store-a', editorial: { revision: 'known-revision' },
        exemplos: { perguntas_anuncio: [{ sku: '', pergunta: 'Geral' }, ...oldModels, { sku: '002', pergunta: 'Outro SKU' }] } }) };
    },
  };
  vm.createContext(sandbox);
  vm.runInContext(functionSource, sandbox);
  const question = { loja: 'Loja A', store_id: 'store-a', sku: '001' };
  await sandbox.salvarTreinamentoAtendimentoPergunta(question, { exemplo: { pergunta: 'Nova', resposta: 'Resposta nova', sku: 'invalido' } });
  let payload = JSON.parse(calls.at(-1).options.body);
  assert.strictEqual(payload.edit_target, 'sku');
  assert.strictEqual(payload.store_id, 'store-a');
  assert.strictEqual(payload.expected_revision, 'known-revision');
  assert.strictEqual(payload.exemplos.length, 62, 'não truncar os modelos anteriores');
  assert(payload.exemplos.every(item => item.sku === '001'));
  assert(!('orientacoes' in payload));
  assert(!('notas_sku' in payload), 'salvar modelo deve preservar a nota existente');
  assert(calls[0].url.includes('store_id=store-a'));
  await sandbox.salvarTreinamentoAtendimentoPergunta(question, { notasSku: '  Nota com espaços  ' });
  payload = JSON.parse(calls.at(-1).options.body);
  assert.strictEqual(payload.notas_sku, '  Nota com espaços  ');
  assert(!('exemplos' in payload), 'salvar nota deve preservar todos os modelos');
  const before = calls.length;
  await assert.rejects(() => sandbox.salvarTreinamentoAtendimentoPergunta({ ...question, sku: '' }), /Identifique o SKU/);
  await assert.rejects(() => sandbox.salvarTreinamentoAtendimentoPergunta({ ...question, store_id: 'unrelated-store' }), /loja exata/);
  assert.strictEqual(calls.length, before, 'sem identidade não deve consultar ou gravar outra loja');
  conflict = true;
  await assert.rejects(() => sandbox.salvarTreinamentoAtendimentoPergunta(question, { exemplo: { pergunta: 'Nova' } }), /Obsidian mudou/);
  console.log('Salvar modelo da pergunta usa loja, SKU e revisão exatos: OK');
})().catch(error => { console.error(error); process.exitCode = 1; });
