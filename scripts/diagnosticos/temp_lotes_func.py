
def buscar_lotes_produto_bling(access_token, id_produto):
    url = f"https://api.bling.com.br/Api/v3/produtos/{id_produto}/lotes"
    headers = {"Authorization": f"Bearer {access_token}"}
    lotes = []
    
    for pag in range(1, 100):
        try:
            r = requests.get(url, headers=headers, params={"pagina": pag, "limite": 100})
            if r.status_code != 200:
                break
            
            lista = r.json().get("data", [])
            if not lista:
                break

            for lote in lista:
                lotes.append(lote)
        except Exception as e:
            print(f"Erro ao buscar lotes do produto {id_produto}: {e}")
            break
            
    return lotes
