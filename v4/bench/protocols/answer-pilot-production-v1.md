# Pilote E2E descriptif préenregistré — production/native — v1

Date : 2026-10-10. GO Romain : pilote via quota ChatGPT, pas d'API. Ce texte et
`answer_pilot.prepare` doivent être figés avant toute nouvelle réponse/jugement.
Aucun résultat du pilote n'existe au moment de cette livraison.

## Population et séparation des informations

BEAM 100K déjà observé (SHA JSONL
`613b85b9e5eca98b88de25bedb7490f1dbf61ae4c361d544452feea19bfa18a3`).
**Jamais le futur held-out.** Deux conversations choisies par le hash existant
`beam-pilot-v1`; dix questions : une par catégorie, alternant ces conversations,
qid minimal dans la catégorie. Aucun score ni réponse n'intervient dans ce choix.
Cela minimise le quota tout en couvrant dix types d'usage ; aucune inférence
populationnelle ni IC avec deux conversations. Aucun retuning après résultat.

Extraction client simulée sans LLM : citations des seuls messages observables,
ordre/session/role/id/time_anchor explicite. Ni source_chat_ids, ni profil synthétique,
ni résumé guidé par question, ni réponses/rubriques futures dans l'ingestion.
Une conversation = un magasin isolé, encodeur résident. C4 approuvé contre BM25
sur EXACTEMENT les mêmes citations ; top_k=10, chunk=1024 caractères, contexte
≤12000 octets après packing. Les golds ne sont accessibles qu'au juge. Récence
non inférée de l'annotation/horloge, supersession confinée à 1.01 : déviations
retrieval documentées, pas prétention à un test temporel complet de production.

## Modèles et attestation

Lecteur `gpt-5.6-luna`, effort **low**. Juge `gpt-6-astra`, effort **high**, choisi
avant résultat : modèle phare pour raisonnement complexe du catalogue opérateur,
confirmé par les métadonnées locales. Pas de fallback de modèle/effort.
Ce juge **n'est pas le snapshot GPT-4o des articles** ; exactitude descriptive,
non comparable aux chiffres publiés. Le slug est figé, les poids distants ne
sont pas attestables via exec. L'empreinte/version du binaire est figée.

Encodeur Granite 97m déjà en cache (snapshot 835ad140…), fichiers hachés,
**auto→MPS exigé, native, batch32, max_seq512**, batch_facts=false. Runtime local
figé ; dtypes/devices des paramètres réellement chargés, révision et empreinte
encodeur consignés **avant capture et avant toute réponse**. CPU de repli interdit.
Les versions de dépendances doivent correspondre au runtime installé de production
et au dossier de qualification, pas au Python système Torch 2.11.
Watermarks MPS 0.4/0.3, threads1 (garde machine), contexte privé seulement.

Chaque appel est un nouveau `codex exec` éphémère depuis un cwd temporaire sans
instructions/configuration du projet. ChatGPT existant obligatoire, provider OpenAI,
forced_login_method=chatgpt, variables de clés API retirées ; aucun jeton OAuth lu
ou réutilisé ailleurs. MCP/web/outils/shell/plugins/hooks/skills/mémoire désactivés.
Tout événement d'outil/inconnu est bloquant. Schémas JSON stricts, prompts complets
persistés dans plan.json ; juge anonyme sans nom de bras. Ordre des bras alterné
par question. Les deux lecteurs reçoivent même question et budget de contexte.

Références officielles consultées : [exec/JSON/schema](https://learn.chatgpt.com/docs/non-interactive-mode),
[authentification](https://learn.chatgpt.com/docs/auth),
[GPT-6 Astra](https://developers.openai.com/api/docs/models/gpt-6-astra).
Aucune utilisation de l'API ni tarif API appliqué à ces appels.

## Plafonds, arrêts et preuves

10 questions ×2 bras ×(1 lecteur +1 juge) = **40 tentatives CLI maximum**,
concurrence1, nice15 ; récupération ≤1200 s, phase CLI ≤3600 s, appel lecteur
≤90 s, juge ≤180 s ; prompt ≤24000 octets, sorties/logs ≤2 Mo/appel.
Seuil d'arrêt observé 400000 tokens (input+output, cached inclus dans input),
**pas un plafond de génération garanti** : le dernier appel peut franchir le seuil.
Durée totale et tentatives sont des plafonds fermes. Pas de téléchargement.
Swap >8.5 Go ou disque <5 Go, mesure indisponible, erreur/quota/format/outil/délai :
arrêt immédiat, preuves partielles conservées, **aucune reprise automatique**.
Le CLI peut gérer son transport interne ; une tentative CLI n'est pas une preuve
qu'il n'a effectué qu'une requête réseau. Aucun relancement de tentative par harness.

API incrémentale : $0 ; aucun achat/recharge commandé par le programme. Claude
vérifie le quota inclus sans achat automatique côté compte avant `answer` : le
CLI n'atteste ni facturation du compte ni quota restant. Les tokens sont rapportés,
pas convertis en pourcentage de quota. Erreur de limite = arrêt, jamais API de repli.

Faisabilité : exactement20 réponses +20 jugements structurés, aucune erreur ni
outil/quota-stop. Les refus/abstentions et réponses doivent être relus dans les
journaux : l'absence de violation technique n'atteste pas absence de refus sémantique.
Score primaire descriptif = fraction `correct=true` par bras selon rubriques
anonymes ; pas d'EM mécanique comme exactitude officielle. Durée/appel, tokens
input/output/cache, tentatives incomplètes/quota inconnu, refus éventuels et delta
sont consignés. Échantillon trop petit pour choisir un gagnant ou revendiquer top1.
Le rapport de faisabilité technique et la revue manuelle des refus sont distincts.
