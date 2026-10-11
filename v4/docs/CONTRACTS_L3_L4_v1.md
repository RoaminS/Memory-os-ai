# L3/L4 — contrat v1 proposé pour gel avant implémentation

**État : spécification et schémas seulement.** Cet incrément n'enregistre pas
`memory_request_status`, n'ajoute pas encore de journal au relais et ne prétend
pas qualifier une nouvelle reconnexion. Claude doit accepter ce contrat avant
le prochain incrément L3/L4. La fixation du protocole ne vaut pas GO production.

Schémas JSON Schema 2020-12 : `contracts/l3-l4-v1.schema.json`, quatre définitions
d'entrée/sortie testées localement. Aucun accès réseau nécessaire pour valider.

## 1. Identité d'une mutation

Identifiant aléatoire durable par **appel logique**, distinct de l'ID de transport
MCP. Le relais préserve un `request_id` explicite valide ; sinon il en attribue un
avant l'envoi et l'inscrit sur disque avec scope, opération, empreinte et ID de
magasin. Un nouveau message de l'agent peut constituer un nouvel appel logique :
le relais ne déduit jamais une identité d'intention d'une ressemblance de texte.

L'empreinte v1 suit le serveur actuel : `model_dump(mode="json")`, valeurs par
défaut explicitées, `request_id` retiré, scope concret résolu par `_request_scope`,
`memory_compact` normalisé en `memory_consolidate`, puis SHA-256 du JSON
`{"operation": operation, "payload": payload}` trié, `ensure_ascii=False`,
séparateurs `(',', ':')`, `allow_nan=False`. Aucun hash du JSON brut du client.
Le scope global et `project='*'` restent distincts. Un ID réutilisé avec autre
charge/scope ne doit pas rendre le résultat d'une autre opération.

Les schémas et valeurs par défaut utilisés par le client sont liés à la version
du serveur ; une résolution ambiguë bloque avant envoi. L3 doit exposer un
`store_instance_id` stable, distinct d'un PID ou d'une version de package. Un
magasin legacy non attesté ou un magasin remplacé produit une incertitude, pas
un « jamais exécuté ». Les règles backup/restauration doivent préserver cette
distinction et empêcher une ancienne copie de se présenter comme une preuve
de non-exécution d'écritures postérieures à son snapshot.

## 2. Journal client

Un seul writer par journal ; fichier privé 0600, parent privé, écritures atomiques
et durables. Aucun bearer token, query, conversation, réponse ou charge complète
dans le journal par défaut : seulement IDs, routage logique et empreintes.
Les cas non résolus ne sont jamais purgés. Les reçus explicites serveur restent
conservés ; la rétention des clients est une décision opérateur documentée.

Transitions : `prepared` (ID durable) → `sending` **durable avant l'appel réseau**
→ `completed` avec hash de réponse, ou `blocked`. Après crash en `sending`,
l'issue est incertaine, même si les octets n'ont finalement jamais été envoyés.
Un `prepared` n'atteste un non-envoi que si l'intégrité et l'exclusivité du journal
sont démontrées. Une erreur fsync/rename ne constitue pas une preuve d'absence.
La reprise ne rejoue pas automatiquement une mutation ; consulter le statut.

Une écriture peut avoir réussi côté serveur malgré l'échec d'enregistrement de
sa réponse côté client. Afficher ID, scope et incertitude ; ne pas fabriquer
un succès durable local ni un nouvel ID pour contourner le blocage.

## 3. Lecture de statut

`memory_request_status` sera authentifié, en lecture seule, sans création de
reçu, d'item ou de transaction d'écriture. Son entrée lie ID, scope explicite,
opération canonique, empreinte attendue et ID de magasin. Le jeton partagé actuel
n'établit pas une isolation par client ; l'API ne promet pas un ACL absent.

| État | Signification et action |
| --- | --- |
| `completed` | Reçu final durable correspondant exactement à l'entrée ; retourner le résultat original, sans réexécuter. |
| `pending` | Tentative connue sans reçu final : peut être partielle sur JSONL ; diagnostic/réconciliation opérateur. |
| `not_found` | Aucun reçu correspondant dans le magasin consulté ; **jamais une preuve de non-exécution** ni permission de replay. |
| `unknown` | Journal indisponible, magasin différent ou legacy non attesté ; conserver le blocage. |
| `conflict` | Empreinte incompatible avec l'ID lié à ce scope ; aucun résultat d'une autre charge divulgué. |

Toutes les réponses portent `replay_permitted=false`. L'absence de reçu après
rollback SQLite ne prouve pas à elle seule l'histoire d'un client ou d'une
restauration. Les erreurs avant tout dispatch identifiées par le relais portent
`not_sent` **dans le diagnostic client**, pas comme conclusion du statut serveur.
`maintenance_denied/outcome=not_started` de L2 concerne seulement l'appel refusé
actuel et ne résout pas une tentative ancienne portant le même ID.

