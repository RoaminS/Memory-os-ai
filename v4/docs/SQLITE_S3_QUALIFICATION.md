# Qualification locale P1/P2/P3 et S3 — 2026-10-09

Base : `61eda7b280aa29125c4ec3c95ee86c23f28c9e4a`.
Code qualifié : `193a0ae9ca2443884be546ceacc1ff9bb2c89e99`.
Python **3.11.14**, SQLite **3.53.2** (minimum 3.51.3 satisfait).
Checkout : `store-s2-work`, metadata Git séparées ; aucune écriture dans le
magasin de production ou dans le dépôt original. Aucune opération GitHub.

| Lot / commit | Preuve |
| --- | --- |
| P1 `f51d580` | Trois tests cache : vecteurs/chunks identiques, zéro encodage sur redémarrage/rollback chaud, corruption locale recalculée seule, namespace invalidant le profil, cache du lot annulé absent. Suite SQLite à ce stade : **34 passed**. |
| P2 `03ede73` | Implicites bornés sans result, explicites rejouables et immuables, rétention/counter rollback, taille doctor/health, erreur DB sans traceback. Suites ops/rétention : **78 passed** après correction d'un test qui supposait basetemp hors dépôt. La correction utilise GIT_CEILING_DIRECTORIES pour tester réellement un dossier sans dépôt. |
| P3 `7dc8589` | Version dynamique vérifiée sur JSONL et SQLite ; suite rétention/version **5 passed**. |
| S3 `6e2a9cc` | Import/export hors ligne, source read-only, byte exact avant nouvelles écritures, export de réconciliation après, pending/incertitudes conservés, inconnus préservés, publication exclusive atomique ; lanceurs de tests et mesure. |
| JSON strict `193a0ae` | Un nombre exponentiel `1e999` était accepté comme Inf par json.loads sans parse_float strict ; désormais refusé avant import. Régression sur metadata imbriquée. |

## Vérification finale

Commande exacte depuis ce checkout :

```sh
./scripts/run_store_checks.sh
```

Résultat : **329 passed**, aucun SKIP/échec, trois avertissements SWIG/faiss,
**41,86 s**. Deux commandes de lint CI : **All checks passed**. `git diff --check`
sans défaut. Log livré : `store-s3-v1-evidence/pytest-lint.log`.

Matrices :

- S2 amendé : **54 exceptions + 54 SIGKILL aux frontières + un SIGKILL après
  acquittement fsyncé** ; aucun acquittement perdu, aucun succès partiel. Le reçu
  reste la dernière écriture de la transaction, malgré cache/rétention/counter.
- S3 : **58 exceptions** aux frontières de l'import et de la publication ; source
  inchangée, destination absente avant publication ou complète après publication.
  **Quatre SIGKILL** : avant/après COMMIT, avant/après publication ; reprise
  reconnaissant les stages, sans doublon ni écrasement de destination.
- Recherche à trois dates, timeline et brief comparés avec le backend JSONL sur
  une copie séparée. Horloge fixe ; seuls chemin de déploiement et compteurs
  spécifiques SQLite sont exclus du brief. Tous les fichiers source originaux
  sont comparés intégralement après chaque scénario.

Ces nombres d'injections sont les itérations de tests pytest, pas autant de tests
pytest distincts. Elles prouvent des pannes de processus/exceptions simulées,
pas une panne électrique ou un disque réellement plein.

## Mesure P1 encore ouverte

Essai de préflight en sandbox : arrêt **avant tout magasin**, `complete=false`,
`results=[]`, motif `Cannot enforce nice >= 15; workload refused`. La sonde
`sysctl vm.swapusage` est également interdite. Aucun résultat 1 000/10 000 n'est
donc qualifié. Le smoke d'ingénierie porte uniquement sur **quatre** éléments
(encodage froid=4, chaud et rollback=0) ; il ne remplace pas la campagne.

Claude doit exécuter, dans un checkout isolé et hors sandbox :

```sh
./scripts/run_store_lifecycle.sh --output .local-qualification/lifecycle-claude-v1
```

Exiger `complete=true`, résultats pour 1 000 **et** 10 000, trois répétitions
chaudes, zéro encodage de corpus chaud. Gardes physiques inchangées, nice≥15,
exécutions séquentielles. À nouveau interrompu par une garde : la mesure reste
incomplète. [SQLITE_S3](SQLITE_S3.md) contient protocole, formule Granite et
exemples explicitement hypothétiques ; aucun Granite n'a été chargé/mesuré.

## Revue / activation

Relire la clé/profil du cache et la politique de changement de poids, l'absence
de cache de requêtes, les triggers de rétention et la dernière écriture reçu,
les pendings legacy, la conservation des unknowns, la comparaison exhaustive,
les leases read-only et l'usage immutable conditionné à l'absence de WAL,
le rename exclusif et le refus de retour perdant les nouvelles écritures.

Contrôle du checkout principal :
`git --no-optional-locks diff --exit-code b1795a9 -- v4/src v4/bench scripts`
⇒ **0**. Ne pas intégrer dans ce checkout tant que run-dev tourne.

S3 est qualifié sur petites copies synthétiques, avec limite d'entrée 128 MiB ;
aucune donnée/trace réelle de production n'a été migrée. Linux renameat2,
gros magasins, délais Granite, S4 backup/restore/purge/pannes physiques et S5
industriel restent ouverts. Il n'y a aucun nouveau GO de production dans ce lot.
