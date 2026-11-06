# sync.py - M3: semaforo y mutex propios con cola de espera, y el bufer
# circular productor-consumidor entre P1 (productor) y P2 (consumidor).
#
# Los procesos no tocan la cola de espera directamente: piden la operacion al
# kernel con yield (como una llamada al sistema) y el kernel decide si el
# proceso sigue o pasa a BLOQUEADO.  Uso dentro de un proceso:
#     yield sem.P            yield sem.V
#     yield m.tomar          yield m.soltar
# Las peticiones son tuplas creadas una sola vez: pedir un semaforo 100 veces
# por segundo no genera basura para el recolector.
from array import array


class Semaforo:
    def __init__(self, nombre, valor=0):
        self.nombre = nombre
        self.valor = valor
        self.cola = []          # PCBs bloqueados esperando el semaforo (FIFO)
        # Peticiones preasignadas: "yield sem.P" no crea objetos nuevos en el heap
        self.P = ('P', self)
        self.V = ('V', self)

    def __repr__(self):
        return 'Sem(%s=%d, cola=%d)' % (self.nombre, self.valor, len(self.cola))


class Mutex:
    """Cerrojo binario con dueno. 'orden' es su lugar en el orden global de
    cerrojos (prevencion de interbloqueo)."""

    def __init__(self, nombre, orden=0):
        self.nombre = nombre
        self.orden = orden
        self.dueno = None
        self.cola = []
        self.tomar = ('tomar', self)
        self.soltar = ('soltar', self)

    def __repr__(self):
        return 'Mutex(%s, dueno=%s)' % (self.nombre, self.dueno.nombre if self.dueno else '-')


def tomar_en_orden(*cerrojos):
    """Solucion al interbloqueo: todos toman los cerrojos en el mismo orden global."""
    for m in sorted(cerrojos, key=lambda c: c.orden):
        yield m.tomar


def soltar_todos(*cerrojos):
    for m in sorted(cerrojos, key=lambda c: c.orden, reverse=True):
        yield m.soltar


class BuferCircular:
    """Bufer circular de muestras (ax, ay, az) en int16.
    Memoria preasignada: un solo array('h') y un memoryview, sin objetos nuevos
    por muestra. Sincronizacion clasica productor-consumidor:
        huecos = semaforo contador de espacios libres (inicia en N)
        items  = semaforo contador de muestras disponibles (inicia en 0)
        mutex  = exclusion mutua sobre los indices"""

    def __init__(self, capacidad):
        self.capacidad = capacidad
        self.datos = array('h', bytes(2 * 3 * capacidad))
        self.vista = memoryview(self.datos)
        self.cabeza = 0     # donde escribe el productor
        self.cola = 0       # donde lee el consumidor
        self.huecos = Semaforo('huecos', capacidad)
        self.items = Semaforo('items', 0)
        self.mutex = Mutex('bufer', orden=0)

    def poner(self, ax, ay, az):
        i = 3 * self.cabeza
        self.datos[i] = ax
        self.datos[i + 1] = ay
        self.datos[i + 2] = az
        self.cabeza = (self.cabeza + 1) % self.capacidad

    def sacar_en(self, destino, pos):
        """Copia una muestra al arreglo destino (3 valores desde pos)."""
        i = 3 * self.cola
        destino[pos] = self.datos[i]
        destino[pos + 1] = self.datos[i + 1]
        destino[pos + 2] = self.datos[i + 2]
        self.cola = (self.cola + 1) % self.capacidad

    def ocupacion(self):
        return self.items.valor

    def nbytes(self):
        return len(self.datos) * 2
