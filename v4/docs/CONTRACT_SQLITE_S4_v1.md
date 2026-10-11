# L6 / ADR-001 S4 — backup en ligne, restore, crash — proposition

2026-10-11. **Contrat amendé accepté ; incrément 1 en qualification, S4 incomplet.** SQLite reste
opt-in ; JSONL et sauvegardes chiffrées actuelles restent en production. Codex
implémente ; Claude qualifie sur copies. Estimation : 4–7 journées-agent.

## 1. Existant et séquencement

S2 : mutations SQLite atomiques, reçu final, WAL/FULL/fullfsync, hooks de panne.
S3 : cache d'embeddings attesté, rétention reçus implicites, migration hors ligne
réversible, exports après nouvelles écritures. Runtime SQLite ≥3.51.3 exigé par
le code. `ops/backup.py` refuse actuellement SQLite ; son tar de fichiers JSONL
n'est pas une sauvegarde SQLite et ne doit jamais aspirer DB/WAL/SHM séparément.

Ordre : correctifs 4.0.5 et isolation qualifiés → S4 backup/drill → S4 crash et
retour après nouvelles écritures → L7b. Contrats/client UI peuvent avancer avant
activation. La production SQLite exige qualification sur copie et GO distinct.

## 2. Contrat de sauvegarde en ligne

Interface proposée : `memory-os-admin backup --backend sqlite --online` ; aucune
nouvelle exposition d'un outil de backup généraliste au lecteur LLM. Source/
destination issues de la configuration opérateur ; chemins symlinks/hardlinks et
stockages distants non qualifiés refusés. Simulation par défaut tant que S4 est
candidate ; `--apply` explicite. Aucun lancement/arrêt launchd implicite.

Le CLI **n'instancie pas MemoryV4/SQLiteStore contre la production** : connexion
SQLite read-only vers une source déjà prête, query_only, schéma/version attestés.
Elle utilise `Connection.backup`, jamais une copie DB/WAL. Les lectures SQLite
coordonnent les verrous WAL/SHM existants ; aucun SQL d'autorité, changement de
profil ou checkpoint de la source. Si une lecture exige la création d'artefacts
source absents, refus : pas de réparation de la production par le CLI.

Connexion source séparée, transaction de lecture et première lecture fixant le
snapshot ; backup par lots de 128 pages. `snapshot_receipt_sequence` vient du
`receipt_counter` monotone, pas du max des reçus purgés ; ce compteur et l'identité
sont lus **dans ce snapshot**, puis retrouvés dans la destination. Cela évite
d'annoncer un cut provenant d'une lecture préalable distincte. Destination neuve,
répertoire0700, DB0600 ; budget total300 s, busy≤30 s, contrôles ressources au
moins chaque seconde. Nice15, concurrence1 ; disque≥5 Go et réserve DB+archive+
drill estimée, swap≤8,5 Go. Timeout/erreur = partial conservé, jamais backup qualifié.

Destination fermée et autonome sans dépendance au WAL source ; intégrité SQLite,
foreign_key_check, migrations/checksums et intégrité logique vérifiées. Inventaire
exhaustif des tables/version/schema, contenus logiques canoniques et high-water
reçus du snapshot ; inclure meta, versions d'items/blocs, reçus explicites et
implicites retenus, historiques hérités, tâches futures. Les index physiques et
le layout des pages ne sont pas un critère d'égalité byte-à-byte avec la source.

Archive chiffrée authentifiée (clé backup0600 existante, jamais régénérée pour
décrypter), manifeste : format/version, source_build, migrations, store_instance_id,
snapshot cut UTC/sequence, compteurs/hashes logiques, profil/cache, SHA des fichiers.
Format SQLite distinct du format JSONL actuel ; rotation existante uniquement
**après** déchiffrement + extraction privée + comparaison exhaustive réussis.
Cache attesté inclus mais reconstructible ; cache absent/invalidé annoncé avant
restauration. Publication exclusive sans écrasement, fsync fichiers/répertoire ;
un échec laisse une preuve, pas un reçu « backup terminé ».

## 3. Restore / réversibilité / identité

`restore-drill` seulement vers une destination neuve et disjointe ; aucune
extraction dans le magasin actif. Refuser archive corrompue, chemins dangereux,
schéma futur/inconnu et clé absente. Comparer toutes les données logiques à
l'inventaire du snapshot ; rebuild dérivé si nécessaire, reopen, probes et /ready
du serveur privé. 10 000 éléments synthétiques/Hash, profil/cache figés : ≤5 min
jusqu'à ready, zéro réencodage si cache attesté complet. Mesurer froid/chaud séparément.

