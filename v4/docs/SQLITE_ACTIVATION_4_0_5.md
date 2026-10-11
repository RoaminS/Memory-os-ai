# SQLite + L7b — revue du runbook et procédure 4.0.5

2026-10-11. Outils/procédure seulement, aucune activation effectuée. Runtime
publié 4.0.5/e7d5abb inchangé. Claude qualifie la copie puis soumet la fenêtre,
SQLite, L7b et la politique backup/drill à Romain. Registre toujours inactif,
C4/scoped_literal/guard3 et auto/MPS/native/batch32 conservés.

## 1. Revue adverse de RUNBOOK_SQLITE_ACTIVATION_v1

Accord sur nouvelle autorité, migration normale conservant l'identité, première
sauvegarde et drill avant les écritures, retour préservant les écritures nouvelles.
Amendements nécessaires :

1. **read_only n'est pas offline.** `window_v1.sh on` maintient le moteur/lease
   ouverts. Arrêter complètement le serveur avant la copie finale/migration ;
   une courte interruption des lectures est donc nécessaire. Suspendre aussi les
   anciens jobs backup/health/drill pendant la bascule.
2. S3/SQLite refusent directement `~/.memory-os-v4` et `~/Memory-os-ai` : copier
   la JSONL arrêtée vers un snapshot privé, migrer ce snapshot vers un nouveau
   dossier réel séparé. Pas de symlink de données, d'import en place ou double autorité.
3. **Le premier cache SQLite est vide.** S3 archive les vecteurs JSONL sans les
   adopter dans embedding_cache. Budgéter un premier encodage/cache ; exiger zéro
   encodage corpus au reopen. Ne pas prétendre que le premier démarrage encode zéro.
4. **L3 : convertisseur natif 4.0.5 incomplet.** Les reçus complets importés
   gardent raw_args_sha256/store_instance_id dans legacy_record mais le handler
   lit les champs de premier niveau : status devient legacy_unattested. Utiliser
   l'adaptateur livré, à relire/qualifier : sous-classe locale du Store du seul
   convertisseur, champs préservés au premier INSERT dans la transaction S3.
   Aucun UPDATE d'un reçu immuable, trigger désactivé ou fichier runtime modifié.
   Original bytes, résultats/canonical fingerprints, histoire et UUID conservés.
5. Le compteur SQLite n'est pas nécessairement le compteur JSONL : S3 traduit les
   derniers reçus et ajoute le reçu de migration. Publier les deux inventaires et
   ce contrôle supplémentaire ; comparer les résultats des reçus, pas deux nombres
   présentés comme un même cut. Un ancien pending reste pending, jamais rejoué.
6. **Rollback S3 --rollback change l'UUID** et conserve tasks/CAS en sidecars
   inactifs sous JSONL. Ne jamais rouvrir les anciens fichiers JSONL après de
   nouvelles écritures SQLite. Les clients doivent traiter identity_changed et
   réconcilier les appels incertains ; aucune répétition automatique autorisée.
7. `window_v1.sh` doit refuser un timeout avec ready=false même si le mode est bon.
   Contrôler aussi version/commit/backend/UUID. Les écritures déjà envoyées avant
   un restart restent potentiellement incertaines ; not_started ne concerne que
   l'appel explicitement refusé avant mutation. Propriétaire Claude : corriger ce
   contrôle dans le script local, puis rejouer son test avant activation.

## 2. Dry run : une campagne, séquentielle, copies uniquement

Les dix sondes sont celles de qualification release, figées dans le plan avant
encodage, top10, project='*', at_time commun, rerank=false, accès/recence de lecture
désactivés **uniquement sur les copies de diagnostic**. Toutes les configurations
MCP et tous les plists réels restent inchangés. Pas d'API ou téléchargement.

Critères figés : snapshot cohérent/hashé ; comparaison S3 exhaustive de tous les
records/provenances/champs inconnus/blocks/reçus/originaux ; heads/count/UUID et
profil production identiques ; **ordre exact** des dix sondes JSONL/SQLite cold/
warm ; zéro texte corpus encodé warm ; premier backup vérifié puis drill privé
réconcilié, nouvel UUID privé et zéro réencodage ; correction, ajout, forget,
block, reçu acquitté et task exercés dans une autre copie, export S3/reopen
conservant tout. Migration≤120s, campagne≤900s (budget opératoire, pas un benchmark).
Temps cold/warm, misses/hits, recherche et migration enregistrés séparément.

