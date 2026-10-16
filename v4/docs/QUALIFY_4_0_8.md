# Qualification 4.0.8 — chaîne finale, production4.0.7 maintenue

16 octobre 2026. **Aucune publication ni bascule autorisée par ces commandes.**
Exécution Claude hors sandbox, séquentielle, nouveaux dossiers, arrêt au premier
échec. Ne déplacer/écraser aucun dossier de preuve existant. Aucun téléchargement.
La version finale évite de produire un deuxième jeu d'artefacts : doctor accepte
PEP440 désormais, mais il n'y a aucun besoin fonctionnel de suffixer cette RC.

## Contrat et espace disque

La source fonctionnelle qualifiée est6182aa5 (796v3/2174v4 dont32TCP). La RC ne
change sous v4/src que __version__=4.0.8 ; **ces nouveaux octets doivent être
requalifiés**, pas assimilés aux preuves historiques. Manifest release_ready=false
jusqu'à source+artefacts, copie/S4, recollecte P-B et relais installés verts.
32 identités TCP exactes ; aucune erreur/skip admise, test lent désélectionné
par le harnais standard puis test sémantique cached séparé obligatoire.

| Étape | Place supplémentaire à prévoir (Go décimaux, estimations) | Réserve / départ |
|---|---:|---|
| Suite complète isolée | 0,20 (135 Mio observés pour le lot local précédent) | ≥3,20 libres, garde3 permanente |
| Build direct+sdist / installs-smokes / twine | 0,25 | ≥3,25 libres ; dépendances existantes réutilisées |
| Source TCP+semantic+v3 | 0,20 ; modèle déjà caché requis | ≥3,20 libres ; pas de téléchargement si cache absent |
| Venvs wheel+sdist hors dépôt | 0,10 (2×28 Mio observés pour4.0.7) | ≥3,10 libres ; .pth vers dépendances qualifiées |
| Copie5workers + HTTP/doctor + S4×2 | budget20F + réserve5 ; minimum de départ7 | **attendre ≥max(7 ; 5+20F) libres** |
| Recollecte P-B/scoring | 0,10 | ≥3,10 libres, hash seulement |
| Relais installés | 0,05 | ≥3,05 libres ; modèles hash, TCP privé |

F = octets DB+WAL+SHM source, exprimés en Go. Garder les preuves des étapes
précédentes : leur coût s'additionne (environ0,85Go provisionnés avant la copie).
Ce tableau est un budget prudent, pas une garantie de taille maximale. Le garde
mesure le disque pendant les commandes. Ne pas supprimer/comprimer les fichiers
d'une autre session pour passer. Une fluctuation externe peut encore faire refuser.

**À ~5,2Go actuellement, exécuter seulement les étapes légères si les marges
restent suffisantes, puis ATTENDRE avant la copie/S4.** Le runner impose7Go ou
5+20F avant toute coupe live. S4 conserve son calcul propre (5Go+3F environ sur
la situation actuelle, ~5,15Go), indépendamment de l'override3Go. Ni saut, ni
réutilisation des preuves S44.0.7 : readiness/doctor4.0.8 participent au service
restauré. Un refus S4 laisse release_ready=false ; aucune dispense automatique.
Swap≤8,5Go mesurable ; nice15, concurrence1. Au plus un job de cette chaîne actif.

## 1. Arbre et source

Importer le bundle `release/4.0.8` en préservant les configs privées. Aucun autre
agent ne modifie l'arbre pendant la chaîne. Conserver les logs des deux lints.

