# Amendement pilote ChatGPT — avertissements de démarrage, v2

2026-10-10, Codex. Amendement figé avant tout NOUVEL appel. Le pilote v1 a déjà
produit une réponse lecteur ; aucune prétention de préenregistrement avant toute
observation. BEAM100K est observé, le pilote reste descriptif et non confirmatoire.

## Incident et politique de parsing

Le répertoire `.local-qualification/answer-pilot-production-v1/` est immuable.
Le premier CLI a terminé après deux `item.completed` de type `error`, avant
`turn.started`, puis une réponse structurée, puis `turn.completed`. Aucun outil.
Le harness v1 a refusé ces avertissements et laissé l'usage inconnu à tort.
L'amendement conserve cette erreur et le `stopped.json restart=false` originaux.

Le CLI conserve skip_host_skill_discovery pour empêcher l'injection de skills
host. `suppress_unstable_features_warning=true` est appliqué uniquement à cette
invocation, pas à la configuration utilisateur. Code mode est explicitement
désactivé via `features.code_mode.enabled=false`, host et shell également.
[Référence officielle](https://learn.chatgpt.com/docs/config-file/config-reference).
La commande de métadonnées valide ces champs sans inférence ; elle ne prouve
pas absence de warnings ni d'outils pendant le futur appel.

Filet de compatibilité : seulement les DEUX textes complets exacts dans
`codex_reader.STARTUP_WARNINGS`, événement `item.completed`, item de forme exacte
id/type/message, après thread.started et AVANT turn.started, chacun une seule
fois. Aucun regex/préfixe/substring. Message changé, warning tardif/dupliqué,
clé supplémentaire, error global, événement inconnu ou outil : arrêt.
Les tests prouvent que commande/MCP/web/file_change restent interdits même
avec un texte d'avertissement accepté. Cette règle n'ignore jamais un outil.

## Reprise des preuves, sans répétition de l'appel terminé

Nouveau dossier `.local-qualification/answer-pilot-warnings-v2/`. La préparation
exige exactement un arrêt d'isolation v1, une seule tentative, zéro jugement et
prédiction, source Git originale attestée, rapports/contextes/profil/checksums
originaux concordants. Seuls answer_pilot.py et codex_reader.py peuvent différer
de la source originale ; tous les fichiers moteur et récupération restent
identiques. Versions runtime, poids, CLI, modèles, efforts, prompts, sélection,
ordre, budgets et configuration sont comparés à l'identique.

La preuve de copie4.0.3 garde sa source53030c2. La source du harness v2 est un
nouveau commit ; cela ne requalifie ni ne reconstruit les artefacts4.0.3 déjà
qualifiés. Le rapprochement repose sur les hashes de TOUS les fichiers moteur
inchangés et le profil réel d'encodage, pas sur une supposition de compatibilité.

Les contextes récupérés en73s sont copiés byte à byte dans le nouveau dossier,
avec origine et SHA du rapport initial, zéro nouvel encodage. La première réponse
INSUFFICIENT_EVIDENCE est revalidée depuis le journal complet puis réutilisée
UNE fois pour la première question/bras figés. Aucun nouvel appel de ce lecteur.
Le prompt initial est reconstruit depuis source/plan/contextes attestés : son
tempfile original n'existe plus, donc pas de prétention à une attestation du
fichier stdin original. Les nouveaux appels persistent leurs prompts exacts.

`historical-usage.json` réconcilie l'ancien usage sans toucher aux anciens fichiers :
input9165, output21, cache0, reasoning0 = **9186 tokens connus**, une tentative.
Le premier juge puis19 lecteurs et19 juges = **39 nouveaux appels**, soit
20 réponses et20 jugements, **40 appels cumulés**. Un ledger exclusif hors v1
empêche de créer un deuxième budget amendé à partir du même pilote arrêté.
Les SHA de toutes les preuves historiques sont revérifiés avant chaque phase.

## Plafonds inchangés et preuves d'arrêt

Toujours10 questions, deux bras C4/BM25, lecteur gpt-5.6-luna low, juge
gpt-6-astra high via ChatGPT ; zéro API/clé/fallback. Le juge n'est pas GPT4o
des publications ; aucun score comparable, IC, supériorité ou classement.
MPS/native/batch32/512, same-arm BM25, données déjà observées, mêmes prompts.

40 tentatives cumulées,3600s CLI cumulées : faute de durée historique persistée,
90s sont réservées conservativement pour le premier lecteur (Claude a observé
5s), donc3510s maximum pour les39 nouveaux appels. Token-stop400000 inclut les
9186 anciens tokens ; ce seuil observé n'est pas un plafond de génération garanti.
Par appel90s lecteur/180s juge, prompt24kB, logs2Mo, nice15, concurrence1,
swap8.5Go/disque5Go. Tout quota/erreur/outils/format/délai arrête, aucune reprise.

L'usage validé de turn.completed est compté indépendamment de l'acceptation de
la réponse : erreur de schéma/isolation, exit non nul ou interruption de garde
ne suppriment pas une consommation connue. Coût incomplet/ambigu reste inconnu,
tentative toujours comptée. Accounting n'accepte jamais un résultat interdit.
Les rapports séparent appels historiques/nouveaux, durée réservée/durée mesurée,
usage connu/inconnu et succès/échec. Les refus sémantiques restent à relire.

Pas de nouvel extracteur/réglage/sélection après observation de la première
réponse. Ce GO reste celui du pilote, pas celui d'une campagne ou d'un déploiement.