nice15, une campagne à la fois, disque≥5GB **plus dix empreintes source** pour les
copies conservées/cache/drill ; gardes S4 propres en plus ; swap≤8,5GB, arrêt si
non attestable. Le worker et son serveur de drill sont les seuls processus lourds,
aucune autre campagne en parallèle. Refus si >128MiB source S3 ou >64MiB DB S4.
Tout écart de classement bloque : pas d'explication/acceptation automatique.
Ne pas écrire pendant la collecte ; si la source évolue pendant la campagne,
le rapport refuse et conserve les preuves. Pas de restart automatique.

Depuis le clone après la fin du tour Codex :

```sh
export PYTHONDONTWRITEBYTECODE=1
SQLITE_PY=/Users/romain/.memory-os-runtime/venv-4.0.5/bin/python
SQLITE_MANIFEST="$PWD/.local-qualification/release-4.0.5-v5/manifest.json"
SQLITE_PLIST="$HOME/Library/LaunchAgents/com.romain.memory-os-v4.plist"
SQLITE_DRY="$PWD/.local-qualification/sqlite-activation-copy-claude-v1"
nice -n 15 "$SQLITE_PY" -B scripts/qualify_sqlite_activation.py \
 --source "$HOME/.memory-os-v4" --output "$SQLITE_DRY" \
 --runtime-python "$SQLITE_PY" --release-manifest "$SQLITE_MANIFEST" \
 --production-plist "$SQLITE_PLIST"
```

`report.json.ok=true` est une preuve sur copie, **pas un GO d'activation**.
Claude relit les inventaires, timings et adaptateur. Dossiers privés 0700 ; aucun
texte de souvenir dans les rapports de sonde, aucune clé/jeton sérialisé. Le
backup du dry run utilise sa propre clé 0600, jamais celle de production.

## 3. Préparer les plists inactifs (aucun déploiement)

Choisir une destination neuve réelle, hors magasin JSONL et hors dossier de
qualification (ne jamais compresser/nettoyer ensuite un dossier devenu autorité).
Exemple daté explicite ; ne pas réutiliser si une tentative l'a créé :

```sh
SQLITE_NEW="$HOME/memory-os-local/stores/sqlite-20261011-v1"
SQLITE_STAGE="$PWD/.local-qualification/sqlite-activation-plists-v1"
"$SQLITE_PY" -B scripts/prepare_sqlite_activation_plists.py \
 --server-plist "$SQLITE_PLIST" \
 --backup-plist "$HOME/Library/LaunchAgents/com.romain.memory-os-backup.plist" \
 --health-plist "$HOME/Library/LaunchAgents/com.romain.memory-os-health.plist" \
 --data-dir "$SQLITE_NEW" --runtime-python "$SQLITE_PY" --work-state 1 \
 --output "$SQLITE_STAGE"
```

`--work-state 1` dans un template n'active rien ; le déploiement exige GO L7b.
Sans ce GO, générer un autre dossier avec `--work-state 0`. Les templates conservent
calendriers/labels/token/key/ops/backup paths ; refus si les paths opérationnels
divergent. Variables serveur : STORE_BACKEND=sqlite_s1, DIR=nouveau dossier,
WORK_STATE=1 (si GO), WRITE_MODE=read_only, SQLITE_EMBEDDING_NAMESPACE=v1 ou la
valeur explicitement préexistante. Namespace inchangé pour conserver le cache.
CL100K_PATH, BUILD_COMMIT, modèle/device/dtype/batch, paramètres de recherche et
confinements sont conservés. Aucun registre ajouté. Pas de nouveau cache externe :
embedding_cache est dérivé dans store.sqlite3 ; poids/tokenizer restent offline.

## 4. Commandes d'activation — exclusivement après GOs

**STOP au premier échec**, pas d'auto-fallback/effacement/rejeu. Sauvegarder les
trois plists et le pointeur runtime, noter cut/items/UUID/receipts. Résoudre tous
les journaux L3 incertains avant fenêtre ; si non résolus, différer l'activation.
Les commandes suivantes sont un runbook à exécuter par Claude, jamais exécutées
par Codex. Aucune publication ou changement de runtime nécessaire.

