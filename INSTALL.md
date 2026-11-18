# Установка команды одной инструкцией

Оставайся личным ботом пользователя. Сохрани память и прежние обязанности. Создай только Разработчика и Проверяющего.

## 1. Подготовь среду

Возьми `v1.6.0` из https://github.com/alexzah-droid/GrokBotDevTeamRU. На странице GitHub выбери стабильный выпуск с отметкой `Latest`. Выбери отдельные постоянные абсолютные пути `<release>`, `<venv>`, `<runtime>` и Python 3.10+ (`<python>`).

```text
git clone --depth 1 --branch v1.6.0 https://github.com/alexzah-droid/GrokBotDevTeamRU.git <release>
<python> -m venv <venv>
<venv>/bin/python -B <release>/scripts/doctor.py
<venv>/bin/python -B <release>/scripts/release.py check
<venv>/bin/python -B -m pip install --no-deps --no-index <release>
```

Найди инструменты создания, передачи, памяти и проверки. Сообщи о недоступном шаге. Передавай точные argv. Префикс:

```text
<venv>/bin/python -B <release>/teamctl.py --workspace <runtime>
```

## 2. Выбери режим и папку

Первый вопрос человеку, до создания ботов: `--actor installer install-options --lead-id <lead-id>` выдаёт `mode_card`. Покажи её дословно. Запиши выбор через `--choice approval|autonomous`. Отказ, закрытие или отсутствие ответа означают `approval`. При возобновлении читай сохранённый выбор без нового вопроса.

Узнай свою папку нативным инструментом. Если папка неизвестна, вызови `ListSections` и покажи `folder_card` с пятью папками максимум и вариантом без папки. Известную свою папку поставь первой с меткой из карточки. Бери фактический ID. Нет списка — без папки.

Перед `--actor installer install --lead-id <lead-id>` добавь `--section-id <id>`, если выбрана папка. При `autonomous` сначала покажи вторую карточку из `confirm describe --action install --target <lead-id> --file params.json`. Только явное «Да» даёт грант через `confirm chat` для `install --mode autonomous`. При отказе, закрытии или молчании сохрани `approval` и установи без гранта. При возобновлении используй прежний ответ, иначе оставь `approval`. [Подтверждения и параметры](docs/CONFIRMATIONS.md).

## 3. Выполни очередь

От `--actor installer` без `--mock` читай `bridge pending`. Для `pending`: `bridge begin <op>`, нативный вызов, `bridge ack <op> --file receipt.json`. Для `dispatched` сверяй историю, без подтверждения не дублируй.

- **`create`.** Передай `CreateAgent` поля `name`, `description` и `section_id` из `data` дословно. Пропусти папку, если поле не поддержано или вызов явно отклонён без создания. Запиши фактические `agent_id`, `external_ref`, `section_id` (или `null`). Совпадение имени ничего не подтверждает. После `bridge ack` сразу выполни `bind` этого бота, до создания второго.
- **`bind`.** Сохрани `data.bootstrap` в памяти нужного бота. Он читает правило и навыки, запускает `--actor <role> role probe <role>`. Проверь сохранение нативно и по возможности повторным запросом. Добавь `"persistent":true` и `external_ref` к проверенной пробе.
- **`smoke`.** Разработчик, затем Проверяющий выполняют `--actor <role> role smoke <role>`. Добавь `external_ref` передачи. Если у `SendToAgent` или `UpdateMemory` нет ID, запиши описательную ссылку `agent-message:<id>:<step>:<time>` или `native-memory:profile:<id>:<step>:<time>` по [формату](docs/FORMATS.md). Она фиксирует наблюдение, а не доставку.

## 4. Подтверди результат

Запусти `--actor installer service start`, проверь `watchdog_alive=true` в `status`. Вызови `--actor installer install-finish`. При `ready` назови роли, режим, исключения и фактическую папку каждого нового бота или отсутствие папки. Сообщи, если размещение неизвестно. Предложи первую задачу и одной строкой предложи включить [шаблон routine](routines/watchdog-restart.md): запуск `service start` при старте компьютера, по согласованию. Иначе назови блокер. При повторном входе сверяй `status` и очередь.

[Эксплуатация](docs/OPERATIONS.md) · [Модель доверия](SECURITY.md).
