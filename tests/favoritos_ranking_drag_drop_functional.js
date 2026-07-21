'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(
    path.join(root, 'static/favoritos/tabelas-layout/06-ranking-manual-historico-ui.js'),
    'utf8'
);
const renderSource = fs.readFileSync(
    path.join(root, 'static/favoritos/tabelas-layout/08-render-avant-mercadolivre.js'),
    'utf8'
);
const styleSource = fs.readFileSync(path.join(root, 'static/favoritos/styles.css'), 'utf8');

function extractFunction(name, context) {
    const marker = `function ${name}`;
    const markerAt = source.indexOf(marker);
    assert.ok(markerAt >= 0, `funcao ${name} ausente`);
    const paramsOpen = source.indexOf('(', markerAt);
    let paramsDepth = 0;
    let paramsClose = -1;
    for (let index = paramsOpen; index < source.length; index += 1) {
        const char = source[index];
        if (char === '(') paramsDepth += 1;
        if (char === ')' && --paramsDepth === 0) {
            paramsClose = index;
            break;
        }
    }
    const bodyOpen = source.indexOf('{', paramsClose);
    let depth = 0;
    let quote = '';
    let escaped = false;
    let end = -1;
    for (let index = bodyOpen; index < source.length; index += 1) {
        const char = source[index];
        if (quote) {
            if (escaped) escaped = false;
            else if (char === '\\') escaped = true;
            else if (char === quote) quote = '';
            continue;
        }
        if (char === '"' || char === "'" || char === '`') {
            quote = char;
            continue;
        }
        if (char === '{') depth += 1;
        if (char === '}' && --depth === 0) {
            end = index + 1;
            break;
        }
    }
    assert.ok(end > bodyOpen, `fim de ${name} ausente`);
    return vm.runInNewContext(`(${source.slice(markerAt, end)})`, context);
}

class FakeClassList {
    constructor() {
        this.values = new Set();
    }

    setFromString(value) {
        this.values = new Set(String(value || '').split(/\s+/).filter(Boolean));
    }

    add(...names) {
        names.filter(Boolean).forEach(name => this.values.add(name));
    }

    remove(...names) {
        names.forEach(name => this.values.delete(name));
    }

    toggle(name, force) {
        if (force === undefined) {
            if (this.values.has(name)) this.values.delete(name);
            else this.values.add(name);
            return this.values.has(name);
        }
        if (force) this.values.add(name);
        else this.values.delete(name);
        return !!force;
    }

    contains(name) {
        return this.values.has(name);
    }

    toString() {
        return [...this.values].join(' ');
    }
}

class FakeElement {
    constructor(tagName, documentRef) {
        this.tagName = String(tagName || '').toUpperCase();
        this.ownerDocument = documentRef;
        this.children = [];
        this.parentElement = null;
        this.dataset = {};
        this.attributes = {};
        this.listeners = new Map();
        this.classList = new FakeClassList();
        this.disabled = false;
        this.draggable = false;
        this.offsetHeight = 40;
        this.rect = { top: 100, height: 40 };
        this.textContent = '';
        this.innerHTML = '';
        this.title = '';
        this.type = '';
    }

    set className(value) {
        this.classList.setFromString(value);
    }

    get className() {
        return this.classList.toString();
    }

    appendChild(child) {
        child.parentElement = this;
        this.children.push(child);
        return child;
    }

    setAttribute(name, value) {
        this.attributes[name] = String(value);
    }

    getAttribute(name) {
        return this.attributes[name] || null;
    }

    addEventListener(type, handler) {
        if (!this.listeners.has(type)) this.listeners.set(type, []);
        this.listeners.get(type).push(handler);
    }

    dispatch(type, event = {}) {
        const payload = {
            preventDefault() { this.defaultPrevented = true; },
            stopPropagation() { this.propagationStopped = true; },
            defaultPrevented: false,
            propagationStopped: false,
            target: this,
            currentTarget: this,
            ...event
        };
        for (const handler of this.listeners.get(type) || []) handler(payload);
        return payload;
    }

    querySelector(selector) {
        if (String(selector).startsWith('.')) {
            const className = String(selector).slice(1);
            if (this.classList.contains(className)) return this;
            for (const child of this.children) {
                const found = child.querySelector(selector);
                if (found) return found;
            }
        }
        return null;
    }

    contains(element) {
        if (element === this) return true;
        return this.children.some(child => child.contains(element));
    }

    getBoundingClientRect() {
        return { ...this.rect };
    }
}

class FakeDocument {
    constructor() {
        this.elements = [];
    }

    createElement(tagName) {
        const element = new FakeElement(tagName, this);
        this.elements.push(element);
        return element;
    }

    querySelectorAll(selector) {
        const classes = String(selector)
            .split(',')
            .map(value => value.trim())
            .filter(value => value.startsWith('.'))
            .map(value => value.slice(1));
        return this.elements.filter(element => classes.some(className => element.classList.contains(className)));
    }
}

