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
MAP_SIZE = 800
MAX_FOOD = 200
FOOD_SPAWN_RATE = 0.2
INITIAL_FOOD = 100
INITIAL_CREATURES = 10

# Creature lifecycle (seconds)
MATURATION_TIME = 5
OLD_AGE_DURATION = 30
STARVATION_TIME = 20
REPRODUCTION_COOLDOWN = 2

# Energy costs / rewards
REPRODUCTION_COST = 20
MOVEMENT_COST = 0.1
VISION_COST = 0.2
SPEED_COST = 0.1
DIRECTION_CHANGE_COST = 0.05
FOOD_ENERGY = 20
PREY_ENERGY = 50

# Interaction radii
EAT_RADIUS = 10
ATTACK_RADIUS = 12
REPRODUCTION_RADIUS = 30

# Mutation rules
CARNIVORE_CHANCE = 0.15
VISION_CHANCE_NONE = 0.1
VISION_CHANCE_ONE = 0.5
VISION_CHANCE_BOTH = 1.0
VISION_RANGE_NONE = (5, 10)
VISION_PARENT_DELTA = (0.05, 0.2)
SPEED_MUTATION = 0.1
DIRECTION_CHANGE_MUTATION = 0.05

# Vision geometry: full cone = 90 degrees, so half-cone = 45 degrees
VISION_HALF_CONE = math.pi / 4

# Globals
creatures = []
foods = []
simulation_running = False
simulation_start_time = None


