# H2 — gel v3 du manifeste `h2-lexical-parity-v1` (22/11/2026)

Pointeur vers le gel ; le protocole et les garanties sont dans [H2_RUN_v3.md](H2_RUN_v3.md), qui est
attesté par ce gel. Ce fichier-ci ne l'est pas (il contient l'empreinte du manifeste).
**Aucune récupération exécutée sur BEAM, aucun rappel calculé. Lancement soumis au verdict Codex.**

| Élément | Valeur |
|---|---|
| Commit source (`SOURCE`) | `60700d2881c51f377f5e707164b589e96220d417` |
| Dossier du plan (`PLAN`, hors Git) | `/Users/romain/memory-os-local/codex_runs/h2-410-plan-v3` |
| SHA-256 de `manifest.json` (`SHA`) | `60a957124df08838551f282b95cb8306d26944905dcd929c59111d5b139d331e` |
| SHA-256 de `plan.json` | `d86b1d59eb3bcb3d72682f3fb8115e944e02a727a5d431899382a9b03d6c70bf` |
| SHA-256 de `runtime.json` | `e2db10e7cec6868bdd0110b9677a73c00f4e978f084850ec8c0f680afa05abfa` (identique au gel v2 : même runtime) |
| Comptages relus sur le corpus | 20 conversations, 5 732 messages, 400 questions, 355 scorées |

```bash
cd /Users/romain/memory-os-local/codex_runs/feature-410-h2
bash scripts/run_beam_h2.sh retrieve \
  --plan-dir /Users/romain/memory-os-local/codex_runs/h2-410-plan-v3 \
  --manifest-sha256 60a957124df08838551f282b95cb8306d26944905dcd929c59111d5b139d331e
```

Une seule tentative, par le lanceur uniquement ; toute reprise exige une revue d'incident (H2_RUN_v3).

## Preuves sur le runtime réel, sans donnée BEAM (4 conversations inventées, Granite chargé)

| Dossier sous `/Users/romain/memory-os-local/codex_runs/` | Résultat |
|---|---|
| `h2-410-selfcheck-v3` | complet, `"synthetic": true` ; MPS / `torch.bfloat16`, 74 tenseurs égaux à l'instantané, tokenizer égal, 73 appels d'encodeur ; superviseur : préfixe de bytecode vide au départ et avant le score, 389 fichiers de modules tous gelés ; workers : préfixe vide au départ et à la fin, 780 (lexical) et 3 512 (sémantique) fichiers gelés, modules sans fichier tous dans la liste explicite ; bras lexical sans ML |
| `h2-410-selfcheck-v3-negative` (`negative-checks.log`, `negative.sh`) | A1-A3 : superviseur refusé avant toute écriture sans préfixe de bytecode, ou avec un préfixe non vide ; A4 : sur une copie temporaire des sources attestées, un `.pyc` forgé du **scoreur** est exécuté sans préfixe (rappel 1,0 pour un classement vide) et ne l'est pas avec un préfixe vide (rappel 0,0) ; B : avec la fonction réelle et l'enregistrement de runtime gelé, un module à origine relative (`foreign.py`), sans origine, un faux `torch.ops` et un fichier absolu étranger sont tous refusés |
| `h2-410-selfcheck-v3-attempt1` | tentative conservée : le contrôle du superviseur a invalidé la mesure pour quatre sous-modules natifs de `pyexpat` absents de la liste ; ajoutés explicitement par `60700d2` |
| `h2-410-selfcheck-v3-attempt2` | même commit que `h2-410-selfcheck-v3`, complet |

Les gels v1 (`h2-410-plan-v1`, `067fc008…47c059`) et v2 (`h2-410-plan-v2`, `6181b179…8d0309`), NON VALIDÉS, sont
conservés intacts.
