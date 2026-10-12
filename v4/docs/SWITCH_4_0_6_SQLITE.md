# 4.0.6 — dossier exact et qualification SQLite avant bascule

GO Romain du 2026-10-12 : publication par Romain et bascule par Claude.
Codex prépare uniquement les preuves/outils ; aucune action sur la production.

## Identités et état du dossier

- Artefact/source : `50edb029d57a20dfc339c5a6ec7d1d62c1f2b5a1`.
- Wheel EXACT, jamais reconstruit :
  `2cf484d882c824e98776e3fc5d9e83f5b62f06e6d4d34c3b1216fe089ed0e201`.
- Sdist EXACTE : `5384a1c535f2561a22827a08f3bfaacd25327db275372b6cca15668a5da51c0d`.
- Magasin : `~/memory-os-local/stores/sqlite-20261011-v1`, identité
  `286dfcb0357940468aedb9617a2ccd9c`, schéma 7.
- Dossier : `.local-qualification/release-4.0.6-v1`. Artefacts copiés depuis
  `~/memory-os-local/release-4.0.6`, rapports v7b et essais SQLite/JSONL scellés.
  Rapports réels : **647 v3, 1883 v4, 28 TCP**, pas 644 v3.
- `release_ready=false` tant que copie SQLite et relais installés ne sont pas
  enregistrés. `verify_release_manifest.py` refuse une bascule prématurée.
- Le commit de l'outillage est distinct de celui de l'artefact :
  `qualification_tooling_commit` et `qualification_tooling_sha256`. Aucune
  modification de `v4/src`, aucune reconstruction des artefacts livrés.
  Ces empreintes ne sont pas une signature de confiance.

## Ce qui change pour le magasin activé

Le diff `e7d5abb..50edb02` laisse **memory.py, embed.py, config.py,
sqlite_store.py, sqlite_engine.py, sqlite_embeddings.py et work_state.py identiques**.
Aucune migration de données/schéma et aucun changement d'identité attendu.
`meta.version` peut évoluer ; caches dérivés peuvent se reconstruire sans
changer faits, historique, blocs, tâches ni reçus. Le worker compare toutes
les tables d'autorité et les métadonnées hors version.

`--warm` appelle désormais `ensure_retrieval()` : le service doit charger le
modèle et la recherche SQLite avant de déclarer la disponibilité. Le serveur
4.0.5 pouvait rester froid malgré ce drapeau. `auto` doit résoudre MPS,
native/batch32/max512, C4 scoped_literal/guard3, même namespace `v1`.
Un cache compatible doit éviter tout encodage du corpus ; charger les poids
et encoder les dix requêtes restent nécessaires. Aucun accélérateur/modèle
nouveau, aucun téléchargement ni ré-encodage implicite accepté pour cette bascule.

Le pinning/descriptor renforcent l'identité installée. Une ancienne déclaration
`MEMORY_V4_BUILD_COMMIT=e7d5abb...` contredit le descriptor 4.0.6 : remplacer par
`50edb02...` ou retirer dans **les quatre jobs**. /ready et doctor doivent exposer
`identity_source=embedded_descriptor`. Provision et quickstart ne sont pas
appelés : SQLite/tâches sont déjà actifs ; aucun nouvel installateur ne doit
modifier ce magasin, ses clients, ses ressources ou sa configuration.

## Qualification hors sandbox par Claude

Préserver les preuves anciennes. Un échec arrête la séquence ; conserver le
nouveau dossier et choisir un suffixe frais pour une reprise explicite.
Concurrence 1, nice 15, swap <= 8,5 GB. Le harnais de copie utilise le plancher
opérateur 3 GB ; **les outils S4 de sauvegarde/drill conservent 5 GB + espace
de travail**. La dérogation d'installation ne permet pas de contourner S4.

Créer deux environnements privés hors dépôt, sans réseau et sans reconstruire
le wheel de publication. L'installation de la sdist exerce son backend PEP 517
local ; le wheel de publication reste inchangé. Les dépendances existantes sont
réutilisées, jamais remplacées ; une dérive du ledger arrête avant la copie.

