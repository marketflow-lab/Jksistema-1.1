import streamlit as st
import pandas as pd
import json
import os
import time
import requests
import base64
from datetime import datetime
import unicodedata

# --- CONFIGURAÇÕES ---
PASTA_INFO = "info"
ARQUIVO_INTEGRACOES = os.path.join(PASTA_INFO, "integracoes.json")
# O novo arquivo de banco de dados será um CSV para melhor compatibilidade.
ARQUIVO_DB_PRODUTOS = os.path.join(PASTA_INFO, "produtos_compilado.csv")

os.makedirs(PASTA_INFO, exist_ok=True)

# --- FUNÇÕES AUXILIARES ---
def carregar_dados_integracao():
    if os.path.exists(ARQUIVO_INTEGRACOES):
        try:
            with open(ARQUIVO_INTEGRACOES, "r", encoding="utf-8") as f:
                return json.load(f)
        except: return {}
    return {}

def salvar_dados_integracao(dados):
    with open(ARQUIVO_INTEGRACOES, "w", encoding="utf-8") as f: 
        json.dump(dados, f, indent=4, ensure_ascii=False)

def carregar_produtos_db():
    if os.path.exists(ARQUIVO_DB_PRODUTOS):
        try:
            return pd.read_csv(ARQUIVO_DB_PRODUTOS, encoding="utf-8")
        except Exception as e:
            st.error(f"Erro ao ler banco de dados de produtos: {e}")
            return pd.DataFrame()
    return pd.DataFrame()

def salvar_produtos_db(df):
    df.to_csv(ARQUIVO_DB_PRODUTOS, index=False, encoding="utf-8")

def atualizar_tokens_loja(nome_loja, access_token, refresh_token):
    dados = carregar_dados_integracao()
    if nome_loja in dados and "Bling" in dados[nome_loja]:
        dados[nome_loja]["Bling"]["access_token"] = access_token
        dados[nome_loja]["Bling"]["refresh_token"] = refresh_token
        dados[nome_loja]["Bling"]["updated_at"] = str(time.time())
        salvar_dados_integracao(dados)

def remover_acentos(texto):
    if not texto: return ""
    texto = str(texto).upper()
    nfkd = unicodedata.normalize('NFKD', texto)
    return "".join([c for c in nfkd if not unicodedata.combining(c)])

# --- API BLING V3 ---
def obter_headers_auth_bling(client_id, client_secret):
    credential = f"{client_id}:{client_secret}"
    cred_b64 = base64.b64encode(credential.encode()).decode()
    return {
        "Authorization": f"Basic {cred_b64}",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json"
    }

def refresh_token_bling(nome_loja, config_bling):
    url = "https://www.bling.com.br/Api/v3/oauth/token"
    refresh = config_bling.get("refresh_token")
    cid = config_bling.get("client_id")
    csec = config_bling.get("client_secret")
    
    if not cid or not csec or not refresh: return None

    payload = {"grant_type": "refresh_token", "refresh_token": refresh}
    headers = obter_headers_auth_bling(cid, csec)
    
    try:
        response = requests.post(url, headers=headers, data=payload, timeout=20)
        if response.status_code == 200:
            novos = response.json()
            atualizar_tokens_loja(nome_loja, novos["access_token"], novos["refresh_token"])
            return novos["access_token"]
    except Exception as e: 
        st.error(f"Erro ao atualizar token do Bling: {e}")
    return None

def mapear_depositos_bling(access_token):
    url = "https://www.bling.com.br/Api/v3/depositos"
    headers = {"Authorization": f"Bearer {access_token}"}
    mapa_ids = {} 
    try:
        r = requests.get(url, headers=headers, params={"situacao": 1}, timeout=10)
        if r.status_code == 200:
            for dep in r.json().get("data", []):
                
                d_id = str(dep.get("id"))
                nome = str(dep.get("descricao", "")).upper()
                
                if any(t in nome for t in ["FULL", "FULFILLMENT", "MERCADO LIVRE", "ENVIO"]):
                    tipo = "FULL"
                elif dep.get("padrao"):
                    tipo = "LOJA_PADRAO"
                else:
                    tipo = "LOJA_SECUNDARIA"
                
                # Regra atualizada: Só pular se desconsiderarSaldo for True E o tipo não for FULL
                if dep.get("desconsiderarSaldo") and tipo != "FULL":
                    continue

                mapa_ids[d_id] = tipo
    except Exception as e:
        st.error(f"Erro ao mapear depósitos do Bling: {e}")
    return mapa_ids

