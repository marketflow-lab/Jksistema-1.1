"use strict";
const assert = require('assert/strict');
const fs = require('fs');
const path = require('path');
const http = require('http');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'pesquisa_mercado.html'), 'utf8')
    .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '')
    .replace('</body>', '<script src="/pesquisa-mercado/global-ui.js"></script><script src="/pesquisa-mercado/history-ui.js"></script></body>');
async function main() {
    const category = {key:'MLB1', nome:'Ferramentas', url:'https://www.mercadolivre.com.br/c/ferramentas'};
    const original = {id:'delete-me', status:'completed', revision:1, scope_category:category, scopeCategory:category,
        created_at:'2026-10-07T12:00:00Z', counters:{ads:7,sellers:1,pending:0}};
    let job = {...original}, revision = 1, deleteStatus = 200, diagnosticStatus = 500, holdDelete = false, holdLatest = false;
    let pendingDelete, pendingLatest, pendingNames;
    const mutations = [], errors = [];
    const ad = {id:'MLB12345678', ad_key:'id:MLB12345678', seller_key:'id:1', titulo:'Produto da pesquisa', vendedor:'Loja A', rank:1};
    const json = (res, value, status=200) => {res.statusCode=status;res.setHeader('Content-Type','application/json');res.end(JSON.stringify(value));};
    const server = http.createServer((req,res) => {
        const u = new URL(req.url,'http://localhost');
        if (u.pathname === '/pesquisa_mercado.html') {res.setHeader('Content-Type','text/html');res.end(html);return;}
        if (u.pathname.startsWith('/pesquisa-mercado/')) {
            const name=path.basename(u.pathname);
            let source=fs.readFileSync(path.join(root,'static/pesquisa-mercado',name),'utf8');
            if(name==='global-ui.js') source=source.replace('else factory(root);','else root.__globalTest = factory(root);');
            res.setHeader('Content-Type',name.endsWith('.css')?'text/css':'text/javascript');res.end(source);return;
        }
        if(u.pathname==='/test/names') {pendingNames=()=>json(res,{job,updated:0,pending:0});return;}
        const suffix=u.pathname.replace('/api/pesquisa-mercado','');
        if(req.method==='DELETE') {
            mutations.push({path:suffix, token:req.headers.authorization});
            if(deleteStatus!==200) {json(res,{detail:'fixture error'},deleteStatus);return;}
            job=null;revision++;
            if(holdDelete) pendingDelete=()=>json(res,{deleted:true,job_id:original.id,revision});
            else json(res,{deleted:true,job_id:original.id,revision});
            return;
        }
        if(suffix==='/jobs/latest') {
            const snapshot=job && {...job};
            if(holdLatest){holdLatest=false;pendingLatest=()=>json(res,{job:snapshot});return;}
            json(res,{job:snapshot});return;
        }
        if(suffix==='/jobs'){json(res,{items:job?[job]:[],total:job?1:0,owner_revision:revision});return;}
        if(suffix==='/consolidado/categorias'){json(res,{items:[],total:0,owner_revision:revision});return;}
        if(suffix==='/consolidado/resumo'){json(res,{researches:job?1:0,unique_ads:job?1:0,owner_revision:revision});return;}
        if(suffix==='/consolidado/ranking'){json(res,{items:job?[ad]:[],total:job?1:0,owner_revision:revision});return;}
        if(!job){json(res,{detail:'Not found'},404);return;}
        if(suffix.endsWith('/diagnostico')){json(res,{scopePath:[category],incompleteCategories:[],incompleteCategoriesTotal:0,salesRecoveryPending:false,recovery:null},diagnosticStatus);return;}
        if(suffix.endsWith('/resumo')){json(res,{job});return;}
        if(suffix.endsWith('/vendedores')){json(res,{items:[{seller_key:'id:1',vendedor:'Loja A'}]});return;}
        if(suffix.endsWith('/categorias')){json(res,{items:[]});return;}
        if(suffix.endsWith('/ranking')){json(res,{items:[ad],total:1,revision:job.revision});return;}
        json(res,{},404);
    });
    await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
    let browser;
    try {
        browser=await chromium.launch({headless:true});
        const context=await browser.newContext({viewport:{width:1280,height:950}});
        await context.addInitScript(()=>{
            localStorage.setItem('access_token','fixture-owner');
            window.electronAPI={getPesquisaMercadoJobStatus:async()=> (await fetch('/api/pesquisa-mercado/jobs/latest')).json(),
                refreshPesquisaMercadoNames:async()=> (await fetch('/test/names')).json(),
                onPesquisaMercadoProgress(cb){window.__progress=cb;return()=>{};}};
        });
        const page=await context.newPage();
        page.on('pageerror',error=>errors.push(error.message));
        await page.route('**/*',route=>new URL(route.request().url()).host===`127.0.0.1:${server.address().port}`?route.continue():route.abort());
        await page.goto(`http://127.0.0.1:${server.address().port}/pesquisa_mercado.html`);
        await page.waitForFunction(()=>window.__globalTest.state.activeJob && document.getElementById('pm-global-ads-count').textContent==='7');
        await page.locator('#pm-mode-history').click();
        await page.locator('[data-consult-job="delete-me"]').click();
        await page.waitForFunction(()=>!window.__jkPesquisaMercadoHistory.state.history.detailBusy);
        assert.equal(await page.locator('#pm-history-detail-rows tr').count(),1,'diagnostic failure preserves saved ranking');
        assert.equal(await page.locator('#pm-history-diagnostic-message').isVisible(),true,'diagnostic failure offers refresh without hiding results');
        diagnosticStatus=200;
        await page.locator('#pm-history-refresh').click();
        await page.waitForFunction(()=>!window.__jkPesquisaMercadoHistory.state.history.detailBusy&&!window.__jkPesquisaMercadoHistory.state.history.busy);
        assert.equal(await page.locator('#pm-history-diagnostic-message').isVisible(),false,'refresh retries the selected diagnostic');
        assert.equal(await page.locator('#pm-history-detail-rows tr').count(),1,'refresh preserves saved results');
        const waitPending=async value=>{for(let n=0;n<100;n++){if(value())return;await new Promise(resolve=>setTimeout(resolve,20));}throw Error('Fixture did not receive held request');};
        await page.evaluate(()=>{window.__namesTask=window.__jkPesquisaMercadoRefreshNames('delete-me');});
        await waitPending(()=>pendingNames);
        assert.equal(await page.locator('[data-delete-job="delete-me"]').isDisabled(),true,'names recovery disables deletion');
        pendingNames();await page.evaluate(()=>window.__namesTask);
        await page.waitForFunction(()=>!document.querySelector('[data-delete-job="delete-me"]').disabled);
        await page.locator('[data-delete-job="delete-me"]').click();
        assert.equal(await page.evaluate(()=>document.activeElement.dataset.cancelDeleteJob),'delete-me','confirmation initially focuses cancel');
        await page.evaluate(value=>window.__progress({job:value}),original);
        assert.equal(await page.evaluate(()=>document.activeElement.dataset.cancelDeleteJob),'delete-me','status update preserves keyboard focus');
        await page.locator('[data-cancel-delete-job="delete-me"]').click();
        assert.equal(mutations.length,0);
        for(const status of [409,500]) {
            deleteStatus=status;
            await page.locator('[data-delete-job="delete-me"]').click();
            await page.locator('[data-confirm-delete-job="delete-me"]').click();
            await page.waitForFunction(()=>!window.__jkPesquisaMercadoHistory.state.history.deleteBusy);
            assert.equal(await page.locator('[data-delete-job="delete-me"]').count(),1,'failed deletion preserves history');
            assert.equal(await page.locator('#pm-history-detail-rows tr').count(),1,'failed deletion preserves detail');
            assert.match(await page.locator('#pm-history-message').innerText(),status===409?/Cancele/:/Não foi possível apagar/);
            await page.locator('[data-cancel-delete-job="delete-me"]').click();
        }
        deleteStatus=200;holdDelete=true;holdLatest=true;
        await page.evaluate(()=>{window.__statusTask=window.__globalTest.synchronize();});
        await waitPending(()=>pendingLatest);
        await page.locator('[data-delete-job="delete-me"]').click();
        // Optional screenshot requested by the caller, using synthetic fixture data only.
        if(process.env.JK_PM_DELETE_PREVIEW) await page.locator('#pm-panel-history').screenshot({path:process.env.JK_PM_DELETE_PREVIEW});
        await page.locator('[data-confirm-delete-job="delete-me"]').click();
        await waitPending(()=>pendingDelete);
        await page.locator('#pm-mode-consolidated').click();
        assert.equal(await page.evaluate(()=>window.__jkPesquisaMercadoHistory.state.history.deleteBusy),'delete-me','tab change preserves pending mutation');
        pendingDelete();
        await page.waitForFunction(()=>!window.__jkPesquisaMercadoHistory.state.history.deleteBusy && !window.__jkPesquisaMercadoHistory.state.consolidated.busy);
        assert.equal(await page.evaluate(()=>window.__globalTest.state.activeJob),null,'deletion clears selected global job');
        assert.equal(await page.locator('#pm-global-ads-count').innerText(),'0');
        pendingLatest();await page.evaluate(()=>window.__statusTask);
        assert.equal(await page.evaluate(()=>window.__globalTest.state.activeJob),null,'late status cannot restore deleted job');
        await page.evaluate(value=>window.__progress({job:value}),original);
        await page.waitForTimeout(70);
        assert.equal(await page.evaluate(()=>window.__globalTest.state.activeJob),null,'late progress cannot restore deleted job');
        assert.equal(await page.locator('#pm-consolidated-rows tr').count(),0);
        await page.locator('#pm-mode-history').click();
        await page.waitForFunction(()=>!window.__jkPesquisaMercadoHistory.state.history.busy);
        assert.equal(await page.locator('#pm-history-list article').count(),0);
        assert.equal(await page.locator('#pm-history-detail-body').isVisible(),false);
        assert.equal(mutations.filter(item=>item.token!=='Bearer fixture-owner').length,0,'mutation retains authenticated owner');
        assert.deepEqual(errors,[]);
        console.log('Pesquisa deletion UI: confirmation focus, names exclusion, error retention, mode change, stale status/progress and empty state OK');
    } finally {if(browser)await browser.close();await new Promise(resolve=>server.close(resolve));}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
