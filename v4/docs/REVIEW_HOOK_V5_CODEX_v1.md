# Hook SessionStart v5 candidat — NON VALIDÉ pour activation

14 octobre 2026. Fichiers de Claude lus uniquement, aucun correctif appliqué.
17/17 assertions originales réexécutées avec auth neutralisée, MCP simulé et
temporaires privés nettoyés. Les sorties/sondes indépendantes sont dans
`codex_runs/hook-v5-review/`. Aucun réseau ni accès token/magasin production.
Cette décision est indépendante de la qualification du serveur 4.0.7.

## H1 — VALIDÉ sur le contrat de pagination simulée

La vraie fonction fetch_changes est exercée par les tests : >20 pages, reprise
catchup=true sans annoncer de nouveaux événements, fin du rattrapage, nouveaux
événements ensuite, expiration/reset et erreur avec curseur conservé. Le curseur
est enregistré après émission. Les libellés de section restent génériques mais
le corps distingue explicitement le rattrapage. La preuve porte sur un SDK
simulé, pas sur le hook installé ni la latence TCP réelle. Le test « timeout »
utilise un code d'erreur du feed, pas une expiration effective du CancelScope.

## H2 — VALIDÉ

Déduplication par task_id seulement pour references.kind=task/rendering=full ;
reference-only et anciens formats restent visibles dans l'annexe. Vérification
indépendante supplémentaire : le vrai serializer assemble() du moteur produit
les références attendues ; T1 plein masqué, T2 référence seul conservé. Plus de
comparaison par sous-chaîne de titre.

## H3 — VALIDÉ pour la négociation annoncée

compact_v2/budget_tokens=1000/client_id=claude-code si capabilities.brief.wire_formats
l'annonce ; fallback legacy explicite pour le serveur4.0.5. Provenance déclarative
correctement distinguée de l'authentification. La lecture de /ready doit toutefois
respecter le budget global ci-dessous.

## H4 — AMENDÉ, non bloquant à lui seul

16 000 octets est explicitement un seuil diagnostique, sans plafond dur promis.
Aucune coupe aveugle du brief. Titres/actions/raisons des annexes signalent leur
troncature. **Le brouillon L9 ne le fait pas** : draft de501 caractères → préfixe500
sans « … ». Remplacer la coupe [:500] par _short(...,500), ou ajouter une notice
« extrait tronqué ». Ne pas annoncer que toutes les troncatures sont marquées
avant ce correctif. Une mesure finale ne démontre pas un budget serveur/client
global ; garder les deux périmètres distincts.

## H5 — NON VALIDÉ : le plafond global10 s n'est pas imposé

remaining() protège seulement les appels B1/L7b. health_alerts() puis
pending_drafts() restent inconditionnels ; changes_enabled()/work_state_enabled()
peuvent déclencher /ready **avant** de vérifier remaining(). main utilise aussi
ses propres timeouts health/git/brief sans tenir compte du reste. L'addition des
bornes dépasse10 s. La promesse « annexes ignorées avec avis » n'est pas respectée :
B1/L7b sautés par budget ne produisent pas d'avis.

Reproduction indépendante (sans attendre10 s) : démarrer emit avec10 ms restants,
status B6 simulé70 ms (bien inférieur au timeout2 s fourni) → retour après71 ms,
budget dépassé et aucun avis d'omission. Avec budget déjà expiré et _CAPS absent,
la vraie readiness() appelle encore httpx.get(timeout=1.5).

Correction avant activation :
- appliquer la même deadline à toutes les phases ; borner chaque timeout par
  min(timeout_local,remaining()), et ne démarrer aucune phase réseau/subprocess
  facultative sans réserve suffisante pour émettre le résultat ;
- vérifier remaining() AVANT toute découverte de capabilities ; timeouts AnyIO
  et synchros cohérents (une annexe passée son budget ne doit pas bloquer l'émission) ;
- si le budget manque, émettre une notice courte pour les changements/tâches
  non lus et conserver le curseur ; aucun silence assimilé à une absence ;
- couvrir main depuis health/git/brief, pas seulement emit ; tests avec clock
  simulée pour l'ordonnancement et un vrai délai court pour la cancellation.

Les brouillons malformés dict/type et le libellé MEMORY_OS_PROJECT_LABEL sont
corrigés. Le filtre numérique accepte encore ended_at=+inf comme date récente :
refuser les dates non finies et documenter la tolérance des dates futures
(durcissement non bloquant).

## H6 — Ordre des tâches : corriger pour respecter T2

fetch_tasks retrie par (status!='blocked',stale), donc une tâche bloquée périmée
passe avant une tâche ouverte fraîche. Sonde sur la vraie fonction :
[fresh-blocked, stale-blocked, fresh-open]. Contrat moteur : bloquées non périmées,
 ouvertes non périmées, puis périmées. Conserver l'ordre retourné par l'API ou trier
par (stale,status!='blocked') sans bouleverser les égalités. Ajouter ce cas à la
suite ; aucun impact sur le serveur, mais une priorité client trompeuse.

**Conclusion : H1–H3 acceptés ; H5 bloque l'activation du hook.** H4/H6 à corriger
avec lui. Les17 tests sont verts mais ne couvrent pas les deux dépassements
reproduits ni l'ordre stale/fresh. Aucun correctif du hook n'est requis dans
v4/src : la candidate serveur4.0.7 conserve son diff version-seule.
