# 4.0.7rc1 — preuves locales Codex et reste à qualifier

14 octobre 2026. Travail dans un clone isolé persistant, base 8e2c03d,
ref livrée `release/4.0.7`. L'arbre principal et la production restent intacts.

| Contrôle | Résultat |
|---|---|
| Suite v3 complète, hash/fakes | **794 passed**, 0 échec/skip |
| Suite v4 hors les trois fichiers TCP et un test slow neuronal | **2089 passed**, 1 deselected, 0 échec/skip |
| Harnais isolation `--suite unit` sur la source finale | ok=true, client_configs_unchanged=true, tcp_qualified=false |
| Lint projet + v4/bench E9,F ; git diff --check | Réussis |
| Diff moteur par rapport à f9d3668 | Une ligne __version__, aucun autre changement sous v4/src |
| README | Liens HTTPS conservés par sanitizer ; décodage des deux configs cohérent ; renderer Markdown complet indisponible localement |
| Hook Claude non modifié | 18 assertions passent ; sondes privées de pagination réelle/dédup révèlent H1/H2 ; activation NON VALIDÉE, H3 à corriger |

Une tentative complète a échoué sur la permission de bind TCP du sandbox
(29 erreurs et 2 skips). Son unique échec hors TCP — alignement des versions du
paquet legacy et du pyproject standalone — a été corrigé avant les suites vertes.
Ces preuves refusées restent conservées. Les 31 TCP sont **à exécuter**, jamais
comptés comme qualifiés ici. Les nouvelles vérifications de release ont 14 cas
modèle-free, inclus dans les 794 tests v3, notamment un vrai format de report
avec durées distinctes et une installation simulée du sdist (sans substituer le wheel).

La construction lourde n'est pas lancée : `vm.swapusage` est interdit dans ce
sandbox, le guard ne peut pas attester le swap. Aucun contournement ni nouveau
modèle/API/inférence. Disque mesuré supérieur au seuil3 Go pendant les suites.

Les logs/XML/report sont dans `.local-qualification/407rc-unit-codex-v1` et
`.local-qualification/rc1-local` du clone isolé (preuves privées, non embarquées).
La construction des vrais wheel/sdist, le manifeste SHA256, 31 TCP, test cached
sémantique, copie installée7→8, HTTP/doctor, S4 backup/drill, relais et report du
gate B RC sont obligatoires avant release_ready. Voir
[QUALIFY_4_0_7_RC1.md](QUALIFY_4_0_7_RC1.md).

Le JSON template est volontairement incomplet et **non qualifiant**. Le générateur
produit le vrai manifeste seulement après les artefacts qualifiés. Aucun hash
d'artefact inventé, aucune publication ni bascule autorisée par cette livraison.
