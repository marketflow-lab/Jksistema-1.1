
def buscar_lancamentos_lote_bling(access_token, id_lote):
    url = f"https://api.bling.com.br/Api/v3/produtos/lotes/{id_lote}/lancamentos"
    headers = {"Authorization": f"Bearer {access_token}"}
    lancamentos = []
    
    for pag in range(1, 100):
        try:
            r = requests.get(url, headers=headers, params={"pagina": pag, "limite": 100})
            if r.status_code != 200:
                break
            
            lista = r.json().get("data", [])
            if not lista:
                break

            for lancamento in lista:
                lancamentos.append(lancamento)
        except Exception as e:
            print(f"Erro ao buscar lancamentos do lote {id_lote}: {e}")
            break
            
    return lancamentos
