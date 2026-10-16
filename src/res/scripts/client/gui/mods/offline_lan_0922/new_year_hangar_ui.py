"""New Year Offensive panel and receipts for the offline hangar.

Built on the exact native surface the LAN waiting room proved on #1513:
``GUI.Window`` / ``GUI.Simple`` / ``GUI.Text`` carrying
``system/maps/col_white.dds`` and ``system/maps/col_black.dds``,
``GUI.addRoot`` / ``delRoot`` / ``reSort``, an overlay at ``z 0.1`` with
``focus`` and ``moveFocus``, and the mouse script methods ``ChainView`` uses.
No asset ships with this module and no stock view is replaced: the panel is
one more root over the hangar and is removed again on close.

Entry point: the offline daily-mission page already owns a native panel with
this exact lifecycle, so this module adds one button to it instead of claiming
a second stock window.  Installing and uninstalling only add and remove that
button.

Toy labels are generated from the definition fields the client actually parses
(``_readToy`` reads id, type, rank and setting, and nothing else), so this
panel never depends on a New Year locale table the regional build may not ship.
"""

import json
import time

from gui.mods.offline_lan_0922 import new_year_event as policy
from gui.mods.offline_lan_0922.ui_i18n import as_text, tr
from gui.mods.offline_lan_0922.waiting_room_ui import (
    WaitingRoomUI, NativeSurface, _ControlScript, CONTROL_Z,
    CONTROL_TEXT_COLOUR, OUTLINE_TEXTURE)

_panels = []
_patches = []


def _log(message):
    import sys
    sys.stdout.write('[Offline LAN 0.9.22] %s\n' % message)


def snapshot():
    from gui.mods.offline_lan_0922.compat import g_compatibility
    state = g_compatibility.garage_state()
    return state.snapshot() if state is not None else {}


def request(action, key, callback=None):
    """Send one event action over the mod's own string-request mailbox."""
    import BigWorld
    from gui.mods.offline_lan_0922.account_rpc.commands import (
        CMD_NEW_YEAR_SERVICE)
    account = BigWorld.player()

    def complete(unused_request, result, error, *unused):
        if BigWorld.player() is not account:
            return
        success = result >= 0
        message = tr(error) if error else ''
        try:
            if success:
                from gui import SystemMessages
                SystemMessages.pushMessage(
                    tr('New Year Offensive: action applied.'),
                    type=SystemMessages.SM_TYPE.Information)
            elif message:
                from gui import SystemMessages
                SystemMessages.pushMessage(message,
                                           type=SystemMessages.SM_TYPE.Error)
        finally:
            if callable(callback):
                callback(success, message)

    account._doCmdStr(CMD_NEW_YEAR_SERVICE,
                      json.dumps({'action': action, 'key': key}), complete)


def toy_label(cat, toy_id):
    """Name one toy from the fields the client's own reader parses."""
    toy = (cat or {}).get('toys', {}).get(int(toy_id))
    if toy is None:
        return tr('Empty')
    return tr('%s %s (%s)') % (
        tr(_TYPE_LABELS.get(toy['type'], toy['type'])),
        _ROMAN[min(max(toy['rank'], 1), 5) - 1],
        tr(_SETTING_LABELS.get(toy['setting'], toy['setting'])))


_TYPE_LABELS = {
    'top': 'Tree top', 'hanging': 'Hanging toy', 'garland': 'Garland',
    'gift': 'Gift', 'snowman': 'Snowman',
    'house_decoration': 'House decoration', 'house_lamp': 'House lamp',
    'street_garland': 'Street garland',
}
_SETTING_LABELS = {
    'soviet': 'Soviet', 'traditionalWestern': 'Traditional',
    'modernWestern': 'Modern', 'asian': 'Asian',
}
_ROMAN = ('I', 'II', 'III', 'IV', 'V')


