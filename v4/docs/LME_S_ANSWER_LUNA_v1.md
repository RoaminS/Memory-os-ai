# LongMemEval-S : campagne de réponses descriptive, Luna — v1

Décision de Romain du **2026-10-11** : « LongMemEval-S via Codex with
gpt-5.6 luna. GPT-4o is no longer available. » Cette campagne remplace la
tentative held-out refusée ; elle **ne transforme pas S en données inédites**.
BEAM 500K : 15 composantes restantes, seuil 30 impossible ; annotation auditée :
629 IDs valides, 70 abstentions sans IDs, un `source_chat_ids` manquant.
MemoryAgentBench-LRU : acquisition 49 342 452 octets, 110 contextes,
171 questions, 74 uniques utilisables, zéro groupe frais attesté : **NO-GO**.
Ces résultats rapportés par Claude ne seront ni réinterprétés ni effacés.

## Ce qui est figé avant résultats

[Protocole machine](../bench/protocols/lme-s-answer-luna-v1.json), puis
`prepare` scelle `plan.json` et son SHA dans `receipt.json`, avant encodage/LLM.
Une nouvelle campagne utilise un dossier neuf ; aucune suppression ou reprise
des tentatives précédentes. Codex n'exécute ni encodage neuronal ni appel CLI.

