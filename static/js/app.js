const S = {
    H: 0, W: 0,
    baseZoom: 1,
    uiZoom: 1,
    strips: [], cuts: [], origCuts: [], sel: null, loaded: false,
    stripImgs: [],
};

const ZOOM_STEPS = [0.25, 0.33, 0.5, 0.67, 0.75, 1, 1.25, 1.5, 2, 2.5, 3];
let zoomIdx = 5;

const G = id => document.getElementById(id);
const pdfCanvas = G('pdf-canvas');
const overlay = G('cut-overlay');
const cutList = G('cut-list');
const outName = G('out-name');
const loadDiv = G('loading');
const scroll = G('c-scroll');

const mm = pt => (pt / 72 * 25.4).toFixed(0);
const tpx = pt => pt * S.baseZoom * S.uiZoom;
const ptp = px => px / (S.baseZoom * S.uiZoom);

let dark = true;
G('theme-btn').addEventListener('click', () => {
    dark = !dark;
    document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light');
    G('theme-icon').className = dark ? 'fa-solid fa-moon' : 'fa-solid fa-sun';
    if (S.loaded) drawCuts();
});

const showLoad = msg => { G('loading-msg').textContent = msg; loadDiv.classList.remove('hidden'); };
const hideLoad = () => loadDiv.classList.add('hidden');
let toastT;
function toast(msg, type = 'ok') {
    const el = G('toast'), ic = el.querySelector('.ti');
    G('tm').textContent = msg;
    ic.className = `ti fa-solid ${type === 'ok' ? 'fa-circle-check' : 'fa-circle-exclamation'}`;
    el.className = `show ${type}`;
    clearTimeout(toastT);
    toastT = setTimeout(() => el.className = '', 2800);
}

function stats() {
    const n = S.cuts.length + 1;
    G('sp').textContent = `${n} page${n !== 1 ? 's' : ''}`;
    G('sc').textContent = `${S.cuts.length} cut${S.cuts.length !== 1 ? 's' : ''}`;
    G('sd').textContent = S.loaded ? `${mm(S.W)}x${mm(S.H)} mm` : '--';
    ['pp', 'pc', 'pd'].forEach(id => G(id).classList.toggle('lit', S.loaded));
}

function applyZoom() {
    S.uiZoom = ZOOM_STEPS[zoomIdx];
    G('z-val').textContent = Math.round(S.uiZoom * 100) + '%';
    if (!S.loaded) return;

    const scrollRatio = scroll.scrollTop / (scroll.scrollHeight || 1);

    redrawCanvas();
    drawCuts();

    requestAnimationFrame(() => {
        scroll.scrollTop = scrollRatio * scroll.scrollHeight;
    });
}

G('z-in').addEventListener('click', () => {
    if (zoomIdx < ZOOM_STEPS.length - 1) { zoomIdx++; applyZoom(); }
});
G('z-out').addEventListener('click', () => {
    if (zoomIdx > 0) { zoomIdx--; applyZoom(); }
});
G('z-fit').addEventListener('click', () => {
    zoomIdx = 5; applyZoom(); // reset to 100%
});

scroll.addEventListener('wheel', e => {
    if (!e.ctrlKey && !e.metaKey) return;
    e.preventDefault();
    if (e.deltaY < 0 && zoomIdx < ZOOM_STEPS.length - 1) zoomIdx++;
    else if (e.deltaY > 0 && zoomIdx > 0) zoomIdx--;
    applyZoom();
}, { passive: false });

const dz = G('dz'), fi = G('fi');
const autoCuts = G('auto-cuts');
dz.addEventListener('click', () => fi.click());
fi.addEventListener('change', e => e.target.files[0] && upload(e.target.files[0]));
dz.addEventListener('dragover', e => { e.preventDefault(); dz.classList.add('drag'); });
dz.addEventListener('dragleave', () => dz.classList.remove('drag'));
dz.addEventListener('drop', e => {
    e.preventDefault(); dz.classList.remove('drag');
    const f = e.dataTransfer.files[0];
    f && f.type === 'application/pdf' ? upload(f) : toast('Please select a PDF file', 'err');
});

async function upload(file) {
    showLoad(autoCuts.checked ? 'Analyzing PDF...' : 'Loading preview...');
    G('empty-state').style.display = 'none';
    G('fn').textContent = file.name;
    G('ftag').classList.add('show');

    const fd = new FormData();
    fd.append('file', file);
    fd.append('auto_cut', autoCuts.checked ? '1' : '0');
    try {
        const r = await fetch('/upload', { method: 'POST', body: fd });
        if (!r.ok) throw new Error(await r.text());
        const d = await r.json();

        S.H = d.total_height;
        S.W = d.page_width;
        S.baseZoom = d.zoom_preview;
        S.strips = d.strips;
        S.cuts = [...d.cut_points].sort((a, b) => a - b);
        S.origCuts = [...S.cuts];
        S.loaded = true;
        S.stripImgs = [];

        outName.value = d.orig_name || 'output';
        G('btn-reset').disabled = G('btn-clear').disabled = G('btn-dl').disabled = false;
        if (d.bars_removed > 0) toast(`${d.bars_removed} dark bar${d.bars_removed > 1 ? 's' : ''} removed`);

        zoomIdx = 5; S.uiZoom = 1; G('z-val').textContent = '100%';

        loadStrips().then(() => { redrawCanvas(); drawCuts(); drawList(); stats(); });

    } catch (e) {
        toast('Error: ' + e.message, 'err');
    } finally { hideLoad(); }
}