def _containers_text(values):
    parts = []
    boxes = sum(values.get('boxes', {}).values())
    chests = sum(values.get('chests', {}).values())
    if boxes:
        parts.append(tr('Boxes: %d') % boxes)
    if chests:
        parts.append(tr('Chests: %d') % chests)
    return u' | '.join(parts) if parts else tr('No containers yet.')


def reward_lines(granted):
    """Describe what one action actually granted, from its own receipt row."""
    if not isinstance(granted, dict):
        return []
    cat = policy.catalogue()
    lines = []
    toys = granted.get('toys') or ()
    if toys:
        lines.append(tr('Toys: %s') % u', '.join(
            toy_label(cat, toy_id) for toy_id in toys))
    if granted.get('fragments'):
        lines.append(tr('Fragments: %d') % int(granted['fragments']))
    if granted.get('credits'):
        lines.append(tr('credits: %d') % int(granted['credits']))
    for identifier, count in sorted((granted.get('chests') or {}).items()):
        lines.append(tr('Chest %s x%d') % (as_text(identifier), int(count)))
    return lines


def notify(granted):
    """Publish one native message for a settled battle's event rewards."""
    if not isinstance(granted, dict):
        return False
    cat = policy.catalogue()
    lines = []
    for key in granted.get('missions') or ():
        row = policy.MISSION_BY_ID.get(key)
        if row is not None:
            label = tr(row[1])
            lines.append(tr('Offensive mission completed: %s.') %
                         (label % row[3] if '%d' in label else label))
    for identifier, count in sorted((granted.get('boxes') or {}).items()):
        lines.append(tr('New Year box received x%d') % int(count))
    for identifier, count in sorted((granted.get('chests') or {}).items()):
        level = policy.CHEST_LEVEL_BY_ID.get(identifier, 0)
        lines.append(tr('New Year chest received: level %d x%d') %
                     (int(level), int(count)))
    if granted.get('fragments'):
        lines.append(tr('Fragments: %d') % int(granted['fragments']))
    if not lines:
        return False
    from gui import SystemMessages
    SystemMessages.pushMessage(u'\n'.join(lines),
                               type=SystemMessages.SM_TYPE.Information)
    return True


class _EventControl(_ControlScript):
    def handleKeyEvent(self, event):
        import Keys
        if event.key == Keys.KEY_ESCAPE and event.isKeyDown():
            self._room.activate('close')
            return True
        return False


class _EventSurface(NativeSurface):
    def window(self):
        # The waiting room proved an untextured window draws nothing and a
        # white one would blank the hangar, so the panel uses the black map.
        return self._gui.Window(OUTLINE_TEXTURE)


