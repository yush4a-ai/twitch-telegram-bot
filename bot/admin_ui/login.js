"use strict";

document.addEventListener("DOMContentLoaded", async () => {
  // Подсказка про домен нужна только если виджет Telegram вообще не отрисовался.
  const hint = document.getElementById("widget-hint");
  if (hint) {
    window.setTimeout(() => {
      const widget = document.querySelector('iframe[src*="oauth.telegram.org"]');
      if (!widget) hint.hidden = false;
    }, 4000);
  }

  const initData = window.Telegram?.WebApp?.initData;
  if (!initData) return;
  try {
    const response = await fetch("/admin/telegram-webapp", {
      method: "POST",
      headers: {"Content-Type": "application/x-www-form-urlencoded"},
      body: new URLSearchParams({init_data: initData}),
      credentials: "same-origin",
      cache: "no-store",
    });
    if (response.ok) window.location.replace("/admin");
  } catch (_) {
    // Telegram Login remains available if Mini App authorization is unavailable.
  }
});
