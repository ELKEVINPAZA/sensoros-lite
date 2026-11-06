# ============================================================================
# kernel.py - EL CORAZON de SensorOS-Lite (modulos M1 a M4).
# ----------------------------------------------------------------------------
# Idea central: cada proceso es un GENERADOR de Python. Cuando el proceso hace
# "yield", le devuelve el control al kernel (como si fuera una interrupcion).
# El kernel guarda el estado en el PCB, pregunta al planificador a quien le
# toca, y reanuda ese generador con send(): eso ES el cambio de contexto.
#
# Lo que un proceso puede pedir con yield (llamadas al sistema):
#   yield                    -> punto de cesion: el planificador puede expulsarlo
#   yield ('ceder',)         -> cede la CPU voluntariamente (vuelve a LISTO)
#   yield ('dormir', ms)     -> pasa a BLOQUEADO durante ms milisegundos
#   yield ('periodo', ms)    -> BLOQUEADO hasta su proxima activacion periodica
#   yield ('P', sem) / ('V', sem) / ('tomar', m) / ('soltar', m)   (sync.py)
#   yield ('reservar', n)    -> pide n bytes contra su cuota de memoria (M4)
#   yield ('liberar', buf)   -> devuelve un buffer
# ============================================================================
import gc                      # recolector de basura (liberar memoria)
import math                    # sqrt() para la desviacion estandar (jitter)
import compat                  # capa que hace correr el kernel en PC o en la placa
# Traemos funciones de compat.py directamente para escribir menos:
from compat import ticks_us, ticks_diff, ticks_add, sleep_us, mem_free, mem_alloc
# Del PCB traemos la clase y los estados que usaremos:
from pcb import PCB, LISTO, EJECUCION, BLOQUEADO, TERMINADO
import planificador            # los algoritmos de planificacion (M2)
from sync import Mutex         # el cerrojo con dueno (para detectar interbloqueos)


# Excepcion propia: se lanza cuando se detecta un interbloqueo (M3).
class Interbloqueo(Exception):
    pass   # no necesita nada mas: solo sirve para distinguirla de otros errores


