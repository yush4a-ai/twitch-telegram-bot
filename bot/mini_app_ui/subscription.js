import { element, panel, action } from './components.js';

const products = {
  viewer_plus: {
    title: 'Viewer Plus',
    detail: 'До 200 стримеров, фильтры, категории, напоминания и пять выбранных видеопревью.',
  },
  streamer_plus: {
    title: 'Streamer Plus',
    detail: 'Оформление публикаций, кнопки, живое превью и измеряемые публикации в подключённых сообществах.',
  },
};
const statuses = {
  pending: 'Ожидает подтверждения', paid: 'Подтверждён',
  cancelled: 'Отменён', expired: 'Истёк', refunded: 'Возврат',
};
const sourceName = (source) => source === 'test' || source === 'mock' ? 'Тестовый доступ' : 'Активный доступ';
const dateText = (value) => value ? new Date(value * 1000).toLocaleString('ru-RU', { dateStyle: 'medium', timeStyle: 'short' }) : '';

export function createSubscriptionFeature(api, getRouter, onAccessChanged) {
  let state = null;
  let loading = false;
  let requested = false;
  let busy = false;
  let feedback = '';
  let error = '';
  const refresh = () => getRouter().refresh();
  async function load() {
    if (loading) return;
    requested = true;
    loading = true;
    try {
      state = await api.post('/app/api/subscription/state');
      error = '';
    } catch {
      error = state ? 'Нет связи. Показан последний загруженный статус.' : 'Не удалось загрузить доступ.';
    } finally { loading = false; refresh(); }
  }
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && getRouter().state.detail === 'subscription') void load();
  });
  async function change(path, values) {
    if (busy) return;
    busy = true;
    feedback = '';
    refresh();
    try {
      const result = await api.post(`/app/api/subscription/${path}`, values);
      if (path === 'test-checkout') feedback = 'Тестовый заказ создан. Доступ ещё не активен.';
      else if (result.status === 'paid') feedback = 'Тестовый доступ активен.';
      else if (result.status === 'refunded') feedback = 'Тестовый доступ отозван.';
      else if (result.status === 'cancelled') feedback = 'Заказ отменён.';
      else feedback = 'Статус изменился. Проверьте его ниже.';
      await load();
      if (path === 'test-confirm' || path === 'test-refund') onAccessChanged();
    } catch {
      feedback = 'Действие не выполнено. Проверьте статус и попробуйте снова.';
      await load();
    } finally { busy = false; refresh(); }
  }
  function renderProduct(target, key) {
    const item = products[key];
    const productState = state[key === 'viewer_plus' ? 'viewer' : 'streamer'];
    const box = element('section', 'panel subscription-product');
    box.append(element('h2', '', item.title));
    box.append(element('p', 'muted', item.detail));
    let status = productState.active
      ? `${sourceName(productState.source)} · до ${dateText(productState.expires_at)}`
      : 'Сейчас без Plus';
    if (key === 'streamer_plus' && !productState.linked) status += ' · сначала подключите Twitch';
    box.append(element('p', 'subscription-status', status));
    if (state.test_checkout_available && (key === 'viewer_plus' || productState.linked)) {
      const controls = element('div', 'actions');
      const start = action('Создать тестовый заказ', () => void change('test-checkout', { product: key }), true);
      start.disabled = busy;
      controls.append(start);
      box.append(controls);
    }
    target.append(box);
  }
  function renderOrder(target, order) {
    const row = element('div', 'subscription-order');
    const copy = element('div', 'row-copy');
    copy.append(element('strong', '', products[order.product]?.title || 'Продукт'));
    copy.append(element('small', '', `${statuses[order.status] || 'Статус неизвестен'} · ${dateText(order.created_at)}`));
    row.append(copy);
    if (state.test_checkout_available && order.status === 'pending') {
      const controls = element('div', 'actions');
      const confirm = action('Подтвердить тест', () => void change('test-confirm', { order_id: order.order_id }));
      const cancel = action('Отменить', () => void change('test-cancel', { order_id: order.order_id }), true);
      confirm.disabled = busy; cancel.disabled = busy;
      controls.append(confirm, cancel); row.append(controls);
    } else if (state.test_checkout_available && order.status === 'paid') {
      const refund = action('Отозвать тестовый доступ', () => void change('test-refund', { order_id: order.order_id }), true);
      refund.disabled = busy;
      row.append(refund);
    }
    target.append(row);
  }
  return {
    render(target, route) {
      target.replaceChildren();
      if (!requested) void load();
      target.append(element('p', 'eyebrow', route.mode === 'viewer' ? 'Зритель' : 'Стример'));
      target.append(element('h1', '', 'Доступ'));
      target.append(element('p', 'lead', 'Возможности и история тестовых заказов.'));
      if (!state) {
        target.append(panel('Проверяем доступ', error || 'Загружаем текущий статус.'));
        if (error) target.append(action('Повторить', () => void load()));
        return;
      }
      if (error) target.append(element('p', 'notice error', error));
      if (feedback) {
        const note = element('p', 'notice', feedback);
        note.setAttribute('role', 'status');
        target.append(note);
      }
      if (state.test_checkout_available) target.append(panel('Тестовый доступ', 'Деньги не списываются. Подтверждение действует только в закреплённом тестовом окружении.'));
      else target.append(panel('Подписка', 'Покупка в приложении пока недоступна. Бесплатные возможности работают без неё.'));
      const first = route.mode === 'viewer' ? 'viewer_plus' : 'streamer_plus';
      renderProduct(target, first);
      renderProduct(target, first === 'viewer_plus' ? 'streamer_plus' : 'viewer_plus');
      target.append(element('h2', 'section-head', 'История'));
      if (!state.history.length) target.append(panel('Заказов пока нет', 'Здесь будут показаны ваши заказы и их статус.'));
      else {
        const list = element('div', 'subscription-history');
        state.history.forEach((order) => renderOrder(list, order));
        target.append(list);
      }
      const update = action('Обновить статус', () => void load(), true);
      update.disabled = loading;
      const actions = element('div', 'actions');
      actions.append(update);
      target.append(actions);
    },
    refresh: load,
  };
}
