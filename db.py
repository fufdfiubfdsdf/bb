import psycopg2
import logging

logger = logging.getLogger(__name__)

class Database:
    def __init__(self, connection_string: str):
        self.connection_string = connection_string
        self._init_db()

    def _init_db(self):
        """Инициализация таблиц для каждого бота."""
        try:
            with psycopg2.connect(self.connection_string) as conn:
                with conn.cursor() as c:
                    for bot_id in load_bot_configs():
                        c.execute(f"""
                            CREATE TABLE IF NOT EXISTS payments_{bot_id} (
                                label TEXT PRIMARY KEY,
                                user_id TEXT,
                                status TEXT
                            )
                        """)
                conn.commit()
                logger.info("База данных инициализирована")
        except Exception as e:
            logger.error(f"Ошибка инициализации базы данных: {e}")
            raise

    def save_payment(self, bot_id: str, label: str, user_id: str):
        """Сохранение платежа."""
        try:
            with psycopg2.connect(self.connection_string) as conn:
                with conn.cursor() as c:
                    c.execute(
                        f"INSERT INTO payments_{bot_id} (label, user_id, status) VALUES (%s, %s, %s) "
                        f"ON CONFLICT (label) DO UPDATE SET user_id = %s, status = %s",
                        (label, user_id, "pending", user_id, "pending")
                    )
                conn.commit()
                logger.info(f"[{bot_id}] Сохранен платеж: label={label}, user_id={user_id}")
        except Exception as e:
            logger.error(f"[{bot_id}] Ошибка сохранения платежа: {e}")
            raise

    def get_user_by_label(self, bot_id: str, label: str) -> str:
        """Получение user_id по label."""
        try:
            with psycopg2.connect(self.connection_string) as conn:
                with conn.cursor() as c:
                    c.execute(f"SELECT user_id FROM payments_{bot_id} WHERE label = %s", (label,))
                    result = c.fetchone()
                    return result[0] if result else None
        except Exception as e:
            logger.error(f"[{bot_id}] Ошибка получения user_id по label={label}: {e}")
            return None

    def update_payment_status(self, bot_id: str, label: str, status: str):
        """Обновление статуса платежа."""
        try:
            with psycopg2.connect(self.connection_string) as conn:
                with conn.cursor() as c:
                    c.execute(
                        f"UPDATE payments_{bot_id} SET status = %s WHERE label = %s",
                        (status, label)
                    )
                conn.commit()
                logger.info(f"[{bot_id}] Обновлен статус платежа: label={label}, status={status}")
        except Exception as e:
            logger.error(f"[{bot_id}] Ошибка обновления статуса платежа: {e}")
            raise
