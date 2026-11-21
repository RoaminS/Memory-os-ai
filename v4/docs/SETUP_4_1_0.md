# Installation multi-clients 4.1.0 — contrat (phase 1a-setup, étape 1)

Statut : **contrat v2, écrit avant tout code d'outil**. La v1 (`779c3a7`) a reçu le verdict Codex
« NON VALIDÉ » du 21/11 (`codex_verdict_setup_contract_v1.md`) ; la v2 traite chaque point,
tableau en §19. Base `cd83511` (`feature/4.1.0-lexical`, porte G1a atteinte), branche
`feature/4.1.0-setup`. Plan de référence : `PLAN_STRATEGIQUE_OEM_v3_1.md`, « Phase 1a-setup » et
annexe A, clause D2 (« rollback conservant les nouvelles écritures ; désinstallation conservant
les données ; aucun secret journalisé »), précisée par `DISTRIBUTION_PLAN_COMMUN_v1.md:35`
(« Désinstallation conserve les données par défaut ; purge séparée explicitement confirmée.
Rollback après nouvelles écritures n'est jamais la remise en service d'une vieille copie »). Ce
document ne modifie aucun fichier sous `v4/src/`, `src/` ni `scripts/`. Il est livré avec ses
tests (§13), rouges par construction là où le comportement n'existe pas, verts là où ils
épinglent un comportement à conserver. Aucune installation, aucun `--apply`, aucun appel
`launchctl` ou `claude` réel, aucun téléchargement n'a été exécuté pour l'écrire. Plateforme
visée : macOS ARM64, CPython 3.11. Chemins d'outils relatifs à `v4/src/memory_os_v4/ops/`.

## 1. Faits de code constatés sur `cd83511`

- **`quickstart.py`** fait déjà : aperçu client sans écriture (`preview`), préparation complète
  en lecture seule (`prepare`), application journalisée (`apply_install`, `apply_clients`),
  sauvegarde de chaque fichier client avant écriture (`<journal>/<client>.bak`, 0600 dans un
  dossier 0700), annulation sémantique de notre seule entrée (`undo`), refus d'un service
  étranger, consentement de téléchargement séparé. Le jeton reste dans son fichier 0600 : les
  configurations clientes ne reçoivent que son chemin (`clients.relay_command`). Ce fichier est
  identique à celui de la base 4.0.9 (`83ddead`).
- **Aucun profil.** `prepare` appelle `provision.plan()` (Granite + table),
  `inspect_current(extras=('ops','brief'))` (catalogue sémantique) ; `_service_plan` écrit
  toujours `MEMORY_V4_EMBED_MODEL`, `MEMORY_V4_DEVICE`, `MEMORY_V4_EMBED_*` ; `_ready` exige la
  révision Granite et le runtime d'encodage. Or `Config.validate_retrieval_profile()` refuse en
  profil lexical toute valeur non par défaut de `embed_model`, `device`, `embed_dtype`, etc.
  (`LEXICAL_DEFAULT_ONLY`) : le plist actuel ne peut pas servir un moteur lexical.
- **`doctor.py::_capabilities`** lit `caps["encoding"]["profile"]["device"]` : sur un service
  lexical (`profile: None`) il lève `TypeError` et rend « malformed deployed capability
  contract » (FAIL). `doctor.run` inspecte son propre runtime avec le catalogue sémantique.
- **SQLite.** Le plancher `MIN_SQLITE = (3, 51, 3)` n'est vérifié qu'à l'ouverture du store
  (`sqlite_store.py:300`) et par la sauvegarde (`sqlite_backup.py:108`), donc après la création
  du jeton et du service. Le store utilise `json_extract` et `PRAGMA journal_mode=WAL` ; il
  n'utilise **aucune** table FTS. Les capacités déployées ne publient pas la version de SQLite
  du processus serveur.
- **Conflits.** Pour les clients JSON, toute entrée `memory-os-ai` différente de la nôtre —
  `direct-http`, `legacy-v3`, `other`, **ou un relais d'une autre installation** — reçoit
  l'action `replace` et est remplacée par un simple `--apply`.
- **Secrets.** `journal.json` conserve `previous_entry` **en clair** (un en-tête
  `Authorization: Bearer …` y est journalisé). En cas d'échec de démarrage,
  `_startup_log_tail` (`quickstart.py:461`) copie les 24 dernières lignes de `server.err`, n'y
  masque que le jeton et le motif `Bearer …`, puis ce texte est **journalisé et placé dans le
  message d'erreur** (`:735`, `:743`) : une clé, un secret étranger ou tout autre contenu du log
  y passe. `install.json["doctor"]` recopie `detail` et `hint` de chaque contrôle.
- **TOML (Codex).** Écriture par ajout quand l'entrée est absente, refus manuel quand elle
  existe. Le candidat n'est pas analysé avant l'écriture : avec `mcp_servers = { … }` (table en
  ligne), l'ajout produit un fichier **invalide**, laissé tel quel. Après `undo`, le fichier
  garde une ligne vide de plus que l'original.
- **JSON.** Réécriture complète en `indent=2` ; clés dupliquées fusionnées sans avertissement.
- **Arrêt.** `undo` ne touche que les clients. Aucune commande de désinstallation. Après un
  échec de `bootstrap`, le plist et le jeton restent ; un nouvel aperçu voit un plist « owned »
  non chargé et ne le signale qu'à l'application.
- **Refus.** Phrases libres (`blockers`) ; la CLI rend un code unique `quickstart_refused`.
- **Relais.** `ops/relay.py` importe `anyio`, `mcp`, `httpx` : il n'est pas exécutable avec la
  seule bibliothèque standard. `import memory_os_v4` charge `numpy`.
- **Outillage.** `scripts/qualify_runtime_resolution.py:47` résout `<wheel>[ops,brief]` sans
  `-c`, puis `check` compare au catalogue **sémantique**. `README.md:36` installe `4.0.9` ; ses
  deux liens d'installation aussi. `server.json` (déjà `4.1.0`) ne lance que le relais.
- **Isolation des tests.** `conftest.py` redirige `HOME` avant collecte. Les fonctions de
  `quickstart` ont `run=subprocess.run` comme valeur par défaut liée à la définition : un test
  qui n'injecte pas `run` exécute les vraies commandes.

## 2. Profil d'installation

### 2.1 Sélection

Le profil d'installation est celui du moteur : `semantic` ou `lexical`. Trois règles,
cohérentes avec le contrat moteur (`LEXICAL_PROFILE_4_1_0.md` §8).

1. **Installation neuve : `--profile` est obligatoire.** Sans lui, l'aperçu rend
   `ok: false`, `blocker_codes: ["setup_profile_required"]`, `profile: null`, et n'écrit rien.
   *Proposition, à arbitrer par Romain* (§16, R1).
2. **Jamais d'après les paquets installés.** `--profile semantic` sans la fermeture ML rend
   `runtime_unqualified` (état `missing_dependency`) dont le message nomme
   `memory-os-ai[ops,brief,semantic]` et `qualified_runtime_constraints_v1.txt` ; le profil du
   plan reste `semantic`. `--profile lexical` avec des paquets ML présents est accepté.
3. **Installation existante : le profil se lit, il ne se convertit pas.** Service possédé :
   profil du plist (`MEMORY_V4_RETRIEVAL_PROFILE`, absent = `semantic`). Sans `--profile`, il
   est repris. Un `--profile` différent est refusé : `setup_profile_conflict`.

Autres refus : `setup_profile_unknown` ; `setup_profile_backend_conflict` (`lexical` + `jsonl`).

### 2.2 Ce que change chaque profil

