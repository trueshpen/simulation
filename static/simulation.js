const socket = io();
const canvas = document.getElementById('simulationCanvas');
const ctx = canvas.getContext('2d');

const MAP_SIZE = 1000;
const DEFAULT_VISION_HALF_CONE = Math.PI / 4;
const CREATURE_SIZE = 5;

// Mirror server's river curve.
const RIVER_AMPLITUDE = 90;
const RIVER_FREQUENCY = 0.008;
const RIVER_BASE_Y = 500;
const RIVER_HALF_WIDTH = 45;

function riverCenterY(x) {
    return RIVER_BASE_Y + RIVER_AMPLITUDE * Math.sin(x * RIVER_FREQUENCY);
}

let state = { creatures: [], food: [], simulationTime: 0 };

function resizeCanvas() {
    // main-layout: [lists-area 460 (2×225 + 10 gap)] [canvas] [right 280] + gaps
    const reservedW = 460 + 280 + 2 * 14 + 2 * 16 + 20;
    const reservedH = 44 + 2 * 16 + 20;
    const maxSize = Math.min(
        window.innerWidth - reservedW,
        window.innerHeight - reservedH,
        MAP_SIZE
    );
    const size = Math.max(400, maxSize);
    canvas.width = size;
    canvas.height = size;
    const scale = size / MAP_SIZE;
    ctx.setTransform(scale, 0, 0, scale, 0, 0);
}

