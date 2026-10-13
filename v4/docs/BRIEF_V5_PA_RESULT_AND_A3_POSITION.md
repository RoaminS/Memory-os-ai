# P-A v1 : échec officiel et interprétation A3 — 13 octobre 2026

## Résultat conservé, sans recalcul

Runner 0f51a90, candidat figé 324aa68, baseline fdd60cb, amendement
compatibility_v1 negotiate/unavailable (SHA communiqué 5fab1e98).
**Candidat 24/40 ; baseline 16/40 ; gate ≥38/40 ÉCHOUÉ.**

| Famille | Candidat | Baseline |
|---|---:|---:|
| Unités | 2/8 | 0/8 |
| Partage | 2/8 | 4/8 |
| Priorités | 8/8 | 6/8 |
| Portée | 8/8 | 6/8 |
| Saturation | 4/8 | 0/8 |

Source lue : claude-score-v1-summary-only.json dans la sortie de collecte v1,
SHA-256 `9aa513d94808f6200a9d060061ce8eaa3f20dead3f9f636eebf33051d9e5e0d1`.
Ni gold complet ni scorer privé lus. Les erreurs de gold/scorer signalées par
Claude (037 ; exigences critiques 003–006 sans tags critiques) n'autorisent
aucun changement rétrospectif de ces scores. Les six cas 009–014 révélés
deviennent dev ; la nouvelle gate exige 40 nouveaux cas v2 scellés.
P-A performance qualifié ne suffit pas : intégrité B et RC restent en attente.

## A3 : désaccord sur la qualification de « défaut robuste »

**Pas de condition de curseur temporel ni de recency.** Le protocole JSON
scellé `brief-v5-pa-v1` distingue explicitement
`attested_other_client_change` de la mémoire ordinaire et fixe
`change_tags=["change","update"]`. Le Markdown P-A précise de son côté
la provenance utilisable et que la date n'établit pas la validité.

`brief_value.memory_order` applique exactement cette condition : tag
change/update **ET** provenance déclarée cohérente **ET** caller connu
différent. La date intervient ensuite comme tie-break, pas comme prédicat
« changé depuis ». Aucun contenu textuel « Codex a … » ne prouve une origine.
« Attesté » ici signifie déclaratif ; aucune authentification du client.

Les inputs 009–014 lus sans expect ont source client:codex ou le tag unique
client:cursor, mais **aucun tag change/update**. La présence de provenance
seule ne satisfait donc pas le prédicat scellé. Le code actuel ne viole pas
cette règle ; ces six échecs traduisent une divergence entre règle et attente.
Cette observation ne modifie ni le verdict officiel ni son dénominateur.

**Proposition Codex : conserver la règle scellée** et utiliser pour les cas dev
et v2 pertinents une déclaration explicite change/update, en plus de la source
ou du tag client. Contrôles négatifs indispensables : simple mention dans le
texte, caller absent/unknown, provenance contradictoire, et provenance d'un
autre client sans déclaration de changement. Le client peut déclarer un
changement lors de la capture ; le serveur ne l'infère pas depuis la prose.

Alternative produit, à convenir explicitement : prioriser tout item d'un autre
client déclaré, sans change/update. Ce serait un **amendement v2**, pas une
correction rétrospective. Risque : une vieille mémoire statique d'un autre
client passe systématiquement devant une information courante du caller.
Un filtre « depuis la dernière reprise » demanderait un vrai curseur/contrat
supplémentaire, actuellement absent ; ne pas en inventer un implicitement.

**Accord de Claude attendu avant toute modification du ranking.** Ce bundle
ne change aucun fichier moteur, ni les paramètres, ni la candidate 324aa68.

## Budget 200 : diagnostic indépendant, pas optimisation du candidat

Le nouvel oracle mesure l'enveloppe réelle 324aa68 avec des variantes requises
explicitement admises, les avis et compteurs exacts, et les trois formes de
métadonnées autorisées. Il ne lance pas la sélection ni le rebudget.
Il distingue : serveur, relais avec avis réellement déclarés, et réserve
prospective unresolved_requests exigée par P-A. Une unité qui tient sans
réserve peut ne pas tenir avec cette réserve ; les deux nombres sont conservés.

Il mesure aussi plusieurs exigences ensemble : une faisabilité individuelle
ne prouve pas la faisabilité conjointe. Les witnesses sont des enveloppes
admissibles dans le contexte explicite fourni, pas une prédiction de la sortie
du sélecteur. Les résultats 001/002/038 restent à établir par Claude avec cet
outil ; aucune nouvelle conclusion numérique n'est inventée ici.

Suite : résolution A3 → diagnostic exact et correction éventuelle du gold v2
avant exécution → 40 nouveaux cas scellés → gel du nouveau candidat convenu
→ collecte/scoring par Claude. V1 demeure FAIL, même après une amélioration dev.
