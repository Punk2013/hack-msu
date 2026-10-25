# Запуск демо

Сценарий: агент читает карточки клиентов из `demo/clients/` и пишет уведомления.
Модель (провайдер) видит только выдуманные ФИО, email, телефоны и паспорта,
а в итоговых файлах — настоящие данные. Все данные в репозитории синтетические.

## 1. Подготовка (один раз)

Нужны Linux, Python ≥ 3.12, Ollama. Для режима с изоляцией сети — `bubblewrap`.

```bash
sudo pacman -S ollama bubblewrap      # Arch; на других дистрибутивах — свой пакетный менеджер
ollama pull qwen2.5:7b                # ~4.7 ГБ; 3b быстрее, но часто бросает задачу
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
```

Проверка, что всё на месте:

```bash
ollama list                           # в списке есть qwen2.5:7b
.venv/bin/pytest -q                   # все тесты зелёные
```

Ollama должна работать: `ollama serve` в отдельном терминале или `sudo systemctl start ollama`.

## 2. Запуск

```bash
scripts/demo.sh qwen2.5:7b
```

С изоляцией сети агента (bubblewrap: агенту доступен только gateway):

```bash
SANDBOX=1 scripts/demo.sh qwen2.5:7b
```

Скрипт сам поднимает gateway на `127.0.0.1:8787`, копирует карточки во временную
рабочую папку, запускает агента и в конце печатает два пути:

```
Отчёт: /tmp/pii-gateway-demo.XXXXXX/report.html
Итоговые файлы: /tmp/pii-gateway-demo.XXXXXX/workspace/out
```

Прогон занимает 1–3 минуты на CPU. Порт 8787 должен быть свободен.

## 3. Что показать

1. **Итоговые файлы** — уведомления с настоящими ФИО, email и телефонами:
   ```bash
   cat /tmp/pii-gateway-demo.XXXXXX/workspace/out/*
   ```
2. **Отчёт** `report.html` (открыть в браузере): слева то, что ушло провайдеру,
   справа то, что осталось у пользователя. Замены подсвечены.
3. **Таблица замен** без самих данных (только ID, типы, счётчики):
   ```bash
   PII_VAULT_DIR=/tmp/pii-gateway-demo.XXXXXX/vault .venv/bin/python -m pii_gateway.vault stats demo
   ```
4. **Проверка на утечки**: ни одно значение из карточек не встречается в исходящих запросах.
   ```bash
   grep -rlwE 'Иванов|Смирнова|ivan\.ivanov|anna\.smirnova|900 000-00-0' \
     /tmp/pii-gateway-demo.XXXXXX/dumps/ || echo "утечек нет"
   ```
5. **Fail-closed и сеть** — автоматические проверки (≈1 минута):
   ```bash
   .venv/bin/pytest -q tests/integration tests/sandbox/test_full_gate.py
   ```

## 4. Если что-то пошло не так

| Симптом | Что делать |
| --- | --- |
| `Gateway не готов` | порт 8787 занят: `ss -ltnp \| grep 8787`, остановить старый процесс |
| `HTTP 404`/`connection refused` от Ollama | `ollama serve`; проверить `ollama list` |
| Модель остановилась, не записав файлы | запустить ещё раз: 7b недетерминирована, агент дважды напоминает ей продолжить |
| `sandbox refused to start` | нужен `bubblewrap` и разрешённые user namespaces; `TMPDIR` должен быть коротким (`/tmp`) |
| `HTTP 500 ... request not sent` | фильтр отказал — это fail-closed, наружу ничего не ушло; подробности в `gateway.log` рядом с отчётом |

Запасной вариант на защите: заранее сделанный прогон, сохранённые `report.html` и файлы из `out/`.

## Облачная модель вместо Ollama

Любой OpenAI-совместимый провайдер; ключ видит только gateway, не агент:

```bash
PII_UPSTREAM_BASE_URL=https://<провайдер>/v1 PII_UPSTREAM_API_KEY=<ключ> \
  scripts/demo.sh <имя-модели>
```
