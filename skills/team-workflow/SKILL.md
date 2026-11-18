---
name: team-workflow
description: Планируй, передавай и принимай задачи трёх ролей.
---

# Задачи команды

Бери префикс из стартового правила до `role read`. Держи свой `actor`. `--actor user` — только с действующим грантом на это действие. Без гранта — никогда. Передавай JSON файлом или stdin, без подстановки в оболочку. [Форматы](../../docs/FORMATS.md).

1. **Руководитель.** Начни с `status`. При `watchdog_alive=false` запусти `service start` и дождись heartbeat для применения в живой установке.
2. Привяжи разрешённые Git-проект и команду через `project bind --file project.json`. В `approval` сначала запроси грант `project.bind`. Запиши цель, критерии и лимит через `task create --file request.json`. Для автоприёмки получи отдельный грант `task.auto_accept` на весь запрос с `auto_accept_ref`.
3. Запиши `task plan <id> --file plan.json`. По `bridge pending` отметь `bridge begin <op>`, отправь `data` и запиши фактическое подтверждение через `bridge ack <op> --file receipt.json`. Для `dispatched` сверь историю. Проверяй доставку по подтверждению, исполнение — по состоянию задачи.
4. **Разработчик и Проверяющий.** Сверь проект, ревизию и состояние через `task show <id>`. После изменения сообщи о `pending` Руководителю для передачи. Объясни блокер.
5. **Руководитель.** Верни замечания в пределах бюджета. Новые требования — новая задача. Для остановки укажи причину в `task block|cancel <id> --file reason.json`. После актуального `pass` запиши `task demo <id> --file demo.json`. Для ручной приёмки нужен `--actor user --confirmation - task accept <id> --file accept.json` с грантом `task.accept`.

## Получи и примени грант

Вызови `confirm describe --action … --target …`, передай параметры через `--file`. В канале `chat` покажи `question` дословно на карточке «Да, подтверждаю» / «Нет». По согласию запиши `asked`, `answer` и нативный `message_ref` в JSON. Вызови `confirm chat --action … --target … --hash … --file chat.json` или `--file -`. Передай код в stdin с `--actor user --confirmation -` и выполни ровно это действие. В `terminal` человек выполняет `confirm issue`. Смена канала: `policy channel terminal` без гранта, `policy channel chat` с терминальным. [Процедура и привязки](../../docs/CONFIRMATIONS.md).

После перерыва сверяй `status`, `task list` и `task show`. Сообщай ID, состояние, версию, доказательства и следующего исполнителя.
