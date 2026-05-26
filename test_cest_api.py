#!/usr/bin/env python3
"""
Script de teste para verificar se CEST está vindo da API Bling
"""

import json
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def _requests_session_with_retry(total_retries=3, backoff_factor=0.3):
    session = requests.Session()
    retry = Retry(
        total=total_retries,
        read=total_retries,
        connect=total_retries,
        backoff_factor=backoff_factor,
        status_forcelist=(429, 500, 502, 503, 504),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def testar_cest(access_token: str, produto_id: str):
    """Testa se CEST está vindo da API Bling para um produto específico"""
    session = _requests_session_with_retry()
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"https://www.bling.com.br/Api/v3/produtos/{produto_id}"
    
    print(f"\n[TEST] Buscando produto {produto_id}...")
    print(f"URL: {url}")
    
    try:
        resp = session.get(url, headers=headers, timeout=20)
        print(f"Status: {resp.status_code}")
        
        if resp.status_code != 200:
            print(f"Erro HTTP {resp.status_code}: {resp.text}")
            return
        
        data = resp.json()
        print(f"\n[RESPONSE] Full JSON (primeiro nível):")
        if "data" in data:
            print(json.dumps({k: type(v).__name__ for k, v in data["data"].items()}, indent=2))
        
        data_det = data.get("data", {}) or {}
        trib = data_det.get("tributacao") or {}
        
        print(f"\n[TRIBUTACAO] Conteúdo:")
        print(json.dumps(trib, indent=2, default=str))
        
        # Buscar NCM
        ncm_det = trib.get("ncm") if isinstance(trib, dict) else data_det.get("ncm")
        if isinstance(ncm_det, dict):
            ncm_det = ncm_det.get("codigo") or ncm_det.get("id") or ncm_det.get("valor")
        ncm_str = str(ncm_det or "").strip()
        
        # Buscar CEST - múltiplas localizações
        cest_str = ""
        if isinstance(trib, dict):
            cest_det = trib.get("cest")
            if isinstance(cest_det, dict):
                cest_det = cest_det.get("codigo") or cest_det.get("id") or cest_det.get("valor")
            elif not cest_det:
                cest_det = data_det.get("cest")
                if isinstance(cest_det, dict):
                    cest_det = cest_det.get("codigo") or cest_det.get("id") or cest_det.get("valor")
            cest_str = str(cest_det or "").strip()
        
        print(f"\n[RESULTADO]")
        print(f"NCM: {ncm_str or '(vazio)'}")
        print(f"CEST: {cest_str or '(vazio)'}")
        
        # Buscar outras informações tributárias relevantes
        print(f"\n[OUTRAS TRIBUTAÇÕES]")
        for key, value in trib.items():
            if key not in ["ncm", "cest"]:
                print(f"  {key}: {value}")
        
    except Exception as e:
        print(f"Erro: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    # Pegar token do arquivo de config
    import os
    from pathlib import Path
    
    info_path = Path(__file__).parent / "info"
    
    # Tentar carregar integracoes.json
    integracoes_file = info_path / "integracoes.json"
    if integracoes_file.exists():
        with open(integracoes_file) as f:
            data = json.load(f)
        
        # Buscar primeira config Bling com access_token
        for client_id, client_data in data.items():
            lojas = client_data.get("lojas", {})
            for loja_name, loja_data in lojas.items():
                bling_cfg = loja_data.get("integracoes", {}).get("bling")
                if bling_cfg and bling_cfg.get("access_token"):
                    access_token = bling_cfg["access_token"]
                    print(f"[CONFIG] Usando: client_id={client_id}, loja={loja_name}")
                    
                    # Testar alguns produtos (buscar IDs de id_bling do estoque)
                    estoque_file = info_path / f"{client_id}/estoque_compilado.csv"
                    if estoque_file.exists():
                        import pandas as pd
                        df = pd.read_csv(estoque_file, dtype=str)
                        if "id_bling" in df.columns:
                            ids = df["id_bling"].dropna().unique()[:3]
                            for pid in ids:
                                if pid:
                                    testar_cest(access_token, pid)
                    else:
                        # Testar com ID fixo
                        testar_cest(access_token, "1")
                    
                    exit(0)
    
    print("Nenhuma configuração Bling encontrada em integracoes.json")
    print("Execute: python test_cest_api.py")
