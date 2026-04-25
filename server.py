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
INITIAL_CREATURES = 20
INITIAL_SPEED_RANGE = (2.2, 4.4)

# Creature lifecycle (seconds)
MATURATION_TIME = 5
OLD_AGE_DURATION = 30
STARVATION_TIME = 20
REPRODUCTION_COOLDOWN_HERB = 2.0   # herbivores reproduce often
REPRODUCTION_COOLDOWN_PRED = 5.0   # carn / croc much slower

# Energy costs / rewards
REPRODUCTION_COST_HERB = 20
REPRODUCTION_COST_PRED = 35
MOVEMENT_COST = 0.1
VISION_COST = 0.03  # per tick per (range * angle / default_angle)
SPEED_COST = 0.1
DIRECTION_CHANGE_COST = 0.05
FOOD_ENERGY = 30
PREY_ENERGY = 35
EAT_COOLDOWN_HERB = 3.0   # herbivore "sytost"
EAT_COOLDOWN_PRED = 6.0   # carnivore + crocodile "sytost" — slower predation
HUNGRY_ENERGY = 50         # below this, creature only chases food
PRED_FULL_ENERGY = 70      # carn / croc at this energy skips a kill

# Species multipliers (runtime, on top of genetic speed/vision).
CARNIVORE_SPEED_BONUS = 1.10
CARNIVORE_VISION_BONUS = 1.10

# Interaction radii
EAT_RADIUS = 20
ATTACK_RADIUS = 18
REPRODUCTION_RADIUS = 50

# Initial spawn: cluster creatures near map center so they can find
# each other to reproduce before dying. info.txt doesn't pin this down.
INITIAL_CLUSTER_RADIUS = 190

# Species
SPECIES_HERB = 'herb'
SPECIES_CARN = 'carn'
SPECIES_CROC = 'croc'

# Mutation rules — per-child species roll for herbivore parents.
CARNIVORE_CHANCE = 0.08
CROCODILE_CHANCE = 0.04
VISION_CHANCE_NONE = 0.1
VISION_RANGE_NONE = (5, 10)
DIRECTION_CHANGE_MUTATION = 0.05

# River: a sine-wave band across the map. Same parameters used on the client.
RIVER_AMPLITUDE = 90
RIVER_FREQUENCY = 0.008
RIVER_BASE_Y = 500       # MAP_SIZE / 2
RIVER_HALF_WIDTH = 45    # 90 px wide river
CROC_HUNT_LAND_RANGE = 30  # crocs can hunt up to this far from water

# Water modifiers
WATER_SPEED_MOD = 0.5    # non-croc 2× slower in water
WATER_VISION_MOD = 0.5   # non-croc 2× shorter vision in water
CROC_LAND_SPEED_MOD = 1.0 / 1.5  # croc 1.5× slower on land

# Crocodile lifecycle is 2× slower than the others.
CROC_AGING_MULT = 2.0


def river_center_y(x):
    return RIVER_BASE_Y + RIVER_AMPLITUDE * math.sin(x * RIVER_FREQUENCY)


def is_water(x, y):
    return abs(y - river_center_y(x)) < RIVER_HALF_WIDTH


def is_in_or_near_water(x, y):
    return abs(y - river_center_y(x)) < RIVER_HALF_WIDTH + CROC_HUNT_LAND_RANGE

# Per-trait mutation: child = avg_of_parents * uniform(MUTATION_LOW, MUTATION_HIGH)
MUTATION_LOW = 0.9
MUTATION_HIGH = 1.2

# Vision geometry
DEFAULT_VISION_ANGLE = math.pi / 4         # 45 deg half-cone = 90 deg total
MAX_VISION_ANGLE = 5 * math.pi / 12        # 75 deg half-cone = 150 deg total
MIN_VISION_ANGLE = math.pi / 12            # 15 deg half-cone = 30 deg total
MAX_VISION_RANGE = 50                      # ~5x the old stable equilibrium
MAX_SPEED = 25                              # cap so long runs don't explode to 100+

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
    event = {'type': 'stat', 't': sim_elapsed()}
    if not creatures:
        event['empty'] = True
        _events.append(event)
        return
    herb = sum(1 for c in creatures if c.species == SPECIES_HERB)
    carn = sum(1 for c in creatures if c.species == SPECIES_CARN)
    croc = sum(1 for c in creatures if c.species == SPECIES_CROC)
    with_vision = [c for c in creatures if c.vision_range > 0]
    avg_dist = sum(c.effective_vision_range() for c in with_vision) / len(with_vision) if with_vision else 0
    avg_angle = sum(c.vision_angle for c in with_vision) / len(with_vision) if with_vision else 0
    event.update({
        'herb': herb,
        'carn': carn,
        'croc': croc,
        'visionCount': len(with_vision),
        'visionDist': avg_dist,
        'visionAngleDeg': math.degrees(2 * avg_angle),
    })
    _events.append(event)


