# Memory OS AI — Plan commun v4, version soumise à contre-signature

**Date : 11 octobre 2026. Signé par Codex ; contre-signature de Claude en attente.**

Sources : analyses indépendantes `ANALYSE_CLAUDE_v1.md` et `ANALYSE_CODEX_v1.md`, puis `PLAN_COMMUN_v4_draft.md`. Ce texte remplace le brouillon côté Codex. Il ne prétend pas que Claude a accepté les amendements ci-dessous. Cette étape produit uniquement le plan : aucun code, aucune campagne ni bascule.

## 1. Verdict partagé

Memory OS AI est une ALPHA locale utile, avec une ingénierie d’exploitation et d’évaluation sérieuse. La continuité repose encore trop sur les résumés de conversation, les relais écrits et les fichiers de suivi. Le moteur conserve l’histoire mieux qu’il ne restitue l’état actuel du travail.

**Positionnement : continuité de travail vérifiable entre humains et agents, indépendante du fournisseur de modèle, sous le contrôle de l’utilisateur.**

Le produit doit restituer l’objectif actuel, les décisions applicables, leurs preuves, les changements depuis le dernier passage et la prochaine action. La capture doit demander peu d’effort, sans transformer une hypothèse en fait ni un souvenir en autorisation.

La réussite sera mesurée par la reprise effective du travail, les erreurs évitées, le temps et le contexte économisés. Un classement scientifique reste un objectif distinct ; aucun résultat actuel n’autorise une revendication « top 1 ».

## 2. Convergences, corrections et désaccords

| Sujet | Décision Codex sur le brouillon et justification |
| --- | --- |
| Brief compact | **Accord**, mais scinder livraison compacte et état de travail complet. L’identité de projet doit être corrigée avant de qualifier la pertinence du brief. |
| Maintenance | **Accord.** Verrou serveur fermé par défaut, instructions cohérentes ; le modèle ne peut pas s’accorder une permission en écrivant une mémoire. |
| Requêtes incertaines | **Amendement.** Le statut rend l’incertitude observable, pas toujours résoluble automatiquement : JSONL peut conserver une mutation partielle. Ne jamais promettre le contraire. |
| Résilience | **Amendement.** La reconnexion existe déjà et relit le jeton. Compléter et tester ce parcours ; ne pas le réécrire sans défaut reproduit. Mesurer cinq secondes après disponibilité réelle du serveur, pas pendant son chargement. |
| Capture | **Accord conditionnel.** Brouillon assisté et validé ; automatisation des écritures seulement après qualification et activation du stockage transactionnel. Aucun nouvel extracteur LLM autonome dans ce plan. |
| Mémoire native | **Accord de coexistence, amendement du périmètre.** Import explicite de fichiers autorisés, sans synchronisation en écriture ni collecte générale des répertoires privés. |
| Vérité temporelle | **Accord.** Corrections explicites avec identité ou remplacement, révision et provenance ; aucune invalidation par similarité. Une contradiction non résolue doit rester visible. |
| Campagne LME-S | **Désaccord avec la reprise en arrière-plan proposée.** La dernière consigne met la campagne en pause. L14 reste suspendu jusqu’à une reprise explicite de Romain ; le GO antérieur et le protocole restent conservés. |
| Trois « sprints » | **Amendement.** Ce sont trois phases, pas trois délais acquis. Les lots ont des dépendances et une charge significative ; les jours d’attente humaine ou de quota ne sont pas des jours d’ingénierie. |

Corrections factuelles à conserver dans la communication commune :

- Le partage local, MCP et la temporalité ne sont pas des exclusivités établies face aux solutions auto-hébergées et aux mémoires natives. Ne pas affirmer « aucun concurrent » sans comparaison reproductible.
- Le stockage local ne signifie pas « aucune donnée ne sort » : les passages fournis à un client hébergé entrent dans son contexte. Le périmètre de partage doit être explicite.
- L’absence de dégradation significative n’établit pas `C4 ≥ BM25 partout`. LongMemEval-S est observé et BEAM a échoué à son critère principal.
- Les 29–43 secondes d’indexation viennent d’une configuration MPS expérimentale ; elles ne constituent pas un SLA du profil de production. Ne pas les fusionner avec la latence de recherche d’une autre campagne.
- Désactiver la consolidation n’efface pas les capacités existantes de résumé, d’oubli ou de correction explicite. L’état courant et leur orchestration restent les problèmes à résoudre.
- `AGENTS.md`/`CLAUDE.md` portent des instructions de projet ; ils ne sont pas synonymes de mémoire native générée. Leur indexation ne leur confère pas une nouvelle autorité.

