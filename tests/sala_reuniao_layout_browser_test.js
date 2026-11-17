'use strict';

// Browser regression using fictional content and fully intercepted requests.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const root = path.resolve(__dirname, '..');
function loadPlaywright() {
  try { return require('playwright'); }
  catch (error) {
    if (error.code !== 'MODULE_NOT_FOUND') throw error;
    const commonGitDir = execFileSync('git', ['rev-parse', '--path-format=absolute', '--git-common-dir'],
      { cwd: root, encoding: 'utf8' }).trim();
    return require(require.resolve('playwright', { paths: [path.dirname(commonGitDir)] }));
  }
}
const { chromium } = loadPlaywright();
const origin = 'https://meeting.test';
const token = 'synthetic-meeting-token';
const html = fs.readFileSync(path.join(root, 'static', 'sala_reuniao.html'), 'utf8');
const fixtureName = 'Pessoa Exemplo com Nome Longo para Conferência';
const roomUrl = index => 'https://fixture.daily.co/room-' + index;
const rooms = Array.from({ length: 12 }, (_, index) => ({
  name: index === 0 ? 'sala-com-nome-longo-'.repeat(5) : 'room-' + (index + 1),
  url: roomUrl(index + 1), ongoing: index % 3 !== 0, duration_seconds: index * 180,
  privacy: index % 2 ? 'private' : 'public', can_end: index === 0,
  participants_count: index % 3 ? 4 : 0,
  participant_names: index % 3 ? [fixtureName, 'Outra Pessoa de Exemplo',
    'NomeSemEspacos'.repeat(10), '<img src=x onerror="window.fixtureInjected=true">'] : [],
}));
const clone = value => JSON.parse(JSON.stringify(value));
const dailyMock = `(${function installDailyMock() {
  const state = window.fixtureDaily = { joins: [], leaves: 0, destroys: 0, sidebar: [], frames: [] };
  window.Daily = { createFrame(stage, options) {
    const listeners = new Map();
    const iframe = document.createElement('iframe');
    iframe.title = 'Synthetic Daily call';
    Object.assign(iframe.style, options.iframeStyle);
    stage.appendChild(iframe);
    const frame = {
      audio: true, video: true,
      on(name, listener) {
        if (!listeners.has(name)) listeners.set(name, []);
        listeners.get(name).push(listener);
        return frame;
      },
      emit(name, payload) { (listeners.get(name) || []).forEach(listener => listener(payload || {})); },
      async join(joinOptions) {
        state.joins.push(joinOptions);
        frame.emit('joined-meeting');
        return { meetingState: 'joined-meeting' };
      },
      async leave() { state.leaves += 1; },
      destroy() { state.destroys += 1; iframe.remove(); },
      participantCounts() { return { present: 2 }; },
      participants() { return { local: { user_name: 'Pessoa Exemplo' }, remote: { user_name: 'Outra Pessoa' } }; },
      localAudio() { return frame.audio; }, localVideo() { return frame.video; },
      async setLocalAudio(value) { frame.audio = value; },
      async setLocalVideo(value) { frame.video = value; },
      async setUserName() {}, async setDailyLang() {},
      async setSidebarView(view) { state.sidebar.push(view); },
      async startScreenShare() { frame.emit('local-screen-share-started'); },
      async stopScreenShare() { frame.emit('local-screen-share-stopped'); },
    };
    state.frames.push(frame);
    return frame;
  } };
}})();`;

