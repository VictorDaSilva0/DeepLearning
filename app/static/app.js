'use strict';

/* =====================================================================================
 * DeepLearning — interface (JavaScript sans dépendance)
 * ===================================================================================== */

// ------------------------------------------------------------------ utilitaires
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const debounce = (fn, ms) => {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
};
const store = {
  get(key, fallback) {
    try {
      const v = localStorage.getItem('dl.' + key);
      return v === null ? fallback : JSON.parse(v);
    } catch {
      return fallback;
    }
  },
  set(key, value) {
    try {
      localStorage.setItem('dl.' + key, JSON.stringify(value));
    } catch {
      /* stockage indisponible : on ignore */
    }
  },
};
const shuffle = (arr) => {
  for (let i = arr.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [arr[i], arr[j]] = [arr[j], arr[i]];
  }
  return arr;
};
const fmtSize = (n) => (n < 1024 ? `${n} o` : n < 1048576 ? `${(n / 1024).toFixed(0)} Ko` : `${(n / 1048576).toFixed(1)} Mo`);
const fmtDate = (iso) => {
  if (!iso) return '';
  const d = new Date(iso.length <= 10 ? iso + 'T00:00:00' : iso);
  return d.toLocaleDateString('fr-FR', { day: 'numeric', month: 'short' });
};
const fmtInterval = (days) =>
  days <= 0 ? "aujourd'hui" : days < 31 ? `${days} j` : days < 365 ? `${Math.round(days / 30)} mois` : `${(days / 365).toFixed(1)} an`;
const regexEscape = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/** Mini-rendu Markdown en ligne (gras, italique, code, surlignage) pour QCM et flashcards. */
function inlineMd(s) {
  return esc(s)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*\\])\*([^*\s](?:[^*]*[^*\s])?)\*(?!\*)/g, '$1<em>$2</em>')
    .replace(/==([^=]+)==/g, '<mark>$1</mark>')
    .replace(/\n/g, '<br>');
}

function toast(message, opts = {}) {
  const el = document.createElement('div');
  el.className = 'toast' + (opts.error ? ' error' : '');
  el.innerHTML = `<span>${esc(message)}</span>`;
  if (opts.action) {
    const b = document.createElement('button');
    b.textContent = opts.action.label;
    b.onclick = () => {
      opts.action.run();
      el.remove();
    };
    el.append(b);
  }
  $('#toasts').append(el);
  setTimeout(() => el.remove(), opts.duration || (opts.error ? 7000 : 4500));
}

async function errorMessage(res) {
  let msg = `Erreur ${res.status}`;
  try {
    const j = await res.json();
    if (typeof j.detail === 'string') msg = j.detail;
    else if (Array.isArray(j.detail)) msg = j.detail.map((d) => d.msg).join(' ; ');
  } catch {
    /* réponse non JSON */
  }
  return msg;
}

async function api(path, { method = 'GET', body } = {}) {
  const opts = { method, headers: {} };
  if (body instanceof FormData) opts.body = body;
  else if (body !== undefined) {
    opts.body = JSON.stringify(body);
    opts.headers['Content-Type'] = 'application/json';
  }
  const res = await fetch(path, opts);
  if (!res.ok) throw await httpError(path, res);
  const ct = res.headers.get('content-type') || '';
  return ct.includes('json') ? res.json() : res.text();
}

/** Erreur d'une requête ; une session expirée (401) réaffiche l'écran de connexion. */
async function httpError(path, res) {
  if (res.status === 401 && !path.startsWith('/api/auth/') && !login.pending) {
    requireLogin().then(() => {
      loadLibrary();
      refreshHealth();
    });
  }
  const err = new Error(res.status === 401 ? 'Connexion requise.' : await errorMessage(res));
  err.status = res.status;
  return err;
}

/** Lit une réponse NDJSON (une ligne JSON par événement) au fil de l'eau. */
async function streamNDJSON(path, body, onEvent, signal) {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body ?? {}),
    signal,
  });
  if (!res.ok) throw await httpError(path, res);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  const handle = (line) => {
    line = line.trim();
    if (!line) return;
    const ev = JSON.parse(line);
    if (ev.type === 'error') throw new Error(ev.message);
    onEvent(ev);
  };
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let i;
      while ((i = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, i);
        buffer = buffer.slice(i + 1);
        handle(line);
      }
    }
    handle(buffer + decoder.decode());
  } catch (e) {
    reader.cancel().catch(() => {});
    throw e;
  }
}

async function renderMarkdown(md) {
  const r = await api('/api/render', { method: 'POST', body: { markdown: md } });
  return r.html;
}

/** Formules : KaTeX est chargé depuis un CDN, s'il est indisponible on laisse le LaTeX brut. */
function typeset(el, dollars = false) {
  if (!window.renderMathInElement || !el) return;
  const delimiters = [
    { left: '\\[', right: '\\]', display: true },
    { left: '\\(', right: '\\)', display: false },
  ];
  if (dollars) delimiters.unshift({ left: '$$', right: '$$', display: true }, { left: '$', right: '$', display: false });
  try {
    window.renderMathInElement(el, { delimiters, throwOnError: false });
  } catch {
    /* formule invalide : on ignore */
  }
}

/** Rendu Markdown « en direct » limité en fréquence (pendant le streaming de l'IA). */
function liveRenderer(target, interval = 350) {
  const getEl = typeof target === 'function' ? target : () => target;
  let pending = null;
  let timer = null;
  let seq = 0;
  const run = async () => {
    timer = null;
    const md = pending;
    pending = null;
    if (md === null) return;
    const mine = ++seq;
    try {
      const html = await renderMarkdown(md);
      const el = getEl();
      if (mine === seq && el) {
        el.innerHTML = html;
        typeset(el);
      }
    } catch {
      /* rendu impossible : on garde l'affichage précédent */
    }
  };
  return {
    update(md) {
      pending = md;
      if (!timer) timer = setTimeout(run, interval);
    },
    async flush(md) {
      clearTimeout(timer);
      timer = null;
      pending = md;
      await run();
    },
  };
}

// ------------------------------------------------------------------ constantes
const KINDS = {
  cours: { icon: '📘', label: 'Cours (CM)' },
  td: { icon: '📗', label: 'TD' },
  tp: { icon: '🔧', label: 'TP' },
  annale: { icon: '📜', label: 'Contrôle année précédente' },
  examen: { icon: '🎯', label: "Examen d'entraînement" },
  corrige: { icon: '🔑', label: 'Corrigé' },
  rendu: { icon: '📑', label: 'Compte-rendu' },
  relecture: { icon: '🧐', label: 'Relecture' },
  note: { icon: '📝', label: 'Note' },
  fiche: { icon: '📋', label: 'Fiche' },
  resume: { icon: '🧾', label: 'Résumé' },
  exercices: { icon: '✏️', label: 'Exercices' },
  qcm: { icon: '✅', label: 'QCM' },
  flashcards: { icon: '🃏', label: 'Flashcards' },
  explication: { icon: '💡', label: 'Explication' },
  glossaire: { icon: '📖', label: 'Glossaire' },
  plan: { icon: '🗺️', label: 'Plan' },
  questions: { icon: '🎓', label: "Questions d'examen" },
};

const TASKS = [
  { id: 'fiche', icon: '📋', label: 'Fiche de révision', desc: 'Notions, définitions, formules, pièges' },
  { id: 'resume', icon: '🧾', label: 'Résumé', desc: 'Les grandes parties en bref' },
  { id: 'exercices', icon: '✏️', label: 'Exercices corrigés', desc: 'Énoncés, corrigés à la fin', count: 5, difficulty: true },
  { id: 'qcm', icon: '✅', label: 'QCM interactif', desc: 'Quiz noté avec explications', count: 10, difficulty: true },
  { id: 'flashcards', icon: '🃏', label: 'Flashcards', desc: 'Mémorisation espacée', count: 15 },
  { id: 'questions', icon: '🎓', label: "Questions d'examen", desc: 'Questions + réponses modèles', count: 8, difficulty: true },
  { id: 'explication', icon: '💡', label: 'Explication simple', desc: 'Analogies, exemples concrets' },
  { id: 'glossaire', icon: '📖', label: 'Glossaire', desc: 'Tous les termes techniques' },
  { id: 'plan', icon: '🗺️', label: 'Plan / carte mentale', desc: "L'organisation des idées" },
  { id: 'corrige', icon: '🔑', label: 'Corrigé détaillé', desc: 'Résout les exercices du TD / TP / contrôle' },
  { id: 'similaire', icon: '🎯', label: "Sujet d'entraînement", desc: 'Nouveau contrôle du même type + corrigé', only: ['annale', 'examen'] },
  { id: 'relecture', icon: '🧐', label: 'Relire mon compte-rendu', desc: 'Couverture du sujet, erreurs, conseils', only: ['rendu'] },
  { id: 'custom', icon: '💬', label: 'Demande libre', desc: 'Ta propre consigne' },
];
/** Tâches qui portent sur un seul document (pas proposées sur « Tous les CM »). */
const MULTI_EXCLUDED = ['corrige', 'relecture', 'similaire'];
/** Tâche proposée d'emblée en ouvrant ce type de document. */
const PREFERRED_TASK = { rendu: 'relecture', annale: 'corrige' };

/** Catégories des documents de cours importés (le type « cours » correspond au CM). */
const CATEGORIES = [
  { kind: 'cours', short: 'CM', label: 'Cours magistraux' },
  { kind: 'td', short: 'TD', label: 'Travaux dirigés' },
  { kind: 'tp', short: 'TP', label: 'Travaux pratiques' },
  { kind: 'annale', short: 'Contrôle', label: 'Contrôles des années précédentes', group: 'Contrôles années préc.' },
  { kind: 'examen', short: 'Examen', label: "Examens d'entraînement", group: "Examens d'entraînement" },
  { kind: 'rendu', short: 'CR', label: 'Comptes-rendus' },
];
const SOURCE_KINDS = CATEGORIES.map((c) => c.kind);

/** Même détection que le serveur : « TD2_routage.pdf » → td, « TP-Docker.pptx » → tp, « Compte-rendu… » → CR,
 *  « Partiel 2024 », « DS1 », « Examen… » → contrôle d'une année précédente, sinon CM. */
