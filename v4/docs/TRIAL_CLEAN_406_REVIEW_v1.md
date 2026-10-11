# Amendements indispensables du trial Claude avant reprise

Relecture en lecture seule du `trial_clean_install_v1.sh`, le 2026-10-12.
Ce fichier appartient au périmètre Claude : aucun changement direct effectué.
**Ne pas relancer v1 tel quel** avec le nouveau profil/default.

1. Le script impose `MEMORY_V4_DIR="$H/.memory-os-v4"`. SQLite refuse ce chemin
   réservé. Remplacer par `MEMORY_V4_DIR="$H/.local/share/memory-os/store"` pour
   l'essai SQLite et passer `--backend sqlite` aux previews/apply. Essai JSONL
   optionnel : autre HOME/répertoire neuf, `--backend jsonl`.
2. Le `guard()` actuel ne mesure que le disque, avant/après les commandes :
   il ne protège pas pendant pip. Ajouter surveillance disque **et swap** chaque
   seconde pendant pip/provision/service, nice 15, un subprocess/job à la fois.
   Seuils : 5 GB réserve, swap 8.5 GB maximum ; mesure absente = stop. La garde
   de quickstart ne peut pas protéger le pip qui précède son installation.
3. Preview/doctor en erreur sont seulement loggés ; le script poursuit.
   Tout échec bloque le prochain step et le succès global. Vérifier pins installés,
   `pip check`, identité/commit de l'artefact, ready (recherche), backend/tâches,
   doctor et résultats explicites save/search/task-CAS. Aucun replay incertain.
4. Label fixe + `cleanup()` fait `bootout` sans attestation et retire aussi un
   plist du HOME réel. Utiliser un label unique par essai, refuser toute occupation,
   ne bootout que le job dont argv/interpréteur/token/store/plist correspondent
   exactement au trial. Ne jamais supprimer de fichier du HOME réel ; le plist
   du trial est sous HOME privé. Conserver les preuves d'échec ; nettoyer seulement
   les fixtures jetables de succès, avec logs/manifestes/mesures préservés.
5. L'argument est seulement un chemin de wheel. Avant téléchargement, vérifier
   le SHA contre le nouveau rapport qualifié, pas celui de `e1c8af2`. Capturer
   le `pip --report` de cette résolution verrouillée et les volumes avant/après.
   Les ≈434 MB précédents ne sont pas une mesure de ce profil. Quotas/API/auth
   d'assistants restent hors de l'essai (clients Codex/Cursor configurés en HOME
   privé, aucune invocation d'agent ou copie d'auth).

La commande de requalification exacte et ses seuils figurent dans
`DISTRIBUTION_D1c_PROFILE_v1.md`. Après succès et libération du disque, Claude
produit son trial v2 avec ces corrections, puis l'exécute selon le GO de Romain.
Aucune exécution du trial ou téléchargement sur Codex.
