"""New Year Offensive event policy; no native GUI or network dependencies.

The atmosphere, collection, rank, craft and chest laws below are the exact
0.9.22 ``items/new_year_types.py`` values, and the container identifiers are
that release's ``scripts/item_defs/new_year/boxes.xml`` and ``chests.xml``.
The reward amounts, the daily offensive missions and the fragment ladder are
explicit offline extensions, not retail data: this fork has no event server
to price them.

Toy and slot definitions are never copied here.  They are read from the
installed client's own ``new_year_types.g_cache`` at call time, so a regional
build with a different ``toys.xml`` decorates with its own toys.  When that
cache is unavailable the toy-producing operations refuse instead of inventing
a toy id the client's cache would reject.

Nothing in this module imports BigWorld, ResMgr or a GUI module, so the
settlement, the wire shapes and the reward tables stay testable without the
client.

Client contract (0.9.22 ``account_helpers/ClientNewYear.synchronize``):

- ``diff['newYear']['slots']`` replaces the placed-toy list and fires
  ``onSlotsChanged``.  The stock ``NewYearController`` turns every changed
  slot into ``onSlotUpdated(slotDescr, toy)``, which ``NewYearBinder`` routes
  to ``CustomizableObjectsManager.updateSlot``.  Publishing the slots is
  therefore what decorates the real hangar; this module never touches a
  scene object itself.
- ``diff['newYear']['inventoryToys']`` maps toy id to a two-item
  ``(totalCount, newCount)`` pair, consumed as ``ToyInventoryData(*data)``.
  ``NewYearController.__onInventoryChanged`` indexes the client toy cache
  with these ids *unguarded*, so only ids present in the live cache may ever
  be published.  ``filtered_state`` enforces that.
- ``diff['tokens']`` carries the box and chest counts; the client keeps only
  the ids its own caches recognise and reads the count from ``data[1]``.

The event state deliberately stays ``NY_STATE.NOT_STARTED``: this module
publishes no ``eventsData`` entry.  Lighting the stock state up would run
``_StateHandler.update``, which pushes a service-channel message and writes
``AccountSettings`` christmas flags, and would make the stock requester
accept box opens that this fork settles itself.  None of that is needed for
decorations, and none of it is verifiable from this repository.
"""

import copy
import hashlib
import random
import time

try:
    string_types = (basestring,)
except NameError:
    string_types = (str,)


# ---------------------------------------------------------------- exact laws
# items/new_year_types.py, 0.9.22.
NATIONAL_SETTINGS = ('soviet', 'traditionalWestern', 'modernWestern', 'asian')
NATIONAL_SETTINGS_IDS_BY_NAME = dict(
    (name, index) for index, name in enumerate(NATIONAL_SETTINGS))
TOY_TYPES = ('top', 'hanging', 'garland', 'gift', 'snowman',
             'house_decoration', 'house_lamp', 'street_garland')
TOY_TYPES_IDS_BY_NAME = dict(
    (name, index) for index, name in enumerate(TOY_TYPES))
MAX_TOY_RANK = 5
INVALID_TOY_ID = -1
MAX_CHEST_LEVEL = 10
TOY_COLLECTION_BYTES = 40
MAX_TOY_ID = TOY_COLLECTION_BYTES * 8 - 1
TOY_ATMOSPHERE_BY_RANK = (1, 2, 4, 7, 25)
TOY_RATING_BY_RANK = (1, 1, 1, 1, 1)
BOUND_ATMOSPHERE_BY_LEVEL = (0, 8, 22, 41, 64, 91, 137, 186, 249, 325)
MAX_ATMOSPHERE_LEVEL = 10
BONUS_FACTOR_BY_ATMOSPHERE_LEVEL = (
    1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 12.0)
BOUND_COLLECTION_RATING_BY_LEVEL = (0, 8, 19, 37, 55)
MAX_COLLECTION_LEVEL = 5
# (xp, tmenXp, freeXp, credits), indexed by collection level minus one.
COLLECTION_BONUSES_BY_LEVEL = (
    (0.0, 0.0, 0.0, 0.0),
    (0.01, 0.01, 0.0, 0.01),
    (0.02, 0.02, 0.0, 0.02),
    (0.03, 0.03, 0.0, 0.03),
    (0.04, 0.04, 0.0, 0.04))
CRAFT_FRAGMENTS_BY_RANK = (10, 20, 30, 70, 250)
CRAFT_COST_BY_RANK = (20, 40, 60, 140, 480)
CRAFT_RANDOM_TOY_COST = 100
CRAFT_PROBABILITY_FOR_RANK = (
    (1, 0.5344), (2, 0.27), (3, 0.12), (4, 0.065), (5, 0.0106))
CRAFT_PROBABILITY_FOR_SETTING = tuple(
    (NATIONAL_SETTINGS_IDS_BY_NAME[name], 0.25) for name in NATIONAL_SETTINGS)
