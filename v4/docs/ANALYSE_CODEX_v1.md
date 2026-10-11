# Analyse indépendante de Codex — Memory OS AI

**11 octobre 2026 — proposition pour confrontation avec Claude.** Son analyse indépendante n’a pas été lue. Cette note distingue mes observations, les preuves du dépôt et les résultats transmis par Claude. Aucun changement de code, aucune campagne d’inférence, aucune bascule ni publication n’accompagne cette analyse.

## 1. Mon verdict et le produit dont j’ai besoin

**Memory OS AI est une ALPHA locale utile, avec une ingénierie de qualification sérieuse. Son caractère indispensable et sa supériorité générale restent à démontrer.**

Dans notre travail, la mémoire partagée permet de retrouver des décisions, des incidents et des résultats entre clients. Mais nous dépendons encore beaucoup de longs relais écrits, de fichiers de suivi et de rappels humains. Le serveur conserve l’histoire mieux qu’il ne me livre l’état exact du travail à reprendre.

Pour devenir indispensable, il doit répondre immédiatement à cinq questions : **quel est l’objectif actuel, quelles décisions s’appliquent encore, quelles preuves les soutiennent, qu’est-ce qui a changé depuis mon dernier passage, quelle action reste à faire ?** Il doit ensuite enregistrer les changements avec peu d’effort et sans ambiguïté.

Ma proposition de positionnement : **la continuité de travail vérifiable entre humains et agents, indépendante du fournisseur de modèle, sous le contrôle de l’utilisateur.** C’est plus précis que « donner une mémoire aux IA ». La symbiose recherchée est opérationnelle : moins de répétitions et d’erreurs, une meilleure reprise, une coopération fiable. Elle ne suppose aucun accès aux états internes des modèles.

## 2. Les forces à préserver

1. **Un usage partagé réel.** Claude et Codex utilisent un même service local, avec projets, recherche, briefs, faits et procédures. MCP et le relais évitent de construire une intégration entièrement différente pour chaque client. L’utilisateur conserve son magasin ; le moteur peut fonctionner sans appel à un LLM externe.
2. **Une mémoire plus structurée qu’un historique de conversation.** Dates de validité, provenance, identités explicites, corrections et relations de remplacement existent déjà. La séparation entre données sources et index reconstruisibles est saine.
3. **Des garde-fous opérationnels substantiels.** Jeton dans un fichier protégé, contrôles Host/Origin, distinction `/health`–`/ready`, reçus de requête, refus de rejouer aveuglément une mutation, sauvegardes vérifiées, doctor et qualifications sur copie constituent une base crédible. La bascule locale 4.0.3 rapportée par Claude a été qualifiée avant activation.
4. **Une capacité à corriger les causes réelles.** L’écart avec BM25 a été attribué à l’indexation et à l’unité de récupération, puis corrigé jusqu’à la parité lexicale. Les tests de panne SQLite et la migration réversible existent ; ce travail n’est pas à refaire.
5. **Une discipline scientifique utile.** Préenregistrement, baselines simples, intervalles appariés, refus de campagnes invalides et publication des échecs protègent la crédibilité du projet. Il faut conserver cette discipline même lorsque les résultats déçoivent.

## 3. Les faiblesses, avec leurs preuves

**F1 — Le coût de contexte du brief est mal maîtrisé.** Durant cette analyse, une demande `memory_session_brief(max_tokens=1000)` a renvoyé **44 243 caractères de JSON**, dont seulement **2 370 caractères pour le champ `text`**. Ce sont des caractères mesurés, pas des tokens mesurés. Le code borne le résumé rendu, mais renvoie aussi les fiches complètes. Le schéma annonce bien un budget du texte rendu : c’est une faiblesse du contrat d’usage, pas une violation de ce schéma. Pour un agent, l’enveloppe complète consomme du contexte. `memory_get_context` offre déjà un contexte borné ; il manque une expérience compacte cohérente dès l’entrée.

**F2 — Le brief confond encore histoire et travail actif.** Dans `session_brief`, les faits sont classés principalement par importance et récence ; les procédures sont les six premières disponibles. Les éléments en attente sont les six premiers correspondant à des tags ou mots-clés. Les procédures et éléments en attente ne reçoivent pas le même filtre de validité que les faits. Un ancien « todo » peut donc survivre à sa résolution. Les blocs permettent de tenir un état manuellement, mais leur remplacement n’exige pas de révision attendue : deux agents peuvent écraser leurs mises à jour.

