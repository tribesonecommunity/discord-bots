# Scripts

This document covers the scripts provided by the bot located in `/scripts`.
These scripts are separate from the main bot functionality.

## Soft Reset

Recalculates the ratings of a category from the given start date (or all games if empty).
It resets all ratings to default rating (25 mu, 8.3 sigma).
The entire game history of the specified categories and queues is reprocessed in order.
One of `--src-categories` and `--src-queues` must not be empty. Categories and queues are loaded case-sensitive!

Unless `--store True` is explicitly specified the rating changes are only logged and not stored.
The changed ratings are printed as an ASCII table into the log.
TODO: export as a csv file.

### Examples

`python ./scripts/soft_reset.py --src-categories CTF-NA --from 2024-03-12 --target-category CTF-NA`
`python ./scripts/soft_reset.py --src-queues 7v7-NA 7v7-EU --from 2024-01-01 --target-region CTF --store True`

It is recommended to shut down the bot during reprocessing while there is no game running.
Please ensure that the bot is shut down and that no games are in progress before running the script.
