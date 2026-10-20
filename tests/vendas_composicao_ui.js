"use strict";

// Offline regression: real sales rendering, bridge and DOM; no application/API is started.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const read = name => fs.readFileSync(path.join(root, name), 'utf8');
const section = (source, start, end) => {
    const from = source.indexOf(start), to = source.indexOf(end, from + start.length);
    assert.ok(from >= 0 && to > from, `Missing source boundaries: ${start}`);
    return source.slice(from, to);
};
const plainHtml = name => read(name).replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '').replace(/<link\b[^>]*>/gi, '');
const css = name => read(name).replace(/@import\s+url\([^)]*\)\s*;/g, '');
const summaryRows = [
    { sku: '479', produto: 'Produto base', pedidos: 10, itens: 59, quantidade: 59, itensDiretos: 20, itensComposicao: 39, itensComponentes: 59, valor: 200, devolucoes: 0, itensDevolucao: 3, itensDevolucaoDiretos: 0, itensDevolucaoComposicao: 3, itensDevolucaoComponentes: 3, valorDevolucao: 0 },
    { sku: '479-K', produto: 'Kit 2', pedidos: 9, itens: 9, quantidade: 9, itensDiretos: 9, itensComposicao: 0, itensComponentes: 0, valor: 180, devolucoes: 0, itensDevolucao: 0, itensDevolucaoComponentes: 0, valorDevolucao: 0 },
    { sku: '479-K3', produto: 'Kit 3', pedidos: 2, itens: 2, quantidade: 2, itensDiretos: 2, itensComposicao: 0, itensComponentes: 0, valor: 60, devolucoes: 1, itensDevolucao: 1, itensDevolucaoComponentes: 0, valorDevolucao: 10 },
    { sku: '479-K5', produto: 'Kit 5', pedidos: 3, itens: 3, quantidade: 3, itensDiretos: 3, itensComposicao: 0, itensComponentes: 0, valor: 150, devolucoes: 0, itensDevolucao: 0, itensDevolucaoComponentes: 0, valorDevolucao: 0 },
].map(row => ({ ...row, __resumo: true, data: '2026-10-01', loja_conta: 'Loja de teste', unidade_negocio: 'Canal de teste' }));
const totals = { pedidos: 24, itens: 59, valor: 590, devolucoes: 1, itensDevolucao: 3, valorDevolucao: 10, itensLiquidos: 56, valorLiquido: 580 };

