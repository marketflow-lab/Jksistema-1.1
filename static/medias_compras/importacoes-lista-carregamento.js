(function installJKImportacoesListaCarregamento(global) {
    'use strict';

    function aguardarAtualizacaoLista(controladoresAuxiliares, vigente, espera) {
        const controller = new AbortController();
        controladoresAuxiliares.add(controller);
        return new Promise((resolve, reject) => {
            let timer;
            const encerrar = erro => {
                clearTimeout(timer);
                controller.signal.removeEventListener('abort', cancelar);
                controladoresAuxiliares.delete(controller);
                if (erro) reject(erro);
                else resolve();
            };
            const cancelar = () => encerrar(new Error('Carregamento substituído.'));
            controller.signal.addEventListener('abort', cancelar, { once: true });
            timer = setTimeout(() => encerrar(), espera);
            if (!vigente()) controller.abort();
        });
    }

    async function carregar(pagina, { propagarErro, reconsultas, limite }) {
        const { persistencia, iniciar, controladoresAuxiliares, obterAuthHeaders, statusCacheLista,
            negarAcessoCacheLista, setErro, limparExibicao, timeoutConsultaMs } = pagina;
        const podeManter = contexto => !contexto || (persistencia?.podeManterDados
            ? persistencia.podeManterDados(contexto)
            : persistencia?.dadosVigentes?.(contexto) !== false);
        const ciclo = iniciar(persistencia, podeManter);
        const { listaId, contextoPersistente, vigente, dadosVigentes } = ciclo;
        let { copiaVisivel } = ciclo;
        const url = '/api/medias-compras/listas-pedidos/' + encodeURIComponent(listaId);
        const chave = 'importacoes:GET:' + url;
        const reconsultar = async (propagar = propagarErro) => {
            if (!vigente()) return;
            if (reconsultas > 0 && Date.now() < limite) {
                statusCacheLista('Consultando a versão atual da lista...');
                return carregar(pagina, { propagarErro: propagar, reconsultas: reconsultas - 1, limite });
            }
            const mensagem = 'A atualização da lista continua pendente. Tente novamente.';
            if (!podeManter(contextoPersistente)) negarAcessoCacheLista(mensagem);
            else statusCacheLista(mensagem);
            if (propagar) throw new Error(mensagem);
        };
        let respostaCompleta = false;
        const aplicar = (data, aguardarEdicao = false, completa = false) => ciclo.aplicar(data, {
            aguardarEdicao, completa, reconsultar,
            // O estado pode mudar enquanto a página espera o fim de uma edição.
            podeAplicar: () => dadosVigentes() && (completa || !respostaCompleta),
        });
        try {
            setErro('');
            if (!listaId) throw new Error('Lista não informada.');
            try {
                const copia = !copiaVisivel && contextoPersistente
                    ? await persistencia.ler(chave, { contexto: contextoPersistente, permitirDesatualizado: true }) : null;
                if (!vigente()) return;
                if (copia && await aplicar(copia.dados)) {
                    copiaVisivel = true;
                    statusCacheLista('Cópia salva em ' + new Date(copia.salvoEm).toLocaleString('pt-BR') + ' · Atualizando...');
                }
            } catch (_cacheError) { /* A consulta principal também atende uma cópia inválida. */ }
            if (!copiaVisivel && contextoPersistente) {
                const controller = new AbortController();
                controladoresAuxiliares.add(controller);
                const timeout = setTimeout(() => controller.abort(), 5000);
                void fetch(url + '?somente_salvos=true', { headers: { ...obterAuthHeaders() }, signal: controller.signal })
                    .then(resp => resp.ok ? resp.json() : null)
                    .then(async data => {
                        if (!respostaCompleta && vigente() && await aplicar(data)) {
                            copiaVisivel = true;
                            statusCacheLista('Dados salvos da lista · Atualizando informações complementares...');
                        }
                    }).catch(() => {}).finally(() => {
                        clearTimeout(timeout);
                        controladoresAuxiliares.delete(controller);
                    });
            }
            const transitorios = new Set(['sources_changing', 'cache_capacity', 'stores_snapshot_initializing',
                'stores_snapshot_unavailable', 'stores_snapshot_credentials_unavailable']);
            for (let tentativa = 0; tentativa < 32 && Date.now() < limite; tentativa++) {
                if (!vigente()) return;
                if (!dadosVigentes()) return reconsultar();
                const controller = new AbortController();
                controladoresAuxiliares.add(controller);
                const timeout = setTimeout(() => controller.abort(), Math.min(timeoutConsultaMs, limite - Date.now()));
                let data, esperaMinima = 0;
                try {
                    const resp = await fetch(url + '?cache_mode=stale', {
                        method: 'GET', headers: { ...obterAuthHeaders() }, signal: controller.signal,
                    });
                    if (!vigente()) return;
                    if ([401, 403].includes(resp.status)) {
                        negarAcessoCacheLista();
                        await persistencia?.invalidar(['importacoes']);
                        if (propagarErro) throw new Error('Acesso à lista indisponível após a atualização.');
                        return;
                    }
                    if (!resp.ok) {
                        const err = await resp.json().catch(() => ({}));
                        const code = err.detail?.code || err.code;
                        if (![409, 503].includes(resp.status) || !transitorios.has(code)) {
                            throw new Error(err.detail?.message || err.detail || 'Falha ao carregar detalhes da lista.');
                        }
                        esperaMinima = 5000;
                    } else {
                        data = await resp.json();
                    }
                } catch (erro) {
                    if (!controller.signal.aborted) throw erro;
                } finally {
                    clearTimeout(timeout);
                    controladoresAuxiliares.delete(controller);
                }
                if (!vigente()) return;
                if (!dadosVigentes()) return reconsultar();
                if (data) {
                    const meta = data._cache;
                    if (data.lista) {
                        if (String(data.lista.id) !== listaId || !Array.isArray(data.lista.itens)) throw new Error('Dados da lista inválidos.');
                        const pronta = !meta || (meta.estado === 'ready' && meta.completo !== false && !meta.stale && !meta.atualizando);
                        if (pronta) {
                            respostaCompleta = true;
                            if (await aplicar(data, copiaVisivel, true) || !vigente()) return;
                            if (!dadosVigentes()) return reconsultar();
                            throw new Error('Dados da lista inválidos.');
                        }
                        if (!copiaVisivel && await aplicar(data)) copiaVisivel = true;
                    } else if (!meta) {
                        throw new Error('Dados da lista inválidos.');
                    }
                    if (meta?.erro && !meta.atualizando) {
                        if (!transitorios.has(meta.erro.code)) throw new Error(meta.erro.message || 'Não foi possível atualizar a lista.');
                        esperaMinima = 5000;
                    }
                    esperaMinima = Math.max(esperaMinima, Math.min(10000, Number(meta?.retry_after_ms) || 0));
                }
                statusCacheLista(copiaVisivel ? 'Dados salvos da lista · Atualizando cálculos...'
                    : 'Preparando os dados da lista...');
                const intervalo = tentativa < 5 ? 1000 : tentativa < 10 ? 2000 : 5000;
                const espera = Math.min(Math.max(intervalo, esperaMinima), limite - Date.now());
                if (espera > 0) await aguardarAtualizacaoLista(controladoresAuxiliares, vigente, espera);
            }
            throw new Error('A atualização dos cálculos continua pendente. Tente novamente.');
        } catch (e) {
            if (!vigente()) return;
            respostaCompleta = true;
            if (!dadosVigentes()) return reconsultar();
            if (propagarErro) throw e;
            if (copiaVisivel) {
                statusCacheLista('Exibindo os dados salvos. Não foi possível atualizar agora.');
                return;
            }
            setErro(e.message || 'Erro ao carregar lista de importação.');
            limparExibicao();
        }
    }

    global.JKImportacoesListaCarregamento = { carregar };
})(window);
