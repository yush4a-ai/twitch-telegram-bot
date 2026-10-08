import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey
from bot.database import Database
from bot.billing import BillingService
from bot.plan_catalog import catalog_payload, PAYMENT_UNAVAILABLE_MESSAGE, BillingRuntimePolicy
from bot.viewer_trial import ViewerTrialService
from tests.test_telegram_navigation import message, CONFIG


class TelegramPlusTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db=Database(':memory:');await self.db.connect();self.addAsyncCleanup(self.db.close)
        self.state=FSMContext(MemoryStorage(),StorageKey(bot_id=999,chat_id=101,user_id=101))
        self.msg=message()

    def cb(self,data,actor=101):
        return SimpleNamespace(data=data,message=self.msg,from_user=SimpleNamespace(id=actor),answer=AsyncMock())

    async def test_role_prices_features_and_inclusion_come_from_shared_catalog(self):
        from bot.handlers.telegram_plus import cb_plus
        await cb_plus(self.cb('menu:plus'),self.state,self.db,CONFIG)
        call=self.msg.edit_text.await_args
        self.assertIn('<b>Зритель Plus</b>\n<b>150 ₽ / месяц</b>',call.args[0])
        for feature in ('200','5 видеопревью','Фильтры','Напоминания','Папки','История'): self.assertIn(feature,call.args[0])
        rows=call.kwargs['reply_markup'].inline_keyboard
        self.assertEqual(rows[0][0].text,'Оформить Зритель Plus: 150 ₽')
        self.assertEqual(rows[1][0].text,'Тариф для стримера')
        await self.db.link_streamer_identity(101,'11','alpha',verified_at=time.time())
        await cb_plus(self.cb('menu:plus'),self.state,self.db,CONFIG)
        call=self.msg.edit_text.await_args
        self.assertIn('<b>Стример Plus</b>\n<b>300 ₽ / месяц</b>',call.args[0]);self.assertIn('В Стример Plus включены все возможности Зритель Plus.',call.args[0])
        self.assertEqual(call.kwargs['reply_markup'].inline_keyboard[0][0].text,'Оформить Стример Plus: 300 ₽')
        self.assertEqual(call.kwargs['reply_markup'].inline_keyboard[1][0].text,'Тариф для зрителя: 150 ₽')

    async def test_active_trial_test_and_frozen_streamer_show_real_expiry_without_paid_claim(self):
        from bot.handlers.telegram_plus import cb_plus
        from bot.subscription_state import SubscriptionService
        now=time.time()
        trial=await ViewerTrialService(self.db).start(101,now=now)
        await cb_plus(self.cb('menu:plus'),self.state,self.db,CONFIG)
        text=self.msg.edit_text.await_args.args[0]
        self.assertIn('Моя подписка',text);self.assertIn('Ознакомительный доступ',text);self.assertNotIn('Оплачено',text)
        self.assertIn('МСК',text)
        await self.db.link_streamer_identity(101,'11','alpha',verified_at=now)
        await self.db.issue_test_streamer_plus('11','test-streamer',starts_at=now-1,expires_at=now+600,
                                               issued_by=999,now=now,beneficiary_telegram_user_id=101)
        await self.db.conn.execute('DELETE FROM streamer_identities WHERE telegram_user_id=101');await self.db.conn.commit()
        await cb_plus(self.cb('menu:plus'),self.state,self.db,CONFIG)
        text=self.msg.edit_text.await_args.args[0]
        self.assertIn('Стример Plus',text);self.assertIn('Зритель Plus включён',text);self.assertIn('Тестовый доступ',text)
        shared=await SubscriptionService(self.db).state(101,now=now)
        self.assertEqual(shared['streamer']['expires_at'],now+600)
        self.assertTrue(shared['viewer']['active']);self.assertEqual(shared['viewer']['expires_at'],trial.expires_at)
        self.assertFalse(shared['streamer']['linked'])
        self.assertFalse((await SubscriptionService(self.db).state(202,now=now))['viewer']['active'])

    async def test_all_three_methods_are_clickable_same_billing_contract_and_zero_mutation(self):
        from bot.handlers.telegram_plus import cb_accept_terms, cb_buy, cb_payment_method
        provider=SimpleNamespace(provider_id='platega',network_free=True,create_payment=AsyncMock(),create_checkout=AsyncMock())
        billing=BillingService(self.db,provider,runtime_policy=BillingRuntimePolicy(mode='sandbox',target_verified=True,
                               allow_external_create=True,allow_invoice=True,period_approved=True,refund_policy_approved=True))
        before=self.db.conn.total_changes
        with (patch.object(billing,'prepare_payment',new_callable=AsyncMock) as actual_payment,
              patch.object(BillingService,'public_purchase',wraps=BillingService.public_purchase) as public):
            for product in ('viewer_plus','streamer_plus'):
                for method in ('stars','sbp','bank_card'):
                    await cb_buy(self.cb('plus:buy:'+product),self.state,self.db)
                    call=self.msg.edit_text.await_args
                    self.assertIn('Platega',call.args[0])
                    buttons=[b for row in call.kwargs['reply_markup'].inline_keyboard for b in row]
                    methods=[b for b in buttons if b.text in ('Telegram Stars','СБП','Банковская карта')]
                    self.assertEqual(len(methods),3)
                    choice=next(b.callback_data for b in methods if b.callback_data.endswith(':'+method))
                    await cb_payment_method(self.cb(choice),self.state,billing)
                    # До подтверждения условий счёт не создаётся и оплата не вызывается.
                    self.assertEqual(public.call_count,(('viewer_plus','streamer_plus').index(product)*3)+('stars','sbp','bank_card').index(method))
                    actual_payment.assert_not_awaited()
                    confirm=next(b.callback_data for b in
                                 [b for row in self.msg.edit_text.await_args.kwargs['reply_markup'].inline_keyboard for b in row]
                                 if b.text=='Я принимаю условия')
                    await cb_accept_terms(self.cb(confirm),self.state,billing)
                    self.assertIn(PAYMENT_UNAVAILABLE_MESSAGE,self.msg.edit_text.await_args.args[0])
                    self.assertIn('Платёж не создан. Деньги не списаны.',self.msg.edit_text.await_args.args[0])
            self.assertEqual(public.call_count,6)
            actual_payment.assert_not_awaited()
        provider.create_payment.assert_not_awaited();provider.create_checkout.assert_not_awaited()
        self.assertEqual(self.db.conn.total_changes,before)
        for table in ('billing_orders','billing_payments','billing_payment_attempts','entitlement_grants'):
            self.assertEqual((await (await self.db.conn.execute('SELECT count(*) FROM '+table)).fetchone())[0],0)

    async def test_wrong_actor_and_cancelled_payment_do_not_reopen_choice_or_call_billing(self):
        from bot.handlers.telegram_plus import cb_buy,cb_payment_method
        billing=SimpleNamespace(public_purchase=unittest.mock.Mock())
        await cb_buy(self.cb('plus:buy:viewer_plus',202),self.state,self.db)
        self.msg.edit_text.assert_not_awaited()
        await cb_buy(self.cb('plus:buy:viewer_plus'),self.state,self.db)
        button=self.msg.edit_text.await_args.kwargs['reply_markup'].inline_keyboard[0][0]
        await self.state.clear()
        await cb_payment_method(self.cb(button.callback_data),self.state,billing)
        billing.public_purchase.assert_not_called()

    def test_public_purchase_validates_catalog_and_stays_off(self):
        for product in catalog_payload()['products']:
            for method in catalog_payload()['methods']:
                result=BillingService.public_purchase(product['product_id'],method['id'])
                self.assertEqual(result,{'state':'unavailable','message':PAYMENT_UNAVAILABLE_MESSAGE,'payment_request_created':False})
        with self.assertRaises(ValueError): BillingService.public_purchase('fake','stars')
        with self.assertRaises(ValueError): BillingService.public_purchase('viewer_plus','crypto')


