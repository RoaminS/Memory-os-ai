# MemoryAgentBench LRU : acquisition et audit sans inférence

Décision de Romain, **2026-10-10**, citation exacte :
**« download and audit MemoryAgentBench-LRU »**.
Périmètre : acquisition du seul split LRU, plafond physique **55 000 000 octets**,
audit d'indépendance/annotations, **0 inférence**. Aucun lecteur, juge, modèle
d'embedding, installation de dépendance, publication ou modification de production.
Un verdict d'éligibilité GO ne lance ni n'autorise la campagne : plan scellé et
protocole adaptés au corpus restent indispensables.

## Acquisition figée

- Dataset : `ai-hyz/MemoryAgentBench`, split `Long_Range_Understanding`.
- Révision complète immuable : `7ea066982b140a19337e17e60d45d4076e042faf`,
  vérifiée dans l'API **avant** transfert ; aucune résolution de `main` ni
  acceptation d'une autre révision à l'exécution. Le SHA du fichier est également
  figé indépendamment. [Commit officiel](https://huggingface.co/datasets/ai-hyz/MemoryAgentBench/commit/7ea066982b140a19337e17e60d45d4076e042faf).
- Fichier unique : `data/Long_Range_Understanding-00000-of-00001.parquet`.
- SHA-256 attendu :
  `5ab175461954db67770d4a4cb69e569b513ebb96aceb9ee79b57f67488bcd539`.
- Taille officielle arrondie : ~49,3 Mo ; taille physique exacte à vérifier dans
  les métadonnées LFS **avant** transfert, comprise dans le plafond 55 Mo.
- Licence : `mit` vérifié dans les métadonnées de la révision **et** le front matter
  du README sauvegardé. Cela atteste la déclaration du benchmark ; cela ne vérifie
  pas automatiquement les licences de tous les livres/documents réutilisés.
- Conversion avec le pyarrow déjà installé dans Homebrew Python 3.11, batch de
  **1 ligne**, sans `default=str` ni autre coercition. Schéma à quatre colonnes,
  exactement 110 contextes, plafond JSONL 150 Mo, plafond par ligne 32 Mo.

[Carte officielle](https://huggingface.co/datasets/ai-hyz/MemoryAgentBench/blob/main/README.md?code=true)
et [métadonnées du fichier](https://huggingface.co/datasets/ai-hyz/MemoryAgentBench/blob/main/data/Long_Range_Understanding-00000-of-00001.parquet).

Le dossier doit être neuf et sous le clone `.local-qualification` ; une erreur
conserve `REQUEST.json`, les métadonnées déjà reçues, les fichiers `.partial` et
`FAILED.json`. Aucune reprise en place ni nouvelle tentative automatique.
Avant tout réseau, le script exige un convertisseur local disponible. Aucun
`load_dataset()`, transfert d'autre split ni téléchargement de modèle.

`PROVENANCE.json` contient le GO exact et sa date, révision complète, URL, taille
physique prévalidée, octets transférés, cap, SHA parquet/JSONL/carte/métadonnées,
empreinte du code d'acquisition, versions Python/pyarrow, méthode de conversion,
110 contextes et compteur d'inférence nul. Le SHA et la taille réels doivent
correspondre aux métadonnées et au SHA figé avant toute conversion.

## Audit et règle GO / NO-GO

L'audit n'importe ni le moteur ML ni CodexReader. Il vérifie les empreintes de
l'acquisition, les relit après le scan, et fait de même pour les corpus observés,
le registre, la revue de sources et ses preuves. Entrées ouvertes en lecture seule.

Le rapport donne : contextes/questions, aliases de réponses, questions avec
références non vides et alignées, anomalies du schéma, familles de sources,
annotations disponibles, composantes fondées sur les empreintes **distinctes**
des composantes avec origines attestées, recoupements/exclusions, capacité en
questions uniques dans les groupes frais, durée et empreintes de tous les inputs.
Il contient des hashes de questions, jamais le texte des questions, réponses,
keypoints ou autres annotations. Le JSONL d'acquisition, privé, conserve les données
originales pour une qualification future.

Format accepté : `context: str`, `questions: list[str]`, `answers: list[list[str]]`
avec longueurs alignées, `metadata: dict` avec les champs publiés. Un couple sans
référence non vide n'est pas utilisable ; des champs inconnus ou listes de
métadonnées non alignées rendent les questions du contexte non utilisables.
Une erreur structurelle bloque l'audit, sans aplatir/inventer des réponses.

Les références textuelles suffisent **structurellement** à un juge de réponses ;
elles ne démontrent ni la justesse sémantique du gold ni la disponibilité de
preuves annotées pour Recall@k. Aucun ID de preuve positif n'est fabriqué.
`keypoints`, `has_answer`, `demo`, dates/types/IDs de questions et événements
annotés restent des données de scorer. L'ingestion future utilisera **le seul
`context` observable**, puis la question pour la recherche ; jamais ces annotations.
Toute utilisation additionnelle des événements/dates exige une définition
préalable dans le futur protocole. Aucun score officiel n'est produit par cet audit.

### Origines réelles et corpus publics

Le [loader officiel](https://github.com/HUST-AI-HYZ/MemoryAgentBench/blob/main/utils/eval_data_utils.py)
emploie `metadata.source` pour filtrer un **sous-dataset**. Ce champ ne prouve pas
110 livres indépendants ; `qa_pair_ids` et les hashes de contextes non plus.
Sans preuve question-blind de l'origine, les contextes d'une famille non attestée
sont groupés conservativement et **ne comptent pas** comme groupes frais certifiés.
Le premier audit pourra donc rendre NO-GO et fournir un template de revue de sources.
C'est une absence de preuve d'indépendance, pas une conclusion que tous les textes
proviennent effectivement du même livre.

Un `--source-review` facultatif permet une seconde passe **toujours sans inférence**,
dans un nouveau dossier. Pour chaque hash de contexte, la revue fournit :

- `canonical_work_id` : identité globale du livre/document/scénario original,
  identique pour tous ses fragments/éditions et tous ses sous-datasets ; jamais
  un simple numéro de ligne ou ID de question rebaptisé ;
- `original_source_url`, `original_licence`, `licence_url` : références primaires
  et vérification de l'usage prévu (`intended_use_permitted: true`) ;
- `mapping_proof_path` et `mapping_proof_sha256` : preuve locale privée, <=2 Mo,
  reliant ce contexte à l'origine, par exemple table officielle d'origine, ID du
  document ou titre vérifié avec citation et provenance. Le booléen seul ne suffit pas ;
- `previously_observed` : vrai si ce travail original a déjà servi au projet ;
  les groupes correspondants sont exclus même sans copie textuelle détectée.

La revue exige `reviewer`, `date`, `question_blind: true`. La véracité sémantique
des mappings/licences reste une attestation humaine : le script vérifie leur
complétude et leurs fichiers/hashes, sans prétendre identifier un livre par magie.
Ne consulter ni questions, ni réponses, ni scores pour choisir les mappings.
Conserver le premier NO-GO. Aucun autre corpus téléchargé sous ce GO.

### Recoupements et regroupement

Le registre livré couvre BEAM 100K, BEAM 500K, LongMemEval-S, oracle et LoCoMo.
Les trois empreintes connues sont imposées ; oracle/LoCoMo sont fingerprintés
avant/après. Ajouter au registre privé toute autre source publique **déjà utilisée**
dans le projet, sous format pris en charge : `beam`, `longmemeval`, `locomo`, `mab`
ou `text` (fichier textuel <=32 Mo). Cela n'autorise aucun téléchargement supplémentaire.
La couverture des données préentraînement des modèles ne peut pas être établie.

Normalisation NFKC/casefold/espaces, hashes de questions exactes, textes complets
et empreintes d'extraits. Une fenêtre de 32 shingles de 64 mots garantit la détection
d'au moins une empreinte pour toute séquence identique de 95 mots, malgré des
en-têtes/décalages. Un rolling hash choisit la fenêtre ; **SHA-256 du texte** vérifie
le match. Les extraits partagés regroupent conservativement les contextes, même
s'ils pourraient être du boilerplate ; aucune exclusion heuristique cachée de ces
matches. Les livres/URLs canoniques attestés sont également regroupés. Tout
recoupement avec une donnée observée exclut **la composante entière**.

Seules les empreintes cibles et les hashes de déduplication résident dans une
SQLite jetable ; les textes observés sont streamés et jetés, notamment les sessions
oracle/S répétées. Cache SQLite 8 Mo, limite DB 350 Mo, guard régulière durant les
scans et calculs. nice>=15, swap<=8,5 Go, disque libre>=5 Go ; arrêt si mesure inconnue.
Budget audit 2 h ; tout arrêt laisse une preuve d'échec et interdit GO.

**GO d'éligibilité** uniquement si : acquisition vérifiée, scan complet de 110
contextes, >=30 composantes fraîches avec origines/licences attestées et >=400
questions uniques avec références utilisables dans ces composantes.
Sinon **NO-GO**, raisons explicites, sortie CLI **2** avec rapport conservé.
Dans tous les cas `campaign_authorized=false`, `requires_sealed_plan_and_protocol=true`,
`inference_calls=0`. Un CI statistique futur et la puissance ne sont pas garantis
par ces deux seuils. Ils ne sont pas un verdict de performance C4 vs BM25.

## Commandes exactes pour Claude, séquentielles hors sandbox

Depuis `/Users/romain/Documents/Codex/2026-10-09/v/work/implementation` après import
du bundle nommé ; les scripts rendent leur propre nice>=15 effectif.

```bash
/opt/homebrew/bin/python3.11 scripts/acquire_mab_lru.py --romain-download-go 'download and audit MemoryAgentBench-LRU' --output .local-qualification/datasets/mab-lru-v1
/opt/homebrew/bin/python3.11 scripts/audit_mab_lru.py --acquisition .local-qualification/datasets/mab-lru-v1 --observed-registry v4/docs/MAB_LRU_OBSERVED_v1.json --output .local-qualification/mab-lru-audit-v1
```

Ne pas enchaîner en arrière-plan. Limiter cette fenêtre à deux jobs : acquisition
et audit initial. La passe revue ci-dessous appartient à une fenêtre ultérieure,
sans troisième lancement immédiat. Si acquisition échouée, conserver le dossier
et diagnostiquer ; l'audit refusera l'absence de provenance qualifiée. Si NO-GO
faute de sources attestées, remplir/copier le template dans un fichier privé,
documenter les preuves originales, puis exécuter une nouvelle passe :

```bash
/opt/homebrew/bin/python3.11 scripts/audit_mab_lru.py --acquisition .local-qualification/datasets/mab-lru-v1 --observed-registry v4/docs/MAB_LRU_OBSERVED_v1.json --source-review .local-qualification/mab-lru-source-review-v1.json --output .local-qualification/mab-lru-audit-reviewed-v1
```

Une extension du registre doit elle aussi être un fichier privé nouveau, passé
via `--observed-registry` et conservé/hashé dans le rapport. Le rapport initial et
le protocole BEAM refusé restent intacts. Les qualifications 4.0.3 existantes ne
qualifient pas automatiquement le nouveau relais 4.0.4 ; aucun rollout ici.

Vérifications source, légères, aucun réseau/inférence :

```bash
PYTHONPATH=v4/src:v4 PYTHONDONTWRITEBYTECODE=1 /Users/romain/Memory-os-ai/.venv/bin/python -m pytest -q -m 'not slow' v4/tests -p no:cacheprovider --basetemp=.local-qualification/mab-lru-claude-tests
ruff check --no-cache --config pyproject.toml src tests scripts
ruff check --no-cache --config pyproject.toml src tests v4/src v4/tests v4/bench scripts --select E9,F
```

Retour attendu à Codex : `PROVENANCE.json`, `report.json` (ou `FAILED.json`), nombre
de contextes/questions/références valides, familles/composantes d'origine attestées,
recoupements par corpus, groupes/questions éligibles et raisons GO/NO-GO.
**Aucune commande de campagne, lecteur, juge ou API dans cette livraison.**