Une restauration/retour historique **change store_instance_id**, conserve
`restored_from_instance_id` et un événement de récupération. Reçus et provenance
historiques restent intacts ; anciens clients/ETags voient identity_changed et
ne rejouent rien. Un redémarrage normal ou upgrade sans rollback conserve l'ID.
Cette règle ne réécrit pas l'historique pour simuler une continuité inexistante.
La comparaison exhaustive valide d'abord le snapshot original ; ensuite seulement,
le drill inscrit sa nouvelle identité et l'événement de récupération. Un diff
autorisé et vérifié documente ces seules écritures de contrôle supplémentaires.

Restauration production = procédure séparée, jamais action du drill : service
arrêté avec preuve, snapshot du magasin actuel s'il est récupérable, qualification
de la cible, décision Romain sur cut/RPO, changement de pointeur réversible,
reconnexion explicite des clients. Aucun remplacement de magasin en ligne.

Après migration et **nouvelles écritures**, rollback JSONL par export sémantique
S3 du magasin courant, avec versions/reçus/tâches sidecars ; l'ancienne sauvegarde
JSONL seule est refusée car elle perdrait les ajouts. Blocs/CAS/tâches deviennent
read-only/inactifs côté ancien runtime si le schéma n'est pas compris.

## 4. Ce qui est et n'est pas garanti

- Crash logiciel/processus du magasin courant : objectif S2/S4 zéro acquittement
  durable perdu et zéro succès pour un lot partiel dans la matrice définie.
- Restore d'un backup : conservation des commits jusqu'au **cut du snapshot**.
  Les écritures suivantes sont hors RPO, sauf récupération depuis l'autorité
  actuelle/WAL qualifié. Pas de promesse « zéro perte » après destruction du disque.
- Proposer backup quotidien, RPO planifié≤24 h, alerte si dernier backup qualifié
  dépasse24 h ; fréquence et stockage externe à décider par Romain. Copier un
  backup sur le même disque n'est pas une protection contre sa destruction.
- Oublis postérieurs au cut : rapprocher tombstones/registre d'effacements ou
  autorité actuelle avant ouverture aux clients. Si cette preuve manque, restore
  reste en quarantaine ; un GO de bascule ne doit pas masquer une réintroduction
  de données oubliées. Gestion complète des sauvegardes/effacements à coordonner L12.
- SIGKILL/exceptions/enospc simulé ne prouvent pas toutes les pannes électriques.

## 5. Matrice et tâches Claude

| S4 | Injection | Preuve bloquante |
|---|---|---|
| B01 | writers actifs pendant backup | DB destination cohérente au cut, tous reçus≤cut présents, pas de reçu partiel |
| B02 | BUSY, timeout, interruption copie/publication | archive jamais qualifiée, source logique inchangée hors mutations normales |
| B03 | bitflip, clé incorrecte, membre/path/schema invalides | refus avant activation, source et destination ancienne intactes |
| B04 | à chaque frontière S2 puis avant/après COMMIT/avant ACK | tous commits acquittés présents, aucun lot partiel ; un commit à réponse perdue peut être présent et reste incertain |
| B05 | crash avant/après cache/publication backup | index reconstruisible, aucune nouvelle autorité divergente |
| B06 | writes + correction + oubli + blocs + reçus après migration | export retour exhaustif, ancienne source byte-restore refusée |
| B07 | restauration historique puis vieux request_id/ETag | nouvelle identité, identity_changed, aucun rejeu |
| B08 | 10 000 éléments, cache complet/incomplet | timings et encodages ; ready≤300 s sur fixture scellée |
| B09 | fenêtre opérateur read_only, relais connecté | mutations refusées avant reçu/journal d'envoi, maintenance_window/not_started ; lectures disponibles |

Toutes les pannes sur magasins jetables ; ENOSPC simulé, jamais remplissage du
disque du Mac. Tests légers de transactions puis jobs lourds séquentiels par
Claude après GO. Réutiliser contrats L3, rétention et règles Host/Origin/token.

Claude prépare un runbook de copies/activation/rollback avec writers arrêtés,
mesures de cut/RPO, fenêtres de backup, conservation chiffrée et scripts clients :
pause des écritures, statut des incertains, redémarrage après changement d'identité.
L9 reste brouillon validé manuellement ; aucune activation SQLite/registre maintenant.

## 6. Amendements R1–R5 acceptés — 2026-10-11

