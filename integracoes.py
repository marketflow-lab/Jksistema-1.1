import streamlit as st
import json
import os
import requests
import base64
import time
import urllib.parse
from datetime import datetime

# --- CONFIGURAÃ‡Ã•ES GERAIS ---
PASTA_INFO = "info"
ARQUIVO_LOJAS = os.path.join(PASTA_INFO, "lojas_config.json")
ARQUIVO_TEMP_AUTH = os.path.join(PASTA_INFO, "temp_integracao.json")
ARQUIVO_CLIENT_ID = os.path.join(PASTA_INFO, "client_id_atual.json")
# CRÃTICO: Essa URI deve estar registrada no console da app ML/Bling.
# Pode ser sobrescrita por variÃ¡vel de ambiente JK_REDIRECT_URI.
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8001/auth/callback"
REDIRECT_URI = os.getenv("JK_REDIRECT_URI", DEFAULT_REDIRECT_URI).strip()

os.makedirs(PASTA_INFO, exist_ok=True)

# --- FUNÃ‡Ã•ES DE CLIENT_ID (TENANT) ---
def obter_client_id():
    """ObtÃ©m o client_id do usuÃ¡rio logado"""
    # Tenta ler do arquivo armazenado
    if os.path.exists(ARQUIVO_CLIENT_ID):
        try:
            with open(ARQUIVO_CLIENT_ID, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get('client_id', '').strip()
        except:
            pass
    
    # Tenta obter da session do Streamlit
    if 'client_id' in st.session_state:
        return st.session_state['client_id']
    
    # Fallback: retorna "default"
    return "default"

def salvar_client_id(client_id: str):
    """Salva o client_id do usuÃ¡rio"""
    try:
        with open(ARQUIVO_CLIENT_ID, 'w', encoding='utf-8') as f:
            json.dump({'client_id': client_id}, f, ensure_ascii=False)
        st.session_state['client_id'] = client_id
    except:
        pass

# --- GERENCIAMENTO DE DADOS (JSON) ---
def carregar_lojas():
    """Carrega as lojas do cliente atual"""
    client_id = obter_client_id()
    pasta_cliente = os.path.join(PASTA_INFO, client_id)
    arquivo = os.path.join(pasta_cliente, "lojas_config.json")

    # MigraÃ§Ã£o legada: move info/lojas_config.json para info/<client_id>/lojas_config.json
    if (not os.path.exists(arquivo)) and os.path.exists(ARQUIVO_LOJAS):
        os.makedirs(pasta_cliente, exist_ok=True)
        try:
            os.replace(ARQUIVO_LOJAS, arquivo)
        except Exception:
            try:
                import shutil
                shutil.copy2(ARQUIVO_LOJAS, arquivo)
            except Exception:
                pass
    
    if os.path.exists(arquivo):
        try:
            with open(arquivo, "r", encoding="utf-8") as f: 
                return json.load(f)
        except: 
            return []
    return []

def salvar_lojas(lojas):
    """Salva as lojas do cliente atual"""
    client_id = obter_client_id()
    pasta_cliente = os.path.join(PASTA_INFO, client_id)
    os.makedirs(pasta_cliente, exist_ok=True)
    arquivo = os.path.join(pasta_cliente, "lojas_config.json")
    
    with open(arquivo, "w", encoding="utf-8") as f: 
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
            atual = l['integracoes'].get(api_nome)
            if isinstance(atual, dict) and isinstance(dados_api, dict):
                merged = dict(atual)
                merged.update(dados_api)
                l['integracoes'][api_nome] = merged
            else:
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

# --- FUNÃ‡Ã•ES DE AUTH (BLING & ML) ---
def auth_bling_get_link(client_id, state):
    # Encode para evitar erros quando o nome da loja (state) ou redirect tiver espaÃ§os
    redirect = urllib.parse.quote(REDIRECT_URI, safe='')
    state_enc = urllib.parse.quote(state, safe='')
    return f"https://www.bling.com.br/Api/v3/oauth/authorize?response_type=code&client_id={client_id}&redirect_uri={redirect}&state={state_enc}"

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
    redirect = urllib.parse.quote(REDIRECT_URI, safe='')
    state_enc = urllib.parse.quote(state, safe='')
    scope = urllib.parse.quote("offline_access read write", safe='')
    return f"https://auth.mercadolivre.com.br/authorization?response_type=code&client_id={app_id}&redirect_uri={redirect}&state={state_enc}&scope={scope}"

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

# --- PÃGINA DE CONFIGURAÃ‡ÃƒO ---
def render_page(navegar_para_callback=None):
    c_back, c_tit = st.columns([1, 5])
    with c_back: 
        if navegar_para_callback:
            if st.button("â¬…ï¸ Voltar"): navegar_para_callback('menu')
    with c_tit: st.title("ðŸ”— Central de IntegraÃ§Ãµes")
    st.divider()

    # --- PAINEL DE CONFIGURAÃ‡ÃƒO DO USUÃRIO ---
    with st.expander("âš™ï¸ ConfiguraÃ§Ãµes do UsuÃ¡rio"):
        col1, col2 = st.columns([3, 1])
        with col1:
            client_id_atual = obter_client_id()
            novo_client_id = st.text_input(
                "NÃºmero do UsuÃ¡rio (ID do Cliente)", 
                value=client_id_atual,
                help="Digite o nÃºmero/ID do usuÃ¡rio para vincular as integraÃ§Ãµes"
            )
            if novo_client_id != client_id_atual:
                salvar_client_id(novo_client_id)
                st.success(f"âœ… NÃºmero do usuÃ¡rio atualizado: {novo_client_id}")
                st.rerun()
        
        with col2:
            st.metric("ID Atual", obter_client_id())
    
    st.divider()

    # Menu Lateral
    lojas_db = carregar_lojas()
    nomes_lojas = [l['nome'] for l in lojas_db]
    c_main, c_side = st.columns([3, 1])

    with c_side:
        st.subheader("ðŸ¢ Lojas")
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
            
            t1, t2, t3 = st.tabs(["ðŸ’Ž Bling", "ðŸ¤ Mercado Livre", "ðŸš€ Turbo"])
            
            # Bling
            with t1:
                cfg = integracoes.get('bling', {})
                if cfg.get('connected'): st.success("Conectado")
                else: st.warning("Desconectado")
                
                # Carrega valores do config salvo (tenta tanto 'id'/'secret' como 'client_id'/'client_secret')
                cid_val = cfg.get('id') or cfg.get('client_id') or ''
                sec_val = cfg.get('secret') or cfg.get('client_secret') or ''
                
                cid = st.text_input("Client ID", value=cid_val, key="b_id")
                sec = st.text_input("Client Secret", value=sec_val, type="password", key="b_sec")
                if st.button("Autenticar Bling"):
                    if cid and sec:
                        # Salva dados de auth temporÃ¡rios
                        salvar_temp_auth({"loja": loja_selecionada, "servico": "bling", "id": cid, "secret": sec})
                        # Pequena pausa para garantir que o arquivo foi escrito
                        time.sleep(0.5)
                        # Gera link de redirecionamento
                        link = auth_bling_get_link(cid, f"{loja_selecionada}|bling")
                        # Redireciona
                        st.markdown(f'<meta http-equiv="refresh" content="0;url={link}">', unsafe_allow_html=True)
            
            # ML
            with t2:
                cfg = integracoes.get('mercadolivre', {})
                if cfg.get('connected'): st.success(f"Conectado (ID: {cfg.get('user_id','')})")
                else: st.warning("Desconectado")
                
                # Carrega valores do config salvo (tenta tanto 'id'/'secret' como 'app_id'/'secret_key')
                app_id_val = cfg.get('id') or cfg.get('app_id') or ''
                secret_val = cfg.get('secret') or cfg.get('secret_key') or ''
                
                app_id = st.text_input("App ID", value=app_id_val, key="ml_id")
                sec_ml = st.text_input("Client Secret", value=secret_val, type="password", key="ml_sec")
                if st.button("Autenticar ML"):
                    if app_id and sec_ml:
                        # Salva dados de auth temporÃ¡rios
                        salvar_temp_auth({"loja": loja_selecionada, "servico": "mercadolivre", "id": app_id, "secret": sec_ml})
                        # Pequena pausa para garantir que o arquivo foi escrito
                        time.sleep(0.5)
                        # Gera link de redirecionamento
                        link = auth_ml_get_link(app_id, f"{loja_selecionada}|mercadolivre")
                        # Redireciona
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
