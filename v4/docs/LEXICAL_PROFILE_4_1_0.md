# Profil lexical 4.1.0 — contrat (phase 1a-cœur, étapes 1 à 3)

Statut : **contrat écrit avant tout code moteur**, à relire par Codex. Base `83ddead`
(`release/4.0.9-hygiene`, non qualifiée), branche `feature/4.1.0-lexical`. Plan de référence :
`PLAN_STRATEGIQUE_OEM_v3_1.md` §4 ; verdict Codex v2 du 21/11. Ce document ne modifie aucun
fichier sous `v4/src/` ni `src/`. Aucun banc n'a été exécuté pour l'écrire. Plateforme visée :
macOS ARM64, CPython 3.11. Chemins moteur relatifs à `v4/src/memory_os_v4/`.

## 1. Faits de code constatés sur `83ddead`

- `import memory_os_v4` ne charge aucun module ML (seulement `numpy`). L'ouverture charge `faiss`
  même avec l'encodeur `hash` : `memory.py` construit toujours `VectorIndex(...)`, dont le
  constructeur importe `faiss`. `torch`/`sentence_transformers` arrivent par
  `Embedder._ensure_model()` (`embed.py`) et, avec `device="auto"`, par `_resolve_device()`.
- `use_dense=False` n'est **pas** un profil lexical : l'encodeur est construit, chaque écriture
  encode (`_index_items` → `_encode_index_texts` → `vector_index.add`), le cache
  `embedding_cache` est rempli, `embed_model`/`dim`/`embed_compute_profile` sont écrits.
- `retrieval.py::HybridRetriever.search` : le chemin `lexical_only` (ordre BM25 brut) exige
  `use_bm25 and not use_dense and not use_entities and not use_recency`, sans expansion ni
  reranker. Les défauts `use_entities=True` et `use_recency=True` l'empêchent donc : avec
  `use_dense=False`, le classement réel est la **fusion `cc`** (min-max par signal, repli sur le
  rang si tous les scores sont égaux) de `bm25` (poids 1,5) et `entity` (0,5), multipliée par
  récence et importance, puis la garde C4 (`lexical_guard_k=3`).
- `sqlite_store.py::SQLiteStore.__init__` mute dès l'ouverture : `PRAGMA journal_mode=WAL`
  (création de `-wal`/`-shm`), migrations, `fsync` du dossier. Avant lui, `MemoryV4.__init__`
  fait `data_dir.mkdir` et `WriterLock` (crée `writer.lock`) ; après lui, il écrit
  `store_instance_id` par `save_meta(dict(self.store.meta))`.
- Les métadonnées SQLite sont le document JSON de la ligne `store_metadata(key='metadata')`,
  chargé dans `store.meta` ; toute écriture recopie le dictionnaire entier, donc une clé inconnue
  est conservée, y compris par une 4.0.x. `check_integrity()` exige seulement que chaque
  `value_json` de `store_metadata` soit un objet.
- `sqlite_embeddings.py` : clé `sha256(_json(profil) + b"\0" + texte)` ; le profil contient
  l'attestation `_compute_profile()`, donc `encoder_source_sha256` = SHA-256 d'`embed.py`.
  Lecture : `dim`, longueur `dim*4`, SHA-256 du blob, valeurs finies, sinon ré-encodage de la
  seule ligne fautive. Adoption historique seulement si `meta.version ∈ {4.0.5, 4.0.6}`,
  `cache_schema == 2`, encodeur attesté, `model_revision` connue et sans
  `unattested_revision_nonce`.
- `ops/qualified_runtime.py::check_requirements()` exige, pour chaque paquet du catalogue
  `resources/qualified_runtime_v1.json`, exactement `==version` dans les exigences du paquet.
  `pyproject.toml` épingle aujourd'hui toute la fermeture (ML comprise) en `==` dans
  `dependencies`.
- `readiness.py::engine_readiness` compare `vector_index.ids()` aux items vivants et renvoie
  `model_not_loaded` sans encodeur ; `capabilities.py::deployed` publie un bloc `encoding` ;
  `ops/quickstart.py::_ready()` exige le runtime d'encodage qualifié et la révision Granite du
  catalogue `provision_v1.json` ; `ops/doctor.py` ne connaît que « dérive ».

## 2. Identité et marqueur

