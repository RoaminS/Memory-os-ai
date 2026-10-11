# D1b — quickstart candidat, revue et qualification

Historique D1b du 2026-10-11 ; état au 2026-10-12 : version source **4.0.6 candidate**, non publiée/non déployée. Les preuves de cette étape ne remplacent pas la qualification des nouveaux artefacts.
Aucune publication/bascule. GitHub gelé. Pas de modèle téléchargé ou encodé,
aucun assistant réel invoqué par Codex ; tests synthétiques séquentiels.

## Revue du code Claude et corrections intégrées

Le périmètre clients et les six tests de Claude sont conservés. Corrections :

1. Journal **avant** la mutation, fsync et état pending, mise à jour après chaque
   client ; un échec conserve le reçu et le backup privé, même si la CLI a changé
   une config avant de refuser. Un remplacement Claude exige remove puis add :
   si add échoue après remove, le journal signale un état partiel ; restauration
   manuelle du seul ancien serveur après revue si l'entrée n'est plus la nôtre.
2. Conflit de fichier pour **tous** les clients, Claude Code compris. L'empreinte
   de l'entrée désirée porte sur les valeurs réelles, pas les valeurs masquées.
   Claude CLI ajoute `type=stdio` : ce champ fait partie du résultat attendu.
3. Undo borné à un stamp UUID, chemins issus du registre, fichiers réguliers
   appartenant à l'utilisateur, liens refusés. Restauration de notre entrée
   seulement, après comparaison sémantique ; TOML ajouté peut être retiré en
   préservant les autres tables. TOML existant à modifier et JSONC Zed : manuel.
4. Répertoires journaux 0700, fichiers/backups 0600, écriture atomique avec fsync.
   Les backups peuvent contenir les autres entrées et leurs secrets : leur contenu
   reste privé. L'aperçu ne montre pas l'ancienne entrée ; le rapport anonymisé
   n'inclut ni configurations étrangères ni contenu de mémoire/backups.
5. Choix clients via `--clients` (défaut explicite affiché : claude-code,codex).
   Claude Desktop actif bloque l'application. Notices pour overrides `.mcp.json`
   et trust Gemini ; aucune mutation du scope projet ou des auth des assistants.

## Contrat CLI installé

**Ces commandes ne sont pas disponibles dans le PyPI 4.0.5 actuel.**

```sh
memory-os-admin quickstart --clients claude-code,codex
memory-os-admin quickstart --clients claude-code,codex --report
# Nouvelle installation : SQLite+tâches par défaut ; alternative explicite :
memory-os-admin quickstart --clients claude-code,codex --backend jsonl
# Seulement sur une nouvelle installation macOS après revue de l'aperçu :
memory-os-admin quickstart --clients claude-code,codex --apply
# Si ressources absentes, consentement distinct :
memory-os-admin quickstart --clients claude-code,codex --apply --download-approved
# Démo de sauvegarde/recherche opt-in, projet unique ; aucune inférence LLM :
memory-os-admin quickstart --clients claude-code,codex --apply --demo
memory-os-admin quickstart --undo STAMP
```

Ordre : préflight → identité embarquée → ressources → token → launchd/readiness
→ clients → doctor + handshake du relais packagé → démo optionnelle. L'aperçu
ne crée aucun fichier ou répertoire et ne contacte pas les fournisseurs. Il
vérifie le cache/table existants et interroge seulement launchd/port local.
`--apply` seul ne télécharge rien. Aucun installateur de dépendances implicite ;
`[ops,brief]` doit être présent. Valeur du token jamais affichée/passée au CLI.

