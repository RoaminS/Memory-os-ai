# Revue read-only du trial clean-install v2

2026-10-12. Scripts Claude `trial_clean_install_v2.sh` et `trial_probe_v1.py`
lus sans modification. Le trial n'a pas été exécuté par Codex. **Amendements
requis avant l'unique essai sur le wheel 4.0.6 qualifié.** Aucun GO supplémentaire
n'est demandé pour corriger ces scripts dans le périmètre Claude.

1. **Résultat demo : niveau JSON incorrect (bloquant).** `apply_install`
   met le résultat complet du probe dans `progress['demo']`, et le probe
   met son résultat de sauvegarde dans `result['demo']`. Remplacer :
   `r['demo']['saved_and_found'] is True` par
   `r['demo']['connected'] is True and r['demo']['demo']['saved_and_found'] is True`.
   La version actuelle échouerait après une écriture de demo réussie.
2. **Arrêt attesté avant suppression des fixtures (bloquant).** Deux grep
   sur la sortie launchctl ne prouvent pas la propriété du service. Comparer
   exactement label, argv/exécutable, plist privé, token et data dir aux valeurs
   du plan/appliquées ; refuser toute divergence. Un bootout refusé/échoué doit
   mettre `status=FAIL` et conserver `T`. Après bootout, vérifier job absent ET
   port sans listener avant tout `rm -rf`. Ne jamais retirer l'environnement
   d'un service encore actif ou dont l'arrêt reste incertain.
3. **Gardes non propagées (bloquant).** `guarded doctor` et `guarded report`
   ont leur code de retour ignoré. Le report exige `|| fail ...`. Pour doctor,
   capturer le code : seuls 0 ou 1 peuvent aller au filtre sémantique des
   clients choisis (1 peut refléter les clients non choisis) ; 99, signal ou
   autre erreur bloque immédiatement. Ne pas convertir un arrêt disque/swap
   en succès parce qu'un JSON complet a déjà été écrit. Ajouter une échéance
   maximale documentée et tuer/attendre tous les descendants encore actifs,
   y compris ceux ayant ignoré SIGTERM ; ne jamais poursuivre en parallèle.
4. **Attestation de production et HTTP (bloquant pour sa promesse).**
   `unavailable == unavailable` ne prouve rien ; une lecture /ready absente
   avant ou après doit refuser l'attestation, sans déclarer production inchangée.
   Utiliser un opener sans proxy ni redirection pour le bearer loopback,
   validation sécurisée du fichier token (0600, fichier régulier, pas symlink).
   Comparer version/commit/profil/backend/identité, tout en formulant le résultat
   comme « identité/configuration inchangées » : cela n'atteste pas l'absence
   de mutations légitimes par les autres clients pendant l'essai.
5. **Configurations applicatives : éviter le faux positif déjà corrigé.**
   Utiliser les empreintes sémantiques de l'entrée memory-os-ai pour les 11
   clients du registre (même logique que `snapshots` du qualifier). Le SHA
   du fichier entier reste approprié pour les fichiers stricts du dépôt,
   pas pour Desktop et les applications qui réécrivent leur état. Un fichier
   illisible/invalide bloque ; ne pas sérialiser les autres entrées/secrets.

Compléments avant interprétation des mesures : capturer l'inventaire du HOME
privé avant/après preview pour démontrer zéro écriture ; /ready doit attester
la version 4.0.6, le commit qualifié, le backend/work_state choisis ET
`retrieval_ready=true`, pas seulement un profil présent. Le probe privé
vérifie le save/search et les ETags sans rejeu ; il ne lance aucun vrai client.
Capturer le script corrigé et son SHA dans les preuves. Préserver tous les
rapports d'échec ; ne modifier ni le wheel ni les critères après l'essai.
