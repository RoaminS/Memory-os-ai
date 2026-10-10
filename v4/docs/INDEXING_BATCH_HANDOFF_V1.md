# Livraison Codex → Claude, 2026-10-10

Base demandée : `07795f28ba6ecbde4f4cfbd21e023d017bdefa55`.
Commit code gelé : `ef30ef35d291c69e57256ccedcc1137adeba4bff`.
Bundle : `indexing-batch-v1.bundle`, deux commits (code, protocoles/livraison).
Ni publication, ni push, ni bascule, ni inférence neuronale exécutée par Codex.
Les modifications de configuration client présentes dans l'arbre ne font pas
partie du bundle.

## Preuves locales

```bash
python3 -m pytest -q v4/tests --ignore=v4/tests/test_server_http_e2e.py
python3 -m ruff check --config pyproject.toml v4/src \
  v4/bench/e2e/indexing_campaign.py v4/tests/test_fact_batch_encoding.py \
  v4/tests/test_indexing_campaign.py v4/tests/test_evidence_bootstrap.py
bash -n scripts/run_indexing_batch.sh
```

Résultats : **988 passed, 2 skipped**, trois warnings de dépendances, 32,46 s ;
Ruff ciblant tout le moteur et les fichiers Python modifiés : vert ; syntaxe
Bash et `git diff --check` : verts. Pas d'affirmation de lint global des anciens
scripts exploratoires/tests hors périmètre.

Tests ajoutés : appels encodeur par vrais lots bornés ; parité scalaire/lots des
vecteurs, métadonnées et rangs avec encodeur hash ; doublons et barrières
identité/remplacement ; injection à chaque frontière d'écriture SQLite du nouveau
chemin ; conservation du reçu acquitté antérieur, aucune réussite partielle ;
refus des différences de vecteurs/reçus/rangs et des gardes ressources inconnues ;
smoke réel MCP stdio dans un magasin jetable avec encodeur hash uniquement.
Le fixture du refus préflight S a été replacé sous `.local-qualification` pour
tester son refus bootstrap, sans modifier le garde de production.

Huit tests de `test_server_http_e2e.py` restent à rejouer hors sandbox ; les deux
tests TCP de `test_ops.py` ignorés localement aussi. La suite entière suivante
les inclut. Aucun temps Granite n'a été mesuré : les objectifs ne sont pas acquis.

## Commandes exactes hors sandbox

Après import du bundle selon le mécanisme Git habituel et checkout de son tip :

```bash
cd /Users/romain/Documents/Codex/2026-10-09/v/work/implementation
git fetch ./indexing-batch-v1.bundle fix/v4-production-foundation
git merge --ff-only FETCH_HEAD
python3 -m pytest -q v4/tests
python3 -m ruff check --config pyproject.toml v4/src \
  v4/bench/e2e/indexing_campaign.py v4/tests/test_fact_batch_encoding.py \
  v4/tests/test_indexing_campaign.py v4/tests/test_evidence_bootstrap.py
bash scripts/run_indexing_batch.sh prepare \
  --source-commit ef30ef35d291c69e57256ccedcc1137adeba4bff \
  --plan-dir .local-qualification/indexing-batch-v1
bash scripts/run_indexing_batch.sh run \
  --plan-dir .local-qualification/indexing-batch-v1
```

Le script utilise par défaut le Python de `~/Memory-os-ai/.venv`, **en lecture
seule**, avec imports exclusivement depuis les archives du clone. Pas de nouveau
modèle/téléchargement. Deux jobs lourds au maximum, séquentiels, trois conversations
par bras, nice 15. Arrêt si swap>8,5 Go, disque<5 Go, RSS>4 Gio ou mesure inconnue.
Budget global des workers≤3 h plus préflight/exports. La sélection n'est pas
fondée sur les temps déjà vus. Ne pas lancer simultanément une autre campagne.

Lire `report.json`, les exports et journaux par bras, `resource-samples.json`,
`plan.json`/`receipt.json`. P50/P95 sur trois conversations sont des statistiques
d'échantillon, pas un IC ou un p95 de population. Les requêtes diagnostiques sont
fixes et sans gold ; aucun rappel/MRR BEAM n'est recalculé.

Si la suite, la parité ou une garde échoue : conserver les preuves, ne pas
relancer/modifier le plan automatiquement, ne pas activer. Un résultat rapide
ne permet pas d'ignorer une différence de classement.

## Relire / décisions ultérieures

- `INDEXING_BATCH_V1.md` : préenregistrement et limites JSONL/SQLite.
- `E2E_HELDOUT_DESIGN_V1.md` : indépendance à auditer avant téléchargement ;
  comparaison à trois bras proposée≤$220, ou pilote Codex à deux bras avec juge
  API≤$15 et quota ChatGPT non garanti. Aucun budget autorisé par ce lot.
- `TEMPORAL_HELDOUT_DESIGN_V1.md` : extracteur question-aveugle et plan factoriel,
  budget indicatif≤$40, acquisition/annotation en supplément.
- `BEAM_RESULT_V2.md` : aucun gain significatif de rappel, aucune revendication
  de leaderboard ; conserver C4 par défaut autorisé sans promotion de la récence.

Romain donnera des GO distincts pour acquisition, dépenses/inférence E2E et
temporelle, campagne à 20 conversations et activation/publication d'une version.