CRAFT_PROBABILITY_FOR_TYPE = (
    (TOY_TYPES_IDS_BY_NAME['top'], 0.08),
    (TOY_TYPES_IDS_BY_NAME['hanging'], 0.3),
    (TOY_TYPES_IDS_BY_NAME['garland'], 0.11),
    (TOY_TYPES_IDS_BY_NAME['gift'], 0.11),
    (TOY_TYPES_IDS_BY_NAME['snowman'], 0.1),
    (TOY_TYPES_IDS_BY_NAME['house_decoration'], 0.1),
    (TOY_TYPES_IDS_BY_NAME['house_lamp'], 0.1),
    (TOY_TYPES_IDS_BY_NAME['street_garland'], 0.1))

# NY_STATE, 0.9.22.  Only the first is ever published by this fork.
NY_STATE_NOT_STARTED = 0
NY_STATE_IN_PROGRESS = 1
NY_STATE_SUSPENDED = 2
NY_STATE_FINISHED = 3

# scripts/item_defs/new_year/boxes.xml and chests.xml, 0.9.22.
RANDOM_BOX_ID = 'ny18:box:r'
SETTING_BOX_IDS = {
    'soviet': 'ny18:box:s',
    'traditionalWestern': 'ny18:box:tw',
    'modernWestern': 'ny18:box:mw',
    'asian': 'ny18:box:a',
}
PRESENT_BOX_IDS = {
    'soviet': 'ny18:box:sp',
    'traditionalWestern': 'ny18:box:twp',
    'modernWestern': 'ny18:box:mwp',
    'asian': 'ny18:box:ap',
}
BOX_IDS = tuple(
    [RANDOM_BOX_ID] +
    [SETTING_BOX_IDS[name] for name in NATIONAL_SETTINGS] +
    [PRESENT_BOX_IDS[name] for name in NATIONAL_SETTINGS])
CHEST_IDS = tuple('ny18:chest:%d' % level
                  for level in range(1, MAX_CHEST_LEVEL + 1))
CHEST_LEVEL_BY_ID = dict((chest_id, level)
                         for level, chest_id in enumerate(CHEST_IDS, 1))

# ------------------------------------------------- offline reward extensions
# Container contents.  ``toys`` is how many toys the craft rules roll,
# ``setting`` pins them to one national setting, and a ``present`` box is the
# small guaranteed-reward container the retail event handed out separately.
BOX_CONTENTS = {
    RANDOM_BOX_ID: {'toys': 3, 'setting': None, 'fragments': 5, 'credits': 0},
    'ny18:box:s': {'toys': 3, 'setting': 'soviet', 'fragments': 5,
                   'credits': 0},
    'ny18:box:tw': {'toys': 3, 'setting': 'traditionalWestern',
                    'fragments': 5, 'credits': 0},
    'ny18:box:mw': {'toys': 3, 'setting': 'modernWestern', 'fragments': 5,
                    'credits': 0},
    'ny18:box:a': {'toys': 3, 'setting': 'asian', 'fragments': 5,
                   'credits': 0},
    'ny18:box:sp': {'toys': 1, 'setting': 'soviet', 'fragments': 0,
                    'credits': 5000},
    'ny18:box:twp': {'toys': 1, 'setting': 'traditionalWestern',
                     'fragments': 0, 'credits': 5000},
    'ny18:box:mwp': {'toys': 1, 'setting': 'modernWestern', 'fragments': 0,
                     'credits': 5000},
    'ny18:box:ap': {'toys': 1, 'setting': 'asian', 'fragments': 0,
                    'credits': 5000},
}
CHEST_CREDITS_PER_LEVEL = 1500
CHEST_FRAGMENTS_PER_LEVEL = 2

SNAPSHOT_KEY = 'newYear'
TOKEN_EXPIRY = 4104777660
# Cumulative fragments that earn the next ladder chest.
LADDER_STEP = 150
# One offensive is three missions, one per metric, exactly like the daily
# missions the same settlement already runs.
MISSION_METRICS = ('battles', 'damage', 'wins')
# (id, label, metric, target, fragments, boxes)
MISSION_POOL = (
    ('ny_battles_3', 'Complete %d battles', 'battles', 3, 15, 0),
    ('ny_battles_5', 'Complete %d battles', 'battles', 5, 25, 0),
    ('ny_damage_4000', 'Complete battles and deal %d damage', 'damage', 4000,
     20, 0),
    ('ny_damage_8000', 'Complete battles and deal %d damage', 'damage', 8000,
     35, 0),
    ('ny_wins_2', 'Complete and win %d battles', 'wins', 2, 10, 1),
    ('ny_wins_3', 'Complete and win %d battles', 'wins', 3, 20, 2),
)
MISSION_BY_ID = dict((row[0], row) for row in MISSION_POOL)
# The first win of each day is the offensive's own bonus container.
FIRST_WIN_BOXES = 1


def _error(message):
    """Raise the garage's own refusal type without importing it at load."""
    from gui.mods.offline_lan_0922.account_rpc.garage import GarageError
    raise GarageError(message)


