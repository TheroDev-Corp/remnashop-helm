from dishka import Provider, Scope, provide

from src.application.common.dao import (
    AuthSessionDao,
    BroadcastDao,
    PaymentGatewayDao,
    PlanDao,
    ReferralDao,
    SettingsDao,
    SubscriptionDao,
    TransactionDao,
    UserDao,
    UserOAuthProviderDao,
    WaitlistDao,
    WebhookDao,
)
from src.infrastructure.database.dao import (
    BroadcastDaoImpl,
    PaymentGatewayDaoImpl,
    PlanDaoImpl,
    ReferralDaoImpl,
    SettingsDaoImpl,
    SubscriptionDaoImpl,
    TransactionDaoImpl,
    UserDaoImpl,
    UserOAuthProviderDaoImpl,
    WaitlistDaoImpl,
    WebhookDaoImpl,
)
from src.infrastructure.redis.auth import RedisAuthRepository


class DaoProvider(Provider):
    scope = Scope.REQUEST

    broadcast = provide(source=BroadcastDaoImpl, provides=BroadcastDao)
    payment_gateway = provide(source=PaymentGatewayDaoImpl, provides=PaymentGatewayDao)
    plan = provide(source=PlanDaoImpl, provides=PlanDao)
    referral = provide(source=ReferralDaoImpl, provides=ReferralDao)
    settings = provide(source=SettingsDaoImpl, provides=SettingsDao)
    subscription = provide(source=SubscriptionDaoImpl, provides=SubscriptionDao)
    transaction = provide(source=TransactionDaoImpl, provides=TransactionDao)
    user = provide(source=UserDaoImpl, provides=UserDao)
    oauth_provider = provide(source=UserOAuthProviderDaoImpl, provides=UserOAuthProviderDao)

    webhook = provide(source=WebhookDaoImpl, provides=WebhookDao, scope=Scope.APP)
    waitlist = provide(source=WaitlistDaoImpl, provides=WaitlistDao, scope=Scope.APP)
    auth_session = provide(source=RedisAuthRepository, provides=AuthSessionDao, scope=Scope.APP)
