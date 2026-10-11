# Plan commun de distribution v1 — Claude × Codex

2026-10-11. Version consolidée après les analyses indépendantes et la revue adverse de Codex. **Codex signe ce texte ; la contresignature de Claude reste attendue.** Le démarrage D0 est autorisé ; les choix produit et publications gardent leurs GOs distincts.

## 1. Diagnostic partagé et limites

Memory-os-ai 4.0.5 est publié et exploité chez Romain ; SQLite et L7b y sont actifs après qualification. Cette preuve porte sur ce déploiement. Le package reste une ALPHA avec JSONL par défaut, SQLite/tâches opt-in et registre canonique inactif. L’accueil demande encore un opérateur expérimenté.

Contradictions à résoudre : README 4.0.2 sur PyPI 4.0.5 ; S4 annoncé indisponible ; identité de build à fournir manuellement ; `[brief]` et table cl100k absents du quick start ; liens de dépôt inaccessibles depuis PyPI ; métadonnées pauvres ; `server.json` 3.0.1 ; Dockerfile v3 ; GitHub public historique v3/GPL ; matrice client incomplète.

**Positionnement adopté : « Une mémoire locale de projet partagée entre vos assistants : décisions datées, prochaines actions et historique vérifiable, sous votre contrôle. »** Cible initiale : développeurs individuels utilisant au moins deux assistants locaux sur macOS Apple Silicon.

GitHub est gelé : aucun push ni publication Actions. Chaque publication, fiche, post ou contact public demande un GO explicite de Romain ; Romain saisit ses jetons. Aucun fichier d’authentification client n’est copié. Aucune opération sur la production n’est autorisée par ce plan. L11 reste bloqué. Les campagnes de qualité des réponses restent en pause sauf décision distincte.

Les communications décrivent fonctionnalités et preuves acquises. Aucun « top 1 », gain de productivité, supériorité générale ou résultat L11. BEAM recall a échoué à son critère ; LongMemEval-S n’est pas indépendant ; le pilote de réponses est descriptif. Le stockage local n’empêche pas un assistant cloud de recevoir les extraits qu’il consulte.

## 2. Étapes, responsables, critères et dimensionnement

Un jour-agent est une estimation de huit heures de travail d’ingénierie équivalent, pas une promesse de durée de génération. Fourchettes hors attente humaine, téléchargements et revues externes. Ne pas lancer tous les lots en parallèle.

| Étape | Contenu / dépendances | Responsables ; effort | Critère de fin |
|---|---|---|---|
| **D0 — Vérité publique** | README court et actuel, matrice des 11 entrées, limites/licence/incidents, résultats contextualisés ; liens utilisables ; métadonnées ; manifeste MCP relayé ; Docker identifié legacy ; conception extras/identité | Claude : texte/matrice. Codex : intégration/métadonnées/conceptions. **1–2 j-agents** | Revue croisée de chaque affirmation ; aucune contradiction version/S4 ; aucune instruction dépendant d’un chemin privé. D0 local peut être livré, D0 public attend une publication autorisée |
| **D1 — Installation macOS complète** | D0 + contrats quickstart/identité/provisionnement relus. Installation durable versionnée ; identité embarquée vérifiée ; extras séparés ; Granite/table attestés ; token/service ; sélection de clients ; aperçu puis écriture opt-in sauvegardée ; doctor et test réel | Codex : packaging/identité/provisionnement/tests/intégration. Claude : quickstart/service/configs/guide en copie isolée. **5–9 j-agents** | Mac Apple Silicon compatible neuf, Python et clients déjà installés/authentifiés, réseau ≥50 Mb/s : **4/5 nouveaux utilisateurs** écrivent une décision dans A puis la retrouvent dans B en **<10 min**, téléchargements runtime/modèle compris, sans aide développeur. Mesurer installation Python séparément ; publier aussi échecs, volume et assistance |
| **D2 — Exploitation compréhensible** | D1 ; état/start/stop/update/uninstall ; configuration réversible ; backup/restore ; rapport d’aide expurgé. Réutiliser les commandes existantes avant d’en ajouter | Claude : UX/service/runbooks ; Codex : contrats durabilité/qualification. **2–4 j-agents** | Sur magasins privés : redémarrage/upgrade sans perte ; rollback conservant nouvelles écritures/tâches et oublis ; restauration qualifiée ; aucune entrée client étrangère modifiée ; aucun secret dans logs/rapport |
| **D3 — Parcours quotidien** | D1 et récupération D2 ; projet exemple, brief, correction de décision, tâche/prochaine action ; mémoires natives conservées | Claude : parcours/testeurs ; Codex : preuves/diagnostic. **1–2 j-agents + ≥7 jours calendrier** | **4/5** testeurs reprennent à J+7 dans un second client sans intervention développeur. Consentement et échecs documentés ; mesure formative, sans effet causal ni remplacement de L11 |
| **D4 — Découverte ciblée** | Docs utiles préparées dès D0/D1 ; diffusion large après D1/D2/D3. Site, registre officiel, PulseMCP, démonstration ; plugin seulement s’il réduit une friction observée | Claude : site/textes/démo ; Codex : manifests/bundles/qualification. **2–4 j-agents** | URL publique actuelle ; chaque parcours suivi depuis son lien ; deux applications réellement connectées ; manifeste officiel et consommateur testés ; GO distinct pour chaque publication |
| **D5 — Plateformes** | Parcours macOS stabilisé. Linux CPU/service utilisateur, puis Windows/ACL/service ou WSL séparé | Codex : portabilité ; Claude : service/qualification externe. **Linux 4–7 ; Windows 8–15 j-agents**, à re-estimer après audit | Machines propres : install, deux clients, secrets, intégrité, backup/restore, panne/reconnexion et upgrade ; ressources/latences publiées par profil. « Expérimental/non qualifié » tant que le gate échoue |
| **D6 — Distribution élargie** | D1/D2 et demandes mesurées ; Docker v4 après Linux ; Homebrew après source publique actuelle ; application autonome selon abandons | Pilotes à nommer avant démarrage. **Docker 3–5 ; Homebrew 2–3 ; application 10–20 j-agents**, indicatifs | Même gate installé/identité/persistance/upgrade pour chaque format ; maintenance et budget approuvés ; abandon si coût injustifié |

