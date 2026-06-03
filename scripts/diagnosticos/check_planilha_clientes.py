"""
Script de diagnóstico para verificar se a planilha "Clientes" 
está configurada corretamente com as planilhas individuais
"""

import gspread
from google.oauth2.service_account import Credentials
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent
INFO_DIR = BASE_DIR / "info"
CREDENTIALS_FILE = INFO_DIR / "credentials.json"
SPREADSHEET_ID_CLIENTES = '1oyLYMd059baSs2KZJ8jqld68Y03a3pNRs32xvZlowNk'

def autenticar_google():
    """Autentica com Google Sheets"""
    try:
        scopes = [
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive'
        ]
        creds = Credentials.from_service_account_file(str(CREDENTIALS_FILE), scopes=scopes)
        return gspread.authorize(creds)
    except Exception as e:
        print(f"❌ Erro ao autenticar: {e}")
        return None

def diagnosticar_planilha_clientes():
    print("=" * 70)
    print("🔍 DIAGNÓSTICO - PLANILHA 'CLIENTES' E PLANILHAS INDIVIDUAIS")
    print("=" * 70)
    print()
    
    client = autenticar_google()
    if not client:
        print("❌ Não foi possível autenticar com Google Sheets")
        return
    
    try:
        sh = client.open_by_key(SPREADSHEET_ID_CLIENTES)
        print(f"✅ Conectado à planilha: {sh.title}")
        print(f"   URL: {sh.url}")
        print()
    except Exception as e:
        print(f"❌ Erro ao abrir planilha Clientes: {e}")
        return
    
    try:
        ws = sh.worksheet("Clientes")
    except:
        ws = sh.sheet1
    
    print(f"📋 Aba sendo analisada: {ws.title}")
    print()
    
    # Buscar dados
    todos_dados = ws.get_all_values()
    if not todos_dados:
        print("❌ Planilha vazia!")
        return
    
    headers_raw = todos_dados[0]
    headers = [str(h).lower().strip() for h in headers_raw]
    
    print("-" * 70)
    print("📊 ESTRUTURA DA PLANILHA")
    print("-" * 70)
    print("\nColunas encontradas:")
    for i, h in enumerate(headers_raw):
        letra_coluna = chr(65 + i) if i < 26 else f"A{chr(65 + i - 26)}"
        print(f"   {letra_coluna} (índice {i}): '{h}'")
    print()
    
    # Buscar índices importantes
    idx_usuario = -1
    for k in ["usuario", "usuário", "user", "login", "nome"]:
        if k in headers:
            idx_usuario = headers.index(k)
            break
    
    idx_client_id = -1
    for k in ["número do cliente", "numero do cliente", "id cliente", "client id"]:
        if k in headers:
            idx_client_id = headers.index(k)
            break
    
    idx_planilha = -1
    for k in ["planilha jk", "planilha", "spreadsheet id", "sheet id", "id planilha"]:
        if k in headers:
            idx_planilha = headers.index(k)
            break
    
    print("-" * 70)
    print("🔑 COLUNAS CRÍTICAS")
    print("-" * 70)
    
    if idx_usuario == -1:
        print("❌ Coluna 'Usuário' NÃO encontrada!")
    else:
        print(f"✅ Coluna 'Usuário': {chr(65 + idx_usuario)} (índice {idx_usuario})")
    
    if idx_client_id == -1:
        print("❌ Coluna 'Número do Cliente' NÃO encontrada!")
    else:
        print(f"✅ Coluna 'Número do Cliente': {chr(65 + idx_client_id)} (índice {idx_client_id})")
    
    if idx_planilha == -1:
        print("❌ Coluna 'Planilha JK' NÃO encontrada!")
        print("   ⚠️  Sistema usará fallback: Coluna I (índice 8)")
        idx_planilha = 8
    else:
        print(f"✅ Coluna 'Planilha JK': {chr(65 + idx_planilha)} (índice {idx_planilha})")
    
    print()
    
    # Verificar usuários
    print("-" * 70)
    print("👥 USUÁRIOS E SUAS PLANILHAS")
    print("-" * 70)
    print()
    
    usuarios_info = []
    
    for i, row in enumerate(todos_dados[1:], start=2):
        if idx_usuario == -1 or len(row) <= idx_usuario:
            continue
            
        usuario = str(row[idx_usuario]).strip()
        if not usuario:
            continue
        
        # Client ID
        client_id = ""
        if idx_client_id != -1 and len(row) > idx_client_id:
            client_id = str(row[idx_client_id]).strip()
        
        # Planilha JK
        planilha_jk = ""
        if len(row) > idx_planilha:
            planilha_jk = str(row[idx_planilha]).strip()
        
        usuarios_info.append({
            'linha': i,
            'usuario': usuario,
            'client_id': client_id,
            'planilha_jk': planilha_jk
        })
    
    if not usuarios_info:
        print("❌ Nenhum usuário encontrado na planilha!")
        return
    
    print(f"Total de usuários: {len(usuarios_info)}\n")
    
    for info in usuarios_info:
        print(f"📍 Linha {info['linha']}: {info['usuario']}")
        
        if not info['client_id']:
            print(f"   ❌ Número do Cliente: NÃO PREENCHIDO")
        else:
            print(f"   ✅ Número do Cliente: {info['client_id']}")
        
        if not info['planilha_jk']:
            print(f"   ❌ Planilha JK (coluna {chr(65 + idx_planilha)}): NÃO PREENCHIDA")
            print(f"      ⚠️  Usará planilha fixa do sistema")
        else:
            print(f"   ✅ Planilha JK: {info['planilha_jk']}")
            # Tentar acessar a planilha
            try:
                sh_user = client.open_by_key(info['planilha_jk'])
                print(f"      ✅ Planilha acessível: {sh_user.title}")
                print(f"      🔗 URL: {sh_user.url}")
            except Exception as e:
                print(f"      ❌ ERRO ao acessar planilha: {e}")
                print(f"      💡 Verifique se o email do credentials.json tem permissão de EDITOR")
        
        # Verificar arquivo local
        if info['client_id']:
            config_path = INFO_DIR / info['client_id'] / "config_sheet.json"
            if config_path.exists():
                import json
                try:
                    with open(config_path, 'r') as f:
                        data = json.load(f)
                        saved_id = data.get('spreadsheet_id', '')
                        if saved_id:
                            print(f"   📁 Arquivo local: {config_path}")
                            print(f"      Planilha salva: {saved_id}")
                            if saved_id != info['planilha_jk']:
                                print(f"      ⚠️  DIFERENTE da planilha na coluna!")
                except:
                    pass
        
        print()
    
    print("=" * 70)
    print("✅ DIAGNÓSTICO CONCLUÍDO")
    print("=" * 70)
    print()
    print("📌 AÇÕES NECESSÁRIAS:")
    print()
    print("1. Se a coluna 'Planilha JK' NÃO foi encontrada:")
    print("   → Adicione uma coluna com o título 'Planilha JK' na planilha Clientes")
    print("   → OU certifique-se que a coluna I (índice 8) existe")
    print()
    print("2. Para cada usuário com 'Planilha JK: NÃO PREENCHIDA':")
    print("   → Abra a planilha Clientes no Google Sheets")
    print("   → Na coluna 'Planilha JK', cole o ID da planilha do usuário")
    print("   → Formato do ID: chave longa da URL (ex: 1Kj8ioVDpjLDH2kTSKWvBYKX4-R5zryTj2Ka5_G2irO0)")
    print()
    print("3. Verifique permissões:")
    print("   → O email do credentials.json deve ter permissão de EDITOR em TODAS as planilhas")
    print()
    print("4. Após corrigir, execute novamente:")
    print("   python check_planilha_clientes.py")
    print()

if __name__ == "__main__":
    diagnosticar_planilha_clientes()
