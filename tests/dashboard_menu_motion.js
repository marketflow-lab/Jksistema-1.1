'use strict';

// Execute: node tests/dashboard_menu_motion.js
// Exercita o controller inteiro em um DOM controlado, sem redes, relógio real ou browser.
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const rootDir = path.resolve(__dirname, '..');
const read = name => fs.readFileSync(path.join(rootDir, name), 'utf8');
const html = read('static/dashboard.html');
const sourceModules = html.match(/const modules = (\[[\s\S]*?\n    \]);/);
assert(sourceModules, 'o menu deve continuar oferecendo os módulos canônicos');
const modules = vm.runInNewContext('(' + sourceModules[1] + ')');
const iconsSource = read('static/dashboard/menu-icons.js');
const controllerSource = read('static/dashboard/menu-motion.js');
const iconWindow = {};
vm.runInNewContext(iconsSource, { window: iconWindow }, { filename: 'menu-icons.js' });
const icons = iconWindow.JKDashboardMenuIcons;
const STORAGE_KEY = 'jk_dashboard_icon_motion_paused';
const rendererStart = html.indexOf('    function normalizarBuscaDashboard(');
const rendererBind = html.indexOf("    document.getElementById('dashboard-menu-search')?.addEventListener", rendererStart);
const rendererEnd = html.indexOf('\n    renderizarCardsDashboard();', rendererBind);
assert(rendererStart >= 0 && rendererEnd > rendererStart, 'renderizador canônico da busca deve continuar disponível');
const rendererSource = html.slice(rendererStart, rendererEnd);
function sourceArray(name) {
  const match = html.match(new RegExp('const ' + name + ' = (\\[[\\s\\S]*?\\n    \\]);'));
  assert(match, 'array canônico ausente: ' + name);
  return vm.runInNewContext('(' + match[1] + ')');
}
const ACTIVE = 'dashboard-menu-motion-active';
const STAGE = 'dashboard-menu-motion-stage';