| Élément | Valeur |
|---|---|
| Nom du profil | `lexical` (l'autre profil est `semantic`, défaut inchangé) |
| Version de la politique | `lex-4.1.0-v1` |
| Sélection | `Config.retrieval_profile` (`"semantic"` par défaut), `MEMORY_V4_RETRIEVAL_PROFILE`, fabrique `Config.lexical(**options)` |
| Marqueur | `store_metadata`, clé `'metadata'`, champ `retrieval_profile` = `{"name": "lexical", "policy": "lex-4.1.0-v1"}` |
| Schéma | 8, inchangé ; aucune migration, aucune table ni ligne nouvelle |

Règles :

1. Le marqueur est écrit **une seule fois**, à la création d'un store lexical neuf (fichier
   `store.sqlite3` absent), dans la même écriture que `store_instance_id`. Il n'est jamais
   réécrit, retiré ni modifié ensuite.
2. **Absence du champ = store sémantique.** Un store sémantique, neuf ou historique, ne reçoit
   jamais de marqueur : le chemin sémantique reste identique à l'octet près dans ses métadonnées.
3. Champ présent mais différent de la valeur exacte (autre politique, forme invalide, `null`) :
   refusé par les deux profils, jamais interprété.
4. Le profil lexical exige `store_backend="sqlite_s1"`. JSONL + lexical est refusé à la
   validation de configuration.

## 3. Politique de classement `lex-4.1.0-v1`

Définition normative : **le classement 4.0.9 du chemin `use_dense=False` aux valeurs par défaut,
sans aucun travail vectoriel.** Le test de parité compare les deux sur un même corpus.

- **Canaux** : `bm25` profil `scoped_literal` (mots Unicode littéraux de `item.text`,
  statistiques et top-N restreints au périmètre projet/type/validité *avant* sélection) et
  `entity` (`EntityIndex.search`, entités de la requête par `extract_entities`). Aucun canal
  `dense` : la clé n'existe pas dans `signal_hits`, rien n'est imputé à zéro.
  `signals["dense"]` reste `{"rank": None, "score": None}` pour la forme.
- **Fusion** : `fuse(..., method="cc")`, poids `bm25=1.5`, `entity=0.5`, min-max par signal,
  repli sur le rang pour un signal à scores tous égaux ; `candidates=100`,
  `n = max(candidates, top_k)`.
- **Récence et importance** : `final = fusion × (0.7 + 0.3 × récence) × (0.8 + 0.4 × importance)`,
  pénalité ×0,5 pour un item invalidé hors `at_time` valide. La formule est figée ; ses
  paramètres (`use_recency`, `recency_only_with_at_time`, demi-vies) restent des réglages
  d'opérateur, comme aujourd'hui.
- **Garde C4** : `lexical_guard_k=3`. Les 3 meilleures correspondances BM25 strictement positives
  (ordre `(-bm25, id)`) sont replacées en tête après toute autre étape ; le score public vaut
  `1/rang`.
- **Égalités** : ordre total `(-score, item.id)` partout (candidats, ancres, RRF). Aucune
  dépendance à `PYTHONHASHSEED` ni à l'ordre d'insertion.
- **Expansions** : aucune (`neighbor_expansion`, `entity_expansion`, `full_candidate_scoring`,
  `temporal_query_mode="auto"`, `facet_channel`, `lexical_first` refusés). Aucun reranker.
- **Multi-requêtes** : `search_multi` inchangé — une recherche par chaîne avec `top_k × 4`,
  RRF pondéré `k = multi_query_rrf_k = 20`, poids requête 1,0 / `queries` 1,0 / `hypotheses` 0,7,
  ordre `(-score_rrf, id)`.
- **Contexte et brief** : `get_context` appelle `mmr` avec un dictionnaire de vecteurs vide ; la
  diversité vaut 0 et l'ordre est celui de la pertinence (comportement actuel de `mmr`).
  `session_brief` n'utilise aucun vecteur.
- **Déterminisme** : pour un même contenu de store, une même requête et un même `at_time`, la
  liste `(id, score)` est identique d'un appel, d'une réouverture et d'un processus à l'autre.
  Sans `at_time`, la récence dépend de l'horloge ; le score public (`1/rang`) n'en dépend pas
  tant que l'ordre ne change pas.
- **Traçabilité** : chaque résultat porte `signals["ranking_policy"] = "lex-4.1.0-v1"`.
- **Limite connue, conservée** : les statistiques du canal `entity` (`df/n`, seuil 10 %) sont
  calculées sur tout le store, pas par projet ; seuls les candidats sont filtrés. Aucun contenu
  ne traverse les projets, mais un poids d'entité peut dépendre d'un autre projet.

Tout changement de cette section crée une nouvelle version de politique (`lex-…-v2`) et un
nouveau contrat ; un store marqué `v1` n'est pas rouvert sous `v2` sans procédure explicite.

