# Команды и форматы


Запускайте контроллер из постоянной копии релиза. Общий префикс: `<venv>/bin/python -B <release>/teamctl.py --workspace <runtime> --actor <role>`. Роли: `lead`, `developer`, `reviewer`, `user`, `installer`, `controller`. JSON передаётся через `--file`, можно `--file -` со stdin. Не собирайте shell-команду из текста пользователя.

Каждая команда печатает JSON. Ошибка печатает JSON в stderr и завершает процесс с кодом 2. После ошибки прочитайте состояние; не считайте действие сохранённым по одному намерению. Некоторые операции, например оценка или approve, сохраняют промежуточное состояние до попытки следующего этапа.

Защищённые человеческие действия требуют глобальный `--confirmation <одноразовый-код>` (или `-` для stdin). Evidence остаётся ссылкой для аудита, но не разрешением. Сначала выполните [процедуру подтверждения](CONFIRMATIONS.md). Примеры ниже с приёмкой, policy set, evaluation, approve/cancel/restore и автоприёмкой предполагают действующий код для точного действия. Ни один вызов без кода не должен объявляться успешно исполненным.

## Установка и роли

```text
install --lead-id <actual-id> [--mode autonomous]
bridge pending
bridge begin <operation-id>
bridge ack <operation-id> --file receipt.json
service start
install-finish
status
role read developer
role probe developer
```

`install --mock` разрешён только для локального теста. `role probe` вызывается от соответствующей роли. Форматы нативных receipts: [INSTALL.md](../INSTALL.md).

## Проект

Руководитель по запросу пользователя выполняет `project bind --file project.json`:

```json
{
  "repository": "/absolute/path/to/project",
  "command": ["/absolute/venv/bin/python", "-B", "-m", "unittest", "discover", "-s", "tests"],
  "timeout": 60,
  "evidence": "actual-user-message-reference"
}
```

Это пример, не универсальная команда всех проектов. Изучите реальный проект. Runtime должен находиться вне этого репозитория. Заново привязать проект во время активной задачи нельзя. После успешных независимых прогонов обеих ролей project status становится ready; до этого — unverified.

## Задача

`task create --file request.json` (Руководитель):

```json
{
  "request_id": "native-user-message-id",
  "user_ref": "actual-message-reference",
  "goal": "Добавить сложение двух чисел",
  "criteria": [{"id":"c1","text":"Сложение 2 и 3 возвращает 5"}],
  "max_rounds": 3,
  "auto_accept": false
}
```

Повтор request_id с теми же данными возвращает прежнюю задачу, с другими — ошибку. Для автоматической приёмки нужны `auto_accept: true` и `auto_accept_ref` — ссылка на явное поручение пользователя. Отсутствие ответа не даёт автоматическую приёмку.

Все действия ниже требуют уникальный `event_id`. Повтор того же действия возвращает известное состояние, повтор ID с другими данными отклоняется.

Руководитель — `task plan <id> --file plan.json`:

```json
{"event_id":"plan-message-id","steps":["Изучить интерфейс","Реализовать","Проверить"]}
```

Разработчик — `project verify --task <id>`, затем `task submit <id> --file submission.json`:

```json
{
  "event_id":"submission-message-id",
  "summary":"Добавлено сложение",
  "docs": {
    "architecture":{"not_needed":"Компоненты и интерфейсы не изменились"},
    "user":{"path":"README.md"},
    "agent":{"not_needed":"Команды работы с проектом не изменились"}
  }
}
```

Для каждой категории обязателен либо отслеживаемый путь, либо содержательная причина not_needed. Контроллер проверяет наличие; содержание независимо проверяет Reviewer.

Проверяющий — отдельный `project verify --task <id>`, затем `task review <id> --file review.json`:

```json
{
  "event_id":"review-message-id",
  "verdict":"pass",
  "criteria":[{"id":"c1","passed":true,"evidence":"run-...: проверено 2+3=5"}]
}
```

Для `changes_requested` или `blocked` нужен непустой `findings`: конкретный дефект/блокер с воспроизведением. Pass требует успешного собственного прогона и покрытия всех критериев ровно по одному разу.

Проверяющий может выполнить `project add-check --file extra-argv.json`, где файл — массив argv, например `["/absolute/venv/bin/python","-B","additional_checks.py"]`. Команды только дополняют базовый профиль; максимум восемь. Следующий `project verify` запускает базовую команду и все добавления, сохраняет их результаты. После добавления прежний pass не заменяет новый прогон; Разработчик не может менять этот дополнительный профиль.

Руководитель — `task demo <id> --file demo.json`:

```json
{"event_id":"demo-message-id","steps":["Открыть проверенную версию","Вычислить 2+3"],"usage":"Вызвать add(2,3)"}
```

Человек — `task accept <id> --file acceptance.json`:

```json
{"event_id":"accept-message-id","evidence":"actual-user-acceptance-message"}
```

`task block` и `task cancel` выполняются Руководителем или по запросу человека с `event_id` и `reason`. `task list` и `task show <id>` доступны для восстановления контекста. Уточнённые требования оформляются новой задачей; прежняя может быть отменена с причиной.

## Режимы и история

```text
policy show
policy channel terminal
policy channel chat
policy set autonomous --evidence <human-message>
policy set approval --role reviewer --evidence <human-message>
policy set inherit --role developer --evidence <human-message>
history developer
version <version-id>
diff <before-id> <after-id>
diff <experiment-id>
events --object <experiment-or-task-id> --limit 100
export
restore developer <version-id> --evidence <human-message>
```

Операции `policy set` и `restore` исполняются как user только от настоящего сообщения человека. Restore создаёт новую запись, не стирает историю, требует завершения активного эксперимента и простоя команды. Версия с несовместимым контрактом отклоняется.

`policy channel terminal` доступен Руководителю без гранта. Возврат к `chat` выполняется от `user` с терминальным грантом `policy.channel`. Команда `improve accept <id> --evidence <human-message>` подтверждает финал испытания в режиме approval и требует отдельный грант.

## Наблюдение

```text
service start
watch --once
service stop
```

`watch` исполняет actor controller; `service start` — installer/lead/controller/user; `service stop` — user/installer. При активном trial остановка запрещена: сначала отмените эксперимент, получив автоматический откат.
