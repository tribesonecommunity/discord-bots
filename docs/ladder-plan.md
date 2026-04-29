# Ladder — Implementation Plan

## Overview

Add a TeamWarfareLeague-style challenge ladder. Players form named teams; teams challenge other teams; the winning challenger moves above the defeated team in the rankings. Loss or draw: no movement. The ladder tracks W/L/D records, runs matches over multiple maps drawn from a configured rotation, and surfaces results in dedicated leaderboard and history channels.

The ladder is fully isolated from the existing queue / TrueSkill / economy / raffle / captain-pick systems. Ladder games never write to `FinishedGame`; they live in their own tables.

Multiple ladders can run concurrently. Every command takes an explicit ladder name.

---

## Confirmed decisions

| # | Decision |
|---|---|
| 1 | Team join flow is **invite-only** — captain invites, invitee accepts. |
| 2 | Default **challenge distance** is `3`, configurable per-ladder (`max_challenge_distance`). |
| 3 | A team may have at most **1 in-flight match** (pending or accepted-but-not-completed, both directions combined). |
| 4 | Result reporting is **single-report**: either captain submits all map scores in one modal, then admins can edit afterward. |
| 5 | Commands always require an explicit **ladder name** argument. |
| 6 | A player can be on **multiple ladders** (one team per ladder). |
| 7 | `max_team_size` is a hard roster cap. **Everyone rostered plays**; teams must have a full roster to challenge or accept. |

### Other defaults applied

- Map selection at accept-time uses `random.sample` (no duplicates within a single match) unless the rotation has fewer maps than `maps_per_match`, in which case duplicates are allowed (`random.choices`) and a warning is posted to the history channel.
- A team starts at the bottom of the ladder when created. The bottom is `max(position) + 1`, or `1` if the ladder is empty.
- The challenger may only challenge teams **strictly above** them, within `max_challenge_distance` positions.
- **No auto-forfeit.** A team can drop below roster cap (player leaves, captain kicks) while a match is in-flight; the match stays open and the team is expected to fill the roster again before playing. Admins have `/ladder admin forceendmatch` to resolve stuck matches.
- A team **cannot disband while it has an in-flight match**. The captain must cancel a pending challenge first, or play / forfeit the match (admin) before disbanding.
- **Position rule on a challenger win**: the challenger inserts **immediately above** the defender. The defender drops by exactly 1. Any teams between them in the old order also shift down by 1 to fill the gap left by the challenger. The challenger's old slot is closed up, not preserved.
  - Example: positions `... B=2, X=3, Y=4, A=5 ...`. A challenges B and wins. New positions: `... A=2, B=3, X=4, Y=5 ...`.
- `maps_per_match` is capped at **5**, to fit a single Discord modal for reporting (modals support max 5 components).

---

## Data model

All tables are new. No changes to existing tables. SQLAlchemy models go in `discord_bots/models.py`; one alembic migration creates all of them.

### `Ladder`

| Column | Type | Notes |
|---|---|---|
| `id` | str (uuid) PK | |
| `name` | str | unique |
| `rotation_id` | FK `rotation.id` | reuses existing `Rotation` |
| `maps_per_match` | int | 1..5 (capped to fit modal); e.g. 3, 5 |
| `max_team_size` | int | hard roster cap; required to challenge/accept |
| `max_challenge_distance` | int | default 3 |
| `max_in_flight_per_team` | int | default 1 |
| `leaderboard_channel_id` | BigInt nullable | per-ladder; not env-config |
| `leaderboard_message_id` | BigInt nullable | the single edited message |
| `history_channel_id` | BigInt nullable | per-ladder |
| `is_active` | bool | soft-disable without delete |
| `created_at` | datetime | |

### `LadderTeam`

| Column | Type | Notes |
|---|---|---|
| `id` | str (uuid) PK | |
| `ladder_id` | FK `ladder.id` | |
| `name` | str | unique within ladder |
| `captain_id` | FK `player.id` | |
| `position` | int | 1 = top of ladder; unique within ladder |
| `wins` | int | match-level (not map-level) |
| `losses` | int | |
| `draws` | int | |
| `created_at` | datetime | |

