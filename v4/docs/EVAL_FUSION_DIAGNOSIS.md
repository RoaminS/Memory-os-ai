# Diagnostic de la campagne oracle et expériences de développement

Date : 2026-10-09. Résultats historiques fournis par Claude ; nouveaux traitements
implémentés, **non exécutés dans le sandbox**. Aucun changement du moteur de
production, aucun push GitHub, aucune soumission de leaderboard.

## Ce que les prédictions établissent

Source : `.local-qualification/eval-oracle-500-v1/` ; 500 questions, 479 avec
preuve de récupération annotée, 474 groupes de sources pour cette métrique.
LongMemEval **oracle**, pas LongMemEval-S. MiniLM, pas Granite de production.
Intervalles historiques : bootstrap apparié par groupes de sessions observables,
2 000 réplications, graine 20261009. Les questions sans preuve annotée sont NA
pour la récupération, pas retirées de l'évaluation des réponses.

| Mesure | Mémoire | BM25 indépendant |
|---|---:|---:|
| Rappel | 0,8023 [0,7713 ; 0,8316] | 0,8121 [0,7809 ; 0,8410] |
| MRR | 0,6414 | 0,6851 |
| Delta rappel apparié | −0,00985 [−0,03006 ; +0,01103] | référence |

Aucune supériorité à BM25 établie. Sur les 479 questions comparables, la mémoire
perd en MRR sur **135**, gagne sur **78**, égalise sur **266**. Le rapport ancien
ne donne pas d'IC apparié du MRR ; le nouveau diagnostic le calculera sans
réexécuter l'ingestion ni les modèles.

| Catégorie | n | Delta rappel | Delta MRR |
|---|---:|---:|---:|
| knowledge-update | 72 | −0,03704 | −0,03803 |
| multi-session | 125 | −0,02040 | −0,03106 |
| single-session-assistant | 56 | +0,01786 | +0,02941 |
| single-session-preference | 30 | −0,08333 | −0,14056 |
| single-session-user | 64 | 0 | −0,06743 |
| temporal-reasoning | 132 | +0,01515 | −0,05652 |

