# ============================================================================
# planificador.py - M2 · Algoritmos de planificacion (intercambiables).
# ----------------------------------------------------------------------------
# El planificador decide QUE proceso usa la CPU. Cada algoritmo responde dos
# preguntas que el kernel le hace:
#   elegir(listos, ahora)               -> de la cola de LISTOS, a quien le doy la CPU
#   debe_expulsar(pcb, ejecutado_us, listos, ahora)
#                                       -> el que corre, sigue o se lo quito ya
# "ahora" es el instante actual en ticks (unidades de tiempo del microcontrolador).
# ============================================================================
from compat import ticks_diff   # para restar ticks de forma segura (los ticks dan la vuelta)


class FCFS:
    """First-Come, First-Served (FIFO): el que llego primero se atiende primero.
    NO es apropiativo: quien toma la CPU la usa hasta terminar su rafaga."""

    nombre = 'FCFS'   # etiqueta corta que aparece en la consola y el CSV

    # De la cola de LISTOS, escoge el que llego antes (el primero de la lista).
    def elegir(self, listos, ahora):
        return listos[0] if listos else None   # si la cola esta vacia -> None

    # FCFS nunca interrumpe a nadie: siempre devuelve False.
    def debe_expulsar(self, pcb, ejecutado_us, listos, ahora):
        return False

    # Texto descriptivo del algoritmo.
    def descripcion(self):
        return 'FCFS'


