# Candidate 4.0.3 : qualification sur copie, pilote, puis GO distincts

Aucun service live contrôlé, aucun magasin live ouvert par un moteur de ces
scripts. La production reste 4.0.2/auto→MPS/native/batch32. Les commandes ci-dessous
sont exécutées **par Claude hors sandbox**, séquentiellement. Pas de téléchargement,
pas d'API, pas de GitHub/PyPI. Dossier de livraison : `releases/4.0.3/QUALIFICATION.md`.

## Qualification après import du bundle

Tentative v1 conservée intégralement dans `.local-qualification/release-4.0.3/` :
9/10 TCP verts, assertion de version figée à4.0.2. Correction v2 : comparaison
avec `memory_os_v4.__version__`, version de la façade legacy alignée4.0.3,
smoke des deux paquets comparé à la distribution. Aucun résultat v1 ne vaut
preuve v2 ; reprendre à STEP1, dans le nouveau dossier ci-dessous. Ne pas
renommer, effacer ou réutiliser les preuves échouées.

Les artefacts et les deux venv privés doivent rester ceux du manifeste ; ils
sont hors bundle mais présents dans le clone partagé. Ne pas reconstruire pour
les publier. Les scripts vérifient les SHA et provenance installée. Une copie
nouvelle doit être utilisée, jamais la copie d'une autre tentative.

```sh
cd /Users/romain/Documents/Codex/2026-10-09/v/work/implementation
release_python=/Users/romain/.memory-os-runtime/venv-4.0.2/bin/python
release_dir=.local-qualification/release-4.0.3-v2
"$release_python" scripts/source_release_checks.py \
  --manifest "$release_dir/manifest.json" --output "$release_dir/source-proof.json"
"$release_python" scripts/verify_release_manifest.py "$release_dir/manifest.json" \
  --record-source-proof "$release_dir/source-proof.json"
"$release_python" scripts/qualify_release_copy.py \
  --manifest "$release_dir/manifest.json" --environments "$release_dir/environments.json" \
  --output-root .local-qualification/copy-4.0.3-v2
"$release_python" scripts/verify_release_manifest.py "$release_dir/manifest.json" \
  --record-copy-proof .local-qualification/copy-4.0.3-v2/report.json
"$release_python" scripts/installed_relay_e2e.py \
  --manifest "$release_dir/manifest.json" --environments "$release_dir/environments.json" \
  --output "$release_dir/installed-relay-proof.json"
"$release_python" scripts/verify_release_manifest.py "$release_dir/manifest.json" \
  --record-relay-proof "$release_dir/installed-relay-proof.json"
"$release_python" scripts/verify_release_manifest.py "$release_dir/manifest.json"
```

La suite v3 locale utilise explicitement un encodeur synthétique ; aucune
prétention de validation sémantique neuronale. `source_release_checks` rejoue
aussi toute la suite v3 avec le vrai MiniLM cached, sans ce double de test.
Les contournements OpenMP sont ceux déjà appliqués par le moteur v4 ; la
qualification source les fixe explicitement, avec threads1.

Arrêter au premier échec ; chaque commande distincte n'implique pas autorisation
de continuer après refus. Attendu source :10 TCP +1 MiniLM cached + suite v3 complète réelle (compteur
figé dans le manifeste), zéro skip.
Deux relais installés :round-trip, arrêt brutal serveur synthétique, résultat
incertain/panne bornée, reconnexion sans rejeu, deux faits acquittés seulement.

### Ce que prouve le drill copie

Premier gros job : copie stable de tous les fichiers réguliers du magasin JSONL
(sauf writer.lock/server.log et rotations ; archives et traces de réparation
conservées), plafond256 Mo. SHA avant/après copie/source, aucune répétition en
cas de mutation concurrente, aucun lease/stop imposé au serveur live. Une copie
stable n'est pas une preuve de durabilité JSONL en cas de crash multifichier.

