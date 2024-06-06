
from gi.repository import GLib as gobject
import dbus
import sys
import os
import logging
import platform

sys.path.insert(1, os.path.join(os.path.dirname(__file__), '/opt/victronenergy/dbus-systemcalc-py/ext/velib_python'))
from vedbus import VeDbusService
from settingsdevice import SettingsDevice

try:
    import thread   # for daemon = True
except ImportError:
    pass

log = logging.getLogger("DbusTrixingTemplate")

class SystemBus(dbus.bus.BusConnection):
    def __new__(cls):
        return dbus.bus.BusConnection.__new__(cls, dbus.bus.BusConnection.TYPE_SYSTEM)


class SessionBus(dbus.bus.BusConnection):
    def __new__(cls):
        return dbus.bus.BusConnection.__new__(cls, dbus.bus.BusConnection.TYPE_SESSION)


def dbusconnection():
    return SessionBus() if 'DBUS_SESSION_BUS_ADDRESS' in os.environ else SystemBus()



class DbusTrixingService:

  def _set_up_device_instance(self, servicename, instance):
       settings_device_path = "/Settings/Devices/{}/ClassAndVrmInstance".format(servicename)
       requested_device_instance = "{}:{}".format(self._deviceclass, instance)
       r = self._settings.addSetting(settings_device_path, requested_device_instance, "", "")
       _s, _di = r.get_value().split(':') # Return the allocated ID provided from dbus SettingDevices
       return int(_di)

  def _handle_changed_setting(self, setting, oldvalue, newvalue):
      log.info("Setting changed, setting: %s, old: %s, new: %s", setting, oldvalue, newvalue)
      if setting == '/CustomName':
          self['/CustomName'] = newvalue
      return True

  def _handle_changed_custom_name(self, setting, newvalue):
      log.info("Custom Name changed, setting: %s, new: %s", setting, newvalue)
      self._settings['/CustomName'] = newvalue
      self['/CustomName'] = newvalue
      return True

  def __init__(self, deviceclass, devicename,
               displayname=None, deviceinstance=None,
               firmwareversion=None, hardwareversion=None,
               serial=None, version=None, connection=None):
    servicename = 'com.victronenergy.' + deviceclass + '.' + devicename
    self._deviceclass = deviceclass
    bus = dbusconnection()
    self._dbusservice = VeDbusService(servicename, bus=bus)
    self._settings = SettingsDevice(bus=bus,
                                    supportedSettings={},
                                    eventCallback=self._handle_changed_setting)
    settings_name = deviceclass + '_' + devicename.replace('.', '_')
    self.device_instance = self._set_up_device_instance(settings_name, deviceinstance)
    path   = "/Settings/Devices/{}/CustomName".format(settings_name)
    self._settings.addSettings({'/CustomName': [path, "", 0, 0]})
    custom_name = ""
    # custom_name = self._set_up_custom_name(settings_name)
    log.info("Registered %s  with DeviceInstance = %d" % (servicename, self.device_instance))

    # Create the management objects, as specified in the ccgx dbus-api document
    self._dbusservice.add_path('/Mgmt/ProcessName', __file__)
    self._dbusservice.add_path('/Mgmt/ProcessVersion', version)
    self._dbusservice.add_path('/Mgmt/Connection', connection)

    # Create the mandatory objects
    self._dbusservice.add_path('/DeviceInstance', self.device_instance)
    self._dbusservice.add_path('/ProductId', 16)
    self._dbusservice.add_path('/ProductName', displayname)
    self._dbusservice.add_path('/FirmwareVersion', firmwareversion)
    self._dbusservice.add_path('/HardwareVersion', hardwareversion)
    self._dbusservice.add_path('/Serial', serial)
    self._dbusservice.add_path('/Connected', 1)

    self._dbusservice.add_path('/CustomName', self._settings['/CustomName'],
        writeable = True,
        onchangecallback = self._handle_changed_custom_name)



    self._retries = 0

  def schedule(self, timeout=5000):
    gobject.timeout_add(timeout, self._safe_update)

  def add_path(self, *args, **kwargs):
      self._dbusservice.add_path(*args, **kwargs)
    
  _kwh = lambda self, p, v: (str(v) + 'kWh')
  _a = lambda self, p, v: (str(v) + 'A')
  _w = lambda self, p, v: (str(int(v)) + 'W')
  _v = lambda self, p, v: (str(v) + 'V')
  _c = lambda self, p, v: (str(v) + 'C')


  def add_power_paths(self):
    paths=[
      '/Ac/L1/Power',
      '/Ac/L1/Voltage',
      '/Ac/L1/Current',
      '/Ac/L1/Energy/Forward',
      '/Ac/L2/Power',
      '/Ac/L2/Voltage',
      '/Ac/L2/Current',
      '/Ac/L2/Energy/Forward',
      '/Ac/L3/Power',
      '/Ac/L3/Voltage',
      '/Ac/L3/Current',
      '/Ac/L3/Energy/Forward',
      '/Ac/Energy/Forward',
      '/Ac/Frequency',
      #'/Ac/Voltage',
      #'/Ac/Current',
      '/Ac/Power',
    ]

    for path in paths:
      cb = None
      if path.endswith('Power'):
          cb = self._w
      elif path.endswith('Current'):
          cb = self._a
      elif path.endswith('Voltage'):
          cb = self._v
      elif path.endswith('Forward'):
          cb = self._kwh
      self._dbusservice.add_path(path, None, gettextcallback=cb)

  def disconnect(self):
      self._dbusservice['/Connected'] = 0

  def connect(self):
      self._dbusservice['/Connected'] = 1

  def __setitem__(self, k, v):
      self._dbusservice[k] = v

  def _safe_update(self):
    try:
        self._update()
        if self._retries > 0:
            log.warn('Connecting')
            self.connect() 
        self._retries = 0
    except Exception as exc:
        tb_str = traceback.format_exception(etype=type(exc), value=exc, tb=exc.__traceback__)

        log.error('Error running update, try %d: %s' % (self._retries, tb_str))
        self._retries += 1
        if self._retries == 12:
            self.disconnect()
    return True

  def update(self):
      raise NotImplemented

