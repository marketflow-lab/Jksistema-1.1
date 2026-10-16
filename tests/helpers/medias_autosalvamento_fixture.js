'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');
const root = path.resolve(__dirname, '..', '..');
const baseline = process.argv.includes('--baseline');
const sourceCache = new Map();

function source(relative) {
    if (!sourceCache.has(relative)) sourceCache.set(relative, baseline
        ? execFileSync('git', ['show', 'HEAD:' + relative], { cwd:root, encoding:'utf8', windowsHide:true })
        : fs.readFileSync(path.join(root, relative), 'utf8'));
    return sourceCache.get(relative);
}

async function fixture(browser, options = {}) {
    const context = await browser.newContext({ viewport:{width:1380,height:850} });
    const page = await context.newPage();
    page.setDefaultTimeout(4000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('dialog', dialog => dialog.accept());
    await page.addInitScript(options => {
        const clone = value => JSON.parse(JSON.stringify(value));
        const tenant = 'tenant-autosave';
        localStorage.setItem('user_data', JSON.stringify({client_id:tenant,username:'synthetic-user'}));
        localStorage.setItem('access_token','synthetic-token');
        const product = sku => ({cubagem_sku:sku,produto_bling:'Catalog '+sku,'M3 individual':0.025,OEM:'OEM-'+sku});
        const item = (sku,index=0) => ({SKU:sku,'Título do produto em inglês':'Product '+sku,Quantidade:index+2,
            'Valor unidade':2.5,'Valor total':(index+2)*2.5,'M3 individual':0.025,'Estimed CBM':String((index+2)*0.025),
            'Estimed Weigh':'0.8','Individual packaging':'Box'});
        const lists = {};
        for (const letter of ['a','b']) lists['list-'+letter] = {id:'list-'+letter,nome_lista:'Pedido '+letter.toUpperCase(),loja:'Loja '+letter.toUpperCase(),
            store_id:'store-'+letter,status:'Lista gerada',fornecedor_id:'supplier-a',supplier:'Supplier A',
            itens:Array.from({length:options.rows || 24},(_,index)=>item('SKU-'+letter.toUpperCase()+'-'+String(index+1).padStart(2,'0'),index))};
        const calls = [], plans = new Map(), pending = new Map();
        const plan = (key,value) => {const queue=plans.get(key)||[];queue.push(value);plans.set(key,queue);};
        window.__autosaveFixture = {calls,lists,plan,release(index){const entry=pending.get(index);if(!entry)throw new Error('Request not held: '+index);pending.delete(index);entry();}};
        const nativeFetch = window.fetch.bind(window);
        window.fetch = async (input,init={}) => {
            const url = new URL(String(input),location.href);
            if(!url.pathname.startsWith('/api/'))return nativeFetch(input,init);
            const headers = new Headers(init.headers);
            if(headers.get('authorization')!=='Bearer synthetic-token'||headers.get('x-client-id')!==tenant)throw new Error('Synthetic tenant/auth missing');
            const method=init.method||'GET', payload=init.body?JSON.parse(init.body):null;
            const matched=url.pathname.match(/^\/api\/medias-compras\/listas-pedidos\/([^/]+)(?:\/(adicionar-sku))?$/);
            const id=matched&&decodeURIComponent(matched[1]);
            let key;
            if(matched)key=(method==='PUT'?'save:':method==='POST'?'add:':'detail:')+id;
            else if(url.pathname==='/api/lojas')key='stores';
            else if(url.pathname.endsWith('/preferencias-skus-ocultos'))key='prefs';
            else if(url.pathname.endsWith('/visao'))key='view';
            else if(url.pathname==='/api/medias-compras/listas-pedidos')key='summaries';
            else if(url.pathname==='/api/cadastro/fornecedores')key='suppliers';
            else if(url.pathname==='/api/cadastro/produto')key='sku:'+url.searchParams.get('sku');
            else if(url.pathname==='/api/cadastro/produtos')key='products';
            else throw new Error('Unexpected API: '+method+' '+url.pathname);
            const selected=(plans.get(key)||[]).shift()||{};
            const call={index:calls.length,key,method,id,savedOnly:url.searchParams.get('somente_salvos')==='true',payload:clone(payload),done:false,failed:false};calls.push(call);
            if(selected.hold)await new Promise(resolve=>pending.set(call.index,resolve));
            let status=selected.status||200, body;
            if(status>=400){body={detail:selected.detail||'Synthetic save failure'};call.failed=true;}
            else if(key==='stores')body=[{store_id:'store-a',nome:'Loja A'},{store_id:'store-b',nome:'Loja B'}];
            else if(key==='prefs')body={skus_ocultos:[]};
            else if(key==='view')body={colunas_meses:[],itens:[]};
            else if(key==='suppliers')body=[{id:'supplier-a',nome_empresa:'Supplier A'},{id:'supplier-b',nome_empresa:'Supplier B'}];
            else if(key==='summaries')body={listas:Object.values(lists).filter(list=>!url.searchParams.get('store_id')||list.store_id===url.searchParams.get('store_id')).map(list=>({...clone(list),itens:[],total_itens:list.itens.length}))};
            else if(key==='products')body=[];
            else if(key.startsWith('sku:')) {const sku=url.searchParams.get('sku');if(sku==='MISSING'){status=404;body={detail:'SKU not found'};}else body={produto:product(sku)};}
            else if(key.startsWith('detail:'))body={lista:clone(lists[id])};
            else if(key.startsWith('save:')) {assertList(id);lists[id]={...lists[id],...clone(payload)};body={lista:clone(lists[id])};}
            else if(key.startsWith('add:')) {assertList(id);const row=item(selected.canonicalSku||payload.sku);row.Quantidade=payload.quantidade;row['Valor unidade']=payload.valor_unitario;row['Valor total']=row.Quantidade*row['Valor unidade'];Object.assign(row,clone(selected.item||{}));const at=lists[id].itens.findIndex(it=>it.SKU===row.SKU);if(at<0)lists[id].itens.push(row);else lists[id].itens[at]=row;body={lista:clone(lists[id]),sku:row.SKU,acao:at<0?'adicionado':'atualizado'};}
            if(selected.body)body=clone(selected.body);
            call.done=true;call.response=clone(body);
            return {ok:status<400,status,headers:new Headers(),json:async()=>clone(body)};
        };
        function assertList(id){if(!lists[id])throw new Error('Unknown synthetic list '+id);}
    },options);
    await page.route('**/*',async route=>{
        const url=new URL(route.request().url());
        if(url.origin!=='http://jk.test')return route.fulfill({status:200,contentType:'text/css',body:''});
        if(url.pathname==='/medias_compras.html')return route.fulfill({status:200,contentType:'text/html',body:source('static/medias_compras.html')});
        if(url.pathname.startsWith('/medias_compras/'))return route.fulfill({status:200,contentType:url.pathname.endsWith('.css')?'text/css':'application/javascript',body:source('static'+url.pathname)});
        if(url.pathname==='/auth.js')return route.fulfill({status:200,contentType:'application/javascript',body:'window.verificarSessao=()=>true;window.obterAuthHeaders=(extra)=>({Authorization:"Bearer synthetic-token","X-Client-ID":"tenant-autosave",...(extra||{})});'});
        return route.fulfill({status:200,contentType:'application/javascript',body:''});
    });
    await page.goto('http://jk.test/medias_compras.html');
    await page.waitForFunction(()=>window.__autosaveFixture.calls.some(call=>call.key==='view'&&call.done));
    await page.locator('#btnAbaListasPedidos').click();
    await openList(page,'A');
    return {page,context,errors};
}

async function openList(page,letter){
    const start=await page.evaluate(()=>__autosaveFixture.calls.length);
    await page.locator('.lista-item').filter({hasText:'Pedido '+letter}).click();
    await page.waitForFunction(letter=>document.querySelector('#nomeListaPedidoEdit')?.value==='Pedido '+letter&&!document.querySelector('#editorListaPedido').inert,letter);
    await page.waitForFunction(()=>!document.querySelector('#fornecedorListaPedidoEdit').disabled);
    // Wait for the actual enrichment response; a fixed delay races the scheduled callback under load.
    await page.waitForFunction(({letter,start})=>__autosaveFixture.calls.slice(start).some(call=>call.key==='detail:list-'+letter.toLowerCase()&&!call.savedOnly&&call.done),{letter,start});
}
function field(page,kind,index=0){return page.locator(`#tblListaPedidoItens [data-kind="${kind}"][data-idx="${index}"]`);}
async function plan(page,key,value){await page.evaluate(({key,value})=>__autosaveFixture.plan(key,value),{key,value});}
async function calls(page,key){return page.evaluate(key=>__autosaveFixture.calls.filter(call=>call.key===key),key);}
async function release(page,index){await page.evaluate(index=>__autosaveFixture.release(index),index);}
async function serverList(page,id='list-a'){return page.evaluate(id=>JSON.parse(JSON.stringify(__autosaveFixture.lists[id])),id);}
async function waitServer(page,predicate,arg){
    try {await page.waitForFunction(predicate,arg,{timeout:3500});}
    catch(error){
        const state=await page.evaluate(()=>({
            status:document.querySelector('#statusListaPedido')?.textContent,
            modalStatus:document.querySelector('#modalAdicionarSkuStatus')?.textContent,
            modalValues:['addSkuInput','addSkuQtdInput','addSkuValorInput'].map(id=>document.getElementById(id)?.value),
            calls:__autosaveFixture.calls.slice(-8).map(({key,done,failed,payload})=>({key,done,failed,payload})),
        }));
        error.message+='\nSynthetic fixture state: '+JSON.stringify(state);
        throw error;
    }
}
async function clean(test){assert.deepStrictEqual(test.errors,[]);await test.context.close();}
module.exports={fixture,openList,field,plan,calls,release,serverList,waitServer,clean};
