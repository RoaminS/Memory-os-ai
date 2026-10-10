# BEAM 100K : protocole prospectif C4/BM25, 2026-10-10

Statut : préenregistré **avant toute inférence BEAM**, aucun résultat de qualité
calculé pendant la préparation. Les contrôles de schéma et les tests synthétiques
ne constituent pas une campagne. C4 est fixé par la décision de Romain ; aucun
réglage sur BEAM, aucune soumission, aucun changement du serveur de production.

## Données et audit

Source : [Mohammadta/BEAM](https://huggingface.co/datasets/Mohammadta/BEAM),
révision `3205395e897e7318c7b094ef4e6047b9b82dbb03`, split **100K**,
CC-BY-SA-4.0. Ne pas confondre cette taille commerciale avec une mesure exacte
du tokenizer du lecteur. Aucun corpus, référence ou réponse n'est livré dans le bundle.

| Fichier local autorisé en lecture | SHA-256 |
|---|---|
| `~/Memory-os-ai/v4/bench/data/beam/beam_100k.parquet` | `c0519be25907005ba873c927c50877471d550873039d96c041554d0075a78ace` |
| `beam_100k.jsonl`, conversion pyarrow 23.0.0 déjà réalisée par Claude | `613b85b9e5eca98b88de25bedb7490f1dbf61ae4c361d544452feea19bfa18a3` |
| `PROVENANCE.json` | `121acb5118facc75f771c2bb6e7d46b3113d622a9172d60cf1e3efe7df08857e` |
| LongMemEval-S comparé | `08d8dad4be43ee2049a22ff5674eb86725d0ce5ff434cde2627e5e8e7e117894` |

Audit exécuté : **20 conversations, 5 732 messages, 400 questions** ; 40 questions
dans chacune des dix catégories : abstention, contradiction_resolution,
event_ordering, information_extraction, instruction_following, knowledge_update,
multi_session_reasoning, preference_following, summarization, temporal_reasoning.
Les réponses attendues et rubriques existent pour les 400 questions.

Les 360 questions hors abstention contiennent `source_chat_ids`, sous forme de
liste ou dictionnaire de listes. **355** ont une annotation non vide, toutes les
références non vides pointent vers des ids de messages existants et uniques.
Ce sont des annotations de source publiées, dont la validité sémantique n'a pas
été revue humainement. Cinq annotations sont explicitement vides :

| Conversation / catégorie / index de question (base 0) | Id opaque publié |
|---|---|
| 9 / preference_following / 1 | `8b9ffeda4682f7999f3d7b2d` |
| 18 / summarization / 0 | `5028c90a23d642468dc4a49f` |
| 18 / summarization / 1 | `4263d29ec2529cdd9836fdda` |
| 20 / summarization / 0 | `72d2733247873e8b0023e117` |
| 20 / summarization / 1 | `f7a4731c5a1c63b713dfa1bb` |

Ces cinq questions et les 40 abstentions sont traitées, mais exclues du
dénominateur rappel/MRR. Aucune preuve inventée, aucune exclusion fondée sur
le résultat. Elles restent admissibles aux mesures de réponse.

Recoupement exact normalisé NFKC/casse/espaces avec les 500 questions et tous
les messages de LongMemEval-S : **0 question, 0 message**. Cette méthode ne
démontre pas une absence de paraphrases, de source générative commune ou de
contamination pré-entraînement. Aucune question/réponse du holdout n'a été
inspectée pour régler le moteur ; l'audit lit uniquement les champs nécessaires
à la validation, au comptage et aux empreintes.

`probing_questions` contient effectivement une **représentation Python**,
contrairement à l'hypothèse « str JSON ». Le décodeur préfère JSON strict ; sinon
AST de littéraux dict/list/scalaires uniquement, puis `ast.literal_eval`, jamais
`eval`. Limites : 8 MB/ligne, 100 000 nœuds, profondeur 30 ; doublons de clés,
appels, noms, opérateurs et références pendantes refusés. Pyarrow n'est pas requis.

## Traitements et pare-feu d'annotations

Un seul bras moteur : **C4, scoped_literal + lexical_guard_k=3**, fusion CC et
poids de 4.0.2 conservés ; Granite-97m local déjà présent (220 227 876 octets),
instantané `835ad14087e140460703cf0fae09f97d469d65c2`, empreintes de chaque
fichier gelées dans le plan. Aucun téléchargement/fallback.

Déviations opérationnelles explicites du serveur : CPU, batch=4 ; séquence=512
comme le défaut 4.0.2 ; JSONL privé ; récence désactivée. BEAM ne fournit pas une
date fiable pour chaque message : aucun calendrier n'est inféré à partir du gold.
La date technique uniforme `2000-01-01 UTC` sert uniquement à capturer les
observations ; les `time_anchor` explicites restent visibles dans le texte.
Cette expérience évalue les défauts **de récupération C4**, pas une identité
de tous les réglages de déploiement (récence/device/batch).

Seuls content/role/id/session/time_anchor du chat sont transmis. Seed, narratives,
profil synthétique, plan, user_questions, index et question_type sont exclus.
Questions de sondage transmises seulement à la recherche, **après** ingestion
de toute la conversation ; leurs réponses, rubriques, difficulté, catégories et
source_chat_ids sont réservés au scoreur/juge. Capture de citations sans LLM,
chunks de 1 024 caractères, ids issus d'observables uniquement.

MCP stdio privé réel, modèle résident, magasin neuf par conversation ; mêmes
citations exactes pour BM25 et mémoire. BM25 littéral du banc inchangé, même
top_k=10 et même plafond de contexte UTF-8 de 12 000 octets. Scores calculés
après ce plafond et regroupement des ids de tours ; nommer la métrique
« rappel de tours annotés sous budget @10 », pas « réponse correcte ».
Rangs, contextes, temps et compteurs d'encodage sont conservés.
Recherche monorequête sur le projet isolé, `kinds=["fact"]` : les épisodes
provenant de la capture restent stockés/indexés et leur coût est compté, mais
ne sont pas admissibles à la récupération. Ce résultat ne démontrera donc
pas une supériorité pour une recherche non filtrée sur tous les types de mémoire.

## Hypothèse, intervalles et arrêts

Confirmation de récupération : 400 questions exécutées, 355 scorables pour
rappel/MRR ; **20 conversations** contributrices. Resampling de conversations
entières, apparié mémoire/BM25, 2 000 réplicats, seed=20261010, IC percentile95,
moyenne pondérée par questions dans les conversations resamplées.

Critère unique : **borne basse IC95 de ΔRecall@10 > 0 ET ΔMRR moyen ≥ 0**,
toutes les questions complètes et aucun échec. Minimum **20 clusters**. Avec
moins de clusters ou un IC indisponible : résultat descriptif/inconclusif,
jamais un succès ; aucune relaxation a posteriori. Les catégories sont
descriptives sans sélection multiple. Vingt clusters limitent fortement la
puissance et la portée d'une généralisation. Aucun rang officiel revendiqué.

Le pilote de réponse utilise deux conversations choisies par hash d'id
observable, une question/catégorie/conversation choisie par hash de question :
**20 questions**, membership gelé avant résultats. Ces deux conversations
sont exclues d'une éventuelle confirmation d'**exactitude des réponses**
(18 conversations, 360 questions restantes). Elles restent dans le test
de récupération préfixé à 20 conversations : ni C4 ni ses paramètres ne
peuvent changer après le pilote. Toute modification implique une nouvelle étude,
pas une nouvelle tentative « confirmatoire » sur ces mêmes conversations.

Exécution séquentielle nice≥15, swap≤8,5 GB décimaux et disque libre≥5 GB ;
mesure refusée = arrêt. Surveillance par le superviseur toutes les 0,5 s,
y compris pendant l'encodage bloquant ; arrêt du worker et de ses serveurs privés.
Budget récupération pilote : 2 h ; complète : 8 h ; plafond par conversation :
1 h. Arrêt de ressources ou de temps = campagne incomplète/infaisable, aucun
score confirmatoire. Pas de reprise automatique ou de réingestion après résultat
incertain. Estimer la durée complète via temps d'ingestion des deux conversations
pilotes et nombre de citations ; publier une fourchette, pas une promesse.

## Lecteur/juge Codex : utile seulement après pilote

Contrat du CLI vérifié avec la compétence OpenAI Docs ; références officielles :
[mode non interactif](https://learn.chatgpt.com/docs/non-interactive-mode),
[configuration](https://learn.chatgpt.com/docs/config-file/config-reference),
[modèle Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna).

La fiche modèle n'annonce pas `minimal` ; **le catalogue Codex local annonce
low/medium/high/xhigh/max et pas minimal**. Le plan par défaut conserve la
commande demandée, mais preflight refuse l'inférence : aucune consommation.
Pas de remplacement silencieux. Un effort `low` exige l'accord explicite de
Romain puis un **nouveau plan** préparé avec `--effort low` avant résultats.
Même si le catalogue valide l'effort, toute erreur réelle du CLI arrête le pilote.

L'adaptateur utilise exclusivement l'authentification Codex déjà existante,
avec `forced_login_method="chatgpt"` (connexion API refusée), aucune clé API,
aucune lecture/copie d'auth.json. Version et SHA-256 du binaire CLI figés ;
le slug demandé est fixe, mais les poids/version distants ne sont pas attestés
par `exec` : limite supplémentaire de reproductibilité. Chaque appel est neuf, éphémère,
cwd temporaire hors dépôt, read-only, sans config utilisateur, règles, MCP,
apps/plugins/hooks, shell ou web ; chargement AGENTS désactivé. Les événements
de tout outil ou les schémas inconnus font échouer la preuve d'isolation.
Les observations sont des données ; les préférences utilisateur enregistrées
peuvent orienter la réponse, sans autoriser l'exécution d'instructions/outils.
Le seul réseau nécessaire est celui du CLI vers son fournisseur, **hors sandbox**
par Claude ; aucune requête vers la mémoire de production ou un leaderboard.

Même lecteur et prompts pour les deux systèmes, ordre des bras déterministe
alterné par question, juge anonyme (sans nom de système) avec référence/rubrique.
Scoring **non officiel**, biais corrélé lecteur/juge explicitement conservé.
EM normalisé/F1/contient-la-réponse sont des diagnostics, peu adaptés aux
rubriques de préférence, synthèse et abstention, pas le critère principal.

Plafonds : 20 questions uniques ; **84 appels** maximum (40 lectures, 40
jugements, 4 contrôles sur deux questions déjà sélectionnées), 45 min total,
90 s/appel, 500 000 tokens input+output réels cumulés, prompt≤24 000 octets.
Les contrôles jugent une référence positive et une réponse manifestement
étrangère pour deux questions d'extraction. Les quatre doivent passer **avant**
les lectures. Erreur modèle/auth/quota/outil/JSON ou dépassement : arrêt immédiat,
sans retry, sans modèle/effort de secours. Les appels échoués sont journalisés
comme tentatives avec usage inconnu, jamais annoncés gratuits.

Utilité provisoire : ≥4/40 réponses jugées correctes, quatre contrôles verts,
aucun échec, budgets respectés. **Claude/Romain doivent annoter sans voir le
verdict du juge les dix premières réponses anonymisées** ; accord humain/juge
≥90 % requis. Cette barre est un filtre de faisabilité, pas une validation
scientifique du juge. Le même modèle pour lecteur et juge demeure une limite.
`human-audit.json` ne contient ni système ni verdict ; comparer ensuite avec les
dix premières lignes de `answers.jsonl`.

Usage enregistré : input/cached/output tokens et temps de chaque appel.
Le CLI ne fournit pas de pourcentage de quota d'abonnement restant ; **ne pas
le déduire des tokens ou convertir en prix API**. Projection descriptive pour
360 questions restantes : 1 440 appels, durée/tokens au prorata du pilote,
avec risque de variation/quota. Aucun lancement de cette campagne de réponses
sans revue humaine, utilité démontrée et **nouveau GO de Romain**.

## Commandes Claude (aucune inférence déjà exécutée)

Depuis le clone Codex, après import du bundle, remplacer SOURCE par le hash
du commit de code livré (pas un commit documentaire ultérieur). Les scripts
réfutent toute différence de source, modèle ou données avec le plan.

```bash
cd /Users/romain/Documents/Codex/2026-10-09/v/work/implementation
bash scripts/run_beam.sh prepare --source-commit SOURCE --plan-dir .local-qualification/beam-c4-v1-minimal
bash scripts/run_beam.sh preflight --plan-dir .local-qualification/beam-c4-v1-minimal
```

Le dernier appel retourne **3** aujourd'hui (minimal absent), sans inférence.
Pour la mesure de récupération, indépendante du lecteur :

```bash
bash scripts/run_beam.sh pilot-retrieval --plan-dir .local-qualification/beam-c4-v1-minimal
bash scripts/run_beam.sh retrieve --plan-dir .local-qualification/beam-c4-v1-minimal
```

Le pilote de lecteur `pilot-reader` produit aujourd'hui un rapport bloqué,
avec zéro appel, même sans lancer la récupération :

```bash
bash scripts/run_beam.sh pilot-reader --plan-dir .local-qualification/beam-c4-v1-minimal
```

**Seulement après accord low**, nouveau plan et membership inchangé :

```bash
bash scripts/run_beam.sh prepare --source-commit SOURCE --effort low --plan-dir .local-qualification/beam-c4-v1-low
bash scripts/run_beam.sh preflight --plan-dir .local-qualification/beam-c4-v1-low
bash scripts/run_beam.sh pilot-retrieval --plan-dir .local-qualification/beam-c4-v1-low
bash scripts/run_beam.sh pilot-reader --plan-dir .local-qualification/beam-c4-v1-low
```

Ne pas lancer deux variantes en parallèle. Les dossiers sont créés exclusivement :
pas d'écrasement d'une tentative. Restituer plan/receipt, audit, report, prédictions,
contextes, usage et contrôles/human-audit ; données et sorties restent privées.
