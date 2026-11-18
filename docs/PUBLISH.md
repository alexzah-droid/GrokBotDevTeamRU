# Выпуск в GitHub


Целевой репозиторий — [GrokBotDevTeam](https://github.com/alexzah-droid/GrokBotDevTeamRU).

## Проверка комплекта

Используйте чистую копию репозитория и Python venv. Если содержимое поставки изменилось, сгенерируйте манифест через `<venv>/bin/python -B scripts/release.py manifest`. Незнакомые изменения в комплекте сначала разберите по diff.

```text
<venv>/bin/python -B scripts/doctor.py
<venv>/bin/python -B scripts/release.py check
<venv>/bin/python -B scripts/validate_skills.py
<venv>/bin/python -B -W error::ResourceWarning -m unittest discover -s tests -v
<venv>/bin/python -B scripts/demo.py
<venv>/bin/python -B scripts/release.py build
git diff --exit-code -- MANIFEST.json release.json VERSION
```

Сверьте содержимое ZIP и TAR.GZ с манифестом. Контрольные суммы двух сборок должны совпадать. В коммит входят исходники и манифест. Архивы с контрольными суммами прикладываются к GitHub Release. Рабочие данные, ключи и журналы держите вне поставки.

## Публикация

1. Проверьте файлы Git-индекса и отправьте изменения на ревью. Дождитесь прохождения GitHub Actions для Python 3.10–3.13.
2. На проверенном коммите создайте тег `v1.5.1`. Опубликуйте его и создайте GitHub Release с отметкой **Pre-release**.
3. Приложите оба архива и их файлы `.sha256`. Добавьте инструкцию установки из тега. Проверьте манифест в скачанном комплекте.
4. Передайте пользователю [сообщение для установки](../install-prompt.txt). Проведите живой пилот: две новые роли, постоянная память, нативные передачи и receipts, smoke, watchdog, независимое ревью, приёмка и откат. Зафиксируйте результат отдельно.
5. Подготовьте стабильную поставку после подтверждения живых сценариев и независимого подтверждения за пределами компьютера бота.

## Python-пакет

`<venv>/bin/python -B -m pip install --no-index --no-deps .` собирает комплект через backend на стандартной библиотеке. Проверьте из другой папки, что `grok-team` читает роли, навыки и манифест установленного пакета.
