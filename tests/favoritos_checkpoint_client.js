'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const root = path.resolve(__dirname, '..');
const client = fs.readFileSync(path.join(root, 'static/favoritos/v2/execution/07-ranking-checkpoints.js'), 'utf8');
const entrypoints = fs.readFileSync(path.join(root, 'static/favoritos/v2/execution/08-ranking-entrypoints.js'), 'utf8');
const copy = value => JSON.parse(JSON.stringify(value));

function makeServer() {
  const records = new Map();
  const histories = new Map();
  const requests = [];
  let sequence = 0;
  let active = 0;
  let maxActive = 0;
  let failClosing = false;
  let gate = null;
  const reply = (status, data) => ({ ok: status === 200, status, json: async () => copy(data) });
  const meta = record => {
    const result = copy(record);
    result.total_candidatos = record.candidatos.length;
    delete result.candidatos;
    delete result.attempt_token;
    return result;
  };
  async function fetch(url, options) {
    const body = options.body ? JSON.parse(options.body) : null;
    const action = url.replace('/api/favoritos/ranking/checkpoints', '');
    requests.push({ action, method: options.method || 'GET', body });
    active += 1;
    maxActive = Math.max(maxActive, active);
    try {
      if (gate) { const wait = gate; gate = null; await wait; }
      if (!action && body) {
        const id = 'checkpoint-' + (++sequence);
        const record = {
          id, version: 1, revision: 1, attempt_token: 'token-' + id, lease_expires_at_ms: 1120000,
          loja: body.loja, sku: body.sku, titulo: body.titulo, opcoes: body.opcoes,
          termos: body.termos.map(term => ({ ...term, estado: 'pendente', confirmed_keys: [], pending_keys: [] })),
          candidatos: [], resumo_coleta: [], estado_coleta: 'coletando', estado_fechamento: 'aberto',
          history_entry_id: null, parent_history_entry_id: null, retomada_numero: 1
        };
        records.set(id, record);
        return reply(200, { success: true, checkpoint: record });
      }
      if (action.startsWith('?')) {
        const loja = new URLSearchParams(action.slice(1)).get('loja');
        return reply(200, { success: true, checkpoints: [...records.values()].filter(item => item.loja === loja).map(meta) });
      }
      const parts = action.split('?')[0].split('/');
      const record = records.get(parts[1]);
      if (!record) return reply(404, { detail: 'Checkpoint ausente' });
      if (!body) return reply(200, { success: true, checkpoint: meta(record) });
      if (record.revision !== body.expected_revision) return reply(409, { detail: 'Conflito' });
      if (options.method === 'PUT') {
        assert.strictEqual(body.attempt_token, record.attempt_token);
        assert.deepStrictEqual(body.termos && body.termos.map(term => [term.campo, term.termo]),
          body.termos && record.termos.map(term => [term.campo, term.termo]));
        for (const field of ['candidatos', 'termos', 'resumo_coleta', 'estado_coleta', 'duracao_execucao_ms']) {
          if (body[field] !== undefined) record[field] = copy(body[field]);
        }
        record.revision += 1;
        record.lease_expires_at_ms = ['erro', 'cancelada'].includes(record.estado_coleta) ? 0 : 1120000;
        return reply(200, { success: true, checkpoint: record });
      }
      if (parts[2] === 'finalizar') {
        record.estado_fechamento = 'fechamento_pendente';
        record.revision += 1;
        record.lease_expires_at_ms = 0;
        if (!record.frozen) {
          record.history_entry_id = body.grupo_ranking.anuncios.length ? 'history-' + record.id : null;
          record.frozen = record.history_entry_id ? {
            id: record.history_entry_id, checkpoint_id: record.id, coleta_status: body.grupo_ranking.coleta_status,
            grupos: [copy(body.grupo_ranking)]
          } : null;
        }
        if (failClosing) {
          failClosing = false;
          return reply(503, { detail: { message: 'Fechamento salvo', checkpoint: meta(record) } });
        }
        record.estado_fechamento = 'confirmado';
        if (record.frozen) histories.set(record.frozen.id, copy(record.frozen));
        return reply(200, { success: true, confirmed: true, checkpoint: record, historico_entry: record.frozen });
      }
      if (parts[2] === 'retomar') {
        const child = copy(record);
        child.id = 'checkpoint-' + (++sequence);
        child.revision = 1;
        child.attempt_token = 'token-' + child.id;
        child.estado_fechamento = 'aberto';
        child.estado_coleta = 'coletando';
        child.lease_expires_at_ms = 1120000;
        child.parent_history_entry_id = record.history_entry_id;
        child.history_entry_id = null;
        child.retomada_numero += 1;
        delete child.frozen;
        record.retomado_por = child.id;
        record.revision += 1;
        records.set(child.id, child);
        return reply(200, { success: true, checkpoint: child });
      }
      throw new Error('Request inesperado');
    } finally { active -= 1; }
  }
  return { records, histories, requests, fetch, get maxActive() { return maxActive; },
    failClosing: () => { failClosing = true; }, defer: promise => { gate = promise; } };
}