def buscar_produtos_bling(access_token):
    url = "https://www.bling.com.br/Api/v3/produtos"
    headers = {"Authorization": f"Bearer {access_token}"}
    prods = []
    
    progresso_texto = "Buscando produtos no Bling... Página {}"
    barra_progresso = st.progress(0, text=progresso_texto.format(1))
    
    # Loop para buscar todas as páginas de produtos
    for pag in range(1, 1000): # Limite alto para garantir que todos os produtos sejam buscados
        try:
            # O parâmetro "situacao" foi removido para buscar TODOS os produtos
            r = requests.get(url, headers=headers, params={"pagina": pag, "limite": 100, "tipo": "P"})
            
            if r.status_code == 401:
                st.error("Erro de autorização (401) ao buscar produtos no Bling. O token de acesso expirou e não pôde ser renovado.")
                st.info("➡️ Solução: Vá à página 'Integrações' e conecte a loja Bling novamente para gerar uma nova autorização.")
                break

            if r.status_code != 200:
                st.warning(f"API Bling respondeu com status {r.status_code} na página {pag}. Parando.")
                break
            
            lista = r.json().get("data", [])
            if not lista: break # Se não houver mais dados, para o loop

            for p in lista:
                prods.append({
                    "id_bling": p.get("id"),
                    "sku": p.get("codigo"),
                    "nome_bling": p.get("nome"),
                    "situacao_bling": p.get("situacao"),
                })
            
            barra_progresso.progress(pag / 50, text=progresso_texto.format(pag + 1)) # Estima 50 páginas
        except Exception as e:
            st.error(f"Erro na busca de produtos do Bling na página {pag}: {e}")
            break
            
    barra_progresso.empty()
    return prods

