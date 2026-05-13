# Evolution Simulation

Real-time browser-based evolution simulation. Creatures (herbivores, carnivores, crocodiles) move around a 1000×1000 world, eat, reproduce, and evolve traits like speed, vision range, and vision angle across generations. A river cuts through the map with water mechanics that affect movement and vision.

---

## How it works

```
Browser (index.html + simulation.js)
    │  WebSocket
    └─── Flask-SocketIO (server.py)
             │
             └─── Simulation loop (ticks every ~50 ms)
                      ├─── Creature movement, energy, aging
                      ├─── Food spawning
                      ├─── Reproduction + mutation
                      └─── Predation (carnivores, crocodiles)
```

**Key files:**
| File | Purpose |
|------|--------|
| `server.py` | Flask-SocketIO server — full simulation logic and world state |
| `static/simulation.js` | Browser rendering — canvas drawing, WebSocket client |
| `templates/index.html` | UI — canvas, stats panel, creature list, event log |
| `static/styles.css` | Styles |
| `requirements.txt` | Python dependencies |

---

## Tech stack

- **Python 3 / Flask** — web server
- **Flask-SocketIO** — real-time WebSocket communication
- **HTML5 Canvas** — creature and world rendering in the browser

---

## Installation

```bash
pip install -r requirements.txt
python server.py
```

Open `http://localhost:5000` in your browser. The simulation starts automatically.

---

## World & Species

### Map
- **Size:** 1000 × 1000 pixels
- **River:** sine-wave band across the map — non-crocodile creatures move and see at half effectiveness in water
- **Food:** spawns randomly up to a maximum of 550 items; initial spawn of 160

### Species

| Species | Key traits |
|---------|----------|
| **Herbivore** (`herb`) | Eats food only; reproduces frequently; mutation source for new species |
| **Carnivore** (`carn`) | Eats adult herbivores and old carnivores; 1.1× speed and vision bonus; slower reproduction |
| **Crocodile** (`croc`) | River-tied apex predator; hunts up to 30 px from water on land; slowed on land |

### Lifecycle (all species)

| Stage | Age | Notes |
|-------|-----|-------|
| **Child** | 0–5 s | Normal speed; cannot reproduce; ignored by carnivores |
| **Adult** | 5 s+ | 1.5× speed; can reproduce; prey for carnivores |
| **Old** | after 2 reproductions | 0.5× speed; cannot reproduce; dies 30 s after reaching adulthood |

### Energy & Survival
- Starvation: death after 20 seconds without food
- Herbivores gain +30 energy per food item; carnivores/crocs gain +35 per kill
- Movement, vision, and direction changes all cost energy per tick

---

## Genetics & Mutation

Traits are inherited from parents with random mutation:

| Trait | Inheritance | Notes |
|-------|-------------|-------|
| **Speed** | Average of parents ±10% | Initial range 2.2–4.4 |
| **Vision range** | Probabilistic (see below) | Energy cost scales with range |
| **Vision angle** | Average of parents ±5% | 90° default |
| **Direction change** | Average of parents ±5% | 0.1–0.3 probability per tick |
| **Species** | Herbivore offspring: 8% carnivore, 4% croc | 100% if both parents are carnivores |

**Vision inheritance:** no-vision parents → 10% chance of gaining vision (5–10 px); one parent with vision → 50% chance (inherited range ±5–20%); both parents with vision → 100% (average ±5–20%).

---

## UI

- **Canvas** — live world render with creatures, food, and river
- **Stats panel** (right column) — live counts for herbivores, carnivores, crocs, food
- **Creature list** — scrollable table with persistent IDs, species, age, energy
- **Event log** — newest events at top; table columns: time, type, mass, vision count, vision range
- **Restart button** — resets the world to initial state

---

## Recent Activity

<!-- AUTO-GENERATED: START -->
*This part is automatically updated by the night AI task*

**Last update:** 2026-05-13 UTC — no new changes

**Last commits:**
- `26bec77` Keep crocodiles tied to the river (2026-04-25)
- `55680db` Predator nerf + rescue herd (2026-04-25)
- `159e172` Crocs swim to water; lighter croc adult colour; croc list back to 225px (2026-04-25)
- `5776b32` River, crocodiles, water mechanics, 4-way balance tune (2026-04-25)
- `26e91d5` MAX_SPEED 15 → 25 per user (2026-04-25)

**Project status:** Active — git remote: https://github.com/trueshpen/simulation
<!-- AUTO-GENERATED: END -->