function createDataTransfer() {
    const values = new Map();
    return {
        effectAllowed: '',
        dropEffect: '',
        dragImage: null,
        setData(type, value) { values.set(type, value); },
        getData(type) { return values.get(type) || ''; },
        setDragImage(element, x, y) { this.dragImage = { element, x, y }; }
    };
}

function findByTitle(rootElement, title) {
    if (rootElement.title === title) return rootElement;
    for (const child of rootElement.children) {
        const found = findByTitle(child, title);
        if (found) return found;
    }
    return null;
}

function createHarness() {
    const document = new FakeDocument();
    const arrowCalls = [];
    const referenceCalls = [];
    const syncCalls = [];
    const removeCalls = [];
    const context = {
        Array,
        Boolean,
        Math,
        Number,
        String,
        document,
        ML_FAVORITOS_RANKING_DRAG_MIME: 'application/x-jk-favoritos-ranking',
        mlFavoritosRankingArrasteAtivo: null,
        mlFavoritosEmExecucao: false,
        skuChaveSku: value => String(value || '').trim().toLowerCase(),
        moverAnuncioRankingFavoritos: (sku, item, direction, options) => arrowCalls.push({ sku, item, direction, options }),
        moverAnuncioRankingFavoritosParaReferencia: (sku, sourceItem, targetItem, placeAfter, options) => {
            referenceCalls.push({ sku, sourceItem, targetItem, placeAfter, options });
        },
        sincronizarAnuncioRankingFavoritos: (sku, item, options) => syncCalls.push({ sku, item, options }),
        removerAnuncioRankingFavoritos: (sku, item, options) => removeCalls.push({ sku, item, options })
    };
    context.contextoEntradaArrasteRankingFavoritos = extractFunction('contextoEntradaArrasteRankingFavoritos', context);
    context.limparEstadoArrasteRankingFavoritos = extractFunction('limparEstadoArrasteRankingFavoritos', context);
    context.arrasteRankingFavoritosMesmoContexto = extractFunction('arrasteRankingFavoritosMesmoContexto', context);
    context.marcarDestinoArrasteRankingFavoritos = extractFunction('marcarDestinoArrasteRankingFavoritos', context);
    context.configurarArrasteLinhaRankingFavoritos = extractFunction('configurarArrasteLinhaRankingFavoritos', context);
    context.criarCelulaAcoesRankingFavoritos = extractFunction('criarCelulaAcoesRankingFavoritos', context);
    return { context, document, arrowCalls, referenceCalls, syncCalls, removeCalls };
}

function createRankingRow(harness, sku, item, options) {
    const row = harness.document.createElement('tr');
    row.appendChild(harness.context.criarCelulaAcoesRankingFavoritos(sku, item, options));
    harness.context.configurarArrasteLinhaRankingFavoritos(row, sku, item, options);
    return row;
}

