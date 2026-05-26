"""
Script de diagnóstico das integrações de Mercado Livre
Verifica quais lojas têm OAuth completo e quais precisam de configuração
"""

import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent
INFO_DIR = BASE_DIR / "info"

def check_ml_integration():
    print("=" * 60)
    print("🔍 DIAGNÓSTICO DE INTEGRAÇÕES - MERCADO LIVRE")
    print("=" * 60)
    print()
    
    # Verificar arquivo global (legado)
    global_file = INFO_DIR / "integracoes.json"
    if global_file.exists():
        print("📄 Arquivo global: info/integracoes.json")
        with open(global_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        for loja_nome, configs in data.items():
            ml_cfg = configs.get("Mercado Livre", {})
            print(f"\n🏢 Loja: {loja_nome}")
            print(f"   Status: {ml_cfg.get('status', False)}")
            
            has_app_id = bool(ml_cfg.get('app_id') or ml_cfg.get('id'))
            has_secret = bool(ml_cfg.get('secret_key') or ml_cfg.get('secret'))
            has_token = bool(ml_cfg.get('access_token'))
            has_user_id = bool(ml_cfg.get('user_id'))
            
            print(f"   ✅ App ID: {has_app_id}")
            print(f"   ✅ Secret: {has_secret}")
            print(f"   {'✅' if has_token else '❌'} Access Token: {has_token}")
            print(f"   {'✅' if has_user_id else '❌'} User ID: {has_user_id}")
            
            if has_token and has_user_id:
                print(f"   🎉 OAUTH COMPLETO - Pronto para usar!")
                print(f"      User ID: {ml_cfg.get('user_id')}")
            elif has_app_id and has_secret:
                print(f"   ⚠️  OAUTH INCOMPLETO - Execute autenticação na página de Integrações")
            else:
                print(f"   ❌ CONFIGURAÇÃO INCOMPLETA - Faltam credenciais")
    
    print("\n" + "-" * 60)
    
    # Verificar pastas multi-tenant
    print("\n📁 Verificando configurações multi-tenant (info/<client_id>/)")
    
    tenant_folders = [d for d in INFO_DIR.iterdir() if d.is_dir() and d.name.isdigit()]
    
    if not tenant_folders:
        print("   ℹ️  Nenhuma pasta de tenant encontrada")
    else:
        for tenant_folder in tenant_folders:
            client_id = tenant_folder.name
            config_file = tenant_folder / "lojas_config.json"
            
            if not config_file.exists():
                continue
            
            print(f"\n👤 Client ID: {client_id}")
            with open(config_file, 'r', encoding='utf-8') as f:
                lojas = json.load(f)
            
            for loja in lojas:
                loja_nome = loja.get('nome')
                integracoes = loja.get('integracoes', {})
                ml_cfg = integracoes.get('mercadolivre', {})
                
                print(f"\n   🏢 Loja: {loja_nome}")
                
                has_app_id = bool(ml_cfg.get('app_id') or ml_cfg.get('id'))
                has_secret = bool(ml_cfg.get('secret_key') or ml_cfg.get('secret'))
                has_token = bool(ml_cfg.get('access_token'))
                has_user_id = bool(ml_cfg.get('user_id'))
                
                print(f"      {'✅' if has_app_id else '❌'} App ID: {has_app_id}")
                print(f"      {'✅' if has_secret else '❌'} Secret: {has_secret}")
                print(f"      {'✅' if has_token else '❌'} Access Token: {has_token}")
                print(f"      {'✅' if has_user_id else '❌'} User ID: {has_user_id}")
                
                if has_token and has_user_id:
                    print(f"      🎉 OAUTH COMPLETO - Pronto para usar!")
                    print(f"         User ID: {ml_cfg.get('user_id')}")
                    if 'updated_at' in ml_cfg:
                        import time
                        from datetime import datetime
                        ts = float(ml_cfg['updated_at'])
                        dt = datetime.fromtimestamp(ts)
                        print(f"         Última atualização: {dt.strftime('%d/%m/%Y %H:%M:%S')}")
                elif has_app_id and has_secret:
                    print(f"      ⚠️  OAUTH INCOMPLETO - Execute autenticação na página de Integrações")
                else:
                    print(f"      ❌ CONFIGURAÇÃO INCOMPLETA")
    
    print("\n" + "=" * 60)
    print("✅ Diagnóstico concluído!")
    print("=" * 60)
    print("\n📌 AÇÃO NECESSÁRIA:")
    print("   Se alguma loja está com '⚠️ OAUTH INCOMPLETO':")
    print("   1. Acesse o dashboard do sistema")
    print("   2. Clique em '🔗 Integrações'")
    print("   3. Selecione a loja")
    print("   4. Na aba 'Mercado Livre', clique em 'Autenticar Mercado Livre'")
    print("   5. Autorize o app no site do ML")
    print("\n   Após isso, execute este script novamente para verificar.\n")

if __name__ == "__main__":
    check_ml_integration()
