'use strict';

const assert = require('assert');
const path = require('path');
const { chromium } = require('playwright');
const { promotionEffectuationSource } = require('./helpers/favoritos_promotion_effectuation_sources');

const root = path.resolve(__dirname, '..');
const sourcePath = path.join(root, 'static', 'favoritos', 'v2', 'ui', 'status-modal.js');
const promotionsSource = promotionEffectuationSource(root);

async function criarPagina(browser, modoRaf) {
    const page = await browser.newPage();
    await page.setContent(`<!doctype html>
        <html><body>
            <div id="ml-favoritos-status"></div>
            <div id="ml-work-modal-live-status"></div>
            <div id="ml-favoritos-status-balloon">
                <div class="ml-favoritos-balloon-title"></div>
                <div id="ml-favoritos-status-balloon-text"></div>
                <div id="ml-favoritos-status-balloon-actions"></div>
            </div>
        </body></html>`);
    await page.evaluate((modo) => {
        window.__rafCallbacks = [];
        window.__restauracoesNavegador = 0;
        window.restaurarNavegadorMlShellSeVisivel = () => {
            window.__restauracoesNavegador += 1;
        };
        if (modo === 'normal') {
            window.requestAnimationFrame = callback => window.setTimeout(callback, 0);
        } else if (modo === 'suspenso' || modo === 'tardio') {
            window.requestAnimationFrame = callback => {
                window.__rafCallbacks.push(callback);
                return window.__rafCallbacks.length;
            };
        } else if (modo === 'ausente') {
            window.requestAnimationFrame = undefined;
        } else if (modo === 'erro') {
            window.requestAnimationFrame = () => {
                throw new Error('rAF indisponivel no renderer');
            };
        }
    }, modoRaf);
    await page.addScriptTag({ path: sourcePath });
    return page;
}

async function dispararAcao(page, valor, opcoes = {}) {
    return page.evaluate(async ({ valorAcao, duplicar }) => {
        const actions = document.getElementById('ml-favoritos-status-balloon-actions');
        const botao = document.createElement('button');
        botao.type = 'button';
        botao.textContent = 'Confirmar';
        actions.appendChild(botao);
        window.__acaoChamadas = 0;
        window.__acaoValores = [];
        const resolver = valorResolvido => {
            window.__acaoChamadas += 1;
            window.__acaoValores.push(valorResolvido);
        };
        window.favoritosResolverAcaoBalao(resolver, valorAcao, botao, { esconder: true });
        if (duplicar) {
            window.favoritosResolverAcaoBalao(resolver, valorAcao, botao, { esconder: true });
        }
        const limite = Date.now() + 1800;
        while (!window.__acaoChamadas && Date.now() < limite) {
            await new Promise(resolve => setTimeout(resolve, 10));
        }
        return {
            chamadas: window.__acaoChamadas,
            valores: window.__acaoValores.slice(),
            disabled: botao.disabled,
            busy: botao.getAttribute('aria-busy'),
            clicked: botao.dataset.favoritosActionClicked,
            rafPendentes: window.__rafCallbacks.length,
            restauracoes: window.__restauracoesNavegador
        };
    }, { valorAcao: valor, duplicar: !!opcoes.duplicar });
}

async function testarDecisaoCruzada(browser) {
    const page = await criarPagina(browser, 'suspenso');
    await page.evaluate(() => {
        window.mlFavoritosBalloonEl = document.getElementById('ml-favoritos-status-balloon');
        window.mlFavoritosBalloonTextEl = document.getElementById('ml-favoritos-status-balloon-text');
        window.mlFavoritosBalloonActionsEl = document.getElementById('ml-favoritos-status-balloon-actions');
        window.mlFavoritosPerguntaResolver = null;
        window.FavoritosV2.searchRanking = {
            publicApi: {
                status: {
                    mostrarBalaoFavoritosStatus: (...args) => window.FavoritosV2.ui.statusModal.mostrarBalaoFavoritosStatus(...args),
                    esconderBalaoFavoritosStatus: (...args) => window.FavoritosV2.ui.statusModal.esconderBalaoFavoritosStatus(...args)
                }
            }
        };
    });
    await page.addScriptTag({ content: promotionsSource });
    const resultado = await page.evaluate(async () => {
        const decisao = window.perguntarConfirmacaoEfetivarFavoritos({
            total: 1,
            nomeCampanha: 'Campanha teste'
        });
        const botoes = Array.from(document.querySelectorAll('#ml-favoritos-status-balloon-actions button'));
        const cancelar = botoes.find(botao => botao.textContent === 'Cancelar');
        const aprovar = botoes.find(botao => botao.textContent === 'Aprovar e alterar');
        if (!cancelar || !aprovar) throw new Error('Botoes da confirmacao nao encontrados.');
        aprovar.dispatchEvent(new MouseEvent('click', { bubbles: true }));
        cancelar.dispatchEvent(new MouseEvent('click', { bubbles: true }));
        const valor = await decisao;
        await new Promise(resolve => setTimeout(resolve, 180));
        return {
            valor,
            restauracoes: window.__restauracoesNavegador,
            aprovarClicado: aprovar.dataset.favoritosActionClicked,
            cancelarClicado: cancelar.dataset.favoritosActionClicked || ''
        };
    });
    assert.strictEqual(resultado.valor, true, 'a primeira decisao deve vencer a corrida');
    assert.strictEqual(resultado.restauracoes, 1, 'Aprovar/Cancelar cruzados devem finalizar e restaurar uma unica vez');
    assert.strictEqual(resultado.aprovarClicado, '1');
    assert.strictEqual(resultado.cancelarClicado, '', 'a segunda decisao nao deve ser agendada');
    await page.close();
}