Unique on `(ladder_id, name)` and `(ladder_id, position)`.

### `LadderTeamPlayer`

Roster junction table.

| Column | Type | Notes |
|---|---|---|
| `id` | str (uuid) PK | |
| `team_id` | FK `ladder_team.id` | |
| `player_id` | FK `player.id` | |
| `joined_at` | datetime | |

Unique on `(team_id, player_id)`. Application-level constraint: a player has at most one team per ladder (enforced via query check; not a DB constraint since it spans tables).

### `LadderTeamInvite`

Pending captain-issued invites.

| Column | Type | Notes |
|---|---|---|
| `id` | str (uuid) PK | |
| `team_id` | FK `ladder_team.id` | |
| `player_id` | FK `player.id` | |
| `invited_by_id` | FK `player.id` | the captain at invite-time |
| `created_at` | datetime | |
| `expires_at` | datetime nullable | optional auto-expire (TBD) |

Unique on `(team_id, player_id)` so a duplicate invite is a no-op.

### `LadderMatch`

| Column | Type | Notes |
|---|---|---|
| `id` | str (uuid) PK | |
| `ladder_id` | FK `ladder.id` | |
| `challenger_team_id` | FK `ladder_team.id` | |
| `defender_team_id` | FK `ladder_team.id` | |
| `status` | enum string | `pending`, `accepted`, `completed`, `cancelled` |
| `winner_team_id` | FK `ladder_team.id` nullable | null on draw / pre-completion |
| `challenger_map_wins` | int | denormalized map-win count |
| `defender_map_wins` | int | denormalized map-win count |
| `challenger_position_at_challenge` | int | snapshot for audit |
| `defender_position_at_challenge` | int | snapshot for audit |
| `challenged_at` | datetime | |
| `accepted_at` | datetime nullable | |
| `completed_at` | datetime nullable | |

### `LadderMatchGame`

One row per map in a match. Created at accept-time.

| Column | Type | Notes |
|---|---|---|
| `id` | str (uuid) PK | |
| `match_id` | FK `ladder_match.id` | |
| `ordinal` | int | 1..maps_per_match |
| `map_id` | FK `map.id` | |
| `challenger_score` | int nullable | reported score |
| `defender_score` | int nullable | reported score |
| `winner_team` | int nullable | 0 = challenger, 1 = defender, -1 = draw |
| `reported_at` | datetime nullable | |
| `reported_by_id` | FK `player.id` nullable | the captain who reported |

Unique on `(match_id, ordinal)`.

---

## Match lifecycle

```
[challenge] -> pending -> [accept]                  -> accepted -> [report] -> completed
                       -> [defender decline]        -> cancelled
                       -> [challenger cancel]       -> cancelled
                       -> [admin cancelmatch]       -> cancelled
                                                       accepted -> [admin forceendmatch] -> completed
                                                       accepted -> [admin cancelmatch]   -> cancelled
```

### 1. `/ladder challenge`

- Caller is challenger team's captain.
- Defender must be **above** challenger in `position`, within `max_challenge_distance`.
- Both teams must have a full roster (`max_team_size` players each).
- Both teams must have `< max_in_flight_per_team` open matches.
- Insert `LadderMatch` row (status `pending`); snapshot positions; post a notice to the history channel.

### 2. `/ladder accept`

- Caller is defender team's captain.
- Insert `maps_per_match` `LadderMatchGame` rows. Maps are picked via `random.sample(rotation_maps, k=maps_per_match)`. If the rotation has fewer maps than `maps_per_match`, allow duplicates (`random.choices`).
- Status -> `accepted`, `accepted_at` = now.
- Post embed to history channel: "Match started: \<challenger\> vs \<defender\>, maps: \<list\>".

### 3. `/ladder decline` and `/ladder cancel`

- `decline`: defender captain rejects a pending challenge -> status `cancelled`.
- `cancel`: challenger captain rescinds their own pending challenge -> status `cancelled`.
- Neither path runs once a match is `accepted`. After accept, only admin tooling can revoke.

### 4. `/ladder report`

Reporting is **one command, one modal, all maps at once** — to keep UX simple.

