import logging
import logging.handlers
import os
import json
import time
import base64
import urllib.parse
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

import streamlit as st
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

# --- IMPORTAÇÃO DOS MÓDULOS LOCAIS ---
import login
import atualizacao
import promo
import renovacao
import etiquetas
import favoritos

try:
    import vendas
except ImportError:
    vendas = None

# --- CONFIGURAÇÕES ---
PASTA_INFO = "info"
PASTA_IMG = "img"
os.makedirs(PASTA_INFO, exist_ok=True)
os.makedirs(PASTA_IMG, exist_ok=True)

CREDENTIALS_FILE = os.path.join(PASTA_INFO, 'credentials.json')
CONFIG_FILE = os.path.join(PASTA_INFO, 'config_sheet.json')
INTEGRACOES_FILE = os.path.join(PASTA_INFO, 'integracoes.json')
LOG_FILE = os.path.join(PASTA_INFO, 'jk_sistema.log')
LOGO_PATH = os.path.join(PASTA_IMG, "Logo Trend.png")
BACKGROUND_PATH = os.path.join(PASTA_IMG, "background.png")
VERSAO_SISTEMA = "1.5"
REDIRECT_URI = "http://localhost:8501"


# --- LOGGING ---
logger = logging.getLogger("jk_sistema")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    handler = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handler.setFormatter(fmt)
    logger.addHandler(handler)


def _requests_session_with_retry(total_retries: int = 3, backoff_factor: float = 0.3) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=total_retries,
        backoff_factor=backoff_factor,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "POST"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


st.set_page_config(page_title="JK Sistema de Gestão", page_icon="🚀", layout="wide", initial_sidebar_state="collapsed")


# ----------------------
# Utilitários de arquivo
# ----------------------
def _safe_read_json(path: str) -> Any:
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except Exception as e:
        logger.exception("Erro lendo JSON %s", path)
        return None


def _safe_write_json(path: str, data: Any) -> bool:
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        return True
    except Exception:
        logger.exception("Erro gravando JSON %s", path)
        return False


# ----------------------
# Integrações & OAuth
# ----------------------
def carregar_dados_integracao() -> Dict[str, Any]:
    data = _safe_read_json(INTEGRACOES_FILE)
    return data if isinstance(data, dict) else {}


def salvar_dados_integracao(dados: Dict[str, Any]) -> bool:
    return _safe_write_json(INTEGRACOES_FILE, dados)


def trocar_code_por_token(code: str, client_id: str, client_secret: str, timeout: int = 15) -> Tuple[bool, Any]:
    """Troca o Authorization Code pelo Access Token e Refresh Token usando Session+Retry."""
    url = "https://www.bling.com.br/Api/v3/oauth/token"
    credential = f"{client_id}:{client_secret}"
    cred_b64 = base64.b64encode(credential.encode()).decode()

    headers = {
        "Authorization": f"Basic {cred_b64}",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }

    session = _requests_session_with_retry()
    try:
        resp = session.post(url, headers=headers, data=payload, timeout=timeout)
        if resp.status_code == 200:
            return True, resp.json()
        logger.warning("Falha trocar token: %s %s", resp.status_code, resp.text[:500])
        return False, f"Erro {resp.status_code}: {resp.text}"
    except Exception as e:
        logger.exception("Erro trocando code por token")
        return False, str(e)