Décision Romain du 2026-10-11 : SQLite+tâches pour un **nouvel** utilisateur,
JSONL disponible via `--backend jsonl`, auto/native/batch32, C4 inchangé,
maintenance/supersession confinées. Le registre demeure inactif. Chemin neuf SQLite :
`~/.local/share/memory-os/store` ; le chemin historique n'est jamais migré implicitement.
Un backend de plist existant est conservé comme choix d'aperçu, sans autoriser une
modification du service ; un changement explicite de backend est refusé par la
vérification du plist. Détails du profil verrouillé dans `DISTRIBUTION_D1c_PROFILE_v1.md`.
Exige un wheel à descripteur valide (env/source seuls refusés), Python durable,
label/plist/argv/token/magasin concordants. Un job différent ou non attestable
est refusé ; aucun `--repair`, kill ou remplacement de plist existant. Dossier
non vide sans service concordant : utiliser le runbook d'upgrade séparé.

Readiness exige recherche réellement prête, backend, identité/version, révision
Granite, profil et tokenizer exacts concordants. Délai de démarrage : 180 s,
conservé en échec ; pas de retry de mutation. Garde disque ≥5 GB et swap ≤8.5 GB
avant application et pendant l'attente, même avec ressources présentes. Si la
limite est franchie après bootstrap, seul le nouveau job attesté créé par ce
quickstart est arrêté ; un job existant/étranger n'est jamais arrêté.

Undo : **configs clients seulement** ; service, token, ressources et magasin sont
conservés pour préserver les écritures récentes. Une entrée modifiée depuis
l'application entraîne refus, sans restauration de fichier entier. Les écritures
concurrentes d'une app restent possibles après le dernier contrôle : fermer
l'app quand demandé ; aucun verrou partagé n'est prétendu avec les éditeurs.

Succès quickstart = configs vérifiées + doctor sans FAIL + handshake/list_tools/
memory_status du relais. **Configured ≠ connected ≠ real usage observed** : la
connexion de chaque assistant exige redémarrage et test par l'utilisateur. La
démo prouve save/search du relais, jamais un handoff mesuré. Les exceptions
MCP/AnyIO restent des échecs journaux ; aucune écriture incertaine n'est rejouée.

## Preuves et commandes Claude

Les 6 tests fournis par Claude passent, étendus par Codex : **42 cas quickstart**.
La qualification légère D1a+D1b : **272 tests, zéro échec/skip** ; logs/XML conservés
sous `.local-qualification/distribution-d1b-light-v3/`. Tests : HOME jetable,
ressources minuscules/simulées, encodeur absent, CLI/launchd/MCP simulés. Ils
vérifient l'aperçu strict, les refus, le journal de panne, le guard-stop borné,
la sémantique undo, les paramètres Claude réalistes et le non-rejeu de démo.

Après import du bundle nommé et fin du tour Codex, depuis la racine du clone :

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:v4/src:v4 \
.local-qualification/brief-test-runtime-claude-3c-v1/bin/python -B \
scripts/qualify_distribution_d1a.py --build \
--source-commit SOURCE_D1B_COMPLET \
--build-python /opt/homebrew/bin/python3.11 \
--output .local-qualification/distribution-d1b-artifacts-claude-v1
```

Dossier neuf ; les refus D1a artifacts v1/v2 sont conservés. La garde réelle
s'applique avant/pour chaque build/install ; aucune dépendance téléchargée.
En plus des builds/identités/entrypoints : garde de tous les marqueurs avec
packaging ET pip, puis aperçu quickstart depuis chacun des deux environnements
installés, HOME/port/label privés, empreinte du HOME identique avant/après.
Cela ne lance ni serveur ni assistant réel et ne lit pas leurs auth.

**À relire par Claude** : parsing conservateur de `launchctl print`, refus d'un
service différent, garde et arrêt du seul nouveau job, réconciliation du journal
client après erreur de CLI, et `_relay_probe`. Qualification réelle d'une nouvelle
installation et essais D1c 4/5 en <10 min restent requis ; les tests simulés et
les smokes qui réutilisent les dépendances ne les remplacent pas. Pas de commande
`--apply` à exécuter sur la production de Romain pour ce lot.

Mesures implémentées : temps ressources/token/readiness/clients/checks/démo,
octets planifiés/téléchargés par provisionnement, succès/état par client. Volumes
dépendances/venv/cache, pic mémoire/température et temps utilisateur complet
restent à mesurer sur les installs propres ; aucun gain d'espace revendiqué.
