# Candidate 4.0.3 : qualification sur copie, pilote, puis GO distincts

Aucun service live contrôlé, aucun magasin live ouvert par un moteur de ces
scripts. La production reste 4.0.2/auto→MPS/native/batch32. Les commandes ci-dessous
sont exécutées **par Claude hors sandbox**, séquentiellement. Pas de téléchargement,
pas d'API, pas de GitHub/PyPI. Dossier de livraison : `releases/4.0.3/QUALIFICATION.md`.

## Qualification après import du bundle

Les artefacts et les deux venv privés doivent rester ceux du manifeste ; ils
sont hors bundle mais présents dans le clone partagé. Ne pas reconstruire pour
les publier. Les scripts vérifient les SHA et provenance installée. Une copie
nouvelle doit être utilisée, jamais la copie d'une autre tentative.

```sh
cd /Users/romain/Documents/Codex/2026-10-09/v/work/implementation
release_python=/Users/romain/.memory-os-runtime/venv-4.0.2/bin/python
release_dir=.local-qualification/release-4.0.3
"$release_python" scripts/source_release_checks.py \
  --manifest "$release_dir/manifest.json" --output "$release_dir/source-proof.json"
"$release_python" scripts/verify_release_manifest.py "$release_dir/manifest.json" \
  --record-source-proof "$release_dir/source-proof.json"
"$release_python" scripts/qualify_release_copy.py \
  --manifest "$release_dir/manifest.json" --environments "$release_dir/environments.json" \
  --output-root .local-qualification/copy-4.0.3-v1
"$release_python" scripts/verify_release_manifest.py "$release_dir/manifest.json" \
  --record-copy-proof .local-qualification/copy-4.0.3-v1/report.json
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
  --record-copy-proof .local-qualification/copy-4.0.3-v1/report.json \
  --copy-review /Users/romain/memory-os-local/reviews/copy-review.json
```

Un profil différent, une écriture source, un corpus réencodé au reopen ou un
classement différent wheel/sdist/reopen sont des blocages non dérogeables.
Une revue de copie ne vaut jamais GO de bascule ni preuve de supériorité.

## Pilote réponses via ChatGPT (deuxième gros job, après copie relue)

Le dossier `releases/4.0.3/qualification.json` donne le **source_commit** figé.
Le helper sélectionne le Git local qui contient ce commit et vérifie toutes
les sources moteur/banc. Aucun résultat historique n'est rejoué. Avant answer,
Claude vérifie l'usage du quota inclus sans achat/recharge côté compte ; la
facturation/quota restant ne sont pas observables par ce harness.

```sh
pilot_commit=$(/Users/romain/.memory-os-runtime/venv-4.0.2/bin/python -c \
 'import json; print(json.load(open("releases/4.0.3/qualification.json"))["source_commit"])')
./scripts/run_answer_pilot.sh prepare \
  --plan-dir .local-qualification/answer-pilot-production-v1 --source-commit "$pilot_commit" \
  --copy-proof .local-qualification/copy-4.0.3-v1/report.json
./scripts/run_answer_pilot.sh retrieve \
  --plan-dir .local-qualification/answer-pilot-production-v1
./scripts/run_answer_pilot.sh answer \
  --plan-dir .local-qualification/answer-pilot-production-v1
```

Arrêt à la première erreur ; aucun relancement d'`answer` ni effacement/renommage
pour contourner l'arrêt. `prepare` : zéro appel modèle ; `retrieve` : deux
conversations privées, MPS/native/batch32 attested, zéro appel LLM ; `answer` :
maximum40 tentatives, aucun API/clé, pas de sous-agent/outils/mémoire live.
Le profil et les dépendances du pilote doivent correspondre au reçu copie.
[Protocole figé](../bench/protocols/answer-pilot-production-v1.md). Publier seulement
les chiffres descriptifs et limites ; relire les20 réponses pour les refus
sémantiques (un JSON valide n'atteste pas l'utilité). Pas de GO dépenses implicite.

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