## 4. Sélection, validation et options incompatibles

`validate_lexical_policy()` est déjà appelée en tête de `MemoryV4.__init__`, avant tout accès
disque : la validation de profil s'y ajoute. Les refus sont des `ValueError` portant un
attribut `code` stable.

| `code` | Cas |
|---|---|
| `profile_unknown` | `retrieval_profile` hors `{"semantic", "lexical"}` |
| `profile_option_conflict` | option incompatible avec `lex-4.1.0-v1` (ci-dessous) |
| `store_profile_mismatch` | store de l'autre profil, sans marqueur, ou marqueur invalide |
| `store_profile_contaminated` | marqueur lexical valide mais traces sémantiques (§5.4) |
| `store_profile_unverifiable` | sonde impossible sans écrire (un seul de `-wal`/`-shm`, fichier illisible) |
| `semantic_dependency_missing` | profil sémantique demandé, fermeture ML non installée (§8) |

En profil lexical, doivent valoir exactement :

- `store_backend="sqlite_s1"`, `use_dense=False` (explicite : pas de bascule silencieuse),
  `use_bm25=True`, `bm25_profile="scoped_literal"`, `use_entities=True`, `use_stemming=True`,
  `fusion="cc"`, `w_bm25=1.5`, `w_entity=0.5`, `candidates=100`, `lexical_guard_k=3`,
  `lexical_first=False`, `multi_query_rrf_k=20`, `index_keywords=True` ;
- à leur valeur par défaut (champs propres au sémantique ou aux expansions) : `embed_model`,
  `device`, `embed_dtype`, `embed_batch_size`, `embed_max_seq_length`, `hash_dim`,
  `batch_facts`, `sqlite_embedding_namespace`, `rerank_model`, `multi_vector`,
  `full_candidate_scoring`, `dense_norm`, `neighbor_expansion`, `entity_expansion`,
  `temporal_query_mode`, `facet_channel`, `w_dense`.

Restent libres : `data_dir`, `project_registry`, `maintenance_mode`, `write_mode`, `work_state`,
`block_char_limit`, `episode_ttl_days`, récence et demi-vies.

`Config.from_env()` : avec `MEMORY_V4_RETRIEVAL_PROFILE=lexical`, le défaut de `use_dense`
devient `False` si `MEMORY_V4_USE_DENSE` est absent (même mécanisme que `guard_default`
aujourd'hui) ; une variable explicite contradictoire (`MEMORY_V4_USE_DENSE=1`,
`MEMORY_V4_EMBED_MODEL`, `MEMORY_V4_RERANK_MODEL`, …) est refusée à la validation.
`Config(use_dense=False)` seul reste le profil sémantique actuel.

## 5. Refus avant toute ouverture mutante

### 5.1 Emplacement de la sonde

Fonction en lecture seule dans `sqlite_store.py` (nom proposé `probe_retrieval_profile`),
appelée par `MemoryV4.__init__` **après** la validation de configuration et
`validate_sqlite_target()` (qui ne fait que des `stat`), **avant** `data_dir.mkdir`,
`WriterLock` et `SQLiteStore(...)`. Un refus ne crée donc ni dossier, ni `writer.lock`, ni
`-wal`/`-shm`, et ne prend aucun bail d'écriture.

Mécanique :

- `store.sqlite3` absent → store neuf (aucune ouverture SQLite).
- ni `-wal` ni `-shm` → `sqlite3.connect("file:…?mode=ro&immutable=1", uri=True)` : aucune
  écriture possible, aucun fichier créé.
- `-wal` et `-shm` présents (écrivain vivant ou arrêt brutal) → `mode=ro` sans `immutable`, même
  règle que `ops/sqlite_backup.readonly` : le fichier principal et le WAL ne changent pas ; seul
  `-shm` (mémoire partagée transitoire) peut être touché par le lecteur.
- un seul des deux présent, ou erreur SQLite → `store_profile_unverifiable` en lexical.
- Lectures : `PRAGMA application_id`, `PRAGMA user_version`, le document `metadata`,
  l'existence d'au moins une ligne `embedding_cache`.

En profil **sémantique**, la sonde ne sert qu'à refuser un store marqué ; si elle ne peut pas
conclure, la main passe au chemin historique, qui applique ses propres refus, inchangés.

Seconde vérification, sous bail d'écriture : après `SQLiteStore(...)` et avant le premier
`save_meta`, le marqueur de `store.meta` est comparé à celui de la sonde ; un écart (course
entre deux processus) ferme le store et lève `store_profile_mismatch` sans écrire de ligne.

