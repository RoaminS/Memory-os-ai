# Identités explicites et récence — conception préenregistrée, 2026-10-10

Design uniquement, aucune activation et aucune dépense. BEAM100K et LME-S sont
observés ; toutes leurs analyses sont in-sample. Aucune hypersimilarité/cosinus
n'autorise un remplacement, aucune consolidation/compact n'est appelée.

## Données et indépendance

Un second held-out, disjoint du test E2E et de tous les jeux observés, fourni par
un dépositaire : ≥50 conversations, ≥200 questions de mise à jour, ≥100 questions
historiques, ≥100 autres/questions sans réponse. Les conversations comportent
dates explicites, états révisés, retours à une valeur ancienne, sujets homonymes,
valeurs multiples et énoncés contradictoires non résolus. Les questions/gold ne
sont pas visibles au concepteur de l'extracteur. SHA, ids et groupes dépendants
sont gelés avant inférence. Bootstrap ≥30 composantes de conversation/preuve ;
les conversations jumelles restent dans une composante. Effectif insuffisant ou
dates non fiables = STOP avant modèle, pas d'exclusions après score.

## Extracteur commun, question-aveugle

Modèle choisi après pilote externe : même lecteur économique que le plan E2E,
snapshot/prompt/effort gelés. Extraction incrémentale par fenêtres de 4 000 tokens
observables, dans l'ordre des messages ; pas de transcript futur, question,
rubrique ni réponse. Sortie structurée : phrase atomique, sources {tour, span},
event_time/valid_from explicites, identité {subject_id,predicate,cardinality},
éventuels ids `replaces`. Une date relative n'est ancrée qu'à la date observable
du message. Les identités ne sont pas des noms seuls : namespace utilisateur +
entité stable opaque ; co-référence incertaine → nouvelle entité et aucun remplacement.

Une cardinalité single n'est admise que pour une propriété explicitement unique
dans le texte/politique gelée (ex. ville de résidence principale) ; cardinalité
multi ailleurs. `replaces` cible uniquement un fait antérieur de la même entité/
portée, cité dans la sortie. Validation déterministe de sources, ids, chronologie
et schéma, rejet compté. L'extracteur n'est pas autorisé à supprimer des citations.
Citation brute conservée avec provenance ; faits sémantiques séparés.

Les sorties de l'extracteur sont mises en cache et partagées entre les bras, pas
ré-extraites avec des prompts distincts. Cela isole les politiques du moteur ;
l'effet de qualité de l'extracteur lui-même demandera un autre test.

## Plan factoriel prospectif

Quatre magasins privés sur les mêmes facts/vecteurs, C4 et budget de contexte
figés : T00 récenceOFF/updatesOFF ; T10 récenceON/updatesOFF ; T01 récenceOFF/
updatesON ; T11 récenceON/updatesON. UpdatesOFF retire identité et `replaces`
uniquement au moteur, et conserve toutes assertions ; dates/textes/sources restent
identiques. UpdatesON applique le contrat explicite existant avec historique.
Les recherches historiques ont `at_time` fourni par la question observable,
jamais par le gold ; le temps de référence pour la récence est fixé à la dernière
date disponible dans le transcript, pas à l'horloge réelle. Demi-vie des faits
180j figée ; aucun tuning. Aucun fait postérieur au temps demandé admissible.

Le lecteur et le juge sont identiques pour tous les bras ; aucune indication de
traitement au juge. Extraction/encodage réutilisés une fois par conversation,
magasins distincts ; exécution séquentielle, aucun nouveau job lourd tant que les
deux jobs d'indexation de ce lot ne sont pas terminés et qu'un GO distinct manque.

Primaire : exactitude de réponse aux mises à jour, T11–T00. Diagnostics prospectifs
T10–T00 (récence), T01–T00 (supersession) ; trois comparaisons, bootstrap apparié
par conversation, 10k réplications, IC bilatéraux98,33 % (Bonferroni). Promouvoir
uniquement un traitement dont la borne basse du delta primaire>0 et gain moyen≥3
points ; aucune régression d'exactitude historique/sans réponse dépassant 2 points,
IC95 non-infériorité borne basse≥−0,02 sur ces groupes. Contrôle supplémentaire :
zéro invalidation inter-sujet/inter-projet et zéro invalidation sans source.
IC indisponible, échec de garde, coût hors budget ou identité illégale = pas de promotion.
Un gain composite ne prouve pas l'effet de chaque facteur : publier interaction
et contrastes, ne pas choisir un autre gagnant après lecture des résultats.

## Coût à autoriser séparément

Exemple plafond : 50 conversations×100k tokens, 1 250 fenêtres ; extracteur
4 500 entrée/512 sortie ⇒5,625M/0,64M ; équivalent API GPT-4.1-mini≈$3,27.
Pour500 questions×4 bras :2 000 lectures à8 500/256≈$7,62 et2 000 jugements
GPT-4o à2 000/128≈$12,56 ; total≈$23,45 hors acquisition/annotation humaine.
Pilote≤20 questions hors test, marge et audit : proposition **GO $40**, compteur
appels/tokens/$ dur, aucun fallback acheté. En mode ChatGPT, le coût extracteur/
lecteur incrémental peut être nul sans achat, mais quota/durée sont mesurés au
pilote ; ne pas l'assimiler à un service gratuit illimité. Tarifs et authenticité
du modèle sont ceux du [protocole E2E](E2E_HELDOUT_DESIGN_V1.md), à revalider.

Le choix effectif du jeu, son coût/licence, les modèles accessibles, le lecteur,
les prompts hashés et le budget temps seront inscrits dans le plan machine
**avant** le GO d'exécution. Aucune commande d'inférence ne figure dans ce lot.
