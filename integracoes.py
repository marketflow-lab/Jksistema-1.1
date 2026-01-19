import streamlit as st
import json
import os
import requests
import base64
import time
from datetime import datetime

# --- CONFIGURAÇÕES GERAIS ---
PASTA_INFO = "info"
ARQUIVO_LOJAS = os.path.join(PASTA_INFO, "lojas_config.json")
ARQUIVO_TEMP_AUTH = os.path.join(PASTA_INFO, "temp_integracao.json")
REDIRECT_URI = "http://localhost:8501" 

os.makedirs(PASTA_INFO, exist_ok=True)

# --- GERENCIAMENTO DE DADOS (JSON) ---
def carregar_lojas():
    if os.path.exists(ARQUIVO_LOJAS):
        try:
            with open(ARQUIVO_LOJAS, "r", encoding="utf-8") as f: 
                return json.load(f)
        except: return []
    return []

def salvar_lojas(lojas):
    with open(ARQUIVO_LOJAS, "w", encoding="utf-8") as f: 
        json.dump(lojas, f, indent=4, ensure_ascii=False)

def buscar_loja(nome_loja):
    lojas = carregar_lojas()
    for l in lojas:
        if l['nome'] == nome_loja: return l
    return None

def atualizar_api_loja(nome_loja, api_nome, dados_api):
    lojas = carregar_lojas()
    encontrou = False
    for l in lojas:
        if l['nome'] == nome_loja:
            if 'integracoes' not in l: l['integracoes'] = {}
            l['integracoes'][api_nome] = dados_api
            encontrou = True
            break
    if not encontrou:
        nova_loja = {"nome": nome_loja, "integracoes": {api_nome: dados_api}}
        lojas.append(nova_loja)
    salvar_lojas(lojas)

def salvar_temp_auth(dados):
    with open(ARQUIVO_TEMP_AUTH, "w", encoding="utf-8") as f: 
        json.dump(dados, f, ensure_ascii=False)

def ler_temp_auth():
    if os.path.exists(ARQUIVO_TEMP_AUTH):
        try:
            with open(ARQUIVO_TEMP_AUTH, "r", encoding="utf-8") as f: return json.load(f)
        except: return None
    return None

def limpar_temp_auth():
    if os.path.exists(ARQUIVO_TEMP_AUTH):
        try: os.remove(ARQUIVO_TEMP_AUTH)
        except: pass

# --- FUNÇÕES DE AUTH (BLING & ML) ---
def auth_bling_get_link(client_id, state):
    return f"https://www.bling.com.br/Api/v3/oauth/authorize?response_type=code&client_id={client_id}&redirect_uri={REDIRECT_URI}&state={state}"

