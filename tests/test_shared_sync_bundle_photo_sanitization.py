import csv
import hashlib
import io
import json
import zipfile
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.services import cadastro_fotos
from backend.services import cadastro_fotos_coordenacao
from backend.services import integracoes
from backend.services import shared_sync  # noqa: F401 - configura o runtime dos modulos
from backend.services import shared_sync_apply_scope
from backend.services import shared_sync_bundle
from backend.services import shared_sync_delta


def _entry_from_path(path: Path, relative_path: str) -> dict:
    data = path.read_bytes()
    return {
        "relative_path": relative_path,
        "abs_path": str(path),
        "size": len(data),
        "mtime": path.stat().st_mtime,
        "sha256": hashlib.sha256(data).hexdigest(),
        "item_keys": [],
    }


def _csv_rows(data: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))


def _montar_bundle(monkeypatch, entries: list[dict], *, strict: bool):
    tenant_root = next(
        (
            str(Path(item["abs_path"]).parent)
            for item in entries
            if str(item.get("relative_path") or "").casefold()
            == "cadastro_produtos_lojas.csv"
            and item.get("abs_path")
        ),
        str(Path(entries[0]["abs_path"]).parent) if entries else ".",
    )
    monkeypatch.setattr(
        shared_sync_bundle,
        "get_tenant_path",
        lambda _client_id: tenant_root,
        raising=False,
    )
    monkeypatch.setattr(
        cadastro_fotos,
        "_cadastro_fotos_escopo_estrito",
        lambda _client_id: strict,
    )
    monkeypatch.setattr(
        shared_sync_bundle,
        "_shared_sync_coletar_arquivos",
        lambda *_args, **_kwargs: (entries, []),
    )
    return shared_sync_bundle._shared_sync_montar_pacote_locked(
        "000002",
        "cadastro",
        "operador",
        user_only=True,
    )


@pytest.mark.parametrize(
    ("referencia", "local"),
    [
        ("imagem.jpg", True),
        ("cadastro_fotos/imagem.jpg", True),
        ("api/cadastro/foto-arquivo/imagem.jpg", True),
        ("x/../api/cadastro/foto/000002/imagem.jpg", True),
        ("https:/api/cadastro/foto-arquivo/imagem.jpg", True),
        ("C:/dados/cadastro_fotos/imagem.jpg", True),
        ("file:///C:/dados/cadastro_fotos/imagem.jpg", True),
        ("foo/imagem.jpg", True),
        ("/qualquer/imagem.jpg", True),
        ("//cdn.example/imagem.jpg", False),
        ("ftp://cdn.example/imagem.jpg", False),
        ("s3://bucket/imagem.jpg", False),
        ("gs://bucket/imagem.png", False),
        ("blob:https://cdn.example/imagem.jpg", False),
        ("lojas/store-a/imagem.jpg", True),
        ("'cadastro_fotos/imagem.jpg'", True),
        ('"imagem.jpg"', True),
        ("`api/cadastro/foto-arquivo/imagem.jpg`", True),
        ("%27cadastro_fotos/imagem.jpg%27", True),
        ("https://cdn.example/imagem.jpg", False),
        ("data:image/jpeg;base64,AA==", False),
    ],
)
def test_detector_central_de_referencia_local_cobre_formatos_consumidos(
    referencia: str,
    local: bool,
) -> None:
    assert cadastro_fotos._cadastro_foto_referencia_local_cadastro(referencia) is local


@pytest.mark.parametrize(
    ("column", "candidate"),
    [
        ("foto", True),
        ("cg_foto", True),
        ("imagem", True),
        ("imagem_url", True),
        ("image_url", True),
        ("url_imagem", True),
        ("link_imagem", True),
        ("picture", True),
        ("thumbnail_url", True),
        ("link_foto", True),
        ("url", False),
        ("link", False),
        ("permalink", False),
        ("produto_url", False),
        ("anuncio_link", False),
        ("callback_url", False),
        ("manual_link", False),
    ],
)
def test_detector_central_limita_aliases_a_semantica_de_foto(
    column: str,
    candidate: bool,
) -> None:
    assert cadastro_fotos._cadastro_foto_coluna_candidata(column) is candidate


