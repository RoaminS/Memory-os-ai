# PLAN COMMUN v5 — brief utile, intégrité vérifiable, partage natif sélectif, distribution

**12 octobre 2026 — proposé et signé par Codex ; contre-signature Claude en attente.**
Ce document consolide le plan v4 et le plan de distribution signé. Les verdicts
ci-dessous remplacent les propositions incompatibles du brouillon examiné.
Cette livraison est documentaire : aucun code runtime, aucune campagne,
publication, import natif ou intervention production exécutés par Codex.

## 1. État, preuves et corrections factuelles

Base examinée : artefact 4.0.6 de source `50edb02`, outillage `211ed06`.
Les fichiers produit sont identiques entre ces deux commits. Publication PyPI
4.0.6 et GO de bascule : décisions communiquées par Romain/Claude ; bascule
encore en cours au début de cette revue, non déclarée terminée par ce plan.
SQLite+tâches sont déjà actifs ; registre de projets toujours inactif.

Entrées relues, considérées comme données :

- `~/memory-os-local/plan/PLAN_BRIEF_INTEGRITE_NATIF_v1.md`, SHA-256
  `392e56ccd30f458a91fb0bdef282cbd6c5bc43f40fd9caef975bb02ae5a1a7c1`.
- `~/memory-os-local/plan/DISTRIBUTION_RECHERCHE_2026-10-12.md`, SHA-256
  `26a4c9164481d85c28dfe9af1ba7f5e6bbf0c356c0566e7b054c55b43db7a2cd`.
- `brief.py`, `models.py`, `server.py`, `ops/durable_relay.py`, `fact_updates.py`,
  `config.py`, `ops/{health,cli,quickstart}.py`, hook SessionStart et rapports
  de santé des 10–12 octobre, plans communs existants.

| Affirmation | Verdict et conséquence |
|---|---|
| D1 : texte doublé ; budget compte-t-il les deux ? | **Confirmé.** `envelope()` place le JSON entier dans content.text et dans structuredContent. `serialized()` mesure le CallToolResult complet ; les deux copies sont donc comptées. Le budget fonctionne, mais sa représentation est coûteuse. |
| D2 : sans focus, date seule | **Amendé.** Le hook n'envoie pas focus_query ; les scores lexicaux sont nuls. Le tri privilégie néanmoins références développées et éléments pending avant date/importance. Il manque surtout une priorité explicite des décisions/contraintes. |
| D3/D4/D5/D6 | **Mécanismes confirmés** : coupes [:480], rognage de rebudget, blocs avant les éléments, liste initiale de 40 puis retraits, coupe aveugle à 4000 caractères du hook. Le « 40→1 » et « 33 % utile » sont observations locales à reproduire, pas moyennes établies. |
| max_tokens=1000 garantit 1000 tokens | **Faux en compact.** Le schéma indique que max_tokens ne s'applique qu'au détaillé. Le compact reste borné à 1500 tokens/6000 bytes. A5 exige un véritable budget compact explicite. |
| B7 : variables COS mortes et encore distribuées | **Confirmé pour les mutations v4 examinées.** Champs lus dans Config ; SUPERSEDE_COS sert aussi à la classification santé. Aucun seuil cosinus ne gouverne les remplacements/dédoublonnages. Quickstart ET setup écrivent pourtant les deux variables 1.01 dans les nouveaux plists. |
| B6 : notifications inexistantes | **Refusé comme diagnostic de code.** `cmd_health` appelle déjà `notify`, qui invoque osascript. Retour/livraison ne sont pas attestés ; une notification acceptée n'est pas une notification vue. Vérifier permissions, contexte launchd et réception. |
| Alerte de supersession répétée | **Cause confirmée** : baseline connue initialisée puis relue sans acquittement incrémental ; --confined/SUPERSEDE_COS classent encore la nouveauté comme anomalie. La légitimité de la décision 3cd1780e→dccdacb6 reste l'audit sémantique de Claude, pas une déduction de l'entité commune. |
| 1 remplacement /642 faits = péremption dominante | **Hypothèse**, pas mesure. Snapshot de santé du 12/10 : 608 faits valides, 1704 éléments ; dénominateurs/date/périmètre diffèrent. Un historique additif peut être correct. Annoter les contradictions d'état réellement actionnables. |
| Toutes les invalidations techniquement réversibles | **Amendé.** L'historique SQLite append-only rend une compensation possible, pas un rollback arbitraire sûr. Oublis, secrets, chaînes de corrections, validité temporelle et concurrence restent contraignants. |
| C peut envoyer source=claude-native et replaces sur feedback/procedure | **Non dans l'API actuelle.** Les modèles publics n'exposent pas ce champ source ; la validation des cibles exige kind=fact et le sweep ignore les procédures. Le contrat documentaire C5 est une dépendance, pas une adaptation de tags suffisante. |

Vision commune : une autorité locale partagée entre assistants, qui fournit le
minimum utile pour reprendre un projet ; décisions actuelles et preuves datées
distinctes ; corrections explicites et traçables ; mémoires natives conservées
et complétées sélectivement ; distribution fondée sur parcours vérifiés, sans
gain de productivité ou classement revendiqué avant mesure appropriée.

## 2. Verdict point par point — A, brief

JA = huit heures d'ingénierie équivalente, hors attente humaine ; estimations,
pas durées de génération. Chaque lot Codex est revu par Claude et inversement.

