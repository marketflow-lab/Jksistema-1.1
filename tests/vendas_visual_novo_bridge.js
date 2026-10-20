const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'static', 'vendas', 'visual-novo-bridge.js'), 'utf8');

class FakeEvent {
    constructor(type, options = {}) { this.type = type; Object.assign(this, options); }
}

class Element {
    constructor(id = '') {
        this.id = id;
        this.value = '';
        this.checked = false;
        this.disabled = false;
        this.textContent = '';
        this.className = '';
        this.children = [];
        this.options = [];
        this.attributes = {};
        this.listeners = new Map();
        this.rows = [];
        this.clicks = 0;
    }
    addEventListener(type, listener) {
        if (!this.listeners.has(type)) this.listeners.set(type, []);
        this.listeners.get(type).push(listener);
    }
    dispatchEvent(event) {
        event.target = this;
        (this.listeners.get(event.type) || []).forEach(listener => listener.call(this, event));
        return true;
    }
    click() { if (!this.disabled) { this.clicks++; this.dispatchEvent(new FakeEvent('click')); } }
    getAttribute(name) { return this.attributes[name] ?? null; }
    querySelectorAll(selector) { return selector === 'tr[data-sku]' ? this.rows : []; }
    querySelector(selector) { return this.parts?.[selector] || null; }
}

