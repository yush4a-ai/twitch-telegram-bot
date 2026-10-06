"""One immutable server catalog; monetary activation stays disabled by default."""

from dataclasses import asdict, dataclass
from types import MappingProxyType

from .billing_models import Money, ProductSnapshot

FREE_VIEWER_CHANNEL_LIMIT = 50
VIEWER_PLUS_CHANNEL_LIMIT = 200
VIEWER_PLUS_VIDEO_SLOTS = 5
CATALOG_VERSION = "2026-10-02-role-plus-300-v1"
PAYMENT_UNAVAILABLE_MESSAGE = "Оплата временно недоступна. Мы заканчиваем подключение платёжной системы."
# Утверждённые условия подписки: один месяц, без автопродления, возврат 14 дней.
# Версия срока должна совпадать у каталога и у платёжного сервиса.
PLUS_PERIOD_RULE = "30_days"
PLUS_PERIOD_VERSION = "2026-10-05-plus-30-days-v1"
PLUS_TERMS_VERSION = "2026-10-05-plus-terms-v1"
# Цена в звёздах Telegram. Предложение к утверждению владельцем: одна звезда
# примерно 1,5 ₽, поэтому 150 ₽ ≈ 100 звёзд и 300 ₽ ≈ 200 звёзд.
VIEWER_PLUS_XTR = 100
STREAMER_PLUS_XTR = 200


@dataclass(frozen=True)
class BillingRuntimePolicy:
    mode: str = "offline"
    target_verified: bool = False
    allow_external_create: bool = False
    allow_invoice: bool = False
    period_approved: bool = False
    refund_policy_approved: bool = False
    # Публичная покупка звёздами в боте. Отдельный флаг: наличие провайдера и
    # ключей не должно само по себе открывать оплату живым людям.
    allow_public_stars: bool = False
    # Запрет банковского канала со стороны production-контракта: значение
    # `PRODUCTION_PAYMENT_POLICY=off` обязано что-то значить, но не должно
    # выключать звёзды Telegram — это отдельный канал.
    external_blocked_by_contract: bool = False

    def __post_init__(self):
        if self.mode not in {"offline", "sandbox"} or any(type(value) is not bool for value in (
            self.target_verified, self.allow_external_create, self.allow_invoice,
            self.period_approved, self.refund_policy_approved, self.allow_public_stars,
            self.external_blocked_by_contract,
        )):
            raise ValueError("invalid billing runtime policy")


@dataclass(frozen=True)
class CheckoutReadiness:
    enabled: bool
    reason_code: str | None


# Каналы приёма денег описаны отдельным модулем: каталог — это то, что видит
# человек, а техническая модель оплаты не должна попадать в пользовательские
# тексты (проверка копирайта запрещает там служебные слова).
from .payment_policy import PaymentChannel, payment_channels  # noqa: F401


_FEATURES = MappingProxyType({
    "viewer_channels": ("До 200 стримеров", "В Free: до 50", "people"),
    "viewer_video": ("5 видеопревью", "Выберите до пяти стримеров, включая тех, кто сейчас не в эфире. В Free: фото.", "video"),
    "viewer_filters": ("Фильтры уведомлений", "По игре и словам в названии, с исключениями", "filter"),
    "viewer_categories": ("Категории", "Нужная категория и уведомления о смене категории", "notification"),
    "viewer_reminders": ("Напоминания", "Через 15 или 30 минут после выбора текущего эфира", "clock"),
    "viewer_folders": ("Папки", "Подписки и общие правила уведомлений", "folder"),
    "viewer_history": ("История", "Результаты собственных уведомлений", "history"),
    "streamer_video": ("Видеопревью в посте", "Живое превью вместо фото при доступном ресурсе", "video"),
    "streamer_text": ("Свой текст и оформление", "Шаблон публикации для собственного канала", "text"),
    "streamer_buttons": ("Дополнительные кнопки", "Проверенные ссылки в публикации", "buttons"),
    "streamer_presets": ("Сохранённые варианты", "Сохраните оформление и примените его отдельным действием", "folder"),
    "streamer_publication_stats": ("Статистика публикаций", "Подтверждённые публикации за 30 дней и сравнение 7 + 7 дней", "chart"),
})
_VIEWER_IDS = tuple(key for key in _FEATURES if key.startswith("viewer_"))
_STREAMER_IDS = tuple(key for key in _FEATURES if key.startswith("streamer_"))
_PRODUCTS = (
    ProductSnapshot("viewer_plus", CATALOG_VERSION, Money(15000, "RUB"), Money(VIEWER_PLUS_XTR, "XTR"),
                    "one_month", PLUS_PERIOD_RULE, PLUS_PERIOD_VERSION, False, (), _VIEWER_IDS),
    ProductSnapshot("streamer_plus", CATALOG_VERSION, Money(30000, "RUB"), Money(STREAMER_PLUS_XTR, "XTR"),
                    "one_month", PLUS_PERIOD_RULE, PLUS_PERIOD_VERSION, False, ("viewer_plus",), _STREAMER_IDS),
)
_METHODS = (
    ("stars", "Telegram Stars", "telegram_stars"),
    ("sbp", "СБП", "platega"),
    ("bank_card", "Банковская карта", "platega"),
)
_BLOCKS = MappingProxyType({
    "viewer_plus": (
        ("До 200 стримеров", "Free: до 50", "people", ("viewer_channels",)),
        ("5 видеопревью", "Free: фото", "video", ("viewer_video",)),
        ("Точные уведомления", "Фильтры, категории, напоминания", "notification", ("viewer_filters", "viewer_categories", "viewer_reminders")),
        ("Папки и история", "Подписки и события в одном месте", "folder", ("viewer_folders", "viewer_history")),
    ),
    "streamer_plus": (
        ("Видеопревью в посте", "Живое превью вместо фото", "video", ("streamer_video",)),
        ("Свой текст и оформление", "Публикация в стиле вашего канала", "text", ("streamer_text",)),
        ("Кнопки и сохранённые варианты", "Ссылки и оформление под разные эфиры", "buttons", ("streamer_buttons", "streamer_presets")),
        ("Статистика публикаций", "Подтверждённые публикации и сравнение периодов", "chart", ("streamer_publication_stats",)),
    ),
})


