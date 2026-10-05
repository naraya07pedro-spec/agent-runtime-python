from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def connect(url: str) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        url,
        hide_parameters=True,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=5,
        pool_timeout=5,
        connect_args={
            "timeout": 5,
            "command_timeout": 10,
            "server_settings": {"statement_timeout": "10000", "lock_timeout": "3000"},
        },
    )
    return engine, async_sessionmaker(engine, expire_on_commit=False)
