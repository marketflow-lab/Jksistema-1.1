'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const helpers = require(path.join(root, 'static', 'vendas', 'periodos-completos.js'));
const source = fs.readFileSync(path.join(root, 'static', 'vendas', 'grafico.js'), 'utf8');
const periodoCacheSource = fs.readFileSync(path.join(root, 'static', 'vendas', 'periodo-cache.js'), 'utf8');
const initSource = fs.readFileSync(path.join(root, 'static', 'vendas', 'init.js'), 'utf8');
const dadosRenderSource = fs.readFileSync(path.join(root, 'static', 'vendas', 'dados-render.js'), 'utf8');
const syncSource = fs.readFileSync(path.join(root, 'static', 'vendas', 'sync.js'), 'utf8');
const htmlStatic = fs.readFileSync(path.join(root, 'static', 'vendas.html'), 'utf8');
const htmlRoot = fs.readFileSync(path.join(root, 'vendas.html'), 'utf8');
const skuHtmlStatic = fs.readFileSync(path.join(root, 'static', 'vendas_sku.html'), 'utf8');
const skuHtmlRoot = fs.readFileSync(path.join(root, 'vendas_sku.html'), 'utf8');
const installerManifest = JSON.parse(fs.readFileSync(
    path.join(root, 'electron_app', 'installer-required-resources.json'),
    'utf8'
));
const referencia = new Date(2026, 7, 19, 12, 0, 0);

assert.deepStrictEqual(helpers.calcularPeriodoMesesCompletos('3m', referencia), {
    inicio: '2026-05-01',
    fim: '2026-07-31'
});
assert.deepStrictEqual(helpers.calcularPeriodoMesesCompletos('6m', referencia), {
    inicio: '2026-02-01',
    fim: '2026-07-31'
});
assert.deepStrictEqual(helpers.calcularPeriodoMesesCompletos('1a', referencia), {
    inicio: '2025-08-01',
    fim: '2026-07-31'
});
assert.deepStrictEqual(helpers.calcularPeriodoMesesCompletos('2a', referencia), {
    inicio: '2024-08-01',
    fim: '2026-07-31'
});
assert.deepStrictEqual(helpers.calcularPeriodoComMesAtual('3m', referencia), {
    inicio: '2026-05-01',
    fim: '2026-08-19'
});
assert.deepStrictEqual(helpers.calcularPeriodoComMesAtual('6m', referencia), {
    inicio: '2026-02-01',
    fim: '2026-08-19'
});
assert.deepStrictEqual(helpers.calcularPeriodoComMesAtual('1a', referencia), {
    inicio: '2025-08-01',
    fim: '2026-08-19'
});
assert.deepStrictEqual(helpers.calcularPeriodoComMesAtual('2a', referencia), {
    inicio: '2024-08-01',
    fim: '2026-08-19'
});
assert.deepStrictEqual(
    helpers.calcularPeriodoComMesAtual('3m', new Date(2026, 0, 15, 12, 0, 0)),
    { inicio: '2025-10-01', fim: '2026-01-15' },
    'mês atual deve atravessar a virada do ano sem perder os três meses anteriores'
);
assert.deepStrictEqual(
    helpers.calcularPeriodoComMesAtual('3m', new Date(2024, 2, 1, 23, 30, 0)),
    { inicio: '2023-12-01', fim: '2024-03-01' },
    'início de março deve preservar fevereiro bissexto como mês anterior completo'
);
assert.deepStrictEqual(
    helpers.calcularPeriodoComMesAtual('3m', new Date(2026, 7, 1, 0, 0, 0)),
    { inicio: '2026-05-01', fim: '2026-08-01' },
    'primeiro dia do mês atual deve ser incluído literalmente'
);
assert.deepStrictEqual(
    helpers.calcularPeriodoComMesAtual('3m', new Date(2026, 7, 31, 23, 59, 59)),
    { inicio: '2026-05-01', fim: '2026-08-31' },
    'último dia do mês atual deve ser incluído literalmente'
);
assert.strictEqual(
    helpers.calcularPeriodoComMesAtual('max', referencia),
    null,
    'Max não deve ser reinterpretado pela função dos atalhos fixos'
);
assert.strictEqual(
    helpers.calcularPeriodoComMesAtual('manual', referencia),
    null,
    'período manual não deve ser reinterpretado pela função dos atalhos fixos'
);
assert.strictEqual(
    helpers.calcularPeriodoComMesAtual('3m', new Date('invalida')),
    null,
    'data de referência inválida deve ser rejeitada'
);
assert.deepStrictEqual(
    helpers.calcularPeriodoMesesCompletos('3m', new Date(2026, 0, 15, 12, 0, 0)),
    { inicio: '2025-10-01', fim: '2025-12-31' },
    'virada do ano deve preservar três meses completos'
);
assert.deepStrictEqual(
    helpers.calcularPeriodoMesesCompletos('3m', new Date(2024, 2, 15, 23, 30, 0)),
    { inicio: '2023-12-01', fim: '2024-02-29' },
    'fevereiro bissexto deve fechar sem depender de conversão UTC'
);
assert.deepStrictEqual(
    helpers.calcularPeriodoMaximoMesesCompletos(
        { inicio: '2024-03-17', fim: '2026-08-19' },
        referencia
    ),
    { inicio: '2024-03-01', fim: '2026-07-31' },
    'Max deve começar no primeiro dia e excluir o mês atual parcial'
);
assert.deepStrictEqual(
    helpers.calcularPeriodoMaximoMesesCompletos(
        { inicio: '2023-11-20', fim: '2024-02-03' },
        referencia
    ),
    { inicio: '2023-11-01', fim: '2024-02-29' },
    'Max deve fechar corretamente um mês histórico bissexto'
);
assert.strictEqual(
    helpers.calcularPeriodoMaximoMesesCompletos(
        { inicio: '2026-08-01', fim: '2026-08-19' },
        referencia
    ),
    null,
    'Max não pode transformar somente o mês atual parcial em resultado completo'
);
assert.deepStrictEqual(helpers.calcularQuantidadeMesesCompletos(120, referencia), {
    inicio: '2016-08-01',
    fim: '2026-07-31'
});
assert.strictEqual(
    helpers.deveNormalizarPreferenciaAtalho(
        { inicio: '2026-02-20', fim: '2026-08-19' },
        '6m'
    ),
    true,
    'preferência legada de 180 dias deve migrar para seis meses fechados'
);
assert.strictEqual(
    helpers.deveNormalizarPreferenciaAtalho(
        { inicio: '2026-02-20', fim: '2026-08-19', origem: 'manual' },
        '6m'
    ),
    false,
    'intervalo manual não pode ser reinterpretado como atalho'
);
assert.strictEqual(
    helpers.deveNormalizarPreferenciaAtalho(
        { inicio: '2026-02-01', fim: '2026-07-31', origem: 'atalho', periodo: '6m' },
        '6m'
    ),
    true,
    'atalho salvo deve avançar para os últimos meses completos ao reabrir a tela'
);
assert.strictEqual(
    helpers.deveNormalizarPreferenciaAtalho(
        { inicio: '2026-02-01', fim: '2026-07-31', origem: 'atalho', periodo: '6m' },
        '3m'
    ),
    false,
    'preferência de outro atalho não pode normalizar o período ativo'
);
assert.strictEqual(
    helpers.deveNormalizarPreferenciaAtalho(
        { inicio: '2024-03-17', fim: '2026-08-18' },
        'max'
    ),
    true,
    'Max legado deve migrar mesmo quando os limites avançaram desde o último uso'
);
assert.strictEqual(
    helpers.deveNormalizarPreferenciaAtalho(
        { inicio: '2024-03-01', fim: '2026-07-31', origem: 'manual' },
        'max'
    ),
    false,
    'intervalo manual versionado deve ser preservado mesmo com Max ativo'
);