def test_bundle_estrito_preserva_campos_genericos_de_url_e_link(
    tmp_path,
    monkeypatch,
):
    cadastro = tmp_path / "cadastro_produtos.csv"
    cadastro.write_text(
        "sku,url,link,permalink,produto_url,anuncio_link,callback_url,manual_link\n"
        "001,cadastro_fotos/url.jpg,/produto/link.jpg,/p/permalink.jpg,"
        "/produto/001.jpg,/anuncio/001.jpg,/callback/001.jpg,/manual/001.jpg\n",
        encoding="utf-8-sig",
    )
    source = cadastro.read_bytes()

    bundle, _manifest, warnings = _montar_bundle(
        monkeypatch,
        [_entry_from_path(cadastro, cadastro.name)],
        strict=True,
    )

    assert warnings == []
    with zipfile.ZipFile(io.BytesIO(bundle), "r") as zf:
        assert zf.read("files/cadastro_produtos.csv") == source
    assert cadastro.read_bytes() == source


def test_bundle_estrito_limpa_referencias_locais_sem_alterar_fontes_ou_csv_de_lojas(
    tmp_path,
    monkeypatch,
):
    segmento = cadastro_fotos._cadastro_store_id_foto_segmento("store-a")
    photo_rel = f"cadastro_fotos/lojas/{segmento}/001.jpg"
    monkeypatch.setattr(
        cadastro_fotos,
        "_cadastro_carregar_lojas_foto",
        lambda _client_id: [{"store_id": "store-a"}],
    )
    cadastro = tmp_path / "cadastro_produtos.csv"
    compilado = tmp_path / "produtos_compilado.csv"
    por_loja = tmp_path / "cadastro_produtos_lojas.csv"
    cadastro.write_text(
        "sku,titulo,foto,cg_foto,imagem,image_url\n"
        "001,Local,cadastro_fotos/001.jpg,/api/cadastro/foto-arquivo/001.png,001.jpg,api/cadastro/foto/001\n"
        "002,Externo,https://cdn.example/002.jpg,https://images.example/cg-002.jpg,https://cdn.example/imagem-002.jpg,https://cdn.example/image-002.jpg\n"
        "003,Loja,lojas/store-a/003.png,,lojas/store-a/003.png,\n",
        encoding="utf-8-sig",
    )
    compilado.write_text(
        "sku,descricao,foto,cg_foto,link_imagem,picture\n"
        "004,Local direto,004.jpg,api/cadastro/foto/004,cadastro_fotos/004.jpg,/api/cadastro/foto-arquivo/004.jpg\n"
        "005,Externo,https://cdn.example/005.jpg,,https://cdn.example/link-005.jpg,https://cdn.example/picture-005.jpg\n",
        encoding="utf-8-sig",
    )
    por_loja.write_text(
        "sku,store_id,foto,cg_foto,row_version,updated_at_utc,deleted_at_utc\n"
        f"001,store-a,{photo_rel},,1,2026-09-02T10:00:00Z,\n",
        encoding="utf-8-sig",
    )
    photo = tmp_path / photo_rel
    photo.parent.mkdir(parents=True, exist_ok=True)
    photo.write_bytes(b"foto")
    fontes_antes = {
        path.name: path.read_bytes()
        for path in (cadastro, compilado, por_loja)
    }
    entries = [
        _entry_from_path(cadastro, cadastro.name),
        _entry_from_path(compilado, compilado.name),
        _entry_from_path(por_loja, por_loja.name),
        _entry_from_path(photo, photo_rel),
    ]

    bundle, manifest, warnings = _montar_bundle(monkeypatch, entries, strict=True)

    assert warnings == []
    with zipfile.ZipFile(io.BytesIO(bundle), "r") as zf:
        cadastro_bundle = zf.read("files/cadastro_produtos.csv")
        compilado_bundle = zf.read("files/produtos_compilado.csv")
        por_loja_bundle = zf.read("files/cadastro_produtos_lojas.csv")

    cadastro_rows = _csv_rows(cadastro_bundle)
    assert cadastro_rows[0]["foto"] == ""
    assert cadastro_rows[0]["cg_foto"] == ""
    assert cadastro_rows[0]["imagem"] == ""
    assert cadastro_rows[0]["image_url"] == ""
    assert cadastro_rows[1]["foto"] == "https://cdn.example/002.jpg"
    assert cadastro_rows[1]["cg_foto"] == "https://images.example/cg-002.jpg"
    assert cadastro_rows[1]["imagem"] == "https://cdn.example/imagem-002.jpg"
    assert cadastro_rows[1]["image_url"] == "https://cdn.example/image-002.jpg"
    assert cadastro_rows[2]["foto"] == ""
    assert cadastro_rows[2]["imagem"] == ""

    compilado_rows = _csv_rows(compilado_bundle)
    assert compilado_rows[0]["foto"] == ""
    assert compilado_rows[0]["cg_foto"] == ""
    assert compilado_rows[0]["link_imagem"] == ""
    assert compilado_rows[0]["picture"] == ""
    assert compilado_rows[1]["foto"] == "https://cdn.example/005.jpg"
    assert compilado_rows[1]["link_imagem"] == "https://cdn.example/link-005.jpg"
    assert compilado_rows[1]["picture"] == "https://cdn.example/picture-005.jpg"

    assert por_loja_bundle == fontes_antes["cadastro_produtos_lojas.csv"]
    assert {
        path.name: path.read_bytes()
        for path in (cadastro, compilado, por_loja)
    } == fontes_antes

    manifest_files = {
        item["relative_path"]: item
        for item in manifest["files"]
    }
    assert manifest_files["cadastro_produtos.csv"]["sha256"] == hashlib.sha256(
        cadastro_bundle
    ).hexdigest()
    assert manifest_files["produtos_compilado.csv"]["sha256"] == hashlib.sha256(
        compilado_bundle
    ).hexdigest()
    assert manifest_files["cadastro_produtos_lojas.csv"]["sha256"] == hashlib.sha256(
        por_loja_bundle
    ).hexdigest()


