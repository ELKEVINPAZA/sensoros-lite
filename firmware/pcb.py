# ============================================================================
# pcb.py - M1 · Bloque de Control de Proceso (PCB).
# ----------------------------------------------------------------------------
# Un sistema operativo "no ve programas, ve PCBs": por cada proceso guarda una
# ficha con TODO lo necesario para gestionarlo, planificarlo y continuar su
# ejecucion mas tarde. Aqui definimos los 5 estados y la clase PCB (esa ficha).
# ============================================================================

# Los 5 estados del ciclo de vida de un proceso (se usan en todas partes).
NUEVO = 'NUEVO'            # recien creado, todavia no puede ejecutar
LISTO = 'LISTO'            # quiere la CPU y espera su turno en la cola
EJECUCION = 'EJECUCION'    # en este instante esta usando la CPU
BLOQUEADO = 'BLOQUEADO'    # espera algo (tiempo, semaforo...): no puede correr
TERMINADO = 'TERMINADO'    # ya acabo (o lo mataron) y libero sus recursos


class PCB:
    # __init__ es el "constructor": se ejecuta solo al crear el objeto con PCB(...).
    # Recibe id, nombre, la funcion del proceso, sus argumentos, la prioridad,
    # la cuota de RAM en KB y la estimacion inicial de rafaga (para SJF/HRRN).
    def __init__(self, pid, nombre, funcion, args, prioridad, cuota_kb, tau_inicial):
        # ---------- Identificacion y estado ----------
        self.pid = pid                # numero unico del proceso (1, 2, 3, ...)
        self.nombre = nombre          # texto para mostrar, ej. 'P1 Muestreo'
        self.estado = NUEVO           # todo proceso nace en estado NUEVO

        # Contexto guardado: en un SO real aqui se guardan el "contador de
        # programa" y los registros de la CPU. En nuestro kernel, cada proceso es
        # un generador de Python, y el generador ya guarda por su cuenta donde
        # quedo y sus variables locales. Por eso aqui solo guardamos el generador.
        self.funcion = funcion        # la funcion que define el proceso
        self.args = args              # argumentos extra al crear el proceso
        self.contexto = None          # el objeto generador ya creado (o None)
        self.valor_pendiente = None   # valor que el proceso recibira al reanudar (send)

        # ---------- Informacion de planificacion (M2) ----------
        self.prioridad = prioridad            # prioridad base (1 = la mayor)
        self.prioridad_efectiva = prioridad   # prioridad con envejecimiento aplicado
        self.tau = tau_inicial                # SJF: estimacion de la proxima rafaga (us)
        self.listo_desde = 0                  # tick en que entro a la cola de LISTOS

        # ---------- Trabajo actual (cada activacion es un "trabajo") ----------
        self.llegada = 0              # tick en que llego (cuando paso a LISTO)
        self.comienzo = None          # tick del primer despacho del trabajo actual
        self.rafaga_us = 0            # CPU (us) consumida en el trabajo actual
        self.despertar_en = None      # tick en que debe salir de BLOQUEADO (dormir/periodo)
        self.activacion_ideal = None  # P1: instante teorico de la proxima muestra (para el jitter)
        self.bloqueado_en = None      # semaforo/mutex por el que esta esperando
        self.cerrojos = []            # lista de mutex que el proceso posee ahora

        # ---------- Informacion de memoria (M4) ----------
        self.cuota = cuota_kb * 1024  # cuota maxima en BYTES (KB * 1024)
        self.reservada = 0            # bytes reservados por el proceso (buffers pedidos)
        self.buferes = []             # lista de buffers (bytearray) que tiene pedidos
        self.pico_rafaga = 0          # mayor memoria asignada en una sola rafaga

        # ---------- Contadores para las metricas ----------
        self.trabajos = 0             # cuantos trabajos ha completado
        self.cpu_us = 0               # CPU total consumida (us)
        self.despachos = 0            # cuantas veces le dieron la CPU (entradas a EJECUCION)
        self.expulsiones = 0          # cuantas veces le quitaron la CPU antes de tiempo
        # Contadores de cambios de estado: sirven para DEMOSTRAR que el proceso
        # NO esta congelado aunque en una foto salga BLOQUEADO. Cada vez que el
        # proceso cambia de estado suben estos numeros, y ult_cambio_t guarda el
        # instante del ultimo cambio (para poder mostrar "hace cuanto cambio").
        self.veces_listo = 0          # cuantas veces entro a LISTO
        self.veces_bloq = 0           # cuantas veces entro a BLOQUEADO (a esperar un evento)
        self.ult_prev = self.estado   # estado del que venia en su ultimo cambio
        self.ult_cambio_t = 0         # tick (us) del ultimo cambio de estado
        self.suma_espera = 0          # suma de tiempos de espera (para el promedio)
        self.suma_retorno = 0         # suma de tiempos de retorno
        self.suma_respuesta = 0       # suma de tiempos de respuesta
        self.plazos_incumplidos = 0   # cuantas muestras periodicas llego tarde (P1)
        self.motivo_fin = ''          # texto de por que termino el proceso

    # Divide una suma acumulada entre el numero de trabajos -> promedio.
    # Si no hay trabajos, devuelve 0 (para no dividir por cero).
    def promedio(self, suma):
        return suma / self.trabajos if self.trabajos else 0
