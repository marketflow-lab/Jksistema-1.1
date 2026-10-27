"""Offline invariants for targeted repair, independent of full-copy completeness."""

import copy
import json
from types import SimpleNamespace

import pytest
import requests
from fastapi import HTTPException

from backend.services import renovacao_promocoes as service
from backend.services.renovacao_correcao import _renovacao_corrigir_divergencias_promocao as repair


TENANT = "tenant-repair-test"
STORE = "store-repair-test"
SOURCE = "C-MLB99001001"
DESTINATION = "C-MLB99001002"
DIVERGENT = "MLB99001101"
MISSING = "MLB99001102"
CORRECT = "MLB99001103"
UNCERTAIN = "MLB99001104"
EXTRA = "MLB99001105"
NEW_ITEM = "MLB99001106"


def member(campaign, item, price=80, base=100, percentage=20):
    return {"situacao": "participante", "raw": {"id": campaign, "item_id": item,
        "type": "SELLER_CAMPAIGN", "status": "started" if campaign == SOURCE else "pending",
        "price": price, "original_price": base, "discount_percentage": percentage}}


def absent():
    return {"situacao": "ausente", "raw": {}}


def unknown(error="Consulta recebeu HTTP 409"):
    return {"situacao": "inconclusivo", "erro": error}


