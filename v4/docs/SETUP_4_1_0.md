# Installation multi-clients 4.1.0 — contrat (phase 1a-setup, étape 1)

Statut : **contrat v1, écrit avant tout code d'outil**. Base `cd83511` (`feature/4.1.0-lexical`,
porte G1a atteinte), branche `feature/4.1.0-setup`. Plan de référence :
`PLAN_STRATEGIQUE_OEM_v3_1.md`, « Phase 1a-setup » et annexe A, clause D2 (« rollback conservant
les nouvelles écritures ; désinstallation conservant les données ; aucun secret journalisé »),
précisée par `DISTRIBUTION_PLAN_COMMUN_v1.md:35` (« Désinstallation conserve les données par
défaut ; purge séparée explicitement confirmée. Rollback après nouvelles écritures n'est jamais la
remise en service d'une vieille copie »). Ce document ne modifie aucun fichier sous `v4/src/`,
`src/` ni `scripts/`. Il est livré avec ses tests (§13), rouges par construction là où le
comportement n'existe pas, verts là où ils épinglent un comportement à conserver. Aucune
installation, aucun `--apply`, aucun appel `launchctl` ou `claude` réel, aucun téléchargement
n'a été exécuté pour l'écrire. Plateforme visée : macOS ARM64, CPython 3.11. Chemins d'outils
relatifs à `v4/src/memory_os_v4/ops/`.

## 1. Faits de code constatés sur `cd83511`

- **`quickstart.py`** fait déjà : aperçu client sans écriture (`preview`), préparation complète
  en lecture seule (`prepare`), application journalisée (`apply_install`, `apply_clients`),
  sauvegarde de chaque fichier client avant écriture (`<journal>/<client>.bak`, 0600 dans un
  dossier 0700), annulation sémantique de notre seule entrée (`undo`), refus d'un service
  étranger, consentement de téléchargement séparé. Le jeton reste dans son fichier 0600 : les
  configurations clientes ne reçoivent que son chemin (`clients.relay_command`).
- **Aucun profil.** `prepare` appelle `provision.plan()` (Granite + table),
  `inspect_current(extras=('ops','brief'))` (catalogue sémantique) ; `_service_plan` écrit
  toujours `MEMORY_V4_EMBED_MODEL`, `MEMORY_V4_DEVICE`, `MEMORY_V4_EMBED_*` ; `_ready` exige la
  révision Granite et le runtime d'encodage. Or `Config.validate_retrieval_profile()` refuse en
  profil lexical toute valeur non par défaut de `embed_model`, `device`, `embed_dtype`, etc.
  (`LEXICAL_DEFAULT_ONLY`) : le plist actuel ne peut pas servir un moteur lexical.
- **`doctor.py::_capabilities`** lit `caps["encoding"]["profile"]["device"]` : sur un service
  lexical (`profile: None`) il lève `TypeError` et rend « malformed deployed capability
  contract » (FAIL). `doctor.run` inspecte son propre runtime avec le catalogue sémantique :
  dans un environnement lexical il échoue sur chaque paquet ML absent.
- **SQLite.** Le plancher `MIN_SQLITE = (3, 51, 3)` n'est vérifié qu'à l'ouverture du store
  (`sqlite_store.py:300`) et par la sauvegarde (`sqlite_backup.py:108`), donc après la création
  du jeton et du service. Le store utilise `json_extract` (index et déclencheurs compris) et
  `PRAGMA journal_mode=WAL` ; il n'utilise **aucune** table FTS (le BM25 est en mémoire).
  L'interpréteur de qualification embarque SQLite 3.53.4.
- **Conflits.** Pour les clients JSON, une entrée `memory-os-ai` existante de classe
  `direct-http`, `legacy-v3` ou `other` reçoit l'action `replace` et est **remplacée par un
  simple `--apply`**. `journal.json` conserve `previous_entry` **en clair** : un en-tête
  `Authorization: Bearer …` d'une ancienne entrée HTTP directe y est donc journalisé.
- **TOML (Codex).** Écriture par ajout en fin de fichier quand l'entrée est absente, refus
  manuel quand elle existe. Le candidat n'est pas analysé avant l'écriture : avec
  `mcp_servers = { … }` (table en ligne), l'ajout de `[mcp_servers.memory-os-ai]` produit un
  fichier **invalide** (`Cannot declare ('mcp_servers', 'memory-os-ai') twice`) ; le contrôle
  après écriture le constate, le fichier reste cassé (la sauvegarde existe). Après `undo`, le
  fichier garde une ligne vide de plus que l'original.
- **JSON.** Réécriture complète en `indent=2` : un fichier indenté à quatre espaces ou en
  tabulations change de forme ; des clés dupliquées sont fusionnées sans avertissement.
- **Annulation.** `undo` ne touche que les clients ; `retained_on_undo` =
  `resources, token, service, store`. Aucune commande de désinstallation n'existe. Après retrait
  manuel du service, `prepare` refuse tout store non vide (« use the qualified upgrade
  runbook ») : un store conservé ne peut pas être rattaché.
- **Refus.** Les blocages sont des phrases libres (`blockers`) ; la CLI rend un code unique
  `quickstart_refused`.
- **Outillage.** `scripts/qualify_runtime_resolution.py:47` résout `<wheel>[ops,brief]` sans
  `-c`, puis `check_runtime_resolution.check` compare au catalogue **sémantique** : depuis
  l'extra `[semantic]`, cette porte ne peut plus passer et ne vérifie aucun verrou.
  `README.md:36` installe `memory-os-ai[ops,brief]==4.0.9` ; ses deux liens d'installation
  (Cursor, VS Code) épinglent aussi `4.0.9`. `server.json` (déjà `4.1.0`) ne lance que le relais.
- **Import du paquet.** `import memory_os_v4` charge `numpy` (`__init__` importe `MemoryV4`) :
  aucune commande du paquet n'est exécutable après `pip install --no-deps`. Les fichiers de
  contraintes sont **dans** le wheel (`resources/*.txt`).
- **Isolation des tests.** `conftest.py` redirige `HOME` avant collecte
  (`INCIDENT_TEST_HOME_2026_10_11.md`). Les fonctions de `quickstart` ont `run=subprocess.run`
  comme valeur par défaut liée à la définition : un test qui n'injecte pas `run` exécute les
  vraies commandes.

## 2. Profil d'installation

### 2.1 Sélection

Le profil d'installation est celui du moteur : `semantic` ou `lexical`
(`config.RETRIEVAL_PROFILES`). Trois règles, cohérentes avec le contrat moteur
(`LEXICAL_PROFILE_4_1_0.md` §8 : défaut moteur `semantic`, lexical explicite, aucun repli).

1. **Installation neuve : `--profile` est obligatoire.** Sans lui, l'aperçu rend
   `ok: false`, `blocker_codes: ["setup_profile_required"]`, `profile: null`, et n'écrit rien.
   L'installateur n'a pas de profil par défaut : le défaut moteur (`semantic`) ne devient pas un
   choix d'installation implicite, et le lexical n'est pas imposé non plus. *Proposition, à
   arbitrer par Romain* (§16, R1).
2. **Jamais d'après les paquets installés.** `--profile semantic` sans la fermeture ML rend
   `runtime_unqualified` (état `missing_dependency`) dont le message nomme
   `memory-os-ai[ops,brief,semantic]` et `qualified_runtime_constraints_v1.txt` ; le profil du
   plan reste `semantic`. `--profile lexical` avec des paquets ML présents est accepté : le
   catalogue lexical ne les exige ni ne les inspecte.
3. **Installation existante : le profil se lit, il ne se convertit pas.** Service possédé
   (`service.state == "owned"`) : le profil est celui du plist
   (`MEMORY_V4_RETRIEVAL_PROFILE`, absent = `semantic`). Sans `--profile`, il est repris. Un
   `--profile` différent est refusé : `setup_profile_conflict`, sans écriture.

Autres refus de sélection : `setup_profile_unknown` (valeur hors des deux profils ; l'analyse
de la ligne de commande la refuse déjà, code de sortie 2) ; `setup_profile_backend_conflict`
(`--profile lexical --backend jsonl` : le lexical exige `sqlite_s1`).

### 2.2 Ce que change chaque profil

| | `lexical` | `semantic` |
|---|---|---|
| Installation qualifiée | `pip install -c qualified_runtime_constraints_lexical_v1.txt "memory-os-ai[ops,brief]==4.1.0"` | `pip install -c qualified_runtime_constraints_v1.txt "memory-os-ai[ops,brief,semantic]==4.1.0"` |
| Runtime inspecté | `inspect_current(profile="lexical", extras=("ops","brief"))` | `inspect_current(profile="semantic", extras=("ops","brief"))` |
| Ressources | `provision.plan(resources=("cl100k",))` : table du tokenizer seule (1 681 126 octets) | `provision.plan()` : Granite + table (221,9 Mo), inchangé |
| Téléchargement | `--download-approved` reste exigé si la table manque ; **aucun** accès à `huggingface.co` | inchangé |
| Environnement du service | `MEMORY_V4_RETRIEVAL_PROFILE=lexical` ; **aucune** variable d'encodeur (`MEMORY_V4_EMBED_MODEL`, `_EMBED_MAX_SEQ_LENGTH`, `_EMBED_DTYPE`, `_EMBED_BATCH_SIZE`, `MEMORY_V4_DEVICE`, `HF_HUB_CACHE`) | **identique à l'octet près à la 4.0.9** : pas de variable de profil ajoutée, sinon un plist 4.0.9 possédé deviendrait « foreign-or-different » |
| Backend | `sqlite` seul | `sqlite` (défaut) ou `jsonl` |
| Readiness exigée | marqueur `retrieval_profile == LEXICAL_MARKER`, `policies.ranking_policy == "lex-4.1.0-v1"`, `bm25_profile == "scoped_literal"`, `lexical_guard_k == 3`, `encoding.state == "absent_by_profile"`, tokenizer prêt ; ni runtime d'encodage ni révision Granite | contrat 4.0.9 inchangé |

Un service qui ne répond pas exactement au profil attendu est refusé avant toute écriture
cliente : `service_profile_mismatch`.

### 2.3 Signatures (ajouts ; les appelants actuels restent valides)

- `quickstart.prepare(settings, *, clients, home, python, backend, run, profile=None,
  replace_existing=(), reuse_store=False, which=shutil.which)` ; le plan gagne `profile`,
  `blocker_codes` (liste parallèle à `blockers`, même longueur, même ordre) et `sqlite_runtime`.
- `quickstart._service_plan(..., *, backend=None, profile="semantic")`.
- `quickstart._ready(settings, identity, *, backend="sqlite", profile="semantic")`.
- `quickstart.QuickstartError(message, code="quickstart_refused")`, attribut `code`.
- `quickstart.apply_install` : revalide avec le profil du plan ; le journal `install.json`
  gagne `profile`, `code` (en cas d'échec), `timing` (§10) ; `ops_env` porte
  `MEMORY_V4_RETRIEVAL_PROFILE` en lexical.
- CLI : `quickstart --profile {lexical,semantic}` ; sans l'option, `profile=None` est transmis
  (jamais une valeur par défaut) ; un refus rend le `code` de l'erreur.

### 2.4 `doctor`

- `doctor.installation_profile(info, environ) -> (profil, source)` : service joignable →
  profil déployé (`capabilities.retrieval_profile.name`), source `"deployed"` ; sinon
  `MEMORY_V4_RETRIEVAL_PROFILE`, source `"environment"` ; sinon le défaut moteur `semantic`,
  source `"engine-default"`, **affichée** dans le détail du contrôle (une supposition visible,
  pas un repli muet). Le contrôle `local-runtime` inspecte le runtime avec ce profil.
- `_capabilities` : un déploiement lexical est accepté. `encoder-runtime` a trois issues :
  PASS « absent by profile », FAIL « missing dependency » (correctif :
  `memory-os-ai[ops,brief,semantic]`), FAIL « drift ». `server-runtime` compare avec le
  catalogue du profil déployé et le nomme. Nouveau contrôle `store-profile` : PASS avec la
  politique, FAIL si le marqueur n'est pas exactement `LEXICAL_MARKER` ou
  `{"name": "semantic", "policy": None}`.
- Nouveau contrôle `sqlite-runtime` (§3).

## 3. SQLite chargé vérifié

Nouveau module `ops/sqlite_runtime.py`, sans effet de bord, importable sans ML.

- `MIN_VERSION` **est** `sqlite_store.MIN_SQLITE` (une seule constante).
- `inspect(*, module=None) -> dict` : vérifie la bibliothèque **chargée par cet interpréteur**
  (`import sqlite3` paresseux ; ce n'est pas l'outil `sqlite3` de la ligne de commande). Il
  n'ouvre qu'une base `:memory:` et n'écrit rien.

  ```
  {"ok": bool, "code": None | <code>, "version": "3.53.4" | None, "minimum": "3.51.3",
   "checks": {"loaded": bool, "version": bool, "json1": bool, "wal_compiled": bool},
   "fts5": bool, "writes": "none"}
  ```

  | Ordre | Contrôle | Méthode | Code de refus |
  |---|---|---|---|
  | 1 | chargement | `import sqlite3`, `connect(":memory:")` | `sqlite_runtime_unavailable` |
  | 2 | version | `sqlite_version_info >= MIN_VERSION`, comparaison par triplet d'entiers | `sqlite_runtime_too_old` |
  | 3 | fonctions JSON | `SELECT json_extract('{"a": 7}', '$.a')` rend 7 | `sqlite_json_missing` |
  | 4 | WAL compilé | `PRAGMA compile_options` ne contient pas `OMIT_WAL` | `sqlite_wal_unavailable` |

  Le premier contrôle en échec fixe le code. `fts5` est **informatif** : le store n'a aucune
  table FTS, l'exiger refuserait à tort des interpréteurs valides.
- `wal_probe(directory, *, module=None) -> {"ok", "code", "journal_mode"}` : preuve
  fonctionnelle, **à l'application seulement**. Crée une base jetable dans `directory` (le
  dossier de journal 0700 que l'application vient de créer), demande `journal_mode=WAL`, exige
  la réponse `wal`, ferme, supprime la base et ses fichiers `-wal`/`-shm`. Refus :
  `sqlite_wal_unavailable`. Elle s'exécute **avant** les ressources, le jeton et le service.
- `prepare` appelle `sqlite_runtime.inspect()` par l'attribut du module. Backend `sqlite` (donc
  tout profil lexical) : un rapport non `ok` ajoute son code aux blocages. Backend `jsonl` : le
  rapport figure dans le plan sans bloquer.
- Portée de l'attestation : la CLI n'a pas d'option `--python` ; l'interpréteur vérifié est
  celui qui exécute `memory-os-admin`, et c'est lui qui est écrit dans le plist et dans les
  entrées clientes. Le paramètre `python=` de la bibliothèque reste un point d'injection de test.

## 4. Aperçu, application, sauvegarde

- **Aperçu = zéro écriture, prouvé par inventaire.** Les tests comparent, avant et après,
  l'inventaire complet de la racine temporaire (chaque chemin, son mode, son `mtime_ns`, ses
  octets), `HOME` compris, pour `prepare` (deux profils, six clients, cas refusés compris),
  `uninstall.plan` et l'aperçu `mcpb`. Commandes autorisées en aperçu : `launchctl print` et
  `pgrep -x Claude` ; ni `claude`, ni `bootstrap`, ni `bootout`. Lecture de `shutil.which("claude")`.
