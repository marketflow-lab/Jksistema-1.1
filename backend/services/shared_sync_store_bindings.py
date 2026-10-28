"""Private, directional store bindings for validated user-share imports."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from fastapi import HTTPException

from backend.services.shared_sync_common import _shared_sync_resolve_tenant_path
from backend.services.shared_sync_merge_integracoes import _shared_sync_loja_key, _shared_sync_validar_identidades_lojas
from backend.services.shared_sync_store_identity import _remote_key, _relation, plan_store_reconciliation


BINDINGS_FILE = 'shared_sync_store_bindings.json'
_CONTEXT_KEYS = ('link_id', 'sync_direction', 'source_client_id', 'source_username',
                 'target_client_id', 'target_username')


def _fail(message, code='shared_sync_store_binding_invalid'):
    raise HTTPException(409, {'code': code, 'message': message})


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def validate_peer(client_id, peer, source_client_id=''):
    if not isinstance(peer, dict) or set(peer) != set(_CONTEXT_KEYS):
        _fail('O contexto do compartilhamento de lojas é inválido.')
    result = {key: str(peer.get(key) or '').strip() for key in _CONTEXT_KEYS}
    if (not all(result.values()) or result['target_client_id'] != str(client_id).strip()
            or result['sync_direction'] not in {'source_to_target', 'target_to_source'}
            or any(len(value) > 256 for value in result.values())
            or (source_client_id and result['source_client_id'] != str(source_client_id).strip())):
        _fail('O pacote e os participantes do compartilhamento não correspondem.')
    return result


def _read_ledger(tenant, peer):
    path = Path(_shared_sync_resolve_tenant_path(tenant, BINDINGS_FILE))
    original = path.read_bytes() if path.exists() else None
    try:
        ledger = json.loads(original.decode('utf-8-sig')) if original is not None else {'schema': 1, 'peers': {}}
        if not isinstance(ledger, dict) or ledger.get('schema') != 1 or not isinstance(ledger.get('peers'), dict):
            raise ValueError()
        entry = ledger['peers'].get(_hash(peer))
        if entry is not None and (not isinstance(entry, dict) or entry.get('identity') != peer or not isinstance(entry.get('stores'), dict)):
            raise ValueError()
        for remote_id, row in (entry or {}).get('stores', {}).items():
            if (not isinstance(remote_id, str) or not remote_id or not isinstance(row, dict)
                    or set(row) - {'unverified_providers'} not in (
                        {'local_id', 'local_name', 'remote_name', 'accounts', 'connection_hash'},
                        {'local_id', 'local_name', 'remote_name', 'accounts', 'connection_hash', 'remote_connection_hash'})
                    or not isinstance(row['local_id'], str) or not row['local_id']
                    or not isinstance(row['remote_name'], str)
                    or not isinstance(row['local_name'], str)
                    or not isinstance(row['accounts'], dict) or not row['accounts']
                    or not re.fullmatch(r'[0-9a-f]{64}', str(row['connection_hash']))
                    or ('remote_connection_hash' in row
                        and not re.fullmatch(r'[0-9a-f]{64}', str(row['remote_connection_hash'])))):
                raise ValueError()
            if 'unverified_providers' in row:
                providers = row['unverified_providers']
                if (not isinstance(providers, list) or not providers
                        or any(not isinstance(provider, str) or provider not in {'mercadolivre', 'bling'}
                               for provider in providers)
                        or providers != sorted(set(providers))):
                    raise ValueError()
            for provider, identity in row['accounts'].items():
                if (provider not in {'mercadolivre', 'bling'} or not isinstance(identity, list)
                        or len(identity) != 2 or not all(isinstance(value, str) for value in identity)
                        or not identity[0]):
                    raise ValueError()
    except (ValueError, TypeError, UnicodeError):
        _fail('O mapa local de lojas não pôde ser validado.')
    return ledger, entry or {'identity': peer, 'stores': {}}, original


def _configs(store):
    from backend.services.shared_sync_merge_integracoes import _shared_sync_servico_key
    configs = store.get('integracoes') or {}
    return {_shared_sync_servico_key(key): value for key, value in configs.items() if isinstance(value, dict)}


def _unidentified_inactive_account(config):
    # An explicitly invalid connection without an account claim is not proof
    # of a second account. It can only be set aside after another integration
    # of this same store has been authenticated; never trust its old tokens.
    return (config.get('connected') is False and config.get('oauth_invalid') is True
            and not any(config.get(key) is not None and str(config[key]).strip()
                        for key in ('user_id', 'seller_id', 'account_id', 'empresa_id')))


def _account_providers(store):
    # An empty UI configuration or an application ID does not identify an
    # account. It must not make an unrelated, not-yet-connected store a match
    # candidate for every incoming account on that platform.
    providers = set()
    for provider, config in _configs(store).items():
        if provider not in {'mercadolivre', 'bling'}:
            continue
        keys = ('access_token', 'refresh_token', 'user_id', 'seller_id',
                'account_id', 'empresa_id') + (('token',) if provider == 'bling' else ())
        if config.get('connected') is True or any(str(config.get(key) or '').strip() for key in keys):
            providers.add(provider)
    return providers


def _connection_hash(store):
    # Never persist credentials. Token rotation invalidates this cached proof;
    # the next use verifies the account again without refreshing either token.
    keys = ('app_id', 'client_id', 'id', 'client_secret', 'secret_key', 'secret',
            'access_token', 'token', 'refresh_token', 'user_id', 'seller_id',
            'account_id', 'empresa_id', 'site_id', 'connected')
    return _hash({provider: {
        **{key: cfg[key] for key in keys if key in cfg},
        # Keep existing hashes for normal connections, while invalidating an
        # optional-provider proof if only its invalid state changes later.
        **({'oauth_invalid': True} if _unidentified_inactive_account(cfg) else {}),
    } for provider, cfg in _configs(store).items() if provider in {'mercadolivre', 'bling'}})


def source_connection_hashes(data):
    """Fingerprint frozen source connections without exporting their values."""
    try:
        stores = json.loads(data.decode('utf-8-sig'))
    except (ValueError, UnicodeError):
        _fail('Não foi possível conferir as conexões antes do envio.')
    if not isinstance(stores, list):
        _fail('Não foi possível conferir as conexões antes do envio.')
    _shared_sync_validar_identidades_lojas(stores, origem='Lojas de origem', status_code=409)
    return {_remote_key(store): _connection_hash(store) for store in stores}


def validate_source_connections(value):
    if value is not None and (not isinstance(value, dict) or any(
            not isinstance(key, str) or not key or key != key.strip() or len(key) > 256
            or not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest)
            for key, digest in value.items())):
        _fail('A prova das conexões de origem é inválida.', 'shared_sync_store_binding_stale')
    return value


def _verify_accounts(local, remote, pairs, *, remote_keys=()):
    from backend.services.central_accounts_migration import _expected_identity, _identity
    records = {'local': {row['store_id']: row for row in local},
               'remote': {_remote_key(row): row for row in remote}}
    selected = {'local': set(), 'remote': set(remote_keys)}
    for remote_key, local_key in pairs:
        selected['local'].add(local_key)
        selected['remote'].add(remote_key)
    tasks, references, needs_other_account = {}, [], set()
    for side in selected:
        for key in selected[side]:
            for provider, config in _configs(records[side][key]).items():
                if provider in {'mercadolivre', 'bling'} and _unidentified_inactive_account(config):
                    needs_other_account.add((side, key))
                    continue
                if provider not in _account_providers(records[side][key]):
                    continue
                access = str(config.get('access_token') or (config.get('token') if provider == 'bling' else '') or '').strip()
                if not access or config.get('connected') is False:
                    _fail('Conecte as contas das lojas nas duas máquinas antes de confirmar sua correspondência.', 'shared_sync_store_account_unverifiable')
                task = (provider, access)
                tasks[task] = None
                references.append((side, key, provider, config, task))
    result = {'local': {}, 'remote': {}}
    try:
        # Only GET identity calls from the existing integration are used here.
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {task: pool.submit(_identity, *task) for task in tasks}
            identities = {task: future.result() for task, future in futures.items()}
        for side, key, provider, config, task in references:
            account, site = identities[task]
            expected, expected_site = _expected_identity(config, provider)
            if not account or (expected and str(account) != expected) or (expected_site and str(site) != expected_site):
                _fail('A identidade retornada pela plataforma diverge da conexão da loja.', 'shared_sync_store_account_mismatch')
            result[side].setdefault(key, {})[provider] = (str(account), str(site or ''))
    except HTTPException as exc:
        if isinstance(exc.detail, dict) and str(exc.detail.get('code', '')).startswith('shared_sync_store_'):
            raise
        _fail('Não foi possível confirmar as contas das lojas. Confira as conexões e tente receber novamente.', 'shared_sync_store_account_unverifiable')
    except Exception:
        _fail('Não foi possível confirmar as contas das lojas. Tente receber novamente.', 'shared_sync_store_account_unverifiable')
    if any(not result[side].get(key) for side, key in needs_other_account):
        _fail('Conecte ao menos uma conta desta loja antes de confirmar sua correspondência.', 'shared_sync_store_account_unverifiable')
    return result


def _same_claim(left, right):
    from backend.services.central_accounts_migration import _expected_identity
    for provider in ('mercadolivre', 'bling'):
        a, _ = _expected_identity(_configs(left).get(provider, {}), provider)
        b, _ = _expected_identity(_configs(right).get(provider, {}), provider)
        if a and a == b:
            return True
    return False

def prepare_store_import(client_id, tenant, local_bytes, remote_bytes, peer):
    from backend.services import integracoes
    peer = validate_peer(client_id, peer)
    local, remote = json.loads(local_bytes or b'[]'), json.loads(remote_bytes)
    if not isinstance(local, list) or not isinstance(remote, list):
        _fail('A lista de lojas recebida é inválida.')
    _shared_sync_validar_identidades_lojas(local, origem='Lojas locais', status_code=409)
    _shared_sync_validar_identidades_lojas(remote, origem='Snapshot remoto', status_code=502)
    local = deepcopy(local)
    for row in local:
        integracoes._integracoes_store_id(client_id, row)
    ledger, entry, previous_bytes = _read_ledger(tenant, peer)
    from backend.services.shared_sync_merge_sqlite import _shared_sync_store_ids_tombstonados
    removed = _shared_sync_store_ids_tombstonados(integracoes._integracoes_ler_tombstones_estrito(client_id))
    remote_keys = {_remote_key(row) for row in remote}
    previous = {key: item['local_id'] for key, item in entry['stores'].items() if key in remote_keys}
    local_ids = {row['store_id'] for row in local}
    # A deleted target is not authority for restoring its old ID. Rebuild
    # only during Lojas reception, using fresh proof of an active survivor.
    rebuild = {key for key, target in previous.items()
               if target in removed and target not in local_ids}
    pairs = []
    for incoming in remote:
        key = _remote_key(incoming)
        for existing in local:
            same_name = _shared_sync_loja_key(existing.get('nome')) == _shared_sync_loja_key(incoming.get('nome'))
            common_providers = _account_providers(existing) & _account_providers(incoming)
            if (previous.get(key) == existing['store_id']
                    or ((existing['store_id'] != key or key in rebuild)
                        and (common_providers or same_name or _same_claim(existing, incoming)))):
                pairs.append((key, existing['store_id']))
                if existing['store_id'] in removed:
                    _fail('Uma das lojas foi excluída nesta máquina. Revise o vínculo antes de receber novamente.')
    # Even an empty destination must reject a snapshot that names the same
    # authenticated account twice. Empty/app-only configurations are not proof.
    remote_providers = {}
    for incoming in remote:
        for provider in _account_providers(incoming):
            remote_providers.setdefault(provider, set()).add(_remote_key(incoming))
    remote_candidates = {key for keys in remote_providers.values() if len(keys) > 1 for key in keys}
    # The optional-provider exception also needs fresh proof for a single new
    # store arriving at an empty destination, where no pair exists yet.
    remote_candidates.update(
        _remote_key(row) for row in remote
        if any(provider in {'mercadolivre', 'bling'} and _unidentified_inactive_account(config)
               for provider, config in _configs(row).items())
    )
    accounts = _verify_accounts(local, remote, pairs, remote_keys=remote_candidates)
    for remote_key, local_key in pairs:
        if not (accounts['local'].get(local_key, {}).keys() & accounts['remote'].get(remote_key, {}).keys()):
            _fail('Não foi possível comprovar a conta de duas lojas com identidades diferentes.', 'shared_sync_store_account_unverifiable')
    for key in rebuild:
        candidates = {row['store_id'] for row in local if row['store_id'] not in removed
                      and _relation(accounts['local'].get(row['store_id'], {}), accounts['remote'].get(key, {})) == 'match'}
        if len(candidates) != 1:
            _fail('A loja vinculada foi removida e não há uma única loja ativa com a mesma conta confirmada.')
        previous[key] = next(iter(candidates))
    for incoming in remote:
        key = _remote_key(incoming)
        candidates = {row['store_id'] for row in local if row['store_id'] != key
                      and _relation(accounts['local'].get(row['store_id'], {}), accounts['remote'].get(key, {})) == 'match'}
        if any(row['store_id'] == key for row in local) and candidates:
            # Empty-looking copies are not safe to remove during a live import:
            # queued sales, purchase lists and editorial writers may still hold
            # this ID. Consolidation needs its own quiescent data transaction.
            _fail('Já existem cópias desta loja nesta máquina. A consolidação precisa ser revisada antes de retirar qualquer cópia ou importar seus dados.', 'shared_sync_store_consolidation_required')
    try:
        plan = plan_store_reconciliation(local, remote, verified_accounts=accounts,
                                        previous_mapping=previous)
    except HTTPException as exc:
        if isinstance(exc.detail, dict) and exc.detail.get('reason') == 'duplicate_remote_account':
            raise HTTPException(exc.status_code, {
                **exc.detail,
                'message': 'O envio contém lojas com contas repetidas ou conflitantes. Revise as lojas na origem antes de receber novamente.',
            }) from exc
        raise
    by_id = {row['store_id']: row for row in plan['local_stores']}
    for incoming in plan['remote_stores']:
        if incoming['store_id'] in plan['matched_local_ids']:
            original = by_id[incoming['store_id']]
            incoming['nome'] = original['nome']
            incoming['integracoes'] = deepcopy(original.get('integracoes') or {})
    records = deepcopy(entry['stores'])
    originals = {_remote_key(row): row for row in remote}
    for key, local_id in plan['mapping'].items():
        if key == local_id:
            records.pop(key, None)
            continue
        original = by_id[local_id]
        records[key] = {'local_id': local_id, 'local_name': original['nome'], 'remote_name': originals[key]['nome'],
                        'accounts': accounts['local'][local_id], 'connection_hash': _connection_hash(original),
                        'remote_connection_hash': _connection_hash(originals[key])}
        unverified = set(entry['stores'].get(key, {}).get('unverified_providers', []))
        unverified.update(provider for row in (original, originals[key])
                          for provider, config in _configs(row).items()
                          if provider in {'mercadolivre', 'bling'} and _unidentified_inactive_account(config))
        # Removing a disconnected configuration is not proof of the company
        # behind its historical data. Clear a restriction only after both
        # current connections authenticate as the same account.
        unverified = sorted(provider for provider in unverified
                            if not accounts['local'].get(local_id, {}).get(provider)
                            or accounts['local'][local_id][provider] != accounts['remote'].get(key, {}).get(provider))
        if unverified:
            records[key]['unverified_providers'] = unverified
    ledger['peers'][_hash(peer)] = {'identity': peer, 'stores': records}
    changed = records != entry['stores']
    plan['binding_bytes'] = json.dumps(ledger, sort_keys=True, ensure_ascii=False, indent=2).encode() if records or changed else previous_bytes
    plan['binding_preimage'] = previous_bytes
    return plan


def remap_bound_sources(client_id, tenant, peer, scope, sources, source_connections=None):
    from backend.services import integracoes
    from backend.services.shared_sync_merge_sqlite import _shared_sync_store_ids_tombstonados
    from backend.services.shared_sync_store_remap import remap_store_sources, sources_reference_store
    peer = validate_peer(client_id, peer)
    _, entry, _ = _read_ledger(tenant, peer)
    if not entry['stores']:
        return sources
    path = Path(_shared_sync_resolve_tenant_path(tenant, 'lojas_config.json'))
    local = json.loads(path.read_text(encoding='utf-8-sig'))
    by_id = {row['store_id']: row for row in local}
    removed = _shared_sync_store_ids_tombstonados(integracoes._integracoes_ler_tombstones_estrito(client_id))
    ids, labels = {}, {}
    for remote_id, record in entry['stores'].items():
        local_id = record['local_id']
        candidate_ids = {} if remote_id.startswith('legacy:') else {remote_id: local_id}
        current = by_id.get(local_id)
        candidate_labels = {record['remote_name']: current['nome'] if current else record['local_name']}
        # Matching Mercado Livre does not prove the company behind historical
        # Bling sales. Include unchanged name-only legacy references here too.
        if (scope == 'vendas' and 'bling' in record.get('unverified_providers', [])
                and sources_reference_store(scope, sources, '' if remote_id.startswith('legacy:') else remote_id,
                                            record['remote_name'])):
            _fail('Reconecte o Bling nas lojas afetadas e confirme a mesma conta nas duas máquinas. Depois envie e receba Lojas e integrações e Vendas novamente.',
                  'shared_sync_store_sales_account_unverified')
        if remap_store_sources(scope, sources, candidate_ids, candidate_labels) == sources:
            continue
        # A tombstone for the remote alias stays intact; only the active local
        # target receives data, under the peer and source-connection proof.
        if local_id not in by_id or local_id in removed:
            _fail('A loja vinculada foi removida. Receba Lojas e integrações novamente.')
        if (remote_id.startswith('legacy:') or not record.get('remote_connection_hash')
                or not isinstance(source_connections, dict)
                or source_connections.get(remote_id) != record['remote_connection_hash']):
            _fail('A conexão de origem não corresponde à conferida. Atualize as duas máquinas e envie e receba Lojas e integrações junto com estes dados novamente.',
                  'shared_sync_store_binding_stale')
        row = by_id[local_id]
        if _connection_hash(row) != record.get('connection_hash'):
            evidence = _verify_accounts([row], [{**row, 'store_id': remote_id}], [(remote_id, local_id)])
            actual = evidence['local'].get(local_id)
            expected = {key: tuple(value) for key, value in record['accounts'].items()}
            if actual != expected:
                _fail('A conta da loja vinculada mudou. Revise o vínculo antes de receber novamente.')
        if not remote_id.startswith('legacy:'):
            ids[remote_id] = local_id
        remote_name, local_name = record['remote_name'], row['nome']
        if remote_name in labels and labels[remote_name] != local_name:
            _fail('Os nomes das lojas vinculadas são ambíguos.')
        labels[remote_name] = local_name
    return remap_store_sources(scope, sources, ids, labels)