function run() {
    const harness = createHarness();
    const sourceItem = { id: 'MLB1000000001' };
    const targetItem = { id: 'MLB1000000002' };
    const sourceRow = createRankingRow(harness, 'SKU-1', sourceItem, { entradaId: 'hist-1', rank: 1, primeiro: true });
    const targetRow = createRankingRow(harness, 'SKU-1', targetItem, { entradaId: 'hist-1', rank: 2, ultimo: true });
    sourceRow.rect = { top: 100, height: 40 };
    targetRow.rect = { top: 200, height: 40 };

    const handle = sourceRow.querySelector('.ml-ranking-drag-handle');
    assert.ok(handle, 'linha deve expor handle de arraste');
    assert.strictEqual(handle.draggable, true);
    assert.strictEqual(sourceRow.draggable, false, 'somente o handle deve ser arrastavel, nao a linha inteira');
    assert.match(handle.getAttribute('aria-label'), /Arrastar anuncio da posicao 1/);
    assert.doesNotMatch(handle.getAttribute('aria-label'), /setas/i, 'texto acessivel da alca nao deve orientar o uso de setas removidas');

    const upButton = findByTitle(sourceRow, 'Mover este anuncio uma posicao para cima');
    const downButton = findByTitle(sourceRow, 'Mover este anuncio uma posicao para baixo');
    assert.strictEqual(upButton, null, 'seta para cima nao deve existir na celula de acoes');
    assert.strictEqual(downButton, null, 'seta para baixo nao deve existir na celula de acoes');
    assert.strictEqual(harness.arrowCalls.length, 0, 'nenhuma acao relativa deve ser disparada sem os botoes de seta');

    const syncButton = findByTitle(sourceRow, 'Sincronizar dados deste anuncio pelo Avant Pro');
    const removeButton = findByTitle(sourceRow, 'Remover este anuncio do ranking');
    assert.ok(syncButton && removeButton, 'Sincronizar e Remover devem permanecer na celula de acoes');
    syncButton.dispatch('click');
    removeButton.dispatch('click');
    assert.strictEqual(harness.syncCalls.length, 1, 'Sincronizar deve continuar chamando seu handler');
    assert.strictEqual(harness.removeCalls.length, 1, 'Remover deve continuar chamando seu handler');
    assert.strictEqual(harness.syncCalls[0].options.entradaId, 'hist-1');
    assert.strictEqual(harness.removeCalls[0].options.entradaId, 'hist-1');

    const transferBefore = createDataTransfer();
    handle.dispatch('dragstart', { dataTransfer: transferBefore });
    assert.strictEqual(sourceRow.classList.contains('is-ranking-dragging'), true);
    assert.strictEqual(transferBefore.effectAllowed, 'move');
    assert.strictEqual(transferBefore.getData('application/x-jk-favoritos-ranking'), 'sku-1|hist-1');
    const overBefore = targetRow.dispatch('dragover', { dataTransfer: transferBefore, clientY: 205 });
    assert.strictEqual(overBefore.defaultPrevented, true);
    assert.strictEqual(targetRow.classList.contains('is-ranking-drop-before'), true);
    targetRow.dispatch('drop', { dataTransfer: transferBefore, clientY: 205 });
    assert.strictEqual(harness.referenceCalls.length, 1);
    assert.strictEqual(harness.referenceCalls[0].sourceItem, sourceItem);
    assert.strictEqual(harness.referenceCalls[0].targetItem, targetItem);
    assert.strictEqual(harness.referenceCalls[0].placeAfter, false);
    assert.strictEqual(harness.referenceCalls[0].options.entradaId, 'hist-1');
    assert.strictEqual(sourceRow.classList.contains('is-ranking-dragging'), false, 'drop deve limpar classe da origem');
    assert.strictEqual(targetRow.classList.contains('is-ranking-drop-before'), false, 'drop deve limpar classe do alvo');

    const transferAfter = createDataTransfer();
    handle.dispatch('dragstart', { dataTransfer: transferAfter });
    targetRow.dispatch('dragover', { dataTransfer: transferAfter, clientY: 235 });
    assert.strictEqual(targetRow.classList.contains('is-ranking-drop-after'), true);
    targetRow.dispatch('drop', { dataTransfer: transferAfter, clientY: 235 });
    assert.strictEqual(harness.referenceCalls.length, 2);
    assert.strictEqual(harness.referenceCalls[1].placeAfter, true);

    const sameTransfer = createDataTransfer();
    handle.dispatch('dragstart', { dataTransfer: sameTransfer });
    sourceRow.dispatch('drop', { dataTransfer: sameTransfer, clientY: 105 });
    assert.strictEqual(harness.referenceCalls.length, 2, 'drop na mesma linha deve ser no-op');

    const otherSkuRow = createRankingRow(harness, 'SKU-2', { id: 'MLB2000000001' }, { entradaId: 'hist-1', rank: 1 });
    const crossSkuTransfer = createDataTransfer();
    handle.dispatch('dragstart', { dataTransfer: crossSkuTransfer });
    const crossSkuOver = otherSkuRow.dispatch('dragover', { dataTransfer: crossSkuTransfer, clientY: 105 });
    assert.strictEqual(crossSkuOver.defaultPrevented, false, 'drag entre SKUs nao deve habilitar drop');
    otherSkuRow.dispatch('drop', { dataTransfer: crossSkuTransfer, clientY: 105 });
    assert.strictEqual(harness.referenceCalls.length, 2, 'drop entre SKUs deve ser rejeitado');

    const otherEntryRow = createRankingRow(harness, 'SKU-1', { id: 'MLB3000000001' }, { entradaId: 'hist-2', rank: 1 });
    const crossEntryTransfer = createDataTransfer();
    handle.dispatch('dragstart', { dataTransfer: crossEntryTransfer });
    const crossEntryOver = otherEntryRow.dispatch('dragover', { dataTransfer: crossEntryTransfer, clientY: 105 });
    assert.strictEqual(crossEntryOver.defaultPrevented, false, 'drag entre entradas nao deve habilitar drop');
    otherEntryRow.dispatch('drop', { dataTransfer: crossEntryTransfer, clientY: 105 });
    assert.strictEqual(harness.referenceCalls.length, 2, 'drop entre entradas deve ser rejeitado');

    assert.match(renderSource, /configurarArrasteLinhaRankingFavoritos\(tr, skuSelecionado, anuncioRender,[\s\S]*entradaId: entradaSelecionada/, 'aba Favoritos deve ativar drag nas linhas do ranking');
    assert.match(source, /configurarArrasteLinhaRankingFavoritos\(tr, sku, anuncio,[\s\S]*entradaId: opcoes\.entradaId \|\| ''/, 'tabela do historico deve ativar drag com sua entrada real');
    assert.match(styleSource, /\.ml-ranking-drag-handle[\s\S]*tr\.is-ranking-dragging[\s\S]*tr\.is-ranking-drop-before[\s\S]*tr\.is-ranking-drop-after/, 'estilos de handle e estados de drag devem permanecer presentes');

    console.log('Favoritos ranking drag-and-drop functional checks passed');
}

run();
