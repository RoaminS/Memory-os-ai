# Amendement préenregistré — MPS/float32, 2026-10-10

Statut : prêt pour mesures Claude après import ; aucun modèle neuronal encodé
par Codex. GO Romain déjà donné pour l'indexation, au maximum deux jobs lourds.
Ce protocole remplace le prochain essai, jamais le gate échoué v1. Aucun
changement de production, publication, push ou appel LLM.

## Traitements figés

Même commit code `b4be2be915401142f0772dae0cdf20191f2c3531`, corpus BEAM100K et snapshot Granite déjà présents,
sources archivées. Trois conversations : **la même sélection hashée que v1**.
A = CPU/natif ; B = MPS/float32 explicite. Faits scalaires dans les deux bras,
batch encodeur 4 pour les épisodes ; longueur 512 ; C4 scoped_literal/garde 3,
fusion/poids/extraction inchangés, récence OFF, stockage JSONL privé. Un modèle
neural plus petit n'est pas ajouté. Le float32 est imposé à la construction et
vérifié ; MPS explicite, fallback CPU désactivé.

A utilise le chemin scalaire rétabli sur les mêmes sources que B ; il ne réutilise
pas les temps v1. B change device **et** précision, pas une ablation du GPU seul.
Deux serveurs MCP privés/modèles résidents au maximum ; une seule capture,
recherche ou inférence à la fois. Pas de partage de vecteurs entre A et B.

Job 1 : conversations dans l'ordre sélectionné, A puis B pour chacune.
Job 2 : ordre des conversations inversé, B puis A pour chacune. Chaque conversation
est donc exposée à **ABBA**, avec position dans le temps symétrique. Les deux jobs
chargent leurs modèles dans l'ordre du premier traitement. Une pause fixe de
60 secondes précède chaque capture (12 minutes au total), exclue du temps
d'ingestion. Chaque répétition ouvre un magasin neuf, avec modèle résident.

Même machine/Python/packages, nice≥15, OMP/BLAS=1, concurrence=1. Garde toutes les
0,5 s : swap≤8,5 Go, disque≥5 Go, RSS worker+deux serveurs≤4 Gio, mémoire driver MPS
rapportée≤2 Gio. High/low-watermark MPS=0,25/0,20, jamais fallback automatique.
Le compteur driver est relevé à la sortie de chaque encode ; il ne mesure pas
tous les pics transitoires. Le watermark et la garde globale complètent ce
contrôle. Ressource inconnue = refus. Aucune autre campagne simultanée.

Budget : 10 800 s/job, **6 h maximum** de workers, 12 ingestions et deux répétitions
par traitement/conversation. Estimation avant mesure : CPU seul≈82 minutes pour
ses six captures selon v1, plus B inconnu, démarrages, recherches et pauses ; ne
pas annoncer une durée B sans données. Un échec arrête avant le job suivant.
`started.json` interdit un redémarrage ; journaux et `stopped.json` conservés.

## Critères distincts et conjoints

1. Identité exacte des éléments, textes, portée, provenance, parents, dates
   métier, reçus et captures après canonicalisation des seuls UUID d'épisodes
   et dates/compteurs techniques, comme v1. Tous les vecteurs finis, forme correcte,
   norme à ±0,02. Aucune tolérance vecteur ni identité des rangs n'est revendiquée.
2. Débit : B p50≤300 s, p95 nearest-rank≤450 s, max≤600 s sur les **six captures** ;
   vitesse médiane appariée A/B≥1,5 pour **chaque** conversation. Pas de p95 de
   population ou IC de débit avec trois conversations ; ici p95=max sur six
   captures, donc le seuil 450 s est effectivement le plus strict.
3. Qualité de développement : toutes les questions des trois conversations,
   même Recall@10 de tours annotés et MRR, packing 12 000 octets, même top-K 10 et
   BM25 littéral du même bras. Annotations sans preuve exclues uniquement des
   deux métriques, questions quand même recherchées et journalisées ; BM25 doit
   rester identique. Aucune annotation ne participe à l'ingestion/extraction.