Les outils `ops` qui ouvrent le fichier sans `MemoryV4` (sauvegarde, restauration,
`storecheck`, migration JSONL→SQLite) sont indépendants du profil : ils copient ou vérifient,
et une migration JSONL produit un store sans marqueur, donc sémantique.

### 5.2 Matrice

| Store | Profil lexical | Profil sémantique |
|---|---|---|
| Neuf | créé, marqueur écrit | créé, sans marqueur (inchangé) |
| Sans marqueur (historique ou sémantique 4.1.0) | `store_profile_mismatch` | ouvert (inchangé) |
| Marqueur exact `lex-4.1.0-v1`, sans trace sémantique | ouvert | `store_profile_mismatch` |
| Marqueur d'une autre politique ou invalide | `store_profile_mismatch` | `store_profile_mismatch` |
| Marqueur exact + traces sémantiques | `store_profile_contaminated` | `store_profile_contaminated` |

### 5.3 Aucune conversion implicite

Ni sémantique → lexical (les vecteurs, le cache et l'attestation ne sont jamais abandonnés par
une simple ouverture), ni lexical → sémantique (aucun encodage de rattrapage). La 4.1.0 ne
fournit **aucun outil de conversion** ; changer de profil = créer un store neuf et y réécrire
les sources par les API publiques.

### 5.4 Retour arrière vers une 4.0.x

Ce que fait une 4.0.x sur un store lexical (lecture du code `83ddead`) : le schéma 8 et ses
sommes sont acceptés ; le champ inconnu est conservé ; `_rebuild_sql_indexes` → `_reembed_all`
encode **tous** les items avec l'encodeur configuré (Granite par défaut) et remplit
`embedding_cache` ; la première mutation écrit `embed_model`, `dim`, `embed_compute_profile` et
`version = 4.0.x`. Avec `work_state`/`defer_retrieval`, cela se produit à la première
recherche. La 4.0.x ne refuse rien et n'avertit pas : c'est une conversion silencieuse.

Ce que la 4.1.0 fait et documente :

- **Détection** : marqueur lexical valide et au moins une trace parmi `embed_model`, `dim`,
  `embed_compute_profile`, une ligne dans `embedding_cache`, ou `version` antérieure à 4.1.0
  → `store_profile_contaminated`, dans les deux profils, sans mutation.
- **Réparation** : aucune réparation automatique. Restaurer la dernière sauvegarde 4.1.0.
- **Avertissement documenté** (README, `OPERATIONS.md`, message du refus, `doctor`) : « un store
  lexical n'a pas de retour arrière 4.0.x ; revenir à une 4.0.x suppose de reprendre le store
  sémantique ou la sauvegarde d'avant 4.1.0 ». Sauvegarde obligatoire avant toute création de
  store lexical à côté d'un store existant.

## 6. Invariants du cache sémantique

1. `embed.py` reste **identique octet pour octet** : SHA-256
   `c2c335c468d7dedf7cff0ca2ce281c83d7c12fc9c65fd2c5b93adbbfcc5d60a7` (valeur à `83ddead`).
   Seuls ses appelants changent (imports paresseux dans `memory.py`/`retrieval.py`). Ce SHA
   entre dans `Embedder.encoding_profile()` puis dans chaque clé de cache : le modifier
   invaliderait tous les vecteurs de production.
2. Contenu d'`encoding_profile()` inchangé : `parameter_dtypes`, `parameter_devices`,
   `model_revision`, `runtime` (torch, sentence-transformers, transformers, numpy),
   `encoder_source_sha256`, `normalized`, `output_dtype`.
3. Formule de clé, profil de cache (`cache_schema: 2`, `namespace`, attestation) et codec
   `<f4` de longueur `dim × 4` inchangés.
4. Les anciennes lignes ne sont jamais supprimées ni réécrites (autre espace de noms, autre
   version) : elles servent au retour arrière.
5. Contrôles de corruption inchangés : dimension, longueur, SHA-256, finitude ; seule la ligne
   fautive est ré-encodée.
6. Règles d'adoption historiques inchangées (`4.0.5`/`4.0.6`, `cache_schema == 2`, attestation,
   révision connue). **Aucune adoption d'une révision de modèle inconnue** : sans révision, le
   nonce `unattested_revision_nonce` interdit toute réutilisation entre processus.
7. Démarrage à chaud : zéro ré-encodage du corpus, `hits == nombre d'items`, `misses == 0`.
8. Le profil lexical ne lit ni n'écrit jamais `embedding_cache`.

## 7. Découplage : ce que fait chaque opération en profil lexical

