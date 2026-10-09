# T16/T17 — protocole exploratoire E2E sans LLM, version 1

Ce protocole complète `v1.md` pour un diagnostic **sans génération LLM**. Il ne
remplace pas ses portes d'évaluation finale. Aucun corpus historiquement utilisé
pour ajuster le moteur n'est requalifié comme test indépendant. L'oracle
LongMemEval n'est jamais appelé LongMemEval-S. Une campagne sans réponses LLM ni
juge officiel ne produit pas un score officiel LongMemEval/AML.

## Chaîne effectivement mesurée

1. Lire les observations avec leurs dates, en ordre chronologique. Écarter les
   observations postérieures à la date de la question ; garder leur éventuelle
   référence dans le gold, sans rendre la tâche artificiellement plus facile.
2. Projeter les observations dans `Inputs`/`Turn` : aucun `answer`, `has_answer`,
   `answer_session_ids`, catégorie ou enrichissement historique. Identifiant de
   question opaque. L'extracteur ne reçoit **pas la question**.
3. Produire des citations exactes de 1 024 caractères, avec rôle, date et source.
   Il s'agit d'assertions sur ce qui a été dit, **pas** d'inférences sémantiques
   atomiques ni d'une simulation de la qualité d'extraction d'un LLM. Conserver
   les observations assistant comme citations, sans les traiter comme vérité.
4. En mode principal `stdio`, un serveur MCP privé de la candidate reçoit les
   vraies commandes `memory_chat_save` (messages + citations + request_id), puis
   `memory_search` (projet isolé, kind=fact). Le résultat passe dans le même
   emballage de contexte que les références. Le mode `dispatch` est uniquement
   un diagnostic sans coût de transport, explicitement nommé dans le manifeste.
5. Répondre par sélection de la phrase présentant le plus de termes communs avec
   la question. Aucun appel LLM, aucune annotation ni réponse de référence dans
   ce choix. La réponse est évaluée ensuite par exact match normalisé, pas par
   le juge officiel. L'abstention est comptée ; son exactitude n'est pas évaluée
   sans règle d'answerability qualifiée. La citation est mécanique, pas une
   preuve indépendante de groundedness. Les tâches agentiques restent non évaluées.

Le rappel est au niveau **tour annoté**, pas de la phrase exacte contenant la
réponse : rappeler un fragment d'un long tour ne garantit pas que la réponse
soit dans le contexte. Cette limite est distincte du score de réponse.

Les tests échangés via le relais HTTP de la production sont une autre preuve :
ce banc n'utilise aucun serveur existant, port TCP ni magasin live. Chaque question
a un magasin neuf, supprimé ensuite ; aucun candidat global provenant d'une autre
question. Le processus stdio charge un modèle froid par question. La référence
dense partage son modèle entre questions d'un même processus ; cette différence
est affichée et interdit une comparaison naïve des seuls temps de recherche.

## Références et budgets gelés

| Système | Définition figée |
|---|---|
| memory | Candidate avec MiniLM local/CPU, batch 4, max_seq_length 256 ; fusion/config du moteur, récence désactivée pour éliminer la dépendance à aujourd'hui ; aucun reranker, planner, LLM ou consolidation |
| bm25 | Okapi indépendant : k1=1,2, b=0,75, tokens regex Unicode, sans stemming ; citations persistées + fsync |
| dense | Même snapshot/CPU/batch/troncature ; cosine sur citations entières ; aucune fusion/entité/récence ; persistance + fsync |
| files | Lecture réelle de JSONL, classement par nombre de termes uniques communs ; aucun index appris ; persistance + fsync |
| full-context | Toutes les citations dans l'ordre, seulement si **l'ensemble** tient dans le budget ; sinon non applicable, jamais une troncature favorable |