async function json(route, payload, status = 200) {
  await route.fulfill({ status, contentType: 'application/json; charset=utf-8', body: JSON.stringify(payload) });
}
async function noOverflow(page, label) {
  const sizes = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    document: document.documentElement.scrollWidth,
    body: document.body.scrollWidth,
  }));
  assert(sizes.document <= sizes.viewport + 1 && sizes.body <= sizes.viewport + 1,
    label + ' deve caber na largura disponível: ' + JSON.stringify(sizes));
}
async function scenario(browser, options = {}) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 940 } });
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  const state = { rooms: clone(options.rooms || []), admin: !!options.admin,
    minutes: options.minutes || 0, requests: [], errors: [], unexpected: [], confirmations: [],
    acceptConfirmation: true, creates: [], ends: [], listReads: 0 };
  page.on('pageerror', error => state.errors.push(error.message));
  page.on('dialog', async dialog => {
    state.confirmations.push(dialog.message());
    if (state.acceptConfirmation) await dialog.accept(); else await dialog.dismiss();
  });
  await context.addInitScript(() => {
    localStorage.setItem('permissions', JSON.stringify({ sala_reuniao: true }));
    localStorage.setItem('access_token', 'synthetic-meeting-token');
    localStorage.setItem('user_data', JSON.stringify({ name: 'Pessoa Exemplo', client_id: 'fixture-client' }));
    window.fixtureCopies = [];
    Object.defineProperty(navigator, 'clipboard', { configurable: true,
      value: { writeText: async text => window.fixtureCopies.push(text) } });
    window.electronAPI = { chooseDisplayMediaSource: async () => ({ success: true,
      source: { id: 'fixture-screen', label: 'Tela de exemplo' } }) };
  });
  const usage = () => ({ participant_seconds: state.minutes * 60, free_participant_minutes: 10000,
    block_participant_minutes: 9950, resets_at: '2026-12-01T00:00:00Z' });
  await context.route('**/*', async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.hostname === 'unpkg.com') return route.fulfill({ contentType: 'application/javascript', body: dailyMock });
    if (url.origin !== origin) {
      state.unexpected.push(request.method() + ' external request');
      return route.fulfill({ status: 404, body: 'Blocked synthetic test request' });
    }
    if (url.pathname === '/sala_reuniao.html') return route.fulfill({ contentType: 'text/html; charset=utf-8', body: html });
    if (url.pathname === '/auth.js') return route.fulfill({ contentType: 'application/javascript', body:
      'window.verificarSessao=()=>true;window.obterToken=()=>localStorage.getItem("access_token");' });
    if (url.pathname === '/favicon.ico') return route.fulfill({ status: 204 });
    if (url.pathname.startsWith('/api/')) {
      assert.equal(request.headers().authorization, 'Bearer ' + token, 'As APIs devem receber a sessão de exemplo.');
      state.requests.push({ path: url.pathname, method: request.method() });
      if (url.pathname === '/api/auth/me') return json(route, { success: true,
        user_data: { name: 'Pessoa Exemplo', client_id: 'fixture-client' }, permissions: { sala_reuniao: true } });
      if (url.pathname === '/api/sala-reuniao/status') return json(route, { daily_configurado: true });
      if (url.pathname === '/api/sala-reuniao/uso-mensal') return json(route, { success: true, usage: usage() });
      if (url.pathname === '/api/sala-reuniao/uso-mensal/adicionar') {
        const payload = request.postDataJSON();
        state.minutes += payload.participant_seconds / 60;
        return json(route, { success: true, usage: usage() });
      }
      if (url.pathname === '/api/sala-reuniao/salas' && request.method() === 'POST') {
        const payload = request.postDataJSON();
        state.creates.push(payload);
        return json(route, { success: true, room: { url: 'https://fixture.daily.co/new-private-room',
          name: 'new-private-room', privacy: payload.privacidade }, host_token: 'synthetic-host-token' });
      }
      if (['/api/sala-reuniao/reunioes-ativas', '/api/sala-reuniao/salas-ativas', '/api/sala-reuniao/salas'].includes(url.pathname)) {
        state.listReads += 1;
        return json(route, { success: true, rooms: state.rooms, is_admin: state.admin });
      }
      if (url.pathname.includes('/encerrar')) {
        const payload = request.postDataJSON();
        state.ends.push(payload);
        state.rooms = state.rooms.filter(room => room.name !== payload.room_name && room.url !== payload.room_url);
        return json(route, { success: true, rooms: state.rooms });
      }
    }
    const relative = decodeURIComponent(url.pathname).replace(/^\/+/, '');
    const asset = path.resolve(root, 'static', relative);
    if (asset.startsWith(path.join(root, 'static') + path.sep) && fs.existsSync(asset) && fs.statSync(asset).isFile()) {
      const types = { '.css': 'text/css', '.js': 'application/javascript', '.svg': 'image/svg+xml', '.png': 'image/png' };
      return route.fulfill({ contentType: types[path.extname(asset)] || 'application/octet-stream', body: fs.readFileSync(asset) });
    }
    state.unexpected.push(request.method() + ' ' + url.pathname);
    return route.fulfill({ status: 404, body: 'Unexpected synthetic test route' });
  });
  await page.goto(origin + '/sala_reuniao.html' + (options.query || ''));
  await page.waitForFunction(() => document.getElementById('dailyStatusBadge').classList.contains('ok'));
  if (!options.query) await page.waitForFunction(() => !/Carregando|Buscando/.test(document.getElementById('roomsStatus').textContent));
  return { page, state, context, async close() {
    assert.deepEqual(state.errors, [], 'A página não deve produzir erros JavaScript.');
    assert.deepEqual(state.unexpected, [], 'Nenhuma solicitação deve escapar dos mocks.');
    await context.close();
  } };
}
async function inCall(page) {
  await page.waitForFunction(() => document.body.classList.contains('in-call') && !document.getElementById('audioBtn').disabled);
  assert.equal(await page.locator('#meetingStage iframe').count(), 1, 'A chamada deve continuar usando o iframe Daily.');
}
async function returnToRooms(page) {
  await page.locator('#callRoomsBtn').click();
  await page.waitForFunction(() => !document.body.classList.contains('in-call') && !document.getElementById('roomsView').hidden);
}

