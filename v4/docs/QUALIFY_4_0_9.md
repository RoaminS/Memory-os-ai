# Qualification 4.0.9 — chaîne finale, production 4.0.7 maintenue

21 novembre 2026 — préparation de l'outillage seulement (rien n'a été exécuté).
**Aucune publication ni bascule autorisée par ces commandes : Romain décide.**
Exécution Claude hors sandbox, séquentielle, nouveaux dossiers, arrêt au premier
échec. Ne déplacer/écraser aucun dossier de preuve existant (408 et antérieurs
conservés, jamais réutilisés). Aucun téléchargement.

## Contrat

4.0.9 = correctif du rapport de santé 4.0.9.dev1 (`v4/src/memory_os_v4/ops/health.py`)
+ documentation et numéro de version. Base runtime FIGÉE conservée : 9ecb4d0
(frontière RCv2 de 4.0.8). Règle moteur appliquée par `release_409_checks.py` :

- chemins modifiés sous `v4/src` depuis 9ecb4d0 ⊆ {`v4/src/memory_os_v4/__init__.py`,
  `v4/src/memory_os_v4/ops/health.py`} (`git diff --no-renames`) ;
- `__init__.py` doit changer, et UNIQUEMENT par la ligne `__version__ = "4.0.8"` →
  `__version__ = "4.0.9"` ;
- tout autre chemin (notamment `brief*.py`, `text.py`, `bm25.py`, store/sqlite) refuse ;
- `ops/health.py` doit être présent dans le diff et EXACTEMENT le correctif relu (contrôle inconditionnel) (blob git `3aa1dfe5d023f6f7efa91cbe6c41e1abeb5ba8b9`,
  a745b40) : tout autre contenu refuse ;
- comparaison en octets (une conversion CRLF ou une fin de ligne modifiée refuse).

Le manifeste enregistre la base, la liste autorisée, les chemins observés et le
transfert de preuves. **P-A n'est PAS relancée** : le périmètre fonctionnel P-A est inchangé (tout
fichier de `v4/src` autre que `__init__.py` et `ops/health.py` est identique octet pour octet à la base ;
`ops/health.py`, épinglé, n'est importé que par `ops/cli.py` et `ops/doctor.py`). Preuve transférée :
gate P-A 40/40 scellé acquis sur ceedb5c (CHANGELOG 4.0.7rc1). TOUT le reste est refait
sur le commit final, sans transfert d'aucune preuve 4.0.8 : suite complète +
source (32 identités TCP exactes, inchangées : le correctif santé n'ajoute aucun
test TCP), artefacts, copie/S4, recollecte P-B liée au commit final, relais
installés. release_ready=false jusqu'à la dernière preuve. Aucune erreur/skip
admise ; test lent désélectionné par le harnais standard puis test sémantique
cached séparé obligatoire.

**Ne pas utiliser `~/memory-os-local/ops/qualify_409_v1/v2/v3_chain.sh`** (désactivés, non
validés) **ni
`~/memory-os-local/ops/qualify_409_c1_chain.sh`** (orphelin du
31/10, visait un autre commit, 06199a2). Seules les commandes ci-dessous valent.

## Espace disque

Même budget et mêmes gardes que 4.0.8 (disque libre actuel ≈7Go).

| Étape | Place supplémentaire à prévoir (Go décimaux, estimations) | Réserve / départ |
|---|---:|---|
| Suite complète isolée | 0,20 (135 Mio observés pour le lot local précédent) | ≥3,20 libres, garde3 permanente |
| Build direct+sdist / installs-smokes / twine | 0,25 | ≥3,25 libres ; dépendances existantes réutilisées |
| Source TCP+semantic+v3 | 0,20 ; modèle déjà caché requis | ≥3,20 libres ; pas de téléchargement si cache absent |
| Venvs wheel+sdist hors dépôt | 0,10 (2×28 Mio observés pour4.0.7) | ≥3,10 libres ; .pth vers dépendances qualifiées |
| Copie5workers + HTTP/doctor + S4×2 | legacy≥7 ; adaptive réserve+10F+288MiB | **legacy≥max(7 ; 5+20F), adaptive≈4,08Go pour F≈78Mo** |
| Recollecte P-B/scoring | 0,10 | ≥3,10 libres, hash seulement |
| Relais installés | 0,05 | ≥3,05 libres ; modèles hash, TCP privé |