function createFixture() {
    const nodes = new Map();
    const ids = ['status', 'syncPersistenteStatus', 'lojaBotoes', 'unidadeNegocioSelect', 'dataIni', 'dataFim', 'mesAno', 'compareAnoPassado', 'mostrarEstoqueGeral', 'mostrarEstoqueSku', 'graficoEstoqueSkuInput', 'filtroTexto', 'btnSync', 'btnCancel', 'btnPareto80', 'btnRelatorioGeralSkus', 'tBody', 'resumo'];
    ids.forEach(id => nodes.set(id, new Element(id)));
    nodes.get('unidadeNegocioSelect').value = '__todos';
    nodes.get('unidadeNegocioSelect').options = [{ value: '__todos', textContent: 'Todas as lojas virtuais' }, { value: 'ml-1', textContent: 'Mercado Livre' }];
    nodes.get('status').textContent = 'Pronto';
    nodes.get('syncPersistenteStatus').textContent = 'Atualização automática habilitada';
    nodes.get('resumo').children = [{ querySelector: selector => ({ textContent: selector === 'h3' ? 'Valor líquido' : 'R$ 9.500,00' }) }];
    const buttons = [];
    for (const [attribute, values] of Object.entries({ 'data-periodo': ['3m', '6m', '1a', '2a', 'max'], 'data-tipo': ['linha', 'barra'], 'data-metrica': ['valor', 'quantidade'], 'data-intervalo': ['dia', 'semana', 'mes'] })) {
        values.forEach(value => { const button = new Element(); button.attributes[attribute] = value; buttons.push(button); });
    }
    const microtasks = [], frames = [], published = [], observations = [];
    const browserWindow = new Element();
    browserWindow.dispatchEvent = event => { published.push(event); return Element.prototype.dispatchEvent.call(browserWindow, event); };
    browserWindow.__jkVendasSyncMonitor = { getLastPayload: () => ({ active: true, automacao: { pendentes: 2 } }) };
    const context = vm.createContext({
        window: browserWindow,
        document: {
            getElementById: id => nodes.get(id) || null,
            querySelectorAll: selector => {
                if (selector === '.grafico-btn') return buttons;
                const match = selector.match(/^\.grafico-btn\[([^\]]+)\]$/);
                return match ? buttons.filter(button => button.getAttribute(match[1]) !== null) : [];
            }
        },
        Event: FakeEvent, CustomEvent: FakeEvent,
        MutationObserver: class { constructor(callback) { this.callback = callback; observations.push(this); } observe(node) { this.node = node; } },
        queueMicrotask: callback => microtasks.push(callback),
        requestAnimationFrame: callback => frames.push(callback),
        fetch: () => { throw new Error('A ponte não deve criar chamadas de rede.'); },
        console
    });
    vm.runInContext(`
        const calls = [];
        let activeClient = 'cliente-a';
        let clientId = 'cliente-a';
        let inicio = '2026-04-01', fim = '2026-10-15';
        let lojaSelecionada = 'JK Peças';
        let lojasDisponiveis = [{nome:'JK Peças',store_id:'store-jk'},{nome:'Deckas',store_id:'store-deckas'}];
        let dadosModoResumo = true;
        let resumoTotaisVendas = {pedidos:16460,itens:18290,valor:2327625.58,devolucoes:1125,itensDevolucao:1224,valorDevolucao:191580.41,itensLiquidos:17066,valorLiquido:2136045.16};
        let periodoGrafico = '6m', tipoGrafico = 'linha', metricaGrafico = 'valor', intervaloGrafico = 'mes';
        let compararAnoPassado = false, mostrarEstoqueGeralGrafico = true, mostrarEstoqueSkuGrafico = false, skuAtualGrafico = null;
        let chartInstance = {resize(){calls.push(['resize','sales']);}}, chartEstoqueInstance = {resize(){calls.push(['resize','stock']);}}, chartSkusEstoqueInstance = {resize(){calls.push(['resize','stockSkus']);}};
        const tBody = document.getElementById('tBody'), resumo = document.getElementById('resumo'), statusEl = document.getElementById('status');
        const unidadeNegocioSelect = document.getElementById('unidadeNegocioSelect'), filtroTexto = document.getElementById('filtroTexto');
        const btnSync = document.getElementById('btnSync'), btnCancel = document.getElementById('btnCancel'), mesAno = document.getElementById('mesAno');
        const compareAnoPassado = document.getElementById('compareAnoPassado'), mostrarEstoqueGeralCheck = document.getElementById('mostrarEstoqueGeral'), mostrarEstoqueSkuCheck = document.getElementById('mostrarEstoqueSku'), graficoEstoqueSkuInput = document.getElementById('graficoEstoqueSkuInput');
        const originalReturn = {native:true};
        const nativeError = new Error('Falha nativa preservada');
        let renderReturn = originalReturn;
        function obterClientId(){return activeClient;}
        function getDataIniISO(){return inicio;}
        function getDataFimISO(){return fim;}
        function obterValorExibicaoSku(row){return Number(row?.valor || 0) > 0 ? Number(row.valor) : Number(row?.valorDevolucao || 0);}
        function renderTabelaAgrupada(rows, extra){calls.push(['table',this,rows,extra]);if(extra==='throw')throw nativeError;return renderReturn;}
        function renderResumoTotaisVendas(totals, flag){calls.push(['totals',this,totals,flag]);return originalReturn;}
        function renderResumo(rows, returns, enabled){calls.push(['summary',rows,returns,enabled]);if(dadosModoResumo)renderResumoTotaisVendas(resumoTotaisVendas,enabled);return originalReturn;}
        function renderizarGrafico(data, comparison){calls.push(['graph',this,data,comparison]);return originalReturn;}
        function renderBotoesLojas(stores){calls.push(['stores',stores]);return originalReturn;}
        function atualizarUnidadesNegocio(rows,mapping){calls.push(['units',rows,mapping]);return originalReturn;}
        function atualizarSelecao(){calls.push(['store-action',lojaSelecionada]);unidadeNegocioSelect.value='__todos';return Promise.resolve('native-store-result');}
        function sincronizarPeriodoTopo(start,end){calls.push(['period-inputs',start,end]);inicio=start;fim=end;}
        function atualizarPeriodoComRecarregamento(manual){calls.push(['period-reload',manual]);return originalReturn;}
        function selecionarMes(value){calls.push(['month',value]);return 'native-month-result';}
        unidadeNegocioSelect.addEventListener('change',function(){calls.push(['unit-action',this.value]);});
        filtroTexto.addEventListener('input',function(){calls.push(['search-action',this.value]);});
        compareAnoPassado.addEventListener('change',function(){compararAnoPassado=this.checked;calls.push(['compare-action',this.checked]);});
        mostrarEstoqueGeralCheck.addEventListener('change',function(){mostrarEstoqueGeralGrafico=this.checked;calls.push(['stock-general',this.checked]);});
        mostrarEstoqueSkuCheck.addEventListener('change',function(){mostrarEstoqueSkuGrafico=this.checked;skuAtualGrafico=graficoEstoqueSkuInput.value;calls.push(['stock-sku',this.checked,skuAtualGrafico]);});
        document.querySelectorAll('.grafico-btn').forEach(button=>button.addEventListener('click',function(){
            for(const [attribute,kind] of [['data-periodo','period'],['data-tipo','type'],['data-metrica','metric'],['data-intervalo','interval']]){
                const value=this.getAttribute(attribute);if(!value)continue;
                if(kind==='period')periodoGrafico=value;if(kind==='type')tipoGrafico=value;if(kind==='metric')metricaGrafico=value;if(kind==='interval')intervaloGrafico=value;
                calls.push(['chart-action',kind,value]);
            }
        }));
        for(const id of ['btnSync','btnCancel','btnPareto80','btnRelatorioGeralSkus'])document.getElementById(id).addEventListener('click',()=>calls.push(['button-action',id]));
        globalThis.fixture = {
            calls, originalReturn, nativeError,
            table:(...args)=>renderTabelaAgrupada.apply(globalThis.tableThis,args),
            totals:(...args)=>renderResumoTotaisVendas(...args), summary:(...args)=>renderResumo(...args),
            graph:(...args)=>renderizarGrafico.apply(globalThis.graphThis,args),
            stores:(...args)=>renderBotoesLojas(...args), units:(...args)=>atualizarUnidadesNegocio(...args),
            setClient:value=>{activeClient=value;}, setStore:value=>{lojaSelecionada=value;},
            setNativeTotals:value=>{resumoTotaisVendas=value;}, setSummaryMode:value=>{dadosModoResumo=value;},
            setRenderReturn:value=>{renderReturn=value;}
        };
    `, context);
    vm.runInContext(source, context);
    return {
        context, nodes, buttons, observations, published, fixture: context.fixture,
        bridge: browserWindow.JKVendasLegacyBridge,
        flush() { while (microtasks.length) microtasks.shift()(); },
        frame() { const pending = frames.splice(0); pending.forEach(callback => callback()); },
        pendingMicrotasks: () => microtasks.length
    };
}

