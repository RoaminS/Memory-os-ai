# /ready : protocole de mesure 4.0.8, scellé avant la mesure candidate

16 octobre 2026. Baseline f003c3a (4.0.7). Aucune donnée de production.

Une seule machine, exécutions séquentielles, même Python et dépendances : 3 500
faits synthétiques, 4 375 versions, 20 blocs, 30 tâches et leurs reçus/événements.
Encodeur hash 16 dimensions, zéro appel neuronal. C'est une échelle comparable
au nombre d'éléments observé dans la répétition, pas une reproduction du contenu,
de la distribution des historiques ou du temps de réponse de la production.

Le script `scripts/measure_ready_408.py` exporte chaque commit dans un dossier
jetable, crée un descripteur de fixture cohérent, mesure les composants puis 31
GET authentifiés via ASGI. Le descripteur exerce les lectures du vérificateur
installé mais ne constitue PAS une qualification de wheel. Premier appel séparé ;
p50/p95/max sur les 30 suivants ; mêmes paramètres pour les deux bras. Pas de
TCP, relay, modèle réel ni contention artificielle. Temporaire supprimé en fin
normale ou en cas d'échec, rapport conservé. Disque minimum 3 Go à chaque requête.

Critères locaux figés : 31/31 réponses prêtes ; audit jamais réutilisé après
écriture/rollback/changement de source ; défauts de lease/poison/index/modèle
refusés immédiatement ; défaut mémoire non suivi détecté en une seconde au plus ;
p95 chaud ≤75 ms et inférieur à la baseline. Le premier appel exhaustif et le
premier appel après invalidation ne sont pas soumis à ce seuil chaud. Aucune
conclusion sur des gains de rappel, de qualité ou de productivité.

La mesure locale peut ne pas attester swap/nice dans le sandbox. Pour une preuve
opérateur : vérifier swap ≤8,5 Go, lancer sous nice15, concurrence1. Refus si swap
non mesurable ou disque insuffisant. Rejouer deux bras dans des dossiers NEUFS ;
un seul bras est actif à la fois. Aucun téléchargement.

Complément figé avant exécution : cinq GET supplémentaires espacés de 1,01s
(au-delà du TTL), publiés séparément. Ils doivent tous être prêts ; aucun gain de
latence n'est requis après expiration. Cela évite de présenter le gain d'une
rafale comme celui d'un audit exhaustif isolé. `--require-swap` impose la mesure
opérateur de swap ; sans cette option les mesures locales sont non qualifiantes.
Les premières exécutions de développement restent conservées, jamais écrasées.
