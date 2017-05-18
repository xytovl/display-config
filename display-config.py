#!/usr/bin/python3
'''
Utility to control gnome-shell output configuration.
'''
import argparse
import collections
import copy
from gi.repository import Gio, GLib

Crtc = collections.namedtuple(
    'Crtc',
    ['id_', 'winsys_id',
     'x', 'y',
     'width', 'height',
     'current_mode',
     'current_transform',
     'transforms',
     'properties'])

Output = collections.namedtuple(
    'Output',
    ['id_', 'winsys_id',
     'current_crtc',
     'possible_crtcs',
     'name',
     'modes',
     'clones',
     'properties'])

CrtcConfiguration = collections.namedtuple(
    'CrtcConfiguration',
    ['id_',
     'new_mode',
     'x', 'y',
     'transform',
     'outputs',
     'properties'])

OutputConfiguration = collections.namedtuple(
    'OutputConfiguration',
    ['id_', 'properties'])

Mode = collections.namedtuple(
    'Mode',
    ['id_', 'winsys_id', 'width', 'height', 'frequency', 'flags'])


class OutputRequest:
    '''
    High level output configuration request:
    contains the id and visible settings.
    '''
    def __init__(self, id_, enabled=True, mode=None, x=None, y=None,
                 transform=0, presentation=False, clone_of=None):
        self.id_ = id_
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
            self.transform = clone_of.transform


class InvalidConfigurationException(Exception):
    pass


class DisplayConfig:
    def __init__(self):
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.proxy = Gio.DBusProxy.new_sync(
            self.bus, Gio.DBusProxyFlags.NONE, None,
            'org.gnome.Mutter.DisplayConfig',
            '/org/gnome/Mutter/DisplayConfig',
            'org.gnome.Mutter.DisplayConfig', None)
        self.get_resources()

    def get_resources(self):
        (self.serial, self.crtcs, self.outputs, self.modes,
         self.max_screen_width,
         self.max_screen_height) = self.proxy.GetResources()
        # Create lists of namedtuples for the results
        self.crtcs = [Crtc(*x) for x in self.crtcs]
        self.outputs = [Output(*x) for x in self.outputs]
        self.modes = [Mode(*x) for x in self.modes]

    def get_crtc(self, id_):
        for crtc in self.crtcs:
            if crtc.id_ == id_:
                return crtc
        raise Exception("Unable to find crtc {0}".format(id_))

    def get_mode(self, id_):
        for mode in self.modes:
            if mode.id_ == id_:
                return mode
        raise Exception("Unable to find mode {0}".format(id_))

    def _get_output(self, id_):
        for output in self.outputs:
            if output.id_ == id_:
                return output
        raise Exception("Unable to find output {0}".format(id_))

    def _configure(self, output_requests,
                   configured_outputs, configured_crtcs):
        if len(output_requests) == 0:
            configured_ids = [crtc.id_ for crtc in configured_crtcs]
            for crtc in self.crtcs:
                if crtc.id_ not in configured_ids:
                    configured_crtcs.append(CrtcConfiguration(
                        id_=crtc.id_,
                        new_mode=-1,
                        x=0, y=0,
                        transform=0,
                        outputs=[],
                        properties={}
                        ))
            return configured_outputs, configured_crtcs

        output_request = output_requests[0]
        output = self._get_output(output_request.id_)

        properties = dict()
        # First parameter is the primary screen
        primary = (len(configured_outputs) == 0)
        if output.properties['primary'] != primary:
            properties['primary'] = GLib.Variant('(b)', (primary,))
        if output.properties['presentation'] != output_request.presentation:
            properties['presentation'] = GLib.Variant(
                '(b)', (output_request.presentation,))

        output_configuration = OutputConfiguration(output_request.id_,
                                                   properties)

        if output_request.clone_of is not None:
            for i, crtc_configuration in enumerate(configured_crtcs):
                if output_request.clone_of.id_ in crtc_configuration.outputs:
                    if crtc_configuration.id_ in output.possible_crtcs:
                        configured_crtcs = copy.deepcopy(configured_crtcs)
                        configured_crtcs[i].outputs.append(output_request.id_)
                        return self._configure(
                            output_requests[1:],
                            configured_outputs + [output_configuration],
                            configured_crtcs)
                    else:
                        # Clone can not be done at crtc level,
                        # use generic processing
                        break

        for crtcid_ in output.possible_crtcs:
            # If crtc is already configured, skip it
            if crtcid_ in [configured.id_ for configured in configured_crtcs]:
                continue
            configured_crtc = CrtcConfiguration(
                id_=crtcid_,
                new_mode=output_request.mode.id_,
                x=output_request.x,
                y=output_request.y,
                transform=output_request.transform,
                outputs=[output.id_],
                properties={})
            try:
                return self._configure(
                    output_requests[1:],
                    configured_outputs + [output_configuration],
                    configured_crtcs + [configured_crtc])
            except InvalidConfigurationException:
                pass

        raise InvalidConfigurationException(
            "Unable to find a configuration for the requested outputs")

    def configure(self, output_requests, persistent=False):
        outputs, crtcs = self._configure(output_requests, [], [])
        params = GLib.Variant('(uba(uiiiuaua{sv})a(ua{sv}))',
                              (self.serial, persistent, crtcs, outputs))
        res = self.proxy.call('ApplyConfiguration', params,
                              Gio.DBusConnectionFlags.NONE, -1, None, None)
        if res is not None:
            print(res)


