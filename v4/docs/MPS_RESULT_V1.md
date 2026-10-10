# MPS BEAM — verdict et décision proposée, 2026-10-10

## Verdict officiel : équivalence non démontrée

Claude a terminé `mps-beam-reuse-v1` en 661 s, avec 400 recherches,
355 questions avec preuve et 20 conversations. Source harness `dce97ba`,
HEAD documentaire `83612c8`. Suite Claude : **1025 passed**, TCP inclus.
Aucun appel LLM. Le rapport original et le préenregistrement restent intacts.

| Métrique | MPS | CPU historique | Δ MPS−CPU, IC bilatéral 97,5 % |
|---|---:|---:|---:|
| Recall@10 | 0,462524 | 0,461905 | +0,000619 [−0,008313 ; +0,013538] |
| MRR | 0,444930 | 0,444273 | +0,000656 [−0,000315 ; +0,001917] |
| Ingestion p50/p95/max | 29,08 / 43,15 / 43,69 s | temps non appariés | objectifs 300/450/600 s réussis |

`equivalent=false`, `criterion_met=false` : l'IC de rappel dépasse la frontière
supérieure +0,01. MRR satisfait sa marge, le gate conjoint échoue. Il s'agit
d'une absence de démonstration d'équivalence, pas d'une preuve de dégradation.

La moyenne observée ne baisse pas. Toutefois, une perte de rappel jusqu'à
0,8313 point reste compatible avec cet IC ; « aucune dégradation possible »
serait faux. Les bornes basses dépassent −1 point, mais **aucune qualification
de non-infériorité n'est déclarée a posteriori**. L'objectif primaire ne change
pas ; aucun rescoring, nouvel intervalle ou nouveau seuil sur ces données.

Le témoin est historique, ses versions logicielles non attestées ; BEAM100K
est déjà observé. Les contrôles source/modèle/configuration et le pont des
60 questions ont réussi, sans transformer ce lot en confirmation indépendante.
Les statistiques numériques concordent avec l'audit précédent : cosinus
minimal CPU/MPS ≈0,999688. La distinction CPU bfloat16 / MPS float32 demeure.
Ce test ne mesure pas l'exactitude des réponses ou un rang de leaderboard.

## Preuves reproductibles, sans rescoring

L'attestation compacte est
[`mps_beam_reuse_v1_summary.json`](../bench/results/mps_beam_reuse_v1_summary.json).

- Plan SHA-256 : `7dfe1880418f19ae18211cd85cad27c072220da4f2f3fca0194275d1cbda8a68`.
- Rapport SHA-256 : `fa1b0b456cb4d310b35d2afe1a73e06c123c1df7bc00e613b3b8301e16d9800a`.
- Prédictions MPS SHA-256 : `99e3fa58a637c4651a293176421936998f5fb6e1b68e081208ee61d29ee1c92b`.

`scripts/verify_mps_reuse_result_v1.py` vérifie les cinq empreintes, le rapport,
la source et le reçu, puis exige explicitement le verdict original faux.
Le vérificateur n'importe aucun modèle, ne parse pas les prédictions pour
calculer des scores et ne réexécute pas le bootstrap. Ne pas utiliser l'ancien
`compare` pour modifier le verdict ; les nouveaux changements de source
refuseraient de toute façon son attestation de harness.

## Recommandation à Romain : CPU défaut, MPS expérimental explicite

Je recommande de **conserver CPU comme choix de production**, et de proposer
MPS/float32 en option expérimentale pour les utilisateurs qui acceptent cette
incertitude en échange du gain de débit. Le passage en production demeure
une décision de Romain. Aucun service ni magasin n'est basculé dans ce lot.

Attention factuelle : le code hérité avait `device=auto`, qui choisit MPS sur
un Mac compatible. La candidate de ce bundle fixe maintenant `Config.device`
à **`cpu`** ; les variables existantes restent explicites. La version 4.0.2
installée n'est pas modifiée. Un ancien `MEMORY_V4_DEVICE=auto` reste prioritaire
sur le nouveau défaut ; l'opérateur doit donc vérifier la configuration effective.
Le mode `auto` n'est pas le profil expérimental validé par ces mesures.

