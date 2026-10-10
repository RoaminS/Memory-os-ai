# MPS : diagnostic et extension BEAM préenregistrée — 2026-10-10

**Mise à jour après exécution : lot complet, performance réussie, équivalence
non démontrée ; voir [MPS_RESULT_V1.md](MPS_RESULT_V1.md).** Le protocole
préenregistré ci-dessous reste conservé, sans nouveau scoring ni changement de gate.

Statut : prêt pour Claude, **aucune exécution neuronale par Codex**. GO Romain
existant pour le chantier indexation ; un seul job lourd supplémentaire,
séquentiel. Aucun changement de production, téléchargement, appel API/LLM,
publication ou push. Le candidat MPS reste expérimental.

## Résultat précédent : échec conservé

`indexing-mps-v2-runtime-fix` est complet : MPS p50 38,94 s, p95/max 41,01 s,
accélération médiane appariée de 22,01 à 23,28 selon la conversation. Performance
réussie. Sur **52 questions avec preuve, trois conversations**, ΔRecall@10
−0,006593, IC97,5 % [−0,011111 ; 0] ; ΔMRR −0,000267,
IC97,5 % [−0,000868 ; 0]. **Équivalence échouée**, marge stricte ±0,01 inchangée.
Les IC sont à 97,5 %, conformément au protocole, pas à 95 %.

Claude a identifié trois questions différentes, toutes défavorables à MPS,
identiques entre AB et BA : `23600790`, `c7a94c78`, `be7b7ff1` (préfixes).
Cela prouve la répétabilité observée des scores, pas le déterminisme universel
de MPS. Trois clusters rendent l'IC discret et peu puissant ; cela ne permet
ni de déclarer l'équivalence, ni d'assouplir la marge après résultat.

## Diagnostic numérique, sans nouvelle inférence

Les démarrages attestent **CPU natif bfloat16**, **MPS float32 explicite**.
La mention ancienne `precision=float32` des snapshots concernait les tableaux
retournés, pas forcément le calcul du modèle. L'expérience change donc device
et précision ; elle n'isole pas les kernels MPS du changement de dtype.

Lecture des `.npy` sauvegardés, calcul des normes/cosinus en float64 :

| Conversation | Erreur maximale de norme CPU | MPS | Cosinus CPU/MPS minimal |
|---|---:|---:|---:|
| 2203b7a3 | 0,003901 | 1,06 × 10⁻⁷ | 0,999745 |
| 4ef06ff2 | 0,003841 | 1,18 × 10⁻⁷ | 0,999874 |
| fe61be4b | 0,003876 | 1,06 × 10⁻⁷ | 0,999688 |

Les statistiques sont identiques entre AB et BA. MPS est déjà quasiment unitaire.
Renormaliser après encodage en float64 change principalement le rayon et ne
reconstitue pas la direction CPU perdue à travers des calculs différents.
Une inversion de quasi-égalité reste possible ; **aucune preuve ne justifie
ce correctif comme remède aux trois pertes**. Ne pas ajuster le candidat sur
ces questions. Une attribution causale nécessiterait notamment un CPU float32
contemporain ; cette ablation n'est pas lancée dans ce lot.

`audit` reproduit ces calculs sur les tableaux enregistrés et publie leurs SHA-256.
Aucune sortie n'est passée à l'extracteur, à l'encodeur ou au moteur.

## Quel témoin CPU réutiliser ?

**LongMemEval-S est incompatible** : plan `lexical-c4-s-v3`, source
`df04fc6da31010c5839f577a02355cd464c6ebe4`, MiniLM-L6-v2,
snapshot `1110a243…`, longueur 256. BEAM utilise Granite-97m-multilingual-r2,
snapshot `835ad14087e140460703cf0fae09f97d469d65c2`, longueur 512.
Réutiliser ces prédictions comme contrôle Granite serait invalide ; l'estimation
500 × 40 s ne s'applique pas à cette comparaison.

Le témoin économique est **BEAM CPU complet déjà produit** :

