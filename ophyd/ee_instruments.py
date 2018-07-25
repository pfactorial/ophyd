# Lucas J. Koerner
# 05/2018
# koerner.lucas@stthomas.edu
# University of St. Thomas

# standard library imports 
import sys
import numpy as np
import scipy.signal as signal
import functools

# use symbolic links
sys.path.append(
    '/Users/koer2434/ophyd/')  # these 2 will become an import of ophyd
sys.path.append(
    '/Users/koer2434/instrbuilder/')  # this instrbuilder: the SCPI library


# imports that require sys.path.append pointers

from instrbuilder.setup import scpi_lia, scpi_fg, scpi_fg2, scpi_dmm, scpi_osc, data_save

from ophyd.scpi import ScpiSignal, ScpiSignalBase, ScpiSignalFileSave, StatCalculator, ScpiCompositeBase, ScpiCompositeSignal
from ophyd import Device, Component, Signal
from ophyd.device import Kind
from instrbuilder.scpi import SCPI

class BlankCommHandle():
    def __init__(self):
        self.write = None
        self.ask = None

bch = BlankCommHandle()
scpi = SCPI([], bch)


def create_filter(order, sample_rate, tau):
    cutoff_freq = 1 / (2 * np.pi * tau)
    norm_cutoff_freq = cutoff_freq / (sample_rate / 2)  # [from 0 - 1]

    num, denom = signal.iirfilter(N=order, Wn=norm_cutoff_freq,
                                  rp=None, rs=None, btype='lowpass', analog=False,
                                  ftype='butter', output='ba')
    return num, denom


def apply_filter(arr, num, denom, sample_rate, tau):
    output_signal = signal.filtfilt(num, denom, arr)

    tau_settle = 5
    settle_idx = int(tau_settle * tau / (1 / sample_rate))
    decimate_length = int(tau / (1 / sample_rate))

    arr_downsample = output_signal[settle_idx::decimate_length]

    return arr_downsample[0]


class ManualDevice(Device):
    val = Component(Signal, name='val')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


