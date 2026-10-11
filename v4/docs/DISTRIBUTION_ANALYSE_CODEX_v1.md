# Distribution de memory-os-ai 4.0.5 — analyse indépendante de Codex

**2026-10-11. Étape 1 : propositions, sans accord commun à ce stade.** Analyse fondée sur le code du clone, les artefacts et les sources officielles consultées aujourd’hui. L’analyse de Claude n’a pas été lue. Aucun code modifié, téléchargement de modèle, appel d’inférence ou acte de publication.

**Diagnostic : le moteur a une base opérationnelle solide, mais sa distribution demande encore un opérateur expérimenté.** La priorité est de rendre la première installation et la première reprise entre deux clients fiables. Accumuler des inscriptions dans des annuaires avant cela amplifierait les échecs d’installation.

L11 reste bloqué suivant la décision de Romain. Aucun gain mesuré de productivité, de reprise entre agents ou de supériorité générale sur les concurrents ne peut être annoncé.

## A. État actuel : produit livré, accueil désynchronisé

La [version 4.0.5 sur PyPI](https://pypi.org/project/memory-os-ai/4.0.5/) est publiée avec wheel et sdist, Python ≥ 3.11, statut Alpha et licence LGPL-3.0-or-later. Ses empreintes correspondent aux artefacts qualifiés. La licence du clone comprend le texte LGPL et son compagnon GPL ; elle n’est plus une décision en suspens.

Les capacités disponibles comprennent mémoire par projet, recherche hybride C4, historique temporel, briefs compacts, relais authentifié, résolution des requêtes incertaines, verrou de maintenance et diagnostic. SQLite, sauvegarde en ligne, restauration privée et tâches/blocs avec CAS sont livrés derrière configuration. Selon le compte rendu de Claude, SQLite et les tâches sont désormais actifs chez Romain ; **cela ne change pas les défauts du package**, qui restent JSONL et tâches désactivées. Le registre canonique demeure inactif.

Les preuves utiles à présenter sont la qualification des distributions installées, les essais TCP/panne/reconnexion, l’absence de rejeu automatique des mutations, les restaurations et les mesures précisément contextualisées. Exemple : 400 mesures de briefs, maximum 3 678 octets et 1 349 tokens ; liste de 50 tâches à 10 000 tâches, p95 0,52 ms sur fixture synthétique. Ces résultats ne prouvent ni l’onboarding d’un inconnu ni toutes les plateformes.

**Défauts d’accueil constatés :**

- Le README, également rendu sur PyPI 4.0.5, annonce encore 4.0.2. Il déclare S4 indisponible alors que les sauvegardes SQLite sont livrées. Les guides accumulent des états historiques contradictoires ; certains dossiers disent encore « candidate », conformément à leur date de rédaction, sans reçu public actuel facilement identifiable.
- Le parcours rapide installe seulement `[ops]`. Les briefs compacts exigent aussi `[brief]`, `tiktoken==0.12.0`, une table locale attestée et `MEMORY_V4_CL100K_PATH`. Le script de provisionnement actuel vise le clone et `.local-qualification`, pas un utilisateur du wheel.
- Une installation PyPI doit fournir manuellement `MEMORY_V4_BUILD_COMMIT` ; sinon `/ready` refuse la qualification. `setup` ne propage pas le chemin cl100k dans ses templates launchd ordinaires. Copier le quick start ne suffit donc pas à reproduire notre installation.
- `server.json` annonce 3.0.1 ; le Dockerfile lance le serveur v3 et ne copie pas les sources v4. Ces fichiers ne représentent pas une distribution 4.0.5 qualifiée.
- Le registre client contient **11 entrées**, dont Windsurf et Devin distincts. Le tableau public est incomplet. Une configuration reconnue par doctor ne prouve pas que l’application a appelé les outils.
- `pyproject.toml` n’a pas de table `project.urls`. Plusieurs parcours reposent sur des chemins du dépôt plutôt que sur une documentation utilisateur versionnée accessible depuis le package.

Le [dépôt GitHub public consulté](https://github.com/romainsantoli-web/Memory-os-ai) expose encore un accueil v3 et un badge GPL. PyPI et GitHub présentent donc deux produits différents. Le gel empêche actuellement de corriger le dépôt public. PyPI et ce dépôt sont des canaux existants vérifiés ; je n’ai pas de preuve suffisante pour affirmer une présence actuelle dans les autres annuaires.

## B. Frictions du nouvel utilisateur

| Étape | Friction réelle | Réponse proposée |
|---|---|---|
| Installer | Python, environnement virtuel, PATH ; dépendances natives et ML lourdes malgré un petit wheel | Une seule méthode durable, version épinglée, contrôle préalable et estimation disque/téléchargements |
| Préparer les ressources | Granite n’est pas livré ; fonctionnement hors ligne supposant un cache déjà présent | Provisionnement explicite avec progression, taille, révision, empreintes et erreur actionnable |
| Démarrer | launchd, plist, interpréteur absolu, identité du build, journal et readiness | Assistant d’installation réexécutable, vérification avant activation, démarrage/arrêt/désinstallation documentés |
| Sécuriser | Jeton 0600, clé de sauvegarde distincte, rotation et récupération | Création automatique du jeton déjà disponible ; expliquer les deux secrets et garder leurs valeurs hors des configurations |
| Connecter | Snippets multiples, formats et scopes différents, override de projet, application qui réécrit sa configuration | Sélection des seuls clients utilisés, aperçu du changement, fusion sauvegardée et vérification réelle |
| Comprendre | 23 outils annoncés dans le déploiement ; sauvegarde, recherche et tâches ne vont pas de soi | Trois gestes visibles : reprendre, enregistrer une décision, transmettre une prochaine action |
| Revenir demain | Service arrêté, projet renommé, souvenir non enregistré, tokenizer absent | Bref état de santé, capture assistée explicite, erreur utile et procédure de récupération |

Le modèle [Granite actuellement publié](https://huggingface.co/ibm-granite/granite-embedding-97m-multilingual-r2/tree/main) contient un fichier de poids d’environ 195 MB et un tokenizer d’environ 25 MB. Le dépôt complet contient d’autres formats : sa taille totale n’est pas celle du téléchargement nécessaire. PyTorch et les autres dépendances s’ajoutent. Il faut mesurer le volume réellement installé plutôt qu’annoncer « léger » à partir du wheel.

**Plateformes :** macOS Apple Silicon est le premier parcours à qualifier pour des débutants. `auto` utilise MPS lorsqu’il est disponible ; conserver le profil attesté, sans reprendre les chiffres d’un autre dtype/batch comme promesse. Linux nécessite des chemins client corrects et un service utilisateur adapté, puis une qualification réelle CPU. Windows nécessite davantage : gestion des secrets par ACL, primitives POSIX actuellement utilisées (`O_NOFOLLOW`, UID), service et dépendances natives à adapter. WSL éventuel doit avoir son propre statut, notamment pour le réseau et les clients Windows. Un wheel `py3-none-any` n’est pas une certification multiplateforme.

La recherche locale ne facture pas d’API. L’assistant qui extrait les faits et utilise les outils consomme néanmoins son abonnement/quota ou son API. Les souvenirs transmis à un client cloud peuvent quitter la machine dans son contexte : « stockage local » ne signifie pas « aucune donnée ne quitte jamais le Mac ».

## C. Canaux : ordre et dépendance au gel GitHub

| Canal | Intérêt et condition | Effet du gel / priorité |
|---|---|---|
| **PyPI** | Canal de référence existant ; README, extras et métadonnées cohérents avec la release | Préparable sans push. Prochaine publication uniquement avec GO et jeton saisi par Romain. **P0** |
| **Installation durable uv/pipx** | Simplifier l’environnement et fixer l’interpréteur du service/relais | Indépendant d’un push ; qualifier les extras et mises à jour. **P0** |
| **uvx** | Essai ponctuel utile ; cache d’environnement jetable | Ne pas en faire le chemin par défaut d’un daemon durable. **P2** |
| **Site documentation/landing** | Une URL actuelle : démarrage, plateformes, clients, sécurité, limites, preuves | Préparer localement ; hébergement hors GitHub possible, publication avec GO. **P0/P1** |
| **Registre MCP officiel** | Découverte structurée adossée à PyPI ; corriger et valider `server.json` | GitHub OAuth pour le namespace existant ne nécessite pas un push ni Actions ; publication avec GO. **P1** |
| **PulseMCP** | Annuaire avec soumissions et intégration du registre officiel | Pas de nécessité de push démontrée pour la soumission ; fiche/source actuelles requises. **P1** |
| **Glama** | Découverte, inspection et classement de dépôts MCP | Une fiche adossée au dépôt figé risque de présenter v3 ; confirmer le mode d’actualisation. **P2** |
| **Smithery** | Distribue notamment des bundles locaux MCPB | Nécessite un bundle qualifié : notre relais suppose un daemon/token déjà préparés. Pas de publication du serveur loopback comme endpoint public. **P2** |
| **mcp.so** | Annuaire supplémentaire ; formulaire demandant une URL de dépôt | Faible priorité tant que la source publique est obsolète ; aucun achat de visibilité proposé. **P3** |
| **Plugin Claude Code / extension Desktop MCPB** | Emballer règles, hooks et raccordement ; réduire le copier-coller | Essai local/ZIP possible ; marketplace publique avec GO. Git distant bloqué si GitHub. **P1/P2** |
| **Cursor / VS Code** | Boutons et configurations d’installation, galerie MCP VS Code | Bouton ≠ installation du daemon ; valider deux clients avant demande d’inscription. Extension dédiée seulement si nécessaire. **P1/P2** |
| **Homebrew** | Installation/service macOS plus familier | Source et artefacts stables requis ; tap GitHub bloqué par le gel. Pas un raccourci à la qualification. **P2** |
| **Docker** | Déploiement Linux reproductible pour utilisateurs techniques | Refaire/qualifier l’image v4 ; persistance, secrets, backups et CPU ; pas de MPS natif macOS dans cette image. Registry hors GitHub possible avec GO. **P2** |
| **Binaire/application autonome** | Éviter Python visible pour les non-développeurs | Packaging ML multiplateforme, signatures, mises à jour et coût de maintenance : trop tôt. **P3** |
| **Posts / démonstrations / communautés** | Acquisition ciblée de développeurs multi-assistants | Brouillons possibles maintenant ; chaque publication/contact public nécessite GO. Après onboarding réussi. **P2** |

Les mécanismes ci-dessus sont vérifiés dans les documentations du [registre MCP : PyPI](https://github.com/modelcontextprotocol/registry/blob/main/docs/modelcontextprotocol-io/package-types.mdx) et de son [authentification](https://github.com/modelcontextprotocol/registry/blob/main/docs/modelcontextprotocol-io/authentication.mdx), de [uv](https://docs.astral.sh/uv/concepts/tools/), [PulseMCP](https://www.pulsemcp.com/api), [Smithery](https://smithery.ai/docs/build/publish), [Claude Code](https://code.claude.com/docs/en/plugin-marketplaces), [VS Code](https://code.visualstudio.com/docs/agent-customization/mcp-servers) et [Cursor](https://cursor.com/docs/context/mcp). [Glama](https://glama.ai/mcp/servers) et le [formulaire mcp.so](https://mcp.so/Submit?type=server) justifient leur examen, pas une garantie d’acceptation.

Le registre officiel vérifie notamment le marqueur `mcp-name` du README PyPI, déjà présent. Il faut aussi rendre explicite le lancement du **relais vers un service partagé**, pour éviter qu’une installation générique lance une nouvelle autorité par client. Une fiche n’installe pas notre architecture complète. Le gel GitHub est une contrainte de publication du dépôt ; il ne doit être ni contourné ni présenté comme bloquant techniquement tous les autres canaux.

## D. Positionnement honnête

**Proposition de valeur : « Une mémoire locale de projet partagée entre vos assistants : décisions datées, prochaines actions et historique vérifiable, sous votre contrôle. »** Première cible : développeurs individuels utilisant au moins deux assistants sur leur machine. L’élargissement aux équipes exige d’autres garanties d’accès, de synchronisation et d’exploitation.

| Alternative | Ce qu’elle apporte déjà | Notre angle défendable et notre manque |
|---|---|---|
| Mem0 | Moteur de mémoire, bibliothèque, plateforme et serveur autohébergeable | Intégration locale des clients et absence de LLM d’extraction côté serveur ; pas de supériorité de précision démontrée |
| Zep / Graphiti | Graphes temporels, contexte ; offre gérée et cœur OSS | Décisions/tâches locales et exploitation individuelle ; moins de gouvernance et d’intégrations applicatives démontrées |
| Letta | Agents persistants, blocs de mémoire partagés, outils | Couche commune pour assistants existants ; ne remplace pas sa plateforme d’agents |
| Supermemory | Mémoire MCP entre clients, service géré et OAuth | Contrôle local et corrections explicites ; nous demandons davantage d’installation et d’exploitation |
| Mémoires natives | Rappel et personnalisation intégrés, parfois mémoire de projet et import/export | Coexister ; apporter un état commun explicite entre outils, sans prétendre que les natifs n’ont pas de mémoire |

Sources : [Mem0 OSS](https://docs.mem0.ai/open-source/overview), [Zep/Graphiti](https://help.getzep.com/zep-vs-graphiti), [Letta](https://docs.letta.com/v1-sdk/concepts/stateful-agents), [Supermemory MCP](https://supermemory.ai/mcp/), [mémoires ChatGPT/Codex](https://learn.chatgpt.com/docs/customization/memories) et [mémoire Claude](https://support.claude.com/en/articles/11817273-use-claude-s-chat-search-and-memory-to-build-on-previous-context). Ces descriptions établissent leur offre, pas des résultats comparatifs vérifiés par nous.

« Tous les assistants oublient » et « seule mémoire universelle » seraient faux. « Plus complet que les concurrents » n’est pas établi. Notre avantage potentiel est la combinaison du contrôle local, des contrats de mutation et de la continuité du travail ; son utilité chez de nouveaux utilisateurs reste à mesurer.

Les résultats restent séparés : retrieval LongMemEval-S favorable mais non indépendant ; BEAM échoue au critère recall ; pilote de réponses dix questions descriptif ; **L11 non exécuté**. Aucun classement officiel, aucun pourcentage de productivité. « Incontournable » et « top 1 » sont des objectifs.

## E. Priorités proposées et critères à figer ensemble

| Ordre | Livrable proposé | Critère vérifiable de fin |
|---|---|---|
| **D0 — Vérité publique** | README court, état actuel, matrice clients/plateformes, limites, provenance/licences, liens et notice d’incident v4.0.4 ; archives techniques séparées | Revue croisée : chaque affirmation reliée à une fonctionnalité ou preuve datée ; zéro contradiction de version/S4 ; parcours entièrement accessible sans chemin privé |
| **D1 — Installation macOS complète** | Environnement durable, identité embarquée vérifiable, `[ops,brief]`, provisionnement attesté, service et deux clients sélectionnés | Depuis un Mac neuf compatible, clients déjà installés/authentifiés, réseau ≥ 50 Mb/s : **4 utilisateurs sur 5** installent puis retrouvent dans B une décision écrite dans A en **< 10 min**, téléchargements inclus ; aucun dépannage développeur, aucune copie d’auth |
| **D2 — Exploitation compréhensible** | Commandes état/start/stop/update/uninstall ; aperçu des configurations, sauvegarde/restauration et aide anonymisable | Sur magasin privé : redémarrage et mise à jour sans perte, rollback après nouvelle écriture, restauration vérifiée ; aucune modification d’entrée client étrangère, aucun secret dans les logs |
| **D3 — Parcours quotidien** | Exemple de projet, bref de reprise, décision corrigée, tâche avec prochaine action, coexistence avec les mémoires natives | Cinq testeurs volontaires : **4/5** reprennent le même projet avec un second client à J+7 sans intervention développeur ; résultat publié seulement après collecte consentie, sans causalité de productivité |
| **D4 — Découverte ciblée** | Site + registre officiel + un annuaire, démonstration et plugin minimal si utile | Artefacts/fiches versionnés, URLs accessibles, installation réellement suivie depuis chaque lien ; deux applications qualifiées en bout en bout ; GO explicite pour chaque publication |
| **D5 — Extension des plateformes** | Linux CPU d’abord ; Windows ensuite selon adaptation sécurité/service | Machines propres : installation, sauvegarde/restauration, panne/reconnexion, deux clients et limites de ressources vérifiés ; statut public « expérimental » tant que ce gate échoue |
| **D6 — Distribution élargie** | Homebrew/Docker puis application autonome si les abandons le justifient | Chaque nouveau format passe les mêmes preuves d’installation, identité, persistance et upgrade ; propriétaire de maintenance nommé, budget approuvé |

D0 et la conception D1 précèdent toute campagne de diffusion. Codex pourrait prendre packaging/identité/contrats et tests ; Claude onboarding/service/clients et documentation pratique ; chacun relit l’autre. Romain valide budget, téléchargements, activation et publication. Les testeurs manipulent uniquement des projets de démonstration privés avec leurs clients déjà authentifiés ; **ces essais d’onboarding ne remplacent pas L11**.

Je propose d’évaluer **SQLite pour les nouvelles installations** afin d’exposer les garanties transactionnelles et les tâches dès le démarrage. C’est une décision produit à qualifier, pas une modification implicite des défauts ni une migration automatique d’anciens utilisateurs. Le registre reste inactif tant que la création d’un projet n’est pas simple.

Le suivi doit compter installations tentées, installations fonctionnelles, première reprise et retour à J+7, avec consentement et sans télémétrie imposée. Téléchargements PyPI, étoiles et inscriptions ne mesurent pas l’usage réel. Avant d’acheter de la visibilité, il faut connaître les abandons et le temps d’assistance par installation.

**Décisions proposées à la comparaison avec Claude :** cible initiale macOS multi-assistants ; réparation de l’accueil avant diffusion ; installation durable unique ; priorité site/PyPI/registre officiel ; SQLite pour nouveaux utilisateurs à discuter ; Windows et binaire après qualification ; communications limitées aux fonctionnalités et preuves acquises. GitHub reste gelé et aucune publication n’est engagée par cette analyse.
