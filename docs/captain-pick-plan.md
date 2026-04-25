# Captain Picking Mode — Implementation Plan

## Overview

Add a queue mode where, instead of auto-generating teams via TrueSkill, the two highest-rated players become captains and snake-draft the rest. Result reporting (Win/Loss/Tie/Cancel) is unchanged after the draft completes.

The mode is opt-in per queue, and the opt-in is implicit: queues created in a separately-registered "captain channel" are automatically captain-pick; queues created in the regular command channel stay traditional.

---

## Channel Scoping

The bot today restricts commands to a single channel (`CHANNEL_ID` env var, enforced by `is_command_channel` in `discord_bots/checks.py`). To support captain games living in their own channel:

- Add **`Config.captain_channel_id`** (nullable `BigInteger`) on the existing singleton `Config` table. `NULL` = feature disabled.
- New admin slash command:
  - `/config captainchannel <#channel>` — set
  - `/config captainchannel clear` — unset
- New check **`is_command_or_captain_channel`** — accepts interactions from either `CHANNEL_ID` or `captain_channel_id`. This replaces `is_command_channel` on all queue/game/add/status/sub/finish commands.
- **`Queue.is_captain_pick` is set implicitly at queue creation**, based on the channel `/queue create` was invoked from:
  - captain channel → `is_captain_pick = True`
  - regular channel → `is_captain_pick = False`
- **Queue listing/joining is channel-aware**: `/add`, `/status`, etc. filter queues by `is_captain_pick == (channel.id == captain_channel_id)`. From the captain channel you only see captain queues; from the regular channel you only see traditional queues.
- Captain games' dynamically-created match text channel and voice channels are still created under `TRIBES_VOICE_CATEGORY_CHANNEL_ID`, same as today. The "captain channel" is just the lobby for adding and status — the game itself runs in its own channel as it does now.
- Single captain channel for now. If we ever need multiple, we'd revisit toward a `Queue.channel_id` column or a `CaptainChannel` table.

---

## Flow

The lifecycle splits into a new **draft phase** between "queue pops" and "game in progress":

### 1. Queue pops
`add_player_to_queue()` reaches `queue.size`. Branch on `queue.is_captain_pick`. If true, call new `start_draft()` instead of the existing `get_even_teams()` + team-write block.

### 2. Captain selection
Pick the two highest-rated players from the popped player set:

| Key | Direction |
|---|---|
| `mu - 3*sigma` | descending |
| `mu` | descending |
| `player_id` | ascending (deterministic fallback) |

Respect `PlayerCategoryTrueskill` if a category is attached, same as the existing rank-bound logic.

Higher-rated = **captain A**. Lower-rated = **captain B**.

### 3. First-pick choice
Captain B sees a message in the match channel with two buttons: **"Pick first"** / **"Pick second"**. Only captain B can interact.

### 4. Snake draft
The chosen first-picker gets pick #1, then alternation goes `A, B, B, A, A, B, B, …, A, B` (standard snake). For a 5v5 that's `1+2+2+2+1 = 8` picks of the 8 non-captain players.

### 5. Pick UI
A persistent message in the match channel showing:
- Team A roster (with captain marked)
- Team B roster (with captain marked)
- Remaining player pool
- Whose turn it is

The current picker has an interactive `Select` dropdown listing remaining players. Each pick edits the message in place to reflect the new state.

### 6. Pick timer
The pick view has a 2-minute timeout. If the captain doesn't pick in time, the bot auto-picks a random remaining player and the draft continues. On bot restart mid-draft, `cog_load` re-attaches a fresh view with a fresh 2-minute timer (a restart effectively gives the captain another 2 minutes — acceptable).

### 7. Draft completes
`finalize_draft()` runs:
- Compute `win_probability` from final teams via the same TrueSkill helpers `get_even_teams()` uses.
- Set `team0_name` / `team1_name` (random generators as today).
- Create voice channels under `TRIBES_VOICE_CATEGORY_CHANNEL_ID`.
- Post the existing `InProgressGameView` with Win/Loss/Tie/Cancel buttons.
- Run `execute_map_rotation` to advance the rotation.