Profil proposé, configuration seulement — **ne pas appliquer au service sans GO** :

```bash
MEMORY_V4_DEVICE=mps
MEMORY_V4_EMBED_DTYPE=float32
MEMORY_V4_EMBED_MAX_SEQ_LENGTH=512
MEMORY_V4_EMBED_BATCH_SIZE=4
MEMORY_V4_BATCH_FACTS=0
```

C4, récence et supersession restent configurés séparément. L'essai de récupération
utilisait C4/scoped_literal/garde 3, récence désactivée, JSONL privé. Aucun résultat
n'est extrapolé au batch 32, à une autre longueur, au dtype natif GPU ou au reranker.
La candidate comprend le contrat dtype ; ne pas supposer que le paquet PyPI
4.0.2 déjà installé traite cette variable ajoutée ultérieurement.

Avant toute adoption : qualifier le paquet effectivement installé, puis tester
sur **copie hors ligne isolée dans le clone**, serveur productif inchangé. Un
changement device/dtype exige des embeddings compatibles : JSONL reconstruit
ses vecteurs selon le profil, SQLite sépare les empreintes. Ne jamais mélanger
vecteurs CPU et requêtes MPS. Une base historique sans profil ne fournit pas
de preuve de compatibilité. Conserver la copie CPU d'origine pour le retour
arrière ; les documents SQLite S4 restent un prérequis de bascule SQLite.

Retour proposé : `MEMORY_V4_DEVICE=cpu`, `MEMORY_V4_EMBED_DTYPE=native`, même
modèle/longueur/batch et magasin CPU conservé. Ne pas reconstruire la production
sur place. Le MPS explicite refuse l'indisponibilité du backend ; aucun fallback
silencieux. Pour les essais : nice15, concurrence1, OMP/BLAS1, swap≤8,5 Go,
disque≥5 Go, RSS≤4 Gio et driver MPS rapporté≤2 Gio, watermarks0,25/0,20.
Ces gardes sont appliquées par les campagnes ; les seuls réglages moteur ne
constituent pas un superviseur de ressources pour un service de production.

## Combien de nouveaux clusters ? Estimation de planification seulement

Aucun nouveau delta ni IC n'est calculé. À partir du seul IC publié, prendre
la distance la plus grande à l'estimation :

`h=max(0,000619−(−0,008313), 0,013538−0,000619)=0,012919`.

Approximation normale : `z(0,9875)=2,2414`, donc
`sigma_cluster≈h×sqrt(20)/2,2414=0,02578`. Cette conversion d'un bootstrap
asymétrique en variance est prudente mais approximative, pas une estimation
exacte de la variance des clusters. Si la distribution et les effectifs par
conversation restaient semblables :

| Objectif prospectif | Nombre approximatif de clusters |
|---|---:|
| Demi-largeur IC97,5 % ≤1 point | `ceil(20×(h/0,01)²)` = **34** |
| Non-infériorité unilatérale 97,5 %, effet vrai supposé 0, puissance80 % | **53** |
| Même test, puissance90 % | **70** |
| Cible recommandée avec marge pour hétérogénéité | **100** |

Formule puissance : `ceil(((z(0,975)+z(puissance))×sigma_cluster/0,01)²)`.
À 100 clusters, demi-largeur bilatérale attendue≈0,00578, **sans garantie**.
Les questions ne remplacent pas les clusters. Des clusters déséquilibrés,
des dépendances restantes ou un changement de corpus peuvent exiger davantage.
Une demi-largeur d'un point n'assure pas à elle seule la puissance d'une
non-infériorité ; les hypothèses d'effet vrai et de variance sont indispensables.

## Prochain test éventuel : design, pas lancement autorisé

Choix proposé : **MPS-HOLDOUT-100**, nouveau corpus de 100 conversations
indépendantes d'environ 100K tokens chacune, inspiré du cadre BEAM, construit
et annoté par un dépositaire indépendant. Ce corpus n'existe pas encore.
Seeds, identités et narratives nouveaux ; aucune extension ou recomposition
de BEAM100K/LME observés. Une construction nécessite devis et GO séparés,
sans appel de lecteur/juge dans ce lot. Ce sera un banc local, pas BEAM officiel.

