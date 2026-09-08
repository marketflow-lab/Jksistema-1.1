"""Delete unscoped legacy public examples and their exact migrated copies.

Reports counts only. Current editorial changes and canonical products are kept.
Historical generations stay immutable; scope validation prevents rollback into
the invalid general/SKU layout. No backup of the deleted examples is created.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path

from backend.modules.context_hub.curation import approve_curated_note, review_curated_note, validate_curated_note
from backend.modules.context_hub.filesystem import _write_text_atomic
from backend.modules.context_hub.paths import _tenant_paths
from backend.modules.context_hub.storage import _connect
from backend.modules.context_hub.store_sku_contracts import canonical_json
from backend.modules.context_hub.store_sku_editor import load_store_guidance_editor, save_store_guidance_editor
from backend.modules.context_hub.store_sku_repository import publish_store_sku_generation
from backend.modules.context_hub.store_sku_repository_support import _generation_knowledge


def repair(client_id, *, info_root, apply=False):
    paths = _tenant_paths(client_id, info_root=info_root)
    legacy = paths.tenant_dir / 'ia_treinamento_perguntas_pos_venda.json'
    original = legacy.read_text(encoding='utf-8-sig')
    payload = json.loads(original)
    examples = (payload.get('exemplos') or {}).get('perguntas_anuncio') or []
    # Global storage is not proof of store ownership, regardless of signatures.
    unknown = {canonical_json(example) for example in examples}
    report = {'legacy_examples_deleted': len(examples), 'editorial_copies_deleted': 0,
              'published_copies_deleted': 0, 'stores_changed': 0, 'applied': apply}

    def cleaned(value):
        result = deepcopy(value)
        before = result.get('exemplos_perguntas') or []
        after = [example for example in before if canonical_json(example) not in unknown]
        if len(after) != len(before):
            result['exemplos_perguntas'] = after
        return result, len(before) - len(after)

    with _connect(paths) as connection:
        stores = connection.execute('''SELECT a.*, g.store_name FROM
            context_hub_store_sku_active_generations a JOIN context_hub_store_sku_generations g
            ON g.generation_id=a.generation_id''').fetchall()
    for store in stores:
        scope = {key: store[key] for key in ('store_ref', 'store_name', 'seller_id', 'site_id', 'surface')}
        snapshot = load_store_guidance_editor(client_id, scope, info_root=info_root)
        generation = store['generation_id']
        with _connect(paths) as connection:
            canonical, general, sku_notes = _generation_knowledge(connection, generation)
            bindings = [dict(row) for row in connection.execute(
                'SELECT item_id, variation_id, sku FROM context_hub_store_sku_bindings WHERE generation_id=?', (generation,))]
        general, removed = cleaned(general)
        published_removed = removed
        changed_slots = {''} if removed else set()
        for sku, value in sku_notes.items():
            sku_notes[sku], removed = cleaned(value)
            published_removed += removed
            if removed:
                changed_slots.add(sku)
        edits = []
        for sku, value in {'': snapshot['guidance']['general'], **snapshot['sku_guidance']}.items():
            replacement, removed = cleaned(value)
            if removed:
                edits.append((sku, replacement))
                report['editorial_copies_deleted'] += removed
        report['published_copies_deleted'] += published_removed
        if not edits and not published_removed:
            continue
        report['stores_changed'] += 1
        if not apply:
            continue
        for sku, replacement in edits:
            snapshot = save_store_guidance_editor(client_id, scope, guidance=replacement, sku=sku,
                expected_revision=snapshot['editorial']['revision'], actor='approved-scope-repair', info_root=info_root)
        # Resume approval after interruption as well as after a fresh edit.
        for sku in changed_slots:
            replacement = snapshot['sku_guidance'].get(sku, {}) if sku else snapshot['guidance']['general']
            if replacement == (sku_notes.get(sku, {}) if sku else general):
                entry = snapshot['editorial']['skus'][sku] if sku else snapshot['editorial']['general']
                steps = [] if entry['status'] in {'approved', 'published'} else (
                    [approve_curated_note] if entry['status'] == 'reviewed' else
                    [validate_curated_note, review_curated_note, approve_curated_note])
                for transition in steps:
                    transition(client_id, entry['note_id'], actor='approved-scope-repair', info_root=info_root)
                snapshot = load_store_guidance_editor(client_id, scope, info_root=info_root)
        if published_removed:
            publish_store_sku_generation(client_id, scope, canonical_documents=canonical,
                store_guidance=general, sku_guidance=sku_notes, bindings=bindings,
                preserve_curated_files=True, expected_active_generation_id=generation,
                actor='approved-scope-repair', info_root=info_root)
    if apply and examples:
        if legacy.read_text(encoding='utf-8-sig') != original:
            raise RuntimeError('Legacy training changed during repair; rerun the repair.')
        payload['exemplos']['perguntas_anuncio'] = []
        _write_text_atomic(legacy, json.dumps(payload, ensure_ascii=False, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tenant', required=True)
    parser.add_argument('--info-root', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    print(json.dumps(repair(args.tenant, info_root=args.info_root.resolve(), apply=args.apply)))
