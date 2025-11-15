#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import subprocess
import logging
from sqlalchemy import create_engine, Column, Integer, String, Boolean, Date, MetaData
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.exc import OperationalError
from dotenv import load_dotenv

# --- Настройка логгирования ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Константы ---
SUPERVISOR_CONF_DIR = "/etc/supervisor/conf.d"
BOT_SCRIPT_PATH = "/home/baknur_user/cargo-crm/bot_template.py" # Путь к твоему скрипту бота
PYTHON_EXECUTABLE = "/home/baknur_user/cargo-crm/venv/bin/python" # Путь к Python в твоем venv
API_URL = "http://127.0.0.1:8000" # URL бэкенда для ботов
USER = "baknur_user" # Имя пользователя Linux, от которого будут запускаться боты
CONFIG_FILE_PREFIX = "cargo_bot_" # Префикс для файлов конфигурации

# --- Шаблон конфигурационного файла Supervisor ---
# %(program_name)s - имя программы (например, cargo_bot_WISH)
# %(company_id)s - ID компании
# %(bot_token)s - Токен Telegram бота
# %(company_code)s - Код компании (для лог файла)
CONFIG_TEMPLATE = """
[program:{program_name}]
command={python_executable} {bot_script_path}
directory={project_dir}
user={user}
autostart=true
autorestart=true
stopwaitsecs=600 ; wait 10 minutes before killing the script
stderr_logfile=/var/log/supervisor/{program_name}_err.log
stdout_logfile=/var/log/supervisor/{program_name}_out.log
environment=LANG="en_US.UTF-8",LC_ALL="en_US.UTF-8",ADMIN_API_URL="{api_url}",TELEGRAM_BOT_TOKEN="{bot_token}"{env_extras}
"""

# --- Определение модели Company (только нужные поля) ---
# Используем declarative_base(), чтобы не импортировать твои модели напрямую
Base = declarative_base()
class Company(Base):
    __tablename__ = 'companies'
    id = Column(Integer, primary_key=True)
    company_code = Column(String, unique=True, index=True)
    telegram_bot_token = Column(String, nullable=True, unique=True)
    # Добавляем is_active, чтобы не запускать ботов для неактивных компаний
    is_active = Column(Boolean, default=True)

def run_supervisor_command(command):
    """Выполняет команду supervisorctl с правами sudo."""
    full_command = ["sudo", "supervisorctl"] + command
    logger.info(f"Выполнение команды: {' '.join(full_command)}")
    try:
        result = subprocess.run(full_command, capture_output=True, text=True, check=True)
        logger.info(f"Вывод supervisorctl: {result.stdout.strip()}")
        return True
    except FileNotFoundError:
        logger.error("Ошибка: Команда 'sudo' или 'supervisorctl' не найдена. Установлен ли Supervisor?")
        return False
    except subprocess.CalledProcessError as e:
        logger.error(f"Ошибка выполнения supervisorctl {' '.join(command)}:")
        logger.error(f"Stderr: {e.stderr.strip()}")
        logger.error(f"Stdout: {e.stdout.strip()}")
        return False
    except Exception as e:
        logger.error(f"Неожиданная ошибка при выполнении supervisorctl: {e}")
        return False

