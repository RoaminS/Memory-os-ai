# Décisions candidate 4.0.2 — 2026-10-10

## D1 : OUI, C4 proposé par défaut dans le paquet ; activation réservée à Romain

`Config()` : `bm25_profile=scoped_literal`, `lexical_guard_k=3`. CC 1,5/1/0,5,
Granite et les autres options de production ne changent pas. Ce n'est pas une
bascule : aucun serveur, plist, client ou magasin réel modifié.

Raison : le défaut lexical ancien perdait des preuves en filtrant après
troncature des candidats. Le profil littéral calcule son corpus et ses candidats
à l'intérieur du périmètre projet/kind/validité ; C1 retrouve la parité exacte
BM25 dev. C4 préserve les trois premières places BM25 positives et laisse les
sept suivantes à la fusion à top-k 10. Dev : ΔRecall +0,026 IC95 [+0,003;+0,056],
ΔMRR moyen +0,002 (IC [-0,005;+0,008], pas une preuve de non-infériorité).
S : 500 exécutées, zéro erreur ; **479 questions avec preuve** contribuent aux
métriques de récupération, 21 abstentions en sont exclues, 466 clusters contribuent.
Recall .753584 vs .711935 ; Δ+.041649 IC95 [.021709;.060768].
MRR .625976 vs .613837 ; Δ+.012139 IC95 [.008015;.016414].
Critère préenregistré franchi, sans nouvelle exploration de paramètres.

Ce résultat S réutilise des questions observées avec l'oracle : **pas indépendant**.
Bootstrap apparié, 2 000 réplications, composantes preuves communes/source_abs,
IC conditionnel aux distracteurs figés. Aucun score officiel, ni résultat de
qualité de réponse : EM extractif=0, lecteur LLM non évalué. MiniLM CPU anglais,
recency désactivée dans le banc ; gain non attesté sur Granite, français,
requêtes multiples ou `memory_get_context` MMR. Un défaut proposé est une décision
de produit réversible, pas une certification de toutes ces charges.

Effets visibles : lexical = mots Unicode du **texte uniquement**, sans stemming,
headers ni mots-clés ajoutés ; les signaux dense/entités restent dans la fusion.
Les hits protégés reçoivent un score de rang 1/r et `pre_guard_score` en signals,
qui n'est pas une probabilité. Garanties du préfixe : recherche à requête unique
uniquement, pas de promesse d'ordre identique après collapse en tours côté banc.
Valider sur une copie Granite et des questions quotidiennes FR/EN avant GO live.

Retour opérateur explicite, avec redémarrage décidé par Romain :

```sh
MEMORY_V4_BM25_PROFILE=legacy
MEMORY_V4_LEXICAL_GUARD_K=0
```

Le changement de profil ne modifie pas les faits : index BM25 dérivé reconstruit,
embeddings conservés. Une configuration env existante `legacy`, BM25 désactivé
ou `LEXICAL_FIRST=1` désactive la garde **si non explicitement fournie**. Une garde
explicite incompatible refuse avant ouverture du magasin. API Python : fournir
`lexical_guard_k=0` avec ces politiques alternatives (pas d'ajustement silencieux).
Les bancs historiques figent maintenant leur base legacy/0 explicitement ; les
plans/source/report S existants restent immuables. Reproduction du résultat :
checkout source df04fc6, pas exécution d'un vieux plan sur les sources 4.0.2.

## D2 : SQLite S2/S3 inclus, TOUJOURS opt-in

Défaut `store_backend=jsonl`. `MEMORY_V4_STORE_BACKEND=sqlite_s1` active la couche
expérimentale : transaction moteur BEGIN IMMEDIATE, reçu final atomique, cache
embeddings persistant, rétention implicite bornée, explicite permanente,
meta.version=version du paquet, migration hors ligne réversible/source inchangée.
Voir ADR-001, SQLITE_S2 et SQLITE_S3 ; tests de panne sur magasins jetables.
Migration ne se lance pas au démarrage. **S4 backup API SQLite et restore-drill
qualifiés restent à livrer** ; ops backup refuse SQLite. Pas d'activation industrielle
avant ce lot et son GO. JSONL conserve ses limites multi-fichiers.

## D3 : publication distincte du déploiement

Deux artefacts figés, SHA256, installations isolées et relais installé testés
séparément. `release_ready=false` tant que manque une preuve TCP wheel ou sdist.
Aucune publication automatique. Romain décide séparément PyPI, activation C4,
migration SQLite et accès réseau/coûts pour le prochain banc.