| Preuve figée | Identifiant |
|---|---|
| Plan | `.local-qualification/beam-c4-retrieval-v2/plan.json` |
| Source CPU | `95276a8123838bfe9f7937f6325307d6be88ac39` |
| SHA-256 plan | `5878e5b54d71a6d6437a42f6e637e6a79552f7fda8d99828ace427dbbb5e604e` |
| SHA-256 prédictions | `f25fc2326e209a0f6c9635b3c3c48bd214b9bab4385acc9f80a519d5e07036b9` |
| Dénominateur | 400 recherches, 355 questions avec preuve, 20 conversations |

Le modèle et **tous les fichiers du snapshot** concordent exactement avec
l'essai MPS récent. Configuration CPU complète, packing, top-K et extraction
concordent après retrait des deux nouveaux champs aux valeurs historiques
`embed_dtype=native`, `batch_facts=false`. Les 60 questions des trois conversations
récentes reproduisent exactement tous les scores CPU et BM25 historiques,
dans les deux ordres. L'audit refuse toute discordance.

**La source actuelle b4be2be n'est pas identique à 95276a8.** Pour éliminer ce
confondant, le lanceur archive la source CPU historique entière, vérifie chaque
hash Python contre son plan, puis applique trois substitutions auditées :

1. `configuration.py` : device de l'encodeur CPU → MPS, reranker CPU conservé ;
2. `embed.py` : chargement du même modèle avec `torch_dtype=torch.float32` ;
3. `usage.py` : compteurs et attestation device/dtype/mémoire, sans changer les
   paramètres de la passe d'encodage.

Extraction, ingestion scalaire des faits, batch 4 des épisodes, moteur,
BM25, fusion C4, découpage et scorer restent ceux du CPU historique. Stores
JSONL vierges par conversation, modèle résident, sans cache de vecteurs partagé.
La source dérivée et toutes les preuves sont hashées dans le nouveau plan.

**Limite conservée : les versions des paquets du CPU historique n'ont pas été
enregistrées.** Les versions récentes et la machine sont attestées et exigées
à l'identique ; le pont des 60 questions apporte une vérification partielle.
Le résultat sera une équivalence aux sorties CPU historiques archivées,
pas une ablation contemporaine prouvant le seul effet du device. Les exports
complets des éléments CPU historiques ne sont pas disponibles non plus : la
source d'ingestion identique, les comptes sans perte et BM25 sont contrôlés,
mais aucune identité exhaustive des deux matérialisations n'est revendiquée.

## Nouveau protocole figé avant exécution

Code du lanceur : **`dce97ba5708e094918526ec4ddaf32cbdcefe700`**.
`prepare` fige les empreintes du modèle, corpus, sources historiques et dérivées,
harness, paramètres et neuf fichiers de référence. Un dossier neuf est obligatoire.

- **Un bras MPS seulement**, même candidat float32 ; aucune normalisation nouvelle.
  C4 `scoped_literal`, garde lexicale 3, longueur 512, épisodes batch 4, faits
  scalaires, récence OFF, extraction question-blind, namespaces et dates inchangés.
- Corpus BEAM100K entier, même ordre relatif, mais les trois conversations du
  pont sont traitées en premier. Leurs 60 scores MPS et BM25 doivent reproduire
  exactement le précédent MPS AB/BA, vérification après recherche/scoring.
  Une divergence bloque avant les 17 conversations supplémentaires.
- Toutes les 400 questions recherchées ; les 45 sans preuve restent journalisées
  et sont exclues uniquement du Recall/MRR apparié. Aucune annotation injectée
  dans l'ingestion. BM25 du même bras doit rester exactement égal au témoin.
