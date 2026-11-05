"""Dos ejemplos diarios calculados con valores nutricionales orientativos.

Pesos de arroz, patata, lentejas, pollo y salmón cocinados; fruta sin piel.
Las cifras son aproximaciones genéricas por 100 g/ml, no una prescripción.
"""
from copy import deepcopy

# nombre, proteínas, carbohidratos y grasas por 100 g (leche en ml).
FOODS = {
    'avena': ('copos de avena (en seco)', 13.5, 60, 7),
    'leche': ('leche semidesnatada', 3.4, 4.8, 1.5),
    'platano': ('plátano', 1.1, 23, 0.3),
    'pan': ('pan', 9, 50, 3),
    'aceite': ('aceite de oliva', 0, 0, 100),
    'arroz': ('arroz cocido', 2.7, 28, 0.3),
    'pollo': ('pechuga de pollo cocinada', 31, 0, 3.6),
    'verduras': ('verduras variadas', 2, 5, 0.3),
    'yogur': ('yogur natural', 5, 6, 2),
    'nueces': ('nueces', 15, 14, 65),
    'salmon': ('salmón cocinado', 22, 0, 12),
    'patata': ('patata cocida', 2, 20, 0.1),
    'huevo': ('huevo sin cáscara', 13, 1, 10),
    'manzana': ('manzana', 0.3, 14, 0.2),
    'pera': ('pera', 0.4, 15, 0.1),
    'lentejas': ('lentejas cocidas', 9, 20, 0.4),
    'miel': ('miel', 0.3, 82, 0),
    'naranja': ('naranja', 1, 12, 0.2),
}

# Cinco tomas: desayuno, media mañana, comida, merienda, cena.
BASE = [
    [
        {'avena': 65, 'leche': 300, 'platano': 150},
        {'pan': 80, 'yogur': 250},
        {'arroz': 250, 'pollo': 110, 'verduras': 200, 'aceite': 15},
        {'manzana': 180, 'nueces': 30},
        {'patata': 300, 'salmon': 75, 'verduras': 200, 'aceite': 15},
    ],
    [
        {'pan': 70, 'aceite': 10, 'huevo': 100, 'naranja': 200},
        {'yogur': 250, 'avena': 40, 'platano': 120},
        {'lentejas': 250, 'arroz': 110, 'verduras': 200, 'aceite': 10},
        {'manzana': 180, 'nueces': 25},
        {'patata': 300, 'pollo': 110, 'verduras': 200, 'aceite': 15},
    ],
]
MEAL_NAMES = ('Desayuno', 'Media mañana', 'Comida', 'Merienda', 'Cena')


def menus_for(day, session_type):
    """Dos días completos, no mezclar tomas de ambos sin recalcular."""
    if session_type not in ('descanso', 'facil', 'calidad', 'larga'):
        raise ValueError('tipo de sesión desconocido')
    menus = deepcopy(BASE)
    # Alterna fruta a lo largo de la semana, manteniendo cantidades calculables.
    if (day.toordinal() // 2) % 2:
        for menu in menus:
            menu[3].pop('manzana')
            menu[3]['pera'] = 165
    if session_type == 'facil':
        menus[0][1]['pan'] += 40
        menus[0][2]['arroz'] += 78
        menus[1][0]['pan'] += 30
        menus[1][2]['arroz'] += 50
        menus[1][2]['aceite'] += 5
        menus[1][4]['pollo'] -= 10
    elif session_type in ('calidad', 'larga'):
        menus[0][1]['pan'] += 80
        menus[0][0]['platano'] += 100
        menus[0][0]['miel'] = 40
        menus[0][2]['arroz'] += 120
        menus[1][0]['pan'] += 120
        menus[1][1]['platano'] += 50
        menus[1][0]['miel'] = 40
        menus[1][4]['patata'] += 160
        menus[1][4]['pollo'] -= 10
    out = []
    for menu in menus:
        sums = [0.0, 0.0, 0.0]
        meals = []
        for name, components in zip(MEAL_NAMES, menu):
            parts = []
            for key, amount in components.items():
                label, p, c, f = FOODS[key]
                parts.append(f"{amount} {'ml' if key == 'leche' else 'g'} de {label}")
                for i, value in enumerate((p, c, f)):
                    sums[i] += amount * value / 100
            meals.append((name, ', '.join(parts)))
        p, c, f = sums
        out.append({'meals': meals, 'protein': round(p), 'carbs': round(c), 'fat': round(f),
                    'kcal': round(4 * p + 4 * c + 9 * f)})
    # Se alterna el orden diariamente; ambas opciones siempre están disponibles.
    return out if day.toordinal() % 2 == 0 else out[::-1]