| Lot | Verdict / contrat retenu | Responsable ; effort | Fin vérifiable |
|---|---|---|---|
| **A1** | **Amender.** Écarter le texte humain brut qui casserait json.loads(content.text). Nouveau format compact : JSON historique dans content.text, texte du brief présent une seule fois ; pas de structuredContent dupliqué. Négociation de format ; ancien format disponible. Aucun outputSchema exigeant une structure absente. | Codex ; 0,75–1,25 JA | SDK/parser JSON, serveur/relais neuf, direct, et ancien relais testés ; compteur du transport final inclut toutes métadonnées/avis. Une occurrence du corps dans le nouveau format. |
| **A2** | **Accord amendé.** Sélection gloutonne déterministe, incrémentale, sans prétendre optimiser globalement un knapsack. Unités entières ; résumé extractif à frontière de phrase ou référence seule si une unité ne tient pas. Jamais couper un mot, SHA, URL ou segment de code ; aucune synthèse LLM implicite. | Codex ; 0,75–1,25 | Tests FR/EN, Unicode/identifiants longs, expansions et notices du relais : zéro fragment arbitraire ; références développables exactes. |
| **A3** | **Amender.** Tâches bloquées/open non périmées et contraintes critiques réservées, puis décisions actuelles, procédures, changements pertinents, faits ; date départage, ne définit pas la vérité. Types décision/contrainte explicites par tags/métadonnées, pas kind inventé. Client d'origine absent = inconnu ; « autre client » seulement si provenance disponible. | Codex ; 0,75–1,25 | Gold de priorité figé ; aucune contrainte critique faisable perdue ; diversité et départages reproductibles. Pas de faits hors projet pour remplir. |
| **A4** | **Amender.** 30 % blocs et 20 % tâches = plafonds initiaux sur budget de contenu restant, pas parts obligatoires. Budget libre réaffecté ; alertes critiques/indéterminées protégées. Si tout ne tient pas, annoncer les omissions critiques. | Codex ; 0,5–0,75 | Tests bloc dominant, tâches nombreuses, sections vides ; pas de sous-remplissage imposé, pas d'alerte cachée. |
| **A5** | **Accord amendé.** Focus projet/branche = indice faible ; première demande donne le focus utile. Supplément UserPromptSubmit une fois par session, après support annoncé par le serveur : enveloppe <=600 tokens, délai total <=2 s, aucune inférence, aucune sauvegarde du prompt. Échec secondaire silencieux pour l'utilisateur, diagnostic local borné. | Claude ; 0,75–1,25 | Fake CLI/hook + tests réels autorisés : un appel maximum, délai borné, aucun retry mutation, secrets/texte complet absents des logs. |
| **A6** | **Amender.** Préférences de présentation client déclaratives, pas droits. Delta fondé sur curseur B5/L1b explicite ; pas « tout ce que Codex sait depuis la dernière session » inféré d'un nom. Pas d'exclusion globale des souvenirs natifs. | Codex + Claude ; inclus B5/C5, pas additionné | Client inconnu reçoit un brief valide ; curseur périmé signalé ; suppression de contenu seulement avec références de versions effectivement déjà chargées. |
| **A7** | **Accord amendé.** Compter candidats éligibles, rendus et omis par type/scope, développement disponible ; distinguer candidate pool et corpus entier. Rien ne révèle les volumes d'un autre projet dans un brief scoped. | Codex ; 0,5–0,75 | Compteurs exacts sur fixtures, sans double comptage ; notice de saturation elle-même tient dans l'enveloppe. |
| **A8** | **Accord.** Retirer la recoupe aveugle du hook. Compter son contexte FINAL, notes L9/B6 et tâches comprises. Ne pas rappeler task_list si les mêmes tâches sont déjà présentes. L'avis initial Q0 d'indisponibilité reste visible. | Claude ; 0,5–0,75 | Zéro coupe additionnelle d'élément ; un rendu de chaque tâche ; budget du hook scellé ; tests panne/projet inconnu/global. |
| **A9** | **Accord amendé.** Qualification transport, qualité de sélection et copie avant/après distinctes ; anciens 100 scénarios = régression, pas test indépendant de progrès. Ajouter un jeu de validation tenu hors réglage. | Claude exécute, Codex protocole/revue ; 1,5–2,5 | Gates de §6, rapports/échecs conservés, T05 et copie qualifiés avant publication/bascule 4.0.7. |

Le « >=85 % utile » universel est **refusé comme gate** : les petites réponses,
références et avertissements ont un coût légitime ; caractères uniques ne
mesurent pas l'utilité. Publier séparation corps/métadonnées/échappement/duplicata
et couverture des informations nécessaires. Le gain ×2 reste hypothèse.

Contrat A1 à sceller : `wire_format=legacy|compact_v2` sur le même outil,
support annoncé dans /ready. Ancien appel direct et ancien relais conservent
le legacy ; nouveau relais négocie compact_v2 seulement si support annoncé.
Le budget se vérifie APRÈS son ajout d'avis locaux. Dans compact_v2,
content.text reste un objet JSON avec mode/project/text/notices/références ;
structuredContent absent et aucun outputSchema qui le rende obligatoire.
`budget_tokens` optionnel compact, 200–1500, distinct de max_tokens détaillé ;
avis d'erreur et budget doivent aussi respecter une demande à 600 tokens.
Ne pas changer implicitement le comportement d'un relais 4.0.6 : ce dernier
recrée aujourd'hui le doublon via envelope après son propre rebudget.
Depuis HOME, dashboard de projets/alertes borné sans mélange de faits par défaut ;
contenu multiprojet uniquement après choix explicite de portée, jamais repli caché.

