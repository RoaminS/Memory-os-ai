#!/bin/zsh
# Qualification 4.1.0 : installation réelle d'UN profil — G7, G8, M1 (v4/docs/QUALIFY_4_1_0.md §3, §4, §6).
# NON LANCÉ. À lancer uniquement par la chaîne relue, sur GO explicite de Romain.
# Un venv vierge, deux HOME jetables (env -i) : installation 1 = aperçu + --apply --demo + --undo --with-service + M1 ;
# installation 2 = --apply --demo + uninstall (aperçu puis --apply). Libellé de test local.memory-os-v4.qual410,
# port 18765 : jamais un libellé commençant par com.romain.memory-os-v4, jamais 8765.
# Ne touche jamais la production ni les vraies configurations client (empreintes avant/après, pid inchangé).
# Différence voulue avec run_install_proof_v2.sh : le filet de sécurité n'est PAS posé avant les préconditions ;
# il n'est armé qu'après la création prouvée du service par CE run, et ne vise qu'un service ré-attesté
# immédiatement avant son arrêt. Un libellé préexistant n'est jamais déchargé. Après un KILL aucun trap ne tourne :
# la détection de la chaîne couvre ce cas.
# Codes : 0 PASS | 1 échec d'une porte | 2 usage | 40 état launchd/port contraire ou inobservable (verrou à conserver).
set -u -o pipefail
zmodload zsh/datetime

LABEL="local.memory-os-v4.qual410"
PORT=18765
PROD_LABEL="com.romain.memory-os-v4"
PY=/opt/homebrew/bin/python3.11
SYS_PY=/usr/bin/python3
VERSION="4.1.0"
CL100K_SHA=223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7
GRANITE_DIR="models--ibm-granite--granite-embedding-97m-multilingual-r2"
SCRIPT_DIR="${0:A:h}"
HELPER="$SCRIPT_DIR/qualify_install_410_helper.py"
CATALOG="$SCRIPT_DIR/../v4/src/memory_os_v4/resources/provision_v1.json"
UID_N="$(id -u)"
REAL_HOME="$HOME"

PROFILE=""; VARIANT=""; WHEEL=""; WHEEL_SHA=""; P=""; COMMIT=""; RUN_TAG=""; SEED=""; GRANITE=""; PTH_PY=""; GO=""
while [ $# -gt 0 ]; do
  case "$1" in
    --profile) PROFILE="$2";; --variant) VARIANT="$2";; --wheel) WHEEL="$2";; --wheel-sha256) WHEEL_SHA="$2";;
    --proof-dir) P="$2";; --source-commit) COMMIT="$2";; --run-tag) RUN_TAG="$2";; --seed-cl100k) SEED="$2";;
    --granite-cache) GRANITE="$2";; --pth-python) PTH_PY="$2";; --network-go) GO="$2";;
    *) echo "usage : option inconnue $1"; exit 2;;
  esac
  shift 2 || { echo "usage : valeur manquante"; exit 2; }
done
usage() { echo "usage : $*"; exit 2; }
[[ "$PROFILE" == lexical || "$PROFILE" == semantic ]] || usage "--profile lexical|semantic"
[[ "$VARIANT" == autonomous || ( "$VARIANT" == pth && "$PROFILE" == semantic ) ]] || usage "--variant autonomous|pth (pth : sémantique seulement)"
[[ "$COMMIT" =~ '^[0-9a-f]{40}$' ]] || usage "--source-commit SHA complet"
[[ "$RUN_TAG" =~ '^v[0-9]+$' ]] || usage "--run-tag vN"
[[ -n "$WHEEL" && -n "$WHEEL_SHA" && -n "$P" && -n "$SEED" ]] || usage "--wheel --wheel-sha256 --proof-dir --seed-cl100k requis"
case "$LABEL" in "$PROD_LABEL"*) echo "libellé de test = production"; exit 2;; esac
[ "$PORT" != 8765 ] || exit 2
# Règle permanente : aucun chemin d'entrée ne peut désigner les dossiers scellés (jamais lus ni listés).
for arg in "$WHEEL" "$P" "$SEED" "$GRANITE" "$PTH_PY"; do
  case "$arg" in *brief_v5_val*|*brient_v5_val*) echo "REFUS : dossier scellé"; exit 2;; esac
done
# Acquisitions réseau : chacune exige son GO nominatif, transmis par la chaîne ; rien d'implicite.
has_go() { [[ ",$GO," == *",$1,"* ]]; }
if [ "$PROFILE" = lexical ]; then
  has_go N3 && has_go N6 || usage "profil lexical : GO N3 et N6 requis (--network-go N3,N6)"
  EXTRAS="[ops,brief]"
