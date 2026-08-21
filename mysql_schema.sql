create table if not exists users (
  id varchar(64) primary key,
  username varchar(80) not null unique,
  password_hash varchar(255) not null,
  role varchar(20) not null default 'user',
  credits int not null default 0,
  access_expires_at datetime not null,
  created_at datetime not null
);

create table if not exists sessions (
  token varchar(128) primary key,
  user_id varchar(64) not null,
  created_at datetime not null,
  foreign key (user_id) references users(id) on delete cascade
);

create table if not exists redeem_keys (
  id varchar(64) primary key,
  code varchar(32) not null unique,
  duration_days int not null,
  key_expires_at datetime null,
  created_by varchar(64) null,
  claimed_by varchar(64) null,
  claimed_at datetime null,
  created_at datetime not null,
  foreign key (created_by) references users(id) on delete set null,
  foreign key (claimed_by) references users(id) on delete set null
);

create table if not exists chat_messages (
  id varchar(64) primary key,
  user_id varchar(64) not null,
  role varchar(20) not null,
  text text not null,
  created_at datetime not null,
  index chat_messages_user_created_idx (user_id, created_at),
  foreign key (user_id) references users(id) on delete cascade
);

create table if not exists credit_transactions (
  id varchar(64) primary key,
  user_id varchar(64) not null,
  amount int not null,
  status varchar(30) not null,
  provider varchar(40) not null,
  reference varchar(160) null,
  created_at datetime not null,
  foreign key (user_id) references users(id) on delete cascade
);

-- ============================================================
-- QUERY REFERENCE: GESTION DE KEYS (REDEEM KEYS)
-- ============================================================
-- Estas queries son equivalentes a las funciones de db.py.
-- Útiles para revisar o insertar datos directamente en la BD.

-- 1) Registrar una key nueva (ej: crear key para 7 dias, sin expiracion de emision)
--    Reemplaza los ? por valores: <code>, <duration_days>, <created_by_user_id>
INSERT INTO redeem_keys
  (id, code, duration_days, key_expires_at, created_by, created_at)
VALUES
  (UUID(), 'SIXTYBETS-AB12-CD34', 7, NULL,
   (SELECT id FROM users WHERE username = 'alec'), NOW());

-- 2) Listar todas las keys con su estado (available / claimed / expired)
SELECT
  code,
  duration_days,
  key_expires_at,
  claimed_by,
  claimed_at,
  created_at,
  CASE
    WHEN claimed_by IS NOT NULL THEN 'claimed'
    WHEN key_expires_at IS NOT NULL AND key_expires_at < NOW() THEN 'expired'
    ELSE 'available'
  END AS status
FROM redeem_keys
ORDER BY created_at DESC
LIMIT 100;

-- 3) Canjear/recoistrar una key para un usuario (equivalente a redeem_key_for_user)
--    Reemplaza <code> y <user_id> por los valores reales
START TRANSACTION;
  SELECT * FROM redeem_keys
  WHERE code = UPPER(TRIM('<code>'))
  FOR UPDATE;
-- Verifica: no debe estar claimed_by ni expirada

  UPDATE redeem_keys
  SET claimed_by = '<user_id>',
      claimed_at = NOW()
  WHERE code = UPPER(TRIM('<code>'))
    AND claimed_by IS NULL
    AND (key_expires_at IS NULL OR key_expires_at > NOW());
COMMIT;

-- 4) Extender el acceso de un usuario tras reclamar una key
--    (suma duration_days al access_expires_at existente)
UPDATE users
SET access_expires_at = GREATEST(access_expires_at, NOW())
  + INTERVAL <duration_days> DAY
WHERE id = '<user_id>';

-- 5) Ver keys sin reclamar (disponibles para canjear)
SELECT code, duration_days, created_at
FROM redeem_keys
WHERE claimed_by IS NULL
  AND (key_expires_at IS NULL OR key_expires_at > NOW());

-- 6) Estadisticas de keys
SELECT
  COUNT(*) AS total,
  SUM(CASE WHEN claimed_by IS NULL
      AND (key_expires_at IS NULL OR key_expires_at > NOW())
      THEN 1 ELSE 0 END) AS available,
  SUM(CASE WHEN claimed_by IS NOT NULL THEN 1 ELSE 0 END) AS claimed,
  SUM(CASE WHEN key_expires_at IS NOT NULL
      AND key_expires_at < NOW()
      AND claimed_by IS NULL THEN 1 ELSE 0 END) AS expired
FROM redeem_keys;

-- ============================================================
-- QUERY REFERENCE: USUARIOS Y SESIONES
-- ============================================================

-- Crear usuario admin directamente
INSERT INTO users (id, username, password_hash, role, credits, access_expires_at, created_at)
VALUES (UUID(), 'admin', '<pbkdf2_hash>', 'admin', 9999,
        DATE_ADD(NOW(), INTERVAL 10000 DAY), NOW());

-- Listar usuarios con su estado de acceso
SELECT
  username,
  role,
  credits,
  access_expires_at,
  CASE
    WHEN access_expires_at < NOW() THEN 'EXPIRADO'
    ELSE 'ACTIVO'
  END AS access_status
FROM users
ORDER BY created_at DESC;

-- Sesiones activas (últimas 24h)
SELECT s.token, u.username, s.created_at
FROM sessions s
JOIN users u ON u.id = s.user_id
WHERE s.created_at >= DATE_SUB(NOW(), INTERVAL 24 HOUR);

-- ============================================================
-- QUERY REFERENCE: MENSAJES DE CHAT
-- ============================================================

-- Mensajes de un usuario (últimas 24h)
SELECT role, text, created_at
FROM chat_messages
WHERE user_id = (SELECT id FROM users WHERE username = '<username>')
  AND created_at >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
ORDER BY created_at ASC;

-- Contar mensajes por usuario (debug)
SELECT u.username, COUNT(m.id) AS msg_count
FROM users u
LEFT JOIN chat_messages m ON m.user_id = u.id
GROUP BY u.id, u.username
ORDER BY msg_count DESC;
