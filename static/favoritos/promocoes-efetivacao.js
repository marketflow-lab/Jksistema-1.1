        function clonarOpcoesPromocaoFavoritos(opcoes) {
            if (!opcoes || typeof opcoes !== 'object') return null;
            try {
                return JSON.parse(JSON.stringify(opcoes));
            } catch (err) {
                return {
                    ...opcoes,
                    campanha: opcoes.campanha && typeof opcoes.campanha === 'object' ? { ...opcoes.campanha } : opcoes.campanha,
                    desconto: opcoes.desconto && typeof opcoes.desconto === 'object' ? { ...opcoes.desconto } : opcoes.desconto
                };
            }
        }
        function extrairOpcoesPromocaoGrupoFavoritos(grupo) {
            if (!grupo || typeof grupo !== 'object') return null;
            const candidatos = [
                grupo.opcoes_promocao,
                grupo.opcoesPromocao,
                grupo.opcoes_promocao_favoritos,
                grupo.promocao_favoritos,
                grupo.promocaoFavoritos,
                grupo.promotion_options,
                grupo.promotionOptions
            ];
            for (const candidato of candidatos) {
                if (candidato && typeof candidato === 'object') return candidato;
            }
            if (
                Object.prototype.hasOwnProperty.call(grupo, 'usar_promocao')
                || grupo.campanha
                || grupo.desconto
            ) {
                return grupo;
            }
            return null;
        }
        function salvarOpcoesPromocaoFavoritosSku(sku, opcoes) {
            const chave = skuChaveSku(sku);
            if (!chave || !opcoes || typeof opcoes !== 'object') return;
            mlFavoritosOpcoesPromocaoPorSku.set(chave, clonarOpcoesPromocaoFavoritos(opcoes));
        }
        function obterOpcoesPromocaoSalvasFavoritosSku(sku) {
            const chave = skuChaveSku(sku);
            if (!chave) return null;
            const opcoes = mlFavoritosOpcoesPromocaoPorSku.get(chave);
            return clonarOpcoesPromocaoFavoritos(opcoes);
        }
        function resolverOpcoesPromocaoGrupoFavoritos(grupo, sku) {
            const opcoesGrupo = extrairOpcoesPromocaoGrupoFavoritos(grupo);
            if (opcoesGrupo) return clonarOpcoesPromocaoFavoritos(opcoesGrupo);
            return obterOpcoesPromocaoSalvasFavoritosSku(sku);
        }
        function obterOpcoesPromocaoFavoritosSku(sku) {
            const resultadoRanking = obterGrupoRankingFavoritosSku(sku);
            const grupo = resultadoRanking && resultadoRanking.grupo;
            const opcoesGrupo = resolverOpcoesPromocaoGrupoFavoritos(grupo, sku);
            if (opcoesGrupo) return opcoesGrupo;
            return obterOpcoesPromocaoSalvasFavoritosSku(sku);
        }
        function obterPercentualPromocaoFixaFavoritos(opcoesPromocao) {
            if (!opcoesPromocao || !opcoesPromocao.usar_promocao) return null;
            const desconto = opcoesPromocao.desconto || {};
            const modo = String(desconto.modo || opcoesPromocao.modo || '').trim();
            if (modo && modo !== 'percentual_fixo') return null;
            const numero = Number(String(
                desconto.percentual ??
                opcoesPromocao.percentual ??
                opcoesPromocao.desconto_percentual ??
                opcoesPromocao.discount_percentage ??
                ''
            ).replace(',', '.').trim());
            if (!Number.isFinite(numero) || numero <= 0 || numero >= 100) return null;
            return numero;
        }
        function assinaturaOpcoesPromocaoFavoritos(opcoesPromocao) {
            const campanha = opcoesPromocao && opcoesPromocao.campanha || {};
            const campanhaId = String(campanha.id || campanha.campaign_id || '').trim();
            const percentual = obterPercentualPromocaoFixaFavoritos(opcoesPromocao);
            if (!campanhaId || percentual === null) return '';
            return `${campanhaId}|${Number(percentual).toFixed(4)}`;
        }
        function promocaoFavoritosSemPercentualFixo(opcoesPromocao) {
            return !!(
                opcoesPromocao
                && opcoesPromocao.usar_promocao
                && obterPercentualPromocaoFixaFavoritos(opcoesPromocao) === null
            );
        }
        function opcoesPromocaoFavoritosProntas(opcoesPromocao) {
            const campanha = opcoesPromocao && opcoesPromocao.campanha || {};
            const campanhaId = String(campanha.id || campanha.campaign_id || '').trim();
            return !!(
                opcoesPromocao
                && opcoesPromocao.usar_promocao
                && campanhaId
                && obterPercentualPromocaoFixaFavoritos(opcoesPromocao) !== null
            );
        }
        function aplicarOpcoesPromocaoFavoritosSku(sku, opcoesPromocao) {
            const chave = skuChaveSku(sku);
            const opcoes = clonarOpcoesPromocaoFavoritos(opcoesPromocao);
            if (!chave || !opcoesPromocaoFavoritosProntas(opcoes)) return null;
            salvarOpcoesPromocaoFavoritosSku(sku, opcoes);
            mlFavoritosOpcoesPromocaoAtual = clonarOpcoesPromocaoFavoritos(opcoes);
            const rankingAtual = obterGrupoRankingFavoritosSku(sku);
            if (rankingAtual && rankingAtual.grupo && typeof rankingAtual.grupo === 'object') {
                rankingAtual.grupo.opcoes_promocao = clonarOpcoesPromocaoFavoritos(opcoes);
            }
            favoritosSalvarEstadoLojaAtual();
            return clonarOpcoesPromocaoFavoritos(opcoes);
        }
        async function escolherPromocaoFavoritosSkuAtual(opcoes = {}) {
            const sku = String(favMlSkuSelecionado || '').trim();
            if (!sku) {
                mostrarBalaoFavoritosStatus('Selecione um SKU antes de escolher campanha e porcentagem.', {
                    erro: true,
                    tempoMs: 4500
                });
                return null;
            }
            const opcoesPromocao = await perguntarOpcoesPromocaoFavoritos({
                exigirPromocao: true,
                loja: favMlLojaSelecionada || mlSkuLojaSelecionada || skuLojaSelecionada || ''
            });
            const aplicadas = aplicarOpcoesPromocaoFavoritosSku(sku, opcoesPromocao);
            if (!aplicadas) {
                atualizarPainelEfetivarFavoritos();
                return null;
            }
            if (Array.isArray(favMlAnunciosSkuAtual) && favMlAnunciosSkuAtual.length) {
                renderizarFavoritosAnunciosMl(favMlAnunciosSkuAtual, sku);
            } else {
                atualizarPainelEfetivarFavoritos();
            }
            if (opcoes.mostrarStatus !== false) {
                mostrarBalaoFavoritosStatus(`Campanha e porcentagem salvas para ${sku}. Simulacao recalculada para aprovar e alterar.`, {
                    tempoMs: 5500,
                    larga: true
                });
            }
            return aplicadas;
        }
        async function garantirPromocaoFavoritosSkuAtual(sku) {
            let opcoesPromocao = obterOpcoesPromocaoFavoritosSku(sku);
            if (opcoesPromocaoFavoritosProntas(opcoesPromocao)) return opcoesPromocao;
            mostrarBalaoFavoritosStatus('Escolha a campanha e a porcentagem para recalcular antes de enviar ao Mercado Livre.', {
                larga: true
            });
            opcoesPromocao = await escolherPromocaoFavoritosSkuAtual({ mostrarStatus: false });
            if (!opcoesPromocaoFavoritosProntas(opcoesPromocao)) {
                mostrarBalaoFavoritosStatus('Campanha e porcentagem nao foram informadas. A alteracao no Mercado Livre nao foi enviada.', {
                    erro: true,
                    tempoMs: 6500,
                    larga: true
                });
                atualizarPainelEfetivarFavoritos();
                return null;
            }
            return opcoesPromocao;
        }
        async function definirProtecaoAutomacaoMlFavoritos(ativa, motivo = 'favoritos-efetivar') {
            if (!(window.electronAPI && typeof window.electronAPI.setMlAutomationActive === 'function')) {
                return false;
            }
            try {
                const resultado = await window.electronAPI.setMlAutomationActive(!!ativa, motivo);
                return !!(resultado && resultado.success !== false);
            } catch (err) {
                console.warn('Nao foi possivel atualizar a protecao da automacao ML:', err);
                return false;
            }
        }
        function calcularPrecoCheioPromocaoFavoritos(precoFinalAlvo, percentual) {
            const alvo = Number(precoFinalAlvo);
            const pct = Number(percentual);
            const fator = 1 - (pct / 100);
            if (!Number.isFinite(alvo) || alvo <= 0 || !Number.isFinite(fator) || fator <= 0) return null;
            const alvoCentavos = Math.round(alvo * 100);
            const centro = Math.max(1, Math.round((alvo / fator) * 100));
            let melhor = null;
            for (let delta = -300; delta <= 300; delta += 1) {
                const baseCentavos = centro + delta;
                if (baseCentavos <= 0) continue;
                const precoCheio = baseCentavos / 100;
                const finalCentavos = Math.round(precoCheio * fator * 100);
                const diffAbs = Math.abs(finalCentavos - alvoCentavos);
                const prioridade = finalCentavos === alvoCentavos ? 0 : (finalCentavos > alvoCentavos ? 1 : 2);
                const candidato = {
                    precoCheio,
                    precoFinalCalculado: finalCentavos / 100,
                    diferenca: (finalCentavos - alvoCentavos) / 100,
                    diffAbs,
                    prioridade,
                    distanciaBase: Math.abs(baseCentavos - centro)
                };
                if (
                    !melhor
                    || candidato.diffAbs < melhor.diffAbs
                    || (candidato.diffAbs === melhor.diffAbs && candidato.prioridade < melhor.prioridade)
                    || (candidato.diffAbs === melhor.diffAbs && candidato.prioridade === melhor.prioridade && candidato.distanciaBase < melhor.distanciaBase)
                ) {
                    melhor = candidato;
                    if (melhor.diffAbs === 0) break;
                }
            }
            return melhor;
        }
        function calcularSimulacaoPrecoFavoritos(anuncioConta, anuncioRanking, opcoesPromocao = null, opcoes = {}) {
            if (promocaoFavoritosSemPercentualFixo(opcoesPromocao)) {
                return { ok: false, status: 'Promocao selecionada sem % fixa. Refaca o favorito informando a porcentagem da campanha.' };
            }
            const ajusteTipoAnuncio = resolverTrocaTipoAnuncioFavoritos(anuncioConta, anuncioRanking, opcoes);
            const precoRanking = obterPrecoVigenteAnuncioFavoritos(anuncioRanking);
            if (precoRanking === null || precoRanking <= 0.01) {
                return { ok: false, status: 'Sem preco do ranking nesta linha.' };
            }

            const custo = parsePrecoAnuncioFavoritos(anuncioConta && (anuncioConta.custo ?? anuncioConta.custo_unitario ?? ''));
            const frete = parsePrecoAnuncioFavoritos(anuncioConta && (
                anuncioConta.frete_ml ??
                anuncioConta.shipping_cost ??
                anuncioConta.shipping_seller_cost ??
                anuncioConta.shipping_list_cost ??
                anuncioConta.shipping_base_cost ??
                anuncioConta.frete ??
                anuncioConta.custo_frete ??
                ''
            ));
            let impostoRate = parseTaxaSimuladorFavoritos(anuncioConta && (
                anuncioConta.imposto_percentual ??
                anuncioConta.imposto_rate ??
                anuncioConta.aliquota_imposto ??
                anuncioConta.imposto ??
                ''
            ));

            const faltando = [];
            if (custo === null) faltando.push('custo');
            if (impostoRate === null) faltando.push('imposto');
            if (frete === null && (anuncioConta && anuncioConta.free_shipping === true)) faltando.push('frete');
            if (faltando.length) {
                return { ok: false, status: `Faltando ${faltando.join(', ')}` };
            }
            if (impostoRate === null) impostoRate = 0;

            const precoAtualConta = obterPrecoVigenteAnuncioFavoritos(anuncioConta);
            const tarifaAtual = parsePrecoAnuncioFavoritos(anuncioConta && (
                anuncioConta.tarifa_ml ??
                anuncioConta.ad_cost ??
                anuncioConta.fee_per_sale ??
                anuncioConta.sale_fee_amount ??
                ''
            ));
            const taxaFixa = parsePrecoAnuncioFavoritos(anuncioConta && (anuncioConta.fixed_fee_amount ?? '')) || 0;
            let taxaVariavel = null;
            if (tarifaAtual !== null && precoAtualConta !== null && precoAtualConta > 0) {
                taxaVariavel = Math.max(0, tarifaAtual - taxaFixa) / precoAtualConta;
            }
            if (taxaVariavel === null || !Number.isFinite(taxaVariavel)) {
                taxaVariavel = parseTaxaSimuladorFavoritos(anuncioConta && (
                    anuncioConta.taxa_ml_percentual ??
                    anuncioConta.sale_fee_pct ??
                    anuncioConta.meli_fee_pct ??
                    ''
                ));
            }
            if (taxaVariavel === null || !Number.isFinite(taxaVariavel)) taxaVariavel = 0;
            if (ajusteTipoAnuncio.usarTaxaPadraoAlvo && ajusteTipoAnuncio.taxaPadraoAlvo !== null) {
                taxaVariavel = ajusteTipoAnuncio.taxaPadraoAlvo;
            }

            const freteCalc = frete !== null ? frete : 0;
            const margemMinima = 0.15;
            const descontoSorteado = Math.round((Math.floor(Math.random() * 150) + 1)) / 100;
            let precoSimulado = Math.max(0.01, Math.round((precoRanking - descontoSorteado) * 100) / 100);
            const custoFixo = custo + freteCalc + taxaFixa;
            const denominadorPrecoMinimo = 1 - impostoRate - taxaVariavel - margemMinima;
            let precoMinimoMargem = null;
            let limiteMargemAplicado = false;
            const precoAlvoCustoIdeal = Math.max(0.01, (Math.round(precoRanking * 100) - 1) / 100);
            const calcularCustoMaximoParaPreco = (precoAlvo) => {
                const preco = Number(precoAlvo);
                if (!Number.isFinite(preco) || preco <= 0 || denominadorPrecoMinimo <= 0) return null;
                const custoMaximo = (preco * denominadorPrecoMinimo) - freteCalc - taxaFixa;
                return Math.floor(custoMaximo * 100) / 100;
            };

            if (denominadorPrecoMinimo <= 0) {
                return { ok: false, status: 'Nao ha preco que mantenha margem minima de 15% com estes custos.' };
            }

            precoMinimoMargem = Math.ceil((custoFixo / denominadorPrecoMinimo) * 100) / 100;
            if (Number.isFinite(precoMinimoMargem) && precoSimulado < precoMinimoMargem) {
                precoSimulado = Math.max(0.01, precoMinimoMargem);
                limiteMargemAplicado = true;
            }

            const calcularComPreco = (preco) => {
                const tarifa = Math.max(0, taxaFixa + (preco * taxaVariavel));
                const imposto = preco * impostoRate;
                const liquido = preco - custo - freteCalc - imposto - tarifa;
                const margemCalc = (liquido * 100) / preco;
                return { tarifa, imposto, liquido, margemCalc };
            };

            let calculado = calcularComPreco(precoSimulado);
            for (let ajuste = 0; calculado.margemCalc < 15 && ajuste < 200; ajuste += 1) {
                precoSimulado = Math.round((precoSimulado + 0.01) * 100) / 100;
                limiteMargemAplicado = true;
                calculado = calcularComPreco(precoSimulado);
            }

            let tarifaSimulada = calculado.tarifa;
            let impostoValor = calculado.imposto;
            let valorLiquido = calculado.liquido;
            let margem = calculado.margemCalc;
            if (!Number.isFinite(margem)) {
                return { ok: false, preco: precoSimulado, status: 'Nao foi possivel simular a margem.' };
            }
            if (margem < 15) {
                return { ok: false, preco: precoSimulado, status: 'Simulacao bloqueada: margem ficaria abaixo de 15%.' };
            }
            const percentualPromocao = obterPercentualPromocaoFixaFavoritos(opcoesPromocao);
            let precoAnuncioSugerido = precoSimulado;
            let precoPromocionalCalculado = null;
            let diferencaPromocao = null;
            const recalcularPromocao = () => {
                precoAnuncioSugerido = precoSimulado;
                precoPromocionalCalculado = null;
                diferencaPromocao = null;
                if (percentualPromocao === null) return;
                const precoCheioInfo = calcularPrecoCheioPromocaoFavoritos(precoSimulado, percentualPromocao);
                if (precoCheioInfo) {
                    precoAnuncioSugerido = precoCheioInfo.precoCheio;
                    precoPromocionalCalculado = precoCheioInfo.precoFinalCalculado;
                    diferencaPromocao = precoCheioInfo.diferenca;
                }
            };
            const recalcularMargem = () => {
                calculado = calcularComPreco(precoSimulado);
                tarifaSimulada = calculado.tarifa;
                impostoValor = calculado.imposto;
                valorLiquido = calculado.liquido;
                margem = calculado.margemCalc;
            };
            recalcularPromocao();

            const precosFinaisReservados = opcoes && opcoes.precosFinaisReservados instanceof Set ? opcoes.precosFinaisReservados : null;
            const precosCheiosReservados = opcoes && opcoes.precosCheiosReservados instanceof Set ? opcoes.precosCheiosReservados : null;
            let ajustePrecoUnico = 0;
            const precoFinalAtual = () => percentualPromocao !== null
                ? (precoPromocionalCalculado ?? precoSimulado)
                : precoSimulado;
            const chaveRanking = chavePrecoCentavosFavoritos(precoRanking);
            const temConflitoPreco = () => {
                const chaveFinal = chavePrecoCentavosFavoritos(precoFinalAtual());
                const chaveCheio = chavePrecoCentavosFavoritos(precoAnuncioSugerido);
                return !!(
                    (chaveRanking && chaveFinal === chaveRanking)
                    || (chaveRanking && chaveCheio === chaveRanking)
                    || (chaveFinal && precosFinaisReservados && precosFinaisReservados.has(chaveFinal))
                    || (chaveCheio && precosCheiosReservados && precosCheiosReservados.has(chaveCheio))
                );
            };
            for (let tentativa = 0; temConflitoPreco() && tentativa < 500; tentativa += 1) {
                precoSimulado = Math.round((precoSimulado + 0.01) * 100) / 100;
                ajustePrecoUnico = Math.round((ajustePrecoUnico + 0.01) * 100) / 100;
                recalcularMargem();
                recalcularPromocao();
            }
            if (temConflitoPreco()) {
                return { ok: false, preco: precoAnuncioSugerido, status: 'Simulacao bloqueada: nao foi possivel gerar preco unico para este anuncio.' };
            }
            if (precosFinaisReservados) {
                const chaveFinal = chavePrecoCentavosFavoritos(precoFinalAtual());
                if (chaveFinal) precosFinaisReservados.add(chaveFinal);
            }
            if (precosCheiosReservados) {
                const chaveCheio = chavePrecoCentavosFavoritos(precoAnuncioSugerido);
                if (chaveCheio) precosCheiosReservados.add(chaveCheio);
            }
            const descontoEfetivo = Math.round((precoRanking - precoSimulado) * 100) / 100;
            const custoIdealAbaixoBase = calcularCustoMaximoParaPreco(precoAlvoCustoIdeal);
            const reducaoCustoIdeal = custoIdealAbaixoBase !== null
                ? Math.max(0, Math.ceil((custo - custoIdealAbaixoBase) * 100) / 100)
                : null;
            return {
                ok: true,
                preco: precoAnuncioSugerido,
                precoCompetitivo: precoSimulado,
                precoPromocional: percentualPromocao !== null ? precoSimulado : null,
                precoPromocionalCalculado,
                percentualPromocao,
                assinaturaPromocao: assinaturaOpcoesPromocaoFavoritos(opcoesPromocao),
                tipoAnuncioAtual: ajusteTipoAnuncio.tipoAtual,
                tipoAnuncioAlvo: ajusteTipoAnuncio.tipoAlvo,
                listingTypeIdAtual: ajusteTipoAnuncio.listingTypeIdAtual,
                listingTypeIdAlvo: ajusteTipoAnuncio.listingTypeIdAlvo,
                trocarTipoAnuncio: ajusteTipoAnuncio.trocar,
                taxaTipoAnuncioAplicada: ajusteTipoAnuncio.usarTaxaPadraoAlvo ? ajusteTipoAnuncio.taxaPadraoAlvo : null,
                diferencaPromocao,
                margem,
                valorLiquido,
                tarifa: tarifaSimulada,
                impostoValor,
                frete: freteCalc,
                custo,
                precoRanking,
                descontoSorteado,
                descontoEfetivo,
                ajustePrecoUnico,
                limiteMargemAplicado,
                precoMinimoMargem,
                precoAlvoCustoIdeal,
                custoIdealAbaixoBase,
                reducaoCustoIdeal
            };
        }
        function obterLojaEfetivarFavoritos(anuncioConta) {
            return favoritosLojaSelecionadaParaApi(
                (anuncioConta && (anuncioConta.loja || anuncioConta.loja_sync || anuncioConta.loja_conta))
                || favMlLojaSelecionada
                || mlSkuLojaSelecionada
                || skuLojaSelecionada
                || ''
            );
        }
        async function efetivarFavoritoMercadoLivre(anuncioConta, anuncioRanking, sim, opcoesPromocao, botao, config = {}) {
            const itemId = String(anuncioConta && (anuncioConta.mlb || anuncioConta.id || anuncioConta.item_id || '') || '').trim();
            const campanha = opcoesPromocao && opcoesPromocao.campanha || {};
            const campanhaId = String(campanha.id || campanha.campaign_id || '').trim();
            const loja = obterLojaEfetivarFavoritos(anuncioConta);
            const precoPromocional = sim && (sim.precoPromocionalCalculado ?? sim.precoPromocional ?? sim.precoCompetitivo);
            if (!itemId || !loja || !campanhaId || !sim || !sim.ok || !precoPromocional) {
                mostrarBalaoFavoritosStatus('Nao foi possivel efetivar: confira loja, MLB, promocao e simulacao.', {
                    erro: true,
                    tempoMs: 6000
                });
                return;
            }

            if (config.confirmar !== false) {
                const pctTxt = sim.percentualPromocao !== null && sim.percentualPromocao !== undefined
                    ? `${Number(sim.percentualPromocao).toFixed(2).replace('.', ',')}%`
                    : '';
                const pergunta = [
                    `Efetivar favorito do ${itemId}?`,
                    `Preco do anuncio: ${formatarPrecoFavoritosMl(sim.preco)}`,
                    `Preco final na promocao: ${formatarPrecoFavoritosMl(precoPromocional)}`,
                    pctTxt ? `Promocao: ${campanha.nome || campanhaId} (${pctTxt})` : `Promocao: ${campanha.nome || campanhaId}`
                ].join('\n');
                if (!window.confirm(pergunta)) return null;
            }

            const textoOriginal = botao ? botao.textContent : '';
            if (botao) {
                botao.disabled = true;
                botao.textContent = '...';
            }
            if (config.mostrarStatus !== false) {
                mostrarBalaoFavoritosStatus(`Efetivando favorito ${itemId}: saindo da promocao atual, ajustando tipo/preco e aplicando a campanha selecionada...`, {
                    larga: true
                });
            }
            try {
                const body = {
                    loja,
                    sku: favMlSkuSelecionado || (anuncioConta && anuncioConta.sku) || '',
                    item_id: itemId,
                    preco_anuncio: sim.preco,
                    preco_promocional: precoPromocional,
                    preco_competitivo: sim.precoCompetitivo,
                    percentual_promocao: sim.percentualPromocao,
                    campanha_id: campanhaId,
                    campanha_nome: campanha.nome || campanha.name || '',
                    promotion_type: campanha.tipo || campanha.type || campanha.promotion_type || 'SELLER_CAMPAIGN',
                    listing_type_id_alvo: sim.listingTypeIdAlvo || '',
                    tipo_anuncio_alvo: sim.tipoAnuncioAlvo || '',
                    tipo_anuncio_atual: sim.tipoAnuncioAtual || '',
                    simulacao: sim,
                    anuncio: anuncioConta || {}
                };
                const response = await fetch('/api/favoritos/ml/efetivar-promocao', {
                    method: 'POST',
                    headers: headersJsonAutenticado(),
                    body: JSON.stringify(body)
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok || !data.success) {
                    throw new Error(normalizarErroEfetivacaoFavoritos(data.detail || data.message || data || `HTTP ${response.status}`));
                }
                const fallbackSemPromocao = !!(data && data.fallback_sem_promocao_aplicado);
                if (config.mostrarStatus !== false) {
                    if (fallbackSemPromocao) {
                        const margemFallback = data.margem_estimada_contingencia !== null && data.margem_estimada_contingencia !== undefined
                            ? ` Margem estimada: ${formatarMargemAnuncioFavoritos(data.margem_estimada_contingencia)}.`
                            : '';
                        mostrarBalaoFavoritosStatus(
                            `Campanha removida/recusada no ML para ${itemId}. Aplicado fallback sem campanha com preco ${formatarPrecoFavoritosMl(data.preco_anuncio)}.${margemFallback}`,
                            { tempoMs: 8000, larga: true }
                        );
                    } else {
                        mostrarBalaoFavoritosStatus(`Tudo certo: favorito feito no Mercado Livre para ${itemId}.`, {
                            tempoMs: 7000
                        });
                    }
                }
                if (favMlStatusEl && config.atualizarStatus !== false) {
                    if (fallbackSemPromocao) {
                        favMlStatusEl.textContent = `Favorito ajustado sem campanha: ${itemId} ficou em ${formatarPrecoFavoritosMl(data.preco_anuncio)} (fallback).`;
                    } else {
                        favMlStatusEl.textContent = `Favorito feito: ${itemId} ficou em ${formatarPrecoFavoritosMl(data.preco_anuncio)} com promocao ${campanha.nome || campanhaId}.`;
                    }
                }
                if (config.recarregar !== false) {
                    await carregarFavoritosAnunciosSku(favMlSkuSelecionado, loja);
                }
                return data;
            } catch (err) {
                if (config.mostrarStatus !== false) {
                    mostrarBalaoFavoritosStatus(`Erro ao efetivar favorito: ${err && err.message ? err.message : err}`, {
                        erro: true,
                        larga: true
                    });
                }
                if (config.propagarErro) throw err;
                return null;
            } finally {
                if (botao) {
                    botao.disabled = false;
                    botao.textContent = textoOriginal || 'Efetivar';
                }
            }
        }
        function textoCurtoStatusSimuladorFavoritos(status) {
            const texto = String(status || '').trim();
            if (!texto) return 'Sem simulacao';
            if (/faltando/i.test(texto)) return texto;
            if (/margem minima|15%|abaixo de 15/i.test(texto)) return 'Limite de margem 15%';
            if (/sem preco/i.test(texto)) return 'Sem preco do ranking';
            if (/sem % fixa/i.test(texto)) return 'Promocao sem %';
            return texto.length > 42 ? `${texto.slice(0, 39)}...` : texto;
        }
        function normalizarErroEfetivacaoFavoritos(valor, limite = 1100) {
            if (valor === null || valor === undefined) return '';
            if (typeof valor === 'string') {
                return valor.length > limite ? `${valor.slice(0, limite - 3)}...` : valor;
            }
            if (valor instanceof Error) {
                return normalizarErroEfetivacaoFavoritos(valor.message || String(valor), limite);
            }
            if (typeof valor === 'object') {
                const detalhe = valor.detail ?? valor.message ?? valor.error ?? valor.erro;
                if (detalhe && detalhe !== valor) {
                    return normalizarErroEfetivacaoFavoritos(detalhe, limite);
                }
                try {
                    const texto = JSON.stringify(valor, null, 2);
                    return texto.length > limite ? `${texto.slice(0, limite - 3)}...` : texto;
                } catch (_) {
                    return String(valor);
                }
            }
            return String(valor);
        }
        function limparLogEfetivarFavoritos() {
            if (!favMlEfetivarLogEl) return;
            limparTimerOcultarStatusEfetivarFavoritos();
            favMlEfetivarLogEl.innerHTML = '';
            favMlEfetivarLogEl.classList.add('hidden');
        }
        function limparTimerOcultarStatusEfetivarFavoritos() {
            if (!favMlEfetivarLogHideTimer) return;
            clearTimeout(favMlEfetivarLogHideTimer);
            favMlEfetivarLogHideTimer = null;
        }
        function agendarOcultarStatusEfetivarFavoritos(delayMs = 5000) {
            limparTimerOcultarStatusEfetivarFavoritos();
            favMlEfetivarLogHideTimer = setTimeout(() => {
                favMlEfetivarLogHideTimer = null;
                if (!favMlEfetivarLogEl) return;
                favMlEfetivarLogEl
                    .querySelectorAll('.ml-favoritos-efetivar-log-row:not(.is-comparison)')
                    .forEach(row => row.remove());
                const temConteudo = !!favMlEfetivarLogEl.querySelector('.ml-favoritos-efetivar-log-row');
                favMlEfetivarLogEl.classList.toggle('hidden', !temConteudo);
            }, Math.max(0, Number(delayMs) || 0));
        }
        function adicionarStatusEfetivarFavoritos(tipo, titulo, detalhe = '') {
            if (!favMlEfetivarLogEl) return;
            limparTimerOcultarStatusEfetivarFavoritos();
            favMlEfetivarLogEl.classList.remove('hidden');
            const row = document.createElement('div');
            row.className = `ml-favoritos-efetivar-log-row is-${tipo || 'info'}`;
            const body = document.createElement('div');
            const titleEl = document.createElement('div');
            titleEl.className = 'ml-favoritos-efetivar-log-title';
            titleEl.textContent = titulo || 'Status';
            body.appendChild(titleEl);
            const detalheTxt = normalizarErroEfetivacaoFavoritos(detalhe);
            if (detalheTxt) {
                const detailEl = document.createElement('div');
                detailEl.className = 'ml-favoritos-efetivar-log-detail';
                detailEl.textContent = detalheTxt;
                body.appendChild(detailEl);
            }
            row.appendChild(body);
            favMlEfetivarLogEl.appendChild(row);
            favMlEfetivarLogEl.scrollTop = favMlEfetivarLogEl.scrollHeight;
        }
        function textoPrecoComparacaoFavoritos(valor) {
            const numero = parsePrecoAnuncioFavoritos(valor);
            return numero !== null ? formatarPrecoFavoritosMl(numero) : '-';
        }
        function resumoPrecosComparacaoFavoritos(anuncio, substitutos = {}) {
            const precos = obterPrecosAnuncioFavoritos(anuncio);
            const temPrecoSubstituto = Object.prototype.hasOwnProperty.call(substitutos, 'preco');
            const temPromocionalSubstituto = Object.prototype.hasOwnProperty.call(substitutos, 'promocional');
            const precoSubstituto = parsePrecoAnuncioFavoritos(substitutos.preco);
            const promocionalSubstituto = parsePrecoAnuncioFavoritos(substitutos.promocional);
            const preco = temPrecoSubstituto
                ? precoSubstituto
                : (precos.preco ?? precos.promocional);
            const promocional = temPromocionalSubstituto
                ? promocionalSubstituto
                : precos.promocional;
            return { preco, promocional };
        }
        function deveMostrarCustoIdealComparacaoFavoritos(item) {
            const registro = item && item.registro || {};
            const sim = registro.sim || {};
            if (!sim.ok || !sim.limiteMargemAplicado || sim.custoIdealAbaixoBase === null || sim.custoIdealAbaixoBase === undefined) {
                return false;
            }
            const data = item && item.data || {};
            const precoBase = obterPrecoVigenteAnuncioFavoritos(registro.ranking);
            const precoNosso = parsePrecoAnuncioFavoritos(data.preco_promocional)
                ?? parsePrecoAnuncioFavoritos(data.preco_anuncio)
                ?? obterPrecoFinalSimulacaoFavoritos(sim);
            if (precoBase === null || precoNosso === null) return true;
            return precoNosso >= precoBase - 0.0001 || Number(sim.descontoEfetivo || 0) < 0.01;
        }
        function textoCustoIdealComparacaoFavoritos(sim) {
            if (!sim || sim.custoIdealAbaixoBase === null || sim.custoIdealAbaixoBase === undefined) return '-';
            const ideal = parsePrecoAnuncioFavoritos(sim.custoIdealAbaixoBase);
            const alvo = parsePrecoAnuncioFavoritos(sim.precoAlvoCustoIdeal);
            if (ideal === null || alvo === null) return '-';
            if (ideal < 0) {
                return `Inviavel: custo teria que ser ${formatarPrecoFavoritosMl(ideal)} (alvo ${formatarPrecoFavoritosMl(alvo)})`;
            }
            const reducao = parsePrecoAnuncioFavoritos(sim.reducaoCustoIdeal);
            const partes = [
                formatarPrecoFavoritosMl(ideal),
                `alvo ${formatarPrecoFavoritosMl(alvo)}`
            ];
            if (reducao !== null && reducao > 0.009) {
                partes.push(`reduzir ${formatarPrecoFavoritosMl(reducao)}`);
            }
            return partes.join(' | ');
        }
        function textoPrecoAlvoComparacaoFavoritos(sim) {
            const alvo = parsePrecoAnuncioFavoritos(sim && sim.precoAlvoCustoIdeal);
            return alvo !== null ? formatarPrecoFavoritosMl(alvo) : '-';
        }
        function textoCustoIdealTabelaComparacaoFavoritos(sim) {
            const ideal = parsePrecoAnuncioFavoritos(sim && sim.custoIdealAbaixoBase);
            if (ideal === null) return '-';
            const reducao = parsePrecoAnuncioFavoritos(sim && sim.reducaoCustoIdeal);
            const partes = [
                ideal < 0
                    ? `Inviavel: ${formatarPrecoFavoritosMl(ideal)}`
                    : formatarPrecoFavoritosMl(ideal)
            ];
            if (reducao !== null && reducao > 0.009) {
                partes.push(`reduzir ${formatarPrecoFavoritosMl(reducao)}`);
            }
            return partes.join(' | ');
        }
        function descreverTipoEfetivacaoFavoritos(registro, data) {
            const sim = registro && registro.sim || {};
            const update = data && data.listing_type_update || {};
            const atual = String(
                update.current_name
                || update.current_listing_type_name
                || sim.tipoAnuncioAtual
                || ''
            ).trim();
            const alvo = String(
                update.target_name
                || update.target_listing_type_name
                || sim.tipoAnuncioAlvo
                || ''
            ).trim();
            if (!atual && !alvo) return '';
            if (sim.tipoMantidoPorBloqueioMl) {
                const original = String(sim.tipoAnuncioAlvoOriginal || '').trim();
                return original
                    ? `tipo mantido: ${atual || alvo} (ML bloqueou ${atual || '-'} -> ${original})`
                    : `tipo mantido: ${atual || alvo}`;
            }
            if (atual && alvo && atual !== alvo) {
                const status = update.changed === false ? 'ja estava no tipo alvo' : 'tipo alterado';
                return `${status}: ${atual} -> ${alvo}`;
            }
            return `tipo: ${alvo || atual}`;
        }
        function descreverSucessoEfetivacaoFavoritos(item) {
            const data = item && item.data || {};
            const registro = item && item.registro || {};
            const sim = registro.sim || {};
            const partes = [];
            const loja = String((data && data.loja) || registro.loja || '').trim();
            if (loja) partes.push(`Loja: ${loja}`);
            const tipo = descreverTipoEfetivacaoFavoritos(registro, data);
            if (tipo) partes.push(tipo);
            if (sim.tipoMantidoPorBloqueioMl && sim.tipoBloqueioMlMotivo) {
                partes.push(`motivo tipo: ${sim.tipoBloqueioMlMotivo}`);
            }
            partes.push(`preco cheio aplicado: ${formatarPrecoFavoritosMl(data.preco_anuncio ?? sim.preco)}`);
            const precoPromocional = data.preco_promocional ?? sim.precoPromocionalCalculado ?? sim.precoPromocional ?? sim.precoCompetitivo;
            if (data.fallback_sem_promocao_aplicado) {
                partes.push('campanha nao aplicada');
                const motivo = normalizarErroEfetivacaoFavoritos(data.fallback_motivo || data.message || '');
                if (motivo) partes.push(`motivo: ${motivo}`);
            } else {
                partes.push(`preco final promocional conferido: ${formatarPrecoFavoritosMl(precoPromocional)}`);
                partes.push(`campanha aplicada: ${data.campanha_nome || data.promotion_id || '-'}`);
            }
            const removidas = Array.isArray(data.promocoes_removidas) ? data.promocoes_removidas.length : 0;
            if (removidas) partes.push(`${removidas} promocao(oes) anterior(es) removida(s)`);
            return partes.filter(Boolean).join(' | ');
        }
        function descreverFalhaEfetivacaoFavoritos(item) {
            const registro = item && item.registro || {};
            const sim = registro.sim || {};
            const partes = [];
            const loja = String(registro.loja || '').trim();
            if (loja) partes.push(`Loja: ${loja}`);
            const tipo = descreverTipoEfetivacaoFavoritos(registro, null);
            if (tipo) {
                const tipoPrevisto = tipo
                    .replace(/^tipo alterado:\s*/i, '')
                    .replace(/^ja estava no tipo alvo:\s*/i, '')
                    .replace(/^tipo:\s*/i, '');
                partes.push(`tipo previsto: ${tipoPrevisto}`);
            }
            if (sim && sim.ok) {
                partes.push(`preco cheio previsto: ${formatarPrecoFavoritosMl(sim.preco)}`);
                const precoPromocional = sim.precoPromocionalCalculado ?? sim.precoPromocional ?? sim.precoCompetitivo;
                partes.push(`preco final promocional previsto: ${formatarPrecoFavoritosMl(precoPromocional)}`);
            } else if (sim && sim.status) {
                partes.push(`simulacao invalida: ${textoCurtoStatusSimuladorFavoritos(sim.status)}`);
            }
            const erro = normalizarErroEfetivacaoFavoritos(item && item.erro || '');
            partes.push(`nao feito/concluido: ${erro || 'O Mercado Livre recusou a alteracao sem detalhar o motivo.'}`);
            return partes.filter(Boolean).join(' | ');
        }
        function criarLinhaResultadoBalaoFavoritos(tipo, titulo, detalhe) {
            const row = document.createElement('div');
            row.className = `ml-favoritos-balloon-result-row is-${tipo || 'info'}`;
            const titleEl = document.createElement('div');
            titleEl.className = 'ml-favoritos-balloon-result-title';
            titleEl.textContent = titulo || 'Resultado';
            row.appendChild(titleEl);
            const detalheEl = document.createElement('div');
            detalheEl.className = 'ml-favoritos-balloon-result-detail';
            detalheEl.textContent = detalhe || '-';
            row.appendChild(detalheEl);
            return row;
        }
        function renderizarResumoResultadoBalaoFavoritos(sucessos, falhas) {
            const feitos = Array.isArray(sucessos) ? sucessos : [];
            const naoFeitos = Array.isArray(falhas) ? falhas : [];
            if (!feitos.length && !naoFeitos.length) return null;
            const wrap = document.createElement('div');
            wrap.className = 'ml-favoritos-balloon-result-list';
            feitos.forEach(item => {
                const itemId = item && (item.itemId || (item.data && item.data.item_id)) || '-';
                const data = item && item.data || {};
                const fallback = !!data.fallback_sem_promocao_aplicado;
                wrap.appendChild(criarLinhaResultadoBalaoFavoritos(
                    'success',
                    `${itemId} - alteracao feita${fallback ? ' com fallback' : ''}`,
                    descreverSucessoEfetivacaoFavoritos(item)
                ));
            });
            naoFeitos.forEach(item => {
                const itemId = item && item.itemId || '-';
                wrap.appendChild(criarLinhaResultadoBalaoFavoritos(
                    'error',
                    `${itemId} - alteracao nao feita`,
                    descreverFalhaEfetivacaoFavoritos(item)
                ));
            });
            return wrap;
        }
        function criarBlocoColarPlanilhaResultadoFavoritos(historicoAlteracoes, compacto = false) {
            if (!historicoAlteracoes || !Array.isArray(historicoAlteracoes.vinculos) || !historicoAlteracoes.vinculos.length) {
                return null;
            }
            if (typeof window.favoritosCriarBotaoColarHistoricoPlanilha !== 'function') {
                return null;
            }
            const wrap = document.createElement('div');
            wrap.className = compacto
                ? 'ml-favoritos-resultado-planilha is-compact'
                : 'ml-favoritos-resultado-planilha';
            const statusEl = document.createElement('div');
            statusEl.className = 'ml-favoritos-resultado-planilha-status';
            const botao = window.favoritosCriarBotaoColarHistoricoPlanilha(historicoAlteracoes, { statusEl });
            botao.classList.add('ml-favoritos-resultado-planilha-btn');
            wrap.appendChild(botao);
            wrap.appendChild(statusEl);
            return wrap;
        }
        function renderizarComparativoEfetivacaoFavoritos(sucessos, mensagemStatus = '', falhas = [], historicoAlteracoes = null) {
            const lista = (Array.isArray(sucessos) ? sucessos : [])
                .filter(item => item && item.registro && item.data);
            const listaFalhas = (Array.isArray(falhas) ? falhas : [])
                .filter(item => item && (item.itemId || item.erro || item.registro));
            if (!lista.length && !listaFalhas.length) return;

            const itensComparacao = [
                ...lista.map(item => ({
                    tipo: 'sucesso',
                    item,
                    registro: item.registro || {},
                    data: item.data || {},
                    erro: ''
                })),
                ...listaFalhas
                    .filter(item => item && item.registro)
                    .map(item => ({
                        tipo: 'falha',
                        item,
                        registro: item.registro || {},
                        data: null,
                        erro: normalizarErroEfetivacaoFavoritos(item.erro || '', 220)
                    }))
            ];
            const totalProcessados = lista.length + listaFalhas.length;
            const mostrarCustoIdeal = itensComparacao.some(item => deveMostrarCustoIdealComparacaoFavoritos(item.item));
            const mostrarStatusLinha = listaFalhas.length > 0;
            const colunas = [
                'Nosso MLB',
                'Nosso preco',
                'Nosso preco com desconto atual',
                'Base MLB',
                'Base preco',
                'Base preco com desconto atual'
            ];
            if (mostrarCustoIdeal) {
                colunas.push('Preco alvo abaixo da base');
                colunas.push('Custo ideal para vender abaixo da base');
            }
            if (mostrarStatusLinha) {
                colunas.push('Status');
            }

            const criarTabela = (wrapClass, tableClass) => {
                const wrap = document.createElement('div');
                wrap.className = wrapClass;
                const table = document.createElement('table');
                table.className = tableClass;
                const thead = document.createElement('thead');
                const trHead = document.createElement('tr');
                colunas.forEach(texto => {
                    const th = document.createElement('th');
                    th.textContent = texto;
                    trHead.appendChild(th);
                });
                thead.appendChild(trHead);
                table.appendChild(thead);

                const tbody = document.createElement('tbody');
                itensComparacao.forEach(item => {
                    const registro = item.registro || {};
                    const data = item.data || {};
                    const sim = registro.sim || {};
                    const substitutosNosso = item.tipo === 'sucesso'
                        ? {
                            preco: data.preco_anuncio,
                            promocional: data.preco_promocional
                        }
                        : {
                            preco: sim.preco,
                            promocional: sim.precoPromocionalCalculado ?? sim.precoPromocional ?? sim.precoCompetitivo
                        };
                    const nosso = resumoPrecosComparacaoFavoritos(registro.anuncio, {
                        preco: substitutosNosso.preco,
                        promocional: substitutosNosso.promocional
                    });
                    const base = resumoPrecosComparacaoFavoritos(registro.ranking);
                    const mostrarIdealLinha = deveMostrarCustoIdealComparacaoFavoritos(item.item);
                    const valores = [
                        { valor: registro.itemId || obterIdAnuncioFavoritos(registro.anuncio) || '-' },
                        { valor: textoPrecoComparacaoFavoritos(nosso.preco), money: true },
                        { valor: textoPrecoComparacaoFavoritos(nosso.promocional), money: true },
                        { valor: obterIdAnuncioFavoritos(registro.ranking) || '-' },
                        { valor: textoPrecoComparacaoFavoritos(base.preco), money: true },
                        { valor: textoPrecoComparacaoFavoritos(base.promocional), money: true }
                    ];
                    if (mostrarCustoIdeal) {
                        valores.push({
                            valor: mostrarIdealLinha ? textoPrecoAlvoComparacaoFavoritos(registro.sim) : '-',
                            money: mostrarIdealLinha
                        });
                        valores.push({
                            valor: mostrarIdealLinha ? textoCustoIdealTabelaComparacaoFavoritos(registro.sim) : '-'
                        });
                    }
                    if (mostrarStatusLinha) {
                        valores.push({
                            valor: item.tipo === 'sucesso'
                                ? 'Alterado'
                                : `Nao feito${item.erro ? `: ${item.erro}` : ''}`
                        });
                    }
                    const tr = document.createElement('tr');
                    if (item.tipo === 'falha') tr.className = 'is-error';
                    valores.forEach(itemValor => {
                        const td = document.createElement('td');
                        td.textContent = itemValor.valor;
                        if (itemValor.money) td.className = 'is-money';
                        tr.appendChild(td);
                    });
                    tbody.appendChild(tr);
                });
                table.appendChild(tbody);
                wrap.appendChild(table);
                return wrap;
            };

            if (itensComparacao.length && favMlEfetivarLogEl) {
                favMlEfetivarLogEl.classList.remove('hidden');
                const row = document.createElement('div');
                row.className = 'ml-favoritos-efetivar-log-row is-comparison';
                const titleEl = document.createElement('div');
                titleEl.className = 'ml-favoritos-efetivar-log-title';
                titleEl.textContent = `Comparacao dos anuncios processados: ${totalProcessados} anuncio(s) (${lista.length} alterado(s), ${listaFalhas.length} nao feito(s)).`;
                row.appendChild(titleEl);
                row.appendChild(criarTabela('ml-favoritos-comparacao-table-wrap', 'ml-favoritos-comparacao-table'));
                const blocoPlanilha = criarBlocoColarPlanilhaResultadoFavoritos(historicoAlteracoes);
                if (blocoPlanilha) row.appendChild(blocoPlanilha);
                favMlEfetivarLogEl.appendChild(row);
                favMlEfetivarLogEl.scrollTop = favMlEfetivarLogEl.scrollHeight;
            }

            if (mlFavoritosStatusEl) {
                mlFavoritosStatusEl.textContent = `Resultado das alteracoes: ${totalProcessados} processado(s), ${lista.length} feita(s), ${listaFalhas.length} nao feita(s).`;
            }

            if (!mlFavoritosBalloonEl || !mlFavoritosBalloonTextEl) return;
            if (mlFavoritosBalloonTimer) {
                clearTimeout(mlFavoritosBalloonTimer);
                mlFavoritosBalloonTimer = null;
            }
            const titulo = mlFavoritosBalloonEl.querySelector('.ml-favoritos-balloon-title');
            if (titulo) titulo.textContent = 'Resultado das alteracoes';
            mlFavoritosBalloonTextEl.innerHTML = '';
            if (mlFavoritosBalloonActionsEl) mlFavoritosBalloonActionsEl.innerHTML = '';
            if (mensagemStatus) {
                const statusResumo = document.createElement('div');
                statusResumo.className = 'ml-favoritos-balloon-status-summary';
                statusResumo.textContent = mensagemStatus;
                mlFavoritosBalloonTextEl.appendChild(statusResumo);
            }
            const resumoResultado = renderizarResumoResultadoBalaoFavoritos(lista, listaFalhas);
            if (resumoResultado) mlFavoritosBalloonTextEl.appendChild(resumoResultado);
            if (itensComparacao.length) {
                mlFavoritosBalloonTextEl.appendChild(criarTabela('ml-favoritos-balloon-comparison-wrap', 'ml-favoritos-balloon-comparison-table'));
            }
            if (mlFavoritosBalloonActionsEl) {
                const blocoPlanilha = criarBlocoColarPlanilhaResultadoFavoritos(historicoAlteracoes, true);
                if (blocoPlanilha) mlFavoritosBalloonActionsEl.appendChild(blocoPlanilha);
                const fecharBtn = document.createElement('button');
                fecharBtn.type = 'button';
                fecharBtn.textContent = 'Fechar';
                fecharBtn.addEventListener('click', esconderBalaoFavoritosStatus);
                mlFavoritosBalloonActionsEl.appendChild(fecharBtn);
            }
            mlFavoritosBalloonEl.classList.remove('hidden');
            mlFavoritosBalloonEl.classList.toggle('is-error', !!listaFalhas.length);
            mlFavoritosBalloonEl.classList.add('is-wide', 'is-comparison');
            posicionarBalaoFavoritosStatus();
        }

        function clonarAnuncioHistoricoAlteracaoFavoritos(anuncio) {
            if (!anuncio || typeof anuncio !== 'object') return {};
            try {
                if (typeof anuncioHistoricoPayload === 'function') {
                    const payloadPadrao = anuncioHistoricoPayload(anuncio);
                    const custoAnuncio = anuncio.custo ?? anuncio.custo_unitario ?? anuncio.custo_produto ?? anuncio.preco_custo ?? anuncio.valor_custo ?? '';
                    const custoFrete = anuncio.custo_frete ?? anuncio.frete_ml ?? anuncio.shipping_cost ?? anuncio.shipping_seller_cost ?? '';
                    return {
                        ...payloadPadrao,
                        custo: payloadPadrao.custo ?? custoAnuncio,
                        custo_unitario: payloadPadrao.custo_unitario ?? custoAnuncio,
                        custo_produto: payloadPadrao.custo_produto ?? custoAnuncio,
                        preco_custo: payloadPadrao.preco_custo ?? custoAnuncio,
                        valor_custo: payloadPadrao.valor_custo ?? custoAnuncio,
                        custo_frete: payloadPadrao.custo_frete ?? custoFrete
                    };
                }
            } catch (err) {
                console.warn('Nao foi possivel usar payload padrao do historico de favoritos:', err);
            }
            const url = String(anuncio.url || anuncio.permalink || anuncio.link || '').trim();
            const id = String(anuncio.id || anuncio.mlb || anuncio.item_id || extrairItemIdAnuncio(url) || '').trim();
            const precos = typeof obterPrecosAnuncioFavoritos === 'function'
                ? obterPrecosAnuncioFavoritos(anuncio)
                : { preco: anuncio.preco ?? anuncio.price ?? null, promocional: anuncio.preco_promocional ?? anuncio.promotional_price ?? null, desconto: '' };
            const imagem = typeof obterImagemAnuncioFavoritos === 'function'
                ? obterImagemAnuncioFavoritos(anuncio)
                : (anuncio.imagem || anuncio.thumbnail || anuncio.foto || '');
            const tipoAnuncio = typeof obterTipoAnuncioFavoritos === 'function'
                ? obterTipoAnuncioFavoritos(anuncio)
                : (anuncio.tipo_anuncio || anuncio.listing_type_name || '');
            const listingTypeId = typeof obterListingTypeIdAnuncioFavoritos === 'function'
                ? obterListingTypeIdAnuncioFavoritos(anuncio)
                : (anuncio.listing_type_id || anuncio.listingTypeId || '');
            return {
                id,
                mlb: id,
                url,
                permalink: url,
                link: url,
                titulo: String(anuncio.titulo || anuncio.title || '').trim(),
                title: String(anuncio.titulo || anuncio.title || '').trim(),
                vendedor: anuncio.vendedor || anuncio.seller_name || anuncio.sellerNickname || anuncio.nickname || '',
                imagem,
                thumbnail: imagem,
                foto: imagem,
                preco: precos.preco,
                price: precos.promocional !== null && precos.promocional !== undefined ? precos.promocional : precos.preco,
                preco_original: precos.promocional !== null && precos.promocional !== undefined ? precos.preco : '',
                preco_promocional: precos.promocional,
                discount_pct: precos.desconto || '',
                custo: anuncio.custo ?? anuncio.custo_unitario ?? anuncio.custo_produto ?? anuncio.preco_custo ?? anuncio.valor_custo ?? '',
                custo_unitario: anuncio.custo_unitario ?? anuncio.custo ?? anuncio.custo_produto ?? anuncio.preco_custo ?? anuncio.valor_custo ?? '',
                custo_produto: anuncio.custo_produto ?? anuncio.custo ?? anuncio.custo_unitario ?? anuncio.preco_custo ?? anuncio.valor_custo ?? '',
                preco_custo: anuncio.preco_custo ?? anuncio.custo ?? anuncio.custo_unitario ?? anuncio.custo_produto ?? anuncio.valor_custo ?? '',
                valor_custo: anuncio.valor_custo ?? anuncio.custo ?? anuncio.custo_unitario ?? anuncio.custo_produto ?? anuncio.preco_custo ?? '',
                custo_frete: anuncio.custo_frete ?? anuncio.frete_ml ?? anuncio.shipping_cost ?? anuncio.shipping_seller_cost ?? '',
                moeda: anuncio.moeda || anuncio.currency_id || 'BRL',
                currency_id: anuncio.currency_id || anuncio.moeda || 'BRL',
                tipo_anuncio: tipoAnuncio,
                listing_type_id: listingTypeId,
                listing_type_name: tipoAnuncio,
                media_mensal: anuncio.media_mensal ?? anuncio.ritmo_atual ?? '',
                vendas: anuncio.vendas,
                data_criacao: anuncio.data_criacao || ''
            };
        }

        function montarLinhaRelatorioInicialAlteracaoFavoritos(registro) {
            const itemId = String(registro && (registro.itemId || obterIdAnuncioFavoritos(registro.anuncio)) || '-').trim();
            const sim = registro && registro.sim || {};
            const partes = [];
            const loja = String(registro && registro.loja || '').trim();
            if (loja) partes.push(`Loja: ${loja}`);
            const tipo = textoTipoEnvioFavoritos(registro || {});
            if (tipo) partes.push(`tipo previsto: ${tipo}`);
            if (sim && sim.ok) {
                partes.push(`preco cheio previsto: ${formatarPrecoFavoritosMl(sim.preco)}`);
                const precoPromocional = sim.precoPromocionalCalculado ?? sim.precoPromocional ?? sim.precoCompetitivo;
                partes.push(`preco final promocional previsto: ${formatarPrecoFavoritosMl(precoPromocional)}`);
                if (sim.ajustePrecoUnico) partes.push(`ajuste preco unico: +${formatarPrecoFavoritosMl(sim.ajustePrecoUnico)}`);
                if (sim.limiteMargemAplicado) partes.push('limite de margem 15% aplicado');
            } else if (sim && sim.status) {
                partes.push(`simulacao invalida: ${textoCurtoStatusSimuladorFavoritos(sim.status)}`);
            }
            return {
                tipo: 'info',
                itemId,
                titulo: `${itemId} - alteracao planejada`,
                detalhe: partes.filter(Boolean).join(' | ') || 'Alteracao planejada.'
            };
        }

        function montarLinhaRelatorioFinalAlteracaoFavoritos(item, sucesso) {
            const itemId = String(item && item.itemId || item && item.data && item.data.item_id || '-').trim();
            if (sucesso) {
                const fallback = !!(item && item.data && item.data.fallback_sem_promocao_aplicado);
                return {
                    tipo: 'success',
                    itemId,
                    titulo: `${itemId} - alteracao feita${fallback ? ' com fallback' : ''}`,
                    detalhe: descreverSucessoEfetivacaoFavoritos(item)
                };
            }
            return {
                tipo: 'error',
                itemId,
                titulo: `${itemId} - alteracao nao feita`,
                detalhe: descreverFalhaEfetivacaoFavoritos(item)
            };
        }

        function montarSimulacaoHistoricoAlteracaoFavoritos(registro, data = null) {
            const sim = registro && registro.sim || {};
            const anuncio = registro && registro.anuncio || {};
            const custoBase = sim.custo ?? anuncio.custo ?? anuncio.custo_unitario ?? anuncio.custo_produto ?? anuncio.preco_custo ?? anuncio.valor_custo ?? null;
            const custoIdeal = sim.custoIdealAbaixoBase ?? sim.custo_ideal_abaixo_base ?? null;
            const precoAlvoCustoIdeal = sim.precoAlvoCustoIdeal ?? sim.preco_alvo_custo_ideal ?? null;
            return {
                ok: !!sim.ok,
                preco_previsto: sim.preco ?? null,
                preco_promocional_previsto: sim.precoPromocionalCalculado ?? sim.precoPromocional ?? sim.precoCompetitivo ?? null,
                preco_aplicado: data && data.preco_anuncio !== undefined ? data.preco_anuncio : null,
                preco_promocional_aplicado: data && data.preco_promocional !== undefined ? data.preco_promocional : null,
                margem_prevista: sim.margem ?? null,
                margem_aplicada: data && data.margem_estimada_contingencia !== undefined ? data.margem_estimada_contingencia : sim.margem ?? null,
                custo: custoBase,
                custo_base: custoBase,
                custo_unitario: custoBase,
                custo_produto: custoBase,
                preco_custo: custoBase,
                valor_custo: custoBase,
                limite_margem_aplicado: !!sim.limiteMargemAplicado,
                limiteMargemAplicado: !!sim.limiteMargemAplicado,
                preco_minimo_margem: sim.precoMinimoMargem ?? null,
                preco_alvo_custo_ideal: precoAlvoCustoIdeal,
                precoAlvoCustoIdeal: precoAlvoCustoIdeal,
                custo_ideal_abaixo_base: custoIdeal,
                custoIdealAbaixoBase: custoIdeal,
                preco_custo_necessario: custoIdeal,
                custo_para_concorrer: custoIdeal,
                custo_maximo_para_concorrer: custoIdeal,
                reducao_custo_ideal: sim.reducaoCustoIdeal ?? null,
                tipo_anuncio_atual: sim.tipoAnuncioAtual || '',
                tipo_anuncio_alvo: sim.tipoAnuncioAlvo || '',
                campanha_id: data && data.promotion_id || '',
                campanha_nome: data && data.campanha_nome || '',
                fallback_sem_promocao: !!(data && data.fallback_sem_promocao_aplicado)
            };
        }

        function montarVinculoHistoricoAlteracaoFavoritos(item, sucesso, sku) {
            const registro = item && item.registro || {};
            const itemId = String(item && item.itemId || registro.itemId || obterIdAnuncioFavoritos(registro.anuncio) || '').trim();
            const relatorioFinal = montarLinhaRelatorioFinalAlteracaoFavoritos(item, sucesso);
            const fallback = !!(sucesso && item && item.data && item.data.fallback_sem_promocao_aplicado);
            return {
                ordem: Number(registro.index) + 1 || 0,
                sku: String(sku || '').trim(),
                itemId,
                loja: String(registro.loja || '').trim(),
                status: sucesso ? 'success' : 'error',
                status_texto: sucesso
                    ? (fallback ? 'Feito sem campanha' : 'Alterado')
                    : 'Nao feito',
                nosso: clonarAnuncioHistoricoAlteracaoFavoritos(registro.anuncio),
                base: clonarAnuncioHistoricoAlteracaoFavoritos(registro.ranking),
                simulacao: montarSimulacaoHistoricoAlteracaoFavoritos(registro, sucesso ? item.data || {} : null),
                relatorio_inicial: montarLinhaRelatorioInicialAlteracaoFavoritos(registro),
                relatorio_final: relatorioFinal
            };
        }

        function montarHistoricoAlteracoesFavoritosPayload({ sku, opcoesPromocao, incluirOutrasContas, validos, bloqueadosPreEnvio, sucessos, falhas, mensagemFinal }) {
            const feitos = Array.isArray(sucessos) ? sucessos : [];
            const naoFeitos = Array.isArray(falhas) ? falhas : [];
            const grupoAtual = typeof obterGrupoRankingFavoritosSku === 'function'
                ? obterGrupoRankingFavoritosSku(sku)
                : null;
            const tituloGrupo = String(grupoAtual && grupoAtual.grupo && grupoAtual.grupo.titulo || '').trim();
            const vinculos = [
                ...feitos.map(item => montarVinculoHistoricoAlteracaoFavoritos(item, true, sku)),
                ...naoFeitos.map(item => montarVinculoHistoricoAlteracaoFavoritos(item, false, sku))
            ].filter(item => item && (item.itemId || item.nosso && item.nosso.id || item.base && item.base.id));
            const registrosIniciais = [
                ...(Array.isArray(validos) ? validos : []),
                ...(Array.isArray(bloqueadosPreEnvio) ? bloqueadosPreEnvio.map(item => item && item.registro).filter(Boolean) : [])
            ];
            return {
                data_iso: new Date().toISOString(),
                sku: String(sku || '').trim(),
                titulo: tituloGrupo,
                loja: favoritosLojaSelecionadaParaApi(favMlLojaSelecionada || '') || favMlLojaSelecionada || '',
                usuario: typeof nomeUsuarioHistoricoFavoritosAtual === 'function' ? nomeUsuarioHistoricoFavoritosAtual() : '',
                origem_ranking_id: favMlHistoricoExecucaoSelecionadaId || '',
                escopo: incluirOutrasContas ? 'todas_contas' : 'loja_atual',
                opcoes_promocao: clonarOpcoesPromocaoFavoritos(opcoesPromocao || {}),
                mensagem_final: mensagemFinal || '',
                relatorio_inicial: {
                    titulo: 'Relatorio inicial',
                    resumo: `${registrosIniciais.length} anuncio(s) planejado(s) para alteracao.`,
                    linhas: registrosIniciais.map(montarLinhaRelatorioInicialAlteracaoFavoritos)
                },
                relatorio_final: {
                    titulo: 'Relatorio final',
                    resumo: mensagemFinal || '',
                    sucessos: feitos.length,
                    falhas: naoFeitos.length,
                    linhas: [
                        ...feitos.map(item => montarLinhaRelatorioFinalAlteracaoFavoritos(item, true)),
                        ...naoFeitos.map(item => montarLinhaRelatorioFinalAlteracaoFavoritos(item, false))
                    ]
                },
                vinculos
            };
        }

        function salvarHistoricoAlteracoesFavoritosProcesso(payload) {
            if (!payload || !Array.isArray(payload.vinculos) || !payload.vinculos.length) return null;
            if (typeof registrarHistoricoAlteracoesFavoritos !== 'function') {
                console.warn('registrarHistoricoAlteracoesFavoritos ainda nao esta disponivel.');
                return null;
            }
            return registrarHistoricoAlteracoesFavoritos(payload);
        }

        function criarCelulaSimuladorPrecoFavoritos(anuncioConta, anuncioRanking, opcoesPromocao = null, simCalculada = null) {
            const td = document.createElement('td');
            td.className = 'ml-favoritos-simulador-cell';
            const sim = simCalculada || calcularSimulacaoPrecoFavoritos(anuncioConta, anuncioRanking, opcoesPromocao);
            if (!sim.ok) {
                const wrap = document.createElement('div');
                wrap.className = 'ml-favoritos-simulador-wrap';
                const badge = document.createElement('span');
                badge.className = 'ml-favoritos-margem-badge is-empty';
                badge.textContent = '-';
                const status = document.createElement('span');
                status.className = 'ml-favoritos-simulador-status';
                status.textContent = textoCurtoStatusSimuladorFavoritos(sim.status || 'Sem anuncio rankeado na mesma linha para simular.');
                td.title = sim.status || 'Sem anuncio rankeado na mesma linha para simular.';
                wrap.appendChild(badge);
                wrap.appendChild(status);
                td.appendChild(wrap);
                return td;
            }

            const wrap = document.createElement('div');
            wrap.className = 'ml-favoritos-simulador-wrap';
            const preco = document.createElement('span');
            preco.className = 'ml-favoritos-simulador-preco';
            preco.textContent = formatarPrecoFavoritosMl(sim.preco);
            const badge = document.createElement('span');
            badge.className = 'ml-favoritos-margem-badge ' + (sim.margem >= 0 ? 'is-positive' : 'is-negative');
            badge.textContent = formatarMargemAnuncioFavoritos(sim.margem);
            const linhasMeta = [];
            const adicionarLinhaMeta = (texto) => {
                if (!texto) return;
                linhasMeta.push(texto);
            };
            if (sim.percentualPromocao !== null) {
                const pctTxt = `${Number(sim.percentualPromocao).toFixed(1).replace('.', ',').replace(',0', '')}%`;
                const finalPromo = sim.precoPromocionalCalculado ?? sim.precoPromocional ?? sim.precoCompetitivo;
                adicionarLinhaMeta(`${pctTxt} -> ${formatarPrecoFavoritosMl(finalPromo)}`);
            } else if (sim.limiteMargemAplicado) {
                if (sim.descontoEfetivo >= 0.01) {
                    adicionarLinhaMeta(`-${formatarPrecoFavoritosMl(sim.descontoEfetivo)}`);
                }
                adicionarLinhaMeta('limite 15%');
            } else {
                adicionarLinhaMeta(`-${formatarPrecoFavoritosMl(sim.descontoEfetivo)}`);
            }
            wrap.appendChild(preco);
            wrap.appendChild(badge);
            if (sim.ajustePrecoUnico) {
                adicionarLinhaMeta('preco unico');
            }
            if (sim.trocarTipoAnuncio && sim.tipoAnuncioAlvo) {
                adicionarLinhaMeta(`tipo ${sim.tipoAnuncioAtual || '-'} -> ${sim.tipoAnuncioAlvo}`);
            }
            linhasMeta.forEach((texto) => {
                const meta = document.createElement('span');
                meta.className = 'ml-favoritos-simulador-meta';
                meta.textContent = texto;
                wrap.appendChild(meta);
            });
            td.title = [
                `Preco ranking: ${formatarPrecoFavoritosMl(sim.precoRanking)}`,
                `Desconto sorteado: ${formatarPrecoFavoritosMl(sim.descontoSorteado)}`,
                `Desconto aplicado: ${sim.descontoEfetivo >= 0 ? formatarPrecoFavoritosMl(sim.descontoEfetivo) : 'sem desconto possivel'}`,
                sim.percentualPromocao !== null
                    ? `Preco cheio sugerido antes da promocao: ${formatarPrecoFavoritosMl(sim.preco)}`
                    : `Preco simulado: ${formatarPrecoFavoritosMl(sim.preco)}`,
                sim.percentualPromocao !== null ? `Promocao fixa: ${Number(sim.percentualPromocao).toFixed(2).replace('.', ',')}%` : '',
                sim.percentualPromocao !== null ? `Preco final alvo apos promocao: ${formatarPrecoFavoritosMl(sim.precoPromocional)}` : '',
                sim.percentualPromocao !== null && sim.precoPromocionalCalculado !== null ? `Preco final calculado apos promocao: ${formatarPrecoFavoritosMl(sim.precoPromocionalCalculado)}` : '',
                sim.ajustePrecoUnico ? `Ajuste para evitar preco igual entre nossos anuncios: +${formatarPrecoFavoritosMl(sim.ajustePrecoUnico)}` : '',
                sim.trocarTipoAnuncio && sim.tipoAnuncioAlvo ? `Tipo do anuncio sera alterado para o tipo do ranking: ${sim.tipoAnuncioAtual || '-'} -> ${sim.tipoAnuncioAlvo}` : '',
                sim.taxaTipoAnuncioAplicada !== null && sim.taxaTipoAnuncioAplicada !== undefined ? `Taxa simulada pelo tipo alvo: ${(Number(sim.taxaTipoAnuncioAplicada) * 100).toFixed(2).replace('.', ',')}%` : '',
                `Margem simulada: ${formatarMargemAnuncioFavoritos(sim.margem)}`,
                sim.limiteMargemAplicado ? 'Limite de margem minima de 15% aplicado' : '',
                sim.limiteMargemAplicado && sim.custoIdealAbaixoBase !== null && sim.custoIdealAbaixoBase !== undefined
                    ? `Custo ideal para vender abaixo da base: ${textoCustoIdealComparacaoFavoritos(sim)}`
                    : '',
                `Liquido: ${formatarPrecoFavoritosMl(sim.valorLiquido)}`,
                `Custo: ${formatarPrecoFavoritosMl(sim.custo)}`,
                `Frete: ${formatarPrecoFavoritosMl(sim.frete)}`,
                `Tarifa estimada: ${formatarPrecoFavoritosMl(sim.tarifa)}`,
                `Imposto: ${formatarPrecoFavoritosMl(sim.impostoValor)}`
            ].filter(Boolean).join(' | ');
            td.appendChild(wrap);
            return td;
        }
        function obterLojaAnuncioFavoritos(anuncio) {
            return String(anuncio && (anuncio.loja || anuncio.loja_sync || anuncio.loja_conta) || '').trim();
        }
        function chaveSkuSelecaoAlteracaoFavoritos(sku) {
            return skuChaveSku(sku || favMlSkuSelecionado || '') || '';
        }
        function obterSetNaoAlterarFavoritosSku(sku, criar = false) {
            const chaveSku = chaveSkuSelecaoAlteracaoFavoritos(sku);
            if (!chaveSku) return null;
            if (!favMlAnunciosNaoAlterarPorSku || !(favMlAnunciosNaoAlterarPorSku instanceof Map)) {
                favMlAnunciosNaoAlterarPorSku = new Map();
            }
            let set = favMlAnunciosNaoAlterarPorSku.get(chaveSku);
            if (!set && criar) {
                set = new Set();
                favMlAnunciosNaoAlterarPorSku.set(chaveSku, set);
            }
            return set || null;
        }
        function chavesAnuncioAlteracaoFavoritos(anuncio, registro = null) {
            const itemId = obterIdAnuncioFavoritos(anuncio || registro && registro.anuncio || {});
            if (!itemId) return [];
            const loja = skuNormalizarLoja(
                registro && registro.loja
                || obterLojaEfetivarFavoritos(anuncio)
                || obterLojaAnuncioFavoritos(anuncio)
                || ''
            );
            const chaves = new Set([`id:${itemId}`]);
            if (loja) chaves.add(`loja:${loja}|${itemId}`);
            return Array.from(chaves);
        }
        function anuncioSelecionadoAlteracaoFavoritos(sku, anuncio, registro = null) {
            const set = obterSetNaoAlterarFavoritosSku(sku, false);
            if (!set || !set.size) return true;
            const chaves = chavesAnuncioAlteracaoFavoritos(anuncio, registro);
            if (!chaves.length) return true;
            return !chaves.some(chave => set.has(chave));
        }
        function definirAnuncioSelecionadoAlteracaoFavoritos(sku, anuncio, selecionado) {
            const set = obterSetNaoAlterarFavoritosSku(sku, !selecionado);
            const chaves = chavesAnuncioAlteracaoFavoritos(anuncio);
            if (!set || !chaves.length) return true;
            chaves.forEach(chave => {
                if (selecionado) set.delete(chave);
                else set.add(chave);
            });
            if (!set.size) {
                const chaveSku = chaveSkuSelecaoAlteracaoFavoritos(sku);
                if (chaveSku && favMlAnunciosNaoAlterarPorSku) favMlAnunciosNaoAlterarPorSku.delete(chaveSku);
            }
            return selecionado;
        }
        function limparSelecaoAlteracaoFavoritosSku(sku) {
            const chaveSku = chaveSkuSelecaoAlteracaoFavoritos(sku);
            if (chaveSku && favMlAnunciosNaoAlterarPorSku) {
                favMlAnunciosNaoAlterarPorSku.delete(chaveSku);
            }
        }
        function filtrarRegistrosSelecionadosAlteracaoFavoritos(registros, sku) {
            return (Array.isArray(registros) ? registros : []).filter(registro => {
                return anuncioSelecionadoAlteracaoFavoritos(sku, registro && registro.anuncio, registro);
            });
        }
        function contarRegistrosDesmarcadosAlteracaoFavoritos(registros, sku) {
            const lista = Array.isArray(registros) ? registros : [];
            return lista.length - filtrarRegistrosSelecionadosAlteracaoFavoritos(lista, sku).length;
        }
        function obterRegistrosSimulacaoFavoritos(anuncios, sku, opcoesPromocao, cachePreferencial = null) {
            const lista = Array.isArray(anuncios) ? anuncios : [];
            const ranking = obterRankingFavoritosParaSimulador(sku);
            const precosFinaisReservados = new Set();
            const precosCheiosReservados = new Set();
            const assinaturaPromocaoAtual = assinaturaOpcoesPromocaoFavoritos(opcoesPromocao);
            return lista.map((anuncio, index) => {
                const rankingLinha = ranking[index];
                const itemId = obterIdAnuncioFavoritos(anuncio);
                const cache = Array.isArray(cachePreferencial) ? cachePreferencial[index] : null;
                let sim = cache && cache.itemId === itemId
                    ? cache.sim
                    : null;
                if (sim && sim.ok && assinaturaPromocaoAtual && sim.assinaturaPromocao !== assinaturaPromocaoAtual) {
                    sim = null;
                }
                if (sim && sim.ok) {
                    const chaveFinalCache = chavePrecoCentavosFavoritos(obterPrecoFinalSimulacaoFavoritos(sim));
                    const chaveCheioCache = chavePrecoCentavosFavoritos(sim.preco);
                    if (
                        (chaveFinalCache && precosFinaisReservados.has(chaveFinalCache))
                        || (chaveCheioCache && precosCheiosReservados.has(chaveCheioCache))
                    ) {
                        sim = null;
                    } else {
                        if (chaveFinalCache) precosFinaisReservados.add(chaveFinalCache);
                        if (chaveCheioCache) precosCheiosReservados.add(chaveCheioCache);
                    }
                }
                if (!sim) {
                    sim = calcularSimulacaoPrecoFavoritos(anuncio, rankingLinha, opcoesPromocao, {
                        precosFinaisReservados,
                        precosCheiosReservados
                    });
                }
                return {
                    anuncio,
                    ranking: rankingLinha,
                    sim,
                    index,
                    itemId,
                    selecionadoAlteracao: anuncioSelecionadoAlteracaoFavoritos(sku, anuncio),
                    loja: obterLojaEfetivarFavoritos(anuncio),
                    lojaOriginal: obterLojaAnuncioFavoritos(anuncio)
                };
            });
        }
        function filtrarRegistrosEfetivaveisFavoritos(registros) {
            const vistos = new Set();
            return (Array.isArray(registros) ? registros : []).filter(registro => {
                const sim = registro && registro.sim;
                const chave = `${skuNormalizarLoja(registro && registro.loja)}|${registro && registro.itemId}`;
                if (!registro || !registro.itemId || !registro.loja || !sim || !sim.ok || sim.percentualPromocao === null) return false;
                if (vistos.has(chave)) return false;
                vistos.add(chave);
                return true;
            });
        }
        function explicarRegistrosNaoEfetivaveisFavoritos(registros, registrosAntesFiltro = null) {
            const lista = Array.isArray(registros) ? registros : [];
            const listaOriginal = Array.isArray(registrosAntesFiltro) ? registrosAntesFiltro : lista;
            if (!lista.length && listaOriginal.length) {
                return 'Os anuncios encontrados pertencem a outra loja. Marque "Fazer tambem nas outras contas" ou selecione a loja do anuncio.';
            }
            if (!lista.length) return 'Nenhum anuncio proprio foi encontrado para este SKU.';
            const semPercentual = lista.find(registro => registro && registro.sim && registro.sim.ok && registro.sim.percentualPromocao === null);
            if (semPercentual) return 'A simulacao estava sem percentual de promocao. Salve campanha e % novamente para recalcular.';
            const semSimulacao = lista.find(registro => !registro || !registro.sim || !registro.sim.ok);
            if (semSimulacao) {
                return semSimulacao && semSimulacao.sim && semSimulacao.sim.status
                    ? textoCurtoStatusSimuladorFavoritos(semSimulacao.sim.status)
                    : 'A simulacao nao ficou valida.';
            }
            const semDados = lista.find(registro => !registro || !registro.itemId || !registro.loja);
            if (semDados) return 'Faltou MLB ou loja em pelo menos um anuncio.';
            return 'Confira os motivos exibidos na coluna Simulador.';
        }
        function criarCardConfirmacaoFavoritos(rotulo, valor) {
            const card = document.createElement('div');
            card.className = 'ml-favoritos-confirm-card';
            const label = document.createElement('span');
            label.className = 'ml-favoritos-confirm-label';
            label.textContent = rotulo || '';
            const value = document.createElement('span');
            value.className = 'ml-favoritos-confirm-value';
            value.textContent = valor || '-';
            card.appendChild(label);
            card.appendChild(value);
            return card;
        }
        function perguntarConfirmacaoEfetivarFavoritos(opcoes = {}) {
            const total = Number(opcoes.total || 0);
            const incluirOutrasContas = !!opcoes.incluirOutrasContas;
            const buscouAnunciosAoAprovar = !!opcoes.buscouAnunciosAoAprovar;
            const nomeCampanha = String(opcoes.nomeCampanha || 'campanha selecionada').trim();
            const totalTrocaTipo = Number(opcoes.totalTrocaTipo || 0);
            const totalTipoMantido = Number(opcoes.totalTipoMantido || 0);
            const totalTrocaAposPromocao = Number(opcoes.totalTrocaAposPromocao || 0);
            const escopo = incluirOutrasContas
                ? 'Inclui outras contas com este SKU'
                : (buscouAnunciosAoAprovar ? 'Somente a loja atual apos buscar o SKU' : 'Somente a loja atual');
            const trocaTipo = totalTrocaTipo
                ? `${totalTrocaTipo} anuncio(s) vao mudar Premium/Classico`
                : (totalTipoMantido ? `${totalTipoMantido} manterao o tipo atual por bloqueio do ML` : 'Sem troca de tipo');
            const textoFallback = [
                `Aprovar e alterar ${total} anuncio(s)?`,
                escopo,
                `Campanha: ${nomeCampanha}`,
                totalTrocaTipo ? `${totalTrocaTipo} anuncio(s) tambem terao o tipo alterado para igual ao ranking.` : '',
                totalTrocaAposPromocao ? `${totalTrocaAposPromocao} anuncio(s) so mostraram downgrade bloqueado antes da limpeza; o sistema vai remover promocoes atuais e tentar de novo.` : '',
                totalTipoMantido ? `${totalTipoMantido} anuncio(s) serao recalculados mantendo Premium porque o Mercado Livre nao liberou downgrade para Classico.` : '',
                'O sistema vai sair da promocao atual, ajustar tipo/preco cheio, aplicar a promocao e conferir no Mercado Livre.',
                'Quando houver mais de um anuncio nosso, os precos finais e cheios nao serao iguais.'
            ].filter(Boolean).join('\n');
            if (!mlFavoritosBalloonEl || !mlFavoritosBalloonTextEl || !mlFavoritosBalloonActionsEl) {
                return Promise.resolve(window.confirm(textoFallback));
            }
            if (mlFavoritosPerguntaResolver) {
                mlFavoritosPerguntaResolver(false);
                mlFavoritosPerguntaResolver = null;
            }
            return new Promise(resolve => {
                const finalizar = (confirmado, botao) => {
                    mlFavoritosPerguntaResolver = null;
                    if (typeof window.favoritosResolverAcaoBalao === 'function') {
                        window.favoritosResolverAcaoBalao(resolve, !!confirmado, botao, {
                            esconder: true
                        });
                    } else {
                        esconderBalaoFavoritosStatus();
                        setTimeout(() => resolve(!!confirmado), 0);
                    }
                };
                mlFavoritosPerguntaResolver = finalizar;
                mostrarBalaoFavoritosStatus('', {
                    manterAcoes: true,
                    larga: true,
                    titulo: 'Confirmar alteracoes no Mercado Livre'
                });
                mlFavoritosBalloonTextEl.innerHTML = '';
                mlFavoritosBalloonActionsEl.innerHTML = '';

                const layout = document.createElement('div');
                layout.className = 'ml-favoritos-confirm-layout';

                const hero = document.createElement('div');
                hero.className = 'ml-favoritos-confirm-hero';
                const heroTitle = document.createElement('strong');
                heroTitle.textContent = `Aprovar e alterar ${total} anuncio(s)?`;
                const heroText = document.createElement('span');
                heroText.textContent = 'Revise as acoes antes de enviar. Nada sera enviado ao Mercado Livre se voce cancelar.';
                hero.appendChild(heroTitle);
                hero.appendChild(heroText);
                layout.appendChild(hero);

                const grid = document.createElement('div');
                grid.className = 'ml-favoritos-confirm-grid';
                grid.appendChild(criarCardConfirmacaoFavoritos('Anuncios', `${total} pronto(s)`));
                grid.appendChild(criarCardConfirmacaoFavoritos('Escopo', escopo));
                grid.appendChild(criarCardConfirmacaoFavoritos('Campanha', nomeCampanha));
                grid.appendChild(criarCardConfirmacaoFavoritos('Tipo do anuncio', trocaTipo));
                layout.appendChild(grid);

                const steps = document.createElement('ul');
                steps.className = 'ml-favoritos-confirm-steps';
                [
                    'Remover a promocao atual quando necessario.',
                    totalTrocaAposPromocao
                        ? 'Aguardar o Mercado Livre liberar a troca apos remover promocoes e entao alterar Premium/Classico.'
                        : (totalTipoMantido
                        ? 'Alterar o tipo quando o Mercado Livre liberar; quando bloquear downgrade, manter o tipo atual e recalcular o preco.'
                        : 'Alterar o tipo para ficar igual ao anuncio do ranking.'),
                    'Alterar o preco cheio e aplicar a campanha selecionada.',
                    'Conferir no Mercado Livre se o preco final promocional ficou correto.'
                ].forEach(texto => {
                    const li = document.createElement('li');
                    li.textContent = texto;
                    steps.appendChild(li);
                });
                layout.appendChild(steps);

                const warning = document.createElement('div');
                warning.className = 'ml-favoritos-confirm-warning';
                warning.textContent = totalTrocaAposPromocao
                    ? 'Alguns anuncios so liberam downgrade depois que a promocao atual sai. O sistema vai limpar as promocoes atuais, esperar a liberacao do Mercado Livre e tentar a troca de modalidade antes de aplicar preco/campanha.'
                    : (totalTipoMantido
                    ? 'Alguns anuncios ficarao Premium porque o Mercado Livre nao disponibilizou downgrade para eles agora. Os precos foram recalculados nessa condicao para preservar margem e campanha.'
                    : 'Quando houver mais de um anuncio nosso, os precos finais e cheios podem ser diferentes para evitar duplicidade e preservar a margem.');
                layout.appendChild(warning);

                mlFavoritosBalloonTextEl.appendChild(layout);

                const cancelar = document.createElement('button');
                cancelar.type = 'button';
                cancelar.className = 'is-muted';
                cancelar.textContent = 'Cancelar';
                cancelar.addEventListener('click', () => finalizar(false, cancelar));

                const confirmar = document.createElement('button');
                confirmar.type = 'button';
                confirmar.className = 'is-primary';
                confirmar.textContent = 'Aprovar e alterar';
                confirmar.addEventListener('click', () => finalizar(true, confirmar));

                mlFavoritosBalloonActionsEl.appendChild(cancelar);
                mlFavoritosBalloonActionsEl.appendChild(confirmar);
                mlFavoritosBalloonEl.classList.remove('is-error', 'is-comparison');
                mlFavoritosBalloonEl.classList.add('is-wide');
                posicionarBalaoFavoritosStatus();
            });
        }
        function normalizarNomePromocaoFavoritos(valor) {
            return String(valor || '')
                .normalize('NFD')
                .replace(/[\u0300-\u036f]/g, '')
                .replace(/\s+/g, ' ')
                .trim()
                .toLowerCase();
        }
        async function carregarFavoritosAnunciosSkuTodasContas(sku) {
            const skuSelecionado = String(sku || '').trim();
            if (!skuSelecionado) return [];
            const params = new URLSearchParams({ sku: skuSelecionado });
            params.set('compartilhar_sku', '1');
            params.set('todas_contas', '1');
            const response = await fetch(`/api/favoritos/ml/anuncios-sku?${params.toString()}`, {
                headers: obterAuthHeaders(),
                cache: 'no-store'
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(data.detail || `HTTP ${response.status}`);
            }
            return Array.isArray(data.anuncios) ? data.anuncios : [];
        }
        function registroFavoritosExigeTrocaTipoAnuncio(registro) {
            const sim = registro && registro.sim;
            if (!sim) return false;
            const alvo = String(sim.listingTypeIdAlvo || '').trim();
            if (!alvo) return false;
            const atual = String(sim.listingTypeIdAtual || '').trim();
            return !!sim.trocarTipoAnuncio || !atual || atual !== alvo;
        }
        function validacaoPermiteTentativaAposPromocoesFavoritos(registro, validacao) {
            const sim = registro && registro.sim || {};
            const atual = listingTypeIdFavoritos((validacao && validacao.current) || sim.listingTypeIdAtual);
            const alvo = listingTypeIdFavoritos((validacao && validacao.target) || sim.listingTypeIdAlvo);
            return atual
                && alvo
                && atual !== alvo
                && ['gold_pro', 'gold_special'].includes(atual)
                && ['gold_pro', 'gold_special'].includes(alvo);
        }
        function textoTipoEnvioFavoritos(registro) {
            const sim = registro && registro.sim;
            if (!sim) return 'sem troca';
            if (sim.trocaTipoAposPromocaoMl) {
                return `${sim.tipoAnuncioAtual || '-'} -> ${sim.tipoAnuncioAlvo || '-'} via API ML`;
            }
            if (sim.tipoMantidoPorBloqueioMl) {
                const atual = sim.tipoAnuncioAtual || nomeTipoPorListingTypeFavoritos(sim.listingTypeIdAtual) || '-';
                const original = sim.tipoAnuncioAlvoOriginal || sim.tipoAnuncioAlvo || '';
                return original ? `mantendo ${atual}; ML bloqueou ${atual} -> ${original}` : `mantendo ${atual}`;
            }
            return registroFavoritosExigeTrocaTipoAnuncio(registro)
                ? `${sim.tipoAnuncioAtual || '-'} -> ${sim.tipoAnuncioAlvo || '-'}`
                : 'sem troca';
        }
        async function validarRegistrosEfetivaveisFavoritosMercadoLivre(registros) {
            const lista = Array.isArray(registros) ? registros : [];
            const paraValidar = lista.filter(registro => {
                const sim = registro && registro.sim;
                return !!(registro && registro.itemId && registro.loja && sim && registroFavoritosExigeTrocaTipoAnuncio(registro));
            });
            if (!paraValidar.length) {
                return { validos: lista, bloqueados: [] };
            }
            mostrarBalaoFavoritosStatus('Validando no Mercado Livre se a troca Premium/Classico esta disponivel...', {
                larga: true
            });
            const response = await fetch('/api/favoritos/ml/validar-efetivacao', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...obterAuthHeaders()
                },
                body: JSON.stringify({
                    itens: paraValidar.map(registro => ({
                        loja: registro.loja || '',
                        item_id: registro.itemId || '',
                        listing_type_id_alvo: registro.sim && registro.sim.listingTypeIdAlvo || '',
                        tipo_anuncio_alvo: registro.sim && registro.sim.tipoAnuncioAlvo || ''
                    }))
                })
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(data.detail || `HTTP ${response.status}`);
            }
            const respostas = Array.isArray(data.itens) ? data.itens : [];
            const mapa = new Map();
            respostas.forEach(item => {
                const chave = `${skuNormalizarLoja(item && item.loja)}|${String(item && item.item_id || '').trim().toUpperCase()}`;
                if (chave) mapa.set(chave, item);
            });
            const validos = [];
            const bloqueados = [];
            lista.forEach(registro => {
                const sim = registro && registro.sim;
                if (!sim || !registroFavoritosExigeTrocaTipoAnuncio(registro)) {
                    validos.push(registro);
                    return;
                }
                const chave = `${skuNormalizarLoja(registro.loja)}|${String(registro.itemId || '').trim().toUpperCase()}`;
                const validacao = mapa.get(chave);
                if (!validacao || validacao.ok !== false) {
                    validos.push(registro);
                    return;
                }
                if (validacaoPermiteTentativaAposPromocoesFavoritos(registro, validacao)) {
                    sim.trocaTipoAposPromocaoMl = true;
                    sim.trocaTipoAposPromocaoMotivo = validacao.message || 'Mercado Livre pode liberar downgrade depois de remover as promocoes atuais.';
                    validos.push(registro);
                    return;
                }
                const motivo = validacao.message || 'Mercado Livre nao disponibiliza essa troca Premium/Classico para o anuncio agora.';
                bloqueados.push({
                    itemId: registro.itemId,
                    erro: motivo,
                    registro,
                    validacao
                });
            });
            return { validos, bloqueados };
        }
        function criarReservasPrecosEfetivacaoFavoritos(registros) {
            const precosFinaisReservados = new Set();
            const precosCheiosReservados = new Set();
            (Array.isArray(registros) ? registros : []).forEach(registro => {
                const sim = registro && registro.sim;
                if (!sim || !sim.ok) return;
                const chaveFinal = chavePrecoCentavosFavoritos(obterPrecoFinalSimulacaoFavoritos(sim));
                const chaveCheio = chavePrecoCentavosFavoritos(sim.preco);
                if (chaveFinal) precosFinaisReservados.add(chaveFinal);
                if (chaveCheio) precosCheiosReservados.add(chaveCheio);
            });
            return { precosFinaisReservados, precosCheiosReservados };
        }
        function bloqueioPermiteFallbackTipoAtualFavoritos(bloqueio) {
            const validacao = bloqueio && bloqueio.validacao || {};
            const registro = bloqueio && bloqueio.registro || {};
            const sim = registro.sim || {};
            const atual = listingTypeIdFavoritos(validacao.current || sim.listingTypeIdAtual);
            const alvo = listingTypeIdFavoritos(validacao.target || sim.listingTypeIdAlvo);
            return atual === 'gold_pro' && alvo === 'gold_special';
        }
        function recalcularRegistroMantendoTipoAtualFavoritos(bloqueio, opcoesPromocao, reservas) {
            const registro = bloqueio && bloqueio.registro;
            if (!registro || !registro.anuncio || !registro.ranking) return null;
            const simOriginal = registro.sim || {};
            const sim = calcularSimulacaoPrecoFavoritos(registro.anuncio, registro.ranking, opcoesPromocao, {
                ...(reservas || {}),
                manterTipoAtual: true
            });
            if (!sim || !sim.ok) {
                return {
                    ok: false,
                    erro: sim && sim.status
                        ? `Nao foi possivel recalcular mantendo Premium: ${textoCurtoStatusSimuladorFavoritos(sim.status)}`
                        : 'Nao foi possivel recalcular mantendo Premium.'
                };
            }
            const motivo = bloqueio && bloqueio.erro
                ? bloqueio.erro
                : 'Mercado Livre nao liberou a troca Premium -> Classico para este anuncio agora.';
            sim.tipoMantidoPorBloqueioMl = true;
            sim.tipoBloqueioMlMotivo = motivo;
            sim.tipoAnuncioAlvoOriginal = simOriginal.tipoAnuncioAlvo || '';
            sim.listingTypeIdAlvoOriginal = simOriginal.listingTypeIdAlvo || '';
            sim.tipoAnuncioAlvo = sim.tipoAnuncioAtual || nomeTipoPorListingTypeFavoritos(sim.listingTypeIdAtual) || 'Premium';
            sim.listingTypeIdAlvo = sim.listingTypeIdAtual || 'gold_pro';
            sim.trocarTipoAnuncio = false;
            return {
                ok: true,
                registro: {
                    ...registro,
                    sim,
                    fallbackTipoAtualMl: true,
                    fallbackTipoAtualMotivo: motivo
                }
            };
        }
        function resolverFallbackTipoAtualFavoritos(bloqueados, validos, opcoesPromocao) {
            const saidaValidos = Array.isArray(validos) ? [...validos] : [];
            const aindaBloqueados = [];
            const fallbacks = [];
            const reservas = criarReservasPrecosEfetivacaoFavoritos(saidaValidos);
            (Array.isArray(bloqueados) ? bloqueados : []).forEach(bloqueio => {
                if (!bloqueioPermiteFallbackTipoAtualFavoritos(bloqueio)) {
                    aindaBloqueados.push(bloqueio);
                    return;
                }
                const fallback = recalcularRegistroMantendoTipoAtualFavoritos(bloqueio, opcoesPromocao, reservas);
                if (!fallback || !fallback.ok || !fallback.registro) {
                    aindaBloqueados.push({
                        ...bloqueio,
                        erro: fallback && fallback.erro || bloqueio.erro || 'Mercado Livre bloqueou a troca de tipo e nao foi possivel recalcular mantendo Premium.'
                    });
                    return;
                }
                saidaValidos.push(fallback.registro);
                fallbacks.push({
                    itemId: fallback.registro.itemId,
                    motivo: fallback.registro.fallbackTipoAtualMotivo,
                    registro: fallback.registro
                });
            });
            return { validos: saidaValidos, bloqueados: aindaBloqueados, fallbacks };
        }
        async function resolverOpcoesPromocaoEfetivacaoParaLoja(opcoesPromocao, loja) {
            if (!opcoesPromocao || !opcoesPromocao.usar_promocao) return opcoesPromocao;
            const lojaApi = favoritosLojaSelecionadaParaApi(loja || '');
            const lojaAtual = favoritosLojaSelecionadaParaApi(favMlLojaSelecionada || mlSkuLojaSelecionada || skuLojaSelecionada || '');
            if (!lojaApi || (lojaAtual && skuNormalizarLoja(lojaApi) === skuNormalizarLoja(lojaAtual))) {
                return opcoesPromocao;
            }

            const campanhaOriginal = opcoesPromocao.campanha || {};
            const idOriginal = String(campanhaOriginal.id || campanhaOriginal.campaign_id || '').trim();
            const nomeOriginal = normalizarNomePromocaoFavoritos(campanhaOriginal.nome || campanhaOriginal.name || campanhaOriginal.title || '');
            const tipoOriginal = String(campanhaOriginal.tipo || campanhaOriginal.type || campanhaOriginal.promotion_type || '').trim().toUpperCase();
            const chaveCache = skuNormalizarLoja(lojaApi);
            let campanhas = favMlPromocoesPorLojaCache.get(chaveCache);
            if (!campanhas) {
                campanhas = await carregarPromocoesAtivasFavoritos(lojaApi);
                favMlPromocoesPorLojaCache.set(chaveCache, campanhas);
            }

            const encontrada = campanhas.find(campanha => String(campanha && campanha.id || '').trim() === idOriginal)
                || campanhas.find(campanha => normalizarNomePromocaoFavoritos(campanha && (campanha.name || campanha.title || campanha.nome)) === nomeOriginal)
                || campanhas.find(campanha => {
                    const tipo = String(campanha && (campanha.type || campanha.promotion_type) || '').trim().toUpperCase();
                    const nome = normalizarNomePromocaoFavoritos(campanha && (campanha.name || campanha.title || campanha.nome));
                    return tipoOriginal && tipo === tipoOriginal && nomeOriginal && (nome.includes(nomeOriginal) || nomeOriginal.includes(nome));
                });
            if (!encontrada) {
                throw new Error(`Promocao ${campanhaOriginal.nome || campanhaOriginal.id || ''} nao encontrada na conta ${lojaApi}.`);
            }

            const ajustada = clonarOpcoesPromocaoFavoritos(opcoesPromocao) || {};
            ajustada.campanha = {
                id: String(encontrada.id || '').trim(),
                nome: String(encontrada.name || encontrada.title || encontrada.id || '').trim(),
                tipo: String(encontrada.type || encontrada.promotion_type || '').trim(),
                status: String(encontrada.status || '').trim()
            };
            return ajustada;
        }
        function atualizarPainelEfetivarFavoritos() {
            if (!favMlEfetivarPanelEl || !favMlEfetivarInfoEl || !favMlEfetivarBtnEl) return;
            const sku = String(favMlSkuSelecionado || '').trim();
            const lista = Array.isArray(favMlAnunciosSkuAtual) ? favMlAnunciosSkuAtual : [];
            const rankingSelecionado = sku ? obterRankingFavoritosParaSimulador(sku) : [];
            const temRankingSelecionado = rankingSelecionado.length > 0;
            if (!sku || (!lista.length && !temRankingSelecionado)) {
                favMlEfetivarPanelEl.classList.add('hidden');
                if (favMlPromocaoBtnEl) favMlPromocaoBtnEl.classList.add('hidden');
                return;
            }
            favMlEfetivarPanelEl.classList.remove('hidden');
            const opcoesPromocao = obterOpcoesPromocaoFavoritosSku(sku);
            const promocaoPronta = opcoesPromocaoFavoritosProntas(opcoesPromocao);
            if (!favMlEfetivacaoEmExecucao) favMlEfetivarBtnEl.textContent = 'Aprovar e alterar';
            if (favMlPromocaoBtnEl) {
                favMlPromocaoBtnEl.classList.remove('hidden');
                favMlPromocaoBtnEl.disabled = favMlEfetivacaoEmExecucao || !sku;
                favMlPromocaoBtnEl.textContent = promocaoPronta ? 'Alterar campanha/%' : 'Campanha e %';
            }
            const registros = lista.length
                ? obterRegistrosSimulacaoFavoritos(lista, sku, opcoesPromocao, favMlSimulacoesSkuAtual)
                : [];
            const registrosSelecionados = filtrarRegistrosSelecionadosAlteracaoFavoritos(registros, sku);
            const totalDesmarcados = registros.length - registrosSelecionados.length;
            const validos = filtrarRegistrosEfetivaveisFavoritos(registrosSelecionados);
            const invalidos = registrosSelecionados.filter(registro => !validos.includes(registro));
            if (!promocaoPronta) {
                favMlEfetivarInfoEl.textContent = 'Ranking alinhado. Escolha campanha e % agora, ou clique em Aprovar e alterar para informar antes de enviar ao Mercado Livre.';
                favMlEfetivarBtnEl.disabled = favMlEfetivacaoEmExecucao || !temRankingSelecionado;
                return;
            }
            if (!lista.length) {
                const origemRanking = favMlHistoricoExecucaoSelecionadaId === FAV_ML_RANKING_ATUAL_ID
                    ? 'Ranking atual'
                    : 'Ranking do historico';
                favMlEfetivarInfoEl.textContent = `${origemRanking} habilitado para aprovar e alterar. Clique para buscar os anuncios do SKU e simular.`;
                favMlEfetivarBtnEl.disabled = favMlEfetivacaoEmExecucao || !temRankingSelecionado;
                return;
            }
            const exemploErro = invalidos.find(registro => registro && registro.sim && !registro.sim.ok);
            const motivo = exemploErro && exemploErro.sim && exemploErro.sim.status
                ? ` Linhas sem valor: ${textoCurtoStatusSimuladorFavoritos(exemploErro.sim.status)}.`
                : '';
            if (!registrosSelecionados.length && registros.length) {
                favMlEfetivarInfoEl.textContent = `Nenhum anuncio selecionado para alterar. Marque ao menos um anuncio.${totalDesmarcados ? ` ${totalDesmarcados} desmarcado(s).` : ''}`;
                favMlEfetivarBtnEl.disabled = true;
                return;
            }
            const textoDesmarcados = totalDesmarcados ? ` ${totalDesmarcados} desmarcado(s) nao serao alterados.` : '';
            favMlEfetivarInfoEl.textContent = validos.length
                ? `${validos.length} anuncio(s) selecionado(s) com simulacao pronta para aprovar.${textoDesmarcados}${motivo}`
                : `Nenhum anuncio selecionado com simulacao valida para aprovar.${textoDesmarcados}${motivo}`;
            favMlEfetivarBtnEl.disabled = favMlEfetivacaoEmExecucao || !validos.length;
        }
        async function efetivarFavoritosMercadoLivreAprovados() {
            if (favMlEfetivacaoEmExecucao) return;
            const sku = String(favMlSkuSelecionado || '').trim();
            if (!sku) {
                mostrarBalaoFavoritosStatus('Selecione um SKU antes de aprovar as alteracoes.', {
                    erro: true,
                    tempoMs: 6000
                });
                atualizarPainelEfetivarFavoritos();
                return;
            }
            let opcoesPromocao = await garantirPromocaoFavoritosSkuAtual(sku);
            if (!opcoesPromocao) return;

            const incluirOutrasContas = !!(favMlEfetivarOutrasContasEl && favMlEfetivarOutrasContasEl.checked);
            let anuncios = Array.isArray(favMlAnunciosSkuAtual) ? favMlAnunciosSkuAtual : [];
            let cache = favMlSimulacoesSkuAtual;
            let buscouAnunciosAoAprovar = false;
            if (incluirOutrasContas || !anuncios.length) {
                const mensagemBusca = incluirOutrasContas
                    ? 'Buscando anuncios deste SKU em todas as contas integradas...'
                    : 'Buscando anuncios deste SKU para aprovar o ranking selecionado...';
                mostrarBalaoFavoritosStatus(mensagemBusca, { larga: true });
                try {
                    anuncios = await carregarFavoritosAnunciosSkuTodasContas(sku);
                } catch (err) {
                    mostrarBalaoFavoritosStatus(`Erro ao buscar anuncios do SKU: ${err && err.message ? err.message : err}`, {
                        erro: true,
                        larga: true
                    });
                    atualizarPainelEfetivarFavoritos();
                    return;
                }
                cache = null;
                buscouAnunciosAoAprovar = true;
            }
            const lojaAtualNorm = skuNormalizarLoja(favoritosLojaSelecionadaParaApi(favMlLojaSelecionada || ''));
            let registros = obterRegistrosSimulacaoFavoritos(anuncios, sku, opcoesPromocao, cache);
            const registrosAntesFiltroLoja = registros;
            if (!incluirOutrasContas && lojaAtualNorm) {
                registros = registros.filter(registro => skuNormalizarLoja(registro.loja) === lojaAtualNorm);
            }
            const registrosAntesFiltroSelecao = registros;
            registros = filtrarRegistrosSelecionadosAlteracaoFavoritos(registros, sku);
            if (!registros.length && registrosAntesFiltroSelecao.length) {
                mostrarBalaoFavoritosStatus('Nenhum anuncio selecionado para alterar. Marque ao menos um anuncio na coluna Alterar.', {
                    erro: true,
                    tempoMs: 7500,
                    larga: true
                });
                atualizarPainelEfetivarFavoritos();
                return;
            }
            let validos = filtrarRegistrosEfetivaveisFavoritos(registros);
            if (!validos.length) {
                const motivo = explicarRegistrosNaoEfetivaveisFavoritos(registros, registrosAntesFiltroLoja);
                mostrarBalaoFavoritosStatus(`Nenhum anuncio com simulacao valida para aprovar. ${motivo}`, {
                    erro: true,
                    tempoMs: 7500,
                    larga: true
                });
                atualizarPainelEfetivarFavoritos();
                return;
            }
            let bloqueadosPreEnvio = [];
            try {
                const validacaoEnvio = await validarRegistrosEfetivaveisFavoritosMercadoLivre(validos);
                validos = validacaoEnvio.validos || [];
                bloqueadosPreEnvio = validacaoEnvio.bloqueados || [];
            } catch (err) {
                mostrarBalaoFavoritosStatus(`Erro ao validar anuncios no Mercado Livre antes de alterar: ${err && err.message ? err.message : err}`, {
                    erro: true,
                    tempoMs: 8000,
                    larga: true
                });
                atualizarPainelEfetivarFavoritos();
                return;
            }
            if (!validos.length) {
                const primeiroBloqueio = bloqueadosPreEnvio[0];
                const motivo = primeiroBloqueio && primeiroBloqueio.erro
                    ? ` Primeiro bloqueio em ${primeiroBloqueio.itemId}: ${primeiroBloqueio.erro}`
                    : ' Nenhum MLB passou na validacao final do Mercado Livre.';
                mostrarBalaoFavoritosStatus(`Nenhum anuncio valido para enviar ao Mercado Livre.${motivo}`, {
                    erro: true,
                    tempoMs: 9000,
                    larga: true
                });
                atualizarPainelEfetivarFavoritos();
                return;
            }

            const nomeCampanha = opcoesPromocao.campanha && (opcoesPromocao.campanha.nome || opcoesPromocao.campanha.id) || 'campanha selecionada';
            const totalTrocaTipo = validos.filter(registro => registroFavoritosExigeTrocaTipoAnuncio(registro)).length;
            const totalTrocaAposPromocao = validos.filter(registro => registro && registro.sim && registro.sim.trocaTipoAposPromocaoMl).length;
            const confirmouAlteracao = await perguntarConfirmacaoEfetivarFavoritos({
                total: validos.length,
                incluirOutrasContas,
                buscouAnunciosAoAprovar,
                nomeCampanha,
                totalTrocaTipo,
                totalTrocaAposPromocao
            });
            if (!confirmouAlteracao) return;

            favMlEfetivacaoEmExecucao = true;
            limparLogEfetivarFavoritos();
            adicionarStatusEfetivarFavoritos('info', 'Iniciando alteracoes no Mercado Livre', `${validos.length} anuncio(s) serao conferidos: sair da promocao atual, ajustar tipo Premium/Classico quando necessario, alterar preco cheio, aplicar campanha e validar o preco final promocional.`);
            if (favMlEfetivarBtnEl) {
                favMlEfetivarBtnEl.disabled = true;
                favMlEfetivarBtnEl.textContent = 'Alterando...';
            }
            const sucessos = [];
            const falhas = bloqueadosPreEnvio.map(item => ({
                itemId: item.itemId,
                erro: item.erro,
                registro: item.registro
            }));
            let protecaoElectronAtiva = false;
            try {
                bloqueadosPreEnvio.forEach(item => {
                    adicionarStatusEfetivarFavoritos(
                        'error',
                        `${item.itemId} nao foi enviado ao Mercado Livre`,
                        item.erro || 'Mercado Livre nao disponibiliza essa troca Premium/Classico para o anuncio agora.'
                    );
                });
                validos.filter(registro => registro && registro.sim && registro.sim.trocaTipoAposPromocaoMl).forEach(registro => {
                    adicionarStatusEfetivarFavoritos(
                        'info',
                        `${registro.itemId} tentara trocar tipo direto no Mercado Livre`,
                        `${registro.sim.trocaTipoAposPromocaoMotivo || 'Mercado Livre nao listou a troca em available_*.'} O sistema vai sair apenas da promocao ativa quando existir e tentar a troca Premium/Classico pelo endpoint oficial.`
                    );
                });
                protecaoElectronAtiva = await definirProtecaoAutomacaoMlFavoritos(true, 'favoritos-efetivar-preco');
                if (protecaoElectronAtiva) {
                    adicionarStatusEfetivarFavoritos('info', 'Protecao ativada', 'Atualizacoes automaticas serao adiadas ate terminar a alteracao no Mercado Livre.');
                }
                for (let i = 0; i < validos.length; i += 1) {
                    const registro = validos[i];
                    const itemId = registro.itemId;
                    mostrarBalaoFavoritosStatus(`Alterando ${i + 1}/${validos.length}: ${itemId}...`, { larga: true });
                    if (favMlEfetivarInfoEl) favMlEfetivarInfoEl.textContent = `Alterando ${i + 1}/${validos.length}: ${itemId}.`;
                    adicionarStatusEfetivarFavoritos(
                        'info',
                        `${i + 1}/${validos.length} - Enviando alteracao para ${itemId}`,
                        `Conta: ${registro.loja || '-'} | Tipo: ${textoTipoEnvioFavoritos(registro)} | Preco cheio: ${formatarPrecoFavoritosMl(registro.sim && registro.sim.preco)} | Preco final promocional: ${formatarPrecoFavoritosMl(registro.sim && (registro.sim.precoPromocionalCalculado ?? registro.sim.precoPromocional ?? registro.sim.precoCompetitivo))}${registro.sim && registro.sim.ajustePrecoUnico ? ` | Ajuste preco unico: +${formatarPrecoFavoritosMl(registro.sim.ajustePrecoUnico)}` : ''}`
                    );
                    try {
                        const opcoesLoja = await resolverOpcoesPromocaoEfetivacaoParaLoja(opcoesPromocao, registro.loja);
                        const data = await efetivarFavoritoMercadoLivre(
                            registro.anuncio,
                            registro.ranking,
                            registro.sim,
                            opcoesLoja,
                            null,
                            {
                                confirmar: false,
                                mostrarStatus: false,
                                atualizarStatus: false,
                                recarregar: false,
                                propagarErro: true
                            }
                        );
                        sucessos.push({ itemId, data, registro });
                        if (data && data.fallback_sem_promocao_aplicado) {
                            const margemFallback = data.margem_estimada_contingencia !== null && data.margem_estimada_contingencia !== undefined
                                ? ` | Margem estimada: ${formatarMargemAnuncioFavoritos(data.margem_estimada_contingencia)}`
                                : '';
                            adicionarStatusEfetivarFavoritos(
                                'info',
                                `${itemId} ajustado sem campanha (fallback)`,
                                `Campanha removida/recusada pelo ML ou margem abaixo de 15%. Preco aplicado sem campanha: ${formatarPrecoFavoritosMl(data.preco_anuncio)} | Referencia ranking: ${formatarPrecoFavoritosMl(data.preco_ranking_referencia)}${margemFallback} | Motivo: ${normalizarErroEfetivacaoFavoritos(data.fallback_motivo || '')}`
                            );
                    } else {
                        const tipoUpdate = data && data.listing_type_update;
                        const tipoTxt = tipoUpdate && tipoUpdate.target_name
                            ? ` | Tipo: ${tipoUpdate.current_name || '-'} -> ${tipoUpdate.target_name}${tipoUpdate.changed ? '' : ' (ja estava)'}`
                            : '';
                        adicionarStatusEfetivarFavoritos(
                            'success',
                            `${itemId} alterado e conferido`,
                            `Preco cheio aplicado: ${formatarPrecoFavoritosMl(data && data.preco_anuncio)} | Preco final promocional: ${formatarPrecoFavoritosMl(data && data.preco_promocional)} | Campanha: ${(data && (data.campanha_nome || data.promotion_id)) || '-'}${tipoTxt}`
                        );
                    }
                    } catch (err) {
                        const erroTxt = normalizarErroEfetivacaoFavoritos(err);
                        falhas.push({ itemId, erro: erroTxt, registro });
                        adicionarStatusEfetivarFavoritos(
                            'error',
                            `${itemId} nao foi alterado`,
                            erroTxt || 'O Mercado Livre recusou a alteracao sem detalhar o motivo.'
                        );
                    }
                }

                if (sucessos.length) {
                    await carregarFavoritosAnunciosSku(sku, favMlLojaSelecionada);
                }
                let mensagemFinalEfetivacao = '';
                const totalFallbackSemCampanha = sucessos.filter(item => item && item.data && item.data.fallback_sem_promocao_aplicado).length;
                if (falhas.length) {
                    const primeiraFalha = falhas[0];
                    const txtFallback = totalFallbackSemCampanha ? ` ${totalFallbackSemCampanha} concluido(s) em fallback sem campanha.` : '';
                    mensagemFinalEfetivacao = `${sucessos.length} favorito(s) feito(s). ${falhas.length} falharam.${txtFallback} Primeiro erro em ${primeiraFalha.itemId}: ${primeiraFalha.erro}`;
                    mostrarBalaoFavoritosStatus(mensagemFinalEfetivacao, {
                        erro: true,
                        larga: true
                    });
                    if (favMlStatusEl) favMlStatusEl.textContent = `${sucessos.length} favorito(s) feito(s); ${falhas.length} falha(s); ${totalFallbackSemCampanha} fallback(s) sem campanha.`;
                    adicionarStatusEfetivarFavoritos('error', 'Processo concluido com falhas', `Sucesso: ${sucessos.length}. Falhas: ${falhas.length}. Fallback sem campanha: ${totalFallbackSemCampanha}. Veja acima o motivo retornado pelo Mercado Livre para cada MLB.`);
                } else {
                    const txtFallback = totalFallbackSemCampanha ? ` (${totalFallbackSemCampanha} em fallback sem campanha)` : '';
                    mensagemFinalEfetivacao = `Tudo certo: ${sucessos.length} favorito(s) feito(s) no Mercado Livre${txtFallback}.`;
                    mostrarBalaoFavoritosStatus(mensagemFinalEfetivacao, {
                        tempoMs: 7500
                    });
                    if (favMlStatusEl) favMlStatusEl.textContent = `${sucessos.length} favorito(s) feito(s) no Mercado Livre${txtFallback}.`;
                    if (totalFallbackSemCampanha) {
                        adicionarStatusEfetivarFavoritos('info', 'Processo concluido', `${sucessos.length} anuncio(s) ajustado(s). ${totalFallbackSemCampanha} sem campanha (fallback por rejeicao da promocao).`);
                    } else {
                        adicionarStatusEfetivarFavoritos('success', 'Processo concluido', `${sucessos.length} anuncio(s) alterado(s), com promocao aplicada e conferida no Mercado Livre.`);
                    }
                }
                let historicoAlteracoesResultado = null;
                try {
                    const historicoAlteracoes = montarHistoricoAlteracoesFavoritosPayload({
                        sku,
                        opcoesPromocao,
                        incluirOutrasContas,
                        validos,
                        bloqueadosPreEnvio,
                        sucessos,
                        falhas,
                        mensagemFinal: mensagemFinalEfetivacao
                    });
                    historicoAlteracoesResultado = historicoAlteracoes;
                    const entradaHistorico = salvarHistoricoAlteracoesFavoritosProcesso(historicoAlteracoes);
                    if (entradaHistorico) {
                        adicionarStatusEfetivarFavoritos(
                            'info',
                            'Historico de favoritos salvo',
                            `${historicoAlteracoes.vinculos.length} vinculo(s) e relatorio(s) da alteracao foram salvos para consulta.`
                        );
                    }
                } catch (err) {
                    console.warn('Nao foi possivel salvar o historico estatico das alteracoes de favoritos:', err);
                    adicionarStatusEfetivarFavoritos(
                        'error',
                        'Historico de favoritos nao salvo',
                        err && err.message ? err.message : String(err || 'Falha desconhecida ao salvar historico.')
                    );
                }
                renderizarComparativoEfetivacaoFavoritos(sucessos, mensagemFinalEfetivacao, falhas, historicoAlteracoesResultado);
                agendarOcultarStatusEfetivarFavoritos(5000);
            } finally {
                if (protecaoElectronAtiva) {
                    await definirProtecaoAutomacaoMlFavoritos(false, 'favoritos-efetivar-preco');
                }
                favMlEfetivacaoEmExecucao = false;
                if (favMlEfetivarBtnEl) {
                    favMlEfetivarBtnEl.textContent = 'Aprovar e alterar';
                }
                atualizarPainelEfetivarFavoritos();
            }
        }
        function criarCelulaMargemAnuncioFavoritos(anuncio) {
            const td = document.createElement('td');
            td.className = 'ml-favoritos-margem-cell';
            const margem = parseMargemAnuncioFavoritos(anuncio && (anuncio.margem_percentual ?? anuncio.margem));
            const badge = document.createElement('span');
            badge.className = 'ml-favoritos-margem-badge';
            if (margem === null) {
                badge.classList.add('is-empty');
                badge.textContent = '-';
                const status = anuncio && anuncio.margem_status && anuncio.margem_status !== 'ok' ? anuncio.margem_status : 'Margem nao calculada para este anuncio.';
                td.title = status;
            } else {
                badge.classList.add(margem >= 0 ? 'is-positive' : 'is-negative');
                badge.textContent = formatarMargemAnuncioFavoritos(margem);
                const detalhes = [
                    anuncio && anuncio.valor_liquido_text ? `Liquido: ${anuncio.valor_liquido_text}` : '',
                    anuncio && anuncio.custo_text ? `Custo: ${anuncio.custo_text}` : '',
                    anuncio && anuncio.frete_ml_text ? `Frete: ${anuncio.frete_ml_text}` : '',
                    anuncio && anuncio.promotion_fee_base_text ? `Tarifa campanha usuario: ${anuncio.promotion_fee_base_text}` : '',
                    anuncio && anuncio.promotion_fee_ml_text ? `Tarifa campanha ML: ${anuncio.promotion_fee_ml_text}` : '',
                    anuncio && anuncio.ad_cost_original_text ? `Tarifa original: ${anuncio.ad_cost_original_text}` : '',
                    anuncio && anuncio.promotion_fee_discount_applied && anuncio.promotion_fee_discount_text ? `Desc. tarifa promo: ${anuncio.promotion_fee_discount_text}` : '',
                    anuncio && anuncio.tarifa_ml_text ? `Tarifa: ${anuncio.tarifa_ml_text}` : '',
                    anuncio && anuncio.imposto_valor_text ? `Imposto: ${anuncio.imposto_valor_text}` : '',
                ].filter(Boolean);
                td.title = detalhes.join(' | ');
            }
            td.appendChild(badge);
            return td;
        }
        function normalizarTipoAnuncioFavoritos(valor) {
            if (valor === null || valor === undefined || valor === '') return '';
            let bruto = valor;
            if (typeof bruto === 'object') {
                bruto = bruto.name || bruto.label || bruto.title || bruto.id || '';
            }
            const original = String(bruto || '').trim();
            if (!original) return '';
            if (original === '-') return '';
            const texto = normalizarNomeVendedorParaBusca(original);
            if (!texto) return original;
            const compacto = texto.replace(/\s+/g, '');
            if (texto.includes('premium') || texto.includes('gold pro') || texto === 'pro') return 'Premium';
            if (compacto.includes('classico') || compacto.includes('classic') || texto.includes('gold special') || texto === 'gold') return 'Classico';
            if (texto === 'free' || compacto.includes('gratis') || compacto.includes('gratuito')) return 'Gratis';
            return original;
        }
        function obterParcelamentoSemJurosFavoritos(anuncio) {
            if (!anuncio) return null;
            const camposBooleanos = [
                anuncio.parcelamento_sem_juros,
                anuncio.parcelamentoSemJuros,
                anuncio.installments_sem_juros,
                anuncio.installmentsSemJuros,
                anuncio.sem_juros,
                anuncio.semJuros,
                anuncio.juros_zero
            ];
            for (const valor of camposBooleanos) {
                if (valor === true || valor === false) return valor;
                if (typeof valor === 'number' && Number.isFinite(valor)) return valor !== 0;
                if (typeof valor === 'string') {
                    const texto = normalizarNomeVendedorParaBusca(valor);
                    if (['true', 'sim', 'yes', '1', 'sem juros', 'semjuros'].includes(texto)) return true;
                    if (['false', 'nao', 'não', 'no', '0', 'com juros'].includes(texto)) return false;
                }
            }
            const installments = anuncio.installments || anuncio.parcelamento || anuncio.installment || null;
            if (installments && typeof installments === 'object') {
                const rate = parsePrecoAnuncioFavoritos(installments.rate ?? installments.interest_rate ?? installments.interestRate ?? installments.juros ?? '');
                const quantity = Number(installments.quantity ?? installments.installments ?? installments.parcelas ?? 0);
                if (rate !== null) return rate === 0 && (!Number.isFinite(quantity) || quantity > 1);
                if (installments.no_interest === true || installments.noInterest === true || installments.sem_juros === true) return true;
            }
            const textoParcelamento = [
                anuncio.parcelamento_texto,
                anuncio.parcelamentoTexto,
                anuncio.installments_text,
                anuncio.installmentsText,
                anuncio.installments && anuncio.installments.text,
                anuncio.installments && anuncio.installments.description
            ].filter(Boolean).join(' ');
            if (textoParcelamento) {
                const texto = normalizarNomeVendedorParaBusca(textoParcelamento);
                if (texto.includes('sem juros')) return true;
                if (texto.includes('com juros')) return false;
            }
            return null;
        }
        function inferirTipoPorParcelamentoFavoritos(anuncio) {
            const semJuros = obterParcelamentoSemJurosFavoritos(anuncio);
            if (semJuros === true) return 'Premium';
            if (semJuros === false) return 'Classico';
            return '';
        }
        function obterTipoAnuncioFavoritos(anuncio) {
            if (!anuncio) return '';
            const tipoParcelamento = inferirTipoPorParcelamentoFavoritos(anuncio);
            if (tipoParcelamento) return tipoParcelamento;
            const candidatos = [
                anuncio.listing_type_id,
                anuncio.listingTypeId,
                anuncio.listing_type,
                anuncio.listingType,
                anuncio.tipo_anuncio,
                anuncio.tipoAnuncio,
                anuncio.tipo,
                anuncio.listing_type_name,
                anuncio.listingTypeName
            ];
            for (const candidato of candidatos) {
                const tipo = normalizarTipoAnuncioFavoritos(candidato);
                if (tipo) return tipo;
            }
            return '';
        }
        function listingTypeIdFavoritos(valor) {
            if (!valor) return '';
            let bruto = valor;
            if (typeof bruto === 'object') {
                bruto = bruto.id || bruto.name || bruto.label || bruto.title || '';
            }
            const texto = normalizarNomeVendedorParaBusca(bruto);
            const compacto = texto.replace(/\s+/g, '');
            if (!texto) return '';
            if (texto.includes('gold pro') || texto.includes('gold_pro') || compacto.includes('goldpro') || texto.includes('premium') || texto === 'pro') return 'gold_pro';
            if (texto.includes('gold special') || texto.includes('gold_special') || compacto.includes('goldspecial') || compacto.includes('classico') || compacto.includes('classic') || texto === 'gold') return 'gold_special';
            if (texto === 'free' || compacto.includes('gratis') || compacto.includes('gratuito')) return 'free';
            return '';
        }
        function nomeTipoPorListingTypeFavoritos(listingTypeId) {
            const id = listingTypeIdFavoritos(listingTypeId);
            if (id === 'gold_pro') return 'Premium';
            if (id === 'gold_special') return 'Classico';
            if (id === 'free') return 'Gratis';
            return '';
        }
        function taxaPadraoListingTypeFavoritos(listingTypeId) {
            const id = listingTypeIdFavoritos(listingTypeId);
            if (id === 'gold_pro') return 0.17;
            if (id === 'gold_special') return 0.12;
            if (id === 'free') return 0;
            return null;
        }
        function obterListingTypeIdAnuncioFavoritos(anuncio) {
            if (!anuncio) return '';
            const candidatos = [
                anuncio.listing_type_id,
                anuncio.listingTypeId,
                anuncio.listing_type && anuncio.listing_type.id,
                anuncio.listing_type,
                anuncio.listingType,
                anuncio.tipo_anuncio,
                anuncio.tipoAnuncio,
                anuncio.tipo,
                anuncio.listing_type_name,
                anuncio.listingTypeName,
                obterTipoAnuncioFavoritos(anuncio)
            ];
            for (const candidato of candidatos) {
                const id = listingTypeIdFavoritos(candidato);
                if (id) return id;
            }
            return '';
        }
        function resolverTrocaTipoAnuncioFavoritos(anuncioConta, anuncioRanking, opcoes = {}) {
            const listingTypeIdAlvoRaw = obterListingTypeIdAnuncioFavoritos(anuncioRanking);
            const listingTypeIdAlvo = ['gold_pro', 'gold_special'].includes(listingTypeIdAlvoRaw) ? listingTypeIdAlvoRaw : '';
            const listingTypeIdAtual = obterListingTypeIdAnuncioFavoritos(anuncioConta);
            const tipoAtual = nomeTipoPorListingTypeFavoritos(listingTypeIdAtual) || obterTipoAnuncioFavoritos(anuncioConta);
            if (opcoes && opcoes.manterTipoAtual) {
                return {
                    tipoAtual,
                    tipoAlvo: tipoAtual,
                    listingTypeIdAtual,
                    listingTypeIdAlvo: listingTypeIdAtual,
                    trocar: false,
                    taxaPadraoAlvo: null,
                    usarTaxaPadraoAlvo: false
                };
            }
            const tipoAlvo = nomeTipoPorListingTypeFavoritos(listingTypeIdAlvo) || obterTipoAnuncioFavoritos(anuncioRanking);
            const taxaPadraoAlvo = taxaPadraoListingTypeFavoritos(listingTypeIdAlvo);
            const trocar = !!listingTypeIdAlvo && (!listingTypeIdAtual || listingTypeIdAtual !== listingTypeIdAlvo);
            return {
                tipoAtual,
                tipoAlvo,
                listingTypeIdAtual,
                listingTypeIdAlvo,
                trocar,
                taxaPadraoAlvo,
                usarTaxaPadraoAlvo: !!listingTypeIdAlvo && taxaPadraoAlvo !== null && (!listingTypeIdAtual || listingTypeIdAtual !== listingTypeIdAlvo)
            };
        }
        function valorIndicaFullFavoritos(valor) {
            if (valor === true) return true;
            if (valor === false || valor === null || valor === undefined) return false;
            if (Array.isArray(valor)) return valor.some(item => valorIndicaFullFavoritos(item));
            if (typeof valor === 'object') {
                return valorIndicaFullFavoritos(valor.logistic_type)
                    || valorIndicaFullFavoritos(valor.logisticType)
                    || valorIndicaFullFavoritos(valor.tipo_logistica)
                    || valorIndicaFullFavoritos(valor.tags)
                    || valor.full === true
                    || valor.is_full === true
                    || valor.fulfillment === true;
            }
            const texto = normalizarNomeVendedorParaBusca(valor);
            return texto === 'full'
                || texto === 'mercado livre full'
                || texto === 'mercadolivre full'
                || texto === 'fulfillment'
                || texto.includes(' logistic type fulfillment ')
                || texto.includes(' mercado livre full ');
        }
        function temIndicadorFullFavoritos(anuncio) {
            if (!anuncio) return false;
            const campos = [
                'full', 'is_full', 'isFull', 'meli_full', 'mercado_livre_full',
                'fulfillment', 'envio_full', 'logistic_type', 'logisticType',
                'shipping_logistic_type', 'shippingLogisticType', 'tipo_logistica'
            ];
            if (campos.some(campo => anuncio[campo] !== null && anuncio[campo] !== undefined && anuncio[campo] !== '')) return true;
            const shipping = anuncio.shipping || anuncio.shipping_info || anuncio.shippingInfo || anuncio.envio || null;
            if (shipping && typeof shipping === 'object') {
                return ['logistic_type', 'logisticType', 'tipo_logistica', 'tags', 'full', 'is_full', 'fulfillment']
                    .some(campo => shipping[campo] !== null && shipping[campo] !== undefined && shipping[campo] !== '');
            }
            return false;
        }
        function obterFullAnuncioFavoritos(anuncio) {
            if (!anuncio) return false;
            return valorIndicaFullFavoritos([
                anuncio.full,
                anuncio.is_full,
                anuncio.isFull,
                anuncio.meli_full,
                anuncio.mercado_livre_full,
                anuncio.fulfillment,
                anuncio.envio_full,
                anuncio.logistic_type,
                anuncio.logisticType,
                anuncio.shipping_logistic_type,
                anuncio.shippingLogisticType,
                anuncio.tipo_logistica,
                anuncio.shipping,
                anuncio.shipping_info,
                anuncio.shippingInfo,
                anuncio.envio
            ]);
        }
        function fullAnuncioDesconhecidoFavoritos(anuncio) {
            return !!(anuncio && !temIndicadorFullFavoritos(anuncio) && !anuncio._fullAnuncioVerificado);
        }
        function preencherFullAnuncioFavoritos(alvo, fonte) {
            if (!alvo || !fonte) return false;
            let alterou = false;
            if (obterFullAnuncioFavoritos(fonte) && !obterFullAnuncioFavoritos(alvo)) {
                alvo.is_full = true;
                alvo.full = true;
                alterou = true;
            }
            const campos = ['full', 'is_full', 'isFull', 'meli_full', 'mercado_livre_full', 'fulfillment', 'envio_full', 'logistic_type', 'logisticType', 'shipping_logistic_type', 'shippingLogisticType', 'tipo_logistica', 'shipping', 'shipping_info', 'shippingInfo', 'envio'];
            campos.forEach(campo => {
                if ((alvo[campo] === null || alvo[campo] === undefined || alvo[campo] === '') && fonte[campo] !== null && fonte[campo] !== undefined && fonte[campo] !== '') {
                    alvo[campo] = fonte[campo];
                    alterou = true;
                }
            });
            if (temIndicadorFullFavoritos(fonte)) alvo._fullAnuncioVerificado = true;
            return alterou;
        }
        function obterTipoCompletoAnuncioFavoritos(anuncio) {
            const tipo = obterTipoAnuncioFavoritos(anuncio);
            const full = obterFullAnuncioFavoritos(anuncio);
            return [tipo, full ? 'Full' : ''].filter(Boolean).join(' ');
        }
        function preencherTipoAnuncioFavoritos(alvo, fonte) {
            if (!alvo || !fonte) return false;
            let alterou = false;
            if (preencherFullAnuncioFavoritos(alvo, fonte)) alterou = true;
            const semJuros = obterParcelamentoSemJurosFavoritos(fonte);
            if (semJuros !== null && obterParcelamentoSemJurosFavoritos(alvo) === null) {
                alvo.parcelamento_sem_juros = semJuros;
                alterou = true;
            }
            const tipo = obterTipoAnuncioFavoritos(fonte);
            if (tipo && !obterTipoAnuncioFavoritos(alvo)) {
                alvo.tipo_anuncio = tipo;
                alvo.listing_type_name = tipo;
                alterou = true;
            }
            const tipoId = fonte.listing_type_id || fonte.listingTypeId || (fonte.listing_type && fonte.listing_type.id) || '';
            if (tipoId && !alvo.listing_type_id) {
                alvo.listing_type_id = tipoId;
                alterou = true;
            }
            return alterou;
        }
        function criarCelulaTipoAnuncioFavoritos(anuncio, vendedor = '') {
            const td = document.createElement('td');
            td.className = 'ml-favoritos-type-cell';
            const tipo = document.createElement('span');
            tipo.className = 'ml-favoritos-type-value';
            tipo.textContent = obterTipoCompletoAnuncioFavoritos(anuncio);
            td.appendChild(tipo);
            const botaoVendedor = criarBotaoIgnorarVendedorFavoritos(vendedor);
            if (botaoVendedor) {
                const action = document.createElement('div');
                action.className = 'ml-favoritos-seller-action';
                action.appendChild(botaoVendedor);
                td.appendChild(action);
            }
            return td;
        }

        function criarCelulaMediaHistoricoFavoritos(anuncio) {
            const td = document.createElement('td');
            td.className = 'ml-favoritos-media-cell';

            const media = document.createElement('span');
            media.className = 'ml-favoritos-media-main';
            media.textContent = formatarMediaVendas(anuncio) || '-';
            td.appendChild(media);

            const vendas = formatarQuantidadeVendidaHistoricoFavoritos(anuncio);
            if (vendas) {
                const vendasEl = document.createElement('span');
                vendasEl.className = 'ml-favoritos-media-detail';
                vendasEl.textContent = `Vendas: ${vendas}`;
                td.appendChild(vendasEl);
            }

            const dias = formatarDiasAnuncio(anuncio);
            if (dias) {
                const diasEl = document.createElement('span');
                diasEl.className = 'ml-favoritos-media-detail';
                diasEl.textContent = `Dias: ${dias}`;
                td.appendChild(diasEl);
            }

            const precos = obterPrecosAnuncioFavoritos(anuncio);
            if (precos.preco !== null || precos.promocional !== null) {
                const temPromocional = precos.promocional !== null && precos.preco !== null;
                const precoBase = precos.preco !== null ? precos.preco : precos.promocional;
                const precoEl = document.createElement('span');
                precoEl.className = `ml-favoritos-media-price${temPromocional ? ' is-original' : ''}`;
                precoEl.textContent = `Preco: ${formatarPrecoFavoritosMl(precoBase)}`;
                td.appendChild(precoEl);

                if (temPromocional) {
                    const promoEl = document.createElement('span');
                    promoEl.className = 'ml-favoritos-media-price';
                    promoEl.textContent = formatarPrecoFavoritosMl(precos.promocional);
                    const descontoTexto = formatarDescontoPrecoFavoritos(precos.desconto);
                    if (descontoTexto) {
                        const descontoEl = document.createElement('span');
                        descontoEl.className = 'ml-favoritos-media-discount';
                        descontoEl.textContent = descontoTexto;
                        promoEl.appendChild(descontoEl);
                    }
                    td.appendChild(promoEl);
                }
            }

            return td;
        }

        function formatarQuantidadeVendidaHistoricoFavoritos(anuncio) {
            const vendasAvant = parseVendasAvantPro(anuncio);
            if (vendasAvant !== null) return String(vendasAvant);
            const vendas = parseNumeroVendas(anuncio && anuncio.vendas);
            return Number.isFinite(vendas) ? String(vendas) : '';
        }
        const normalizarChaveVendedor = (valor) => normalizarNomeVendedorParaBusca(valor);
        const deveAtualizarVendedor = (atual, fonteAtual, novo, fonteNova) => {
            if (!hasTexto(novo)) {
                return false;
            }
            if (!vendedorValido(novo)) {
                return false;
            }
            if (!hasTexto(atual)) {
                return true;
            }
            if (!vendedorValido(atual)) {
                return true;
            }
            const atualTexto = normalizarChaveVendedor(atual);
            const novoTexto = normalizarChaveVendedor(novo);
            if (atualTexto === novoTexto) {
                return false;
            }
            const pesoAtual = pesoFonteVendedor(fonteAtual);
            const pesoNovo = pesoFonteVendedor(fonteNova);
            if (pesoNovo > pesoAtual) return true;
            if (pesoNovo < pesoAtual) return false;
            const scoreAtual = scoreNomeVendedor(atual);
            const scoreNovo = scoreNomeVendedor(novo);
            return scoreNovo > scoreAtual;
        };
        const deveAtualizarVendas = (atual, fonteAtual, novo, fonteNova) => {
            const atualNumero = parseNumeroVendas(atual);
            const novoNumero = parseNumeroVendas(novo);
            if (!hasNumeroVendas(novoNumero)) return false;
            if (!fonteVendasConfiavel(fonteNova)) return false;
            if (!hasNumeroVendas(atualNumero)) return true;
            if (fonteVendasAvantPro(fonteAtual) && !fonteVendasAvantPro(fonteNova)) return false;
            const pesoAtual = pesoFonteVendas(fonteAtual);
            const pesoNovo = pesoFonteVendas(fonteNova);
            return pesoNovo > pesoAtual || (pesoNovo === pesoAtual && atualNumero !== novoNumero);
        };
