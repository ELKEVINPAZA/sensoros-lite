# procesos.py - Los seis procesos de aplicacion de SensorOS-Lite.
# Cada proceso es un generador: recibe el kernel (k), su propio PCB (yo) y el
# estado compartido (c). Cada yield le devuelve el control al kernel.
import os
from array import array
from compat import ticks_us, ticks_diff, ticks_add, mem_free
from sync import Semaforo, Mutex, BuferCircular
import rasgos


class Compartido:
    """Recursos compartidos entre procesos (la 'memoria compartida' del sistema)."""

    def __init__(self, cfg, sensor, modelo):
        self.cfg = cfg
        self.sensor = sensor
        self.modelo = modelo
        n = cfg.N_COPIAS
        # P1 -> P2: bufer circular productor-consumidor
        self.bufer = BuferCircular(cfg.CAPACIDAD_BUFER)
        # P2 principal -> copias de P2 (experimento E2): una ventana copiada por copia
        self.ventana_lista = [Semaforo('ventana%d' % i, 0) for i in range(n)]
        self.ventanas_copia = [array('h', bytes(6 * cfg.VENTANA)) for i in range(n)]
        # P2_k -> P3_k: buzon de una casilla con dos semaforos
        self.hay_rasgos = [Semaforo('hay_rasgos%d' % i, 0) for i in range(n)]
        self.hueco_rasgos = [Semaforo('hueco_rasgos%d' % i, 1) for i in range(n)]
        self.rasgos = [array('f', [0.0] * rasgos.N_RASGOS) for i in range(n)]
        # P3 -> P5 (cada clase nueva) y P3 -> P4 (cada N ventanas)
        self.clase_nueva = Semaforo('clase_nueva', 0)
        self.pedir_registro = Semaforo('pedir_registro', 0)
        # Captura de datos (M5): P2 -> P4 cada salto de 64 muestras crudas
        self.hay_salto = Semaforo('hay_salto', 0)
        self.salto = array('h', bytes(6 * cfg.SALTO))
        # Dos cerrojos que usan P4 y P6 (escenario de interbloqueo de M3).
        # 'orden' define el orden global: primero fs (1), luego estado (2).
        self.cerrojo_fs = Mutex('fs', orden=1)
        self.cerrojo_estado = Mutex('estado', orden=2)
        # Estado de la clasificacion (protegido por cerrojo_estado)
        self.clase = -1
        self.ventanas = 0
        # Ultima muestra del sensor (x10): la deja P1 y la usan P5 y P6 para
        # poder mostrar el valor real en tiempo real (sin crear objetos nuevos).
        self.ult_t = 0
        self.ult_h = 0
        self.ult_c = 0
        self.us_ventana = 0          # CPU de P2 + P3 acumulada (E3: ticks por ventana)
        self.inferencia_us_max = 0
        self.inferencia_us_suma = 0


def _asegurar_carpeta(nombre):
    try:
        os.mkdir(nombre)
    except OSError:
        pass


