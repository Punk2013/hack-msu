# Первый прогон #12 — 2026-10-25

Ветка: `agent/hornyhydrazine/sandbox-egress`. База перед проверкой: main `17cc862`.
Окружение: Linux 5.15.0-198-generic, Python 3.12.2, bubblewrap 0.6.1;
внутренний bridge — distro `/usr/bin/python3` (3.10).

Команды из корня проекта:

```bash
.venv/bin/python -m pytest -q -rs
.venv/bin/python -m ruff check
git diff --cached --check
```

Результат: **125 passed**, без SKIP, 19.52 s; ruff и проверка пробелов чистые.
Включены 26 тестов `tests/sandbox/` и security-регрессии из main.
Один warning — deprecation Starlette TestClient/httpx, не ошибка теста.

Реально выполнены:

- Bubblewrap с отдельными namespaces и сброшенными capabilities; один фиксированный
  TCP gateway доступен через UNIX relay.
- Дочерний Python не соединился с другим IPv4 listener хоста; вне sandbox тот же
  listener успешно принял положительный контроль.
- Host IPv6 listener доступен контрольному процессу хоста, из sandbox — нет.
- Host UDP receiver получил контрольный DNS-пакет снаружи; из namespace пакет
  до него не дошёл. Запрос относится только к синтетическому `example.test`.
- Скрытые vault/WAL/SHM и mock config/code не читаются; попытки записи, включая
  symlink, не изменили файлы хоста. Настоящий gateway source внутри не виден.
- Вход read-only, файл в выделенном export успешно записан; ключи провайдера
  и прочие credentials из окружения не попали агенту.
- Установленный `demo_agent` (только копия клиентского script) запущен в namespace
  со своим Python runtime, дошёл через relay до аутентифицированного gateway;
  upstream получил замену, клиент получил восстановленный ответ.
- Независимый HTTP receiver поймал намеренный passthrough leak и не получил
  запрос при ошибке фильтра/неизвестном вложении. JSON escapes и URL encoding
  канареек проверены отдельно.

Границы результата: detector/vault в boundary/client-тестах — явные test doubles;
это не качество реального NER и не полный пользовательский tool loop. На Arch
машине демо запуск ещё не выполнен. Живой провайдер, streaming у receiver,
произвольные кодировки/эксфильтрация, packet capture и OSS baseline не проверялись.
Gate 1 всей системы этим отчётом не объявляется пройденным.

Отдельно при небольшом ревью #19 воспроизведён числовой bypass через `seed`;
замечание передано автору в PR. Общие gateway-файлы в нашей ветке не изменялись.

## После независимого ревью #23

Замечания IldwayS к `60c0ffa` воспроизведены: wrong-path POST с канарейкой
терял тело, GET не попадал в счётчик. Новые регрессии до исправления дали
2 failed / 8 passed. После исправления все методы/URL учитываются, доступные
ограниченные тела проверяются независимо от статуса; неподдержанный framing,
незавершённые запросы и ошибки не дают чистого verdict. CLI при leak/inconclusive
возвращает ненулевой код. Добавлены проверки wrong path, GET/неизвестного метода,
GET-body, chunked/duplicate/oversize/missing framing, частичного тела и in-flight.

Portable unit-тесты Linux-профиля отделены явной фикстурой; добавлен production
отказ на Darwin. Пути UNIX sockets короткие; источник hardlink на той же FS.
Нативный запуск на Mac здесь не выполнен — запрошен повторный прогон ревьюера.

База обновлена до main `c85827c`. Полный Linux-прогон: **193 passed**, без SKIP,
25.49 s; профильный — 38 passed; ruff и diff --check чистые.
В режиме без bwrap: **34 passed / 4 skipped**, все пропуски — реальные namespaces.
Это не подтверждает сетевой gate в среде без bwrap.
