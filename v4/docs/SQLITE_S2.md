# ADR-001 S2 — transactions complètes du moteur SQLite

Date : 2026-10-09. Auteur : Codex. Revue et qualification cible : Claude.
Ce lot est expérimental derrière **le drapeau existant**
`MEMORY_V4_STORE_BACKEND=sqlite_s1`. JSONL reste le défaut. Aucune bascule de
production, migration JSONL ni publication n'est exécutée ou autorisée par ce lot.

## Contrat effectivement livré

| Critère | Implémentation et preuve |
| --- | --- |
| Une mutation moteur = un `BEGIN IMMEDIATE`, un reçu final, un `COMMIT` avant retour | Décorateur des ajouts unitaires/bulk/faits/documents, corrections explicites/identitaires dans `add_facts`, oubli, consolidation, import v3 et flush ; `execute_request` englobe le gestionnaire MCP entier, y compris ses appels directs aux blocs/Store. Trace SQL du lot composite et des chemins document/import/consolidation : exactement un BEGIN/COMMIT. |
| Aucun succès pour un lot partiel | Toute exception annule le lot ; une exception d'une mutation enfant, même interceptée par le gestionnaire, condamne la transaction englobante. Test du véritable `memory_chat_save` : épisodes + premier fait annulés quand la correction suivante est invalide ; `dispatch` rend `ok=false`. |
| Blocs et reçus partagent l'autorité SQLite | `block_versions` append-only, empreinte SHA-256, clé `(project-or-null, name)` canonique ; dernière version/historique matérialisés en mémoire. `request_receipts` append-only avec séquence globale, scope, request_id, opération, empreinte du payload, résultat sérialisé et horodatage. Les déclencheurs interdisent UPDATE/DELETE ; tests d'intégrité et d'immuabilité. |
| Acquittement durable et rejeu explicite | Le reçu est la dernière écriture métier avant COMMIT ; même `(scope, request_id)` et empreinte = copie du résultat, aucune nouvelle écriture. Empreinte différente = conflit. Aucun pending SQLite ne survit à un rollback. Tests de rejeu avant/après redémarrage. |
| Résultat incertain sans duplication automatique | Une exception pendant/après COMMIT ou une récupération SQL impossible désactive les opérations et `/ready` sur la connexion. Fermer puis rouvrir ; le reçu complet indique le résultat validé. Test réel d'une connexion qui exécute COMMIT puis lève une erreur ; aucun nouvel appel admis avant réouverture. |
| Appels sans request_id | Chaque mutation englobante reçoit un identifiant UUID interne et un reçu complet. Cela garantit l'atomicité, **pas** le rejeu sûr d'une requête réseau sans identifiant connu du client. |
| Index dérivés reconstructibles | SQLite est suffisant pour reconstruire vecteurs/chunks, BM25, entités, facettes et caches du retriever. Aucun fichier d'index n'est écrit par le moteur SQLite. Au démarrage, tous les caches sur disque sont ignorés ; après rollback, rechargement des sources puis reconstruction des index et de leurs références. Une reconstruction impossible désactive le moteur et `/ready`. Tests de caches corrompus et de reconstruction impossible. |
| Compatibilité sous drapeau | Les tests JSONL existants restent verts. Le nom `sqlite_s1` est conservé pour éviter un changement de configuration implicite. Schéma S1 pur → S2 par DDL transactionnel, checksum S1 vérifié avant upgrade, historique/metadata conservés. Tout journal JSONL blocs/reçus non vide exige une migration hors ligne et est refusé avant modification. |

La frontière d'acquittement publique est **MemoryV4/execute_request**, pas un
appel direct à `engine.store.append()`, une modification d'un objet `MemoryItem`
déjà retourné ni une écriture de fichier. Les appels Store internes rejoignent
la transaction via savepoints ; Store conserve son API de bas niveau S1.
Les références Python antérieures à un rollback doivent être relues.

Les lectures Python directes peuvent encore accumuler des compteurs d'accès en
mémoire, comme en JSONL : ces statistiques non flushées ne sont pas des mutations
métier acquittées. Via MCP, même une lecture exécutée par `execute_request` est
englobée avec ses statistiques et son reçu. Un appel enfant ne constitue jamais
un acquittement indépendant de son gestionnaire englobant.

## Statut de requête après incident

Après avoir fermé le moteur incertain et acquis le verrou avec un **nouveau**
moteur sur un magasin de qualification, consulter
`engine.requests.records.get((scope, request_id))` (API Python interne,
**aucun nouveau endpoint MCP** livré ici).

- Reçu `state=complete` : effets et réponse validés ensemble ; redemander le
  même identifiant/opération/payload renvoie la réponse mémorisée.
- Aucun reçu : aucune partie de cette requête S2 n'a été validée ; même
  identifiant/payload peut être réessayé explicitement après réouverture.
