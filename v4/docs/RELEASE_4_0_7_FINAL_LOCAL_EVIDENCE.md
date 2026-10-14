# Candidate4.0.7 à version finale — preuve locale v2

14 octobre 2026. Base80f56fd, branche release/4.0.7, travail dans le clone isolé
persistant sous codex_runs ; aucune écriture dans le clone principal.

- v3 complète en hash/fakes : **794 passed**, 0 erreurs/échecs/skips.
- v4 hors trois fichiers TCP et un test slow neuronal : **2089 passed**,
  1 deselected, 0 erreurs/échecs/skips ; isolation ok, configs inchangées.
- Contrôle packaging après maintien du chemin historique v4.0.7rc1.md :
  **40 passed** (déjà inclus dans v4, pas un nouveau total).
- Lint projet et v4/bench E9,F, git diff --check : verts.
- Sous v4/src, diff f9d3668→candidate : uniquement __version__4.0.7 ; doctor inchangé.
- Suite complète tentée : **refus avant tests**, swap non mesurable dans le
  sandbox ; aucun contournement. TCP/qualifications installées non acquises ici.
- Disque ≥3 Go mesuré pendant les suites, aucun modèle téléchargé/encodé,
  aucun appelLLM/API, aucune action production ou publication.

Preuves privées du clone isolé : `.local-qualification/407-final-full-codex-v2`
(refus), `407-final-unit-codex-v2` (ok), `407-final-packaging-codex-v2.log/.xml`.
La reprise Claude complète est dans [QUALIFY_4_0_7.md](QUALIFY_4_0_7.md) :
source31TCP+sémantique, builds/envs installés, coupeSQLite7→8 exhaustive,
non-vacuité/cache/reopen, doctor, S4 backup/drill, recollecteB et relais. Dossiers
v2 neufs, nouveaux hashes ; release_ready reste faux jusqu'à toutes ces preuves.

La version finale ne vaut pas GO de publier/déployer. Production4.0.5 maintenue.
Limite doctor rc/dev consignée séparément pour4.0.8.

Hookv5 :17 assertions originales passent ; H1–H3 acceptés. **Activation refusée**
par [REVIEW_HOOK_V5_CODEX_v1.md](REVIEW_HOOK_V5_CODEX_v1.md) : la deadline10s n'est
pas globale, aucune notice sur certaines annexes sautées ; ordre stale/fresh et
extrait L9 à corriger. Aucun fichier de Claude modifié. Les sorties indépendantes
sont sous `codex_runs/hook-v5-review/`, sans réseau/token/magasin production.
