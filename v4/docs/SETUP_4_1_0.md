# Installation multi-clients 4.1.0 — contrat (phase 1a-setup, étape 1)

Statut : **contrat v5 amendé (verdict n° 5 « VALIDÉ AVEC AMENDEMENTS », traité en §22 bis), en cours
d'implémentation** ; écarts constatés à l'implémentation : §23. Le verdict n° 4 sur la v4 (`ad9d8bb`,
`codex_verdict_setup_contract_v4.md` : deux faux négatifs de dépendance) est traité en §22 ; le
verdict n° 3 sur la v3 (`7e76eba`) en §21. Historique : La v1 (`779c3a7`) a reçu le verdict Codex
« NON VALIDÉ » du 21/11 (`codex_verdict_setup_contract_v1.md`), traité point par point en §19 ;
la v2 (`fc9b449`) le verdict n° 2, « NON VALIDÉ, plus étroit » (`codex_verdict_setup_contract_v2.md`),
traité en §20. Base `cd83511` (`feature/4.1.0-lexical`, porte G1a atteinte), branche
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
- **Classification permissive.** `clients.classify` (`clients.py:56`) rend `relay` dès que la
  commande et ses arguments, mis bout à bout, **contiennent** `memory_os_v4.ops.relay`,
  `memory-os-relay` ou `stdio_relay.py` : `/usr/bin/env python -m memory_os_v4.ops.relay`,
  `/bin/sh -c "…; python -m memory_os_v4.ops.relay"` ou un programme étranger recevant cette
  chaîne en argument sont tous « relay ». Cette classe sert au diagnostic (`doctor`) ; elle
  n'est ni une forme reconnue ni une preuve de propriété.
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
   Décision de Romain : R1 (§16) ; recommandation : obligatoire.
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
- `quickstart.preview(..., replace_existing=(), journal_root=None)` ; chaque client du plan porte
  `installation_identity`, `current_identity` et `ownership` (§5.2). `journal_root` : dossier
  des journaux de cette installation (défaut `~/.local/state/memory-os/quickstart`), lu sans
  écriture.
- `quickstart.installation_identity(env) -> str`, `quickstart.entry_identity(entry) -> str | None`,
  `quickstart.relay_form(entry) -> bool`.
- Nouveau module `ops/diagnostics.py` : `EXCEPTION_TYPES`, `OTHER`, `exception_label(exc)`,
  `exception_types(text)` (§8).
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

Trois notions distinctes ; aucune ne remplace les autres.

1. **Forme reconnue** — `relay_form(entry)` est vrai seulement si : `command` est un chemin
   **absolu** dont le nom de fichier est `python`, `python3` ou `python3.<n>` ; `args` est
   **exactement** `["-m", "memory_os_v4.ops.relay"]` ; les seules clés de l'entrée sont
   `command`, `args`, `env` et la clé de transport du client (`"type": "stdio"`, ou ses
   équivalents Zed et Grok) ; les seules variables d'`env` sont `MEMORY_V4_CLIENT_ID`,
   `MEMORY_V4_URL`, `MEMORY_V4_TOKEN_FILE`, `MEMORY_V4_OPS_DIR`. Tout le reste est une forme
   **lâche**, même classée `relay` : enveloppe `env` ou `sh`, programme étranger, argument en
   plus ou en tête, interpréteur relatif, script `memory-os-relay` ou `stdio_relay.py`, entrée
   portant aussi une URL, variable supplémentaire.
2. **Identité de destination** — `installation_identity(env) = sha256(JSON canonique {"url":
   MEMORY_V4_URL, "token_file": MEMORY_V4_TOKEN_FILE})` ; `entry_identity(entry)` la rend pour
   une entrée portant les deux variables, sinon `None`. Elle dit **vers quel service** l'entrée
   pointe. Ce n'est **pas** une preuve de propriété : n'importe qui peut écrire ces deux
   chemins.
3. **Preuve de propriété** — un **journal de confiance** de cette installation lie le client,
   l'entrée et l'installation : journal sous `journal_root`, privé et sur chemin sûr (règles du
   §6 : 0700/0600, propriétaire courant, un seul lien, aucun lien symbolique), contenant pour
   **ce client** un enregistrement `applied`, non annulé, dont `applied_entry_sha256` est
   l'empreinte de l'entrée actuellement en place et dont `installation_identity` est celle de
   cette exécution. Chaque application écrit ces deux champs. Un journal 4.0.9 (qui porte
   `applied_entry` sans empreinte) vaut preuve si cette entrée est exactement celle en place.

   **Une preuve est consommée par le retrait qu'elle autorise.** Tout retrait de notre entrée —
   par `undo` **ou par `uninstall`** — **révoque durablement** les enregistrements
   correspondants (même client, même empreinte) dans tous les journaux de confiance, journaux
   4.0.9 compris : `revoked: true`, `revoked_by: <stamp de l'opération>`, écrits par
   `_atomic_write`, mode conservé. Un enregistrement révoqué ne prouve plus rien : la même
   entrée recréée à la main ensuite est `unproven`. **Ordre : révocation d'abord, retrait
   ensuite.** Si le retrait échoue après la révocation, l'entrée reste en place et non prouvée
   (`failed-entry-kept-unproven`, résultat partiel) : l'erreur va dans le sens sûr, un
   consentement nommé sera demandé. Si la révocation échoue (écriture d'un journal impossible),
   ce client n'est pas touché : `revocation-failed-entry-untouched`, **aucune écriture dans son
   fichier**, résultat partiel, service conservé. Ce qui est garanti alors, et seulement cela
   (amendement du verdict n° 5) : **client intact ; révocations déjà acquises conservées**
   (jamais défaites : un journal déjà réécrit reste révoqué) ; **résultat partiel**
   (`uninstall_partial`) ; **service conservé**. La preuve n'est donc « intacte » que si l'échec
   survient à la **première** écriture ; après un échec sur le deuxième journal, le premier est
   déjà révoqué et l'entrée n'est plus prouvée que par les journaux concordants restants.
   `revoked_journals` donne le nombre de révocations acquises. **Reprise** : une nouvelle
   exécution relit les journaux ; s'il reste au moins un enregistrement concordant non révoqué,
   l'entrée est encore prouvée (`remove`, `ownership: "journal"`), les journaux restants sont
   révoqués puis l'entrée est retirée ; s'il n'en reste aucun, elle est `keep-unproven` et
   demande un consentement nommé. Les journaux sont traités dans l'ordre de leur chemin.
   Tous les journaux concordants sont révoqués,
   pas seulement le plus récent ; un journal portant une autre empreinte n'est pas réécrit ;
   mode 0600, dossier 0700 et lien unique sont conservés par la réécriture. Le résultat donne
   `revoked_journals` par client. Les preuves des autres clients ne sont pas modifiées.

   Chaque élément de la preuve est vérifié séparément, et testé en n'altérant que lui :
   l'interpréteur seul (forme toujours stricte, même destination → l'empreinte ne concorde
   plus), le champ `installation_identity` du journal seul, l'empreinte seule, le statut seul.

`ownership` vaut `"journal"` quand les trois conditions sont réunies, sinon `"unproven"`.