def normalize_angle(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


def spawn_food():
    if len(foods) < MAX_FOOD and random.random() < FOOD_SPAWN_RATE:
        foods.append({
            'x': random.uniform(0, MAP_SIZE),
            'y': random.uniform(0, MAP_SIZE),
        })


class Creature:
    def __init__(self, x, y, speed=3, direction_change=0.2, vision_range=0, is_carnivore=False):
        self.x = x
        self.y = y
        self.speed = speed
        self.direction_change = direction_change
        self.vision_range = vision_range
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

    def move(self, target_xy=None):
        if target_xy is not None:
            tx, ty = target_xy
            self.direction = math.atan2(ty - self.y, tx - self.x)
        elif random.random() < self.direction_change:
            self.direction += random.uniform(-math.pi / 4, math.pi / 4)

        self.direction = normalize_angle(self.direction)

        mult = self.age_multiplier()
        self.x += math.cos(self.direction) * self.speed * mult
        self.y += math.sin(self.direction) * self.speed * mult

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

        self.energy -= MOVEMENT_COST * self.speed * mult
        if self.vision_range > 0:
            self.energy -= VISION_COST * self.vision_range
        self.energy -= SPEED_COST * self.speed
        self.energy -= DIRECTION_CHANGE_COST * self.direction_change

    def can_see(self, target_x, target_y):
        if self.vision_range <= 0:
            return False
        dx = target_x - self.x
        dy = target_y - self.y
        distance = math.hypot(dx, dy)
        if distance > self.vision_range:
            return False
        diff = abs(normalize_angle(math.atan2(dy, dx) - self.direction))
        return diff <= VISION_HALF_CONE

    def is_prey_for(self, predator):
        """Whether `self` can be eaten by `predator` (a carnivore)."""
        if self is predator or not self.alive:
            return False
        if not self.is_carnivore:
            return self.is_adult and not self.is_old
        return self.is_old

    def find_target(self, food_list, creature_list):
        if self.vision_range <= 0:
            return None
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

    def try_eat(self, food_list, creature_list):
        """Consume food/prey within touch range. Mutates food_list and marks prey dead."""
        now = time.time()
        if self.is_carnivore:
            for other in creature_list:
                if not other.is_prey_for(self):
                    continue
                if math.hypot(other.x - self.x, other.y - self.y) <= ATTACK_RADIUS:
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

    def can_reproduce_with(self, other, now):
        if not self.is_adult or not other.is_adult:
            return False
        if self.is_old or other.is_old:
            return False
        if now - self.last_reproduction < REPRODUCTION_COOLDOWN:
            return False
        if now - other.last_reproduction < REPRODUCTION_COOLDOWN:
            return False
        if math.hypot(self.x - other.x, self.y - other.y) > REPRODUCTION_RADIUS:
            return False
        if self.energy < REPRODUCTION_COST or other.energy < REPRODUCTION_COST:
            return False
        return True

    def reproduce(self, other):
        now = time.time()
        if not self.can_reproduce_with(other, now):
            return None

        self.energy -= REPRODUCTION_COST
        other.energy -= REPRODUCTION_COST
        self.last_reproduction = now
        other.last_reproduction = now
        self.reproduction_count += 1
        other.reproduction_count += 1

        avg_speed = (self.speed + other.speed) / 2
        child_speed = max(0.5, avg_speed * random.uniform(1 - SPEED_MUTATION, 1 + SPEED_MUTATION))

        avg_dc = (self.direction_change + other.direction_change) / 2
        child_dc = avg_dc * random.uniform(1 - DIRECTION_CHANGE_MUTATION, 1 + DIRECTION_CHANGE_MUTATION)
        child_dc = max(0.1, min(0.3, child_dc))

        parents_with_vision = sum(1 for p in (self, other) if p.vision_range > 0)
        if parents_with_vision == 0:
            child_vision = random.uniform(*VISION_RANGE_NONE) if random.random() < VISION_CHANCE_NONE else 0
        elif parents_with_vision == 1:
            if random.random() < VISION_CHANCE_ONE:
                base = max(self.vision_range, other.vision_range)
                child_vision = base * (1 + random.uniform(*VISION_PARENT_DELTA))
            else:
                child_vision = 0
        else:
            base = (self.vision_range + other.vision_range) / 2
            child_vision = base * (1 + random.uniform(*VISION_PARENT_DELTA))

        if self.is_carnivore and other.is_carnivore:
            child_carn = True
        else:
            child_carn = random.random() < CARNIVORE_CHANCE

        return Creature(
            x=(self.x + other.x) / 2,
            y=(self.y + other.y) / 2,
            speed=child_speed,
            direction_change=child_dc,
            vision_range=child_vision,
            is_carnivore=child_carn,
        )


def initialize_simulation():
    global creatures, foods, simulation_start_time
    creatures = [
        Creature(
            x=random.uniform(0, MAP_SIZE),
            y=random.uniform(0, MAP_SIZE),
            speed=random.uniform(2, 4),
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
            if c.energy <= 0:
                c.alive = False
                continue

            target = c.find_target(foods, creatures)
            c.move(target)
            c.try_eat(foods, creatures)

        # Reproduction pass: each creature reproduces at most once per tick
        new_children = []
        paired = set()
        for c in creatures:
            if id(c) in paired or not c.alive or not c.is_adult or c.is_old:
                continue
            for other in creatures:
                if other is c or id(other) in paired or not other.alive:
                    continue
                if c.can_reproduce_with(other, now):
                    child = c.reproduce(other)
                    if child is not None:
                        new_children.append(child)
                        paired.add(id(c))
                        paired.add(id(other))
                        break
        creatures.extend(new_children)
        creatures[:] = [c for c in creatures if c.alive]

        spawn_food()

        try:
            state = {
                'creatures': [{
                    'x': c.x,
                    'y': c.y,
                    'direction': c.direction,
                    'isCarnivore': c.is_carnivore,
                    'vision': c.vision_range,
                    'speed': c.speed,
                    'directionChange': c.direction_change,
                    'age': now - c.creation_time,
                    'isAdult': c.is_adult,
                    'isOld': c.is_old,
                    'energy': c.energy,
                } for c in creatures],
                'food': foods,
                'simulationTime': (now - simulation_start_time) if simulation_start_time else 0,
            }
            socketio.emit('simulation_state', state)
        except Exception as e:
            logger.error(f'Error sending state: {e}')

        time.sleep(0.1)


@app.route('/')
def index():
    return render_template('index.html')


@socketio.on('connect')
def handle_connect():
    logger.info('Client connected')


@socketio.on('init_simulation')
def handle_init():
    logger.info('Re-initializing simulation')
    initialize_simulation()


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
