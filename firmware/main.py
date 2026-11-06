# ============================================================================
# main.py - Arranque de SensorOS-Lite (H0 / M1).
# ----------------------------------------------------------------------------
# Secuencia completa al encender el ESP32:
#   1) codigo ROM interno  ->  2) bootloader + FreeRTOS + MicroPython
#   3) boot.py (apaga Wi-Fi/BT)  ->  4) main.py  ->  5) consola REPL
# En el paso 4 el kernel de SensorOS toma el control del sistema.
# ============================================================================
import gc                            # recolector de basura (para liberar RAM al iniciar)
import config as cfg                 # importamos config.py con el alias "cfg"
from kernel import Kernel            # el kernel (kernel.py)
import procesos as pr                # los 6 procesos (procesos.py)
import sensor                        # el sensor DHT22 (GPIO4)


# Elige y carga el modelo TinyML que pida config.py ('lr' o 'arbol').
def cargar_modelo(nombre):
    if nombre == 'arbol':            # si pidieron el arbol de decision...
        import modelo_arbol as m     # ...cargamos modelo_arbol.py
    else:                            # si no, por defecto...
        import modelo_lr as m        # ...cargamos la regresion logistica
    return m                         # devolvemos el modulo del modelo


# Crea el kernel, los recursos compartidos y los 6 procesos.
def construir(cfg):
    """Crea el kernel, los recursos compartidos y los seis procesos (NUEVO -> LISTO)."""
    gc.collect()                     # limpiamos basura antes de arrancar (empezar limpio)
    k = Kernel(cfg)                  # creamos el kernel con la configuracion
    s, tipo = sensor.abrir(cfg)      # abrimos el sensor -> (objeto_sensor, texto_del_tipo)
    print('[main] sensor:', tipo)    # avisamos que sensor quedo (DHT22 GPIO4)
    # Creamos el objeto con los recursos compartidos: sensor + modelo + buffers + semaforos.
    c = pr.Compartido(cfg, s, cargar_modelo(cfg.MODELO))
    # Esta tupla guarda la funcion de cada uno de los 6 procesos, en orden.
    funciones = (pr.p1_muestreo, pr.p2_rasgos, pr.p3_modelo,
                 pr.p4_registro, pr.p5_salida, pr.p6_monitor)
    # zip() une cada fila de PROCESOS (nombre, prioridad, cuota) con su funcion.
    for (nombre, prioridad, cuota), f in zip(cfg.PROCESOS, funciones):
        k.crear(nombre, f, prioridad, cuota, c)      # crea el PCB del proceso (en NUEVO)
    # E2: copias adicionales de P2 y P3, con la misma prioridad y cuota que los originales.
    for i in range(1, cfg.N_COPIAS):
        # i es el numero de copia (1, 2, ...) que el proceso recibe como argumento extra.
        k.crear('P2.%d Rasgos' % i, pr.p2_rasgos, cfg.PROCESOS[1][1], cfg.PROCESOS[1][2], c, i)
        k.crear('P3.%d Modelo' % i, pr.p3_modelo, cfg.PROCESOS[2][1], cfg.PROCESOS[2][2], c, i)
    for p in k.procesos:             # ahora si: pasamos todos de NUEVO a LISTO
        k.admitir(p)                 # admitir() crea el generador y lo pone en la cola
    return k, c                      # devolvemos el kernel y los recursos compartidos


# Enciende o apaga el LED de la placa (para indicar cosas al usuario).
def _led(valor):
    try:
        from machine import Pin     # en la placa, "machine" maneja los pines
        Pin(cfg.PIN_LED, Pin.OUT).value(valor)   # pone el pin del LED en 1 o 0
    except Exception:
        pass                        # en el PC no existe "machine": lo ignoramos


# Funcion principal: lo que se ejecuta al arrancar.
def principal():
    captura = cfg.MODO == 'captura'  # True si estamos en modo "grabar datos"
    if captura:
        # Damos 5 s para que la persona prepare el dedo; el LED encendido = grabando.
        import time
        print('[main] CAPTURA de "%s" (sesion %d, %s) en 5 s...' % (cfg.ETIQUETA, cfg.SESION, cfg.INTEGRANTE))
        time.sleep(5)                # esperamos 5 segundos
        _led(1)                      # LED encendido: aviso de "estoy grabando"
    k, c = construir(cfg)            # construimos el sistema completo
    # Avisamos que arranco y recordamos los comandos disponibles.
    print('[main] SensorOS-Lite con', k.planificador.descripcion(),
          '- comandos: f | r [q] | p | s | h | a(AUTO) | e | q')
    try:
        k.ejecutar(cfg.DURACION_S)   # corremos el kernel (0 = sin limite de tiempo)
    except KeyboardInterrupt:
        pass                         # si el usuario hace Ctrl+C, salimos sin error
    if captura:
        _led(0)                      # apagamos el LED (ya no grabamos)
        print('[main] captura terminada')
    print(k.tabla_estados())         # al salir, mostramos la tabla final de estados
    return k                         # devolvemos el kernel (util para pruebas)


# Este bloque hace que el sistema arranque solo cuando la placa ejecuta main.py.
if __name__ == '__main__':
    principal()
