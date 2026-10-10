# T05 4.0.3 v2 — candidate locale verte, garde FERMÉE

Non publiée, non déployée. Cette candidate remplace les artefacts v1.
Tentative v1 conservée intégralement dans `.local-qualification/release-4.0.3/`
(9/10 TCP, assertion de version ancienne) ; aucune preuve v1 réutilisée.
[Audit et correctifs de version](VERSION_AUDIT_v2.md) ; **25 readiness ASGI verts**.

Source définitive des artefacts et du protocole :
`53030c2c99b09b5dc3f8467dda0a114e47477ee2`.
Ce dossier est un reçu après construction ; `releases/` n'entre pas dans les
artefacts. Aucune modification runtime/banc après cette source.

[Reçu structuré](qualification.json), logs/commandes/venv/manifeste/artefacts :
`.local-qualification/release-4.0.3-v2/`. Aucun modèle téléchargé, encodage neuronal,
appel LLM/API, TCP ou magasin de production exécuté/ouvert par Codex.

| Preuve locale | Résultat |
|---|---|
| Sonde adverse ops actuelle | release_ready=true pour la sonde seulement ; garde générale fermée |
| Suite v3 complète unitaire | **566 passed**, coverage **81,83 %**, encodeur synthétique explicite |
| Suite v4 légère | **1 041 passed**, coverage **87,00 %**, 10 TCP +1 MiniLM réel différés |
| Lint | src/tests/scripts complet + E9,F v4/src/tests/bench : verts |
| Build | sdist puis wheel depuis sdist, no-isolation, SOURCE_DATE_EPOCH du commit |
| Twine | check --strict : **2 PASSED** |
| Deux installations hors dépôt | wheel et sdist, extras ops, no-index/no-deps/no-cache/no-build-isolation, pip check verts |
| Cinq commandes/docs/licences | deux package_smoke verts : trois serveurs stdio hash, admin, relais indisponible contrôlé, docs/entry points/licences |
| Suites sdist hors dépôt | **566 v3**, **1 037 v4**, exclusions Git/slow ci-dessous |
| Résultats historiques MPS | verify_mps_reuse_result_v1 OK ; criterion_met=false conservé, aucun nouveau score |
| Publication | refus attendu, release_ready=false : modèle/TCP/copie/relais encore en attente |

Python3.11.14/macOS ARM64 ; 3.12 absent. Dépendances partagées depuis un pool privé
copié, sans projet éditable/pth source ; aucun changement du venv de production.
Les quatre versions du pool (Torch2.10.0/ST5.2.3/Transformers5.2.0/NumPy2.4.2)
correspondent à celles du runtime4.0.2 installé. Ce n'est pas une résolution réseau
fraîche ni une qualification Linux/Windows/Python3.12.

## Transparence et exclusions

Les premiers essais v3 sans le mode synthétique ont échoué avant l'encodage, en
cherchant des métadonnées HF interdites par le firewall du banc. La fixture
resident était également restée sans embed_dtype : corrigée. Les essais avec
encodeur synthétique ont révélé le conflit libomp FAISS/Torch : l'import de la
façade v4 applique maintenant sa politique existante à ce mode de test, threads1.
Les traces d'échec sont conservées dans `.local-qualification/403-*.log`.
**Ce double ne valide pas la qualité neuronale v3** : une nouvelle preuve du
jeu complet avec MiniLM cached réel est obligatoire dans source_release_checks,
`MEMORY_TEST_HASH_V3=0`, mêmes gardes et politique OpenMP, avant de lever la garde.

Les premiers artefacts et preuves intermédiaires sont conservés sous
`release-4.0.3.failed-sdist-archives-c242008` et
`release-4.0.3.before-reader-isolation-e980509`. Ils ne sont pas qualifiés/publiables.
Seules les deux empreintes finales ci-dessous doivent être utilisées.

Le sdist n'a pas d'historique Git. Quatre tests verts dans le clone y sont
explicitement désélectionnés, sans effacer leur code :

- test_session_copies::test_archived_reader_and_200_predictions_match_without_model
- test_evidence_bootstrap::test_s_preflight_stops_before_any_model_worker
- test_mps_reuse_campaign::test_overlay_changes_only_three_reviewed_files
- test_mps_reuse_campaign::test_archived_source_worker_runs_from_untracked_cwd_with_hash_only

Le cinquième est `slow` MiniLM réel, différé aux contrôles source hors sandbox.
Aucune exclusion pour un échec fonctionnel applicable au paquet installé.

## Empreintes finales

```text
740888408e84dace96fef81cde132b61216d365cd0e770e288e93d918668b9b5  memory_os_ai-4.0.3-py3-none-any.whl
0a7b5182742ffb1d197db7de7758d6583c4427c6349fcd2f00a678637d32a656  memory_os_ai-4.0.3.tar.gz
```

Artefacts hors bundle, déjà présents dans le clone partagé. Ne pas reconstruire
pour publier. Manifeste/garde : `.local-qualification/release-4.0.3-v2/manifest.json`.

## Claude : ordre exact hors sandbox

[Commandes complètes et procédure de retour arrière](../../v4/docs/QUALIFY_4_0_3.md).
D'abord source10 TCP +1 MiniLM réel +566 v3 cached réels, puis copies baseline4.0.2/
wheel4.0.3/sdist4.0.3 et reopens, profil natif attesté, doctor privé, revue des
classements changés éventuels, enfin deux relais installés. Tout refus est bloquant.
L'explication d'un écart de recherche nécessite un reçu Claude lié au SHA du rapport
copie ; aucun accord automatique. Défauts conservés auto/native/32/512, C4, JSONL.

Deuxième gros job seulement : pilote BEAM observé10 questions, **40 tentatives
CLI maximum**, Luna low lecteur / Astra high juge, via ChatGPT ; production-copy
receipt et profil exact exigés avant préparation. prepare/retrieve/answer séparés,
aucun relancement automatique ni score officiel/comparaison aux publications.
La disponibilité/flags/login ont été inspectés sans inférence. `features list`
affiche encore unified_exec=true malgré --disable : ce backend reste dérivé,
**shell_tool=false** est le verrou shell. Les événements du vrai pilote doivent
prouver absence d'outils ; les contrôles locaux ne prétendent pas l'avoir prouvée.

Garde closed jusqu'aux preuves sources/copie/relais. Pilot accuracy n'est pas une
condition de supériorité ni un rang. Romain conserve des GO distincts : publication,
bascule, toute migration SQLite, nouveau corpus/coût/campagne de confirmation.
Aucun changement de configurations utilisateurs déjà modifiées dans le clone livré.