else
  EXTRAS="[ops,brief,semantic]"
  if [ "$VARIANT" = autonomous ]; then has_go N5 || usage "sémantique autonome : GO N5 requis (jamais de repli automatique sur .pth)"
  else [ -x "$PTH_PY" ] || usage "--pth-python requis pour la variante .pth"; fi
  [ -d "$GRANITE/snapshots" ] || usage "--granite-cache : cache local du modèle requis"
fi
[ ! -e "$P" ] || { echo "PROOF_DIR existe déjà : jamais réutilisé"; exit 2; }
[ -x "$PY" ] && [ -x "$SYS_PY" ] && [ -f "$HELPER" ] && [ -f "$CATALOG" ] || { echo "outil absent"; exit 2; }
[ "$(shasum -a 256 "$WHEEL" | cut -c1-64)" = "$WHEEL_SHA" ] || { echo "sha du wheel"; exit 2; }
[ "$(shasum -a 256 "$SEED" | cut -c1-64)" = "$CL100K_SHA" ] || { echo "sha de la table cl100k locale"; exit 2; }

mkdir -p "$P" && chmod 700 "$P" && cd "$P" || exit 2
exec > >(tee -a "$P/proof.log") 2>&1
VENV="$P/venv"; H1="$P/home1"; H2="$P/home2"; D1="$P/install1"; D2="$P/install2"
mkdir -p "$D1" "$D2" "$P/tmp" "$P/pip-cache"

helper() { "$SYS_PY" -I "$HELPER" "$@"; }
step() { echo; echo "=== $* ($(date '+%H:%M:%S'))"; }
rcfile() { print -r -- "{\"returncode\": $2}" > "$1"; }
VERDICT=1; SERVICE_OURS=0; SERVICE_HOME=""; SAFETY_NET=0
finish() {   # verdict final, écrit une seule fois ; une preuve absente ou tronquée vaut échec
  local ok=false; [ "$VERDICT" = 0 ] && [ "$SAFETY_NET" = 0 ] && ok=true
  [ -e "$P/safety_net.json" ] || print -r -- "{\"fired\": $([ "$SAFETY_NET" = 1 ] && echo true || echo false)}" > "$P/safety_net.json"
  [ -e "$P/shell_verdict.json" ] || print -r -- "{\"ok\": $ok}" > "$P/shell_verdict.json"
}
fail() { echo "PROOF=FAIL: $*"; VERDICT=1; exit 1; }
state_fail() { echo "PROOF=FAIL (état launchd/port contraire ou inobservable): $*"; VERDICT=1; exit 40; }

# Filet de sécurité : armé seulement après la création PROUVÉE du service par ce run (SERVICE_OURS=1).
cleanup() {
  local rc=$?
  if [ "$SERVICE_OURS" = 1 ]; then
    # Ré-attestation immédiate : plist sous le HOME jetable du run, programme = venv du run.
    if helper attest-service --home "$SERVICE_HOME" --venv "$VENV" --profile "$PROFILE" --output "$P/safety_net_attest.$EPOCHSECONDS.json"; then
      echo "filet de sécurité : arrêt du service de test créé par ce run ($LABEL)"
      launchctl bootout "gui/$UID_N/$LABEL" || true
      SAFETY_NET=1
    else
      echo "filet de sécurité : service non ré-attesté comme appartenant au run — AUCUN arrêt ; intervention humaine"
    fi
  fi
  [ "$SAFETY_NET" = 1 ] && VERDICT=1   # le filet ne transforme jamais un échec de retour arrière en succès
  finish
  # Rapport de diagnostic même en échec (ok=false) ; son absence vaut de toute façon échec.
  [ -e "$P/report.json" ] || helper assemble --proof-dir "$P" > /dev/null 2>&1 || true
  echo "safety_net_fired=$SAFETY_NET exit=$rc"
  [ "$SAFETY_NET" = 1 ] && [ "$rc" = 0 ] && exit 1
}
trap cleanup EXIT