async function main() {
    assert.equal(read('vendas.html'), read('static/vendas.html'));
    assert.equal(read('vendas_sku.html'), read('static/vendas_sku.html'));
    const browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({ viewport: { width: 1300, height: 900 } });
    const errors = [];
    try {
        const page = await context.newPage();
        page.on('pageerror', error => errors.push(String(error)));
        await page.route('**/*', route => route.request().url() === 'http://vendas.fixture.test/'
            ? route.fulfill({ contentType: 'text/html', body: plainHtml('static/vendas.html') }) : route.abort());
        await page.goto('http://vendas.fixture.test/');
        await page.addStyleTag({ content: css('static/vendas/styles.css') + css('static/vendas/visual-novo.css') });
        await page.addScriptTag({ content: read('static/vendas/runtime.js') });
        await page.addScriptTag({ content: section(read('static/vendas/sync.js'), 'function formatCurrency(', 'function renderBotoesLojas(') });
        await page.addScriptTag({ content: section(read('static/vendas/assistente.js'), 'function obterChaveMesAssistenteVendas(', 'function obterHistoricoAssistenteVendas(') });
        await page.addScriptTag({ content: section(read('static/vendas/grafico.js'), 'function atualizarObservacoesGraficoVendas(', 'function aplicarFiltroTempoDoGrafico(') });
        await page.addScriptTag({ content: `
            function getDataIniISO() { return window.__fixtureStart || '2026-10-01'; }
            function getDataFimISO() { return '2026-10-31'; }
            function formatDate(value) { return value; }
            function formatarLabelDataGrafico(value) { return value; }
            function normalizarPrevisaoMesAtual() { return null; }
            let metricaGrafico = 'quantidade';
            function renderRankingSidebarVendas() {}
            function obterClientId() { return localStorage.getItem('fixture-client') || 'fixture-a'; }
            clientId = 'fixture-a';
            lojaSelecionada = '__todas';
            unidadeNegocioSelect.value = '__todos';
        ` });
        await page.addScriptTag({ content: read('static/vendas/visual-novo-bridge.js') });
        await page.evaluate(({rows, totals}) => {
            dados = rows; dadosFiltrados = rows; dadosModoResumo = true; resumoTotaisVendas = totals;
            vendasResumoMeta = { composicao: { incomplete: false } };
            renderResumoTotaisVendas(totals); renderTabelaAgrupada(rows);
            atualizarObservacoesGraficoVendas({ labels: ['2026-10-01'], quantidades_vendas: [59], quantidades_devolucoes: [3], valores_vendas: [590], valores_devolucoes: [10] });
        }, {rows: summaryRows, totals});
        await page.waitForFunction(() => document.querySelectorAll('#tBody tr').length === 4);
        assert.match(await page.locator('#tBody tr[data-sku="479"] td').nth(3).getAttribute('title'), /20 diretos \+ 39 por composição/);
        assert.equal(await page.locator('#tBody tr[data-sku="479"] td').nth(2).textContent(), '10');
        assert.equal(await page.locator('#tBody tr[data-sku="479-K"] td').nth(3).textContent(), '9');
        assert.match(await page.locator('#graficoObservacoesVendas').textContent(), /5,1%/);
        const analysis = await page.evaluate(() => obterResumoTelaVendas());
        assert.equal(analysis.analise_mensal.meses[0].quantidade_vendida, 59, 'A soma física não pode contar novamente os 14 kits');
        assert.equal(analysis.analise_mensal.meses[0].quantidade_devolvida, 3, 'A devolução física não pode somar o kit-pai');
        assert.equal(analysis.analise_mensal.meses[0].pedidos_total, 24);
        assert.equal(analysis.analise_mensal.meses[0].valor_vendido, 590);
        assert.equal(analysis.analise_mensal.meses[0].valor_devolvido, 10);
        assert.equal(analysis.skus_ranking.find(row => row.sku === '479').qtd_vendida, 59);
        assert.equal(analysis.skus_ranking.find(row => row.sku === '479-K').qtd_vendida, 9);
        assert.deepEqual(analysis.totais_periodo, totals);
        const multiMonth = await page.evaluate(() => {
            window.__fixtureStart = '2026-09-01';
            const result = obterResumoTelaVendas();
            delete window.__fixtureStart;
            return result;
        });
        assert.equal(multiMonth.analise_mensal, null, 'Um resumo de vários meses com componentes não pode virar uma venda/devolução no mês final');
        assert.deepEqual(multiMonth.totais_periodo, totals, 'Os totais exatos continuam disponíveis sem uma série mensal inventada');
        assert.match(analysis.criterio_quantidades, /somar suas linhas duplica/);
        assert.equal(await page.evaluate(() => rankingDevolTop5.find(row => row.sku === '479').ocorrencias), 0);
        assert.equal(await page.evaluate(() => JKVendasLegacyBridge.getSnapshot().rows[1].itensComponentes), 0);
        await page.addScriptTag({ content: read('static/vendas/visual-novo.js') });
        await page.evaluate(() => JKVendasNova.show());
        await page.locator('#vnTabProducts').click();
        await page.locator('#vnProductTableBody [data-vn-sku="479"]').click();
        assert.match(await page.locator('#vnProductDetail').textContent(), /20 vendidos diretamente \+ 39 por composição = 59 unidades/);
        assert.match(await page.locator('#vnProductDetail').textContent(), /Pedidos, ocorrências de devolução e valores permanecem no SKU vendido originalmente/);
        assert.equal(await page.locator('#vnOrders').textContent(), '24');
        assert.equal(await page.locator('#vnItems').textContent(), '56');
        await page.locator('[data-vn-close="vnProductDialog"]').click();
        await page.locator('#vnReturnsOnly').check();
        assert.equal(await page.locator('#vnProductTableBody [data-vn-sku="479"]').count(), 1, 'O filtro de devoluções inclui componentes com zero ocorrências próprias');
        await page.evaluate(() => {
            vendasResumoMeta.composicao.incomplete = true;
            renderResumoTotaisVendas(resumoTotaisVendas);
        });
        await page.waitForFunction(() => document.getElementById('vnCompositionWarning').textContent.includes('inválidas'));
        assert.equal(await page.locator('#composicaoAviso').getAttribute('hidden'), null);
        for (const code of ['legacy_composition_unverified', 'legacy_component_identity_unverified', 'legacy_catalog_unverified']) {
            await page.evaluate(code => {
                vendasResumoMeta.composicao.warnings = [{code,store_id:'internal-secret-store',sku:'internal-secret-sku'}];
                renderResumoTotaisVendas(resumoTotaisVendas);
            }, code);
            await page.waitForFunction(() => document.getElementById('vnCompositionWarning').textContent.includes('cadastro legado'));
            assert.match(await page.locator('#composicaoAviso').textContent(), /atualize o Cadastro da loja/);
            assert.doesNotMatch(await page.locator('#vnCompositionWarning').textContent(), /internal-secret/);
        }
        await page.evaluate(() => {
            const fraction = {sku:'FRAC',produto:'Componente fracionário',__resumo:true,data:'2026-10-01',pedidos:0,itens:0.125,quantidade:0.125,itensDiretos:0,itensComposicao:0.125,itensComponentes:0.125,valor:0,devolucoes:0,itensDevolucao:0.00000001,itensDevolucaoDiretos:0,itensDevolucaoComposicao:0.00000001,itensDevolucaoComponentes:0.00000001};
            dados = [fraction]; dadosFiltrados = dados;
            resumoTotaisVendas = {pedidos:0,itens:0.125,valor:0,devolucoes:0,itensDevolucao:0.00000001,valorDevolucao:0,itensLiquidos:0.12499999,valorLiquido:0};
            vendasResumoMeta.composicao = {incomplete:false};
            renderResumoTotaisVendas(resumoTotaisVendas); renderTabelaAgrupada(dados);
            atualizarObservacoesGraficoVendas({labels:['2026-10-01'],quantidades_vendas:[0.125],quantidades_devolucoes:[0.00000001]});
        });
        await page.waitForFunction(() => document.querySelector('#tBody tr[data-sku="FRAC"]'));
        assert.equal(await page.locator('#tBody tr[data-sku="FRAC"] td').nth(3).textContent(),'0,125');
        await page.locator('#vnProductTableBody [data-vn-sku="FRAC"]').click();
        assert.match(await page.locator('#vnProductDetail').textContent(), /0,125 por composição = 0,125 unidades/);
        assert.match(await page.locator('#vnProductDetail').textContent(), /0,00000001 por composição/);
        assert.equal(await page.locator('#vnItems').textContent(),'0,12499999');
        assert.match(await page.locator('#graficoObservacoesVendas').textContent(), /0,125 item/);
        assert.equal(await page.locator('#vnCompositionWarning').textContent(),'');
        const fractionalMonth = await page.evaluate(() => montarAnaliseMensalAssistenteVendas().meses[0]);
        assert.equal(fractionalMonth.quantidade_vendida,0.125);
        assert.equal(fractionalMonth.quantidade_devolvida,0.00000001);
        assert.equal(fractionalMonth.top_skus_vendidos[0].quantidade_vendida,0.125);
        await page.locator('[data-vn-close="vnProductDialog"]').click();
        await page.evaluate(() => {
            localStorage.setItem('fixture-client', 'fixture-b');
            JKVendasNova.refresh();
        });
        await page.waitForFunction(() => document.getElementById('vnProductTableBody').children.length === 0);
        assert.equal(await page.evaluate(() => JKVendasLegacyBridge.getSnapshot().compositionWarning), '', 'Avisos da loja/cliente anterior não vazam no snapshot');
        await page.evaluate(() => {
            dadosFiltrados = [{sku:'antigo', data:'2026-10-01', quantidade:2, valor:7}];
            dadosModoResumo = false; devolucaoItens = [];
        });
        const legacy = await page.evaluate(() => montarAnaliseMensalAssistenteVendas().meses[0]);
        assert.equal(legacy.quantidade_vendida, 2); assert.equal(legacy.pedidos_total, 1); assert.equal(legacy.valor_vendido, 7);

        const detail = await context.newPage();
        detail.on('pageerror', error => errors.push(String(error)));
        await detail.route('**/*', route => route.request().url() === 'http://sku.fixture.test/'
            ? route.fulfill({ contentType: 'text/html', body: plainHtml('static/vendas_sku.html') }) : route.abort());
        await detail.goto('http://sku.fixture.test/');
        await detail.addScriptTag({ content: `
            const resumo = document.getElementById('resumo'), tHead = document.getElementById('tHead'), tBody = document.getElementById('tBody'), statusEl = document.getElementById('status');
            function formatCurrency(value) { return Number(value || 0).toLocaleString('pt-BR', {style:'currency',currency:'BRL'}); }
        ` + section(read('static/vendas_sku.html'), '    function vendaEhEbazarSomatorio(', '    function obterClientId(') });
        const direct = Array.from({length: 10}, (_, i) => ({data:'2026-10-01', numero:`P${i}`, numero_nf:`NF${i}`, quantidade:2, valor:20, pedidos_contribuicao:1}));
        const derived = [['479-K',9,2],['479-K3',2,3],['479-K5',3,5]].map(([sku,n,f]) => ({data:'2026-10-01',numero:`P-${sku}`,numero_nf:`NF-${sku}`, __composicao:true, sku_original:sku, quantidade_original:n, fator_composicao:f, quantidade:n*f, valor:0, pedidos_contribuicao:0}));
        const returns = [{__composicao:true,sku_original:'479-K3',quantidade_original:1,fator_composicao:3,quantidade:3,valor_total:0,devolucoes_contribuicao:0}];
        await detail.evaluate(({sales,returns}) => {renderResumo(sales,returns);renderTabela(sales,returns);}, {sales:[...direct,...derived],returns});
        const cards = await detail.locator('#resumo .card').evaluateAll(cards => Object.fromEntries(cards.map(card => [card.querySelector('h3').textContent,card.querySelector('.val').textContent])));
        assert.equal(cards['Pedidos Vendidos'], '10'); assert.equal(cards['Itens Vendidos'], '59');
        assert.equal(cards['Valor Bruto'].replace(/\s/g,''), 'R$200,00');
        assert.equal(cards.Devolucoes,'0'); assert.equal(cards['Itens Devolvidos'],'3');
        const derivedRow = detail.locator('#tBody tr').nth(10);
        assert.equal(await derivedRow.locator('td').nth(1).textContent(),'P-479-K');
        assert.equal(await derivedRow.locator('td').nth(2).textContent(),'NF-479-K');
        assert.equal(await derivedRow.locator('td').nth(4).textContent(),'18');
        assert.match(await derivedRow.locator('td').nth(6).textContent(), /Componente de 479-K \(9 × 2\)/);
        assert.match(await detail.locator('#composicaoResumo').textContent(), /quantidades incluem componentes/);
        await detail.evaluate(() => {
            const fraction = {__composicao:true,sku_original:'FRAC-K',quantidade_original:1,fator_composicao:0.125,quantidade:0.125,valor:0,pedidos_contribuicao:0};
            renderResumo([fraction],[]);renderTabela([fraction],[]);
        });
        assert.equal(await detail.locator('#resumo .card .val').nth(1).textContent(),'0,125');
        assert.equal(await detail.locator('#tBody tr td').nth(4).textContent(),'0,125');
        assert.match(await detail.locator('#tBody tr td').nth(6).textContent(), /1 × 0,125/);
        await detail.evaluate(() => {renderResumo([{quantidade:2,valor:10}],[]);renderTabela([{__composicao:true,sku_original:'<img src=x onerror="window.injected=true">',quantidade_original:1,fator_composicao:2,quantidade:2}],[]);});
        assert.equal(await detail.locator('#composicaoResumo').getAttribute('hidden'),'');
        assert.equal(await detail.locator('#resumo .card .val').first().textContent(),'1');
        assert.equal(await detail.locator('#tBody img').count(),0, 'O SKU de origem é texto e não HTML executável');
        assert.equal(await detail.evaluate(() => Boolean(window.injected)),false);
        assert.deepEqual(errors, []);
        console.log('vendas_composicao_ui: OK (totais físicos, origem, devoluções, isolamento e fallback)');
    } finally {
        await context.close(); await browser.close();
    }
}
main().catch(error => {console.error(error);process.exitCode=1;});