def test_bundle_nao_estrito_preserva_referencias_locais(tmp_path, monkeypatch):
    cadastro = tmp_path / "cadastro_produtos.csv"
    compilado = tmp_path / "produtos_compilado.csv"
    por_loja = tmp_path / "cadastro_produtos_lojas.csv"
    cadastro.write_text(
        "sku,foto,cg_foto\n001,cadastro_fotos/001.jpg,/api/cadastro/foto/001\n",
        encoding="utf-8-sig",
    )
    compilado.write_text(
        "sku,foto,cg_foto\n002,002.png,cadastro_fotos/002.png\n",
        encoding="utf-8-sig",
    )
    por_loja.write_text(
        "sku,store_id,foto\n001,store-a,lojas/store-a/001.jpg\n",
        encoding="utf-8-sig",
    )
    fontes = {
        path.name: path.read_bytes()
        for path in (cadastro, compilado, por_loja)
    }
    entries = [
        _entry_from_path(cadastro, cadastro.name),
        _entry_from_path(compilado, compilado.name),
        _entry_from_path(por_loja, por_loja.name),
    ]

    bundle, _manifest, warnings = _montar_bundle(monkeypatch, entries, strict=False)

    assert warnings == []
    with zipfile.ZipFile(io.BytesIO(bundle), "r") as zf:
        for relative_path, source_data in fontes.items():
            assert zf.read(f"files/{relative_path}") == source_data
    assert {
        path.name: path.read_bytes()
        for path in (cadastro, compilado, por_loja)
    } == fontes


def test_bundle_estrito_inclui_apenas_foto_de_linha_canonica_ativa(
    tmp_path,
    monkeypatch,
):
    segmento = cadastro_fotos._cadastro_store_id_foto_segmento("store-a")
    monkeypatch.setattr(
        cadastro_fotos,
        "_cadastro_carregar_lojas_foto",
        lambda _client_id: [{"store_id": "store-a"}],
    )
    canonico = tmp_path / "cadastro_produtos_lojas.csv"
    canonico.write_text(
        "store_id,sku,foto,row_version,updated_at_utc,deleted_at_utc\n"
        f"store-a,001,cadastro_fotos/lojas/{segmento}/001.jpg,1,2026-08-01T00:00:00Z,\n"
        f"store-a,002,cadastro_fotos/lojas/{segmento}/002.jpg,2,2026-08-02T00:00:00Z,2026-08-02T00:00:00Z\n",
        encoding="utf-8-sig",
    )
    fotos = []
    for nome in ("001.jpg", "002.jpg", "003.jpg"):
        rel = f"cadastro_fotos/lojas/{segmento}/{nome}"
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(nome.encode("ascii"))
        fotos.append(
            _entry_from_path(path, rel)
        )

    bundle, manifest, warnings = _montar_bundle(
        monkeypatch,
        [_entry_from_path(canonico, canonico.name), *fotos],
        strict=True,
    )

    assert warnings == []
    assert {item["relative_path"] for item in manifest["files"]} == {
        "cadastro_produtos_lojas.csv",
        f"cadastro_fotos/lojas/{segmento}/001.jpg",
    }
    with zipfile.ZipFile(io.BytesIO(bundle), "r") as arquivo:
        assert set(arquivo.namelist()) == {
            "manifest.json",
            "files/cadastro_produtos_lojas.csv",
            f"files/cadastro_fotos/lojas/{segmento}/001.jpg",
        }