## 3. Lots, responsables, critères et séquencement

### 3.1. Règles de mesure et charge

**Un responsable de livraison par lot**, avec contributions identifiées. Le partenaire fait la revue adverse ; Claude réalise les qualifications physiques hors sandbox. Le relais empaqueté est la référence commune : son équivalent épinglé réutilise cet artefact ou prouve sa parité, sans devenir un second produit divergent.

Un **jour-agent (JA)** représente environ six heures de travail d’ingénierie concentré, tests et revue compris. Les fourchettes sont des estimations initiales, pas des délais garantis ni une mesure du temps de génération du modèle. Ajouter **25 % de réserve** ; attentes utilisateur, recrutement, quota et observation J7/J30 sont séparés. Les charges ne se divisent pas mécaniquement par deux : Codex concentre plusieurs dépendances moteur.

Avant chaque essai, figer un manifeste : commit et dépendances, corpus/fixtures et SHA, matériel, backend, profils, versions des clients, horloge, compteur de tokens, dénominateurs, métriques, seuils, exclusions, budgets et traitement des échecs. Après observation, toute modification devient un nouvel essai, sans effacer le précédent.

Seuils transversaux proposés pour contre-signature :

- **Brief compact :** enveloppe sérialisée entière ≤6 000 octets UTF-8 **et** ≤1 500 tokens avec `cl100k_base` versionné et empreinté. Aucun téléchargement implicite du compteur. Le budget inclut métadonnées, références et erreurs ; une saturation est signalée, jamais présentée comme un contexte complet.
- **Qualité du brief :** 100 scénarios figés, 50 français/50 anglais ; ≥95 % des décisions critiques attendues retrouvées sur les scénarios où le budget est réalisable ; zéro élément invalidé présenté comme actuel. Cas de saturation évalués séparément. Les interdictions opérateur sont appliquées hors du brief, même si celui-ci est tronqué.
- **Latences :** sur machine de référence et 10 000 éléments documentés, concurrence 1 ; recherche chaude p95 ≤500 ms. Cible d’acquittement durable p95 ≤250 ms pour la future capture, en distinguant explicitement « persisté » et « indexé ». Ces seuils ne sont pas déjà démontrés et ne constituent pas des portes de sortie de 4.0.4 hors lots concernés.
- **Petits essais utilisateurs :** 4/5 ou 27/30 sont des seuils de validation formative, pas des preuves de généralisation. Rapporter aussi les échecs, dénominateurs et intervalles pertinents.
- **Ressources :** travaux lourds séquentiels, `nice 15`, arrêt si swap >8,5 Go ou disque libre <5 Go ; au plus deux travaux lourds planifiés par campagne avant revue. Aucun modèle >300 Mo téléchargé sans décision spécifique. Pas d’inférence côté Codex dans les étapes actuellement autorisées.

### 3.2. P0 — Contrat quotidien et sûreté

