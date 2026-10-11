# D0 / contrats proposés D1 — installation légère et identité embarquée

Conception historique du 2026-10-11, depuis relue/implémentée en D1.
État 2026-10-12 : sources **4.0.6 candidate**, non publiée/non déployée ;
le groupe proposé `v3` ci-dessous a finalement été nommé `legacy-v3` (marqueurs PEP 508).
Lors de D0, seules les métadonnées changeaient, sans modification runtime/defaults
ni requalification/publication de 4.0.5. La préparation finale est documentée
dans `docs/releases/v4.0.6.md`.

## 1. Installation légère : conserver le produit réellement livré

`pyproject.toml` reste inchangé sur les dépendances dans D0. Le poids d’un modèle n’est pas le poids du runtime. Retirer PDF/OCR/office ne supprime pas les dépendances ML. Le volume réellement économisé sera mesuré sur environnement neuf ; aucune cible « <2 GB » acquise ou garantie.

Proposition D1a, à figer après revue d’imports :

| Groupe | Contenu cible | Condition |
|---|---|---|
| Core sémantique v4 | MCP, pydantic, numpy, faiss-cpu, sentence-transformers, transformers, huggingface_hub ; torch transitif | C4/Granite garde son comportement. Ne pas vendre un core sans torch tant que v4 a besoin d’encoder |
| `ops` | cryptography | Inchangé ; parcours complet installe ops |
| `brief` | tiktoken 0.12.0 + provisionnement séparé de la table | Installer l’extra ne prouve pas que la table est disponible |
| `documents` | pymupdf pour l’ingestion PDF v4 | Déjà import optionnel dans `_h_ingest` ; erreur explicite sans extra |
| `v3` | dépendances nécessaires au moteur document historique : pymupdf, pytesseract, python-pptx, python-docx, Pillow, requests, plus core partagé | Auditer imports directs/dynamiques et imports transitifs ; ne pas rendre import/CLI v3 silencieusement cassés |
| `audio`, `doc-legacy`, `cloud-*` | extras historiques, aliases préservés si besoin | Déclarer leur dépendance au groupe v3 quand nécessaire ; vérifier chaque commande documentée |
| `dev` | tests/build/lint ; fixture v3 nécessite `[legacy-v3,dev]` | La CI ne doit pas cacher un défaut de core derrière un environnement full |

Ne pas mélanger ce premier découpage avec un nouveau backend vectoriel, modèle plus petit, ONNX ou core entièrement sans ML. Un futur mode work-only allégé nécessiterait sa propre interface explicite et qualification ; aucun fallback hash pour simuler une installation neuronale réussie.

Preuves D1 : wheel et sdist installés hors dépôt sur core seul, `[ops,brief]`, `[legacy-v3,ops,brief]` ; `pip check`, cinq console scripts ; appel v3 sans extra → message d’installation utile, pas traceback ; imports v4 sans OCR/office ; tests v3 avec extra ; model/capture/ranking C4 inchangés. Mesurer débit réseau, octets reçus, venv, cache Granite/table, pic temporaire, temps total et temps modèle séparé. Nettoyage volontaire des environnements de test uniquement, aucune preuve supprimée.

## 2. Identité embarquée : contrat proposé

Problème : `readiness.build_identity()` accepte actuellement un SHA fourni par env ou lit Git ; le wheel installé ne contient pas de checkout, donc un débutant doit fournir `MEMORY_V4_BUILD_COMMIT`. Ce SHA seul n’est pas une signature ni une vérification du contenu.

Proposition : générer pendant le build un descripteur installé, lu via `importlib.resources`, sans Git, réseau ou modèle au runtime. Champs : version, source_commit complet, schéma du descripteur, arbre applicatif source et empreintes SHA-256 des fichiers runtime attendus. La génération utilise un export propre d’un commit scellé ; l’epoch et les chemins sont déterministes.

- Les empreintes portent sur le **payload installé**, après toute transformation de packaging ; comparer wheel/sdist. Le descripteur s’exclut explicitement de son propre inventaire. Aucun SHA du wheel inscrit dans le wheel : ce serait circulaire.
- Le manifeste externe de qualification enregistre le SHA des artefacts, du descripteur et l’inventaire. Version du package, descripteur et reçu externe doivent correspondre. Le source_commit est le commit des sources construites, pas forcément le commit ultérieur du reçu de qualification.
- Le runtime vérifie présence, formats, version et fichiers attendus ; manquant/modifié → `build_unqualified`, jamais « commit attesté » silencieux. Examiner le coût d’une vérification en cache par processus ; une modification après ouverture ne doit pas être présentée comme vérifiée en continu.
- Wheel installé : descripteur valide prioritaire. Env concordante acceptée pour compatibilité opérateur ; env contradictoire refuse. Sans descripteur, packages de développement/restores anciens gardent un chemin opérateur explicitement marqué déclaratif selon le contrat relu ; aucun effacement du contrôle de readiness.
- Checkout/editable : Git propre vérifié ou mode développement non qualifié ; ne pas réutiliser automatiquement le descripteur d’un ancien build. Tester imports depuis un sous-dossier non suivi.
- Les empreintes/HTTPS/manifeste connu vérifient la cohérence. Un attaquant qui remplace code **et** descripteur peut fabriquer cette cohérence : signature fournisseur et source de confiance sont des sujets séparés. Pas de claim « supply chain signée » sans clés/signature vérifiées.
- Source d’identité exposée dans `/ready`, doctor et release proof. Conserver le modèle, device/dtype/batch, révision et namespace des vecteurs ; le changement de mécanisme d’identité ne doit pas entraîner un réencodage ou un mélange de profils.