## 3. Verdict point par point — B, intégrité

| Lot | Verdict / contrat retenu | Responsable ; effort | Fin vérifiable |
|---|---|---|---|
| **B1** | **Accord amendé.** Annonce des invalidations et dédoublonnages retournés, références et raison ; texte ancien via lecture explicite scoped. Tous clients reçoivent un résultat explicite ; hook Claude complète l'affichage sans prétendre prouver l'attention humaine. | Claude ; 0,5–0,75 | Toute mutation testée expose ses impacts ; événement rejoué ne génère pas un second changement ; informations sensibles absentes des notifications. |
| **B2** | **Amender.** Raison requise pour une invalidation effective, cibles exactes et révisions attendues/CAS, projet/kind/temps vérifiés. Entité commune = indice/avis, jamais preuve ni refus automatique. Prévisualiser impacts ; >3 invalidations requiert confirmation explicite de la liste d'impacts, pas une simple phrase reason. | Codex ; 1–1,5 | Audit hors ligne sur copie puis matrice adversariale ; zéro fausse invalidation/lot partiel, CAS stale refusé ; taux de refus légitimes documenté. |
| **B3** | **Accord amendé.** Refuser nouvelles identités single à prédicat générique other/unknown/default après normalisation du contrôle. Identité != facette : vocabulaire précis extensible, pas enum de facettes. Anciennes identités restent lisibles, jamais renommées automatiquement. | Codex ; 0,25–0,5 | Tests homonymes, multi, égalité de dates, prédicats spécifiques ; lecture historique inchangée. |
| **B4** | **Refuser la réouverture proposée ; amender en compensation.** Pas de nouvel outil memory_revert. Étendre l'API de correction existante : aperçu puis nouvelle assertion actuelle rétablissant le contenu antérieur, remplaçant la tête corrigée avec raison, CAS et reçu. Ne pas rouvrir sa fenêtre passée ni effacer la correction. | Codex ; 1,5–2,5 | Transaction SQLite unique ; historique temporel avant/pendant/après correct ; cible changée/oubliée/secret refusée ; statut après interruption résolu sans rejeu aveugle. Exercice opérateur <5 min après disponibilité. |
| **B5 / L1b** | **Accord amendé.** Étendre memory_timeline(view=changes, since_cursor, limit) plutôt que créer memory_changes ou surcharger request_status. Événements durables issus des mutations/reçus, pagination/cursors liés à instance+scope ; restauration/rétention/perte du curseur explicitement signalées. Curseur avancé seulement après injection réussie. | Codex ; 2–3 | Corrections, oublis, doublons/no-op, tâches/blocs et compensation identifiables ; panne n'annonce pas un événement non committé ; couverture des opérations anciennes explicitée, pas reconstruite comme certaine. |
| **B6** | **Accord amendé immédiat.** Diagnostiquer la chaîne existante, IDs d'alertes stables, classification mécanisme versus revue sémantique, accusés persistants et escalade. Acquittement local 0700/0600 et écriture atomique ; pas prétendre à une signature cryptographique sans ancre/clé/menace définies. | Claude client/ops 0,75–1,25 ; Codex santé 0,5–0,75 | Notification tentée/acceptée/échec distincts ; réception opérateur démontrée ; même alerte acquittée non présentée comme nouvelle ; source inaccessible jamais considérée saine. |
| **B7** | **Accord amendé prioritaire.** Cesser de générer COS et de les appeler protection ; déprécier les options/--confined avec avertissement explicite, ne pas empêcher le démarrage d'un utilisateur existant pour ces seules variables inertes. Publier la politique réelle explicit-only dans ready/doctor/santé et docs. | Codex ; 0,5–0,75 (+ santé ci-dessus) ; Claude plists sous GO | Nouveau setup/quickstart/jobs sans COS/--confined ; ancien env accepté avec avis ; variations COS n'ont aucun effet sur mutations ; dédup exacte, jamais cosinus. |
| **B8** | **Accord exploratoire, différé.** File de propositions d'état contradictoire, sans invalidation automatique ; distinction affirmation historique/état actuel. Préférer état courant dans blocs/tasks CAS plutôt que reclassifier tout l'historique. | Codex détecteur 1–2 ; Claude audit 0,5–1 | Jeu annoté, précision et faux signalements publiés ; décisions manuelles datées. Pas de gate « diviser par 2 » avant baseline et périmètre scellés. |
| **B9** | **Accord amendé.** Propriétés des faits : aucun fait clos sans replaces/identité single validés. Révision documentaire C5 : clôture uniquement par lignée explicite/CAS. Rejouer incidents, confusion entités, temporalité, concurrence, compensation, crash à chaque frontière. | Codex ; 1–1,5 ; Claude attaque/rejeu | Historique + reçu final atomiques SQLite ; zéro succès annoncé pour lot partiel ; JSONL ne reçoit aucune promesse transactionnelle nouvelle. |
| **B10** | **Différer, maintenance toujours désactivée.** Chaque opération nécessite aperçu sur copie, impacts exacts, contrat de compensation/caches et GO spécifique. Decay/importance ne sont pas annulés par une simple compensation de faits. | Claude ops ; 0,5–1 après contrat distinct | Aucun réveil automatique de consolidate/compact ; diff auditable ; retour arrière préservant nouvelles écritures. Hors gate 4.0.7. |

