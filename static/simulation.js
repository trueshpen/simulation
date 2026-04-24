// Připojení k WebSocket serveru
let socket = io();

// Globální proměnné
let canvas = document.getElementById('simulationCanvas');
let ctx = canvas.getContext('2d');
let creatures = [];
let foods = [];
let simulationTime = 0;
let isRunning = false;

// Konstanty
const MAP_SIZE = 800; // Zmenšená mapa
const STATS_WIDTH = 200;
const STATS_PADDING = 20;
const CREATURE_SIZE = 5; // Zmenšení tvorů na polovinu

// Funkce pro vykreslení
function draw() {
    // Vyčištění canvasu
    ctx.fillStyle = '#fff';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    
    // Vykreslení hranic mapy
    ctx.strokeStyle = '#000';
    ctx.lineWidth = 2;
    ctx.strokeRect(0, 0, MAP_SIZE, MAP_SIZE);
    
    // Vykreslení jídla
    ctx.fillStyle = '#808080';
    for (const food of foods) {
        ctx.beginPath();
        ctx.arc(food.x, food.y, 2, 0, Math.PI * 2); // Zmenšení jídla
        ctx.fill();
    }
    
    // Vykreslení tvorů
    for (const creature of creatures) {
        // Barva podle typu
        if (creature.isCarnivore) {
            ctx.fillStyle = creature.isOld ? '#800000' : '#ff0000';
        } else {
            ctx.fillStyle = creature.isOld ? '#006400' : '#00ff00';
        }
        
        // Vykreslení těla
        ctx.beginPath();
        ctx.arc(creature.x, creature.y, CREATURE_SIZE, 0, Math.PI * 2);
        ctx.fill();
        
        // Vykreslení zraku ve směru pohybu
        if (creature.vision > 0) {
            ctx.strokeStyle = 'rgba(255, 255, 255, 0.2)';
            ctx.beginPath();
            ctx.arc(creature.x, creature.y, creature.vision, 0, Math.PI * 2);
            ctx.stroke();
        }
    }
    
    // Vykreslení statistik
    drawStats();
}

// Funkce pro vykreslení statistik
function drawStats() {
    const statsX = MAP_SIZE + STATS_PADDING;
    const statsY = STATS_PADDING;
    const lineHeight = 25;
    
    // Pozadí statistik
    ctx.fillStyle = '#f0f0f0';
    ctx.fillRect(MAP_SIZE, 0, STATS_WIDTH, canvas.height);
    
    // Nadpis
    ctx.fillStyle = '#000';
    ctx.font = 'bold 16px Arial';
    ctx.fillText('Statistiky', statsX, statsY);
    
    // Počítání statistik
    let herbivores = 0;
    let carnivores = 0;
    let totalVision = 0;
    let creaturesWithVision = 0;
    let totalSpeed = 0;
    let totalDirectionChange = 0;
    
    for (const creature of creatures) {
        if (creature.isCarnivore) {
            carnivores++;
        } else {
            herbivores++;
        }
        if (creature.vision > 0) {
            totalVision += creature.vision;
            creaturesWithVision++;
        }
        totalSpeed += creature.speed;
        totalDirectionChange += creature.directionChange;
    }
    
    // Vykreslení statistik
    ctx.font = '14px Arial';
    let y = statsY + lineHeight * 2;
    
    ctx.fillText(`Čas: ${Math.floor(simulationTime)}s`, statsX, y);
    y += lineHeight;
    
    ctx.fillText(`Býložravci: ${herbivores}`, statsX, y);
    y += lineHeight;
    
    ctx.fillText(`Masožravci: ${carnivores}`, statsX, y);
    y += lineHeight;
    
    ctx.fillText(`Jídlo: ${foods.length}`, statsX, y);
    y += lineHeight;
    
    const avgVision = creaturesWithVision > 0 ? totalVision / creaturesWithVision : 0;
    ctx.fillText(`Průměrný zrak: ${avgVision.toFixed(1)}`, statsX, y);
    y += lineHeight;
    
    const avgSpeed = creatures.length > 0 ? totalSpeed / creatures.length : 0;
    ctx.fillText(`Průměrná rychlost: ${avgSpeed.toFixed(1)}`, statsX, y);
    y += lineHeight;
    
    const avgDirectionChange = creatures.length > 0 ? totalDirectionChange / creatures.length : 0;
    ctx.fillText(`Průměrná změna směru: ${(avgDirectionChange * 100).toFixed(1)}%`, statsX, y);
}

