# Qualification 4.0.7 — incrément B2, 13 octobre 2026

Base ad51076. Amendement a57d169 précède code ca5c970. Version 4.0.6.dev0.
B3 déjà livré ; B2 candidate ; B4/B5 contrats proposés et B9 restant.
Aucun brief, profil d'encodage, cache-key ou schéma SQLite changé.
Le champ audit facultatif est conservé dans l'historique logique existant ;
absent des anciens enregistrements non correctifs. Production intacte.

## Preuves locales

- 34 cas B2 (CAS, fast unique, impacts multiples/implicites, aperçu, legacy,
  JSONL, refus/date, chat, provenance, receipt replay, cold model-free) ;
- ces cas + 14 suites de régression : **320 passed**, 29,91 s,
  .local-qualification/b2-final-v2.{log,xml} dans le worktree persistant ;
- 30 tests TCP collectés seulement, **non exécutés** en sandbox ; manifeste
  exact de qualification mis à jour (28 existants + 2 B2) ;
- Ruff check : scopes projet et E9,F verts ; git diff --check vert ;
- catalogue exact cl100k : ad51076=10036 tokens/43651 octets,
  candidat=10652/46153 ; **23 outils**, +6,14 % ;
  depuis d003e54 (10010 tokens) : +6,41 %, sous le seuil cumulé +10 %.

Encodeur hash sur magasins privés uniquement ; 0 neural/API/téléchargement.
La qualification indépendante P-B n'a pas été exécutée et n'est pas revendiquée.
Les premiers échecs de développement (audit aléatoire, shape replay, fixture
cold) restent conservés, puis corrigés avant les preuves vertes.

## Claude : import puis séquence hors sandbox

Importer la référence nommée release/4.0.7 au-dessus de ad51076, arbre propre,
sans édition concurrente. Les dossiers sont NOUVEAUX ; aucun écrasement de preuve.
Le bundle contient l'amendement, le code B2 et les documents, pas les stores.

```sh
PYTHON="$HOME/memory-os-local/qual-envs/release406-installed-2zfywl93/wheel/bin/python"
export PYTHONDONTWRITEBYTECODE=1 MEMORY_V4_MIN_FREE_BYTES=3000000000
export MEMORY_V4_CL100K_PATH="$HOME/.config/memory-os/cl100k_base.tiktoken"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 MEMORY_V4_EMBED_MODEL=hash

PYTHONPATH=src:v4/src:v4 "$PYTHON" -B -m pytest -q -p no:cacheprovider \
  v4/tests/test_integrity_v5_b2.py \
  --basetemp=.local-qualification/407-integrity-b2-targeted-claude-v1 \
  --junitxml=.local-qualification/407-integrity-b2-targeted-claude-v1.xml

"$PYTHON" -B scripts/qualify_405_test_isolation.py --suite full \
  --output .local-qualification/407-integrity-b2-full-claude-v1
```

Le full inclut les deux nouveaux TCP :
test_B2_fast_correction_revision_and_relay_clientinfo et
test_B2_relay_preview_is_read_only_and_has_no_receipt, ainsi que tous les
TCP antérieurs. Échec = arrêter, conserver le dossier, pas de tentative de
validation P-B sur un arbre non qualifié. Aucun accès d'écriture production
n'est nécessaire pour ces tests privés.

## Relecture et gel

1. Contre-amendement legacy >3 à confirmer ; A/C/D acceptés. Contrat B2 v2
   remplace v1, l'avertissement correction_unverified prépare le B1 client.
2. Lire INTEGRITY_V5_B4_B5_CONTRACTS_v1 avant leur implementation.
3. Format INTEGRITY_V5_P_B_CASE_FORMAT_v1 : transmettre inputs/manifest seulement,
   garder gold et scorer séparés. Signaler toute incompatibilité avant collecte.
   Seal des 40 cas avant gel du candidat COMPLET ; cet incrément est du dev.
4. Aucune activation, release, nouvelle migration ou annonce publique implicite.

