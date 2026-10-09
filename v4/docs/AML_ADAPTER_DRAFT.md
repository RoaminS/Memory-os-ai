# T25 — adaptateur Add/Search local, statut DRAFT

2026-10-09. **Pas de conformité officielle attestée et aucune soumission.** La
consigne de Romain interdit le réseau à Codex pendant ce lot. Aucun règlement/
SDK AML actuel n'a été trouvé dans les documents locaux inspectés ; le texte
historique de l'accord ne suffit pas à certifier les règles actuelles. Le nom de
leaderboard, URL, version/commit du harness, échéance, endpoints et budgets
officiels restent à confirmer à partir d'une copie locale des règles. Ne pas
inventer une API HTTP à partir du seul intitulé « Add/Search ».

Livré : `bench.e2e.leaderboard.AddSearchAdapter`, réutilisé par le vrai pipeline
MCP stdio. `request()` fournit un **contrat JSON local v1** testé, pour faciliter
l'adaptation finale du harness :

```json
{"op":"Add","user_id":"alice","messages":[{"id":"s1:0","session":"s1","role":"user","content":"J'ai adopté un chat.","date":"2026-10-09T10:00:00+02:00"}]}
```

```json
{"op":"Search","user_id":"alice","query":"Quel animal ai-je adopté ?","limit":10}
```

Add répond `tool_calls,added,deduped` ; Search répond
`results:[{id,text,score}],tool_calls`. Injections de `answer`, `has_answer`, faits
préextraits ou champs inconnus refusées. Le texte brut demeure une observation,
pas une instruction. Identifiants de source uniques et dates avec fuseau exigés.
Un namespace explicite par campagne est obligatoire, projets opaques par utilisateur
(même `user_id="*"` ne sélectionne pas la portée globale). Idempotence par empreinte
du payload de capture ; aucune répétition automatique après résultat incertain.

L'adaptateur exige un `ToolClient` injecté, avec magasin privé ; il n'expose aucun
port, n'a aucun client réseau de soumission ni identifiant AML. Les tests de
contrat utilisent un faux client ; l'exécution MCP réelle est une preuve séparée
à obtenir hors sandbox avec `scripts/run_eval_lot.sh`.

Avant de considérer T25 terminé : fournir la copie locale et l'empreinte des
règles, relire le harness à une révision précise, documenter correspondances et
écarts avec ce contrat, exécuter ses tests de conformité localement, qualifier
budgets/temps et génération/jugement requis. **Une interface locale Add/Search
verte ne prouve ni l'éligibilité ni un classement.** Une exposition/soumission
publique, même demandée plus tard, reste une action distincte à autoriser.
