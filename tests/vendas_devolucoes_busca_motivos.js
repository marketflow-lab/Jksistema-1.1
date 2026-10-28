'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');
const {fixture, origin, range, query, storeA, listReady, until} = require('./vendas_devolucoes_store_filter');
const {record, identified} = require('./vendas_devolucoes_motivos');
const idle = () => ({phase:'idle',running:false,total:0,processed:0,identified:0,pending:0,reconnect_required:0});
const active = () => ({phase:'processing',running:true,total:2,processed:0,identified:0,pending:2,reconnect_required:0});
const done = () => ({phase:'completed',running:false,total:2,processed:2,identified:1,pending:0,reconnect_required:0});
const unknown = {status:'not_identified',descricao:'',codigo:'',fonte:'',status_coleta:'pending'};
const rows = () => [record('200',1,1,100,unknown),record('290',2,1,150,unknown)];

async function setup(browser) {
    const items = rows();
    const f = await fixture(browser,{items});
    const state = {data:idle(), posts:[], gets:0, holdPost:false, pendingPost:null, postStatus:200, holdStatus:false, pendingStatus:null};
    await f.page.route('**/api/notas-entrada/motivos/*', async route => {
        const req = route.request();
        const respond = (data,status=200) => route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
        if (req.method()==='POST') {
            state.posts.push({url:req.url(),body:req.postDataJSON(),headers:req.headers()});
            state.data=active();
            if (state.holdPost) await new Promise(resolve => {state.pendingPost=async()=>{await respond(state.data,state.postStatus);resolve();};});
            else await respond(state.data,state.postStatus);
        } else {
            state.gets++;
            const snapshot=structuredClone(state.data);
            if (state.holdStatus) {
                state.holdStatus=false;
                await new Promise(resolve=>{state.pendingStatus=async()=>{await respond(snapshot);resolve();};});
            } else await respond(snapshot);
        }
    });
    const open = async () => {
        await f.page.goto(origin+'/devolucoes.html?'+query({...range,loja:storeA}));
        await listReady(f.page,'200');
        await until(()=>state.gets>0,'Initial persisted progress was not consulted');
    };
    const poll = async () => {
        const count=state.gets;
        await f.page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
        await until(()=>state.gets>count,'Progress was not refreshed');
    };
    return {f,state,items,open,poll,async close(){if(state.pendingPost)await state.pendingPost();if(state.pendingStatus)await state.pendingStatus();await f.close();}};
}

