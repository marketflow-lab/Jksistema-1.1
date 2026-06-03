#!/usr/bin/env python3
"""
Script de diagnóstico para problemas de autenticação com Google Sheets
Execute: python diagnostico_login.py
"""

import os
import json
import sys

def verificar_arquivos():
    print("\n" + "="*60)
    print("1. VERIFICAÇÃO DE ARQUIVOS")
    print("="*60)
    
    CREDENTIALS_FILE = os.path.join("info", "credentials.json")
    CONFIG_FILE = os.path.join("info", "config_sheet.json")
    
    # Verificar credentials.json
    if os.path.exists(CREDENTIALS_FILE):
        print(f"✅ {CREDENTIALS_FILE} existe")
        try:
            with open(CREDENTIALS_FILE, 'r', encoding='utf-8') as f:
                creds = json.load(f)
            print(f"   - Type: {creds.get('type', 'N/A')}")
            print(f"   - Project ID: {creds.get('project_id', 'N/A')}")
            print(f"   - Client email: {creds.get('client_email', 'N/A')}")
            print(f"   - Válido: SIM ✅")
        except Exception as e:
            print(f"   ❌ Erro ao ler credentials.json: {e}")
    else:
        print(f"❌ {CREDENTIALS_FILE} NÃO EXISTE")
        print("   → Solução: Adicione o JSON da service account na pasta info/")
    
    # Verificar config_sheet.json
    if os.path.exists(CONFIG_FILE):
        print(f"\n✅ {CONFIG_FILE} existe")
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            spreadsheet_id = cfg.get('spreadsheet_id', '')
            print(f"   - Spreadsheet ID: {spreadsheet_id}")
            if spreadsheet_id:
                print(f"   - Válido: SIM ✅")
            else:
                print(f"   - ❌ Spreadsheet ID vazio!")
        except Exception as e:
            print(f"   ❌ Erro ao ler config_sheet.json: {e}")
    else:
        print(f"\n❌ {CONFIG_FILE} NÃO EXISTE")
        print("   → Solução: Execute o app e ative-o com o ID da planilha")

def verificar_conexao_google():
    print("\n" + "="*60)
    print("2. TESTE DE CONEXÃO COM GOOGLE")
    print("="*60)
    
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        print("✅ Módulos importados com sucesso")
    except ImportError as e:
        print(f"❌ Erro ao importar módulos: {e}")
        print("   → Execute: pip install -r requirements.txt")
        return
    
    CREDENTIALS_FILE = os.path.join("info", "credentials.json")
    
    if not os.path.exists(CREDENTIALS_FILE):
        print("❌ credentials.json não encontrado")
        return
    
    try:
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
        creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scopes)
        client = gspread.authorize(creds)
        print("✅ Autenticação com Google bem-sucedida")
    except Exception as e:
        print(f"❌ Erro na autenticação: {e}")
        return

def verificar_planilha():
    print("\n" + "="*60)
    print("3. TESTE DE ACESSO À PLANILHA")
    print("="*60)
    
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        print("❌ Módulos não instalados")
        return
    
    CREDENTIALS_FILE = os.path.join("info", "credentials.json")
    CONFIG_FILE = os.path.join("info", "config_sheet.json")
    
    if not os.path.exists(CREDENTIALS_FILE) or not os.path.exists(CONFIG_FILE):
        print("❌ Arquivos de configuração não encontrados")
        return
    
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        spreadsheet_id = cfg.get('spreadsheet_id')
        
        if not spreadsheet_id:
            print("❌ Spreadsheet ID vazio")
            return
        
        print(f"Tentando acessar planilha: {spreadsheet_id}")
        
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
        creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scopes)
        client = gspread.authorize(creds)
        
        sh = client.open_by_key(spreadsheet_id)
        print(f"✅ Planilha aberta: {sh.title}")
        
        # Listar abas
        abas = [ws.title for ws in sh.worksheets()]
        print(f"\n📋 Abas disponíveis:")
        for aba in abas:
            print(f"   - {aba}")
        
        # Procurar aba "Clientes"
        if "Clientes" in abas:
            print(f"\n✅ Aba 'Clientes' encontrada")
            ws = sh.worksheet("Clientes")
            registros = ws.get_all_records()
            print(f"   - Linhas com dados: {len(registros)}")
            
            if registros:
                print(f"   - Colunas esperadas: Usuario, Senha, Nome, Validade")
                primeira_linha = registros[0]
                colunas_encontradas = list(primeira_linha.keys())
                print(f"   - Colunas encontradas: {', '.join(colunas_encontradas)}")
        else:
            print(f"\n❌ Aba 'Clientes' NÃO ENCONTRADA")
            print(f"   → Solução: Crie uma aba chamada 'Clientes' na planilha")
        
    except Exception as e:
        print(f"❌ Erro ao acessar planilha: {e}")

def main():
    print("\n")
    print("╔════════════════════════════════════════════════════════╗")
    print("║     DIAGNÓSTICO DE AUTENTICAÇÃO - JK GESTOR           ║")
    print("╚════════════════════════════════════════════════════════╝")
    
    verificar_arquivos()
    verificar_conexao_google()
    verificar_planilha()
    
    print("\n" + "="*60)
    print("RESUMO DE SOLUÇÕES")
    print("="*60)
    print("""
Se encontrou problemas:

1. **Arquivo credentials.json não existe:**
   - Baixe da Google Cloud Console
   - Coloque em: info/credentials.json

2. **Spreadsheet ID inválido:**
   - Abra o app e ative com ID correto
   - Ou edite: info/config_sheet.json

3. **Aba 'Clientes' não existe:**
   - Crie a aba na planilha do Google Sheets
   - Adicione colunas: Usuario | Senha | Nome | Validade

4. **Erro de permissões:**
   - Verifique Google Cloud Console
   - Garanta que a service account tem acesso à planilha

5. **Depois corrija os problemas e execute novamente:**
   streamlit run app.py
    """)
    print("="*60 + "\n")

if __name__ == "__main__":
    main()
