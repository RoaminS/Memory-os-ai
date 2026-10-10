# T05 — candidate 4.0.2 : locale verte, publication FERMÉE

**Non publiée, non déployée.** Source des artefacts :
`03ffc075cd2e31bd0480839182d1024c740b7168`. Le commit de ce dossier est un
reçu post-construction ; `releases/` n'entre pas dans le wheel/sdist. Aucun
changement de runtime après cette source. Le choix C4 proposé reste soumis au
GO d'activation de Romain ; SQLite S2/S3 est toujours opt-in, S4 non livré.

Revue/import Claude : `139c6d1→dbfe7ac`, `228bbb1→9a780c8`, `02af645→ca01335`,
`0eb80ba→21332c4`. Correctifs de revue : `063e68c` ; décision C4 : `1c37d3a` ;
version/scripts : `48468bb`, `1226ac7` ; packaging : `98d5dd9`, `c8076d7` ;
prérequis build attested : `03ffc07`. Voir
[revue](../../v4/docs/REVIEW_OPS_DOCTOR_4_0_2.md) et
[décisions/rollback](../../v4/docs/RELEASE_4_0_2_DECISIONS.md).

## Preuves et limites

Logs, commandes, pool/environnements et manifeste sous
`.local-qualification/release-4.0.2/`. [Reçu de qualification](qualification.json),
[reçu de résultat S](evaluation.json) (479 cas avec preuve, 466 clusters).

| Contrôle | Résultat |
|---|---|
| Sonde adverse ops adaptée au setup auto-jeton | `release_ready=true`, 0 blocage observé (ce verdict de sonde n'ouvre pas la garde PyPI) |
| Tests ciblés doctor/C4/garde/retrieval | 236 passed ; garde seule : 13 passed |
| v3 checkout, coverage ≥80 % | **561 passed**, **82,00 %** |
| v4 checkout léger, coverage ≥80 % | **934 passed**, **86,93 %**, 1 test réel MiniLM exclu ; 10 TCP source séparés |
| Lint | `ruff check src tests scripts` + E9,F src/tests/v4/bench/scripts : verts |
| Build | wheel construit depuis le sdist, `build --no-isolation`, SOURCE_DATE_EPOCH du commit source |
| Twine | `twine check --strict` : **2 PASSED** |
| Installations | 2 venv neufs hors dépôt, `[ops]`, no-index/no-deps/no-build-isolation/no-cache ; **2 pip check verts** |
| Paquets installés | 53 modules runtime wheel byte-identiques aux sources ; versions, docs, licences LGPL/GPL et cinq entry points vérifiés |
| Commandes installées | Deux `package_smoke.py --version 4.0.2` verts : trois serveurs stdio réels hash, help/selfcheck, admin, échec relais indisponible contrôlé |
| Suites depuis sdist extrait hors dépôt | **561 v3**, **932 v4**, 3 désélections v4 détaillées ci-dessous |
| Inventaire relais installé | `--check-only` vert ; aucun TCP exécuté ici ni preuve de succès fabriquée |
| Garde actuelle | Refus code 1 attendu : TCP source et MiniLM cached encore en attente ; **release_ready=false** |

Python 3.11.14/macOS ARM64. Python 3.12 absent, 3.14 non qualifié. Dépendances
partagées via pool **privé copié** de la qualification précédente, sans projet
éditable/hooks de source ; chaque paquet memory-os-ai est propre à son venv.
Aucun téléchargement ni inférence neuronale dans cette qualification locale.
Ce n'est pas une preuve de résolution réseau fraîche ou de compatibilité Linux/Windows.
Les imports/stubs de modèles sont permis ; le seul test d'encodage réel reste à Claude.

L'essai hors dépôt a trouvé et corrigé deux omissions sdist (sources bench et
fixtures legacy bridges). Deux tests **d'archives Git** ne s'appliquent pas au
sdist dépourvu d'historique :
`test_session_copies.py::test_archived_reader_and_200_predictions_match_without_model`
et `test_evidence_bootstrap.py::test_s_preflight_stops_before_any_model_worker`.
Ils sont verts dans les 934 tests du checkout et explicitement désélectionnés
uniquement dans cet essai sdist ; le troisième est le test `slow` MiniLM.
Les erreurs intermédiaires sont conservées dans la trace de qualification ;
elles ne qualifient aucun ancien artefact.

Les contrôles checkout verts de 1226ac7 sont réutilisés après modifications
MANIFEST/README seulement ; diff src/tests/v4/src/v4/tests inchangé.
L'essai du sdist c8076d7 est réutilisé après le dernier README : **172 fichiers**
runtime/bench/tests/bridges comparés exhaustivement, byte-identiques au sdist final.
Les deux installations et leurs essais console ont été refaits sur les **SHA finaux**.
`reuse-sdist-proof.json` et `reused-checks.json` détaillent la provenance.
Aucune source/modèle/options/résultat S historique modifié ou relancé.