def now_seconds(now=None):
    return int(time.time() if now is None else now)


def _count(value, minimum=0, maximum=None):
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        return minimum
    if result < minimum:
        return minimum
    if maximum is not None and result > maximum:
        return maximum
    return result


# ------------------------------------------------------------------ catalogue
def client_cache():
    """Return the installed client's New Year cache, or None."""
    try:
        from items import new_year_types
    except ImportError:
        return None
    return getattr(new_year_types, 'g_cache', None)


def _toy_groups(toys):
    groups = {}
    for toy_id, toy in toys.items():
        group = toy['typeID'] << 16 | toy['settingID'] << 8 | toy['rank']
        groups.setdefault(group, []).append(toy_id)
    for ids in groups.values():
        ids.sort()
    return groups


def catalogue(cache=None):
    """Normalize the client's toy/slot definitions, or return None.

    A definition this fork cannot bound is dropped rather than trusted: an
    out-of-range rank would index the exact rank tables out of bounds, and an
    out-of-range toy id would not fit the collection bitmap.
    """
    if cache is None:
        cache = client_cache()
    if cache is None:
        return None
    toys = {}
    for key, descr in (getattr(cache, 'toys', None) or {}).items():
        toy_id = _count(key, -1)
        rank = _count(getattr(descr, 'rank', 0), 0)
        name = getattr(descr, 'type', None)
        setting = getattr(descr, 'setting', None)
        if not 0 <= toy_id <= MAX_TOY_ID or not 1 <= rank <= MAX_TOY_RANK:
            continue
        if name not in TOY_TYPES_IDS_BY_NAME or setting is None:
            continue
        setting_id = _count(getattr(descr, 'settingID', -1), -1)
        if not 0 <= setting_id < len(NATIONAL_SETTINGS):
            setting_id = NATIONAL_SETTINGS_IDS_BY_NAME.get(setting, -1)
        if not 0 <= setting_id < len(NATIONAL_SETTINGS):
            continue
        toys[toy_id] = {
            'id': toy_id, 'type': name, 'rank': rank, 'setting': setting,
            'typeID': TOY_TYPES_IDS_BY_NAME[name], 'settingID': setting_id,
            'atmosphere': TOY_ATMOSPHERE_BY_RANK[rank - 1],
            'rating': TOY_RATING_BY_RANK[rank - 1],
            'fragments': CRAFT_FRAGMENTS_BY_RANK[rank - 1],
        }
    raw_slots = getattr(cache, 'slots', None) or ()
    slots = []
    for descr in raw_slots:
        if descr is None:
            slots.append(None)
            continue
        name = getattr(descr, 'type', None)
        if name not in TOY_TYPES_IDS_BY_NAME:
            slots.append(None)
            continue
        slots.append({'id': _count(getattr(descr, 'id', len(slots))),
                      'type': name})
    if not toys or not slots:
        return None
    return {'toys': toys, 'slots': slots, 'groups': _toy_groups(toys)}


def slot_count(cat):
    """Return the placed-toy list length the client expects."""
    if cat is None:
        cat = catalogue()
    return len(cat['slots']) if cat is not None else 0


# ---------------------------------------------------------------------- state
def _normalized(raw):
    result = {
        'slots': [], 'inventory': {}, 'fragments': 0, 'collection': [],
        'boxes': {}, 'chests': {}, 'missions': {}, 'ladder': 0,
        'firstWinDay': -1,
    }
    if not isinstance(raw, dict):
        return result
    for toy_id, count in (raw.get('inventory') or {}).items():
        total = _count(toy_id, -1)
        owned = _count(count if not isinstance(count, (list, tuple))
                       else (count[0] if count else 0))
        if 0 <= total <= MAX_TOY_ID and owned > 0:
            result['inventory'][total] = owned
    result['slots'] = [_count(value, INVALID_TOY_ID, MAX_TOY_ID)
                       for value in (raw.get('slots') or ())]
    result['fragments'] = _count(raw.get('fragments'))
    result['ladder'] = _count(raw.get('ladder'))
    seen = set()
    for toy_id in (raw.get('collection') or ()):
        toy_id = _count(toy_id, -1)
        if 0 <= toy_id <= MAX_TOY_ID:
            seen.add(toy_id)
    # An owned toy is always collected; a save that lost the bitmap keeps its
    # collection rating instead of silently dropping event bonuses.
    seen.update(result['inventory'])
    result['collection'] = sorted(seen)
    for key, ids in (('boxes', BOX_IDS), ('chests', CHEST_IDS)):
        for identifier in ids:
            owned = _count((raw.get(key) or {}).get(identifier))
            if owned:
                result[key][identifier] = owned
    missions = raw.get('missions')
    result['missions'] = missions if isinstance(missions, dict) else {}
    # Kept outside ``missions`` on purpose: ``daily_state`` rebuilds that
    # mapping for today and would otherwise drop this marker every battle.
    result['firstWinDay'] = _count(raw.get('firstWinDay', -1), -1)
    return result