class NewYearPanel(WaitingRoomUI):
    """Decorations and lootboxes on one native page over the hangar."""

    def __init__(self, on_close=None, surface=None):
        WaitingRoomUI.__init__(self, lambda unused: False, lambda: (),
                               on_close=on_close,
                               surface=surface or _EventSurface())
        self._last_paint = 0
        self._slot = 0
        self._toy_index = 0
        self._busy = False

    def install(self):
        if self._panel is not None:
            return True
        WaitingRoomUI.install(self)
        for component in (list(self._controls.values()) +
                          list(self._labels.values())):
            self._panel.delChild(component)
        self._controls, self._labels = {}, {}
        self._panel.script = _EventControl(self, None)
        self._make_label('title', tr('NEW YEAR OFFENSIVE'),
                         (-0.89, 0.84, 0), 1.8, 0.12)
        for role, y in (('atmosphere', 0.64), ('collection', 0.48),
                        ('fragments', 0.32), ('containers', 0.16),
                        ('slot', -0.04), ('toy', -0.20)):
            self._make_label(role, '', (-0.89, y, 0), 1.8, 0.13)
        self._make_label('note', tr('Placed toys decorate this hangar and pay '
                                    'an experience and credits bonus.'),
                         (-0.89, -0.40, 0), 1.8, 0.13)
        self._make_label('status', '', (-0.89, -0.56, 0), 1.8, 0.13)
        for role, label, x in (
                ('slot_previous', '<', -0.80), ('slot_next', '>', -0.58),
                ('toy_previous', '<', -0.28), ('toy_next', '>', -0.06),
                ('place', tr('PLACE'), 0.24), ('clear', tr('CLEAR'), 0.52),
                ('open_box', tr('OPEN BOX'), 0.80)):
            self._make_control(role, (x, -0.76, CONTROL_Z), 0.20, 0.14)
            self._make_label(role, label, (x, -0.76, 0), 0.20, 0.12,
                             anchor='CENTER', colour=CONTROL_TEXT_COLOUR)
            self._controls[role].script = _EventControl(self, role)
        for role, label, x in (('open_chest', tr('OPEN CHEST'), -0.55),
                               ('craft', tr('CRAFT'), -0.05),
                               ('close', tr('CLOSE'), 0.55)):
            self._make_control(role, (x, -0.92, CONTROL_Z), 0.42, 0.14)
            self._make_label(role, label, (x, -0.92, 0), 0.42, 0.12,
                             anchor='CENTER', colour=CONTROL_TEXT_COLOUR)
            self._controls[role].script = _EventControl(self, role)
        return True

    def _sync_selection(self):
        return ()

    def _move_pointer(self):
        WaitingRoomUI._move_pointer(self)
        stamp = int(time.time())
        if self._open and self._last_paint != stamp:
            self._last_paint = stamp
            self.refresh()

    def _selection(self):
        """Return the catalogue, state, slot descriptor and candidate toys."""
        cat = policy.catalogue()
        values = policy.filtered_state(snapshot(), cat)
        slots = (cat or {}).get('slots') or []
        if not slots:
            return cat, values, None, []
        self._slot = min(max(self._slot, 0), len(slots) - 1)
        slot = slots[self._slot]
        if slot is None:
            return cat, values, None, []
        owned = sorted(toy_id for toy_id, count in values['inventory'].items()
                       if count > 0 and
                       cat['toys'][toy_id]['type'] == slot['type'])
        if owned:
            self._toy_index = min(max(self._toy_index, 0), len(owned) - 1)
        else:
            self._toy_index = 0
        return cat, values, slot, owned

    def _refresh_contents(self):
        self._apply_layout()
        cat, values, slot, owned = self._selection()
        if cat is None:
            self._set_text('atmosphere', tr(
                'The installed client has no New Year definitions.'))
            for role in ('collection', 'fragments', 'containers', 'slot',
                         'toy', 'status'):
                self._set_text(role, '')
        else:
            atmosphere = policy.atmosphere_level(values['slots'], cat)
            current, bound = policy.atmosphere_progress(values['slots'], cat)
            self._set_text('atmosphere', tr('Atmosphere: level %d of %d (%d/%d)')
                           % (atmosphere, policy.MAX_ATMOSPHERE_LEVEL,
                              current, bound))
            levels = policy.collection_levels(values['collection'], cat)
            self._set_text('collection', tr('Collection: %s | collected %d of %d')
                           % (u'/'.join(str(level) for level in levels),
                              len(values['collection']), len(cat['toys'])))
            self._set_text('fragments', tr('Fragments: %d') %
                           values['fragments'])
            self._set_text('containers', _containers_text(values))
            if slot is None:
                self._set_text('slot', tr('No decoration slot here.'))
                self._set_text('toy', '')
            else:
                placed = (values['slots'][self._slot]
                          if self._slot < len(values['slots']) else -1)
                self._set_text('slot', tr('Slot %d of %d: %s | placed: %s') % (
                    self._slot + 1, len(cat['slots']),
                    tr(_TYPE_LABELS.get(slot['type'], slot['type'])),
                    toy_label(cat, placed)))
                if owned:
                    self._set_text('toy', tr('To place: %s (owned %d)') % (
                        toy_label(cat, owned[self._toy_index]),
                        values['inventory'][owned[self._toy_index]]))
                else:
                    self._set_text('toy', tr(
                        'No owned toy of this type. Open a box or craft one.'))
            cost = policy.calculate_craft_cost(False, False, -1)
            self._set_text('status', tr('Craft a random toy for %d fragments.')
                           % cost)
        for component in (list(self._controls.values()) +
                          list(self._labels.values())):
            self._set(component, 'visible', True)
        self._set(self._panel, 'visible', True)
        self._paint()
        return True

    def _run(self, action, key):
        if self._busy:
            return False
        self._busy = True

        def done(success, message):
            self._busy = False
            if success:
                self.refresh()

        request(action, key, done)
        return True

    def activate(self, role):
        if not self._open:
            return False
        if role == 'close':
            self.close()
            if callable(self._on_close):
                self._on_close()
            return True
        cat, values, slot, owned = self._selection()
        if role == 'slot_previous':
            self._slot = max(0, self._slot - 1)
            self._toy_index = 0
            return self.refresh()
        if role == 'slot_next':
            self._slot = min(len((cat or {}).get('slots') or []) - 1,
                             self._slot + 1)
            self._toy_index = 0
            return self.refresh()
        if role == 'toy_previous' and owned:
            self._toy_index = (self._toy_index - 1) % len(owned)
            return self.refresh()
        if role == 'toy_next' and owned:
            self._toy_index = (self._toy_index + 1) % len(owned)
            return self.refresh()
        if role == 'place':
            if not owned:
                return False
            return self._run('place_toy', '%d:%d' % (
                self._slot, owned[self._toy_index]))
        if role == 'clear':
            return self._run('place_toy', '%d:%d' % (self._slot, -1))
        if role == 'open_box':
            identifier = sorted(values['boxes'])[0] if values['boxes'] else None
            if identifier is None:
                return False
            return self._run('open_box', identifier)
        if role == 'open_chest':
            identifier = sorted(values['chests'])[0] if values['chests'] else None
            if identifier is None:
                return False
            return self._run('open_chest', identifier)
        if role == 'craft':
            return self._run('craft_toy', '::')
        return False