def main():
    """Основная функция скрипта."""
    logger.info("--- Запуск скрипта управления ботами Supervisor ---")

    # --- Загрузка переменных окружения ---
    project_dir = os.path.dirname(os.path.abspath(__file__))
    dotenv_path = os.path.join(project_dir, '.env')
    if os.path.exists(dotenv_path):
        load_dotenv(dotenv_path)
        logger.info("Переменные из .env загружены.")
    else:
        logger.warning("Файл .env не найден.")

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        logger.error("Переменная DATABASE_URL не найдена в .env или окружении.")
        sys.exit(1)

    # --- Подключение к БД ---
    try:
        engine = create_engine(database_url)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        db = SessionLocal()
        logger.info("Успешное подключение к базе данных.")
    except OperationalError as e:
        logger.error(f"Ошибка подключения к базе данных: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Неожиданная ошибка при подключении к БД: {e}")
        sys.exit(1)

    # --- Получение данных компаний с токенами ---
    try:
        companies_with_bots = db.query(Company).filter(
            Company.telegram_bot_token.isnot(None), # Есть токен
            Company.telegram_bot_token != '',      # Токен не пустой
            Company.is_active == True             # Компания активна
        ).all()
        logger.info(f"Найдено {len(companies_with_bots)} активных компаний с токенами ботов в БД.")
    except Exception as e:
        logger.error(f"Ошибка при запросе компаний из БД: {e}")
        db.close()
        sys.exit(1)

    # Словарь для отслеживания нужных конфигов {имя_программы: данные_компании}
    required_programs = {}
    for company in companies_with_bots:
        if not company.company_code:
             logger.warning(f"Пропуск компании ID {company.id}: отсутствует company_code.")
             continue
        program_name = f"{CONFIG_FILE_PREFIX}{company.company_code}"
        required_programs[program_name] = company

    # --- Получение существующих конфигов Supervisor ---
    existing_conf_files = {} # {имя_файла_без_расширения: полный_путь}
    try:
        if not os.path.exists(SUPERVISOR_CONF_DIR):
             logger.warning(f"Директория {SUPERVISOR_CONF_DIR} не существует. Пропускаем проверку существующих конфигов.")
        else:
            for filename in os.listdir(SUPERVISOR_CONF_DIR):
                if filename.startswith(CONFIG_FILE_PREFIX) and filename.endswith(".conf"):
                    program_name_from_file = filename[:-5] # Убираем .conf
                    existing_conf_files[program_name_from_file] = os.path.join(SUPERVISOR_CONF_DIR, filename)
            logger.info(f"Найдено {len(existing_conf_files)} существующих конфигурационных файлов ботов.")
    except Exception as e:
        logger.error(f"Ошибка при чтении директории {SUPERVISOR_CONF_DIR}: {e}")
        # Продолжаем, но можем не удалить старые файлы

    # --- Синхронизация: Создание / Обновление ---
    configs_changed = False
    for program_name, company in required_programs.items():
        conf_path = os.path.join(SUPERVISOR_CONF_DIR, f"{program_name}.conf")
        # --- ЛОГИКА ВКЛЮЧЕНИЯ ИИ ---
        # Включаем ИИ только для компании с кодом 'TEST' (или можно добавить других)
        env_extras = ""
        if company.company_code == "TEST":
            env_extras = ',ENABLE_AI="True"'

        conf_content = CONFIG_TEMPLATE.format(
            program_name=program_name,
            python_executable=PYTHON_EXECUTABLE,
            bot_script_path=BOT_SCRIPT_PATH,
            bot_token=company.telegram_bot_token,
            company_id=company.id,
            api_url=API_URL,
            project_dir=project_dir,
            user=USER,
            env_extras=env_extras # <-- Вставляем доп. переменную
        ).strip() + "\n" # Добавляем перенос строки в конце

        # Проверяем, существует ли файл и нужно ли его обновить
        needs_update = True
        if os.path.exists(conf_path):
            try:
                with open(conf_path, 'r') as f:
                    current_content = f.read()
                if current_content == conf_content:
                    needs_update = False # Содержимое совпадает, обновлять не нужно
                else:
                     logger.info(f"Конфигурация для {program_name} изменилась.")
            except Exception as e:
                logger.warning(f"Не удалось прочитать существующий файл {conf_path}: {e}. Файл будет перезаписан.")
        else:
             logger.info(f"Конфигурация для {program_name} будет создана.")


        if needs_update:
            try:
                # Используем sudo для записи в /etc
                # Лучше запускать сам скрипт через sudo, чем вызывать sudo внутри
                # НО! Для простоты пока оставим вызов sudo dd
                # Важно: 'w' для open здесь не сработает из-за прав
                command = f'echo "{conf_content.replace("\"", "\\\"")}" | sudo dd of={conf_path}'
                subprocess.run(command, shell=True, check=True, capture_output=True)
                logger.info(f"Успешно записан файл: {conf_path}")
                configs_changed = True
            except subprocess.CalledProcessError as e:
                logger.error(f"Ошибка записи файла {conf_path} через sudo dd: {e.stderr.decode()}")
            except Exception as e:
                logger.error(f"Не удалось записать файл {conf_path}: {e}")

    # --- Синхронизация: Удаление ненужных ---
    programs_to_remove = set(existing_conf_files.keys()) - set(required_programs.keys())
    for program_name in programs_to_remove:
        conf_path = existing_conf_files[program_name]
        logger.info(f"Удаление устаревшего файла конфигурации: {conf_path}")
        try:
            subprocess.run(["sudo", "rm", conf_path], check=True)
            configs_changed = True
        except subprocess.CalledProcessError as e:
            logger.error(f"Ошибка удаления файла {conf_path}: {e}")
        except Exception as e:
            logger.error(f"Не удалось удалить файл {conf_path}: {e}")

    # --- Применение изменений в Supervisor ---
    if configs_changed:
        logger.info("Обнаружены изменения в конфигурации. Обновление Supervisor...")
        if run_supervisor_command(["reread"]):
            run_supervisor_command(["update"])
    else:
        logger.info("Изменений в конфигурации не обнаружено.")

    # Проверка статуса (опционально)
    logger.info("Проверка статуса процессов Supervisor...")
    run_supervisor_command(["status"])

    db.close()
    logger.info("--- Скрипт управления ботами завершен ---")

if __name__ == "__main__":
    main()