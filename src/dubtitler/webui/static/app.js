'use strict';
/* Review app: dashboard, transcript (gate 1), translation (gate 2), glossary.
 *
 * No build step and no framework. The data is small, the views are four, and a
 * toolchain would outweigh both. Everything is fetched fresh from the server,
 * which reads it from disk, so a video that finishes the pipeline shows up on
 * refresh with nothing to regenerate.
 *
 * Nothing here knows which languages the job uses. The labels come from
 * /api/state, so the same page serves German to Norwegian and English to
 * Spanish without a change.
 */

const $ = s => document.querySelector(s);
const el = (t, cls, txt) => {
  const e = document.createElement(t);
  if (cls) e.className = cls;
  if (txt != null) e.textContent = txt;
  return e;
};
const esc = s => String(s ?? '').replace(/[&<>"]/g, c =>
  ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
const pad = (n, w) => String(n).padStart(w, '0');
const ts = s => `${pad(Math.floor(s / 60), 2)}:${pad(Math.floor(s % 60), 2)}`;

const api = async (path, opts) => {
  const r = await fetch(path, opts);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.error || r.statusText);
  return body;
};

function setStatus(cls, msg) {
  const e = $('#status');
  e.className = 'status ' + (cls || '');
  e.textContent = msg;
}

/* ---------------------------------------------------------------- player */
/* One <audio> for the whole app. Switching video swaps .src; the browser
   caches each file, so moving between views costs nothing. */
const player = {
  audio: $('#player'),
  slug: null,
  stopAt: null,
  loop: false,
  onTick: null,

  use(slug) {
    if (this.slug === slug) return;
    this.slug = slug;
    this.audio.src = `/audio/${slug}.mp3`;
  },
  play(slug, start, end) {
    this.use(slug);
    const go = () => {
      this.audio.currentTime = start;
      this.stopAt = end;
      this.audio.play().catch(() => {});
    };
    // readyState 0 means metadata has not arrived, so currentTime is ignored.
    if (this.audio.readyState === 0) {
      this.audio.addEventListener('loadedmetadata', go, {once: true});
      this.audio.load();
    } else go();
  },
  toggle() { this.audio.paused ? this.audio.play() : this.audio.pause(); },
  stop() { this.audio.pause(); this.stopAt = null; },
};
/* A video with review data but no source file in media/ fails here. Without
   this the play button is silently inert, which reads as a bug in the app
   rather than a gap in the inputs. */
player.audio.addEventListener('error', () => {
  if (!player.slug) return;
  setStatus('error', `no audio for ${player.slug}, is the source video in media/?`);
});
player.audio.addEventListener('timeupdate', () => {
  if (player.stopAt !== null && player.audio.currentTime >= player.stopAt) {
    if (player.loop && player.loopStart != null) {
      player.audio.currentTime = player.loopStart;
    } else {
      player.audio.pause();
      player.stopAt = null;
    }
  }
  if (player.onTick) player.onTick(player.audio.currentTime);
});

/* ------------------------------------------------------------- autosave */
function autosaver(url, buildPayload) {
  let timer = null;
  const save = async () => {
    setStatus('saving', 'saving…');
    try {
      const r = await api(url, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(buildPayload()),
      });
      document.querySelectorAll('.dirty').forEach(e => e.classList.remove('dirty'));
      setStatus('saved', `saved → ${r.wrote}`);
    } catch (e) {
      setStatus('error', 'save failed: ' + e.message);
    }
  };
  return {
    now: save,
    queue(target) {
      if (target) target.classList.add('dirty');
      setStatus('saving', 'saving…');
      clearTimeout(timer);
      timer = setTimeout(save, 800);
    },
  };
}