### 2.1 Revue adverse de Codex, point par point

- **D0 — Accord amendé.** Une release future 4.0.6 ne doit pas être annoncée comme publiée. README de Claude = brouillon marqué « candidate ». Les URLs temporaires vont vers les pages existantes ; ne pas inventer un site disponible. Le Dockerfile est marqué legacy, pas qualifié par ajout d’un commentaire.
- **D1 — Accord amendé.** Séparer D1a packaging/ressources, D1b quickstart, D1c essais débutants. Retirer OCR/office/v3 ne retire pas PyTorch/FAISS du profil neuronal : aucun « plusieurs GB économisés » sans mesure. `quickstart` est une fonction nouvelle ; 4.0.6 n’est donc pas une release « sans nouvelle fonctionnalité ». Pas de downgrade silencieux en hash pour passer un gate.
- **D2 — Accord amendé.** Écrire le contrat update/uninstall avant code : distinguer logiciel, service, configs, secrets et données. Désinstallation conserve les données par défaut ; purge séparée explicitement confirmée. Rollback après nouvelles écritures n’est jamais la remise en service d’une vieille copie.
- **D3 — Accord.** Cinq testeurs suffisent pour repérer la friction, pas pour affirmer un gain populationnel. Cas privés, clients déjà authentifiés, aucune copie d’auth ; ne pas relancer L11 indirectement.
- **D4 — Accord amendé.** Préparer l’aide et le site tôt, diffuser après onboarding. GitHub OAuth ne nécessite pas de push : le registre n’attend pas forcément le dégel. Un plugin, une extension VS Code et un serveur MCP sont des formats distincts ; éviter trois wrappers sans besoin démontré.
- **D5 — Accord.** Linux n’entre pas dans D1/4.0.6 par opportunisme. WSL n’est pas une preuve Windows native. Mac Intel n’hérite pas de la qualification MPS Apple Silicon.
- **D6 — Accord amendé.** Pas de propriétaire « à assigner » au démarrage d’un lot. Le budget/maintenance précèdent tout format supplémentaire ; Docker n’offre pas le MPS natif macOS.

Corrections de l’analyse de Claude : le runtime force le mode hors ligne par défaut ; un modèle absent ne garantit pas un téléchargement au premier lancement. Mem0 a une offre OSS/autohébergeable ; Graphiti est OSS ; les mémoires natives ont aussi import/export. Ces offres ne doivent pas être caricaturées comme seulement cloud ou totalement enfermées. GitHub gelé ne rend pas tous les catalogues techniquement inaccessibles.

### 2.2 Séquence et contenu de la candidate 4.0.6

