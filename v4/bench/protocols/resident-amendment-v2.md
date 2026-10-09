# Amendement préenregistré : développement résident et confirmation S

2026-10-09 — établi après C0 et l'arrêt E1 du protocole v1, **avant tout score du
nouveau protocole**. Aucun réglage de poids, aucun nouveau bras : E1–E5 et la
partition de développement de 103 questions restent ceux du préenregistrement
v1. Le moteur de production et son interface ne sont pas modifiés. Code
exécutable : `bench.e2e.amendment`, `resident`, `worker`, `run`.

## Motivation et mesures historiques autorisées pour dimensionner

C0 : 103 questions terminées, rappel mémoire 0,834 et MRR 0,668, contre
0,863 et 0,743 pour BM25. Ces valeurs ne montrent pas de supériorité.

E1 Granite : 33 questions terminées dans le budget de 1 500 s. Sur ces 33
sorties : initialisation médiane 4 104 ms, ingestion médiane 35 337 ms,
recherche médiane 38,2 ms. Totaux : démarrage 151,4 s, ingestion 1 287,3 s,
end-to-end 1 461,9 s ; 474 658 jetons d'encodeur en 1 287,8 s d'encodage,
soit 368,6 jetons/s. **L'ingestion domine** : supprimer le chargement à froid ne
suffit pas à rendre 1 500 s adéquates. Ces données dimensionnent uniquement le
budget ; les 33 scores partiels ne participent pas à la sélection.

## Résidence et isolation

Un processus MCP stdio privé et un encodeur résident par bras mémoire, pour
toutes ses conversations successives. Le cross-encoder E3 est également chargé
une fois. Une conversation est l'historique observable fourni pour un cas du
benchmark ; ses sessions sont rejouées chronologiquement, avec exclusion des
tours futurs par rapport à sa date de question. Les recherches primaire,
diagnostique et répétées utilisent toutes ce magasin résident.

Chaque conversation ouvre un **magasin vide et des indexes indépendants**, en
réutilisant les modèles. Le magasin précédent est fermé (flush et libération du
writer lease), puis supprimé dans le répertoire privé du processus. Aucun fait,
index lexical, score ou journal de requêtes d'une conversation ne fuit dans la
suivante. Le partage de modèles n'implique aucun partage d'observations.

Le contrôle `eval_open_conversation` existe exclusivement dans le serveur
`bench.e2e.offline_server` lancé avec un nonce propriétaire. Il accepte un seul
identifiant opaque hexadécimal de 24 caractères, jamais un chemin ou une
configuration. Un identifiant déjà tenté est refusé : aucune répétition d'une
mutation incertaine. Le serveur renvoie des preuves `encoder_reused` et
`store_empty`, vérifiées par le worker. La fabrique d'encodeurs est remplacée
seulement dans ce processus d'évaluation, avec paramètres de modèle immuables ;
aucun outil de production n'est ajouté.

Mesures enregistrées : démarrage MCP/modèles et coût de warmup **une fois** dans
`memory-lifecycle.json`, fermeture globale, fermeture/initialisation du magasin
par conversation, extraction, ingestion, première recherche sur modèle déjà
chargé et répétitions à chaud. Compteurs de modèles par conversation calculés
par différences ; les coûts de démarrage ne sont pas refacturés à chaque ligne.
Les recherches de trace ne remplacent pas la prédiction primaire. Les modèles
restent en CPU, un thread, batch=4, séquence=256 ; le contexte reste dix citations
et 12 000 octets UTF-8. E5 conserve son pool primaire de 100 pour sélectionner
dix tours distincts, appliqué également au BM25 du même bras.

## Budgets et terminaison — règles figées

| Exécution | Temps maximal | Conversation / absence de progression |
|---|---:|---:|
| E1 Granite, 103 cas + BM25 + rapports | 7 200 s | 300 s |
| Chacun des E2–E5 | 3 600 s | 300 s |
| Campagne de développement | 22 500 s | supervision tous les 0,5 s |
| Confirmation S, modèle MiniLM | 172 800 s (48 h) | 7 200 s |
| Confirmation S, modèle Granite | 604 800 s (7 jours) | 7 200 s |

Budget E1 : extrapolation de l'ingestion observée, 1 287,3 × 103 / 33 ≈
4 018 s ; 7 200 s laisse environ 79 % de marge pour variation des conversations,
IO, recherche, baseline et IC. Le maximum d'ingestion observé est 72,3 s : 300 s
par conversation laisse une marge supplémentaire. Les autres bras conservent
le modèle MiniLM ; leur plafond augmente sans modifier leur configuration.