| Exigence | Preuve / refus |
|---|---|
| **500 questions S**, source SHA `08d8dad4be43ee2049a22ff5674eb86725d0ce5ff434cde2627e5e8e7e117894`, abstentions incluses | Empreinte entière à prepare et avant chaque phase/lot ; dénominateur exact, type connu et référence disponible ; aucune exclusion selon résultat |
| Lecteur **gpt-5.6-luna low**, juge **gpt-5.6-luna high** | Catalogue local, slug/effort inscrits au plan ; aucune substitution ; haut effort retenu avant résultats pour les rubriques/raisonnement temporel |
| Grading officiel par type, abstention `'_abs' in question_id` | [Source officielle](https://github.com/xiaowu0162/LongMemEval/blob/main/src/evaluation/evaluate_qa.py) récupérée en ≤20 Ko, SHA/provenance ; seules les cinq chaînes littérales sont lues par AST, jamais ses imports/client API ; espaces/retours conservés dans les prompts émis |
| Moteur **4.0.3 qualifié**, pas le relais 4.0.4 du clone actuel | Archive du commit **53030c2c99b09b5dc3f8467dda0a114e47477ee2**, extrait sous le plan ; manifeste de tous les fichiers ; vérification de version/chemin importé ; SHA de l'encodeur égal à la preuve de copie |
| Encodage de production | Granite97m snapshot `835ad14087e140460703cf0fae09f97d469d65c2`, auto→MPS obligatoire, native/torch.bfloat16, batch32, 512 tokens, normalisé float32 ; attestation complète égale à `copy-4.0.3-v2/report.json`, aucun fallback CPU |
| C4 vs BM25 du même bras | Mêmes observations datées, mêmes citations 1024 caractères, top10, enveloppe 12 000 octets ; `scoped_literal`, `lexical_guard_k=3` ; BM25 k1=1,2/b=0,75 avec mêmes IDs/départages |
| Aucune fuite d'annotation | Capture déterministe sans LLM, antérieure à la question, sans catégorie/référence ; lecteur reçoit question, date et observations uniquement ; juge anonyme reçoit question/référence/réponse, jamais bras/scores/recherche |
| Profil d'évaluation explicite | Magasin jetable isolé **par question**, encodeur résident réutilisé ; recency désactivée ; seuils de confinement dedup/supersede 1,01 ; aucune consolidation ni identité sémantique inventée ; ce n'est pas une extraction LLM de faits |

Les six types officiels sont conservés. Le grader applique notamment la tolérance
temporelle off-by-one, la règle des valeurs mises à jour et la rubrique souple des
préférences. Les prompts ne sont pas réécrits pour Luna : seule la voie de transport
et le modèle changent. Le juge répond en texte **yes/no**, sans instruction JSON
ajoutée. Le parseur refuse tout autre résultat, sans le compter arbitrairement faux
ni réessayer. Sur les résultats acceptés, le label égale celui du script officiel.
Le script officiel accepterait aussi une réponse contenant le mot `yes` : notre
validation stricte de format est une différence déclarée.

Le fichier amont n'a pas de révision Git attestée dans cet environnement : le
**contenu téléchargé**, ses SHA et les templates littéraux sont figés ; aucun
suivi automatique de `main` après prepare. Les termes audités doivent rester
identiques au snapshot local (hors continuation d'espaces), sinon acquisition
refusée et amendement requis avant inférence. Attribution/licence MIT :
[LongMemEval](../bench/protocols/LONGMEMEVAL_LICENSE.txt).

## Mesure et interprétation

Primaire : **exactitude micro sur 500 questions**, une réponse et un verdict par
question par bras, abstentions incluses. Comparaison : différence appariée C4−BM25.
Bootstrap 10 000 réplicats, seed20261011, IC95 ; composantes des
`answer_session_ids` partagés et des questions dérivées `*_abs` avec leur source,
politique `evidence-sessions-abs-v3`. Minimum **30 groupes**, dénominateur complet
exigé avant tout score final. L'effectif/groupage est calculé à prepare et publié.
Il peut différer des 466 groupes du rappel S, qui excluait les questions sans preuve.

Critère numérique descriptif : **borne basse IC95 Δaccuracy > 0**. IC indisponible :
critère faux avec raison, jamais remplacement par bootstrap de questions.
Résultats par `question_type` et sous-ensemble d'abstention : secondaires,
sans déclaration de supériorité multiple ; IC indisponible sous 30 groupes.
Les IC restent conditionnels aux distracteurs fixes. Les modèles sont identiques
dans les deux bras, les questions/ordres de bras alternent sans adaptation.
Les sessions Codex sont éphémères ; le juge ne voit pas son éventuelle réponse
antérieure comme lecteur, mais **un juge du même modèle reste une limite**.

Le rapport exporte les hypothèses au format officiel `question_id/hypothesis`,
les labels, les scores individuels, les IC, le ledger et les SHA des sorties.
Un échec ou un dénominateur incomplet produit seulement progression/usage,
**aucune estimation finale sur un sous-ensemble sélectionné par réussite**.

**Statut exclusivement DESCRIPTIF** : S a déjà servi à observer la récupération.
Le juge Luna n'est pas **GPT-4o-2024-08-06** ; le CLI ne garantit ni snapshot des
poids distants, ni température0/max_tokens10 comme le protocole API publié.
Aucun score de classement, confirmation indépendante ou « top1 » autorisé.
Le bras full-context n'est pas réalisable dans ce chemin CLI borné approuvé.
Il n'est pas remplacé par un oracle ni crédité à notre système : références
externes seulement, **Zep71,2 % et full-context GPT-4o60,2 %**, [papier Zep,
table2](https://arxiv.org/html/2501.13956v1). Ces configurations/modèles/méthodes
diffèrent et ne constituent pas une comparaison expérimentale contre nous.

## Budget, arrêts et jours de quota

- **2 000 appels réussis** : 1 000 lecteur +1 000 juge. Plafond cumulatif
  **2 200 tentatives**, réserve de 200 refus de quota exclusivement avant génération.
  Pas de répétition d'un résultat généré, d'une erreur réseau ou d'un appel incertain.
- Au plus **40 appels/lot**, 3 600s/lot ; lecteur90s/appel, juge180s/appel,
  temps CLI cumulé48h, récupération8h. Concurrence1/nice15 ; swap≤8,5GB,
  espace libre≥5GB. Échantillonnage périodique ; arrêt immédiat au seuil dépassé.
- Seuil d'arrêt **40 millions de tokens observés**, non un plafond imposé au
  fournisseur : un appel achevé peut dépasser le seuil ; aucun suivant alors.
  Chaque tentative est réservée durablement **avant** lancement. Usage input,
  output et cache récupéré même pour une réponse rejetée si un `turn.completed`
  valide unique existe ; coût inconnu reste inconnu, jamais zéro.
- Authentification **ChatGPT uniquement**, aucune lecture/export de token OAuth,
  aucune clé/client API. **0 dollar API**, utilisation du quota d'abonnement.
  Les tokens ne donnent aucun pourcentage de quota ni délai de recharge.
- Arrêt `batch`/refus pré-génération de quota : **reprise manuelle `--resume`**,
  même plan, mêmes fichiers/CLI/runtime/modèles. Aucun loop/scheduler automatique.
  `pending`/`blocked`/lock incertain : aucune reprise avant résolution opérateur.
  Arrêt budget/erreur/changement CLI : ne pas déplacer le dossier pour contourner
  le blocage ; garder les preuves et demander un amendement explicite.

Estimation, pas promesse : extrapolation du pilote10questions/40appels =
**18 117 400tokens et14 100s CLI (~3,9h)**. Le pilote utilisait Astrahigh sur
BEAM, donc prévoir **4–20h CLI** avec le nouveau judge/datacorpus, **3–6h récupération**
(ancien S~19,7s/question, profil CPU historique différent). Les limites48h/8h
laissent une marge ; les jours d'attente du quota sont inconnus. Aucune inférence
n'est faite par Codex pour affiner ces coûts ; seul Claude mesure sous le GO donné.

## Commandes pour Claude, hors sandbox

Importer la **branche nommée** du bundle, garder preuves/corpus d'origine ;
ne pas toucher le serveur/MCP installé ni le runtime. Voir le handoff pour
hashes de livraison et commit exact du harnais. Depuis le clone :

```bash
export MEMORY_EVAL_PYTHON=/Users/romain/.memory-os-runtime/venv-4.0.3/bin/python
export PYTHONDONTWRITEBYTECODE=1
PYTHONPATH=v4/src:v4 /Users/romain/Memory-os-ai/.venv/bin/python -m pytest -q -m 'not slow' v4/tests -p no:cacheprovider
ruff check --no-cache --config pyproject.toml src tests scripts
ruff check --no-cache --config pyproject.toml src tests v4/src v4/tests v4/bench scripts --select E9,F
bash -n scripts/run_lme_answer_campaign.sh

# Source officielle minuscule uniquement : pas de données/modèle supplémentaires.
nice -n 15 "$MEMORY_EVAL_PYTHON" scripts/acquire_lme_grader.py \
  --out .local-qualification/lme-official-grader-v1

./scripts/run_lme_answer_campaign.sh prepare \
  --plan-dir .local-qualification/lme-s-answer-luna-v1 \
  --source-commit 845507ee2180e7119508ee8e9fc1bdc4962b294b \
  --copy-proof .local-qualification/copy-4.0.3-v2/report.json \
  --official-evaluator .local-qualification/lme-official-grader-v1/evaluate_qa.py
```

Claude relit le plan scellé **avant** `retrieve` : source moteur53030c2,
SHA S, Luna low/high, MPS/native/32, 500questions, nombre de clusters≥30,
abstentions, provenance/templates, budgets et limite des claims.
Une phase refusée préserve ses fichiers et ne consomme aucun appel LLM.
Les étapes suivantes sont **successives**, jamais lancées simultanément :

```bash
./scripts/run_lme_answer_campaign.sh retrieve \
  --plan-dir .local-qualification/lme-s-answer-luna-v1

# Premier lot seulement, après récupération complète et quota disponible.
./scripts/run_lme_answer_campaign.sh answer \
  --plan-dir .local-qualification/lme-s-answer-luna-v1 --batch-calls 40

# À déclencher MANUELLEMENT après chaque état batch/quota, sans modifier le plan.
./scripts/run_lme_answer_campaign.sh answer \
  --plan-dir .local-qualification/lme-s-answer-luna-v1 --batch-calls 40 --resume

# Uniquement lorsque progress.json indique complete (2 000 appels acceptés).
./scripts/run_lme_answer_campaign.sh report \
  --plan-dir .local-qualification/lme-s-answer-luna-v1
```

Si le fichier officiel existe déjà, ne pas le retélécharger ni réexécuter acquire.
Si le plan existe déjà, ne pas refaire prepare/retrieve. Reprise exactement sur le
ledger initial après batch/quota ; garder `PROVENANCE.json`, `receipt.json`, tous
les prompts/events/usage et SQLite. Pas de publication/bascule/GitHub dans ce lot.
