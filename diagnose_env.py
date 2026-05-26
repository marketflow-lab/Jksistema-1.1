import sys
import os
import json
import py_compile
import importlib

ROOT = os.path.dirname(__file__)
INFO = os.path.join(ROOT, 'info')

print('Diagnóstico rápido do ambiente')
print('Python:', sys.version.split()[0])

# Verificar arquivos
files_to_check = ['info/credentials.json', 'info/config_sheet.json']
for f in files_to_check:
    p = os.path.join(ROOT, f)
    print(f'Arquivo {f}:', 'EXISTS' if os.path.exists(p) else 'MISSING')
    if os.path.exists(p):
        try:
            with open(p, 'r', encoding='utf-8') as fh:
                data = fh.read()
                print(f'  Tamanho: {len(data)} bytes')
                try:
                    j = json.loads(data)
                    print('  JSON válido: keys =', list(j.keys()))
                except Exception as e:
                    print('  JSON inválido:', e)
        except Exception as e:
            print('  Falha leitura:', e)

# Verificar imports
packages = ['streamlit', 'streamlit_authenticator', 'gspread', 'google.oauth2.service_account', 'bcrypt', 'pandas']
for pkg in packages:
    try:
        mod = importlib.import_module(pkg)
        try:
            ver = getattr(mod, '__version__', None)
        except Exception:
            ver = None
        print(f'Import OK: {pkg}  version={ver}')
    except Exception as e:
        print(f'Import FAILED: {pkg} -> {e}')

# Compilar arquivos principais (sintaxe)
py_files = ['app.py', 'login.py']
for f in py_files:
    p = os.path.join(ROOT, f)
    if os.path.exists(p):
        try:
            py_compile.compile(p, doraise=True)
            print(f'Compilação OK: {f}')
        except py_compile.PyCompileError as e:
            print(f'Erro de sintaxe em {f}:', e)
        except Exception as e:
            print(f'Erro compilando {f}:', e)
    else:
        print(f'Arquivo não encontrado: {f}')

# Mostrar últimos logs se houver
log = os.path.join(INFO, 'jk_sistema.log')
if os.path.exists(log):
    print('\nÚltimas linhas do log:', log)
    try:
        with open(log, 'r', encoding='utf-8') as fh:
            lines = fh.readlines()
        for line in lines[-30:]:
            print(line.rstrip())
    except Exception as e:
        print('Falha leitura log:', e)
else:
    print('\nLog não encontrado:', log)

print('\nDiagnóstico finalizado.')
