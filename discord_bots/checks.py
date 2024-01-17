from discord import Colour, Message
from discord.ext.commands.context import Context

from discord_bots.utils import send_message

from .models import Session, Player, AdminRole


async def is_admin(ctx: Context):
    """
    Check to wrap functions that require admin

    https://discordpy.readthedocs.io/en/stable/ext/commands/commands.html#global-checks
    """
    session = Session()
    message: Message = ctx.message
    caller = (
        session.query(Player)
        .filter(Player.id == message.author.id, Player.is_admin == True)
        .first()
    )
    if caller:
        session.close()
        return True

    if not message.guild:
        session.close()
        return False

    member = message.guild.get_member(message.author.id)
    if not member:
        session.close()
        return False

    admin_roles = session.query(AdminRole).all()
    admin_role_ids = map(lambda x: x.role_id, admin_roles)
    member_role_ids = map(lambda x: x.id, member.roles)
    is_admin: bool = len(set(admin_role_ids).intersection(set(member_role_ids))) > 0
    if is_admin:
        session.close()
        return True
    else:
        await send_message(
            message.channel,
            embed_description="You must be an admin to use that command",
            colour=Colour.red(),
        )
        session.close()
        return False
