# Livraison corrective BEAM C4 retrieval v2

Le correctif rend l'attestation Git indépendante du répertoire courant du
worker et ajoute une reproduction automatisée depuis un sous-dossier non suivi.
Le plan a aussi été réduit au périmètre décidé par Romain : récupération C4
contre BM25 uniquement, zéro lecteur et zéro appel LLM autorisé.

Commits :

- `c5eb15c` — correction `cwd=clone` et régression Git séparée ;
- `95276a8` — plan `beam-c4-retrieval-v2`, lecteur retiré du lanceur.

Preuves locales sans inférence : 31 tests BEAM ciblés verts ; 965 tests v4
légers verts, 1 test neuronal lent exclu ; lint de qualification vert.
`frozen()` passe depuis
`.local-qualification/beam-c4-retrieval-v2/attestation-smoke`, qui est non suivi.
Le plan local préparé porte le SHA-256
`5878e5b54d71a6d6437a42f6e637e6a79552f7fda8d99828ace427dbbb5e604e`.

Les deux tentatives v1 de Claude ont échoué avant toute inférence. Elles sont
documentées, conservées et exclues. Utiliser uniquement le nouveau dossier
`.local-qualification/beam-c4-retrieval-v2` et les commandes de
[l'amendement](../../v4/docs/BEAM_RETRIEVAL_V2_AMENDMENT.md).
