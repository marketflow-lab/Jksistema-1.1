"""
Script de teste direto das APIs do Portal Único Siscomex.
Consulta II (TEC), IPI (TIPI) e tratamentos tributários (TTCE) para um NCM.

Uso: python testar_api_aliquotas.py [NCM]
Exemplo: python testar_api_aliquotas.py 85369090
"""
import json
import re
import sys
import time
import requests

# ── Configuração ─────────────────────────────────────────────────────────────
CONFIG_FILE = r"info\000002\siscomex_config.json"
SCOPE_KEY   = "JK Peças::caio"
NCM_TESTE   = sys.argv[1].replace(".", "") if len(sys.argv) > 1 else "85369090"
HOST        = "portalunico.siscomex.gov.br"

# ── Helpers ───────────────────────────────────────────────────────────────────
def formatar_ncm(ncm: str) -> str:
    d = re.sub(r"\D", "", ncm).zfill(8)
    return f"{d[:4]}.{d[4:6]}.{d[6:]}"

def extrair_mensagem_erro(resp: requests.Response) -> str:
    texto = resp.text or ""
    if "<" in texto and ">" in texto:
        m = re.search(r"<h1[^>]*>(.*?)</h1>", texto, re.I | re.S)
        if m:
            return re.sub(r"<[^>]+>", "", m.group(1)).strip()
        m = re.search(r"<title[^>]*>(.*?)</title>", texto, re.I | re.S)
        if m:
            return re.sub(r"<[^>]+>", "", m.group(1)).strip()
        return f"HTML ({len(texto)} chars)"
    try:
        j = resp.json()
        return str(j.get("message") or j.get("erro") or j.get("error") or texto[:300])
    except Exception:
        return texto[:300]

def carregar_config():
    with open(CONFIG_FILE, encoding="utf-8") as f:
        raw = f.read()
    # corrige encoding latin1 mal salvo
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = json.loads(raw.encode("latin1").decode("utf-8", errors="replace"))
    scopes = data.get("scopes", {})
    # tenta a chave exata ou a primeira disponível
    cfg = scopes.get(SCOPE_KEY) or next(iter(scopes.values()), None)
    if not cfg:
        raise RuntimeError(f"Nenhuma configuração encontrada em {CONFIG_FILE}")
    return cfg

# ── 1. Autenticação ───────────────────────────────────────────────────────────
def autenticar(cfg: dict) -> dict:
    url = f"https://{HOST}/portal/api/autenticar/chave-acesso"
    headers = {
        "Content-Type":  "application/json",
        "Accept":        "application/json",
        "Role-Type":     cfg.get("role_type", "IMPEXP"),
        "Client-Id":     cfg["client_id"],
        "Client-Secret": cfg["client_secret"],
    }
    print(f"\n[AUTH] POST {url}")
    time.sleep(2)  # evita rate-limit
    resp = requests.post(url, headers=headers, timeout=25)
    print(f"  Status: {resp.status_code}")
    if not resp.ok:
        raise RuntimeError(f"Falha na autenticação: {extrair_mensagem_erro(resp)}")
    set_token  = resp.headers.get("Set-Token", "")
    csrf_token = resp.headers.get("X-CSRF-Token", "")
    if not set_token or not csrf_token:
        # tenta no corpo
        try:
            body = resp.json()
            set_token  = set_token  or str(body.get("set-token") or body.get("setToken") or "")
            csrf_token = csrf_token or str(body.get("x-csrf-token") or body.get("csrfToken") or "")
        except Exception:
            pass
    if not set_token or not csrf_token:
        print(f"  Headers recebidos: {dict(resp.headers)}")
        raise RuntimeError("Set-Token ou X-CSRF-Token ausente na resposta de autenticação.")
    print(f"  Set-Token:    {set_token[:30]}...")
    print(f"  X-CSRF-Token: {csrf_token[:30]}...")
    return {"set_token": set_token, "csrf_token": csrf_token}

def api_headers(cfg: dict, auth: dict) -> dict:
    role = cfg.get("role_type", "IMPEXP")
    # Modo "raw": token JWT diretamente no Authorization (sem prefixo Bearer/Token)
    return {
        "Accept":        "application/json",
        "Content-Type":  "application/json",
        "Authorization": auth["set_token"],   # raw — exigido pelo Portal Único
        "X-CSRF-Token":  auth["csrf_token"],
        "Role-Type":     role,
        "User-Agent":    "JKSistema/1.0",
    }

