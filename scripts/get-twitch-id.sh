# https://dev.twitch.tv/docs/authentication/getting-tokens-oauth/#client-credentials-grant-flow
curl -XPOST https://id.twitch.tv/oauth2/token \
   -H "Content-Type: application/x-www-form-urlencoded" \
   -d 'client_id=$TWITCH_CLIENT_ID&client_secret=$TWITCH_CLIENT_SECRET&grant_type=client_credentials'

# https://dev.twitch.tv/docs/api/reference/#get-games
curl -X GET 'https://api.twitch.tv/helix/games?id=278321' \
    -H 'Authorization: Bearer $TWITCH_AUTH_TOKEN' \
    -H 'Client-Id: $TWITCH_CLIENT_ID'
