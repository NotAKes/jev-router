# OpenCode + Jev Router

OpenCode — клиент к OpenAI-совместимым API. В этой схеме он не знает ни одной
модели Polza: он всегда просит модель `auto`, а роутер сам выбирает уровень
(Haiku / Mini / Flash / Terra 6.1 / Sol 6.1 / Sol 6.2 / Sonnet / Sol Pro / Opus / Astra / Astra Pro).

Сначала должен работать сам роутер — см. [QUICKSTART.md](QUICKSTART.md).
Дальше всё сводится к одному JSON-файлу настроек.

## 1. Установка OpenCode

Нужна версия с поддержкой пользовательского провайдера
`@opencode/ai/providers/openai-compatible` (проверено на 2.3.0).

**macOS.** Официальное приложение ставится как обычный `.dmg`:

```text
/Applications/OpenCode.app
```

Внутри приложения есть свой CLI, но он **не попадает в `PATH`**. На проверенной
машине `which opencode` не находит ничего, поэтому команда `opencode` из
терминала не заработает «из коробки». Абсолютные пути той же установки:

```text
~/Library/Application Support/ai.opencode.desktop/cli/2.3.0-arm64/opencode-cli
```

Если терминальный запуск нужен постоянно, добавьте каталог с CLI в `PATH`
(номер версии и суффикс платформы в пути будут меняться после обновлений).
Приложению это не требуется: настройки оно читает из конфига, а не из `PATH`.

**Windows и Linux.** Скачайте OpenCode с [официальной страницы Desktop](https://opencode.ai/desktop). Для Windows есть [отдельная инструкция с автозапуском](WINDOWS_AUTOSTART_JEV_NO_ANTHROPIC.md).
Настройка провайдера ниже от системы не зависит: путь к конфигу
`~/.config/opencode/opencode.json` (на Windows — `%USERPROFILE%\.config\opencode\opencode.json`),
если OpenCode не переопределяет его сам.

Данные и логи приложения:

```text
~/.config/opencode/opencode.json      настройки
~/.local/share/opencode/opencode.db   история сессий (SQLite)
~/.local/share/opencode/log/opencode.log
```

## 2. Настройка провайдера одной командой

Из каталога проекта, где лежит `setup_opencode.sh`:

```bash
./setup_opencode.sh
```

Скрипт добавляет Jev в `~/.config/opencode/opencode.json` (или в `opencode/opencode.json` внутри `XDG_CONFIG_HOME`). При изменении существующего файла создаётся `opencode.json.<суффикс>.bak`. Остальные провайдеры и модель по умолчанию сохраняются; для явной смены модели запустите `./setup_opencode.sh --default`. При наличии JSONC используйте ручную настройку ниже.

## 3. То же самое руками

Для нового файла подойдёт пример ниже. В существующем файле сначала сделайте резервную копию, затем добавьте только запись `jev` в `providers`, не заменяя весь конфиг. Поле `model` меняйте, только если хотите сделать Jev моделью по умолчанию.

```json
{
  "$schema": "https://opencode.ai/config.json",
  "providers": {
    "jev": {
      "name": "Jev Router",
      "package": "@opencode/ai/providers/openai-compatible",
      "settings": { "baseURL": "http://127.0.0.1:8787/v1" },
      "models": {
        "auto": {
          "name": "Jev Auto",
          "capabilities": {
            "tools": true,
            "input": ["text", "image"],
            "output": ["text"]
          }
        }
      }
    }
  },
  "model": "jev/auto"
}
```

Что здесь важно:

| Поле | Значение | Зачем |
|---|---|---|
| `baseURL` | `http://127.0.0.1:8787/v1` | OpenCode сам добавляет `/chat/completions`; `/v1` не дублируйте |
| `models.auto` | одно имя `auto` | остальные модели роутер выбирает сам |
| `capabilities.tools` | `true` | иначе OpenCode не даст модели вызывать инструменты |
| `capabilities.input` | `["text", "image"]` | без `image` клиент вырежет картинку до отправки в роутер |
| `model` | `jev/auto` | провайдер по умолчанию |

API-ключ провайдера оставьте пустым. Если OpenCode требует непустое поле,
впишите `local`: роутер не проверяет клиентский ключ. Настоящий ключ Polza
вводится при запуске роутера (`POLZA_API_KEY`).

## 4. Проверка

```bash
# роутер запущен и отвечает
curl -s http://127.0.0.1:8787/v1/models

# первое сообщение уже платное: вызов Jev + выбранная модель
```

В терминале роутера появится строка вида

```text
10:28:47  sol    -> openai/gpt-6.2-sol  [Jev 0.46 (не уверен -> sol)]
```

После правки конфига OpenCode нужно перезапустить: настройки читаются при старте.

## 5. Картинки

Изображения уходят в роутер только если у модели `auto` в конфиге есть
`input: ["text", "image"]`. Оригинал получает выбранная модель, а для
маршрутизации картинку описывает дешёвая vision-модель — это дополнительные
расходы (см. [README.md](README.md)).

## 6. Ошибка 400 про encrypted content

```text
openai/gpt-6.2-sol ошибка 400: ... The encrypted content for item rs_...
could not be verified. Reason: Encrypted content could not be decrypted or parsed.
```

Причина: OpenCode сохраняет `reasoning` прошлых ответов и присылает его обратно.
Зашифрованный reasoning привязан к модели и эндпоинту, которые его выдали. Пока
уровень `sol` указывал на `gpt-6.1-sol`, блок был валиден; после перехода на
`gpt-6.2-sol` старый блок уже не расшифровывается, и запрос падает целиком —
на каждом ходе, пока элемент лежит в истории.

Что делать:

- с обновлением роутера (`sanitize_reasoning` в `jev_router.py`) чистить ничего не
  нужно: reasoning вырезается перед отправкой, старая сессия продолжает работать;
- если роутер старый, начните новый чат в OpenCode — история с чужим блоком
  останется позади;
- `JEV_KEEP_REASONING=1` отключает чистку. Это нужно только для отладки: с
  включённым флагом ошибка возвращается при смене модели под тем же уровнем.

## 7. Ограничения

- Прокси слушает `127.0.0.1`. OpenCode в Docker, на сервере или на телефоне
  до него не достучится.
- Ни авторизации, ни TLS у локального порта нет — не открывайте его в интернет.
- Роутер не переписывает запросы с моделью, отличной от `auto`: они уходят в
  Polza как есть.