```sh
SQLITE_DOMAIN="gui/$(id -u)"
SQLITE_SAVED="$PWD/.local-qualification/sqlite-activation-plists-before-v1"
mkdir -m 700 "$SQLITE_SAVED"
cp "$SQLITE_PLIST" "$SQLITE_SAVED/"
cp "$HOME/Library/LaunchAgents/com.romain.memory-os-backup.plist" "$SQLITE_SAVED/"
cp "$HOME/Library/LaunchAgents/com.romain.memory-os-health.plist" "$SQLITE_SAVED/"
# Après GO fenêtre : suspendre les deux jobs avant le restart du serveur.
launchctl bootout "$SQLITE_DOMAIN/com.romain.memory-os-backup"
launchctl bootout "$SQLITE_DOMAIN/com.romain.memory-os-health"
"$HOME/memory-os-local/ops/window_v1.sh" on
# Recontrôler les journaux après ce restart : tout appel devenu uncertain
# doit être réconcilié avant l'arrêt/migration, sans rejouer la mutation.
# Backup JSONL final selon la procédure déjà qualifiée ; le serveur reste read_only.
MEMORY_V4_DIR="$HOME/.memory-os-v4" MEMORY_V4_SERVICE_LABEL=com.romain.memory-os-v4 \
 "$SQLITE_PY" -m memory_os_v4.ops.cli backup
launchctl bootout "$SQLITE_DOMAIN/com.romain.memory-os-v4"
# Attendre réellement la disparition du PID et confirmer le writer.lock libre.
# Ne pas supprimer un lock pour forcer le passage.
SQLITE_FINAL="$PWD/.local-qualification/sqlite-activation-final-cut-v1"
nice -n 15 "$SQLITE_PY" -B scripts/qualify_sqlite_activation.py \
 --source "$HOME/.memory-os-v4" --output "$SQLITE_FINAL" \
 --runtime-python "$SQLITE_PY" --release-manifest "$SQLITE_MANIFEST" \
 --production-plist "$SQLITE_PLIST"
```

Cette seconde preuve est sur le **dernier cut arrêté**, avec les mêmes critères,
sans transférer les résultats d'un ancien snapshot aux nouvelles écritures.
Vérifier ok=true et production_modified=false. Le rapport original reste intact.
Après GO SQLite, vers le dossier neuf (parent créé, destination absente) :

```sh
mkdir -p "$HOME/memory-os-local/stores"
PYTHONPATH='' nice -n 15 "$SQLITE_PY" -B scripts/migrate_sqlite_activation.py \
 "$SQLITE_FINAL/snapshot" "$SQLITE_NEW" > "$SQLITE_FINAL/migration-simulation.json"
PYTHONPATH='' nice -n 15 "$SQLITE_PY" -B scripts/migrate_sqlite_activation.py \
 "$SQLITE_FINAL/snapshot" "$SQLITE_NEW" --apply > "$SQLITE_FINAL/migration-applied.json"
```

Pour toutes les vérifications/jobs, charger les variables exactes du template
sans imprimer de secrets ni utiliser eval. Cette fonction n'exécute que la
commande explicitement passée, pas les ProgramArguments du plist :

```sh
sqlite_env_run() {
 "$SQLITE_PY" -B -c '
import os, plistlib, sys
with open(sys.argv[1], "rb") as f:
    profile = plistlib.load(f)["EnvironmentVariables"]
env = {k:v for k,v in os.environ.items() if not k.startswith(("MEMORY_", "OPENAI_", "ANTHROPIC_"))}
env.update(profile, PYTHONPATH="", PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1", PYTHONOPTIMIZE="0")
os.execvpe(sys.argv[2], sys.argv[2:], env)
' "$SQLITE_STAGE/com.romain.memory-os-v4.plist" "$@"
}
sqlite_env_run nice -n 15 "$SQLITE_PY" -B scripts/qualify_sqlite_prepared_store.py \
 --store "$SQLITE_NEW" --copy-report "$SQLITE_FINAL/report.json" \
 --plan "$SQLITE_FINAL/plan.json" --output "$SQLITE_FINAL/prepared-store.json"
plutil -lint "$SQLITE_STAGE/com.romain.memory-os-v4.plist"
# Après revue du prepared-store.ok et GO L7b si work-state=1 :
cp "$SQLITE_STAGE/com.romain.memory-os-v4.plist" "$SQLITE_PLIST"
launchctl bootstrap "$SQLITE_DOMAIN" "$SQLITE_PLIST"
```

