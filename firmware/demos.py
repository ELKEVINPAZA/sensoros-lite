# demos.py - Demostraciones en vivo de M3 (carrera e interbloqueo) y M4 (OOM-killer).
# Desde el REPL de la placa:
#     import demos
#     demos.carrera()          # antes (sin mutex) y despues (con mutex)
#     demos.interbloqueo()     # antes (orden opuesto) y despues (orden global)
#     demos.oom()              # un proceso excede su cuota y el sistema sigue
import config
from kernel import Kernel
from sync import Mutex, tomar_en_orden, soltar_todos


class Cfg:
    """Copia de config.py para que cada demo use su propio kernel."""

    def __init__(self, **cambios):
        for nombre in dir(config):
            if nombre.isupper():
                setattr(self, nombre, getattr(config, nombre))
        self.CONSOLA = False            # las demos no leen comandos del serial
        self.MEDIR_MEM_CADA = 1         # medir la RAM en cada despacho
        for k, v in cambios.items():
            setattr(self, k, v)


# ------------------------------------------------------------- condicion de carrera
class Contador:
    valor = 0


def _incrementar_sin_proteccion(k, yo, c, veces):
    for _ in range(veces):
        leido = c.valor          # 1) leer
        yield ('ceder',)         #    el kernel cambia de proceso entre leer y escribir
        c.valor = leido + 1      # 2) escribir: pisa lo que escribio el otro proceso


def _incrementar_con_mutex(k, yo, c, veces, m):
    for _ in range(veces):
        yield m.tomar      # seccion critica protegida
        leido = c.valor
        yield ('ceder',)         # el mismo yield, pero el otro proceso no puede entrar
        c.valor = leido + 1
        yield m.soltar


def carrera(veces=100):
    print('\n=== M3 Condicion de carrera: 2 procesos x %d incrementos ===' % veces)
    for corregido in (False, True):
        k = Kernel(Cfg(ALGORITMO='RR', QUANTUM_MS=1))
        c = Contador()
        m = Mutex('contador')
        for nombre in ('A', 'B'):
            if corregido:
                k.admitir(k.crear(nombre, _incrementar_con_mutex, 1, 2, c, veces, m))
            else:
                k.admitir(k.crear(nombre, _incrementar_sin_proteccion, 1, 2, c, veces))
        k.ejecutar()
        print('%-22s esperado=%d obtenido=%d  -> %s' % (
            'DESPUES (con mutex):' if corregido else 'ANTES (sin mutex):',
            2 * veces, c.valor, 'correcto' if c.valor == 2 * veces else 'CARRERA: se perdieron %d' % (2 * veces - c.valor)))


# -------------------------------------------------------------------- interbloqueo
def _p4(k, yo, fs, estado, ordenado):
    # P4 Registro: necesita el archivo (fs) y el estado de la clasificacion
    if ordenado:
        yield from tomar_en_orden(fs, estado)
    else:
        yield fs.tomar
        print('  P4 tiene fs, pide estado')
        yield ('ceder',)
        yield estado.tomar
    print('  P4 escribe el registro')
    yield from soltar_todos(fs, estado)


def _p6(k, yo, fs, estado, ordenado):
    # P6 Monitor: toma los mismos cerrojos en el orden opuesto (o en el global)
    if ordenado:
        yield fs.tomar              # orden global: primero fs (1), luego estado (2)
        print('  P6 tiene fs, pide estado')
        yield ('ceder',)
        yield estado.tomar
    else:
        yield estado.tomar
        print('  P6 tiene estado, pide fs')
        yield ('ceder',)
        yield fs.tomar
    print('  P6 escribe el CSV')
    yield from soltar_todos(fs, estado)


def interbloqueo():
    print('\n=== M3 Interbloqueo: P4 y P6 toman dos cerrojos ===')
    for ordenado in (False, True):
        print('ANTES (orden opuesto):' if not ordenado else 'DESPUES (orden global fs -> estado):')
        k = Kernel(Cfg(ALGORITMO='RR', QUANTUM_MS=1, ORDEN_GLOBAL=ordenado))
        fs = Mutex('fs', orden=1)
        estado = Mutex('estado', orden=2)
        p4 = k.admitir(k.crear('P4 Registro', _p4, 3, 8, fs, estado, ordenado))
        p6 = k.admitir(k.crear('P6 Monitor', _p6, 4, 4, fs, estado, ordenado))
        k.ejecutar()
        print('  interbloqueos detectados=%d | P4: %s (%s) | P6: %s (%s)' % (
            k.interbloqueos, p4.estado, p4.motivo_fin, p6.estado, p6.motivo_fin))


# --------------------------------------------------------------------- OOM-killer
def _gloton(k, yo, bloque):
    """Proceso defectuoso: reserva memoria sin liberarla."""
    total = 0
    while True:
        buf = yield ('reservar', bloque)
        total += len(buf)
        print('  Gloton lleva %d B (cuota %d B)' % (total, yo.cuota))
        yield ('dormir', 20)


def _trabajador(k, yo, ciclos):
    for i in range(ciclos):
        yield ('dormir', 20)
    print('  Trabajador termino sus %d ciclos: el sistema siguio funcionando' % ciclos)


def _gloton_lista(k, yo):
    """Asigna objetos en una sola rafaga hasta pasar su cuota (lo mide gc.mem_alloc)."""
    yield ('dormir', 30)
    basura = []
    for i in range(400):
        basura.append(bytearray(64))
    yield ('dormir', 20)


def _gloton_enorme(k, yo):
    """Pide de golpe mas RAM de la que tiene la placa: MicroPython lanza MemoryError."""
    yield ('dormir', 50)
    enorme = bytearray(1024 * 1024)  # noqa: F841
    yield ('dormir', 20)


def oom():
    print('\n=== M4 Politica ante falta de memoria (OOM-killer) ===')
    k = Kernel(Cfg(ALGORITMO='PRIO'))
    g = k.admitir(k.crear('Gloton', _gloton, 2, 2, 512))           # cuota 2 KB
    h = k.admitir(k.crear('Gloton2', _gloton_lista, 2, 4))          # cuota 4 KB
    e = k.admitir(k.crear('Gloton3', _gloton_enorme, 2, 16))        # cuota 16 KB
    t = k.admitir(k.crear('Trabajador', _trabajador, 1, 2, 15))
    k.ejecutar()
    for p in (g, h, e, t):
        print('  %-10s %-10s %s' % (p.nombre, p.estado, p.motivo_fin))
    print('  procesos terminados por OOM-killer: %d' % k.oom_kills)


def todas():
    carrera()
    interbloqueo()
    oom()


if __name__ == '__main__':
    todas()
