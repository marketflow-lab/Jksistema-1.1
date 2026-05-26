import streamlit as st
import pandas as pd
import json
import os
import requests
import base64
import time
import sqlite3
from datetime import datetime, timedelta
import plotly.express as px

# ==============================================================================
# --- CONFIGURAÇÕES E ARQUIVOS ---
# ==============================================================================
PASTA_INFO = "info"
os.makedirs(PASTA_INFO, exist_ok=True)

ARQUIVO_INTEGRACOES = os.path.join(PASTA_INFO, "integracoes.json")
ARQUIVO_DB_VENDAS = os.path.join(PASTA_INFO, "vendas_historico.db")

# ==============================================================================
# --- BANCO DE DADOS (SQLITE) ---
# ==============================================================================
def get_db_connection():
    return sqlite3.connect(ARQUIVO_DB_VENDAS)

def inicializar_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS vendas (
            id_unico TEXT PRIMARY KEY, data DATE, loja_conta TEXT, canal TEXT,
            numero TEXT, situacao TEXT, sku TEXT, produto TEXT,
            quantidade REAL, valor REAL, mes_ano TEXT
        )
    ''')
    conn.commit()
    conn.close()

def salvar_vendas_db(df_novas):
    if df_novas.empty: return
    conn = get_db_connection()
    try:
        df_novas.to_sql('vendas', conn, if_exists='append', index=False, chunksize=500,
                        method=lambda table, conn, keys, data_iter: 
                        conn.executemany(f"INSERT OR REPLACE INTO {table.name} ({', '.join(keys)}) VALUES ({', '.join(['?'] * len(keys))})", data_iter))
    finally:
        conn.close()

def carregar_vendas_db():
    if not os.path.exists(ARQUIVO_DB_VENDAS): return pd.DataFrame()
    conn = get_db_connection()
    try:
        df = pd.read_sql("SELECT * FROM vendas", conn, parse_dates=['data'])
    except Exception as e:
        st.error(f"Erro ao ler banco de dados de vendas: {e}")
        df = pd.DataFrame()
    finally:
        conn.close()
    return df

inicializar_db()

# ==============================================================================
# --- LÓGICA DE AUTENTICAÇÃO E CONFIGURAÇÃO CENTRALIZADA ---
# ==============================================================================
def carregar_integracoes_json():
    if os.path.exists(ARQUIVO_INTEGRACOES):
        try:
            with open(ARQUIVO_INTEGRACOES, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            st.error(f"Erro ao ler 'integracoes.json': {e}")
            return {}
    return {}

def salvar_integracoes_json(dados):
    try:
        with open(ARQUIVO_INTEGRACOES, "w", encoding="utf-8") as f:
            json.dump(dados, f, indent=4, ensure_ascii=False)
        return True
    except IOError as e:
        st.error(f"Erro ao salvar 'integracoes.json': {e}")
        return False

def carregar_contas():
    contas_formatadas = []
    dados_integracao = carregar_integracoes_json()
    for nome_loja, config_loja in dados_integracao.items():
        bling_config = config_loja.get("Bling")
        if bling_config and bling_config.get("status"):
            contas_formatadas.append({
                "loja": nome_loja, "id": bling_config.get("client_id"),
                "secret": bling_config.get("client_secret"),
                "access_token": bling_config.get("access_token"),
                "refresh_token": bling_config.get("refresh_token"),
            })
    return contas_formatadas

def obter_headers_auth(client_id, client_secret):
    credential = f"{client_id}:{client_secret}"
    cred_b64 = base64.b64encode(credential.encode()).decode()
    return {"Authorization": f"Basic {cred_b64}", "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}

def refresh_token(conta):
    url = "https://www.bling.com.br/Api/v3/oauth/token"
    payload = {"grant_type": "refresh_token", "refresh_token": conta.get("refresh_token")}
    headers = obter_headers_auth(conta.get("id"), conta.get("secret"))
    try:
        res = requests.post(url, headers=headers, data=payload, timeout=20)
        if res.status_code == 200:
            novos_tokens = res.json()
            dados_integracao = carregar_integracoes_json()
            if conta['loja'] in dados_integracao:
                dados_integracao[conta['loja']]['Bling']['access_token'] = novos_tokens["access_token"]
                dados_integracao[conta['loja']]['Bling']['refresh_token'] = novos_tokens["refresh_token"]
                salvar_integracoes_json(dados_integracao)
            return novos_tokens["access_token"]
        else:
            dados_integracao = carregar_integracoes_json()
            if conta['loja'] in dados_integracao:
                dados_integracao[conta['loja']]['Bling']['status'] = False
                salvar_integracoes_json(dados_integracao)
            st.error(f"Refresh token falhou para {conta['loja']}. Status: {res.status_code}. A loja foi desconectada.")
            st.rerun()
    except requests.RequestException as e:
        st.error(f"Erro de conexão ao atualizar token para {conta['loja']}: {e}")
    return None

# ==============================================================================
# --- API BLING V3 - BUSCA DETALHADA ---
# ==============================================================================
def buscar_mapa_lojas(token):
    url = "https://api.bling.com.br/Api/v3/configuracoes/lojas-virtuais"
    headers = {"Authorization": f"Bearer {token}"}
    mapa = {}
    try:
        res = requests.get(url, headers=headers, params={"situacao": 1}, timeout=10)
        if res.status_code == 200:
            for loja in res.json().get("data", []):
                mapa[str(loja.get("id"))] = loja.get("descricao", "Loja Virtual")
    except requests.RequestException: pass
    return mapa

def buscar_vendas_v3(conta, dias=None, data_ini=None, data_fim=None):
    token = refresh_token(conta)
    if not token: 
        st.error(f"Falha ao obter token para a conta {conta.get('loja')}. Verifique a conexão.")
        return []

    mapa_lojas = buscar_mapa_lojas(token)
    
    if not data_ini or not data_fim:
        hoje = datetime.now()
        dias = dias if dias is not None else 30
        data_ini = (hoje - timedelta(days=dias)).strftime("%Y-%m-%d")
        data_fim = hoje.strftime("%Y-%m-%d")

    url_lista = "https://api.bling.com.br/Api/v3/pedidos/vendas"
    headers = {"Authorization": f"Bearer {token}"}
    
    lista_vendas_processadas = []
    pagina = 1
    status_text = st.empty()
    
    try:
        while True:
            params = {"dataInicial": data_ini, "dataFinal": data_fim, "pagina": pagina, "limite": 100}
            res = requests.get(url_lista, headers=headers, params=params, timeout=20)
            
            if res.status_code == 429:
                st.warning("Rate limit da API atingido. Aguardando 3 segundos...")
                time.sleep(3); continue
            if res.status_code != 200:
                st.error(f"Erro na API do Bling (Página {pagina}): Status {res.status_code} - {res.text[:500]}")
                break
            
            pedidos_resumo = res.json().get("data", [])
            if not pedidos_resumo: break
            
            total_pag = len(pedidos_resumo)
            for idx, p_resumo in enumerate(pedidos_resumo):
                id_pedido = p_resumo.get("id")
                status_text.text(f"Loja: {conta.get('loja')} | Pg {pagina} | Pedido {idx+1}/{total_pag}...")
                
                url_detalhe = f"https://api.bling.com.br/Api/v3/pedidos/vendas/{id_pedido}"
                try:
                    res_det = requests.get(url_detalhe, headers=headers, timeout=10)
                    if res_det.status_code == 200:
                        p_completo = res_det.json().get("data", {})
                        canal = mapa_lojas.get(str(p_completo.get("loja", {}).get("id", "")), "Balcão/Painel")
                        for item in p_completo.get("itens", []):
                            lista_vendas_processadas.append({
                                "data": p_completo.get("data"), "loja_conta": conta.get("loja"), "canal": canal,
                                "numero": p_completo.get("numeroPedidoLoja") or str(p_completo.get("numero")),
                                "situacao": p_completo.get("situacao", {}).get("nome", "-"),
                                "sku": item.get("codigo") or "N/D",
                                "produto": item.get("descricao") or "Produto s/ descrição",
                                "quantidade": float(item.get("quantidade", 0)),
                                "valor": float(item.get("valor", 0)) * float(item.get("quantidade", 0))
                            })
                    time.sleep(0.35)
                except requests.RequestException as e_detail:
                    st.warning(f"Não foi possível detalhar o pedido {id_pedido}. Erro: {e_detail}")
            pagina += 1
            if pagina > 200: 
                st.warning("Limite de segurança de 200 páginas atingido."); break
    except requests.RequestException as e:
        st.error(f"Ocorreu um erro de conexão ao buscar vendas: {e}")
        
    status_text.empty()
    return lista_vendas_processadas

def processar_dataframe(df_raw):
    df = pd.DataFrame(df_raw)
    if df.empty: return df
    df['data'] = pd.to_datetime(df['data'], errors='coerce')
    df.dropna(subset=['data'], inplace=True)
    df['mes_ano'] = df['data'].dt.to_period('M').astype(str)
    df['id_unico'] = df['loja_conta'] + '_' + df['numero'].astype(str) + '_' + df['sku'].astype(str)
    return df

# ==============================================================================
# --- RENDERIZAÇÃO DA PÁGINA ---
# ==============================================================================
def render_page(navegar_para):
    if "filtro_conta_selecionada" not in st.session_state:
        st.session_state["filtro_conta_selecionada"] = None

    if st.button("⬅️ Voltar ao Menu"):
        navegar_para('menu')

    st.markdown("## 💲 Histórico de Vendas (Detalhado)")
    st.markdown("---")
    
    c_main, c_side = st.columns([3, 1])

    # SIDEBAR
    contas = carregar_contas()
    with c_side:
        with st.container(border=True):
            st.markdown("### Lojas Conectadas")
            if not contas:
                st.warning("Nenhuma loja Bling conectada. Vá para 'Integrações'.")
            else:
                if st.button("👀 Ver Todas as Lojas", use_container_width=True):
                    st.session_state["filtro_conta_selecionada"] = None
                    st.rerun()
                st.markdown("---")
                for c in contas:
                    if st.button(f"📂 {c.get('loja', 'Loja')}", use_container_width=True):
                        st.session_state["filtro_conta_selecionada"] = c.get('loja')
                        st.rerun()
            
            st.markdown("---")
            if st.button("⚙️ Ir para Integrações"):
                navegar_para('integracao')

            if st.session_state["filtro_conta_selecionada"]:
                st.info(f"Filtro ativo: **{st.session_state['filtro_conta_selecionada']}**")

    # MAIN
    with c_main:
        st.markdown("##### 1. Baixar Vendas para o Banco de Dados Local")
        with st.container(border=True):
            d_col1, d_col2 = st.columns(2)
            data_inicio = d_col1.date_input("Data Início", value=datetime.now() - timedelta(days=30))
            data_fim = d_col2.date_input("Data Fim", value=datetime.now())

            if st.button("⬇️ BUSCAR E SALVAR NO PERÍODO", type="primary", use_container_width=True):
                if not contas: 
                    st.error("Nenhuma conta Bling conectada.")
                elif data_inicio > data_fim:
                    st.error("A data de início não pode ser posterior à data de fim.")
                else:
                    bar = st.progress(0, "Iniciando busca...")
                    total_salvo = 0
                    
                    data_ini_str = data_inicio.strftime("%Y-%m-%d")
                    data_fim_str = data_fim.strftime("%Y-%m-%d")

                    for i, c in enumerate(contas):
                        vendas_conta = buscar_vendas_v3(c, data_ini=data_ini_str, data_fim=data_fim_str)
                        if vendas_conta:
                            df_novas = processar_dataframe(vendas_conta)
                            salvar_vendas_db(df_novas)
                            total_salvo += len(df_novas)
                            st.toast(f"{c.get('loja')}: {len(df_novas)} registros salvos.", icon="✅")
                        bar.progress((i+1)/len(contas))
                    
                    bar.empty()
                    st.success(f"Busca concluída! {total_salvo} registros totais salvos no banco de dados.")
                    time.sleep(1)
                    st.rerun()

        st.markdown("---")
        st.markdown("##### 2. Análise de Vendas do Banco de Dados")
        
        df = carregar_vendas_db()
        if not df.empty:
            if st.session_state["filtro_conta_selecionada"]:
                df = df[df['loja_conta'] == st.session_state["filtro_conta_selecionada"]]

            c1, c2, c3 = st.columns(3)
            periodo = c1.selectbox("📅 Período de Análise", ["30 dias", "90 dias", "6 Meses", "12 Meses", "Mês Atual", "Tudo"])
            
            hoje = datetime.now()
            if "30" in periodo: d_corte = hoje - timedelta(days=30)
            elif "90" in periodo: d_corte = hoje - timedelta(days=90)
            elif "6" in periodo: d_corte = hoje - timedelta(days=180)
            elif "12" in periodo: d_corte = hoje - timedelta(days=365)
            elif "Mês" in periodo: d_corte = hoje.replace(day=1)
            else: d_corte = df['data'].min()

            df_filt = df[df['data'] >= d_corte].copy()
            
            lista_canais = ["TODOS"] + sorted(df_filt['canal'].fillna("").unique())
            canal_sel = c2.selectbox("🛒 Canal/Origem", lista_canais)
            if canal_sel != "TODOS": df_filt = df_filt[df_filt['canal'] == canal_sel]
            
            termo = c3.text_input("📦 Buscar SKU ou Nome", placeholder="Digite para filtrar...")
            if termo:
                df_filt = df_filt[df_filt['produto'].astype(str).str.contains(termo, case=False, na=False) | 
                                  df_filt['sku'].astype(str).str.contains(termo, case=False, na=False)]

            if not df_filt.empty:
                k1, k2, k3 = st.columns(3)
                k1.metric("Qtd Vendida", int(df_filt['quantidade'].sum()))
                k2.metric("Receita Total", f"R$ {df_filt['valor'].sum():,.2f}")
                k3.metric("Registros", len(df_filt))

                st.markdown("### Vendas por SKU no Período")
                resumo_sku = df_filt.groupby(['sku', 'produto'])['quantidade'].sum().reset_index().sort_values('quantidade', ascending=False)
                st.dataframe(resumo_sku, use_container_width=True, hide_index=True, height=400, column_config={'sku': 'SKU', 'produto': 'Produto', 'quantidade': 'Qtd Vendida'})

                st.markdown("### Detalhamento das Vendas")
                df_show = df_filt[['data', 'loja_conta', 'canal', 'numero', 'sku', 'produto', 'quantidade', 'valor']].copy()
                df_show['data'] = df_show['data'].dt.strftime('%d/%m/%Y')
                st.dataframe(df_show, use_container_width=True, hide_index=True)
            else: 
                st.info("Sem dados para o filtro selecionado.")
        else:
            st.warning("Banco de dados de vendas vazio. Use o formulário acima para buscar e salvar as vendas.")