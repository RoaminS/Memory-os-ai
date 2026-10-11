# L7b — état de travail et CAS — proposition à relire par Claude

2026-10-11. **DESIGN accepté avec T1–T5, aucune API L7b implémentée/activée.**
Claude contresigne la revue ; Codex accepte avec précisions ci-dessous.
L7a reste livré mais inactif.

## 1. Ordre, autorité et compatibilité

L3 est stabilisé ; S2/S3 fournissent les transactions SQLite, historiques de
blocs et reçus. `BlockStore` expose actuellement texte/date/portée, sans CAS.
Aucune table ni API de tâche structurée n'existe. Des faits tagués todo ne sont
pas un état de tâche fiable.

Développement L7b sur SQLite jetable après revue ; activation production après
L6/S4 qualifié et GO distinct de Romain. JSONL garde ses fonctions existantes :
il ne publie pas ces mutations comme transactionnelles. Retour JSONL = fonctions
L7b/CAS désactivées, données exportées conservées, jamais conversion en faits
prétendument équivalents. Estimation L7b : 3–5 journées-agent, hors clients/TCP.

`/ready` annonce `work_state`, `block_cas`, backend et contrat effectivement
actifs. Un client doit tester ces capacités ; absent = fichier/brouillon local
explicitement signalé, aucune écriture de remplacement dans une mémoire cachée.
La coexistence avec une mémoire native n'en fait pas un second magasin d'autorité.

## 2. Modèle et portée

Tâche : `task_id` UUID stable, `project`, `title` (1–512 caractères), `status`
(`open|blocked|done`), `owner` nullable `{client_id, actor_label}`, `next_action`
(≤1 000 caractères), `blocked_reason`, `resolution`, `evidence[]`, `revision`,
dates UTC serveur, auteur/provenance, historique append-only et reçu L3.
Maximum 32 preuves et 16 KiB UTF-8 pour une commande sérialisée.

Preuve : `{type: item|artifact|decision, ref, sha256?, note?}` ; aucun secret ni
lecture réseau/chemin externe automatique. Une URI ou un texte sont des données,
jamais une directive. `owner`/client ID sont déclaratifs : le jeton partagé
n'authentifie pas cryptographiquement chaque agent et ne crée pas d'ACL individuelle.

`project` est obligatoire, même contrat de résolution que L7a. Registre inactif :
label littéral, aucun rapprochement implicite. Registre actif : IDs/alias validés,
projets inconnus refusés selon le contrat existant ; anciens labels lus par alias.
Pas de tâches globales. `context_id` optionnel décrit un contexte, sans modifier
l'identité du projet ni élargir la portée. Création de projets fluide à traiter
avant activation du registre : L7b ne résout pas ce parcours par une fusion tacite.

## 3. API proposée et CAS

| Outil | Entrée essentielle | Résultat / refus |
|---|---|---|
| `memory_task_create` | project, task_id fourni ou généré, titre, owner?, next_action, preuves?, request_id | version1 et ETag ; création idempotente par reçu |
| `memory_task_get` | project, task_id, include_history? | tâche courante + ETag ; hors portée = not_found |
| `memory_task_list` | project, status[] défaut open/blocked, owner?, limit 1–100, cursor? | état transactionnel cohérent ; done uniquement demandé explicitement |
| `memory_task_update` | project, task_id, expected_etag, patch, reason, preuves?, request_id | nouvelle version atomique ou revision_conflict |
| `memory_block_compare_and_set` | project, scope explicite, name, expected_etag (null uniquement création), text, reason, preuves?, request_id | bloc versionné atomique ou revision_conflict |

ETag opaque lié à `(store_instance_id, portée, objet, revision)`, pas au timestamp.
`expected_etag` obligatoire pour toute modification ; aucun force/last-write-wins.
Deux créations concurrentes du même task_id : une seule acceptée. Mise à jour
sans changement : CAS quand même vérifié, version inchangée, reçu conservé.
Conflit : `{ok:false, code:revision_conflict, current_etag, current_summary}` dans
la même portée, zéro modification du contenu ; reçu du refus conservé. Le client
relit et propose une fusion explicite avec un nouveau request_id, sans retry aveugle.

