'use strict';

const assert = require('assert');
const { chromium } = require('playwright');
const { fixture,openList,field,plan,calls,release,serverList,waitServer,clean } = require('./helpers/medias_autosalvamento_fixture');

async function quantityConfirmations(browser) {
    const test=await fixture(browser);const {page}=test;
    try {
        for(const [value,confirmation] of [[7,'Enter'],[8,'Tab'],[9,'blur'],[0,'Enter']]){
            const quantity=field(page,'qtd');await quantity.fill(String(value));
            if(confirmation==='blur')await page.locator('#nomeListaPedidoEdit').click();else await quantity.press(confirmation);
            await waitServer(page,value=>__autosaveFixture.lists['list-a'].itens[0].Quantidade===value,value);
        }
        assert.strictEqual((await serverList(page)).itens[0]['Valor total'],0);
        await openList(page,'B');
        await openList(page,'A');
        assert.strictEqual(await field(page,'qtd').inputValue(),'0','Confirmed zero must survive reopening the list');
        assert.strictEqual((await calls(page,'save:list-a')).length,4,'Each settled confirmation must persist once, without duplicate Enter/change/blur saves');
    } finally {await clean(test);}
}

async function editableFieldsAndValidation(browser) {
    const test=await fixture(browser);const {page}=test;
    try {
        for(const [kind,value,key,expected] of [['vun','1.234,56','Valor unidade',1234.56],['vun','0','Valor unidade',0],['cbm','0,125','Estimed CBM','0,125'],['peso','2,500','Estimed Weigh','2,500'],['embalagem','Caixa reforçada','Individual packaging','Caixa reforçada']]){
            await field(page,kind).fill(value);await field(page,kind).press('Tab');
            await waitServer(page,({key,expected})=>__autosaveFixture.lists['list-a'].itens[0][key]===expected,{key,expected});
        }
        await page.locator('#nomeListaPedidoEdit').fill('');await page.locator('#nomeListaPedidoEdit').press('Enter');
        await page.waitForFunction(()=>/nome/i.test(document.querySelector('#statusListaPedido').textContent)&&!/sucesso/i.test(document.querySelector('#statusListaPedido').textContent));
        assert.strictEqual((await serverList(page)).nome_lista,'Pedido A');
        await page.locator('#nomeListaPedidoEdit').fill('Pedido A revisado');await page.locator('#nomeListaPedidoEdit').press('Enter');
        await waitServer(page,()=>__autosaveFixture.lists['list-a'].nome_lista==='Pedido A revisado');
        await page.locator('#lojaListaPedidoEdit').selectOption('store-b');
        await waitServer(page,()=>__autosaveFixture.lists['list-a'].store_id==='store-b'&&__autosaveFixture.lists['list-a'].loja==='Loja B');
        await page.locator('#fornecedorListaPedidoEdit').selectOption('supplier-b');
        await waitServer(page,()=>__autosaveFixture.lists['list-a'].fornecedor_id==='supplier-b');
    } finally {await clean(test);}
}