**F3 — Une contradiction de politique subsiste.** `instructions.py` recommande `memory_consolidate` chaque semaine ou au-delà de 2 000 éléments ; le protocole local l’interdit depuis les incidents. Le handler inspecté exécute la consolidation, et `dry_run` vaut faux par défaut. Je n’y ai trouvé aucun contrôle serveur représentant cette interdiction opérateur. Une consigne dans `AGENTS.md` ne remplace pas un verrou d’autorisation. C’est prioritaire avant toute capture ou maintenance davantage automatisée.

**F4 — Le relais sait s’arrêter prudemment, mais la reprise reste difficile.** Les reçus et l’idempotence existent ; pourtant, la table publique des outils n’expose pas de consultation dédiée du statut d’une requête. L’agent reçoit « résultat incertain, ne pas répéter » sans parcours simple pour résoudre cette incertitude. De plus, `request_id` reste optionnel. Il manque une chaîne complète : identifiant durable côté client, acquittement explicite, consultation du statut, traitement documenté des cas non résolus.

**F5 — La mémorisation demande encore trop de travail à l’agent.** Je dois choisir quoi sauvegarder, reformuler des faits atomiques, renseigner projet et mots-clés, distinguer correction et ajout, puis penser à le faire. Cette qualité éditoriale est utile, mais son coût se répète. Les hooks Claude Code assistent le démarrage et rappellent la sauvegarde ; cette assistance n’est pas uniformément garantie dans tous les clients. Une configuration détectée parmi neuf clients ne prouve ni une connexion effective ni un usage quotidien réussi.

**F6 — La durabilité industrielle n’est pas achevée.** Le backend JSONL utilisé en production n’offre pas une transaction unique couvrant toutes les écritures et le reçu. SQLite S2/S3 apporte cette fondation, mais reste opt-in. Les sauvegardes qualifiées actuelles nécessitent l’arrêt du writer ; le parcours de sauvegarde/restauration SQLite S4 reste un préalable à son activation. Les injections d’exceptions et SIGKILL prouvent des propriétés précises, pas la résistance universelle à toute panne matérielle.

**F7 — Provenance ne signifie pas vérité ni autorisation.** Un fait sauvegardé par un agent peut être une interprétation ou une hypothèse. Sa répétition par d’autres agents ne doit pas augmenter artificiellement sa crédibilité. Il faut rendre systématiques le lien vers la preuve originale, sa révision, l’auteur de l’affirmation et son statut. De même, une mémoire ancienne ne peut pas autoriser une action aujourd’hui. Le partage par projet ne doit pas être présenté comme une isolation de sécurité entre clients.

**F8 — Les preuves de valeur restent fragmentaires.** Les résultats disponibles ne soutiennent aucune revendication « top 1 » :

| Preuve | Résultat et portée exacte |
| --- | --- |
| LongMemEval-S, C4 | 500 questions exécutées, 479 avec preuves évaluées en retrieval ; rappel 0,754 contre 0,712, Δ +0,042, IC95 [+0,022 ; +0,061]. Mais questions déjà observées via l’oracle, distracteurs fixes, et profil MiniLM/CPU différent du Granite/MPS de production. |
| BEAM 100K | 355 questions avec preuves : rappel 0,462 contre 0,445, Δ +0,017, IC95 [−0,004 ; +0,040]. Critère préenregistré non atteint, malgré une amélioration du MRR. |
| Pilote de réponses | 10 questions : C4 40 %, BM25 20 %, 40 appels et 362 348 tokens. Signal descriptif, échantillon minuscule, juge différent des publications. |
| Accélération MPS | Gain de vitesse important dans les configurations testées, mais équivalence bilatérale non démontrée. La production utilisait déjà MPS ; ce n’est pas un nouveau gain de production acquis par cette expérience. |

Nous n’avons pas encore une mesure convaincante du temps économisé par utilisateur, des erreurs évitées, du coût total de contexte ou de la fidélisation. Ces mesures comptent autant que la recherche de passages.

Preuves locales principales : `v4/src/memory_os_v4/{memory,server,models,instructions,idempotency}.py`, `v4/src/memory_os_v4/ops/relay.py`, `v4/docs/OPERATIONS.md`, `v4/docs/SQLITE_S3_QUALIFICATION.md` et les résultats référencés dans le `README.md`. Le code inspecté inclut des évolutions candidates ; leur présence dans le clone ne signifie pas leur déploiement en 4.0.3.

## 4. Position face aux concurrents