| Entrée existante | Action sans consentement | Avec consentement nommé |
|---|---|---|
| égale à la nôtre | `unchanged` | — |
| forme reconnue, même destination, **prouvée par le journal** | `replace` (montrée par l'aperçu, sauvegardée) | — |
| classe `relay`, même destination, mais forme lâche **ou** sans preuve (écrite à la main, modifiée depuis notre écriture, preuve d'un autre client, journal non sûr ou annulé) | `refuse-existing-relay-unproven` | `replace` |
| classe `relay`, autre destination ou destination inconnue | `refuse-existing-relay-other-installation` | `replace` |
| `direct-http`, `legacy-v3`, `other` | `refuse-existing-<classe>` | `replace` |

Code de blocage : `client_entry_conflict`. Consentement : `--replace-existing
cursor,claude-desktop`, **par client** ; il ne vaut pas pour un autre client ; nommer un client
non sélectionné est refusé (`client_consent_unselected`). Codex : aucun consentement possible.
La désinstallation applique les mêmes trois notions (§7).

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
| **A — `server.type: "uv"`** (manifeste 0.4) | `manifest.json` + `pyproject.toml` dépendant de `memory-os-ai[ops,brief]==4.1.0` ; l'hôte gère l'environnement | paquet minuscule, indépendant de la plateforme, conforme | exige le paquet sur **PyPI** (publication : hors phase) et le réseau à l'installation ; l'hôte résout les dépendances : rien dans les notes de spécification ne permet de lui transmettre des contraintes, donc le verrou qualifié n'est pas garanti ; type 0.4 alors que la spécification s'annonce 0.3 |
| **B — `python` avec dépendances embarquées** | `server/lib` = fermeture lexicale résolue depuis `qualified_runtime_constraints_lexical_v1.txt` pour darwin-arm64 / CPython 3.11 | hors ligne, verrou qualifié embarqué, reproductible à partir de wheels à empreinte fixée | ≈ 69 Mo décompressés pour 36 paquets (mesure du runtime de qualification), dont 8 à binaires natifs (`numpy`, `cryptography`, `pydantic-core`, `cffi`, `rpds-py`, `regex`, `tiktoken`, `charset-normalizer`) : verrouillé à une plateforme et à CPython 3.11 ; le type `python` ne fournit pas d'interpréteur, il faut donc soit le Python 3.11 de l'hôte (à exiger par `compatibility.runtimes`, échec sinon), soit un interpréteur embarqué (hors de ce que la spécification décrit) ; second exemplaire du runtime à maintenir à côté du venv ; construction = téléchargement de wheels |
| **C — reporter le `.mcpb`** | aucun paquet ; Claude Desktop reste un client qualifié par `claude_desktop_config.json` | aucun risque de conformité ; rien de nouveau à qualifier ; D1 n'en dépend pas (les testeurs utilisent `quickstart`) | l'application doit être fermée pendant l'écriture ; pas d'installation « en un clic » |

**Recommandation : C pour la phase 1a-setup**, B ou A dans une phase ultérieure (A dès que la
publication existe ; B si une installation hors ligne en un clic est demandée). Décision de
Romain : R7 (§16). Ce contrat et ses tests sont écrits pour C : aucune commande `mcpb`, aucun
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
  0600, propriétaire courant, **un seul lien physique** chacun, aucun lien symbolique sur le
  chemin (dossier du journal et dossier racine compris) ; sinon `journal_unsafe`, rien n'est
  fait. Les huit défauts sont testés : trois modes, lien symbolique du dossier et de la racine,
  lien physique de chacun des deux fichiers, propriétaire différent.
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
  | `stop-unconfirmed` | `bootout` rend 0 mais le job reste chargé, **ou son état ne peut plus être observé** (`launchctl print` en erreur autre que « service introuvable », ou en délai dépassé : `observation: "indeterminate"`) | `service_stop_unconfirmed` | **en place** |

  Seule la réponse explicite « Could not find service » vaut absence. Une erreur ou un délai de
  l'observation n'est jamais assimilé à « absent ». Un plist n'est jamais déplacé après un
  arrêt non confirmé : le job continuerait de tourner sans définition sur disque.
- **Données : jamais.** Aucun fichier du store, des sauvegardes, de la clé, du jeton ni des
  ressources n'est supprimé, restauré, remplacé, ni même ouvert. Aucune ancienne copie n'est
  remise en service : `data_restored_from_copy: False`. Preuve sur un **vrai store SQLite
  temporaire** (§13) : des mémoires écrites par le moteur après l'installation sont retrouvées
  par recherche après le retour arrière, avec le même `store_instance_id`.

## 7. Désinstallation

Nouveau module `ops/uninstall.py` ; commande `memory-os-admin uninstall [--clients …] [--apply]`.

- `plan(settings, *, home, python, run, clients=None, remove_existing=())` : lecture seule.
  `apply(plan, settings, *, home, run, stop_timeout=10.0, sleep=time.sleep)`.
- **Retire, dans les clients sélectionnés** (`--clients`, défaut : tout le registre) :
  - l'entrée `memory-os-ai` **prouvée par le journal** (§5.2 : forme reconnue, même
    destination, enregistrement concordant) : `remove`, `ownership: "journal"` ;
  - une entrée de classe `relay` pointant vers **notre** service mais sans preuve ou de forme
    lâche, **ou dont la preuve a été révoquée par un retrait antérieur** (§5.2) : gardée,
    `keep-unproven` / `kept-unproven` ; retirée seulement avec un consentement
    nommé `--remove-existing <client>` (`ownership: "consent"`, client non sélectionné →
    `client_consent_unselected`) ;
  - toute autre entrée (autre classe, autre destination) : gardée, `keep-foreign` /
    `kept-foreign`.

  Sauvegarde de chaque fichier touché ; formats conservés (après installation puis
  désinstallation, les six fichiers retrouvent leurs octets d'origine).
- **Le service survit tant qu'une entrée en dépend ou peut en dépendre.** La dépendance au
  service est une quatrième notion, distincte de la propriété : une entrée que nous ne
  retirons pas (non prouvée, forme lâche, HTTP directe, commande opaque) peut très bien
  utiliser notre service.
  `uninstall.service_dependency(entry, settings, *, home) -> "wired" | "indeterminate" | "none"` :

  **Règle : `none` exige une preuve positive.** Une entrée n'est indépendante de notre
  service que si elle **établit elle-même** une autre destination. Tout ce qui n'est pas
  établi — destination absente, héritée, variable, commande opaque — est `indeterminate`, et
  `indeterminate` conserve le service. Ni la classe de l'entrée, ni les valeurs par défaut du
  relais, ni l'environnement de la commande `uninstall` ne prouvent une indépendance.

  | Entrée restante | Ce qui est établi | Dépendance |
  |---|---|---|
  | relais de forme stricte, ou script console `memory-os-relay` (nu, ou forme `uvx` reconnue, ci-dessous), **avec un `MEMORY_V4_URL` littéral dans son `env`**, visant **notre** service (hôte de bouclage et port) | l'entrée nomme notre service | `wired`, quel que soit le client (sens sûr : le service est conservé) |
  | même forme, `MEMORY_V4_URL` littéral visant une **autre** destination (autre port, ou hôte hors bouclage), dans un client où la priorité de l'`env` de l'entrée sur l'environnement hérité est **établie** (liste ci-dessous) | la destination : l'`env` de l'entrée est imposé au processus lancé et prime sur l'environnement hérité | `none` |
  | même forme, autre destination, dans un client où cette priorité **n'est pas établie**, ou client non précisé | rien : un `MEMORY_V4_URL` hérité visant notre port pourrait l'emporter | `indeterminate` |
  | HTTP directe (`url`, `serverUrl`, `httpUrl` littérale), sans `command`, **une seule destination** (un seul champ, ou plusieurs champs désignant le même hôte et le même port) | la destination : c'est l'URL elle-même | `wired` si hôte de bouclage (`127.0.0.1`, `localhost`, `::1`) et port sont les nôtres, avec ou sans barre finale ; `none` sinon |
  | HTTP directe portant **plusieurs champs URL contradictoires** (destinations différentes), ou un champ URL non littéral | rien : on ne sait pas lequel le client utilise | `indeterminate` |
  | serveur v3 de forme exacte : interpréteur, `args == ["-m", "memory_os_ai.server"]` | un serveur stdio autonome, avec son propre magasin, qui n'ouvre aucune connexion vers le service v4 | `none` |
  | relais de forme stricte ou script console **sans `MEMORY_V4_URL` dans son `env`** (forme du README et de `server.json`) | rien : le relais lit l'environnement de **son** processus (`ops/settings.py:73`) ; le client a pu être lancé avec `MEMORY_V4_URL` visant notre port et le transmettre sans l'écrire dans sa configuration ; la valeur par défaut `8765` ne s'applique que si rien n'est hérité, ce que la configuration ne dit pas | `indeterminate`, quel que soit notre port |
  | `MEMORY_V4_URL` non littéral (`${VAR}`, valeur non textuelle) | rien | `indeterminate` |
  | enveloppe (`sh`, `env`), programme étranger, arguments en plus — **même avec un `MEMORY_V4_URL` explicite** | rien : la commande peut fixer ou remplacer sa destination | `indeterminate` |
  | classe `other` : commande opaque sans marqueur (`/opt/bin/mon-relais`), avec ou sans variables | rien : l'absence de marqueur ne prouve pas qu'elle n'appelle pas notre relais | `indeterminate` |
  | mention de `memory_os_ai.server` hors de la forme exacte (dans une enveloppe) ; entrée portant à la fois `command` et URL | rien | `indeterminate` |

  La comparaison porte sur le **service** (hôte de bouclage et port), pas sur le fichier de
  jeton : une entrée qui vise notre port dépend de notre service même avec un autre jeton.

  **Priorité de l'`env` d'une entrée sur l'environnement hérité : bornée aux clients où elle
  est établie** (amendement du verdict n° 5). Elle est établie, d'après les sources lues par
  le relecteur, pour **VS Code** (`extHostMcpNode.ts`), **Codex** (`rmcp-client/src/utils.rs`)
  et **Gemini CLI** (`mcp-client.ts`), et pour eux seuls. Constante :
  `uninstall.ENV_PRECEDENCE_ESTABLISHED = {"vscode", "codex", "gemini-cli"}`. **Antigravity n'en fait pas
  partie** : ses sources n'ont pas été lues, et partager l'arborescence `.gemini/` ne dit rien de la façon dont
  il lance un serveur ; la règle « non établi → `indeterminate` » s'applique (décision du coordinateur).
  Pour **Claude Code, Claude Desktop, Cursor, Antigravity** et tout autre client du registre, elle n'est
  **pas établie ici** : aucun code de ce dépôt ne la démontre (le dépôt ne contient que le
  relais, qui lit l'environnement de son processus, `ops/settings.py:73` ; la façon dont le
  client compose cet environnement est hors du dépôt) et aucun accès réseau n'est permis pour
  la vérifier. Pour eux, un relais avec URL explicite étrangère est `indeterminate` et conserve
  le service. Les vingt-six scénarios synthétiques ne constituent pas cette preuve.
  `service_dependency(entry, settings, *, home, client=None)` : `client` est la clé du client
  qui porte l'entrée ; absent, il vaut « non établi ».

  **Forme `uvx` reconnue** : `command` de nom `uvx`, arguments composés **uniquement** de
  `--python <v>`, `--no-python-downloads`, `--from <exigence>` où l'exigence nomme le paquet
  `memory-os-ai` (extras et version libres), terminés par `memory-os-relay`. Tout autre
  argument (`--with`, `--env-file`, autre paquet, `--from` absent) en fait une commande
  opaque : `indeterminate`, même avec une URL explicite étrangère.

  Cas ajoutés par l'amendement, tous testés : URL explicite étrangère contre environnement
  hérité contradictoire (l'environnement de l'outil visant notre service : `none` dans un
  client établi, `indeterminate` ailleurs) ; URL explicite vers notre service contre
  environnement hérité étranger (`wired`) ; client non établi ou non précisé ; `uvx` avec URL
  explicite étrangère (forme reconnue : `none` dans un client établi ; autre paquet ou
  `--env-file` : `indeterminate`) ; champs URL contradictoires (`indeterminate`).

  Conséquences assumées : l'entrée du README (`uvx … memory-os-relay` sans environnement) et
  toute entrée `memory-os-ai` opaque retiennent le service jusqu'à ce que l'utilisateur les
  retire (`--remove-existing <client>` pour un relais, ou à la main) ; `next_steps` nomme le
  client et la raison. Il n'y a toujours aucun forçage. L'installation n'est pas concernée :
  `quickstart` n'arrête jamais un service à cause d'une entrée cliente, et ses règles de
  remplacement (§5.2) ne dépendent pas de cette notion.

  Après les retraits prévus, on évalue **toutes** les entrées `memory-os-ai` restantes de
  **tout le registre** (pas seulement la sélection, pas seulement les six). S'il en reste une
  `wired` ou `indeterminate` : `service.action = "keep-clients-remaining"`,
  `remaining_clients` les nomme, `remaining_dependencies` donne la raison par client,
  `next_steps` dit quoi retirer ou vérifier, **aucun** `bootout`, plist en place ; le résultat
  est `ok: true`, `selective: true`, `partial: false`. Le service n'est arrêté, par la séquence
  du §6, que lorsqu'il ne reste **aucune** dépendance (`remaining_clients: []`). Il n'existe
  pas d'option pour forcer l'arrêt d'un service encore utilisé. Chaque client du plan porte
  son champ `dependency`.

  Exclure `memory-os-relay` de la forme stricte (§5.2) vaut pour la **propriété automatique**
  seulement : cette entrée n'est jamais retirée ni remplacée sans consentement, mais elle
  **compte** comme dépendance. À l'installation, une entrée déjà exactement égale à la nôtre
  reste `unchanged`, quel que soit l'état de ses preuves.
- **Ordre et issues** (arbitrage Q8) :
  1. **Revalidation globale avant toute mutation** : empreinte de chaque fichier client,
     empreinte du plist, identité du job. Tout écart → `UninstallError(code=
     "uninstall_changed_since_preview")`, **rien** n'est modifié, le service continue.
     Plan refusé (service étranger : `uninstall_service_foreign` ; Claude Desktop ouvert :
     `client_app_running`) → `UninstallError(code="preflight_refused")`.
  2. **Clients d'abord.** Un conflit **tardif** (fichier réécrit pendant l'exécution) laisse
     ce client en `conflict-file-changed-since-preview`, les autres sont traités ; résultat
     **partiel explicite** : `ok: false`, `partial: true`, `code: "uninstall_partial"`.
  3. **Service ensuite, seulement si tous les retraits clients ont réussi et qu'aucun client
     ne reste raccordé.** Retrait incomplet : `service.outcome = "kept-clients-incomplete"` ;
     client restant : `"kept-clients-remaining"`. Dans les deux cas aucun `bootout`, plist en
     place (un client encore configuré ne doit pas pointer vers un service arrêté).
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
  - `claude-desktop-extension` — `inspected: False`, `present: None` : toujours signalée, sous
    l'option C comme sous les autres, parce qu'elle n'est pas visible ; formulation
    **conditionnelle**, qui n'affirme jamais qu'une extension est installée : « If present:
    check Claude Desktop → Settings → Extensions and remove `memory-os-ai` » ;
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
  rattachement automatique. Décision de Romain : R4 (§16) ; recommandation : hors périmètre.

## 8. Aucun secret journalisé

Secrets : la valeur du jeton, la clé de sauvegarde, et tout secret étranger (clés d'API
d'autres serveurs, en-tête `Authorization` d'une ancienne entrée, compte de `~/.claude.json`,
contenu d'un log ou d'une sortie de commande).

**Principe : des diagnostics structurés, à liste blanche.** On ne peut pas masquer un secret
qu'on ne connaît pas ; on n'écrit donc que des champs dont on connaît la forme. Aucun texte
libre venu d'un log, d'un sous-processus ou d'une exception étrangère n'atteint une sortie.

**Sources brutes et diagnostics produits : deux choses différentes.** La règle « aucun secret »
porte sur ce que **nous produisons** : journaux, rapports, résultats, erreurs, sorties de la
CLI. Elle ne porte pas sur le **log brut du serveur** (`<ops_dir>/logs/server.err`), écrit par
le processus serveur : c'est une source, qui peut contenir n'importe quoi. Pour elle la règle
est double :

- **non-propagation** : rien de son contenu ne passe dans ce que nous produisons ; nous ne la
  copions pas, n'en extrayons aucune ligne ; seuls son chemin, sa taille et des noms de classe
  de la liste fermée en sortent ;
- **conservation** : elle reste à sa place, **octets inchangés**, dans son dossier 0700, pour
  l'opérateur ; nous ne la tronquons, déplaçons ni supprimons.

Les tests séparent les deux : le contrôle de propagation fouille tout le dossier d'état **sauf
exactement ce fichier** (et les `*.bak`) ; un test distinct vérifie que ce fichier a ses octets
et son mode d'origine et qu'il est le **seul** fichier du dossier d'état à contenir la
sentinelle (une copie posée à côté serait détectée).

**Noms d'exception : une liste fermée, explicite, testée**, sans découverte dynamique.
`diagnostics.EXCEPTION_TYPES` contient exactement : `AssertionError`, `AttributeError`,
`ConnectionError`, `FileNotFoundError`, `ImportError`, `IndexError`, `KeyError`,
`KeyboardInterrupt`, `MemoryError`, `ModuleNotFoundError`, `NotImplementedError`, `OSError`,
`PermissionError`, `ProfileError`, `ProvisionError`, `QuickstartError`, `RuntimeError`,
`SecretFileError`, `SyntaxError`, `SystemExit`, `TimeoutError`, `TypeError`, `UninstallError`,
`ValueError`, `sqlite3.DatabaseError`, `sqlite3.OperationalError`. Toute autre classe devient
la constante `"other"` ; son nom n'est jamais recopié (une classe étrangère homonyme d'un nom
de la liste aussi). `exception_label(exc)` applique la règle à une exception ;
`exception_types(text)` à un log : lignes commençant par un identifiant pointé terminé par
`Error`, `Exception`, `Failure`, `Exit` ou `Interrupt` suivi de `: ` ; noms connus triés et
dédoublonnés, puis `"other"` s'il y en a d'inconnus. La même règle vaut pour le journal
d'installation, la CLI et `doctor`.

