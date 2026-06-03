import os
import backend_api

base = backend_api.PASTA_INFO
clientes = []
for nome in os.listdir(base):
    caminho = os.path.join(base, nome)
    if os.path.isdir(caminho):
        clientes.append(nome)
clientes = sorted(set(clientes))
print('clientes_encontrados=', clientes)

for client_id in clientes:
    try:
        resultado = backend_api._ia_rag_reindexar_app(client_id, force=True)
        print(f'[OK] {client_id}:', resultado.get('inseridos'), 'docs inseridos; preparados=', resultado.get('documentos_preparados'))
    except Exception as exc:
        print(f'[ERRO] {client_id}:', exc)
