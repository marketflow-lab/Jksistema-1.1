(function installJKMediasListasCache(global) {
    'use strict';

    global.JKMedias.defineModule('listas-cache', ['core', 'state', 'api', 'resultados'], ({ modules }) => {
        const tags = ['importacoes'];
        const api = () => global.JKCachePersistente;

        function capturar(chave) {
            const contexto = api()?.contexto();
            return {
                chave: JSON.stringify(['medias-listas-pedidos-v1', chave]), contexto,
                sessao: modules.resultados.identidadeSessao(),
                persistente: !!api()?.vigente(contexto), invalidada: false,
            };
        }

        function capturarDetalhe(id) {
            return capturar(['detalhe', String(id), String(lojaSelecionada || '__todas'), String(storeIdSelecionado || '')]);
        }

        function vigente(leitura, verificarDados = true) {
            if (!leitura || leitura.invalidada || leitura.sessao !== modules.resultados.identidadeSessao()) return false;
            if (!leitura.persistente) return true;
            return verificarDados ? !!api()?.dadosVigentes(leitura.contexto, tags) : !!api()?.vigente(leitura.contexto);
        }

        async function ler(leitura) {
            if (!leitura?.persistente || !vigente(leitura)) return null;
            try {
                const salvo = await api()?.ler(leitura.chave, { contexto: leitura.contexto });
                return vigente(leitura) ? salvo : null;
            } catch (_erro) { return null; }
        }

        async function salvar(leitura, dados) {
            if (!leitura?.persistente || !vigente(leitura)) return false;
            try {
                const confirmado = modules.resultados.copiarResultado(dados);
                return !!await api()?.salvar(leitura.chave, confirmado, { contexto: leitura.contexto, tags });
            }
            catch (_erro) { return false; }
        }

        async function invalidar(leitura) {
            if (!vigente(leitura)) return false;
            leitura.invalidada = true;
            try { return !!await api()?.invalidar(tags); }
            catch (_erro) { return false; }
        }

        function confirmarMutacao(leitura, resposta) {
            if (!vigente(leitura, false) || !leitura.persistente) return null;
            const contexto = api()?.confirmarMutacao(leitura.contexto, resposta);
            return contexto ? { ...leitura, contexto } : null;
        }

        function texto(salvo, falhou = false) {
            const data = new Date(salvo?.salvoEm);
            const horario = Number.isFinite(data.getTime()) ? ' em ' + data.toLocaleString('pt-BR') : '';
            return 'Dados salvos' + horario + '. ' + (falhou
                ? 'Atualização indisponível; última versão mantida.' : 'Atualizando dados...');
        }

        return { cacheListasPedidos: Object.freeze({ capturar, capturarDetalhe, vigente, ler, salvar, invalidar, confirmarMutacao, texto }) };
    });
})(typeof globalThis !== 'undefined' ? globalThis : this);