# ----------------------
# OAuth Interceptor
# ----------------------
def _process_oauth_callback() -> None:
    qp = st.query_params
    if "code" not in qp:
        return

    st.markdown("### 🔄 Finalizando Conexão com Bling...")
    st.info("Aguarde — processando autorização...")

    code_recebido = qp.get("code")
    state_loja = qp.get("state")

    if not code_recebido or not state_loja:
        st.query_params.clear()
        st.rerun()
        return

    dados = carregar_dados_integracao()
    loja = state_loja
    if loja not in dados:
        st.error("Loja não encontrada. Refaça o processo.")
        st.stop()

    bling_cfg = dados.get(loja, {}).get("Bling", {})
    client_id = bling_cfg.get("client_id")
    client_secret = bling_cfg.get("client_secret")

    if not client_id or not client_secret:
        st.error("Client ID ou Secret não encontrados. Tente configurar novamente.")
        st.stop()

    ok, result = trocar_code_por_token(code_recebido, client_id, client_secret)
    if ok:
        bling_cfg.update({
            "access_token": result.get("access_token"),
            "refresh_token": result.get("refresh_token"),
            "status": True,
            "updated_at": str(time.time()),
        })
        dados[loja]["Bling"] = bling_cfg
        if not salvar_dados_integracao(dados):
            st.warning("Conectado, mas não foi possível salvar os dados localmente.")
        st.success(f"✅ Sucesso! Loja '{loja}' conectada.")
        time.sleep(1.2)
        st.query_params.clear()
        st.session_state['pagina_atual'] = 'integracao'
        st.rerun()
    else:
        erro_str = str(result).lower()
        if "invalid_client" in erro_str or "credentials are invalid" in erro_str:
            st.error("Falha na autenticação com Bling: Credenciais Inválidas")
            st.warning(
                "🚨 O 'Client ID' e/ou 'Client Secret' que você inseriu estão incorretos. "
                "A autorização não pode ser completada."
            )
            st.info(
                "➡️ Solução: Obtenha as credenciais corretas no seu painel de aplicativos do Bling, "
                "insira-as novamente na página de Integrações e clique em 'Salvar Credenciais' antes de "
                "tentar autorizar novamente."
            )
            st.query_params.clear()
        else:
            # Mantém o erro genérico para outros problemas
            st.error(f"Falha ao trocar token: {result}")
        st.stop()


_process_oauth_callback()


# ----------------------
# UI helpers
# ----------------------
def get_img_as_base64(file_path: str) -> Optional[str]:
    try:
        if not os.path.exists(file_path):
            return None
        with open(file_path, 'rb') as f:
            data = f.read()
        return f"data:image/png;base64,{base64.b64encode(data).decode()}"
    except Exception:
        logger.exception("Erro convertendo imagem para base64: %s", file_path)
        return None


def aplicar_estilo_background() -> None:
    bg = get_img_as_base64(BACKGROUND_PATH)
    bg_css = f'url("{bg}")' if bg else 'url("https://img.freepik.com/free-vector/dark-low-poly-background-with-blue-lights_1017-26315.jpg?w=1380")'
    st.markdown(f"""
        <style>
            .stApp {{background-image: linear-gradient(rgba(0,0,0,0.7), rgba(0,0,0,0.7)), {bg_css}; background-size: cover; background-attachment: fixed; background-position: center;}}
            .block-container {{background-color: rgba(25, 25, 35, 0.85); backdrop-filter: blur(10px); border-radius: 20px; padding: 3rem !important; margin-top: 2rem; border: 1px solid rgba(255,255,255,0.1);}}
            h1, h2, h3 {{color: #ffffff !important;}} p, label, span, div {{color: #e0e0e0 !important;}}
            div[data-baseweb="input"] {{background-color: rgba(0,0,0,0.3) !important; border: 1px solid #444 !important; border-radius: 8px !important; color: white !important;}}
            input.st-bd {{color: white !important;}}
            div.stButton > button {{border-radius: 8px; font-weight: bold; border: none;}}
            section[data-testid="stSidebar"] {{background-color: rgba(15, 15, 20, 0.95) !important; border-right: 1px solid #333;}}
            .card-integracao {{background-color: rgba(255,255,255,0.05); padding: 20px; border-radius: 10px; border: 1px solid #444; margin-bottom: 10px;}}
            .status-conectado {{color: #00ff00; font-weight: bold;}}
            .status-desconectado {{color: #ff4444; font-weight: bold;}}
            .stDeployButton, footer, #MainMenu {{visibility: hidden;}}

            /* Custom Sidebar Styles */
            [data-testid="stSidebar"] .user-info {{
                background-color: rgba(255, 255, 255, 0.05);
                border-radius: 10px;
                padding: 1rem;
                margin-bottom: 1rem;
            }}
            [data-testid="stSidebar"] .user-info h3 {{
                text-align: center;
                margin-bottom: 0.25rem;
            }}
            [data-testid="stSidebar"] .alert-box {{
                background-color: rgba(30, 60, 100, 0.5);
                border: 1px solid rgba(79, 140, 255, 0.6);
                border-radius: 10px;
                padding: 0.75rem 1rem;
                margin-bottom: 1.5rem;
                font-size: 0.9em;
                color: #e0e0e0;
                text-align: center;
            }}
            [data-testid="stSidebar"] .stButton button {{
                background-color: rgba(255, 255, 255, 0.08);
                border: 1px solid rgba(255, 255, 255, 0.2);
                border-radius: 8px;
                font-weight: 600;
            }}
            [data-testid="stSidebar"] .stButton button:hover {{
                border-color: #4facfe;
                color: #4facfe !important;
                background-color: rgba(79, 172, 254, 0.1);
            }}
            [data-testid="stSidebar"] a {{
                text-decoration: none;
            }}
            [data-testid="stSidebar"] div[data-testid="stMarkdownContainer"] p {{
                 color: #a0a0a0 !important;
            }}
            [data-testid="stSidebar"] div[data-testid="stMarkdownContainer"] b {{
                 color: #e0e0e0 !important;
            }}
        </style>
    """, unsafe_allow_html=True)