# ----------------------------------------------------------------------------- P1
def p1_muestreo(k, yo, c):
    """Lee el sensor DHT22 y llena el bufer circular (productor).

    Corre a 100 Hz (cada 10 ms), pero el DHT22 solo entrega un valor nuevo cada
    DHT_PERIODO_MS (2 s): entre lecturas el sensor devuelve el ultimo valor
    bueno. Cada ~2 s imprime el valor real, para VER los cambios en el serial."""
    b = c.bufer
    s = c.sensor
    esperar_periodo = ('periodo', k.cfg.PERIODO_P1_MS)   # peticion preasignada
    # cada cuantas vueltas de 10 ms hay que informar (2000 ms / 10 ms = 200)
    cada = max(1, k.cfg.DHT_PERIODO_MS // k.cfg.PERIODO_P1_MS)
    i = 0
    while True:
        t, h, cf = s.leer()            # (temperatura x10, humedad x10, confort x10)
        yield b.huecos.P                # espera un hueco libre en el bufer
        yield b.mutex.tomar             # entra a la seccion critica del bufer
        b.poner(t, h, cf)               # deja la muestra en el bufer circular
        yield b.mutex.soltar
        yield b.items.V                 # avisa que hay una muestra nueva
        # Guardamos la ultima muestra para que P5 y P6 puedan mostrarla.
        c.ult_t, c.ult_h, c.ult_c = t, h, cf
        if i % cada == 0:               # cada ~2 s: prueba de que el sensor es REAL
            print('[P1] T=%.1f C  H=%.1f %%  confort=%.1f' %
                  (t / 10.0, h / 10.0, cf / 10.0))
        i += 1
        yield esperar_periodo          # BLOQUEADO hasta la proxima activacion (10 ms)


# ----------------------------------------------------------------------------- P2
def p2_rasgos(k, yo, c, copia=0):
    """Consume muestras, arma ventanas de 128 con 50 % de solapamiento (cada 64
    muestras) y calcula los rasgos. La copia 0 es el consumidor del bufer; las
    copias 1..4 (E2) recalculan sobre una copia de la misma ventana."""
    cfg = k.cfg
    n, salto = cfg.VENTANA, cfg.SALTO
    b = c.bufer
    preasignado = cfg.MEMORIA == 'B'
    if preasignado:
        # Memoria B: todos los buferes se reservan una sola vez
        ventana = array('h', bytes(6 * n))
        vista = memoryview(ventana)
        salida = array('f', [0.0] * rasgos.N_RASGOS)
    else:
        ventana = []
    llenas = 0
    while True:
        # 1) obtener la ventana
        if copia == 0:
            if preasignado:
                if llenas == n:                       # desplazar 64 muestras (solapamiento)
                    vista[0:3 * (n - salto)] = vista[3 * salto:3 * n]
                    llenas = n - salto
                base = llenas
            else:
                if len(ventana) == 3 * n:
                    ventana = ventana[3 * salto:]     # Memoria A: lista nueva por ventana
            for i in range(salto):
                yield b.items.P
                yield b.mutex.tomar
                if preasignado:
                    b.sacar_en(ventana, 3 * llenas)
                    llenas += 1
                else:
                    tres = [0, 0, 0]
                    b.sacar_en(tres, 0)
                    ventana.extend(tres)
                yield b.mutex.soltar
                yield b.huecos.V
            if cfg.MODO == 'captura':                  # pasa el salto crudo a P4
                ini = 3 * base if preasignado else len(ventana) - 3 * salto
                for j in range(3 * salto):
                    c.salto[j] = ventana[ini + j]
                yield c.hay_salto.V
            completas = (llenas if preasignado else len(ventana) // 3) == n
            if not completas:
                continue
            for j in range(1, cfg.N_COPIAS):          # entregar copia a las demas P2
                dst = c.ventanas_copia[j]
                for i in range(3 * n):
                    dst[i] = ventana[i]
                yield c.ventana_lista[j].V
            if cfg.MODO == 'captura':
                continue
        else:
            yield c.ventana_lista[copia].P
            ventana = c.ventanas_copia[copia]
        # 2) calcular rasgos por etapas, cediendo la CPU entre etapas (puntos de expulsion)
        if not preasignado:
            salida = [0.0] * rasgos.N_RASGOS          # Memoria A: objetos nuevos
        acumulado = 0
        j = 0
        for e in range(rasgos.N_ETAPAS):
            t0 = ticks_us()
            for r in rasgos.etapa(e, ventana, n):
                salida[j] = r
                j += 1
            acumulado += ticks_diff(ticks_us(), t0)
            yield
        c.us_ventana += acumulado
        # 3) entregar a P3 por el buzon (productor-consumidor de una casilla)
        yield c.hueco_rasgos[copia].P
        dst = c.rasgos[copia]
        for i in range(rasgos.N_RASGOS):
            dst[i] = salida[i]
        yield c.hay_rasgos[copia].V


# ----------------------------------------------------------------------------- P3
def p3_modelo(k, yo, c, copia=0):
    """Clasifica cada ventana con el modelo TinyML (inferencia en MicroPython puro)."""
    cfg = k.cfg
    x = array('f', [0.0] * rasgos.N_RASGOS)
    while True:
        yield c.hay_rasgos[copia].P
        src = c.rasgos[copia]
        for i in range(rasgos.N_RASGOS):
            x[i] = src[i]
        yield c.hueco_rasgos[copia].V
        t0 = ticks_us()
        clase = c.modelo.predecir(x)
        dt = ticks_diff(ticks_us(), t0)
        c.us_ventana += dt
        if copia != 0:
            continue
        c.inferencia_us_suma += dt
        if dt > c.inferencia_us_max:
            c.inferencia_us_max = dt
        c.ventanas += 1
        if clase != c.clase:
            c.clase = clase
            yield c.clase_nueva.V
        if c.ventanas % cfg.N_VENTANAS_REGISTRO == 0:
            yield c.pedir_registro.V


# ----------------------------------------------------------------------------- P4
def p4_registro(k, yo, c):
    """Guarda en flash la clase cada N ventanas (o las muestras crudas en modo captura)."""
    cfg = k.cfg
    if cfg.MODO == 'captura':
        _asegurar_carpeta('datos')
        nombre = 'datos/s%d_%s_%s.csv' % (cfg.SESION, cfg.INTEGRANTE, cfg.ETIQUETA)
        with open(nombre, 'w') as f:
            f.write('temp,hum,confort\n')          # los 3 canales (x10) del DHT22
        while True:
            yield c.hay_salto.P
            with open(nombre, 'a') as f:
                s = c.salto
                for i in range(0, len(s), 3):
                    f.write('%d,%d,%d\n' % (s[i], s[i + 1], s[i + 2]))
            yield
    _asegurar_carpeta(cfg.CARPETA_RESULTADOS)
    nombre = '%s/registro_%s.csv' % (cfg.CARPETA_RESULTADOS, cfg.ETIQUETA_CORRIDA)
    with open(nombre, 'w') as f:
        f.write('t_ms,ventana,clase\n')
    while True:
        yield c.pedir_registro.P
        # Toma dos cerrojos: fs (archivo) y estado (resultado de la clasificacion)
        primero, segundo = c.cerrojo_fs, c.cerrojo_estado
        yield primero.tomar
        yield ('ceder',)                 # ventana para que P6 intervenga
        yield segundo.tomar
        linea = '%d,%d,%s\n' % (ticks_us() // 1000, c.ventanas, c.modelo.CLASES[c.clase])
        with open(nombre, 'a') as f:
            f.write(linea)
        yield segundo.soltar
        yield primero.soltar


# ----------------------------------------------------------------------------- P5
def p5_salida(k, yo, c):
    """Muestra cada clase nueva en el serial y en el LED (1, 2 o 3 destellos:
    1 = frio, 2 = normal, 3 = calido). Tambien imprime el valor del sensor, para
    que se VEA que la clasificacion responde a cambios reales del ambiente."""
    led = None
    try:
        from machine import Pin
        led = Pin(k.cfg.PIN_LED, Pin.OUT)
    except Exception:
        pass                            # en el PC no existe "machine": lo ignoramos
    while True:
        yield c.clase_nueva.P           # espera a que P3 anuncie una clase nueva
        clase = c.clase
        print('[P5] ambiente = %-6s  T=%.1f C  H=%.1f %%  confort=%.1f  (ventana %d)' % (
            c.modelo.CLASES[clase], c.ult_t / 10.0, c.ult_h / 10.0,
            c.ult_c / 10.0, c.ventanas))
        for i in range(clase + 1):      # destella (clase+1) veces: 1, 2 o 3
            if led:
                led.value(1)            # LED encendido
            yield ('dormir', 80)        # 80 ms encendido
            if led:
                led.value(0)            # LED apagado
            yield ('dormir', 120)       # 120 ms apagado


# ----------------------------------------------------------------------------- P6
def p6_monitor(k, yo, c):
    """Imprime la tabla de estados cada PERIODO_TABLA_S s y guarda una fila con
    las metricas (M6) cada PERIODO_MONITOR_S s en el CSV."""
    cfg = k.cfg
    _asegurar_carpeta(cfg.CARPETA_RESULTADOS)
    nombre = '%s/monitor_%s.csv' % (cfg.CARPETA_RESULTADOS, cfg.ETIQUETA_CORRIDA)
    base = k.procesos[:6]
    with open(nombre, 'w') as f:
        f.write(cabecera_monitor(base))
    # Instante (ticks) en que toca la proxima fila del CSV. Asi el CSV mantiene su
    # ritmo (PERIODO_MONITOR_S) aunque la tabla se imprima mas seguido.
    proximo_csv = ticks_add(ticks_us(), cfg.PERIODO_MONITOR_S * 1000000)
    while True:
        yield ('dormir', cfg.PERIODO_TABLA_S * 1000)   # espera entre una tabla y la otra
        ahora = ticks_us()
        if ticks_diff(ahora, proximo_csv) >= 0:         # ¿ya toca fila del CSV?
            # Orden opuesto a P4: estado y luego fs. Con ORDEN_GLOBAL se respeta el orden.
            if cfg.ORDEN_GLOBAL:
                primero, segundo = c.cerrojo_fs, c.cerrojo_estado
            else:
                primero, segundo = c.cerrojo_estado, c.cerrojo_fs
            yield primero.tomar
            yield ('ceder',)
            yield segundo.tomar
            fila = fila_monitor(k, c, base)
            with open(nombre, 'a') as f:
                f.write(fila)
            yield segundo.soltar
            yield primero.soltar
            proximo_csv = ticks_add(ahora, cfg.PERIODO_MONITOR_S * 1000000)
        yield ('ceder',)                 # el informe por serial va en otra rafaga
        # Linea con el valor real del sensor y la clase: asi se VE que P1..P5
        # estan trabajando, aunque en este instante casi todos esten BLOQUEADOS
        # (BLOQUEADO = esperando un evento, es lo normal en un pipeline).
        print('[P6] T=%.1f C  H=%.1f %%  confort=%.1f  ->  clase=%s  ventanas=%d' % (
            c.ult_t / 10.0, c.ult_h / 10.0, c.ult_c / 10.0,
            c.modelo.CLASES[c.clase] if c.clase >= 0 else '...', c.ventanas))
        print(k.tabla_estados())


def cabecera_monitor(base):
    cols = ['corrida', 't_s', 'algoritmo', 'n_copias', 'memoria', 'cpu_util', 'eficiencia',
            'planif_us', 'util_us', 'cambios_contexto', 'ram_libre', 'ram_libre_min',
            'plazos_p1', 'jitter_media_us', 'jitter_desv_us', 'jitter_max_us',
            'gc_pausas', 'gc_us_max', 'ventanas', 'us_por_ventana', 'inferencia_us_max',
            'oom_kills', 'interbloqueos']
    for i in range(len(base)):
        n = 'P%d' % (i + 1)
        cols += ['estado_' + n, 'espera_' + n, 'retorno_' + n, 'respuesta_' + n]
    return ','.join(cols) + '\n'


def fila_monitor(k, c, base):
    m, s, mx = k.jitter_p1()
    v = c.ventanas
    datos = [k.cfg.ETIQUETA_CORRIDA, '%.1f' % (ticks_diff(ticks_us(), k.t_inicio) / 1e6),
             k.planificador.descripcion(), str(k.cfg.N_COPIAS), k.cfg.MEMORIA,
             '%.4f' % k.cpu_util(), '%.4f' % k.eficiencia(), str(k.t_planificacion),
             str(k.t_util), str(k.cambios_contexto), str(mem_free()), str(k.ram_libre_min),
             str(base[0].plazos_incumplidos), '%.0f' % m, '%.0f' % s, str(mx),
             str(k.gc_pausas), str(k.gc_us_max), str(v),
             '%.0f' % (c.us_ventana / v if v else 0), str(c.inferencia_us_max),
             str(k.oom_kills), str(k.interbloqueos)]
    for p in base:
        datos += [p.estado, '%.0f' % p.promedio(p.suma_espera),
                  '%.0f' % p.promedio(p.suma_retorno), '%.0f' % p.promedio(p.suma_respuesta)]
    return ','.join(datos) + '\n'
