# L5 — capacités réellement chargées

`/ready` authentifié et `memory_status.capabilities` exposent :
- moteur/version/build (commit et provenance) ;
- backend effectivement ouvert, UUID du magasin, atomicité réelle SQLite/JSONL ;
- modèle/profil d'encodage attesté, paramètres effectifs C4 et maintenance ;
- contrat de reçus L3, absence de rejeu automatique de mutation ;
- registre chargé, son SHA-256 et politique des labels inconnus ;
- brief compact/detailed/expansion, budgets et disponibilité exacte du tokenizer.

Le snapshot ready est capturé sous le verrou du moteur, sans chargement d'un moteur
froid. Un moteur occupé/non intègre ne fabrique pas un snapshot prêt. Le profil
existant reste exposé aussi au niveau ready pour compatibilité. Le serveur déclare
`client_relays: unknown_to_server` : il ne peut identifier tous les runtimes clients.

Doctor vérifie la cohérence de la réponse et peut comparer des attentes explicites :
`MEMORY_V4_EXPECT_SERVER_VERSION`, `MEMORY_V4_EXPECT_BACKEND`, `MEMORY_V4_EXPECT_DEVICE`,
`MEMORY_V4_EXPECT_REGISTRY_SHA256`. Il ne transforme pas sa Config locale par défaut
en attente de déploiement. Mismatch = FAIL, capacités absentes sur ancien serveur =
WARN inconnu. Tokenizer compact annoncé mais indisponible = FAIL.

La présence d'une configuration client reste une observation de CONFIGURATION.
Pour une preuve de contrôle issue d'un client réel : dans ce client, appeler
`memory_status`, puis `memory_request_status({})`. Le relais retourne
`connection.observation` : client_id déclaré, endpoint haché, timestamp, identité
serveur/commit/UUID et artefact du relais (version, SHA-256 du code empaqueté).
Pas de texte utilisateur, jeton, arguments, chemin privé ou contenu de mémoire.

Claude peut agréger ces objets (sans les réécrire) dans un fichier local :
`{"schema_version":1,"connections":[...observations...]}` puis fournir
`MEMORY_V4_CLIENT_PROOFS=/chemin/fichier.json` à doctor. Une preuve n'est retenue que
si elle a moins de 300 s, vise le même endpoint/serveur courant/UUID et correspond
à l'artefact local de référence de doctor. Sinon état inconnu, jamais installation
inventée. Un relais épinglé différent exige revue de son artefact, pas une simple
égalité des numéros de version.

Ce sont des observations locales exportées, PAS une authentification du nom client
ni une sonde de connexion encore vivante : un processus peut s'arrêter après le
contrôle. doctor les décrit comme telles. Aucun fichier de preuve automatique,
aucune télémétrie envoyée, aucune activation de clients.