| Opération | Comportement lexical |
|---|---|
| Import, ouverture | aucun `torch`, `sentence_transformers`, `faiss`, `transformers` ; `embedder = None` ; pas de `VectorIndex` FAISS |
| Écriture (`add_episode`, `add_episodes_bulk`, `add_facts`, `add_document`) | BM25, entités, facettes, sources ; aucun encodage, aucun `chunk_map` vectoriel |
| `forget`, corrections | retrait des index lexicaux ; aucune opération vectorielle |
| Reconstruction, rollback SQL | `_rebuild_bm25_entities()` seul |
| `status` | `retrieval_profile` = marqueur, `vector_count = 0`, `embed_model`/`dim`/`encoding_profile` = `None`, `retrieval_ready = True` |
| `flush`, fermeture | sources SQL seulement ; `embed_model`/`dim` jamais écrits |
| `get_context`, `session_brief` | §3 (MMR sans vecteurs) |
| `consolidate` | balayages par cosinus et propositions de résumés par regroupement indisponibles : listes vides et `"similarity": "unavailable_in_lexical_profile"` ; `decay` inchangé. Jamais d'erreur d'import. |
| `ensure_retrieval` | sans effet (la récupération lexicale est prête dès l'ouverture) |

## 8. Empaquetage

- **Fermeture ML dans l'extra `[semantic]`** : `torch`, `sentence-transformers`,
  `transformers`, `faiss-cpu` et leurs dépendances propres. La liste exacte est calculée à
  l'étape 7 depuis `resources/qualified_runtime_closure_v1.json`, pas recopiée ici. `numpy`
  reste dans le cœur (importé par `embed.py`, `memory.py`, `retrieval.py`).
- **Métadonnées du paquet** (`pyproject.toml`) : **bornes compatibles** (`>=min,<majeure
  suivante`), cœur comme extras. Elles disent ce qui peut fonctionner, pas ce qui est qualifié.
- **Verrous exacts `==`** : uniquement dans les ressources de contraintes du runtime qualifié,
  une par profil, OS et Python :
  - `resources/qualified_runtime_constraints_v1.txt` et `qualified_runtime_v1.json`
    (`granite-macos-arm64-py311-v1`) : **conservés à l'octet près** (catalogue sémantique
    historique) ;
  - nouveaux `resources/qualified_runtime_lexical_v1.json`
    (`lexical-macos-arm64-py311-v1`, `encoding_runtime_keys: []`) et
    `resources/qualified_runtime_constraints_lexical_v1.txt`, sans aucun paquet ML.
  Installation qualifiée : `pip install -c <contraintes du profil> memory-os-ai[ops,brief]`
  (plus `[semantic]` pour le profil sémantique).
- **`qualified_runtime.py`** : `catalog`, `expected_versions`, `compare`, `inspect_current`
  reçoivent `profile="semantic"` par défaut (appelants actuels inchangés). `check_requirements`
  est scindé :
  - `check_requirements(requirements, *, profile, extras)` valide les **métadonnées** : chaque
    paquet du catalogue du profil a une exigence active dont l'ensemble de versions **contient**
    la version qualifiée ; aucun paquet de la fermeture ML n'est exigé sans marqueur d'extra ;
    tous le sont sous `extra == "semantic"` ;
  - `check_constraints(lines, *, profile, extras)` garde la règle actuelle : exactement
    `==version` pour chaque paquet du catalogue, appliquée au fichier de contraintes.
  `inspect_current` continue de comparer les versions **installées** aux versions exactes.
- Installation nue sans extra et profil sémantique demandé : refus
  `semantic_dependency_missing` nommant l'extra, avant toute ouverture de store. Le profil par
  défaut reste `semantic` tant que H2 n'a pas tranché.

## 9. Capacités et attestations

Trois états distincts, jamais confondus :

| État | Sens | Verdict |
|---|---|---|
| `absent_by_profile` | profil lexical : encodeur absent par choix | normal |
| `missing_dependency` | profil sémantique, paquet ML non installé | échec, correctif : installer `[semantic]` |
| `drift` | paquet installé, version différente du verrou du profil | échec, correctif : runtime qualifié |

- `qualified_runtime.compare` ajoute, par paquet en écart, `state: "missing" | "drift"`
  (`actual is None` → `missing`).
- `capabilities.py::deployed` : ajoute `retrieval_profile` (nom, politique) ; `runtime` inspecté
  avec le catalogue du profil ; en lexical, `encoding = {"state": "absent_by_profile",
  "model": None, "profile": None, "loaded": False}` ; `policies.ranking_policy`.
