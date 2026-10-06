"""Closed, bounded requests for resumable manual rankings."""
from __future__ import annotations

import json
from typing import Annotated, Literal
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, StringConstraints, model_validator

ShortId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")]
Text80 = Annotated[str, StringConstraints(max_length=80)]
Text160 = Annotated[str, StringConstraints(max_length=160)]
Text220 = Annotated[str, StringConstraints(max_length=220)]
Text500 = Annotated[str, StringConstraints(max_length=500)]
Url800 = Annotated[str, StringConstraints(max_length=800)]
NumberValue = float | Annotated[str, StringConstraints(max_length=40)] | None

# Covers 240 bounded candidates, including 8,000-character Unicode descriptions.
MAX_CHECKPOINT_BYTES = 24_000_000

class CheckpointModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)

    @model_validator(mode="after")
    def bounded_document(self):
        if len(json.dumps(self.model_dump(mode="json"), ensure_ascii=False).encode("utf-8")) > MAX_CHECKPOINT_BYTES:
            raise ValueError("Checkpoint excede o limite de tamanho.")
        return self

class FavoritosCheckpointOpcoes(CheckpointModel):
    usar_ia_ranking: bool = False
    avulso: bool | None = None
    max_anuncios: int = Field(default=80, ge=20, le=80)
    max_passadas: int = Field(default=2, ge=1, le=3)
    lote_cliques: int = Field(default=6, ge=0, le=8)
    tempo_limite_ms: Literal[90000] = 90000

class FavoritosCheckpointTermo(CheckpointModel):
    campo: int = Field(ge=1, le=3)
    termo: Text220 = Field(min_length=1)
    estado: Literal["pendente", "parcial", "concluida"] = "pendente"
    confirmed_keys: list[Text160] = Field(default_factory=list, max_length=80)
    pending_keys: list[Text160] = Field(default_factory=list, max_length=80)

class FavoritosCheckpointCandidato(CheckpointModel):
    id: Text80 = ""
    mlb: Text80 = ""
    url: Url800 = ""
    permalink: Url800 = ""
    link: Url800 = ""
    link_normalizado: Url800 = ""
    linkFonte: Text80 = ""
    link_fonte: Text80 = ""
    chave_canonica: Text160 = ""
    chaveCanonica: Text160 = ""
    titulo: Text500 = ""
    title: Text500 = ""
    descricao: Annotated[str, StringConstraints(max_length=8000)] = ""
    tituloFonte: Text80 = ""
    titulo_fonte: Text80 = ""
    vendedor: Text160 = ""
    seller_name: Text160 = ""
    seller_nickname: Text160 = ""
    official_store_name: Text160 = ""
    loja_vendedora: Text160 = ""
    vendedorFonte: Text80 = ""
    vendedor_fonte: Text80 = ""
    vendas: NumberValue = None
    vendasFonte: Text80 = ""
    vendas_fonte: Text80 = ""
    data_criacao: Text80 = ""
    dataCriacaoFonte: Text80 = ""
    data_criacao_fonte: Text80 = ""
    imagem: Url800 = ""
    thumbnail: Url800 = ""
    foto: Url800 = ""
    fotoFonte: Text80 = ""
    foto_fonte: Text80 = ""
    preco: NumberValue = None
    price: NumberValue = None
    preco_original: NumberValue = None
    original_price: NumberValue = None
    preco_promocional: NumberValue = None
    promotional_price: NumberValue = None
    fonte_preco: Text80 = ""
    precoFonte: Text80 = ""
    preco_fonte: Text80 = ""
    media_mensal: NumberValue = None
    ritmo_atual: NumberValue = None
    ritmo_vendas_mes: NumberValue = None
    ritmo_atual_fonte: Text80 = ""
    ritmo_vendas_mes_fonte: Text80 = ""
    media_vendas_mensal: NumberValue = None
    media_mensal_fonte: Text80 = ""
    meses_desde_criacao: NumberValue = None
    visitas: NumberValue = None
    posicao: int | None = Field(default=None, ge=0, le=10000)
    tipo_anuncio: Text80 = ""
    listing_type_id: Text80 = ""
    listing_type_name: Text80 = ""
    logistic_type: Text80 = ""
    shipping_mode: Text80 = ""
    is_full: bool | None = None
    condicao: Text80 = ""
    moeda: Text80 = ""
    currency_id: Text80 = ""
    parcelamento_sem_juros: bool | None = None
    origem_dados: Text80 = ""
    cacheAvant: bool = False
    estado_qualidade: Text80 = ""
    suspeito: bool = False
    capturado_em: Text80 = ""
    capturado_em_ms: int | None = Field(default=None, ge=0, validation_alias=AliasChoices("capturado_em_ms", "__capturadoEm"))
    pesquisas_origem: list[Text220] = Field(default_factory=list, max_length=3)
    campos_origem: list[Text80] = Field(default_factory=list, max_length=3)
    motivo_ia: Text500 = ""

