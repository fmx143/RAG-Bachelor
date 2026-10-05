# Spec — jugement manuel d'un échantillon de QCM

Statut : proposition, aucun code. Complète `eval-retrieval.md` (qui mesure la récupération) : ici on mesure la **qualité des questions générées** de la banque (`question_bank`, `core/bank.py`).

## 1. But

Juger à la main 40 questions, avec le texte source sous les yeux, puis obtenir des taux par critère, par source et par type. Une colonne « verdict LLM » est prévue pour mesurer plus tard l'accord entre un juge LLM et ce jugement humain.

## 2. Périmètre et hypothèses à confirmer

- **Types inclus : `mcq_single`, `mcq_multi`, `tf`.** `free` est exclu (pas d'options, donc ni « distracteur correct » ni QCM). Si « QCM » désignait seulement `mcq_*`, retirer `tf` : un seul paramètre `--types`.
- **Critère « distracteur correct » sur `tf`** : sans objet (`Faux` n'est pas un distracteur au sens QCM) → cellule `na` pour `tf`, exclue du dénominateur.
- Lecture seule sur `data/` (règles du projet) : l'export tourne sur une **copie** (§3.1), jamais sur `data/app.db*` ni `data/chroma/`.

## 3. Export (`rag-eval-qcm export`)

Point d'entrée proposé : `src/rag_bachelor/eval/qcm.py`, sous-commandes `export` et `stats`, déclarées dans `pyproject.toml` à côté de `rag-eval`. Un seul module, stdlib uniquement (`argparse`, `csv`, `random`, `json`).

### 3.1 Sources de données

- **Questions** : via la couche `study.store` uniquement (`bank_sources()`, `list_bank_questions(source=, qtype=, limit=, offset=)`), jamais de SQL direct. `list_bank_questions` n'a pas de « tout lister » : paginer par `(source, qtype)` jusqu'à épuisement.
- **Copie de la DB** : copier ensemble `app.db`, `app.db-wal`, `app.db-shm` dans un dossier temporaire et pointer `DB_PATH` dessus. Attention : `get_conn()` exécute le schéma et `_migrate` (écritures, dont `_retag_single_answer_multi`) → acceptable uniquement parce que c'est la copie. L'export refuse de tourner si `settings.db_path` est sous `data/` (même garde-fou que `rag-eval` pour Chroma).
- **Chunks** : `get_collection().get(ids=chunk_ids, include=["documents", "metadatas"])` sur le champ `chunk_ids` de chaque question (format `<source>__p<page>__c<idx>`). Même garde-fou : `CHROMA_DIR` (copie) ou `CHROMA_HOST` explicite. Les ids sont restitués **dans l'ordre de `chunk_ids`** (Chroma ne garantit pas l'ordre) ; un id introuvable est écrit tel quel avec le texte `[CHUNK ABSENT]` et compté dans un avertissement (index modifié depuis la génération).

### 3.2 Échantillonnage stratifié, graine fixe

- Strates = `(source, qtype)` parmi les types inclus ; graine par défaut `--seed 42`, `random.Random(seed)`.
- Allocation : tour de rôle sur les strates (triées), une question tirée au hasard dans chaque strate non vide à chaque tour, jusqu'à 40. Garantit que toute strate non vide est représentée tant que `nb_strates ≤ 40` ; les petites strates sont épuisées avant que les grosses reçoivent le reste.
- Si `nb_strates > 40` : les 40 premières strates dans l'ordre d'un mélange seedé ; l'export l'affiche.
- Déterminisme : mêmes DB + même graine → mêmes 40 `bank_id`. L'export affiche l'effectif obtenu par strate.
- `--n 40` en option (défaut 40).

### 3.3 Fichiers produits (dans `eval/qcm/`, versionnés)

1. `eval/qcm/sample.jsonl` — une ligne par question, données figées, **pas de colonnes de jugement** : `bank_id, source, pages, qtype, difficulty, question, options, correct, answer, chunks: [{id, page, text}]`.
2. `eval/qcm/grid.csv` — la grille à remplir (§4), préremplie avec `bank_id, source, qtype, difficulty` et colonnes de jugement vides.
3. `eval/qcm/sheet.md` — **fiche de lecture** générée pour juger confortablement : une section par question (énoncé, options avec bonnes réponses marquées, explication, puis les chunks de la fenêtre). C'est un fichier jetable, régénérable depuis `sample.jsonl`.

Si `grid.csv` existe déjà, `export` refuse d'écraser (perte de jugements) sauf `--force`.

## 4. Grille de jugement

**Format retenu : CSV** (UTF-8, séparateur `,`) — se remplit dans un tableur, se relit avec `csv`, se diffe proprement. Le markdown est écarté : tableau de 40 × 12 colonnes illisible et fragile à éditer. `sheet.md` fournit le confort de lecture.

Colonnes (une ligne par `bank_id`) :

| Colonne | Valeurs | Sens |
|---|---|---|
| `bank_id`, `source`, `qtype`, `difficulty` | préremplis | identité + strates |
| `supported` | `oui` / `partiel` / `non` | la bonne réponse marquée est-elle soutenue par les chunks de la fenêtre ? |
| `distractor_correct` | `oui` / `non` / `na` | `oui` = **au moins un distracteur est en réalité correct** (défaut). `na` pour `tf` |
| `bad_source` | `oui` / `non` | la fenêtre est inadaptée (exercice, énoncé de TD, sommaire, page de garde…) |
| `hidden_ref` | `oui` / `non` | l'énoncé renvoie à un contexte caché (« l'extrait », « le texte », « ce document », « selon l'auteur »…) |
| `trivial` | `oui` / `non` | répondable sans le cours (bon sens, élimination évidente, énoncé qui donne la réponse) |
| `difficulty_ok` | `oui` / `non` | le niveau déclaré (`facile/moyen/difficile`) est-il cohérent avec la question ? |
| `comment` | texte libre | optionnel |
| `llm_verdict` | vide au départ | **colonne réservée** (§6) |

Convention : `oui` = **défaut** pour `distractor_correct`, `bad_source`, `hidden_ref`, `trivial` ; `supported` et `difficulty_ok` sont orientés « `oui` = bon ». Le script porte cette orientation dans une table de constantes, pas dans le CSV.

Une cellule vide = non jugée : exclue des dénominateurs et comptée dans `n_non_juge`.

## 5. Script de statistiques (`rag-eval-qcm stats`)

Entrée : `eval/qcm/grid.csv` (+ `sample.jsonl` pour valider que les `bank_id` correspondent).

- Taux par critère, **global**, puis **par `source`**, puis **par `qtype`** (tableau texte, option `--json` pour un rapport).
- Chaque taux est affiché `k/n (p %)`, `n` = cellules jugées (hors `na`/vide). Aucun taux sans son `n` : avec 40 questions et des strates de 2–5 questions, les pourcentages par source sont **indicatifs** (le dire dans la sortie).
- `supported` : trois modalités rapportées (`oui` / `partiel` / `non`), plus un taux « soutenue » = `oui` seul et un taux « soutenue ou partielle ».
- Critères booléens : taux de défaut (`oui`).
- Lignes mal formées (valeur hors vocabulaire, `bank_id` inconnu) : erreur explicite avec le numéro de ligne, pas de correction silencieuse.
- Sortie complémentaire : liste des `bank_id` avec au moins un défaut, pour retrouver les questions à corriger/supprimer.

## 6. Colonne « verdict LLM » (prévue, non implémentée)

- Colonne `llm_verdict` déjà présente et vide dans `grid.csv` (évite de migrer plus tard).
- Format envisagé : un JSON compact par cellule avec la **même grille de critères** (`{"supported": "oui", "bad_source": "non", …}`), de façon à comparer critère par critère. À trancher lors de la spec du juge LLM ; le script `stats` ignore cette colonne pour l'instant.
- Mesure d'accord future : par critère, accord brut et κ de Cohen (humain vs LLM) sur les cellules jugées des deux côtés ; matrice de confusion pour `supported` (3 modalités). Le jugement humain reste la référence.
- Condition : le juge LLM doit lire exactement les mêmes `chunks` que `sample.jsonl` — d'où le gel des textes dans ce fichier plutôt qu'une relecture de Chroma au moment du juge.

## 7. Tests (pytest, sans Chroma ni LLM)

Fonctions pures sur données construites à la main :
- échantillonnage : déterminisme à graine fixe, représentation de chaque strate, effectif ≤ `n`, cas `nb_strates > n` ;
- parsing de la grille : cellule vide, `na`, valeur invalide (erreur avec numéro de ligne) ;
- taux : orientation des critères (défaut vs soutenu), exclusion des cellules vides/`na` du dénominateur, ventilation par source/type.

Pas de test sur l'export réel (dépend des données non versionnées) ; il est vérifié à la main sur la copie.

## 8. Hors périmètre

- Juge LLM, calcul de κ, correction/suppression automatique des questions défectueuses.
- Échantillon de questions `free`.
- Toute écriture dans `data/`, tout ré-indexage.

## 9. Définition de « terminé »

- `rag-eval-qcm export` sur une copie produit `sample.jsonl`, `grid.csv`, `sheet.md`, identiques à graine égale.
- `rag-eval-qcm stats` sur une grille remplie imprime les taux global / par source / par type avec `k/n`.
- `pytest -q`, `ruff check src/ tests/`, `mypy src/` passent.
