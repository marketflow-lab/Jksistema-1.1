"""Privacy boundaries for persisted promotion audit reports."""

import json
import math

import pytest

from backend.services import renovacao_promocoes as promotions


ITEM = "MLB999400101"
SOURCE = "C-MLB999400001"
DESTINATION = "C-MLB999400002"
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJmaXh0dXJlIn0.signature_fixture"
TOKEN = "APP_USR-111222333-fixture-private"


def private_result():
    return {
        "success": False,
        "total_origem": 1,
        "Authorization": "Bearer fixture-header-secret",
        "nested": [{
            "access_token": TOKEN, "refresh_token": "fixture-refresh-secret",
            "cfg": {"user_id": "fixture-user", "private": "fixture-config-secret"},
            "headers": {"Authorization": "Bearer fixture-nested-header"},
            "raw": {"request_body": "fixture-raw-secret"},
            "public": {"item_id": ITEM, "campanha_origem_id": SOURCE},
        }],
        "conferencia": {"itens": [{
            "item_id": ITEM, "campanha_destino_id": DESTINATION,
            "preco_origem": 80.0, "preco_destino": 160.0,
            "base_destino": 200.0, "percentual_origem": 20.0,
            "Headers": {"Authorization": "fixture-case-secret"},
            "RAW": {"token": "fixture-uppercase-raw-secret"},
        }]},
        "erros_operacao": {ITEM: f"Falha para {ITEM}: Bearer fixture-error-secret; {JWT}; {TOKEN}"},
    }


def test_report_sanitizer_removes_secret_containers_recursively_and_preserves_audit_values():
    original = private_result()
    sanitized = promotions._renovacao_sanitizar_relatorio(original)
    serialized = json.dumps(sanitized)

    for forbidden in ("Authorization", "access_token", "refresh_token", "cfg", "headers", "raw"):
        def assert_no_key(value):
            if isinstance(value, dict):
                assert forbidden.lower() not in {str(key).lower() for key in value}
                for nested in value.values():
                    assert_no_key(nested)
            elif isinstance(value, list):
                for nested in value:
                    assert_no_key(nested)
        assert_no_key(sanitized)

    assert "fixture-config-secret" not in serialized
    assert "fixture-raw-secret" not in serialized
    assert "fixture-case-secret" not in serialized
    assert sanitized["nested"][0]["public"] == {"item_id": ITEM, "campanha_origem_id": SOURCE}
    row = sanitized["conferencia"]["itens"][0]
    assert (row["item_id"], row["campanha_destino_id"]) == (ITEM, DESTINATION)
    assert (row["preco_origem"], row["preco_destino"], row["base_destino"], row["percentual_origem"]) == (80, 160, 200, 20)
    assert original["Authorization"] == "Bearer fixture-header-secret"


@pytest.mark.parametrize("private_text", [
    "Bearer fixture-bearer-secret",
    JWT,
    TOKEN,
    "fixture.audit+private@example.test",
    r"C:\PrivateFixture\credentials.json",
    r"\\fixture-server\private-share\credentials.json",
    "/srv/private-fixture/credentials.json",
    "access_token=fixture-assignment-secret",
    'refresh_token: "fixture-refresh-assignment-secret"',
])
def test_operational_errors_redact_credentials_email_and_filesystem_paths(private_text):
    text = f"Falha ao conferir {ITEM} em {SOURCE}: {private_text}"
    sanitized = promotions._renovacao_sanitizar_relatorio({"erros_operacao": {ITEM: text}})
    error = sanitized["erros_operacao"][ITEM]

    assert private_text not in error
    assert "fixture-assignment-secret" not in error
    assert "fixture-refresh-assignment-secret" not in error
    assert ITEM in error and SOURCE in error
    assert "Falha ao conferir" in error


def test_non_finite_financial_numbers_become_null_without_changing_finite_prices():
    result = promotions._renovacao_sanitizar_relatorio({
        "preco_origem": math.nan,
        "preco_destino": math.inf,
        "percentual_destino": -math.inf,
        "base_destino": 200.0,
        "percentual_origem": 20.0,
        "extras": [math.nan, 80.0],
    })
    assert result == {
        "preco_origem": None, "preco_destino": None, "percentual_destino": None,
        "base_destino": 200.0, "percentual_origem": 20.0, "extras": [None, 80.0],
    }
    json.dumps(result, allow_nan=False)


def test_saved_report_contains_only_sanitized_audit_data(monkeypatch, tmp_path):
    tenant = tmp_path / "tenant-fixture"
    monkeypatch.setattr(promotions.ctx, "get_tenant_path", lambda client: str(tenant))
    result = private_result()
    result["conferencia"]["itens"][0]["percentual_destino"] = math.nan
    payload = {
        "loja": "Loja Fixture", "campanha_origem_id": SOURCE,
        "campanha_destino_id": DESTINATION, "somente_conferir": True,
        "Authorization": "Bearer fixture-payload-secret", "cfg": {"access_token": TOKEN},
    }

    promotions._renovacao_salvar_conferencia("tenant-fixture", "audit-fixture-001", payload, result)

    path = tenant / "renovacao_auditorias" / "audit-fixture-001.json"
    serialized = path.read_text(encoding="utf-8")
    saved = json.loads(serialized)
    assert saved["job_id"] == "audit-fixture-001"
    assert saved["somente_conferir"] is True
    assert (saved["campanha_origem_id"], saved["campanha_destino_id"]) == (SOURCE, DESTINATION)
    row = saved["resultado"]["conferencia"]["itens"][0]
    assert (row["item_id"], row["preco_destino"], row["base_destino"]) == (ITEM, 160, 200)
    assert row["percentual_destino"] is None
    for secret in (TOKEN, JWT, "fixture-payload-secret", "fixture-config-secret", "fixture-raw-secret",
                   "fixture-header-secret", "fixture-nested-header", "fixture-error-secret"):
        assert secret not in serialized
    assert not list(path.parent.glob("*.tmp"))


@pytest.mark.parametrize("job_id", ["../escape", r"..\escape", "nested/job"])
def test_report_identifier_cannot_select_a_path_outside_the_tenant(monkeypatch, tmp_path, job_id):
    tenant = tmp_path / "tenant-fixture"
    monkeypatch.setattr(promotions.ctx, "get_tenant_path", lambda client: str(tenant))
    with pytest.raises(ValueError):
        promotions._renovacao_salvar_conferencia("tenant-fixture", job_id, {}, {})
    assert not tenant.exists()
