# Campagne E2E tenue à l'écart — protocole v1, 2026-10-10

## Décision et portée

Romain autorise une campagne Codex sur quota ChatGPT, sans API ni dépense API,
avec lecteur **gpt-5.6-luna / low** et juge **gpt-6-astra / high**. Le GO de
bascule locale 4.0.3 est exécuté par Claude selon `QUALIFY_4_0_3.md` ; aucun GO de
publication PyPI n'a été donné. Ce lot ne change ni le serveur ni ses données.

Le pilote observé v2 est terminé : 10 questions, 40 appels (39 nouveaux et un
historique), 362 348 tokens, 282 secondes CLI, exactitude C4 0,40 / BM25 0,20.
Résultat descriptif, sans conclusion de supériorité. Rapport préservé
`.local-qualification/answer-pilot-warnings-v2/answers/report.json`, SHA-256
`a94a563b20a7cc3c1a75c0e075355b4d6b993377b44711534b9a8e42c0a0490c`. Aucun gain par catégorie
ne sert à sélectionner les futures questions ou à changer C4.

**Préenregistrement avant tout téléchargement ou résultat :**
[`ANSWER_HELDOUT_PROTOCOL_v1.json`](ANSWER_HELDOUT_PROTOCOL_v1.json).
Le lanceur applique ce contrat et scelle ensuite `plan.json` avec les empreintes
réelles des données et preuves, avant extraction, retrieval et appels Codex.
Tous les prompts sélectionnés sont contrôlés avant le premier appel, sans
exclure une question trop longue ; le budget d'un juge inclut ensuite sa réponse
candidate réelle, qui peut provoquer un arrêt si elle est trop longue.
Le protocole est figé ; le plan d'exécution ne peut être scellé avant acquisition
approuvée et audit réussi. Aucun corpus encore absent n'est présenté comme validé.

## Corpus proposé et GO distinct

