"""New Year Offensive panel: rendering, cursor rules and the garage requests."""

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

from test_port_0922_new_year_event import _Cache


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = (ROOT / 'src' / 'res' / 'scripts' /
                'client' / 'gui' / 'mods' / 'offline_lan_0922')


def _install_package_modules():
    for name in ('gui', 'gui.mods', 'gui.mods.offline_lan_0922'):
        if name not in sys.modules:
            module = types.ModuleType(name)
            module.__path__ = [str(PACKAGE_ROOT)]
            sys.modules[name] = module


def _load(name):
    _install_package_modules()
    full_name = 'gui.mods.offline_lan_0922.' + name
    sys.modules.pop(full_name, None)
    spec = importlib.util.spec_from_file_location(
        full_name, PACKAGE_ROOT / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module


class _Component(object):
    """A fake native component that rejects properties this client lacks."""

    ALLOWED = frozenset((
        'horizontalPositionMode', 'verticalPositionMode', 'widthMode',
        'heightMode', 'horizontalAnchor', 'verticalAnchor', 'position',
        'width', 'height', 'materialFX', 'colour', 'focus', 'moveFocus',
        'mouseButtonFocus', 'crossFocus', 'visible', 'script', 'text', 'font',
        'multiline'))

    def __init__(self, kind, texture=None, text=''):
        object.__setattr__(self, 'kind', kind)
        object.__setattr__(self, 'texture', texture)
        object.__setattr__(self, 'children', [])
        object.__setattr__(self, 'properties', {'text': text})

    def __setattr__(self, name, value):
        if name not in self.ALLOWED:
            raise AttributeError(name)
        self.properties[name] = value

    def __getattr__(self, name):
        try:
            return self.properties[name]
        except KeyError:
            raise AttributeError(name)

    def addChild(self, component):
        self.children.append(component)

    def delChild(self, component):
        if component in self.children:
            self.children.remove(component)


class _Surface(object):
    def __init__(self):
        self.roots = []
        self.resorts = 0

    def window(self):
        return _Component('window', '')

    def simple(self, texture=''):
        return _Component('simple', texture)

    def text(self):
        return _Component('text', text='')

    def add_root(self, component):
        self.roots.append(component)

    def remove_root(self, component):
        if component in self.roots:
            self.roots.remove(component)

    def resort(self):
        self.resorts += 1

    def cursor_position(self):
        return (0.0, 0.0)

    def show_cursor(self):
        return True

    def hide_cursor(self):
        return True

    def tick(self, delay, function):
        return 1

    def cancel_tick(self, handle):
        return None


class _ServicePanelStub(object):
    """Stands in for the offline daily-mission page this button joins."""

    def __init__(self):
        self._controls = {}
        self._labels = {}
        self._open = True
        self.activated = []

    def install(self):
        self._make_control('daily', (0.0, 0.0, 0.0), 0.5, 0.1)
        self._make_label('daily', 'DAILY', (0.0, 0.0, 0.0), 0.5, 0.1)
        return True

    def activate(self, role):
        self.activated.append(role)
        return True

    def _make_control(self, role, position, width, height):
        self._controls[role] = _Component('simple')
        return self._controls[role]

    def _make_label(self, role, text, position, width, height,
                    anchor='LEFT', colour=(255, 255, 255, 255)):
        self._labels[role] = _Component('text', text=text)
        return self._labels[role]


class NewYearPanelTests(unittest.TestCase):
    """The panel renders the policy's own numbers and issues real requests."""

    def setUp(self):
        self.module = _load('new_year_hangar_ui')
        self.policy = self.module.policy
        self.cache = _Cache()
        self.cat = self.policy.catalogue(self.cache)
        self.assertIsNotNone(self.cat)
        self.snapshot = {}
        self.requests = []
        self.messages = []
        for target, replacement in (
                ('client_cache', lambda: self.cache),
        ):
            scope = mock.patch.object(self.policy, target, replacement)
            scope.start()
            self.addCleanup(scope.stop)
        snapshot_scope = mock.patch.object(
            self.module, 'snapshot', lambda: self.snapshot)
        snapshot_scope.start()
        self.addCleanup(snapshot_scope.stop)
        request_scope = mock.patch.object(self.module, 'request', self._request)
        request_scope.start()
        self.addCleanup(request_scope.stop)
        system_messages = types.SimpleNamespace(
            pushMessage=lambda text, type=None: self.messages.append(text),
            SM_TYPE=types.SimpleNamespace(Information=0, Error=1))
        gui_module = sys.modules['gui']
        message_scope = mock.patch.object(
            gui_module, 'SystemMessages', system_messages, create=True)
        message_scope.start()
        self.addCleanup(message_scope.stop)
        self.surface = _Surface()
        self.closed = []
        self.panel = self.module.NewYearPanel(
            on_close=lambda: self.closed.append(1), surface=self.surface)

    def _request(self, action, key, callback=None):
        self.requests.append((action, key))
        if callable(callback):
            callback(True, '')

    def _text(self, role):
        return self.panel._labels[role].properties['text']

    def _grant(self, **kwargs):
        values = self.policy.state(self.snapshot)
        for name, value in kwargs.items():
            getattr(self.policy, 'add_' + name)(values, value) \
                if name in ('fragments',) else None
        self.policy.write(self.snapshot, values)
        return values

    # ------------------------------------------------------------- rendering
    def test_install_builds_the_panel_hidden(self):
        self.assertTrue(self.panel.install())
        # Nothing is painted until the player opens it.
        self.assertFalse(self._text('title') == '' or
                         self.panel._labels['title'].properties['visible'])
        for role in ('slot_previous', 'slot_next', 'toy_previous', 'toy_next',
                     'place', 'clear', 'open_box', 'open_chest', 'craft',
                     'close'):
            self.assertIn(role, self.panel._controls)

    def test_open_publishes_the_decorated_hangar_numbers(self):
        self.assertTrue(self.panel.open())
        toy_id = next(candidate for candidate, toy in
                      sorted(self.cat['toys'].items())
                      if toy['rank'] == 5 and toy['type'] == 'top')
        values = self.policy.state(self.snapshot)
        self.policy.grant_toys(values, [toy_id], self.cat)
        self.policy.place_toy(values, 0, toy_id, self.cat)
        self.policy.add_fragments(values, 42)
        self.policy.write(self.snapshot, values)
        self.assertTrue(self.panel.refresh())
        # One rank-5 toy is 25 atmosphere, which is level 3 of the release.
        self.assertIn('3', self._text('atmosphere'))
        self.assertIn('42', self._text('fragments'))
        self.assertIn('1', self._text('collection'))

    def test_a_client_without_definitions_says_so(self):
        scope = mock.patch.object(self.policy, 'client_cache', lambda: None)
        scope.start()
        self.addCleanup(scope.stop)
        self.assertTrue(self.panel.open())
        self.assertIn('no New Year definitions', self._text('atmosphere'))
        # The numeric rows are cleared rather than left showing a stale hangar.
        self.assertEqual('', self._text('fragments'))

    # ---------------------------------------------------------- the cursors
    def test_the_slot_cursor_clamps_to_the_installed_slot_count(self):
        self.assertTrue(self.panel.open())
        slots = len(self.cat['slots'])
        for unused in range(slots + 7):
            self.assertTrue(self.panel.activate('slot_next'))
        self.assertEqual(slots - 1, self.panel._slot)
        for unused in range(slots + 7):
            self.assertTrue(self.panel.activate('slot_previous'))
        self.assertEqual(0, self.panel._slot)

    def test_the_toy_cursor_wraps_only_within_the_owned_toys(self):
        self.assertTrue(self.panel.open())
        values = self.policy.state(self.snapshot)
        owned = sorted(candidate for candidate, toy in
                       sorted(self.cat['toys'].items())
                       if toy['type'] == self.cat['slots'][0]['type'])[:3]
        self.policy.grant_toys(values, owned, self.cat)
        self.policy.write(self.snapshot, values)
        self.assertTrue(self.panel.activate('toy_next'))
        self.assertEqual(1, self.panel._toy_index)
        self.assertTrue(self.panel.activate('toy_previous'))
        self.assertTrue(self.panel.activate('toy_previous'))
        self.assertEqual(2, self.panel._toy_index)

    def test_the_toy_cursor_does_not_move_without_an_owned_toy(self):
        self.assertTrue(self.panel.open())
        self.assertFalse(self.panel.activate('toy_next'))
        self.assertEqual(0, self.panel._toy_index)

    # ------------------------------------------------------ garage requests
    def test_place_issues_the_selected_slot_and_a_matching_toy(self):
        self.assertTrue(self.panel.open())
        # Slot 2 is a hanging slot, so only a hanging toy may be offered.
        self.panel._slot = 2
        self.assertEqual('hanging', self.cat['slots'][2]['type'])
        toy_id = next(candidate for candidate, toy in
                      sorted(self.cat['toys'].items())
                      if toy['type'] == 'hanging')
        values = self.policy.state(self.snapshot)
        self.policy.grant_toys(values, [toy_id], self.cat)
        self.policy.write(self.snapshot, values)
        self.assertTrue(self.panel.activate('place'))
        self.assertEqual([('place_toy', '2:%d' % toy_id)], self.requests)

    def test_place_refuses_without_a_toy_of_that_type(self):
        self.assertTrue(self.panel.open())
        self.assertFalse(self.panel.activate('place'))
        self.assertEqual([], self.requests)

    def test_clear_issues_an_empty_slot_request(self):
        self.assertTrue(self.panel.open())
        self.panel._slot = 3
        self.assertTrue(self.panel.activate('clear'))
        self.assertEqual([('place_toy', '3:-1')], self.requests)

    def test_open_box_refuses_until_the_account_owns_a_box(self):
        self.assertTrue(self.panel.open())
        self.assertFalse(self.panel.activate('open_box'))
        self.assertEqual([], self.requests)
        values = self.policy.state(self.snapshot)
        self.policy.add_containers(
            values, 'boxes', self.policy.RANDOM_BOX_ID, 2)
        self.policy.write(self.snapshot, values)
        self.assertTrue(self.panel.activate('open_box'))
        self.assertEqual([('open_box', self.policy.RANDOM_BOX_ID)],
                         self.requests)

    def test_open_chest_refuses_until_the_account_owns_a_chest(self):
        self.assertTrue(self.panel.open())
        self.assertFalse(self.panel.activate('open_chest'))
        values = self.policy.state(self.snapshot)
        self.policy.add_containers(values, 'chests', 'ny18:chest:2', 1)
        self.policy.write(self.snapshot, values)
        self.assertTrue(self.panel.activate('open_chest'))
        self.assertEqual([('open_chest', 'ny18:chest:2')], self.requests)

    def test_craft_issues_the_random_toy_request(self):
        self.assertTrue(self.panel.open())
        self.assertTrue(self.panel.activate('craft'))
        self.assertEqual([('craft_toy', '::')], self.requests)

    def test_close_runs_the_caller_callback(self):
        self.assertTrue(self.panel.open())
        self.assertTrue(self.panel.activate('close'))
        self.assertEqual([1], self.closed)

    def test_a_closed_panel_ignores_every_action(self):
        self.assertFalse(self.panel.activate('craft'))
        self.assertFalse(self.panel.activate('close'))
        self.assertEqual([], self.requests)
        self.assertEqual([], self.closed)

    def test_a_failed_action_does_not_leave_the_panel_busy(self):
        self.assertTrue(self.panel.open())

        def refused(action, key, callback=None):
            self.requests.append((action, key))
            if callable(callback):
                callback(False, 'NEW_YEAR_NOT_ENOUGH_FRAGMENTS')

        scope = mock.patch.object(self.module, 'request', refused)
        scope.start()
        self.addCleanup(scope.stop)
        self.assertTrue(self.panel.activate('craft'))
        self.assertTrue(self.panel.activate('craft'))
        self.assertEqual(2, len(self.requests))

    # ----------------------------------------------------------- the receipt
    def test_notify_names_the_missions_containers_and_fragments(self):
        granted = {'missions': ['ny_battles_3'],
                   'boxes': {self.policy.RANDOM_BOX_ID: 2},
                   'chests': {'ny18:chest:2': 1}, 'fragments': 45}
        self.assertTrue(self.module.notify(granted))
        message = self.messages[-1]
        self.assertIn('45', message)
        self.assertIn('x2', message)
        self.assertIn('level 2', message)

    def test_notify_stays_silent_for_a_battle_that_granted_nothing(self):
        self.assertFalse(self.module.notify(
            {'missions': [], 'boxes': {}, 'chests': {}, 'fragments': 0}))
        self.assertEqual([], self.messages)

    def test_notify_refuses_a_payload_it_does_not_understand(self):
        self.assertFalse(self.module.notify(None))
        self.assertFalse(self.module.notify('fragments'))
        self.assertEqual([], self.messages)

    def test_reward_lines_name_the_toys_from_their_own_definitions(self):
        toy_id = sorted(self.cat['toys'])[0]
        lines = self.module.reward_lines({'toys': [toy_id], 'fragments': 7})
        self.assertEqual(2, len(lines))
        # The label is built from type, rank and setting, never a locale row.
        self.assertIn('I', lines[0])
        self.assertIn('7', lines[1])


class NewYearButtonTests(unittest.TestCase):
    """install() adds exactly one button to the offline daily-mission page."""

    def setUp(self):
        self.module = _load('new_year_hangar_ui')
        # install() imports the daily-mission page lazily, so it has to be
        # loaded here before the class it patches can be swapped out.
        self.services_ui = _load('offline_services_ui')
        self.original = self.services_ui.ServicePanel
        self.services_ui.ServicePanel = _ServicePanelStub
        self.addCleanup(setattr, self.services_ui, 'ServicePanel',
                        self.original)
        self.addCleanup(self.module.uninstall)

    def test_install_adds_one_button_to_the_daily_mission_page(self):
        self.module.install()
        panel = _ServicePanelStub()
        self.assertTrue(panel.install())
        self.assertIn('daily', panel._controls)
        self.assertIn('new_year', panel._controls)
        self.assertIn('new_year', panel._labels)
        # A second install must not duplicate the button.
        self.assertTrue(panel.install())
        self.assertEqual(2, len(panel._controls))

    def test_uninstall_restores_the_daily_mission_page(self):
        self.module.install()
        self.module.uninstall()
        panel = _ServicePanelStub()
        panel.install()
        self.assertNotIn('new_year', panel._controls)
        self.assertIn('daily', panel._controls)

    def test_the_event_button_routes_to_the_event_panel(self):
        opened = []
        scope = mock.patch.object(
            self.module, 'open_panel', lambda: opened.append(1))
        scope.start()
        self.addCleanup(scope.stop)
        self.module.install()
        panel = _ServicePanelStub()
        panel.install()
        self.assertTrue(panel.activate('new_year'))
        self.assertEqual([1], opened)
        # Every other role still reaches the page's own handler.
        self.assertTrue(panel.activate('daily'))
        self.assertEqual(['daily'], panel.activated)

    def test_the_event_button_is_ignored_on_a_closed_page(self):
        opened = []
        scope = mock.patch.object(
            self.module, 'open_panel', lambda: opened.append(1))
        scope.start()
        self.addCleanup(scope.stop)
        self.module.install()
        panel = _ServicePanelStub()
        panel.install()
        panel._open = False
        self.assertTrue(panel.activate('new_year'))
        self.assertEqual([], opened)
        self.assertEqual(['new_year'], panel.activated)


if __name__ == '__main__':
    unittest.main()
