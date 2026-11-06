# ============================================================================
# sensor.py - Acceso al hardware: el sensor DHT22 (temperatura y humedad).
# ----------------------------------------------------------------------------
# El DHT22 es un sensor DIGITAL que mide TEMPERATURA (-40 a 80 C) y HUMEDAD
# relativa (0 a 100 %). Usa un protocolo propio de un solo cable de datos, y por
# eso MicroPython ya trae el modulo "dht":
#     import dht
#     d = dht.DHT22(Pin(4))     # cable de datos del sensor en GPIO4
#     d.measure()               # dispara una medida (tarda unos pocos ms)
#     d.temperature()           # grados Celsius (numero con decimales)
#     d.humidity()              # humedad relativa (numero con decimales)
#
# LIMITACION IMPORTANTE: el DHT22 NO se puede leer a 100 Hz. El fabricante solo
# garantiza UNA lectura cada 2 segundos (0.5 Hz). Por eso este archivo guarda el
# ULTIMO valor bueno y solo vuelve a leer cada DHT_PERIODO_MS. P1 sigue corriendo
# a 100 Hz (cada 10 ms) pero el valor del sensor cambia cada 2 s: asi el kernel
# muestrea rapido y, cuando el ambiente cambia, ese cambio se ve en tiempo real.
#
# Cada muestra tiene 3 canales (asi el resto del sistema no cambia):
#   canal 0 = TEMPERATURA x10  (decimas de grado, entero)
#   canal 1 = HUMEDAD     x10  (decimas de porcentaje, entero)
#   canal 2 = CONFORT     x10  (indice de bochorno, decimas, entero)
# Se guardan multiplicados por 10 porque el bufer circular usa int16 (array 'h')
# y no admite decimales: 22.5 C se guarda como 225.
# ============================================================================
import math                                  # exp() para el calculo del confort
from compat import ticks_us, ticks_diff      # reloj del sistema

# Las 3 clases de ambiente que el modelo de M5 debe distinguir.
CLASES = ('frio', 'normal', 'calido')


# ------------------------------------------------------------------ confort
def confort(t, h):
    """Indice de bochorno (humidex): junta temperatura y humedad en un solo
    numero que dice "cuanto se siente" el ambiente. Formula estandar:
        e = 6.112 * exp(17.67*t/(t+243.5)) * (h/100)   (presion de vapor, hPa)
        confort = t + 0.5555 * (e - 10)
    Con mucha humedad el bochorno sube; con poca humedad baja."""
    e = 6.112 * math.exp(17.67 * t / (t + 243.5)) * (h / 100.0)   # presion de vapor
    return t + 0.5555 * (e - 10.0)                                # temperatura "sentida"


def clase_de(conf, cfg):
    """Convierte el confort en el indice de clase: 0=frio, 1=normal, 2=calido."""
    if conf < cfg.CONFORT_FRIO:        # por debajo del umbral de frio
        return 0
    if conf < cfg.CONFORT_CALIDO:      # entre frio y calido -> ambiente normal
        return 1
    return 2                           # por encima -> ambiente calido


# --------------------------------------------------------------- sensor REAL
class DHT:
    """Sensor REAL DHT22 en el pin indicado."""

    def __init__(self, pin, cfg):
        from machine import Pin         # solo existe dentro de la placa
        import dht                      # driver DHT de MicroPython
        self.cfg = cfg
        self.d = dht.DHT22(Pin(pin))    # creamos el sensor en ese GPIO
        self.t = 22.0                   # ultimo valor bueno de temperatura (C)
        self.h = 50.0                   # ultimo valor bueno de humedad (%)
        self.ult = ticks_us()           # instante de la ultima lectura real
        self.medir()                    # hacemos la primera lectura al arrancar

    def medir(self):
        """Hace UNA lectura del DHT22. Si falla (error de checksum o de tiempo)
        conserva el ultimo valor bueno: asi P1 nunca muere por un fallo puntual
        del sensor, que es justo lo que dejaba el sistema 'colgado'."""
        try:
            self.d.measure()               # dispara la conversion interna
            self.t = self.d.temperature()  # temperatura en grados Celsius
            self.h = self.d.humidity()     # humedad relativa en %
        except Exception:
            pass                          # nos quedamos con self.t y self.h

    def leer(self):
        """Devuelve (temp x10, hum x10, confort x10). Solo relee el sensor cuando
        ya pasaron DHT_PERIODO_MS desde la ultima lectura."""
        ahora = ticks_us()
        if ticks_diff(ahora, self.ult) >= self.cfg.DHT_PERIODO_MS * 1000:
            self.medir()                  # ya toca una lectura nueva
            self.ult = ahora              # anotamos cuando fue
        return (int(round(self.t * 10)), int(round(self.h * 10)),
                int(round(confort(self.t, self.h) * 10)))


# ------------------------------------------------------------------ fabrica
def abrir(cfg):
    """Devuelve el sensor DHT22 ya inicializado y su descripcion."""
    return DHT(cfg.PIN_DHT, cfg), 'DHT22 GPIO%d' % cfg.PIN_DHT