async function numericBlanks(browser) {
    const test=await fixture(browser);const {page}=test;
    try {
        for(const value of ['','-1','1.5']){
            await field(page,'qtd').fill(value);
            await field(page,'qtd').press('Tab');
            await page.waitForFunction(()=>/quantidade/i.test(document.querySelector('#statusListaPedido').textContent)&&!/sucesso/i.test(document.querySelector('#statusListaPedido').textContent));
            assert.strictEqual((await calls(page,'save:list-a')).length,0,'Invalid quantity '+JSON.stringify(value)+' must not send a PUT');
            if(value===''){
                await page.locator('#btnSalvarListaPedido').click();
                assert.strictEqual((await calls(page,'save:list-a')).length,0,'Manual save must also reject the visible blank quantity');
                assert(!/sucesso/i.test(await page.locator('#statusListaPedido').innerText()));
            }
            const saved=(await serverList(page)).itens[0];
            assert.strictEqual(saved.Quantidade,2);
            assert.strictEqual(saved['Valor unidade'],2.5);
            assert.strictEqual(saved['Valor total'],5);
        }
        await field(page,'qtd').fill('0');await field(page,'qtd').press('Tab');
        await waitServer(page,()=>__autosaveFixture.lists['list-a'].itens[0].Quantidade===0);
        assert.strictEqual((await serverList(page)).itens[0]['Valor total'],0);
        await field(page,'vun',1).fill('');await field(page,'vun',1).press('Tab');
        await waitServer(page,()=>__autosaveFixture.lists['list-a'].itens[1]['Valor unidade']===0);
        const saved=(await serverList(page)).itens[1];
        assert.strictEqual(saved.Quantidade,3,'Blank cost must retain the confirmed quantity');
        assert.strictEqual(saved['Valor total'],0);
        assert.strictEqual((await calls(page,'save:list-a')).length,2,'Only corrected zero quantity and normalized zero cost may save');
    } finally {await clean(test);}
}
async function queuedSavePreservesUnconfirmedTyping(browser) {
    const test=await fixture(browser,{rows:200});const {page}=test;
    try {
        await plan(page,'save:list-a',{hold:true});
        await field(page,'qtd',0).fill('18');await field(page,'qtd',0).press('Enter');
        await page.waitForFunction(()=>__autosaveFixture.calls.some(call=>call.key==='save:list-a'&&!call.done));
        const first=(await calls(page,'save:list-a'))[0];
        await field(page,'qtd',1).fill('29');await field(page,'qtd',1).press('Enter');
        await field(page,'embalagem',2).fill('Rascunho ainda sem confirmar');
        await page.evaluate(()=>{window.__draftNode=document.querySelector('[data-kind="embalagem"][data-idx="2"]');const wrap=document.querySelector('.mini-table-wrap');wrap.style.maxWidth='600px';document.querySelector('#tblListaPedidoItens').style.minWidth='1400px';wrap.scrollLeft=180;window.scrollTo(0,1400);});
        const scroll=await page.evaluate(()=>({y:scrollY,x:document.querySelector('.mini-table-wrap').scrollLeft}));
        assert(scroll.y>0&&scroll.x>0,'Fixture must have actual document and horizontal scroll');
        assert.strictEqual((await calls(page,'save:list-a')).length,1,'Only one PUT for the list may be in flight');
        await release(page,first.index);
        await waitServer(page,()=>__autosaveFixture.lists['list-a'].itens[1].Quantidade===29);
        const persisted=await serverList(page);
        assert.strictEqual(persisted.itens[0].Quantidade,18);
        assert.strictEqual(persisted.itens[2]['Individual packaging'],'Box','Unconfirmed typing must stay out of the queued payload');
        assert.strictEqual(await page.evaluate(()=>document.activeElement===window.__draftNode&&window.__draftNode.isConnected),true,'Autosave must preserve the focused DOM node');
        assert.strictEqual(await field(page,'embalagem',2).inputValue(),'Rascunho ainda sem confirmar');
        assert.deepStrictEqual(await page.evaluate(()=>({y:scrollY,x:document.querySelector('.mini-table-wrap').scrollLeft})),scroll,'Response must preserve document and horizontal scroll');
        await field(page,'embalagem',2).press('Tab');
        await waitServer(page,()=>__autosaveFixture.lists['list-a'].itens[2]['Individual packaging']==='Rascunho ainda sem confirmar');
    } finally {await clean(test);}
}

