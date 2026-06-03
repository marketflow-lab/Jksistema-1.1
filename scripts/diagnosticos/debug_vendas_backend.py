#!/usr/bin/env python3
"""
ðŸ” SCRIPT DE DEBUG - DiagnÃ³stico do MÃ³dulo de Vendas
======================================================

Este script analisa a estrutura do backend_api.py e identifica problemas
que podem causar redirecionamento para login ao acessar o mÃ³dulo de vendas.

Uso: python debug_vendas_backend.py
"""

import os
import sys
import json
import re
from pathlib import Path

class DebugVendas:
    def __init__(self):
        self.base_dir = Path(__file__).parent
        self.backend_file = self.base_dir / 'backend_api.py'
        self.login_response_found = False
        self.client_id_in_response = False
        self.issues = []
        self.warnings = []
        self.success = []
        
    def print_header(self, text):
        print(f"\n{'='*70}")
        print(f"  {text}")
        print(f"{'='*70}\n")
    
    def print_section(self, text):
        print(f"\n--- {text} ---\n")
    
    def print_ok(self, text):
        print(f"  âœ… {text}")
        self.success.append(text)
    
    def print_error(self, text):
        print(f"  âŒ {text}")
        self.issues.append(text)
    
    def print_warning(self, text):
        print(f"  âš ï¸  {text}")
        self.warnings.append(text)
    
    def print_info(self, text):
        print(f"  â„¹ï¸  {text}")
    
    def analise_backend_api(self):
        """Analisa backend_api.py"""
        self.print_section("1. VERIFICANDO backend_api.py")
        
        if not self.backend_file.exists():
            self.print_error(f"Arquivo nÃ£o encontrado: {self.backend_file}")
            return False
        
        self.print_ok(f"Arquivo encontrado: {self.backend_file}")
        
        with open(self.backend_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 1.1 Procurar por @app.post("/api/login")
        login_pattern = r'@app\.post\s*\(\s*["\']\/api\/login["\']'
        if re.search(login_pattern, content):
            self.print_ok("Endpoint /api/login encontrado")
            self.login_response_found = True
        else:
            self.print_error("Endpoint /api/login NÃƒO encontrado")
            return False
        
        # 1.2 Procurar por LoginResponse com client_id
        # Buscar padrÃµes como: "client_id": ou client_id =
        login_end = content.find('@app.post("/api/login")')
        next_decorator = content.find('@app.', login_end + 1)
        login_section = content[login_end:next_decorator]
        
        if '"client_id"' in login_section or "'client_id'" in login_section:
            self.print_ok("ReferÃªncia a 'client_id' encontrada no endpoint /api/login")
            self.client_id_in_response = True
        else:
            self.print_warning("âš ï¸  'client_id' NÃƒO encontrado na resposta de login")
            self.print_info("O client_id deve ser incluÃ­do em user_data na resposta LoginResponse")
        
        # 1.3 Procurar por get_tenant_id
        if 'def get_tenant_id' in content:
            self.print_ok("FunÃ§Ã£o get_tenant_id encontrada")
            # Verificar se X-Client-ID Ã© obrigatÃ³rio
            if 'Header(...)' in content and 'x_client_id' in content.lower():
                self.print_ok("get_tenant_id valida header X-Client-ID como obrigatÃ³rio")
        else:
            self.print_error("FunÃ§Ã£o get_tenant_id NÃƒO encontrada")
        
        # 1.4 Procurar por endpoints que requerem X-Client-ID
        endpoints = {
            '/api/vendas': r'@app\.get\s*\(\s*["\']\/api\/vendas["\']',
            '/api/lojas': r'@app\.get\s*\(\s*["\']\/api\/lojas["\']',
            '/api/notas-entrada': r'@app\.get\s*\(\s*["\']\/api\/notas-entrada["\']'
        }
        
        self.print_info("Endpoints que requerem X-Client-ID:")
        for endpoint, pattern in endpoints.items():
            if re.search(pattern, content):
                if 'Depends(get_tenant_id)' in content:
                    self.print_ok(f"  {endpoint} âœ…")
                else:
                    self.print_warning(f"  {endpoint} (verificar se requer get_tenant_id)")
            else:
                self.print_warning(f"  {endpoint} nÃ£o encontrado")
        
        return True
    
    def analise_frontend(self):
        """Analisa frontend_index.html"""
        self.print_section("2. VERIFICANDO frontend_index.html")
        
        frontend_file = self.base_dir / 'frontend_index.html'
        
        if not frontend_file.exists():
            self.print_error(f"Arquivo nÃ£o encontrado: {frontend_file}")
            return False
        
        with open(frontend_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Procurar por localStorage.setItem para user_data
        if "localStorage.setItem('user_data'" in content or 'localStorage.setItem("user_data"' in content:
            self.print_ok("Frontend armazena user_data no localStorage")
        else:
            self.print_error("Frontend NÃƒO armazena user_data no localStorage")
        
        # Procurar por client_id
        if 'client_id' in content:
            self.print_ok("ReferÃªncia a 'client_id' encontrada no frontend")
        else:
            self.print_warning("âš ï¸  'client_id' nÃ£o mencionado no frontend (pode estar em backend_api)")
        
        return True
    
    def analise_vendas_html(self):
        """Analisa vendas.html"""
        self.print_section("3. VERIFICANDO vendas.html")
        
        vendas_file = self.base_dir / 'vendas.html'
        
        if not vendas_file.exists():
            self.print_error(f"Arquivo nÃ£o encontrado: {vendas_file}")
            return False
        
        with open(vendas_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Procurar por funÃ§Ã£o obterClientId
        if 'function obterClientId' in content or 'const obterClientId' in content:
            self.print_ok("FunÃ§Ã£o obterClientId encontrada")
        else:
            self.print_error("FunÃ§Ã£o obterClientId NÃƒO encontrada")
        
        # Procurar por X-Client-ID header
        if "'X-Client-ID'" in content or '"X-Client-ID"' in content:
            self.print_ok("Header X-Client-ID Ã© enviado nas requisiÃ§Ãµes")
        else:
            self.print_error("Header X-Client-ID NÃƒO Ã© enviado nas requisiÃ§Ãµes")
        
        # Procurar por localStorage.getItem('user_data')
        if "localStorage.getItem('user_data')" in content or 'localStorage.getItem("user_data")' in content:
            self.print_ok("vendas.html recupera user_data do localStorage")
        else:
            self.print_error("vendas.html NÃƒO recupera user_data do localStorage")
        
        # Verificar redirecionamento para login
        if 'window.location.href' in content and 'frontend_index.html' in content:
            self.print_info("vendas.html redireciona para login se nÃ£o autenticado")
        
        return True
    
    def verificar_arquivos_config(self):
        """Verifica se arquivos de configuraÃ§Ã£o existem"""
        self.print_section("4. VERIFICANDO ARQUIVOS DE CONFIGURAÃ‡ÃƒO")
        
        info_dir = self.base_dir / 'info'
        
        if not info_dir.exists():
            self.print_warning(f"DiretÃ³rio 'info' nÃ£o existe: {info_dir}")
            return False
        
        self.print_ok(f"DiretÃ³rio 'info' encontrado: {info_dir}")
        
        required_files = {
            'credentials.json': 'Credenciais do Google',
            'config_sheet.json': 'ID da planilha principal',
            'integracoes.json': 'IntegraÃ§Ã£o com lojas',
        }
        
        for filename, description in required_files.items():
            file_path = info_dir / filename
            if file_path.exists():
                self.print_ok(f"  {filename} âœ… ({description})")
            else:
                self.print_warning(f"  {filename} âš ï¸  ({description})")
        
        return True
    
    def gerar_relatorio(self):
        """Gera relatÃ³rio final"""
        self.print_header("ðŸ“Š RELATÃ“RIO FINAL")
        
        self.print_section("RESUMO")
        print(f"\n  âœ… Sucessos: {len(self.success)}")
        print(f"  âš ï¸  Avisos: {len(self.warnings)}")
        print(f"  âŒ Problemas: {len(self.issues)}")
        
        if self.issues:
            self.print_section("PROBLEMAS ENCONTRADOS")
            for i, issue in enumerate(self.issues, 1):
                print(f"  {i}. {issue}")
        
        if self.warnings:
            self.print_section("AVISOS")
            for i, warning in enumerate(self.warnings, 1):
                print(f"  {i}. {warning}")
        
        # AnÃ¡lise final
        self.print_section("ANÃLISE FINAL")
        
        if not self.client_id_in_response:
            print("""
  ðŸ”´ PROBLEMA CRÃTICO IDENTIFICADO:
  
  O endpoint /api/login NÃƒO estÃ¡ incluindo 'client_id' na resposta.
  
  SOLUÃ‡ÃƒO:
  
  Edite backend_api.py e procure pelo endpoint @app.post("/api/login").
  Na resposta LoginResponse, certifique-se de incluir 'client_id':
  
  â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
  â”‚ return LoginResponse(                                        â”‚
  â”‚     success=True,                                           â”‚
  â”‚     message="Login realizado com sucesso!",                â”‚
  â”‚     user_data={                                             â”‚
  â”‚         "name": stored_user["name"],                        â”‚
  â”‚         "username": user_key,                               â”‚
  â”‚         "client_id": dados.client_id  # âœ… ADICIONE ISTO!  â”‚
  â”‚     },                                                       â”‚
  â”‚     permissions=permissoes                                  â”‚
  â”‚ )                                                            â”‚
  â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
            """)
        elif len(self.issues) == 0:
            print("""
  ðŸŸ¢ NENHUM PROBLEMA CRÃTICO ENCONTRADO
  
  Se o mÃ³dulo de vendas ainda redireciona para login:
  
  1. Limpe o cache do navegador (Ctrl+Shift+Delete)
  2. FaÃ§a login novamente
    3. Acesse http://127.0.0.1:8001/debug_vendas.html para diagnosticar
  4. Verifique os logs do backend (procure por erros de autenticaÃ§Ã£o)
            """)
        else:
            print("""
  ðŸŸ¡ EXISTEM ALGUNS PROBLEMAS
  
  Resolva os problemas listados acima e tente novamente.
            """)
        
        self.print_section("PRÃ“XIMOS PASSOS")
        print("""
    1. Execute: http://127.0.0.1:8001/debug_vendas.html
     - Esta pÃ¡gina fornece diagnÃ³stico em tempo real
     - Mostra exactamente o que estÃ¡ faltando
  
  2. Verifique os logs do backend:
     - Procure por mensagens de erro de autenticaÃ§Ã£o
    - Use: uvicorn backend_api:app --reload --port 8001 --log-level debug
  
  3. Teste manualmente o endpoint de login:
    curl -X POST http://127.0.0.1:8001/api/login \\
       -H "Content-Type: application/json" \\
       -d '{"username":"test","password":"test","client_id":"test-client"}'
        """)

def main():
    debug = DebugVendas()
    
    debug.print_header("ðŸ” DIAGNÃ“STICO DO MÃ“DULO DE VENDAS")
    print(f"DiretÃ³rio de trabalho: {debug.base_dir}\n")
    
    # Executar anÃ¡lises
    debug.analise_backend_api()
    debug.analise_frontend()
    debug.analise_vendas_html()
    debug.verificar_arquivos_config()
    
    # Gerar relatÃ³rio
    debug.gerar_relatorio()

if __name__ == '__main__':
    main()