# Chronologie M1 : lignes « nom début fin » (secondes epoch) ; T0 = lancement de python3.11 -m venv.
span() { local name=$1 t0=$EPOCHREALTIME rc; shift; "$@"; rc=$?; print -r -- "$name $t0 $EPOCHREALTIME" >> "$P/timeline.txt"; return $rc; }
absent() {   # $1 = fichier de sortie ; libellé explicitement reconnu absent ET port libre, sinon arrêt
  helper service-state --expect absent --output "$1"; local rc=$?
  [ $rc -eq 0 ] || state_fail "libellé/port de test : rc=$rc ($1)"
}
tenv() {   # $1 = HOME jetable ; environnement vide, hors ligne sauf pip sous GO
  local home=$1; shift
  env -i HOME="$home" USER="$USER" LOGNAME="$USER" TMPDIR="$P/tmp" LANG=en_US.UTF-8 \
    PATH="$VENV/bin:/usr/bin:/bin:/usr/sbin:/sbin" PIP_CONFIG_FILE=/dev/null PIP_NO_INPUT=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_CACHE_DIR="$P/pip-cache" PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 MEMORY_V4_URL="http://127.0.0.1:$PORT/mcp/" \
    MEMORY_V4_SERVICE_LABEL="$LABEL" "$@"
}
seed_table() { mkdir -p "$1/.config/memory-os" && chmod 700 "$1/.config" "$1/.config/memory-os" \
  && cp "$SEED" "$1/.config/memory-os/cl100k_base.tiktoken" && chmod 600 "$1/.config/memory-os/cl100k_base.tiktoken"; }
seed_model() {   # $1 = HOME, $2 = dossier de preuve ; copie du cache local vérifié, contrôlée fichier par fichier
  mkdir -p "$1/.cache/huggingface/hub" && cp -R "$GRANITE" "$1/.cache/huggingface/hub/$GRANITE_DIR" \
    && helper verify-granite --cache "$1/.cache/huggingface/hub/$GRANITE_DIR" --catalog "$CATALOG" --output "$2/granite.json"
}

step "0. Préconditions (avant T0)"
absent "$D1/pre_state.json"
helper production-snapshot --real-home "$REAL_HOME" --output "$P/production_before.json" || state_fail "production inobservable"
helper meta --profile "$PROFILE" --variant "$VARIANT" --source-commit "$COMMIT" --run-tag "$RUN_TAG" --version "$VERSION" \
  --wheel "$WHEEL" --wheel-sha256 "$WHEEL_SHA" --venv "$VENV" --output "$P/meta.json" || fail "méta"
mkdir -p "$H1" "$H2" && chmod 700 "$H1" "$H2"
print -r -- "{\"home\": \"$H1\"}" > "$D1/home.json"; print -r -- "{\"home\": \"$H2\"}" > "$D2/home.json"
# Préparations réalisables en amont, effectuées avant T0 (hors M1).
if [ "$PROFILE" = lexical ]; then
  seed_table "$H2" || fail "table cl100k (installation 2)"   # installation 1 : N6, aucune pré-copie
else
  seed_table "$H1" && seed_table "$H2" || fail "table cl100k"
  seed_model "$H1" "$D1" && seed_model "$H2" "$D2" || fail "modèle pré-placé non conforme à provision_v1.json"
fi

step "1. venv vierge (T0)"
span venv tenv "$H1" "$PY" -m venv "$VENV" || fail "venv"
span control_blank tenv "$H1" "$VENV/bin/python" -m pip freeze > "$P/freeze_blank.txt" || fail "pip freeze"
[ ! -s "$P/freeze_blank.txt" ] || fail "venv non vierge"

step "2. Installation sous contraintes ($PROFILE, $VARIANT) : roues seulement"
span pip_nodeps tenv "$H1" "$VENV/bin/python" -m pip install --no-deps "$WHEEL" > "$P/install_nodeps.log" || fail "pip --no-deps"
C="$(tenv "$H1" "$VENV/bin/memory-os-constraints" "$PROFILE")" || fail "localisateur de contraintes"
echo "contraintes: $C"
case "$C" in "$VENV/lib/"*/site-packages/*) ;; *) fail "contraintes hors site-packages";; esac
if [ "$VARIANT" = autonomous ]; then
  span pip_deps tenv "$H1" "$VENV/bin/python" -m pip install --only-binary=:all: --index-url https://pypi.org/simple \
    --report "$P/pip_install_report.json" -c "$C" "${WHEEL}${EXTRAS}" > "$P/install.log" || fail "pip -c (réseau sous GO)"
else
  # Variante .pth (décision de Romain) : dépendances réutilisées, aucune acquisition ; libellée comme telle dans M1.
  SITE="$(print -r -- "$VENV"/lib/python*/site-packages(N[1]))"; [ -d "$SITE" ] || fail "site-packages"
  span control_pth "$PTH_PY" -I -c 'import sys; from pathlib import Path; print("\n".join(s for s in sys.path if s and Path(s).name == "site-packages"))' > "$SITE/qualified-dependencies.pth" || fail ".pth"
