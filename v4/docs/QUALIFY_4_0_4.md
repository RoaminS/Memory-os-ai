# Candidate 4.0.4 : qualification, puis GO distincts

Production actuelle :4.0.3. Aucune publication, bascule ou modification du magasin
live autorisée par ce document. Opérateur :Claude hors sandbox. Incréments3b/3c,
release et copies possèdent des preuves distinctes ; conserver les échecs3b
v1/v2/exact-v1. Aucun résultat échoué ne vaut preuve de qualification.

## 1. Prérequis et incrément

Importer la ref nommée `feat/404-increment-3c`, préserver les modifications
personnelles aux configs, puis exécuter **toutes** les commandes de
`QUALIFY_404_INCREMENT3C.md` dans leurs dossiers neufs. La table officielle est
déjà disponible ; aucun téléchargement ni nouvelle autorisation requis ici.
Registre inactif : `unset MEMORY_V4_PROJECT_REGISTRY`. Les hooks gardent Q0v2.

## 2. Qualification des artefacts déjà construits

La tentative locale v1 reste préservée : schéma JSON L3/L4 absent du sdist,
collecte refusée ; inclusion corrigée dans les deux distributions.

Dossier :`.local-qualification/release-4.0.4-v4/`. Les artefacts et les deux venv
hors dépôt sont ceux du manifeste ; **ne pas reconstruire** pour ces tests ou une
publication ultérieure. Le manifeste source identifie le commit de construction,
qui peut être un ancêtre du commit contenant le reçu de livraison. Les empreintes
code/tests/scripts/scénarios doivent correspondre, sinon refus.

Exécuter séquentiellement ; **arrêt au premier échec**, pas de bloc `;` continuant
après un refus. Les scripts ne contrôlent que des services privés sur port aléatoire.

```sh
cd /Users/romain/Documents/Codex/2026-10-09/v/work/implementation
brief_python="$PWD/.local-qualification/brief-test-runtime-claude-3c-v1/bin/python"
release_dir=.local-qualification/release-4.0.4-v4
export PYTHONPATH="$PWD/src:$PWD/v4/src:$PWD/v4"
export MEMORY_V4_CL100K_PATH="$PWD/.local-qualification/cl100k/cl100k_base.tiktoken"
unset MEMORY_V4_PROJECT_REGISTRY
"$brief_python" scripts/verify_release_manifest.py "$release_dir/manifest.json" \
  --record-brief-source .local-qualification/404-increment3c-claude-v1/report.json \
  --record-brief-exact .local-qualification/404-increment3c-exact-claude-v1/report.json
"$brief_python" scripts/source_release_checks.py \
  --manifest "$release_dir/manifest.json" --output "$release_dir/source-proof.json"
"$brief_python" scripts/verify_release_manifest.py "$release_dir/manifest.json" \
  --record-source-proof "$release_dir/source-proof.json"
"$brief_python" scripts/qualify_release_copy.py \
  --manifest "$release_dir/manifest.json" --environments "$release_dir/environments.json" \
  --baseline-python /Users/romain/.memory-os-runtime/venv-4.0.3/bin/python \
  --output-root .local-qualification/copy-4.0.4-v1
"$brief_python" scripts/verify_release_manifest.py "$release_dir/manifest.json" \
  --record-copy-proof .local-qualification/copy-4.0.4-v1/report.json
"$brief_python" scripts/installed_relay_e2e.py \
  --manifest "$release_dir/manifest.json" --environments "$release_dir/environments.json" \
  --output "$release_dir/installed-relay-proof.json"
"$brief_python" scripts/verify_release_manifest.py "$release_dir/manifest.json" \
  --record-relay-proof "$release_dir/installed-relay-proof.json"
"$brief_python" scripts/verify_release_manifest.py "$release_dir/manifest.json"
```

Attendu source :20 TCP,1 MiniLM déjà cached, suite v3 réelle complète
(compteur figé dans le manifeste), zéro skip. Les tests neuronaux sont exécutés
par Claude seulement, nice15 et concurrence1. Le contrôle de copies s'arrête si
swap>8.5GB ou disque<5GB ou mesure indisponible ; aucun téléchargement modèle.
Deux relais installés :round-trip, enveloppe brief mesurée exactement, coupure,
statut local pendant panne, reconnexion sans rejeu, exactement deux faits.
Les 20 TCP sources couvrent aussi adoption après crash, rotation et30 cycles.

### Copies : ce qui est vérifié

Lecture seule de tous les fichiers réguliers du magasin production JSONL, hors
verrou/log serveur ; SHA avant/après la copie, plafond256MB. Une écriture
concurrente pendant la copie fait refuser sans retry automatique. Il s'agit d'un
snapshot vérifié, pas d'une promesse de transaction JSONL multifichier.

