# Staging deploy — TwitchSignalBot

## Граница

Этот runbook относится только к Railway project `14282646-e318-4b80-b35d-4369270de255`, environment `staging` (`7a873177-8ada-4b78-8732-a0bfdc1d519b`), service `worker` (`45e46f2a-dba3-4b18-bc5f-b6fafa260055`) и боту `@SignalStreamsBot`. Production получает код из GitHub `main`; push/merge `main` здесь запрещён. ID проверяются скриптом `scripts/staging_deploy.py` и зафиксированы в `scripts/staging_target.json`.

## Перед загрузкой

1. На ветке `autonomous/twitchsignal-roadmap` прочитать `git diff`, `git status --short --branch`, `git log -1 --oneline`. Зафиксировать весь пакет коммитом; рабочее дерево должно быть чистым.
2. Выполнить `.venv\Scripts\python.exe -m scripts.staging_deploy --check`. Скрипт проверит project, обе среды, отдельные Volume, staging source без GitHub, production source `main`, одну staging replica, активный deployment и путь staging-настроек в metadata.
3. Выполнить `.venv\Scripts\python.exe -m scripts.staging_deploy --deploy`. Скрипт повторно прогонит полный `pytest -q -p no:cacheprovider`, ещё раз проверит Git и Railway, соберёт **только файлы проверенного commit** через `git archive` во временный Windows TEMP и вызовет `railway up` с явными project/environment/service ID и `--path-as-root`. Secret values, `.env`, локальная DB и логи в архив не попадают.
4. Режим `--bootstrap-deploy` был нужен только для первоначального staging deployment без healthcheck; после успешного bootstrap он обязан отказать.

После загрузки guard сам ждёт terminal `SUCCESS`, сверяет ID активного staging deployment и повторяет target check. Он возвращает 0 только после этих проверок.

## После загрузки

1. Нулевой код выхода сырого Railway CLI не является терминальным успехом: в R1 CLI завершился, когда новый deployment ещё был `DEPLOYING`. Guard ждёт активного `SUCCESS`; независимо проверить результат ниже.
2. Прочитать `railway deployment list --project 14282646-e318-4b80-b35d-4369270de255 --environment 7a873177-8ada-4b78-8732-a0bfdc1d519b --service 45e46f2a-dba3-4b18-bc5f-b6fafa260055 --limit 1 --json`. Дождаться `SUCCESS`; проверить `cliMessage=staging <SHA>` и четыре `propertyFileMapping` пути `$.environments.staging.deploy.*`. При `FAILED`/`CRASHED` остановиться и изучить staging deployment logs.
3. Проверить `GET https://worker-staging-2f74.up.railway.app/healthz` на HTTP 200 и `{"status":"ok"}`. В R1 в период переключения Volume наблюдался временный 502; `/healthz` не отражает Telegram и optional preview.
4. Через `railway ssh` с теми же тремя ID вызвать Telegram `getMe` из контейнера и вывести только `username`; ожидается `SignalStreamsBot`. Не печатать token и другие variables.
5. `/health` в Telegram включает отдельный preview snapshot, но требует `OWNER_CHAT_ID`. В R1 он отсутствует в staging; до назначения подтверждённого тестового владельца preview E2E отмечается как `unknown`.

## Настройки и ограничение Railway

`railway.json` задаёт `/healthz`, timeout 300 с, draining 30 с и overlap 0 только для `staging`. Railway отметил их как file overrides в `propertyFileMapping`; базовые поля service остались `null`. Config as Code объявлен устаревающим с 2026-12-01, поэтому до этой даты требуется миграция на Infrastructure as Code с отдельным review staging/production границы. Сервис использует Volume, так что переключение версий может дать короткий простой.