/* --------------------------------------------------------------- routing */
const routes = [
  [/^#\/$/, () => viewDashboard()],
  [/^#\/v\/([^/]+)\/transcript$/, m => viewTranscript(m[1])],
  [/^#\/v\/([^/]+)\/translation$/, m => viewTranslation(m[1])],
  [/^#\/glossary$/, () => viewGlossary()],
];

let STATE = null;

async function render() {
  const hash = location.hash || '#/';
  $('#view').innerHTML = '<p class="loading">loading…</p>';
  $('#hint').textContent = '';
  $('#tools').innerHTML = '';
  player.stop();
  player.onTick = null;
  setStatus('', '');

  if (!STATE) {
    STATE = await api('/api/state');
    document.title = `${STATE.project}, review`;
    $('#project').textContent = STATE.project;
  }
  buildNav(hash);

  for (const [re, fn] of routes) {
    const m = hash.match(re);
    if (m) {
      try { await fn(m); } catch (e) {
        $('#view').innerHTML = `<p class="loading">error: ${esc(e.message)}</p>`;
      }
      return;
    }
  }
  location.hash = '#/';
}

function buildNav(hash) {
  const nav = $('#nav');
  nav.innerHTML = '';
  for (const v of STATE.videos) {
    // The nav is tight, so show a short form and keep the filename in the
    // tooltip: the file on disk must always stay findable.
    const short = v.title.split(/\s+[, –-]\s+/)[0];
    const a = el('a', '', short.length > 28 ? short.slice(0, 27) + '…' : short);
    a.title = `${v.title}  (${v.video})`;
    a.href = `#/v/${v.slug}/transcript`;
    if (hash.includes(`/v/${v.slug}/`)) a.className = 'on';
    nav.append(a);
  }
  const g = el('a', hash === '#/glossary' ? 'on' : '', 'glossary');
  g.href = '#/glossary';
  nav.append(g);
}

window.addEventListener('hashchange', render);

/* ------------------------------------------------------------- dashboard */
function viewDashboard() {
  const wrap = el('div', 'cards');
  for (const v of STATE.videos) {
    const c = el('div', 'card');
    // The title is editable in place: whatever produced it is a starting
    // point, and retyping it here is faster than editing videos.md.
    const h = el('input', 'title');
    h.value = v.title;
    h.spellcheck = false;
    h.title = 'rename, saves to project/videos.md';
    h.onchange = async () => {
      const title = h.value.trim();
      if (!title || title === v.title) { h.value = v.title; return; }
      setStatus('saving', 'saving…');
      try {
        const r = await api('/api/videos', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({videos: [{video: v.video, title}]}),
        });
        v.title = title;
        setStatus('saved', `saved → ${r.wrote}`);
        STATE = await api('/api/state');
        buildNav(location.hash);
      } catch (e) { setStatus('error', 'save failed: ' + e.message); h.value = v.title; }
    };
    const src = el('div', 'srcname', v.video);
    const stage = el('span', 'stage' + (v.has_render ? '' : ' early'), v.stage);
    c.append(h, src, stage);

    const pct = v.segments ? Math.round(100 * v.reviewed / v.segments) : 0;
    const meta = el('div', 'meta');
    meta.innerHTML =
      `<b>${v.segments}</b> segments · <b>${v.flagged}</b> flagged · ` +
      `<b>${v.reviewed}</b> reviewed<br>` +
      `<b>${v.translated}</b>/${v.sentences} sentences translated`;
    const bar = el('div', 'bars');
    bar.innerHTML = `<i style="width:${pct}%"></i>`;
    c.append(meta, bar);

    const links = el('div', 'golinks');
    const t = el('a', '', 'transcript'); t.href = `#/v/${v.slug}/transcript`;
    links.append(t);
    if (v.translated) {
      const x = el('a', '', 'translation'); x.href = `#/v/${v.slug}/translation`;
      links.append(x);
    }
    c.append(links);
    wrap.append(c);
  }

  const g = el('div', 'card');
  g.innerHTML = `<h2>Glossary</h2><span class="stage">${
    STATE.glossary.approved === STATE.glossary.terms ? 'approved' : 'in review'}</span>
    <div class="meta"><b>${STATE.glossary.approved}</b>/<b>${STATE.glossary.terms}</b> terms approved</div>
    <div class="golinks"><a href="#/glossary">open</a></div>`;
  wrap.append(g);

  $('#view').replaceChildren(wrap);
  $('#hint').textContent =
    'Everything here is read from disk on each request: finish the pipeline for '
    + 'another video and it appears on refresh.';
}

/* ------------------------------------------------------- transcript view */
async function viewTranscript(slug) {
  const {transcript: d} = await api(`/api/video/${slug}`);
  const segs = d.segments;
  player.use(slug);

  const saver = autosaver(`/api/transcript/${slug}`, () => ({
    segments: segs.map((s, i) => ({id: i, text: $(`#t${i}`).value.trim()})),
    reviewed: segs.map((s, i) => i).filter(i => $(`#c${i}`).checked),
  }));

  let current = -1, lastWord = null;
  const wrap = el('div');

  segs.forEach((s, i) => {
    const box = el('div', 'seg' + (s.flags.length ? ' flagged' : ''));
    box.id = `s${i}`;
    const head = el('div', 'head');
    const btn = el('button', 'tc', `▶ ${ts(s.start)}`);
    btn.onclick = () => play(i);
    head.append(btn);
    if (s.speaker) head.append(el('span', 'spk', s.speaker));
    s.flags.forEach(f => head.append(el('span', 'chip', f)));
    box.append(head);

    s.notes.forEach(n => {
      const d2 = el('div', 'note');
      d2.innerHTML = esc(n).replace(/`([^`]*)`/g, '<code>$1</code>');
      box.append(d2);
    });

    const words = el('div', 'words');
    words.id = `w${i}`;
    s.words.forEach(w => {
      words.append(el('span', w.disputed ? 'd' : '', w.w));
      words.append(document.createTextNode(' '));
    });
    box.append(words);

    const ta = el('textarea');
    ta.id = `t${i}`; ta.rows = 2; ta.spellcheck = false; ta.value = s.text;
    ta.oninput = () => saver.queue(ta);
    box.append(ta);

    const foot = el('div', 'foot');
    const lab = el('label');
    const cb = el('input'); cb.type = 'checkbox'; cb.id = `c${i}`;
    cb.checked = d.reviewed.includes(i);
    if (cb.checked) box.classList.add('done');
    cb.onchange = () => { box.classList.toggle('done', cb.checked); count(); saver.now(); };
    lab.append(cb, document.createTextNode('reviewed'));
    foot.append(lab);
    box.append(foot);
    wrap.append(box);
  });

  function play(i) {
    current = i; lastWord = null;
    document.querySelectorAll('.seg').forEach(e => e.classList.remove('playing'));
    const box = $(`#s${i}`);
    box.classList.add('playing');
    box.scrollIntoView({block: 'nearest', behavior: 'smooth'});
    player.loopStart = segs[i].start;
    player.play(slug, segs[i].start, segs[i].end);
  }
  function step(delta) {
    play(Math.min(segs.length - 1, Math.max(0, current < 0 ? 0 : current + delta)));
  }
  player.onTick = t => {
    if (current < 0) return;
    const w = segs[current].words;
    let hit = null;
    for (let k = 0; k < w.length; k++) {
      if (t >= w[k].start && t <= w[k].end) { hit = k; break; }
    }
    if (hit === lastWord) return;
    const box = $(`#w${current}`);
    if (box) {
      box.querySelectorAll('span').forEach(e => e.classList.remove('now'));
      if (hit !== null) box.children[hit]?.classList.add('now');
    }
    lastWord = hit;
  };

  function count() {
    const n = segs.filter((s, i) => $(`#c${i}`).checked).length;
    $('#counter').textContent = `${n}/${segs.length} reviewed`;
  }

  tools(slug, {
    counter: true,
    onlyFlagged: on => segs.forEach((s, i) => {
      $(`#s${i}`).style.display = (on && !s.flags.length) ? 'none' : '';
    }),
    keys: {play: () => player.toggle(), step, edit: () => $(`#t${current}`)?.focus()},
  });

  $('#view').replaceChildren(heading(d.title, d.video, 'transcript'), wrap);
  count();
  const engines = d.engines && d.engines.length > 1
    ? `Wavy red underline marks a word ${d.engines.join(', ')} transcribed differently.`
    : 'Only one engine ran, so flags come from its own confidence numbers.';
  $('#hint').innerHTML =
    '<kbd>space</kbd> play/pause &nbsp; <kbd>J</kbd>/<kbd>K</kbd> next/previous ' +
    '&nbsp; <kbd>L</kbd> loop &nbsp; <kbd>enter</kbd> edit &nbsp; <kbd>esc</kbd> ' +
    'leave the box. ' + esc(engines);
}

/* ------------------------------------------------------ translation view */
async function viewTranslation(slug) {
  const {translation: d} = await api(`/api/video/${slug}`);
  const rows = d.sentences;
  player.use(slug);

  let current = -1;
  const wrap = el('div');

  rows.forEach((s, i) => {
    const box = el('div', 'tr' + (s.flags.length ? ' flagged' : ''));
    box.id = `r${i}`;
    const head = el('div', 'head');
    const btn = el('button', 'tc', `▶ ${ts(s.start)}`);
    btn.onclick = () => play(i);
    head.append(btn);
    if (s.speaker) head.append(el('span', 'spk', s.speaker));
    s.flags.forEach(f => head.append(el('span', 'chip', f)));
    box.append(head);

    const pair = el('div', 'pair');
    const l = el('div');
    l.append(el('div', 'lbl', STATE.source.name), el('div', '', s.source));
    const r = el('div');
    r.append(el('div', 'lbl', STATE.target.name),
             el('div', 'no', s.target || '—'));
    pair.append(l, r);

    if (s.back) {
      const b = el('div', 'back');
      b.append(el('div', 'lbl', 'back-translation'));
      const t = el('div');
      t.innerHTML = diffWords(s.source, s.back);
      b.append(t);
      pair.append(b);
    }
    box.append(pair);
    wrap.append(box);
  });

  function play(i) {
    current = i;
    document.querySelectorAll('.tr').forEach(e => e.classList.remove('playing'));
    const box = $(`#r${i}`);
    box.classList.add('playing');
    box.scrollIntoView({block: 'nearest', behavior: 'smooth'});
    player.loopStart = rows[i].start;
    player.play(slug, rows[i].start, rows[i].end);
  }
  const step = d2 =>
    play(Math.min(rows.length - 1, Math.max(0, current < 0 ? 0 : current + d2)));

  tools(slug, {
    onlyFlagged: on => rows.forEach((s, i) => {
      $(`#r${i}`).style.display = (on && !s.flags.length) ? 'none' : '';
    }),
    keys: {play: () => player.toggle(), step},
  });

  $('#view').replaceChildren(heading(d.title, d.video, 'translation'), wrap);
  const flagged = rows.filter(r => r.flags.length).length;
  setStatus('', `${rows.length} sentences · ${flagged} flagged`);
  $('#hint').innerHTML =
    '<kbd>space</kbd> play/pause &nbsp; <kbd>J</kbd>/<kbd>K</kbd> next/previous ' +
    `&nbsp; <kbd>L</kbd> loop. Highlighted words in the back-translation differ ` +
    `from the ${esc(STATE.source.name)}: read those, not the whole column.`;
}

function heading(title, video, view) {
  const h = el('div', 'viewhead');
  h.append(el('h1', '', title), el('span', 'srcname', `${video} · ${view}`));
  return h;
}

/* Word-level diff of the source against its back-translation. */
function diffWords(a, b) {
  const A = a.split(/\s+/), B = b.split(/\s+/);
  const norm = w => w.toLowerCase().replace(/[^\p{L}\p{N}]/gu, '');
  const setA = new Set(A.map(norm));
  return B.map(w => setA.has(norm(w)) ? esc(w) : `<mark>${esc(w)}</mark>`).join(' ');
}

/* --------------------------------------------------------- glossary view */
async function viewGlossary() {
  const d = await api('/api/glossary');
  const terms = d.terms;

  const saver = autosaver('/api/glossary', () => ({
    terms: terms.map((t, i) => ({
      term: t.term,
      translation: $(`#n${i}`).value.trim(),
      notes: $(`#m${i}`).value.trim(),
      approved: $(`#a${i}`).checked,
    })),
  }));

  const wrap = el('div');
  terms.forEach((t, i) => {
    const box = el('div', 'term' + (t.approved ? ' confirmed done' : ''));
    box.id = `g${i}`;

    const head = el('div', 'termhead');
    head.append(el('span', 'de', t.term), el('span', 'arrow', '→'));
    const tr = el('input', 'no');
    tr.id = `n${i}`; tr.value = t.translation; tr.spellcheck = false;
    tr.oninput = () => saver.queue(tr);
    head.append(tr);
    const lab = el('label', 'ok');
    const cb = el('input'); cb.type = 'checkbox'; cb.id = `a${i}`; cb.checked = t.approved;
    cb.onchange = () => { box.classList.toggle('done', cb.checked); count(); saver.now(); };
    lab.append(cb, document.createTextNode('approved'));
    head.append(lab);
    box.append(head);

    const notes = el('input', 'notes'); notes.id = `m${i}`; notes.value = t.notes;
    notes.placeholder = 'notes'; notes.spellcheck = false;
    notes.oninput = () => saver.queue(notes);
    box.append(notes);

    const occs = el('div', 'occs');
    if (!t.occurrences.length) {
      occs.append(el('div', 'occ muted', 'no occurrence found in any transcript'));
    }
    t.occurrences.forEach(o => {
      const row = el('div', 'occ');
      const btn = el('button', 'tc', `▶ ${o.video} ${ts(o.start)}`);
      btn.onclick = () => { player.loopStart = o.start; player.play(o.slug, o.start, o.end); };
      const span = el('span');
      span.innerHTML = highlight(o.text, t.term);
      row.append(btn, span);
      occs.append(row);
    });
    box.append(occs);
    wrap.append(box);
  });

  function count() {
    const n = terms.filter((t, i) => $(`#a${i}`).checked).length;
    $('#counter').textContent = `${n}/${terms.length} approved`;
  }

  tools(null, {
    counter: true,
    onlyFlagged: on => terms.forEach((t, i) => {
      $(`#g${i}`).style.display = (on && $(`#a${i}`).checked) ? 'none' : '';
    }),
    filterLabel: 'hide approved',
    extra: [['approve all', () => {
      terms.forEach((t, i) => { $(`#a${i}`).checked = true; $(`#g${i}`).classList.add('done'); });
      count(); saver.now();
    }]],
  });

  $('#view').replaceChildren(wrap);
  count();
  $('#hint').textContent =
    'Each term lists the sentences it occurs in, press ▶ to hear one. '
    + 'A green left edge marks a reading confirmed against the audio at gate 1.';
}

function highlight(text, term) {
  const stem = term.split(' ')[0].replace(/s$/, '')
    .replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return esc(text).replace(new RegExp(`(\\b${stem}\\w*)`, 'ig'), '<b>$1</b>');
}

/* ----------------------------------------------------------- toolbar/keys */
function tools(slug, opts) {
  const box = $('#tools');
  box.innerHTML = '';
  if (opts.counter) {
    const c = el('span', 'status'); c.id = 'counter';
    box.append(c);
  }
  if (opts.onlyFlagged) {
    const b = el('button', '', opts.filterLabel || 'flagged only');
    b.onclick = () => { b.classList.toggle('on'); opts.onlyFlagged(b.classList.contains('on')); };
    box.append(b);
  }
  if (opts.keys) {
    const l = el('button', '', 'loop');
    l.onclick = () => { player.loop = !player.loop; l.classList.toggle('on', player.loop); };
    const rate = el('select');
    [['0.75', '0.75×'], ['1', '1×'], ['1.25', '1.25×'], ['1.5', '1.5×']]
      .forEach(([v, t]) => {
        const o = el('option', '', t); o.value = v; if (v === '1') o.selected = true;
        rate.append(o);
      });
    rate.onchange = e => { player.audio.playbackRate = parseFloat(e.target.value); };
    box.append(l, rate);
  }
  (opts.extra || []).forEach(([label, fn]) => {
    const b = el('button', '', label); b.onclick = fn; box.append(b);
  });
  KEYS = opts.keys || null;
}

let KEYS = null;
document.addEventListener('keydown', e => {
  const typing = /^(INPUT|TEXTAREA)$/.test(e.target.tagName);
  if (e.key === 'Escape' && typing) { e.target.blur(); return; }
  if (typing || !KEYS) return;
  if (e.key === ' ') { e.preventDefault(); KEYS.play(); }
  else if (e.key === 'j' || e.key === 'ArrowDown') { e.preventDefault(); KEYS.step(1); }
  else if (e.key === 'k' || e.key === 'ArrowUp') { e.preventDefault(); KEYS.step(-1); }
  else if (e.key === 'l') {
    player.loop = !player.loop;
    document.querySelectorAll('#tools button').forEach(b => {
      if (b.textContent === 'loop') b.classList.toggle('on', player.loop);
    });
  } else if (e.key === 'Enter' && KEYS.edit) { e.preventDefault(); KEYS.edit(); }
});

render();