- **Application = `--apply` explicite**, précédée d'une revalidation complète ; ordre des
  étapes : `sqlite` (sonde WAL) → `resources` → `token` → `service` → `service_ready` →
  `clients` → `checks` → `demo` (sur demande).
- **Sauvegarde** : chaque fichier client existant est copié à l'identique dans le journal de
  l'opération avant d'être modifié (0600, dossier 0700), Claude Code compris (`~/.claude.json`,
  bien que l'écriture passe par sa CLI). Un fichier non modifié (`unchanged`, refus, conflit)
  n'est pas copié. Le journal reçoit `backup_sha256` et, pour une entrée remplacée,
  `previous_class` et `previous_entry_sha256` — jamais l'entrée (§8).
- **Point d'écriture unique** : toute écriture d'un fichier client ou d'un journal passe par
  `quickstart._atomic_write` (fichier temporaire, `fsync`, `os.replace`, mode conservé).
- **Codes** : chaque refus a un code stable (§11) ; `apply_install` sur un plan refusé lève
  `QuickstartError(code="preflight_refused")`.

## 5. Clients

Périmètre qualifié : **Claude Code, Claude Desktop, Codex, Cursor, VS Code, Antigravity**. Les
autres clients du registre (Gemini CLI, Grok, Zed, Devin, Windsurf) gardent le comportement
4.0.9 et ne sont pas déclarés qualifiés par cette phase.

### 5.1 Par client

| Client | Fichier (sous `$HOME`) | Format | Écriture | Entrée existante non relais |
|---|---|---|---|---|
| Claude Code | `.claude.json` | JSON, clé `mcpServers` | par `claude mcp add --scope user` (la CLI possède le fichier) ; CLI absente → `client_cli_missing` dès l'aperçu | `claude mcp remove` puis `add`, seulement avec consentement nommé |
| Claude Desktop | `Library/Application Support/Claude/claude_desktop_config.json` | JSON, `mcpServers` | fusion de notre clé, application fermée ; ou paquet `.mcpb` (§5.2) | consentement nommé |
| Codex | `.codex/config.toml` | TOML, `[mcp_servers.<nom>]` | ajout en fin de fichier quand l'entrée est absente | **jamais réécrite** : `refuse-toml-existing-manual` avec l'extrait à appliquer |
| Cursor | `.cursor/mcp.json` | JSON, `mcpServers` | fusion de notre clé | consentement nommé |
| VS Code | `Library/Application Support/Code/User/mcp.json` | JSON, `servers`, `"type": "stdio"` | fusion de notre clé | consentement nommé |
| Antigravity | `.gemini/config/mcp_config.json` | JSON, `mcpServers` | fusion de notre clé | consentement nommé |

Propriétés communes, par client :

- **Idempotence** : une entrée déjà égale à la nôtre donne `unchanged` ; aucune écriture,
  aucune sauvegarde, aucune commande.
- **Entrées et réglages étrangers** : conservés à l'identique (égalité des données analysées,
  ordre des clés compris) ; jamais affichés dans un aperçu, un rapport ou un journal.
