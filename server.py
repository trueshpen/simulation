import random
import math
import time
from flask import Flask, render_template
from flask_socketio import SocketIO, emit
import threading
import logging

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

# Konstanty
MAP_SIZE = 800
MAX_FOOD = 200
FOOD_SPAWN_RATE = 0.2
INITIAL_FOOD = 100
INITIAL_CREATURES = 10

# Konstanty pro tvory
MATURATION_TIME = 5  # Čas do dospělosti v sekundách
MAX_AGE = 30  # Maximální věk v sekundách
STARVATION_TIME = 20  # Čas do smrti hladem v sekundách
REPRODUCTION_COOLDOWN = 2  # Čas mezi reprodukcemi v sekundách
REPRODUCTION_COST = 20  # Cena reprodukce
MOVEMENT_COST = 0.1  # Cena pohybu
VISION_COST = 0.2  # Cena zraku
SPEED_COST = 0.1  # Cena rychlosti
DIRECTION_CHANGE_COST = 0.05  # Cena změny směru

# Konstanty pro mutace
CARNIVORE_CHANCE = 0.15  # Šance stát se masožravcem
VISION_CHANCE_NONE = 0.1  # Šance získat zrak bez rodičů se zrakem
VISION_CHANCE_ONE = 0.5  # Šance získat zrak s jedním rodičem se zrakem
VISION_CHANCE_BOTH = 1.0  # Šance získat zrak s oběma rodiči se zrakem
VISION_RANGE_NONE = (5, 10)  # Rozsah zraku bez rodičů se zrakem
VISION_RANGE_PARENT = (0.05, 0.2)  # Rozsah bonusu k zraku rodiče

# Globální proměnné
creatures = []
foods = []
simulation_running = False

