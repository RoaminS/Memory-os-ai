# Revue Codex du contrat B6 v1 — 12 octobre 2026

Verdict : **accord avec amendements R1–R6 avant déploiement client**.
Production maintenue en 4.0.5 ; aucun acquittement exécuté dans cette revue.

## Question de propriété du fichier

Oui, un client ops peut écrire `ops_dir/known_supersessions.json` : ce fichier
est un état opérateur, pas une table d'autorité ni le magasin. Claude peut
implémenter cette écriture dans son périmètre **après une décision explicite
de Romain pour les alertes affichées**, sous les conditions ci-dessous.
Un futur `memory-os-admin health ack` est préférable pour mutualiser le contrat
et les tests ; il n'est pas nécessaire d'ajouter un outil MCP et ce n'est pas
une dépendance imposée à l'incrément client immédiat.

La règle « serveur seul écrivain du magasin » reste inchangée. Le fichier ops
ne peut ni rétablir un fait, ni effacer une correction, ni approuver sa légitimité.

## Texte de remplacement proposé

**R1 — §2, identifiants et épisodes.** Remplacer la neutralisation générale des
chiffres par : « Les identifiants d'alertes sont liés à une catégorie stable et
à un épisode. Les quantités fluctuantes ne multiplient pas les notifications,
mais changement de statut HTTP, gravité, résolution puis réapparition,
nouvelle sauvegarde défaillante ou restauration/identité inconnue ouvrent un
épisode distinct. Aucune aggravation n'est masquée par un acquittement précédent. »
Ajouter un cas 401→503 et un cas résolution→récidive aux tests préenregistrés.

**R2 — §3, visibilité.** Remplacer l'affichage systématique des textes anciens/
nouveaux par : « Le bandeau contient IDs, date, catégorie, compte et provenance
du rapport, sans textes de mémoire. Les détails sont une lecture explicite
scoped, avec filtrage des éléments oubliés/sensibles et scanner ; indisponibilité
annoncée. Aucun accès SQLite brut ne peut créer de WAL/SHM ou contourner ce filtre.
Employer l'API de lecture ou le lecteur ops existant avec ses préconditions,
jamais un simple sqlite3.connect(mode=ro) supposé sans écriture. »

**R3 — §1, rapport utilisable.** Ajouter : « Lecture bornée, objet JSON validé,
horodatage et empreinte du rapport exposés. Rapport absent, tronqué, illisible
ou vieux de plus de 26 h = avis explicite, jamais état sain ni acquittement
autorisé. Un rapport produit avec échec de scan ne justifie aucune disparition
des alertes précédentes. Les traces techniques sont masquées et bornées. »

**R4 — §4, écriture et reprise.** Remplacer par : « Un seul writer d'acquittement,
verrou opérateur distinct, répertoire 0700, fichiers réguliers 0600 appartenant
à l'utilisateur, liens symboliques/hardlinks refusés, tailles bornées. La décision
est liée à l'ID, à l'empreinte exacte du rapport montré, au commentaire et à
l'acteur déclaré. Journal durable fsync avant projection, remplacement atomique
du JSON puis fsync du répertoire. Une panne entre ces écritures conserve un
état pending explicite ; la reprise termine uniquement cette décision déjà
enregistrée, sans acquitter une autre alerte. Rejeu identique idempotent,
concurrence sans perte d'IDs, fichier invalidé jamais écrasé automatiquement. »
`--by romain` est déclaratif : aucune preuve d'authentification ou d'attention
humaine ne se déduit de ce paramètre. Pas d'acquittement dans SessionStart.

**R5 — §4, durée.** Remplacer « permanent acknowledgement » par : « acquittement
de l'événement de supersession examiné, pas autorisation permanente liée au
seul item_id ; conserver la cible et les dates examinées dans la trace. Le
format historique `known_supersessions.ids` sert de projection compatible,
pas de preuve que toute future version/restauration de cet ID est approuvée.
En cas de changement de l'événement, restauration ou identité non attestée,
réexposer l'alerte et demander une nouvelle revue ; ne pas déduire le silence
de santé de la légitimité. La garantie multi-épisodes complète dépendra des
événements durables B5 ; annoncer cette limite avant tout usage. »
Pour les autres anomalies, le délai de 24 h est un maximum : gravité accrue ou
source illisible annule le masquage immédiatement. Horloge reculée = visible.

**R6 — §tests et budgets.** Remplacer « hook stays ≤ its budget and silent on
secondary failure » par : « Le bandeau d'indisponibilité critique reste visible,
dans un contexte final borné comprenant brief, tâches et alertes. Seul l'échec
du supplément secondaire A5 est silencieux. Le hook ne coupe pas de mots ni
de JSON et ne refait pas task_list si les mêmes tâches sont déjà injectées. »
Tester lien symbolique, rapport périmé, acquittement sans décision/ID explicite,
panne après chaque écriture, deux writers, changement d'événement et données
sensibles, en plus des cas du draft. Réception macOS reste un test humain.

## Coordination avec B7

Le nouveau produit n'utilise plus COS pour qualifier la santé. Le rapport expose
la politique **déployée** si `/ready` l'annonce, sinon « unavailable » : il ne
prétend pas attester une protection par une variable du client ops.
Les supersessions non acquittées restent visibles même sans `--confined`.
La première observation ne constitue pas un acquittement automatique.
`superseded_new` et le format historique de la baseline restent disponibles
pour la compatibilité 4.0.5 ; leur nom signifie « non présent dans la projection
connue », pas forcément « événement créé aujourd'hui ».

Les plists live 4.0.5 ne sont pas modifiés par ce bundle. Retirer leurs arguments
historiques ou remplacer le job santé nécessite la procédure client/opérateur
autorisée ; ne pas lancer du code de développement contre le magasin réel.
