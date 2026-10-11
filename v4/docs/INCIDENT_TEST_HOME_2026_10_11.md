# Incident : tests v3 écrivant des configurations utilisateur

## Faits et cause racine

Le 2026-10-11, Claude a constaté une réécriture de la configuration Claude
Desktop pendant `source_release_checks.py`, étape `v3_cached` (12:00:46–12:01:38).
L'entrée `memory-os-ai` pointait vers le runtime de test et `memory_os_ai.server`
(v3), au lieu du relais v4. Claude a sauvegardé le fichier écrasé puis restauré
le relais ; neuf autres configurations contrôlées avaient des mtimes inchangés.
Codex n'a modifié aucune configuration utilisateur ni aucun magasin de production.

Contrôle supplémentaire de Codex en lecture seule : `~/.codex/mcp.json`,
`.mcp.json` et `.vscode/mcp.json` de ce clone contiennent aussi l'entrée v3
`["-m", "memory_os_ai.server"]`, avec mtimes respectifs
`2026-10-11T12:01:37.630214+02:00`, `.629959` et `.630413`.
Cela concorde exactement avec le même setup. Le premier est distinct du
`~/.codex/config.toml` actif ; les deux autres sont des configurations du projet,
distinctes de celles du scope utilisateur. Claude doit vérifier leur usage et
restaurer les entrées relayées appropriées si nécessaire ; cette livraison
ne les édite pas et ne les inclut pas dans le commit/bundle.

Test responsable :
`tests/test_zz_full_coverage.py::TestSetupMainCLI::test_main_all_target`.
Il mockait `setup.setup_claude_desktop`, `setup_codex`, etc. Cependant,
`setup.main()` appelle les fonctions conservées **à l'import** dans `TARGETS`.
Ces références restaient originales. Le test n'isolait ni HOME ni le cwd.
Les callbacks réels pouvaient donc écrire Claude Desktop, `.codex/mcp.json`,
`.memory-os-ai`, `.mcp.json` et `.vscode/mcp.json` du cwd. Le bloc `except Exception`
de `main()` pouvait masquer un refus du sandbox : un test vert ne prouvait pas
l'absence de tentatives d'écriture.

## Qualification 4.0.3 : exposition confirmée, effet historique non attesté

Preuves conservées dans `.local-qualification/release-4.0.3-v2` :

- `source-proof.json` : source `53030c2c99b09b5dc3f8467dda0a114e47477ee2`,
  `v3_cached` = 566 passed, zéro erreur/échec/skip.
- `v3_cached.xml` : suite datée `2026-10-10T20:09:12.225002+02:00`,
  le test fautif apparaît réussi (0,002 s).
- Le test du commit attesté contient les mêmes mocks inefficaces ; le lanceur
  n'isole pas HOME. Le même risque existe dans les tests distribués en 4.0.3.

La qualification 4.0.3 **a exécuté le chemin dangereux**. Aucune empreinte ni
mtime de configuration avant/après cette qualification n'a été conservée dans
ces preuves : affirmer une réécriture effective à cette date serait excessif.
Ne pas réexécuter l'ancien test contre le vrai HOME pour la démontrer.

## Correction destinée à 4.0.5

1. Le test mocke le dictionnaire `TARGETS` et vérifie chaque callback attendu.
2. `conftest.py`, partagé par les suites v3/v4, redirige HOME, USERPROFILE,
   AppData et XDG **avant collecte** vers un dossier temporaire, puis vers un
   HOME distinct pour chaque test. Les conftest des deux suites le chargent aussi
   quand `-c v4/pyproject.toml`/`--confcutdir` excluent le conftest racine.
   Les magasins/ops hérités sont redirigés ;
   jeton/registre hérités supprimés. `_HOME` du setup est redirigé par test.
   `MEMORY_WORKSPACE` est limité au checkout pour lire les fixtures et la
   documentation du selfcheck ; cela n'autorise aucune écriture hors des chemins
   jetables permis par la garde.
   Le HOME du test reste séparé de `tmp_path`, dont les inventaires de magasins
   doivent être exhaustifs. Les caches HF existants restent lisibles sans copie
   de poids ni téléchargement ; leurs écritures sont interdites.
3. `scripts/test_home_guard.py` bloque les écritures des API Python auditées sous
   le HOME opérateur, sauf répertoires explicitement possédés : HOME jetable,
   `.local-qualification` et `.pytest_cache` du checkout. Les chemins sont
   résolus, y compris symlinks/dir_fd ; les connexions SQLite natives ouvrant
   un fichier potentiellement inscriptible sont également contrôlées.
   Une violation est enregistrée : le teardown/session échoue même si le code
   intercepte `PermissionError`. Lectures autorisées, aucune directive mémoire.
4. La prochaine sdist inclut conftest + support, et la qualification de source
   inclut leur empreinte. Un lancement pytest qui ignore volontairement les
   conftest n'est pas un lancement de qualification autorisé.

La garde Python **n'est pas un sandbox système** : les écritures arbitraires
d'un programme natif/shell externe ne sont pas interceptées exhaustivement.
Les sous-processus héritent du HOME jetable. Le lanceur supplémentaire compare
avant/après SHA-256, inode et mtime de onze configurations clients, anciennes
configurations v3 et fichiers du projet, sans divulguer leur contenu. Un changement
est bloquant et exige une enquête d'attribution (un client concurrent peut écrire).
Ces mesures complètent le sandbox et ne promettent pas sa substitution.

## Preuves et reprise par Claude

15 régressions légères : vrai setup `all` sur HOME/projet jetables ; blocages
write/open/rename/unlink/mkdir/symlink, dir_fd, descripteur et SQLite ; erreur
interceptée provoquant quand même un échec pytest ; HOME isolé dès la collecte ;
inclusion sdist et entrée pytest v4 avec confcutdir. Aucune réexécution de l'ancien
test sur un profil réel.

Depuis le clone, après import du bundle, utiliser un dossier neuf :

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:v4/src:v4 \
  MEMORY_V4_CL100K_PATH=/Users/romain/.config/memory-os/cl100k_base.tiktoken \
  .local-qualification/brief-test-runtime-claude-3c-v1/bin/python -B \
  scripts/qualify_405_test_isolation.py --suite full \
  --output .local-qualification/405-test-isolation-claude-v1
```

Le runtime de test est déjà provisionné (tiktoken + extension), pas modifié.
La table existante est utilisée en lecture seule ; aucun téléchargement.
Le lanceur exécute séquentiellement v3 synthétique puis v4 non-slow incluant les
20 TCP. Minimum 585 v3 / 1512 v4, zéro échec/erreur/skip, aucune configuration
client modifiée. Garde disque 5 Go/swap 8,5 Go avant et pendant chaque suite ;
swap non mesurable = refus de `--suite full`, pas de restart automatique.
Une mesure `--suite unit` dans le sandbox déclare explicitement le swap inconnu
et exclut les 20 TCP. Aucun encodage neuronal, API, publication ou bascule.

Cette livraison ne fabrique pas de nouvelles distributions 4.0.4 et ne change
pas leur manifeste qualifié. Le numéro de package reste 4.0.4 jusqu'à la
préparation et qualification séparées de la candidate 4.0.5.