aplicar_estilo_background()


# ----------------------
# Google Sheets / Autenticação
# ----------------------
def carregar_id_sistema() -> Optional[str]:
    config = _safe_read_json(CONFIG_FILE)
    if isinstance(config, dict):
        return config.get('spreadsheet_id', '').strip() or None
    return None


def autenticar_google() -> Optional[gspread.Client]:
    if not os.path.exists(CREDENTIALS_FILE):
        logger.info("Arquivo de credentials não encontrado: %s", CREDENTIALS_FILE)
        return None
    try:
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scopes)
        return gspread.authorize(creds)
    except Exception:
        logger.exception("Falha ao autenticar Google")
        return None


SPREADSHEET_ID_SISTEMA = carregar_id_sistema()
if not SPREADSHEET_ID_SISTEMA:
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        if os.path.exists(LOGO_PATH):
            st.image(LOGO_PATH, width=150)
        with st.container():
            st.markdown('<div class="setup-container" style="text-align:center;"><h2>🔑 Ativação</h2></div>', unsafe_allow_html=True)
            nome = st.text_input("Seu Nome:")
            chave = st.text_input("Chave de Ativação:")
            if st.button("✅ ATIVAR", type="primary"):
                if nome and chave:
                    client = autenticar_google()
                    if client:
                        try:
                            client.open_by_key(chave).update_title(f"{nome} - {datetime.now().strftime('%d/%m/%Y')}")
                            _safe_write_json(CONFIG_FILE, {'spreadsheet_id': chave.strip()})
                            st.success("Sucesso!")
                            time.sleep(1)
                            st.rerun()
                        except Exception:
                            logger.exception("Erro ao validar chave de ativação")
                            st.error("Erro na chave")
    st.stop()


authenticator, is_authenticated = login.render_login_component()
if not is_authenticated:
    st.stop()


# ----------------------
# Roteamento e UI
# ----------------------
if 'pagina_atual' not in st.session_state:
    st.session_state['pagina_atual'] = 'menu'


def navegar_para(pagina: str) -> None:
    st.session_state['pagina_atual'] = pagina
    st.rerun()


if 'update_checked' not in st.session_state:
    st.session_state['update_checked'] = True