Snapshots privés indépendants : baseline installée4.0.2 puis wheel4.0.3/reopen,
sdist4.0.3/reopen. Même configuration issue du plist **sans** store/token/service
live, même timestamp de requête figé, dix requêtes FR/EN fixées dans le worker,
aucune annotation benchmark. Le worker ne peut ouvrir que son sous-dossier owned.
Pas de compaction/consolidation ; journal d'accès désactivé uniquement sur ces
copies pour éviter de changer les sondes suivantes. Logs auteur/archives/trace
inchangés byte à byte, autres métadonnées inchangées hors caches/version.

Mesure :cold/reopen(seconds), textes corpus encodés, profil réel des paramètres,
révision modèle et quatre versions runtime. Baseline/candidate doivent partager
backend/précision/batch512/32/runtime ; wheel/sdist/reopen même profil. Reopen :
**zéro texte corpus réencodé**, warmup/requêtes comptés séparément par la durée.
Le profil du processus baseline ne rétro-atteste pas les anciens vecteurs :
leur backend/precision demeurent inconnus si la méta était absente.
Classements complets par requête, scores et timings conservés ; wheel/sdist/reopen
identiques. Doctor interroge un serveur **privé**, port dynamique ≠8765/8799,
jeton privé0600, /health et /ready sécurisés. Absence de backups/launchd/clients
sur ce périmètre privé = WARN explicites ; **zéro FAIL** exigé, aucun doctor live.

Swap >8.5 Go, disque <5 Go ou mesure indisponible : arrêt, nice15, concurrence1,
timeout1200 s/worker, startup doctor300 s. Serveurs/processus enfants uniquement
arrêtés. Aucun `~/.memory-os-v4` en argument du worker, jamais de migration implicite.
Le programme maître lit seulement les fichiers de production pour les copier.

Si des classements baseline→candidate changent, `report.json` conserve les
requêtes et l'explication **technique** (cache ancien non attesté reconstruit).
Cela ne démontre pas équivalence de qualité. La garde refuse ce cas jusqu'à
revue Claude explicite, liée au SHA original. Après examen des logs et des
classements, Claude peut écrire dans son périmètre un `copy-review.json` :

```json
{"report_sha256":"SHA256_COMPLET_DU_REPORT_ORIGINAL","reviewer":"Claude",
 "accepted":true,"rationale":"Explication spécifique, au moins 80 caractères : requêtes changées, corpus/provenance inchangés, profils natifs identiques, risques résiduels. Ne pas copier ce texte générique pour accepter."}
```

Puis remplacer **seulement** la commande d'enregistrement copie par :

```sh
"$release_python" scripts/verify_release_manifest.py "$release_dir/manifest.json" \
  --record-copy-proof .local-qualification/copy-4.0.3-v2/report.json \
  --copy-review /Users/romain/memory-os-local/reviews/copy-review.json
```

Un profil différent, une écriture source, un corpus réencodé au reopen ou un
classement différent wheel/sdist/reopen sont des blocages non dérogeables.
Une revue de copie ne vaut jamais GO de bascule ni preuve de supériorité.

## Pilote réponses via ChatGPT — amendement explicite v2

La qualification complète4.0.3 v2 est terminée par Claude. Les artefacts et leur
source53030c2 restent figés. Le pilote v1 a consommé un appel lecteur terminé,
refusé à tort pour deux avertissements de démarrage ; les preuves restent
immuables dans `.local-qualification/answer-pilot-production-v1/`.

Le nouveau harness réutilise les contextes attestés et la première réponse
revalidée, sans refaire ce CLI :39 appels nouveaux maximum,40 cumulés. Le nouveau
reçu fournit sa source distincte de celle des artefacts. Exécuter hors sandbox,
stop au premier échec ; **ne pas lancer retrieve**, déjà réutilisé par prepare :