[BEAM, dépôt des auteurs](https://huggingface.co/datasets/Mohammadta/BEAM/blob/main/README.md),
révision `3205395e897e7318c7b094ef4e6047b9b82dbb03`, partition 500K : **35 conversations,
86 081 645 octets de données décompressées annoncés**. Les **105 542 405 octets de
téléchargement publiés concernent toutes les partitions**, pas le seul fichier 500K.
Licence déclarée **CC-BY-SA-4.0**. Le fichier distant proposé est
`data/500K-00000-of-00001.parquet` ; sa taille physique et son SHA-256 LFS doivent
être lus dans les métadonnées à cette révision. Plafond strict : **110 000 000
octets pour ce seul fichier** ; aucune installation de dépendances ou de modèles.
La source est attribuée aux auteurs de
[Beyond a Million Tokens](https://arxiv.org/abs/2510.27246) ; conserver licence,
révision, provenance et attribution avec toute diffusion de données dérivées.

**Téléchargement en attente du GO de Romain**, explicitement demandé. Le script
refuse une destination existante, écrit uniquement dans `.local-qualification`
du clone, vérifie octets/SHA puis convertit en streaming avec le pyarrow déjà
présent dans `/opt/homebrew/bin/python3.11`. Aucun téléchargement automatique par
le lanceur de campagne. La taille d'exploitation JSONL dépend de la conversion ;
prévoir <1 Go de données et journaux, contrôlé par le garde disque de 5 Go.

## Audit d'indépendance et sélection, avant toute inférence

1. Vérifier provenance, 35 conversations, schéma strict, source/parquet/JSONL,
   révisions, licence et accord de téléchargement enregistré.
2. Comparer avec **tout BEAM 100K observé** et **tout LongMemEval-S observé**
   (l'oracle/dev utilise les mêmes questions et des historiques inclus dans S).
   Exclure une conversation entière dès recoupement : identité de conversation,
   seed `(category,id)`, titre/thème, narration normalisée >=80 caractères,
   question normalisée, tour normalisé >=160 caractères ou empreinte d'extrait.
   Les extraits utilisent des shingles de 64 mots et un winnowing de 32 shingles :
   95 mots consécutifs partagés garantissent une empreinte commune, même avec
   ajout d'un en-tête. Des correspondances très générales peuvent rendre ce
   contrôle conservateur ; ne pas relâcher le seuil après connaissance des données.
3. Regrouper les conversations restantes partageant ces signatures en composantes
   dépendantes ; publier IDs exclus, motifs et effectifs, sans texte des questions.
4. Choisir **une question par catégorie et conversation éligible**, hash déterministe
   de l'identité observable, jamais de la réponse, rubrique ou d'un score. Les dix
   catégories contribuent, dont abstention ; ordre des questions hashé et ordre des
   bras alterné. Au maximum **350 questions / 1 400 tâches lecteur+juge**.
5. Refuser avant retrieval si moins de **300 questions ou 30 composantes**.
   `audit.json` et `REFUSED.json` restent conservés ; aucune exécution sur un
   sous-ensemble plus petit et aucune relaxation automatique. Si BEAM 500K reprend
   les seeds de 100K, un autre corpus réellement nouveau et un nouveau GO sont
   nécessaires. La disponibilité du split n'est pas une preuve de fraîcheur.

Ce contrôle prouve l'absence des recoupements détectables définis, **pas l'absence
de paraphrase sémantique, de contamination d'entraînement des modèles ou de
scénarios communs non déclarés**. Ces limites accompagnent tout résultat.

## Contrat expérimental et critère figé

Moteur strictement identique à la source 4.0.3 de la copie qualifiée : C4,
`scoped_literal`, `lexical_guard_k=3`. Granite97m déjà en cache, même révision,
**auto résolu MPS, précision native, batch32, longueur512**. Device, paramètres,
dtypes, runtime et empreinte du code d'encodage sont attestés avant capture et
comparés exhaustivement à la copie qualifiée. Pas de fallback CPU. Les corpus
sont jetables, un magasin isolé par conversation, serveur résident, jobs séquentiels.

Extraction question-aveugle de citations : uniquement `chat/content/time_anchor`
observables ; chunks1024, top10, contexte12 000 octets par bras. BM25 utilise
**exactement le même corpus de citations**. Aucune référence, rubrique, catégorie,
seed/narration, annotation de preuve ou futur probe dans l'ingestion ou le lecteur.
Comme au pilote : date synthétique de capture2000-01-01, récence désactivée et
confinement de déduplication cosinus1.01. Le profil **d'encodage** est celui de
production ; toutes les politiques d'ingestion live ne sont pas revendiquées
comme reproduites. Pas de changement post-hoc de récence, supersession, prompt
ou budget de contexte.

Un `codex exec` éphémère par réponse ou jugement, sans outil/MCP/mémoire/web,
authentification ChatGPT obligatoire ; aucune clé ou token OAuth vers l'API.
Le juge reçoit candidat anonyme, question, référence et rubrique, jamais le bras.
Les prompts sont ceux du pilote v2. Le juge Astra n'est **pas** le snapshot GPT-4o
des publications ; les slugs sont fixes mais le service ne permet pas d'attester
les poids distants. Aucun remplacement automatique si catalogue/CLI change.
[Documentation officielle du mode non interactif](https://learn.chatgpt.com/docs/non-interactive-mode).

**Primaire :** exactitude binaire jugée, moyenne par question ; delta apparié
C4−BM25. Bootstrap des conversations/composantes entières, 10 000 réplications,
seed20261011, IC95 percentile. **Critère : dénominateur complet, zéro appel bloqué,
>=30 composantes et borne basse de l'IC95 du delta strictement positive.**
IC indisponible = critère non atteint. Pas d'arrêt anticipé de significativité,
pas de sélection de catégories, pas de transformation post-hoc du critère.
Les IC mesurent l'incertitude d'échantillonnage, pas la validité du juge.
350 questions ne garantissent pas la puissance pour un faible gain : sous
indépendance artificielle, un delta binaire a une demi-largeur conservatrice
~10,5 points ; les dépendances par conversation peuvent l'élargir davantage.
Les résultats catégoriels ou revues manuelles restent exploratoires.

**Full-context exclu** : les conversations 500K dépassent le chemin lecteur CLI
figé et son contexte plafonné ; ni troncature renommée full-context, ni API
autorisée. Aucun score Mem0/Zep/Letta publié n'est traité comme un bras apparié.
Un succès autorise seulement « meilleur que ce BM25 reproduit, dans ces conditions
et sur ces données auditées » ; **aucun classement officiel ou top 1**.

## Budgets, reprises et preuves

Projection du pilote, pas une mesure : 300–350 questions → 1 200–1 400 tâches,
~10,9–12,7 millions de tokens comptés et ~2,4–2,7 h CLI actif, hors attentes quota.
**0 $ API incrémental**, quota ChatGPT non estimable en pourcentage ; aucun coût
API fictif ni solde de quota inféré des tokens. Le retrieval MPS est estimé
~1,5–2,5 h à partir de 100K ×5, sans garantie de linéarité ; plafond **4 h**.

Plafonds globaux : tâches prévues + **200 tentatives de quota**, donc <=1 600
appels lancés ; **60 000 s CLI cumulées**, seuil observé **16 millions de tokens**,
40 appels/batch, 3 600 s/batch, 90 s/lecteur et 180 s/juge. Le seuil tokens est un
arrêt avant l'appel suivant, pas un plafond de génération serveur ; une réponse
peut le franchir. Les rejets de quota sans usage complet restent **tokens inconnus**,
jamais zéro. Tous les appels tentés comptent, même refusés ou interrompus.

Le garde vérifie swap <=8,5 Go décimaux, disque >=5 Go, nice15, concurrence1 ;
aucun encodage sur Codex, aucune modification du magasin en service. Pendant
retrieval, un parent supervise même un encodage bloquant et nettoie les processus
MCP qu'il possède. Pas de cron, boucle de reprise, API fallback ou modèle téléchargé.

`campaign.sqlite3` est le registre local durable (`synchronous=FULL`), lié à
l'empreinte du plan. Une tentative est réservée avant lancement ; logs, prompt,
usage, durée et reçu final sont conservés/empreintés avant acquittement. Les
réponses/jugements complets sont réutilisés sans nouvel appel. Une erreur inconnue,
outillage, schéma, timeout, garde machine, tentative pending ou lock abandonné
**bloque** ; ne pas enlever un verrou pour rejouer un appel incertain.

Seul un rejet pré-génération explicitement reconnu « You've hit your usage limit »,
sans item, outil, réponse ou tour complété, permet une reprise quota. L'usage
absent reste inconnu dans le rapport. Après arrêt quota ou limite de batch :
**attendre le quota / jour suivant puis lancer manuellement `--resume`**, sans
changer plan, code, CLI, modèles, données ni contextes. Les budgets ne se
réinitialisent pas. Si le CLI ou un modèle disparaît, conserver les preuves et
faire une revue d'amendement avant tout nouvel appel. Un batch n'est jamais une
nouvelle campagne.

## Commandes de Claude, hors sandbox

Depuis le clone, après import du bundle nommé (voir `ANSWER_HELDOUT_HANDOFF_v1.md`),
rejouer les tests légers. La bascule4.0.3 reste une procédure séparée exécutée par
Claude sous le GO déjà donné ; cette campagne n'en lance aucune étape.

**Uniquement après GO explicite du téléchargement**, avec la citation exacte
de la décision à la place de l'argument ci-dessous :

```bash
/opt/homebrew/bin/python3.11 scripts/acquire_beam_500k.py \
  --romain-download-go 'GO Romain YYYY-MM-DD : téléchargement BEAM500K plafonné110Mo' \
  --output .local-qualification/datasets/beam-500k-v1
```

Répertoire fresh ; échec conservé, jamais renommé pour prétendre à une acquisition
réussie. Le script vérifie la taille physique upstream avant download et refuse
si le fichier attendu/SHA manque ou si le plafond est dépassé.

```bash
export MEMORY_EVAL_PYTHON=/Users/romain/.memory-os-runtime/venv-4.0.3/bin/python
scripts/run_answer_campaign.sh prepare \
  --plan-dir .local-qualification/heldout-answer-beam-500k-v1 \
  --data .local-qualification/datasets/beam-500k-v1/beam_500k.jsonl \
  --provenance .local-qualification/datasets/beam-500k-v1/PROVENANCE.json \
  --copy-proof .local-qualification/copy-4.0.3-v2/report.json \
  --source-commit 31b7084ba766ff8d414a8ae70f692176426d9a4e
```

Si le runtime est ailleurs, utiliser **le Python réellement attesté lors de la
bascule**, avec les mêmes versions de dépendances que la copie qualifiée ; le
profil doit comparer exactement. Aucune installation/download automatique.
`prepare` ne fait aucune inférence. **Si REFUSED.json apparaît, ne rien lancer.**
Après audit/plan scellé, contrôler le nombre de groupes et l'empreinte de plan :

```bash
LC_ALL=C shasum -a 256 .local-qualification/heldout-answer-beam-500k-v1/plan.json
scripts/run_answer_campaign.sh retrieve \
  --plan-dir .local-qualification/heldout-answer-beam-500k-v1
scripts/run_answer_campaign.sh answer \
  --plan-dir .local-qualification/heldout-answer-beam-500k-v1 --batch-calls 40
```

Lire `progress.json`, sans modifier les preuves. Pour **chaque** batch suivant,
uniquement sur décision explicite de Claude/Romain après disponibilité du quota :

```bash
scripts/run_answer_campaign.sh answer \
  --plan-dir .local-qualification/heldout-answer-beam-500k-v1 --batch-calls 40 --resume
```

Après `status=complete` seulement :

```bash
scripts/run_answer_campaign.sh report \
  --plan-dir .local-qualification/heldout-answer-beam-500k-v1
```

Livrer `plan/receipt/audit`, contextes+attestation retrieval, SQLite registre,
chaque prompt/JSONL événement/stderr/usage, `progress.json`, `report.json` et leurs
empreintes. Si une étape échoue, conserver le répertoire ; aucun score complet
ou reprise automatique. Aucun appel Codex n'a été effectué par Codex pour ce lot.
