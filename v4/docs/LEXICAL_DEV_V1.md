# BM25 : diagnostic causal et développement lexical v1 — 2026-10-09

Ce protocole est écrit **avant toute nouvelle campagne**. Aucun résultat de S,
aucune donnée nouvelle, aucun changement du BM25 de référence, du juge, de la
capture ou du contexte. La campagne précédente compte 103 questions dev, pas
100 : on conserve les 100 premières de son ordre gelé, indépendamment des
résultats, et publie les trois exclusions. Les résultats restent exploratoires.

## Hypothèses vérifiables

E3 n'est PAS BM25 seul : `reranker=ettin-17m`, `rerank_weight=1`, 30 candidats.
Les indices réels comptent un épisode par message brut **et** un fait par span
client (1024 caractères + wrapper observation). L'index lexical porte sur
header contextuel + texte, avec tokenisation différente de `\w+` Unicode
casefold (stopwords, caractères seuls, accents, dates; stemming selon bras).
Le filtre kind/projet est appliqué après top-N dans l'ancien chemin. Ceci peut
modifier N, df, avgdl et évincer un fait avant son filtrage. Il faut mesurer
chaque effet; l'échec d'E3 ne prouve pas à lui seul lequel domine.

Diagnostic par question : unités et empreintes des textes/tokens, nombres
capture/quotes/épisodes/index/faits, déduplication et provenance, listes/rangs
BM25 avant/après filtre, preuves perdues/gagnées, regroupement quote→tour,
nombre de tours distincts effectif et coupure du budget UTF-8. Comparaisons
appariées, une intervention à la fois : référence → header seul → tokenizer
hérité → stemming → corpus mixte → top-N avant filtre → reranking E3 observé.
L'importance/recency sont constantes dans cette campagne; aucun MMR dans
`search` simple. La diversification n'est activée que par le packing E5.

Lecture des **traces déjà produites**, aucune nouvelle exécution neuronale :
sur les 100 IDs retenus, E3 contient 13,06 faits visibles en moyenne sur son
pool global plafonné à 30; le premier rang global n'est pas visible parmi les
faits retournés pour 75 questions.
99/100 traces primaires ont un signal de reranker. Les contextes E3 contiennent
7,69 tours distincts en moyenne contre 8,80 pour la référence (77 contextes
E3 avec moins de 10 tours). E1 : 23,21 faits visibles parmi son pool lexical
global plafonné à 100, 79 premiers rangs non visibles, 8,07 tours contre 8,80. Ces traces
sont bornées : un fait absent ne prouve pas sa suppression à l'ingestion.
Preuve locale : `.local-qualification/lexical-dev-v1-evidence/historical-audits.json`,
empreintes des artefacts précédents contrôlées. Attribution nette header /
tokenizer / corpus / CE : à mesurer par les interventions, pas à inventer.

## Correctif moteur, activation explicite

`MEMORY_V4_BM25_PROFILE=scoped_literal` : tokens Unicode littéraux sur le
**texte stocké seul**; corpus, IDF et longueurs restreints par les mêmes
filtres kind/projet/at_time avant top-N. Cache dérivé invalidé par génération
d'index et ensemble des IDs éligibles. top-N couvre au moins top_k. Les
changements de profil reconstruisent BM25 au redémarrage sans ré-encodage.
Dense/entities gardent leurs représentations actuelles. Le profil par défaut
reste legacy; aucune activation de production dans ce lot.
En fusion, les candidats hors scope sont écartés **avant normalisation** pour
ne pas fixer les extrema/les rangs du scoring des faits autorisés. Le budget
de collecte dense/entity reste global top-N; cette limite demeure mesurée
par la campagne, aucun gain de fusion n'est présumé.
Le filtrage temporel reste celui du moteur; la pénalité actuelle des faits
invalidés est conservée. La parité exacte suppose des observations valides,
de même importance; la capture de ce protocole satisfait ces conditions.

Sans dense/entities/recency/expansion/reranker, l'ordre lexical est le score
Okapi brut puis ID, exactement celui de la référence sur le même corpus.
`lexical_first=true` conserve ce préfixe positif, puis utilise la fusion pour
compléter les places restantes : scores de sortie 1/rang, signaux originaux
conservés. Garantie pour `search` simple, pas pour le RRF multi-query.

## Bras figés et décision

| Bras | Paramètres, identiques hors différences déclarées |
|---|---|
| C1 | scoped_literal; dense/entities OFF; aucun reranker; recency OFF |
| C2 | scoped_literal; fusion CC; MiniLM; w_bm25=1.5, w_dense=1, w_entity=.5 |
| C3 | C2 + lexical_first=true |