Baseline **installée4.0.3**, puis wheel4.0.4/reopen et sdist4.0.4/reopen sur copies
indépendantes. Profil auto→MPS/native/batch32/512 repris du plist ; C4 inchangé,
registre inactif, confinement supersession conservé. Aucune consolidation.
Les historiques/provenances/reçus sont byte-identiques ; le nombre d'éléments
et la méta non cache restent inchangés sauf l'enrôlement L3 : si absent,
`store_instance_id` (32 chiffres hexadécimaux) est créé par le moteur sur chaque
copie. Une identité existante ne peut changer ; cold→reopen la conserve.
Ces copies sont des magasins distincts et leurs UUID peuvent différer. Les classements des dix sondes FR/EN sont
identiques wheel/sdist/reopen ; zéro texte corpus réencodé au reopen ; profils
natifs/révision/runtime comparés. Cold/reopen, rebuild et requêtes mesurés
séparément. Doctor privé :zéro FAIL ; absence de vrais clients/launchd privés =WARN.

Si baseline→candidate change, inspecter chaque sonde/profil/cache, puis fournir
une revue **spécifique** liée au SHA du rapport original ; aucun accord automatique.
Le format est celui de `QUALIFY_4_0_3.md`, reviewer Claude, rationale≥80 caractères.
Remplacer seulement l'enregistrement copie par :

```sh
"$brief_python" scripts/verify_release_manifest.py "$release_dir/manifest.json" \
  --record-copy-proof .local-qualification/copy-4.0.4-v1/report.json \
  --copy-review /Users/romain/memory-os-local/reviews/copy-review-404.json
```

Profil différent, perte/altération d'un historique, classement différent entre
artefacts/reopen ou réencodage au reopen sont bloquants et non dérogeables.
En cas d'échec, conserver l'intégralité du dossier ; une nouvelle tentative
nécessite un nouveau dossier et les mêmes artefacts vérifiés, jamais un effacement.

## 3. Bascule ultérieure — uniquement après GO de Romain

1. Présenter manifeste final (`release_ready=true`), SHA wheel/sdist, preuves
   exactes/TCP/copie/doctor et durée de reconstruction. Ce booléen signifie
   qualification technique, jamais permission de publier ou d'activer.
2. Préparer un nouveau runtime4.0.4 avec **les artefacts qualifiés** et mêmes
   dépendances (extra `[ops,brief]`, tiktoken0.12.0 complet). Conserver4.0.3 intact.
   Configurer `MEMORY_V4_CL100K_PATH` vers une copie durable vérifiée de la table,
   accessible au serveur et aux relais ; ne pas dépendre d'un dossier temporaire.
   Le relais doit venir du package4.0.4, sans fork parallèle. Vérifier l'import
   `tiktoken_ext` et les deux tokens de «hello world» avant de toucher au service.
3. Après GO explicite, suspendre tous les écrivains clients et jobs backup/health,
   relever leurs requêtes incertaines ; ne jamais rejouer pour "voir". Sauvegarder
   plist/configs/jeton via le protocole de Claude (secrets hors logs), créer un
   backup qualifié à un point d'arrêt avec l'outil admin existant et le
   `MEMORY_V4_SERVICE_LABEL=com.romain.memory-os-v4` explicite. Le backup et les
   journaux de relais sont conservés ; aucune rotation/purge d'incertains.
4. Arrêter proprement4.0.3 avant d'ouvrir le magasin avec4.0.4. Garder le magasin
   **courant**, jamais la copie de qualification : les écritures depuis le drill
   seraient sinon perdues. Modifier uniquement runtime/build_commit et table
   locale ; maintenir C4, auto/MPS/native/batch32/512, JSONL, COS1.01 et maintenance
   désactivée. **Aucune variable registre**, aucune migration/activation SQLite.
5. Démarrer puis vérifier `/ready` version/commit, profil attesté, capacités,
   registre inactif, table prête, nombre d'éléments, doctor zéro FAIL, brief
   compact, sondes figées et round-trip contrôlé sans supersession inattendue.
   Enregistrer la preuve de chaque relais/client avant de réouvrir les écrivains.
   Consigner la bascule et sa durée ; le GO publication reste séparé.

## 4. Rollback préservant les écritures récentes

Suspendre immédiatement les écrivains/jobs et arrêter4.0.4. Préserver un backup
qualifié du **magasin courant** et tous les journaux de relais, y compris les
entrées pending/uncertain ; relever les IDs depuis le dernier backup. Aucun
retour brut au snapshot prébascule après de nouvelles écritures.

Avant de redémarrer4.0.3, Claude teste sur une **nouvelle copie du magasin courant**
que l'ancien moteur lit exhaustivement items/blocs/reçus/historique, conserve les
IDs et le nombre, puis les dix sondes. L'ancien moteur doit gérer lui-même les
caches selon son profil attesté ; aucune édition manuelle des fichiers live et
aucune bascule CPU. Un échec de compatibilité bloque le rollback automatique :
Romain décide d'une réparation réversible testée sur copie, jamais d'une perte
silencieuse. SQLite étant inactif, aucun downgrade de schéma SQLite à prévoir.

Après GO rollback, rétablir runtime/plist4.0.3 **et** les relais4.0.3 coordonnés,
sans effacer les journaux4.0.4. L'ancien serveur n'offre pas les contrats L3/L1a :
les clients ne doivent pas continuer avec le nouveau relais ni rejouer les
mutations en attente. Résoudre celles-ci avec les preuves4.0.4 sur copie et
revue opérateur. Vérifier /ready/doctor/sondes avant de reprendre les écrivains.
La restauration d'un backup antérieur n'est envisageable qu'après réconciliation
exhaustive des écritures postbackup, preuve sur copie et GO explicite de Romain.
