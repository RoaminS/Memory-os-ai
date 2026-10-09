# Amendement S v2 — copies de sessions datées, 2026-10-09

Préenregistré par Codex avant toute mesure S. C4 dev a franchi son gate :
Δrappel +0,026 IC95 [+0,003 ; +0,056], ΔMRR moyen +0,002 (IC traversant zéro).
La première préparation S a échoué sur des identifiants de sessions répétés ;
aucune campagne S ni prédiction S n'existe. Le candidat C4, son modèle MiniLM,
sa configuration résolue, ses poids, sa garde lexicale de trois éléments,
ses budgets de récupération et ses critères numériques restent figés.

## Une seule politique : conserver toutes les copies

Conserver les **500 questions**. Un identifiant de session répété est accepté
uniquement si toutes ses occurrences ont exactement la même séquence de
rôles/contenus observables ; les annotations `has_answer` sont exclues de
cette comparaison. Un contenu divergent ou un désalignement des longueurs
reste une erreur bloquante. Les dates de chaque occurrence sont conservées,
avec la même exclusion des observations postérieures à la question.

Chaque copie reçoit un identifiant physique déterministe construit uniquement
avec son identifiant source, sa date et sa position observable. Les tours et
citations sont distincts, donc aucune collision d'identité ni fusion accidentelle
pendant l'ingestion. Les sessions non dupliquées gardent exactement leurs
identifiants et leur comportement précédents. La mémoire et BM25 reçoivent
strictement les mêmes observations et citations, avec les mêmes limites.

Pour le score seulement, les tours homologues des copies appartiennent à une
même classe de preuve `(identifiant source, numéro de tour)` : une copie
retrouvée suffit ; le rappel et son dénominateur ne doublent pas. Les places
consommées par plusieurs copies **ne sont pas rendues** au système. Le MRR
garde le rang du premier tour physique pertinent, après le regroupement des
chunks déjà établi ; nDCG compte chaque classe pertinente une seule fois à
son premier rang. Les aliases sont réservés au score et ne sont jamais
transmis au client, au moteur ou à la réponse extractive. Le clustering
bootstrap garde les identifiants de sessions **sources**, pas les copies,
pour ne pas créer artificiellement des observations indépendantes.

Choix : conserver les quinze questions difficiles sans effacer une date et
sans récompenser deux fois la même preuve. Cette adaptation est publiée et
non officielle ; les questions S recoupent toujours l'oracle observé. Les IC
du protocole sont groupés par sessions sources, pas le bootstrap par question
cité par Claude. Une seule composante source rend l'IC indisponible et le gate
échoue. Aucun rang leaderboard ni test indépendant n'est revendiqué.

## Reprise de la réservation, pas changement de candidat

La réservation v1 et `winner.json` restent intacts. Le préparateur v2 exige
que v1 ne contienne que ce fichier, sans plan, magasin ni prédictions, et
vérifie le verrou, les empreintes des preuves dev et leur source historique.
Les sources moteur/capture/BM25/paramètres doivent être identiques au candidat
qualifié. Seuls le lecteur, l'équivalence du score et le nouveau lanceur
changent. Les 200 prédictions dev sont rescorrées sans modèle ; leurs scores
doivent rester exactement identiques et leurs données n'avoir aucun alias.
La nouvelle réservation v2 est exclusive, et le gagnant est copié **octet pour
octet** avant toute relecture S. L'audit publie les identifiants des questions
dupliquées, les dates/occurrences et l'empreinte du contenu observable.

Le plan v2 fige le SHA officiel S
`08d8dad4be43ee2049a22ff5674eb86725d0ce5ff434cde2627e5e8e7e117894`,
les 500 identifiants, la politique, le nouvel état source, les preuves dev,
l'audit, le gagnant original, le chevauchement oracle, l'estimation et leurs
empreintes. Toute modification invalide le lancement. Aucune nouvelle
variante ni relance silencieuse d'une campagne partielle.

## Ressources et durée

Le préparateur calcule une estimation **sans modèle**, par streaming : taille
de la conversation maximale, nombres de tours/citations/chars indexés,
mémoire minimale des vecteurs (`citations + épisodes` × 384 × 4 octets,
séparée des index/Python/runtime) et extrapolation de temps depuis le journal
d'ingestion C4 dev. Cela ne constitue pas une mesure S ; l'extrapolation ignore
le ralentissement des index/déduplication aux grands haystacks et publie une
fourchette prudente 1–4 fois le temps extrapolé. Le budget reste **48 h**,
2 h/question, une seule conversation résidente, CPU un thread, batch 4,
16 Mo d'ingestion/question, top-k 10, contexte 12 000 octets, une recherche
chaude, sans trace de candidats. Aucune réduction du haystack si infaisable.

Estimation nominale déjà calculable sur les seules traces dev : **107,34 s**
d'ingestion pour **5 181 789 caractères** indexés. À 115k jetons bruts par
question S et l'approximation caractères/4 (pas un tokenizer), deux
représentations donnent ~460 millions de caractères : **2,65 h** linéaires,
fourchette **2,65–10,59 h**, hors effets non linéaires et surcoûts. Le
préparateur remplace cette approximation par le volume observable réel S
et publie également la conversation maximale et le minimum mémoire vectoriel.

Enveloppe de planification RAM : **2–6 Go pour le processus de campagne**,
à confirmer par mesure réelle, pas une garantie ni une limite RSS imposée.
Les index et objets Python dominent souvent les quelques Mo de vecteurs ;
pas de conservation des 500 magasins. Un seul magasin neuf à la fois et un
seul modèle résident. Arrêt physique swap >8,5 Go/disque libre <5 Go,
`nice 15`, offline, aucun téléchargement. Préparation et exécution gardées ;
si la sandbox refuse les sondes ou nice, Claude doit les lancer hors sandbox.

```sh
bash scripts/run_lexical_s_v2.sh prepare "$REV"
bash scripts/run_lexical_s_v2.sh run "$REV"
```

Le dossier cible par défaut est `.local-qualification/lexical-c4-s-v2` ; ne
supprimer aucun verrou ni le dossier v1 interrompu.