Les backups/drills CLI restent des diagnostics sur magasins synthétiques, pas
une qualification SQLite S4 ou une coupure électrique. JSONL n'a pas l'atomicité
multi-fichiers ; les transactions SQLite S2 ne constituent pas des backups S4.
Aucun magasin/service/config client de production ou dépôt original modifié.
Les deux configurations utilisateur déjà modifiées dans ce clone ne sont pas livrées.

## Empreintes finales — ne pas reconstruire

```text
9cf2f5ccec0da504e7aff5aad5fd782a621340b650eb0902f1e2ff45f9c5543a  memory_os_ai-4.0.2-py3-none-any.whl
1c3e68c456cd33e96d7a9953fc35c2d3ec29cebdf6aa757f2863787407512552  memory_os_ai-4.0.2.tar.gz
```

Artefacts : `.local-qualification/release-4.0.2/artifacts/`, hors bundle Git.
Les anciennes empreintes ont été retirées de la qualification finale.

## Claude : exécuter hors sandbox, dans cet ordre

Depuis le clone Codex, garder les artefacts/environnements en place. Les scripts
n'installent rien, ne contrôlent aucun service existant et ne téléchargent rien.
Le premier exécute les **10 TCP source + 1 MiniLM déjà en cache**, séquentiellement,
nice15, et surveille swap/disque avant lancement et toutes les deux secondes :
arrêt si swap >8,5 Go ou disque <5 Go, si sonde indisponible ou délai >300 s/suite.
Les groupes de processus arrêtés sont uniquement ceux créés pour ces tests.

```sh
cd /Users/romain/Documents/Codex/2026-10-09/v/work/implementation
release_python=/Users/romain/Memory-os-ai/.venv/bin/python
release_dir=.local-qualification/release-4.0.2
"$release_python" scripts/source_release_checks.py \
  --manifest "$release_dir/manifest.json" --output "$release_dir/source-proof.json"
"$release_python" scripts/verify_release_manifest.py "$release_dir/manifest.json" \
  --record-source-proof "$release_dir/source-proof.json"
"$release_python" scripts/installed_relay_e2e.py \
  --manifest "$release_dir/manifest.json" --environments "$release_dir/environments.json" \
  --output "$release_dir/installed-relay-proof.json"
"$release_python" scripts/verify_release_manifest.py "$release_dir/manifest.json" \
  --record-relay-proof "$release_dir/installed-relay-proof.json"
"$release_python" scripts/verify_release_manifest.py "$release_dir/manifest.json"
```

Arrêter au premier refus : ne pas lancer les commandes suivantes sur preuve
absente/en échec. Attendu source : **10 +1 passed, zéro skip**. Attendu installé :
**wheel PASS + sdist PASS** (round-trip, SIGKILL synthétique, panne bornée,
reconnexion du même relais, exactement deux faits acquittés, absence de rejeu,
arrêt AnyIO propre, version/commit/provenance site-packages et SHA concordants).
En cas de panne de mesure, ne pas réduire le protocole pour obtenir un PASS.
Le manifest ne peut s'ouvrir qu'après source ET les deux relais installés.
Claude conserve les nouveaux rapports, actualise le reçu/dossier dans son périmètre,
et relit C4/rollback, Grok TOML natif, garde de publication et limites SQLite S4.

## Romain : décisions distinctes, aucune action exécutée ici

Après preuves complètes et GO PyPI, authentification hors commande, la commande
exacte gardée est :

```sh
/Users/romain/Memory-os-ai/.venv/bin/python scripts/verify_release_manifest.py \
  .local-qualification/release-4.0.2/manifest.json && \
/Users/romain/Memory-os-ai/.venv/bin/python -m twine upload --non-interactive \
  --repository-url https://upload.pypi.org/legacy/ \
  .local-qualification/release-4.0.2/artifacts/memory_os_ai-4.0.2-py3-none-any.whl \
  .local-qualification/release-4.0.2/artifacts/memory_os_ai-4.0.2.tar.gz
```

Pour un setup **ultérieur autorisé** du paquet installé, fournir l'identité
`MEMORY_V4_BUILD_COMMIT=03ffc075cd2e31bd0480839182d1024c740b7168` ; cela ne prouve
pas à lui seul l'authenticité du build. Référence : SHA du dossier + preuves reçues.
Décider séparément activation C4 (copies Granite/FR/EN d'abord), SQLite après S4,
et [banc neuf/coûts](../../v4/docs/NEXT_BENCHMARK_4_0_2.md) : BEAM 100K ≈5,43 Mo,
lecteur/jugement figés estimés à ≤$6 pour 500×2 réponses sous hypothèses détaillées.
Aucune revendication top1 et aucune soumission. Aucune mutation de mémoire live ;
Claude peut relayer ce reçu à la mémoire partagée depuis son périmètre autorisé.