class BasicStatistics(Device):
    func_list = [np.sum, np.mean, np.std, np.min, np.max, len]
    components = {}

    for func in func_list:
        func_name = func.__name__
        components[func_name] = Component(StatCalculator, name=func_name, img=None,
                                          stat_func=func, kind=Kind.hinted)

    locals().update(components)

    def __init__(self, array_source, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for func in self.func_list:
            getattr(self, func.__name__)._img = array_source.get_array
            # update the name
            getattr(self, func.__name__).name = array_source.name + getattr(self, func.__name__).name


class FilterStatistics(Device):

    # TODO: How to not re-run the filter for each statistic, maybe by re-assigning _img below
    components = {}

    # use functools.partial to input all parameters but the data array
    #   generate the filter numerator and denominator here

    order = 1  # db/octave = order*6dB
    sample_rate = 1220.680518480077 * 8  # 5e6/512/8*8 Hz
    print('Sample rate = {} [Hz]'.format(sample_rate))
    tau = 30e-3

    num, denom = create_filter(order=order, sample_rate=sample_rate, tau=tau)
    func_name = 'filter_6dB'
    func = functools.partial(apply_filter, num=num, denom=denom, sample_rate=sample_rate, tau=tau)
    components[func_name] = Component(StatCalculator, name=func, img=None,
                                      stat_func=func, kind=Kind.hinted)

    order = 4  # db/octave = order*24dB
    num, denom = create_filter(order=order, sample_rate=sample_rate, tau=tau)
    func_name = 'filter_24dB'
    func = functools.partial(apply_filter, num=num, denom=denom, sample_rate=sample_rate, tau=tau)
    components[func_name] = Component(StatCalculator, name=func, img=None,
                                      stat_func=func, kind=Kind.hinted)

    locals().update(components)

    def __init__(self, array_source, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for func in self.func_list:
            getattr(self, func.__name__)._img = array_source.get_array
            # update the name
            getattr(self, func.__name__).name = array_source.name + getattr(self, func.__name__).name


# ------------------------------------------------------------
# 					Lock-in Amplifier
# ------------------------------------------------------------


class LockIn(Device):
    components = {}
    for cmd_key, cmd in scpi_lia._cmds.items():
        if cmd.is_config:
            comp_kind = Kind.config
        else:
            comp_kind = Kind.normal

        if hasattr(cmd.getter_type, 'returns_array'):
            if cmd.getter_type.returns_array:
                if cmd_key == 'read_buffer':
                    # setup, monitoring and wind-down for the read_buffer command.
                    # TODO: make this less awkward, part of instrbuilder?
                    status_monitor = {'name': 'data_pts_ready', 'configs': {},
                                      'threshold_function': lambda read_val, thresh: read_val > thresh,
                                      'threshold_level': 100,
                                      'poll_time': 0.05,
                                      'trig_name': ['reset_scan', 'start_scan', 'trig'],
                                      'trig_configs': {},
                                      'post_name': 'pause_scan',
                                      'post_configs': {}}
                    components[cmd.name] = Component(ScpiSignalFileSave, name=cmd.name,
                                                     scpi_cl=scpi_lia, cmd_name=cmd.name,
                                                     save_path = data_save.directory,
                                                     kind = Kind.normal,
                                                     precision = 10, # precision sets length printed in live table
                                                     configs = {'start_pt': 0, 'num_pts': 80},
                                                     status_monitor=status_monitor)
                else:
                    print('Skipping LockIn command {}. Returns an array but a status monitor dictionary is not prepared'.format(cmd.name))

        else:
            if cmd.setter and cmd.getter_inputs == 0 and cmd.setter_inputs < 2:
                components[cmd.name] = Component(ScpiSignal, scpi_cl=scpi_lia, cmd_name=cmd.name,
                                                 configs={}, kind=comp_kind)
            if (not cmd.setter) and cmd.getter_inputs == 0:
                components[cmd.name] = Component(ScpiSignalBase, scpi_cl=scpi_lia, cmd_name=cmd.name,
                                                 configs={}, kind=comp_kind)

    # Other commands need to be explicitly entered
    # Long setters (i.e. SCPI commands that takes more than a single value)

    off_exp = Component(ScpiSignal,
                        scpi_cl=scpi_lia, cmd_name='off_exp',
                        configs={'chan': 2})  # offset and expand

    ch1_disp = Component(ScpiSignal,
                         scpi_cl=scpi_lia, cmd_name='ch1_disp',
                         configs={'ratio': 0})  # ratio the display to None (0), Aux1 (1) or Aux2 (2)

    unconnected = scpi_lia.unconnected

    locals().update(components)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.help = scpi_lia.help
        self.help_all = scpi_lia.help_all

    def stage(self):
        super().stage()


# ------------------------------------------------------------
# 					Function Generator
# ------------------------------------------------------------


class FunctionGen(Device):
    components = {}
    for cmd_key, cmd in scpi_fg._cmds.items():
        if cmd.is_config:
            comp_kind = Kind.config
        else:
            comp_kind = Kind.normal

        if hasattr(cmd.getter_type, 'returns_array'):
            if cmd.getter_type.returns_array:
                print('Skipping FunctionGen command {}. Returns an array but a status monitor dictionary is not prepared'.format(cmd.name))
        else:
            if cmd.setter and cmd.getter_inputs == 0 and cmd.setter_inputs < 2:
                components[cmd.name] = Component(ScpiSignal, scpi_cl=scpi_fg, cmd_name=cmd.name,
                                                 configs={}, kind=comp_kind)
            if (not cmd.setter) and cmd.getter_inputs == 0:
                components[cmd.name] = Component(ScpiSignalBase, scpi_cl=scpi_fg, cmd_name=cmd.name,
                                                 configs={}, kind=comp_kind)
    unconnected = scpi_fg.unconnected
    locals().update(components)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.help = scpi_fg.help
        self.help_all = scpi_fg.help_all

    def stage(self):
        # TODO: this is too specific
        self.load.set('INF')
        self.output.set('ON')
        super().stage()

# ------------------------------------------------------------
# 					Function Generator
# ------------------------------------------------------------


class FunctionGen2(Device):
    components = {}
    for cmd_key, cmd in scpi_fg2._cmds.items():
        if cmd.is_config:
            comp_kind = Kind.config
        else:
            comp_kind = Kind.normal

        if hasattr(cmd.getter_type, 'returns_array'):
            if cmd.getter_type.returns_array:
                print('Skipping FunctionGen command {}. Returns an array but a status monitor dictionary is not prepared'.format(cmd.name))
        else:
            if cmd.setter and cmd.getter_inputs == 0 and cmd.setter_inputs < 2:
                components[cmd.name] = Component(ScpiSignal, scpi_cl=scpi_fg2, cmd_name=cmd.name,
                                                 configs={}, kind=comp_kind)
            if (not cmd.setter) and cmd.getter_inputs == 0:
                components[cmd.name] = Component(ScpiSignalBase, scpi_cl=scpi_fg2, cmd_name=cmd.name,
                                                 configs={}, kind=comp_kind)
    unconnected = scpi_fg2.unconnected
    locals().update(components)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.help = scpi_fg2.help
        self.help_all = scpi_fg2.help_all

    def stage(self):
        # TODO: this is too specific
        self.load.set('INF')
        self.output.set('ON')
        super().stage()

# ------------------------------------------------------------
# 					Function Generator (using __init_subclass__)
# ------------------------------------------------------------

"""
class EEInstrument():
    pass


class FunctionGen():
    print('here')
    def __init_subclass__(cls, scpi, **kwargs):
        # super().__init_subclass__(**kwargs)
        print('here2')
        components = {}
        for cmd_key, cmd in scpi._cmds.items():
            if cmd.is_config:
                comp_kind = Kind.config
            else:
                comp_kind = Kind.normal

            if hasattr(cmd.getter_type, 'returns_array'):
                if cmd.getter_type.returns_array:
                    print('Skipping FunctionGen command {}. Returns an array but a status monitor dictionary is not prepared'.format(cmd.name))
            else:
                if cmd.setter and cmd.getter_inputs == 0 and cmd.setter_inputs < 2:
                    components[cmd.name] = Component(ScpiSignal, scpi_cl=scpi, cmd_name=cmd.name,
                                                     configs={}, kind=comp_kind)
                if (not cmd.setter) and cmd.getter_inputs == 0:
                    components[cmd.name] = Component(ScpiSignalBase, scpi_cl=scpi, cmd_name=cmd.name,
                                                     configs={}, kind=comp_kind)
        cls.unconnected = scpi.unconnected
        locals().update(components)

        cls.help = scpi.help
        cls.help_all = scpi.help_all

#    def __init__(self, *args, **kwargs):
#        super().__init__(*args, **kwargs)
"""

# ------------------------------------------------------------
# 					Oscilloscope
# ------------------------------------------------------------

class Oscilloscope(Device):
    components = {}
    channels = [1, 2]
    for cmd_key, cmd in scpi_osc._cmds.items():
        if cmd.is_config:
            comp_kind = Kind.config
        else:
            comp_kind = Kind.normal

        if hasattr(cmd.getter_type, 'returns_array'):
            if cmd.name == 'display_data':
                print('Creating display data command')

                def save_png(filename, data):
                    with open(filename, 'wb') as out_f:
                        out_f.write(bytearray(data))

                components[cmd.name] = Component(ScpiSignalFileSave, name=cmd.name,
                                                 scpi_cl=scpi_osc, cmd_name=cmd.name,
                                                 save_path=data_save.directory,
                                                 save_func=save_png, save_spec='PNG', save_ext='png',
                                                 kind=Kind.normal,
                                                 precision=10)  # this precision won't print the full file name, but enough to be unique)

            elif cmd.getter_type.returns_array:
                print('Skipping Oscilloscpe command {}.'.format(cmd.name))
                print(' Returns an array but a status monitor dictionary is not prepared')

        else:
            if cmd.setter and cmd.getter_inputs == 0 and cmd.setter_inputs < 2:
                components[cmd.name] = Component(ScpiSignal, scpi_cl=scpi_osc, cmd_name=cmd.name,
                                                 configs={}, kind=comp_kind)
            if (not cmd.setter) and cmd.getter_inputs == 0:
                components[cmd.name] = Component(ScpiSignalBase, scpi_cl=scpi_osc, cmd_name=cmd.name,
                                                 configs={}, kind=comp_kind)

        #   Create components per chanel
        for channel in channels:
            if cmd.setter and cmd.getter_inputs == 1 and cmd.setter_inputs == 2 and '{channel}' in cmd.ascii_str:
                components[cmd.name + '_chan{}'.format(channel)] = Component(ScpiSignal, scpi_cl=scpi_osc, cmd_name=cmd.name,
                                                 configs={'channel': channel}, kind=comp_kind)
            if (not cmd.setter) and cmd.getter_inputs == 1 and '{channel}' in cmd.ascii_str:
                components[cmd.name + '_chan{}'.format(channel)] = Component(ScpiSignalBase, scpi_cl=scpi_osc, cmd_name=cmd.name,
                                                 configs={'channel': channel}, kind=comp_kind)

        if cmd.name == 'meas_phase':  # requires two channels to find phase difference
            components[cmd.name] = Component(ScpiSignalBase, scpi_cl=scpi_osc, cmd_name=cmd.name,
                                             configs={'chan1':1, 'chan2':2}, kind=comp_kind)

    unconnected = scpi_osc.unconnected
    locals().update(components)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.help = scpi_osc.help
        self.help_all = scpi_osc.help_all

# ------------------------------------------------------------
# 					Digital Multimeter
# ------------------------------------------------------------


class MultiMeter(Device):
    components = {}
    for cmd_key, cmd in scpi_dmm._cmds.items():
        if cmd.is_config:
            comp_kind = Kind.config
        else:
            comp_kind = Kind.normal

        if hasattr(cmd.getter_type, 'returns_array'):
            if cmd.name == 'burst_volt':
                components[cmd.name] = Component(ScpiSignalFileSave, name=cmd.name,
                                                 scpi_cl=scpi_dmm, cmd_name=cmd.name,
                                                 save_path=data_save.directory,
                                                 kind=Kind.normal,
                                                 precision=10, # this precision won't print the full file name, but enough to be unique
                                                 configs={'reads_per_trigger': 1024, 'aperture': 20e-6,
                                                 'trig_source': 'EXT', 'trig_count': 1})

            if cmd.name == 'burst_volt_timer':
                components[cmd.name] = Component(ScpiSignalFileSave, name=cmd.name,
                                                 scpi_cl=scpi_dmm, cmd_name=cmd.name,
                                                 save_path=data_save.directory,
                                                 kind=Kind.normal,
                                                 precision=10,
                                                 # this precision won't print the full file name, but enough to be unique
                                                 configs={'reads_per_trigger': 8, 'aperture': 20e-6,
                                                          'trig_source': 'EXT', 'trig_count': 1024,
                                                          'sample_timer': 102.4e-6, 'repeats': 1})

            elif cmd.getter_type.returns_array:
                print('Skipping command {}. '.format(cmd.name))
                print('Returns an array but a status monitor dictionary is not prepared')
        else:
            if cmd.setter and cmd.getter_inputs == 0 and cmd.setter_inputs < 2:
                components[cmd.name] = Component(ScpiSignal, scpi_cl=scpi_dmm, cmd_name=cmd.name,
                                                 configs={}, kind=comp_kind)
            if (not cmd.setter) and cmd.getter_inputs == 0:
                components[cmd.name] = Component(ScpiSignalBase, scpi_cl=scpi_dmm, cmd_name=cmd.name,
                                                 configs={}, kind=comp_kind)
            # AC/DC configurations.
            #   Create DC versions
            if cmd.setter and cmd.getter_inputs == 1 and cmd.setter_inputs == 2 and '{ac_dc}' in cmd.ascii_str:
                components[cmd.name + '_dc'] = Component(ScpiSignal, scpi_cl=scpi_dmm, cmd_name=cmd.name,
                                                 configs={'ac_dc': 'DC'}, kind=comp_kind)
            if (not cmd.setter) and cmd.getter_inputs == 1 and '{ac_dc}' in cmd.ascii_str:
                components[cmd.name + '_dc'] = Component(ScpiSignalBase, scpi_cl=scpi_dmm, cmd_name=cmd.name,
                                                 configs={'ac_dc': 'DC'}, kind=comp_kind)
            # AC/DC configurations.
            #   Create AC versions
            if cmd.setter and cmd.getter_inputs == 1 and cmd.setter_inputs == 2 and '{ac_dc}' in cmd.ascii_str:
                components[cmd.name + '_ac'] = Component(ScpiSignal, scpi_cl=scpi_dmm, cmd_name=cmd.name,
                                                 configs={'ac_dc': 'AC'}, kind=comp_kind)
            if (not cmd.setter) and cmd.getter_inputs == 1 and '{ac_dc}' in cmd.ascii_str:
                components[cmd.name + '_ac'] = Component(ScpiSignalBase, scpi_cl=scpi_dmm, cmd_name=cmd.name,
                                                 configs={'ac_dc': 'AC'}, kind=comp_kind)

    unconnected = scpi_dmm.unconnected
    locals().update(components)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.help = scpi_dmm.help
        self.help_all = scpi_dmm.help_all