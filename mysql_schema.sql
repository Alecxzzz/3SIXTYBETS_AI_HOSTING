-- ============================================================
-- SIXTYDB - SCRIPT CORREGIDO Y LISTO PARA USAR
-- ============================================================
-- Las secciones marcadas "EJECUTAR DIRECTO" no necesitan cambios.
-- Las secciones marcadas "REEMPLAZA ANTES DE CORRER" tienen un
-- valor real de ejemplo, pero debes cambiarlo por el tuyo.
-- ============================================================


-- ============================================================
-- 1) CREACION DE TABLAS  -- EJECUTAR DIRECTO (ya lo hiciste)
-- ============================================================
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

create table if not exists pagadito_orders (
  id varchar(64) primary key,
  user_id varchar(64) not null,
  plan_code varchar(40) not null,
  amount decimal(10, 2) not null,
  currency varchar(3) not null default 'USD',
  ern varchar(64) not null unique,
  token_trans varchar(128) null,
  status varchar(20) not null default 'pending',
  reference varchar(160) null,
  created_at datetime not null,
  updated_at datetime null,
  foreign key (user_id) references users(id) on delete cascade
);


-- ============================================================
-- 2) CREAR UNA KEY NUEVA  -- REEMPLAZA EL CODE ANTES DE CORRER
-- ============================================================
-- El 'code' debe ser unico. Cambia 'SIXTYBETS-XXXX-YYYY' cada vez.
INSERT INTO redeem_keys
  (id, code, duration_days, key_expires_at, created_by, created_at)
VALUES
  (UUID(), 'SIXTYBETS-XXXX-YYYY', 7, NULL,
   (SELECT id FROM users WHERE username = 'alec'), NOW());


-- ============================================================
-- 3) LISTAR KEYS CON ESTADO  -- EJECUTAR DIRECTO
-- ============================================================
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


-- ============================================================
-- 4) CANJEAR UNA KEY PARA UN USUARIO
--    REEMPLAZA 'CODIGO_AQUI' y 'USERNAME_AQUI' antes de correr
-- ============================================================
START TRANSACTION;

  SELECT * FROM redeem_keys
  WHERE code = UPPER(TRIM('CODIGO_AQUI'))
  FOR UPDATE;
  -- Verifica manualmente: claimed_by debe ser NULL y no debe estar expirada

  UPDATE redeem_keys
  SET claimed_by = (SELECT id FROM users WHERE username = 'USERNAME_AQUI'),
      claimed_at = NOW()
  WHERE code = UPPER(TRIM('CODIGO_AQUI'))
    AND claimed_by IS NULL
    AND (key_expires_at IS NULL OR key_expires_at > NOW());

COMMIT;


-- ============================================================
-- 5) EXTENDER ACCESO DE UN USUARIO (sumar dias)
--    REEMPLAZA 'alec' y 30 por el usuario/dias que necesites
--    (version corregida: usa JOIN, no subconsulta sobre la misma tabla)
-- ============================================================
UPDATE users u
JOIN (SELECT id FROM users WHERE username = 'alec') AS target
  ON u.id = target.id
SET u.access_expires_at = GREATEST(u.access_expires_at, NOW()) + INTERVAL 30 DAY;


-- ============================================================
-- 6) VER KEYS DISPONIBLES (sin reclamar)  -- EJECUTAR DIRECTO
-- ============================================================
SELECT code, duration_days, created_at
FROM redeem_keys
WHERE claimed_by IS NULL
  AND (key_expires_at IS NULL OR key_expires_at > NOW());


-- ============================================================
-- 7) ESTADISTICAS DE KEYS  -- EJECUTAR DIRECTO
-- ============================================================
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
-- 8) HACER ADMIN A UN USUARIO YA EXISTENTE (ej: alec)
--    Nota: 'alec' ya existe en tu BD, por eso este es UPDATE,
--    NO un INSERT (el INSERT original daba error de duplicado).
-- ============================================================
UPDATE users
SET role = 'admin'
WHERE username = 'alec';


-- ============================================================
-- 9) CREAR UN USUARIO NUEVO (ej: admin adicional)
--    REEMPLAZA username y password_hash antes de correr.
--    IMPORTANTE: password_hash NO se escribe a mano aqui.
--    Debe generarse desde tu app Python con la misma funcion
--    de hashing que usa tu backend (ej: pbkdf2_hmac), para que
--    el login funcione. Un valor inventado aqui NO servira.
-- ============================================================
INSERT INTO users (id, username, password_hash, role, credits, access_expires_at, created_at)
VALUES (UUID(), 'NUEVO_USERNAME', 'HASH_GENERADO_DESDE_TU_APP', 'admin', 9999,
        DATE_ADD(NOW(), INTERVAL 10000 DAY), NOW());


-- ============================================================
-- 10) LISTAR USUARIOS CON ESTADO DE ACCESO  -- EJECUTAR DIRECTO
-- ============================================================
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


-- ============================================================
-- 11) SESIONES ACTIVAS (ultimas 24h)  -- EJECUTAR DIRECTO
-- ============================================================
SELECT s.token, u.username, s.created_at
FROM sessions s
JOIN users u ON u.id = s.user_id
WHERE s.created_at >= DATE_SUB(NOW(), INTERVAL 24 HOUR);


-- ============================================================
-- 12) MENSAJES DE CHAT DE UN USUARIO (ultimas 24h)
--     REEMPLAZA 'USERNAME_AQUI' antes de correr
-- ============================================================
SELECT role, text, created_at
FROM chat_messages
WHERE user_id = (SELECT id FROM users WHERE username = 'USERNAME_AQUI')
  AND created_at >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
ORDER BY created_at ASC;


-- ============================================================
-- 13) CONTAR MENSAJES POR USUARIO (debug)  -- EJECUTAR DIRECTO
-- ============================================================
SELECT u.username, COUNT(m.id) AS msg_count
FROM users u
LEFT JOIN chat_messages m ON m.user_id = u.id
GROUP BY u.id, u.username
ORDER BY msg_count DESC;