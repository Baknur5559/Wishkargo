#!/bin/bash
# -----------------------------------------------------------------
# Скрипт бэкапа (ВЕРСИЯ 2 - Исправлен парсинг .env)
# -----------------------------------------------------------------

set -e # Остановить скрипт, если любая команда выдаст ошибку

echo "[$(date)] --- Начало скрипта бэкапа ---"

# === КОНФИГУРАЦИЯ ===
GDRIVE_REMOTE="gdrive_backup"
GDRIVE_PATH="Cargo_CRM_Backups"
ENV_FILE="/home/baknur_user/cargo-crm/.env"
BACKUP_DIR="/home/baknur_user/cargo-crm-backups"
DATE=$(date +%Y-%m-%d_%H%M)
FILENAME="cargo_crm_FULL_${DATE}.sql.dump" # Изменил расширение для ясности

# --- Автоматическое получение данных из .env (ИСПРАВЛЕННАЯ ВЕРСИЯ) ---
if [ ! -f "$ENV_FILE" ]; then
    echo "[ОШИБКА] Файл .env не найден по пути $ENV_FILE!"
    exit 1
fi

export $(grep -v '^#' $ENV_FILE | xargs)

if [ -z "$DATABASE_URL" ]; then
    echo "[ОШИБКА] DATABASE_URL не найден в $ENV_FILE!"
    exit 1
fi

# Парсим DATABASE_URL (postgresql://USER:PASS@HOST/DB)
DB_USER=$(echo $DATABASE_URL | sed -n 's/postgresql:\/\/\([^:]*\):.*/\1/p')
DB_PASSWORD=$(echo $DATABASE_URL | sed -n 's/postgresql:\/\/[^:]*:\([^@]*\)@.*/\1/p')
DB_NAME=$(echo $DATABASE_URL | sed -n 's/.*\/\(.*\)/\1/p')
# ИСПРАВЛЕННАЯ ЛОВЛЯ ХОСТА (между @ и /)
DB_HOST=$(echo $DATABASE_URL | sed -n 's/.*@\([^\/]*\)\/.*/\1/p')

if [ -z "$DB_USER" ] || [ -z "$DB_PASSWORD" ] || [ -z "$DB_NAME" ] || [ -z "$DB_HOST" ]; then
    echo "[ОШИБКА] Не удалось разобрать DATABASE_URL. Проверь .env!"
    echo "DB_USER: $DB_USER, DB_NAME: $DB_NAME, DB_HOST: $DB_HOST"
    exit 1
fi

# ИСПРАВЛЕННАЯ УСТАНОВКА ПАРОЛЯ
export PGPASSWORD=$DB_PASSWORD
echo "[+] Данные .env успешно загружены (Хост: $DB_HOST)"
# --- Конец авто-получения данных ---


# === ЛОГИКА СКРИПТА ===

# 1. Создаем временную папку
mkdir -p $BACKUP_DIR
echo "[+] Временная папка проверена: $BACKUP_DIR"

# 2. Делаем "слепок" (dump)
echo "[+] Создание 'слепка' базы данных: $FILENAME..."
pg_dump -U $DB_USER -h $DB_HOST -d $DB_NAME -F c -b -v -f "${BACKUP_DIR}/${FILENAME}"
echo "[+] 'Слепок' успешно создан."

# 3. Загружаем на Google Drive
MONTH_PATH=$(date +%Y-%m)
echo "[+] Загрузка ${FILENAME} в Google Drive ($GDRIVE_PATH/$MONTH_PATH)..."
rclone copy "${BACKUP_DIR}/${FILENAME}" "${GDRIVE_REMOTE}:${GDRIVE_PATH}/${MONTH_PATH}/"
echo "[+] Загрузка завершена."

# 4. Ротация (Удаляем старые бэкапы)
echo "[+] Удаление старых бэкапов (старше 7 дней) с Google Drive..."
rclone delete --min-age 7d "${GDRIVE_REMOTE}:${GDRIVE_PATH}/"
echo "[+] Ротация завершена."

# 5. Очистка (Удаляем временный файл с сервера)
echo "[+] Очистка временных файлов на сервере..."
rm "${BACKUP_DIR}/${FILENAME}"
echo "[+] Очистка завершена."

echo "[$(date)] --- Скрипт бэкапа успешно завершен ---"