def state(snapshot):
    raw = (snapshot or {}).get(SNAPSHOT_KEY)
    return _normalized(raw)


def write(snapshot, values):
    """Store one normalized state, dropping empty optional rows."""
    if not isinstance(snapshot, dict):
        return
    stored = {
        'slots': list(values['slots']),
        'inventory': dict(values['inventory']),
        'fragments': int(values['fragments']),
        'collection': sorted(set(values['collection'])),
        'boxes': dict(values['boxes']),
        'chests': dict(values['chests']),
        'ladder': int(values['ladder']),
        'missions': copy.deepcopy(values['missions']),
        'firstWinDay': int(values['firstWinDay']),
    }
    snapshot[SNAPSHOT_KEY] = stored


def filtered_state(snapshot, cat=None):
    """Return the state bounded by the live client catalogue.

    ``NewYearController.__onInventoryChanged`` indexes the client toy cache
    with a published inventory id and does not guard the lookup, so a toy this
    build no longer defines must not be published at all.  Placed toys fall
    back to the empty slot, which is what the client itself does.
    """
    if cat is None:
        cat = catalogue()
    values = state(snapshot)
    if cat is None:
        values['slots'] = []
        values['inventory'] = {}
        values['collection'] = []
        return values
    known = cat['toys']
    values['inventory'] = dict((toy_id, count) for toy_id, count
                               in values['inventory'].items()
                               if toy_id in known)
    values['slots'] = [toy_id if toy_id in known else INVALID_TOY_ID
                       for toy_id in values['slots']]
    count = len(cat['slots'])
    if len(values['slots']) < count:
        values['slots'].extend([INVALID_TOY_ID] * (count - len(values['slots'])))
    del values['slots'][count:]
    values['collection'] = [toy_id for toy_id in values['collection']
                            if toy_id in known]
    return values


def saved_fields(snapshot):
    return _normalized((snapshot or {}).get(SNAPSHOT_KEY))


def restored_fields(ledger):
    """Rebuild the snapshot section from one saved ledger row."""
    return {SNAPSHOT_KEY: _normalized(ledger)}


# ------------------------------------------------------- atmosphere/collection
def total_atmosphere(slots, cat):
    if cat is None:
        return 0
    toys = cat['toys']
    return sum(toys[toy_id]['atmosphere'] for toy_id in slots
               if toy_id in toys)


def atmosphere_level(slots, cat):
    total = total_atmosphere(slots, cat)
    for level, bound in enumerate(BOUND_ATMOSPHERE_BY_LEVEL):
        if total < bound:
            return level
    return MAX_ATMOSPHERE_LEVEL


def atmosphere_progress(slots, cat):
    total = total_atmosphere(slots, cat)
    for level, bound in enumerate(BOUND_ATMOSPHERE_BY_LEVEL):
        if total < bound:
            previous = BOUND_ATMOSPHERE_BY_LEVEL[level - 1]
            return (total - previous, bound - previous)
    final = (BOUND_ATMOSPHERE_BY_LEVEL[-1] - BOUND_ATMOSPHERE_BY_LEVEL[-2])
    return (final, final)


def collection_ratings(collection, cat):
    ratings = [0] * len(NATIONAL_SETTINGS)
    if cat is None:
        return ratings
    toys = cat['toys']
    for toy_id in collection:
        toy = toys.get(toy_id)
        if toy is not None:
            ratings[toy['settingID']] += toy['rating']
    return ratings


def collection_level(rating):
    for level, bound in enumerate(BOUND_COLLECTION_RATING_BY_LEVEL):
        if rating < bound:
            return level
    return MAX_COLLECTION_LEVEL


def collection_levels(collection, cat):
    return tuple(collection_level(rating)
                 for rating in collection_ratings(collection, cat))


def bonuses(atmosphere, collection):
    """Return the exact 0.9.22 ``(xp, tmenXp, freeXp, credits)`` fractions."""
    atmosphere = _count(atmosphere, 0, MAX_ATMOSPHERE_LEVEL)
    collection = _count(collection, 0, MAX_COLLECTION_LEVEL)
    if atmosphere < 1 or collection < 1:
        return (0.0, 0.0, 0.0, 0.0)
    row = COLLECTION_BONUSES_BY_LEVEL[collection - 1]
    factor = BONUS_FACTOR_BY_ATMOSPHERE_LEVEL[atmosphere - 1]
    return (row[0] * factor, row[1] * factor, row[2] * factor,
            row[3] * factor)


