# Qualification 4.0.7 — candidate de version finale, chaîne complète v2

14 octobre 2026. **Candidate uniquement, chaîne finale 4.0.7 non publiée.
Production maintenue en 4.0.5.** Accord sur la proposition Claude : aucun
amendement « version + doctor.py ». Le doctor actuel refuse rc/dev par son
regex de version ; correctif prévu en 4.0.8. La RC1 reste refusée et ses
preuves conservées. Reprendre toute la chaîne, ne pas réutiliser ses empreintes.
Aucune commande ci-dessous ne publie ni ne modifie le service, son plist ou son
magasin. Claude exécute hors sandbox, séquentiellement, dans des dossiers neufs.
Arrêter au premier refus ; garder toutes les preuves, ne jamais écraser un run.

## 1. Périmètre et critères gelés avant exécution

Sous `v4/src`, la différence avec f9d3668 doit être exactement la chaîne de
version de `memory_os_v4/__init__.py`. Le paquet historique et le pyproject
standalone ont aussi leur version alignée, hors de ce périmètre. Aucun correctif
fonctionnel moteur, profil d'encodage ou paramètre P-A/P-B ajouté à la RC.

| Preuve | Critère |
|---|---|
| Source | v3/v4 complètes, aucune erreur/skip ; **31 identités TCP** exactes ; configurations client inchangées ; deux lints |
| Artefacts | wheel direct + sdist, wheel reconstruit du sdist ; installation/smokes hors dépôt ; descriptor/payload/commit vérifiés ; twine strict |
| Profil | runtime épinglé, Granite révision inchangée, auto→MPS/native/batch32/max512 ; C4 scoped_literal/guard3 ; registre inactif, SQLite/tasks actifs |
| Coupe | API backup de 4.0.5, source SQLite ouverte en lecture seule ; schéma7, instance attendue ; aucun moteur sur le magasin live |
| Migration | inventaire **exhaustif** lignes/objets SQL 7→8 ; seule migration8 et son journal initial vide ajoutés ; reçus, versions, blocs, tâches, identité et cache préservés |
| Journal | coverage_start dans la fenêtre de migration, génération stable au reopen ; historique antérieur accessible mais aucun événement rétrospectif inventé |
| Cache | **0 miss et 0 texte du corpus encodé**, premier démarrage et reopen, les deux artefacts ; profils strictement identiques à 4.0.5 |
| Recherche | 10 requêtes préexistantes, un instant figé partagé, scope moteur `None`, top10/rerank=false ; **≥1 hit par requête par worker**, comptes recomptés ; IDs ordonnés identiques, durées séparées |
| Coût | migration ≤120 s, startup par worker ≤120 s, campagne copie ≤900 s ; aucun dépassement accepté automatiquement |
| HTTP privé | --warm, port aléatoire loopback/token privé0600 ; ready version/commit/descriptor ; read_only/SQLite/tasks/C4/tokenizer ; doctor sans FAIL |
| S4 installé | backup schéma8 qualifié et vérifié, identité source conservée ; drill privé vérifié, nouvelle identité de restauration, oublis réconciliés, réouverture privée réussie |
| Relais | wheel ET sdist installés : round-trip/panne/reconnexion sans rejeu, statut local pendant panne, version de handshake exacte |
| Gate B RC | inputs/gold/scorer/seuils inchangés, binding nouveau commit ; 160 lignes, 40/40 et six gates, rejeu true ; moteur = version seule |

Seuil disque effectif : 5 Go par défaut, dérogation opérateur approuvée
`MEMORY_V4_MIN_FREE_BYTES=3000000000`. Swap ≤8,5 Go ; si non mesurable,
qualification lourde refusée. Nice15, concurrence1 ; aucun téléchargement de
modèle, aucun processus tiers touché. **S4 conserve sa réserve produit de 5 Go
plus l’espace de backup/drill** : l’override 3 Go du harnais ne la réduit pas.
Prévoir cette marge avant la campagne copie, sinon elle sera refusée.
Les recherches neurales sont uniquement
exécutées par Claude sur les copies, avec les poids déjà disponibles.

## 2. Préparation commune (Claude)

Importer la ref nommée `release/4.0.7` et synchroniser l'arbre en préservant les
configs privées. La base de ce bundle est le commit RC1 `80f56fd` déjà importé.
La ref livrée reste `release/4.0.7`.

