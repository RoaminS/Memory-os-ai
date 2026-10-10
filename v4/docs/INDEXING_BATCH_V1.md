# Encodage de faits par lots — préenregistrement du 2026-10-10

GO Romain : code/tests locaux, mesures lourdes par Claude uniquement, deux jobs
maximum. Aucun nouveau score BEAM, aucun appel LLM ni changement de production.

## Correctif et limites

`add_facts` regroupe les faits sans identité/remplacement jusqu'à
`embed_batch_size` (4 dans cette mesure). La déduplication consulte les faits
persistés puis les candidats du lot dans leur ordre. Les doublons ne sont pas
encodés. Une identité ou `replaces` vide le lot avant validation et conserve le
chemin séquentiel de remplacement. Les ids dupliqués continuent de provoquer une
erreur ; SQLite annule la mutation entière avec ses vecteurs/reçus ; JSONL conserve
son contrat antérieur de réussite partielle possible en cas d'erreur.

Le décorateur transactionnel existant reste propriétaire du `BEGIN IMMEDIATE`
et du reçu final. Aucun commit intermédiaire n'est introduit. Les épisodes sont
déjà encodés par lots ; ils restent comptés. Le rollback technique est
`MEMORY_V4_EMBED_BATCH_SIZE=1`, au prix d'un encodage scalaire des faits ordinaires.
Aucune nouvelle version n'est publiée par ce lot.

## Plan gelé avant les mesures

Lanceur : `scripts/run_indexing_batch.sh`. `prepare` atteste puis archive les
sources du baseline `07795f28ba6ecbde4f4cfbd21e023d017bdefa55` et du commit candidat
explicite. Les deux workers importent exclusivement leur archive, avec le même
harness d'instrumentation. Toute modification d'archive, corpus, snapshot,
harness, plan ou runtime provoque un refus.

Sélection prospective : les trois plus petits hashes
`identity("indexing-batch-v1", conversation_source_id)` parmi les 20 conversations
BEAM 100K. Pas de sélection par difficulté ni par temps observé. Même ordre du
corpus dans les deux bras ; ordre des bras fixé avant puis après. Cette séquence
peut subir un effet thermique/cache : le gain ne sera pas généralisé au-delà de
cette machine/charge. Pas de répétition sélectionnée pour obtenir un succès.

Granite snapshot `835ad14087e140460703cf0fae09f97d469d65c2`, CPU float32, longueur
512, batch 4, threads OMP/BLAS=1, C4 scoped_literal/garde 3, récence OFF, JSONL
privé, modèle résident, magasin neuf par conversation. Nice≥15, concurrence=1,
swap≤8,5 Go, disque libre≥5 Go, RSS worker+serveur≤4 Gio. Le superviseur échantillonne
les ressources toutes les 0,5 s et termine seulement ses processus identifiés.
Budget 5 400 s/bras ; toute panne/budget/garde bloque le deuxième bras et laisse
`stopped.json`. `started.json` interdit un redémarrage automatique.

Temps distincts : démarrage du serveur/modèle ; ouverture/fermeture du magasin ;
ingestion mesurée depuis l'extraction question-aveugle jusqu'au retour durable de
la dernière capture MCP ; encodeur/appels/textes. L'export de preuve est hors
chronométrage. Cinq requêtes diagnostiques **fixes, sans questions BEAM ni gold**
vérifient l'ordre hybride hors temps d'ingestion ; aucun rappel/MRR n'est calculé.

## Gate, sans modification après observation

- Les trois paires terminent, chaque ingestion après est strictement plus rapide.
- Après : p50≤300 s, p95 nearest-rank≤450 s, max≤600 s. Sur n=3, p95=max ;
  ce gate est une faisabilité, pas l'estimation d'un p95 de population ou un IC.
- Textes, portée, temps métier, provenance/parents, ordre des ids et reçus sont
  exactement identiques après canonicalisation des seuls ids d'épisodes aléatoires,
  dates techniques de création et compteurs d'accès. Les dates métier ne sont
  jamais normalisées.
- Tous les vecteurs : `atol=2e-6`, `rtol=1e-5`, aucun NaN. Rangs hybrides exacts
  pour les cinq diagnostics, rangs denses exacts pour 32 vecteurs stockés utilisés
  comme sondes (sans nouvel encodage). Aucun gate assoupli en présence d'une égalité.
- Suite v4 entière verte hors sandbox. La parité de tous les classements possibles
  n'est pas démontrable par ces sondes : toute différence connue bloque l'activation.

Les critères ne sont pas encore mesurés. La qualification du coût sur les 20
conversations attendra un GO distinct ; ce lot ne l'exécute pas.

## Exécution Claude

Après import du bundle et positionnement sur son tip, copier le commit code exact
indiqué dans la livraison ; `prepare` ne charge aucun modèle :

```bash
cd /Users/romain/Documents/Codex/2026-10-09/v/work/implementation
python3 -m pytest -q v4/tests
python3 -m ruff check --config pyproject.toml v4/src \
  v4/bench/e2e/indexing_campaign.py v4/tests/test_fact_batch_encoding.py \
  v4/tests/test_indexing_campaign.py v4/tests/test_evidence_bootstrap.py
bash scripts/run_indexing_batch.sh prepare --source-commit ef30ef35d291c69e57256ccedcc1137adeba4bff \
  --plan-dir .local-qualification/indexing-batch-v1
bash scripts/run_indexing_batch.sh run \
  --plan-dir .local-qualification/indexing-batch-v1
```

Le dernier appel lance exactement deux jobs lourds séquentiels. Lire
`report.json`, les `rows.json`, `resource-samples.json`, archives et empreintes ;
ne pas effacer un échec, modifier le plan ou relancer. Le lecteur LLM et le
protocole temporel ci-dessous ne sont pas autorisés à être exécutés.
