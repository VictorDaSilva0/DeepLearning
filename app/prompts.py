"""Consignes envoyées au modèle local."""

from __future__ import annotations

SYSTEM_TUTOR = (
    "Tu es un professeur particulier expert et bienveillant qui aide un étudiant à réviser ses cours. "
    "Tu écris en français clair et précis, en Markdown propre (titres, listes à puces, **gras** sur les "
    "mots-clés, tableaux quand c'est utile). Les formules mathématiques sont écrites en LaTeX entre $...$ "
    "(en ligne) ou $$...$$ (bloc). Tu t'appuies avant tout sur le cours fourni ; si tu ajoutes une "
    "information qui n'y figure pas, signale-le par « (hors cours) ». N'invente jamais de sources. "
    "Ne commence pas ta réponse par une phrase d'introduction du type « Voici… » : va droit au but."
)

SYSTEM_WRITER = (
    "Tu es un assistant d'écriture méticuleux. Tu renvoies uniquement le texte demandé, sans introduction, "
    "sans commentaire et sans l'entourer de balises de code. Tu conserves la mise en forme Markdown."
)

SYSTEM_CONDENSE = (
    "Tu condenses des extraits de cours pour qu'ils servent ensuite à créer des fiches de révision. "
    "Tu es fidèle au texte : tu gardes toutes les définitions, notions, formules, méthodes, dates, chiffres "
    "et exemples clés, et tu supprimes les répétitions et le remplissage. Tu écris en Markdown concis, "
    "sans introduction ni conclusion."
)

DIFFICULTY_LABELS = {"facile": "facile", "moyen": "intermédiaire", "difficile": "difficile (niveau examen)"}

QCM_SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "choices": {"type": "array", "items": {"type": "string"}},
                    "answer": {"type": "integer"},
                    "explanation": {"type": "string"},
                },
                "required": ["question", "choices", "answer", "explanation"],
            },
        }
    },
    "required": ["questions"],
}

FLASHCARDS_SCHEMA = {
    "type": "object",
    "properties": {
        "cards": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"front": {"type": "string"}, "back": {"type": "string"}},
                "required": ["front", "back"],
            },
        }
    },
    "required": ["cards"],
}

# kind = type du document créé ; reserve = tokens réservés à la réponse ; schema = sortie JSON.
TASKS: dict[str, dict] = {
    "fiche": {
        "label": "Fiche de révision",
        "kind": "fiche",
        "reserve": 3500,
        "prompt": (
            "Rédige une fiche de révision complète mais synthétique de ce cours. Structure attendue :\n"
            "# Fiche — <titre du cours>\n"
            "## L'essentiel en 5 lignes\n"
            "## Notions clés (une sous-partie ### par grande notion, avec explications courtes)\n"
            "## Définitions (liste « **terme** : définition »)\n"
            "## Formules, règles et méthodes (si le cours en contient)\n"
            "## Exemples types\n"
            "## Pièges fréquents et points d'attention\n"
            "## Questions pour s'auto-évaluer (5 questions, sans les réponses)\n"
            "Sois concis, privilégie les listes à puces, et ne laisse de côté aucune notion importante."
        ),
    },
    "resume": {
        "label": "Résumé",
        "kind": "resume",
        "reserve": 2500,
        "prompt": (
            "Fais un résumé structuré de ce cours : un titre (#), puis une section ## par grande partie du "
            "cours, chacune résumée en un court paragraphe suivi de 2 à 4 puces sur les points essentiels. "
            "Termine par une section ## À retenir absolument (5 puces maximum)."
        ),
    },
    "exercices": {
        "label": "Exercices corrigés",
        "kind": "exercices",
        "reserve": 5000,
        "prompt": (
            "Crée {count} exercices d'entraînement de difficulté {difficulty} sur ce cours, variés (application "
            "directe, raisonnement, cas pratique, et si pertinent calcul ou analyse de document). Chaque "
            "exercice est numéroté (## Exercice N — <thème>), avec un énoncé précis et autonome.\n"
            "Ne donne PAS les solutions sous les énoncés : regroupe tous les corrigés détaillés à la fin, "
            "dans une section # Corrigés, avec une sous-partie ## Corrigé de l'exercice N pour chacun "
            "(démarche étape par étape, résultat final en **gras**, et l'erreur classique à éviter)."
        ),
    },
    "qcm": {
        "label": "QCM interactif",
        "kind": "qcm",
        "reserve": 5000,
        "schema": QCM_SCHEMA,
        "prompt": (
            "Crée un QCM de {count} questions de difficulté {difficulty} qui couvre l'ensemble du cours. "
            "Chaque question a exactement 4 propositions (« choices ») dont une seule est correcte ; "
            "« answer » est l'indice (0 à 3) de la bonne réponse — varie sa position d'une question à "
            "l'autre. Les mauvaises réponses doivent être plausibles. « explanation » justifie la bonne "
            "réponse en une ou deux phrases. Ne numérote pas les propositions. Réponds uniquement en JSON."
        ),
    },
    "flashcards": {
        "label": "Flashcards",
        "kind": "flashcards",
        "reserve": 4000,
        "schema": FLASHCARDS_SCHEMA,
        "prompt": (
            "Crée {count} flashcards pour mémoriser ce cours (définitions, notions, formules, dates, "
            "méthodes). « front » : une question courte ou une notion à définir. « back » : la réponse, "
            "concise (une à trois phrases), exacte et autonome. Une seule idée par carte. "
            "Réponds uniquement en JSON."
        ),
    },
    "explication": {
        "label": "Explication simplifiée",
        "kind": "explication",
        "reserve": 3500,
        "prompt": (
            "Explique ce cours très simplement, comme à un élève qui le découvre : pour chaque notion "
            "importante, donne l'idée intuitive, une analogie de la vie courante et un exemple concret. "
            "Termine par une section ## Ce qu'il faut vraiment comprendre."
        ),
    },
    "glossaire": {
        "label": "Glossaire",
        "kind": "glossaire",
        "reserve": 3000,
        "prompt": (
            "Établis le glossaire de ce cours : tous les termes techniques, sigles et notions, classés par "
            "ordre alphabétique, sous forme de tableau Markdown | Terme | Définition | Exemple |."
        ),
    },
    "plan": {
        "label": "Plan / carte mentale",
        "kind": "plan",
        "reserve": 2500,
        "prompt": (
            "Fais le plan détaillé de ce cours sous forme de carte mentale textuelle : une liste à puces "
            "hiérarchique (3 niveaux maximum) qui montre l'organisation des idées et les liens entre elles. "
            "Chaque puce tient en quelques mots."
        ),
    },
    "questions": {
        "label": "Questions d'examen",
        "kind": "questions",
        "reserve": 4000,
        "prompt": (
            "Liste {count} questions de cours susceptibles de tomber à l'examen (difficulté {difficulty}), "
            "du type « Définissez… », « Expliquez… », « Comparez… ». Pour chacune, donne en dessous une "
            "réponse modèle concise dans un bloc de citation (> ), avec les mots-clés attendus en **gras**."
        ),
    },
    "custom": {
        "label": "Demande libre",
        "kind": "note",
        "reserve": 4000,
        "prompt": "{instructions}",
    },
}