| Source | Règle |
|---|---|
| Échec de démarrage du service | `startup_failure` ⊆ `{state, runs, last_exit_code, pid, process_state, exception_types, log_path, log_bytes, stop_attempted, stopped}`. **`stderr_tail` disparaît.** `exception_types` : `diagnostics.exception_types` sur les 8 192 derniers octets (liste fermée ci-dessous, sinon `"other"`) ; jamais un message. Le message d'erreur donne le chemin du log privé, pas son contenu. Le log reste en place, dossier 0700 |
| Sous-processus (`launchctl`, `claude`, `pgrep`) | seuls le nom de la commande et le code de retour ; stdout et stderr ne sont jamais copiés, délai dépassé compris |
| Contrôles `doctor` pendant l'installation | `install.json["doctor"] = [{"area", "status"}]` ; `detail` et `hint` ne sont pas journalisés (relancer `doctor` pour les lire). Échec : `code: "doctor_failed"`, message nommant les zones en échec |
| Sortie de `doctor` | détails issus de gabarits ; d'une exception étrangère, seulement `exception_label` |
| Exceptions | texte conservé seulement pour nos propres classes (`QuickstartError`, `ProvisionError`, `UninstallError`), dont les messages sont construits sans contenu ; toute autre exception est réduite à `exception_label`, avec un code stable (par exemple `relay_probe_failed`) |
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
tous les fichiers du dossier d'état hors `*.bak` et hors le log brut nommé ci-dessus, vérifié à
part.

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
| Nouveaux tests | les sept fichiers du §13 verts (735 tests après amendements et écarts du §23 ; 482 à la v5), sans `skip` ni `xfail` |
| Non-régression | les huit fichiers du §14 verts après les adaptations listées ; suite `v4/tests` : mêmes échecs que le relevé de G1a (`.local-baseline/v4_failures_cd83511.txt`) |
| Zéro écriture en aperçu | tests d'inventaire (installation, désinstallation) |
| D2 | retour arrière, désinstallation, arrêts en échec, vrai store, secrets (§13) |
| Fichiers intouchés | `embed.py` (SHA-256 `c2c335c4…5d60a7`), `sqlite_embeddings.py`, migrations, ressources sémantiques : `git diff cd83511` vide |
| Aucun ML en lexical | relais et CLI d'administration importés avec les modules ML bloqués |
| Localisateur sur wheel installé | wheel construit ; venv vierge **hors `HOME` réel** ; `pip install --no-deps <wheel>` (sans réseau) ; `memory-os-constraints lexical` rend un fichier existant sous `site-packages` ; aucun paquet tiers installé |
| Preuve d'installation | venv vierge, installation lexicale sous contraintes, `quickstart --profile lexical` en aperçu puis `--apply` dans un `HOME` jetable avec un libellé launchd de test — opération manuelle sous GO de Romain, hors suites |
| Revue | verdict Codex « VALIDÉ » |

## 13. Tests

**État après implémentation : 735 tests verts** (clients 263, cycle de vie 275, profil 62, reprise 50, outillage
25, secrets 32, SQLite 28) : les 482 de la v5, 47 d'amendements (§22 bis) et 206 d'écarts, de décisions ultérieures et de corrections de revue (§23, §23.4 à §23.11). Le reste de
cette section décrit l'état de la v5, avant tout code.

482 tests dans sept fichiers ; avant implémentation, **82 verts** (comportement de `cd83511`, à
conserver) et **400 rouges par construction**, sans `skip` ni `xfail`. `ruff check` : aucun
défaut. Module d'appui `setup_fixture_410.py` :

- HOME temporaire avec les six clients (entrées étrangères, commentaires, indentations variées,
  secrets sentinelles) ;
- `FakeSystem`, le `run=` injecté : `launchctl`, `pgrep`, CLI `claude`, toute autre commande
  refusée. Il sait **échouer** : `bootout` en échec, en délai dépassé, ou « réussi » avec un job
  qui reste chargé ; **observation du job en erreur ou en délai dépassé après l'arrêt** ;
  `bootstrap` en échec ; job remplacé ; service qui redémarre en boucle ; bruit contenant des
  secrets sur stdout/stderr ; crochets exécutés pendant une commande ;
- `FakeClock`, horloge déterministe ;
- `write_memories` / `read_memories` : **vrai moteur, profil lexical, sans ML** ;
- `all_text(root, exclude=…)` et `raw_logs(c)` : texte de ce que nous produisons, le log brut
  du serveur étant exclu par son chemin exact.

| Fichier | Verts | Rouges | Objet |
|---|---|---|---|
| `test_sqlite_runtime_410_setup.py` | 2 | 26 | SQLite chargé vérifié |
| `test_setup_profile_410_setup.py` | 6 | 56 | profil, référence figée, readiness, horloge, `doctor`, CLI |
| `test_setup_clients_410_setup.py` | 58 | 125 | six clients, formats, conflits, forme reconnue, preuve de propriété |
| `test_setup_lifecycle_410_setup.py` | 4 | 115 | D2 : aperçu, sauvegarde, secrets du chemin nominal, retour arrière, désinstallation, sélection, révocation des preuves, dépendance au service |
| `test_setup_recovery_410_setup.py` | 1 | 33 | arrêts en échec ou indéterminés, journal sûr, installation interrompue, conflits tardifs, **vrai store** |
| `test_setup_secrets_410_setup.py` | 6 | 25 | secrets sur les chemins d'échec, log brut, liste fermée d'exceptions |
| `test_setup_release_tooling_410_setup.py` | 5 | 20 | README, `server.json`, portes de résolution (`main()` compris), localisateur |

**Verts (82).**

- SQLite (2) : plancher moteur ; interpréteur de qualification conforme.
- Profil (6) : plan de la table seule ; référence figée du service sémantique ; readiness
  sémantique acceptée ; service lexical refusé par la readiness sémantique ; dérive d'encodeur
  vue par `doctor` ; profil inconnu refusé par la CLI.
- Clients (58) : registre ; notre entrée sans secret (6) ; création (5) ; sauvegarde (6) ;
  seconde exécution (6) ; fichier choisi seul ; Claude Code par sa CLI ; échec de la CLI ;
  Codex : préfixe, entrée existante jamais réécrite (3), tableau de tables, ajouts valides (3),
  modification ultérieure conservée, entrée identique `unchanged`, relais périmé toujours
  manuel ; JSON : indentation (2), octets après `undo` (2), serveur ajouté conservé (4), JSONC ;
  fichier réécrit (5) ; aucune configuration étrangère exposée (6).
- Cycle de vie (4) : aperçu client ; sauvegarde = image d'avant ; `undo` client sans effet sur
  les données ; aucun paquet `.mcpb` livré.
- Reprise (1) : le banc de preuve sur vrai store fonctionne aujourd'hui, sans ML.
- Secrets (6) : échec de la CLI `claude` réduit à son code ; `doctor` réel sur fichiers
  hostiles ; exception de la liste réduite à sa classe par la CLI (3) ; **le contrôle de
  propagation exclut exactement le log brut** et détecte une copie posée à côté.
- Outillage (5) : `server.json` ; relais et CLI sans ML ; contraintes empaquetées ; porte
  sémantique inchangée.

**Rouges (400), et ce que chacun prouvera.**

- `test_sqlite_runtime_410_setup.py` (26) : rapport sans écriture ; constante unique ; trois
  versions refusées, trois acceptées ; JSON absent ; WAL non compilé ; bibliothèque non
  chargeable ; connexion impossible ; priorité ; sonde WAL confinée (et sa limite déclarée),
  puis refusée ; `quickstart` refusé avant toute écriture, quatre codes × deux profils (8) ;
  `jsonl` non bloqué ; sonde avant jeton et service ; contrôle `doctor` « this interpreter ».
