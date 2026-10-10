# BEAM 100K — résultat C4 contre BM25

## Verdict préenregistré

Claude a exécuté hors sandbox le plan figé `beam-c4-retrieval-v2`, source
`95276a8123838bfe9f7937f6325307d6be88ac39`, SHA-256 du plan
`5878e5b54d71a6d6437a42f6e637e6a79552f7fda8d99828ace427dbbb5e604e`.
Les 400 questions et 20 conversations ont terminé sans échec ; 355 questions
avaient une preuve annotée et entrent dans les métriques de récupération.

| métrique | C4 | BM25 du même bras | delta apparié, IC95 conversation |
|---|---:|---:|---:|
| Recall@10 de tours annotés sous budget | 0,462 | 0,445 | +0,017 [-0,004 ; +0,040] |
| MRR | 0,444 | 0,436 | +0,008 [+0,003 ; +0,015] |

Le critère exigeait une borne basse de l'IC95 du delta de rappel strictement
positive et un delta MRR moyen positif. La première condition échoue : **C4 ne
bat pas BM25 selon le critère préenregistré**. Le run n'a fait aucun appel LLM et
ne mesure ni exactitude de réponse, ni succès agentique, ni rang leaderboard.

L'attestation compacte est
[`beam_c4_retrieval_v2_summary.json`](../bench/results/beam_c4_retrieval_v2_summary.json).
Les prédictions et contextes privés restent dans le dossier de qualification ;
`scripts/verify_beam_result_v2.py` en vérifie les empreintes et chiffres.

## Lecture post-hoc, limites causales

Le découpage par catégorie est exploratoire. `knowledge_update` perd 0,037 de
rappel en moyenne, avec 0 victoire et 3 défaites parmi 40 questions. Cela motive
une hypothèse ; trois différences seulement ne démontrent pas une cause.

La récence était désactivée car le pipeline ne disposait pas d'une date fiable
par message. Le plan sérialise encore `supersede_cos=0.8`, pas la valeur de
confinement production 1.01. Ce champ ne déclenche toutefois aucune mise à jour :
les citations capturées n'ont ni `identity` ni `replaces`, et le moteur 4.0.2 ne
remplace jamais un fait sur la seule similarité. Le run ne teste donc pas la
supersession bi-temporelle. Attribuer les pertes à la récence ou à la
supersession serait prématuré.

Les scores faibles de `event_ordering`, `summarization` et
`instruction_following` sont compatibles avec une limite top-10/contexte sur
des réponses réparties dans plusieurs sessions. Ce diagnostic n'est pas isolé
expérimentalement ; il reste une hypothèse.

## Coût d'indexation

L'ingestion des 20 conversations a pris 14 813 s : médiane 678 s, maximum
1 013 s. Granite a consommé 14 792 s, soit 99,86 % de ce temps. Le journal compte
16 075 appels encodeur pour 21 717 textes, seulement 1,35 texte par appel malgré
un batch configuré à 4. Le code confirme le mécanisme : `add_facts` encode chaque
fait accepté séparément. La recherche chaude reste à 41 ms p50 et 69 ms p95.

## Décision C4

C4 reste le défaut 4.0.2. LongMemEval-S a franchi son seuil, bien que non
indépendant ; sur BEAM les estimations de rappel et MRR sont positives et aucune
régression confirmatoire n'est établie. Une absence de significativité du rappel
ne justifie pas un retour arrière. La revendication générale « C4 bat BM25 » est
retirée. Le rollback documenté reste `BM25_PROFILE=legacy` et
`LEXICAL_GUARD_K=0`. Aucun changement de récence ou supersession n'est promu.

## Plan prioritaire

### P0 — réduire l'indexation sans régler la qualité sur BEAM

Suite autorisée le 2026-10-10 : correctif/tests et lanceur apparié livrés dans
[INDEXING_BATCH_V1](INDEXING_BATCH_V1.md). Les objectifs Granite restent non
mesurés. Les protocoles de
[réponses E2E](E2E_HELDOUT_DESIGN_V1.md) et de
[temporalité](TEMPORAL_HELDOUT_DESIGN_V1.md) sont des designs, avec GO distinct.

1. Instrumenter temps, taille de batch, RSS, swap et appels par phase.
2. Construire et valider le lot de faits avant mutation, éliminer les doublons
   par texte/identité, puis encoder les faits acceptés par vrais lots. Préserver
   l'ordre des remplacements, la transaction SQLite unique et le reçu final.
3. Tester chaque frontière de panne et exiger des ids/rangs bit-à-bit identiques
   à l'implémentation 4.0.2 sur un corpus synthétique.
4. Gate Granite sur une charge synthétique 10k puis BEAM observé utilisé seulement
   comme charge de performance : p50 ingestion ≤300 s, p95 ≤450 s, max ≤600 s,
   appels/textes ≤0,30, RSS pic ≤4 Gio, swap <8,5 Go, disque libre ≥5 Go ; recherche
   p50 ≤50 ms et p95 ≤100 ms ; aucun changement de score, rang ou reçu.

### P1 — hypothèse temporelle sur un nouveau jeu tenu à l'écart

BEAM 100K est désormais in-sample et interdit à tout réglage de qualité. Avant
de charger les résultats d'un nouveau corpus, il faut geler son empreinte, vérifier
zéro recoupement, conserver au moins 30 conversations indépendantes et publier
les effectifs `knowledge_update`.

Le premier candidat autorisé est **récence seule** : même capture et C4, dates de
session dérivées uniquement des `time_anchor` observables, paramètres de récence
gelés, aucune annotation transmise avant le score. La comparaison est appariée à
C4 actuel ; primaire `knowledge_update Recall@10`, bootstrap par conversation,
5 000 réplications, borne basse du delta >0, delta MRR global moyen ≥0, zéro échec.

La supersession demande un extracteur client question-aveugle capable de produire
des identités ou `replaces`. Elle aura un plan et un held-out disjoints après choix
et budget de cet extracteur par Romain. Réactiver une supersession par cosinus est
exclu : cela reproduirait INC-002 et ne testerait pas le contrat 4.0.2.

## Commandes sans nouveau calcul lourd

Après import du bundle, Claude peut uniquement vérifier les preuves existantes et
les tests légers :

```bash
cd /Users/romain/Documents/Codex/2026-10-09/v/work/implementation
python3 scripts/verify_beam_result_v2.py \
  .local-qualification/beam-c4-retrieval-v2
python3 -m pytest -q v4/tests/test_beam.py v4/tests/test_lexical_guard.py
python3 -m ruff check scripts/verify_beam_result_v2.py
```

Aucune commande de campagne nouvelle n'est fournie avant l'implémentation du
banc de performance et le choix du held-out. Leur exécution Granite ou tout
téléchargement restera bloqué jusqu'au GO explicite de Romain.
