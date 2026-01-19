import json
import streamlit as st
import streamlit_authenticator as stauth
import gspread
from google.oauth2.service_account import Credentials
import bcrypt
import socket
import datetime
from datetime import datetime
import os
import time

# ==============================================================================
# --- CONFIGURAÇÕES ---
# ==============================================================================
PASTA_INFO = "info"
PASTA_IMG = "img"
CREDENTIALS_FILE = os.path.join(PASTA_INFO, 'credentials.json')
LOGO_PATH = os.path.join(PASTA_IMG, "Logo Trend.png")
# Arquivo salvo na ativação que contém o spreadsheet_id a ser usado pelo app
CONFIG_FILE = os.path.join(PASTA_INFO, 'config_sheet.json')
# Planilha fixa de clientes para autenticação de login
SPREADSHEET_ID_CLIENTES = '1oyLYMd059baSs2KZJ8jqld68Y03a3pNRs32xvZlowNk'

# ==============================================================================
# --- FUNÇÕES DE BACKEND (COM CACHE) ---
# ==============================================================================

@st.cache_resource(show_spinner="Conectando ao Google...")
def autenticar_google_sheets():
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    if not os.path.exists(CREDENTIALS_FILE):
        return None
    try:
        creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scopes)
        return gspread.authorize(creds)
    except Exception as e:
        print(f"Erro Auth Google: {e}")
        return None

def verificar_validade_acesso(username):
    """
    Verifica se a validade de acesso do usuário não expirou.
    Retorna (True/False, mensagem)
    """
    try:
        client = autenticar_google_sheets()
        if not client:
            return True, "Não foi possível verificar validade (erro Google)"
        
        sh = client.open_by_key(SPREADSHEET_ID_CLIENTES)
        ws = sh.worksheet("Clientes")
        
        todos_dados = ws.get_all_values()
        if not todos_dados:
            return True, "Planilha vazia"
        
        headers = [str(h).lower().strip() for h in todos_dados[0]]
        
        # Encontrar índices das colunas
        try:
            idx_usuario = headers.index("usuario")
        except ValueError:
            try:
                idx_usuario = headers.index("nome ")
            except ValueError:
                idx_usuario = headers.index("nome") if "nome" in headers else 0
        
        idx_validade = headers.index("validade") if "validade" in headers else -1
        
        if idx_validade < 0:
            print("[DEBUG] Coluna 'Validade' não encontrada")
            return True, "Coluna de validade não configurada"
        
        # Procurar pela linha do usuário
        for row in todos_dados[1:]:
            if row and str(row[idx_usuario]).lower().strip() == username.lower():
                data_validade_str = str(row[idx_validade]).strip()
                
                if not data_validade_str:
                    print(f"[DEBUG] Sem data de validade para {username}")
                    return False, "❌ Acesso sem validade configurada. Contate o administrador."
                
                try:
                    # Tentar parse em formato DD/MM/YYYY
                    data_validade = datetime.strptime(data_validade_str, "%d/%m/%Y")
                    data_atual = datetime.now()
                    
                    if data_atual > data_validade:
                        print(f"[DEBUG] Acesso expirado para {username}: {data_validade_str}")
                        return False, f"❌ Acesso expirado em {data_validade_str}. Contate o administrador."
                    else:
                        dias_restantes = (data_validade - data_atual).days
                        if dias_restantes <= 7:
                            print(f"[DEBUG] Acesso vencendo em {dias_restantes} dias para {username}")
                            return True, f"⚠️ Acesso vence em {dias_restantes} dias ({data_validade_str})"
                        else:
                            print(f"[DEBUG] Acesso válido até {data_validade_str} para {username}")
                            return True, f"✅ Acesso válido até {data_validade_str}"
                except ValueError as e:
                    print(f"[DEBUG] Erro ao parse data '{data_validade_str}': {e}")
                    return False, f"❌ Formato de data inválido: {data_validade_str}"
        
        return False, "❌ Usuário não encontrado na planilha"
        
    except Exception as e:
        print(f"[DEBUG] Erro ao verificar validade: {e}")
        return True, f"⚠️ Erro ao verificar validade (continuando): {e}"


