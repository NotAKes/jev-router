"""Original deterministic algorithm tasks with independently checked answers."""

from collections import deque
from itertools import combinations, permutations, product
from math import comb
import json


def build_cases() -> list[dict]:
    """Build twelve dev cases; cross-check every answer by a second method."""
    cases = []

    def add(number, category, prompt, value):
        if isinstance(value, int):
            answer = {"type": "exact", "accepted": [str(value)]}
        else:
            answer = {"type": "json", "value": value}
        cases.append({
            "id": f"ext-algo-{number:02d}",
            "split": "dev",
            "category": category,
            "source": "original-generated",
            "prompt": prompt,
            "answer": answer,
        })

    def data(value):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    # 1. Weighted interval DP versus enumeration of all subsets.
    jobs = [(0, 3, 5), (1, 4, 6), (3, 5, 4), (4, 7, 7),
            (5, 8, 8), (7, 9, 5), (8, 11, 6), (9, 12, 8),
            (0, 5, 9), (3, 8, 12), (7, 12, 13), (12, 14, 4),
            (11, 14, 7), (14, 16, 3), (12, 16, 7)]
    ordered = sorted(jobs, key=lambda job: job[1])
    best, ways = [0], [1]
    for i, (start, end, reward) in enumerate(ordered):
        predecessor = 0
        for j in range(i):
            if ordered[j][1] <= start:
                predecessor = j + 1
        take = best[predecessor] + reward
        skip = best[-1]
        best.append(max(take, skip))
        ways.append((ways[predecessor] if take >= skip else 0)
                    + (ways[i] if skip >= take else 0))
    brute_best, brute_count = -1, 0
    for mask in range(1 << len(jobs)):
        chosen = sorted(jobs[i] for i in range(len(jobs)) if mask >> i & 1)
        if any(a[1] > b[0] for a, b in zip(chosen, chosen[1:])):
            continue
        reward = sum(job[2] for job in chosen)
        if reward > brute_best:
            brute_best, brute_count = reward, 1
        elif reward == brute_best:
            brute_count += 1
    assert (best[-1], ways[-1]) == (brute_best, brute_count)
    add(1, "weighted-interval-scheduling",
        "Задания заданы тройками [начало,конец,награда]: " + data(jobs)
        + ". Интервалы полуоткрытые [начало,конец); касание разрешено. "
        "Каждое задание можно выбрать не более одного раза, задания различаются "
        "позициями в списке. Найдите максимальную сумму наград и число различных "
        "подмножеств, достигающих её. Ответ — только JSON вида "
        '{"reward":целое,"count":целое}, без пояснений.',
        {"reward": best[-1], "count": ways[-1]})

    # 2. Topological shortest-path DP versus enumeration of complete paths.
    edges = [(0, 1, 3), (0, 2, 2), (0, 3, 5), (1, 3, 1),
             (1, 4, -2), (2, 3, 2), (2, 4, -1), (2, 5, 4),
             (3, 5, -1), (3, 6, 3), (4, 5, 2), (4, 6, 1),
             (5, 6, -1), (5, 7, 4), (6, 7, 2), (6, 8, 3),
             (7, 9, -2), (8, 9, -3)]
    adjacency = [[] for _ in range(10)]
    for u, v, weight in edges:
        adjacency[u].append((v, weight))
    distance, counts = [float("inf")] * 10, [0] * 10
    distance[0], counts[0] = 0, 1
    for u in range(10):
        for v, weight in adjacency[u]:
            candidate = distance[u] + weight
            if candidate < distance[v]:
                distance[v], counts[v] = candidate, counts[u]
            elif candidate == distance[v]:
                counts[v] += counts[u]
    path_costs = []

    def list_paths(u, cost):
        if u == 9:
            path_costs.append(cost)
        else:
            for v, weight in adjacency[u]:
                list_paths(v, cost + weight)

    list_paths(0, 0)
    shortest = min(path_costs)
    assert (distance[9], counts[9]) == (shortest, path_costs.count(shortest))
    add(2, "dag-shortest-path-count",
        "В ориентированном ациклическом графе вершины 0..9. Рёбра "
        "[откуда,куда,вес]: " + data(edges)
        + ". Найдите минимальный вес пути из 0 в 9 и количество путей такого "
        "веса. Пути различаются последовательностями вершин; параллельных рёбер "
        'нет. Ответ — только JSON {"distance":целое,"count":целое}.',
        {"distance": distance[9], "count": counts[9]})

    # 3. Order-ideal bitmask DP versus checking every permutation.
    precedence = [(0, 3), (0, 4), (1, 4), (1, 5), (2, 5),
                  (3, 6), (4, 6), (4, 7), (5, 7)]
    prerequisites = [0] * 8
    for u, v in precedence:
        prerequisites[v] |= 1 << u
    dp = [0] * (1 << 8)
    dp[0] = 1
    for mask in range(1 << 8):
        for v in range(8):
            if not mask >> v & 1 and prerequisites[v] & mask == prerequisites[v]:
                dp[mask | (1 << v)] += dp[mask]
    enumeration = 0
    for order in permutations(range(8)):
        positions = {v: i for i, v in enumerate(order)}
        enumeration += all(positions[u] < positions[v] for u, v in precedence)
    assert dp[-1] == enumeration
    add(3, "topological-order-count",
        "Вершины ориентированного графа: 0..7; рёбра: " + data(precedence)
        + ". Сколько существует топологических порядков (перестановок всех "
        "вершин, где начало каждого ребра раньше конца)? "
        "Ответ — только целое число, без взятия по модулю.", dp[-1])

    # 4. Exact-weight 0/1 knapsack DP versus subset enumeration.
    items = [(2, 4), (3, 5), (4, 7), (5, 9), (6, 10), (3, 5),
             (7, 12), (2, 3), (5, 8), (4, 7), (8, 14), (1, 1)]
    capacity = 19
    states = [None] * (capacity + 1)
    states[0] = (0, 1)
    for weight, value in items:
        for total in range(capacity, weight - 1, -1):
            previous = states[total - weight]
            if previous is None:
                continue
            candidate, count = previous[0] + value, previous[1]
            current = states[total]
            if current is None or candidate > current[0]:
                states[total] = (candidate, count)
            elif candidate == current[0]:
                states[total] = (candidate, current[1] + count)
    optimum = max(state[0] for state in states if state is not None)
    optimal_count = sum(state[1] for state in states
                        if state is not None and state[0] == optimum)
    feasible_values = []
    for mask in range(1 << len(items)):
        chosen = [item for i, item in enumerate(items) if mask >> i & 1]
        if sum(item[0] for item in chosen) <= capacity:
            feasible_values.append(sum(item[1] for item in chosen))
    assert (optimum, optimal_count) == (max(feasible_values), feasible_values.count(optimum))
    add(4, "knapsack-optimal-subset-count",
        "В рюкзак вместимости " + str(capacity) + " даны предметы [вес,ценность]: "
        + data(items) + ". Каждый предмет берётся 0 или 1 раз; даже одинаковые "
        "пары на разных позициях — разные предметы. Общий вес не превышает "
        "вместимость (заполнять точно не обязательно). Найдите максимальную "
        "ценность и число подмножеств с такой ценностью. "
        'Ответ — только JSON {"value":целое,"count":целое}.',
        {"value": optimum, "count": optimal_count})

    # 5. Edit-grid DP versus paired-position enumeration and gap combinatorics.
    source, target = "abacba", "bacaba"
    rows, cols = len(source), len(target)
    costs = [[0] * (cols + 1) for _ in range(rows + 1)]
    alignments = [[0] * (cols + 1) for _ in range(rows + 1)]
    alignments[0][0] = 1
    for i in range(rows + 1):
        for j in range(cols + 1):
            if i == j == 0:
                continue
            options = []
            if i:
                options.append((costs[i - 1][j] + 1, alignments[i - 1][j]))
            if j:
                options.append((costs[i][j - 1] + 1, alignments[i][j - 1]))
            if i and j:
                options.append((costs[i - 1][j - 1] + (source[i - 1] != target[j - 1]),
                                alignments[i - 1][j - 1]))
            costs[i][j] = min(cost for cost, count in options)
            alignments[i][j] = sum(count for cost, count in options if cost == costs[i][j])

    # Independent exhaustive enumeration: choose paired character positions;
    # enumerate gap interleavings between pairs, rather than applying the DP recurrence.
    enumerated = {}
    for paired in range(min(rows, cols) + 1):
        for left in combinations(range(rows), paired):
            for right in combinations(range(cols), paired):
                price = rows + cols - 2 * paired + sum(source[i] != target[j] for i, j in zip(left, right))
                count = 1
                previous_i = previous_j = -1
                for i, j in list(zip(left, right)) + [(rows, cols)]:
                    deletes, inserts = i - previous_i - 1, j - previous_j - 1
                    count *= comb(deletes + inserts, deletes)
                    previous_i, previous_j = i, j
                enumerated[price] = enumerated.get(price, 0) + count
    assert (costs[-1][-1], alignments[-1][-1]) == (min(enumerated), enumerated[min(enumerated)])
    add(5, "edit-distance-alignment-count",
        f'Строки s="{source}", t="{target}". Рассмотрим пути по решётке от '
        "(0,0) до (len(s),len(t)). Шаг (i,j)->(i+1,j) стоит 1 (удаление), "
        "(i,j)->(i,j+1) стоит 1 (вставка), (i,j)->(i+1,j+1) стоит 0 при "
        "s[i]=t[j], иначе 1. Разрешены только шаги внутри решётки. Найдите "
        "минимальную стоимость и число путей минимальной стоимости. Считаются "
        "именно последовательности шагов, а не результирующие строки или "
        'перестановки операций. Ответ — только JSON {"distance":целое,"count":целое}.',
        {"distance": costs[-1][-1], "count": alignments[-1][-1]})

    # 6. Finite-state string DP versus complete binary-string enumeration.
    length, ones = 17, 8
    state_counts = {("", 0, 0): 1}
    for _ in range(length):
        next_counts = {}
        for (suffix, used, occurrences), count in state_counts.items():
            for bit in "01":
                text = suffix + bit
                if text.endswith("111"):
                    continue
                key = (text[-2:], used + (bit == "1"),
                       occurrences + text.endswith("101"))
                next_counts[key] = next_counts.get(key, 0) + count
        state_counts = next_counts
    total = sum(count for (suffix, used, occurrences), count in state_counts.items()
                if used == ones and occurrences == 2)
    brute_total = 0
    for bits in product("01", repeat=length):
        text = "".join(bits)
        if text.count("1") == ones and "111" not in text:
            brute_total += sum(text[i:i + 3] == "101" for i in range(length - 2)) == 2
    assert total == brute_total
    add(6, "constrained-binary-strings",
        f"Сколько бинарных строк длины {length} содержат ровно {ones} единиц, "
        "не содержат подстроки 111 и содержат ровно два вхождения подстроки 101? "
        "Перекрывающиеся вхождения учитываются отдельно, начальные нули "
        "разрешены. Ответ — только целое число без модуля.", total)

    # 7. Tarjan low-link criterion versus component counting after deletion.
    undirected = [(0, 1), (1, 2), (2, 0), (1, 3), (3, 4), (4, 5),
                  (5, 3), (3, 6), (6, 7), (7, 8), (8, 6), (7, 9),
                  (10, 11), (11, 12)]
    neighbors = [[] for _ in range(13)]
    for u, v in undirected:
        neighbors[u].append(v)
        neighbors[v].append(u)
    discovery, low = [-1] * 13, [0] * 13
    clock, cuts = 0, set()

    def tarjan(u, parent):
        nonlocal clock
        discovery[u] = low[u] = clock
        clock += 1
        children = 0
        for v in neighbors[u]:
            if discovery[v] == -1:
                children += 1
                tarjan(v, u)
                low[u] = min(low[u], low[v])
                if parent != -1 and low[v] >= discovery[u]:
                    cuts.add(u)
            elif v != parent:
                low[u] = min(low[u], discovery[v])
        if parent == -1 and children > 1:
            cuts.add(u)

    for vertex in range(13):
        if discovery[vertex] == -1:
            tarjan(vertex, -1)

    def component_count(removed):
        unseen = set(range(13)) - {removed}
        count = 0
        while unseen:
            stack = [unseen.pop()]
            count += 1
            while stack:
                for v in neighbors[stack.pop()]:
                    if v in unseen:
                        unseen.remove(v)
                        stack.append(v)
        return count

    baseline = component_count(None)
    brute_cuts = [v for v in range(13) if component_count(v) > baseline]
    assert sorted(cuts) == brute_cuts
    add(7, "articulation-points",
        "Простой неориентированный граф имеет вершины 0..12 и рёбра "
        + data(undirected) + ". Точка сочленения — вершина, удаление которой "
        "вместе с инцидентными рёбрами строго увеличивает число компонент "
        "связности всего графа. Исходный граф может быть несвязным. "
        "Ответ — только JSON-массив всех точек сочленения по возрастанию.", sorted(cuts))

    # 8. Kosaraju + DAG DP versus transitive closure + path enumeration.
    directed = [(0, 1), (1, 2), (2, 0), (2, 3), (1, 5), (3, 4),
                (4, 3), (4, 5), (4, 6), (5, 6), (6, 7), (7, 6),
                (7, 8), (5, 9), (8, 9), (9, 10), (10, 9), (10, 11)]
    weights = [3, -2, 4, -4, 1, 6, 2, -1, -5, 4, 3, 2]
    forward, reverse = [[] for _ in weights], [[] for _ in weights]
    for u, v in directed:
        forward[u].append(v)
        reverse[v].append(u)
    visited, finish = set(), []

    def postorder(u):
        visited.add(u)
        for v in forward[u]:
            if v not in visited:
                postorder(v)
        finish.append(u)

    for u in range(len(weights)):
        if u not in visited:
            postorder(u)
    labels = [-1] * len(weights)
    groups = []
    for root in reversed(finish):
        if labels[root] != -1:
            continue
        index = len(groups)
        group, stack = [], [root]
        labels[root] = index
        while stack:
            u = stack.pop()
            group.append(u)
            for v in reverse[u]:
                if labels[v] == -1:
                    labels[v] = index
                    stack.append(v)
        groups.append(group)
    component_weights = [sum(weights[v] for v in group) for group in groups]
    dag = [set() for _ in groups]
    indegree = [0] * len(groups)
    for u, v in directed:
        a, b = labels[u], labels[v]
        if a != b and b not in dag[a]:
            dag[a].add(b)
            indegree[b] += 1
    queue = deque(i for i, degree in enumerate(indegree) if degree == 0)
    longest = component_weights.copy()
    while queue:
        u = queue.popleft()
        for v in dag[u]:
            longest[v] = max(longest[v], longest[u] + component_weights[v])
            indegree[v] -= 1
            if indegree[v] == 0:
                queue.append(v)
    reach = [[i == j for j in range(len(weights))] for i in range(len(weights))]
    for u, v in directed:
        reach[u][v] = True
    for k in range(len(weights)):
        for i in range(len(weights)):
            for j in range(len(weights)):
                reach[i][j] = reach[i][j] or (reach[i][k] and reach[k][j])
    remaining, other_groups = set(range(len(weights))), []
    while remaining:
        root = min(remaining)
        group = {v for v in remaining if reach[root][v] and reach[v][root]}
        remaining -= group
        other_groups.append(group)
    other_edges = {i: set() for i in range(len(other_groups))}
    other_weights = [sum(weights[v] for v in group) for group in other_groups]
    for i, group in enumerate(other_groups):
        for j, destination in enumerate(other_groups):
            if i != j and any(u in group and v in destination for u, v in directed):
                other_edges[i].add(j)
    path_sums = []

    def all_component_paths(u, cost, seen):
        path_sums.append(cost)
        for v in other_edges[u]:
            if v not in seen:
                all_component_paths(v, cost + other_weights[v], seen | {v})

    for u in other_edges:
        all_component_paths(u, other_weights[u], {u})
    assert max(longest) == max(path_sums)
    add(8, "scc-condensation-longest-path",
        "Ориентированный граф: вершины 0..11, веса вершин по порядку " + data(weights)
        + ", рёбра " + data(directed) + ". Сожмите каждую компоненту сильной "
        "связности в одну вершину с весом, равным сумме весов её вершин; "
        "петли удалите, повторные рёбра объедините. Найдите максимальную сумму "
        "весов вершин непустого пути в полученном DAG. Начинать и заканчивать "
        "можно где угодно, путь из одной компоненты разрешён; отрицательные "
        "веса не обнуляются. Ответ — только целое число.", max(longest))

    # 9. Edmonds-Karp augmentations versus enumeration of every s-t cut.
    network = [(0, 1, 8), (0, 2, 7), (0, 3, 4), (1, 2, 3),
               (1, 4, 6), (1, 5, 2), (2, 4, 4), (2, 5, 6),
               (3, 2, 2), (3, 5, 5), (4, 5, 3), (4, 6, 5),
               (4, 7, 3), (5, 6, 4), (5, 7, 6), (6, 7, 5)]
    residual = [[0] * 8 for _ in range(8)]
    for u, v, capacity in network:
        residual[u][v] += capacity
    flow = 0
    while True:
        parent = [-1] * 8
        parent[0] = 0
        queue = deque([0])
        while queue and parent[7] == -1:
            u = queue.popleft()
            for v, capacity in enumerate(residual[u]):
                if capacity > 0 and parent[v] == -1:
                    parent[v] = u
                    queue.append(v)
        if parent[7] == -1:
            break
        amount, v = float("inf"), 7
        while v:
            amount = min(amount, residual[parent[v]][v])
            v = parent[v]
        v = 7
        while v:
            u = parent[v]
            residual[u][v] -= amount
            residual[v][u] += amount
            v = u
        flow += amount
    cut_values = []
    for mask in range(1 << 6):
        side = {0} | {v for v in range(1, 7) if mask >> (v - 1) & 1}
        cut_values.append(sum(capacity for u, v, capacity in network
                              if u in side and v not in side))
    assert flow == min(cut_values)
    add(9, "maximum-flow",
        "Сеть имеет вершины 0..7, исток 0, сток 7. Ориентированные рёбра "
        "[откуда,куда,пропускная способность]: " + data(network)
        + ". Найдите максимальный поток: на каждом ребре поток неотрицателен "
        "и не больше его пропускной способности; в промежуточных вершинах "
        "сохраняется баланс. Отсутствующие рёбра имеют нулевую ёмкость. "
        "Ответ — только целое число.", flow)

    # 10. Difference of two monotone windows versus quadratic enumeration.
    array = [2, 1, 3, 2, 4, 1, 2, 3, 1, 4, 2, 2, 1, 3, 4, 1, 2, 3, 2, 1, 4, 3]
    limit, distinct = 16, 3

    def at_most(k):
        frequencies, left, running, result = {}, 0, 0, 0
        for right, value in enumerate(array):
            frequencies[value] = frequencies.get(value, 0) + 1
            running += value
            while len(frequencies) > k or running > limit:
                removed = array[left]
                running -= removed
                frequencies[removed] -= 1
                if frequencies[removed] == 0:
                    del frequencies[removed]
                left += 1
            result += right - left + 1
        return result

    subarrays = at_most(distinct) - at_most(distinct - 1)
    brute_subarrays = sum(len(set(array[left:right])) == distinct
                          and sum(array[left:right]) <= limit
                          for left in range(len(array))
                          for right in range(left + 1, len(array) + 1))
    assert subarrays == brute_subarrays
    add(10, "constrained-subarray-count",
        "Массив: " + data(array) + f". Сколько непустых непрерывных подмассивов "
        f"имеют ровно {distinct} различных значения и сумму не больше {limit}? "
        "Подмассивы различаются парами индексов начала и конца, даже если "
        "значения совпадают. Ответ — только целое число.", subarrays)

    # 11. Remainder-state DP versus enumeration and direct integer conversion.
    digits_length, modulus, sum_modulus = 7, 17, 5
    residue_counts = {(0, 0, -1): 1}
    for position in range(digits_length):
        next_counts = {}
        for (remainder, digit_sum, previous), count in residue_counts.items():
            for digit in range(6):
                if digit == previous or (position == 0 and digit == 0):
                    continue
                key = ((remainder * 10 + digit) % modulus,
                       (digit_sum + digit) % sum_modulus, digit)
                next_counts[key] = next_counts.get(key, 0) + count
        residue_counts = next_counts
    divisible = sum(count for (remainder, digit_sum, previous), count in residue_counts.items()
                    if remainder == digit_sum == 0)
    brute_divisible = 0
    for digits in product(range(6), repeat=digits_length):
        if digits[0] == 0 or any(a == b for a, b in zip(digits, digits[1:])):
            continue
        if sum(digits) % sum_modulus == 0:
            number = int("".join(map(str, digits)))
            brute_divisible += number % modulus == 0
    assert divisible == brute_divisible
    add(11, "modular-digit-dp",
        f"Сколько существует {digits_length}-значных десятичных чисел, "
        "использующих только цифры 0,1,2,3,4,5, у которых соседние цифры "
        f"различаются, само число делится на {modulus}, а сумма цифр делится "
        f"на {sum_modulus}? Первая цифра не ноль. Ответ — только целое число, "
        "без взятия количества по модулю.", divisible)

    # 12. Assignment bitmask DP versus all bijections.
    matrix = [[7, 3, None, 6, 4, 8, 5], [4, 6, 3, None, 7, 5, 8],
              [None, 5, 6, 3, 4, 7, 5], [6, None, 4, 7, 3, 5, 6],
              [5, 4, 7, 5, None, 3, 6], [3, 7, 5, 4, 6, None, 3],
              [4, 3, 5, 6, 5, 4, None]]
    assignment = {0: (0, 1)}
    for worker, row in enumerate(matrix):
        next_assignment = {}
        for mask, (cost, count) in assignment.items():
            for job, price in enumerate(row):
                if price is None or mask >> job & 1:
                    continue
                new_mask, candidate = mask | (1 << job), cost + price
                current = next_assignment.get(new_mask)
                if current is None or candidate < current[0]:
                    next_assignment[new_mask] = (candidate, count)
                elif candidate == current[0]:
                    next_assignment[new_mask] = (candidate, current[1] + count)
        assignment = next_assignment
    minimum, count = assignment[(1 << len(matrix)) - 1]
    assignment_costs = []
    for order in permutations(range(len(matrix))):
        prices = [matrix[i][job] for i, job in enumerate(order)]
        if all(price is not None for price in prices):
            assignment_costs.append(sum(prices))
    assert (minimum, count) == (min(assignment_costs), assignment_costs.count(minimum))
    add(12, "assignment-optimum-count",
        "Есть 7 работников и 7 работ, нумерация 0..6. Матрица стоимости "
        "(строка — работник, столбец — работа; null означает запрет): " + data(matrix)
        + ". Каждому работнику назначьте ровно одну работу, каждую работу "
        "ровно одному работнику. Найдите минимальную суммарную стоимость и "
        "число назначений с этой стоимостью. Назначения различаются хотя бы "
        'одной парой работник–работа. Ответ — только JSON {"cost":целое,"count":целое}.',
        {"cost": minimum, "count": count})

    assert len(cases) == 12
    return cases
