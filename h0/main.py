# h0/main.py - Puesta en marcha (H0): parpadea el LED y lee el DHT22 (GPIO4).
# El DHT22 mide temperatura (C) y humedad (%), y de ahi calculamos el confort.
# Correr sin guardar:   python pc/placa.py h0 COM3
# Guardar como main.py: python pc/placa.py h0-guardar COM3
import os
import gc
import time
import math
import machine
from machine import Pin
import dht

print('uname:', os.uname())
print('freq :', machine.freq(), 'Hz')
print('RAM libre:', gc.mem_free(), 'B')

led = Pin(2, Pin.OUT)             # LED integrado
sensor = dht.DHT22(Pin(4))        # pin de DATOS del DHT22 -> GPIO4


def confort(t, h):
    """Indice de bochorno: combina temperatura y humedad (igual que firmware/sensor.py)."""
    e = 6.112 * math.exp(17.67 * t / (t + 243.5)) * (h / 100.0)
    return t + 0.5555 * (e - 10.0)


print('Leyendo el DHT22 durante 20 s (el sensor solo da un valor nuevo cada 2 s)')
for i in range(10):
    try:
        sensor.measure()                 # dispara una lectura
        t = sensor.temperature()         # temperatura en grados Celsius
        h = sensor.humidity()            # humedad relativa en %
        print('t=%4.1f s  T=%5.1f C  H=%5.1f %%  confort=%5.1f' % (i * 2, t, h, confort(t, h)))
    except Exception as e:
        print('t=%4.1f s  error de lectura: %s' % (i * 2, e))
    led.value(not led.value())           # parpadeo del LED en cada lectura
    time.sleep(2)                        # esperamos los 2 s que pide el DHT22

led.value(0)
print('H0 listo')
