# 4.0.5 sûreté et préparation P1 — relais Claude × Codex

2026-10-11. Source candidate uniquement. Aucun service/configuration utilisateur
modifié, aucun artefact 4.0.4 refabriqué. Claude atteste 4.0.4 publié sur PyPI
(wheel1c2dfc5a…, sdist51c4ca04…) et actif. SQLite/registre restent inactifs.

## 1. Etat des preuves et ordre proposé

La qualification isolation 4.0.5 de Claude s'est arrêtée **avant les suites** :
disque3,79 Go, swap4,68 Go ; `client_configs_unchanged=true`. Conserver ce report,
aucun nouveau run au même emplacement. Observation initiale Codex : environ3,79 Go libres.
La revue statique finale relève 5,33 Go, mais sysctl du swap est refusé dans le
sandbox (rc1). Les deux gardes ne sont donc pas attestables ici ; aucune suite lancée.
Lint projet (`--config pyproject.toml`) et lint E9/F sur src/tests/v4/scripts :
verts ; parsing AST des 9 fichiers Python modifiés/ajoutés et compilation du
child TCP synthétique : valides. `git diff --check` : vert. Preuve locale
`.local-qualification/405-safety-p1-codex-v1/static-proof-v3.json` ; ce n'est pas
une preuve de justesse/exécution du correctif, ni une qualification de release.
Les 585v3/1492v4 du correctif antérieur ne qualifient pas les nouveaux changements.
Nouveaux tests fournis mais **non exécutés**, qualification machine toujours manquante.

1. P0 4.0.5 : isolation + préflight clair + retrait des configs legacy suivies.
   Revue statique maintenant ; suites/TCP et T05 de la candidate après espace≥5 Go.
   Estimation 0,5–1 JA, hors qualification/release et attente opérateur.
2. L6 S4, contrat relu puis implementation/qualification sur copies : 4–7 JA.
3. L7b/CAS, contrat relu puis implementation SQLite jetable : 3–5 JA.
   Mutations production uniquement après L6 activé et GO séparé.
4. L8 et L1b ; capture L9 reste assistée et manuelle. L11 attend budget/cas scellés.

Les contrats `CONTRACT_SQLITE_S4_v1.md` et `CONTRACT_L7b_WORK_STATE_v1.md`
nécessitent la contre-revue Claude ; aucune API de ces contrats n'est livrée ici.

## 2. Préflight 4.0.5

Avant toute mutation, le relais appelle le préflight read-only. Une erreur/timeout
ou un résultat invalide ne prouve pas qu'un contrat ancien est installé.

| Code | Cause | Etat de cette tentative / action |
|---|---|---|
| preflight_unavailable | transport, erreur outil, JSON invalide ou refus générique du contrôle | aucune mutation envoyée ; nouvelle tentative **manuelle** après ready si aucune ancienne requête incertaine du même ID |
| upstream_not_ready | code explicite engine_not_ready/engine_initializing/upstream_not_ready | même règle ; aucune attente/reprise automatique |
| legacy_unattested | réponse de contrôle réussie, contrat l3-v2 absent/ancien | aucune mutation envoyée ; corriger le contrat, pas un retry aveugle |
| preflight_invalid | l3-v2 déclaré mais identité/empreinte/portée/opération invalide | aucune mutation envoyée ; diagnostic requis |
| invalid_arguments | validation du payload par le serveur | aucune mutation envoyée ; corriger le payload, retryable=false |
| token_missing/token_invalid/authentication_rejected/identity_changed | préflight refusé pour authentification/identité | aucune mutation envoyée ; corriger/relire identité avant nouvelle action |
| outcome_uncertain/completion_not_durable | mutation commencée / reçu non confirmé | statut L3 obligatoire ; aucun rejeu |

Les refus préflight deviennent un résultat MCP `isError=true` avec données
structurées : request_id, code, attempt_outcome=not_sent, mutation_sent=false,
retryable, automatic_retry=false, logical_status_required. Le statut local sans
ID expose la dernière observation préflight (éphémère, datée), sans inventer de
reçu. Une ancienne requête locale du même ID ou un journal douteux désactive
l'indication retryable. Un message « rien envoyé » concerne **cette tentative**,
pas l'histoire complète d'un ID ; les incertains antérieurs restent protégés.

14 cas unitaires préflight fournis : timeout/erreur/JSON, warming vrai/faux, contrat ancien,
attestation d'opération erronée, authentification, reprise manuelle après ready,
ancien ID incertain sans effet/rejeu supplémentaire, refus de validation,
alias canonique compact→consolidate uniquement simulé, et rejet messages vides
sur JSONL/SQLite jetables. Deux cas TCP ajoutés dans
`test_l3_tcp.py` : contrôle synthetic engine_not_ready et panne réelle, résultat
structuré transmis par le SDK → statut local sans incertain → reprise manuelle
après ready → un effet exactement. Ils sont eux aussi **non exécutés** ici.
Les 20TCP existants restent obligatoires, soit désormais 22, sans réduire L3/L4.

En fin de session, Codex a appelé chat_save avec `messages=[]` par erreur. Le
schéma exige min1 : le préflight serveur valide ce payload avant toute mutation.
Le relais déployé a répondu legacy_unattested, puis statut unknown ; aucun rejeu
effectué. Le serveur observé juste après est bien 4.0.4/621f5b8, l3-v2 et MPS,
et le journal local annonce zéro incertain. Ce cas n'atteste donc pas un serveur
legacy et ne suffit pas à attribuer la précédente erreur au démarrage. Le nouveau
code distingue invalid_arguments avec chemins/types de validation, sans recopier
les valeurs d'entrée. Request_id à garder pour examen :
`codex-20261011-405-safety-p1-3e4cd82`. Compte rendu durable dans Git en attendant.