TRANSFORMS: dict[str, str] = {
    "corriger": (
        "Corrige l'orthographe, la grammaire, la conjugaison, la ponctuation et la typographie du texte "
        "suivant, sans en changer le sens ni la mise en forme Markdown."
    ),
    "reformuler": "Reformule le texte suivant pour le rendre plus clair et plus fluide, en gardant le sens et le niveau de détail.",
    "simplifier": "Réécris le texte suivant plus simplement, pour qu'un débutant le comprenne, en gardant les informations essentielles.",
    "developper": "Développe le texte suivant : ajoute des explications, des exemples concrets et des précisions utiles.",
    "resumer": "Résume le texte suivant en quelques puces qui gardent l'essentiel.",
    "structurer": (
        "Mets en forme le texte suivant en Markdown propre et structuré (titres, listes, **gras** sur les "
        "mots-clés, tableaux si pertinent) sans retirer ni inventer de contenu."
    ),
    "exemples": "Écris 2 ou 3 exemples concrets qui illustrent le texte suivant (sans répéter le texte).",
    "traduire_en": "Traduis le texte suivant en anglais.",
    "traduire_fr": "Traduis le texte suivant en français.",
}

OCR_PROMPT = (
    "Transcris fidèlement en Markdown tout le texte de cette page de cours : titres, paragraphes, listes, "
    "tableaux (en tableau Markdown) et formules (en LaTeX entre $). Si la page contient un schéma important, "
    "décris-le brièvement entre crochets. N'ajoute aucun commentaire et n'invente rien."
)


def task_user_prompt(task: str, title: str, source: str, count: int, difficulty: str, instructions: str) -> str:
    spec = TASKS[task]
    body = spec["prompt"].format(
        count=count,
        difficulty=DIFFICULTY_LABELS.get(difficulty, difficulty),
        instructions=instructions or "Aide-moi à réviser ce cours.",
    )
    extra = ""
    if instructions and task != "custom":
        extra = f"\n\nConsignes supplémentaires de l'étudiant : {instructions}"
    return f"Voici le cours « {title} » :\n\n<cours>\n{source}\n</cours>\n\n{body}{extra}"


def condense_prompt(part: int, total: int, words: int, chunk: str) -> str:
    return (
        f"Voici la partie {part}/{total} d'un cours. Condense-la fidèlement en {words} mots maximum, en "
        f"gardant les titres, définitions, formules, méthodes et exemples clés.\n\n<extrait>\n{chunk}\n</extrait>"
    )


def chat_system_prompt(title: str, context: str, partial: bool) -> str:
    scope = "des extraits pertinents du" if partial else "le contenu du"
    return (
        f"{SYSTEM_TUTOR}\n\nTu discutes avec l'étudiant à propos de son cours « {title} ». Voici {scope} cours :\n"
        f"<cours>\n{context}\n</cours>\n\nRéponds de manière pédagogique et concise. Si la réponse ne se "
        "trouve pas dans le cours, dis-le, puis réponds avec tes connaissances générales en le signalant. "
        "Si l'étudiant te demande de l'interroger, pose une question à la fois et corrige ses réponses."
    )