4. Équivalence **avant résultats** : moyenne des deux répétitions par question,
   puis bootstrap apparié par conversation entière, 10 000 tirages, seed 20261010,
   ≥3 clusters. IC bilatéraux **97,5 %** de B−A entièrement dans **]−0,01;+0,01[**
   pour rappel **et** MRR (marge absolue d'un point ; correction conservatrice
   des deux métriques). IC absent ou frontière touchée = non qualifié ; aucun
   assouplissement après score. Les répétitions ne deviennent pas six clusters.
5. Suite v4 complète verte hors sandbox, zéro appel LLM, aucun élément omis ni
   échec de justesse. Succès uniquement si les cinq contrôles passent.

**Limite majeure : trois clusters observés donnent un screening très peu
puissant**, conditionnel à ces conversations/distracteurs. Même un IC dans la
marge ne démontre pas l'équivalence générale. Un succès permet seulement de
proposer une qualification sur les 20 conversations, avec GO/préenregistrement
distincts, puis un test indépendant de réponses E2E. BEAM100K et LME-S restent
in-sample ; ce lot ne produit aucune confirmation ou revendication leaderboard.

La suppression du gate bit/rang identique est prospective et justifiée par
l'objectif qualité ; elle ne transforme pas le lot CPU précédent en succès.
`report.json` publie un échec de performance/équivalence ; en cas de panne,
il contient `complete=false`, la cause et un critère faux au lieu de disparaître.

## Contrat candidat et caches

`MEMORY_V4_EMBED_DTYPE=native|float32` ; défaut natif inchangé.
`MEMORY_V4_BATCH_FACTS=0|1` ; défaut 0, expérimentation CPU par lots retirée des
défauts. Le cache de modèles distingue la précision ; SQLite distingue l'empreinte
de vecteurs float32 ; JSONL reconstruit ses vecteurs si le profil device/précision/
longueur a changé. Un ancien cache sans profil est reconstruit pour un dtype
explicitement non natif. Une ancienne base native sans device attesté ne prouve
pas sa compatibilité avec un changement de backend : activation réelle seulement
après reconstruction contrôlée hors ligne sur copie, jamais en mélangeant les
anciens vecteurs avec de nouvelles requêtes. Aucun tel changement n'est fait ici.

Handshake MCP : `serverInfo.version=memory_os_v4.__version__` (4.0.2 dans cette
candidate), pas la version du SDK ; test réel stdio avec hash, partagé par HTTP.

## Commandes exactes Claude

```bash
cd /Users/romain/Documents/Codex/2026-10-09/v/work/implementation
git fetch ./indexing-mps-v2-runtime-fix.bundle fix/v4-production-foundation
git merge --ff-only FETCH_HEAD
python3 -m pytest -q v4/tests
python3 -m ruff check --config pyproject.toml v4/src \
  v4/bench/e2e/indexing_mps_campaign.py v4/bench/e2e/client.py \
  v4/bench/e2e/configuration.py v4/bench/e2e/resident.py v4/bench/e2e/usage.py \
  v4/tests/test_encoding_precision.py v4/tests/test_indexing_mps_campaign.py \
  v4/tests/test_fact_batch_encoding.py v4/tests/test_server_stdio_e2e.py
bash scripts/run_indexing_mps.sh prepare --source-commit b4be2be915401142f0772dae0cdf20191f2c3531 \
  --plan-dir .local-qualification/indexing-mps-v2-runtime-fix
bash scripts/run_indexing_mps.sh run --plan-dir .local-qualification/indexing-mps-v2-runtime-fix
```

Préflight sans inférence, sans téléchargement ; seule la dernière commande lance
les deux jobs. Lire plan/receipt/empreintes, report, rows, dtype réel, encoder_ms,
tokens utiles/paddés, startups et ressources. Si MPS refuse ModernBERT, arrêter
et transmettre l'erreur, sans essayer ONNX/autre précision dans ce plan.

## Qualification locale Codex

`python3 -m pytest -q v4/tests --ignore=v4/tests/test_server_http_e2e.py` :
**1002 passed, 2 skipped**, trois warnings de dépendances, 33,71 s. Les huit
tests HTTP exclus et les deux TCP ignorés sont inclus dans la commande Claude
ci-dessus. Ruff du moteur et de tous les fichiers Python modifiés, syntaxe Bash
et `git diff --check` : verts.

Tests sans modèle : dtype explicite transmis et cache séparé ; refus MPS indisponible ;
retour scalaire par défaut ; cache JSONL/SQLite reconstruit une fois après changement
de précision puis réutilisé ; équivalence/IC manquant/identité/temps ; lancement réel
du worker depuis son fichier et un cwd non suivi, deux serveurs stdio privés avec
encodeur hash seulement ; handshake MCP annoncé en 4.0.2. Aucune mesure MPS réelle.

## Correctif du préflight runtime — 2026-10-10

Claude a rejoué la candidate initiale `28ddf37` : **1012 passed**, TCP inclus.
Le préflight s'est ensuite arrêté sur la métadonnée `rank-bm25`, paquet absent et
inutilisé (BM25 interne), avant toute mesure. Aucun nouveau résultat observé.

Le commit code `b4be2be915401142f0772dae0cdf20191f2c3531` retire uniquement ce paquet
de l'attestation. Deux régressions vérifient son absence et le refus conservé
d'une dépendance réellement requise manquante. Suite ciblée : **9 passed** ; Ruff
des deux fichiers modifiés : vert. `runtime()` réussit également avec le Python
réel `~/Memory-os-ai/.venv/bin/python`, sans charger de modèle ni modifier `.venv`.
Aucun autre réglage, critère, garde, bras, snapshot ou budget ne change. Les
commandes ci-dessus utilisent un nouveau dossier ; les preuves du refus initial
restent intactes. Le plan et les empreintes sont régénérés avant toute mesure.
