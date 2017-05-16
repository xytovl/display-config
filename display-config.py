#!/usr/bin/python3
import argparse
import collections
import copy
from gi.repository import Gio, GLib
import sys

Crtc = collections.namedtuple('Crtc',
    ['id', 'winsys_id',
        'x', 'y',
        'width', 'height',
        'current_mode',
        'current_transform',
        'transforms',
        'properties'])

Output = collections.namedtuple('Output',
        ['id', 'winsys_id',
            'current_crtc',
            'possible_crtcs',
            'name',
            'modes',
            'clones',
            'properties'])

CrtcConfiguration = collections.namedtuple('CrtcConfiguration',
        ['id',
            'new_mode',
            'x', 'y',
            'transform',
            'outputs',
            'properties'])

OutputConfiguration = collections.namedtuple('OutputConfiguration',
        ['id', 'properties'])

Mode = collections.namedtuple('Mode',
        ['id', 'winsys_id', 'width', 'height', 'frequency', 'flags'])

class OutputRequest:
    def __init__(self, id, enabled = True, mode = None, x = None, y = None, transform = 0, presentation = False, clone_of = None):
        self.id = id
        self.clone_of = clone_of
        self.presentation = presentation
        if clone_of is None:
            self.enabled = enabled
            self.mode = mode
            self.x = x
            self.y = y
            self.transform = transform
        else:
            self.enabled = clone_of.enabled
            self.mode = clone_of.mode
            self.x = clone_of.x
            self.y = clone_of.y
            self.transform.clone_of.transform

class DisplayConfig:
    def __init__(self):
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.proxy = Gio.DBusProxy.new_sync(self.bus, Gio.DBusProxyFlags.NONE, None,
            'org.gnome.Mutter.DisplayConfig', '/org/gnome/Mutter/DisplayConfig', 'org.gnome.Mutter.DisplayConfig', None)
        self.get_resources()

    def get_resources(self):
        self.serial, self.crtcs, self.outputs, self.modes, self.max_screen_width, self.max_screen_height = self.proxy.GetResources()
        # Create lists of namedtuples for the results
        self.crtcs = [Crtc(*x) for x in self.crtcs]
        self.outputs = [Output(*x) for x in self.outputs]
        self.modes = [Mode(*x) for x in self.modes]

    def get_crtc(self, id):
        for crtc in self.crtcs:
            if crtc.id == id:
                return crtc
        raise Exception("Unable to find crtc {0}".format(id))

    def get_mode(self, id):
        for mode in self.modes:
            if mode.id == id:
                return mode
        raise Exception("Unable to find mode {0}".format(id))

    def _get_output(self, id):
        for output in self.outputs:
            if output.id == id:
                return output
        raise Exception("Unable to find output {0}".format(id))

    def _configure(self, output_requests, configured_outputs, configured_crtcs):
        if len(output_requests) == 0:
            configured_ids = [crtc.id for crtc in configured_crtcs]
            for crtc in self.crtcs:
                if crtc.id not in configured_ids:
                    configured_crtcs.append(CrtcConfiguration(
                        id = crtc.id,
                        new_mode = -1,
                        x = 0, y = 0,
                        transform = 0,
                        outputs = [],
                        properties = {}
                        ))
            return configured_outputs, configured_crtcs

        output_request = output_requests[0]
        output = self._get_output(output_request.id)

        properties = dict()
        if output.properties['primary'] != (len(configured_outputs) == 0):
            properties['primary'] = GLib.Variant('(b)', (len(configured_outputs) == 0,))
        if output.properties['presentation'] != output_request.presentation:
            properties['presentation'] = GLib.Variant('(b)', (output_request.presentation,))

        output_configuration = OutputConfiguration(output_request.id, properties)

        if output_request.clone_of is not None:
            for i, crtc_configuration in enumerate(configured_crtcs):
                if output_request.clone_of.id in crtc_configuration.outputs:
                    if crtc_configuration.id in output.possible_crtcs:
                        configured_crtcs = copy.deepcopy(configured_crtcs)
                        configured_crtcs[i].outputs.append(output_request.id)
                        return self._configure(output_requests[1:],
                                configured_outputs + output_configuration,
                                configured_crtcs)
                    else:
                        # Clone can not be done at crtc level, use standard path
                        break

        for crtcid in output.possible_crtcs:
            # If crtc is already configured, skip it
            if crtcid in [configured.id for configured in configured_crtcs]:
                continue
            configured_crtc = CrtcConfiguration(id = crtcid,
                    new_mode = output_request.mode.id,
                    x = output_request.x,
                    y = output_request.y,
                    transform = output_request.transform,
                    outputs = [output.id],
                    properties = {})
            try:
                return self._configure(output_requests[1:],
                        configured_outputs + [output_configuration],
                        configured_crtcs + [configured_crtc])
            except:
                pass

        raise Exception("Unable to find a configuration for the requested outputs")

    def configure(self, output_requests, persistent = False):
        outputs, crtcs = self._configure(output_requests, [], [])
        params = GLib.Variant('(uba(uiiiuaua{sv})a(ua{sv}))', (self.serial, persistent, crtcs, outputs))
        res = self.proxy.call('ApplyConfiguration', params, Gio.DBusConnectionFlags.NONE, -1, None, None)
        if res is not None:
            print(res)

