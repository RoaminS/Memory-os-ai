# L1a — brief compact (candidate 4.0.4)

`memory_session_brief` utilise désormais `mode="compact"` par défaut au niveau MCP.
Le mode `detailed` est explicite ; l'API Python historique `engine.session_brief`
garde son format détaillé. Les anciens tests de structure demandent detailed.
`max_tokens` reste une limite approximative du texte détaillé seulement ; le compact
utilise les plafonds fixes de son enveloppe entière.

## Contrat

Compact : `text` à injecter par le hook, `references` (id/kind), `blocks` (références),
`project` canonique et `notices`. L'enveloppe CallToolResult entière, content ET
structuredContent, est comptée après sérialisation JSON compacte UTF-8,
`by_alias=True`, `exclude_none=True` : ≤6000 octets ET ≤1500 tokens cl100k_base.
Le cadre JSON-RPC/id de transport est exclu, conformément au protocole scellé.
Le résultat SDK reçu est vérifié dans un test MCP sur streams en mémoire ; le test
TCP correspondant doit aussi réussir avec le compteur exact. Pas de compensation
« texte court mais métadonnées illimitées » : erreurs et notices sont incluses.

Les éléments sont filtrés par projet/alias et validité actuelle AVANT sélection,
quel que soit leur kind. Supprimés, expirés, futurs, invalidations sans borne et
éléments explicitement tagués `sensitive` sont exclus. Une invalidation datée dans
le futur suit sa borne temporelle. Aucun filtre sémantique magique de secret.
Le mode détaillé applique aussi la validité à ses sections/focus.
Les blocs conservent leur contrat d'héritage explicite, sans validité inventée.

Sélection déterministe, sans encodage neuronal : références demandées, enfants
directs, éléments en attente, score BM25 dans le périmètre valide, date, importance,
ID pour départager. Plafond de candidats et troncature explicites ; `budget_saturated`,
`item_truncated`, `block_truncated` signalent un contexte incomplet. Pas de score
benchmark ni d'affirmation d'amélioration du rappel attachée à cette sélection.

```json
{"project":"prj_memory_os_ai","focus_query":"prochaine action","mode":"compact"}
```

Développer une référence :

```json
{"project":"prj_memory_os_ai","mode":"detailed","expand":["id-retourne"]}
```

Les enfants directs valides sont inclus, toujours dans le même projet. Pour un
bloc : `expand:["block:working_rules"]`. Une référence absente, supprimée, sensible
ou étrangère renvoie la même notice `reference_not_found`. Le texte complet n'est
pas promis dans un compact ; utiliser detailed explicitement, sans plafond 1500.
Un conflit entre blocs d'alias provoque une notice `ambiguous_block` et aucune
injection d'un état choisi arbitrairement.

Le relais ajoute `unresolved_requests` puis rebudgete l'enveloppe finale. Il garde
les détails/identités des appels dans `memory_request_status`, pas dans le brief.
Une panne = `upstream_unavailable`, pas un contexte vide présenté comme réussi.
Un ancien serveur sans contrat compact n'est pas accepté comme compact par défaut.
Un tokenizer absent = `tokenizer_unavailable`, sans repli détaillé ni téléchargement.

## Compteur local exact

Extra `[brief]` : **tiktoken 0.12.0**. Table officielle cl100k_base fournie explicitement
via `MEMORY_V4_CL100K_PATH`, SHA-256 requis :
`223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7`.
Le runtime construit Encoding à partir des octets locaux vérifiés : il n'appelle
pas le chargeur réseau de tiktoken. Pattern cl100k_base provenant de tiktoken MIT,
attribution/licence dans `TIKTOKEN_LICENSE.md`. Version différente ou table manquante/
modifiée = refus. `/ready.capabilities.brief.tokenizer` et doctor exposent cet état.

La table n'est pas embarquée dans ce bundle, ni téléchargée par Codex. L'acquisition
explicite est séparée dans `scripts/provision_cl100k.py`, GO Romain requis.
Le serveur et chaque runtime de relais doivent tous disposer du compteur/table.

**Depuis 4.1.3 (décisions D3 et D4).**
- La table attestée est **embarquée** dans le paquet (`memory_os_v4/resources/cl100k_base.tiktoken`, notice MIT dans
  `THIRD_PARTY_NOTICES.md`). Le service et le relais comptent sans aucune configuration.
- `MEMORY_V4_CL100K_PATH` reste une surcharge explicite : définie, elle seule est utilisée, sans repli silencieux, et
  son SHA-256 doit être identique.
- `tiktoken>=0.12.0,<1` est accepté (versions exactes figées dans les verrous qualifiés). Un auto-test encode six
  vecteurs de référence au chargement ; tout écart donne `tokenizer_unavailable`, jamais un compte faux.
- `/ready.capabilities.brief.tokenizer.table_source` vaut `embedded` ou `explicit`.
Ne pas activer cette candidate avant la preuve exacte et la qualification sur copie.

## Qualification

Jeu amendé et scellé AVANT code : commit cd4ad41, manifeste et original conservés
sous `v4/tests/fixtures/brief_l1a/`. 100 scénarios, 50 FR/50 EN, deux backends/deux
chemins. La suite unitaire utilise un DOUBLE de compteur identifié comme tel et
vérifie structure/validité/isolation/coupures/budgets de mécanique ; ce double ne
qualifie pas les tokens. `scripts/qualify_brief_l1a.py` compte réellement les 400
variantes avec cl100k_base et refuse le succès si ressource/guard/scénario manque.
Le test sans inférence de « non-exécution de directives » concerne le code, pas
le comportement futur du LLM. Aucun claim d'utilité terrain ni de leaderboard.