function guessKind(filename) {
  const stem = filename.replace(/\.[^.]+$/, '');
  if (/compte[\s_-]*rendu|(?:^|[^a-z])(?:cr|rapport)(?![a-z])/i.test(stem)) return 'rendu';
  if (
    /annale|examen|partiel|rattrapage|midterm|(?:^|[^a-z])exams?(?![a-z])|contr[oô]le(?![\s_-]*(?:d[eu]s?(?![a-z])|d['’]))|(?:^|[^a-z])(?:ds|dst|cc)\d*(?![a-z])/i.test(stem)
  )
    return 'annale';
  if (/(?:^|[^a-z])td(?![a-z])|travaux[\s_-]*dirig/i.test(stem)) return 'td';
  if (/(?:^|[^a-z])tp(?![a-z])|travaux[\s_-]*pratiq/i.test(stem)) return 'tp';
  return 'cours';
}

const TOOLS = [
  { id: 'corriger', label: '✔ Corriger' },
  { id: 'reformuler', label: '🔁 Reformuler' },
  { id: 'simplifier', label: '🪶 Simplifier' },
  { id: 'developper', label: '➕ Développer' },
  { id: 'resumer', label: '✂ Résumer' },
  { id: 'structurer', label: '🧱 Structurer' },
  { id: 'exemples', label: '🧪 Exemples' },
  { id: 'traduire_en', label: '🇬🇧 Anglais' },
  { id: 'traduire_fr', label: '🇫🇷 Français' },
];

const CHAT_SUGGESTIONS = [
  'Quels sont les points essentiels de ce cours ?',
  'Interroge-moi sur ce cours, une question à la fois.',
  'Explique-moi la notion la plus difficile avec un exemple.',
  "Fais-moi un TD d'entraînement sur ce cours, avec le corrigé.",
  "Prépare-moi un examen blanc d'1 h sur ce cours.",
];
/** Sans cours ouvert : les suggestions qui finissent par « … » se complètent avant l'envoi. */
const CHAT_SUGGESTIONS_FREE = [
  'Fais-moi un cours sur …',
  'Crée un TD de 5 exercices sur …',
  'Prépare-moi un examen blanc de 2 h sur …',
  'Explique-moi simplement …',
];
/** Types proposés pour « 📄 En faire un document » sous une réponse de l'IA. */
const ANSWER_KINDS = ['cours', 'td', 'tp', 'examen', 'fiche', 'exercices', 'note'];

// ------------------------------------------------------------------ état
const state = {
  subjects: [],
  docs: [],
  current: null,
  health: null,
  stats: null,
  mode: store.get('mode', 'split'),
  aiOpen: window.innerWidth < 1180 ? false : store.get('aiOpen', true),
  aiTab: 'generate',
  task: store.get('task', 'fiche'),
  scope: 'doc', // 'doc' = le CM ouvert, 'all' = tous les CM de la matière
  scopeOff: new Set(), // CM décochés dans la liste
  scopeSubject: undefined,
  difficulty: 'moyen',
  collapsed: store.get('collapsed', {}),
  chats: {},
  chatFiles: {}, // fichiers donnés à l'IA, par discussion : { id, name, chars, images, uploading }
  docTab: 'edit',
  dirty: false,
  previewSel: '',
  controllers: {},
  toolCtx: null,
  quiz: null,
  review: null,
  keyMode: null,
  pendingFind: null,
};

const editor = $('#editor');
const preview = $('#preview');

// ================================================================== bibliothèque
// ------------------------------------------------------------------ semestres
const semNum = (s) => parseInt(String(s || '').slice(1), 10) || 0;
const semLabel = (s) => (s ? `Semestre ${semNum(s)} · I${Math.ceil(semNum(s) / 2)}` : 'Autres matières');
const currentSemester = () => state.settings?.current_semester || '';

/** Semestres présents, le semestre en cours d'abord, puis du plus récent au plus ancien. */
function semesterList() {
  const cur = currentSemester();
  const list = [...new Set(state.subjects.map((s) => s.semester || ''))];
  return list.sort((a, b) => {
    if (a === cur) return -1;
    if (b === cur) return 1;
    if (!a) return 1;
    if (!b) return -1;
    return semNum(b) - semNum(a);
  });
}

/** Options d'un <select> de matières, regroupées par semestre. */
function subjectOptions(noneLabel = 'Sans matière') {
  let html = `<option value="">${esc(noneLabel)}</option>`;
  for (const sem of semesterList()) {
    const subs = state.subjects.filter((s) => (s.semester || '') === sem);
    html += `<optgroup label="${esc(semLabel(sem))}">${subs.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join('')}</optgroup>`;
  }
  return html;
}

const subjectById = (id) => state.subjects.find((s) => s.id === id);

function daysUntil(iso) {
  const d = new Date(iso + 'T00:00:00');
  const t = new Date();
  t.setHours(0, 0, 0, 0);
  return Math.round((d - t) / 86400000);
}
const countdownText = (n) => (n === 0 ? "aujourd'hui" : n === 1 ? 'demain' : n < 0 ? `il y a ${-n} j` : `J-${n}`);
const urgency = (n) => (n <= 3 ? 'soon' : n <= 10 ? 'near' : '');

// ------------------------------------------------------------------ chargement
async function loadLibrary() {
  const [subjects, docs, upcoming] = await Promise.all([
    api('/api/subjects'),
    api('/api/documents'),
    api('/api/events/upcoming?limit=60').catch(() => []),
  ]);
  state.subjects = subjects;
  state.docs = docs;
  state.upcoming = upcoming;
  renderLibrary();
  fillSubjectSelects();
  updateNextExamBadge();
}

async function refreshUpcoming() {
  try {
    state.upcoming = await api('/api/events/upcoming?limit=60');
  } catch {
    state.upcoming = [];
  }
  renderLibrary();
  updateNextExamBadge();
}

function updateNextExamBadge() {
  const b = $('#next-exam');
  const next = (state.upcoming || [])[0];
  b.hidden = !next;
  if (next) {
    const n = daysUntil(next.date);
    b.textContent = n === 0 ? "auj." : `J-${n}`;
    b.className = urgency(n);
    b.parentElement.title = `Prochain : ${next.title} (${countdownText(n)})`;
  }
}

function renderLibrary() {
  const nav = $('#library');
  const scroll = nav.scrollTop;
  nav.innerHTML = '';
  if (!state.docs.length && !state.subjects.length) {
    nav.innerHTML = '<div class="empty">Ta bibliothèque est vide.<br>Crée une matière ou importe un cours ⬆</div>';
    return;
  }
  const byId = new Map(state.docs.map((d) => [d.id, d]));
  const cur = currentSemester();
  // Prochain examen de chaque matière (dans les 3 semaines) pour l'afficher à côté du nom.
  const exams = new Map();
  for (const ev of state.upcoming || []) {
    if (ev.subject_id && !exams.has(ev.subject_id) && daysUntil(ev.date) <= 21) exams.set(ev.subject_id, ev);
  }

  for (const sem of semesterList()) {
    const subs = state.subjects.filter((s) => (s.semester || '') === sem);
    const key = 'sem:' + sem;
    const collapsed = state.collapsed[key] ?? Boolean(cur && sem !== cur);
    const nDocs = subs.reduce((a, s) => a + (s.doc_count || 0), 0);
    const block = document.createElement('section');
    block.className = 'semester' + (collapsed ? ' collapsed' : '');
    block.innerHTML = `
      <div class="semester-head" title="${subs.length} matière(s), ${nDocs} document(s)">
        <span class="caret">▾</span>
        <span>${esc(semesterList().length === 1 && !sem ? 'Matières' : semLabel(sem))}</span>
        ${sem && sem === cur ? '<span class="chip now">en cours</span>' : ''}
        <span class="spacer"></span>
        <span class="count">${subs.length}</span>
      </div>
      <div class="semester-body"></div>`;
    block.querySelector('.semester-head').addEventListener('click', () => {
      const now = !block.classList.contains('collapsed');
      state.collapsed[key] = now;
      store.set('collapsed', state.collapsed);
      block.classList.toggle('collapsed', now);
    });
    const body = block.querySelector('.semester-body');
    for (const s of subs) {
      body.append(subjectGroup(s, state.docs.filter((d) => d.subject_id === s.id), byId, exams.get(s.id)));
    }
    nav.append(block);
  }

  const loose = state.docs.filter((d) => d.subject_id == null);
  if (loose.length) {
    const block = document.createElement('section');
    block.className = 'semester';
    block.innerHTML = '<div class="semester-head"><span class="caret">▾</span><span>Sans matière</span></div><div class="semester-body"></div>';
    block.querySelector('.semester-body').append(subjectGroup({ id: null, name: 'Documents à ranger', color: '#9ca3af' }, loose, byId));
    nav.append(block);
  }
  nav.scrollTop = scroll;
  updateLibraryPresence();
}

function subjectGroup(g, docs, byId, exam) {
  const inGroup = (d) => d && (d.subject_id ?? null) === g.id;
  const roots = docs.filter((d) => !d.parent_id || !inGroup(byId.get(d.parent_id)));
  const childrenOf = (id) => docs.filter((d) => d.parent_id === id);
  const key = String(g.id);

  const wrap = document.createElement('div');
  wrap.className = 'subject' + (state.collapsed[key] ? ' collapsed' : '') + (docs.length ? '' : ' no-docs');
  let examChip = '';
  if (exam) {
    const n = daysUntil(exam.date);
    examChip = `<span class="exam-chip ${n <= 3 ? 'soon' : ''}" title="${esc(exam.title)} — ${fmtDate(exam.date)}">📅 ${countdownText(n)}</span>`;
  }
  wrap.innerHTML = `
    <div class="subject-head" data-sid="${g.id ?? ''}" title="${esc(g.name)}">
      <span class="caret">▾</span>
      <span class="swatch" style="background:${esc(g.color)}"></span>
      <span class="name">${esc(g.name)}</span>
      ${docs.length ? `<span class="count">${docs.length}</span>` : ''}
      <span class="spacer"></span>
      ${examChip}
      ${g.id !== null ? '<button class="icon-btn" data-menu title="Options de la matière">⋯</button>' : ''}
    </div>
    <div class="subject-docs"></div>`;
  const head = wrap.querySelector('.subject-head');
  head.addEventListener('click', (e) => {
    if (e.target.closest('[data-menu]')) {
      e.stopPropagation();
      subjectMenu(e.target.closest('[data-menu]'), g);
      return;
    }
    if (!docs.length) {
      if (g.id !== null) subjectMenu(head.querySelector('[data-menu]') || head, g);
      return;
    }
    state.collapsed[key] = !state.collapsed[key];
    store.set('collapsed', state.collapsed);
    wrap.classList.toggle('collapsed', state.collapsed[key]);
  });
  head.addEventListener('dragover', () => head.classList.add('drop-target'));
  head.addEventListener('dragleave', () => head.classList.remove('drop-target'));
  head.addEventListener('drop', () => head.classList.remove('drop-target'));

  const list = wrap.querySelector('.subject-docs');
  const seen = new Set();
  const add = (d, depth) => {
    if (seen.has(d.id)) return;
    seen.add(d.id);
    list.append(docItem(d, depth));
    childrenOf(d.id).forEach((c) => add(c, depth + 1));
  };
  // Sous-groupes CM / TD / TP (les documents générés restent rangés sous leur document d'origine ;
  // ceux faits à partir de plusieurs CM — fiche de toute la matière… — en tête des CM).
  const multi = roots.filter((d) => d.sources_count && !SOURCE_KINDS.includes(d.kind));
  const groups = [
    ...CATEGORIES.map((c) => ({ ...c, docs: [...(c.kind === 'cours' ? multi : []), ...roots.filter((d) => d.kind === c.kind)] })),
    {
      kind: 'autre', short: 'Notes & fiches', label: 'Notes et documents',
      docs: roots.filter((d) => !SOURCE_KINDS.includes(d.kind) && !d.sources_count),
    },
  ];
  const labelled = groups.some((grp) => grp.kind !== 'autre' && grp.docs.length);
  for (const grp of groups) {
    if (!grp.docs.length) continue;
    if (labelled) {
      const lbl = document.createElement('div');
      lbl.className = 'cat-label';
      lbl.title = grp.label;
      const n = grp.kind === 'autre' ? grp.docs.length : grp.docs.filter((d) => d.kind === grp.kind).length;
      lbl.innerHTML = `<span>${esc(grp.group || grp.short)}</span><span class="n">${n}</span>`;
      list.append(lbl);
    }
    grp.docs.forEach((d) => add(d, 0));
  }
  return wrap;
}

function docItem(d, depth) {
  const b = document.createElement('button');
  b.className = 'doc-item' + (depth ? ' child' : '') + (state.current?.id === d.id ? ' active' : '');
  b.style.paddingLeft = `${8 + Math.min(depth, 4) * 16}px`;
  b.dataset.id = d.id;
  b.title = d.title;
  if (d.sources_count) b.title += ` — à partir de ${d.sources_count} CM`;
  b.innerHTML = `<span class="ico">${KINDS[d.kind]?.icon || '📄'}</span><span class="name">${esc(d.title)}</span>${
    d.sources_count ? `<span class="multi" title="Fait à partir de ${d.sources_count} CM">📚 ${d.sources_count}</span>` : ''
  }`;
  b.onclick = () => go(`#doc/${d.id}`);
  return b;
}

function highlightActive() {
  $$('.doc-item').forEach((b) => b.classList.toggle('active', +b.dataset.id === state.current?.id));
}

function fillSubjectSelects() {
  const sel = $('#doc-subject');
  sel.innerHTML = subjectOptions();
  if (state.current) sel.value = state.current.subject_id ?? '';
}

function showMenu(anchor, items) {
  closeMenus();
  const menu = document.createElement('div');
  menu.className = 'menu floating-menu';
  for (const it of items) {
    const b = document.createElement('button');
    b.textContent = it.label;
    if (it.danger) b.classList.add('danger');
    b.onclick = (e) => {
      e.stopPropagation();
      closeMenus();
      it.run();
    };
    menu.append(b);
  }
  // Dans une fenêtre modale, le menu doit être à l'intérieur (sinon il reste caché derrière elle).
  (anchor.closest('dialog') || document.body).append(menu);
  const r = anchor.getBoundingClientRect();
  menu.style.top = `${r.bottom + 4}px`;
  menu.style.left = `${Math.max(8, Math.min(r.left, innerWidth - 230))}px`;
  setTimeout(() => document.addEventListener('click', closeMenus, { once: true }), 0);
}

function closeMenus() {
  $$('.floating-menu').forEach((m) => m.remove());
  $('#menu-export').hidden = true;
}

function subjectMenu(anchor, subject) {
  const nCM = subjectCMs(subject.id).length;
  showMenu(anchor, [
    ...(nCM >= 2 ? [{ label: `📚 Fiche de révision de tous les CM (${nCM})`, run: () => openSubjectSheet(subject.id) }] : []),
    { label: '⬆ Importer un cours ici', run: () => openImport([], subject.id) },
    { label: '＋ Nouvelle note ici', run: () => newNote(subject.id) },
    { label: '📅 Ajouter un examen', run: () => openEventDialog(null, { subject_id: subject.id }) },
    { label: '🃏 Réviser ses flashcards', run: () => { state.reviewSubject = subject.id; go('#review'); } },
    { label: '✎ Modifier (nom, semestre, couleur)', run: () => openSubjectDialog(subject) },
    { label: '🗑 Supprimer la matière', danger: true, run: () => deleteSubject(subject) },
  ]);
}

const SUBJECT_COLORS = [
  '#6366f1', '#ec4899', '#f59e0b', '#10b981', '#06b6d4', '#8b5cf6', '#ef4444', '#84cc16',
  '#0ea5e9', '#f97316', '#14b8a6', '#a855f7', '#e11d48', '#65a30d', '#2563eb', '#d97706',
];

const subjectDlg = { subject: null, color: SUBJECT_COLORS[0], resolve: null };

/** Fenêtre de création / modification d'une matière. Renvoie la matière enregistrée (ou null). */
function openSubjectDialog(subject = null, defaults = {}) {
  subjectDlg.subject = subject;
  $('#subj-heading').textContent = subject ? 'Modifier la matière' : 'Nouvelle matière';
  $('#subj-name').value = subject?.name || '';
  const sems = new Set(['', ...Array.from({ length: 10 }, (_, i) => `S${i + 1}`), ...state.subjects.map((s) => s.semester || '')]);
  $('#subj-semester').innerHTML = [...sems]
    .sort((a, b) => semNum(a) - semNum(b))
    .map((s) => `<option value="${s}">${s ? semLabel(s) : 'Aucun semestre'}</option>`)
    .join('');
  $('#subj-semester').value = subject ? subject.semester || '' : defaults.semester ?? currentSemester();
  subjectDlg.color = subject?.color || SUBJECT_COLORS[state.subjects.length % SUBJECT_COLORS.length];
  renderSwatches();
  $('#dlg-subject').showModal();
  setTimeout(() => $('#subj-name').focus(), 30);
  return new Promise((resolve) => (subjectDlg.resolve = resolve));
}

function renderSwatches() {
  $('#subj-colors').innerHTML = SUBJECT_COLORS.map(
    (c) => `<button type="button" data-c="${c}" style="background:${c}" class="${c === subjectDlg.color ? 'active' : ''}" title="${c}"></button>`,
  ).join('');
}

async function saveSubjectDialog() {
  const name = $('#subj-name').value.trim();
  if (!name) {
    toast('Donne un nom à la matière.');
    $('#subj-name').focus();
    return;
  }
  const body = { name, semester: $('#subj-semester').value, color: subjectDlg.color };
  try {
    const s = subjectDlg.subject
      ? await api(`/api/subjects/${subjectDlg.subject.id}`, { method: 'PATCH', body })
      : await api('/api/subjects', { method: 'POST', body });
    $('#dlg-subject').close();
    await loadLibrary();
    toast(subjectDlg.subject ? 'Matière modifiée.' : `Matière « ${s.name} » créée.`);
    subjectDlg.resolve?.(s);
    subjectDlg.resolve = null;
  } catch (e) {
    toast(e.message, { error: true });
  }
}

const createSubject = () => openSubjectDialog(null);

async function deleteSubject(subject) {
  if (!confirm(`Supprimer la matière « ${subject.name} » ?\nSes documents sont conservés (rangés dans « Sans matière »).`)) return;
  await api(`/api/subjects/${subject.id}`, { method: 'DELETE' });
  await loadLibrary();
}

// ================================================================== navigation
function go(hash) {
  if (location.hash === hash) route();
  else location.hash = hash;
}

async function route() {
  setNav(false); // téléphone : on referme la bibliothèque après un choix
  const m = location.hash.match(/^#doc\/(\d+)/);
  if (m) return openDoc(+m[1]);
  await leaveDoc();
  if (location.hash.startsWith('#review')) return showReviewView();
  if (location.hash.startsWith('#calendar')) return showCalendarView();
  return showHome();
}

function showView(name) {
  for (const v of ['home', 'doc', 'review', 'calendar']) $(`#view-${v}`).hidden = v !== name;
  $('#app').classList.toggle('wide', name === 'calendar'); // le calendrier prend la place du panneau IA
  $('#btn-calendar').classList.toggle('active', name === 'calendar');
  $('#btn-review-all').classList.toggle('active', name === 'review');
  if (name !== 'doc') liveWhere(); // (un document s'annonce une fois chargé, dans openDoc)
}

async function leaveDoc() {
  await flushSave();
  live.doc = null;
  live.carets.clear();
  drawCarets();
  state.current = null;
  state.keyMode = null;
  highlightActive();
  refreshAIPanel();
}

// ================================================================== accueil
async function showHome() {
  showView('home');
  let s;
  try {
    s = await api('/api/stats');
  } catch (e) {
    toast(e.message, { error: true });
    return;
  }
  state.stats = s;
  updateDueBadge(s.due_cards);
  const k = s.kinds || {};
  const generated = Object.entries(k)
    .filter(([kind]) => ![...SOURCE_KINDS, 'note'].includes(kind))
    .reduce((a, [, n]) => a + n, 0);
  const imported = SOURCE_KINDS.reduce((a, kind) => a + (k[kind] || 0), 0);
  const stat = (num, label, href) =>
    `<div class="stat${href ? ' clickable' : ''}" ${href ? `data-href="${href}"` : ''}><div class="num">${num}</div><div class="lbl">${label}</div></div>`;
  $('#home-stats').innerHTML = [
    stat(imported, `cours importés · ${k.cours || 0} CM, ${k.td || 0} TD, ${k.tp || 0} TP`),
    stat(generated, 'fiches, QCM, exercices… générés'),
    stat(s.due_cards, "cartes à réviser aujourd'hui", '#review'),
    stat(s.total_cards, 'flashcards au total'),
  ].join('');
  $$('#home-stats [data-href]').forEach((el) => (el.onclick = () => go(el.dataset.href)));

  await refreshUpcoming();
  renderUpcomingList($('#home-exams'), (state.upcoming || []).slice(0, 5));

  const recent = $('#home-recent');
  recent.innerHTML = s.recent.length
    ? s.recent
        .map((d) => `<li><span>${KINDS[d.kind]?.icon || '📄'}</span><a href="#doc/${d.id}">${esc(d.title)}</a><span class="muted small">${fmtDate(d.updated_at)}</span></li>`)
        .join('')
    : '<li class="empty">Rien pour l\'instant.</li>';

  const att = $('#home-attempts');
  att.innerHTML = s.attempts.length
    ? s.attempts
        .map((a) => {
          const pct = Math.round((100 * a.score) / a.total);
          const cls = pct >= 70 ? 'good' : pct >= 50 ? 'mid' : 'bad';
          return `<li><a href="#doc/${a.document_id}">${esc(a.title)}</a><span class="score ${cls}">${a.score}/${a.total}</span><span class="muted small">${fmtDate(a.created_at)}</span></li>`;
        })
        .join('')
    : '<li class="empty">Génère un QCM depuis un cours pour t\'entraîner.</li>';

  $('#home-warning')?.remove();
  if (state.health && !state.health.ollama) {
    const w = document.createElement('div');
    w.id = 'home-warning';
    w.className = 'card warn-card';
    w.innerHTML = "<b>⚠ Ollama n'est pas joignable.</b> Lance l'application Ollama pour activer l'IA (l'import et l'éditeur fonctionnent sans).";
    $('#home-stats').before(w);
  }
}

function updateDueBadge(n) {
  $('#due-count').textContent = n;
  $('#btn-review-all').classList.toggle('has', n > 0);
  $('#m-due').textContent = n > 99 ? '99+' : n;
  $('#m-due').hidden = !n;
}

async function refreshDueCount() {
  try {
    const s = await api('/api/stats');
    state.stats = s;
    updateDueBadge(s.due_cards);
  } catch {
    /* hors ligne */
  }
}

// ================================================================== document
async function openDoc(id, { force = false } = {}) {
  if (!force && state.current?.id === id) {
    showView('doc');
    liveWhere();
    return;
  }
  await flushSave();
  let doc;
  try {
    doc = await api(`/api/documents/${id}`);
  } catch (e) {
    toast(e.message, { error: true });
    go('#');
    return;
  }
  const sameDoc = state.current?.id === id;
  state.current = doc;
  state.dirty = false;
  showView('doc');
  $('#doc-title').value = doc.title;
  updateKindUI();
  fillSubjectSelects();
  editor.value = doc.content;
  if (!sameDoc) {
    editor.scrollTop = 0;
    preview.scrollTop = 0;
    editor.setSelectionRange(0, 0);
  }
  liveOpenDoc(doc);
  liveWhere(); // rejoint le document : version en direct + curseurs des autres
  renderDocPresence();
  $('#btn-source').hidden = !doc.source_file;
  $('#doc-source').textContent = doc.source_name ? `Source : ${doc.source_name}` : '';
  setSaveState('');
  updateStats();
  updateScanBanner();
  updateLangUI();
  closeFind();
  setupDocTabs(sameDoc ? state.docTab : null);
  highlightActive();
  if (!sameDoc) {
    $('#gen-output').hidden = !state.controllers.gen;
    $('#tool-output').hidden = true;
  }
  refreshAIPanel();
  if (state.pendingFind) {
    const q = state.pendingFind;
    state.pendingFind = null;
    if (state.docTab === 'edit') {
      openFind();
      $('#find-input').value = q;
      computeMatches();
      gotoMatch(1);
    }
  }
}

/** Badge du type de document, ou sélecteur CM / TD / TP / Note pour les documents de cours. */
function updateKindUI() {
  const doc = state.current;
  const kind = KINDS[doc.kind] || { icon: '📄', label: doc.kind };
  const editable = SOURCE_KINDS.includes(doc.kind) || doc.kind === 'note';
  $('#doc-kind').textContent = `${kind.icon} ${kind.label}`;
  $('#doc-kind').hidden = editable;
  $('#doc-category').hidden = !editable;
  if (editable) $('#doc-category').value = doc.kind;
  // Compte-rendu : choix du sujet de TD / TP auquel il répond.
  const parentSel = $('#doc-parent');
  parentSel.hidden = doc.kind !== 'rendu';
  if (doc.kind === 'rendu') {
    const subjects = state.docs.filter((d) => ['td', 'tp'].includes(d.kind) && d.subject_id === doc.subject_id && d.id !== doc.id);
    parentSel.innerHTML =
      '<option value="">📎 Sujet : aucun</option>' +
      subjects.map((d) => `<option value="${d.id}">📎 ${esc(KINDS[d.kind].label)} : ${esc(d.title)}</option>`).join('');
    parentSel.value = subjects.some((d) => d.id === doc.parent_id) ? String(doc.parent_id) : '';
  }
}

async function changeParent() {
  const doc = state.current;
  if (!doc) return;
  const v = $('#doc-parent').value;
  const parent_id = v ? +v : null;
  try {
    await api(`/api/documents/${doc.id}`, { method: 'PATCH', body: { parent_id } });
  } catch (e) {
    toast(e.message, { error: true });
    return;
  }
  doc.parent_id = parent_id;
  await loadLibrary();
  toast(parent_id ? 'Compte-rendu rattaché à son sujet.' : 'Compte-rendu détaché du sujet.');
}

// ------------------------------------------------------------------ traduction
function updateLangUI() {
  const doc = state.current;
  const translated = Boolean(doc?.data && doc.data.translation);
  $('#lang-banner').hidden = !doc || translated || doc.lang !== 'en' || ['qcm', 'flashcards'].includes(doc.kind);
  $('#doc-translated').hidden = !translated;
  $('#lang-status').textContent = "Les termes techniques garderont leur nom anglais entre parenthèses, ex. « noyau (kernel) ».";
}

async function runTranslate() {
  const doc = state.current;
  if (!doc || state.controllers.translate) return;
  await flushSave();
  const ctrl = new AbortController();
  state.controllers.translate = ctrl;
  $('#btn-translate').disabled = true;
  editor.readOnly = true;
  let chars = 0;
  let base = 'Traduction en cours…';
  let done = null;
  try {
    await streamNDJSON(
      `/api/documents/${doc.id}/translate`,
      {},
      (ev) => {
        if (ev.type === 'status') {
          base = ev.message;
          chars = 0;
        } else if (ev.type === 'token') {
          chars += ev.text.length;
        } else if (ev.type === 'done') {
          done = ev;
        }
        $('#lang-status').textContent = chars ? `${base} (${chars.toLocaleString('fr-FR')} caractères)` : base;
      },
      ctrl.signal,
    );
    if (!done) throw new Error("La traduction s'est interrompue.");
    toast('Cours traduit en français ✔');
    await loadLibrary();
    if (state.current?.id === doc.id) await openDoc(doc.id, { force: true });
  } catch (e) {
    if (e.name !== 'AbortError') toast(e.message, { error: true });
    updateLangUI();
  } finally {
    state.controllers.translate = null;
    $('#btn-translate').disabled = false;
    if (state.docTab !== 'text') editor.readOnly = false;
  }
}

async function untranslate() {
  const doc = state.current;
  if (!doc) return;
  if (!confirm('Revenir à la version anglaise d\'origine ?\nLes modifications faites sur la traduction seront perdues.')) return;
  try {
    await api(`/api/documents/${doc.id}/untranslate`, { method: 'POST' });
  } catch (e) {
    toast(e.message, { error: true });
    return;
  }
  await loadLibrary();
  await openDoc(doc.id, { force: true });
  toast('Version anglaise restaurée.');
}

async function changeKind() {
  const doc = state.current;
  if (!doc) return;
  const kind = $('#doc-category').value;
  try {
    await api(`/api/documents/${doc.id}`, { method: 'PATCH', body: { kind } });
  } catch (e) {
    toast(e.message, { error: true });
    $('#doc-category').value = doc.kind;
    return;
  }
  doc.kind = kind;
  const entry = state.docs.find((d) => d.id === doc.id);
  if (entry) entry.kind = kind;
  updateKindUI();
  renderLibrary();
  toast(`Classé en ${KINDS[kind].label}.`);
}

function setupDocTabs(keep) {
  const kind = state.current.kind;
  let tabs = null;
  let preferred = null;
  if (kind === 'qcm') tabs = [['quiz', '▶ Jouer'], ['text', '📄 Texte']];
  else if (kind === 'flashcards') tabs = [['review', '🧠 Réviser'], ['cards', '🗂 Cartes'], ['text', '📄 Texte']];
  else if (state.current.source_file) {
    tabs = [['edit', '✎ Texte modifiable'], ['original', `📄 Original (${(state.current.source_name || '').split('.').pop().toUpperCase()})`]];
    preferred = store.get('courseTab', 'edit');
  }
  const bar = $('#doc-tabs');
  bar.hidden = !tabs;
  if (tabs) {
    bar.innerHTML = tabs.map(([k, l]) => `<button data-dtab="${k}">${esc(l)}</button>`).join('');
    const has = (k) => k && tabs.some(([t]) => t === k);
    showDocTab(has(keep) ? keep : has(preferred) ? preferred : tabs[0][0]);
  } else {
    showDocTab('edit');
  }
}

async function showDocTab(tab) {
  state.docTab = tab;
  state.keyMode = null;
  $$('#doc-tabs button').forEach((b) => b.classList.toggle('active', b.dataset.dtab === tab));
  $('#pane-editor').hidden = !(tab === 'edit' || tab === 'text');
  $('#pane-quiz').hidden = tab !== 'quiz';
  $('#pane-review').hidden = tab !== 'review';
  $('#pane-cards').hidden = tab !== 'cards';
  $('#pane-original').hidden = tab !== 'original';
  $('#view-doc').classList.toggle('readonly', tab === 'text');
  $('#mode-switch').hidden = tab !== 'edit';
  editor.readOnly = tab === 'text';
  if (state.current.source_file && (tab === 'edit' || tab === 'original')) store.set('courseTab', tab);
  if (tab === 'original') {
    loadOriginal();
  } else if (tab === 'text') {
    if (state.current.kind === 'flashcards' && !liveActive()) {
      const fresh = await api(`/api/documents/${state.current.id}`);
      state.current.content = fresh.content;
      editor.value = fresh.content;
    }
    applyMode('preview', false);
  } else if (tab === 'edit') {
    applyMode(state.mode, false);
  } else if (tab === 'quiz') {
    startQuiz(false);
  } else if (tab === 'review') {
    startReview($('#pane-review'), { documentId: state.current.id });
  } else if (tab === 'cards') {
    renderCardsEditor();
  }
  updateSelectionUI();
}

/** Affiche le fichier d'origine dans l'outil (PDF natif, ou Word/PowerPoint converti en PDF). */
async function loadOriginal() {
  const doc = state.current;
  if (!doc?.source_file) return;
  const frame = $('#orig-frame');
  const pdfName = (doc.source_name || 'cours').replace(/\.[^.]+$/, '') + '.pdf';
  const url = `/api/documents/${doc.id}/preview/${encodeURIComponent(pdfName)}`;
  const ext = (doc.source_name || doc.source_file).split('.').pop().toLowerCase();
  $('#orig-open').href = url;
  $('#orig-download').href = `/api/documents/${doc.id}/source`;
  $('#orig-download').setAttribute('download', doc.source_name || '');
  $('#orig-info').textContent = doc.source_name + (ext === 'pdf' ? '' : ' — affiché en PDF (conversion automatique)');
  if (frame.dataset.doc === String(doc.id) && !frame.hidden) return;

  const loading = $('#orig-loading');
  frame.hidden = true;
  frame.removeAttribute('src');
  loading.hidden = false;
  loading.classList.remove('error');
  loading.querySelector('.spinner').hidden = false;
  $('#orig-msg').textContent =
    ext === 'pdf' ? 'Chargement du PDF…' : `Conversion du fichier ${ext.toUpperCase()} en PDF… (quelques secondes la première fois)`;
  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error(await errorMessage(res));
    await res.blob();
  } catch (e) {
    if (state.current?.id !== doc.id) return;
    loading.classList.add('error');
    loading.querySelector('.spinner').hidden = true;
    const stale = await checkServerVersion();
    $('#orig-msg').textContent = stale
      ? "⚠ Le serveur tourne sur une ancienne version : double-clique sur lancer.bat pour le mettre à jour, puis recharge la page."
      : `⚠ ${e.message} — utilise « Télécharger le fichier » pour l'ouvrir.`;
    return;
  }
  if (state.current?.id !== doc.id || state.docTab !== 'original') return;
  frame.src = url;
  frame.dataset.doc = String(doc.id);
  frame.hidden = false;
  loading.hidden = true;
}

function applyMode(mode, remember = true) {
  if (remember) {
    state.mode = mode;
    store.set('mode', mode);
  }
  // Sur téléphone, pas de place pour l'éditeur et l'aperçu côte à côte : on lit l'aperçu.
  const shown = mode === 'split' && isPhone() ? 'preview' : mode;
  const split = $('#editor-split');
  split.classList.remove('mode-edit', 'mode-split', 'mode-preview');
  split.classList.add('mode-' + shown);
  $$('#mode-switch button').forEach((b) => b.classList.toggle('active', b.dataset.mode === shown));
  if (shown !== 'edit') renderPreviewNow();
}

// ------------------------------------------------------------------ téléphone
const phoneQuery = window.matchMedia('(max-width: 760px)');
const isPhone = () => phoneQuery.matches;

/** Bibliothèque en tiroir (téléphone). */
function setNav(open) {
  $('#app').classList.toggle('nav-open', !!open && isPhone());
}

/** Téléphone : formules et tableaux trop larges sont réduits pour tenir dans l'écran (pas de défilement latéral). */
function fitToWidth(el) {
  const width = (box) => {
    const cs = getComputedStyle(box);
    return box.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
  };
  const scale = (ratio) => Math.max(ratio * 0.97, 0.45);
  // Formules centrées ($$ … $$) : une seule ligne, jamais coupée.
  for (const f of el.querySelectorAll('.katex-display')) {
    f.style.fontSize = '';
    if (!isPhone() || !f.clientWidth) continue;
    const ratio = f.clientWidth / f.scrollWidth;
    if (ratio < 1) f.style.fontSize = scale(ratio) * 100 + '%';
  }
  // Formules dans le texte : KaTeX les coupe aux « = » et « + », mais un morceau (une fraction…) peut rester trop large.
  const avail = isPhone() ? width(el) : 0;
  for (const k of el.querySelectorAll('.katex')) {
    if (k.closest('.katex-display')) continue;
    k.style.fontSize = '';
    if (avail <= 0) continue;
    const box = k.parentElement.closest('p, li, td, th, blockquote, dd, h1, h2, h3, h4, h5, h6') || el;
    const room = Math.min(avail, width(box) || avail);
    const widest = Math.max(0, ...[...k.querySelectorAll('.katex-html > .base')].map((b) => b.getBoundingClientRect().width));
    if (widest > room) k.style.fontSize = scale(room / widest) * 1.21 + 'em'; // KaTeX : 1.21em par défaut
  }
  // Tableaux : coupés aux syllabes ; trop larges quand même → police réduite, et en dernier recours coupure n'importe où.
  for (const t of el.querySelectorAll('table')) {
    t.style.fontSize = '';
    t.classList.remove('squeeze');
    const w = t.getBoundingClientRect().width;
    if (avail <= 0 || w <= avail + 1) continue;
    const size = parseFloat(getComputedStyle(t).fontSize);
    t.style.fontSize = Math.max(size * (avail / w) * 0.97, 10.5) + 'px';
    if (t.getBoundingClientRect().width > avail + 1) t.classList.add('squeeze');
  }
}

// iPhone/iPad : pas de zoom automatique quand on touche un champ (le zoom à deux doigts reste possible).
if (/iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1)) {
  const vp = document.querySelector('meta[name=viewport]');
  if (vp && !/maximum-scale/.test(vp.content)) vp.content += ', maximum-scale=1';
}

let previewSeq = 0;
async function renderPreviewNow() {
  if (!state.current || $('#editor-split').classList.contains('mode-edit')) return;
  const mine = ++previewSeq;
  try {
    const html = await renderMarkdown(editor.value);
    if (mine !== previewSeq) return;
    preview.innerHTML = html || '<p class="muted">Aperçu vide.</p>';
    typeset(preview);
    fitToWidth(preview);
  } catch {
    /* le serveur ne répond pas : on garde l'aperçu */
  }
}
const renderPreview = debounce(renderPreviewNow, 250);

function setSaveState(text) {
  $('#save-state').textContent = text;
}

const scheduleSave = debounce(() => saveNow(), 800);

/** Enregistrement classique (sans le canal temps réel : hors connexion, ou en attendant d'être relié). */
async function saveNow() {
  const doc = state.current;
  if (!doc || !state.dirty) return;
  if (liveActive()) {
    state.dirty = false;
    livePush();
    return;
  }
  const content = editor.value;
  state.dirty = false;
  setSaveState('Enregistrement…');
  const saving = (state.saving = api(`/api/documents/${doc.id}`, { method: 'PATCH', body: { content } }));
  try {
    await saving;
    doc.content = content;
    // Point de départ commun si le canal temps réel revient : seules les frappes suivantes seront fusionnées.
    if (live.doc?.id === doc.id && !live.doc.joined) Object.assign(live.doc, { server: content, known: content, inflight: null });
    if (state.current === doc) setSaveState('Enregistré ✓');
  } catch (e) {
    state.dirty = true;
    setSaveState('⚠ Non enregistré');
    toast('Enregistrement impossible : ' + e.message, { error: true });
  } finally {
    if (state.saving === saving) state.saving = null;
  }
}

async function flushSave() {
  if (state.dirty) await saveNow();
  await liveSettled();
}

function updateStats() {
  const t = editor.value;
  const words = (t.match(/\S+/g) || []).length;
  const nf = (n) => n.toLocaleString('fr-FR');
  $('#doc-stats').textContent =
    `${nf(words)} mots · ${nf(t.length)} caractères · ~${nf(Math.ceil(t.length / 3.3))} tokens · ${Math.max(1, Math.round(words / 220))} min de lecture`;
}

function updateScanBanner() {
  const pages = state.current?.scanned_pages || [];
  $('#scan-banner').hidden = !pages.length;
  $('#scan-count').textContent = pages.length;
  $('#ocr-status').textContent = '';
}

const saveTitle = debounce(async () => {
  const doc = state.current;
  if (!doc) return;
  const title = $('#doc-title').value.trim() || 'Sans titre';
  try {
    await api(`/api/documents/${doc.id}`, { method: 'PATCH', body: { title } });
    doc.title = title;
    const entry = state.docs.find((d) => d.id === doc.id);
    if (entry) entry.title = title;
    const item = $(`.doc-item[data-id="${doc.id}"] .name`);
    if (item) item.textContent = title;
  } catch (e) {
    toast(e.message, { error: true });
  }
}, 600);

async function changeSubject() {
  const doc = state.current;
  if (!doc) return;
  const v = $('#doc-subject').value;
  const subject_id = v ? +v : null;
  await api(`/api/documents/${doc.id}`, { method: 'PATCH', body: { subject_id } });
  doc.subject_id = subject_id;
  await loadLibrary();
  updateKindUI();
}

async function newNote(subjectId) {
  const sid = subjectId !== undefined ? subjectId : state.current?.subject_id ?? null;
  const d = await api('/api/documents', {
    method: 'POST',
    body: { title: 'Nouvelle note', content: '# Nouvelle note\n\n', kind: 'note', subject_id: sid },
  });
  await loadLibrary();
  go(`#doc/${d.id}`);
  setTimeout(() => {
    $('#doc-title').focus();
    $('#doc-title').select();
  }, 80);
}

async function deleteDoc() {
  const d = state.current;
  if (!d) return;
  if (!confirm(`Supprimer « ${d.title} » ?\nCette action est définitive (les documents générés à partir de lui sont conservés).`)) return;
  try {
    await api(`/api/documents/${d.id}`, { method: 'DELETE' });
  } catch (e) {
    toast(e.message, { error: true });
    return;
  }
  state.dirty = false;
  state.current = null;
  delete state.chats[d.id];
  await loadLibrary();
  go('#');
  toast('Document supprimé.');
}

function exportDoc(kind) {
  const d = state.current;
  if (!d) return;
  if (kind === 'md') location.href = `/api/documents/${d.id}/export?format=md`;
  else if (kind === 'html') window.open(`/api/documents/${d.id}/export?format=html`, '_blank');
  else if (kind === 'copy')
    navigator.clipboard.writeText(editor.value).then(
      () => toast('Texte copié dans le presse-papiers.'),
      () => toast('Copie impossible.', { error: true }),
    );
}

// ================================================================== temps réel
/* Présence (qui est connecté, où), modifications des autres en direct et édition à plusieurs.
 * Le texte voyage sous forme d'opérations (comme ot.js) : liste de composantes — entier > 0 = garder
 * n caractères, chaîne = insérer, entier < 0 = supprimer n caractères (positions JavaScript, UTF-16).
 * Le serveur (app/live.py) ordonne les opérations et transforme celles écrites en même temps. */
const OT = {
  push(op, c) {
    const last = op[op.length - 1];
    if (typeof c === 'string') {
      if (!c) return;
      if (typeof last === 'string') op[op.length - 1] = last + c;
      else if (typeof last === 'number' && last < 0) {
        if (typeof op[op.length - 2] === 'string') op[op.length - 2] += c;
        else op.splice(op.length - 1, 0, c);
      } else op.push(c);
    } else if (c) {
      if (typeof last === 'number' && (last > 0) === (c > 0)) op[op.length - 1] = last + c;
      else op.push(c);
    }
  },
  baseLen: (op) => op.reduce((n, c) => (typeof c === 'string' ? n : n + Math.abs(c)), 0),
  noop: (op) => op.every((c) => typeof c === 'number' && c > 0),
  apply(text, op) {
    let i = 0;
    let out = '';
    for (const c of op) {
      if (typeof c === 'string') out += c;
      else if (c > 0) {
        out += text.slice(i, i + c);
        i += c;
      } else i -= c;
      if (i > text.length) throw new Error('opération plus longue que le texte');
    }
    if (i !== text.length) throw new Error('opération incomplète');
    return out;
  },
  /** op1 et op2 partent du même texte → [op1', op2'] (à position égale, op1 insère en premier). */
  transform(op1, op2) {
    if (OT.baseLen(op1) !== OT.baseLen(op2)) throw new Error('opérations sur des textes différents');
    const p1 = [];
    const p2 = [];
    let i1 = 0;
    let i2 = 0;
    let a = op1[i1++];
    let b = op2[i2++];
    while (a !== undefined || b !== undefined) {
      if (typeof a === 'string') {
        OT.push(p1, a);
        OT.push(p2, a.length);
        a = op1[i1++];
        continue;
      }
      if (typeof b === 'string') {
        OT.push(p1, b.length);
        OT.push(p2, b);
        b = op2[i2++];
        continue;
      }
      if (a === undefined || b === undefined) throw new Error('opérations incompatibles');
      if (a > 0 && b > 0) {
        const m = Math.min(a, b);
        OT.push(p1, m);
        OT.push(p2, m);
        a = a - m || op1[i1++];
        b = b - m || op2[i2++];
      } else if (a < 0 && b < 0) {
        const m = Math.min(-a, -b);
        a = a + m || op1[i1++];
        b = b + m || op2[i2++];
      } else if (a < 0) {
        const m = Math.min(-a, b);
        OT.push(p1, -m);
        a = a + m || op1[i1++];
        b = b - m || op2[i2++];
      } else {
        const m = Math.min(a, -b);
        OT.push(p2, -m);
        a = a - m || op1[i1++];
        b = b + m || op2[i2++];
      }
    }
    return [p1, p2];
  },
  /** Position d'un curseur après l'opération. */
  index(op, index) {
    let n = index;
    for (const c of op) {
      if (typeof c === 'string') n += c.length;
      else if (c > 0) index -= c;
      else {
        n -= Math.min(index, -c);
        index += c;
      }
      if (index < 0) break;
    }
    return n;
  },
  /** Opération qui transforme a en b (une seule zone modifiée, sans couper un émoji en deux). */
  diff(a, b) {
    const max = Math.min(a.length, b.length);
    let p = 0;
    while (p < max && a.charCodeAt(p) === b.charCodeAt(p)) p++;
    let s = 0;
    while (s < max - p && a.charCodeAt(a.length - 1 - s) === b.charCodeAt(b.length - 1 - s)) s++;
    const high = (c) => c >= 0xd800 && c <= 0xdbff;
    const low = (c) => c >= 0xdc00 && c <= 0xdfff;
    if (p > 0 && high(a.charCodeAt(p - 1))) p--;
    if (s > 0 && low(a.charCodeAt(a.length - s))) s--;
    const op = [];
    OT.push(op, p);
    OT.push(op, b.slice(p, b.length - s));
    OT.push(op, -(a.length - p - s));
    OT.push(op, s);
    return op;
  },
};

const live = {
  ws: null,
  open: false,
  stop: false, // déconnexion volontaire (fermeture de session)
  retry: 0,
  retryTimer: 0,
  pingTimer: 0,
  key: Math.random().toString(36).slice(2, 12), // cet onglet, d'une reconnexion à l'autre
  me: null,
  users: [], // un élément par onglet ouvert : { cid, id, name, color, view, doc, typing }
  where: '',
  doc: null, // { id, rev, server, known, inflight, joined } — voir livePush
  seq: 0,
  queue: null, // messages mis de côté pendant une saisie en cours (clavier de téléphone)
  queueTimer: 0,
  carets: new Map(), // curseurs des autres dans ce document : cid → { a, b, name, color, at }
  lastValue: '',
  textVersion: 0,
  caretTimer: 0,
  caretSent: '',
};

/** L'éditeur est synchronisé en direct avec le serveur (sinon : enregistrement classique). */
const liveActive = () => !!(live.open && live.doc?.joined && live.doc.id === state.current?.id);

function liveConnect() {
  if (live.ws || live.stop || !('WebSocket' in window)) return;
  clearTimeout(live.retryTimer);
  const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/api/live?key=${live.key}`);
  live.ws = ws;
  ws.onopen = () => {
    live.open = true;
    live.retry = 0;
    live.where = '';
    liveWhere();
    clearInterval(live.pingTimer);
    live.pingTimer = setInterval(() => liveSend({ t: 'ping' }), 25000); // Cloudflare coupe au bout de 100 s sans échange
  };
  ws.onmessage = (e) => {
    let m;
    try {
      m = JSON.parse(e.data);
    } catch {
      return;
    }
    liveReceive(m);
  };
  ws.onclose = (e) => {
    if (live.ws !== ws) return;
    live.ws = null;
    live.open = false;
    clearInterval(live.pingTimer);
    if (live.doc) live.doc.joined = false;
    live.users = [];
    live.carets.clear();
    renderPresence();
    drawCarets();
    if (live.stop || e.code === 4401 || e.code === 4403) return; // session expirée : reconnexion après la connexion
    const delay = [500, 1000, 2000, 5000, 10000][Math.min(live.retry++, 4)];
    live.retryTimer = setTimeout(liveConnect, delay);
  };
}

function liveSend(m) {
  if (!live.open || live.ws?.readyState !== WebSocket.OPEN) return false;
  live.ws.send(JSON.stringify(m));
  return true;
}

/** Indique au serveur où l'on est (accueil, calendrier, document…) — et rejoint le document ouvert. */
function liveWhere() {
  const view = !$('#view-doc').hidden && state.current ? 'doc' : !$('#view-calendar').hidden ? 'calendar' : !$('#view-review').hidden ? 'review' : 'home';
  const doc = view === 'doc' ? state.current.id : null;
  const key = `${view}:${doc}`;
  if (key === live.where && !(doc && live.doc && !live.doc.joined)) return;
  if (liveSend({ t: 'where', view, doc })) live.where = key;
}

function liveOpenDoc(doc) {
  live.doc = { id: doc.id, rev: 0, server: doc.content, known: doc.content, inflight: null, joined: false };
  live.carets.clear();
  live.lastValue = editor.value;
  live.caretSent = '';
  drawCarets();
}

function liveReceive(m) {
  if (live.queue && ['joined', 'ack', 'op', 'caret'].includes(m.t)) {
    live.queue.push(m); // une saisie est en cours (mot en composition) : on l'applique juste après
    return;
  }
  switch (m.t) {
    case 'welcome':
      live.me = m.me;
      break;
    case 'presence':
      live.users = m.users || [];
      renderPresence();
      break;
    case 'joined':
      liveJoined(m);
      break;
    case 'ack':
      liveAck(m);
      break;
    case 'op':
      liveRemoteOp(m);
      break;
    case 'caret':
      liveRemoteCaret(m);
      break;
    case 'gone':
      liveGone(m);
      break;
    case 'changed':
      liveChanged(m);
      break;
    case 'error':
      toast(m.message, { error: true });
      break;
  }
}

function liveHold(on) {
  clearTimeout(live.queueTimer);
  if (on) {
    live.queue = live.queue || [];
    live.queueTimer = setTimeout(() => liveHold(false), 4000); // filet de sécurité
    return;
  }
  const q = live.queue || [];
  live.queue = null;
  q.forEach(liveReceive);
  if (liveActive()) livePush();
}

/** Version de référence reçue (ouverture, reconnexion) : on y fusionne ce qui a été tapé entre-temps. */
function liveJoined(m) {
  const d = live.doc;
  if (!d || d.id !== m.doc) return;
  if (state.saving) {
    state.saving.finally(() => liveSend({ t: 'sync', doc: d.id }));
    return;
  }
  let base = d.known;
  if (d.inflight) {
    const applied = m.seq != null ? m.seq >= d.inflight.seq : m.content === d.known;
    if (!applied) base = d.server;
  }
  let value = m.content;
  try {
    const mine = OT.diff(base, editor.value);
    if (!OT.noop(mine)) value = OT.apply(editor.value, OT.transform(mine, OT.diff(base, m.content))[1]);
  } catch {
    value = m.content;
  }
  Object.assign(d, { rev: m.rev, server: m.content, known: m.content, inflight: null, joined: true });
  state.dirty = false;
  if (value !== editor.value) applyEditorOp(OT.diff(editor.value, value));
  livePush();
  liveSendCaret();
}

/** Envoie ce qui a été tapé (une opération à la fois ; la suite part à l'accusé de réception). */
function livePush() {
  const d = live.doc;
  if (!d || !d.joined || d.inflight || !live.open || live.queue || d.id !== state.current?.id) return;
  const op = OT.diff(d.known, editor.value);
  if (OT.noop(op)) return;
  d.inflight = { op, seq: ++live.seq };
  d.known = editor.value;
  if (!liveSend({ t: 'op', doc: d.id, rev: d.rev, seq: d.inflight.seq, op })) {
    d.inflight = null;
    d.known = d.server;
    return;
  }
  setSaveState('Enregistrement…');
}

function liveAck(m) {
  const d = live.doc;
  if (!d || d.id !== m.doc || !d.inflight) return;
  if (m.rev !== d.rev + 1) return liveResync();
  d.rev = m.rev;
  d.server = d.known;
  d.inflight = null;
  if (state.current) state.current.content = d.server;
  if (editor.value !== d.known) livePush();
  else {
    setSaveState('Enregistré ✓');
    liveSendCaret();
  }
}

function liveRemoteOp(m) {
  const d = live.doc;
  if (!d || d.id !== m.doc || !d.joined) return;
  if (m.rev !== d.rev + 1) return liveResync();
  try {
    let r = m.op;
    const server = OT.apply(d.server, r);
    if (d.inflight) {
      const [inflight, r2] = OT.transform(d.inflight.op, r);
      d.inflight.op = inflight;
      r = r2;
    }
    const local = OT.transform(OT.diff(d.known, editor.value), r)[1]; // en tenant compte de ce qui n'est pas encore parti
    d.known = OT.apply(d.known, r);
    d.server = server;
    d.rev = m.rev;
    applyEditorOp(local);
  } catch {
    liveResync();
  }
}

function liveResync() {
  const d = live.doc;
  if (!d) return;
  d.joined = false;
  liveSend({ t: 'sync', doc: d.id });
}

/** Modification venue d'ailleurs : appliquée à l'éditeur en gardant la sélection et le défilement. */
function applyEditorOp(op) {
  const old = editor.value;
  const next = OT.apply(old, op);
  if (next === old) return;
  const { selectionStart: s, selectionEnd: e, selectionDirection: dir, scrollTop } = editor;
  editor.value = next;
  editor.setSelectionRange(OT.index(op, s), OT.index(op, e), dir);
  editor.scrollTop = scrollTop;
  for (const c of live.carets.values()) {
    c.a = OT.index(op, c.a);
    c.b = OT.index(op, c.b);
  }
  live.lastValue = next;
  live.textVersion++;
  renderPreview();
  updateStats();
  if (!$('#find-bar').hidden) computeMatches();
  drawCarets();
}

/** Après une frappe : les curseurs des autres suivent le texte, puis on envoie. */
function liveLocalChange() {
  const prev = live.lastValue;
  const now = editor.value;
  live.lastValue = now;
  live.textVersion++;
  if (live.carets.size && prev !== now) {
    const op = OT.diff(prev, now);
    for (const c of live.carets.values()) {
      c.a = OT.index(op, c.a);
      c.b = OT.index(op, c.b);
    }
    drawCarets();
  }
}

/** Attend que le serveur ait reçu toutes les modifications (avant une génération IA, un changement de page…). */
function liveSettled(ms = 3000) {
  return new Promise((resolve) => {
    const t0 = Date.now();
    const check = () => {
      const d = live.doc;
      if (!liveActive() || (!d.inflight && d.known === editor.value) || Date.now() - t0 > ms) return resolve();
      livePush();
      setTimeout(check, 40);
    };
    check();
  });
}

function liveSendCaret() {
  if (live.caretTimer) return;
  live.caretTimer = setTimeout(() => {
    live.caretTimer = 0;
    const d = live.doc;
    if (!liveActive() || d.inflight || d.known !== editor.value) return; // repart après l'accusé de réception
    const focused = document.activeElement === editor && !editor.readOnly;
    const m = focused ? { t: 'caret', doc: d.id, rev: d.rev, a: editor.selectionStart, b: editor.selectionEnd } : { t: 'caret', doc: d.id, a: null };
    const sig = `${d.id}:${m.rev}:${m.a}:${m.b}`;
    if (sig !== live.caretSent && liveSend(m)) live.caretSent = sig;
  }, 80);
}

function liveRemoteCaret(m) {
  const d = live.doc;
  if (!d || d.id !== m.doc) return;
  if (m.a == null) {
    live.carets.delete(m.cid);
    return drawCarets();
  }
  if (!d.joined || m.rev !== d.rev) return;
  try {
    let a = m.a;
    let b = m.b;
    if (d.inflight) {
      a = OT.index(d.inflight.op, a);
      b = OT.index(d.inflight.op, b);
    }
    const local = OT.diff(d.known, editor.value);
    a = OT.index(local, a);
    b = OT.index(local, b);
    const u = live.users.find((x) => x.cid === m.cid);
    live.carets.set(m.cid, { a, b, name: u?.name || '…', color: u?.color || '#888', at: Date.now() });
    drawCarets();
  } catch {
    /* curseur périmé : ignoré */
  }
}

function liveGone(m) {
  if (state.current?.id !== m.doc) return;
  toast(`${m.by?.name || "Quelqu'un"} a supprimé ce document.`, { error: true });
  live.goneDoc = m.doc; // (pas de second message pour la même suppression)
  state.dirty = false;
  live.doc = null;
  state.current = null;
  loadLibrary().catch(() => {});
  go('#');
}

// ------------------------------------------------------------------ modifications des autres
const refreshLibrarySoon = debounce(() => loadLibrary().catch(() => {}), 400);
const refreshHomeSoon = debounce(() => {
  if (!$('#view-home').hidden) showHome();
}, 900);
const refreshDueSoon = debounce(refreshDueCount, 1000);

function liveChanged(m) {
  const mine = !!(m.by && state.user && m.by.id === state.user.id);
  if (m.msg && m.by && !mine && !(m.doc && m.doc === live.goneDoc)) toast(`${m.by.name} ${m.msg}`);
  if (m.what === 'docs' || m.what === 'subjects') {
    refreshLibrarySoon();
    if (!mine && m.doc && m.doc === state.current?.id && m.fields?.length) refreshDocMeta();
  }
  if (m.what === 'events') {
    refreshUpcoming();
    if (!$('#view-calendar').hidden) renderCalendar();
  }
  if (m.what === 'cards' && !mine && m.doc === state.current?.id && state.docTab === 'cards' && !$('#pane-cards').contains(document.activeElement)) {
    renderCardsEditor();
  }
  if (m.what === 'cards' || m.what === 'review') refreshDueSoon();
  refreshHomeSoon();
}

/** Titre, matière, type… changés par quelqu'un d'autre sur le document ouvert. */
async function refreshDocMeta() {
  const doc = state.current;
  let fresh;
  try {
    fresh = await api(`/api/documents/${doc.id}`);
  } catch {
    return;
  }
  if (state.current !== doc) return;
  for (const k of ['title', 'kind', 'subject_id', 'parent_id', 'data', 'lang']) doc[k] = fresh[k];
  if (document.activeElement !== $('#doc-title')) $('#doc-title').value = doc.title;
  updateKindUI();
  fillSubjectSelects();
  updateLangUI();
}

// ------------------------------------------------------------------ présence
const initials = (name) => {
  const parts = String(name || '?').split(/[\s._-]+/).filter(Boolean);
  return ((parts[0]?.[0] || '?') + (parts[1]?.[0] || '')).toUpperCase();
};
const avatarHtml = (u, title = '') =>
  `<span class="avatar sm${u.typing ? ' typing' : ''}" style="background:${esc(u.color)}"${title ? ` title="${esc(title)}"` : ''}>${esc(initials(u.name))}</span>`;

function whereLabel(u) {
  if (u.view === 'doc' && u.doc != null) return state.docs.find((d) => d.id === u.doc)?.title || 'un document';
  return { home: 'Accueil', calendar: 'Calendrier des examens', review: 'Révision des flashcards' }[u.view] || 'En ligne';
}

/** Les autres personnes connectées (une entrée par personne, même avec plusieurs onglets). */
function othersOnline(docId) {
  const byUser = new Map();
  for (const u of live.users) {
    if (u.id === state.user?.id || (docId !== undefined && u.doc !== docId)) continue;
    const prev = byUser.get(u.id);
    if (!prev || (u.typing && !prev.typing) || (u.doc != null && prev.doc == null)) byUser.set(u.id, u);
  }
  return [...byUser.values()].sort((a, b) => a.name.localeCompare(b.name, 'fr'));
}

function avatarStack(list, max) {
  const html = list
    .slice(0, max)
    .map((u) => avatarHtml(u, `${u.name} — ${u.typing ? 'écrit dans ' : ''}${whereLabel(u)}`))
    .join('');
  return html + (list.length > max ? `<span class="avatar sm more">+${list.length - max}</span>` : '');
}

function renderPresence() {
  const others = othersOnline();
  const row = $('#online');
  row.hidden = !live.open;
  row.innerHTML = others.length
    ? `<span class="live-dot"></span><span class="lbl">${others.length} en ligne</span><span class="avatar-stack">${avatarStack(others, 5)}</span>`
    : '<span class="live-dot"></span><span class="lbl muted">Personne d\'autre en ligne</span>';
  const m = $('#m-online');
  m.hidden = !others.length;
  m.innerHTML = avatarStack(others, 3);
  renderDocPresence();
  updateLibraryPresence();
}

/** Qui regarde / écrit dans le document ouvert. */
function renderDocPresence() {
  const doc = state.current;
  const here = doc ? othersOnline(doc.id) : [];
  const box = $('#doc-people');
  box.hidden = !here.length;
  box.innerHTML = avatarStack(here, 4);
  const typing = here.filter((u) => u.typing === doc?.id).map((u) => u.name);
  const note = $('#doc-typing');
  note.hidden = !typing.length;
  note.textContent = typing.length === 1 ? `✎ ${typing[0]} écrit…` : `✎ ${typing.slice(0, -1).join(', ')} et ${typing.at(-1)} écrivent…`;
  // Curseurs : nom et couleur à jour ; on retire ceux des personnes parties du document.
  for (const [cid, c] of live.carets) {
    const u = live.users.find((x) => x.cid === cid && x.doc === doc?.id);
    if (!u) live.carets.delete(cid);
    else Object.assign(c, { name: u.name, color: u.color });
  }
  drawCarets();
}

/** Pastilles de couleur à côté des documents ouverts par d'autres. */
function updateLibraryPresence() {
  $$('#library .doc-item .here').forEach((el) => el.remove());
  const byDoc = new Map();
  for (const u of othersOnline()) if (u.doc != null) byDoc.set(u.doc, [...(byDoc.get(u.doc) || []), u]);
  for (const [docId, users] of byDoc) {
    const item = $(`#library .doc-item[data-id="${docId}"]`);
    if (!item) continue;
    const span = document.createElement('span');
    span.className = 'here';
    span.innerHTML = users
      .slice(0, 3)
      .map((u) => `<i class="${u.typing === docId ? 'typing' : ''}" style="background:${esc(u.color)}" title="${esc(u.name)}${u.typing === docId ? ' écrit…' : ''}"></i>`)
      .join('');
    item.append(span);
  }
}

function showPeople(anchor) {
  closeMenus();
  const others = othersOnline();
  const menu = document.createElement('div');
  menu.className = 'menu floating-menu people-menu';
  menu.innerHTML =
    `<div class="people-head">${others.length ? `${others.length} autre${others.length > 1 ? 's' : ''} personne${others.length > 1 ? 's' : ''} en ligne` : "Personne d'autre n'est connecté"}</div>` +
    others
      .map((u) => {
        const doc = u.view === 'doc' && u.doc != null ? u.doc : '';
        const where = `${u.typing ? '✎ écrit dans ' : doc ? '👁 ' : ''}${whereLabel(u)}`;
        return `<button data-doc="${doc}" ${doc ? 'title="Ouvrir ce document"' : 'disabled'}>${avatarHtml(u)}<span class="who"><b>${esc(u.name)}</b><small>${esc(where)}</small></span></button>`;
      })
      .join('');
  menu.querySelectorAll('button[data-doc]').forEach((b) => {
    b.onclick = (e) => {
      e.stopPropagation();
      closeMenus();
      if (b.dataset.doc) go(`#doc/${b.dataset.doc}`);
    };
  });
  menu.onclick = (e) => e.stopPropagation();
  document.body.append(menu);
  const r = anchor.getBoundingClientRect();
  menu.style.top = `${r.bottom + 4}px`;
  menu.style.left = `${Math.max(8, Math.min(r.left, innerWidth - 290))}px`;
  setTimeout(() => document.addEventListener('click', closeMenus, { once: true }), 0);
}

// ------------------------------------------------------------------ curseurs des autres
const caretMirror = document.createElement('div');
let caretFrame = 0;

function drawCarets() {
  if (!caretFrame) caretFrame = requestAnimationFrame(drawCaretsNow);
}

function drawCaretsNow() {
  caretFrame = 0;
  const layer = $('#carets');
  layer.innerHTML = '';
  if (!live.carets.size || !state.current || editor.offsetParent === null || editor.offsetWidth === 0) return;
  Object.assign(layer.style, {
    left: `${editor.offsetLeft}px`,
    top: `${editor.offsetTop}px`,
    width: `${editor.clientWidth}px`,
    height: `${editor.clientHeight}px`,
  });
  const cs = getComputedStyle(editor);
  const padL = parseFloat(cs.paddingLeft) + parseFloat(cs.borderLeftWidth);
  const padT = parseFloat(cs.paddingTop) + parseFloat(cs.borderTopWidth);
  const lh = parseFloat(cs.lineHeight) || parseFloat(cs.fontSize) * 1.5;
  const layout = `${editor.clientWidth}|${live.textVersion}|${cs.fontSize}`;
  if (!caretMirror.isConnected) document.body.append(caretMirror);
  for (const [cid, c] of live.carets) {
    const pos = Math.max(0, Math.min(c.b, editor.value.length));
    if (c.layout !== `${layout}|${pos}`) {
      // Copie invisible du texte, mêmes polices et retours à la ligne, pour trouver la position du curseur.
      Object.assign(caretMirror.style, {
        position: 'absolute', visibility: 'hidden', top: '0', left: '-10000px', whiteSpace: 'pre-wrap',
        overflowWrap: cs.overflowWrap, wordBreak: cs.wordBreak, fontFamily: cs.fontFamily, fontSize: cs.fontSize,
        fontWeight: cs.fontWeight, lineHeight: cs.lineHeight, letterSpacing: cs.letterSpacing, tabSize: cs.tabSize,
        fontVariantLigatures: cs.fontVariantLigatures, fontFeatureSettings: cs.fontFeatureSettings,
        width: `${editor.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight)}px`, padding: '0', border: '0',
      });
      caretMirror.textContent = editor.value.slice(0, pos);
      const mark = document.createElement('span');
      mark.textContent = '​';
      caretMirror.append(mark);
      c.x = mark.offsetLeft;
      c.y = mark.offsetTop;
      c.layout = `${layout}|${pos}`;
    }
    const x = c.x + padL - editor.scrollLeft;
    const y = c.y + padT - editor.scrollTop;
    if (y < -lh || y > editor.clientHeight) continue;
    const el = document.createElement('div');
    el.className = 'rcaret' + (Date.now() - c.at > 5000 ? ' idle' : '') + (y < 18 ? ' below' : '');
    el.style.cssText = `left:${x}px;top:${y}px;height:${lh}px;--c:${c.color}`;
    el.innerHTML = `<span>${esc(c.name)}</span>`;
    layer.append(el);
  }
  caretMirror.textContent = '';
}
setInterval(() => live.carets.size && drawCarets(), 2500); // l'étiquette du nom s'efface après quelques secondes

// ================================================================== éditeur
function getSel() {
  const s = editor.selectionStart;
  const e = editor.selectionEnd;
  return { s, e, text: editor.value.slice(s, e) };
}

/** Remplace une plage en conservant l'historique d'annulation (Ctrl+Z) quand c'est possible. */
function replaceRange(start, end, text, selStart, selEnd) {
  let ok = false;
  if (!editor.readOnly && editor.offsetParent !== null) {
    editor.focus();
    if (document.activeElement === editor) {
      editor.setSelectionRange(start, end);
      try {
        ok = document.execCommand('insertText', false, text);
      } catch {
        ok = false;
      }
    }
  }
  if (!ok) {
    editor.setRangeText(text, start, end, 'end');
    editor.dispatchEvent(new Event('input'));
  }
  if (selStart !== undefined) editor.setSelectionRange(selStart, selEnd ?? selStart);
}

function wrap(before, after = before, placeholder = 'texte') {
  const { s, e, text } = getSel();
  const v = editor.value;
  if (text && v.slice(s - before.length, s) === before && v.slice(e, e + after.length) === after) {
    replaceRange(s - before.length, e + after.length, text, s - before.length, e - before.length);
    return;
  }
  const inner = text || placeholder;
  replaceRange(s, e, before + inner + after, s + before.length, s + before.length + inner.length);
}

function lineRange() {
  const v = editor.value;
  const { s, e } = getSel();
  const start = v.lastIndexOf('\n', s - 1) + 1;
  const endRef = e > s && v[e - 1] === '\n' ? e - 1 : e;
  let end = v.indexOf('\n', endRef);
  if (end < 0) end = v.length;
  return [start, end];
}

function mapLines(fn) {
  const [s, e] = lineRange();
  const lines = editor.value.slice(s, e).split('\n');
  const out = fn(lines).join('\n');
  replaceRange(s, e, out, s, s + out.length);
}

function toggleHeading(level) {
  mapLines((lines) =>
    lines.map((l) => {
      const m = l.match(/^(#{1,6})\s+/);
      const body = l.replace(/^#{1,6}\s+/, '');
      return m && m[1].length === level ? body : '#'.repeat(level) + ' ' + body;
    }),
  );
}

const LIST_RE = /^(\s*)([-*+]\s+\[[ xX]\]\s+|[-*+]\s+|\d+[.)]\s+|>\s?)/;
function markerType(l) {
  if (/^\s*[-*+]\s+\[[ xX]\]\s+/.test(l)) return 'task';
  if (/^\s*[-*+]\s+/.test(l)) return 'ul';
  if (/^\s*\d+[.)]\s+/.test(l)) return 'ol';
  if (/^\s*>/.test(l)) return 'quote';
  return null;
}

function toggleList(kind) {
  mapLines((lines) => {
    const all = lines.every((l) => !l.trim() || markerType(l) === kind);
    let n = 0;
    return lines.map((l) => {
      if (!l.trim()) return l;
      const m = l.match(LIST_RE);
      const indent = m ? m[1] : l.match(/^\s*/)[0];
      const body = m ? l.slice(m[0].length) : l.trimStart();
      if (all) return indent + body;
      n += 1;
      const marker = kind === 'ul' ? '- ' : kind === 'ol' ? `${n}. ` : kind === 'task' ? '- [ ] ' : '> ';
      return indent + marker + body;
    });
  });
}

function insertBlock(text, selFrom, selTo) {
  const { s, e } = getSel();
  const v = editor.value;
  const before = s === 0 ? '' : v[s - 1] !== '\n' ? '\n\n' : s > 1 && v[s - 2] !== '\n' ? '\n' : '';
  const after = e >= v.length ? '\n' : v[e] !== '\n' ? '\n\n' : '';
  const base = s + before.length;
  replaceRange(s, e, before + text + after, base + (selFrom ?? text.length), base + (selTo ?? selFrom ?? text.length));
}

function editorCommand(cmd) {
  if (editor.readOnly) return;
  if ($('#editor-split').classList.contains('mode-preview') && !['find', 'focus'].includes(cmd)) applyMode('split');
  const { s, e, text } = getSel();
  switch (cmd) {
    case 'h1': return toggleHeading(1);
    case 'h2': return toggleHeading(2);
    case 'h3': return toggleHeading(3);
    case 'bold': return wrap('**', '**', 'texte en gras');
    case 'italic': return wrap('*', '*', 'texte en italique');
    case 'strike': return wrap('~~', '~~', 'texte barré');
    case 'mark': return wrap('==', '==', 'texte surligné');
    case 'code': return wrap('`', '`', 'code');
    case 'ul': return toggleList('ul');
    case 'ol': return toggleList('ol');
    case 'task': return toggleList('task');
    case 'quote': return toggleList('quote');
    case 'link': {
      const label = text || 'texte du lien';
      const ins = `[${label}](https://)`;
      return replaceRange(s, e, ins, s + label.length + 3, s + ins.length - 1);
    }
    case 'table': {
      const t = '| Colonne 1 | Colonne 2 | Colonne 3 |\n| --- | --- | --- |\n|  |  |  |\n|  |  |  |';
      return insertBlock(t, 2, 11);
    }
    case 'math':
      if (text.includes('\n')) return wrap('$$\n', '\n$$', 'x^2');
      return wrap('$', '$', 'x^2');
    case 'codeblock': {
      const inner = text || 'code';
      return insertBlock('```\n' + inner + '\n```', 4, 4 + inner.length);
    }
    case 'hr': return insertBlock('---');
    case 'find': return openFind();
    case 'focus': return $('#app').classList.toggle('focus');
    default:
  }
}

function handleEditorKeys(e) {
  const mod = e.ctrlKey || e.metaKey;
  if (mod && !e.altKey) {
    const k = e.key.toLowerCase();
    const map = { b: 'bold', i: 'italic', k: 'link' };
    if (map[k] && !e.shiftKey) {
      e.preventDefault();
      editorCommand(map[k]);
      return;
    }
    if (k === 's') {
      e.preventDefault();
      scheduleSave();
      saveNow();
      return;
    }
    if (k === 'f' || k === 'h') {
      e.preventDefault();
      openFind();
      if (k === 'h') $('#replace-input').focus();
      return;
    }
  }
  if (e.key === 'F3') {
    e.preventDefault();
    gotoMatch(e.shiftKey ? -1 : 1);
    return;
  }
  if (e.key === 'Tab' && !mod) {
    const { s, e: end } = getSel();
    const v = editor.value;
    const lineStart = v.lastIndexOf('\n', s - 1) + 1;
    const multi = v.slice(s, end).includes('\n');
    if (multi || markerType(v.slice(lineStart, v.indexOf('\n', s) < 0 ? v.length : v.indexOf('\n', s)))) {
      e.preventDefault();
      mapLines((lines) => lines.map((l) => (e.shiftKey ? l.replace(/^ {1,2}/, '') : '  ' + l)));
    } else if (!e.shiftKey) {
      e.preventDefault();
      replaceRange(s, end, '    ', s + 4);
    }
    return;
  }
  if (e.key === 'Enter' && !e.shiftKey && !mod && !e.altKey && editor.selectionStart === editor.selectionEnd) {
    const v = editor.value;
    const pos = editor.selectionStart;
    const ls = v.lastIndexOf('\n', pos - 1) + 1;
    const line = v.slice(ls, pos);
    const m = line.match(/^(\s*)([-*+] \[[ xX]\] |[-*+] |(\d+)([.)]) |> )/);
    if (!m) return;
    e.preventDefault();
    if (line.trim() === m[0].trim()) {
      replaceRange(ls, pos, '', ls); // élément vide : on sort de la liste
      return;
    }
    let marker = m[2];
    if (m[3]) marker = `${+m[3] + 1}${m[4]} `;
    marker = marker.replace(/\[[xX]\]/, '[ ]');
    const ins = '\n' + m[1] + marker;
    replaceRange(pos, pos, ins, pos + ins.length);
  }
}

// ------------------------------------------------------------------ rechercher / remplacer
const finder = { matches: [], idx: -1 };

function openFind() {
  if (state.docTab !== 'edit') return;
  if ($('#editor-split').classList.contains('mode-preview')) applyMode('split');
  $('#find-bar').hidden = false;
  const sel = getSel().text;
  if (sel && !sel.includes('\n') && sel.length < 200) $('#find-input').value = sel;
  $('#find-input').focus();
  $('#find-input').select();
  computeMatches();
}

function closeFind() {
  $('#find-bar').hidden = true;
  finder.matches = [];
}

function computeMatches() {
  const q = $('#find-input').value;
  finder.matches = [];
  finder.idx = -1;
  if (q) {
    const cs = $('#find-case').checked;
    const hay = cs ? editor.value : editor.value.toLowerCase();
    const needle = cs ? q : q.toLowerCase();
    let i = hay.indexOf(needle);
    while (i >= 0 && finder.matches.length < 10000) {
      finder.matches.push(i);
      i = hay.indexOf(needle, i + needle.length);
    }
  }
  updateFindCount();
}

function updateFindCount() {
  const n = finder.matches.length;
  $('#find-count').textContent = $('#find-input').value ? (n ? `${finder.idx + 1 > 0 ? finder.idx + 1 : '–'}/${n}` : '0 résultat') : '';
}

function gotoMatch(dir) {
  if (!finder.matches.length) computeMatches();
  const n = finder.matches.length;
  if (!n) return;
  if (finder.idx < 0) {
    const cur = editor.selectionEnd;
    finder.idx = dir > 0 ? finder.matches.findIndex((m) => m >= cur) : finder.matches.findLastIndex((m) => m < editor.selectionStart);
    if (finder.idx < 0) finder.idx = dir > 0 ? 0 : n - 1;
  } else {
    finder.idx = (finder.idx + dir + n) % n;
  }
  const m = finder.matches[finder.idx];
  const len = $('#find-input').value.length;
  editor.blur();
  editor.setSelectionRange(m, m + len);
  editor.focus();
  updateFindCount();
}

function replaceOne() {
  const q = $('#find-input').value;
  if (!q) return;
  const { s, text } = getSel();
  const cs = $('#find-case').checked;
  const same = cs ? text === q : text.toLowerCase() === q.toLowerCase();
  if (same) {
    const r = $('#replace-input').value;
    replaceRange(s, s + text.length, r, s + r.length);
    computeMatches();
    finder.idx = -1;
  }
  gotoMatch(1);
}

function replaceAll() {
  const q = $('#find-input').value;
  if (!q) return;
  const re = new RegExp(regexEscape(q), $('#find-case').checked ? 'g' : 'gi');
  const count = (editor.value.match(re) || []).length;
  if (!count) return toast('Aucune occurrence.');
  const r = $('#replace-input').value;
  const out = editor.value.replace(re, () => r);
  replaceRange(0, editor.value.length, out, 0);
  computeMatches();
  toast(`${count} occurrence(s) remplacée(s).`);
}

// ================================================================== panneau IA
function applyAIOpen() {
  $('#app').classList.toggle('no-ai', !state.aiOpen);
  store.set('aiOpen', state.aiOpen);
}

function setAITab(tab) {
  state.aiTab = tab;
  $$('#ai-tabs button').forEach((b) => b.classList.toggle('active', b.dataset.tab === tab));
  refreshAIPanel();
}

function refreshAIPanel() {
  const hasDoc = !!state.current;
  const tab = state.aiTab;
  $('#ai-generate').hidden = tab !== 'generate' || !hasDoc;
  $('#ai-tools').hidden = tab !== 'tools' || !hasDoc;
  $('#ai-chat').hidden = tab !== 'chat';
  $('#ai-nodoc').hidden = tab === 'chat' || hasDoc;
  if (tab === 'chat') renderChat();
  if (hasDoc) {
    renderScope();
    renderTaskGrid();
  }
  updateSelectionUI();
}

// Portée : le CM ouvert, ou tous les CM de la matière (→ un seul document pour toute la matière).
/** Les CM d'une matière, dans l'ordre des titres (« Chapitre 2 » avant « Chapitre 10 »). */
function subjectCMs(subjectId) {
  if (subjectId == null) return [];
  return state.docs
    .filter((d) => d.subject_id === subjectId && d.kind === 'cours')
    .sort((a, b) => a.title.localeCompare(b.title, 'fr', { numeric: true }));
}

const multiScope = () => !$('#opt-scope-row').hidden && state.scope === 'all';
const chosenCMs = () => subjectCMs(state.current?.subject_id).filter((d) => !state.scopeOff.has(d.id));

function renderScope() {
  const doc = state.current;
  const cms = doc?.kind === 'cours' ? subjectCMs(doc.subject_id) : [];
  const row = $('#opt-scope-row');
  row.hidden = cms.length < 2;
  if (!row.hidden && state.scopeSubject !== doc.subject_id) {
    // Choix mémorisé par matière ; la liste des CM exclus repart de zéro.
    state.scopeSubject = doc.subject_id;
    state.scope = store.get('genScope.' + doc.subject_id, 'doc');
    state.scopeOff = new Set();
  }
  const all = multiScope();
  $('#btn-generate').textContent = all ? `✨ Générer sur ${chosenCMs().length} CM` : '✨ Générer';
  if (row.hidden) return;
  $$('#opt-scope button').forEach((b) => b.classList.toggle('active', b.dataset.scope === state.scope));
  $('#scope-count').textContent = `(${cms.length})`;
  $('#scope-pick').hidden = !all;
  const n = chosenCMs().length;
  $('#scope-summary').textContent = n === cms.length ? `Les ${n} CM sont inclus — choisir…` : `${n} CM sur ${cms.length} inclus — choisir…`;
  $('#scope-list').innerHTML = cms
    .map((d) => `<label class="check" title="${esc(d.title)}"><input type="checkbox" data-cm="${d.id}" ${state.scopeOff.has(d.id) ? '' : 'checked'}> <span>${esc(d.title)}</span></label>`)
    .join('');
}

/** Depuis la bibliothèque : ouvre le premier CM avec « Fiche de révision » sur tous les CM. */
function openSubjectSheet(subjectId) {
  const first = subjectCMs(subjectId)[0];
  if (!first) return;
  state.scopeSubject = subjectId;
  state.scope = 'all';
  state.scopeOff = new Set();
  store.set('genScope.' + subjectId, 'all');
  state.task = 'fiche';
  store.set('task', 'fiche');
  state.aiOpen = true;
  applyAIOpen();
  setAITab('generate');
  go(`#doc/${first.id}`);
}

/** Tâches proposées pour le document ouvert (la relecture n'a de sens que pour un compte-rendu). */
const visibleTasks = () =>
  TASKS.filter((t) => (!t.only || t.only.includes(state.current?.kind)) && !(multiScope() && MULTI_EXCLUDED.includes(t.id)));

function renderTaskGrid() {
  const tasks = visibleTasks();
  const html = tasks
    .map((t) => `<button class="task" data-task="${t.id}"><span class="t">${t.icon} ${esc(t.label)}</span><span class="d">${esc(t.desc)}</span></button>`)
    .join('');
  if ($('#task-grid').innerHTML !== html) $('#task-grid').innerHTML = html;
  // Sur un compte-rendu, on propose directement la relecture ; sur un ancien contrôle, le corrigé.
  const kind = state.current?.kind;
  const preferred = PREFERRED_TASK[kind] && state.lastKind !== kind ? PREFERRED_TASK[kind] : state.task;
  state.lastKind = kind;
  selectTask(tasks.some((t) => t.id === preferred) ? preferred : tasks.some((t) => t.id === state.task) ? state.task : tasks[0].id);
}

function selectTask(id) {
  const t = TASKS.find((x) => x.id === id) || TASKS[0];
  state.task = t.id;
  store.set('task', t.id);
  $$('.task').forEach((b) => b.classList.toggle('active', b.dataset.task === t.id));
  $('#opt-count-row').hidden = !t.count;
  if (t.count) $('#opt-count').value = store.get('count.' + t.id, t.count);
  $('#opt-diff-row').hidden = !t.difficulty;
  $('#opt-instructions').placeholder =
    t.id === 'custom'
      ? 'Ta demande : « fais un tableau comparatif des… », « invente un sujet d\'examen de 2 h »…'
      : 'Consignes supplémentaires (facultatif) : « insiste sur les formules », « niveau L1 »…';
}

function renderToolGrid() {
  $('#tool-grid').innerHTML = TOOLS.map((t) => `<button data-tool="${t.id}">${t.label}</button>`).join('');
}

/** Sélection courante : dans l'éditeur, ou dans l'aperçu (retrouvée dans le texte si possible). */
function selectionInfo() {
  if (!state.current || $('#pane-editor').hidden) return null;
  const t = state.previewSel;
  if (t && t.trim()) {
    const idx = editor.value.indexOf(t);
    return { text: t, start: idx >= 0 ? idx : null, end: idx >= 0 ? idx + t.length : null, exact: idx >= 0 };
  }
  const editorVisible = !$('#editor-split').classList.contains('mode-preview');
  if (editorVisible && editor.selectionEnd > editor.selectionStart) {
    const { s, e, text } = getSel();
    if (text.trim()) return { text, start: s, end: e, exact: true };
  }
  return null;
}

function updateSelectionUI() {
  const info = selectionInfo();
  $('#opt-selection-row').hidden = !info || multiScope();
  if (!info) $('#opt-selection').checked = false;
  $('#sel-size').textContent = info ? info.text.length.toLocaleString('fr-FR') : '0';
  $('#tools-hint').textContent = info
    ? `Sélection : ${info.text.length.toLocaleString('fr-FR')} caractères. Choisis une action.`
    : "Sélectionne un passage dans l'éditeur (sinon tout le document est utilisé), puis choisis une action.";
}

// ------------------------------------------------------------------ génération
function setGenStatus(msg) {
  $('#gen-status').textContent = msg;
}

async function generate() {
  const doc = state.current;
  if (!doc) return;
  const task = TASKS.find((x) => x.id === state.task);
  const instructions = $('#opt-instructions').value.trim();
  if (task.id === 'custom' && !instructions) {
    toast('Écris ta demande dans le champ de consignes.');
    $('#opt-instructions').focus();
    return;
  }
  const documentIds = multiScope() ? chosenCMs().map((d) => d.id) : null;
  if (documentIds && documentIds.length < 2) {
    toast('Coche au moins deux CM (ou choisis « Ce CM »).');
    $('#scope-pick').open = true;
    return;
  }
  await flushSave();
  const useSel = !documentIds && $('#opt-selection').checked && !$('#opt-selection-row').hidden;
  const selection = useSel ? selectionInfo()?.text || null : null;
  const count = Math.max(1, Math.min(60, +$('#opt-count').value || task.count || 10));
  if (task.count) store.set('count.' + task.id, count);

  const ctrl = new AbortController();
  state.controllers.gen = ctrl;
  const out = $('#gen-output');
  out.hidden = false;
  out.classList.remove('finished');
  $('#gen-result').innerHTML = '';
  $('#gen-done').hidden = true;
  $('#gen-done').className = 'done-card';
  $('#gen-thinking').hidden = true;
  $('#gen-thinking pre').textContent = '';
  $('#btn-generate').disabled = true;
  $('#btn-gen-stop').hidden = false;
  setGenStatus('Préparation…');

  const isJson = task.id === 'qcm' || task.id === 'flashcards';
  const live = liveRenderer($('#gen-result'));
  let text = '';
  let thinking = '';
  let done = null;
  try {
    await streamNDJSON(
      '/api/generate',
      { document_id: doc.id, task: task.id, count, difficulty: state.difficulty, instructions, selection, document_ids: documentIds },
      (ev) => {
        if (ev.type === 'status') setGenStatus(ev.message);
        else if (ev.type === 'thinking') {
          thinking += ev.text;
          $('#gen-thinking').hidden = false;
          $('#gen-thinking pre').textContent = thinking;
          setGenStatus('Le modèle réfléchit…');
        } else if (ev.type === 'token') {
          text += ev.text;
          if (isJson) {
            const n = (text.match(task.id === 'qcm' ? /"question"\s*:/g : /"front"\s*:/g) || []).length;
            $('#gen-result').innerHTML = `<div class="json-progress">${task.icon} ${n} ${task.id === 'qcm' ? 'question(s)' : 'carte(s)'} rédigée(s)…</div>`;
          } else {
            live.update(text);
          }
          setGenStatus('Rédaction en cours…');
        } else if (ev.type === 'done') done = ev;
      },
      ctrl.signal,
    );
    if (!isJson) await live.flush(text);
    if (!done) throw new Error('La génération s\'est interrompue.');
    const newDoc = done.document;
    const tokens = done.info?.eval_count;
    setGenStatus(`Terminé${tokens ? ` · ${tokens} tokens` : ''}${done.info?.model ? ` · ${done.info.model}` : ''}`);
    if (isJson) {
      $('#gen-result').innerHTML = `<div class="json-progress">${task.icon} Prêt ! Ouvre le document pour ${task.id === 'qcm' ? 'lancer le quiz' : 'réviser les cartes'}.</div>`;
    }
    const card = $('#gen-done');
    card.hidden = false;
    card.className = 'done-card' + (done.warning ? ' warn' : '');
    card.innerHTML = `<span>${done.warning ? '⚠ ' + esc(done.warning) : '✔ Ajouté à ta bibliothèque'}</span><button class="btn small primary">Ouvrir →</button>`;
    card.querySelector('button').onclick = () => {
      if (isPhone()) {
        // L'assistant couvre tout l'écran : on le referme pour montrer le document créé.
        state.aiOpen = false;
        applyAIOpen();
      }
      go(`#doc/${newDoc.id}`);
    };
    await loadLibrary();
    refreshDueCount();
  } catch (e) {
    if (e.name === 'AbortError') setGenStatus('Génération interrompue.');
    else {
      setGenStatus('Échec.');
      const card = $('#gen-done');
      card.hidden = false;
      card.className = 'done-card warn';
      card.innerHTML = `<span>⚠ ${esc(e.message)}</span>`;
    }
  } finally {
    out.classList.add('finished');
    $('#btn-generate').disabled = false;
    $('#btn-gen-stop').hidden = true;
    state.controllers.gen = null;
  }
}

// ------------------------------------------------------------------ discussion
const chatKey = () => (state.current ? state.current.id : 'global');

function renderChat() {
  const log = $('#chat-log');
  const msgs = state.chats[chatKey()] || [];
  log.innerHTML = '';
  if (!msgs.length) {
    log.innerHTML = `<div class="chat-empty">${
      state.current
        ? `Pose une question sur <b>${esc(state.current.title)}</b>, ou demande-moi d'en faire un TD, un examen d'entraînement, des flashcards…<br>📎 pour me donner un autre fichier.`
        : "Pose une question, demande-moi de <b>créer un document</b> (« fais-moi un cours sur… », « prépare un examen blanc de 2 h sur… ») ou donne-moi un fichier avec 📎 (PDF, Word, PowerPoint, photo…) : pas besoin de l'importer."
    }</div>`;
  }
  for (const m of msgs) {
    const d = document.createElement('div');
    d.className = `msg ${m.role}${m.error ? ' error' : ''}${m.pending ? ' pending' : ''}`;
    if (m.role === 'user' || m.error) {
      d.textContent = m.content;
      if (m.files?.length) {
        const f = document.createElement('div');
        f.className = 'msg-files';
        f.textContent = m.files.map((n) => `📎 ${n}`).join('  ');
        d.prepend(f);
      }
    } else {
      d.classList.add('markdown', 'small');
      d.innerHTML = m.html || esc(m.content);
    }
    m.el = d;
    log.append(d);
    if (m.role === 'assistant') {
      m.cardsEl = document.createElement('div');
      m.cardsEl.className = 'chat-docs';
      log.append(m.cardsEl);
      renderDocCards(m);
      if (!m.pending && !m.error && m.content.trim().length > 200) {
        const actions = document.createElement('div');
        actions.className = 'msg-actions';
        actions.innerHTML = '<button type="button" class="link" title="Enregistrer cette réponse dans la bibliothèque">📄 En faire un document</button>';
        actions.querySelector('button').onclick = (e) => answerToDocMenu(e.currentTarget, m);
        log.append(actions);
      }
    }
  }
  log.scrollTop = log.scrollHeight;
  const sug = $('#chat-suggestions');
  sug.hidden = msgs.length > 0;
  sug.innerHTML = (state.current ? CHAT_SUGGESTIONS : CHAT_SUGGESTIONS_FREE).map((s) => `<button type="button">${esc(s)}</button>`).join('');
  const busy = !!state.controllers.chat;
  $('#chat-stop').hidden = !busy;
  $('#chat-send').disabled = busy;
  renderChatFiles();
}

/** Documents que l'IA rédige à la suite d'une réponse (« fais-moi un TD… »). */
function renderDocCards(m) {
  if (!m.cardsEl) return;
  m.cardsEl.innerHTML = (m.docs || [])
    .map((c) => {
      const k = KINDS[c.kind] || { icon: '📄', label: 'Document' };
      if (c.status === 'error') {
        return `<div class="doc-card error"><span class="ico">⚠</span><div class="info"><b>${esc(c.title)}</b><small>${esc(c.message)}</small></div></div>`;
      }
      if (c.status === 'done') {
        return `<div class="doc-card done${c.warning ? ' warn' : ''}"><span class="ico">${k.icon}</span><div class="info"><b>${esc(c.document.title)}</b><small>${
          c.warning ? '⚠ ' + esc(c.warning) : `${esc(k.label)} ajouté à ta bibliothèque`
        }</small></div><button type="button" class="btn small primary" data-open="${c.document.id}">Ouvrir →</button></div>`;
      }
      const size = c.chars ? ` · ${c.chars.toLocaleString('fr-FR')} caractères` : '';
      return `<div class="doc-card writing"><span class="spinner"></span><div class="info"><b>${k.icon} ${esc(c.title)}</b><small>${esc(k.label)} en cours de rédaction${size}…</small></div></div>`;
    })
    .join('');
  m.cardsEl.hidden = !m.docs?.length;
  // L'IA a seulement lancé la rédaction, sans rien écrire : pas de bulle vide.
  if (m.el) m.el.hidden = !!m.docs?.length && !m.content.trim();
}

function openCreatedDoc(id) {
  if (isPhone()) {
    // L'assistant couvre tout l'écran : on le referme pour montrer le document.
    state.aiOpen = false;
    applyAIOpen();
  }
  go(`#doc/${id}`);
}

/** Enregistre une réponse de l'IA comme document (cours, TD, examen…). */
function answerToDocMenu(anchor, m) {
  showMenu(
    anchor,
    ANSWER_KINDS.map((kind) => ({ label: `${KINDS[kind].icon} ${KINDS[kind].label}`, run: () => answerToDoc(m, kind) })),
  );
}

async function answerToDoc(m, kind) {
  const msgs = state.chats[chatKey()] || [];
  const question = msgs[msgs.indexOf(m) - 1]?.content || '';
  const heading = m.content.match(/^#\s+(.+)$/m)?.[1]?.trim();
  const title = (heading || `${KINDS[kind].label} — ${question.replace(/\s+/g, ' ').slice(0, 70) || "réponse de l'IA"}`).slice(0, 200);
  const content = heading ? m.content : `# ${title}\n\n${m.content}`;
  const doc = state.current;
  try {
    const d = await api('/api/documents', {
      method: 'POST',
      body: { title, content, kind, subject_id: doc?.subject_id ?? null, parent_id: doc && !SOURCE_KINDS.includes(kind) ? doc.id : null },
    });
    await loadLibrary();
    toast(`${KINDS[kind].icon} « ${title} » ajouté à ta bibliothèque.`, { action: { label: 'Ouvrir', run: () => openCreatedDoc(d.id) } });
  } catch (e) {
    toast(e.message, { error: true });
  }
}

// ------------------------------------------------------------------ fichiers donnés à l'IA
function renderChatFiles() {
  const list = state.chatFiles[chatKey()] || [];
  const box = $('#chat-files');
  box.hidden = !list.length;
  box.innerHTML = list
    .map((f, i) => {
      const what = f.uploading ? 'lecture…' : f.chars ? `${Math.max(1, Math.round(f.chars / 1000))} k car.` : `${f.images} image${f.images > 1 ? 's' : ''}`;
      const icon = /\.(png|jpe?g|webp|gif|bmp)$/i.test(f.name) ? '🖼' : fileIcon(f.name);
      return `<span class="file-chip${f.uploading ? ' loading' : ''}" title="${esc(f.warning || f.name)}">${icon}<span class="n">${esc(f.name)}</span><small>${what}</small>${
        f.uploading ? '<span class="spinner"></span>' : `<button type="button" data-i="${i}" aria-label="Retirer ${esc(f.name)}">✕</button>`
      }</span>`;
    })
    .join('');
}

/** Lit des fichiers (PDF, Word, PowerPoint, images…) pour la discussion, sans les ajouter à la bibliothèque. */
async function addChatFiles(fileList) {
  const files = [...fileList].slice(0, 10);
  if (!files.length) return;
  const key = chatKey();
  const list = (state.chatFiles[key] ||= []);
  const items = files.map((f, i) => ({ name: f.name || `capture-${Date.now()}-${i}.png`, uploading: true }));
  list.push(...items);
  renderChatFiles();
  const fd = new FormData();
  files.forEach((f, i) => fd.append('files', f, items[i].name));
  const job = (async () => {
    try {
      const res = await fetch('/api/attachments', { method: 'POST', body: fd });
      if (!res.ok) throw await httpError('/api/attachments', res);
      const out = await res.json();
      items.forEach((it, i) => Object.assign(it, out[i] || { error: 'Pas de réponse du serveur.' }));
    } catch (e) {
      items.forEach((it) => (it.error = e.message));
    }
    for (const it of items) {
      it.uploading = false;
      if (it.error) toast(`${it.name} : ${it.error}`, { error: true });
      else if (it.warning) toast(`${it.name} : ${it.warning}`);
    }
    state.chatFiles[key] = (state.chatFiles[key] || []).filter((f) => !f.error);
    if (chatKey() === key) renderChatFiles();
  })();
  state.chatUploads = Promise.all([state.chatUploads, job]);
  await job;
}

async function sendChat(text) {
  text = text.trim();
  if (!text || state.controllers.chat) return;
  const key = chatKey();
  if ((state.chatFiles[key] || []).some((f) => f.uploading)) {
    toast('Lecture du fichier en cours…');
    await state.chatUploads;
  }
  const files = (state.chatFiles[key] || []).filter((f) => f.id);
  const msgs = (state.chats[key] ||= []);
  const asked = { role: 'user', content: text };
  // Fichiers nommés dans le message où ils apparaissent pour la première fois.
  const shown = new Set(msgs.flatMap((m) => m.files || []));
  const fresh = files.map((f) => f.name).filter((n) => !shown.has(n));
  if (fresh.length) asked.files = fresh;
  msgs.push(asked);
  const answer = { role: 'assistant', content: '', pending: true, docs: [] };
  msgs.push(answer);
  const ctrl = new AbortController();
  state.controllers.chat = ctrl;
  renderChat();
  // L'historique rappelle au modèle les fichiers donnés et les documents déjà créés.
  const history = msgs
    .filter((m) => !m.pending && !m.error)
    .map((m) => {
      let content = m.content;
      if (m.files?.length) content += `\n\n[Fichiers joints : ${m.files.join(', ')}]`;
      for (const c of m.docs || []) if (c.status === 'done') content += `\n\n[Document créé dans la bibliothèque : ${KINDS[c.kind]?.label || c.kind} « ${c.document.title} »]`;
      return { role: m.role, content: content.trim() || '(document créé)' };
    });
  const payload = { document_id: state.current?.id ?? null, messages: history, attachments: files.map((f) => f.id) };
  const live = liveRenderer(() => answer.el, 300);
  const log = $('#chat-log');
  const follow = () => {
    if (log.scrollHeight - log.scrollTop - log.clientHeight < 160) log.scrollTop = log.scrollHeight;
  };
  const card = (n) => answer.docs.find((c) => c.n === n);
  try {
    await streamNDJSON(
      '/api/chat',
      payload,
      (ev) => {
        if (ev.type === 'token') {
          answer.content += ev.text;
          live.update(answer.content);
          follow();
        } else if (ev.type === 'create') {
          answer.docs.push({ n: ev.n, kind: ev.kind, title: ev.title, status: 'writing', chars: 0 });
          renderDocCards(answer);
          follow();
        } else if (ev.type === 'create_token') {
          const c = card(ev.n);
          if (c) {
            c.chars += ev.text.length;
            if (c.chars - (c.shown || 0) > 150) {
              c.shown = c.chars;
              renderDocCards(answer);
            }
          }
        } else if (ev.type === 'created') {
          Object.assign(card(ev.n) || {}, { status: 'done', document: ev.document, warning: ev.warning });
          renderDocCards(answer);
          refreshLibrarySoon();
          refreshDueSoon();
        } else if (ev.type === 'create_error') {
          Object.assign(card(ev.n) || {}, { status: 'error', message: ev.message });
          renderDocCards(answer);
        }
      },
      ctrl.signal,
    );
    await live.flush(answer.content);
    answer.html = answer.el?.innerHTML;
  } catch (e) {
    for (const c of answer.docs) if (c.status === 'writing') Object.assign(c, { status: 'error', message: e.name === 'AbortError' ? 'Rédaction interrompue.' : e.message });
    if (e.name !== 'AbortError') {
      answer.error = !answer.content && !answer.docs.length;
      if (answer.error) answer.content = '⚠ ' + e.message;
      else toast(e.message, { error: true });
    } else if (!answer.content && !answer.docs.length) {
      msgs.splice(msgs.indexOf(answer), 1);
    }
    if (!answer.error && answer.content) {
      await live.flush(answer.content);
      answer.html = answer.el?.innerHTML;
    }
  } finally {
    answer.pending = false;
    state.controllers.chat = null;
    if (chatKey() === key) renderChat();
    log.scrollTop = log.scrollHeight;
  }
}

async function saveChat() {
  const msgs = (state.chats[chatKey()] || []).filter((m) => !m.error && !m.pending);
  if (!msgs.length) return toast('La discussion est vide.');
  const doc = state.current;
  const title = `Discussion — ${doc ? doc.title : 'tuteur'}`;
  const content =
    `# ${title}\n\n` + msgs.map((m) => (m.role === 'user' ? `**🙋 Moi :** ${m.content}` : `**🤖 IA :**\n\n${m.content}`)).join('\n\n---\n\n') + '\n';
  const d = await api('/api/documents', {
    method: 'POST',
    body: { title, content, kind: 'note', subject_id: doc?.subject_id ?? null, parent_id: doc?.id ?? null },
  });
  await loadLibrary();
  toast('Discussion enregistrée comme note.', { action: { label: 'Ouvrir', run: () => go(`#doc/${d.id}`) } });
}

// ------------------------------------------------------------------ outils texte
async function runTool(action, instructions = '') {
  const doc = state.current;
  if (!doc) return;
  if (state.docTab !== 'edit') return toast("Les outils texte s'utilisent dans l'éditeur.");
  if (state.controllers.tool) return;
  let info = selectionInfo();
  let whole = false;
  if (!info) {
    info = { text: editor.value, start: 0, end: editor.value.length, exact: true };
    whole = true;
  }
  if (!info.text.trim()) return toast('Le document est vide.');
  if (whole && info.text.length > 6000 && !confirm(`Aucune sélection : l'action s'appliquera à tout le document (${info.text.length.toLocaleString('fr-FR')} caractères). Continuer ?`)) return;

  const label = action === 'custom' ? 'Consigne libre' : TOOLS.find((t) => t.id === action)?.label || action;
  state.toolCtx = { docId: doc.id, ...info, whole, result: '' };
  const ctrl = new AbortController();
  state.controllers.tool = ctrl;
  const out = $('#tool-output');
  out.hidden = false;
  out.classList.remove('finished');
  $('#tool-result').innerHTML = '';
  $('#tool-actions').hidden = true;
  $('#tool-stop').hidden = false;
  $('#tool-status').textContent = `${label} — ${whole ? 'document entier' : `sélection (${info.text.length} car.)`}…`;
  $('#tool-replace').textContent = whole ? 'Remplacer le document' : 'Remplacer la sélection';
  $('#tool-replace').disabled = !info.exact;
  const live = liveRenderer($('#tool-result'), 300);
  let result = '';
  try {
    await streamNDJSON(
      '/api/transform',
      { text: info.text, action, instructions },
      (ev) => {
        if (ev.type === 'token') {
          result += ev.text;
          live.update(result);
        }
      },
      ctrl.signal,
    );
    result = result.trim().replace(/^```(?:markdown|md)?\n([\s\S]*?)\n```$/, '$1');
    await live.flush(result);
    state.toolCtx.result = result;
    $('#tool-status').textContent = `${label} — terminé.`;
    $('#tool-actions').hidden = !result;
  } catch (e) {
    $('#tool-status').textContent = e.name === 'AbortError' ? 'Interrompu.' : '⚠ ' + e.message;
    if (e.name === 'AbortError' && result) {
      state.toolCtx.result = result;
      $('#tool-actions').hidden = false;
    }
  } finally {
    out.classList.add('finished');
    $('#tool-stop').hidden = true;
    state.controllers.tool = null;
  }
}

function applyTool(how) {
  const ctx = state.toolCtx;
  if (!ctx || !ctx.result) return;
  if (!state.current || state.current.id !== ctx.docId || state.docTab !== 'edit') {
    return toast("Ouvre le document d'origine (onglet éditeur) pour appliquer le résultat.", { error: true });
  }
  let { start, end } = ctx;
  const v = editor.value;
  if (start == null || v.slice(start, end) !== ctx.text) {
    const idx = v.indexOf(ctx.text);
    if (idx < 0) return toast("Le texte d'origine a changé : copie le résultat à la main.", { error: true });
    start = idx;
    end = idx + ctx.text.length;
  }
  if (how === 'replace') replaceRange(start, end, ctx.result, start, start + ctx.result.length);
  else {
    const ins = '\n\n' + ctx.result;
    replaceRange(end, end, ins, end + 2, end + ins.length);
  }
  $('#tool-output').hidden = true;
  state.toolCtx = null;
  toast('Texte mis à jour (Ctrl+Z pour annuler).');
}

// ------------------------------------------------------------------ OCR des pages scannées
async function runOCR() {
  const doc = state.current;
  if (!doc || state.controllers.ocr) return;
  await flushSave();
  const ctrl = new AbortController();
  state.controllers.ocr = ctrl;
  $('#btn-ocr').disabled = true;
  editor.readOnly = true;
  let chars = 0;
  try {
    await streamNDJSON(
      `/api/documents/${doc.id}/ocr`,
      {},
      async (ev) => {
        if (ev.type === 'status') {
          chars = 0;
          $('#ocr-status').textContent = ev.message;
        } else if (ev.type === 'token') {
          chars += ev.text.length;
          $('#ocr-status').textContent = $('#ocr-status').textContent.replace(/ · \d+ car\.$/, '') + ` · ${chars} car.`;
        } else if (ev.type === 'page_done' && state.current?.id === doc.id && !liveActive()) {
          // (en direct, la page transcrite arrive toute seule dans l'éditeur)
          const fresh = await api(`/api/documents/${doc.id}`);
          editor.value = fresh.content;
          state.current.content = fresh.content;
          renderPreviewNow();
          updateStats();
        }
      },
      ctrl.signal,
    );
    toast('Pages scannées transcrites ✔');
  } catch (e) {
    if (e.name !== 'AbortError') toast(e.message, { error: true });
  } finally {
    state.controllers.ocr = null;
    editor.readOnly = false;
    $('#btn-ocr').disabled = false;
    if (state.current?.id === doc.id) openDoc(doc.id, { force: true });
  }
}

// ================================================================== QCM
function startQuiz(shuffled) {
  const qs = (state.current?.data?.questions || []).map((q, i) => ({ ...q, n: i }));
  if (shuffled) shuffle(qs);
  state.quiz = { qs, idx: 0, answers: [], docId: state.current.id, saved: false, history: null };
  state.keyMode = 'quiz';
  renderQuiz();
}

function renderQuiz() {
  const pane = $('#pane-quiz');
  const z = state.quiz;
  if (!z.qs.length) {
    pane.innerHTML = '<div class="empty-state"><div class="e-icon">🤔</div><h3>Ce QCM ne contient aucune question.</h3></div>';
    return;
  }
  if (z.idx >= z.qs.length) return renderQuizResult();
  const q = z.qs[z.idx];
  const answered = z.answers[z.idx];
  const isAnswered = answered !== undefined;
  const score = z.answers.filter((a, i) => a === z.qs[i].answer).length;
  const L = 'ABCDEFGH';
  pane.innerHTML = `
    <div class="quiz">
      <div class="quiz-top">
        <span class="muted">Question ${z.idx + 1} / ${z.qs.length}</span>
        <div class="progress"><div style="width:${(100 * (z.idx + (isAnswered ? 1 : 0))) / z.qs.length}%"></div></div>
        <span class="score">${score} ✔</span>
      </div>
      <div class="quiz-q">${inlineMd(q.question)}</div>
      <div class="choices">
        ${q.choices
          .map((c, i) => {
            let cls = '';
            if (isAnswered && i === q.answer) cls = 'correct';
            else if (isAnswered && i === answered) cls = 'wrong';
            return `<button class="choice ${cls}" data-i="${i}" ${isAnswered ? 'disabled' : ''}><span class="letter">${L[i]}</span><span>${inlineMd(c)}</span></button>`;
          })
          .join('')}
      </div>
      ${
        isAnswered
          ? `<div class="explanation">${
              answered === q.answer ? '✅ <b>Bonne réponse !</b>' : `❌ <b>Raté.</b> La bonne réponse était <b>${L[q.answer]}</b>.`
            } ${inlineMd(q.explanation || '')}</div>
             <div class="quiz-nav"><button class="btn primary" id="quiz-next">${z.idx + 1 < z.qs.length ? 'Question suivante →' : 'Voir mon score'}</button></div>`
          : ''
      }
      <div class="kbd-hint">Raccourcis : <kbd>1</kbd>–<kbd>${q.choices.length}</kbd> pour répondre · <kbd>Entrée</kbd> pour continuer</div>
    </div>`;
  typeset(pane, true);
  $$('.choice', pane).forEach((b) => (b.onclick = () => answerQuiz(+b.dataset.i)));
  $('#quiz-next', pane)?.addEventListener('click', nextQuiz);
}

function answerQuiz(i) {
  const z = state.quiz;
  if (!z || z.answers[z.idx] !== undefined || i >= z.qs[z.idx].choices.length) return;
  z.answers[z.idx] = i;
  renderQuiz();
}

function nextQuiz() {
  const z = state.quiz;
  if (!z || z.answers[z.idx] === undefined) return;
  z.idx += 1;
  renderQuiz();
}

async function renderQuizResult() {
  const pane = $('#pane-quiz');
  const z = state.quiz;
  const total = z.qs.length;
  const score = z.answers.filter((a, i) => a === z.qs[i].answer).length;
  const pct = Math.round((100 * score) / total);
  const cls = pct >= 70 ? 'good' : pct >= 50 ? 'mid' : 'bad';
  const msg = pct >= 90 ? 'Excellent ! 🎉' : pct >= 70 ? 'Très bien 👍' : pct >= 50 ? 'Pas mal, encore un effort 💪' : 'À retravailler 📚';
  const L = 'ABCDEFGH';
  const mistakes = z.qs.map((q, i) => ({ q, a: z.answers[i] })).filter(({ q, a }) => a !== q.answer);
  if (!z.saved) {
    z.saved = true;
    try {
      z.history = await api(`/api/documents/${z.docId}/attempts`, { method: 'POST', body: { score, total } });
      refreshDueCount();
    } catch {
      z.history = null;
    }
  }
  if (state.quiz !== z || state.docTab !== 'quiz') return;
  pane.innerHTML = `
    <div class="quiz">
      <div class="quiz-result">
        <div class="big score ${cls}">${score}/${total}</div>
        <div>${pct} % — ${msg}</div>
        <div class="row" style="justify-content:center;margin-top:16px;flex-wrap:wrap">
          <button class="btn" id="quiz-retry">↺ Recommencer</button>
          <button class="btn primary" id="quiz-shuffle">🔀 Recommencer en mélangeant</button>
          ${mistakes.length ? '<button class="btn accent" id="quiz-cards">🃏 Flashcards de mes erreurs</button>' : ''}
        </div>
      </div>
      ${
        mistakes.length
          ? `<h3>À revoir (${mistakes.length})</h3><div class="mistakes">${mistakes
              .map(
                ({ q, a }) => `<div class="mistake"><div class="q">${inlineMd(q.question)}</div>
                  <div class="y">✗ Ta réponse : ${inlineMd(q.choices[a] ?? '—')}</div>
                  <div class="a">✓ ${L[q.answer]}. ${inlineMd(q.choices[q.answer])}</div>
                  ${q.explanation ? `<div class="muted small">${inlineMd(q.explanation)}</div>` : ''}</div>`,
              )
              .join('')}</div>`
          : '<p style="text-align:center">Aucune erreur, bravo ! 🎉</p>'
      }
      ${
        z.history && z.history.length > 1
          ? `<h3>Historique</h3><ul class="plain-list">${z.history
              .map((h) => `<li><span class="score">${h.score}/${h.total}</span><span class="muted small">${new Date(h.created_at).toLocaleString('fr-FR')}</span></li>`)
              .join('')}</ul>`
          : ''
      }
    </div>`;
  typeset(pane, true);
  $('#quiz-retry', pane).onclick = () => startQuiz(false);
  $('#quiz-shuffle', pane).onclick = () => startQuiz(true);
  $('#quiz-cards', pane)?.addEventListener('click', () => mistakesToCards(mistakes));
}

async function mistakesToCards(mistakes) {
  const doc = state.current;
  const title = `Flashcards — erreurs du ${doc.title.replace(/^QCM — /, 'QCM ')}`;
  try {
    const deck = await api('/api/documents', {
      method: 'POST',
      body: { title, content: '', kind: 'flashcards', subject_id: doc.subject_id, parent_id: doc.id },
    });
    for (const { q } of mistakes) {
      const back = q.choices[q.answer] + (q.explanation ? `\n\n${q.explanation}` : '');
      await api(`/api/documents/${deck.id}/cards`, { method: 'POST', body: { front: q.question, back } });
    }
    await loadLibrary();
    refreshDueCount();
    toast(`${mistakes.length} carte(s) créée(s).`, { action: { label: 'Ouvrir', run: () => go(`#doc/${deck.id}`) } });
  } catch (e) {
    toast(e.message, { error: true });
  }
}

// ================================================================== flashcards
async function startReview(container, { documentId = null, subjectId = null } = {}) {
  container.innerHTML = '<div class="review"><div class="empty-state"><span class="spinner" style="display:inline-block"></span></div></div>';
  const params = new URLSearchParams();
  if (documentId) params.set('document_id', documentId);
  if (subjectId) params.set('subject_id', subjectId);
  let queue = [];
  try {
    queue = await api('/api/review?' + params);
  } catch (e) {
    container.innerHTML = `<div class="empty-state">${esc(e.message)}</div>`;
    return;
  }
  state.review = { container, queue, done: 0, revealed: false, documentId, subjectId, practice: false };
  state.keyMode = 'review';
  renderReview();
}

async function startPractice() {
  const s = state.review;
  const cards = await api(`/api/documents/${s.documentId}/cards`);
  s.queue = shuffle(cards.map((c) => ({ ...c, deck_title: state.current?.title })));
  s.practice = true;
  s.done = 0;
  s.revealed = false;
  state.keyMode = 'review';
  renderReview();
}

function renderReview() {
  const s = state.review;
  const c = s.container;
  if (!s.queue.length) {
    state.keyMode = null;
    c.innerHTML = `
      <div class="review"><div class="empty-state">
        <div class="e-icon">🎉</div>
        <h3>${s.done ? `Séance terminée : ${s.done} carte(s) révisée(s) !` : 'Rien à réviser pour le moment.'}</h3>
        <p>${s.practice ? 'Entraînement libre terminé.' : 'Les cartes reviendront automatiquement quand il sera temps de les revoir.'}</p>
        ${s.documentId ? '<button class="btn" id="rv-practice">🔁 Entraînement libre (toutes les cartes, sans planification)</button>' : ''}
      </div></div>`;
    $('#rv-practice', c)?.addEventListener('click', startPractice);
    return;
  }
  const card = s.queue[0];
  let actions;
  if (!s.revealed) actions = '<button class="btn primary reveal" id="rv-reveal">Afficher la réponse</button>';
  else if (s.practice)
    actions = `<div class="grades" style="grid-template-columns:1fr 1fr">
        <button class="grade g0" data-g="0">✗ À revoir<small>la carte revient</small></button>
        <button class="grade g2" data-g="2">✓ Je savais<small>carte suivante</small></button></div>`;
  else {
    const iv = card.intervals || {};
    const names = ['À revoir', 'Difficile', 'Bien', 'Facile'];
    actions = `<div class="grades">${names
      .map((n, g) => `<button class="grade g${g}" data-g="${g}">${n}<small>${fmtInterval(iv[g] ?? 0)}</small></button>`)
      .join('')}</div>`;
  }
  c.innerHTML = `
    <div class="review">
      <div class="review-top">
        <span>Reste <b>${s.queue.length}</b></span>
        ${s.practice ? '<span class="chip">entraînement libre</span>' : ''}
        <span class="spacer"></span>
        <span class="chip" title="${esc(card.deck_title || '')}">${esc(card.deck_title || '')}</span>
      </div>
      <div class="flashcard" id="rv-card">
        <div class="face-label">Question</div>
        <div class="front">${inlineMd(card.front)}</div>
        ${s.revealed ? `<div class="back">${inlineMd(card.back)}</div>` : ''}
      </div>
      ${actions}
      <div class="kbd-hint">${
        s.revealed
          ? s.practice
            ? '<kbd>1</kbd> À revoir · <kbd>2</kbd> Je savais'
            : '<kbd>1</kbd> À revoir · <kbd>2</kbd> Difficile · <kbd>3</kbd> Bien · <kbd>4</kbd> Facile'
          : '<kbd>Espace</kbd> pour retourner la carte'
      }</div>
    </div>`;
  typeset(c, true);
  $('#rv-reveal', c)?.addEventListener('click', revealCard);
  $('#rv-card', c).addEventListener('click', () => {
    if (!s.revealed && !getSelection().toString()) revealCard();
  });
  $$('.grade', c).forEach((b) => (b.onclick = () => gradeCard(+b.dataset.g)));
}

function revealCard() {
  if (!state.review || state.review.revealed) return;
  state.review.revealed = true;
  renderReview();
}

function gradeCard(g) {
  const s = state.review;
  if (!s || !s.revealed || !s.queue.length) return;
  const card = s.queue.shift();
  s.revealed = false;
  if (s.practice) {
    if (g === 0) s.queue.push(card);
    else s.done += 1;
    renderReview();
    return;
  }
  if (g === 0) s.queue.push(card);
  else s.done += 1;
  renderReview();
  api(`/api/cards/${card.id}/review`, { method: 'POST', body: { grade: g } })
    .then((updated) => {
      Object.assign(card, updated);
      refreshDueCount();
    })
    .catch((e) => toast(e.message, { error: true }));
}

async function renderCardsEditor() {
  const pane = $('#pane-cards');
  const docId = state.current.id;
  const cards = await api(`/api/documents/${docId}/cards`);
  if (state.current?.id !== docId || state.docTab !== 'cards') return;
  pane.innerHTML = `
    <div class="cards-editor">
      <div class="row"><h3 style="margin:0">${cards.length} carte(s)</h3><span class="spacer"></span>
        <span class="muted small">Modifie directement le recto / verso — enregistrement automatique.</span></div>
      <div id="cards-list" style="margin-top:14px"></div>
      <div class="card-new">
        <h4 style="margin:0 0 8px">Ajouter une carte</h4>
        <div class="card-row">
          <textarea id="new-front" placeholder="Recto (question)"></textarea>
          <textarea id="new-back" placeholder="Verso (réponse)"></textarea>
          <button class="btn primary" id="new-card-add">Ajouter</button>
        </div>
      </div>
    </div>`;
  const list = $('#cards-list', pane);
  for (const c of cards) {
    const row = document.createElement('div');
    row.className = 'card-row';
    row.innerHTML = `
      <textarea data-f="front">${esc(c.front)}</textarea>
      <textarea data-f="back">${esc(c.back)}</textarea>
      <button class="icon-btn danger" title="Supprimer la carte">🗑</button>
      <div class="meta">Prochaine révision : ${fmtDate(c.due)} · ${c.reps} révision(s) réussie(s) d'affilée · ${c.lapses} oubli(s)</div>`;
    $$('textarea', row).forEach((ta) => {
      const original = { value: ta.value };
      ta.addEventListener('change', async () => {
        if (!ta.value.trim() || ta.value === original.value) return;
        try {
          await api(`/api/cards/${c.id}`, { method: 'PATCH', body: { [ta.dataset.f]: ta.value } });
          original.value = ta.value;
          toast('Carte enregistrée.');
        } catch (e) {
          toast(e.message, { error: true });
        }
      });
    });
    $('button', row).onclick = async () => {
      if (!confirm('Supprimer cette carte ?')) return;
      await api(`/api/cards/${c.id}`, { method: 'DELETE' });
      renderCardsEditor();
      refreshDueCount();
    };
    list.append(row);
  }
  $('#new-card-add', pane).onclick = async () => {
    const front = $('#new-front', pane).value.trim();
    const back = $('#new-back', pane).value.trim();
    if (!front || !back) return toast('Remplis le recto et le verso.');
    try {
      await api(`/api/documents/${docId}/cards`, { method: 'POST', body: { front, back } });
      await renderCardsEditor();
      refreshDueCount();
      $('#new-front')?.focus();
    } catch (e) {
      toast(e.message, { error: true });
    }
  };
}

async function showReviewView() {
  showView('review');
  const sel = $('#review-subject');
  sel.innerHTML = subjectOptions('Toutes les matières');
  sel.value = state.reviewSubject ?? '';
  await startReview($('#review-global'), { subjectId: sel.value ? +sel.value : null });
}

// ================================================================== calendrier
const EVENT_KINDS = {
  examen: 'Examen', partiel: 'Partiel', rattrapage: 'Rattrapage', oral: 'Oral',
  tp: 'TP noté', projet: 'Rendu de projet', autre: 'Autre',
};
const MONTHS_SHORT = ['janv.', 'févr.', 'mars', 'avr.', 'mai', 'juin', 'juil.', 'août', 'sept.', 'oct.', 'nov.', 'déc.'];
const isoDate = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
const eventColor = (ev) => ev.subject_color || '#6366f1';

function renderUpcomingList(ul, events) {
  if (!events.length) {
    ul.innerHTML = '<li class="empty">Aucun examen prévu. <button class="link" data-add>Ajouter un examen</button></li>';
    ul.querySelector('[data-add]').onclick = () => openEventDialog();
    return;
  }
  ul.innerHTML = events
    .map((ev) => {
      const d = new Date(ev.date + 'T00:00:00');
      const n = daysUntil(ev.date);
      const time = ev.start_time ? `${ev.start_time}${ev.end_time ? '–' + ev.end_time : ''}` : '';
      const meta = [EVENT_KINDS[ev.kind], ev.subject_name, time, ev.location].filter(Boolean).join(' · ');
      return `<li data-ev="${ev.id}" style="--ev:${esc(eventColor(ev))}">
        <div class="date"><b>${d.getDate()}</b><small>${MONTHS_SHORT[d.getMonth()]}</small></div>
        <div class="info"><div class="t">${esc(ev.title)}</div><div class="m">${esc(meta)}</div></div>
        <span class="countdown ${urgency(n)}">${countdownText(n)}</span>
      </li>`;
    })
    .join('');
  $$('[data-ev]', ul).forEach((li) => {
    li.onclick = () => openEventDialog((state.upcoming || []).concat(state.calEvents || []).find((e) => e.id === +li.dataset.ev));
  });
}

async function showCalendarView() {
  showView('calendar');
  if (!state.calMonth) {
    const t = new Date();
    state.calMonth = new Date(t.getFullYear(), t.getMonth(), 1);
  }
  await renderCalendar();
}

async function renderCalendar() {
  const first = state.calMonth;
  const start = new Date(first);
  start.setDate(1 - ((first.getDay() + 6) % 7)); // lundi de la première semaine
  const end = new Date(start);
  end.setDate(start.getDate() + 41);
  $('#cal-month').textContent = first.toLocaleDateString('fr-FR', { month: 'long', year: 'numeric' });
  let events = [];
  try {
    [events] = await Promise.all([api(`/api/events?start=${isoDate(start)}&end=${isoDate(end)}`), refreshUpcoming()]);
  } catch (e) {
    toast(e.message, { error: true });
  }
  state.calEvents = events;
  const byDay = new Map();
  for (const ev of events) {
    if (!byDay.has(ev.date)) byDay.set(ev.date, []);
    byDay.get(ev.date).push(ev);
  }
  const today = isoDate(new Date());
  let html = ['Lun', 'Mar', 'Mer', 'Jeu', 'Ven', 'Sam', 'Dim'].map((d) => `<div class="cal-dow">${d}</div>`).join('');
  for (let i = 0; i < 42; i++) {
    const d = new Date(start);
    d.setDate(start.getDate() + i);
    const iso = isoDate(d);
    const evs = byDay.get(iso) || [];
    const cls = ['cal-day', d.getMonth() !== first.getMonth() ? 'other' : '', iso === today ? 'today' : '', d.getDay() % 6 === 0 ? 'weekend' : '']
      .filter(Boolean)
      .join(' ');
    const chips = evs
      .slice(0, 3)
      .map(
        (ev) =>
          `<div class="cal-ev" data-ev="${ev.id}" style="--ev:${esc(eventColor(ev))}" title="${esc(ev.title)}${ev.location ? ' — ' + esc(ev.location) : ''}">${ev.start_time ? `<b>${ev.start_time}</b> ` : ''}${esc(ev.subject_name || ev.title)}</div>`,
      )
      .join('');
    const more = evs.length > 3 ? `<div class="cal-more">+${evs.length - 3} autre(s)</div>` : '';
    html += `<div class="${cls}" data-date="${iso}"><span class="num">${d.getDate()}</span>${chips}${more}</div>`;
  }
  const grid = $('#cal-grid');
  grid.innerHTML = html;
  renderUpcomingList($('#cal-upcoming'), (state.upcoming || []).slice(0, 12));
}

function shiftMonth(delta) {
  const m = state.calMonth;
  state.calMonth = new Date(m.getFullYear(), m.getMonth() + delta, 1);
  renderCalendar();
}

const eventDlg = { event: null };

function openEventDialog(event = null, defaults = {}) {
  eventDlg.event = event;
  $('#ev-heading').textContent = event ? "Modifier l'événement" : 'Nouvel examen';
  $('#ev-subject').innerHTML = subjectOptions('— Aucune matière —');
  const v = event || defaults;
  $('#ev-subject').value = v.subject_id ?? '';
  $('#ev-kind').value = v.kind || 'examen';
  $('#ev-date').value = v.date || isoDate(new Date());
  $('#ev-start').value = v.start_time || '';
  $('#ev-end').value = v.end_time || '';
  $('#ev-title').value = event?.title || '';
  $('#ev-location').value = v.location || '';
  $('#ev-notes').value = v.notes || '';
  $('#ev-delete').hidden = !event;
  $('#dlg-event').showModal();
}

function autoEventTitle() {
  const s = subjectById(+$('#ev-subject').value);
  return `${EVENT_KINDS[$('#ev-kind').value] || 'Examen'}${s ? ' — ' + s.name : ''}`;
}

async function saveEvent() {
  const date = $('#ev-date').value;
  if (!date) {
    toast('Choisis une date.');
    return;
  }
  const subject = $('#ev-subject').value;
  const body = {
    subject_id: subject ? +subject : null,
    kind: $('#ev-kind').value,
    date,
    start_time: $('#ev-start').value,
    end_time: $('#ev-end').value,
    title: $('#ev-title').value.trim() || autoEventTitle(),
    location: $('#ev-location').value,
    notes: $('#ev-notes').value,
  };
  try {
    if (eventDlg.event) await api(`/api/events/${eventDlg.event.id}`, { method: 'PATCH', body });
    else await api('/api/events', { method: 'POST', body });
    $('#dlg-event').close();
    toast(eventDlg.event ? 'Événement modifié.' : `Ajouté au calendrier (${countdownText(daysUntil(date))}).`);
    await afterEventChange(date);
  } catch (e) {
    toast(e.message, { error: true });
  }
}

async function deleteEvent() {
  const ev = eventDlg.event;
  if (!ev || !confirm(`Supprimer « ${ev.title} » du calendrier ?`)) return;
  await api(`/api/events/${ev.id}`, { method: 'DELETE' });
  $('#dlg-event').close();
  toast('Événement supprimé.');
  await afterEventChange();
}

async function afterEventChange(date) {
  if (!$('#view-calendar').hidden) {
    if (date) {
      const d = new Date(date + 'T00:00:00');
      state.calMonth = new Date(d.getFullYear(), d.getMonth(), 1);
    }
    await renderCalendar();
  } else if (!$('#view-home').hidden) {
    await showHome();
  } else {
    await refreshUpcoming();
  }
}

// ================================================================== import
const imp = { files: [], kinds: [] };

function fileIcon(name) {
  const ext = name.split('.').pop().toLowerCase();
  return { pdf: '📕', docx: '📘', doc: '📘', pptx: '📙', ppt: '📙', txt: '📄', md: '📝', markdown: '📝' }[ext] || '📄';
}

function fillImportSubjects(selected) {
  const sel = $('#imp-subject');
  sel.innerHTML = subjectOptions('— Sans matière —') + '<option value="__new">＋ Nouvelle matière…</option>';
  sel.value = selected != null && selected !== '' && state.subjects.some((s) => String(s.id) === String(selected)) ? String(selected) : '';
}

function openImport(files = [], subjectId) {
  const pre = subjectId !== undefined && subjectId !== '' ? subjectId : state.current?.subject_id ?? store.get('lastSubject', '');
  fillImportSubjects(pre);
  imp.files = [];
  imp.kinds = [];
  addImportFiles(files);
  $('#imp-status').textContent = '';
  const dlg = $('#dlg-import');
  if (!dlg.open) dlg.showModal();
}

function addImportFiles(files) {
  for (const f of files) {
    if (!imp.files.some((x) => x.name === f.name && x.size === f.size)) {
      imp.files.push(f);
      imp.kinds.push(guessKind(f.name)); // CM / TD / TP deviné d'après le nom, modifiable dans la liste
    }
  }
  renderImportList();
}

function renderImportList() {
  const kindSelect = (i) =>
    `<select class="select small" data-kind="${i}" title="Type de document">${CATEGORIES.map(
      (c) => `<option value="${c.kind}" ${imp.kinds[i] === c.kind ? 'selected' : ''}>${c.short}</option>`,
    ).join('')}</select>`;
  $('#imp-list').innerHTML = imp.files
    .map(
      (f, i) =>
        `<li><span>${fileIcon(f.name)}</span><span class="n">${esc(f.name)} <span class="muted">(${fmtSize(f.size)})</span></span>${kindSelect(i)}<button type="button" class="icon-btn" data-rm="${i}" title="Retirer">✕</button></li>`,
    )
    .join('');
  $('#imp-go').disabled = !imp.files.length;
}

async function doImport() {
  if (!imp.files.length) return;
  const sid = $('#imp-subject').value;
  store.set('lastSubject', sid);
  const fd = new FormData();
  imp.files.forEach((f, i) => {
    fd.append('files', f);
    fd.append('kinds', imp.kinds[i] || guessKind(f.name));
  });
  if (sid) fd.append('subject_id', sid);
  $('#imp-go').disabled = true;
  const legacy = imp.files.some((f) => /\.(doc|ppt)$/i.test(f.name));
  $('#imp-status').innerHTML = `<span class="spinner" style="display:inline-block;vertical-align:middle"></span> Conversion en cours…${
    legacy ? ' (les .doc / .ppt passent par Word / PowerPoint : quelques secondes de plus)' : ''
  }`;
  try {
    const results = await api('/api/import', { method: 'POST', body: fd });
    imp.files = [];
    imp.kinds = [];
    $('#imp-list').innerHTML = results
      .map((r) => {
        if (r.error) return `<li class="err"><span>✖</span><span class="n">${esc(r.filename)}<small>${esc(r.error)}</small></span></li>`;
        const warn = (r.warnings || []).map((w) => `<small>⚠ ${esc(w)}</small>`).join('');
        const cat = CATEGORIES.find((c) => c.kind === r.document.kind);
        return `<li><span>✔</span><span class="n">${cat ? `<b>${cat.short}</b> · ` : ''}<a href="#doc/${r.document.id}" data-open>${esc(r.document.title)}</a>${warn}</span></li>`;
      })
      .join('');
    $$('#imp-list [data-open]').forEach((a) => a.addEventListener('click', () => $('#dlg-import').close()));
    const ok = results.filter((r) => r.document);
    $('#imp-status').textContent = `${ok.length} / ${results.length} fichier(s) importé(s).`;
    await loadLibrary();
    if (ok.length === 1 && results.length === 1 && !(ok[0].warnings || []).length) {
      $('#dlg-import').close();
      go(`#doc/${ok[0].document.id}`);
      toast('Cours importé ✔');
    }
  } catch (e) {
    $('#imp-status').textContent = '⚠ ' + e.message;
    $('#imp-go').disabled = false;
  }
}

// ================================================================== réglages & état d'Ollama
/** Le serveur tourne-t-il sur le code à jour ? (sinon : bandeau d'avertissement) */
async function checkServerVersion() {
  let stale = false;
  try {
    const res = await fetch('/api/version', { cache: 'no-store' });
    if (res.status === 404) stale = true; // serveur antérieur à cette vérification
    else if (res.ok) stale = Boolean((await res.json()).stale);
  } catch {
    /* serveur injoignable : signalé ailleurs */
  }
  $('#update-banner').hidden = !stale;
  return stale;
}

async function refreshHealth() {
  checkServerVersion();
  try {
    const h = await api('/api/health');
    state.health = h;
    $('#ollama-dot').className = 'dot ' + (h.ollama && h.model ? 'ok' : 'ko');
    $('#ollama-label').textContent = h.ollama ? (h.model ? `Ollama · ${h.model}` : 'Ollama · aucun modèle installé') : 'Ollama hors ligne';
    $('#ollama-label').title = h.error || (h.ollama ? `Ollama ${h.version}` : "Lance l'application Ollama pour activer l'IA.");
    $('#ai-model').textContent = h.model || (h.ollama ? 'aucun modèle' : 'hors ligne');
  } catch {
    $('#ollama-dot').className = 'dot ko';
    $('#ollama-label').textContent = 'Serveur DeepLearning injoignable';
  }
}

async function openSettings() {
  let settings, health;
  try {
    [settings, health] = await Promise.all([api('/api/settings'), api('/api/health')]);
  } catch (e) {
    return toast(e.message, { error: true });
  }
  const sel = $('#set-model');
  sel.innerHTML = health.models.length
    ? health.models.map((m) => `<option value="${esc(m.name)}">${esc(m.name)}${m.parameter_size ? ` — ${esc(m.parameter_size)}` : ''}</option>`).join('')
    : '<option value="">(aucun modèle trouvé)</option>';
  sel.value = settings.model && health.models.some((m) => m.name === settings.model) ? settings.model : health.model || '';
  $('#set-model-caps').textContent = health.capabilities ? `Capacités du modèle actif : ${health.capabilities.join(', ')}` : '';
  const ctx = $('#set-ctx');
  if (![...ctx.options].some((o) => +o.value === +settings.num_ctx)) {
    ctx.insertAdjacentHTML('beforeend', `<option value="${settings.num_ctx}">${settings.num_ctx}</option>`);
  }
  ctx.value = String(settings.num_ctx);
  $('#set-temp').value = settings.temperature;
  $('#set-temp-val').textContent = settings.temperature;
  $('#set-think').checked = !!settings.think;
  const sems = [...new Set(['', ...state.subjects.map((s) => s.semester || ''), settings.current_semester || ''])]
    .filter((s, i) => s || i === 0)
    .sort((a, b) => semNum(a) - semNum(b));
  $('#set-semester').innerHTML = sems.map((s) => `<option value="${s}">${s ? semLabel(s) : 'Aucun (tout déplier)'}</option>`).join('');
  $('#set-semester').value = settings.current_semester || '';
  // Réglages communs à tous : en lecture seule pour les comptes non administrateurs.
  const ro = !isAdmin();
  $$('#settings-form select, #settings-form input').forEach((el) => (el.disabled = ro));
  $('#set-save').hidden = ro;
  $('#set-readonly').hidden = !ro;
  $('#dlg-settings').showModal();
}

async function saveSettings() {
  try {
    await api('/api/settings', {
      method: 'PUT',
      body: {
        model: $('#set-model').value,
        num_ctx: +$('#set-ctx').value,
        temperature: +$('#set-temp').value,
        think: $('#set-think').checked,
        current_semester: $('#set-semester').value,
      },
    });
    const semChanged = (state.settings?.current_semester || '') !== $('#set-semester').value;
    state.settings = await api('/api/settings');
    if (semChanged) {
      // Le nouveau semestre en cours reprend l'affichage par défaut (déplié, les autres repliés).
      for (const k of Object.keys(state.collapsed)) if (k.startsWith('sem:')) delete state.collapsed[k];
      store.set('collapsed', state.collapsed);
      renderLibrary();
    }
    $('#dlg-settings').close();
    toast('Réglages enregistrés.');
    refreshHealth();
  } catch (e) {
    toast(e.message, { error: true });
  }
}

// ================================================================== comptes (outil partagé)
const login = { pending: null, resolve: null, step: 'auth', password: '' };
const isAdmin = () => !state.authEnabled || !!state.user?.is_admin;

function setUser(user, enabled) {
  state.user = user;
  state.authEnabled = !!enabled;
  $('#btn-account').hidden = !state.authEnabled || !user;
  if (user) {
    $('#account-name').textContent = user.username;
    $('#account-avatar').textContent = user.username.slice(0, 1).toUpperCase();
  }
}

/** Vérifie la session au démarrage ; affiche l'écran de connexion si besoin. */
async function ensureLogin() {
  try {
    const me = await api('/api/auth/me');
    setUser(me.user, me.auth);
    if (!me.user.must_change) return;
    // Mot de passe provisoire d'une visite précédente : on le redemande avant d'en choisir un nouveau.
    await api('/api/auth/logout', { method: 'POST' }).catch(() => {});
  } catch (e) {
    if (e.status !== 401) return; // serveur injoignable : init() affichera l'erreur
  }
  await requireLogin();
}

/** Affiche l'écran de connexion ; la promesse est tenue une fois connecté avec un mot de passe définitif. */
function requireLogin() {
  if (login.pending) return login.pending;
  login.pending = new Promise((resolve) => (login.resolve = resolve));
  for (const d of $$('dialog[open]')) d.close();
  showLoginStep('auth');
  $('#login-screen').hidden = false;
  setTimeout(() => ($('#login-user').value ? $('#login-pass') : $('#login-user')).focus(), 0);
  return login.pending;
}

function showLoginStep(step) {
  login.step = step;
  $('#login-step-auth').hidden = step !== 'auth';
  $('#login-step-change').hidden = step !== 'change';
  $('#login-user').required = $('#login-pass').required = step === 'auth';
  $('#login-new').required = $('#login-new2').required = step === 'change';
  $('#login-submit').textContent = step === 'auth' ? 'Se connecter' : 'Enregistrer et continuer';
  $('#login-error').hidden = true;
}

async function submitLogin(e) {
  e.preventDefault();
  const btn = $('#login-submit');
  btn.disabled = true;
  $('#login-error').hidden = true;
  try {
    if (login.step === 'auth') {
      const password = $('#login-pass').value;
      const r = await api('/api/auth/login', { method: 'POST', body: { username: $('#login-user').value.trim(), password } });
      setUser(r.user, r.auth);
      if (r.user.must_change) {
        login.password = password;
        showLoginStep('change');
        $('#login-new').focus();
        return;
      }
    } else {
      const pw = $('#login-new').value;
      if (pw !== $('#login-new2').value) throw new Error('Les deux mots de passe ne correspondent pas.');
      const r = await api('/api/auth/password', { method: 'POST', body: { current: login.password, new: pw } });
      setUser(r.user, true);
      toast('Mot de passe enregistré. Bienvenue !');
    }
    login.password = '';
    for (const id of ['#login-pass', '#login-new', '#login-new2']) $(id).value = '';
    $('#login-screen').hidden = true;
    const resolve = login.resolve;
    login.pending = login.resolve = null;
    resolve?.();
    liveConnect(); // (re)connexion au direct avec la nouvelle session
  } catch (err) {
    $('#login-error').textContent = err.message;
    $('#login-error').hidden = false;
  } finally {
    btn.disabled = false;
  }
}

async function logout() {
  await flushSave();
  live.stop = true;
  live.ws?.close();
  await api('/api/auth/logout', { method: 'POST' }).catch(() => {});
  location.hash = '';
  location.reload();
}

async function openAccount() {
  if (!state.authEnabled || !state.user) return;
  $('#acc-username').textContent = state.user.username;
  for (const id of ['#acc-current', '#acc-new', '#acc-new2', '#acc-new-user']) $(id).value = '';
  $('#acc-secret').hidden = true;
  $('#acc-admin').hidden = !state.user.is_admin;
  $('#dlg-account').showModal();
  if (state.user.is_admin) {
    renderPublicUrl();
    renderUsers();
  }
}

async function changeMyPassword() {
  const current = $('#acc-current').value;
  const pw = $('#acc-new').value;
  if (!current || !pw) return toast('Remplis le mot de passe actuel et le nouveau.', { error: true });
  if (pw !== $('#acc-new2').value) return toast('Les deux nouveaux mots de passe ne correspondent pas.', { error: true });
  try {
    await api('/api/auth/password', { method: 'POST', body: { current, new: pw } });
    for (const id of ['#acc-current', '#acc-new', '#acc-new2']) $(id).value = '';
    toast('Mot de passe changé (tes autres appareils ont été déconnectés).');
  } catch (e) {
    toast(e.message, { error: true });
  }
}

async function renderPublicUrl() {
  const box = $('#acc-url');
  box.textContent = "Recherche de l'adresse…";
  try {
    const p = await api('/api/admin/public-url');
    if (p.url) {
      box.innerHTML = `<span>${p.status === 'up' ? '🟢' : '🟠'}</span><code>${esc(p.url)}</code>
        <button type="button" class="btn small" data-copy="${esc(p.url)}">Copier</button>
        ${p.temporary ? '<small class="muted">Adresse temporaire : elle change à chaque redémarrage du PC ou de Docker (la nouvelle s\'affiche ici).</small>' : ''}`;
    } else if (p.status === 'off') {
      box.textContent = "Pas de tunnel : l'outil n'est accessible que depuis ce PC.";
    } else if (p.warning) {
      box.innerHTML = `⚠️ <span>${esc(p.warning)}</span> <button type="button" class="link" data-refresh-url>vérifier à nouveau</button>`;
    } else {
      box.innerHTML = '🟠 Tunnel en cours de connexion… <button type="button" class="link" data-refresh-url>réessayer</button>';
    }
  } catch (e) {
    box.textContent = e.message;
  }
}

async function renderUsers() {
  let users;
  try {
    users = await api('/api/users');
  } catch (e) {
    return toast(e.message, { error: true });
  }
  state.users = users;
  const fmt = (iso) => (iso ? `${fmtDate(iso)} ${iso.slice(11, 16)}` : '');
  $('#acc-users').innerHTML = users
    .map(
      (u) => `<li class="${u.active ? '' : 'off'}" data-uid="${u.id}">
        <span class="avatar">${esc(u.username.slice(0, 1).toUpperCase())}</span>
        <span class="who"><b>${esc(u.username)}</b><small>${u.last_login ? `Dernière connexion : ${fmt(u.last_login)}` : 'Jamais connecté'}</small></span>
        ${u.is_admin ? '<span class="tag">admin</span>' : ''}
        ${u.must_change && u.active ? '<span class="tag warn">mot de passe provisoire</span>' : ''}
        ${u.active ? '' : '<span class="tag warn">désactivé</span>'}
        ${u.id === state.user.id ? '<span class="muted small">(toi)</span>' : '<button type="button" class="icon-btn" data-umenu title="Actions">⋯</button>'}
      </li>`,
    )
    .join('');
}

function showSecret(username, password, created) {
  const box = $('#acc-secret');
  box.hidden = false;
  box.innerHTML = `<span>${created ? 'Compte créé' : 'Nouveau mot de passe provisoire'} pour <b>${esc(username)}</b> — transmets-le-lui, il ne sera plus affiché :</span>
    <code>${esc(password)}</code>
    <span class="muted small">Il devra choisir son propre mot de passe à la première connexion.</span>
    <button type="button" class="btn small" data-copy="${esc(`Identifiant : ${username}\nMot de passe provisoire : ${password}`)}">Copier identifiant + mot de passe</button>`;
}

async function createUser() {
  const username = $('#acc-new-user').value.trim();
  if (!/^[\w.-]{2,32}$/.test(username)) {
    return toast('Identifiant : 2 à 32 caractères, lettres, chiffres, point, tiret ou _ (sans espace).', { error: true });
  }
  try {
    const r = await api('/api/users', { method: 'POST', body: { username, is_admin: $('#acc-new-admin').checked } });
    $('#acc-new-user').value = '';
    $('#acc-new-admin').checked = false;
    showSecret(r.user.username, r.password, true);
    renderUsers();
  } catch (e) {
    toast(e.message, { error: true });
  }
}

function userMenu(anchor, u) {
  const act = async (fn) => {
    try {
      await fn();
      renderUsers();
    } catch (e) {
      toast(e.message, { error: true });
    }
  };
  showMenu(anchor, [
    {
      label: '🔑 Nouveau mot de passe provisoire',
      run: () => act(async () => {
        const r = await api(`/api/users/${u.id}/reset`, { method: 'POST' });
        showSecret(u.username, r.password, false);
      }),
    },
    {
      label: u.is_admin ? '⬇ Retirer les droits admin' : '⬆ Rendre administrateur',
      run: () => act(() => api(`/api/users/${u.id}`, { method: 'PATCH', body: { is_admin: !u.is_admin } })),
    },
    {
      label: u.active ? '⏸ Désactiver (bloque la connexion)' : '▶ Réactiver',
      run: () => act(() => api(`/api/users/${u.id}`, { method: 'PATCH', body: { active: !u.active } })),
    },
    {
      label: '🗑 Supprimer le compte',
      danger: true,
      run: () => {
        if (confirm(`Supprimer le compte « ${u.username} » ? (Les cours restent dans la bibliothèque.)`)) {
          act(() => api(`/api/users/${u.id}`, { method: 'DELETE' }));
        }
      },
    },
  ]);
}

// ================================================================== recherche
function highlightText(text, q) {
  const safe = esc(text);
  if (!q) return safe;
  return safe.replace(new RegExp(regexEscape(esc(q)), 'gi'), (m) => `<mark>${m}</mark>`);
}

const doSearch = debounce(async () => {
  const q = $('#search').value.trim();
  const box = $('#search-results');
  if (q.length < 2) {
    box.hidden = true;
    return;
  }
  try {
    const res = await api('/api/search?q=' + encodeURIComponent(q));
    box.innerHTML = res.length
      ? res
          .map((r) => `<button data-id="${r.id}">${KINDS[r.kind]?.icon || '📄'} ${highlightText(r.title, q)}<small>${highlightText(r.snippet, q)}</small></button>`)
          .join('')
      : '<div class="empty" style="padding:10px">Aucun résultat.</div>';
    box.hidden = false;
  } catch {
    box.hidden = true;
  }
}, 200);

// ================================================================== clavier global
function handleGlobalKeys(e) {
  if (e.key === 'Escape') {
    closeMenus();
    $('#search-results').hidden = true;
    if (!$('#find-bar').hidden && (document.activeElement === editor || $('#find-bar').contains(document.activeElement))) closeFind();
  }
  const tag = (e.target.tagName || '').toLowerCase();
  if (['input', 'textarea', 'select'].includes(tag) || e.target.isContentEditable) return;
  if (e.ctrlKey || e.metaKey || e.altKey) return;

  if (state.keyMode === 'review' && state.review?.container.offsetParent) {
    const s = state.review;
    if ((e.key === ' ' || e.key === 'Enter') && !s.revealed) {
      e.preventDefault();
      revealCard();
    } else if (s.revealed && /^[1-4]$/.test(e.key)) {
      e.preventDefault();
      const n = +e.key - 1;
      gradeCard(s.practice ? (n === 0 ? 0 : 2) : n);
    }
  } else if (state.keyMode === 'quiz' && !$('#pane-quiz').hidden && state.quiz) {
    if (/^[1-8]$/.test(e.key)) {
      e.preventDefault();
      answerQuiz(+e.key - 1);
    } else if (e.key === 'Enter' || e.key === 'ArrowRight') {
      e.preventDefault();
      nextQuiz();
    }
  }
}

// ================================================================== initialisation
function bindEvents() {
  // Barre latérale
  $('#btn-import').onclick = () => openImport([]);
  $('#btn-new-note').onclick = () => newNote();
  $('#btn-new-subject').onclick = () => createSubject();
  $('#btn-review-all').onclick = () => {
    state.reviewSubject = '';
    go('#review');
  };
  $('#btn-calendar').onclick = () => go('#calendar');
  $('#update-reload').onclick = () => location.reload();
  $('#btn-settings').onclick = openSettings;

  // Comptes
  $('#login-form').addEventListener('submit', submitLogin);
  $('#btn-account').onclick = openAccount;
  $('#acc-save-pw').onclick = changeMyPassword;
  $('#acc-create').onclick = createUser;
  $('#acc-logout').onclick = logout;
  $('#acc-new-user').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      createUser();
    }
  });
  $('#dlg-account').addEventListener('click', (e) => {
    const copy = e.target.closest('[data-copy]');
    if (copy) {
      navigator.clipboard.writeText(copy.dataset.copy).then(
        () => toast('Copié.'),
        () => toast('Copie impossible : sélectionne le texte à la main.', { error: true }),
      );
    }
    if (e.target.closest('[data-refresh-url]')) renderPublicUrl();
    const m = e.target.closest('[data-umenu]');
    if (m) {
      e.stopPropagation();
      const u = state.users?.find((x) => x.id === +m.closest('[data-uid]').dataset.uid);
      if (u) userMenu(m, u);
    }
  });

  // Matières
  $('#subj-save').onclick = saveSubjectDialog;
  $('#subj-name').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      saveSubjectDialog();
    }
  });
  $('#subj-colors').addEventListener('click', (e) => {
    const b = e.target.closest('[data-c]');
    if (!b) return;
    subjectDlg.color = b.dataset.c;
    renderSwatches();
  });
  $('#dlg-subject').addEventListener('close', () => {
    subjectDlg.resolve?.(null);
    subjectDlg.resolve = null;
  });

  // Calendrier
  $('#cal-add').onclick = () => openEventDialog();
  $('#cal-prev').onclick = () => shiftMonth(-1);
  $('#cal-next').onclick = () => shiftMonth(1);
  $('#cal-today').onclick = () => {
    const t = new Date();
    state.calMonth = new Date(t.getFullYear(), t.getMonth(), 1);
    renderCalendar();
  };
  $('#cal-grid').addEventListener('click', (e) => {
    const chip = e.target.closest('[data-ev]');
    if (chip) return openEventDialog((state.calEvents || []).find((ev) => ev.id === +chip.dataset.ev));
    const day = e.target.closest('[data-date]');
    if (day) openEventDialog(null, { date: day.dataset.date });
  });
  $('#ev-save').onclick = saveEvent;
  $('#ev-delete').onclick = deleteEvent;
  $('#search').addEventListener('input', doSearch);
  $('#search').addEventListener('focus', doSearch);
  $('#search').addEventListener('blur', () => setTimeout(() => ($('#search-results').hidden = true), 200));
  $('#search-results').addEventListener('mousedown', (e) => {
    const b = e.target.closest('button[data-id]');
    if (!b) return;
    e.preventDefault();
    state.pendingFind = $('#search').value.trim();
    $('#search-results').hidden = true;
    if (state.current?.id === +b.dataset.id) {
      openDoc(+b.dataset.id, { force: true });
    } else go(`#doc/${b.dataset.id}`);
  });

  // Accueil
  $('#home-browse').onclick = () => $('#file-input').click();
  $('#home-drop').addEventListener('click', (e) => {
    if (e.target === $('#home-drop') || e.target.closest('.dz-icon')) $('#file-input').click();
  });

  // En-tête du document
  $('#doc-title').addEventListener('input', saveTitle);
  $('#doc-title').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      editor.focus();
    }
  });
  $('#doc-subject').addEventListener('change', changeSubject);
  $('#doc-category').addEventListener('change', changeKind);
  $('#doc-parent').addEventListener('change', changeParent);
  $('#btn-translate').onclick = runTranslate;
  $('#btn-untranslate').onclick = untranslate;
  $$('#mode-switch button').forEach((b) => (b.onclick = () => applyMode(b.dataset.mode)));
  $('#btn-source').onclick = () => {
    if (!state.current) return;
    if ($('#doc-tabs [data-dtab="original"]')) showDocTab('original');
    else window.open(`/api/documents/${state.current.id}/source`, '_blank');
  };
  $('#btn-export').onclick = (e) => {
    e.stopPropagation();
    const m = $('#menu-export');
    const show = m.hidden;
    closeMenus();
    m.hidden = !show;
    if (show) setTimeout(() => document.addEventListener('click', closeMenus, { once: true }), 0);
  };
  $$('#menu-export button').forEach((b) => (b.onclick = () => exportDoc(b.dataset.export)));
  $('#btn-delete').onclick = deleteDoc;
  $('#btn-toggle-ai').onclick = () => {
    state.aiOpen = !state.aiOpen;
    applyAIOpen();
  };
  $('#ai-close').onclick = () => {
    state.aiOpen = false;
    applyAIOpen();
  };

  // Téléphone : barre du haut et tiroir de la bibliothèque
  $('#m-menu').onclick = () => setNav(!$('#app').classList.contains('nav-open'));
  $('#drawer-backdrop').onclick = () => setNav(false);
  $('#m-calendar').onclick = () => go('#calendar');
  $('#m-review').onclick = () => go('#review');
  $('#m-ai').onclick = () => {
    state.aiOpen = !state.aiOpen;
    setNav(false);
    applyAIOpen();
  };
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && $('#app').classList.contains('nav-open')) setNav(false);
  });
  phoneQuery.addEventListener('change', () => {
    setNav(false);
    applyMode(state.mode, false); // repasse en éditeur + aperçu en quittant le format téléphone
  });
  window.addEventListener('resize', debounce(() => fitToWidth(preview), 200)); // rotation de l'écran
  $('#doc-tabs').addEventListener('click', (e) => {
    const b = e.target.closest('[data-dtab]');
    if (b) showDocTab(b.dataset.dtab);
  });
  $('#btn-ocr').onclick = runOCR;

  // Éditeur
  $('#toolbar').addEventListener('click', (e) => {
    const b = e.target.closest('[data-cmd]');
    if (b) editorCommand(b.dataset.cmd);
  });
  editor.addEventListener('input', () => {
    if (!state.current) return;
    liveLocalChange();
    if (liveActive()) {
      livePush(); // en direct : envoyé tout de suite aux autres (et enregistré par le serveur)
    } else {
      state.dirty = true;
      setSaveState('Modifications…');
      scheduleSave();
    }
    liveSendCaret();
    renderPreview();
    updateStats();
    if (!$('#find-bar').hidden) computeMatches();
  });
  editor.addEventListener('keydown', handleEditorKeys);
  for (const ev of ['select', 'mouseup', 'keyup', 'focus']) {
    editor.addEventListener(ev, () => {
      state.previewSel = '';
      updateSelectionUI();
      liveSendCaret();
    });
  }
  editor.addEventListener('blur', liveSendCaret);
  // Clavier de téléphone : un mot en cours de composition ne doit pas être modifié sous les doigts.
  editor.addEventListener('compositionstart', () => liveHold(true));
  editor.addEventListener('compositionend', () => setTimeout(() => liveHold(false), 0));
  window.addEventListener('resize', drawCarets);
  $('#online').onclick = (e) => showPeople(e.currentTarget);
  $('#m-online').onclick = (e) => showPeople(e.currentTarget);
  $('#doc-people').onclick = (e) => showPeople(e.currentTarget);
  // Téléphone remis en route, réseau revenu : on se reconnecte sans attendre.
  const reconnect = () => {
    if (!live.ws && !live.stop && state.user && document.visibilityState === 'visible') {
      live.retry = 0;
      liveConnect();
    }
  };
  document.addEventListener('visibilitychange', reconnect);
  window.addEventListener('online', reconnect);
  editor.addEventListener('scroll', () => {
    if (live.carets.size) drawCarets();
    if (!$('#editor-split').classList.contains('mode-split')) return;
    const max = editor.scrollHeight - editor.clientHeight;
    if (max > 0) preview.scrollTop = (editor.scrollTop / max) * (preview.scrollHeight - preview.clientHeight);
  });
  document.addEventListener('selectionchange', () => {
    const ws = getSelection();
    if (ws && ws.anchorNode && preview.contains(ws.anchorNode)) {
      state.previewSel = ws.toString();
      updateSelectionUI();
    }
  });

  // Rechercher / remplacer
  $('#find-input').addEventListener('input', () => {
    computeMatches();
  });
  $('#find-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      gotoMatch(e.shiftKey ? -1 : 1);
    }
  });
  $('#replace-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      replaceOne();
    }
  });
  $('#find-case').onchange = computeMatches;
  $('#find-next').onclick = () => gotoMatch(1);
  $('#find-prev').onclick = () => gotoMatch(-1);
  $('#replace-one').onclick = replaceOne;
  $('#replace-all').onclick = replaceAll;
  $('#find-close').onclick = closeFind;

  // Panneau IA
  $('#ai-tabs').addEventListener('click', (e) => {
    const b = e.target.closest('[data-tab]');
    if (b) setAITab(b.dataset.tab);
  });
  $('#task-grid').addEventListener('click', (e) => {
    const b = e.target.closest('[data-task]');
    if (b) selectTask(b.dataset.task);
  });
  $('#task-grid').addEventListener('dblclick', (e) => {
    if (e.target.closest('[data-task]') && !state.controllers.gen) generate();
  });
  $('#opt-scope').addEventListener('click', (e) => {
    const b = e.target.closest('[data-scope]');
    if (!b || !state.current) return;
    state.scope = b.dataset.scope;
    store.set('genScope.' + state.current.subject_id, state.scope);
    refreshAIPanel();
  });
  $('#scope-list').addEventListener('change', (e) => {
    const id = +e.target.dataset.cm;
    if (!id) return;
    if (e.target.checked) state.scopeOff.delete(id);
    else state.scopeOff.add(id);
    renderScope();
  });
  $('#opt-difficulty').addEventListener('click', (e) => {
    const b = e.target.closest('[data-v]');
    if (!b) return;
    state.difficulty = b.dataset.v;
    $$('#opt-difficulty button').forEach((x) => x.classList.toggle('active', x === b));
  });
  $('#btn-generate').onclick = generate;
  $('#btn-gen-stop').onclick = () => state.controllers.gen?.abort();

  $('#chat-form').addEventListener('submit', (e) => {
    e.preventDefault();
    const v = $('#chat-input').value;
    $('#chat-input').value = '';
    sendChat(v);
  });
  $('#chat-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      $('#chat-form').requestSubmit();
    }
  });
  $('#chat-suggestions').addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    if (!b.textContent.endsWith('…')) return sendChat(b.textContent);
    // Suggestion à compléter (« Fais-moi un cours sur … ») : on la met dans le champ.
    const input = $('#chat-input');
    input.value = b.textContent.slice(0, -1);
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
  });
  $('#chat-log').addEventListener('click', (e) => {
    const b = e.target.closest('[data-open]');
    if (b) openCreatedDoc(+b.dataset.open);
  });
  // Fichiers donnés à l'IA : 📎, coller une capture d'écran, glisser-déposer sur le panneau
  $('#chat-attach').onclick = () => $('#chat-file-input').click();
  $('#chat-file-input').onchange = (e) => {
    addChatFiles(e.target.files);
    e.target.value = '';
  };
  $('#chat-files').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-i]');
    if (!b) return;
    (state.chatFiles[chatKey()] || []).splice(+b.dataset.i, 1);
    renderChatFiles();
  });
  $('#chat-input').addEventListener('paste', (e) => {
    const files = [...(e.clipboardData?.files || [])];
    if (!files.length) return;
    e.preventDefault();
    addChatFiles(files);
  });
  $('#ai-nodoc-chat').onclick = () => setAITab('chat');
  $('#chat-stop').onclick = () => state.controllers.chat?.abort();
  $('#chat-clear').onclick = () => {
    if (state.controllers.chat) return;
    delete state.chats[chatKey()];
    delete state.chatFiles[chatKey()];
    renderChat();
  };
  $('#chat-save').onclick = saveChat;

  $('#tool-grid').addEventListener('click', (e) => {
    const b = e.target.closest('[data-tool]');
    if (b) runTool(b.dataset.tool);
  });
  const customTool = () => {
    const v = $('#tool-custom').value.trim();
    if (!v) return toast('Écris une consigne.');
    runTool('custom', v);
  };
  $('#tool-custom-go').onclick = customTool;
  $('#tool-custom').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      customTool();
    }
  });
  $('#tool-replace').onclick = () => applyTool('replace');
  $('#tool-insert').onclick = () => applyTool('after');
  $('#tool-copy').onclick = () =>
    navigator.clipboard.writeText(state.toolCtx?.result || '').then(() => toast('Copié.'));
  $('#tool-discard').onclick = () => {
    $('#tool-output').hidden = true;
    state.toolCtx = null;
  };
  $('#tool-stop').onclick = () => state.controllers.tool?.abort();

  // Import
  $('#file-input').addEventListener('change', (e) => {
    const files = [...e.target.files];
    e.target.value = '';
    if (!files.length) return;
    if ($('#dlg-import').open) addImportFiles(files);
    else openImport(files);
  });
  $('#imp-browse').onclick = () => $('#file-input').click();
  $('#imp-drop').addEventListener('click', (e) => {
    if (e.target === $('#imp-drop')) $('#file-input').click();
  });
  $('#imp-list').addEventListener('click', (e) => {
    const b = e.target.closest('[data-rm]');
    if (!b) return;
    imp.files.splice(+b.dataset.rm, 1);
    imp.kinds.splice(+b.dataset.rm, 1);
    renderImportList();
  });
  $('#imp-list').addEventListener('change', (e) => {
    const sel = e.target.closest('[data-kind]');
    if (sel) imp.kinds[+sel.dataset.kind] = sel.value;
  });
  $('#imp-go').onclick = doImport;
  $('#imp-subject').addEventListener('change', async (e) => {
    if (e.target.value !== '__new') return;
    const s = await createSubject();
    fillImportSubjects(s ? s.id : '');
  });

  // Glisser-déposer de fichiers n'importe où dans la fenêtre
  let dragDepth = 0;
  const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes('Files');
  window.addEventListener('dragenter', (e) => {
    if (!hasFiles(e)) return;
    dragDepth += 1;
    $('#drop-overlay').hidden = false;
  });
  window.addEventListener('dragleave', (e) => {
    if (!hasFiles(e)) return;
    dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth) $('#drop-overlay').hidden = true;
  });
  // Au-dessus du panneau IA, les fichiers sont donnés à l'IA au lieu d'être importés.
  const overAI = (e) => !!e.target.closest?.('#ai-panel') && state.aiOpen && !$('#dlg-import').open;
  window.addEventListener('dragover', (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    const msg = overAI(e) ? "📎 Dépose ici pour donner le fichier à l'IA (sans l'importer)" : '📥 Dépose tes cours pour les importer';
    const label = $('#drop-overlay div');
    if (label.textContent !== msg) label.textContent = msg;
  });
  window.addEventListener('drop', (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    dragDepth = 0;
    $('#drop-overlay').hidden = true;
    const files = [...e.dataTransfer.files];
    if (!files.length) return;
    if (overAI(e)) {
      if (state.aiTab !== 'chat') setAITab('chat');
      addChatFiles(files);
      return;
    }
    const head = e.target.closest?.('.subject-head');
    if ($('#dlg-import').open) addImportFiles(files);
    else openImport(files, head ? head.dataset.sid : undefined);
  });

  // Réglages
  $('#set-temp').addEventListener('input', () => ($('#set-temp-val').textContent = $('#set-temp').value));
  $('#set-save').onclick = saveSettings;
  for (const form of $$('dialog form')) {
    form.addEventListener('submit', (e) => {
      if (e.submitter?.value !== 'cancel') e.preventDefault();
    });
  }

  // Révision globale
  $('#review-subject').addEventListener('change', () => {
    state.reviewSubject = $('#review-subject').value;
    startReview($('#review-global'), { subjectId: state.reviewSubject ? +state.reviewSubject : null });
  });

  document.addEventListener('keydown', handleGlobalKeys);
  window.addEventListener('hashchange', route);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') flushSave();
  });
  window.addEventListener('beforeunload', () => {
    if (state.dirty && state.current) {
      fetch(`/api/documents/${state.current.id}`, {
        method: 'PATCH',
        keepalive: true,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: editor.value }),
      });
    }
  });
}

async function init() {
  bindEvents();
  renderTaskGrid();
  renderToolGrid();
  applyAIOpen();
  applyMode(state.mode, false);
  setAITab('generate');
  await ensureLogin(); // outil partagé : connexion avant tout le reste
  liveConnect(); // présence et modifications en direct
  try {
    state.settings = await api('/api/settings');
    await loadLibrary();
  } catch (e) {
    toast('Impossible de charger la bibliothèque : ' + e.message, { error: true });
  }
  await refreshHealth();
  setInterval(refreshHealth, 30000);
  refreshDueCount();
  route();
}

init();
