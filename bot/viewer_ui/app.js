(() => {
  "use strict";

  const telegram = window.Telegram && window.Telegram.WebApp;
  const message = document.getElementById("message");
  const subscriptions = document.getElementById("subscriptions");
  const digest = document.getElementById("digest");
  const initData = telegram && telegram.initData;

  if (!initData) {
    message.textContent = "Откройте этот экран из личного чата с ботом в Telegram.";
    return;
  }
  telegram.ready();

  async function request(path, values = {}) {
    const response = await fetch(`/viewer/api/${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ init_data: initData, ...values }),
      credentials: "same-origin",
    });
    if (!response.ok) {
      if (response.status === 403) throw new Error("Доступ к этой настройке недоступен.");
      if (response.status === 409) throw new Error("Настройки изменились. Обновите экран.");
      throw new Error("Не удалось сохранить настройки. Попробуйте ещё раз.");
    }
    return response.json();
  }

  function field(label, value) {
    const wrapper = document.createElement("label");
    wrapper.textContent = label;
    const input = document.createElement("input");
    input.type = "text";
    input.value = value.join(", ");
    input.autocomplete = "off";
    wrapper.append(input);
    return { wrapper, input };
  }

  function terms(input) {
    return input.value.split(",").map(value => value.trim()).filter(Boolean);
  }

  function card(item, plusActive) {
    const section = document.createElement("article");
    section.className = "card";
    const heading = document.createElement("h2");
    heading.textContent = item.login;
    const status = document.createElement("p");
    status.textContent = item.is_live ? "Сейчас в эфире" : "Сейчас не в эфире";
    const row = document.createElement("div");
    row.className = "row";
    const label = document.createElement("label");
    label.textContent = "Уведомлять о начале";
    const toggle = document.createElement("input");
    toggle.type = "checkbox";
    toggle.checked = item.notify_enabled;
    toggle.setAttribute("aria-label", `Оповещения ${item.login}`);
    toggle.addEventListener("change", async () => {
      toggle.disabled = true;
      try {
        await request("notify", { login: item.login, enabled: toggle.checked });
        message.textContent = "Настройка сохранена.";
      } catch (error) {
        toggle.checked = !toggle.checked;
        message.textContent = error.message;
      } finally {
        toggle.disabled = false;
      }
    });
    row.append(label, toggle);
    section.append(heading, status, row);

    if (plusActive) {
      const filter = document.createElement("form");
      filter.className = "filter";
      const title = document.createElement("h2");
      title.textContent = "Умный фильтр";
      const current = item.filter || { version: 0, games: [], title_keywords: [], exclude_keywords: [] };
      const games = field("Игры (через запятую)", current.games);
      const keywords = field("Слова в названии эфира", current.title_keywords);
      const exclude = field("Исключить слова в названии", current.exclude_keywords);
      const save = document.createElement("button");
      save.type = "submit";
      save.textContent = "Сохранить фильтр";
      filter.append(title, games.wrapper, keywords.wrapper, exclude.wrapper, save);
      filter.addEventListener("submit", async event => {
        event.preventDefault();
        save.disabled = true;
        try {
          const result = await request("filter", {
            login: item.login,
            expected_version: current.version,
            games: terms(games.input),
            title_keywords: terms(keywords.input),
            exclude_keywords: terms(exclude.input),
          });
          current.version = result.version;
          message.textContent = "Фильтр сохранён.";
        } catch (error) {
          message.textContent = error.message;
        } finally {
          save.disabled = false;
        }
      });
      section.append(filter);
    }
    return section;
  }

  async function load() {
    try {
      const state = await request("state");
      subscriptions.replaceChildren();
      if (!state.subscriptions.length) {
        const empty = document.createElement("p");
        empty.className = "empty";
        empty.textContent = "Пока нет подписок. Добавьте стримера в личном чате с ботом.";
        subscriptions.append(empty);
      }
      for (const item of state.subscriptions) subscriptions.append(card(item, state.plus_active));
      digest.replaceChildren();
      digest.hidden = !(state.plus_active && state.digest_available);
      if (!digest.hidden) {
        const panel = document.createElement("div");
        panel.className = "card row";
        const label = document.createElement("label");
        label.textContent = "Сводка после тихих часов";
        const toggle = document.createElement("input");
        toggle.type = "checkbox";
        toggle.checked = state.digest_enabled;
        toggle.setAttribute("aria-label", "Сводка после тихих часов");
        toggle.addEventListener("change", async () => {
          toggle.disabled = true;
          try {
            await request("digest", { enabled: toggle.checked });
            message.textContent = "Настройка сохранена.";
          } catch (error) {
            toggle.checked = !toggle.checked;
            message.textContent = error.message;
          } finally {
            toggle.disabled = false;
          }
        });
        panel.append(label, toggle);
        digest.append(panel);
      }
      message.textContent = state.plus_active
        ? "Viewer Plus активен. Настройки синхронизированы с ботом."
        : "Базовые оповещения доступны. Расширенные фильтры требуют Viewer Plus.";
    } catch (error) {
      message.textContent = error.message;
    }
  }

  load();
})();