function row(sku, overrides = {}) {
    return { sku, produto: `Produto ${sku}`, pedidos: 3, itens: 5, devolucoes: 1, itensDevolucao: 1, valorDevolucao: 12, valor: 60, store_id: 'store-jk', foto: '/catalogo/item.png', nested: { origem: 'real' }, ...overrides };
}

async function main() {
    assert.doesNotThrow(() => new Function(source));
    const h = createFixture(), { bridge, fixture } = h;
    const rows = [row('392-K'), row('devolucao', { valor: 0, valorDevolucao: 125 })];
    const thisValue = { owner: 'legacy' };
    h.context.tableThis = thisValue;
    assert.strictEqual(fixture.table(rows, 'native-argument'), fixture.originalReturn);
    assert.strictEqual(fixture.calls[0][1], thisValue, 'O contexto this do render original deve ser preservado');
    assert.strictEqual(fixture.calls[0][2], rows, 'Os argumentos originais não podem ser substituídos');
    assert.strictEqual(fixture.calls[0][3], 'native-argument');
    fixture.summary([], [], true);
    const graph = { labels: ['2026-04'], valores_vendas: [273488.61], estoque_geral: [7], previsao_mes_atual: { mes: '2026-10', valor_projetado: 355793.7 } };
    const comparison = { labels: ['2025-04'], valores_vendas: [222000] };
    assert.strictEqual(fixture.graph(graph, comparison), fixture.originalReturn);
    assert.strictEqual(h.pendingMicrotasks(), 1, 'Renders encadeados devem gerar uma única publicação');
    h.flush();
    let snapshot = bridge.getSnapshot();
    assert.strictEqual(snapshot.rows.length, 2);
    assert.strictEqual(snapshot.rows[1].displayValue, 125, 'Usa a regra nativa para SKU apenas devolvido');
    assert.strictEqual(snapshot.rows[0].foto, '/catalogo/item.png');
    assert.strictEqual(snapshot.rows[0].store_id, 'store-jk');
    assert.strictEqual(snapshot.totals.valorLiquido, 2136045.16, 'Mantém o total nativo, sem derivá-lo dos SKUs');
    assert.strictEqual(snapshot.totals.pedidos, 16460);
    assert.strictEqual(snapshot.clientId, 'cliente-a');
    assert.strictEqual(snapshot.contextKey, JSON.stringify(['cliente-a', '2026-04-01', '2026-10-15', 'JK Peças', '__todos', 0]));
    assert.strictEqual(snapshot.graph.data.valores_vendas[0], 273488.61);
    assert.strictEqual(snapshot.graph.comparison.valores_vendas[0], 222000);
    assert.strictEqual(snapshot.loading, false);
    assert.strictEqual(h.published.at(-1).type, 'jk:vendas-view-state');

    rows[0].nested.origem = 'alterado depois'; graph.valores_vendas[0] = -1;
    snapshot.rows[0].nested.origem = 'alterado pelo consumidor'; snapshot.totals.valor = -1;
    snapshot.graph.comparison.valores_vendas[0] = -1; snapshot.stores[0].nome = 'Outra'; snapshot.sync.automacao.pendentes = -1;
    snapshot = bridge.getSnapshot();
    assert.strictEqual(snapshot.rows[0].nested.origem, 'real');
    assert.strictEqual(snapshot.totals.valor, 2327625.58);
    assert.strictEqual(snapshot.graph.data.valores_vendas[0], 273488.61);
    assert.strictEqual(snapshot.graph.comparison.valores_vendas[0], 222000);
    assert.strictEqual(snapshot.stores[0].nome, 'JK Peças');
    assert.strictEqual(snapshot.sync.automacao.pendentes, 2);

    let delivered = 0, deliveredProduct = '';
    const unsubscribeFirst = bridge.subscribe(value => { if (value.rows[0]) value.rows[0].produto = 'Corrompido'; }, { immediate: false });
    const unsubscribeSecond = bridge.subscribe(value => { delivered++; deliveredProduct = value.rows[0].produto; }, { immediate: false });
    const unsubscribeThrowing = bridge.subscribe(() => { throw new Error('Consumidor falhou'); }, { immediate: false });
    fixture.table([row('392-K')]); h.flush();
    assert.strictEqual(delivered, 1, 'Um consumidor não modifica o snapshot de outro');
    assert.strictEqual(deliveredProduct, 'Produto 392-K');
    unsubscribeFirst(); unsubscribeSecond(); unsubscribeThrowing();
    fixture.table([row('392-K')]); h.flush();
    assert.strictEqual(delivered, 1, 'A inscrição deve poder ser encerrada');
    let immediate = 0; const unsubscribeImmediate = bridge.subscribe(() => { immediate++; });
    assert.strictEqual(immediate, 1); unsubscribeImmediate();
    assert.throws(() => bridge.subscribe(null), /função/);

    assert.throws(() => fixture.table([row('erro')], 'throw'), error => error === fixture.nativeError, 'A exceção original deve permanecer a mesma');
    assert.strictEqual(bridge.getSnapshot().rows[0].sku, '392-K', 'Falhas do motor não publicam linhas incompletas');
    const promise = Promise.resolve('render-result'); fixture.setRenderReturn(promise);
    assert.strictEqual(fixture.table([row('392-K')]), promise, 'A identidade do retorno do render é preservada');
    fixture.setRenderReturn(fixture.originalReturn); h.flush();

    fixture.setClient('cliente-b'); snapshot = bridge.getSnapshot();
    assert.strictEqual(snapshot.rows.length, 0); assert.strictEqual(snapshot.totals, null); assert.strictEqual(snapshot.graph.data, null);
    assert.strictEqual(snapshot.stores.length, 0); assert.strictEqual(snapshot.units.length, 0); assert.strictEqual(snapshot.sync, null);
    assert.strictEqual(snapshot.loading, true, 'Dados do cliente anterior ficam indisponíveis ao mudar de cliente');
    fixture.setClient('cliente-a');
    assert.strictEqual(bridge.getSnapshot().rows[0].sku, '392-K');
    const storeResult = bridge.setStore('Deckas');
    assert.strictEqual(await storeResult, 'native-store-result');
    assert.strictEqual(bridge.getSnapshot().rows.length, 0, 'Não rotula as linhas da loja anterior como nova loja');
    assert.strictEqual(bridge.getSnapshot().totals, null);
    assert.throws(() => bridge.setStore('Loja inexistente'), /disponível/);
    fixture.table([row('deckas', { store_id: 'store-deckas' })]); fixture.summary([], [], true); h.flush();
    assert.strictEqual(bridge.getSnapshot().rows[0].sku, 'deckas');
    assert.strictEqual(bridge.setUnit('ml-1'), true);
    assert.strictEqual(bridge.getSnapshot().filters.unidade, 'ml-1');
    assert.strictEqual(bridge.getSnapshot().rows.length, 0, 'Troca de unidade também invalida linhas antigas');
    assert.throws(() => bridge.setUnit('outra'), /disponível/);
    assert.strictEqual(bridge.setPeriod('2026-09-01', '2026-09-30'), fixture.originalReturn);
    assert.deepStrictEqual(Array.from(fixture.calls.find(call => call[0] === 'period-inputs').slice(1)), ['2026-09-01', '2026-09-30']);
    assert.throws(() => bridge.setPeriod('2026-02-31', '2026-03-01'), /válido/);
    assert.throws(() => bridge.setPeriod('2026-10-15', '2026-09-01'), /válido/);
    assert.strictEqual(bridge.setMonth('2026-08'), 'native-month-result');
    assert.throws(() => bridge.setMonth('2026-13'), /válido/);

    for (const [method, value] of [['setChartPeriod', '1a'], ['setChartType', 'barra'], ['setMetric', 'quantidade'], ['setInterval', 'semana']]) assert.strictEqual(bridge[method](value), true);
    snapshot = bridge.getSnapshot();
    assert.strictEqual(snapshot.chartOptions.periodo, '1a'); assert.strictEqual(snapshot.chartOptions.tipoGrafico, 'barra');
    assert.strictEqual(snapshot.chartOptions.metrica, 'quantidade'); assert.strictEqual(snapshot.chartOptions.intervaloGrafico, 'semana');
    assert.throws(() => bridge.setChartType('tipo-arbitrario'), /inválida/);
    bridge.setComparison(true); bridge.setStockGeneral(false); bridge.setStockSku(true, '  392-K  ');
    snapshot = bridge.getSnapshot();
    assert.strictEqual(snapshot.chartOptions.compararAnoPassado, true);
    assert.strictEqual(snapshot.chartOptions.mostrarEstoqueGeralGrafico, false);
    assert.strictEqual(snapshot.chartOptions.mostrarEstoqueSkuGrafico, true);
    assert.strictEqual(snapshot.chartOptions.skuAtualGrafico, '392-K');
    bridge.setSearch('392-K');
    assert.strictEqual(bridge.getSnapshot().filters.search, '392-K');
    assert.strictEqual(bridge.getSnapshot().loading, true);
    assert.ok(fixture.calls.some(call => call[0] === 'search-action' && call[1] === '392-K'));
    bridge.sync(); bridge.cancel(); bridge.openReport('pareto'); bridge.openReport('general');
    for (const id of ['btnSync', 'btnCancel', 'btnPareto80', 'btnRelatorioGeralSkus']) assert.strictEqual(h.nodes.get(id).clicks, 1);
    h.nodes.get('btnSync').disabled = true; assert.strictEqual(bridge.sync(), false); assert.strictEqual(h.nodes.get('btnSync').clicks, 1);
    assert.throws(() => bridge.openReport('outro'), /inválido/);
    bridge.resizeCharts(); assert.strictEqual(fixture.calls.filter(call => call[0] === 'resize').length, 3);
    assert.strictEqual(typeof bridge.getChartInstances().sales.resize, 'function');

    bridge.setSearch(''); fixture.table([row('392-K'), row('lote')]);
    const nativeRow = new Element(); nativeRow.attributes['data-sku'] = '392-K'; h.nodes.get('tBody').rows = [nativeRow];
    assert.strictEqual(bridge.openSku('392-K'), true); assert.strictEqual(nativeRow.clicks, 1);
    assert.strictEqual(bridge.openSku('inexistente'), false, 'Não abre SKU ausente do contexto de vendas');
    const pending = bridge.openSku('lote');
    const delayedRow = new Element(); delayedRow.attributes['data-sku'] = 'lote'; h.nodes.get('tBody').rows.push(delayedRow);
    h.frame(); assert.strictEqual(await pending, true); assert.strictEqual(delayedRow.clicks, 1);
    h.nodes.get('tBody').rows = [];
    const cancelled = bridge.openSku('lote'); fixture.setStore('JK Peças'); h.frame();
    assert.strictEqual(await cancelled, false, 'Navegação pendente é cancelada se o escopo mudar');

    fixture.setSummaryMode(false); fixture.summary([], [], true);
    assert.strictEqual(bridge.getSnapshot().totals, null);
    assert.strictEqual(bridge.getSnapshot().summaryCards[0].valueText, 'R$ 9.500,00', 'Fluxo legado sem resumo numérico mantém os valores exibidos');
    const largeList = Array.from({ length: 6001 }, (_, index) => row(`SKU-${index}`));
    fixture.table(largeList); h.flush();
    assert.strictEqual(bridge.getSnapshot().rows.length, 6001, 'A ponte não limita os produtos ao recorte de uma prévia ou cache');

    fixture.graph(graph, comparison);
    assert.strictEqual(bridge.getSnapshot().graphLoading, false);
    bridge.setMetric('valor');
    assert.strictEqual(bridge.getSnapshot().graphLoading, true, 'Métrica nova invalida o canvas ainda renderizado na unidade anterior');
    assert.strictEqual(bridge.getSnapshot().graph.data, null);
    fixture.graph(graph, comparison);
    assert.strictEqual(bridge.getSnapshot().graphLoading, false);
    bridge.setChartType('linha');
    assert.strictEqual(bridge.getSnapshot().graphLoading, true, 'Tipo novo invalida a apresentação até o render correspondente');
    assert.strictEqual(bridge.getSnapshot().graph.data, null);
    fixture.graph(graph, comparison);
    assert.strictEqual(bridge.getSnapshot().graphLoading, false);

    const bridgeBefore = bridge;
    vm.runInContext(source, h.context);
    assert.strictEqual(h.context.window.JKVendasLegacyBridge, bridgeBefore, 'Carregamento repetido não duplica a ponte');
    assert.ok(h.observations.length > 0, 'Status e opções legados também devem publicar atualizações');
    console.log('vendas_visual_novo_bridge: ok (herança, ações, retornos, exceções, isolamento, lote e lista completa)');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
