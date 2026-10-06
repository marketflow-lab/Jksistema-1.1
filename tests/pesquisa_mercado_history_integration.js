 'use strict';
const assert=require('assert/strict');
const fs=require('fs');
const path=require('path');
const os=require('os');
const net=require('net');
const {spawn,execFile}=require('child_process');
const {performance}=require('perf_hooks');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..');

async function freePort(){const server=net.createServer();await new Promise(r=>server.listen(0,'127.0.0.1',r));const port=server.address().port;await new Promise(r=>server.close(r));return port;}
async function main(){
 const port=await freePort(),origin=`http://127.0.0.1:${port}`;
 const fixtureRoot=fs.mkdtempSync(path.join(os.tmpdir(),'jk-pm-history-e2e-'));
 const child=spawn('py',['-3',path.join(__dirname,'pesquisa_mercado_history_fixture.py'),String(port),fixtureRoot],{
  cwd:root,windowsHide:true,env:{...process.env,PYTHONPATH:root,PYTHONDONTWRITEBYTECODE:'1',PYTHONUTF8:'1'},stdio:['ignore','pipe','pipe']});
 let output='',browser,spawnError;child.stdout.on('data',s=>output=(output+s).slice(-12000));child.stderr.on('data',s=>output=(output+s).slice(-12000));
 child.on('error',error=>{spawnError=error;});
 try{
  let meta,lastProbeError='';
  const startupStarted=performance.now(),startupDeadline=startupStarted+30000;
  while(performance.now()<startupDeadline){
   if(spawnError)throw new Error(`Fixture spawn failed (${spawnError.code||'unknown'}): ${spawnError.message}`);
   if(child.exitCode!=null||child.signalCode!=null)throw new Error(`Fixture exited code=${child.exitCode} signal=${child.signalCode}: ${output}`);
   try{const timeout=Math.max(1,Math.ceil(Math.min(500,startupDeadline-performance.now())));const r=await fetch(origin+'/fixture/meta',{signal:AbortSignal.timeout(timeout)});if(r.ok){meta=await r.json();break;}lastProbeError=`HTTP ${r.status}`;}catch(error){lastProbeError=error.message;}
   const remaining=startupDeadline-performance.now();if(remaining>0)await new Promise(r=>setTimeout(r,Math.min(100,remaining)));
  }
  if(spawnError)throw new Error(`Fixture spawn failed (${spawnError.code||'unknown'}): ${spawnError.message}`);
  assert.ok(meta,`Fixture startup timed out after ${Math.round(performance.now()-startupStarted)} ms; pid=${child.pid||'unavailable'} code=${child.exitCode} signal=${child.signalCode}; last probe: ${lastProbeError}; output: ${output||'(empty)'}`);
  console.log(`Fixture synthetic SQLite/FastAPI ready in ${Math.round(performance.now()-startupStarted)} ms`);
  browser=await chromium.launch({headless:true});
  const context=await browser.newContext({viewport:{width:1440,height:960}});
  await context.addInitScript(()=>{
   localStorage.setItem('access_token','fixture-alice');window.__commands=[];
   const get=async()=> (await fetch('/api/pesquisa-mercado/jobs/latest',{headers:{Authorization:'Bearer fixture-alice'}})).json();
   const command=action=>async payload=>{window.__commands.push({action,payload});if(action==='pause')return (await fetch('/fixture/pause',{method:'POST',headers:{Authorization:'Bearer fixture-alice','Content-Type':'application/json'},body:JSON.stringify(payload)})).json();return get();};
   window.electronAPI={getPesquisaMercadoJobStatus:get,pausePesquisaMercadoJob:command('pause'),resumePesquisaMercadoJob:command('resume'),cancelPesquisaMercadoJob:command('cancel'),startPesquisaMercadoJob:command('start'),showPesquisaMercadoBrowser:command('browser'),onPesquisaMercadoProgress(cb){window.__pmProgress=cb;return()=>{};}};
  });
  const page=await context.newPage(),errors=[],mutations=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',req=>{if(new URL(req.url()).pathname.startsWith('/api/pesquisa-mercado')&&req.method()!=='GET')mutations.push(req.method()+' '+req.url());});
  await page.route('**/*',route=>new URL(route.request().url()).origin===origin?route.continue():route.abort());
  await page.goto(origin+'/pesquisa_mercado.html');
  await page.waitForFunction(()=>!document.getElementById('pm-active-research').hidden);
  await page.locator('#pm-mode-history').click();
  await page.locator(`#pm-history-list [data-consult-job="${meta.old}"]`).waitFor();
  await page.locator(`#pm-history-list [data-consult-job="${meta.old}"]`).click();
  await page.waitForFunction(()=>document.querySelectorAll('#pm-history-detail-rows tr').length===2);
  assert.match(await page.locator('#pm-history-detail-heading').innerText(),/Peças de Carros/);
  assert.match(await page.locator('#pm-history-detail-rows').innerText(),/400/);
  assert.deepEqual(await page.evaluate(()=>window.__commands),[],'historical consultation cannot execute commands');
  const progress=await (await fetch(origin+'/fixture/progress',{method:'POST'})).json();
  await page.evaluate(value=>window.__pmProgress(value),progress);
  assert.equal(await page.evaluate(()=>window.__jkPesquisaMercadoHistory.state.history.detailId),meta.old);
  assert.equal(await page.locator('#pm-panel-history').isVisible(),true);
  assert.match(await page.locator('#pm-history-detail-rows').innerText(),/400/,'active progress must preserve historical results');
  await page.locator('#pm-mode-history').focus();await page.keyboard.press('ArrowRight');
  assert.equal(await page.locator('#pm-mode-consolidated').getAttribute('aria-selected'),'true');
  await page.waitForFunction(()=>document.querySelectorAll('#pm-consolidated-rows tr').length===3);
  const bomba=page.locator('#pm-consolidated-rows tr').filter({hasText:'Bomba de combustível'});
  assert.match(await bomba.innerText(),/200/);assert.match(await bomba.innerText(),/2 pesquisa/);
  const sensor=page.locator('#pm-consolidated-rows tr').filter({hasText:'Sensor de temperatura'});
  assert.doesNotMatch(await sensor.locator('td').first().innerText(),/\d/,'latest pending observation has no rank');
  assert.match(await sensor.innerText(),/Avant Pro indisponiveis/);
  await bomba.locator('[data-origin-index]').click();
  await page.waitForFunction(()=>document.querySelectorAll('#pm-consolidated-origin-list [data-consult-job]').length===2);
  await page.locator(`#pm-consolidated-origin-list [data-consult-job="${meta.old}"]`).click();
  await page.waitForFunction(()=>document.querySelectorAll('#pm-history-detail-rows tr').length===2);
  assert.equal(await page.locator('#pm-panel-history').isVisible(),true);
  await page.locator('#pm-active-research-follow').click();
  await page.locator('#pm-global-pause').click();
  await page.waitForFunction(()=>window.__commands.length===1&&!document.getElementById('pm-global-resume').disabled);
  const commands=await page.evaluate(()=>window.__commands);
  assert.equal(commands[0].action,'pause');assert.equal(commands[0].payload.jobId,meta.current,'pause targets active job, never the historical selection');
  assert.deepEqual(mutations,[],'history and consolidated issue only API GETs');
  const artifacts=process.env.JK_HISTORY_SCREENSHOTS;
  if(artifacts){
   assert.ok(fs.existsSync(artifacts));
   await page.evaluate(()=>{const badge=document.createElement('span');badge.className='source-chip';badge.textContent='Dados ilustrativos de verificação';document.querySelector('.header-actions').appendChild(badge);});
   await page.locator('#pm-mode-history').click();
   await page.waitForFunction(({old,current})=>{
    const h=window.__jkPesquisaMercadoHistory.state.history;
    return h.loaded&&!h.busy&&!h.scopesBusy&&!h.detailBusy&&h.detailId===old&&h.detail&&h.detailItems.length===2&&h.items.some(job=>job.id===current&&job.status==='paused');
   },meta);
   assert.match(await page.locator(`#pm-history-list article:has([data-consult-job="${meta.current}"])`).innerText(),/Pausada/,'historical card must reflect the current paused research before capture');
   await page.screenshot({path:path.join(artifacts,'historico-1.0.177.png'),fullPage:true});
   await page.locator('#pm-mode-consolidated').click();
   await page.locator('#pm-consolidated-refresh').click();
   await page.waitForFunction(()=>{const c=window.__jkPesquisaMercadoHistory.state.consolidated;return c.loaded&&!c.busy&&!c.scopesBusy&&!c.originBusy;});
   await page.screenshot({path:path.join(artifacts,'consolidado-1.0.177.png'),fullPage:true});
  }
  await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'consolidated mobile overflow');await page.locator('#pm-mode-history').click();assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'history mobile overflow');
  assert.deepEqual(errors,[]);
  console.log('Pesquisa Mercado E2E SQLite/FastAPI/UI: leitura histórica, dedup/latest pending, origens, teclado, progresso independente, pausa ativa e 390 px OK');
 }finally{
  if(browser)await browser.close();
  if(child.exitCode==null&&child.pid){if(process.platform==='win32')await new Promise(resolve=>execFile('taskkill',['/PID',String(child.pid),'/T','/F'],{windowsHide:true},()=>resolve()));else child.kill('SIGTERM');}
  const resolved=fs.realpathSync(fixtureRoot),temporary=fs.realpathSync(os.tmpdir());const relative=path.relative(temporary,resolved);
  assert.ok(relative&&!relative.startsWith('..')&&!path.isAbsolute(relative)&&path.basename(resolved).startsWith('jk-pm-history-e2e-'));
  fs.rmSync(resolved,{recursive:true,force:true});
 }
}
main().catch(e=>{console.error(e);process.exitCode=1});
