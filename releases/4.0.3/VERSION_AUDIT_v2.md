# Requalification v2 — assertions et provenance de version

2026-10-10, Codex. La tentative v1 a échoué sur une assertion TCP, pas sur
la version annoncée par le serveur : 9 passed / 1 failed, `ok=false`.
Le dossier `.local-qualification/release-4.0.3/` est conservé intégralement.
La tentative v2 utilise `.local-qualification/release-4.0.3-v2/`, sans reprendre
les preuves v1. Aucun modèle/API/TCP/production exécuté par Codex.

Audit `rg -n '4\.0\.2' tests v4/tests scripts` :

| Occurrence | Décision |
|---|---|
| test_server_http_e2e, comparaison `/ready` et `/health` | Corrigée : version importée du paquet, pas de littéral |
| test_z_coverage_boost, version legacy Python | Corrigée : égalité des versions des deux paquets ; façade legacy alignée4.0.3 |
| package_smoke, paquets installés | Renforcé : les deux `__version__` doivent égaler la version4.0.3 du manifeste |
| test_beam, test_answer_pilot, test_mps_reuse_campaign, test_indexing_mps_campaign | Profil historique `beam-c4-4.0.2`, intentionnel, laissé intact |
| test_ops_doctor | Réponses synthétiques4.0.2 pour tests de validation ; aucune comparaison à la version courante |
| test_release_gate | Compatibilité et exemples négatifs de manifeste4.0.2, intentionnels |
| qualify_release_copy, run_answer_pilot.sh | Runtime baseline4.0.2 : installation de production actuelle, pas candidate |
| source_release_checks, verify_release_manifest, package_smoke | Ensembles de versions supportées, pas assertion figée sur le serveur courant |
| qualify_4_0_3_local, a6_adversarial_probe | Origine du pool ou commentaire historique, conservés |

Les scripts des deux relais installés reçoivent la version du manifeste ; les
tests TCP ops et le test MiniLM réel n'ont pas d'autre assertion4.0.2 courante.
25 tests readiness ASGI verts localement ; les 10 tests TCP réels restent à
rejouer par Claude, avec MiniLM cached et toute la suite v3 à encodeur réel.

Le protocole, modèles et caps du pilote ne changent pas. La source et les
empreintes changent : aucun plan ancien ni ancien artefact n'est réutilisable.
La nouvelle livraison expose `refs/heads/fix/v4-production-foundation` dans
le bundle ; publication et bascule restent interdites avant GO distinct.