- Intégrité défaillante : ne pas conclure « absent donc annulé » ; préserver et
  inspecter/restaurer le magasin. Les pendings legacy JSONL ne relèvent pas de ce
  raisonnement : leur réconciliation sera traitée en S3.

Cette inspection n'effectue aucun rejeu d'appel d'outil aval par le relais.

## Matrice de panne reproductible

`test_sqlite_engine.py` utilise exclusivement `HashEmbedder(dim=16)`, sans modèle
neural, téléchargement ni serveur TCP. Les sous-processus sont **séquentiels**.
Un magasin source jetable fermé est copié pour chaque cas ; aucun fichier de
production n'est lu ou modifié. Le témoin d'acquittement est un journal distinct
du SQLite, écrit après retour puis fsyncé.

Le lot composite comprend : deux épisodes, deux faits dont une correction
invalidant le fait précédent, oubli d'un épisode, append d'un bloc projet,
bloc global, compteur d'accès, metadata et reçu. Toutes les écritures DML
passent par `SQLiteStore.write`, instrumenté avant/après chaque écriture ;
BEGIN/COMMIT sont également instrumentés. Les savepoints ne constituent pas
des validations indépendantes ; leurs erreurs SQL sont aussi testées en S1.

Pour les **42 frontières** de ce lot :

1. Exception injectée : avant COMMIT, comparaison exhaustive des lignes de cinq
   tables avec l'état initial, index à nouveau prêts, aucun reçu ni succès du lot.
   Après COMMIT, réponse incertaine et moteur bloqué ; après réouverture, lot
   complet avec reçu, jamais moitié de lot.
2. SIGKILL : 42 processus distincts, puis un 43e arrêté **après** retour et
   fsync d'un nouvel acquittement. À chaque réouverture, les acquittements
   antérieurs, l'historique et les blocs sont conservés ; avant COMMIT, snapshot
   logique initial exact ; après COMMIT/acquittement, effets complets et rejeu
   sans doublon. Aucun témoin de succès pour les interruptions avant retour.

Ces preuves qualifient la panne de **processus** et des exceptions d'écriture
simulées. Elles ne qualifient pas une coupure électrique, des erreurs matérielles,
un vrai disque plein, la robustesse VFS ni toute la porte industrielle G1a.

## Commandes de revue et limites de livraison

**Ne pas intégrer ce bundle dans le checkout gelé par run-dev avant la fin de
la campagne.** Le lot a été développé dans `.local-qualification/store-s2-work`,
avec metadata Git distinctes, pour préserver les empreintes du code actif.
Utiliser un checkout séparé ou attendre la fin pour la revue et l'intégration.

Depuis le checkout du bundle, sur le runtime cible (un vieux SQLite donne des
SKIP et ne constitue pas une qualification) :

```sh
PYTHONDONTWRITEBYTECODE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
PYTHONPATH=v4/src /Users/romain/Memory-os-ai/.venv/bin/python -m pytest \
  -q -s -p no:cacheprovider --basetemp=.local-qualification/store-s2-tests \
  v4/tests/test_sqlite_store.py v4/tests/test_sqlite_engine.py \
  v4/tests/test_writer_idempotency.py v4/tests/test_readiness.py \
  v4/tests/test_core_blocks.py v4/tests/test_core_memory.py \
  v4/tests/test_scope_and_fact_updates.py v4/tests/test_core_migrate.py \
  v4/tests/test_server_handlers.py v4/tests/test_incident_002_dedup.py \
  v4/tests/test_server_project_scope.py
ruff check --no-cache --config pyproject.toml src tests v4/src v4/tests v4/bench scripts --select E9,F
ruff check --no-cache --config pyproject.toml src tests scripts
```

SQLite ≥ 3.51.3, WAL/FULL/fullfsync/checkpoint_fullfsync et FK restent requis ;
répertoire synchronisé à l'initialisation, base neuve 0600, écrivain unique.
Les tableaux de checksums attestent les migrations déclarées, pas un DDL
inviolable contre un attaquant local. Les historiques et reçus croissent sans
purge dans ce lot.

**Limites** : encodage et mises à jour des index ont lieu pendant le verrou et
la transaction ; reconstruction complète au démarrage SQLite. Le calcul hors
transaction/revalidation et la performance avec Granite restent à qualifier.
La sauvegarde admin existante refuse SQLite ; S4 doit employer `Connection.backup()`
avec restauration qualifiée. Ne jamais copier seulement une DB active sans WAL.

**S3 reste à livrer** : import/export JSONL strict hors ligne et réversible,
manifestes, champs inconnus, traces des réparations A2, reçus legacy/pending,
comparaison exhaustive des sources et retour arrière après nouvelles écritures.
L'upgrade S1→S2 n'est pas une migration JSONL→SQLite ; l'ancien exécutable S1
refusera un schéma S2, donc conserver une sauvegarde fermée S1 avant upgrade.
Aucun downgrade de DB implicite. Aucun GO de production n'est demandé avant
qualification S3–S5 et revue adverse de Claude.
