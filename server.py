import os
import random
import math
import time
import logging
from flask import Flask, render_template
from flask_socketio import SocketIO

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

# World
MAP_SIZE = 1000
MAX_FOOD = 550
FOOD_SPAWN_RATE = 0.70
INITIAL_FOOD = 160
INITIAL_CREATURES = 10
INITIAL_SPEED_RANGE = (2.2, 4.4)

# Creature lifecycle (seconds)
MATURATION_TIME = 5
OLD_AGE_DURATION = 30
STARVATION_TIME = 20
REPRODUCTION_COOLDOWN = 2

# Energy costs / rewards
REPRODUCTION_COST = 20
MOVEMENT_COST = 0.1
VISION_COST = 0.03  # per tick per (range * angle / default_angle)
SPEED_COST = 0.1
DIRECTION_CHANGE_COST = 0.05
FOOD_ENERGY = 30
PREY_ENERGY = 60
EAT_COOLDOWN = 3.0  # seconds after a meal before a creature eats again ("sytost")
HUNGRY_ENERGY = 50  # below this, creature only chases food (ignores potential mates)

# Carnivores are larger, stronger predators — they get a runtime multiplier
# on top of their genetic speed/vision. Herbivores use 1.0.
CARNIVORE_SPEED_BONUS = 1.20
CARNIVORE_VISION_BONUS = 1.10

# Interaction radii
EAT_RADIUS = 20
ATTACK_RADIUS = 15
REPRODUCTION_RADIUS = 50

# Initial spawn: cluster creatures near map center so they can find
# each other to reproduce before dying. info.txt doesn't pin this down.
INITIAL_CLUSTER_RADIUS = 190

# Mutation rules
CARNIVORE_CHANCE = 0.15
VISION_CHANCE_NONE = 0.1
VISION_RANGE_NONE = (5, 10)
DIRECTION_CHANGE_MUTATION = 0.05

# Per-trait mutation: child = avg_of_parents * uniform(MUTATION_LOW, MUTATION_HIGH)
MUTATION_LOW = 0.9
MUTATION_HIGH = 1.2

# Vision geometry
DEFAULT_VISION_ANGLE = math.pi / 4         # 45 deg half-cone = 90 deg total
MAX_VISION_ANGLE = 5 * math.pi / 12        # 75 deg half-cone = 150 deg total
MIN_VISION_ANGLE = math.pi / 12            # 15 deg half-cone = 30 deg total
MAX_VISION_RANGE = 50                      # ~5x the old stable equilibrium

# Litter size distribution: 1 child 50%, 2 30%, 3 15%, 4 5%
LITTER_CUMULATIVE = [(0.50, 1), (0.80, 2), (0.95, 3), (1.00, 4)]


def sample_litter_size():
    r = random.random()
    for threshold, count in LITTER_CUMULATIVE:
        if r < threshold:
            return count
    return 1

# Globals
creatures = []
foods = []
simulation_running = False
simulation_start_time = None
_next_creature_id = 0

# Event log (flushed into every broadcast) — only periodic stat snapshot
_events = []
_last_stats_time = 0.0
STATS_INTERVAL = 15.0


def sim_elapsed():
    return (time.time() - simulation_start_time) if simulation_start_time else 0.0


def log_event(kind, text):
    _events.append({'type': kind, 'text': text, 't': sim_elapsed()})


def maybe_log_stats():
    global _last_stats_time
    now = time.time()
    if now - _last_stats_time < STATS_INTERVAL:
        return
    _last_stats_time = now
    if not creatures:
        log_event('stat', 'Žádní tvorové')
        return
    herb = sum(1 for c in creatures if not c.is_carnivore)
    carn = len(creatures) - herb
    with_vision = [c for c in creatures if c.vision_range > 0]
    avg_vision = sum(c.effective_vision_range() for c in with_vision) / len(with_vision) if with_vision else 0
    avg_angle = sum(c.vision_angle for c in with_vision) / len(with_vision) if with_vision else 0
    avg_speed = sum(c.effective_speed() for c in creatures) / len(creatures)
    log_event(
        'stat',
        f'Stav: býl={herb}, mas={carn}, jídlo={len(foods)}, '
        f'zrak {len(with_vision)}/{len(creatures)} '
        f'(dálka={avg_vision:.1f}, úhel={math.degrees(2 * avg_angle):.0f}°), '
        f'rychl={avg_speed:.2f}',
    )


