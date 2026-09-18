# Operational store reads

`integracoes.ler_lojas` and `buscar_loja_snapshot` are operational readers.
They intersect published opaque store identities with current canonical
credentials, without acquiring store, product, cost or photo writer locks.
ML and Bling connections are independently bound to public identity fingerprints;
fingerprints never contain access tokens, refresh tokens or secrets.

The display endpoint keeps its existing row shape. The disk projection uses
schema v2 and accepts v1 during background upgrade. A legacy Bling connection
without a connection epoch stays unavailable until explicit maintenance assigns
one and publishes the generation. Canonical corruption and missing snapshots
are temporary errors with Retry-After, never an empty successful store list.

`_unavailable_providers` is private reader metadata: a provider changed identity
or awaits publication. Consumers must report an incomplete/temporary result,
not a disconnected account, and must never switch to another store or raw file.
Central sessions are checked before attaching machine-local Turbo by exact ID.

## Remaining maintenance and mutation callers

- `integracoes.carregar_lojas` and `buscar_loja`: compatibility maintenance
  entrypoints; recovery, legacy migration, canonical RMW and explicit OAuth
  management retain these paths. `salvar_lojas` still owns atomic publication.
- `store_listing_service`: elected background initialization/recovery/upgrade.
- `shared_sync_bundle` and `shared_sync_apply_scope`: canonical capture and
  transactional apply/recovery respectively, under their existing locks.
- `cadastro_lojas_produtos`: only `somente_commit=True` uses the canonical
  loader to revalidate identity during a product commit.
- `cadastro_importacao`: preparation uses snapshot lookup; the cost-import
  commit retains canonical lookup under its transaction.
- `integracoes_api`: explicit OAuth/connection changes retain canonical exact
  identity checks. GET list/detail use snapshot readers.
- `store_oauth_refresh`: network happens outside writer locks; only the
  compare-and-set token commit rereads canonical rows under the writer lock.

Runtime exports named `carregar_lojas`/`buscar_loja` intentionally remain for
compatibility, but `backend_api`, sales and marketplace adapters bind them to
the operational readers. Question automation has its own compatibility adapter.

## OAuth

Cross-process gates are per physical tenant, opaque store ID and provider.
ML token publication compares account identity and credential version and writes
only refreshed token fields, preserving unrelated edits. ML external mutations
revalidate the prepared store/account immediately before the network request.
Bling retains its compare-and-set and receives the same provider-scoped gate.
Neither gate holds the general store mutex while waiting on the provider.
