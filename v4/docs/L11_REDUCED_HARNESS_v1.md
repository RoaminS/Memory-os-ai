# L11 réduit — contrat de cas et protocole scellable v1

2026-10-11 · Codex : harness · Claude : cas, grilles, qualification extérieure.
Romain a autorisé 30 paires M/N, 10 par Claude Code/Codex/Grok, et un scorer
Codex gpt-5.6-luna sur le quota ChatGPT. **Aucune session réelle exécutée par Codex.**
Ce document remplace les volumes et strates du protocole L11 v2 pour cette seule
campagne réduite : ni baseline 4.0.3, ni Gemini, ni interfaces GUI, ni 94 handoffs.

## 1. Format à remettre par Claude

Deux JSONL UTF-8 distincts : `cases.jsonl` et `grids.jsonl`. Exactement 30 identifiants
uniques et identiques dans les deux fichiers. Pas de secret, dialogue utilisateur
brut ou information personnelle non nécessaire. Ces fichiers restent privés.

Exemple de ligne **cases.jsonl** :

```json
{"case_id":"L11-001","project":"memory-os-ai-demo","task_type":"resume","starting_agent":"codex","taking_over_agent":"claude","order":"MN","workspace":"/private/tmp/l11-cases/workspaces/L11-001","history":{"facts":[{"text":"Le projet memory-os-ai-demo doit préserver les écritures récentes pendant le retour arrière.","event_time":"2026-10-11","keywords":["rollback","writes","durability"]}],"blocks":[{"name":"project_state","text":"Activation préparée ; validation sur copie terminée ; décision opérateur attendue."}],"tasks":[{"create":{"title":"Préparer le retour arrière","next_action":"Vérifier l'export sémantique sur copie"},"patch":{"status":"blocked","blocked_reason":"GO opérateur requis"}}]},"native_channel_inventory":["HANDOFF.md","README.md"]}
```

Exemple de ligne **grids.jsonl**, jamais remise au repreneur :

```json
{"case_id":"L11-001","objective":"Préparer une activation réversible","decisions":[{"id":"D1","text":"L'activation attend la décision de l'opérateur","critical":true}],"constraints":[{"id":"C1","text":"Préserver toutes les écritures récentes au retour arrière","critical":true}],"next_action":"Vérifier l'export sémantique sur une copie","forbidden_actions":["Activer la production immédiatement"]}
```

Règles vérifiées par `prepare` :

- `taking_over_agent` : `claude`, `codex`, `grok`, **10 cas chacun** ;
  `starting_agent` différent. Au moins trois projets. Le démarrage est une provenance
  déclarée : Claude précise dans le protocole si l'historique est réel ou reconstruit/synthétique.
- Les quatre `task_type` : `resume`, `decision`, `constraint`, `state`, tous représentés.
- Alternance scellée par ordre des lignes : ligne 1 `MN`, ligne 2 `NM`, etc.
  Répartir les clients de façon à obtenir cinq MN et cinq NM par client.
- `case_id` : lettres ASCII/chiffres/`_`/`-`, ≤60 caractères ; projet même alphabet,
  ≤120. Pas de libellé permettant une injection dans le prompt.
- `history.facts` : objets `FactIn` de 4.0.5 ; dates absolues et identités explicites
  si nécessaire. Les IDs fournis doivent être uniques à l'intérieur du cas.
- `history.blocks` : liste `{name,text}`, noms uniques, scope projet uniquement.
- `history.tasks` : `{create,patch?,evidence?}`. `create` contient les champs de
  `TaskCreateIn` sauf `project`, `request_id`, `client_provenance`, injectés par le
  harness. `patch` est un `TaskPatchIn` optionnel, appliqué avec l'ETag obtenu lors
  de la création. Une tâche done exige résolution et preuves conformes au contrat.
  Ce canal ne permet pas de falsifier updated_at pour simuler 14 jours de staleness.
- Historique ≤64 000 octets. Workspace réel, fichiers réguliers UTF-8 uniquement,
  sans symlink ni fichier/dossier caché, ≤128 000 octets/fichier, ≤1 Mo au total.
  HANDOFF et fichiers de suivi **déjà écrits avant le gel**, accessibles dans les
  deux conditions. Aucun `.mcp.json`, `.git`, `.claude`, `.grok` ou `.codex` importé.
- Grille : objective/next_action non vides ; decisions/constraints avec IDs uniques,
  text et booléen critical ; forbidden_actions liste de propositions interdites.
  Les rubriques attendues doivent être accessibles dans les canaux scellés, sans
  ajouter un résumé après observation. Pas d'exigence seulement connue du juge.