async function lateResponseCannotReplaceAnotherListOrStore(browser) {
    const test=await fixture(browser);const {page}=test;
    try {
        await plan(page,'save:list-a',{hold:true});
        await field(page,'qtd').fill('31');await field(page,'qtd').press('Enter');
        await page.waitForFunction(()=>__autosaveFixture.calls.some(call=>call.key==='save:list-a'&&!call.done));
        const old=(await calls(page,'save:list-a'))[0];
        await page.locator('#lojaBotoes button').filter({hasText:'Loja B'}).click();
        await openList(page,'B');
        await field(page,'qtd').fill('42');await field(page,'qtd').press('Enter');
        await waitServer(page,()=>__autosaveFixture.lists['list-b'].itens[0].Quantidade===42);
        await release(page,old.index);
        await waitServer(page,()=>__autosaveFixture.lists['list-a'].itens[0].Quantidade===31);
        assert.strictEqual(await page.locator('#nomeListaPedidoEdit').inputValue(),'Pedido B');
        assert.strictEqual(await field(page,'qtd').inputValue(),'42');
        assert.strictEqual((await serverList(page,'list-a')).store_id,'store-a');
        assert.strictEqual((await serverList(page,'list-b')).store_id,'store-b');
    } finally {await clean(test);}
}

async function reopenSameListWaitsForPendingWrites(browser) {
    const test=await fixture(browser);const {page}=test;
    try {
        await plan(page,'save:list-a',{hold:true});
        await field(page,'qtd').fill('50');await field(page,'qtd').press('Enter');
        await page.waitForFunction(()=>__autosaveFixture.calls.some(call=>call.key==='save:list-a'&&!call.done));
        const old=(await calls(page,'save:list-a'))[0];
        await openList(page,'B');
        const previousReads=(await calls(page,'detail:list-a')).length;
        await page.locator('.lista-item').filter({hasText:'Pedido A'}).click();
        await page.waitForTimeout(100);
        assert.strictEqual(await page.locator('#editorListaPedido').evaluate(panel=>panel.inert),true,'Reopening must keep the editor inert until its pending write finishes');
        assert.strictEqual((await calls(page,'detail:list-a')).length,previousReads,'A reopening GET must wait for the pending PUT to avoid loading stale values');
        await release(page,old.index);
        await page.waitForFunction(()=>document.querySelector('#nomeListaPedidoEdit')?.value==='Pedido A'&&!document.querySelector('#editorListaPedido').inert);
        assert.strictEqual(await field(page,'qtd').inputValue(),'50');
        await field(page,'vun').fill('5');await field(page,'vun').press('Enter');
        await waitServer(page,()=>__autosaveFixture.lists['list-a'].itens[0]['Valor unidade']===5);
        assert.strictEqual((await serverList(page)).itens[0].Quantidade,50,'A later edit after reopening must retain the earlier confirmed quantity');
        assert.strictEqual((await serverList(page)).itens[0]['Valor total'],250);
    } finally {await clean(test);}
}
async function visibleErrorAndManualRetry(browser) {
    const test=await fixture(browser);const {page}=test;
    try {
        await plan(page,'save:list-a',{status:503,detail:'Falha sintética ao salvar'});
        await field(page,'qtd').fill('53');await field(page,'qtd').press('Enter');
        await page.waitForFunction(()=>document.querySelector('#statusListaPedido').textContent.includes('Falha sintética'));
        assert.strictEqual((await serverList(page)).itens[0].Quantidade,2);
        assert.strictEqual(await field(page,'qtd').inputValue(),'53');
        await page.locator('#btnSalvarListaPedido').click();
        await waitServer(page,()=>__autosaveFixture.lists['list-a'].itens[0].Quantidade===53);
        await page.waitForFunction(()=>/sucesso/i.test(document.querySelector('#statusListaPedido').textContent));
    } finally {await clean(test);}
}