Ces écarts par catégorie sont descriptifs, sans test de significativité multiple.
Exemples reproductibles, préfixes d'identifiants opaques dans les prédictions :
`60472f9c` (nombre de projets hors thèse), `09d032c9` (préférence batterie),
`95228167` (magasin de guitare), `92a0aa75` (durée d'emploi). BM25 place une
preuve annotée en premier ; la mémoire manque les preuves dans le contexte
retourné et sélectionne des observations moins pertinentes. Pour les questions
temporelles, meilleur rappel moyen mais moins bon MRR indique notamment un
problème de placement de la première preuve, pas uniquement de couverture.

## Mécanismes à vérifier, sans attribution causale inventée

La configuration figée est CC, normalisation min–max par requête, poids
BM25=1,5 / dense=1 / entités=0,5, candidats=100, stemming activé,
`full_candidate_scoring=false`, recency désactivée, sans reranker.

1. **Poids et normalisation** : le min–max donne une grande amplitude à une
   distribution dense même peu discriminante. Le poids lexical 1,5 ne garantit
   pas la priorité de BM25 ; dense et entités peuvent ensemble déplacer une
   preuve lexicalement précise. Un canal absent d'un top-N reçoit moins de
   contribution qu'un candidat présent ; rescoring complet est une ablation.
2. **Candidats et corpus** : les épisodes bruts et les faits cités sont indexés
   ensemble avant le filtre `kinds=['fact']`. Les épisodes peuvent occuper des
   places dans les top-N et influencent les normalisations et les statistiques
   lexicales. Le BM25 indépendant indexe uniquement les mêmes citations avec
   leur enveloppe d'observation ; le moteur ajoute aussi son contextual header
   et applique le stemming. Ce n'est donc pas une comparaison ne variant que
   les poids de fusion.
3. **Découpage** : plusieurs morceaux d'un long tour peuvent consommer les dix
   places du contexte. Les anciennes sorties dédupliquent les ids de tours et
   ne conservent ni ids de morceaux ni scores de signaux : impossible de
   quantifier rétrospectivement ce gaspillage ou le bruit dense par question.

Les nouvelles sorties conservent ids de morceaux sélectionnés, signaux/rangs
primaires et trace diagnostique d'un pool de 100. Celle-ci est une **requête
séparée après la première recherche**, avec durée et coût comptés : demander
100 au lieu de 10 change l'oversampling du multi-query. Elle ne remplace jamais
le classement primaire. E5 élargit explicitement le pool primaire à 100, pour
les deux systèmes, afin de sélectionner dix tours distincts. Les traitements
composés E2/E3/E5 pourront montrer un gain de configuration, sans isoler chacun
de leurs ingrédients.

## Où passent les 4,7 secondes

Chaque question ouvre un **nouveau magasin et serveur stdio**, puis ingère
l'historique, recherche et ferme le serveur. Ce n'est pas la latence d'une
recherche sur un serveur résident.

| Phase mémoire | Médiane (ms, rang empirique) | Part du temps cumulé |
|---|---:|---:|
| initialisation | environ 2 879 | 60,8 % |
| ingestion | environ 1 208 | 27,8 % |
| première recherche | environ 14,9 | 0,35 % |
| réponse extractive | environ 0,34 | 0,007 % |
| temps restant | non attribué | environ 11,1 % |

La médiane globale du rapport est 4 689 ms contre 2,93 ms pour BM25. Les
médianes des phases ne s'additionnent pas. Le reste inclut fermeture, extraction
et surcoûts non instrumentés ; impossible de le qualifier intégralement de
surcoût MCP. Le démarrage inclut chargement/initialisation du modèle et handshake
MCP : pas de séparation fine dans les anciennes mesures.

Les anciennes sorties comptent 20 297 appels d'encodeur, 29 612 textes et
5 842 150 jetons pour la mémoire, contre 969 / 18 899 / 3 356 904 pour dense seul.
La mémoire ingère les épisodes et faits ; les encodages par ajout, warmup et
écritures du magasin contribuent au coût. Le nouveau worker conserve les
compteurs après initialisation, ingestion, première recherche et répétitions,
mesure la fermeture et trois recherches à chaud. Coûts de trace et répétitions
restent facturés dans l'end-to-end ; `retrieval_ms` et `warm_search_ms` sont les
mesures de recherche à examiner séparément. Aucun SLO de production déduit de
ce protocole synthétique.

## Correction du juge — exclusivement des proxys non officiels

`answer-proxies-v2` : Unicode NFKC, casefold, ponctuation séparée, espaces
normalisés, articles anglais a/an/the supprimés.

- **EM** : égalité de séquences de jetons normalisés avec une référence.
- **F1** : intersection de multisets de jetons, maximum sur les références
  explicitement fournies ; répétitions comptées. Une phrase contenant Paris
  peut obtenir un F1 positif sans égalité exacte à « Paris ».
- **Contient** : une référence complète figure comme séquence contiguë de
  jetons, pas comme sous-chaîne arbitraire (« 12 » ne correspond pas à « 312 »).

Aucune réponse/alias inventé à partir d'un barème en prose. Référence vide après
normalisation : NA ; abstention/échec avec référence : zéro ; full-context hors
budget : NA. Ces proxys ignorent sémantique, négation, raisonnement arithmétique,
alias implicites et qualité d'une préférence : « not Paris » peut satisfaire
contient. L'EM peut rester nul si le générateur copie une phrase au lieu de
répondre. Ni juge officiel LongMemEval, ni preuve de qualité agentique. Les
fichiers historiques restent inchangés ; re-score dans un nouveau dossier avec
reçus SHA-256, résultats par question et IC appariés MRR/F1/contient.

## Préenregistrement et critères de décision

Les **500 questions ont déjà été observées**. Répartition déterministe à partir
des sources observables : groupes connectés de sessions, stratification par
catégorie, 20 % des groupes de chaque strate arrondis au supérieur pour le
développement. Les autres questions sont en `quarantine_observed_ids`, jamais
qualifiées de test vierge ; `test_ids=[]`. Le lanceur vérifie intégralement cette
répartition et refuse une sélection arbitraire ou de quarantaine. Les catégories
ne sont utilisées que pour répartir/scorer, jamais par extraction ou requêtes.

| Bras | Traitement fixé avant ses scores |
|---|---|
| C0 | MiniLM et fusion actuelle, contrôle tracé |
| E1 | Granite 97m local, autres paramètres inchangés |
| E2 | BM25=3, dense=0,25, entités désactivées, normalisation fixe et rescoring complet |
| E3 | dense/entités désactivés, sans stemming, candidats=rerank_top_n=30, rerank_weight=1, cross-encoder local 17m |
| E4 | requête initiale + au plus deux expansions lexicales déterministes tirées de la question seule, sans LLM ni annotations |
| E5 | morceaux 512 caractères, pool primaire 100, déduplication des tours avant contexte limité à dix citations |

Les identifiants de citations et le départage des ex æquo sont stables entre bras.
Le namespace dépend du protocole et de la graine ; les empreintes du fichier
contenant les annotations servent uniquement de preuve, jamais de signal pour
les identifiants. Les magasins restent privés et distincts par question.

BM25 indépendant est réexécuté **dans chaque bras**, avec mêmes citations et
budget de contexte ; E5 applique son découpage et sa déduplication aux deux
systèmes. CPU, un thread, encodeur/cross-encoder batch=4 et longueur=256,
contexte 12 000 octets, pas de LLM, aucune facturation API. E3 reranke tous ses
candidats lexicaux admissibles : pas de mélange avec une queue de scores fusion
non normalisés. Un reranker demandé sans signal d'exécution fait échouer la
question au lieu de valider un fallback silencieux. Les appels/paires/jetons
réels du cross-encoder sont comptés.

Critère primaire : delta apparié de rappel contre le BM25 **du même bras**.
Secondaire : delta MRR. Rapport complet : échecs dans les dénominateurs, IC
bootstrap par groupes, latences à froid/à chaud, coûts, taille de magasin.
Les cinq comparaisons de développement sont exploratoires : aucun intervalle
isolé ne justifie un titre de victoire significative. Sans réglage additionnel,
choisir au plus un bras par delta rappel décroissant, puis delta MRR, puis p50
end-to-end croissant, et figer cette configuration.

Confirmation à prévoir **sur de nouvelles données indépendantes** : une seule
configuration figée contre BM25 ; borne inférieure IC95 du delta rappel >0 et
borne inférieure du delta MRR ≥0 ; publier effectif, échecs et coûts. Le protocole
oracle ne donne pas un rang au leaderboard. Romain doit décider du jeu de
confirmation et d'un éventuel budget LLM avant cette étape.

## Garde et commandes exécutables par Claude

Modèles déjà présents, sans téléchargement : MiniLM 91,6 Mo, Granite 220,2 Mo,
cross-encoder 17m 71,2 Mo. Un seul bras puis un seul système à la fois ; nice ≥15,
arrêt si swap >8,5 Go décimaux, disque libre <5 Go, ou mesure indisponible.
Surveillance 0,5 s ; 120 s/question, 1 500 s/bras, 9 600 s/campagne. Pas de
reprise automatique écrasant un bras incomplet. Les serveurs stdio privés sont
nettoyés par le superviseur existant, avec vérification du nonce propriétaire.
Le répertoire `.local-qualification` doit rester dans ce clone.

Après import du bundle, depuis ce clone, **hors sandbox**, exécuter ces trois
commandes dans l'ordre (relecture de plan.json entre prepare et run) :

```sh
cd /Users/romain/Documents/Codex/2026-10-09/v/work/implementation
./scripts/run_eval_development.sh 76af27628aec8bd853f3ad8a7b61223dcd4159c5 eval-dev-fusion-v1 diagnose
./scripts/run_eval_development.sh 76af27628aec8bd853f3ad8a7b61223dcd4159c5 eval-dev-fusion-v1 prepare
./scripts/run_eval_development.sh 76af27628aec8bd853f3ad8a7b61223dcd4159c5 eval-dev-fusion-v1 run
```

Le hash indiqué atteste le code exécuté ; le commit documentaire ultérieur ne le modifie pas.
`diagnose` produit `eval-dev-fusion-v1-diagnosis/report.json`, `per-question.json`
et `rescored.jsonl`, sans modèle ; `prepare` produit plan, partition et reçu avant
scores ; `run` produit C0/E1–E5 avec leurs propres frozen.json, prédictions,
rapports et preuves. Relecture attendue : pas d'échecs, différences appariées
par catégorie, trace primaire vs diagnostique, temps d'encodage par phase,
coût du reranking/multi-query et stabilité des trois recherches à chaud.

Vérifications légères effectuées ici :

```sh
PYTHONDONTWRITEBYTECODE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
PYTHONPATH=v4:v4/src /Users/romain/Memory-os-ai/.venv/bin/python -m pytest \
  -q -p no:cacheprovider tests/test_eval_experiments.py tests/test_e2e_eval.py \
  tests/test_evaluation_manifest.py tests/test_release_gate.py
ruff check src tests scripts
ruff check src tests v4/src v4/tests v4/bench scripts tools --select E9,F
sh -n scripts/run_eval_development.sh
```

**77 passed**, deux lints verts, syntaxe shell valide. Les gardes des commandes
prepare/diagnose refusent ici nice (`Operation not permitted`), code retour 2,
aucun dossier de campagne créé. Ces tests ne remplacent pas la campagne réelle.

Empreintes des sources historiques relues :

- report.json : `a9ed66b439efbfa913bf830ffcf76847f7df92bae745e8fd83a44e7c1f42bd2f`
- predictions.jsonl : `690b04af31d5dbfb152040833ebcabe7a23732c0c7ecc53518646f5a0ff49dce`
- frozen.json : `431577c2ce38793057fe2cfa2f5fedc5ff9dfbdf238454af2fde1eb7557a4e10`