function harness(server = makeServer()) {
  let now = 1000000;
  let owner = 'usuario-a';
  const intervals = new Map();
  const errors = [];
  const outcomes = [];
  const calls = [];
  const listings = {
    chaveAnuncioFavoritos: item => item.id || item.url || '',
    normalizarAnuncioFavoritosPesquisa: (item, sku, term) => ({
      ...item, pesquisas_origem: [term.termo], campos_origem: ['Pesquisa ' + term.campo]
    }),
    deduplicarAnunciosFavoritos: items => {
      const map = new Map();
      for (const item of items) {
        const prior = map.get(listings.chaveAnuncioFavoritos(item));
        if (!prior) map.set(listings.chaveAnuncioFavoritos(item), copy(item));
        else map.set(listings.chaveAnuncioFavoritos(item), {
          ...prior, ...Object.fromEntries(Object.entries(item).filter(([, value]) => value !== '' && value !== null)),
          pesquisas_origem: [...new Set([...(prior.pesquisas_origem || []), ...(item.pesquisas_origem || [])])],
          campos_origem: [...new Set([...(prior.campos_origem || []), ...(item.campos_origem || [])])]
        });
      }
      return [...map.values()];
    }
  };
  const control = { verificarCancelamentoFavoritos() {
    if (ctx.mlFavoritosCancelado) { const error = new Error('Cancelado'); error.canceladoFavoritos = true; throw error; }
  } };
  const ctx = {
    console, JSON, Promise, Map, Set, Array, Object, String, Number, Math, Error, AbortController, URLSearchParams,
    Date: class extends Date { static now() { return now; } },
    setTimeout, clearTimeout,
    setInterval: callback => { const id = intervals.size + 1; intervals.set(id, callback); return id; },
    clearInterval: id => intervals.delete(id),
    obterAuthHeaders: () => ({ 'X-Client-ID': 'cliente', Authorization: owner }),
    usernameHistoricoFavoritosAtual: () => owner,
    headersJsonAutenticado: () => ({ 'Content-Type': 'application/json' }),
    favoritosLojaSelecionadaParaApi: () => 'Loja A',
    fetch: server.fetch,
    crypto: { randomUUID: () => 'request-' + Math.random().toString(36).slice(2) },
    mlFavoritosEmExecucao: false, mlFavoritosCancelado: false,
    mlHistoricoFavoritosCache: [], mlHistoricoFavoritosServidorCarregado: false,
    lerHistoricoFavoritos: () => ctx.mlHistoricoFavoritosCache,
    salvarHistoricoFavoritosLocal: () => {},
    renderizarHistoricoFavoritos: () => {},
    ML_FAVORITOS_COLETA_ANUNCIOS_MAX: 80,
    montarResumoPesquisaFavoritos: (number, total, ads) => ({ pesquisa: number, total_visiveis: total, coletados: ads.length }),
    formatarResumoPesquisaFavoritos: () => 'Resumo',
    guardarResultadoRankingFavorito: group => { ctx.lastGroup = group; },
    FavoritosV2: {
      execution: { publicApi: {} }, checkpointUi: { refresh() {} },
      searchRanking: { publicApi: {
        listings, control, status: { mostrarBalaoFavoritosStatus: value => errors.push(value) },
        enrichment: { enriquecerAnunciosFavoritosRanking: async () => {} }
      } }
    }
  };
  ctx.window = ctx;
  vm.createContext(ctx);
  vm.runInContext(client, ctx);
  vm.runInContext(entrypoints, ctx);
  const checkpoints = ctx.FavoritosV2.execution.checkpoints;
  ctx.prepararRankingAvulsoFavoritos = options => {
    ctx.mlFavoritosEmExecucao = true;
    return { contexto: {
      opcoes: options, termos: options.termos, skuRanking: options.sku || 'AVULSO', tituloProcesso: 'Teste',
      info: { sku: options.sku || 'AVULSO', loja: options.loja || 'Loja A', titulo: 'Teste', termos: options.termos, avulso: options.avulso == null ? !options.sku || options.sku === 'AVULSO' : options.avulso }
    } };
  };
  ctx.enriquecerRankingAvulsoFavoritos = async (context, items) => items;
  ctx.calcularRankingAvulsoFavoritos = async (context, items, summaries) => ({
    ...context.info, anuncios: items.slice(0, 10), resumo_coleta: summaries, duracao_execucao_ms: 300
  });
  ctx.concluirRankingAvulsoFavoritos = group => outcomes.push(group);
  ctx.tratarErroRankingAvulsoFavoritos = error => errors.push(error);
  ctx.limparEstadoRankingAvulsoFavoritos = () => { ctx.mlFavoritosEmExecucao = false; ctx.mlFavoritosCancelado = false; };
  ctx.coletarAnunciosPrimeiraPaginaFavoritosSemFluxoAntigo = async (info, term, options) => {
    calls.push({ term, options });
    const items = options.anunciosSeed.concat([{ id: 'MLB' + term.campo, vendas: 0, vendasFonte: 'avantpro', is_full: '' }]);
    options.onSnapshot({ anuncios: items, confirmedKeys: ['mlb:MLB' + term.campo] });
    if (term.campo === 2) Object.defineProperty(items, '__favoritosTempoEsgotado', { value: true });
    return items;
  };
  ctx.FavoritosV2.execution.publicApi.rankearAvulsoMercadoLivre = ctx.rankearAvulsoMercadoLivre;
  return { ctx, checkpoints, server, errors, outcomes, calls, advance: ms => { now += ms; }, tick: () => { for (const cb of intervals.values()) cb(); },
    changeOwner: () => { owner = 'usuario-b'; } };
}