| | `lexical` | `semantic` |
|---|---|---|
| Installation qualifiée | `pip install -c <contraintes lexicales> "memory-os-ai[ops,brief]==4.1.0"` | `pip install -c <contraintes sémantiques> "memory-os-ai[ops,brief,semantic]==4.1.0"` |
| Runtime inspecté | `inspect_current(profile="lexical", extras=("ops","brief"))` | `inspect_current(profile="semantic", extras=("ops","brief"))` |
| Ressources | `provision.plan(resources=("cl100k",))` : table seule (1 681 126 octets) | `provision.plan()` : Granite + table (221,9 Mo), inchangé |
| Téléchargement | `--download-approved` si la table manque ; aucun accès à `huggingface.co` | inchangé |
| Environnement du service | `MEMORY_V4_RETRIEVAL_PROFILE=lexical` ; **aucune** variable d'encodeur (`MEMORY_V4_EMBED_MODEL`, `_EMBED_MAX_SEQ_LENGTH`, `_EMBED_DTYPE`, `_EMBED_BATCH_SIZE`, `MEMORY_V4_DEVICE`, `HF_HUB_CACHE`) ; les autres variables sont celles de la référence figée | **identique à l'octet près** à la référence figée (§2.5) |
| Backend | `sqlite` seul | `sqlite` (défaut) ou `jsonl` |
| Readiness exigée | marqueur `LEXICAL_MARKER`, `policies.ranking_policy == "lex-4.1.0-v1"`, `bm25_profile == "scoped_literal"`, `lexical_guard_k == 3`, `encoding.state == "absent_by_profile"`, tokenizer prêt | contrat 4.0.9 |
| Readiness, les deux profils | `capabilities.store.sqlite_version` publié et ≥ 3.51.3 (§3) | idem |

Un service qui ne répond pas exactement au profil attendu est refusé avant toute écriture
cliente : `service_profile_mismatch`.

Ce que la vérification du runtime prouve : les versions **installées** sont égales au verrou du
profil. Elle ne prouve pas que `-c` a été utilisé pour les obtenir, et n'a pas à le prouver :
c'est l'état qui est qualifié, pas l'historique de la commande.

### 2.3 Signatures (ajouts ; les appelants actuels restent valides)

- `quickstart.prepare(settings, *, clients, home, python, backend, run, profile=None,
  replace_existing=(), which=shutil.which)` ; le plan gagne `profile`, `blocker_codes` (liste
  parallèle à `blockers`, même longueur, même ordre) et `sqlite_runtime`.
- `quickstart.preview(..., replace_existing=())` ; chaque client du plan porte
  `installation_identity` et `current_identity` (§5.2).
