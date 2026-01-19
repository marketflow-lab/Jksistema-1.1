import streamlit as st
import pandas as pd
from io import BytesIO

class FullManager:
    """
    Módulo avançado de gestão Full para Trend Minas.
    Funcionalidades:
    1. Processamento de Estoque (limpeza de headers complexos).
    2. Processamento de Devoluções.
    3. Cruzamento inteligente para identificar Risco de Retorno.
    """

    def __init__(self):
        pass

    def _load_csv_dynamic_header(self, file_buffer, anchor_col):
        """
        Lê o CSV procurando dinamicamente a linha de cabeçalho correta
        baseada em uma coluna âncora (ex: 'Código ML').
        """
        try:
            file_buffer.seek(0)
            # Tenta ler as primeiras linhas para achar o header
            df_raw = pd.read_csv(file_buffer, header=None, nrows=20, encoding='utf-8')
            header_idx = -1
            
            for i, row in df_raw.iterrows():
                # Concatena a linha inteira como uma string para busca
                row_str = ' '.join(row.astype(str).fillna('').tolist())
                if anchor_col in row_str:
                    header_idx = i
                    break
            
            file_buffer.seek(0) # Reseta o buffer para a leitura completa
            if header_idx != -1:
                return pd.read_csv(file_buffer, skiprows=header_idx, encoding='utf-8')
            
            # Fallback se não encontrar o âncora
            file_buffer.seek(0)
            return pd.read_csv(file_buffer, encoding='utf-8')
            
        except Exception as e:
            st.error(f"Erro ao carregar CSV: {e}")
            return pd.DataFrame()

    def get_stock_risk_analysis(self, file_stock, file_returns):
        """
        Gera o relatório unificado de Risco.
        Retorna um DataFrame com SKUs que têm estoque E histórico de devolução.
        """
        # 1. Carregar e processar Estoque
        df_stock = self._load_csv_dynamic_header(file_stock, "Código ML")
        if df_stock.empty:
            st.warning("DataFrame de estoque vazio ou não pôde ser carregado.")
            return pd.DataFrame()
        
        stock_rename_map = {}
        for c in df_stock.columns:
            col_str = str(c)
            if "Código ML" in col_str: stock_rename_map[c] = 'sku'
            if "Produto" in col_str: stock_rename_map[c] = 'titulo'
            if "Vendas últimos 30" in col_str: stock_rename_map[c] = 'vendas'
            if "Aptas para venda" in col_str: stock_rename_map[c] = 'estoque_apto'
        
        df_stock = df_stock.rename(columns=stock_rename_map)

        required_stock_cols = ['sku', 'titulo', 'estoque_apto', 'vendas']
        if not all(col in df_stock.columns for col in required_stock_cols):
            st.error(f"Colunas essenciais não encontradas no arquivo de estoque. Esperado: {required_stock_cols}, Encontrado: {list(df_stock.columns)}")
            return pd.DataFrame()

        df_stock = df_stock.dropna(subset=['sku'])
        for col in ['vendas', 'estoque_apto']:
            df_stock[col] = pd.to_numeric(df_stock[col], errors='coerce').fillna(0).astype(int)

        # 2. Carregar e processar Devoluções
        df_returns = self._load_csv_dynamic_header(file_returns, "N.º da ordem")
        if df_returns.empty:
            st.warning("DataFrame de devoluções vazio ou não pôde ser carregado.")
            return pd.DataFrame()
        
        if 'SKU' in df_returns.columns:
            df_returns = df_returns.rename(columns={'SKU': 'sku'})
        
        if 'sku' not in df_returns.columns:
            st.error("Coluna 'SKU' não encontrada no arquivo de devoluções.")
            return pd.DataFrame()

        returns_agg = df_returns.dropna(subset=['sku']).groupby('sku').size().reset_index(name='qtd_devolucoes')

        # 3. Merge e Análise de Risco
        df_merged = pd.merge(df_stock[required_stock_cols], returns_agg, on='sku', how='left')
        df_merged['qtd_devolucoes'] = df_merged['qtd_devolucoes'].fillna(0).astype(int)
        
        def classificar_risco(row):
            if row['qtd_devolucoes'] == 0: return 'Seguro'
            if row['qtd_devolucoes'] >= 3: return 'CRÍTICO'
            if row['vendas'] == 0 and row['qtd_devolucoes'] > 0: return 'Alto (Sem Vendas)'
            return 'Atenção'
        df_merged['Status Risco'] = df_merged.apply(classificar_risco, axis=1)
        
        df_final = df_merged[df_merged['estoque_apto'] > 0].sort_values(by=['qtd_devolucoes', 'vendas'], ascending=[False, True])
        
        # Adiciona o título de volta para exibição
        df_final = pd.merge(df_final, df_stock[['sku', 'titulo']], on='sku', how='left')
        
        return df_final[['sku', 'titulo', 'estoque_apto', 'vendas', 'qtd_devolucoes', 'Status Risco']]

def render_page(navegar_para):
    """Renderiza a página de Análise de Risco do Full."""
    if st.button("⬅️ Voltar ao Menu"):
        navegar_para('menu')

    st.title("🔎 Análise de Risco de Devolução (Full)")
    st.markdown("---")

    st.info("Faça o upload dos relatórios CSV do Mercado Livre para análise de risco de devolução.")
    
    col1, col2 = st.columns(2)
    uploaded_stock = col1.file_uploader("1. Relatório de Estoque Full (Resumo)", type="csv")
    uploaded_returns = col2.file_uploader("2. Relatório de Devoluções (Triages)", type="csv")

    if uploaded_stock is not None and uploaded_returns is not None:
        manager = FullManager()
        
        stock_buffer = BytesIO(uploaded_stock.getvalue())
        returns_buffer = BytesIO(uploaded_returns.getvalue())
        
        with st.spinner("Processando arquivos e analisando dados..."):
            df_risco = manager.get_stock_risk_analysis(stock_buffer, returns_buffer)

        if not df_risco.empty:
            st.subheader("⚠️ Radar de Devoluções")
            df_problemas = df_risco[df_risco['Status Risco'] != 'Seguro']
            
            if df_problemas.empty:
                st.success("🎉 Ótima notícia! Nenhum produto em estoque apresenta risco de devolução com base no histórico fornecido.")
            else:
                st.warning(f"Foram encontrados {len(df_problemas)} SKUs em estoque com histórico de devolução.")

                def highlight_risk(row):
                    style = ''
                    risco = row['Status Risco']
                    if risco == 'CRÍTICO':
                        style = 'background-color: #8B0000; color: white' # Vermelho Escuro
                    elif 'Alto' in risco:
                        style = 'background-color: #FF8C00; color: white' # Laranja Escuro
                    elif risco == 'Atenção':
                        style = 'background-color: #BDB76B; color: black' # Amarelo Escuro
                    return [style] * len(row)
                
                st.dataframe(df_problemas.style.apply(highlight_risk, axis=1), use_container_width=True)
        else:
            st.error("A análise não pôde ser concluída. Verifique as mensagens de erro acima e os arquivos enviados.")