- `test_setup_profile_410_setup.py` (56) : profil obligatoire, inconnu, `jsonl` ; profil
  consigné (2) ; aucun choix d'après les paquets ; catalogue du profil (2) ; dérive lexicale ;
  ressources par profil (2) ; service lexical ; environnement accepté par `Config.from_env()` ;
  octets figés avec `profile="semantic"` ; service lexical = référence figée moins les
  variables documentées ; plan sémantique égal à l'héritage ; profil relu (2) ; conversion
  refusée (2) ; readiness lexicale ; version SQLite du serveur exigée (8) ; capacités réelles
  publiant la version ; huit altérations lexicales ; installation lexicale de bout en bout ;
  chronométrage à horloge déterministe ; CLI (4) ; `doctor` lexical, dépendance manquante,
  marqueur malformé, profil d'installation (5), SQLite du serveur distinct (4).
- `test_setup_clients_410_setup.py` (125) : table en ligne Codex (2) ; candidat analysé ; octets
  après `undo` Codex ; indentation (2) ; sans saut de ligne final ; octets après `undo` JSON
  (2) ; clés dupliquées ; entrée étrangère non remplacée (15) ; remplacement consenti (9) ;
  consentement non transmissible ; hors sélection ; **forme reconnue** : trois formes strictes
  acceptées, **onze formes lâches refusées** (enveloppe `env`, enveloppe `sh`, programme
  étranger, argument en plus, option en tête, interpréteur relatif, autre exécutable, script
  console, script hérité, entrée portant aussi une URL, variable en plus) ; identité de
  destination distincte de la propriété ; **entrée prouvée par le journal mise à jour sans
  consentement** (3) ; **même entrée écrite à la main non remplacée** ; **preuve non
  transmissible à un autre client** ; **entrée dont seul l'interpréteur absolu a changé**
  (forme stricte conservée : c'est l'empreinte qui refuse) ; **journal non sûr, à lien
  physique, annulé, ou dont un seul champ est altéré — `installation_identity`, empreinte,
  statut** (7) ; **forme lâche jamais
  remplacée sans consentement** (33 : trois clients × onze formes) ; autre destination (8) ;
  remplacement avec consentement nommé (15) ; **journal 4.0.9 accepté s'il porte l'entrée
  exacte, refusé pour un autre client** ; **le journal lie client, entrée et installation** ;
  journal sans entrée précédente en clair ; `undo` depuis la sauvegarde ; sauvegarde altérée.
- `test_setup_lifecycle_410_setup.py` (115) : aperçu complet (2), refusé (2) ; dix refus à code
  stable ; six clients configurés et sauvegardés ; aucun secret dans journaux, plist, CLI,
  refus (3) ; secret préexistant dans la seule sauvegarde ; `undo` par défaut ; retour arrière
  avec service ; service non créé par l'installation ; service modifié ; désinstallation :
  aperçu, retrait, octets d'origine, sauvegardes et secrets, entrées étrangères, **seul ce que
  le journal prouve est retiré** (3 : copie écrite à la main, enveloppe, argument en plus),
  **retrait d'une entrée non prouvée sur consentement nommé**, **sélection partielle : service
  conservé**, **arrêt seulement au dernier client**, **clients restants cherchés dans tout le
  registre**, **aucune option de forçage**, **révocation des preuves** (6 : séquence
  installer Cursor et VS Code → désinstaller Cursor → recréer l'entrée à la main →
  désinstaller Cursor, entrée gardée et service conservé pour VS Code ; même entrée refusée
  par `quickstart` alors qu'elle était prouvée avant le retrait ; révocation avant retrait,
  échec du retrait laissant l'entrée non prouvée ; `undo` révoque aussi ; trois journaux
  concordants dont un 4.0.9 tous révoqués, un quatrième non concordant intact, modes
  conservés ; échec d'écriture de la révocation : client intact, résultat partiel, preuve
  intacte), **dépendance au service** (65 : vingt-six entrées classées — six `wired`, quatorze
  `indeterminate`, six `none` — puis les mêmes vingt-six en désinstallation réelle, service
  conservé sauf pour `none` ; environnement hérité jamais supposé, trois formes de relais ×
  trois environnements de l'outil (9) ; service conservé pour un relais qui peut hériter sa
  destination ; service conservé pour une commande opaque ; script console exclu de la
  propriété mais compté comme dépendance ; entrée exactement égale `unchanged`), idempotence, service étranger, Desktop ouvert,
  refus global si changement depuis l'aperçu, jamais « complète » et retrait manuel à
  formulation conditionnelle, absence de purge, CLI ; store conservé non adopté.
- `test_setup_recovery_410_setup.py` (33) : plist jamais déplacé après `bootout` en échec, en
  délai dépassé ou non confirmé (3) ; identité revérifiée avant l'arrêt et absence observée
  après ; **observation en erreur ou en délai dépassé = arrêt non confirmé**, pour le retour
  arrière (2) et la désinstallation (2) ; job remplacé jamais arrêté ; journal non privé ou
  chemin non sûr (**8**, dont liens physiques, propriétaire, racine en lien symbolique) ;
  installation interrompue (3) ; désinstallation : clients avant le service, résultat partiel
  et reprise (3), job remplacé après l'aperçu, conflit tardif ; vrai store : mémoires
  retrouvées après retour arrière, après désinstallation, après arrêt en échec (3), WAL non
  fusionné laissé intact par le retour arrière **et par la désinstallation** (2).
- `test_setup_secrets_410_setup.py` (25) : échec de démarrage en faits structurés ; **log brut
  intact, privé, seul porteur de son contenu** ; sortie de la CLI ; sortie de sous-processus
  jamais copiée (5) ; détails `doctor` non journalisés (3) ; exception de la liste réduite à
  sa classe avec un code (4) ; refus avec secrets étrangers ; **liste fermée explicite** ;
  **classification d'un log sans écho d'un nom inconnu** (5) ; **classe inconnue réduite à la
  constante** dans le journal, la CLI et `doctor` (3).
- `test_setup_release_tooling_410_setup.py` (20) : README (5) ; localisateur sans dépendance,
  sur arborescence nue, un seul profil (3), commande installée ; commande de résolution par
  profil (2), sans défaut ; `main()` sous contraintes (2), refusé sans profil, en échec sur un
  paquet ML ; catalogue du profil ; fermeture ML refusée.