fi
span control_pip_check tenv "$H1" "$VENV/bin/python" -m pip check > "$P/pip_check.txt"; rcfile "$P/pip_check.json" $?
span control_freeze tenv "$H1" "$VENV/bin/python" -m pip freeze --exclude-editable > "$P/freeze.txt" || fail "pip freeze"
span control_constraints helper constraints-check --constraints "$C" --freeze "$P/freeze.txt" --profile "$PROFILE" \
  --variant "$VARIANT" --output "$P/constraints_check.json" || fail "versions ≠ verrous, paquet hors liste ou paquet ML"
span control_locations tenv "$H1" "$VENV/bin/python" -I - "$PROFILE" "$VENV" > "$P/locations.json" <<'EOF' || fail "localisation du paquet installé"
import importlib.metadata as md, json, sys
from pathlib import Path
import memory_os_constraints, memory_os_v4
profile, venv = sys.argv[1], Path(sys.argv[2]).resolve()
package = Path(memory_os_v4.__file__).resolve()
locator = Path(memory_os_constraints.path(profile)).resolve()
inside = lambda p: "site-packages" in p.parts and venv in p.parents
direct = json.loads(md.distribution("memory-os-ai").read_text("direct_url.json"))
pth = next(iter(venv.glob("lib/python*/site-packages/qualified-dependencies.pth")), None)
print(json.dumps({
    "package_path": str(package), "package_under_site_packages": inside(package),
    "locator_under_site_packages": inside(locator), "version": memory_os_v4.__version__,
    "candidate_origin_sha256": direct.get("archive_info", {}).get("hashes", {}).get("sha256"),
    "reused_paths": [{"path": line, "distributions": sorted(
        d.metadata["Name"] + "==" + d.version for d in md.distributions(path=[line]))}
        for line in (pth.read_text().split("\n") if pth else []) if line],
}))
EOF
helper tree-bytes --root "$P/pip-cache" --output "$P/pip_cache_bytes.json" || fail "octets pip"

step "3. Installation 1 : aperçu sans écriture (inventaire avec SHA-256 de contenu)"
CLIENTS="codex,cursor"
helper inventory --root "$H1" --output "$D1/inv_preview_before.json" || fail "inventaire"
t0=$EPOCHREALTIME
tenv "$H1" "$VENV/bin/memory-os-admin" quickstart --profile "$PROFILE" --clients "$CLIENTS" > "$D1/preview.json"; rcfile "$D1/preview_rc.json" $?
helper inventory --root "$H1" --output "$D1/inv_preview_after.json" || fail "inventaire"
print -r -- "control_preview $t0 $EPOCHREALTIME" >> "$P/timeline.txt"
helper same-inventory "$D1/inv_preview_before.json" "$D1/inv_preview_after.json" --output "$D1/preview_inventory.json" || fail "l'aperçu a écrit dans HOME"
helper client-state --home "$H1" --output "$D1/clients_before_apply.json" || fail "état client"

install_apply() {   # $1 = HOME, $2 = dossier de preuve, $3… = options supplémentaires ; attestation puis armement
  local home=$1 d=$2 rc; shift 2
  tenv "$home" "$VENV/bin/memory-os-admin" quickstart --profile "$PROFILE" --clients "$CLIENTS" "$@" --apply --demo > "$d/apply.json"; rc=$?
  return $rc
}
after_apply() {   # $1 = HOME, $2 = dossier, $3 = rc de l'application
  local home=$1 d=$2 rc=$3 stamp journal
  rcfile "$d/apply_rc.json" "$rc"
  # G7 : service présent, attesté comme appartenant au run (plist du HOME concerné, interpréteur du venv, profil attendu).
  helper attest-service --home "$home" --venv "$VENV" --profile "$PROFILE" --output "$d/service.json"; local arc=$?
  if [ $arc -eq 0 ]; then SERVICE_OURS=1; SERVICE_HOME="$home"; fi   # création prouvée : le filet est armé ici, pas avant
  [ $arc -ne 3 ] || state_fail "service de test inobservable après application"
  [ "$rc" -eq 0 ] || fail "quickstart --apply --demo en échec (voir $d/apply.json)"
  [ $arc -eq 0 ] || state_fail "service de test absent ou non attesté après une application réussie"
  stamp="$("$SYS_PY" -I -c 'import json,sys;print(json.load(open(sys.argv[1])).get("stamp") or "")' "$d/apply.json")"
  [ -n "$stamp" ] || fail "pas de stamp"
  journal="$home/.local/state/memory-os/quickstart/$stamp/install.json"
  [ -f "$journal" ] && cp "$journal" "$d/install.json" || fail "install.json absent du journal"
  tenv "$home" MEMORY_V4_DIR="$home/.local/share/memory-os/store" "$VENV/bin/memory-os-admin" doctor --json > "$d/doctor.json"; rcfile "$d/doctor_rc.json" $?
  helper client-state --home "$home" --output "$d/clients_after_apply.json" || fail "état client"
  print -r -- "$stamp" > "$d/stamp.txt"
}