@pytest.fixture
def world(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Unexpected network request")

    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr(service.ctx, "_ml_api_request", forbidden)
    monkeypatch.setattr(service.ctx, "_ml_obter_promocoes_item", forbidden)
    monkeypatch.setattr(service.ctx, "_parse_float_flex", lambda value: float(value) if value is not None else None)
    monkeypatch.setattr(service.ctx, "_ml_extrair_preco_promocao_raw",
                        lambda raw, **kwargs: (raw.get("price"), raw.get("discount_percentage")))
    state = SimpleNamespace(
        source={item: member(SOURCE, item) for item in (DIVERGENT, MISSING, CORRECT)},
        destination={DIVERGENT: member(DESTINATION, DIVERGENT, price=90, percentage=10),
                     CORRECT: member(DESTINATION, CORRECT), EXTRA: member(DESTINATION, EXTRA)},
        expected=3, audits=[], lookups=[], mutations=[], batches=[], bases=[], invalidations=[],
        after_audit=None, before_apply=None, fail_final=False, failures={}, lookup_overrides={},
        cfg={"user_id": "seller-test", "access_token": "private-not-for-report"},
    )

    def audit(client, store, source, destination, *args, **kwargs):
        assert (client, store, source, destination) == (TENANT, STORE, SOURCE, DESTINATION)
        assert kwargs["somente_conferir"] is True
        assert kwargs["quantidade_origem_esperada"] == state.expected
        state.audits.append(kwargs)
        if state.fail_final and len(state.audits) > 1:
            raise HTTPException(503, "Final audit temporarily unavailable")
        states = {item: {SOURCE: state.source.get(item, absent()),
                        DESTINATION: state.destination.get(item, absent())}
                  for item in set(state.source) | set(state.destination)}
        conference = service._renovacao_montar_conferencia(states, SOURCE, DESTINATION, state.expected)
        source_items = [item for item, data in state.source.items() if data["situacao"] == "participante"]
        present = sum(state.destination.get(item, {}).get("situacao") == "participante" for item in source_items)
        errors = [{"item_id": row["item_id"], "erro": row["erro"]}
                  for row in conference["itens"] if not row["confirmado"]]
        errors.extend(conference["inconclusivos"])
        result = {"success": conference["completa"], "loja": STORE,
            "origem": {"id": SOURCE}, "campanha_destino": {"id": DESTINATION},
            "somente_conferir": True, "total_origem": conference["total_origem"],
            "total_origem_esperado": state.expected, "mlbs_ja_presentes": present,
            "faltantes": len(source_items) - present, "incluidos": 0, "atualizados": 0,
            "falhas": errors[:50], "falhas_total": len(errors), "conferencia": conference,
            "erros_operacao": {}}
        if state.after_audit:
            state.after_audit(len(state.audits))
        return result

    def cfg(client, store):
        assert (client, store) == (TENANT, STORE)
        return state.cfg

    def lookup(client, store, config, item, campaigns):
        assert (client, store) == (TENANT, STORE)
        assert campaigns == [SOURCE, DESTINATION]
        assert config["user_id"] == "seller-test"
        state.lookups.append(item)
        override = state.lookup_overrides.get(item)
        if override:
            return override(item), config
        return {SOURCE: copy.deepcopy(state.source.get(item, absent())),
                DESTINATION: copy.deepcopy(state.destination.get(item, absent()))}, config

    def details(client, store, config, items):
        assert (client, store) == (TENANT, STORE)
        state.batches.append(list(items))
        return [{"id": item, "original_price": 100} for item in items], config

    def base(client, store, config, item, detail):
        assert (client, store) == (TENANT, STORE)
        state.bases.append(item)
        return detail.get("original_price"), config

    def apply(client, store, config, item, destination, percentage, base, source_price, **kwargs):
        assert (client, store, destination) == (TENANT, STORE, DESTINATION)
        assert kwargs["campanha_origem_id"] == SOURCE
        assert kwargs["preferir_percentual"] is True
        state.mutations.append((item, kwargs["metodo"], percentage, base))
        if state.before_apply:
            state.before_apply(item)
        failures_left = state.failures.get(item, 0)
        if failures_left:
            state.failures[item] = failures_left - 1
            return False, "Provider did not confirm the expected discount", config
        state.destination[item] = member(DESTINATION, item,
            price=service._renovacao_preco_com_desconto(base, percentage), base=base, percentage=percentage)
        return True, "", config

    monkeypatch.setattr(service, "_renovacao_sincronizar_promocao_existente", audit)
    monkeypatch.setattr(service.ctx, "_obter_cfg_ml", cfg)
    monkeypatch.setattr(service, "_renovacao_consultar_participacoes", lookup)
    monkeypatch.setattr(service.ctx, "_ml_buscar_itens_batch", details)
    monkeypatch.setattr(service, "_renovacao_preco_base_atual", base)
    monkeypatch.setattr(service, "_renovacao_adicionar_item_campanha", apply)
    monkeypatch.setattr(service.ctx, "_cache_invalidar_loja", lambda client, store: state.invalidations.append((client, store)))
    return state


def execute(world, **kwargs):
    return repair(TENANT, STORE, SOURCE, DESTINATION,
                  quantidade_origem_esperada=world.expected, **kwargs)


def test_repairs_only_initial_divergent_and_absent_items_and_independently_audits_again(world):
    result = execute(world)
    assert result["success"] is True
    assert result["somente_conferir"] is False
    assert result["corrigir_divergencias"] is True
    assert world.mutations == [(DIVERGENT, "PUT", 20, 100), (MISSING, "POST", 20, 100)]
    assert set(world.lookups) == {DIVERGENT, MISSING}
    assert world.batches == [[MISSING]]
    assert world.bases == [MISSING]
    assert len(world.audits) == 2
    assert world.invalidations == [(TENANT, STORE)]
    assert result["correcao"]["corrigidos"] == 2
    assert result["correcao"]["incluidos"] == 1
    assert result["correcao"]["atualizados"] == 1
    assert result["correcao"]["pendentes"] == 0
    assert result["correcao"]["passagens"] == 1
    assert result["conferencia"]["extras_destino"] == [EXTRA]
    rows = {row["item_id"]: row for row in result["correcao"]["itens"]}
    assert rows[DIVERGENT]["antes"]["preco_destino"] == 90
    assert rows[DIVERGENT]["depois"]["preco_destino"] == 80
    assert all(row["depois"]["confirmado"] for row in rows.values())
    assert "private-not-for-report" not in json.dumps(result)


def test_repairs_confirmed_items_despite_count_mismatch_without_claiming_full_success(world):
    world.expected = 2
    result = execute(world)
    assert len(world.mutations) == 2
    assert result["correcao"]["corrigidos"] == 2
    assert result["correcao"]["pendentes"] == 0
    assert result["success"] is False
    assert result["conferencia"]["contagem_coerente"] is False
    assert result["total_origem"] == 3
    assert result["total_origem_esperado"] == 2


@pytest.mark.parametrize("status", [400, 409])
def test_persistent_lookup_error_is_requeried_twice_and_never_authorizes_a_write(world, status):
    world.source[UNCERTAIN] = unknown(f"Consulta recebeu HTTP {status}")
    result = execute(world)
    assert world.lookups.count(UNCERTAIN) == 2
    assert UNCERTAIN not in {call[0] for call in world.mutations}
    assert result["correcao"]["corrigidos"] == 2
    assert result["correcao"]["pendentes"] == 1
    assert result["success"] is False


def test_inconclusive_source_can_recover_before_any_write(world):
    world.source[UNCERTAIN] = unknown()

    def recover(item):
        assert item == UNCERTAIN
        if world.lookups.count(item) == 1:
            return {SOURCE: unknown(), DESTINATION: unknown()}
        world.source[item] = member(SOURCE, item)
        return {SOURCE: world.source[item], DESTINATION: absent()}

    world.lookup_overrides[UNCERTAIN] = recover
    world.expected = 4
    result = execute(world)
    assert world.lookups.count(UNCERTAIN) == 2
    assert [call for call in world.mutations if call[0] == UNCERTAIN] == [(UNCERTAIN, "POST", 20, 100)]
    assert result["correcao"]["corrigidos"] == 3
    assert result["correcao"]["pendentes"] == 0
    assert result["success"] is True


def test_destination_inconclusive_never_turns_into_an_inclusion(world):
    def unknown_target(item):
        return {SOURCE: world.source[item], DESTINATION: unknown()}

    world.lookup_overrides[DIVERGENT] = unknown_target
    result = execute(world)
    assert world.lookups.count(DIVERGENT) == 2
    assert world.mutations == [(MISSING, "POST", 20, 100)]
    assert result["correcao"]["pendentes"] == 1


def test_item_removed_from_source_is_skipped_without_touching_destination(world):
    def remove(item):
        world.source.pop(item, None)
        return {SOURCE: absent(), DESTINATION: world.destination[item]}

    world.lookup_overrides[DIVERGENT] = remove
    result = execute(world)
    assert DIVERGENT not in {call[0] for call in world.mutations}
    assert world.destination[DIVERGENT]["raw"]["price"] == 90
    assert result["correcao"]["fora_origem"] == 1
    assert DIVERGENT in result["conferencia"]["extras_destino"]
    assert result["success"] is False


def test_current_origin_percentage_replaces_the_audit_snapshot_value(world):
    def change_after_initial_audit(number):
        if number == 1:
            world.source[DIVERGENT] = member(SOURCE, DIVERGENT, price=70, percentage=30)

    world.after_audit = change_after_initial_audit
    result = execute(world)
    assert (DIVERGENT, "PUT", 30, 100) in world.mutations
    row = next(row for row in result["correcao"]["itens"] if row["item_id"] == DIVERGENT)
    assert row["antes"]["percentual_origem"] == 20
    assert row["depois"]["percentual_origem"] == 30
    assert row["depois"]["preco_destino"] == 70


def test_concurrently_regularized_item_is_not_updated_again(world):
    def regularize(number):
        if number == 1:
            world.destination[DIVERGENT] = member(DESTINATION, DIVERGENT)

    world.after_audit = regularize
    result = execute(world)
    assert world.mutations == [(MISSING, "POST", 20, 100)]
    assert result["correcao"]["corrigidos"] == 1
    assert result["correcao"]["ja_regularizados"] == 1
    assert result["success"] is True


def test_failed_correction_is_retried_once_and_still_needs_final_confirmation(world):
    world.failures[DIVERGENT] = 1
    result = execute(world)
    assert [call[0] for call in world.mutations].count(DIVERGENT) == 2
    assert [call[0] for call in world.mutations].count(MISSING) == 1
    assert result["correcao"]["tentativas"] == 3
    assert result["correcao"]["passagens"] == 2
    assert result["correcao"]["corrigidos"] == 2
    assert result["success"] is True
    row = next(row for row in result["correcao"]["itens"] if row["item_id"] == DIVERGENT)
    assert [trial["confirmado_apos_tentativa"] for trial in row["tentativas"]] == [False, True]


def test_persistent_provider_failure_stops_after_two_passes(world):
    world.failures[DIVERGENT] = 100
    result = execute(world)
    assert [call[0] for call in world.mutations].count(DIVERGENT) == 2
    assert result["correcao"]["tentativas"] == 3
    assert result["correcao"]["corrigidos"] == 1
    assert result["correcao"]["pendentes"] == 1
    assert result["success"] is False
    assert DIVERGENT in result["erros_operacao"]


def test_successful_helper_cannot_override_a_divergent_independent_final_audit(world):
    def reintroduce_divergence(item):
        if item == MISSING:
            world.destination[DIVERGENT] = member(DESTINATION, DIVERGENT, price=90, percentage=10)

    world.before_apply = reintroduce_divergence
    result = execute(world)
    assert [call[0] for call in world.mutations].count(DIVERGENT) == 1
    assert result["correcao"]["corrigidos"] == 1
    assert result["correcao"]["pendentes"] == 1
    assert result["success"] is False


def test_newly_discovered_divergence_is_reported_but_does_not_expand_the_initial_scope(world):
    def add_item(item):
        world.source[NEW_ITEM] = member(SOURCE, NEW_ITEM)
        world.destination[NEW_ITEM] = member(DESTINATION, NEW_ITEM, price=90, percentage=10)

    world.before_apply = add_item
    result = execute(world)
    assert NEW_ITEM not in {call[0] for call in world.mutations}
    assert NEW_ITEM not in result["correcao"]["ids_candidatos"]
    assert any(row["item_id"] == NEW_ITEM and not row["confirmado"] for row in result["conferencia"]["itens"])
    assert result["success"] is False


def test_no_discrepancies_need_one_audit_and_no_credentials_or_mutation(world, monkeypatch):
    world.destination[DIVERGENT] = member(DESTINATION, DIVERGENT)
    world.destination[MISSING] = member(DESTINATION, MISSING)
    monkeypatch.setattr(service.ctx, "_obter_cfg_ml", lambda *args: pytest.fail("No candidate needs a config"))
    result = execute(world)
    assert result["success"] is True
    assert result["correcao"]["candidatos"] == 0
    assert result["correcao"]["passagens"] == 0
    assert len(world.audits) == 1
    assert not world.mutations and not world.lookups and not world.invalidations


def test_final_audit_failure_preserves_attempt_history_without_claiming_confirmation(world):
    world.fail_final = True
    result = execute(world)
    assert len(world.mutations) == 2
    assert result["success"] is False
    assert result["correcao"]["corrigidos"] == 0
    assert result["correcao"]["pendentes"] == 2
    assert result["correcao"]["conferencia_final_inconclusiva"] is True
    assert result["conferencia"]["confirmados"] == 0
    assert result["falhas_total"] == 3
    assert all(not row["confirmado"] for row in result["conferencia"]["itens"])
    assert all(row["preco_destino"] is None for row in result["conferencia"]["itens"])
    assert all(row["tentativas"] for row in result["correcao"]["itens"])


def test_final_audit_failure_counts_all_rows_and_unknowns_without_duplicate_failure_ids(world):
    world.source[UNCERTAIN] = unknown()
    world.fail_final = True
    result = execute(world)
    assert result["falhas_total"] == 4
    assert {row["item_id"] for row in result["falhas"]} == {DIVERGENT, MISSING, CORRECT, UNCERTAIN}
    assert result["correcao"]["pendentes"] == 3


def test_all_discrepancies_are_selected_beyond_the_top_50_failure_summary(world):
    participants = [f"MLB{999500000 + number}" for number in range(1098)]
    divergent = set(participants[:51])
    missing = set(participants[51:69])
    uncertain = {f"MLB{999600000 + number}" for number in range(14)}
    extras = {f"MLB{999700000 + number}" for number in range(10)}
    world.source = {item: member(SOURCE, item) for item in participants}
    world.source.update({item: unknown(f"Consulta recebeu HTTP {400 if number < 9 else 409}")
                         for number, item in enumerate(sorted(uncertain))})
    world.destination = {item: member(DESTINATION, item, price=90 if item in divergent else 80,
                                     percentage=10 if item in divergent else 20)
                         for item in participants if item not in missing}
    world.destination.update({item: member(DESTINATION, item) for item in extras})
    world.expected = 908
    result = execute(world)
    assert result["correcao"]["candidatos"] == 83
    assert set(result["correcao"]["ids_candidatos"]) == divergent | missing | uncertain
    assert len(world.mutations) == 69
    assert {item for item, method, *_ in world.mutations if method == "PUT"} == divergent
    assert {item for item, method, *_ in world.mutations if method == "POST"} == missing
    assert all(world.lookups.count(item) == 2 for item in uncertain)
    assert set(world.batches[0]) == missing
    assert result["correcao"]["corrigidos"] == 69
    assert result["correcao"]["pendentes"] == 14
    assert result["correcao"]["confirmados_antes"] == 1029
    assert result["correcao"]["confirmados_depois"] == 1098
    assert result["correcao"]["contagem_coerente_antes"] is False
    assert result["correcao"]["contagem_coerente_depois"] is False
    assert result["falhas_total"] == 14
    assert result["conferencia"]["extras_destino"] == sorted(extras)
    assert result["success"] is False


def test_unconfirmed_store_account_prevents_any_correction(world):
    world.cfg.pop("user_id")
    with pytest.raises(HTTPException) as error:
        execute(world)
    assert error.value.status_code == 502
    assert not world.lookups and not world.mutations


def test_missing_current_price_base_stays_pending_instead_of_compounding_discount(world, monkeypatch):
    monkeypatch.setattr(service, "_renovacao_preco_base_atual", lambda client, store, cfg, *args: (None, cfg))
    result = execute(world)
    assert world.mutations == [(DIVERGENT, "PUT", 20, 100)]
    assert result["correcao"]["pendentes"] == 1
    assert result["success"] is False
    assert "Preco base atual nao confirmado" in result["erros_operacao"][MISSING]


def test_progress_stays_ordered_between_initial_audit_repairs_and_final_audit(world):
    progress = []
    result = execute(world, progress_callback=lambda payload: progress.append(payload["progress"]))
    assert result["success"] is True
    assert progress == sorted(progress)
    assert progress and min(progress) >= 35 and max(progress) <= 75