Aujourd'hui les rouges échouent sur des API absentes ou sur un comportement réellement
différent (remplacement sans consentement ni preuve, TOML cassé, queue de log journalisée,
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
| tout test existant qui remplace une entrée relais posée à la main, ou attend un nom de classe d'exception hors de la liste fermée | l'entrée est d'abord écrite par l'outil (journal) ou le consentement est nommé ; le nom attendu devient `"other"` — à recenser à l'étape 4, aucun cas relevé à la lecture de `test_quickstart.py` et `test_ops_doctor.py` |

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

Seules R1, R2, R4, R6 (nom, auteur) et R7 attendent Romain. Tout le reste de ce contrat est
arrêté, y compris les réponses de Codex aux trois questions de la v2 : identité de destination
plus journal de confiance (§5.2), liste fermée d'exceptions (§8), extension Desktop toujours
signalée comme non inspectée (§7).

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
| Q6 | pas de remplacement automatique d'un relais d'identité inconnue ou différente, ni d'une forme lâche, ni sans preuve par journal | §5.2 |
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

## 20. Verdict Codex n° 2 sur la v2 : point par point

| Point du verdict | Changement | Section · tests |
|---|---|---|
| **B1** — contrôle de secrets impossible à satisfaire (le log brut est fouillé alors qu'il doit être conservé) | distinction écrite entre source brute et diagnostics produits ; non-propagation et conservation ; le contrôle exclut exactement le log brut ; intégrité, mode et unicité vérifiés à part | §8 · `raw_logs`, `all_text(exclude=…)` ; `test_the_propagation_check_excludes_exactly_the_raw_log_and_nothing_else`, `test_raw_server_log_is_kept_intact_private_and_is_the_only_place_holding_its_content` |
| **B2** — classification `relay` par sous-chaîne ; l'identité de destination n'est pas une propriété | trois notions : forme reconnue stricte, identité de destination, preuve par journal de confiance liant client, entrée et installation ; sans preuve concordante, consentement nommé ; fait de code consigné | §1, §5.2, §7 · 3 formes strictes, 11 formes lâches, 33 cas sans consentement, preuve par journal (3), écrite à la main, autre client, modifiée, journal non sûr (5), journal 4.0.9, liaison du journal ; désinstallation : 3 cas + consentement |
| **B3** — `uninstall --clients cursor` pouvait arrêter le service commun | service conservé tant qu'un client connu du registre reste raccordé ; arrêt au dernier ; aucun forçage | §7 · `test_selective_uninstall_keeps_the_service_…`, `test_service_stops_only_when_the_last_wired_client_is_removed`, `test_remaining_clients_are_looked_up_in_the_whole_registry_…`, `test_uninstall_has_no_option_to_force_the_stop_of_a_shared_service` |
| WAL non fusionné : « rollback and uninstall » ne testait que `undo` | test séparé pour la désinstallation, inventaire complet du store (octets, modes, dates), puis réouverture par le moteur | §7 · `test_real_store_uncheckpointed_wal_is_left_alone_by_uninstall` |
| Observation après arrêt jamais mise en échec | `FakeSystem` rend l'observation en erreur ou en délai dépassé ; indéterminé ≠ absent ; plist en place | §6 · `test_rollback_treats_an_unobservable_job_as_not_stopped` (2), `test_uninstall_treats_an_unobservable_job_as_not_stopped` (2) |
| Journal sûr : liens physiques et propriétaire non couverts | quatre cas ajoutés (lien physique de chaque fichier, propriétaire, racine en lien symbolique) | §6 · `test_rollback_trusts_only_a_private_journal_on_safe_paths` (8) |
| Question 1 — identité | destination + journal de confiance pour tout remplacement ou retrait automatique | §5.2, §7 |
| Question 2 — `exception_types` | liste fermée explicite et testée ; classe inconnue → `"other"` ; étendue à `doctor` et à la CLI | §8 · `test_exception_names_come_from_an_explicit_closed_list`, classification d'un log (5 cas), journal, CLI et `doctor` (3) |
| Question 3 — extension sous C | toujours signalée, `inspected: False`, `present: None`, formulation conditionnelle | §7 · `test_uninstall_never_claims_to_be_complete_and_names_what_it_cannot_see` |
| Mentions « à arbitrer / à confirmer » sur des décisions arrêtées | retirées ; seules R1, R2, R4, R6 (nom, auteur) et R7 attendent Romain | §2.1, §5.4, §7, §16 |

## 21. Verdict Codex n° 3 sur la v3 : point par point

| Point du verdict | Changement | Section · tests |
|---|---|---|
| **N1** — la désinstallation ne périme pas la preuve de propriété | une preuve est consommée par le retrait qu'elle autorise : `undo` **et** `uninstall` révoquent durablement les enregistrements correspondants de tous les journaux de confiance (`revoked`, `revoked_by`) ; révocation avant retrait ; échec du retrait → entrée laissée non prouvée | §5.2, §7 · `test_uninstall_revokes_the_proof_so_a_hand_made_copy_is_not_removed_later` (la séquence exacte, service conservé pour VS Code), `test_revoked_proof_no_longer_lets_quickstart_replace_the_entry`, `test_the_proof_is_revoked_before_the_entry_is_removed`, `test_undo_revokes_the_proof_too` |
| **N2** — destination inconnue ou implicite lue comme « pas de dépendance » | dépendance au service = quatrième notion, résolue avec les valeurs par défaut du relais ; `wired`, `indeterminate`, `none` ; service conservé pour `wired` et `indeterminate` ; script console et `uvx … memory-os-relay` du README comptés ; HTTP directe vers notre port comptée ; comparaison sur hôte de bouclage et port | §7 · `test_service_dependency_of_an_entry_is_resolved_with_the_relay_defaults` (14), `test_uninstall_keeps_the_service_for_an_entry_that_depends_or_may_depend_on_it` (14), `test_implicit_defaults_do_not_wire_an_entry_to_a_service_on_another_port` |
| `memory-os-relay` exclu de la forme stricte mais dépendance détectée | énoncé ; champ `dependency` par client | §7 · `test_strict_ownership_still_excludes_the_console_script_that_counts_as_a_dependency` |
| Entrée exactement égale reste `unchanged` | énoncé, y compris après révocation | §7 · `test_an_entry_exactly_equal_to_ours_is_unchanged_whatever_its_history` |
| Test `:445` : l'argument ajouté rendait la forme lâche | seul l'interpréteur absolu change ; forme stricte et destination vérifiées dans le test | §5.2 · `test_an_entry_edited_after_our_write_is_no_longer_proven` |
| Test `:466` : la destination demandée changeait | seul le champ `installation_identity` du journal est altéré ; deux cas voisins ajoutés (empreinte seule, statut seul) ; le test vérifie que la destination concorde toujours | §5.2 · `test_only_a_trusted_matching_journal_counts_as_proof` (7) |

## 22. Verdict Codex n° 4 sur la v4 : point par point

| Point du verdict | Changement | Section · tests |
|---|---|---|
| `other = none` inacceptable : une commande opaque peut appeler notre relais | la « limite déclarée » est supprimée ; règle « `none` exige une preuve positive » ; commande opaque → `indeterminate`, service conservé | §7 · `test_uninstall_keeps_the_service_for_an_opaque_command` ; cas `opaque-command`, `opaque-command-with-other-url`, `class-other` |
| Ce qui peut encore être `none`, justifié cas par cas | trois cas seulement : relais de forme stricte ou script console avec URL littérale explicite vers une autre destination (l'`env` de l'entrée prime sur l'hérité) ; HTTP directe vers une autre destination (l'URL est la destination) ; serveur v3 de forme exacte (serveur autonome). Une enveloppe, même avec URL explicite, ou une mention de v3 hors forme exacte : `indeterminate` | §7 · six cas `none`, et `shell-wrapper-with-explicit-other-url`, `legacy-v3-inside-a-wrapper`, `http-and-command-mixed` |
| Environnement hérité : l'absence de `MEMORY_V4_URL` ne prouve pas le port par défaut | plus aucune résolution par valeur par défaut ; relais sans URL explicite → `indeterminate` quel que soit notre port ; l'environnement de l'outil n'établit rien ; l'ancien test qui exigeait `none` et l'arrêt est remplacé | §7 · `test_inherited_environment_is_never_assumed_for_a_relay_without_an_explicit_url` (9), `test_uninstall_keeps_the_service_for_a_relay_that_may_inherit_its_destination` |
| Conséquences sur les cas de dépendance et sur l'installation | vingt-six cas reclassés (l'entrée du README sans environnement : `indeterminate`, service conservé) ; `quickstart` non concerné, énoncé | §7 · `test_service_dependency_is_none_only_when_another_destination_is_established` (26), `test_uninstall_keeps_the_service_for_an_entry_that_depends_or_may_depend_on_it` (26) |
| Complément : révocation de plusieurs journaux concordants, dont un 4.0.9 | tous révoqués, non concordant intact, autres clients intacts | §5.2 · `test_every_matching_journal_is_revoked_and_nothing_else_is_touched` |
| Complément : échec d'écriture pendant la révocation | client intact, preuve intacte, résultat partiel, service conservé, autre client traité | §5.2 · `test_a_failed_revocation_leaves_the_client_untouched_and_the_result_partial` |
| Complément : modes conservés | 0600, dossier 0700, lien unique après réécriture | même test que la révocation multiple |

Le §21 décrit la v4 ; sa ligne N2 (« résolue avec les valeurs par défaut du relais ») est
remplacée par la règle ci-dessus.

## 22 bis. Verdict Codex n° 5 sur la v5 (« VALIDÉ AVEC AMENDEMENTS ») : point par point

`codex_verdict_setup_contract_v5.md`. Les deux amendements obligatoires sont intégrés au texte
et aux tests avant le code des outils.

| Point du verdict | Changement | Section · tests |
|---|---|---|
| **Amendement 1** — la priorité de l'`env` explicite sur l'hérité n'est établie que pour VS Code, Codex, Gemini CLI | affirmation bornée : `none` pour un relais à URL explicite étrangère seulement dans un client établi (`ENV_PRECEDENCE_ESTABLISHED` = VS Code, Codex, Gemini CLI) ; Claude Code, Claude Desktop, Cursor, Antigravity, client non précisé : `indeterminate` ; `service_dependency(..., client=None)` | §7 · `test_explicit_foreign_url_frees_the_service_only_where_env_precedence_is_established` (24), `test_uninstall_keeps_the_service_for_an_explicit_foreign_url_in_a_client_not_established` (4, dont Antigravity non établi et VS Code établi) |
| Cas : URL explicite contre environnement hérité contradictoire | l'environnement de l'outil n'établit rien ; seul le client décide | §7 · `test_explicit_url_against_a_contradictory_inherited_environment` (4) |
| Cas : `uvx` avec URL explicite étrangère | forme `uvx` reconnue définie strictement ; autre paquet ou `--env-file` : `indeterminate` | §7 · cas `uvx-explicit-other-port`, `uvx-foreign-package-explicit-other-url`, `uvx-env-file-explicit-other-url` |
| HTTP directe : plusieurs champs URL contradictoires | `indeterminate` ; champs concordants acceptés | §7 · cas `direct-http-contradictory-urls`, `direct-http-two-foreign-urls-that-differ`, `direct-http-agreeing-urls`, `direct-http-agreeing-foreign-urls` |
| v3 exact : une mention dans une enveloppe ne suffit pas | déjà couvert (`legacy-v3-inside-a-wrapper`) ; l'interpréteur doit être un chemin d'interpréteur, les arguments exactement `-m memory_os_ai.server` | §7 |
| **Amendement 2** — « preuve intacte » fausse après un échec sur le deuxième journal | garanties réécrites : client intact, révocations acquises conservées, résultat partiel, service conservé ; reprise décrite | §5.2 · `test_a_revocation_failing_on_the_second_journal_keeps_the_first_and_resumes` |

Effet sur les comptes : 47 tests ajoutés au fichier du cycle de vie (7 cas de dépendance × 2,
liste des clients établis, 24 + 4 + 3 cas d'amendement 1, 1 scénario d'amendement 2) ; avant le
code des outils : **529 tests, 82 verts, 447 rouges**. Le test
`test_service_dependency_is_none_only_when_another_destination_is_established` précise désormais
le client (`vscode`, celui du test de désinstallation réelle, client établi) : sans client, les trois cas
« relais à URL explicite étrangère » sont `indeterminate`.

## 23. Écarts d'implémentation

Règles du contrat corrigées ou précisées pendant l'implémentation, avec leur raison. Chaque
écart est reflété par les tests.

### 23.1 Règles corrigées (le test reflète la règle corrigée)

| Règle du contrat | Constat | Règle appliquée · tests |
|---|---|---|
| §7 : HTTP directe ou relais à URL explicite, « `none` sinon (autre port, ou hôte hors bouclage) » | faux négatif possible : sur **notre** port, un nom d'hôte peut désigner cette machine (`localhost.`, `0.0.0.0`, `127.1`, `0177.0.0.1`, `[::ffff:127.0.0.1]`, nom DNS ou de `/etc/hosts`) ; deux analyseurs d'URL peuvent lire différemment une URL à barre oblique inverse ou à `userinfo` | seule une URL ASCII de forme simple est littérale ; **autre port → `none`** ; sur notre port : `127.0.0.1`, `localhost`, `[::1]` → `wired` ; adresse IPv4 canonique hors `127/8` et `0/8` → `none` ; tout autre hôte → `indeterminate`. Le cas `direct-http-other-host` (`https://example.com:8765`) devient `indeterminate` · 15 cas ajoutés à `DEPENDENTS` (× 2 tests) |
| §7 : forme `uvx` et v3 exact | `--from "memory-os-ai @ https://…"`, `--python /chemin`, interpréteur v3 relatif ou `PYTHON*` dans l'`env` exécutent un code arbitraire | `--from` = nom d'index `memory-os-ai[extras][==version]` seulement ; `--python` = numéro de version ; v3 exact = interpréteur **absolu**, aucune variable `PYTHON*` · cas `uvx-from-a-url…`, `uvx-python-path…`, `legacy-v3-relative-interpreter`, `legacy-v3-with-pythonpath` |
| §7 : « toutes les entrées de tout le registre » | les serveurs **par projet** de Claude Code (`~/.claude.json`, `projects.*.mcpServers`) n'étaient pas vus | ils comptent comme dépendances (jamais écrits ni retirés) ; les `.mcp.json` des projets ne sont pas énumérables : limite déclarée, dite dans `next_steps` · `test_a_project_scope_entry_of_claude_code_keeps_the_service`, `test_uninstall_says_what_it_cannot_inspect…` |
| §7 : plan refusé si le job chargé n'est pas attesté | une désinstallation sélective qui **conserve** le service n'a pas à attester un job qu'elle ne touche pas | le job non attesté ne refuse le plan que si le plan l'arrêterait ; l'arrêt ré-atteste toujours (§6) · `test_a_selective_uninstall_does_not_need_to_attest_a_job_it_will_not_stop` |
| §6 : `undo --with-service` arrête le service créé par cette installation | il arrêtait un service encore utilisé par un client d'une **autre** exécution, ou laissait un client non restauré pointer vers un service arrêté | même règle que la désinstallation : `kept-clients-remaining` (dépendances de tout le registre) et `kept-clients-incomplete` ; journal incapable de décrire son service (4.0.9) : `refused-job-not-attested` · `test_rollback_keeps_the_service_while_another_client_is_wired_to_it`, `…_when_a_client_could_not_be_restored`, `…_cannot_describe_its_service…` |
| §6 : second `undo` | l'entrée déjà retirée rendait `refused-entry-changed-since-apply`, donc un arrêt en échec ne pouvait jamais être repris | entrée absente et preuve révoquée : `nothing-to-undo` ; plist déjà déplacé : `plist-already-absent` · `test_rollback_whose_stop_failed_is_finished_by_a_second_run` (3) |
| §5.2 : une preuve révoquée ne prouve plus rien | `undo` retirait encore une entrée identique recréée à la main | `refused-proof-revoked` · `test_rollback_does_not_remove_an_entry_whose_proof_was_already_consumed` |
| §6 : rétablissement de l'entrée précédente (Claude Code) | la CLI `claude` ne sait exprimer qu'une entrée stdio simple ; une entrée HTTP faisait échouer le retour arrière après le retrait, et les variables d'une entrée étrangère passaient en ligne de commande | seule une entrée précédente de **notre** forme stricte est rétablie par la CLI ; sinon `refused-removal-manual` **avant** toute modification (entrée et preuve intactes, l'ancienne entrée reste dans la sauvegarde privée) · `test_rollback_never_pushes_a_foreign_previous_entry_through_the_claude_cli` |
| §5.3 : formats, au retrait aussi | clés dupliquées fusionnées au retrait ; retrait TOML par lignes non revérifié ; fins de ligne CRLF réécrites en LF | le fichier d'après retrait est calculé et **validé avant** révocation et écriture (`removal_text`) : sinon `refused-removal-manual` ; les fins de ligne sont conservées · `test_removal_refuses_a_json_file_with_duplicate_keys…`, `test_toml_removal_is_parsed_before_it_is_written`, `test_crlf_client_files_keep_their_line_endings` |
| §6 : aucun lien symbolique sur le chemin du journal | seuls le dossier et sa racine étaient vérifiés | tous les ancêtres · `test_a_journal_below_a_symbolic_link_higher_on_the_path_is_not_trusted` |
| §8 : `process_state` dans `startup_failure` | valeur libre recopiée de `launchctl print` | forme fermée (mot court en minuscules), sinon `null` · `test_process_state_is_a_closed_shape…` |
| §14 : liste des scripts dans `test_distribution_d1a.py` | la liste est dans `tests/test_distribution_static.py` | adaptation faite dans ce fichier |

