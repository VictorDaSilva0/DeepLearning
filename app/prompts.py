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
    "corrige": {
        "label": "Corrigé détaillé",
        "kind": "corrige",
        "reserve": 6000,
        "prompt": (
            "Ce document est un sujet d'exercices (TD, TP ou contrôle). Rédige le corrigé détaillé de chaque "
            "exercice et de chaque question, dans l'ordre du sujet, avec une section ## Exercice N par exercice. "
            "Pour chaque question : rappelle en une ligne ce qui est demandé (avec ses points si le sujet donne "
            "un barème), puis donne la démarche étape par étape (justifications, calculs, schémas décrits, "
            "commandes ou code si nécessaire) et le résultat final en **gras**. Si une question dépend d'une "
            "information absente du sujet (fichier, figure, valeur), dis-le et pose une hypothèse raisonnable. "
            "Si tu n'es pas sûr d'un résultat, signale-le clairement. "
            "Termine par une section ## Points de méthode à retenir."
        ),
    },
    "similaire": {
        "label": "Sujet d'entraînement",
        "kind": "examen",
        "reserve": 6500,
        "prompt": (
            "Ce document est le sujet d'un contrôle d'une année précédente. Rédige un NOUVEAU sujet "
            "d'entraînement du même type : même format (exercices, questions de cours, QCM… dans les mêmes "
            "proportions), même durée et même barème s'ils sont indiqués, même difficulté et mêmes notions, mais "
            "avec des questions, des valeurs, des cas et des exemples différents — on ne doit pas pouvoir "
            "recopier le corrigé de l'ancien sujet.\n"
            "Commence par # Sujet d'entraînement, puis la durée et les documents autorisés si l'original les "
            "indique, puis les exercices (## Exercice N — <thème> (x points)).\n"
            "Ne donne PAS les solutions sous les énoncés : après une ligne ---, ajoute une section # Corrigé "
            "avec, pour chaque question, la réponse détaillée, le résultat en **gras** et la répartition des "
            "points."
        ),
    },
    "relecture": {
        "label": "Relecture du compte-rendu",
        "kind": "relecture",
        "reserve": 4500,
        "prompt": (
            "Le document ci-dessus est le compte-rendu rédigé par l'étudiant{subject_hint}. Relis-le comme un "
            "enseignant exigeant mais bienveillant et rédige :\n"
            "## Couverture du sujet — un tableau | Attendu du sujet | Statut (✅ / ⚠️ / ❌) | Commentaire | qui "
            "vérifie chaque livrable et chaque critère d'évaluation demandés{subject_only}.\n"
            "## Erreurs et imprécisions techniques — commandes, configurations ou explications fausses, "
            "risquées ou incomplètes, avec la correction.\n"
            "## Points forts\n"
            "## Améliorations conseillées — concrètes et classées par priorité.\n"
            "## Ce qu'il faut retenir pour l'examen — les notions du TP à maîtriser.\n"
            "Termine par une appréciation globale en une phrase. Ne donne pas de note chiffrée."
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

SYSTEM_TRANSLATOR = (
    "Tu es un traducteur technique spécialisé en informatique (systèmes, réseaux, Linux, programmation). "
    "Tu traduis de l'anglais vers le français des supports de cours au format Markdown, avec un français "
    "naturel, précis et pédagogique. Tu renvoies uniquement la traduction, sans aucun commentaire."
)


def translate_prompt(chunk: str, part: int, total: int) -> str:
    return (
        f"Traduis en français cette partie ({part}/{total}) d'un cours, en respectant STRICTEMENT ces règles :\n"
        "1. Termes techniques : écris le terme français suivi du terme anglais d'origine entre parenthèses, à sa "
        "première apparition dans chaque section, puis le terme français seul. Exemples : « noyau (kernel) », "
        "« interpréteur de commandes (shell) », « caractères génériques (wildcards) », « système de fichiers "
        "(filesystem) ». Si un terme est habituellement gardé en anglais en français (Linux, shell, alias, "
        "script, root…), garde-le tel quel.\n"
        "2. Ne traduis JAMAIS ce qui est entre ``` (blocs de code, sorties de terminal) ni entre ` (code en ligne), "
        "ni les commandes, options, noms de fichiers, chemins, variables et URL : recopie-les à l'identique.\n"
        "3. Conserve exactement la structure Markdown : titres (avec leur numéro), listes et indentation, "
        "**gras**, *italique*, tableaux, citations (>), blocs de code et lignes vides.\n"
        "4. Traduis aussi les titres et les notes de l'orateur.\n"
        "5. Renvoie uniquement le Markdown traduit, sans balises <texte>.\n\n"
        f"<texte>\n{chunk}\n</texte>"
    )


def translate_title_prompt(title: str) -> str:
    return (
        "Traduis ce titre de cours en français, de façon concise, sans parenthèses ni guillemets, en gardant "
        f"les sigles et numéros (ex. « LPIC1 101 Chapter 02 - Using the shell » → « LPIC1 101 Chapitre 02 - "
        f"Utiliser le shell »). Renvoie uniquement le titre traduit.\n\nTitre : {title}"
    )


OCR_PROMPT = (
    "Transcris fidèlement en Markdown tout le texte de cette page de cours : titres, paragraphes, listes, "
    "tableaux (en tableau Markdown) et formules (en LaTeX entre $). Si la page contient un schéma important, "
    "décris-le brièvement entre crochets. N'ajoute aucun commentaire et n'invente rien."
)


DOC_LABELS = {
    "cours": "le cours magistral (CM)",
    "td": "le sujet de TD (travaux dirigés)",
    "tp": "le sujet de TP (travaux pratiques)",
    "annale": "le sujet de contrôle d'une année précédente",
    "examen": "le sujet d'examen d'entraînement",
    "rendu": "le compte-rendu rédigé par l'étudiant",
}


def task_user_prompt(
    task: str,
    title: str,
    source: str,
    count: int,
    difficulty: str,
    instructions: str,
    doc_kind: str = "cours",
    subject: tuple[str, str] | None = None,
) -> str:
    """subject = (titre, contenu) du sujet de TD/TP auquel est rattaché un compte-rendu."""
    spec = TASKS[task]
    body = spec["prompt"].format(
        count=count,
        difficulty=DIFFICULTY_LABELS.get(difficulty, difficulty),
        instructions=instructions or "Aide-moi à réviser ce cours.",
        subject_hint=" en réponse au sujet fourni" if subject else "",
        subject_only=" par le sujet" if subject else " (déduis-les du compte-rendu lui-même)",
    )
    extra = ""
    if instructions and task != "custom":
        extra = f"\n\nConsignes supplémentaires de l'étudiant : {instructions}"
    label = DOC_LABELS.get(doc_kind, "le document")
    intro = ""
    if subject:
        intro = f"Voici le sujet « {subject[0]} » :\n\n<sujet>\n{subject[1]}\n</sujet>\n\n"
    return f"{intro}Voici {label} « {title} » :\n\n<cours>\n{source}\n</cours>\n\n{body}{extra}"


# --------------------------------------------------------------------------- plusieurs CM à la fois

# Tâches qui n'ont pas de sens sur un ensemble de CM (elles portent sur un TD / TP / compte-rendu).
MULTI_EXCLUDED = {"corrige", "relecture", "similaire"}
# Tokens de réponse en plus : un document qui couvre toute la matière est plus long.
MULTI_EXTRA_RESERVE = {"fiche": 2500, "resume": 1500, "glossaire": 1500, "plan": 1000}

MULTI_PROMPTS = {
    "fiche": (
        "Rédige UNE SEULE fiche de révision qui couvre l'intégralité de ces CM (et non une fiche par CM). "
        "Structure attendue :\n"
        "# Fiche — {subject}\n"
        "## L'essentiel en 10 lignes (vue d'ensemble de la matière)\n"
        "## Plan de la matière (une puce par CM : « **CM n — titre** : idée principale »)\n"
        "## Notions clés (une sous-partie ### par grande notion ; regroupe ce qui va ensemble même si c'est "
        "réparti sur plusieurs CM, et indique la provenance entre parenthèses, ex. (CM 2))\n"
        "## Définitions (liste « **terme** : définition », sans doublons)\n"
        "## Formules, commandes, règles et méthodes (si les cours en contiennent)\n"
        "## Exemples types\n"
        "## Liens entre les CM (ce qui s'appuie sur quoi)\n"
        "## Pièges fréquents et points d'attention\n"
        "## Questions pour s'auto-évaluer (8 questions qui couvrent tous les CM, sans les réponses)\n"
        "Sois synthétique, privilégie les listes à puces, et n'oublie aucun CM."
    ),
    "resume": (
        "Fais UN SEUL résumé structuré de l'ensemble de ces CM : un titre (# Résumé — {subject}), puis une "
        "section ## par CM, résumée en un court paragraphe suivi de 2 à 4 puces sur les points essentiels. "
        "Termine par une section ## À retenir absolument sur toute la matière (8 puces maximum)."
    ),
}

MULTI_NOTE = (
    "Important : considère l'ensemble de ces CM comme un seul cours (toute la matière). Répartis ton travail "
    "sur tous les CM sans en oublier, et produis un document unique."
)


def multi_user_prompt(
    task: str,
    subject: str,
    sources: list[tuple[str, str]],
    count: int,
    difficulty: str,
    instructions: str,
) -> str:
    """sources = [(titre du CM, texte — complet, condensé ou fiche existante)], dans l'ordre des CM."""
    blocks = "\n\n".join(
        f'<cours numero="{n}" titre="{title.replace(chr(34), chr(39))}">\n{text}\n</cours>'
        for n, (title, text) in enumerate(sources, start=1)
    )
    if task in MULTI_PROMPTS:
        body = MULTI_PROMPTS[task].format(subject=subject)
    else:
        body = TASKS[task]["prompt"].format(
            count=count,
            difficulty=DIFFICULTY_LABELS.get(difficulty, difficulty),
            instructions=instructions or "Aide-moi à réviser cette matière.",
            subject_hint="",
            subject_only=" (déduis-les du document lui-même)",
        )
        body = f"{body}\n\n{MULTI_NOTE}"
    extra = f"\n\nConsignes supplémentaires de l'étudiant : {instructions}" if instructions and task != "custom" else ""
    return (
        f"Voici les {len(sources)} cours magistraux (CM) de la matière « {subject} », dans l'ordre :\n\n"
        f"{blocks}\n\n{body}{extra}"
    )


def condense_prompt(part: int, total: int, words: int, chunk: str) -> str:
    return (
        f"Voici la partie {part}/{total} d'un cours. Condense-la fidèlement en {words} mots maximum, en "
        f"gardant les titres, définitions, formules, méthodes et exemples clés.\n\n<extrait>\n{chunk}\n</extrait>"
    )


def chat_system_prompt(
    doc: tuple[str, str] | None,
    files: list[tuple[str, str]],
    partial: bool,
    subjects: list[str],
    images: int = 0,
) -> str:
    """doc = (titre, extraits) du cours ouvert ; files = (nom, extraits) des fichiers joints à la discussion."""
    out = SYSTEM_TUTOR
    scope = "des extraits pertinents" if partial else "le contenu"
    if doc:
        out += (
            f"\n\nTu discutes avec l'étudiant à propos de son cours « {doc[0]} ». Voici {scope} de ce cours :\n"
            f"<cours>\n{doc[1]}\n</cours>"
        )
    if files:
        blocks = "\n\n".join(f'<document nom="{n.replace(chr(34), chr(39))}">\n{t}\n</document>' for n, t in files)
        out += f"\n\nL'étudiant t'a aussi donné ces fichiers (ils ne sont pas dans sa bibliothèque) :\n{blocks}"
    if images:
        out += f"\n\nIl t'a aussi envoyé {images} image(s) (photo, capture d'écran ou page scannée), jointe(s) à son dernier message."
    out += (
        "\n\nRéponds de manière pédagogique et concise. Si la réponse ne se trouve pas dans les documents fournis, "
        "dis-le, puis réponds avec tes connaissances générales en le signalant. Si l'étudiant te demande de "
        "l'interroger, pose une question à la fois et corrige ses réponses."
        "\n\nTu peux aussi CRÉER des documents dans sa bibliothèque avec l'outil creer_document : cours, TD, TP, "
        "examen d'entraînement (examen blanc), fiche de révision, exercices, QCM, flashcards, résumé ou note. "
        "Dès que l'étudiant te demande de créer, faire, rédiger, préparer, écrire ou générer l'un de ces documents "
        "(« fais-moi un cours sur… », « prépare un examen blanc de 2 h », « un TD de 5 exercices », « des flashcards "
        "sur ce chapitre »…), appelle l'outil au lieu d'écrire le document dans la discussion, puis dis en une "
        "phrase ce que tu prépares. Le cours ouvert et les fichiers joints sont transmis automatiquement à la "
        "rédaction : inutile de les recopier dans les consignes. Pour une simple question ou une explication, "
        "réponds normalement sans créer de document."
    )
    if subjects:
        out += f"\nMatières de l'étudiant : {', '.join(subjects)}."
    return out


# --------------------------------------------------------------------------- création de documents (discussion)

SYSTEM_AUTHOR = (
    "Tu es un enseignant-auteur expérimenté d'une école d'ingénieurs (informatique, réseaux, systèmes, objets "
    "connectés). Tu rédiges en français des supports pédagogiques rigoureux, prêts à être distribués : Markdown "
    "propre (titres, listes, **gras** sur les mots-clés, tableaux, blocs de code pour les commandes), formules en "
    "LaTeX entre $...$ ou $$...$$. Tu renvoies uniquement le document, en commençant directement par son titre "
    "(# …), sans phrase d'introduction ni commentaire, et sans l'entourer de balises de code."
)

CREATE_TYPES: dict[str, dict] = {
    "cours": {
        "label": "Cours", "kind": "cours", "reserve": 7000,
        "prompt": (
            "Rédige un cours complet et pédagogique, prêt à être révisé. Structure : # {title} ; ## Objectifs "
            "(3 à 5 puces) ; puis les grandes parties en ## numérotées, chacune avec des explications claires, les "
            "**définitions** en gras, des exemples concrets (commandes dans des blocs de code, calculs détaillés, "
            "tableaux comparatifs quand c'est utile) et un encadré « > **À retenir** » ; ## Résumé (8 puces au "
            "plus) ; ## Questions pour vérifier (5 questions, sans les réponses)."
        ),
    },
    "td": {
        "label": "TD", "kind": "td", "reserve": 6500,
        "prompt": (
            "Rédige un sujet de TD (travaux dirigés). Structure : # {title} ; une courte introduction (objectifs, "
            "prérequis) ; ## Rappels de cours (l'essentiel, en quelques lignes) ; puis des exercices progressifs "
            "« ## Exercice N — <thème> (★ à ★★★) » avec des énoncés précis, des données chiffrées réalistes et des "
            "questions numérotées. Ne mets pas les solutions sous les énoncés : après une ligne ---, ajoute "
            "« # Corrigé » avec la solution détaillée de chaque question (démarche, puis résultat en **gras**)."
        ),
    },
    "tp": {
        "label": "TP", "kind": "tp", "reserve": 6500,
        "prompt": (
            "Rédige un sujet de TP (travaux pratiques). Structure : # {title} ; ## Objectifs ; ## Prérequis et "
            "matériel (logiciels, machines virtuelles, versions) ; puis « ## Partie N — <thème> » avec des étapes "
            "numérotées, les commandes ou le code à saisir dans des blocs de code, ce que l'on doit observer et les "
            "questions à traiter dans le compte-rendu ; ## Livrables attendus ; ## Pour aller plus loin. Après une "
            "ligne ---, ajoute « # Éléments de correction » (résultats attendus, commandes, pièges fréquents)."
        ),
    },
    "examen": {
        "label": "Examen d'entraînement", "kind": "examen", "reserve": 7000,
        "prompt": (
            "Rédige un sujet d'examen d'entraînement réaliste, comme un vrai contrôle. Structure : # {title} ; la "
            "durée, les documents autorisés et le barème total sur 20 ; puis des exercices variés (questions de "
            "cours, exercices d'application, problème de synthèse, éventuellement un court QCM) sous la forme "
            "« ## Exercice N — <thème> (x points) », dont la longueur et la difficulté correspondent à la durée. "
            "Ne donne PAS les solutions sous les énoncés : après une ligne ---, ajoute « # Corrigé » avec, pour "
            "chaque question, la réponse détaillée, le résultat en **gras** et la répartition des points."
        ),
    },
    "fiche": {
        "label": "Fiche de révision", "kind": "fiche", "reserve": 4000,
        "prompt": (
            "Rédige une fiche de révision synthétique. Structure : # {title} ; ## L'essentiel en 5 lignes ; "
            "## Notions clés ; ## Définitions (« **terme** : définition ») ; ## Formules, commandes et méthodes ; "
            "## Exemples types ; ## Pièges fréquents ; ## Questions pour s'auto-évaluer (sans les réponses)."
        ),
    },
    "exercices": {
        "label": "Exercices", "kind": "exercices", "reserve": 6000,
        "prompt": (
            "Rédige une série d'exercices d'entraînement progressifs : # {title}, puis « ## Exercice N — <thème> "
            "(★ à ★★★) » avec des énoncés précis. Après une ligne ---, ajoute « # Corrigé » détaillé."
        ),
    },
    "resume": {
        "label": "Résumé", "kind": "resume", "reserve": 3000,
        "prompt": (
            "Rédige un résumé structuré : # {title} ; une section ## par grande partie (un court paragraphe et 2 à 4 "
            "puces chacune) ; ## À retenir absolument (8 puces au plus)."
        ),
    },
    "note": {
        "label": "Note", "kind": "note", "reserve": 4000,
        "prompt": "Rédige le document demandé en Markdown propre : # {title}, puis le contenu.",
    },
    "qcm": {"label": "QCM", "kind": "qcm", "task": "qcm", "count": 10},
    "flashcards": {"label": "Flashcards", "kind": "flashcards", "task": "flashcards", "count": 15},
}

# Le modèle nomme parfois le type à sa façon (« examen_blanc », « Fiche de révision »…) : mots-clés, sans accents.
CREATE_KIND_RULES = [
    (r"\bqcm\b|quiz", "qcm"),
    (r"flash|\bcartes?\b", "flashcards"),
    (r"exam|controle|partiel|blanc|\bds\b|entrainement", "examen"),
    (r"\btd\b|travaux diriges", "td"),
    (r"\btp\b|travaux pratiques", "tp"),
    (r"fiche", "fiche"),
    (r"exercice", "exercices"),
    (r"resume|synthese", "resume"),
    (r"\bcours\b|\bcm\b|chapitre|lecon", "cours"),
]

CREATE_TOOL = {
    "type": "function",
    "function": {
        "name": "creer_document",
        "description": (
            "Crée un nouveau document dans la bibliothèque de l'étudiant (cours, TD, TP, examen d'entraînement, "
            "fiche, exercices, QCM, flashcards, résumé, note). À utiliser dès que l'étudiant demande de créer, "
            "faire, rédiger, préparer ou générer un tel document."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": [k for k in CREATE_TYPES]},
                "titre": {"type": "string", "description": "Titre court, ex. « TD — Découpage en sous-réseaux »"},
                "consignes": {
                    "type": "string",
                    "description": "Tout ce que le document doit contenir : thème, notions, niveau, nombre "
                                   "d'exercices ou de questions, durée, avec ou sans corrigé…",
                },
                "matiere": {"type": "string", "description": "Matière concernée (parmi celles de l'étudiant), si elle est connue"},
                "nombre": {"type": "integer", "description": "Nombre de questions (QCM) ou de cartes (flashcards), si précisé"},
            },
            "required": ["type", "titre", "consignes"],
        },
    },
}


def create_prompt(kind: str, title: str, request: str, sources: list[tuple[str, str]], message: str, images: int = 0) -> str:
    spec = CREATE_TYPES[kind]
    if sources:
        blocks = "\n\n".join(f'<document titre="{t.replace(chr(34), chr(39))}">\n{x}\n</document>' for t, x in sources)
        intro = (
            f"Documents de référence :\n\n{blocks}\n\nAppuie-toi en priorité sur ces documents (mêmes notions, "
            "même niveau, même vocabulaire, mêmes notations)."
        )
    else:
        intro = (
            "Aucun document de référence : appuie-toi sur tes connaissances, au niveau d'une 4e année d'école "
            "d'ingénieurs en informatique et réseaux."
        )
    if images:
        intro += f"\nL'étudiant a aussi joint {images} image(s) : sers-t'en comme référence."
    return (
        f"{intro}\n\nDemande de l'étudiant : « {message.strip()} »\nCe qu'il faut rédiger : {request.strip()}\n\n"
        + spec["prompt"].format(title=title)
    )
