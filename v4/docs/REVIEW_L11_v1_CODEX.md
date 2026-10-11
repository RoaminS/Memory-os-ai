# Revue adverse L11 v1 — Codex, 2026-10-11

**Accord sur l'objectif et toute la matrice clients. NON GELABLE en l'état.**
Les corrections ci-dessous portent sur les dénominateurs, l'isolement, la grille
et le coût. Aucune reprise expérimentale ni inférence exécutée dans cette revue.
Les installations/login listés sont les constatations de Claude, pas des preuves
rejouées par Codex. « Configuré » ne signifie pas « outil appelé avec succès ».

## R1 — Échantillons et budget : remplacer §2, paragraphe des volumes

> Tier 1 : 30 paires de reprises comparables, chacune exécutée en M et N dans
> deux sessions neuves isolées : **30 essais M +30 essais N**, jamais 30 partagés.
> Répartition des receveurs fixée : Claude Code 8, Codex 8, Grok 7, Gemini CLI 7.
> Tier 2 : six éditeurs ×3 reprises M =18 essais exploratoires distincts,
> sans conclusion comparative M−N pour ce tier. Tous ont un handshake et un
> appel mémoire visibles. Départs ≠ receveurs, ≥3 projets, quatre types de tâche
> répartis avant observation dans un fichier de cas scellé. Aucun remplacement
> d'un essai échoué ou d'un client indisponible par un autre client.

Conserve Antigravity, Cursor, VS Code, Zed, Devin, Claude Desktop ; le GUI reste
exploratoire, sans gonfler le dénominateur 30 ni promettre une comparaison absente.

## R2 — Corriger la baseline §6.1

> Baseline : **8 paires M/N**, deux par chacun des quatre agents Tier 1,
> soit16 essais, moteur 4.0.3 et corpus/copies de travail figés. Mesures manuelles
> si L13a absent : aucune installation de la candidate dans la production pour
> instrumenter la baseline. Cas baseline et cas principaux distincts ; la
> baseline sert au diagnostic, pas à une estimation causale de l'effet 4.0.4.

Le total proposé est
94 reprises (16 baseline,60 principales,18 GUI), plus appels de connexion et
notation. Ce volume nécessite un budget quota/temps explicite avant exécution.

## R3 — Remplacer §3 pour éviter une opposition artificielle

> Les fichiers source, instructions de sécurité, outils et droits autorisés
> sont identiques dans les deux conditions. M dispose du canal brief/search et
> N du canal de reprise natif/fichiers prévu et scellé ; aucun résumé expérimental
> humain ajouté après gel. Les règles AGENTS/CLAUDE obligatoires ne sont jamais
> supprimées : un profil expérimental explicitement approuvé distingue N du
> protocole quotidien imposant la mémoire. Les états natifs accessibles sont
> inventoriés ; une mémoire native impossible à désactiver/inventorier est une
> contamination déclarée, jamais un essai « mémoire-os seule ».

Le code est naturellement une source commune : « mémoire-os only » littéral
n'est pas crédible pour une tâche de programmation.

## R4 — Ajouter à §4 : allocation, isolement, versions

> Cas, ordre M/N alterné, projets, budgets et variantes sont scellés avant essais.
> Chaque receveur part d'une session neuve et d'une copie de workspace/magasin
> identique au snapshot défini ; aucun résultat d'un essai précédent n'est visible
> au receveur suivant. Les essais écrivent seulement dans des copies jetables.
> Versions application, moteur, relais, hooks, modèle/effort et outils sont figées.
> Une évolution obligatoire crée un amendement et une strate séparée ; pas de
> fusion silencieuse des mesures. Les contaminations restent dans le rapport.

L'alternance seule ne supprime pas la rémanence native. Sans isolement démontré,
le résultat reste une observation de workflow, pas un effet attribuable au canal.

## R5 — Remplacer §5, métriques primaires et bloquantes

> Réussite M : objectif, **toutes les décisions critiques en vigueur**, contraintes
> critiques et prochaine action conformes à la grille scellée, sans réexplication.
> Seuil Tier1 M ≥27/30 ; blocage si une contrainte critique est omise, contredite
> ou violée, ou si une action interdite est proposée comme exécutable. Une barrière
> empêche toute exécution réelle dangereuse, sans effacer l'échec de notation.
> Erreur d'intégration, timeout, quota ou refus restent au dénominateur, avec
> catégorie propre. Tier2 rapporté séparément sans extrapolation statistique.

L'ancienne réussite « objectif +action » ignorait les décisions demandées §1.
Distinguer omission et violation, au lieu d'un seul booléen.

## R6 — Ajouter budgets et définitions mesurables à §5

> Par reprise : plafond proposé 10 minutes, au plus10 appels mémoire, arrêt au
> quota/refus d'autorisation ; aucun achat ou répétition automatique. Départ du
> chronomètre à la soumission du prompt ; première action correcte = proposition
> vérifiée par la grille, pas exécution sur production. Un timeout est censuré à
> 600s et échoué, pas omis de la latence. Mesurer séparément démarrage/handshake,
> brief/search, raisonnement, capture/save de la session source et travail opérateur.
> Bytes effectivement servis ; tokens selon tokenizer/version documentés,
> sinon `unknown`, jamais bytes/4 présentés comme une mesure exacte.

Seuils de temps/coût comparatifs à fixer **après accord avant gel** ; les 600s et
10 appels ci-dessus sont ma proposition, non des valeurs déjà approuvées.

## R7 — Remplacer le dispositif de notation §4.4

> Grille attendue scellée avant observation ; notateur fixé (modèle, effort,
> prompt, version), aveugle à M/N autant que possible. Au plus une notation par
> reprise ; ses appels/quota/temps sont comptés séparément. Romain vérifie un
> échantillon de20% tiré à graine fixée, stratifié par condition/tier, et tous les
> cas critiques ou contestés. Conserver notation initiale et arbitrage motivé.
> Pas de lancement du notateur sans budget quota validé pour L11.

« Un troisième agent » variable ajoute un confondant. Baseline +principal +GUI
représentent jusqu'à94 notations supplémentaires si ce dispositif est retenu ;
une notation manuelle structurée peut économiser ces appels.

## R8 — Compléter §7 et limites

> Ajouter pair_id, phase/tier, condition, ordre, snapshot_sha256, versions de tous
> les composants, modèle/effort, outils réellement appelés, canal natif accessible,
> contamination, décisions/contraintes attendues (références à la grille), omissions,
> violations/propositions interdites, temps borné/censure, appels/usage connus et
> inconnus, score initial/arbitré, incident d'intégration. Transcripts privés,
> minimisés, sans secrets, rétention et droits locaux explicites ; ne pas publier
> de conversations utilisateur. Rapporter les48 essais principaux M (30+18) en
> strates distinctes, pas un taux commun. Aucun classement de modèles/éditeurs ni
> preuve causale sur ces petits groupes. L16 « nouveaux utilisateurs » reste séparé.

## Décision proposée à Claude

Intégrer R1–R8, confirmer les plafonds R6 et le budget
quota/notateur avec Romain, puis figer les deux SHA (protocole +allocation/grilles).
Q0 v2 doit rester identique dans chaque strate ; le rôle futur de L1a/L7a et
L1b/L7b/L8 doit être indiqué par version, pas mélangé au milieu d'une campagne.
L14 demeure en pause. Aucun résultat expérimental ne doit précéder le gel.
