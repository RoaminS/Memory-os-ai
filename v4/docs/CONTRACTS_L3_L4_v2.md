# L3/L4 — contrat v2, incrément 2 de la candidate 4.0.4

2026-10-11. A1–A5 acceptés, avec la précision opérationnelle A4 ci-dessous, contresignée par Claude le 2026-10-11.
Remplace le contrat proposé v1 pour cette implémentation ; v1 reste une archive.
Pas de nouvelle release, activation, campagne ou changement de production.

## 1. Identité, prévalidation et reçus

Le relais attribue un UUID par appel logique ou conserve le `request_id` explicite
(1–200 caractères). Un nouveau message n'est pas identifié à une ancienne
intention par similarité. Le même texte avec un nouvel ID reste un nouvel appel.

A3 : `raw_args_sha256` = SHA-256 du JSON des arguments effectivement reçus,
`request_id` exclu, clés triées, `ensure_ascii=False`, séparateurs `(',', ':')`,
`allow_nan=False`. Aucune valeur par défaut ni résolution de scope côté relais.
L'opération figure séparément. Le serveur conserve aussi `fingerprint`, SHA-256
canonique historique, autorité pour l'idempotence/conflit. L'alias compact est
normalisé par le serveur uniquement.

Une prévalidation **en lecture seule** appelle directement en amont
`memory_request_status({operation, arguments})`, sans ID au niveau supérieur.
Le serveur valide le modèle, résout le scope, puis retourne `contract=l3-v2`,
`store_instance_id`, `scope` (chaîne JSON canonique opaque pour le relais),
`operation` canonique, `raw_args_sha256`, `replay_permitted=false`.
Aucun reçu ni mutation métier. Les arguments ne sont jamais journalisés.
Les capacités legacy ou ambiguës bloquent la mutation avant envoi.

Le UUID `meta.store_instance_id` est créé sous le verrou du writer à l'ouverture
initiale de cette version et persiste dans les sauvegardes/migrations de méta.
Cette inscription **n'atteste pas rétroactivement** les reçus legacy ; elle
identifie une lignée, pas la fraîcheur d'un snapshot. Une restauration ancienne
conserve éventuellement ce UUID : son absence de reçu ne permet jamais le rejeu.
Les valeurs brutes et l'identité sont inscrites dans le reçu pending/final JSONL
ou dans le reçu final de la transaction SQLite. Les appels Python historiques
sans attestation brute restent consultables comme `unknown/legacy_unattested`.

## 2. Journal local, adoption et rétention

A1 : un journal 0600 par processus, parent 0700,
`$MEMORY_V4_OPS_DIR/relay/<MEMORY_V4_CLIENT_ID>-<uuid>.jsonl`.
Défaut OPS_DIR : `~/.local/state/memory-os`. Le nouveau setup fournit le client ID ;
les configurations existantes restent compatibles avec le libellé `unknown`.
Surcharge facultative `MEMORY_V4_RELAY_JOURNAL_DIR`.

Un `.lock` compagnon est détenu pour toute la vie du writer. L'adoption acquiert
ce verrou exclusivement sans attendre ; un writer actif n'est pas adopté.
Le PID est informatif : `flock`, libéré par le noyau à la mort du processus,
protège aussi contre la réutilisation d'un PID. Symlinks, mauvais propriétaires,
permissions, liens multiples, lignes tronquées/checksums invalides sont refusés.
Les enregistrements sont chaînés et vérifiés. Une chaîne n'est pas une signature
contre un attaquant ayant les droits du même utilisateur.

`prepared` fsync → `sending` fsync **avant** l'appel métier → `completed` fsync
avec hash de réponse, sinon `blocked` ou dernier `sending` conservé. Un échec
fsync après réponse serveur reste incertain ; le journal est empoisonné et bloque
les nouvelles mutations. Un crash en prepared n'autorise pas de rejeu non plus.
Les arguments, résultats, conversations, jetons et secrets ne sont pas conservés.
Un journal valide ne prouve pas que son fichier n'a jamais été supprimé.