def auth_bling_exchange(client_id, client_secret, code):
    url = "https://www.bling.com.br/Api/v3/oauth/token"
    credential = f"{client_id}:{client_secret}"
    headers = {
        "Authorization": f"Basic {base64.b64encode(credential.encode()).decode()}",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json"
    }
    payload = {"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI}
    try:
        resp = requests.post(url, headers=headers, data=payload)
        if resp.status_code == 200: return True, resp.json()
        return False, resp.text
    except Exception as e: return False, str(e)

def auth_ml_get_link(app_id, state):
    return f"https://auth.mercadolivre.com.br/authorization?response_type=code&client_id={app_id}&redirect_uri={REDIRECT_URI}&state={state}"

def auth_ml_exchange(app_id, client_secret, code):
    url = "https://api.mercadolibre.com/oauth/token"
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
    payload = {
        "grant_type": "authorization_code",
        "client_id": app_id,
        "client_secret": client_secret,
        "code": code,
        "redirect_uri": REDIRECT_URI
    }
    try:
        resp = requests.post(url, headers=headers, data=payload)
        if resp.status_code == 200: return True, resp.json()
        return False, resp.text
    except Exception as e: return False, str(e)

# --- PÁGINA DE CONFIGURAÇÃO ---
def render_page(navegar_para_callback=None):
    c_back, c_tit = st.columns([1, 5])
    with c_back: 
        if navegar_para_callback:
            if st.button("⬅️ Voltar"): navegar_para_callback('menu')
    with c_tit: st.title("🔗 Central de Integrações")
    st.divider()

    # Callback Auth
    qp = st.query_params
    if "code" in qp and "state" in qp:
        code = qp["code"]
        state = qp["state"]
        try:
            parts = state.split("|")
            if len(parts) >= 2:
                loja_alvo = parts[0]
                servico = parts[1]
                dados_temp = ler_temp_auth()
                if dados_temp and dados_temp.get('loja') == loja_alvo and dados_temp.get('servico') == servico:
                    cid = dados_temp['id']
                    sec = dados_temp['secret']
                    sucesso = False
                    resultado = None
                    with st.status(f"Conectando {servico.upper()}...", expanded=True) as status:
                        if servico == "bling": sucesso, resultado = auth_bling_exchange(cid, sec, code)
                        elif servico == "mercadolivre": sucesso, resultado = auth_ml_exchange(cid, sec, code)
                        
                        if sucesso:
                            dados_finais = {
                                "id": cid, "secret": sec,
                                "access_token": resultado.get("access_token"),
                                "refresh_token": resultado.get("refresh_token"),
                                "connected": True,
                                "updated_at": str(time.time())
                            }
                            if "user_id" in resultado: dados_finais["user_id"] = resultado["user_id"]
                            atualizar_api_loja(loja_alvo, servico, dados_finais)
                            limpar_temp_auth()
                            status.update(label="Conectado!", state="complete")
                            st.success("Sucesso! Recarregando...")
                            time.sleep(2)
                            st.query_params.clear()
                            st.rerun()
                        else:
                            status.update(label="Erro", state="error")
                            st.error(f"Falha: {resultado}")
        except Exception as e: st.error(f"Erro no retorno: {e}")

    # Menu Lateral
    lojas_db = carregar_lojas()
    nomes_lojas = [l['nome'] for l in lojas_db]
    c_main, c_side = st.columns([3, 1])

    with c_side:
        st.subheader("🏢 Lojas")
        loja_selecionada = st.selectbox("Selecione:", ["Nova Loja..."] + nomes_lojas)
        if loja_selecionada == "Nova Loja...":
            novo_nome = st.text_input("Nome da Loja")
            if st.button("Criar", type="primary"):
                if novo_nome:
                    atualizar_api_loja(novo_nome, "criacao", {"data": str(datetime.now())})
                    st.rerun()
        else:
            if st.button("Excluir Loja"):
                novas = [l for l in lojas_db if l['nome'] != loja_selecionada]
                salvar_lojas(novas)
                st.rerun()

    # Painel Principal
    with c_main:
        if loja_selecionada and loja_selecionada != "Nova Loja...":
            st.subheader(f"Configurando: {loja_selecionada}")
            dados_loja = buscar_loja(loja_selecionada)
            integracoes = dados_loja.get('integracoes', {})
            
            t1, t2, t3 = st.tabs(["💎 Bling", "🤝 Mercado Livre", "🚀 Turbo"])
            
            # Bling
            with t1:
                cfg = integracoes.get('bling', {})
                if cfg.get('connected'): st.success("Conectado")
                else: st.warning("Desconectado")
                
                cid = st.text_input("Client ID", value=cfg.get('id', ''), key="b_id")
                sec = st.text_input("Client Secret", value=cfg.get('secret', ''), type="password", key="b_sec")
                if st.button("Autenticar Bling"):
                    if cid and sec:
                        salvar_temp_auth({"loja": loja_selecionada, "servico": "bling", "id": cid, "secret": sec})
                        link = auth_bling_get_link(cid, f"{loja_selecionada}|bling")
                        st.markdown(f'<meta http-equiv="refresh" content="0;url={link}">', unsafe_allow_html=True)
            
            # ML
            with t2:
                cfg = integracoes.get('mercadolivre', {})
                if cfg.get('connected'): st.success(f"Conectado (ID: {cfg.get('user_id','')})")
                else: st.warning("Desconectado")
                
                app_id = st.text_input("App ID", value=cfg.get('id', ''), key="ml_id")
                sec_ml = st.text_input("Client Secret", value=cfg.get('secret', ''), type="password", key="ml_sec")
                if st.button("Autenticar ML"):
                    if app_id and sec_ml:
                        salvar_temp_auth({"loja": loja_selecionada, "servico": "mercadolivre", "id": app_id, "secret": sec_ml})
                        link = auth_ml_get_link(app_id, f"{loja_selecionada}|mercadolivre")
                        st.markdown(f'<meta http-equiv="refresh" content="0;url={link}">', unsafe_allow_html=True)
            
            # Turbo
            with t3:
                cfg = integracoes.get('mercadoturbo', {})
                tk = st.text_input("Token Turbo", value=cfg.get('token', ''), key="mt_tok")
                if st.button("Salvar Turbo"):
                    atualizar_api_loja(loja_selecionada, "mercadoturbo", {"token": tk, "connected": True})
                    st.success("Salvo!")
        else:
            st.info("Crie ou selecione uma loja ao lado.")