- **Seul le fichier du client choisi est touché.**
- **Fichier réécrit par son application** entre l'aperçu et l'application (Claude Desktop
  ouvert, éditeur) : `conflict-file-changed-since-preview`, aucune écriture, aucune sauvegarde.
  Claude Desktop en cours d'exécution est en outre un blocage d'aperçu (`client_app_running`),
  dont le message propose la voie `.mcpb`.
- **Notre entrée** : `command` = interpréteur absolu, `args` = `["-m","memory_os_v4.ops.relay"]`,
  `env` = `MEMORY_V4_CLIENT_ID`, `MEMORY_V4_URL`, `MEMORY_V4_TOKEN_FILE`, `MEMORY_V4_OPS_DIR`.
  Aucune valeur secrète.

### 5.2 Conflits : ne jamais écraser en silence

- Classe `relay` avec un contenu différent (autre interpréteur, autre chemin) : action
  `replace`, appliquée par `--apply` (elle est montrée par l'aperçu et sauvegardée).
- Classes `direct-http`, `legacy-v3`, `other` : action `refuse-existing-<classe>`, code
  `client_entry_conflict`. Le remplacement exige un **consentement nommé par client** :
  `--replace-existing cursor,claude-desktop` (`replace_existing=("cursor",…)`). Le consentement
  d'un client ne vaut pas pour un autre ; nommer un client non sélectionné est refusé
  (`client_consent_unselected`). L'ancien fichier est dans la sauvegarde.
- Codex : pas de consentement possible, l'entrée existante reste (refus manuel), faute de
  réécriture TOML sûre sans dépendance nouvelle.

### 5.3 Formats

**TOML (Codex).** Le candidat (texte antérieur + nos deux tables) est **analysé par `tomllib`
avant toute écriture** ; il n'est écrit que si ses données sont égales aux données antérieures
plus notre entrée. Sinon : `refuse-toml-structure-manual` dès l'aperçu (table en ligne
`mcp_servers = { … }`, par exemple), aucune écriture. Les octets antérieurs sont un préfixe du
nouveau fichier (commentaires et mise en forme intacts). `undo` retire exactement ce qui a été
ajouté : si rien d'autre n'a changé, le fichier retrouve ses **octets d'origine** ; s'il a été
modifié depuis, les ajouts ultérieurs sont conservés.