const listenersMarker = source.indexOf('// Event listeners para controles de gráfico');
const listenerStart = source.indexOf("document.querySelectorAll('.grafico-btn[data-periodo]')", listenersMarker);
const listenerEnd = source.indexOf("document.querySelectorAll('.grafico-btn[data-tipo]')", listenerStart);
const listenerSource = source.slice(listenerStart, listenerEnd);
const aplicarPeriodoStart = source.indexOf('function obterAssinaturaContextoPeriodoGrafico');
const normalizarPeriodoStart = source.indexOf('async function normalizarPeriodoGraficoRestaurado');
const aplicarPeriodoSource = source.slice(aplicarPeriodoStart, normalizarPeriodoStart);
const normalizarPeriodoSource = source.slice(normalizarPeriodoStart, listenersMarker);
assert.match(aplicarPeriodoSource, /periodosCompletos\.calcularPeriodoMesesCompletos\(periodo, hoje\)/);
assert.match(aplicarPeriodoSource, /periodosCompletos\.calcularPeriodoComMesAtual\(periodo, hoje\)/);
assert.match(aplicarPeriodoSource, /periodosCompletos\.calcularPeriodoMaximoMesesCompletos\(limites, hoje\)/);
assert.match(aplicarPeriodoSource, /tokenAtual !== periodoGraficoSelecaoToken/);
assert.match(aplicarPeriodoSource, /assinaturaContexto !== obterAssinaturaContextoPeriodoGrafico\(\)/);
assert.match(aplicarPeriodoSource, /if \(periodo === 'max'\) return null/);
assert.doesNotMatch(aplicarPeriodoSource, /obterLimitesComVendas\(\)/);
assert.match(aplicarPeriodoSource, /origem: 'atalho', periodo/);
assert.match(listenerSource, /aplicarPeriodoGraficoMesesCompletos\(periodoSelecionado\)/);
assert.match(listenerSource, /if \(periodoGraficoAtalhoPendente === periodoSelecionado\) return/);
assert.match(listenerSource, /if \(resultado === null && periodoGrafico === periodoSelecionado\)/);
assert.match(listenerSource, /periodoGrafico = periodoAnterior \|\| '3m'/);
assert.doesNotMatch(
    listenerSource.slice(0, listenerSource.indexOf('aplicarPeriodoGraficoMesesCompletos(periodoSelecionado)')),
    /salvarPreferenciaGrafico\(\)/,
    'preset pendente não pode ser persistido antes de as datas serem aplicadas'
);
assert.doesNotMatch(
    listenerSource.slice(0, listenerSource.indexOf('aplicarPeriodoGraficoMesesCompletos(periodoSelecionado)')),
    /classList\.(?:add|remove)\('active'\)/,
    'Max pendente deve manter o botão anterior até calcular datas completas'
);
assert.match(aplicarPeriodoSource, /aplicarPreferenciaGraficoUI\(\);\s*salvarPreferenciaGrafico\(\)/);
assert.doesNotMatch(listenerSource, /periodoAnterior !== 'max'/);
assert.doesNotMatch(listenerSource, /setDate\(|-\s*(90|180|365|730)/);
assert.match(normalizarPeriodoSource, /deveNormalizarPreferenciaAtalho/);
assert.match(normalizarPeriodoSource, /const preferenciaRestaurada = carregarPreferenciaPeriodoData\(\)/);
assert.match(normalizarPeriodoSource, /preferenciaRestaurada\.origem === 'manual'/);
assert.match(normalizarPeriodoSource, /preferenciaRestaurada\.periodo !== periodoGrafico/);
assert.match(normalizarPeriodoSource, /if \(periodoGraficoAtalhoPendente\) return false/);
assert.match(normalizarPeriodoSource, /recarregar: false/);
assert.match(aplicarPeriodoSource, /reaplicarPeriodoGraficoAposMudancaFiltro/);
assert.match(initSource, /const periodoNormalizado = await normalizarPeriodoGraficoRestaurado\(\)/);
assert.match(initSource, /periodoNormalizado === false\s*&& !periodoGraficoAtalhoPendente[\s\S]*await reaplicarPeriodoGraficoAposMudancaFiltro\(\)/);
assert.match(initSource, /if \(data_inicio_param && data_fim_param\) \{\s*periodoGrafico = 'manual'/);
assert.match(periodoCacheSource, /function salvarPreferenciaPeriodoData\(preferencia = \{\}\)/);
assert.match(periodoCacheSource, /origem,\s*periodo,\s*versao: 2/);
assert.match(periodoCacheSource, /marcarPeriodoGraficoManual\(\)/);
assert.match(source, /function invalidarPeriodoGraficoPendente\(\) \{[\s\S]*periodoGraficoAtalhoPendente = null/);
assert.match(source, /periodoGraficoIncluiMesAtual\(\)\) params\.set\('incluir_previsao_mes_atual', 'true'\)/);
assert.match(source, /previsaoMesAtual && intervaloGrafico === 'mes' && !usandoQuantidade/);
assert.match(source, /isForecast: true/);
assert.doesNotMatch(source, /Mês atual realizado até/);
assert.doesNotMatch(source, /const resumoPrevisaoMesAtual/);
assert.doesNotMatch(source, /subtitle:\s*\{\s*display: Boolean\(previsaoMesAtual\)/);
assert.match(source, /indicesCompactos\[indicesCompactos\.length - 1\] = ultimoIndice/);
assert.match(source, /estimativa, não valor realizado/);
assert.match(aplicarPeriodoSource, /removerDatasExplicitasUrlAposAtalho\(\)/);
assert.match(dadosRenderSource, /let periodoAtualizadoPromise = Promise\.resolve\(true\)/);
assert.match(dadosRenderSource, /periodoAtualizadoPromise = reaplicarPeriodoGraficoAposMudancaFiltro\(\)/);
assert.match(dadosRenderSource, /periodoAtualizado = await periodoAtualizadoPromise;\s*if \(periodoAtualizado === false\) return;\s*await carregarVendas/);
assert.match(syncSource, /periodoAtualizado = await reaplicarPeriodoGraficoAposMudancaFiltro\(\);\s*if \(periodoAtualizado === false\) return;[\s\S]*await carregarVendas/);
assert.ok(
    syncSource.indexOf("unidadeNegocioSelect.value = '__todos'")
        < syncSource.indexOf('await reaplicarPeriodoGraficoAposMudancaFiltro()'),
    'troca de loja deve limpar a unidade antes de recalcular Max'
);
assert.ok(
    syncSource.indexOf("btn.classList.toggle('active'")
        < syncSource.indexOf('await reaplicarPeriodoGraficoAposMudancaFiltro()'),
    'troca de loja deve atualizar a seleção visual antes de aguardar Max'
);

const compactarStart = source.indexOf('function compactarDadosGrafico');
const compactarEnd = source.indexOf('function renderizarGrafico(', compactarStart);
const compactarSource = source.slice(compactarStart, compactarEnd);
const compactarDadosGraficoTeste = new Function(
    `${compactarSource}; return compactarDadosGrafico;`
)();
const labelsLongos = Array.from({ length: 320 }, (_valor, indice) => `dia-${indice}`);
const serieLonga = Array.from({ length: 320 }, (_valor, indice) => indice);
const dadosCompactados = compactarDadosGraficoTeste(labelsLongos, [serieLonga], 160);
assert.ok(dadosCompactados.labels.length <= 160, 'compactação deve respeitar o limite de pontos');
assert.strictEqual(dadosCompactados.labels.at(-1), 'dia-319', 'compactação deve preservar o mês/dia atual');
assert.strictEqual(dadosCompactados.series[0].at(-1), 319, 'séries devem preservar o valor do último ponto');

assert.match(htmlStatic, /periodos-completos\.js\?v=20260827-vendas-previsao-mes-atual-v1/);
assert.match(htmlStatic, /periodo-cache\.js\?v=20260831-vendas-lojas-visiveis-v1/);
assert.match(htmlStatic, /sync\.js\?v=20260831-vendas-lojas-visiveis-v1/);
assert.match(htmlStatic, /dados-render\.js\?v=20260831-vendas-lojas-visiveis-v1/);
assert.match(htmlStatic, /grafico\.js\?v=20260828-vendas-sem-resumo-previsao-v1/);
assert.match(htmlStatic, /init\.js\?v=20260819-vendas-meses-completos-v1/);
assert.match(htmlStatic, /data-periodo="3m" title="3 meses completos \+ mês atual" aria-label="3 meses completos \+ mês atual"/);
assert.match(htmlStatic, /data-periodo="6m" title="6 meses completos \+ mês atual" aria-label="6 meses completos \+ mês atual"/);
assert.match(htmlStatic, /data-periodo="1a" title="12 meses completos \+ mês atual" aria-label="12 meses completos \+ mês atual"/);
assert.match(htmlStatic, /data-periodo="2a" title="24 meses completos \+ mês atual" aria-label="24 meses completos \+ mês atual"/);
assert.match(htmlStatic, /data-periodo="max" title="Máximo: meses completos" aria-label="Máximo: meses completos"/);
assert.strictEqual(htmlRoot, htmlStatic, 'vendas.html e static/vendas.html devem permanecer espelhados');
assert.match(skuHtmlStatic, /periodos-completos\.js\?v=20260827-vendas-previsao-mes-atual-v1/);
assert.match(skuHtmlStatic, /data-periodo="3m" title="3 meses completos \+ mês atual" aria-label="3 meses completos \+ mês atual"/);
assert.match(skuHtmlStatic, /data-periodo="6m" title="6 meses completos \+ mês atual" aria-label="6 meses completos \+ mês atual"/);
assert.match(skuHtmlStatic, /data-periodo="1a" title="12 meses completos \+ mês atual" aria-label="12 meses completos \+ mês atual"/);
assert.match(skuHtmlStatic, /data-periodo="2a" title="24 meses completos \+ mês atual" aria-label="24 meses completos \+ mês atual"/);
assert.match(skuHtmlStatic, /data-periodo="max" title="Máximo: meses completos" aria-label="Máximo: meses completos"/);
assert.match(skuHtmlStatic, /obterLimitesComVendasServidorSku/);
assert.match(skuHtmlStatic, /calcularPeriodoMaximoMesesCompletos\(limites, hoje\)/);
assert.match(skuHtmlStatic, /calcularPeriodoComMesAtual\(periodo, hoje\)/);
assert.match(skuHtmlStatic, /\['3m', '6m', '1a', '2a'\]\.includes\(periodoGrafico\)[\s\S]*params\.set\('incluir_previsao_mes_atual', 'true'\)/);
assert.match(skuHtmlStatic, /previsaoMesAtual[\s\S]*intervaloGrafico === 'mes'[\s\S]*!usandoQuantidade/);
assert.match(skuHtmlStatic, /isForecast: true/);
assert.match(skuHtmlStatic, /const valorBruto = \(dataset\.data \|\| \[\]\)\[index\];\s*if \(valorBruto === null \|\| valorBruto === undefined \|\| valorBruto === ''\) return/);
assert.match(skuHtmlStatic, /Previsão linear de faturamento bruto/);
assert.doesNotMatch(skuHtmlStatic, /Mês atual realizado até/);
assert.doesNotMatch(skuHtmlStatic, /const resumoPrevisaoMesAtual/);
assert.doesNotMatch(skuHtmlStatic, /subtitle:\s*\{\s*display: Boolean\(previsaoMesAtual\)/);
assert.match(skuHtmlStatic, /tokenAtual !== carregarGraficoSkuToken/);
assert.match(skuHtmlStatic, /simulação linear:[^<]*dias corridos/);
assert.match(skuHtmlStatic, /tokenAtual !== periodoGraficoSelecaoToken/);
assert.match(skuHtmlStatic, /assinaturaContexto !== obterAssinaturaContextoPeriodoGraficoSku\(\)/);
assert.match(skuHtmlStatic, /atualizarUrlPeriodoGraficoSku\(periodo\)/);
assert.match(skuHtmlStatic, /if \(periodoGraficoAtalhoPendente === periodoSelecionado\) return/);
assert.match(skuHtmlStatic, /function invalidarPeriodoGraficoPendenteSku\(\) \{[\s\S]*periodoGraficoAtalhoPendente = null/);
assert.match(skuHtmlStatic, /reaplicarPeriodoGraficoAposMudancaFiltroSku/);
assert.match(skuHtmlStatic, /carregarUnidadesNegocioSku\(\)\.finally\(async \(\) => \{[\s\S]*reaplicarPeriodoGraficoAposMudancaFiltroSku\(\)/);
assert.match(skuHtmlStatic, /periodoGrafico = 'manual'/);
assert.match(skuHtmlStatic, /qs\.delete\('periodo_grafico'\)/);
assert.match(skuHtmlStatic, /marcarPeriodoGraficoManualSku\(\)/);
assert.doesNotMatch(skuHtmlStatic, /periodoAnterior !== 'max'/);
assert.doesNotMatch(skuHtmlStatic, /calcularQuantidadeMesesCompletos\(120/);
const skuInlineScripts = Array.from(
    skuHtmlStatic.matchAll(/<script>\s*([\s\S]*?)<\/script>/g),
    match => match[1]
);
assert.ok(skuInlineScripts.length >= 2, 'scripts inline da tela SKU devem existir');
skuInlineScripts.forEach((script, indice) => {
    assert.doesNotThrow(
        () => new Function(script),
        `script inline ${indice + 1} da tela SKU deve continuar válido`
    );
});
const skuListenerStart = skuHtmlStatic.indexOf('// Event listeners para controles de gráfico');
const skuListenerEnd = skuHtmlStatic.indexOf("document.querySelectorAll('.grafico-btn[data-tipo]')", skuListenerStart);
const skuListenerSource = skuHtmlStatic.slice(skuListenerStart, skuListenerEnd);
assert.doesNotMatch(
    skuListenerSource,
    /setDate\(|setFullYear\(|-\s*(90|180|365|730)/
);
assert.doesNotMatch(
    skuListenerSource.slice(0, skuListenerSource.indexOf('aplicarPeriodoGraficoMesesCompletosSku(periodoSelecionado)')),
    /classList\.(?:add|remove)\('active'\)/,
    'Max SKU pendente deve manter o botão anterior até calcular datas completas'
);
assert.match(skuHtmlStatic, /atualizarUrlPeriodoGraficoSku\(periodo\);\s*aplicarPreferenciaGraficoUI\(\);\s*salvarPreferenciaGraficoSku\(\)/);
assert.strictEqual(skuHtmlRoot, skuHtmlStatic, 'vendas_sku.html e static/vendas_sku.html devem permanecer espelhados');
assert.ok(installerManifest.requiredSourceFiles.includes('static/vendas/periodos-completos.js'));
assert.ok(installerManifest.requiredPackagedFiles.includes('local_app/static/vendas/periodos-completos.js'));

const skuAplicarPeriodoStart = skuHtmlStatic.indexOf('async function aplicarPeriodoGraficoMesesCompletosSku');
const skuAplicarPeriodoEnd = skuHtmlStatic.indexOf('function formatarLabelDataGrafico', skuAplicarPeriodoStart);
const skuAplicarPeriodoSource = skuHtmlStatic.slice(skuAplicarPeriodoStart, skuAplicarPeriodoEnd);
assert.ok(skuAplicarPeriodoStart >= 0 && skuAplicarPeriodoEnd > skuAplicarPeriodoStart);

async function testarCliqueConcorrenteAposMax() {
    const DataFixa = class extends Date {
        constructor(...args) {
            if (args.length) {
                super(...args);
            } else {
                super(2026, 7, 19, 12, 0, 0);
            }
        }
    };

    function criarCenario() {
        let resolverLimitesMax;
        const limitesMaxPendentes = new Promise(resolve => {
            resolverLimitesMax = resolve;
        });
        const periodosAplicados = [];
        const preferenciasSalvas = [];
        const urlsAtualizadas = [];
        const periodosConfirmadosNaUi = [];
        const criarHarness = new Function(
            'window',
            'Date',
            'limitesMaxPendentes',
            'registrarPeriodo',
            'registrarPreferencia',
            'registrarUi',
            `
                let periodoGraficoSelecaoToken = 0;
                let periodoGraficoAtalhoPendente = null;
                let periodoGrafico = '3m';
                let periodoSelecionadoPeloUsuario = false;
                let lojaSelecionada = '__todas';
                const unidadeNegocioSelect = { value: '__todos' };
                let datasAtuais = { inicio: '2026-05-20', fim: '2026-08-19' };
                const getDataIniISO = () => datasAtuais.inicio;
                const getDataFimISO = () => datasAtuais.fim;
                const obterLimitesComVendasServidor = () => limitesMaxPendentes;
                const obterLimitesComVendas = () => null;
                const sincronizarPeriodoTopo = (inicio, fim) => {
                    datasAtuais = { inicio, fim };
                    registrarPeriodo(inicio, fim);
                };
                const atualizarPeriodoTexto = () => {};
                const salvarPreferenciaPeriodoData = registrarPreferencia;
                const salvarPreferenciaGrafico = () => {};
                const aplicarPreferenciaGraficoUI = () => registrarUi(periodoGrafico);
                const atualizarPeriodoComRecarregamento = () => {
                    throw new Error('o teste usa recarregar=false');
                };
                ${aplicarPeriodoSource}
                return {
                    selecionar(periodo) {
                        periodoGrafico = periodo;
                        return aplicarPeriodoGraficoMesesCompletos(periodo, {
                            hoje: new Date(2026, 7, 19, 12, 0, 0),
                            recarregar: false
                        });
                    },
                    definirDatas(inicio, fim) {
                        datasAtuais = { inicio, fim };
                    },
                    definirUnidade(unidade) {
                        unidadeNegocioSelect.value = unidade;
                    },
                    reaplicar: () => reaplicarPeriodoGraficoAposMudancaFiltro()
                };
            `
        );
        const janela = {
            JKVendasPeriodosCompletos: helpers,
            location: {
                search: '?data_inicio=2026-02-20&data_fim=2026-08-19&loja=Loja+A',
                pathname: '/vendas.html',
                hash: ''
            },
            history: {
                replaceState(_estado, _titulo, destino) {
                    urlsAtualizadas.push(destino);
                }
            }
        };
        return {
            harness: criarHarness(
                janela,
                DataFixa,
                limitesMaxPendentes,
                (inicio, fim) => periodosAplicados.push({ inicio, fim }),
                preferencia => preferenciasSalvas.push(preferencia),
                periodo => periodosConfirmadosNaUi.push(periodo)
            ),
            resolverLimitesMax,
            periodosAplicados,
            preferenciasSalvas,
            urlsAtualizadas,
            periodosConfirmadosNaUi
        };
    }

    const atalho = criarCenario();
    const cliqueMax = atalho.harness.selecionar('max');
    const clique6m = await atalho.harness.selecionar('6m');
    atalho.resolverLimitesMax({ inicio: '2020-04-12', fim: '2026-08-19' });
    const maxTardio = await cliqueMax;
    assert.strictEqual(clique6m, true);
    assert.strictEqual(maxTardio, false, 'resposta tardia de Max não pode sobrescrever outro atalho');
    assert.deepStrictEqual(atalho.periodosAplicados, [{ inicio: '2026-02-01', fim: '2026-08-19' }]);
    assert.deepStrictEqual(atalho.preferenciasSalvas, [{ origem: 'atalho', periodo: '6m' }]);
    assert.deepStrictEqual(atalho.periodosConfirmadosNaUi, ['6m']);
    assert.ok(
        atalho.urlsAtualizadas.some(url => url === '/vendas.html?loja=Loja+A'),
        'atalho confirmado deve remover datas explícitas antigas da URL'
    );

    const manual = criarCenario();
    const maxAntesDoManual = manual.harness.selecionar('max');
    manual.harness.definirDatas('2026-04-15', '2026-07-10');
    manual.resolverLimitesMax({ inicio: '2020-04-12', fim: '2026-08-19' });
    assert.strictEqual(await maxAntesDoManual, false, 'Max tardio não pode sobrescrever datas manuais');
    assert.deepStrictEqual(manual.periodosAplicados, []);

    const filtro = criarCenario();
    const maxAntesDoFiltro = filtro.harness.selecionar('max');
    filtro.harness.definirUnidade('Unidade Outlet');
    filtro.resolverLimitesMax({ inicio: '2020-04-12', fim: '2026-08-19' });
    assert.strictEqual(await maxAntesDoFiltro, false, 'Max tardio não pode usar limites de outro filtro');
    assert.deepStrictEqual(filtro.periodosAplicados, []);

    const filtroReaplicado = criarCenario();
    const maxDoContextoAnterior = filtroReaplicado.harness.selecionar('max');
    filtroReaplicado.harness.definirUnidade('Unidade Outlet');
    const maxDoContextoAtual = filtroReaplicado.harness.reaplicar();
    filtroReaplicado.resolverLimitesMax({ inicio: '2020-04-12', fim: '2026-08-19' });
    assert.strictEqual(await maxDoContextoAnterior, false);
    assert.strictEqual(await maxDoContextoAtual, true);
    assert.deepStrictEqual(filtroReaplicado.periodosAplicados, [{ inicio: '2020-04-01', fim: '2026-07-31' }]);
    assert.deepStrictEqual(filtroReaplicado.periodosConfirmadosNaUi, ['max']);

    const indisponivel = criarCenario();
    const maxSemLimites = indisponivel.harness.selecionar('max');
    indisponivel.resolverLimitesMax(null);
    assert.strictEqual(await maxSemLimites, null, 'Max sem mês completo não pode fingir ser 3m');
    assert.deepStrictEqual(indisponivel.periodosAplicados, []);
}

async function testarRestauracaoPreferenciaLegada() {
    const DataFixa = class extends Date {
        constructor(...args) {
            if (args.length) {
                super(...args);
            } else {
                super(2026, 7, 19, 12, 0, 0);
            }
        }
    };
    const criarHarness = new Function(
        'window',
        'Date',
        'preferenciaInicial',
        'periodoInicial',
        'limitesServidor',
        `
            let periodoGraficoSelecaoToken = 0;
            let periodoGraficoAtalhoPendente = null;
            let periodoGrafico = periodoInicial;
            let periodoSelecionadoPeloUsuario = false;
            let lojaSelecionada = '__todas';
            const unidadeNegocioSelect = { value: '__todos' };
            const data_inicio_param = '';
            const data_fim_param = '';
            const periodoPrefSalvo = preferenciaInicial;
            let datasAtuais = {
                inicio: preferenciaInicial.inicio,
                fim: preferenciaInicial.fim
            };
            let preferenciaAtual = { ...preferenciaInicial };
            const preferenciasSalvas = [];
            const getDataIniISO = () => datasAtuais.inicio;
            const getDataFimISO = () => datasAtuais.fim;
            const obterLimitesComVendasServidor = async () => limitesServidor;
            const obterLimitesComVendas = () => null;
            const sincronizarPeriodoTopo = (inicio, fim) => {
                datasAtuais = { inicio, fim };
            };
            const atualizarPeriodoTexto = () => {};
            const carregarPreferenciaPeriodoData = () => preferenciaAtual;
            const salvarPreferenciaPeriodoData = (preferencia = {}) => {
                preferenciasSalvas.push(preferencia);
                const origem = preferencia.origem === 'atalho' ? 'atalho' : 'manual';
                preferenciaAtual = {
                    inicio: datasAtuais.inicio,
                    fim: datasAtuais.fim,
                    origem,
                    periodo: origem === 'atalho' ? String(preferencia.periodo || '') : ''
                };
            };
            const salvarPreferenciaGrafico = () => {};
            const aplicarPreferenciaGraficoUI = () => {};
            const marcarPeriodoGraficoManual = () => {
                periodoGrafico = 'manual';
                salvarPreferenciaGrafico();
                aplicarPreferenciaGraficoUI();
            };
            const atualizarPeriodoComRecarregamento = () => {
                throw new Error('a normalização inicial não deve recarregar duas vezes');
            };
            ${aplicarPeriodoSource}
            ${normalizarPeriodoSource}
            return {
                normalizar: () => normalizarPeriodoGraficoRestaurado(),
                async aplicarAtalho(periodo) {
                    periodoGrafico = periodo;
                    periodoGraficoAtalhoPendente = periodo;
                    try {
                        return await aplicarPeriodoGraficoMesesCompletos(periodo, {
                            hoje: new Date(2026, 7, 19, 12, 0, 0),
                            recarregar: false
                        });
                    } finally {
                        periodoGraficoAtalhoPendente = null;
                    }
                },
                iniciarAtalhoPendente(periodo) {
                    periodoGrafico = periodo;
                    periodoGraficoAtalhoPendente = periodo;
                },
                obterDatas: () => datasAtuais,
                obterPreferencias: () => preferenciasSalvas,
                obterPeriodo: () => periodoGrafico
            };
        `
    );

    const legado = criarHarness(
        { JKVendasPeriodosCompletos: helpers },
        DataFixa,
        { inicio: '2026-02-20', fim: '2026-08-19', origem: '', periodo: '' },
        '6m',
        null
    );
    assert.strictEqual(await legado.normalizar(), true);
    assert.deepStrictEqual(legado.obterDatas(), { inicio: '2026-02-01', fim: '2026-08-19' });
    assert.deepStrictEqual(legado.obterPreferencias(), [{ origem: 'atalho', periodo: '6m' }]);

    const manual = criarHarness(
        { JKVendasPeriodosCompletos: helpers },
        DataFixa,
        { inicio: '2026-02-20', fim: '2026-08-19', origem: 'manual', periodo: '' },
        'max',
        null
    );
    assert.strictEqual(await manual.normalizar(), false);
    assert.strictEqual(manual.obterPeriodo(), 'manual');
    assert.deepStrictEqual(manual.obterDatas(), { inicio: '2026-02-20', fim: '2026-08-19' });
    assert.deepStrictEqual(manual.obterPreferencias(), []);

    const legadoManual = criarHarness(
        { JKVendasPeriodosCompletos: helpers },
        DataFixa,
        { inicio: '2026-04-15', fim: '2026-07-10', origem: '', periodo: '' },
        '6m',
        null
    );
    assert.strictEqual(await legadoManual.normalizar(), false);
    assert.strictEqual(legadoManual.obterPeriodo(), 'manual');
    assert.deepStrictEqual(legadoManual.obterDatas(), { inicio: '2026-04-15', fim: '2026-07-10' });
    assert.deepStrictEqual(legadoManual.obterPreferencias(), [{}]);

    const maxVersionado = criarHarness(
        { JKVendasPeriodosCompletos: helpers },
        DataFixa,
        { inicio: '2021-01-01', fim: '2026-06-30', origem: 'atalho', periodo: 'max' },
        'max',
        { inicio: '2020-04-12', fim: '2026-08-19' }
    );
    assert.strictEqual(await maxVersionado.normalizar(), true);
    assert.strictEqual(maxVersionado.obterPeriodo(), 'max');
    assert.deepStrictEqual(maxVersionado.obterDatas(), { inicio: '2020-04-01', fim: '2026-07-31' });
    assert.deepStrictEqual(maxVersionado.obterPreferencias(), [{ origem: 'atalho', periodo: 'max' }]);

    const maxPendenteSobre6m = criarHarness(
        { JKVendasPeriodosCompletos: helpers },
        DataFixa,
        { inicio: '2026-02-01', fim: '2026-07-31', origem: 'atalho', periodo: '6m' },
        'max',
        null
    );
    assert.strictEqual(await maxPendenteSobre6m.normalizar(), true);
    assert.strictEqual(maxPendenteSobre6m.obterPeriodo(), '6m');
    assert.deepStrictEqual(maxPendenteSobre6m.obterDatas(), { inicio: '2026-02-01', fim: '2026-08-19' });
    assert.deepStrictEqual(maxPendenteSobre6m.obterPreferencias(), [{ origem: 'atalho', periodo: '6m' }]);

    const interacaoAntesDaNormalizacao = criarHarness(
        { JKVendasPeriodosCompletos: helpers },
        DataFixa,
        { inicio: '2026-05-01', fim: '2026-07-31', origem: 'manual', periodo: '' },
        'manual',
        null
    );
    assert.strictEqual(await interacaoAntesDaNormalizacao.aplicarAtalho('3m'), true);
    assert.strictEqual(await interacaoAntesDaNormalizacao.normalizar(), true);
    assert.strictEqual(interacaoAntesDaNormalizacao.obterPeriodo(), '3m');
    assert.deepStrictEqual(interacaoAntesDaNormalizacao.obterDatas(), { inicio: '2026-05-01', fim: '2026-08-19' });

    const maxAindaPendente = criarHarness(
        { JKVendasPeriodosCompletos: helpers },
        DataFixa,
        { inicio: '2026-04-15', fim: '2026-07-10', origem: 'manual', periodo: '' },
        'manual',
        null
    );
    maxAindaPendente.iniciarAtalhoPendente('max');
    assert.strictEqual(await maxAindaPendente.normalizar(), false);
    assert.strictEqual(maxAindaPendente.obterPeriodo(), 'max');
    assert.deepStrictEqual(maxAindaPendente.obterDatas(), { inicio: '2026-04-15', fim: '2026-07-10' });
}

async function testarAplicacaoPeriodoSku() {
    const DataFixa = class extends Date {
        constructor(...args) {
            if (args.length) {
                super(...args);
            } else {
                super(2026, 7, 19, 12, 0, 0);
            }
        }
    };

    function criarCenario() {
        let resolverLimites;
        const limitesPendentes = new Promise(resolve => {
            resolverLimites = resolve;
        });
        const periodosAplicados = [];
        const periodosUrl = [];
        const periodosConfirmadosNaUi = [];
        const criarHarness = new Function(
            'window',
            'Date',
            'limitesPendentes',
            'registrarPeriodo',
            'registrarUrl',
            'registrarUi',
            `
                let periodoGraficoSelecaoToken = 0;
                let periodoGraficoAtalhoPendente = null;
                let periodoGrafico = '3m';
                let lojaAlvo = 'Loja A';
                const unidadeNegocioSelect = { value: '__todos' };
                let datasAtuais = { inicio: '2026-05-20', fim: '2026-08-19' };
                const getDataIniISO = () => datasAtuais.inicio;
                const getDataFimISO = () => datasAtuais.fim;
                const obterAssinaturaContextoPeriodoGraficoSku = () => JSON.stringify([
                    String(lojaAlvo || ''),
                    String(unidadeNegocioSelect.value || ''),
                    getDataIniISO(),
                    getDataFimISO()
                ]);
                const obterLimitesComVendasServidorSku = () => limitesPendentes;
                const setDataRangeISO = (inicio, fim) => {
                    datasAtuais = { inicio, fim };
                    registrarPeriodo(inicio, fim);
                };
                const atualizarPeriodoTexto = () => {};
                const atualizarUrlPeriodoGraficoSku = registrarUrl;
                const salvarPreferenciaGraficoSku = () => {};
                const aplicarPreferenciaGraficoUI = () => registrarUi(periodoGrafico);
                ${skuAplicarPeriodoSource}
                return {
                    selecionar(periodo) {
                        periodoGrafico = periodo;
                        return aplicarPeriodoGraficoMesesCompletosSku(periodo, {
                            hoje: new Date(2026, 7, 19, 12, 0, 0)
                        });
                    },
                    definirUnidade(unidade) {
                        unidadeNegocioSelect.value = unidade;
                    },
                    reaplicar: () => reaplicarPeriodoGraficoAposMudancaFiltroSku()
                };
            `
        );
        return {
            harness: criarHarness(
                { JKVendasPeriodosCompletos: helpers },
                DataFixa,
                limitesPendentes,
                (inicio, fim) => periodosAplicados.push({ inicio, fim }),
                periodo => periodosUrl.push(periodo),
                periodo => periodosConfirmadosNaUi.push(periodo)
            ),
            resolverLimites,
            periodosAplicados,
            periodosUrl,
            periodosConfirmadosNaUi
        };
    }

    const fixo = criarCenario();
    assert.strictEqual(await fixo.harness.selecionar('6m'), true);
    assert.deepStrictEqual(fixo.periodosAplicados, [{ inicio: '2026-02-01', fim: '2026-08-19' }]);
    assert.deepStrictEqual(fixo.periodosUrl, ['6m']);
    assert.deepStrictEqual(fixo.periodosConfirmadosNaUi, ['6m']);

    const filtro = criarCenario();
    const maxAntesDoFiltro = filtro.harness.selecionar('max');
    filtro.harness.definirUnidade('Unidade Outlet');
    filtro.resolverLimites({ inicio: '2020-04-12', fim: '2026-08-19' });
    assert.strictEqual(await maxAntesDoFiltro, false, 'Max SKU tardio não pode usar limites de outro filtro');
    assert.deepStrictEqual(filtro.periodosAplicados, []);

    const filtroReaplicado = criarCenario();
    const maxDoContextoAnterior = filtroReaplicado.harness.selecionar('max');
    filtroReaplicado.harness.definirUnidade('Unidade Outlet');
    const maxDoContextoAtual = filtroReaplicado.harness.reaplicar();
    filtroReaplicado.resolverLimites({ inicio: '2020-04-12', fim: '2026-08-19' });
    assert.strictEqual(await maxDoContextoAnterior, false);
    assert.strictEqual(await maxDoContextoAtual, true);
    assert.deepStrictEqual(filtroReaplicado.periodosAplicados, [{ inicio: '2020-04-01', fim: '2026-07-31' }]);
    assert.deepStrictEqual(filtroReaplicado.periodosConfirmadosNaUi, ['max']);

    const maximo = criarCenario();
    const maxValido = maximo.harness.selecionar('max');
    maximo.resolverLimites({ inicio: '2020-04-12', fim: '2026-08-19' });
    assert.strictEqual(await maxValido, true);
    assert.deepStrictEqual(maximo.periodosAplicados, [{ inicio: '2020-04-01', fim: '2026-07-31' }]);
    assert.deepStrictEqual(maximo.periodosUrl, ['max']);
    assert.deepStrictEqual(maximo.periodosConfirmadosNaUi, ['max']);

    const indisponivel = criarCenario();
    const maxSemLimites = indisponivel.harness.selecionar('max');
    indisponivel.resolverLimites(null);
    assert.strictEqual(await maxSemLimites, null, 'Max SKU sem mês completo não pode fingir ser 3m');
    assert.deepStrictEqual(indisponivel.periodosAplicados, []);
}

Promise.resolve()
    .then(testarRestauracaoPreferenciaLegada)
    .then(testarCliqueConcorrenteAposMax)
    .then(testarAplicacaoPeriodoSku)
    .then(() => console.log('vendas_complete_month_periods: ok'))
    .catch(error => {
        console.error(error);
        process.exitCode = 1;
    });