- Pour les **355 questions**, deltas MPS−CPU ; bootstrap par conversation entière,
  10 000 tirages, seed 20261010, minimum **20 clusters contributifs**. Les deux
  IC bilatéraux **97,5 %**, Recall@10 et MRR, doivent être entièrement dans
  **]−0,01 ; +0,01[**. IC absent, frontière touchée, question manquante ou contrôle
  incohérent = non qualifié. Aucune nouvelle marge, variante ou sélection.
- Performance sur les 20 captures : p50 ≤300 s, p95 nearest-rank ≤450 s,
  max ≤600 s. Pas de ratio de vitesse causal apparié avec les temps historiques.
  Critère conjoint = intégrité, performance, équivalence et suite v4 verte.

Le bootstrap à 20 conversations est plus informatif que trois, mais reste
conditionnel à ce corpus observé. Les trois répétitions ne constituent pas
de nouveaux clusters. BEAM100K est in-sample ; les 17 nouvelles mesures MPS
ne rendent pas leurs questions indépendantes des analyses antérieures.
Aucune confirmation externe, exactitude de réponse ou place au leaderboard.

## Temps, mémoire, arrêts

20 × 38,94 s donne environ **13 minutes d'ingestion** par extrapolation des trois
conversations ; prévoir **15–40 minutes au total**, estimation non garantie.
Un modèle résident ≈388 Mo de poids float32 nominaux, plus activations,
tokenisation, vecteurs, runtime et caches MPS ; ce n'est pas une estimation RSS.
Garde RSS worker+serveur ≤4 Gio, driver MPS rapporté ≤2 Gio,
swap ≤8,5 Go décimaux, disque libre ≥5 Go, nice ≥15, OMP/BLAS=1, concurrence=1.
High/low-watermark MPS 0,25/0,20 et fallback CPU désactivé. Le compteur driver
mesure les fins d'encodage ; il ne couvre pas tous les pics transitoires.

Plafond **7 200 s au total**, **600 s par conversation**, un seul job lourd.
`started.json` interdit le redémarrage ; ressources inconnues, dépassement,
panne ou discordance produisent un échec conservé, sans autre traitement.
Pas d'autre campagne simultanée. Préflight sans modèle chargé/inférence.
En cas de pont incompatible, ne pas acheter silencieusement un nouveau CPU :
faire revoir l'écart, puis nouveau protocole/GO pour un contrôle contemporain
(ordre de grandeur du CPU complet historique : 4 h 07).

## Commandes Claude après import du bundle

```bash
cd /Users/romain/Documents/Codex/2026-10-09/v/work/implementation
python3 -m pytest -q v4/tests
python3 -m ruff check --config pyproject.toml v4/src \
  v4/bench/e2e/mps_reuse_campaign.py v4/tests/test_mps_reuse_campaign.py
bash scripts/run_mps_reuse.sh audit
bash scripts/run_mps_reuse.sh prepare \
  --source-commit dce97ba5708e094918526ec4ddaf32cbdcefe700 \
  --plan-dir .local-qualification/mps-beam-reuse-v1
bash scripts/run_mps_reuse.sh run \
  --plan-dir .local-qualification/mps-beam-reuse-v1
```

Le wrapper utilise le Python existant `~/Memory-os-ai/.venv/bin/python`, sans
modifier cet environnement. Les métadonnées Git sont lues depuis la racine
du clone, même lorsque le worker démarre dans son dossier non suivi. Seule
la dernière commande lance l'encodage MPS. Conserver plan/receipt, report,
prédictions, captures, usage, resource-samples, worker.log et stopped éventuel.
Le rapport porte les SHA-256 des prédictions nouvelles et historiques et de
`complete.json`. Aucun passage en production n'est autorisé par ce rapport.

## Preuves Codex

- Audit dans le Python réel : modèle/configuration compatibles, 60 scores CPU
  répétés dans AB/BA identiques au témoin, statistiques numériques reproduites.
- `python3 -m pytest -q v4/tests --ignore=v4/tests/test_server_http_e2e.py` :
  **1015 passed, 2 skipped**, 3 warnings de dépendances, 36,02 s.
- Onze régressions ajoutées : preuves historiques altérées, modèle différent,
  pont CPU divergent/MPS non répétable, trois fichiers seuls modifiés,
  worker réel stdio sur source archivée depuis cwd non suivi avec encodeur hash,
  jointure 400/355/20, contrôle BM25, doublons et dénominateurs incompatibles.
- Ruff moteur/fichiers ajoutés, Bash et diff check : verts.
- Claude doit rejouer les huit tests HTTP exclus et les deux tests TCP ignorés
  via la suite complète ci-dessus. Aucun modèle neuronal encodé par Codex.