class Kernel:
    # Constructor: prepara las estructuras internas del kernel.
    def __init__(self, cfg):
        self.cfg = cfg              # guardamos la configuracion (config.py)
        self.procesos = []          # la "tabla de procesos": lista de todos los PCBs
        self.listos = []            # cola de procesos LISTOS (esperan la CPU)
        self.dormidos = []          # procesos BLOQUEADOS por tiempo (dormir/periodo)
        self.actual = None          # el proceso que esta en EJECUCION ahora mismo
        self.siguiente_pid = 1      # numero que tendra el proximo proceso creado
        # Creamos el planificador segun lo pida config.py (FCFS, RR, PRIO, ...).
        self.planificador = planificador.crear(cfg.ALGORITMO, cfg.QUANTUM_MS,
                                               cfg.ALFA, cfg.ENVEJECIMIENTO_MS,
                                               None, getattr(cfg, 'EPOCA_AUTO_MS', 3000))
        # Si el planificador es el modo AUTO, necesita referencia al kernel para
        # leer sus metricas durante la ejecucion (igual que al cambiar en vivo).
        if hasattr(self.planificador, 'conectar'):
            self.planificador.conectar(self)
        self.corriendo = False      # bandera: True mientras el ciclo principal corre
        self.bitacora = []          # ultimos eventos (para mostrar en la consola)
        # Cada cuantos despachos medimos la RAM (medir memoria es costoso).
        self.medir_cada = getattr(cfg, 'MEDIR_MEM_CADA', 1)
        self.reiniciar_metricas()   # pone todos los contadores a cero
        # Traza de cambios de estado: si esta encendida, el kernel imprime en vivo
        # cada cambio de estado (para que se VEA en tiempo real). Se prende/apaga
        # con la tecla "t" de la consola. Se limita a ~66 lineas/s (1 cada 15 ms)
        # para que no inunde el serial.
        self.traza = False          # ¿imprimir los cambios de estado en vivo?
        # Instante de la ultima linea impresa. Se inicializa con el tick ACTUAL
        # (nunca con 0: los ticks dan la vuelta y ticks_diff(t, 0) podria salir
        # negativo, y entonces la traza no imprimiria nada).
        self.traza_t = ticks_us()
        # Crea la consola serial (leer teclas) solo si esta activada en config.py.
        self.consola = _Consola() if getattr(cfg, 'CONSOLA', True) else None

    # ------------------------------------------------------------------ metricas
    # Pone en cero todos los contadores de tiempo y de los procesos.
    def reiniciar_metricas(self):
        self.t_inicio = ticks_us()  # instante de referencia para medir el tiempo total
        self.t_util = 0             # CPU gastada DENTRO de los procesos (trabajo util)
        self.t_planificacion = 0    # CPU gastada en el kernel (elegir, cambiar de contexto...)
        self.t_ocioso = 0           # CPU sin nada que hacer
        self.vueltas = 0            # vueltas del bucle principal (prueba de que corre)
        self.cambios_contexto = 0   # cuantas veces se cambio de proceso
        self.gc_pausas = 0          # cuantas veces corrio el recolector de basura
        self.gc_us_total = 0        # tiempo total perdido en recoleccion (us)
        self.gc_us_max = 0          # la pausa de GC mas larga (us)
        self.ram_libre_min = mem_free()   # minima RAM libre vista (para ver el peor caso)
        self.interbloqueos = 0      # cuantos interbloqueos se detectaron
        self.oom_kills = 0          # cuantos procesos se mataron por pasarse de RAM
        # --- Jitter de P1: que tan tarde se activa P1 respecto a su ideal ---
        self.j_n = 0                # cuantas muestras de jitter llevamos
        self.j_suma = 0             # suma de los retrasos (para el promedio)
        self.j_suma2 = 0            # suma de los retrasos al cuadrado (para la desviacion)
        self.j_max = 0              # el retraso maximo visto
        for p in self.procesos:     # reiniciamos tambien los contadores de cada proceso
            p.trabajos = p.cpu_us = p.despachos = p.expulsiones = 0
            p.suma_espera = p.suma_retorno = p.suma_respuesta = 0
            p.plazos_incumplidos = 0
            p.veces_listo = p.veces_bloq = 0        # cambios de estado contados
            p.ult_prev = p.estado                   # de donde venia su ultimo cambio
            p.ult_cambio_t = self.t_inicio          # instante del ultimo cambio

    # Eficiencia = trabajo util / (trabajo util + costo de planificar).
    def eficiencia(self):
        total = self.t_util + self.t_planificacion
        return self.t_util / total if total else 0

    # Uso de CPU = trabajo util / tiempo total transcurrido.
    def cpu_util(self):
        total = ticks_diff(ticks_us(), self.t_inicio)
        return self.t_util / total if total > 0 else 0

    # Devuelve (promedio, desviacion estandar, maximo) del jitter de P1, en us.
    def jitter_p1(self):
        if not self.j_n:
            return 0, 0, 0
        media = self.j_suma / self.j_n                          # promedio
        var = max(0, self.j_suma2 / self.j_n - media * media)   # varianza (nunca negativa)
        return media, math.sqrt(var), self.j_max                # raiz de la varianza = desv.

    # Cuenta total de plazos incumplidos de todos los procesos.
    def plazos_incumplidos(self):
        return sum(p.plazos_incumplidos for p in self.procesos)

    # Anota un mensaje en la bitacora y lo imprime con el prefijo [kernel].
    def registrar(self, texto):
        self.bitacora.append(texto)
        if len(self.bitacora) > 20:      # solo guardamos los ultimos 20 eventos
            self.bitacora.pop(0)
        print('[kernel]', texto)

    # ---------------------------------------------------------- gestion de procesos
    # Crea un proceso: arma su PCB y lo agrega a la tabla (aun en estado NUEVO).
    def crear(self, nombre, funcion, prioridad, cuota_kb, *args):
        p = PCB(self.siguiente_pid, nombre, funcion, args, prioridad, cuota_kb,
                self.cfg.TAU_INICIAL_US)
        self.siguiente_pid += 1     # el proximo proceso tendra otro numero
        self.procesos.append(p)
        return p                    # devolvemos el proceso recien creado

    # Admite un proceso: pasa de NUEVO a LISTO. Aqui se crea el generador.
    def admitir(self, p):
        p.contexto = p.funcion(self, p, *p.args)   # crear el generador = crear contexto
        self._a_listo(p, ticks_us())               # lo ponemos en la cola de listos
        return p

    # Busca un proceso por el comienzo de su nombre (ej. 'P3' encuentra 'P3 Modelo').
    def buscar(self, nombre):
        for p in self.procesos:
            if p.nombre.startswith(nombre):
                return p
        return None

    # Cambia el algoritmo de planificacion EN VIVO (lo usan las teclas f, r, p...).
    def cambiar_algoritmo(self, nombre, quantum_ms=None):
        if quantum_ms is not None:              # si pidieron un quantum nuevo (tecla "r 10")
            self.cfg.QUANTUM_MS = quantum_ms
        self.planificador = planificador.crear(nombre, self.cfg.QUANTUM_MS,
                                               self.cfg.ALFA, self.cfg.ENVEJECIMIENTO_MS,
                                               None, getattr(self.cfg, 'EPOCA_AUTO_MS', 3000))
        # El modo AUTO (meta-planificador) necesita una referencia al kernel para
        # poder leer las metricas en vivo (plazos incumplidos, cambios de contexto).
        if hasattr(self.planificador, 'conectar'):
            self.planificador.conectar(self)
        self.cfg.ALGORITMO = self.planificador.nombre   # dejamos constancia del cambio
        self.reiniciar_metricas()               # empezamos a medir de nuevo desde cero
        self.registrar('planificador -> ' + self.planificador.descripcion())  # avisamos

    # ------------------------------------------------- cambios de estado (M1)
    # Punto UNICO por el que pasa TODO cambio de estado de un proceso. Ademas de
    # poner el estado nuevo, lleva la cuenta de cuantas veces entro a cada estado
    # y cuando fue su ultimo cambio: eso es lo que permite DEMOSTRAR en la tabla
    # que un proceso sigue vivo aunque en una foto salga BLOQUEADO.
    def _cambiar_estado(self, p, nuevo, ahora=None):
        if p.estado == nuevo:               # si el estado no cambia, no hay nada que contar
            return
        prev = p.estado                     # el estado del que venia
        p.ult_prev = prev                   # lo guardamos para mostrarlo luego
        p.estado = nuevo                    # aplicamos el estado nuevo
        t = ticks_us() if ahora is None else ahora
        p.ult_cambio_t = t                  # instante del cambio
        if nuevo == LISTO:                  # contamos las entradas a cada estado
            p.veces_listo += 1
        elif nuevo == BLOQUEADO:
            p.veces_bloq += 1
        # Traza en vivo (si esta encendida con "t"): imprime la transicion. Se
        # limita a una linea cada 15 ms para no inundar el serial.
        if self.traza and ticks_diff(t, self.traza_t) >= 15000:
            self.traza_t = t
            print('[estado] %7.1f ms  %-13s %s -> %s' % (
                ticks_diff(t, self.t_inicio) / 1000.0, p.nombre, prev, nuevo))

    # Pasa un proceso a LISTO y lo mete al final de la cola.
    def _a_listo(self, p, ahora, llegada=None):
        self._cambiar_estado(p, LISTO, ahora)   # LISTO (antes NUEVO o BLOQUEADO)
        p.listo_desde = ahora       # anotamos desde cuando espera (para envejecimiento)
        if p.comienzo is None:      # si es un trabajo NUEVO (no lo habiamos empezado)
            p.llegada = ahora if llegada is None else llegada   # momento de llegada
            p.rafaga_us = 0         # todavia no ha consumido CPU en este trabajo
        self.listos.append(p)       # lo agregamos al FINAL de la cola

    # Se llama cuando un proceso termina su rafaga (se bloquea o termina de verdad):
    # aqui se calculan sus tiempos de retorno, espera y respuesta.
    def _fin_de_trabajo(self, p, ahora):
        if p.comienzo is None:      # si nunca llego a empezar, no hay nada que medir
            return
        retorno = ticks_diff(ahora, p.llegada)            # T_retorno = fin - llegada
        espera = retorno - p.rafaga_us                    # T_espera  = retorno - CPU usada
        respuesta = ticks_diff(p.comienzo, p.llegada)     # T_respuesta = primer despacho - llegada
        p.trabajos += 1                                   # contamos un trabajo mas
        p.suma_retorno += retorno                         # acumulamos para los promedios
        p.suma_espera += espera
        p.suma_respuesta += respuesta
        if hasattr(self.planificador, 'actualizar'):      # SJF/HRRN actualizan tau
            self.planificador.actualizar(p, p.rafaga_us)
        p.comienzo = None                                 # listo para el proximo trabajo

    # Termina un proceso (por fin normal, error, OOM o interbloqueo): libera todo.
    def _terminar(self, p, motivo):
        ahora = ticks_us()
        self._fin_de_trabajo(p, ahora)      # cerramos el trabajo actual (metricas)
        self._cambiar_estado(p, TERMINADO, ahora)   # su estado final
        p.motivo_fin = motivo               # guardamos POR QUE termino
        if p in self.listos:                # si estaba en la cola de listos, lo sacamos
            self.listos.remove(p)
        if p in self.dormidos:              # si estaba durmiendo, lo sacamos
            self.dormidos.remove(p)
        if p.bloqueado_en is not None and p in p.bloqueado_en.cola:  # si esperaba algo
            p.bloqueado_en.cola.remove(p)   # lo quitamos de esa cola de espera
        for m in list(p.cerrojos):          # liberamos los cerrojos que tuviera
            self._soltar(p, m)
        p.buferes = []                      # liberamos la lista de buffers de memoria
        p.reservada = 0                     # y su contador de bytes reservados
        try:
            p.contexto.close()              # cerramos el generador (si existe)
        except Exception:
            pass
        p.contexto = None
        self.registrar('%s TERMINADO (%s)' % (p.nombre, motivo))   # avisamos en consola

    # ------------------------------------------------------------------- M4 memoria
    # Politica ante falta de memoria: matar al que se paso y seguir (OOM-killer).
    def _oom_kill(self, p, detalle):
        self.oom_kills += 1                     # contamos la baja
        self._terminar(p, 'OOM-killer: ' + detalle)   # lo terminamos con el motivo
        self._recolectar(forzado=True)          # forzamos limpieza de memoria

    # Llama al recolector de basura (gc.collect) cuando hace falta y mide la pausa.
    def _recolectar(self, forzado=False):
        libre = mem_free()                      # cuanta RAM libre hay ahora
        if forzado or libre < self.cfg.UMBRAL_GC:   # si forzamos o si baja del umbral
            t0 = ticks_us()                     # cronometro antes
            gc.collect()                        # recolector de basura
            dt = ticks_diff(ticks_us(), t0)     # cuanto tardo (la "pausa")
            self.gc_pausas += 1                 # contamos una pausa mas
            self.gc_us_total += dt              # sumamos su tiempo
            if dt > self.gc_us_max:
                self.gc_us_max = dt             # guardamos la peor pausa vista
            libre = mem_free()                  # recalculamos la RAM libre despues
        if libre < self.ram_libre_min:          # actualizamos el minimo historico
            self.ram_libre_min = libre

    # -------------------------------------------------------------- M3 sincronizacion
    # Suelta un mutex: se lo pasa al primero que este esperando en su cola.
    def _soltar(self, p, m):
        if m.dueno is not p:                    # control: solo el dueño puede soltarlo
            raise RuntimeError('%s suelta %s sin ser su dueno' % (p.nombre, m.nombre))
        p.cerrojos.remove(m)                    # el proceso ya no tiene este cerrojo
        m.dueno = None                          # el cerrojo queda libre
        if m.cola:                              # si alguien esperaba, se lo damos
            sig = m.cola.pop(0)                 # el primero de la cola (FIFO)
            m.dueno = sig                       # ahora ese es el dueño
            sig.cerrojos.append(m)              # lo anotamos en su lista de cerrojos
            sig.bloqueado_en = None             # ya no espera
            self._a_listo(sig, ticks_us())      # lo despertamos (vuelve a LISTO)

    # Busca un "ciclo" en el grafo de espera -> si lo hay, es un INTERBLOQUEO.
    def _detectar_ciclo(self, p, m):
        cadena = [p.nombre]                     # empezamos la cadena con quien pide
        dueno = m.dueno                         # quien tiene ahora el cerrojo m
        visitados = 0                           # para no dar vueltas infinitas
        while dueno is not None and visitados < len(self.procesos):
            cadena.append(dueno.nombre)         # agregamos el dueño a la cadena
            if dueno is p:                      # si volvimos al inicio, hay ciclo
                return cadena
            espera = dueno.bloqueado_en         # ¿por que espera el dueño?
            if not isinstance(espera, Mutex):   # si no espera por un mutex, no hay ciclo
                return None
            dueno = espera.dueno                # seguimos por el siguiente dueño
            visitados += 1
        return None

    # --------------------------------------------------------- llamadas al sistema
    # Atiende la peticion que un proceso cedio con yield.
    # Devuelve (accion, valor), donde accion es 'seguir', 'ceder', 'bloquear', etc.
    def _llamada(self, p, pet, ahora):
        if pet is None:                         # yield a secas: es un punto de cesion
            return 'seguir', None
        op = pet[0]                             # la primera parte de la peticion (la operacion)
        if op == 'ceder':                       # el proceso cede la CPU voluntariamente
            return 'ceder', None
        if op == 'dormir':                      # pide dormir X ms
            p.despertar_en = ticks_add(ahora, int(pet[1] * 1000))   # despertara aqui
            p.activacion_ideal = None           # no es un proceso periodico
            return 'dormir', None
        if op == 'periodo':                     # pide dormir HASTA su proxima activacion
            periodo = int(pet[1] * 1000)        # el periodo en us
            if p.activacion_ideal is None:      # primera vez: el ideal es ahora
                p.activacion_ideal = ahora
            limite = ticks_add(p.activacion_ideal, periodo)   # momento ideal del proximo trabajo
            if ticks_diff(ahora, limite) > 0:   # si ya se paso del ideal -> llego tarde
                p.plazos_incumplidos += 1       # contamos un plazo incumplido
                while ticks_diff(ahora, limite) > 0:   # saltamos al siguiente periodo libre
                    limite = ticks_add(limite, periodo)
            p.activacion_ideal = limite         # el nuevo ideal para la proxima vez
            p.despertar_en = limite             # despertara en ese momento
            return 'dormir', None
        if op == 'P':                           # operacion P (wait) de semaforo
            s = pet[1]                          # el semaforo
            if s.valor > 0:                     # si hay recurso disponible
                s.valor -= 1                    # lo tomamos
                return 'seguir', None           # y seguimos corriendo
            s.cola.append(p)                    # no hay recurso: a la cola del semaforo
            p.bloqueado_en = s                  # anotamos por quien espera
            return 'bloquear', None             # y lo BLOQUEAMOS
        if op == 'V':                           # operacion V (signal) de semaforo
            s = pet[1]
            if s.cola:                          # si alguien esperaba
                sig = s.cola.pop(0)             # sacamos al primero
                sig.bloqueado_en = None         # ya no espera
                self._a_listo(sig, ahora)       # lo despertamos
            else:
                s.valor += 1                    # si nadie esperaba, subimos el contador
            return 'seguir', None
        if op == 'tomar':                       # tomar un mutex (lock)
            m = pet[1]
            if self.cfg.ORDEN_GLOBAL:           # si el orden global esta activo (previene interbloqueos)
                for c in p.cerrojos:            # revisamos los cerrojos que ya tiene
                    if c.orden >= m.orden:      # si intenta pedir uno de orden menor o igual...
                        raise Interbloqueo('%s viola el orden global: tiene %s y pide %s'
                                           % (p.nombre, c.nombre, m.nombre))   # ...error
            if m.dueno is None:                 # si el mutex esta libre
                m.dueno = p                     # lo tomamos
                p.cerrojos.append(m)            # lo anotamos como nuestro
                return 'seguir', None
            if m.dueno is p:                    # si ya era nuestro, es un error de programa
                raise RuntimeError('%s ya tiene %s' % (p.nombre, m.nombre))
            m.cola.append(p)                    # esta ocupado: nos ponemos en su cola
            p.bloqueado_en = m                  # anotamos por quien esperamos
            ciclo = self._detectar_ciclo(p, m)  # ¿esto creo un ciclo? (interbloqueo)
            if ciclo:
                self.interbloqueos += 1
                self.registrar('INTERBLOQUEO detectado: ' + ' -> '.join(ciclo))
                return 'interbloqueo', ciclo    # avisamos al kernel para que actue
            return 'bloquear', None             # si no hay ciclo, nos bloqueamos normal
        if op == 'soltar':                      # soltar un mutex (unlock)
            self._soltar(p, pet[1])
            return 'seguir', None
        if op == 'reservar':                    # pedir memoria contra la cuota (M4)
            n = pet[1]                          # cuantos bytes pide
            if p.reservada + n > p.cuota:       # si se pasa de su cuota -> OOM
                return 'oom', 'reserva %d B, cuota %d B' % (p.reservada + n, p.cuota)
            buf = bytearray(n)                  # creamos el buffer de n bytes
            p.buferes.append(buf)               # lo guardamos en su lista
            p.reservada += n                    # sumamos a lo reservado
            return 'seguir', buf                # devolvemos el buffer al proceso
        if op == 'liberar':                     # devolver memoria
            buf = pet[1]
            if buf in p.buferes:                # si era suyo
                p.buferes.remove(buf)           # lo quitamos
                p.reservada -= len(buf)         # restamos los bytes
            return 'seguir', None
        raise ValueError('peticion desconocida: %r' % (pet,))   # peticion que no conocemos

    # ------------------------------------------------------------ cambio de contexto
    # LISTO -> EJECUCION: reanuda el proceso hasta que se bloquee, termine o lo expulsen.
    def _despachar(self, p):
        t_kernel = ticks_us()               # empezamos a medir el tiempo del kernel
        self.listos.remove(p)               # ya no esta en la cola de listos
        self._cambiar_estado(p, EJECUCION, t_kernel)   # ahora esta en ejecucion
        p.prioridad_efectiva = p.prioridad  # al ejecutar, su prioridad vuelve a la base
        p.despachos += 1                    # contamos un despacho mas
        self.cambios_contexto += 1          # y un cambio de contexto
        self.actual = p                     # el proceso actual pasa a ser este
        if p.comienzo is None:              # si es la primera vez que corre este trabajo
            p.comienzo = t_kernel           # anotamos cuando comenzo
            if p.activacion_ideal is not None and p.pid == 1:   # si es P1 (periodico)
                retraso = ticks_diff(t_kernel, p.llegada)       # cuanto se retraso
                self.j_n += 1                                   # contamos una muestra de jitter
                self.j_suma += retraso
                self.j_suma2 += retraso * retraso
                if retraso > self.j_max:
                    self.j_max = retraso                        # guardamos el peor
        ejecutado = 0                       # CPU consumida por el proceso en este despacho
        valor = p.valor_pendiente           # valor que le pasaremos con send() (o None)
        p.valor_pendiente = None
        # Medir memoria con gc.mem_alloc() es costoso: medimos en el primer despacho
        # y luego 1 de cada MEDIR_MEM_CADA despachos.
        medir = (p.despachos - 1) % self.medir_cada == 0
        if medir:
            # gc.mem_alloc() cuenta lo ASIGNADO, incluida la basura que todavia no
            # se ha recolectado. Antes de medir limpiamos esa basura para tener una
            # linea base honesta (lo que esta VIVO antes de la rafaga).
            gc.collect()
            mem0 = mem_alloc()          # memoria viva antes de la rafaga
        else:
            mem0 = 0
        kernel_us = 0                       # tiempo que el kernel gasta atendiendo a este proceso
        accion = 'seguir'                   # resultado por defecto
        while True:                         # bucle: le damos "pasos" al proceso
            t0 = ticks_us()                 # cronometro antes del paso
            kernel_us += ticks_diff(t0, t_kernel)   # sumamos el tiempo de kernel previo
            try:
                pet = p.contexto.send(valor)    # EJECUTAMOS el proceso hasta su proximo yield
                t1 = ticks_us()                 # cronometro despues del paso
            except StopIteration:               # el generador termino: el proceso acabo
                t1 = t_kernel = ticks_us()
                self._contar(p, ticks_diff(t1, t0))   # contamos su ultimo tramo de CPU
                self._terminar(p, 'fin normal')       # lo terminamos normalmente
                accion = 'fin'
                break
            except MemoryError:                 # el proceso pidio mas memoria de la que hay
                t1 = t_kernel = ticks_us()
                self._contar(p, ticks_diff(t1, t0))
                self._oom_kill(p, 'MemoryError')      # lo matamos (OOM-killer)
                accion = 'fin'
                break
            except Exception as e:              # cualquier otro error del proceso
                t1 = t_kernel = ticks_us()
                self._contar(p, ticks_diff(t1, t0))
                compat.imprimir_excepcion(e)          # mostramos el error
                self._terminar(p, 'error: %s' % e)    # y terminamos ese proceso
                accion = 'fin'
                break
            d = ticks_diff(t1, t0)              # cuanto corrio el proceso en este paso
            self._contar(p, d)                  # sumamos a las metricas
            ejecutado += d                      # y a lo ejecutado en este despacho
            t_kernel = t1                       # el kernel vuelve a estar "al dia"
            try:
                accion, valor = self._llamada(p, pet, t1)   # atendemos la peticion del yield
            except Exception as e:              # si la peticion es invalida
                compat.imprimir_excepcion(e)
                self._terminar(p, 'error: %s' % e)
                accion = 'fin'
                break
            if accion == 'seguir':              # el proceso puede seguir corriendo
                # ¿el planificador quiere expulsarlo ya? (fin de quantum, etc.)
                if self.planificador.debe_expulsar(p, ejecutado, self.listos, t1):
                    accion = 'expulsar'
                    break                       # salimos del bucle y lo devolvemos a LISTO
                continue                        # si no, le damos otro paso
            break                               # otra accion (bloquear, dormir...) -> salimos

        # M4: cuanta memoria RETIENE el proceso al final de esta rafaga. Volvemos a
        # recolectar para no contar como consumo la basura transitoria que el
        # proceso creo y ya no usa (ej. los flotantes temporales del calculo de
        # rasgos): asi solo se mide lo que el proceso deja VIVO.
        if medir and accion != 'fin' and accion != 'oom':
            gc.collect()                        # limpiamos la basura de la rafaga
            usado = mem_alloc() - mem0          # memoria viva que quedo de la rafaga
            if usado > p.pico_rafaga:
                p.pico_rafaga = usado           # guardamos el pico
            if usado > p.cuota:                 # si se paso de su cuota en una rafaga
                accion, valor = 'oom', 'asigno %d B en una rafaga, cuota %d B' % (usado, p.cuota)
        ahora = ticks_us()
        if accion in ('expulsar', 'ceder'):     # EJECUCION -> LISTO
            p.expulsiones += 1                  # contamos la expulsion
            p.valor_pendiente = valor           # guardamos lo que devolvera al reanudar
            self._a_listo(p, ahora)             # vuelve al final de la cola de listos
        elif accion == 'dormir':                # EJECUCION -> BLOQUEADO por tiempo
            self._fin_de_trabajo(p, ahora)      # se cierra el trabajo actual
            self._cambiar_estado(p, BLOQUEADO, ahora)
            self.dormidos.append(p)             # entra a la lista de dormidos
        elif accion == 'bloquear':              # EJECUCION -> BLOQUEADO (semaforo/mutex)
            self._fin_de_trabajo(p, ahora)
            self._cambiar_estado(p, BLOQUEADO, ahora)
        elif accion == 'interbloqueo':          # se detecto un ciclo
            self._fin_de_trabajo(p, ahora)
            self._cambiar_estado(p, BLOQUEADO, ahora)
            self._resolver_interbloqueo(p)      # el kernel rompe el ciclo (mata a uno)
        elif accion == 'oom':                   # se paso de memoria
            self._oom_kill(p, valor)
        self.actual = None                      # ya no hay proceso "actual"
        # Sumamos el tiempo total de kernel gastado durante este despacho.
        self.t_planificacion += kernel_us + ticks_diff(ticks_us(), t_kernel)

    # Suma tiempo de CPU d segundos al proceso y a las metricas globales.
    def _contar(self, p, d):
        p.cpu_us += d               # CPU acumulada del proceso
        p.rafaga_us += d            # CPU de la rafaga actual
        self.t_util += d            # CPU util del sistema

    # Recuperacion de interbloqueo: termina al proceso que cerro el ciclo.
    def _resolver_interbloqueo(self, victima):
        self._terminar(victima, 'victima de interbloqueo')   # y libera sus cerrojos

    # Despierta a los procesos dormidos cuyo momento ya llego.
    def _despertar(self, ahora):
        i = 0
        while i < len(self.dormidos):               # recorremos la lista de dormidos
            p = self.dormidos[i]
            if ticks_diff(ahora, p.despertar_en) >= 0:   # ya es hora de despertar
                self.dormidos.pop(i)                # lo sacamos de dormidos
                # Para un proceso periodico la "llegada" es su activacion ideal:
                # asi el retraso del kernel cuenta como espera y como jitter.
                llegada = p.despertar_en if p.activacion_ideal is not None else ahora
                self._a_listo(p, ahora, llegada)    # lo ponemos en LISTO
            else:
                i += 1                              # aun no le toca: pasamos al siguiente

    # Devuelve cuantos us faltan para el proximo despertar (el mas cercano).
    def _proximo_despertar(self, ahora):
        menor = None
        for p in self.dormidos:
            d = ticks_diff(p.despertar_en, ahora)   # cuanto falta para este
            if menor is None or d < menor:
                menor = d                           # nos quedamos con el menor
        return menor

    # ----------------------------------------------------------------- ciclo principal
    # Bucle principal del kernel: repetir "despertar -> elegir -> despachar".
    def ejecutar(self, duracion_s=0):
        self.corriendo = True                       # bandera: estamos corriendo
        # Si duracion_s es 0, no hay fin; si no, calculamos el tick de fin.
        fin = ticks_add(ticks_us(), int(duracion_s * 1000000)) if duracion_s else None
        self.vueltas = 0                            # contador de vueltas del bucle
        while self.corriendo:
            t0 = ticks_us()
            if fin is not None and ticks_diff(t0, fin) >= 0:   # ¿se acabo el tiempo?
                break
            self._despertar(t0)                     # despertar a los que les toque
            p = self.planificador.elegir(self.listos, t0)       # a quien le doy la CPU
            self.t_planificacion += ticks_diff(ticks_us(), t0)  # costo de elegir
            if p is not None:                       # si hay alguien listo
                self._despachar(p)                  # le doy la CPU
            else:                                   # si no hay nadie listo
                vivos = [q for q in self.procesos if q.estado != TERMINADO]
                if not vivos:                       # ¿todos terminaron?
                    self.registrar('no quedan procesos')
                    break
                espera = self._proximo_despertar(ticks_us())
                if espera is None:                  # todos bloqueados sin evento: no hay salida
                    self.registrar('todos los procesos estan BLOQUEADOS sin evento pendiente')
                    break
                t_o = ticks_us()                    # medimos el tiempo ocioso
                if espera > 200:                    # si falta bastante, dormimos un poco
                    sleep_us(min(espera - 100, 2000))   # (max 2 ms) para no gastar CPU
                self.t_ocioso += ticks_diff(ticks_us(), t_o)
            vueltas = self.vueltas
            vueltas += 1
            self.vueltas = vueltas
            if vueltas % 64 == 0:                   # cada 64 vueltas...
                t_k = ticks_us()
                self._recolectar()                  # revisamos la memoria (GC si hace falta)
                if self.consola:
                    self.consola.atender(self)      # y leemos el teclado
                self.t_planificacion += ticks_diff(ticks_us(), t_k)
        self.corriendo = False

    # Pide detener el ciclo principal (lo usa el comando "q").
    def detener(self):
        self.corriendo = False

    # ------------------------------------------------------------------ informes
    # Plantilla de la tabla de estados: un ancho fijo por columna. Se usa igual
    # para el encabezado y para las filas, asi TODO queda alineado en columnas.
    #   Proceso | Estado | Prio | Desp | Trab | Lis/Blo | Esp ms | Ret ms |
    #   Resp ms | Ult ms | RAM B
    _FORMATO_TABLA = '%-13s %-10s %5s %5s %5s %8s %8s %8s %8s %7s %12s'

    # Devuelve, como texto, la tabla de estados que se ve en la consola.
    def tabla_estados(self):
        ahora = ticks_us()                          # instante de esta "foto"
        m, s, mx = self.jitter_p1()                 # metricas de jitter de P1
        lineas = []                                 # aqui armamos las lineas de salida
        # --- linea 1: resumen general del sistema ---
        lineas.append('=== SensorOS-Lite | %s | CPU util %.1f%% | eficiencia %.1f%% | RAM libre %d B ===' % (
            self.planificador.descripcion(), 100 * self.cpu_util(),
            100 * self.eficiencia(), mem_free()))
        # --- linea 2: contadores globales ---
        # Recordatorio para la demo: BLOQUEADO no es un error, es "esperando un
        # evento" (un semaforo, un periodo...). En un pipeline casi todos los
        # procesos estan BLOQUEADOS en cada instante: lo que importa es que las
        # columnas Desp/Trab suban y que Lis/Blo sigan subiendo, es decir, que
        # TODOS van cambiando de estado (nadie esta congelado).
        lineas.append('vueltas=%d  cambios de contexto=%d  plazos incumplidos=%d  '
                      '| BLOQUEADO = esperando evento (normal)' % (
                          self.vueltas, self.cambios_contexto, self.plazos_incumplidos()))
        # --- tabla: encabezado + separador + una fila por proceso ---
        # Lis/Blo = cuantas veces el proceso entro a LISTO y a BLOQUEADO.
        # Ult ms  = hace cuantos ms fue su ULTIMO cambio de estado (si crece
        #           sin parar, seria el unico caso preocupante).
        cab = self._FORMATO_TABLA % ('Proceso', 'Estado', 'Prio', 'Desp', 'Trab',
                                     'Lis/Blo', 'Esp ms', 'Ret ms', 'Resp ms',
                                     'Ult ms', 'RAM B')
        lineas.append(cab)                          # encabezado de la tabla
        lineas.append('-' * len(cab))               # linea separadora (mismo ancho)
        for p in self.procesos:                     # una fila por cada proceso
            lineas.append(self._FORMATO_TABLA % (
                p.nombre, p.estado, '%d(%d)' % (p.prioridad, p.prioridad_efectiva),
                p.despachos, p.trabajos,
                '%d/%d' % (p.veces_listo, p.veces_bloq),            # cambios a LISTO/BLOQ
                '%.2f' % (p.promedio(p.suma_espera) / 1000.0),      # espera (us -> ms)
                '%.2f' % (p.promedio(p.suma_retorno) / 1000.0),     # retorno
                '%.2f' % (p.promedio(p.suma_respuesta) / 1000.0),   # respuesta
                '%.0f' % (ticks_diff(ahora, p.ult_cambio_t) / 1000.0),  # ms del ultimo cambio
                '%d/%d' % (max(p.pico_rafaga, p.reservada), p.cuota)))
        # --- ultima linea: jitter de P1 (en ms: us / 1000) ---
        lineas.append('P1 plazos incumplidos=%d  jitter media=%.2f ms desv=%.2f ms max=%.2f ms' % (
            self.procesos[0].plazos_incumplidos if self.procesos else 0,
            m / 1000.0, s / 1000.0, mx / 1000.0))
        return '\n'.join(lineas)                    # unimos todo con saltos de linea