- `readiness.py::engine_readiness` : en lexical, pas de contrôle `model_not_loaded` ni de
  couverture vectorielle ; couverture BM25/entités/facettes contre les items vivants
  inchangée ; raison `ready`.
- `ops/quickstart.py::_ready()` : en lexical, exige marqueur de profil, `bm25_profile`,
  `lexical_guard_k`, `encoding.state == "absent_by_profile"` et le tokenizer prêt ; n'exige ni
  runtime d'encodage ni révision Granite. Chemin sémantique inchangé.
- `ops/provision.py` : `plan(resources=("cl100k",))` pour le profil lexical — pas de Granite.
- `doctor` : contrôle `encoder-runtime` à trois issues (PASS « absent par choix », FAIL
  « dépendance manquante », FAIL « dérive ») ; nouveau contrôle `store-profile` (marqueur,
  contamination).
- **Tokenizer du brief** : « sans modèle » ne dispense pas de `tiktoken` ni de la table
  `cl100k_base` attestée (`brief_tokens.status()`, `TABLE_SHA256`). L'extra `[brief]` et la
  table restent requis et vérifiés dans les deux profils.

Le code de `_ready()`, `provision` et `doctor` relève de la phase 1a-setup (GO séparé) ; les
étapes 4 à 7 livrent `capabilities.py`, `readiness.py` et `qualified_runtime.py`, dont ils
dépendent.

## 10. Mesure H2 (parité lexical / sémantique) — gelée ici, non exécutée

| Élément | Valeur gelée |
|---|---|
| Protocole | `v4/bench/protocols/v1.md`, famille « récupération de preuves » uniquement |
| Corpus | BEAM (`Mohammadta/BEAM`), révision `3205395e897e7318c7b094ef4e6047b9b82dbb03`, CC-BY-SA-4.0 |
| Split | **100K**, entier : aucune partition dev/test, aucun réglage autorisé |
| Fichier | `beam_100k.jsonl`, SHA-256 `613b85b9e5eca98b88de25bedb7490f1dbf61ae4c361d544452feea19bfa18a3` (parquet `c0519be2…a78ace`), cf. `BEAM_PROTOCOL_v1.md` |
| Effectif | 20 conversations, 5 732 messages, 400 questions exécutées, **355 questions scorées** (preuve annotée non vide) |
| Bras | lexical : profil `lexical`, `lex-4.1.0-v1` ; sémantique : profil par défaut du même commit (Granite, C4) ; mêmes réglages hors canal dense, récence désactivée dans les deux bras comme dans `beam-c4-retrieval-v2` ; stores SQLite neufs et isolés |
| Métrique | Recall@10 des tours annotés, sous le même budget que `BEAM_PROTOCOL_v1.md` |
| Statistique | delta apparié lexical − sémantique ; bootstrap par **conversation** entière, 2 000 réplicats, seed `20261010`, IC percentile 95 % |
| Seuil | Recall@10 lexical ≥ Recall@10 sémantique − 0,02 **et** borne basse de l'IC95 apparié ≥ −0,03 |

Règle de décision : seuil atteint (400/400 exécutées, 0 échec) → le lexical peut devenir le
profil par défaut recommandé ; sinon le sémantique reste recommandé, le lexical est proposé
comme profil « léger » et l'écart est publié. Dans tous les cas, aucune revendication de
rappel supérieur. Le seuil a été fixé en connaissant déjà BEAM (C4 0,462 contre BM25 0,445,
`BEAM_RESULT_V2.md`) : il ne vaut que sur ce corpus gelé, n'est pas un test indépendant et ne
dit rien des réponses générées ni des tâches d'agent. Un manifeste `h2-lexical-parity-v1` est
gelé par `evaluation_manifest.py` avant l'exécution ; toute modification du corpus, des bras ou
du seuil après observation invalide la mesure.

## 11. Critères de sortie G1a, vérifiables

Relevé de référence dans `.local-baseline/` (session parente, `83ddead`) :

- `v4_suite_83ddead.txt` : 12 échecs, 2 219 réussites ; liste exacte `v4_failures_83ddead.txt` ;
- `root_suite_83ddead.txt` : 35 échecs, 853 réussites, 16 erreurs ; liste exacte
  `root_failures_83ddead.txt` (51 lignes).