Compensation B4 composée et source révision C5 : **SQLite uniquement pour apply**
tant que la même garantie n'existe pas en JSONL ; lecture et aperçu conservés,
apply JSONL refuse backend_unsupported. Pas de bypass maintenance/read_only,
ni exception automatique parce qu'une invalidation a moins de sept jours.

Le clientInfo est déclaratif. Un remplacement « autre client » produit un avis
de provenance, pas une décision d'autorisation. Les alertes déjà acquittées
restent consultables ; une aggravation disque/backup ouvre un nouvel épisode.
Le défaut disque 5 GB / dérogation provisioning-qualification >=3 GB ne change
pas la réserve **S4 de 5 GB plus espace de travail**. Le rapport du 12/10 donne
4,1 GiB et 4 378 345 472 bytes : l'alerte stockage y est fondée.

Contrats B4/B5 à faire contresigner avant code : correction compensatrice via
`memory_add_facts` (extension de l'entrée, aperçu et apply explicitement
distincts), raison + références/révisions attendues + request_id. La confirmation
des impacts lie le payload et les cibles attendues ; ne jamais l'appeler preuve
d'accord humain parce qu'un modèle a envoyé confirm=true. Sur cible oubliée
ou refusée, zéro écriture et outcome not_started. Timeline changes paginée :
curseur opaque lié à instance/scope/version du contrat et ordre de commit ;
acteur client déclaré/inconnu, mécanisme, IDs/révisions avant-après, raison,
reçu. Les propositions refusées et no-op ne sont pas annoncés comme changements
committés. Event_time métier et temps de commit sont distincts. Aucun endpoint
de statut ne sert à autoriser un rejeu d'une mutation encore incertaine.

## 4. Verdict point par point — C, natif

**Clarification/amendement du v4 :** aucune écriture dans les fichiers de mémoire
native. Un import unidirectionnel, sélectif et autorisé dans la mémoire partagée
peut être expérimenté chez Romain ; ce n'est pas une synchronisation générale
publique livrée par 4.0.7. « Garder les deux mémoires » est déjà décidé : ne pas
redemander ce principe. Le contenu réellement partagé reste à approuver.

| Lot | Verdict / contrat retenu | Responsable ; effort | Fin vérifiable |
|---|---|---|---|
| **C1** | **Accord amendé, dry run.** Parsing déterministe, allowlist de racines/projets, liens symboliques refusés, contrôle de stabilité du fichier, scanner + exclusions machine-local. Mapping inconnu = quarantaine locale, pas default/claude-native global. MEMORY.md index distingué des documents pour éviter duplication. | Claude ; 0,75–1,25 | Aucune requête d'écriture dans dry run ; rapport masqué de périmètre/mapping/refus ; fichiers natifs et source partagée inchangés. |
| **C2** | **Amender.** Révisions de documents, pas transformation automatique de paragraphes en faits atomiques. CAS de lignée source_id + SHA précédent, nouvel événement observé ; SHA inchangé = no-op. Suppression/renommage proposés et tracés, pas déduction de remplacement de tous les faits dérivés. | Claude client ; 0,5–1 ; moteur inclus C5 | Deux agents concurrents ne publient pas deux têtes ; timeout incertain bloque la lignée jusqu'au statut ; chaîne reprise après crash/idempotence. |
| **C3** | **Amender.** Hooks après Write/Edit + réconciliation incrémentale locale ; pas blocage de SessionStart par un import. File bornée, notification du retard ; aucune modification auto du natif. | Claude ; 0,5–0,75 | Cas modification manuelle, édition non atomique, suppression, renommage, panne ; contrôle sans changement p95<=1 s ; en régime prêt publication p95<=5 s, latence de panne exclue et annoncée. |
| **C4** | **Accord.** Aucun chemin d'écriture vers ~/.claude/projects/*/memory ; état technique séparé en 0700/0600. Ne copier aucun fichier d'authentification Claude/Codex/Grok. | Claude ; 0,25–0,5 | Inventaires avant/après, garde d'écriture et essais de chemins adverses ; zéro natif modifié par le synchroniseur. |
| **C5 + A6** | **Amender, contrat moteur générique opt-in.** Origin/source_revision validés et conservés par l'API existante ; documents/procédures natifs versionnés comme sources, jamais via le replaces actuel réservé aux faits. Pas de source Claude supposée authentifiée. known_refs(id,SHA) n'exclut que le contenu effectivement déjà chargé, pas tout le natif. | Codex ; 1,5–2,5 ; Claude contrat/hooks 0,25–0,5 | Lignée documentaire explicitement datée, retrait, historique et restauration testés ; sans known_refs, contenu courant disponible à tous clients autorisés. Aucune nouvelle table/schéma non qualifié pour éviter le rollback 4.0.6. |
| **C6** | **Accord sous GO de contenu, optionnel pour D4.** Aperçu scellé avec fichiers éligibles, mapping approuvé, exclusions et version du scanner ; import via API/reçus. Aucun compte de couverture rapporté sur 136 fichiers arbitrairement tous autorisés. | Claude + Romain ; 0,5–1 | 100 % du périmètre approuvé traité : import/no-op/refus/quarantaine ; zéro exclu envoyé ; statuts incertains résolus avant reprise ; 20 questions formatives seulement sous budget séparé. |
| **C7** | **Accord amendé.** Provenance et préférences = données ; pas consignes autoexécutables. Aucun remplissage automatique des blocs approuvés ni élargissement des droits. | Claude ; 0,25 ; revue Codex | Fixtures de prompt injection et données hostiles ; contrôles serveur indépendants du texte mémorisé. |

