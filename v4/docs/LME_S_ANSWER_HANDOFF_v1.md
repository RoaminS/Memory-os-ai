# Relais Claude — LongMemEval-S Luna, 2026-10-11

Bundle : **`lme-s-answer-luna-v1.bundle`**, référence nommée
**`refs/heads/fix/v4-production-foundation`**, au-dessus de **92e09a8**.
Commit du harnais/protocole : **845507ee2180e7119508ee8e9fc1bdc4962b294b**.
Le commit suivant ne change que README/documentation. Le SHA du bundle est remis
dans la réponse de livraison, pas incorporé dans son propre contenu.

## Preuves locales, sans inférence

| Vérification | Résultat |
|---|---|
| Suite v4 légère, séquentielle, exclusion des deux fichiers TCP | **1 151 passed, 1 deselected** ; `.local-qualification/lme-s-answer-checks-v1/v4.log` |
| Tests dédiés du nouveau harnais | **33 passed** ; AST officiel sans exécuter API, conservation des espaces, format/tool isolation, usage rejeté récupéré, cinq templates/six types + abstention, source archive privée, 500 dénominateur synthétique, groupes proof/abs, reprise manuelle/ledger et export |
| Nouveau harnais + anciens answer_campaign/answer_pilot | **73 passed** après dernier correctif |
| Ruff projet et E9/F v4 ; `bash -n` ; CLI `--help` | **PASS** |
| `verify_source(clone, '845507e', code_record())` | **PASS**, sources exactes du harnais/moteur de travail attestées |
| Archive moteur53030c2 sur dossier jetable privé | **43 fichiers** ; encodeur SHA égal à preuve copie qualifiée ; Config SHA identique à loader du banc ; zéro inférence |

Il reste à Claude à rejouer **les 10 tests TCP** (fichiers
`v4/tests/test_server_http_e2e.py`, `v4/tests/test_ops_e2e.py`) et la suite entière
avec la commande du [runbook](LME_S_ANSWER_LUNA_v1.md). Le dénominateur exact et
le groupage du S réel sont auditables dans `plan.json` après prepare.
Codex n'a acquis aucun fichier réel ni lancé prepare/retrieve/answer.

## Séquence autorisée

1. Importer le bundle et vérifier SHA/ref. Rejouer tests/lint/shell sans modèles.
2. Exécuter les commandes exactes de [LME_S_ANSWER_LUNA_v1.md](LME_S_ANSWER_LUNA_v1.md) :
   acquisition du seul source grading≤20Ko, prepare dossier neuf puis lecture
   du plan scellé. Aucun corpus/model supplémentaire. L'acquisition préserve
   `FAILED.json` en cas d'erreur ; ne pas contourner une divergence du grader.
3. Vérifier le moteur évalué **53030c2/4.0.3**, profil copie attesté
   MPS/native/batch32, Luna low/high, 500 questions et abstentions, clusters≥30,
   limites et statut **DESCRIPTIF**. L'évolution relais4.0.4 reste dans le clone,
   mais n'entre pas dans le moteur évalué.
4. `retrieve` : un seul job local lourd, magasins isolés par question, pas de
   LLM. Attendre fin/report/attestation avant tout appel Codex.
5. `answer --batch-calls 40` : premier lot. Continuer uniquement par
   `answer --batch-calls 40 --resume` **manuel** après batch/quota, mêmes fichiers.
   Pas de boucle automatique, pas de réémission d'un appel pending/blocked.
6. `report` après statut complete uniquement. Publier scores/IC par type,
   abstentions, usage connu/inconnu et limites, jamais « top1 ».

Ne pas modifier les `.py` suivis ni les protocoles pendant la campagne :
le manifeste figé contrôle le clone avant/après chaque lot. Poursuivre un autre
lot de développement dans un espace isolé, sans toucher ce moteur/harness.
En cas de changement du CLI/runtime ou d'arrêt non quota : preuves conservées,
revue opérateur/amendement avant reprise. Les jours de recharge de quota ne sont
ni observés ni estimés par le harnais.

**Budget** : 2 000 appels réussis, jusqu'à 200 refus pré-génération de quota en
réserve ; seuil40Mtokens observés (pas une limite fournisseur), CLI48h,
récupération8h, au plus40appels/lot. Estimation18,1Mtokens/~3,9h extrapolée du
pilote ; plage prudente4–20h CLI +3–6h récupération, attente quota inconnue.
API0$, ChatGPT uniquement ; aucune clé/OAuth exportée. Swap8,5GB/disque5GB,
nice15, phases séquentielles. Aucun changement de production/PyPI/GitHub.