```bash
cd /Users/romain/Documents/Codex/2026-10-09/v/work/implementation
export MEMORY_V4_MIN_FREE_BYTES=3000000000 PYTHONDONTWRITEBYTECODE=1
export MEMORY_V4_CL100K_PATH=/Users/romain/.config/memory-os/cl100k_base.tiktoken
unset MEMORY_V4_PROJECT_REGISTRY
R="$PWD/.local-qualification/release-4.0.6-v1"
P="$PWD/.local-qualification/brief-test-runtime-claude-3c-v1/bin/python"
PYTHONPATH=src:v4/src:v4 "$P" -B - "$R" <<'PY'
import json, os, pathlib, shutil, subprocess, tempfile, venv
from memory_os_v4.ops.provision import guard
r = pathlib.Path(__import__('sys').argv[1]); guard(r)
root = pathlib.Path(tempfile.mkdtemp(prefix='release406-installed-', dir='/private/tmp'))
root.chmod(0o700)
deps = pathlib.Path('/Users/romain/Memory-os-ai/.venv/lib/python3.11/site-packages')
old_site = pathlib.Path('/Users/romain/.memory-os-runtime/venv-4.0.5/lib/python3.11/site-packages')
env = {k: os.environ[k] for k in ('PATH', 'LANG') if k in os.environ}
env.update(HOME=str(root), PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1',
           PIP_CONFIG_FILE=os.devnull, HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
result = {}
for kind, suffix in [('wheel', '.whl'), ('sdist', '.tar.gz')]:
    guard(r)
    target = root / kind; venv.EnvBuilder(with_pip=True).create(target)
    site = target / 'lib/python3.11/site-packages'
    (site / 'zz_shared_deps.pth').write_text(str(deps) + '\n')
    for leaf in ('tiktoken', 'tiktoken_ext', 'tiktoken-0.12.0.dist-info'):
        shutil.copytree(old_site / leaf, site / leaf)
    artifact = next((r / 'artifacts').glob('*' + suffix))
    py = target / 'bin/python'
    subprocess.run([str(py), '-I', '-m', 'pip', '--isolated', 'install', '--no-index',
        '--no-deps', '--no-build-isolation', str(artifact)], env=env, check=True, timeout=180)
    subprocess.run([str(py), '-I', '-m', 'pip', 'check'], env=env, check=True, timeout=30)
    code = '''from memory_os_v4.ops.qualified_runtime import inspect_current, expected_versions
from memory_os_v4.readiness import build_identity
assert len(expected_versions()) == 61
assert len(expected_versions(extras=('ops','brief'))) == 65
r=inspect_current(extras=('ops','brief')); assert r['ok'], r['mismatches']
b=build_identity(); assert b['identity_source']=='embedded_descriptor'
assert b['commit']=='50edb029d57a20dfc339c5a6ec7d1d62c1f2b5a1'
'''
    subprocess.run([str(py), '-I', '-c', code], env=env, check=True, timeout=30)
    result[kind + '_environment'] = str(target)
(r / 'environments.json').write_text(json.dumps(result, indent=2) + '\n')
PY
```

Puis, séquentiellement (aucune action launchd ni mémoire vivante ouverte par un moteur) :

```bash
"$P" -B scripts/qualify_sqlite_release_copy.py \
  --manifest "$R/manifest.json" --environments "$R/environments.json" \
  --baseline-python /Users/romain/.memory-os-runtime/venv-4.0.5/bin/python \
  --source-dir /Users/romain/memory-os-local/stores/sqlite-20261011-v1 \
  --production-plist /Users/romain/Library/LaunchAgents/com.romain.memory-os-v4.plist \
  --output-root .local-qualification/copy-4.0.6-sqlite-v1 &&
"$P" -B scripts/verify_release_manifest.py "$R/manifest.json" \
  --record-copy-proof .local-qualification/copy-4.0.6-sqlite-v1/report.json &&
"$P" -B scripts/installed_relay_e2e.py --manifest "$R/manifest.json" \
  --environments "$R/environments.json" --output "$R/installed-relay-proof.json" &&
"$P" -B scripts/verify_release_manifest.py "$R/manifest.json" \
  --record-relay-proof "$R/installed-relay-proof.json" &&
"$P" -B scripts/verify_release_manifest.py "$R/manifest.json"
```

Le snapshot utilise l'API SQLite backup et un inventaire exhaustif de la coupe
transactionnelle, cap 64 MiB ; ne jamais copier séparément DB/WAL/SHM par `cp`.
La source doit déjà avoir ses WAL/SHM ; aucune réparation sur la source.
Les écritures ordinaires d'autres clients peuvent continuer pendant le snapshot :
la preuve concerne une coupe, pas l'immobilité byte à byte d'un serveur actif.

Critères figés : 5 workers baseline/wheel/sdist/reopen, même identité/schéma,
mêmes tables d'autorité et métadonnées hors version, mêmes 10 listes top10
ordonnées, profils identiques, **0 encodage corpus** dans chaque worker,
startup retrieval <=120 s/cas, campagne <=900 s ; /ready privé `--warm`,
SQLite+tâches+CAS, registre inactif, read_only, C4 inchangé, descriptor valide,
doctor privé 0 FAIL. Les WARN attendus d'absence de jobs/archives/clients privés
ne prouvent pas la santé de la production. Écart = refus, pas d'accord automatique.