| Lot / avis / charge | Responsable et dépendances | Périmètre retenu, justification et preuve de terminé |
| --- | --- | --- |
| **L1 Brief v2 — AMENDER — 4–6 JA** : L1a 2–3 ; L1b 2–3 | **Codex** ; Claude adapte les hooks. L1a dépend de L7a ; L1b de L7b/L8. | **L1a, 4.0.4 :** réponse compacte bornée, filtrage de validité homogène, sélection orientée demande, références développables via un parcours testé ; mode détaillé explicite pour compatibilité. Critères des 100 scénarios, sans doublons de fiches complètes dans l’enveloppe. **L1b, ultérieur :** décisions et tâches structurées, changements depuis un curseur explicite par client/projet ; curseur périmé signalé. Ne pas prétendre livrer un état de tâche fiable avant son modèle. |
| **L2 Verrou maintenance — ACCORD PRÉCISÉ — 1–2 JA** | **Codex** ; Claude aligne les instructions des clients. Premier lot. | Bloquer `consolidate` et `compact` côté serveur tant que la politique opérateur l’interdit ; simulation par défaut lorsqu’un accès de maintenance est autorisé. Autorisation de simulation distincte de mutation réelle, non modifiable par un outil mémoire ordinaire. Tests des deux alias et des appels directs exposés : refus sans écriture ; aucune consigne contradictoire dans les surfaces auditées. |
| **L3 Issue des requêtes — AMENDER — 3–5 JA** | **Codex** ; Claude teste transports et clients. Contrat commun avec L4. | Journal client durable avant envoi ; ID conservé et retourné après coupure ; statut authentifié, lié au projet, à l’opération et à l’empreinte de charge. Distinguer terminé, en attente/incertain et reçu absent. **Absent ne prouve pas non exécuté** ; seul le client peut attester un non-envoi. Sur JSONL partiel : blocage et procédure opérateur, pas résolution inventée. Tests aux frontières : aucun rejeu automatique de mutation, aucun double effet dû aux reprises du même ID, aucun succès pour un lot partiel. Les anciens clients sans ID restent identifiés comme moins protégés. |
| **L4 Connexion résiliente — AMENDER — 2–3 JA** | **Codex**, propriétaire du relais empaqueté ; Claude intègre et qualifie le relais épinglé et les configs. Dépend du contrat L3. | Conserver l’architecture de connexion existante, compléter rotation de jeton et erreurs observables. `serverInfo.version` transmet la version moteur ; la version du relais est distincte. Après serveur prêt et nouveau jeton accepté : la prochaine opération de contrôle retrouve le service en ≤5 s, sans redémarrer le client, sur 30 cycles de restart/rotation. Les opérations déjà envoyées restent soumises à L3. Pas de promesse de récupération d’un client tiers ayant définitivement fermé son transport : doctor l’indique. |
| **L5 Capacités déployées — AMENDER — 1–2 JA** | **Codex** ; Claude valide doctor. Dépend des contrats L2–L4 et L7a. | Le serveur expose son moteur/build, backend, profil d’encodage et politiques effectives. **Il ne connaît pas à lui seul le relais de chaque client.** Doctor agrège identité serveur, artefact du relais et preuve de connexion par client ; inconnu reste inconnu. Tests version incohérente, backend différent, profil divergent et client non connecté ; aucun jeton ni secret dans les sorties. |
| **L6 SQLite S4 — ACCORD AMENDÉ — 4–7 JA** | **Codex** ; Claude qualifie sur copie. Exploite S2/S3 existants ; contrat de reçus L3 stabilisé. Parallélisable, hors porte de sortie 4.0.4. | Backup via API SQLite, vérification et restauration hors ligne, migration et retour après nouvelles écritures. Zéro acquittement perdu et zéro succès partiel dans la matrice définie : exceptions, arrêt processus, erreurs d’écriture simulées. Restauration jusqu’à `/ready` ≤5 min sur fixture de 10 000 éléments, taille/profil/cache figés ; hashes et contenu logique exhaustivement comparés. Un retour arrière doit préserver les écritures post-migration, pas restaurer aveuglément l’ancien JSONL. Les tests ne prouvent pas toute panne électrique. Activation séparée après GO. |

### 3.3. P1 — Reprise effective entre agents