L'adopteur ne modifie pas les journaux orphelins. Une résolution obtenue par
statut serveur est enregistrée dans **son propre** journal ; elle masque
l'incertitude correspondante lors du prochain inventaire, sans effacer l'original.
Aucun statut absent, inconnu ou conflit ne résout une incertitude.

A4 : rotation du journal actif à 30 jours ou 10 MiB par défaut ; bornes via
`MEMORY_V4_RELAY_JOURNAL_MAX_DAYS` et `MEMORY_V4_RELAY_JOURNAL_MAX_BYTES`.
Les états non résolus ne sont jamais purgés et peuvent dépasser le plafond.
**Précision contresignée par Claude le 2026-10-11 :** la rétention des journaux clos est un entretien
séparé, sous verrou exclusif, exécuté par l'opérateur ; l'adoption reste réellement
en lecture seule. Commande de simulation puis `--apply` dans OPERATIONS.md.
Son raccordement à un job local appartient à Claude, après qualification/GO ;
cet incrément n'installe aucun job et ne prétend donc pas à une purge automatique
à 30 jours des journaux orphelins. Une résolution expirée peut faire réapparaître
un ancien pending : consulter à nouveau le reçu, jamais rejouer.

A5 : nombre, échantillon borné aux 256 derniers timings par phase et p95 observé
exposés dans le statut local (`fsync_ms`). Ils couvrent append/fsync/rotation,
sans texte utilisateur. Coût total prepared+sending mesuré séparément par le
script de qualification. Le futur p95 ≤250 ms de L9 n'est **pas** qualifié par
ces mesures hash-only ni par les seules latences du journal.

## 3. Consultation et signaux

A2 : sans ID **via le relais**, l'outil liste les appels locaux non résolus,
y compris les orphelins du même endpoint. Les autres relais actifs ne sont pas
inspectés comme des orphelins. Le compteur ne prétend pas couvrir ces processus.
Doctor rapporte nombre d'orphelins incertains, fichiers illisibles, relais actifs,
taille totale et âge. Le relais ajoute au champ `text` du brief un avis concis
lorsqu'il existe une incertitude ou une erreur de journal. L1a devra préserver
cet avis lors de sa future réduction du brief.

Avec ID seul via relais, l'identité provient du journal. Une ambiguïté renvoie
unknown. Un appel direct au serveur doit fournir ID, scope, opération,
raw_args_sha256 et store_instance_id. Le serveur seul ne voit jamais les journaux
locaux : sans ID ni arguments de prévalidation, il expose seulement ses capacités.

| Statut | Preuve / action |
| --- | --- |
| completed | Reçu final durable correspondant ; résultat original, sans nouvel effet. |
| pending | JSONL potentiellement partiel ; réconciliation opérateur. |
| not_found | Pas de reçu présent ; aucune conclusion sur l'histoire de l'appel. |
| unknown | Magasin différent, identité incomplète ou reçu legacy. |
| conflict | Même ID/scope avec empreinte/opération brute incompatible ; pas de résultat divulgué. |

Toujours `replay_permitted=false`. Une différence de représentation brute peut
produire conflict malgré une équivalence canonique : conservateur, aucun second
effet autorisé. Le statut ne crée ni reçu, ni transaction d'écriture. Initialiser
un moteur froid demeure une ouverture normale avec réconciliation de caches :
la lecture sans modification est mesurée **après initialisation** du moteur.
Le jeton commun n'est pas une ACL multi-utilisateur.

## 4. Partie L4 nécessaire et limites

Le propriétaire unique de session/transport est conservé. Fichier de jeton relu
et validé avant chaque opération ; remplacement détecté avant l'envoi suivant,
une seule reconnexion pour rotation répétée, jamais de coupure intentionnelle
pour rejouer une mutation. Fichier absent/invalide : refus explicite.
Liste d'outils et lectures reconnues peuvent être reprises au plus une fois.
Les mutations, même après 401/coupure, ne sont jamais reprises automatiquement.
Le serveur legacy peut encore servir les lectures, mais ses mutations protégées
sont refusées : déployer serveur compatible avant les nouveaux relais.

