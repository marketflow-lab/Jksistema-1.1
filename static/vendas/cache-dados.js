(function instalarCacheVendas() {
    'use strict';

    const estados = new Map();
    const capturas = new WeakMap();
    const api = () => window.JKCachePersistente;
    function identidadeRede() {
        try { return JSON.stringify(['user_data', 'permissions', 'access_token'].map(chave => localStorage.getItem(chave))); }
        catch (_erro) { return ''; }
    }
    function contexto() {
        const capturado = api()?.contexto() || {};
        capturas.set(capturado, { cacheAtivo: Boolean(api()?.vigente(capturado)), identidade: identidadeRede() });
        return capturado;
    }
    function vigente(capturado) {
        const captura = capturas.get(capturado);
        if (captura && !captura.cacheAtivo) return captura.identidade === identidadeRede();
        return !api() || api().vigente(capturado);
    }
    function dadosVigentes(capturado) {
        if (!capturas.get(capturado)?.cacheAtivo) return vigente(capturado);
        return api()?.dadosVigentes ? api().dadosVigentes(capturado, ['vendas']) : vigente(capturado);
    }
    async function ler(chave, capturado) {
        if (!capturas.get(capturado)?.cacheAtivo) return null;
        try { return await api()?.ler(chave, { contexto: capturado }) || null; }
        catch (_erro) { return null; }
    }
    async function salvar(chave, dados, capturado) {
        if (!capturas.get(capturado)?.cacheAtivo) return false;
        try { return await api()?.salvar(chave, dados, { contexto: capturado, tags: ['vendas'] }) || false; }
        catch (_erro) { return false; }
    }
    function estado(tipo, situacao, salvoEm) {
        if (!situacao) estados.delete(tipo);
        else estados.set(tipo, { situacao, salvoEm: salvoEm || estados.get(tipo)?.salvoEm });
        const painel = document.getElementById('cacheVendasStatus');
        if (!painel) return;
        painel.hidden = estados.size === 0;
        painel.textContent = [...estados].map(([nome, valor]) => {
            const data = new Date(valor.salvoEm);
            const momento = Number.isNaN(data.getTime()) ? '' : ' em ' + data.toLocaleString('pt-BR');
            return nome + ': dados salvos' + momento + '. '
                + (valor.situacao === 'erro' ? 'Atualização indisponível; última versão mantida.' : 'Conferindo atualização...');
        }).join(' ');
        window.dispatchEvent(new CustomEvent('jk:vendas-cache-state'));
    }
    function limpar() {
        estados.clear();
        estado('', null);
    }
    window.JKVendasCache = Object.freeze({ contexto, vigente, dadosVigentes, ler, salvar, estado, limpar });
})();