# Clase interna: lee el teclado por el serial para cambiar cosas EN VIVO.
# Comandos: f | r [q] | p | s | h | a (AUTO) | e (estados) | t (traza) | q (salir)
class _Consola:
    def __init__(self):
        self.poll = None                    # "escucha" del teclado (puede ser None)
        self.linea = ''                     # lo que se va escribiendo, caracter por caracter
        try:
            import select                   # select permite saber si hay tecla sin bloquear
            import sys
            self.sys = sys
            self.poll = select.poll()       # objeto que vigila la entrada estandar
            self.poll.register(sys.stdin, select.POLLIN)   # avisar cuando haya algo que leer
        except Exception:
            self.poll = None                # si no se puede (ej. en el PC), consola desactivada

    # Se llama periodicamente desde el kernel: lee teclas y arma la linea.
    def atender(self, k):
        if self.poll is None:
            return                          # sin consola disponible, no hacemos nada
        while self.poll.poll(0):            # mientras haya caracteres esperando
            c = self.sys.stdin.read(1)      # leemos un caracter
            if not c:
                return
            if c in '\r\n':                 # si es Enter, ejecutamos la linea completa
                self.ejecutar(k, self.linea.strip())
                self.linea = ''             # y borramos lo escrito
            else:
                self.linea += c             # si no, lo agregamos a la linea

    # Interpreta un comando completo escrito por el usuario.
    def ejecutar(self, k, cmd):
        if not cmd:
            return                          # linea vacia: nada que hacer
        partes = cmd.split()                # separamos por espacios (ej. "r 10")
        c = partes[0].lower()               # primer trozo en minusculas
        if c == 'f':
            k.cambiar_algoritmo('FCFS')     # cambiar a FCFS
        elif c == 'r':
            k.cambiar_algoritmo('RR', int(partes[1]) if len(partes) > 1 else None)  # RR (quantum opcional)
        elif c == 'p':
            k.cambiar_algoritmo('PRIO')     # cambiar a Prioridades
        elif c == 's':
            k.cambiar_algoritmo('SJF')      # cambiar a SJF
        elif c == 'h':
            k.cambiar_algoritmo('HRRN')     # cambiar a HRRN
        elif c == 'a':
            k.cambiar_algoritmo('AUTO')     # modo AUTO: prueba todos y se queda con el mejor
        elif c == 'e':
            print(k.tabla_estados())        # imprimir la tabla de estados
        elif c == 't':
            k.traza = not k.traza           # prender/apagar la traza de cambios de estado
            if k.traza:
                k.traza_t = ticks_us()      # al prender, empezamos a contar desde ahora
            print('[kernel] traza de cambios de estado %s' % ('ON' if k.traza else 'OFF'))
        elif c == 'q':
            k.detener()                     # detener el kernel
        else:
            print('comandos: f | r [quantum_ms] | p | s | h | a(AUTO) | e | t(traza) | q')