def build_header_variants(cfg: dict, auth: dict) -> list[dict]:
    """Monta 3 variantes essenciais para GET (TEC/TIPI)."""
    token = auth["set_token"]
    csrf  = auth["csrf_token"]
    role  = cfg.get("role_type", "IMPEXP")
    base = {
        "Accept":        "application/json",
        "Authorization": token,
        "X-CSRF-Token":  csrf,
        "Role-Type":     role,
        "User-Agent":    "JKSistema/1.0",
    }
    return [
        dict(base),
        {k: v for k, v in base.items() if k != "X-CSRF-Token"},
        {k: v for k, v in base.items() if k not in {"X-CSRF-Token", "Role-Type"}},
    ]

# ── 2. TEC — II ───────────────────────────────────────────────────────────────
def consultar_tec(ncm_fmt: str, cfg: dict, auth: dict) -> dict:
    url = f"https://{HOST}/tec/api/ext/nomenclatura/ncm/{ncm_fmt}"
    variantes = build_header_variants(cfg, auth)
    print(f"\n[TEC] GET {url}  ({len(variantes)} variantes de header)")
    for i, h in enumerate(variantes, 1):
        auth_preview = (h.get("Authorization") or "")[:40]
        print(f"  [{i}] Authorization={auth_preview}... CSRF={'sim' if 'X-CSRF-Token' in h else 'não'} Role={'sim' if 'Role-Type' in h else 'não'}")
        resp = requests.get(url, headers=h, timeout=15)
        print(f"       → Status: {resp.status_code}")
        if resp.ok:
            data = resp.json()
            print(f"  Resposta: {json.dumps(data, ensure_ascii=False, indent=2)}")
            return data
        if resp.status_code not in {401, 403}:
            break
    print(f"  ERRO: {extrair_mensagem_erro(resp)}")
    return {"erro": extrair_mensagem_erro(resp)}

# ── 3. TIPI — IPI ─────────────────────────────────────────────────────────────
def consultar_tipi(ncm_fmt: str, cfg: dict, auth: dict) -> dict:
    url = f"https://{HOST}/tipi/api/ext/ncm/{ncm_fmt}"
    variantes = build_header_variants(cfg, auth)
    print(f"\n[TIPI] GET {url}  ({len(variantes)} variantes de header)")
    for i, h in enumerate(variantes, 1):
        auth_preview = (h.get("Authorization") or "")[:40]
        print(f"  [{i}] Authorization={auth_preview}... CSRF={'sim' if 'X-CSRF-Token' in h else 'não'} Role={'sim' if 'Role-Type' in h else 'não'}")
        resp = requests.get(url, headers=h, timeout=15)
        print(f"       → Status: {resp.status_code}")
        if resp.ok:
            data = resp.json()
            print(f"  Resposta: {json.dumps(data, ensure_ascii=False, indent=2)}")
            return data
        if resp.status_code not in {401, 403}:
            break
    print(f"  ERRO: {extrair_mensagem_erro(resp)}")
    return {"erro": extrair_mensagem_erro(resp)}