def event_bonus_amounts(snapshot, awarded, cat=None):
    """Return this battle's extra income from the decorated hangar.

    The amounts match the keys ``economy.battle_income`` already adds to its
    reserve bonuses row, so the event shares one tested income path instead of
    introducing a second multiplier stage.
    """
    if cat is None:
        cat = catalogue()
    if cat is None:
        return {}
    awarded = awarded if isinstance(awarded, dict) else {}
    values = filtered_state(snapshot, cat)
    levels = collection_levels(values['collection'], cat)
    atmosphere = atmosphere_level(values['slots'], cat)
    result = {}
    for setting_id, collection in enumerate(levels):
        xp, tankmen, free_xp, credits = bonuses(atmosphere, collection)
        for key, fraction, base in (
                ('xp', xp, 'xp'), ('crew_xp', tankmen, 'xp'),
                ('free_xp', free_xp, 'free_xp'),
                ('credits', credits, 'credits')):
            amount = int(max(0, _count(awarded.get(base))) * fraction)
            if amount > result.get(key, 0):
                result[key] = amount
    return result


def progress(snapshot, cat=None):
    """Return the event's displayed progress row."""
    if cat is None:
        cat = catalogue()
    values = filtered_state(snapshot, cat)
    atmosphere = atmosphere_level(values['slots'], cat)
    current, bound = atmosphere_progress(values['slots'], cat)
    return {
        'atmosphere': atmosphere,
        'maxAtmosphere': MAX_ATMOSPHERE_LEVEL,
        'progress': current,
        'bound': bound,
        'totalAtmosphere': total_atmosphere(values['slots'], cat),
        'collectionLevels': collection_levels(values['collection'], cat),
        'fragments': values['fragments'],
        'boxes': dict(values['boxes']),
        'chests': dict(values['chests']),
        'placed': sum(1 for toy_id in values['slots']
                      if toy_id != INVALID_TOY_ID),
        'slots': len(values['slots']),
        'collected': len(values['collection']),
        'toyCount': len(cat['toys']) if cat is not None else 0,
    }


# ------------------------------------------------------------ wire publishing
def pack_collection(toy_ids):
    """Return the 40-byte bitmap ``_unpackBinaryCollection`` reads."""
    data = bytearray(TOY_COLLECTION_BYTES)
    for toy_id in toy_ids:
        toy_id = _count(toy_id, -1)
        if 0 <= toy_id <= MAX_TOY_ID:
            data[toy_id >> 3] |= 1 << (toy_id & 7)
    return data


def sync_payload(snapshot, cat=None):
    """Return the exact ``diff['newYear']`` section for one account push.

    An empty mapping is the only safe answer when the client's toy catalogue
    cannot be read.  ``ClientNewYear.synchronize`` replaces ``__slots`` with
    whatever length this publishes, and ``NewYearController.getPlacedToy``
    indexes that list by a slot id bounded only by the *descriptor* count, so
    a short list would raise inside the stock lobby instead of showing an
    undecorated tree.  Omitting every key leaves the client's own defaults
    standing, which is what an account with no event data already does.
    """
    if cat is None:
        cat = catalogue()
    if cat is None:
        return {}
    values = filtered_state(snapshot, cat)
    return {
        'slots': list(values['slots']),
        'inventoryToys': dict((toy_id, (count, 0)) for toy_id, count
                              in values['inventory'].items()),
        'level': atmosphere_level(values['slots'], cat),
        'toyFragments': int(values['fragments']),
        'toyCollection': pack_collection(values['collection']),
    }


def token_payload(snapshot):
    """Return the box and chest tokens the client's own caches recognise."""
    values = state(snapshot)
    tokens = {}
    for identifier, count in values['boxes'].items():
        tokens[identifier] = (TOKEN_EXPIRY, int(count))
    for identifier, count in values['chests'].items():
        tokens[identifier] = (TOKEN_EXPIRY, int(count))
    return tokens


def service_diff(snapshot, unused_outcome=None):
    """Publish the event with the same builder a full sync uses."""
    return {'newYear': sync_payload(snapshot),
            'tokens': token_payload(snapshot)}


# ------------------------------------------------------------------ toy rules
def make_random_choice(choices, rng):
    roll = rng.random()
    probability = 0.0
    for value, weight in choices:
        probability += weight
        if roll <= probability:
            return value
    return choices[0][0]


def choose_toy(cat, type_id, setting_id, rank, rng):
    """Apply the exact 0.9.22 craft rules, bounded by this build's toys."""
    if cat is None:
        return None
    if type_id is None or type_id < 0:
        type_id = make_random_choice(CRAFT_PROBABILITY_FOR_TYPE, rng)
    if setting_id is None or setting_id < 0:
        setting_id = make_random_choice(CRAFT_PROBABILITY_FOR_SETTING, rng)
    if rank is None or rank < 0:
        rank = make_random_choice(CRAFT_PROBABILITY_FOR_RANK, rng)
    rank = _count(rank, 1, MAX_TOY_RANK)
    group = cat['groups'].get(type_id << 16 | setting_id << 8 | rank)
    if not group:
        # A regional build may omit one type/setting/rank combination. Roll
        # the rank down instead of failing an already paid container.
        for fallback in range(rank - 1, 0, -1):
            group = cat['groups'].get(type_id << 16 | setting_id << 8 | fallback)
            if group:
                break
    if not group:
        return None
    return int(rng.choice(group))