class DbusTrixingPvInverter(DbusTrixingService):

  def __init__(self, devicename, position=0, **kwargs):
    super().__init__('pvinverter', devicename,
                     **kwargs)
    self._dbusservice.add_path('/MaxPower', None, gettextcallback=self._w)
    self._dbusservice.add_path('/Position', position)  # 0 = AC-In, should be writable...
    self._dbusservice.add_path('/ErrorCode', 0)  # No Error
    self._dbusservice.add_path('/StatusCode', 0)  # No Error
    self._dbusservice.add_path('/Ac/PowerLimit', None, gettextcallback=self._w)

    self.add_power_paths()


class DbusTrixingTemperature(DbusTrixingService):
  def __init__(self, devicename, **kwargs):
    super().__init__('temperature', devicename,
                     **kwargs)
    self._dbusservice.add_path('/TemperatureType', 2)  # 0=battery, 1=fridge, 2=generic
    self._dbusservice.add_path('/Temperature', None, gettextcallback=self._c)
    self._dbusservice.add_path('/Status', 0)  # 0=ok, 1=disconnected, 2=short circuit

  def set_temperature(self, temperature):
    self['/Temperature'] = temperature


def prepare():
  root = logging.getLogger()
  root.setLevel(logging.INFO)

  handler = logging.StreamHandler(sys.stdout)
  handler.setLevel(logging.INFO)
  formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
  handler.setFormatter(formatter)
  root.addHandler(handler)


  try:
    thread.daemon = True # allow the program to quit
  except NameError:
    pass

  from dbus.mainloop.glib import DBusGMainLoop
  # Have a mainloop, so we can send/receive asynchronous calls to and from dbus
  DBusGMainLoop(set_as_default=True)
  log.info('Early Setup complete')


def run():
  log.info('Connected to dbus, and switching over to gobject.MainLoop() (= event based)')
  mainloop = gobject.MainLoop()
  mainloop.run()


