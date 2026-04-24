const socket = io();
const canvas = document.getElementById('simulationCanvas');
const ctx = canvas.getContext('2d');

const MAP_SIZE = 1000;
const DEFAULT_VISION_HALF_CONE = Math.PI / 4;
const CREATURE_SIZE = 5;

let state = { creatures: [], food: [], simulationTime: 0 };

function resizeCanvas() {
    // main-layout: [herb 225] [carn 225] [canvas ???] [right 280] + gaps
    const reservedW = 2 * 225 + 280 + 3 * 14 + 2 * 16 + 20;
    // Vertical: top bar (~44) + body padding + safety
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

function draw() {
    ctx.save();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.fillStyle = '#fafafa';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.restore();

    ctx.strokeStyle = '#333';
    ctx.lineWidth = 2;
    ctx.strokeRect(0, 0, MAP_SIZE, MAP_SIZE);

    ctx.fillStyle = '#29b6f6';
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

        if (c.isCarnivore) {
            ctx.fillStyle = c.isOld ? '#8b0000' : '#d32f2f';
        } else {
            ctx.fillStyle = c.isOld ? '#1b5e20' : '#43a047';
        }
        const radius = c.isAdult ? CREATURE_SIZE : CREATURE_SIZE * 0.7;
        ctx.beginPath();
        ctx.arc(c.x, c.y, radius, 0, Math.PI * 2);
        ctx.fill();
    }
}

function updateStats() {
    const creatures = state.creatures;
    const herbivores = creatures.filter(c => !c.isCarnivore).length;
    const carnivores = creatures.length - herbivores;
    const withVision = creatures.filter(c => c.vision > 0);
    const avgVision = withVision.length
        ? withVision.reduce((a, c) => a + c.vision, 0) / withVision.length
        : 0;
    const avgSpeed = creatures.length
        ? creatures.reduce((a, c) => a + c.speed, 0) / creatures.length
        : 0;

    document.getElementById('stat-time').textContent = Math.floor(state.simulationTime || 0) + 's';
    document.getElementById('stat-herbivores').textContent = herbivores;
    document.getElementById('stat-carnivores').textContent = carnivores;
    document.getElementById('stat-food').textContent = state.food.length;
    document.getElementById('stat-vision').textContent = avgVision.toFixed(1);
    document.getElementById('stat-speed').textContent = avgSpeed.toFixed(2);
}

const MAX_LOG_ENTRIES = 150;
const logList = () => document.getElementById('log-list');
const herbListBody = () => document.getElementById('herb-list-body');
const carnListBody = () => document.getElementById('carn-list-body');

const LIST_RENDER_INTERVAL_MS = 500;
let lastListRender = 0;

function creatureColor(c) {
    if (c.isCarnivore) return c.isOld ? '#8b0000' : '#d32f2f';
    return c.isOld ? '#1b5e20' : '#43a047';
}

function creatureRowHTML(c) {
    const color = creatureColor(c);
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
    if (!herb || !carn) return;
    const sorted = state.creatures.slice().sort((a, b) => (a.id || 0) - (b.id || 0));
    const herbRows = [];
    const carnRows = [];
    for (const c of sorted) {
        (c.isCarnivore ? carnRows : herbRows).push(creatureRowHTML(c));
    }
    herb.innerHTML = herbRows.join('');
    carn.innerHTML = carnRows.join('');
}

function appendLogs(events) {
    const list = logList();
    if (!list || !events.length) return;
    const nearBottom = list.scrollTop + list.clientHeight >= list.scrollHeight - 4;
    const frag = document.createDocumentFragment();
    for (const e of events) {
        const row = document.createElement('div');
        row.className = 'log-entry log-' + e.type;
        const ts = document.createElement('span');
        ts.className = 'log-time';
        ts.textContent = Math.floor(e.t).toString().padStart(3, '0') + 's';
        row.appendChild(ts);
        row.appendChild(document.createTextNode(e.text));
        frag.appendChild(row);
    }
    list.appendChild(frag);
    while (list.children.length > MAX_LOG_ENTRIES) {
        list.removeChild(list.firstChild);
    }
    if (nearBottom) list.scrollTop = list.scrollHeight;
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
        const list = logList();
        if (list) list.innerHTML = '';
        const h = herbListBody();
        if (h) h.innerHTML = '';
        const c = carnListBody();
        if (c) c.innerHTML = '';
        socket.emit('init_simulation');
    });
});
