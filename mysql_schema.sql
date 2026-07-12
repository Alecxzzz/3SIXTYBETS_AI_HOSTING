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
