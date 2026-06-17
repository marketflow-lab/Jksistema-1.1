"""Infrastructure service helpers.

This module contains the local app health and update-check logic used by the
technical infrastructure routes.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Any, Optional

import requests


def build_health_payload(
    *,
    app_version: str,
    firebase_configured: bool,
    firebase_active: bool,
    firebase_live_features: bool,
    firebase_last_error: str,
) -> dict:
    return {
        "ok": True,
        "appVersion": app_version,
        "firebaseConfigured": bool(firebase_configured),
        "firebaseActive": bool(firebase_active),
        "firebaseLiveFeatures": bool(firebase_live_features),
        "firebaseLastError": firebase_last_error,
    }


def _latest_yml_url() -> str:
    owner = os.getenv("JK_UPDATE_OWNER", "marketflow-lab").strip() or "marketflow-lab"
    repo = os.getenv("JK_UPDATE_REPO", "Jksistema-1.1").strip() or "Jksistema-1.1"
    return (
        os.getenv("JK_UPDATE_LATEST_YML_URL", "").strip()
        or f"https://github.com/{owner}/{repo}/releases/latest/download/latest.yml"
    )


def _normalizar_versao_app(valor: Any) -> str:
    texto = str(valor or "").strip()
    texto = re.sub(r"^[vV]\s*", "", texto)
    match = re.search(r"\d+(?:\.\d+){0,3}", texto)
    return match.group(0) if match else ""


def _chave_comparacao_versao(valor: Any) -> tuple[int, int, int, int]:
    versao = _normalizar_versao_app(valor)
    partes = []
    for pedaco in versao.split("."):
        try:
            partes.append(int(pedaco))
        except Exception:
            partes.append(0)
    while len(partes) < 4:
        partes.append(0)
    return tuple(partes[:4])


def _parse_latest_yml_simples(texto: str) -> dict:
    dados: dict[str, Any] = {}
    for linha in str(texto or "").splitlines():
        match = re.match(r"^\s*([A-Za-z0-9_-]+)\s*:\s*(.*?)\s*$", linha)
        if not match:
            continue
        chave, valor = match.group(1), match.group(2).strip()
        if valor.startswith(("'", '"')) and valor.endswith(("'", '"')) and len(valor) >= 2:
            valor = valor[1:-1]
        if chave in {"version", "path", "sha512", "releaseDate", "url", "size"}:
            dados[chave] = valor
    if not dados.get("version"):
        match = re.search(r"(?im)^\s*version\s*:\s*['\"]?([^'\"\r\n]+)", texto or "")
        if match:
            dados["version"] = match.group(1).strip()
    return dados


def _buscar_latest_yml_update(latest_yml_url: str) -> dict:
    headers = {
        "User-Agent": "JK-Sistema-Updater/1.0",
        "Accept": "text/yaml,text/plain,*/*",
        "Cache-Control": "no-cache",
    }
    resp = requests.get(latest_yml_url, headers=headers, timeout=10, allow_redirects=True)
    resp.raise_for_status()
    dados = _parse_latest_yml_simples(resp.text)
    if not dados.get("version"):
        raise RuntimeError("latest.yml publicado nao informou a versao.")
    dados["source_url"] = latest_yml_url
    return dados


def check_app_update(current_version: Optional[str] = None) -> dict:
    """Verificacao rapida pelo backend local para a UI nunca ficar presa no Electron."""
    latest_yml_url = _latest_yml_url()
    atual = _normalizar_versao_app(current_version) or "0.0.0"
    try:
        latest = _buscar_latest_yml_update(latest_yml_url)
        ultima = _normalizar_versao_app(latest.get("version")) or "0.0.0"
        disponivel = _chave_comparacao_versao(ultima) > _chave_comparacao_versao(atual)
        return {
            "success": True,
            "currentVersion": atual,
            "latestVersion": ultima,
            "available": disponivel,
            "upToDate": not disponivel,
            "publishedOlderThanInstalled": _chave_comparacao_versao(ultima) < _chave_comparacao_versao(atual),
            "file": latest.get("path") or latest.get("url") or "",
            "releaseDate": latest.get("releaseDate") or "",
            "source": latest.get("source_url") or latest_yml_url,
            "checkedAt": datetime.utcnow().isoformat() + "Z",
        }
    except requests.exceptions.Timeout:
        return {
            "success": False,
            "currentVersion": atual,
            "message": "Tempo esgotado consultando o GitHub.",
        }
    except requests.exceptions.RequestException as exc:
        return {
            "success": False,
            "currentVersion": atual,
            "message": f"Falha ao consultar o GitHub: {exc}",
        }
    except Exception as exc:
        return {
            "success": False,
            "currentVersion": atual,
            "message": str(exc) or "Nao foi possivel verificar a atualizacao.",
        }


def _carregar_manifesto_android_update(base_dir: str, pasta_info: str) -> tuple[dict, str]:
    candidatos = [
        os.getenv("JK_ANDROID_UPDATE_MANIFEST", "").strip(),
        os.path.join(pasta_info, "android_update.json"),
        os.path.join(base_dir, "android_app", "update-manifest.json"),
    ]
    for caminho in candidatos:
        caminho = str(caminho or "").strip()
        if not caminho or not os.path.exists(caminho):
            continue
        try:
            with open(caminho, "r", encoding="utf-8") as arquivo:
                dados = json.load(arquivo)
            if isinstance(dados, dict):
                return dados, caminho
        except Exception:
            continue
    return {}, ""


def _inteiro_update(valor: Any, padrao: int = 0) -> int:
    try:
        return int(valor)
    except Exception:
        return padrao


def check_mobile_update(
    *,
    platform: str = "android",
    version: Optional[str] = None,
    version_code: Optional[int] = None,
    base_dir: str,
    pasta_info: str,
) -> dict:
    """Manifesto simples para o app Android privado consultar novas versoes."""
    plataforma = str(platform or "android").strip().lower()
    if plataforma not in {"android", "mobile"}:
        return {
            "success": False,
            "updateAvailable": False,
            "message": "Plataforma nao suportada.",
        }

    manifesto, origem = _carregar_manifesto_android_update(base_dir, pasta_info)
    ultima_versao = _normalizar_versao_app(
        manifesto.get("version") or os.getenv("JK_ANDROID_VERSION", "")
    )
    codigo_remoto = _inteiro_update(
        manifesto.get("versionCode") or os.getenv("JK_ANDROID_VERSION_CODE", 0)
    )
    apk_url = str(
        manifesto.get("apkUrl")
        or manifesto.get("url")
        or os.getenv("JK_ANDROID_APK_URL", "").strip()
        or ""
    ).strip()
    versao_atual = _normalizar_versao_app(version) or "0.0.0"
    codigo_atual = _inteiro_update(version_code, 0)

    disponivel = False
    if codigo_remoto and codigo_atual:
        disponivel = codigo_remoto > codigo_atual
    elif ultima_versao:
        disponivel = _chave_comparacao_versao(ultima_versao) > _chave_comparacao_versao(versao_atual)
    if not apk_url:
        disponivel = False

    return {
        "success": True,
        "platform": "android",
        "currentVersion": versao_atual,
        "currentVersionCode": codigo_atual,
        "version": ultima_versao,
        "versionCode": codigo_remoto,
        "latestVersion": ultima_versao,
        "latestVersionCode": codigo_remoto,
        "updateAvailable": disponivel,
        "available": disponivel,
        "upToDate": not disponivel,
        "apkUrl": apk_url,
        "notes": str(manifesto.get("notes") or ""),
        "required": bool(manifesto.get("required", False)),
        "source": origem or "env",
        "checkedAt": datetime.utcnow().isoformat() + "Z",
    }