F = octets DB+WAL+SHM source, exprimés en Go. Les preuves des étapes précédentes
s'additionnent (environ0,85Go provisionnés avant la copie). Budget prudent, pas
une garantie. Le garde mesure le disque pendant les commandes. Ne pas supprimer
ni comprimer les fichiers d'une autre session pour passer.

**legacy impose d'attendre sous ≈7Go ; adaptive_v1 peut être qualifiée sur
copies si son budget calculé reste disponible.** Choix EXPLICITE et PRIVÉ, limité
à la commande de copie/S4 (jamais exporté aux suites), pas une modification du
défaut ni de la production. S4 conserve réserve+6F+32MiB. L'override3Go du
harnais ne réduit aucune de ces formules. Ni saut, ni réutilisation d'un S4
antérieur ; une erreur laisse release_ready=false. Swap≤8,5Go mesurable ;
nice15, concurrence1. Au plus un job de cette chaîne actif.

**Règle de Romain, appliquée en permanence par `~/memory-os-local/ops/qualify_409_v4_chain.sh` :** pendant
TOUTES les étapes, disque libre ≥ 5 Go et swap ≤ 8,5 Go (Go décimaux), mesurés toutes les 2 s ; sinon tout l'arbre de processus de l'étape
(descendants suivis par point fixe, plus balayage des processus démarrés après la chaîne sous la branche ou sous
`qual-envs/409-installed-claude-*`) est gelé puis arrêté, l'étape marquée en échec, sans relance automatique.
Verrou commun `ops/logs/qualify_409.lock`, conservé tant qu'un processus survit. La copie ne démarre qu'avec ≥ 9,2 Go libres
(budget adaptive ≈4,1 Go + 5 Go). Les départs à 3,05–3,25 Go du tableau sont les gardes internes du harnais
(MEMORY_V4_MIN_FREE_BYTES=3e9), un filet de sécurité, pas le seuil de la chaîne. Mesure de swap absente ou
illisible : refus. Voir
`BACKUP_CAPACITY_4_0_8.md` et `SQLITE_GROWTH_2026_10_26.md` (inchangés).

## 1. Arbre et source

Arbre : worktree `release/4.0.9-hygiene`. Aucun autre agent ne modifie l'arbre
pendant la chaîne. `RC_PY` doit exister (environnement installé 4.0.7 qualifié,
réutilisé comme en 4.0.8) : sinon ARRÊT, ne pas le recréer dans cette chaîne.
Conserver les logs des deux lints.

```sh
cd "$HOME/memory-os-local/codex_runs/release-409-hygiene"
RC_PY="$HOME/memory-os-local/qual-envs/407-installed-v2/wheel/bin/python"
test -x "$RC_PY" || { echo "RC_PY absent : arrêt"; exit 1; }
RC_BUILD_PY=/opt/homebrew/bin/python3.11
RC_COMMIT=$(git rev-parse HEAD)
test -z "$(git status --porcelain)" || { echo "arbre non propre : arrêt"; exit 1; }
export PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:v4/src:v4:scripts
export MEMORY_V4_MIN_FREE_BYTES=3000000000
# Source/build/tests : défauts du produit, indépendants du shell opérateur.
unset MEMORY_V4_SQLITE_STORAGE_POLICY MEMORY_V4_SQLITE_BACKUP_MAX_BYTES
export MEMORY_V4_CL100K_PATH="$HOME/.config/memory-os/cl100k_base.tiktoken"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
unset MEMORY_V4_PROJECT_REGISTRY MEMORY_V4_EMBED_MODEL MEMORY_V4_BUILD_COMMIT

# Attendu : exactement __init__.py (ligne __version__) et ops/health.py.
git diff --no-renames --name-only 9ecb4d06f68e2c7efddf77aad66f4ed5f147f356 "$RC_COMMIT" -- v4/src
git diff 9ecb4d06f68e2c7efddf77aad66f4ed5f147f356 "$RC_COMMIT" -- v4/src/memory_os_v4/__init__.py
nice -n 15 "$RC_PY" -B scripts/qualify_405_test_isolation.py --suite full \
  --output .local-qualification/409-full-claude-v1
/opt/homebrew/bin/ruff check --no-cache --config pyproject.toml src tests scripts
/opt/homebrew/bin/ruff check --no-cache --config pyproject.toml \
  src tests v4/src v4/tests v4/bench scripts --select E9,F
```

## 2. Artefacts et manifeste (garde fermée)

