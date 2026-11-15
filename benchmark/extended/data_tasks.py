"""Synthetic deterministic data-analysis cases, checked by independent methods."""

from fractions import Fraction
import json
import sqlite3


def build_cases() -> list[dict]:
    """Return original dev cases; every reference is cross-checked on each call."""
    cases = []

    def dump(value):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    def add(number, category, text, value):
        answer = ({"type": "exact", "accepted": [str(value)]}
                  if isinstance(value, str) else {"type": "json", "value": value})
        cases.append({
            "id": f"ext-data-{number:02d}", "split": "dev",
            "category": category, "source": "original-generated",
            "prompt": "Все данные ниже синтетические. " + text,
            "answer": answer,
        })

    def fraction(value):
        return f"{value.numerator}/{value.denominator}"

    # Dictionary reduction versus pairwise dominance (including tombstones).
    events = [
        ["a", 10, 1, "u1", 120, False], ["b", 12, 2, "u2", 90, False],
        ["a", 14, 3, "u1", 150, False], ["c", 13, 4, "u1", 70, False],
        ["b", 12, 5, "u2", 110, False], ["d", 18, 6, "u3", 200, False],
        ["a", 14, 7, "u2", 160, False], ["c", 16, 8, "u1", 0, True],
        ["d", 17, 9, "u3", 999, False], ["e", 20, 10, "u1", 80, False],
        ["f", 19, 11, "u3", 50, False], ["f", 21, 12, "u3", 0, True],
        ["g", 22, 13, "u2", 40, False], ["e", 20, 14, "u1", 95, False],
    ]
    winners = {}
    for row in events:
        if row[0] not in winners or row[1:3] > winners[row[0]][1:3]:
            winners[row[0]] = row
    totals = {}
    for row in winners.values():
        if not row[5]:
            totals[row[3]] = totals.get(row[3], 0) + row[4]
    surviving = [r for r in events if not r[5] and not any(
        s[0] == r[0] and (s[1], s[2]) > (r[1], r[2]) for s in events)]
    brute = {u: sum(r[4] for r in surviving if r[3] == u)
             for u in sorted({r[3] for r in surviving})}
    assert totals == brute
    add(1, "event-dedup-latest", "Журнал версий [id,event_ts,seq,user,kopecks,deleted]: "
        + dump(events) + ". Для каждого id оставьте максимум по (event_ts,seq) "
        "лексикографически; seq уникален. Порядок входа приоритетом не является. "
        "Затем удалите победителей с deleted=true (к старой версии не возвращаться). "
        "Суммируйте kopecks по user победившей версии. Ответ только JSON-объект "
        "user → сумма; пользователей без оставшихся записей не включайте.", totals)

    # Online watermark state versus independent prefix maxima.
    arrivals = [["a", 1, 8], ["b", 4, 12], ["c", 2, 9], ["d", 9, 20],
                ["e", 5, 7], ["f", 6, 11], ["g", 12, 13], ["h", 9, 17],
                ["i", 8, 6], ["j", 15, 19], ["k", 12, 5], ["l", 10, 14],
                ["m", 14, 23], ["n", 20, 29], ["o", 17, 31], ["p", 16, 37]]
    windows, dropped, maximum = {}, [], None
    for event_id, timestamp, amount in arrivals:
        if maximum is not None and timestamp < maximum - 3:
            dropped.append(event_id)
        else:
            key = str((timestamp // 5) * 5)
            bucket = windows.setdefault(key, {"count": 0, "sum": 0})
            bucket["count"] += 1
            bucket["sum"] += amount
        maximum = timestamp if maximum is None else max(maximum, timestamp)
    accepted_positions = [i for i, r in enumerate(arrivals)
                          if i == 0 or r[1] >= max(s[1] for s in arrivals[:i]) - 3]
    reference_windows = {}
    for start in range(0, max(r[1] for r in arrivals) + 1, 5):
        rows = [arrivals[i] for i in accepted_positions
                if start <= arrivals[i][1] < start + 5]
        if rows:
            reference_windows[str(start)] = {"count": len(rows), "sum": sum(r[2] for r in rows)}
    reference_dropped = [r[0] for i, r in enumerate(arrivals) if i not in accepted_positions]
    assert (windows, dropped) == (reference_windows, reference_dropped)
    add(2, "event-time-watermark", "Записи [id,event_minute,amount] в порядке arrival: "
        + dump(arrivals) + ". Перед каждой записью watermark = максимум event_minute "
        "ВСЕХ ранее прибывших записей минус 3; перед первой он равен -бесконечности. "
        "Отбросьте запись, только если её event_minute < watermark (равенство допустимо). "
        "Затем обновите максимум, даже для отброшенной записи. Принятые события "
        "сгруппируйте по event-time окнам [5k,5k+5), не по arrival. Окна дополнительно "
        "не закрываются. Ответ только JSON {\"windows\":{\"начало\":{\"count\":N,\"sum\":S},...},"
        "\"dropped\":[id,...]}; только непустые окна, dropped в порядке arrival.",
        {"windows": windows, "dropped": dropped})

    # Net balances + integer floor versus purchase-wise scans + divmod.
    ledger = [[1, "buy", "p1", "u1", 10001], [2, "buy", "p2", "u1", 6000],
              [3, "buy", "p3", "u2", 4500], [4, "refund", "p1", "u1", 4000],
              [5, "buy", "p4", "u2", 9000], [6, "refund", "p3", "u2", 501],
              [7, "buy", "p5", "u1", 2000], [8, "refund", "p4", "u2", 2000],
              [9, "buy", "p6", "u3", 1429], [10, "refund", "p5", "u1", 2000],
              [11, "buy", "p7", "u3", 9999], [12, "refund", "p1", "u1", 1]]
    net = {}
    for _, kind, purchase, _, amount in ledger:
        net[purchase] = net.get(purchase, 0) + (amount if kind == "buy" else -amount)
    allocations, user_totals = {}, {}
    for _, kind, purchase, user, _ in ledger:
        if kind == "buy":
            payout = min(net[purchase] * 7 // 100, 450, 700 - user_totals.get(user, 0))
            allocations[purchase] = payout
            user_totals[user] = user_totals.get(user, 0) + payout
    candidates = []
    for t, kind, purchase, user, amount in ledger:
        if kind == "buy":
            refunds = sum(r[4] for r in ledger if r[1] == "refund" and r[2] == purchase)
            raw, _ = divmod((amount - refunds) * 7, 100)
            candidates.append((t, purchase, user, min(raw, 450)))
    check_allocations = {}
    for t, purchase, user, raw in candidates:
        preceding_raw = sum(r[3] for r in candidates if r[0] < t and r[2] == user)
        check_allocations[purchase] = max(0, min(raw, 700 - preceding_raw))
    check_totals = {u: min(700, sum(r[3] for r in candidates if r[2] == u))
                    for u in {r[2] for r in candidates}}
    assert (allocations, user_totals) == (check_allocations, check_totals)
    add(3, "cashback-refunds-caps", "Леджер [time,type,purchase,user,kopecks]: "
        + dump(ledger) + ". У каждой покупки один buy; refund уменьшает именно её "
        "сумму. Все суммы целые копейки, возвраты корректны. Это окончательный "
        "расчёт: сначала вычтите ВСЕ возвраты из каждой покупки. Её кандидат "
        "кешбэка = min(450, floor(остаток*7/100)). Затем распределите лимит "
        "700 копеек на пользователя между его покупками в порядке времени buy: "
        "каждая получает минимум кандидата и оставшегося лимита. Возвраты "
        "не являются отдельными начислениями. Включите нулевые начисления. "
        "Ответ только JSON {\"purchases\":{purchase:копейки,...},\"users\":{user:копейки,...}}.",
        {"purchases": allocations, "users": user_totals})

    # SQLite executes the literal query; Python constructs the outer join.
    customers = [[1, "A"], [2, "A"], [3, "B"], [4, "B"], [5, "C"], [6, "A"]]
    orders = [[101, 1, "paid", 100], [102, 1, "paid", None],
              [103, 1, "cancelled", 900], [104, 2, "paid", 0],
              [105, 3, "cancelled", 200], [106, 4, "paid", 70],
              [107, 4, "paid", 30], [108, None, "paid", 999]]
    query = """SELECT c.segment, COUNT(*), COUNT(o.id), COUNT(o.amount),
COUNT(DISTINCT c.id), COALESCE(SUM(o.amount),0)
FROM customers c LEFT JOIN orders o
ON c.id=o.customer_id AND o.status='paid'
GROUP BY c.segment ORDER BY c.segment"""
    with sqlite3.connect(":memory:") as conn:
        conn.execute("CREATE TABLE customers(id INTEGER, segment TEXT)")
        conn.execute("CREATE TABLE orders(id INTEGER, customer_id INTEGER, status TEXT, amount INTEGER)")
        conn.executemany("INSERT INTO customers VALUES (?,?)", customers)
        conn.executemany("INSERT INTO orders VALUES (?,?,?,?)", orders)
        sql_result = [list(r) for r in conn.execute(query)]
    python_result = []
    for segment in sorted({c[1] for c in customers}):
        joined = []
        for customer_id, group in customers:
            if group == segment:
                matches = [o for o in orders if o[1] == customer_id and o[2] == "paid"]
                joined.extend((customer_id, o) for o in (matches or [None]))
        python_result.append([segment, len(joined), sum(o is not None for _, o in joined),
                              sum(o is not None and o[3] is not None for _, o in joined),
                              len({c for c, _ in joined}),
                              sum(o[3] for _, o in joined if o is not None and o[3] is not None)])
    assert sql_result == python_result
    add(4, "sql-left-join-null-counts", "Таблицы customers(id,segment)=" + dump(customers)
        + "; orders(id,customer_id,status,amount)=" + dump(orders)
        + ". null — SQL NULL. Дубликатов id в каждой таблице нет. Вычислите "
        "результат SQL (семантика SQLite):\n" + query
        + ";\nОтвет только JSON-массив строк результата, столбцы и порядок как в SELECT.", sql_result)

    # Confusion matrix versus direct counts; Fraction versus integer cross-products.
    labels = ["cat", "dog", "fox", "owl"]
    predictions = [["cat", "cat"], ["cat", "dog"], ["cat", "cat"], ["cat", "fox"],
                   ["dog", "dog"], ["dog", "cat"], ["dog", "fox"], ["dog", "dog"],
                   ["dog", "dog"], ["fox", "cat"], ["fox", "fox"], ["fox", "dog"],
                   ["fox", "fox"], ["owl", "dog"], ["owl", "fox"], ["cat", "cat"],
                   ["dog", "owl"], ["fox", "fox"], ["cat", "owl"]]
    matrix = [[0 for _ in labels] for _ in labels]
    for truth, predicted in predictions:
        matrix[labels.index(truth)][labels.index(predicted)] += 1
    scores = []
    for i in range(len(labels)):
        tp = matrix[i][i]
        denominator = sum(matrix[i]) + sum(row[i] for row in matrix)
        scores.append(Fraction(2 * tp, denominator) if denominator else Fraction(0))
    macro = sum(scores, Fraction(0)) / len(labels)
    micro = Fraction(sum(matrix[i][i] for i in range(len(labels))), len(predictions))
    numerator, denominator = 0, 1
    check_scores = []
    for label in labels:
        tp = sum(t == p == label for t, p in predictions)
        fp = sum(t != label and p == label for t, p in predictions)
        fn = sum(t == label and p != label for t, p in predictions)
        n, d = 2 * tp, 2 * tp + fp + fn
        if d == 0:
            n, d = 0, 1
        check_scores.append(Fraction(n, d))
        numerator, denominator = numerator * d + n * denominator, denominator * d
    check_macro = Fraction(numerator, denominator * len(labels))
    total_tp = sum(t == p for t, p in predictions)
    total_fp = sum(t != p for t, p in predictions)
    check_micro = Fraction(2 * total_tp, 2 * total_tp + 2 * total_fp)
    assert (scores, macro, micro) == (check_scores, check_macro, check_micro)
    add(5, "multiclass-f1-exact", "Предсказания [true,pred]: " + dump(predictions)
        + ". Классы в порядке " + dump(labels)
        + ". Для каждого класса one-vs-rest F1=2TP/(2TP+FP+FN); нулевой "
        "знаменатель даёт F1=0. Macro — среднее F1 по всем перечисленным "
        "классам, micro — F1 после суммирования TP,FP,FN по классам. "
        "Ответ только JSON {\"per_class\":[\"p/q\",...],\"macro\":\"p/q\",\"micro\":\"p/q\"}. "
        "Каждая дробь несократимая с положительным знаменателем, включая /1.",
        {"per_class": [fraction(s) for s in scores], "macro": fraction(macro), "micro": fraction(micro)})

    # Group-set membership versus enumeration of all cross-split row pairs.
    samples = [["s01", "g1", "train"], ["s02", "g1", "train"],
               ["s03", "g2", "train"], ["s04", "g3", "train"],
               ["s05", "g3", "train"], ["s06", "g4", "train"],
               ["s07", "g1", "val"], ["s08", "g2", "val"],
               ["s09", "g2", "val"], ["s10", "g5", "val"],
               ["s11", "g3", "val"], ["s12", "g6", "val"],
               ["s13", "g1", "test"], ["s14", "g7", "test"],
               ["s15", "g4", "val"], ["s16", "g4", "val"]]
    train_groups = {r[1] for r in samples if r[2] == "train"}
    val_groups = {r[1] for r in samples if r[2] == "val"}
    overlap = sorted(train_groups & val_groups)
    leaked = sorted(r[0] for r in samples if r[2] == "val" and r[1] in train_groups)
    pair_count = sum(sum(r[1] == group and r[2] == "train" for r in samples)
                     * sum(r[1] == group and r[2] == "val" for r in samples)
                     for group in overlap)
    pairs = [(t, v) for t in samples for v in samples
             if t[2] == "train" and v[2] == "val" and t[1] == v[1]]
    check_overlap = sorted({t[1] for t, _ in pairs})
    check_leaked = sorted({v[0] for _, v in pairs})
    assert (overlap, leaked, pair_count) == (check_overlap, check_leaked, len(pairs))
    add(6, "group-split-leakage", "Датасет [sample_id,group_id,split]: " + dump(samples)
        + ". sample_id уникальны. Утечка: группа встречается и в train, и в val; "
        "test полностью игнорировать. Найдите отсортированные лексикографически "
        "списки таких групп и затронутых val sample_id, а также число пар "
        "(train sample_id,val sample_id) с одной группой. Ответ только JSON "
        "{\"groups\":[...],\"val_ids\":[...],\"pairs\":целое}.",
        {"groups": overlap, "val_ids": leaked, "pairs": pair_count})

    # Tuple-key stable sort versus insertion sort with explicit comparisons.
    ranking = [["z", 90, 20, 5], ["a", None, 1, 1], ["q", 95, None, 2],
               ["b", 90, 20, 5], ["x", 95, 30, 4], ["c", 95, 30, 3],
               ["r", 90, None, 1], ["d", 95, 30, 3], ["e", None, None, 0],
               ["f", 90, 10, 9], ["g", 95, None, 1], ["h", 90, 20, 2]]
    sorted_rows = sorted(ranking, key=lambda r: (r[1] is None, -r[1] if r[1] is not None else 0,
                                                r[2] is None, r[2] if r[2] is not None else 0, r[3]))

    def comes_before(a, b):
        for index, descending in ((1, True), (2, False), (3, False)):
            x, y = a[index], b[index]
            if x == y:
                continue
            if x is None:
                return False
            if y is None:
                return True
            return x > y if descending else x < y
        return False

    insertion = []
    for row in ranking:
        position = 0
        while position < len(insertion) and not comes_before(row, insertion[position]):
            position += 1
        insertion.insert(position, row)
    assert sorted_rows == insertion
    add(7, "stable-sort-nulls", "Строки [id,score,latency,created]: " + dump(ranking)
        + ". Отсортируйте: score по убыванию, null после любого числа; затем "
        "latency по возрастанию, null после любого числа; затем created по "
        "возрастанию. При равенстве всех трёх ключей сохраните порядок входа "
        "(id не является ключом). Ответ только JSON-массив id ВСЕХ строк в порядке результата.",
        [r[0] for r in sorted_rows])

    # Weighted exact fractions versus integer common-denominator arithmetic.
    cohorts = {"new": 5, "returning": 3, "loyal": 2}
    observations = [["new", "A", 1], ["new", "A", 0], ["new", "A", 0],
                    ["new", "A", None], ["new", "B", 1], ["new", "B", 0],
                    ["returning", "A", 1], ["returning", "A", 1], ["returning", "A", 0],
                    ["returning", "B", 1], ["returning", "B", 0], ["returning", "B", 0],
                    ["returning", "B", None], ["loyal", "A", 1], ["loyal", "A", 0],
                    ["loyal", "B", 1], ["loyal", "B", 1], ["loyal", "B", 0],
                    ["loyal", "B", 0], ["loyal", "B", None]]
    rates = {}
    for variant in ("A", "B"):
        weighted = Fraction(0)
        for cohort, weight in cohorts.items():
            values = [r[2] for r in observations if r[:2] == [cohort, variant] and r[2] is not None]
            weighted += weight * Fraction(sum(values), len(values))
        rates[variant] = weighted / sum(cohorts.values())
    counts = {}
    for cohort, variant, outcome in observations:
        if outcome is not None:
            success, total = counts.get((cohort, variant), (0, 0))
            counts[(cohort, variant)] = success + outcome, total + 1
    reference_rates = {}
    for variant in ("A", "B"):
        denominator = 1
        for cohort in cohorts:
            denominator *= counts[(cohort, variant)][1]
        numerator = sum(weight * counts[(cohort, variant)][0]
                        * (denominator // counts[(cohort, variant)][1])
                        for cohort, weight in cohorts.items())
        reference_rates[variant] = Fraction(numerator, denominator * sum(cohorts.values()))
    lift = (rates["B"] - rates["A"]) / rates["A"]
    reference_lift = reference_rates["B"] / reference_rates["A"] - 1
    assert (rates, lift) == (reference_rates, reference_lift)
    add(8, "weighted-cohort-standardization", "Целевые веса когорт=" + dump(cohorts)
        + "; наблюдения [cohort,variant,outcome]=" + dump(observations)
        + ". Каждая строка — отдельный пользователь. outcome=1 конверсия, 0 отказ, "
        "null незавершённое наблюдение: исключить и из числителя, и из знаменателя. "
        "Для каждой пары cohort,variant найдите долю конверсий среди завершённых; "
        "стандартизованная rate варианта = сумма(weight*доля)/сумма всех целевых "
        "весов. Это НЕ объединённая доля по строкам. Relative lift=(rate_B-rate_A)/rate_A, "
        "не проценты. Ответ только JSON {\"A\":\"p/q\",\"B\":\"p/q\",\"lift\":\"p/q\"}, "
        "все дроби несократимые, знаменатель положительный (включая /1).",
        {"A": fraction(rates["A"]), "B": fraction(rates["B"]), "lift": fraction(lift)})

    # Mutable stock/reservations versus recomputation from the accepted ledger.
    initial = {"a": 8, "b": 5}
    operations = [[4, 4, "reserve", "r3", "a", 5], [1, 1, "reserve", "r1", "a", 6],
                  [3, 3, "reserve", "r2", "b", 3], [2, 2, "reserve", "r0", "a", 3],
                  [5, 5, "cancel", "r1", None, 0], [6, 6, "reserve", "r4", "a", 5],
                  [6, 7, "reserve", "r5", "a", 4], [7, 8, "ship", "r2", None, 0],
                  [8, 9, "cancel", "r2", None, 0], [9, 10, "restock", None, "a", 3],
                  [10, 11, "reserve", "r6", "a", 4], [11, 12, "ship", "r4", None, 0],
                  [12, 13, "ship", "r5", None, 0], [13, 14, "reserve", "r7", "b", 3],
                  [14, 15, "restock", None, "b", 2], [15, 16, "reserve", "r8", "b", 4],
                  [16, 17, "cancel", "r6", None, 0], [17, 18, "ship", "r8", None, 0]]
    chronological = sorted(operations, key=lambda r: (r[0], r[1]))
    stock, active = dict(initial), {}
    shipped = {sku: 0 for sku in initial}
    accepted, rejected = [], []
    for _, seq, kind, reservation, sku, quantity in chronological:
        ok = True
        if kind == "restock":
            stock[sku] += quantity
        elif kind == "reserve":
            if stock[sku] < quantity:
                ok = False
            else:
                stock[sku] -= quantity
                active[reservation] = (sku, quantity)
        elif reservation not in active:
            ok = False
        else:
            saved_sku, saved_quantity = active.pop(reservation)
            if kind == "cancel":
                stock[saved_sku] += saved_quantity
            else:
                shipped[saved_sku] += saved_quantity
        (accepted if ok else rejected).append(seq)
    reserved = {sku: sum(q for s, q in active.values() if s == sku) for sku in initial}
    accepted_ledger, check_rejected = [], []
    for op in chronological:
        _, seq, kind, reservation, sku, quantity = op
        closed = {r[3] for r in accepted_ledger if r[2] in ("ship", "cancel")}
        open_rows = [r for r in accepted_ledger if r[2] == "reserve" and r[3] not in closed]
        if kind == "reserve":
            # Available = physical stock (initial + restock - shipped) - open holds.
            delivered = sum(r[5] for r in accepted_ledger if r[2] == "restock" and r[4] == sku)
            shipped_ids = {r[3] for r in accepted_ledger if r[2] == "ship"}
            consumed = sum(r[5] for r in accepted_ledger
                           if r[2] == "reserve" and r[3] in shipped_ids and r[4] == sku)
            held = sum(r[5] for r in open_rows if r[4] == sku)
            ok = initial[sku] + delivered - consumed - held >= quantity
        elif kind in ("ship", "cancel"):
            ok = any(r[3] == reservation for r in open_rows)
        else:
            ok = True
        if ok:
            accepted_ledger.append(op)
        else:
            check_rejected.append(seq)
    shipped_ids = {r[3] for r in accepted_ledger if r[2] == "ship"}
    closed = {r[3] for r in accepted_ledger if r[2] in ("ship", "cancel")}
    check_shipped = {s: sum(r[5] for r in accepted_ledger
                           if r[2] == "reserve" and r[3] in shipped_ids and r[4] == s) for s in initial}
    check_reserved = {s: sum(r[5] for r in accepted_ledger
                            if r[2] == "reserve" and r[3] not in closed and r[4] == s) for s in initial}
    check_stock = {s: initial[s] + sum(r[5] for r in accepted_ledger if r[2] == "restock" and r[4] == s)
                  - check_shipped[s] - check_reserved[s] for s in initial}
    assert (accepted, rejected, stock, reserved, shipped) == (
        [r[1] for r in accepted_ledger], check_rejected, check_stock, check_reserved, check_shipped)
    add(9, "transactional-inventory", "Начальный доступный остаток=" + dump(initial)
        + "; операции [time,seq,type,reservation,sku,qty]=" + dump(operations)
        + ". Выполните по (time,seq) по возрастанию; seq уникален. restock увеличивает "
        "доступный остаток. reserve принимается целиком только при достаточном "
        "доступном остатке и уменьшает его, создавая активный резерв. id в reserve "
        "уникальны и не переиспользуются, даже после отказа. cancel активного резерва "
        "возвращает его количество в доступный остаток; ship активного резерва "
        "увеличивает отгруженное, НЕ меняя доступное. Оба закрывают резерв. "
        "cancel/ship неактивного или неизвестного резерва отклоняются. Отклонение "
        "не меняет состояние. restock всегда принимается. Для cancel/ship sku и qty "
        "берутся из резерва, null и 0 — заглушки. Ответ только JSON "
        "{\"accepted\":[seq,...],\"rejected\":[seq,...],\"available\":{sku:N,...},"
        "\"reserved\":{sku:N,...},\"shipped\":{sku:N,...}}; списки в порядке обработки, "
        "во всех трёх объектах включить все исходные sku, в том числе нулевые.",
        {"accepted": accepted, "rejected": rejected, "available": stock,
         "reserved": reserved, "shipped": shipped})

    # Linear session scan versus undirected threshold-connected components.
    activity = [["e01", "u1", 21], ["e02", "u2", 5], ["e03", "u1", 0],
                ["e04", "u1", 10], ["e05", "u2", 16], ["e06", "u1", 21],
                ["e07", "u3", 7], ["e08", "u1", 31], ["e09", "u2", 26],
                ["e10", "u3", 17], ["e11", "u1", 42], ["e12", "u2", 36],
                ["e13", "u3", 28], ["e14", "u2", 47], ["e15", "u1", 43],
                ["e16", "u3", 28], ["e17", "u3", 38], ["e18", "u4", 99]]
    session_result = {}
    for user in sorted({r[1] for r in activity}):
        rows = sorted((r for r in activity if r[1] == user), key=lambda r: (r[2], r[0]))
        sessions = []
        for row in rows:
            if not sessions or row[2] - sessions[-1][-1][2] > 10:
                sessions.append([])
            sessions[-1].append(row)
        session_result[user] = {
            "sessions": len(sessions),
            "duration": sum(s[-1][2] - s[0][2] for s in sessions),
            "max_events": max(len(s) for s in sessions),
        }
    components = []
    unseen = set(range(len(activity)))
    while unseen:
        seed = min(unseen)
        unseen.remove(seed)
        component, frontier = [seed], [seed]
        while frontier:
            i = frontier.pop()
            neighbors = [j for j in unseen if activity[j][1] == activity[i][1]
                         and abs(activity[j][2] - activity[i][2]) <= 10]
            for j in neighbors:
                unseen.remove(j)
                component.append(j)
                frontier.append(j)
        components.append(component)
    check_sessions = {}
    for user in sorted({r[1] for r in activity}):
        groups = [c for c in components if activity[c[0]][1] == user]
        check_sessions[user] = {
            "sessions": len(groups),
            "duration": sum(max(activity[i][2] for i in c) - min(activity[i][2] for i in c) for c in groups),
            "max_events": max(map(len, groups)),
        }
    assert session_result == check_sessions
    add(10, "event-time-sessionization", "События [event_id,user,event_minute]: " + dump(activity)
        + ". id уникальны; одинаковые минуты — разные события, удалять их нельзя. "
        "Для каждого пользователя отсортируйте события по (event_minute,event_id). "
        "Новую сессию начинайте только если разница с предыдущим событием "
        "этого пользователя строго больше 10 минут; равенство 10 оставляет "
        "событие в той же сессии. Длительность сессии = последняя минута минус "
        "первая, в одиночной сессии 0. Ответ только JSON {user:{\"sessions\":N,"
        "\"duration\":D,\"max_events\":M},...}, где D — сумма длительностей, "
        "M — максимальное число событий в одной сессии; включить всех пользователей.", session_result)

    return cases
