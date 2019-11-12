#!/usr/bin/env python3
import sys
import dbus
import time
import struct
import signal
import subprocess
import argparse
from xml.dom import minidom

def tryRunning(argv):
    try:
        subprocess.Popen(argv)
    except Exception as e:
        print("Error: %s" % str(e))

parser = argparse.ArgumentParser()
parser.add_argument("--hci", metavar="INT", type=int, default=0,
    help="use Bluetooth hciX (default 0, see `hcitool dev')")
parser.add_argument("--resolution", metavar="INT", type=int, default=20,
    help="polling rate (default 20 per second)")
args = parser.parse_args()
hciID = "hci%d" % args.hci
resolution = args.resolution

def signal_handler(sig, frame):
    sys.exit(0)
signal.signal(signal.SIGINT, signal_handler)

# https://developer.apple.com/library/archive/documentation/CoreBluetooth/Reference/AppleNotificationCenterServiceSpecification/Introduction/Introduction.html

notificationSource = None
controlPoint = None
dataSource = None
iphoneID = None

ancsID = "7905f431-b5ce-4e99-a40f-4b1e122d00d0"
notificationSourceID = "9fbf120d-6301-42d9-8c58-25e699a21dbd"
controlPointID = "69d1d8f3-45e1-49a8-9821-9bbdfdaad9d9"
dataSourceID = "22eac6e9-24d6-4bb5-be44-b36ace7c7bfb"

bus = dbus.SystemBus()
hci = bus.get_object("org.bluez", "/org/bluez/%s" % hciID)
devices = hci.Introspect()
devices = minidom.parseString(devices).getElementsByTagName("node")
devices = map(lambda x: x.getAttribute("name"), devices)
devices = list(filter(lambda x: x.startswith("dev_"), devices))
for deviceID in devices:
    device = bus.get_object("org.bluez", "/org/bluez/%s/%s" % (hciID, deviceID))
    props = dbus.Interface(device, "org.freedesktop.DBus.Properties")
    connected = props.Get("org.bluez.Device1", "Connected")
    if not connected:
        continue
    services = device.Introspect()
    services = minidom.parseString(services).getElementsByTagName("node")
    services = map(lambda x: x.getAttribute("name"), services)
    services = list(filter(lambda x: x.startswith("service"), services))
    for serviceID in services:
        service = bus.get_object("org.bluez", "/org/bluez/%s/%s/%s" % (hciID, deviceID, serviceID))
        props = dbus.Interface(service, "org.freedesktop.DBus.Properties")
        id = props.Get("org.bluez.GattService1", "UUID")
        if id != ancsID:
            continue
        print("Found an iPhone!")
        characteristics = service.Introspect()
        characteristics = minidom.parseString(characteristics).getElementsByTagName("node")
        characteristics = map(lambda x: x.getAttribute("name"), characteristics)
        characteristics = list(filter(lambda x: x.startswith("char"), characteristics))
        for characteristicID in characteristics:
            characteristic = bus.get_object("org.bluez", "/org/bluez/%s/%s/%s/%s" % (hciID, deviceID, serviceID, characteristicID))
            props = dbus.Interface(characteristic, "org.freedesktop.DBus.Properties")
            id = props.Get("org.bluez.GattCharacteristic1", "UUID")
            if id == notificationSourceID:
                notificationSource = characteristic
                iphoneID = deviceID
            elif id == controlPointID:
                controlPoint = characteristic
            elif id == dataSourceID:
                dataSource = characteristic

if notificationSource is None or controlPoint is None or dataSource is None:
    print("iPhone ANCS not found!")
    exit(1)

device = bus.get_object("org.bluez", "/org/bluez/%s/%s" % (hciID, iphoneID))
battery = dbus.Interface(device, "org.freedesktop.DBus.Properties")

nsControl = dbus.Interface(notificationSource, "org.bluez.GattCharacteristic1")
nsProps = dbus.Interface(notificationSource, "org.freedesktop.DBus.Properties")
cpControl = dbus.Interface(controlPoint, "org.bluez.GattCharacteristic1")
cpProps = dbus.Interface(controlPoint, "org.freedesktop.DBus.Properties")
dsControl = dbus.Interface(dataSource, "org.bluez.GattCharacteristic1")
dsProps = dbus.Interface(dataSource, "org.freedesktop.DBus.Properties")

nsControl.StartNotify()
dsControl.StartNotify()
nsLastMsg = nsProps.Get("org.bluez.GattCharacteristic1", "Value")
dsLastMsg = dsProps.Get("org.bluez.GattCharacteristic1", "Value")
batteryLast = battery.Get("org.bluez.Battery1", "Percentage")
while True:
    time.sleep(1.0 / resolution)

    msg = nsProps.Get("org.bluez.GattCharacteristic1", "Value")
    if msg != nsLastMsg:
        nsLastMsg = msg
        [op, _, _, _, id] = struct.unpack("<BBBBI", bytearray(msg))
        if op == 0:
            fetchAttributes = list(struct.pack(
                "<BIBBHBH", 0, id,
                0,         # app id
                1, 65535,  # title
                3, 65535,  # message
            ))
            print("New notification! Asking for details...")
            cpControl.WriteValue(fetchAttributes, {})

    msg = dsProps.Get("org.bluez.GattCharacteristic1", "Value")
    if msg != dsLastMsg:
        dsLastMsg = msg
        print("Notification details received!")
        msg = bytearray(msg)
        appIDSize, msg = struct.unpack("<BIBH", msg[:8])[3], msg[8:]
        appID, msg = msg[:appIDSize], msg[appIDSize:]
        titleSize, msg = struct.unpack("<BH", msg[:3])[1], msg[3:]
        title, msg = msg[:titleSize], msg[titleSize:]
        messageSize, msg = struct.unpack("<BH", msg[:3])[1], msg[3:]
        message, msg = msg[:messageSize], msg[messageSize:]

        appID = appID.decode('utf8', errors='ignore')
        title = title.decode('utf8', errors='ignore')
        message = message.decode('utf8', errors='ignore')
        print("From: %s (%s)" % (title, appID))
        print(message)
        tryRunning(['handlers/notification.sh', title, appID, message])

    batteryState = battery.Get("org.bluez.Battery1", "Percentage")
    if batteryState != batteryLast:
        batteryLast = batteryState
        print("Battery is at %d percent" % batteryState)
        tryRunning(['handlers/battery.sh', batteryState])
