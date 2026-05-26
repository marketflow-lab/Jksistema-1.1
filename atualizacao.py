import streamlit as st
import os
import sys
import shutil
import re
import time
import subprocess
import requests
import gspread
from google.oauth2.service_account import Credentials

# ==============================================================================
# --- CONFIGURAÇÕES ---
# ==============================================================================

CREDENTIALS_FILE = 'credentials.json'
SPREADSHEET_ID_CLIENTES = '1oyLYMd059baSs2KZJ8jqld68Y03a3pNRs32xvZlowNk' 

# ==============================================================================
# --- FUNÇÕES AUXILIARES (BACKEND) ---
# ==============================================================================

def autenticar_google_sheets():
    """Autenticação específica para o módulo de atualização."""
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    if not os.path.exists(CREDENTIALS_FILE):
        return None
    try:
        creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scopes)
        return gspread.authorize(creds)
    except Exception:
        return None

def verificar_atualizacao(client_google, usuario_logado=None):
    """
    Verifica atualização Global (Aba Config) e Individual (Aba Clientes).
    Retorna: versao_nuvem, link, mensagem, forcar_global, forcar_individual
    """
    try:
        sh = client_google.open_by_key(SPREADSHEET_ID_CLIENTES)
        
        # --- 1. VERIFICAÇÃO GLOBAL (Aba Config) ---
        versao_nuvem = None
        link_download = None
        mensagem = ""
        forcar_global = False
        
        try:
            ws_config = sh.worksheet("Config")
            # B1: Versão, B2: Link, B3: Msg, B4: Forçar Global (SIM/NAO)
            dados_config = ws_config.get_values("B1:B4")
            
            if dados_config and len(dados_config) >= 2:
                versao_nuvem = str(dados_config[0][0]).strip()
                link_download = str(dados_config[1][0]).strip()
                if len(dados_config) > 2: mensagem = str(dados_config[2][0]).strip()
                if len(dados_config) > 3: 
                    forcar_global = (str(dados_config[3][0]).strip().upper() == "SIM")
        except:
            pass 

        # --- 2. VERIFICAÇÃO INDIVIDUAL (Aba Clientes) ---
        forcar_individual = False
        if usuario_logado:
            try:
                ws_clientes = sh.worksheet("Clientes")
                registros = ws_clientes.get_all_records()
                for row in registros:
                    u = str(row.get('Usuario', '')).strip()
                    if u == usuario_logado:
                        flag = str(row.get('ForcarUpdate', '')).strip().upper()
                        if flag == "SIM":
                            forcar_individual = True
                        break
            except Exception:
                pass

        return versao_nuvem, link_download, mensagem, forcar_global, forcar_individual

    except Exception as e:
        print(f"Erro ao verificar atualização: {e}")
        return None, None, None, False, False