**JSON.** Unité d'indentation détectée et conservée (espaces ou tabulation), présence du saut
de ligne final conservée, ordre des clés conservé, texte non ASCII non échappé. Pour un fichier
dans sa forme canonique, application puis `undo` rendent les octets d'origine. Clés dupliquées :
`refuse-json-duplicate-keys` (jamais de fusion muette). Fichier avec commentaires (JSONC, admis
par VS Code) : `refuse-unreadable`, aucune réécriture.

### 5.4 Claude Desktop : paquet `.mcpb`

Nouveau module `ops/mcpb.py` et commande `memory-os-admin mcpb --output FICHIER [--apply]`.

- **Un seul paquet générique**, identique pour tous les utilisateurs : il ne contient ni
  chemin absolu, ni `HOME`, ni jeton, et ne demande jamais le jeton. Il **lance le relais d'un
  environnement déjà installé** ; il n'embarque ni le paquet ni ses dépendances (le runtime
  qualifié reste celui du venv).
- `mcpb.manifest()` :

  ```
  manifest_version: mcpb.MANIFEST_VERSION
  name: "memory-os-ai"            version: <version du paquet>
  display_name, description, author
  server:
    type: "python"                entry_point: "server/main.py"
    mcp_config:
      command: "${user_config.python}"
      args: ["-m", "memory_os_v4.ops.relay"]
      env: MEMORY_V4_CLIENT_ID = "claude-desktop"
           MEMORY_V4_TOKEN_FILE = "${user_config.token_file}"
           MEMORY_V4_URL = "${user_config.url}"
  user_config:
    python:     type file,   required, défaut "${HOME}/.memory-os/venv/bin/python"
    token_file: type file,   défaut "${HOME}/.config/memory-os/http.token"
    url:        type string, défaut "http://127.0.0.1:8765/mcp/"
  compatibility: platforms ["darwin"], runtimes {"python": ">=3.11,<3.12"}
  ```

  Aucun champ `sensitive` : il n'y a rien de secret à saisir. `server/main.py` est un lanceur
  de bibliothèque standard qui appelle `memory_os_v4.ops.relay.main`.
- **Reproductibilité** : `mcpb.render() -> bytes` produit l'archive en mémoire. Entrées
  exactement `manifest.json`, `server/main.py`, dans cet ordre ; date fixe `1980-01-01 00:00:00` ;
  `ZIP_STORED` (aucune dépendance à la version de zlib) ; attributs `0o100644`, système 3 ;
  ni champ supplémentaire ni commentaire. Deux rendus sont identiques à l'octet près, quels
  que soient l'heure, le fuseau et l'`umask`. Le SHA-256 est rendu par l'aperçu et par
  l'application ; c'est l'empreinte de l'artefact de version.
- `mcpb.build(output)` : écriture exclusive (`mcpb_output_exists` si le fichier existe, jamais
  d'écrasement), destination sûre (`mcpb_output_unsafe` : dossier parent absent ou lien
  symbolique, suffixe autre que `.mcpb`), mode 0644 ; rend `{"ok", "path", "bytes", "sha256",
  "files", "token_in_bundle": False}`. La CLI sans `--apply` rend le manifeste et l'empreinte
  sans rien écrire.
- **Limite assumée** : une extension installée par `.mcpb` est gérée par Claude Desktop, hors
  de `claude_desktop_config.json` ; `doctor` et `uninstall` ne la voient pas (§17, Q2).

## 6. Retour arrière

`quickstart --undo STAMP [--with-service]` ; `undo(stamp, *, home, run, with_service=False)`.

- **Clients** (comportement actuel, conservé) : notre entrée est retirée, ou l'entrée
  précédente rétablie, **seulement** si l'entrée en place est encore celle que nous avons
  écrite ; sinon `refused-entry-changed-since-apply`. C'est un retrait sémantique, jamais la
  remise en place de la sauvegarde : ce que l'utilisateur ou l'application a écrit depuis dans
  le fichier est conservé. L'entrée précédente est relue dans la sauvegarde 0600, après
  contrôle de `backup_sha256` et `previous_entry_sha256` (`refused-backup-changed` sinon).