def grant_toys(values, toy_ids, cat):
    """Add rolled toys to one normalized state; return the accepted ids."""
    granted = []
    for toy_id in toy_ids:
        toy = cat['toys'].get(int(toy_id)) if cat is not None else None
        if toy is None:
            continue
        toy_id = int(toy_id)
        values['inventory'][toy_id] = values['inventory'].get(toy_id, 0) + 1
        if toy_id not in values['collection']:
            values['collection'].append(toy_id)
        granted.append(toy_id)
    values['collection'] = sorted(set(values['collection']))
    return granted


def add_fragments(values, amount):
    amount = _count(amount)
    values['fragments'] += amount
    return amount


def add_containers(values, key, identifier, amount):
    amount = _count(amount)
    if amount <= 0:
        return 0
    known = BOX_IDS if key == 'boxes' else CHEST_IDS
    if identifier not in known:
        return 0
    values[key][identifier] = values[key].get(identifier, 0) + amount
    return amount


def ladder_chests(values):
    """Convert accumulated fragments into ladder chests, in place."""
    granted = []
    while values['fragments'] >= (values['ladder'] + 1) * LADDER_STEP:
        values['ladder'] += 1
        level = min(MAX_CHEST_LEVEL, values['ladder'])
        identifier = CHEST_IDS[level - 1]
        add_containers(values, 'chests', identifier, 1)
        granted.append(identifier)
    return granted


# ------------------------------------------------------------- the offensive
def _mission_rank(day, key):
    return hashlib.sha256(
        ('newyear:%d:%s' % (day, key)).encode('ascii')).digest()


def daily_state(snapshot, now=None):
    """Return today's three offensive missions and their progress."""
    day = now_seconds(now) // 86400
    raw = state(snapshot).get('missions') or {}
    same_day = _count(raw.get('day', -1), -1) == day
    ids = raw.get('missions') if same_day else None
    if not (isinstance(ids, (list, tuple)) and len(ids) == len(MISSION_METRICS)
            and len(set(ids)) == len(MISSION_METRICS)
            and all(key in MISSION_BY_ID for key in ids)):
        ids = [min((row for row in MISSION_POOL if row[2] == metric),
                   key=lambda row: _mission_rank(day, row[0]))[0]
               for metric in MISSION_METRICS]
    ids = list(ids)
    claimed = raw.get('claimed') if same_day else ()
    result = {'day': day, 'missions': ids,
              'claimed': [key for key in ids if key in (claimed or ())]}
    for key in ids:
        target = MISSION_BY_ID[key][3]
        result[key] = (min(target, _count(raw.get(key)))
                       if same_day else 0)
    return result


def advance_battle(snapshot, facts, now=None):
    """Advance the offensive by one battle, inside the receipt transaction.

    Returns what the battle granted.  Toys are not granted here: only
    fragments and containers, so the settlement never needs the client's toy
    catalogue and a receipt replayed on a build with different toys still
    settles identically.
    """
    facts = facts if isinstance(facts, dict) else {}
    if facts.get('premature_leave', False):
        return None
    stamp = now_seconds(now if now is not None else facts.get('finished_at'))
    values = state(snapshot)
    if _count((values.get('missions') or {}).get('day', -1), -1) > stamp // 86400:
        # A save from a later day must not be rewound by a late receipt.
        return None
    daily = daily_state(snapshot, stamp)
    metrics = {'battles': 1,
               'damage': max(0, _count(facts.get('damage'))),
               'wins': int(bool(facts.get('won')))}
    completed = []
    # The receipt is what the player is told and what a replayed battle
    # observes, so every container it grants has to be counted here and not
    # only in the state.
    granted = {'fragments': 0, 'boxes': {}, 'chests': {}, 'missions': completed}
    for key in daily['missions']:
        unused_id, unused_label, metric, target, fragments, boxes = \
            MISSION_BY_ID[key]
        daily[key] = min(target, daily[key] + metrics[metric])
        if daily[key] >= target and key not in daily['claimed']:
            daily['claimed'].append(key)
            add_fragments(values, fragments)
            for unused_index in range(boxes):
                if add_containers(values, 'boxes', RANDOM_BOX_ID, 1):
                    granted['boxes'][RANDOM_BOX_ID] = (
                        granted['boxes'].get(RANDOM_BOX_ID, 0) + 1)
            completed.append(key)
    values['missions'] = daily
    if metrics['wins'] and stamp // 86400 != _count(
            values.get('firstWinDay', -1), -1):
        values['firstWinDay'] = stamp // 86400
        if add_containers(values, 'boxes', RANDOM_BOX_ID, FIRST_WIN_BOXES):
            granted['boxes'][RANDOM_BOX_ID] = FIRST_WIN_BOXES
    for identifier in ladder_chests(values):
        granted['chests'][identifier] = granted['chests'].get(identifier, 0) + 1
    granted['fragments'] = sum(
        MISSION_BY_ID[key][4] for key in completed)
    write(snapshot, values)
    return granted


