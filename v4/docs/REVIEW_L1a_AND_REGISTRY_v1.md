# Revue adverse et gel L1a — 2026-10-11

Base qualifiée par Claude : f88a41d, 1291 tests dont 18 TCP, 30 reconnexions
max 40 ms/p50 28 ms après ready. Aucun résultat 3b observé avant ce gel.

Scénarios reçus e93e9662… : 100 cas, 50 FR/50 EN, 10 familles. Acceptés avec
amendements tracés individuellement dans le manifeste, original conservé.

1. `required_block_names`, `forbidden_block_names`, `deterministic`, `compare_modes`,
   `compare_paths`, `request.expand` acceptés. Expansion = IDs + enfants directs,
   filtrage de projet/validité avant sélection, pas de traversal récursif illimité.
2. `unknown_project` remplacé par le code L7a existant `project_unknown`.
3. `focus` remplacé par `fact` : focus est une section, pas un kind persistant.
4. Panne amont et notices de journal ne sont pas des états du moteur direct :
   attentes qualifiées par chemin (`path_errors`, `relay_notice_codes`).
5. Référence étrangère = `reference_not_found`, comme une référence absente ;
   ne pas exposer son existence via `out_of_scope`. Aucune donnée étrangère retournée.
6. `secret_like` devient le tag explicite `sensitive`, exclu du brief et de son
   expansion. Pas de détecteur de secrets ni de garantie DLP. Les autres outils
   gardent leur contrat ; oublier/faire tourner un vrai secret reste nécessaire.
7. `no_directive_execution` prouve que le code brief n'appelle aucune mutation ni
   outil prescrit par la mémoire. Sans inférence, cela ne prouve pas l'obéissance
   d'un lecteur LLM ; le texte porte un avertissement données/non-instructions.
8. Blocs volumineux : injecter des sources historiques en fixture, sans les
   tronquer au préalable via l'API d'écriture (sinon faux test du budget du brief).

Limites du jeu : beaucoup de paires FR/EN traduites, cas task restreints,
pas de preuve d'utilité terrain ou d'indépendance statistique. 100/100 est un
contrat fonctionnel ; compléter les erreurs/enveloppes par tests adverses SDK.
Tokenisation exacte offline obligatoire pour qualifier les budgets : table
cl100k_base absente du clone et du cache local repéré ; acquisition explicite
par Claude soumise à GO, jamais déclenchée par le serveur ou les tests.

## Registre L7a / UX avant activation

Oui : avec registre actif, labels inconnus refusés pour lecture/écriture MCP.
Pas de mode permissif qui créerait silencieusement des identités concurrentes.
Tous les projets encore utilisés doivent avoir une entrée approuvée ; les labels
historiques exclus restent stockés et accessibles via administration inter-projets
explicite `*`, sans être automatiquement accessibles à un hook.

Procédure nouveau projet : le hook affiche une notice avec proposition locale
(ID stable, label, contexte, racine ; remote/root Git comme indices) ; l'opérateur
vérifie collisions/forks et approuve ; modifier une COPIE du registre, valider par
`python -m memory_os_v4.projects --registry ...`, déployer atomiquement le fichier
approuvé puis redémarrer le serveur après GO. Les hooks vérifient le SHA-256 dans
ready avant capture. Aucun appel à projet omis ou `*` pour contourner un refus.

Le draft des 55 projets est INACTIF. La fusion des trois labels memory-os-ai,
Memory-os-ai et implementation n'est pas autorisée par leur nom ou cette revue :
Romain doit confirmer l'attribution des 9 éléments implementation et les autres
aliases/contextes. Ne pas activer avant cette validation, qualification sur copie
et vérification des empreintes client/serveur. Aucun registre privé copié au dépôt.

Validation syntaxique effectuée : 55 projets, SHA-256 b92ab0380432e637162d5100986c78e2e3a4aeacebfcc3281c40d4fe73737f30. Les 54 entrées sans contexte ne suffisent pas à identifier automatiquement un workspace : ajouter/approuver le contexte des projets utilisés avant de brancher leurs hooks. Les projets historiques non utilisés peuvent rester sans contexte.