Les instructions de sécurité réelles sont transcrites dans les fichiers du cas.
L'opérateur approuve le profil expérimental commun M/N : proposer seulement,
outils lecture seuls, pas de sauvegarde. Les règles quotidiennes ne sont pas effacées
dans les vrais dépôts. **Q0 est désactivé dans les deux bras**, explicitement : ce
pilote mesure la reprise via outils MCP/fichiers et ne qualifie pas le hook de production.

## 2. Conditions, isolation et budgets

Chaque session reçoit exactement : `Take over the work in progress on project X.`
Un même message système de sécurité/format est imposé à tous les clients et bras.
La sortie finale est JSON `{objective,decisions[],constraints[],first_action}`.
`first_action` est une proposition textuelle conservée verbatim, jamais une exécution.

M : serveur HTTP 4.0.5 **installé et attesté contre la wheel qualifiée**, sqlite_s1,
work_state actif, magasin vide privé puis faits/blocs/tâches insérés exclusivement
par l'API. Un port loopback aléatoire, jeton privé 0600, aucune adresse de production.
Profil de production MPS/native/batch32, longueur512, scoped_literal/guard3, poids
locaux hors ligne ; modèle/révision/dtypes/runtime relevés dans memory_status.
Cache de poids partagé en lecture hors ligne ; HOME/cache applicatif/store privés.
Les lectures passent par le **relais 4.0.5 empaqueté**, entouré d'une façade restrictive.

N : **aucun serveur mémoire ni magasin mémoire**, façade de lecture de fichiers
seulement. Copie du même workspace, même système/prompt/format, même modèle/effort
par client. Les grilles, historique mémoire, tokens et journaux ne sont pas dans
le catalogue de fichiers. Aucun résultat d'un autre essai accessible.

Les workspaces exécutés et HOME sont dans `/private/tmp/memory-l11-…`, **hors du
clone/git root** : Grok ne peut pas hériter du `.mcp.json` de production du dépôt.
Les configurations connues dans les ancêtres sont refusées. Les copies appartenant
au harness sont archivées dans le dossier de l'essai après arrêt des processus ;
elles ne sont pas supprimées. Les secrets OAuth temporaires sont retirés avant archivage.

La façade ne publie que brief/search/timeline/block_get/task_list/task_get/context/
status/search_occurrences. Aucune mutation, consolidation ou compaction. Le serveur
privé est écrivain pendant le seed uniquement du point de vue des outils exposés :
le refus de mutation est assuré par la façade, **pas par une variable env modifiée
après démarrage**. Accès projet strict, global refusé. Les lectures du moteur peuvent
actualiser des compteurs dérivés dans le magasin privé ; aucun effet en production.

CLI : Claude `--strict-mcp-config`, settings/hooks désactivés, built-ins vides ;
Codex `--ignore-user-config --ignore-rules`, read-only, fonctionnalités shell/web/
hooks/agents désactivées, table MCP fermée ; Grok HOME/GROK_HOME privés, TOML MCP
privé, built-ins vides, mémoire native désactivée, leader privé, permissions sans
demande, seuls MCP de la façade autorisés. Les appels Grok `use_tool` basés sur un
fichier sont refusés par règles Read et détection ; seul le format inline est admis.

**Limite d'attestation** : HOME privé et allowlist ne prouvent pas l'absence de
politique globale/managed ni de personnalisation distante du fournisseur. La revue
opérateur de ces couches est obligatoire et scellée. `qualify` vérifie le relais,
les catalogues M/N et la découverte Grok sans LLM ; Claude relit `grok-inspect.json`
et les commandes avant `accept-qualification`. Une contamination découverte interdit
l'exécution dans cette strate ; nouveau protocole/plan nécessaire. Les fausses CLI
ne prouvent pas une vraie invocation MCP par chaque CLI : celle-ci est mesurée et
ses échecs restent dans le dénominateur, sans remplacement.

Budgets figés : 60 lancements de repreneur maximum ; ≤600 s après soumission/essai ;
≤10 tentatives de lecture mémoire/essai et ≤10 transmissions effectives ; démarrage
+ seed ≤180 s, mesurés séparément ; transcript stdout+stderr ≤8 Mo. Un refus de
façade est un échec d'intégration. Appels administratifs de seed/qualification comptés
séparément, hors budget de 10. Une nouvelle connexion à une façade déjà journalisée
est refusée : pas de remise à zéro du compteur.