assert(icons, 'o mapa de ícones deve estar disponível antes do controller defer');
assert.deepStrictEqual(Object.keys(icons).sort(), Array.from(modules, mod => mod.key).sort());
const allIds = new Set();
for (const [key, svg] of Object.entries(icons)) {
  assert.strictEqual(typeof svg, 'string');
  assert(/<svg\b[^>]*viewBox="0 0 120 120"/.test(svg), key + ': SVG escalável');
  assert(!/<(?:script|foreignObject|animate(?:Transform|Motion)?|set)\b/i.test(svg), key + ': SVG estático com CSS controlado');
  assert(!/\bon[a-z]+\s*=|\b(?:href|xlink:href)\s*=\s*["'](?!#)|@import/i.test(svg), key + ': sem código ou recursos externos');
  assert(!/url\(\s*["']?(?!#)[^)]+\)/i.test(svg), key + ': sem imagens/fontes externas');
  const styles = (svg.match(/<style>([\s\S]*?)<\/style>/) || [])[1];
  assert(styles, key + ': animações controladas por CSS');
  const animatedRules = Array.from(styles.matchAll(/([^{}]+)\{[^{}]*animation\s*:/g), match => match[1]);
  assert(animatedRules.length && animatedRules.every(selector => selector.includes('.dashboard-menu') && selector.includes('.motion-active')), key + ': animações limitadas ao menu e ao estado ativo');
  const ids = Array.from(svg.matchAll(/\bid="([^"]+)"/g), match => match[1]);
  assert.strictEqual(ids.length, new Set(ids).size, key + ': IDs internos únicos');
  ids.forEach(id => {
    assert(!allIds.has(id), 'IDs não podem colidir entre os 20 cards: ' + id);
    allIds.add(id);
  });
  for (const match of svg.matchAll(/url\(#([^)]+)\)/g)) {
    assert(ids.includes(match[1]), key + ': referência local deve resolver: ' + match[1]);
  }
}

class Events {
  constructor() { this.listeners = new Map(); this.once = new Map(); }
  addEventListener(type, callback, options) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type).add(callback);
    if (options && options.once) {
      if (!this.once.has(type)) this.once.set(type, new Set());
      this.once.get(type).add(callback);
    }
  }
  removeEventListener(type, callback) {
    const set = this.listeners.get(type);
    if (set) set.delete(callback);
  }
  dispatch(type, values = {}) {
    const event = Object.assign({
      type, target: this, currentTarget: this, bubbles: false, defaultPrevented: false,
      preventDefault() { this.defaultPrevented = true; },
      stopPropagation() { this.stopped = true; },
    }, values);
    let target = this;
    do {
      event.currentTarget = target;
      for (const callback of Array.from(target.listeners.get(type) || [])) {
        if ((target.once.get(type) || new Set()).has(callback)) target.removeEventListener(type, callback);
        callback.call(target, event);
      }
      target = event.bubbles && !event.stopped ? target.parentNode : null;
    } while (target);
    return event;
  }
  dispatchEvent(event) { return !this.dispatch(event.type, event).defaultPrevented; }
  listenerCount() { return Array.from(this.listeners.values()).reduce((sum, set) => sum + set.size, 0); }
}

function camel(value) { return value.replace(/-([a-z])/g, (_, char) => char.toUpperCase()); }

class Element extends Events {
  constructor(tagName = 'div', ownerDocument) {
    super();
    this.tagName = tagName.toUpperCase();
    this.nodeType = 1;
    this.ownerDocument = ownerDocument;
    this.parentNode = null;
    this.children = [];
    this.dataset = {};
    this.attributes = {};
    this.hidden = false;
    this._text = '';
    this._classes = new Set();
    this._html = '';
    this.bounds = { top: 100, bottom: 240, left: 100, right: 260, width: 160, height: 140 };
    this.classList = {
      add: (...names) => names.forEach(name => this._classes.add(name)),
      remove: (...names) => names.forEach(name => this._classes.delete(name)),
      contains: name => this._classes.has(name),
      toggle: (name, force) => {
        const enabled = force === undefined ? !this._classes.has(name) : Boolean(force);
        enabled ? this._classes.add(name) : this._classes.delete(name);
        return enabled;
      },
    };
    const properties = new Map();
    this.style = {
      setProperty: (name, value) => properties.set(name, String(value)),
      removeProperty: name => properties.delete(name),
      getPropertyValue: name => properties.get(name) || '',
    };
  }
  get className() { return Array.from(this._classes).join(' '); }
  set className(value) { this._classes = new Set(String(value).split(/\s+/).filter(Boolean)); }
  get id() { return this.attributes.id || ''; }
  set id(value) { this.setAttribute('id', value); }
  get href() { return this.attributes.href || ''; }
  set href(value) { this.setAttribute('href', value); }
  get parentElement() { return this.parentNode instanceof Element ? this.parentNode : null; }
  get childNodes() { return this.children; }
  get firstElementChild() { return this.children[0] || null; }
  get isConnected() { return Boolean(this.ownerDocument && this.ownerDocument.body.contains(this)); }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  set textContent(value) { this.replaceChildren(); this._text = String(value); }
  get innerHTML() { return this._html; }
  set innerHTML(markup) {
    this.replaceChildren();
    this._html = String(markup);
    const stack = [this];
    for (const match of this._html.matchAll(/<\/?([A-Za-z][\w:-]*)\b([^>]*)>/g)) {
      if (match[0].startsWith('</')) { if (stack.length > 1) stack.pop(); continue; }
      const node = new Element(match[1], this.ownerDocument);
      for (const attr of match[2].matchAll(/([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')/g)) node.setAttribute(attr[1], attr[2] ?? attr[3]);
      stack[stack.length - 1].appendChild(node);
      if (!match[0].endsWith('/>')) stack.push(node);
    }
  }
  setAttribute(name, value) {
    if (name === 'class') this.className = value;
    else if (name.startsWith('data-')) this.dataset[camel(name.slice(5))] = String(value);
    else this.attributes[name] = String(value);
  }
  getAttribute(name) {
    if (name === 'class') return this.className;
    if (name.startsWith('data-')) return this.dataset[camel(name.slice(5))] ?? null;
    return this.attributes[name] ?? null;
  }
  hasAttribute(name) { return this.getAttribute(name) !== null; }
  removeAttribute(name) {
    if (name.startsWith('data-')) delete this.dataset[camel(name.slice(5))];
    else delete this.attributes[name];
  }
  appendChild(child) {
    if (child.parentNode) child.remove();
    child.parentNode = this;
    child.ownerDocument = this.ownerDocument;
    this.children.push(child);
    return child;
  }
  append(...children) { children.forEach(child => this.appendChild(child)); }
  insertBefore(child, reference) {
    if (child.parentNode) child.remove();
    const index = this.children.indexOf(reference);
    child.parentNode = this;
    child.ownerDocument = this.ownerDocument;
    this.children.splice(index < 0 ? this.children.length : index, 0, child);
    return child;
  }
  replaceChild(child, old) { this.insertBefore(child, old); old.remove(); return old; }
  replaceWith(child) { if (this.parentNode) this.parentNode.replaceChild(child, this); }
  replaceChildren(...children) {
    this.children.forEach(child => { child.parentNode = null; });
    this.children = [];
    this._text = '';
    children.forEach(child => this.appendChild(child));
  }
  remove() {
    if (!this.parentNode) return;
    const parent = this.parentNode;
    parent.children.splice(parent.children.indexOf(this), 1);
    this.parentNode = null;
  }
  contains(child) { return this === child || this.children.some(node => node.contains(child)); }
  matches(selector) {
    return selector.split(',').some(part => {
      let value = part.trim();
      const excluded = Array.from(value.matchAll(/:not\(([^)]+)\)/g), match => match[1]);
      if (excluded.some(item => this.matches(item))) return false;
      value = value.replace(/:not\([^)]+\)/g, '').replace(/:scope/g, '');
      const attributes = Array.from(value.matchAll(/\[([\w:-]+)(?:=["']?([^"'\]]+)["']?)?\]/g));
      if (attributes.some(match => match[2] === undefined ? !this.hasAttribute(match[1]) : this.getAttribute(match[1]) !== match[2])) return false;
      value = value.replace(/\[[^\]]+\]/g, '');
      const id = value.match(/#([\w-]+)/);
      if (id && this.id !== id[1]) return false;
      if (Array.from(value.matchAll(/\.([\w-]+)/g), match => match[1]).some(name => !this.classList.contains(name))) return false;
      const tag = value.match(/^[A-Za-z][\w:-]*/);
      return !tag || this.tagName === tag[0].toUpperCase();
    });
  }
  closest(selector) { for (let node = this; node instanceof Element; node = node.parentElement) if (node.matches(selector)) return node; return null; }
  querySelectorAll(selector) {
    const result = [];
    const selectors = selector.split(',').map(value => value.trim().split(/\s+(?![^\[]*\])/));
    function matchesChain(node, chain) {
      if (!node.matches(chain[chain.length - 1])) return false;
      let parent = node.parentElement;
      for (let index = chain.length - 2; index >= 0; index--) {
        while (parent && !parent.matches(chain[index])) parent = parent.parentElement;
        if (!parent) return false;
        parent = parent.parentElement;
      }
      return true;
    }
    function visit(node) {
      node.children.forEach(child => {
        if (selectors.some(chain => matchesChain(child, chain))) result.push(child);
        visit(child);
      });
    }
    visit(this);
    return result;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  getBoundingClientRect() { return this.bounds; }
  focus() {
    const document = this.ownerDocument;
    const old = document.activeElement;
    if (old === this) return;
    document.activeElement = document.body;
    if (old) { old.dispatch('blur'); old.dispatch('focusout', { bubbles: true }); }
    document.activeElement = this;
    this.dispatch('focus');
    this.dispatch('focusin', { bubbles: true });
  }
  blur() { if (this.ownerDocument.activeElement === this) this.ownerDocument.body.focus(); }
  click() {
    const event = this.dispatch('click', { bubbles: true });
    const anchor = this.closest('a[href]');
    if (anchor && !event.defaultPrevented) this.ownerDocument.navigations.push(anchor.href);
    return event;
  }
}

function createHarness(options = {}) {
  const stored = options.stored || new Map();
  const document = new Events();
  document.readyState = 'loading';
  document.hidden = Boolean(options.hidden);
  document.navigations = [];
  document.createElement = tag => new Element(tag, document);
  document.createElementNS = (_, tag) => document.createElement(tag);
  document.body = document.createElement('body');
  document.activeElement = document.body;
  document.querySelectorAll = selector => document.body.querySelectorAll(selector);
  document.querySelector = selector => document.body.querySelector(selector);
  document.getElementById = id => document.querySelector('#' + id);
  const main = document.createElement('main'); main.className = 'container dashboard-menu'; document.body.appendChild(main);
  const feedback = document.createElement('p'); feedback.id = 'dashboard-menu-feedback'; main.appendChild(feedback);
  const cardsRoot = document.createElement('div'); cardsRoot.id = 'cards'; main.appendChild(cardsRoot);
  const baselineHandlers = new Map();
  function makeCard(mod) {
    const card = document.createElement('a');
    card.className = 'card dashboard-menu-module'; card.dataset.key = mod.key; card.href = mod.href;
    const legacyClick = () => {};
    card.addEventListener('click', legacyClick); baselineHandlers.set(card, legacyClick);
    const atlas = document.createElement('span'); atlas.className = 'dashboard-menu-illustration'; card.appendChild(atlas);
    const label = document.createElement('h3'); label.textContent = mod.title; card.appendChild(label);
    return card;
  }
  const initialModules = options.modules || modules;
  initialModules.forEach(mod => cardsRoot.appendChild(makeCard(mod)));
  let now = 0, nextTimer = 0;
  const timers = new Map();
  function schedule(callback, delay, recurring, args) {
    const id = ++nextTimer;
    timers.set(id, { callback, delay: Math.max(1, Number(delay) || 0), due: now + Math.max(1, Number(delay) || 0), recurring, args });
    return id;
  }
  function tick(ms) {
    const end = now + ms;
    let steps = 0;
    while (true) {
      const ready = Array.from(timers.entries()).filter(([, timer]) => timer.due <= end).sort((a, b) => a[1].due - b[1].due)[0];
      if (!ready) break;
      assert(++steps < 10000, 'o controller não deve criar um loop de agendamento');
      const [id, timer] = ready; now = timer.due;
      if (!timer.recurring) timers.delete(id);
      timer.callback(...timer.args);
      if (timer.recurring && timers.get(id) === timer) timer.due += timer.delay;
    }
    now = end;
  }
  const mutationObservers = [], intersectionObservers = [];
  class Mutation {
    constructor(callback) { this.callback = callback; this.disconnected = false; mutationObservers.push(this); }
    observe(target, config) { this.target = target; this.config = config; }
    disconnect() { this.disconnected = true; }
  }
  class Intersection {
    constructor(callback) { this.callback = callback; this.targets = new Set(); this.disconnected = false; intersectionObservers.push(this); }
    observe(target) { this.targets.add(target); }
    unobserve(target) { this.targets.delete(target); }
    disconnect() { this.targets.clear(); this.disconnected = true; }
    emit(targets, visible) {
      this.callback(targets.map(target => ({ target, isIntersecting: visible, intersectionRatio: visible ? 1 : 0, boundingClientRect: target.bounds })), this);
    }
  }
  const reduced = new Events(); reduced.matches = Boolean(options.reduced); reduced.media = '(prefers-reduced-motion: reduce)';
  reduced.addListener = fn => reduced.addEventListener('change', fn);
  reduced.removeListener = fn => reduced.removeEventListener('change', fn);
  const windowEvents = new Events();
  const context = {
    console, document, Element, HTMLElement: Element, Node: { ELEMENT_NODE: 1 },
    JKDashboardMenuIcons: options.withIcons === false ? undefined : icons,
    MutationObserver: Mutation,
    IntersectionObserver: options.withIntersection === false ? undefined : Intersection,
    innerHeight: 900, innerWidth: 1400,
    localStorage: {
      getItem(key) { if (options.storageBlocked) throw new Error('storage denied'); return stored.get(key) ?? null; },
      setItem(key, value) { if (options.storageBlocked) throw new Error('storage denied'); stored.set(key, String(value)); },
      removeItem(key) { stored.delete(key); },
    },
    matchMedia(query) { return query.includes('prefers-reduced-motion') ? reduced : { matches: true, addEventListener() {}, removeEventListener() {} }; },
    setTimeout: (fn, ms, ...args) => schedule(fn, ms, false, args),
    setInterval: (fn, ms, ...args) => schedule(fn, ms, true, args),
    clearTimeout: id => timers.delete(id), clearInterval: id => timers.delete(id),
    requestAnimationFrame: fn => schedule(() => fn(now), 16, false, []),
    cancelAnimationFrame: id => timers.delete(id),
    performance: { now: () => now },
    addEventListener: windowEvents.addEventListener.bind(windowEvents),
    removeEventListener: windowEvents.removeEventListener.bind(windowEvents),
    navigator: { maxTouchPoints: 0 },
  };
  context.window = context;
  vm.createContext(context);
  vm.runInContext(controllerSource, context, { filename: 'menu-motion.js' });
  document.readyState = 'complete'; document.dispatch('DOMContentLoaded');
  tick(30);
  function cards() { return cardsRoot.querySelectorAll('.card.dashboard-menu-module'); }
  function stage(card) { return card.querySelector('.' + STAGE); }
  function visible(targets = cards(), value = true) {
    intersectionObservers.forEach(observer => observer.emit(targets.filter(card => observer.targets.has(card)), value));
    tick(30);
  }
  function notifyRoot(removedNodes = []) {
    mutationObservers.forEach(observer => {
      if (!observer.disconnected && observer.target === cardsRoot) observer.callback([{ type: 'childList', target: cardsRoot, addedNodes: cards(), removedNodes }], observer);
    });
    tick(30);
  }
  function rerender(nextModules) {
    const removedNodes = cards();
    cardsRoot.replaceChildren(...nextModules.map(makeCard));
    notifyRoot(removedNodes);
  }
  return {
    context, document, main, cardsRoot, stored, reduced, timers, intersectionObservers, baselineHandlers,
    cards, stage, tick, visible, rerender, notifyRoot,
    dispatchWindow: (type, values) => windowEvents.dispatch(type, values),
    mutationObservers, windowEvents,
    active: () => cards().filter(card => card.classList.contains(ACTIVE)),
    pauseButton: () => document.getElementById('dashboard-menu-motion-toggle'),
    hide(value) { document.hidden = value; document.dispatch('visibilitychange'); tick(30); },
    reduce(value) { reduced.matches = value; reduced.dispatch('change', { matches: value }); tick(30); },
  };
}

function assertCardsPreserved(harness, expected) {
  const cards = harness.cards();
  assert.strictEqual(cards.length, expected.length, 'animação não cria módulos fora da seleção autorizada');
  cards.forEach((card, index) => {
    assert.strictEqual(card.dataset.key, expected[index].key);
    assert.strictEqual(card.href, expected[index].href, 'rota deve sobreviver à troca do atlas pelo SVG');
    assert.strictEqual(card.querySelector('h3').textContent, expected[index].title, 'rótulo deve ser preservado');
    assert.strictEqual(card.querySelectorAll('.' + STAGE).length, 1, 'cada card tem uma cena');
    assert.strictEqual(harness.stage(card).querySelectorAll('svg').length, 1, 'cada cena tem um SVG');
  });
}

const full = createHarness();
full.visible();
full.tick(6000);
assertCardsPreserved(full, modules);
assert(full.active().length > 0, 'demonstração deve animar os cards visíveis');
for (const card of full.cards()) {
  const event = card.querySelector('svg').click();
  assert.strictEqual(event.defaultPrevented, false, 'ícone não pode interceptar a navegação');
}
assert.deepStrictEqual(full.document.navigations, Array.from(modules, mod => mod.href));

const limitedModules = [modules.find(mod => mod.key === 'favoritos'), modules.find(mod => mod.key === 'cadastro')];
const limited = createHarness({ modules: limitedModules });
limited.visible();
assertCardsPreserved(limited, limitedModules);
const previous = limited.cards();
const intervalCount = Array.from(limited.timers.values()).filter(timer => timer.recurring).length;
const nextModules = [modules.find(mod => mod.key === 'avant')];
limited.rerender(nextModules);
limited.visible();
assertCardsPreserved(limited, nextModules);
for (const oldCard of previous) {
  assert.strictEqual(oldCard.listenerCount(), 1, 'recriação limpa só os handlers do controller e preserva o click anterior');
  assert(oldCard.listeners.get('click').has(limited.baselineHandlers.get(oldCard)));
  assert(limited.intersectionObservers.every(observer => !observer.targets.has(oldCard)), 'observer não pode reter card removido');
}
limited.rerender(nextModules);
limited.visible();
assertCardsPreserved(limited, nextModules);
assert.strictEqual(Array.from(limited.timers.values()).filter(timer => timer.recurring).length, intervalCount, 'pesquisa/recriação não duplica o ciclo');
limited.cards()[0].focus();
assert(limited.stage(limited.cards()[0]).classList.contains('motion-active'), 'card recriado responde ao teclado');

const pause = createHarness();
pause.visible();
pause.tick(6000);
const toggle = pause.pauseButton();
assert(toggle && toggle.tagName === 'BUTTON', 'controle acessível de pausa deve existir');
toggle.click();
assert.strictEqual(pause.stored.get(STORAGE_KEY), '1');
assert.strictEqual(toggle.getAttribute('aria-pressed'), 'true');
const stopped = new Set(pause.active());
const inactive = pause.cards().find(card => !stopped.has(card));
assert(inactive, 'fixtures precisam de um card fora do ciclo corrente');
inactive.dispatch('pointerenter');
inactive.focus();
pause.tick(12000);
assert.deepStrictEqual(new Set(pause.active()), stopped, 'pausa congela a cena e bloqueia novas ativações por relógio/mouse/foco');
inactive.click();
assert.strictEqual(pause.document.navigations.at(-1), inactive.href, 'pausa não altera o click do menu');
const reloaded = createHarness({ stored: pause.stored });
reloaded.visible(); reloaded.tick(12000);
assert.strictEqual(reloaded.pauseButton().getAttribute('aria-pressed'), 'true', 'pausa sobrevive à nova página');
assert.strictEqual(reloaded.active().length, 0, 'preferência persistida impede autoplay');
reloaded.pauseButton().click();
reloaded.visible(); reloaded.tick(6000);
assert.strictEqual(reloaded.stored.get(STORAGE_KEY), '0');
assert(reloaded.active().length > 0, 'Retomar reativa somente as cenas elegíveis');

const background = createHarness();
background.visible(); background.tick(6000);
background.hide(true);
assert.strictEqual(background.main.dataset.motionHidden, 'true');
const beforeHidden = new Set(background.active());
const hiddenCard = background.cards().find(card => !beforeHidden.has(card));
hiddenCard.dispatch('pointerenter');
background.tick(12000);
assert(background.active().every(card => beforeHidden.has(card)), 'aba oculta não inicia outra cena');
background.hide(false); background.visible(); background.tick(6000);
assert.strictEqual(background.main.dataset.motionHidden, 'false');
assert(background.active().length > 0);

background.reduce(true);
assert.strictEqual(background.main.dataset.motionReduced, 'true');
assert.strictEqual(background.active().length, 0, 'movimento reduzido remove a atividade das cenas');
background.cards()[0].dispatch('pointerenter');
background.cards()[0].focus();
background.tick(12000);
assert.strictEqual(background.active().length, 0, 'mouse/foco/relógio não ultrapassam movimento reduzido');
background.reduce(false); background.visible(); background.tick(6000);
assert(background.active().length > 0);
const reducedStart = createHarness({ reduced: true });
reducedStart.visible(); reducedStart.tick(12000);
assert.strictEqual(reducedStart.active().length, 0, 'movimento reduzido também vale desde o primeiro carregamento');

const offscreen = createHarness();
offscreen.visible(); offscreen.tick(6000);
offscreen.visible(offscreen.cards(), false);
offscreen.tick(12000);
assert.strictEqual(offscreen.active().length, 0, 'cenas fora da tela precisam parar');
offscreen.cards()[0].dispatch('pointerenter');
assert.strictEqual(offscreen.active().length, 0, 'hover não vence a suspensão por visibilidade');
offscreen.visible(); offscreen.tick(6000);
assert(offscreen.active().length > 0, 'cenas retornam quando entram na tela');

const pausedReduced = createHarness({ stored: new Map([[STORAGE_KEY, '1']]) });
pausedReduced.reduce(true); pausedReduced.reduce(false);
pausedReduced.visible(); pausedReduced.tick(12000);
assert.strictEqual(pausedReduced.stored.get(STORAGE_KEY), '1', 'preferência de acessibilidade não sobrescreve a pausa do usuário');
assert.strictEqual(pausedReduced.active().length, 0);

const blockedStorage = createHarness({ storageBlocked: true });
blockedStorage.visible(); blockedStorage.tick(6000);
assertCardsPreserved(blockedStorage, modules);
blockedStorage.pauseButton().click();
assert.strictEqual(blockedStorage.pauseButton().getAttribute('aria-pressed'), 'true', 'storage indisponível não impede pausa local');


const searchMenu = createHarness({ modules: limitedModules });
const searchInput = searchMenu.document.createElement('input');
searchInput.id = 'dashboard-menu-search'; searchInput.value = '';
searchMenu.main.appendChild(searchInput);
searchMenu.context.dashboardMenuGroups = sourceArray('dashboardMenuGroups');
searchMenu.context.dashboardMenuIllustrationKeys = sourceArray('dashboardMenuIllustrationKeys');
const searchModules = [
  modules.find(mod => mod.key === 'analise_promo'),
  modules.find(mod => mod.key === 'favoritos'),
  modules.find(mod => mod.key === 'cadastro'),
];
searchMenu.context.enabled = searchModules;
vm.runInContext(rendererSource, searchMenu.context, { filename: 'dashboard canonical renderer' });
vm.runInContext('renderizarCardsDashboard()', searchMenu.context);
searchMenu.notifyRoot(); searchMenu.visible();
assert.strictEqual(searchMenu.cards().length, 3);
searchInput.value = 'promoÇÃO';
searchInput.dispatch('input');
searchMenu.notifyRoot(); searchMenu.visible();
assert.deepStrictEqual(searchMenu.cards().map(card => card.dataset.key), ['analise_promo'], 'busca real normaliza acentos e recria o módulo permitido');
assert.strictEqual(searchMenu.cards()[0].href, searchModules[0].href);
assert.strictEqual(searchMenu.stage(searchMenu.cards()[0]).querySelectorAll('svg').length, 1);
const searchedCard = searchMenu.cards()[0];
searchInput.value = 'sem resultado possível';
searchInput.dispatch('input');
searchMenu.notifyRoot(); searchMenu.tick(6000);
assert.strictEqual(searchMenu.cards().length, 0, 'busca sem resultado desmonta todos os ícones');
assert.strictEqual(searchedCard.listenerCount(), 0, 'ícone removido pela busca perde os handlers de movimento');
const clearSearch = searchMenu.cardsRoot.querySelector('.dashboard-menu-empty button');
assert(clearSearch, 'estado vazio mantém a ação canônica de limpar busca');
clearSearch.click();
searchMenu.notifyRoot(); searchMenu.visible();
assert.strictEqual(searchMenu.cards().length, 3, 'limpar busca restaura apenas os módulos habilitados');
assert.strictEqual(searchMenu.document.activeElement, searchInput, 'ação canônica devolve foco ao campo');
searchMenu.cards().forEach(card => {
  assert.strictEqual(searchMenu.stage(card).querySelectorAll('svg').length, 1);
  assert.strictEqual(card.querySelector('svg').click().defaultPrevented, false);
});
assert.deepStrictEqual(searchMenu.document.navigations, searchModules.map(mod => mod.href));

const crossTab = createHarness();
crossTab.visible(); crossTab.tick(6000);
crossTab.stored.set(STORAGE_KEY, '1');
crossTab.dispatchWindow('storage', { key: STORAGE_KEY, newValue: '1' });
assert.strictEqual(crossTab.pauseButton().getAttribute('aria-pressed'), 'true', 'pausa sincroniza entre abas');
const crossTabPaused = new Set(crossTab.active());
crossTab.tick(12000);
assert.deepStrictEqual(new Set(crossTab.active()), crossTabPaused);
crossTab.stored.set(STORAGE_KEY, '0');
crossTab.dispatchWindow('storage', { key: STORAGE_KEY, newValue: '0' });
crossTab.tick(6000);
assert.strictEqual(crossTab.pauseButton().getAttribute('aria-pressed'), 'false');

const cached = createHarness();
cached.visible();
const cachedCard = cached.cards()[0];
const beforeCacheListeners = cachedCard.listenerCount();
const beforeCacheIntervals = Array.from(cached.timers.values()).filter(timer => timer.recurring).length;
cached.dispatchWindow('pagehide', { persisted: true });
assert.strictEqual(cached.main.dataset.motionHidden, 'true', 'BFCache congela a apresentação');
assert.strictEqual(cachedCard.listenerCount(), beforeCacheListeners, 'BFCache retém os handlers para retomada');
cached.dispatchWindow('pageshow', { persisted: true });
assert.strictEqual(cached.main.dataset.motionHidden, 'false');
assert.strictEqual(cachedCard.listenerCount(), beforeCacheListeners, 'BFCache não duplica handlers');
assert.strictEqual(Array.from(cached.timers.values()).filter(timer => timer.recurring).length, beforeCacheIntervals, 'BFCache não duplica timers');
cached.dispatchWindow('pagehide', { persisted: false });
assert.strictEqual(cached.timers.size, 0, 'navegação definitiva limpa o ciclo');
assert(cached.mutationObservers.every(observer => observer.disconnected));
assert(cached.intersectionObservers.every(observer => observer.disconnected && observer.targets.size === 0));
assert.strictEqual(cached.reduced.listenerCount(), 0);
assert.strictEqual((cached.document.listeners.get('visibilitychange') || new Set()).size, 0);
assert.strictEqual(cached.windowEvents.listenerCount(), 0);
cached.cards().forEach(card => {
  assert.strictEqual(card.listenerCount(), 1, 'cleanup preserva o click anterior de cada módulo');
  assert(!card.classList.contains(ACTIVE));
});
const gone = cached.cards()[0];
gone.dispatch('pointerenter'); cached.tick(12000);
assert(!gone.classList.contains(ACTIVE), 'card antigo não reativa depois do cleanup');
gone.querySelector('svg').click();
assert.strictEqual(cached.document.navigations.at(-1), gone.href);

const missingIcons = createHarness({ withIcons: false });
assert.strictEqual(missingIcons.pauseButton(), null);
assert.strictEqual(missingIcons.cards().length, modules.length, 'falha de assets preserva o menu');
assert(missingIcons.cards().every(card => card.querySelector('.dashboard-menu-illustration')));
missingIcons.cards()[0].click();
assert.strictEqual(missingIcons.document.navigations[0], modules[0].href);

console.log('dashboard menu motion: 20 icons; routes, canonical search, rerender, cleanup, persisted/cross-tab pause, BFCache, background, reduced motion and viewport: OK');