From here, **result reporting is unchanged** — the existing flow keys off `game_id` and works on captain-pick games as-is.

---

## Data Model Changes

One alembic migration covers all of these.

### `Queue`
- **`+ is_captain_pick: Boolean, default False`** — implicitly `True` when the queue is created from the captain channel; `False` otherwise. Not directly user-editable.

### `Config` (singleton)
- **`+ captain_channel_id: BigInteger, nullable`** — the Discord channel id registered as the captain lobby.

### `InProgressGame`
- **`+ is_drafting: Boolean, default False`** — marks games that are still in the picking phase. Lets sub flows, stats, etc. ignore them.

### `InProgressGamePlayer`
- **`+ is_captain: Boolean, default False`** — set `True` for the two captains at draft start; never changes. Avoids needing to derive "who is captain" from `DraftPick` joins.
- **`~ team` becomes nullable** — during the draft, non-captain rows have `team = NULL`. A pick sets `team` to 0 or 1.

### `FinishedGame`
- **`+ is_captain_pick: Boolean, default False`** — denormalized from `Queue.is_captain_pick` at game-finish time. Lets stats/leaderboard queries exclude captain games with a single column filter instead of joining through `Queue`.

### `DraftPick` (new table)

| Column | Type |
|---|---|
| `in_progress_game_id` | FK → `InProgressGame.id` |
| `pick_number` | `Integer` (1, 2, 3, …) |
| `captain_player_id` | FK → `Player.id` |
| `picked_player_id` | FK → `Player.id` |

Used for audit/history and to determine whose turn is next. Captains are derivable from `is_captain` on `InProgressGamePlayer`; `DraftPick` is purely the pick log.

### Lifecycle summary
Captains get written as `InProgressGamePlayer` rows with `team` 0/1 and `is_captain=True` at draft start. The other players are written with `team=NULL` and `is_captain=False`, then their `team` flips to 0 or 1 as each pick happens. The draft is "complete" when zero rows have `team IS NULL`.

---

## Behavior Decisions

