# Protocole lexical C4 v1 — préenregistrement prospectif

Décision Codex, 2026-10-09, avant toute nouvelle mesure C4 : **une seule
variante**, un préfixe fixe de **3 éléments BM25**, puis le classement fusionné
C2 sans les éléments déjà présents. Aucun ajustement après observation C4.

## Hypothèse et limites

C1 égale exactement la référence ; C2 gagne +0,026 de rappel mais perd −0,031
de MRR ; C3 remplit les dix places avec BM25 et annule le gain de rappel.
C4 protège trois places (30 % du budget), laissant sept places à la fusion.
Trois est un choix structurel fixé ici, pas le résultat d'une recherche de
paramètres sur les prédictions. Les trois meilleurs **éléments**, avec leurs
égalités départagées par identifiant, gardent leur ordre lexical. Le reste
conserve l'ordre C2. Aucun découpage, regroupement, juge ou BM25 de référence
ne change. Pas d'accès aux annotations dans le moteur. Cela ne garantit pas
le MRR de tous les tours après regroupement ni le rappel : le gate tranche.

## Développement

Seul C4, contre BM25 recalculé dans le même bras : mêmes 100 identifiants,
ordre et sélection de lexical-dev-v1 ; aucun nouvel exemple. Les preuves C1,
C2, C3 et du diagnostic antérieur sont vérifiées par empreinte. MiniLM local
inchangé, CPU, un thread, batch 4, serveur/modèle résidents, magasin neuf par
conversation ; `scoped_literal`, CC 1,5/1/0,5, `lexical_guard_k=3`, pas de
recency, expansion, CE ou requêtes multiples. Top-k 10, contexte 12 000 octets,
chunks 1 024 caractères, candidats 100. Trois recherches chaudes mesurées ;
démarrage/ingestion/recherche/coûts enregistrés séparément.

Gate **prospectif propre à C4**, différent de l'ancien gate C2/C3 : campagne
complète 100/100, zéro erreur, borne basse IC95 apparié du delta rappel > 0
ET delta MRR **moyen** >= 0. IC par composantes connexes de sessions sources,
2 000 bootstrap, seed 20261009 ; IC indisponible = échec. Questions sans
preuve : exclues des métriques de récupération, dénominateur explicitement
rapporté, conservées dans l'exécution et les métriques de réponse. MRR IC95
toujours publié : moyenne >= 0 ne prouve pas une non-infériorité statistique.
Ces IC dev sont descriptifs après plusieurs explorations ; franchir ce gate
ne constitue pas une nouvelle confirmation indépendante de significativité.
Budget bras 3 600 s, question 300 s, campagne avec contrôles 4 500 s.

## Validation S conditionnelle, un seul candidat figé

`prepare-s` refuse le moindre accès au fichier S avant gate dev, contrôles
d'intégrité et écriture de `winner.json` et du verrou unique de confirmation.
Seul C4 et sa configuration exacte sont autorisés. Les 500 questions S sont
exécutées sans sous-échantillonnage ni troncature du haystack, SHA-256 attendu
`08d8dad4be43ee2049a22ff5674eb86725d0ce5ff434cde2627e5e8e7e117894`.
Top-k, capture, référence, contexte et modèle identiques ; une recherche
chaude, trace désactivée pour limiter le disque. Budget 172 800 s (48 h),
7 200 s/question, ingestion 16 Mo/question. Ce plafond prudent couvre les
~115k jetons bruts/question, indexés sous deux représentations ; il ne prédit
pas la durée à partir des petits haystacks oracle. Dépassement = infaisable,
jamais réduction du jeu ou nouvelle variante.

Critère numérique S figé dès ce document : 500/500 sans erreur, borne basse
IC95 delta rappel > 0 ET delta MRR moyen >= 0 ; tous les IC sont publiés.
**Les questions S recoupent l'oracle déjà observé.** Le programme mesure et
publie le recoupement avec les 500 identifiants oracle historiques. Il s'agit
d'une validation sur haystacks étendus, pas d'un test indépendant ni d'une
preuve de rang leaderboard ; aucune soumission. Les proxys de réponse
extractifs EM/F1/containment restent non officiels.

## Contraintes et reprise

Exécutions séquentielles, `nice 15`, aucun réseau IP, modèles en cache et
mode offline. Arrêt si swap > 8,5 Go ou espace libre < 5 Go. Aucun contournement
si les sondes système sont indisponibles. Reprise seulement d'un résultat
complet ou d'un dépassement de budget receiptable et inchangé ; magasin
partiel sans reçu préservé, jamais repris comme succès. Aucun changement de
source, données, modèle, options ou preuves entre préparation et exécution.

## Activation de production (opérateur, après qualification)

Le défaut reste `legacy`, garde 0, sans activation automatique. Serveur
**candidate** redémarré sur copie de qualification avec :

```sh
export MEMORY_V4_BM25_PROFILE=scoped_literal
export MEMORY_V4_FUSION=cc
export MEMORY_V4_USE_BM25=1
export MEMORY_V4_LEXICAL_FIRST=0
export MEMORY_V4_LEXICAL_GUARD_K=3
```

Garde 0 active le profil et la fusion corrigée C2 seuls ; garde 3 active C4.
La reconstruction BM25 conserve données/provenance et embeddings en cache
(JSONL et SQLite). Retour arrière : `BM25_PROFILE=legacy`, `LEXICAL_GUARD_K=0`,
redémarrage ; jamais modifier directement le magasin de production. Les
garanties de rang concernent `memory_search` **à requête unique** ; les
requêtes multiples/hypothèses/facettes sont refusionnées en RRF et n'ont pas
cette garantie ; `memory_get_context` peut aussi diversifier par MMR.
Expansions/CE éventuels restent dans la queue ; le préfixe protégé
est appliqué en dernier. Le profil change les tokens (Unicode, mots littéraux,
sans stemming) : une victoire MiniLM/anglais ne qualifie pas automatiquement
granite ou la charge quotidienne multilingue. Claude doit relire la politique
et tester une copie avant toute bascule de production décidée par Romain.
Ces options appartiennent à cette candidate, pas au paquet PyPI 4.0.1 déjà
publié. Aucune republication ni modification des configs actives par Codex.

## Commandes hors sandbox

Depuis le clone Codex, après import du bundle, `REV` = son tip complet :

```sh
bash scripts/run_lexical_c4.sh prepare-dev "$REV"
bash scripts/run_lexical_c4.sh run-dev "$REV"
bash scripts/run_lexical_c4.sh prepare-s "$REV"
bash scripts/run_lexical_c4.sh run-s "$REV"
```

La troisième commande refuse S si le gate dev échoue. Ne pas remplacer la
variante ni changer le budget après observation pour sauver un résultat.
