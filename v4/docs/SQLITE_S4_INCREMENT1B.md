# SQLite S4 incrément1b — correction qualifiée localement

2026-10-11. Base `0befeb3`. La lecture de l'identité utilisait à tort la clé
`meta` ; `SQLiteStore.save_meta` stocke `metadata`. Les deux lectures (pin et
inventaire) sont corrigées. Régression sur un vrai SQLiteStore/MemoryV4 jetable,
encodeur Hash : identité lue par inventory égale à celle du moteur.

Preuves Codex : `s4-unit-codex-1b-v1.xml` — **39 passed** ;
`sqlite-s4-increment1b-codex-unit-v1/report.json` — **52 passed**, ok=true,
tcp_qualified=false. Les38 tests S4 précédents passent avec cette correction.
Le rapport Claude précédent 12 failed/40 passed reste conservé ; ce n'était pas
une erreur d'intégrité SQLite structurelle. Aucun ancien rapport n'est réécrit.

Politique amendée à la demande de Claude : les petites unités synthétiques Hash
bornées peuvent tourner dans le sandbox sans attestation swap. `--tcp`, campagnes,
CLI backup/drill réels gardent la garde disque/swap réelle et fail-closed.
Le lanceur conserve wall300 s ; fixtures S4≤16 MiB/cas, nettoyage après réussite.
La qualification globale reste séquentielle/hors sandbox et comparaison des
configs inchangées. Aucun test pendant modification concurrente du même arbre.

Après import, commandes (nouveaux dossiers exclusivement) :

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:v4/src:v4 \
 .local-qualification/brief-test-runtime-claude-3c-v1/bin/python -B \
 scripts/qualify_sqlite_s4_increment1.py --tcp \
 --output .local-qualification/sqlite-s4-increment1b-claude-v1
```

53 cas attendus dont B09 ; pas d'activation S4/SQLite impliquée par leur réussite.
Cette livraison corrige l'incrément1, elle ne fournit pas encore les gates de
réouverture, de réconciliation ni la matrice crash complète.
