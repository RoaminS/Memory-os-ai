# Revue du hook SessionStart v4 candidat — NON VALIDÉ pour activation

14 octobre 2026. Lecture seule des fichiers de Claude. Les 18 assertions originales sont reproduites avec auth neutralisée et temporaires privés : 18 PASS, 0 FAIL. Elles remplacent `fetch_changes` par un FakeServer ; elles ne testent pas sa vraie pagination.

Trois sondes supplémentaires sont conservées dans `release-407-rc1/hook-review/` ; aucun appel au serveur réel, aucun accès token/prod. Cette revue concerne le hook, distinct de la RC moteur.

## H1 — Rattrapage initial incomplet présenté comme des changements depuis la session suivante (bloquant B1)

`fetch_changes` fait jusqu’à 20 pages lors de la première activation, puis persiste le curseur même si `has_more=true`. La session suivante considère `cursor!=None` comme une baseline achevée et présente la suite des anciens événements comme « depuis la dernière session ». Reproduit avec la véritable fonction et un SDK mocké : 20 pages initiales, curseur-20 ; quatre autres pages historiques détaillées à la session suivante.

Correction : persister aussi un état `catchup_pending` et rester en mode rattrapage tant que `has_more=true`. Annoncer clairement le rattrapage incomplet ; ne revendiquer « depuis la dernière session » qu’après baseline complète. Conserver le budget et les cursors, sans boucle illimitée. Tester plus de 20 pages, reprise, timeout et changement de génération.

## H2 — Déduplication de tâches par sous-chaîne de titre (bloquant A8)

`task_already_shown` retourne vrai si le titre apparaît dans un fait ordinaire. Reproduction : « Review deployment » est supprimé à cause du fait « Review deployment was discussed yesterday ». Deux tâches homonymes ou partageant les 120 premiers caractères peuvent également être masquées.

Correction : comparer les IDs de tâches réellement rendues dans le brief, pas le titre. Un ID présent seulement comme référence ne prouve pas que title/next_action ont été montrés. Conserver les tâches homonymes distinctes ; ajouter un contrôle fait contenant le titre et un contrôle référence-only.

## H3 — Le hook ne négocie pas compact_v2 et ne déclare pas le client

`fetch_brief` envoie seulement project/max_tokens. Il conserve donc le contrat legacy à carriers dupliqués, et ne transmet pas le client_id nécessaire à la priorité des changements provenant d’un autre client. Ce n’est pas une erreur du moteur.

Correction proposée pour 4.0.7 : `project`, `wire_format="compact_v2"`, `budget_tokens=1000`, `client_id="claude-code"` (déclaratif, sans authentification supposée). Négocier via les capabilities et garder un fallback explicite pour 4.0.5, sans prétendre au format unique sur cet ancien serveur. Un focus_query au démarrage n’est pas obligatoire : il n’existe pas encore de demande utilisateur pertinente.

## H4 — Budget final diagnostique, non imposé

Le seuil 16 000 octets n’est que journalisé après émission. La sonde d’un texte dépassant le contrat serveur montre ce comportement, mais **ne démontre pas un dépassement avec un brief conforme ≤6 000 octets**. Les annexes health/drafts/tasks/changes n’ont toutefois pas de budget global.

Si A8 revendique un plafond dur, réserver les parts, réduire les annexes par unités entières avec références/notice, sans coupe aveugle du brief. Sinon documenter « seuil diagnostique » et mesurer les sorties réelles avant de déclarer le gate A8. Les titres/actions des annexes restent coupés par `[:120]`/`[:160]` : ce n’est pas une coupe du brief, mais l’annexe doit annoncer sa troncature.

## H5 — Robustesse et latence à mesurer (non bloquant RC)

- `pending_drafts` ne vérifie pas que le JSON est un dict ou que ended_at est numérique : un brouillon local malformé peut faire échouer emit, au lieu d’un avis. Ignorer proprement les entrées invalides, avec signal diagnostique.
- Budget total non gelé : health 1,5 s + brief 8 s + B6 2 s + ready 1,5 s + changes 3 s + tasks 5 s, hors git et fichiers. Définir un budget SessionStart global et mesurer p50/p95 avec serveur disponible et panne.
- Libellé déduit du basename git, sans override explicite : les clones nommés différemment peuvent lire des projets différents avec registre inactif. Prévoir un libellé explicite validé ou documenter cette limite ; aucun rapprochement automatique.

Le curseur sauvegardé seulement après émission, l’absence de replay, les avis de reset/indisponibilité et le retrait de la coupe aveugle du brief vont dans le bon sens. H1–H3 doivent être traités avant activation du hook ; H4 doit être arbitré selon le contrat de budget. Aucun correctif appliqué à un fichier de Claude.
