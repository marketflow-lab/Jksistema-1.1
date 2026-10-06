"use strict";

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

class Element {
    constructor(tag = 'div') {
        this.tagName = tag.toUpperCase();
        this.children = [];
        this.attrs = {};
        this.events = {};
        this.className = '';
        this._text = '';
        this.disabled = false;
        this.hidden = false;
        this.classList = {
            add: name => { this.className = `${this.className} ${name}`.trim(); },
            remove: name => { this.className = this.className.split(/\s+/).filter(item => item !== name).join(' '); },
            contains: name => this.className.split(/\s+/).includes(name),
            toggle: (name, force) => force ? this.classList.add(name) : this.classList.remove(name)
        };
    }
    set textContent(value) { this._text = String(value); this.children = []; }
    get textContent() { return this._text + this.children.map(child => child.textContent).join('\n'); }
    set innerHTML(value) {
        assert.ok(value === '' || value.startsWith('\n                    <thead>'), 'UI interpolou HTML em vez de texto');
        this._text = ''; this.children = [];
        if (value) this.appendChild(new Element('tbody'));
    }
    appendChild(child) { this.children.push(child); child.parentNode = this; return child; }
    setAttribute(name, value) { this.attrs[name] = String(value); }
    addEventListener(name, listener) { this.events[name] = listener; }
    querySelector(selector) { return this.all().find(item => selector === 'tbody' ? item.tagName === 'TBODY' : false) || null; }
    all() { return this.children.flatMap(child => [child, ...child.all()]); }
    buttons() { return this.all().filter(child => child.tagName === 'BUTTON'); }
}

function meta(overrides = {}) {
    return {
        id: 'checkpoint-1', revision: 4, loja: 'Loja original', sku: 'AVULSO', titulo: 'Produto exemplo',
        estado_coleta: 'parcial', estado_fechamento: 'confirmado', total_candidatos: 2,
        history_entry_id: 'history-original', lease_expires_at_ms: 0,
        termos: [{ campo: 1, termo: 'termo completo', estado: 'concluida' }, { campo: 3, termo: 'termo incompleto', estado: 'parcial' }],
        ...overrides
    };
}

function harness(items = [meta()]) {
    const resumeCalls = [];
    const timers = [];
    const context = {
        window: { FavoritosV2: {
            execution: { checkpoints: { items, retomar: async (id, revision) => { resumeCalls.push({ id, revision }); } }, publicApi: { atualizarTabelasFavoritosEditaveis: () => {} } },
            promotionEffectuation: { publicApi: { options: { resolverOpcoesPromocaoGrupoFavoritos: () => null } } }
        } },
        document: { createElement: tag => new Element(tag), querySelector: () => null, getElementById: () => null },
        Date, Number, String, Math, Array, Set, Promise,
        setTimeout: (callback, duration) => { timers.push({ callback, duration }); return timers.length; }, clearTimeout: () => {},
        mlFavoritosEmExecucao: false,
        favMlAnunciosRecarregarBtnEl: null,
        mlSkuLojaSelecionada: 'Loja original', skuLojaSelecionada: '',
        favMlSkuSelecionado: 'AVULSO', favMlHistoricoExecucaoSelecionadaId: '__atual__',
        FAV_ML_RANKING_ATUAL_ID: '__atual__',
        favOutrosAnunciosBodyEl: new Element('tbody'), favRankingDataEl: new Element(), favOutrosAnunciosEmptyEl: new Element(),
        favRankingIncluirAnuncioBtnEl: null,
        histMlSkuSelecionado: '', histMlHistoricoExecucaoSelecionadaId: '',
        mlHistoricoFavoritosListEl: new Element(), mlHistoricoFavoritosEmptyEl: new Element(), mlHistoricoFavoritosStatusEl: new Element(),
        ML_FAVORITOS_HISTORICO_MAX: 20, ML_FAVORITOS_RANKING_ANUNCIOS_MAX: 10, ML_FAVORITOS_HISTORICO_ANUNCIOS_MAX: 10,
        skuChaveSku: value => String(value || '').trim().toLowerCase(),
        nomeUsuarioHistoricoFavoritosAtual: () => 'Usuário teste', usernameHistoricoFavoritosAtual: () => 'usuario-teste',
        obterUsuarioHistoricoFavoritos: () => '',
        favoritosLojaSelecionadaParaApi: () => 'Loja original', favoritosEhTodasLojas: () => false,
        filtrarAnunciosIgnoradosRanking: lista => Array.isArray(lista) ? lista : [],
        formatarDataHistoricoFavoritos: () => '06/10/2026', sufixoUsuarioHistoricoFavoritos: () => '',
        formatarTermosPesquisaFavoritos: () => 'termos salvos', normalizarTermosPesquisaFavoritos: lista => lista || [],
        criarBlocoResumoColetaFavoritos: () => null,
        lerHistoricoFavoritos: () => [], normalizarListaAnunciosHistoricoFavoritosFrontend: lista => lista || []
    };
    vm.createContext(context);
    for (const file of ['05-resultados-historico.js', '06-ranking-manual-historico-ui.js', '08-render-avant-mercadolivre.js']) {
        vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'static/favoritos/tabelas-layout', file), 'utf8'), context, { filename: file });
    }
    // External data helpers are stubs; checkpoint rendering and the complete history/main renderers are real.
    context.lerHistoricoFavoritos = () => [];
    context.normalizarListaAnunciosHistoricoFavoritosFrontend = lista => lista || [];
    context.sufixoUsuarioHistoricoFavoritos = () => '';
    context.criarTabelaHistoricoFavoritos = () => new Element('table');
    return { context, ui: context.window.FavoritosV2.checkpointUi, resumeCalls, timers };
}

