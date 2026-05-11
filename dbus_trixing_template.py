
import sys
import os
import logging
import platform
import traceback

import faulthandler
import threading
import time

try:
    from gi.repository import GLib as gobject
    import dbus
    sys.path.insert(1, os.path.join(os.path.dirname(__file__), '/opt/victronenergy/dbus-systemcalc-py/ext/velib_python'))
    from vedbus import VeDbusService
    from settingsdevice import SettingsDevice
    _HAVE_DBUS = True
except Exception:
    _HAVE_DBUS = False

    class _StubGLib:
        def timeout_add(self, ms, cb):
            def _loop():
                while True:
                    time.sleep(ms / 1000.0)
                    if not cb():
                        return
            threading.Thread(target=_loop, daemon=True).start()
        def MainLoop(self):
            return self
        def run(self):
            while True:
                time.sleep(3600)

    gobject = _StubGLib()

    class _StubSetting:
        def __init__(self, value):
            self._value = value
        def get_value(self):
            return self._value

    class SettingsDevice:
        def __init__(self, bus=None, supportedSettings=None, eventCallback=None):
            self._d = {}
        def addSetting(self, path, default, *args):
            self._d[path] = default
            return _StubSetting(default)
        def addSettings(self, settings):
            for key, (path, default, *_) in settings.items():
                self._d[key] = default
        def __getitem__(self, key):
            return self._d.get(key, '')
        def __setitem__(self, key, value):
            self._d[key] = value

    class VeDbusService:
        def __init__(self, service_name, bus=None, register=None):
            self._name = service_name
            self._paths = {}
        def add_path(self, path, value, gettextcallback=None, writeable=False, onchangecallback=None):
            self._paths[path] = value
        def register(self):
            log.info('Stub: registered %s', self._name)
        def __setitem__(self, path, value):
            old = self._paths.get(path)
            self._paths[path] = value
            if old != value:
                log.info('%-30s = %s', path, value)
        def __getitem__(self, path):
            return self._paths.get(path)

    def dbusconnection():
        return None

try:
    import thread
except ImportError:
    pass

log = logging.getLogger("DbusTrixingTemplate")

if _HAVE_DBUS:
    class SystemBus(dbus.bus.BusConnection):
        def __new__(cls):
            return dbus.bus.BusConnection.__new__(cls, dbus.bus.BusConnection.TYPE_SYSTEM)

    class SessionBus(dbus.bus.BusConnection):
        def __new__(cls):
            return dbus.bus.BusConnection.__new__(cls, dbus.bus.BusConnection.TYPE_SESSION)

    def dbusconnection():
        return SessionBus() if 'DBUS_SESSION_BUS_ADDRESS' in os.environ else SystemBus()



class Watchdog:
    def __init__(self, timeout=30):
        self.time = None
        self.timeout = timeout

    def update(self):
        self.time = time.time()

    def run(self):
        while True:
            if time.time() - self.time > self.timeout:
                log.error('Watchdog timeout')
                faulthandler.dump_traceback()
                os._exit(1)

            time.sleep(self.timeout)

    def start(self):
        self.update()
        t = threading.Thread(target=self.run)
        t.daemon = True
        t.start()


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
               serial=None, version=None, connection=None,
               register=None):
    servicename = 'com.victronenergy.' + deviceclass + '.' + devicename
    self.watchdog = Watchdog()
    self._deviceclass = deviceclass
    bus = dbusconnection()
    self._dbusservice = VeDbusService(servicename, bus=bus, register=register)
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
    try:
        procpath = os.path.join('/proc', str(os.getpid()), 'cmdline')
        cmd = [os.path.basename(c) for c in open(procpath).read().split('\x00')]
    except OSError:
        cmd = [os.path.basename(sys.argv[0])]
    log.info("Process Name %s", ' '.join(cmd))
    self._dbusservice.add_path('/Mgmt/ProcessName', ' '.join(cmd))
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

  def register(self):
    self._dbusservice.register()

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
        tb_str = ''.join(traceback.format_exception(exc, value=exc, tb=exc.__traceback__))

        log.error('Error running update, try %d: %s' % (self._retries, tb_str))
        self._retries += 1
        if self._retries == 12:
            self.disconnect()
    self.watchdog.update()
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

    self.trackers = 0
    self._dbusservice.add_path('/NrOfTrackers', self.trackers)
    self.add_power_paths()

  def add_tracker(self):
    n = self.trackers
    self._dbusservice.add_path('/Pv/%d/V' % n, None)
    self._dbusservice.add_path('/Pv/%d/P' % n, None)
    self._dbusservice.add_path('/Pv/%d/Name' % n, None)
    self._dbusservice.add_path('/Pv/%d/Yield/System' % n, None)
    self._dbusservice.add_path('/Pv/%d/MppOperationMode' % n, None)
    self.trackers += 1
    self['/NrOfTrackers'] = self.trackers


class DbusTrixingTemperature(DbusTrixingService):
  def __init__(self, devicename, **kwargs):
    super().__init__('temperature', devicename, register=False,
                     **kwargs)
    self._dbusservice.add_path('/TemperatureType', 2)  # 0=battery, 1=fridge, 2=generic
    self._dbusservice.add_path('/Temperature', None, gettextcallback=self._c)
    self._dbusservice.add_path('/Status', 0)  # 0=ok, 1=disconnected, 2=short circuit
    self.register()

  def set_temperature(self, temperature):
    self['/Temperature'] = temperature


class DbusTrixingEnergyMeter(DbusTrixingService):
  def __init__(self, devicename, role='acload', position=0, **kwargs):
    super().__init__(role, devicename,
                     **kwargs)
    # 1: AC-in, 0: AC-out
    self._dbusservice.add_path('/Position', position)
    # pvinverter, grid, acsensor
    role_names = ['grid', 'pvinverter', 'genset', 'acload']
    self._dbusservice.add_path('/Role', role)
    self._dbusservice.add_path('/AllowedRoles', role_names)
    self.add_power_paths()
    self.register()


class DbusTrixingHeatpump(DbusTrixingService):
  def __init__(self, devicename, **kwargs):
    super().__init__('heatpump', devicename,
                     **kwargs)
    # http://github.com/victronenergy/venus/wiki/dbus#heatpump
    self._dbusservice.add_path('/Temperature', None, gettextcallback=self._c)
    self._dbusservice.add_path('/TargetTemperature', None, gettextcallback=self._c)
    self._dbusservice.add_path('/State', None)
    self.register()

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

  if not _HAVE_DBUS:
    log.info('dbus/gi not available — running in stub mode')
    return

  try:
    thread.daemon = True
  except NameError:
    pass

  from dbus.mainloop.glib import DBusGMainLoop
  DBusGMainLoop(set_as_default=True)
  log.info('Early Setup complete')


def run():
  if _HAVE_DBUS:
    log.info('Connected to dbus, switching to gobject.MainLoop()')
  mainloop = gobject.MainLoop()
  mainloop.run()