Scorer : **Codex gpt-5.6-luna, effort low**, ChatGPT uniquement, aucun outil, message
figé dans le plan. Maximum60 lancements, ≤180 s chacun, un seul scoring par session.
Un échec d'intégration reçoit un échec déterministe sans appel de juge inutile.
Les IDs/condition/client sont retirés du payload du scorer ; la forme du texte
peut néanmoins révéler le canal. Pas de juge API ni conversion OAuth→API.

Ces plafonds portent sur les **lancements CLI**, pas sur le nombre de tours du modèle
interne. Tokens/usage réellement exposés conservés par fournisseur, caches inclus
selon leurs sémantiques respectives. Quota restant/pourcentage inconnu ; 0$ API
incrémental imposé par auth abonnement et suppression des fallbacks API, pas une
affirmation d'absence de coût d'abonnement. Cap théorique séquentiel ≈14,5 h incluant
seed/qualification/scoring ; commencer par de petits lots, jamais un rattrapage automatique.

nice15, concurrence1, swap≤8,5 Go, disque≥5 Go, mesure continue. Aucun téléchargement
de poids/tokenizer. Tous les échecs/quotas/timeouts restent enregistrés. Les tentatives
sont persistées/fsync avant lancement. Une tentative pending sans completed bloque
toute reprise : jamais rejouée par `--resume`. Après un échec terminal, `--resume`
manuel passe à l'essai **suivant**, sans modifier le plan ou remplacer le précédent.
Un verrou empêche deux opérateurs de lancer le même plan simultanément.

## 3. Authentification et spécifications client

Claude remet `clients.json` : objet de trois clés claude/codex/grok. Pour chaque clé :

```json
{"path":"/chemin/absolu/vers/cli","model":"identifiant-explicite-disponible","effort":"low","auth_home":"/chemin/prive/profil-auth","isolation_review":{"subscription_only":true,"managed_hooks_absent":true,"native_memory_disabled":true,"external_connectors_absent":true}}
```

**Modèles repreneurs à fixer par Claude avant prepare**, sans alias changeant si un
ID explicite existe ; aucune substitution automatique. Binaire et `--version` scellés.
Le scorer reste luna/low indépendamment du modèle repreneur Codex.

`auth_home` contient seulement le fichier d'auth abonnement nécessaire :
`.claude/.credentials.json` (claudeAiOauth), `.codex/auth.json` (tokens ChatGPT),
`.grok/auth.json` (connexion abonnement). Mode0600, jamais symlink. Le harness copie
uniquement ce fichier dans un HOME neuf, pas de configs/historique/mémoires natives.
Il supprime cette copie après l'essai, y compris après erreur ; les secrets ne sont
jamais imprimés, inclus dans les hash de preuve ou transmis au scorer. Pas de clé API.
L'auth peut être renouvelée par l'opérateur sans changer modèle/plan ; jamais reconnectée
automatiquement par le harness. Un kill externe peut laisser la copie privée : la
retirer après résolution de l'incident, conserver les journaux de coût/essai.