def carregar_permissoes_modulos(username):
    """
    Carrega as permissões de módulo para o usuário a partir da planilha.
    Retorna um dicionário com as permissões.
    """
    # Por padrão, todos os módulos são negados. Usado como fallback.
    permissoes_padrao = {
        'analise_promo': False,
        'renovacao_fixa': False,
        'vendas': False,
        'estoque': False,
        'integracao': False,
        'etiquetas': False,
        'full': False,
        'favoritos': False,
    }
    try:
        client = autenticar_google_sheets()
        if not client:
            print("[WARN] Não foi possível carregar permissões (erro Google), usando padrão.")
            return permissoes_padrao

        sh = client.open_by_key(SPREADSHEET_ID_CLIENTES)
        ws = sh.worksheet("Clientes")
        todos_dados = ws.get_all_values()
        if not todos_dados:
            return permissoes_padrao

        headers = [str(h).lower().strip() for h in todos_dados[0]]
        try:
            idx_usuario = headers.index("usuario")
        except ValueError:
            idx_usuario = headers.index("nome") if "nome" in headers else 0

        # Mapping from column index (0-based) to module name based on user feedback
        MODULOS_MAP = {
            10: 'analise_promo',   # Col K: Análise Promocional ML
            11: 'renovacao_fixa',  # Col L: Renovação Fixa
            12: 'vendas',          # Col M: Vendas
            13: 'estoque',         # Col N: Estoque
            14: 'integracao',      # Col O: Integração
            15: 'etiquetas',       # Col P: Etiquetas
            16: 'full',            # Col Q: Full
            17: 'favoritos',       # Col R: Favoritos
        }

        for row in todos_dados[1:]:
            if row and len(row) > idx_usuario and str(row[idx_usuario]).lower().strip() == username.lower():
                print(f"[DEBUG] Encontrada linha de permissões para o usuário {username}")
                
                # Cria um novo dicionário de permissões para o usuário encontrado
                user_permissions = {}
                for col_idx, mod_name in MODULOS_MAP.items():
                    is_allowed = False  # Permissão é falsa por padrão
                    if len(row) > col_idx:
                        valor = str(row[col_idx]).upper().strip()
                        # O módulo só é ativado se a célula contiver "VERDADEIRO" ou "TRUE"
                        if valor == 'VERDADEIRO' or valor == 'TRUE':
                            is_allowed = True
                    user_permissions[mod_name] = is_allowed
                
                print(f"[DEBUG] Permissões carregadas para {username}: {user_permissions}")
                return user_permissions
        
        print(f"[WARN] Usuário {username} não encontrado para carregar permissões, usando padrão (tudo falso).")
        return permissoes_padrao

    except Exception as e:
        print(f"[ERROR] Erro ao carregar permissões: {e}")
        # Em caso de erro, retorna as permissões padrão para não travar o app
        return permissoes_padrao


