/* Единая таксономия сбоев: текст и решение о повторе задаются в одном месте.
   Раньше каждый экран решал сам, поэтому отказ в доступе (403) выглядел как
   «проверьте связь», а «Повторить» предлагалась и там, где повторять нечего. */

export const FAILURE_KINDS = Object.freeze([
  'timeout', 'network', 'rate_limited', 'access_denied', 'auth_expired',
  'not_found', 'conflict', 'invalid_request', 'unavailable', 'payment_pending',
  'server_error', 'unknown',
]);

export const PAYMENT_STATUS_TEXT = 'Проверяем статус платежа…';

export function availabilityText(reason, fallback) {
  // Каждая причина отказа — своим текстом: «подключаем платёжную систему»
  // вместо «счёт уже создан» сбивало с толку и в боте, и в приложении.
  if (reason === 'payment_in_progress') return 'У вас уже есть неоплаченный счёт. Он действует 15 минут: оплатите его в чате с ботом или подождите и попробуйте снова.';
  if (reason === 'already_active') return 'Подписка уже действует — продлевать не нужно.';
  if (reason === 'upgrade_unapproved') return 'Смена тарифа пока недоступна. Напишите в поддержку: /paysupport';
  if (typeof fallback === 'string' && fallback) return fallback;
  return 'Оплата сейчас недоступна.';
}

function failure(kind, message, {retry = false, pending = false} = {}) {
  return {kind, message, retry, pending};
}

/**
 * Разбирает сбой запроса: возвращает понятный текст, признак «повторять
 * бессмысленно» и признак «состояние операции нужно перепроверить».
 *
 * cause — ошибка из api.js (ApiError для ответа сервера, ApiError(0,'timeout')
 * для таймаута, ApiError(0,'network') для обрыва связи), context — экран:
 * 'prepare' (подготовка оплаты), 'order' (статус операции), 'action' (прочее).
 */
export function describeFailure(cause, {context = 'action', fallback = ''} = {}) {
  const status = Number.isFinite(cause?.status) ? cause.status : 0;
  const code = typeof cause?.code === 'string' ? cause.code : '';
  const data = cause && typeof cause.data === 'object' && cause.data ? cause.data : null;
  const payment = context === 'prepare';

  if (code === 'timeout') {
    if (payment) return failure('timeout', PAYMENT_STATUS_TEXT, {pending: true});
    return failure('timeout', context === 'order'
      ? 'Ответ приходит дольше обычного. Проверьте операцию ещё раз.'
      : 'Ответ приходит дольше обычного. Попробуйте ещё раз.', {retry: true});
  }
  if (code === 'network' || code === 'aborted') {
    return failure('network', 'Нет связи. Попробуйте ещё раз.', {retry: true});
  }
  if (status === 401 || (status === 403 && code === 'unauthorized')) {
    return failure('auth_expired', 'Время входа истекло. Обновите приложение или откройте его заново из чата с ботом.');
  }
  if (status === 429) {
    return failure('rate_limited', 'Слишком много запросов. Подождите минуту и попробуйте снова.');
  }
  if (status === 403) {
    return failure('access_denied', context === 'order'
      ? 'Операция недоступна. Если вы уверены, что она ваша, напишите в поддержку: /paysupport'
      : 'Нет доступа к этому действию. Напишите в поддержку: /paysupport');
  }
  if (status === 404) {
    return failure('not_found', 'Операция не найдена. Оформите подписку заново.');
  }
  if (status === 409) {
    return failure('conflict', 'Операция уже изменилась. Обновите статус.');
  }
  if (status === 400) {
    return failure('invalid_request', 'Запрос отклонён. Обновите приложение и попробуйте снова.');
  }
  if (status === 503 && data && data.payment_request_created === false) {
    // Сервер точно ничего не создал: повтор разрешён, но предлагать его кнопкой
    // бессмысленно — причина постоянная (политика оплаты или уже открытый счёт).
    return failure('unavailable', availabilityText(data.reason_code, data.message || fallback));
  }
  if (status === 503 && payment) {
    // Ответ без подтверждения «заказ не создан»: безопасно считать, что счёт мог
    // появиться, и сначала проверить состояние операции.
    return failure('payment_pending', PAYMENT_STATUS_TEXT, {pending: true});
  }
  if (status >= 500) {
    return failure('server_error', 'Сервис временно недоступен. Попробуйте ещё раз позже.', {retry: true});
  }
  return failure('unknown', 'Не удалось выполнить действие. Попробуйте ещё раз.', {retry: true});
}