with st.sidebar:
    # 1. Logo
    if os.path.exists(LOGO_PATH):
        st.image(LOGO_PATH, width=150, output_format='PNG')

    # 2. User Info
    st.markdown("---")
    name = st.session_state.get('name', 'Usuário')
    p_nome = name.split()[0] if name and isinstance(name, str) else "Usuário"
    st.markdown(f"""
    <div class="user-info">
        <h3 style='color:#e0e0e0; margin-bottom: 5px;'>Olá, {p_nome}!</h3>
        <p style='color:#a0a0a0; font-size:0.9em; margin-bottom:0;'>Bem-vindo(a) ao sistema.</p>
    </div>
    """, unsafe_allow_html=True)

    # 3. Navigation
    permissoes = st.session_state.get('permissoes_modulos', {})
    if st.button("🏠 Início", use_container_width=True, help="Voltar para o menu principal"):
        navegar_para('menu')
    if permissoes.get('integracao', False):
        if st.button("🧩 Integrações", use_container_width=True, help="Configurar integrações com Bling, ML, etc."):
            navegar_para('integracao')

    st.markdown("<div style='margin-top: 1rem;'></div>", unsafe_allow_html=True)  # Spacer

    # 4. Access Validity
    msg_validade = st.session_state.get('msg_validade', 'Status de acesso não verificado.')
    st.markdown(f"<div class='alert-box'><b>{msg_validade}</b></div>", unsafe_allow_html=True)

    # 5. Logout
    st.markdown("<div style='margin-top: 1rem;'></div>", unsafe_allow_html=True)  # Spacer
    st.markdown("<hr style='margin:0.5rem 0; border-top:1px solid #444;'>", unsafe_allow_html=True)
    try:
        authenticator.logout("Sair", "sidebar")
    except Exception:
        logger.exception("Erro ao adicionar botão de logout")
    st.caption(f"v{VERSAO_SISTEMA} | JK Sistema")



def page_integracao() -> None:
    if st.button("⬅️ Voltar ao Menu"):
        navegar_para('menu')
    st.title("🧩 Central de Integrações")
    st.markdown("---")


    dados = carregar_dados_integracao()

    col_loja, col_add = st.columns([3, 1])
    with col_loja:
        lojas_existentes = list(dados.keys())
        loja_selecionada = st.selectbox("Selecione a Loja:", ["Selecione..."] + lojas_existentes)

    with col_add:
        nova_loja = st.text_input("Criar Nova Loja", placeholder="Nome da Loja", label_visibility="collapsed")
        if st.button("➕ Adicionar Loja"):
            if nova_loja and nova_loja not in dados:
                dados[nova_loja] = {"Bling": {"status": False}, "Mercado Livre": {"status": False}, "Mercado Turbo": {"status": False}}
                salvar_dados_integracao(dados)
                st.success(f"Loja {nova_loja} criada!")
                st.rerun()

    if loja_selecionada == "Selecione...":
        st.info("Selecione uma loja acima ou crie uma nova.")
        return

    st.subheader(f"Configurações: {loja_selecionada}")

    # Garante que as chaves de serviço existam e salva se necessário.
    dados_modificados = False
    for svc in ["Bling", "Mercado Livre", "Mercado Turbo"]:
        if svc not in dados[loja_selecionada]:
            dados[loja_selecionada][svc] = {"status": False}
            dados_modificados = True
    
    if dados_modificados:
        salvar_dados_integracao(dados)
        # Não fazer st.rerun() aqui, pois isso causa o loop de vibração.
        # A página continuará a execução com os dados corrigidos em memória.
        # E a correção estará salva para as próximas execuções.

    integracoes = ["Bling", "Mercado Livre", "Mercado Turbo"]
    cols = st.columns(3)

    for i, nome_integ in enumerate(integracoes):
        status = dados[loja_selecionada][nome_integ].get("status", False)
        status_txt = "🟢 CONECTADO" if status else "🔴 DESCONECTADO"
        css_class = "status-conectado" if status else "status-desconectado"

        with cols[i]:
            st.markdown(f"""
            <div class="card-integracao">
                <h3>{nome_integ}</h3>
                <p class="{css_class}">{status_txt}</p>
            </div>
            """, unsafe_allow_html=True)

            label_btn = "Editar Conexão" if status else f"Conectar {nome_integ}"
            with st.expander(label_btn):
                if nome_integ == "Bling":
                    st.caption("Passo 1: Insira Client ID e Secret.")
                    client_id = st.text_input("Client ID", value=dados[loja_selecionada]["Bling"].get("client_id", ""), type="password")
                    client_secret = st.text_input("Client Secret", value=dados[loja_selecionada]["Bling"].get("client_secret", ""), type="password")
                    if st.button("💾 Salvar Credenciais", key=f"save_creds_{loja_selecionada}"):
                        dados[loja_selecionada]["Bling"]["client_id"] = client_id
                        dados[loja_selecionada]["Bling"]["client_secret"] = client_secret
                        salvar_dados_integracao(dados)
                        st.success("Credenciais salvas! Agora clique no botão abaixo para autorizar.")
                        time.sleep(0.5)
                        st.rerun()

                    st.markdown("---")
                    st.caption("Passo 2: Autorizar aplicativo no Bling")
                    cid_salvo = dados[loja_selecionada]["Bling"].get("client_id")
                    if cid_salvo:
                        state_encoded = urllib.parse.quote(loja_selecionada)
                        auth_url = f"https://www.bling.com.br/Api/v3/oauth/authorize?response_type=code&client_id={cid_salvo}&state={state_encoded}"
                        st.markdown(f'<a href="{auth_url}" target="_blank"><button>🔐 AUTORIZAR NO BLING (Abre nova aba)</button></a>', unsafe_allow_html=True)
                        st.info("Após autorizar, você será redirecionado automaticamente para cá e a conexão ficará verde.")
                    else:
                        st.warning("Salve o Client ID acima primeiro.")

                elif nome_integ == "Mercado Livre":
                    app_id = st.text_input("App ID", value=dados[loja_selecionada]["Mercado Livre"].get("app_id", ""))
                    secret_key = st.text_input("Secret Key", value=dados[loja_selecionada]["Mercado Livre"].get("secret_key", ""), type="password")
                    if st.button("Salvar ML", key=f"save_ml_{loja_selecionada}"):
                        dados[loja_selecionada]["Mercado Livre"]["app_id"] = app_id
                        dados[loja_selecionada]["Mercado Livre"]["secret_key"] = secret_key
                        dados[loja_selecionada]["Mercado Livre"]["status"] = bool(app_id and secret_key)
                        salvar_dados_integracao(dados)
                        st.rerun()

                elif nome_integ == "Mercado Turbo":
                    token_turbo = st.text_input("Token", value=dados[loja_selecionada]["Mercado Turbo"].get("token", ""), type="password")
                    if st.button("Salvar Turbo", key=f"save_turbo_{loja_selecionada}"):
                        dados[loja_selecionada]["Mercado Turbo"]["token"] = token_turbo
                        dados[loja_selecionada]["Mercado Turbo"]["status"] = bool(token_turbo)
                        salvar_dados_integracao(dados)
                        st.rerun()