function drawRiver() {
    const step = 4;
    ctx.fillStyle = '#7ec8e8';  // light water blue
    ctx.beginPath();
    for (let x = 0; x <= MAP_SIZE; x += step) {
        const y = riverCenterY(x) - RIVER_HALF_WIDTH;
        if (x === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    for (let x = MAP_SIZE; x >= 0; x -= step) {
        const y = riverCenterY(x) + RIVER_HALF_WIDTH;
        ctx.lineTo(x, y);
    }
    ctx.closePath();
    ctx.fill();
}

function speciesColor(c) {
    if (c.species === 'croc') return c.isOld ? '#062b09' : '#1b5e20';
    if (c.species === 'carn') return c.isOld ? '#8b0000' : '#d32f2f';
    // herbivore: light brown / dark brown
    return c.isOld ? '#5d4037' : '#a1887f';
}

function draw() {
    ctx.save();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.fillStyle = '#f3eee0';  // dry grassland tan
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.restore();

    drawRiver();

    ctx.strokeStyle = '#333';
    ctx.lineWidth = 2;
    ctx.strokeRect(0, 0, MAP_SIZE, MAP_SIZE);

    // Food (grass) — green
    ctx.fillStyle = '#4caf50';
    for (const f of state.food) {
        ctx.beginPath();
        ctx.arc(f.x, f.y, 3, 0, Math.PI * 2);
        ctx.fill();
    }

    for (const c of state.creatures) {
        if (c.vision > 0) {
            const half = c.visionAngle || DEFAULT_VISION_HALF_CONE;
            ctx.fillStyle = 'rgba(120, 120, 120, 0.1)';
            ctx.beginPath();
            ctx.moveTo(c.x, c.y);
            const d = c.direction || 0;
            ctx.arc(c.x, c.y, c.vision, d - half, d + half);
            ctx.closePath();
            ctx.fill();
        }

        ctx.fillStyle = speciesColor(c);
        const radius = c.isAdult ? CREATURE_SIZE : CREATURE_SIZE * 0.7;
        ctx.beginPath();
        ctx.arc(c.x, c.y, radius, 0, Math.PI * 2);
        ctx.fill();
    }
}

function updateStats() {
    const creatures = state.creatures;
    const herb = creatures.filter(c => c.species === 'herb').length;
    const carn = creatures.filter(c => c.species === 'carn').length;
    const croc = creatures.filter(c => c.species === 'croc').length;
    const withVision = creatures.filter(c => c.vision > 0);
    const avgVision = withVision.length
        ? withVision.reduce((a, c) => a + c.vision, 0) / withVision.length
        : 0;
    const avgSpeed = creatures.length
        ? creatures.reduce((a, c) => a + c.speed, 0) / creatures.length
        : 0;

    document.getElementById('stat-time').textContent = Math.floor(state.simulationTime || 0) + 's';
    document.getElementById('stat-herbivores').textContent = herb;
    document.getElementById('stat-carnivores').textContent = carn;
    document.getElementById('stat-crocodiles').textContent = croc;
    document.getElementById('stat-food').textContent = state.food.length;
    document.getElementById('stat-vision').textContent = avgVision.toFixed(1);
    document.getElementById('stat-speed').textContent = avgSpeed.toFixed(2);
}

const MAX_LOG_ENTRIES = 150;
const logList = () => document.getElementById('log-list');
const herbListBody = () => document.getElementById('herb-list-body');
const carnListBody = () => document.getElementById('carn-list-body');
const crocListBody = () => document.getElementById('croc-list-body');

const LIST_RENDER_INTERVAL_MS = 500;
let lastListRender = 0;

function creatureRowHTML(c) {
    const color = speciesColor(c);
    const dotClass = c.isAdult ? 'adult' : 'child';
    let visCell;
    if (c.vision > 0) {
        const angleDeg = Math.round((c.visionAngle || DEFAULT_VISION_HALF_CONE) * 2 * 180 / Math.PI);
        visCell = `${c.vision.toFixed(1)} / ${angleDeg}°`;
    } else {
        visCell = '<span class="vis-dim">—</span>';
    }
    return '<tr>'
        + `<td class="col-id">${c.id ?? ''}</td>`
        + `<td class="col-species"><span class="creature-dot ${dotClass}" style="background:${color}"></span></td>`
        + `<td>${c.speed.toFixed(2)}</td>`
        + `<td>${visCell}</td>`
        + '</tr>';
}

function renderCreatureList() {
    const herb = herbListBody();
    const carn = carnListBody();
    const croc = crocListBody();
    if (!herb || !carn || !croc) return;
    const sorted = state.creatures.slice().sort((a, b) => (a.id || 0) - (b.id || 0));
    const buckets = { herb: [], carn: [], croc: [] };
    for (const c of sorted) {
        const html = creatureRowHTML(c);
        if (c.species === 'carn') buckets.carn.push(html);
        else if (c.species === 'croc') buckets.croc.push(html);
        else buckets.herb.push(html);
    }
    herb.innerHTML = buckets.herb.join('');
    carn.innerHTML = buckets.carn.join('');
    croc.innerHTML = buckets.croc.join('');
}

function appendLogs(events) {
    const list = logList();
    if (!list || !events.length) return;
    const scroller = list.closest('.log-scroll');

    // Newest at top: reverse batch order so the most recent event ends
    // up at index 0 after insertion at the front of the table.
    const frag = document.createDocumentFragment();
    for (let i = events.length - 1; i >= 0; i--) {
        const e = events[i];
        if (e.type !== 'stat') continue;
        const tr = document.createElement('tr');
        tr.className = 'log-stat';
        const ts = Math.floor(e.t).toString() + 's';
        if (e.empty) {
            tr.innerHTML = `<td>${ts}</td><td colspan="5"><em>žádní tvorové</em></td>`;
        } else {
            const visStats = e.visionCount > 0
                ? `${e.visionDist.toFixed(1)} / ${Math.round(e.visionAngleDeg)}°`
                : '—';
            tr.innerHTML = `<td>${ts}</td><td>${e.herb}</td><td>${e.carn}</td><td>${e.croc ?? 0}</td>`
                + `<td>${e.visionCount}</td><td>${visStats}</td>`;
        }
        frag.appendChild(tr);
    }

    const heightBefore = scroller ? scroller.scrollHeight : 0;
    const wasScrolled = scroller ? scroller.scrollTop > 4 : false;

    list.insertBefore(frag, list.firstChild);
    while (list.children.length > MAX_LOG_ENTRIES) {
        list.removeChild(list.lastChild);
    }

    // If user had scrolled away from the top to read history, keep their
    // viewport anchored to the same row instead of letting new entries
    // shove the visible content down.
    if (scroller && wasScrolled) {
        const delta = scroller.scrollHeight - heightBefore;
        if (delta > 0) scroller.scrollTop += delta;
    }
}

socket.on('simulation_state', function (data) {
    state = data;
    draw();
    updateStats();
    if (data.events && data.events.length) appendLogs(data.events);

    const now = performance.now();
    if (now - lastListRender > LIST_RENDER_INTERVAL_MS) {
        lastListRender = now;
        renderCreatureList();
    }
});

socket.on('connect', () => console.log('Connected'));
socket.on('disconnect', () => console.log('Disconnected'));
socket.on('connect_error', (err) => console.error('Connection error:', err));

window.addEventListener('load', () => {
    resizeCanvas();
    window.addEventListener('resize', () => { resizeCanvas(); draw(); });

    const restartBtn = document.getElementById('restartButton');
    restartBtn.addEventListener('click', () => {
        for (const el of [logList(), herbListBody(), carnListBody(), crocListBody()]) {
            if (el) el.innerHTML = '';
        }
        socket.emit('init_simulation');
    });
});
