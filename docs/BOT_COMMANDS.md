# Full list of commands

```
CategoryCommands:
  clearqueuecategory
  createcategory
  listcategories
  showcategory            
  removecategory
  setcategoryname
  setcategoryrated
  setcategoryunrated
  setqueuecategory
  setmingamesforleaderboard       
  setcategorysigmadecay   Configure sigma decay settings for the category
MapCommands:
  addmap                  Add a map to the map pool
  changegamemap           Change the map for a game
  changequeuemap          Change the next map for a queue (note: affects all queues sharing that rotation)
  listmaps                List all maps in the map pool
  removemap               Remove a map from the map pool
QueueCommands:
  setqueuerotation        Assign a map rotation to a queue
  showqueuerotation       Shows the map rotation assigned to a queue
RaffleCommands:
  createraffle            TODO: Implementation
  myraffle                Displays how many raffle tickets you have
  rafflestatus            Displays raffle ticket information and raffle leaderboard
  runraffle               TODO: Implementation
  setrotationmapraffle    Set the raffle ticket reward for a map in a rotation
RotationCommands:
  addrotation             Add a rotation to the rotation pool
  addrotationmap          Add a map to a rotation at a specific ordinal (position)
  listrotations           List all rotations in the rotation pool
  removerotation          Remove a rotation from the rotation pool
  removerotationmap       Remove a map from a rotation
  setrotationmapordinal   Set the ordinal (position) for a map in a rotation
VoteCommands:
  mockvotes               Generates 6 mock votes for testing
  setmapvotethreshold     Set the number of votes required to pass
  unvote                  Remove all of a player's votes
  unvotemap               Remove all of a player's votes for a map
  unvoteskip              Remove all of a player's votes to skip the next map
No Category:
  add                     Players adds self to queue(s). If no args to all existing queues
  addadmin
  addadminrole
  addqueuerole
  autosub                 Picks a person to sub at random
  ban                     TODO: remove player from queues
  cancelgame
  clearqueue
  clearqueuerange
  coinflip
  commend
  commendstats
  createcommand
  createdbbackup
  createqueue
  decayplayer
  del                     Players deletes self from queue(s)
  deletegame
  delplayer               Admin command to delete player from all queues
  disableleaderboard
  disablestats
  editcommand
  editgamewinner
  enableleaderboard
  enablestats
  finishgame
  gamehistory
  help                    Shows this message
  isolatequeue
  listadminroles
  listadmins
  listbans
  listchannels
  listdbbackups
  listnotifications
  listplayerdecays
  listqueueroles
  lockqueue
  lt
  mockqueue
  movegameplayers
  notify
  pug
  removeadmin
  removeadminrole
  removecommand
  removedbbackup
  removenotifications
  removequeue
  removequeuerole
  resetleaderboardchannel
  resetplayertrueskill
  restart
  roll
  setbias
  setcaptainbias
  setcommandprefix
  setgamecode
  setqueueordinal
  setqueuerange
  setqueuesweaty
  setsigma
  showgame
  showgamedebug
  showqueuerange
  showsigma               Returns the player's base sigma. Doesn't consider regions
  showtrueskillnormdist   Print the normal distribution of the trueskill in a given queue.
  stats
  status
  streams
  sub
  testleaderboard
  trueskill
  unban
  unisolatequeue
  unlockqueue
  unsetqueuesweaty
```