def verificar_trava_seguranca(username):
    """
    Verifica se o usuário pode logar nesta máquina específica.
    Registra a máquina e data/hora do acesso na planilha.
    """
    try:
        hostname = socket.gethostname()
        data_hora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        
        # Tenta atualizar a planilha com informações do acesso
        try:
            client = autenticar_google_sheets()
            if client:
                sh = client.open_by_key(SPREADSHEET_ID_CLIENTES)
                ws = sh.worksheet("Clientes")
                
                # Buscar todas as linhas para encontrar o usuário
                todos_dados = ws.get_all_values()
                if todos_dados:
                    headers = [str(h).lower().strip() for h in todos_dados[0]]
                    
                    # Encontrar índices das colunas
                    try:
                        idx_usuario = headers.index("usuario")
                    except ValueError:
                        try:
                            idx_usuario = headers.index("nome ")
                        except ValueError:
                            idx_usuario = headers.index("nome") if "nome" in headers else 0
                    
                    idx_maquina = headers.index("maquina") if "maquina" in headers else -1
                    idx_acesso = headers.index("ultimo acesso") if "ultimo acesso" in headers else -1
                    
                    # Procurar pela linha do usuário (considerando case-insensitive)
                    for i, row in enumerate(todos_dados[1:], start=2):  # Start at line 2 (headers are line 1)
                        if row and str(row[idx_usuario]).lower().strip() == username.lower():
                            # Atualizar máquina
                            if idx_maquina >= 0:
                                ws.update_cell(i, idx_maquina + 1, hostname)
                                print(f"[DEBUG] Máquina '{hostname}' registrada para {username}")
                            
                            # Atualizar último acesso
                            if idx_acesso >= 0:
                                ws.update_cell(i, idx_acesso + 1, data_hora)
                                print(f"[DEBUG] Acesso '{data_hora}' registrado para {username}")
                            break
        except Exception as e:
            print(f"[DEBUG] Erro ao atualizar dados de acesso: {e}")
        
        return True, f"✅ Acesso liberado para {username} em {hostname}"
    except Exception as e:
        return True, f"✅ Acesso liberado (erro ao registrar hardware: {e})"