async function delayedSkuKeepsNewerQuantity(browser) {
    const test=await fixture(browser);const {page}=test;
    try {
        await plan(page,'sku:NEW-01',{hold:true});
        await field(page,'sku').fill('NEW-01');await field(page,'sku').press('Tab');
        await page.waitForFunction(()=>__autosaveFixture.calls.some(call=>call.key==='sku:NEW-01'&&!call.done));
        const lookup=(await calls(page,'sku:NEW-01'))[0];
        await field(page,'qtd').fill('64');await field(page,'qtd').press('Enter');
        assert.strictEqual((await serverList(page)).itens[0].SKU,'SKU-A-01');
        await release(page,lookup.index);
        await waitServer(page,()=>__autosaveFixture.lists['list-a'].itens[0].SKU==='NEW-01'&&__autosaveFixture.lists['list-a'].itens[0].Quantidade===64);
        assert.strictEqual((await serverList(page)).itens[0].OEM,'OEM-NEW-01');
    } finally {await clean(test);}
}

async function badAndDuplicateSkuDoNotSave(browser) {
    const test=await fixture(browser);const {page}=test;
    try {
        await field(page,'sku').fill('SKU-A-02');await field(page,'sku').press('Tab');
        await page.locator('#modalAvisoSkuCentral.open').waitFor();
        assert.strictEqual((await calls(page,'save:list-a')).length,0);
        assert.strictEqual((await calls(page,'sku:SKU-A-02')).length,0);
        assert(!/sucesso/i.test(await page.locator('#statusListaPedido').innerText()),'Rejected duplicate must not report success');
        await page.locator('[data-jk-action="fechar-aviso"]').click();
        await field(page,'sku').fill('MISSING');await field(page,'sku').press('Tab');
        await page.locator('#modalAvisoSkuCentral.open').waitFor();
        assert.strictEqual((await calls(page,'save:list-a')).length,0);
        assert.strictEqual(await field(page,'sku').inputValue(),'SKU-A-01');
        assert.strictEqual((await serverList(page)).itens[0].SKU,'SKU-A-01');
        assert(!/sucesso/i.test(await page.locator('#statusListaPedido').innerText()),'Missing SKU must not report success');
        await page.locator('[data-jk-action="fechar-aviso"]').click();
        await field(page,'sku').fill('CORRECTED-01');await field(page,'sku').press('Tab');
        await waitServer(page,()=>__autosaveFixture.lists['list-a'].itens[0].SKU==='CORRECTED-01');
        assert.strictEqual((await calls(page,'save:list-a')).length,1,'Correcting a rejected SKU must produce exactly one save');
    } finally {await clean(test);}
}

async function skuResponseAfterSelectionChange(browser) {
    const test=await fixture(browser);const {page}=test;
    try {
        await plan(page,'sku:LATE-01',{hold:true});
        await field(page,'sku').fill('LATE-01');await field(page,'sku').press('Tab');
        await page.waitForFunction(()=>__autosaveFixture.calls.some(call=>call.key==='sku:LATE-01'&&!call.done));
        const lookup=(await calls(page,'sku:LATE-01'))[0];
        await openList(page,'B');await release(page,lookup.index);
        await page.waitForFunction(()=>__autosaveFixture.calls.some(call=>call.key==='sku:LATE-01'&&call.done));
        assert.strictEqual(await page.locator('#nomeListaPedidoEdit').inputValue(),'Pedido B');
        assert.strictEqual(await field(page,'sku').inputValue(),'SKU-B-01');
        assert.strictEqual((await serverList(page,'list-b')).itens[0].SKU,'SKU-B-01');
        assert.strictEqual((await calls(page,'save:list-b')).length,0);
    } finally {await clean(test);}
}

