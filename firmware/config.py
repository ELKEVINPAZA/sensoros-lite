# ============================================================================
# config.py - PANEL DE CONTROL de SensorOS-Lite.
# ----------------------------------------------------------------------------
# Aqui estan TODOS los parametros del mini sistema operativo.
# Se pueden cambiar editando este archivo, y algunos en vivo desde la consola
# serial del kernel (teclas f, r, p, s, h, e...; ver kernel.py).
#
# Recordatorio de Python: "NOMBRE = valor" guarda ese valor en una variable.
# Otro archivo puede leerlo asi:   import config as cfg   ->   cfg.NOMBRE
# ============================================================================

# --- M2 · Planificador ------------------------------------------------------
# Algoritmo con el que ARRANCA el sistema. Opciones: FCFS, RR, PRIO, SJF, HRRN
# y AUTO. AUTO (meta-planificador) prueba todos por turnos y se queda con el
# que menos plazos incumplidos causa (ver EPOCA_AUTO_MS).
ALGORITMO = 'PRIO'
# Round Robin (RR): cuantos milisegundos (ms) puede usar un proceso la CPU de
# corrido antes de que lo expulsen y le toque a otro. Se cambia en vivo con "r".
QUANTUM_MS = 5
# SJF: constante alfa para estimar la proxima rafaga con la formula
# tau(n+1) = alfa * t(n) + (1 - alfa) * tau(n).  0.5 = mitad de cada cosa.
ALFA = 0.5
# SJF/HRRN: primera estimacion de la rafaga (en microsegundos, us).
TAU_INICIAL_US = 2000
# PRIO: cada cuantos ms que un proceso lleva esperando en LISTO sube 1 nivel de
# prioridad. Sirve para que ningun proceso se "muera de hambre" (inanicion).
ENVEJECIMIENTO_MS = 50
# AUTO: cuanto dura (en ms) la prueba de cada algoritmo. Si hay 5 candidatos y
# vale 3000, en 15 s ya probo todos y se queda con el mejor. Se cambia con "a".
EPOCA_AUTO_MS = 3000

# --- Tabla de procesos (seccion 2 del enunciado) ----------------------------
# Cada linea es una tupla con 3 datos: (nombre, prioridad, cuota de RAM en KB).
# OJO: 1 es la MAYOR prioridad (asi lo maneja el profesor en clase).
PROCESOS = (
    ('P1 Muestreo', 1, 8),    # lee el sensor; el mas critico (prio 1), cuota 8 KB
    ('P2 Rasgos',   2, 4),    # calcula los rasgos; prio 2, cuota 4 KB
    ('P3 Modelo',   2, 16),   # clasifica con el modelo; prio 2, cuota 16 KB
    ('P4 Registro', 3, 8),    # guarda datos en flash; prio 3, cuota 8 KB
    ('P5 Salida',   3, 2),    # muestra la clase en LED/serial; prio 3, cuota 2 KB
    ('P6 Monitor',  4, 4),    # mide y escribe el CSV; prio 4 (la menor), 4 KB
)

PERIODO_P1_MS = 10          # P1 se activa cada 10 ms (= 100 Hz, como pide M5).
                            # OJO: el DHT22 solo da un valor nuevo cada
                            # DHT_PERIODO_MS; entre lecturas se repite el ultimo.
VENTANA = 128               # cuantas muestras forman una ventana de analisis
SALTO = 64                  # avanza 64 muestras por ventana -> 50% de solapamiento
CAPACIDAD_BUFER = 256       # cuantas muestras caben en el bufer circular P1 -> P2
N_VENTANAS_REGISTRO = 4     # P4 guarda una linea de registro cada N ventanas
PERIODO_MONITOR_S = 5       # cada cuantos segundos P6 escribe su fila del CSV (metricas)
PERIODO_TABLA_S = 1         # cada cuantos segundos se IMPRIME la tabla de estados en
                            # pantalla (mas chico = se ve mas seguido). Solo cambia lo
                            # que se VE: el CSV sigue con PERIODO_MONITOR_S.

# --- Sensor (DHT22: temperatura y humedad) ----------------------------------
# Sensor: el DHT22 real conectado en PIN_DHT. Es el unico sensor del sistema.
SENSOR = 'dht'
PIN_DHT = 4                 # pin de DATOS del DHT22 (el cable del medio): GPIO4
DHT_PERIODO_MS = 2000       # el DHT22 solo admite 1 lectura cada 2 s (0.5 Hz)
PIN_LED = 2                 # pin del LED integrado de la placa
# Umbrales del CONFORT TERMICO (indice de bochorno = temperatura + humedad).
# Definen las 3 clases que clasifica el modelo de M5:
#   confort < CONFORT_FRIO            -> clase 'frio'
#   CONFORT_FRIO <= confort < CALIDO  -> clase 'normal'
#   confort >= CONFORT_CALIDO         -> clase 'calido'
CONFORT_FRIO = 20.0
CONFORT_CALIDO = 26.0

# --- M3 · Sincronizacion ----------------------------------------------------
# True = usar el "orden global de cerrojos" (previene interbloqueos).
# False = desactivarlo a proposito para reproducir el interbloqueo en la demo.
ORDEN_GLOBAL = True

# --- M4 · Memoria -----------------------------------------------------------
# 'A' = crear objetos nuevos por ventana (genera basura). 'B' = buffers preasignados.
MEMORIA = 'B'
# Si la RAM libre baja de este umbral (20 KB = 20*1024 bytes), el kernel hace gc.collect().
UMBRAL_GC = 20 * 1024
# Medir la RAM de cada proceso es costoso: se mide 1 de cada 8 despachos.
MEDIR_MEM_CADA = 8

# --- M5 · TinyML ------------------------------------------------------------
# Modelo a usar en la placa: 'lr' (regresion logistica) o 'arbol' (arbol).
# Con el dataset publico, el ARBOL queda casi perfecto (99.6 % en la sesion
# reservada y 0 errores clasificando ambientes constantes, como los de la
# placa): aprende los mismos umbrales de confort (20 y 26). La regresion
# logistica (93.5 %) falla en ambientes cerca de los limites.
MODELO = 'arbol'

# --- Modo de operacion ------------------------------------------------------
MODO = 'normal'             # 'normal' = clasificar en vivo | 'captura' = grabar datos
ETIQUETA = 'frio'           # en captura: que clase se graba: frio | normal | calido
SESION = 1                  # en captura: 1 o 2 (dos sesiones en dias distintos)
INTEGRANTE = 'luis'         # en captura: quien graba
N_COPIAS = 1                # experimento E2: copias de P2 y P3 (1 a 5)
DURACION_S = 0              # 0 = correr sin limite de tiempo
CONSOLA = True              # True = leer comandos de teclado por el cable serial
CARPETA_RESULTADOS = 'resultados'   # carpeta donde P6 y P4 escriben sus CSV
ETIQUETA_CORRIDA = 'demo'   # nombre de la corrida (va en cada fila del CSV)
