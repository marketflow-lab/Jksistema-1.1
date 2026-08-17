from types import SimpleNamespace

from backend.services import admin_usuarios_common


class _CredentialsFake:
    caminho = ""
    scopes = []

    @classmethod
    def from_service_account_file(cls, caminho, scopes):
        cls.caminho = caminho
        cls.scopes = list(scopes)
        return {"credencial": caminho}


def _configurar_fakes(monkeypatch):
    _CredentialsFake.caminho = ""
    _CredentialsFake.scopes = []
    monkeypatch.setattr(admin_usuarios_common, "Credentials", _CredentialsFake, raising=False)
    monkeypatch.setattr(
        admin_usuarios_common,
        "gspread",
        SimpleNamespace(authorize=lambda credencial: {"cliente": credencial}),
        raising=False,
    )


def test_google_sheets_usa_credencial_canonica_quando_checkout_nao_tem_arquivo(
    monkeypatch,
    tmp_path,
):
    _configurar_fakes(monkeypatch)
    monkeypatch.setattr(
        admin_usuarios_common,
        "CREDENTIALS_FILE",
        str(tmp_path / "checkout" / "info" / "credentials.json"),
        raising=False,
    )
    appdata = tmp_path / "appdata"
    credencial_canonica = (
        appdata / "JK Sistema Cliente" / "local_app" / "info" / "credentials.json"
    )
    credencial_canonica.parent.mkdir(parents=True)
    credencial_canonica.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("APPDATA", str(appdata))

    cliente = admin_usuarios_common.autenticar_google_sheets()

    assert cliente == {"cliente": {"credencial": str(credencial_canonica)}}
    assert _CredentialsFake.caminho == str(credencial_canonica)
    assert "https://www.googleapis.com/auth/spreadsheets" in _CredentialsFake.scopes


def test_google_sheets_preserva_credencial_explicitamente_configurada(monkeypatch, tmp_path):
    _configurar_fakes(monkeypatch)
    credencial_configurada = tmp_path / "info-configurada" / "credentials.json"
    credencial_configurada.parent.mkdir(parents=True)
    credencial_configurada.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        admin_usuarios_common,
        "CREDENTIALS_FILE",
        str(credencial_configurada),
        raising=False,
    )

    appdata = tmp_path / "appdata"
    credencial_canonica = (
        appdata / "JK Sistema Cliente" / "local_app" / "info" / "credentials.json"
    )
    credencial_canonica.parent.mkdir(parents=True)
    credencial_canonica.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("APPDATA", str(appdata))

    admin_usuarios_common.autenticar_google_sheets()

    assert _CredentialsFake.caminho == str(credencial_configurada)
