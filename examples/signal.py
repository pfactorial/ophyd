#!/usr/bin/env python2.7
'''
A simple test for :class:`EpicsSignal`
'''

import time

import config
from ophyd.controls import EpicsSignal
from ophyd.utils.epics_pvs import record_field
import ophyd

import epics

def test():
    def callback(sub_type=None, timestamp=None, value=None, **kwargs):
        logger.info('[callback] [%s] (type=%s) value=%s' % (timestamp, sub_type, value))

    loggers = ('ophyd.controls.signal',
               'ophyd.session',
               )

    config.setup_loggers(loggers)
    logger = config.logger

    motor_record = config.motor_recs[0]
    val = record_field(motor_record, 'VAL')
    rbv = record_field(motor_record, 'RBV')

    rw_signal = EpicsSignal(rbv, write_pv=val)
                            # put_complete=True)
    rw_signal.subscribe(callback, event_type=rw_signal.SUB_READBACK)
    rw_signal.subscribe(callback, event_type=rw_signal.SUB_REQUEST)

    rw_signal.value = 2
    time.sleep(1.)
    rw_signal.value = 1
    time.sleep(1.)

if __name__ == '__main__':
    test()
