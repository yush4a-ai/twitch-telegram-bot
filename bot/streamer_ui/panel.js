"use strict";
const message = document.getElementById("message");
const content = document.getElementById("content");
const communityMessage = document.getElementById("community-message");
const communityList = document.getElementById("community-list");
const templateCard = document.getElementById("template-card");
const templateMessage = document.getElementById("template-message");
let selectedCommunity = null;
let templateVersion = 0;

async function refreshStats() {
  const card = document.getElementById("stats-card");
  const content = document.getElementById("stats-content");
  card.hidden = false;
  content.textContent = "Загружаем статистику…";
  try {
    const response = await fetch("/streamer/api/stats", {
      credentials: "same-origin", cache: "no-store",
    });
    if (!response.ok) throw new Error("stats_unavailable");
    const stats = await response.json();
    content.textContent = `Подключено сообществ: ${stats.connected_communities}. ` +
      `Опубликовано live-постов за 30 дней: ${stats.published_posts}.`;
  } catch (_) {
    content.textContent = "Статистика временно недоступна.";
  }
}

async function loadTemplate(community) {
  selectedCommunity = community;
  templateCard.hidden = false;
  document.getElementById("template-community").textContent = `Сообщество: ${community.title}`;
  templateMessage.textContent = "Загружаем оформление…";
  try {
    const response = await fetch(`/streamer/api/templates/${community.chat_id}`, {
      credentials: "same-origin", cache: "no-store",
    });
    if (!response.ok) throw new Error("template_unavailable");
    const value = await response.json();
    templateVersion = value.version;
    document.getElementById("template-headline").value = value.headline;
    document.getElementById("template-body").value = value.body;
    for (let index = 1; index <= 2; index++) {
      const button = value.buttons[index - 1];
      document.getElementById(`template-button-${index}-label`).value = button?.label || "";
      document.getElementById(`template-button-${index}-url`).value = button?.url || "";
    }
    templateMessage.textContent = value.version ? "Сохранённое оформление загружено." : "Оформление пока не задано.";
  } catch (_) {
    templateCard.hidden = true;
    templateMessage.textContent = "Не удалось загрузить оформление. Проверь права и попробуй снова.";
  }
}

async function refreshCommunities() {
  communityMessage.textContent = "Проверяем доступные сообщества…";
  try {
    const response = await fetch("/streamer/api/communities", {credentials: "same-origin", cache: "no-store"});
    if (!response.ok) throw new Error("communities_unavailable");
    const payload = await response.json();
    communityList.replaceChildren();
    for (const community of payload.communities) {
      const item = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = `${community.title} · настроить пост`;
      button.addEventListener("click", () => loadTemplate(community));
      item.append(button);
      communityList.append(item);
    }
    if (!payload.communities.some((community) => community.chat_id === selectedCommunity?.chat_id)) {
      selectedCommunity = null;
      templateCard.hidden = true;
    }
    communityMessage.textContent = payload.communities.length
      ? "Сообщества с подтверждёнными правами:"
      : "Нет доступных сообществ. Если они были подключены, проверь права администратора у себя и бота.";
  } catch (_) {
    communityMessage.textContent = "Не удалось проверить права сообществ. Попробуй обновить.";
  }
}

async function refreshProfile() {
  message.textContent = "Обновляем данные…";
  try {
    const response = await fetch("/streamer/api/profile", {credentials: "same-origin", cache: "no-store"});
    if (response.status === 401 || response.status === 403) {
      window.location.replace("/streamer");
      return;
    }
    if (!response.ok) throw new Error("profile_unavailable");
    const profile = await response.json();
    document.getElementById("channel-title").textContent = profile.twitch_login;
    document.getElementById("plus-title").textContent = profile.plus_active ? "Streamer Plus активен" : "Без Plus";
    document.getElementById("plus-expiry").textContent = profile.plus_expires_at
      ? `Действует до ${new Date(profile.plus_expires_at * 1000).toLocaleString("ru-RU")}`
      : "Тестовый доступ пока не выдан или срок истёк.";
    content.hidden = false;
    message.textContent = "Данные обновлены";
    await refreshCommunities();
    document.getElementById("stats-card").hidden = !profile.plus_active;
    if (profile.plus_active) await refreshStats();
  } catch (_) {
    message.textContent = "Данные временно недоступны. Попробуй обновить.";
  }
}

document.getElementById("refresh").addEventListener("click", refreshProfile);
document.getElementById("community-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const raw = document.getElementById("chat-id").value.trim().replace(/^−/, "-");
  const chatId = Number(raw);
  if (!/^-[0-9]+$/.test(raw) || !Number.isSafeInteger(chatId)) {
    communityMessage.textContent = "Укажи числовой ID группы или канала с минусом в начале.";
    return;
  }
  communityMessage.textContent = "Проверяем права…";
  try {
    const response = await fetch("/streamer/api/communities", {
      method: "POST", credentials: "same-origin", cache: "no-store",
      headers: {"Content-Type": "application/json"}, body: JSON.stringify({chat_id: chatId}),
    });
    if (!response.ok) {
      communityMessage.textContent = response.status === 403
        ? "Нужен активный Plus и права администратора у тебя и бота."
        : "Не удалось подключить. Проверь ID и попробуй снова.";
      return;
    }
    document.getElementById("chat-id").value = "";
    await refreshCommunities();
  } catch (_) {
    communityMessage.textContent = "Сеть недоступна. Попробуй позже.";
  }
});
document.getElementById("template-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!selectedCommunity) return;
  const buttons = [];
  for (let index = 1; index <= 2; index++) {
    const label = document.getElementById(`template-button-${index}-label`).value.trim();
    const url = document.getElementById(`template-button-${index}-url`).value.trim();
    if (label || url) {
      if (!label || !url) {
        templateMessage.textContent = "Для каждой кнопки заполни название и ссылку.";
        return;
      }
      buttons.push({label, url});
    }
  }
  templateMessage.textContent = "Сохраняем…";
  try {
    const response = await fetch(`/streamer/api/templates/${selectedCommunity.chat_id}`, {
      method: "PUT", credentials: "same-origin", cache: "no-store",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        version: templateVersion,
        headline: document.getElementById("template-headline").value,
        body: document.getElementById("template-body").value,
        buttons,
      }),
    });
    if (response.status === 409) {
      templateMessage.textContent = "Оформление изменилось в другой вкладке. Открой его заново перед сохранением.";
      return;
    }
    if (!response.ok) {
      templateMessage.textContent = response.status === 403
        ? "Нужен активный Plus и права администратора у тебя и бота."
        : "Не удалось сохранить. Проверь текст и HTTPS-ссылки.";
      return;
    }
    templateVersion = (await response.json()).version;
    templateMessage.textContent = "Оформление сохранено.";
  } catch (_) {
    templateMessage.textContent = "Сеть недоступна. Попробуй позже.";
  }
});
refreshProfile();
