# boot.py - Se ejecuta primero tras el startup code de MicroPython.
# Prepara el entorno para medir siempre en las mismas condiciones:
# frecuencia fija y radios (Wi-Fi y Bluetooth) apagados.
import machine

machine.freq(240000000)

try:
    import network
    network.WLAN(network.STA_IF).active(False)
    network.WLAN(network.AP_IF).active(False)
except Exception:
    pass

try:
    import bluetooth
    bluetooth.BLE().active(False)
except Exception:
    pass