## Revue des scripts a8 de Claude (lecture seule)

Scripts lus : `~/memory-os-local/ops/a8_bascule_406.sh`, `a8_verify_406.sh`,
`a8_rollback_406.sh`. La conception wheel exact, quatre jobs, même magasin,
ancien runtime conservé et rollback sans restauration d'archive est correcte.
**Amendements avant exécution :**

1. Le contrôle Git `HEAD == want_commit` confond source de l'artefact et
   outillage ajouté après qualification. Exiger arbre suivi propre, HEAD égal
   à `qualification_tooling_commit` du manifeste, empreintes tooling concordantes ;
   garder `want_commit=50edb02...` pour le descriptor/wheel/ready. Exécuter le
   vérificateur mis à jour, pas celui livré dans l'ancien sdist.
2. Remplacer le regex/compte `n>=66` par le code `expected_versions` /
   `inspect_current(extras=('ops','brief'))` ci-dessus : **61 core, 65 uniques**.
   Les 66 lignes sont un doublon, pas 66 paquets. Employer `-I`, neutraliser
   PYTHONPATH/PYTHONSTARTUP/PYTHONOPTIMIZE et vérifier descriptor+pip check avant
   modification d'un service. Ressources copiées : les trois dossiers tiktoken,
   table cl100k empreinte `223921b7...65b2a7`, compteur hello world=2.
3. Le backup hérite actuellement de nice 0 et peut être refusé par S4 : lancer
   `['/usr/bin/nice','-n','15', *p['ProgramArguments']]`. Neutraliser les variables
   MEMORY_/PYTHONPATH héritées, puis charger l'environnement du plist backup.
   Exiger retour 0, `ok`, `qualified`, `verified`, `backend=='sqlite'`,
   `online==true`, `source_sql_writes==false`, identité INSTANCE ci-dessus,
   empreinte/archive présente, reçu de coupe enregistré. Le plancher S4 reste
   5 GB + espace de travail : sous réserve, **arrêt avant modification service**.
4. Préparer et valider les quatre nouvelles définitions avant publication des
   plists. Conserver store_dir, token/key, archives, namespace, `--warm`,
   auto/native/batch32, C4, `DEDUP_COS=1.01`, tâches/SQLite, registre absent,
   schedules/Nice15 ; changer seulement runtime et déclaration de build.
   Les quatre jobs actuels utilisent bien `python -m ...` : changer argv[0]
   est correct pour CES plists. Vérifier aussi argv[1:] et ne pas généraliser
   cette règle à un plist utilisant directement memory-os-admin.
5. Vérifier l'absence effective de chaque job après bootout (échec après 30 s),
   pas seulement sortir d'une boucle. Arrêter les jobs auxiliaires avant le
   serveur, démarrer/vérifier le serveur avant de recharger les trois jobs.
   Prévoir la commande de rollback sur tout échec après la première modification.
   Une fenêtre read_only courte rend coupe/comptages stables ; sinon les
   comptages avant/après ne peuvent pas prouver zéro perte sous écritures concurrentes.
6. `a8_verify` : appeler doctor dans l'environnement du plist serveur, en
   neutralisant les overrides hérités. Sinon OpsSettings retombe sur l'ancien
   `~/.memory-os-v4` JSONL. Vérifier /ready brut : descriptor, runtime.ok,
   instance, SQLite/tâches/CAS, retrieval+encoder loaded, registre inactif,
   C4, namespace/config conservés et write_mode attendu. Le profil complet
   doit égaler la copie qualifiée, pas seulement `device=mps`.
7. Comparer wheel local/manifeste ET empreinte publiée sur PyPI par Claude/Romain
   avant bascule ; aucun rebuild. Après reprise, handshake relay 4.0.6,
   statut local/journaux conservés et save/search + cycle tâche/CAS sur un
   projet jetable avec preuve de clôture. Le simple total >= avant est un
   signal de cohérence, pas une preuve de conservation de tout l'historique.

Rollback a8 : restaurer quatre définitions et pointeur, stopper/vérifier les
processus, puis redémarrer 4.0.5 et contrôler identité/profil/tâches/ready.
Conserver **le même magasin SQLite et les écritures 4.0.6** ; schéma et moteur
inchangés le permettent. Ne restaurer aucune archive antérieure dans ce rollback.
Une corruption/restore est une procédure séparée avec GO et réconciliation
des oublis ; jamais une suppression des écritures récentes.
