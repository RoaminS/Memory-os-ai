# S4 incrément2 — récupération contrôlée, à qualifier hors sandbox

2026-10-11. Base commune `0befeb3` ; inclut correction1b `7dff573`.
Version inchangée4.0.4, développement pour4.0.5. Aucune publication/bascule,
activation SQLite, modification directe de production ou campagne LLM.

## 1. Livré et conditions de preuve

- **Metadata :** les deux lectures utilisent `metadata`, comme SQLiteStore.
  Régression réelle SQLiteStore/Hash ; incrément1b :39 S4 et52 avec préflight verts.
- **Récupération :** archive d'abord comparée exhaustivement ; source actuelle
  SQLite active, read-only, même instance, compteur≥cut et historique immuable
  de l'archive contenu exactement dans l'autorité. Les reçus explicites anciens
  doivent subsister ; la purge normale des implicites est permise.
- **R5 :** scan complet des versions pour tombstones, liste des IDs/projets/
  révisions/SHA et résultats deleted/already_deleted/absent_from_snapshot.
  Une liste vide est justifiée par le scan exhaustif. Ce schéma ancien ne relie
  pas chaque version oubliée à son reçu original, souvent implicite/purgé :
  aucune association n'est inventée. Preuve = historique immuable, cut courant
  attesté, plus **reçu explicite de récupération** portant la liste complète.
  Son ID/portée operator:s4, séquence et résultat sont conservés dans la copie.
- **Identité :** un BEGIN IMMEDIATE pour tombstones, chunk map, nouvelle
  store_instance_id, restored_from_instance_id, événement/historique de récupération
  et reçu final. Versions/provenances, anciens reçus, blocs, legacy et cache
  préexistants sont conservés ; seuls ajouts/diffs annoncés sont autorisés.
- **Ouverture privée :** `--reconcile-forgets` explicite, autorité vérifiée avant
  et après les sondes ; ready200 authentifié, commit fourni, profil identique,
  total, trois témoins au maximum, zéro réencodage corpus, ancien ID refusé.
  Token privé neuf0600, port loopback éphémère, serveur read_only, HF offline,
  arrêt du groupe processus en finally. Aucune promotion/pointeur production.
- **Rotation :** archive exclusive, fsync, suppression du lien temporaire avant
  enregistrement qualifié ; rétention7 jours/4 semaines/pinned, uniquement
  archives SQLite connues et SHA intact. Fichiers inconnus/corrompus/diagnostics
  conservés. Une erreur de rotation est un avertissement, pas une invalidation
  de la sauvegarde déjà vérifiée/durable.
- **Modèles hebdomadaires :** opt-in inactifs, exigent sqlite_s1 et commit qualifié
  avant toute création de token ; backend et profil propagés au job. Drill
  privé avec rapprochement des oublis ; génération seule, jamais launchctl load.

Toujours `quarantined=true`, `reopening_allowed=false` dans le résultat opérateur.
`private_reopen_verified=true, level=service` prouve seulement le drill privé.
Source absente, lignée divergente, historique incomplet, tombstone ambigu ou
mutation après rapprochement → refus, preuve conservée, aucun succès partiel.

## 2. Preuves locales et incidents conservés

**Rapport final** `.local-qualification/405-s4-increment2-codex-unit-v3/report.json` :
ok=true, **585 v3 +1582 v4**, zéro échec, configs inchangées ; TCP non exécuté.
Ruff projet et E9/F sur v3/v4/bench/scripts passent. Aucun téléchargement/modèle
neural/API appelé. Les petites unités Hash tournent localement, les contrôles
ressources réels restent obligatoires pour TCP, CLI opérateur et lifecycle10k.

S4 :71 tests, dont matrice20 frontières ENOSPC simulées ; SIGKILL aux20 frontières
et après ACK, zéro lot partiel annoncé et tous les ACK retrouvés avec reçu exact.
Backup :11 frontières SIGKILL (ouverture/pin/copie/vérification/publication/
enregistrement/ACK), source logique conservée et archives qualifiées intactes.
S2 et S3/migration sont aussi rejoués dans la suite finale. ENOSPC simulé ne
remplit pas le disque ; ces tests ne prouvent pas toutes les pannes électriques.
Cas S4≤16 MiB à teardown, nettoyage uniquement après réussite, échecs préservés.

Rapports précédents gardés :

- `405-s4-increment2-codex-unit-v1` : interrompu, cinq échecs/rechargements et
  blocage causés par le cycle du guard JSONL. Correction par référence faible,
  ancien handle refusé, test de libération immédiate sans gc.collect.
  Desktop config SHA a aussi changé pendant ce lancement ; entrée MCP toujours
  relayée à la lecture suivante, attribution non prouvée, fichier jamais restauré
  ni modifié par Codex. Ce rapport reste refusé, non utilisé comme qualification.