class FavoritosCheckpointAmostra(CheckpointModel):
    posicao: int | None = Field(default=None, ge=0, le=10000)
    mlb: Text80 = ""
    id: Text80 = ""
    titulo: Text160 = ""
    title: Text160 = ""
    faltas: list[Annotated[str, StringConstraints(max_length=40)]] = Field(default_factory=list, max_length=10)
    origem_dados: Annotated[str, StringConstraints(max_length=120)] = ""
    tituloFonte: Annotated[str, StringConstraints(max_length=120)] = ""
    titulo_fonte: Annotated[str, StringConstraints(max_length=120)] = ""
    fotoFonte: Annotated[str, StringConstraints(max_length=120)] = ""
    foto_fonte: Annotated[str, StringConstraints(max_length=120)] = ""
    precoFonte: Annotated[str, StringConstraints(max_length=120)] = ""
    preco_fonte: Annotated[str, StringConstraints(max_length=120)] = ""
    vendasFonte: Annotated[str, StringConstraints(max_length=120)] = ""
    vendas_fonte: Annotated[str, StringConstraints(max_length=120)] = ""
    vendedorFonte: Annotated[str, StringConstraints(max_length=120)] = ""
    vendedor_fonte: Annotated[str, StringConstraints(max_length=120)] = ""
    dataCriacaoFonte: Annotated[str, StringConstraints(max_length=120)] = ""
    data_criacao_fonte: Annotated[str, StringConstraints(max_length=120)] = ""


Counter = Annotated[int, Field(ge=0, le=604800000)]
CounterMap = Annotated[dict[Text80, Counter], Field(max_length=30)]


class FavoritosCheckpointResumo(CheckpointModel):
    pesquisa: int = Field(default=1, ge=1, le=100)
    termo: Text220 = ""
    campo: Annotated[str, StringConstraints(max_length=40)] | int = ""
    visiveis: Counter = 0
    coletados: Counter = 0
    com_titulo: Counter = 0
    com_foto: Counter = 0
    com_preco: Counter = 0
    com_link: Counter = 0
    com_dados_avant: Counter = 0
    incompletos: Counter = 0
    suspeitos: Counter = 0
    avant_nao_vinculado: Counter = 0
    tempo_esgotado: bool = False
    login_avant_bloqueado: bool = False
    motivo_encerramento: Text80 = ""
    passadas: int = Field(default=0, ge=0, le=100)
    posicoes_percorridas: int = Field(default=0, ge=0, le=10000)
    cliques_avant: int = Field(default=0, ge=0, le=10000)
    capturados_avant: int = Field(default=0, ge=0, le=10000)
    tempo_navegacao_ms: Counter = 0
    tempo_materializacao_ms: Counter = 0
    tempo_avant_ms: Counter = 0
    tempo_finalizacao_ms: Counter = 0
    motivos_incompletos: CounterMap = Field(default_factory=dict)
    origens_dados: CounterMap = Field(default_factory=dict)
    amostras_incompletos: list[FavoritosCheckpointAmostra] = Field(default_factory=list, max_length=10)


class FavoritosCheckpointCreateRequest(CheckpointModel):
    request_id: ShortId
    loja: Text160 = Field(min_length=1)
    sku: Text80 = Field(default="AVULSO", min_length=1)
    titulo: Text500 = ""
    termos: list[FavoritosCheckpointTermo] = Field(min_length=1, max_length=3)
    opcoes: FavoritosCheckpointOpcoes = Field(default_factory=FavoritosCheckpointOpcoes)

    @model_validator(mode="after")
    def unique_terms(self):
        if len({item.campo for item in self.termos}) != len(self.termos):
            raise ValueError("Campos de pesquisa repetidos.")
        return self

class FavoritosCheckpointMutationRequest(CheckpointModel):
    request_id: ShortId
    loja: Text160 = Field(min_length=1)
    expected_revision: int = Field(ge=1)
    attempt_token: ShortId

class FavoritosCheckpointUpdateRequest(FavoritosCheckpointMutationRequest):
    estado_coleta: Literal["coletando", "parcial", "concluida", "cancelada", "erro"] | None = None
    termos: list[FavoritosCheckpointTermo] | None = Field(default=None, max_length=3)
    candidatos: list[FavoritosCheckpointCandidato] | None = Field(default=None, max_length=240)
    resumo_coleta: list[FavoritosCheckpointResumo] | None = Field(default=None, max_length=3)
    duracao_execucao_ms: int | None = Field(default=None, ge=0, le=604800000)

class FavoritosCheckpointResumeRequest(CheckpointModel):
    request_id: ShortId
    loja: Text160 = Field(min_length=1)
    expected_revision: int = Field(ge=1)

class FavoritosCheckpointFinalizeRequest(CheckpointModel):
    request_id: ShortId
    loja: Text160 = Field(min_length=1)
    expected_revision: int = Field(ge=1)
    attempt_token: ShortId | None = None
    recuperar_fechamento: bool = False
    grupo_ranking: dict | None = None
