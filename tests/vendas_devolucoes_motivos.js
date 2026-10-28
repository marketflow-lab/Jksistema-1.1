"use strict";

// Real report HTML/CSS/JS, synthetic API responses, no network or personal data.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const { fixture, origin, range, query, storeA, storeB, listReady, itemCalls, until } = require('./vendas_devolucoes_store_filter');
const identified = (descricao, codigo='DAMAGED', extra={}) => ({status:'identified',descricao,codigo,fonte:'mercado_livre',status_coleta:'done',...extra});
const record = (sku, numero, quantidade, valor, motivo, extra={}) => ({
    sku, descricao:'Produto '+sku, numero_nota:'NF-'+numero, data_emissao:'2026-10-05',
    loja_conta:storeA, unidade_negocio:'Canal A', unidade_negocio_virtual:'Canal A',
    quantidade, valor_total:valor, valor_unitario:valor/quantidade,
    fornecedor:'Comprador de teste '+numero, motivo_devolucao:motivo, ...extra,
});
const reportItems = [
    record('200',1,2,120,identified('Produto com defeito')),
    record('200',2,1,60,identified('Produto com defeito','DAMAGED',{status_coleta:'retry'})),
    record('200',3,3,180,identified('Desistência da compra','REGRET')),
    record('501',4,2,140,{status:'not_identified',descricao:'',codigo:'',fonte:'',status_coleta:'pending'}),
    record('999',5,1,999,identified('Motivo exclusivo da loja B','OTHER'),{loja_conta:storeB,unidade_negocio:'Canal B',unidade_negocio_virtual:'Canal B'}),
];
const numeric = text => Number(String(text).replace(/[^\d,.-]/g,'').replace(/\./g,'').replace(',','.'));
const openReport = async (f, filters={}) => {
    await f.page.goto(origin+'/devolucoes.html?'+query({...range,loja:storeA,...filters}));
    await f.page.waitForFunction(()=>document.getElementById('statusBar').style.display==='none' && Boolean(window.JKDevolucoesRelatorio));
};
async function reportRows(locator) {
    return locator.locator('table').first().evaluate(table=>{
        const normalize=value=>value.toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g,'');
        const headings=Array.from(table.querySelectorAll('thead th'),node=>normalize(node.textContent));
        const indexes={motivo:headings.findIndex(value=>value.includes('motivo')),quantidade:headings.findIndex(value=>value.includes('devolu')),percentual:headings.findIndex(value=>/particip|percent|%/.test(value)),valor:headings.findIndex(value=>value.includes('valor'))};
        return Array.from(table.querySelectorAll('tbody tr')).map(row=>{
            const cells=Array.from(row.querySelectorAll('td'),node=>node.textContent.trim());
            return Object.fromEntries(Object.entries(indexes).map(([key,index])=>[key,cells[index]||'']));
        });
    });
}
function findReason(rows,label) {
    const found=rows.find(row=>row.motivo.includes(label));
    assert.ok(found,'Motivo ausente: '+label+'; linhas: '+JSON.stringify(rows));
    return found;
}
async function assertNoPageOverflow(page) {
    const dimensions=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}));
    assert.ok(dimensions.scroll<=dimensions.width+1,'A página não deve rolar horizontalmente; apenas a tabela pode rolar');
}
async function verifyTotalsAndSku(browser) {
    const f=await fixture(browser,{items:reportItems});
    try {
        await openReport(f);
        await listReady(f.page,'200');
        assert.equal(await f.page.locator('#totalSkus').textContent(),'2');
        assert.equal(await f.page.locator('#totalDevolucoes').textContent(),'4');
        assert.equal(await f.page.locator('#totalItens').textContent(),'8');
        assert.equal(numeric(await f.page.locator('#valorTotal').textContent()),500);
        assert.match(await f.page.locator('#coberturaMotivos').textContent(),/3\s*(?:de|\/)\s*4/);
        const rows=await reportRows(f.page.locator('#relatorioMotivos'));
        const damaged=findReason(rows,'Produto com defeito'), regret=findReason(rows,'Desistência da compra');
        assert.equal(numeric(damaged.quantidade),2);
        assert.equal(numeric(damaged.percentual),50);
        assert.equal(numeric(damaged.valor),180);
        assert.equal(numeric(regret.quantidade),1);
        assert.equal(numeric(regret.percentual),25);
        assert.equal(numeric(regret.valor),180);
        assert.equal(rows.reduce((sum,row)=>sum+numeric(row.quantidade),0),4);
        assert.equal(rows.reduce((sum,row)=>sum+numeric(row.valor),0),500);
        assert.equal(rows.reduce((sum,row)=>sum+numeric(row.percentual),0),100);
        assert.doesNotMatch(await f.page.locator('#relatorioMotivos').textContent(),/exclusivo da loja B/);
        assert.doesNotMatch(await f.page.locator('#tabelaCorpo .group-row').allTextContents().then(text=>text.join(' ')),/Comprador de teste/);
        const general=await f.page.locator('#relatorioMotivos').textContent();
        const calls=itemCalls(f).length;
        await f.page.locator('#buscaSku').fill('200');
        assert.equal(await f.page.locator('#tabelaCorpo .group-row').count(),1);
        assert.equal(await f.page.locator('#relatorioMotivos').textContent(),general,'Buscar SKU não deve alterar o relatório geral');
        assert.equal(await f.page.locator('#totalDevolucoes').textContent(),'4');
        await f.page.locator('#expand-0').click();
        const detail=f.page.locator('#details-0');
        await detail.waitFor({state:'visible'});
        const skuReasons=await reportRows(detail.locator('.sku-reasons'));
        assert.equal(skuReasons.reduce((sum,row)=>sum+numeric(row.quantidade),0),3);
        assert.equal(skuReasons.reduce((sum,row)=>sum+numeric(row.valor),0),360);
        assert.ok(Math.abs(numeric(findReason(skuReasons,'Produto com defeito').percentual)-66.7)<0.11);
        assert.equal(await detail.locator('.registros-devolucao').getAttribute('open'),null);
        await detail.locator('.registros-devolucao summary').click();
        await detail.getByText('Comprador de teste 1',{exact:true}).waitFor({state:'visible'});
        assert.equal(itemCalls(f).length,calls,'Busca e expansão usam a mesma consulta filtrada');
        await f.page.locator('#expand-0').click();
        await detail.waitFor({state:'hidden'});
        await f.page.locator('#buscaSku').fill('inexistente');
        assert.equal(await f.page.locator('#tabelaCorpo .group-row').count(),0);
        assert.equal(await f.page.locator('#relatorioMotivos').textContent(),general);
        await f.page.locator('#buscaSku').fill('');
        await f.page.locator('#lojaSelect').selectOption(storeB);
        await listReady(f.page,'999');
        assert.equal(await f.page.locator('#totalDevolucoes').textContent(),'1');
        assert.match(await f.page.locator('#relatorioMotivos').textContent(),/exclusivo da loja B/);
        assert.doesNotMatch(await f.page.locator('#relatorioMotivos').textContent(),/Produto com defeito|Desistência/);
        assert.deepEqual(f.errors,[]);
    } finally {await f.close();}
}
async function verifyBlankSkusExcluded(browser) {
    const invalidReason = 'Motivo exclusivo de SKU inválido';
    const records = [
        record('VALIDO',1,2,42,identified('Produto com defeito')),
        ...['   ', '', null].map((sku,index)=>record(sku,index+2,3,900,identified(invalidReason))),
    ];
    const f=await fixture(browser,{items:records});
    try {
        await openReport(f);
        await listReady(f.page,'VALIDO');
        assert.equal(await f.page.locator('#totalSkus').textContent(),'1');
        assert.equal(await f.page.locator('#totalDevolucoes').textContent(),'1');
        assert.equal(await f.page.locator('#totalItens').textContent(),'2');
        assert.equal(numeric(await f.page.locator('#valorTotal').textContent()),42);
        assert.equal(await f.page.locator('#tabelaCorpo .group-row').count(),1);
        const rows=await reportRows(f.page.locator('#relatorioMotivos'));
        assert.equal(rows.reduce((sum,row)=>sum+numeric(row.quantidade),0),1);
        assert.equal(rows.reduce((sum,row)=>sum+numeric(row.valor),0),42);
        assert.doesNotMatch(await f.page.locator('#relatorioMotivos').textContent(),/Motivo exclusivo de SKU inválido/);
        await f.page.locator('#expand-0').click();
        const skuRows=await reportRows(f.page.locator('#details-0 .sku-reasons'));
        assert.equal(skuRows.reduce((sum,row)=>sum+numeric(row.quantidade),0),rows.reduce((sum,row)=>sum+numeric(row.quantidade),0));
        assert.equal(skuRows.reduce((sum,row)=>sum+numeric(row.valor),0),rows.reduce((sum,row)=>sum+numeric(row.valor),0));
        assert.deepEqual(f.errors,[]);
    } finally {await f.close();}
}
async function verifyUnknownAndCodeOnly(browser) {
    const states=['not_identified','not_informed','not_linked','ambiguous','unavailable','reconnect_required'];
    const records=states.map((status,index)=>record('PENDENTE-'+index,index+1,1,10,{status,descricao:'',codigo:'',fonte:'',status_coleta:status==='not_identified'?'pending':'done'}));
    records.push(record('LEGADO',20,1,10,undefined));
    records.push(record('CODIGO',21,1,10,identified('','SIZE_ISSUE')));
    const f=await fixture(browser,{items:records});
    try {
        await openReport(f);
        await listReady(f.page,'CODIGO');
        assert.equal(await f.page.locator('#totalDevolucoes').textContent(),'8');
        assert.match(await f.page.locator('#coberturaMotivos').textContent(),/1\s*(?:de|\/)\s*8/);
        const rows=await reportRows(f.page.locator('#relatorioMotivos'));
        assert.equal(rows.reduce((sum,row)=>sum+numeric(row.quantidade),0),8);
        assert.equal(rows.reduce((sum,row)=>sum+numeric(row.valor),0),80);
        assert.match(await f.page.locator('#relatorioMotivos').textContent(),/SIZE_ISSUE/,'Código explícito não deve ser descartado nem traduzido por suposição');
        assert.doesNotMatch(await f.page.locator('#relatorioMotivos').textContent(),/NaN|undefined|Produto com defeito/);
        assert.ok(rows.length>2,'Estados sem motivo devem conservar as distinções de disponibilidade');
        assert.deepEqual(f.errors,[]);
    } finally {await f.close();}
}
async function verifyCompactAndResponsive(browser) {
    const records=Array.from({length:75},(_,index)=>record('200',index+1,1,100,identified(index<50?'Produto com defeito':'Desistência da compra',index<50?'DAMAGED':'REGRET'),{descricao:'Display LCD para velocímetro e painel Titanium',fornecedor:'Comprador sintético com nome bastante longo '+index}));
    records.push(record('405',80,2,230,identified('Produto incompatível','FITMENT'),{descricao:'Kit de manutenção para transmissão'}));
    records.push(record('0012',81,1,79,{status:'not_informed',descricao:'',codigo:'',fonte:'bling',status_coleta:'done'},{descricao:'Sensor de velocidade universal'}));
    const f=await fixture(browser,{items:records});
    try {
        await openReport(f);
        await listReady(f.page,'200');
        const height=await f.page.locator('#tabelaCorpo .group-row').first().evaluate(row=>row.getBoundingClientRect().height);
        assert.ok(height<=90,'75 compradores não podem inflar a linha de SKU: '+height+'px');
        assert.doesNotMatch(await f.page.locator('#tabelaCorpo .group-row').first().textContent(),/Comprador sintético/);
        await assertNoPageOverflow(f.page);
        const folder=process.env.JK_DEVOLUCOES_ARTIFACTS_DIR;
        if(folder){fs.mkdirSync(path.resolve(folder),{recursive:true});await f.page.screenshot({path:path.join(path.resolve(folder),'devolucoes-motivos-desktop.png'),fullPage:true});}
        await f.page.locator('#expand-0').click();
        await f.page.locator('#details-0').waitFor({state:'visible'});
        if(folder)await f.page.screenshot({path:path.join(path.resolve(folder),'devolucoes-motivos-sku.png'),fullPage:true});
        await f.page.locator('#expand-0').click();
        await f.page.setViewportSize({width:390,height:844});
        await assertNoPageOverflow(f.page);
        for(const id of ['lojaSelect','unidadeNegocioSelect','buscaSku']){
            assert.ok(await f.page.locator('#'+id).evaluate(node=>{const r=node.getBoundingClientRect();return r.left>=0 && r.right<=innerWidth;}),id+' deve caber na tela móvel');
        }
        if(folder)await f.page.screenshot({path:path.join(path.resolve(folder),'devolucoes-motivos-mobile.png'),fullPage:true});
        await f.page.setViewportSize({width:768,height:1024});
        await assertNoPageOverflow(f.page);
        assert.deepEqual(f.errors,[]);
    } finally {await f.close();}
}
async function verifyLiteralData(browser) {
    const injection='<img src=x onerror="window.__injected=true">';
    const sku='SKU-\"'+injection, description='Produto '+injection, buyer='Comprador '+injection, reason='Motivo '+injection;
    const f=await fixture(browser,{items:[record(sku,1,1,12,identified(reason),{descricao:description,fornecedor:buyer,numero_nota:'NF-'+injection})]});
    try {
        await openReport(f);
        await listReady(f.page,sku);
        assert.ok((await f.page.locator('#tabelaCorpo .group-row').textContent()).includes(description));
        assert.ok((await f.page.locator('#relatorioMotivos').textContent()).includes(reason));
        await f.page.locator('#expand-0').click();
        await f.page.locator('#details-0 .registros-devolucao summary').click();
        await f.page.getByText(buyer,{exact:true}).waitFor({state:'visible'});
        assert.equal(await f.page.locator('#tabelaCorpo img,#relatorioMotivos img').count(),0);
        assert.equal(await f.page.evaluate(()=>Boolean(window.__injected)),false);
        await f.page.goto(origin+'/devolucoes_sku.html?'+query({sku,...range,loja:storeA}));
        await f.page.getByText(buyer,{exact:true}).waitFor();
        assert.ok((await f.page.locator('#relatorioMotivos').textContent()).includes(reason));
        assert.equal(await f.page.locator('#tabelaCorpo img,#relatorioMotivos img').count(),0);
        assert.equal(await f.page.evaluate(()=>Boolean(window.__injected)),false);
        assert.deepEqual(f.errors,[]);
    } finally {await f.close();}
}
async function verifyDetailAndComponents(browser) {
    const f=await fixture(browser,{items:reportItems});
    try {
        await f.page.goto(origin+'/devolucoes_sku.html?'+query({sku:'200',...range,loja:storeA}));
        await f.page.locator('#tabelaCorpo').getByText('NF-1',{exact:true}).waitFor();
        assert.equal(await f.page.locator('#totalDevol').textContent(),'3');
        assert.equal(await f.page.locator('#totalQtd').textContent(),'6');
        assert.equal(numeric(await f.page.locator('#valorTotal').textContent()),360);
        const rows=await reportRows(f.page.locator('#relatorioMotivos'));
        assert.equal(rows.reduce((sum,row)=>sum+numeric(row.quantidade),0),3);
        assert.match(await f.page.locator('#tabelaCorpo').textContent(),/Produto com defeito/);
        await f.page.setViewportSize({width:390,height:844});
        await assertNoPageOverflow(f.page);
        assert.deepEqual(f.errors,[]);
    } finally {await f.close();}
    const projected=await fixture(browser,{items:[record('COMPONENTE',1,6,0,identified('Produto com defeito'),{devolucoes_contribuicao:0,sku_origem:'KIT',composicao_origem:'componente'})]});
    try {
        await projected.page.goto(origin+'/devolucoes_sku.html?'+query({sku:'COMPONENTE',...range,loja:storeA}));
        await projected.page.locator('#tabelaCorpo').getByText('NF-1',{exact:true}).waitFor();
        assert.equal(await projected.page.locator('#totalDevol').textContent(),'0','Componente projetado não duplica a ocorrência do kit');
        assert.equal(await projected.page.locator('#totalQtd').textContent(),'6');
        assert.equal(numeric(await projected.page.locator('#valorTotal').textContent()),0);
        assert.match(await projected.page.locator('#tabelaCorpo').textContent(),/Produto com defeito/);
        assert.doesNotMatch(await projected.page.locator('#relatorioMotivos').textContent(),/NaN|Infinity/);
        assert.deepEqual(projected.errors,[]);
    } finally {await projected.close();}
}
async function verifyReportClearsAndRace(browser) {
    const f=await fixture(browser,{items:reportItems});
    try {
        await openReport(f);
        await listReady(f.page,'200');
        f.state.hold=true;
        await f.page.getByRole('button',{name:/Filtrar/}).click();
        await until(()=>f.held.length===1,'A consulta antiga não foi suspensa');
        await f.page.locator('#lojaSelect').selectOption(storeB);
        await until(()=>f.held.length===2,'A consulta da nova loja não foi suspensa');
        assert.doesNotMatch(await f.page.locator('#relatorioMotivos').textContent(),/Produto com defeito|Desistência/);
        await f.held[1].release();
        await listReady(f.page,'999');
        await f.held[0].release();
        assert.match(await f.page.locator('#relatorioMotivos').textContent(),/exclusivo da loja B/);
        assert.doesNotMatch(await f.page.locator('#relatorioMotivos').textContent(),/Produto com defeito|Desistência/);
        f.state.hold=false;f.state.emptyItems=true;
        await f.page.getByRole('button',{name:/Filtrar/}).click();
        await f.page.waitForFunction(()=>document.getElementById('totalDevolucoes').textContent==='0' && document.getElementById('statusBar').style.display==='none');
        assert.doesNotMatch(await f.page.locator('#relatorioMotivos').textContent(),/exclusivo da loja B/);
        assert.equal(await f.page.locator('#totalItens').textContent(),'0');
        f.state.emptyItems=false;f.state.itemStatus=503;
        await f.page.getByRole('button',{name:/Filtrar/}).click();
        await f.page.waitForFunction(()=>document.getElementById('statusBar').textContent.includes('HTTP 503'));
        assert.doesNotMatch(await f.page.locator('#relatorioMotivos').textContent(),/exclusivo da loja B|Produto com defeito/);
        assert.equal(await f.page.locator('#totalDevolucoes').textContent(),'0');
        assert.deepEqual(f.errors,[]);
    } finally {await f.close();}
}
async function main(){
    const browser=await chromium.launch({headless:true});
    try {
        await verifyTotalsAndSku(browser);
        await verifyBlankSkusExcluded(browser);
        await verifyUnknownAndCodeOnly(browser);
        await verifyCompactAndResponsive(browser);
        await verifyLiteralData(browser);
        await verifyDetailAndComponents(browser);
        await verifyReportClearsAndRace(browser);
        console.log('vendas_devolucoes_motivos: OK (general/SKU reconciliation, unknown reasons, compact/mobile layout, literal data, projections, loading and races)');
    } finally {await browser.close();}
}
if(require.main===module)main().catch(error=>{console.error(error);process.exitCode=1;});
module.exports={reportItems,record,identified};