def normalize_angle(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


def spawn_food():
    if len(foods) < MAX_FOOD and random.random() < FOOD_SPAWN_RATE:
        foods.append({
            'x': random.uniform(0, MAP_SIZE),
            'y': random.uniform(0, MAP_SIZE),
        })


class Creature:
    def __init__(self, x, y, speed=3, direction_change=0.2,
                 vision_range=0, vision_angle=0, is_carnivore=False):
        global _next_creature_id
        _next_creature_id += 1
        self.id = _next_creature_id
        self.x = x
        self.y = y
        self.speed = speed
        self.direction_change = direction_change
        self.vision_range = vision_range
        self.vision_angle = vision_angle
        self.is_carnivore = is_carnivore
        self.energy = 100
        self.direction = random.uniform(-math.pi, math.pi)
        self.last_reproduction = 0
        self.reproduction_count = 0
        self.last_food = time.time()
        self.is_adult = False
        self.is_old = False
        self.adult_time = None
        self.old_time = None
        self.creation_time = time.time()
        self.alive = True

    def age_multiplier(self):
        if self.is_old:
            return 0.5
        if self.is_adult:
            return 1.5
        return 1.0

    def effective_speed(self):
        return self.speed * (CARNIVORE_SPEED_BONUS if self.is_carnivore else 1.0)

    def effective_vision_range(self):
        return self.vision_range * (CARNIVORE_VISION_BONUS if self.is_carnivore else 1.0)

    def move(self, target_xy=None):
        if target_xy is not None:
            tx, ty = target_xy
            self.direction = math.atan2(ty - self.y, tx - self.x)
        elif random.random() < self.direction_change:
            self.direction += random.uniform(-math.pi / 4, math.pi / 4)

        self.direction = normalize_angle(self.direction)

        mult = self.age_multiplier()
        eff_speed = self.effective_speed()
        self.x += math.cos(self.direction) * eff_speed * mult
        self.y += math.sin(self.direction) * eff_speed * mult

        if self.x < 0:
            self.x = 0
            self.direction = normalize_angle(math.pi - self.direction)
        elif self.x > MAP_SIZE:
            self.x = MAP_SIZE
            self.direction = normalize_angle(math.pi - self.direction)
        if self.y < 0:
            self.y = 0
            self.direction = normalize_angle(-self.direction)
        elif self.y > MAP_SIZE:
            self.y = MAP_SIZE
            self.direction = normalize_angle(-self.direction)

        self.energy -= MOVEMENT_COST * eff_speed * mult
        if self.vision_range > 0:
            # Cost scales with both effective range and angle (normalized so
            # that a default 90° cone at range R costs VISION_COST * R).
            self.energy -= VISION_COST * self.effective_vision_range() * (self.vision_angle / DEFAULT_VISION_ANGLE)
        self.energy -= SPEED_COST * eff_speed
        self.energy -= DIRECTION_CHANGE_COST * self.direction_change
        if self.energy < 0:
            self.energy = 0

    def can_see(self, target_x, target_y):
        if self.vision_range <= 0:
            return False
        dx = target_x - self.x
        dy = target_y - self.y
        distance = math.hypot(dx, dy)
        if distance > self.effective_vision_range():
            return False
        diff = abs(normalize_angle(math.atan2(dy, dx) - self.direction))
        return diff <= self.vision_angle

    def is_prey_for(self, predator):
        """Whether `self` can be eaten by `predator` (a carnivore)."""
        if self is predator or not self.alive:
            return False
        if not self.is_carnivore:
            return self.is_adult and not self.is_old
        return self.is_old

    def is_hungry(self):
        return self.energy < HUNGRY_ENERGY

    def can_mate(self):
        """Whether this creature is eligible to seek or accept a mate."""
        if not self.is_adult:
            return False
        # Old herbivores can't reproduce; old carnivores can.
        if self.is_old and not self.is_carnivore:
            return False
        return True

    def find_target(self, food_list, creature_list):
        """Pick who/what to walk toward this tick.
        Hungry creatures only look for food. Satisfied creatures prefer a
        visible mate and fall back to food if no mate is in sight.
        """
        if self.vision_range <= 0:
            return None

        food_target = self._scan_food(food_list, creature_list)
        if self.is_hungry():
            return food_target

        mate_target = self._scan_mate(creature_list)
        return mate_target if mate_target is not None else food_target

    def _scan_food(self, food_list, creature_list):
        best = None
        best_dist = float('inf')
        if self.is_carnivore:
            for other in creature_list:
                if not other.is_prey_for(self):
                    continue
                if not self.can_see(other.x, other.y):
                    continue
                d = math.hypot(other.x - self.x, other.y - self.y)
                if d < best_dist:
                    best_dist = d
                    best = (other.x, other.y)
        else:
            for food in food_list:
                if not self.can_see(food['x'], food['y']):
                    continue
                d = math.hypot(food['x'] - self.x, food['y'] - self.y)
                if d < best_dist:
                    best_dist = d
                    best = (food['x'], food['y'])
        return best

    def _scan_mate(self, creature_list):
        if not self.can_mate():
            return None
        best = None
        best_dist = float('inf')
        for other in creature_list:
            if other is self or not other.alive:
                continue
            if other.is_carnivore != self.is_carnivore:
                continue
            if not other.can_mate():
                continue
            if not self.can_see(other.x, other.y):
                continue
            d = math.hypot(other.x - self.x, other.y - self.y)
            if d < best_dist:
                best_dist = d
                best = (other.x, other.y)
        return best

    def try_eat(self, food_list, creature_list):
        """Consume food/prey within touch range. Mutates food_list and marks prey dead."""
        now = time.time()
        # Both species have "sytost": 5 seconds after a meal they don't eat.
        if now - self.last_food < EAT_COOLDOWN:
            return False
        if self.is_carnivore:
            for other in creature_list:
                if not other.is_prey_for(self):
                    continue
                if math.hypot(other.x - self.x, other.y - self.y) > ATTACK_RADIUS:
                    continue
                other.alive = False
                self.energy += PREY_ENERGY
                self.last_food = now
                return True
        else:
            for i, food in enumerate(food_list):
                if math.hypot(food['x'] - self.x, food['y'] - self.y) <= EAT_RADIUS:
                    food_list.pop(i)
                    self.energy += FOOD_ENERGY
                    self.last_food = now
                    return True
        return False

    def repro_status(self, other, now):
        """None = can reproduce; otherwise a Czech reason string."""
        if not self.is_adult or not other.is_adult:
            return 'dítě'
        if self.is_carnivore != other.is_carnivore:
            return 'jiný druh'
        # Old herbivores can't reproduce; old carnivores can.
        if (self.is_old and not self.is_carnivore) or \
           (other.is_old and not other.is_carnivore):
            return 'stáří'
        if now - self.last_reproduction < REPRODUCTION_COOLDOWN or \
           now - other.last_reproduction < REPRODUCTION_COOLDOWN:
            return 'cooldown'
        if self.energy < REPRODUCTION_COST or other.energy < REPRODUCTION_COST:
            return 'málo energie'
        if math.hypot(self.x - other.x, self.y - other.y) > REPRODUCTION_RADIUS:
            return 'daleko'
        return None

    def can_reproduce_with(self, other, now):
        return self.repro_status(other, now) is None

    def reproduce(self, other):
        now = time.time()
        if not self.can_reproduce_with(other, now):
            return []

        self.energy -= REPRODUCTION_COST
        other.energy -= REPRODUCTION_COST
        self.last_reproduction = now
        other.last_reproduction = now
        self.reproduction_count += 1
        other.reproduction_count += 1

        n = sample_litter_size()
        return [self._make_child(other) for _ in range(n)]

    def _make_child(self, other):
        # All continuous traits mutate via parent-average * uniform(0.9, 1.2):
        # -10% to +20%, average +5%/gen drift, so traits grow but can also
        # occasionally dip. User-specified.
        avg_speed = (self.speed + other.speed) / 2
        child_speed = avg_speed * random.uniform(MUTATION_LOW, MUTATION_HIGH)

        avg_dc = (self.direction_change + other.direction_change) / 2
        child_dc = avg_dc * random.uniform(1 - DIRECTION_CHANGE_MUTATION, 1 + DIRECTION_CHANGE_MUTATION)
        child_dc = max(0.1, min(0.3, child_dc))

        vision_parents = [p for p in (self, other) if p.vision_range > 0]
        if not vision_parents:
            if random.random() < VISION_CHANCE_NONE:
                child_vision = random.uniform(*VISION_RANGE_NONE)
                child_vangle = DEFAULT_VISION_ANGLE
            else:
                child_vision = 0
                child_vangle = 0
        elif len(vision_parents) == 1:
            parent = vision_parents[0]
            child_vision = parent.vision_range * random.uniform(MUTATION_LOW, MUTATION_HIGH)
            child_vangle = parent.vision_angle * random.uniform(MUTATION_LOW, MUTATION_HIGH)
        else:
            avg_vision = (self.vision_range + other.vision_range) / 2
            avg_vangle = (self.vision_angle + other.vision_angle) / 2
            child_vision = avg_vision * random.uniform(MUTATION_LOW, MUTATION_HIGH)
            child_vangle = avg_vangle * random.uniform(MUTATION_LOW, MUTATION_HIGH)

        if child_vision > 0:
            child_vision = min(child_vision, MAX_VISION_RANGE)
            child_vangle = max(MIN_VISION_ANGLE, min(MAX_VISION_ANGLE, child_vangle))

        if self.is_carnivore and other.is_carnivore:
            child_carn = True
        else:
            child_carn = random.random() < CARNIVORE_CHANCE

        return Creature(
            x=(self.x + other.x) / 2 + random.uniform(-5, 5),
            y=(self.y + other.y) / 2 + random.uniform(-5, 5),
            speed=child_speed,
            direction_change=child_dc,
            vision_range=child_vision,
            vision_angle=child_vangle,
            is_carnivore=child_carn,
        )


def initialize_simulation():
    global creatures, foods, simulation_start_time, _events, _last_stats_time
    global _next_creature_id
    _events = []
    _last_stats_time = 0.0
    _next_creature_id = 0
    cx, cy = MAP_SIZE / 2, MAP_SIZE / 2
    creatures = [
        Creature(
            x=cx + random.uniform(-INITIAL_CLUSTER_RADIUS, INITIAL_CLUSTER_RADIUS),
            y=cy + random.uniform(-INITIAL_CLUSTER_RADIUS, INITIAL_CLUSTER_RADIUS),
            speed=random.uniform(*INITIAL_SPEED_RANGE),
            direction_change=random.uniform(0.1, 0.3),
            vision_range=0,
            is_carnivore=False,
        )
        for _ in range(INITIAL_CREATURES)
    ]
    foods = [
        {'x': random.uniform(0, MAP_SIZE), 'y': random.uniform(0, MAP_SIZE)}
        for _ in range(INITIAL_FOOD)
    ]
    simulation_start_time = None


def update_simulation():
    global creatures, foods
    logger.info('Simulation thread started')

    while True:
        if not simulation_running:
            time.sleep(0.1)
            continue

        now = time.time()

        for c in creatures:
            if not c.alive:
                continue

            age = now - c.creation_time
            if not c.is_adult and age >= MATURATION_TIME:
                c.is_adult = True
                c.adult_time = now
            if c.is_adult and not c.is_old and c.reproduction_count >= 2:
                c.is_old = True
                c.old_time = now

            if c.is_old and (now - c.old_time) >= OLD_AGE_DURATION:
                c.alive = False
                continue
            if (now - c.last_food) > STARVATION_TIME:
                c.alive = False
                continue

            target = c.find_target(foods, creatures)
            c.move(target)
            c.try_eat(foods, creatures)

        # Reproduction pass: each creature reproduces at most once per tick.
        # Also logs "near-miss": adults close enough but unable to reproduce
        # for reasons other than cooldown (cooldown is just normal pacing).
        new_children = []
        paired = set()
        for c in creatures:
            if id(c) in paired or not c.alive or not c.is_adult or c.is_old:
                continue
            for other in creatures:
                if other is c or id(other) in paired or not other.alive:
                    continue
                if math.hypot(c.x - other.x, c.y - other.y) > REPRODUCTION_RADIUS:
                    continue
                if c.can_reproduce_with(other, now):
                    children = c.reproduce(other)
                    if children:
                        new_children.extend(children)
                        paired.add(id(c))
                        paired.add(id(other))
                        break
        creatures.extend(new_children)
        creatures[:] = [c for c in creatures if c.alive]

        spawn_food()
        maybe_log_stats()

        try:
            socketio.emit('simulation_state', current_state())
        except Exception as e:
            logger.error(f'Error sending state: {e}')

        time.sleep(0.1)


@app.context_processor
def inject_asset_version():
    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')

    def asset_version(filename):
        try:
            return int(os.path.getmtime(os.path.join(static_dir, filename)))
        except OSError:
            return 0
    return {'asset_version': asset_version}


@app.route('/')
def index():
    return render_template('index.html')


def current_state():
    global _events
    now = time.time()
    state = {
        'creatures': [{
            'id': c.id,
            'x': c.x, 'y': c.y, 'direction': c.direction,
            'isCarnivore': c.is_carnivore,
            'vision': c.effective_vision_range(), 'visionAngle': c.vision_angle,
            'speed': c.effective_speed(), 'directionChange': c.direction_change,
            'age': now - c.creation_time, 'isAdult': c.is_adult, 'isOld': c.is_old,
            'energy': c.energy,
        } for c in creatures],
        'food': foods,
        'simulationTime': (now - simulation_start_time) if simulation_start_time else 0,
        'events': _events,
    }
    _events = []
    return state


@socketio.on('connect')
def handle_connect():
    global simulation_running, simulation_start_time
    logger.info('Client connected')
    initialize_simulation()
    simulation_running = True
    simulation_start_time = time.time()
    socketio.emit('simulation_state', current_state())


@socketio.on('init_simulation')
def handle_init():
    global simulation_start_time
    logger.info('Re-initializing simulation')
    initialize_simulation()
    if simulation_running:
        simulation_start_time = time.time()
    socketio.emit('simulation_state', current_state())


@socketio.on('start_simulation')
def handle_start():
    global simulation_running, simulation_start_time
    logger.info('Starting simulation')
    if simulation_start_time is None:
        simulation_start_time = time.time()
    simulation_running = True


@socketio.on('stop_simulation')
def handle_stop():
    global simulation_running
    logger.info('Stopping simulation')
    simulation_running = False


if __name__ == '__main__':
    import threading
    initialize_simulation()
    threading.Thread(target=update_simulation, daemon=True).start()
    socketio.run(app, debug=False, host='0.0.0.0', allow_unsafe_werkzeug=True)
