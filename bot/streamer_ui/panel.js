"use strict";
const message = document.getElementById("message");
const content = document.getElementById("content");

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
  } catch (_) {
    message.textContent = "Данные временно недоступны. Попробуй обновить.";
  }
}

document.getElementById("refresh").addEventListener("click", refreshProfile);
refreshProfile();