```sh
cd "$HOME/Documents/Codex/2026-10-09/v/work/implementation"
RC_PY="$HOME/memory-os-local/qual-envs/release406-installed-2zfywl93/wheel/bin/python"
RC_BUILD_PY="/opt/homebrew/bin/python3.11"
RC_COMMIT=$(git rev-parse HEAD)
export PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:v4/src:v4:scripts
export MEMORY_V4_MIN_FREE_BYTES=3000000000
export MEMORY_V4_CL100K_PATH="$HOME/.config/memory-os/cl100k_base.tiktoken"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
unset MEMORY_V4_PROJECT_REGISTRY MEMORY_V4_EMBED_MODEL

git diff f9d366813aaabb9e0c9111257938bc9a008f31e2 "$RC_COMMIT" -- v4/src
nice -n 15 "$RC_PY" -B scripts/qualify_405_test_isolation.py --suite full \
  --output .local-qualification/407-full-claude-v2
/opt/homebrew/bin/ruff check --no-cache --config pyproject.toml src tests scripts
/opt/homebrew/bin/ruff check --no-cache --config pyproject.toml src tests v4/src v4/tests v4/bench scripts --select E9,F
```

Le premier harnais reprend toutes les suites isolées hash, les 31 TCP et leurs
identités. Les deux lints sont exigés et leurs sorties à conserver. La RC ne
s'appuie pas sur des tests TCP ignorés ou sur un simple nombre de cas.

## 3. Construction et manifeste des vrais artefacts

Le harnais D1 construit avec le backend PEP517 réel, directement puis via sdist,
installe les deux wheels hors source et contrôle les marqueurs, commandes et
preview quickstart sans écriture. Il réutilise les dépendances qualifiées ; cela
n'est pas une nouvelle résolution réseau ou un essai clean-install 4.0.7.

```sh
nice -n 15 "$RC_PY" -B scripts/qualify_distribution_d1a.py --build \
  --source-commit "$RC_COMMIT" --build-python "$RC_BUILD_PY" \
  --output .local-qualification/407-artifacts-claude-v2

"$RC_PY" -B scripts/make_release_407.py \
  --source-commit "$RC_COMMIT" \
  --artifact-proof .local-qualification/407-artifacts-claude-v2/report.json \
  --full-proof .local-qualification/407-full-claude-v2/report.json \
  --output .local-qualification/release-4.0.7-v2
RC_MANIFEST="$PWD/.local-qualification/release-4.0.7-v2/manifest.json"

nice -n 15 "$RC_PY" -B scripts/source_release_checks.py \
  --manifest "$RC_MANIFEST" \
  --output .local-qualification/release-4.0.7-v2/source-proof-input.json
"$RC_PY" -B scripts/verify_release_manifest.py "$RC_MANIFEST" \
  --record-source-proof .local-qualification/release-4.0.7-v2/source-proof-input.json
```

`make_release_407` copie **les octets qualifiés** du wheel direct et du sdist,
calcule SHA256/tailles, scelle les scripts et preuves, puis exécute twine strict.
Les empreintes ne sont jamais prévues ou fabriquées avant la construction.
`release_ready=false` reste obligatoire à ce stade. `source_release_checks`
rejoue les 31 TCP, le test sémantique avec MiniLM déjà caché, puis toute v3 cached ;
aucun download autorisé. Un cache absent donne un refus, jamais un skip accepté.

## 4. Environnements installés et copie de production (Claude seulement)

```sh
RC_ENVS="$HOME/memory-os-local/qual-envs/407-installed-v2"
nice -n 15 "$RC_PY" -B scripts/prepare_release_407_envs.py \
  --manifest "$RC_MANIFEST" --build-python "$RC_BUILD_PY" --output "$RC_ENVS"

nice -n 15 "$RC_PY" -B scripts/qualify_sqlite_release_407.py \
  --manifest "$RC_MANIFEST" --environments "$RC_ENVS/environments.json" \
  --baseline-python "$HOME/.memory-os-runtime/venv-4.0.5/bin/python" \
  --source-dir "$HOME/memory-os-local/stores/sqlite-20261011-v1" \
  --production-plist "$HOME/Library/LaunchAgents/com.romain.memory-os-v4.plist" \
  --expected-instance-id 286dfcb0357940468aedb9617a2ccd9c \
  --output-root .local-qualification/407-copy-claude-v2

"$RC_PY" -B scripts/verify_release_manifest.py "$RC_MANIFEST" \
  --record-copy-proof .local-qualification/407-copy-claude-v2/report.json
```

Les environnements sont des venvs neufs **hors dépôt**, alimentés hors réseau
par les dépendances existantes et le build-tool wheel déjà installé. Le sdist
est réellement installé par pip ; il n'est pas remplacé par le wheel direct.
Descriptor/source/runtime sont vérifiés indépendamment dans chacun. La coupe
transactionnelle live est copiée intégralement, puis cinq workers tournent :
baseline7, wheel8, sdist8 et leurs deux reopens. HTTP/doctor et backups S4/drills
utilisent ces copies8, dossiers ops/clé/token privés et ports éphémères.

La source peut recevoir des écritures concurrentes ; **la coupe scellée** est
l'autorité comparée. Ne pas comparer le SHA physique live d'un WAL actif. Les
preuves ne contiennent pas le texte de la mémoire mais restent privées.

## 5. Recollecte P-B RC et report conditionnel du gate