Pour S, ordre de grandeur préalable : 115 000 jetons bruts par cas, deux
représentations indexées (épisodes et citations), soit environ 230 000 jetons
par cas. À 368,6 jetons/s, 500 cas représentent environ 312 000 s ; le plafond
Granite offre environ 1,94 fois cette extrapolation. Ce calcul est prudent mais
approximatif : tokenizer, troncature des représentations, taille réelle des
historiques et température CPU peuvent modifier le coût. MiniLM reçoit un
plafond de 48 h. **Aucune garantie de faisabilité des 500 cas sur ce Mac.**

La mémoire reste bornée structurellement : lecture streaming (entrée JSON
maximale 32 MiB), une conversation/index à la fois, un encodeur et éventuellement
un reranker locaux de moins de 300 Mo chacun, batch=4, ingestion maximale
16 000 000 octets de citations pour chaque système. Le haystack n'est pas réduit
aux sessions annotées, tronqué ou résumé en fonction de la question. Tout
historique futur est filtré par sa date observable, pas par ses annotations.
Le disque des anciens magasins est libéré entre conversations. Les ressources
sont vérifiées avant et pendant les workers : nice ≥15, arrêt si swap >8,5 Go,
disque libre <5 Go, ou mesure indisponible. Exécutions séquentielles ; aucun
modèle téléchargé, aucune connexion IP/DNS, aucun magasin de production ouvert.

- **Temps écoulé / timeout de conversation** : bras `infaisable`, preuves et
  questions manquantes conservées, bras exclu de la sélection ; passage au
  suivant après nettoyage vérifié et contrôle des ressources.
- **Erreur de justesse du protocole** (provenance incorrecte, modèle/reranker
  non exécuté, magasin non vide, exception non budgétaire), artefact modifié,
  source différente, ou dépassement swap/disque : arrêt de toute la campagne.
  Un faible score de rappel n'est pas une erreur technique bloquante.
- Toute ligne échouée déjà observée empêche de masquer une erreur de justesse
  derrière un timeout ultérieur. Les bras partiels ne deviennent pas complets
  par exclusion des questions restantes.

## C0 et reprise

C0 n'est pas relancé. `prepare-dev` valide ses preuves (tous les SHA-256 des
artefacts, reçu frozen/report, 206 prédictions uniques sans échec, 103 ids
sélectionnés), le commit historique attesté par Git, l'identité du modèle et la
configuration. Le code moteur, extraction, BM25, réponses, scorer, planning et
résolution de configuration doivent rester identiques. Namespace et budget de
contexte sont vérifiés. Les empreintes du dataset ne sont jamais des signaux
pour les citations ; leurs ids et départages sont stables entre bras.

La preuve de réutilisation a été vérifiée dans ce clone ; SHA-256 des prédictions
C0 : `dea4723c2a25085e9f7d78b6d3d61697815135448029abfab9694f77d1686568`.
Ses **scores** servent de référence historique. Ses latences à froid ne sont
pas comparables aux nouvelles latences résidentes, et C0 n'est pas candidat à
la sélection E1–E5.

Chaque bras reçoit un outcome lié au hash du plan et aux preuves de ses
artefacts. Relancer `run-dev` saute les bras complets et les bras infaisables
après revérification ; ne relance jamais C0. Un répertoire partiel sans outcome
reste intact et bloque une reprise automatique : un nouvel amendement est
nécessaire. On ne supprime ni ne réécrit les anciennes campagnes.

## Sélection et confirmation S — un seul candidat

Après terminaison de tous E1–E5 (complets ou infaisables), sélectionner parmi les
bras complets sans échec selon la règle v1 : delta rappel mémoire moins BM25 du
**même bras** décroissant, puis delta MRR décroissant, puis p50 end-to-end
croissant ; dernier ex æquo par identifiant lexical. Aucun réajustement après
les scores. Sans bras complet, aucune confirmation autorisée. « Gagnant de
sélection » ne signifie pas que le bras dépasse BM25.

`prepare-s` écrit durablement le gagnant et verrouille l'unique candidat **avant
la lecture de S pour sa préparation**. Les paramètres du bras et preuves de
développement sont incorporés au plan. Puis vérification du fichier fourni :

`/Users/romain/Memory-os-ai/v4/bench/data/longmemeval_s.json`

SHA-256 imposé :
`08d8dad4be43ee2049a22ff5674eb86725d0ce5ff434cde2627e5e8e7e117894`.

