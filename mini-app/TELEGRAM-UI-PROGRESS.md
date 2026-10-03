# SDD ledger — plan: docs/superpowers/plans/2026-10-03-telegram-ui.md

Исходный HEAD608acffa29ff5195d9716ca9651199bde59fee99, branch autonomous/twitchsignal-roadmap. Owner request и дополнение §§29–36 прочитаны. Старый Mini App release сохранён; один исполнитель.

Pre-flight: Task1 home/cancel → Tasks2/3/4/5 shared navigation; Task3 own intents/cancel → Task1 Menu; Task4 shared subscription/first-release OFF → Mini App existing JSON contract. Конфликтов с owner request нет; API shape/old callback filters сохраняются.

Ruling: persistent ReplyKeyboard и inline home требуют двух Telegram сообщений — отдельная короткая подсказка при инициализации, затем один home; API допускает один reply_markup. Стоимость: дополнительная короткая строка, native расположение определяется клиентом.
Ruling: Bash bookkeeping helpers заменены видимым этим ledger и test logs/audit на Windows; owner запрещает отсутствующие community helpers/скрытые исполнители. Стоимость: ручная запись evidence, source/test/commit остаются проверяемыми.

Task 1: complete — RED5 new navigation failures, implementation → PASS21tests/34subtests (NAV-pass.log); real Dispatcher Menu precedes FSM for5pending states; exacthome4/adminMore/sharedbuilder/replyflags/deeplink attribution. Scoped diff reviewed. Local edit encoding issue traced cp1251 default → explicitUTF8 repaired; assertions preserved, no runtime workaround. New buttons implemented sequentially before any deployment. BASE608acff.
Task 2: complete — RED6 → PASS11new/navigation +28legacy/11subtests (ADD-*.log). Confirmation before mutation, own/stale/cancel, late lookup, atomic50/200, duplicate-before-Twitch, strict URL hosts. Old direct track/addfound/deep links retained; changed old home expectations to owner exact text. BASEa53eebd. Scoped review: price/rights not client controlled; no sender/DB migration.
Task 3: complete — RED6+3compat → PASS74tests/14subtests (STREAMER-pass.log). Verified identity only, existing OAuth/community intents, Menu cancellation including late creation/legacy OAuth/import, channel-only and persistent Menu restored. Old tscommunity binding, duplicate shared idempotent, legacy group manage rows stay; no new group invitation. Mini App posts entry /app + honest two-step instruction, no pretend query route. BASEcdf7ef5. Scoped services/permission/cancel review.
Task 4: in_progress — следующий RED shared Plus/catalog/status/OFF.
Task 5: pending.
Task 6: pending.