**R1.** `MEMORY_V4_WRITE_MODE=read_only` ou serveur `--read-only`, réservé à
l'opérateur, figé au démarrage ; retour `read_write` par redémarrage contrôlé.
Aucun outil LLM ne change la politique. Toute mutation métier, y compris blocs,
flush et maintenance destructive, est refusée avant réservation/reçu ; résultat
`code=maintenance_window, outcome=not_started`. Préflight du relais : rien envoyé,
aucune nouvelle entrée incertaine, reprise manuelle possible seulement pour une
requête sans incertitude antérieure. Les lectures/status restent disponibles,
sans nouveaux reçus d'accès ni flush des accès à la fermeture. Ce mode est un
gel **sémantique des mutations**, pas un montage filesystem immuable : ouverture
normale, métadonnées d'enrôlement, caches dérivés et coordination WAL peuvent
écrire lors du démarrage. Il ne remplace ni l'arrêt prouvé avant remplacement
du magasin, ni la connexion strictement read-only du CLI de sauvegarde.

**R2.** Health/doctor exposent empreinte disque, espace estimé DB+archive+drill,
espace libre et marge par volume. Seuil conservateur : réserve 5 Go **plus**
espace de travail, et jamais moins de deux fois cet espace ; backup/drill refusés
en dessous, nice15 et swap≤8,5 Go attestés. Incrément1 borné : DB64 MiB,
archive chiffrée68 MiB, manifeste2 MiB, tar décompressé borné, 300 s/30 s BUSY.
Ces caps ne qualifient pas les bases plus grandes et ne constituent pas une
réservation système empêchant un autre projet de remplir le disque.

**R3.** Modèles launchd opt-in : backup quotidien, drill hebdomadaire lundi03:30,
nice15 ; génération sans chargement. Health indique date, durée, archive et
niveau de preuve. Dans l'incrément1, `level=files`, `quarantined=true`,
`reopening_allowed=false` : le test hebdomadaire complet jusqu'à `/ready`
reste à implémenter/qualifier. Ne pas activer ces modèles sur la production JSONL.

**R4.** Destination chiffrée hors disque interne et RPO à décider par Romain
(disque externe/iCloud Drive à qualifier, pas d'upload implicite). Option non
implémentée/configurée dans cet incrément ; aucune garantie sinistre disque.

**R5.** Avant toute ouverture d'un restore historique : rapprochement de tous
les oublis après cut, preuve de la source du registre, liste obligatoire des
`forgets_replayed` (IDs, reçus, séquences, résultat), y compris liste vide
explicitement justifiée. Une liste vide par défaut n'est pas une preuve. Si
l'autorité actuelle/registre est indisponible, quarantaine, jamais réouverture
automatique. Incrément1 : `forget_reconciliation=pending`, lancement de serveur
restauré refusé ; nouvelle identité/événement de récupération et réconciliation
seront livrés ensemble dans l'incrément suivant.

La rotation SQLite, l'attestation du commit source, la matrice complète B04–B08
et la qualification copie/activation restent bloquantes pour S4. Le manifeste
actuel conserve le profil/version du magasin et annonce honnêtement le commit
source non attesté. Voir `SQLITE_S4_INCREMENT1.md` pour les preuves et limites.

**État ultérieur incrément2 :** rotation, réconciliation depuis l'autorité
disponible, identité/reçu de récupération et drill HTTP privé sont implémentés.
Les unités et matrices synthétiques passent ; TCP/10k/copie industrielle restent
à qualifier. `reopening_allowed=false` reste un garde de promotion production,
distinct de `private_reopen_verified`. Les paragraphes incrément1 ci-dessus
décrivent l'état historique, remplacé pour la livraison par
`SQLITE_S4_INCREMENT2.md` ; aucune qualification/activation complète n'est actée.

**Signatures : Claude contresigne avec R1–R5 (revue du 2026-10-11) ; Codex accepte
avec les précisions ci-dessus. Aucun GO de déploiement/activation n'est inféré.**

**État candidat P1 ultérieur :** export rollback de l'autorité SQLite courante
après nouvelles écritures, conservation exhaustive, identité/reçu/source-cut,
rotation interrompue et matrices de crash complétées. Historique L7b inclus
au schéma6 ; archive connue5 vérifiée avant mise à niveau privée. Les sidecars
L7b exportés restent inactifs en JSONL et interdisent un reimport implicite qui
les perdrait. Voir `P1_S4_L7B_INCREMENT_v1.md` ; aucune activation ni qualification
industrielle complète n'est inférée des tests synthétiques.