step "4. Installation 1 : --apply --demo (T1 au retour)"
if [ "$PROFILE" = lexical ]; then   # N6 : exercée UNE fois, ici
  span apply install_apply "$H1" "$D1" --download-approved; after_apply "$H1" "$D1" $?
else
  span apply install_apply "$H1" "$D1"; after_apply "$H1" "$D1" $?
fi

step "5. Installation 1 : retour arrière (--undo STAMP --with-service)"
tenv "$H1" "$VENV/bin/memory-os-admin" quickstart --undo "$(cat "$D1/stamp.txt")" --with-service > "$D1/undo.json"; rcfile "$D1/undo_rc.json" $?
sleep 2
absent "$D1/undo_state.json"; SERVICE_OURS=0   # libellé reconnu absent, port libre : le filet est désarmé
helper client-state --home "$H1" --output "$D1/clients_after_undo.json" || fail "état client"
[ ! -e "$H1/Library/LaunchAgents/$LABEL.plist" ] || fail "plist de test encore en place après undo"
absent "$D1/post_state.json"

step "6. Installation 2 (second HOME jetable, même venv) : --apply --demo"
absent "$D2/pre_state.json"
install_apply "$H2" "$D2"; after_apply "$H2" "$D2" $?

step "7. Installation 2 : désinstallation (aperçu sans écriture, puis --apply)"
helper inventory --root "$H2" --output "$D2/data_before.json" || fail "inventaire"
tenv "$H2" "$VENV/bin/python" -I "$HELPER" store-logical --store "$H2/.local/share/memory-os/store" --output "$D2/store_before.json" || fail "store (avant)"
helper inventory --root "$H2" --output "$D2/inv_uninstall_before.json" || fail "inventaire"
tenv "$H2" "$VENV/bin/memory-os-admin" uninstall > "$D2/uninstall_preview.json"; rcfile "$D2/uninstall_preview_rc.json" $?
helper inventory --root "$H2" --output "$D2/inv_uninstall_after.json" || fail "inventaire"
helper same-inventory "$D2/inv_uninstall_before.json" "$D2/inv_uninstall_after.json" --output "$D2/uninstall_preview_inventory.json" || fail "l'aperçu de désinstallation a écrit dans HOME"
tenv "$H2" "$VENV/bin/memory-os-admin" uninstall --apply > "$D2/uninstall.json"; rcfile "$D2/uninstall_rc.json" $?
sleep 2
absent "$D2/uninstall_state.json"; SERVICE_OURS=0
# Conservation des données, vérifiée après arrêt : identité et contenu logique du store, fichiers immuables, journaux.
helper inventory --root "$H2" --output "$D2/data_after.json" || fail "inventaire"
tenv "$H2" "$VENV/bin/python" -I "$HELPER" store-logical --store "$H2/.local/share/memory-os/store" --output "$D2/store_after.json" || fail "store (après)"
helper client-state --home "$H2" --output "$D2/clients_after_uninstall.json" || fail "état client"
absent "$D2/post_state.json"

step "8. Production et vraies configurations inchangées ; rapport"
helper production-snapshot --real-home "$REAL_HOME" --output "$P/production_after.json" || state_fail "production inobservable"
helper timeline --input "$P/timeline.txt" --output "$P/timeline.json" || fail "chronologie M1"
VERDICT=0; finish
helper assemble --proof-dir "$P" || { VERDICT=1; echo "PROOF=FAIL: rapport refusé (voir report.json)"; exit 1; }
(cd "$P" && find . -type f \( -name '*.json' -o -name '*.log' -o -name '*.txt' \) -not -path './venv/*' -not -path './home*' \
  -not -path './pip-cache/*' -not -path './tmp/*' -not -name SHA256SUMS -print0 | sort -z | xargs -0 shasum -a 256 > SHA256SUMS)
df -h / | tail -1
echo "PROOF=PASS"
