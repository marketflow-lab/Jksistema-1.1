'use strict';

// Real page, events and IndexedDB; every HTTP response contains synthetic data.
// Optional source root runs the same user journey against the pre-fix checkout.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require('playwright');
const root = path.resolve(process.env.JK_MEDIAS_TEST_SOURCE_ROOT || path.join(__dirname, '..'));
const tempRoot = path.resolve(process.env.JK_TEST_TMP || os.tmpdir());
const profile = fs.mkdtempSync(path.join(tempRoot, 'jk-medias-listas-cache-'));
const host = 'http://localhost/medias_compras.html';
const html = fs.readFileSync(path.join(root, 'static/medias_compras.html'), 'utf8');
const initialIdentity = {client_id:'tenant-cache',username:'user-a'};
const initialPermissions = {medias_compras:true};
const stores = [{nome:'Loja A',store_id:'store-a'},{nome:'Loja B',store_id:'store-b'}];
const copy = value => JSON.parse(JSON.stringify(value));
const meta = (complete = true, extra = {}) => ({estado:'ready',stale:false,atualizando:false,
    versao:complete?'full-v1':'saved-v1',gerado_em:new Date().toISOString(),autoridade:'authority-a',
    politica_fotos:'photos-a',pendente_fontes:false,completo:complete,...extra});
