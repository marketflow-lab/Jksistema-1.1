(function (global) {
  'use strict';
  const namespace = global.FavoritosV2 = global.FavoritosV2 || {};
  const execution = namespace.execution = namespace.execution || {};
  const prefix = '/api/favoritos/ranking/checkpoints';
  const clone = value => JSON.parse(JSON.stringify(value));
  const candidateFields = new Set(('id mlb url permalink link chave_canonica chaveCanonica link_normalizado linkFonte link_fonte descricao suspeito titulo title tituloFonte titulo_fonte vendedor seller_name seller_nickname official_store_name loja_vendedora vendedorFonte vendedor_fonte vendas vendasFonte vendas_fonte data_criacao dataCriacaoFonte data_criacao_fonte imagem thumbnail foto fotoFonte foto_fonte preco price preco_original original_price preco_promocional promotional_price fonte_preco precoFonte preco_fonte media_mensal ritmo_atual ritmo_vendas_mes media_vendas_mensal media_mensal_fonte ritmo_atual_fonte ritmo_vendas_mes_fonte meses_desde_criacao visitas posicao tipo_anuncio listing_type_id listing_type_name logistic_type shipping_mode is_full condicao moeda currency_id parcelamento_sem_juros origem_dados cacheAvant estado_qualidade capturado_em __capturadoEm capturado_em_ms pesquisas_origem campos_origem motivo_ia').split(' '));
  const api = { items: [], list, start, updateTerm, updatePool, termSeed, flush, finalize, stop, retomar, bindGroup };
  let listGeneration = 0;
  let listScope = '';
  execution.checkpoints = api;

  function ownerScope() {
    const headers = typeof obterAuthHeaders === 'function' ? obterAuthHeaders() : {};
    return JSON.stringify([typeof usernameHistoricoFavoritosAtual === 'function' ? usernameHistoricoFavoritosAtual() : '', headers['X-Client-ID'] || headers['X-Client-Id'] || headers['x-client-id'] || '', headers.Authorization || headers.authorization || '']);
  }

  function currentStore() {
    return typeof favoritosLojaSelecionadaParaApi === 'function' ? favoritosLojaSelecionadaParaApi() : '';
  }

  function refreshUi() {
    if (global.FavoritosV2.checkpointUi) global.FavoritosV2.checkpointUi.refresh();
  }

  async function request(path, options = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch(prefix + path, {
        ...options, headers: headersJsonAutenticado(), signal: controller.signal, cache: 'no-store'
      });
      const data = await response.json();
      if (!response.ok || data.success === false) {
        const detail = data.detail || data.erro;
        const error = new Error(typeof detail === 'string' ? detail : detail && detail.message || 'Nao foi possivel salvar ou retomar a coleta.');
        error.checkpointStatus = response.status;
        error.checkpointSaved = detail && detail.checkpoint || null;
        throw error;
      }
      return data;
    } catch (error) {
      if (error && error.name === 'AbortError') {
        const timeout = new Error('O servidor excedeu o prazo para salvar o progresso. A coleta confirmada permanece disponível no Histórico.');
        timeout.checkpointTimeout = true;
        throw timeout;
      }
      throw error;
    } finally { clearTimeout(timer); }
  }

  function requestId() {
    return global.crypto && typeof global.crypto.randomUUID === 'function'
      ? global.crypto.randomUUID() : Date.now().toString(36) + '-' + Math.random().toString(36).slice(2);
  }

  async function list() {
    const loja = currentStore();
    const scope = ownerScope() + ':' + loja;
    const generation = ++listGeneration;
    if (listScope !== scope) { api.items = []; listScope = scope; }
    if (!loja) { refreshUi(); return []; }
    const data = await request('?loja=' + encodeURIComponent(loja));
    if (generation !== listGeneration || scope !== ownerScope() + ':' + currentStore()) return api.items;
    api.items = data.checkpoints || [];
    refreshUi();
    return api.items;
  }

  function remember(checkpoint) {
    const storeKey = value => typeof skuNormalizarLoja === 'function' ? skuNormalizarLoja(value) : String(value || '').trim().toLowerCase();
    if (storeKey(checkpoint.loja) !== storeKey(currentStore())) return;
    const metadata = { ...checkpoint, total_candidatos: (checkpoint.candidatos || []).length };
    delete metadata.candidatos;
    delete metadata.attempt_token;
    api.items = api.items.filter(item => item.id !== checkpoint.id).concat(metadata);
    refreshUi();
  }

  function checkedSession(session) {
    const message = session.owner !== ownerScope()
      ? 'A identidade mudou durante a coleta. Reabra o Favoritos na conta original.'
      : session.closed ? 'Esta tentativa de coleta foi encerrada.' : '';
    if (message) { const error = new Error(message); error.checkpointFailure = true; throw error; }
  }

  async function start(contexto) {
    const owner = ownerScope();
    const supplied = contexto.opcoes.checkpointExecutar;
    const data = supplied ? { checkpoint: supplied } : await request('', {
      method: 'POST',
      body: JSON.stringify({
        request_id: requestId(), loja: contexto.info.loja, sku: contexto.skuRanking,
        titulo: contexto.info.titulo,
        termos: contexto.termos.map((termo, index) => ({ campo: Number(termo.campo) || index + 1, termo: termo.termo })),
        opcoes: { avulso: contexto.info.avulso !== false, usar_ia_ranking: contexto.opcoes.usarIaRanking === true, max_anuncios: 80, max_passadas: 2, lote_cliques: 6, tempo_limite_ms: 90000 }
      })
    });
    if (owner !== ownerScope()) throw new Error('A identidade mudou durante a abertura da coleta.');
    const checkpoint = data.checkpoint;
    const session = {
      checkpoint, draft: clone(checkpoint), owner, chain: Promise.resolve(),
      closed: false, dirty: false, saving: false, failure: null, progressAt: Date.now(),
      startedAt: Date.now(), lastHeartbeat: Date.now()
    };
    contexto.checkpointSession = session;
    session.timer = setInterval(() => {
      if (session.closed || session.saving) return;
      const due = session.dirty && Date.now() - session.progressAt >= 5000;
      if (due || Date.now() - session.lastHeartbeat >= 10000) flush(session, !due).catch(() => {});
    }, 1000);
    remember(checkpoint);
    return session;
  }

  function listingKey(item) {
    return execution ? global.FavoritosV2.searchRanking.publicApi.listings.chaveAnuncioFavoritos(item) : '';
  }

  function candidatesPayload(items) {
    return (items || []).slice(0, 240).map(item => {
      const payload = {};
      Object.entries(item || {}).forEach(([key, value]) => {
        if (!candidateFields.has(key) || value === undefined) return;
        if (key === '__capturadoEm') {
          if (Number.isFinite(Number(value))) payload.capturado_em_ms = Number(value);
          return;
        }
        if (['is_full', 'parcelamento_sem_juros'].includes(key) && typeof value !== 'boolean') { payload[key] = null; return; }
        if (Array.isArray(value)) payload[key] = value.slice(0, 3).map(v => String(v).slice(0, key === 'pesquisas_origem' ? 220 : 80));
        else if (value === null || typeof value === 'number' || typeof value === 'boolean') payload[key] = value;
        else if (typeof value === 'string') payload[key] = value.slice(0, key === 'descricao' ? 8000 : /^(url|permalink|link|link_normalizado|imagem|thumbnail|foto)$/.test(key) ? 800 : /[Ff]onte/.test(key) ? 80 : /^(vendedor|seller_name|seller_nickname|official_store_name|loja_vendedora|chave_canonica|chaveCanonica)$/.test(key) ? 160 : 500);
      });
      return payload;
    }).filter(item => listingKey(item));
  }

  function termSeed(session, pesquisa) {
    const term = session.draft.termos.find(item => Number(item.campo) === Number(pesquisa.campo) && item.termo === pesquisa.termo);
    const anuncios = session.draft.candidatos.filter(item =>
      (item.pesquisas_origem || []).includes(pesquisa.termo) || (item.campos_origem || []).map(String).some(campo => campo === String(pesquisa.campo) || campo === 'Pesquisa ' + pesquisa.campo));
    const seed = clone(anuncios).map(item => item.capturado_em_ms == null ? item : { ...item, __capturadoEm: item.capturado_em_ms });
    return { term, anuncios: seed, confirmedKeys: term && term.confirmed_keys || [] };
  }

  function updateTerm(session, pesquisa, snapshot, estado, resumo) {
    checkedSession(session);
    const term = session.draft.termos.find(item => Number(item.campo) === Number(pesquisa.campo) && item.termo === pesquisa.termo);
    if (!term) throw new Error('Pesquisa nao pertence ao progresso salvo.');
    const normalizados = (snapshot.anuncios || []).map(item =>
      global.FavoritosV2.searchRanking.publicApi.listings.normalizarAnuncioFavoritosPesquisa(item, session.draft.sku, pesquisa));
    const listings = global.FavoritosV2.searchRanking.publicApi.listings;
    const merged = listings.deduplicarAnunciosFavoritos(session.draft.candidatos.concat(normalizados));
    const counts = new Map();
    session.draft.candidatos = candidatesPayload(merged.filter(item => {
      const origins = item.pesquisas_origem || [pesquisa.termo];
      const kept = origins.filter(termo => (counts.get(termo) || 0) < 80);
      if (!kept.length) return false;
      kept.forEach(termo => counts.set(termo, (counts.get(termo) || 0) + 1));
      item.pesquisas_origem = kept;
      item.campos_origem = session.draft.termos.filter(termo => kept.includes(termo.termo)).map(termo => 'Pesquisa ' + termo.campo);
      return true;
    }));
    term.confirmed_keys = [...new Set((snapshot.confirmedKeys || term.confirmed_keys || []).filter(key => String(key).length <= 160))].slice(0, 80);
    term.estado = estado || 'parcial';
    if (resumo) {
      session.draft.resumo_coleta = session.draft.resumo_coleta.filter(item => Number(item.campo) !== Number(pesquisa.campo)).concat(clone(resumo));
    }
    session.draft.estado_coleta = session.draft.termos.every(item => item.estado === 'concluida') ? 'concluida' : 'coletando';
    session.dirty = true;
  }

  function updatePool(session, items) {
    checkedSession(session);
    const listings = global.FavoritosV2.searchRanking.publicApi.listings;
    session.draft.candidatos = candidatesPayload(listings.deduplicarAnunciosFavoritos(session.draft.candidatos.concat(items || [])));
    session.dirty = true;
  }

  function flush(session, heartbeat = false, estado = null) {
    const action = async () => {
      checkedSession(session);
      session.saving = true;
      const payload = {
        request_id: requestId(), loja: session.draft.loja,
        expected_revision: session.checkpoint.revision, attempt_token: session.checkpoint.attempt_token
      };
      if (!heartbeat) {
        Object.assign(payload, {
          estado_coleta: estado || session.draft.estado_coleta,
          termos: clone(session.draft.termos), candidatos: candidatesPayload(session.draft.candidatos),
          resumo_coleta: clone(session.draft.resumo_coleta || []),
          duracao_execucao_ms: Date.now() - session.startedAt
        });
        session.dirty = false;
      }
      try {
        const data = await request('/' + encodeURIComponent(session.checkpoint.id), { method: 'PUT', body: JSON.stringify(payload) });
        checkedSession(session);
        session.checkpoint = data.checkpoint;
        session.lastHeartbeat = Date.now();
        if (!heartbeat) session.progressAt = Date.now();
        session.failure = null;
        remember(data.checkpoint);
        return data;
      } catch (error) {
        session.failure = error;
        if (!heartbeat) session.dirty = true;
        if (error.checkpointStatus === 409 || error.checkpointStatus === 401 || error.checkpointStatus === 403) session.closed = true;
        throw error;
      } finally { session.saving = false; }
    };
    session.chain = session.chain.catch(() => {}).then(action);
    return session.chain;
  }

  function bindGroup(grupo, session) {
    grupo.checkpoint_id = session.checkpoint.id;
    grupo.coleta_status = session.draft.termos.every(item => item.estado === 'concluida') ? 'concluida' : 'parcial';
    grupo.parent_history_entry_id = session.checkpoint.parent_history_entry_id || '';
    grupo.retomada_numero = session.checkpoint.retomada_numero || 1;
    return grupo;
  }

  function acceptHistory(entry) {
    if (!entry) return;
    const current = lerHistoricoFavoritos().filter(item => item.id !== entry.id);
    const history = typeof normalizarHistoricoFavoritosFrontend === 'function'
      ? normalizarHistoricoFavoritosFrontend([entry].concat(current)) : [entry].concat(current);
    if (typeof mlHistoricoFavoritosCache !== 'undefined') mlHistoricoFavoritosCache = history;
    if (typeof mlHistoricoFavoritosServidorCarregado !== 'undefined') mlHistoricoFavoritosServidorCarregado = true;
    salvarHistoricoFavoritosLocal(history);
    if (typeof renderizarHistoricoFavoritos === 'function') renderizarHistoricoFavoritos();
  }

  async function confirmClosing(session, payload) {
    const path = '/' + encodeURIComponent(session.checkpoint.id);
    try { return await request(path + '/finalizar', { method: 'POST', body: JSON.stringify(payload) }); }
    catch (error) {
      if (error.checkpointSaved || error.checkpointStatus) throw error;
      checkedSession(session);
      const saved = await request(path + '?loja=' + encodeURIComponent(session.draft.loja));
      checkedSession(session);
      const checkpoint = saved.checkpoint;
      if (checkpoint.estado_fechamento === 'confirmado') {
        return request(path + '/finalizar', {
          method: 'POST', body: JSON.stringify({
            request_id: requestId(), loja: checkpoint.loja,
            expected_revision: checkpoint.revision, recuperar_fechamento: true
          })
        });
      }
      error.checkpointSaved = checkpoint;
      throw error;
    }
  }

  async function finalize(session, grupo) {
    clearInterval(session.timer);
    await session.chain.catch(() => {});
    await flush(session, false, grupo.coleta_status);
    checkedSession(session);
    const id = requestId();
    const payload = {
      request_id: id, loja: session.draft.loja,
      expected_revision: session.checkpoint.revision, attempt_token: session.checkpoint.attempt_token,
      grupo_ranking: clone(grupo)
    };
    let data;
    try {
      data = await confirmClosing(session, payload);
    } catch (error) {
      if (session.owner === ownerScope() && error.checkpointSaved) {
        session.checkpoint = { ...session.checkpoint, ...error.checkpointSaved };
        remember({ ...session.checkpoint, candidatos: session.draft.candidatos });
        session.closed = session.checkpoint.estado_fechamento !== 'aberto';
      }
      throw error;
    }
    checkedSession(session);
    session.closed = true;
    session.checkpoint = data.checkpoint;
    grupo.coleta_status = data.checkpoint.estado_coleta;
    acceptHistory(data.historico_entry);
    remember(data.checkpoint);
    return { ...data, success: data.confirmed === true || data.success === true, duracao_execucao_ms: grupo.duracao_execucao_ms };
  }

  async function stop(session, estado) {
    if (!session) return;
    clearInterval(session.timer);
    try { await session.chain.catch(() => {}); if (!session.closed) await flush(session, false, estado || 'parcial'); }
    finally { session.closed = true; }
  }

  async function retomar(id, revision) {
    if (typeof mlFavoritosEmExecucao !== 'undefined' && mlFavoritosEmExecucao) return;
    const owner = ownerScope();
    let item = api.items.find(value => value.id === id);
    try {
      if (!item) throw new Error('Recarregue o Historico para consultar a coleta salva.');
      if (item.estado_fechamento === 'fechamento_pendente') {
        const recovery = await request('/' + encodeURIComponent(id) + '/finalizar', {
          method: 'POST', body: JSON.stringify({
            request_id: requestId(), loja: item.loja, expected_revision: revision, recuperar_fechamento: true
          })
        });
        if (owner !== ownerScope()) throw new Error('A identidade mudou durante a retomada.');
        acceptHistory(recovery.historico_entry);
        remember(recovery.checkpoint);
        item = recovery.checkpoint;
        revision = item.revision;
        if (item.estado_coleta === 'concluida') return;
      }
      const data = await request('/' + encodeURIComponent(id) + '/retomar', {
        method: 'POST', body: JSON.stringify({ request_id: requestId(), expected_revision: revision, loja: item.loja })
      });
      if (owner !== ownerScope()) throw new Error('A identidade mudou durante a retomada.');
      const checkpoint = data.checkpoint;
      remember({ ...item, retomado_por: checkpoint.id });
      remember(checkpoint);
      await execution.publicApi.rankearAvulsoMercadoLivre({
        termos: checkpoint.termos, sku: checkpoint.sku, loja: checkpoint.loja, titulo: checkpoint.titulo,
        avulso: checkpoint.opcoes.avulso == null ? String(checkpoint.sku).toUpperCase() === 'AVULSO' : checkpoint.opcoes.avulso,
        usarIaRanking: checkpoint.opcoes.usar_ia_ranking, checkpointExecutar: checkpoint,
        tituloProcesso: 'Retomando coleta'
      });
    } catch (error) {
      if (owner === ownerScope()) {
        global.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(error.message, { erro: true, larga: true });
        await list().catch(() => {});
      }
    }
  }
})(window);