(function () {
    'use strict';
    const names = { etiqueta: 'Etiqueta original', sku: 'SKU e quantidade', danfe: 'Código DANFE' };
    const stores = ['Mercado Livre', 'Shopee', 'TikTok', 'Amazon'];
    const memory = new Map();
    let state = null;
    let revision = 0;
    let timer;
    let controller;
    let defaultsPromise;
    const clone = value => JSON.parse(JSON.stringify(value));
    const round = value => Math.round(value * 100) / 100;
    const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

    function identity() {
        try {
            const user = JSON.parse(localStorage.getItem('user_data') || '{}');
            let claims = {};
            const token = localStorage.getItem('access_token') || '';
            if (token.split('.').length === 3) {
                try {
                    const part = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
                    claims = JSON.parse(decodeURIComponent(Array.from(atob(part), c => '%' + c.charCodeAt(0).toString(16).padStart(2, '0')).join('')));
                } catch (_) { /* A sessão da página continua sendo a fonte principal. */ }
            }
            const client = claims.client_id || user.client_id;
            const username = claims.sub || user.username || user.user_id;
            return client && username ? [String(client), String(username)] : null;
        } catch (_) { return null; }
    }

    function storageKey(store) {
        const scope = identity();
        return scope ? 'jk:etiquetas-layout:v1:' + JSON.stringify([...scope, store]) : null;
    }

    function validLayout(layout) {
        if (!layout || layout.version !== 1 || !layout.page || !layout.items) return false;
        if (![layout.page.width, layout.page.height].every(n => Number.isFinite(n) && n > 0 && n <= 300)) return false;
        if (layout.danfe_source) {
            const crop = layout.danfe_source;
            if (!['x', 'y', 'width', 'height'].every(key => Number.isFinite(crop[key])) || crop.x < 0 || crop.y < 0 ||
                crop.width <= 0 || crop.height <= 0 || crop.x + crop.width > 1.000001 || crop.y + crop.height > 1.000001) return false;
        }
        return Object.keys(names).every(id => {
            const item = layout.items[id];
            return item && ['x', 'y', 'width', 'height'].every(key => Number.isFinite(item[key])) &&
                item.x >= 0 && item.y >= 0 && item.width >= 1 && item.height >= 1 &&
                item.x + item.width <= layout.page.width + .001 && item.y + item.height <= layout.page.height + .001 &&
                [0, 90, 180, 270].includes(item.rotation) && typeof item.visible === 'boolean';
        });
    }

    function getSavedLayout(store) {
        const key = storageKey(store);
        if (!key) return null;
        try {
            const value = JSON.parse(localStorage.getItem(key) || 'null');
            return validLayout(value) ? clone(value) : null;
        } catch (_) {
            const value = memory.get(key);
            return validLayout(value) ? clone(value) : null;
        }
    }

    async function getCurrentLayout(store) {
        if (!defaultsPromise) {
            defaultsPromise = fetch('/etiquetas/layout-defaults.json?v=20261102-config-atual-v2')
                .then(async response => {
                    if (!response.ok) throw new Error('Não foi possível carregar a configuração atual.');
                    const data = await response.json();
                    if (data.version !== 1 || !data.stores) throw new Error('Configuração atual inválida.');
                    return data.stores;
                }).catch(error => { defaultsPromise = null; throw error; });
        }
        const layouts = await defaultsPromise;
        if (!validLayout(layouts[store])) throw new Error('Configuração atual inválida.');
        return clone(layouts[store]);
    }

    function createDialog() {
        const dialog = document.createElement('dialog');
        dialog.id = 'etiquetas-layout-dialog';
        dialog.className = 'layout-dialog';
        dialog.setAttribute('aria-labelledby', 'layout-title');
        dialog.innerHTML = `
            <header class="layout-head"><div><span class="layout-eyebrow">CONFIGURAÇÃO DE IMPRESSÃO</span><h2 id="layout-title">Layout da etiqueta <span id="layout-store"></span></h2><p>Selecione um item para mover e ajustar dentro da área de impressão.</p></div><button type="button" id="layout-close" class="layout-icon-button" aria-label="Fechar editor">×</button></header>
            <div class="layout-body">
                <aside class="layout-layers"><h3>Itens da etiqueta</h3>
                    ${Object.entries(names).map(([id, name], index) => `<button type="button" class="layout-layer" data-layout-select="${id}"><span class="layout-layer-number">${index + 1}</span><span>${name}<small>${id === 'etiqueta' ? 'Documento da loja' : id === 'sku' ? 'Identificação do produto' : 'Chave e código de barras'}</small></span></button>`).join('')}
                    <p class="layout-help">Arraste para posicionar. Use a alça no canto para redimensionar.</p>
                    <p class="layout-help">Com um item selecionado, as setas movem 1 mm. Shift + seta move 5 mm.</p>
                    <button type="button" class="layout-secondary" id="layout-reset">Restaurar padrão</button>
                </aside>
                <section class="layout-workspace" aria-label="Área de impressão">
                    <div class="layout-canvas-head"><span id="layout-sample">Gerando prévia…</span><span id="layout-paper-size">100 × 150 mm</span></div>
                    <div class="layout-paper-wrap"><div class="layout-ruler-top" aria-hidden="true"><span>0</span><span id="layout-ruler-width">100 mm</span></div><div id="etiquetas-layout-page" class="layout-paper" aria-label="Folha de impressão">
                        ${Object.entries(names).map(([id, name]) => `<div class="layout-item layout-item--${id}" data-layout-item="${id}" role="button" tabindex="0" aria-label="Mover ${name}"><img alt="${name}" draggable="false"><span class="layout-item-label">${name}</span><span class="layout-resize" data-layout-resize="${id}" aria-hidden="true"></span></div>`).join('')}
                    </div></div>
                    <p id="layout-source-note" class="layout-source-note">Amostra ilustrativa. Use um arquivo para conferir os dados da loja.</p>
                </section>
                <aside class="layout-properties"><h3 id="layout-selected-name">Etiqueta original</h3><p class="layout-help">Posição e tamanho em milímetros</p>
                    <div class="layout-fields">${[['x', 'Esquerda'], ['y', 'Topo'], ['width', 'Largura'], ['height', 'Altura']].map(([key, label]) => `<label>${label}<span class="layout-unit"><input type="number" data-layout-field="${key}" step="0.1" min="0" aria-label="${label} em milímetros"><span>mm</span></span></label>`).join('')}</div>
                    <label class="layout-rotation">Rotação<select data-layout-field="rotation" aria-label="Rotação"><option value="0">0°</option><option value="90">90°</option><option value="180">180°</option><option value="270">270°</option></select></label>
                    <label class="layout-visible"><input type="checkbox" data-layout-field="visible"> Imprimir este item</label>
                    <div id="layout-crop-tools" class="layout-crop-tools" hidden><button type="button" class="layout-secondary" id="layout-crop-open">Selecionar área DANFE</button><button type="button" class="layout-crop-clear" id="layout-crop-clear" hidden>Voltar ao código integrado</button><p class="layout-help">Marque o código no documento original para movê-lo separadamente.</p></div>
                    <div class="layout-preview-tools"><button type="button" class="layout-secondary" id="layout-preview">Atualizar prévia</button><p class="layout-help">Usa o arquivo selecionado na tela para esta loja, quando houver.</p></div>
                    <details class="layout-print-check"><summary>Conferir impressão</summary><img id="layout-print-preview" alt="Prévia do PDF com o layout atual"></details>
                </aside>
            </div>
            <section id="layout-crop-panel" class="layout-crop-panel" hidden><h3>Selecione o código DANFE no original</h3><p>Arraste sobre a chave e o código de barras. Essa área será separada para você posicionar na impressão.</p><div id="layout-crop-canvas" class="layout-crop-canvas"><img id="layout-crop-source" alt="Etiqueta original para selecionar a região DANFE" draggable="false"><div id="layout-crop-box" class="layout-crop-box"></div></div><div class="layout-crop-fields">${[['x', 'Esquerda'], ['y', 'Topo'], ['width', 'Largura'], ['height', 'Altura']].map(([key, label]) => `<label>${label} (%)<input type="number" data-crop-field="${key}" step="0.1" min="0" max="100" aria-label="${label} do recorte em porcentagem"></label>`).join('')}</div><div class="layout-crop-actions"><button type="button" class="layout-secondary" id="layout-crop-cancel">Cancelar seleção</button><button type="button" class="layout-primary" id="layout-crop-apply">Usar área selecionada</button></div></section>
            <div id="layout-status" class="layout-status" role="status" aria-live="polite"></div>
            <footer class="layout-footer"><span>Configuração salva por loja, neste navegador.</span><div><button type="button" id="layout-cancel" class="layout-secondary">Cancelar</button><button type="button" id="layout-save" class="layout-primary" disabled>Salvar layout</button></div></footer>`;
        document.body.append(dialog);
        dialog.querySelectorAll('[data-layout-select]').forEach(button => button.addEventListener('click', () => select(button.dataset.layoutSelect)));
        dialog.querySelectorAll('[data-layout-item]').forEach(item => {
            item.addEventListener('pointerdown', startDrag);
            item.addEventListener('click', () => select(item.dataset.layoutItem));
            item.addEventListener('focus', () => select(item.dataset.layoutItem));
            item.addEventListener('keydown', moveWithKeyboard);
        });
        dialog.querySelectorAll('[data-layout-field]').forEach(input => input.addEventListener('change', changeField));
        dialog.querySelector('#layout-cancel').addEventListener('click', close);
        dialog.querySelector('#layout-close').addEventListener('click', close);
        dialog.querySelector('#layout-save').addEventListener('click', save);
        dialog.querySelector('#layout-preview').addEventListener('click', () => loadPreview());
        dialog.querySelector('#layout-crop-open').addEventListener('click', openCrop);
        dialog.querySelector('#layout-crop-cancel').addEventListener('click', closeCrop);
        dialog.querySelector('#layout-crop-apply').addEventListener('click', () => {
            if (!state?.crop) return;
            state.layout.danfe_source = clone(state.crop);
            state.layout.items.danfe.visible = true;
            state.selected = 'danfe';
            closeCrop();
            changed();
        });
        dialog.querySelector('#layout-crop-clear').addEventListener('click', () => {
            if (!state?.layout) return;
            delete state.layout.danfe_source;
            state.layout.items.danfe.visible = false;
            state.selected = 'etiqueta';
            changed();
        });
        dialog.querySelector('#layout-crop-canvas').addEventListener('pointerdown', startCrop);
        dialog.querySelectorAll('[data-crop-field]').forEach(input => input.addEventListener('change', event => {
            if (!state?.crop) return;
            const key = event.target.dataset.cropField;
            const value = Number(event.target.value) / 100;
            if (!Number.isFinite(value)) return;
            state.crop[key] = clamp(value, key === 'width' || key === 'height' ? .01 : 0, 1);
            state.crop.width = Math.min(state.crop.width, 1 - state.crop.x);
            state.crop.height = Math.min(state.crop.height, 1 - state.crop.y);
            paintCrop();
        }));
        dialog.querySelector('#layout-reset').addEventListener('click', restoreCurrentLayout);
        dialog.addEventListener('cancel', event => { event.preventDefault(); if (state?.cropping) closeCrop(); else close(); });
        dialog.addEventListener('click', event => {
            if (event.target === dialog) {
                const bounds = dialog.getBoundingClientRect();
                if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) close();
            }
        });
        return dialog;
    }

    const dialog = createDialog();
    const el = id => dialog.querySelector('#' + id);
    const field = name => dialog.querySelector('[data-layout-field="' + name + '"]');

    function status(message, error = false) {
        el('layout-status').textContent = message;
        el('layout-status').classList.toggle('is-error', error);
    }

    function select(id) {
        if (!state || !state.layout || state.availability[id] === false) return;
        state.selected = id;
        paint();
    }

    function paint() {
        if (!state || !state.layout) return;
        const { layout, selected } = state;
        const item = layout.items[selected];
        const paper = el('etiquetas-layout-page');
        paper.style.aspectRatio = `${layout.page.width} / ${layout.page.height}`;
        el('layout-paper-size').textContent = `${round(layout.page.width)} × ${round(layout.page.height)} mm`;
        el('layout-ruler-width').textContent = `${round(layout.page.width)} mm`;
        el('layout-selected-name').textContent = names[selected];
        for (const id of Object.keys(names)) {
            const data = layout.items[id];
            const node = dialog.querySelector('[data-layout-item="' + id + '"]');
            const available = state.availability[id] !== false;
            node.hidden = !available || !data.visible;
            node.classList.toggle('is-selected', id === selected);
            Object.assign(node.style, { left: `${data.x / layout.page.width * 100}%`, top: `${data.y / layout.page.height * 100}%`, width: `${data.width / layout.page.width * 100}%`, height: `${data.height / layout.page.height * 100}%` });
            const image = node.querySelector('img');
            const rotated = data.rotation % 180 !== 0;
            image.style.width = rotated ? `${data.height / data.width * 100}%` : '100%';
            image.style.height = rotated ? `${data.width / data.height * 100}%` : '100%';
            image.style.transform = `translate(-50%, -50%) rotate(${-data.rotation}deg)`;
            const button = dialog.querySelector('[data-layout-select="' + id + '"]');
            button.classList.toggle('is-selected', id === selected);
            button.setAttribute('aria-pressed', String(id === selected));
            button.disabled = !available;
        }
        for (const key of ['x', 'y', 'width', 'height', 'rotation']) field(key).value = round(item[key]);
        field('visible').checked = item.visible;
        field('x').max = round(layout.page.width - item.width);
        field('y').max = round(layout.page.height - item.height);
        field('width').min = field('height').min = 1;
        field('width').max = round(layout.page.width - item.x);
        field('height').max = round(layout.page.height - item.y);
        el('layout-crop-tools').hidden = !state.source || state.store === 'Amazon' || (!state.canSelectDanfe && !layout.danfe_source);
        el('layout-crop-clear').hidden = !layout.danfe_source;
    }

    function paintCrop() {
        if (!state?.crop) return;
        const crop = state.crop;
        Object.assign(el('layout-crop-box').style, { left: `${crop.x * 100}%`, top: `${crop.y * 100}%`, width: `${crop.width * 100}%`, height: `${crop.height * 100}%` });
        dialog.querySelectorAll('[data-crop-field]').forEach(input => { input.value = round(crop[input.dataset.cropField] * 100); });
        el('layout-crop-apply').disabled = crop.width < .01 || crop.height < .01;
    }

    function openCrop() {
        if (!state?.source || !state.layout) return;
        clearTimeout(timer);
        controller?.abort();
        revision++;
        state.cropping = true;
        state.crop = clone(state.layout.danfe_source || { x: .05, y: .7, width: .9, height: .25 });
        dialog.querySelector('.layout-body').hidden = true;
        el('layout-crop-panel').hidden = false;
        el('layout-save').disabled = true;
        const source = el('layout-crop-source');
        source.onload = () => { el('layout-crop-canvas').style.aspectRatio = `${source.naturalWidth} / ${source.naturalHeight}`; };
        source.src = state.source;
        paintCrop();
        status('Inclua a chave e as barras na seleção. Confira a área antes de confirmar.');
        el('layout-crop-cancel').focus();
    }

    function closeCrop() {
        if (!state) return;
        state.cropping = false;
        el('layout-crop-panel').hidden = true;
        dialog.querySelector('.layout-body').hidden = false;
        el('layout-save').disabled = false;
        el('layout-crop-open').focus();
    }

    function startCrop(event) {
        if (event.button !== 0 || !state?.cropping) return;
        event.preventDefault();
        const canvas = event.currentTarget;
        const bounds = canvas.getBoundingClientRect();
        const point = ev => ({ x: clamp((ev.clientX - bounds.left) / bounds.width, 0, 1), y: clamp((ev.clientY - bounds.top) / bounds.height, 0, 1) });
        const start = point(event);
        const session = state;
        canvas.setPointerCapture(event.pointerId);
        const move = update => {
            if (state !== session) return;
            const end = point(update);
            state.crop = { x: Math.min(start.x, end.x), y: Math.min(start.y, end.y), width: Math.abs(end.x - start.x), height: Math.abs(end.y - start.y) };
            paintCrop();
        };
        const finish = () => {
            canvas.removeEventListener('pointermove', move);
            canvas.removeEventListener('pointerup', finish);
            canvas.removeEventListener('pointercancel', finish);
            canvas.removeEventListener('lostpointercapture', finish);
        };
        canvas.addEventListener('pointermove', move);
        canvas.addEventListener('pointerup', finish);
        canvas.addEventListener('pointercancel', finish);
        canvas.addEventListener('lostpointercapture', finish);
    }

    function normalizeItem(item) {
        const page = state.layout.page;
        item.width = clamp(round(item.width), 1, page.width);
        item.height = clamp(round(item.height), 1, page.height);
        item.x = clamp(round(item.x), 0, page.width - item.width);
        item.y = clamp(round(item.y), 0, page.height - item.height);
    }

    function changed() {
        clearTimeout(timer);
        revision++;
        controller?.abort();
        paint();
        status('Layout alterado. Confira a prévia e salve para usar na impressão.');
        el('layout-print-preview').removeAttribute('src');
        timer = setTimeout(() => { if (state) void loadPreview(); }, 450);
    }

    function changeField(event) {
        if (!state?.layout) return;
        const input = event.target;
        const key = input.dataset.layoutField;
        const item = state.layout.items[state.selected];
        if (key === 'visible') item.visible = input.checked;
        else {
            const value = Number(input.value);
            if (!Number.isFinite(value) || input.value.trim() === '') { paint(); return; }
            item[key] = value;
            normalizeItem(item);
        }
        changed();
    }

    function startDrag(event) {
        if (event.button !== 0 || !state?.layout) return;
        const node = event.currentTarget;
        const id = node.dataset.layoutItem;
        if (state.availability[id] === false) return;
        event.preventDefault();
        select(id);
        node.focus({ preventScroll: true });
        clearTimeout(timer);
        controller?.abort();
        revision++;
        const initial = clone(state.layout.items[id]);
        const origin = { x: event.clientX, y: event.clientY };
        const bounds = el('etiquetas-layout-page').getBoundingClientRect();
        const resize = !!event.target.closest('[data-layout-resize]');
        const session = state;
        node.setPointerCapture(event.pointerId);
        const move = update => {
            if (state !== session) return;
            const dx = (update.clientX - origin.x) / bounds.width * state.layout.page.width;
            const dy = (update.clientY - origin.y) / bounds.height * state.layout.page.height;
            const item = state.layout.items[id];
            if (resize) {
                item.width = round(clamp(initial.width + dx, 1, state.layout.page.width - initial.x));
                item.height = round(clamp(initial.height + dy, 1, state.layout.page.height - initial.y));
            } else {
                item.x = initial.x + dx;
                item.y = initial.y + dy;
            }
            normalizeItem(item);
            paint();
        };
        const finish = () => {
            node.removeEventListener('pointermove', move);
            node.removeEventListener('pointerup', finish);
            node.removeEventListener('pointercancel', finish);
            node.removeEventListener('lostpointercapture', finish);
            if (state === session) changed();
        };
        node.addEventListener('pointermove', move);
        node.addEventListener('pointerup', finish);
        node.addEventListener('pointercancel', finish);
        node.addEventListener('lostpointercapture', finish);
    }

    function moveWithKeyboard(event) {
        const directions = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
        if (!directions[event.key] || !state?.layout) return;
        event.preventDefault();
        const item = state.layout.items[event.currentTarget.dataset.layoutItem];
        const step = event.shiftKey ? 5 : 1;
        item.x += directions[event.key][0] * step;
        item.y += directions[event.key][1] * step;
        normalizeItem(item);
        changed();
    }

    async function loadPreview({ configuration = false } = {}) {
        if (!state) return false;
        clearTimeout(timer);
        const session = state;
        const scope = storageKey(state.store);
        if (scope !== state.scope) { status('A sessão mudou. Feche e abra o editor novamente.', true); return false; }
        const request = ++revision;
        controller?.abort();
        controller = new AbortController();
        const signal = controller.signal;
        const form = new FormData();
        form.append('store', state.store);
        if (state.layout) form.append('layout', JSON.stringify(state.layout));
        const selectedStore = document.querySelector('input[name="loja"]:checked')?.value;
        let hasSource = false;
        if (selectedStore === state.store) {
            for (const [id, name] of [['file-etiquetas', 'file_etiquetas'], ['file-lista', 'file_lista'], ['file-nf', 'file_nota_fiscal']]) {
                const file = document.getElementById(id)?.files[0];
                const relevant = state.store === 'Amazon' ? name === 'file_nota_fiscal' : name !== 'file_nota_fiscal';
                if (file && relevant) {
                    form.append(name, file);
                    if (name === 'file_etiquetas' || name === 'file_nota_fiscal') hasSource = true;
                }
            }
        }
        el('layout-preview').disabled = true;
        el('layout-save').disabled = true;
        el('layout-sample').textContent = 'Carregando prévia…';
        status('Carregando a prévia da configuração atual…');
        try {
            const token = localStorage.getItem('access_token');
            const readConfig = configuration && !hasSource;
            const url = readConfig ? '/api/etiquetas/layout/config?store=' + encodeURIComponent(state.store) : '/api/etiquetas/layout/preview';
            const response = await fetch(url, { method: readConfig ? 'GET' : 'POST', headers: token ? { Authorization: `Bearer ${token}` } : {}, ...(readConfig ? {} : { body: form }), signal });
            if (state !== session || request !== revision || storageKey(state.store) !== scope) return false;
            if (response.status === 404 || response.status === 405) throw new Error('O servidor local precisa ser reiniciado para carregar o editor de etiquetas. A configuração exibida foi preservada. Após reiniciar, atualize a página.');
            let data;
            try { data = await response.json(); } catch (_) { throw new Error('O servidor não retornou uma prévia válida. Tente atualizar a prévia novamente.'); }
            if (state !== session || request !== revision || storageKey(state.store) !== scope) return false;
            if (!response.ok || !data.success) throw new Error(typeof data.detail === 'string' ? data.detail : data.message || 'Não foi possível gerar a prévia.');
            if (!validLayout(data.layout)) throw new Error('O servidor retornou um layout inválido.');
            state.layout = clone(data.layout);
            state.rendered = true;
            el('etiquetas-layout-page').classList.remove('needs-preview');
            state.availability = data.availability || {};
            state.canSelectDanfe = data.can_select_danfe ?? state.availability.danfe === false;
            const original = data.source || data.components?.etiqueta;
            state.source = typeof original === 'string' && /^data:image\/png;base64,/.test(original) ? original : null;
            for (const id of Object.keys(names)) {
                const image = dialog.querySelector('[data-layout-item="' + id + '"] img');
                const source = data.components?.[id];
                if (source && /^data:image\/png;base64,/.test(source)) image.src = source;
                else image.removeAttribute('src');
            }
            if (data.preview && /^data:image\/png;base64,/.test(data.preview)) el('layout-print-preview').src = data.preview;
            if (state.availability[state.selected] === false) state.selected = 'etiqueta';
            el('layout-sample').textContent = data.sample ? 'Configuração atual · amostra' : 'Prévia do seu arquivo';
            el('layout-source-note').textContent = data.sample ? 'Posições da configuração atual, com dados de exemplo. Selecione um PDF e atualize para conferir sua etiqueta.' : 'Prévia da primeira etiqueta do arquivo. O layout será aplicado às etiquetas processadas.';
            state.warnings = Array.isArray(data.warnings) ? data.warnings : [];
            status(state.warnings.join(' ') || 'Prévia pronta. Posicione os itens e salve o layout.');
            paint();
            el('layout-save').disabled = false;
            return true;
        } catch (error) {
            if (error.name !== 'AbortError' && state === session && request === revision) {
                status(error.message || 'Falha ao carregar a prévia.', true);
                el('layout-sample').textContent = 'Prévia indisponível';
            }
            return false;
        } finally {
            if (state === session && request === revision) el('layout-preview').disabled = false;
        }
    }

    async function restoreCurrentLayout() {
        if (!state) return;
        const session = state;
        clearTimeout(timer);
        controller?.abort();
        revision++;
        try {
            const layout = await getCurrentLayout(state.store);
            if (state !== session) return;
            state.layout = layout;
            state.selected = 'etiqueta';
            paint();
            await loadPreview({ configuration: true });
        } catch (error) {
            if (state === session) status(error.message, true);
        }
    }

    async function save() {
        if (!state?.layout || !validLayout(state.layout)) return;
        const session = state;
        if (!await loadPreview() || state !== session) return;
        const key = storageKey(state.store);
        if (!key || key !== state.scope) { status('É necessário entrar na sua conta para salvar o layout.', true); return; }
        try {
            localStorage.setItem(key, JSON.stringify(state.layout));
            memory.set(key, clone(state.layout));
        } catch (_) { status('O navegador não permitiu salvar. Verifique o armazenamento e tente novamente.', true); return; }
        const store = state.store;
        close();
        const gear = document.querySelector('[data-layout-store="' + store + '"]');
        gear?.classList.add('has-saved-layout');
        gear?.setAttribute('title', `Editar layout de ${store} (personalizado)`);
    }

    function close() {
        clearTimeout(timer);
        controller?.abort();
        revision++;
        const trigger = state?.trigger;
        state = null;
        dialog.close();
        trigger?.focus({ preventScroll: true });
    }

    async function open(store, trigger) {
        if (!stores.includes(store) || dialog.open) return;
        const saved = getSavedLayout(store);
        state = { store, trigger, scope: storageKey(store), layout: saved, selected: 'etiqueta', availability: {}, warnings: [], rendered: false };
        const session = state;
        el('layout-crop-panel').hidden = true;
        dialog.querySelector('.layout-body').hidden = false;
        el('layout-store').textContent = store;
        el('layout-save').disabled = true;
        el('layout-sample').textContent = saved ? 'Configuração salva' : 'Configuração atual';
        el('layout-print-preview').removeAttribute('src');
        el('etiquetas-layout-page').classList.add('needs-preview');
        el('layout-source-note').textContent = saved ? 'Suas posições salvas estão exibidas. Carregando o conteúdo da etiqueta…' : 'Carregando as posições usadas atualmente na impressão…';
        dialog.querySelectorAll('[data-layout-item]').forEach(item => {
            item.hidden = true;
            item.querySelector('img').removeAttribute('src');
        });
        if (saved) paint();
        dialog.showModal();
        el('layout-close').focus();
        if (!saved) {
            try {
                const layout = await getCurrentLayout(store);
                if (state !== session) return;
                state.layout = layout;
                paint();
            } catch (_) { /* A rota de configuração também fornece as posições atuais. */ }
        }
        if (state === session) await loadPreview({ configuration: !saved });
    }

    document.querySelectorAll('[data-layout-store]').forEach(gear => {
        gear.addEventListener('click', event => { event.preventDefault(); event.stopPropagation(); void open(gear.dataset.layoutStore, gear); });
        if (getSavedLayout(gear.dataset.layoutStore)) gear.classList.add('has-saved-layout');
    });
    window.JKEtiquetasLayout = { getSavedLayout, open };
})();