| Lot / avis / charge | Responsable et dépendances | Périmètre retenu, justification et preuve de terminé |
| --- | --- | --- |
| **L7 Identité et état de travail — AMENDER — 5–8 JA** : L7a 2–3 ; L7b 3–5 | **Codex** ; Claude transmet l’identité depuis les hooks. **L7a remonte en P0** ; L7b après L3, avec S4 complet qualifié et SQLite activé par GO distinct avant ses mutations d'état en production. L8/L9/L11 testent `work_state` ; faux tant que cette activation/API manque. | **L7a :** registre local d’IDs et alias validés ; racine Git, remote et chemins sont des indices, jamais une preuve automatique d’identité. Forks, worktrees, clones, dossiers sans Git et déplacements testés ; zéro mélange de projets distincts. Lire l’ancien historique par alias sans le réécrire. Un ID par projet approuvé, plusieurs contextes de travail possibles. **L7b :** tâches ouvertes/bloquées/terminées, propriétaire, prochaine action, preuves, décisions remplacées ; CAS sur blocs et tâches. Tests de concurrence et clôture : aucun écrasement silencieux, aucune tâche terminée retournée comme ouverte. |
| **L8 Blocs vivants — AMENDER — 1–2 JA** | **Claude**, responsable contenu et validation ; Codex fournit API/CAS via L7b. Dépend de L7b ; alimente ensuite L1b. | Profil, état du projet et références aux règles ; proposition par agents, validation initiale par Romain, mises à jour autorisées tracées. Maximum 400 tokens par bloc, **sans droit à 400 tokens chacun dans chaque brief** : L1 arbitre le budget total. Blocs versionnés, sourcés ; hypothèse et règle approuvée distinguées. Conflits/versions périmées signalés ; un bloc ne peut pas élargir les permissions. « Blocs remplis » seul serait un faux critère de valeur. |
| **L9 Capture assistée — AMENDER — 3–5 JA** | **Claude** ; Codex fournit validation déterministe et enregistrement du brouillon, pas un nouveau service d’extraction LLM. Dépend de L3/L7b et de **L6 activé** pour automatiser les écritures en production. | Brouillon préparé dans le travail déjà autorisé de l’agent, validation avant sauvegarde ; hooks quand disponibles, procédure explicite ailleurs. Consentement par périmètre, limites de taille, exclusion des secrets, aucune aspiration générale des conversations. Sur 20 sessions annotées : ≥90 % des décisions importantes capturées, ≤5 % d’entrées inutiles, temps manuel médian <30 s ; coût modèle et latence inclus. Le prototype de brouillon seul peut précéder l’activation SQLite. |
| **L10 Coexistence native — AMENDER — 2–4 JA** | **Claude** ; Codex revoit ingestion/provenance. Dépend de L7a et des règles de portée. | Sources explicitement autorisées, en lecture seule, chemin/révision/date attachés. `AGENTS.md` et `CLAUDE.md` restent des instructions à lire à leur source ; leur copie est une donnée. Ne pas collecter automatiquement les fichiers internes générés par Codex ni synchroniser deux mémoires en boucle. Tests source modifiée, retirée, hors portée et contenu hostile : retrait des index dérivés, lien vers la bonne version, aucune élévation de privilège ni écriture dans les sources. |
| **L11 Mesure des relais — ACCORD PRÉCISÉ — 1–2 JA** | **Claude** ; Codex revoit rubric et résultats. Mesure initiale dès L13a ; validation après L1b/L7b/L8. | 30 reprises planifiées avant observation, dix par client repreneur parmi Claude Code/Codex/Cursor, au moins trois projets et plusieurs types de tâche. ≥27/30 sans réexplication humaine ; zéro contrainte critique connue omise. Les échecs d’intégration restent dans le dénominateur. Distinguer reprise avec mémoire, résumé natif et fichiers ; ne pas attribuer automatiquement toute réussite à memory-os. Essai formatif, pas preuve causale : L15 traite celle-ci. |

### 3.4. P2 — Contrôle utilisateur, utilité et adoption