Matrice avant implémentation : wheel/sdist même identité et payload ; version divergent ; fichier absent/modifié ; descripteur invalide ; env compatible/contradictoire ; checkout propre/sale ; editable ancien ; absence Git ; aucun accès réseau ; aucun chargement modèle ; copy qualification et relais annoncent la version de release. Garder les preuves de refus.

## 3. Manifeste MCP : brouillon relayé, gate de consommateur

`server.json` passe de 3.0.1 à la version de package de travail 4.0.5. Il annonce la dépendance à un service partagé existant, token **fichier**, URL loopback et executable `memory-os-relay`. Aucun daemon neuf par lancement client ; aucun bearer en texte dans le manifeste.

Schéma officiel : [server.json](https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json), [spécification](https://github.com/modelcontextprotocol/registry/blob/main/docs/reference/server-json/generic-server-json.md). Les `runtimeArguments` ciblent uvx : `--from memory-os-ai[ops]==4.0.5 memory-os-relay`. Certains consommateurs ajoutent ensuite l’identifiant du package ; le main du relais actuel ignore argv. **Ce placement n’est pas une garantie universelle du schéma.**

Le champ publisher-provided conserve `publication_ready=false` : **marqueur de brouillon, pas un verrou que le registre serait tenu d’appliquer**. Aucun automatisme ne publie ce fichier. Avant D4 : validation publisher/schéma, observation de l’argv réellement construit par un consommateur VS Code/registre et lancement installé sur service privé ; initialize/list-tools/version/status, token invalide/serveur absent, absence d’ouverture d’un second magasin. Si le consommateur lance le serveur au lieu du relais ou rejette cette convention, ne pas publier : utiliser une méthode manuelle qualifiée ou un format d’artifact qui désigne l’executable sans ambiguïté. Métadonnée custom `launch` n’est pas un champ d’exécution standard.

Commande manuelle versionnée, **à titre de définition, non exécutée ici** :

```sh
uvx --from 'memory-os-ai[ops]==4.0.5' memory-os-relay
```

La résidence du daemon utilise l’environnement durable D1, pas uvx. Le manifeste sera aligné sur 4.0.6 quand cette candidate sera préparée et relu après le découpage des extras. Marqueur PyPI `mcp-name` conservé ; GitHub OAuth possible sans push, mais GO de publication toujours requis.

## 4. Quickstart : frontières à respecter côté Claude

Claude rédige d’abord le contrat : dry-run/aperçu par défaut, sélection de deux clients, `--apply` explicite, aucune copie d’auth. Scopes/configs effectifs (projet/utilisateur), variantes d’applications, PATH/interpréteur absolu et services identifiés. Garder un journal de modifications et des backups ; restauration sémantique de notre entrée sans écraser les changements indépendants d’une application. Fichier invalide → refus, jamais remplacement complet.

Préparer identité et ressources avant création/activation du service. Après disponibilité : doctor, handshake de chaque client choisi et save/search sur projet de démonstration privé. Un timeout, quota ou absence de connexion devient un état explicite ; aucune mutation incertaine répétée, aucun succès d’installation déclaré sur seule lecture de config. Le parcours source actuel n’est pas remplacé par une commande encore non implémentée.

Définir en revue le traitement d’un install existant, port pris, job étranger, token existant, config modifiée pendant preview, table/model manquants et dossier de données non vide. Le quickstart n’effectue pas de migration SQLite ou de purge sans choix séparé. Linux/Windows restent hors D1.

## 5. Preuves D0 et revue externe attendue

- Contrôle de cohérence local : 11 checks verts (versions, dépendances/commandes inchangées, URLs, Alpha/macOS, pin/relais, token-path, namespace, brouillon fermé, legacy Docker, main actuel sans argv).
- Validation du schéma pyproject par setuptools hors réseau : PASS. Vérification distante des classifiers Trove non effectuée.
- `v4/tests/test_ops_doctor.py` : 32 pass, sans modèle/inférence. Lint E9/F projet et `git diff --check` verts.
- Première sonde TOML a refusé une table URLs initialement placée avant dependencies ; placement corrigé et nouvelle sonde verte. Trace conservée dans la preuve privée.
- `mcp-publisher validate server.json` : non qualifié, DNS du registre inaccessible. Le binaire installé appelle `/v0/validate` ; ne pas présenter ce refus réseau comme une validation locale ni comme une publication.

Preuves privées : `.local-qualification/distribution-d0-v1/`. Aucun build/requalification 4.0.5 ni gros job (disque observé ~3 GB, sous seuil). Claude doit relire les deux contrats D1, contresigner le plan, puis exécuter uniquement la validation du manifeste, sans login/publish :

```sh
mcp-publisher validate server.json
```

Le test réel de consommateur demeure D4, après D1. Aucun nouveau téléchargement, installation utilisateur ou test de production demandé par ce reçu.


## 6. Revue externe D0 / accord D1 — 2026-10-11

Claude a contresigné le plan et les contrats extras/identité. Il a exécuté `mcp-publisher validate server.json` avec mcp-publisher 1.4.1 : valide contre registry.modelcontextprotocol.io, sans login/publish. Cette preuve externe complète le refus DNS local précédent, conservé ; elle ne qualifie pas encore un consommateur D4. Doctor expliquera la suppression possible de l’ancien SHA env ; `/ready` distinguera `embedded_descriptor`, `env_declared`, `git_checkout`, `unqualified`.
