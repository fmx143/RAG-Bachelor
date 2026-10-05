# Audit de vérification — plan d'amélioration RAG-Bachelor

Audit en lecture seule, commit `4dcb2c53b5d46273c24af28acfc0020b0e9a315a`. Aucune modification de `src/`, `tests/`, `data/`.
Scripts jetables et copies dans le scratchpad de session ; checksums de `data/app.db`, `app.db-wal`, `chroma.sqlite3` identiques avant/après la baseline (`md5sum -c` OK).

**Limites de l'audit.** Les commandes `doppler`, `curl` et `uv pip install` (réseau) ont été **refusées par les permissions** de la session ; je ne les ai pas contournées. Donc D1–D3 et H1–H4 sont « Non vérifiable ». Les données locales (`data/`) sont des données de **dev datées du 24/08**, pas la production NAS (Postgres + Chroma serveur) : les volumes ci-dessous sont des ordres de grandeur du dev, pas de la prod.

---

## 1. Baseline (étape 0)

| Mesure | Résultat |
|---|---|
| Commit | `4dcb2c5` (branche `main`) |
| `git status` | **Non propre** : `?? data/app.db-shm`, `?? data/app.db-wal` (fichiers WAL SQLite non ignorés) |
| `pytest -q` | **107 passed**, 0 failed, 0 skipped, 15,8 s (1 warning de dépréciation httpx/starlette) |
| `ruff check src/ tests/` | 0 erreur |
| `mypy src/` | **5 erreurs** (alors que le `CLAUDE.md` annonce « strict mypy ») |

Erreurs mypy : `ingest/index.py:13` (`chromadb.ClientAPI` non défini), `index.py:67` et `core/retriever.py:32` (type `list[list[float]]` vs signature chromadb 1.5.9), `retriever.py:47` (`int(meta.get("page"))`), `routes/documentation.py:278` (`ctx` redéfini). Ce sont des dérives de versions, pas des bugs runtime, mais la référence anti-régression n'est donc pas « 0 erreur mypy » : c'est **5**.

Précaution : `tests/conftest.py` n'isole que la DB SQLite. J'ai lancé pytest avec `CHROMA_DIR` et `PDFS_DIR` redirigés vers des dossiers vides ; les tests actuels mockent Chroma, mais rien ne l'impose (voir §5).

---

## 2. Tableau de vérification