Les capacités ci-dessous viennent des documentations officielles consultées le 11 octobre 2026. Ce sont des descriptions de produits, pas un classement indépendant de leur qualité.

| Concurrent | Ce qu’il oblige à prendre au sérieux |
| --- | --- |
| **Mem0** | La plateforme documente extraction et recherche combinant mémoire et graphe. « Nous stockons et retrouvons des faits » ne suffit pas à nous distinguer. [Documentation](https://docs.mem0.ai/platform/features/graph-memory) |
| **Zep / Graphiti** | Graphiti propose déjà graphe temporel et recherche hybride ; Zep ajoute une offre opérée. La temporalité et l’hybridation ne sont pas des exclusivités de memory-os-ai. [Comparaison officielle](https://help.getzep.com/zep-vs-graphiti) |
| **Letta** | La mémoire est intégrée au cycle de vie de l’agent : fichiers versionnés, inspection et maintenance en arrière-plan. La continuité et la lisibilité sont des fonctions de produit. [Documentation](https://docs.letta.com/configuration/memory) |
| **Supermemory** | Son MCP expose recherche, documents et contexte de profil. La mémoire partagée accessible par MCP est déjà un terrain concurrentiel. [Présentation MCP](https://supermemory.ai/changelog/revamped-supermemory-mcp-experience/) |
| **LangMem** | Distingue profils, collections, mémoire épisodique/procédurale et gestion active ou en arrière-plan. L’organisation et l’actualisation comptent autant que le stockage. [Guide conceptuel](https://langchain-ai.github.io/langmem/concepts/conceptual_guide/) |
| **Cognee** | Combine structuration documentaire et graphe ; documente aussi les permissions sur les données. L’intégration des sources et leur contrôle d’accès sont des exigences concurrentielles. [Documentation des permissions](https://docs.cognee.ai/core-concepts/permissions-system/acl) |
| **MemOS** | Met en avant une sélection et une organisation de la mémoire adaptées à l’exécution. Il faut évaluer la pertinence du contexte livré au moment utile. [Documentation](https://docs.openmem.net/memos_cloud/introduction/mem_schedule/) |
| **Mémoires natives** | ChatGPT et Codex ont des mémoires distinctes, dont une mémoire locale Codex ; Claude Code dispose d’une mémoire automatique locale. Leur intégration directe réduit l’effort utilisateur. Le caractère local seul n’est donc pas différenciant. [Documentation officielle OpenAI](https://learn.chatgpt.com/docs/customization/memories), [Claude Code](https://code.claude.com/docs/en/memory) |

**Notre avantage potentiel est leur combinaison : continuité entre fournisseurs, preuves inspectables, maîtrise locale et reprise de travail fiable. Cet avantage reste à démontrer auprès d’utilisateurs autres que nous.** Il faut coexister avec les mémoires natives, clarifier les responsabilités et éviter les duplications ; les remplacer systématiquement serait une mauvaise hypothèse de départ.

## 5. Ce qui manque, classé par impact

Les critères suivants sont des **objectifs proposés**, pas des résultats acquis. Les seuils devront être figés avec Claude avant les essais.

| Rang | Capacité à livrer | Critère de terminé et preuve |
| --- | --- | --- |
| **1 — Contexte immédiatement utile** | Un brief compact orienté tâche, des références développables à la demande, un budget couvrant toute la réponse. | Sur 100 scénarios français/anglais : plafond sérialisé respecté dans 100 % des cas ; cible de 1 500 tokens avec tokenizer déclaré, plafond en octets séparé ; ≥95 % des décisions critiques attendues présentes ; aucune procédure invalidée présentée comme actuelle. Comparer au brief actuel à budget égal. |
| **2 — Sûreté et issue des opérations** | Politique de maintenance appliquée côté serveur ; identifiants durables générés par les adaptateurs ; outil de statut des requêtes. | Tous les appels de maintenance interdits sont refusés sans mutation. Tests de coupure avant, pendant et après acquittement : aucun doublon, aucun succès partiel annoncé, chaque statut distingue résultat confirmé et incertitude persistante ; aucune répétition automatique d’un cas incertain. |
| **3 — État de travail partagé** | Identité stable projet/dépôt/tâche, état ouvert/bloqué/terminé, décision remplaçante, prochain geste, preuves et révision attendue pour les modifications concurrentes. | 30 passages de relais entre trois clients : ≥90 % des reprises sans réexplication humaine, aucune contrainte critique connue omise, aucune tâche terminée dans les tâches ouvertes, conflits d’écriture détectés. Les fichiers et commits restent les références du travail réel. |
| **4 — Capture avec peu d’effort** | Assistance explicite et consentie là où les clients le permettent ; sauvegarde simple ailleurs ; distinction durable/indexé et retour rapide. | Sur 20 sessions annotées manuellement : ≥90 % des décisions importantes capturées, ≤5 % de souvenirs inutiles, <30 s de gestion manuelle médiane par session. À 10 000 éléments sur machine de référence : acquittement durable p95 ≤250 ms et recherche chaude p95 ≤500 ms, indexation mesurée séparément. |
| **5 — Durabilité activable** | Terminer sauvegarde/restauration SQLite, qualification sur copie, migration et retour arrière opérables. | Matrice crash/erreur d’écriture : zéro écriture acquittée perdue dans les scénarios testés. Restauration exhaustive vérifiée, incluant reçus et écritures récentes ; objectif ≤5 min à 10 000 éléments sur machine déclarée. Bascule uniquement après qualification et accord opérateur. |
| **6 — Contrôle et confiance utilisateur** | Vue « ce qui est retenu et pourquoi », correction, portée de partage, export, oubli et traitement explicite des sauvegardes ; hypothèses séparées des faits attestés. | Quatre utilisateurs sur cinq corrigent, exportent et retirent un souvenir sans terminal, chaque action en moins de deux minutes. Tests de cloisonnement : aucun élément privé dans un contexte interdit. L’effacement est vérifié dans les données actives et les dérivés ; la rétention des backups est affichée sans promettre une disparition physique immédiate. |
| **7 — Bénéfice mesuré en situation réelle** | Évaluation du travail accompli, incluant coût mémoire, relectures, répétitions et erreurs ; comparaison avec fichiers de suivi et BM25 à budgets égaux. | Protocole préenregistré sur ≥30 familles de tâches indépendantes, analyse appariée et IC par famille. Cible : réduction médiane du temps de reprise ≥20 %, borne basse du gain >0, sans dégradation dépassant une marge d’erreur préfixée. Dimensionner l’échantillon avant de revendiquer un effet. |
| **8 — Adoption reproductible** | Trois parcours complets d’abord : Claude Code, Codex, Cursor ; ensuite extension documentée de la matrice clients. | Quatre nouveaux utilisateurs sur cinq réalisent en <10 min installation, connexion, sauvegarde et reprise dans un autre client. Séparer clairement « recette disponible », « connexion testée » et « continuité testée ». Mesurer ensuite usage à J7/J30 et motifs d’abandon. |

La durabilité du rang 5 est un **prérequis de l’automatisation du rang 4**, même si son bénéfice est moins visible. Le classement exprime l’impact utilisateur ; les dépendances déterminent l’ordre de réalisation.

## 6. Priorités proposées pour le futur accord

**P0 — Réparer le contrat quotidien et fermer les risques connus.** Brief compact, cohérence des consignes et verrou de maintenance, statut des requêtes. Ouvrir en parallèle le chantier SQLite S4 sur copies, sans changer la production. Ajouter aux versions un état lisible des capacités effectivement déployées : moteur, relais, backend, profil d’encodage et intégrations vérifiées.

**P1 — Réussir le passage de relais.** État de tâche versionné, clôture explicite, preuves reliées aux sources, capture assistée puis contrôles utilisateur. Une démonstration exigible : je termine un travail dans Codex, Claude ou Cursor reprend la bonne tâche et ses contraintes, puis sa conclusion revient dans mon prochain brief sans collage humain ni ancien état trompeur.

**P2 — Prouver l’utilité et élargir la distribution.** Tests utilisateurs, mesure du coût total et comparaison aux pratiques existantes. Reprendre ensuite les évaluations de réponses prévues : LongMemEval-S restera descriptif puisqu’il a déjà été observé. Toute revendication concurrentielle demandera des conditions comparables, un protocole indépendant et les règles du classement concerné.

**Je conserverais C4 comme défaut réversible à ce stade**, conformément à la décision déjà prise : aucune preuve actuelle ne justifie un nouveau changement global. J’afficherais ses limites et rendrais BM25 accessible. Je n’ajouterais pas maintenant un graphe généraliste, un nouvel extracteur coûteux, davantage de clients superficiellement branchés ou une campagne massive pour compenser les problèmes d’usage.

La prochaine étape est la confrontation de cette proposition avec celle de Claude : résoudre les désaccords, fixer quelques critères communs, puis répartir les lots. **Ce document ne prétend pas constituer cet accord ni autoriser une mise en œuvre supplémentaire.**
