# Profil lexical 4.1.0 — contrat (phase 1a-cœur, étapes 1 à 3)

Statut : **contrat v2.1, écrit avant tout code moteur**. La v1 (`7bb9a89`) a reçu le verdict Codex
« VALIDÉ AVEC AMENDEMENTS » du 21/11 (`codex_verdict_contract_410_v1.md`) ; la v2 (`6f1f40b`)
intègre chaque amendement et consigne les dix arbitrages (§14). Le verdict Codex sur la v2
(`codex_verdict_contract_410_v2.md`, « VALIDÉ AVEC AMENDEMENTS ») demandait trois corrections,
intégrées ici (v2.1, §15) : DDL initial d'`embedding_cache` autorisé (§6.8), mode rétrospectif
implémenté **avant** G1a (§13), tests de priorité des refus et version `10.0.0` (§12). Base `83ddead`
(`release/4.0.9-hygiene`, non qualifiée), branche `feature/4.1.0-lexical`. Plan de référence :
`PLAN_STRATEGIQUE_OEM_v3_1.md` §4. Ce document ne modifie aucun fichier sous `v4/src/` ni
`src/`. Aucun banc n'a été exécuté pour l'écrire ; les sorties de référence du §3 ont été
produites en exécutant le moteur **non modifié** de `83ddead` sur un corpus de test. Plateforme
visée : macOS ARM64, CPython 3.11. Chemins moteur relatifs à `v4/src/memory_os_v4/`.

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
- `MemoryV4.__init__` appelle `ProjectRegistry.load(config.project_registry)` (`memory.py:126`)
  **avant** `validate_lexical_policy()` : si un registre est configuré, son fichier est ouvert en
  lecture (`O_RDONLY|O_NOFOLLOW|O_NONBLOCK`), `fstat`, lecture d'au plus 1 Mio + 1 octet.
- `SQLiteStore.save_meta` (`sqlite_store.py:330`) réécrit **tout** le document JSON à chaque
  appel ; `_flush_sql_sources` l'appelle à chaque mutation avec `version` = version du paquet.
- Garde C4 (`retrieval.py:829-836`) : les ancres sont triées **après** la pénalité ×0,5 des items
  invalidés. Plancher adaptatif de demi-vie (`retrieval.py:761`, `_corpus_span_days`) : étendue
  temporelle de **tous** les items non supprimés du store, tous projets confondus.
- Canal `entity` (`retrieval.py:695,701`, `entities.py:137`) : `EntityIndex.search` calcule
  `df/n`, le seuil de 10 % et son top-N sur **tout** le store ; le filtre projet/type/validité
  n'est appliqué qu'ensuite.
- `consolidate.py` : `dedup_sweep` (égalité de texte normalisé, `:24`) et `supersede_sweep`
  (`replacement_sweep`, `:52`) sont **déterministes et non vectoriels** ; ils reçoivent
  `vector_index` sans l'utiliser. Seul `summary_proposals` (regroupement par cosinus) lit les
  vecteurs.
- `get_context` (`memory.py:1046`) passe à `mmr` les vecteurs des items trouvés : en profil
  sémantique, même avec `use_dense=False`, le contexte est diversifié par les vecteurs.
- `text.py::lexical_tokens` (`:72`) : sous `scoped_literal`, `use_stemming` n'est pas lu.
- `bench/evaluation_manifest.py` (`:33,169,181`) exige des partitions `dev` et `test` non vides
  et `selection_rule == "dev_only_then_freeze"`.
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