| Critère | Vérification |
|---|---|
| Non-régression | mêmes commandes que le relevé ; l'ensemble trié des identifiants `FAILED`/`ERROR` est **identique** aux deux listes ; 0 test ignoré. Les tests existants qui affirment les `==` de `pyproject.toml` (`test_qualified_runtime.py`, `test_distribution_d1a.py`) sont adaptés à l'étape 6–7 ; chaque adaptation est listée dans la revue. |
| Nouveaux tests | les trois fichiers du §12 verts (62 tests) |
| Aucun ML en lexical | `test_import_write_search_reopen_never_import_ml_modules` : interpréteur neuf, import bloqué ; aucune **tentative** d'import ni module `torch`, `sentence_transformers`, `faiss`, `transformers` dans `sys.modules` après import, écriture, recherche, réouverture |
| SHA d'`embed.py` | `shasum -a 256 v4/src/memory_os_v4/embed.py` = valeur du §6 ; `git diff 83ddead -- v4/src/memory_os_v4/embed.py` vide |
| Migrations | `schema_checksums()` égal aux 8 sommes épinglées ; `SCHEMA_VERSION == 8` |
| Cache sémantique chaud | `test_warm_semantic_cache_is_reused_without_reencoding_the_corpus` vert, inchangé depuis `83ddead` |
| Wheel lexical | étape 7 : wheel installé dans un environnement vierge avec les contraintes lexicales, sans paquet ML ; les tests lexicaux y passent |
| Copie de store qualifiée | ci-dessous |
| Revue | verdict Codex « VALIDÉ » |

**Copie de store qualifiée** : copie privée d'un store sémantique réel produite par l'outil de
sauvegarde (`ops/sqlite_backup`) puis restaurée dans un dossier hors production, jamais le
dossier vivant, jamais un store de qualification scellé. Opération manuelle sous GO de Romain,
hors suites de tests. Preuve attendue : (1) inventaire et SHA-256 de chaque fichier avant ;
(2) ouverture en profil lexical → `store_profile_mismatch` ; (3) inventaire et SHA-256 après :
identiques, aucun fichier créé ; (4) dans un autre dossier, store lexical neuf : écriture,
recherche, fermeture, réouverture, mêmes résultats.

## 12. Tests

Verts dès maintenant et à maintenir verts (16) — `v4/tests/test_semantic_invariants_410.py` :
SHA d'`embed.py`, contenu d'`encoding_profile()` sur un modèle factice, schéma 8 et sommes de
migration, clés de métadonnées d'un store sémantique et absence de marqueur, formule de clé et
codec float32, cache chaud, corruption d'une ligne et conservation des anciennes lignes, règles
d'adoption (8 cas), rejeu idempotent d'une requête explicite.

**Rouges jusqu'aux étapes 4 à 7** (46, sans `skip` ni `xfail`) :

- `v4/tests/test_lexical_profile.py` (33) :
  `test_import_write_search_reopen_never_import_ml_modules`,
  `test_default_profile_is_semantic_and_lexical_selection_is_explicit`,
  `test_options_incompatible_with_the_frozen_policy_are_refused_before_any_write` (25 cas),
  `test_unknown_profile_and_contradictory_environment_are_refused`,
  `test_fresh_lexical_store_carries_the_marker_and_no_semantic_state`,
  `test_reopen_preserves_identity_marker_items_and_ranking`,
  `test_ranking_is_deterministic_and_ties_break_on_item_id`,
  `test_policy_equals_the_frozen_reference_without_the_dense_channel`,
  `test_projects_are_isolated_in_results_and_in_corpus_statistics`.
- `v4/tests/test_lexical_store_guard.py` (13) :
  `test_semantic_store_is_refused_in_lexical_before_any_mutation`,
  `test_semantic_store_without_writer_lock_file_gets_no_new_file`,
  `test_historical_stores_without_marker_are_semantic_and_refused`,
  `test_semantic_store_with_a_live_wal_is_refused_without_touching_database_or_wal`,
  `test_lexical_store_is_refused_in_semantic_before_any_mutation`,
  `test_lexical_marker_with_another_policy_is_refused_unchanged`,
  `test_malformed_marker_is_never_interpreted` (5 cas),
  `test_lexical_store_contaminated_by_a_4_0_x_runtime_is_refused_by_both_profiles`,
  `test_refused_configuration_never_creates_a_directory_or_a_store`.

Aujourd'hui ils échouent tous sur `Config(retrieval_profile=…)` ou `Config.lexical`
(`TypeError`/`AttributeError`). Tant qu'ils sont rouges, la suite `v4/tests` compte 46 échecs
de plus que le relevé ; G1a se mesure une fois qu'ils sont verts.

## 13. Ordre d'implémentation, fichier par fichier (étapes 4 à 7, aucun code maintenant)

Étape 4 — sélection, sonde, marqueur :

