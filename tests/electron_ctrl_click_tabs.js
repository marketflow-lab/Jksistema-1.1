'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..');
const read = file => fs.readFileSync(path.join(root, file), 'utf8');
const origin = 'http://127.0.0.1:8001';
const salesNavigation = read('static/vendas/periodo-cache.js').split('function atualizarPeriodoComRecarregamento')[0];
const fullSource = read('static/full/cobertura.js');
const fullNavigation = fullSource.slice(fullSource.indexOf('function abrirModuloFullAcao('), fullSource.indexOf('function executarAcaoCobertura('));
const meetingSource = read('static/ia-sidebar/04-mensagens-notificacoes.part.js');
const meetingNavigation = meetingSource.slice(meetingSource.indexOf('function _msgAbrirSalaReuniao('), meetingSource.indexOf('function _msgTocarChamadaPulso('));
const target = '/target.html?sku=Pe%C3%A7a%20%26%201&loja=store-a#detalhes';

function fixtureHtml() {
    return `<!doctype html><html><head><title>Origem</title><style>body{padding-top:120px}</style><script src="/auth/navigation.js"></script></head><body>
        <input id="draft" value="initial"><a id="link" href="${target}"><span>Detalhes</span></a>
        <a id="download" href="/report.html" download>Download</a><a id="hash" href="#section">Seção</a>
        <button id="inline" onclick="location.href='${target}'">Detalhes por botão</button>
        <button id="transition" onclick="navegarComTransicao('${target}')">Com transição</button>
        <button id="listener">Com listener</button><button id="action">Ação</button>
        <button id="full" onclick="abrirModuloFullAcao('${target}','Detalhes')">Abrir pelo Full</button>
        <button id="meeting" onclick="_msgAbrirSalaReuniao({url:'${target}'})">Abrir sala</button>
        <div id="skuLoadingOverlay" aria-hidden="true"></div><table><tbody id="rows"><tr data-sku="SKU & 1"><td>SKU da venda</td></tr></tbody></table>
        <script>
            window.fixtureIdentity = Math.random(); window.actionCount = 0;
            document.querySelector('#listener').addEventListener('click', () => location.assign('${target}'));
            document.querySelector('#action').addEventListener('click', () => actionCount++);
            const tBody=document.querySelector('#rows'), skuLoadingOverlay=document.querySelector('#skuLoadingOverlay');
            const lojaSelecionada='Loja & Peças', unidadeNegocioSelect={value:'Canal & A'};
            function getDataIniISO(){return '2026-11-01';} function getDataFimISO(){return '2026-11-12';}
            ${salesNavigation}
            ${fullNavigation}
            function _msgSalaReuniaoUrl(call){return call.url;}
            ${meetingNavigation}
        </script></body></html>`;
}