Exiger HTTP200 ready=true, version4.0.5/commit e7d5abb, backend sqlite_s1,
instance_id=final-cut, registry=false, work_state/CAS selon GO, write_mode=read_only.
L7b démarre à froid : ready_work_only/profile historique est honnête ; la première
recherche charge le modèle, lit le cache préparé et doit annoncer zéro miss corpus.
Les dix sondes figées ont été vérifiées sur l'autorité préparée **offline**, sans
modifier l'accès/recence métier ; pas de comparaison post-hoc aux scores BEAM.

Contrôle authentifié exact après ready (réexécuter après window off en remplaçant
want='read_only' par 'read_write') ; token lu depuis son fichier, jamais imprimé :

```sh
sqlite_env_run "$SQLITE_PY" -B - "$SQLITE_FINAL/report.json" <<'PY'
import anyio, json, os, sys
from memory_os_v4.ops.settings import OpsSettings
from memory_os_v4.ops.auth import http_get, call_tool
s = OpsSettings.from_env()
want = 'read_only'
with open(sys.argv[1]) as f:
    ref = json.load(f)['worker']
code, body = http_get(s.ready_url, s)
r = json.loads(body)
c = r['capabilities']
assert code == 200 and r['ready'] is True
assert r['version'] == '4.0.5' and r['commit'] == 'e7d5abbbc41972359497a922b788e798b5b4c1cf'
assert c['store']['backend'] == 'sqlite_s1' and c['store']['instance_id'] == ref['store_instance_id']
assert c['policies']['write_mode'] == want
assert c['work_state'] is (os.environ.get('MEMORY_V4_WORK_STATE') == '1')
assert c['projects']['registry_enabled'] is False
async def check():
    await call_tool('memory_search', {'project':'*','query':'memory-os-ai','top_k':10}, s)
    status = await call_tool('memory_status', {'project':'*'}, s)
    assert status['total'] == ref['live_total'] and status['embedding_cache']['misses'] == 0
anyio.run(check)
print('ready/profile/count/cache checks passed')
PY
```

```sh
sqlite_env_run "$SQLITE_PY" -m memory_os_v4.ops.cli doctor --json > "$SQLITE_FINAL/doctor-read-only.json"
sqlite_env_run nice -n 15 "$SQLITE_PY" -m memory_os_v4.ops.cli backup \
 --backend sqlite --online --apply > "$SQLITE_FINAL/first-online-backup.json"
sqlite_env_run nice -n 15 "$SQLITE_PY" -m memory_os_v4.ops.cli restore-drill \
 --backend sqlite --apply --reconcile-forgets > "$SQLITE_FINAL/first-private-drill.json"
sqlite_env_run "$SQLITE_PY" -m memory_os_v4.ops.cli doctor --json > "$SQLITE_FINAL/doctor-after-drill.json"
```

Exiger backup.ok/qualified/verified=true, UUID autorité conservé ; drill.ok=true,
private_reopen_verified=true, forget_reconciliation=complete, nouvelle identité
privée, server_check.corpus_reencodings=0. **Pas --no-server** pour ce gate.
Doctor0FAIL ; inspecter WARN et politiques storage/backup/drill. Puis seulement :

```sh
"$HOME/memory-os-local/ops/window_v1.sh" off
```

Revalider ready=true/read_write/backend/identité. Write round-trip relayé avec
request_id neuf (ADD puis forget dans zz-test), reçu completed ; task
create/update/done/CAS dans un projet de test si GO L7b, puis SessionStart list8.
Ne jamais répéter une mutation uncertain. Conserver durée fenêtre/cuts/hashes.

## 5. Jobs et retour arrière

