# P-B v1 — intégrité, préenregistrement du 13 octobre 2026

Baseline d003e54. Le fichier `v4/tests/fixtures/integrity_v5/protocol_v1.json`
et son SHA fixent les paramètres **avant** nouveau code B3/B2.
Le commit de protocole précède les commits produit. Aucun résultat P-A n'est
réutilisé comme preuve d'intégrité ; aucune comparaison leaderboard.

## Ordre et gates

B3 garde des nouvelles identités → B2 aperçu/raison/CAS/impacts → B4 compensation
et B5 journal via API existantes → B9 adversarial/crash. Contrats B4/B5 à
contresigner avant code, comme demandé dans PLAN_COMMUN_v5. Version inchangée
4.0.6.dev0 jusqu'au RC ; production 4.0.5 inchangée.

B3 : contrôler NFKC puis casefold puis strip, **sans appliquer ce contrôle à la
valeur persistée**. Refuser exactement other/unknown/default pour single.
Les prédicats précis restent extensibles, distincts du vocabulaire des facettes.
Generic multi reste additif. Les anciennes identités restent lisibles, sans
renommage/migration ; une nouvelle assertion single générique est refusée même
si une identité identique existe déjà. Aucun changement de cardinalité historique.

B2 : raison non vide pour une invalidation effective, cibles et révisions exactes,
aperçu sans écriture ni encodage, CAS sous verrou au début de l'application.
Au-delà de trois invalidations, confirmation de la liste d'impacts liée au
payload et aux révisions ; une confirmation déclarative ne prouve pas un accord
humain. Entité commune = éventuel avertissement, jamais refus automatique.
La confirmation ne permet aucun contournement de maintenance/read_only/scope.

## Développement et validation indépendants

Codex écrit les régressions synthétiques de développement. Claude garde un
nouveau gold de **40 scénarios** scellés avant le gel du candidat d'intégrité :
10 identité/temps/portée ; 10 correction/CAS/concurrence ; 10 compensation et
traçabilité ; 10 crash/oubli/secrets/document/CAS. Les données du gold ne sont
pas transmises à Codex ; seuls inputs compatibles et manifeste avant exécution.
Pas d'exécution de validation pendant qu'un arbre est modifié.

Critères : zéro invalidation inattendue ; zéro succès annoncé pour un lot
partiel ; zéro violation de scope/confidentialité ; tous remplacements légitimes
annotés, y compris sans entité commune, acceptés avec raison/CAS/impacts valides.
Chacun des 40 cas reste dans le dénominateur. Publier effectifs/refus et limites
d'échantillon, jamais une estimation robuste depuis quelques corrections réelles.
Historique et reçu final SQLite atomiques ; aucune nouvelle promesse JSONL.

B9 injecte interruption/exception à chaque frontière d'écriture sur magasins
jetables et vérifie les acquittements, reçus, historique, reprises sans rejeu
aveugle, reconstruction des index dérivés. Rejeu des incidents uniquement sur
copies : aucun accès d'écriture aux magasins de production. Aucun secret réel
utilisé dans les fixtures.

Le catalogue est mesuré contre la baseline : zéro outil MCP ajouté, cible
≤+10 % de tokens schémas/descriptions. Les jobs lourds sont exécutés par Claude,
séquentiellement avec nice 15 et gardes machine. Tests unitaires légers hash
seulement. Pas de téléchargement, appel API, consolidate/compact ou bascule.

Les fonctionnalités sans leur preuve restent en cours. Un protocole ou un
contrat livré n'est pas une qualification ni une activation de production.