async function main() {
    const browser = await chromium.launch({headless: true});
    const page = await browser.newPage({viewport: {width: 1440, height: 900}});
    const errors = [], popups = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('popup', popup => popups.push(popup));
    await page.route('**/*', route => {
        const url = new URL(route.request().url());
        if (url.origin !== origin) return route.abort();
        if (url.pathname === '/electron_shell.html') return route.fulfill({contentType:'text/html',body:read('electron_shell.html')});
        if (url.pathname.endsWith('.html')) return route.fulfill({contentType:'text/html',body:fixtureHtml()});
        let file;
        if (url.pathname === '/auth/navigation.js') file='static/auth/navigation.js';
        else if (url.pathname.startsWith('/static/') && url.pathname.endsWith('.js')) file=url.pathname.slice(1);
        if(file && fs.existsSync(path.join(root,file))) return route.fulfill({contentType:'application/javascript',body:read(file)});
        return route.fulfill({status:404,body:'not found'});
    });
    try {
        await page.goto(origin+'/electron_shell.html?appUrl='+encodeURIComponent(origin+'/dashboard.html'));
        await page.waitForFunction(()=>tabs.size===1 && [...tabs.values()][0].webview.contentDocument?.querySelector('#draft'));
        const originalId=await page.evaluate(()=>activeId);
        const original=page.frames().find(frame=>frame.url()===origin+'/dashboard.html');
        assert.ok(original);
        await original.locator('#draft').fill('rascunho preservado');
        const identity=await original.evaluate(()=>fixtureIdentity);
        assert.equal(await original.evaluate(()=>typeof window.navigation?.addEventListener),'function');
        let expected=1;
        for(const selector of ['#link span','#inline','#listener','#transition','#full','#meeting']) {
            await page.evaluate(id=>activateTab(id),originalId);
            await original.locator(selector).click({modifiers:['Control']});
            expected++;
            await page.waitForFunction(count=>tabs.size===count,expected);
            assert.equal(await page.evaluate(()=>tabs.get(activeId).url),origin+target);
            assert.equal(original.url(),origin+'/dashboard.html');
            assert.equal(await original.locator('#draft').inputValue(),'rascunho preservado');
            assert.equal(await original.evaluate(()=>fixtureIdentity),identity);
            assert.equal(await original.locator('body').evaluate(node=>node.classList.contains('jk-page-leaving')),false);
        }
        await page.evaluate(id=>activateTab(id),originalId);
        await original.locator('#rows td').click({modifiers:['Control']});
        await page.waitForFunction(count=>tabs.size===count,++expected);
        const salesUrl=new URL(await page.evaluate(()=>tabs.get(activeId).url));
        assert.equal(salesUrl.pathname,'/vendas_sku.html');
        assert.equal(salesUrl.searchParams.get('sku'),'SKU & 1');
        assert.equal(salesUrl.searchParams.get('loja'),'Loja & Peças');
        assert.equal(salesUrl.searchParams.get('data_inicio'),'2026-11-01');
        assert.equal(salesUrl.searchParams.get('data_fim'),'2026-11-12');
        assert.equal(salesUrl.searchParams.get('unidade_negocio'),'Canal & A');
        assert.equal(await original.locator('#skuLoadingOverlay').getAttribute('aria-hidden'),'true');
        await page.evaluate(id=>activateTab(id),originalId);
        await original.locator('#action').click({modifiers:['Control']});
        assert.equal(await original.evaluate(()=>actionCount),1);
        await original.evaluate(()=>new Promise(resolve=>setTimeout(resolve,30)));
        assert.equal(await original.evaluate(()=>JKTabNavigation.openIfRequested('/late.html')),false,'Ctrl cannot leak into a later save/login redirect');
        await original.locator('#hash').click({modifiers:['Control']});
        assert.equal(await page.evaluate(()=>tabs.size),expected);
        // A parent without a registered frame and a registered frame targeting
        // an external origin must not create tabs through postMessage.
        await page.evaluate(origin=>window.postMessage({channel:'jk-open-module-tab',payload:{url:origin+'/forged.html'}},'*'),origin);
        await original.evaluate(()=>top.postMessage({channel:'jk-open-module-tab',payload:{url:'https://foreign.test/page.html'}},'*'));
        await original.evaluate(()=>top.postMessage({channel:'jk-open-module-tab',payload:{url:'javascript:alert(1)'}},'*'));
        await page.evaluate(()=>new Promise(resolve=>setTimeout(resolve,30)));
        assert.equal(await page.evaluate(()=>tabs.size),expected);
        await original.locator('#inline').click();
        await original.waitForURL(origin+target);
        assert.equal(await page.evaluate(()=>tabs.size),expected,'An ordinary click must keep using the original tab');
        assert.equal(popups.length,0,'Internal Ctrl+click must not create browser windows');
        assert.deepEqual(errors,[]);
        console.log('electron_ctrl_click_tabs: OK (links, inline/listener navigation, transitions, sales filters, drafts, duplicates, stale modifiers and message isolation)');
    } finally {await browser.close();}
}

if(require.main===module) main().catch(error=>{console.error(error);process.exitCode=1;});