- `quickstart.installation_identity(env) -> str`, `quickstart.entry_identity(entry) -> str | None`.
- `quickstart._service_plan(..., *, backend=None, profile="semantic")`.
- `quickstart._ready(settings, identity, *, backend="sqlite", profile="semantic")`.
- `quickstart.QuickstartError(message, code="quickstart_refused")`, attribut `code`.
- `quickstart.apply_install(..., clock=time.monotonic)` : toute durée vient de `clock` ; le
  journal `install.json` gagne `profile`, `code` (en cas d'échec), `timing` (§10).
- `quickstart.undo(stamp, *, home, run, with_service=False, stop_timeout=10.0, sleep=time.sleep)`.
- `capabilities.deployed()` : `store.sqlite_version = sqlite3.sqlite_version` (une ligne, côté
  serveur ; aucun autre changement moteur).
- CLI : `quickstart --profile {lexical,semantic}`, `--replace-existing`, `--with-service` ;
  sans `--profile`, `profile=None` est transmis ; un refus rend le `code` de l'erreur.

### 2.4 `doctor`

- `doctor.installation_profile(info, environ) -> (profil, source)` : service joignable →
  `"deployed"` ; sinon `MEMORY_V4_RETRIEVAL_PROFILE` → `"environment"` ; sinon défaut moteur
  `semantic` → `"engine-default"`, **affiché** dans le détail du contrôle.
- `_capabilities` : déploiement lexical accepté. `encoder-runtime` : PASS « absent by
  profile », FAIL « missing dependency » (correctif `memory-os-ai[ops,brief,semantic]`), FAIL
  « drift ». `server-runtime` : catalogue du profil déployé, nommé. `store-profile` : PASS avec
  la politique, FAIL si le marqueur est malformé.
- Deux contrôles SQLite distincts (§3) : `sqlite-runtime` (« this interpreter ») et
  `server-sqlite-runtime` (« running server »).

### 2.5 Référence figée du service sémantique

Le test `test_semantic_service_definition_equals_the_frozen_4_0_9_reference` compare la
définition produite pour des entrées fixes (`/nonexistent-home-410`, interpréteur
`/opt/memory-os/venv/bin/python`, port 43219) à une **référence littérale** écrite dans le
test : document plist complet (libellé, arguments, vingt variables avec leurs valeurs et leur
ordre, `KeepAlive`, `RunAtLoad`, `StandardErrorPath`) et SHA-256 des octets
(`bea151cf…fb2066`). Ce n'est pas la sortie de la fonction comparée à elle-même. Le profil
lexical est défini comme cette référence moins les six variables d'encodeur, plus
`MEMORY_V4_RETRIEVAL_PROFILE=lexical`.

## 3. SQLite chargé vérifié

Nouveau module `ops/sqlite_runtime.py`, sans effet de bord, importable sans ML.

- `MIN_VERSION` **est** `sqlite_store.MIN_SQLITE`.
- `inspect(*, module=None) -> dict` : bibliothèque **chargée par cet interpréteur** ; n'ouvre
  qu'une base `:memory:`, n'écrit rien.

  ```
  {"ok": bool, "code": None | <code>, "version": "3.53.4" | None, "minimum": "3.51.3",
   "checks": {"loaded": bool, "version": bool, "json1": bool, "wal_compiled": bool},
   "fts5": bool, "writes": "none"}
  ```

  | Ordre | Contrôle | Méthode | Code de refus |
  |---|---|---|---|
  | 1 | chargement | `import sqlite3`, `connect(":memory:")` | `sqlite_runtime_unavailable` |
  | 2 | version | triplet d'entiers ≥ `MIN_VERSION` | `sqlite_runtime_too_old` |
  | 3 | fonctions JSON | `SELECT json_extract('{"a": 7}', '$.a')` rend 7 | `sqlite_json_missing` |
  | 4 | WAL compilé | `PRAGMA compile_options` sans `OMIT_WAL` | `sqlite_wal_unavailable` |

  Le premier échec fixe le code. `fts5` est informatif.
- `wal_probe(directory, *, module=None) -> {"ok", "code", "journal_mode", "proves"}` : preuve
  fonctionnelle **à l'application seulement** (arbitrage Q4), dans le dossier de journal 0700,
  avant ressources, jeton et service ; base jetable supprimée avec ses `-wal`/`-shm`.
  **Limite déclarée** : `proves = "WAL obtained on the volume of the probe directory only"`.
  Elle ne prouve pas le WAL sur un autre volume hébergeant le store ; cette preuve-là est
  apportée par le moteur à l'ouverture (`SQLiteStore` exige la réponse `wal`) puis par
  `_ready`, qui échoue si le service ne devient pas prêt.
- `prepare` appelle `sqlite_runtime.inspect()` par l'attribut du module. Backend `sqlite` : un
  rapport non `ok` bloque. Backend `jsonl` : informé sans blocage.
- **Deux attestations, jamais confondues** (arbitrage Q3) :
  - *interpréteur de la commande* : suffisant pour une installation **neuve**, dont le service
    sera lancé avec ce même interpréteur (la CLI n'a pas d'option `--python`). Contrôle
    `doctor` `sqlite-runtime`, détail « this interpreter ». Il ne certifie pas un processus
    déjà lancé ;
  - *processus serveur* : `capabilities.store.sqlite_version`, publié par le serveur.
    `_ready` l'exige pour un backend SQLite : absent ou illisible →
    `sqlite_runtime_unattested` ; inférieur au plancher → `sqlite_runtime_too_old`. Contrôle
    `doctor` `server-sqlite-runtime` : PASS « running server », FAIL
    `sqlite_runtime_too_old`, WARN « not attested by the running server » (serveur antérieur).

## 4. Aperçu, application, sauvegarde

- **Aperçu = zéro écriture, prouvé par inventaire** (chaque chemin, mode, `mtime_ns`, octets de
  la racine temporaire, `HOME` compris) pour `prepare` (deux profils, six clients, cas refusés
  compris) et `uninstall.plan`. Commandes autorisées en aperçu : `launchctl print`,
  `pgrep -x Claude` ; lecture de `which("claude")`.
- **Application = `--apply` explicite**, précédée d'une revalidation complète ; étapes :
  `sqlite` (sonde WAL) → `resources` → `token` → `service` → `service_ready` → `clients` →
  `checks` → `demo` (sur demande).
- **Sauvegarde** : chaque fichier client existant est copié à l'identique dans le journal de
  l'opération avant d'être modifié (0600, dossier 0700), Claude Code compris. Le journal reçoit
  `backup_sha256`, `backup_may_hold_secrets: true`, et, pour une entrée remplacée,
  `previous_class` et `previous_entry_sha256` — jamais l'entrée (§8).
- **Qui écrit quoi** (la v1 disait à tort « toute écriture passe par `_atomic_write` ») :

  | Cible | Écrite par | Garanties |
  |---|---|---|
  | Fichiers clients JSON et TOML ; journaux | **nous**, uniquement par `quickstart._atomic_write` | temporaire, `fsync`, `os.replace`, mode conservé |
  | `~/.claude.json` (Claude Code) | **la CLI `claude`**, jamais nous | nous : sauvegarde avant, empreinte vérifiée avant, entrée relue après ; l'atomicité est celle de la CLI |
  | Sauvegardes `*.bak` | nous, copie + `fsync` | 0600, création dans un dossier neuf |
  | Jeton, plist | nous, `auth.write_secret_file` | création exclusive, jamais d'écrasement |
  | Plist retiré | nous, déplacement dans le journal | seulement après arrêt confirmé (§6) |

- **Codes** : chaque refus a un code stable (§11) ; `apply_install` sur un plan refusé lève
  `QuickstartError(code="preflight_refused")`.
- **Installation interrompue.** Un échec après la création du plist (par exemple `bootstrap`
  en échec : `service_bootstrap_failed`) laisse plist, jeton et journal en place, sans aucune
  écriture cliente. L'aperçu suivant le dit au lieu de le découvrir à l'application :
  `service_not_loaded`, message nommant `quickstart --undo <stamp> --with-service`.

## 5. Clients

Périmètre qualifié : **Claude Code, Claude Desktop, Codex, Cursor, VS Code, Antigravity**. Les
autres clients du registre gardent le comportement 4.0.9 et ne sont pas déclarés qualifiés.

### 5.1 Par client

| Client | Fichier (sous `$HOME`) | Format | Écriture | Entrée existante autre que la nôtre |
|---|---|---|---|---|
| Claude Code | `.claude.json` | JSON, `mcpServers` | `claude mcp add --scope user` ; CLI absente → `client_cli_missing` | `claude mcp remove` puis `add`, selon §5.2 |
| Claude Desktop | `Library/Application Support/Claude/claude_desktop_config.json` | JSON, `mcpServers` | fusion de notre clé, application fermée | §5.2 |
| Codex | `.codex/config.toml` | TOML | ajout quand l'entrée est absente | **jamais réécrite** ; seule l'entrée strictement identique à la nôtre donne `unchanged` (arbitrage Q5) |
| Cursor | `.cursor/mcp.json` | JSON, `mcpServers` | fusion de notre clé | §5.2 |
| VS Code | `Library/Application Support/Code/User/mcp.json` | JSON, `servers`, `"type": "stdio"` | fusion de notre clé | §5.2 |
| Antigravity | `.gemini/config/mcp_config.json` | JSON, `mcpServers` | fusion de notre clé | §5.2 |

Propriétés communes : idempotence (`unchanged` : ni écriture, ni sauvegarde, ni commande) ;
entrées et réglages étrangers conservés à l'identique et jamais affichés ; seul le fichier du
client choisi est touché ; fichier réécrit par son application entre l'aperçu et l'application
→ `conflict-file-changed-since-preview`, aucune écriture ; Claude Desktop en cours d'exécution
→ `client_app_running` dès l'aperçu. Notre entrée : interpréteur absolu,
`["-m","memory_os_v4.ops.relay"]`, `MEMORY_V4_CLIENT_ID`, `MEMORY_V4_URL`,
`MEMORY_V4_TOKEN_FILE`, `MEMORY_V4_OPS_DIR` ; aucune valeur secrète.

### 5.2 Conflits : ne jamais écraser en silence

**Identité d'installation.** Une installation est le service qu'elle désigne :
`installation_identity(env) = sha256(JSON canonique {"url": MEMORY_V4_URL, "token_file":
MEMORY_V4_TOKEN_FILE})`. Deux installations ne peuvent pas partager l'URL et le fichier de
jeton sans être le même service. `entry_identity(entry)` rend cette empreinte pour une entrée
de classe `relay` qui porte **les deux** variables, sinon `None` (identité inconnue).
L'interpréteur, `MEMORY_V4_OPS_DIR` et `MEMORY_V4_CLIENT_ID` n'entrent pas dans l'identité :
un venv déplacé reste la même installation.

| Entrée existante | Action sans consentement | Avec consentement nommé |
|---|---|---|
| égale à la nôtre | `unchanged` | — |
| `relay`, identité **égale** à celle de cette installation | `replace` (montrée par l'aperçu, sauvegardée) | — |
| `relay`, identité **différente ou inconnue** (autre jeton, autre URL, sans environnement, script `memory-os-relay` nu) | `refuse-existing-relay-other-installation` | `replace` |
| `direct-http`, `legacy-v3`, `other` | `refuse-existing-<classe>` | `replace` |

Code de blocage : `client_entry_conflict`. Consentement : `--replace-existing
cursor,claude-desktop`, **par client** ; il ne vaut pas pour un autre client ; nommer un client
non sélectionné est refusé (`client_consent_unselected`). Codex : aucun consentement possible.
La désinstallation applique la même identité (§7) : elle ne retire que les relais de **cette**
installation.

### 5.3 Formats

**TOML (Codex).** Le candidat est **analysé par `tomllib` avant toute écriture** et n'est écrit
que si ses données sont égales aux données antérieures plus notre entrée ; sinon
`refuse-toml-structure-manual` dès l'aperçu. Les octets antérieurs sont un préfixe du nouveau
fichier. `undo` retire exactement ce qui a été ajouté : octets d'origine si rien d'autre n'a
changé, ajouts ultérieurs conservés sinon.

**JSON.** Indentation (espaces ou tabulation), saut de ligne final et ordre des clés conservés,
texte non ASCII non échappé. Application puis `undo` rendent les octets d'origine d'un fichier
canonique. Clés dupliquées : `refuse-json-duplicate-keys`. JSONC : `refuse-unreadable`.

### 5.4 Claude Desktop et le paquet `.mcpb`

**Le manifeste de la v1 est retiré.** Il décrivait un serveur `type: "python"` sans
dépendances embarquées, lançant l'interpréteur d'un venv extérieur au paquet. Les notes de
spécification (`.local-baseline/mcpb_manifest_spec_notes_20261121.md`) établissent que les
types `python` et `node` **exigent** des dépendances embarquées et que le lancement d'un
exécutable hors du paquet n'est pas spécifié : à traiter comme non permis. Et le relais
dépend de `mcp`, `anyio`, `httpx` (§1) : un paquet `python` sans dépendances est impossible.

| Option | Contenu | Avantages | Coûts et risques |
|---|---|---|---|
| **A — `server.type: "uv"`** (manifeste 0.4) | `manifest.json` + `pyproject.toml` dépendant de `memory-os-ai[ops,brief]==4.1.0` ; l'hôte gère l'environnement | paquet minuscule, indépendant de la plateforme, conforme | exige le paquet sur **PyPI** (publication : hors phase) et le réseau à l'installation ; l'hôte résout les dépendances : le verrou qualifié n'est garanti que si les contraintes peuvent être transmises, à vérifier ; type 0.4 alors que la spécification s'annonce 0.3 |
| **B — `python` avec dépendances embarquées** | `server/lib` = fermeture lexicale résolue depuis `qualified_runtime_constraints_lexical_v1.txt` pour darwin-arm64 / CPython 3.11 | hors ligne, verrou qualifié embarqué, reproductible à partir de wheels à empreinte fixée | ≈ 69 Mo décompressés pour 36 paquets (mesure du runtime de qualification), dont 8 à binaires natifs (`numpy`, `cryptography`, `pydantic-core`, `cffi`, `rpds-py`, `regex`, `tiktoken`, `charset-normalizer`) : verrouillé à une plateforme et à CPython 3.11 ; le type `python` ne fournit pas d'interpréteur, il faut donc soit le Python 3.11 de l'hôte (à exiger par `compatibility.runtimes`, échec sinon), soit un interpréteur embarqué (hors de ce que la spécification décrit) ; second exemplaire du runtime à maintenir à côté du venv ; construction = téléchargement de wheels |
| **C — reporter le `.mcpb`** | aucun paquet ; Claude Desktop reste un client qualifié par `claude_desktop_config.json` | aucun risque de conformité ; rien de nouveau à qualifier ; D1 n'en dépend pas (les testeurs utilisent `quickstart`) | l'application doit être fermée pendant l'écriture ; pas d'installation « en un clic » |

**Recommandation : C pour la phase 1a-setup**, B ou A dans une phase ultérieure (A dès que la
publication existe ; B si une installation hors ligne en un clic est demandée). *Décision de
Romain* (§16, R7). Ce contrat et ses tests sont écrits pour C : aucune commande `mcpb`, aucun
module `ops/mcpb.py` (test `test_no_bundle_is_shipped_by_this_phase`).

Quel que soit le choix ultérieur, exigences déjà fixées :

- `manifest_version` **épinglée** : `"0.3"` pour B, `"0.4"` pour A ; toute autre valeur est un
  échec de test ;
- **validation indépendante** : le manifeste est validé contre le schéma JSON officiel de
  cette version, déposé dans le dépôt comme ressource de test avec son SHA-256 et sa
  provenance (acquisition sous GO ; aucun accès réseau pendant les tests), et par
  `mcpb validate` de l'outil officiel quand il est disponible hors ligne ; des constantes
  maison ne valent pas validation ;
- **preuve réelle** dans Claude Desktop (installation de l'extension, outils listés, une
  écriture et une recherche), sous GO de Romain, hors suites ;
- jamais de jeton dans le paquet ni de champ qui le demande ; archive reproductible à l'octet.

**Extension déjà installée.** Une extension est gérée par Claude Desktop, hors de
`claude_desktop_config.json` : ni `doctor` ni `uninstall` ne la voient. La désinstallation ne
prétend donc jamais être complète (§7) : elle rend « retrait manuel requis » avec la procédure.

## 6. Retour arrière

`quickstart --undo STAMP [--with-service]`.

- **Journal de confiance** (arbitrage Q7) : dossier 0700, `journal.json` et `install.json`
  0600, propriétaire courant, un seul lien, aucun lien symbolique sur le chemin ; sinon
  `journal_unsafe`, rien n'est fait.
- **Clients** (comportement actuel, conservé) : retrait sémantique de notre entrée, ou
  rétablissement de la précédente, **seulement** si l'entrée en place est encore celle que
  nous avons écrite (`refused-entry-changed-since-apply` sinon). Jamais la remise en place de
  la sauvegarde : ce qui a été écrit depuis dans le fichier est conservé. L'entrée précédente
  est relue dans la sauvegarde après contrôle de `backup_sha256` et `previous_entry_sha256`
  (`refused-backup-changed` sinon).
- **Service**, avec `--with-service` seulement. Séquence : (1) le journal atteste que **cette**
  installation a créé le service ; (2) le plist a l'empreinte journalisée ; (3) le job chargé
  est **ré-attesté** (programme, arguments, fichier de jeton, dossier) juste avant l'arrêt ;
  (4) `launchctl bootout`, une seule fois, sans relance ni arrêt forcé ; (5) le job est
  **observé absent** (`launchctl print`, dans `stop_timeout`) ; (6) alors seulement le plist
  est **déplacé** dans le journal de l'installation.

  | Issue (`service.undo`) | Condition | Code | Plist |
  |---|---|---|---|
  | `not-requested` | sans `--with-service` | — | en place |
  | `stopped-and-plist-moved` | séquence complète | — | déplacé |
  | `plist-moved-job-was-absent` | job jamais chargé (installation interrompue) | — | déplacé, aucun `bootout` |
  | `not-created-by-this-install` | le service préexistait | — | en place |
  | `refused-changed-since-install` | empreinte du plist différente | `service_changed_since_install` | en place |
  | `refused-job-not-attested` | job remplacé ou non attesté | `service_changed_since_install` | en place |
  | `stop-failed` | `bootout` rend un code non nul | `service_stop_failed` | **en place** |
  | `stop-timeout` | `bootout` dépasse son délai | `service_stop_timeout` | **en place** |
  | `stop-unconfirmed` | `bootout` rend 0 mais le job reste chargé | `service_stop_unconfirmed` | **en place** |

  Un plist n'est jamais déplacé après un arrêt non confirmé : le job continuerait de tourner
  sans définition sur disque.
- **Données : jamais.** Aucun fichier du store, des sauvegardes, de la clé, du jeton ni des
  ressources n'est supprimé, restauré, remplacé, ni même ouvert. Aucune ancienne copie n'est
  remise en service : `data_restored_from_copy: False`. Preuve sur un **vrai store SQLite
  temporaire** (§13) : des mémoires écrites par le moteur après l'installation sont retrouvées
  par recherche après le retour arrière, avec le même `store_instance_id`.

## 7. Désinstallation

Nouveau module `ops/uninstall.py` ; commande `memory-os-admin uninstall [--clients …] [--apply]`.

- `plan(settings, *, home, python, run, clients=None)` : lecture seule.
  `apply(plan, settings, *, home, run, stop_timeout=10.0, sleep=time.sleep)`.
- **Retire** :
  - dans chaque client, l'entrée `memory-os-ai` dont l'identité est celle de **cette**
    installation (§5.2) : `remove`. Toute autre entrée — autre classe, relais d'une autre
    installation ou d'identité inconnue — est gardée : `keep-foreign` / `kept-foreign`.
    Sauvegarde de chaque fichier touché ; formats conservés (après installation puis
    désinstallation, les six fichiers retrouvent leurs octets d'origine) ;
  - le service launchd **possédé**, par la séquence du §6.
- **Ordre et issues** (arbitrage Q8) :
  1. **Revalidation globale avant toute mutation** : empreinte de chaque fichier client,
     empreinte du plist, identité du job. Tout écart → `UninstallError(code=
     "uninstall_changed_since_preview")`, **rien** n'est modifié, le service continue.
     Plan refusé (service étranger : `uninstall_service_foreign` ; Claude Desktop ouvert :
     `client_app_running`) → `UninstallError(code="preflight_refused")`.
  2. **Clients d'abord.** Un conflit **tardif** (fichier réécrit pendant l'exécution) laisse
     ce client en `conflict-file-changed-since-preview`, les autres sont traités ; résultat
     **partiel explicite** : `ok: false`, `partial: true`, `code: "uninstall_partial"`.
  3. **Service ensuite, seulement si tous les retraits clients ont réussi.** Sinon
     `service.outcome = "kept-clients-incomplete"` : aucun `bootout`, plist en place (un client
     encore configuré ne doit pas pointer vers un service arrêté).
  4. Arrêt non confirmé : `stop-failed`, `stop-timeout` ou `stop-unconfirmed`, avec le code
     du §6 ; plist **en place**, `partial: true`, `next_steps` donne la commande `launchctl
     bootout` à lancer à la main. Une nouvelle exécution termine le travail.
- **Ne retire jamais** : store, sauvegardes, clé, jeton, ressources, journaux et leurs
  sauvegardes, journaux du relais, logs, venv. Listés dans `retained` (`kind`, `path`,
  `exists`) ; `data_deleted: False`. **Aucune option de purge** : ni paramètre ni drapeau, et
  le module n'appelle aucune suppression récursive. La désinstallation n'ouvre pas le store.
  Preuve sur vrai store (§13) : mémoires retrouvées par recherche après désinstallation, même
  identité, store toujours inscriptible.
- **Jamais « complète ».** Plan et résultat portent `complete: False`, un `scope` qui décrit ce
  qui a été traité, et `manual_removal_required`, liste d'éléments `{kind, inspected,
  procedure}` :
  - `claude-desktop-extension` — `inspected: False` : une extension `.mcpb` éventuelle n'est
    pas visible ; procédure : Claude Desktop → Settings → Extensions → retirer `memory-os-ai` ;
  - `claude-code-hooks` — hooks de `~/.claude/settings.json` ;
  - `auxiliary-launchd-jobs` — jobs `.backup`/`.health` posés par `setup --write-launchd` ;
  - `python-environment` — le venv et le paquet (`pip uninstall`).
- **Idempotence** : seconde exécution `nothing_to_do: True`, service `absent`, aucune écriture.
- **Store conservé puis réinstallation : hors périmètre.** Un store non vide n'est jamais
  adopté : `store_not_empty`, message renvoyant au runbook de mise à niveau. Il n'y a **pas**
  d'option `--reuse-store` dans cette phase. Justification : rattacher un store exige
  d'attester son identité à la désinstallation, puis de vérifier identité, profil, backend et
  version à la réinstallation — c'est la sémantique de la mise à niveau, pas une variante
  triviale de l'installation neuve ; l'écrire à moitié donnerait un chemin d'adoption non
  prouvé. « Conserver les données » est prouvé par leur récupérabilité (ci-dessus), pas par un
  rattachement automatique. *Périmètre à confirmer par Romain* (§16, R4).

## 8. Aucun secret journalisé

Secrets : la valeur du jeton, la clé de sauvegarde, et tout secret étranger (clés d'API
d'autres serveurs, en-tête `Authorization` d'une ancienne entrée, compte de `~/.claude.json`,
contenu d'un log ou d'une sortie de commande).

**Principe : des diagnostics structurés, à liste blanche.** On ne peut pas masquer un secret
qu'on ne connaît pas ; on n'écrit donc que des champs dont on connaît la forme. Aucun texte
libre venu d'un log, d'un sous-processus ou d'une exception étrangère n'atteint une sortie.

| Source | Règle |
|---|---|
| Échec de démarrage du service | `startup_failure` ⊆ `{state, runs, last_exit_code, pid, process_state, exception_types, log_path, log_bytes, stop_attempted, stopped}`. **`stderr_tail` disparaît.** `exception_types` : noms de classes d'exception reconnus dans les 8 192 derniers octets, pris dans un **ensemble fermé** (exceptions natives de Python et classes d'erreur du paquet) ; jamais leur message. Le message d'erreur donne le chemin du log privé, pas son contenu. Le log reste en place, dossier 0700 |
| Sous-processus (`launchctl`, `claude`, `pgrep`) | seuls le nom de la commande et le code de retour ; stdout et stderr ne sont jamais copiés, délai dépassé compris |
| Contrôles `doctor` pendant l'installation | `install.json["doctor"] = [{"area", "status"}]` ; `detail` et `hint` ne sont pas journalisés (relancer `doctor` pour les lire). Échec : `code: "doctor_failed"`, message nommant les zones en échec |
| Sortie de `doctor` | détails issus de gabarits ; d'une exception étrangère, seulement le nom de classe |
| Exceptions | texte conservé seulement pour nos propres classes (`QuickstartError`, `ProvisionError`, `UninstallError`), dont les messages sont construits sans contenu ; toute autre exception est réduite à son nom de classe, avec un code stable (par exemple `relay_probe_failed`) |
| Aperçus, rapports, sorties de la CLI | notre entrée, des chemins, des classes, des empreintes |
| Journaux | `previous_entry` remplacé par `previous_class` et `previous_entry_sha256` |
| Plist | chemin du fichier de jeton, jamais sa valeur |

**Sauvegardes : la seule exception, déclarée.** Une sauvegarde `*.bak` est la copie intégrale
du fichier d'origine. Elle peut donc contenir des **secrets préexistants** — une clé d'API
étrangère, ou **ce même jeton** si une ancienne entrée HTTP directe le portait (la v1 affirmait
à tort le contraire). Ces secrets préexistants sont autorisés **uniquement** dans ces
sauvegardes privées : 0600, dossier 0700, jamais affichées, jamais recopiées dans un journal,
citées par chemin avec `backup_may_hold_secrets: true`. Nous n'y ajoutons jamais de secret.

Méthode de test : sentinelles pour le jeton (le vrai, créé par l'installation), la clé de
sauvegarde (octets et hexadécimal), des secrets étrangers et un secret « de fuite » injecté dans
les chemins d'échec : log du serveur (dont une ligne imitant un nom de classe d'exception),
stdout/stderr de chaque commande simulée, détails de contrôles `doctor`, texte d'exceptions,
fichiers hostiles lus par `doctor`. Sont fouillés : résultats, erreurs, sorties de la CLI, et
tous les fichiers du dossier d'état hors `*.bak`.

## 9. Outillage de version

- **`scripts/qualify_runtime_resolution.py`** : `--profile` obligatoire ;
  `resolution_command(python, wheel, output, *, profile)` construit la commande — `lexical` →
  `<wheel>[ops,brief]` avec `-c <contraintes lexicales>` ; `semantic` →
  `<wheel>[ops,brief,semantic]` avec `-c <contraintes sémantiques>`. **`main()` est testé avec
  un exécuteur simulé** : la commande exécutée est celle de `resolution_command`, le profil
  est transmis à `check`, la preuve consigne `profile` et `constraints_sha256` ; sans
  `--profile`, code 2 et aucune exécution ; une résolution lexicale contenant `torch` fait
  échouer `main()` par le vrai `check` (`ml_packages`).
  `check_runtime_resolution.check(..., profile="semantic")` compare au catalogue du profil.
- **Localisateur de contraintes** (arbitrage Q1 : adopté). Module de premier niveau
  `memory_os_constraints` (bibliothèque standard seule, n'importe pas `memory_os_v4`),
  commande `memory-os-constraints {lexical|semantic}`. Preuves : imports tiers bloqués ;
  arborescence nue du paquet sans `site-packages` (`python -I -S`) ; et, à l'implémentation,
  **wheel installé avec `--no-deps`** dans un environnement vierge (§12).
- **`README.md`** : passe à `4.1.0`, deux variantes, chacune sous contraintes. **Deux**
  commandes `pip` (la v1 disait trois) :

  ```bash
  python3.11 -m venv ~/.memory-os/venv
  V=~/.memory-os/venv/bin
  $V/pip install --no-deps "memory-os-ai==4.1.0"
  $V/pip install -c "$($V/memory-os-constraints lexical)" "memory-os-ai[ops,brief]==4.1.0"
  $V/memory-os-admin quickstart --profile lexical --clients claude-code,codex          # aperçu
  $V/memory-os-admin quickstart --profile lexical --clients claude-code,codex --apply --download-approved --demo
  ```

  Variante sémantique : `memory-os-constraints semantic`,
  `"memory-os-ai[ops,brief,semantic]==4.1.0"`, `--profile semantic`. Le texte dit ce que chaque
  profil télécharge, décrit `uninstall` (« never deletes » les données, retrait manuel du
  reste) et ne cite plus aucune installation `4.0.9`. Les deux liens d'installation épinglent
  `memory-os-ai[ops,brief]==4.1.0`. Emplacement du venv : `~/.memory-os/venv`, inchangé
  (décision technique, R6).
- **`server.json`** : confirmé, inchangé (relais seul, `[ops,brief]`, aucune variable secrète).
- **Bancs : hors périmètre.** `v4/bench/e2e/configuration.py:37` sérialise `retrieval_profile` ;
  un plan gelé (`mps_reuse_campaign.py:110`) peut être refusé. À traiter avec H2.

## 10. Durée d'installation (clause D1) — définie ici, non mesurée

- **Instrumentation** : `install.json["timing"] = {"profile", "download_bytes", "steps_ms":
  {"sqlite", "resources", "token", "service", "service_ready", "clients", "checks"[, "demo"]},
  "total_s", "clock": "monotonic"}`. Toutes les durées viennent de l'horloge injectée
  (`clock=`). Le test utilise une **horloge déterministe** que seules les étapes simulées font
  avancer (sonde 10 ms, ressources 2 000, readiness 3 000, CLI `claude` 40, `doctor` 500, sonde
  du relais 700) et exige les valeurs exactes par étape et un total de 6,25 s : des durées
  nulles ou inventées échouent. `service` couvre l'écriture du plist et `bootstrap` ;
  `service_ready` l'attente de readiness.
- **Protocole de mesure (ultérieur, sous GO)** : machine ou compte vierge, réseau réel,
  commandes du README. T0 = `python3.11 -m venv` ; T1 = `quickstart … --apply --demo` rendant
  `ok: true`. Rapportés : total, durée de `pip` (mesurée à part), étapes, octets téléchargés,
  « temps hors modèle ». La clause D1 reste à re-signer pour le profil lexical (R2).

## 11. Codes de refus

| Code | Où | Sens |
|---|---|---|
| `setup_profile_required` · `setup_profile_unknown` · `setup_profile_conflict` · `setup_profile_backend_conflict` | aperçu | §2.1 |
| `service_profile_mismatch` | application | le service démarré n'a pas le profil attendu |
| `sqlite_runtime_unavailable` · `sqlite_runtime_too_old` · `sqlite_json_missing` · `sqlite_wal_unavailable` | aperçu, application, `doctor` | §3 |
| `sqlite_runtime_unattested` | application | le serveur ne publie pas une version SQLite lisible |
| `runtime_unqualified` · `build_unqualified` · `disk_guard` | aperçu | runtime, identité de construction, espace |
| `client_unknown` · `client_config_manual` · `client_entry_conflict` · `client_consent_unselected` · `client_app_running` · `client_cli_missing` | aperçu | §5 |
| `service_foreign` · `port_or_label_occupied` · `store_not_empty` | aperçu | service étranger, port ou libellé pris, store non vide |
| `service_not_loaded` | aperçu | plist possédé mais job absent (installation interrompue) |
| `preflight_refused` | application, désinstallation | plan refusé présenté à l'application |
| `service_bootstrap_failed` · `service_startup_failed` · `doctor_failed` · `relay_probe_failed` | application | étape en échec, diagnostics du §8 |
| `service_stop_failed` · `service_stop_timeout` · `service_stop_unconfirmed` · `service_changed_since_install` | retour arrière, désinstallation | §6 |
| `journal_unsafe` | retour arrière | journal non privé ou chemin non sûr |
| `uninstall_service_foreign` · `uninstall_changed_since_preview` · `uninstall_partial` | désinstallation | §7 |

Les phrases de `blockers` restent ; `blocker_codes` est la liste parallèle. Tout refus d'aperçu
laisse l'inventaire de la machine inchangé.

## 12. Critères de sortie de la phase 1a-setup, vérifiables

| Critère | Vérification |
|---|---|
| Nouveaux tests | les sept fichiers du §13 verts (321 tests), sans `skip` ni `xfail` |
| Non-régression | les huit fichiers du §14 verts après les adaptations listées ; suite `v4/tests` : mêmes échecs que le relevé de G1a (`.local-baseline/v4_failures_cd83511.txt`) |
| Zéro écriture en aperçu | tests d'inventaire (installation, désinstallation) |
| D2 | retour arrière, désinstallation, arrêts en échec, vrai store, secrets (§13) |
| Fichiers intouchés | `embed.py` (SHA-256 `c2c335c4…5d60a7`), `sqlite_embeddings.py`, migrations, ressources sémantiques : `git diff cd83511` vide |
| Aucun ML en lexical | relais et CLI d'administration importés avec les modules ML bloqués |
| Localisateur sur wheel installé | wheel construit ; venv vierge **hors `HOME` réel** ; `pip install --no-deps <wheel>` (sans réseau) ; `memory-os-constraints lexical` rend un fichier existant sous `site-packages` ; aucun paquet tiers installé |
| Preuve d'installation | venv vierge, installation lexicale sous contraintes, `quickstart --profile lexical` en aperçu puis `--apply` dans un `HOME` jetable avec un libellé launchd de test — opération manuelle sous GO de Romain, hors suites |
| Revue | verdict Codex « VALIDÉ » |

## 13. Tests

321 tests dans sept fichiers ; avant implémentation, **82 verts** (comportement de `cd83511`, à
conserver) et **239 rouges par construction**, sans `skip` ni `xfail`. `ruff check` : aucun
défaut. Module d'appui `setup_fixture_410.py` :

- HOME temporaire avec les six clients (entrées étrangères, commentaires, indentations variées,
  secrets sentinelles) ;
- `FakeSystem`, le `run=` injecté : `launchctl`, `pgrep`, CLI `claude`, toute autre commande
  refusée. Il sait **échouer** : `bootout` en échec, en délai dépassé, ou « réussi » avec un job
  qui reste chargé ; `bootstrap` en échec ; job remplacé ; service qui redémarre en boucle ;
  bruit contenant des secrets sur stdout/stderr ; crochets exécutés pendant une commande ;
- `FakeClock`, horloge déterministe ;
- `write_memories` / `read_memories` : **vrai moteur, profil lexical, sans ML**, sur le store
  de l'installation simulée — transactions réelles, réouverture, recherche.

| Fichier | Verts | Rouges | Objet |
|---|---|---|---|
| `test_sqlite_runtime_410_setup.py` | 2 | 26 | SQLite chargé vérifié |
| `test_setup_profile_410_setup.py` | 6 | 56 | profil, référence figée, readiness, horloge, `doctor`, CLI |
| `test_setup_clients_410_setup.py` | 59 | 61 | six clients, formats, conflits, identité de relais |
| `test_setup_lifecycle_410_setup.py` | 4 | 36 | D2 : aperçu, sauvegarde, secrets du chemin nominal, retour arrière, désinstallation |
| `test_setup_recovery_410_setup.py` | 1 | 24 | arrêts en échec, installation interrompue, conflits tardifs, **vrai store** |
| `test_setup_secrets_410_setup.py` | 5 | 16 | secrets sur les chemins d'échec |
| `test_setup_release_tooling_410_setup.py` | 5 | 20 | README, `server.json`, portes de résolution (`main()` compris), localisateur |

**Verts (82).**

- SQLite (2) : plancher moteur ; interpréteur de qualification conforme.
- Profil (6) : plan de la table seule ; **référence figée** du service sémantique (document
  littéral et SHA-256) ; readiness sémantique acceptée ; service lexical refusé par la
  readiness sémantique ; dérive d'encodeur vue par `doctor` ; profil inconnu refusé par la CLI.
- Clients (59) : registre ; notre entrée sans secret (6) ; création (5) ; sauvegarde (6) ;
  seconde exécution (6) ; fichier choisi seul ; Claude Code par sa CLI ; échec de la CLI ;
  Codex : préfixe, entrée existante jamais réécrite (3), tableau de tables, ajouts valides (3),
  modification ultérieure conservée, entrée identique `unchanged`, relais périmé toujours
  manuel ; JSON : indentation (2), octets après `undo` (2), serveur ajouté conservé (4), JSONC ;
  **relais de cette installation** mis à jour sans consentement ; fichier réécrit (5) ; aucune
  configuration étrangère exposée (6).
- Cycle de vie (4) : aperçu client ; sauvegarde = image d'avant ; `undo` client sans effet sur
  les données ; aucun paquet `.mcpb` livré.
- Reprise (1) : le banc de preuve sur vrai store fonctionne aujourd'hui, sans ML.
- Secrets (5) : échec de la CLI `claude` réduit à son code ; `doctor` réel sur fichiers
  hostiles ; exception étrangère réduite à sa classe par la CLI (3).
- Outillage (5) : `server.json` ; relais et CLI sans ML ; contraintes empaquetées ; porte
  sémantique inchangée.

**Rouges (239), et ce que chacun prouvera.**

- `test_sqlite_runtime_410_setup.py` (26) : rapport sans écriture ; constante unique ; trois
  versions refusées, trois acceptées ; JSON absent ; WAL non compilé ; bibliothèque non
  chargeable ; connexion impossible ; priorité ; sonde WAL confinée (et sa limite déclarée),
  puis refusée ; `quickstart` refusé avant toute écriture, quatre codes × deux profils (8) ;
  `jsonl` non bloqué ; sonde avant jeton et service ; contrôle `doctor` « this interpreter ».
- `test_setup_profile_410_setup.py` (56) : profil obligatoire, inconnu, `jsonl` ; profil
  consigné (2) ; aucun choix d'après les paquets ; catalogue du profil (2) ; dérive lexicale ;
  ressources par profil (2) ; service lexical ; environnement accepté par `Config.from_env()` ;
  **octets figés avec `profile="semantic"`** ; **service lexical = référence figée moins les
  variables documentées** ; plan sémantique égal à l'héritage ; profil relu (2) ; conversion
  refusée (2) ; readiness lexicale ; **version SQLite du serveur exigée** (8 : deux profils ×
  ancienne, très ancienne, absente, illisible) ; **capacités réelles publiant la version** ;
  huit altérations lexicales ; installation lexicale de bout en bout ; **chronométrage à
  horloge déterministe** ; CLI (4) ; `doctor` lexical, dépendance manquante, marqueur
  malformé, profil d'installation (5), **SQLite du serveur distinct de celui de la commande** (4).
- `test_setup_clients_410_setup.py` (61) : table en ligne Codex (2) ; candidat analysé ; octets
  après `undo` Codex ; indentation (2) ; sans saut de ligne final ; octets après `undo` JSON
  (2) ; clés dupliquées ; entrée étrangère non remplacée (15) ; remplacement consenti (9) ;
  consentement non transmissible ; hors sélection ; **identité d'installation** ; **identité
  nommée par le plan** ; **relais d'identité différente ou inconnue non remplacé** (15 : trois
  clients × cinq cas) ; **remplacé avec consentement nommé** (5) ; journal sans entrée en
  clair ; `undo` depuis la sauvegarde ; sauvegarde altérée.
- `test_setup_lifecycle_410_setup.py` (36) : aperçu complet (2), refusé (2) ; dix refus à code
  stable ; six clients configurés et sauvegardés ; aucun secret dans journaux, plist, CLI,
  refus (3) ; **secret préexistant présent dans la seule sauvegarde** ; `undo` par défaut ;
  retour arrière avec service ; service non créé par l'installation ; service modifié ;
  désinstallation (12 : aperçu, retrait, octets d'origine, sauvegardes et secrets, entrées
  étrangères, idempotence, service étranger, Desktop ouvert, **refus global si changement
  depuis l'aperçu**, **jamais « complète » et retrait manuel**, absence de purge, CLI) ; store
  conservé non adopté et **absence d'option `--reuse-store`**.
- `test_setup_recovery_410_setup.py` (24) : plist **jamais déplacé** après `bootout` en échec,
  en délai dépassé ou non confirmé (3) ; identité revérifiée avant l'arrêt et absence observée
  après ; job remplacé jamais arrêté ; journal non privé ou chemin non sûr (4) ; installation
  interrompue : preuves laissées, retour arrière sans arrêt, nouvel aperçu refusé (3) ;
  désinstallation : clients avant le service, résultat partiel et reprise après arrêt en échec
  (3), job remplacé après l'aperçu, conflit tardif avec service conservé ; **vrai store** :
  mémoires retrouvées après retour arrière avec service, après désinstallation (store toujours
  inscriptible), après arrêt en échec (3), WAL non fusionné laissé intact.
- `test_setup_secrets_410_setup.py` (16) : échec de démarrage rendu en faits structurés, sans
  queue de log ; log conservé et privé ; sortie de la CLI ; sortie de sous-processus jamais
  copiée (5 : nominal, CLI `claude`, `bootstrap`, arrêt du retour arrière, arrêt de la
  désinstallation) ; détails `doctor` non journalisés (3) ; exception étrangère réduite à sa
  classe avec un code (4) ; refus avec secrets étrangers dans les clients.
- `test_setup_release_tooling_410_setup.py` (20) : README (5) ; localisateur sans dépendance,
  **sur arborescence nue**, un seul profil (3), commande installée ; commande de résolution
  par profil (2), sans défaut ; **`main()` sous contraintes avec profil transmis à `check`**
  (2), **`main()` refusé sans profil**, **`main()` en échec sur un paquet ML** ; catalogue du
  profil ; fermeture ML refusée.

Aujourd'hui les rouges échouent sur des API absentes ou sur un comportement réellement
différent (remplacement sans consentement ni identité, TOML cassé, queue de log journalisée,
détails `doctor` journalisés, indentation, README, porte sans contraintes).

## 14. Tests existants : état et adaptations

État sur `cd83511` avec les nouveaux fichiers présents : `test_quickstart.py`,
`test_ops_doctor.py`, `test_distribution_d1a.py`, `test_qualified_runtime.py`,
`test_doctor_pep440_408.py`, `test_lexical_packaging_410.py`,
`test_lexical_capabilities_410.py`, `test_capabilities_l5.py` : **270 réussites, 0 échec**.
La suite `v4/tests` complète n'a pas été relancée (aucun fichier source modifié).

Règle : rien n'est affaibli ; chaque adaptation est listée dans la revue avec sa cause.

| Test | Adaptation |
|---|---|
| `test_quickstart.py`, `installation()` et appels de `prepare` sur une installation neuve | ajout de `profile="semantic"` ; assertions inchangées |
| `test_quickstart.py::test_crashing_new_service_fails_fast_with_masked_tail_and_attested_stop` | `'AttributeError' in failed['stderr_tail']` devient `failed['exception_types'] == ['AttributeError']` et l'absence de `stderr_tail` ; l'absence du jeton reste exigée — la règle se durcit |
| `test_quickstart.py`, tests lisant `progress['doctor']` | forme `{area, status}` ; aucun n'en lit le détail aujourd'hui |
| `test_quickstart.py`, `ready` synthétiques et `test_ready_requires_actual_retrieval_profile_and_identity` | `store.sqlite_version` ajouté à la réponse valide |
| `test_qualified_runtime.py` (porte réseau) | l'appel reçoit `--profile semantic` ; comparaisons inchangées |
| `test_distribution_d1a.py` (scripts et modules du wheel) | `memory-os-constraints` et `memory_os_constraints` s'ajoutent aux listes attendues |
| `test_capabilities_l5.py`, `test_lexical_capabilities_410.py` | `store.sqlite_version` s'ajoute là où le bloc `store` est comparé en entier |

## 15. Ordre d'implémentation, fichier par fichier (aucun code maintenant)

Étape 2 — SQLite et codes : `ops/sqlite_runtime.py` (nouveau) ; `capabilities.py`
(`store.sqlite_version`) ; `ops/quickstart.py` (`QuickstartError.code`, `blocker_codes`,
`inspect`, sonde WAL) ; `ops/doctor.py` (`_sqlite_runtime`, `server-sqlite-runtime`).

Étape 3 — profil : `ops/quickstart.py` (`profile` dans `prepare`, `_service_plan`, `_ready`,
`apply_install` ; ressources ; `clock` et `timing`) ; `ops/provision.py` **inchangé** ;
`ops/cli.py` (`--profile`, code) ; `ops/doctor.py` (`installation_profile`, `_capabilities`,
`store-profile`). `test_quickstart.py` adapté d'abord. → fichiers SQLite et profil verts.

Étape 4 — clients : `ops/quickstart.py` (identité, consentement nommé, journal sans entrée en
clair, `undo` depuis la sauvegarde, candidat TOML, retrait exact, indentation, clés
dupliquées, `which`) ; `ops/clients.py` (lecture stricte) ; `ops/cli.py`
(`--replace-existing`). → fichier clients vert.

Étape 5 — diagnostics : `ops/quickstart.py` (`startup_failure` structuré, suppression de
`_startup_log_tail`, `doctor` réduit, codes d'étape, sous-processus sans sortie). → fichier
secrets vert, hors cas de désinstallation.

Étape 6 — retour arrière et désinstallation : `ops/quickstart.py` (`undo(with_service=…)`,
séquence d'arrêt, journal de confiance, `service_not_loaded`) ; `ops/uninstall.py` (nouveau) ;
`ops/cli.py` (`uninstall`, `--with-service`). → fichiers cycle de vie, reprise, secrets verts.

Étape 7 — outillage : `v4/src/memory_os_constraints.py` (nouveau), `pyproject.toml` ;
`scripts/qualify_runtime_resolution.py`, `scripts/check_runtime_resolution.py` ; `README.md`,
`docs/CLIENTS.md` ; note de version. → dernier fichier vert.

Étape 8 : revue adverse Codex ; preuves sur paquet installé (§12) sous GO.

Jamais modifiés : `embed.py`, `sqlite_embeddings.py`, migrations, ressources sémantiques,
`sqlite_store.py`, `memory.py`, `config.py`.

## 16. Décisions

**À trancher par Romain** (recommandation en premier) :

- **R1 — Profil d'une installation neuve.** Recommandé : `--profile` obligatoire, aucun défaut.
  Alternative : défaut `lexical` pour l'installateur (une option de moins pour les testeurs de
  D1, mais un choix implicite qui diffère du défaut moteur).
- **R2 — Clause D1** : re-signature pour le profil lexical ; protocole du §10.
- **R4 — Rattachement d'un store conservé.** Recommandé : hors de cette phase (§7) ; le
  rattachement relève du runbook de mise à niveau. Alternative : l'inscrire dans une phase
  ultérieure avec son propre contrat (attestation à la désinstallation, tests positif et de
  discordance d'identité, de profil, de version).
- **R6 — Nom et auteur affichés** d'un éventuel paquet `.mcpb` (sans objet si R7 = C).
- **R7 — `.mcpb`** (§5.4). Recommandé : **C**, reporter ; Claude Desktop par son fichier de
  configuration. Alternatives : B (dépendances embarquées, ≈ 69 Mo, une plateforme), A (`uv`,
  exige la publication sur PyPI).

**Déjà cadrée** : R3 — aucune purge dans cette phase ; seule une extension ultérieure
relèverait de Romain.

**Techniques, tranchées ici** :

- **R5 — Amorçage sous contraintes** : adopté. Deux commandes `pip` (`--no-deps`, puis sous
  contraintes) et le localisateur. Une installation sans contraintes est « compatible », pas
  « qualifiée » : `quickstart` la refuse si une version installée diffère du verrou. Des
  versions conformes qualifient l'état ; elles ne prouvent pas l'usage de `-c`.
- **R6 — Emplacement du venv** : `~/.memory-os/venv`, celui du README actuel.

## 17. Arbitrages Q1–Q8 (réponses de Codex, adoptées)

| | Arbitrage | Où |
|---|---|---|
| Q1 | module autonome à bibliothèque standard ; preuve sur wheel installé sans dépendances | §9, §12 |
| Q2 | manifeste v1 retiré ; version de schéma épinglée, validation indépendante, preuve Desktop ; retrait manuel signalé | §5.4, §7 |
| Q3 | attestation locale suffisante pour une installation neuve ; version déployée publiée par le serveur ; `doctor` distingue les deux | §3 |
| Q4 | sonde WAL à l'application seulement ; limite de volume déclarée | §3 |
| Q5 | refus TOML maintenu ; exemption pour l'entrée strictement identique | §5.1 |
| Q6 | pas de remplacement automatique d'un relais d'identité inconnue ou différente | §5.2 |
| Q7 | journal privé, chemins sûrs, empreinte, identité du job revérifiée, arrêt confirmé avant déplacement | §6 |
| Q8 | refus global si conflit avant mutation ; résultat partiel explicite et service conservé si conflit tardif | §7 |

## 18. Hors périmètre

Linux et Python 3.12–3.13 (phase 1b) ; publication, PyPI, GitHub, registre MCP ; mesure H2 ;
mesure de D1 ; bancs ; clients hors des six ; purge ; paquet `.mcpb` (sous réserve de R7) ;
rattachement d'un store conservé (sous réserve de R4) ; migration ou conversion de profil ;
mise à niveau d'une installation 4.0.x.

## 19. Verdict Codex sur la v1 : point par point

| Point du verdict | Changement | Section · tests |
|---|---|---|
| MCPB : `python` sans dépendances, hors spécification | manifeste retiré ; options A/B/C chiffrées, recommandation C, décision R7 ; version épinglée, validation par schéma officiel, preuve Desktop | §5.4, §16 · `test_no_bundle_is_shipped_by_this_phase` ; fichier `test_setup_mcpb_410_setup.py` supprimé |
| Extension active après `uninstall` non signalée | `manual_removal_required`, `complete: False`, procédure Desktop | §7 · `test_uninstall_never_claims_to_be_complete_and_names_what_it_cannot_see` |
| Propriété du relais | identité d'installation ; relais d'identité différente ou inconnue → consentement nommé ; test « ours » réécrit avec identité | §5.2 · `test_installation_identity_…`, `test_relay_entry_of_this_installation_…`, `…_unknown_or_other_identity_…` (15), `…_only_with_named_consent` (5) |
| Secrets en échec | diagnostics structurés à liste blanche ; `stderr_tail` supprimé ; sentinelles jeton, clé, secret étranger dans log, sous-processus, `doctor`, exceptions | §8 · `test_setup_secrets_410_setup.py` (21) |
| Arrêt : `bootout` échoué, délai, job remplacé, installation interrompue | tableau d'issues ; `FakeSystem` sait échouer ; plist jamais déplacé sans arrêt confirmé | §6, §7 · `test_setup_recovery_410_setup.py` |
| Vrai store SQLite, nouvelles transactions, réouverture | moteur réel en profil lexical ; recherche après retour arrière, désinstallation, arrêt en échec ; WAL non fusionné | §6, §7 · quatre tests `test_real_store_…` (6 cas) |
| « Toute écriture via `_atomic_write` » | tableau « qui écrit quoi » ; exception Claude Code énoncée | §4 |
| « Une sauvegarde ne contient jamais le jeton » | corrigé : secrets préexistants autorisés dans les seules sauvegardes privées | §8 · `test_a_pre_existing_secret_survives_only_in_the_private_backup` |
| `main()` de la porte non testé | `main()` avec exécuteur simulé, profil jusqu'à `check`, refus sans profil, échec sur paquet ML | §9 · trois tests `test_resolution_gate_main_…` (4 cas) |
| Environnement sémantique comparé à lui-même | référence littérale indépendante et SHA-256 des octets | §2.5 · `test_semantic_service_definition_equals_the_frozen_4_0_9_reference` et deux tests associés |
| Durées nulles acceptées | horloge injectée déterministe, valeurs exactes | §10 · `test_install_journal_carries_the_timing_needed_by_clause_d1` |
| `--reuse-store` : refus systématique accepté | sorti du périmètre, justifié ; absence de l'option testée | §7, R4 · `test_reinstall_on_a_kept_store_is_refused_and_no_reattach_option_exists` |
| Manifeste conforme aux seules constantes maison | plus de manifeste ; exigence de schéma officiel fixée pour la suite | §5.4 |
| Faux fichiers SQLite | preuves sur vrai store ajoutées (les tests d'octets restent) | §13 |
| Q1–Q8 | réponses adoptées | §17 |
| R1–R6 | Romain : R1, R2, R4, R6 (nom, auteur), R7 ; techniques tranchées : R5, R6 (venv) ; texte de R5 corrigé | §16, §2.2 |