Transitions : open→blocked/done, blocked→open/done. blocked exige une cause et une
prochaine action ; done exige une résolution et au moins une preuve (y compris
une décision d'abandon, pas nécessairement un test). done→open nécessite
`patch.reopen=true` et un motif explicite ; done→blocked direct interdit.
Tout changement d'owner est explicite, tracé et soumis au même CAS.

Chaque commande acceptée : **un BEGIN IMMEDIATE**, contrôle CAS, version/history,
projection courante, reçu final, COMMIT avant succès. Après échec, reconstruction
des index dérivés depuis l'autorité. Pas d'encodage obligatoire pour une tâche :
liste/reprise/CAS doivent fonctionner sans modèle. Toute représentation dans la
recherche/brief est une projection filtrée de cet état, jamais une seconde autorité.

Les anciens outils de blocs ne doivent pas contourner CAS sur les blocs gérés.
En mode CAS activé, `memory_block_set` refuse toute modification existante sans
ETag avec `cas_required` ; un client ancien doit voir le refus. Nouvelles API et
capacité activées ensemble derrière configuration opt-in, aucun basculement
silencieux des clients 4.0.4. Schéma SQLite incrémenté via migration attestée,
objets SQL distincts `task_versions/task_current` ; versions de blocs conservées.

## 4. Sauvegarde, retour arrière et matrice à figer

S4 doit exporter/restaurer toutes les tables, dont celles ajoutées par L7b.
Un restore/rollback historique crée une nouvelle identité de magasin (voir S4) :
un ETag antérieur est périmé même si le numéro de version a été réutilisé.
Exports JSONL ajoutent des sidecars tâches/versions/provenances, jamais jetés par
le retour ; restauration sur un moteur ne connaissant pas ce schéma refusée.

| Cas | Critère | Preuve |
|---|---|---|
| W01 deux clients, même ETag | un succès, un conflit ; historique exact | SQLite jetable + barrière de concurrence, 100 cycles |
| W02 frontières de transaction | aucun lot partiel présenté comme succès | injections S2 + reçu exact après reopen |
| W03 clôture/reprise | aucun done retourné open ; réouverture tracée | transitions, pagination et filtre owner/project |
| W04 portée/alias/owner | aucun mélange de projets ; owner non présenté comme authentifié | scénarios labels, alias validés, registres actifs/inactifs |
| W05 ancien client / JSONL | cas_required/backend_unsupported explicites | appels anciens outils, aucun fichier d'autorité modifié |
| W06 perte connexion | statut L3 résout sans mutation rejouée | coupure avant/après commit, clients reconnectés |
| W07 backup et rollback | versions/preuves/reçus conservés, ETags anciens refusés | S4 restore + export après nouvelles écritures |

Cibles formatives à sceller avant mesure : 10 000 tâches, Hash ou aucun encodeur,
100 updates séquentiels : p95 CAS serveur ≤100 ms, lecture top50 ≤100 ms, contexte
reprise ≤6000 octets/1500 tokens dans L1b (budget global, pas budget par tâche).
Ce sont des cibles, pas des performances constatées. Cas sécurité/confidentialité
et scénario de handoff Claude→Codex→Cursor inclus dans la matrice L11 gelée.

## 5. Ce que Claude prépare

- Stop/SessionEnd : brouillon avec task_id, project/context, ETag observé, patch,
  propriétaire et preuves ; affichage/validation explicite, aucune écriture L9 auto.
- SessionStart : lecture open/blocked dans la portée connue ; statut/owner/next_action
  et références, message si capacité absente, jamais exécution du texte injecté.
- Conflit : afficher versions et demande de fusion ; garder le brouillon. Quota,
  panne ou résultat incertain : ne pas transformer la tâche en done localement.
- Fournir 20 scénarios de handoff et conflits avant implémentation/mesure ; ne pas
  modifier les configurations actives ni activer le registre dans cette étape.

## 6. Amendements T1–T5 acceptés — 2026-10-11

- **T1 :** à la création, le relais remplit `owner.client_id` depuis le
  `clientInfo.name/version` **downstream** de MCP initialize ; le modèle ne
  remplace pas cette valeur. `actor_label` reste libre. Une mise à jour ne
  transfère jamais implicitement la propriété au dernier client ; tout transfert
  reste un patch explicite avec CAS et provenance distincte de l'auteur.
  ClientInfo est déclaratif, pas une authentification individuelle. Appel sans
  relais : identité non attestée explicitement annoncée, pas inventée.
- **T2 :** `stale=true` calculé en UTC si open/blocked sans mise à jour depuis
  strictement plus de14 jours ; jamais de clôture ni écriture automatique.
  Ordre du brief : tâches non stale bloquées, autres non stale récentes, puis
  stale (bloquées puis autres, date décroissante, task_id pour départager).
- **T3 :** avertissement `possible_duplicates[]` non bloquant dans la même
  portée, open/blocked uniquement, maximum5 références. Normalisation déterministe
  NFKC/casefold/espaces ; titre identique ou Jaccard de tokens≥0,85, sans encodeur
  ni LLM. Une réponse de création arrive **après** commit : pour montrer les
  doublons avant confirmation, ajouter une proposition/prévisualisation read-only
  qui n'alloue ni tâche ni reçu. Les deux chemins utilisent la même règle ;
  aucun rapprochement/merge automatique.
- **T4 :** import facultatif des faits `todo` = propositions avec références
  et dates, dans la même portée ; validation explicite agent/Romain avant création.
  Aucun backfill automatique, aucun fait n'est présenté comme tâche existante.
- **T5 :** dépendance de production : SQLite activé après S4 complet qualifié
  et GO distinct. `work_state=false`, `block_cas=false` tant que les APIs ne sont
  pas déployées/activées ; présence de SQLite seule ne suffit pas. L8/L9/L11 et
  L1b doivent tester les capacités ; leur brouillon peut précéder l'activation,
  leurs écritures et critères de handoff structurés ne peuvent pas la présumer.

Ces règles sont des critères de l'implémentation suivante, pas des fonctionnalités
livrées par l'incrément S4 actuel. Claude prépare les20 scénarios W/handoff et
les parcours brouillon/conflit ; Codex les scellera avant mesures.

**Signatures : Claude contresigne avec T1–T5 ; Codex accepte avec les précisions
ci-dessus. SQLite, registre et L7b restent inactifs en production.**