1. `config.py` : `retrieval_profile`, constantes de profil et de politique, `Config.lexical`,
   erreur à `code`, validation de profil dans `validate_lexical_policy()`, `from_env`.
   `__init__.py` : version `4.1.0` dès cette étape (la règle de contamination du §5.4 compare
   `version` à 4.1.0) ; les tests existants qui épinglent `4.0.9` sont adaptés et listés.
2. `sqlite_store.py` : sonde en lecture seule (aucun changement de `SCHEMA`, `MIGRATIONS`,
   `SQLiteStore.__init__`).
3. `memory.py` (constructeur seulement) : appel de la sonde avant `mkdir`/`WriterLock`/
   `SQLiteStore`, matrice du §5.2, écriture du marqueur sur store neuf, seconde vérification.
   → `test_lexical_store_guard.py` et les tests de validation deviennent verts.

Étape 5 — découplage vectoriel :

4. `vector_index.py` : inchangé (import `faiss` déjà paresseux).
5. `retrieval.py` : `rerank`/annotations sans import ML au chargement, canal dense réellement
   optionnel (`embedder`/`vector_index` à `None`), `signals["ranking_policy"]`.
6. `memory.py` : `_index_items`, `_add_chunk_vectors`, `forget`, `_reconcile_indexes`,
   `_rebuild_sql_indexes`, `_reembed_all`, `_flush_sql_sources`, `status`, `flush`,
   `get_context`, `ensure_retrieval`, `_compute_profile`, `_consolidate`.
7. `consolidate.py` : chemins sans index vectoriel (§7).
8. `server.py` : `memory_status` (bloc `embedding_cache`) et démarrage `--warm` en lexical.
9. `__init__.py` : les variables d'environnement BLAS restent (sans effet sans ML).
10. `embed.py` : **aucune modification**.
    → `test_lexical_profile.py` devient vert.

Étape 6 — capacités et runtime qualifié :

11. `resources/qualified_runtime_lexical_v1.json`,
    `resources/qualified_runtime_constraints_lexical_v1.txt` (nouveaux) ; ressources
    sémantiques intactes.
12. `ops/qualified_runtime.py` : paramètre `profile`, `check_requirements`/`check_constraints`,
    état `missing`/`drift` ; tests `test_qualified_runtime.py` adaptés d'abord.
13. `capabilities.py`, puis `readiness.py`.

Étape 7 — empaquetage :

14. `pyproject.toml` : extra `[semantic]`, bornes compatibles ; `test_distribution_*` adaptés.
15. Construction du wheel, installation lexicale en environnement vierge, exécution des tests
    lexicaux ; vérification que l'installation sémantique contrainte reproduit le catalogue
    `granite-macos-arm64-py311-v1`.

Puis étape 8 : revue adverse Codex, mesure de G1a.

## 14. Questions ouvertes pour la revue Codex

1. **Retour arrière** : le contrat retient « détection + avertissement documenté ». Variante
   possible sans toucher au schéma : rendre le store lexical illisible par une 4.0.x en
   exploitant `check_integrity()` (une ligne `store_metadata` dont la valeur n'est pas un
   objet). Elle donnerait un vrai refus 4.0.x, mais casse volontairement un invariant
   d'intégrité et les outils de sauvegarde 4.0.x. Écartée par défaut ; à trancher.
2. **Sévérité des options** : `w_bm25`, `w_entity`, `candidates`, `use_stemming` figés ;
   récence et demi-vies libres (BEAM impose la récence désactivée). Trop strict, pas assez ?
3. **WAL vivant** : la sonde `mode=ro` peut modifier `-shm`. Alternative : refuser toute sonde
   quand `-wal` existe (`store_profile_unverifiable`), au prix d'un store lexical non rouvrable
   après arrêt brutal sans procédure.
4. **Contamination par `version`** : faut-il traiter `version < 4.1.0` seule (sans vecteurs)
   comme contamination, ou seulement les traces d'encodage ?
5. **H2** : BEAM 100K entier sans partition, déjà observé pour C4/BM25 ; les bras passent de
   JSONL (campagne v2) à SQLite. Acceptable comme corpus gelé, ou faut-il LongMemEval-S ?
6. **`consolidate` en lexical** : listes vides + indicateur, ou refus explicite ?
7. **Périmètre 1a-cœur / 1a-setup** : `_ready()`, `provision`, `doctor` spécifiés ici mais
   codés en 1a-setup ; confirmer.
8. **Statistiques d'entités globales** (§3) : conservées telles quelles pour la parité avec la
   référence ; à cadrer par projet dans une politique `v2` ?