async function main() {
    const browser = await chromium.launch({ headless: true });
    try {
        const normal = await criarPagina(browser, 'normal');
        const normalResultado = await dispararAcao(normal, true);
        assert.deepStrictEqual(normalResultado.valores, [true], 'rAF normal deve confirmar com o valor original');
        assert.strictEqual(normalResultado.chamadas, 1, 'rAF normal deve resolver uma unica vez');
        assert.strictEqual(normalResultado.disabled, true, 'botao deve ficar bloqueado antes do trabalho assincrono');
        assert.strictEqual(normalResultado.busy, 'true');
        await normal.close();

        const suspenso = await criarPagina(browser, 'suspenso');
        const suspensoResultado = await dispararAcao(suspenso, false);
        assert.strictEqual(suspensoResultado.chamadas, 1, 'rAF suspenso deve cair no fallback e resolver');
        assert.deepStrictEqual(suspensoResultado.valores, [false], 'cancelamento deve preservar false no fallback');
        assert.ok(suspensoResultado.rafPendentes >= 1, 'cenario deve realmente manter o callback de rAF suspenso');
        await suspenso.close();

        const tardio = await criarPagina(browser, 'tardio');
        const tardioAntes = await dispararAcao(tardio, true);
        assert.strictEqual(tardioAntes.chamadas, 1, 'fallback deve concluir antes do rAF tardio');
        const tardioDepois = await tardio.evaluate(async () => {
            const callbacks = window.__rafCallbacks.splice(0);
            callbacks.forEach(callback => callback(performance.now()));
            await new Promise(resolve => setTimeout(resolve, 80));
            return {
                callbacksExecutados: callbacks.length,
                chamadas: window.__acaoChamadas,
                restauracoes: window.__restauracoesNavegador
            };
        });
        assert.ok(tardioDepois.callbacksExecutados >= 1, 'teste deve entregar o rAF atrasado depois do fallback');
        assert.strictEqual(tardioDepois.chamadas, 1, 'rAF tardio nao pode repetir a resolucao');
        assert.strictEqual(
            tardioDepois.restauracoes,
            tardioAntes.restauracoes,
            'rAF tardio nao pode repetir fechamento/restauracao depois do fallback'
        );
        await tardio.close();

        const ausente = await criarPagina(browser, 'ausente');
        const ausenteResultado = await dispararAcao(ausente, true);
        assert.strictEqual(ausenteResultado.chamadas, 1, 'ausencia de rAF deve usar temporizador');
        assert.deepStrictEqual(ausenteResultado.valores, [true]);
        await ausente.close();

        const erro = await criarPagina(browser, 'erro');
        const erroResultado = await dispararAcao(erro, true);
        assert.strictEqual(erroResultado.chamadas, 1, 'excecao ao agendar rAF deve usar fallback');
        assert.deepStrictEqual(erroResultado.valores, [true]);
        await erro.close();

        const duplicado = await criarPagina(browser, 'normal');
        const duplicadoResultado = await dispararAcao(duplicado, true, { duplicar: true });
        assert.strictEqual(duplicadoResultado.chamadas, 1, 'a mesma acao nao pode resolver duas vezes');
        assert.strictEqual(duplicadoResultado.clicked, '1');
        await duplicado.close();

        await testarDecisaoCruzada(browser);

        console.log('OK: acoes do modal resolvem uma vez com rAF normal, suspenso, tardio, ausente ou com erro.');
    } finally {
        await browser.close();
    }
}

main().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
