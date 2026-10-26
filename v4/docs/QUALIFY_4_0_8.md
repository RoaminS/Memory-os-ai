# Qualification 4.0.8 — chaîne finale, production4.0.7 maintenue

26 octobre 2026 — RC v2. **Aucune publication ni bascule autorisée par ces commandes.**
Exécution Claude hors sandbox, séquentielle, nouveaux dossiers, arrêt au premier
échec. Ne déplacer/écraser aucun dossier de preuve existant. Aucun téléchargement.
La version finale évite de produire un deuxième jeu d'artefacts : doctor accepte
PEP440 désormais, mais il n'y a aucun besoin fonctionnel de suffixer cette RC.

## Contrat et espace disque

La RC v2 ajoute les correctifs de capacité/flux S4 et la policy disque. Elle
reste4.0.8 ; source runtime FIGÉE sur9ecb4d0, aucun changement sous v4/src après
ce gel. Ce gel n'est PAS une qualification : preuves RCv1 invalides pour ces
nouveaux octets. Refaire intégralement source/artefacts/copie/S4/P-B/relais dans
les dossiers v2 ci-dessous. release_ready=false jusqu'à la dernière preuve.
32 identités TCP exactes ; aucune erreur/skip admise, test lent désélectionné
par le harnais standard puis test sémantique cached séparé obligatoire.

| Étape | Place supplémentaire à prévoir (Go décimaux, estimations) | Réserve / départ |
|---|---:|---|
| Suite complète isolée | 0,20 (135 Mio observés pour le lot local précédent) | ≥3,20 libres, garde3 permanente |
| Build direct+sdist / installs-smokes / twine | 0,25 | ≥3,25 libres ; dépendances existantes réutilisées |
| Source TCP+semantic+v3 | 0,20 ; modèle déjà caché requis | ≥3,20 libres ; pas de téléchargement si cache absent |
| Venvs wheel+sdist hors dépôt | 0,10 (2×28 Mio observés pour4.0.7) | ≥3,10 libres ; .pth vers dépendances qualifiées |
| Copie5workers + HTTP/doctor + S4×2 | legacy≥7 ; adaptive réserve+10F+288MiB | **legacy≥max(7 ; 5+20F), adaptive≈4,08Go ici** |
| Recollecte P-B/scoring | 0,10 | ≥3,10 libres, hash seulement |
| Relais installés | 0,05 | ≥3,05 libres ; modèles hash, TCP privé |

F = octets DB+WAL+SHM source, exprimés en Go. Garder les preuves des étapes
précédentes : leur coût s'additionne (environ0,85Go provisionnés avant la copie).
Ce tableau est un budget prudent, pas une garantie de taille maximale. Le garde
mesure le disque pendant les commandes. Ne pas supprimer/comprimer les fichiers
d'une autre session pour passer. Une fluctuation externe peut encore faire refuser.

**À~5,2Go : legacy impose d’attendre ; adaptive_v1 peut être qualifiée sur
copies si son budget calculé (≈4,08Go ici) reste disponible.** Choix EXPLICITE,
pas une modification du défaut. S4 conserve réserve+6F+32MiB (≈5,50Go legacy,
≈3,50Go adaptive pourF≈78Mo). L’override3Go du harnais ne réduit aucune de ces
formules. Ni saut, ni réutilisation de S4v1 ; une erreur laisse release_ready=false.
Swap≤8,5Go mesurable ; nice15, concurrence1. Au plus un job de cette chaîne actif.

### Amendement RC v2 — capacité et réserve explicite

Lire `BACKUP_CAPACITY_4_0_8.md` et `SQLITE_GROWTH_2026_10_26.md`.
4GiB par défaut ; option MEMORY_V4_SQLITE_BACKUP_MAX_BYTES, toutes bornes
archive/snapshot/drill dérivées. Traitement par flux, format MOSB1 compatible.
La réserve legacy5gb reste le défaut. Le tableau ci-dessus utilise les formules du document
capacity :6F+32MiB de travail. À~78Mo source : legacy5,50Go / copie≥7Go.

Pour une qualification PRIVÉE de l'option proposée, sélectionner explicitement
`MEMORY_V4_SQLITE_STORAGE_POLICY=adaptive_v1` : S4≈3,50Go ; copie complète≈4,08Go
(réserve=max3Go/2F + travail6F+32MiB + copies4F +256MiB). Ne pas poursuivre si
le garde calculé refuse. Le choix n'active pas cette option en production ; la
mise à jour des jobs/configurations de production reste soumise à Romain.
Tous les répertoires v1 restent conservés. Pas de réutilisation de S4v1.

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
# Choix EXPLICITE pour la qualification PRIVÉE de la proposition seulement :
export MEMORY_V4_SQLITE_STORAGE_POLICY=adaptive_v1
# Cap optionnel explicite ; défaut identique4GiB, aucun réglage sur les jobs live :
export MEMORY_V4_SQLITE_BACKUP_MAX_BYTES=4294967296
export MEMORY_V4_CL100K_PATH="$HOME/.config/memory-os/cl100k_base.tiktoken"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
unset MEMORY_V4_PROJECT_REGISTRY MEMORY_V4_EMBED_MODEL MEMORY_V4_BUILD_COMMIT

git diff 9ecb4d06f68e2c7efddf77aad66f4ed5f147f356 "$RC_COMMIT" -- v4/src
nice -n 15 "$RC_PY" -B scripts/qualify_405_test_isolation.py --suite full \
  --output .local-qualification/408rc-full-claude-v2
/opt/homebrew/bin/ruff check --no-cache --config pyproject.toml src tests scripts
/opt/homebrew/bin/ruff check --no-cache --config pyproject.toml \
  src tests v4/src v4/tests v4/bench scripts --select E9,F