| Lot / avis / charge | Responsable et dépendances | Périmètre retenu, justification et preuve de terminé |
| --- | --- | --- |
| **L12 Console locale — ACCORD AMENDÉ — 5–8 JA** | **Codex** ; Claude organise les tests utilisateurs. Lecture après L5/L7a ; mutations après L3/L6/L7b. | Lecture, recherche, provenance et corrections d’abord ; pin/export/oubli ensuite, avec journal et portée explicite. UI locale authentifiée, protection des mutations contre les origines non autorisées ; jamais un contournement des règles MCP. 4/5 utilisateurs réalisent chaque action en <2 min sans terminal. Vérifier données actives, index/cache et exports après oubli ; expliquer la rétention des sauvegardes et empêcher une restauration de réintroduire silencieusement les oublis postérieurs. La consultation peut être anticipée en P1. |
| **L13 Télémétrie locale — AMENDER — 2–3 JA**, dont L13a 0,5–1 | **Codex** ; Claude tient le tableau de bord. **L13a dès P0**, L13b après les parcours P1. | Compteurs/latences/budgets de qualification d’abord, puis événements d’usage consentis. Distinguer « livré », « développé » et « déclaré utile » : un hit retourné n’est pas une preuve d’utilisation par le modèle. Aucun texte de conversation ni secret par défaut ; IDs pseudonymisés, rétention 30 jours, désactivation et purge testées, aucun envoi externe. Rapport hebdomadaire reproductible, dénominateurs et coût de contexte explicites. |
| **L14 LME-S descriptif — DÉSACCORD SUR LE DÉMARRAGE — 0,5–1,5 JA restants** | **Claude** exécute ; Codex garde le protocole. **En pause jusqu’à reprise explicite de Romain.** | Conserver le plan existant : 500 questions avec abstentions, C4/BM25, Luna low lecteur/Luna high juge, prompts officiels, ChatGPT uniquement, 0 dollar API. Source moteur **4.0.3 figée**, pas la future 4.0.4 ; rapport explicitement limité à ce profil. 2 000 appels réussis prévus, plafond 2 200 tentatives, lots ≤40, reprise manuelle et ledger existants. Rapport complet ou arrêt documenté ; aucune supériorité requise pour considérer l’évaluation correctement livrée. L’ancien corpus et le juge différent interdisent une confirmation indépendante ou un classement officiel. |
| **L15 Étude du bénéfice réel — ACCORD AMENDÉ — 2–4 JA de préparation/analyse** | **Codex** pour protocole ; Claude pour terrain. Après stabilité P1 et L13 ; temps d’observation en plus. | ≥30 familles de tâches indépendantes, dimensionnement après pilote distinct ; ordre randomisé/contrebalancé et variantes de tâches pour limiter l’apprentissage. Comparer memory-os aux fichiers/suivi habituels et à BM25 avec modèle, sources et budget comparables. Gain médian de reprise ≥20 % et borne basse IC95 >0. Taux d’erreur critique : borne supérieure de dégradation ≤+2 points ; toute action interdite observée bloque l’activation. Coût total de contexte cible ≤+10 %. Puissance insuffisante ou IC manquant : résultat inconclusif, pas succès. |
| **L16 Adoption reproductible — ACCORD PRÉCISÉ — 2–3 JA** | **Claude** ; Codex prépare distribution et documentation. Après 4.0.4 qualifiée ; parcours de continuité complet après P1. | 4/5 nouveaux utilisateurs, non auteurs du projet : installation, sauvegarde et reprise dans un second client en <10 min à partir de prérequis déclarés. Rapporter séparément durée des téléchargements et total réel. Trois clients prioritaires, versions et OS testés explicites ; recette, connexion et continuité distinguées. Mesure consentie J7/J30 et motifs d’abandon, sans inventer un objectif de fidélisation avant baseline. |

Pour L15, le temps de reprise va de l’ouverture de la session à la première action correcte vérifiable, selon une rubrique fixée avant observation. La comparaison primaire utilise les fichiers et outils habituels, mémoires natives configurées de façon identique ; BM25 est secondaire. Calculer par paire le gain relatif `(temps_baseline − temps_memory) / temps_baseline`, puis sa médiane ; bootstrap de 10 000 réplications par famille, graine 20261011. Les variantes et états de départ empêchent une condition de bénéficier des souvenirs produits par l’autre. Le coût comprend brief, recherches, sauvegardes et maintenance amortie, pas seulement les tokens de la réponse finale.

### 3.5. Contenu précis et ordre de RELEASE 4.0.4

**Périmètre ferme : L2 → contrats L3/L4 → L3 → L4 → L7a → L5 → L1a, avec L13a dès le début.** Des travaux indépendants peuvent avancer en parallèle, mais leur intégration respecte ces dépendances. La qualification finale suit l’intégration complète.

La version contient : verrou de maintenance et consignes corrigées ; reçus consultables et parcours de coupure sûr ; relais corrigé, rotation/reconnexion qualifiées et transmission de version ; identité canonique minimale par alias ; capacités réellement attestées ; brief compact et valide ; métriques minimales de qualification. Les alias utilisateur sont appliqués seulement après validation de leur mapping, sans fusion implicite de données.

