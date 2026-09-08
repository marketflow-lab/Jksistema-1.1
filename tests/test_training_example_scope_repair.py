import json

import pytest

from backend.modules.context_hub import api as hub
from backend.modules.context_hub import guidance_examples
from backend.modules.context_hub.contracts import ContextHubValidationError
from backend.modules.context_hub.paths import _tenant_paths
from backend.modules.context_hub.store_sku_editor import load_store_guidance_editor, save_store_guidance_editor
from backend.modules.context_hub.store_sku_repository import publish_store_sku_generation, rollback_store_sku_generation
from scripts.repair_store_training_examples import repair

STORE = {'store_ref': 'store-a', 'store_name': 'Loja A', 'seller_id': '123', 'site_id': 'MLB'}
EXAMPLE = {'sku': '0007', 'pergunta': 'Qual medida?', 'resposta': 'Medida antiga.'}


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    info = tmp_path / 'info'
    info.mkdir()
    hub.configure_context_hub(base_dir=tmp_path, info_root=info, surface='test')
    with monkeypatch.context() as old:
        old.setattr(guidance_examples, 'validate_guidance_examples', lambda *args: None)
        generation = publish_store_sku_generation('tenant-a', STORE,
            canonical_documents={'0007': {'sku': '0007', 'nome': 'Produto A'}},
            store_guidance={'orientacoes_perguntas': 'Texto geral preservar', 'exemplos_perguntas': [EXAMPLE]},
            sku_guidance={'0007': {'notas': 'Nota propria preservar'}},
            bindings=[{'item_id': 'MLB100', 'sku': '0007'}], info_root=info)['generation_id']
    paths = _tenant_paths('tenant-a', info_root=info)
    legacy = paths.tenant_dir / 'ia_treinamento_perguntas_pos_venda.json'
    legacy.write_text(json.dumps({'exemplos': {'perguntas_anuncio': [EXAMPLE], 'pos_venda': []},
        'por_loja': {'store_id:store-b': {'exemplos': {'perguntas_anuncio': [EXAMPLE]}}}}), encoding='utf-8')
    return info, generation, legacy


def test_repair_deletes_global_copies_preserves_rules_and_is_idempotent(seeded):
    info, generation, legacy = seeded
    before = load_store_guidance_editor('tenant-a', STORE, info_root=info)
    preview = repair('tenant-a', info_root=info)
    assert preview['legacy_examples_deleted'] == preview['editorial_copies_deleted'] == preview['published_copies_deleted'] == 1
    assert load_store_guidance_editor('tenant-a', STORE, info_root=info)['generation_id'] == generation
    result = repair('tenant-a', info_root=info, apply=True)
    after = load_store_guidance_editor('tenant-a', STORE, info_root=info)
    assert result['stores_changed'] == 1
    assert after['guidance']['general']['exemplos_perguntas'] == []
    assert after['guidance']['general']['orientacoes_perguntas'] == 'Texto geral preservar'
    assert after['sku_guidance'] == before['sku_guidance']
    assert after['editorial']['general']['status'] == 'published'
    assert after['published_guidance']['guidance']['general'] == after['guidance']['general']
    data = json.loads(legacy.read_text(encoding='utf-8'))
    assert data['exemplos']['perguntas_anuncio'] == []
    assert data['por_loja']['store_id:store-b']['exemplos']['perguntas_anuncio'] == [EXAMPLE]
    assert repair('tenant-a', info_root=info, apply=True)['stores_changed'] == 0
    with pytest.raises(ContextHubValidationError):
        rollback_store_sku_generation('tenant-a', STORE, generation, info_root=info)
    assert load_store_guidance_editor('tenant-a', STORE, info_root=info)['generation_id'] == after['generation_id']


def test_repair_preserves_unpublished_obsidian_edit(seeded):
    info, _, _ = seeded
    before = load_store_guidance_editor('tenant-a', STORE, info_root=info)
    target = _tenant_paths('tenant-a', info_root=info).curated_dir / before['editorial']['general']['relative_path']
    target.write_text(target.read_text(encoding='utf-8').replace('Texto geral preservar', 'Texto novo pendente') + '\n## Personalizado\nPreservar este texto.\n', encoding='utf-8')
    repair('tenant-a', info_root=info, apply=True)
    after = load_store_guidance_editor('tenant-a', STORE, info_root=info)
    assert after['guidance']['general']['orientacoes_perguntas'] == 'Texto novo pendente'
    assert after['published_guidance']['guidance']['general']['orientacoes_perguntas'] == 'Texto geral preservar'
    assert after['editorial']['general']['status'] == 'draft'
    assert 'Preservar este texto.' in target.read_text(encoding='utf-8')


def test_repair_resumes_after_failure_without_reintroducing_examples(seeded, monkeypatch):
    import scripts.repair_store_training_examples as operation
    info, old_generation, legacy = seeded
    with monkeypatch.context() as failure:
        def unavailable(*args, **kwargs):
            raise RuntimeError('publication interrupted')
        failure.setattr(operation, 'publish_store_sku_generation', unavailable)
        with pytest.raises(RuntimeError):
            repair('tenant-a', info_root=info, apply=True)
    before = load_store_guidance_editor('tenant-a', STORE, info_root=info)
    assert before['generation_id'] == old_generation
    assert before['guidance']['general']['exemplos_perguntas'] == []
    assert json.loads(legacy.read_text())['exemplos']['perguntas_anuncio'] == [EXAMPLE]
    repair('tenant-a', info_root=info, apply=True)
    after = load_store_guidance_editor('tenant-a', STORE, info_root=info)
    assert after['editorial']['general']['status'] == 'published'
    assert after['published_guidance']['guidance']['general']['exemplos_perguntas'] == []


@pytest.mark.parametrize('sku', ['', '0008'])
def test_editor_rejects_wrong_example_scope_without_writing(seeded, sku):
    info, _, _ = seeded
    repair('tenant-a', info_root=info, apply=True)
    before = load_store_guidance_editor('tenant-a', STORE, info_root=info)
    with pytest.raises(ContextHubValidationError):
        save_store_guidance_editor('tenant-a', STORE, guidance={'exemplos_perguntas': [EXAMPLE]}, sku=sku,
            expected_revision=before['editorial']['revision'], info_root=info)
    assert load_store_guidance_editor('tenant-a', STORE, info_root=info)['editorial']['revision'] == before['editorial']['revision']
