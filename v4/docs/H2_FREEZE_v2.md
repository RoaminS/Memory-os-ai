# H2 — gel v2 du manifeste `h2-lexical-parity-v1` (22/11/2026)

> **Remplacé par [H2_FREEZE_v3.md](H2_FREEZE_v3.md).** Gel v2 jugé NON VALIDÉ, jamais exécuté sur BEAM.

Pointeur vers le gel ; le protocole et les garanties sont dans [H2_RUN_v2.md](H2_RUN_v2.md), qui est
attesté par ce gel. Ce fichier-ci ne l'est pas (il contient l'empreinte du manifeste).
**Aucune récupération exécutée sur BEAM, aucun rappel calculé. Lancement soumis au verdict Codex.**

| Élément | Valeur |
|---|---|
| Commit source (`SOURCE`) | `7f788545f433b99c2cc9bc0fa3e1181c0847cd47` |
| Dossier du plan (`PLAN`, hors Git) | `/Users/romain/memory-os-local/codex_runs/h2-410-plan-v2` |
| SHA-256 de `manifest.json` (`SHA`) | `6181b17931a29af82f0ad179857e6bdb0f39ed4cb08a354edc534660ee8d0309` |
| SHA-256 de `plan.json` | `91d114f74a201156bd3b71859de9cd73327e5b33b71274729aee69b0eea527f8` |
| SHA-256 de `runtime.json` | `e2db10e7cec6868bdd0110b9677a73c00f4e978f084850ec8c0f680afa05abfa` |
| Comptages relus sur le corpus | 20 conversations, 5 732 messages, 400 questions, 355 scorées ; question la plus longue : 369 octets |

```bash
cd /Users/romain/memory-os-local/codex_runs/feature-410-h2
bash scripts/run_beam_h2.sh retrieve \
  --plan-dir /Users/romain/memory-os-local/codex_runs/h2-410-plan-v2 \
  --manifest-sha256 6181b17931a29af82f0ad179857e6bdb0f39ed4cb08a354edc534660ee8d0309
```

Une seule tentative ; toute reprise exige une revue d'incident (voir H2_RUN_v2).

## Preuves sur le runtime réel, sans donnée BEAM (4 conversations inventées, Granite chargé)

| Dossier sous `/Users/romain/memory-os-local/codex_runs/` | Résultat |
|---|---|
| `h2-410-selfcheck-v2` | complet, `"synthetic": true` ; MPS / `torch.bfloat16` ; 74 tenseurs (97 441 152 paramètres) égaux à `model.safetensors`, tokenizer égal à `tokenizer.json` ; 73 appels d'encodeur ; 3 512 fichiers de modules chargés, tous gelés ; bras lexical sans ML |
| `h2-410-selfcheck-v2-hostile-env` | même résultat avec `HF_HOME`, `HF_HUB_CACHE`, `TRANSFORMERS_CACHE`, `SENTENCE_TRANSFORMERS_HOME`, `XDG_CACHE_HOME`, `PYTHONSTARTUP` hérités vers des chemins étrangers : retirés, cache gelé imposé |
| `h2-410-selfcheck-v2-negative` (`negative-checks.log`) | worker refusé avant toute écriture dans cinq cas : cache HF redirigé, `TRANSFORMERS_CACHE` présent, entrée supplémentaire dans le chemin d'import, mode chemin sûr absent, autre empreinte de manifeste |
| `h2-410-selfcheck-v2-attempt1` | tentative de mise au point conservée : refus de trois modules torch construits en mémoire (`torch.ops`, `torch.classes`), corrigé par `7f78854` |

Le gel v1 (`h2-410-plan-v1`, manifeste `067fc008…47c059`, NON VALIDÉ) est conservé intact.
