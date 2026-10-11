# Changelog

## 4.0.6 — candidate ALPHA (2026-10-12), non publiée / non déployée

- D0 : métadonnées et liens publics corrigés, manifeste MCP stdio relayé en
  4.0.6 (brouillon non publié), Docker explicitement historique v3, README et
  matrice de 11 clients distinguant configuration, connexion et usage réel.
- D1 : dépendances documents/historique déplacées vers `[documents]` et
  `[legacy-v3]` ; le core neuronal conserve PyTorch/Granite. Les commandes v3
  signalent l'extra manquant ; aucun gain de taille annoncé sans mesure neuve.
- Identité de build embarquée et vérifiée depuis wheel/sdist, source/payload
  contrôlés ; `build_unqualified` si descriptor absent ou modifié. Identité
  exposée dans ready/doctor ; ce contrôle n'est pas une signature de confiance.
- Profil qualifié épinglé exactement : torch 2.10.0, sentence-transformers
  5.2.3, transformers 5.2.0, numpy 2.4.2 et dépendances numériques/tokenizer.
  Quickstart refuse un profil installé différent avant activation. La liste
  de versions ne prouve pas l'équivalence entre toutes les machines.
- Provision explicite Granite/cl100k avec révisions/empreintes, aperçu sans
  écriture, téléchargement uniquement après consentement ; offline par défaut.
- Quickstart macOS : choix des clients, aperçu sans écriture, `--apply`, consentements
  téléchargement/demo séparés, configurations sauvegardées/journalisées,
  restauration sémantique de notre entrée ; refus des fichiers invalides,
  services étrangers et magasins non vides. Aucun job backup planifié implicitement.
- Décision Romain du 2026-10-11 : **installation neuve via quickstart = SQLite
  + tâches**, alternative `--backend jsonl`. Aucune migration implicite des
  installations existantes ; défaut Config JSONL et registre canonique inactif conservés.
- Correctifs de packaging/refus : helper PEP 517 disponible dans le build direct
  et depuis sdist ; extra `legacy-v3` évite le marqueur de version ambigu `v3` ;
  contrôle de tous les marqueurs produits ; identité invalide refusée avant token.
  `--warm` charge réellement la recherche SQLite ; mode froid sans modèle préservé,
  test séparé hash/warm. Qualification TCP par les **28 identités attendues**.
- Préparation finale : versions alignées, notes et docs de release embarquées ;
  aucun nouveau changement fonctionnel au-dessus de `62d906f`.
- Qualification de ce commit/ces artefacts et essai d'installation neuve encore
  requis. Aucun gain de productivité mesuré : L11 non exécuté ; LME-S/BEAM
  descriptifs, profils/limites explicités, aucun classement officiel revendiqué.
  Publication, bascule et GitHub restent soumis aux décisions distinctes de Romain.

## 4.0.5 — ALPHA publiée / déployée (2026-10-11)

- Isolation HOME/XDG/AppData des tests v3/v4 et garde d'écriture dans le HOME réel ;
  correction des callbacks setup historiques pouvant modifier les configs utilisateur.
- Templates MCP relayés ; erreurs préflight retryables avant envoi distinctes des
  résultats incertains après envoi. Aucun rejeu automatique des mutations.
- Qualification : comparaison sémantique de notre entrée dans les fichiers clients,
  SHA strict des fichiers du dépôt. Suites, TCP, copie et relais installés qualifiés.
- SQLite S4 et L7b livrés opt-in : sauvegarde/drill, rollback après nouvelles écritures,
  tâches/CAS, historique et reçus atomiques ; liste filtrée/paginée en SQL au schéma7.
  Mesure 10k par Claude : CAS p95 5,5 ms, list50 p95 0,52 ms ; preuve synthétique.
- Démarrage SQLite/tâches à froid sans modèle ; recherche initialisée explicitement ;
  ready expose retrieval_ready et capabilities.policies.write_mode.
- Romain a approuvé publication/bascule puis activation séparée SQLite+tâches le
  2026-10-11. Cette activation concerne son installation, pas le défaut Config
  du package. Registre inactif et profil auto/MPS/native/batch32/C4 conservés.
- Incident tests 4.0.4 documenté : ne pas exécuter cette ancienne suite avec le HOME réel.

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