async function addSkuWaitsForPendingPut(browser) {
    const test=await fixture(browser);const {page}=test;
    try {
        await plan(page,'save:list-a',{hold:true});
        await field(page,'qtd').fill('75');await field(page,'qtd').press('Enter');
        await page.waitForFunction(()=>__autosaveFixture.calls.some(call=>call.key==='save:list-a'&&!call.done));
        const old=(await calls(page,'save:list-a'))[0];
        await page.locator('#btnAdicionarSkuPedido').click();
        await page.locator('#addSkuInput').fill('ADDED-01');await page.locator('#addSkuQtdInput').fill('4');await page.locator('#addSkuValorInput').fill('3,50');
        await page.locator('#btnConfirmarAdicionarSku').click();
        await page.waitForTimeout(100);
        assert.strictEqual((await calls(page,'add:list-a')).length,0,'POST add must not race the older full PUT');
        await release(page,old.index);
        await waitServer(page,()=>__autosaveFixture.lists['list-a'].itens.some(item=>item.SKU==='ADDED-01'));
        await page.waitForFunction(()=>!document.querySelector('#modalAdicionarSkuPedido').classList.contains('open'));
        const saved=await serverList(page);
        assert.strictEqual(saved.itens.find(item=>item.SKU==='SKU-A-01').Quantidade,75);
        assert.strictEqual(saved.itens.find(item=>item.SKU==='ADDED-01').Quantidade,4);
        await field(page,'qtd').fill('76');await field(page,'qtd').press('Enter');
        await waitServer(page,()=>__autosaveFixture.lists['list-a'].itens[0].Quantidade===76);
        assert((await serverList(page)).itens.some(item=>item.SKU==='ADDED-01'),'Later full saves must retain the added SKU');
    } finally {await clean(test);}
}

async function existingSkuModalUpdateSurvivesAutosave(browser) {
    const test=await fixture(browser);const {page}=test;
    try {
        const count=(await serverList(page)).itens.length;
        await page.locator('#btnAdicionarSkuPedido').click();
        await page.locator('#addSkuInput').fill('SKU-A-01');
        await page.locator('#addSkuQtdInput').fill('88');
        await page.locator('#addSkuValorInput').fill('6,75');
        await page.locator('#btnConfirmarAdicionarSku').click();
        await waitServer(page,()=>__autosaveFixture.lists['list-a'].itens[0].Quantidade===88);
        await page.waitForFunction(()=>!document.querySelector('#modalAdicionarSkuPedido').classList.contains('open'));
        assert.strictEqual((await serverList(page)).itens.length,count,'Reinclusion must update the existing item without duplication');
        assert.strictEqual((await serverList(page)).itens[0]['Valor unidade'],6.75);
        await field(page,'embalagem',1).fill('Embalagem após reinclusão');
        await field(page,'embalagem',1).press('Tab');
        await waitServer(page,()=>__autosaveFixture.lists['list-a'].itens[1]['Individual packaging']==='Embalagem após reinclusão');
        const saved=(await serverList(page)).itens[0];
        assert.strictEqual(saved.Quantidade,88,'A later autosave must retain the quantity returned by POST for the existing SKU');
        assert.strictEqual(saved['Valor unidade'],6.75,'A later autosave must retain the cost returned by POST for the existing SKU');
        assert.strictEqual(saved['Valor total'],594);
    } finally {await clean(test);}
}
async function addedSkuSurvivesDraftAndManualSave(browser) {
    const test=await fixture(browser);const {page}=test;
    try {
        const count=(await serverList(page)).itens.length;
        await plan(page,'add:list-a',{hold:true});
        await page.locator('#btnAdicionarSkuPedido').click();
        await page.locator('#addSkuInput').fill('DRAFT-ADDED-01');
        await page.locator('#addSkuQtdInput').fill('4');
        await page.locator('#addSkuValorInput').fill('3,50');
        await page.locator('#btnConfirmarAdicionarSku').click();
        await page.waitForFunction(()=>__autosaveFixture.calls.some(call=>call.key==='add:list-a'&&!call.done));
        const added=(await calls(page,'add:list-a'))[0];
        await page.locator('[data-jk-action="fechar-adicionar-sku"]').click();
        await field(page,'embalagem',1).fill('Rascunho durante inclusão');
        await field(page,'embalagem',1).evaluate(node=>{window.__postDraftNode=node;});
        await release(page,added.index);
        await waitServer(page,()=>__autosaveFixture.lists['list-a'].itens.some(item=>item.SKU==='DRAFT-ADDED-01'));
        await page.waitForFunction(()=>!document.querySelector('#btnConfirmarAdicionarSku').disabled);
        assert.strictEqual(await page.evaluate(()=>document.activeElement===window.__postDraftNode&&window.__postDraftNode.isConnected),true,'POST response must keep the unconfirmed focused draft node');
        assert.strictEqual(await field(page,'embalagem',1).inputValue(),'Rascunho durante inclusão');
        assert.strictEqual((await serverList(page)).itens[1]['Individual packaging'],'Box','POST must not implicitly confirm the newer draft');
        await page.locator('#btnSalvarListaPedido').click();
        await waitServer(page,()=>__autosaveFixture.lists['list-a'].itens[1]['Individual packaging']==='Rascunho durante inclusão');
        await page.waitForFunction(()=>/sucesso/i.test(document.querySelector('#statusListaPedido').textContent));
        const saved=await serverList(page);
        assert.strictEqual(saved.itens.length,count+1,'A full manual save must retain the newly added item even when its row was not rendered during typing');
        assert.strictEqual(saved.itens.find(item=>item.SKU==='DRAFT-ADDED-01')?.Quantidade,4);
        assert.strictEqual(saved.itens.find(item=>item.SKU==='DRAFT-ADDED-01')?.['Valor unidade'],3.5);
    } finally {await clean(test);}
}
async function deletionAndReordering(browser) {
    const test=await fixture(browser,{rows:4});const {page}=test;
    try {
        await field(page,'excluir-item',1).click();
        await waitServer(page,()=>__autosaveFixture.lists['list-a'].itens.length===3);
        assert(!(await serverList(page)).itens.some(item=>item.SKU==='SKU-A-02'));
        const rows=page.locator('#tblListaPedidoItens tbody tr[data-lista-pedido-row]');
        const lastSku=await rows.last().locator('[data-kind="sku"]').inputValue();
        await rows.last().locator('td').nth(2).dragTo(rows.first().locator('td').nth(2),{targetPosition:{x:5,y:2}});
        await waitServer(page,sku=>__autosaveFixture.lists['list-a'].itens[0].SKU===sku,lastSku);
    } finally {await clean(test);}
}


