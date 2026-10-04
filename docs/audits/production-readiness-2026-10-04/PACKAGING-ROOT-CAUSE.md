# Staging packaging — scoped inventory, 04.10.2026

Исходный checkpoint: d882160e4c47cdd5d578024f42b24349972beb7d. Только metadata `git ls-tree -rlz HEAD`, без чтения output/ или source DB.

| Категория tracked Git inputs | Файлы | Байты |
| --- | ---: | ---: |
| Полный source tree | 9104 | 364424250 |
| docs/ | 8473 | 349483574 |
| PNG во всём tree | 6790 | 326470557 |
| bot/ | 151 | 9508199 |

Railway rejected compressed upload 323153880 bytes. `_committed_bundle` экспортировал весь commit, включая audit screenshots и design history. Это source upload packaging defect. Full Git history/source/evidence сохраняются без удаления и перекодирования.

## Dependency inventory до изменения

| Класс | Inputs | Доказательство |
| --- | --- | --- |
| REQUIRED_RUNTIME | main.py, весь bot/** | main imports bot modules; сохранены все modules, migrations, handlers и package initializers без выборочного исключения |
| REQUIRED_RUNTIME | scripts/staging_target.json | bot/config.py:359 и bot/production_admission.py:75 читают exact target file |
| REQUIRED_RUNTIME | bot/admin_ui/**, streamer_ui/**, viewer_ui/** | соответствующие *_web.py читают HTML/CSS/JS из Path(__file__).with_name |
| REQUIRED_RUNTIME | bot/mini_app_ui/** | mini_app_web.py:23,79,93; index.html и все 17 зарегистрированных assets |
| REQUIRED_RUNTIME | bot/oauth_result_ui/** | oauth_result.py:7 и oauth.py:500, UI CSS/JS |
| REQUIRED_RUNTIME | bot/legal_ui/** | legal_web.py:15,44,51; template + CSS |
| REQUIRED_RUNTIME | bot/assets/** | telegram_home.py:19–21; welcome, home, channel-guide PNG, unchanged bytes |
| REQUIRED_RUNTIME | bot/growth_ui/** | growth_site.py:12,248–273,285–295; CSS/logo, два MP4, poster, два fonts + font license |
| REQUIRED_LEGAL | docs/legal/** | legal_documents.py:17,169,208; manifest + пять canonical Markdown inputs; existing owner_accepted=false сохранён |
| REQUIRED_BUILD | requirements.txt, railpack.json, railway.json, Procfile, .python-version | dependency install, pinned Python/FFmpeg build, startup, staging-only health/drain overrides |
| NOT_RUNTIME | docs/audits/**, docs/design/**, docs/superpowers/**, other docs/evidence/screenshots | runtime reads only docs/legal, никаких dynamic imports/resources из этих путей не найдено |
| NOT_RUNTIME | .agents/**, tests/**, development scripts/**, mini-app historical exports/marketing research | build/startup/runtime их не импортируют и не читают; scripts/staging_target.json — явное исключение |

Scoped searches: Path/open/read_text/read_bytes, importlib/resources, static/FileResponse и imports в main.py + bot/**/*.py. Остальные file reads — environment-defined DB/storage/admission, /proc и transient preview captures/markers/renders; это внешние runtime inputs, не packaged source files. Admission file явно запрещён внутри repository; .env не включается, Railway environment остаётся источником секретов. Preview binary outputs обеспечивает неизменённый railpack, capture assets создаются runtime. Неоднозначные bot files сохранены целиком.

## Implementation contract

Один canonical `scripts/runtime_package_manifest.py`: allowlist roots bot/ и docs/legal/, семь явных root/config/target files, reviewed REQUIRED_FILES minimum для каждого существующего input. Новый audit directory никогда не попадает в upload. Git commit разрешается в exact SHA, ls-tree выбирает только allowlist, cat-file materializes verified blobs; working tree bytes не используются. Manifest path/size/SHA256/Git blob/mode отсортирован; fingerprint — SHA256 canonical JSON без самого fingerprint. Manifest сохраняется вне upload directory, чтобы каждый uploaded file оставался committed Git blob. После записи сверяются bytes и отсутствие unexpected files.

Size gate 50 MiB применяется ДО materialization/upload, required bundle существенно меньше. Missing inputs, symlinks/gitlinks, DB/sidecars/secret-file types в allowed roots — STOP. Existing branch/clean/pinned target/production/full-suite/health/drain/deployment identity guards сохранены. Full source archive для audit/D не изменён. Runtime application/build/legal bytes unchanged.

RED: test_real_deploy_bundle_excludes_tracked_audit_and_design failed ожидаемо: docs/audits присутствует в прежнем deploy bundle (1 failed, 11 deselected, 11.76s). После изменения focused packaging/deploy: 46 PASS, 12 subtests, 25.14s. Полный release gate ещё не заявляется; deploy до него запрещён. Native отложено владельцем, Computer Use не используется.
