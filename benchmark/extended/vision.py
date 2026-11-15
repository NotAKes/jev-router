"""Original raster assets and visual tasks; no external fonts or dependencies."""

from itertools import combinations
import struct
import zlib

# Minimal original 5x7 bitmap glyphs, used only for synthetic benchmark assets.
FONT = {
    '0': ['01110','10001','10011','10101','11001','10001','01110'],
    '1': ['00100','01100','00100','00100','00100','00100','01110'],
    '2': ['01110','10001','00001','00010','00100','01000','11111'],
    '3': ['11110','00001','00001','01110','00001','00001','11110'],
    '4': ['00010','00110','01010','10010','11111','00010','00010'],
    '5': ['11111','10000','10000','11110','00001','00001','11110'],
    '6': ['01110','10000','10000','11110','10001','10001','01110'],
    '7': ['11111','00001','00010','00100','01000','01000','01000'],
    '8': ['01110','10001','10001','01110','10001','10001','01110'],
    '9': ['01110','10001','10001','01111','00001','00001','01110'],
    'A': ['01110','10001','10001','11111','10001','10001','10001'],
    'B': ['11110','10001','10001','11110','10001','10001','11110'],
    'C': ['01111','10000','10000','10000','10000','10000','01111'],
    'D': ['11110','10001','10001','10001','10001','10001','11110'],
    'E': ['11111','10000','10000','11110','10000','10000','11111'],
    'F': ['11111','10000','10000','11110','10000','10000','10000'],
    'G': ['01111','10000','10000','10111','10001','10001','01111'],
    'H': ['10001','10001','10001','11111','10001','10001','10001'],
    'I': ['11111','00100','00100','00100','00100','00100','11111'],
    'J': ['00111','00010','00010','00010','10010','10010','01100'],
    'K': ['10001','10010','10100','11000','10100','10010','10001'],
    'L': ['10000','10000','10000','10000','10000','10000','11111'],
    'M': ['10001','11011','10101','10101','10001','10001','10001'],
    'N': ['10001','11001','10101','10011','10001','10001','10001'],
    'O': ['01110','10001','10001','10001','10001','10001','01110'],
    'P': ['11110','10001','10001','11110','10000','10000','10000'],
    'Q': ['01110','10001','10001','10001','10101','10010','01101'],
    'R': ['11110','10001','10001','11110','10100','10010','10001'],
    'S': ['01111','10000','10000','01110','00001','00001','11110'],
    'T': ['11111','00100','00100','00100','00100','00100','00100'],
    'U': ['10001','10001','10001','10001','10001','10001','01110'],
    'V': ['10001','10001','10001','10001','10001','01010','00100'],
    'W': ['10001','10001','10001','10101','10101','10101','01010'],
    'X': ['10001','10001','01010','00100','01010','10001','10001'],
    'Y': ['10001','10001','01010','00100','00100','00100','00100'],
    'Z': ['11111','00001','00010','00100','01000','10000','11111'],
    '-': ['00000','00000','00000','11111','00000','00000','00000'],
    '+': ['00000','00100','00100','11111','00100','00100','00000'],
    '=': ['00000','00000','11111','00000','11111','00000','00000'],
    ' ': ['00000'] * 7,
}


class Canvas:
    def __init__(self, width=900, height=600):
        self.width, self.height = width, height
        self.pixels = bytearray(b'\xff' * width * height * 3)

    def rect(self, x, y, width, height, color=(25, 35, 45)):
        if not (0 <= x <= x + width <= self.width and 0 <= y <= y + height <= self.height):
            raise ValueError('Drawing outside canvas')
        row = bytes(color) * width
        for line in range(y, y + height):
            start = (line * self.width + x) * 3
            self.pixels[start:start + width * 3] = row

    def text(self, x, y, value, scale=3):
        for char in str(value).upper():
            for j, row in enumerate(FONT[char]):
                for i, bit in enumerate(row):
                    if bit == '1':
                        self.rect(x + i * scale, y + j * scale, scale, scale)
            x += 6 * scale

    def table(self, headers, rows, title):
        self.text(24, 20, title)
        columns = len(headers)
        width = 840 // columns
        for j, row in enumerate([headers] + rows):
            for i, value in enumerate(row):
                self.text(30 + i * width, 80 + j * 45, value)
            self.rect(24, 110 + j * 45, 840, 1, (170, 180, 190))

    def png(self):
        def chunk(kind, data):
            return (struct.pack('>I', len(data)) + kind + data +
                    struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff))
        raw = b''.join(b'\0' + self.pixels[y * self.width * 3:(y + 1) * self.width * 3]
                       for y in range(self.height))
        return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', self.width, self.height, 8, 2, 0, 0, 0)) +
                chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b''))


