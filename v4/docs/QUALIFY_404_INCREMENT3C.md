# Increment 3c : runtime complet et qualification exacte de 3b/3c

Correction du transport des dépendances : `tiktoken_ext` ajouté au runtime,
validation avant création, tests d'import dans un interpréteur vierge et de refus
quand l'extension manque. Aucun changement aux 100 scénarios scellés, aux budgets,
à C4, ni au profil d'encodage. Les échecs Claude v1/v2/exact-v1 sont conservés.

Table cl100k déjà téléchargée avec le GO de Romain ; ne pas la télécharger à
nouveau. Les commandes ci-dessous sont pour Claude **hors sandbox**, séquentielles,
sans modèle, sans service live. Exécuter chaque étape seulement si la précédente
passe ; sorties neuves, aucun effacement/reprise de preuves échouées.

```sh
cd /Users/romain/Documents/Codex/2026-10-09/v/work/implementation
/opt/homebrew/bin/python3.11 scripts/make_brief_test_runtime.py \
  --output .local-qualification/brief-test-runtime-claude-3c-v1
brief_python="$PWD/.local-qualification/brief-test-runtime-claude-3c-v1/bin/python"
export MEMORY_V4_CL100K_PATH="$PWD/.local-qualification/cl100k/cl100k_base.tiktoken"
export PYTHONDONTWRITEBYTECODE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
unset MEMORY_V4_PROJECT_REGISTRY
"$brief_python" -B -c 'import tiktoken, tiktoken_ext; print("runtime import OK")'
"$brief_python" scripts/qualify_404_increment3c.py --suite full \
  --output .local-qualification/404-increment3c-claude-v1
"$brief_python" scripts/qualify_brief_l1a.py \
  --output .local-qualification/404-increment3c-exact-claude-v1
"$brief_python" scripts/verify_404_increment3b.py \
  --source-report .local-qualification/404-increment3c-claude-v1/report.json \
  --brief-report .local-qualification/404-increment3c-exact-claude-v1/report.json
```

Attendu : au moins1510 tests dont20 TCP, zéro échec/skip, lint vert,
`l1a_transport_qualified=true` ; 400 mesures exactes
(100 FR/EN ×2 backends ×2 chemins) ; table officielle SHA
`223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7`.
Le verdict combiné valide l'incrément, jamais une publication ou bascule.
Les gardes imposent disque≥5GB/swap≤8.5GiB. Mesure swap indisponible : la
qualification exacte refuse ; aucun contournement de cette garde n'est livré.

La candidate4.0.4 inclut ce correctif et possède ses propres artefacts/manifeste.
Enchaîner uniquement selon `QUALIFY_4_0_4.md` après succès des preuves ci-dessus.