# ----------------------------------------------------------------- operations
def _rng():
    return random.Random()


def place_toy(values, slot_id, toy_id, cat):
    """Place or clear one slot; return the accepted toy id."""
    if cat is None:
        _error('NEW_YEAR_CATALOGUE_UNAVAILABLE')
    slot_id = _count(slot_id, -1)
    if not 0 <= slot_id < len(cat['slots']):
        _error('NEW_YEAR_INVALID_SLOT')
    slot = cat['slots'][slot_id]
    if slot is None:
        _error('NEW_YEAR_INVALID_SLOT')
    toy_id = _count(toy_id, INVALID_TOY_ID, MAX_TOY_ID)
    if toy_id == INVALID_TOY_ID:
        placed = values['slots'][slot_id] if slot_id < len(values['slots']) \
            else INVALID_TOY_ID
        while len(values['slots']) <= slot_id:
            values['slots'].append(INVALID_TOY_ID)
        values['slots'][slot_id] = INVALID_TOY_ID
        # A removed toy returns to the inventory it was placed from.
        if placed != INVALID_TOY_ID:
            values['inventory'][placed] = values['inventory'].get(placed, 0) + 1
        return INVALID_TOY_ID
    toy = cat['toys'].get(toy_id)
    if toy is None:
        _error('NEW_YEAR_UNKNOWN_TOY')
    if toy['type'] != slot['type']:
        _error('NEW_YEAR_TOY_SLOT_TYPE_MISMATCH')
    while len(values['slots']) < len(cat['slots']):
        values['slots'].append(INVALID_TOY_ID)
    previous = values['slots'][slot_id]
    if previous == toy_id:
        _error('NEW_YEAR_TOY_ALREADY_PLACED')
    if values['inventory'].get(toy_id, 0) < 1:
        _error('NEW_YEAR_TOY_NOT_OWNED')
    values['inventory'][toy_id] -= 1
    if values['inventory'][toy_id] <= 0:
        del values['inventory'][toy_id]
    if previous != INVALID_TOY_ID:
        values['inventory'][previous] = values['inventory'].get(previous, 0) + 1
    values['slots'][slot_id] = toy_id
    return toy_id


def open_box(values, box_id, cat, rng=None):
    if box_id not in BOX_CONTENTS:
        _error('NEW_YEAR_UNKNOWN_BOX')
    if values['boxes'].get(box_id, 0) < 1:
        _error('NEW_YEAR_NO_BOXES')
    rng = rng or _rng()
    contents = BOX_CONTENTS[box_id]
    values['boxes'][box_id] -= 1
    if values['boxes'][box_id] <= 0:
        del values['boxes'][box_id]
    setting_id = (NATIONAL_SETTINGS_IDS_BY_NAME[contents['setting']]
                  if contents['setting'] else -1)
    rolled = [choose_toy(cat, -1, setting_id, -1, rng)
              for unused_index in range(contents['toys'])]
    granted = grant_toys(values, [toy for toy in rolled if toy is not None],
                         cat)
    add_fragments(values, contents['fragments'])
    result = {'kind': 'box', 'id': box_id, 'toys': granted,
              'fragments': contents['fragments'],
              'credits': _count(contents['credits'])}
    for identifier in ladder_chests(values):
        result.setdefault('chests', {})
        result['chests'][identifier] = result.get('chests', {}).get(
            identifier, 0) + 1
    return result