## 4. Relais, authentification et version

Conserver le propriétaire unique des sessions/transports du relais empaqueté.
Relire le fichier de jeton à la reconnexion ; détecter son remplacement atomique
sans exposer son contenu, respecter le contrôle de permissions, refuser un fichier
manquant/invalide. Ne pas couper une mutation en cours pour relancer son envoi.
Une réponse 401 après un envoi ne justifie pas le replay automatique de la mutation.

Une opération de contrôle explicitement en lecture seule peut faire au plus une
reconnexion/relecture ; les mutations envoyées ne sont pas rejouées. L'absence
de serveur est annoncée et bornée, sans boucle infinie ni succès vide.

Le handshake initial transmet `init.serverInfo.version` ; le diagnostic distingue
version relais, version annoncée au client et version actuellement observée en
amont. Une reconnexion ne réécrit pas rétroactivement un handshake MCP déjà fait.
Si l'identité/contrat change, signaler `identity_changed` et exiger une nouvelle
initialisation appropriée plutôt que mentir sur une ancienne annonce.

Seuil : trente cycles restart/rotation sur même contrat serveur ; **prochaine
opération de contrôle terminée ≤5 s après serveur prêt et jeton valide accepté**.
Mesurer aussi le démarrage séparément ; ne pas compter les 30 s de chargement
du moteur dans cette promesse. Une connexion cliente définitivement fermée par
son application peut nécessiter une réinitialisation : doctor doit l'expliquer.
Ce seuil n'est pas encore vérifié par le présent incrément.

## 5. Matrice de tests à réaliser en L3/L4

| ID | Injection / variation | Preuve exigée | Exécutant |
| --- | --- | --- | --- |
| C01 | Crash avant/après fsync de `prepared` et `sending` | ID stable ou refus avant envoi ; aucune certitude inventée | Codex, magasins/journaux jetables |
| C02 | Coupure avant acceptation serveur, pendant action, après commit avant réponse | Aucun rejeu de mutation ; statut exact ou incertain | Codex unitaire + Claude TCP |
| C03 | Même ID/même charge, charge différente, scope différent, alias compact | Reçu conforme ; conflit isolé ; aucun double effet | Codex JSONL et SQLite |
| C04 | Action JSONL partielle puis restart | `pending` reste bloqué ; aucune annonce de rollback total | Codex + Claude TCP |
| C05 | Rollback SQLite, journal absent/corrompu, copie ancienne, autre magasin | `not_found`/`unknown`, jamais permission automatique de replay | Codex |
| C06 | Restauration après writes récents et journal client conservé | Pas d'ancienne copie interprétée comme non-exécution | Codex avec L6 |
| C07 | Jeton remplacé 0600, invalide, absent, mauvaise permission | Reconnexion bornée ou erreur explicite ; secret jamais loggé | Claude TCP, 30 cycles |
| C08 | Restart durant mutation, client annulé, transport terminé | Issue incertaine liée à l'ID ; zéro nouvel envoi automatique | Claude TCP |
| C09 | Serveur/relais de versions différentes, serveur legacy sans statut | Diagnostic explicite ; aucune capacité inventée | Codex + Claude TCP |
| C10 | Journal client non inscriptible/disque plein simulé | Avant envoi : refus ; après succès serveur : incertitude traçable | Codex |
| C11 | Statut répété, mauvais scope/empreinte, token invalide | Lecture sans mutation, pas de résultat d'une autre requête | Codex + Claude TCP |
| C12 | Deux processus/clients concurrents et clock changes | Journal sérialisé ; délais monotones ; indépendance des IDs | Codex + Claude TCP |

Les tests de cet incrément vérifient **les schémas** et L2/L13a, pas ces scénarios
L3/L4 encore à implémenter. Pas de mention « TCP qualifié » tant que Claude n'a
pas fourni les preuves effectives.

## 6. Travail parallèle demandé à Claude

1. Aligner AGENTS.md et copies clientes sur L2 sans autoriser la maintenance.
2. Relire C01–C12, le scope canonique et les journaux ; confirmer le contrat avant L3.
3. Préparer Q0 : injecter seulement `text`, signaler une indisponibilité, mais ne
   pas traiter des blocs vides comme une absence de tout contexte. Un fallback
   global peut mélanger des projets : opt-in explicite, provenance/portée visible,
   exclusion des sources non autorisées ; sinon demander une identité valide.
4. Figer la baseline L11 avant observation : 30 reprises, dix par client
   repreneur, ≥3 projets, rubric des contraintes critiques, temps, contexte,
   réexplication, source réellement utilisée et échecs conservés. Aucun réglage
   sur les résultats ; distinguer mesures natives et mémoire externe.
5. Préparer les tests physiques sans changer le serveur local ni reprendre L14.