def buscar_saldos_bling(access_token, produtos, mapa_deps):
    url = "https://www.bling.com.br/Api/v3/estoques/saldos"
    headers = {"Authorization": f"Bearer {access_token}"}
    ids = [p['id_bling'] for p in produtos]
    mapa_saldos = {}
    
    total_chunks = (len(ids) + 49) // 50
    progresso_texto = "Buscando saldos no Bling... Lote {} de {}"
    barra_progresso = st.progress(0, text=progresso_texto.format(1, total_chunks))

    for i in range(0, len(ids), 50):
        chunk_num = (i // 50) + 1
        chunk = ids[i:i+50]
        params = [('idsProdutos[]', str(pid)) for pid in chunk]
        
        try:
            r = requests.get(url, headers=headers, params=params, timeout=20)
            if r.status_code == 200:
                for item in r.json().get("data", []):
                    pid = item.get("produto",{}).get("id")
                    
                    if pid not in mapa_saldos: 
                        mapa_saldos[pid] = {'loja': 0, 'full': 0}

                    for dep in item.get("depositos", []):
                        qtd = float(dep.get("saldoFisico", 0))
                        did = str(dep.get("id"))
                        
                        tipo = mapa_deps.get(did)
                        
                        if tipo == "FULL": 
                            mapa_saldos[pid]['full'] += qtd
                        elif tipo == "LOJA_PADRAO":
                            mapa_saldos[pid]['loja'] += qtd
            else:
                st.warning(f"Erro ao buscar saldos do Bling (Lote {chunk_num}): Status {r.status_code}")
        except Exception as e:
            st.error(f"Erro na requisição de saldos do Bling (Lote {chunk_num}): {e}")

        barra_progresso.progress(chunk_num / total_chunks, text=progresso_texto.format(chunk_num, total_chunks))
    
    barra_progresso.empty()
    return mapa_saldos

def buscar_lotes_produto_bling(access_token, id_produto):
    url = f"https://api.bling.com.br/Api/v3/produtos/{id_produto}/lotes"
    headers = {"Authorization": f"Bearer {access_token}"}
    lotes = []
    
    for pag in range(1, 100):
        try:
            r = requests.get(url, headers=headers, params={"pagina": pag, "limite": 100})
            if r.status_code != 200:
                break
            
            lista = r.json().get("data", [])
            if not lista:
                break

            for lote in lista:
                lotes.append(lote)
        except Exception as e:
            print(f"Erro ao buscar lotes do produto {id_produto}: {e}")
            break
            
    return lotes

def buscar_lancamentos_lote_bling(access_token, id_lote):
    url = f"https://api.bling.com.br/Api/v3/produtos/lotes/{id_lote}/lancamentos"
    headers = {"Authorization": f"Bearer {access_token}"}
    lancamentos = []
    
    for pag in range(1, 100):
        try:
            r = requests.get(url, headers=headers, params={"pagina": pag, "limite": 100})
            if r.status_code != 200:
                break
            
            lista = r.json().get("data", [])
            if not lista:
                break

            for lancamento in lancamentos:
                lancamentos.append(lancamento)
        except Exception as e:
            print(f"Erro ao buscar lancamentos do lote {id_lote}: {e}")
            break
            
    return lancamentos

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


# --- API MERCADO TURBO (PLACEHOLDER) ---
def buscar_dados_mercado_turbo(config_turbo):
    token = config_turbo.get("token")
    if not token:
        st.warning("Token do Mercado Turbo não configurado. Pulando sincronização.")
        return pd.DataFrame()

    # --- INÍCIO DO PLACEHOLDER ---
    st.info("A integração com Mercado Turbo ainda precisa ser implementada.")
    st.warning("É necessário ter a documentação da API para buscar os dados dos produtos (título, preço, status, etc.).")
    
    # Exemplo de como a função deveria retornar os dados.
    # Esta estrutura de dados é um EXEMPLO. A real dependerá da API.
    # O importante é que ela retorne um DataFrame com a coluna 'sku'.
    dados_exemplo = [
        {"sku": "SKU-001", "titulo_turbo": "Produto Exemplo 1 Turbo", "preco_turbo": 199.90, "status_turbo": "ativo"},
        {"sku": "SKU-002", "titulo_turbo": "Produto Exemplo 2 Turbo", "preco_turbo": 250.00, "status_turbo": "pausado"},
        {"sku": "SKU-003", "titulo_turbo": "Produto Exemplo 3 Turbo", "preco_turbo": 150.00, "status_turbo": "ativo"},
    ]
    df_turbo = pd.DataFrame(dados_exemplo)
    # --- FIM DO PLACEHOLDER ---
    
    st.toast("Dados de exemplo do Mercado Turbo carregados.", icon="🚀")
    return df_turbo

# --- FUNÇÃO PRINCIPAL DE SINCRONIZAÇÃO ---
def sincronizar_dados_loja(nome_loja, config_loja):
    # Parte 1: Bling
    st.markdown("#### Etapa 1: Sincronizando com o Bling")
    config_bling = config_loja.get("Bling", {})
    token = refresh_token_bling(nome_loja, config_bling)
    if not token: 
        token = config_bling.get("access_token")
    if not token:
        st.error("Não foi possível obter token do Bling. Verifique as credenciais.")
        return False, "Falha na autenticação com Bling."

    deps = mapear_depositos_bling(token)
    prods_bling = buscar_produtos_bling(token)
    
    if not prods_bling:
        st.warning("Nenhum produto encontrado no Bling.")
        df_bling = pd.DataFrame()
    else:
        saldos_bling = buscar_saldos_bling(token, prods_bling, deps)
        
        for p in prods_bling:
            s = saldos_bling.get(p['id_bling'])
            if s:
                p['saldo_loja'] = s['loja']
                p['saldo_full'] = s['full']
        
        df_bling = pd.DataFrame(prods_bling)
    
    st.success(f"Bling: {len(df_bling)} produtos encontrados.")

    # Parte 2: Mercado Turbo
    st.markdown("#### Etapa 2: Sincronizando com o Mercado Turbo")
    config_turbo = config_loja.get("Mercado Turbo", {})
    df_turbo = buscar_dados_mercado_turbo(config_turbo)
    st.success(f"Mercado Turbo: {len(df_turbo)} produtos de exemplo carregados.")

    # Parte 3: Unificação dos Dados
    st.markdown("#### Etapa 3: Unificando banco de dados")
    if df_bling.empty and df_turbo.empty:
        st.success("Nenhum produto encontrado em nenhuma das fontes para esta loja.")
        # Limpa os dados antigos da loja, já que nada foi encontrado
        df_final = pd.DataFrame()
    elif not df_bling.empty and not df_turbo.empty:
        # Garante que a coluna 'sku' existe em ambos antes de fazer o merge
        if 'sku' not in df_turbo.columns:
            st.error("O retorno do Mercado Turbo não contém a coluna 'sku'. A unificação será parcial.")
            df_final = df_bling
        elif 'sku' not in df_bling.columns:
             st.error("O retorno do Bling não contém a coluna 'sku'. A unificação será parcial.")
             df_final = df_turbo
        else:
            df_final = pd.merge(df_bling, df_turbo, on="sku", how="outer")
            st.success("Dados do Bling e Mercado Turbo unificados.")
            
    elif not df_bling.empty:
        df_final = df_bling
        st.success("Apenas dados do Bling foram encontrados e processados.")
    else: # Apenas df_turbo tem dados
        df_final = df_turbo
        st.success("Apenas dados do Mercado Turbo foram encontrados e processados.")
    
    # Preenche colunas de saldo para itens que só existem no turbo
    if not df_final.empty:
        for col in ['saldo_loja', 'saldo_full']:
            if col in df_final.columns:
                df_final[col] = df_final[col].fillna(0)

        df_final["loja_sync"] = nome_loja
        df_final["last_update"] = datetime.now().strftime("%d/%m/%Y %H:%M")
    
    # Salva no DB, removendo dados antigos DESSA loja
    df_old = carregar_produtos_db()
    
    if not df_old.empty and 'loja_sync' in df_old.columns:
        df_old_sem_loja_atual = df_old[df_old['loja_sync'] != nome_loja]
        df_final = pd.concat([df_old_sem_loja_atual, df_final], ignore_index=True)
    
    salvar_produtos_db(df_final)
    st.success(f"Banco de dados local atualizado com {len(df_final)} produtos no total.")
    
    return True, f"Sincronização da loja '{nome_loja}' concluída!"


# --- RENDERIZAÇÃO DA PÁGINA ---
def render_page(navegar_para_callback):
    if st.button("⬅️ Voltar ao Menu"):
        navegar_para_callback('menu')

    st.title("📦 Gerenciamento de Estoque")
    st.markdown("---")
    
    dados_integracao = carregar_dados_integracao()
    lojas_bling_ok = [k for k, v in dados_integracao.items() if v.get("Bling", {}).get("status")]

    c_main, c_side = st.columns([3, 1])
    
    with c_side:
        st.header("⚙️ Controle")
        st.markdown("### 👁️ Visualizar Loja")
        
        if not lojas_bling_ok:
            st.warning("Nenhuma loja Bling conectada.")
            loja_visualizar = None
        else:
            loja_visualizar = st.radio("Escolha a loja para visualizar/sincronizar:", options=lojas_bling_ok)

        st.markdown("---")
        st.markdown("### 🔄 Sincronizar Dados")
        
        if loja_visualizar:
            if st.button(f"Atualizar Loja: {loja_visualizar}", use_container_width=True, type="primary"):
                if loja_visualizar in dados_integracao:
                    with st.spinner(f"Sincronizando {loja_visualizar}... Isso pode levar alguns minutos."):
                        ok, msg = sincronizar_dados_loja(loja_visualizar, dados_integracao[loja_visualizar])
                    
                    if ok: 
                        st.success(msg)
                        time.sleep(1.5)
                        st.rerun()
                    else: 
                        st.error(msg)
                else:
                    st.error("Configuração da loja não encontrada.")
            
            if st.button("Calcular Vendas Mensais", use_container_width=True):
                if loja_visualizar in dados_integracao:
                    config_bling = dados_integracao[loja_visualizar].get("Bling", {})
                    token = refresh_token_bling(loja_visualizar, config_bling)
                    if not token:
                        token = config_bling.get("access_token")
                    
                    if token:
                        produtos = buscar_produtos_bling(token)
                        if produtos:
                            df_vendas_mensais = calcular_vendas_mensais(token, produtos)
                            st.session_state['vendas_mensais'] = df_vendas_mensais
                        else:
                            st.warning("Nenhum produto encontrado para calcular as vendas.")
                    else:
                        st.error("Não foi possível obter token do Bling.")
                else:
                    st.error("Configuração da loja não encontrada.")

        else:
            st.info("Selecione uma loja acima para poder sincronizar.")

    with c_main:
        df = carregar_produtos_db()
        
        if 'vendas_mensais' in st.session_state and not st.session_state['vendas_mensais'].empty:
            st.subheader("Vendas Mensais por SKU (baseado em saídas de estoque)")
            st.dataframe(st.session_state['vendas_mensais'])

        if df.empty:
            st.info("Banco de dados de produtos vazio. Selecione e sincronize uma loja ao lado.")
            return

        if loja_visualizar:
            if 'loja_sync' in df.columns:
                df_filtrado = df[df['loja_sync'] == loja_visualizar]
            else:
                df_filtrado = pd.DataFrame()
        else:
            df_filtrado = pd.DataFrame()

        if not df_filtrado.empty:
            st.subheader(f"Estoque Compilado: {loja_visualizar}")
            
            busca = st.text_input("🔍 Buscar SKU ou Nome:", placeholder="Digite para filtrar...")
            if busca:
                busca_norm = remover_acentos(busca)
                df_filtrado['filtro'] = df_filtrado['nome_bling'].apply(remover_acentos) + df_filtrado['sku'].apply(remover_acentos)
                mask = df_filtrado['filtro'].str.contains(busca_norm, na=False)
                df_show = df_filtrado[mask].drop(columns=['filtro'])
            else:
                df_show = df_filtrado
            
            # Define colunas a serem exibidas. Adapte conforme os dados do Turbo forem implementados.
            colunas_visiveis = [
                "sku", "nome_bling", "saldo_loja", "saldo_full", 
                "titulo_turbo", "preco_turbo", "status_turbo", # Colunas do Turbo (exemplo)
                "last_update"
            ]
            
            # Filtra o dataframe para mostrar apenas as colunas que realmente existem
            colunas_existentes = [c for c in colunas_visiveis if c in df_show.columns]
            
            st.dataframe(
                df_show[colunas_existentes],
                column_config={
                    "sku": "SKU",
                    "nome_bling": "Nome Bling",
                    "saldo_loja": st.column_config.NumberColumn("Estoque Loja", format="%.0f"),
                    "saldo_full": st.column_config.NumberColumn("Estoque Full", format="%.0f"),
                    "last_update": "Atualizado em",
                    "titulo_turbo": "Título Mercado Turbo",
                    "preco_turbo": st.column_config.NumberColumn("Preço Turbo", format="R$ %.2f"),
                    "status_turbo": "Status Turbo"
                },
                hide_index=True,
                use_container_width=True
            )
            st.caption(f"Total de itens listados: {len(df_show)}")
        else:
            if loja_visualizar:
                st.warning(f"Nenhum dado encontrado para a loja: {loja_visualizar}")
                st.markdown("Se você acabou de selecionar a loja, clique em **Atualizar** na barra lateral.")