function loadStrips() {
    return new Promise(resolve => {
        S.stripImgs = [];
        let left = S.strips.length;
        if (!left) { resolve(); return; }
        S.strips.forEach((strip, i) => {
            const img = new Image();
            img.onload = () => {
                S.stripImgs[i] = { img, strip };
                if (!--left) resolve();
            };
            img.src = 'data:image/png;base64,' + strip.b64;
        });
    });
}

function redrawCanvas() {
    const W = Math.round(tpx(S.W));
    const H = Math.round(tpx(S.H));

    pdfCanvas.width = W;
    pdfCanvas.height = H;
    overlay.setAttribute('width', W);
    overlay.setAttribute('height', H);
    overlay.setAttribute('viewBox', `0 0 ${W} ${H}`);

    const ctx = pdfCanvas.getContext('2d');
    ctx.fillStyle = '#fff';
    ctx.fillRect(0, 0, W, H);

    S.stripImgs.forEach(({ img, strip }) => {
        const yPx = Math.round(tpx(strip.y_start));
        const destH = Math.round(tpx(strip.y_end - strip.y_start));
        ctx.drawImage(img, 0, yPx, W, destH);
    });
}

function drawCuts() {
    overlay.innerHTML = '';
    const W = tpx(S.W);

    const bounds = [0, ...S.cuts, S.H].sort((a, b) => a - b);
    bounds.forEach((b, i) => {
        if (i >= bounds.length - 1) return;
        const r = svgEl('rect');
        sa(r, {
            x: 0, y: tpx(bounds[i]),
            width: W, height: tpx(bounds[i + 1] - bounds[i]),
            fill: i % 2 === 0 ? 'var(--stripe-a)' : 'var(--stripe-b)',
        });
        r.classList.add('ptint');
        overlay.appendChild(r);
    });

    S.cuts.forEach((pt, idx) => makeLine(pt, idx, W));
}

function makeLine(pt, idx, W) {
    const y = tpx(pt);
    const isSel = S.sel === idx;
    const g = svgEl('g');
    g.classList.add('cut-g');
    if (isSel) g.classList.add('active');
    g.dataset.idx = idx;

    const grab = svgEl('rect');
    sa(grab, { x: 0, y: y - 12, width: W, height: 24, fill: 'transparent' });
    grab.style.pointerEvents = 'all';
    g.appendChild(grab);

    const shadow = svgEl('line');
    sa(shadow, { x1: 0, x2: W, y1: y + 1.5, y2: y + 1.5, class: 'c-shadow' });
    g.appendChild(shadow);

    const line = svgEl('line');
    sa(line, { x1: 0, x2: W, y1: y, y2: y, class: 'c-line' });
    g.appendChild(line);

    [20, W - 20].forEach(cx => {
        const c = svgEl('circle');
        sa(c, { cx, cy: y, r: isSel ? 6 : 5, class: 'c-dot' });
        g.appendChild(c);
    });

    const label = `${mm(pt)} mm`;
    const lw = label.length * 6.3 + 14;
    const lx = 36;

    const pill = svgEl('rect');
    sa(pill, { x: lx, y: y - 13, width: lw, height: 14, rx: 3, class: 'c-pill' });
    g.appendChild(pill);

    const txt = svgEl('text');
    sa(txt, { x: lx + lw / 2, y: y - 3, 'text-anchor': 'middle', class: 'c-txt' });
    txt.textContent = label;
    g.appendChild(txt);

    setupDrag(g, idx);
    g.addEventListener('click', e => { e.stopPropagation(); select(idx); });
    overlay.appendChild(g);
}

const svgEl = t => document.createElementNS('http://www.w3.org/2000/svg', t);
function sa(el, obj) { for (const [k, v] of Object.entries(obj)) el.setAttribute(k, v); }