def test_delta_foto_orfa_nao_e_marcada_conhecida_e_sai_apos_materializacao(
    tmp_path,
    monkeypatch,
):
    segmento = cadastro_fotos._cadastro_store_id_foto_segmento("store-a")
    photo_rel = f"cadastro_fotos/lojas/{segmento}/late.jpg"
    monkeypatch.setattr(
        cadastro_fotos,
        "_cadastro_carregar_lojas_foto",
        lambda _client_id: [{"store_id": "store-a"}],
    )
    foto = tmp_path / photo_rel
    foto.parent.mkdir(parents=True, exist_ok=True)
    foto.write_bytes(b"foto-tardia")
    foto_entry = _entry_from_path(
        foto,
        photo_rel,
    )

    bundle_orfao, manifest_orfao, _warnings = _montar_bundle(
        monkeypatch,
        [foto_entry],
        strict=True,
    )
    assert manifest_orfao["file_count"] == 0
    assert manifest_orfao["item_keys"] == []
    with zipfile.ZipFile(io.BytesIO(bundle_orfao), "r") as arquivo:
        assert arquivo.namelist() == ["manifest.json"]

    canonico = tmp_path / "cadastro_produtos_lojas.csv"
    canonico.write_text(
        "store_id,sku,foto,row_version,updated_at_utc,deleted_at_utc\n"
        f"store-a,late,{photo_rel},1,2026-08-03T00:00:00Z,\n",
        encoding="utf-8-sig",
    )
    canonico_entry = _entry_from_path(canonico, canonico.name)
    foto_key = (
        f"cadastro:file:{photo_rel}:revision:"
        + hashlib.sha256(b"foto-tardia").hexdigest()
    )
    row_key = "cadastro:cadastro_produtos_lojas.csv:store_id:store-a:sku:late:revision:row"
    monkeypatch.setattr(
        cadastro_fotos,
        "_cadastro_fotos_escopo_estrito",
        lambda _client_id: True,
    )
    monkeypatch.setattr(
        shared_sync_bundle,
        "_shared_sync_coletar_arquivos_delta",
        lambda *_args, **_kwargs: (
            [canonico_entry, foto_entry],
            [],
            [row_key, foto_key],
        ),
    )

    _bundle, manifest, warnings = shared_sync_bundle._shared_sync_montar_pacote_locked(
        "000002",
        "cadastro",
        "operador",
        user_only=True,
        known_keys=set(),
    )

    assert warnings == []
    assert manifest["delta"] is True
    assert set(manifest["item_keys"]) == {row_key, foto_key}
    assert {item["relative_path"] for item in manifest["files"]} == {
        "cadastro_produtos_lojas.csv",
        photo_rel,
    }