### 23.2 Précisions et ajouts (sens sûr)

- **Étape `service_ready`** : c'est une durée (`steps_ms`), pas un nom d'étape du journal ; `stage` reste
  `"service"` pendant l'attente (comportement 4.0.9, exigé par `test_quickstart.py`).
- **Codes hors du tableau du §11**, tous stables : `download_not_approved`, `preflight_changed`,
  `service_not_ready`, `resources_failed`, `token_failed`, `client_write_failed` (application) ;
  `undo_partial` (retour arrière : un client non restauré) ; `uninstall_refused` (défaut de
  `UninstallError`). `quickstart_refused` reste le défaut des refus anciens sans code propre.
- **Issues hors des tableaux** : `plist-already-absent`, `kept-clients-remaining`, `kept-clients-incomplete`,
  `refused-proof-revoked`, `refused-removal-manual` (retour arrière et désinstallation) ;
  `keep-unreadable`, `keep-unsafe-path` (configuration illisible ou derrière un lien symbolique : gardée,
  dépendance `indeterminate`).
- **`uninstall.settings_from_service`** : la CLI lit URL, jeton, store et dossier d'exploitation dans le plist
  installé (un shell neuf ne porte pas ces variables) ; la propriété reste décidée par `plan`. Service
  attesté aussi par le journal d'installation de confiance quand l'interpréteur de la commande diffère.
- **R1 provisoire** : `quickstart.NEW_INSTALL_DEFAULT_PROFILE = None` ; un défaut serait cette constante.
- **Journal** : `applied_entry` (notre entrée, sans secret) reste écrit à côté de son empreinte ;
  `toml_appended` conserve le texte exact ajouté. Journal de désinstallation : `uninstall.json`, sous
  `~/.local/state/memory-os/uninstall/<stamp>/`.
- **Robustesse des aperçus** : fichier client de mode 000, entrée TOML portant une date, JSON trop
  profond, journal non fiable ou mal formé : refus ou ignoré, jamais une exception.

- **Preuve « sans ML » du banc sur vrai store** : l'assertion sur `sys.modules` dans le processus de test dépendait
  de l'ordre des tests (un test antérieur peut avoir chargé `torch`) ; elle est remplacée par
  `test_real_lexical_store_harness_needs_no_ml_module`, qui rejoue écriture, réouverture et recherche dans un
  interpréteur neuf où les modules ML sont bloqués.

### 23.4 Revue d'implémentation Codex n° 1 (`codex_verdict_setup_impl_v1.md`, sur `58a9fd6`) : point par point

| Point | Correction | Tests |
|---|---|---|
| **P1** — entrée devenue étrangère **pendant** la révocation, puis retirée | l'empreinte attendue est revérifiée au retrait, sur les octets mêmes dont l'écriture est construite (`remove_client_entry(expected_sha256=…)`, puis dernière relecture avant le remplacement atomique ; Claude Code : entrée relue avant `claude mcp remove`). Écart → `conflict-file-changed-since-preview` (désinstallation) ou `refused-entry-changed-since-apply` (retour arrière), résultat partiel, service conservé | `test_uninstall_never_removes_an_entry_that_changed_during_its_own_revocation`, `test_undo_never_removes…`, `test_claude_code_entry_changed_during_its_revocation…` |
| **P2** — service annoncé conservé puis arrêté | l'empreinte du plan revalidée avant toute mutation comprend l'action du service, `remaining_clients` et `remaining_dependencies` (clients hors sélection compris) ; et un plan qui annonçait un service conservé ne l'arrête jamais dans la même exécution (`kept-as-planned`) | `test_a_service_announced_as_kept_is_not_stopped_when_the_other_client_vanishes_after_the_preview`, `test_a_kept_service_stays_kept_even_if_its_last_dependency_vanishes_during_the_apply` |
| **P2** — chemins non sûrs traités différemment | une seule inspection (`uninstall._inspect`, contrôle complet du chemin) pour le plan et pour `remaining_dependencies` : lien symbolique sur un ancêtre, pendant ou non → `indeterminate` | `test_a_config_behind_a_symbolic_link_in_a_parent_directory_keeps_the_service` (2) |
| **P1** — preuve de E1 survivant au remplacement E1 → E2 | un remplacement **consomme** la preuve de l'entrée remplacée : `apply_clients` révoque (même client, même empreinte, tous les journaux de confiance) avant d'écrire ; révocation impossible → `revocation-failed-entry-untouched`, aucune écriture | `test_a_replace_consumes_the_proof_of_the_entry_it_replaces` (séquence exacte) |
| Ambiguïté détectable (TOML) | un fichier TOML portant **les deux** tables (`mcp_servers` et `mcp.servers`) avec notre nom dans l'une : `keep-ambiguous`, dépendance `indeterminate`, jamais une preuve d'absence | `test_a_toml_file_with_both_server_tables_is_ambiguous_never_a_proof_of_no_dependency` |

### 23.5 Revue d'implémentation Codex n° 2 (`codex_verdict_setup_impl_v2.md`, sur `344c5ad`)

Les cinq points du §23.4 sont levés ; un P1 restait.

| Point | Correction | Tests |
|---|---|---|
| **P1** — entrée étrangère écrasée pendant la révocation d'un **remplacement** (E1 prouvée → aperçu « replace » par E2 → un autre écrivain pose F pendant la révocation → F écrasée par E2, la sauvegarde ne contenant que E1) | l'écriture elle-même compare puis écrit : `_write_client(expected_file_sha256=…)` exige que le fichier soit, octet pour octet, celui de l'aperçu **sur les octets dont l'écriture est construite**, puis le revérifie juste avant le remplacement atomique ; Claude Code : fichier revérifié juste avant la CLI (ni `remove` ni `add`). Écart → `conflict-file-changed-since-preview` pour ce client, entrée de l'autre écrivain conservée, résultat partiel, autres clients traités. Vaut pour les créations comme pour les remplacements, JSON, TOML et Claude Code | `test_a_replace_never_overwrites_an_entry_written_during_its_own_revocation` (Cursor, Claude Desktop, VS Code, Claude Code), `test_a_create_never_overwrites_an_entry_written_after_the_last_check` (Cursor, Codex TOML, Claude Code ; TOML n'a pas de remplacement : la création est le seul chemin d'écriture) |
| Test suggéré : E1 → E2 → `undo` → nouveau remplacement | consentement nommé exigé (limite acceptée du §23.3) | `test_after_a_replace_and_its_undo_a_new_replace_needs_named_consent` |

Limite de la garantie : précisée et réduite au §23.6.

### 23.6 Revue d'implémentation Codex n° 3 (`codex_verdict_setup_impl_v3.md`, sur `2a24fc8`) : l'empreinte de la cible est vérifiée immédiatement avant chaque mutation

Discipline unique, **« préparer d'abord, vérifier en dernier, puis remplacer »** : `_atomic_write` écrit,
vide, synchronise (`fsync`), ferme et règle le mode du fichier temporaire **avant** de relire la cible ; la
comparaison de son empreinte à celle des octets dont l'écriture a été construite est la **dernière** étape,
suivie du seul `os.replace`. Écart → temporaire supprimé, `EntryChanged`, rien n'est remplacé. L'attente
est un **argument explicite et obligatoire** de `_atomic_write` (§23.7 ; le mécanisme hors bande de la
première version de cette section est supprimé).