```sh
nice -n 15 "$RC_PY" -B scripts/qualify_distribution_d1a.py --build \
  --source-commit "$RC_COMMIT" --build-python "$RC_BUILD_PY" \
  --output .local-qualification/409-artifacts-claude-v1
"$RC_PY" -B scripts/make_release_409.py --source-commit "$RC_COMMIT" \
  --artifact-proof .local-qualification/409-artifacts-claude-v1/report.json \
  --full-proof .local-qualification/409-full-claude-v1/report.json \
  --output .local-qualification/release-4.0.9-v1
RC_MANIFEST="$PWD/.local-qualification/release-4.0.9-v1/manifest.json"
nice -n 15 "$RC_PY" -B scripts/source_release_checks_409.py \
  --manifest "$RC_MANIFEST" \
  --output .local-qualification/release-4.0.9-v1/source-proof-input.json
"$RC_PY" -B scripts/verify_release_manifest.py "$RC_MANIFEST" \
  --record-source-proof .local-qualification/release-4.0.9-v1/source-proof-input.json
```

PEP517 direct/via-sdist, installations hors source, marqueurs, cinq commandes
installées, preview sans écritures, descripteur et twine strict requis. Hashes et
tailles viennent des octets construits ; aucune empreinte prédite. L'inventaire
atteste les outils409 et leurs dépendances partagées. Les scripts407/408
restent conservés ; un manifeste 4.0.8 se vérifie avec SON outillage scellé.

## 3. Installations puis copie installée schema8→8 (Claude uniquement)

```sh
RC_ENVS="$HOME/memory-os-local/qual-envs/409-installed-claude-v1"
nice -n 15 "$RC_PY" -B scripts/prepare_release_409_envs.py \
  --manifest "$RC_MANIFEST" --build-python "$RC_BUILD_PY" --output "$RC_ENVS"
RC_PLIST="$HOME/Library/LaunchAgents/com.romain.memory-os-v4.plist"
RC_STORE=$("$RC_PY" -B - "$RC_PLIST" <<'PY'
import plistlib,sys
from pathlib import Path
print(plistlib.loads(Path(sys.argv[1]).read_bytes())['EnvironmentVariables']['MEMORY_V4_DIR'])
PY
)
# Choix explicite PRIVÉ, limité à cette commande (jamais exporté aux suites).
# PAUSE si le budget calculé refuse (legacy≥7Go ; adaptive selon F).
MEMORY_V4_SQLITE_STORAGE_POLICY=adaptive_v1 \
MEMORY_V4_SQLITE_BACKUP_MAX_BYTES=4294967296 \
nice -n 15 "$RC_PY" -B scripts/qualify_sqlite_release_409.py \
  --manifest "$RC_MANIFEST" --environments "$RC_ENVS/environments.json" \
  --baseline-python "$HOME/.memory-os-runtime/venv-4.0.7/bin/python" \
  --source-dir "$RC_STORE" --production-plist "$RC_PLIST" \
  --expected-instance-id 286dfcb0357940468aedb9617a2ccd9c \
  --output-root .local-qualification/409-copy-claude-v1
"$RC_PY" -B scripts/verify_release_manifest.py "$RC_MANIFEST" \
  --record-copy-proof .local-qualification/409-copy-claude-v1/report.json
```

Baseline = production4.0.7 (`venv-4.0.7`). Le plist et le magasin ne sont que
LUS ; aucun contrôle de service. Coupe par API backup du runtime CANDIDAT4.0.9,
source ouverte en lecture seule ; la baseline ouvre uniquement les snapshots
privés scellés. Cinq workers : baseline4.0.7, wheel/sdist4.0.9 et reopens.
Comparaison exhaustive des tables/objets SQL, autorité et instance identiques,
journal inchangé, aucune migration. MPS/native32, zéro miss/encodage corpus,
dix recherches figées ≥1hit et classements identiques. Démarrage≤120s/worker,
chaîne≤900s. Doctor read_only (il lit `ops/health.py` : preuve à refaire),
0FAIL. Backup et drill service liés à la même archive, deux artefacts testés.

## 4. P-B RC : nouveau binding sur le commit final, mêmes règles, aucune lecture du gold

