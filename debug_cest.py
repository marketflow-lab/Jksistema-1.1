"""
Endpoint de debug para ver estrutura de resposta do produto Bling incluindo CEST
Adicionar em backend_api.py e acessar GET /api/debug/bling-produto/{produto_id}
"""

@app.get("/api/debug/bling-produto/{produto_id}")
async def debug_bling_produto(produto_id: str, client_id: str = Depends(get_tenant_id)):
    """DEBUG ONLY: Retorna resposta raw do Bling para um produto"""
    try:
        lojas = carregar_lojas(client_id)
        bling_cfg = None
        for loja in lojas:
            cfg = (loja.get("integracoes") or {}).get("bling")
            if cfg and cfg.get("access_token"):
                bling_cfg = cfg
                break
        
        if not bling_cfg:
            return {"error": "Nenhuma loja Bling conectada"}
        
        headers = {"Authorization": f"Bearer {bling_cfg.get('access_token')}"}
        resp = BLING_SESSION.get(
            f"https://www.bling.com.br/Api/v3/produtos/{produto_id}",
            headers=headers,
            timeout=20
        )
        
        if resp.status_code != 200:
            return {
                "error": f"HTTP {resp.status_code}",
                "text": resp.text
            }
        
        data = resp.json()
        return {
            "produto_id": produto_id,
            "status": 200,
            "data_keys": list((data.get("data") or {}).keys()),
            "tributacao_raw": (data.get("data") or {}).get("tributacao"),
            "full_response": data
        }
    except Exception as e:
        return {"error": str(e)}
