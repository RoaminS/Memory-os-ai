# 4.0.8 RC v2 — capacité S4 et réserve disque

26 octobre 2026. Candidate seulement ; production4.0.7 inchangée. Aucun job ou
plist existant n'est modifié. Le snapshot d'urgence n'est pas déclaré « backup
S4 qualifié » : c'est la coupe de diagnostic fournie par Claude.

## Plafonds explicites et cohérents

`MEMORY_V4_SQLITE_BACKUP_MAX_BYTES` : entier en octets, **4GiB par défaut**
(4294967296), plage autorisée64MiB–16GiB. Valeur vide/invalide/hors plage : refus,
jamais repli silencieux. Un opérateur peut choisir1GiB (1073741824).
Doctor/health publient la marge DB/WAL/SHM au plafond ; avertissement dès80%,
échec de capacité au-delà. Chaque fichier DB/WAL/SHM respecte le plafond DB.

Source de vérité : `ops/sqlite_limits.py`. De DB et du manifeste2MiB dérivent
les bornes tar décompressé (DB+manifest+64KiB), gzip (+1%+64KiB conservateurs),
archive chiffrée (+33octets). Même politique pour lecture, progression de
snapshot, compression, ciphertext, déchiffrement, décompression, extraction et
drill. Limites inclusives, dépassement refusé. Pas de préallocation de4GiB.

Le plafond plus haut seul aurait amplifié les copies en RAM. S4 construit et
lit désormais par flux (blocs≤1MiB) ; spool privé déchiffré puis décompressé sur
disque, supprimé automatiquement. **Authentification GCM complète AVANT analyse
ou extraction**. Format inchangé `MOSB1 | nonce12 | AES-GCM(tar.gz,AAD=MOSB1) | tag16`,
clé32octets et publication/fsync/lecture de contrôle conservés. Utilisation de
Cipher/AES/GCM de cryptography, aucune nouvelle construction cryptographique.
Tar : deux fichiers réguliers connus, tailles bornées, refus liens/chemins/PAX/
GNU extensions/duplicats/troncatures ; aucun payload d'extension alloué par tarfile.
Les BLOB legacy sont également hachés en flux dans l'inventaire : même checksum
logique qu'avant, pas une matérialisation du fichier legacy entier en Python.

Interop testée dans les DEUX sens avec le chiffrement historique sur petit
jeu. Les outils4.0.7 gardent leur cap64MiB : ils ne peuvent pas restaurer les
nouvelles grandes archives, bien que le format soit identique. Utiliser le
runtime S4 qualifié4.0.8 pour ces opérations, pas un outil4.0.7 modifié à la main.
4GiB est une capacité, pas une qualification de performance jusqu'à4GiB : timeout
300s S4, garde mémoire/swap, champs JSON par ligne et ressources restent des
contraintes. Qualification de croissance à renouveler avant d'approcher la limite.

## Réserve : pas d'abaissement automatique

Défaut inchangé **`legacy5gb`**, réserve5Go. La meilleure estimation du travail
par flux est maintenant6F+32MiB (F=DB+WAL+SHM, minimum1MiB). Le besoin sur chaque
volume est réserve+travail ; estimation prudente même si les volumes diffèrent.
Le garde est vérifié pendant l'opération ; aucune rotation avant snapshot publié
et vérifié, aucun effacement de l'historique ni du cache pour passer un seuil.

Option **EXPLICITE** : `MEMORY_V4_SQLITE_STORAGE_POLICY=adaptive_v1` (SQLite seul).
Réserve=max(3Go,2F), besoin=réserve+6F+32MiB. **Plancher3Go non négociable** ;
policy inconnue refusée. L'override `MEMORY_V4_MIN_FREE_BYTES` du harnais ne
modifie pas cette réserve S4. Les templates setup S4 recopient uniquement les
options explicites après validation, avant toute création de token.

Pour F≈78Mo : legacy≈5,50Go nécessaires ; adaptive≈3,50Go. La copie installée
conserve4F de copies plus256MiB : adaptive≈4,08Go nécessaires au départ ; legacy
conserve≥max(7Go,5Go+20F). Ce sont des budgets, pas des garanties face aux autres
processus. Le swap≤8,5Go et nice15 restent obligatoires. Les erreurs/copies
incomplètes restent non qualifiées ; relance dans un dossier neuf seulement.

**Décision proposée à Romain** : accepter adaptive_v1 pour les jobs SQLite
backup/health/drill après qualification privée, ou conserver5Go et libérer de
la place/déporter le stockage. Le défaut reste5Go tant que cette option n'est
pas explicitement appliquée. Réduire la réserve laisse2Go de moins pour les
écritures concurrentes/swap : le contrôle ne réserve pas physiquement cet espace
et ne peut empêcher un autre processus de le consommer. Gardes et alertes restent
indispensables. Ne pas déclarer l'incident résolu avant un nouveau backup ET drill
qualifiés, les jobs nocturnes corrigés sous GO et le résultat du lendemain vérifié.

Changer seulement l'environnement des jobs4.0.7 ne corrige PAS MAX_DB : ce code
ne lit pas le nouveau paramètre. Un éventuel job CLI4.0.8 en ligne contre le
serveur4.0.7 peut être qualifié séparément sans migration/écriture SQL de source,
mais nécessite les artefacts installés qualifiés et le GO de Romain ; aucun
remplacement de job ni accès production n'est exécuté par Codex ici.
