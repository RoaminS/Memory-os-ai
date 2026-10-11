# Changelog

## 4.0.5 — correctifs en préparation, non publiée / non déployée

- Préflight du relais : panne/initialisation distinctes d'un contrat ancien,
  résultat « cette mutation non envoyée », reprise manuelle seulement et aucun
  raccourci pour les request_id déjà incertains. Qualification machine en attente.
  Arguments invalides signalés distinctement ; alias canonique compact conservé.
- Configurations MCP personnelles retirées du suivi Git, fichiers privés
  conservés ; template explicite v4 relayé à la place du serveur v3 autochargé.
- Archivage vérifié des preuves sans retrait des originaux (donc sans gain disque)
  et contrats L7b/CAS amendés ; L7b non implémenté/non activé.
- SQLite S4 incrément2, candidate non qualifiée pour activation : fenêtre serveur read_only,
  sauvegarde en ligne par API SQLite, inventaire exhaustif et archive chiffrée,
  drill fichiers en quarantaine, marge disque health/doctor et modèles hebdomadaires
  inactifs. Réconciliation des tombstones depuis l'autorité actuelle, nouvelle
  identité et reçu atomiques sur copie, ouverture HTTP privée contrôlée, rotation
  et matrices ENOSPC/SIGKILL synthétiques. Clé metadata corrigée, guard JSONL à
  référence faible, compteurs hors brief. 585 v3 +1582 v4 unitaires passent ;
  TCP et lifecycle10k à qualifier par Claude. Aucune activation SQLite/production.
  Voir `v4/docs/SQLITE_S4_INCREMENT2.md` pour incidents et portes restantes.

- Isolation commune des tests v3/v4 avant leur collecte : HOME/XDG/AppData
  jetables, garde des écritures Python dans le HOME réel, échec même si l'erreur
  est interceptée, protection des chemins via symlink et dir_fd.
- Correction de `TestSetupMainCLI.test_main_all_target` : mock de `TARGETS`,
  dont les callbacks originaux contournaient les anciens mocks de fonctions.
- Isolation incluse dans la prochaine sdist et dans les empreintes de source ;
  qualification dédiée avec comparaison SHA-256/mtime des configurations clients.
  Aucun changement de serveur, profil d'encodage ou magasin de production.

## 4.0.4 — ALPHA (2026-10-11), qualifiée / déployée localement

- Incident de qualification : la suite v3 livrée dans cette sdist peut remplacer
  la configuration Claude Desktop par le serveur v3 et écrire d'autres configs
  locales. Ne pas exécuter les tests de la sdist 4.0.4 sur un profil utilisateur
  réel ; utiliser un utilisateur/conteneur jetable. Correctif prévu en 4.0.5.
  La configuration locale a été restaurée par Claude ; le service 4.0.4 n'est
  pas la cause. Voir `v4/docs/INCIDENT_TEST_HOME_2026_10_11.md`.
- Qualification complète et bascule locale réalisées par Claude avec le GO de
  Romain ; publication PyPI réalisée par Romain, empreintes vérifiées par Claude.

- Verrou serveur de maintenance ; instructions clients alignées, opérations
  destructrices interdites sans activation explicite par l'opérateur.
- Journal durable du relais, statut local des requêtes incertaines même pendant
  panne du catalogue, adoption read-only, rotation bornée sans purge d'incertains.
  Aucun rejeu automatique ; limites de transactions JSONL explicites.
- Rotation du jeton et reconnexion bornées, erreurs explicites, détection d'un
  changement d'identité serveur ; `serverInfo.version` amont conservée.
- Capacités déployées dans `/ready` et doctor ; observations client datées,
  identité/profil/version contrôlés ; diagnostic distinct des preuves de connexion.
- Brief compact par défaut : enveloppe entière ≤6000 octets / ≤1500 tokens
  cl100k_base, validité/portée filtrées, références expansibles, detailed explicite.
  Extra `[brief]` et table locale SHA-256 vérifiée requis, aucun téléchargement
  implicite ; correction du runtime jetable incluant `tiktoken_ext`.
- Registre canonique IDs/alias livré mais INACTIF selon Romain ; aucune fusion
  automatique, aucune réécriture historique. Création de nouveaux projets à
  fluidifier avant activation séparée.
- C4, JSONL et profil auto→MPS/native/batch32/512 inchangés ; SQLite opt-in.
- Compteurs/latences/budgets de qualification locaux ; aucune nouvelle campagne
  ni prétention leaderboard. Qualification source, exact tokens, copie4.0.3→4.0.4,
  et relais installés requise avant GO distincts de publication et bascule.
- Diagnostic BEAM : annotations manquantes distinguées des IDs inexistants ;
  refus pour groupes indépendants insuffisants avant lecture des annotations.

## 4.0.3 — ALPHA (2026-10-10), non publiée / déployée localement

- Bascule exécutée par Claude à 22:36 avec le GO de Romain : sauvegarde qualifiée
  de 1050 éléments, `/ready` 4.0.3, doctor 15 PASS / 0 FAIL. Publication PyPI non autorisée.

- Défauts conservés : C4 scoped_literal + garde 3 ; device auto (MPS sur le Mac
  déployé), précision native, batch 32 ; JSONL par défaut, SQLite S2/S3 opt-in.
- Profil d'encodage attesté : dtypes/devices des paramètres, révision modèle
  connue, versions runtime, empreinte du code encodeur ; visible dans status/ready.
  Un profil inconnu/incomplet/différent reconstruit tous les vecteurs avant lecture.
  Une révision non attestable interdit la réutilisation entre instances.
- Encodage et ajout groupés disponibles explicitement ; aucun résultat CPU/MPS
  historique ne démontre l'équivalence stricte. Pas d'accélération nouvelle activée.
- Pilote de réponses descriptif BEAM observé, C4/BM25 : Luna low lecteur, Astra high
  juge, ChatGPT via codex exec uniquement ; 10 questions, 40 appels maximum, aucun
  score publié avant exécution. Ce juge n'est pas le GPT-4o des publications.
- Qualification des distributions et reconstruction sur copies privées exigées,
  garde fermée jusqu'aux preuves TCP, relais installé et copie/profil/doctor.
  Publication et bascule restent deux GO distincts de Romain.

## 4.0.2 — ALPHA (2026-10-10), publiée sur PyPI

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
- Évaluation post-publication BEAM 100K : le critère préenregistré C4 contre BM25
  n'est pas atteint (ΔRecall +0,017, IC95 [-0,004;+0,040] ; ΔMRR +0,008
  [+0,003;+0,015]). Aucun score d'exactitude ni rang leaderboard revendiqué.

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