| Chemin de mutation | Vérification au dernier moment | Test |
|---|---|---|
| `quickstart` — création JSON, création TOML (`_write_client`) | empreinte des octets lus (égale à celle de l'aperçu), revérifiée juste avant `os.replace` | `test_a_create_never_overwrites_an_entry_written_while_the_temporary_file_is_prepared` (Cursor, VS Code, Codex) |
| `quickstart` — remplacement JSON | idem, après la révocation | `test_a_replace_never_overwrites_an_entry_written_while_the_temporary_file_is_prepared` (2), et pendant la révocation : `…_written_during_its_own_revocation` (4) |
| `undo` — retrait JSON, retrait TOML (`remove_client_entry`) | empreinte de l'entrée attendue sur les octets lus, puis empreinte de ces octets juste avant `os.replace` | `test_undo_never_overwrites_an_entry_written_while_the_temporary_file_is_prepared` (Cursor, Codex) |
| `undo` — **rétablissement** JSON de l'entrée précédente (même fonction, autre branche) | idem | `test_undo_never_overwrites_an_entry_written_while_a_json_restoration_is_prepared` |
| `uninstall` — retrait JSON, TOML | même fonction | `test_uninstall_never_overwrites_an_entry_written_while_the_temporary_file_is_prepared` (Cursor, Codex) |
| Claude Code — `claude mcp remove` (remplacement, retour arrière, désinstallation) | fichier (empreinte de l'aperçu) ou entrée attendue relus immédiatement avant la commande | `test_claude_code_create_is_revalidated_immediately_before_the_cli`, `test_claude_code_entry_changed_during_its_revocation…` |
| Claude Code — `claude mcp add` : création, ajout après `remove`, rétablissement de l'entrée précédente | notre nom doit être **absent** du fichier immédiatement avant chaque `add` (`_claude_add`) | `test_claude_code_replace_is_revalidated_between_remove_and_add`, `test_claude_code_undo_is_revalidated_before_it_restores_the_previous_entry` |
| Claude Code — retour au mieux à l'entrée précédente après un `add` en échec | même contrôle avant ce second `add` : une entrée posée par un autre écrivain après l'échec est conservée ; sinon l'entrée précédente (notre forme stricte) est remise | `test_claude_code_best_effort_return_after_a_failed_add_is_revalidated` (2) |
| Révocation — réécriture d'un journal de confiance (`revoke_proofs`) | empreinte des octets lus du journal, juste avant `os.replace` ; écart → révocation en échec, client intact | `test_a_journal_changed_while_its_revocation_is_prepared_is_not_overwritten` |
| Journaux propres à l'exécution (`journal.json`, `install.json`, `uninstall.json` d'un dossier neuf 0700 créé par cette exécution) | sans objet : aucun autre écrivain n'a ce chemin ; pas d'empreinte attendue | — |
| Jeton, plist | création exclusive (`O_EXCL`), jamais d'écrasement (inchangé) ; plist déplacé seulement après arrêt confirmé et empreinte revérifiée (§6) | tests du §6 |

**Fenêtres restantes, déclarées.** (1) *Chez nous, réduite au minimum* : entre la relecture finale de la
cible et `os.replace` — une lecture puis un renommage, sans autre E/S ; aucun verrou partagé avec les
clients ne peut la fermer. (2) *Interne à la CLI `claude`* : entre notre relecture et la lecture-écriture
que la CLI fait elle-même de `~/.claude.json`, et pendant celle-ci ; son atomicité est celle de la CLI (§4).
Entre nos deux commandes d'un remplacement (`remove` puis `add`), la fenêtre évitable est fermée par la
revalidation avant `add`.

### 23.7 Revue d'implémentation Codex n° 4 (`codex_verdict_setup_impl_v4.md`, sur `fb5c02d`) : attente d'écriture explicite, aucun état hors bande

Le mécanisme `_expecting` (attente posée dans un dictionnaire de module autour de l'appel) était fautif :
contextes imbriqués sur un même chemin (le `pop` ne rétablissait pas l'attente externe), fils concurrents, et
surtout « pas d'attente » équivalait à « pas de contrôle ». **Choix : la recommandation du coordinateur,
l'argument explicite**, plutôt qu'un `ContextVar` — il supprime la classe de défauts au lieu de la couvrir :
il n'y a plus aucun état partagé entre deux appels, donc ni imbrication, ni fil, ni alias de chemin à gérer.

`_atomic_write(path, text, *, expected)` — `expected` est **obligatoire, nommé, sans valeur par défaut** :

- une empreinte SHA-256 (64 chiffres hexadécimaux) : la cible doit avoir exactement ces octets au dernier moment ;
- `ABSENT` : la cible ne doit pas exister ;
- `UNGUARDED` : accepté seulement pour `journal.json`, `install.json` ou `uninstall.json` dans un dossier
  privé 0700 que `new_journal` a créé **dans cette exécution** (registre `_FRESH_JOURNALS`, en ajout seul,
  qui ne peut que restreindre) ; partout ailleurs, refus ;
- toute autre valeur (`None` compris) : refus ; argument oublié : `TypeError`.

Une cible qui est un lien symbolique, qui n'est pas un fichier ordinaire ou qui a plusieurs liens physiques
est refusée : tout alias d'un fichier est soit le même chemin à lien unique, soit un refus. Les espions des
tests de la phase (sept, tous dans les fichiers `*_410_setup.py`) passent à la signature à trois arguments ;
aucun test préexistant du §14 n'utilisait `_atomic_write`.

Reste, distinct et assumé : `_write_client(expected_file_sha256=…)` garde une valeur par défaut « pas
d'empreinte d'aperçu » pour ses appelants historiques (`test_quickstart.py`) ; elle ne désactive rien,
l'écriture elle-même attend toujours l'empreinte des octets dont elle est construite.

| Cas | Test |
|---|---|
| attente absente (`TypeError`), positionnelle, ou invalide | `test_a_write_without_an_expectation_is_impossible` |
| empreinte exigée, périmée ; `ABSENT` exige l'absence ; une empreinte exige le fichier | `test_expected_digest_and_absent_are_each_enforced` |
| `UNGUARDED` hors d'un journal neuf de cette exécution (autre dossier, dossier 0700 non créé par l'exécution, autre nom de fichier, dossier devenu non privé) | `test_unguarded_is_only_for_a_journal_file_in_a_fresh_private_directory_of_this_run` |
| appels imbriqués sur le même chemin | `test_a_nested_write_on_the_same_path_does_not_disturb_the_expectation_of_the_outer_one` |
| deux fils entrelacés sur le même chemin (événements, aucun `sleep`) | `test_two_threads_on_the_same_path_each_keep_their_own_expectation` |
| chemins distincts | `test_writes_on_distinct_paths_do_not_share_anything` |
| exception pendant l'écriture : ni état, ni temporaire | `test_an_exception_during_a_write_leaves_no_state_and_no_temporary_file` |
| alias : lien symbolique, lien physique, chemin relatif | `test_aliases_of_a_file_are_the_same_checked_path_or_a_refusal` |

### 23.8 Revue d'implémentation Codex n° 5 (`codex_verdict_setup_impl_v5.md`, sur `724a8b4`) : le dernier contrôle ne suit jamais un lien

Les contrôles de type et de nombre de liens précédaient la préparation du temporaire, et le contrôle final
lisait la cible en suivant les liens : un lien symbolique **pendant** posé durant la préparation passait pour
une absence (`ABSENT`) et était écrasé. Correction, pour **toute** attente de `_atomic_write` (les chemins
JSON, TOML et journaux ; **pas** Claude Code, qui passe par la CLI : la phrase « ils passent tous par
`_atomic_write` » de la première version de cette section était fausse, voir §23.9) : `_verify_target`,
dernière étape avant `os.replace`,

- n'utilise que `os.lstat` : `ABSENT` exige que le nom n'existe pas (un lien pendant n'est pas une absence) ;
- pour une empreinte : fichier ordinaire à lien unique, octets lus par un descripteur ouvert avec
  `O_NOFOLLOW` (et `O_NONBLOCK`), identité (périphérique, inode) égale à celle vue par `lstat` avant **et**
  après la lecture ;
- `UNGUARDED` : absent, ou fichier ordinaire à lien unique ;
- sinon `EntryChanged` : l'objet trouvé est laissé tel quel, le temporaire est supprimé.

| Injection pendant la préparation du temporaire | Test |
|---|---|
| lien symbolique pendant, `ABSENT` attendu | `test_a_dangling_symbolic_link_created_during_the_preparation_is_not_absence` ; par le chemin réel : `test_a_client_file_that_did_not_exist_is_not_created_over_a_dangling_link` |
| lien symbolique vers un fichier au contenu attendu, empreinte attendue | `test_a_symbolic_link_to_identical_content_created_during_the_preparation_is_not_the_target` ; par le chemin réel (JSON, TOML) : `test_a_client_file_swapped_for_a_link_during_the_preparation_is_never_written_through` (2) |
| lien physique créé pendant la préparation | `test_a_hard_link_created_during_the_preparation_is_a_conflict` |
| dossier ou FIFO à la place de la cible (`ABSENT` et empreinte) | `test_a_directory_or_a_fifo_appearing_during_the_preparation_is_a_conflict` (4) |

Chaque test vérifie que l'objet posé est conservé, que le temporaire est nettoyé et que le conflit est rendu.

### 23.9 Revue d'implémentation Codex n° 6 (`codex_verdict_setup_impl_v6.md`, sur `6d46110`) : un seul dernier regard, pour les deux familles de mutation

Le contrôle avant `claude mcp add` passait par `read_entry`, qui suit les liens et lit « fichier introuvable »
comme une absence : un lien pendant (ou une FIFO, un dossier, un lien physique) substitué à `~/.claude.json`
entre deux commandes passait pour une absence. Correction : **une seule lecture de dernier regard**,
`_read_verified` (`lstat` seulement ; lien pendant ≠ absence ; fichier ordinaire à lien unique lu par un
descripteur `O_NOFOLLOW | O_NONBLOCK`, identité vérifiée avant et après), utilisée par les deux familles :

**A — mutations par `_atomic_write`** (`_verify_target`, dernière étape avant `os.replace`) :

| Chemin | Attente | Test |
|---|---|---|
| création / remplacement JSON, création TOML (`_write_client`) | empreinte des octets lus, ou `ABSENT` | §23.6, §23.8 |
| retrait / rétablissement JSON, retrait TOML (`remove_client_entry` : `undo`, `uninstall`) | empreinte des octets lus | §23.6 |
| révocation : réécriture d'un journal de confiance (`revoke_proofs`) | empreinte des octets lus | §23.6 |
| journaux de l'exécution (`journal.json`, `install.json`, `uninstall.json`) | `UNGUARDED`, dossier neuf de l'exécution | §23.7 |

**B — mutations par la CLI `claude`** (`_claude_guard`, immédiatement avant **chaque** commande) :

| Commande | Attente évaluée sur les octets de `_read_verified` | Test |
|---|---|---|
| `remove` d'un remplacement (`_write_client`) | empreinte du fichier de l'aperçu **et** entrée lue | `test_claude_code_runs_no_command_at_all_over_an_object_substituted_before_the_first_one[replace-*]` (3) |
| `add` d'une création | empreinte du fichier de l'aperçu (ou absence vraie) et notre nom absent | idem `[create-*]` (3) |
| `add` après le `remove` d'un remplacement | notre nom absent | `test_claude_code_replace_runs_no_add_over_an_object_substituted_after_the_remove` (5 objets) |
| `add` de retour au mieux après un `add` en échec | notre nom absent | `test_claude_code_best_effort_return_runs_no_add_over_an_object_substituted_after_the_failure` (5) |
| `remove` d'un retour arrière ou d'une désinstallation (`remove_client_entry`) | entrée attendue | idem premier test `[undo-*]` (3) ; `test_claude_code_entry_changed_during_its_revocation…` |
| `add` rétablissant l'entrée précédente (`undo`) | notre nom absent | `test_claude_code_undo_runs_no_restoring_add_over_an_object_substituted_after_the_remove` (5) |

Les cinq objets : lien pendant, lien vers un fichier valide, lien physique, FIFO, dossier. Chaque test
vérifie le refus, l'objet conservé et **qu'aucune commande `claude` ultérieure n'a été lancée**.

**Périmètre de cet inventaire** : les mutations faites **directement** par `quickstart.py`, `uninstall.py` et
`sqlite_runtime.py` sur les fichiers clients, les journaux, le plist et le jeton. Il ne couvre pas les effets
**délégués**, listés à part au §23.10 (ressources par `provision.apply`, écritures mémoire de la
démonstration, état `launchd`, fichiers du serveur et du relais).

**C — recherche de toute autre mutation du système de fichiers** dans `quickstart.py`, `uninstall.py`,
`sqlite_runtime.py` (`grep` de `os.replace`, `shutil.`, `unlink`, `rename`, `write_`, `chmod`, `mkdir`,
`os.link`, `symlink`, `write_secret_file`) — aucune ne touche un fichier client hors des familles A et B :

| Mutation | Cible | Garde |
|---|---|---|
| renommage du plist (`_move_plist`, `os.rename`) | plist de **notre** service → journal | destination choisie d'abord (dossier privé, nom libre, `lstat`), puis `_plist_is` (même `_read_verified`) en **dernière** étape, puis le renommage (§23.10) · `test_the_plist_is_not_moved_when_it_became_a_link_while_the_service_stopped` (2), `test_the_plist_check_is_the_last_step_before_the_move`, `test_the_plist_move_is_a_rename_right_after_its_check` |
| `write_secret_file` (jeton, plist) | création | exclusive (`O_EXCL | O_NOFOLLOW`), jamais d'écrasement |
| `shutil.copy2` + `chmod` (`backup_client_file`) | nouveau `*.bak` dans le dossier neuf de l'exécution | lecture seule du fichier client |
| `mkdir` / `chmod` (`new_journal`, dossiers store et logs d'une installation neuve) | dossiers à nous | création ; racine sans lien symbolique |
| `chmod 0600` des journaux de l'exécution | fichiers de la famille A, `UNGUARDED` | dossier neuf de l'exécution |
| `unlink` du temporaire (`_atomic_write`) ; fichiers de la sonde WAL (`sqlite_runtime.wal_probe`) | fichiers créés par l'appel lui-même | — |

Aucune autre mutation trouvée.

### 23.10 Revue d'implémentation Codex n° 7 (`codex_verdict_setup_impl_v7.md`, sur `9ca88f7`)

**1. Présence par appartenance de clé, clés dupliquées refusées partout.** `{"mcpServers": {"memory-os-ai":
null}}` passait pour une absence, et `json.loads` laissait une table ou un nom dupliqué masquer une entrée
derrière la dernière valeur. Règle unique, appliquée à **toute lecture JSON qui fonde une décision** :

- `clients.strict_loads` refuse une clé dupliquée **à tout niveau** ;
- notre nom est « présent » dès que la **clé** existe (`SERVER in servers`), valeur `null` comprise ; une
  table de serveurs qui n'est pas un objet est refusée.

| Lecture | Effet d'une clé dupliquée, d'une valeur `null`, d'une table non-objet | Test |
|---|---|---|
| `clients.read_entry` (aperçu `quickstart`, plan `uninstall`, balayage des dépendances, `doctor`, `undo`) | état `unreadable` : `refuse-unreadable` / `keep-unreadable`, dépendance `indeterminate` | `test_a_masked_or_null_entry_is_never_read_as_no_dependency` (7), `test_quickstart_never_creates_over_a_null_or_masked_entry` (8) |
| aperçu `quickstart`, clés dupliquées à tout niveau (§5.3) | `refuse-json-duplicate-keys`, avant tout autre motif | même test ; `test_json_with_duplicate_keys_is_refused_not_silently_deduplicated` |
| portée projet de Claude Code (`uninstall._claude_project_entries`) | lecture stricte ; non lisible ou `null` → `indeterminate` | premier test (cas `claude-code`) |
| `_claude_guard`, avant chaque commande `claude` | `EntryChanged`, aucune commande | les trois tests d'injection du §23.9, étendus aux cinq variantes JSON (`null`, table dupliquée, nom dupliqué, doublon imbriqué, table non-objet) : avant `add`, avant l'`add` de rétablissement, avant l'`add` de retour au mieux (15 cas) |
| écriture et retrait JSON (`_write_client`, `removal_text`), lecture de la sauvegarde (`_entry_in_text`) | refus, rien n'est écrit | `test_a_json_client_write_refuses_a_null_or_masked_entry_found_at_write_time` (4), `test_removal_refuses_a_json_file_with_duplicate_keys_and_keeps_the_proof` (adapté : le refus vient désormais dès le plan, `keep-unreadable`) |

TOML : `tomllib` refuse déjà les clés dupliquées et n'a pas de `null`.

**2. Plist : le contrôle est la dernière E/S avant le déplacement.** `_move_plist` choisit d'abord la
destination (dossier de journal privé, nom libre trouvé par `lstat`), puis `_plist_is`, puis `os.rename`,
sans autre E/S entre les deux. Un renommage impossible (autre volume) laisse la définition en place :
`refused-changed-since-install`, jamais une copie.

**3. Effets délégués, hors de l'inventaire du §23.9.** Ils ne sont pas réimplémentés ici ; leurs garanties
sont définies là où ils vivent.

| Effet | Par qui, où | Garantie, et où elle est définie |
|---|---|---|
| téléchargement et pose des ressources (Granite, table `cl100k`) | `provision.apply` : cache Hugging Face et chemin de la table, sous un dossier de transit `.memory-os-provision-*` | seulement avec `--download-approved` ; plan revalidé (`preview != expected` → refus), verrou du cache, taille et SHA-256 épinglés de chaque fichier, garde disque/swap ; aucun fichier client, jeton ni service (`ops/provision.py`, `DISTRIBUTION_D1a_v1.md`) ; inchangé par cette phase (§15) |
| création du jeton | `auth.create_token` → `write_secret_file` | création exclusive 0600, jamais d'écrasement (`ops/auth.py`) |
| chargement / arrêt du service | `launchctl bootstrap` / `bootout` | §4, §6 : état `launchd`, aucune écriture de fichier par nous au-delà du plist |
| store, log `server.err`, journaux du relais | le **processus serveur** et le relais, une fois lancés | moteur (`LEXICAL_PROFILE_4_1_0.md`, contrat du store) ; `quickstart` n'ouvre jamais le store ; le log brut est une source conservée (§8) |
| sonde du relais (`_relay_probe`, `demo=False`) | relais stdio → serveur | lectures seules (`memory_status`, `memory_task_list` sur un projet de contrôle unique) |
| démonstration (`--demo`, sur demande) | relais → serveur : **une** écriture `memory_chat_save` puis une recherche, dans un projet unique `memory-os-demo-<uuid>` | jamais rejouée (identifiant de requête unique, échec = « never replay »), aucune suppression ; défini par `quickstart._relay_probe` (`v4/src/memory_os_v4/ops/quickstart.py`, branche `demo`) et par l'en-tête de ce module (amendement Q6, repris de `v4/docs/DISTRIBUTION_D1a_v1.md` §1 ; le document `QUICKSTART_CONTRACT_v1.md` cité auparavant n'est pas dans le dépôt) |
| contrôles `doctor` | lecture seule | `ops/doctor.py` |

### 23.11 Revue d'implémentation Codex n° 8 (`codex_verdict_setup_impl_v8.md`, sur `3894f00`) : une structure présente mais invalide n'est jamais une absence

`_claude_project_entries` ignorait un `mcpServers` de portée projet présent mais non-objet
(`{"projects": {"/w": {"mcpServers": ["memory-os-ai"]}}}`) : aucune dépendance, service arrêtable. Règle,
appliquée à tous les balayages : **structure absente → aucune entrée ; structure présente mais pas de la
forme attendue → `indeterminate`, service conservé** (désinstallation et `undo --with-service`).

| Balayage | Structures présentes-invalides | Résultat | Constat du passage en revue |
|---|---|---|---|
| Claude Code, portée projet (`uninstall._claude_project_entries`) | document non-objet ; `projects` non-objet ; projet non-objet (`null` compris) ; `mcpServers` de projet non-objet ; valeur de serveur non-objet | `indeterminate` | **corrigé ici** (les quatre premiers cas étaient lus comme une absence) |
| JSON, portée utilisateur — Claude Code, Claude Desktop, Cursor, Antigravity, Gemini CLI, Devin, Windsurf (`mcpServers`), VS Code (`servers`), Zed (`context_servers`) : `clients.read_entry` | document non-objet ; table non-objet (`null` compris) ; valeur de serveur non-objet (`null` compris) ; clés dupliquées | `unreadable` → `indeterminate` | déjà conforme depuis le §23.10 ; contrôle du document non-objet rendu explicite |
| TOML — Codex, Grok (`clients._toml_entry`) | `mcp_servers` non-table (`[]`, `false`, `""`, chaîne, tableau de tables) ; `mcp` non-table ; `mcp.servers` non-table ; valeur de serveur non-table | `unreadable` → `indeterminate` | **corrigé ici** : une valeur « fausse » (`[]`, `false`, `""`, `0`) de `mcp_servers` retombait sur l'autre table puis sur « aucune entrée » |
| TOML, les deux tables présentes avec notre nom | — | `ambiguous` → `indeterminate` | §23.4 |

Tests : `test_a_present_but_invalid_server_structure_keeps_the_service` (21 structures × désinstallation et
`undo --with-service` = 42 cas, chacun vérifiant l'absence de `bootout`, le plist en place et le fichier
intact) ; `test_an_absent_structure_is_still_no_dependency` (5 : l'autre face de la règle, sans quoi le
service ne pourrait jamais être arrêté) ; l'exclusion `continue` du test du §23.10 est retirée.

### 23.3 Limites résiduelles

- Les `.mcp.json` de projet ne sont pas inspectés (ci-dessus).
- Claude Code : si `claude mcp add` échoue après le `remove` d'un remplacement **consenti** d'une entrée
  étrangère, celle-ci ne subsiste que dans la sauvegarde privée ; désinstallation sans CLI `claude` : la
  preuve est révoquée puis le retrait échoue (`failed-entry-kept-unproven`), aucun contrôle `which` dans
  `uninstall.plan`.
- Un journal 4.0.9 annulé **par une 4.0.9** (qui n'écrivait aucune marque de révocation) prouve encore une
  entrée identique recréée à la main : limite de compatibilité historique seulement (toute opération 4.1
  révoque, remplacement compris, §23.4).
- Après un remplacement prouvé E1 → E2, `undo` remet E1 depuis la sauvegarde, mais E1 n'est plus prouvée
  (sa preuve a été consommée par le remplacement) : une mise à jour ultérieure demandera un consentement nommé
  (`test_after_a_replace_and_its_undo_a_new_replace_needs_named_consent`).
- Le descripteur de construction atteste les deux paquets, pas le module de premier niveau
  `memory_os_constraints.py` (couvert par l'inventaire source du sdist seulement).
- Preuve du localisateur : wheel construit hors ligne depuis le commit scellé et inspecté (module, deux
  fichiers de contraintes, point d'entrée) ; l'installation `pip --no-deps` dans un venv vierge (§12) reste
  à faire sous GO.
- Antigravity n'est **pas** dans `ENV_PRECEDENCE_ESTABLISHED` (retiré : rattachement à Gemini CLI non vérifié) ;
  un relais Antigravity à URL explicite étrangère retient donc le service jusqu'à preuve ou retrait de l'entrée.
