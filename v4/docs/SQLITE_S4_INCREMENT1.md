# SQLite S4 — incrément1, candidate à qualifier

2026-10-11. Au-dessus de `aecf638`. Aucun changement de production, aucune
inférence neuronale/API, aucun téléchargement ni publication. Version du package
inchangée (4.0.4) : ce code prépare 4.0.5, ce n'est pas une distribution qualifiée.
JSONL, C4 et auto/MPS/native/batch32 restent les défauts ; registre inactif.

## Livraison et limites

| Livré dans cet incrément | Preuve préparée, non exécutée ici |
|---|---|
| Fenêtre opérateur read_only, figée ; refus maintenance_window/not_started avant reçu et envoi du relais ; lectures maintenues | 18 cas Hash JSONL/SQLite + cas TCP B09, plus régression préflight |
| Connexion read-only vers source SQLite active, WAL/SHM préexistants ; snapshot fixé par transaction puis API backup | B01 : écriture après pin exclue du cut, inventaire destination identique |
| Vérification de schéma/migrations, intégrité SQLite/FK, toutes tables/colonnes/lignes/historiques/blobs/reçus/cache ; archive AES authentifiée, publication exclusive et fsync | Cas fichier/drill chiffré et 8 frontières d'exception B02/B05 ; aucune réussite enregistrée pour ces échecs |
| Drill strict vers destination neuve, tar/extraction bornés, checksum, quarantaine, serveur restauré interdit | Corruption, traversal, doublon, taille, bombe gzip ; source sans WAL/SHM refusée sans réparation |
| Réserve disque/marge health et doctor ; nice15/concurrence1/swap/temps ; modèles launchd opt-in | Pression simulée, lease exclusive, plist hebdomadaire inactif |

Les38 tests S4 et13 tests préflight sont préparés. B09 ajoute1 test TCP.
Le lanceur exige au moins51 tests unitaires /52 avec TCP, zéro échec/erreur/skip.
Ce sont des **effectifs attendus**, pas des tests passés. Les anciennes preuves
`b7bab3f` ne qualifient pas cette source. Le lancement local doit refuser avant
pytest tant que disque<5 Go ou swap inconnu/>8,5 Go.

Preuve locale : `sqlite-s4-increment1-codex-guard-v1/report.json` annonce
`ok=false`, `tests_started=false`, erreur « Swap unavailable; full outside-sandbox
qualification refused ». Espace libre au contrôle suivant : environ5,01 Go,
marge fragile ; aucune attestation swap dans le sandbox. Les deux commandes
Ruff (configuration projet puis E9/F sur tout v3/v4) et AST/syntaxe embarquée
passent ; ce sont des contrôles statiques, pas la qualification des tests.

Cap des fixtures S4 : 16 MiB par cas contrôlés à teardown, suppression du seul
répertoire pytest du cas lorsque call ET teardown passent. Échecs/skips conservés.
Ce contrôle n'est pas une quota disque OS ; le lanceur mesure réellement la
machine chaque seconde et s'arrête en300 s. La sauvegarde candidate est bornée
à DB64 MiB/archive68 MiB, nice15, concurrence1, 300 s et BUSY30 s.

Le mode serveur read_only gèle les mutations métier, **pas** les écritures
d'initialisation/cache/WAL. Le backup CLI n'instancie ni Store ni encodeur source.
Les champs `source_build.commit=null, attested=false` évitent d'inventer une
attestation absente du schéma actuel. L'inventaire reprend le profil/version
enregistrés et inclut chaque table connue ; un schéma futur est refusé.

Le drill fichier peut réussir avec `reopening_allowed=false` et
`forget_reconciliation=pending` : ces succès ne qualifient PAS S4 complet.
Pas de lancement de serveur, de nouvelle identité de restore, de rejeu d'oubli,
de rotation d'archives SQLite ou de copie externe dans cet incrément. Les
exceptions injectées ne prouvent pas SIGKILL/ENOSPC/panne électrique ; cette
matrice et les10 000 éléments restent à exécuter dans l'incrément suivant.

