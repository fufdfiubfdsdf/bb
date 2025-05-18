import os
from dataclasses import dataclass
from typing import Dict

@dataclass
class BotConfig:
    token: str
    yoomoney_wallet: str
    notification_secret: str
    private_channel_id: int
    price: float
    description: str

def load_bot_configs() -> Dict[str, BotConfig]:
    """Загрузка конфигураций ботов из переменных окружения или файла."""
    bots = {}
    # Пример: загрузка из переменных окружения
    for i in range(1, 15):  # Предполагаем до 14 ботов
        prefix = f"BOT_{i}_"
        token = os.getenv(f"{prefix}TOKEN")
        if not token:
            continue
        bots[f"bot{i}"] = BotConfig(
            token=token,
            yoomoney_wallet=os.getenv(f"{prefix}YOOMONEY_WALLET", "4100118178122985"),
            notification_secret=os.getenv(f"{prefix}NOTIFICATION_SECRET", "CoqQlgE3E5cTzyAKY1LSiLU1"),
            private_channel_id=int(os.getenv(f"{prefix}PRIVATE_CHANNEL_ID", "-1002640947060")),
            price=float(os.getenv(f"{prefix}PRICE", "600.00")),
            description=os.getenv(f"{prefix}DESCRIPTION", (
                "Тариф: Стандарт\n"
                "Стоимость: {price} 🇷🇺RUB\n"
                "Срок действия: 1 месяц\n\n"
                "Доступ к закрытому каналу"
            ))
        )
    return bots