@pytest.mark.parametrize("delta", [False, True], ids=["full", "delta"])
def test_bundle_falha_se_linha_ativa_referencia_foto_ausente(
    tmp_path,
    monkeypatch,
    delta,
):
    segmento = cadastro_fotos._cadastro_store_id_foto_segmento("store-a")
    monkeypatch.setattr(
        cadastro_fotos,
        "_cadastro_carregar_lojas_foto",
        lambda _client_id: [{"store_id": "store-a"}],
    )
    canonico = tmp_path / "cadastro_produtos_lojas.csv"
    canonico.write_text(
        "store_id,sku,foto,row_version,updated_at_utc,deleted_at_utc\n"
        f"store-a,001,cadastro_fotos/lojas/{segmento}/001.jpg,1,2026-09-02T10:00:00Z,\n",
        encoding="utf-8-sig",
    )
    entry = _entry_from_path(canonico, canonico.name)
    if delta:
        monkeypatch.setattr(
            cadastro_fotos,
            "_cadastro_fotos_escopo_estrito",
            lambda _client_id: True,
        )
        monkeypatch.setattr(
            shared_sync_bundle,
            "_shared_sync_coletar_arquivos_delta",
            lambda *_args, **_kwargs: ([entry], [], ["row-key"]),
        )
        montar = lambda: shared_sync_bundle._shared_sync_montar_pacote_locked(
            "000002",
            "cadastro",
            "operador",
            user_only=True,
            known_keys=set(),
        )
    else:
        montar = lambda: _montar_bundle(monkeypatch, [entry], strict=True)

    with pytest.raises(HTTPException) as exc_info:
        montar()

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "shared_sync_store_photo_missing"


