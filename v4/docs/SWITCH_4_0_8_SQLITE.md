# Bascule 4.0.7 → 4.0.8 : contrat à adapter dans a9_switch

16 octobre 2026. Analyse uniquement, pas un GO. Ne pas exécuter le script a9
4.0.7 tel quel pour 4.0.8. Aucun fichier de Claude n'est modifié dans ce lot.

## Delta établi par le code

- SQLite reste **schéma8** (`SCHEMA_VERSION`, migrations SQL et checksums
  inchangés depuis f003c3a). **Aucune migration** : ni création, ni vidage/réinitalisation
  de `change_events` ou de sa génération/coverage_start. Le journal existant,
  les tâches/blocs/révisions, reçus explicites et tombstones doivent être conservés.
- `embed.py`, le catalogue/provisionnement du modèle, les 61 épingles runtime et
  la construction des clés d'embeddings sont inchangés. Profil **auto→MPS / native /
  batch32 / max512**, Granite révision835ad140… et empreinte encodeur identiques.
  Ce fait de code ne remplace pas les preuves de zéro miss/réencodage sur copie.
- C4 `scoped_literal`, `lexical_guard_k=3`, registre inactif, SQLite/tasks actifs
  restent identiques. Nouveaux champs : provenance déclarative des mutations ;
  policy d'audit readiness bornée ; doctor PEP440. Aucun nouvel outil MCP.
- Bascule = **runtime + relais + définitions des jobs**, sans conversion de données.
  Garder néanmoins une coupe8 pré-bascule et un NOUVEAU répertoire de service :
  ne pas ouvrir le magasin de production en écriture avec un candidat non validé.

## Contrôles obligatoires pour le futur a9_408

1. **P0** : nouveau GO explicite de Romain ; script adapté/haché/revu/répété ;
   manifeste4.0.8 release_ready, inventaire d'outillage correspondant, wheel aux
   octets exacts, comparaison PyPI uniquement si publié. Baseline4.0.7 :
   commit descripteur `9e41ea40141f8ed4d7ed12a92a1900d2409272c3` ; ce n'est PAS
   le commit d'outillage f003c3a. Cible = `manifest.source_commit`, jamais celui
   d'un wrapper ultérieur. Versions des 61 dépendances vérifiées, aucun upgrade.
2. **Ressources** : ≥7 Go libres avant départ/arrêt, ou davantage selon empreinte
   réelle ; swap≤8,5 Go mesurable. Marges S4 de 5 Go + travail préservées en
   continu. Recontrôles avant backup/drill/B ; pas d'override abaissant S4 à3 Go.
3. **Sauvegarde/coupe** : backup8 en ligne vérifié avant arrêt ; arrêter les quatre
   jobs, PID disparus/port fermé, verrou écrivain EXISTANT acquis, WAL absent ou
   vide (ne pas forcer un checkpoint). Copie/APFS vers nouveau dossier privé,
   SHA vérifié de la coupe stable. Inventaire EXHAUSTIF8→8 exact, cache compris ;
   pas d'appel à `verify_upgrade(7,8)` ni d'exigence de journal vide.
4. **Publication** : mêmes protections v2.4 (phases durables, fchmod indépendant
   de umask, sync fichier/remplacement/répertoire, permissions/propriétaire,
   anciens fichiers `.before`, pointeur atomique). Définitions préparées et
   validées AVANT arrêt ; chemins serveur/backup/health/drill/relay cohérents.
5. **Fenêtre A** : serveur4.0.8 `--warm`, `MEMORY_V4_WRITE_MODE=read_only`.
   Prédicat : HTTP200, ok/ready true, version4.0.8, commit du manifeste, identité
   instance inchangée, descripteur embarqué valide, backend sqlite_s1,
   retrieval_ready=true, profil strictement identique au profil4.0.7 figé enP0,
   registry_enabled=false, work_state/block_cas=true, C4 identique, runtime.ok,
   tokenizer.ready ; `policies.write_mode == policies.writes.mode == read_only`,
   `mutations_allowed=false`.
6. **Audit nouveau** : `capabilities.readiness_audit.max_reuse_seconds == 1.0`,
   `live_checks_every_request == true`, détection défaut mémoire non suivi≤1 s.
   La readiness n'atteste pas un audit disque exhaustif à CHAQUE GET. Les audits
   S4/doctor explicites restent exhaustifs ; aucun échec mis en cache.
7. **Avant B** : memory_status cache misses0 et compte identique ; dix recherches
   figées de `release_copy_worker.QUERIES`, même at_time, top10/rerank=false,
   `project="*"` au serveur ou `None` au moteur. Chaque requête/worker≥1 hit,
   IDs ordonnés strictement identiques à la baseline8, pas une égalité vide.
   Doctor0FAIL. Backup8 qualifié + drill8 **service** sur CETTE archive,
   SHA/archive liés, réconciliation complète, identité restaurée nouvelle,
   réouverture privée/commit4.0.8/profil/cache0. Refus de mutation A démontré
   avant toute réouverture, puis preuve B uniquement sous GO.
8. **B et verify** : marqueur durable WRITES_MAY_HAVE_STARTED AVANT l'étape pouvant
   permettre les écritures. ready identique sauf mode read_write et
   mutations_allowed=true ; définitions sur disque ET chargées des quatre jobs,
   serveur running, chemins nouveaux, cache0, doctor0FAIL. Relais installé :
   serverInfo.version4.0.8, même instance/répertoire, ensemble des outils
   inchangé, memory_request_status disponible. Le nouveau test TCP exerce
   l'auteur déclaré jusque dans le journal des sept mutations usuelles.

## Retour et hook

Avant le marqueur : retour sur la coupe/les définitions4.0.7, puis identité→lecture
figée non vide→readiness complète avant auxiliaires (conserver restore_ready).
Le prédicat du retour attend4.0.7/9e41ea4 ; il ne doit pas exiger la nouvelle
capacité readiness_audit absente de4.0.7. Vérifier ensuite profil/cache et doctor.

Après le marqueur : ne jamais restaurer silencieusement l'ancienne coupe et
perdre des écritures. Le schéma8 est commun, mais une qualification de retour
sur copie **incluant les nouveaux reçus/auteurs/écritures** reste nécessaire ;
compatibilité de schéma seule≠autorisation de rollback. Garder le service/data
courants et diagnostiquer selon le contrat a9. Retour ultérieur sous GO séparé.

Hook v7.1 reste en place ; aucune activation/configuration de hook ici. Le
mapping explicite racine→projet est une décision client distincte. Pas de fusion
de `Memory-os-ai`/`memory-os-ai`, ni de registre activé implicitement.