async function confirmedSameListCanRefreshWithoutSavingAgain(browser) {
    const test=await fixture(browser);const {page}=test;
    try {
        await field(page,'qtd').fill('51');await field(page,'qtd').press('Enter');
        await waitServer(page,()=>__autosaveFixture.lists['list-a'].itens[0].Quantidade===51);
        await page.waitForFunction(()=>document.querySelector('#statusListaPedido').textContent==='Lista salva com sucesso.');
        const reads=(await calls(page,'detail:list-a')).length;
        await openList(page,'A');
        assert((await calls(page,'detail:list-a')).length>reads,'Confirmed autosave must not permanently block the same list');
        assert.strictEqual(await field(page,'qtd').inputValue(),'51');
        assert.strictEqual((await calls(page,'save:list-a')).length,1,'Refresh must not create another PUT');
        await page.locator('#btnSalvarListaPedido').click();
        await page.waitForFunction(()=>document.querySelector('#statusListaPedido').textContent==='Lista salva com sucesso.'&&__autosaveFixture.calls.filter(call=>call.key==='save:list-a').length===2);
        assert.strictEqual(await page.locator('#editorListaPedido').evaluate(panel=>panel.inert),false,'Manual save must not wait for its own queue');
    } finally {await clean(test);}
}

async function cachedRefreshDoesNotSaveAndFreshBecomesEditingBase(browser) {
    const test=await fixture(browser);const {page}=test;
    try {
        const saved=await serverList(page),stale=JSON.parse(JSON.stringify(saved)),fresh=JSON.parse(JSON.stringify(saved));
        stale.itens[0]['Título do produto em inglês']='Cached product';
        fresh.itens[0]['Título do produto em inglês']='Fresh product';fresh.itens[0].OEM='FRESH-OEM';
        fresh.itens[1].Quantidade=77;fresh.itens[1]['Valor total']=192.5;
        const metadata=(version,old=false)=>({estado:'ready',stale:old,atualizando:old,versao:version,gerado_em:new Date().toISOString(),autoridade:'authority-a',politica_fotos:'photos-a',pendente_fontes:false,completo:true});
        await plan(page,'detail:list-a',{body:{lista:saved,_cache:{...metadata('saved'),completo:false}}});
        await plan(page,'detail:list-a',{body:{lista:stale,_cache:metadata('old',true)}});
        await plan(page,'detail:list-a',{body:{lista:fresh,_cache:metadata('new')}});
        await openList(page,'A');
        await field(page,'qtd').focus();
        await page.waitForFunction(()=>document.querySelector('#tblListaPedidoItens').textContent.includes('Fresh product'));
        assert.strictEqual((await calls(page,'save:list-a')).length,0,'saved/stale/fresh reads and renders cannot save');
        await field(page,'qtd').fill('61');await field(page,'qtd').press('Enter');
        await waitServer(page,()=>__autosaveFixture.lists['list-a'].itens[0].Quantidade===61);
        const persisted=await serverList(page);
        assert.strictEqual(persisted.itens[0].OEM,'FRESH-OEM','Autosave must retain the fresh catalog complement');
        assert.strictEqual(persisted.itens[1].Quantidade,77,'A refresh while another unchanged field is focused must update the full confirmed base');
        assert.strictEqual(persisted.itens[0]['Título do produto em inglês'],'Fresh product');
        assert.strictEqual((await calls(page,'save:list-a')).length,1);
    } finally {await clean(test);}
}