def spawn_food():
    if len(foods) < MAX_FOOD and random.random() < FOOD_SPAWN_RATE:
        foods.append({
            'x': random.uniform(0, MAP_SIZE),
            'y': random.uniform(0, MAP_SIZE)
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
        self.direction = random.uniform(0, 360)
        self.last_reproduction = 0
        self.reproduction_count = 0
        self.last_food = time.time()
        self.age = 0
        self.is_adult = False
        self.is_old = False
        self.adult_time = None
        self.old_time = None
        self.creation_time = time.time()

    def move(self):
        # Změna směru podle genu
        if random.random() < self.direction_change:
            self.direction += random.uniform(-math.pi/4, math.pi/4)
        
        # Pohyb s ohledem na věk
        speed_multiplier = 1.0
        if self.is_adult and not self.is_old:
            speed_multiplier = 1.5
        elif self.is_old:
            speed_multiplier = 0.5
        
        self.x += math.cos(self.direction) * self.speed * speed_multiplier
        self.y += math.sin(self.direction) * self.speed * speed_multiplier
        
        # Odrážení od stěn
        if self.x < 0:
            self.x = 0
            self.direction = math.pi - self.direction
        elif self.x > MAP_SIZE:
            self.x = MAP_SIZE
            self.direction = math.pi - self.direction
        if self.y < 0:
            self.y = 0
            self.direction = -self.direction
        elif self.y > MAP_SIZE:
            self.y = MAP_SIZE
            self.direction = -self.direction
        
        # Spotřeba energie
        self.energy -= MOVEMENT_COST * self.speed * speed_multiplier
        if self.vision_range > 0:
            self.energy -= VISION_COST * self.vision_range
        self.energy -= SPEED_COST * self.speed
        self.energy -= DIRECTION_CHANGE_COST * self.direction_change

    def update_age(self):
        age = time.time() - self.last_food
        
        # Kontrola dospělosti
        if not self.is_adult and age >= 5:
            self.is_adult = True
            self.adult_time = time.time()
        
        # Kontrola stáří
        if self.is_adult and not self.is_old and self.reproduction_count >= 2:
            self.is_old = True
            self.old_time = time.time()
        
        # Kontrola hladu
        if age > STARVATION_TIME:
            return False
        
        # Kontrola maximálního věku
        if self.is_old and age > MAX_AGE:
            return False
        
        return True

    def can_see(self, target_x, target_y):
        if self.vision_range <= 0:
            return False
        
        dx = target_x - self.x
        dy = target_y - self.y
        distance = math.sqrt(dx*dx + dy*dy)
        
        if distance > self.vision_range:
            return False
        
        # Výpočet úhlu k cíli
        target_angle = math.atan2(dy, dx)
        angle_diff = abs(target_angle - self.direction)
        if angle_diff > math.pi:
            angle_diff = 2 * math.pi - angle_diff
        
        # Zrak je v úhlu 90 stupňů (π/2 radiánů)
        return angle_diff <= math.pi/4

    def find_nearest_food(self, food_list, creature_list):
        nearest_food = None
        min_distance = float('inf')
        
        for food in food_list:
            if self.is_carnivore:
                # Masožravci hledají jiné tvory
                for other in creature_list:
                    if other != self and (
                        (not other.is_carnivore and other.is_adult) or  # Býložravci
                        (other.is_carnivore and other.is_old)  # Staří masožravci
                    ):
                        if self.can_see(other.x, other.y):
                            dx = other.x - self.x
                            dy = other.y - self.y
                            distance = math.sqrt(dx*dx + dy*dy)
                            if distance < min_distance:
                                min_distance = distance
                                nearest_food = other
            else:
                # Býložravci hledají jídlo
                if self.can_see(food['x'], food['y']):
                    dx = food['x'] - self.x
                    dy = food['y'] - self.y
                    distance = math.sqrt(dx*dx + dy*dy)
                    if distance < min_distance:
                        min_distance = distance
                        nearest_food = food
        
        return nearest_food

    def can_reproduce_with(self, other):
        if not self.is_adult or not other.is_adult:
            return False
        
        if self.is_old or other.is_old:
            return False
        
        if time.time() - self.last_reproduction < REPRODUCTION_COOLDOWN:
            return False
        
        if time.time() - other.last_reproduction < REPRODUCTION_COOLDOWN:
            return False
        
        return True

    def reproduce(self, other):
        if not self.can_reproduce_with(other):
            return None
        
        self.energy -= REPRODUCTION_COST
        other.energy -= REPRODUCTION_COST
        self.last_reproduction = time.time()
        other.last_reproduction = time.time()
        self.reproduction_count += 1
        other.reproduction_count += 1
        
        return Creature(x=(self.x + other.x) / 2, y=(self.y + other.y) / 2, speed=(self.speed + other.speed) / 2, direction_change=(self.direction_change + other.direction_change) / 2, vision_range=(self.vision_range + other.vision_range) / 2, is_carnivore=self.is_carnivore or other.is_carnivore)

def initialize_simulation():
    global creatures, foods
    creatures = []
    foods = []
    
    # Create initial food
    for _ in range(INITIAL_FOOD):
        foods.append({
            'x': random.uniform(0, MAP_SIZE),
            'y': random.uniform(0, MAP_SIZE)
        })
    
    # Create initial creatures
    for _ in range(INITIAL_CREATURES):
        creatures.append(Creature(
            x=random.uniform(0, MAP_SIZE),
            y=random.uniform(0, MAP_SIZE),
            speed=random.uniform(2, 4),
            direction_change=random.uniform(0.1, 0.3),
            vision_range=0,
            is_carnivore=False
        ))

def update_simulation():
    global creatures, foods, simulation_running
    
    logger.info('Simulation thread started')
    
    while True:
        if not simulation_running:
            time.sleep(0.1)
            continue
            
        current_time = time.time()
        logger.debug(f'Updating simulation. Creatures: {len(creatures)}, Food: {len(foods)}')
        
        # Update creatures
        for c in creatures[:]:
            # Update age
            c.age = current_time - c.creation_time
            
            # Check adulthood
            if not c.is_adult and c.age >= 5:
                c.is_adult = True
                c.adult_time = current_time
            
            # Check old age
            if c.is_adult and not c.is_old and c.reproduction_count >= 2:
                c.is_old = True
                c.old_time = current_time
            
            # Check death from old age
            if c.is_old and current_time - c.old_time >= 30:
                creatures.remove(c)
                continue
            
            # Check starvation
            if current_time - c.last_food > STARVATION_TIME:
                creatures.remove(c)
                continue
            
            # Move creature
            c.move()
            
            # Check for food
            nearest_food = c.find_nearest_food(foods, creatures)
            if nearest_food:
                if c.is_carnivore and isinstance(nearest_food, Creature):
                    c.energy += 50
                    c.last_food = current_time
                    creatures.remove(nearest_food)
                elif not c.is_carnivore and isinstance(nearest_food, dict):
                    c.energy += 20
                    c.last_food = current_time
                    foods.remove(nearest_food)
            
            # Check for reproduction
            for other in creatures:
                if c != other and c.can_reproduce_with(other):
                    child = c.reproduce(other)
                    if child:
                        creatures.append(child)
        
        # Spawn new food
        if len(foods) < MAX_FOOD and random.random() < FOOD_SPAWN_RATE:
            foods.append({
                'x': random.uniform(0, MAP_SIZE),
                'y': random.uniform(0, MAP_SIZE)
            })
        
        # Send state to clients
        try:
            state = {
                'creatures': [{
                    'x': c.x,
                    'y': c.y,
                    'isCarnivore': c.is_carnivore,
                    'vision': c.vision_range,
                    'speed': c.speed,
                    'directionChange': c.direction_change,
                    'age': c.age,
                    'isAdult': c.is_adult,
                    'isOld': c.is_old
                } for c in creatures],
                'food': foods
            }
            socketio.emit('simulation_state', state)
            logger.debug('State sent to clients')
        except Exception as e:
            logger.error(f'Error sending state: {e}')
        
        time.sleep(0.1)

@app.route('/')
def index():
    return render_template('index.html')

@socketio.on('connect')
def handle_connect():
    logger.info('Client connected')
    if not creatures and not foods:
        logger.info('Initializing simulation')
        initialize_simulation()

@socketio.on('start_simulation')
def handle_start():
    logger.info('Starting simulation')
    global simulation_running
    simulation_running = True

@socketio.on('stop_simulation')
def handle_stop():
    logger.info('Stopping simulation')
    global simulation_running
    simulation_running = False

if __name__ == '__main__':
    logger.info('Starting server')
    # Initialize simulation
    initialize_simulation()
    
    # Start simulation thread
    simulation_thread = threading.Thread(target=update_simulation, daemon=True)
    simulation_thread.start()
    
    # Start Flask app
    socketio.run(app, debug=True, host='0.0.0.0') 