const terms = [{ campo: 1, termo: 'pesquisa pronta' }, { campo: 2, termo: 'pesquisa pendente' }, { campo: 3, termo: 'ultima pesquisa' }];
async function run() {
  const first = harness();
  await first.ctx.rankearAvulsoMercadoLivre({ termos: terms });
  assert.strictEqual(first.calls.length, 3, 'timeout controlado deve continuar na próxima pesquisa');
  assert.strictEqual(first.outcomes[0].coleta_status, 'parcial');
  const prior = copy(first.server.histories.values().next().value);
  const original = [...first.server.records.values()][0];
  assert.deepStrictEqual(original.termos.map(term => term.estado), ['concluida', 'parcial', 'concluida']);
  assert.strictEqual(original.candidatos[0].vendasFonte, 'avantpro');
  assert.strictEqual(original.candidatos[0].is_full, null);
  assert.strictEqual(first.ctx.mlHistoricoFavoritosCache.length, 1, 'cache visível deve receber confirmação canônica');
  const reopened = harness(first.server);
  await reopened.checkpoints.list();
  await reopened.checkpoints.retomar(original.id, original.revision);
  assert.deepStrictEqual(reopened.calls.map(call => call.term.campo), [2], 'reabrir só pesquisas incompletas');
  assert.strictEqual(reopened.calls[0].options.anunciosSeed[0].vendasFonte, 'avantpro');
  assert.strictEqual(reopened.server.histories.size, 2, 'cada retomada cria novo histórico');
  assert.deepStrictEqual(reopened.server.histories.get(prior.id), prior, 'snapshot anterior imutável');

  const zero = harness();
  zero.ctx.coletarAnunciosPrimeiraPaginaFavoritosSemFluxoAntigo = async () => {
    const result = [];
    Object.defineProperty(result, '__favoritosTempoEsgotado', { value: true });
    return result;
  };
  await zero.ctx.rankearAvulsoMercadoLivre({ termos: [terms[1]] });
  assert.strictEqual(zero.server.histories.size, 0, 'zero anúncios não pode criar ranking concluído');
  assert.strictEqual(zero.outcomes[0].coleta_status, 'parcial');

  for (const interruption of ['cancelamento', 'login', 'calculo']) {
    const interrupted = harness();
    if (interruption === 'calculo') interrupted.ctx.calcularRankingAvulsoFavoritos = async () => { throw new Error('Calculo'); };
    else interrupted.ctx.coletarAnunciosPrimeiraPaginaFavoritosSemFluxoAntigo = async (info, term, options) => {
      if (term.campo === 1) { const items = [{ id: 'MLB1', vendas: 3, vendasFonte: 'avantpro' }]; options.onSnapshot({ anuncios: items }); return items; }
      if (interruption === 'cancelamento') interrupted.ctx.mlFavoritosCancelado = true;
      const error = new Error(interruption);
      error.loginAvantProNecessario = interruption === 'login';
      throw error;
    };
    await interrupted.ctx.rankearAvulsoMercadoLivre({ termos: interruption === 'calculo' ? [terms[0], terms[2]] : terms.slice(0, 2) });
    const saved = [...interrupted.server.records.values()][0];
    assert.strictEqual(saved.estado_coleta, interruption === 'cancelamento' ? 'cancelada' : 'erro');
    assert.ok(saved.candidatos.length > 0, 'interrupção deve preservar coleta confirmada');
    assert.strictEqual(interrupted.server.histories.size, 0);
    if (interruption === 'calculo') {
      await interrupted.checkpoints.retomar(saved.id, saved.revision);
      assert.strictEqual(interrupted.calls.length, 2, 'falha no cálculo deve retomar só etapas restantes, sem repetir pesquisas concluídas');
    }
  }

  const serial = harness();
  const context = { opcoes: {}, skuRanking: 'AVULSO', info: { loja: 'Loja A', titulo: 'Serial' }, termos: [terms[0]] };
  const session = await serial.checkpoints.start(context);
  const eighty = Array.from({ length: 80 }, (_, index) => ({ id: 'MLB' + (index + 1000), vendasFonte: 'avantpro', vendas: index, __capturadoEm: 1000000, descricao: 'descricao ' + index }));
  serial.checkpoints.updateTerm(session, terms[0], { anuncios: eighty }, 'concluida');
  serial.advance(5000);
  serial.tick();
  await session.chain;
  assert.strictEqual(session.checkpoint.candidatos.length, 80, 'pool preservado antes dos filtros');
  const gate = Promise.withResolvers ? Promise.withResolvers() : (() => { let resolve; return { promise: new Promise(r => { resolve = r; }), resolve }; })();
  serial.server.defer(gate.promise);
  const write = serial.checkpoints.flush(session);
  const heartbeat = serial.checkpoints.flush(session, true);
  gate.resolve();
  await Promise.all([write, heartbeat]);
  assert.strictEqual(serial.server.maxActive, 1, 'gravações devem ser serializadas');
  const heartbeatRequest = serial.server.requests.at(-1).body;
  assert.ok(!('candidatos' in heartbeatRequest), 'heartbeat não pode sobrescrever snapshot');
  assert.strictEqual(session.checkpoint.candidatos[0].capturado_em_ms, 1000000);
  const group = serial.checkpoints.bindGroup({ anuncios: eighty.slice(0, 10), duracao_execucao_ms: 30 }, session);
  await serial.checkpoints.finalize(session, group);
  assert.strictEqual(serial.server.records.get(session.checkpoint.id).candidatos.length, 80);

  const interruptedClosing = harness();
  interruptedClosing.server.failClosing();
  await interruptedClosing.ctx.rankearAvulsoMercadoLivre({ termos: [terms[0]] });
  const frozen = [...interruptedClosing.server.records.values()][0];
  assert.strictEqual(frozen.estado_fechamento, 'fechamento_pendente');
  assert.strictEqual(interruptedClosing.checkpoints.items[0].revision, frozen.revision);
  await interruptedClosing.checkpoints.retomar(frozen.id, frozen.revision);
  assert.strictEqual(interruptedClosing.calls.length, 1, 'fechamento pendente recupera sem pesquisar novamente');
  assert.strictEqual(interruptedClosing.server.histories.size, 1);

  const lostAck = harness();
  let loseOnce = true;
  lostAck.ctx.fetch = async (url, options) => {
    const response = await lostAck.server.fetch(url, options);
    if (url.endsWith('/finalizar') && loseOnce) { loseOnce = false; throw new TypeError('Conexao perdida depois de gravar'); }
    return response;
  };
  await lostAck.ctx.rankearAvulsoMercadoLivre({ termos: [terms[0]] });
  assert.strictEqual(lostAck.outcomes.length, 1, 'ACK perdido deve consultar fechamento já confirmado');
  assert.strictEqual(lostAck.server.histories.size, 1, 'recuperação de ACK não duplica histórico');
  assert.strictEqual(lostAck.calls.length, 1);

  const stale = harness();
  let release;
  stale.server.defer(new Promise(resolve => { release = resolve; }));
  const opening = stale.checkpoints.start({ ...context, termos: [terms[0]] });
  stale.changeOwner();
  release();
  await assert.rejects(opening, /identidade mudou/);
  assert.strictEqual(stale.checkpoints.items.length, 0, 'resposta antiga não aparece na outra identidade');
  const timed = harness();
  timed.ctx.setTimeout = callback => setTimeout(callback, 1);
  timed.ctx.fetch = (_url, options) => new Promise((_resolve, reject) => {
    options.signal.addEventListener('abort', () => { const error = new Error('Prazo'); error.name = 'AbortError'; reject(error); });
  });
  await assert.rejects(timed.checkpoints.start(context), error => error.checkpointTimeout && !error.canceladoFavoritos && error.name !== 'AbortError');
  console.log('Favoritos checkpoint client and resume integration checks passed');
}
if (require.main === module) run().catch(error => { console.error(error); process.exitCode = 1; });
module.exports = { harness, terms };