- **Service** : sans `--with-service`, rien (`{"undo": "not-requested"}`). Avec l'option, le
  service n'est arrêté que si **cette** installation l'a créé (`steps.service.created`), si le
  plist a toujours l'empreinte journalisée et si le job chargé est attesté. Alors : `launchctl
  bootout`, puis le plist est **déplacé** dans le journal de l'installation (jamais supprimé).
  Autres issues : `not-created-by-this-install`, `refused-changed-since-install`.
- **Données : jamais.** Le retour arrière ne supprime, ne restaure ni ne remplace aucun
  fichier du store, des sauvegardes, de la clé, du jeton ni des ressources. Il ne remet jamais
  en service une ancienne copie : `data_restored_from_copy: False`. Tout ce qui a été écrit en
  mémoire depuis l'installation est conservé à l'octet près (`-wal` non fusionné compris). Le
  résultat liste `retained` (store, backups, backup-key, token, resources) avec chemin et
  existence.

## 7. Désinstallation

Nouveau module `ops/uninstall.py` ; commande `memory-os-admin uninstall [--clients …] [--apply]`.

- `uninstall.plan(settings, *, home, python, run, clients=None) -> dict` : lecture seule
  (`writes: "none (preview)"`). `uninstall.apply(plan, settings, *, home, run) -> dict`.
- **Retire** :
  - dans chaque client du registre, l'entrée `memory-os-ai` qui est **le relais de cette
    installation** (classe `relay`, `MEMORY_V4_TOKEN_FILE` et `MEMORY_V4_URL` égaux aux
    réglages) : action `remove`. Toute autre entrée — autre classe, ou relais d'une autre
    installation — est gardée : `keep-foreign` / `kept-foreign`. Mêmes règles qu'à
    l'installation : sauvegarde de chaque fichier touché, retrait de notre seule clé, formats
    conservés (après installation puis désinstallation, les six fichiers retrouvent leurs
    octets d'origine), fichier modifié depuis l'aperçu ignoré
    (`conflict-file-changed-since-preview`, les autres clients sont traités, `ok: false`),
    Claude Desktop ouvert bloquant (`client_app_running`) ;
  - le service launchd **possédé** (libellé, programme `<python> -m memory_os_v4.server`,
    `MEMORY_V4_TOKEN_FILE` et `MEMORY_V4_DIR` égaux aux réglages) : `bootout`, plist déplacé
    dans le journal de désinstallation. Service étranger : `uninstall_service_foreign`, plan
    refusé, rien n'est touché (`UninstallError(code="preflight_refused")`).
- **Ne retire jamais** : store, sauvegardes, clé de sauvegarde, jeton, ressources (table,
  modèle), journaux d'installation et leurs sauvegardes, journaux du relais, logs, venv. Le
  plan et le résultat les listent dans `retained` (`kind`, `path`, `exists`) ;
  `data_deleted: False`. **Il n'existe aucune option de purge** : ni paramètre ni drapeau
  (`--purge`, `--delete-data`, `--force` sont refusés par l'analyse), et le module n'appelle
  aucune suppression récursive. La purge reste une opération manuelle séparée (§16, R3).
- **Idempotence** : une seconde exécution rend `nothing_to_do: True`, service `absent`,
  clients `absent` ou `missing-config`, aucune écriture.
- Signalés sans être touchés : jobs auxiliaires posés à la main par `setup --write-launchd`
  (`.backup`, `.health`) et hooks Claude Code de `~/.claude/settings.json` (`notices`).
- **Store conservé puis réinstallation** : un store non vide n'est jamais adopté en silence.
  Sans option : `store_not_empty`, message nommant `--reuse-store`. Avec `--reuse-store`
  (`reuse_store=True`) : accepté seulement si un journal de désinstallation 0600 atteste ce
  store (même dossier, même `store_instance_id`, même profil, même backend, même version du
  paquet) et si la sonde en lecture seule du store (`LEXICAL_PROFILE_4_1_0.md` §5) le confirme ;
  sinon `store_reuse_unattested`. Toute autre situation relève du runbook de mise à niveau.
  *Périmètre à confirmer* (§16, R4).

## 8. Aucun secret journalisé

Secrets : la valeur du jeton, la clé de sauvegarde, et tout secret étranger présent dans une
configuration cliente (clés d'API d'autres serveurs, en-tête `Authorization` d'une ancienne
entrée, compte de `~/.claude.json`).

| Sortie | Règle |
|---|---|
| Aperçus, rapports, sorties de la CLI (stdout et stderr), messages d'erreur, contrôles de `doctor` | aucun secret ; seulement notre entrée, des chemins, des classes, des empreintes |
| Journaux (`journal.json`, `install.json`, `uninstall.json`) | aucun secret. `previous_entry` **disparaît** du journal : remplacé par `previous_class` et `previous_entry_sha256` |
| Plist du service | le chemin du fichier de jeton, jamais sa valeur |
| Paquet `.mcpb` | ni jeton ni chemin de machine |
| Sauvegardes `*.bak` | **copies intégrales privées**, donc avec les secrets étrangers du fichier d'origine ; 0600 dans un dossier 0700 ; jamais affichées, citées par chemin seulement. Une sauvegarde n'est pas un journal. Elle ne contient jamais le jeton (elle précède sa création et les configurations n'en portent que le chemin) |

Méthode de test : des sentinelles sont plantées (clé d'API étrangère, jeton Bearer d'une entrée
HTTP directe, jeton de compte dans `~/.claude.json`), le vrai jeton est créé par l'installation,
puis chaque sortie est fouillée : résultats, sorties de la CLI (aperçu, rapport, application),
tous les fichiers du dossier d'état hors `*.bak`, plist, messages de refus.

## 9. Outillage de version

- **`scripts/qualify_runtime_resolution.py`** : `resolution_command(python, wheel, output, *,
  profile)` construit la commande ; `profile` est obligatoire, sans défaut. `lexical` →
  `<wheel>[ops,brief]` avec `-c <contraintes lexicales>` ; `semantic` →
  `<wheel>[ops,brief,semantic]` avec `-c <contraintes sémantiques>`. L'option `--profile`
  du script est obligatoire. `scripts/check_runtime_resolution.py::check(..., profile="semantic")`
  compare au catalogue du profil ; en lexical, tout paquet de `ml_closure()` présent dans la
  résolution est un échec, listé dans `ml_packages`. Le défaut `semantic` de `check` conserve
  les appelants actuels. Ces scripts accèdent au réseau : ils ne sont pas exécutés ici.
- **Localisateur de contraintes.** Les fichiers de contraintes sont dans le wheel et aucune
  commande du paquet ne tourne avant l'installation des dépendances (§1). Nouveau module de
  premier niveau `memory_os_constraints` (bibliothèque standard seule, n'importe pas
  `memory_os_v4`, localise la ressource par `importlib.util.find_spec`) et commande
  `memory-os-constraints {lexical|semantic}` qui écrit le chemin absolu du fichier. Exactement
  un profil connu, sinon code 2. *Choix d'empaquetage à confirmer* (§17, Q1).
- **`README.md`** : passe à `4.1.0`, deux variantes, chacune sous contraintes.

  ```bash
  python3.11 -m venv ~/.memory-os/venv
  V=~/.memory-os/venv/bin
  $V/pip install --no-deps "memory-os-ai==4.1.0"
  $V/pip install -c "$($V/memory-os-constraints lexical)" "memory-os-ai[ops,brief]==4.1.0"
  $V/memory-os-admin quickstart --profile lexical --clients claude-code,codex          # aperçu
  $V/memory-os-admin quickstart --profile lexical --clients claude-code,codex --apply --download-approved --demo
  ```

  Variante sémantique : `memory-os-constraints semantic`, `"memory-os-ai[ops,brief,semantic]==4.1.0"`,
  `--profile semantic`. Le texte dit ce que chaque profil télécharge (« no embedding model » ;
  221,9 Mo), décrit `uninstall` (« never deletes » les données) et ne cite plus aucune
  installation `4.0.9`. Les deux liens d'installation (Cursor en base64, VS Code en URL)
  épinglent `memory-os-ai[ops,brief]==4.1.0`. Les mesures de durée citées (4.0.6) sont retirées
  ou gardées comme historiques, sans valeur pour la 4.1.0.
- **`server.json`** : confirmé, inchangé. Il ne lance que le relais
  (`memory-os-ai[ops,brief]==4.1.0`, `memory-os-relay`) : pas d'extra `[semantic]`, aucune
  variable secrète. Le relais et la CLI d'administration s'importent sans aucun module ML.
- **Bancs : hors périmètre.** `v4/bench/e2e/configuration.py:37` sérialise le nouveau champ
  `retrieval_profile` par `asdict(cfg)` ; un plan de campagne gelé comparé à cette
  configuration (`mps_reuse_campaign.py:110`) peut donc être refusé. Cela ne touche ni
  l'installation ni le service ; à traiter avec la mesure H2 ou par une tâche séparée.

## 10. Durée d'installation (clause D1) — définie ici, non mesurée

D1 : « 4/5 en < 10 min, téléchargements compris », sur le profil lexical. Ce que l'installation
lexicale retire du chemin : la fermeture ML (trente paquets, dont `torch`) et Granite
(220 227 876 octets) ; il reste les dépendances du cœur et la table de 1,7 Mo.

- **Instrumentation** : `install.json["timing"] = {"profile", "download_bytes", "steps_ms":
  {"sqlite", "resources", "token", "service", "service_ready", "clients", "checks"[, "demo"]},
  "total_s", "clock": "monotonic"}`.
- **Protocole de mesure (ultérieur, sous GO)** : machine ou compte vierge, réseau réel,
  commandes du README copiées telles quelles. T0 = lancement de `python3.11 -m venv` ; T1 =
  `quickstart … --apply --demo` rendant `ok: true`. Rapportés : durée totale, durée de `pip`
  (mesurée à part : elle n'est pas dans le journal), étapes du journal, octets téléchargés,
  « temps hors modèle » = total moins l'étape `resources`. La clause D1 reste à re-signer pour
  le profil lexical ; aucune durée n'est affirmée par ce contrat.

## 11. Codes de refus

| Code | Où | Sens |
|---|---|---|
| `setup_profile_required` | aperçu | installation neuve sans `--profile` |
| `setup_profile_unknown` | erreur | profil hors `lexical`/`semantic` |
| `setup_profile_conflict` | aperçu | profil demandé ≠ profil du service installé |
| `setup_profile_backend_conflict` | aperçu | `lexical` avec `jsonl` |
| `service_profile_mismatch` | application | le service démarré n'a pas le profil attendu |
| `sqlite_runtime_unavailable` · `sqlite_runtime_too_old` · `sqlite_json_missing` · `sqlite_wal_unavailable` | aperçu, application (sonde WAL), `doctor` | §3 |
| `runtime_unqualified` | aperçu | paquet manquant ou dérive, par rapport au catalogue du profil |
| `build_unqualified` | aperçu | identité de construction non qualifiée |
| `disk_guard` | aperçu | espace libre insuffisant |
| `client_unknown` | aperçu | client hors registre |
| `client_config_manual` | aperçu | action `refuse-*` (illisible, JSONC, TOML existant ou structure, clés dupliquées) |
| `client_entry_conflict` | aperçu | entrée `direct-http`/`legacy-v3`/`other` sans consentement nommé |
| `client_consent_unselected` | erreur | consentement pour un client non sélectionné |
| `client_app_running` | aperçu | Claude Desktop en cours d'exécution |
| `client_cli_missing` | aperçu | CLI `claude` introuvable |
| `service_foreign` | aperçu | plist ou job étranger, différent ou non attesté |
| `port_or_label_occupied` | aperçu | port ou libellé pris |
| `store_not_empty` · `store_reuse_unattested` | aperçu | §7 |
| `preflight_refused` | application | plan refusé présenté à l'application |
| `uninstall_service_foreign` | désinstallation | le service n'est pas le nôtre |
| `mcpb_output_exists` · `mcpb_output_unsafe` | `mcpb` | §5.4 |

Les phrases de `blockers` restent (les tests existants les lisent) ; `blocker_codes` est la
liste parallèle. Tout refus d'aperçu laisse l'inventaire de la machine inchangé.

## 12. Critères de sortie de la phase 1a-setup, vérifiables

| Critère | Vérification |
|---|---|
| Nouveaux tests | les six fichiers du §13 verts (243 tests), sans `skip` ni `xfail` |
| Non-régression | les huit fichiers du §14 verts après les adaptations listées ; suite `v4/tests` : mêmes échecs que le relevé de G1a |
| Zéro écriture en aperçu | tests d'inventaire du §13 (installation, désinstallation, `mcpb`) |
| D2 | tests de retour arrière, de désinstallation et de secrets du §13 |
| Fichiers intouchés | `embed.py` (SHA-256 `c2c335c4…5d60a7`), `sqlite_embeddings.py`, migrations, ressources sémantiques : `git diff cd83511` vide |
| Aucun ML en lexical | relais et CLI d'administration importés avec les modules ML bloqués |
| Preuve sur paquet installé | wheel construit ; environnement vierge **hors `HOME` réel** ; installation lexicale sous contraintes ; `quickstart --profile lexical` en aperçu puis `--apply` dans un `HOME` jetable avec un libellé launchd de test — opération manuelle sous GO de Romain, hors suites |
| Revue | verdict Codex « VALIDÉ » |

## 13. Tests

243 tests dans six fichiers ; avant implémentation, **74 verts** (comportement de `cd83511`, à
conserver) et **169 rouges par construction**, sans `skip` ni `xfail`. Module d'appui :
`setup_fixture_410.py` (HOME temporaire avec les six clients, entrées étrangères, commentaires
et indentations variées, secrets sentinelles ; `FakeSystem`, le `run=` injecté qui simule
`launchctl`, `pgrep` et la CLI `claude` et refuse toute autre commande ; inventaires).

| Fichier | Verts | Rouges | Objet |
|---|---|---|---|
| `test_sqlite_runtime_410_setup.py` | 2 | 26 | SQLite chargé vérifié |
| `test_setup_profile_410_setup.py` | 6 | 41 | profil d'installation, service, readiness, `doctor`, CLI |
| `test_setup_clients_410_setup.py` | 57 | 39 | les six clients, formats, conflits |
| `test_setup_lifecycle_410_setup.py` | 3 | 36 | D2 : aperçu, sauvegarde, secrets, retour arrière, désinstallation |
| `test_setup_mcpb_410_setup.py` | 1 | 12 | paquet `.mcpb` |
| `test_setup_release_tooling_410_setup.py` | 5 | 15 | README, `server.json`, portes de résolution, localisateur |

Commande : depuis `v4/`, `PYTHONPATH=src:../src <python 3.11 du runtime 4.0.8> -m pytest
<fichiers> -q -p no:cacheprovider`. `ruff check` : aucun défaut sur les sept fichiers.

**Verts (74).**

- SQLite (2) : plancher moteur `3.51.3` ; l'interpréteur de qualification a la version, les
  fonctions JSON et le WAL.
- Profil (6) : `provision.plan(resources=("cl100k",))` ne mentionne pas Granite et n'écrit
  rien ; définition du service sémantique figée (vingt variables, aucune de profil) ; readiness
  sémantique 4.0.9 acceptée ; service lexical refusé par la readiness sémantique ; dérive
  d'encodeur signalée par `doctor` ; profil inconnu refusé par l'analyse de la CLI.
- Clients (57) : chemins du registre ; notre entrée sans secret (6) ; création conservant
  entrées étrangères et mode (5) ; sauvegarde à l'identique avant écriture (6) ; seconde
  exécution `unchanged` sans écriture ni commande (6) ; seul le fichier choisi touché ; Claude
  Code par sa CLI ; échec de la CLI ; Codex : préfixe et commentaires conservés, entrée
  existante jamais réécrite (3), tableau de tables refusé, structures où l'ajout reste valide
  (3), modification ultérieure conservée par `undo` ; JSON : indentation à deux espaces (2),
  octets d'origine après `undo` pour deux clients (2), serveur ajouté après l'installation
  conservé (4), JSONC refusé ; relais périmé mis à jour ; fichier réécrit avant l'application
  (5) ; aucune configuration étrangère exposée (6).
- Cycle de vie (3) : aperçu client sans écriture ; sauvegarde = image d'avant ; `undo` client
  sans effet sur store, jeton et sauvegardes.
- `.mcpb` (1) : la cible de lancement est le relais.
- Outillage (5) : `server.json` ; relais sans ML ; CLI d'administration sans ML ; deux fichiers
  de contraintes empaquetés ; porte sémantique inchangée.

**Rouges (169), et ce que chacun prouvera.**

- `test_sqlite_runtime_410_setup.py` (26) : rapport de l'interpréteur courant sans écriture ;
  constante unique ; trois versions trop anciennes et trois acceptées ; fonctions JSON
  absentes ; WAL non compilé ; bibliothèque non chargeable ; connexion impossible ; priorité
  des refus ; sonde WAL confinée à son dossier, puis refusée ; `quickstart` refusé avant toute
  écriture pour les quatre codes dans les deux profils (8) ; `jsonl` informé sans blocage ;
  sonde WAL avant jeton et service ; contrôle `doctor`.
- `test_setup_profile_410_setup.py` (41) : installation neuve sans profil refusée ; profil
  inconnu ; lexical + `jsonl` ; profil consigné (2) ; aucun choix d'après les paquets
  installés ; catalogue du profil (2) ; dérive lexicale nommant ses contraintes ; table seule
  en lexical ; Granite + table en sémantique ; service lexical sans variable d'encodeur ;
  environnement du plist accepté par `Config.from_env()` ; service sémantique identique à
  l'héritage ; profil relu d'un service existant (2) ; conversion refusée (2) ; readiness
  lexicale acceptée, puis huit altérations refusées ; installation lexicale de bout en bout
  sans modèle ; chronométrage D1 ; CLI (4) ; `doctor` lexical, dépendance manquante, marqueur
  malformé, profil d'installation (5).
- `test_setup_clients_410_setup.py` (39) : table en ligne Codex refusée dès l'aperçu (2) ;
  candidat TOML analysé avant écriture ; octets d'origine après `undo` Codex ; indentation
  conservée (2) ; fichier sans saut de ligne final ; octets d'origine après `undo` JSON (2) ;
  clés dupliquées ; entrée étrangère non remplacée sans consentement (15 : cinq clients, trois
  classes) ; remplacement consenti et sauvegarde (9) ; consentement non transmissible ;
  consentement hors sélection ; journal sans entrée précédente en clair ; `undo` depuis la
  sauvegarde ; sauvegarde altérée refusée.
- `test_setup_lifecycle_410_setup.py` (36) : aperçu complet sans écriture (2), refusé sans
  écriture (2) ; dix refus à code stable sans écriture ; voie `.mcpb` proposée ; six clients
  configurés et sauvegardés ; aucun secret dans les journaux, le plist, la CLI, les refus (3) ;
  `undo` par défaut ; retour arrière avec service conservant chaque écriture ; service non créé
  par l'installation ; service modifié ; désinstallation : aperçu, retrait, octets d'origine,
  sauvegardes et secrets, entrées étrangères, idempotence, service étranger, Desktop ouvert,
  fichier modifié, absence de purge, CLI (11) ; store conservé non adopté (2).
- `test_setup_mcpb_410_setup.py` (12) : manifeste ; aucun champ secret ; plateforme ; aucune
  valeur de machine ; contenu exact ; reproductibilité ; écriture unique ; non-écrasement ;
  destinations refusées (3) ; CLI.
- `test_setup_release_tooling_410_setup.py` (15) : README (5) ; localisateur sans dépendance,
  un seul profil (3), commande installée ; commande de résolution par profil (2), sans défaut ;
  catalogue du profil ; fermeture ML refusée en lexical.

Aujourd'hui les rouges échouent sur des API absentes (`prepare(profile=…)`,
`preview(replace_existing=…)`, `_ready(profile=…)`, modules `sqlite_runtime`, `uninstall`,
`mcpb`, `memory_os_constraints`, `doctor.installation_profile`) ou sur un comportement
réellement différent (remplacement sans consentement, TOML cassé, indentation, README).

**Test reporté, compté à part** : le chemin positif de `--reuse-store` exige un vrai store
lexical et un journal de désinstallation produit par l'outil ; il s'écrit à l'étape 6 (§15),
si R4 est retenue.

## 14. Tests existants : état et adaptations

État sur `cd83511` avec les nouveaux fichiers présents : `test_quickstart.py`,
`test_ops_doctor.py`, `test_distribution_d1a.py`, `test_qualified_runtime.py`,
`test_doctor_pep440_408.py`, `test_lexical_packaging_410.py`,
`test_lexical_capabilities_410.py`, `test_capabilities_l5.py` : **270 réussites, 0 échec**.
La suite `v4/tests` complète n'a pas été relancée pour ce contrat (aucun fichier source
modifié).

Règle : rien n'est affaibli ; chaque adaptation est listée dans la revue avec sa cause.

| Test | Adaptation |
|---|---|
| `test_quickstart.py`, assistant `installation()` et appels directs de `prepare` sur une installation neuve | ajout de `profile="semantic"` (une ligne) ; assertions inchangées |
| `test_quickstart.py`, tests lisant `previous_entry` | aucun aujourd'hui ; tout nouveau lecteur passe par la sauvegarde |
| `test_quickstart.py::test_toml_semantic_undo_preserves_unrelated_edit` | inchangé ; l'égalité d'octets ajoutée ne retire rien |
| `test_qualified_runtime.py` (chargement de `qualify_runtime_resolution`) | l'appel de la porte reçoit `--profile` ; comparaisons inchangées |
| `test_distribution_d1a.py` (METADATA, scripts du wheel) | la commande `memory-os-constraints` et le module `memory_os_constraints` s'ajoutent aux listes attendues, si Q1 est retenue |

## 15. Ordre d'implémentation, fichier par fichier (aucun code maintenant)

Étape 2 — SQLite et codes :

1. `ops/sqlite_runtime.py` (nouveau).
2. `ops/quickstart.py` : `QuickstartError.code`, `blocker_codes`, appel de
   `sqlite_runtime.inspect`, sonde WAL en tête d'application.
3. `ops/doctor.py` : `_sqlite_runtime`. → `test_sqlite_runtime_410_setup.py` vert, hors les
   tests dépendant du profil.

Étape 3 — profil :

4. `ops/quickstart.py` : `profile` dans `prepare`, `_service_plan`, `_ready`, `apply_install`,
   `settings_for_install` ; ressources par profil ; `timing`. `ops/provision.py` : **inchangé**
   (`plan(resources=("cl100k",))` existe).
5. `ops/cli.py` : `--profile`, code de refus.
6. `ops/doctor.py` : `installation_profile`, `_capabilities`, `store-profile`.
   `test_quickstart.py` adapté d'abord (§14). → `test_setup_profile_410_setup.py` vert.

Étape 4 — clients :

7. `ops/quickstart.py` : consentement nommé, journal sans entrée en clair et `undo` depuis la
   sauvegarde, candidat TOML analysé, retrait TOML exact, indentation JSON, clés dupliquées,
   `which`. `ops/clients.py` : lecture stricte des clés dupliquées ; registre inchangé.
   `ops/cli.py` : `--replace-existing`. → `test_setup_clients_410_setup.py` vert.

Étape 5 — retour arrière et désinstallation :

8. `ops/quickstart.py` : `undo(with_service=…)`, `retained`.
9. `ops/uninstall.py` (nouveau) ; `ops/cli.py` : `uninstall`, `--with-service`.

Étape 6 — store conservé (si R4) :

10. `ops/quickstart.py` : `reuse_store`, attestation par journal et sonde ; test positif.
    → `test_setup_lifecycle_410_setup.py` vert.

Étape 7 — `.mcpb` :

11. `ops/mcpb.py` (nouveau) ; `ops/cli.py` : `mcpb`. → `test_setup_mcpb_410_setup.py` vert.

Étape 8 — outillage de version :

12. `v4/src/memory_os_constraints.py` (nouveau), `pyproject.toml` (`py-modules`, script).
13. `scripts/qualify_runtime_resolution.py`, `scripts/check_runtime_resolution.py`.
14. `README.md`, `docs/CLIENTS.md` ; note de version. → dernier fichier vert.

Étape 9 : revue adverse Codex ; preuve sur paquet installé (§12) sous GO.

Fichiers jamais modifiés par cette phase : `embed.py`, `sqlite_embeddings.py`, migrations,
ressources sémantiques, `sqlite_store.py`, `memory.py`, `config.py`.

## 16. Décisions laissées à Romain

- **R1 — Profil d'une installation neuve.** Proposé : `--profile` obligatoire, aucun défaut
  (§2.1). Alternative : défaut `lexical` pour l'installateur (une option de moins pour les
  testeurs de D1, mais un choix implicite qui diffère du défaut moteur). Le README met la
  variante lexicale en premier dans les deux cas.
- **R2 — Clause D1** : re-signature pour le profil lexical (plan §6) ; le protocole du §10
  est-il celui de la mesure ?
- **R3 — Purge.** Proposé : aucune commande de purge dans cette phase ; la suppression des
  données reste manuelle et documentée. Alternative : une commande séparée, à confirmation
  explicite, dans une phase ultérieure.
- **R4 — Réinstallation sur un store conservé** (`--reuse-store`, §7). Proposé : dans le
  périmètre, sous sa forme minimale. Sans elle, « conserver les données » laisse un store que
  seul le runbook de mise à niveau peut rattacher.
- **R5 — Installation du README en trois commandes `pip`** (`--no-deps`, puis sous
  contraintes). C'est le prix d'un runtime qualifié sans canal de publication des contraintes ;
  une installation sans contraintes est « compatible », pas « qualifiée », et `quickstart` la
  refuse (`runtime_unqualified`).
- **R6 — `.mcpb`** : emplacement par défaut du venv (`~/.memory-os/venv`) inscrit dans le
  manifeste ; nom et auteur affichés.

## 17. Questions ouvertes pour le relecteur

- **Q1** — `memory_os_constraints` comme module de premier niveau : acceptable, ou préférer une
  ligne `python -c` documentée (sans nouveau module) ?
- **Q2** — Schéma MCPB : les champs du §5.4 (`manifest_version`, `server.type: "python"`,
  substitutions `${user_config.*}` et `${HOME}`, types `file`/`string`) sont écrits de mémoire,
  sans consultation de la spécification (pas de réseau pour ce contrat). À vérifier contre la
  version de la spécification avant l'étape 7 ; `MANIFEST_VERSION` est volontairement une
  constante non épinglée par les tests. Faut-il que `doctor`/`uninstall` détectent une
  extension installée (emplacement géré par Claude Desktop) ?
- **Q3** — Attestation SQLite : suffit-il de vérifier l'interpréteur de la CLI, ou faut-il que
  le serveur publie `sqlite_version` dans ses capacités pour que `_ready` et `doctor` le
  vérifient côté service ?
- **Q4** — Sonde WAL fonctionnelle à l'application seulement (l'aperçu se limite aux options de
  compilation) : arbitrage entre « zéro écriture » et preuve complète en aperçu.
- **Q5** — Codex : refus maintenu pour une entrée existante, même avec consentement.
- **Q6** — Relais périmé (classe `relay`, autre interpréteur) remplacé sans consentement
  supplémentaire : peut être le relais d'une autre installation.
- **Q7** — `undo --with-service` : la preuve « créé par cette installation » repose sur le
  journal 0600 ; suffisant ?
- **Q8** — Désinstallation avec un fichier client modifié depuis l'aperçu : les autres clients
  et le service sont traités (`ok: false`). Préférer un refus global ?

## 18. Hors périmètre

Linux et Python 3.12–3.13 (phase 1b) ; publication, PyPI, GitHub, registre MCP ; mesure H2 ;
mesure de D1 ; bancs (§9) ; qualification des clients hors des six ; purge des données ;
migration ou conversion de profil d'un store ; mise à niveau d'une installation 4.0.x.