def page_submenu_promo() -> None:
    if st.button("⬅️ Voltar ao Menu"):
        navegar_para('menu')

    st.title("📈 Promoção Mercado Livre")
    st.markdown("---")

    # Aplica o mesmo estilo de botão do menu principal
    st.markdown("""
        <style>
        div.stButton > button {
            height: 130px; width: 100%; font-size: 20px; font-weight: 600;
            border-radius: 12px; border: 1px solid #3a3a45;
            background: linear-gradient(145deg, #25252e, #1a1a21);
            box-shadow: 0 4px 10px rgba(0,0,0,0.4); color: #e0e0e0;
        }
        div.stButton > button:hover {
            border-color: #4facfe; color: #4facfe !important;
            transform: translateY(-4px); background: #2a2a35;
        }
        </style>
    """, unsafe_allow_html=True)
    
    st.markdown("<h5>Selecione um dos módulos abaixo:</h5>", unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)
    
    permissoes = st.session_state.get('permissoes_modulos', {})
    
    col1, col2, col3 = st.columns([1.5, 1.5, 2])

    with col1:
        if permissoes.get('analise_promo', False):
            if st.button("📊 Análise de Promoção", use_container_width=True):
                navegar_para('analise_promo')

    with col2:
        if permissoes.get('renovacao_fixa', False):
            if st.button("🔄 Renovação Fixa", use_container_width=True):
                navegar_para('renovacao_fixa')

    st.markdown("<br><br><br>", unsafe_allow_html=True)


