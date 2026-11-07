(function instalarComentariosSku(global) {
    'use strict';

    const escopos = new Map();
    const registros = new WeakMap();
    const atualizacoes = typeof BroadcastChannel === 'function' ? new BroadcastChannel('jk-comentarios-sku') : null;
    const validadeMs = 30000;
    const meses = 'JAN FEV MAR ABR MAI JUN JUL AGO SET OUT NOV DEZ'.split(' ');
    const seletorLinha = '.jk-sku-comment-row';
    const seletorSku = '.jk-sku-comment-anchor';
    let ui, menuAtual, editorAtual, ancoraBalao, timerBalao;

    function chaveSku(sku) {
        const texto = String(sku ?? '').trim();
        // Mesma identidade usada pelo cadastro e pela API de comentários.
        const data = /^(\d+)\s*[-/]\s*([A-Za-z]{3})$/.exec(texto);
        const mes = data ? meses.indexOf(data[2].toUpperCase()) + 1 : 0;
        if (mes) return data[1].replace(/^0+(?=\d)/, '') + '-' + mes;
        return /^\d$/.test(texto) ? texto.padStart(3, '0') : texto.toUpperCase();
    }

    function vigente(escopo) {
        return escopos.get(escopo.url) === escopo;
    }

    function comentario(registro) {
        return registro.escopo.comentarios?.get(registro.chave) || '';
    }

    function marcar(registro) {
        registro.celula.classList.toggle('jk-sku-has-comment', Boolean(comentario(registro)));
    }

    function marcarVisiveis(escopo) {
        if (!vigente(escopo)) return;
        let alvo;
        document.querySelectorAll(seletorLinha).forEach(linha => {
            const registro = registros.get(linha);
            if (registro?.escopo !== escopo) return;
            marcar(registro);
            if (registro.celula.matches(':hover, :focus')) alvo = registro.celula;
        });
        if (alvo) abrirBalao(alvo);
        if (menuAtual?.escopo === escopo) rotularMenu(menuAtual);
    }

    async function requisitar(escopo, dados) {
        const resposta = await fetch(escopo.url, {
            method: dados ? 'PUT' : 'GET',
            headers: global.obterAuthHeaders(dados ? { 'Content-Type': 'application/json' } : undefined),
            cache: 'no-store',
            signal: AbortSignal.timeout(15000),
            ...(dados ? { body: JSON.stringify(dados) } : {}),
        });
        if (!resposta.ok) throw new Error('Não foi possível acessar os comentários.');
        return resposta.json();
    }

    function carregar(escopo) {
        if (!vigente(escopo)) return Promise.resolve();
        if (!escopo.pendente) {
            const revisao = escopo.revisao;
            escopo.ultimaTentativa = Date.now();
            escopo.pendente = requisitar(escopo).then(dados => {
                if (revisao !== escopo.revisao) return;
                escopo.comentarios = new Map(Object.entries(dados.comentarios || {}));
                escopo.carregadoEm = Date.now();
                marcarVisiveis(escopo);
            }).finally(() => { escopo.pendente = null; });
        }
        return escopo.pendente;
    }

    function paraTabela(loja = '__todas', storeId = '') {
        const params = new URLSearchParams();
        if (storeId) params.set('store_id', storeId);
        else if (loja && loja !== '__todas') params.set('loja', loja);
        const url = '/api/comentarios-sku' + (params.size ? '?' + params.toString() : '');
        if (!escopos.has(url)) escopos.set(url, { url, loja, comentarios: null, revisao: 0 });
        const escopo = escopos.get(url);

        return (linha, celula, sku) => {
            const chave = chaveSku(sku);
            if (!chave || !celula || !vigente(escopo)) return;
            const registro = { celula, escopo, chave, sku: String(sku).trim() };
            registros.set(linha, registro);
            linha.classList.add('jk-sku-comment-row');
            celula.classList.add('jk-sku-comment-anchor');
            celula.tabIndex = 0;
            marcar(registro);
            if (!escopo.ultimaTentativa) void carregar(escopo).catch(() => {});
        };
    }

    function obterInterface() {
        if (ui) return ui;
        const menu = document.createElement('button');
        menu.type = 'button';
        menu.className = 'jk-sku-comment-menu';
        menu.hidden = true;
        menu.addEventListener('click', () => {
            const registro = menuAtual;
            fecharMenu();
            if (registro && registro.celula.getClientRects().length && vigente(registro.escopo)) void abrirEditor(registro);
        });

        const balao = document.createElement('div');
        balao.id = 'jk-sku-comment-tooltip';
        balao.setAttribute('role', 'tooltip');
        balao.hidden = true;
        balao.addEventListener('pointerenter', () => clearTimeout(timerBalao));
        balao.addEventListener('pointerleave', fecharBalao);

        const dialogo = document.createElement('dialog');
        dialogo.className = 'jk-sku-comment-dialog';
        dialogo.setAttribute('aria-labelledby', 'jk-sku-comment-title');
        dialogo.innerHTML = `
            <form>
                <h2 id="jk-sku-comment-title"></h2>
                <p class="jk-sku-comment-scope"></p>
                <label for="jk-sku-comment-text">Observação</label>
                <textarea id="jk-sku-comment-text" rows="5" maxlength="2000" placeholder="Escreva uma observação sobre este SKU"></textarea>
                <p class="jk-sku-comment-status" role="status"></p>
                <div class="jk-sku-comment-actions">
                    <button type="button" data-remover hidden>Remover comentário</button>
                    <button type="button" data-cancelar>Cancelar</button>
                    <button type="submit" data-salvar>Salvar</button>
                </div>
            </form>`;
        const form = dialogo.querySelector('form');
        const texto = form.querySelector('textarea');
        const salvar = form.querySelector('[data-salvar]');
        const cancelar = form.querySelector('[data-cancelar]');
        const remover = form.querySelector('[data-remover]');
        const status = form.querySelector('[role="status"]');
        form.addEventListener('submit', event => {
            event.preventDefault();
            void persistir(texto.value.trim());
        });
        remover.addEventListener('click', () => void persistir(''));
        cancelar.addEventListener('click', () => dialogo.close());
        dialogo.addEventListener('cancel', event => {
            if (editorAtual?.salvando) event.preventDefault();
        });
        dialogo.addEventListener('close', () => {
            editorAtual = null;
            texto.value = '';
        });
        document.body.append(menu, balao, dialogo);
        ui = { menu, balao, dialogo, form, texto, salvar, cancelar, remover, status };
        return ui;
    }

    function rotularMenu(registro) {
        ui.menu.textContent = comentario(registro) ? 'Editar comentário' : 'Adicionar comentário';
    }

    function posicionar(elemento, x, y, acima = y) {
        const margem = 8;
        const { width, height } = elemento.getBoundingClientRect();
        const esquerda = Math.max(margem, Math.min(x, global.innerWidth - width - margem));
        const vertical = y + height + margem > global.innerHeight ? acima - height : y;
        const topo = Math.max(margem, Math.min(vertical, global.innerHeight - height - margem));
        elemento.style.left = esquerda + 'px';
        elemento.style.top = topo + 'px';
    }

    function abrirMenu(registro, x, y) {
        fecharBalao();
        const { menu } = obterInterface();
        menuAtual = registro;
        rotularMenu(registro);
        menu.hidden = false;
        posicionar(menu, x, y);
        menu.focus({ preventScroll: true });
    }

    function fecharMenu() {
        menuAtual = null;
        if (ui) ui.menu.hidden = true;
    }

    function abrirBalao(celula) {
        fecharBalao();
        const registro = registros.get(celula.closest(seletorLinha));
        if (!registro || !vigente(registro.escopo) || menuAtual || editorAtual) return;
        if (Date.now() - registro.escopo.ultimaTentativa > validadeMs) void carregar(registro.escopo).catch(() => {});
        const observacao = comentario(registro);
        if (!observacao) return;
        const { balao } = obterInterface();
        const caixa = celula.getBoundingClientRect();
        balao.textContent = observacao;
        balao.hidden = false;
        ancoraBalao = celula;
        celula.setAttribute('aria-describedby', balao.id);
        posicionar(balao, caixa.left, caixa.bottom + 4, caixa.top - 4);
    }

    function fecharBalao() {
        clearTimeout(timerBalao);
        if (!ancoraBalao) return;
        ancoraBalao.removeAttribute('aria-describedby');
        ancoraBalao = null;
        ui.balao.hidden = true;
        ui.balao.textContent = '';
    }

    async function abrirEditor(registro) {
        const { escopo, sku } = registro;
        const { dialogo, form, texto, salvar, remover, cancelar, status } = obterInterface();
        const editor = { registro, salvando: false };
        editorAtual = editor;
        form.querySelector('h2').textContent = 'Comentário do SKU ' + sku;
        form.querySelector('.jk-sku-comment-scope').textContent = escopo.loja === '__todas' ? 'Todas as lojas' : escopo.loja;
        texto.value = '';
        texto.disabled = salvar.disabled = true;
        cancelar.disabled = false;
        remover.hidden = true;
        status.textContent = 'Carregando comentário…';
        dialogo.showModal();
        try {
            if (escopo.pendente || !escopo.carregadoEm || Date.now() - escopo.carregadoEm > validadeMs) await carregar(escopo);
        } catch {
            if (editorAtual === editor) status.textContent = 'Não foi possível carregar o comentário. Feche e tente novamente.';
            return;
        }
        if (editorAtual !== editor || !dialogo.open || !vigente(escopo)) return;
        texto.value = comentario(registro);
        remover.hidden = !texto.value;
        remover.disabled = texto.disabled = salvar.disabled = false;
        status.textContent = '';
        texto.focus();
    }

    async function persistir(valor) {
        const editor = editorAtual;
        if (!editor || editor.salvando || ui.salvar.disabled) return;
        const { escopo, sku } = editor.registro;
        if (!vigente(escopo)) return;
        editor.salvando = true;
        const { dialogo, texto, salvar, remover, cancelar, status } = ui;
        texto.disabled = salvar.disabled = remover.disabled = cancelar.disabled = true;
        status.textContent = 'Salvando…';
        try {
            const dados = await requisitar(escopo, { sku, comentario: valor });
            if (editorAtual !== editor || !vigente(escopo)) return;
            escopo.revisao += 1;
            if (dados.comentario) escopo.comentarios.set(dados.sku, dados.comentario);
            else escopo.comentarios.delete(dados.sku);
            marcarVisiveis(escopo);
            dialogo.close();
            atualizacoes?.postMessage('salvo');
        } catch {
            if (editorAtual !== editor) return;
            editor.salvando = false;
            texto.disabled = salvar.disabled = remover.disabled = cancelar.disabled = false;
            status.textContent = 'Não foi possível salvar o comentário. Tente novamente.';
        }
    }

    function alvoDoMenu(evento) {
        if (evento.target.closest('input, textarea, select, [contenteditable="true"]')) return null;
        const registro = registros.get(evento.target.closest(seletorLinha));
        return registro && vigente(registro.escopo) && !editorAtual ? registro : null;
    }

    document.addEventListener('contextmenu', evento => {
        const registro = alvoDoMenu(evento);
        if (!registro) return;
        evento.preventDefault();
        abrirMenu(registro, evento.clientX, evento.clientY);
    });
    document.addEventListener('keydown', evento => {
        if (evento.key === 'ContextMenu' || (evento.shiftKey && evento.key === 'F10')) {
            const registro = alvoDoMenu(evento);
            if (!registro) return;
            evento.preventDefault();
            const caixa = registro.celula.getBoundingClientRect();
            abrirMenu(registro, caixa.left, caixa.bottom);
        } else if (evento.key === 'Escape') {
            const celula = menuAtual?.celula;
            fecharMenu();
            if (celula?.isConnected) celula.focus({ preventScroll: true });
            fecharBalao();
        }
    });
    document.addEventListener('pointerdown', evento => {
        if (menuAtual && evento.target !== ui.menu) fecharMenu();
    });
    document.addEventListener('pointerover', evento => {
        const celula = evento.target.closest(seletorSku);
        if (celula && !celula.contains(evento.relatedTarget)) abrirBalao(celula);
    });
    document.addEventListener('pointerout', evento => {
        if (ancoraBalao?.contains(evento.target) && !ancoraBalao.contains(evento.relatedTarget)) {
            timerBalao = setTimeout(fecharBalao, 120);
        }
    });
    document.addEventListener('focusin', evento => {
        if (menuAtual && evento.target !== ui.menu) fecharMenu();
        const celula = evento.target.closest(seletorSku);
        if (celula) abrirBalao(celula);
    });
    document.addEventListener('focusout', evento => {
        if (evento.target === ancoraBalao) fecharBalao();
    });

    function fecharSobreposicoes(evento) {
        if (evento.target === ui?.balao) return;
        fecharMenu();
        if (ancoraBalao?.isConnected && ancoraBalao.matches(':hover, :focus')) {
            const caixa = ancoraBalao.getBoundingClientRect();
            if (caixa.width && caixa.height && caixa.bottom > 0 && caixa.top < global.innerHeight) {
                posicionar(ui.balao, caixa.left, caixa.bottom + 4, caixa.top - 4);
                return;
            }
        }
        fecharBalao();
    }

    atualizacoes?.addEventListener('message', () => {
        // O aviso apenas invalida a leitura; o conteúdo sempre vem da API autenticada.
        escopos.forEach(escopo => { escopo.carregadoEm = escopo.ultimaTentativa = 0; });
        const ativos = new Set(Array.from(document.querySelectorAll(seletorLinha), linha => registros.get(linha)?.escopo));
        ativos.forEach(escopo => {
            if (!escopo || !vigente(escopo)) return;
            void Promise.resolve(escopo.pendente).catch(() => {}).then(() => carregar(escopo)).catch(() => {});
        });
    });

    function limparSessao() {
        escopos.clear();
        fecharMenu();
        fecharBalao();
        if (ui?.dialogo.open) ui.dialogo.close();
        document.querySelectorAll('.jk-sku-has-comment').forEach(celula => celula.classList.remove('jk-sku-has-comment'));
    }

    global.addEventListener('scroll', fecharSobreposicoes, true);
    global.addEventListener('resize', fecharSobreposicoes);
    global.addEventListener('jk-auth-changed', limparSessao);
    global.addEventListener('jk-tenant-changed', limparSessao);
    global.addEventListener('storage', evento => {
        if (evento.key === 'user_data' || evento.key === null) limparSessao();
    });
    global.JKComentariosSku = { paraTabela };
})(window);