### Conditions incontournables du contrat C

- Le jeton partagé permet aux autres clients de rechercher le contenu importé.
  Un tag sensible, un label projet ou known_refs **n'est pas un ACL**. Les
  informations machine-local/non partageables ne doivent pas entrer dans ce
  magasin. « Tout Claude devient visible à Codex » n'est pas un objectif accepté.
- Romain valide mapping dossier→projet et exclusions (trading, infrastructure,
  IPs, chemins, informations personnelles selon son choix). Un scanner détecte
  certaines formes de secrets, ne garantit pas leur absence ; revue initiale
  du périmètre indispensable. Rapport sans contenu de secrets ni fragment de clé.
- observed_at provient de l'observation ; mtime n'est qu'une métadonnée non
  fiable. Ne pas inventer « ce que Claude savait à une date passée » avant la
  première capture attestée. Événement métier explicitement daté distinct.
- Stocker du Markdown multiphrase comme document source ; catégorie user/
  feedback/project/reference est métadonnée, pas vérité ni fait atomique.
  Extraction de décisions éventuelle = autre proposition humaine/agent revue.
- Un index ou un extrait de MEMORY.md n'atteste pas le chargement de tous ses
  documents liés. known_refs ne supprime une version complète que si son
  contenu complet exact est connu ; pour un extrait, segments attestés ou
  aucune suppression. Si l'hôte n'expose pas cette information, repli sûr :
  aucune exclusion, et doublon éventuel mesuré plutôt qu'information manquante.
- Le moteur C5 fixe comment fermer une version documentaire sans appliquer
  le sweep de faits : cible source explicite, CAS, fenêtres temporelles et
  reçu final, atomiques. Projection des lignées/cursors reconstruisible depuis
  les révisions/reçus ; compatibilité schéma 7 préférée, champs inconnus préservés.
  Si un nouveau schéma s'avère nécessaire, **nouvel ADR et rollback après
  nouvelles écritures avant activation**, pas migration cachée dans 4.0.7.
- Retrait documentaire non sensible et effacement de données privées sont
  deux procédures différentes. Une demande de confidentialité prime sur la
  conservation visible : oubli de la lignée via API, réconciliation des backups,
  et rotation du secret à sa source si incident. Aucun revival automatique.
- API générique livrée opt-in/inactive ; scripts dogfooding restent chez
  Claude. Import, scanner et droits spécifiques ne sont pas annoncés comme
  fonction native universelle du produit.
- C6 ne charge que les SHA approuvés : contenu modifié après aperçu = nouvelle
  proposition, pas envoi sous l'ancien consentement. L'autorisation d'un import
  initial ne vaut pas autorisation de synchronisation continue. Romain peut
  approuver explicitement un périmètre continu avec exclusions ; hors de ce
  périmètre, C3 met en file une proposition sans écrire dans la mémoire partagée.

## 5. Réponse aux cinq critiques de Claude

| Critique | Réponse explicite |
|---|---|
| **a — deux outils supplémentaires** | **Accord.** Correction/compensation via API existante et journal via timeline. request_status reste statut des appels, pas historique métier. Zéro outil MCP supplémentaire visé ; coût des schémas mesuré, pas seulement leur nombre. Si un multiplexage rend les droits ambigus, contrat revu avant exception. |
| **b — entité liée, raison et faux refus** | **Accord amendé.** L'entité commune ne bloque pas ; reason est nécessaire à l'audit mais ne prouve ni lien ni autorisation. CAS/identité précise/projet/temps/impacts sont les garde-fous. Histoire réelle trop peu corrigée pour une estimation robuste : publier effectifs, ajouter gold adverse. |
| **c — deuxième brief <=600 tokens/2 s** | **Accord**, budget compact explicite et mesure totale du hook ; silence sur cet échec secondaire seulement. Q0 et alertes critiques initiales restent visibles. |
| **d — confidentialité et dogfooding** | **Accord renforcé** : inconnus en quarantaine, pas d'import global default ; pas d'exclusion sur un simple nom de client ; scanner non infaillible ; import Romain-only sous GO, sans copies d'auth. |
| **e — 4.0.7 avant lancement large** | **Accord sur brief/B7 prioritaires.** B6 client immédiat après bascule ; correction de classification santé reste produit Codex. Scope 4.0.7 inclut B2–B5/B9 et socle A6/C5 opt-in comme demandé, mais B8/B10 différés. Désaccord explicite : C6 optionnel ne doit pas bloquer D4 du produit. |

## 6. Mesures à pré-enregistrer avant implémentation/résultats