def page_menu() -> None:
    c1, c2 = st.columns([1, 4])
    with c1:
        if os.path.exists(LOGO_PATH):
            st.image(LOGO_PATH, width=130)
    with c2:
        st.markdown('<h1 style="margin-top:10px;">JK Sistema de Gestão</h1>', unsafe_allow_html=True)
    st.markdown("---")

    # Carrega as permissões da sessão. O padrão é usado como fallback.
    permissoes = st.session_state.get('permissoes_modulos', {
        'analise_promo': False, 'renovacao_fixa': False, 'vendas': False, 'estoque': False, 
        'integracao': False, 'etiquetas': False, 'full': False, 'favoritos': False
    })

    st.markdown("""
        <style>
        div.stButton > button {
            height: 130px; width: 100%; font-size: 20px; font-weight: 600;
            border-radius: 12px; border: 1px solid #3a3a45;
            background: linear-gradient(145deg, #25252e, #1a1a21);
            box-shadow: 0 4px 10px rgba(0,0,0,0.4); color: #e0e0e0;
        }
        div.stButton > button:hover {
            border-color: #4facfe; color: #4facfe !important;
            transform: translateY(-4px); background: #2a2a35;
        }
        /* Estilo para botão desativado */
        div.stButton > button:disabled {
            background: linear-gradient(145deg, #2a2a2e, #1f1f21);
            color: #555 !important;
            cursor: not-allowed;
            opacity: 0.5;
        }
        </style>
    """, unsafe_allow_html=True)

    # Adiciona uma "permissão virtual" para o submenu de promoção
    permissoes['submenu_promo'] = permissoes.get('analise_promo', False) or permissoes.get('renovacao_fixa', False)

    # --- Definição dos Módulos ---
    modulos = [
        {"label": "📈 Promoção Mercado Livre", "target": "submenu_promo", "perm_key": "submenu_promo"},
        {"label": "⭐ Favoritos", "target": "favoritos", "perm_key": "favoritos"},
        {"label": "🏷️ Etiquetas", "target": "etiquetas", "perm_key": "etiquetas"},
        {"label": "💲 Vendas", "target": "vendas", "perm_key": "vendas", "check_import": "vendas"},
        {"label": "📦 Estoque", "target": "estoque", "perm_key": "estoque", "check_import": "estoque"},
        {"label": "🟦 Full", "target": "full", "perm_key": "full"},
    ]

    # --- Filtrar Módulos Visíveis ---
    modulos_visiveis = []
    for mod in modulos:
        # Verifica a permissão
        if not permissoes.get(mod["perm_key"], False):
            continue
        # Verifica se o módulo de importação existe, se necessário
        if "check_import" in mod:
            try:
                # O módulo `vendas` é especial
                if mod["check_import"] == "vendas":
                    if not vendas:
                        continue
                else:
                    __import__(mod["check_import"])
            except ImportError:
                continue
        modulos_visiveis.append(mod)

    # --- Renderização Dinâmica em Grade ---
    if not modulos_visiveis:
        st.warning("Nenhum módulo disponível para seu usuário. Contate o administrador.")
        return

    num_colunas = 3
    for i in range(0, len(modulos_visiveis), num_colunas):
        cols = st.columns(num_colunas, gap="medium")
        # Pega a "fatia" de módulos para esta linha
        linha_modulos = modulos_visiveis[i:i + num_colunas]

        for j, mod in enumerate(linha_modulos):
            with cols[j]:
                if st.button(mod["label"], use_container_width=True, key=f"btn_{mod['target']}"):
                    navegar_para(mod["target"])
        
        # Adiciona um espaçamento vertical entre as linhas
        st.markdown("<div style='margin-top: 15px;'></div>", unsafe_allow_html=True)


# ROTEAMENTO FINAL
pagina = st.session_state['pagina_atual']

if pagina == 'menu':
    page_menu()
elif pagina == 'submenu_promo':
    page_submenu_promo()
elif pagina == 'analise_promo':
    promo.render_page(navegar_para)
elif pagina == 'renovacao_fixa':
    renovacao.render_page(navegar_para)
elif pagina == 'favoritos':
    favoritos.render_page(navegar_para)
elif pagina == 'etiquetas':
    etiquetas.render_page(navegar_para)
elif pagina == 'vendas' and vendas:
    vendas.render_page(navegar_para)
elif pagina == 'estoque':
    import estoque
    estoque.render_page(navegar_para)
elif pagina == 'integracao':
    page_integracao()
elif pagina == 'full':
    import full
    full.render_page(navegar_para)
else:
    page_menu()