L'inventaire doit montrer une variante S, non oracle, et exactement 500 questions.
Aucune partition de S : les 500 historiques entiers sont exécutés contre le
BM25 du même bras. Source et modèles restent figés. La confirmation retire la
requête de trace supplémentaire pour contenir son coût ; les signaux primaires
restent ceux du bras, E5 garde le pool=100 nécessaire à sa diversité. Une
répétition à chaud par question, avec coût compté, et les mêmes limites de
contexte/ingestion pour les deux systèmes. Réponses et expansions restent sans
LLM, sans données futures ni annotations transmises aux clients.

Critère préenregistré : campagne complète, zéro échec, 500/500 exécutées ;
**borne inférieure IC95 du delta rappel >0 ET borne inférieure IC95 du delta
MRR ≥0**. Bootstrap apparié, 2 000 réplications, graine 20261009, groupes
connectés de sessions de haystack observables ; les cas sans preuve annotée
restent NA pour la récupération et sont dénombrés. IC indisponible ne satisfait
jamais le critère. Rapport des ressources, coûts, latences et dénominateurs.
Ni EM/F1/contient non officiels, ni résultat de récupération ne donnent un rang
au leaderboard Agent Memory.

**Indépendance vérifiée, pas présumée** : `prepare-s` compare les ids de S aux
500 ids oracle déjà observés et annonce leur chevauchement avant exécution.
Tout chevauchement qualifie le résultat de validation entre variantes, pas de
confirmation sur questions indépendantes. Les distracteurs partagés de S
peuvent aussi connecter toutes les questions en un seul groupe : dans ce cas
l'IC regroupé est indisponible, aucune victoire significative déclarée. Le
lanceur distingue `numerical_criterion_met` et `independent_confirmation_passed`.
Il refuse un second candidat pour cette campagne, même si le premier échoue.
Ces limites doivent être relues dans le plan S avant d'engager une longue
exécution. Des données nouvelles pourraient être nécessaires pour conclure.

## Livraison et commandes Claude — hors sandbox

Depuis ce clone, après import du bundle, exécuter dans l'ordre :

```sh
cd /Users/romain/Documents/Codex/2026-10-09/v/work/implementation
task_source=343a1e6c4a93df33e3de82bc394dc0dcee943959
./scripts/run_eval_resident.sh "$task_source" prepare-dev
./scripts/run_eval_resident.sh "$task_source" run-dev
./scripts/run_eval_resident.sh "$task_source" prepare-s
./scripts/run_eval_resident.sh "$task_source" run-s
```

Relire `.local-qualification/eval-resident-dev-v2/plan.json` après prepare-dev,
puis `.local-qualification/eval-confirmation-s-v1/plan.json` après prepare-s
(particulièrement gagnant, empreintes, chevauchement, groupes et budgets).
Pour reprendre le développement : répéter uniquement la commande `run-dev`.
Les étapes prepare exigent des dossiers nouveaux ; ne pas réutiliser v1.

Relecture adverse attendue : schéma du contrôle privé, un seul chargement de
modèle, magasin vide avant chaque capture et absence de fuite de conversation,
comptage démarrage/ingestion/recherche séparé, classes de terminaison et
réutilisation des outcomes, verrou du gagnant et critères d'IC.

Preuves locales, sans modèles ni réseau :

```sh
PYTHONDONTWRITEBYTECODE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
PYTHONPATH=v4:v4/src /Users/romain/Memory-os-ai/.venv/bin/python -m pytest \
  -q -p no:cacheprovider tests/test_eval_resident.py tests/test_eval_experiments.py \
  tests/test_e2e_eval.py tests/test_evaluation_manifest.py tests/test_release_gate.py
ruff check src tests scripts
ruff check src tests v4/src v4/tests v4/bench scripts tools --select E9,F
sh -n scripts/run_eval_resident.sh
```

101 tests passent, deux lints verts, syntaxe shell valide. Les tests couvrent
l'isolation et le partage de modèle, les chemins interdits, le non-rejeu, les
coûts de warmup, la continuation uniquement sur budget, les dénominateurs,
la reprise sans relancer les bras, le gagnant écrit avant accès S, le refus
d'un second candidat et les deux bornes d'IC. Une vérification d'intégration
légère des preuves réelles de C0 passe également.

Les gardes nice/swap du sandbox empêchent toute campagne réelle ici ; aucune
nouvelle prédiction ni aucun score S n'a été produit. Claude doit vérifier le
MCP stdio résident réel et les campagnes hors sandbox. Aucun push GitHub.
