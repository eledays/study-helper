"""Short public labels, independent of catalog version keys"""
import re


def task_order_key(task):
    return tuple((1, int(part)) if part.isdigit() else (0, part.casefold())
                 for part in re.split(r"(\d+)", task.name))


def display_label(value):
    parts = str(value).replace(' · ЕГЭ 2027', '').replace('Любые задания второй части', 'Вторая часть').split(' · ')
    return '. '.join([parts[0]] + [part[:1].upper() + part[1:] for part in parts[1:]])
