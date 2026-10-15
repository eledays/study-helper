"""Result drafts shared by Telegram input modes"""
import re
from .messages import bot_text, UserMessageError


def full_result(tasks):
    return {str(task.id): task.max_score for task in tasks}


def grid_rows(tasks, selected, callback, columns=4):
    buttons = [(bot_text('format.selected_task_button' if t.id in selected else 'format.task_button', task_name=t.name[:35]), callback(t.id)) for t in tasks]
    return [buttons[n:n+columns] for n in range(0, len(buttons), columns)]


def parse_points(text, tasks):
    tokens = re.split(r'[\s,;]+', text.strip())
    if len(tokens) != len(tasks):
        raise UserMessageError(bot_text('errors.bulk_count', count=len(tasks)))
    values = {}
    for task, token in zip(tasks, tokens):
        if token in ('-', '—'):
            values[str(task.id)] = None
            continue
        try:
            value = int(token)
        except ValueError:
            raise UserMessageError(bot_text('errors.bulk_format'))
        if not 0 <= value <= task.max_score:
            raise UserMessageError(bot_text('errors.bulk_range', task_name=task.name, maximum=task.max_score))
        values[str(task.id)] = value
    return values