```sh
PB_DIR="$HOME/memory-os-local/brief_v5_val/pb"
PB_SEAL="$PB_DIR/sealed_pb_v1_4"
PB_INPUT_SHA=70ab2c61172f5401ea12b68a2b3566e593c25689a42e8f9b2eac21d2a4d05de0
RC_BIND="$PWD/.local-qualification/409-pb-binding-claude-v1.json"
"$RC_PY" -B - "$PB_SEAL/manifest_v1_4.json" "$RC_COMMIT" "$PB_INPUT_SHA" "$RC_BIND" <<'PY'
import datetime,hashlib,json,sys
from pathlib import Path
m,c,h,out=sys.argv[1:]
b=dict(candidate_commit=c,inputs_sha256=h,manifest_sha256=hashlib.sha256(Path(m).read_bytes()).hexdigest(),bound_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),bound_by='Claude')
with Path(out).open('x') as f: json.dump(b,f,indent=2)
PY
nice -n 15 "$RC_PY" -B scripts/run_integrity_v5_validation.py --run \
  --inputs "$PB_SEAL/inputs_v1_4.jsonl" --manifest "$PB_SEAL/manifest_v1_4.json" \
  --binding "$RC_BIND" --inputs-sha256 "$PB_INPUT_SHA" --candidate-commit "$RC_COMMIT" \
  --table "$MEMORY_V4_CL100K_PATH" --output .local-qualification/409-pb-collect-claude-v1
nice -n 15 "$RC_PY" -B scripts/score_release_409_gate.py \
  --manifest "$RC_MANIFEST" --scorer "$PB_DIR/score_pb_v3_9.py" \
  --cases "$PB_SEAL/cases_pb_v1_4.jsonl" \
  --collection .local-qualification/409-pb-collect-claude-v1 \
  --output .local-qualification/409-pb-score-claude-v1.json
"$RC_PY" -B scripts/record_release_409_gate.py \
  --collection .local-qualification/409-pb-collect-claude-v1/report.json \
  --scorer .local-qualification/409-pb-score-claude-v1.json \
  --output .local-qualification/409-pb-proof-claude-v1.json
"$RC_PY" -B scripts/verify_release_manifest.py "$RC_MANIFEST" \
  --record-gate-proof .local-qualification/409-pb-proof-claude-v1.json
```

Scorer/helper hachés, copies privées uniquement, aucun changement de gold/seuils.
L'archive `git archive <commit> v4/src pyproject.toml` lie la collecte au commit
final. 40/40, six gates et replay true requis. Limites conservées : MAC exact
exclu, rejeu-oracle candidat pour défauts non sécuritaires, classement
oubli/requête non indépendant, périmètre synthétique non exhaustif, authenticité
locale non signée. Ces gates ne mesurent pas l'utilité réelle.

## 5. Relais et vérification finale

```sh
nice -n 15 "$RC_PY" -B scripts/installed_relay_e2e.py \
  --manifest "$RC_MANIFEST" --environments "$RC_ENVS/environments.json" \
  --output .local-qualification/409-installed-relays-claude-v1.json
"$RC_PY" -B scripts/verify_release_manifest.py "$RC_MANIFEST" \
  --record-relay-proof .local-qualification/409-installed-relays-claude-v1.json
"$RC_PY" -B scripts/verify_release_manifest.py "$RC_MANIFEST"
```

Seule la preuve finale peut lever release_ready. Inventaire/round-trip, panne,
reconnexion sans rejeu, statut local pendant panne et handshake4.0.9 requis pour
wheel ET sdist. `scripts/installed_relay_case.py` exécute le brief compact et
`memory_request_status` pendant la panne pour 4.0.8 et 4.0.9 (ces versions manquaient de sa liste dans ce dépôt,
ce qui aurait fait refuser `--record-relay-proof`). Ce script fait partie de l'inventaire scellé 4.0.8 : un
manifeste 4.0.8 se vérifie avec son outillage historique, jamais en réécrivant ses empreintes.

**Attention : `--record-relay-proof` positionne déjà `release_ready=true`.** Seul compte le résultat de la
vérification finale : la chaîne v4 écrit `CHAIN_RESULT=PASS` dans son dossier de logs uniquement après elle ; un
arrêt ou un refus de cette dernière commande interdit toute publication même si `release_ready` vaut true. release_ready=true n'autorise ni publication PyPI ni bascule de
production : chacune exige un GO distinct de Romain. La procédure de bascule
(`SWITCH_4_0_8_SQLITE.md`) doit être adaptée/revue pour 4.0.9, jamais exécutée
telle quelle.
