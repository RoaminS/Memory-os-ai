# Correctif externe du harnais S4 — reprise copie v4, artefacts inchangés

15 octobre 2026. Patch Claude PATCH_S4_WRITER_v1 accepté tel quel. S4 est en
ligne : garder SQLiteStore ouvert pendant backup ET drill sur la **copie**.
Ne pas créer/réparer WAL/SHM depuis le lecteur de sauvegarde. Aucun v4/src modifié.

## Provenance et preuves

**Le source_commit des artefacts reste9e41ea40141f8ed4d7ed12a92a1900d2409272c3.**
Le nouveau commit livré est celui du harnais externe, pas un nouveau build.
Conserver exactement wheel/sdist, leurs SHA, descripteurs et environnements de
release-4.0.7-v2. Ne pas reconstruire ni annoncer que ces artefacts proviennent
du nouveau commit. Leur sdist embarque encore l'ancien script de qualification ;
la correction s'applique au harnais externe utilisé pour les qualifier, pas au
serveur distribué.

| Preuve | Décision |
|---|---|
| Full31TCP, lints, source31TCP+sémantique+794v3 | Restent valides **sur9e41ea4** ; aucune réécriture des rapports/source_sha256 |
| Wheels/sdist, twine, installations, pins/descripteurs | Inchangés, réutilisables ; ne pas modifier source_commit |
| P-A et P-B/recollecte déjà enregistrée gate_rc sur9e41ea4 | Conservés ; code moteur/collecteur inchangé |
| Copie v3 refusée, cinq workers antérieurs | Garder comme diagnostic ; ne pas convertir en succès |
| Harnais corrigé | Deux régressions légères nouvelles, lint ; amendement explicite des seules empreintes du runner |
| Copie v4 | Refaire campagne complète : cinq workers, doctor, S4 wheel+sdist/drills, sans réutiliser ses anciens workers |
| Relais installés et vérification finale | À exécuter puis enregistrer, comme prévu |

Le manifeste ne peut pas rester **byte-identique** : ses empreintes d'outil
refuseraient le nouveau runner. L'amendement ci-dessous conserve une copie de
l'original, la source des artefacts et toutes les anciennes preuves, enregistre
le commit du harnais et sa régression, ne lève pas release_ready. La carte
qualification_source_sha256 reste celle de9e41ea4 (preuve historique), distincte
des outils utilisés désormais. Ne pas relancer source_release_checks sur l'arbre
amendé en prétendant tester9e41ea4 : un éventuel rejeu doit partir de ce commit.
Si l'on décide de reconstruire avec le nouveau commit, il faut au contraire
nouveaux artefacts/empreintes et toute la qualification ; ce n'est pas la voie
retenue pour cette reprise minimale.

## Claude : tests ciblés et amendement du manifeste

Après import de la ref release/4.0.7, depuis le clone principal. Aucune suite
complète ni nouvelle copie exécutée par Codex. Nouveaux chemins, arrêt au premier
échec. Seuil3 Go/swap8,5 Go du harnais inchangé ; S4 conserve son propre seuil5 Go
plus espace de travail. Aucun téléchargement ou action sur le service live.

```sh
RC_PY="$HOME/memory-os-local/qual-envs/release406-installed-2zfywl93/wheel/bin/python"
export PYTHONPATH=src:v4/src:v4:scripts PYTHONDONTWRITEBYTECODE=1
export MEMORY_V4_MIN_FREE_BYTES=3000000000
export MEMORY_V4_CL100K_PATH="$HOME/.config/memory-os/cl100k_base.tiktoken"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
unset MEMORY_V4_EMBED_MODEL MEMORY_V4_PROJECT_REGISTRY
RC_MANIFEST="$PWD/.local-qualification/release-4.0.7-v2/manifest.json"

"$RC_PY" -B -m pytest -c pyproject.toml tests/test_release_407_s4_writer.py \
  -q -p no:cacheprovider --basetemp=.local-qualification/407-s4-writer-claude-v1 \
  --junitxml=.local-qualification/407-s4-writer-claude-v1.xml
/opt/homebrew/bin/ruff check --no-cache --config pyproject.toml src tests scripts
/opt/homebrew/bin/ruff check --no-cache --config pyproject.toml src tests v4/src v4/tests v4/bench scripts --select E9,F
```

Les tests utilisent le **vrai snippet du harnais**, un magasin Hash jetable,
SQLite/archives/crypto/réconciliation réels ; seuls la mesureOS et le probeHTTP
privé sont simulés. Deux cas : refus sans écrivain, succès avec écrivain. Ce
n'est pas une qualification réseau : le drill réel reste obligatoire en copie v4.

Amendement opérateur, original préservé ; refus si la source/les outils ou les
preuves ne correspondent pas, ou si un succès copie/relais était déjà enregistré :