def get_product(product_id: str) -> ProductSnapshot:
    for product in _PRODUCTS:
        if product.product_id == product_id:
            return product
    raise ValueError("unknown billing product")


def list_products() -> tuple[ProductSnapshot, ...]:
    return _PRODUCTS


def get_feature(feature_id: str) -> dict[str, str]:
    if not isinstance(feature_id, str) or feature_id not in _FEATURES:
        raise ValueError("unknown catalog feature")
    title, description, icon = _FEATURES[feature_id]
    return {"title": title, "description": description, "icon": icon}


def checkout_readiness(product_id: str, method: str, policy: BillingRuntimePolicy) -> CheckoutReadiness:
    product = get_product(product_id)
    if method not in {row[0] for row in _METHODS} or not isinstance(policy, BillingRuntimePolicy):
        raise ValueError("invalid checkout method/policy")
    if policy.mode == "offline":
        return CheckoutReadiness(False, "payments_unavailable")
    if not policy.target_verified:
        return CheckoutReadiness(False, "target_unverified")
    if not policy.period_approved or product.period_rule == "unapproved":
        return CheckoutReadiness(False, "period_unapproved")
    if not policy.refund_policy_approved:
        return CheckoutReadiness(False, "policy_unapproved")
    # Звёзды требуют и счёта, и разрешения владельца на публичную оплату:
    # витрина не должна обещать способ, который затем откажет.
    if method == "stars" and (
        product.xtr is None or not policy.allow_invoice or not policy.allow_public_stars
    ):
        return CheckoutReadiness(False, "stars_unavailable")
    if method != "stars" and not policy.allow_external_create:
        return CheckoutReadiness(False, "provider_unavailable")
    return CheckoutReadiness(True, None)


def catalog_payload(policy: BillingRuntimePolicy | None = None) -> dict:
    policy = policy or BillingRuntimePolicy()
    products = []
    for product in _PRODUCTS:
        item = asdict(product)
        item.update({
            "title": "Зритель Plus" if product.product_id == "viewer_plus" else "Стример Plus",
            "price_label": f"{product.rub.amount_minor // 100} ₽",
            "period_label": "1 месяц",
            "value": "Больше стримеров, видеопревью и более точные уведомления." if product.product_id == "viewer_plus" else "Больше возможностей для автоматических публикаций о стримах.",
            "benefit_blocks": [{"title": title, "description": description, "icon": icon, "feature_ids": ids}
                               for title, description, icon, ids in _BLOCKS[product.product_id]],
            "method_readiness": {method: asdict(checkout_readiness(product.product_id, method, policy)) for method, _title, _provider in _METHODS},
        })
        products.append(item)
    return {
        "version": CATALOG_VERSION, "products": products,
        "features": {feature_id: get_feature(feature_id) for feature_id in _FEATURES},
        "methods": [{"id": method, "title": title, "provider": provider,
                     "readiness": asdict(checkout_readiness("viewer_plus", method, policy))}
                    for method, title, provider in _METHODS],
        "primary_products": {"viewer": "viewer_plus", "streamer": "streamer_plus"},
        "limits": {"free_streamers": FREE_VIEWER_CHANNEL_LIMIT, "plus_streamers": VIEWER_PLUS_CHANNEL_LIMIT, "video_slots": VIEWER_PLUS_VIDEO_SLOTS},
        "free_features": ["go_live", "photo_preview", "quiet_hours", "twitch_unavailable", "standard_post", "twitch_connection", "telegram_channel_connection"],
    }


def viewer_channel_limit(plus_active: bool) -> int:
    return VIEWER_PLUS_CHANNEL_LIMIT if plus_active else FREE_VIEWER_CHANNEL_LIMIT


def viewer_video_slots(plus_active: bool) -> int:
    return VIEWER_PLUS_VIDEO_SLOTS if plus_active else 0