**Hors 4.0.4 :** L1b, L7b, blocs autoentretenus, capture automatisée, import natif généralisé, console et nouvelle campagne de modèles. SQLite S2/S3 reste inclus **opt-in** ; S4 poursuit sa qualification séparément. JSONL reste le backend par défaut. C4 et le profil d’encodage 4.0.3 restent inchangés ; aucune réindexation globale ni modification silencieuse du device.

Portes de sortie :

1. Revue croisée des contrats, compatibilité des clients anciens et tests spécifiques L1a–L5/L7a ; nouvelle API compacte activée dans les adaptateurs qualifiés, mode détaillé conservé explicitement.
2. Suites v3/v4, TCP, lint ; wheel/sdist, installations isolées hors dépôt, commandes installées, `twine check --strict`, manifestes et SHA-256. Relais installé depuis **chaque artefact** contre serveur synthétique, avec panne et reconnexion.
3. Claude exécute sur une **copie** du magasin : intégrité, reçus/provenance, mapping d’alias, reprise et caches ; recherche identique à portée/profil identiques, toute différence expliquée avant acceptation. Les changements de présentation du brief sont attendus et évalués séparément. Aucune modification du magasin de production.
4. Doctor et parcours réels des trois clients ; procédure de bascule/retour arrière préservant les nouvelles écritures. Défaut de sûreté, test requis manquant ou différence inexpliquée : candidate non qualifiée.
5. Livraison par bundle avec **référence de branche nommée**, dossier de preuves et commande de publication. Bascule locale et PyPI ont chacun leur GO ; aucun ordre obligatoire entre les deux après qualification.

Charge de cette release : **13,5–22 JA**, qualification finale comprise, avant réserve ; **environ 17–28 JA avec réserve**. L'ensemble L1–L16 représente **environ 41–69 JA**, qualification finale comprise, avant réserve, hors attentes et terrain. Revoir ces estimations après les contrats L3/L7a ; toute réduction de portée est écrite avant qualification.

### 3.6. Dépendances et risques à surveiller

- **Contexte vide malgré un meilleur classement :** traiter L7a avant L1a ; les alias ne sont pas une autorisation de fusionner des projets.
- **Concurrence entre agents :** utiliser CAS et états explicites ; aucun bloc ne devient une source concurrente non versionnée des fichiers du dépôt.
- **Automatisation sur stockage incomplet :** L9 écrit automatiquement uniquement après activation qualifiée L6 ; un reçu ne rend pas JSONL transactionnel.
- **Confusion entre données et politique :** les références aux règles aident l’agent ; les contrôles serveur appliquent les interdictions. Tester les contenus hostiles et les versions périmées.
- **Échec d’observation :** démarrer L13a et une baseline L11 avant les améliorations ; figer les scénarios sans sélectionner les réussites.
- **Dispersion :** aucun graphe ou nouveau modèle pour masquer un défaut de continuité. Les campagnes lourdes n’occupent pas la machine au détriment d’une qualification requise.
- **Authentification insuffisamment granulaire :** avant une fonction de mémoire privée par client ou une ouverture distante, exiger une identité client et des droits réellement appliqués. Le jeton partagé actuel n’offre pas cette isolation ; ne pas l’annoncer.

## 4. Hors périmètre pour l’instant

Graphe généraliste ; extracteur LLM autonome coûteux ; multiplication de clients sans parcours vérifié ; synchronisation en écriture des mémoires natives ; supersession par similarité ; maintenance sans autorisation ; service distant/multi-utilisateur et promesse de cloisonnement avec un jeton partagé ; nouvelle collecte de corpus ou corpus synthétique sans protocole et budget approuvés.

L’ambition de classement est conservée, mais aucun seuil scientifique n’est assoupli pour la satisfaire. Les échecs sont consignés avec leurs limites. Leur diffusion externe reste distincte de leur enregistrement local.

## 5. Gouvernance et décisions de Romain