def main():
    # Split arguments in groups:
    # One argument with no leading dash followed
    # by options, until the next non-option argument
    # program --opt1 a --opt2 b X --x1 c Y Z 
    # will give [['--opt1', 'a', '--opt2', 'b'], ['X', '--x1', 'c'], ['Y'], ['Z']]
    arg_packs = []
    pack = []
    for arg in sys.argv[1:]:
        if arg.startswith('-') or pack and pack[-1].startswith('-'):
            pack.append(arg)
        else:
            arg_packs.append(pack)
            pack = [arg]
    arg_packs.append(pack)

    main_parser = argparse.ArgumentParser(description='Manage display configuration.')


    main_args = main_parser.parse_args(arg_packs[0])

    dc = DisplayConfig()

    if len(arg_packs) == 1:
        # Print the current configuration and quit
        modes = {}
        for mode in dc.modes:
            modes[mode.id] = mode
        for output in dc.outputs:
            if output.current_crtc == -1:
                print('{name}: {vendor} {product} (off)'.format(
                    name = output.name,
                    vendor = output.properties['vendor'],
                    product = output.properties['product']))
            else:
                crtc = dc.get_crtc(output.current_crtc)
                mode = modes[crtc.current_mode]
                print('{name}: {vendor} {product} {width}x{height}+{x}+{y}'.format(
                    name = output.name,
                    vendor = output.properties['vendor'],
                    product = output.properties['product'],
                    width = mode.width,
                    height = mode.height,
                    x = crtc.x,
                    y = crtc.y))
            printed_modes = set()
            for mode_id in output.modes:
                mode = '\t{width}x{height}'.format(
                    width = modes[mode_id].width,
                    height = modes[mode_id].height)
                if mode not in printed_modes:
                    print(mode)
                    printed_modes.add(mode)
        return

    outputs = {}
    for output in dc.outputs:
        outputs[output.name] = output

    output_requests = []
    for i,pack in enumerate(arg_packs[1:]):
        if pack[0] not in outputs:
            print('Output {output} unknown, possible outputs: {available}'.format(
                output = pack[0],
                available = ', '.join(outputs.keys())))
            sys.exit(1)
        if pack[0] in [p[0] for p in arg_packs[1:i]]:
            print('Output {output} provided twice'.format(output = pack[0]))
            sys.exit(1)

        output = outputs[pack[0]]

        crtc = dc.get_crtc(output.current_crtc) if output.current_crtc != -1 else None

        mode_choices = {}
        default_mode = None
        for mode_id in output.modes:
            mode = dc.get_mode(mode_id)
            short_mode = '{width}x{height}'.format(
                    width = mode.width, height = mode.height)
            if default_mode is None or ( crtc is not None and crtc.current_mode == mode_id):
                default_mode = short_mode

            if short_mode not in mode_choices or mode_choices[short_mode].frequency < mode.frequency:
                mode_choices[short_mode] = mode

        screen_parser = argparse.ArgumentParser(description='Settings for one display.')
        screen_parser.add_argument('name', help='Name of the display to configure')
        screen_parser.add_argument('--mode', help='widthxheight[@refresh]', choices = list(mode_choices.keys()), default = default_mode)
        screen_parser.add_argument('-x', '--x', help='Horizontal offset of the screen', type=int, default = crtc.x if crtc is not None else 0)
        screen_parser.add_argument('-y', '--y', help='Vertical offset of the screen', type=int, default = crtc.y if crtc is not None else 0)
        screen_parser.add_argument('--presentation', help='Presentation mode', type=bool, default = False)
        if i > 0:
            screen_parser.add_argument('--clone', help='Clone of the provided screen', choices = [pack[0] for pack in arg_packs[1:i+1]])

        args = screen_parser.parse_args(pack)

        if i > 0 and args.clone:
            clone = None # FIXME implement
        else:
            clone = None
        output_requests.append(OutputRequest(output.id,
            mode = mode_choices[args.mode],
            x = args.x, y = args.y, presentation = args.presentation, clone_of = clone))

    dc.configure(output_requests)

if __name__ == '__main__':
    main()
