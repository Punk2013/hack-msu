
## pi через PII Gateway

[pi](https://github.com/badlogic/pi-mono) работает как обычно, но все запросы к модели проходят
через gateway. Установка pi: `npm install -g @mariozechner/pi-coding-agent`.

```bash
cd /путь/к/рабочей/папке
/путь/к/hack-msu/scripts/pi.sh                      # меню: локальные модели Ollama или облако
/путь/к/hack-msu/scripts/pi.sh ollama:qwen2.5:7b
/путь/к/hack-msu/scripts/pi.sh openai:gpt-4o-mini   # ключ из OPENAI_API_KEY или спросит
```

- Ключ облачного провайдера получает только gateway, pi видит локальный токен.
- `bash` в pi отключён, остальные инструменты (read, edit, write, grep, find, ls) работают
  в текущей папке.
- После выхода из pi: `~/pii-gateway-reports/<время>/report.html` — по каждому обращению к модели
  видно, как текст был заменён в запросе и как ответ модели восстановлен обратно.
  Рядом `journal.jsonl`. Файлы содержат реальные данные и доступны только владельцу.
- На CPU локальная 7b с большим системным промптом pi отвечает медленно (минуты на ход).