```sh
cd "$HOME/Documents/Codex/2026-10-09/v/work/implementation"
RC_PY="$HOME/memory-os-local/qual-envs/407-installed-v2/wheel/bin/python"
RC_BUILD_PY=/opt/homebrew/bin/python3.11
RC_COMMIT=$(git rev-parse HEAD)
export PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:v4/src:v4:scripts
export MEMORY_V4_MIN_FREE_BYTES=3000000000
export MEMORY_V4_CL100K_PATH="$HOME/.config/memory-os/cl100k_base.tiktoken"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
unset MEMORY_V4_PROJECT_REGISTRY MEMORY_V4_EMBED_MODEL MEMORY_V4_BUILD_COMMIT

git diff 6182aa581626013e74f6c1987262171671baaf23 "$RC_COMMIT" -- v4/src
nice -n 15 "$RC_PY" -B scripts/qualify_405_test_isolation.py --suite full \
  --output .local-qualification/408rc-full-claude-v1
/opt/homebrew/bin/ruff check --no-cache --config pyproject.toml src tests scripts
/opt/homebrew/bin/ruff check --no-cache --config pyproject.toml \
  src tests v4/src v4/tests v4/bench scripts --select E9,F
```

## 2. Artefacts et manifeste (garde fermée)

```sh
nice -n 15 "$RC_PY" -B scripts/qualify_distribution_d1a.py --build \
  --source-commit "$RC_COMMIT" --build-python "$RC_BUILD_PY" \
  --output .local-qualification/408rc-artifacts-claude-v1
"$RC_PY" -B scripts/make_release_408.py --source-commit "$RC_COMMIT" \
  --artifact-proof .local-qualification/408rc-artifacts-claude-v1/report.json \
  --full-proof .local-qualification/408rc-full-claude-v1/report.json \
  --output .local-qualification/release-4.0.8-v1
RC_MANIFEST="$PWD/.local-qualification/release-4.0.8-v1/manifest.json"
nice -n 15 "$RC_PY" -B scripts/source_release_checks_408.py \
  --manifest "$RC_MANIFEST" \
  --output .local-qualification/release-4.0.8-v1/source-proof-input.json
"$RC_PY" -B scripts/verify_release_manifest.py "$RC_MANIFEST" \
  --record-source-proof .local-qualification/release-4.0.8-v1/source-proof-input.json
```

PEP517 direct/via-sdist, installations hors source, marqueurs, cinq commandes
installées, preview sans écritures, descripteur et twine strict requis. Hashes et
tailles viennent des octets construits ; aucune empreinte prédite. L'inventaire
atteste les nouveaux outils408 et leurs dépendances partagées. Les scripts407
restent conservés ; leur manifeste doit se vérifier avec SON outillage scellé,
pas avec l'arbre408 ayant un nouveau verify_release_manifest.py.

## 3. Installations puis copie installée schema8→8 (Claude uniquement)

```sh
RC_ENVS="$HOME/memory-os-local/qual-envs/408rc-installed-v1"
nice -n 15 "$RC_PY" -B scripts/prepare_release_408_envs.py \
  --manifest "$RC_MANIFEST" --build-python "$RC_BUILD_PY" --output "$RC_ENVS"
RC_PLIST="$HOME/Library/LaunchAgents/com.romain.memory-os-v4.plist"
RC_STORE=$("$RC_PY" -B - "$RC_PLIST" <<'PY'
import plistlib,sys
from pathlib import Path
print(plistlib.loads(Path(sys.argv[1]).read_bytes())['EnvironmentVariables']['MEMORY_V4_DIR'])
PY
)
# PAUSE obligatoire ici tant que ≥max(7Go,5Go+20F) n'est pas disponible.
nice -n 15 "$RC_PY" -B scripts/qualify_sqlite_release_408.py \
  --manifest "$RC_MANIFEST" --environments "$RC_ENVS/environments.json" \
  --baseline-python "$HOME/.memory-os-runtime/venv-4.0.7/bin/python" \
  --source-dir "$RC_STORE" --production-plist "$RC_PLIST" \
  --expected-instance-id 286dfcb0357940468aedb9617a2ccd9c \
  --output-root .local-qualification/408rc-copy-claude-v1
"$RC_PY" -B scripts/verify_release_manifest.py "$RC_MANIFEST" \
  --record-copy-proof .local-qualification/408rc-copy-claude-v1/report.json
```

