
def calcular_vendas_mensais(access_token, produtos):
    all_sales = []
    
    # Placeholder para mostrar progresso
    status_text = st.empty()

    for i, produto in enumerate(produtos):
        id_produto = produto['id_bling']
        sku = produto['sku']
        status_text.text(f"Processando produto {i+1}/{len(produtos)}: {sku}")

        lotes = buscar_lotes_produto_bling(access_token, id_produto)
        for lote in lotes:
            id_lote = lote.get("idLote")
            if not id_lote:
                continue

            lancamentos = buscar_lancamentos_lote_bling(access_token, id_lote)
            for lancamento in lancamentos:
                # Assuming that a 'tipoLancamento' of 2 means a sale
                if lancamento.get('tipoLancamento') == 2:
                    data = lancamento.get('data')
                    quantidade = lancamento.get('quantidade', 0)
                    
                    if data and quantidade > 0:
                        all_sales.append({
                            "sku": sku,
                            "data": data,
                            "quantidade": quantidade
                        })

    status_text.empty()
    if not all_sales:
        return pd.DataFrame()

    df_sales = pd.DataFrame(all_sales)
    df_sales['data'] = pd.to_datetime(df_sales['data'])
    df_sales['mes_ano'] = df_sales['data'].dt.to_period('M').astype(str)
    
    vendas_mensais = df_sales.groupby(['sku', 'mes_ano'])['quantidade'].sum().reset_index()
    return vendas_mensais