Claude scelle les gold/attendus ; Codex scelle le protocole, paramètres et
analyse avant réglage. Développement et validation séparés ; tout changement
de seuil après observation est un amendement daté, jamais une réussite rétroactive.
Pas d'inférence ni nouvelle donnée téléchargée dans cette revue.

| Campagne | Protocole / seuils figés proposés | Preuve et limites |
|---|---|---|
| **P-A : transport** | Sérialisation réelle serveur + relais + SDK, JSON compact UTF-8 excluant None, cl100k local exact ; 100 % <=6000 bytes ET <=1500 tokens ; supplément A5 <=600 tokens. Nouvel encodage du corps une fois. Tester ancien format et négociation, erreurs/avis compris. | Fixtures L1a SHA `8d005cf0fa6de55ec75e1f023a4fa02f802a99dc73589fe5e38241914baa2209` inchangées ; 100 scénarios/400 mesures et TCP. Analyser ratio de texte unique par déciles de taille, pas promesse universelle 85 %. |
| **P-A : utilité** | Baseline 4.0.6, candidat figé, 100 scénarios régression + 40 nouveaux FR/EN 20/20, cinq familles, annotations critiques et feasibility définies avant code. Au moins 38/40 couvrent tous les besoins critiques faisables ; 0 contenu invalide/hors portée/secret explicitement tagué ; 0 fragment coupé ; 100 % références valides. | Jeu de dev distinct proposé par Codex ; Claude garde le gold des 40 jusqu'au gel du candidat. Faisabilité = coût des unités obligatoires + overhead réel compatible avec les deux budgets, défini sans regarder le résultat candidat. Toutes exclusions publiées. |
| **P-A : performance** | Aucun encodeur/LLM appelé par build ; chaud 10k éléments synthétiques, 100 appels séquentiels, serveur p95<=250 ms, transport p95<=750 ms ; suppl. hook total<=2 s, contexte injecté SessionStart<=1500 tokens et supplément<=600. | Profil/machine/cache et p50/p95/max, bytes, tokens, appels comptés ; échauffement déclaré. Sur copie réelle, conserver baseline/candidat et toutes omissions. Pas de gros job pendant la qualification de bascule. |
| **P-B : mutations** | 40 scénarios adverses scellés (cibles différentes, sans entité commune, single/multi, chaines temporelles, concurrence, cross-client, compensation, retrait documentaire, secrets/oubli) + incidents réels en copie. Zéro invalidation inattendue ; 100 % lots partiels refusés ou annulés sans succès annoncé. | Injection à chaque écriture SQLite avec reçu final. Toute correction légitime annotée sans entité commune doit passer avec impacts/CAS corrects ; audit de l'histoire en lecture seule, effectifs/faiblesse d'échantillon déclarés. |
| **P-B6 : visibilité** | 12 événements synthétiques incluant alerte légitime revue, suspecte, illisible, disque insuffisant, backup périmé, aggravation, doublon, permission notification refusée. Tous enregistrés ; critique détecté à la prochaine sonde <=24 h et visible au prochain SessionStart ; notification tentée <=60 s après détection. | Test manuel réception macOS + cas notification bloquée ; ACK distinct du transport. Même événement acquitté n'est pas « nouveau » ; non-acquitté récapitulé/escaladé sans flood. Aucun détail secret dans un banner. |
| **P-C : import** | 30 fixtures avant implémentation : secrets, faux positifs SHA/IDs, projets inconnus, liens, courses d'édition, duplicates/index, retraits, CAS, panne/reprise. Zéro source exclue envoyée ; tous statuts comptés. Régime prêt : p95 publication<=5 s ; réconciliation inchangée p95<=1 s. | Fake serveur/CLI puis magasin privé. Aucun auth file copié. ≥16/20 questions correctement sourcées après import est cible formative sous GO, sans claim causal ni comparabilité leaderboard. |
| **P-D : adoption** | Cinq développeurs Mac multi-assistants, SDK/apps réellement installés : >=4/5 installent puis sauvegardent/retrouvent dans un autre client en <10 min, sans intervention de l'auteur ; suivi J+7, erreurs dans le dénominateur. | Les essais propres SQLite 77 s/JSONL 80 s sont preuve d'installation, pas preuve 4/5 utilisateurs ni gain de reprise. L11 reste bloqué, campagnes réponse restent en pause sauf décision dédiée. |

Le coût du catalogue MCP (schémas/descriptions) est gelé comme baseline avant
ajouts : cible <=+10 % de tokens, 0 outil additionnel ; dépassement exige revue
du contrat, pas remplacement par une API opaque. Aucun seuil de performance
ne justifie suppression d'un contrôle de validité ou hausse automatique des budgets.

L'enveloppe contractuelle est le **CallToolResult complet sérialisé**, comme
dans les preuves L1a ; mesurer séparément framing JSON-RPC/HTTP et rendu du
client. Ne pas rebaptiser le budget du résultat en budget de toute la requête
réseau ou de tout le prompt de l'agent. Les 40 nouvelles validations sont
formatives pour ce produit, pas preuve d'un score leaderboard.

## 7. Séquencement unique, responsables et dépendances