Pourquoi ce choix : la [carte officielle BEAM](https://huggingface.co/datasets/Mohammadta/BEAM/blob/3205395e897e7318c7b094ef4e6047b9b82dbb03/README.md)
annonce seulement 35 exemples en 500K et 35 en 1M, avant audit de recoupement ;
un fichier différent n'assure pas des clusters indépendants. Le cadre de
[génération BEAM](https://github.com/mohammadtavakoli78/BEAM) permet de concevoir
des conversations et sondes nouvelles. Je ne recommande pas de compter
automatiquement les 70 exemples publics comme 70 sources indépendantes,
ni d'acheter un CPU complet sur un corpus trop petit pour la puissance visée.

Proposition à préenregistrer définitivement **avant construction/consultation
des sorties du moteur**, après GO sur le budget :

1. 100 clusters contributifs après audit des sources communes, utilisateurs,
   seeds, narratives et preuves, sans recoupement avec les corpus observés ;
   au moins dix sondes avec preuve par cluster. Le dépositaire verrouille ids,
   empreintes, règles d'exclusion et annotations de tours, avec audit humain.
   Des copies/extensions ou narratives communes sont regroupées ; <100 = STOP.
2. Même source du moteur et runtime contemporain dans les deux bras,
   Granite/snapshot/512/batch4/scalar/C4 figés. CPU natif contre MPS float32,
   stores isolés, capture question-blind, même packing et BM25 de contrôle.
   Alterner AB/BA par conversation selon une seed fixée avant résultats,
   équilibré 50/50 ; une seule opération active à la fois.
3. Hypothèse prospective : perte tolérée **<1 point**, une amélioration étant
   acceptable. Co-primaires Recall@10 et MRR ; bootstrap apparié par cluster,
   10 000 tirages, seed figée, **bornes basses unilatérales 97,5 % >−0,01 pour
   les deux métriques**. IC absent, contrôle divergent, erreur non résolue,
   cluster manquant ou garde dépassée = non qualifié. Ce choix prospectif
   n'annule jamais les deux échecs d'équivalence déjà publiés.
4. N=100 fixé, aucune inspection intermédiaire des deltas, arrêt pour ressource
   ou intégrité seulement ; aucune prolongation motivée par un résultat limite.
   Conserver les seuils performance300/450/600 s pour MPS et tous les échecs.
5. Coût local indicatif sur des charges comparables : CPU≈20,6 h et MPS≈55 min,
   extrapolations des seuls temps existants. Deux jobs séquentiels de 50 paires,
   plafond24 h/job, 48 h total, mêmes gardes machine. Les temps de construction
   et coûts d'annotation/génération restent à chiffrer avant GO. Aucun fallback
   API ou job supplémentaire automatique.

Le manifeste, l'adaptateur du nouveau corpus et le lanceur exécutable seront
qualifiés après disponibilité/audit des données ; il n'existe donc aucune
commande de campagne nouvelle à lancer aujourd'hui. Un succès soutiendrait
la non-infériorité de récupération sur ce protocole, pas l'équivalence exacte,
l'exactitude des réponses ou « top1 ». Réutiliser ensuite le banc réservé pour
une étude de réponses E2E exige son propre préenregistrement avant résultats.

## Commandes Claude après import, sans modèle ni bascule

```bash
cd /Users/romain/Documents/Codex/2026-10-09/v/work/implementation
python3 scripts/verify_mps_reuse_result_v1.py .local-qualification/mps-beam-reuse-v1
python3 -m pytest -q v4/tests
python3 -m ruff check --config pyproject.toml v4/src \
  scripts/verify_mps_reuse_result_v1.py \
  v4/tests/test_encoding_precision.py v4/tests/test_mps_result_attestation.py
```

Qualification Codex : **1020 passed, 2 skipped**, huit tests HTTP exclus,
3 warnings de dépendances, 35,60 s ; Ruff et vérificateur verts. Tests ajoutés :
CPU reste le défaut même si MPS est disponible, profil MPS explicite depuis
l'environnement, empreintes altérées refusées et échec d'équivalence conservé
même après régénération accidentelle d'un résumé de succès. Aucune inférence
neuronale, aucun téléchargement de dataset, aucun modèle/API appelé par Codex.
