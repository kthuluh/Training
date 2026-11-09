-- Run once in Supabase SQL editor. Data is accessed exclusively by the backend.
create table public.athlete_profiles (
  user_id uuid primary key references auth.users(id) on delete cascade,
  approved boolean not null default false,
  display_name text not null default '',
  birth_year integer,
  goal text not null default '',
  goal_date date,
  experience text not null default '',
  available_days integer,
  constraints_text text not null default '',
  updated_at timestamptz not null default now()
);
create table public.strava_connections (
  user_id uuid primary key references auth.users(id) on delete cascade,
  athlete_id bigint not null unique,
  access_token_cipher text not null,
  refresh_token_cipher text not null,
  expires_at bigint not null
);
create table public.oauth_states (
  state_hash text primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  expires_at timestamptz not null
);
alter table public.athlete_profiles enable row level security;
alter table public.strava_connections enable row level security;
alter table public.oauth_states enable row level security;
-- No client policies: only the service role on the server can read/write these.
-- Approve a user in the Supabase SQL editor, replacing the UUID from Authentication:
-- update public.athlete_profiles set approved = true where user_id = 'USER_UUID';