async function run() {
    {
        const { ui, resumeCalls } = harness();
        const container = new Element();
        ui.renderGroup(container, { checkpoint_id: 'checkpoint-1', coleta_status: 'parcial', anuncios: [{ id: 'a' }, { id: 'b' }] });
        assert.match(container.textContent, /Ranking parcial/);
        assert.match(container.textContent, /2 anúncio\(s\) rankeado\(s\)/);
        assert.match(container.textContent, /Pesquisa 3: termo incompleto/);
        assert.doesNotMatch(container.textContent, /Pesquisa 1: termo completo/);
        const resume = container.buttons()[0];
        const event = { preventDefault() {}, stopPropagation() {} };
        const first = resume.events.click(event);
        await resume.events.click(event);
        await first;
        assert.deepStrictEqual(resumeCalls, [{ id: 'checkpoint-1', revision: 4 }], 'clique duplo não inicia duas retomadas');
        let stopped = false;
        resume.events.keydown({ stopPropagation() { stopped = true; } });
        assert.strictEqual(stopped, true, 'Enter do botão não deve abrir o cartão histórico pai');
    }
    {
        const malicious = '<img src=x onerror="globalThis.pwned=true">';
        const { ui, context } = harness([meta({ titulo: malicious, total_candidatos: 0, termos: [{ campo: 2, termo: malicious, estado: 'pendente' }] })]);
        const container = new Element();
        assert.strictEqual(ui.renderPending(container, []), 1);
        assert.match(container.textContent, /Coleta parcial sem anúncios/);
        assert.ok(container.textContent.includes(malicious), 'título e termo devem ser texto literal');
        assert.strictEqual(container.all().some(item => item.tagName === 'IMG'), false);
        assert.strictEqual(context.pwned, undefined);
        const history = [{ id: 'history-original', grupos: [{ checkpoint_id: 'checkpoint-1', sku: 'AVULSO' }] }];
        assert.strictEqual(ui.renderPending(new Element(), history), 0, 'checkpoint com snapshot visível não deve duplicar cartão');
        assert.strictEqual(ui.renderPending(new Element(), [], 'outro-sku'), 0, 'contexto de SKU deve ser respeitado');
    }
    {
        const { ui, context, timers } = harness([meta({ lease_expires_at_ms: Date.now() + 120000 })]);
        const container = new Element();
        ui.renderGroup(container, { checkpoint_id: 'checkpoint-1', coleta_status: 'parcial', anuncios: [] });
        assert.strictEqual(container.buttons()[0].disabled, true);
        assert.strictEqual(container.buttons()[0].textContent, 'Coleta em andamento');
        assert.ok(timers.some(timer => timer.duration > 0 && timer.duration <= 120025), 'lease precisa atualizar o botão ao expirar sem reabrir a aba');
        context.window.FavoritosV2.execution.checkpoints.items[0].lease_expires_at_ms = Date.now() - 1;
        const expired = new Element();
        ui.renderGroup(expired, { checkpoint_id: 'checkpoint-1', coleta_status: 'parcial', anuncios: [] });
        assert.strictEqual(expired.buttons()[0].disabled, false, 'reserva abandonada deve permitir retomar após expirar');
        context.mlFavoritosEmExecucao = true;
        const active = new Element();
        ui.renderGroup(active, { checkpoint_id: 'checkpoint-1', coleta_status: 'parcial', anuncios: [] });
        assert.strictEqual(active.buttons()[0].disabled, true);
    }
    {
        const { ui } = harness([meta({ retomado_por: 'checkpoint-child' })]);
        const parent = new Element();
        ui.renderGroup(parent, { checkpoint_id: 'checkpoint-1', coleta_status: 'parcial', anuncios: [{ id: 'a' }] });
        assert.match(parent.textContent, /Ranking parcial/);
        assert.match(parent.textContent, /nova execução/);
        assert.strictEqual(parent.buttons().length, 0, 'snapshot original não retoma após criação do filho');
        assert.strictEqual(ui.renderPending(new Element(), []), 0);
        const legacy = new Element();
        assert.strictEqual(ui.renderGroup(legacy, { sku: 'AVULSO', anuncios: [] }), null, 'legado mantém somente Refazer');
    }
    {
        const { ui } = harness([meta({ estado_coleta: 'concluida', estado_fechamento: 'confirmado', termos: [{ campo: 1, termo: 'fim', estado: 'concluida' }] })]);
        const complete = new Element();
        assert.strictEqual(ui.renderGroup(complete, { checkpoint_id: 'checkpoint-1', coleta_status: 'concluida', anuncios: [{ id: 'a' }] }), null);
        assert.strictEqual(ui.renderPending(new Element(), []), 0);
    }
    {
        const { ui, context } = harness([meta({ estado_coleta: 'concluida', estado_fechamento: 'fechamento_pendente', termos: [{ campo: 1, termo: 'fim', estado: 'concluida' }] })]);
        context.window.FavoritosV2.execution.checkpoints.retomar = async () => { throw new Error('Conflito de revisão'); };
        const container = new Element();
        ui.renderGroup(container, { checkpoint_id: 'checkpoint-1', coleta_status: 'concluida', anuncios: [{ id: 'a' }] });
        assert.match(container.textContent, /Finalização pendente/);
        await container.buttons()[0].events.click({ preventDefault() {}, stopPropagation() {} });
        assert.match(container.textContent, /Conflito de revisão/);
        assert.strictEqual(container.buttons()[0].disabled, false);
    }
    {
        const { context } = harness();
        const group = { sku: 'AVULSO', titulo: 'Zero confirmado', termos: ['t'], anuncios: [], removidos_ia: [],
            checkpoint_id: 'checkpoint-1', coleta_status: 'parcial', parent_history_entry_id: 'original', historico_origem_id: 'original', retomada_numero: 1 };
        const history = context.montarEntradaHistoricoFavoritos([group]);
        assert.strictEqual(history.grupos.length, 1, 'snapshot parcial vazio deve permanecer no histórico');
        for (const field of ['checkpoint_id', 'coleta_status', 'parent_history_entry_id', 'historico_origem_id', 'retomada_numero']) {
            assert.strictEqual(history.grupos[0][field], group[field], `metadado ${field} precisa sobreviver ao normalizador`);
        }
        context.obterGrupoRankingFavoritosSku = () => ({ grupo: group, entrada: history });
        context.renderizarFavoritosOutrosAnuncios('AVULSO');
        assert.match(context.favRankingDataEl.textContent, /Coleta parcial sem anúncios/);
        assert.strictEqual(context.favRankingDataEl.buttons().length, 1, 'painel Favoritos mantém ação mesmo com ranking vazio');
        context.lerHistoricoFavoritos = () => [history];
        context.histMlSkuSelecionado = 'AVULSO';
        context.renderizarHistoricoFavoritos();
        assert.match(context.mlHistoricoFavoritosListEl.textContent, /Coleta parcial sem anúncios/);
        assert.strictEqual(context.mlHistoricoFavoritosEmptyEl.classList.contains('hidden'), true);
        assert.ok(context.mlHistoricoFavoritosListEl.buttons().some(button => button.textContent === 'Refazer'), 'histórico preserva ação existente');
    }
    {
        const canonicalId = '00000000-0000-4000-8000-000000000001';
        const checkpointRef = { id: canonicalId, version: 1, estado_coleta: 'parcial',
            root_id: canonicalId, parent_id: '', parent_history_entry_id: '', retomada_numero: 1 };
        const backendGroup = { sku: 'AVULSO', titulo: 'Resposta canônica do backend',
            termos: [{ campo: 1, termo: 'completo' }, { campo: 2, termo: 'incompleto' }],
            anuncios: [], removidos_ia: [], checkpoint_ref: checkpointRef,
            resumo_coleta: [{ campo: '1', termo: 'completo', tempo_esgotado: false }, { campo: '2', termo: 'incompleto', tempo_esgotado: true }] };
        const backendHistory = { id: 'historia-canonica', data_iso: '2026-10-06T10:00:00Z', loja: 'Loja original', grupos: [backendGroup] };
        const { context, ui } = harness([]);
        const display = new Element();
        ui.renderGroup(display, { ...backendGroup, anuncios: Array.from({ length: 18 }, (_,i) => ({ id: `demo-${i}` })) }, backendHistory);
        assert.match(display.textContent, /Ranking parcial/);
        assert.match(display.textContent, /18 anúncio\(s\)/);
        assert.match(display.textContent, /Pesquisa 2: incompleto/);
        assert.doesNotMatch(display.textContent, /Pesquisa 1: completo/, 'sem metas, só o resumo incompleto deve ser indicado');
        assert.strictEqual(display.buttons().length, 0, 'referência histórica não contém revisão nem autorização para iniciar retomada');

        const normalized = context.normalizarHistoricoFavoritosFrontend([backendHistory]);
        assert.deepStrictEqual(JSON.parse(JSON.stringify(normalized[0].grupos[0].checkpoint_ref)), checkpointRef);
        assert.strictEqual(normalized[0].grupos[0].checkpoint_id, canonicalId);
        assert.strictEqual(normalized[0].grupos[0].coleta_status, 'parcial');
        const roundtrip = context.montarEntradaHistoricoFavoritos(normalized[0].grupos);
        assert.deepStrictEqual(JSON.parse(JSON.stringify(roundtrip.grupos[0].checkpoint_ref)), checkpointRef, 'novo payload mantém o formato canônico aceito pelo backend');
        const directRoundtrip = context.montarEntradaHistoricoFavoritos([backendGroup]);
        assert.strictEqual(directRoundtrip.grupos[0].checkpoint_id, canonicalId, 'entrada canônica sem aliases também deve sobreviver à montagem');

        context.lerHistoricoFavoritos = () => [backendHistory];
        context.favMlHistoricoExecucaoSelecionadaId = backendHistory.id;
        const selected = context.obterGrupoRankingFavoritosSku('AVULSO');
        assert.strictEqual(selected.entrada, backendHistory, 'grupo selecionado devolve a entrada para preservar o contexto');
        context.renderizarFavoritosOutrosAnuncios('AVULSO');
        assert.match(context.favRankingDataEl.textContent, /Coleta parcial sem anúncios/);
        assert.strictEqual(context.favRankingDataEl.buttons().length, 0);
        context.histMlSkuSelecionado = 'AVULSO';
        context.renderizarHistoricoFavoritos();
        assert.match(context.mlHistoricoFavoritosListEl.textContent, /Coleta parcial sem anúncios/);
        assert.strictEqual(context.mlHistoricoFavoritosEmptyEl.classList.contains('hidden'), true, 'histórico sem aliases mantém grupo vazio após reabrir');
        assert.strictEqual(context.montarUltimosFavoritosRankeados(10, [backendHistory]).length, 1);

        context.window.FavoritosV2.execution.checkpoints.items = [meta({ id: canonicalId, history_entry_id: backendHistory.id })];
        ui.refresh();
        assert.strictEqual(context.favRankingDataEl.buttons().filter(button => button.textContent === 'Retomar coleta').length, 1, 'metas autorizadas carregadas habilitam a retomada após reabertura');
        assert.strictEqual(ui.renderPending(new Element(), [backendHistory]), 0, 'a referência canônica também evita cartão pendente duplicado');
        const conflict = new Element();
        ui.renderGroup(conflict, { ...backendGroup, checkpoint_id: 'alias-antigo', coleta_status: 'concluida' }, backendHistory);
        assert.match(conflict.textContent, /Coleta parcial sem anúncios/, 'referência canônica prevalece sobre aliases antigos');
    }
    {
        const { ui } = harness([meta({ total_rankeados: 0, total_candidatos: 2,
            termos: [{ campo: 1, termo: 'concluído', estado: 'concluida' }] })]);
        const reopened = new Element();
        assert.strictEqual(ui.renderPending(reopened, []), 1);
        assert.match(reopened.textContent, /Coleta parcial sem anúncios/);
        assert.match(reopened.textContent, /0 anúncio\(s\) rankeado\(s\)/);
        assert.match(reopened.textContent, /2 candidato\(s\) preservado\(s\)/);
        assert.doesNotMatch(reopened.textContent, /Pesquisas incompletas|Pesquisa 1:/, 'termos concluídos não devem ser apresentados como pendentes');
        const resume = reopened.buttons()[0];
        assert.strictEqual(resume.textContent, 'Retomar coleta');
        assert.strictEqual(resume.disabled, false);
        assert.match(resume.title, /Recalcular/);
        assert.doesNotMatch(resume.title, /pesquisas incompletas/);

        const direct = new Element();
        ui.renderGroup(direct, { checkpoint_id: 'checkpoint-1', coleta_status: 'parcial', anuncios: [{ id: 'ranking-visível' }] });
        assert.match(direct.textContent, /Ranking parcial/);
        assert.match(direct.textContent, /1 anúncio\(s\) rankeado\(s\)/, 'array do snapshot prevalece sobre total_rankeados do metadata');
    }
    console.log('Favoritos checkpoint UI: partial/zero, queries, safe text, lease, concurrency, lineage, legacy, finalization and history passed.');
}

run().catch(error => { console.error(error); process.exitCode = 1; });