def realizar_atualizacao(link_download, versao_atual_sistema):
    """
    Faz backup, baixa nova versão (usando gdown ou requests) e cria o .bat instalador.
    """
    try:
        st.info("Iniciando processo de atualização...")

        # 1. Extrair ID do Google Drive
        file_id = None
        match = re.search(r'[?&]id=([a-zA-Z0-9_-]+)|/d/([a-zA-Z0-9_-]+)', link_download)
        if match:
            file_id = match.group(1) or match.group(2)
        
        if not file_id:
            st.error("Não foi possível identificar o ID do arquivo no link fornecido.")
            return False

        # 2. Criar Backup
        pasta_backup = f"Backup_v{versao_atual_sistema}"
        if not os.path.exists(pasta_backup):
            try:
                os.makedirs(pasta_backup)
                # Faz backup do arquivo principal atual (seja app.py ou main.py)
                current_file = os.path.basename(sys.argv[0])
                if os.path.exists(current_file):
                    shutil.copy(current_file, os.path.join(pasta_backup, current_file))
                    st.toast(f"Backup criado na pasta: {pasta_backup}", icon="💾")
            except Exception:
                pass 
        
        # 3. Preparar Download
        nome_arquivo_temp = "atualizacao_temp.txt"
        st.info("Baixando nova versão do servidor (Modo Robusto)...")
        
        # --- TENTATIVA 1: Usando GDOWN (Prioritário para Google Drive) ---
        sucesso_download = False
        try:
            try:
                import gdown
            except ImportError:
                st.warning("Instalando componente de download seguro...")
                subprocess.check_call([sys.executable, "-m", "pip", "install", "gdown"])
                import gdown
            
            url_gdown = f'https://drive.google.com/uc?id={file_id}'
            
            if os.path.exists(nome_arquivo_temp):
                os.remove(nome_arquivo_temp)
                
            out = gdown.download(url_gdown, nome_arquivo_temp, quiet=False, fuzzy=True)
            
            if out:
                sucesso_download = True
                
        except Exception as e_gdown:
            print(f"Gdown falhou: {e_gdown}")
            sucesso_download = False

        # --- TENTATIVA 2: Requests com User-Agent (Fallback) ---
        if not sucesso_download:
            st.info("Tentando método alternativo de download...")
            URL_BASE = "https://docs.google.com/uc?export=download"
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Connection': 'keep-alive',
            }
            
            session = requests.Session()
            response = session.get(URL_BASE, params={'id': file_id}, headers=headers, stream=True)
            
            # Busca token de confirmação (para arquivos grandes)
            token = None
            for key, value in response.cookies.items():
                if key.startswith('download_warning'):
                    token = value
                    break
            
            if token:
                params = {'id': file_id, 'confirm': token}
                response = session.get(URL_BASE, params=params, headers=headers, stream=True)
            
            with open(nome_arquivo_temp, "wb") as f:
                for chunk in response.iter_content(chunk_size=32768):
                    if chunk: f.write(chunk)
            
            sucesso_download = True

        # 4. VALIDAÇÃO
        if not os.path.exists(nome_arquivo_temp) or os.path.getsize(nome_arquivo_temp) < 100:
             st.error("Erro: O arquivo baixado está vazio ou corrompido.")
             return False

        # Verifica se baixou um HTML de erro em vez do código
        try:
            with open(nome_arquivo_temp, 'r', encoding='utf-8', errors='ignore') as f:
                inicio = f.read(500)
                if "<!DOCTYPE html>" in inicio or "<html" in inicio.lower():
                    st.error("Falha Crítica: O Google Drive bloqueou o acesso. Verifique as permissões do link.")
                    return False
        except:
            pass 

        # 5. Criar script BAT para realizar a troca
        # O script espera 3 segundos, deleta o app atual e renomeia o temp para o nome original
        current_script_name = "main.py" # Assumindo que o arquivo principal será main.py
        
        bat_content = f"""
@echo off
timeout /t 3 >nul
if exist "{current_script_name}" del "{current_script_name}"
if exist "{nome_arquivo_temp}" ren "{nome_arquivo_temp}" "{current_script_name}"
start Executar.bat
del "%~f0"
"""
        with open("update_install.bat", "w") as bat_file:
            bat_file.write(bat_content)

        return True
    except Exception as e:
        st.error(f"Erro na atualização: {e}")
        return False

# ==============================================================================
# --- COMPONENTE DE UI (SIDEBAR) ---
# ==============================================================================

def render_sidebar_update_check(usuario_logado, versao_sistema_atual):
    """
    Componente visual para ser colocado na Sidebar.
    Verifica atualizações e exibe botão ou inicia update forçado.
    """
    try:
        # Tenta autenticar (pode ser redundante, mas garante que o update funcione isolado)
        client_att = autenticar_google_sheets()
        
        if client_att:
            v_nuvem, v_link, v_msg, force_global, force_individual = verificar_atualizacao(
                client_att, 
                usuario_logado=usuario_logado
            )
            
            # Verifica se a versão da nuvem é diferente da atual
            tem_update = (v_nuvem and str(v_nuvem).strip() != versao_sistema_atual)
            
            if tem_update:
                # CASO 1: ATUALIZAÇÃO FORÇADA
                if force_individual or force_global:
                    st.toast("⚠️ Atualização Crítica Iniciada...", icon="🔄")
                    st.info(f"Atualizando para versão {v_nuvem}. Aguarde...")
                    
                    with st.spinner(f"Baixando atualização..."):
                        sucesso = realizar_atualizacao(v_link, versao_sistema_atual)
                        if sucesso:
                            subprocess.Popen("update_install.bat", shell=True)
                            time.sleep(2)
                            st.stop()
                            sys.exit()

                # CASO 2: ATUALIZAÇÃO MANUAL (BOTÃO)
                else:
                    st.warning(f"🚀 **Atualização Disponível!**\n\nVersão: {v_nuvem}\n\n{v_msg}")
                    
                    if st.button("🔄 ATUALIZAR SISTEMA", key="btn_update_now", type="primary"):
                        with st.spinner(f"Instalando versão {v_nuvem}..."):
                            sucesso = realizar_atualizacao(v_link, versao_sistema_atual)
                            if sucesso:
                                subprocess.Popen("update_install.bat", shell=True)
                                time.sleep(1)
                                st.stop()
                                sys.exit()

    except Exception as e:
        # Falha silenciosa na sidebar para não travar o uso do sistema
        pass