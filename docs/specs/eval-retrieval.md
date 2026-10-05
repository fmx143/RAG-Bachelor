# Spec — évaluation de la récupération (delta sur l'existant)

Statut : proposition, aucun code. Base : `src/rag_bachelor/eval/` (`cli.py`, `dataset.py`, `metrics.py`), commande `rag-eval`, jeu `eval/questions.jsonl` (40 questions).

## 1. Ce qui existe et ne change pas

- Jeu JSONL versionné : `id, question, source, pages, status, type, note`. `type` ∈ `lexical | paraphrase | multi_page | hors_sujet` (`hors_sujet` = hors corpus, `pages` vide). Seules les questions `status = valide` sont évaluées.
- Appel de la vraie `retrieve()`, sans LLM ; garde-fou : refus de tourner sans `CHROMA_DIR`/`CHROMA_HOST` explicite (lecture seule, hors suite pytest par défaut).
- Granularité `(source, page)`, recall@{1,3,5,10}, MRR, détail par `type`, rapport JSON, `--compare`.
- Pas de dossier `eval/reports/` aujourd'hui : **aucun rapport existant à garder compatible**, le schéma peut évoluer librement.

## 2. Écarts à combler

### 2.1 hit@k (nouveau, en plus de recall@k)

- Définition : 1 si au moins une page attendue (même `source`) figure dans le top-k, sinon 0. Moyenne sur les questions non `hors_sujet`.
- Raison : `recall@k` est une fraction des pages attendues ; pour `multi_page` il ne dit pas « a-t-on trouvé de quoi répondre ». Les deux sont conservés ; clés du rapport : `hit@1/3/5/10`.
- Questions `multi_page` : `hit@k` est tolérant (une page suffit), `recall@k` est strict. L'écart entre les deux est lui-même un signal.

### 2.2 Score du top-1 et comparaison corpus / hors corpus

- Le score est celui de `retrieve()` : similarité cosinus `max(0, 1 − distance)` (`core/retriever.py`). Il doit être conservé au lieu d'être perdu dans `Hit = (source, page)`.
- Le rapport ajoute, par question : `top1_score` et `top1_correct` (le top-1 est-il sur une page attendue ; absent pour `hors_sujet`).
- Les questions `hors_sujet` ne sont **plus ignorées** : elles sortent des recall/hit/MRR (inchangé) mais entrent dans une section `score_top1` à trois groupes :
  1. `in_corpus_correct` : top-1 correct,
  2. `in_corpus_wrong` : top-1 sur une mauvaise page,
  3. `hors_sujet`.
  Par groupe : `n`, min, médiane, moyenne, max.
- Usage attendu : lire le recouvrement entre les groupes 1 et 3 pour décider si un seuil de pertinence est possible (audit B3). Cette spec **ne propose pas** de seuil : elle fournit la mesure.
- Limite à documenter dans la sortie : avec peu de questions `hors_sujet`, les statistiques sont indicatives ; afficher `n`.

### 2.3 Format `expected` multi-source : non retenu

- Le format actuel (`source` + `pages`, une seule source par question) couvre les 40 questions. Passer à `expected: [{source, pages}]` imposerait de migrer le jeu et le parseur sans besoin démontré.
- Déclencheur pour le faire : une question dont la réponse exige deux documents. Alors ajouter `expected` en champ optionnel, `source`/`pages` restant valides.
- `type` joue déjà le rôle de la « catégorie » demandée ; pas de second champ.

### 2.4 Comparaison entre deux exécutions

Étendre `--compare` (pas de script séparé : un seul point d'entrée, moins de code) pour afficher :
- l'écart sur **toutes** les métriques globales (y compris `hit@k`) ;
- l'écart **par `type`** ;
- les questions dont le résultat change : hit@1 passé de 1 à 0 (régressions) et de 0 à 1 (gains), avec `id` ;
- le nombre de questions communes ; la comparaison se fait **sur les `id` communs** et signale ceux présents d'un seul côté (jeu modifié entre deux runs) ;
- un message explicite si le rapport comparé n'a pas la même version de schéma ou k différent, au lieu d'un `KeyError`.

## 3. Schéma du rapport (version 2)

Champs existants conservés : `k`, `n`, `n_hors_sujet`, `metrics`, `by_type`, `per_question`. Ajouts :
- `schema` : `2`.
- `metrics` : + `hit@k`.
- `by_type[type]` : + `hit@k`.
- `per_question[]` : + `hit` (par k), `top1_score`, `top1_correct`.
- `score_top1` : les trois groupes de 2.2.
- `per_question` inclut aussi les `hors_sujet`, avec `top1_score` seul.

## 4. Tests (pytest, sans Chroma ni modèle)

Fonctions pures, testées sur des listes de hits construites à la main :
- `hit_at_k` : page attendue au rang 1, au rang k, au rang k+1, mauvaise source avec bonne page, `multi_page` avec une seule page trouvée (hit=1, recall<1).
- Statistiques `score_top1` : groupes vides, un seul élément, classement correct/faux/hors sujet.
- Diff de rapports : régression, gain, `id` en non-commun, schéma/k différents.
- L'exécution réelle (`rag-eval` sur un vrai index) reste **hors** de la suite par défaut, comme aujourd'hui.

## 5. Définition de « terminé »

- `rag-eval` sur un index réel produit un rapport schéma 2 contenant `hit@k`, `score_top1` (3 groupes, `n` affiché) et `by_type`.
- `rag-eval --compare` entre deux rapports liste régressions et gains par `id`.
- `pytest -q`, `ruff check src/ tests/` et `mypy src/` passent ; aucun test ne touche `data/`.

## 6. Hors périmètre

Seuil de pertinence, reranker, BM25, changement du chunking : cette spec ne fait que **mesurer**. Rédaction des questions : reste à ta charge ; le statut `brouillon` → `valide` reste manuel.