1. **Valeur immuable, pas écriture unique.** La valeur du champ est fixée à la création d'un
   store lexical neuf (fichier `store.sqlite3` absent) : le premier `SQLiteStore.save_meta` écrit
   ensemble, dans un seul document, exactement `retrieval_profile`, `store_instance_id` et
   `version` (= version du paquet). Ensuite chaque `save_meta` réécrit physiquement tout le
   document JSON (c'est son fonctionnement) : la garantie porte sur la **valeur**, qu'aucun
   chemin 4.1.0 ne modifie, ne retire ni ne remplace, et non sur le nombre d'écritures.
2. **Absence du champ = store sémantique.** Un store sémantique, neuf ou historique, ne reçoit
   jamais de marqueur : le chemin sémantique reste identique à l'octet près dans ses métadonnées
   (y compris l'absence de `version` avant la première mutation).
3. Champ présent mais différent de la valeur exacte (autre politique, forme invalide, `null`) :
   refusé par les deux profils, jamais interprété.
4. Le profil lexical exige `store_backend="sqlite_s1"`. JSONL + lexical est refusé à la
   validation de configuration.
5. Un store lexical propre porte toujours une `version` de la forme `X.Y.Z` ≥ `4.1.0` (§5.4).
   Toute version de paquet qui écrit un store lexical est donc une version finale `X.Y.Z`, sans
   suffixe.

## 3. Politique de classement `lex-4.1.0-v1`

Définition normative : **le classement 4.0.9 du chemin `use_dense=False` aux valeurs par défaut,
sans aucun travail vectoriel.** La référence n'est pas un second chemin du futur code : ce sont
les **sorties du moteur `83ddead` non modifié**, figées en données littérales
(`v4/tests/lexical_frozen_83ddead.py`, identiques sous `PYTHONHASHSEED` 0, 1 et 4242, horloge
de recherche fixée). `test_lexical_reference_410.py` prouve que le moteur de base les reproduit ;
`test_lexical_profile.py` exige que le profil lexical les reproduise. Régénérer ces données
depuis un commit ultérieur est interdit : un écart est un changement de politique.

- **Canaux** : `bm25` profil `scoped_literal` (mots Unicode littéraux de `item.text`,
  statistiques et top-N restreints au périmètre projet/type/validité *avant* sélection) et
  `entity` (`EntityIndex.search`, entités de la requête par `extract_entities`). Aucun canal
  `dense` : la clé n'existe pas dans `signal_hits`, rien n'est imputé à zéro.
  `signals["dense"]` reste `{"rank": None, "score": None}` pour la forme.
- **Fusion** : `fuse(..., method="cc")`, poids `bm25=1.5`, `entity=0.5`, min-max par signal,
  repli sur le rang pour un signal à scores tous égaux ; `candidates=100`,
  `n = max(candidates, top_k)`.
- **Récence et importance** : `final = fusion × (0.7 + 0.3 × récence) × (0.8 + 0.4 × importance)`,
  puis ×0,5 pour un item invalidé (`invalidated_by` renseigné), sauf si `at_time` est fourni et
  que l'item était valide à cette date. La formule est figée ; ses paramètres restent des
  réglages d'opérateur, avec trois régimes exacts :
  1. `use_recency=False` : récence = 1 ;
  2. défaut (`use_recency=True`, `recency_only_with_at_time=False`) : demi-vie du type d'item,
     âge mesuré à `at_time` s'il est fourni, sinon à l'horloge ;
  3. `recency_only_with_at_time=True` : sans `at_time`, récence = 1 ; avec `at_time`,
     demi-vie = `max(demi-vie du type, 0,25 × étendue)` où l'étendue est
     `max(event_time) − min(event_time)` sur **tous les items non supprimés du store, tous
     projets et tous types confondus** (valeur mise en cache tant que le nombre d'items ne
     change pas). Un item d'un autre projet peut donc modifier la récence — jamais le contenu —
     des résultats d'un projet (`test_adaptive_half_life_floor_uses_the_global_time_span`).
- **Garde C4** : `lexical_guard_k=3`. Les scores BM25 bruts du périmètre sont d'abord multipliés
  par 0,5 pour les items invalidés (même exception `at_time` que ci-dessus) ; les ancres sont les
  `min(3, top_k)` items de score **pénalisé** strictement positif, triés `(-score pénalisé, id)`.
  Elles sont replacées en tête après toute autre étape, le reste suit dans l'ordre de fusion ;
  le score public vaut `1/rang` et `signals["bm25"]` conserve le rang et le score **non
  pénalisés**. Un item invalidé peut ainsi être le premier rang BM25 et la troisième ancre
  (`test_c4_anchors_are_sorted_after_the_invalidated_penalty`).
- **Égalités** : ordre total `(-score, item.id)` partout (candidats, ancres, RRF). Aucune
  dépendance à `PYTHONHASHSEED` ni à l'ordre d'insertion.
- **Expansions** : aucune (`neighbor_expansion`, `entity_expansion`, `full_candidate_scoring`,
  `temporal_query_mode="auto"`, `facet_channel`, `lexical_first` refusés). Aucun reranker.
- **Multi-requêtes** : `search_multi` inchangé — une recherche par chaîne avec `top_k × 4`,
  RRF pondéré `k = multi_query_rrf_k = 20`, poids requête 1,0 / `queries` 1,0 / `hypotheses` 0,7,
  ordre `(-score_rrf, id)`.
- **Contexte et brief** : `get_context` appelle `mmr` avec un dictionnaire de vecteurs **vide** ;
  la diversité vaut 0 et l'ordre est celui de la pertinence. Ce contexte **n'est pas** celui du
  profil sémantique, qui diversifie avec les vecteurs stockés : la parité est testée contre MMR
  à vecteurs vides (données figées `context_empty_mmr`), et le contexte sémantique fait l'objet
  d'une assertion distincte (`context_semantic`). `session_brief` n'utilise aucun vecteur.
- **Déterminisme** : pour un même contenu de store, une même configuration effective, une même
  requête et une même horloge (ou un même `at_time`), la liste `(id, score)` et les signaux sont
  identiques d'un appel, d'une réouverture et d'un processus à l'autre, quel que soit
  `PYTHONHASHSEED` (`test_ranking_is_identical_across_processes_and_hash_seeds`). Sans `at_time`
  en régime 2, la récence dépend de l'horloge ; le score public (`1/rang`) n'en dépend pas tant
  que l'ordre ne change pas.
- **Traçabilité** : chaque résultat porte `signals["ranking_policy"] = "lex-4.1.0-v1"`. Toute
  comparaison ou mesure enregistre la **configuration effective** (récence, demi-vies,
  `recency_only_with_at_time`, `index_keywords`) à côté de la politique.
- **Limites connues, conservées en v1** (canal `entity`, global au store) :
  1. *statistiques* : `df/n` et le seuil de 10 % sont calculés sur tout le store ; un document
     d'un autre projet peut activer ou éteindre le poids d'une entité
     (`test_entity_threshold_depends_on_other_projects`) ;
  2. *compétition top-N avant filtrage* : `EntityIndex.search` retient les 100 meilleurs
     candidats du store entier, puis seulement le filtre de projet s'applique ; à scores égaux
     (ordre par id), 100 items d'autres projets peuvent évincer du canal `entity` un item du
     projet interrogé, qui reste trouvé par BM25
     (`test_entity_top_100_is_saturated_by_other_projects_before_filtering`).
  Aucun contenu ne traverse les projets ; seuls des poids, donc l'ordre de la queue, peuvent
  dépendre d'un autre projet. Ce que le périmètre garantit, et ce que les tests affirment, se
  limite aux rangs et scores BM25 bruts et aux ancres C4. L'isolation des statistiques et des
  candidats d'entités relève d'une politique `v2`.

Tout changement de cette section crée une nouvelle version de politique (`lex-…-v2`) et un
nouveau contrat ; un store marqué `v1` n'est pas rouvert sous `v2` sans procédure explicite.

## 4. Sélection, validation et options incompatibles

### 4.1 Ordre exact dans `MemoryV4.__init__`, et ce qui est touché avant la sonde

La validation **ne précède pas tout accès disque** : l'ordre réel, conservé, est le suivant.

| # | Étape | Accès disque |
|---|---|---|
| 1 | `Config.from_env()` si aucune configuration n'est passée | aucun (environnement) |
| 2 | `ProjectRegistry.load` | si un registre est configuré : ouverture en lecture seule, `fstat`, lecture ≤ 1 Mio + 1 octet de **ce fichier** ; rien n'est créé ni écrit ; une erreur de registre est levée avant la validation de profil (ordre inchangé) |
| 3 | `validate_lexical_policy()` + validation de profil ; profil lexical : version du paquet finale `X.Y.Z` ≥ 4.1.0, sinon `profile_version_not_final` | aucun |
| 4 | politiques de maintenance et d'écriture | aucun |
| 5 | profil sémantique : présence de la fermeture ML (§8) | `importlib.util.find_spec` : lecture des répertoires d'import, aucun import, aucune écriture. **Jamais exécutée en profil lexical** |
| 6 | contrôle du backend, `validate_sqlite_target` | `resolve` et `stat` du dossier cible, de `items.jsonl`, `blocks.jsonl`, `requests.jsonl`, `meta.json`, `store.sqlite3` et de ses `-wal`/`-shm` |
| 7 | **sonde n° 1** (§5.1) | `stat` de `store.sqlite3` et de ses `-wal`/`-shm` ; lecture seule `immutable=1` de `store.sqlite3` **uniquement** si aucun des deux n'existe ; paire présente → aucune ouverture, décision reportée à l'étape 9 |
| 8 | `data_dir.mkdir`, `WriterLock` | création éventuelle du dossier et de `writer.lock` |
| 9 | **sonde n° 2 sous bail** (§5.1), qui fait autorité | lecture seule (`immutable=1` sans annexe, `mode=ro` avec la paire : WAL lu) |
| 10 | `SQLiteStore(...)` puis premier `save_meta` | première étape mutante |

Avant la sonde n° 1, rien n'est donc créé ni écrit ; le dossier du store n'est lu que par des
`stat`. Les refus des étapes 3, 5, 7 et 9 sont des `ValueError` portant un attribut `code`
stable ; un refus aux étapes 3 à 7 ne crée ni dossier ni fichier. Pour un store dont la paire
`-wal`/`-shm` est présente à l'étape 7, le refus de profil est prononcé à l'étape 9 : le seul
effet possible sur le système de fichiers avant ce refus est celui de `WriterLock` (§5.1).

| `code` | Cas |
|---|---|
| `profile_unknown` | `retrieval_profile` hors `{"semantic", "lexical"}` |
| `profile_option_conflict` | option incompatible avec `lex-4.1.0-v1` (ci-dessous) |
| `profile_version_not_final` | profil lexical demandé alors que la version du paquet en cours d'exécution n'est pas une version finale `X.Y.Z` ≥ 4.1.0 (`.devN`, `rcN`, suffixe local…) : la version estampillée rendrait le store refusé (`store_profile_contaminated`) à toute réouverture. Refus avant tout accès au store, création comme réouverture ; le profil sémantique n'est pas concerné |
| `semantic_dependency_missing` | profil sémantique demandé, fermeture ML non installée (§8) |
| `store_profile_unverifiable` | fichier existant dont le profil est indéterminable, ou store changé entre les deux sondes (§5.1) — **dans les deux profils** |
| `store_profile_mismatch` | marqueur invalide, ou store de l'autre profil |
| `store_profile_contaminated` | marqueur lexical valide mais traces sémantiques (§5.4) |

Priorité quand plusieurs cas s'appliquent : `store_profile_unverifiable`, puis marqueur
invalide (`store_profile_mismatch`), puis contamination, puis comparaison au profil demandé.

### 4.2 Options

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

`use_stemming` est **inerte** sous `scoped_literal` (`text.py:72`) : son gel à `True` est une
convention d'identité de configuration, pas un réglage de classement.

Restent libres : `data_dir`, `project_registry`, `maintenance_mode`, `write_mode`, `work_state`,
`block_char_limit`, `episode_ttl_days`, `use_recency`, `recency_only_with_at_time` et les
demi-vies. Ces options libres changent des scores : elles font partie de la configuration
effective à enregistrer (§3).

`Config.from_env()` : avec `MEMORY_V4_RETRIEVAL_PROFILE=lexical`, le défaut de `use_dense`
devient `False` si `MEMORY_V4_USE_DENSE` est absent (même mécanisme que `guard_default`
aujourd'hui) ; une variable explicite contradictoire (`MEMORY_V4_USE_DENSE=1`,
`MEMORY_V4_EMBED_MODEL`, `MEMORY_V4_RERANK_MODEL`, …) est refusée à la validation.
`Config(use_dense=False)` seul reste le profil sémantique actuel. Le profil n'est **jamais**
déduit des dépendances installées (§8).

## 5. Refus avant toute ouverture mutante

### 5.1 La sonde, exécutée deux fois

Fonction en lecture seule dans `sqlite_store.py` (nom proposé `probe_retrieval_profile`). Elle
renvoie un constat : `état` (neuf / existant), marqueur lu, verdict de contamination,
`store_instance_id`, `application_id`, `user_version`.

Mécanique :

- `store.sqlite3` absent et aucun fichier `-wal`/`-shm` → store neuf (aucune ouverture SQLite).
- `store.sqlite3` absent mais `-wal` ou `-shm` présent → **indéterminable**.
- ni `-wal` ni `-shm` → `sqlite3.connect("file:…?mode=ro&immutable=1", uri=True)` : aucune
  écriture possible, aucun fichier créé (en mode `immutable`, SQLite ne crée aucune annexe et
  ne pose aucun verrou). `immutable` est réservé à cet état sans WAL, dans les deux sondes.
- `-wal` **et** `-shm` présents (écrivain vivant, copie à chaud ou arrêt brutal) : le contenu
  du WAL doit être lu, donc par `mode=ro` sans `immutable` (même règle que
  `ops/sqlite_backup.readonly` ; un marqueur présent seulement dans le WAL est vu). Or une
  ouverture `mode=ro` d'une base WAL dont les annexes viennent d'être supprimées par la
  fermeture normale du dernier écrivain les **recrée** si le dossier est inscriptible. Cette
  ouverture n'est donc faite **que sous bail d'écriture** (sonde n° 2), où aucun moteur
  écrivain ne peut fermer concurremment : la paire est stable. Sans bail (sonde n° 1) :
  **aucune ouverture**, constat `deferred_to_lease` (ni refus ni accord). Sous bail, le fichier
  principal et le WAL ne changent pas d'un octet ; **la modification transitoire de `-shm` par
  le lecteur est explicitement exclue de la garantie d'immutabilité**.
- un seul des deux présent → **indéterminable** (aucune ouverture, aucun fichier touché).
- erreur SQLite à l'ouverture ou à la lecture (pas une base, E/S, verrou), ou ligne `metadata`
  présente dont la valeur n'est pas un objet JSON → **indéterminable**.
- Lectures : `PRAGMA application_id`, `PRAGMA user_version`, le document `metadata`,
  l'existence d'au moins une ligne `embedding_cache`. Table `store_metadata` ou ligne `metadata`
  absente (schéma ancien, fichier créé mais jamais marqué) = pas de marqueur = sémantique.

**Indéterminable → `store_profile_unverifiable` dans les DEUX profils.** Le chemin sémantique
historique n'est plus autorisé à ouvrir un fichier existant dont la sonde n'a pas pu lire le
profil : il pourrait ouvrir en écriture un store lexical non identifié. Pour un store dont le
profil est déterminé comme sémantique, le chemin historique et ses propres refus (schéma
inconnu, sommes de migration) restent inchangés.

**Sonde n° 1**, avant `data_dir.mkdir` et `WriterLock` (étape 7 du §4.1). Elle n'appelle
jamais `sqlite3.connect` sans `immutable=1` : elle ne peut donc **créer aucune annexe**, quelle
que soit la concurrence. Trois issues :

- aucune annexe → ouverture `immutable=1`, constat complet ; un refus ne crée ni dossier, ni
  `writer.lock`, ni `-wal`/`-shm`, et ne prend aucun bail ;
- paire incomplète ou annexe orpheline → `store_profile_unverifiable` sur la seule foi des
  `stat`, sans aucune ouverture, mêmes garanties ;
- paire complète → **`deferred_to_lease`** : rien n'est ouvert, rien n'est refusé ; la décision
  est prise par la sonde n° 2.

Les `stat` et l'ouverture ne sont pas atomiques ; les deux courses possibles sont sans effet
sur le disque :

- le dernier écrivain ferme (annexes supprimées) juste après l'observation de la paire : la
  sonde n° 1 n'ouvre rien (constat périmé `deferred_to_lease`), la sonde n° 2 voit l'état réel ;
- un écrivain apparaît entre les `stat` et l'ouverture `immutable=1` : la sonde n° 1 peut
  seulement lire un fichier principal périmé (ou obtenir une erreur de lecture →
  `store_profile_unverifiable`, sans effet) ; elle ne crée ni ne modifie rien. Un constat
  périmé accepté à tort est rattrapé par la sonde n° 2, qui fait autorité ; un refus prononcé
  sur un constat périmé est sûr (rien n'a été touché, réessayer).

**Sonde n° 2, complète, sous bail d'écriture et AVANT la construction de `SQLiteStore`**
(étape 9). Le constructeur n'est pas un point de contrôle acceptable : il active le WAL, migre
et initialise le journal des changements (`sqlite_store.py:187,197,220`, `changes.py:22`) avant
qu'on puisse relire quoi que ce soit. La sonde n° 2 refait toutes les lectures, puis :

1. si son constat appelle à lui seul un refus (indéterminable, marqueur invalide, contaminé,
   autre profil), ce refus est levé avec son code ;
2. sinon, si la sonde n° 1 avait rendu un constat complet, il lui est comparé sur l'état
   (neuf/existant), le marqueur, le verdict de contamination — une contamination conserve le
   marqueur, le marqueur seul ne suffit donc pas —, `store_instance_id`, `application_id` et
   `user_version` ; tout écart → `store_profile_unverifiable` (le store a changé pendant
   l'ouverture ; réessayer) ;
3. si la sonde n° 1 était `deferred_to_lease`, il n'y a rien à comparer : **le constat de la
   sonde n° 2 décide seul**. Seule exigence héritée de la sonde n° 1 : elle avait vu un
   `store.sqlite3` existant ; un constat « neuf » sous bail (le fichier a disparu) →
   `store_profile_unverifiable`, rien n'est créé à sa place.

Sous bail, la sonde n° 2 applique la même mécanique de `stat` : sans annexe `immutable=1`,
paire complète `mode=ro`, paire incomplète ou orpheline → indéterminable sans ouverture.

Le bail exclut tout autre moteur écrivain ; la sonde n° 2 fait donc autorité contre une course
entre deux processus `MemoryV4`, y compris la fermeture du dernier écrivain. **Hors contrat** :
tout écrivain qui ne prend pas le bail (`sqlite3` en ligne de commande, restauration, copie
manuelle). S'il ferme la base entre les `stat` et l'ouverture `mode=ro` de la sonde n° 2, des
annexes peuvent être recréées ; le bail est la seule protection et il a été violé.

**Le bail couvre toute la vie de la connexion SQLite du moteur, sur tous les chemins de
fermeture** (§17). Un moteur ne possède qu'une connexion SQLite, celle de `SQLiteStore`
(`SQLiteRequests`, `SQLiteBlocks`, `SQLiteEmbeddings`, le journal des changements et l'état de
travail la partagent). Elle est fermée **avant** le relâchement du bail par `close()`, par un
échec du constructeur, et par la finalisation automatique (ramasse-miettes, sortie de
l'interpréteur). Le moteur suivant ne peut donc pas observer une paire `-wal`/`-shm` qu'une
connexion du moteur précédent supprimerait après coup. Le bail n'est relâché **qu'une fois la
fermeture effective constatée** : si elle échoue, l'exclusion est conservée (§18).

**Conséquence pour un écrivain vivant.** Un moteur ouvert tient le bail et sa paire
`-wal`/`-shm` existe : une seconde ouverture concurrente reçoit `deferred_to_lease` à la
sonde n° 1, puis le refus habituel de `WriterLock` (`WriterConflictError`, « already has an
active writer ») — et non plus un code de profil —, sans avoir ouvert SQLite.

**Ce que fait `WriterLock`** (`writer_lock.py`, inchangé) : `open("a+b")` de
`<dossier>/writer.lock` — le fichier est créé vide (0 octet) s'il n'existait pas ; s'il existe
il n'est ni écrit, ni tronqué, ni daté (POSIX) — puis `flock(LOCK_EX | LOCK_NB)`. Le fichier
n'est jamais supprimé, y compris au relâchement. `data_dir.mkdir` ne crée le dossier que s'il
n'existait pas (impossible après un `deferred_to_lease`, qui suppose `store.sqlite3` présent).

**Garantie, énoncée précisément.** Refus à la sonde n° 1 (store sans annexe, ou paire
incomplète/orpheline) : rien n'est créé, pas même `writer.lock`. Refus à la sonde n° 2 — c'est
**toujours** le cas d'un store dont la paire `-wal`/`-shm` était présente à la sonde n° 1, et
celui d'une course entre les sondes — : le seul effet possible sur le système de fichiers
avant le refus est celui de `WriterLock` ci-dessus (création d'un `writer.lock` vide s'il
manquait, et du dossier s'il n'existait pas) ; le bail est relâché avant de lever ;
`store.sqlite3` et ses `-wal`/`-shm` ne sont ni créés ni modifiés (à l'exception `-shm`
ci-dessus quand la paire existait sous bail). Aucune des deux sondes ne crée d'annexe.

**Création interrompue avant marquage.** Entre la création du fichier par `SQLiteStore` et le
premier `save_meta`, un arrêt laisse un fichier au schéma 8, sans ligne `metadata` et sans
item : par la règle 2 du §2 c'est un store **sémantique**. Le profil lexical le refuse
(`store_profile_mismatch`) sans le toucher ; il n'est jamais adopté ni marqué après coup.
Remède documenté : vérifier qu'il est vide, supprimer ce dossier, recréer.

Les outils `ops` qui ouvrent le fichier sans `MemoryV4` (sauvegarde, restauration,
`storecheck`, migration JSONL→SQLite) sont indépendants du profil : ils copient ou vérifient,
et une migration JSONL produit un store sans marqueur, donc sémantique.

### 5.2 Matrice

| Store | Profil lexical | Profil sémantique |
|---|---|---|
| Neuf | créé, marqueur écrit | créé, sans marqueur (inchangé) |
| Sans marqueur (historique, sémantique 4.1.0, création interrompue) | `store_profile_mismatch` | ouvert (inchangé) |
| Marqueur exact `lex-4.1.0-v1`, sans trace sémantique | ouvert | `store_profile_mismatch` |
| Marqueur d'une autre politique ou invalide | `store_profile_mismatch` | `store_profile_mismatch` |
| Marqueur exact + traces sémantiques | `store_profile_contaminated` | `store_profile_contaminated` |
| Profil indéterminable (fichier illisible, métadonnées non objet, paire `-wal`/`-shm` incomplète, fichier annexe orphelin) | `store_profile_unverifiable` | `store_profile_unverifiable` |
| Store changé entre les deux sondes | code du nouveau constat, sinon `store_profile_unverifiable` | idem |
| Paire `-wal`/`-shm` complète à la sonde n° 1 | décision de la sonde n° 2 seule, après le bail (lignes ci-dessus) | idem |

### 5.3 Aucune conversion implicite

Ni sémantique → lexical (les vecteurs, le cache et l'attestation ne sont jamais abandonnés par
une simple ouverture), ni lexical → sémantique (aucun encodage de rattrapage). La 4.1.0 ne
fournit **aucun outil de conversion** ; changer de profil = créer un store neuf et y réécrire
les sources par les API publiques.

### 5.4 Retour arrière vers une 4.0.x : détection et avertissement

Ce que fait une 4.0.x sur un store lexical (lecture du code `83ddead`) : le schéma 8 et ses
sommes sont acceptés ; le champ inconnu est conservé ; `_rebuild_sql_indexes` → `_reembed_all`
encode **tous** les items avec l'encodeur configuré (Granite par défaut) et remplit
`embedding_cache` ; la première mutation écrit `embed_model`, `dim`, `embed_compute_profile` et
`version = 4.0.x`. Avec `work_state`/`defer_retrieval`, cela se produit à la première
recherche. La 4.0.x ne refuse rien et n'avertit pas : c'est une conversion silencieuse.

Ce que la 4.1.0 fait et documente — **détection et avertissement, rien d'autre** :

- **Détection** : marqueur lexical valide et **au moins une** des traces suivantes, chacune
  suffisante à elle seule → `store_profile_contaminated`, dans les deux profils, sans mutation :
  1. clé `embed_model` présente dans les métadonnées ;
  2. clé `dim` présente ;
  3. clé `embed_compute_profile` présente ;
  4. au moins une ligne dans `embedding_cache` (seule lecture de cette table par le profil
     lexical, faite par la sonde) ;
  5. `version` ne prouvant pas une écriture 4.1.0 ou ultérieure.
- **Règle de version (refus conservateur).** `version` est valide si et seulement si c'est une
  chaîne de la forme exacte `^\d+\.\d+\.\d+$` ; la comparaison se fait sur le triplet
  d'**entiers** `(X, Y, Z) ≥ (4, 1, 0)`, jamais sur le texte (`4.10.0` > `4.1.0`). Une version
  **absente**, non textuelle ou **malformée** (`"4.1"`, `"4.1.0.dev1"`, `"banana"`, `410`) est
  refusée comme une version antérieure : un store lexical propre porte une version dès sa
  création (§2). Une version seule inférieure à 4.1.0 signale le passage d'un runtime
  incompatible ; elle ne prouve pas qu'un encodage a eu lieu, d'où le terme *conservateur*.
- **Réparation** : aucune réparation automatique. Restaurer la dernière sauvegarde 4.1.0.
- **Avertissement documenté** (README, `OPERATIONS.md`, message du refus, `doctor`) : « un store
  lexical n'a pas de retour arrière 4.0.x ; revenir à une 4.0.x suppose de reprendre le store
  sémantique ou la sauvegarde d'avant 4.1.0 ». Sauvegarde obligatoire avant toute création de
  store lexical à côté d'un store existant.
- **Écarté** : rendre le store illisible par une 4.0.x en cassant `check_integrity()`. Ce
  sabotage d'un invariant d'intégrité casserait les outils de sauvegarde 4.0.x et ne produirait
  qu'un refus tardif, après des opérations mutantes.

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
7. Démarrage à chaud : zéro ré-encodage du corpus, `hits == nombre d'items`, `misses == 0` —
   **à quatre conditions** : même corpus (mêmes textes d'index), même profil de cache (modèle,
   dimension, `max_seq_length`, `batch_size`, périphérique, `cache_schema`, `namespace`,
   attestation complète dont `encoder_source_sha256` et les versions du runtime), **révision du
   modèle connue**, lignes de cache valides (dimension, longueur, SHA-256, finitude). Une
   révision inconnue impose le nonce `unattested_revision_nonce` : chaque processus ré-encode
   alors tout le corpus, par conception ; ce n'est pas une régression de cette promesse.
8. Le profil lexical n'écrit jamais de vecteur dans `embedding_cache` et n'en lit jamais les
   données, **à la seule exception de la sonde de contamination du §5.1** (`existe-t-il au moins
   une ligne ?`, sur sa propre connexion en lecture seule). Sur la connexion du moteur lexical :
   - **autorisé** : le DDL initial du schéma **inchangé** (`SCHEMA_CACHE`, migration 3,
     `CREATE TABLE embedding_cache`), exécuté par `SQLiteStore.__init__` à la création d'un
     store neuf comme dans le profil sémantique — le schéma 8 et ses sommes restent identiques,
     la table existe donc, vide, dans tout store lexical ;
   - **interdit** : toute lecture de données de cette table (`SELECT`) hors de la sonde, et
     toute écriture de vecteur (`INSERT`, `UPDATE`, `DELETE`). Une fois le store ouvert, la
     connexion du moteur n'émet plus aucune instruction SQL nommant `embedding_cache`
     (vérifié par trace SQL dans `test_lexical_profile.py`).

## 7. Découplage : ce que fait chaque opération en profil lexical

| Opération | Comportement lexical |
|---|---|
| Import, ouverture | aucun `torch`, `sentence_transformers`, `faiss`, `transformers`, pas même une tentative d'import ni un `find_spec` ; `embedder = None` ; aucune instance de `VectorIndex`, `Embedder` ou `HashEmbedder` n'est construite |
| Écriture (`add_episode`, `add_episodes_bulk`, `add_facts`, `add_document`) | BM25, entités, facettes, sources ; aucun encodage, aucun `chunk_map` vectoriel |
| `forget`, corrections | retrait des index lexicaux ; aucune opération vectorielle |
| Reconstruction, rollback SQL | `_rebuild_bm25_entities()` seul : après une faute SQL, sources, BM25, entités et facettes reviennent à l'état d'avant, sans ré-encodage ni accès vectoriel |
| Corrections, reçus, CAS de blocs | inchangés (remplacement explicite, rejeu idempotent, conflit d'ETag) ; aucun point de faute `embedding_cache`, aucun accès vectoriel |
| `status` | `retrieval_profile` = marqueur, `vector_count = 0`, `embed_model`/`dim`/`encoding_profile` = `None`, `retrieval_ready = True` |
| `flush`, fermeture | sources SQL seulement ; `embed_model`/`dim` jamais écrits |
| `get_context`, `session_brief` | §3 (MMR sans vecteurs) |
| `consolidate` | **résultat partiel explicite.** `dedup_sweep` (égalité de texte normalisé), `supersede_sweep` (remplacements explicites) et `decay` sont déterministes, non vectoriels et **conservés à l'identique** (mêmes clés, mêmes valeurs qu'en sémantique). Seules les propositions de regroupement exigent des vecteurs : `summary_proposals == []` et clé supplémentaire `summary_proposals_status == "unavailable_in_lexical_profile"`. Le résultat sémantique est inchangé (pas de nouvelle clé). Jamais d'erreur d'import, jamais de refus. |
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
    la version qualifiée ; en profil lexical, aucun paquet de la fermeture ML n'est une exigence
    active ; en profil sémantique, tous le sont. `extras` sélectionne, comme aujourd'hui, les
    `extra_versions` du catalogue (`ops`, `brief`) ; le catalogue sémantique, inchangé, garde
    les paquets ML dans ses `versions`, et l'extra `semantic` est implicitement actif pour
    l'évaluation des marqueurs `Requires-Dist` de ce profil ;
  - `check_constraints(lines, *, profile, extras)` garde la règle exacte actuelle, appliquée au
    fichier de contraintes : exactement `==version` pour chaque paquet du catalogue du profil,
    et **aucune ligne hors de ce catalogue** (le fichier est le verrou : les contraintes
    sémantiques ne valident pas le profil lexical, ni l'inverse). Résultat
    `{"ok": …, "mismatches": {paquet: …}}`.
  `inspect_current` continue de comparer les versions **installées** aux versions exactes.
- **Installation nue** (`pip install memory-os-ai`, sans extra) : le profil par défaut **reste
  `semantic`**. Ouvrir un moteur sémantique sans la fermeture ML lève
  `semantic_dependency_missing` (`ValueError`), dont le message nomme
  `memory-os-ai[semantic]`, à l'étape 5 du §4.1 : avant la sonde, avant toute création de
  dossier ou de fichier, quel que soit le backend. Détection par
  `importlib.util.find_spec` sur les modules requis, sans import : `faiss` toujours (même avec
  l'encodeur `hash`, même avec `use_dense=False`, même avec `work_state`) ; `torch`,
  `sentence_transformers` et `transformers` sauf si `embed_model == "hash"`.
  **Aucun repli automatique** : le profil n'est jamais choisi d'après les dépendances
  installées ; le lexical se sélectionne explicitement (§2). Le profil lexical n'appelle jamais
  `find_spec` sur ces modules.

## 9. Capacités et attestations

Trois états distincts, jamais confondus :

| État | Où il apparaît | Sens | Verdict |
|---|---|---|---|
| `absent_by_profile` | `capabilities.deployed()["encoding"]["state"]` | profil lexical : encodeur absent par choix | normal |
| `missing` | écart de `qualified_runtime.compare`, état global `missing_dependency` | paquet du catalogue du profil non installé | échec, correctif : installer `[semantic]` (ou le paquet manquant) |
| `drift` | écart de `qualified_runtime.compare`, état global `drift` | paquet installé, version différente du verrou du profil | échec, correctif : runtime qualifié |

- `qualified_runtime.compare` : chaque écart devient
  `{"expected": …, "actual": …, "state": "missing" | "drift"}` (`actual is None` → `missing`) ;
  le résultat porte `state` = `"qualified"` sans écart, `"missing_dependency"` dès qu'un paquet
  manque (les dérives restent listées), sinon `"drift"`. Avec le catalogue lexical, l'absence
  comme la présence d'un paquet ML n'est ni exigée ni inspectée.
- `capabilities.py::deployed` : ajoute `retrieval_profile` (nom, politique) ; `runtime` inspecté
  avec le catalogue du profil ; en lexical, `encoding = {"state": "absent_by_profile",
  "model": None, "profile": None, "loaded": False}` ; `policies.ranking_policy`. Un moteur
  sémantique sans ML ne s'ouvre pas (§8) : l'état `missing` n'est donc visible que par
  l'inspection du runtime et par `doctor`, jamais par un moteur ouvert.
- `readiness.py::engine_readiness` : en lexical, pas de contrôle `model_not_loaded`, pas de
  couverture vectorielle, jamais `ready_work_only` (la récupération est prête dès l'ouverture) ;
  couverture BM25/entités/facettes contre les items vivants inchangée : un index lexical
  incomplet donne `indexes_not_ready`.
- **Tokenizer du brief** : « sans modèle » ne dispense pas de `tiktoken` ni de la table
  `cl100k_base` attestée (`brief_tokens.status()`, `TABLE_SHA256`). L'extra `[brief]` et la
  table restent requis et vérifiés dans les deux profils.

**Périmètre (confirmé).** Phase 1a-cœur, étapes 4 à 7 : `capabilities.py`, `readiness.py`,
`ops/qualified_runtime.py` et les ressources du runtime. Phase 1a-setup (GO séparé) :

- `ops/quickstart.py::_ready()` : en lexical, exige marqueur de profil, `bm25_profile`,
  `lexical_guard_k`, `encoding.state == "absent_by_profile"` et le tokenizer prêt ; n'exige ni
  runtime d'encodage ni révision Granite. Chemin sémantique inchangé.
- `ops/provision.py` : `plan(resources=("cl100k",))` pour le profil lexical — pas de Granite.
- `doctor` : contrôle `encoder-runtime` à trois issues (PASS « absent par choix », FAIL
  « dépendance manquante », FAIL « dérive ») ; nouveau contrôle `store-profile` (marqueur,
  contamination).

**Aucun parcours setup (`quickstart`, `provision`, `doctor`) n'est déclaré compatible avec le
profil lexical avant cette adaptation.**

## 10. Mesure H2 (parité lexical / sémantique) — gelée ici, non exécutée

| Élément | Valeur gelée |
|---|---|
| Protocole | `v4/bench/protocols/v1.md`, famille « récupération de preuves » uniquement |
| Corpus | BEAM (`Mohammadta/BEAM`), révision `3205395e897e7318c7b094ef4e6047b9b82dbb03`, CC-BY-SA-4.0 — **accepté pour cette décision limitée** ; LongMemEval-S n'est pas requis ici |
| Jeu évalué | **100K**, entier, en une seule partition `evaluation` : aucune partition dev/test, aucun réglage autorisé |
| Fichier | `beam_100k.jsonl`, SHA-256 `613b85b9e5eca98b88de25bedb7490f1dbf61ae4c361d544452feea19bfa18a3` (parquet `c0519be2…a78ace`), cf. `BEAM_PROTOCOL_v1.md` |
| Effectif | 20 conversations, 5 732 messages, 400 questions exécutées, **355 questions scorées** (preuve annotée non vide) |
| Bras | lexical : profil `lexical`, `lex-4.1.0-v1` ; sémantique : profil par défaut du même commit (Granite, C4) ; **stores SQLite (`sqlite_s1`) neufs et isolés dans les deux bras** ; mêmes réglages hors canal dense, récence désactivée dans les deux bras comme dans `beam-c4-retrieval-v2` ; configuration effective complète de chaque bras enregistrée dans le manifeste |
| Métrique | Recall@10 des tours annotés, sous le même budget que `BEAM_PROTOCOL_v1.md` |
| Statistique | delta apparié lexical − sémantique ; bootstrap par **conversation** entière, 2 000 réplicats, seed `20261010`, IC percentile 95 % |
| Seuil | Recall@10 lexical ≥ Recall@10 sémantique − 0,02 **et** borne basse de l'IC95 apparié ≥ −0,03 |

Règle de décision : seuil atteint (400/400 exécutées, 0 échec) → le lexical peut devenir le
profil par défaut recommandé ; sinon le sémantique reste recommandé, le lexical est proposé
comme profil « léger » et l'écart est publié. Dans tous les cas, aucune revendication de
rappel supérieur.

**Mesure rétrospective, sans indépendance statistique.** Le seuil a été fixé en connaissant
déjà BEAM (C4 0,462 contre BM25 0,445, `BEAM_RESULT_V2.md`) et les bras passent de JSONL
(campagne v2) à SQLite : la mesure ne vaut que sur ce corpus gelé, n'est **pas** un test
indépendant, ne revendique aucune indépendance statistique et ne dit rien des réponses générées
ni des tâches d'agent.

**Gel par `bench/evaluation_manifest.py` en mode rétrospectif.** L'outil actuel exige des
partitions `dev` et `test` non vides et `selection_rule == "dev_only_then_freeze"` : H2 ne peut
pas le satisfaire honnêtement, et un faux découpage dev/test est interdit. Avant H2, l'outil
reçoit un mode explicite, tests d'abord (`test_evaluation_manifest_retrospective_410.py`) :

- `selection_rule == "retrospective_no_selection"` ;
- bloc `retrospective` obligatoire : `independence_claim: false`, `tuning_allowed: false`,
  `prior_exposure` = liste non vide de phrases décrivant ce qui a déjà été observé ;
- fichier de partitions : **une seule** clé `evaluation`, non vide, identifiants uniques ; les
  clés `dev` et `test` y sont refusées, même vides ; `evaluation_count` remplace `test_count`
  (dont la présence est refusée) ;
- tout le reste du manifeste (source, verrou d'environnement, matériel, budgets, systèmes à
  configuration résolue, données hachées) est validé comme aujourd'hui ;
- le mode `dev_only_then_freeze` garde exactement son sens : il refuse un bloc `retrospective`
  et une partition `evaluation`.

Le manifeste `h2-lexical-parity-v1` est gelé dans ce mode avant l'exécution ; toute
modification du corpus, des bras ou du seuil après observation invalide la mesure.

## 11. Critères de sortie G1a, vérifiables

Relevé de référence dans `.local-baseline/` (session parente, `83ddead`) :

- `v4_suite_83ddead.txt` : 12 échecs, 2 219 réussites ; liste exacte `v4_failures_83ddead.txt` ;
- `root_suite_83ddead.txt` : 35 échecs, 853 réussites, 16 erreurs ; liste exacte
  `root_failures_83ddead.txt` (51 lignes).

| Critère | Vérification |
|---|---|
| Non-régression | mêmes commandes que le relevé ; l'ensemble trié des identifiants `FAILED`/`ERROR` est **identique** aux deux listes, **et la cause de chaque échec est vérifiée inchangée** ; 0 test ignoré. Les tests existants à adapter (§11.1) ne figurent pas parmi les échecs de référence : l'identité des listes reste atteignable. |
| Nouveaux tests | les sept fichiers du §12 verts (148 tests), **mode rétrospectif du manifeste compris** (§13, étape 7 bis) |
| Aucun ML en lexical | `test_import_write_search_reopen_never_import_ml_modules` : interpréteur neuf, import bloqué ; aucune **tentative** d'import ni module `torch`, `sentence_transformers`, `faiss`, `transformers` dans `sys.modules` après import, écriture, recherche, réouverture |
| SHA d'`embed.py` | `shasum -a 256 v4/src/memory_os_v4/embed.py` = valeur du §6 ; `git diff 83ddead -- v4/src/memory_os_v4/embed.py` vide |
| Migrations | `schema_checksums()` égal aux 8 sommes épinglées ; `SCHEMA_VERSION == 8` |
| Cache sémantique chaud | `test_warm_semantic_cache_is_reused_without_reencoding_the_corpus` vert, inchangé depuis `83ddead` (conditions du §6.7 réunies par la fixture) |
| Wheel lexical | §11.2 |
| Copie de store qualifiée | §11.3 |
| Revue | verdict Codex « VALIDÉ » |

### 11.1 Tests existants à adapter (étapes 4, 6 et 7), et comment

Règle : **conserver les contrôles exacts sur les contraintes, mettre les identités de version en
cohérence, ne rien affaiblir.** Chaque adaptation est listée dans la revue avec sa cause.

| Test | Aujourd'hui | Adaptation |
|---|---|---|
| `test_qualified_runtime.py:201` `test_project_pins_match_shipped_qualified_profile` | exige `==` exact dans `pyproject.toml` | le contrôle `==` exact passe sur le **fichier de contraintes** via `check_constraints` (profil sémantique, extras `ops`/`brief`) ; `pyproject.toml` est vérifié par `check_requirements` (bornes contenant la version qualifiée, ML sous `[semantic]`) ; les deux assertions sur le catalogue (`torch == 2.10.0`, `sentence-transformers == 5.2.3`) restent |
| `test_qualified_runtime.py:236` `test_missing_package_is_explicit_and_unsafe_loose_metadata_rejected` | `torch>=2.10,<2.11` refusé par `check_requirements` | le même jeu de lignes est refusé par `check_constraints` (une contrainte lâche reste interdite) ; la partie « paquet manquant » vérifie en plus `state == "missing"` |
| `test_distribution_d1a.py:249` (METADATA du wheel) | `check_requirements(Requires-Dist)` avec `==` | `check_requirements(Requires-Dist, profile="lexical")` et `check_requirements(Requires-Dist, profile="semantic")` : bornes contenant la version qualifiée, aucun paquet ML actif sans l'extra |
| `test_distribution_d1a.py:329` `test_extras_and_v3_failure_are_explicit` | `"sentence-transformers==5.2.3" in dependencies` | `sentence-transformers` absent de `dependencies`, présent dans `optional-dependencies["semantic"]` avec une borne contenant 5.2.3 ; le verrou `==5.2.3` est affirmé sur le fichier de contraintes sémantique |
| `test_distribution_d1a.py:39` `test_release_versions_and_packaged_notes_are_consistent` | toutes les identités de version égales | **inchangé, non affaibli** : `pyproject.toml`, `v4/pyproject.toml`, `server.json`, `memory_os_ai.__version__` et la note de version passent ensemble à `4.1.0` |
| tests épinglant `4.0.9` (`tests/test_release_409.py`, `test_schema7_genuine_upgrade.py`, `tests/test_z_coverage_boost.py`) | à examiner au passage de version | un test qui prouve une règle sur les octets de la 4.0.9 reste tel quel ; une identité de version courante est mise en cohérence ; aucune assertion retirée |

### 11.2 Sous-ensemble exécutable sans ML, et preuve du wheel

Tous les tests 4.1.0 ne tournent pas sans ML : construire un moteur sémantique, même avec
l'encodeur `hash`, exige FAISS.

| Fichier | Sans ML | Remarque |
|---|---|---|
| `test_lexical_profile.py` | **oui** | référence = données littérales figées ; aucun moteur sémantique |
| `test_lexical_capabilities_410.py` | **oui** | versions installées injectées |
| `lexical_fixture_410.py`, `lexical_frozen_83ddead.py` | **oui** | modules d'appui, requis par les deux fichiers ci-dessus |
| `test_lexical_packaging_410.py` | sans ML, mais **checkout source** | lit `pyproject.toml` |
| `test_evaluation_manifest_retrospective_410.py` | sans ML, mais **checkout source** | importe `bench/` |
| `test_lexical_reference_410.py` | non | moteur sémantique `hash` → FAISS |
| `test_lexical_store_guard.py` | non | construit des stores sémantiques `hash` |
| `test_semantic_invariants_410.py` | non | profil sémantique |

**Preuve du wheel lexical (étape 7).** Environnement vierge ; `pip install -c
qualified_runtime_constraints_lexical_v1.txt <wheel>[ops,brief]` plus `pytest` ; aucun paquet
ML installé. Les deux fichiers « oui » et leurs deux modules d'appui sont copiés dans un
dossier **hors du dépôt** et exécutés depuis ce dossier, **sans `PYTHONPATH` ni `-c`/`rootdir`
pointant vers les sources**. La preuve consigne `memory_os_v4.__file__` (sous `site-packages`
de l'environnement), l'absence de `torch`, `sentence_transformers`, `transformers` et `faiss`
(`importlib.util.find_spec(...) is None`) et le résultat des tests. Un passage obtenu en
important `v4/src` ne compte pas : c'est le **paquet installé** qui est prouvé. Les
sous-processus de ces tests réutilisent le répertoire du paquet importé par le test, donc le
même paquet installé.

### 11.3 Copie de store qualifiée

Copie privée d'un store sémantique réel produite par l'outil de sauvegarde
(`ops/sqlite_backup`) puis restaurée dans un dossier hors production, jamais le dossier vivant,
jamais un store de qualification scellé. Opération manuelle sous GO de Romain, hors suites de
tests. Preuve attendue : (1) inventaire et SHA-256 de chaque fichier avant ; (2) ouverture en
profil lexical → `store_profile_mismatch` ; (3) inventaire et SHA-256 après : identiques, aucun
fichier créé ; (4) dans un autre dossier, store lexical neuf : écriture, recherche, fermeture,
réouverture, mêmes résultats.

## 12. Tests

148 tests dans sept fichiers (142 au contrat v2, plus 6 ajoutés par l'amendement Codex du
21/11 dans `test_lexical_store_guard.py` : la version `10.0.0` et cinq cas cumulés de priorité
des refus) ; avant les étapes 4 à 7, **32 verts** (comportement de `83ddead`, à maintenir) et
**116 rouges par construction**, sans `skip` ni `xfail`. Les sept fichiers, mode rétrospectif
compris, sont verts avant G1a (§13). Modules d'appui : `lexical_fixture_410.py` (corpus,
scénarios partagés entre le bras de référence et le bras lexical, rejeu interprocessus,
détecteur d'accès vectoriel) et `lexical_frozen_83ddead.py` (sorties figées de `83ddead`).

| Fichier | Verts | Rouges | Objet |
|---|---|---|---|
| `test_semantic_invariants_410.py` | 20 | 0 | invariants sémantiques |
| `test_lexical_reference_410.py` | 9 | 0 | le moteur de base reproduit les données figées |
| `test_lexical_profile.py` | 0 | 44 | profil lexical, sans ML |
| `test_lexical_store_guard.py` | 0 | 48 | sonde, refus, courses, priorité des refus |
| `test_lexical_capabilities_410.py` | 0 | 5 | capacités, readiness, états du runtime |
| `test_lexical_packaging_410.py` | 1 | 4 | métadonnées et contraintes par profil |
| `test_evaluation_manifest_retrospective_410.py` | 2 | 15 | mode rétrospectif H2 |

**Verts.**

- `test_semantic_invariants_410.py` (20) : SHA d'`embed.py`, contenu d'`encoding_profile()` sur
  un modèle factice, schéma 8 et sommes de migration, clés de métadonnées d'un store sémantique
  et absence de marqueur, formule de clé et codec float32, cache chaud, corruption d'une ligne
  et conservation des anciennes lignes, règles d'adoption (8 cas), rejeu idempotent ; plus, en
  v2, trois corruptions distinctes (somme, valeurs non finies, dimension) et le rejeu après
  réouverture. **Portée limitée, assumée** : les huit cas d'adoption n'exercent que
  `_legacy_profile`, le ré-encodage utilise l'encodeur `hash`. Ces gardes **ne remplacent pas**
  `test_sqlite_cache_release.py` (adoption, atomicité, corruption) ni les gardes CAS et
  corrections existantes (`test_work_state.py`, `test_scope_and_fact_updates.py`), qui restent
  dans le critère de non-régression.
- `test_lexical_reference_410.py` (9) : reproduction des données figées dans le processus et
  dans des processus neufs sous trois `PYTHONHASHSEED` ; contexte sémantique distinct du contexte
  à vecteurs vides ; pénalité C4 ; plancher de demi-vie global ; seuil d'entités global ;
  saturation du top-100 interprojets.
- `test_lexical_packaging_410.py` (1) : ressources sémantiques identiques à l'octet près.
- `test_evaluation_manifest_retrospective_410.py` (2) : le mode de sélection exige toujours
  `dev` et `test` ; une règle inconnue est refusée.

**Rouges.**

- `test_lexical_profile.py` (44) : aucun import ML (interpréteur neuf) ; sélection par défaut et
  explicite ; 25 options incompatibles ; profil inconnu et environnement contradictoire ; store
  neuf marqué ; réouverture ; déterminisme et égalités ; **reproduction des sorties figées de
  `83ddead`** (rangs, signaux, fusion, récence, ancres, multi-requêtes, `at_time`, contexte
  comparé à MMR à vecteurs vides) ; rejeu interprocessus sous trois `PYTHONHASHSEED`, horloge
  et configuration fixes ; pénalité C4 ; plancher de demi-vie ; seuil d'entités et saturation
  du top-100 ; isolation des projets limitée à ce que BM25 garantit (rangs et scores BM25
  bruts, ancres — pas l'ordre final) ; rollback après faute SQL à quatre points ; corrections,
  reçus et CAS de blocs ; consolidation partielle ; ces trois derniers avec détecteur d'accès
  vectoriel et trace SQL ; refus sémantique sans ML avant toute création de fichier, sans repli.
- `test_lexical_store_guard.py` (48) : store sémantique refusé en lexical sans mutation ; copie
  sans `writer.lock` ; stores historiques ; WAL vivant ; store lexical refusé en sémantique ;
  autre politique ; marqueur malformé (5) ; contamination groupée ; configuration refusée ;
  **profil indéterminable dans les deux profils** (4) ; **WAL ou SHM isolé** (2) ; fichier
  annexe orphelin (2) ; **marqueur présent seulement dans le WAL** ; **contamination par chaque
  trace séparée** (10, dont version absente et malformée) ; comparaison de version par triplet
  d'entiers (5, dont `10.0.0`, que la comparaison textuelle classerait avant `4.1.0`) ;
  **priorité des refus sur cas cumulés** (5 : indéterminable > marqueur invalide > contamination
  > comparaison au profil demandé) ; store neuf estampillé ; **courses** entre les deux sondes — contamination à
  marqueur inchangé, identité remplacée, changement de profil, store apparu ; **création
  interrompue avant marquage**.
- `test_lexical_capabilities_410.py` (5) : catalogues par profil ; dépendance manquante contre
  dérive ; absence de ML normale en lexical ; encodeur absent par choix ; readiness sans vecteur
  et index lexical incomplet.
- `test_lexical_packaging_410.py` (4) : verrous exacts dans les contraintes de chaque profil ;
  métadonnées à bornes compatibles et ML sous `[semantic]` ; identité de version `4.1.0` (2).
- `test_evaluation_manifest_retrospective_410.py` (15) : manifeste rétrospectif accepté ; ses
  huit défauts de franchise refusés ; cinq découpages factices ou vides refusés ; le mode de
  sélection refuse les champs rétrospectifs.

Aujourd'hui les rouges échouent sur `Config(retrieval_profile=…)`/`Config.lexical`
(`TypeError`/`AttributeError`), sur les API de runtime par profil, sur les ressources lexicales
absentes ou sur le mode de manifeste absent. Tant qu'ils sont rouges, la suite `v4/tests`
compte 116 échecs de plus que le relevé ; G1a se mesure une fois qu'ils sont verts.

## 13. Ordre d'implémentation, fichier par fichier (étapes 4 à 7, aucun code maintenant)

Étape 4 — sélection, sonde, marqueur :

1. `config.py` : `retrieval_profile`, constantes de profil et de politique, `Config.lexical`,
   erreur à `code`, validation de profil dans `validate_lexical_policy()`, `from_env`.
   `__init__.py` : version `4.1.0` dès cette étape (la règle de contamination du §5.4 compare
   `version` à 4.1.0) ; identités de version mises en cohérence (§11.1).
2. `sqlite_store.py` : sonde en lecture seule (aucun changement de `SCHEMA`, `MIGRATIONS`,
   `SQLiteStore.__init__`).
3. `memory.py` (constructeur seulement) : contrôle de dépendance sémantique, sonde n° 1 avant
   `mkdir`/`WriterLock`, **sonde n° 2 sous bail avant `SQLiteStore(...)`**, matrice du §5.2,
   écriture conjointe marqueur + `store_instance_id` + `version` sur store lexical neuf.
   → `test_lexical_store_guard.py` et les tests de validation deviennent verts.

Étape 5 — découplage vectoriel :

4. `vector_index.py` : inchangé (import `faiss` déjà paresseux).
5. `retrieval.py` : `rerank`/annotations sans import ML au chargement, canal dense réellement
   optionnel (`embedder`/`vector_index` à `None`), `signals["ranking_policy"]`.
6. `memory.py` : `_index_items`, `_add_chunk_vectors`, `forget`, `_reconcile_indexes`,
   `_rebuild_sql_indexes`, `_reembed_all`, `_flush_sql_sources`, `status`, `flush`,
   `get_context`, `ensure_retrieval`, `_compute_profile`, `_consolidate`.
7. `consolidate.py` : `summary_proposals` seul devient indisponible ; les deux balayages et
   `decay` ne changent pas (§7).
8. `server.py` : `memory_status` (bloc `embedding_cache`) et démarrage `--warm` en lexical.
9. `__init__.py` : les variables d'environnement BLAS restent (sans effet sans ML).
10. `embed.py` : **aucune modification**.
    → `test_lexical_profile.py` devient vert.

Étape 6 — capacités et runtime qualifié :

11. `resources/qualified_runtime_lexical_v1.json`,
    `resources/qualified_runtime_constraints_lexical_v1.txt` (nouveaux) ; ressources
    sémantiques intactes.
12. `ops/qualified_runtime.py` : paramètre `profile`, `check_requirements`/`check_constraints`,
    états `missing`/`drift` ; tests `test_qualified_runtime.py` adaptés d'abord (§11.1).
13. `capabilities.py`, puis `readiness.py`.
    → `test_lexical_capabilities_410.py` devient vert.

Étape 7 — empaquetage :

14. `pyproject.toml` : extra `[semantic]`, bornes compatibles ; `test_distribution_*` adaptés
    (§11.1). → `test_lexical_packaging_410.py` devient vert.
15. Construction du wheel, preuve du §11.2 sur le paquet **installé** ; vérification que
    l'installation sémantique contrainte reproduit le catalogue `granite-macos-arm64-py311-v1`.

Étape 7 bis — mode rétrospectif du manifeste (**avant G1a**, amendement Codex du 21/11) :

16. `bench/evaluation_manifest.py` reçoit le mode rétrospectif du §10, sans changer le
    comportement ni les messages du mode `dev_only_then_freeze`
    (→ `test_evaluation_manifest_retrospective_410.py` vert). Le critère « sept fichiers
    verts » du §11 inclut donc ce fichier ; la mesure H2 elle-même reste postérieure à G1a.

Puis étape 8 : revue adverse Codex, mesure de G1a.

## 14. Décisions arbitrées par Codex (verdict du 21/11 sur le contrat v1)

| # | Question | Décision | Où |
|---|---|---|---|
| 1 | Retour arrière 4.0.x | **Détection + avertissement.** Le sabotage d'intégrité est rejeté : il casserait les outils 4.0.x et ne donnerait qu'un refus tardif, après des opérations mutantes. | §5.4 |
| 2 | Sévérité des options | **Gel accepté.** Récence et demi-vies restent configurables, la configuration effective étant enregistrée pour toute comparaison ; `use_stemming` est inerte sous `scoped_literal`, son gel est une convention. | §3, §4.2 |
| 3 | Sonde et WAL | **`mode=ro` accepté avec `-wal` et `-shm` existants**, la modification transitoire de `-shm` étant exclue de la garantie ; **paire incomplète → refus dans les deux profils** ; `immutable` réservé à un état stable sans WAL. | §5.1 |
| 4 | Contamination par `version` seule | **Refus conservateur accepté.** Elle signale un passage incompatible sans prouver un encodage ; versions absentes ou malformées refusées ; comparaison par triplet d'entiers. | §5.4 |
| 5 | Corpus H2 | **BEAM accepté pour cette décision limitée**, manifeste adapté (mode rétrospectif), SQLite identique dans les deux bras ; LongMemEval-S non requis ; aucune indépendance statistique revendiquée. | §10 |
| 6 | `consolidate` en lexical | **Résultat partiel explicite** : déduplication, remplacement et décroissance conservés ; propositions vectorielles vides avec indicateur d'indisponibilité. | §7 |
| 7 | Périmètre cœur / setup | **Séparation confirmée** : capacités, readiness et runtime dans le cœur ; `_ready`, `provision` et `doctor` dans setup ; aucun parcours setup déclaré compatible avant adaptation. | §9 |
| 8 | Tests existants | **Adaptations nécessaires et listées** ; contrôles exacts sur les contraintes préservés ; identités de version mises en cohérence, `test_distribution_d1a.py:39` non affaibli ; identité des listes d'échecs maintenue, causes vérifiées. | §11.1 |
| 9 | Entités globales | **Conservées en v1**, en documentant les statistiques **et** la compétition top-N avant filtrage ; isolation des statistiques et des candidats en `v2`. | §3 |
| 10 | Installation nue | **Défaut sémantique maintenu**, erreur explicite demandant `[semantic]` ; lexical sélectionné explicitement ; aucun repli automatique selon les dépendances installées. | §8 |

Aucune question ouverte ne subsiste dans ce contrat.

## 15. Amendements du verdict Codex sur la v2 (21/11), intégrés en v2.1

| # | Amendement | Correction | Où |
|---|---|---|---|
| 1 | Contradiction SQL : le §6.8 interdisait toute instruction nommant `embedding_cache`, alors que le constructeur conservé crée cette table | DDL initial du schéma inchangé explicitement autorisé ; lectures de données hors sonde et écritures de vecteurs toujours interdites | §6.8 |
| 2 | Critère G1a contradictoire : « tous les tests verts » exigé, mode rétrospectif reporté après G1a | Mode rétrospectif implémenté avant G1a (étape 7 bis) | §11, §13 |
| 3 | Tests discriminants | Version `10.0.0` ajoutée ; cinq cas cumulés verrouillent la priorité indéterminable > marqueur invalide > contamination > comparaison de profil | §12, `test_lexical_store_guard.py` |

Lectures confirmées par ce verdict, appliquées telles quelles : la priorité du §4.1 concerne les
motifs de refus du **store** (les validations de configuration et de dépendances restent
antérieures) ; à la seconde sonde, le code de son propre refus prévaut, sinon un constat
différent produit `store_profile_unverifiable`.

## 16. Amendement du verdict Codex sur l'implémentation v1 (21/11)

| # | Défaut | Correction | Où |
|---|---|---|---|
| 1 | Course dans la sonde : `stat` des annexes puis ouverture `mode=ro` ; si le dernier écrivain fermait entre les deux, la sonde n° 1 pouvait recréer `-wal`/`-shm` avant de refuser | La sonde n° 1 n'ouvre jamais sans `immutable=1` ; paire présente → `deferred_to_lease`, décision par la sonde n° 2 sous bail ; garantie reformulée (seul effet avant refus : `WriterLock`) ; écrivains sans bail hors contrat | §4.1, §5.1, §5.2, `test_lexical_store_guard.py` (fermeture du dernier écrivain entre observation et ouverture ; aucune connexion sans `immutable=1` hors bail) |
| 2 | Risque latent : aucune garde avant l'écriture d'une version non finale dans un store lexical | `profile_version_not_final` à l'étape 3 du §4.1, avant tout accès au store | §4.1, `test_lexical_store_guard.py` |

Ces deux corrections portent `test_lexical_store_guard.py` de 48 à 59 tests (6 pour la course,
5 pour la garde de version).

## 17. Amendement du verdict Codex sur l'implémentation v2 (21/11)

| # | Défaut | Correction | Où |
|---|---|---|---|
| 1 | Course résiduelle en finalisation automatique : le finaliseur du moteur ne relâchait que le bail (`WriterLock.close`), sans fermer le store. À la sortie de l'interpréteur ou à la collecte du moteur, le bail pouvait être libre alors que la connexion SQLite existait encore ; le moteur suivant observait la paire à la sonde n° 2, la destruction tardive de la connexion supprimait les annexes, et l'ouverture `mode=ro` pouvait les recréer avant un refus | Le finaliseur (`memory._release_engine`) ferme chaque connexion SQLite du moteur **puis** relâche le bail : même ordre que `close()` | `memory.py`, §5.1, `test_engine_finalizer_410.py` |

**Ordre exact du finaliseur**, identique sur les trois chemins (`close()`, échec du
constructeur, finalisation automatique) : (1) `SQLiteStore.close()` — fermeture de la connexion,
point de contrôle du WAL et suppression de `-wal`/`-shm` par SQLite si c'était la dernière
connexion ; (2) `WriterLock.close()`. ~~Une erreur à l'étape 1 n'empêche pas l'étape 2~~ —
**remplacé par le §18** : une erreur à l'étape 1 empêche l'étape 2, le bail est conservé.

**Propriétés.** Le finaliseur ne référence pas le moteur : il reçoit la liste des stores et le
verrou eux-mêmes (références fortes), qui ne peuvent donc pas être collectés avant lui. Une fois
le relâchement réussi, il ne se refait pas ; `close()` après lui ne fait rien, et inversement
(après un échec, voir §18) ;
un moteur finalisé refuse toute opération (« Memory engine is closed »). Dans un processus
enfant issu d'un `fork`, il ne ferme rien : la connexion et le bail hérités appartiennent au
parent (`WriterLock.close` ne déverrouille déjà pas depuis un enfant). Le store JSONL n'a pas
de connexion persistante : rien à fermer.

**Portée.** Correction d'ordre pure, appliquée aux deux profils, sémantique compris. Elle ne
change aucune donnée stockée, ni le schéma, ni les migrations, ni `embed.py`, ni
`writer_lock.py`. Un processus tué (`SIGKILL`, `os._exit`) n'exécute aucun finaliseur : le noyau
relâche le bail et ferme la connexion ensemble, sans point de contrôle ; la paire reste alors
sur disque, stable, et relève de la sonde n° 2 (§5.1).

**Tests** (`test_engine_finalizer_410.py`, 11) : ordre au moment du relâchement (connexion déjà
fermée, annexes absentes) par le finaliseur seul, par le ramasse-miettes, par `close()` et par
un constructeur en échec, dans les deux profils ; idempotence avec `close()` ; ~~bail relâché
après une fermeture en échec~~ (remplacé, §18) ; aucune fermeture depuis un `fork` ; et, entre deux processus,
un écrivain qui sort **sans** `close()` : à l'instant où son bail est libre (processus encore
vivant, bloqué dans le relâchement), un second processus est refusé par la sonde n° 2 sans
qu'aucun fichier soit créé, remplacé ou réécrit, puis ouvre le store sous bail ; le journal
d'événements de l'écrivain est `store_closed` puis `lease_released`. Sur l'ancien ordre, huit
de ces tests échouent (bail relâché connexion ouverte, paire présente).

## 18. Amendement du verdict Codex n° 3 sur `1666d06` (21/11)

| # | Défaut | Correction | Où |
|---|---|---|---|
| 1 | Le finaliseur relâchait **toujours** le bail (`finally`), même quand `store.close()` levait : bail libre, connexion SQLite peut-être encore ouverte, donc la course du §17 restait possible sur le chemin d'échec. Le `weakref.finalize` était de plus consommé par l'échec : `close()` ne pouvait plus réessayer | Le bail n'est relâché **qu'après la fermeture effective** de chaque connexion ; sinon il est **conservé**, l'erreur est propagée et le relâchement reste **rejouable** | `memory.py` (`_EngineRelease`, `_connection_closed`, `_UNRELEASED`, `MemoryV4.close`), §5.1, `test_engine_finalizer_410.py` |

**Fermeture effective.** Un store est tenu pour fermé si `store.close()` est revenu sans
erreur **et** si sa connexion `sqlite3` est constatée fermée (`_connection_closed` :
`connection.total_changes` lève `ProgrammingError` sur une connexion fermée ; la sonde ne touche
pas la base et n'est pas soumise au contrôle de fil d'exécution). Un `close()` qui revient en
laissant la connexion ouverte est une erreur (`StoreCloseError`), traitée comme un `close()` qui
lève. Un store sans connexion persistante (JSONL) est fermé par définition.

**État de relâchement partagé.** `_EngineRelease(pid, stores, bail)` est l'unique objet appelé
par `close()`, par l'échec du constructeur et par le `weakref.finalize` (ramasse-miettes,
sortie de l'interpréteur). Il ne référence jamais le moteur. À chaque appel, sous son propre
verrou : (1) déjà relâché → rien ; (2) processus enfant d'un `fork` → aucune fermeture,
`WriterLock.close()` (qui ne déverrouille pas depuis un enfant) ; (3) sinon, tentative de
fermeture de **chaque** store encore en attente ; un store effectivement fermé est retiré de la
liste et n'est **jamais refermé** ; (4) s'il reste un échec : **bail conservé**, première erreur
propagée ; (5) sinon `WriterLock.close()`, état « relâché ».

**Reprise.** Après un échec, le moteur est fermé aux opérations (« Memory engine is closed »)
mais tient toujours le bail : tout autre moteur, du même processus ou d'un autre, est refusé
(`WriterConflictError`). Un nouvel `engine.close()` rejoue les seules fermetures en attente et,
quand toutes ont abouti, relâche le bail — que le `weakref.finalize` ait été consommé ou non.

**Quand personne ne peut réessayer** (moteur collecté par le ramasse-miettes, constructeur en
échec, sortie de l'interpréteur) : voir §19 — le bail est épinglé au **processus** et non à un
objet Python. Le relâchement en échec reste aussi dans `memory._UNRELEASED`, simple poignée de
reprise dont la garantie ne dépend pas. Garder le bail trop longtemps est le mode de
défaillance accepté ; le relâcher sur une connexion ouverte ne l'est pas. ~~Limite résiduelle :
ordre de destruction non maîtrisé pendant la destruction finale de l'interpréteur~~ — **levée
par le §19**.

**Tests** (`test_engine_finalizer_410.py`, 23). Le test « tentative → relâchement » est remplacé
par la vérification de la propriété, dans les deux profils, pour un `close()` qui lève et pour
un `close()` qui revient connexion ouverte, par `close()` et par le finaliseur : connexion
encore ouverte et paire présente ; **un autre processus** est refusé pour le `flock` puis pour
l'ouverture d'un moteur, sans rien créer ni réécrire ; l'erreur est propagée ; un nouvel essai
échoue de même tant que la cause persiste ; cause levée, `close()` ferme la connexion **puis**
relâche le bail, et l'autre processus prend le bail et ouvre le store. S'y ajoutent : moteur
collecté avec fermeture en échec (bail tenu, relâchement épinglé), constructeur en échec avec
fermeture en échec (bail tenu, erreur d'ouverture conservée en contexte), échec partiel sur
plusieurs stores (aucune double fermeture, seule la fermeture en attente est rejouée, `fork`).
Enfin un vrai `fork` : l'enfant qui appelle `close()` sur le moteur hérité ne ferme pas la
connexion et ne libère pas le bail du parent, qui ferme ensuite normalement.
Avec un relâchement du bail malgré l'échec, douze de ces tests échouent.

## 19. Amendement du verdict Codex n° 4 sur `acc637d` (21/11)

| # | Défaut | Correction | Où |
|---|---|---|---|
| 1 | Après un échec persistant, le bail ne tenait qu'à des références Python (`_UNRELEASED`). Pendant la destruction finale de l'interpréteur, l'objet verrou pouvait être détruit (descripteur fermé, `flock` libéré) **avant** la fermeture tardive de la connexion SQLite | Le bail en échec est **épinglé au processus** : `WriterLock.pin()` duplique le descripteur du verrou (`os.dup`) dans un entier brut qu'aucun objet Python ne possède | `writer_lock.py` (`pin`, `_pinned_fds`, `close`), `memory.py` (`_EngineRelease`) |
| 2 | `SQLiteStore` n'était inscrit dans la liste des stores du moteur qu'**après** son constructeur. Si celui-ci échouait après `sqlite3.connect` et que sa fermeture de secours échouait aussi, l'état de relâchement ne connaissait aucune connexion et libérait le bail | Le store s'inscrit lui-même dans la liste (`SQLiteStore(data_dir, owned=…)`) **dès que la connexion existe**, avant toute autre initialisation | `sqlite_store.py`, `memory.py` |

**Épinglage (défaut 1).** Un verrou `flock` appartient à la *description de fichier ouverte*,
pas à un descripteur : il dure jusqu'à un `LOCK_UN` explicite ou jusqu'à la fermeture de
**tous** les descripteurs qui la désignent. Au premier échec de relâchement, avant de lever,
`_EngineRelease` appelle `WriterLock.pin()` : le doublon est un entier, que ni le
ramasse-miettes ni la destruction finale de l'interpréteur ne peuvent fermer. La destruction de
l'objet verrou ou de son fichier, dans n'importe quel ordre, ne ferme que le descripteur
d'origine : le bail reste tenu. Le seul `LOCK_UN` du code est dans `WriterLock.close()`, que
seul un relâchement **réussi** appelle (il ferme alors aussi le doublon) ; `WriterLock` n'a pas
de `__del__`. Sans reprise réussie, le doublon n'est fermé que par le noyau, à la disparition
du processus : plus aucun code de ce processus ne peut alors fermer une connexion SQLite. Une
connexion fermée tardivement par la destruction de l'interpréteur l'est donc **sous bail** ;
une connexion jamais fermée laisse sa paire sur disque, stable (cas `SIGKILL`, §17). Un enfant
de `fork` n'épingle rien. Portée : verrous `flock` (POSIX) ; sous Windows le doublon est créé de
même, sans test.

**Connexion couverte dès sa création (défaut 2).** `SQLiteStore.__init__` ajoute le store à la
liste `owned` immédiatement après `sqlite3.connect`. Si la suite du constructeur échoue :
fermeture de secours réussie → le relâchement referme sans effet, constate la connexion fermée,
libère le bail ; fermeture de secours en échec → le store est en attente, connexion constatée
ouverte, **bail conservé et épinglé**, relâchement rejouable. Les autres appelants de
`SQLiteStore` (migration, restauration) ne passent pas `owned` : inchangés.

**Tests** (`test_engine_finalizer_410.py`, 27).
- Échec persistant, dans un processus qui sort normalement (pas `os._exit`) : après le `close()`
  en échec, le processus fait ce que fait la destruction finale dans le pire ordre — toutes les
  références Python au bail disparaissent d'abord (objet verrou constaté détruit), la connexion
  est détruite ensuite. À chaque étape, processus vivant, **un autre processus** est refusé
  (`flock` et ouverture du moteur) : verrou détruit et connexion ouverte avec sa paire ; puis
  connexion détruite, paire supprimée sous bail. Le bail n'est obtenu qu'après la sortie du
  processus. L'ordre est provoqué explicitement parce que celui de CPython n'est ni spécifié ni
  reproductible ; la garantie, elle, ne dépend d'aucun ordre.
- Test discriminant associé : même scénario avec `pin()` neutralisé (références seules) → à la
  destruction de l'objet verrou, l'autre processus **obtient le bail et ouvre le store** alors
  que la connexion et sa paire existent encore.
- Constructeur de `SQLiteStore` en échec après `connect` (`changes.initialize`) avec fermeture
  de secours en échec (`Connection.close` de la connexion du store) : connexion ouverte, autre
  processus refusé, store en attente dans le relâchement, bail épinglé ; nouvel essai en échec →
  toujours refusé ; cause levée → connexion fermée puis bail libre. Variante fermeture de
  secours réussie : bail libre, rien d'épinglé. Sans l'inscription dès la connexion, le premier
  cas échoue (bail libre, connexion ouverte).
