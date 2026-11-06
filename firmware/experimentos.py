# experimentos.py - Corre los experimentos E1 a E4 en la placa y deja los CSV en resultados/.
# Reglas de medicion: mismo firmware y misma machine.freq(), Wi-Fi y Bluetooth
# apagados (boot.py), tiempos con ticks_us/ticks_diff, tres repeticiones.
# Desde el REPL:
#     import experimentos
#     experimentos.e1()   # ~12 min     experimentos.e2()   # ~15 min
#     experimentos.e3()   # ~6 min      experimentos.e4()   # segundos
import gc
import os
from compat import ticks_us, ticks_diff, mem_free
from demos import Cfg
import main
import procesos as pr

REPETICIONES = 3
DURACION_S = 60


def _frecuencia():
    try:
        import machine
        return machine.freq()
    except Exception:
        return 0


def _correr(experimento, etiqueta, rep, duracion, **cambios):
    corrida = '%s_%s_r%d' % (experimento, etiqueta, rep)
    cfg = Cfg(DURACION_S=duracion, ETIQUETA_CORRIDA=corrida, **cambios)
    gc.collect()
    k, c = main.construir(cfg)
    print('\n>>> %s (%s, %d s)' % (corrida, k.planificador.descripcion(), duracion))
    k.ejecutar(duracion)
    base = k.procesos[:6]
    ruta = '%s/%s_resumen.csv' % (cfg.CARPETA_RESULTADOS, experimento)
    nuevo = True
    try:
        os.stat(ruta)
        nuevo = False
    except OSError:
        pass
    with open(ruta, 'a') as f:
        if nuevo:
            f.write('experimento,variante,repeticion,freq_hz,' + pr.cabecera_monitor(base))
        f.write('%s,%s,%d,%d,' % (experimento, etiqueta, rep, _frecuencia()) + pr.fila_monitor(k, c, base))
    del k, c
    gc.collect()


def e1(duracion=DURACION_S, quantums=(2, 10)):
    """E1 Planificacion: FCFS, Round Robin con dos quantums y Prioridades."""
    variantes = [('FCFS', {'ALGORITMO': 'FCFS'})]
    for q in quantums:
        variantes.append(('RRq%d' % q, {'ALGORITMO': 'RR', 'QUANTUM_MS': q}))
    variantes.append(('PRIO', {'ALGORITMO': 'PRIO'}))
    for rep in range(1, REPETICIONES + 1):
        for etiqueta, cambios in variantes:
            _correr('E1', etiqueta, rep, duracion, **cambios)


def e2(duracion=DURACION_S, algoritmo='PRIO'):
    """E2 Escalamiento de carga: copias de P2 y P3 de 1 a 5 con el mismo algoritmo."""
    for rep in range(1, REPETICIONES + 1):
        for n in range(1, 6):
            _correr('E2', 'copias%d' % n, rep, duracion, ALGORITMO=algoritmo, N_COPIAS=n)


def e3(duracion=DURACION_S, algoritmo='PRIO'):
    """E3 Memoria: A objetos nuevos por ventana vs B buferes preasignados."""
    for rep in range(1, REPETICIONES + 1):
        for m in ('A', 'B'):
            _correr('E3', 'mem' + m, rep, duracion, ALGORITMO=algoritmo, MEMORIA=m)


def e4(archivo='rasgos_reservada.csv'):
    """E4 Modelo TinyML: RAM, tiempo por ventana y tamano de cada modelo en la placa.
    La exactitud en la sesion reservada se recalcula aqui con los mismos rasgos.
    El archivo se lee VENTANA POR VENTANA (streaming): asi no hacen falta los
    ~200 KB que ocuparia tener todas las ventanas en RAM (la placa no los tiene)."""
    from array import array
    ruta = 'resultados/E4_modelos_placa.csv'
    with open(ruta, 'w') as f:
        f.write('modelo,repeticion,freq_hz,ventanas,exactitud,us_mediana,us_max,ram_bytes,tamano_bytes\n')
    for nombre in ('lr', 'arbol'):
        gc.collect()
        libre0 = mem_free()
        m = main.cargar_modelo(nombre)
        gc.collect()
        ram = libre0 - mem_free()          # RAM que ocupa el modelo cargado
        tam = os.stat('modelo_%s.py' % nombre)[6]
        for rep in range(1, REPETICIONES + 1):
            tiempos = []
            aciertos = 0
            n = 0
            gc.collect()
            with open(archivo) as f:
                f.readline()               # saltamos la cabecera
                for linea in f:
                    v = linea.strip().split(',')
                    if len(v) < 2:         # linea vacia o mal formada: la ignoramos
                        continue
                    x = array('f', [float(t) for t in v[:-1]])   # una sola ventana
                    y = int(v[-1])
                    t0 = ticks_us()
                    p = m.predecir(x)
                    tiempos.append(ticks_diff(ticks_us(), t0))
                    aciertos += (p == y)
                    n += 1
                    if n % 256 == 0:       # de vez en cuando liberamos la basura
                        gc.collect()
            tiempos.sort()
            with open(ruta, 'a') as f:
                f.write('%s,%d,%d,%d,%.4f,%d,%d,%d,%d\n' % (
                    nombre, rep, _frecuencia(), n, aciertos / n,
                    tiempos[len(tiempos) // 2], tiempos[-1], ram, tam))
        print('E4', nombre, 'listo')


def todos():
    e1()
    e2()
    e3()
    e4()