def open_chest(values, chest_id, cat, rng=None):
    level = CHEST_LEVEL_BY_ID.get(chest_id)
    if level is None:
        _error('NEW_YEAR_UNKNOWN_CHEST')
    if values['chests'].get(chest_id, 0) < 1:
        _error('NEW_YEAR_NO_CHESTS')
    rng = rng or _rng()
    values['chests'][chest_id] -= 1
    if values['chests'][chest_id] <= 0:
        del values['chests'][chest_id]
    rank = min(MAX_TOY_RANK, 1 + (level - 1) // 3)
    rolled = choose_toy(cat, -1, -1, rank, rng)
    granted = grant_toys(values, [rolled] if rolled is not None else [], cat)
    fragments = CHEST_FRAGMENTS_PER_LEVEL * level
    add_fragments(values, fragments)
    return {'kind': 'chest', 'id': chest_id, 'level': level, 'toys': granted,
            'fragments': fragments,
            'credits': CHEST_CREDITS_PER_LEVEL * level}


def craft_toy(values, toy_type, setting, rank, cat, rng=None):
    if cat is None:
        _error('NEW_YEAR_CATALOGUE_UNAVAILABLE')
    type_id = -1 if toy_type is None else TOY_TYPES_IDS_BY_NAME.get(toy_type, -2)
    setting_id = (-1 if setting is None
                  else NATIONAL_SETTINGS_IDS_BY_NAME.get(setting, -2))
    if type_id == -2 or setting_id == -2:
        _error('NEW_YEAR_INVALID_CRAFT')
    if rank is None:
        wanted = -1
    else:
        wanted = _count(rank, -1)
        if not (-1 <= wanted <= MAX_TOY_RANK):
            _error('NEW_YEAR_INVALID_CRAFT')
    cost = calculate_craft_cost(toy_type is not None, setting is not None,
                                wanted)
    if values['fragments'] < cost:
        _error('NEW_YEAR_NOT_ENOUGH_FRAGMENTS')
    rng = rng or _rng()
    toy_id = choose_toy(cat, type_id, setting_id, wanted, rng)
    if toy_id is None:
        _error('NEW_YEAR_CRAFT_UNAVAILABLE')
    values['fragments'] -= cost
    grant_toys(values, [toy_id], cat)
    return {'kind': 'craft', 'toys': [toy_id], 'cost': cost,
            'fragments': 0, 'credits': 0}


def break_toys(values, pairs, cat):
    """Break owned, unplaced toys back into fragments."""
    if cat is None:
        _error('NEW_YEAR_CATALOGUE_UNAVAILABLE')
    requested = []
    for pair in pairs or ():
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            _error('NEW_YEAR_INVALID_BREAK')
        toy_id = _count(pair[0], -1)
        amount = _count(pair[1])
        if toy_id not in cat['toys'] or amount < 1:
            _error('NEW_YEAR_INVALID_BREAK')
        requested.append((toy_id, amount))
    if not requested:
        _error('NEW_YEAR_INVALID_BREAK')
    available = dict(values['inventory'])
    fragments = 0
    for toy_id, amount in requested:
        if available.get(toy_id, 0) < amount:
            _error('NEW_YEAR_TOY_NOT_OWNED')
        available[toy_id] -= amount
        fragments += cat['toys'][toy_id]['fragments'] * amount
    values['inventory'] = dict((toy_id, count) for toy_id, count
                               in available.items() if count > 0)
    add_fragments(values, fragments)
    return {'kind': 'break', 'toys': [], 'fragments': fragments, 'credits': 0}


def calculate_craft_cost(is_type_specified, is_setting_specified, rank):
    """The exact 0.9.22 craft price, including both specification surcharges."""
    if rank is None or rank == -1:
        base = CRAFT_RANDOM_TOY_COST
    elif 1 <= rank <= MAX_TOY_RANK:
        base = CRAFT_COST_BY_RANK[rank - 1]
    else:
        return None
    cost = base
    if is_type_specified:
        cost += base
    if is_setting_specified:
        cost += base
    return int(cost)


def _parse_place_key(key):
    parts = str(key).split(':')
    if len(parts) != 2:
        _error('NEW_YEAR_INVALID_SLOT')
    return _count(parts[0], -1), _count(parts[1], INVALID_TOY_ID, MAX_TOY_ID)


def _parse_craft_key(key):
    parts = str(key).split(':')
    if len(parts) != 3:
        _error('NEW_YEAR_INVALID_CRAFT')
    toy_type = parts[0] or None
    setting = parts[1] or None
    rank = -1 if not parts[2] else _count(parts[2], -2)
    return toy_type, setting, rank


def _parse_break_key(key):
    import json
    try:
        rows = json.loads(str(key))
    except (TypeError, ValueError):
        _error('NEW_YEAR_INVALID_BREAK')
    if not isinstance(rows, list):
        _error('NEW_YEAR_INVALID_BREAK')
    return [tuple(row) for row in rows]


def transact(state_owner, action, key, rng=None):
    """Mutate through GarageState's rollback and persistence boundary.

    Returns the reward row so the caller can publish one receipt for what was
    actually granted.  Credits go through the wallet owner, never by writing
    the snapshot directly.
    """
    cat = catalogue()
    if cat is None and action in ('place_toy', 'open_box', 'open_chest',
                                  'craft_toy', 'break_toys'):
        _error('NEW_YEAR_CATALOGUE_UNAVAILABLE')
    with state_owner._transaction():
        snapshot = state_owner.snapshot()
        values = state(snapshot)
        if action == 'place_toy':
            slot_id, toy_id = _parse_place_key(key)
            outcome = {'kind': 'slot', 'slot': slot_id,
                       'toy': place_toy(values, slot_id, toy_id, cat),
                       'toys': [], 'fragments': 0, 'credits': 0}
        elif action == 'open_box':
            outcome = open_box(values, str(key), cat, rng)
        elif action == 'open_chest':
            outcome = open_chest(values, str(key), cat, rng)
        elif action == 'craft_toy':
            toy_type, setting, rank = _parse_craft_key(key)
            outcome = craft_toy(values, toy_type, setting, rank, cat, rng)
        elif action == 'break_toys':
            outcome = break_toys(values, _parse_break_key(key), cat)
        else:
            _error('NEW_YEAR_UNKNOWN_ACTION')
        credits = _count(outcome.get('credits'))
        if credits:
            state_owner._pay_back({'credits': credits})
        write(snapshot, values)
        state_owner.revision += 1
    outcome['progress'] = progress(state_owner.snapshot(), cat)
    return outcome
