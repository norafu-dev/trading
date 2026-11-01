"""登录和设置的流程。每个函数自己开事务，可预期的失败用返回值表达，由路由转成 HTTP 错误。

顺序是固定的：拿锁 → 全局限速 → IP 限速 → 校验 → 写结果和 login_event → 提交。
拿着锁算 argon2，所以同一时间最多只有一个在算（spec 0004 Key invariants）。
"""

import asyncio
from dataclasses import dataclass

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from harvester.api import auth_core as core
from harvester.api import auth_store as store
from harvester.api.deps import Clock
from harvester.crypto import PURPOSE_TOTP, DecryptError, KeyRing, decrypt, encrypt
from harvester.db.models import DashboardCredential

log = structlog.get_logger("harvester.api.auth")


@dataclass(frozen=True)
class RateLimited:
    retry_after_seconds: int


@dataclass(frozen=True)
class SetupRequired:
    pass


@dataclass(frozen=True)
class AlreadySetUp:
    pass


@dataclass(frozen=True)
class BadCredentials:
    pass


@dataclass(frozen=True)
class SetupCodeInvalid:
    pass


@dataclass(frozen=True)
class TotpInvalid:
    pass


@dataclass(frozen=True)
class SecretUndecryptable:
    pass


@dataclass(frozen=True)
class LoggedIn:
    cookie_value: str


@dataclass(frozen=True)
class TotpProvisioning:
    otpauth_uri: str
    secret: str


LoginResult = RateLimited | SetupRequired | BadCredentials | SecretUndecryptable | LoggedIn
BeginResult = RateLimited | AlreadySetUp | SetupCodeInvalid | TotpProvisioning
CompleteResult = (
    RateLimited | AlreadySetUp | SetupCodeInvalid | TotpInvalid | SecretUndecryptable | LoggedIn
)


def _decrypt_totp(keys: KeyRing, blob: bytes) -> str | SecretUndecryptable:
    plain = decrypt(keys, PURPOSE_TOTP, blob)
    if isinstance(plain, DecryptError):
        log.error("totp_secret_undecryptable", reason=plain.reason)
        return SecretUndecryptable()
    return plain.decode()


async def _rehash_if_outdated(credential: DashboardCredential, password: str) -> None:
    if core.password_needs_rehash(credential.password_hash):
        credential.password_hash = await asyncio.to_thread(core.hash_password, password)


async def login(
    session: AsyncSession,
    *,
    password: str,
    totp_code: str,
    ctx: core.RequestContext,
    old_token_hash: bytes | None,
    keys: KeyRing,
    clock: Clock,
) -> LoginResult:
    async with session.begin():
        await store.lock_auth(session)
        # 等锁可能要一会儿，所以拿到锁之后才取时间。
        now = clock()
        wait = await store.check_rate_limit(session, ctx, now)
        if wait is not None:
            log.warning("auth.rate_limited", ip=ctx.ip)
            return RateLimited(wait)
        credential = await store.get_credential(session)
        if credential is None:
            return SetupRequired()
        secret = _decrypt_totp(keys, credential.totp_secret_enc)
        if isinstance(secret, SecretUndecryptable):
            return secret

        # 两项都校验完才下结论，不透露是哪一个错了。
        password_ok = await asyncio.to_thread(
            core.verify_password, credential.password_hash, password
        )
        step = core.verify_totp_step(secret, totp_code, now, credential.totp_last_step)
        # 只有两者都对才写 totp_last_step，别人没法用错误密码烧掉你当前的验证码。
        if not password_ok or step is None or not await store.claim_totp_step(session, step):
            await store.record_event(session, "bad_credentials", ctx.ip, ctx.user_agent, now)
            log.info("auth.login_failed", ip=ctx.ip, outcome="bad_credentials")
            return BadCredentials()

        await _rehash_if_outdated(credential, password)
        cookie_value = await store.create_session(session, ctx, old_token_hash, now)
        await store.record_event(session, "success", ctx.ip, ctx.user_agent, now)
        await store.purge_old_rows(session, now)
        log.info("auth.login_succeeded", ip=ctx.ip, outcome="success")
        return LoggedIn(cookie_value)


async def _record_bad_setup_code(
    session: AsyncSession, ctx: core.RequestContext, clock: Clock
) -> SetupCodeInvalid:
    await store.record_event(session, "setup_bad_code", ctx.ip, ctx.user_agent, clock())
    log.info("auth.login_failed", ip=ctx.ip, outcome="setup_bad_code")
    return SetupCodeInvalid()


async def setup_begin(
    session: AsyncSession, *, setup_code: str, ctx: core.RequestContext, keys: KeyRing, clock: Clock
) -> BeginResult:
    async with session.begin():
        await store.lock_auth(session)
        now = clock()
        wait = await store.check_rate_limit(session, ctx, now)
        if wait is not None:
            log.warning("auth.rate_limited", ip=ctx.ip)
            return RateLimited(wait)
        if await store.get_credential(session) is not None:
            return AlreadySetUp()
        secret = core.new_totp_secret()
        stored = await store.store_pending_totp(
            session,
            core.hash_token(setup_code),
            encrypt(keys, PURPOSE_TOTP, secret.encode()),
            now,
        )
        if not stored:
            return await _record_bad_setup_code(session, ctx, clock)
        return TotpProvisioning(otpauth_uri=core.totp_uri(secret), secret=secret)


async def setup_complete(
    session: AsyncSession,
    *,
    setup_code: str,
    password: str,
    totp_code: str,
    ctx: core.RequestContext,
    old_token_hash: bytes | None,
    keys: KeyRing,
    clock: Clock,
) -> CompleteResult:
    async with session.begin():
        await store.lock_auth(session)
        now = clock()
        wait = await store.check_rate_limit(session, ctx, now)
        if wait is not None:
            log.warning("auth.rate_limited", ip=ctx.ip)
            return RateLimited(wait)
        if await store.get_credential(session) is not None:
            return AlreadySetUp()
        code_row = await store.get_valid_setup_code(session, core.hash_token(setup_code), now)
        if code_row is None:
            return await _record_bad_setup_code(session, ctx, clock)
        secret_enc = code_row.pending_totp_secret_enc
        if secret_enc is None:
            return TotpInvalid()
        secret = _decrypt_totp(keys, secret_enc)
        if isinstance(secret, SecretUndecryptable):
            return secret
        step = core.verify_totp_step(secret, totp_code, now, None)
        if step is None:
            return TotpInvalid()

        session.add(
            DashboardCredential(
                id=store.SINGLE_ROW_ID,
                password_hash=await asyncio.to_thread(core.hash_password, password),
                totp_secret_enc=secret_enc,
                totp_last_step=step,
                created_at=now,
                updated_at=now,
            )
        )
        await session.delete(code_row)
        cookie_value = await store.create_session(session, ctx, old_token_hash, now)
        await store.record_event(session, "setup_completed", ctx.ip, ctx.user_agent, now)
        log.info("auth.setup_completed", ip=ctx.ip, outcome="setup_completed")
        return LoggedIn(cookie_value)


async def logout(
    session: AsyncSession, *, token_hash: bytes | None, ctx: core.RequestContext, clock: Clock
) -> None:
    if token_hash is None:
        return
    async with session.begin():
        # 只有真的删掉了会话才记，这个接口是公开的，不能让人刷满记录。
        if await store.delete_session(session, token_hash):
            await store.record_event(session, "logout", ctx.ip, ctx.user_agent, clock())
            log.info("auth.logout", ip=ctx.ip, outcome="logout")
