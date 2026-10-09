# Amendement S v3 — unité du bootstrap, 2026-10-09

Préenregistrement avant toute exécution du modèle sur S. La préparation v2
est complète mais aucune prédiction S n'existe. Les 500 questions ont une
seule composante si tout distracteur partagé crée un lien ; cette définition
rend l'IC impossible par construction. L'audit de Claude compte 495 composantes
par sessions de preuve (maximum deux questions), avant lien explicite `_abs`.
Il s'agit d'observations sur la structure des annotations, pas de résultats S.

## Unité S et portée de l'inférence

**Une seule politique S** : composantes connexes des questions liées par un
identifiant source figurant dans `answer_session_ids`, plus chaque question
dont l'identifiant finit par `_abs` liée à sa question source (suffixe retiré)
si celle-ci est présente. Les copies datées gardent leur identifiant source
pour ces liens. Les questions sans preuve restent dans la campagne et les
métriques de réponse ; elles ne contribuent pas à rappel/MRR, comme auparavant.
Les annotations de preuve et les liens sont réservés au superviseur/scorer,
jamais transmis à l'extraction, au moteur, au classement ou au modèle.

Le bootstrap apparié tire des composantes avec remise, conserve ensemble les
questions d'une composante, et calcule la moyenne **pondérée par questions**
des deltas mémoire−BM25. IC percentile 95 %, 2 000 réplications, seed 20261009,
inchangés. Pas de bootstrap distinct des deux systèmes. Les distracteurs
communs ne fusionnent pas les groupes : le corpus de distracteurs est traité
comme **fixe**, avec un magasin isolé par question et un candidat déterministe.
Cette définition estime la variabilité entre tâches/preuves sur ce corpus.

L'isolation des magasins ne prouve PAS l'absence de dépendance due aux
distracteurs communs. L'IC est donc **conditionnel à ce corpus et à cette
hypothèse de regroupement**, et non une garantie de généralisation à de nouveaux
utilisateurs ou distracteurs. S recoupe l'oracle observé ; aucun test indépendant,
score officiel ni rang leaderboard n'est revendiqué. Le regroupement par tout
haystack est conservé et publié comme audit de dépendance, pas comme IC alternatif
permettant de choisir le résultat favorable.

## Seuil et échec

Seuil prospectif S : **30 composantes contributrices** pour chacun des deltas
primaires rappel/MRR. Choix opérationnel conservateur par rapport au minimum
technique de deux, sans prétendre que 30 garantit une validité asymptotique.
Le superviseur recompte les groupes sur S avant tout chargement du modèle :
moins de 30 => arrêt immédiat, pas de campagne coûteuse. Les sous-catégories
de moins de 30 groupes gardent leur moyenne descriptive et un IC indisponible.

Si un IC primaire reste indisponible après exécution (erreur, arrêt, trop peu
de groupes contributifs), le critère numérique échoue et le rapport explique
la cause. Aucun fallback par question, aucune relaxation du seuil, aucun
changement de candidat, de données ou de critère après observation.
Le critère reste : 500/500 sans erreur, borne basse IC95 Δrappel >0 et
ΔMRR moyen >=0. L'IC MRR est publié ; moyenne positive n'est pas une preuve
statistique de non-infériorité.

## Dev inchangé et gel v3

Le défaut du banc reste le regroupement par **tout haystack** avec minimum
deux ; les options S v3 sont refusées pour le variant oracle. Le lecteur ajoute
les sessions de preuve à `Gold` seulement ; les `Inputs`, les scores unitaires,
les groupes dev et les tirages bootstrap dev restent inchangés. Le contrôleur
recalcule sans modèle les 200 scores et **l'intégralité des métriques/IC dev**
contre le rapport existant et les sources archivées. Toute divergence bloque.

Le plan v3 est régénéré depuis les petits artefacts v2 déjà qualifiés, sans
modèle et sans rescanner S : la règle déterministe, le fichier S et son SHA,
ses 500 identifiants, le gagnant original, les paramètres et le critère sont
déjà figés. Le nouveau plan fige la politique S, le seuil 30 et les nouvelles
empreintes source. Le regroupement réel sera recalculé et publié dans
`bootstrap-preflight.json` **avant** démarrage du worker ; la règle et les
annotations sont liées par l'empreinte officielle S. La valeur v2
`source_clusters=1` reste dans l'audit, pas comme nombre de groupes S v3.

Les plans/verrous v1/v2 sont conservés. v3 exige l'absence de campagne, magasin
ou prédiction v2, réserve une seule fois v3 et conserve le gagnant octet pour
octet. Seul le banc d'inférence change ; sources moteur, références, capture,
modèle, garde lexicale, poids et budgets sont vérifiés identiques à C4.
La régénération est une opération légère sur des métadonnées existantes ;
le lanceur de campagne conserve `nice 15`, les sondes swap/disque, l'exécution
séquentielle et les limites 8,5 Go/5 Go, 48 h et 2 h/question.

```sh
bash scripts/run_lexical_s_v3.sh prepare "$REV"
bash scripts/run_lexical_s_v3.sh run "$REV"
```

Depuis le clone Codex ; v3 cible `.local-qualification/lexical-c4-s-v3`.
Si v3 est déjà livré figé, exécuter seulement `run`. Aucun effacement de verrou.