- `...unit-v2` : deux échecs puis corrections : compteurs de cache déplacés
  dans `memory_status`, hors statistiques sémantiques/brief ; fixture historique
  CPU exclut write_mode, absent de l'archive immuable. Aucun paramètre ni résultat
  historique d'évaluation modifié ; régression Hash uniquement.
- Le run Claude `405-safety-p1-claude-v2` reste INVALID_CONCURRENT_EDIT selon
  Claude et ne fournit aucune preuve. Ne pas qualifier pendant un run Codex.

## 3. Commandes Claude après import et fin du tour Codex

Les deux bundles ont une ref nommée. `sqlite-s4-increment2-v1.bundle` est
cumulatif depuis0befeb3 et peut être importé directement ;1b reste fourni
séparément pour revue. Toujours de nouveaux dossiers, jamais réutiliser un refus.

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:v4/src:v4 \
 .local-qualification/brief-test-runtime-claude-3c-v1/bin/python -B \
 scripts/qualify_sqlite_s4_increment1.py --tcp \
 --output .local-qualification/sqlite-s4-increment2-claude-v1
```

86 cas attendus :71 S4 +13 préflight +B09/B10 TCP. B10 = vrai private restore
Hash, oublis, nouvelle identité, ready200, total/recherche, zéro réencodage.
Les sondes fake des unités ne sont jamais comptées comme preuve physique TCP.

Ensuite, qualification complète, **24 TCP**, au moins1606 v4 +585 v3 :

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:v4/src:v4 \
 MEMORY_V4_CL100K_PATH=/Users/romain/.config/memory-os/cl100k_base.tiktoken \
 .local-qualification/brief-test-runtime-claude-3c-v1/bin/python -B \
 scripts/qualify_405_test_isolation.py --suite full \
 --output .local-qualification/405-s4-increment2-claude-v1
```

Enfin un seul job lifecycle10k séquentiel nice15, garde swap/disque réelle,
source/clé/magasin Hash neufs exclusivement sous le dossier de preuve :

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:v4/src:v4 nice -n 15 \
 .local-qualification/brief-test-runtime-claude-3c-v1/bin/python -B \
 scripts/qualify_sqlite_s4_lifecycle.py --items 10000 \
 --git-dir .local-qualification/lexical-dev-git \
 --source-commit "$(git --git-dir=.local-qualification/lexical-dev-git rev-parse refs/heads/feat/sqlite-s4-increment2)" \
 --output .local-qualification/sqlite-s4-lifecycle-10k-claude-v1
```

Le script vérifie commit/objets/diff/untracked avant mesure. Si le Git utilisé
par Claude est ailleurs, remplacer **les deux** occurrences git-dir par son
Git d'importation, sans changer les sources. Critère : drill≤300 s,9995 items
retrouvés sur10000,5 oublis rapprochés, ready200, zéro réencodage corpus.
`s4_activation_qualified=false` même si ce test passe : copies synthétiques ne
qualifient pas une migration/bascule de la production JSONL.

## 4. Portes restantes et travaux Claude/Romain

- Réconciliation limitée aux items oubliés de l'autorité disponible. Effacement
  physique, blocs/archives/doublons confidentiels et registre indépendant en cas
  de destruction de l'autorité relèvent aussi de L12 ; aucun droit de réouvrir
  sans preuve, aucun fichier source réparé/créé pour contourner ce refus.
- Backup d'un écrivain actif seulement : WAL/SHM existants requis. Runtime≥3.51.3,
  schéma connu, DB64 MiB/300 s. Bases plus grandes et source fermée non qualifiées.
- Profil/namespace/cache/package doivent correspondre ; changement de modèle,
  namespace/version ou cache incomplet demande qualification de reconstruction
  séparée. Le test10k est Hash, pas une mesure Granite/MPS constatée.
- source_build du magasin ancien reste commit non attesté ; commit du serveur
  privé provient de l'attestation opérateur/de livraison. Qualification finale
  de source/déploiement, migration copie JSONL→SQLite et rollback après nouvelles
  écritures sont encore obligatoires avant activation industrielle.
- Claude revoit R5/identité/RPO, fournit le runbook de fenêtre read_only,
  statut des incertains et reconnexion identity_changed. Une promotion réelle
  doit revalider le cut sous gel/arrêt des writers ; le drill n'effectue pas cette
  promotion. Les20 scénarios L7b peuvent avancer, capacités restent false.
- Romain décide RPO/destination externe, activation SQLite/jobs et futures
  publication/bascule. Aucun GO n'est inféré des tests de cette livraison.