## 3. Configurations du dépôt

Les `.mcp.json` et `.vscode/mcp.json` suivis annonçaient encore v3. Retrait de
l'index Git avec `--cached` et ajout au gitignore : **fichiers privés restaurés
par Claude conservés byte-à-byte**. Un clone propre n'active plus le vieux serveur.
Template explicite : `bridges/shared-v4/mcp.template.json` ; relais installé,
token fichier0600, aucun chemin personnel ni serveur v3. Fusion opérateur, scope
et PATH vérifiés ; ne jamais écraser une config locale avec le template.
MANIFEST exclut explicitement les deux configs actives, y compris si un checkout
ou un ancien SOURCES.txt local les contenait encore au prochain build.
**Import du bundle** : il contient deux suppressions de fichiers suivis. Un
checkout ordinaire peut retirer leurs copies dans le clone receveur. Avant tout
checkout/import d'arbre, sauvegarder ces deux configs privées dans un dossier
0700 et relever leurs SHA ; après import, vérifier leur présence/égalité et les
remettre depuis cette copie si le checkout les a retirées. Ne jamais remplacer
les configs opérateur par le template. Dans le clone Codex, `rm --cached` n'a
touché aucune des deux copies restaurées par Claude.

## 4. Archivage des preuves : opinion et procédure

Accord pour compresser les contenus sans perte, manifeste + SHA + vérification
complète. **Archive + originaux conservés = zéro octet libéré.** Pour récupérer
l'espace, il faut déplacer/retirer les originaux après preuve de conservation ;
cette étape attend le GO de Romain et **n'existe pas dans le script livré**.
Ne pas nommer cette étape « sans suppression » : préserver les preuves logiques
est différent de conserver leurs deux représentations physiques.

`zstd`/`zstandard` ne sont pas disponibles dans le runtime vérifié. Le script
utilise `tar.gz` stdlib, sans installation. Une variante zstd pourrait suivre
après revue/autorisation ; le choix de codec ne justifie pas de perdre des preuves.

`archive_pytest_evidence.py` : une source basetemp nommée, run/report terminal
apparenté, simulation par défaut (métadonnées uniquement), opérateur attestant
l'absence de writer pour apply ; pas de balayage/purge automatique. Source jamais
retirée ; symlinks archivés comme valeurs sans suivre leurs cibles, hardlinks
internes conservés. Fichiers spéciaux, liens externes d'inode et xattrs/flags
refusés. Byte hashes, chemin/type, mode, uid/gid, mtime_ns, liens vérifiés. Atime,
ACL et métadonnées forensiques non listées ne sont pas une promesse de conservation.
Une source exigeant ces métadonnées nécessite une procédure d'archivage adaptée.

Apply : nice15, un job, max512 Mo non compressés/50000 entrées ; sondes
disque/swap avant et pendant. Réserve≥5 Go **plus** borne temporaire d'archive.
Source relue/comparée avant publication exclusive0600 ; partial conservé si
échec. Archive, manifeste et SHA valides requis ; pas d'extraction automatique.
Les grandes scans/hashages ne commencent pas sous le seuil. Le script ne peut
donc pas réparer à lui seul le manque d'espace actuel sans lever un garde-fou :
Romain doit décider d'une récupération d'espace préalable ou d'un stockage externe
et d'une procédure de déplacement séparée. Aucune dérogation ajoutée.

Exemple après import, **simulation seulement**, sur un ancien run terminé :

```sh
PYTHONDONTWRITEBYTECODE=1 .local-qualification/brief-test-runtime-claude-3c-v1/bin/python -B \
  scripts/archive_pytest_evidence.py \
  --source .local-qualification/405-test-isolation-codex-v4/v4-tmp \
  --finished-report .local-qualification/405-test-isolation-codex-v4/report.json \
  --output .local-qualification/405-test-isolation-codex-v4/v4-tmp.tar.gz
```

Ne pas copier ce chemin si le run n'existe pas dans le clone Claude : choisir
explicitement un run fini du clone concerné. Apply éventuel après GO et espace :
même commande précédée de `/usr/bin/nice -n 15`, plus `--apply --attest-finished`.
Toujours zéro retrait des originaux et zéro espace récupéré par ce script.

## 5. Commandes après récupération d'espace / responsabilités

Depuis le clone ayant importé la référence nommée, **nouveau dossier** :

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:v4/src:v4 \
  MEMORY_V4_CL100K_PATH=/Users/romain/.config/memory-os/cl100k_base.tiktoken \
  .local-qualification/brief-test-runtime-claude-3c-v1/bin/python -B \
  scripts/qualify_405_test_isolation.py --suite full \
  --output .local-qualification/405-safety-p1-claude-v1
```

Attendus : ≥585v3 / ≥1532v4 (22TCP), zéro échec/skip, configurations inchangées.
Les 18 nouveaux cas unitaires préflight/archives et 2 TCP sont inclus. Ni seuil abaissé ni reprise
automatique après garde machine. T05/release4.0.5 séparés, aucun wheel fabriqué ici.

Lint complémentaire, mêmes règles que les qualifications précédentes :

```sh
/opt/homebrew/bin/ruff check --no-cache --config pyproject.toml src tests scripts
/opt/homebrew/bin/ruff check --no-cache --config pyproject.toml src tests v4/src v4/tests v4/bench scripts --select E9,F
```

Claude : revoir les refus préflight et les contracts ; fournir les cas de
concurrence/handoff et préparer les hooks L7b décrits dans le contrat ; confirmer
L9 uniquement brouillons ; préparer pause/status/reconnexion pour les drills S4.
Romain : récupération d'espace/remplacement éventuel des originaux archivés,
budget/placement des backups et futurs GO de publication/bascule/activation.