Coupe par API backup du runtime4.0.7, source ouverte en lecture seule, aucun
moteur candidat dessus. Cinq workers : baseline4.0.7, wheel/sdist4.0.8 et reopens.
Comparaison exhaustive de TOUTES les tables/objets SQL, métadonnées/cache inclus,
autorité et instance identiques. Journal préexistant non vide admissible mais
inchangé (génération, coverage_start, événements). Aucune migration. MPS/native32,
C4 identiques ; zéro miss/encodage corpus dès premier open et reopen ; dix recherches
figées au même at_time, ≥1hit chacune dans CHAQUE worker, classements identiques.
Démarrage≤120s/worker, chaîne copie≤900s. HTTP --warm/doctor read_only sur port et
jeton privés, doctor0FAIL. Backup et drill service liés explicitement à la même
archive, writer privé ouvert pendant S4, deux artefacts installés réellement testés.

## 4. P-B RC : nouveau binding, mêmes règles, aucune lecture du gold par Codex

```sh
PB_DIR="$HOME/memory-os-local/brief_v5_val/pb"
PB_SEAL="$PB_DIR/sealed_pb_v1_4"
PB_INPUT_SHA=70ab2c61172f5401ea12b68a2b3566e593c25689a42e8f9b2eac21d2a4d05de0
RC_BIND="$PWD/.local-qualification/408rc-pb-binding-v1.json"
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
  --table "$MEMORY_V4_CL100K_PATH" --output .local-qualification/408rc-pb-collect-claude-v1
nice -n 15 "$RC_PY" -B scripts/score_release_408_gate.py \
  --manifest "$RC_MANIFEST" --scorer "$PB_DIR/score_pb_v3_9.py" \
  --cases "$PB_SEAL/cases_pb_v1_4.jsonl" \
  --collection .local-qualification/408rc-pb-collect-claude-v1 \
  --output .local-qualification/408rc-pb-score-claude-v1.json
"$RC_PY" -B scripts/record_release_408_gate.py \
  --collection .local-qualification/408rc-pb-collect-claude-v1/report.json \
  --scorer .local-qualification/408rc-pb-score-claude-v1.json \
  --output .local-qualification/408rc-pb-proof-claude-v1.json
"$RC_PY" -B scripts/verify_release_manifest.py "$RC_MANIFEST" \
  --record-gate-proof .local-qualification/408rc-pb-proof-claude-v1.json
```

Scorer/helper hachés, copies privées uniquement, aucun changement de gold/seuils.
40/40, six gates et replay true requis. Limites conservées : MAC exact exclu,
rejeu-oracle candidat pour défauts non sécuritaires, classement oubli/requête non
indépendant, périmètre synthétique non exhaustif, authenticité locale non signée.
Les nouveaux chemins de provenance sont aussi couverts par les32TCP de source ;
ces gates ne mesurent pas l'utilité réelle ni une supériorité concurrentielle.

## 5. Relais et vérification finale

```sh
nice -n 15 "$RC_PY" -B scripts/installed_relay_e2e.py \
  --manifest "$RC_MANIFEST" --environments "$RC_ENVS/environments.json" \
  --output .local-qualification/408rc-installed-relays-claude-v1.json
"$RC_PY" -B scripts/verify_release_manifest.py "$RC_MANIFEST" \
  --record-relay-proof .local-qualification/408rc-installed-relays-claude-v1.json
"$RC_PY" -B scripts/verify_release_manifest.py "$RC_MANIFEST"
```

Seule la preuve finale peut lever release_ready. Inventaire/round-trip, panne,
reconnexion sans rejeu, statut local pendant panne et handshake4.0.8 requis pour
wheel ET sdist. Publication/bascule nécessitent encore leurs GOs distincts.
Bascule future : `SWITCH_4_0_8_SQLITE.md` ; a9 doit être adapté/revu/répété,
jamais exécuté tel quel avec ses constantes4.0.7.