function makeList(id, index) {
    const store = stores[index];
    return {id,nome_lista:'Pedido '+id.toUpperCase(),store_id:store.store_id,loja:store.nome,
        fornecedor_id:'supplier-a',supplier:'Fornecedor sintético',status:'Lista gerada',
        created_at:'2026-10-20T12:00:00Z',updated_at:'2026-10-20T12:00:00Z',
        itens:[{SKU:'SKU-'+id.toUpperCase(),'Título do produto em inglês':'Produto '+id,
            Quantidade:id==='a'?0:2,'Valor unidade':id==='a'?0:10,'Valor total':id==='a'?0:20,
            OEM:'','M3 individual':'','Estimed CBM':'','Estimed Weigh':'0.8','Individual packaging':'Box'}]};
}
const serverLists = {a:makeList('a',0),c:makeList('c',0),b:makeList('b',1)};
function fullList(id) {
    const list = copy(serverLists[id]);
    list.itens[0].OEM = 'OEM-'+id;
    list.itens[0]['M3 individual'] = 0.025;
    list.itens[0]['Estimed CBM'] = '0.050';
    return list;
}
let state;
const errors = [], checks = [];
async function json(route, body, status = 200) {
    await route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)}).catch(() => {});
}
async function start({hold = false, identity = initialIdentity, permissions = initialPermissions} = {}) {
    const context = await chromium.launchPersistentContext(profile,{headless:true,viewport:{width:1440,height:900}});
    state = {hold,held:[],calls:[],mutations:[],allowMutations:false};
    await context.addInitScript(({identity,permissions}) => {
        if (location.origin !== 'http://localhost') return;
        localStorage.setItem('user_data',JSON.stringify(identity));
        localStorage.setItem('permissions',JSON.stringify(permissions));
        localStorage.setItem('access_token','synthetic-token-'+identity.client_id+'-'+identity.username);
    },{identity,permissions});
    context.on('page',page => {
        page.on('pageerror',error => errors.push(error.message));
        page.on('dialog',dialog => dialog.accept());
    });
    await context.route('**/*',async route => {
        const request = route.request(), url = new URL(request.url());
        try {
            if (url.origin !== 'http://localhost') return route.fulfill({contentType:'text/css',body:''});
            if (url.pathname === '/medias_compras.html') return route.fulfill({contentType:'text/html',body:html});
            if (url.pathname.startsWith('/medias_compras/') || url.pathname === '/cache-persistente.js') {
                return route.fulfill({contentType:url.pathname.endsWith('.css')?'text/css':'application/javascript',
                    body:fs.readFileSync(path.join(root,'static',url.pathname.slice(1)))});
            }
            if (url.pathname === '/auth.js') return route.fulfill({contentType:'application/javascript',body:
                'window.verificarSessao=()=>true;window.obterAuthHeaders=(extra)=>({Authorization:"Bearer "+localStorage.getItem("access_token"),"X-Client-ID":JSON.parse(localStorage.getItem("user_data")).client_id,...(extra||{})});'});
            if (!url.pathname.startsWith('/api/')) return route.fulfill({contentType:'application/javascript',body:''});
            assert.equal(request.headers()['x-client-id'],identity.client_id);
            assert.equal(request.headers().authorization,'Bearer synthetic-token-'+identity.client_id+'-'+identity.username);
            const method = request.method();
            const match = url.pathname.match(/^\/api\/medias-compras\/listas-pedidos\/([^/]+)$/);
            const id = match && decodeURIComponent(match[1]);
            const item = {route,url,method,id}; state.calls.push(item);
            if (method !== 'GET') {
                assert.ok(state.allowMutations,'Only the explicit save/delete scenario may mutate synthetic data');
                assert.ok(id && ['PUT','DELETE'].includes(method),'Unexpected synthetic mutation');
                state.mutations.push({id,method});
                if (method === 'DELETE') {delete serverLists[id]; return json(route,{ok:true});}
                serverLists[id] = {...serverLists[id],...request.postDataJSON()};
                return json(route,{lista:copy(serverLists[id])});
            }
            if (state.hold && url.pathname.startsWith('/api/medias-compras/listas-pedidos')) {
                state.held.push(item); return;
            }
            if (id) return serverLists[id]
                ? json(route,{lista:url.searchParams.get('somente_salvos')==='true'?copy(serverLists[id]):fullList(id),
                    _cache:meta(url.searchParams.get('somente_salvos')!=='true')})
                : json(route,{detail:'Lista inexistente'},404);
            if (url.pathname === '/api/medias-compras/listas-pedidos') {
                const filtered = Object.values(serverLists).filter(list => !url.searchParams.get('store_id') || list.store_id === url.searchParams.get('store_id'));
                return json(route,{listas:filtered.map(list => ({...copy(list),itens:[],total_itens:list.itens.length}))});
            }
            if (url.pathname === '/api/lojas') return json(route,stores);
            if (url.pathname === '/api/cadastro/fornecedores') return json(route,[{id:'supplier-a',nome_empresa:'Fornecedor sintético'}]);
            if (url.pathname.endsWith('/preferencias-skus-ocultos')) return json(route,{skus_ocultos:[]});
            if (url.pathname.endsWith('/visao')) return json(route,{colunas_meses:[],itens:[]});
            if (url.pathname.endsWith('/posicao-estoque')) return json(route,{saldo_atual_estoque:0,estoque_em_transito:0,estoque_em_transito_listas:[]});
            throw new Error('Unexpected synthetic API '+url.pathname);
        } catch (error) {errors.push(error.message);return json(route,{detail:error.message},500);}
    });
    return context;
}
async function open(context, store = 'Loja A') {
    const page = await context.newPage(); page.setDefaultTimeout(5000);
    await page.goto(host,{waitUntil:'domcontentloaded'});
    await page.locator('#lojaBotoes button').filter({hasText:store}).click();
    await page.locator('#btnAbaListasPedidos').click();
    return page;
}
async function readyIndex(page, ids, name) {
    const start = performance.now();
    await page.waitForFunction(ids => ids.length === listasPedidosResumo.length
        && ids.every(id => listasPedidosResumo.some(list => list.id===id))
        && document.querySelectorAll('.lista-item').length===ids.length,ids,{timeout:2500});
    checks.push({name,milliseconds:Math.round(performance.now()-start)});
}
async function select(page,id) {await page.locator('.lista-item').filter({hasText:'Pedido '+id.toUpperCase()}).click();}
async function ready(page,id,{quantity = serverLists[id]?.itens[0].Quantidade,name = id,complete = true} = {}) {
    const start = performance.now();
    await page.waitForFunction(({id,quantity,complete}) => listaPedidoAtual?.id===id
        && listaPedidoAtual.itens[0].Quantidade===quantity
        && (!complete || listaPedidoAtual.itens[0].OEM==='OEM-'+id)
        && !document.querySelector('#editorListaPedido').inert
        && document.querySelector('#tblListaPedidoItens input[data-kind="sku"]')?.value==='SKU-'+id.toUpperCase(),
        {id,quantity,complete},{timeout:2500});
    if (complete) assert.equal(await page.evaluate(()=>listaPedidoAtual.itens[0]['M3 individual']),0.025);
    checks.push({name,milliseconds:Math.round(performance.now()-start)});
}
async function records(page) {
    return page.evaluate(async () => {
        if (!window.JKCachePersistente) return null;
        return new Promise((resolve,reject) => {
            const opening = indexedDB.open('jk-ui-snapshots-v1');
            opening.onerror=()=>reject(opening.error);
            opening.onsuccess=()=>{
                const db=opening.result;
                if (!db.objectStoreNames.contains('snapshots')) {db.close();resolve([]);return;}
                const tx=db.transaction('snapshots','readonly'), request=tx.objectStore('snapshots').getAll();
                request.onsuccess=()=>resolve(request.result);
                tx.oncomplete=()=>db.close(); tx.onerror=()=>{db.close();reject(tx.error);};
            };
        });
    });
}
async function persistedFull(page,id) {
    if (!await page.evaluate(()=>Boolean(window.JKCachePersistente))) return; // Baseline reaches the observable restart failure.
    for (let attempt=0;attempt<100;attempt++) {
        if ((await records(page)).some(row=>row.dados?.lista?.id===id && row.dados.lista.itens[0].OEM==='OEM-'+id)) return;
        await page.waitForTimeout(20);
    }
    throw new Error('Complete detail was not persisted: '+id);
}
async function ageSnapshots(page) {
    if (!await page.evaluate(()=>Boolean(window.JKCachePersistente))) return;
    await page.evaluate(()=>new Promise((resolve,reject)=>{
        const opening=indexedDB.open('jk-ui-snapshots-v1');
        opening.onerror=()=>reject(opening.error);
        opening.onsuccess=()=>{
            const db=opening.result,tx=db.transaction(['snapshots','indices'],'readwrite');
            for(const name of ['snapshots','indices']){
                const store=tx.objectStore(name),request=store.openCursor();
                request.onsuccess=()=>{const cursor=request.result;if(!cursor)return;const row=cursor.value;
                    row.salvoEm=Date.now()-20*60*1000;
                    if(row.dados?.meta)row.dados.meta.gerado_em=new Date(row.salvoEm).toISOString();
                    cursor.update(row);cursor.continue();};
            }
            tx.oncomplete=()=>{db.close();resolve();};tx.onerror=()=>{db.close();reject(tx.error);};
        };
    }));
}
async function held(predicate) {
    for(let attempt=0;attempt<250;attempt++){
        const item=[...state.held].reverse().find(predicate);
        if(item)return item;
        await new Promise(resolve=>setTimeout(resolve,10));
    }
    throw new Error('Expected pending synthetic request did not start');
}
async function release(item,body,status=200){state.held=state.held.filter(candidate=>candidate!==item);await json(item.route,body,status);}
async function store(page,name){await page.locator('#lojaBotoes button').filter({hasText:name}).click();}
async function prime(context){
    const page=await open(context);await readyIndex(page,['a','c'],'live index A');
    assert.equal(await page.evaluate(()=>window.isSecureContext&&Boolean(crypto.subtle)&&Boolean(indexedDB)),true,
        'Persistence regression requires native secure-context hashing and IndexedDB');
    for(const id of ['a','c']){await select(page,id);await ready(page,id,{name:'live full '+id});await persistedFull(page,id);}
    await store(page,'Loja B');await readyIndex(page,['b'],'live index B');
    await select(page,'b');await ready(page,'b',{name:'live full b'});await persistedFull(page,'b');
    return page;
}
async function main(){
    let context=await start();
    try{
        let page=await prime(context);await ageSnapshots(page);await context.close();
        context=await start({hold:true});page=await open(context);
        await readyIndex(page,['a','c'],'restart restores index before network');
        await held(item=>!item.id&&item.url.pathname.endsWith('/listas-pedidos'));
        await select(page,'a');await ready(page,'a',{name:'restart restores complete A before network'});
        assert.equal(await page.locator('input[data-kind="qtd"]').inputValue(),'0');
        const savedA=(await records(page)).find(row=>row.dados?.lista?.id==='a').salvoEm;
        const firstA=await held(item=>item.id==='a'&&item.url.searchParams.has('somente_salvos'));
        await release(firstA,{dados:null,_cache:meta(false,{estado:'waiting',stale:true,atualizando:true,versao:'waiting'})},202);
        await page.waitForTimeout(400);
        await ready(page,'a',{name:'20-minute saved detail survives cold-server 202'});
        assert.equal((await records(page)).find(row=>row.dados?.lista?.id==='a').salvoEm,savedA,'202 cannot renew the saved timestamp');
        await select(page,'c');await ready(page,'c',{name:'cached C opens while A refresh is pending'});
        await select(page,'a');await ready(page,'a',{name:'cached A reopens before network'});
        const failed=await held(item=>item.id==='a'&&item.url.searchParams.has('somente_salvos'));
        await release(failed,{detail:'Falha sintética'},503);
        await page.waitForFunction(()=>/indisponível/i.test(document.querySelector('#statusListaPedido').textContent));
        await ready(page,'a',{name:'503 preserves complete detail'});
        assert.equal((await records(page)).find(row=>row.dados?.lista?.id==='a').salvoEm,savedA,'503 cannot renew the saved timestamp');
        await store(page,'Loja B');await readyIndex(page,['b'],'store B index isolation');
        await select(page,'b');await ready(page,'b',{name:'store B complete cache'});
        await store(page,'Loja A');await readyIndex(page,['a','c'],'return to store A index');
        await select(page,'c');await ready(page,'c',{name:'detail usable during network timeout'});
        await page.waitForFunction(()=>/indisponível/i.test(document.querySelector('#statusListaPedido').textContent),null,{timeout:7000});
        await ready(page,'c',{name:'timeout preserves cached editor'});
        await select(page,'a');await ready(page,'a',{name:'restore before local edit'});
        const late=await held(item=>item.id==='a'&&item.url.searchParams.has('somente_salvos'));
        const input=page.locator('input[data-kind="qtd"]');await input.fill('88');
        await page.evaluate(()=>{window.__editedNode=document.activeElement;});
        const stale=copy(serverLists.a);stale.itens[0].Quantidade=999;
        await release(late,{lista:stale,_cache:meta(false)});
        await page.waitForTimeout(100);
        assert.equal(await input.inputValue(),'88');
        assert.equal(await page.evaluate(()=>document.activeElement===window.__editedNode&&window.__editedNode.isConnected),true,
            'Late response must preserve the focused editor node');
        checks.push({name:'late response preserves unconfirmed typing and focus'});
        await context.close();

        for(const variant of [
            {identity:{...initialIdentity,client_id:'tenant-other'},name:'tenant'},
            {identity:{...initialIdentity,username:'user-other'},name:'user'},
            {permissions:{medias_compras:true,allowed_stores:['store-b']},name:'permissions'},
        ]){
            context=await start({hold:true,...variant});page=await open(context);
            await held(item=>!item.id&&item.url.pathname.endsWith('/listas-pedidos'));
            assert.equal(await page.locator('.lista-item').count(),0,variant.name+' cannot restore previous authority cache');
            checks.push({name:variant.name+' isolation'});await context.close();
        }

        context=await start();page=await open(context);await readyIndex(page,['a','c'],'mutation index');
        await select(page,'a');await ready(page,'a',{name:'mutation source'});await persistedFull(page,'a');
        state.allowMutations=true;
        await page.locator('input[data-kind="qtd"]').fill('57');await page.locator('input[data-kind="qtd"]').press('Enter');
        for(let attempt=0;attempt<200&&serverLists.a.itens[0].Quantidade!==57;attempt++)await page.waitForTimeout(10);
        assert.equal(serverLists.a.itens[0].Quantidade,57,'Confirmed quantity must reach canonical synthetic storage');
        await page.waitForFunction(()=>/sucesso/i.test(document.querySelector('#statusListaPedido').textContent));
        await context.close();
        context=await start({hold:true});page=await open(context);
        await held(item=>!item.id&&item.url.pathname.endsWith('/listas-pedidos'));
        // Exercise the public open action even if mutation invalidated the index as well.
        await page.evaluate(()=>{void abrirListaPedidoPorId('a');});
        await held(item=>item.id==='a');
        assert.ok(await page.evaluate(()=>listaPedidoAtual===null||listaPedidoAtual.itens[0].Quantidade===57),
            'Restart after PUT must never restore old quantity zero');
        checks.push({name:'successful save fences stale detail across restart'});
        const refreshed=await held(item=>item.id==='a');
        await release(refreshed,{lista:copy(serverLists.a),_cache:meta(false)});
        await ready(page,'a',{quantity:57,complete:false,name:'confirmed save reloads canonical value'});
        await context.close();

        context=await start();page=await open(context,'Loja B');await readyIndex(page,['b'],'delete index');
        await select(page,'b');await ready(page,'b',{name:'delete source'});await persistedFull(page,'b');
        state.allowMutations=true;
        await page.evaluate(()=>{void excluirListaPedido('b','Pedido B','Lista gerada');});
        for(let attempt=0;attempt<200&&serverLists.b;attempt++)await page.waitForTimeout(10);
        assert.equal(serverLists.b,undefined);
        await page.waitForFunction(()=>/apagada com sucesso/i.test(document.querySelector('#statusListaPedido').textContent));
        await context.close();
        context=await start({hold:true});page=await open(context,'Loja B');
        await held(item=>!item.id&&item.url.pathname.endsWith('/listas-pedidos'));
        assert.equal(await page.locator('.lista-item').count(),0,'Deleted list cannot return from index cache');
        await page.evaluate(()=>{void abrirListaPedidoPorId('b');});await held(item=>item.id==='b');
        assert.equal(await page.evaluate(()=>listaPedidoAtual),null,'Deleted detail cannot return after restart');
        checks.push({name:'successful delete fences index and detail across restart'});
        assert.deepEqual(errors,[]);
        console.log('PASS Medias list persistence: '+JSON.stringify(checks));
    }finally{
        await context.close();
        const resolved=path.resolve(profile);assert.equal(path.dirname(resolved),tempRoot);
        assert.ok(path.basename(resolved).startsWith('jk-medias-listas-cache-'));
        fs.rmSync(resolved,{recursive:true,force:true});
    }
}
main().catch(error=>{console.error(error);process.exitCode=1;});