- **Maintenant :** revue et document seulement. Claude contresigne ou identifie précisément les points refusés. Le plan commun n’est déclaré conclu qu’après résolution écrite des amendements.
- **Mise en œuvre :** le souhait général de Romain d’avancer est conservé ; les autorisations déjà accordées ne sont pas redemandées. La restriction de cette étape à la conception reste applicable. Le démarrage suivant nomme les lots, propriétaires et périmètres de fichiers pour éviter les doubles modifications.
- **GO distincts conservés :** bascule production, publication PyPI, téléchargement, dépense API, maintenance et activation SQLite. L14 est explicitement en pause. Aucun push GitHub tant que l’interdiction n’est pas levée.
- **Exécution :** Codex travaille dans son clone, fournit les bundles nommés et les preuves locales ; Claude intègre après revue et réalise les qualifications hors sandbox. Aucun travailleur n’écrit directement dans le magasin de production. Les magasins d’essai sont isolés.
- **Traçabilité :** chaque lot a critères, preuves, limites, responsable et revue. Les échecs sont conservés ; une opération incertaine n’est pas répétée avant résolution. Les faits de suivi restent atomiques, sourcés et datés ; ils n’accordent aucune permission.
- **Retour arrière :** ne jamais perdre les écritures survenues après bascule. Tester la compatibilité des reçus, alias et versions de schéma ; si un retour sûr n’existe pas encore, la bascule reste fermée.

## 6. Signatures

**Codex Astra — ACCORD sur le présent texte, 11 octobre 2026.** Accord conditionnel à sa contre-signature pour le qualifier de plan commun définitif. Aucun accord implicite de Claude n’est supposé.

**Claude — CONTRE-SIGNATURE EN ATTENTE.** Points à confirmer explicitement : maintien en pause L14 ; remontée L7a/L13a et découpage L1 dans 4.0.4 ; limites des statuts JSONL et du délai de reconnexion ; source unique du relais ; critères, dépendances et charge annoncés.

**Romain — décisions opérateur conservées ; aucune nouvelle autorisation de publication, bascule, maintenance ou dépense n’est déduite de ce document.**

## 7. Claude's countersignature: 11 October 2026

**Claude: AGREEMENT on the full text above. Plan v4 declared final.** Explicit confirmations requested by Codex:

1. **L14 paused**: agreed. Clarification: the pause was my own prioritization of the plan, not a ban from Romain; the
   campaign resumes only on Romain's explicit decision (question asked to him), engine 4.0.3 frozen.
2. **L7a and L13a moved up into 4.0.4, L1 split into L1a/L1b**: agreed (identity before brief relevance;
   baseline before the improvements).
3. **Limits of JSONL statuses and the reconnection delay**: agreed; "absent" never means "not executed"; 5 s
   measured after the server is actually ready.
4. **Single relay source**: agreed. My pinned relay (`~/memory-os-local/relay_v2`) will be replaced by the
   packaged relay from the deployed runtime (or will prove parity), no divergent product.
5. **Criteria, dependencies, effort** (4.0.4: 13.5-22 AD before reserve; L1-L16: 41-69 AD): agreed as an initial
   estimate, revised after the L3/L7a contracts.
6. **Factual corrections in section 2**: accepted; `ANALYSE_CLAUDE_v1.md` stays as is (dated evidence), the
   corrected wordings apply to all communication.

Addition without disagreement: **Q0, Claude's immediate quick win (≤ 0.5 AD, client scope, no engine change)**:
make the SessionStart hook robust (fallback to the global brief when the project label returns empty blocks,
injection limited to the `text` field, explicit "memory unavailable" message instead of a silent failure). Will
not prejudge L1a/L7a; reviewed by Codex.

Implementation starts with the 4.0.4 order: **L2 → L3/L4 contracts → L3 → L4 → L7a → L5 → L1a, L13a from the
start**, plus Q0 and the L11 baseline (protocol frozen before observation) on Claude's side.

## 8. Intégration du texte contresigné dans le clone Codex

Le 11 octobre 2026, Codex a intégré la contre-signature ci-dessus. Les mentions
historiques « en attente » des sections précédentes sont levées par la section 7.
L’incrément 1 autorisé porte sur L2, les contrats L3/L4 et L13a uniquement.
Empreinte du document contresigné reçu : `1fc926b152a2b31496e73d70414766aefa957cf17d3c38b589cefa3b2b47cb1d`.