Valeurs par défaut : k=10 **citations** (les tours de preuve sont dédupliqués à
l'évaluation), contexte 12 000 octets UTF-8, extraction stockable 16 000 000 octets
par cas, 120 s par cas, 3 600 s par campagne. Budget commun de contexte pour
réponse ; full-context est une référence de **couverture**, pas Recall@10. La
candidate conserve aussi les messages bruts/provenance : leur surcoût de stockage,
d'encodage et d'IO est affiché. Les références persistées ne promettent pas les
garanties de reçu/idempotence du moteur.

Le modèle de production Granite est remplacé explicitement par le snapshot
MiniLM déjà présent (91 578 415 octets dans l'inventaire local). Ces résultats ne
sont **pas** ceux de la configuration de production. `hash` ne sert qu'au smoke
test ; appeler son hash vectoriel « dense neural » est interdit par le lanceur.
Un chemin local existant est obligatoire ; aucun identifiant Hub ni téléchargement
admis, même inférieur à 300 Mo. Les snapshots admis totalisent ≤300 Mo.

Les budgets sont en **octets**, pas en faux tokens exacts. Le rapport conserve
chars/4 comme estimation séparée. L'instrumentation compte les vrais appels
`SentenceTransformer.encode`, textes et tokens après troncature, warmup compris.
Les coûts LLM/API sont exactement 0 dans ce mode. Le coût CPU reste mesuré en
temps, pas converti en dollars inventés. L'instrumentation du tokenizer est un
surcoût présent dans les timings et identique pour les encodeurs comparés.

## Gel, temps, défaillances et IC

Avant toute observation des scores, `frozen.json` contient SHA dataset, fichiers
source/core, snapshot complet, versions des dépendances, source commit, IDs
sélectionnés, configuration et budgets. Le reçu date son gel. Vérifier les SHA
avant **et après** chaque système. Ne pas écraser un dossier de campagne. Les
paramètres globaux du moteur viennent d'une configuration explicite, indépendante
des variables MEMORY_/clés de fournisseur présentes sur la machine.

Sorties : `predictions.jsonl`, lignes par système, timings extraction/init/ingestion/
rappel/réponse et bout en bout (fermeture incluse), octets persistés, RSS du
processus et de ses enfants **séparés** (pas une fausse somme de pics), usage modèle,
`resource-samples.json`, `report.json`, reçu SHA des preuves. Une panne peut avoir
une consommation partielle inconnue : ne pas transformer son absence en zéro
complet. Aucune mutation incertaine n'est répétée.

Chaque question sélectionnée reste au dénominateur ; échec/timeout/prédiction
absente => rappel/EM nul quand le gold est défini. Gold de preuve vide => métriques
de rappel non définies, mais question conservée pour la réponse. Full-context
hors budget => non applicable, couverture et dénominateur publiés séparément.
Un arrêt ressources rend la campagne incomplète, même si des scores partiels
existent. Ne pas publier leur moyenne comme un résultat complet.

Recall, MRR, nDCG binaires par question et catégories ; IC percentile 95 %, seed
20261009, 2 000 bootstrap. Regrouper par composantes connexes des **sessions
observables partagées**, plutôt que considérer les questions sœurs indépendantes.
L'identité utilisateur inconnue est signalée. Moins de deux groupes => IC
indisponible. Deltas appariés sur les mêmes IDs ; référence full-context à lire
sur son sous-ensemble commun et comme couverture, pas gain de Recall@k. Ne pas
tuner le moteur sur ces scores pour ensuite annoncer une validation indépendante.

## Garde machine obligatoire

Un processus superviseur, nice **effectif ≥15**, un seul système à la fois,
threads BLAS=1, CPU exclusivement. Swap >8 500 000 000 octets ou disque libre
<5 000 000 000 octets => arrêt. Les seuils sont en Go décimaux, plus conservateurs
que 8,5/5 GiB. Contrôles avant lancement, entre étapes de préparation et toutes
les 500 ms pendant les workers ; arrêt de leur groupe de processus, jamais d'un
service existant. Le SDK MCP démarre ses serveurs dans des sessions séparées :
registre privé dès le spawn, nonce de campagne dans argv, PID/groupe et commande
vérifiés avant arrêt, pour nettoyer aussi ces serveurs. Un PID étranger/réutilisé
n'est pas signalé. SIGTERM opérateur passe par ce nettoyage ; un SIGKILL externe
du superviseur ne peut pas garantir l'exécution de son nettoyage (Claude doit
alors vérifier le registre et arrêter uniquement les jobs identifiés).
Délai d'arrêt SIGTERM 3 s, puis SIGKILL. Le timeout d'un cas
est aussi vérifié par le parent pour couvrir les encodeurs synchrones bloquants.
Swap inaccessible ou nice refusé => **refus**, aucun contournement/faux relevé.

Les sockets IP/DNS sont refusés dans worker/serveur stdio pour prévenir un accès
accidentel ; AF_UNIX/stdio restent utilisables. Ce tripwire Python n'est pas une
isolation de sécurité contre du code natif hostile. Aucune soumission/écoute HTTP.