class RoundRobin:
    """Round Robin (RR): cola circular; cada proceso recibe igual porcion de
    tiempo (el "quantum"). SI es apropiativo: si se acaba el quantum, se va al
    final de la cola y le toca a otro."""

    nombre = 'RR'

    def __init__(self, quantum_ms):
        # Guardamos el quantum convertido a microsegundos (1 ms = 1000 us).
        self.quantum_us = int(quantum_ms * 1000)

    def elegir(self, listos, ahora):
        return listos[0] if listos else None   # RR tambien atiende en orden de llegada

    # Se expulsa si HAY alguien mas esperando Y ya se consumio el quantum.
    def debe_expulsar(self, pcb, ejecutado_us, listos, ahora):
        return bool(listos) and ejecutado_us >= self.quantum_us

    def descripcion(self):
        return 'RR(q=%dms)' % (self.quantum_us // 1000)   # ej. 'RR(q=5ms)'


class PrioridadesEnvejecimiento:
    """Prioridades apropiativo (1 = mayor prioridad, como en clase) CON
    envejecimiento: cada cierto tiempo que un proceso espera en LISTO, su
    prioridad sube un nivel. Asi evitamos que un proceso se muera de hambre."""

    nombre = 'PRIO'

    def __init__(self, envejecimiento_ms):
        # Intervalo en microsegundos que debe esperar un proceso para "envejecer".
        self.intervalo_us = int(envejecimiento_ms * 1000)

    # Recalcula la prioridad efectiva de cada proceso LISTO segun lo que lleva esperando.
    def _envejecer(self, listos, ahora):
        for p in listos:   # recorremos cada proceso en la cola de listos
            # cuantos "intervalos" completos lleva esperando desde que entro a LISTO
            niveles = ticks_diff(ahora, p.listo_desde) // self.intervalo_us
            # a mayor espera, menor numero -> mayor prioridad. Nunca baja de 1.
            p.prioridad_efectiva = max(1, p.prioridad - niveles)

    def elegir(self, listos, ahora):
        if not listos:
            return None
        self._envejecer(listos, ahora)     # primero actualizamos prioridades
        mejor = listos[0]                  # candidato inicial
        for p in listos:                   # buscamos el de mayor prioridad (numero menor)
            if p.prioridad_efectiva < mejor.prioridad_efectiva:
                mejor = p                  # si empatan, gana el que ya estaba primero (mas antiguo)
        return mejor

    def debe_expulsar(self, pcb, ejecutado_us, listos, ahora):
        if not listos:
            return False                   # no hay nadie esperando -> que siga
        self._envejecer(listos, ahora)
        for p in listos:                   # si alguien esperando ya es mas urgente que el que corre...
            if p.prioridad_efectiva < pcb.prioridad:
                return True                # ...lo expulsamos
        return False


    def descripcion(self):
        return 'PRIO(env=%dms)' % (self.intervalo_us // 1000)   # ej. 'PRIO(env=50ms)'


class SJF:
    """Shortest Job First (no apropiativo). Como no sabemos la rafaga real de
    antemano, la ESTIMAMOS con un promedio exponencial:
        tau(n+1) = alfa * t(n) + (1 - alfa) * tau(n)
    (tau = estimacion; t = ultima rafaga medida)."""

    nombre = 'SJF'

    def __init__(self, alfa):
        self.alfa = alfa   # peso de la ultima rafaga (0..1)

    def elegir(self, listos, ahora):
        if not listos:
            return None
        mejor = listos[0]                  # candidato inicial
        for p in listos:
            if p.tau < mejor.tau:          # el de rafaga ESTIMADA mas corta
                mejor = p
        return mejor

    def debe_expulsar(self, pcb, ejecutado_us, listos, ahora):
        return False                       # SJF no apropiativo: no interrumpe

    # Al terminar una rafaga se actualiza la estimacion del proceso.
    def actualizar(self, pcb, rafaga_us):
        pcb.tau = self.alfa * rafaga_us + (1 - self.alfa) * pcb.tau

    def descripcion(self):
        return 'SJF(a=%.2f)' % self.alfa  # ej. 'SJF(a=0.50)'


class HRRN:
    """Highest Response Ratio Next (no apropiativo).
    Elige al de mayor "razon de respuesta" = (espera + rafaga_estimada) / rafaga_estimada.
    Favorece a los que llevan mucho esperando (evita inanicion) sin ignorar rafagas cortas."""

    nombre = 'HRRN'

    def __init__(self, alfa):
        self.alfa = alfa   # para estimar la rafaga (misma idea que SJF)

    def elegir(self, listos, ahora):
        if not listos:
            return None
        mejor, mejor_razon = None, -1      # -1 para que cualquier razon real lo supere
        for p in listos:
            espera = ticks_diff(ahora, p.listo_desde)   # lo que lleva esperando
            razon = (espera + p.tau) / p.tau            # formula de HRRN
            if razon > mejor_razon:
                mejor, mejor_razon = p, razon
        return mejor

    def debe_expulsar(self, pcb, ejecutado_us, listos, ahora):
        return False                       # HRRN no apropiativo

    def actualizar(self, pcb, rafaga_us):
        pcb.tau = self.alfa * rafaga_us + (1 - self.alfa) * pcb.tau

    def descripcion(self):
        return 'HRRN'


# Algoritmos que el modo AUTO prueba por turnos (el orden no importa).
CANDIDATOS_AUTO = ('FCFS', 'RR', 'PRIO', 'SJF', 'HRRN')


class Adaptativo:
    """MODO AUTO (meta-planificador).
    En una sola CPU no pueden mandar varios algoritmos a la vez, asi que en vez de
    eso partimos el tiempo en EPOCAS: en cada epoca usa un algoritmo distinto y
    mide su desempeno; cuando ya probo todos, se queda con el que mejor puntuo.
    "Mejor" = menos plazos incumplidos de P1 y, en empate, menos cambios de contexto.
    """

    # Constructor: recibe que algoritmos probar y los parametros de siempre.
    def __init__(self, nombres=None, quantum_ms=5, alfa=0.5, envejecimiento_ms=50, epoca_ms=3000):
        self.nombre = 'AUTO'                   # nombre corto del planificador
        self.nombres = list(nombres or CANDIDATOS_AUTO)   # a quienes vamos a probar
        # Creamos un objeto por cada candidato (cada uno ya sabe planificar solo).
        self.sub = [crear(n, quantum_ms, alfa, envejecimiento_ms) for n in self.nombres]
        self.epoca_us = int(epoca_ms) * 1000   # cuanto dura cada prueba (us)
        self.kernel = None                     # lo llena el kernel con conectar()
        self.i = 0                             # candidato que estamos probando ahora
        self.t_epoca = None                    # instante en que empezo la epoca actual
        self.marca_plazos = 0                  # plazos de P1 al empezar la epoca
        self.marca_ctx = 0                     # cambios de contexto al empezar la epoca
        self.puntajes = {}                     # nombre -> (plazos, cambios_ctx)
        self.elegido = None                    # indice del ganador (cuando ya se decidio)

    # El kernel nos pasa su referencia para poder leer sus metricas.
    def conectar(self, kernel):
        self.kernel = kernel
        self.t_epoca = None                    # la proxima eleccion reinicia la epoca

    # Lee las dos metricas con las que puntuamos a cada algoritmo.
    def _metricas(self):
        plazos = self.kernel.procesos[0].plazos_incumplidos if (self.kernel and self.kernel.procesos) else 0
        ctx = self.kernel.cambios_contexto if self.kernel else 0
        return plazos, ctx

    # Cierra la epoca actual: guarda cuanto costo (plazos y contextos) este candidato.
    def _cerrar_epoca(self):
        plazos, ctx = self._metricas()         # como estan las metricas ahora
        d = (plazos - self.marca_plazos, ctx - self.marca_ctx)   # lo que costo esta epoca
        nombre = self.nombres[self.i]          # el candidato que acaba de terminar
        antes = self.puntajes.get(nombre)      # su mejor resultado anterior (si lo hay)
        if antes is None or d < antes:         # nos quedamos con su mejor epoca
            self.puntajes[nombre] = d
        self.kernel.registrar('AUTO: %s -> %d plazos, %d cambios de contexto' % (nombre, d[0], d[1]))

    # ¿Ya termino la epoca actual? Si si, cerramos una y arrancamos la siguiente.
    def _avanzar(self, ahora):
        if self.kernel is None:                # todavia no nos conectaron
            return
        if self.elegido is not None:           # ya decidimos el ganador: usamos ese y ya
            return
        if self.t_epoca is None:               # primera vez: arrancamos la primera epoca
            self.t_epoca = ahora
            self.marca_plazos, self.marca_ctx = self._metricas()
            return
        if ticks_diff(ahora, self.t_epoca) < self.epoca_us:
            return                             # la epoca sigue: no cambiamos nada
        self._cerrar_epoca()                   # la epoca termino: la puntuamos
        if len(self.puntajes) >= len(self.nombres):   # ¿ya probamos a todos?
            # Elegimos el de menor puntaje (menos plazos/contextos = mejor).
            self.elegido = min(range(len(self.nombres)),
                               key=lambda j: self.puntajes[self.nombres[j]])
            self.kernel.registrar('AUTO: el mejor es %s' % self.nombres[self.elegido])
        else:                                  # aun faltan por probar: pasamos al siguiente
            self.i = (self.i + 1) % len(self.nombres)
        self.t_epoca = ahora                   # la epoca nueva empieza ahora
        self.marca_plazos, self.marca_ctx = self._metricas()

    # Devuelve el algoritmo que debe planificar en este instante.
    def _actual(self):
        return self.sub[self.elegido if self.elegido is not None else self.i]

    # El kernel pregunta a quien darle la CPU: se lo delegamos al algoritmo actual.
    def elegir(self, listos, ahora):
        self._avanzar(ahora)
        return self._actual().elegir(listos, ahora)

    # El kernel pregunta si hay que expulsar: tambien lo decide el algoritmo actual.
    def debe_expulsar(self, pcb, ejecutado_us, listos, ahora):
        self._avanzar(ahora)
        return self._actual().debe_expulsar(pcb, ejecutado_us, listos, ahora)

    # SJF/HRRN actualizan su estimacion (tau): se la pasamos al algoritmo actual.
    def actualizar(self, pcb, rafaga_us):
        a = self._actual()
        if hasattr(a, 'actualizar'):
            a.actualizar(pcb, rafaga_us)

    # Texto para la tabla de estados: que esta probando o cual gano.
    def descripcion(self):
        if self.elegido is not None:
            return 'AUTO->%s' % self.nombres[self.elegido]
        return 'AUTO(probando %s)' % self.nombres[self.i]


# "Fabrica": dado un nombre de texto, devuelve el objeto del algoritmo listo para usar.
def crear(nombre, quantum_ms=5, alfa=0.5, envejecimiento_ms=50, candidatos=None, epoca_ms=3000):
    nombre = nombre.upper()                 # aceptamos 'fcfs' o 'FCFS' (sin importar mayusculas)
    if nombre == 'FCFS':
        return FCFS()
    if nombre == 'RR':
        return RoundRobin(quantum_ms)
    if nombre == 'PRIO':
        return PrioridadesEnvejecimiento(envejecimiento_ms)
    if nombre == 'SJF':
        return SJF(alfa)
    if nombre == 'HRRN':
        return HRRN(alfa)
    if nombre == 'AUTO':                     # meta-planificador: prueba y elige el mejor
        return Adaptativo(candidatos, quantum_ms, alfa, envejecimiento_ms, epoca_ms)
    raise ValueError('algoritmo desconocido: ' + nombre)   # si escriben mal, avisamos