```sh
"$RC_PY" -B - "$RC_MANIFEST" .local-qualification/407-s4-writer-claude-v1.xml <<'PY'
from pathlib import Path
import datetime, hashlib, json, os, subprocess, sys, xml.etree.ElementTree as ET
from release_407_checks import validate_local
sha=lambda b:hashlib.sha256(b).hexdigest()
base='9e41ea40141f8ed4d7ed12a92a1900d2409272c3'
p=Path(sys.argv[1]); proof=Path(sys.argv[2]); before=p.read_bytes(); m=json.loads(before)
assert m['source_commit']==base and m['version']=='4.0.7' and m['release_ready'] is False
assert 'copy' not in m['evidence'] and not m.get('relay_proof_sha256')
commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
allowed={'scripts/qualify_sqlite_release_407.py','tests/test_release_407_s4_writer.py','v4/docs/QUALIFY_4_0_7_S4_WRITER.md'}
changed=set(subprocess.check_output(['git','diff','--name-only',base,commit],text=True).splitlines())
assert changed==allowed, changed
name='qualify_sqlite_release_407.py'
old=sha(subprocess.check_output(['git','show',base+':scripts/'+name]))
new=sha(Path('scripts',name).read_bytes())
assert old != new
assert m['qualification_tooling_sha256'][name]==old and m['copy_scripts_sha256']['runner']==old
assert m['qualification_source_sha256']['scripts/'+name]==old
nodes=list(ET.fromstring(proof.read_bytes()).iter('testsuite'))
assert sum(int(n.get('tests','0')) for n in nodes)==2
assert all(sum(int(n.get(k,'0')) for n in nodes)==0 for k in ('errors','failures','skipped'))
backup=p.with_name('manifest.pre-s4-writer-'+sha(before)[:12]+'.json')
assert not backup.exists()
target=p.parent/'evidence'/'s4-writer-unit.xml'
assert not target.exists()
m['qualification_tooling_sha256'][name]=new
m['copy_scripts_sha256']['runner']=new
m['evidence']['s4_writer_regression']={'path':'evidence/s4-writer-unit.xml','sha256':sha(proof.read_bytes())}
m.setdefault('tooling_amendments',[]).append({'date':datetime.datetime.now(datetime.timezone.utc).isoformat(),'tooling_commit':commit,'artifact_source_commit':base,'file':'scripts/'+name,'old_sha256':old,'new_sha256':new,'previous_manifest_sha256':sha(before),'reason':'Online S4: private copy writer open during backup and drill'})
validate_local(m,p.parent,require_copy=False)
assert p.read_bytes()==before
with backup.open('xb') as f:f.write(before)
with target.open('xb') as f:f.write(proof.read_bytes())
tmp=p.with_name('manifest.s4-writer.tmp')
with tmp.open('x') as f:
    json.dump(m,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
assert p.read_bytes()==before
os.replace(tmp,p)
print('Tooling amendment recorded; artifacts/source proofs unchanged; release_ready=false')
PY
```

Aucune preuve historique modifiée. Garder l'ancien manifeste comme ancêtre et
l'amendement dans le dossier de publication privé.

## Claude : copie v4, relais et final

```sh
nice -n 15 "$RC_PY" -B scripts/qualify_sqlite_release_407.py \
  --manifest "$RC_MANIFEST" \
  --environments "$HOME/memory-os-local/qual-envs/407-installed-v2/environments.json" \
  --baseline-python "$HOME/.memory-os-runtime/venv-4.0.5/bin/python" \
  --source-dir "$HOME/memory-os-local/stores/sqlite-20261011-v1" \
  --production-plist "$HOME/Library/LaunchAgents/com.romain.memory-os-v4.plist" \
  --expected-instance-id 286dfcb0357940468aedb9617a2ccd9c \
  --output-root .local-qualification/407-copy-claude-v4
"$RC_PY" -B scripts/verify_release_manifest.py "$RC_MANIFEST" \
  --record-copy-proof .local-qualification/407-copy-claude-v4/report.json
nice -n 15 "$RC_PY" -B scripts/installed_relay_e2e.py \
  --manifest "$RC_MANIFEST" \
  --environments "$HOME/memory-os-local/qual-envs/407-installed-v2/environments.json" \
  --output .local-qualification/407-installed-relays-claude-v4.json
"$RC_PY" -B scripts/verify_release_manifest.py "$RC_MANIFEST" \
  --record-relay-proof .local-qualification/407-installed-relays-claude-v4.json
"$RC_PY" -B scripts/verify_release_manifest.py "$RC_MANIFEST"
```

La recollecte gate_rc est déjà enregistrée dans le manifeste constaté ; conserver
son binding9e41ea4. Si elle manque dans un autre dossier, suivre QUALIFY_4_0_7.md
en fixant RC_COMMIT=9e41ea40141f8ed4d7ed12a92a1900d2409272c3, jamais le HEAD du
correctif de harnais. Toute autre différence de source est hors de cet amendement.
La levée de garde dépend toujours de toutes les preuves, pas du seul patch.
Aucune publication/bascule sans GO de Romain.
