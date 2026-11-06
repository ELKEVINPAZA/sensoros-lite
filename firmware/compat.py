# ============================================================================
# compat.py - Capa de COMPATIBILIDAD (traductor).
# ----------------------------------------------------------------------------
# Permite que el MISMO codigo del kernel corra en dos lugares:
#   - en el ESP32 (MicroPython): usa los modulos reales del hardware.
#   - en el PC (CPython): emula lo minimo para poder probar sin la placa.
# ============================================================================
import gc
import time

try:
    # En el ESP32 (MicroPython) existen estas funciones de tiempo con "ticks".
    from time import ticks_us, ticks_ms, ticks_diff, ticks_add, sleep_us
    EN_PLACA = True                      # bandera: estamos en la placa de verdad
except ImportError:
    EN_PLACA = False                     # estamos en el PC (no existen los ticks)
    # Replicamos el comportamiento de MicroPython: los ticks dan la vuelta
    # (periodo 2^30). Por eso SIEMPRE se comparan con ticks_diff() y NUNCA se
    # restan directamente (si no, al dar la vuelta saldria un numero enorme).
    _PERIODO = 1 << 30                   # 2 elevado a 30 (el ciclo de los ticks)
    _MITAD = _PERIODO >> 1               # la mitad del periodo (2^29)
    _T0 = time.perf_counter_ns()         # instante inicial (reloj muy preciso)

    # Microsegundos desde el arranque (con vuelta cada 2^30).
    def ticks_us():
        return ((time.perf_counter_ns() - _T0) // 1000) & (_PERIODO - 1)

    # Milisegundos desde el arranque (con vuelta cada 2^30).
    def ticks_ms():
        return ((time.perf_counter_ns() - _T0) // 1000000) & (_PERIODO - 1)

    # Diferencia entre dos ticks, contemplando la vuelta (devuelve positivo o negativo).
    def ticks_diff(fin, inicio):
        d = (fin - inicio) & (_PERIODO - 1)   # diferencia "modulo" (siempre positiva)
        return d - _PERIODO if d >= _MITAD else d   # si paso de la mitad, es negativo

    # Suma un delta a un tick (con vuelta).
    def ticks_add(t, delta):
        return (t + delta) & (_PERIODO - 1)

    # Dormir en microsegundos (time.sleep trabaja en segundos).
    def sleep_us(us):
        time.sleep(us / 1000000)

# gc.mem_free()/gc.mem_alloc() existen solo en MicroPython. En el PC se
# aproximan con tracemalloc (un "medidor" de memoria de Python).
if hasattr(gc, 'mem_free'):
    mem_free = gc.mem_free           # en la placa: las funciones reales
    mem_alloc = gc.mem_alloc
else:
    import tracemalloc               # en el PC: medidor de memoria
    tracemalloc.start()
    _HEAP_VIRTUAL = 4 * 1024 * 1024  # suponemos 4 MB de heap virtual (los objetos de
                                     # CPython ocupan mucho mas que en MicroPython)

    # Cuanta memoria esta usando el programa ahora (en el PC).
    def mem_alloc():
        return tracemalloc.get_traced_memory()[0]

    # Cuanta memoria "libre" queda (heap virtual menos lo usado).
    def mem_free():
        return max(0, _HEAP_VIRTUAL - mem_alloc())


# Imprime una excepcion de forma compatible en placa y en PC.
def imprimir_excepcion(e):
    try:
        import sys
        sys.print_exception(e)       # en MicroPython
    except AttributeError:
        import traceback             # en CPython
        traceback.print_exception(e)
