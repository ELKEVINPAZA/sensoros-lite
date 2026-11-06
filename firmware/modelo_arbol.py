# modelo_arbol.py - Arbol de decision (CART) generado por pc/entrenar.py
# ENTRENADO CON DATASET PUBLICO (weather_classification_data.csv), etiquetado por confort termico con los umbrales del sistema
# Cada nodo: (rasgo, umbral, izquierdo, derecho); hoja: (-1, clase, 0, 0)
CLASES = ('frio', 'normal', 'calido')
NODOS = (
    (8, 19.95, 1, 2),
    (-1, 0, 0, 0),
    (8, 26.25, 3, 4),
    (-1, 1, 0, 0),
    (-1, 2, 0, 0),
)


def predecir(x):
    i = 0
    while True:
        rasgo, umbral, izq, der = NODOS[i]
        if rasgo < 0:
            return umbral
        i = izq if x[rasgo] <= umbral else der
