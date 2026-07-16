        function formatarResumoColetaFavoritosTela(resumo) {
            if (!resumo || typeof resumo !== 'object') return '';
            const pesquisa = Number(resumo.pesquisa) || 1;
            const visiveis = Number(resumo.visiveis) || Number(resumo.coletados) || 0;
            const coletados = Number(resumo.coletados) || 0;
            const comTitulo = Number(resumo.com_titulo) || 0;
            const comFoto = Number(resumo.com_foto) || 0;
            const comPreco = Number(resumo.com_preco) || 0;
            const comLink = Number(resumo.com_link) || 0;
            const comDadosAvant = Number(resumo.com_dados_avant) || 0;
            const incompletos = Number(resumo.incompletos) || 0;
            const suspeitos = Number(resumo.suspeitos) || 0;
            const avantNaoVinculado = Number(resumo.avant_nao_vinculado) || 0;
            const base = `Pesquisa ${pesquisa} concluida: ${visiveis} visiveis | ${coletados} coletados | ${comTitulo} com titulo | ${comFoto} com foto | ${comPreco} com preco | ${comLink} com link | ${comDadosAvant} com Avant | ${incompletos} incompletos | ${suspeitos} suspeitos`;
            const motivos = Object.entries(resumo.motivos_incompletos || {})
                .filter(([, valor]) => Number(valor) > 0)
                .sort((a, b) => Number(b[1]) - Number(a[1]) || a[0].localeCompare(b[0]))
                .slice(0, 5)
                .map(([chave, valor]) => `${chave}=${valor}`)
                .join(', ');
            return `${base}${motivos ? ` | faltas: ${motivos}` : ''}${avantNaoVinculado ? ` | ${avantNaoVinculado} Avant ignorados sem MLB/link` : ''}`;
        }

        function criarBlocoResumoColetaFavoritos(grupo) {
            const resumos = Array.isArray(grupo && grupo.resumo_coleta)
                ? grupo.resumo_coleta.map(formatarResumoColetaFavoritosTela).filter(Boolean)
                : [];
            if (!resumos.length) return null;
            const bloco = document.createElement('div');
            bloco.className = 'ml-favoritos-termos ml-favoritos-resumo-coleta';
            bloco.textContent = resumos.join(' | ');
            return bloco;
        }

        function renderizarFavoritosPesquisaResultados(grupos) {
            if (!mlFavoritosPanelEl || !mlFavoritosListEl || !mlFavoritosEmptyEl) return;
            mlFavoritosPanelEl.classList.remove('hidden');
            mlFavoritosListEl.innerHTML = '';
            mlFavoritosEmptyEl.classList.toggle('hidden', (grupos || []).length > 0);
            if (mlFavoritosPanelTitleEl) {
                const todosAvulsos = (grupos || []).length > 0 && (grupos || []).every(grupo => grupo && grupo.avulso);
                mlFavoritosPanelTitleEl.textContent = todosAvulsos ? 'Ranqueamento avulso' : 'Favoritos dos SKUs selecionados';
            }

            (grupos || []).forEach(grupo => {
                const card = document.createElement('section');
                card.className = 'ml-favoritos-sku-card';

                const head = document.createElement('div');
                head.className = 'ml-favoritos-sku-head';
                const titulo = document.createElement('h4');
                titulo.className = 'ml-favoritos-sku-title';
                titulo.textContent = `${grupo.sku}${grupo.titulo ? ` - ${grupo.titulo}` : ''}`;
                const termos = document.createElement('div');
                termos.className = 'ml-favoritos-termos';
                const termosTexto = formatarTermosPesquisaFavoritos(grupo.termos);
                termos.textContent = termosTexto
                    ? `Pesquisas usadas: ${termosTexto}`
                    : 'Nenhuma pesquisa preenchida para este SKU.';
                head.appendChild(titulo);
                head.appendChild(termos);
                const resumoColeta = criarBlocoResumoColetaFavoritos(grupo);
                if (resumoColeta) head.appendChild(resumoColeta);
                card.appendChild(head);

                const anunciosVisiveis = filtrarAnunciosIgnoradosRanking(grupo.anuncios, grupo.sku);
                if (grupo.erro || !anunciosVisiveis.length) {
                    const empty = document.createElement('div');
                    empty.className = 'muted ml-favoritos-empty';
                    empty.textContent = grupo.erro || (
                        Array.isArray(grupo.anuncios) && grupo.anuncios.length
                            ? 'Todos os anuncios encontrados para este SKU estao na lista de ignorados.'
                            : 'Nenhum anuncio encontrado para as pesquisas deste SKU.'
                    );
                    card.appendChild(empty);
                    mlFavoritosListEl.appendChild(card);
                    return;
                }

                const wrap = document.createElement('div');
                wrap.className = 'ml-favoritos-table-wrap';
                const table = document.createElement('table');
                table.innerHTML = `
                    <thead>
                        <tr>
                            <th>Foto</th>
                            <th>Rank</th>
                            <th>MLB</th>
                            <th>Media mensal</th>
                            <th>Dias</th>
                            <th>Titulo</th>
                            <th>Preco</th>
                            <th>Tipo</th>
                        </tr>
                    </thead>
                    <tbody></tbody>
                `;
                const tbody = table.querySelector('tbody');
                anunciosVisiveis.forEach((anuncio, index) => {
                    const tr = document.createElement('tr');
                    tr.appendChild(criarCelulaFotoAnuncioFavoritos(anuncio));
                    const valores = [
                        `#${index + 1}`,
                        anuncio.id || extrairItemIdAnuncio(anuncio.url) || '',
                        formatarMediaVendas(anuncio),
                        formatarDiasAnuncio(anuncio),
                        anuncio.titulo || ''
                    ];
                    valores.forEach(valor => {
                        const td = document.createElement('td');
                        td.textContent = valor;
                        tr.appendChild(td);
                    });
                    tr.appendChild(criarCelulaPrecoAnuncioFavoritos(anuncio, [
                        { rotulo: 'Vendas', valor: formatarVendasAvantPro(anuncio) }
                    ]));
                    tr.appendChild(criarCelulaTipoAnuncioFavoritos(anuncio, anuncio.vendedor || ''));
                    tbody.appendChild(tr);
                });
                wrap.appendChild(table);
                card.appendChild(wrap);
                mlFavoritosListEl.appendChild(card);
            });
        }

        function normalizarAnuncioHistoricoFavoritosFrontend(anuncio) {
            if (!anuncio || typeof anuncio !== 'object') return anuncio;
            const item = { ...anuncio };
            const id = item.id || extrairItemIdAnuncio(item.url || item.permalink || item.link) || '';
            if (id) {
                item.id = id;
                item.mlb = item.mlb || id;
            }
            const url = typeof limparLinkProdutoMercadoLivreFavoritos === 'function'
                ? limparLinkProdutoMercadoLivreFavoritos(item.url || item.permalink || item.link, id)
                : (item.url || item.permalink || item.link || '');
            if (url) {
                item.url = url;
                item.permalink = url;
                item.link = url;
                item.link_normalizado = item.link_normalizado || url;
            }
            let titulo = String(item.titulo || item.title || '').replace(/\s+/g, ' ').trim();
            if (typeof tituloAnuncioFavoritosPrecisaComplemento === 'function' && tituloAnuncioFavoritosPrecisaComplemento(titulo, id)) {
                titulo = '';
            }
            if (!titulo && typeof extrairTituloAnuncioFavoritosPorLink === 'function') {
                titulo = extrairTituloAnuncioFavoritosPorLink(item.url || item.permalink || item.link);
            }
            item.titulo = titulo;
            item.title = titulo;
            const imagem = obterImagemAnuncioFavoritos(item);
            if (imagem) {
                item.imagem = imagem;
                item.thumbnail = imagem;
                item.foto = item.foto || imagem;
            }
            if (!item.moeda && !item.currency_id) {
                item.moeda = 'BRL';
                item.currency_id = 'BRL';
            }
            if (typeof obterPrecosAnuncioFavoritos === 'function') {
                const precos = obterPrecosAnuncioFavoritos(item);
                if (precos && (precos.preco !== null || precos.promocional !== null)) {
                    const precoBase = precos.preco !== null ? precos.preco : precos.promocional;
                    const precoFinal = precos.promocional !== null ? precos.promocional : precoBase;
                    item.preco = precoBase;
                    item.price = precoFinal;
                    if (precos.promocional !== null && precos.preco !== null) {
                        item.preco_original = item.preco_original || precos.preco;
                        item.original_price = item.original_price || precos.preco;
                        item.preco_promocional = item.preco_promocional || precos.promocional;
                        item.promotional_price = item.promotional_price || precos.promocional;
                        item.discount_pct = item.discount_pct || precos.desconto || '';
                    }
                }
            }
            item.historico_estatico = item.historico_estatico !== false;
            item.ranking_historico_estatico = item.ranking_historico_estatico !== false;
            item.bloquear_atualizacao_historico = item.bloquear_atualizacao_historico !== false;
            item.fonte_registro = item.fonte_registro || 'historico_ranqueamento';
            return item;
        }

        function anuncioRankingHistoricoEstaticoFavoritos(anuncio) {
            return !!(anuncio && (
                anuncio.historico_estatico
                || anuncio.ranking_historico_estatico
                || anuncio.bloquear_atualizacao_historico
                || anuncio._historicoRankingEstatico
            ));
        }

        function marcarAnuncioRankingHistoricoEstaticoFavoritos(anuncio) {
            if (!anuncio || typeof anuncio !== 'object') return anuncio;
            anuncio.historico_estatico = true;
            anuncio.ranking_historico_estatico = true;
            anuncio.bloquear_atualizacao_historico = true;
            anuncio.fonte_registro = anuncio.fonte_registro || 'historico_ranqueamento';
            return anuncio;
        }

        function marcarGrupoRankingHistoricoEstaticoFavoritos(grupo) {
            if (!grupo || typeof grupo !== 'object') return grupo;
            grupo.historico_estatico = true;
            grupo.ranking_historico_estatico = true;
            grupo.bloquear_atualizacao_historico = true;
            if (Array.isArray(grupo.anuncios)) grupo.anuncios.forEach(marcarAnuncioRankingHistoricoEstaticoFavoritos);
            if (Array.isArray(grupo.removidos_ia)) grupo.removidos_ia.forEach(marcarAnuncioRankingHistoricoEstaticoFavoritos);
            return grupo;
        }

        function grupoRankingHistoricoEstaticoFavoritos(grupo, entradaSelecionada = '') {
            const entrada = String(entradaSelecionada || favMlHistoricoExecucaoSelecionadaId || '').trim();
            return !!(grupo && (
                grupo.historico_estatico
                || grupo.ranking_historico_estatico
                || grupo.bloquear_atualizacao_historico
                || (entrada && entrada !== FAV_ML_RANKING_ATUAL_ID)
            ));
        }

        function textoHistoricoFavoritosSeguro(valor, limite = 500) {
            const texto = String(valor === null || valor === undefined ? '' : valor).replace(/\s+/g, ' ').trim();
            if (!texto) return '';
            const max = Number(limite) > 0 ? Number(limite) : 500;
            return texto.length > max ? `${texto.slice(0, max - 1)}...` : texto;
        }

        function normalizarDuracaoExecucaoFavoritosMs(valor) {
            if (valor === null || valor === undefined || valor === '') return null;
            const numero = Number(valor);
            if (!Number.isFinite(numero) || numero < 0) return null;
            return Math.min(Math.floor(numero), 7 * 24 * 60 * 60 * 1000);
        }

        function formatarDuracaoExecucaoFavoritos(valor) {
            const duracaoMs = normalizarDuracaoExecucaoFavoritosMs(valor);
            if (duracaoMs === null) return '';
            const totalSegundos = Math.floor(duracaoMs / 1000);
            const horas = Math.floor(totalSegundos / 3600);
            const minutos = Math.floor((totalSegundos % 3600) / 60);
            const segundos = totalSegundos % 60;
            return [horas, minutos, segundos]
                .map(item => String(item).padStart(2, '0'))
                .join(':');
        }

        function normalizarLinhaRelatorioAlteracaoFavoritos(linha) {
            if (!linha) return null;
            if (typeof linha === 'string') {
                const detalheTexto = textoHistoricoFavoritosSeguro(linha, 900);
                return detalheTexto ? { tipo: 'info', titulo: 'Relatorio', detalhe: detalheTexto } : null;
            }
            if (typeof linha !== 'object') return null;
            const detalhe = textoHistoricoFavoritosSeguro(linha.detalhe || linha.descricao || linha.mensagem || '', 900);
            const titulo = textoHistoricoFavoritosSeguro(linha.titulo || linha.title || linha.itemId || linha.item_id || 'Relatorio', 160);
            if (!detalhe && !titulo) return null;
            return {
                ...linha,
                tipo: textoHistoricoFavoritosSeguro(linha.tipo || linha.status || 'info', 40),
                titulo,
                detalhe
            };
        }

        function normalizarRelatorioAlteracaoFavoritos(relatorio) {
            if (!relatorio) return null;
            if (typeof relatorio === 'string') {
                const detalhe = textoHistoricoFavoritosSeguro(relatorio, 1200);
                return detalhe ? { titulo: 'Relatorio', resumo: detalhe, linhas: [] } : null;
            }
            if (typeof relatorio !== 'object') return null;
            const linhas = Array.isArray(relatorio.linhas)
                ? relatorio.linhas.map(normalizarLinhaRelatorioAlteracaoFavoritos).filter(Boolean)
                : [];
            return {
                ...relatorio,
                titulo: textoHistoricoFavoritosSeguro(relatorio.titulo || relatorio.title || 'Relatorio', 160),
                resumo: textoHistoricoFavoritosSeguro(relatorio.resumo || relatorio.mensagem || relatorio.status || '', 1200),
                linhas
            };
        }

        function normalizarVinculoAlteracaoFavoritos(vinculo) {
            const item = vinculo && typeof vinculo === 'object' ? vinculo : {};
            return {
                ...item,
                ordem: Number(item.ordem || item.rank || item.posicao || item.index || 0) || 0,
                sku: textoHistoricoFavoritosSeguro(item.sku || '', 80),
                loja: textoHistoricoFavoritosSeguro(item.loja || '', 120),
                itemId: textoHistoricoFavoritosSeguro(item.itemId || item.item_id || '', 40),
                status: textoHistoricoFavoritosSeguro(item.status || item.tipo || 'info', 40),
                status_texto: textoHistoricoFavoritosSeguro(item.status_texto || item.mensagem || '', 300),
                nosso: normalizarAnuncioHistoricoFavoritosFrontend(item.nosso || item.anuncio || {}),
                base: normalizarAnuncioHistoricoFavoritosFrontend(item.base || item.ranking || {}),
                relatorio_inicial: normalizarLinhaRelatorioAlteracaoFavoritos(item.relatorio_inicial),
                relatorio_final: normalizarLinhaRelatorioAlteracaoFavoritos(item.relatorio_final),
                simulacao: item.simulacao && typeof item.simulacao === 'object' ? { ...item.simulacao } : {}
            };
        }

        function normalizarHistoricoAlteracaoFavoritos(snapshot) {
            const item = snapshot && typeof snapshot === 'object' ? snapshot : {};
            return {
                ...item,
                data_iso: textoHistoricoFavoritosSeguro(item.data_iso || '', 80),
                loja: textoHistoricoFavoritosSeguro(item.loja || '', 120),
                usuario: textoHistoricoFavoritosSeguro(item.usuario || item.nome_usuario || '', 120),
                sku: textoHistoricoFavoritosSeguro(item.sku || '', 80),
                titulo: textoHistoricoFavoritosSeguro(item.titulo || '', 220),
                mensagem_final: textoHistoricoFavoritosSeguro(item.mensagem_final || '', 1200),
                relatorio_inicial: normalizarRelatorioAlteracaoFavoritos(item.relatorio_inicial),
                relatorio_final: normalizarRelatorioAlteracaoFavoritos(item.relatorio_final),
                vinculos: Array.isArray(item.vinculos)
                    ? item.vinculos.map(normalizarVinculoAlteracaoFavoritos).filter(Boolean)
                    : []
            };
        }

        function normalizarHistoricoFavoritosFrontend(lista) {
            if (!Array.isArray(lista)) return [];
            const usuarioAtual = nomeUsuarioHistoricoFavoritosAtual();
            const usernameAtual = usernameHistoricoFavoritosAtual();
            return lista
                .filter(Boolean)
                .map(entrada => {
                    if (!entrada || typeof entrada !== 'object') return entrada;
                    const usuarioEntrada = obterUsuarioHistoricoFavoritos(entrada) || usuarioAtual;
                    return {
                        ...entrada,
                        usuario: entrada.usuario || usuarioEntrada,
                        nome_usuario: entrada.nome_usuario || usuarioEntrada,
                        username: entrada.username || usernameAtual || usuarioEntrada,
                        duracao_execucao_ms: normalizarDuracaoExecucaoFavoritosMs(entrada.duracao_execucao_ms),
                        grupos: Array.isArray(entrada.grupos)
                            ? entrada.grupos.map(grupo => ({
                                ...grupo,
                                duracao_execucao_ms: normalizarDuracaoExecucaoFavoritosMs(grupo && grupo.duracao_execucao_ms),
                                anuncios: Array.isArray(grupo && grupo.anuncios)
                                    ? grupo.anuncios.map(normalizarAnuncioHistoricoFavoritosFrontend)
                                    : [],
                                removidos_ia: Array.isArray(grupo && grupo.removidos_ia)
                                    ? grupo.removidos_ia.map(normalizarAnuncioHistoricoFavoritosFrontend)
                                    : []
                            }))
                            : [],
                        alteracoes_favoritos: Array.isArray(entrada.alteracoes_favoritos)
                            ? entrada.alteracoes_favoritos.map(normalizarHistoricoAlteracaoFavoritos).filter(Boolean)
                            : []
                    };
                })
                .slice(0, ML_FAVORITOS_HISTORICO_MAX);
        }

        function chaveHistoricoFavoritos() {
            const cid = userData && userData.client_id ? String(userData.client_id) : 'default';
            const usuario = userData && (userData.username || userData.email || userData.name || userData.nome)
                ? String(userData.username || userData.email || userData.name || userData.nome)
                : 'usuario';
            const usuarioKey = usuario.trim().toLowerCase().replace(/[^a-z0-9_-]+/gi, '_').slice(0, 60) || 'usuario';
            return `favoritos_ml_historico_${cid}_${usuarioKey}`;
        }

        function lerHistoricoFavoritosLocal() {
            try {
                const bruto = window.localStorage ? window.localStorage.getItem(chaveHistoricoFavoritos()) : null;
                const lista = bruto ? JSON.parse(bruto) : [];
                return normalizarHistoricoFavoritosFrontend(lista);
            } catch (err) {
                console.warn('Nao foi possivel carregar cache local do historico de favoritos:', err);
                return [];
            }
        }

        function salvarHistoricoFavoritosLocal(lista) {
            try {
                if (window.localStorage) {
                    window.localStorage.setItem(chaveHistoricoFavoritos(), JSON.stringify(normalizarHistoricoFavoritosFrontend(lista)));
                }
            } catch (err) {
                console.warn('Nao foi possivel salvar cache local do historico de favoritos:', err);
            }
        }

        function lerHistoricoFavoritos() {
            if (!mlHistoricoFavoritosCache.length && !mlHistoricoFavoritosServidorCarregado) {
                mlHistoricoFavoritosCache = lerHistoricoFavoritosLocal();
            }
            return normalizarHistoricoFavoritosFrontend(mlHistoricoFavoritosCache);
        }

        async function salvarHistoricoFavoritosServidor(lista, opcoes = {}) {
            const historico = normalizarHistoricoFavoritosFrontend(lista);
            const finalizacao = opcoes && opcoes.finalizarDuracao && typeof opcoes.finalizarDuracao === 'object'
                ? opcoes.finalizarDuracao
                : null;
            const finalizarIds = [...new Set((Array.isArray(finalizacao && finalizacao.ids) ? finalizacao.ids : [])
                .map(value => String(value || '').trim())
                .filter(Boolean))];
            const inicioExecucaoMs = Number(finalizacao && finalizacao.inicio_execucao_ms) || 0;
            const body = { historico };
            if (finalizarIds.length && inicioExecucaoMs > 0) {
                body.finalizar_ids = finalizarIds;
                body.inicio_execucao_ms = Math.trunc(inicioExecucaoMs);
            }
            const controller = typeof AbortController === 'function' ? new AbortController() : null;
            const timeout = controller ? setTimeout(() => controller.abort(), 30000) : null;
            let response;
            try {
                response = await fetch('/api/favoritos/historico', {
                    method: 'PUT',
                    headers: headersJsonAutenticado(),
                    body: JSON.stringify(body),
                    ...(controller ? { signal: controller.signal } : {})
                });
            } finally {
                if (timeout) clearTimeout(timeout);
            }
            if (!response.ok) {
                let detalhe = `HTTP ${response.status}`;
                try {
                    const dataErro = await response.json();
                    detalhe = dataErro.detail || detalhe;
                } catch (_err) {}
                throw new Error(detalhe);
            }
            const data = await response.json();
            const remoto = normalizarHistoricoFavoritosFrontend(data.historico);
            mlHistoricoFavoritosCache = remoto;
            mlHistoricoFavoritosServidorCarregado = true;
            salvarHistoricoFavoritosLocal(remoto);
            agendarSincronizacaoHistoricoFavoritosVinculos();
            return {
                historico: remoto,
                duracao_execucao_ms: normalizarDuracaoExecucaoFavoritosMs(data.duracao_execucao_ms),
                finalizados_ids: Array.isArray(data.finalizados_ids) ? data.finalizados_ids : [],
                finalizado_em_ms: Number(data.finalizado_em_ms) || 0
            };
        }

        function enfileirarSalvamentoHistoricoFavoritosServidor(lista, opcoes = {}) {
            const historico = normalizarHistoricoFavoritosFrontend(lista);
            const anterior = mlHistoricoFavoritosPersistenciaFila || Promise.resolve({ success: true, historico: [] });
            const tarefa = Promise.resolve(anterior)
                .catch(() => null)
                .then(() => salvarHistoricoFavoritosServidor(historico, opcoes))
                .then(resultadoServidor => ({
                    success: true,
                    historico: normalizarHistoricoFavoritosFrontend(resultadoServidor && resultadoServidor.historico),
                    duracao_execucao_ms: normalizarDuracaoExecucaoFavoritosMs(resultadoServidor && resultadoServidor.duracao_execucao_ms),
                    finalizados_ids: Array.isArray(resultadoServidor && resultadoServidor.finalizados_ids)
                        ? resultadoServidor.finalizados_ids
                        : [],
                    finalizado_em_ms: Number(resultadoServidor && resultadoServidor.finalizado_em_ms) || 0,
                    erro: ''
                }))
                .catch(err => {
                    tratarErroSalvarHistoricoFavoritosServidor(err);
                    return {
                        success: false,
                        historico: [],
                        erro: err && err.message ? err.message : String(err)
                    };
                });
            mlHistoricoFavoritosPersistenciaFila = tarefa;
            mlHistoricoFavoritosUltimaPersistenciaPromise = tarefa;
            return tarefa;
        }

        async function confirmarSalvamentoHistoricoFavoritosServidor(idsEsperados = [], opcoes = {}) {
            const ids = [...new Set((Array.isArray(idsEsperados) ? idsEsperados : [])
                .map(value => String(value || '').trim())
                .filter(Boolean))];
            const duracaoEsperadaMs = normalizarDuracaoExecucaoFavoritosMs(opcoes && opcoes.duracao_execucao_ms);
            const resultado = await Promise.resolve(
                mlHistoricoFavoritosUltimaPersistenciaPromise
                || mlHistoricoFavoritosPersistenciaFila
                || { success: true, historico: lerHistoricoFavoritos() }
            );
            if (!resultado || resultado.success !== true) {
                return {
                    success: false,
                    ids,
                    faltantes: ids,
                    erro: resultado && resultado.erro || 'O servidor nao confirmou o salvamento do historico.'
                };
            }
            const entradasServidor = normalizarHistoricoFavoritosFrontend(resultado.historico);
            const mapaServidor = new Map(entradasServidor
                .map(item => [String(item && item.id || '').trim(), item])
                .filter(([id]) => Boolean(id)));
            const idsServidor = new Set(mapaServidor.keys());
            const faltantes = ids.filter(id => !idsServidor.has(id));
            const duracaoDivergente = duracaoEsperadaMs === null
                ? []
                : ids.filter(id => {
                    const entrada = mapaServidor.get(id);
                    return normalizarDuracaoExecucaoFavoritosMs(entrada && entrada.duracao_execucao_ms) !== duracaoEsperadaMs;
                });
            const duracaoGruposDivergente = duracaoEsperadaMs === null
                ? []
                : ids.filter(id => {
                    const entrada = mapaServidor.get(id);
                    return (Array.isArray(entrada && entrada.grupos) ? entrada.grupos : []).some(grupo => (
                        normalizarDuracaoExecucaoFavoritosMs(grupo && grupo.duracao_execucao_ms) !== duracaoEsperadaMs
                    ));
                });
            return {
                success: faltantes.length === 0 && duracaoDivergente.length === 0 && duracaoGruposDivergente.length === 0,
                ids,
                faltantes,
                duracao_execucao_ms: duracaoEsperadaMs,
                duracao_divergente_ids: duracaoDivergente,
                duracao_grupos_divergente_ids: duracaoGruposDivergente,
                historico: resultado.historico,
                erro: faltantes.length
                    ? `O servidor nao devolveu ${faltantes.length} registro(s) do historico salvo.`
                    : (duracaoDivergente.length || duracaoGruposDivergente.length
                        ? `O servidor nao confirmou o tempo de execucao completo em ${new Set([...duracaoDivergente, ...duracaoGruposDivergente]).size} registro(s).`
                        : '')
            };
        }

        function agendarSalvarHistoricoFavoritosServidor(lista) {
            const historico = normalizarHistoricoFavoritosFrontend(lista);
            if (mlHistoricoFavoritosSaveTimer) clearTimeout(mlHistoricoFavoritosSaveTimer);
            mlHistoricoFavoritosSaveTimer = setTimeout(() => {
                mlHistoricoFavoritosSaveTimer = null;
                enfileirarSalvamentoHistoricoFavoritosServidor(historico);
            }, 300);
        }

        function tratarErroSalvarHistoricoFavoritosServidor(err) {
            console.warn('Nao foi possivel salvar historico de favoritos no servidor:', err);
            if (mlHistoricoFavoritosStatusEl && document.getElementById('aba-historico')?.classList.contains('active')) {
                mlHistoricoFavoritosStatusEl.textContent = `Historico mantido em cache local, mas ainda nao foi salvo no servidor: ${err && err.message ? err.message : err}`;
            }
        }

        function salvarHistoricoFavoritos(lista, opcoes = {}) {
            const historico = normalizarHistoricoFavoritosFrontend(lista);
            mlHistoricoFavoritosCache = historico;
            salvarHistoricoFavoritosLocal(historico);
            if (opcoes && opcoes.imediato) {
                if (mlHistoricoFavoritosSaveTimer) {
                    clearTimeout(mlHistoricoFavoritosSaveTimer);
                    mlHistoricoFavoritosSaveTimer = null;
                }
                return enfileirarSalvamentoHistoricoFavoritosServidor(historico, opcoes);
            } else {
                agendarSalvarHistoricoFavoritosServidor(historico);
            }
            return null;
        }

        function duracaoExecucaoMescladaHistoricoFavoritos(...entradas) {
            const duracoes = [];
            entradas.forEach(entrada => {
                if (!entrada || typeof entrada !== 'object') return;
                const duracaoEntrada = normalizarDuracaoExecucaoFavoritosMs(entrada.duracao_execucao_ms);
                if (duracaoEntrada !== null) duracoes.push(duracaoEntrada);
                (Array.isArray(entrada.grupos) ? entrada.grupos : []).forEach(grupo => {
                    const duracaoGrupo = normalizarDuracaoExecucaoFavoritosMs(grupo && grupo.duracao_execucao_ms);
                    if (duracaoGrupo !== null) duracoes.push(duracaoGrupo);
                });
            });
            return duracoes.length ? Math.max(...duracoes) : null;
        }

        function preservarDuracaoExecucaoMergeHistoricoFavoritos(preferida, complementar = null) {
            const saida = { ...(preferida || {}) };
            const duracao = duracaoExecucaoMescladaHistoricoFavoritos(saida, complementar);
            if (duracao === null) return saida;
            saida.duracao_execucao_ms = duracao;
            if (Array.isArray(saida.grupos)) {
                saida.grupos = saida.grupos.map(grupo => ({
                    ...grupo,
                    duracao_execucao_ms: duracao
                }));
            }
            return saida;
        }

        function mesclarHistoricosFavoritos(...listas) {
            const mapa = new Map();
            listas.forEach(lista => {
                normalizarHistoricoFavoritosFrontend(lista).forEach(entrada => {
                    const chave = idEntradaHistoricoFavoritos(entrada) || `${entrada.data_iso || ''}_${mapa.size}`;
                    if (!chave) return;
                    if (mapa.has(chave)) {
                        mapa.set(chave, preservarDuracaoExecucaoMergeHistoricoFavoritos(mapa.get(chave), entrada));
                        return;
                    }
                    mapa.set(chave, entrada);
                });
            });
            return Array.from(mapa.values())
                .sort((a, b) => {
                    const dataA = Date.parse(a && a.data_iso || '') || 0;
                    const dataB = Date.parse(b && b.data_iso || '') || 0;
                    return dataB - dataA;
                })
                .slice(0, ML_FAVORITOS_HISTORICO_MAX);
        }

        function assinaturaHistoricoFavoritos(lista) {
            return normalizarHistoricoFavoritosFrontend(lista)
                .map(entrada => `${idEntradaHistoricoFavoritos(entrada) || entrada.data_iso || ''}:${entrada.duracao_execucao_ms ?? ''}`)
                .join('|');
        }

        async function carregarHistoricoFavoritosServidor() {
            const historicoLocal = lerHistoricoFavoritosLocal();
            mlHistoricoFavoritosCache = historicoLocal;
            try {
                const response = await fetch('/api/favoritos/historico', {
                    headers: obterAuthHeaders(),
                    cache: 'no-store'
                });
                if (!response.ok) {
                    let detalhe = `HTTP ${response.status}`;
                    try {
                        const dataErro = await response.json();
                        detalhe = dataErro.detail || detalhe;
                    } catch (_err) {}
                    throw new Error(detalhe);
                }
                const data = await response.json();
                const historicoServidor = normalizarHistoricoFavoritosFrontend(data.historico);
                mlHistoricoFavoritosServidorCarregado = true;
                const historicoAtual = mesclarHistoricosFavoritos(mlHistoricoFavoritosCache, lerHistoricoFavoritosLocal(), historicoLocal);
                const historicoMesclado = mesclarHistoricosFavoritos(historicoServidor, historicoAtual);
                if (historicoMesclado.length) {
                    mlHistoricoFavoritosCache = historicoMesclado;
                    salvarHistoricoFavoritosLocal(historicoMesclado);
                    if (assinaturaHistoricoFavoritos(historicoMesclado) !== assinaturaHistoricoFavoritos(historicoServidor)) {
                        const persistencia = await enfileirarSalvamentoHistoricoFavoritosServidor(historicoMesclado);
                        if (!persistencia || persistencia.success !== true) {
                            throw new Error(persistencia && persistencia.erro || 'Falha ao mesclar o historico no servidor.');
                        }
                    }
                } else {
                    mlHistoricoFavoritosCache = [];
                    salvarHistoricoFavoritosLocal([]);
                }
                if (document.getElementById('aba-historico')?.classList.contains('active')) {
                    renderizarHistoricoFavoritos();
                }
                if (document.getElementById('aba-favoritos')?.classList.contains('active')) {
                    renderizarFavoritosOutrosAnuncios(favMlSkuSelecionado);
                }
            } catch (err) {
                mlHistoricoFavoritosServidorCarregado = false;
                console.warn('Nao foi possivel carregar historico de favoritos do servidor:', err);
                if (mlHistoricoFavoritosStatusEl && document.getElementById('aba-historico')?.classList.contains('active')) {
                    mlHistoricoFavoritosStatusEl.textContent = `Usando cache local do historico. Erro ao acessar servidor: ${err && err.message ? err.message : err}`;
                }
            }
        }

        async function recarregarHistoricoFavoritosSincronizado(evento = {}) {
            const ts = Number(evento && evento.ts || Date.now());
            if (ts && ts <= mlHistoricoFavoritosSyncUltimoTs) return;
            mlHistoricoFavoritosSyncUltimoTs = ts || Date.now();
            await carregarHistoricoFavoritosServidor();
        }

        function inicializarSincronizacaoHistoricoFavoritos() {
            try {
                if (typeof BroadcastChannel === 'function') {
                    mlHistoricoFavoritosSyncBroadcast = new BroadcastChannel(ML_FAVORITOS_HISTORICO_SYNC_CHANNEL);
                    mlHistoricoFavoritosSyncBroadcast.onmessage = (event) => {
                        const data = event && event.data ? event.data : {};
                        if (data && data.tipo === 'historico-favoritos-importado') {
                            recarregarHistoricoFavoritosSincronizado(data);
                        }
                    };
                }
            } catch (_err) {
                mlHistoricoFavoritosSyncBroadcast = null;
            }
            window.addEventListener('storage', (event) => {
                if (!event || event.key !== ML_FAVORITOS_HISTORICO_SYNC_STORAGE_KEY || !event.newValue) return;
                try {
                    const data = JSON.parse(event.newValue);
                    if (data && data.tipo === 'historico-favoritos-importado') {
                        recarregarHistoricoFavoritosSincronizado(data);
                    }
                } catch (_err) {}
            });
        }

        function agendarSincronizacaoHistoricoFavoritosVinculos() {
            if (mlHistoricoFavoritosSyncVinculosTimer) clearTimeout(mlHistoricoFavoritosSyncVinculosTimer);
            mlHistoricoFavoritosSyncVinculosTimer = setTimeout(() => {
                mlHistoricoFavoritosSyncVinculosTimer = null;
                try {
                    if (typeof window.jkFavoritosHistoricoSyncNow === 'function') {
                        window.jkFavoritosHistoricoSyncNow();
                    }
                } catch (err) {
                    console.warn('Nao foi possivel iniciar sync do historico de favoritos entre usuarios:', err);
                }
            }, 1200);
        }

        function anuncioHistoricoPayload(anuncio) {
            const precos = obterPrecosAnuncioFavoritos(anuncio);
            const id = anuncio && (anuncio.id || extrairItemIdAnuncio(anuncio.url || anuncio.permalink || anuncio.link) || '');
            const url = typeof limparLinkProdutoMercadoLivreFavoritos === 'function'
                ? limparLinkProdutoMercadoLivreFavoritos(anuncio && (anuncio.url || anuncio.permalink || anuncio.link), id)
                : (anuncio && (anuncio.url || anuncio.permalink || anuncio.link) || '');
            let titulo = String(anuncio && (anuncio.titulo || anuncio.title) || '').replace(/\s+/g, ' ').trim();
            if (typeof tituloAnuncioFavoritosPrecisaComplemento === 'function' && tituloAnuncioFavoritosPrecisaComplemento(titulo, id)) {
                titulo = '';
            }
            if (!titulo && typeof extrairTituloAnuncioFavoritosPorLink === 'function') {
                titulo = extrairTituloAnuncioFavoritosPorLink(anuncio && (anuncio.url || anuncio.permalink || anuncio.link) || url);
            }
            const imagem = obterImagemAnuncioFavoritos(anuncio);
            return {
                id: id || '',
                historico_estatico: true,
                ranking_historico_estatico: true,
                bloquear_atualizacao_historico: true,
                fonte_registro: 'historico_ranqueamento',
                url,
                permalink: url,
                link: url,
                link_normalizado: url,
                linkFonte: anuncio && (anuncio.linkFonte || anuncio.link_fonte || ''),
                link_fonte: anuncio && (anuncio.linkFonte || anuncio.link_fonte || ''),
                titulo,
                title: titulo,
                tituloFonte: anuncio && (anuncio.tituloFonte || anuncio.titulo_fonte || ''),
                titulo_fonte: anuncio && (anuncio.tituloFonte || anuncio.titulo_fonte || ''),
                vendedor: anuncio && (anuncio.vendedor || anuncio.seller_name || anuncio.sellerName || anuncio.seller_nickname || anuncio.sellerNickname || anuncio.nickname || anuncio.official_store_name || anuncio.officialStoreName || '') || '',
                vendedorFonte: anuncio && (anuncio.vendedorFonte || anuncio.vendedor_fonte || anuncio.fonte_vendedor || ''),
                vendedor_fonte: anuncio && (anuncio.vendedorFonte || anuncio.vendedor_fonte || anuncio.fonte_vendedor || ''),
                loja_vendedora: obterNomeLojaVendedoraHistoricoFavoritos(anuncio),
                seller_name: anuncio && (anuncio.seller_name || anuncio.sellerName || '') || '',
                seller_nickname: anuncio && (anuncio.seller_nickname || anuncio.sellerNickname || '') || '',
                official_store_name: anuncio && (anuncio.official_store_name || anuncio.officialStoreName || '') || '',
                vendas: anuncio && anuncio.vendas,
                vendasFonte: anuncio && (anuncio.vendasFonte || anuncio.vendas_fonte || ''),
                vendas_fonte: anuncio && (anuncio.vendasFonte || anuncio.vendas_fonte || ''),
                data_criacao: anuncio && anuncio.data_criacao || '',
                dataCriacaoFonte: anuncio && (anuncio.dataCriacaoFonte || anuncio.data_criacao_fonte || anuncio.fonte_data_criacao || ''),
                data_criacao_fonte: anuncio && (anuncio.dataCriacaoFonte || anuncio.data_criacao_fonte || anuncio.fonte_data_criacao || ''),
                imagem,
                thumbnail: imagem,
                foto: imagem,
                fotoFonte: anuncio && (anuncio.fotoFonte || anuncio.foto_fonte || ''),
                foto_fonte: anuncio && (anuncio.fotoFonte || anuncio.foto_fonte || ''),
                preco: precos.preco,
                price: precos.promocional !== null ? precos.promocional : precos.preco,
                preco_original: precos.promocional !== null ? precos.preco : '',
                original_price: precos.promocional !== null ? precos.preco : '',
                preco_promocional: precos.promocional,
                promotional_price: precos.promocional,
                discount_pct: precos.desconto || '',
                custo: anuncio && (anuncio.custo ?? anuncio.custo_unitario ?? anuncio.custo_produto ?? anuncio.preco_custo ?? anuncio.valor_custo ?? ''),
                custo_unitario: anuncio && (anuncio.custo_unitario ?? anuncio.custo ?? anuncio.custo_produto ?? anuncio.preco_custo ?? anuncio.valor_custo ?? ''),
                custo_produto: anuncio && (anuncio.custo_produto ?? anuncio.custo ?? anuncio.custo_unitario ?? anuncio.preco_custo ?? anuncio.valor_custo ?? ''),
                preco_custo: anuncio && (anuncio.preco_custo ?? anuncio.custo ?? anuncio.custo_unitario ?? anuncio.custo_produto ?? anuncio.valor_custo ?? ''),
                valor_custo: anuncio && (anuncio.valor_custo ?? anuncio.custo ?? anuncio.custo_unitario ?? anuncio.custo_produto ?? anuncio.preco_custo ?? ''),
                custo_frete: anuncio && (anuncio.custo_frete ?? anuncio.frete_ml ?? anuncio.shipping_cost ?? anuncio.shipping_seller_cost ?? ''),
                fonte_preco: fontePrecoFavoritos(anuncio),
                precoFonte: anuncio && (anuncio.precoFonte || anuncio.preco_fonte || anuncio.fonte_preco || ''),
                preco_fonte: anuncio && (anuncio.precoFonte || anuncio.preco_fonte || anuncio.fonte_preco || ''),
                moeda: anuncio && (anuncio.moeda || anuncio.currency_id || 'BRL'),
                currency_id: anuncio && (anuncio.currency_id || anuncio.moeda || 'BRL'),
                parcelamento_sem_juros: obterParcelamentoSemJurosFavoritos(anuncio),
                tipo_anuncio: obterTipoAnuncioFavoritos(anuncio),
                listing_type_id: anuncio && (anuncio.listing_type_id || anuncio.listingTypeId || ''),
                listing_type_name: anuncio && (anuncio.listing_type_name || anuncio.tipo_anuncio || ''),
                shipping: anuncio && (anuncio.shipping || anuncio.shipping_info || anuncio.shippingInfo || null),
                logistic_type: anuncio && (anuncio.logistic_type || anuncio.logisticType || anuncio.shipping_logistic_type || ''),
                shipping_mode: anuncio && (anuncio.shipping_mode || anuncio.shippingMode || ''),
                is_full: temIndicadorFullFavoritos(anuncio) ? obterFullAnuncioFavoritos(anuncio) : '',
                media_mensal: anuncio && (anuncio.media_mensal ?? anuncio.ritmo_atual ?? anuncio.ritmo_vendas_mes ?? ''),
                ritmo_atual: anuncio && (anuncio.ritmo_atual ?? anuncio.media_mensal ?? ''),
                media_mensal_fonte: anuncio && (anuncio.media_mensal_fonte || anuncio.ritmo_atual_fonte || ''),
                media_vendas_mensal: anuncio && anuncio.media_vendas_mensal,
                meses_desde_criacao: anuncio && anuncio.meses_desde_criacao,
                pesquisas_origem: Array.isArray(anuncio && anuncio.pesquisas_origem) ? anuncio.pesquisas_origem.slice(0, 3) : [],
                campos_origem: Array.isArray(anuncio && anuncio.campos_origem) ? anuncio.campos_origem.slice(0, 3) : [],
                motivo_ia: anuncio && (anuncio.motivo_ia || anuncio.motivo || '')
            };
        }

        function grupoRankingFavoritosEhAvulso(grupo) {
            return Boolean(grupo && (grupo.avulso || grupo.pesquisa_avulsa || skuChaveSku(grupo.sku) === 'avulso'));
        }

        function entradaHistoricoFavoritosEhAvulsa(entrada) {
            return Array.isArray(entrada && entrada.grupos)
                && entrada.grupos.some(grupo => grupoRankingFavoritosEhAvulso(grupo));
        }

        function montarEntradaHistoricoFavoritos(grupos) {
            const agora = new Date();
            const gruposHistorico = (Array.isArray(grupos) ? grupos : [])
                .map(grupo => {
                    const anuncios = Array.isArray(grupo && grupo.anuncios)
                        ? grupo.anuncios.slice(0, ML_FAVORITOS_RANKING_ANUNCIOS_MAX).map(anuncioHistoricoPayload)
                        : [];
                    const removidosIa = Array.isArray(grupo && grupo.removidos_ia)
                        ? grupo.removidos_ia.slice(0, 80).map(anuncioHistoricoPayload)
                        : [];
                    const opcoesPromocao = resolverOpcoesPromocaoGrupoFavoritos(grupo, grupo && grupo.sku);
                    const duracaoExecucaoMs = normalizarDuracaoExecucaoFavoritosMs(grupo && grupo.duracao_execucao_ms);
                    return {
                        sku: grupo && grupo.sku || '',
                        titulo: grupo && grupo.titulo || '',
                        historico_estatico: true,
                        ranking_historico_estatico: true,
                        bloquear_atualizacao_historico: true,
                        termos: Array.isArray(grupo && grupo.termos) ? grupo.termos : [],
                        opcoes_promocao: opcoesPromocao || null,
                        avulso: grupoRankingFavoritosEhAvulso(grupo),
                        pesquisa_avulsa: grupoRankingFavoritosEhAvulso(grupo),
                        duracao_execucao_ms: duracaoExecucaoMs,
                        duracao_tarefa_ms: Math.max(0, Number(grupo && grupo.duracao_tarefa_ms) || 0),
                        timings: grupo && grupo.timings && typeof grupo.timings === 'object'
                            ? { ...grupo.timings }
                            : null,
                        total_anuncios: Array.isArray(grupo && grupo.anuncios) ? grupo.anuncios.length : 0,
                        usou_ia: !!(grupo && grupo.usou_ia),
                        ia_confirmados: Number(grupo && grupo.ia_confirmados) || 0,
                        ia_max_confirmados: Number(grupo && grupo.ia_max_confirmados) || 0,
                        removidos_ia_total: Number(grupo && grupo.removidos_ia_total) || removidosIa.length,
                        resumo_coleta: Array.isArray(grupo && grupo.resumo_coleta)
                            ? grupo.resumo_coleta.map(resumo => ({
                                pesquisa: Number(resumo && resumo.pesquisa) || 1,
                                termo: resumo && resumo.termo || '',
                                campo: resumo && resumo.campo || '',
                                visiveis: Number(resumo && resumo.visiveis) || 0,
                                coletados: Number(resumo && resumo.coletados) || 0,
                                com_titulo: Number(resumo && resumo.com_titulo) || 0,
                                com_foto: Number(resumo && resumo.com_foto) || 0,
                                com_preco: Number(resumo && resumo.com_preco) || 0,
                                com_link: Number(resumo && resumo.com_link) || 0,
                                com_dados_avant: Number(resumo && resumo.com_dados_avant) || 0,
                                incompletos: Number(resumo && resumo.incompletos) || 0,
                                suspeitos: Number(resumo && resumo.suspeitos) || 0,
                                avant_nao_vinculado: Number(resumo && resumo.avant_nao_vinculado) || 0,
                                tempo_esgotado: !!(resumo && resumo.tempo_esgotado),
                                login_avant_bloqueado: !!(resumo && resumo.login_avant_bloqueado),
                                motivo_encerramento: String(resumo && resumo.motivo_encerramento || ''),
                                passadas: Number(resumo && resumo.passadas) || 0,
                                posicoes_percorridas: Number(resumo && resumo.posicoes_percorridas) || 0,
                                cliques_avant: Number(resumo && resumo.cliques_avant) || 0,
                                capturados_avant: Number(resumo && resumo.capturados_avant) || 0,
                                tempo_navegacao_ms: Number(resumo && resumo.tempo_navegacao_ms) || 0,
                                tempo_materializacao_ms: Number(resumo && resumo.tempo_materializacao_ms) || 0,
                                tempo_avant_ms: Number(resumo && resumo.tempo_avant_ms) || 0,
                                tempo_finalizacao_ms: Number(resumo && resumo.tempo_finalizacao_ms) || 0,
                                tempo_enriquecimento_ms: Number(resumo && resumo.tempo_enriquecimento_ms) || 0,
                                motivos_incompletos: resumo && resumo.motivos_incompletos && typeof resumo.motivos_incompletos === 'object'
                                    ? { ...resumo.motivos_incompletos }
                                    : {},
                                origens_dados: resumo && resumo.origens_dados && typeof resumo.origens_dados === 'object'
                                    ? { ...resumo.origens_dados }
                                    : {},
                                amostras_incompletos: Array.isArray(resumo && resumo.amostras_incompletos)
                                    ? resumo.amostras_incompletos.slice(0, 10).map(item => ({ ...item }))
                                    : []
                            }))
                            : [],
                        removidos_ia: removidosIa,
                        anuncios
                    };
                })
                .filter(grupo => grupo.sku && (grupo.anuncios.length || grupo.removidos_ia.length));

            const opcoesEntrada = gruposHistorico.map(grupo => grupo.opcoes_promocao).find(Boolean) || null;
            const duracoesExecucao = gruposHistorico
                .map(grupo => normalizarDuracaoExecucaoFavoritosMs(grupo.duracao_execucao_ms))
                .filter(valor => valor !== null);
            const nomeUsuario = nomeUsuarioHistoricoFavoritosAtual();
            const usernameUsuario = usernameHistoricoFavoritosAtual();
            return {
                id: `${agora.getTime()}_${Math.random().toString(36).slice(2, 8)}`,
                data_iso: agora.toISOString(),
                loja: favoritosLojaSelecionadaParaApi() || '',
                usuario: nomeUsuario,
                nome_usuario: nomeUsuario,
                username: usernameUsuario,
                opcoes_promocao: opcoesEntrada,
                duracao_execucao_ms: duracoesExecucao.length ? Math.max(...duracoesExecucao) : null,
                total_skus: gruposHistorico.length,
                total_anuncios: gruposHistorico.reduce((acc, grupo) => acc + grupo.total_anuncios, 0),
                grupos: gruposHistorico
            };
        }

        function registrarHistoricoFavoritos(grupos) {
            const entrada = montarEntradaHistoricoFavoritos(grupos);
            if (!entrada.grupos.length) return null;
            const historico = lerHistoricoFavoritos();
            historico.unshift(entrada);
            salvarHistoricoFavoritos(historico.slice(0, ML_FAVORITOS_HISTORICO_MAX), { imediato: true });
            if (document.getElementById('aba-historico')?.classList.contains('active')) {
                renderizarHistoricoFavoritos();
            }
            if (document.getElementById('aba-links-alinhados')?.classList.contains('active')) {
                renderizarLinksAlinhadosFavoritos();
            }
            return entrada;
        }

        function atualizarDuracaoExecucaoHistoricosFavoritos(idsHistorico = [], duracaoExecucaoMs = null) {
            const ids = new Set((Array.isArray(idsHistorico) ? idsHistorico : [])
                .map(value => String(value || '').trim())
                .filter(Boolean));
            const duracao = normalizarDuracaoExecucaoFavoritosMs(duracaoExecucaoMs);
            if (!ids.size || duracao === null) {
                return Promise.resolve({
                    success: false,
                    historico: [],
                    erro: 'Nao foi possivel associar o tempo de execucao ao historico.'
                });
            }
            let atualizados = 0;
            const historico = lerHistoricoFavoritos().map(entrada => {
                const id = String(entrada && entrada.id || '').trim();
                if (!ids.has(id)) return entrada;
                atualizados += 1;
                return {
                    ...entrada,
                    duracao_execucao_ms: duracao,
                    grupos: Array.isArray(entrada.grupos)
                        ? entrada.grupos.map(grupo => ({ ...grupo, duracao_execucao_ms: duracao }))
                        : []
                };
            });
            if (atualizados !== ids.size) {
                return Promise.resolve({
                    success: false,
                    historico,
                    erro: `Nao encontrei ${ids.size - atualizados} registro(s) para salvar o tempo de execucao.`
                });
            }
            return salvarHistoricoFavoritos(historico, { imediato: true });
        }

        async function finalizarDuracaoExecucaoHistoricosFavoritos(idsHistorico = [], inicioExecucaoMs = null) {
            const ids = [...new Set((Array.isArray(idsHistorico) ? idsHistorico : [])
                .map(value => String(value || '').trim())
                .filter(Boolean))];
            const inicio = Number(inicioExecucaoMs) || 0;
            const duracaoProvisoria = normalizarDuracaoExecucaoFavoritosMs(Date.now() - inicio);
            if (!ids.length || inicio <= 0 || duracaoProvisoria === null) {
                return {
                    success: false,
                    historico: [],
                    erro: 'Nao foi possivel finalizar o tempo de execucao no historico.'
                };
            }
            const idsSet = new Set(ids);
            let atualizados = 0;
            const historico = lerHistoricoFavoritos().map(entrada => {
                const id = String(entrada && entrada.id || '').trim();
                if (!idsSet.has(id)) return entrada;
                atualizados += 1;
                return {
                    ...entrada,
                    duracao_execucao_ms: duracaoProvisoria,
                    grupos: Array.isArray(entrada.grupos)
                        ? entrada.grupos.map(grupo => ({ ...grupo, duracao_execucao_ms: duracaoProvisoria }))
                        : []
                };
            });
            if (atualizados !== ids.length) {
                return {
                    success: false,
                    historico,
                    erro: `Nao encontrei ${ids.length - atualizados} registro(s) para finalizar o tempo de execucao.`
                };
            }
            const persistencia = await salvarHistoricoFavoritos(historico, {
                imediato: true,
                finalizarDuracao: {
                    ids,
                    inicio_execucao_ms: inicio
                }
            });
            if (!persistencia || persistencia.success !== true) return persistencia;
            const duracaoPersistida = normalizarDuracaoExecucaoFavoritosMs(persistencia.duracao_execucao_ms);
            if (duracaoPersistida === null) {
                return {
                    ...persistencia,
                    success: false,
                    erro: 'O servidor salvou o historico, mas nao devolveu a duracao final da execucao.'
                };
            }
            const confirmacao = await confirmarSalvamentoHistoricoFavoritosServidor(ids, {
                duracao_execucao_ms: duracaoPersistida
            });
            return {
                ...persistencia,
                ...confirmacao,
                duracao_execucao_ms: duracaoPersistida,
                finalizado_em_ms: Number(persistencia.finalizado_em_ms) || (inicio + duracaoPersistida)
            };
        }

        function registrarHistoricoAlteracoesFavoritos(snapshot) {
            const alteracao = normalizarHistoricoAlteracaoFavoritos(snapshot);
            if (!alteracao || !Array.isArray(alteracao.vinculos) || !alteracao.vinculos.length) return null;
            const agora = alteracao.data_iso || new Date().toISOString();
            const usuario = alteracao.usuario || nomeUsuarioHistoricoFavoritosAtual();
            const username = usernameHistoricoFavoritosAtual();
            const entrada = {
                id: `alt_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
                tipo: 'alteracao_favoritos',
                data_iso: agora,
                loja: alteracao.loja || favoritosLojaSelecionadaParaApi() || '',
                usuario,
                nome_usuario: usuario,
                username: username || usuario,
                total_skus: alteracao.sku ? 1 : 0,
                total_anuncios: alteracao.vinculos.length,
                grupos: [],
                alteracoes_favoritos: [alteracao]
            };
            const historico = lerHistoricoFavoritos();
            historico.unshift(entrada);
            salvarHistoricoFavoritos(historico.slice(0, ML_FAVORITOS_HISTORICO_MAX), { imediato: true });
            if (document.getElementById('aba-links-alinhados')?.classList.contains('active')) {
                renderizarLinksAlinhadosFavoritos();
            }
            return entrada;
        }

        function filtrarHistoricoFavoritosPorLojaAtual(historico, skuCompartilhado = '') {
            const lista = Array.isArray(historico) ? historico : [];
            // Historico de ranqueamento deve ser compartilhado entre lojas.
            // O filtro por loja continua valendo apenas para os anuncios atuais do SKU.
            return lista;
        }

        function lojaAtualHistoricoFavoritosEstaticoNormalizada() {
            const lojaAtual = typeof favoritosLojaSelecionadaParaApi === 'function'
                ? favoritosLojaSelecionadaParaApi()
                : (mlSkuLojaSelecionada || skuLojaSelecionada || '');
            if (!lojaAtual || (typeof favoritosEhTodasLojas === 'function' && favoritosEhTodasLojas(lojaAtual))) return '';
            return skuNormalizarLoja(lojaAtual);
        }

        function nomeLojaAtualHistoricoFavoritosEstatico() {
            const lojaAtual = typeof favoritosLojaSelecionadaParaApi === 'function'
                ? favoritosLojaSelecionadaParaApi()
                : (mlSkuLojaSelecionada || skuLojaSelecionada || '');
            if (!lojaAtual || (typeof favoritosEhTodasLojas === 'function' && favoritosEhTodasLojas(lojaAtual))) return '';
            return String(lojaAtual || '').trim();
        }

        function coletarLojasHistoricoFavoritosEstaticoFonte(fonte, lojas) {
            if (!fonte || typeof fonte !== 'object') return;
            [
                fonte.loja,
                fonte.loja_sync,
                fonte.lojaSync,
                fonte.nome_loja,
                fonte.nomeLoja,
                fonte.conta,
                fonte.conta_ml,
                fonte.contaMl,
                fonte.descricao_ml_loja,
                fonte.descricaoMlLoja
            ].forEach(valor => {
                const texto = String(valor || '').trim();
                if (texto) lojas.push(texto);
            });
        }

        function historicoFavoritoEstaticoPertenceLojaAtual(entrada, snapshot) {
            const lojaAtualNorm = lojaAtualHistoricoFavoritosEstaticoNormalizada();
            if (!lojaAtualNorm) return true;
            const lojas = [];
            coletarLojasHistoricoFavoritosEstaticoFonte(entrada, lojas);
            coletarLojasHistoricoFavoritosEstaticoFonte(snapshot, lojas);
            (Array.isArray(snapshot && snapshot.vinculos) ? snapshot.vinculos : []).forEach(vinculo => {
                coletarLojasHistoricoFavoritosEstaticoFonte(vinculo, lojas);
                coletarLojasHistoricoFavoritosEstaticoFonte(vinculo && vinculo.nosso, lojas);
            });
            return lojas.some(loja => skuNormalizarLoja(loja) === lojaAtualNorm);
        }

        function obterHistoricoMaisRecenteSku(sku) {
            const chave = skuChaveSku(sku);
            if (!chave) return null;
            for (const entrada of filtrarHistoricoFavoritosPorLojaAtual(lerHistoricoFavoritos(), sku)) {
                const grupo = (entrada.grupos || []).find(item => skuChaveSku(item && item.sku) === chave);
                if (grupo) {
                    const opcoesPromocao = resolverOpcoesPromocaoGrupoFavoritos(grupo, sku)
                        || resolverOpcoesPromocaoGrupoFavoritos(entrada, sku);
                    return {
                        entrada,
                        grupo: {
                            ...grupo,
                            historico_estatico: true,
                            ranking_historico_estatico: true,
                            bloquear_atualizacao_historico: true,
                            anuncios: Array.isArray(grupo.anuncios)
                                ? grupo.anuncios.map(anuncio => marcarAnuncioRankingHistoricoEstaticoFavoritos({ ...anuncio }))
                                : [],
                            removidos_ia: Array.isArray(grupo.removidos_ia)
                                ? grupo.removidos_ia.map(anuncio => marcarAnuncioRankingHistoricoEstaticoFavoritos({ ...anuncio }))
                                : [],
                            opcoes_promocao: opcoesPromocao || grupo.opcoes_promocao || null,
                            data_iso: entrada.data_iso,
                            loja: entrada.loja || grupo.loja || '',
                            usuario: obterUsuarioHistoricoFavoritos(entrada),
                            nome_usuario: obterUsuarioHistoricoFavoritos(entrada)
                        }
                    };
                }
            }
            return null;
        }

        function obterHistoricoFavoritosSelecionadoSku(sku, entradaId) {
            const chave = skuChaveSku(sku);
            const idSelecionado = String(entradaId || '').trim();
            if (!chave || !idSelecionado) return null;
            for (const entrada of filtrarHistoricoFavoritosPorLojaAtual(lerHistoricoFavoritos(), sku)) {
                if (idEntradaHistoricoFavoritos(entrada) !== idSelecionado) continue;
                const grupo = (entrada.grupos || []).find(item => skuChaveSku(item && item.sku) === chave);
                if (!grupo) return null;
                const opcoesPromocao = resolverOpcoesPromocaoGrupoFavoritos(grupo, sku)
                    || resolverOpcoesPromocaoGrupoFavoritos(entrada, sku);
                return {
                    entrada,
                    grupo: {
                        ...grupo,
                        historico_estatico: true,
                        ranking_historico_estatico: true,
                        bloquear_atualizacao_historico: true,
                        anuncios: Array.isArray(grupo.anuncios)
                            ? grupo.anuncios.map(anuncio => marcarAnuncioRankingHistoricoEstaticoFavoritos({ ...anuncio }))
                            : [],
                        removidos_ia: Array.isArray(grupo.removidos_ia)
                            ? grupo.removidos_ia.map(anuncio => marcarAnuncioRankingHistoricoEstaticoFavoritos({ ...anuncio }))
                            : [],
                        opcoes_promocao: opcoesPromocao || grupo.opcoes_promocao || null,
                        data_iso: entrada.data_iso,
                        loja: entrada.loja || grupo.loja || '',
                        usuario: obterUsuarioHistoricoFavoritos(entrada),
                        nome_usuario: obterUsuarioHistoricoFavoritos(entrada)
                    }
                };
            }
            return null;
        }

        function obterUltimoSkuHistoricoFavoritos() {
            for (const entrada of filtrarHistoricoFavoritosPorLojaAtual(lerHistoricoFavoritos())) {
                const grupo = (entrada.grupos || []).find(item => item && item.sku);
                if (grupo && grupo.sku) return String(grupo.sku).trim();
            }
            return '';
        }

        function idEntradaHistoricoFavoritos(entrada) {
            return String(entrada && (entrada.id || entrada.data_iso) || '').trim();
        }

        function montarUltimosFavoritosRankeados(limite = 10, historicoBase = null) {
            const historico = Array.isArray(historicoBase)
                ? historicoBase
                : filtrarHistoricoFavoritosPorLojaAtual(lerHistoricoFavoritos());
            const recentes = [];
            for (const entrada of historico) {
                const grupos = Array.isArray(entrada && entrada.grupos) ? entrada.grupos : [];
                for (const grupo of grupos) {
                    const sku = String(grupo && grupo.sku || '').trim();
                    if (!sku) continue;
                    const anuncios = filtrarAnunciosIgnoradosRanking(grupo && grupo.anuncios, grupo && grupo.sku);
                    if (!anuncios.length) continue;
                    recentes.push({
                        entrada,
                        entrada_id: idEntradaHistoricoFavoritos(entrada),
                        grupo: marcarGrupoRankingHistoricoEstaticoFavoritos({
                            ...grupo,
                            anuncios: anuncios.map(anuncio => marcarAnuncioRankingHistoricoEstaticoFavoritos({ ...anuncio })),
                            data_iso: entrada && entrada.data_iso || grupo.data_iso || '',
                            loja: entrada && entrada.loja || grupo.loja || '',
                            usuario: obterUsuarioHistoricoFavoritos(entrada),
                            nome_usuario: obterUsuarioHistoricoFavoritos(entrada)
                        }),
                        sku,
                        titulo: String(grupo && grupo.titulo || '').trim(),
                        data_iso: entrada && entrada.data_iso || grupo.data_iso || '',
                        loja: entrada && entrada.loja || grupo.loja || '',
                        usuario: obterUsuarioHistoricoFavoritos(entrada),
                        nome_usuario: obterUsuarioHistoricoFavoritos(entrada),
                        total_anuncios: anuncios.length
                    });
                    if (recentes.length >= limite) return recentes;
                }
            }
            return recentes;
        }

        function criarBotaoFavoritoRecente(item, onClick) {
            const botao = document.createElement('div');
            botao.className = 'ml-favoritos-recente-item';
            botao.setAttribute('role', 'button');
            botao.tabIndex = 0;
            const conteudo = document.createElement('div');
            conteudo.className = 'ml-favoritos-recente-content';
            const titulo = document.createElement('span');
            titulo.className = 'ml-favoritos-recente-title';
            const ehAvulso = grupoRankingFavoritosEhAvulso(item && (item.grupo || item));
            titulo.textContent = ehAvulso
                ? `Ranqueamento avulso${item.titulo ? ` - ${item.titulo}` : ''}`
                : `${item.sku}${item.titulo ? ` - ${item.titulo}` : ''}`;
            const data = formatarDataHistoricoFavoritos(item.data_iso) || 'data desconhecida';
            const duracaoExecucaoValor = item && item.duracao_execucao_ms !== null && item.duracao_execucao_ms !== undefined
                ? item.duracao_execucao_ms
                : (item && item.entrada ? item.entrada.duracao_execucao_ms : null);
            const duracaoExecucao = formatarDuracaoExecucaoFavoritos(duracaoExecucaoValor);
            const meta = document.createElement('span');
            meta.className = 'ml-favoritos-recente-meta';
            meta.textContent = `${item.total_anuncios} anuncio(s) rankeado(s) | ${data}${duracaoExecucao ? ` | Tempo total: ${duracaoExecucao}` : ''}${item.loja ? ` | Loja: ${item.loja}` : ''}${sufixoUsuarioHistoricoFavoritos(item.entrada || item)}`;
            conteudo.appendChild(titulo);
            conteudo.appendChild(meta);
            const termosTexto = formatarTermosPesquisaFavoritos(item && item.grupo && item.grupo.termos);
            if (termosTexto) {
                const termos = document.createElement('span');
                termos.className = 'ml-favoritos-recente-meta';
                termos.textContent = `Pesquisas: ${termosTexto}`;
                conteudo.appendChild(termos);
            }
            const excluirBtn = document.createElement('button');
            excluirBtn.type = 'button';
            excluirBtn.className = 'ml-favoritos-excluir-historico-btn';
            excluirBtn.textContent = 'Excluir';
            excluirBtn.title = 'Excluir este favorito do historico.';
            excluirBtn.addEventListener('click', (event) => {
                event.preventDefault();
                event.stopPropagation();
                removerFavoritoHistoricoIndividual(item);
            });
            botao.appendChild(conteudo);
            botao.appendChild(excluirBtn);
            botao.addEventListener('click', () => onClick(item));
            botao.addEventListener('keydown', (event) => {
                if (event.key !== 'Enter' && event.key !== ' ') return;
                event.preventDefault();
                onClick(item);
            });
            return botao;
        }

        function obterUrlAnuncioLinksAlinhadosFavoritos(anuncio) {
            return String(anuncio && (anuncio.url || anuncio.permalink || anuncio.link) || '').trim();
        }

        function obterMlbAnuncioLinksAlinhadosFavoritos(anuncio) {
            const url = obterUrlAnuncioLinksAlinhadosFavoritos(anuncio);
            return String(anuncio && (anuncio.id || anuncio.mlb || anuncio.item_id) || extrairItemIdAnuncio(url) || '').trim();
        }

        function obterTituloAnuncioLinksAlinhadosFavoritos(anuncio, fallback = '') {
            return textoHistoricoFavoritosSeguro(anuncio && (anuncio.titulo || anuncio.title) || fallback || '', 260);
        }

        function obterVendedorAnuncioLinksAlinhadosFavoritos(anuncio) {
            return textoHistoricoFavoritosSeguro(anuncio && (anuncio.vendedor || anuncio.seller || anuncio.seller_name) || '', 160);
        }

        function resumoRelatorioVinculoAlteracaoFavoritos(snapshot, vinculo) {
            const partes = [];
            const inicial = vinculo && vinculo.relatorio_inicial;
            const final = vinculo && vinculo.relatorio_final;
            if (inicial && inicial.detalhe) partes.push(`Antes: ${inicial.detalhe}`);
            if (final && final.detalhe) partes.push(`Depois: ${final.detalhe}`);
            if (snapshot && snapshot.mensagem_final) partes.push(`Resumo: ${snapshot.mensagem_final}`);
            return textoHistoricoFavoritosSeguro(partes.join(' | '), 1800);
        }

        function montarLinksAlinhadosFavoritos(limite = 500) {
            const links = [];
            const historico = filtrarHistoricoFavoritosPorLojaAtual(lerHistoricoFavoritos());
            for (const entrada of historico) {
                const alteracoes = Array.isArray(entrada && entrada.alteracoes_favoritos) ? entrada.alteracoes_favoritos : [];
                if (alteracoes.length) {
                    alteracoes.forEach(snapshot => {
                        const vinculos = Array.isArray(snapshot && snapshot.vinculos) ? snapshot.vinculos : [];
                        vinculos.forEach((vinculo, index) => {
                            const nosso = vinculo && vinculo.nosso || {};
                            const base = vinculo && vinculo.base || {};
                            const relatorioFinal = vinculo && vinculo.relatorio_final || {};
                            const status = String(vinculo && (vinculo.status || vinculo.tipo) || relatorioFinal.tipo || 'info').trim();
                            links.push({
                                tipo: 'alteracao',
                                data_iso: snapshot && snapshot.data_iso || entrada && entrada.data_iso || '',
                                usuario: snapshot && snapshot.usuario || obterUsuarioHistoricoFavoritos(entrada),
                                loja: vinculo && vinculo.loja || snapshot && snapshot.loja || entrada && entrada.loja || '',
                                sku: vinculo && vinculo.sku || snapshot && snapshot.sku || '',
                                rank: Number(vinculo && vinculo.ordem) || index + 1,
                                nosso_mlb: obterMlbAnuncioLinksAlinhadosFavoritos(nosso) || vinculo && vinculo.itemId || '',
                                nosso_url: obterUrlAnuncioLinksAlinhadosFavoritos(nosso),
                                nosso_vendedor: obterVendedorAnuncioLinksAlinhadosFavoritos(nosso),
                                nosso_titulo: obterTituloAnuncioLinksAlinhadosFavoritos(nosso),
                                base_mlb: obterMlbAnuncioLinksAlinhadosFavoritos(base),
                                base_url: obterUrlAnuncioLinksAlinhadosFavoritos(base),
                                base_vendedor: obterVendedorAnuncioLinksAlinhadosFavoritos(base),
                                base_titulo: obterTituloAnuncioLinksAlinhadosFavoritos(base),
                                status,
                                status_texto: textoHistoricoFavoritosSeguro(vinculo && vinculo.status_texto || relatorioFinal.titulo || (status === 'success' ? 'Alteracao feita' : 'Alteracao registrada'), 180),
                                relatorio: resumoRelatorioVinculoAlteracaoFavoritos(snapshot, vinculo),
                                relatorio_inicial: vinculo && vinculo.relatorio_inicial || null,
                                relatorio_final: relatorioFinal || null,
                                mensagem_final: snapshot && snapshot.mensagem_final || ''
                            });
                        });
                    });
                    if (links.length >= limite) return links.slice(0, limite);
                    continue;
                }
                const grupos = Array.isArray(entrada && entrada.grupos) ? entrada.grupos : [];
                for (const grupo of grupos) {
                    const anuncios = Array.isArray(grupo && grupo.anuncios) ? grupo.anuncios : [];
                    anuncios.forEach((anuncio, index) => {
                        const url = String(anuncio && (anuncio.url || anuncio.permalink || anuncio.link) || '').trim();
                        const mlb = String(anuncio && (anuncio.id || anuncio.mlb || anuncio.item_id) || extrairItemIdAnuncio(url) || '').trim();
                        if (!url && !mlb) return;
                        links.push({
                            tipo: 'ranking_legacy',
                            data_iso: entrada && entrada.data_iso || '',
                            usuario: obterUsuarioHistoricoFavoritos(entrada),
                            loja: entrada && entrada.loja || '',
                            sku: grupo && grupo.sku || '',
                            rank: index + 1,
                            nosso_mlb: '',
                            nosso_url: '',
                            nosso_vendedor: '',
                            nosso_titulo: '',
                            base_mlb: mlb,
                            base_url: url,
                            base_vendedor: String(anuncio && anuncio.vendedor || '').trim(),
                            base_titulo: String(anuncio && (anuncio.titulo || anuncio.title) || grupo && grupo.titulo || '').trim(),
                            status: 'info',
                            status_texto: 'Ranking salvo',
                            relatorio: 'Link alinhado salvo no historico antigo.'
                        });
                    });
                    if (links.length >= limite) return links.slice(0, limite);
                }
            }
            return links.slice(0, limite);
        }

        function criarCelulaLinksAlinhados(texto, opcoes = {}) {
            const td = document.createElement('td');
            if (opcoes.nowrap) td.style.whiteSpace = 'nowrap';
            if (opcoes.className) td.className = opcoes.className;
            td.textContent = texto || '-';
            if (opcoes.title) td.title = opcoes.title;
            return td;
        }

        function criarCelulaAnuncioLinksAlinhadosFavoritos(item, prefixo) {
            const td = document.createElement('td');
            td.className = 'ml-links-alinhados-ad';
            const mlb = item && item[`${prefixo}_mlb`] || '';
            const vendedor = item && item[`${prefixo}_vendedor`] || '';
            const titulo = item && item[`${prefixo}_titulo`] || '';
            if (!mlb && !vendedor && !titulo) {
                td.textContent = '-';
                return td;
            }
            if (mlb) {
                const strong = document.createElement('strong');
                strong.textContent = mlb;
                td.appendChild(strong);
            }
            if (vendedor) {
                const meta = document.createElement('span');
                meta.className = 'ml-links-alinhados-meta';
                meta.textContent = vendedor;
                td.appendChild(meta);
            }
            if (titulo) {
                const title = document.createElement('span');
                title.className = 'ml-links-alinhados-title';
                title.textContent = titulo;
                td.appendChild(title);
            }
            return td;
        }

        function criarCelulaStatusLinksAlinhadosFavoritos(item) {
            const td = document.createElement('td');
            const span = document.createElement('span');
            const status = String(item && item.status || 'info').toLowerCase();
            span.className = `ml-links-alinhados-status is-${status === 'success' || status === 'sucesso' ? 'success' : status === 'error' || status === 'falha' ? 'error' : 'info'}`;
            span.textContent = item && item.status_texto || '-';
            td.appendChild(span);
            return td;
        }

        function criarBadgeStatusLinksAlinhadosFavoritos(item) {
            const span = document.createElement('span');
            const status = String(item && item.status || 'info').toLowerCase();
            span.className = `ml-links-alinhados-status is-${status === 'success' || status === 'sucesso' ? 'success' : status === 'error' || status === 'falha' ? 'error' : 'info'}`;
            span.textContent = item && item.status_texto || '-';
            return span;
        }

        function normalizarAnuncioHistoricoFavoritoEstatico(anuncio) {
            const base = anuncio && typeof anuncio === 'object' ? { ...anuncio } : {};
            const normalizado = normalizarAnuncioHistoricoFavoritosFrontend(base) || base;
            return marcarAnuncioRankingHistoricoEstaticoFavoritos(normalizado);
        }

        function anuncioHistoricoFavoritoTemIdentidade(anuncio) {
            return !!(anuncio && (
                obterMlbAnuncioLinksAlinhadosFavoritos(anuncio)
                || obterUrlAnuncioLinksAlinhadosFavoritos(anuncio)
            ));
        }

        function vinculoHistoricoFavoritoRelacionado(vinculo) {
            if (!vinculo || typeof vinculo !== 'object') return false;
            return anuncioHistoricoFavoritoTemIdentidade(vinculo.nosso)
                && anuncioHistoricoFavoritoTemIdentidade(vinculo.base);
        }

        function montarHistoricosFavoritosAlteracoesEstaticas(limite = 80) {
            const saida = [];
            const historico = lerHistoricoFavoritos();
            for (const entrada of historico) {
                const alteracoes = Array.isArray(entrada && entrada.alteracoes_favoritos) ? entrada.alteracoes_favoritos : [];
                for (const snapshot of alteracoes) {
                    if (!historicoFavoritoEstaticoPertenceLojaAtual(entrada, snapshot)) continue;
                    const vinculos = (Array.isArray(snapshot && snapshot.vinculos) ? snapshot.vinculos : [])
                        .filter(vinculoHistoricoFavoritoRelacionado)
                        .map((vinculo, index) => ({
                            ...vinculo,
                            ordem: Number(vinculo.ordem || vinculo.rank || vinculo.posicao || index + 1) || index + 1,
                            nosso: normalizarAnuncioHistoricoFavoritoEstatico(vinculo.nosso),
                            base: normalizarAnuncioHistoricoFavoritoEstatico(vinculo.base)
                        }));
                    if (!vinculos.length) continue;
                    saida.push({
                        entrada,
                        snapshot,
                        data_iso: snapshot && snapshot.data_iso || entrada && entrada.data_iso || '',
                        loja: snapshot && snapshot.loja || entrada && entrada.loja || '',
                        usuario: snapshot && snapshot.usuario || obterUsuarioHistoricoFavoritos(entrada),
                        sku: snapshot && snapshot.sku || vinculos[0].sku || '',
                        titulo: snapshot && snapshot.titulo || '',
                        mensagem_final: snapshot && snapshot.mensagem_final || '',
                        vinculos
                    });
                    if (saida.length >= limite) return saida;
                }
            }
            return saida;
        }

        function criarTabelaHistoricoFavoritoEstatico(titulo, colunas, tipo = '') {
            const tipoClasse = String(tipo || '').trim().replace(/[^a-z0-9_-]+/gi, '');
            const painel = document.createElement('section');
            painel.className = `ml-links-alinhados-panel${tipoClasse ? ` is-${tipoClasse}` : ''}`;
            const h4 = document.createElement('h4');
            h4.textContent = titulo;
            painel.appendChild(h4);
            const wrap = document.createElement('div');
            wrap.className = 'ml-links-alinhados-table-wrap';
            const table = document.createElement('table');
            table.className = `ml-links-alinhados-table ml-links-alinhados-static-table${tipoClasse ? ` is-${tipoClasse}` : ''}`;
            const thead = document.createElement('thead');
            const trHead = document.createElement('tr');
            colunas.forEach(coluna => {
                const th = document.createElement('th');
                th.textContent = coluna;
                trHead.appendChild(th);
            });
            thead.appendChild(trHead);
            const tbody = document.createElement('tbody');
            table.appendChild(thead);
            table.appendChild(tbody);
            wrap.appendChild(table);
            painel.appendChild(wrap);
            return { painel, tbody };
        }

        function criarCelulaOrdemHistoricoFavorito(ordem) {
            const td = document.createElement('td');
            td.className = 'ml-favoritos-ordem-cell';
            td.textContent = `${Number(ordem) || 0}\u00ba`;
            return td;
        }

        function obterTipoHistoricoFavorito(anuncio, tipoFallback = '') {
            const tipoDireto = typeof obterTipoCompletoAnuncioFavoritos === 'function'
                ? obterTipoCompletoAnuncioFavoritos(anuncio)
                : '';
            if (tipoDireto) return tipoDireto;
            const candidatos = [
                anuncio && anuncio.tipo_anuncio,
                anuncio && anuncio.tipoAnuncio,
                anuncio && anuncio.tipo,
                anuncio && anuncio.listing_type_name,
                anuncio && anuncio.listingTypeName,
                anuncio && anuncio.listing_type_id,
                anuncio && anuncio.listingTypeId,
                tipoFallback
            ];
            for (const candidato of candidatos) {
                const tipo = typeof normalizarTipoAnuncioFavoritos === 'function'
                    ? normalizarTipoAnuncioFavoritos(candidato)
                    : String(candidato || '').trim();
                if (tipo) return tipo;
                if (typeof nomeTipoPorListingTypeFavoritos === 'function') {
                    const nome = nomeTipoPorListingTypeFavoritos(candidato);
                    if (nome) return nome;
                }
            }
            return 'Tipo nao informado';
        }

        function criarCelulaMlbHistoricoFavorito(anuncio, lojaFallback = '', tipoFallback = '') {
            const td = document.createElement('td');
            td.className = 'ml-favoritos-mlb-cell';
            const mlb = obterMlbAnuncioLinksAlinhadosFavoritos(anuncio);
            const codigo = document.createElement('span');
            codigo.className = 'ml-favoritos-mlb-main';
            codigo.textContent = mlb || '';
            td.appendChild(codigo);
            const loja = textoHistoricoFavoritosSeguro(
                anuncio && (anuncio.loja || anuncio.loja_sync || anuncio.loja_conta || anuncio.loja_vendedora || anuncio.vendedor)
                || lojaFallback
                || '',
                160
            );
            if (loja) {
                const lojaEl = document.createElement('span');
                lojaEl.className = 'ml-favoritos-mlb-loja';
                lojaEl.textContent = loja;
                td.appendChild(lojaEl);
            }
            const tipo = obterTipoHistoricoFavorito(anuncio, tipoFallback);
            if (tipo) {
                const tipoEl = document.createElement('span');
                tipoEl.className = 'ml-favoritos-mlb-tipo';
                tipoEl.textContent = tipo;
                td.appendChild(tipoEl);
            }
            return td;
        }

        function parsePrecoHistoricoFavorito(valor) {
            const preco = typeof parsePrecoAnuncioFavoritos === 'function'
                ? parsePrecoAnuncioFavoritos(valor)
                : Number(valor);
            return preco !== null && preco !== undefined && Number.isFinite(Number(preco)) ? Number(preco) : null;
        }

        function calcularDescontoHistoricoFavorito(precoNormal, precoComDesconto) {
            const normal = parsePrecoHistoricoFavorito(precoNormal);
            const desconto = parsePrecoHistoricoFavorito(precoComDesconto);
            if (normal === null || desconto === null || normal <= 0 || desconto >= normal) return '';
            const percentual = ((normal - desconto) / normal) * 100;
            if (!Number.isFinite(percentual) || percentual <= 0) return '';
            return Number(percentual.toFixed(2));
        }

        function aplicarPrecoResultadoNossoHistoricoFavorito(anuncio, vinculo) {
            const saida = anuncio && typeof anuncio === 'object' ? { ...anuncio } : {};
            const simulacao = vinculo && vinculo.simulacao || {};
            const sucesso = vinculoHistoricoFavoritoTeveSucesso(vinculo);
            if (!sucesso || !simulacao || typeof simulacao !== 'object') return saida;

            const precoNormal = parsePrecoHistoricoFavorito(simulacao.preco_aplicado ?? simulacao.preco_previsto);
            const precoPromocional = simulacao.fallback_sem_promocao
                ? null
                : parsePrecoHistoricoFavorito(simulacao.preco_promocional_aplicado ?? simulacao.preco_promocional_previsto);

            if (precoNormal !== null) {
                saida.preco = precoNormal;
                saida.standard_price = precoNormal;
                saida.base_price = precoNormal;
            }

            if (precoNormal !== null && precoPromocional !== null && precoPromocional < precoNormal) {
                saida.price = precoPromocional;
                saida.preco_original = precoNormal;
                saida.original_price = precoNormal;
                saida.preco_promocional = precoPromocional;
                saida.promotional_price = precoPromocional;
                saida.discount_pct = saida.discount_pct || calcularDescontoHistoricoFavorito(precoNormal, precoPromocional);
                saida.desconto_percentual = saida.desconto_percentual || saida.discount_pct || '';
            } else if (precoNormal !== null) {
                saida.price = precoNormal;
                saida.preco_original = '';
                saida.original_price = '';
                saida.preco_promocional = '';
                saida.promotional_price = '';
                saida.discount_pct = '';
                saida.desconto_percentual = '';
            }
            return saida;
        }

        function criarCelulaPrecoCompletoHistoricoFavorito(anuncio) {
            if (typeof criarCelulaPrecoHistoricoFavoritos === 'function') {
                return criarCelulaPrecoHistoricoFavoritos(anuncio);
            }
            if (typeof criarCelulaPrecoAnuncioFavoritos === 'function') {
                return criarCelulaPrecoAnuncioFavoritos(anuncio);
            }
            const td = document.createElement('td');
            td.textContent = formatarPrecoResumoHistoricoFavorito(anuncio && (anuncio.price ?? anuncio.preco));
            return td;
        }

        function criarIndicadorTravaMargemHistoricoFavorito(vinculo) {
            if (!vinculoHistoricoFavoritoTeveSucesso(vinculo)) return null;
            const travado = simulacaoHistoricoFavoritoTravouMargem(vinculo);
            if (!travado) return null;
            const span = document.createElement('span');
            span.className = 'ml-links-alinhados-margin-lock is-locked';
            span.textContent = 'Travado 15%';
            span.title = 'O preco foi limitado pela margem minima de 15%.';
            return span;
        }

        function criarLinhaNossoAnuncioHistoricoFavorito(vinculo) {
            const anuncio = aplicarPrecoResultadoNossoHistoricoFavorito(vinculo.nosso || {}, vinculo);
            const tr = document.createElement('tr');
            tr.appendChild(criarCelulaOrdemHistoricoFavorito(vinculo.ordem));
            tr.appendChild(criarCelulaFotoAnuncioFavoritos(anuncio));
            tr.appendChild(criarCelulaMlbHistoricoFavorito(anuncio, vinculo.loja, vinculo && vinculo.simulacao && (vinculo.simulacao.tipo_anuncio_atual || vinculo.simulacao.tipoAnuncioAtual)));
            tr.appendChild(criarCelulaTextoFavoritos(obterTituloAnuncioLinksAlinhadosFavoritos(anuncio), { long: true }));
            const tdPreco = criarCelulaPrecoCompletoHistoricoFavorito(anuncio);
            const indicadorMargem = criarIndicadorTravaMargemHistoricoFavorito(vinculo);
            if (indicadorMargem) tdPreco.appendChild(indicadorMargem);
            tr.appendChild(tdPreco);
            tr.appendChild(criarCelulaStatusLinksAlinhadosFavoritos(vinculo));
            return tr;
        }

        function criarLinhaBaseAnuncioHistoricoFavorito(vinculo) {
            const anuncio = vinculo.base || {};
            const tr = document.createElement('tr');
            tr.appendChild(criarCelulaOrdemHistoricoFavorito(vinculo.ordem));
            tr.appendChild(criarCelulaFotoAnuncioFavoritos(anuncio));
            tr.appendChild(criarCelulaMlbHistoricoFavorito(anuncio, '', vinculo && vinculo.simulacao && (vinculo.simulacao.tipo_anuncio_alvo || vinculo.simulacao.tipoAnuncioAlvo)));
            tr.appendChild(criarCelulaMediaHistoricoFavoritos(anuncio));
            tr.appendChild(criarCelulaPrecoCompletoHistoricoFavorito(anuncio));
            tr.appendChild(criarCelulaTextoFavoritos(obterTituloAnuncioLinksAlinhadosFavoritos(anuncio), { long: true }));
            return tr;
        }

        function adicionarLinhaRelatorioHistoricoFavorito(container, rotulo, linha) {
            const detalhe = textoHistoricoFavoritosSeguro(linha && linha.detalhe || linha && linha.resumo || '', 1200);
            if (!detalhe) return;
            const row = document.createElement('div');
            row.className = 'ml-links-alinhados-report-row';
            const label = document.createElement('span');
            label.textContent = rotulo;
            const texto = document.createElement('p');
            texto.textContent = detalhe;
            row.appendChild(label);
            row.appendChild(texto);
            container.appendChild(row);
        }

        function textoSimulacaoHistoricoFavorito(simulacao) {
            if (!simulacao || typeof simulacao !== 'object') return '';
            const partes = [];
            const addPreco = (rotulo, valor) => {
                const numero = parsePrecoAnuncioFavoritos(valor);
                if (numero !== null) partes.push(`${rotulo}: ${formatarPrecoFavoritosMl(numero)}`);
            };
            addPreco('Preco previsto', simulacao.preco_previsto);
            addPreco('Promocional previsto', simulacao.preco_promocional_previsto);
            addPreco('Preco aplicado', simulacao.preco_aplicado);
            addPreco('Promocional aplicado', simulacao.preco_promocional_aplicado);
            if (simulacao.margem_prevista !== null && simulacao.margem_prevista !== undefined && simulacao.margem_prevista !== '') {
                partes.push(`Margem prevista: ${formatarMargemAnuncioFavoritos(simulacao.margem_prevista)}`);
            }
            if (simulacao.tipo_anuncio_atual || simulacao.tipo_anuncio_alvo) {
                partes.push(`Tipo: ${simulacao.tipo_anuncio_atual || '-'} -> ${simulacao.tipo_anuncio_alvo || '-'}`);
            }
            if (simulacao.campanha_nome || simulacao.campanha_id) {
                partes.push(`Campanha: ${simulacao.campanha_nome || simulacao.campanha_id}`);
            }
            if (simulacao.fallback_sem_promocao) partes.push('Fallback sem promocao aplicado');
            return partes.join(' | ');
        }

        function formatarPrecoResumoHistoricoFavorito(valor) {
            const numero = typeof parsePrecoAnuncioFavoritos === 'function'
                ? parsePrecoAnuncioFavoritos(valor)
                : Number(valor);
            if (numero === null || numero === undefined || !Number.isFinite(Number(numero))) return '-';
            return typeof formatarPrecoFavoritosMl === 'function' ? formatarPrecoFavoritosMl(numero) : String(numero);
        }

        function formatarMargemResumoHistoricoFavorito(valor) {
            if (valor === null || valor === undefined || valor === '') return '-';
            return typeof formatarMargemAnuncioFavoritos === 'function'
                ? formatarMargemAnuncioFavoritos(valor)
                : `${valor}%`;
        }

        function textoRelatorioCurtoHistoricoFavorito(linha, limite = 180) {
            const texto = textoHistoricoFavoritosSeguro(linha && (linha.resumo || linha.detalhe) || '', limite * 2);
            if (!texto) return '';
            const partes = texto
                .split('|')
                .map(parte => parte.trim())
                .filter(Boolean)
                .slice(0, 2);
            return textoHistoricoFavoritosSeguro(partes.join(' | ') || texto, limite);
        }

        function textoResultadoCurtoHistoricoFavorito(vinculo) {
            const status = textoHistoricoFavoritosSeguro(vinculo && vinculo.status_texto || '', 140);
            if (status && status !== '-') return status;
            return textoRelatorioCurtoHistoricoFavorito(vinculo && vinculo.relatorio_final, 140) || '-';
        }

        function obterPrecoVigenteHistoricoFavorito(anuncio) {
            if (!anuncio) return null;
            if (typeof obterPrecoVigenteAnuncioFavoritos === 'function') {
                const preco = obterPrecoVigenteAnuncioFavoritos(anuncio);
                if (preco !== null && preco !== undefined && Number.isFinite(Number(preco))) return Number(preco);
            }
            const campos = [
                anuncio.preco_promocional,
                anuncio.promotional_price,
                anuncio.price,
                anuncio.preco,
                anuncio.valor,
                anuncio.sale_price,
                anuncio.preco_original,
                anuncio.original_price
            ];
            for (const campo of campos) {
                const preco = typeof parsePrecoAnuncioFavoritos === 'function'
                    ? parsePrecoAnuncioFavoritos(campo)
                    : Number(campo);
                if (preco !== null && preco !== undefined && Number.isFinite(Number(preco))) return Number(preco);
            }
            return null;
        }

        function obterPrecoNossoHistoricoFavorito(vinculo) {
            const simulacao = vinculo && vinculo.simulacao || {};
            const candidatos = simulacao.fallback_sem_promocao
                ? [simulacao.preco_aplicado, simulacao.preco_previsto]
                : [
                    simulacao.preco_promocional_aplicado,
                    simulacao.preco_promocional_previsto,
                    simulacao.preco_aplicado,
                    simulacao.preco_previsto
                ];
            for (const candidato of candidatos) {
                const preco = typeof parsePrecoAnuncioFavoritos === 'function'
                    ? parsePrecoAnuncioFavoritos(candidato)
                    : Number(candidato);
                if (preco !== null && preco !== undefined && Number.isFinite(Number(preco))) return Number(preco);
            }
            return obterPrecoVigenteHistoricoFavorito(vinculo && vinculo.nosso);
        }

        function vinculoHistoricoFavoritoTeveSucesso(vinculo) {
            const status = String(vinculo && vinculo.status || '').toLowerCase();
            const finalTipo = String(vinculo && vinculo.relatorio_final && vinculo.relatorio_final.tipo || '').toLowerCase();
            if (status === 'error' || status === 'falha' || finalTipo === 'error') return false;
            return status === 'success' || status === 'sucesso' || /feito|alterad/i.test(String(vinculo && vinculo.status_texto || ''));
        }

        function simulacaoHistoricoFavoritoTemPromocao(simulacao) {
            if (!simulacao || simulacao.fallback_sem_promocao) return false;
            const promocional = typeof parsePrecoAnuncioFavoritos === 'function'
                ? parsePrecoAnuncioFavoritos(simulacao.preco_promocional_aplicado ?? simulacao.preco_promocional_previsto)
                : Number(simulacao.preco_promocional_aplicado ?? simulacao.preco_promocional_previsto);
            return promocional !== null && promocional !== undefined && Number.isFinite(Number(promocional)) && Number(promocional) > 0;
        }

        function simulacaoHistoricoFavoritoTravouMargem(vinculo) {
            const simulacao = vinculo && vinculo.simulacao || {};
            if (simulacao.limite_margem_aplicado || simulacao.limiteMargemAplicado) return true;
            const texto = [
                vinculo && vinculo.relatorio_inicial && vinculo.relatorio_inicial.detalhe,
                vinculo && vinculo.relatorio_final && vinculo.relatorio_final.detalhe
            ].filter(Boolean).join(' ');
            return /limite\s+de\s+margem|margem\s+15|15%/i.test(texto);
        }

        function obterCustoIdealHistoricoFavorito(simulacao) {
            if (!simulacao || typeof simulacao !== 'object') return '';
            return formatarPrecoResumoHistoricoFavorito(
                simulacao.custo_ideal_abaixo_base
                ?? simulacao.custoIdealAbaixoBase
                ?? null
            );
        }

        function textoMotivoFalhaHistoricoFavorito(vinculo) {
            const detalhe = textoRelatorioCurtoHistoricoFavorito(vinculo && vinculo.relatorio_final, 220);
            if (!detalhe) return '';
            return detalhe.replace(/^Loja:\s*[^|]+\|\s*/i, '').trim();
        }

        function textoCategoriaAlteradaHistoricoFavorito(simulacao) {
            if (!simulacao || typeof simulacao !== 'object') return '';
            const atual = textoHistoricoFavoritosSeguro(
                simulacao.tipo_anuncio_atual || simulacao.tipoAnuncioAtual || '',
                60
            );
            const alvo = textoHistoricoFavoritosSeguro(
                simulacao.tipo_anuncio_alvo || simulacao.tipoAnuncioAlvo || '',
                60
            );
            if (!atual || !alvo) return '';
            const norm = (valor) => String(valor || '').trim().toLowerCase();
            if (norm(atual) === norm(alvo)) return '';
            return `Categoria do anuncio alterada de ${atual} para ${alvo}.`;
        }

        function textoResumoFinalVinculoHistoricoFavorito(vinculo) {
            const simulacao = vinculo && vinculo.simulacao || {};
            const sucesso = vinculoHistoricoFavoritoTeveSucesso(vinculo);
            if (!sucesso) {
                const motivo = textoMotivoFalhaHistoricoFavorito(vinculo);
                return `Favorito nao feito.${motivo ? ` Motivo: ${motivo}.` : ''}`;
            }

            const nosso = obterPrecoNossoHistoricoFavorito(vinculo);
            const base = obterPrecoVigenteHistoricoFavorito(vinculo && vinculo.base);
            const concorrendo = nosso !== null && base !== null ? nosso <= base + 0.0001 : null;
            const fallback = !!simulacao.fallback_sem_promocao;
            const temPromocao = simulacaoHistoricoFavoritoTemPromocao(simulacao);
            const travouMargem = simulacaoHistoricoFavoritoTravouMargem(vinculo);
            const custoIdeal = obterCustoIdealHistoricoFavorito(simulacao);
            const categoriaTexto = textoCategoriaAlteradaHistoricoFavorito(simulacao);
            const finalizar = (texto) => categoriaTexto ? `${texto} ${categoriaTexto}` : texto;

            if (concorrendo === true) {
                if (fallback) {
                    return finalizar('Feito favorito e concorrendo. Feito sem promocao pois o Mercado Livre nao aceitou a campanha.');
                }
                if (temPromocao) {
                    return finalizar('Feito favorito, produto colocado em promocao e concorrendo.');
                }
                return finalizar('Feito favorito e concorrendo.');
            }

            if (concorrendo === false) {
                const partes = ['Feito favorito. Nao estamos concorrendo.'];
                if (custoIdeal && custoIdeal !== '-') {
                    partes.push(`Para concorrer o produto precisa ser comprado por ${custoIdeal}.`);
                }
                if (travouMargem && temPromocao) {
                    partes.push('Produto travado na margem de 15%, promocao aplicada.');
                } else if (travouMargem) {
                    partes.push('Produto travado na margem de 15%.');
                } else if (temPromocao) {
                    partes.push('Promocao aplicada.');
                }
                if (fallback) {
                    partes.push('Feito sem promocao pois o Mercado Livre nao aceitou a campanha.');
                }
                return finalizar(partes.join(' '));
            }

            if (fallback) {
                return finalizar('Feito favorito. Feito sem promocao pois o Mercado Livre nao aceitou a campanha.');
            }
            if (temPromocao) {
                return finalizar('Feito favorito, produto colocado em promocao.');
            }
            return finalizar('Feito favorito.');
        }

        function criarItemResumoRelatorioHistoricoFavorito(rotulo, valor, opcoes = {}) {
            const item = document.createElement('div');
            item.className = `ml-links-alinhados-report-summary-item${opcoes.wide ? ' is-wide' : ''}${opcoes.highlight ? ' is-highlight' : ''}`;
            const label = document.createElement('span');
            label.textContent = rotulo;
            const conteudo = document.createElement('strong');
            conteudo.textContent = valor || '-';
            item.appendChild(label);
            item.appendChild(conteudo);
            return item;
        }

        function adicionarDetalheRelatorioHistoricoFavorito(container, rotulo, linhaOuTexto) {
            const detalhe = typeof linhaOuTexto === 'string'
                ? textoHistoricoFavoritosSeguro(linhaOuTexto, 1200)
                : textoHistoricoFavoritosSeguro(linhaOuTexto && (linhaOuTexto.detalhe || linhaOuTexto.resumo) || '', 1200);
            if (!detalhe) return false;
            const row = document.createElement('div');
            row.className = 'ml-links-alinhados-report-row';
            const label = document.createElement('span');
            label.textContent = rotulo;
            const texto = document.createElement('p');
            texto.textContent = detalhe;
            row.appendChild(label);
            row.appendChild(texto);
            container.appendChild(row);
            return true;
        }

        function criarBlocoRelatorioVinculoHistoricoFavorito(vinculo) {
            const article = document.createElement('article');
            article.className = 'ml-links-alinhados-report-card';
            const head = document.createElement('div');
            head.className = 'ml-links-alinhados-report-head';
            const titulo = document.createElement('strong');
            const nossoId = obterMlbAnuncioLinksAlinhadosFavoritos(vinculo.nosso);
            const baseId = obterMlbAnuncioLinksAlinhadosFavoritos(vinculo.base);
            titulo.textContent = `${Number(vinculo.ordem) || 0}\u00ba | ${nossoId || '-'} relacionado com ${baseId || '-'}`;
            head.appendChild(titulo);
            head.appendChild(criarBadgeStatusLinksAlinhadosFavoritos(vinculo));
            article.appendChild(head);
            const resumo = document.createElement('p');
            resumo.className = 'ml-links-alinhados-report-message';
            resumo.textContent = textoResumoFinalVinculoHistoricoFavorito(vinculo);
            article.appendChild(resumo);

            const detalhes = document.createElement('details');
            detalhes.className = 'ml-links-alinhados-report-details';
            const summary = document.createElement('summary');
            summary.textContent = 'Ver detalhes';
            detalhes.appendChild(summary);
            let temDetalhes = false;
            temDetalhes = adicionarDetalheRelatorioHistoricoFavorito(detalhes, 'Antes', vinculo.relatorio_inicial) || temDetalhes;
            temDetalhes = adicionarDetalheRelatorioHistoricoFavorito(detalhes, 'Depois', vinculo.relatorio_final) || temDetalhes;
            const simulacaoDetalhe = textoSimulacaoHistoricoFavorito(vinculo.simulacao);
            if (simulacaoDetalhe) {
                temDetalhes = adicionarDetalheRelatorioHistoricoFavorito(detalhes, 'Valores', simulacaoDetalhe) || temDetalhes;
            }
            if (temDetalhes) article.appendChild(detalhes);
            return article;
        }

        function sincronizarAlturasLinhasHistoricoFavorito(tbodyA, tbodyB) {
            if (!tbodyA || !tbodyB || !tbodyA.isConnected || !tbodyB.isConnected) return false;
            const linhasA = Array.from(tbodyA.querySelectorAll('tr'));
            const linhasB = Array.from(tbodyB.querySelectorAll('tr'));
            const total = Math.max(linhasA.length, linhasB.length);
            if (!total) return true;
            [...linhasA, ...linhasB].forEach(linha => {
                linha.style.height = '';
                Array.from(linha.children || []).forEach(td => { td.style.height = ''; });
            });
            for (let idx = 0; idx < total; idx += 1) {
                const linhaA = linhasA[idx];
                const linhaB = linhasB[idx];
                const altura = Math.ceil(Math.max(
                    76,
                    linhaA ? linhaA.getBoundingClientRect().height : 0,
                    linhaB ? linhaB.getBoundingClientRect().height : 0
                ));
                [linhaA, linhaB].filter(Boolean).forEach(linha => {
                    linha.style.height = `${altura}px`;
                    Array.from(linha.children || []).forEach(td => { td.style.height = `${altura}px`; });
                });
            }
            return true;
        }

        function agendarSincronizarAlturasLinhasHistoricoFavorito(tbodyA, tbodyB) {
            const rodar = () => {
                if (!sincronizarAlturasLinhasHistoricoFavorito(tbodyA, tbodyB)) {
                    setTimeout(() => sincronizarAlturasLinhasHistoricoFavorito(tbodyA, tbodyB), 60);
                }
            };
            if (typeof requestAnimationFrame === 'function') requestAnimationFrame(rodar);
            else setTimeout(rodar, 0);
            setTimeout(rodar, 160);
            setTimeout(rodar, 500);
            [...(tbodyA?.querySelectorAll('img') || []), ...(tbodyB?.querySelectorAll('img') || [])]
                .filter(img => img && !img.complete)
                .forEach(img => {
                    img.addEventListener('load', rodar, { once: true });
                    img.addEventListener('error', rodar, { once: true });
                });
        }

        function criarBlocoHistoricoFavoritoEstatico(item) {
            const bloco = document.createElement('article');
            bloco.className = 'ml-links-alinhados-execucao';
            const head = document.createElement('div');
            head.className = 'ml-links-alinhados-execucao-head';
            const tituloWrap = document.createElement('div');
            const titulo = document.createElement('h3');
            titulo.textContent = `${item.sku || 'SKU'}${item.titulo ? ` - ${item.titulo}` : ''}`;
            const meta = document.createElement('div');
            meta.className = 'ml-links-alinhados-execucao-meta';
            const partesMeta = [
                formatarDataHistoricoFavoritos(item.data_iso),
                item.loja ? `Loja: ${item.loja}` : '',
                item.usuario ? `Usuario: ${item.usuario}` : '',
                `${item.vinculos.length} anuncio(s) relacionado(s)`
            ].filter(Boolean);
            meta.textContent = partesMeta.join(' | ');
            tituloWrap.appendChild(titulo);
            tituloWrap.appendChild(meta);
            head.appendChild(tituloWrap);
            if (typeof window.favoritosCriarBotaoColarHistoricoPlanilha === 'function') {
                const botaoColarPlanilha = window.favoritosCriarBotaoColarHistoricoPlanilha(item);
                if (botaoColarPlanilha) head.appendChild(botaoColarPlanilha);
            }
            bloco.appendChild(head);

            if (item.mensagem_final) {
                const resumo = document.createElement('div');
                resumo.className = 'ml-links-alinhados-execucao-resumo';
                resumo.textContent = item.mensagem_final;
                bloco.appendChild(resumo);
            }

            const grid = document.createElement('div');
            grid.className = 'ml-links-alinhados-grid';
            const nossos = criarTabelaHistoricoFavoritoEstatico('Anuncios do Mercado Livre', ['Ordem', 'Foto', 'MLB', 'Titulo', 'Preco', 'Resultado'], 'nossos');
            const bases = criarTabelaHistoricoFavoritoEstatico('Anuncios ranqueados relacionados', ['Ordem', 'Foto', 'MLB', 'Media mensal', 'Preco', 'Titulo'], 'base');
            item.vinculos.forEach(vinculo => {
                nossos.tbody.appendChild(criarLinhaNossoAnuncioHistoricoFavorito(vinculo));
                bases.tbody.appendChild(criarLinhaBaseAnuncioHistoricoFavorito(vinculo));
            });
            grid.appendChild(nossos.painel);
            grid.appendChild(bases.painel);
            bloco.appendChild(grid);
            agendarSincronizarAlturasLinhasHistoricoFavorito(nossos.tbody, bases.tbody);

            const relatorios = document.createElement('section');
            relatorios.className = 'ml-links-alinhados-relatorios';
            const relTitle = document.createElement('h4');
            relTitle.textContent = 'Relatorio individual das alteracoes';
            relatorios.appendChild(relTitle);
            item.vinculos.forEach(vinculo => relatorios.appendChild(criarBlocoRelatorioVinculoHistoricoFavorito(vinculo)));
            bloco.appendChild(relatorios);
            return bloco;
        }

        function agruparHistoricosFavoritosPorSku(historicos) {
            const grupos = [];
            const mapa = new Map();
            (Array.isArray(historicos) ? historicos : []).forEach(item => {
                const skuTexto = String(item && item.sku || '').trim();
                const chave = skuChaveSku(skuTexto) || `sem_sku_${grupos.length + 1}`;
                let grupo = mapa.get(chave);
                if (!grupo) {
                    grupo = {
                        chave,
                        sku: skuTexto || 'Sem SKU',
                        titulo: String(item && item.titulo || '').trim(),
                        itens: [],
                        totalVinculos: 0
                    };
                    mapa.set(chave, grupo);
                    grupos.push(grupo);
                }
                if (!grupo.titulo && item && item.titulo) grupo.titulo = String(item.titulo || '').trim();
                grupo.itens.push(item);
                grupo.totalVinculos += Array.isArray(item && item.vinculos) ? item.vinculos.length : 0;
            });
            return grupos;
        }

        function criarGrupoSkuHistoricoFavoritoEstatico(grupo) {
            const section = document.createElement('section');
            section.className = 'ml-links-alinhados-sku-group';
            const head = document.createElement('div');
            head.className = 'ml-links-alinhados-sku-head';
            const titleWrap = document.createElement('div');
            const title = document.createElement('h3');
            title.textContent = `${grupo.sku}${grupo.titulo ? ` - ${grupo.titulo}` : ''}`;
            const meta = document.createElement('div');
            meta.className = 'ml-links-alinhados-sku-meta';
            meta.textContent = `${grupo.itens.length} historico(s) | ${grupo.totalVinculos} anuncio(s) relacionado(s)`;
            titleWrap.appendChild(title);
            titleWrap.appendChild(meta);
            head.appendChild(titleWrap);
            section.appendChild(head);
            const list = document.createElement('div');
            list.className = 'ml-links-alinhados-sku-list';
            grupo.itens.forEach(item => list.appendChild(criarBlocoHistoricoFavoritoEstatico(item)));
            section.appendChild(list);
            return section;
        }

        function renderizarLinksAlinhadosFavoritos() {
            if (!mlLinksAlinhadosBodyEl || !mlLinksAlinhadosEmptyEl) return;
            const historicos = montarHistoricosFavoritosAlteracoesEstaticas();
            mlLinksAlinhadosBodyEl.innerHTML = '';
            mlLinksAlinhadosEmptyEl.classList.toggle('hidden', historicos.length > 0);
            if (mlLinksAlinhadosStatusEl) {
                const totalVinculos = historicos.reduce((acc, item) => acc + item.vinculos.length, 0);
                const lojaAtual = nomeLojaAtualHistoricoFavoritosEstatico();
                const sufixoLoja = lojaAtual ? ` para ${lojaAtual}` : '';
                mlLinksAlinhadosStatusEl.textContent = historicos.length
                    ? `${historicos.length} execucao(oes) estatica(s)${sufixoLoja} | ${totalVinculos} anuncio(s) relacionado(s).`
                    : `Nenhuma alteracao de favoritos salva${sufixoLoja} com anuncios relacionados.`;
            }
            if (historicos.length > 1) {
                agruparHistoricosFavoritosPorSku(historicos).forEach(grupo => {
                    mlLinksAlinhadosBodyEl.appendChild(criarGrupoSkuHistoricoFavoritoEstatico(grupo));
                });
                return;
            }
            historicos.forEach(item => {
                mlLinksAlinhadosBodyEl.appendChild(criarBlocoHistoricoFavoritoEstatico(item));
            });
        }

        function abrirFavoritoRecenteNaAbaFavoritos(item) {
            const sku = String(item && item.sku || '').trim();
            if (!sku) return;
            const chave = skuChaveSku(sku);
            favMlHistoricoExecucaoSelecionadaId = String(item && item.entrada_id || '').trim();
            const grupoItem = item && item.grupo ? marcarGrupoRankingHistoricoEstaticoFavoritos({
                ...item.grupo,
                anuncios: Array.isArray(item.grupo.anuncios)
                    ? item.grupo.anuncios.map(anuncio => marcarAnuncioRankingHistoricoEstaticoFavoritos({ ...anuncio }))
                    : [],
                data_iso: item.data_iso || item.grupo.data_iso || '',
                loja: item.loja || item.grupo.loja || ''
            }) : null;
            if (chave && grupoItem) {
                mlFavoritosResultadosPorSku.set(chave, {
                    ...grupoItem
                });
            }
            if (grupoRankingFavoritosEhAvulso(grupoItem || item)) {
                favMlSkuSelecionado = sku;
                favMlLojaSelecionada = item.loja || grupoItem?.loja || favoritosLojaSelecionadaParaApi() || '';
                favMlAnunciosSkuAtual = [];
                mudarAba('favoritos');
                renderizarFavoritosSkuSidebar();
                renderizarFavoritosAnunciosMl([], sku);
                renderizarFavoritosOutrosAnuncios(sku);
                return;
            }
            favMlSkuSelecionado = sku;
            favMlLojaSelecionada = item.loja || item.grupo?.loja || favMlLojaSelecionada || favoritosLojaSelecionadaParaApi() || '';
            mudarAba('favoritos');
            renderizarFavoritosSkuSidebar();
            renderizarSkuSidebarMercadoLivre();
            renderizarFavoritosOutrosAnuncios(sku);
            carregarFavoritosAnunciosSku(sku, favMlLojaSelecionada, {
                manterRankingSelecionado: true,
                compartilharSku: true
            });
        }

        function abrirRankingHistoricoParaAprovarFavoritos(grupo, entrada) {
            const sku = String(grupo && grupo.sku || '').trim();
            if (!sku) return;
            abrirFavoritoRecenteNaAbaFavoritos({
                sku,
                grupo,
                entrada_id: idEntradaHistoricoFavoritos(entrada),
                data_iso: entrada && entrada.data_iso || grupo.data_iso || grupo.data_ranking_iso || '',
                loja: grupo.loja || entrada && entrada.loja || ''
            });
        }

        function abrirFavoritoRecenteNoHistorico(item) {
            const sku = String(item && item.sku || '').trim();
            if (!sku) return;
            histMlSkuSelecionado = sku;
            histMlHistoricoExecucaoSelecionadaId = String(item && item.entrada_id || '').trim();
            renderizarHistoricoSkuSidebar();
            renderizarHistoricoFavoritos();
            if (mlHistoricoFavoritosListEl && typeof mlHistoricoFavoritosListEl.scrollIntoView === 'function') {
                mlHistoricoFavoritosListEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
        }

        function refazerRankingHistoricoFavoritos(grupo, entrada = null) {
            const termos = normalizarTermosPesquisaFavoritos(grupo && grupo.termos);
            if (!termos.length) {
                mostrarBalaoFavoritosStatus('Este histórico não tem termos salvos para refazer a consulta.', {
                    erro: true,
                    tempoMs: 3500
                });
                return;
            }
            const grupoRefazer = {
                ...grupo,
                loja: grupo && grupo.loja || entrada && entrada.loja || favoritosLojaSelecionadaParaApi() || '',
                avulso: grupoRankingFavoritosEhAvulso(grupo)
            };
            preencherCamposPesquisaAvulsaMl(termos);
            mudarAba('navegador');
            rankearAvulsoMercadoLivre({
                termos,
                sku: grupoRefazer.sku || 'AVULSO',
                titulo: grupoRefazer.titulo || '',
                loja: grupoRefazer.loja,
                avulso: grupoRefazer.avulso,
                tituloProcesso: grupoRefazer.avulso
                    ? 'Refazendo ranqueamento avulso'
                    : `Refazendo ranking ${grupoRefazer.sku}`
            });
        }

        function obterGrupoRankingFavoritosSku(sku) {
            const chave = skuChaveSku(sku);
            if (!chave) return null;
            const entradaSelecionada = String(favMlHistoricoExecucaoSelecionadaId || '').trim();
            if (entradaSelecionada && entradaSelecionada !== FAV_ML_RANKING_ATUAL_ID) {
                const historico = obterHistoricoFavoritosSelecionadoSku(sku, entradaSelecionada);
                if (historico) {
                    return {
                        grupo: historico.grupo,
                        data_iso: historico.entrada && historico.entrada.data_iso || historico.grupo.data_iso || '',
                        loja: historico.entrada && historico.entrada.loja || historico.grupo.loja || ''
                    };
                }
                return null;
            }
            const grupoAtual = mlFavoritosResultadosPorSku.get(chave);
            if (entradaSelecionada === FAV_ML_RANKING_ATUAL_ID && grupoAtual) {
                return {
                    grupo: grupoAtual,
                    data_iso: grupoAtual.data_iso || grupoAtual.data_ranking_iso || '',
                    loja: grupoAtual.loja || favoritosLojaSelecionadaParaApi() || ''
                };
            }
            return null;
        }

        function chavesRemocaoAnuncioRankingFavoritos(anuncio) {
            if (!anuncio) return [];
            const chaves = new Set(chavesAnuncioFavoritos(anuncio));
            const id = extrairItemIdAnuncio(anuncio.id || anuncio.mlb || anuncio.url || anuncio.permalink || anuncio.link)
                || String(anuncio.id || anuncio.mlb || '').trim().toUpperCase().replace(/-/g, '');
            if (id) chaves.add(`id:${id}`);
            [anuncio.url, anuncio.permalink, anuncio.link].forEach(url => {
                const texto = String(url || '').split('#')[0].trim().toLowerCase();
                if (texto) chaves.add(`url:${texto}`);
            });
            const titulo = String(anuncio.titulo || anuncio.title || '').replace(/\s+/g, ' ').trim().toLowerCase();
            if (titulo) {
                const vendedor = normalizarNomeVendedor(anuncio.vendedor || '').toLowerCase();
                const precos = obterPrecosAnuncioFavoritos(anuncio);
                const preco = String(precos.promocional ?? precos.preco ?? anuncio.price ?? anuncio.preco ?? '').trim();
                chaves.add(`produto:${titulo}|${vendedor}|${preco}`);
            }
            return Array.from(chaves).filter(Boolean);
        }

        function anuncioCorrespondeRemocaoRankingFavoritos(anuncio, chavesAlvo) {
            if (!chavesAlvo || !chavesAlvo.size) return false;
            return chavesRemocaoAnuncioRankingFavoritos(anuncio).some(chave => chavesAlvo.has(chave));
        }

        function removerAnuncioDeGrupoRankingFavoritos(grupo, chavesAlvo) {
            if (!grupo || !Array.isArray(grupo.anuncios) || !chavesAlvo || !chavesAlvo.size) return 0;
            const antes = grupo.anuncios.length;
            grupo.anuncios = grupo.anuncios.filter(anuncio => !anuncioCorrespondeRemocaoRankingFavoritos(anuncio, chavesAlvo));
            grupo.total_anuncios = grupo.anuncios.length;
            return antes - grupo.anuncios.length;
        }

        function moverAnuncioEmGrupoRankingFavoritos(grupo, chavesAlvo, direcao) {
            if (!grupo || !Array.isArray(grupo.anuncios) || !chavesAlvo || !chavesAlvo.size) return false;
            const passo = direcao < 0 ? -1 : 1;
            const origem = grupo.anuncios.findIndex(anuncio => anuncioCorrespondeRemocaoRankingFavoritos(anuncio, chavesAlvo));
            if (origem < 0) return false;
            let destino = origem + passo;
            while (destino >= 0 && destino < grupo.anuncios.length && vendedorIgnoradoNoRanking(grupo.anuncios[destino] && grupo.anuncios[destino].vendedor)) {
                destino += passo;
            }
            if (destino < 0 || destino >= grupo.anuncios.length) return false;
            const temporario = grupo.anuncios[origem];
            grupo.anuncios[origem] = grupo.anuncios[destino];
            grupo.anuncios[destino] = temporario;
            grupo.ordem_manual = true;
            grupo.total_anuncios = grupo.anuncios.length;
            return true;
        }

        function recalcularTotaisHistoricoFavoritos(entrada) {
            if (!entrada || !Array.isArray(entrada.grupos)) return;
            entrada.total_skus = entrada.grupos.filter(grupo => grupo && grupo.sku).length;
            entrada.total_anuncios = entrada.grupos.reduce((acc, grupo) => (
                acc + (Array.isArray(grupo && grupo.anuncios) ? grupo.anuncios.length : 0)
            ), 0);
        }

        function encontrarGrupoHistoricoRankingFavoritos(historico, chaveSku, entradaId = '') {
            const entradaAlvo = String(entradaId || '').trim();
            for (const entrada of filtrarHistoricoFavoritosPorLojaAtual(historico, chaveSku)) {
                if (entradaAlvo && idEntradaHistoricoFavoritos(entrada) !== entradaAlvo) continue;
                const grupoHistorico = (entrada.grupos || []).find(item => skuChaveSku(item && item.sku) === chaveSku);
                if (grupoHistorico) return { entrada, grupo: grupoHistorico };
            }
            return null;
        }

        function removerAnuncioRankingFavoritos(sku, anuncio, opcoes = {}) {
            const skuSelecionado = String(sku || favMlSkuSelecionado || '').trim();
            if (!skuSelecionado || !anuncio) return;

            const chavesAlvo = new Set(chavesRemocaoAnuncioRankingFavoritos(anuncio));
            if (!chavesAlvo.size) return;

            const chaveSku = skuChaveSku(skuSelecionado);
            const salvoIgnorado = adicionarAnuncioIgnoradoSku(skuSelecionado, anuncio);
            let removido = false;
            const entradaId = String(opcoes.entradaId || '').trim();
            const grupoAtual = entradaId ? null : mlFavoritosResultadosPorSku.get(chaveSku);
            if (grupoAtual) {
                removido = removerAnuncioDeGrupoRankingFavoritos(grupoAtual, chavesAlvo) > 0 || removido;
                mlFavoritosResultadosPorSku.set(chaveSku, grupoAtual);
            }

            const historico = lerHistoricoFavoritos();
            let historicoAlterado = false;
            const alvoHistorico = encontrarGrupoHistoricoRankingFavoritos(historico, chaveSku, entradaId);
            if (alvoHistorico) {
                const removidosHistorico = removerAnuncioDeGrupoRankingFavoritos(alvoHistorico.grupo, chavesAlvo);
                if (removidosHistorico > 0) {
                    historicoAlterado = true;
                    removido = true;
                    recalcularTotaisHistoricoFavoritos(alvoHistorico.entrada);
                }
            }
            if (historicoAlterado) salvarHistoricoFavoritos(historico);

            renderizarFavoritosOutrosAnuncios(skuSelecionado);
            renderizarAnunciosIgnoradosSku();
            if (skuChaveSku(skuSelecionado) === skuChaveSku(favMlSkuSelecionado) && Array.isArray(favMlAnunciosSkuAtual)) {
                renderizarFavoritosAnunciosMl(favMlAnunciosSkuAtual, favMlSkuSelecionado);
            }
            renderizarHistoricoFavoritos();
            if (favMlStatusEl) {
                favMlStatusEl.textContent = removido || salvoIgnorado
                    ? `Anuncio ignorado para o SKU ${skuSelecionado}. Ele nao entra mais no ranking desse SKU.`
                    : `Nao localizei esse anuncio salvo no ranking do SKU ${skuSelecionado}.`;
            }
            if (mlHistoricoFavoritosStatusEl && document.getElementById('aba-historico')?.classList.contains('active') && removido) {
                mlHistoricoFavoritosStatusEl.textContent = `Anuncio ignorado para o SKU ${skuSelecionado}.`;
            }
        }

        function moverAnuncioRankingFavoritos(sku, anuncio, direcao, opcoes = {}) {
            const skuSelecionado = String(sku || favMlSkuSelecionado || '').trim();
            if (!skuSelecionado || !anuncio) return;
            const chavesAlvo = new Set(chavesRemocaoAnuncioRankingFavoritos(anuncio));
            if (!chavesAlvo.size) return;

            const chaveSku = skuChaveSku(skuSelecionado);
            let mudou = false;
            const entradaId = String(opcoes.entradaId || '').trim();
            const grupoAtual = entradaId ? null : mlFavoritosResultadosPorSku.get(chaveSku);
            if (grupoAtual) {
                mudou = moverAnuncioEmGrupoRankingFavoritos(grupoAtual, chavesAlvo, direcao) || mudou;
                mlFavoritosResultadosPorSku.set(chaveSku, grupoAtual);
            }

            const historico = lerHistoricoFavoritos();
            let historicoAlterado = false;
            const alvoHistorico = encontrarGrupoHistoricoRankingFavoritos(historico, chaveSku, entradaId);
            if (alvoHistorico) {
                const moveuHistorico = moverAnuncioEmGrupoRankingFavoritos(alvoHistorico.grupo, chavesAlvo, direcao);
                if (moveuHistorico) {
                    historicoAlterado = true;
                    mudou = true;
                    recalcularTotaisHistoricoFavoritos(alvoHistorico.entrada);
                    if (!grupoAtual) {
                        mlFavoritosResultadosPorSku.set(chaveSku, alvoHistorico.grupo);
                    }
                }
            }
            if (historicoAlterado) salvarHistoricoFavoritos(historico, { imediato: true });

            renderizarFavoritosOutrosAnuncios(skuSelecionado);
            if (skuChaveSku(skuSelecionado) === skuChaveSku(favMlSkuSelecionado) && Array.isArray(favMlAnunciosSkuAtual)) {
                renderizarFavoritosAnunciosMl(favMlAnunciosSkuAtual, favMlSkuSelecionado);
            }
            renderizarHistoricoFavoritos();
            if (favMlStatusEl && mudou) {
                favMlStatusEl.textContent = `Ranking do SKU ${skuSelecionado} reordenado.`;
            }
            if (mlHistoricoFavoritosStatusEl && document.getElementById('aba-historico')?.classList.contains('active') && mudou) {
                mlHistoricoFavoritosStatusEl.textContent = `Ranking do SKU ${skuSelecionado} reordenado.`;
            }
        }