class PaymentStatusCopyTests(unittest.TestCase):
    """Текст про способы оплаты обязан совпадать с фактической готовностью."""

    def test_bank_channel_copy_follows_the_real_readiness(self):
        from bot.handlers.telegram_plus import bank_channel_ready, payment_status_text

        ready_policy = BillingRuntimePolicy("sandbox", True, True, False, True, True)
        bank = SimpleNamespace(runtime_policy=ready_policy, public_callback_ready=lambda: True)
        closed = SimpleNamespace(runtime_policy=BillingRuntimePolicy(),
                                 public_callback_ready=lambda: False)
        unadmitted = SimpleNamespace(runtime_policy=ready_policy,
                                     public_callback_ready=lambda: False)

        self.assertTrue(bank_channel_ready(bank))
        self.assertFalse(bank_channel_ready(closed))
        self.assertFalse(bank_channel_ready(unadmitted))
        self.assertFalse(bank_channel_ready(None))

        self.assertEqual(payment_status_text(None, None), PAYMENT_UNAVAILABLE_MESSAGE)
        bank_only = payment_status_text(None, bank)
        self.assertIn('СБП и банковская карта: в приложении.', bank_only)
        self.assertNotIn('позже', bank_only)
        self.assertNotIn('подключим', bank_only)
        self.assertIn('Stars: пока недоступны.', bank_only)
