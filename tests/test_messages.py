from datetime import date
from pathlib import Path
import ast
import pytest
from study import messages


def test_catalog_covers_message_calls_and_formats():
    # A missing key or broken substitution must be caught before a Telegram conversation
    for filename in ('bot.py', 'services.py', 'result_input.py'):
        tree = ast.parse(Path('study', filename).read_text())
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name):
                continue
            if call.func.id not in ('bot_text', 'bot_html', 'bot_texts') or not call.args:
                continue
            for node in ast.walk(call.args[0]):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and '.' in node.value and not node.value.endswith('.'):
                    value = messages._value(node.value)
                    assert isinstance(value, str if call.func.id != 'bot_texts' else list)
    text = messages.bot_text('history.record', day=date(2026, 10, 15), results='№1: 1/1')
    assert '№1: 1/1' in text
    score_prompt = messages.bot_text('entry.task_score', task_name='№19', maximum=4)
    assert '№19' in score_prompt and '4' in score_prompt


def test_copy_changes_are_loaded_without_restart(tmp_path, monkeypatch):
    path = tmp_path / 'bot_messages.toml'
    monkeypatch.setattr(messages, 'MESSAGES_PATH', path)
    path.write_text('[entry]\nsubject = "Первый вариант"\n')
    assert messages.bot_text('entry.subject') == 'Первый вариант'
    path.write_text('[entry]\nsubject = "Изменённая формулировка"\n')
    assert messages.bot_text('entry.subject') == 'Изменённая формулировка'


def test_variant_lists_are_independent_and_nonempty(tmp_path, monkeypatch):
    labels = messages.bot_texts('wellbeing.mood_labels')
    labels.clear()
    assert len(messages.bot_texts('wellbeing.mood_labels')) == 5
    path = tmp_path / 'bot_messages.toml'
    path.write_text('[notifications]\ndaily_variants = []\n')
    monkeypatch.setattr(messages, 'MESSAGES_PATH', path)
    with pytest.raises(TypeError):
        messages.bot_texts('notifications.daily_variants')