1. D0 local : métadonnées/manifeste relayé/avertissement Docker, accueil et matrice relus, contrats D1.
2. D1a : extras import-safe, identité embarquée et ressources provisionnables depuis le wheel ; contrôle de taille/temps.
3. D1b : quickstart macOS idempotent, aperçu, choix clients, `--apply` explicite, sauvegardes et restauration des configs.
4. D2 minimum : commandes de diagnostic et runbooks start/stop/update/uninstall réversibles ; premier backup/drill sur copie.
5. D1c : cinq essais débutants ; corriger et refaire les essais échoués en conservant les preuves.
6. T05 complet : wheel/sdist/installations hors dépôt, v3 avec son extra, v4, TCP/relais installés, lint/twine, copy/drill ; puis seulement candidate qualifiée soumise à Romain. D3 poursuit le pilote à J+7 ; le lancement large D4 attend ses résultats.

La version ne change qu’à la préparation réelle de la candidate. D0 modifie encore les métadonnées de travail 4.0.5 ; **il ne remplace pas les artefacts PyPI déjà publiés**. Pas d’activation SQLite/registre, changement d’encodeur ou nouveau modèle dans D0. Les GOs d’installation de test, téléchargement et publication restent séparés. Les gros jobs externes restent séquentiels et gardés (disque ≥5 GB, swap ≤8,5 GB), au plus deux jobs lourds autorisés, sans simultanéité imposée.

## 3. Décisions produit et points restant à acter

1. **Nouvelles installations SQLite + tâches** : proposition soutenue par les deux analyses. Qualifier un profil candidat explicite, puis obtenir le choix de Romain **avant de figer les defaults de release**. Anciennes installations inchangées ; aucune migration automatique. Activation du registre séparée, après UX de création de projet.
2. **Méthode durable** : proposer `uv tool install` versionné comme parcours de référence ; venv/pip en secours ; pipx après qualification. `uvx` autorisé pour un relais/essai, pas comme résidence du daemon. Choix à figer dans la revue D1.
3. **Budget ressources** : mesurer avant de contractualiser le seuil « <2 GB ». Rapporter téléchargements, taille venv, cache modèles et pic disque, avec versions/profil. Le gate de dix minutes demeure ; pas de plafond arbitraire prétendument déjà atteignable.
4. **Hébergement/publication** : Romain choisit domaine/hébergeur/budget. Site local préparé pendant D0 ; URL publique ajoutée seulement après GO. Déblocage GitHub reste une décision séparée ; aucun hébergeur de remplacement pour contourner le gel du dépôt.
5. **Identité embarquée et canonicalisation des clients** : contrats D1 relus par Claude avant implémentation. Identité de release n’est pas signature du code ; propriétaire client déclaratif n’est pas identité authentifiée.

Désaccords résiduels à résoudre par contresignature : aucun sur l’ordre ou le positionnement proposé ; la méthode durable, le budget disque et les defaults de nouvelles installations restent des **choix ouverts**, pas un accord déjà acquis.

## 4. Mesure, preuves et périmètres

Avec consentement et sans télémétrie imposée : tentatives, succès d’installation, première reprise, retour à J+7, raisons d’abandon et minutes d’assistance. Téléchargements/stars ≠ utilisateurs actifs. Les mesures portent sur le profil qualifié ; encoder/device/dtype/batch et corpus accompagnent chaque chiffre. Aucun achat de visibilité avant diagnostic des abandons.

**Périmètre Codex maintenant :** `pyproject.toml`, `server.json`, `Dockerfile`, `v4/docs/DISTRIBUTION_PLAN_COMMUN_v1.md`, `v4/docs/DISTRIBUTION_D0_DESIGN_v1.md`, preuve locale privée et livraison Git par bundle. Aucun changement runtime ni dépendances dans D0.

**Périmètre Claude en parallèle :** rédiger `~/memory-os-local/distribution/README_4.0.6.md` et `~/memory-os-local/distribution/docs/CLIENTS.md`, plus sources des preuves/URLs. Marquer 4.0.5 actuel et 4.0.6 candidate ; distinguer configuration/handshake/usage réel et package/déploiement Romain. Ne pas écrire `README.md` ou `docs/CLIENTS.md` du clone pendant le tour Codex. Codex relit et intègre ensuite.

Les docs publiques doivent expliquer les préparations encore manuelles tant que D1 n’existe pas. Les corrections locales ne rendent pas la page PyPI actuelle ni GitHub actuels par magie. Les preuves de D0 ne valent pas qualification de release.

## 5. Signatures

- **Codex — approuvé le 2026-10-11**, avec les amendements de §2.1 incorporés dans cette version. D0 démarré dans le périmètre défini, aucune publication.
- **Claude — auteur du draft ; contresignature du présent texte amendé attendue.** Aucune signature n’est ajoutée à sa place.
- **Romain — GOs produit, budgets, téléchargements, production et publications conservés.** Ce document ne vaut pas leur obtention.
