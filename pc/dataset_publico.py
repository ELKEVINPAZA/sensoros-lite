"""dataset_publico.py - Convierte un dataset PUBLICO en los CSV que espera pc/entrenar.py.

El enunciado del proyecto pide datos propios capturados con SensorOS-Lite; el
docente autorizo usar un dataset publico o datos sinteticos. Este script toma
weather_classification_data.csv (solo las columnas Temperature y Humidity),
calcula el CONFORT TERMICO con la MISMA formula que la placa (firmware/sensor.py)
y reparte las muestras en las 3 clases del sistema (frio, normal, calido) usando
los umbrales de firmware/config.py (CONFORT_FRIO=20, CONFORT_CALIDO=26).

Genera un archivo por (sesion, integrante, clase) con el MISMO formato que la
captura real de la placa:
    datos_publicos/s<sesion>_<integrante>_<clase>.csv   con columnas temp,hum,confort (x10)

Las muestras se escriben a 100 Hz (como P1). El DHT22 real solo entrega un valor
nuevo cada 2 s, asi que en la placa el valor se repite 200 veces seguidas (2 s a
100 Hz): por eso cada fila del dataset se repite --bloque veces. Con --bloque
>= 128 (= tamano de la ventana) la mayoria de las ventanas de entrenamiento
quedan EXACTAMENTE constantes, igual que en la placa; si el bloque es pequeno,
las ventanas nunca son constantes y el modelo falla al clasificar en vivo.

Ademas, dentro de cada archivo las filas se ordenan por confort: asi los bloques
consecutivos son parecidos (el ambiente cambia despacio) y las ventanas que caen
entre dos bloques tambien tienen poca variacion.

Uso:  python pc/dataset_publico.py [--csv weather_classification_data.csv]
                                  [--salida datos_publicos] [--por-clase 400]
                                  [--bloque 200] [--semilla 7]
"""
import argparse
import csv
import os
import random
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, 'firmware'))     # para importar el firmware
from sensor import confort          # noqa: E402  (misma formula de confort que la placa)
import config as cfg                # noqa: E402  (umbrales CONFORT_FRIO / CONFORT_CALIDO)

# Las 3 clases del sistema, en el mismo orden que usar el modelo (0, 1, 2).
CLASES = ('frio', 'normal', 'calido')


def clase_de(conf):
    """Convierte el confort en indice de clase (identico a firmware/sensor.py)."""
    if conf < cfg.CONFORT_FRIO:        # por debajo del umbral de frio
        return 0
    if conf < cfg.CONFORT_CALIDO:      # entre frio y calido -> normal
        return 1
    return 2                           # por encima -> calido


def leer_dataset(ruta):
    """Lee Temperature y Humidity y devuelve {clase: [(t, h), ...]}."""
    if not os.path.exists(ruta):
        sys.exit('No encuentro el dataset: ' + ruta)
    por_clase = {c: [] for c in CLASES}
    with open(ruta, newline='') as f:
        for fila in csv.DictReader(f):
            t = float(fila['Temperature'])     # temperatura en grados Celsius
            h = float(fila['Humidity'])        # humedad relativa en %
            por_clase[CLASES[clase_de(confort(t, h))]].append((t, h))
    return por_clase


def escribir(ruta, filas, bloque):
    """Escribe el CSV con la cabecera y cada fila repetida 'bloque' veces (x10)."""
    with open(ruta, 'w') as f:
        f.write('temp,hum,confort\n')          # misma cabecera que la captura real
        for t, h in filas:
            c = confort(t, h)                  # indice de bochorno
            linea = '%d,%d,%d\n' % (round(t * 10), round(h * 10), round(c * 10))
            f.write(linea * bloque)            # el DHT22 repite el valor hasta la proxima lectura


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--csv', default=os.path.join(RAIZ, 'weather_classification_data.csv'))
    ap.add_argument('--salida', default=os.path.join(RAIZ, 'datos_publicos'))
    ap.add_argument('--por-clase', type=int, default=400,
                    help='filas por clase; 0 = usar el minimo (balancear)')
    ap.add_argument('--bloque', type=int, default=200, help='repeticiones por fila (2 s de DHT22)')
    ap.add_argument('--semilla', type=int, default=7)
    a = ap.parse_args()

    por_clase_datos = leer_dataset(a.csv)
    print('Dataset:', a.csv)
    for c in CLASES:
        print('  %-8s %5d filas' % (c, len(por_clase_datos[c])))

    # Balanceamos: todas las clases usan la MISMA cantidad de filas (si no, el
    # modelo se sesga hacia 'frio'/'calido', que son las mas abundantes).
    disponible = min(len(por_clase_datos[c]) for c in CLASES)
    n = a.por_clase if a.por_clase > 0 else disponible
    if n > disponible:
        sys.exit('--por-clase=%d supera el minimo disponible (%d).' % (n, disponible))

    rnd = random.Random(a.semilla)             # mezcla reproducible
    # Reparto por sesion e integrante: 2 sesiones x 2 integrantes = 4 partes.
    reparto = ((1, 'luis'), (1, 'kevin'), (2, 'luis'), (2, 'kevin'))
    os.makedirs(a.salida, exist_ok=True)

    print('\nEscribiendo en', a.salida)
    for idx, c in enumerate(CLASES):
        filas = por_clase_datos[c][:]          # copiamos para no alterar el original
        rnd.shuffle(filas)                     # orden aleatorio (reproducible)
        filas = filas[:n]                       # nos quedamos con n filas balanceadas
        # Partimos las n filas en 4 trozos casi iguales (uno por sesion/integrante).
        tam = n // 4
        for k, (sesion, integrante) in enumerate(reparto):
            ini = k * tam
            fin = n if k == 3 else ini + tam    # el ultimo se queda con el sobrante
            trozo = filas[ini:fin]
            # Ordenamos por confort: los bloques consecutivos quedan parecidos
            # (el ambiente cambia despacio, como en la vida real).
            trozo.sort(key=lambda th: confort(th[0], th[1]))
            ruta = os.path.join(a.salida, 's%d_%s_%s.csv' % (sesion, integrante, c))
            escribir(ruta, trozo, a.bloque)
            muestras = len(trozo) * a.bloque
            ventanas = max(0, (muestras - 128) // 64 + 1)
            print('  %-22s %6d muestras -> %4d ventanas' %
                  (os.path.basename(ruta), muestras, ventanas))
    print('\nListo. Entrena con:  python pc/entrenar.py --datos %s --reservada 2'
          % os.path.basename(a.salida))


if __name__ == '__main__':
    main()