| ID | Affirmation | Verdict | Preuve | Impact sur le plan |
|---|---|---|---|---|
| A1 | PyMuPDF, pages sans texte ignorées, pas d'OCR | **Partiel** | `ingest/extract.py:10,31,37` (seuil 20 car.), `chunk.py:69` (pages vides sautées). **Mais** `routes/documentation.py:82-89` : si le toggle « vision » est actif, les pages avec images sont légendées par un LLM vision et réintégrées. Pas d'OCR au sens strict. | Docling n'est pas le seul moyen de récupérer le contenu visuel : le mécanisme existe déjà (coûteux, par page). |
| A2 | Splitter récursif ~900 / 150 de chevauchement, sans structure | **Partiel (overlap infirmé)** | Séparateurs et 900 confirmés (`chunk.py:9`, `config.py:45`). Test : texte de 400 mots uniques → 3 chunks, **0 mot partagé** entre chunks consécutifs. L'overlap n'est appliqué que dans le repli caractère (`chunk.py:36`, starts 0/750/1500 vérifiés). Chunks bornés à la page, jamais à cheval. | Le `CLAUDE.md` et le plan surestiment le chevauchement : une réponse à cheval sur deux chunks est coupée. C'est un gain peu coûteux à évaluer avant d'envisager Docling. |
| A3 | Métadonnées minimales : fichier + page | **Confirmé** | Chroma copie : clés réelles `{page, source}` sur 602/602 chunks (`index.py:61-63`). `chunk_index` n'existe que dans l'ID `{source}__p{n}__c{i}`. | Suffisant pour un recall@k **par page**. Pas de section, de titre ni de type de bloc. |
| A4 | Taille du corpus | **Confirmé (dev)** | 602 chunks, 4 documents, moyenne **665 car.** (min 37, max 899). Par doc : 289 / 216 / 86 / 11 chunks. 61 chunks < 200 car. (10 %). | BM25 en mémoire : trivial (602 chunks). Même ×100 reste raisonnable. |
| A5 | Pages ignorées comme vides | **Confirmé (info récupérable)** | `extract_pages` relancé sur les PDF : Fondamentaux 1/161 (p.2), Tests TIC 2/117 (p.2, 117), les 2 autres 0. Toutes les pages vides **contiennent des images**. Non persisté : affiché seulement si 1 PDF indexé (`documentation.py:90-106`). | Perte négligeable en texte (3 pages / 316). |
| B1 | Retriever purement sémantique | **Confirmé** | `core/retriever.py:31-35` : `collection.query(query_embeddings=…)`, rien d'autre. Distance cosinus (`index.py:39`). | Hybride = vrai gain structurel. |
| B2 | Propagation de « Sources utilisées (3–10) » | **Confirmé, avec nuance** | `ask.html` slider `n_sources` → `ask.py:46` `max(3, min(n, 10))` → `qa.py:19` `top_k` → `retriever.py:23,33` `n_results=min(k, count)`. `settings.retrieval_top_k` n'est **jamais utilisé** sur ce chemin (seulement si `top_k` vide). `questions.py:54` fixe `top_k=6` en dur, `[:4]` utilisés. | Un rerank/RRF devra surfetcher (top 20-30) puis couper à `top_k`. |
| B3 | Seuil de pertinence / « je ne sais pas » | **Infirmé (inexistant)** | Aucun seuil sur `score`. Seuls garde-fous : index vide → message (`qa.py:29-34`) et consigne de prompt « dis-le clairement » (`qa.py:12-13`). Le score est calculé (`retriever.py:48`) mais jamais filtré. | Hors-sujet = 5 chunks irrelevants au LLM. À traiter avec l'éval. |
| C1 | `llm.py` = abstraction provider, choix persisté en SQLite | **Partiel** | `LLMProvider` Protocol `runtime_checkable` : `chat(messages: list[dict[str,str]], model: str\|None=None, json_mode: bool=False) -> str` et `caption(image_png: bytes, prompt: str) -> str` (`llm.py:18-33`). `OllamaProvider`, `OpenAIProvider`, `get_provider() -> tuple[LLMProvider, str]`. Choix lu via `store.get_setting("llm_provider")` à **chaque appel**, en SQLite **ou Postgres** selon `POSTGRES_HOST`. Seuls `ollama`/`openai` valides (`routes/settings.py:219`). | La persistance n'est pas SQLite-only ; l'ajout d'un provider touche aussi la liste de validation. |
| C2 | Client Ollama : lib `ollama`, host + Authorization sans refonte ? | **Confirmé** | `llm.py:43` `ollama.Client(host=settings.ollama_host)`. `ollama` 0.6.2 : `BaseClient.__init__(client, host, *, follow_redirects, timeout, headers, **kwargs)` ; `headers=` supporté. **Piège** : le client lit **lui-même** `OLLAMA_API_KEY` et `OLLAMA_HOST` dans l'environnement et ajoute `Authorization: Bearer` s'il n'est pas déjà défini. | Faisable sans refonte (`host=https://ollama.com`, `headers=`). Mais si `OLLAMA_API_KEY` est injectée par Doppler pour le cloud, le provider Ollama **local** enverrait aussi ce bearer. Ne pas contourner `pydantic-settings` (règle projet) et passer `headers` explicitement. |
| C3 | Sites d'appel du LLM ; routage banque ≠ Q&A | **Confirmé (faisable)** | 4 appels : `core/qa.py:52-53`, `core/questions.py:62-63`, `core/bank.py:120,131`, `ingest/vision.py:21-22` ; `_deps.py:50` ne lit que le nom. Tous via `get_provider()` global. `chat(model=…)` existe déjà ; `bank.py` appelle `get_provider()` **une fois** avant la boucle. | Banque → autre provider : ajouter un paramètre/clé de setting dans `get_provider(purpose=…)` ; sans casser le Protocol. |
| C4 | Effort pour un 3e provider « ollama-cloud » | **Estimation** | Fichiers : `core/llm.py` (classe + branche `get_provider`), `config.py` (`ollama_cloud_*`, `SecretStr`), `routes/settings.py:219` (+ `_VALID_PROVIDERS`, vérif clé), `templates/partials/provider_panel.html`, `docker-compose.yml`/Doppler. Tests existants utilisables : `tests/test_provider.py` (7 tests : défaut, persistance, repli sans clé, construction client), `test_no_key_leak.py`. **Manquants** : test du nouveau provider + header, repli sans clé, `json_mode` cloud, validation de `/settings/provider`. | ~1 classe + ~4 fichiers. Risque modéré, bien couvert par le motif de `test_provider.py`. |
| D1 | `GET https://ollama.com/api/tags` avec bearer | **Non vérifiable** | `doppler` et `curl` refusés par les permissions de session. | — |
| D2 | `/api/chat` minimal + latence | **Non vérifiable** | Idem. | — |
| D3 | `/api/embed` bge-m3 sur le cloud | **Non vérifiable** | Idem. | Sans impact : embeddings restent locaux. |
| E1 | SM-2 maison dans `study/srs.py` | **Confirmé** | `srs.py:49-77`, formule EF `0.1-(5-g)(0.08+(5-g)0.02)`, plancher 1,3. | — |
| E2 | « Difficile » remet l'intervalle à 1 jour | **Confirmé** | Boutons (`partials/revision_card.html`, `hx-vals`) : **Raté=0, Difficile=2, Bien=4, Parfait=5**. `srs.py:57-60` : `grade < 3` ⇒ `repetitions=0`, `interval=1`. EF : g0 **−0,80**, g2 **−0,32**, g4 **0**, g5 **+0,10**. | « Difficile » = **échec** pour l'algorithme (reset), et un « Bien » ne fait jamais monter l'EF. Les libellés trompent l'utilisateur. Aussi : QCM multi partiel `score < 0.5` ⇒ grade 2 ⇒ reset (`revision.py:41-53`). Motive FSRS. |
| E3 | Historique complet pour rejouer FSRS | **Partiel** | Schéma : `reviews(id, card_id → cards ON DELETE CASCADE, grade INTEGER, reviewed_at TEXT DEFAULT datetime('now'))` (`store.py:39-44`; PG : `CURRENT_TIMESTAMP`). Rejouable en ordre, **mais** : échelle {0,2,4,5} à mapper sur 1–4 ; supprimer une carte **supprime son historique** ; `reviewed_at` en TEXT (formats SQLite/PG à normaliser, UTC vs local non garanti) ; pas de stability/difficulty stockés ; les essais de l'onglet Banque (`last_result`) **ne créent pas de review**. | Migration faisable ; prévoir de conserver `reviews`, d'ajouter les colonnes FSRS en `ALTER` via `_CARD_COLUMNS_ADDED`. |
| E4 | Volume cartes/révisions | **Confirmé (dev)** | Copie DB : **81 cartes, 3 révisions** (grades 0/2/4), 68 questions de banque, `app_settings` vide. EF : 79 cartes à 2,5 (jamais révisées). **Volumes prod inconnus.** | En dev, la migration ne rejoue presque rien. Vérifier la prod Postgres avant de juger l'effort. |
| F1 | Banque « fenêtre par fenêtre » | **Confirmé** | `bank.py:22` `_WINDOW_SIZE = 4` ; `bank.py:117` tranches de 4 chunks consécutifs triés `(page, index)` (`bank.py:84`). Aucune notion de section : une fenêtre traverse pages et chapitres (pages [6,7,8], [33,34,35] en base). | Windows ~2 700 car. sans frontières logiques → questions sur table des matières ou exercices (voir F5). |
| F2 | Dédoublonnage par embedding | **Confirmé** | `bank.py:32` seuil **0,88** cosinus ; embedding du **texte de la question** comparé à tous les embeddings de questions déjà stockés pour la **même source** + acceptées du run (`bank.py:121,150`). Seuil hérité d'OpenAI et **jamais mesuré pour bge-m3** (aveu en `bank.py:28-31`). Longueurs différentes ⇒ `-1.0` (jamais doublon). | Seuil à calibrer avec l'éval, pas à supposer. |
| F3 | Chunk source stocké par question | **Partiel** | `BankQuestion.pages` + `chunk_ids` (`store.py:350-355`) : les **4 IDs de la fenêtre**, pas le chunk exact. Posé programmatiquement, jamais par le LLM. Les cartes SRS (`cards`) **n'ont pas** ces champs. | Une vérification QCM peut relire les 4 chunks via Chroma. **Docling/réindexation casserait ces IDs** (voir §3). |
| F4 | Étape de vérification après génération | **Infirmé (inexistante)** | `core/qtypes.py` : validation de **forme** (JSON, indices, nb d'options), `parse_structured_items`. Aucun contrôle sémantique. Seule contrainte : le prompt (`bank.py:50`). | Justifie l'item « vérification QCM ». |
| F5 | 10 QCM échantillonnés | **Confirmé : problèmes fréquents** | Échantillon aléatoire (seed 42), jugement manuel sur les 4 chunks de fenêtre. **5/10 avec un défaut de fond**, 2 mineurs, 3 acceptables (détail ci-dessous). Biais : lu sur les ~1 400 premiers caractères de chaque fenêtre, n=10, un seul juge. | Forte valeur d'une passe de vérification ; **à mesurer** sur un échantillon plus large. |
| G1 | Sens de « topic » | **Confirmé, avec nuance** | `cards.topic` : nom du PDF pour les cartes venant de la banque (`store.py:539`), **texte libre** (sujet ou doc) pour l'onglet Générer (`generate.py:68,147`). Doc = topic uniquement pour la banque. Maîtrise = EF moyen normalisé 1,3–3,5 (`stats.py:36`) : un EF à 2,5 (jamais révisé) ⇒ 54 % de « maîtrise » (**79/81 cartes** à 2,5 en dev). | La barre de maîtrise est quasi arbitraire (voir E2). FSRS fournirait une vraie rétention. |
| H1 | `bm25s` + `PyStemmer` sous Python 3.13 | **Non vérifiable** | `uv pip install` refusé (réseau). | — |
| H2 | `fsrs` sous Python 3.13, API de rejeu | **Non vérifiable** | Idem. | — |
| H3 | `docling` : conflits torch / sentence-transformers | **Partiel : prémisse fausse** | Installation non testée. Mais **torch n'est pas épinglé** : `pyproject.toml` ne liste que `sentence-transformers>=3.0.0` (pas de torch). Installés : torch 2.13.0, sentence-transformers 5.6.0, transformers 5.13.0. Il n'existe pas d'épingle à violer ; le risque est inverse : une résolution Docling peut **déplacer torch/transformers** sans signal. | À tester dans un venv jetable avec `uv pip compile`. |
| H4 | Latence CPU du reranker | **Non vérifiable** | Modèle non téléchargeable (réseau refusé). | — |

### Détail F5 (10 QCM de la copie de la banque)

| # | Défaut | Détail |
|---|---|---|
| 44 | **Distracteur correct** | « Quels sont deux éléments clés du cycle de vie… » : réponses `Prototypage`, `Exploitation informatique` ; `Gestion de projet` (« gestion du projet / Pilotage et conduite » figure dans le même extrait) est écarté à tort. |
| 62 | **Réponse non soutenue** | Source = **feuille d'exercices** (PDF `2.Fondamentaux_exercices…`), sans corrigé : les bonnes réponses PDCA sont devinées par le LLM ; l'explication se contredit (« ce qui n'est pas illustré ici car les exemples cités ne sont pas corrects »). |
| 48 | **Réponse non soutenue** | La fenêtre montre la page « Validation des acquis » (exercices), rien sur budget/délais ; 3 bonnes réponses sur 4 dont deux quasi-synonymes. |
| 32 | **Distracteur ambigu** | L'option 0 « Ils ne sont pas tous obligatoires selon les exigences du client » est quasi vraie d'après l'extrait (« pas tous obligatoirement mis en œuvre »). |
| 15 | **Sur-affirmation** | « Les phases sont identiques » ; la source dit « mêmes phases et activités **de principe** ». Question « Comparez » à réponse unique triviale. |
| 4 | Trivial / hors-sujet | Fenêtre = **table des matières** ; question de culture générale, distracteurs absurdes (« obsolètes »). |
| 7 | Référence cachée | « selon **l'extrait** » : renvoie à un extrait que l'étudiant ne voit pas ; trivial. |
| 36, 29, 9 | Acceptables | Soutenus par le texte ; distracteurs peu plausibles mais corrects ; étiquette « difficile » discutable (tous les « difficile » commencent par « Comparez… »). |

---

## 3. Section I — Risques de régression par modification

Tests existants : 107, dont `test_provider` (7), `test_srs` (13), `test_chunk` (11), `test_index_job` (4), `test_bank` / `test_bank_answer` / `test_revision_answer`, `test_store_migration` (2), `test_store_adapt`, `test_no_key_leak`.
**Aucun test** de `retriever.py`, `qa.py` (hors mock d'erreur), `embeddings.py`, `index.py` (Chroma réel), ni de **backend Postgres** (seul `_adapt` est testé), ni de `routes/documentation.py` hors job.

### I.1 Provider Ollama Cloud
- **Couverts** : choix/persistance/repli du provider (`test_provider.py`), non-fuite d'exception (`test_no_key_leak.py`).
- **Non couverts** : en-tête `Authorization`, `json_mode` côté cloud, validation de `/settings/provider` pour un 3e nom, `caption()` (modèle vision cloud ≠ `ollama_vision_model`), rendu de `provider_panel.html`.
- **Points de casse** : (a) `ollama.Client` lit `OLLAMA_API_KEY` en env ⇒ fuite du bearer vers l'Ollama local ; (b) le repli silencieux vers Ollama local quand la clé manque (`llm.py:103-105`) — **en prod OpenAI-only, il n'y a aucun Ollama**, l'échec serait une exception générique (le message UI reste générique, OK) ; (c) `get_provider()` est appelé aussi par `sidebar_ctx()` à **chaque page** : une lecture DB de plus par rendu ; (d) timeouts : `ollama.Client(timeout=None)` par défaut, un cloud lent bloque un thread de la banque.

### I.2 Hybride BM25 + RRF (+ reranker)
- **Couverts** : rien sur la récupération (`retriever.py` sans test).
- **Non couverts** : tout : ordre, `top_k`, score, `n_results`, collection vide, concurrence avec `index_chunks`.
- **Points de casse** : (a) **BM25 en mémoire doit être reconstruit** après chaque indexation/suppression (`documentation.py:94-97`, `delete_source`) ; en prod, Chroma est un **autre conteneur** : l'index BM25 doit venir de `collection.get()` ou d'un cache invalidé ; (b) **215/602 chunks (36 %) contiennent le trait d'union conditionnel U+00AD** (« nécessai­ res ») : sans normalisation Unicode, le stemmer français et la tokenisation seront fausse sur plus d'un tiers du corpus ; (c) `SearchResult.score` est un cosinus [0,1] affiché dans le template : un score RRF aurait une autre échelle ; (d) la signature de `retrieve()` est utilisée par `qa.py` et `questions.py` (`top_k=6`) ; (e) latence : l'embedding est déjà le coût dominant.

### I.3 Docling à la place de PyMuPDF/splitter (réindexation complète)
- **Couverts** : `extract_pages` (sur PDF synthétique), `chunk_pages`, job d'index mocké.
- **Non couverts** : PDF réels, tables, pages vides, `render_page_png` (liée à PyMuPDF), vision captioning.
- **Points de casse** : (a) **les IDs de chunks `{source}__p{n}__c{i}` sont stockés dans `question_bank.chunk_ids`** (`store.py:61`) et parsés par regex (`bank.py:58`) : une réindexation avec un autre découpage **invalide les références de la banque existante** ; (b) `delete_source` + réindexation = fenêtre pendant laquelle le Q&A est vide/partiel ; (c) `is_empty`/`has_images` sont consommés par le job et la vision ; (d) `render_page_png` et `/docs/page/...png` supposent PyMuPDF et la pagination 1-indexée ; (e) dépendances lourdes dans l'image Docker `python:3.13-slim` sur le NAS ; (f) re-vectorisation de ~600 chunks à ~1,8 chunk/s ≈ 6 min en dev ; (g) `chromadb/chroma:latest` **non épinglé** dans `docker-compose.yml:55` vs client `chromadb` 1.5.9.

### I.4 Migration SM-2 → FSRS
- **Couverts** : `test_srs.py` (SM-2 uniquement, doit être remplacé), `test_revision_answer.py`, `test_store_migration.py` (patron d'ALTER réutilisable).
- **Non couverts** : rejeu de l'historique, mapping {0,2,4,5} → {1,2,3,4}, fuseaux, Postgres réel.
- **Points de casse** : (a) `update_card` est appelé depuis `routes/revision.py` (2 endroits) et `bank.py` ; (b) `Card` dataclass + `_row_to_card` + `get_topic_stats_rows` (`AVG(ease_factor)`) + `stats.py` (maîtrise basée sur l'EF) : **les barres de maîtrise cassent** si l'EF disparaît ; (c) double backend : `ALTER TABLE` via `_CARD_COLUMNS_ADDED` (SQLite sans `IF NOT EXISTS`) ; `due_date` est un TEXT ISO (date, pas datetime) alors que FSRS raisonne en horodatage ; (d) historique supprimé avec la carte (CASCADE) ; (e) en dev, 3 révisions seulement : la validation sur données réelles doit se faire sur la prod ; (f) boutons : les libellés (Raté/Difficile/Bien/Parfait) sont déjà décalés d'un cran vs FSRS (Again/Hard/Good/Easy).

### I.5 Passe de vérification des QCM
- **Couverts** : `test_bank.py` (génération mockée), `test_qtypes.py` (validation de forme).
- **Non couverts** : tout contrôle sémantique ; la boucle de génération avec un second appel LLM.
- **Points de casse** : (a) doublement des appels LLM/coûts dans `generate_bank_for_source` (boucle séquentielle, un job unique `_JOB` en mémoire, `should_stop` testé avant chaque fenêtre) ; (b) le job tourne dans `BackgroundTasks` et la connexion SQLite est **partagée** avec les requêtes (timeout 30 s) ; (c) politique de rejet : supprimer/marquer ? `question_bank` n'a pas de colonne de statut ; `UNIQUE(source, question)` ; (d) la vérification a besoin du texte des chunks (`get_source_chunks`) — déjà en mémoire dans la boucle ; (e) les sources de type **exercices** (réponses inconnues) ne peuvent pas être vérifiées par le texte : il faut les exclure ou les traiter à part.

---

## 4. Recommandation : ordre de priorité

Ordre proposé : *éval → Ollama Cloud → hybride → Docling → FSRS → vérification QCM*.

**Verdict : partiellement justifié ; je propose un autre ordre.**

1. **Éval d'abord : confirmé, et plus urgent que prévu.** Il n'existe ni jeu de référence ni test de récupération ; les décisions suivantes (seuil 0,88, hybride, reranker, overlap) ne sont mesurables qu'avec une éval. Condition : métadonnées `{source, page}` suffisantes (A3). Prévoir aussi un **filet minimal** : 5 erreurs mypy à fixer pour une baseline propre, et isoler Chroma/PDFs dans `conftest.py`.
2. **Vérification QCM passe de la 6ᵉ place à la 2ᵉ.** Les faits : aucune vérification (F4), **5/10** QCM échantillonnés avec défaut de fond (F5), coût en dehors de tout refactor lourd, et c'est le seul item **directement visible par l'étudiant** (il révise des réponses fausses). Ne dépend pas de Docling. Commencer par exclure les sources « exercices » et la table des matières.
3. **Correction de l'overlap et du seuil de pertinence (nouveau, petit).** L'overlap de 150 n'existe pas (A2) et il n'y a aucun seuil (B3). Les deux se testent avec l'éval, sans nouvelle dépendance. À faire **avant** de juger de l'utilité de Docling.
4. **Hybride BM25 + RRF** : justifié (B1), corpus minuscule (A4), mais **prérequis : normaliser U+00AD** (36 % des chunks) et définir l'invalidation de l'index (Chroma distant en prod). Reranker : H4 non mesuré ⇒ optionnel, conditionné à l'éval.
5. **FSRS : justifié sur le fond** (E2 : « Difficile » = reset ; maîtrise arbitraire G1), **mais** à faire une fois les volumes de prod connus. Le risque de migration est faible en dev (3 révisions) mais **inconnu en prod**.
6. **Ollama Cloud : à reléguer après l'éval.** Faisable sans refonte (C2/C3) mais D1–D3 **non vérifiés** (clé Doppler, modèles, latence) : le gain (coût/qualité) ne se juge qu'avec l'éval et un appel réel. À faire quand D1–D2 sont confirmés.
7. **Docling en dernier, et seulement si l'éval le justifie.** Perte de texte négligeable (A5 : 3 pages sur 316), la plupart des problèmes de qualité observés viennent du **fenêtrage et de la génération**, pas de l'extraction. Coût élevé : invalide les `chunk_ids` de la banque (I.3a), image Docker plus lourde, incertitude H3.

---

## 5. Découvertes hors liste

1. **Données dev incomplètes si copiées naïvement.** `data/app.db-wal` (3,8 Mo) est **plus gros** que `app.db` (1,3 Mo) : copier seul `app.db` aurait donné des chiffres faux. Les trois fichiers ont été copiés ensemble. Les fichiers `-wal`/`-shm` ne sont pas dans `.gitignore` (`git status` sale).
2. **`pytest` n'isole pas Chroma ni `data/pdfs`** (`tests/conftest.py` ne remplace que `db_path`). Aujourd'hui les tests mockent Chroma ; un futur test d'intégration ouvrirait le vrai `data/chroma`.
3. **Le `CLAUDE.md` annonce « strict mypy » mais mypy rend 5 erreurs** (dérives de chromadb 1.5.9).
4. **`chromadb/chroma:latest` non épinglé** (`docker-compose.yml:55`) alors que le client est en `>=0.5.0` : dérive de version serveur/client possible, particulièrement avant une réindexation.
5. **Bruit de texte** : 215/602 chunks contiennent U+00AD (trait d'union conditionnel) ; 61 chunks < 200 car. ; sources **hétérogènes** : un PDF d'**exercices sans corrigé** et un **sommaire** alimentent la banque de QCM (F5 #62, #4).
6. **`retrieval_top_k` jamais utilisé** sur le chemin du Q&A (B2) ; `questions.py` fixe `top_k=6` et n'en utilise que 4.
7. **Repli silencieux vers Ollama** (`llm.py:103-105`) : en prod OpenAI-only, une clé manquante provoque une erreur générique sans message explicite pour l'utilisateur.
8. **Libellés trompeurs** : « Difficile » (grade 2) est traité comme un échec (E2). Les « difficile » générés sont presque tous des « Comparez… » (F5).
9. **Toutes les pages ont des images** (161/161, 117/117, 29/29, 9/9) : activer le toggle vision enverrait **chaque page** à un LLM vision (coût/temps non bornés).
10. **Seuils non mesurés** : 0,88 (F2) ; pas d'évaluation du modèle d'embedding.

## 6. À faire pour lever les « Non vérifiable »

Lancer manuellement (autorisations de la session) : `doppler secrets --only-names` puis D1–D3 ; un venv jetable avec `bm25s PyStemmer fsrs` (H1/H2), `uv pip compile` avec `docling` (H3) et un `CrossEncoder` sur 20 paires (H4). Je peux le faire si vous autorisez ces commandes.