Après GO politique : installer les templates **backup et health uniquement**
(pas l'ancien backup JSONL ; pas de régénération globale des clients), et le drill :

```sh
cp "$SQLITE_STAGE/com.romain.memory-os-backup.plist" "$HOME/Library/LaunchAgents/"
cp "$SQLITE_STAGE/com.romain.memory-os-health.plist" "$HOME/Library/LaunchAgents/"
cp "$SQLITE_STAGE/com.romain.memory-os-v4.restore-drill.plist" "$HOME/Library/LaunchAgents/"
launchctl bootstrap "$SQLITE_DOMAIN" "$HOME/Library/LaunchAgents/com.romain.memory-os-backup.plist"
launchctl bootstrap "$SQLITE_DOMAIN" "$HOME/Library/LaunchAgents/com.romain.memory-os-health.plist"
launchctl bootstrap "$SQLITE_DOMAIN" "$HOME/Library/LaunchAgents/com.romain.memory-os-v4.restore-drill.plist"
```

Backup en ligne à 04h selon calendrier existant, nice15, archive SQLite vérifiée,
rotation existante 7 jours/4 semaines ; health08h30 route automatiquement SQLite
et signale marge insuffisante, reçus/tâches, backup âgé/drill absent ou vieux.
Drill privé réconcilié hebdomadaire lundi03h30, nice15, peut charger le modèle.
Les templates jobs restent read_only (mode du client CLI, pas changement du serveur).
Pas de garantie RPO24h si Mac endormi/job manqué ; publier l'âge effectif du dernier
cut et choisir fréquence/RPO/destination externe avec Romain (aucune copie R4 automatique).
Ne pas faire tourner backup/drill simultanément : lease S4 refuse le second.

Retour : suspendre les trois jobs, window on puis arrêt complet du writer,
backup SQLite online final avant arrêt, garder l'autorité courante/archives.
Même avant nouvelles écritures, privilégier l'export courant pour éviter un cut
oublié ; **après nouvelles écritures, ancien JSONL interdit** :

```sh
SQLITE_ROLLBACK="$HOME/memory-os-local/stores/jsonl-rollback-20261011-v1"
PYTHONPATH='' nice -n 15 "$SQLITE_PY" -m memory_os_v4.sqlite_migrate export \
 "$SQLITE_NEW" "$SQLITE_ROLLBACK" --rollback > "$SQLITE_FINAL/rollback-export.json"
```

Lease exclusive + WAL checkpointé requis : si WAL non vide, STOP, ne pas effacer
WAL/SHM pour forcer une lecture immutable. Export de toutes les versions courantes,
blocks, reçus explicites/pending, tombstones et sidecars tasks/CAS ; UUID **nouveau**,
recovery_history/rollback.json/reçu de contrôle. Vérifier les hashes/inventaires
contre SQLite courant, rouvrir et qualifier une copie JSONL sous 4.0.5/C4/MPS.
Pas de remise en production d'un --original ni du snapshot initial.

Après GO retour, adapter la copie du plist à DIR=SQLITE_ROLLBACK,
STORE_BACKEND=jsonl, WORK_STATE=0, WRITE_MODE=read_only ; conserver modèle/profile,
table/token/commit. Vérifier nouveau UUID/lineage, compte courant, forgets et
classements sur le même cut ; tasks et CAS inactifs explicitement annoncés.
Reconfigurer les jobs JSONL puis reopen seulement après qualification. Les anciennes
sessions voient identity_changed : réconciliation humaine des appels incertains,
jamais replay automatique. L'ancien JSONL et le SQLite courant restent conservés.

Modification exacte du template serveur de retour, jamais du snapshot de données :

```sh
SQLITE_BACK_PLIST="$SQLITE_FINAL/server-rollback.plist"
"$SQLITE_PY" -B - "$SQLITE_STAGE/com.romain.memory-os-v4.plist" "$SQLITE_ROLLBACK" "$SQLITE_BACK_PLIST" <<'PY'
import plistlib, sys
with open(sys.argv[1], 'rb') as f:
    p = plistlib.load(f)
p['EnvironmentVariables'].update(MEMORY_V4_DIR=sys.argv[2], MEMORY_V4_STORE_BACKEND='jsonl',
                                MEMORY_V4_WORK_STATE='0', MEMORY_V4_WRITE_MODE='read_only')
p['StandardErrorPath'] = sys.argv[2] + '/server.log'
p['StandardOutPath'] = sys.argv[2] + '/server.out.log'
with open(sys.argv[3], 'xb') as f:
    plistlib.dump(p, f)
PY
plutil -lint "$SQLITE_BACK_PLIST"
# Après qualification de la copie de rollback et GO retour :
cp "$SQLITE_BACK_PLIST" "$SQLITE_PLIST"
launchctl bootstrap "$SQLITE_DOMAIN" "$SQLITE_PLIST"
```

Les jobs JSONL à restaurer doivent eux aussi pointer vers SQLITE_ROLLBACK (nouvelle
autorité), pas ~/.memory-os-v4 ; modifier DIR dans des copies des plists sauvegardés
avant bootstrap. Le runtime reste 4.0.5, pas un rollback logiciel implicite.
