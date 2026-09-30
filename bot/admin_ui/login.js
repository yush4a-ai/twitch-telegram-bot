"use strict";

document.addEventListener("DOMContentLoaded", async () => {
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