def normalize_angle(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


def spawn_food():
    if len(foods) >= MAX_FOOD or random.random() >= FOOD_SPAWN_RATE:
        return
    # 70% bias toward riverbank (more grass near water), 30% anywhere on land.
    for _ in range(8):
        x = random.uniform(0, MAP_SIZE)
        if random.random() < 0.7:
            rcy = river_center_y(x)
            sign = random.choice([-1, 1])
            offset = random.uniform(RIVER_HALF_WIDTH + 5, RIVER_HALF_WIDTH + 90)
            y = rcy + offset * sign
        else:
            y = random.uniform(0, MAP_SIZE)
        if 0 <= y <= MAP_SIZE and not is_water(x, y):
            foods.append({'x': x, 'y': y})
            return


class Creature:
    def __init__(self, x, y, speed=3, direction_change=0.2,
                 vision_range=0, vision_angle=0, species=SPECIES_HERB):
        global _next_creature_id
        _next_creature_id += 1
        self.id = _next_creature_id
        self.x = x
        self.y = y
        self.speed = speed
        self.direction_change = direction_change
        self.vision_range = vision_range
        self.vision_angle = vision_angle
        self.species = species
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
        # River-crossing state. None when on land or in water but not crossing.
        self.river_crossing = None  # 'south' | 'north' | None

    @property
    def is_carnivore(self):
        return self.species == SPECIES_CARN

    @property
    def is_crocodile(self):
        return self.species == SPECIES_CROC

    @property
    def is_predator(self):
        return self.species in (SPECIES_CARN, SPECIES_CROC)

    def maturation_time(self):
        return MATURATION_TIME * (CROC_AGING_MULT if self.is_crocodile else 1.0)

    def old_age_duration(self):
        return OLD_AGE_DURATION * (CROC_AGING_MULT if self.is_crocodile else 1.0)

    def starvation_time(self):
        return STARVATION_TIME * (CROC_AGING_MULT if self.is_crocodile else 1.0)

    def age_multiplier(self):
        if self.is_old:
            return 0.5
        if self.is_adult:
            return 1.5
        return 1.0

    def position_speed_mod(self):
        in_w = is_water(self.x, self.y)
        if self.is_crocodile:
            return 1.0 if in_w else CROC_LAND_SPEED_MOD
        return WATER_SPEED_MOD if in_w else 1.0

    def position_vision_mod(self):
        if self.is_crocodile:
            return 1.0
        return WATER_VISION_MOD if is_water(self.x, self.y) else 1.0

    def species_speed_bonus(self):
        if self.is_carnivore:
            return CARNIVORE_SPEED_BONUS
        return 1.0  # crocs use only land/water modifier

    def species_vision_bonus(self):
        if self.is_carnivore:
            return CARNIVORE_VISION_BONUS
        return 1.0

    def effective_speed(self):
        return self.speed * self.species_speed_bonus() * self.position_speed_mod()

    def effective_vision_range(self):
        return self.vision_range * self.species_vision_bonus() * self.position_vision_mod()

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

    def maybe_river_crossing(self, target):
        """When a non-croc steps into water, force it to walk straight to the
        opposite bank instead of swimming around. Resets when it leaves the
        water again."""
        if self.is_crocodile:
            return target
        if not is_water(self.x, self.y):
            self.river_crossing = None
            return target
        # In water — pick a target shore if not already crossing.
        if self.river_crossing is None:
            rcy = river_center_y(self.x)
            self.river_crossing = 'south' if self.y < rcy else 'north'
        rcy = river_center_y(self.x)
        if self.river_crossing == 'south':
            return (self.x, rcy + RIVER_HALF_WIDTH + 20)
        else:
            return (self.x, rcy - RIVER_HALF_WIDTH - 20)

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
        """Whether `self` can be eaten by `predator`.

        Rules per predator species:
          - carnivore: any adult herbivore (incl. old); old carnivores; not crocs.
          - crocodile: any adult herbivore or carnivore; old crocodiles only.

        Children are never prey.
        """
        if self is predator or not self.alive or not self.is_adult:
            return False
        if predator.is_crocodile:
            if self.is_crocodile:
                return self.is_old
            return True  # crocs eat any adult herb/carn
        if predator.is_carnivore:
            if self.species == SPECIES_HERB:
                return True
            if self.species == SPECIES_CARN:
                return self.is_old
            return False  # carns don't hunt crocs
        return False  # herbs don't hunt

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
        Herbivores and carnivores flee from their predators when seen.
        Hungry creatures only look for food; satisfied creatures pick the
        closer of food/mate. Crocodiles, when otherwise idle, head to water.
        """
        # Predator flee for non-crocs (sighted only)
        if not self.is_crocodile and self.vision_range > 0:
            predator = self._scan_predator(creature_list)
            if predator is not None:
                return (self.x + (self.x - predator.x),
                        self.y + (self.y - predator.y))

        target = None
        if self.vision_range > 0:
            food_target, food_dist = self._scan_food(food_list, creature_list)
            if self.is_hungry():
                target = food_target
            else:
                mate_target, mate_dist = self._scan_mate(creature_list)
                if food_target is None:
                    target = mate_target
                elif mate_target is None:
                    target = food_target
                else:
                    target = food_target if food_dist <= mate_dist else mate_target

        # Rescue herd: only for non-crocs, only when the species is nearly
        # extinct (≤ 5 individuals). Crocs use water homing instead so they
        # never drift away from the river.
        if target is None and not self.is_crocodile:
            same = [c for c in creature_list
                    if c is not self and c.alive and c.species == self.species]
            if 0 < len(same) <= 5:
                avg_x = sum(c.x for c in same) / len(same)
                avg_y = sum(c.y for c in same) / len(same)
                target = (avg_x, avg_y)

        # Crocodile homing: walk to the river when otherwise idle.
        if target is None and self.is_crocodile and not is_water(self.x, self.y):
            return (self.x, river_center_y(self.x))

        return target

    def _scan_predator(self, creature_list):
        """Nearest visible adult predator that hunts this creature's species."""
        nearest = None
        min_dist = float('inf')
        for other in creature_list:
            if not other.alive or not other.is_adult:
                continue
            # Will `other` hunt me?
            if self.species == SPECIES_HERB and other.species not in (SPECIES_CARN, SPECIES_CROC):
                continue
            if self.species == SPECIES_CARN and other.species != SPECIES_CROC:
                continue
            if not self.can_see(other.x, other.y):
                continue
            d = math.hypot(other.x - self.x, other.y - self.y)
            if d < min_dist:
                min_dist = d
                nearest = other
        return nearest

    def _scan_food(self, food_list, creature_list):
        best = None
        best_dist = float('inf')
        if self.is_predator:
            # Crocs only hunt when in water or close to it.
            if self.is_crocodile and not is_in_or_near_water(self.x, self.y):
                return None, float('inf')
            for other in creature_list:
                if not other.is_prey_for(self):
                    continue
                # Crocs don't chase prey that's already too far from water.
                if self.is_crocodile and not is_in_or_near_water(other.x, other.y):
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
        return best, best_dist

    def _scan_mate(self, creature_list):
        if not self.can_mate():
            return None, float('inf')
        # Crocs only seek mates while themselves in/near water, so they
        # don't go wandering across dry land chasing a partner.
        if self.is_crocodile and not is_in_or_near_water(self.x, self.y):
            return None, float('inf')
        best = None
        best_dist = float('inf')
        for other in creature_list:
            if other is self or not other.alive:
                continue
            if other.species != self.species:
                continue
            if not other.can_mate():
                continue
            # Crocs ignore mates that are too far from water.
            if self.is_crocodile and not is_in_or_near_water(other.x, other.y):
                continue
            if not self.can_see(other.x, other.y):
                continue
            d = math.hypot(other.x - self.x, other.y - self.y)
            if d < best_dist:
                best_dist = d
                best = (other.x, other.y)
        return best, best_dist

    def try_eat(self, food_list, creature_list):
        """Consume food/prey within touch range. Mutates food_list and marks prey dead."""
        now = time.time()
        cooldown = EAT_COOLDOWN_PRED if self.is_predator else EAT_COOLDOWN_HERB
        if now - self.last_food < cooldown:
            return False
        if self.is_predator:
            # Predator satiety.
            if self.energy >= PRED_FULL_ENERGY:
                return False
            # Crocs only attack when in/near water.
            if self.is_crocodile and not is_in_or_near_water(self.x, self.y):
                return False
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
        if self.species != other.species:
            return 'jiný druh'
        # Old herbivores can't reproduce; old carns and crocs can.
        if (self.is_old and self.species == SPECIES_HERB) or \
           (other.is_old and other.species == SPECIES_HERB):
            return 'stáří'
        cooldown = REPRODUCTION_COOLDOWN_PRED if self.is_predator else REPRODUCTION_COOLDOWN_HERB
        if now - self.last_reproduction < cooldown or \
           now - other.last_reproduction < cooldown:
            return 'cooldown'
        cost = REPRODUCTION_COST_PRED if self.is_predator else REPRODUCTION_COST_HERB
        if self.energy < cost or other.energy < cost:
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

        cost = REPRODUCTION_COST_PRED if self.is_predator else REPRODUCTION_COST_HERB
        self.energy -= cost
        other.energy -= cost
        self.last_reproduction = now
        other.last_reproduction = now
        self.reproduction_count += 1
        other.reproduction_count += 1

        n = sample_litter_size()
        if self.species in (SPECIES_CARN, SPECIES_CROC) and other.species == self.species:
            n = min(n, 2)  # predator lineages have smaller broods
        return [self._make_child(other) for _ in range(n)]

    def _make_child(self, other):
        # All continuous traits mutate via parent-average * uniform(0.9, 1.2):
        # -10% to +20%, average +5%/gen drift, so traits grow but can also
        # occasionally dip. User-specified.
        avg_speed = (self.speed + other.speed) / 2
        child_speed = avg_speed * random.uniform(MUTATION_LOW, MUTATION_HIGH)
        child_speed = min(child_speed, MAX_SPEED)

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

        # Child species:
        #  - both same predator → that predator
        #  - both herbivore → mutation roll (carn or croc or stay herb)
        #  - mixed species can't reproduce (filtered by repro_status)
        if self.species == other.species and self.species != SPECIES_HERB:
            child_species = self.species
        else:
            r = random.random()
            if r < CARNIVORE_CHANCE:
                child_species = SPECIES_CARN
            elif r < CARNIVORE_CHANCE + CROCODILE_CHANCE:
                child_species = SPECIES_CROC
            else:
                child_species = SPECIES_HERB

        return Creature(
            x=(self.x + other.x) / 2 + random.uniform(-5, 5),
            y=(self.y + other.y) / 2 + random.uniform(-5, 5),
            speed=child_speed,
            direction_change=child_dc,
            vision_range=child_vision,
            vision_angle=child_vangle,
            species=child_species,
        )


def initialize_simulation():
    global creatures, foods, simulation_start_time, _events, _last_stats_time
    global _next_creature_id
    _events = []
    _last_stats_time = 0.0
    _next_creature_id = 0
    cx, cy = MAP_SIZE / 2, MAP_SIZE / 2
    creatures = []
    for _ in range(INITIAL_CREATURES):
        # Place initial herbs on dry land in the cluster area.
        for _ in range(20):
            x = cx + random.uniform(-INITIAL_CLUSTER_RADIUS, INITIAL_CLUSTER_RADIUS)
            y = cy + random.uniform(-INITIAL_CLUSTER_RADIUS, INITIAL_CLUSTER_RADIUS)
            if not is_water(x, y):
                break
        creatures.append(Creature(
            x=x, y=y,
            speed=random.uniform(*INITIAL_SPEED_RANGE),
            direction_change=random.uniform(0.1, 0.3),
            vision_range=0,
            species=SPECIES_HERB,
        ))
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
            if not c.is_adult and age >= c.maturation_time():
                c.is_adult = True
                c.adult_time = now
            if c.is_adult and not c.is_old and c.reproduction_count >= 2:
                c.is_old = True
                c.old_time = now

            if c.is_old and (now - c.old_time) >= c.old_age_duration():
                c.alive = False
                continue
            if (now - c.last_food) > c.starvation_time():
                c.alive = False
                continue

            target = c.find_target(foods, creatures)
            target = c.maybe_river_crossing(target)
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
            'species': c.species,
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