// Poslouchání aktualizací ze serveru
socket.on('simulation_state', function(data) {
    console.log('Received simulation state:', data);
    
    try {
        // Aktualizace stavu simulace
        creatures = data.creatures;
        foods = data.food;
        simulationTime = data.simulationTime;
        
        // Překreslení
        draw();
        
        // Update statistics
        document.getElementById('creatureCount').textContent = data.creatures.length;
        document.getElementById('foodCount').textContent = data.food.length;
        document.getElementById('carnivoreCount').textContent = data.creatures.filter(c => c.isCarnivore).length;
        document.getElementById('herbivoreCount').textContent = data.creatures.filter(c => !c.isCarnivore).length;
        
        console.log('Canvas updated successfully');
    } catch (error) {
        console.error('Error updating canvas:', error);
    }
});

// Inicializace
window.onload = function() {
    // Nastavení velikosti canvasu
    updateCanvasSize();
    
    // Přidání event listeneru pro změnu velikosti okna
    window.addEventListener('resize', updateCanvasSize);
    
    // Přidání event listeneru pro tlačítko restart
    document.getElementById('restartButton').addEventListener('click', function() {
        socket.emit('init_simulation', {});
    });
    
    // Přidání event listeneru pro tlačítko start/stop
    document.getElementById('startButton').addEventListener('click', function() {
        console.log('Start button clicked, current state:', isRunning);
        if (!isRunning) {
            isRunning = true;
            this.textContent = 'Stop';
            console.log('Emitting start_simulation event');
            socket.emit('start_simulation');
        } else {
            isRunning = false;
            this.textContent = 'Start';
            console.log('Emitting stop_simulation event');
            socket.emit('stop_simulation');
        }
    });
    
    // Inicializace simulace na serveru
    socket.emit('init_simulation', {});
};

// Funkce pro aktualizaci velikosti canvasu
function updateCanvasSize() {
    const maxWidth = window.innerWidth - 20; // 20px padding
    const maxHeight = window.innerHeight - 20;
    
    // Výpočet škálovacího faktoru
    const scaleX = (maxWidth - STATS_WIDTH) / MAP_SIZE;
    const scaleY = maxHeight / MAP_SIZE;
    const scale = Math.min(scaleX, scaleY, 1); // Maximálně 100% velikost
    
    // Nastavení velikosti canvasu
    canvas.width = MAP_SIZE * scale + STATS_WIDTH;
    canvas.height = MAP_SIZE * scale;
    
    // Nastavení transformace pro škálování
    ctx.setTransform(scale, 0, 0, scale, 0, 0);
}

// Socket event handlers
socket.on('connect', function() {
    console.log('Connected to server');
});

socket.on('disconnect', function() {
    console.log('Disconnected from server');
});

socket.on('connect_error', function(error) {
    console.error('Connection error:', error);
});

socket.on('simulation_state', function(data) {
    console.log('Received simulation state:', data);
    
    try {
        // Clear canvas
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        
        // Draw food
        ctx.fillStyle = 'green';
        data.food.forEach(food => {
            ctx.beginPath();
            ctx.arc(food.x, food.y, 3, 0, Math.PI * 2);
            ctx.fill();
        });
        
        // Draw creatures
        data.creatures.forEach(creature => {
            // Set color based on type and age
            if (creature.isCarnivore) {
                ctx.fillStyle = creature.isOld ? '#800000' : '#ff0000';
            } else {
                ctx.fillStyle = creature.isOld ? '#006400' : '#00ff00';
            }
            
            // Draw creature
            ctx.beginPath();
            ctx.arc(creature.x, creature.y, 5, 0, Math.PI * 2);
            ctx.fill();
            
            // Draw vision range if creature has vision
            if (creature.vision > 0) {
                ctx.strokeStyle = 'rgba(255, 255, 255, 0.2)';
                ctx.beginPath();
                ctx.arc(creature.x, creature.y, creature.vision, 0, Math.PI * 2);
                ctx.stroke();
            }
        });
        
        // Update statistics
        document.getElementById('creatureCount').textContent = data.creatures.length;
        document.getElementById('foodCount').textContent = data.food.length;
        document.getElementById('carnivoreCount').textContent = data.creatures.filter(c => c.isCarnivore).length;
        document.getElementById('herbivoreCount').textContent = data.creatures.filter(c => !c.isCarnivore).length;
        
        console.log('Canvas updated successfully');
    } catch (error) {
        console.error('Error updating canvas:', error);
    }
}); 