def carregar_usuarios_sheets():
    """
    Lê a planilha de clientes de forma robusta, aceitando variações nos nomes das colunas.
    """
    client = autenticar_google_sheets()
    if not client:
        print("[ERRO] Cliente Google não autenticado.")
        return None

    try:
        print(f"[DEBUG] Usando planilha de clientes: {SPREADSHEET_ID_CLIENTES}")
        sh = client.open_by_key(SPREADSHEET_ID_CLIENTES)
        
        try:
            ws = sh.worksheet("Clientes")
            print("[DEBUG] Aba 'Clientes' encontrada")
        except:
            ws = sh.sheet1
            print("[DEBUG] Usando primeira aba disponível")
            
        registros = ws.get_all_records()
        print(f"[DEBUG] {len(registros)} registros carregados")
        
        # DEBUG: Mostra as colunas encontradas para ajudar a diagnosticar erros
        if registros:
            print(f"[DEBUG] Colunas encontradas na planilha: {list(registros[0].keys())}")
        
        credentials = {"usernames": {}}

        # Listas de tentativas para nomes de colunas (Case sensitive e variations)
        keys_user = ["Usuário", "Usuario", "User", "usuario", "user", "Login"]
        keys_pass = ["Senha", "Password", "senha", "password", "Pass"]
        keys_name = ["Nome", "Name", "nome"]
        keys_email = ["Email", "E-mail", "email"]

        def get_val(row, keys):
            for k in keys:
                if k in row:
                    return str(row[k]).strip()
            return ""

        for row in registros:
            user = get_val(row, keys_user)
            raw_pw = get_val(row, keys_pass)
            name = get_val(row, keys_name) or user
            email = get_val(row, keys_email)
            
            if user and raw_pw:
                # Converte username para lowercase (streamlit-authenticator faz isso automaticamente)
                user_key = user.lower()
                
                # Se a senha não começar com $2 (bcrypt), gera o hash com bcrypt
                if not str(raw_pw).startswith("$2"):
                    try:
                        hashed_pw = bcrypt.hashpw(str(raw_pw).encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
                    except Exception as e:
                        print(f"[ERRO] Falha hash {user}: {e}")
                        hashed_pw = raw_pw 
                else:
                    hashed_pw = raw_pw
                
                credentials["usernames"][user_key] = {
                    "email": email,
                    "name": name,
                    "password": hashed_pw
                }
        
        if not credentials["usernames"]:
            print("[ERRO CRÍTICO] Nenhum usuário válido encontrado. Verifique os nomes das colunas na planilha.")
            return None

        print(f"[DEBUG] Credenciais carregadas para: {list(credentials['usernames'].keys())}")
        return credentials

    except Exception as e:
        print(f"[ERRO CRÍTICO] Falha ao carregar usuários: {e}")
        return None

# ==============================================================================
# --- COMPONENTE DE LOGIN (FRONTEND) ---
# ==============================================================================
def render_login_component():
    creds = carregar_usuarios_sheets()
    
    if not creds:
        col1, col2, col3 = st.columns([1,2,1])
        with col2:
            st.error("⚠️ Erro: Nenhum usuário encontrado na planilha.")
            st.info("Verifique se as colunas 'Usuário' e 'Senha' existem na planilha.")
            if st.button("Tentar Conectar Novamente"):
                st.cache_resource.clear()
                st.rerun()
        return None, False

    # IMPORTANTE: Cookie name deve ser genérico (sem versão) para permitir transição suave
    # Se há erro anterior, o cookie será simplesmente ignorado
    authenticator = stauth.Authenticate(
        creds,
        "jk_gestor", 
        "jk_sistema_secret_key", 
        cookie_expiry_days=30
    )

    # Renderizar UI do login
    try:
        authenticator.login("main")
    except Exception as e:
        print(f"[ERRO] authenticator.login() exception: {e}")
        st.error("Erro no login. Tente novamente.")
        return None, False
    
    # Verificar status de autenticação (agora em st.session_state)
    authentication_status = st.session_state.get("authentication_status", None)
    username = st.session_state.get("username", None)
    name = st.session_state.get("name", None)

    if authentication_status is True:
        # PRIMEIRO: Verificar se o acesso não expirou (APENAS UMA VEZ POR SESSÃO)
        if 'validade_verificada' not in st.session_state:
            print("[DEBUG] Verificando validade do acesso (primeira vez na sessão)...")
            validade_ok, msg_validade = verificar_validade_acesso(username)
            st.session_state['msg_validade'] = msg_validade
            st.session_state['validade_ok'] = validade_ok
            st.session_state['validade_verificada'] = True

        # Carrega as permissões dos módulos para a sessão
        if 'permissoes_modulos' not in st.session_state:
            permissoes = carregar_permissoes_modulos(username)
            st.session_state['permissoes_modulos'] = permissoes
        
        # Agora, usa os valores salvos na sessão para decidir o que fazer
        if not st.session_state.get('validade_ok', False):
            st.error(st.session_state.get('msg_validade'))
            st.markdown("---")
            
            col_btn, col_space = st.columns([1, 2])
            with col_btn:
                if st.button("🔄 Fazer novo login", use_container_width=True, key="btn_novo_login"):
                    print("[DEBUG] Limpando session state para novo login...")
                    # Limpa chaves da sessão, incluindo as novas de validade
                    keys_to_delete = [
                        'authentication_status', 'username', 'name', 'logout', 
                        'hardware_validado', 'validade_verificada', 'msg_validade', 'validade_ok'
                    ]
                    for key in keys_to_delete:
                        if key in st.session_state:
                            del st.session_state[key]
                            print(f"[DEBUG] Deletado: {key}")
                    
                    st.cache_resource.clear()
                    print("[DEBUG] Cache de recursos limpo.")
                    st.rerun()
            
            return authenticator, False
        
        # Se a validade está OK, a mensagem não é mais exibida aqui, apenas na sidebar.
        # O código continua para a verificação de hardware.
        
        # SEGUNDO: Verificar hardware e registrar acesso
        if 'hardware_validado' not in st.session_state:
            aut, msg = verificar_trava_seguranca(username)
            if aut:
                st.session_state['hardware_validado'] = True
                st.toast(msg, icon="✅")
            else:
                st.error(msg)
                if st.button("Sair"):
                    st.session_state.authentication_status = None
                    st.session_state.username = None
                    st.session_state.name = None
                    if 'hardware_validado' in st.session_state:
                        del st.session_state['hardware_validado']
                    st.rerun()
                return authenticator, False
        
        return authenticator, True

    elif authentication_status is False:
        st.error("❌ Usuário ou senha incorretos")
        return authenticator, False

    elif authentication_status is None:
        col1, col2, col3 = st.columns([1,1,1])
        with col2:
             if os.path.exists(LOGO_PATH): 
                 st.image(LOGO_PATH, use_container_width=True)
        st.warning("Insira suas credenciais para acessar o sistema.")
        return authenticator, False