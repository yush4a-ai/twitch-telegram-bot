import { ApiError } from './api.js';
import { element, panel, action } from './components.js';

export function createStreamerFeature(api, getRouter, telegram) {
  let data = null;
  let loading = false;
  let requested = false;
  let error = '';
  let feedback = '';
  let connectIntent = '';
  let connectUrl = '';
  let communityIntent = '';
  let communityFallback = '';
  let statusTimer = null;
  let selectedPostChat = null;
  let postState = null;
  let postLoading = false;
  let postRefreshPending = false;
  let postError = '';
  let postFeedback = '';
  let postDraft = null;
  let postConflict = false;
  let postContextGeneration = 0;
  let presetName = '';
  let presetBusy = false;
  try {
    connectIntent = api.storage.getItem('ts-streamer-connect-intent') || '';
    communityIntent = api.storage.getItem('ts-streamer-community-intent') || '';
  } catch {}
  const refresh = () => getRouter().refresh();
  const remember = (key, value) => {
    try { if (value) api.storage.setItem(key, value); else api.storage.removeItem(key); } catch {}
  };
  function heading(target, title, lead) {
    target.append(element('p', 'eyebrow', 'Стример'), element('h1', '', title), element('p', 'lead', lead));
  }
  async function load() {
    if (loading) return;
    requested = true; loading = true;
    if (!data) refresh();
    try {
      data = await api.post('/app/api/streamer/profile');
      error = '';
      postState = null;
      if (data.connected) {
        connectIntent = ''; connectUrl = '';
        remember('ts-streamer-connect-intent', '');
      }
    } catch (cause) {
      error = cause instanceof ApiError && (cause.status === 401 || cause.status === 403)
        ? 'Время входа истекло. Откройте приложение из чата бота.'
        : 'Нет связи. Показываем последние загруженные данные.';
    } finally { loading = false; refresh(); }
    if (connectIntent || communityIntent) void checkIntent();
  }
  function scheduleStatus() {
    if (statusTimer) clearTimeout(statusTimer);
    if (!connectIntent && !communityIntent) return;
    statusTimer = setTimeout(() => {
      if (!document.hidden) void checkIntent();
    }, 5000);
  }
  async function checkIntent() {
    if (document.hidden) return;
    try {
      if (connectIntent) {
        const status = await api.post('/app/api/streamer/connect-intent/status', { intent_id: connectIntent });
        if (status.status === 'connected') {
          connectIntent = ''; remember('ts-streamer-connect-intent', '');
          feedback = `Twitch подключён: ${status.twitch_login}.`;
          await load(); return;
        }
        if (!['pending', 'verifying'].includes(status.status)) {
          connectIntent = ''; remember('ts-streamer-connect-intent', '');
          feedback = status.status === 'cancelled' ? 'Подключение отменено.'
            : 'Подключение не завершилось. Попробуйте ещё раз.';
        }
      }
      if (communityIntent) {
        const status = await api.post('/app/api/streamer/community-intent/status', { intent_id: communityIntent });
        if (status.status === 'connected') {
          communityIntent = ''; communityFallback = '';
          remember('ts-streamer-community-intent', '');
          feedback = 'Сообщество подключено.';
          await load(); return;
        }
        if (!['pending', 'verifying'].includes(status.status)) {
          communityIntent = ''; communityFallback = '';
          remember('ts-streamer-community-intent', '');
          feedback = status.status === 'denied'
            ? 'Не удалось подтвердить права в сообществе. Проверьте права администратора у вас и бота.'
            : 'Выбор сообщества не завершён. Можно попробовать снова.';
        }
      }
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 403) {
        connectIntent = ''; communityIntent = '';
        remember('ts-streamer-connect-intent', ''); remember('ts-streamer-community-intent', '');
        feedback = 'Срок входа истёк. Откройте приложение из чата бота.';
      } else {
        feedback = 'Не удалось проверить подключение. Повторим, когда связь восстановится.';
      }
    }
    refresh(); scheduleStatus();
  }
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && requested) void load();
  });
  async function startConnect() {
    try {
      const intent = await api.post('/app/api/streamer/connect-intent');
      connectIntent = intent.intent_id;
      connectUrl = intent.authorize_url;
      remember('ts-streamer-connect-intent', connectIntent);
      feedback = 'Откройте Twitch и подтвердите подключение. После возврата проверим результат.';
      refresh(); scheduleStatus();
      telegram.openLink(intent.authorize_url);
    } catch (cause) {
      feedback = cause instanceof ApiError && cause.code === 'connect_unavailable'
        ? 'Подключение Twitch сейчас недоступно. Попробуйте позже.'
        : 'Не удалось начать подключение. Попробуйте ещё раз.';
      refresh();
    }
  }
  async function cancelConnect() {
    if (!connectIntent) return;
    try {
      await api.post('/app/api/streamer/connect-intent/cancel', { intent_id: connectIntent });
      connectIntent = ''; connectUrl = '';
      remember('ts-streamer-connect-intent', '');
      feedback = 'Подключение отменено.';
    } catch {
      feedback = 'Не удалось отменить подключение. Проверим его статус.';
      scheduleStatus();
    }
    refresh();
  }
  async function startCommunity(chatType) {
    try {
      const intent = await api.post('/app/api/streamer/community-intent', { chat_type: chatType });
      const intentId = intent.intent_id;
      communityIntent = intent.intent_id;
      communityFallback = intent.fallback_url || '';
      remember('ts-streamer-community-intent', communityIntent);
      feedback = 'Выберите сообщество. Подключим его после проверки ваших прав и прав бота.';
      refresh(); scheduleStatus();
      const sent = await telegram.requestChat(intent.prepared_id);
      if (sent === true) {
        feedback = 'Выбор отправлен боту. Проверяем права сообщества…';
        void checkIntent();
      } else if (sent === null && communityFallback) {
        feedback = 'Выберите сообщество через кнопку в чате бота, затем вернитесь сюда.';
        telegram.openTelegramLink(communityFallback);
      } else if (sent === false) {
        try {
          await api.post('/app/api/streamer/community-intent/cancel', { intent_id: intentId });
          if (communityIntent === intentId) {
            communityIntent = ''; communityFallback = '';
            remember('ts-streamer-community-intent', '');
          }
          feedback = 'Выбор отменён. Сообщество не подключено.';
        } catch {
          feedback = 'Не удалось подтвердить отмену. Проверяем статус выбора.';
          scheduleStatus();
        }
      } else {
        feedback = 'Откройте чат бота для выбора сообщества.';
      }
      refresh();
    } catch {
      feedback = 'Не удалось начать выбор сообщества. Попробуйте ещё раз.';
      refresh();
    }
  }
  async function togglePublishing(community, enabled) {
    try {
      await api.post('/app/api/streamer/communities/toggle', {
        chat_id: community.chat_id, enabled,
      });
      feedback = enabled
        ? 'Публикации включены. Бот отправит обычный пост при следующем подтверждённом эфире.'
        : 'Публикации приостановлены.';
      await load();
    } catch (cause) {
      feedback = cause instanceof ApiError && cause.code === 'permission_denied'
        ? 'Нужны права администратора у вас и бота. После исправления попробуйте ещё раз.'
        : 'Не удалось изменить публикации. Попробуйте ещё раз.';
      refresh();
    }
  }
  const draftKey = (chatId) => `ts-streamer-template-${data.twitch_login}-${chatId}`;
  function restoreDraft(chatId, saved) {
    try {
      const raw = api.storage.getItem(draftKey(chatId));
      if (raw) {
        const parsed = JSON.parse(raw);
        if (parsed && Number.isInteger(parsed.version) && typeof parsed.headline === 'string'
            && typeof parsed.body === 'string' && Array.isArray(parsed.buttons)) {
          return { version: parsed.version, headline: parsed.headline, body: parsed.body,
            buttons: [0, 1].map((index) => ({
              label: typeof parsed.buttons[index]?.label === 'string' ? parsed.buttons[index].label : '',
              url: typeof parsed.buttons[index]?.url === 'string' ? parsed.buttons[index].url : '',
            })) };
        }
      }
    } catch {}
    return { version: saved.version, headline: saved.headline, body: saved.body,
      buttons: [0, 1].map((index) => saved.buttons[index] || { label: '', url: '' }) };
  }
  function rememberDraft(chatId) {
    try { api.storage.setItem(draftKey(chatId), JSON.stringify(postDraft)); } catch {}
  }
  async function loadPostState(chatId) {
    if (postLoading || !chatId) return;
    postLoading = true; postError = ''; refresh();
    try {
      const [example, template, presets] = await Promise.all([
        api.post('/app/api/streamer/post-example', { chat_id: chatId }),
        api.post('/app/api/streamer/template', { chat_id: chatId }),
        api.post('/app/api/streamer/presets'),
      ]);
      let stats = null;
      let compare = null;
      if (template.can_edit) {
        try { stats = await api.post('/app/api/streamer/stats'); } catch { /* State still renders without statistics. */ }
        try { compare = await api.post('/app/api/streamer/stats/compare'); } catch { /* Keep editor available. */ }
      }
      if (selectedPostChat !== chatId) return;
      postState = { chatId, example, template, presets, stats, compare };
      postDraft = restoreDraft(chatId, template);
      postContextGeneration += 1;
    } catch (cause) {
      postError = cause instanceof ApiError && (cause.status === 401 || cause.status === 403)
        ? 'Доступ изменился. Вернитесь в приложение из чата бота и обновите страницу.'
        : 'Не удалось загрузить пример. Проверьте связь и попробуйте снова.';
    } finally {
      postLoading = false;
      if (postRefreshPending) {
        postRefreshPending = false;
        postState = null;
        if (selectedPostChat) void loadPostState(selectedPostChat);
      }
      refresh();
    }
  }
  function refreshPostAfterMutation() {
    postState = null;
    if (postLoading) postRefreshPending = true;
    else if (selectedPostChat) void loadPostState(selectedPostChat);
  }
  async function saveTemplate(chatId) {
    if (!postDraft) return;
    try {
      const buttons = postDraft.buttons.filter((item) => item.label || item.url);
      const saved = await api.post('/app/api/streamer/template', {
        chat_id: chatId, version: postDraft.version,
        headline: postDraft.headline, body: postDraft.body, buttons,
      });
      postDraft.version = saved.version;
      try { api.storage.removeItem(draftKey(chatId)); } catch {}
      postConflict = false;
      postFeedback = 'Сохранено. Оформление применяется к постам при действующем Plus.';
      postState = null;
      void loadPostState(chatId);
    } catch (cause) {
      postConflict = cause instanceof ApiError && cause.status === 409;
      postFeedback = cause instanceof ApiError && cause.status === 409
        ? 'Оформление изменилось в другом окне. Ваш текст сохранён здесь; обновите версию перед повтором.'
        : cause instanceof ApiError && cause.status === 403
          ? 'Доступ к оформлению закончился. Черновик сохранён.'
          : cause instanceof ApiError && cause.status === 400
            ? 'Проверьте длину текста и адреса кнопок: нужен HTTPS-сайт.'
            : 'Не удалось сохранить оформление. Черновик сохранён.';
      refresh();
      if (cause instanceof ApiError && cause.status === 403) void load();
    }
  }
  async function refreshTemplateVersion(chatId) {
    try {
      const current = await api.post('/app/api/streamer/template', { chat_id: chatId });
      if (!current.can_edit) {
        postFeedback = 'Доступ к оформлению закончился. Черновик сохранён.';
        void load(); return;
      }
      postDraft.version = current.version;
      postContextGeneration += 1;
      rememberDraft(chatId);
      postConflict = false;
      postFeedback = 'Актуальная версия загружена. Ваш текст сохранён; проверьте его и нажмите «Сохранить оформление».';
    } catch {
      postFeedback = 'Не удалось получить актуальную версию. Ваш текст сохранён.';
    }
    refresh();
  }
  async function setAnimation(chatId, enabled) {
    try {
      await api.post('/app/api/streamer/preview', { chat_id: chatId, enabled });
      postFeedback = enabled ? 'Живое превью включено для этого сообщества.'
        : 'Живое превью выключено.';
      postState = null; void loadPostState(chatId);
    } catch (cause) {
      postFeedback = cause instanceof ApiError && cause.status === 403
        ? 'Для живого превью нужен действующий Streamer Plus и права в сообществе.'
        : 'Не удалось изменить превью. Попробуйте ещё раз.';
      refresh();
      if (cause instanceof ApiError && cause.status === 403) void load();
    }
  }
  async function savePreset() {
    if (!postDraft || presetBusy) return;
    presetBusy = true;
    try {
      await api.post('/app/api/streamer/presets/create', {
        name: presetName.trim(), headline: postDraft.headline, body: postDraft.body,
        buttons: postDraft.buttons.filter((item) => item.label || item.url),
      });
      presetName = '';
      postFeedback = 'Вариант сохранён. Текущее оформление и опубликованные посты не изменились.';
      refreshPostAfterMutation();
    } catch (cause) {
      postFeedback = cause instanceof ApiError && cause.code === 'name_taken'
        ? 'Название уже занято. Выберите другое.'
        : cause instanceof ApiError && cause.code === 'preset_limit'
          ? 'Достигнут предел сохранённых вариантов. Удалите ненужный.'
          : cause instanceof ApiError && cause.status === 403
            ? 'Доступ Streamer Plus завершился. Вариант не сохранён.'
            : cause instanceof ApiError && cause.status === 400
              ? 'Проверьте название, текст и HTTPS-адреса кнопок.'
              : 'Не удалось сохранить вариант. Попробуйте ещё раз.';
      if (cause instanceof ApiError && cause.status === 403) void load();
    } finally { presetBusy = false; refresh(); }
  }
  async function applyPreset(preset, chatId) {
    if (!postDraft || presetBusy) return;
    const contextGeneration = postContextGeneration;
    const draft = postDraft;
    const draftSnapshot = JSON.stringify(draft);
    presetBusy = true;
    try {
      await api.post('/app/api/streamer/presets/apply', {
        preset_id: preset.id, chat_id: chatId, expected_version: draft.version,
      });
      if (selectedPostChat !== chatId || postContextGeneration !== contextGeneration
          || postDraft !== draft || JSON.stringify(postDraft) !== draftSnapshot) {
        if (selectedPostChat === chatId) refreshPostAfterMutation();
        return;
      }
      try { api.storage.removeItem(draftKey(chatId)); } catch {}
      postDraft = null;
      postConflict = false;
      postFeedback = `Вариант «${preset.name}» применён к будущим постам этого сообщества.`;
      refreshPostAfterMutation();
    } catch (cause) {
      if (selectedPostChat !== chatId) return;
      if (cause instanceof ApiError && cause.status === 409
          && postDraft?.version !== draft.version) return;
      postConflict = cause instanceof ApiError && cause.status === 409;
      postFeedback = cause instanceof ApiError && cause.status === 409
        ? 'Оформление изменилось в другом окне. Обновите версию перед применением варианта.'
        : cause instanceof ApiError && cause.status === 403
          ? 'Нет доступа к варианту или сообществу. Проверьте Streamer Plus и права администратора.'
          : 'Не удалось применить вариант. Текущее оформление сохранено.';
      if (cause instanceof ApiError && cause.status === 403) void load();
    } finally { presetBusy = false; refresh(); }
  }
  async function deletePreset(preset) {
    if (presetBusy) return;
    const sdk = window.Telegram?.WebApp;
    const confirmed = sdk?.showConfirm
      ? await new Promise((resolve) => sdk.showConfirm(`Удалить вариант «${preset.name}»?`, resolve))
      : window.confirm(`Удалить вариант «${preset.name}»?`);
    if (!confirmed) return;
    presetBusy = true;
    try {
      await api.post('/app/api/streamer/presets/delete', { preset_id: preset.id });
      postFeedback = 'Вариант удалён. Текущее оформление сохранено.';
      refreshPostAfterMutation();
    } catch { postFeedback = 'Не удалось удалить вариант. Попробуйте ещё раз.'; }
    finally { presetBusy = false; refresh(); }
  }
  function renderChannel(target) {
    heading(target, 'Мой канал', 'Twitch и сообщества Telegram в одном месте.');
    if (!data.connected) {
      const box = element('section', 'panel feature-panel');
      box.append(element('h2', '', 'Подключите Twitch'));
      box.append(element('p', 'muted', 'Войдите в свой Twitch-аккаунт. Подключение сообщества и обычный пост бесплатны.'));
      box.append(action('Подключить Twitch', () => void startConnect()));
      if (connectUrl) box.append(action('Открыть страницу Twitch', () => telegram.openLink(connectUrl), true));
      if (connectIntent) {
        box.append(element('p', 'notice', 'Ожидаем подтверждение Twitch.'));
        box.append(action('Отменить подключение', () => void cancelConnect(), true));
      }
      target.append(box);
    } else {
      target.append(panel('Twitch подключён', data.twitch_login));
      const box = element('section', 'panel feature-panel');
      box.append(element('h2', '', 'Сообщества'));
      if (!data.communities.length) box.append(element('p', 'muted', 'Пока нет подключённых сообществ. Добавьте бота администратором группы или канала и выберите его здесь.'));
      for (const community of data.communities) {
        const line = element('div', 'panel feature-panel');
        line.append(element('strong', '', community.title), element('small', '',
          !community.permission_ok ? 'Нужны права администратора у вас и бота'
            : community.publishing ? 'Публикации включены' : 'Публикации выключены'));
        if (community.publishing) {
          line.append(action('Приостановить публикации', () => void togglePublishing(community, false), true));
        } else if (community.permission_ok) {
          line.append(action('Включить публикации', () => void togglePublishing(community, true)));
        }
        box.append(line);
      }
      const buttons = element('div', 'actions');
      buttons.append(action('Выбрать группу', () => void startCommunity('group')));
      buttons.append(action('Выбрать канал', () => void startCommunity('channel'), true));
      box.append(buttons);
      if (communityIntent) box.append(element('p', 'notice', 'Ждём выбор и повторную проверку прав.'));
      if (communityFallback) box.append(action('Открыть чат бота', () => telegram.openTelegramLink(communityFallback), true));
      target.append(box);
    }
    if (feedback) {
      const note = element('p', 'notice', feedback);
      note.setAttribute('role', 'status'); target.append(note);
    }
  }
  function renderPosts(target) {
    heading(target, 'Посты', 'Оформление для ваших сообществ.');
    if (!data.connected) {
      target.append(panel('Сначала подключите Twitch', 'После подключения и выбора сообщества здесь появится пример поста.'));
      return;
    }
    if (!data.communities.length) {
      target.append(panel('Выберите сообщество', 'Подключите группу или канал в разделе «Мой канал», чтобы увидеть пост.'));
      target.append(action('Мой канал', () => getRouter().setTab('channel')));
      return;
    }
    if (!data.communities.some((item) => item.chat_id === selectedPostChat)) {
      selectedPostChat = data.communities[0].chat_id;
      postState = null;
    }
    if (data.communities.length > 1) {
      const field = element('label', 'post-community-select', 'Сообщество');
      const select = element('select', 'input');
      for (const community of data.communities) {
        const option = element('option', '', community.title);
        option.value = String(community.chat_id); select.append(option);
      }
      select.value = String(selectedPostChat);
      select.addEventListener('change', () => {
        selectedPostChat = Number(select.value); postState = null; postDraft = null;
        postContextGeneration += 1;
        postFeedback = ''; postConflict = false; void loadPostState(selectedPostChat);
      });
      field.append(select); target.append(field);
    }
    if (postError) {
      const note = element('p', 'notice error', postError); note.setAttribute('role', 'alert');
      target.append(note, action('Повторить', () => void loadPostState(selectedPostChat), true));
    }
    if (!postState || postState.chatId !== selectedPostChat) {
      if (!postLoading && !postError) void loadPostState(selectedPostChat);
      if (!postError) target.append(element('div', 'status-panel', 'Загружаем пример поста…'));
      return;
    }
    const { example, template, stats, compare } = postState;
    const community = data.communities.find((item) => item.chat_id === selectedPostChat);
    const box = element('section', 'panel feature-panel');
    box.append(element('h2', '', `Пример · ${community.title}`));
    box.append(element('p', 'muted', 'Локальный пример. Сообщение в Telegram не отправляется.'));
    box.append(element('div', 'post-preview-text', example.text));
    const buttons = element('div', 'post-preview-buttons');
    for (const button of example.buttons) buttons.append(element('span', 'post-preview-button', button.label));
    box.append(buttons);
    box.append(element('small', 'muted', example.custom_active
      ? 'Применено ваше оформление.' : 'Стандартное оформление.'));
    target.append(box);
    if (postFeedback) {
      const note = element('p', 'notice', postFeedback);
      note.setAttribute('role', 'status'); target.append(note);
    }
    if (postConflict) target.append(action('Обновить версию', () => void refreshTemplateVersion(selectedPostChat), true));
    if (!template.can_edit) {
      renderPresets(target, selectedPostChat, false);
      target.append(panel('Обычный пост доступен бесплатно', template.version
        ? 'Ваше оформление сохранено и вернётся при действующем Streamer Plus.'
        : 'Дополнительный текст, кнопки и живое превью доступны с Streamer Plus.'));
      const access = element('div', 'actions');
      access.append(action('Посмотреть доступ', () => getRouter().openDetail('subscription'), true));
      target.append(access);
      return;
    }
    const media = element('section', 'panel feature-panel');
    media.append(element('h2', '', 'Живое превью'));
    media.append(element('p', 'muted', 'Во время эфира бот обновляет короткое видео в посте.'));
    const mediaLabel = element('label', 'switch-row');
    const mediaSwitch = element('input'); mediaSwitch.type = 'checkbox';
    mediaSwitch.checked = example.animation_enabled;
    mediaSwitch.disabled = !community.publishing;
    mediaSwitch.addEventListener('change', () => void setAnimation(selectedPostChat, mediaSwitch.checked));
    mediaLabel.append(mediaSwitch, element('span', '', 'Включить для этого сообщества'));
    media.append(mediaLabel);
    if (!community.publishing) media.append(element('small', 'muted', 'Сначала включите публикации в разделе «Мой канал».'));
    target.append(media);
    renderTemplateEditor(target, selectedPostChat);
    renderPresets(target, selectedPostChat, true);
    target.append(panel('Подтверждённые публикации за 30 дней', stats
      ? `Бот подтвердил: ${stats.published_posts}. Просмотры Telegram не измеряются.`
      : 'Статистика временно недоступна. Пример и оформление продолжают работать.'));
    target.append(panel('Публикации: последние 7 дней и предыдущие 7', compare
      ? `${compare.current_posts} и ${compare.previous_posts} подтверждённых постов. Сравнение не измеряет просмотры.`
      : 'Сравнение временно недоступно.'));
  }
  function renderPresets(target, chatId, canEdit) {
    const presets = postState?.presets?.presets || [];
    if (!canEdit && !presets.length) return;
    const box = element('section', 'panel feature-panel');
    box.append(element('h2', '', 'Сохранённые варианты'));
    box.append(element('p', 'muted', canEdit
      ? 'Сохранение не меняет текущий пост. Применение меняет оформление будущих публикаций только в выбранном сообществе.'
      : 'Варианты сохранены и снова станут доступны для применения с Streamer Plus.'));
    if (canEdit && postDraft) {
      const field = element('label', 'template-field', 'Название варианта');
      const input = element('input', 'input'); input.name = 'preset_name'; input.autocomplete = 'off';
      input.maxLength = 40; input.value = presetName;
      input.addEventListener('input', () => { presetName = input.value; });
      field.append(input); box.append(field);
      const save = action('Сохранить вариант', () => void savePreset());
      save.disabled = presetBusy; box.append(save);
    }
    if (!presets.length) box.append(element('p', 'muted', 'Пока нет сохранённых вариантов.'));
    for (const preset of presets) {
      const row = element('div', 'panel feature-panel');
      row.append(element('strong', '', preset.name), element('small', 'muted', preset.headline));
      const actions = element('div', 'actions');
      if (canEdit) {
        const apply = action('Применить', () => void applyPreset(preset, chatId));
        apply.disabled = presetBusy; actions.append(apply);
      }
      const remove = action('Удалить', () => void deletePreset(preset), true);
      remove.disabled = presetBusy; actions.append(remove);
      row.append(actions); box.append(row);
    }
    target.append(box);
  }
  function renderTemplateEditor(target, chatId) {
    if (!postDraft) return;
    const box = element('section', 'panel feature-panel');
    box.append(element('h2', '', 'Оформление поста'));
    box.append(element('p', 'muted', 'Заголовок до 60 знаков, текст до 140, две HTTPS-кнопки.'));
    const form = element('form', 'template-form');
    const input = (label, value, max, update, multiline = false) => {
      const field = element('label', 'template-field', label);
      const control = element(multiline ? 'textarea' : 'input', 'input');
      control.value = value; control.maxLength = max;
      if (multiline) control.rows = 3;
      control.addEventListener('input', () => {
        update(control.value); postContextGeneration += 1; rememberDraft(chatId);
      });
      field.append(control); return field;
    };
    form.append(input('Заголовок', postDraft.headline, 60, (value) => { postDraft.headline = value; }));
    form.append(input('Текст', postDraft.body, 140, (value) => { postDraft.body = value; }, true));
    for (let index = 0; index < 2; index++) {
      form.append(input(`Кнопка ${index + 1} · название`, postDraft.buttons[index]?.label || '', 24,
        (value) => { postDraft.buttons[index].label = value; }));
      form.append(input(`Кнопка ${index + 1} · HTTPS-адрес`, postDraft.buttons[index]?.url || '', 512,
        (value) => { postDraft.buttons[index].url = value; }));
    }
    const save = action('Сохранить оформление', () => {});
    save.type = 'submit'; form.append(save);
    form.addEventListener('submit', (event) => { event.preventDefault(); void saveTemplate(chatId); });
    box.append(form); target.append(box);
  }
  function renderProfile(target) {
    heading(target, 'Профиль', 'Подключение и доступ стримера.');
    target.append(panel('Twitch', data.connected ? data.twitch_login : 'Не подключён'));
    target.append(panel('Streamer Plus', data.plus_active ? 'Активен' : 'Обычный пост и подключение сообщества доступны бесплатно.'));
    const access = element('div', 'actions');
    access.append(action('Доступ и история', () => getRouter().openDetail('subscription'), true));
    target.append(access);
  }
  return {
    render(target, route) {
      if (!requested) { queueMicrotask(() => { if (!requested) void load(); }); target.append(element('div', 'status-panel', 'Загружаем данные стримера…')); return; }
      if (!data) { target.append(element('div', 'status-panel', error || 'Загружаем данные стримера…')); return; }
      if (error) target.append(element('p', 'notice error', error));
      if (route.tab === 'channel') renderChannel(target);
      else if (route.tab === 'posts') renderPosts(target);
      else renderProfile(target);
    },
    refresh: load,
  };
}