- Either captain runs `/ladder report <ladder> <match_id>`.
- The bot opens a modal titled `Report match: <challenger> vs <defender>`. The modal has one text input per map (up to 5), each labeled with the map name and accepting a score in `our-their` form, e.g. `3-1`. Pre-filled with any prior values so the same command can be used to amend.
- On submit:
  - Parse each row; reject the whole submission if any row is malformed.
  - Compute `winner_team` per game (higher score wins; equal -> draw).
  - Update all `LadderMatchGame` rows (`challenger_score`, `defender_score`, `winner_team`, `reported_at`, `reported_by_id`).
  - Finalize the match (step 5 below).
- Admins can use `/ladder admin editmatch <match_id>` which opens the same modal pre-populated, allowing post-hoc corrections. Re-submission re-runs finalization.

### 5. Match finalization (internal)

- Compute `challenger_map_wins` and `defender_map_wins` from `LadderMatchGame.winner_team`.
- Determine match `winner_team_id`:
  - challenger map wins > defender map wins -> challenger wins
  - defender map wins > challenger map wins -> defender wins
  - equal -> draw (no `winner_team_id`)
- Update team records: winner `wins += 1`, loser `losses += 1`, draws `draws += 1` if applicable.
- **Position update** (only if challenger won AND `challenger.position > defender.position`):
  - Capture `old_defender_pos = defender.position`, `old_challenger_pos = challenger.position`.
  - In one transaction: `UPDATE ladder_team SET position = position + 1 WHERE ladder_id = ? AND position >= old_defender_pos AND position < old_challenger_pos`.
  - Then: `UPDATE ladder_team SET position = old_defender_pos WHERE id = challenger.id`.
  - Net effect: defender drops by 1; everyone strictly between them also drops by 1; challenger lands at the defender's old position; challenger's old slot is closed up.
- Status -> `completed`, `completed_at` = now.
- Post match summary embed to history channel (with the position changes spelled out).
- Refresh leaderboard message.

### 6. Roster shortfalls and stuck matches

We do **not** auto-forfeit. A team that drops below `max_team_size` mid-match is expected to refill via captain invite. The match remains in `accepted` status until reported.

Admin escape hatches:

- `/ladder admin forceendmatch <match_id> <winner: challenger|defender|draw>` — finalizes the match in the named team's favor (or as a draw). Same finalization path as a normal report; updates records, applies the position rule, posts to history. Use when a team has gone inactive or refuses to play.
- `/ladder admin cancelmatch <match_id>` — sets status `cancelled` with no record changes. Use when a match should be voided entirely.

A team **cannot disband** while it has an in-flight match. The captain must:
- cancel the match (if `pending`), or
- play and report the match, or
- ask an admin to `forceendmatch` or `cancelmatch` first.

---

## Commands

New cog `discord_bots/cogs/ladder.py` (style mirrors `cogs/admin.py` and `cogs/in_progress_game.py`). Top-level group `/ladder` with subgroups `admin`, `team`. All commands use `is_command_or_captain_channel` for channel gating; admin commands stack `is_admin_app_command`.

### Admin

| Command | Description |
|---|---|
| `/ladder admin create <name> <rotation> <maps_per_match> <max_team_size>` | Create a new ladder. |
| `/ladder admin delete <name>` | Delete a ladder (cascades teams/matches). |
| `/ladder admin setchannels <ladder> <leaderboard_channel> <history_channel>` | Set per-ladder channels. |
| `/ladder admin setmapspermatch <ladder> <value>` | Edit `maps_per_match` (1..5). |
| `/ladder admin setmaxteamsize <ladder> <value>` | Edit `max_team_size`. |
| `/ladder admin setchallengedistance <ladder> <value>` | Edit `max_challenge_distance`. |
| `/ladder admin setactive <ladder> <value>` | Toggle `is_active` (write-block when false). |
| `/ladder admin editmatch <match_id>` | Open the report modal pre-populated with current scores; re-runs finalization on submit. |
| `/ladder admin forceendmatch <match_id> <winner: challenger\|defender\|draw>` | Finalize a stuck match in a team's favor (or draw). Applies records + position rule. |
| `/ladder admin cancelmatch <match_id>` | Void a match entirely; no record changes. |
| `/ladder admin forceadjust <ladder> <team_name> <new_position>` | Manually move a team in the rankings (rest shift to fill / make room). |
| `/ladder admin removeteam <ladder> <team_name>` | Force-disband a team (admin); ranks compact. Cancels any in-flight match. |

