# 4.0.4 — incrément 1 : livraison Codex à Claude

Date : 2026-10-11. Branche nommée : **`feat/404-increment-1`**.
Base de travail : `4232a75` (campagne Luna déjà livrée, toujours en pause).
Le bundle inclut aussi les deux commits Luna antérieurs depuis `92e09a8`, pour
ne pas exiger leur import préalable ; ils ne sont ni modifiés ni exécutés ici.

## Contenu et limites

| Commit | Livraison |
| --- | --- |
| `907d274` | L2 : verrou fermé avant reçus/transactions, simulation par défaut, policy figée, instructions corrigées, tests JSONL/SQLite. |
| `e54905c` | Contrats L3/L4 : schémas et matrice C01–C12, pas de nouvel endpoint ni journal du relais ; fixture d'archive historique adaptée au nouveau champ Config sans toucher l'archive. |
| `55f4d54` | L13a : agrégats bornés opt-in en RAM, observation sans contenu, scripts de qualification hash-only, deux nouveaux tests TCP L2/L13a. |

La signature de Claude et Q0 sont intégrés dans `PLAN_COMMUN_v4.md`. Le plan
final n'est pas une qualification de release. Aucun wheel/sdist construit,
aucune installation/runtime/production modifié. Le numéro de package reste
**4.0.3** dans cet incrément de développement vers 4.0.4 : ne pas distribuer ces
sources sous un artefact 4.0.3. Le passage cohérent des versions et la fabrication
de 4.0.4 appartiennent à la qualification complète, après les incréments suivants.

`memory_request_status` et le journal client L3 ne sont **pas encore présents**.
Les 30 cycles de reconnexion L4 ne sont **pas encore qualifiés**. Les capacités
L5, le brief L1a et l'identité L7a ne sont pas inclus. C4, encodage et activation
SQLite restent inchangés. Aucune écriture de maintenance sur la production.

## Preuves locales

`evidence/404-increment1/manifest.json` lie sources, scripts et preuves à
`55f4d54`. Suite v4 légère : **1188 passed**, **0 skipped**, un test neuronal
`slow` désélectionné ; les fichiers TCP sont explicitement exclus de cette suite.
Les deux commandes lint passent. Les tests ciblés avaient donné **98 passed**.

Un premier passage complet a échoué sur une fixture de campagne archivée :
`resolved_config()` incluait désormais `maintenance_mode`, absent de l'ancien
moteur CPU figé. La fixture retire ce champ comme les autres champs plus récents.
L'archive et les campagnes n'ont pas été changées. Le journal initial est
conservé ; la nouvelle exécution complète est dans un dossier neuf.

Baseline synthétique de vingt épisodes/hash : cinq briefs, cinq recherches,
un refus, zéro panne d'observation. Maximum de contenu brief retourné :
**10 326 octets** pour une demande de 1 000 tokens ; seuls 100 octets de texte
rendu par brief dans cette fixture. Ce constat mesure le problème actuel,
**pas un succès du futur budget L1a**. Durées hash non représentatives de Granite,
tokens non mesurés, octets hors enveloppe réseau MCP. Les métriques désactivées
par défaut ne déposent aucun journal de payload dans le magasin.

## Commandes exactes hors sandbox

Choisir un **checkout propre** de la branche livrée ; ne pas forcer une mise à
jour de fichiers modifiés. Option reproductible dans le périmètre Claude, sans
toucher les sources/installations en production et sans réseau :

```sh
task_src=/Users/romain/Documents/Codex/2026-10-09/v/work/implementation
task_review=/Users/romain/memory-os-local/qualification/404-increment1
git clone --shared --no-checkout "$task_src" "$task_review"
git -C "$task_review" fetch "$task_src/404-increment1.bundle" \
  refs/heads/feat/404-increment-1:refs/heads/feat/404-increment-1
git -C "$task_review" switch feat/404-increment-1
cd "$task_review"
PYTHONDONTWRITEBYTECODE=1 /Users/romain/Memory-os-ai/.venv/bin/python \
  scripts/qualify_404_increment1.py --suite full \
  --output .local-qualification/404-increment1-claude-v1
```

Le clone de qualification et le dossier de sortie doivent être neufs. Le clone
partagé dépend des objets Git de sa source : garder celle-ci intacte pendant les
tests. Le script s'arrête au premier échec, conserve ses preuves et refuse les
tests sélectionnés ignorés. Attendus : **1200 tests réussis** si la même sélection
est collectée (1188 locaux + 12 TCP), lint vert, baseline hash complète ; contrôler
le XML et le rapport plutôt que le nombre seul. Aucun modèle téléchargé/chargé.

Pour isoler uniquement les tests TCP après la revue, dans un autre dossier neuf :

```sh
PYTHONDONTWRITEBYTECODE=1 /Users/romain/Memory-os-ai/.venv/bin/python \
  scripts/qualify_404_increment1.py --suite tcp \
  --output .local-qualification/404-increment1-tcp-claude-v1
```

Ces 12 tests se trouvent dans `test_server_http_e2e.py` et `test_ops_e2e.py` :
deux nouveaux cas de maintenance/mesures ; partage à deux clients ; retry avec
ID ; quatre refus HTTP/auth/Host/Origin ; identité et authentification `/ready` ;
relais avec jeton ; restart sans replay. Ce ne sont pas encore les futurs tests
L3/L4 C01–C12 ni une preuve de qualification d'artefacts installés 4.0.4.

## Travail parallèle et revue demandés

1. **L2 :** aligner AGENTS.md et copies clientes, supprimer les automatismes
   « weekly / >2000 ». Ne pas activer `preview` ou `apply` en production.
2. **L3/L4 :** accepter ou amender `CONTRACTS_L3_L4_v1.md` et ses schémas avant
   implémentation : ID magasin/backup ancien, hash canonique, absence de reçu,
   journal fsyncé avant envoi, changement d'identité en reconnexion.
3. **Q0 :** soumettre le hook. Texte seul et erreur explicite conviennent ;
   des blocs vides n'impliquent pas un brief vide. Le fallback global doit être
   explicitement autorisé, borné et identifié, jamais mélanger silencieusement
   les projets ni exposer des sources hors portée. Le défaut sûr reste un
   diagnostic d'identité, pas une recherche globale automatique.
4. **L11 :** figer rubric, scénarios et dénominateurs avant les premières reprises ;
   distinguer mémoire externe, résumé natif et fichiers, et compter les échecs.
5. Rejouer les tests physiques ci-dessus et rapporter les journaux/manifeste.
   L14 reste en pause. Ni bascule, ni PyPI, ni GitHub dans cet incrément.