## Qualification Claude, une fois la garde réelle satisfaite

Importer le bundle nommé dans le git privé habituel ; ne pas écraser les dossiers
de refus précédents. Depuis la racine du clone, avec le runtime déjà installé :

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:v4/src:v4 \
  .local-qualification/brief-test-runtime-claude-3c-v1/bin/python -B \
  scripts/qualify_sqlite_s4_increment1.py --tcp \
  --output .local-qualification/sqlite-s4-increment1-claude-v1
```

Si cette étape passe, qualification globale séquentielle, sans modèle téléchargé :

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:v4/src:v4 \
  MEMORY_V4_CL100K_PATH=/Users/romain/.config/memory-os/cl100k_base.tiktoken \
  .local-qualification/brief-test-runtime-claude-3c-v1/bin/python -B \
  scripts/qualify_405_test_isolation.py --suite full \
  --output .local-qualification/405-safety-s4-claude-v1
```

Minimum attendu : 585 v3, 1572 v4 dont23 TCP. Configurations clients comparées
avant/après ; zéro modification exigée. Rapport de S4 : `tests_started`, counts,
empreintes des sources, garde et `s4_activation_qualified=false` même si tests verts.
Aucun lourd job lancé ici. Ne pas contourner la garde ni compresser/supprimer
des preuves/projets d'autrui pour gagner de la place.

## Essai CLI — uniquement sur fixture SQLite jetable après tests verts

Utiliser un magasin Hash SQLite **actif** dans un dossier jetable du clone,
sa propre clé backup0600 existante et des répertoires ops/backups disjoints.
Ne pas utiliser la production ni l'ancienne copie JSONL avec ce CLI SQLite.
Les paramètres suivants sont des chemins de fixture, pas des destinations actives :

```sh
export MEMORY_V4_DIR="$PWD/.local-qualification/s4-cli-fixture/store"
export MEMORY_V4_BACKUP_DIR="$PWD/.local-qualification/s4-cli-fixture/backups"
export MEMORY_V4_BACKUP_KEY="$PWD/.local-qualification/s4-cli-fixture/backup.key"
export MEMORY_V4_OPS_DIR="$PWD/.local-qualification/s4-cli-fixture/ops"
PYTHONPATH=src:v4/src nice -n 15 \
  .local-qualification/brief-test-runtime-claude-3c-v1/bin/python -B \
  -m memory_os_v4.ops.cli backup --backend sqlite --online
```

Après revue de la simulation, même commande avec `--apply`. Drill fichier :
`-m memory_os_v4.ops.cli restore-drill --backend sqlite --apply --no-server`.
Sans `--no-server`, demande d'ouverture refusée. Aucun contrôle launchd implicite.
Ces variables sont celles d'`ops/settings.py` ; ne pas les omettre ni substituer
une variable inconnue qui ferait retomber sur un chemin réel.

## Prochain incrément et travaux parallèles

1. R5 : cut/registre d'oublis qualifié, liste exhaustive de replay et preuve de
   rapprochement ; nouvelle identité/événement de récupération, statut L3/ETags
   anciens. Réouverture privée seulement après ces gates, jamais GO implicite.
2. S4 : crash processus à frontières COMMIT/ACK/cache/archive/log, ENOSPC simulé,
   retour JSONL après écritures et oubli, rotation sûre, attestation source,
   probes/drill privé jusqu'à ready sur10k synthétiques ; ensuite copie et GO.
3. L7b seulement après ce socle, selon T1–T5. Les20 scénarios Claude et les
   brouillons de clients peuvent avancer maintenant ; capacités restent false.

Claude prépare en parallèle le runbook maintenance_window/statut des incertains/
identity_changed, le registre d'oublis post-cut et20 scénarios W01–W07. Romain
décide de l'espace disque, du RPO/destination externe, de l'activation SQLite et
des jobs. Aucun de ces changements opérateur n'est exécuté par cette livraison.