```sh
cd /Users/romain/Documents/Codex/2026-10-09/v/work/implementation
pilot_commit=$(/Users/romain/.memory-os-runtime/venv-4.0.2/bin/python -c \
 'import json; print(json.load(open("releases/answer-pilot-warnings-v2/qualification.json"))["source_commit"])')
./scripts/run_answer_pilot.sh prepare \
  --plan-dir .local-qualification/answer-pilot-warnings-v2 --source-commit "$pilot_commit" \
  --copy-proof .local-qualification/copy-4.0.3-v2/report.json \
  --previous-plan .local-qualification/answer-pilot-production-v1
./scripts/run_answer_pilot.sh answer \
  --plan-dir .local-qualification/answer-pilot-warnings-v2
```

Préparation : zéro modèle/LLM ; vérification Git/runtime/modèles/CLI/copie/preuves
originaux, SHA des contextes conservé, ledger exclusif interdisant un deuxième
budget. Aucun rename/delete/restart de v1. Avant answer, Claude vérifie le quota
inclus sans achat/recharge : quota restant/facturation non observables par harness.
Aucun appel d'API ni relancement automatique. Même Luna low/Astra high, plafonds,
questions et configuration d'encodage ;**3510s CLI nouvelles +90s réservées**.
[Amendement figé](../bench/protocols/answer-pilot-production-v2.md).
Les scores restent descriptifs, sans comparaison officielle/claim ; relire les
réponses pour refus/abstentions. Publication/bascule4.0.3 restent des GO distincts.

## Bascule et retour arrière : procédure pour un GO ultérieur seulement

Ce paragraphe est un runbook, **aucune étape live autorisée aujourd'hui**.

1. Claude présente à Romain SHA des deux artefacts, preuves source/TCP/copies,
   doctor/reopen, revue éventuelle des changements de recherche, durée du rebuild
   et profils. Vérifier absence de nouveaux événements depuis la copie : celle-ci
   ne sera jamais substituée au magasin actuel, pour ne pas perdre de faits récents.
2. Après GO bascule explicite : sauvegarder plist/env et créer un backup qualifié
   à point d'arrêt du serveur, par l'outil admin actuel (service_label explicite).
   Vérifier le backup puis fermer les clients/serveur et leur écrivain ; pas de
   copie brute pendant mutation. Installer l'artefact qualifié dans un NOUVEAU
   runtime4.0.3, mêmes versions des dépendances que la qualification, sans modifier
   le venv4.0.2. Conserver backup/runtime4.0.2. Publication est un autre GO.
3. Préserver auto/native/batch32/512 ; ne pas passer à CPU, float32 ou batch4.
   Si Romain préfère MPS explicite, le changement de plist a son GO et refuse
   tout fallback CPU. Confinements1.01 restent. SQLite désactivé. Modifier uniquement
   le chemin du runtime/build_commit vers la source qualifiée et relancer via Claude.
   Laisser le moteur seul reconstruire les caches inconnus, selon durée qualifiée.
4. Vérifier /ready(version/commit/profil), status(corpus), doctor, requêtes fixées
   et un round-trip sans supersession imprévue. Reconnecter les clients seulement
   après succès. Toute activation et résultat consignés par Claude.
5. Échec avant nouvelles écritures : arrêter4.0.3, rétablir plist/runtime4.0.2.
   **Ne pas ouvrir avec4.0.2 des caches4.0.3 issus d'un autre profil** : utiliser
   backup prébascule vérifié, ou garder les logs courants et retirer seulement les
   caches dérivés identifiés, afin que4.0.2 reconstruise sur MPS/native/32.
   Méta d'encodage à inspecter, jamais prétendre adoption automatique sûre par l'ancien
   moteur. Après nouvelles écritures, préserver les logs/réceptions courants ; aucun
   retour au backup écrasant ces écritures. Rebuild hors service, tests puis reprise.
   Supprimer des caches ou restaurer un backup live demande le GO et le protocole ops
   de Claude ; ce lot ne fournit aucun script destructif de rollback.