Chaque bras refait son BM25 de référence sur les mêmes quotes et budget.
top_k=10, contexte=12000 octets, chunks=1024, trace=100, warm_searches=3,
CPU1/batch4/max_seq256, résident par bras avec magasin neuf par conversation.
C1 doit avoir **100/100 listes de quotes sélectionnées identiques** à BM25 :
un écart bloque C2/C3. C2/C3 : delta recall apparié IC95 bas >0 et delta MRR
IC95 bas >=0 pour proposer un candidat; bootstrap cluster 2000, seed20261009.
Sélection parmi bras admissibles : delta recall, delta MRR, p50 recherche
chaude, puis ID. Aucun réglage ultérieur sur ces résultats. Une égalité de C3
ne constitue pas une victoire. Pas de confirmation S automatique ni de claim
leaderboard; réexamen de l'indépendance des questions nécessaire avant S.

Budget : 3600s/bras, 300s/question, 3h+15min/campagne; MiniLM seulement déjà
en cache, zéro téléchargement, réseau IP interdit. Diagnostic lexical ≤900s.
Séquentiel, nice15, arrêt swap>8.5Go ou disque<5Go; garde impossible à mesurer
=> arrêt. Timeout => bras infaisable, poursuivre; justesse/intégrité => arrêt.
Reprise uniquement des sorties complètes dont toutes les empreintes concordent.

## Commandes hors sandbox, après import du bundle

Depuis le clone contenant les sources livrées et les artefacts resident-dev-v2 :

```sh
REV=<tip-complet-du-bundle>
bash scripts/run_lexical_dev.sh prepare "$REV"
bash scripts/run_lexical_dev.sh diagnose "$REV"
bash scripts/run_lexical_dev.sh run "$REV"
```

Le troisième argument facultatif est le nouveau dossier de plan, le quatrième
(prepare seulement) le dossier resident-dev-v2 existant. La préparation
contrôle la chaîne de preuves et gèle plan/receipt avant le diagnostic.
Le lancement de C1 exige d'abord un diagnostic complet 100/100 lié au plan
et au commit par empreintes; une interruption ou erreur du diagnostic bloque
les campagnes, même si l'opérateur saisit ensuite la commande `run`.
Résultats : `diagnosis/questions.jsonl`, `diagnosis/report.json`, C1/C2/C3
avec prédictions, durées démarrage/ingestion/recherche chaude séparées,
empreintes, puis `development-verdict.json`. Un arrêt de diagnostic conserve
les fichiers incomplets et exige un nouveau dossier, jamais un écrasement.
Le sandbox refuse setpriority et sysctl swap : aucune garde désactivée pour
produire des chiffres nouveaux ici; Claude exécute les campagnes hors sandbox.

## Qualification du lot, distincte des résultats de campagne

S3 intégré depuis `98000fa` (hérite de S2 `61eda7b`). Le runner Store crée son
dossier de basetemp et fixe MEMORY_WORKSPACE au clone sauf valeur explicite.
`bash scripts/run_store_checks.sh` : **329 passed**, 32,73s; deux commandes lint
vertes. Mesures lifecycle S3 rapportées par Claude dans
`/Users/romain/memory-os-local/reviews/claude_results_dev_v2_and_s3.md` :
1000 froid/chaud/rollback 0,32/0,25/0,22s; 10000 3,6/2,9/2,5s;
0 texte existant ré-encodé, trois répétitions. Pas de mesure granite nouvelle.

Suite ciblée suivante : **205 passed**, assertions de dérive du
protocole incluses.

```sh
PYTHONPATH=src:v4/src:v4 /Users/romain/Memory-os-ai/.venv/bin/python -m pytest \
  -q -p no:cacheprovider --basetemp=.local-qualification/lexical-final-tests \
  tests/test_e2e_eval.py tests/test_eval_experiments.py tests/test_eval_resident.py \
  tests/test_evaluation_manifest.py v4/tests/test_lexical_dev.py \
  v4/tests/test_lexical_parity.py v4/tests/test_core_bm25.py \
  v4/tests/test_core_retrieval.py v4/tests/test_sqlite_cache.py
ruff check --no-cache --config pyproject.toml src tests v4/src v4/tests v4/bench scripts --select E9,F
ruff check --no-cache --config pyproject.toml src tests scripts
```

Éléments de preuve : `.local-qualification/lexical-dev-v1-evidence/` (logs,
historical-audits.json, manifest.json). Tests hash/faux modèles uniquement,
séquentiels. Baseline, capture et métriques inchangés. La parité complète
100/100 et une victoire de fusion **ne sont pas encore mesurées** : les tests
prouvent les invariants et régressions synthétiques; la campagne gardée reste
à Claude. Relecture prioritaire : lexical_index_tokens, cache scoping,
invalidation/temporel, formule BM25, gate C1 et distinction CE/retrieval.