Si le login Claude sur ce Mac dépend uniquement du trousseau et ne peut fonctionner
avec le fichier OAuth privé, **refuser avant les sessions**, ne pas réactiver le HOME
réel ou `--bare` (ce dernier exige API/apiKeyHelper selon l'aide locale).

## 4. Commandes exactes pour Claude

Depuis le clone, après import du bundle nommé et rédaction des cas/grilles/specs :

```sh
PY=/Users/romain/.memory-os-runtime/venv-4.0.5/bin/python
PLAN="$PWD/.local-qualification/l11-reduced-claude-v1"
INPUT=/private/tmp/l11-cases
PYTHONPATH='' PYTHONDONTWRITEBYTECODE=1 "$PY" -B scripts/run_l11_reduced.py prepare \
  --cases "$INPUT/cases.jsonl" --grids "$INPUT/grids.jsonl" \
  --clients "$INPUT/clients.json" --protocol v4/docs/L11_REDUCED_HARNESS_v1.md \
  --output "$PLAN" --runtime-python "$PY" \
  --release-manifest "$PWD/.local-qualification/release-4.0.5-v5/manifest.json" \
  --production-plist "$HOME/Library/LaunchAgents/com.romain.memory-os-v4.plist" \
  --cl100k "$HOME/.config/memory-os/cl100k_base.tiktoken" \
  --approval 'Romain 2026-10-11 : L11 réduit, 30 paires M/N, Claude Code/Codex/Grok ; scorer Codex luna via quota ChatGPT'
PYTHONPATH='' PYTHONDONTWRITEBYTECODE=1 nice -n 15 "$PY" -B \
  "$PLAN/tooling/run_l11_reduced.py" qualify --plan-dir "$PLAN"
```

**Lire qualification.json, les commandes générées et les six sous-dossiers qualification/**,
ainsi que la découverte Grok. Vérifier que les entrées MCP effectives appartiennent
uniquement à la façade privée et qu'aucun hook/connector global n'agit. Si cette revue
est verte et le profil expérimental/cas accepté par Claude :

```sh
PYTHONPATH='' "$PY" -B "$PLAN/tooling/run_l11_reduced.py" accept-qualification \
  --plan-dir "$PLAN" --review 'Claude : catalogues, commandes, auth privée et couches globales vérifiés ; aucune production accessible'
PYTHONPATH='' nice -n 15 "$PY" -B "$PLAN/tooling/run_l11_reduced.py" run \
  --plan-dir "$PLAN" --batch-calls 2
```

Puis lots manuels après inspection des rapports et quotas, jamais une boucle :

```sh
PYTHONPATH='' nice -n 15 "$PY" -B "$PLAN/tooling/run_l11_reduced.py" run \
  --plan-dir "$PLAN" --batch-calls 4 --resume
PYTHONPATH='' nice -n 15 "$PY" -B "$PLAN/tooling/run_l11_reduced.py" score \
  --plan-dir "$PLAN" --batch-calls 4
PYTHONPATH='' nice -n 15 "$PY" -B "$PLAN/tooling/run_l11_reduced.py" score \
  --plan-dir "$PLAN" --batch-calls 4 --resume
PYTHONPATH='' "$PY" -B "$PLAN/tooling/run_l11_reduced.py" report --plan-dir "$PLAN"
```

Une erreur de qualification se conserve dans ce plan ; **nouveau plan-dir** après
correction et nouvel amendement si nécessaire. Aucun essai repreneur n'a alors été
lancé. Une erreur terminale repreneur/juge est conservée et reste dans les 60 allocations.
Un état pending exige réconciliation humaine de la consommation avant toute suite ;
le harness ne fournit aucun bouton de rejeu.

## 5. Rapport, contrôle humain et preuves attendues

Par client/condition : allocations10, sessions terminées, erreurs d'intégration,
scores acceptés, succès, temps individuels et octets MCP sérialisés effectivement
servis. `served_bytes` mesure l'enveloppe JSON MCP, **pas** les octets TCP/SSE ou les
descripteurs de catalogue. Tokens servis `unknown` ; aucune conversion octets/4.
Première action verbatim, transcript et outils journalisés ; temps de réponse terminée
et intervalle d'observation de la première proposition JSON conservés séparément
(`cli.arrivals.json`). La justesse est déterminée ensuite par la grille : pas de
temps artificiel de première action correcte. Les horodatages permettent une
annotation humaine complémentaire.

Succès = objectif + toutes décisions critiques + toutes contraintes critiques +
prochaine action conformes et aucune violation/proposition interdite. Omission ≠
exécution, distinguée dans le prompt du scorer et dans l'arbitrage humain. Succès M
≥27/30 est un seuil formatif, pas une preuve causale, de supériorité, ou de leaderboard.
Pas d'IC imposé pour ce petit pilote ; M−N et différences entre agents sont descriptifs.

`report.json` tire **12/60 sessions**, seed20261011, deux par client/condition,
indépendamment des résultats, plus toutes contraintes critiques non récupérées,
violations et erreurs de scoring. Romain contrôle cette liste et tout cas disputé.
Conserver les scores initiaux ; arbitrage à côté, avec justification, sans écrasement.
Le rapport indique toujours arbitrage humain pending ; aucune validation finale
automatique des observations par le seul juge.

Attendus de Claude : revue du format et des adaptations au protocole v2 ; rédaction
des30cas/grilles/specs ; revue d'isolation ; qualification privée réelle (sans LLM),
puis lots CLI approuvés et suivi quota ; échantillon humain ; état des clients réels
avant/après si nécessaire. **Aucune activation/publication/modification de production.**

Références d'isolation : aide locale des trois CLI et guides locaux Grok
`07-mcp-servers.md`, `14-headless-mode.md`, `26-config-reference.md` ; Codex
[référence officielle des commandes](https://developers.openai.com/codex/cli/reference/).
Les versions distantes des modèles et la disponibilité des quotas ne sont pas attestées
par ces pages ni par un simple `--version`.
