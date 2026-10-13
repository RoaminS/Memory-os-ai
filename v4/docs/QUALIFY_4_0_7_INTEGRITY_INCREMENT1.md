# Qualification 4.0.7 — intégrité incrément 1 (B3), 13 octobre 2026

## Périmètre exact

P-A v3.4b validé pour les 40 cas scellés ; détail et limites dans
P_A_VERDICT_V5_VAL2_1.md. B3 implémenté, B2 contrat proposé non implémenté ;
B4/B5/B9 attendent les étapes suivantes. Version 4.0.6.dev0 inchangée.
Aucun profil neural, cache, schéma SQLite ou fichier de brief modifié.
Production reste 4.0.5, aucune publication/bascule.

Commit de protocole **508b322** précède code B3 **7f85e04**. Base d003e54.
Contrôle B3 partagé par modèle MCP, normalisation Python et frontière prospective
validate_replacements : single other/unknown/default refusés après NFKC/casefold/
strip, jamais correction automatique de la valeur persistée. L'historique n'est
pas soumis à la validation des nouvelles entrées. Cardinalité multi et prédicats
précis hors vocabulaire de facettes sont conservés.

## Preuves locales

- 42 nouvelles régressions B3 et suites extraction/scope/incident/transactions/
  batch : **111 passed**, 27,46 s ;
- handlers MCP, core memory et B7 : **108 passed**, 0,92 s ;
- total **219 tests verts**, uniquement stores synthétiques et encodeur hash ;
  aucune inférence neuronale/TCP/API/téléchargement ;
- Ruff src/tests/scripts et E9,F src/tests/v4/scripts, diff --check : verts ;
- catalogue MCP sérialisé, cl100k exact : **23 outils inchangés**, baseline
  d003e54 = 10010 tokens, candidat = 10036, **+0,260 %** (seuil +10 %).
  Mesure du catalogue seulement, pas coût du prompt d'un client ou mesure TCP.

Historique de développement conservé : premier test legacy SQLite attendait
à tort zéro reçu interne après initialisation/close ; 41/42 passaient. La fixture
compare désormais items + item_versions, puisqu'un reçu de flush interne est
légitime ; les tests de REJET conservent leur comparaison stricte des reçus.
Aucun changement produit pour faire passer cette assertion. Les logs/JUnit et
le premier échec sont conservés dans le worktree, .local-qualification/.

## Claude — reprise et qualification séquentielle

Importer le bundle release/4.0.7 au-dessus de d003e54, arbre sans modifications
suivies et sans édition concurrente. Ne pas écraser les preuves antérieures.

```sh
PYTHON="$HOME/memory-os-local/qual-envs/release406-installed-2zfywl93/wheel/bin/python"
export PYTHONDONTWRITEBYTECODE=1 MEMORY_V4_MIN_FREE_BYTES=3000000000
export MEMORY_V4_CL100K_PATH="$HOME/.config/memory-os/cl100k_base.tiktoken"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 MEMORY_V4_EMBED_MODEL=hash

PYTHONPATH=src:v4/src:v4 "$PYTHON" -B -m pytest -q -p no:cacheprovider \
  v4/tests/test_integrity_v5_b3.py \
  v4/tests/test_core_extract.py v4/tests/test_scope_and_fact_updates.py \
  v4/tests/test_incident_002_dedup.py v4/tests/test_sqlite_engine.py \
  v4/tests/test_fact_batch_encoding.py v4/tests/test_server_handlers.py \
  v4/tests/test_core_memory.py v4/tests/test_inert_cos_b7.py \
  --basetemp=.local-qualification/407-integrity-b3-targeted-claude-v1 \
  --junitxml=.local-qualification/407-integrity-b3-targeted-claude-v1.xml

"$PYTHON" -B scripts/qualify_405_test_isolation.py --suite full \
  --output .local-qualification/407-integrity-b3-full-claude-v1
```

Le full rejoue v3/v4 et le manifeste existant de **28 TCP**, inchangé : pas de
nouveau test TCP dans cet incrément. Les deux nouveaux cas handler vérifient
memory_add_facts et memory_chat_save sans création d'engine/reçu en cas de refus.
Le catalogue peut être mesuré séparément dans le même environnement qualifié :

```sh
PYTHONPATH=v4/src "$PYTHON" -B - <<'PY'
import anyio, json
from memory_os_v4 import server, brief_tokens
catalog = [t.model_dump(mode='json', by_alias=True, exclude_none=True)
           for t in anyio.run(server.list_tools)]
raw = json.dumps(catalog, ensure_ascii=False, separators=(',', ':'))
print({'tools': len(catalog), 'schema_tokens': brief_tokens.counter()(raw),
       'bytes': len(raw.encode('utf-8'))})
PY
```

## Travail parallèle attendu de Claude

1. Relire INTEGRITY_V5_B2_CONTRACT_v1.md : mode preview/apply, reason,
   expected_revisions, exact confirmed_impacts au-delà de trois invalidations,
   request_id ; aligner les clients sur les messages sans activer de correction.
2. Trancher le contrat JSONL réduit/refus de lot invalidant, sans promettre une
   atomicité absente ; décision de conception à consigner avant code B2.
3. Préparer puis sceller les 40 validations P-B indépendantes ; conserver le
   gold privé et transmettre seulement format/manifeste/inputs après audit.
4. B4/B5 feront l'objet de contrats dédiés contresignés avant code, dans l'ordre
   du plan. Aucun nouveau GO de Romain requis pour préparer ces lots déjà
   autorisés ; activation, release et actions production restent séparées.
