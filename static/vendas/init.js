// Carregar lojas e vendas salvas automaticamente
carregarPreferenciaGrafico();
agendarSegundoPlano(() => iaRestaurarOuCriarConversa().catch(() => {}), 900);
aplicarPreferenciaGraficoUI();
carregarLojas()
    .then(async () => {
        if (retornoDoSku) {
            statusEl.className = 'status-bar loading';
            statusEl.innerHTML = `${spinnerHtml}Retornando da visão SKU...`;
            setTimeout(async () => {
                try {
                    const restaurouCache = await restaurarCacheTelaVendasSeValido();
                    if (restaurouCache) {
                        agendarSegundoPlano(() => carregarGrafico(), 250);
                        return;
                    }
                    const syncAtivo = await verificarSyncEmAndamentoNaEntrada();
                    if (!syncAtivo && getDataIniISO() && getDataFimISO()) {
                        await carregarVendas({ retornoRapido: true });
                        agendarSegundoPlano(() => carregarGrafico(), 300);
                    }
                    if (!syncEmAndamento) {
                        statusEl.className = 'status-bar';
                        statusEl.textContent = '';
                    }
                } catch (_e) {
                    statusEl.className = 'status-bar';
                    statusEl.textContent = 'Retorno concluído. Clique em Atualizar período para recarregar os dados.';
                }
            }, 180);
            return null;
        }

        await verificarSyncEmAndamentoNaEntrada();
        if (getDataIniISO() && getDataFimISO()) {
            return carregarVendas().then(() => agendarSegundoPlano(() => carregarGrafico(), 300));
        }
        statusEl.className = 'status-bar';
        statusEl.textContent = '';
        return null;
    })
    .catch(e => console.error('Erro ao carregar dados:', e));
