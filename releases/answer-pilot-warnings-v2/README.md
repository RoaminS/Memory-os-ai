# Pilote réponses — v2 prêt, sans nouvel appel LLM

Codex,2026-10-10. [Reçu/empreintes](qualification.json),
[amendement figé](../../v4/bench/protocols/answer-pilot-production-v2.md).
Source harness6fc00c99333a4414c06e71add98523d8ae8d9813 ; source des artefacts
4.0.3 qualifiés53030c2, artefacts/empreintes inchangés. Aucun GO publication/bascule.

Le plan est **déjà préparé** dans le clone partagé, SHA-256 :
`3e81a3c09926ff6acdc749b334be05e858f8aafcb4d01bb8ad89cb295d5529bb`.
Les contextes/profil de récupération ont été réutilisés avec hashes et source
inchangés. Aucune récupération neuronale ni appel lecteur/juge lancé par Codex.
Luna low/Astra high,10 questions, mêmes prompts/bras/caps. Première réponse
historique revalidée : une tentative,9186 tokens ;39 appels futurs maximum,
40 cumulés. Pas de score officiel/confirmation ni comparaison GPT4o publié.

68 tests ciblés,1063 v4 légers,566 v3 synthétiques et lint verts. Les véritables
tests modèle/TCP de la release4.0.3 ont été qualifiés par Claude, pas rejoués par
Codex pour ce changement de banc. La commande de métadonnées CLI accepte les
settings : code_mode=false,code_mode_host=false,shell_tool=false ; suppression
de warning [officiellement documentée](https://learn.chatgpt.com/docs/config-file/config-reference).
Elle ne prouve pas encore absence de warnings pendant le prochain modèle.
Deux warnings exacts avant turn.started sont seuls autorisés ; tout outil,
autre erreur/événement ou warning tardif/dupliqué reste bloquant.

Claude : après import du bundle nommé, **ne pas refaire prepare ou retrieve**.
Vérifier le quota ChatGPT inclus sans achat/recharge ; exécuter hors sandbox :

```sh
cd /Users/romain/Documents/Codex/2026-10-09/v/work/implementation
./scripts/run_answer_pilot.sh answer \
  --plan-dir .local-qualification/answer-pilot-warnings-v2
```

Le script vérifie encore les empreintes de la source, données, poids, CLI,
runtime, copie, contextes et TOUTES les preuves v1 ; ledger exclusif,39 nouveaux
appels,3510s nouvelles +90s réservées pour le premier appel, token-stop400000
incluant les9186 historiques, nice15, swap8.5Go/disque5Go. Le warning nice du
sandbox pendant la préparation concernait exclusivement des métadonnées,
zéro modèle/LLM ; la priorité sera exigée avant answer hors sandbox.

Arrêt au premier refus, aucune reprise automatique/renommage/réinitialisation.
Ne pas appeler answer une deuxième fois, même si l'arrêt paraît technique.
Les anciens stopped.json/usage.json restent intacts ; historical-usage.json
réconcilie leur consommation inconnue à tort. L'usage de tout futur appel
terminé est compté même si sa réponse est refusée ; consommation partielle
non attestable reste explicitement inconnue. Publier les résultats descriptifs
et limites seulement après revue des réponses/refus par Claude.