### Captain / player

| Command | Description |
|---|---|
| `/ladder team create <ladder> <team_name>` | Caller becomes captain; team starts at the bottom. |
| `/ladder team invite <ladder> <player>` | Captain-only. Creates a `LadderTeamInvite`. |
| `/ladder team uninvite <ladder> <player>` | Captain-only. Revokes a pending invite. |
| `/ladder team accept <ladder> <team_name>` | Invitee accepts an outstanding invite. Enforces roster cap and one-team-per-ladder. |
| `/ladder team decline <ladder> <team_name>` | Invitee declines. |
| `/ladder team leave <ladder>` | Player removes themselves. If captain leaves, must `transfer` first. |
| `/ladder team kick <ladder> <player>` | Captain-only. |
| `/ladder team transfer <ladder> <player>` | Captain-only; gives captaincy to another rostered player. |
| `/ladder team disband <ladder>` | Captain-only. Releases roster. **Blocked while team has an in-flight match.** |
| `/ladder challenge <ladder> <opponent_team>` | Challenger captain. |
| `/ladder accept <ladder>` | Defender captain. Maps roll here. (Match implicit — at most 1 in-flight per team.) |
| `/ladder decline <ladder>` | Defender captain. |
| `/ladder cancel <ladder>` | Challenger captain (only while `pending`). |
| `/ladder report <ladder>` | Either captain. Opens a modal with one row per map to enter scores. |

### Read-only

| Command | Description |
|---|---|
| `/ladder list` | All ladders with summary. |
| `/ladder rankings <ladder>` | Current standings (also pinned in leaderboard channel). |
| `/ladder team info <ladder> <team>` | Roster, captain, record, current position, in-flight matches. |
| `/ladder team list <ladder>` | All teams in a ladder. |
| `/ladder matchinfo <match_id>` | Match details, per-map scores (autocomplete on match_id). |

---

## Channels

There are **three** kinds of ladder-related channels:

1. **Ladder command channel** (single, server-wide) — where `/ladder ...` commands are run. Mirrors the captain-channel pattern (`Config.captain_channel_id`). Stored on the existing singleton `Config` table as a new nullable `ladder_channel_id` column. Set via a new admin command `/config ladderchannel <#channel>`.
2. **Leaderboard channel** (per-ladder) — bot-edited single-message rankings.
3. **History channel** (per-ladder) — bot posts an embed per lifecycle event.

Leaderboard + history channel IDs live on the `Ladder` row (not env vars), set via `/ladder admin setchannels`. If unset, those features are no-ops. The ladder command channel is global because all ladders share the same lobby for issuing commands.

### Leaderboard channel

Single bot-edited message, pattern from `discord_bots/utils.py:1595`. Refreshed on:
- match completion
- team create / disband / forceadjust
- ladder config change
- challenge issued / accepted / cancelled (so the in-flight annotation stays current)

Layout (embed):

```
Ladder: <name>          maps/match: 3   roster: 8   challenge range: 3
-----------------------------------------------------------
1.  Team Alpha                        12-3-1
2.  Crimson Vipers                     9-4-0     [challenged by Echo Squad]
3.  Echo Squad                         8-5-2     [challenging Crimson Vipers]
4.  Northwind                          7-6-0
...
```

In-flight annotations:
- A team that has issued a pending challenge: `[challenging <opponent>]`.
- A team that has received a pending challenge: `[challenged by <opponent>]`.
- Both teams in an accepted match: `[challenge accepted vs <opponent>]` (same string on both sides).

### History channel

A new embed posted on each lifecycle event:
- **Challenge issued** (pending) — challenger, defender, snapshot positions.
- **Match accepted** — with rolled map list.
- **Match cancelled** — by whom (decline / cancel / admin).
- **Match completed** — final per-map scores, match winner, before/after positions for both teams.
- **Position adjustment** — admin `forceadjust` posts a record.