| Ordre | Lot / livrable | Propriétaire / dépendance / charge |
|---|---|---|
| **0** | Finir bascule 4.0.6, copie SQLite/relais/manifeste, amendements a8, ready/doctor, jobs et rollback même store | **Claude**, revue Codex ; GO déjà donné, pas à redemander. Run 64 prioritaire ; aucun import de ce bundle ni changement clone pendant son exécution. |
| **1** | B6 diagnostic/visibilité client et B7 contrat vérité/public warning préparés ; documenter que COS est inerte | **Claude** hors dépôt ; **Codex** contrats/docs puis correctif produit dans branche isolée. Retrait du plist live seulement selon procédure/GO ; ne pas croire qu'un retrait répare rétroactivement santé 4.0.6. |
| **2 — voies parallèles bornées** | **Produit 4.0.7 : A1→A2→A4→A3→A7, puis B3/B2→B4/B5→B9 ; A6/C5 dépend de B5 et du contrat documentaire.** | **Codex**, revue Claude ; ~12–19 JA moteur/contrats/tests, pas les 2–3 JA du seul brief. Préférer incréments de contrats, pas release partielle annoncée complète. |
| **2 — client** | A8/B1/B6, A5 après capacité budget/format, C1–C4 dry run seulement | **Claude**, revue Codex ; ~5–9 JA. État temporaire/hook seuls dans son périmètre ; aucune écriture native ni import partagé. |
| **2 — distribution préparée** | Registre, accueil README PyPI, boutons Cursor/VS Code, prototype .mcpb relais ; publication seulement sous GO | **Claude** textes/liens/opérations ; **Codex** manifests/qualification ; ~2–4 JA. Préparation parallèle permise ; aides n'affirment pas que 4.0.7 est publiée. |
| **3** | A9 + B9, T05 complet 4.0.7, copy/schema/caches/retour 4.0.6 après écritures nouvelles, profils MPS et JSONL/SQLite supportés | **Claude** hors sandbox, **Codex** harnais/revue ; ~2–4 JA. Aucun corpus/modèle nouveau, aucun basculement implicite de profil. Tous contrôles requis verts avant soumettre release. |
| **3 — option native** | C6 import sélectif et vérification des reçus après dry run, mapping/exclusions/protocole signés | **Claude + Romain** sous GO contenu. Peut rester désactivé sans bloquer publication 4.0.7 ni D4 ; changements de schéma nécessitent gate distinct. |
| **4** | D1c/P-D et D3 usage J+7 ; corrections des parcours/frictions constatées | **Claude**, revue Codex ; ~1–2 JA + attente terrain. Auth native existante, aucun contournement du refus L11. |
| **5** | D4 lancement ciblé : produit Mac multi-assistants, démo locale, PyPI/registry/liens, posts autorisés individuellement | **Claude**, exactitude technique Codex, GO Romain. Gates A9/T05, P-D, D2 minimum et suivi D3 atteints. Pas dépendant d'un import natif privé. |
| **6** | Linux CPU + service utilisateur puis Windows ; formats additionnels après demande observée | **Codex portabilité**, **Claude qualification** ; Linux 4–7 JA, Windows 8–15 JA (estimations précédentes à revalider). Qualification dédiée, aucune promesse cross-platform anticipée. |
| **Après** | B8 puis éventuel B10 ; campagnes leaderboard/end-to-end, imports généralisés et packaging autonome selon décisions séparées | Aucun réveil implicite. Replanifier à partir des mesures, pas disperser le chemin critique. |

Total préparatoire avant D4 : **22–38 JA**, réserve de 25 % = **28–48 JA**,
hors attentes terrain et nouvelle plateforme. La charge A9 est incluse dans les 2–4 JA de qualification, pas additionnée
une seconde fois. Les tâches parallèles ne sont pas toutes simultanées ; deux travaux lourds maximum autorisés, exécution
séquentielle sur ce Mac. Les coûts A6/B5 et C5 ne sont comptés qu'une fois.
Chaque incrément est livré par bundle nommé, revue, preuves ; pas de qualification
sur un arbre en train d'être modifié. Ce plan ne crée aucune nouvelle autorisation
de publication, maintenance, téléchargement ou dépense.

## 8. Distribution PyPI-first corrigée

| Canal | Décision / dépendance / critère |
|---|---|
| **Registre MCP officiel** | Préparer metadata relay stdio pointant vers la version QUALIFIÉE publiée. GitHub OAuth n'exige pas de push ; GO explicite login/publication. Valider schema, namespace, ownership PyPI/mcp-name, et qu'un consommateur configure le relais, pas un nouveau store. Ancienne v3.0.1 signalée par Claude ; API non accessible lors de cette revue, donc relire l'entrée avant publier. |
| **README PyPI / landing** | Canal principal, français/anglais selon public, LGPL de l'artefact, Mac Apple Silicon qualifié, ressources/profil/disk/serveur/token visibles, incidents corrigés et limites. Le long_description vient de la release : un README local ne modifie pas la page 4.0.6 ; intégrer les changements dans 4.0.7, ne jamais ré-uploader une version identique. |
| **Boutons Cursor / VS Code** | Générer configurations du RELAIS, token par chemin local, jamais en URL/base64 ni server engine par client. Vérifier rendu réel PyPI et clic dans apps/installations propres ; fournir fallback texte si URI filtrée. Bouton = configuration, pas provision automatique, ni preuve d'usage. |
| **.mcpb** | Prototype extension de connexion au service déjà installé, avec source unique du relais empaqueté. Ne pas embarquer tous Torch/Granite pour chaque client, ni faire passer un launcher pour l'installation complète. Vérifier capacités de l'hôte, dépendances natives, token/URL, panne, upgrade/uninstall conservant store ; consentement et GO publication. |
| **uv / uvx / pipx** | Qualifier installation durable versionnée pour le daemon ; uvx peut servir essai/relais avec version/extra explicites, pas référence de résidence d'un service launchd dans un cache temporaire. Ni ressources gratuites ni installation petite par le seul changement d'outil. |
| **Homebrew tap** | Peut utiliser un Git distant autre que GitHub, mais nécessite hébergement/dépôt accessible et maintenance. Pas « sans repo », pas contournement du gel. Différer du chemin critique jusqu'au GO canal/hébergement et parcours update/rollback qualifié. |
| **Canaux dépendants du repo public** | Plugin marketplaces, Gemini gallery, awesome-mcp-servers, claim Glama, Smithery : en attente selon décision Romain ; dépendances exactes réexaminer avant reprise. Ne pas recopier en fait établi des exigences OpenAI/marketplaces non vérifiées ici. |

