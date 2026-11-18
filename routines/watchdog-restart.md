# Восстановление watchdog

Включай routine только по выбору пользователя. Владелец — Руководитель. Триггер — запуск компьютера или, если недоступен, согласованная ежеминутная проверка.

Скопируй первые пять элементов argv из стартового правила. Добавь `--actor controller service start`:

```text
<installed-python> -B <release>/teamctl.py --workspace <runtime> --actor controller service start
```

Проверь `status.watchdog_alive` после запуска. При ошибке сообщи блокер. [Эксплуатация](../docs/OPERATIONS.md#watchdog).