@pytest.mark.parametrize("delta", [False, True], ids=["full", "delta"])
@pytest.mark.parametrize(
    "motivo",
    ["arquivo maior que o limite", "erro de leitura"],
    ids=["oversized", "read-error"],
)
def test_bundle_falha_se_foto_ativa_existente_foi_omitida_da_coleta(
    tmp_path,
    monkeypatch,
    delta,
    motivo,
):
    segmento = cadastro_fotos._cadastro_store_id_foto_segmento("store-a")
    photo_rel = f"cadastro_fotos/lojas/{segmento}/001.jpg"
    canonico = tmp_path / "cadastro_produtos_lojas.csv"
    canonico.write_text(
        "store_id,sku,foto,row_version,updated_at_utc,deleted_at_utc\n"
        f"store-a,001,{photo_rel},1,2026-09-02T10:00:00Z,\n",
        encoding="utf-8-sig",
    )
    foto = tmp_path / photo_rel
    foto.parent.mkdir(parents=True, exist_ok=True)
    foto.write_bytes(b"bytes-presentes-mas-nao-coletados")
    entry = _entry_from_path(canonico, canonico.name)
    monkeypatch.setattr(
        cadastro_fotos,
        "_cadastro_carregar_lojas_foto",
        lambda _client_id: [{"store_id": "store-a"}],
    )

    monkeypatch.setattr(
        shared_sync_bundle,
        "get_tenant_path",
        lambda _client_id: str(tmp_path),
        raising=False,
    )
    monkeypatch.setattr(
        cadastro_fotos,
        "_cadastro_fotos_escopo_estrito",
        lambda _client_id: True,
    )
    warning = f"{photo_rel} ignorado: {motivo}."
    if delta:
        monkeypatch.setattr(
            shared_sync_bundle,
            "_shared_sync_coletar_arquivos_delta",
            lambda *_args, **_kwargs: ([entry], [warning], ["row-key"]),
        )
        kwargs = {"known_keys": set()}
    else:
        monkeypatch.setattr(
            shared_sync_bundle,
            "_shared_sync_coletar_arquivos",
            lambda *_args, **_kwargs: ([entry], [warning]),
        )
        kwargs = {}

    with pytest.raises(HTTPException) as exc_info:
        shared_sync_bundle._shared_sync_montar_pacote_locked(
            "000002",
            "cadastro",
            "operador",
            user_only=True,
            **kwargs,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "shared_sync_store_photo_missing"
    assert exc_info.value.detail["files"] == [photo_rel]


def test_delta_com_linha_canonica_reinclui_foto_ja_conhecida(
    tmp_path,
    monkeypatch,
):
    segmento = cadastro_fotos._cadastro_store_id_foto_segmento("store-a")
    photo_rel = f"cadastro_fotos/lojas/{segmento}/001.jpg"
    canonico = tmp_path / "cadastro_produtos_lojas.csv"
    canonico.write_text(
        "store_id,sku,nome,foto,row_version,updated_at_utc,deleted_at_utc\n"
        f"store-a,001,Metadado novo,{photo_rel},2,2026-09-02T11:00:00Z,\n",
        encoding="utf-8-sig",
    )
    foto = tmp_path / photo_rel
    foto.parent.mkdir(parents=True, exist_ok=True)
    foto.write_bytes(b"foto-ja-conhecida")
    entries = [
        _entry_from_path(canonico, canonico.name),
        _entry_from_path(foto, photo_rel),
    ]
    photo_key = (
        f"cadastro:file:{photo_rel}:revision:"
        f"{hashlib.sha256(foto.read_bytes()).hexdigest()}"
    )
    monkeypatch.setattr(
        shared_sync_delta,
        "_shared_sync_coletar_arquivos",
        lambda *_args, **_kwargs: (entries, []),
    )

    delta_entries, warnings, item_keys = (
        shared_sync_delta._shared_sync_coletar_arquivos_delta(
            "000002",
            "cadastro",
            known_keys={photo_key},
        )
    )

    assert warnings == []
    assert {item["relative_path"] for item in delta_entries} == {
        "cadastro_produtos_lojas.csv",
        photo_rel,
    }
    assert photo_key not in item_keys


@pytest.mark.parametrize(
    "payload",
    [
        json.dumps(
            {
                "schema": cadastro_fotos.CADASTRO_FOTOS_CONFIG_SCHEMA,
                "strict_store_scope": True,
                "shared_groups": [],
                "extra": True,
            }
        ).encode("utf-8"),
        json.dumps(
            {
                "schema": cadastro_fotos.CADASTRO_FOTOS_CONFIG_SCHEMA,
                "strict_store_scope": True,
                "shared_groups": [
                    {
                        "group_id": "grupo",
                        "store_ids": ["store-a", "store-b"],
                        "extra": True,
                    }
                ],
            }
        ).encode("utf-8"),
        (
            b'{"schema":"jk.cadastro.fotos.v1",'
            b'"strict_store_scope":true,"strict_store_scope":true,'
            b'"shared_groups":[]}'
        ),
        b"{" + (b" " * (256 * 1024)) + b"}",
    ],
    ids=["top-extra", "group-extra", "duplicate-key", "oversized"],
)
@pytest.mark.parametrize("delta", [False, True], ids=["full", "delta"])
def test_bundle_rejeita_config_fora_do_schema_fechado(
    tmp_path,
    monkeypatch,
    payload,
    delta,
):
    (tmp_path / "lojas_config.json").write_text(
        json.dumps(
            [
                {"store_id": "store-a", "nome": "Loja A"},
                {"store_id": "store-b", "nome": "Loja B"},
            ]
        ),
        encoding="utf-8",
    )
    config = tmp_path / cadastro_fotos.CADASTRO_FOTOS_CONFIG_ARQUIVO
    config.write_bytes(payload)
    entry = _entry_from_path(config, config.name)
    monkeypatch.setattr(
        shared_sync_bundle,
        "get_tenant_path",
        lambda _client_id: str(tmp_path),
        raising=False,
    )
    if delta:
        delta_entry = dict(entry)
        delta_entry["data"] = config.read_bytes()
        delta_entry.pop("abs_path", None)
        monkeypatch.setattr(
            shared_sync_bundle,
            "_shared_sync_coletar_arquivos_delta",
            lambda *_args, **_kwargs: ([delta_entry], [], ["config-key"]),
        )
        kwargs = {"known_keys": set()}
    else:
        monkeypatch.setattr(
            shared_sync_bundle,
            "_shared_sync_coletar_arquivos",
            lambda *_args, **_kwargs: ([entry], []),
        )
        kwargs = {}

    with pytest.raises(HTTPException) as exc_info:
        shared_sync_bundle._shared_sync_montar_pacote_locked(
            "000002",
            "cadastro",
            "operador",
            user_only=True,
            **kwargs,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "cadastro_photo_config_invalid"


@pytest.mark.parametrize("delta", [False, True], ids=["full", "delta"])
def test_bundle_canonicaliza_config_valida_com_parser_do_receiver(
    tmp_path,
    monkeypatch,
    delta,
):
    (tmp_path / "lojas_config.json").write_text(
        json.dumps(
            [
                {"store_id": "store-a", "nome": "Loja A"},
                {"store_id": "store-b", "nome": "Loja B"},
            ]
        ),
        encoding="utf-8",
    )
    config = tmp_path / cadastro_fotos.CADASTRO_FOTOS_CONFIG_ARQUIVO
    config.write_text(
        json.dumps(
            {
                "shared_groups": [
                    {
                        "store_ids": ["store-b", "store-a"],
                        "group_id": "grupo",
                    }
                ],
                "strict_store_scope": True,
                "schema": cadastro_fotos.CADASTRO_FOTOS_CONFIG_SCHEMA,
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    entry = _entry_from_path(config, config.name)
    monkeypatch.setattr(
        shared_sync_bundle,
        "get_tenant_path",
        lambda _client_id: str(tmp_path),
        raising=False,
    )
    monkeypatch.setattr(
        cadastro_fotos,
        "_cadastro_fotos_escopo_estrito",
        lambda _client_id: True,
    )
    if delta:
        delta_entry = dict(entry)
        delta_entry["data"] = config.read_bytes()
        delta_entry.pop("abs_path", None)
        monkeypatch.setattr(
            shared_sync_bundle,
            "_shared_sync_coletar_arquivos_delta",
            lambda *_args, **_kwargs: ([delta_entry], [], ["config-key"]),
        )
        kwargs = {"known_keys": set()}
    else:
        monkeypatch.setattr(
            shared_sync_bundle,
            "_shared_sync_coletar_arquivos",
            lambda *_args, **_kwargs: ([entry], []),
        )
        kwargs = {}

    bundle, manifest, warnings = shared_sync_bundle._shared_sync_montar_pacote_locked(
        "000002",
        "cadastro",
        "operador",
        user_only=True,
        **kwargs,
    )

    assert warnings == []
    with zipfile.ZipFile(io.BytesIO(bundle), "r") as arquivo:
        canonical = arquivo.read("files/cadastro_fotos_config.json")
    parsed = json.loads(canonical.decode("utf-8"))
    assert parsed["shared_groups"][0]["store_ids"] == ["store-a", "store-b"]
    assert canonical.endswith(b"\n")
    assert manifest["files"][0]["sha256"] == hashlib.sha256(canonical).hexdigest()


@pytest.mark.parametrize("ext", ["gif", "bmp"])
@pytest.mark.parametrize("delta", [False, True], ids=["full", "delta"])
def test_bundle_inclui_config_e_foto_store_gif_bmp(
    tmp_path,
    monkeypatch,
    ext,
    delta,
):
    segmento = cadastro_fotos._cadastro_store_id_foto_segmento("store-a")
    monkeypatch.setattr(
        cadastro_fotos,
        "_cadastro_carregar_lojas_foto",
        lambda _client_id: [{"store_id": "store-a"}],
    )
    config = tmp_path / cadastro_fotos.CADASTRO_FOTOS_CONFIG_ARQUIVO
    (tmp_path / "lojas_config.json").write_text(
        json.dumps([{"store_id": "store-a", "nome": "Loja A"}]),
        encoding="utf-8",
    )
    config.write_text(
        json.dumps(
            {
                "schema": cadastro_fotos.CADASTRO_FOTOS_CONFIG_SCHEMA,
                "strict_store_scope": True,
                "shared_groups": [],
            }
        ),
        encoding="utf-8",
    )
    canonico = tmp_path / "cadastro_produtos_lojas.csv"
    photo_rel = f"cadastro_fotos/lojas/{segmento}/001.{ext}"
    canonico.write_text(
        "store_id,sku,foto,row_version,updated_at_utc,deleted_at_utc\n"
        f"store-a,001,{photo_rel},1,2026-09-02T10:00:00Z,\n",
        encoding="utf-8-sig",
    )
    photo = tmp_path / photo_rel
    photo.parent.mkdir(parents=True, exist_ok=True)
    photo.write_bytes(b"imagem")
    entries = [
        _entry_from_path(config, config.name),
        _entry_from_path(canonico, canonico.name),
        _entry_from_path(photo, photo_rel),
    ]
    if delta:
        monkeypatch.setattr(
            cadastro_fotos,
            "_cadastro_fotos_escopo_estrito",
            lambda _client_id: True,
        )
        monkeypatch.setattr(
            shared_sync_bundle,
            "_shared_sync_coletar_arquivos_delta",
            lambda *_args, **_kwargs: (entries, [], ["config", "row", "photo"]),
        )
        _bundle_data, manifest, warnings = (
            shared_sync_bundle._shared_sync_montar_pacote_locked(
                "000002",
                "cadastro",
                "operador",
                user_only=True,
                known_keys=set(),
            )
        )
    else:
        _bundle_data, manifest, warnings = _montar_bundle(
            monkeypatch,
            entries,
            strict=True,
        )

    assert warnings == []
    assert {item["relative_path"] for item in manifest["files"]} == {
        cadastro_fotos.CADASTRO_FOTOS_CONFIG_ARQUIVO,
        "cadastro_produtos_lojas.csv",
        photo_rel,
    }


@pytest.mark.parametrize("scope", ["lojas_integracoes", "cadastro", "vendas"])
def test_bundle_publico_monta_scope_protegido_inteiramente_dentro_do_lock(
    monkeypatch,
    scope,
):
    eventos = []

    @contextmanager
    def bloquear(client_id):
        assert client_id == "000002"
        eventos.append("lock-enter")
        try:
            yield
        finally:
            eventos.append("lock-exit")

    def montar(*args, **kwargs):
        eventos.append("montar")
        return b"bundle", {"scope": scope}, []

    monkeypatch.setattr(integracoes, "_integracoes_bloquear_rmw_lojas", bloquear)
    monkeypatch.setattr(shared_sync_bundle, "_shared_sync_montar_pacote_locked", montar)

    resultado = shared_sync_bundle._shared_sync_montar_pacote(
        "000002",
        scope,
        "operador",
    )

    assert resultado == (b"bundle", {"scope": scope}, [])
    assert eventos == ["lock-enter", "montar", "lock-exit"]


def test_bundle_publico_nao_monta_quando_lock_falha(monkeypatch):
    eventos = []

    @contextmanager
    def bloquear(_client_id):
        eventos.append("lock-enter")
        raise HTTPException(
            status_code=409,
            detail={"code": "cadastro_photo_transition_busy"},
        )
        yield  # pragma: no cover - mantem a funcao como context manager

    def montar(*_args, **_kwargs):
        eventos.append("montar")
        return b"", {}, []

    monkeypatch.setattr(integracoes, "_integracoes_bloquear_rmw_lojas", bloquear)
    monkeypatch.setattr(shared_sync_bundle, "_shared_sync_montar_pacote_locked", montar)

    with pytest.raises(HTTPException) as exc_info:
        shared_sync_bundle._shared_sync_montar_pacote(
            "000002",
            "cadastro",
            "operador",
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "cadastro_photo_transition_busy"
    assert eventos == ["lock-enter"]


def test_bundle_publico_scope_sem_catalogo_de_loja_nao_adquire_lock(monkeypatch):
    def bloquear(_client_id):
        raise AssertionError("scope sem catalogo nao deve adquirir o lock")

    monkeypatch.setattr(integracoes, "_integracoes_bloquear_rmw_lojas", bloquear)
    monkeypatch.setattr(
        shared_sync_bundle,
        "_shared_sync_montar_pacote_locked",
        lambda *_args, **_kwargs: (b"bundle", {"scope": "favoritos_historico"}, []),
    )

    assert shared_sync_bundle._shared_sync_montar_pacote(
        "000002",
        "favoritos_historico",
        "operador",
    ) == (b"bundle", {"scope": "favoritos_historico"}, [])


@pytest.mark.parametrize(
    ("error_code", "expected_code"),
    [
        ("locked", "cadastro_photo_transition_busy"),
        ("unsafe_tenant", "cadastro_photo_transition_unavailable"),
        ("lock_unsafe", "cadastro_photo_transition_unavailable"),
        ("lock_unavailable", "cadastro_photo_transition_unavailable"),
    ],
)
def test_shared_sync_distingue_contenda_de_indisponibilidade_do_lock_de_fotos(
    monkeypatch,
    error_code,
    expected_code,
):
    @contextmanager
    def falhar(*_args, **_kwargs):
        raise cadastro_fotos_coordenacao.CadastroFotosCoordenacaoErro(
            error_code,
            "Falha de coordenacao simulada.",
        )
        yield  # pragma: no cover - mantem a funcao como context manager

    monkeypatch.setattr(
        cadastro_fotos_coordenacao,
        "bloquear_transicao_fotos_tenant",
        falhar,
    )

    with pytest.raises(HTTPException) as exc_info:
        with shared_sync_apply_scope._shared_sync_bloquear_transicao_fotos_tenant(
            "tenant-seguro"
        ):
            raise AssertionError("o corpo nao deve ser executado")

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == expected_code