def close_panel():
    for panel in list(_panels):
        try:
            panel.uninstall()
        except Exception as error:
            _log('New Year panel uninstall failed: %s' % error)
        _panels.remove(panel)


def install():
    """Add the event's one button to the offline daily-mission page."""
    from gui.mods.offline_lan_0922 import offline_services_ui

    def install_panel(panel):
        _original_install(panel)
        if 'new_year' in panel._controls:
            return True
        panel._make_control('new_year', (0.05, -0.85, CONTROL_Z), 0.75, 0.16)
        panel._make_label('new_year', tr('NEW YEAR OFFENSIVE'),
                          (0.05, -0.85, 0), 0.75, 0.14, anchor='CENTER',
                          colour=CONTROL_TEXT_COLOUR)
        panel._controls['new_year'].script = offline_services_ui._ServiceControl(
            panel, 'new_year')
        return True

    _original_install = offline_services_ui.ServicePanel.install
    _patches.append((offline_services_ui.ServicePanel, 'install',
                     _original_install))
    offline_services_ui.ServicePanel.install = install_panel

    original_activate = offline_services_ui.ServicePanel.activate
    _patches.append((offline_services_ui.ServicePanel, 'activate',
                     original_activate))

    def activate(panel, role):
        if role == 'new_year' and panel._open:
            open_panel()
            return True
        return original_activate(panel, role)

    offline_services_ui.ServicePanel.activate = activate


def uninstall():
    close_panel()
    while _patches:
        owner, name, original = _patches.pop()
        setattr(owner, name, original)


def open_panel():
    panel = NewYearPanel()
    _panels.append(panel)
    if not panel.open():
        _panels.remove(panel)
        panel.uninstall()
        from gui import SystemMessages
        SystemMessages.pushMessage(
            tr('The New Year panel could not open.'),
            type=SystemMessages.SM_TYPE.Error)
        return None
    return panel
