#!/home/cedric/anaconda3/envs/2point7/bin/python
# -*- coding: utf-8 -*-

################################################################################
#
# Copyright (C) 2022
# Observatoire Radioastronomique de Nançay,
# Observatoire de Paris, PSL Research University, CNRS, Univ. Orléans, OSUC,
# 18330 Nançay, France
#
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
################################################################################
# Author: Cedric Viou (Cedric.Viou@obs-nancay.fr)
#
# Description:
# Configure and run design adc_sst_v7 (win-FFT SEFRAM)
################################################################################


import casperfpga
import time
import numpy as np
import struct
import sys
import logging
import pylab
import matplotlib.pyplot as plt
import signal
import imp


import ADC_clock
import ADC
import sefram

ADC_clock = imp.reload(ADC_clock)
ADC = imp.reload(ADC)
sefram = imp.reload(sefram)



roach2 = "192.168.40.71"
bitstream = "../bof/adc_sst_v7/bit_files/adc_sst_v7_2022_Aug_25_1418.fpg"

conf_Valon = True
ADC_cal = True

conf_Valon = True

#FEED, Fe = 'HF', 3200000000.0 # 1.6-3.2  GHz
FEED, Fe = 'BF', 3700000000.0 #   0-1.85 GHz
F_valon = Fe / 2
Fsys = F_valon / 8
Fin = 130000000# Hz

Valon = ADC_clock.ADC_clock()
if conf_Valon:
  Valon.set_config(FA=F_valon/1e6,
                   PA=-4,
                   FB=Fin/1e6,
                   PB=-4,
                   )
Valon.print_config()


lh = logging.StreamHandler()
logger = logging.getLogger(roach2)
logger.addHandler(lh)
logger.setLevel(10)




# make class to control CASPER FPGA design for NRT channelizer
class adc_sst_v7(object):
  def __init__(self, name, bitstream=None, Fe=None, feed='BF'):
    self.name = name
    self.fpga = casperfpga.CasperFpga(self.name)
    time.sleep(0.2)
    self.Fe = Fe
    self.F_valon = self.Fe / 2
    self.Fsys = self.F_valon / 8
    self._feed = feed

    assert self.fpga.is_connected(), 'ERROR connecting to server %s.\n' % (self.name)
    if bitstream is not None:
      print('------------------------')
      print('Programming FPGA with %s...' % bitstream)
      sys.stdout.flush()
      self.fpga.upload_to_ram_and_program(bitstream)
      print('done')

    self.monitoring_regs = (
                   'frmr_pcktizer_cur_timestamp',
                   'frmr_pcktizer_cur_smpl_cnt',
                   'frmr_pcktizer_cur_smpl_per_sec',
                   'frmr_acc_cnt',
                   #'eof_cnt',
                   'OneGbE_tx_full',
                   )
    # Add peripherals and submodules
    self.ADCs = (ADC.ADC(fpga=self.fpga, zdok_n=0, Fe=self.Fe),
                 ADC.ADC(fpga=self.fpga, zdok_n=1, Fe=self.Fe))
    self.SEFRAM = sefram.sefram(fpga=self.fpga, Fe=self.Fe, packetizer_basename='pcktizer_')

    # init modules
    self.SEFRAM.disable()


  def cnt_rst(self):
    self.fpga.write_int('cnt_rst', 1)
    self.fpga.write_int('cnt_rst', 0)

  def arm_PPS(self):
    self.fpga.write_int('reg_arm', 0)
    now = time.time()
    before_half_second = 0.5 - (now-int(now))
    if before_half_second < 0:
      before_half_second += 1
    time.sleep(before_half_second)
    self.fpga.write_int('reg_arm', 1)

  def listdev(self):
    return self.fpga.listdev()

  def monitor(self):
    for reg in self.monitoring_regs:
        print(reg, self.fpga.read_uint(reg))

  @property
  def feed(self):
    return self._feed

  @feed.setter
  def feed(self, value):
    if value not in ('BF', 'HF'):
      raise ValueError('NRT feeds are BF (1-1.8GHz, connected on ADC_I) or HF (1.7-3.5GHz, conected on ADC_Q)')
    self._feed = value
    adcmode = {'BF': 'I',
               'HF': 'Q',
               }
    for ADC in self.ADCs:
      ADC.adcmode=adcmode[self._feed]
      ADC.adcmode=adcmode[self._feed]



mydesign = adc_sst_v7(roach2, bitstream=bitstream, Fe=Fe)


dev = mydesign.listdev()
for d in dev:
    print(d)
print()


if ADC_cal:
  print('Calibrating ADCs')
  [ ADC.run_DVW_calibration() for ADC in mydesign.ADCs ]
  [ ADC.print_DVW_calibration() for ADC in mydesign.ADCs ]
  print('Done')



Nfft = 4096
nof_lanes = 8
mydesign.feed = FEED


if False:
  [ ADC.get_snapshot(count=100) for ADC in mydesign.ADCs ]
  [ ADC.dump_snapshot() for ADC in mydesign.ADCs ]


fig, axs = plt.subplots(nrows = len(mydesign.ADCs), 
                        ncols = 3,
                        sharex='col', sharey='col',
                        )
for ADC_axs, ADC in zip(axs, mydesign.ADCs):
  ADC.get_snapshot()
  ADC.plot_interleaved_data(ADC_axs)
plt.tight_layout()
plt.show(block=False)



print('SEFRAM Configuration')

mydesign.SEFRAM.disable()
mydesign.cnt_rst()
time.sleep(0.2)



Nspec_per_sec = mydesign.SEFRAM.Fe / mydesign.SEFRAM.Nfft
acc_len = int(Nspec_per_sec // 10)
mydesign.SEFRAM.acc_len = acc_len

print('vacc_n_frmr_acc_cnt = ', mydesign.SEFRAM.acc_cnt)

fft_shift_reg = 0xfff
mydesign.SEFRAM.fft_shift = fft_shift_reg
print('FFT gain =  = ', mydesign.SEFRAM.fft_gain)

mydesign.SEFRAM.dst_addr = ("192.168.41.1", 0xcece)
mydesign.SEFRAM.IFG = 100000
mydesign.SEFRAM.print_datarate()

# fpga.write_int('vacc_n_frmr_pcktizer_ADC_freq', int(Fe), blindwrite=True)
# set during SEFRAM instanciation

# mydesign.SEFRAM.ID = 0xcece
# set in SEFRAM constructor


mydesign.SEFRAM.arm()



print('Wait for half second and arm PPS_trigger')
mydesign.arm_PPS()
mydesign.monitor()

print('Started!!!')
time.sleep(1)
mydesign.monitor()
time.sleep(1)
mydesign.monitor()


mydesign.monitor()

# after dummy frame, allow outputing data and starting framer 
mydesign.SEFRAM.enable()

mydesign.SEFRAM.time = "now"  # set time to current UNIX timestamp
#print(mydesign.SEFRAM.time)
ts         = mydesign.fpga.read_uint('frmr_pcktizer_cur_timestamp')
sample_cnt = mydesign.fpga.read_uint('frmr_pcktizer_cur_smpl_cnt')
sysfreq    = mydesign.fpga.read_uint('frmr_pcktizer_cur_smpl_per_sec')
print( ts + float(sample_cnt) / (sysfreq+1) )


time.sleep(1)
mydesign.monitor()
time.sleep(1)
mydesign.monitor()
time.sleep(1)
mydesign.monitor()
time.sleep(1)


plt.show()



