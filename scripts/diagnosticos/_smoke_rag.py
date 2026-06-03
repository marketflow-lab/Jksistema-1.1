import backend_api
for client_id in ['000002','000007','default']:
    try:
        docs_vendas = backend_api._ia_rag_docs_db_vendas(client_id)
        docs_cadastro = backend_api._ia_rag_docs_csv_cadastro(client_id)
        print(client_id, 'vendas_docs=', len(docs_vendas), 'cadastro_docs=', len(docs_cadastro))
        print('  first_vendas=', docs_vendas[0].title if docs_vendas else None)
        print('  first_cadastro=', docs_cadastro[0].title if docs_cadastro else None)
    except Exception as exc:
        print(client_id, 'ERR', exc)