Claude conserve le gold. Aucun changement d'inputs, de règles ou de seuils n'est
autorisé ici. Le binding du RC est créé dans un nouveau fichier **avant**
collection. Le wrapper de scoring vérifie l'empreinte exacte de v3.9 et du helper,
les copie dans un temporaire privé, change uniquement `CANDIDATE`/`CLONE` en
mémoire et appelle le scorer d'origine. Les fichiers de Claude restent intacts.

```sh
PB_DIR="$HOME/memory-os-local/brief_v5_val/pb"
PB_SEAL="$PB_DIR/sealed_pb_v1_4"
PB_INPUT_SHA=70ab2c61172f5401ea12b68a2b3566e593c25689a42e8f9b2eac21d2a4d05de0
RC_BIND="$PWD/.local-qualification/407-pb-binding-v2.json"
"$RC_PY" -B - "$PB_SEAL/manifest_v1_4.json" "$RC_COMMIT" "$PB_INPUT_SHA" "$RC_BIND" <<'PY'
import datetime,hashlib,json,sys
from pathlib import Path
m,c,h,out=sys.argv[1:]
p=Path(out)
b=dict(candidate_commit=c,inputs_sha256=h,manifest_sha256=hashlib.sha256(Path(m).read_bytes()).hexdigest(),bound_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),bound_by='Claude')
with p.open('x') as f: json.dump(b,f,indent=2)
PY

nice -n 15 "$RC_PY" -B scripts/run_integrity_v5_validation.py --run \
  --inputs "$PB_SEAL/inputs_v1_4.jsonl" --manifest "$PB_SEAL/manifest_v1_4.json" \
  --binding "$RC_BIND" --inputs-sha256 "$PB_INPUT_SHA" --candidate-commit "$RC_COMMIT" \
  --table "$MEMORY_V4_CL100K_PATH" --output .local-qualification/407-pb-collect-claude-v2

nice -n 15 "$RC_PY" -B scripts/score_release_407_gate.py \
  --manifest "$RC_MANIFEST" --scorer "$PB_DIR/score_pb_v3_9.py" \
  --cases "$PB_SEAL/cases_pb_v1_4.jsonl" \
  --collection .local-qualification/407-pb-collect-claude-v2 \
  --output .local-qualification/407-pb-score-claude-v2.json

"$RC_PY" -B scripts/record_release_407_gate.py \
  --collection .local-qualification/407-pb-collect-claude-v2/report.json \
  --scorer .local-qualification/407-pb-score-claude-v2.json \
  --output .local-qualification/407-pb-proof-claude-v2.json
"$RC_PY" -B scripts/verify_release_manifest.py "$RC_MANIFEST" \
  --record-gate-proof .local-qualification/407-pb-proof-claude-v2.json
```

Reporter toutes les cinq limites B : MAC exact non vérifié, oracle candidat
pour les défauts non sécuritaires, classement oubli/requête non indépendant,
périmètre synthétique non exhaustif et authenticité locale conditionnelle non
signée. Le report A reste conditionné au diff version-seule ; les gates ne sont
ni une étude d'usage réel, ni une supériorité concurrentielle.

## 6. Relais installés et vérification finale

```sh
nice -n 15 "$RC_PY" -B scripts/installed_relay_e2e.py \
  --manifest "$RC_MANIFEST" --environments "$RC_ENVS/environments.json" \
  --output .local-qualification/407-installed-relays-claude-v2.json
"$RC_PY" -B scripts/verify_release_manifest.py "$RC_MANIFEST" \
  --record-relay-proof .local-qualification/407-installed-relays-claude-v2.json
"$RC_PY" -B scripts/verify_release_manifest.py "$RC_MANIFEST"
```

Seule cette dernière séquence, après toutes les preuves vertes, peut rendre cette
**candidate à version finale** release_ready. Aucun droit de publication ou
bascule en découle : Romain décide après revue. Ne jamais renommer un wheel RC
ni lui substituer les artefacts RC1. Les boutons ciblent 4.0.7, inutilisables
avant sa publication.

## 7. Hook et bascule future

Le hook v5 candidat est indépendant de la version moteur ; voir la revue
`REVIEW_HOOK_V5_CODEX_v1.md` dans codex_runs. Ses tests de pagination simulée
ne qualifient pas le comportement du hook installé dans un vrai client.
Ne pas l'activer sur la seule qualification des artefacts serveur.

Pour une future stable : suivre `SWITCH_4_0_7_SQLITE.md`, nouvelle coupe finale
sous read_only, sauvegarde7 vérifiée, migration sur **nouveau répertoire**, puis
premier backup8/drill privé avant writes et GO distinct de Romain. **Ne jamais
ouvrir schéma8 avec 4.0.5.** Avant nouvelles écritures, abandonner la copie8 et
revenir à la coupe7 ; après écritures8, rollback vers ancien backup7 perdrait des
écritures : correctif en avant/restauration8 + réconciliation, pas de downgrade
non qualifié ni d'export S3 présenté comme un retour SQLite7 sans perte.
