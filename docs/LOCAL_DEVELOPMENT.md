# Local development

## Running locally

See the docs on [bot setup](./BOT_SETUP.md) and [execution](./RUNNING.md).

## Editor

VSCode is recommended. If you do, install these vscode plugins:

- Python
- Pylance

## Type checking

If you use VSCode, this project will use basic type checking. See
[`./.vscode/settings.json`](./.vscode/settings.json)

```json
{
  "python.analysis.typeCheckingMode": "basic"
}
```

This enforces type checks for the types declared.

## Formatting

Use python black: https://github.com/psf/black

- Go to vscode preferences (cmd + `,` on mac, ctrl + `,` on windows)
- Type "python formatting" in the search bar
- For the option `Python > Formatting: Provider` select `black`

### Pre-commit hook

This project uses `darker` for formatting in a pre-commit hook. Darker
documentation: https://pypi.org/project/darker/. pre-commit documentation:
https://pre-commit.com/#installation

- `pip install darker`
- `pip install pre-commit`
- `pre-commit install`
- `pre-commit autoupdate`

## Creating a new DB migration

Migrations are handled by Alembic: https://alembic.sqlalchemy.org/. See here for
a tutorial: https://alembic.sqlalchemy.org/en/latest/tutorial.html.

To apply migrations:

- `alembic upgrade head`

To create new migrations:

- Make your changes in `models.py`
- Generate a migration file: `alembic revision --autogenerate -m "Your migration
name here"`. Your migration file will be in `alembic/versions`.
- Apply your migration to the database: `alembic upgrade head`
- Commit your migration: `git add alembic/versions`

Common issues:

- Alembic does not pick up certain changes like renaming tables or columns
  correctly. For these changes you'll need to manually edit the migration file.
  See here for a full list of changes Alembic will not detect correctly:
  https://alembic.sqlalchemy.org/en/latest/autogenerate.html#what-does-autogenerate-detect-and-what-does-it-not-detect
- To set a default value for a column, you'll need to use `server_default`:
  https://docs.sqlalchemy.org/en/14/core/defaults.html#server-defaults. This
  sets a default on the database side.
- Alembic also sometimes has issues with constraints and naming. If you run into
  an issue like this, you may need to hand edit the migration. See here:
  https://alembic.sqlalchemy.org/en/latest/naming.html