# ── 4. TTCE — PIS / COFINS ────────────────────────────────────────────────────
def consultar_ttce(ncm_fmt: str, cfg: dict, auth: dict) -> dict:
    url = f"https://{HOST}/ttce/api/ext/tratamentos-tributarios/importacao/"
    # TTCE espera NCM sem pontos (8 dígitos)
    ncm_sem_pontos = re.sub(r"\D", "", ncm_fmt)
    payload = {
        "codigoNcm":        ncm_sem_pontos,
        "codigoPais":       cfg.get("codigo_pais_padrao", 741),
        "tipoOperacao":     cfg.get("tipo_operacao_padrao", "I"),
        "regimeTributario": cfg.get("regime_tributario", "simples"),
    }
    print(f"  NCM enviado ao TTCE: {ncm_sem_pontos}")
    hdrs = {
        "Accept":        "application/json",
        "Content-Type":  "application/json",
        "Authorization": auth["set_token"],
        "X-CSRF-Token":  auth["csrf_token"],
        "Role-Type":     cfg.get("role_type", "IMPEXP"),
        "User-Agent":    "JKSistema/1.0",
    }
    print(f"\n[TTCE] POST {url}")
    print(f"  Payload: {json.dumps(payload, ensure_ascii=False)}")
    resp = requests.post(url, headers=hdrs, json=payload, timeout=30)
    print(f"  Status: {resp.status_code}")
    if resp.ok:
        data = resp.json()
        print(f"  Resposta: {json.dumps(data, ensure_ascii=False, indent=2)}")
        return data
    # Tenta sem X-CSRF-Token (alguns ambientes não exigem)
    if resp.status_code == 401:
        print(f"  Tentando sem X-CSRF-Token...")
        hdrs2 = {k: v for k, v in hdrs.items() if k != "X-CSRF-Token"}
        resp2 = requests.post(url, headers=hdrs2, json=payload, timeout=30)
        print(f"  Status (sem CSRF): {resp2.status_code}")
        if resp2.ok:
            data = resp2.json()
            print(f"  Resposta: {json.dumps(data, ensure_ascii=False, indent=2)}")
            return data
        print(f"  ERRO: {extrair_mensagem_erro(resp2)}")
        return {"erro": extrair_mensagem_erro(resp2)}
    print(f"  ERRO: {extrair_mensagem_erro(resp)}")
    return {"erro": extrair_mensagem_erro(resp)}

# ── 5. Resumo das alíquotas ───────────────────────────────────────────────────
def resumir(ncm_fmt: str, tec: dict, tipi: dict, ttce: dict):
    print("\n" + "="*60)
    print(f"RESUMO ALÍQUOTAS — NCM {ncm_fmt}")
    print("="*60)

    # II
    ii = tec.get("aliquotaAd") or tec.get("aliquota") or tec.get("aliquotaII")
    if ii is not None:
        print(f"  II  (TEC):  {float(ii):.4f}%")
    else:
        print(f"  II  (TEC):  N/D — {tec.get('erro', 'sem dados')}")

    # IPI
    ipi = tipi.get("aliquota") or tipi.get("aliquotaIpi") or tipi.get("aliquotaIPI")
    if ipi is not None:
        print(f"  IPI (TIPI): {float(ipi):.4f}%")
    else:
        print(f"  IPI (TIPI): N/D — {tipi.get('erro', 'sem dados')}")

    # PIS / COFINS — extrai do TTCE
    pis = cofins = None
    monofasico = False
    tratamentos = []
    if isinstance(ttce, dict):
        tratamentos = ttce.get("tratamentosTributarios") or []
    for t in tratamentos:
        regime = (t.get("regime") or {}).get("nome", "")
        if "MONOFASICO" in regime.upper():
            monofasico = True
        tributos = t.get("tributos") or []
        for tr in tributos:
            nome = (tr.get("tributo") or tr.get("nome") or "").upper()
            aliq = tr.get("aliquota") or tr.get("percentual")
            if "PIS" in nome and pis is None:
                pis = aliq
            if "COFINS" in nome and cofins is None:
                cofins = aliq

    print(f"  PIS-Importação:    {'Monofásico (diferenciado)' if monofasico else f'{float(pis):.4f}%' if pis is not None else 'N/D (base: 2,10%)'}")
    print(f"  COFINS-Importação: {'Monofásico (diferenciado)' if monofasico else f'{float(cofins):.4f}%' if cofins is not None else 'N/D (base: 9,65%)'}")
    if monofasico:
        print("  ⚠  Produto monofásico detectado no TTCE.")
    if "erro" in ttce:
        print(f"  TTCE: ERRO — {ttce['erro']}")
    print("="*60)

# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    ncm_fmt = formatar_ncm(NCM_TESTE)
    print(f"Testando NCM: {ncm_fmt}")

    try:
        cfg  = carregar_config()
        auth = autenticar(cfg)

        tec  = consultar_tec(ncm_fmt, cfg, auth)
        tipi = consultar_tipi(ncm_fmt, cfg, auth)
        ttce = consultar_ttce(ncm_fmt, cfg, auth)

        resumir(ncm_fmt, tec, tipi, ttce)

    except Exception as e:
        print(f"\n[FALHA] {e}")
        sys.exit(1)