def main():
    dc = DisplayConfig()

    outputs = {output.name: output for output in dc.outputs}
    output_names = list(outputs.keys())
    main_parser = argparse.ArgumentParser(
        description='Manage display configuration.')

    main_parser.add_argument('--persistent',
                             help='Make this configuration the default',
                             action='store_true')

    main_parser.add_argument('OUTPUT',
                             help='Output to enable',
                             nargs='?',
                             choices=output_names)
    main_parser.add_argument('options',
                             help=argparse.SUPPRESS,
                             nargs=argparse.REMAINDER)

    args = main_parser.parse_args()
    persistent = args.persistent

    if args.OUTPUT is None:
        # Print the current configuration and quit
        modes = {}
        for mode in dc.modes:
            modes[mode.id_] = mode
        for output in dc.outputs:
            if output.current_crtc == -1:
                print('{name}: {vendor} {product} (off)'.format(
                    name=output.name,
                    vendor=output.properties['vendor'],
                    product=output.properties['product']))
            else:
                crtc = dc.get_crtc(output.current_crtc)
                mode = modes[crtc.current_mode]
                print('{name}: {vendor} {product} '
                      '{width}x{height}+{x}+{y}'.format(
                          name=output.name,
                          vendor=output.properties['vendor'],
                          product=output.properties['product'],
                          width=mode.width,
                          height=mode.height,
                          x=crtc.x,
                          y=crtc.y))
            printed_modes = set()
            for mode_id in output.modes:
                mode = '\t{width}x{height}'.format(
                    width=modes[mode_id].width,
                    height=modes[mode_id].height)
                if mode not in printed_modes:
                    print(mode)
                    printed_modes.add(mode)
        return

    # Process outputs one by one
    processed_outputs = {}
    output_requests = []
    while getattr(args, 'OUTPUT', False):
        output = outputs[args.OUTPUT]
        processed_outputs[args.OUTPUT] = output

        if output.current_crtc == -1:
            crtc = None
        else:
            crtc = dc.get_crtc(output.current_crtc)

        # List modes available for current output
        default_mode = None
        mode_choices = {}
        for mode_id in output.modes:
            mode = dc.get_mode(mode_id)
            mode_string = '{width}x{height}'.format(
                width=mode.width, height=mode.height)
            if default_mode is None or (
                    crtc is not None and crtc.current_mode == mode_id):
                default_mode = mode_string

            if (mode_string not in mode_choices or
                    mode_choices[mode_string].frequency < mode.frequency):
                mode_choices[mode_string] = mode

        output_parser = argparse.ArgumentParser(
            description='Settings for one display.')
        output_parser.add_argument('--mode',
                                   help='width x height',
                                   choices=list(mode_choices.keys()),
                                   default=default_mode)
        position_group = output_parser.add_mutually_exclusive_group()

        position_group.add_argument(
                '--position',
                help='Horizontal, vertical offset of the screen',
                metavar=('X', 'Y'),
                type=int,
                nargs=2,
                default=[crtc.x, crtc.y] if crtc is not None else [0, 0])

        # Clone can only be specified for second screen or later
        if processed_outputs:
            position_group.add_argument('--clone',
                                        help='Clone of the provided screen',
                                        choices=processed_outputs.keys())

        output_parser.add_argument('--presentation',
                                   help='Presentation mode',
                                   action='store_true',
                                   default=False)

        # Parameter for following screens
        remaining = list(set(outputs.keys()) - set(processed_outputs.keys()))
        if remaining:
            output_parser.add_argument('OUTPUT',
                                       help='Next output to enable',
                                       nargs='?',
                                       choices=remaining)
            output_parser.add_argument('options',
                                       help=argparse.SUPPRESS,
                                       nargs=argparse.REMAINDER)

        args = output_parser.parse_args(args.options)

        if output_requests and args.clone:
            for req in output_requests:
                if req.id_ == processed_outputs[args.clone].id_:
                    clone = req
        else:
            clone = None

        output_requests.append(OutputRequest(
            output.id_,
            mode=mode_choices[args.mode],
            x=args.position[0], y=args.position[1],
            presentation=args.presentation, clone_of=clone))

    dc.configure(output_requests, persistent=persistent)


if __name__ == '__main__':
    main()
