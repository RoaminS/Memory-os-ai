# Changelog

## 4.0.2 — candidate ALPHA (2026-10-10), non publiée

- Doctor installation + registre neuf clients + setup auto-jeton ; erreurs de configuration
  contrôlées, diagnostic SQLite conservé via `doctor --store-only`.
- C4 scoped_literal/garde 3 proposés par défaut ; rollback explicite legacy/0.
  LongMemEval-S : 500 exécutées, 479 avec preuve, 466 clusters ; ΔRecall +0,042
  IC95 [+0,022;+0,061], ΔMRR +0,012 [+0,008;+0,0164]. MiniLM, questions recoupant
  l’oracle : résultat non indépendant, pas une exactitude de réponse ni un classement.
- SQLite S2 transactions/cache/reçus et S3 migration hors ligne réversible livrés opt-in ;
  JSONL reste par défaut. Backup/restore SQLite S4 restent à qualifier.
- Qualification de deux nouveaux artefacts ; garde fermée tant que manquent les preuves
  du relais installé. Activation de production réservée à Romain.
- Voir `docs/releases/v4.0.2.md`, `v4/docs/RELEASE_4_0_2_DECISIONS.md`
  et `releases/4.0.2/QUALIFICATION.md`.

## 4.0.1 — ALPHA intermédiaire, publication soumise à qualification finale

- Identité de distribution et des deux moteurs : 4.0.1, sans suffixe rc.
- HTTP local : Host/Origin validés par le SDK MCP ; bearer obligatoire côté serveur,
  chargé depuis un fichier privé ; `/health` et `/ready` authentifiés, identité de build.
- Faits : remplacement sur identité explicite ; déduplication conservatrice par
  texte normalisé identique et portée compatible, jamais par seule similarité.
- Un écrivain moteur par magasin et reçus de requêtes JSONL. Résultat incertain
  après panne intermédiaire : aucune promesse de transaction commune aux fichiers.
- A2 : réparation ciblée sur plan versionné de douze invalidations INC-001/001b,
  simulation par défaut, historique/provenance préservés et trace vérifiable.
- SQLite S1 expérimental derrière Store et drapeau explicite : magasins neufs,
  historique, transactions et tests de panne de processus ; aucune migration.
- Import exact des clients/ops Claude : relais, admin token/health/backup/drill/
  hook/setup et extra `[ops]`. Revues A6 R1–R7 et
  B1–B5 levées ; qualification du relais installé exigée avant publication. Les
  garanties industrielles complètes restent à réaliser en SQLite S2–S5.
- Notices d'exploitation, règles agents et changelog inclus dans les distributions.

La revue A6 ne constitue pas seule un GO de publication. Lire
`v4/docs/A6_REVIEW_6d3f749.md` et `docs/releases/v4.0.1.md` avant toute utilisation
des outils ops. Aucun déploiement/réparation de production effectué par ce lot.

## 4.0.0

Version v4 déjà publiée sur PyPI selon l'opérateur ; moteur v4 et compatibilité v3.
Les corrections ci-dessus répondent aux incidents et à la qualification ultérieure.
