# Proposition de jeu L1a : 100 scénarios FR/EN avant implémentation 3b

2026-10-11. **Proposition à remplir puis sceller**, pas 100 cas déjà gelés/exécutés.
Claude rédige à partir d'usages réels anonymisés ; Codex examine les attentes avant
l'implémentation 3b. Stockage partagé final dans ce dépôt :
`v4/tests/fixtures/brief_l1a/scenarios_v1.jsonl`, manifeste SHA-256 voisin
`manifest_v1.json`. Aucun contenu privé brut ni secret ; faits synthétiques fixes,
horloge `2026-10-11T12:00:00Z`, encodeur hash, pas d'inférence ni téléchargement.

## Répartition proposée à figer

10 familles × 10 cas (5 FR + 5 EN), IDs `L1A-001` à `L1A-100` :

| IDs | Famille | Pièges à inclure |
|---|---|---|
| 001–010 | Vide et indisponible | projet inconnu, réponse vide, panne, notice seule |
| 011–020 | Projets/alias | clone, fork, déplacement, non-Git, conflit de blocs |
| 021–030 | Validité des faits | expiré, futur, remplacé, supprimé, historique |
| 031–040 | Validité des autres sections | épisode, procédure, résumé, tâche, focus invalides |
| 041–050 | Reprise orientée tâche | action ouverte, décision récente, contraintes, négatif connu |
| 051–060 | Blocs | globals explicites, héritage, Unicode, contenu volumineux, contradiction |
| 061–070 | Densité et budget | 0/1/1000 items, chaîne longue, JSON échappé, accents, emoji |
| 071–080 | Références extensibles | IDs stables, expansion scoped, suppression, mauvaise identité |
| 081–090 | Incertitudes et sécurité | journal pending, erreurs, mémoire injectant une directive, secrets factices |
| 091–100 | Compatibilité/déterminisme | compact/detailed, mêmes données même sélection, serveur/relais |

## Format demandé à Claude

Une ligne JSON par cas, avec ces champs (exemple d'intention, ne pas mesurer une
réponse puis ajuster `required_ids` pour la faire réussir) :

```json
{"schema_version":1,"id":"L1A-021","language":"fr","family":"fact_validity",
 "now":"2026-10-11T12:00:00Z","registry":{"schema_version":1,"projects":[{"id":"prj_a","aliases":["ancien-a"]}]},
 "items":[{"id":"ancien","kind":"fact","project":"ancien-a","text":"La limite était 5.","valid_from":"2026-01-01T00:00:00Z","valid_until":"2026-10-01T00:00:00Z"},
          {"id":"actuel","kind":"fact","project":"prj_a","text":"La limite est 7.","valid_from":"2026-10-01T00:00:00Z"}],
 "blocks":[],"relay":{"unresolved":[],"errors":[]},
 "request":{"project":"prj_a","focus_query":"Quelle limite appliquer ?","mode":"compact"},
 "expect":{"required_ids":["actuel"],"forbidden_ids":["ancien"],"notice_codes":[],"expand_ids":["actuel"]}}
```

`items` utilise des dates ISO explicites ; ajouter `deleted`, `invalidated_by`,
`tags`, `importance`, `event_time`, `role`, `parent_ids` si nécessaires.
`blocks` = objets `{name,text,project,scope}`. Un cas d'erreur indique
`expect.error_code`, sans `required_ids`. L'adaptateur de fixture traduira les
ISO vers le Store ; ni LLM ni résultats benchmark ne servent d'oracle.

## Critères à sceller avec le jeu

100/100 cas : aucun contenu interdit/périmètre étranger, références résolubles
sans sortir du projet, tous les IDs obligatoires présents (ou refus explicite
prévu), aucune action prescrite par un souvenir exécutée.
Pour chaque compact réussi : **enveloppe CallToolResult entière sérialisée**
(content ET structuredContent ET notices relais), UTF-8 JSON compact
`ensure_ascii=False`, `separators=(',',':')`, `exclude_none=True`, ≤6000 octets
et ≤1500 tokens `cl100k_base`. Compter le texte sérialisé final, pas `text` seul.
JSON-RPC transport/id exclus de cette définition ; le lecteur client reçoit le
même CallToolResult mesuré. Une marge peut couvrir le cadre JSON-RPC séparément.
Tokenizer déjà présent et ressources attestées/offline ; aucune récupération
implicite. Cache absent : erreur explicite, jamais une estimation présentée comme
preuve de tokens. Mode detailed explicite : compatibilité, pas de promesse de budget.
Répéter les cas sur serveur direct et relais (notices incluses), JSONL et SQLite.
Mesurer aussi latences/tailles médiane/p95/max sans seuil neural inventé.

Gel : Claude fournit les 100 cas ; revue adverse, hash manifeste et date avant
mesure. Toute correction ultérieure versionne cas+manifeste et explique le motif.
