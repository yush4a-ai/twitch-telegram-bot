"use strict";
const message = document.getElementById("message");
const content = document.getElementById("content");
const communityMessage = document.getElementById("community-message");
const communityList = document.getElementById("community-list");

async function refreshCommunities() {
  communityMessage.textContent = "Проверяем доступные сообщества…";
  try {
    const response = await fetch("/streamer/api/communities", {credentials: "same-origin", cache: "no-store"});
    if (!response.ok) throw new Error("communities_unavailable");
    const payload = await response.json();
    communityList.replaceChildren();
    for (const community of payload.communities) {
      const item = document.createElement("li");
      item.textContent = community.title;
      communityList.append(item);
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
refreshProfile();
