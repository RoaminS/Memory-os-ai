# P-A brief 4.0.7 — verdict validé le 13 octobre 2026

**VALIDÉ pour les 40 cas scellés V5-VAL v2.1**, candidat moteur
`ceedb5cfdfd0d61929276dbcd1417ea077044412`, runner d003e54. Claude conserve le gold.
Codex n'a pas ouvert le gold ni les outputs officiels : Codex a revu le scorer
et reproduit les contre-exemples sur des fixtures indépendantes synthétiques.

## Preuves et borne de la conclusion

- Claude rapporte, sur les mêmes octets collectés sans réexécution : **40/40**,
  cinq familles 8/8, six gates vraies : seuil ≥38, zéro violation safety,
  zéro fragment, références valides, critiques faisables satisfaits, intégrité
  de collection. Aucun seuil/gold changé.
- Scorer final `score_v3_4b.py`, SHA-256
  `a47246d360e0ea9693eda754036d93b2a0879848b8fae58b0ffa51a10b2c8adb`.
- Suite `mutation_suite_v3_3.py`, SHA-256
  `72008545aa29abc6c1018d324a60c10e329928b6130f0276befd08fea6184b7e`.
  Log Claude : **28/28 contrôles = 1 positif + 27 mutations**, pas 28 mutations.
- Codex : empreintes conformes, contrôle positif synthétique **40/40**, puis
  **6/6 mutations refusées** par les catégories attendues (S1–S4, T1/T2).
  Tous bytes/SHA/annexes régénérés ; zéro lecture du gold, du corpus ou d'outputs
  officiels. Aucun modèle, appel réseau/API ou test TCP dans cette revue.
- Performance séparée, rerun préenregistré rapporté par Claude : serveur
  **p95 193,9 ms**, seuil 250 ms. Échec sous contention et tous rapports conservés.
  Codex ne prétend pas avoir reproduit la mesure physique sur ce Mac.

Les corrections successives du juge ont eu lieu après observation des résultats
et restent tracées : v2.1/v2.2/v3.x et rapports antérieurs sont conservés.
La baseline sans spans/A7 demeure partielle, non observable : **aucun score
comparatif de baseline revendiqué**. Le gate atteste des propriétés du produit
sur ce jeu formatif ; il ne prouve ni gain utilisateur, ni supériorité générale,
ni score/position de leaderboard. La qualification release/copie et les GOs
publication/bascule restent distincts.

Les garde-fous réparés couvrent complétude, budgets complets, offsets et contenu,
scope/validité, critiques, confidentialité de tout le payload et comptage par type.
Le compteur par type comporte normalement cinq champs ; outside_pool optionnel
est accepté uniquement si égal à eligible−pool. Les totaux positionnels, l'avis
coverage_detail_omitted et les différences légitimes serveur/relais restent admis.
Les cas légitimes d'API plus généraux (owner/patch de tâches, expansions historiques,
prédicats et aliases étendus) nécessitent leurs fixtures spécifiques : aucune
preuve universelle ne découle des 40 cas.

## Suite

Gate P-A close pour ce périmètre. Démarrage du protocole P-B et lots B3/B2 puis
B4/B5 puis B9, incréments isolés. Le candidat P-A demeure figé ; ne pas le
rebaptiser comme qualification automatique d'un moteur d'intégrité ultérieur.