Pattern mirrors `discord_bots/cogs/in_progress_game.py:202`.

---

## Permissions

- Admin commands: `is_admin_app_command` (existing).
- Captain commands: an inline check inside each command compares `LadderTeam.captain_id` against `interaction.user.id` for the ladder named in the command. (No standalone decorator — kept inline because the lookup needs the ladder argument.)
- Other player-facing commands look up the caller's team via `LadderTeamPlayer` and gate on roster membership inline.

All ladder commands gate channel via a new check `is_ladder_channel` (uses `Config.ladder_channel_id`). Mirrors `is_command_or_captain_channel` from the captain-pick rollout.

---

## Isolation from existing systems

- No writes to `FinishedGame`, `FinishedGamePlayer`, `Player.rated_trueskill_*`, `PlayerCategoryTrueskill`, `Queue`, `InProgressGame`, economy, raffle.
- Ladder uses its own `LadderMatch` / `LadderMatchGame` history.
- Ladder reuses read-only: `Player`, `Map`, `Rotation`, `RotationMap`. No schema changes.
- Existing leaderboard (`LEADERBOARD_CHANNEL`) and game history (`GAME_HISTORY_CHANNEL`) are untouched. Ladder posts to its own per-ladder channels.

---

## Migration plan

Single alembic migration creating all six new tables (`ladder`, `ladder_team`, `ladder_team_player`, `ladder_team_invite`, `ladder_match`, `ladder_match_game`). Use `batch_op` for SQLite compatibility; follow conventions in `alembic/versions/20250607184820_7d5d0da1c412_add_queue_position_model.py`.

---

## Phased delivery

Each phase is a standalone, reviewable commit on `feat/ladder`.

| Phase | Scope | Verifies |
|---|---|---|
| 1 | `Config.ladder_channel_id` column, `/config ladderchannel`, `is_ladder_channel` check, all model tables + migration, admin scaffolding (`/ladder admin create|delete|list|setchannels|setconfig`) | Schema is correct; admins can configure the ladder channel and spin up a ladder. |
| 2 | Team management (`/ladder team create|invite|uninvite|accept|decline|leave|kick|transfer|disband|info|list`) | Rosters, invites, captaincy work end-to-end with no matches. |
| 3 | Challenges + match start (`/ladder challenge|accept|decline|cancel`, map rolling, history-channel post on accept, leaderboard in-flight annotations) | Full challenge flow up to "match in progress". |
| 4 | Result reporting + ranking (`/ladder report` modal, position update, W/L, leaderboard refresh; admin `editmatch|forceendmatch|cancelmatch|forceadjust|removeteam`) | Full end-to-end ladder. |

---

## Resolved questions

1. **Disband / roster shortfall**: no auto-forfeit. Match stays open until reported; admins have `/ladder admin forceendmatch` and `/ladder admin cancelmatch`. A team cannot disband while it has an in-flight match.
2. **Invite expiry**: invites have **no expiration**. They live until accepted, declined, or revoked by the captain (`/ladder team uninvite` — added below).
3. **Ladder channel scope**: **dedicated ladder channel**. New `Config.ladder_channel_id` column + `/config ladderchannel` admin command. New check `is_ladder_channel` gates all `/ladder ...` commands.
4. **Leaderboard refresh**: refresh on every relevant event (match completion, challenge issued/accepted/cancelled, team mutations, config change). Matches are infrequent, so chattiness is not a concern.
5. **In-flight annotations on leaderboard**: yes, show `[challenging X]` / `[challenged by Y]` / `[match in progress vs Z]` next to teams. Also recorded in history channel.
6. **Map duplication when rotation < maps_per_match**: allow duplicates via `random.choices`; post a warning to the history channel.
7. **`Ladder.is_active = false`**: block all writes (challenge, accept, report, team mutations) but allow reads (rankings, info, list).

A small additional command added during this round:

- `/ladder team uninvite <ladder> <player>` — captain-only; revokes an outstanding invite.
