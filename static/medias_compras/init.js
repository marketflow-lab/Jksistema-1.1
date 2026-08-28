(function installJKMediasInit(global) {
    'use strict';

    const app = global.JKMedias;
    app.defineModule('init', ['core', 'state', 'api', 'transito', 'filtros', 'tabela-colunas', 'listas', 'editor', 'editor-produtos', 'importacao', 'tabela'], () => {
        function executarAcao(elemento) {
            const action = elemento.dataset.jkAction;
            if (action === 'periodo') return carregarVisao(Number(elemento.dataset.periodo));
            if (action === 'aba') return selecionarAba(elemento.dataset.aba || 'lista');
            if (action === 'limpar-pesquisa') return limparPesquisaSku();
            if (action === 'produtos-sem-venda') return abrirPaginaProdutosSemVenda();
            if (action === 'abrir-lista-compra') return abrirModalListaCompra();
            if (action === 'baixar-sugestao') return baixarListaSugestaoExcel();
            if (action === 'importar-lista') return importarListaPedidoPorExcel();
            if (action === 'abrir-adicionar-sku') return abrirModalAdicionarSkuPedido();
            if (action === 'salvar-lista') return salvarListaPedidoAtual();
            if (action === 'fechar-lista-compra') return fecharModalListaCompra();
            if (action === 'gerar-lista-compra') return gerarListaCompraExcel();
            if (action === 'fechar-adicionar-sku') return fecharModalAdicionarSkuPedido();
            if (action === 'confirmar-adicionar-sku') return confirmarAdicionarSkuPedido();
            if (action === 'fechar-aviso') return fecharAvisoSkuCentral();
            if (action === 'reexibir-sku') return reexibirSkuPorCodigo(elemento.dataset.sku || '');
            if (action === 'ocultar-sku') return ocultarSkuPorCodigo(elemento.dataset.sku || '');
            return undefined;
        }

        function ligarEventos() {
            document.addEventListener('click', (event) => {
                const elemento = event.target && event.target.closest ? event.target.closest('[data-jk-action]') : null;
                if (!elemento) return;
                event.preventDefault();
                executarAcao(elemento);
            });
            const pesquisa = document.getElementById('pesquisaSkuInput');
            if (pesquisa) pesquisa.addEventListener('input', () => atualizarPesquisaSku(pesquisa.value));
        }

        function iniciar() {
            app.assertReady();
            ligarEventos();
            renderBotoesLojas([]);
            carregarSkusOcultosServidor().finally(() => {
                carregarLojas().finally(() => carregarVisao(12));
            });
        }

        iniciar();
        return { iniciar, ligarEventos };
    });
})(typeof globalThis !== 'undefined' ? globalThis : this);