Sources primaires consultées le 12/10, sans publication :
[MCP tools/structured content](https://modelcontextprotocol.io/specification/2025-06-18/server/tools),
[auth registre](https://modelcontextprotocol.io/registry/authentication),
[Cursor install links](https://cursor.com/docs/mcp/install-links),
[VS Code MCP](https://code.visualstudio.com/docs/agent-customization/mcp-servers),
[MCPB, formats et runtimes](https://github.com/modelcontextprotocol/mcpb),
[uv tools](https://docs.astral.sh/uv/guides/tools/),
[Homebrew taps](https://docs.brew.sh/Taps).

La duplication structurée actuelle suit une recommandation de compatibilité MCP,
pas une nécessité d'avoir deux corps pour une réponse textuelle sans outputSchema.
Les formats/runtimes MCPB restent à vérifier dans l'hôte installé : un UV bundle
ne dispense pas du gate natif. Les statistiques concurrents/téléchargements,
étiquettes « Claude seulement » et allégations CVE non vérifiées sont écartées
du message public. Positionnement = local, partagé, daté, tâches/CAS et preuves,
pas exclusivité absolue, « plus complet que tous » ou top 1.

## 9. Décisions de Romain, numérotées

**Déjà actées, non redemandées :** publication/bascule 4.0.6, maintien des deux
mémoires, GitHub gelé, cible Mac multi-assistants puis Linux/Windows,
SQLite+tâches pour installs neuves, profil production auto/MPS/native/batch32/C4.

1. Priorité/scope 4.0.7 et enveloppe estimée 28–48 JA ; B8/B10 et campagnes de
   classement différés ; pas d'inférence supplémentaire implicitement autorisée.
2. Confirmer compensation B4 avec CAS/reçu, sans réécriture historique, et
   niveau de validation des impacts >3/corrections sensibles ; apply SQLite-only.
3. Valider le périmètre natif partagé : allowlist projets, mapping, exclusions
   machine-local, traitement des retraits/confidentialité et risques résiduels.
4. GO C6 sur un aperçu précis et scellé après qualification ; consentement à
   une étude 20 questions/budget de quota distinct, ou revue manuelle sans agent.
5. Publication PyPI 4.0.7 et bascule locale après T05/A9 : GOs séparés ; aucun
   de ces GOs ne se déduit de celui de 4.0.6.
6. GO explicite registre/OAuth, changements publics README/liens/.mcpb et
   chaque post D4. Pas de connexion compte ou message public exécutés ici.
7. Hébergement éventuel tap/domaine et budget ; aucune nouvelle source Git
   publique pour contourner le gel, aucun changement GitHub sans autorisation.
8. Temps/recrutement cinq testeurs Mac, protocole de collecte privée et J+7 ;
   priorité Linux ensuite, budget/machines de qualification à préciser.
9. Actions disque/notifications nécessaires : libération opérateur de place
   pour S4 si sous réserve, permissions de notification et personne qui acquitte.
   Aucun fichier d'un autre projet supprimé ni garde S4 abaissée automatiquement.
10. Maintenance B10 ou reprise des évaluations L14/L11 seulement par décision
    explicite ultérieure ; refus de copier les auth files toujours respecté.

## 10. Signatures et désaccords à résoudre

**Codex — ACCORD sur ce texte consolidé, 12 octobre 2026.** Accord sur les
objectifs et l'ordre ; aucun accord supposé de Claude ni GO supplémentaire de Romain.

**Claude — CONTRE-SIGNATURE EN ATTENTE.** Confirmer notamment : format JSON
compatible/négocié ; entité = avis ; compensation plutôt que réouverture ;
notifications déjà existantes mais non attestées ; classification sans preuve
automatique de légitimité ; documents natifs/scanner/known_refs sélectifs ;
C6 optionnel pour D4 ; effort/mesures et maintien des réserves S4.

**Désaccords explicites avec le brouillon, non lissés :** seuil universel 85 %
utile ; entity-only refus ; légitimité automatique ; annulation qui rouvre le
passé ; source client considérée authentifiée ; exclusion totale du natif Claude ;
import de tous les fichiers ; mtime présenté comme connaissance passée ;
suppression native assimilée à autorisation de purger/à obligation de conserver ;
import privé rendu obligatoire pour lancer le produit. Si Claude refuse un
amendement, consigner le point et résoudre avant déclarer v5 définitif.