(async () => {
  assert.equal(html, fs.readFileSync(path.join(root, 'sala_reuniao.html'), 'utf8'), 'As duas páginas devem oferecer o mesmo layout.');
  const browser = await chromium.launch({ headless: true,
    ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}) });
  try {
    const empty = await scenario(browser);
    assert.equal(await empty.page.locator('#roomsView').isVisible(), true);
    assert.equal(await empty.page.locator('#meetingView').isHidden(), true);
    assert.equal(await empty.page.locator('#activeRoomsList [data-room-action]').count(), 0);
    assert.match(await empty.page.locator('#activeRoomsList').innerText(), /Nenhuma|nenhuma/);
    assert.equal(await empty.page.locator('#endAllRoomsBtn').isHidden(), true);
    assert.equal(empty.state.creates.length, 0, 'Abrir a tela deve apenas consultar as salas.');
    await empty.page.locator('#quickDailyUrl').fill('https://invalid.example/room');
    await empty.page.locator('#quickJoinBtn').click();
    assert.equal(await empty.page.evaluate(() => window.fixtureDaily.joins.length), 0);
    assert.equal(await empty.page.locator('#roomsView').isVisible(), true);
    await empty.close();

    const active = await scenario(browser, { rooms });
    const { page, state } = active;
    assert.equal(await page.locator('#activeRoomsList .room-card').count(), rooms.length);
    assert.equal(await page.locator('#activeRoomsList [data-room-action="end"]').count(), 1,
      'O encerramento deve respeitar a permissão de cada sala.');
    assert.equal(await page.locator('#endAllRoomsBtn').isHidden(), true, 'Encerrar todas deve permanecer restrito a administradores.');
    assert.equal(await page.locator('#activeRoomsList img').count(), 0, 'Nomes recebidos devem ser exibidos como texto.');
    assert.equal(await page.evaluate(() => window.fixtureInjected), undefined);
    for (const width of [1440, 1024, 768, 390, 320]) {
      await page.setViewportSize({ width, height: 940 });
      await noOverflow(page, 'Lista de salas em ' + width + ' px');
      for (const selector of ['#newMeetingBtn', '#quickJoinBtn', '#refreshRoomsBtn']) {
        const button = page.locator(selector);
        await button.scrollIntoViewIfNeeded();
        assert.equal(await button.isVisible(), true, selector + ' deve permanecer acessível.');
      }
    }
    await page.setViewportSize({ width: 1440, height: 940 });
    await page.locator('[data-room-action="copy"]').first().click();
    await page.waitForFunction(() => window.fixtureCopies.length === 1);
    assert.equal(await page.evaluate(() => window.fixtureCopies[0]), rooms[0].url);
    state.acceptConfirmation = false;
    await page.locator('[data-room-action="end"]').first().click();
    assert.equal(state.ends.length, 0, 'Cancelar a confirmação deve preservar a sala.');
    state.acceptConfirmation = true;
    await page.locator('[data-room-action="end"]').first().click();
    await page.waitForFunction(expected => document.querySelectorAll('#activeRoomsList .room-card').length === expected, rooms.length - 1);
    assert.equal(state.ends[0].room_name, rooms[0].name);
    state.admin = true;
    await page.locator('#refreshRoomsBtn').click();
    await page.waitForFunction(() => !document.getElementById('endAllRoomsBtn').hidden);
    state.admin = false;
    await page.locator('#refreshRoomsBtn').click();
    await page.waitForFunction(() => document.getElementById('endAllRoomsBtn').hidden);

    await page.locator('input[name="startPrivacy"][value="private"]').check({ force: true });
    await page.locator('#newMeetingBtn').click();
    await inCall(page);
    assert.equal(state.creates.length, 1);
    assert.equal(state.creates[0].privacidade, 'private');
    assert.equal(state.creates[0].criar_token_host, true);
    assert.equal(state.creates[0].nome_host, 'Pessoa Exemplo');
    assert.equal(state.creates[0].habilitar_chat, true);
    assert.equal(state.creates[0].habilitar_compartilhar_tela, true);
    assert.equal(await page.evaluate(() => window.fixtureDaily.joins.at(-1).token), 'synthetic-host-token');
    for (const width of [1440, 1024, 768, 390, 320]) {
      await page.setViewportSize({ width, height: 740 });
      await noOverflow(page, 'Chamada em ' + width + ' px');
      for (const selector of ['#audioBtn', '#videoBtn', '#screenBtn', '#peopleBtn', '#chatBtn', '#callRoomsBtn', '#callLeaveBtn']) {
        await page.locator(selector).scrollIntoViewIfNeeded();
        const bounds = await page.locator(selector).boundingBox();
        assert(bounds && bounds.x >= -1 && bounds.x + bounds.width <= width + 1 && bounds.y >= -1 && bounds.y + bounds.height <= 741,
          selector + ' deve poder ser alcançado na chamada em ' + width + ' px.');
      }
    }
    await page.setViewportSize({ width: 1440, height: 940 });
    await page.locator('#audioBtn').click();
    assert.equal(await page.evaluate(() => window.fixtureDaily.frames.at(-1).audio), false);
    await page.locator('#videoBtn').click();
    assert.equal(await page.evaluate(() => window.fixtureDaily.frames.at(-1).video), false);
    await page.locator('#screenBtn').click();
    await page.waitForFunction(() => document.getElementById('screenBtn').classList.contains('is-on'));
    await page.locator('#screenBtn').click();
    await page.waitForFunction(() => !document.getElementById('screenBtn').classList.contains('is-on'));
    await page.locator('#peopleBtn').click();
    await page.locator('#chatBtn').click();
    assert.deepEqual(await page.evaluate(() => window.fixtureDaily.sidebar), ['participants', 'chat']);
    await returnToRooms(page);
    assert.equal(await page.locator('#meetingStage iframe').count(), 0);
    assert.equal(await page.evaluate(() => window.fixtureDaily.leaves), 1);
    await page.locator('#quickDailyUrl').fill('https://fixture.daily.co/invited-room');
    await page.locator('#quickDailyUrl').press('Enter');
    await inCall(page);
    assert.equal(await page.evaluate(() => window.fixtureDaily.joins.at(-1).url), 'https://fixture.daily.co/invited-room');
    await page.locator('#callLeaveBtn').click();
    await page.waitForFunction(() => !document.body.classList.contains('in-call'));
    assert.equal(await page.locator('#meetingStage iframe').count(), 0);
    assert.equal(await page.locator('#audioBtn').isDisabled(), true);
    await page.locator('#copyInviteBtn').click();
    assert.match(await page.evaluate(() => window.fixtureCopies.at(-1)), /https:\/\/fixture\.daily\.co\/invited-room/);
    await active.close();

    const invitation = await scenario(browser, { query: '?join=' + encodeURIComponent('https://fixture.daily.co/incoming?t=synthetic-invite-token') + '&room_name=convite&privacy=private' });
    await inCall(invitation.page);
    const joined = await invitation.page.evaluate(() => window.fixtureDaily.joins.at(-1));
    assert.equal(joined.url, 'https://fixture.daily.co/incoming');
    assert.equal(joined.token, 'synthetic-invite-token');
    assert.equal(joined.userName, 'Pessoa Exemplo');
    await returnToRooms(invitation.page);
    await invitation.close();

    const gate = await scenario(browser, { rooms: rooms.slice(0, 2), minutes: 9949 });
    assert.equal(await gate.page.locator('#newMeetingBtn').isEnabled(), true);
    gate.state.minutes = 9950;
    await gate.page.evaluate(() => loadMonthlyUsage());
    await gate.page.waitForFunction(() => document.getElementById('newMeetingBtn').disabled);
    for (const selector of ['#newMeetingBtn', '#quickJoinBtn', '#createRoomBtn', '#joinBtn']) {
      assert.equal(await gate.page.locator(selector).isDisabled(), true, selector + ' deve respeitar o limite preventivo.');
    }
    for (const button of await gate.page.locator('[data-room-action="join"]').all()) assert.equal(await button.isDisabled(), true);
    assert.match(await gate.page.locator('#roomsView [data-usage-lock]').innerText(), /9[.,]?950|9950/);
    assert.equal(gate.state.creates.length, 0);
    assert.equal(await gate.page.evaluate(() => window.fixtureDaily.joins.length), 0);
    const progress = gate.page.locator('#roomsView [data-usage-progress]');
    if (await progress.count()) assert.equal(Number(await progress.getAttribute('aria-valuenow')), 99.5);
    gate.state.minutes = 0;
    await gate.page.evaluate(() => loadMonthlyUsage());
    await gate.page.waitForFunction(() => !document.getElementById('newMeetingBtn').disabled);
    await gate.close();
    console.log('Sala de reunião: layout, responsividade e fluxos sintéticos OK.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
