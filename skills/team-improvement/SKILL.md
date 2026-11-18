---
name: team-improvement
description: Предложи, оцени и испытай обратимое улучшение инструкции.
---

# Самоулучшение

1. Сверь `policy show`, `version <id>` и конфигурацию оценки. Настройку `evaluation <role> --file config.json --evidence …` авторизует пользователь. [Набор и среда оценки](../../docs/EVALUATION.md).
2. Собери повторяемые сбои и гипотезу. Только сама роль или Руководитель вызывает `improve propose <role> --body candidate.md --hypothesis … --evidence …`. Меняй только `behavior`.
3. Проверь назначенного `evaluator` через `improve show <id>`. Под этой ролью запусти `improve evaluate <id>`. Не повторяй прерванную оценку автоматически.
4. В `approval` Руководитель получает грант `improve.approve` для `improve approve` по [процедуре задач](../team-workflow/SKILL.md#получи-и-примени-грант). В терминале укажи `--reviewed-diff`. В `autonomous` запуск испытания автоматический. При отсрочке проверь `improve show` перед `improve progress <id>`.
5. Независимый оценщик записывает результат через `improve observe <id> --file outcome.json`. При `awaiting_acceptance` Руководитель получает новый грант `improve.accept` для `improve accept`. Если при применении и на момент итогового наблюдения действует `autonomous`, успешный итог принимается автоматически. Провал или истечение срока откатывает версию, в том числе в ожидании ответа.
6. Сверь `history <role>`, `version <id>`, `diff <id>` и `events --object <id>`. Для ручных `improve cancel` и `restore` применяй процедуру гранта. [Восстановление](../../docs/OPERATIONS.md#откат).

По запросу пользователя меняй режим через `policy set <mode> [--role <role>] --evidence …` с грантом. `inherit` снимает индивидуальное переопределение.