async function bulkAndRefresh(browser) {
    const t=await setup(browser);const {f,state,items}=t;
    try {
        await t.open();
        assert.equal(state.posts.length,0,'Opening the report must not start a historical lookup');
        await f.page.locator('#expand-0').click();
        await f.page.locator('#details-0 summary').click();
        state.holdPost=true;
        await f.page.evaluate(()=>{document.getElementById('buscarMotivos').click();document.getElementById('buscarMotivos').click();});
        await until(()=>state.pendingPost,'The bulk POST did not start');
        assert.equal(state.posts.length,1,'Double click must not enqueue twice');
        assert.deepEqual(state.posts[0].body,{},'The bulk lookup covers all history, without display filters or tenant in the body');
        assert.equal(new URL(state.posts[0].url).search,'');
        assert.equal(state.posts[0].headers.authorization,'Bearer offline-fixture');
        assert.ok(state.posts[0].headers['x-client-id']);
        assert.equal(await f.page.locator('#buscarMotivos').isDisabled(),true);
        await state.pendingPost();state.pendingPost=null;
        await f.page.waitForFunction(()=>document.getElementById('buscaMotivosStatus').textContent.includes('Busca em andamento'));
        items[0].motivo_devolucao=identified('Produto diferente','PDD_TEST');
        state.data=done();
        await t.poll();
        await f.page.waitForFunction(()=>document.getElementById('coberturaMotivos').textContent.includes('1 de 2'));
        assert.match(await f.page.locator('#relatorioMotivos').textContent(),/Produto diferente/);
        assert.equal(await f.page.locator('#details-0').isVisible(),true,'Background update must preserve expanded SKU');
        assert.equal(await f.page.locator('#details-0 details').evaluate(node=>node.open),true,'Buyer details must retain their open state');
        assert.equal(await f.page.locator('#lojaSelect').inputValue(),storeA);
        assert.equal(await f.page.locator('#dataInicio').inputValue(),range.data_inicio);
        assert.equal(await f.page.locator('#totalDevolucoes').textContent(),'2');
        assert.equal(await f.page.locator('#buscarMotivos').isEnabled(),true);
        const folder=process.env.JK_DEVOLUCOES_ARTIFACTS_DIR;
        if(folder){fs.mkdirSync(folder,{recursive:true});await f.page.screenshot({path:path.join(folder,'devolucoes-buscar-motivos-desktop.png'),fullPage:true});}
        await f.page.setViewportSize({width:390,height:844});
        assert.ok(await f.page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
        if(folder)await f.page.screenshot({path:path.join(folder,'devolucoes-buscar-motivos-mobile.png'),fullPage:true});
        assert.deepEqual(f.errors,[]);
    } finally {await t.close();}
}

async function reloadAndUncertainPost(browser) {
    const t=await setup(browser);const {f,state}=t;
    try {
        state.data=active();await t.open();
        await f.page.waitForFunction(()=>document.getElementById('buscarMotivos').disabled);
        assert.equal(state.posts.length,0,'An existing operation must resume from GET status');
        await f.page.reload();await listReady(f.page,'200');
        await f.page.waitForFunction(()=>document.getElementById('buscaMotivosStatus').textContent.includes('Busca em andamento'));
        assert.equal(state.posts.length,0);
        state.data=done();await t.poll();
        await f.page.waitForFunction(()=>!document.getElementById('buscarMotivos').disabled);
        state.postStatus=503;
        await f.page.locator('#buscarMotivos').click();
        await f.page.waitForFunction(()=>document.getElementById('buscaMotivosStatus').textContent.includes('Busca em andamento'));
        assert.equal(state.posts.length,1,'An uncertain POST response must check status instead of blindly repeating');
        state.data={...done(),phase:'blocked',identified:0,reconnect_required:2};await t.poll();
        await f.page.waitForFunction(()=>document.getElementById('buscaMotivosStatus').textContent.includes('reconectar'));
        assert.equal(await f.page.locator('#buscarMotivos').isEnabled(),true);
        assert.deepEqual(f.errors,[]);
    } finally {await t.close();}
}

async function tenantRace(browser) {
    const t=await setup(browser);const {f,state}=t;
    try {
        await t.open();state.data={...done(),total:9876,processed:9876,identified:9876};state.holdStatus=true;
        await t.poll();await until(()=>state.pendingStatus,'Old tenant progress was not suspended');
        state.data=idle();
        const gets=state.gets;
        await f.page.evaluate(()=>{localStorage.setItem('user_data',JSON.stringify({client_id:'tenant-new'}));window.dispatchEvent(new StorageEvent('storage',{key:'user_data'}));});
        await until(()=>state.gets>gets,'New tenant progress was not loaded');
        await state.pendingStatus();state.pendingStatus=null;
        await f.page.waitForFunction(()=>document.getElementById('buscaMotivosStatus').textContent.includes('todo o histórico'));
        assert.doesNotMatch(await f.page.locator('#buscaMotivosStatus').textContent(),/9.876|9876/);
        assert.deepEqual(f.errors,[]);
    } finally {await t.close();}
}

async function completionWhileReportIsLoading(browser) {
    const t=await setup(browser);const {f,state,items}=t;
    try {
        await t.open();
        f.state.hold=true;state.data=active();await t.poll();
        await until(()=>f.held.length===1,'The earlier report refresh was not suspended');
        items[0].motivo_devolucao=identified('Motivo final confirmado','PDD_FINAL');
        state.data=done();await t.poll();
        await f.page.waitForFunction(()=>document.getElementById('buscaMotivosStatus').textContent.includes('Busca concluída'));
        f.state.hold=false;await f.held[0].release();
        await f.page.waitForFunction(()=>consultaDevolucoesController===null);
        await f.page.evaluate(()=>{const original=Date.now;Date.now=()=>original()+20000;});
        await t.poll();
        await f.page.waitForFunction(()=>document.getElementById('relatorioMotivos').textContent.includes('Motivo final confirmado'));
        assert.match(await f.page.locator('#coberturaMotivos').textContent(),/1 de 2/);
        assert.deepEqual(f.errors,[]);
    } finally {await t.close();}
}

async function completedOnFirstStatus(browser) {
    const t=await setup(browser);const {f,state,items}=t;
    try {
        state.data=done();state.holdStatus=true;f.state.hold=true;
        await f.page.goto(origin+'/devolucoes.html?'+query({...range,loja:storeA}));
        await until(()=>f.held.length===1 && state.pendingStatus,'Initial report and completed status were not suspended');
        items[0].motivo_devolucao=identified('Motivo encontrado antes de abrir','PDD_OPEN');
        await state.pendingStatus();state.pendingStatus=null;
        await f.page.waitForFunction(()=>document.getElementById('buscaMotivosStatus').textContent.includes('Busca concluída'));
        f.state.hold=false;await f.held[0].release();
        await listReady(f.page,'200');
        assert.match(await f.page.locator('#coberturaMotivos').textContent(),/0 de 2/,'The initial response must retain its older snapshot');
        await f.page.waitForFunction(()=>consultaDevolucoesController===null);
        await f.page.evaluate(()=>{const original=Date.now;Date.now=()=>original()+20000;});
        await t.poll();
        await f.page.waitForFunction(()=>document.getElementById('relatorioMotivos').textContent.includes('Motivo encontrado antes de abrir'));
        assert.match(await f.page.locator('#coberturaMotivos').textContent(),/1 de 2/);
        assert.equal(state.posts.length,0);
        assert.deepEqual(f.errors,[]);
    } finally {await t.close();}
}

async function main(){
    const browser=await chromium.launch({headless:true});
    try{await bulkAndRefresh(browser);await reloadAndUncertainPost(browser);await tenantRace(browser);await completionWhileReportIsLoading(browser);await completedOnFirstStatus(browser);
        console.log('vendas_devolucoes_busca_motivos: OK (bulk action, durable progress reload, report refresh, uncertain POST, tenant race, mobile)');
    }finally{await browser.close();}
}
if(require.main===module)main().catch(error=>{console.error(error);process.exitCode=1;});
