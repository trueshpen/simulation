const socket = io();
const canvas = document.getElementById('simulationCanvas');
const ctx = canvas.getContext('2d');

const MAP_SIZE = 800;
const VISION_HALF_CONE = Math.PI / 4;
const CREATURE_SIZE = 5;

let state = { creatures: [], food: [], simulationTime: 0 };
let isRunning = false;

function resizeCanvas() {
    const padding = 40;
    const maxSize = Math.min(
        window.innerWidth - padding,
        window.innerHeight - 260,
        MAP_SIZE
    );
    const size = Math.max(300, maxSize);
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

    ctx.fillStyle = '#2e7d32';
    for (const f of state.food) {
        ctx.beginPath();
        ctx.arc(f.x, f.y, 2.5, 0, Math.PI * 2);
        ctx.fill();
    }

    for (const c of state.creatures) {
        if (c.vision > 0) {
            ctx.fillStyle = 'rgba(120, 120, 120, 0.1)';
            ctx.beginPath();
            ctx.moveTo(c.x, c.y);
            const d = c.direction || 0;
            ctx.arc(c.x, c.y, c.vision, d - VISION_HALF_CONE, d + VISION_HALF_CONE);
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

socket.on('simulation_state', function (data) {
    state = data;
    draw();
    updateStats();
});

socket.on('connect', () => console.log('Connected'));
socket.on('disconnect', () => console.log('Disconnected'));
socket.on('connect_error', (err) => console.error('Connection error:', err));

window.addEventListener('load', () => {
    resizeCanvas();
    window.addEventListener('resize', () => { resizeCanvas(); draw(); });

    const startBtn = document.getElementById('startButton');
    const restartBtn = document.getElementById('restartButton');

    startBtn.addEventListener('click', () => {
        if (!isRunning) {
            isRunning = true;
            startBtn.textContent = 'Zastavit';
            socket.emit('start_simulation');
        } else {
            isRunning = false;
            startBtn.textContent = 'Spustit';
            socket.emit('stop_simulation');
        }
    });

    restartBtn.addEventListener('click', () => {
        socket.emit('init_simulation');
    });
});