| Decision | Choice |
|---|---|
| **Voice channels** | Created at draft completion, not at start, to avoid people sitting in the wrong channel. |
| **Map** | Picked at draft start (visible during picks), but rotation only advances at draft completion. |
| **Map rotations** | Shared with traditional queues — captain queues just point to existing rotations via the existing `rotation_id` column. No schema work needed. |
| **Pick timer** | 2 minutes per pick. On timeout, auto-pick a random remaining player. |
| **Captain tiebreaker** | `mu - 3*sigma` → `mu` → `player_id` (deterministic). |
| **Sweaty queues** | Orthogonal. If `is_captain_pick` is `True`, the `is_sweaty` path is ignored entirely at runtime. |
| **TrueSkill updates** | **Not** applied for captain-pick games. We still write `FinishedGame` and `FinishedGamePlayer` rows for history, but skip `Rating.update()` and leave `rated_trueskill_mu/sigma` and `PlayerCategoryTrueskill` untouched. Captain games are recorded but unrated. |
| **Stats / leaderboards** | Captain-pick games are **excluded** from all leaderboard and stats output. Filter on `FinishedGame.is_captain_pick == False` in every aggregation query. Captain games are recorded for history only — no public ranking or per-map stats include them. |
| **Sigma decay** | No special handling. The existing decay task uses last-finished-game as the activity signal, and captain games still write `FinishedGame` rows, so playing a captain game keeps a player's sigma fresh. (Verify mechanism at implementation time.) |
| **Autosub** | **Disabled** for captain-pick games entirely (both drafting and live). Manual `/sub` is allowed but triggers the draft-restart flow described below. |
| **Raffle tickets** | **Disabled** for captain-pick games. Gate the raffle-ticket-award block in `finish_in_progress_game()` on `not queue.is_captain_pick` (raffle is part of the economy/rewards stack we're disabling). |
| **Minimum queue size** | Captain-pick requires `queue.size >= 4` (two captains + at least two picks). Validated at queue creation. |
| **1v1 / debug fallback** | Not implemented for now. Revisit later. |
| **Economy / predictions** | Disabled entirely for captain-pick games. Skip the `prediction_open=True` block and the `create_prediction_message` call when `queue.is_captain_pick`. |
| **Win probability** | Still computed and stored on `InProgressGame.win_probability` after the draft completes. **Never** displayed to users — neither in-progress embeds, finished-game embeds, nor stats outputs. Implementation: thread a `hide_win_probability` bool (derived from the queue flag) into the embed/string builders so the `(X%)` suffix is skipped. The DB column stays `nullable=False` with a real value. |

### Substitutions
Allowed during draft, but a sub completely restarts the draft:

1. Replace the subbed player's `InProgressGamePlayer` row.
2. Clear all `DraftPick` rows for the game.
3. Reset every non-captain row's `team` to `NULL`.
4. **If a captain was the one subbed out**: clear `is_captain` on both old captains, reset their teams to `NULL`, re-run captain selection on the new player set, set `is_captain=True` and teams 0/1 on the new top two.
5. Disable the existing draft message; post a fresh `FirstPickChoiceView` and update `InProgressGame.message_id`.
6. Announce "Draft restarted due to sub" in the match channel.

---

## Touch Points

### Schema
- `discord_bots/models.py`
- new alembic migration in `alembic/versions/`

Add `Queue.is_captain_pick`, `Config.captain_channel_id`, `InProgressGame.is_drafting`, `InProgressGamePlayer.is_captain`, `FinishedGame.is_captain_pick`. Make `InProgressGamePlayer.team` nullable. Create `DraftPick` table.

### Channel scoping
- `discord_bots/checks.py` — add `is_command_or_captain_channel`.
- `discord_bots/cogs/config.py` — add `/config captainchannel` slash command (set / clear).
- All cogs currently using `is_command_channel` for queue/game/add/status/sub/finish commands → switch to `is_command_or_captain_channel`.

### Pop branching
- `discord_bots/commands.py:create_game()` — if `queue.is_captain_pick`, call new `start_draft()` instead of running `get_even_teams()` and the team-write block.

### New draft starter
- `discord_bots/commands.py` (new `start_draft()`) — pick captains by tiebreaker rules, create `InProgressGame` with `is_drafting=True`, write captain `InProgressGamePlayer` rows (`is_captain=True`, team 0/1), write non-captain rows (`team=NULL`), create the match text channel (no voice yet), post the first-pick-choice view. Skip the entire economy/prediction block.

### New views
- `discord_bots/views/draft.py` (new file)
  - **`FirstPickChoiceView`** — two buttons, gated to captain B.
  - **`DraftPickView`** — `Select` dropdown of remaining players, gated to the current picker, `timeout=120` with random-pick fallback. Edits the message in place after each pick.

### New cog
- `discord_bots/cogs/draft.py` (new file) — `DraftCommands` handles the state machine: receives interactions, writes `DraftPick` rows, updates `InProgressGamePlayer.team`, decides whose turn is next via the snake-draft formula, and on completion calls `finalize_draft()`. Mirrors the `in_progress_game.py:cog_load` pattern for restart resilience — for any `InProgressGame` with `is_drafting=True`, re-register the appropriate view (`FirstPickChoice` if no first-pick recorded, otherwise `DraftPick`) bound to the stored `message_id`.

### Finalize
- `discord_bots/commands.py` (new `finalize_draft()`) — compute `win_probability` from final teams via the same TrueSkill helpers `get_even_teams()` uses, set team names, create voice channels, post the existing `InProgressGameView`, run `execute_map_rotation`. Set `is_drafting=False`.

### Sub-during-draft restart
- `discord_bots/commands.py:sub` — branch when `in_progress_game.is_drafting=True`: run the restart sequence described under **Substitutions** above, instead of the normal sub path.

### Hide win-probability rendering
- `discord_bots/commands.py`
  - lines 987, 994 (in-progress game embed)
  - lines 1662, 1669 (`rebalance_game` embed)
- `discord_bots/utils.py`
  - lines 234–247, 386–403 (pre-game team listings)
  - lines 506–509 (finished-game output)
  - lines 543–553 (team-string helper)

Thread `hide_win_probability` through these helpers; skip the `(X%)` suffix when set.

### Skip TrueSkill update on finish
- `discord_bots/cogs/in_progress_game.py:finishgame_callback` (and related) — when the finished game's queue is captain-pick, write `FinishedGame` and `FinishedGamePlayer` rows for history but skip `Rating.update()` and the per-category trueskill writes (around lines 593–621).
- Set `FinishedGame.is_captain_pick = True` at the same write site, copied from the queue.

### Skip raffle ticket award for captain games
- `discord_bots/cogs/in_progress_game.py:649–664` — gate the raffle-ticket-award block on `not queue.is_captain_pick`.

### Filter captain games out of stats / leaderboards
Apply `FinishedGame.is_captain_pick == False` to all aggregation queries:
- `discord_bots/utils.py:1495–1509` (`print_leaderboard`)
- `discord_bots/cogs/common.py:236–256` (`/stats`)
- `discord_bots/cogs/map.py:281–300, 402–443` (per-map stats)

### Disable autosub for captain games
- `discord_bots/commands.py:autosub` — at the top, fetch the in-progress game's queue. If `queue.is_captain_pick`, return a "not supported for captain-pick games" message. Prevents both the crash on `team IS NULL` during draft and the design-mismatch of auto-substituting into a hand-picked roster.

### Nullable-team read sweep
With `InProgressGamePlayer.team` becoming nullable, audit and guard reads that currently assume non-null. The set of files identified during the audit:
- `discord_bots/commands.py:878, 891, 957, 970, 1590, 1603, 1632, 1645` — autosub & rebalance team queries.
- `discord_bots/utils.py:270, 276, 648, 658, 679, 689, 796, 817` — embed/string helpers.

For each, either guard with `if not game.is_drafting:` or add `.filter(InProgressGamePlayer.team.isnot(None))` to the query. The autosub disable above neutralizes most of these, but the embed helpers can still be hit (e.g. `/status` displaying a drafting game), so the sweep is necessary regardless. Reads inside `finish_in_progress_game()` (`cogs/in_progress_game.py:436, 438, 484, 494`) are safe — they only run on `is_drafting=False` games. Economy reads (`cogs/economy.py:992`) are safe — economy is disabled for captain games.

---

## Implementation Order

1. Schema + alembic migration
2. Channel-scoping infrastructure
   - `Config.captain_channel_id` read/write
   - `/config captainchannel` slash command
   - `is_command_or_captain_channel` check
   - Update existing cogs to use the new check
3. Queue creation auto-flag based on channel
4. Channel-aware queue filtering in `/add`, `/status`, etc.
5. `start_draft()`
   - Captain selection
   - Initial DB writes
   - Create match text channel
   - Post `FirstPickChoiceView`
6. `FirstPickChoiceView` (two buttons, captain-B-only)
7. `DraftPickView` (Select dropdown, 2-min timeout, random fallback)
8. `finalize_draft()`
   - `win_probability` computation
   - Voice channels
   - `InProgressGameView`
   - `execute_map_rotation`
9. Restart resilience: `cog_load` for the draft cog
10. Sub-during-draft restart logic
11. Disable `/autosub` for captain-pick games
12. Nullable-team read sweep (guard embed/query reads)
13. Hide `win_probability` rendering everywhere
14. Skip economy entirely for captain queues (predictions + raffle tickets)
15. Skip TrueSkill update on finish for captain games; set `FinishedGame.is_captain_pick`
16. Filter captain games out of leaderboard and stats queries

---

## Open Questions

None remaining — all decisions locked in. Ready to start implementation on approval.