def build_cases():
    """Return (cases, {relative asset path: PNG bytes}), all references cross-checked."""
    cases, assets = [], {}

    def image(name, headers, rows, title):
        canvas = Canvas()
        canvas.table(headers, rows, title)
        path = 'assets/' + name + '.png'
        assets[path] = canvas.png()
        return path

    def add(number, prompt, paths, value, category):
        cases.append({'id': f'ext-vision-{number:02d}', 'split': 'dev', 'category': category,
                      'source': 'original-generated', 'prompt': prompt, 'images': paths,
                      'answer': {'type': 'json', 'value': value} if isinstance(value, (dict, list)) else
                      {'type': 'exact', 'accepted': [str(value)]}})

    # Two images: join by ID, not row position.
    stock = [['A', 18], ['B', 12], ['C', 25], ['D', 9], ['E', 16]]
    delta = [['D', 4, 8], ['B', 10, 3], ['E', 2, 7], ['A', 0, 6], ['C', 1, 15]]
    balances = {key: n + next(i - o for k, i, o in delta if k == key) for key, n in stock}
    check = {k: next(n for key, n in stock if k == key) + i - o for k, i, o in delta}
    assert balances == check
    paths = [image('stock', ['ID', 'N'], stock, 'INITIAL STOCK'),
             image('movements', ['ID', 'IN', 'OUT'], delta, 'MOVEMENTS')]
    add(1, 'На двух изображениях начальные остатки N и движения IN/OUT. Соедините строки по ID, '
        'не по позиции. Итог = N + IN - OUT. Ответ только JSON-объект {ID:итог}, включить все товары.',
        paths, balances, 'vision-multi-image-join')

    # Instruction lives solely on the image, exercising image-only routing.
    c = Canvas()
    c.text(24, 30, 'ANSWER ONLY THE INTEGER RESULT')
    c.text(24, 110, '137 + 286 - 94 =', scale=5)
    assets['assets/image_only_arithmetic.png'] = c.png()
    answer = 137 + 286 - 94
    assert answer + 94 - 286 == 137
    add(2, '', ['assets/image_only_arithmetic.png'], answer, 'vision-image-only')

    # Image-only table: no task in the user text.
    c = Canvas()
    c.table(['ID', 'VALUE'], [['A', 37], ['B', 52], ['C', 46], ['D', 29]], 'ANSWER ONLY SUM OF VALUES')
    assets['assets/image_only_table.png'] = c.png()
    total = sum([37, 52, 46, 29])
    check = 0
    for v in reversed([37, 52, 46, 29]):
        check += v
    assert total == check
    add(3, '', ['assets/image_only_table.png'], total, 'vision-image-only')

    # Count paths on a raster grid, verify DP against enumeration of move orders.
    blocked = {(1, 1), (2, 3), (4, 2), (3, 4)}
    c = Canvas()
    for y in range(6):
        for x in range(6):
            c.rect(110 + x * 65, 70 + y * 65, 58, 58,
                   (30, 35, 40) if (x, y) in blocked else (220, 230, 240))
    c.text(115, 85, 'S', scale=4)
    c.text(440, 410, 'F', scale=4)
    assets['assets/grid_paths.png'] = c.png()
    dp = {}
    for y in range(6):
        for x in range(6):
            dp[x, y] = 0 if (x, y) in blocked else (1 if (x, y) == (0, 0) else
                                                  dp.get((x - 1, y), 0) + dp.get((x, y - 1), 0))
    count = 0
    for rights in combinations(range(10), 5):
        x = y = 0
        valid = True
        for step in range(10):
            x += step in rights
            y += step not in rights
            valid = valid and (x, y) not in blocked
        count += valid
    assert dp[5, 5] == count
    add(4, 'На картинке сетка 6×6 клеток. S — верхняя левая клетка, F — нижняя правая. '
        'Чёрные клетки запрещены, светлые разрешены. Можно переходить на соседнюю клетку '
        'только вправо или вниз. Сколько путей из S в F? Ответ только целое число.',
        ['assets/grid_paths.png'], count, 'vision-grid-path-count')

    invoice = [['A', 3, 125, 10], ['B', 2, 340, 25], ['C', 7, 80, 0], ['D', 4, 215, 5]]
    # Discount is per unit in kopecks, not percent; all integer arithmetic.
    amount = sum(q * (p - d) for _, q, p, d in invoice)
    units = [p - d for _, q, p, d in invoice for _ in range(q)]
    assert amount == sum(units)
    path = image('invoice', ['ID', 'QTY', 'PRICE', 'DISC'], invoice, 'INVOICE IN KOPECKS')
    add(5, 'На счёте QTY — количество, PRICE — цена одной единицы в копейках, DISC — '
        'скидка на одну единицу в копейках, НЕ проценты. Сумма строки QTY*(PRICE-DISC). '
        'Ответ только общая сумма целым числом копеек.', [path], amount, 'vision-invoice-arithmetic')

    durations = [['A', 0, 4, 7], ['B', 1, 6, 11], ['C', 4, 8, 9],
                 ['D', 6, 9, 6], ['E', 8, 12, 10], ['F', 9, 13, 12], ['G', 12, 15, 8]]
    best = 0
    for mask in range(1 << len(durations)):
        chosen = sorted((r for j, r in enumerate(durations) if mask >> j & 1), key=lambda r: r[1])
        if all(a[2] <= b[1] for a, b in zip(chosen, chosen[1:])):
            best = max(best, sum(r[3] for r in chosen))
    by_end = sorted(durations, key=lambda r: r[2])
    values = [0]
    for i, (_, start, _, reward) in enumerate(by_end):
        predecessor = max([0] + [j + 1 for j in range(i) if by_end[j][2] <= start])
        values.append(max(values[-1], values[predecessor] + reward))
    assert values[-1] == best
    path = image('scheduling', ['ID', 'START', 'END', 'REWARD'], durations, 'JOBS')
    add(6, 'На картинке задания: начало START, конец END, награда REWARD. Интервалы '
        'полуоткрытые [START,END), касание разрешено. Выберите непересекающееся подмножество '
        'с максимальной суммой наград. Каждое задание можно брать один раз. Ответ только число.',
        [path], best, 'vision-weighted-scheduling')
    return cases, assets
