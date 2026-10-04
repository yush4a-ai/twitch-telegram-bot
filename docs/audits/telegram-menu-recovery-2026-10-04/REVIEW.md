# Scoped review

Reviewer: /root/menu_root_cause_review, один read-only reviewer, без файловых изменений, внешних действий и полного suite.

Подтвердил исходные lost restore и late selector воспроизведения. Во время diff review нашёл stale requestA → active selectorB, ChatShared DB-read → новый FSM generation и ложную отмену при ошибке DB/OAuth. Все закреплены отрицательными тестами; root исправил code и прогнал focused suite. Последний review сообщил: blockers не обнаружены; циклических блокировок и восстановления клавиатуры в группах/чужому actor нет. Последний review нашёл блокировку отмены в pre-recovery при занятой отправке. RED через ErrorGuard → on_menu подтвердил её; неблокирующий pre-recovery исправил порядок. Финальный reviewer подтвердил закрытие, blockers отсутствуют. 16 новых регрессий PASS / 2 subtests на последнем snapshot.

Продуктовый copy: названия кнопок взяты из прямого решения владельца. Сообщение при ошибке отмены не подтверждает отмену подключения. Цены, тарифы, Free, HTML/export, media и Mini App не изменены. Stop-slop применён к новому пользовательскому сообщению и отчёту.