async function permissionsInvalidationCannotSubmitQueuedEdits(browser) {
    const test=await fixture(browser);const {page}=test;
    try {
        await plan(page,'save:list-a',{hold:true});
        await field(page,'qtd',0).fill('71');await field(page,'qtd',0).press('Enter');
        await page.waitForFunction(()=>__autosaveFixture.calls.some(call=>call.key==='save:list-a'&&!call.done));
        const first=(await calls(page,'save:list-a'))[0];
        await field(page,'qtd',1).fill('72');await field(page,'qtd',1).press('Enter');
        await field(page,'embalagem',2).fill('Unconfirmed focused draft');
        await page.evaluate(()=>{localStorage.setItem('permissions',JSON.stringify({medias_compras:false}));dispatchEvent(new StorageEvent('storage',{key:'permissions'}));});
        assert.strictEqual(await page.locator('[data-kind="qtd"]').count(),0);
        await release(page,first.index);
        await page.waitForTimeout(80);
        assert.strictEqual((await calls(page,'save:list-a')).length,1,'Permission loss must not start queued PUTs or save focused drafts');
        assert.strictEqual(await page.evaluate(()=>listaPedidoAtual),null,'Old completed PUT cannot restore the invalidated editor');
    } finally {await clean(test);}
}

(async()=>{
    const browser=await chromium.launch({headless:true});
    const tests=[confirmedSameListCanRefreshWithoutSavingAgain,cachedRefreshDoesNotSaveAndFreshBecomesEditingBase,permissionsInvalidationCannotSubmitQueuedEdits,quantityConfirmations,editableFieldsAndValidation,numericBlanks,queuedSavePreservesUnconfirmedTyping,lateResponseCannotReplaceAnotherListOrStore,reopenSameListWaitsForPendingWrites,visibleErrorAndManualRetry,delayedSkuKeepsNewerQuantity,badAndDuplicateSkuDoNotSave,skuResponseAfterSelectionChange,addSkuWaitsForPendingPut,existingSkuModalUpdateSurvivesAutosave,addedSkuSurvivesDraftAndManualSave,deletionAndReordering];
    const selection=process.env.JK_AUTOSAVE_TEST;
    try {for(const test of tests.filter(test=>!selection||test.name===selection)){await test(browser);console.log('PASS '+test.name);}}
    finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
