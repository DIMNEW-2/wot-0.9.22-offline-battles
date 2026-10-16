"""New Year Offensive: exact laws, wire shape, settlement and garage actions."""

import json
import sys
import unittest
from unittest import mock

import test_port_0922_garage as garage_fixture

_MISSING = object()


class _Toy(object):
    def __init__(self, toy_id, kind, rank, setting):
        self.id = toy_id
        self.type = kind
        self.rank = rank
        self.setting = setting


class _Slot(object):
    def __init__(self, slot_id, kind):
        self.id = slot_id
        self.type = kind


class _Cache(object):
    """A complete toy grid, shaped like the client's own g_cache."""

    def __init__(self):
        self.toys = {}
        toy_id = 0
        for kind in ('top', 'hanging', 'garland', 'gift', 'snowman',
                     'house_decoration', 'house_lamp', 'street_garland'):
            for setting in ('soviet', 'traditionalWestern', 'modernWestern',
                            'asian'):
                for rank in range(1, 6):
                    toy = _Toy(toy_id, kind, rank, setting)
                    toy.settingID = ('soviet', 'traditionalWestern',
                                     'modernWestern', 'asian').index(setting)
                    self.toys[toy_id] = toy
                    toy_id += 1
        # slots.xml order: one top, four hanging, two garland, two gift, then
        # snowman, house decoration, house lamp and street garland.
        kinds = (['top'] + ['hanging'] * 4 + ['garland'] * 2 + ['gift'] * 2 +
                 ['snowman', 'house_decoration', 'house_lamp',
                  'street_garland'])
        self.slots = [_Slot(index, kind) for index, kind in enumerate(kinds)]


class _Rng(object):
    """Deterministic stand-in exposing only random() and choice()."""

    def __init__(self, rolls=None, picks=None):
        self._rolls = list(rolls or [])
        self._picks = list(picks or [])

    def random(self):
        return self._rolls.pop(0) if self._rolls else 0.0

    def choice(self, rows):
        if self._picks:
            return rows[self._picks.pop(0) % len(rows)]
        return rows[0]


