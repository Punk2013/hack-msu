# Паспорт проекта

Статус: **разработка MVP**. Архитектура — [ARCHITECTURE.md](ARCHITECTURE.md).
Обсуждение — [Issue #4](https://github.com/Punk2013/hack-msu/issues/4).

## Задание

Локальный gateway между LLM-агентом и облачной моделью. Он заменяет персональные данные
в запросах на маркеры или вымышленные значения, а в ответах возвращает настоящие.
Агент работает с обезличенными данными, пользователь получает результат с реальными.

- **Пользователь и демо:** агент готовит уведомления клиентам по `.txt`/`.md` с синтетическими
  данными. Провайдер видит только замены, итоговые файлы содержат реальные значения.
- **MVP:** OpenAI Chat Completions; типы PERSON, EMAIL, PHONE, PASSPORT_RU; по умолчанию выдуманные данные (`realistic`), метки — запасной вариант;
  fail-closed; политика инструментов; sandbox сети агента.
- **Не входит:** Anthropic/Responses API, автоматика публичных лиц, OCR, вложения, остальные типы ПДн
  (только после сквозного прохода).
- **Стек:** Python ≥ 3.12, FastAPI + httpx, pytest, ruff. Linux (демо на машине Punk2013, Arch).
- **Только синтетические данные.** Реальные ПДн, ключи и дампы с ними в репозиторий не кладём.

## Расписание (25.10.2026, MSK)

Защита в **18:00**. Код замораживаем в **17:15**.

| Время | Что | Кто |
| --- | --- | --- |
| до 13:00 | Merge архитектуры (PR #8) и каркаса (bootstrap PR). Каждый подтверждает задачу в своём Issue | все |
| 13:00–14:00 | Обед. Агенты могут работать по своим Issue на синтетике | — |
| **14:00** | Checkpoint 1: у каждого модуля marker-путь или регэкспы с тестами в PR | все |
| **14:45** | **Gate 0**: клиент выбран (OpenCode или свой `demo_agent`), pass-through работает | роль 1 |
| **15:30** | Checkpoint 2: **сквозной проход** без стриминга: detect → mask → upstream → restore | 1 + 2 + 3 |
| 15:30–16:30 | Стриминг, политика tools, NER для PERSON, sandbox, стенд утечек | все |
| **16:30** | **Gate 1**: fail-closed, блокировка tools, сетевой тест. Дальше только исправления | 1 + 4 |
| 16:30–17:15 | Прогон демо, метрики, исправления. Realistic-режим — только если всё выше готово | все |
| **17:15** | **Code freeze**: в main только исправления для демо | — |
| 17:30–18:00 | Подготовка к защите: слайды, сценарий демо, репетиция | все |
| 18:00–19:30 | Защита | — |

Если checkpoint не пройден — пишем в Issue #4, что мешает, и режем объём, а не сроки.
Порядок отсечения: склонение ФИО (остаются выдуманные ФИО в именительном падеже) → NER (только regex + словарь имён) → стриминг → sandbox ОС
(остаётся политика tools в gateway).

## Команда и задачи

| Роль | Участник | Инструмент | Issue | Модули |
| --- | --- | --- | --- | --- |
| 1. Gateway, интеграция, демо | Punk2013 | Claude Code | [#9](https://github.com/Punk2013/hack-msu/issues/9) | `gateway/`, `core/`, `demo_agent`, CI |
| 2. Детекция ПДн | IldwayS | Codex | [#10](https://github.com/Punk2013/hack-msu/issues/10) | `detect/`, `tests/detect/` |
| 3. Vault, mask/restore | VladGabdulin | Antigravity | [#11](https://github.com/Punk2013/hack-msu/issues/11) | `pseudo/`, `vault/`, `tests/pseudo/`, `tests/vault/` |
| 4. Sandbox и стенд утечек | hornyhydrazine | Codex | [#12](https://github.com/Punk2013/hack-msu/issues/12) | `sandbox/`, `bench/`, `tests/sandbox/` |

Подготовка к защите: слайды — IldwayS и hornyhydrazine, демо — Punk2013, метрики — каждый по
своему модулю (таблица в ARCHITECTURE.md §9).

Ревью: любой другой участник. Пары по умолчанию: 1 ↔ 2, 3 ↔ 1, 4 ↔ 1, 2 ↔ 3.
Чтобы не ждать: ревью чужого PR — в течение 15 минут после просьбы в PR.

## Что делать агенту прямо сейчас

1. `git pull`, прочитать AGENTS.md, этот файл, ARCHITECTURE.md и свой Issue.
2. Ответить в своём Issue: принято / ветка / первый результат / блокер.
3. Работать только в своих модулях. Интерфейсы — строго из `src/pii_gateway/core/types.py`.
   Нужна правка общих типов — просьба в Issue #9.
4. До 14:00 открыть PR с первым рабочим кусочком и тестами. Маленькие PR лучше большого.

## Команды проекта

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest              # тесты
.venv/bin/ruff check          # линтер
```

Запуск gateway (OpenAI-совместимый endpoint на `http://127.0.0.1:8787/v1`):

```bash
PII_UPSTREAM_BASE_URL=http://localhost:11434/v1 \
PII_WORKSPACE=./demo PII_WRITE_ROOTS=./demo/out \
.venv/bin/uvicorn pii_gateway.gateway.app:app --host 127.0.0.1 --port 8787
```

Переменные: `PII_UPSTREAM_API_KEY` (ключ провайдера, хранится только у gateway),
`PII_LOCAL_TOKEN` (токен, который агент передаёт gateway), `PII_PROJECT_ID`, `PII_MODE=realistic|marker` (по умолчанию `realistic`),
`PII_PASSTHROUGH=1` (Gate 0: без изменений), `PII_DUMP_DIR` (полные дампы, **только синтетика**).
Клиент: указать `base_url` = `http://127.0.0.1:8787/v1`.

Демо одной командой (gateway + `demo_agent` + отчёт «что видел провайдер»):

```bash
scripts/demo.sh qwen2.5:7b
```

Vault, дампы и `report.html` сохраняются в `demo/.run/<время>/`, результат агента — в `demo/out/`.
CI запускает пробельную проверку, ruff и pytest.

## Контракты

Общий контракт ядра и протокола — ARCHITECTURE.md §4–§6, код — `src/pii_gateway/core/types.py`.
Зависимость своего модуля владелец добавляет в `pyproject.toml` в своём PR.