```

## 2. Artefacts et manifeste (garde fermée)

```sh
nice -n 15 "$RC_PY" -B scripts/qualify_distribution_d1a.py --build \
  --source-commit "$RC_COMMIT" --build-python "$RC_BUILD_PY" \
  --output .local-qualification/408rc-artifacts-claude-v2
"$RC_PY" -B scripts/make_release_408.py --source-commit "$RC_COMMIT" \
  --artifact-proof .local-qualification/408rc-artifacts-claude-v2/report.json \
  --full-proof .local-qualification/408rc-full-claude-v2/report.json \
  --output .local-qualification/release-4.0.8-v2
RC_MANIFEST="$PWD/.local-qualification/release-4.0.8-v2/manifest.json"
nice -n 15 "$RC_PY" -B scripts/source_release_checks_408.py \
  --manifest "$RC_MANIFEST" \
  --output .local-qualification/release-4.0.8-v2/source-proof-input.json
"$RC_PY" -B scripts/verify_release_manifest.py "$RC_MANIFEST" \
  --record-source-proof .local-qualification/release-4.0.8-v2/source-proof-input.json
```

PEP517 direct/via-sdist, installations hors source, marqueurs, cinq commandes
installées, preview sans écritures, descripteur et twine strict requis. Hashes et
tailles viennent des octets construits ; aucune empreinte prédite. L'inventaire
atteste les nouveaux outils408 et leurs dépendances partagées. Les scripts407
restent conservés ; leur manifeste doit se vérifier avec SON outillage scellé,
pas avec l'arbre408 ayant un nouveau verify_release_manifest.py.

## 3. Installations puis copie installée schema8→8 (Claude uniquement)

```sh
RC_ENVS="$HOME/memory-os-local/qual-envs/408rc-installed-v2"
nice -n 15 "$RC_PY" -B scripts/prepare_release_408_envs.py \
  --manifest "$RC_MANIFEST" --build-python "$RC_BUILD_PY" --output "$RC_ENVS"
RC_PLIST="$HOME/Library/LaunchAgents/com.romain.memory-os-v4.plist"
RC_STORE=$("$RC_PY" -B - "$RC_PLIST" <<'PY'
import plistlib,sys
from pathlib import Path
print(plistlib.loads(Path(sys.argv[1]).read_bytes())['EnvironmentVariables']['MEMORY_V4_DIR'])
PY
)
# PAUSE si le budget calculé refuse (legacy≥7Go ; adaptive selonF, ≈4,08Go ici).
nice -n 15 "$RC_PY" -B scripts/qualify_sqlite_release_408.py \
  --manifest "$RC_MANIFEST" --environments "$RC_ENVS/environments.json" \
  --baseline-python "$HOME/.memory-os-runtime/venv-4.0.7/bin/python" \
  --source-dir "$RC_STORE" --production-plist "$RC_PLIST" \
  --expected-instance-id 286dfcb0357940468aedb9617a2ccd9c \
  --output-root .local-qualification/408rc-copy-claude-v2
"$RC_PY" -B scripts/verify_release_manifest.py "$RC_MANIFEST" \
  --record-copy-proof .local-qualification/408rc-copy-claude-v2/report.json
```

Coupe par API backup du runtime CANDIDAT4.0.8 (nouvelle capacité, lecture seule), source ouverte en lecture seule, aucun
moteur candidat dessus. La baseline4.0.7 ne peut plus prendre la coupe via son ancien S4 cap64MiB :
elle ouvre UNIQUEMENT les snapshots privés scellés via un lecteur SQL immutable
indépendant, avec le plafond du harnais. Aucun fichier de son runtime n’est
modifié ni monkeypatché. Cinq workers : baseline4.0.7, wheel/sdist4.0.8 et reopens.
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
RC_BIND="$PWD/.local-qualification/408rc-pb-binding-v2.json"
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
  --table "$MEMORY_V4_CL100K_PATH" --output .local-qualification/408rc-pb-collect-claude-v2
nice -n 15 "$RC_PY" -B scripts/score_release_408_gate.py \
  --manifest "$RC_MANIFEST" --scorer "$PB_DIR/score_pb_v3_9.py" \
  --cases "$PB_SEAL/cases_pb_v1_4.jsonl" \
  --collection .local-qualification/408rc-pb-collect-claude-v2 \
  --output .local-qualification/408rc-pb-score-claude-v2.json
"$RC_PY" -B scripts/record_release_408_gate.py \
  --collection .local-qualification/408rc-pb-collect-claude-v2/report.json \
  --scorer .local-qualification/408rc-pb-score-claude-v2.json \
  --output .local-qualification/408rc-pb-proof-claude-v2.json
"$RC_PY" -B scripts/verify_release_manifest.py "$RC_MANIFEST" \
  --record-gate-proof .local-qualification/408rc-pb-proof-claude-v2.json
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
  --output .local-qualification/408rc-installed-relays-claude-v2.json
"$RC_PY" -B scripts/verify_release_manifest.py "$RC_MANIFEST" \
  --record-relay-proof .local-qualification/408rc-installed-relays-claude-v2.json
"$RC_PY" -B scripts/verify_release_manifest.py "$RC_MANIFEST"
```

Seule la preuve finale peut lever release_ready. Inventaire/round-trip, panne,
reconnexion sans rejeu, statut local pendant panne et handshake4.0.8 requis pour
wheel ET sdist. Publication/bascule nécessitent encore leurs GOs distincts.
Bascule future : `SWITCH_4_0_8_SQLITE.md` ; a9 doit être adapté/revu/répété,
jamais exécuté tel quel avec ses constantes4.0.7.