class NewYearEventTests(unittest.TestCase):
    def setUp(self):
        self.requests, self.commands, self.garage = \
            garage_fixture._request_modules()
        self.policy = garage_fixture._load_port_module('new_year_event')
        module_scope = mock.patch.dict(sys.modules, {
            'gui.mods.offline_lan_0922.account_rpc.garage': self.garage})
        module_scope.start()
        self.addCleanup(module_scope.stop)
        self.cache = _Cache()
        self.cat = self.policy.catalogue(self.cache)
        self.assertIsNotNone(self.cat)

    def _state(self, wallet=None):
        return self.garage.GarageState({
            'wallet': wallet or {'gold': 0, 'credits': 0, 'crystal': 0,
                                 'freeXP': 0}})

    def _with_cache(self, cache=_MISSING):
        """Serve a fake client cache, or no cache at all."""
        scope = mock.patch.object(
            self.policy, 'client_cache',
            lambda: (self.cache if cache is _MISSING else cache))
        scope.start()
        self.addCleanup(scope.stop)

    # ------------------------------------------------------------- the laws
    def test_atmosphere_and_collection_laws_match_the_release(self):
        self.assertEqual((1, 2, 4, 7, 25), self.policy.TOY_ATMOSPHERE_BY_RANK)
        self.assertEqual((0, 8, 22, 41, 64, 91, 137, 186, 249, 325),
                         self.policy.BOUND_ATMOSPHERE_BY_LEVEL)
        self.assertEqual(10, len(self.policy.BOUND_ATMOSPHERE_BY_LEVEL))
        self.assertEqual(10, len(self.policy.BONUS_FACTOR_BY_ATMOSPHERE_LEVEL))
        self.assertEqual((0, 8, 19, 37, 55),
                         self.policy.BOUND_COLLECTION_RATING_BY_LEVEL)
        self.assertEqual(5, len(self.policy.COLLECTION_BONUSES_BY_LEVEL))
        for table in (self.policy.CRAFT_FRAGMENTS_BY_RANK,
                      self.policy.CRAFT_COST_BY_RANK):
            self.assertEqual(5, len(table))

    def test_atmosphere_level_and_progress_boundaries(self):
        # getAtmosphereLevel walks the bounds with a strict comparison, so an
        # undecorated hangar already sits on level 1; the level-1 bonus row is
        # the all-zero one, which is what keeps the payout at zero.
        self.assertEqual(1, self.policy.atmosphere_level([], self.cat))
        self.assertEqual((0, 8), self.policy.atmosphere_progress([], self.cat))
        self.assertEqual(1, self.policy.collection_level(0))
        # One rank-5 toy is 25 atmosphere: level 3, inside the 22..41 band.
        slot = [None] * 13
        rank5 = next(toy_id for toy_id, toy in self.cat['toys'].items()
                     if toy['rank'] == 5)
        slot[0] = rank5
        self.assertEqual(25, self.policy.total_atmosphere(slot, self.cat))
        self.assertEqual(3, self.policy.atmosphere_level(slot, self.cat))
        self.assertEqual((3, 19), self.policy.atmosphere_progress(slot,
                                                                 self.cat))

    def test_bonus_is_zero_until_both_levels_are_reached(self):
        # Retail indexes the bonus table by ``level - 1``, so level 2 pays the
        # second row (0.01), and the atmosphere factor multiplies it.
        self.assertEqual((0.0, 0.0, 0.0, 0.0), self.policy.bonuses(1, 1))
        self.assertEqual((0.03, 0.03, 0.0, 0.03), self.policy.bonuses(3, 2))
        self.assertEqual((0.02, 0.02, 0.0, 0.02), self.policy.bonuses(1, 3))
        # Collection level 5 (0.04) at atmosphere level 10 (factor 12.0).
        self.assertEqual((0.48, 0.48, 0.0, 0.48), self.policy.bonuses(10, 5))
        # Free XP carries no bonus at any collection level in this release.
        for level in range(1, 6):
            self.assertEqual(0.0, self.policy.bonuses(10, level)[2])

    def test_craft_price_includes_both_specification_surcharges(self):
        calculate = self.policy.calculate_craft_cost
        self.assertEqual(100, calculate(False, False, None))
        self.assertEqual(100, calculate(False, False, -1))
        self.assertEqual(20, calculate(False, False, 1))
        self.assertEqual(40, calculate(True, False, 1))
        self.assertEqual(40, calculate(False, True, 1))
        self.assertEqual(60, calculate(True, True, 1))
        self.assertEqual(1440, calculate(True, True, 5))
        self.assertIsNone(calculate(False, False, 6))

    # ------------------------------------------------------- catalogue reads
    def test_catalogue_is_read_from_the_installed_client(self):
        self._with_cache()
        self.assertIsNotNone(self.policy.catalogue())
        self._with_cache(cache=None)
        self.assertIsNone(self.policy.catalogue())

    def test_out_of_range_definitions_are_dropped_not_trusted(self):
        broken = _Cache()
        broken.toys[900] = _Toy(900, 'garland', 1, 'soviet')
        broken.toys[900].settingID = 0
        broken.toys[901] = _Toy(901, 'garland', 9, 'soviet')
        broken.toys[901].settingID = 0
        cat = self.policy.catalogue(broken)
        self.assertNotIn(900, cat['toys'])
        self.assertNotIn(901, cat['toys'])
        self.assertEqual(len(self.cache.toys), len(cat['toys']))

    # ---------------------------------------------------------- wire shape
    def test_sync_payload_matches_the_client_contract(self):
        self._with_cache()
        state = self.policy.state({})
        # Slot 2 is a hanging slot, so the placed toy must be a hanging toy.
        slot = 2
        self.assertEqual('hanging', self.cat['slots'][slot]['type'])
        toy_id = next(candidate for candidate, toy in
                      sorted(self.cat['toys'].items())
                      if toy['type'] == 'hanging')
        self.policy.grant_toys(state, [toy_id], self.cat)
        self.policy.place_toy(state, slot, toy_id, self.cat)
        snapshot = {}
        self.policy.write(snapshot, state)
        payload = self.policy.sync_payload(snapshot)
        # ClientNewYear replaces __slots wholesale; NewYearController indexes
        # it by a slot id bounded only by the descriptor count.
        self.assertEqual(len(self.cache.slots), len(payload['slots']))
        self.assertEqual(toy_id, payload['slots'][slot])
        # ToyInventoryData(*data) needs exactly two items.
        for value in payload['inventoryToys'].values():
            self.assertEqual(2, len(value))
        # A placed toy is no longer loose inventory.
        self.assertEqual({}, payload['inventoryToys'])
        collected = payload['toyCollection']
        self.assertEqual(self.policy.TOY_COLLECTION_BYTES, len(collected))
        unpacked = [position * 8 + bit for position, byte in
                    enumerate(bytearray(collected)) for bit in range(8)
                    if byte & 1 << bit]
        self.assertEqual([toy_id], unpacked)
        self.assertEqual(1, payload['level'])

    def test_sync_payload_is_omitted_without_a_catalogue(self):
        """A short slot list would raise inside the stock lobby."""
        self._with_cache(cache=None)
        snapshot = {}
        self.policy.write(snapshot, self.policy.state({}))
        self.assertEqual({}, self.policy.sync_payload(snapshot))

    def test_published_toys_are_bounded_by_the_live_catalogue(self):
        """__onInventoryChanged indexes the client toy cache unguarded."""
        self._with_cache()
        snapshot = {'newYear': {'slots': [999999], 'inventory': {999998: 2},
                                'collection': [999997]}}
        payload = self.policy.sync_payload(snapshot)
        self.assertEqual({}, payload['inventoryToys'])
        self.assertEqual([-1] * len(self.cache.slots), payload['slots'])
        self.assertEqual(self.policy.TOY_COLLECTION_BYTES,
                         len(payload['toyCollection']))

    def test_tokens_carry_only_box_and_chest_identifiers(self):
        state = self.policy.state({})
        self.policy.add_containers(state, 'boxes',
                                   self.policy.RANDOM_BOX_ID, 2)
        self.policy.add_containers(state, 'chests', 'ny18:chest:3', 1)
        self.assertEqual(0, self.policy.add_containers(
            state, 'boxes', 'ny18:box:bogus', 1))
        snapshot = {}
        self.policy.write(snapshot, state)
        tokens = self.policy.token_payload(snapshot)
        self.assertEqual({'ny18:box:r': (self.policy.TOKEN_EXPIRY, 2),
                          'ny18:chest:3': (self.policy.TOKEN_EXPIRY, 1)},
                         tokens)
        for value in tokens.values():
            self.assertEqual(2, len(value))

    # ------------------------------------------------------------ settlement
    def _facts(self, damage=0, won=False, at=86400 * 20000 + 3600):
        return {'damage': damage, 'won': won, 'premature_leave': False,
                'finished_at': at}

    def test_offensive_missions_grant_fragments_and_containers_once(self):
        """Six good battles complete the day's three missions exactly once.

        Which three missions a day carries is a hash of the day, so the
        expected totals are derived from the selection instead of guessed.
        """
        snapshot = {}
        at = 86400 * 20000 + 3600
        day = self.policy.daily_state(snapshot, at)
        chosen = [self.policy.MISSION_BY_ID[key] for key in day['missions']]
        self.assertEqual(3, len(chosen))
        expected_fragments = sum(row[4] for row in chosen)
        expected_boxes = (sum(row[5] for row in chosen) +
                          self.policy.FIRST_WIN_BOXES)
        fragments = boxes = 0
        completed = set()
        for index in range(6):
            granted = self.policy.advance_battle(
                snapshot, self._facts(9000, True, at + index))
            fragments += granted['fragments']
            boxes += sum(granted['boxes'].values())
            completed.update(granted['missions'])
        self.assertEqual(set(day['missions']), completed)
        self.assertEqual(expected_fragments, fragments)
        self.assertEqual(expected_boxes, boxes)
        values = self.policy.state(snapshot)
        self.assertEqual(expected_fragments, values['fragments'])
        self.assertEqual(
            expected_boxes, values['boxes'][self.policy.RANDOM_BOX_ID])

    def test_a_seventh_battle_grants_nothing_new(self):
        snapshot = {}
        at = 86400 * 20000 + 3600
        for index in range(6):
            self.policy.advance_battle(
                snapshot, self._facts(9000, True, at + index))
        before = self.policy.state(snapshot)
        extra = self.policy.advance_battle(
            snapshot, self._facts(9000, True, at + 6))
        self.assertEqual([], extra['missions'])
        self.assertEqual(0, extra['fragments'])
        self.assertEqual({}, extra['boxes'])
        after = self.policy.state(snapshot)
        self.assertEqual(before['fragments'], after['fragments'])
        self.assertEqual(before['boxes'], after['boxes'])

    def test_premature_leave_and_training_grant_nothing(self):
        snapshot = {}
        facts = self._facts(5000, True)
        facts['premature_leave'] = True
        self.assertIsNone(self.policy.advance_battle(snapshot, facts))
        self.assertEqual({}, self.policy.state(snapshot)['boxes'])

    def test_ladder_turns_accumulated_fragments_into_chests(self):
        snapshot = {}
        state = self.policy.state(snapshot)
        self.policy.add_fragments(state, self.policy.LADDER_STEP * 3)
        granted = self.policy.ladder_chests(state)
        self.assertEqual(3, len(granted))
        self.assertEqual(3, state['ladder'])
        # Chests are capped at the release's own maximum level.
        state['fragments'] = self.policy.LADDER_STEP * 30
        self.policy.ladder_chests(state)
        self.assertEqual(30, state['ladder'])
        self.assertEqual(10, max(self.policy.CHEST_LEVEL_BY_ID[identifier]
                                 for identifier in state['chests']))

    def test_event_bonus_uses_the_decorated_hangar(self):
        self._with_cache()
        snapshot = {}
        state = self.policy.state(snapshot)
        # Fill every slot with rank-5 toys: far past the top atmosphere band.
        for index, slot in enumerate(self.cat['slots']):
            if slot is None:
                continue
            toy_id = next(
                candidate for candidate, toy in self.cat['toys'].items()
                if toy['rank'] == 5 and toy['type'] == slot['type'] and
                candidate not in state['slots'])
            self.policy.grant_toys(state, [toy_id], self.cat)
            self.policy.place_toy(state, index, toy_id, self.cat)
        self.policy.write(snapshot, state)
        self.assertEqual(10, self.policy.atmosphere_level(
            state['slots'], self.cat))
        bonus = self.policy.event_bonus_amounts(
            snapshot, {'xp': 1000, 'credits': 10000, 'free_xp': 100})
        self.assertEqual(120, bonus['xp'])
        self.assertEqual(120, bonus['crew_xp'])
        self.assertEqual(1200, bonus['credits'])
        # Free XP has no bonus row in this release, so no key is published.
        self.assertEqual(0, bonus.get('free_xp', 0))

    def test_event_bonus_is_zero_for_an_undecorated_hangar(self):
        self._with_cache()
        self.assertEqual({}, self.policy.event_bonus_amounts(
            {}, {'xp': 1000, 'credits': 10000}))

    # -------------------------------------------------------- garage actions
    def test_open_box_grants_toys_fragments_and_credits_once(self):
        self._with_cache()
        state = self._state({'gold': 0, 'credits': 0, 'crystal': 0,
                             'freeXP': 0})
        snapshot = state.snapshot()
        values = self.policy.state(snapshot)
        self.policy.add_containers(values, 'boxes', 'ny18:box:sp', 1)
        self.policy.write(snapshot, values)
        outcome = self.policy.transact(state, 'open_box', 'ny18:box:sp',
                                       rng=_Rng())
        self.assertEqual(1, len(outcome['toys']))
        self.assertEqual(5000, outcome['credits'])
        self.assertEqual(5000, state.snapshot()['wallet']['credits'])
        self.assertEqual({}, self.policy.state(state.snapshot())['boxes'])
        with self.assertRaises(self.garage.GarageError):
            self.policy.transact(state, 'open_box', 'ny18:box:sp', rng=_Rng())

    def test_open_box_without_a_catalogue_refuses_instead_of_inventing(self):
        self._with_cache(cache=None)
        state = self._state()
        snapshot = state.snapshot()
        values = self.policy.state(snapshot)
        self.policy.add_containers(values, 'boxes',
                                   self.policy.RANDOM_BOX_ID, 1)
        self.policy.write(snapshot, values)
        with self.assertRaises(self.garage.GarageError):
            self.policy.transact(state, 'open_box',
                                 self.policy.RANDOM_BOX_ID)
        # The refusal left the container owned and the wallet untouched.
        self.assertEqual(1, self.policy.state(
            state.snapshot())['boxes'][self.policy.RANDOM_BOX_ID])

    def test_chest_pays_by_its_own_level(self):
        self._with_cache()
        state = self._state()
        snapshot = state.snapshot()
        values = self.policy.state(snapshot)
        self.policy.add_containers(values, 'chests', 'ny18:chest:4', 1)
        self.policy.write(snapshot, values)
        outcome = self.policy.transact(state, 'open_chest', 'ny18:chest:4',
                                       rng=_Rng())
        self.assertEqual(4, outcome['level'])
        self.assertEqual(self.policy.CHEST_CREDITS_PER_LEVEL * 4,
                         outcome['credits'])
        self.assertEqual(2, outcome['level'] // 2)  # rank 1 + (4-1)//3
        self.assertEqual(1, len(outcome['toys']))
        self.assertEqual(self.policy.CHEST_CREDITS_PER_LEVEL * 4,
                         state.snapshot()['wallet']['credits'])

    def test_placing_a_toy_moves_it_between_inventory_and_slot(self):
        self._with_cache()
        state = self._state()
        snapshot = state.snapshot()
        values = self.policy.state(snapshot)
        garland = next(toy_id for toy_id, toy in self.cat['toys'].items()
                       if toy['type'] == 'garland')
        self.policy.grant_toys(values, [garland], self.cat)
        self.policy.write(snapshot, values)
        # Slot 2 is a hanging slot, so a garland must be refused.
        with self.assertRaises(self.garage.GarageError):
            self.policy.transact(state, 'place_toy', '2:%d' % garland)
        garland_slot = next(index for index, slot in
                            enumerate(self.cat['slots'])
                            if slot is not None and slot['type'] == 'garland')
        outcome = self.policy.transact(
            state, 'place_toy', '%d:%d' % (garland_slot, garland))
        self.assertEqual(garland, outcome['toy'])
        after = self.policy.state(state.snapshot())
        self.assertEqual(garland, after['slots'][garland_slot])
        self.assertEqual({}, after['inventory'])
        # Clearing the slot returns the toy to the inventory.
        self.policy.transact(state, 'place_toy', '%d:-1' % garland_slot)
        after = self.policy.state(state.snapshot())
        self.assertEqual(-1, after['slots'][garland_slot])
        self.assertEqual(1, after['inventory'][garland])

    def test_craft_spends_fragments_and_break_returns_them(self):
        self._with_cache()
        state = self._state()
        snapshot = state.snapshot()
        values = self.policy.state(snapshot)
        self.policy.add_fragments(values, 100)
        self.policy.write(snapshot, values)
        outcome = self.policy.transact(state, 'craft_toy', '::', rng=_Rng())
        self.assertEqual(100, outcome['cost'])
        self.assertEqual(1, len(outcome['toys']))
        self.assertEqual(0, self.policy.state(state.snapshot())['fragments'])
        toy_id = outcome['toys'][0]
        rank = self.cat['toys'][toy_id]['rank']
        self.policy.transact(
            state, 'break_toys', json.dumps([[toy_id, 1]]))
        self.assertEqual(self.policy.CRAFT_FRAGMENTS_BY_RANK[rank - 1],
                         self.policy.state(state.snapshot())['fragments'])

    def test_craft_refuses_without_fragments(self):
        self._with_cache()
        state = self._state()
        self.policy.write(state.snapshot(), self.policy.state({}))
        with self.assertRaises(self.garage.GarageError):
            self.policy.transact(state, 'craft_toy', '::', rng=_Rng())

    def test_event_state_survives_a_save_round_trip(self):
        self._with_cache()
        store = garage_fixture._load('garage_store')
        state = self._state()
        snapshot = state.snapshot()
        values = self.policy.state(snapshot)
        toy_id = sorted(self.cat['toys'])[0]
        self.policy.grant_toys(values, [toy_id], self.cat)
        self.policy.add_fragments(values, 42)
        self.policy.add_containers(values, 'boxes',
                                   self.policy.RANDOM_BOX_ID, 2)
        self.policy.write(snapshot, values)
        ledger = store._ledger_payload(snapshot)
        saved = ledger['newYear']
        self.assertEqual(42, saved['fragments'])
        restored = {}
        restored.update(self.policy.restored_fields(saved))
        self.assertEqual(42, self.policy.state(restored)['fragments'])
        self.assertEqual(2, self.policy.state(restored)['boxes'][
            self.policy.RANDOM_BOX_ID])
        self.assertEqual([toy_id], self.policy.state(restored)['collection'])

    def test_a_saved_ledger_round_trips_through_the_store_restore(self):
        """The garage restore path rebuilds the event section from the save."""
        self._with_cache()
        store = garage_fixture._load('garage_store')
        state = self._state()
        snapshot = state.snapshot()
        values = self.policy.state(snapshot)
        self.policy.add_fragments(values, 7)
        self.policy.add_containers(values, 'chests', 'ny18:chest:2', 1)
        self.policy.write(snapshot, values)
        ledger = store._ledger_payload(snapshot)
        staged = {'wallet': {'credits': 0, 'gold': 0, 'crystal': 0,
                             'freeXP': 0}}
        # _apply_ledger reads the ledger out of the whole saved payload.
        self.assertTrue(store._apply_ledger(staged, {'ledger': ledger}))
        after = self.policy.state(staged)
        self.assertEqual(7, after['fragments'])
        self.assertEqual(1, after['chests']['ny18:chest:2'])


class NewYearCommandWiringTests(unittest.TestCase):
    def test_the_event_command_is_registered_with_its_own_id(self):
        requests, commands, unused_garage = garage_fixture._request_modules()
        self.assertEqual(19901, commands.CMD_NEW_YEAR_SERVICE)
        self.assertIn(commands.CMD_NEW_YEAR_SERVICE, requests.HANDLERS)
        # The stock 0.9.22 event commands stay unclaimed: their client-side
        # validator refuses everything this fork does not publish.
        for stock in range(20161, 20170):
            self.assertNotIn(stock, requests.HANDLERS)

    def test_a_malformed_request_is_refused_without_touching_the_garage(self):
        requests, commands, unused_garage = garage_fixture._request_modules()
        result = requests.HANDLERS[commands.CMD_NEW_YEAR_SERVICE](
            {'garage': None}, ['not json'])
        self.assertEqual(commands.RES_FAILURE, result.result_id)


if __name__ == '__main__':
    unittest.main()