function setupDrag(g, idx) {
    let sy, sp;
    let ghostLine;

    const onMove = e => {
        const np = Math.max(5, Math.min(S.H - 5, sp + ptp(e.clientY - sy)));
        const currentY = tpx(np);

        // Muoviamo solo la linea fantasma
        if (ghostLine) {
            sa(ghostLine, { y1: currentY, y2: currentY });
        }

        const listItem = cutList.querySelector(`.cut-row[data-i="${idx}"] .cut-val`);
        const listPt = cutList.querySelector(`.cut-row[data-i="${idx}"] .cut-pt`);
        if (listItem) listItem.textContent = `${mm(np)} mm`;
        if (listPt) listPt.textContent = `${Math.round(np)} pt`;
    };

    const onUp = e => {
        g.classList.remove('active');
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);

        // Rimuoviamo la linea fantasma dal DOM
        if (ghostLine) {
            ghostLine.remove();
            ghostLine = null;
        }

        const np = Math.max(5, Math.min(S.H - 5, sp + ptp(e.clientY - sy)));
        S.cuts[idx] = np;

        S.cuts.sort((a, b) => a - b);
        drawCuts();
        drawList();
        stats();
    };

    g.addEventListener('mousedown', e => {
        e.preventDefault();
        e.stopPropagation();
        sy = e.clientY;
        sp = S.cuts[idx];
        g.classList.add('active');
        select(idx);

        ghostLine = svgEl('line');
        sa(ghostLine, {
            x1: 0,
            x2: tpx(S.W),
            y1: tpx(sp),
            y2: tpx(sp),
            stroke: 'var(--cut-active-stroke)',
            'stroke-width': 2,
            'stroke-dasharray': '6 4',
            opacity: 0.6,
            'pointer-events': 'none'
        });
        overlay.appendChild(ghostLine);

        document.addEventListener('mousemove', onMove);
        document.addEventListener('mouseup', onUp);
    });
}

pdfCanvas.addEventListener('click', e => {
    if (!S.loaded) return;
    const pt = ptp(e.clientY - pdfCanvas.getBoundingClientRect().top);
    if (S.cuts.some(c => Math.abs(c - pt) < 15)) { toast('Too close to an existing cut', 'err'); return; }
    S.cuts.push(pt);
    S.cuts.sort((a, b) => a - b);
    select(S.cuts.indexOf(pt));
    drawCuts(); drawList(); stats();
});

function select(idx) { S.sel = idx; drawCuts(); drawList(); }

function drawList() {
    if (!S.cuts.length) {
        cutList.innerHTML = `<div class="cut-empty"><i class="fa-regular fa-scissors"></i>No cuts yet - click the PDF to add one</div>`;
        return;
    }
    const bounds = [0, ...S.cuts, S.H].sort((a, b) => a - b);
    cutList.innerHTML = S.cuts.map((pt, i) => {
        const secH = bounds[i + 2] - bounds[i + 1];
        return `
    <div class="cut-row ${S.sel === i ? 'sel' : ''}" data-i="${i}">
      <div class="cut-num">${i + 1}</div>
      <div class="cut-val">${mm(pt)} mm</div>
      <div class="cut-pt">${Math.round(pt)} pt</div>
      <button class="cut-x" data-i="${i}"><i class="fa-solid fa-xmark"></i></button>
    </div>
    <div class="sec-sep"><i class="fa-solid fa-arrows-up-down" style="font-size:7px;margin-right:3px"></i>${mm(secH)} mm</div>`;
    }).join('');

    cutList.querySelectorAll('.cut-row').forEach(row => {
        row.addEventListener('click', () => {
            const i = +row.dataset.i; select(i);
            scroll.scrollTo({ top: tpx(S.cuts[i]) - scroll.clientHeight / 2, behavior: 'smooth' });
        });
    });
    cutList.querySelectorAll('.cut-x').forEach(btn => {
        btn.addEventListener('click', e => { e.stopPropagation(); delCut(+btn.dataset.i); });
    });
}

function delCut(i) { S.cuts.splice(i, 1); S.sel = null; drawCuts(); drawList(); stats(); }

document.addEventListener('keydown', e => {
    if (document.activeElement === outName) return;
    if ((e.key === 'Delete' || e.key === 'Backspace') && S.sel !== null) delCut(S.sel);
    if (e.key === 'Escape') { S.sel = null; drawCuts(); drawList(); }
    if (e.key === '+' || (e.ctrlKey && e.key === '=')) { e.preventDefault(); if (zoomIdx < ZOOM_STEPS.length - 1) { zoomIdx++; applyZoom(); } }
    if (e.key === '-' && e.ctrlKey) { e.preventDefault(); if (zoomIdx > 0) { zoomIdx--; applyZoom(); } }
});

G('btn-reset').addEventListener('click', () => {
    S.cuts = [...S.origCuts]; S.sel = null;
    drawCuts(); drawList(); stats();
    toast('Cuts reset to suggestions');
});
G('btn-clear').addEventListener('click', () => {
    S.cuts = []; S.sel = null;
    drawCuts(); drawList(); stats();
});

G('btn-dl').addEventListener('click', async () => {
    if (!S.loaded) return;
    const name = outName.value.trim() || 'output';
    showLoad('Generating PDF...');
    try {
        const r = await fetch('/paginate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ cut_points: S.cuts, name }),
        });
        if (!r.ok) throw new Error(await r.text());
        const a = Object.assign(document.createElement('a'), {
            href: URL.createObjectURL(await r.blob()),
            download: name.endsWith('.pdf') ? name : name + '.pdf',
        });
        a.click(); URL.revokeObjectURL(a.href);
        toast(`${name}.pdf downloaded`);
    } catch (e) {
        toast('Download error: ' + e.message, 'err');
    } finally { hideLoad(); }
});

hideLoad();
stats();