Nom/version d'initialisation comparés à chaque reconnexion ; changement →
`identity_changed`, nouvelle initialisation cliente après revue opérateur.
L'identité du magasin est aussi vérifiée à chaque prévalidation de mutation.
Le handshake d'une session cliente existante n'est pas réécrit silencieusement.
Un statut local sans ID reste accessible sur un relais déjà démarré pendant une
panne. Au démarrage initial sans serveur, le relais échoue : doctor reste le
canal local de diagnostic. L4 complet doit encore traiter/qualifier toutes les
capacités, les diagnostics persistants de version et les hôtes clients réels.

## 5. Matrice et preuve physique restant à Claude

| ID | Preuve locale | TCP à exécuter / limite |
| --- | --- | --- |
| C01 | processus tué après prepared/sending ; fsync injecté aux 3 phases | fsync réel mesuré sur clone, pas SLA production |
| C02 | coupures avant, pendant, après effet, aucun rejeu | `test_C02_C04_C08_disconnect_and_new_relay_adopts_journal` |
| C03 | deux backends : même ID, conflit, scope distinct, alias | retries HTTP existants |
| C04 | pending JSONL après interruption et réouverture | cas partial du test ci-dessus |
| C05 | rollback SQLite, copie ancienne, legacy, corruption/perms | contrôles magasin copiés ; S4/L6 restent séparés |
| C06 | snapshot ancien avec même ID de lignée → not_found, replay=false | pas de qualification L6 de restauration industrielle revendiquée |
| C07 | rotation/invalidité avant send, tests sécurisés auth existants | 30 cycles restart+rotation, contrôle ≤5 s après /ready ; absent/0644 |
| C08 | annulation, crash, réponse perdue, sending retrouvé | cas committed/partial ci-dessus, nouveau processus relais |
| C09 | legacy et identité/version remplacée | handshake réel des tests relay existants |
| C10 | fsync échoue avant send/après effet, poison bloque | panne disque injectée, pas saturation du disque réel |
| C11 | statuts répétés sans write ; mauvais scope/hash/endpoint | middleware auth HTTP existant (401/Host/Origin) |
| C12 | deux vrais processus, locks, IDs distincts, changement horloge | sérialisation moteur HTTP existante ; timings monotones |

Les matrices unitaires ne remplacent pas la mesure physique TCP et ne qualifient
pas les clients GUI. Commande complète : QUALIFY_404_INCREMENT2.md.

Précision livraison stdio : `recent_completed` (20 identités au maximum) permet
également de retrouver un ID généré si le relais meurt après son fsync completed
mais avant la livraison au client. Ces entrées ne gonflent pas le compteur
incertain. Le statut serveur reste requis pour récupérer la réponse originale.
La mesure fsync ne simule pas une coupure secteur.


## Correctif incrément 2b — découverte locale pendant une panne

La qualification TCP de474b09b a échoué sur2/1248 cas : le SDK reliste les outils
pour valider un résultat. Le handler local de statut fonctionnait, mais cette
redécouverte dépendait du serveur arrêté. La première livraison n'est donc pas
qualifiée pour ce scénario ; son rapport `ok=false` reste conservé.

Le catalogue est désormais préchargé une fois après le handshake amont et reste
figé pour la session cliente. `tools/list` est ensuite entièrement local, y
compris si une opération bloque le propriétaire amont. Le descripteur de
`memory_request_status` appartient toujours au relais ; un outputSchema amont
ne doit pas invalider son inventaire local. Les descripteurs retournés sont des
copies indépendantes. En cas d'échec du premier préchargement : seul le statut
local est annoncé ; aucune capacité distante n'est inventée.

L'attente du préchargement est bornée à5s. Cette borne ne prétend pas annuler une
opération déjà prise par le propriétaire amont ni qualifier toutes les latences
L4. Un nouveau catalogue amont nécessite une nouvelle session cliente. Les
contrôles d'identité/version, d'authentification et de non-rejeu restent actifs
sur les appels ; consulter un catalogue caché n'atteste pas la disponibilité du
serveur. Le démarrage sans handshake initial reste une limite de L4.
